#!/usr/bin/env python3
"""Diagnostic script to identify where /scratch paths are being generated."""
import os
import sys
from pathlib import Path

print("=" * 80)
print("AGOUTIC ENVIRONMENT & CONFIG DIAGNOSIS")
print("=" * 80)

# Check environment variables
print("\n▶ ENVIRONMENT VARIABLES (Agoutic-related):")
relevant_vars = [
    "AGOUTIC_CODE",
    "AGOUTIC_DATA",
    "AGOUTIC_ROOT",
    "AGOUTIC_WORK_DIR",
    "LAUNCHPAD_WORK_DIR",
    "NEXTFLOW_WORK_DIR",
    "NXF_WORK",
    "NXF_HOME",
]

for var in relevant_vars:
    val = os.getenv(var)
    print(f"  {var:25} = {val or '(not set)'}")

# Check Launchpad config
print("\n▶ LAUNCHPAD CONFIG PATHS:")
sys.path.insert(0, '/Users/eli/code/agoutic')
try:
    from launchpad.config import AGOUTIC_CODE, AGOUTIC_DATA, LAUNCHPAD_WORK_DIR, LAUNCHPAD_LOGS_DIR
    print(f"  AGO UTIC_CODE:    {AGOUTIC_CODE}")
    print(f"  AGOUTIC_DATA:     {AGOUTIC_DATA}")
    print(f"  LAUNCHPAD_WORK_DIR:  {LAUNCHPAD_WORK_DIR}")
    print(f"  LAUNCHPAD_LOGS_DIR:  {LAUNCHPAD_LOGS_DIR}")
    
    # Check if they exist
    print("\n▶ DIRECTORY EXISTENCE:")
    for name, path in [
        ("AGOUTIC_DATA", AGOUTIC_DATA),
        ("LAUNCHPAD_WORK_DIR", LAUNCHPAD_WORK_DIR),
    ]:
        exists = Path(path).exists()
        print(f"  {name:25} exists: {exists}")
        
except Exception as e:
    print(f"  ERROR loading config: {e}")

# Check if /scratch exists
print("\n▶ ROOT-LEVEL /SCRATCH CHECK:")
scratch_exists = Path("/scratch").exists()
print(f"  /scratch exists: {scratch_exists}")
if scratch_exists:
    try:
        stat_info = Path("/scratch").stat()
        print(f"  /scratch permissions: {oct(stat_info.st_mode)[-3:]}")
    except Exception as e:
        print(f"  Error checking /scratch permissions: {e}")
else:
    print("  /scratch does NOT exist (expected on macOS)")

# Try to create /scratch and show error
print("\n▶ TRY TO CREATE /scratch (will fail on macOS):")
try:
    Path("/scratch").mkdir(parents=True, exist_ok=True)
    print("  ✓ /scratch created successfully")
except Exception as e:
    print(f"  ✗ Error: {e}")
    print(f"    Error type: {type(e).__name__}")

print("\n" + "=" * 80)
