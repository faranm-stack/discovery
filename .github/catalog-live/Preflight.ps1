# Read-only preflight for the supervised Studio pilot. Changes nothing.
param(
  [string]$Sub = $env:AZURE_SUBSCRIPTION_ID,
  [string]$Rg = $env:DISCOVERY_RESOURCE_GROUP,
  [string]$Supercomputer = $env:DISCOVERY_SUPERCOMPUTER,
  [string]$PilotCsv = "$PSScriptRoot\pilot.csv",
  [string]$Only
)
$ErrorActionPreference = 'Stop'
if (-not ($Sub -and $Rg -and $Supercomputer)) { throw 'Set AZURE_SUBSCRIPTION_ID, DISCOVERY_RESOURCE_GROUP and DISCOVERY_SUPERCOMPUTER (or pass -Sub, -Rg, -Supercomputer).' }
$fail = 0
function Check($ok, $msg) { if ($ok) { Write-Host "PASS $msg" } else { Write-Host "FAIL $msg" -ForegroundColor Red; $script:fail++ } }

$acct = az account show -o json 2>$null | ConvertFrom-Json
Check ($acct -and $acct.id -eq $Sub) "az session on subscription $Sub"
if (-not $acct) { Write-Host 'Run az login in your own terminal, then rerun.'; exit 1 }

$sc = az resource show --ids "/subscriptions/$Sub/resourceGroups/$Rg/providers/Microsoft.Discovery/supercomputers/$Supercomputer" --api-version 2026-02-01-preview -o json | ConvertFrom-Json -AsHashtable
$scIds = ([regex]::Matches(($sc | ConvertTo-Json -Depth 10), '"principalId":\s*"([0-9a-f-]{36})"') | ForEach-Object { $_.Groups[1].Value }) | Sort-Object -Unique

$tools = (az rest --method get --url "https://management.azure.com/subscriptions/$Sub/resourceGroups/$Rg/providers/Microsoft.Discovery/tools?api-version=2026-02-01-preview" -o json | ConvertFrom-Json -AsHashtable).value
$liveServers = az acr list --query "[].loginServer" -o tsv
$pullCache = @{}

foreach ($row in Import-Csv $PilotCsv) {
  if ($Only -and $row.agent -ne $Only) { continue }
  $tool = $tools | Where-Object { $_.name -eq $row.tool } | Select-Object -First 1
  if (-not $tool) { Check $false "$($row.agent): tool '$($row.tool)' deployed in $Rg"; continue }
  foreach ($img in ($tool.properties.definitionContent.infra | ForEach-Object { $_.image.acr })) {
    $server = ($img -split '/')[0]; $repo = (($img -split '/', 2)[1] -split ':')[0]; $tag = ($img -split ':')[-1]
    $acr = ($server -split '\.')[0] -replace '-.*', ''
    if ($liveServers -notcontains $server) { Write-Host "WARN $($row.agent): registry $server not visible to this identity; image and pull checks skipped" -ForegroundColor Yellow; continue }
    $tags = az acr repository show-tags -n $acr --repository $repo -o tsv 2>$null
    Check ($tags -contains $tag) "$($row.agent): image $img exists"
    if (-not $pullCache.ContainsKey($acr)) {
      $id = az acr show -n $acr --query id -o tsv
      $pullCache[$acr] = az role assignment list --scope $id --include-inherited --query "[?roleDefinitionName=='AcrPull' || roleDefinitionName=='Owner' || roleDefinitionName=='Contributor'].principalId" -o tsv
    }
    Check ([bool]($scIds | Where-Object { $pullCache[$acr] -contains $_ })) "$($row.agent): $Supercomputer can pull from $acr"
  }
}
Write-Host "`n$fail failing check(s)."
exit $fail
