#!/usr/bin/env python3
"""
Script to submit a REAL job to Server 3.
Usage:
    python server3/submit_real_job.py --url http://localhost:8001 --input /path/to/data --mode DNA
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
    
    try:
        while True:
            try:
                response = httpx.get(url, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    status = data['status']
                    progress = data['progress_percent']
                    message = data.get('message', '')
                    
                    # Clear line and print status
                    sys.stdout.write(f"\r⏳ Status: {status:<10} | Progress: {progress}% | {message[:40]}")
                    sys.stdout.flush()
                    
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
                
            time.sleep(2)
            
    except KeyboardInterrupt:
        print("\n\n🛑 Creation Stopped (Job continues on server)")

def main():
    parser = argparse.ArgumentParser(description="Submit a real job to Server 3")
    
    parser.add_argument("--url", default="http://localhost:8001", help="Server 3 URL")
    parser.add_argument("--project", default="manual_test_001", help="Project ID")
    parser.add_argument("--sample", default="manual_sample", help="Sample Name")
    parser.add_argument("--mode", default="DNA", choices=["DNA", "RNA", "CDNA"], help="Analysis Mode")
    parser.add_argument("--input", required=True, help="Path to input .pod5 directory")
    parser.add_argument("--no-poll", action="store_true", help="Do not poll for status after valid submission")
    parser.add_argument("--ref", default="GRCh38", help="Reference genome (GRCh38, mm39)")
    
    args = parser.parse_args()
    
    run_uuid = submit_job(
        server_url=args.url,
        project_id=args.project,
        sample_name=args.sample,
        mode=args.mode,
        input_directory=args.input,
        reference_genome=args.ref
    )
    
    if run_uuid and not args.no_poll:
        poll_status(args.url, run_uuid)

if __name__ == "__main__":
    main()
