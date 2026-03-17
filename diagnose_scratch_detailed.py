#!/usr/bin/env python3  
"""
Comprehensive diagnostic to identify /scratch path issue in execution.
This helps identify config or environment variable problems that might cause 
incorrect backend selection or path defaults.
"""
import os
import sys
import json
from pathlib import Path

print("=" * 90)
print("AGOUTIC EXECUTION & BACKEND DIAGNOSTIC")
print("=" * 90)

# === SECTION 1: Environment Variables ===
print("\n[1] ENVIRONMENT VARIABLES - Full Agoutic Stack:")
for var in sorted(os.environ):
    if any(keyword in var.upper() for keyword in ['AGOUTIC', 'NEXTFLOW', 'DOGME', 'LAUNCHPAD', 'NXF', 'SLURM']):
        value = os.environ[var]
        # Truncate long values
        if len(value) > 80:
            value = value[:77] + "..."
        print(f"  {var:30} = {value}")

# === SECTION 2: Launchpad/Cortex Config Paths ===
print("\n[2] CONFIGURED PATHS (from launchpad.config):")
sys.path.insert(0, '/Users/eli/code/agoutic')
try:
    from launchpad.config import (
        AGOUTIC_CODE, AGOUTIC_DATA, LAUNCHPAD_WORK_DIR, LAUNCHPAD_LOGS_DIR, DOGME_REPO, NEXTFLOW_BIN
    )
    paths = {
        "AGOUTIC_CODE": str(AGOUTIC_CODE),
        "AGOUTIC_DATA": str(AGOUTIC_DATA),
        "LAUNCHPAD_WORK_DIR": str(LAUNCHPAD_WORK_DIR),
        "LAUNCHPAD_LOGS_DIR": str(LAUNCHPAD_LOGS_DIR),
        "DOGME_REPO": str(DOGME_REPO),
        "NEXTFLOW_BIN": str(NEXTFLOW_BIN),
    }
    for name, path in paths.items():
        exists = "✓" if Path(path).exists() else "✗"
        print(f"  {name:25} = {path:45} [{exists}]")
except Exception as e:
    print(f"  ERROR: {e}")

# === SECTION 3: Backend Selection Logic ===
print("\n[3] BACKEND ROUTING LOGIC TEST:")
test_cases = [
    {"execution_mode": "local", "expected": "NextflowExecutor"},
    {"execution_mode": "slurm", "expected": "SlurmBackend"},
    {"execution_mode": None, "expected": "NextflowExecutor"},
    {"execution_mode": "", "expected": "NextflowExecutor"},
    {"execution_mode": "SLURM", "expected": "NextflowExecutor"},  # Should be lowercase check
    {"execution_mode": " slurm", "expected": "NextflowExecutor"},  # Should be exact match
]

for test in test_cases:
    exec_mode = test["execution_mode"]
    expected = test["expected"]
    
    # Simulate launchpad backend selection logic
    if exec_mode == "slurm":
        selected = "SlurmBackend"
    else:
        selected = "NextflowExecutor"
    
    status = "✓" if selected == expected else "✗"
    print(f"  execution_mode={repr(exec_mode):15} → {selected:20} (expected {expected:20}) [{status}]")

# === SECTION 4: /scratch Check ===
print("\n[4] /SCRATCH DIRECTORY CHECK:")
scratch_path = Path("/scratch")
print(f"  /scratch exists: {scratch_path.exists()}")
if scratch_path.exists():
    try:
        stat_info = scratch_path.stat()
        print(f"  /scratch permissions: {oct(stat_info.st_mode)[-3:]}")
        print(f"  /scratch is_dir: {scratch_path.is_dir()}")
    except Exception as e:
        print(f"  Error checking /scratch: {e}")
else:
    print(f"  /scratch does NOT exist (expected on macOS)")

# === SECTION 5: Check for /scratch in any config files ===
print("\n[5] CONFIG FILES - Searching for '/scratch' references:")
config_files = [
    "/Users/eli/code/agoutic/launchpad/config.py",
    "/Users/eli/code/agoutic/cortex/config.py",
    "/Users/eli/code/agoutic/.env",
    "/Users/eli/code/agoutic/.env.local",
]

scratch_found_in_config = False
for cfgfile in config_files:
    cfg_path = Path(cfgfile)
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                for i, line in enumerate(f, 1):
                    if "/scratch" in line and not line.strip().startswith("#"):
                        print(f"  ⚠️ Found in {cfgfile}:{i}")
                        print(f"     {line.rstrip()}")
                        scratch_found_in_config = True
        except Exception as e:
            pass

if not scratch_found_in_config:
    print("  ✓ No /scratch references found in config files")

# === SECTION 6: Recent Launchpad Job Logs ===
print("\n[6] RECENT JOB SUBMISSION LOG (last 5 seconds of launchpad_logs/):")
logs_dir = Path("/Users/eli/code/agoutic/data/launchpad_logs")
if logs_dir.exists():
    log_files = sorted(logs_dir.glob("*"))
    if log_files:
        # Show last few files  
        for log_file in log_files[-3:]:
            print(f"  - {log_file.name}")
    else:
        print("  (No log files)")
else:
    print("  (launchpad_logs directory not found)")

print("\n" + "=" * 90)
