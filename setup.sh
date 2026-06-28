#!/usr/bin/env bash
# ============================================
# Satellite Team — Setup Script (Mac / Linux)
# ติดตั้งทุกอย่างที่ต้องใช้ในคำสั่งเดียว
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh              # Standard setup
#   ./setup.sh --docker     # Setup + start Docker
#   ./setup.sh --skip-venv  # Skip virtual environment
#   ./setup.sh --help       # Show help
# ============================================

set -e

# ===========================
# Config
# ===========================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PATH="$SCRIPT_DIR/venv"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"
WITH_DOCKER=false
SKIP_VENV=false

# ===========================
# Parse Arguments
# ===========================
for arg in "$@"; do
    case $arg in
        --docker)    WITH_DOCKER=true ;;
        --skip-venv) SKIP_VENV=true ;;
        --help|-h)
            echo ""
            echo "Satellite Team Setup Script"
            echo ""
            echo "Usage:"
            echo "  ./setup.sh              # Standard setup"
            echo "  ./setup.sh --docker     # Setup + start Docker containers"
            echo "  ./setup.sh --skip-venv  # Skip virtual environment creation"
            echo "  ./setup.sh --help       # Show this help"
            echo ""
            exit 0
            ;;
    esac
done

# ===========================
# Helpers
# ===========================
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m' # No Color

step() {
    echo ""
    echo -e "${GRAY}============================================================${NC}"
    echo -e "  ${CYAN}[$1] $2${NC}"
    echo -e "${GRAY}============================================================${NC}"
}

ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[!!]${NC} $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; }

command_exists() { command -v "$1" &> /dev/null; }

# ===========================
# Start
# ===========================
echo ""
echo -e "  ${CYAN}================================================${NC}"
echo -e "       ${CYAN}Satellite Team - Setup Script${NC}"
echo -e "       Project: TaSawan (Hackathon 2026)"
echo -e "  ${CYAN}================================================${NC}"
echo ""
echo "  Project root: $SCRIPT_DIR"
echo "  Date: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  OS: $(uname -s) $(uname -m)"

# ===========================
# Step 1: Check Prerequisites
# ===========================
step "1/7" "Checking prerequisites..."

ALL_GOOD=true

# Python 3
if command_exists python3; then
    PY_CMD="python3"
    PY_VERSION=$($PY_CMD --version 2>&1)
    ok "Python: $PY_VERSION"
elif command_exists python; then
    PY_CMD="python"
    PY_VERSION=$($PY_CMD --version 2>&1)
    ok "Python: $PY_VERSION"
else
    fail "Python 3 not found!"
    echo "    Install: https://www.python.org/downloads/"
    echo "    Mac:     brew install python3"
    echo "    Ubuntu:  sudo apt install python3 python3-venv python3-pip"
    ALL_GOOD=false
fi

# pip
if $PY_CMD -m pip --version &> /dev/null; then
    ok "pip: available"
else
    warn "pip not found"
    echo "    Install: $PY_CMD -m ensurepip --upgrade"
fi

# Git
if command_exists git; then
    ok "Git: $(git --version)"
else
    warn "Git not found (optional)"
fi

# Docker
if command_exists docker; then
    ok "Docker: $(docker --version)"
else
    warn "Docker not found (needed for PostGIS + MinIO)"
    if $WITH_DOCKER; then
        fail "Cannot start Docker without Docker installed!"
        WITH_DOCKER=false
    fi
fi

if [ "$ALL_GOOD" = false ]; then
    echo ""
    fail "Missing required prerequisites. Install them and try again."
    exit 1
fi

# ===========================
# Step 2: Virtual Environment
# ===========================
step "2/7" "Setting up Python virtual environment..."

if $SKIP_VENV; then
    warn "Skipping venv (--skip-venv flag)"
    PIP_CMD="$PY_CMD -m pip"
    PYTHON_CMD="$PY_CMD"
else
    if [ -d "$VENV_PATH" ]; then
        ok "venv already exists"
    else
        echo "  Creating venv..."
        $PY_CMD -m venv "$VENV_PATH"
        ok "venv created"
    fi

    PYTHON_CMD="$VENV_PATH/bin/python"
    PIP_CMD="$VENV_PATH/bin/pip"

    # Upgrade pip
    echo "  Upgrading pip..."
    $PYTHON_CMD -m pip install --upgrade pip --quiet 2>&1 || true
    ok "pip upgraded"
fi

# ===========================
# Step 3: Install Dependencies
# ===========================
step "3/7" "Installing Python dependencies..."

REQ_FILE="$SCRIPT_DIR/requirements.txt"

if [ -f "$REQ_FILE" ]; then
    echo "  Installing from requirements.txt..."
    echo "  (This may take a few minutes on first run)"
    $PIP_CMD install -r "$REQ_FILE" 2>&1 | tail -5
    ok "Dependencies installed"
else
    fail "requirements.txt not found!"
    exit 1
fi

# ===========================
# Step 4: Setup .env
# ===========================
step "4/7" "Setting up environment variables..."

if [ -f "$ENV_FILE" ]; then
    ok ".env already exists (not overwriting)"
elif [ -f "$ENV_EXAMPLE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    ok ".env created from .env.example"
    warn "Remember to add your API keys to .env!"
    echo ""
    echo "    API Keys needed:"
    echo "    - Copernicus: https://dataspace.copernicus.eu"
    echo "    - NASA FIRMS: https://firms.modaps.eosdis.nasa.gov/api/area/"
    echo "    - GISTDA:     https://sphere.gistda.or.th"
    echo "    - TMD:        https://data.tmd.go.th"
else
    warn ".env.example not found, skipping"
fi

# ===========================
# Step 5: Create Data Directories
# ===========================
step "5/7" "Creating data directories..."

DATA_DIRS=(
    "data/raw/sentinel2"
    "data/raw/viirs"
    "data/raw/weather"
    "data/raw/chirps"
    "data/raw/dem"
    "data/processed/indices"
    "data/processed/zonal_stats"
    "data/processed/poc"
    "data/validation"
)

for dir in "${DATA_DIRS[@]}"; do
    mkdir -p "$SCRIPT_DIR/$dir"
    touch "$SCRIPT_DIR/$dir/.gitkeep"
done

ok "Data directories created (${#DATA_DIRS[@]} folders)"

# ===========================
# Step 6: Quick Verification
# ===========================
step "6/7" "Running quick verification..."

$PYTHON_CMD -c "
import sys
print(f'Python {sys.version}')

tests = []
for pkg, name in [
    ('requests', 'requests'),
    ('pandas', 'pandas'),
    ('numpy', 'numpy'),
    ('pystac_client', 'pystac-client'),
    ('dotenv', 'python-dotenv'),
    ('loguru', 'loguru'),
]:
    try:
        __import__(pkg)
        tests.append((name, True))
    except ImportError:
        tests.append((name, False))

passed = sum(1 for _, ok in tests if ok)
print(f'\nCore packages: {passed}/{len(tests)} OK')
for name, ok in tests:
    print(f'  {name:20s} {\"OK\" if ok else \"MISSING\"}')

sys.exit(0 if passed == len(tests) else 1)
" 2>&1 | while read -r line; do echo "  $line"; done

if [ $? -eq 0 ]; then
    ok "All core packages verified!"
else
    warn "Some packages may be missing"
fi

# ===========================
# Step 7: Docker (Optional)
# ===========================
step "7/7" "Docker services..."

if $WITH_DOCKER; then
    COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
    if [ -f "$COMPOSE_FILE" ]; then
        echo "  Starting PostGIS + MinIO..."
        docker compose -f "$COMPOSE_FILE" up -d 2>&1 | while read -r line; do echo "  $line"; done

        if [ $? -eq 0 ]; then
            ok "Docker containers started!"
            echo ""
            echo "  Services:"
            echo "    PostGIS:       localhost:5432 (user: satellite, db: geoai)"
            echo "    MinIO API:     http://localhost:9000"
            echo "    MinIO Console: http://localhost:9001 (admin/minioadmin)"
        else
            warn "Docker compose failed. Is Docker running?"
        fi
    fi
else
    echo "  Skipped (use --docker flag to start containers)"
fi

# ===========================
# Done!
# ===========================
echo ""
echo -e "  ${GREEN}================================================${NC}"
echo -e "       ${GREEN}Setup Complete!${NC}"
echo -e "  ${GREEN}================================================${NC}"
echo ""
echo "  Quick start:"

if ! $SKIP_VENV; then
    echo "    1. Activate venv:   source venv/bin/activate"
fi
echo "    2. Check API keys:  python pipeline/config.py"
echo "    3. Run PoC:         python run_poc.py"
echo "    4. Start Docker:    docker compose up -d"
echo ""
echo "  API Keys (add to .env):"
echo "    - Copernicus:  https://dataspace.copernicus.eu"
echo "    - NASA FIRMS:  https://firms.modaps.eosdis.nasa.gov/api/area/"
echo "    - GISTDA:      https://sphere.gistda.or.th"
echo "    - TMD:         https://data.tmd.go.th"
echo ""
