# Runs the approved pilot through Studio (one turn per agent). Each turn shows up in Studio under project PRValidation.
# Default is a dry run. Pass -Go to actually start turns (uses model tokens and supercomputer jobs).
param(
  [switch]$Go,
  [string]$Project = 'PRValidation',
  [string]$PilotCsv = "$PSScriptRoot\pilot.csv",
  [string]$Only
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

foreach ($row in Import-Csv $PilotCsv) {
  if ($Only -and $row.agent -ne $Only) { continue }
  $dir = Join-Path $out $row.label
  if (-not $Go) { Write-Host "DRY RUN: $($row.agent) -> $dir"; continue }
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $proj = if ($row.project) { $row.project } else { $Project }
  $extra = @()
  if ($row.PSObject.Properties['asset'] -and $row.asset) {
    if (-not ($env:AZURE_SUBSCRIPTION_ID -and $env:DISCOVERY_RESOURCE_GROUP -and $env:DISCOVERY_STORAGE_CONTAINER)) {
      throw "$($row.agent) needs a storage asset: set AZURE_SUBSCRIPTION_ID, DISCOVERY_RESOURCE_GROUP and DISCOVERY_STORAGE_CONTAINER."
    }
    $extra = @('-StorageAssetId', "/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:DISCOVERY_RESOURCE_GROUP/providers/Microsoft.Discovery/storageContainers/$env:DISCOVERY_STORAGE_CONTAINER/storageAssets/$($row.asset)")
  }
  pwsh -NoProfile -File $turn -Project $proj -Agent $row.agent -Label $row.label -Prompt $row.prompt -OutDir $dir @extra *>&1 | Tee-Object (Join-Path $dir 'console.txt')
  "{0},{1},{2},exit={3}" -f (Get-Date -Format o), $row.agent, $row.label, $LASTEXITCODE | Add-Content (Join-Path $out 'ledger.csv')
}
if ($Go) { Write-Host "Evidence: $out  (exit 0 = response completed, not pass; read each summary.txt)" }
