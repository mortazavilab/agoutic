#!/bin/bash
# =============================================================================
# AGOUTIC - Server Manager
# =============================================================================
# Launches all server processes (Cortex, 3, 4, and consortium MCP servers)
# as background processes with PID tracking and log files.
#
# Features:
#   - Reliable port-based process killing (fallback from PIDs)
#   - Automatic log rotation with timestamps on start/restart
#   - Structured JSON-lines logging via Python structlog
#
# Usage:
#   ./agoutic_servers.sh              # Start all servers
#   ./agoutic_servers.sh --stop       # Stop all servers
#   ./agoutic_servers.sh --status     # Check server status
#   ./agoutic_servers.sh --restart    # Restart all servers
# =============================================================================

# Don't use set -e — helper functions return nonzero legitimately

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGOUTIC_VERSION="$(cat "$SCRIPT_DIR/VERSION" 2>/dev/null | tr -d '\n' || echo '0.0.0')"
export AGOUTIC_CODE="${AGOUTIC_CODE:-$SCRIPT_DIR}"
export AGOUTIC_DATA="${AGOUTIC_DATA:-$AGOUTIC_CODE/data}"

PIDS_DIR="$AGOUTIC_CODE/pids"
LOGS_DIR="$AGOUTIC_DATA/logs"

# Port assignments
CORTEX_PORT="${CORTEX_PORT:-8000}"
LAUNCHPAD_PORT="${LAUNCHPAD_PORT:-8003}"
LAUNCHPAD_MCP_PORT="${LAUNCHPAD_MCP_PORT:-8002}"
ANALYZER_PORT="${ANALYZER_PORT:-8004}"
ANALYZER_MCP_PORT="${ANALYZER_MCP_PORT:-8005}"
ENCODE_MCP_PORT="${ENCODE_MCP_PORT:-8006}"
EDGEPYTHON_MCP_PORT="${EDGEPYTHON_MCP_PORT:-8007}"
UI_PORT="${UI_PORT:-8501}"

# Map service names to ports
declare -A PORT_MAP=(
    ["launchpad-rest"]=$LAUNCHPAD_PORT
    ["launchpad-mcp"]=$LAUNCHPAD_MCP_PORT
    ["analyzer-rest"]=$ANALYZER_PORT
    ["analyzer-mcp"]=$ANALYZER_MCP_PORT
    ["encode-mcp"]=$ENCODE_MCP_PORT
    ["edgepython-mcp"]=$EDGEPYTHON_MCP_PORT
    ["cortex"]=$CORTEX_PORT
)

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

# --- Log Rotation ---

rotate_logs() {
    # Rotate existing log files by renaming them with a timestamp.
    # Called at the start of cmd_start so each run gets fresh logs.
    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)

    if [ ! -d "$LOGS_DIR" ]; then
        return
    fi

    local rotated=0
    for logfile in "$LOGS_DIR"/*.jsonl "$LOGS_DIR"/*.log; do
        # Skip glob patterns that didn't match
        [ -e "$logfile" ] || continue

        # Skip empty files
        [ -s "$logfile" ] || continue

        local base
        base=$(basename "$logfile")
        local ext="${base##*.}"
        local name="${base%.*}"

        # Only rotate the current (non-timestamped) log files.
        # Already-rotated files contain a dot-separated timestamp like
        # "cortex.20260213_082438.jsonl" — skip them.
        if [[ "$name" == *.* ]]; then
            continue
        fi

        mv "$logfile" "$LOGS_DIR/${name}.${timestamp}.${ext}"
        rotated=$((rotated + 1))
    done

    if [ "$rotated" -gt 0 ]; then
        log "Rotated $rotated log file(s) with timestamp $timestamp"
    fi
}

# --- Process management ---

is_pid_our_process() {
    # Verify that a PID belongs to a Python/uvicorn process (not a recycled PID).
    local pid="$1"
    if ! kill -0 "$pid" 2>/dev/null; then
        return 1
    fi
    local cmd
    cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
    if [[ "$cmd" == *python* ]] || [[ "$cmd" == *uvicorn* ]]; then
        return 0
    fi
    return 1
}

is_running() {
    local name="$1"
    local pid_file="$PIDS_DIR/$name.pid"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if is_pid_our_process "$pid"; then
            return 0  # Running
        fi
    fi
    # Fallback: check if anything is listening on the expected port
    local port="${PORT_MAP[$name]}"
    if [ -n "$port" ]; then
        local port_pid
        port_pid=$(lsof -ti :"$port" 2>/dev/null | head -1)
        if [ -n "$port_pid" ] && is_pid_our_process "$port_pid"; then
            return 0
        fi
    fi
    return 1  # Not running
}

get_running_pid() {
    # Return the PID of the running process for a service name.
    # Tries PID file first, then port-based lookup.
    local name="$1"
    local pid_file="$PIDS_DIR/$name.pid"

    # Try PID file first
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if is_pid_our_process "$pid"; then
            echo "$pid"
            return 0
        fi
    fi

    # Fallback: port-based lookup
    local port="${PORT_MAP[$name]}"
    if [ -n "$port" ]; then
        local port_pid
        port_pid=$(lsof -ti :"$port" 2>/dev/null | head -1)
        if [ -n "$port_pid" ]; then
            echo "$port_pid"
            return 0
        fi
    fi

    return 1
}

kill_by_port() {
    # Kill any process listening on a given port.
    local port="$1"
    local pids
    pids=$(lsof -ti :"$port" 2>/dev/null || true)

    if [ -z "$pids" ]; then
        return 1
    fi

    for pid in $pids; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done

    # Wait for graceful shutdown
    local waited=0
    while [ $waited -lt 5 ]; do
        local still_alive=false
        for pid in $pids; do
            if kill -0 "$pid" 2>/dev/null; then
                still_alive=true
                break
            fi
        done
        if ! $still_alive; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    # Force kill remaining
    for pid in $pids; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done

    return 0
}

start_process() {
    local name="$1"
    local command="$2"
    local log_file="$LOGS_DIR/${name}.log"
    local pid_file="$PIDS_DIR/${name}.pid"

    if is_running "$name"; then
        local running_pid
        running_pid=$(get_running_pid "$name")
        warn "$name is already running (PID: $running_pid)"
        return
    fi

    log "Starting $name..."
    # Run in background; shell-level stdout/stderr capture serves as
    # safety net for pre-logging crashes. Application-level structured
    # logs go to logs/*.jsonl via common.logging_config.
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
    local port="${PORT_MAP[$name]}"
    local killed=false

    # Strategy 1: PID file (validated)
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if is_pid_our_process "$pid"; then
            log "Stopping $name (PID: $pid)..."
            kill "$pid" 2>/dev/null || true

            # Wait up to 5 seconds for graceful shutdown
            for i in {1..5}; do
                if ! kill -0 "$pid" 2>/dev/null; then
                    break
                fi
                sleep 1
            done

            # Force kill if still running
            if kill -0 "$pid" 2>/dev/null; then
                warn "Force killing $name (PID: $pid)..."
                kill -9 "$pid" 2>/dev/null || true
                sleep 1
            fi

            killed=true
        fi
        rm -f "$pid_file"
    fi

    # Strategy 2: Port-based fallback — kill whatever is on the port
    if [ -n "$port" ]; then
        local port_pids
        port_pids=$(lsof -ti :"$port" 2>/dev/null || true)
        if [ -n "$port_pids" ]; then
            if ! $killed; then
                log "Stopping $name via port $port..."
            else
                log "Cleaning up orphan(s) on port $port..."
            fi
            kill_by_port "$port"
            killed=true
        fi
    fi

    if $killed; then
        success "$name stopped"
    else
        warn "$name: not running"
    fi
}

# --- Commands ---

cmd_start() {
    ensure_dirs
    rotate_logs
    log "Starting AGOUTIC servers (${AGOUTIC_VERSION})..."
    echo ""

    # Launchpad - REST API (Nextflow/Dogme job execution)
    start_process "launchpad-rest" \
        "python -m uvicorn launchpad.app:app --host 0.0.0.0 --port $LAUNCHPAD_PORT"

    # Launchpad - MCP Server (HTTP mode)
    start_process "launchpad-mcp" \
        "python -m launchpad.mcp_server --host 0.0.0.0 --port $LAUNCHPAD_MCP_PORT"

    # Analyzer - REST API (Analysis engine)
    start_process "analyzer-rest" \
        "python -m analyzer.app"

    # Analyzer - MCP Server (HTTP mode)
    start_process "analyzer-mcp" \
        "python -m analyzer.mcp_server --host 0.0.0.0 --port $ANALYZER_MCP_PORT"

    # ENCODE MCP Server (consortium)
    start_process "encode-mcp" \
        "python -m atlas.launch_encode --host 0.0.0.0 --port $ENCODE_MCP_PORT"

    # edgePython MCP Server (differential expression)
    start_process "edgepython-mcp" \
        "python -m edgepython_mcp.launch_edgepython --host 0.0.0.0 --port $EDGEPYTHON_MCP_PORT"

    # Cortex - Main orchestrator (start last)
    start_process "cortex" \
        "python -m uvicorn cortex.app:app --host 0.0.0.0 --port $CORTEX_PORT"

    echo ""
    log "All servers started. Port summary:"
    echo "  Cortex (Orchestrator):   http://localhost:$CORTEX_PORT"
    echo "  Launchpad (Jobs REST):      http://localhost:$LAUNCHPAD_PORT"
    echo "  Launchpad (Jobs MCP):       http://localhost:$LAUNCHPAD_MCP_PORT"
    echo "  Analyzer (Analysis REST):  http://localhost:$ANALYZER_PORT"
    echo "  Analyzer (Analysis MCP):   http://localhost:$ANALYZER_MCP_PORT"
    echo "  ENCODE (Consortium MCP):   http://localhost:$ENCODE_MCP_PORT"
    echo "  edgePython (DE MCP):       http://localhost:$EDGEPYTHON_MCP_PORT"
    echo ""
    log "Structured logs:  $LOGS_DIR/*.jsonl"
    log "Unified log:      $LOGS_DIR/agoutic.jsonl"
    log "Start the UI separately:  streamlit run ui/appUI.py --server.port $UI_PORT"
}

cmd_stop() {
    log "Stopping AGOUTIC servers..."
    echo ""

    stop_process "cortex"
    stop_process "edgepython-mcp"
    stop_process "encode-mcp"
    stop_process "analyzer-mcp"
    stop_process "analyzer-rest"
    stop_process "launchpad-mcp"
    stop_process "launchpad-rest"

    echo ""
    log "All servers stopped."
}

cmd_status() {
    log "AGOUTIC server status:"
    echo ""

    local services=("launchpad-rest" "launchpad-mcp" "analyzer-rest" "analyzer-mcp" "encode-mcp" "edgepython-mcp" "cortex")
    local labels=("Launchpad REST" "Launchpad MCP" "Analyzer REST" "Analyzer MCP" "ENCODE MCP" "edgePython MCP" "Cortex")

    for i in "${!services[@]}"; do
        local name="${services[$i]}"
        local label="${labels[$i]}"
        local port="${PORT_MAP[$name]}"
        if is_running "$name"; then
            local pid
            pid=$(get_running_pid "$name")
            success "$label: running (PID: $pid, port: $port)"
        else
            error "$label: not running (port: $port)"
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