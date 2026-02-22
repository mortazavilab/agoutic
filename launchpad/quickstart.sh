#!/usr/bin/env bash
# Quick-start script for Launchpad
# Sets up environment and demonstrates basic functionality

set -e

echo "╔════════════════════════════════════════════════════════════╗"
echo "║        AGOUTIC Launchpad - Quick Start Guide              ║"
echo "║   Dogme/Nextflow Job Execution Engine (Week 3)           ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo

# Color codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Environment Setup
echo -e "${BLUE}[1/5]${NC} Setting up environment variables..."

# Default AGOUTIC_ROOT to agoutic_code/data if not set
export AGOUTIC_ROOT="${AGOUTIC_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data}"
export DOGME_REPO="${DOGME_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/dogme}"
export NEXTFLOW_BIN="${NEXTFLOW_BIN:-/usr/local/bin/nextflow}"
export MAX_CONCURRENT_JOBS="${MAX_CONCURRENT_JOBS:-2}"
export JOB_POLL_INTERVAL="${JOB_POLL_INTERVAL:-10}"
export LLM_URL="${LLM_URL:-http://localhost:11434/v1}"

echo "✅ Environment variables set:"
echo "   AGOUTIC_ROOT: $AGOUTIC_ROOT"
echo "   DOGME_REPO: $DOGME_REPO"
echo "   NEXTFLOW_BIN: $NEXTFLOW_BIN"
echo

# Step 2: Create directories
echo -e "${BLUE}[2/5]${NC} Creating work directories..."

mkdir -p "$AGOUTIC_ROOT/launchpad_work"
mkdir -p "$AGOUTIC_ROOT/launchpad_logs"
mkdir -p "$AGOUTIC_ROOT/database"

echo "✅ Directories created:"
echo "   Work: $AGOUTIC_ROOT/launchpad_work"
echo "   Logs: $AGOUTIC_ROOT/launchpad_logs"
echo "   Data: $AGOUTIC_ROOT/database"
echo

# Step 3: Check dependencies
echo -e "${BLUE}[3/5]${NC} Checking dependencies..."

# Check Python
python_version=$(python --version 2>&1 | awk '{print $2}')
echo "✅ Python: $python_version"

# Check FastAPI
python -c "import fastapi; print(f'✅ FastAPI: {fastapi.__version__}')" 2>/dev/null || echo "❌ FastAPI not installed"

# Check SQLAlchemy
python -c "import sqlalchemy; print(f'✅ SQLAlchemy: {sqlalchemy.__version__}')" 2>/dev/null || echo "❌ SQLAlchemy not installed"

# Check Nextflow
if command -v nextflow &> /dev/null; then
    echo "✅ Nextflow: $(nextflow -v 2>&1 | head -1)"
else
    echo "⚠️  Nextflow: Not found in PATH"
fi

echo

# Step 4: Show commands
echo -e "${BLUE}[4/5]${NC} Available commands:"
echo

echo -e "${GREEN}Start Launchpad:${NC}"
echo "  uvicorn launchpad.app:app --host 0.0.0.0 --port 8001 --reload"
echo

echo -e "${GREEN}Check Server Health:${NC}"
echo "  curl http://localhost:8001/health"
echo

echo -e "${GREEN}Run Tests:${NC}"
echo "  pytest launchpad/test_launchpad.py -v"
echo

echo -e "${GREEN}Run Demo:${NC}"
echo "  python launchpad/demo_launchpad.py"
echo "  python launchpad/demo_launchpad.py dna      # Submit DNA job"
echo "  python launchpad/demo_launchpad.py rna      # Submit RNA job"
echo "  python launchpad/demo_launchpad.py cdna     # Submit cDNA job"
echo "  python launchpad/demo_launchpad.py list     # List all jobs"
echo

# Step 5: Next steps
echo -e "${BLUE}[5/5]${NC} Next steps:"
echo

echo -e "${YELLOW}Option A: Interactive Demo${NC}"
echo "  1. Start Launchpad: uvicorn launchpad.app:app --port 8001"
echo "  2. In another terminal: python launchpad/demo_launchpad.py"
echo

echo -e "${YELLOW}Option B: Manual Testing${NC}"
echo "  1. Start Launchpad: uvicorn launchpad.app:app --port 8001"
echo "  2. Check health: curl http://localhost:8001/health"
echo "  3. Submit job: curl -X POST http://localhost:8001/jobs/submit ..."
echo

echo -e "${YELLOW}Option C: Run Tests${NC}"
echo "  pytest launchpad/test_launchpad.py -v --tb=short"
echo

echo -e "${GREEN}✅ Quick start setup complete!${NC}"
echo

echo "📚 For more details, see: launchpad/README.md"
echo
