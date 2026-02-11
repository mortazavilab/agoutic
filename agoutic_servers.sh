#!/bin/bash
# =============================================================================
# AGOUTIC - Start All Servers
# =============================================================================
# Launches all server processes (Server 1, 3, 4, and consortium MCP servers)
# as background processes with PID tracking and log files.
#
# Usage:
#   ./agoutic_servers.sh              # Start all servers
#   ./agoutic_servers.sh --stop       # Stop all servers
#   ./agoutic_servers.sh --status     # Check server status
#   ./agoutic_servers.sh --restart    # Restart all servers
# =============================================================================

set -e

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AGOUTIC_CODE="${AGOUTIC_CODE:-$SCRIPT_DIR}"
export AGOUTIC_DATA="${AGOUTIC_DATA:-$AGOUTIC_CODE/data}"

PIDS_DIR="$AGOUTIC_CODE/pids"
LOGS_DIR="$AGOUTIC_CODE/logs"

# Port assignments
SERVER1_PORT="${SERVER1_PORT:-8000}"
SERVER3_PORT="${SERVER3_PORT:-8003}"
SERVER3_MCP_PORT="${SERVER3_MCP_PORT:-8002}"
SERVER4_PORT="${SERVER4_PORT:-8004}"
SERVER4_MCP_PORT="${SERVER4_MCP_PORT:-8005}"
ENCODE_MCP_PORT="${ENCODE_MCP_PORT:-8006}"
UI_PORT="${UI_PORT:-8501}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No color

# --- Helper functions ---

ensure_dirs() {
    mkdir -p "$PIDS_DIR" "$LOGS_DIR"
}

log() {
    echo -e "${BLUE}[AGOUTIC]${NC} $1"
}

success() {
    echo -e "${GREEN}  ✅ $1${NC}"
}

warn() {
    echo -e "${YELLOW}  ⚠️  $1${NC}"
}

error() {
    echo -e "${RED}  ❌ $1${NC}"
}

is_running() {
    local pid_file="$PIDS_DIR/$1.pid"
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0  # Running
        fi
    fi
    return 1  # Not running
}

start_process() {
    local name="$1"
    local command="$2"
    local log_file="$LOGS_DIR/${name}.log"
    local pid_file="$PIDS_DIR/${name}.pid"

    if is_running "$name"; then
        warn "$name is already running (PID: $(cat "$pid_file"))"
        return
    fi

    log "Starting $name..."
    # Run in background, redirect stdout/stderr to log file
    cd "$AGOUTIC_CODE"
    eval "$command" >> "$log_file" 2>&1 &
    local pid=$!
    echo "$pid" > "$pid_file"
    
    # Brief pause to check if process started successfully
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        success "$name started (PID: $pid, log: $log_file)"
    else
        error "$name failed to start. Check: $log_file"
        rm -f "$pid_file"
    fi
}

stop_process() {
    local name="$1"
    local pid_file="$PIDS_DIR/${name}.pid"

    if [ ! -f "$pid_file" ]; then
        warn "$name: no PID file found"
        return
    fi

    local pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
        log "Stopping $name (PID: $pid)..."
        kill "$pid" 2>/dev/null
        
        # Wait up to 5 seconds for graceful shutdown
        for i in {1..5}; do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 1
        done

        # Force kill if still running
        if kill -0 "$pid" 2>/dev/null; then
            warn "Force killing $name..."
            kill -9 "$pid" 2>/dev/null
        fi

        success "$name stopped"
    else
        warn "$name was not running"
    fi
    rm -f "$pid_file"
}

# --- Commands ---

cmd_start() {
    ensure_dirs
    log "Starting AGOUTIC servers..."
    echo ""

    # Server 3 - REST API (Nextflow/Dogme job execution)
    start_process "server3-rest" \
        "python -m uvicorn server3.app:app --host 0.0.0.0 --port $SERVER3_PORT"

    # Server 3 - MCP Server (HTTP mode)
    start_process "server3-mcp" \
        "python -m server3.mcp_server --host 0.0.0.0 --port $SERVER3_MCP_PORT"

    # Server 4 - REST API (Analysis engine)
    start_process "server4-rest" \
        "python -m server4.app"

    # Server 4 - MCP Server (HTTP mode)
    start_process "server4-mcp" \
        "python -m server4.mcp_server --host 0.0.0.0 --port $SERVER4_MCP_PORT"

    # ENCODE MCP Server (consortium)
    start_process "encode-mcp" \
        "python server2/launch_encode.py --host 0.0.0.0 --port $ENCODE_MCP_PORT"

    # Server 1 - Main orchestrator (start last)
    start_process "server1" \
        "python -m uvicorn server1.app:app --host 0.0.0.0 --port $SERVER1_PORT"

    echo ""
    log "All servers started. Port summary:"
    echo "  Server 1 (Orchestrator):   http://localhost:$SERVER1_PORT"
    echo "  Server 3 (Jobs REST):      http://localhost:$SERVER3_PORT"
    echo "  Server 3 (Jobs MCP):       http://localhost:$SERVER3_MCP_PORT"
    echo "  Server 4 (Analysis REST):  http://localhost:$SERVER4_PORT"
    echo "  Server 4 (Analysis MCP):   http://localhost:$SERVER4_MCP_PORT"
    echo "  ENCODE (Consortium MCP):   http://localhost:$ENCODE_MCP_PORT"
    echo ""
    log "Start the UI separately:  streamlit run ui/app.py --server.port $UI_PORT"
}

cmd_stop() {
    log "Stopping AGOUTIC servers..."
    echo ""

    stop_process "server1"
    stop_process "encode-mcp"
    stop_process "server4-mcp"
    stop_process "server4-rest"
    stop_process "server3-mcp"
    stop_process "server3-rest"

    echo ""
    log "All servers stopped."
}

cmd_status() {
    log "AGOUTIC server status:"
    echo ""

    local services=("server3-rest" "server3-mcp" "server4-rest" "server4-mcp" "encode-mcp" "server1")
    local labels=("Server 3 REST" "Server 3 MCP" "Server 4 REST" "Server 4 MCP" "ENCODE MCP" "Server 1")

    for i in "${!services[@]}"; do
        local name="${services[$i]}"
        local label="${labels[$i]}"
        if is_running "$name"; then
            local pid=$(cat "$PIDS_DIR/${name}.pid")
            success "$label: running (PID: $pid)"
        else
            error "$label: not running"
        fi
    done
    echo ""
}

cmd_restart() {
    cmd_stop
    echo ""
    sleep 2
    cmd_start
}

# --- Main ---

case "${1:-start}" in
    --start|start)
        cmd_start
        ;;
    --stop|stop)
        cmd_stop
        ;;
    --status|status)
        cmd_status
        ;;
    --restart|restart)
        cmd_restart
        ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
