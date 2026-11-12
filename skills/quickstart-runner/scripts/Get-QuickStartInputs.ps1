#requires -Version 5.1
<#
.SYNOPSIS
Downloads verified BioPharma inputs only; never extracts or executes them.
.DESCRIPTION
The invoking assistant MUST obtain this script from the SAME exact Revision
used below, and explicitly read the downloaded quickstart-runner.md afterwards.
Do not fetch this script from a branch or infer setup/science approval from a
successful download. No Python, modules, credentials or external programs are used.
Without -ApproveDownload, only a bounded local plan is printed (no network/writes).
Destination must be new, absolute, local, <=120 characters, with an existing
parent and no reparse/symlink ancestors. Per-file limit: 2 GiB; metadata: 1 MiB.
Requests have a 120-second header/read timeout and a 30-minute total deadline.
Failures retain .partial files; reruns require a different new destination.
Source URLs have no query/auth/ports. GitHub release redirects alone may use
GitHub's release-assets.githubusercontent.com signed HTTPS delivery URLs.
No retries. Receipt paths are repository-relative, never machine-private paths.
Dot-source to load functions without executing the entry point.
#>
[CmdletBinding()]
param(
    [string]$Repository,
    [string]$Revision,
    [string]$Destination,
    [switch]$ApproveDownload
)

function Assert-QSIdentity {
    param([string]$Repository, [string]$Revision)
    if ($Repository -cnotmatch '\A[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\z') {
        throw 'Repository must be owner/repo, not a URL.'
    }
    if ($Revision -cnotmatch '\A[0-9a-fA-F]{40}\z') {
        throw 'Revision must be an exact 40-hex git commit SHA, not a branch or tag.'
    }
}

function Assert-QSPath {
    param([string]$Value, [switch]$Leaf)
    if ([string]::IsNullOrEmpty($Value) -or $Value.Length -gt 220 -or
        $Value -cnotmatch '\A[A-Za-z0-9_. /-]+\z' -or ($Leaf -and $Value.Contains('/'))) {
        throw 'Unsafe source path or filename.'
    }
    foreach ($part in $Value.Split('/')) {
        if (-not $part -or $part -eq '.' -or $part -eq '..' -or
            $part.EndsWith('.') -or $part.EndsWith(' ') -or $part.StartsWith(' ') -or
            $part -match '\A(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)') {
            throw 'Traversal or reserved filename rejected.'
        }
    }
}

function Assert-QSAncestors {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    while ($null -ne $item) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Symlink/reparse ancestors are not allowed.'
        }
        # Parent is a plain DirectoryInfo, not a provider-decorated PSIsContainer object.
        if (($item.Attributes -band [IO.FileAttributes]::Directory) -eq 0) { throw 'Destination parent must be a directory.' }
        $item = $item.Parent
    }
}

function Get-QSDestination {
    param([string]$Destination)
    if ([string]::IsNullOrWhiteSpace($Destination) -or $Destination.Length -gt 120 -or
        -not [IO.Path]::IsPathRooted($Destination) -or $Destination.StartsWith('\\') -or
        $Destination.StartsWith('//')) { throw 'Use a new short absolute local destination (maximum 120 characters).' }
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
        $Destination -notmatch '\A[A-Za-z]:[\\/]') { throw 'Use an absolute local drive path.' }
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($Destination))
        if ($drive.DriveType -ne [IO.DriveType]::Fixed -and $drive.DriveType -ne [IO.DriveType]::Removable) {
            throw 'Destination must be on a local fixed or removable drive, not a network mapping.'
        }
    }
    $full = [IO.Path]::GetFullPath($Destination)
    $leaf = [IO.Path]::GetFileName($full)
    Assert-QSPath $leaf -Leaf
    if ($leaf.Length -gt 64 -or $full.Length -gt 120) { throw 'Destination is too long.' }
    # Inspect literal ancestors too: normalization must not hide a link followed by '..'.
    $root = [IO.Path]::GetPathRoot($Destination)
    $cursor = $root
    $parts = $Destination.Substring($root.Length) -split '[\\/]'
    for ($i = 0; $i -lt ($parts.Length - 1); $i++) {
        if ($parts[$i] -eq '.' -or $parts[$i] -eq '..' -or -not $parts[$i]) { throw 'Destination traversal rejected.' }
        $cursor = [IO.Path]::Combine($cursor, $parts[$i])
        Assert-QSAncestors $cursor
    }
    Assert-QSAncestors ([IO.Path]::GetDirectoryName($full))
    if (Test-Path -LiteralPath $full) { throw 'Destination already exists; nothing will be overwritten.' }
    # Get-Item also detects dangling links on supported PowerShell hosts.
    if ($null -ne (Get-Item -LiteralPath $full -Force -ErrorAction SilentlyContinue)) { throw 'Destination already exists.' }
    return $full
}

function Get-QSRawUrl {
    param([string]$Repository, [string]$Revision, [string]$Path)
    Assert-QSIdentity $Repository $Revision
    Assert-QSPath $Path
    $escaped = ($Path.Split('/') | ForEach-Object { [Uri]::EscapeDataString($_) }) -join '/'
    return "https://raw.githubusercontent.com/$Repository/$Revision/$escaped"
}

function Assert-QSReleaseUrl {
    param([string]$Url)
    # Match the original string, not a URI normalized to hide ports or traversal.
    if ($Url -cnotmatch '\Ahttps://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9][A-Za-z0-9._-]{0,99})/releases/download/([A-Za-z0-9][A-Za-z0-9._-]{0,127})/cdk_non_md_deliverables\.zip\z') {
        throw 'Snapshot URL must be a public HTTPS GitHub versioned release asset without query, auth or port.'
    }
    $tag = $Matches[3]
    if ($tag -match '(?i)(latest|nightly|rolling)' -or $tag -in @('main', 'master', 'HEAD')) {
        throw 'Snapshot release must use a fixed version, not a moving alias.'
    }
}

function Get-QSProperty {
    param($Object, [string]$Name)
    if ($null -ne $Object) {
        $property = $Object.PSObject.Properties[$Name]
        if ($null -ne $property) { return $property.Value }
    }
    return $null
}

function Get-QSEntries {
    param($Inventory, [string]$Repository, [string]$Revision)
    Assert-QSIdentity $Repository $Revision
    $version = Get-QSProperty $Inventory 'formatVersion'
    if (($version -isnot [int] -and $version -isnot [long]) -or $version -ne 1) { throw 'Unsupported inventory formatVersion.' }
    $record = Get-QSProperty (Get-QSProperty $Inventory 'packages') 'biopharma'
    $approved = Get-QSProperty $record 'publicationApproved'
    if ($approved -isnot [bool] -or -not $approved) { throw 'BioPharma inventory is not publicationApproved.' }
    $required = @(
        'cdk_operational_bundle_v1.zip', 'cdk_operational_bundle_v1.sha256',
        'cdk_operational_bundle_v1_manifest.csv', 'cdk_operational_bundle_v1_validation.json',
        'cdk_non_md_deliverables.zip', 'cdk_non_md_deliverables.sha256'
    )
    $guide = Get-QSProperty $record 'guide'
    $files = @(Get-QSProperty $record 'files')
    if ($null -eq $guide -or $files.Count -ne 6) { throw 'Inventory must contain one guide and exactly six input files.' }
    $names = @{}
    $paths = @{}
    $entries = @()
    foreach ($entry in (@($guide) + $files)) {
        $name = Get-QSProperty $entry 'name'
        $path = Get-QSProperty $entry 'path'
        $bytes = Get-QSProperty $entry 'bytes'
        $hash = Get-QSProperty $entry 'sha256'
        if ($name -isnot [string] -or $path -isnot [string]) { throw 'Missing inventory name/path.' }
        Assert-QSPath $name -Leaf
        Assert-QSPath $path
        if ($path.Split('/')[-1] -cne $name) { throw 'Source filename must match inventory name.' }
        if ($names.ContainsKey($name) -or $paths.ContainsKey($path)) { throw 'Duplicate filename or source path.' }
        $names[$name] = $true
        $paths[$path] = $true
        if (($bytes -isnot [int] -and $bytes -isnot [long]) -or $bytes -le 0 -or $bytes -gt 2147483648) {
            throw 'Inventory bytes must be a positive integer no greater than 2 GiB.'
        }
        if ($hash -isnot [string] -or $hash -cnotmatch '\A[0-9a-fA-F]{64}\z') { throw 'Invalid SHA-256.' }
        if ($entries.Count -eq 0) {
            if ($name -cnotmatch '\AQuickStart-BioPharma[^/]*\.md\z') { throw 'Expected a BioPharma Markdown guide.' }
        } elseif ($required -cnotcontains $name) { throw 'Unexpected BioPharma input filename.' }
        $url = Get-QSRawUrl $Repository $Revision $path
        $explicit = Get-QSProperty $entry 'url'
        if ($null -ne $entry.PSObject.Properties['url']) {
            if ($name -cne 'cdk_non_md_deliverables.zip' -or $explicit -isnot [string]) {
                throw 'Only the snapshot may specify an explicit URL.'
            }
            Assert-QSReleaseUrl $explicit
            if ((Get-QSProperty $record 'releaseUrl') -cne $explicit) { throw 'releaseUrl must match the snapshot URL.' }
            $url = $explicit
        }
        $entries += [pscustomobject]@{ name = $name; path = $path; bytes = [long]$bytes; sha256 = $hash.ToLowerInvariant(); url = $url }
    }
    $release = Get-QSProperty $record 'releaseUrl'
    if ($null -ne $release) {
        Assert-QSReleaseUrl $release
        if (($entries | Where-Object name -CEQ 'cdk_non_md_deliverables.zip').url -cne $release) {
            throw 'releaseUrl must match the snapshot URL.'
        }
    }
    return $entries
}

function Assert-QSTransportUrl {
    param([string]$Url, [string]$ReleaseSource)
    if ($Url -cmatch '\Ahttps://raw\.githubusercontent\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/[0-9a-fA-F]{40}/[A-Za-z0-9_./% -]+\z') {
        $decoded = [Uri]::UnescapeDataString(([Uri]$Url).AbsolutePath.TrimStart('/'))
        Assert-QSPath $decoded
        return
    }
    if ($ReleaseSource -and $Url -ceq $ReleaseSource) { Assert-QSReleaseUrl $Url; return }
    if ($ReleaseSource) {
        Assert-QSReleaseUrl $ReleaseSource
        # GitHub's signed CDN query is transport-only; never inventory-controlled.
        if ($Url -cmatch '\Ahttps://release-assets\.githubusercontent\.com/[^\s#\\]+\z') {
            $uri = [Uri]$Url
            if (-not $uri.UserInfo -and $uri.Port -eq 443 -and $uri.AbsolutePath.StartsWith('/github-production-release-asset/')) { return }
        }
    }
    throw 'Disallowed HTTP source or redirect host.'
}

function Copy-QSBoundedStream {
    param([IO.Stream]$InputStream, [IO.Stream]$OutputStream, [long]$MaxBytes,
          [string]$Label, [Diagnostics.Stopwatch]$Clock)
    if ($MaxBytes -le 0 -or $MaxBytes -gt 2147483648) { throw 'Invalid stream size limit.' }
    if ($null -eq $Clock) { $Clock = [Diagnostics.Stopwatch]::StartNew() }
    $buffer = New-Object byte[] 65536
    $total = [long]0
    try {
        while ($true) {
            if ($Clock.Elapsed.TotalSeconds -ge 1800) { throw 'Download exceeded its 30-minute deadline.' }
            if ($InputStream.CanTimeout) {
                $InputStream.ReadTimeout = [int][Math]::Max(1, [Math]::Min(120000, (1800000 - $Clock.ElapsedMilliseconds)))
            }
            $count = [int][Math]::Min($buffer.Length, $MaxBytes + 1 - $total)
            $read = $InputStream.Read($buffer, 0, $count)
            if ($read -eq 0) { break }
            $OutputStream.Write($buffer, 0, $read)
            $total += $read
            if ($total -gt $MaxBytes) { throw 'Response exceeds allowed byte length (read capped at limit + 1).' }
            Write-Progress -Activity "Downloading $Label" -Status "$total / $MaxBytes bytes" -PercentComplete ([int](100 * $total / $MaxBytes))
        }
        return $total
    } finally { Write-Progress -Activity "Downloading $Label" -Completed }
}

function Receive-QSHttp {
    param([string]$Url, [IO.Stream]$OutputStream, [long]$MaxBytes, [string]$Label)
    $release = $null
    if ($Url.StartsWith('https://github.com/')) { Assert-QSReleaseUrl $Url; $release = $Url }
    $current = $Url
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $tls = [Net.ServicePointManager]::SecurityProtocol
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        for ($hop = 0; $hop -le 3; $hop++) {
            Assert-QSTransportUrl $current $release
            $remaining = 1800000 - $clock.ElapsedMilliseconds
            if ($remaining -le 0) { throw 'Download deadline exceeded.' }
            $request = [Net.HttpWebRequest]::Create($current)
            $request.AllowAutoRedirect = $false
            $request.UseDefaultCredentials = $false
            $request.Credentials = $null
            $request.Proxy = $null
            $request.PreAuthenticate = $false
            $request.AutomaticDecompression = [Net.DecompressionMethods]::None
            $request.Timeout = [int][Math]::Min(120000, $remaining)
            $request.ReadWriteTimeout = $request.Timeout
            $request.UserAgent = 'QuickStart-Input-Downloader/1'
            $response = $null
            $stream = $null
            try {
                $response = $request.GetResponse()
                $status = [int]$response.StatusCode
                if ($status -in @(301, 302, 303, 307, 308)) {
                    if (-not $release -or $hop -eq 3) { throw 'Unexpected or excessive redirects.' }
                    $next = $response.Headers['Location']
                    Assert-QSTransportUrl $next $release
                    # A release redirect cannot switch to arbitrary raw repository content.
                    if (-not $next.StartsWith('https://release-assets.githubusercontent.com/')) { throw 'Release must redirect only to GitHub release assets.' }
                    $current = $next
                    continue
                }
                if ($status -ne 200) { throw 'Expected HTTP 200.' }
                if ($response.ContentLength -gt $MaxBytes) { throw 'Content-Length exceeds allowed byte length.' }
                $stream = $response.GetResponseStream()
                return Copy-QSBoundedStream $stream $OutputStream $MaxBytes $Label $clock
            } catch {
                # Do not echo signed delivery URLs, machine paths or proxy details.
                throw "Download failed for $Label (HTTP, timeout, redirect or size validation); no retry was attempted."
            } finally {
                if ($null -ne $stream) { $stream.Dispose() }
                if ($null -ne $response) { $response.Dispose() }
                $request.Abort()
            }
        }
    } finally { [Net.ServicePointManager]::SecurityProtocol = $tls }
}

function Get-QSInventory {
    param([string]$Url)
    $memory = New-Object IO.MemoryStream
    try {
        $null = Receive-QSHttp $Url $memory 1048576 'downloads.json'
        if ($memory.Length -eq 0) { throw 'Empty inventory.' }
        $utf8 = New-Object Text.UTF8Encoding($false, $true)
        return ($utf8.GetString($memory.ToArray()).TrimStart([char]0xFEFF) | ConvertFrom-Json -ErrorAction Stop)
    } finally { $memory.Dispose() }
}

function Save-QSFile {
    param($Entry, [string]$Destination)
    Assert-QSAncestors $Destination
    $target = [IO.Path]::Combine($Destination, $Entry.name)
    $partial = $target + '.partial'
    if ((Test-Path -LiteralPath $target) -or (Test-Path -LiteralPath $partial)) { throw 'Refusing to overwrite an existing file or partial.' }
    $file = $null
    $sha = $null
    try {
        $file = [IO.File]::Open($partial, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $null = Receive-QSHttp $Entry.url $file $Entry.bytes $Entry.name
        $file.Flush()
        $length = $file.Length
        if ($length -le 0 -or $length -gt $Entry.bytes -or ($Entry.sha256 -and $length -ne $Entry.bytes)) {
            throw "Length mismatch for $($Entry.name); partial retained."
        }
        $file.Position = 0
        $sha = [Security.Cryptography.SHA256]::Create()
        $hash = ([BitConverter]::ToString($sha.ComputeHash($file))).Replace('-', '').ToLowerInvariant()
        if ($Entry.sha256 -and $hash -cne $Entry.sha256) { throw "SHA-256 mismatch for $($Entry.name); partial retained." }
    } finally {
        if ($null -ne $sha) { $sha.Dispose() }
        if ($null -ne $file) { $file.Dispose() }
    }
    Assert-QSAncestors $Destination
    [IO.File]::Move($partial, $target) # Two-argument Move never overwrites.
    Write-Host ("Verified {0}: {1} bytes; SHA-256 {2}" -f $Entry.name, $length, $hash)
    return [pscustomobject][ordered]@{ name = $Entry.name; path = $Entry.path; url = $Entry.url; bytes = $length; sha256 = $hash }
}

function Invoke-QSDownload {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Repository,
          [Parameter(Mandatory=$true)][string]$Revision,
          [Parameter(Mandatory=$true)][string]$Destination, [switch]$ApproveDownload)
        $ErrorActionPreference = 'Stop'
    Assert-QSIdentity $Repository $Revision
    $folder = Get-QSDestination $Destination
    $inventoryUrl = Get-QSRawUrl $Repository $Revision 'skills/quickstart-runner/references/downloads.json'
    Write-Host "Source repository: $Repository; exact commit: $Revision"
    if (-not $ApproveDownload) {
        Write-Host 'PLAN ONLY: no network or writes. Approval would fetch bounded downloads.json, validate publication approval and all seven records, then download the guide and six inputs, shared SKILL.md and LICENSE.'
        Write-Host 'Maximum: 2 GiB per input; 1 MiB each for inventory, skill and license. No retries, extraction, installation or science. Fetch this script at the same commit before invoking it.'
        return
    }
    $inventory = Get-QSInventory $inventoryUrl
    $entries = @(Get-QSEntries $inventory $Repository $Revision)
    foreach ($extra in @(@('skills/quickstart-runner/SKILL.md', 'quickstart-runner.md'), @('LICENSE', 'LICENSE'))) {
        $entries += [pscustomobject]@{ path = $extra[0]; name = $extra[1]; url = (Get-QSRawUrl $Repository $Revision $extra[0]); bytes = [long]1048576; sha256 = $null }
    }
    # Recheck after network/validation, before the first filesystem write.
    $folder = Get-QSDestination $Destination
    $null = New-Item -Path $folder -ItemType Directory -ErrorAction Stop
    $results = @()
    foreach ($entry in $entries) {
        Write-Host "Source: $($entry.path) at commit $Revision"
        $results += Save-QSFile $entry $folder
    }
    $receipt = [ordered]@{ formatVersion = 1; stage = 'downloaded-only'; repository = $Repository;
        sourceCommit = $Revision; inventoryUrl = $inventoryUrl; files = $results }
    $receiptPath = [IO.Path]::Combine($folder, 'QuickStart-download-receipt.json')
    $receiptPartial = $receiptPath + '.partial'
    Assert-QSAncestors $folder
    $output = [IO.File]::Open($receiptPartial, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $utf8 = New-Object Text.UTF8Encoding($false)
        $data = $utf8.GetBytes(($receipt | ConvertTo-Json -Depth 8))
        $output.Write($data, 0, $data.Length)
    } finally { $output.Dispose() }
    [IO.File]::Move($receiptPartial, $receiptPath)
    Write-Host 'Downloaded-only: nine files verified and receipt saved. No extraction, installation or science performed. Assistant must explicitly read quickstart-runner.md and the guide; further actions require their own approval.'
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        if (-not $Repository -or -not $Revision -or -not $Destination) { throw 'Repository, exact Revision and Destination are required.' }
        Invoke-QSDownload -Repository $Repository -Revision $Revision -Destination $Destination -ApproveDownload:$ApproveDownload
    } catch { Write-Error -Message $_.Exception.Message -ErrorAction Continue; exit 1 }
}