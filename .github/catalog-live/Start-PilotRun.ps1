# Runs the approved pilot through Studio (one turn per agent). Each turn shows up in Studio under project PRValidation.
# Default is a dry run. Pass -Go to actually start turns (uses model tokens and supercomputer jobs).
param(
  [switch]$Go,
  [string]$Project = 'PRValidation',
  [string]$PilotCsv = "$PSScriptRoot\pilot.csv",
  [string]$Only,
  [ValidateRange(1, 8)][int]$Parallel = 4
)
$ErrorActionPreference = 'Stop'
$turn = Join-Path $PSScriptRoot 'Invoke-DiscoveryStudioTurn.ps1'
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$out = if ($env:CATALOG_EVIDENCE_DIR) { $env:CATALOG_EVIDENCE_DIR } else { Join-Path $PSScriptRoot "evidence-$stamp" }

$pre = @{ PilotCsv = $PilotCsv }
if ($Only) { $pre.Only = $Only }
& (Join-Path $PSScriptRoot 'Preflight.ps1') @pre
if ($LASTEXITCODE -ne 0 -and $Go -and -not $Only) {
  Write-Host 'Preflight failed. Fix it, remove the failing rows, or run one agent with -Only <agent>.' -ForegroundColor Red; exit 1
}

$rows = @(Import-Csv $PilotCsv | Where-Object { -not $Only -or $_.agent -eq $Only })
if (-not $Go) { $rows | ForEach-Object { Write-Host "DRY RUN: $($_.agent) -> $(Join-Path $out $_.label)" }; exit 0 }
foreach ($row in $rows) {
  if ($row.PSObject.Properties['asset'] -and $row.asset -and -not ($env:AZURE_SUBSCRIPTION_ID -and $env:DISCOVERY_RESOURCE_GROUP -and $env:DISCOVERY_STORAGE_CONTAINER)) {
    throw "$($row.agent) needs a storage asset: set AZURE_SUBSCRIPTION_ID, DISCOVERY_RESOURCE_GROUP and DISCOVERY_STORAGE_CONTAINER."
  }
}
New-Item -ItemType Directory -Force -Path $out | Out-Null
Write-Host "Running $($rows.Count) turn(s), $Parallel at a time. Agent output goes to the evidence artifact, not this log."

# Each turn is its own Studio conversation, so turns are independent and can run side by side.
$results = $rows | ForEach-Object -ThrottleLimit $Parallel -Parallel {
  $row = $_; $out = $using:out; $turn = $using:turn
  $dir = Join-Path $out $row.label
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $proj = if ($row.project) { $row.project } else { $using:Project }
  $extra = @()
  if ($row.PSObject.Properties['asset'] -and $row.asset) {
    $extra = @('-StorageAssetId', "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:DISCOVERY_RESOURCE_GROUP/providers/Microsoft.Discovery/storageContainers/$env:DISCOVERY_STORAGE_CONTAINER/storageAssets/$($row.asset)")
  }
  $start = Get-Date
  Write-Host "START $($row.agent) ($($row.label))"
  pwsh -NoProfile -File $turn -Project $proj -Agent $row.agent -Label $row.label -Prompt $row.prompt -OutDir $dir @extra *> (Join-Path $dir 'console.txt')
  $code = $LASTEXITCODE
  $secs = [int]((Get-Date) - $start).TotalSeconds
  Write-Host "END   $($row.agent) exit=$code ${secs}s"
  [pscustomobject]@{ When = $start.ToString('o'); Agent = $row.agent; Label = $row.label; Exit = $code; Seconds = $secs }
}
foreach ($r in $results) {
  "{0},{1},{2},secs={3},exit={4}" -f $r.When, $r.Agent, $r.Label, $r.Seconds, $r.Exit | Add-Content (Join-Path $out 'ledger.csv')
}
if ($Go) { Write-Host "Evidence: $out  (exit 0 = response completed, not pass; read each summary.txt)" }
