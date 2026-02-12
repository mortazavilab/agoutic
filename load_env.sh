#!/bin/bash
# Load environment variables from .env file
# Source this file before running any AGOUTIC commands locally:
#   source load_env.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    echo "Loading environment variables from .env..."
    # Export variables from .env, skipping comments and empty lines
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip comments and empty lines
        if [[ "$line" =~ ^[[:space:]]*# ]] || [[ -z "$line" ]]; then
            continue
        fi
        # Export the variable
        export "$line"
        echo "  ✓ $line"
    done < "$ENV_FILE"
    echo "✅ Environment loaded"
else
    echo "⚠️  No .env file found at $ENV_FILE"
fi