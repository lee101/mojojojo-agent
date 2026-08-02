# Install a checksummed mojojojo-agent Windows release without requiring Python.
$ErrorActionPreference = "Stop"

$repo = if ($env:MJJ_REPO) { $env:MJJ_REPO } else { "lee101/mojojojo-agent" }
$version = if ($env:MJJ_VERSION) { $env:MJJ_VERSION } else { "latest" }
$installDir = if ($env:MJJ_INSTALL_DIR) {
    $env:MJJ_INSTALL_DIR
} else {
    Join-Path $env:LOCALAPPDATA "Programs\mjj"
}
$architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
switch ($architecture) {
    "x64" { $arch = "x86_64" }
    default { throw "mjj: unsupported Windows architecture: $architecture" }
}
$asset = "mjj-windows-$arch.zip"

if ($env:MJJ_BASE_URL) {
    $base = $env:MJJ_BASE_URL.TrimEnd("/")
} elseif ($version -eq "latest") {
    $base = "https://github.com/$repo/releases/latest/download"
} else {
    $base = "https://github.com/$repo/releases/download/$version"
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) ("mjj-install-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $archive = Join-Path $temporary $asset
    $sums = Join-Path $temporary "SHA256SUMS"
    Write-Host "Downloading $asset..."
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$asset" -OutFile $archive
    Invoke-WebRequest -UseBasicParsing -Uri "$base/SHA256SUMS" -OutFile $sums

    $expected = $null
    foreach ($line in Get-Content $sums) {
        if ($line -match '^([0-9A-Fa-f]{64})\s+\*?(.+)$' -and $Matches[2] -eq $asset) {
            $expected = $Matches[1].ToLowerInvariant()
            break
        }
    }
    if (-not $expected) { throw "mjj: $asset has no published checksum" }
    $actual = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "mjj: checksum verification failed for $asset" }

    $expanded = Join-Path $temporary "expanded"
    Expand-Archive -Path $archive -DestinationPath $expanded
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    Copy-Item -Force (Join-Path $expanded "mjj.exe") (Join-Path $installDir "mjj.exe")

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathParts = @($userPath -split ';' | Where-Object { $_ })
    if ($pathParts -notcontains $installDir) {
        $newPath = (@($installDir) + $pathParts) -join ';'
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    }
    if (($env:Path -split ';') -notcontains $installDir) {
        $env:Path = "$installDir;$env:Path"
    }
    Write-Host "Installed mjj to $installDir\mjj.exe"
    Write-Host "Open a new terminal and run: mjj auth --probe"
} finally {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $temporary
}
