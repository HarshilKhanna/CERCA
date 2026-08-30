# CERCA Deployment Setup Script
# ------------------------------
# Run this ONCE before `docker compose up`.
# It does three things:
#   1. Checks prerequisites (Docker, Python, git, free RAM)
#   2. Sparse-clones the DeathStarBench socialNetwork config files into ./dsb/
#   3. Verifies the resulting directory layout is correct
#
# Usage (from e:\CERCA\):
#   powershell -ExecutionPolicy Bypass -File scripts\setup_dsb.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot   # e:\CERCA

Write-Host ""
Write-Host "=== CERCA Pre-deployment Setup ===" -ForegroundColor Cyan
Write-Host ""

# --- 1. Prerequisite checks ---------------------------------------------------

function Check-Command {
    param([string]$cmd, [string]$friendly)
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "  [FAIL]  $friendly not found in PATH" -ForegroundColor Red
        return $false
    }
    Write-Host "  [OK]    $friendly found" -ForegroundColor Green
    return $true
}

Write-Host "-- Checking prerequisites --"
$allOk = $true
if (-not (Check-Command "docker" "Docker"))  { $allOk = $false }
if (-not (Check-Command "git"    "Git"))     { $allOk = $false }
if (-not (Check-Command "python" "Python 3")){ $allOk = $false }

# Docker daemon running?
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK]    Docker daemon is running" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL]  Docker daemon is NOT running - start Docker Desktop first" -ForegroundColor Red
        $allOk = $false
    }
} catch {
    Write-Host "  [FAIL]  Docker daemon is NOT running - start Docker Desktop first" -ForegroundColor Red
    $allOk = $false
}

# RAM check
$ramBytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
$ramGB    = [math]::Round($ramBytes / 1073741824, 1)
if ($ramGB -ge 8) {
    Write-Host "  [OK]    Physical RAM: $ramGB GB" -ForegroundColor Green
} else {
    Write-Host "  [WARN]  Physical RAM: $ramGB GB (less than 8 GB - ensure Docker Desktop memory >= 5 GB)" -ForegroundColor Yellow
}

if (-not $allOk) {
    Write-Host ""
    Write-Host "Fix the above issues and re-run this script." -ForegroundColor Red
    exit 1
}

# --- 2. Sparse-clone DeathStarBench socialNetwork ----------------------------

$dsbDir = Join-Path $root "dsb"
$snDir  = Join-Path $dsbDir "socialNetwork"
$nginxDir = Join-Path $snDir "nginx-web-server"

if (Test-Path $nginxDir) {
    Write-Host ""
    Write-Host "-- DSB socialNetwork already present - skipping download" -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "-- Downloading DeathStarBench socialNetwork (zip from GitHub) --"

    # Download the repo as a zip and extract only the socialNetwork/ subdirectory.
    # This completely avoids git checkout and the Windows illegal-filename problem
    # caused by 'tester-0*...' files in daprApps_v1/.

    $zipUrl  = "https://github.com/delimitrou/DeathStarBench/archive/refs/heads/master.zip"
    $zipPath = Join-Path $env:TEMP "DeathStarBench.zip"
    $extractBase = Join-Path $env:TEMP "DeathStarBench_extract"

    # Clean up any previous failed download
    if (Test-Path $zipPath)       { Remove-Item -Force $zipPath }
    if (Test-Path $extractBase)   { Remove-Item -Recurse -Force $extractBase }

    Write-Host "  Downloading $zipUrl ..."
    Write-Host "  (This is ~30-50 MB - may take a minute on slow connections)"

    try {
        # Use TLS 1.2 - GitHub requires it
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    } catch {
        Write-Host "  [FAIL]  Download failed: $_" -ForegroundColor Red
        exit 1
    }

    Write-Host "  [OK]    Download complete" -ForegroundColor Green
    Write-Host "  Extracting socialNetwork/ from zip ..."

    # Extract using .NET ZipFile - skips files with illegal Windows characters
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)

    New-Item -ItemType Directory -Force -Path $dsbDir | Out-Null

    $extracted = 0
    $skipped   = 0
    foreach ($entry in $zip.Entries) {
        # Only extract entries inside the socialNetwork/ folder
        # Zip root is DeathStarBench-master/socialNetwork/...
        if (-not $entry.FullName.Contains("/socialNetwork/")) { continue }

        # Strip the leading "DeathStarBench-master/" prefix
        $relative = $entry.FullName -replace '^[^/]+/', ''
        $destPath = Join-Path $dsbDir $relative.Replace('/', '\')

        # Skip entries with Windows-illegal characters
        $illegalChars = [System.IO.Path]::GetInvalidPathChars() + [char]'*' + [char]'?'
        $hasIllegal = $false
        foreach ($ch in $illegalChars) {
            if ($relative.Contains($ch)) { $hasIllegal = $true; break }
        }
        if ($hasIllegal) { $skipped++; continue }

        if ($entry.FullName.EndsWith('/')) {
            # Directory entry
            New-Item -ItemType Directory -Force -Path $destPath | Out-Null
        } else {
            # File entry - ensure parent dir exists
            $parentDir = Split-Path $destPath -Parent
            if (-not (Test-Path $parentDir)) {
                New-Item -ItemType Directory -Force -Path $parentDir | Out-Null
            }
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destPath, $true)
            $extracted++
        }
    }
    $zip.Dispose()

    # Clean up zip and temp extract
    Remove-Item -Force $zipPath

    Write-Host "  [OK]    Extracted $extracted files ($skipped skipped - illegal chars)" -ForegroundColor Green

    if ($extracted -eq 0) {
        Write-Host "  [FAIL]  No files extracted - check network and retry" -ForegroundColor Red
        exit 1
    }
}

# --- 3. Verify required paths -------------------------------------------------

Write-Host ""
Write-Host "-- Verifying DSB file layout --"

$required = @(
    "dsb\socialNetwork\nginx-web-server\conf\nginx.conf",
    "dsb\socialNetwork\nginx-web-server\lua-scripts",
    "dsb\socialNetwork\nginx-web-server\gen-lua",
    "dsb\socialNetwork\nginx-web-server\pages",
    "dsb\socialNetwork\config\jaeger-config.yml"
)

$layoutOk = $true
foreach ($rel in $required) {
    $full = Join-Path $root $rel
    if (Test-Path $full) {
        Write-Host "  [OK]    $rel" -ForegroundColor Green
    } else {
        Write-Host "  [MISS]  $rel" -ForegroundColor Red
        $layoutOk = $false
    }
}

if (-not $layoutOk) {
    Write-Host ""
    Write-Host "Some DSB files are missing. The sparse checkout may be incomplete." -ForegroundColor Red
    Write-Host "Try running these commands manually:"
    Write-Host "  cd $dsbDir"
    Write-Host "  git sparse-checkout reapply"
    Write-Host "  git checkout"
    exit 1
}

# --- 4. Check Python dependencies ---------------------------------------------

Write-Host ""
Write-Host "-- Checking Python dependencies --"

$pkgs   = @("requests", "pandas", "tqdm", "networkx", "matplotlib", "numpy")
$needInstall = $false

foreach ($pkg in $pkgs) {
    $null = python -c "import $pkg" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK]    $pkg" -ForegroundColor Green
    } else {
        Write-Host "  [MISS]  $pkg" -ForegroundColor Yellow
        $needInstall = $true
    }
}

if ($needInstall) {
    Write-Host ""
    Write-Host "  Installing missing packages from requirements.txt ..."
    $reqFile = Join-Path $root "requirements.txt"
    python -m pip install -r $reqFile --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK]    Packages installed" -ForegroundColor Green
    } else {
        Write-Host "  [WARN]  pip install returned errors - check output above" -ForegroundColor Yellow
    }
}

# --- Done ---------------------------------------------------------------------

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1.  docker compose up -d"
Write-Host "  2.  Wait ~90 seconds for all services to start"
Write-Host "  3.  python scripts\init_social_graph.py"
Write-Host "  4.  python scripts\generate_workload.py --duration 120"
Write-Host "  5.  python scripts\run_collection.py --lookback 10m"
Write-Host "  6.  python scripts\verify_collection.py"
Write-Host ""
