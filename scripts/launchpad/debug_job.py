#!/usr/bin/env python3
"""
Script to debug a submitted job and check what's actually happening.
Usage:
    python scripts/launchpad/debug_job.py <run_uuid>
    python scripts/launchpad/debug_job.py --work-dir /path/to/work/dir
"""

import argparse
import sys
from pathlib import Path
import subprocess

def check_job(work_dir: Path):
    """Check the status of a job by examining its work directory."""
    
    print(f"🔍 Debugging job in: {work_dir}")
    print("=" * 70)
    
    # Check if directory exists
    if not work_dir.exists():
        print(f"❌ Work directory doesn't exist!")
        return
    
    # List all files in work directory
    print("\n📁 Files in work directory:")
    for item in sorted(work_dir.iterdir()):
        if item.is_file():
            size = item.stat().st_size
            print(f"  {item.name:30s} ({size:>10,} bytes)")
        elif item.is_dir():
            try:
                num_files = len(list(item.rglob("*")))
                print(f"  {item.name + '/':<30s} ({num_files:>10} files)")
            except:
                print(f"  {item.name + '/':<30s} (directory)")
    
    # Check marker files
    print("\n🏷️  Job Status Markers:")
    markers = {
        ".nextflow_running": "RUNNING",
        ".nextflow_success": "SUCCESS",
        ".nextflow_failed": "FAILED",
        ".nextflow_pid": "PID",
    }
    
    for marker, status in markers.items():
        marker_path = work_dir / marker
        if marker_path.exists():
            content = marker_path.read_text().strip()
            print(f"  ✅ {marker:25s} - {status}: {content[:50]}")
        else:
            print(f"  ⬜ {marker:25s} - Not present")
    
    # Check for PID and process status
    pid_file = work_dir / ".nextflow_pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            print(f"\n🔢 Process PID: {pid}")
            
            # Check if process is running
            try:
                import os
                import signal
                os.kill(pid, 0)
                print(f"   ✅ Process is RUNNING")
                
                # Try to get process info
                try:
                    result = subprocess.run(
                        ["ps", "-p", str(pid), "-o", "pid,comm,etime,state"],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0:
                        print(f"   Process info:")
                        for line in result.stdout.strip().split('\n'):
                            print(f"     {line}")
                except:
                    pass
                    
            except OSError:
                print(f"   ❌ Process is NOT RUNNING (died or finished)")
        except Exception as e:
            print(f"   ⚠️  Could not check PID: {e}")
    
    # Check nextflow config
    config_file = work_dir / "nextflow.config"
    if config_file.exists():
        print(f"\n⚙️  Nextflow Config (first 20 lines):")
        lines = config_file.read_text().split('\n')[:20]
        for line in lines:
            print(f"  {line}")
        if len(config_file.read_text().split('\n')) > 20:
            print(f"  ... ({len(config_file.read_text().split('\n')) - 20} more lines)")
    
    # Check pod5 symlink
    pod5_link = work_dir / "pod5"
    print(f"\n📦 Pod5 Directory:")
    if pod5_link.exists():
        if pod5_link.is_symlink():
            target = pod5_link.resolve()
            print(f"  ✅ Symlink exists: {pod5_link} -> {target}")
            if target.exists():
                try:
                    files = list(target.glob("*.pod5"))
                    print(f"     Contains {len(files)} .pod5 files")
                    if files:
                        for f in files[:3]:
                            print(f"       - {f.name}")
                        if len(files) > 3:
                            print(f"       ... and {len(files) - 3} more")
                except Exception as e:
                    print(f"     ⚠️  Could not list files: {e}")
            else:
                print(f"     ❌ Symlink target doesn't exist!")
        else:
            print(f"  ⚠️  pod5 exists but is not a symlink")
    else:
        print(f"  ❌ pod5 symlink doesn't exist")
    
    # Check log files
    print(f"\n📋 Log Files:")
    log_dir = work_dir.parent.parent / "launchpad_logs"
    if work_dir.name != work_dir:
        run_uuid = work_dir.name
        log_files = [
            (f"{run_uuid}.log", "Nextflow main log"),
            (f"{run_uuid}_stdout.log", "Standard output"),
            (f"{run_uuid}_stderr.log", "Standard error"),
        ]
        
        for log_name, description in log_files:
            log_path = log_dir / log_name
            if log_path.exists():
                size = log_path.stat().st_size
                print(f"  📄 {log_name:30s} - {size:>10,} bytes ({description})")
                if size > 0 and size < 5000:
                    print(f"     Last few lines:")
                    lines = log_path.read_text().strip().split('\n')[-10:]
                    for line in lines:
                        print(f"       {line[:100]}")
            else:
                print(f"  ⬜ {log_name:30s} - Not found")
    
    # Check Nextflow work directory
    nf_work = work_dir / "work"
    if nf_work.exists():
        print(f"\n🔧 Nextflow Work Directory:")
        try:
            subdirs = [d for d in nf_work.iterdir() if d.is_dir()]
            print(f"  Contains {len(subdirs)} subdirectories")
            if subdirs:
                for d in subdirs[:3]:
                    files = list(d.glob("*"))
                    print(f"    - {d.name}/ ({len(files)} files)")
                if len(subdirs) > 3:
                    print(f"    ... and {len(subdirs) - 3} more")
        except Exception as e:
            print(f"  ⚠️  Could not list: {e}")
    else:
        print(f"\n⚠️  Nextflow work directory doesn't exist yet")
    
    # Check .nextflow.log
    nf_log = work_dir / ".nextflow.log"
    if nf_log.exists():
        print(f"\n📜 Nextflow Log (.nextflow.log) - last 20 lines:")
        lines = nf_log.read_text().strip().split('\n')[-20:]
        for line in lines:
            print(f"  {line}")
    else:
        print(f"\n⚠️  .nextflow.log doesn't exist (Nextflow may not have started)")


def main():
    parser = argparse.ArgumentParser(description="Debug a Launchpad job")
    parser.add_argument("run_uuid", nargs="?", help="Run UUID to debug")
    parser.add_argument("--work-dir", help="Direct path to work directory")
    
    args = parser.parse_args()
    
    if args.work_dir:
        work_dir = Path(args.work_dir)
    elif args.run_uuid:
        # Assume default location
        work_dir = Path("/media/backup_disk/agoutic_root/launchpad_work") / args.run_uuid
    else:
        print("❌ Must provide either run_uuid or --work-dir")
        parser.print_help()
        sys.exit(1)
    
    check_job(work_dir)


if __name__ == "__main__":
    main()
