#!/bin/bash
# Startup script for Server4 (Analysis Server)

echo "🚀 Starting Server4 - Analysis Server"
echo "======================================"

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1)
echo "Python: $PYTHON_VERSION"

# Check if running from correct directory
if [ ! -d "server4" ]; then
    echo "❌ Error: Must run from project root (agoutic/)"
    exit 1
fi

# Set default environment variables if not set
export SERVER4_HOST=${SERVER4_HOST:-0.0.0.0}
export SERVER4_PORT=${SERVER4_PORT:-8004}
export SERVER4_MCP_PORT=${SERVER4_MCP_PORT:-8005}
export DATABASE_URL=${DATABASE_URL:-sqlite:///./data/database/agoutic.db}
export AGOUTIC_WORK_DIR=${AGOUTIC_WORK_DIR:-./data/server3_work}

echo ""
echo "Configuration:"
echo "  HOST: $SERVER4_HOST"
echo "  REST PORT: $SERVER4_PORT"
echo "  MCP PORT: $SERVER4_MCP_PORT"
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
        echo "Starting REST API server on port $SERVER4_PORT..."
        echo "Docs available at: http://$SERVER4_HOST:$SERVER4_PORT/docs"
        echo ""
        python3 -m server4.app
        ;;
    
    mcp)
        echo "Starting MCP server on stdio..."
        echo "(This mode is for agent integration, not standalone use)"
        echo ""
        python3 -m server4.mcp_server
        ;;
    
    test)
        echo "Running test suite..."
        echo ""
        python3 server4/test_analysis.py
        ;;
    
    dev)
        echo "Starting REST API in development mode with auto-reload..."
        echo "Docs available at: http://$SERVER4_HOST:$SERVER4_PORT/docs"
        echo ""
        uvicorn server4.app:app --host $SERVER4_HOST --port $SERVER4_PORT --reload
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
