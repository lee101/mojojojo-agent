# Install a checksummed mojojojo-agent Windows release without requiring Python.
[CmdletBinding()]
param(
    [string]$Version,
    [string]$InstallDir,
    [string]$Repo,
    [string]$BaseUrl,
    [switch]$NoPathUpdate
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if (-not $Repo) { $Repo = if ($env:MJJ_REPO) { $env:MJJ_REPO } else { "lee101/mojojojo-agent" } }
if (-not $Version) { $Version = if ($env:MJJ_VERSION) { $env:MJJ_VERSION } else { "latest" } }
if (-not $InstallDir) {
    $InstallDir = if ($env:MJJ_INSTALL_DIR) {
        $env:MJJ_INSTALL_DIR
    } else {
        Join-Path $env:LOCALAPPDATA "Programs\mjj"
    }
}
if (-not $BaseUrl -and $env:MJJ_BASE_URL) { $BaseUrl = $env:MJJ_BASE_URL }
if ($env:MJJ_NO_PATH_UPDATE -match '^(1|true|yes)$') { $NoPathUpdate = $true }

if ($Repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw "mjj: repository must be OWNER/REPO" }
if ($Version -ne "latest" -and $Version -notmatch '^v\d') {
    if ($Version -match '^\d') { $Version = "v$Version" }
    else { throw "mjj: invalid release version: $Version" }
}

$architectureValue = [System.Runtime.InteropServices.RuntimeInformation,mscorlib]::OSArchitecture
if ($null -eq $architectureValue) { $architectureValue = $env:PROCESSOR_ARCHITEW6432 }
if (-not $architectureValue) { $architectureValue = $env:PROCESSOR_ARCHITECTURE }
if (-not $architectureValue) { throw "mjj: unable to detect Windows architecture" }
$architecture = ([string]$architectureValue).ToLowerInvariant()
switch ($architecture) {
    { $_ -in "x64", "amd64", "x86_64" } { $arch = "x86_64"; break }
    default { throw "mjj: unsupported Windows architecture: $architecture" }
}
$asset = "mjj-windows-$arch.zip"

if ($BaseUrl) {
    $base = $BaseUrl.TrimEnd("/")
} elseif ($Version -eq "latest") {
    $base = "https://github.com/$Repo/releases/latest/download"
} else {
    $base = "https://github.com/$Repo/releases/download/$Version"
}

function Get-MjjAsset {
    param([Parameter(Mandatory)][string]$Uri, [Parameter(Mandatory)][string]$Destination)

    if ($Uri.StartsWith("file://", [StringComparison]::OrdinalIgnoreCase)) {
        $localPath = ([Uri]$Uri).LocalPath
        Copy-Item -Force $localPath $Destination
        return
    }
    $lastError = $null
    foreach ($attempt in 1..3) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $Destination -TimeoutSec 30
            return
        } catch {
            $lastError = $_
            if ($attempt -lt 3) { Start-Sleep -Seconds $attempt }
        }
    }
    throw "mjj: failed to download $Uri after 3 attempts: $lastError"
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) ("mjj-install-" + [guid]::NewGuid())
$staged = $null
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $archive = Join-Path $temporary $asset
    $sums = Join-Path $temporary "SHA256SUMS"
    Write-Host "Downloading $asset..."
    Get-MjjAsset -Uri "$base/$asset" -Destination $archive
    Get-MjjAsset -Uri "$base/SHA256SUMS" -Destination $sums

    $expected = $null
    foreach ($line in Get-Content $sums) {
        if ($line -match '^([0-9A-Fa-f]{64})\s+\*?(.+)$' -and $Matches[2] -eq $asset) {
            $expected = $Matches[1].ToLowerInvariant()
            break
        }
    }
    if (-not $expected) { throw "mjj: $asset has no valid published checksum" }
    $actual = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "mjj: checksum verification failed for $asset" }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $entries = @($zip.Entries)
        if ($entries.Count -ne 1 -or $entries[0].FullName -ne "mjj.exe" -or $entries[0].Length -le 0) {
            throw "mjj: release archive must contain exactly one root file named mjj.exe"
        }
    } finally {
        $zip.Dispose()
    }

    $expanded = Join-Path $temporary "expanded"
    Expand-Archive -Path $archive -DestinationPath $expanded
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $staged = Join-Path $InstallDir (".mjj.install." + [guid]::NewGuid() + ".exe")
    Copy-Item -Force (Join-Path $expanded "mjj.exe") $staged

    $smoke = Start-Process -FilePath $staged -ArgumentList "--version" -Wait -PassThru -NoNewWindow
    if ($smoke.ExitCode -ne 0) { throw "mjj: downloaded executable failed its smoke test" }

    $destination = Join-Path $InstallDir "mjj.exe"
    if (Test-Path $destination) {
        [IO.File]::Replace($staged, $destination, $null, $true)
    } else {
        [IO.File]::Move($staged, $destination)
    }
    $staged = $null

    if (-not $NoPathUpdate) {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $pathParts = @($userPath -split ';' | Where-Object { $_ })
        if ($pathParts -notcontains $InstallDir) {
            $newPath = (@($InstallDir) + $pathParts) -join ';'
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        }
        if (($env:Path -split ';') -notcontains $InstallDir) {
            $env:Path = "$InstallDir;$env:Path"
        }
    }
    Write-Host "Installed mjj to $destination"
    if ($NoPathUpdate) {
        Write-Host "Add $InstallDir to PATH, then run: mjj auth --probe"
    } else {
        Write-Host "Open a new terminal and run: mjj auth --probe"
    }
} finally {
    if ($staged -and (Test-Path $staged)) {
        Remove-Item -Force -ErrorAction SilentlyContinue $staged
    }
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $temporary
}
