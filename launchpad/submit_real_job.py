#!/usr/bin/env python3
"""
Script to submit a REAL job to Launchpad.
Usage:
    python launchpad/submit_real_job.py --url http://localhost:8001 --input /path/to/data --mode DNA
"""

import argparse
import sys
import time
import json
import httpx
from typing import Optional

def submit_job(
    server_url: str,
    project_id: str,
    sample_name: str,
    mode: str,
    input_directory: str,
    reference_genome: str,
    modifications: Optional[str] = None
):
    """Submit a job to the server."""
    url = f"{server_url.rstrip('/')}/jobs/submit"
    
    payload = {
        "project_id": project_id,
        "sample_name": sample_name,
        "mode": mode,
        "input_directory": input_directory,
        "reference_genome": reference_genome,
    }
    
    if modifications:
        payload["modifications"] = modifications
        
    print(f"🚀 Submitting job to {url}...")
    print(f"📋 Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = httpx.post(url, json=payload, timeout=30.0)
        
        if response.status_code == 200:
            data = response.json()
            print("\n✅ Job Submitted Successfully!")
            print(f"🔹 Run UUID: {data['run_uuid']}")
            print(f"🔹 Status:   {data['status']}")
            print(f"🔹 Work Dir: {data['work_directory']}")
            return data['run_uuid']
        else:
            print(f"\n❌ Submission Failed (Status {response.status_code})")
            print(f"Body: {response.text}")
            return None
            
    except httpx.RequestError as e:
        print(f"\n❌ Connection Error: {e}")
        return None

def poll_status(server_url: str, run_uuid: str):
    """Poll the status of a job until completion or user interrupt."""
    url = f"{server_url.rstrip('/')}/jobs/{run_uuid}/status"
    
    print(f"\n📊 Monitoring Job {run_uuid} (Ctrl+C to stop)...")
    print("=" * 80)
    
    last_completed = []
    last_running = []
    
    try:
        while True:
            try:
                response = httpx.get(url, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    status = data['status']
                    progress = data['progress_percent']
                    message = data.get('message', '')
                    tasks = data.get('tasks', {})
                    
                    # Clear screen and show header
                    sys.stdout.write("\033[2J\033[H")  # Clear screen and move to top
                    print(f"📊 Job: {run_uuid}")
                    print(f"🔹 Status: {status} | Progress: {progress}%")
                    print(f"💬 {message}")
                    print("=" * 80)
                    
                    # Show task details - handle both dict and empty cases
                    if tasks and isinstance(tasks, dict):
                        total = tasks.get('total', 0)
                        completed_count = tasks.get('completed_count', 0)
                        failed_count = tasks.get('failed_count', 0)
                        running_list = tasks.get('running', [])
                        completed_list = tasks.get('completed', [])
                        
                        if total > 0 or running_list or completed_list:
                            print(f"\n📈 Task Progress:")
                            if total > 0:
                                print(f"   Total: {total}")
                                print(f"   ✅ Completed: {completed_count}")
                                if failed_count > 0:
                                    print(f"   ❌ Failed: {failed_count}")
                                print(f"   🏃 Running: {len(running_list)}")
                            
                            # Show recently completed tasks
                            if completed_list:
                                print(f"\n✅ Completed Tasks:")
                                
                                # Group tasks by base name
                                task_groups = {}
                                for task in completed_list:
                                    # Extract base name and instance number
                                    # e.g., "mainWorkflow:doradoTask (1)" -> "mainWorkflow:doradoTask", 1
                                    if '(' in task and ')' in task:
                                        base_name = task[:task.rfind('(')].strip()
                                        try:
                                            instance = int(task[task.rfind('(')+1:task.rfind(')')].strip())
                                            if base_name not in task_groups:
                                                task_groups[base_name] = []
                                            task_groups[base_name].append(instance)
                                        except ValueError:
                                            # Not a number in parentheses, treat as single task
                                            task_groups[task] = None
                                    else:
                                        task_groups[task] = None
                                
                                # Display grouped tasks
                                for base_name, instances in task_groups.items():
                                    if instances is None:
                                        # Single task without instances
                                        print(f"   • {base_name}")
                                    elif len(instances) == 1:
                                        # Single instance, show as is
                                        print(f"   • {base_name} ({instances[0]})")
                                    else:
                                        # Multiple instances, show consolidated
                                        instances_sorted = sorted(instances)
                                        print(f"   • {base_name} ({len(instances)}/{max(instances_sorted)})")
                                
                                last_completed = completed_list
                            
                            # Show currently running tasks
                            if running_list:
                                if running_list != last_running:
                                    print(f"\n🏃 Currently Running:")
                                    for task in running_list:
                                        # Shorten long task names
                                        task_short = task if len(task) < 70 else task[:67] + "..."
                                        print(f"   • {task_short}")
                                    last_running = running_list
                            elif last_running:
                                # Tasks finished, clear display
                                last_running = []
                    else:
                        # No task data available yet
                        print(f"\n⏳ Waiting for task information...")
                    
                    print("\n" + "=" * 80)
                    print("Press Ctrl+C to stop monitoring (job will continue)")
                    
                    if status in ["COMPLETED", "FAILED"]:
                        print("\n")
                        if status == "COMPLETED":
                            print("🎉 Job Finished Successfully!")
                        else:
                            print("💥 Job Failed.")
                        break
                else:
                    print(f"\n⚠️ Error checking status: {response.status_code}")
            
            except httpx.RequestError:
                print("\n⚠️ Connection Check Failed")
                
            time.sleep(1.5)  # Poll every 1.5 seconds for better real-time updates
            
    except KeyboardInterrupt:
        print("\n\n🛑 Monitoring Stopped (Job continues on server)")

def get_debug_info(server_url: str, run_uuid: str):
    """Get detailed debug information about a job."""
    url = f"{server_url.rstrip('/')}/jobs/{run_uuid}/debug"
    
    print(f"\n🔍 Getting Debug Info for Job {run_uuid}...")
    
    try:
        response = httpx.get(url, timeout=10.0)
        if response.status_code == 200:
            data = response.json()
            
            print(f"\n📋 Job Status: {data['status']}")
            print(f"📁 Work Directory: {data['work_directory']}")
            
            print(f"\n🏷️  Markers:")
            for marker, info in data['markers'].items():
                if info['exists']:
                    print(f"  ✅ {marker}: {info['content'][:60]}")
                else:
                    print(f"  ⬜ {marker}: Not present")
            
            if data['process_info']:
                print(f"\n🔢 Process Info:")
                for key, value in data['process_info'].items():
                    print(f"  {key}: {value}")
            
            print(f"\n📦 Files:")
            for filename, info in data['files'].items():
                if 'exists' in info and not info['exists']:
                    print(f"  ⬜ {filename}: Not found")
                elif info.get('type') == 'symlink':
                    status = "✅" if info['target_exists'] else "❌"
                    print(f"  {status} {filename} -> {info['target']}")
                elif info.get('type') == 'directory':
                    print(f"  📁 {filename}/: {info.get('num_files', '?')} files")
                elif info.get('type') == 'file':
                    print(f"  📄 {filename}: {info['size']:,} bytes")
            
            print(f"\n📋 Log Files:")
            for log_name, log_info in data['logs_preview'].items():
                if 'exists' in log_info and not log_info['exists']:
                    print(f"  ⬜ {log_name}: Not found")
                elif 'error' in log_info:
                    print(f"  ⚠️  {log_name}: {log_info['error']}")
                else:
                    print(f"  📄 {log_name}: {log_info['size']:,} bytes, {log_info['lines']} lines")
                    
                    # Show errors/warnings for .nextflow.log
                    if log_name == ".nextflow.log" and 'errors_and_warnings' in log_info:
                        if log_info['errors_and_warnings']:
                            print(f"     ⚠️  Errors/Warnings:")
                            for line in log_info['errors_and_warnings']:
                                if line.strip():
                                    print(f"       {line[:120]}")
                    
                    # Show last few lines for other logs
                    elif log_info.get('last_10_lines') or log_info.get('last_20_lines'):
                        lines = log_info.get('last_20_lines') or log_info.get('last_10_lines')
                        if lines:
                            print(f"     Last few lines:")
                            for line in lines[-5:]:
                                if line.strip():
                                    print(f"       {line[:120]}")
            
            return data
        else:
            print(f"\n❌ Error: {response.status_code}")
            print(f"Body: {response.text}")
            return None
            
    except httpx.RequestError as e:
        print(f"\n❌ Connection Error: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Submit a real job to Launchpad")
    
    parser.add_argument("--url", default="http://localhost:8001", help="Launchpad URL")
    parser.add_argument("--project", default="manual_test_001", help="Project ID")
    parser.add_argument("--sample", default="manual_sample", help="Sample Name")
    parser.add_argument("--mode", default="DNA", choices=["DNA", "RNA", "CDNA"], help="Analysis Mode")
    parser.add_argument("--input", required=True, help="Path to input .pod5 directory")
    parser.add_argument("--no-poll", action="store_true", help="Do not poll for status after submission")
    parser.add_argument("--ref", default="GRCh38", help="Reference genome (GRCh38, mm39)")
    parser.add_argument("--debug", help="Get debug info for an existing job UUID (skips submission)")
    
    args = parser.parse_args()
    
    # If --debug is provided, just show debug info
    if args.debug:
        get_debug_info(args.url, args.debug)
        return
    
    run_uuid = submit_job(
        server_url=args.url,
        project_id=args.project,
        sample_name=args.sample,
        mode=args.mode,
        input_directory=args.input,
        reference_genome=args.ref
    )
    
    if run_uuid and not args.no_poll:
        # Give the job a moment to start before polling
        time.sleep(1)
        poll_status(args.url, run_uuid)
        # After polling completes, show debug info
        print("\n" + "="*70)
        get_debug_info(args.url, run_uuid)

if __name__ == "__main__":
    main()
