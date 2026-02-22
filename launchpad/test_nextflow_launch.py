#!/usr/bin/env python3
"""
Test script to verify Nextflow can be launched.
Run this on the server to diagnose issues.
"""
import subprocess
import sys
from pathlib import Path

def test_nextflow():
    """Test if Nextflow is available and working."""
    
    print("🔍 Testing Nextflow Installation...")
    print("=" * 70)
    
    # Check if nextflow command exists
    nextflow_paths = [
        "/usr/local/bin/nextflow",
        Path.home() / "bin" / "nextflow",
        "nextflow",  # In PATH
    ]
    
    nextflow_bin = None
    for nf_path in nextflow_paths:
        nf_path = Path(nf_path) if not isinstance(nf_path, Path) else nf_path
        
        if nf_path.exists() and nf_path.is_file():
            print(f"✅ Found Nextflow at: {nf_path}")
            nextflow_bin = nf_path
            break
        elif str(nf_path) == "nextflow":
            # Check in PATH
            try:
                result = subprocess.run(["which", "nextflow"], capture_output=True, text=True)
                if result.returncode == 0:
                    nextflow_bin = result.stdout.strip()
                    print(f"✅ Found Nextflow in PATH: {nextflow_bin}")
                    break
            except:
                pass
    
    if not nextflow_bin:
        print("❌ Nextflow not found!")
        print("   Please install Nextflow or set NEXTFLOW_BIN environment variable")
        return False
    
    # Test nextflow version
    print(f"\n📋 Testing Nextflow command...")
    try:
        result = subprocess.run(
            [str(nextflow_bin), "-version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print("✅ Nextflow responds to -version:")
            print(result.stdout)
        else:
            print(f"⚠️  Nextflow returned error code {result.returncode}")
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ Nextflow command timed out")
        return False
    except Exception as e:
        print(f"❌ Error running Nextflow: {e}")
        return False
    
    # Check Dogme repo
    print(f"\n🧬 Checking Dogme Repository...")
    dogme_paths = [
        Path("/media/backup_disk/agoutic_code/dogme"),
        Path.cwd().parent / "dogme",
        Path.home() / "dogme",
    ]
    
    dogme_repo = None
    for dogme_path in dogme_paths:
        if dogme_path.exists() and (dogme_path / "dogme.nf").exists():
            print(f"✅ Found Dogme at: {dogme_path}")
            dogme_repo = dogme_path
            break
    
    if not dogme_repo:
        print("❌ Dogme repository not found!")
        print("   Looking for directory containing dogme.nf")
        return False
    
    print(f"   dogme.nf exists: {(dogme_repo / 'dogme.nf').exists()}")
    
    # Check work directory permissions
    print(f"\n📁 Checking Work Directory...")
    work_dir = Path("/media/backup_disk/agoutic_root/launchpad_work")
    
    if work_dir.exists():
        print(f"✅ Work directory exists: {work_dir}")
        
        # Test write permissions
        test_file = work_dir / ".test_write"
        try:
            test_file.write_text("test")
            test_file.unlink()
            print(f"✅ Can write to work directory")
        except Exception as e:
            print(f"❌ Cannot write to work directory: {e}")
            return False
    else:
        print(f"⚠️  Work directory doesn't exist: {work_dir}")
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            print(f"✅ Created work directory")
        except Exception as e:
            print(f"❌ Cannot create work directory: {e}")
            return False
    
    # Check logs directory
    print(f"\n📋 Checking Logs Directory...")
    logs_dir = Path("/media/backup_disk/agoutic_root/launchpad_logs")
    
    if logs_dir.exists():
        print(f"✅ Logs directory exists: {logs_dir}")
    else:
        print(f"⚠️  Logs directory doesn't exist: {logs_dir}")
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            print(f"✅ Created logs directory")
        except Exception as e:
            print(f"❌ Cannot create logs directory: {e}")
            return False
    
    # Test write to logs
    test_log = logs_dir / ".test_log"
    try:
        test_log.write_text("test")
        test_log.unlink()
        print(f"✅ Can write to logs directory")
    except Exception as e:
        print(f"❌ Cannot write to logs directory: {e}")
        return False
    
    print("\n" + "=" * 70)
    print("✅ All checks passed! Nextflow should work.")
    print(f"\nConfiguration:")
    print(f"  Nextflow: {nextflow_bin}")
    print(f"  Dogme:    {dogme_repo}")
    print(f"  Work Dir: {work_dir}")
    print(f"  Logs Dir: {logs_dir}")
    
    return True


if __name__ == "__main__":
    success = test_nextflow()
    sys.exit(0 if success else 1)
