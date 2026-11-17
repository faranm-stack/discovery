#Requires -Version 7.0
<#
.SYNOPSIS
  Runs one Discovery Studio-equivalent chat turn against a deployed agent and saves the evidence.

.DESCRIPTION
  Creates (or reuses) an investigation and conversation on the Discovery workspace data plane,
  posts one user prompt to the named agent, optionally attaches storage assets exactly like the
  Studio attach button (discoveryProperties.storageAssets), polls to a terminal status, and
  saves the request, response, and input items. Prints every tool call, including generated scripts.

  Never starts sign-in: it only reuses the current Azure CLI session.
#>
param(
  [Parameter(Mandatory)][string]$Project,
  [Parameter(Mandatory)][string]$Agent,
  [string]$Prompt,
  [string]$PromptFile,
  [string[]]$StorageAssetId = @(),
  [string]$Investigation,
  [string]$InvestigationDisplayName,
  [string]$Conversation,
  [string]$Label = 'turn',
  [string]$OutDir = (Join-Path ([IO.Path]::GetTempPath()) 'discovery-studio-turns'),
  [string]$WorkspaceEndpoint = $env:DISCOVERY_WORKSPACE_ENDPOINT,
  [string]$ApiVersion = '2026-02-01-preview',
  [int]$TimeoutMinutes = 25,
  [int]$PreviewChars = 1500
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($WorkspaceEndpoint)) { throw 'Set DISCOVERY_WORKSPACE_ENDPOINT or pass -WorkspaceEndpoint.' }
$base = $WorkspaceEndpoint.TrimEnd('/')
$api = "api-version=$ApiVersion"

if ($PromptFile) { $Prompt = Get-Content -Raw -Encoding utf8 $PromptFile }
if ([string]::IsNullOrWhiteSpace($Prompt)) { throw 'Provide -Prompt or -PromptFile.' }

$assetPattern = '^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Discovery/storageContainers/[^/]+/storageAssets/[^/]+$'
foreach ($id in $StorageAssetId) {
  if ($id -notmatch $assetPattern) { throw "Not a Discovery storage asset ARM ID: $id" }
}

function Get-DiscoveryToken {
  $t = az account get-access-token --resource https://discovery.azure.com --query accessToken -o tsv --only-show-errors 2>$null
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($t)) {
    throw 'Azure CLI has no usable session for https://discovery.azure.com. Sign in yourself in a normal terminal (az login); this script never starts sign-in.'
  }
  return $t.Trim()
}

function Invoke-Discovery([string]$Method, [string]$Uri, $Body) {
  $headers = @{ Authorization = "Bearer $(Get-DiscoveryToken)"; Accept = 'application/json' }
  $params = @{ Method = $Method; Uri = $Uri; Headers = $headers; SkipHttpErrorCheck = $true; TimeoutSec = 120 }
  if ($null -ne $Body) {
    $params.ContentType = 'application/json; charset=utf-8'
    $params.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 30))
  }
  $response = Invoke-WebRequest @params
  [pscustomobject]@{ Status = [int]$response.StatusCode; Content = [string]$response.Content }
}

function Get-Excerpt([string]$Text, [int]$Max) {
  if ($null -eq $Text) { return '' }
  if ($Text.Length -le $Max) { return $Text }
  return $Text.Substring(0, $Max) + " ...[+$($Text.Length - $Max) chars]"
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $OutDir "$Label-$stamp"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

if (-not $Conversation) {
  if (-not $Investigation) {
    $slug = ($Label.ToLower() -replace '[^a-z0-9-]', '-' -replace '-+', '-').Trim('-')
    $slug = $slug.Substring(0, [Math]::Min(15, $slug.Length)).Trim('-')
    $Investigation = "$slug-$(Get-Date -Format 'MMddHHmm')"
  }
  if ($Investigation.Length -lt 3 -or $Investigation.Length -gt 24 -or $Investigation -notmatch '^[a-z0-9][a-z0-9-]*[a-z0-9]$') {
    throw "Investigation name must be 3-24 lowercase letters, digits, or hyphens: '$Investigation'"
  }
  $invUri = "$base/projects/$Project/investigations/$Investigation`?$api"
  $existing = Invoke-Discovery GET $invUri $null
  if ($existing.Status -ne 200) {
    $display = if ($InvestigationDisplayName) { $InvestigationDisplayName } else { "$Label ($Agent)" }
    $created = Invoke-Discovery PUT $invUri @{ description = "Scripted validation turn for agent $Agent."; displayName = $display }
    if ($created.Status -notin 200, 201) { throw "Investigation create failed: HTTP $($created.Status) $(Get-Excerpt $created.Content 400)" }
  }
  $conv = Invoke-Discovery POST "$base/conversations?$api" @{
    # Studio opens the conversation whose displayName equals the investigation name.
    displayName = $Investigation
    investigationName = "/projects/$Project/investigations/$Investigation"
    projectName = $Project
  }
  if ($conv.Status -notin 200, 201) { throw "Conversation create failed: HTTP $($conv.Status) $(Get-Excerpt $conv.Content 400)" }
  $Conversation = ($conv.Content | ConvertFrom-Json).name
  if (-not $Conversation) { throw 'Conversation create returned no name.' }
}

$request = [ordered]@{
  input = @(@{ type = 'message'; role = 'user'; content = @(@{ type = 'input_text'; text = $Prompt }) })
  agent = @{ type = 'agent_reference'; name = $Agent }
}
if ($StorageAssetId.Count -gt 0) { $request.discoveryProperties = @{ storageAssets = @($StorageAssetId) } }
$request | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 (Join-Path $runDir 'request.json')

Write-Host "PROJECT=$Project AGENT=$Agent"
Write-Host "INVESTIGATION=$Investigation"
Write-Host "CONVERSATION=$Conversation"
Write-Host "ATTACHED_ASSETS=$($StorageAssetId.Count)"

$post = Invoke-Discovery POST "$base/conversations/$Conversation/openai/v1/responses" $request
if ($post.Status -ge 400) {
  Set-Content -Encoding utf8 (Join-Path $runDir 'post-error.txt') $post.Content
  throw "Response POST failed: HTTP $($post.Status) $(Get-Excerpt $post.Content 600)"
}
$body = $post.Content | ConvertFrom-Json -Depth 100
$responseId = $body.id
Write-Host "RESPONSE_ID=$responseId"

$terminal = 'completed', 'failed', 'cancelled', 'expired', 'incomplete'
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
while ($body.status -notin $terminal -and (Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 15
  $poll = Invoke-Discovery GET "$base/conversations/$Conversation/openai/v1/responses/$responseId" $null
  if ($poll.Status -eq 200) {
    $body = $poll.Content | ConvertFrom-Json -Depth 100
    Write-Host ("{0} status={1}" -f (Get-Date -Format 'HH:mm:ss'), $body.status)
  } else {
    Write-Host ("{0} poll HTTP {1}" -f (Get-Date -Format 'HH:mm:ss'), $poll.Status)
  }
}

$body | ConvertTo-Json -Depth 100 | Set-Content -Encoding utf8 (Join-Path $runDir 'response.json')
$inputs = Invoke-Discovery GET "$base/conversations/$Conversation/openai/v1/responses/$responseId/input_items" $null
if ($inputs.Status -eq 200) { Set-Content -Encoding utf8 (Join-Path $runDir 'input-items.json') $inputs.Content }

$summary = [Collections.Generic.List[string]]::new()
$summary.Add("status=$($body.status) model=$($body.model) agent=$($body.agent_reference.name) version=$($body.agent_reference.version)")
if ($body.usage) { $summary.Add("usage input=$($body.usage.input_tokens) output=$($body.usage.output_tokens)") }
foreach ($item in @($body.output)) {
  switch ($item.type) {
    'function_call' {
      $summary.Add("--- function_call $($item.name)")
      try {
        $parsed = $item.arguments | ConvertFrom-Json -Depth 50
        foreach ($p in $parsed.PSObject.Properties) {
          if ($p.Value -is [string] -and $p.Value.Length -gt 200) {
            $summary.Add("  [$($p.Name)]")
            $summary.Add($p.Value)
          } else {
            $summary.Add("  $($p.Name) = $($p.Value | ConvertTo-Json -Depth 10 -Compress)")
          }
        }
      } catch { $summary.Add("  arguments: $(Get-Excerpt ([string]$item.arguments) $PreviewChars)") }
    }
    'function_call_output' { $summary.Add('--- function_call_output'); $summary.Add((Get-Excerpt ([string]$item.output) $PreviewChars)) }
    'message' {
      $text = (@($item.content) | ForEach-Object { $_.text }) -join "`n"
      $summary.Add('--- message'); $summary.Add((Get-Excerpt $text ($PreviewChars * 3)))
    }
    default { $summary.Add("--- $($item.type)") }
  }
}
if ($body.discoveryProperties.storageAssets) {
  $summary.Add('--- storage assets on response')
  $body.discoveryProperties.storageAssets | ForEach-Object { $summary.Add("  $_") }
}
if ($body.error) { $summary.Add("error: $($body.error | ConvertTo-Json -Depth 10 -Compress)") }

$summary | Set-Content -Encoding utf8 (Join-Path $runDir 'summary.txt')
$summary | ForEach-Object { Write-Host $_ }
Write-Host "EVIDENCE_DIR=$runDir"
Write-Host "FINAL_STATUS=$($body.status)"
if ($body.status -ne 'completed') { exit 1 }
