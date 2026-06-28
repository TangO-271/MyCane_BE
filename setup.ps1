<# 
.SYNOPSIS
    🛰️ Satellite Team — Setup Script (Windows)
    ติดตั้งทุกอย่างที่ต้องใช้ในคำสั่งเดียว

.DESCRIPTION
    สคริปต์นี้จะ:
    1. ตรวจสอบ prerequisites (Python, Docker, Git)
    2. สร้าง Python virtual environment
    3. ติดตั้ง dependencies ทั้งหมด
    4. สร้างไฟล์ .env จาก .env.example
    5. สร้างโฟลเดอร์ data
    6. รัน quick test
    7. (Optional) เปิด Docker containers

.USAGE
    .\setup.ps1              # ติดตั้งปกติ
    .\setup.ps1 -WithDocker  # ติดตั้ง + เปิด Docker
    .\setup.ps1 -SkipVenv    # ข้าม venv (ใช้ system Python)
#>

param(
    [switch]$WithDocker,
    [switch]$SkipVenv,
    [switch]$Help
)

# ===========================
# Config
# ===========================
$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot "venv"
$EnvFile = Join-Path $ProjectRoot ".env"
$EnvExample = Join-Path $ProjectRoot ".env.example"

# ===========================
# Helpers
# ===========================
function Write-Step {
    param([string]$Step, [string]$Message)
    Write-Host ""
    Write-Host "=" * 60 -ForegroundColor DarkGray
    Write-Host "  [$Step] $Message" -ForegroundColor Cyan
    Write-Host "=" * 60 -ForegroundColor DarkGray
}

function Write-OK {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  [!!] $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host "  [FAIL] $Message" -ForegroundColor Red
}

function Test-Command {
    param([string]$Command)
    try {
        Get-Command $Command -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

# ===========================
# Help
# ===========================
if ($Help) {
    Write-Host ""
    Write-Host "Satellite Team Setup Script" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  .\setup.ps1              # Standard setup"
    Write-Host "  .\setup.ps1 -WithDocker  # Setup + start Docker containers"
    Write-Host "  .\setup.ps1 -SkipVenv    # Skip virtual environment creation"
    Write-Host "  .\setup.ps1 -Help        # Show this help"
    Write-Host ""
    exit 0
}

# ===========================
# Start
# ===========================
Write-Host ""
Write-Host "  ================================================" -ForegroundColor Cyan
Write-Host "       Satellite Team - Setup Script" -ForegroundColor Cyan
Write-Host "       Project: TaSawan (Hackathon 2026)" -ForegroundColor DarkCyan
Write-Host "  ================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Project root: $ProjectRoot"
Write-Host "  Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# ===========================
# Step 1: Check Prerequisites
# ===========================
Write-Step "1/7" "Checking prerequisites..."

$allGood = $true

# Python
if (Test-Command "python") {
    $pyVersion = python --version 2>&1
    Write-OK "Python: $pyVersion"
    
    # Check minimum version
    $versionMatch = [regex]::Match($pyVersion, '(\d+)\.(\d+)')
    if ($versionMatch.Success) {
        $major = [int]$versionMatch.Groups[1].Value
        $minor = [int]$versionMatch.Groups[2].Value
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
            Write-Warn "Python 3.10+ recommended (you have $major.$minor)"
        }
    }
} else {
    Write-Fail "Python not found!"
    Write-Host "    Download: https://www.python.org/downloads/" -ForegroundColor Gray
    Write-Host "    Make sure to check 'Add Python to PATH' during installation" -ForegroundColor Gray
    $allGood = $false
}

# pip
if (Test-Command "pip") {
    Write-OK "pip: available"
} else {
    Write-Warn "pip not found as standalone command (will try python -m pip)"
}

# Git
if (Test-Command "git") {
    $gitVersion = git --version 2>&1
    Write-OK "Git: $gitVersion"
} else {
    Write-Warn "Git not found (optional but recommended)"
    Write-Host "    Download: https://git-scm.com/download/win" -ForegroundColor Gray
}

# Docker (optional)
if (Test-Command "docker") {
    $dockerVersion = docker --version 2>&1
    Write-OK "Docker: $dockerVersion"
} else {
    Write-Warn "Docker not found (needed for PostGIS + MinIO)"
    Write-Host "    Download: https://www.docker.com/products/docker-desktop/" -ForegroundColor Gray
    if ($WithDocker) {
        Write-Fail "Cannot start Docker containers without Docker installed!"
        $WithDocker = $false
    }
}

if (-not $allGood) {
    Write-Host ""
    Write-Fail "Missing required prerequisites. Please install them and try again."
    exit 1
}

# ===========================
# Step 2: Create Virtual Environment
# ===========================
Write-Step "2/7" "Setting up Python virtual environment..."

if ($SkipVenv) {
    Write-Warn "Skipping venv creation (-SkipVenv flag)"
    $PipCmd = "pip"
    $PythonCmd = "python"
} else {
    if (Test-Path $VenvPath) {
        Write-OK "venv already exists at: $VenvPath"
    } else {
        Write-Host "  Creating venv..." -ForegroundColor Gray
        python -m venv $VenvPath
        Write-OK "venv created at: $VenvPath"
    }
    
    $PipCmd = Join-Path $VenvPath "Scripts\pip.exe"
    $PythonCmd = Join-Path $VenvPath "Scripts\python.exe"
    
    # Upgrade pip
    Write-Host "  Upgrading pip..." -ForegroundColor Gray
    & $PythonCmd -m pip install --upgrade pip --quiet 2>&1 | Out-Null
    Write-OK "pip upgraded"
}

# ===========================
# Step 3: Install Dependencies
# ===========================
Write-Step "3/7" "Installing Python dependencies..."

$requirementsFile = Join-Path $ProjectRoot "requirements.txt"

if (Test-Path $requirementsFile) {
    Write-Host "  Installing from requirements.txt..." -ForegroundColor Gray
    Write-Host "  (This may take a few minutes on first run)" -ForegroundColor DarkGray
    
    & $PipCmd install -r $requirementsFile 2>&1 | ForEach-Object {
        if ($_ -match "^Successfully installed") {
            Write-OK $_
        } elseif ($_ -match "^Requirement already satisfied") {
            # silent
        } elseif ($_ -match "ERROR") {
            Write-Warn $_
        }
    }
    
    Write-OK "Dependencies installed"
} else {
    Write-Fail "requirements.txt not found at: $requirementsFile"
    exit 1
}

# ===========================
# Step 4: Setup .env
# ===========================
Write-Step "4/7" "Setting up environment variables..."

if (Test-Path $EnvFile) {
    Write-OK ".env already exists (not overwriting)"
    Write-Host "    Edit .env to add your API keys" -ForegroundColor Gray
} elseif (Test-Path $EnvExample) {
    Copy-Item $EnvExample $EnvFile
    Write-OK ".env created from .env.example"
    Write-Warn "Remember to add your API keys to .env!"
    Write-Host ""
    Write-Host "    API Keys needed:" -ForegroundColor Yellow
    Write-Host "    - Copernicus: https://dataspace.copernicus.eu" -ForegroundColor Gray
    Write-Host "    - NASA FIRMS: https://firms.modaps.eosdis.nasa.gov/api/area/" -ForegroundColor Gray
    Write-Host "    - GISTDA Sphere: https://sphere.gistda.or.th" -ForegroundColor Gray
    Write-Host "    - TMD: https://data.tmd.go.th" -ForegroundColor Gray
} else {
    Write-Warn ".env.example not found, skipping .env setup"
}

# ===========================
# Step 5: Create Data Directories
# ===========================
Write-Step "5/7" "Creating data directories..."

$dataDirs = @(
    "data\raw\sentinel2",
    "data\raw\viirs",
    "data\raw\weather",
    "data\raw\chirps",
    "data\raw\dem",
    "data\processed\indices",
    "data\processed\zonal_stats",
    "data\processed\poc",
    "data\validation"
)

foreach ($dir in $dataDirs) {
    $fullPath = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $fullPath)) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
    }
}

Write-OK "Data directories created ($($dataDirs.Count) folders)"

# Create .gitkeep files so empty dirs are tracked
foreach ($dir in $dataDirs) {
    $gitkeep = Join-Path $ProjectRoot "$dir\.gitkeep"
    if (-not (Test-Path $gitkeep)) {
        New-Item -ItemType File -Path $gitkeep -Force | Out-Null
    }
}

# ===========================
# Step 6: Quick Verification Test
# ===========================
Write-Step "6/7" "Running quick verification..."

$testScript = @"
import sys
print(f'Python {sys.version}')

# Test core imports
tests = []
try:
    import requests; tests.append(('requests', True))
except: tests.append(('requests', False))

try:
    import pandas; tests.append(('pandas', True))
except: tests.append(('pandas', False))

try:
    import numpy; tests.append(('numpy', True))
except: tests.append(('numpy', False))

try:
    from pystac_client import Client; tests.append(('pystac-client', True))
except: tests.append(('pystac-client', False))

try:
    from dotenv import load_dotenv; tests.append(('python-dotenv', True))
except: tests.append(('python-dotenv', False))

try:
    from loguru import logger; tests.append(('loguru', True))
except: tests.append(('loguru', False))

# Optional (may fail on fresh install, that's ok)
optional_tests = []
try:
    import rasterio; optional_tests.append(('rasterio', True))
except: optional_tests.append(('rasterio', False))

try:
    import geopandas; optional_tests.append(('geopandas', True))
except: optional_tests.append(('geopandas', False))

passed = sum(1 for _, ok in tests if ok)
total = len(tests)

print(f'\nCore packages: {passed}/{total} OK')
for name, ok in tests:
    status = 'OK' if ok else 'MISSING'
    print(f'  {name:20s} {status}')

if optional_tests:
    opt_passed = sum(1 for _, ok in optional_tests if ok)
    print(f'\nOptional packages: {opt_passed}/{len(optional_tests)} OK')
    for name, ok in optional_tests:
        status = 'OK' if ok else 'MISSING (install separately if needed)'
        print(f'  {name:20s} {status}')

sys.exit(0 if passed == total else 1)
"@

$env:PYTHONIOENCODING = "utf-8"
$testResult = & $PythonCmd -c $testScript 2>&1
$testResult | ForEach-Object { Write-Host "  $_" }

if ($LASTEXITCODE -eq 0) {
    Write-OK "All core packages verified!"
} else {
    Write-Warn "Some packages may be missing, but setup can continue"
}

# ===========================
# Step 7: Docker (Optional)
# ===========================
Write-Step "7/7" "Docker services..."

if ($WithDocker) {
    $dockerCompose = Join-Path $ProjectRoot "docker-compose.yml"
    if (Test-Path $dockerCompose) {
        Write-Host "  Starting PostGIS + MinIO..." -ForegroundColor Gray
        docker compose -f $dockerCompose up -d 2>&1 | ForEach-Object { Write-Host "  $_" }
        
        if ($LASTEXITCODE -eq 0) {
            Write-OK "Docker containers started!"
            Write-Host ""
            Write-Host "  Services:" -ForegroundColor Cyan
            Write-Host "    PostGIS:      localhost:5432 (user: satellite, db: geoai)" -ForegroundColor Gray
            Write-Host "    MinIO API:    http://localhost:9000" -ForegroundColor Gray
            Write-Host "    MinIO Console: http://localhost:9001 (admin/minioadmin)" -ForegroundColor Gray
        } else {
            Write-Warn "Docker compose failed. Is Docker Desktop running?"
        }
    }
} else {
    Write-Host "  Skipped (use -WithDocker flag to start containers)" -ForegroundColor Gray
    Write-Host "    Run manually: docker compose up -d" -ForegroundColor DarkGray
}

# ===========================
# Done!
# ===========================
Write-Host ""
Write-Host "  ================================================" -ForegroundColor Green
Write-Host "       Setup Complete!" -ForegroundColor Green
Write-Host "  ================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Quick start:" -ForegroundColor Cyan

if (-not $SkipVenv) {
    Write-Host "    1. Activate venv:    .\venv\Scripts\Activate.ps1" -ForegroundColor White
}
Write-Host "    2. Check API keys:   python pipeline\config.py" -ForegroundColor White
Write-Host "    3. Run PoC:          python run_poc.py" -ForegroundColor White
Write-Host "    4. Start Docker:     docker compose up -d" -ForegroundColor White
Write-Host ""
Write-Host "  API Keys (add to .env):" -ForegroundColor Yellow
Write-Host "    - Copernicus:   https://dataspace.copernicus.eu" -ForegroundColor DarkGray
Write-Host "    - NASA FIRMS:   https://firms.modaps.eosdis.nasa.gov/api/area/" -ForegroundColor DarkGray
Write-Host "    - GISTDA:       https://sphere.gistda.or.th" -ForegroundColor DarkGray
Write-Host "    - TMD:          https://data.tmd.go.th" -ForegroundColor DarkGray
Write-Host ""
