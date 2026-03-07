#!/bin/bash
# Startup script for Analyzer (Analysis Server)

echo "🚀 Starting Analyzer - Analysis Server"
echo "======================================"

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1)
echo "Python: $PYTHON_VERSION"

# Check if running from correct directory
if [ ! -d "analyzer" ]; then
    echo "❌ Error: Must run from project root (agoutic/)"
    exit 1
fi

# Set default environment variables if not set
export ANALYZER_HOST=${ANALYZER_HOST:-0.0.0.0}
export ANALYZER_PORT=${ANALYZER_PORT:-8004}
export ANALYZER_MCP_PORT=${ANALYZER_MCP_PORT:-8005}
export DATABASE_URL=${DATABASE_URL:-sqlite:///./data/database/agoutic.db}
export AGOUTIC_WORK_DIR=${AGOUTIC_WORK_DIR:-./data/launchpad_work}

echo ""
echo "Configuration:"
echo "  HOST: $ANALYZER_HOST"
echo "  REST PORT: $ANALYZER_PORT"
echo "  MCP PORT: $ANALYZER_MCP_PORT"
echo "  DATABASE: $DATABASE_URL"
echo "  WORK_DIR: $AGOUTIC_WORK_DIR"
echo ""

# Check if work directory exists
if [ ! -d "$AGOUTIC_WORK_DIR" ]; then
    echo "⚠️  Warning: Work directory not found: $AGOUTIC_WORK_DIR"
    echo "   Creating directory..."
    mkdir -p "$AGOUTIC_WORK_DIR"
fi

# Parse command line arguments
MODE=${1:-rest}

case $MODE in
    rest)
        echo "Starting REST API server on port $ANALYZER_PORT..."
        echo "Docs available at: http://$ANALYZER_HOST:$ANALYZER_PORT/docs"
        echo ""
        python3 -m analyzer.app
        ;;
    
    mcp)
        echo "Starting MCP server on stdio..."
        echo "(This mode is for agent integration, not standalone use)"
        echo ""
        python3 -m analyzer.mcp_server
        ;;
    
    test)
        echo "Running test suite..."
        echo ""
        python3 -m pytest tests/analyzer -q
        ;;
    
    dev)
        echo "Starting REST API in development mode with auto-reload..."
        echo "Docs available at: http://$ANALYZER_HOST:$ANALYZER_PORT/docs"
        echo ""
        uvicorn analyzer.app:app --host $ANALYZER_HOST --port $ANALYZER_PORT --reload
        ;;
    
    *)
        echo "Usage: $0 [rest|mcp|test|dev]"
        echo ""
        echo "Modes:"
        echo "  rest  - Start REST API server (default)"
        echo "  mcp   - Start MCP server for agent integration"
        echo "  test  - Run test suite"
        echo "  dev   - Start REST API with auto-reload"
        exit 1
        ;;
esac
