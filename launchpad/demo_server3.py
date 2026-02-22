"""
Demo/Integration script for Launchpad.
Shows how to submit jobs and track their progress.
"""
import asyncio
import aiohttp
import json
from typing import Optional
import sys
from pathlib import Path

# Configuration
LAUNCHPAD_URL = "http://127.0.0.1:8001"
POLL_INTERVAL = 2  # seconds

class LaunchpadClient:
    """Async client for Launchpad API."""
    
    def __init__(self, base_url: str = LAUNCHPAD_URL):
        self.base_url = base_url.rstrip("/")
    
    async def health_check(self) -> dict:
        """Check server health."""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/health") as resp:
                return await resp.json()
    
    async def submit_job(
        self,
        project_id: str,
        sample_name: str,
        mode: str,
        input_directory: str,
        reference_genome: str = "GRCh38",
        modifications: Optional[str] = None,
    ) -> dict:
        """Submit a Dogme/Nextflow job."""
        payload = {
            "project_id": project_id,
            "sample_name": sample_name,
            "mode": mode,
            "input_directory": input_directory,
            "reference_genome": reference_genome,
        }
        if modifications:
            payload["modifications"] = modifications
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/jobs/submit",
                json=payload
            ) as resp:
                if resp.status != 200:
                    print(f"❌ Error: {resp.status}")
                    print(await resp.text())
                    raise RuntimeError(f"Failed to submit job: {resp.status}")
                return await resp.json()
    
    async def get_status(self, run_uuid: str) -> dict:
        """Get job status."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/jobs/{run_uuid}/status"
            ) as resp:
                if resp.status == 404:
                    raise RuntimeError(f"Job {run_uuid} not found")
                return await resp.json()
    
    async def get_details(self, run_uuid: str) -> dict:
        """Get full job details."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/jobs/{run_uuid}"
            ) as resp:
                if resp.status == 404:
                    raise RuntimeError(f"Job {run_uuid} not found")
                return await resp.json()
    
    async def get_logs(self, run_uuid: str, limit: int = 50) -> dict:
        """Get job logs."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/jobs/{run_uuid}/logs",
                params={"limit": limit}
            ) as resp:
                if resp.status == 404:
                    raise RuntimeError(f"Job {run_uuid} not found")
                return await resp.json()
    
    async def list_jobs(
        self,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> dict:
        """List jobs."""
        params = {}
        if project_id:
            params["project_id"] = project_id
        if status:
            params["status"] = status
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/jobs",
                params=params,
            ) as resp:
                return await resp.json()
    
    async def cancel_job(self, run_uuid: str) -> dict:
        """Cancel a running job."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/jobs/{run_uuid}/cancel"
            ) as resp:
                return await resp.json()

async def demo_submit_dna_job():
    """Demo: Submit a DNA analysis job."""
    print("\n" + "="*60)
    print("🧬 DEMO 1: Submit DNA Analysis Job")
    print("="*60 + "\n")
    
    client = LaunchpadClient()
    
    # Check server is healthy
    print("🔍 Checking server health...")
    try:
        health = await client.health_check()
        print(f"✅ Server OK: {health['status']}")
    except Exception as e:
        print(f"❌ Server not responding: {e}")
        print(f"   Make sure Launchpad is running: uvicorn launchpad.app:app --port 8001")
        return
    
    # Submit DNA job
    print("\n📤 Submitting DNA analysis job...")
    try:
        result = await client.submit_job(
            project_id="demo_project_001",
            sample_name="demo_liver_dna",
            mode="DNA",
            input_directory="/data/samples/liver_dna",
            reference_genome="GRCh38",
            modifications="5mCG_5hmCG,6mA",
        )
        
        run_uuid = result["run_uuid"]
        print(f"✅ Job submitted successfully!")
        print(f"   Run UUID: {run_uuid}")
        print(f"   Status: {result['status']}")
        print(f"   Work Dir: {result['work_directory']}")
        
        return run_uuid
    
    except Exception as e:
        print(f"❌ Failed to submit: {e}")
        return None

async def demo_track_job(run_uuid: str):
    """Demo: Track job progress."""
    print("\n" + "="*60)
    print("📊 DEMO 2: Track Job Progress")
    print("="*60 + "\n")
    
    if not run_uuid:
        print("⚠️  No job UUID provided")
        return
    
    client = LaunchpadClient()
    
    print(f"🔍 Monitoring job {run_uuid}...\n")
    
    for i in range(5):  # Check 5 times
        try:
            status = await client.get_status(run_uuid)
            
            print(f"[{i+1}] Status: {status['status']}")
            print(f"    Progress: {status['progress_percent']}%")
            print(f"    Message: {status['message']}\n")
            
            if status['status'] in ("COMPLETED", "FAILED"):
                print(f"✅ Job finished with status: {status['status']}")
                break
            
            await asyncio.sleep(POLL_INTERVAL)
        
        except Exception as e:
            print(f"⚠️  Error checking status: {e}\n")
            await asyncio.sleep(POLL_INTERVAL)

async def demo_get_logs(run_uuid: str):
    """Demo: Retrieve job logs."""
    print("\n" + "="*60)
    print("📝 DEMO 3: View Job Logs")
    print("="*60 + "\n")
    
    if not run_uuid:
        print("⚠️  No job UUID provided")
        return
    
    client = LaunchpadClient()
    
    print(f"📖 Logs for job {run_uuid}:\n")
    
    try:
        logs_resp = await client.get_logs(run_uuid, limit=10)
        
        if not logs_resp["logs"]:
            print("ℹ️  No logs available yet")
            return
        
        for log in logs_resp["logs"]:
            timestamp = log["timestamp"].split("T")[1][:8]
            level = log["level"]
            message = log["message"]
            
            icons = {
                "INFO": "ℹ️",
                "WARN": "⚠️",
                "ERROR": "❌",
            }
            icon = icons.get(level, "📝")
            
            print(f"{icon} [{timestamp}] {level}: {message}")
        
        print(f"\n✅ Retrieved {len(logs_resp['logs'])} log entries")
    
    except Exception as e:
        print(f"❌ Failed to get logs: {e}")

async def demo_list_jobs():
    """Demo: List all jobs."""
    print("\n" + "="*60)
    print("📋 DEMO 4: List All Jobs")
    print("="*60 + "\n")
    
    client = LaunchpadClient()
    
    try:
        jobs_resp = await client.list_jobs()
        
        print(f"Total jobs: {jobs_resp['total_count']}")
        print(f"Running jobs: {jobs_resp['running_count']}\n")
        
        if not jobs_resp["jobs"]:
            print("ℹ️  No jobs found")
            return
        
        for job in jobs_resp["jobs"]:
            print(f"🔹 {job['sample_name']}")
            print(f"   UUID: {job['run_uuid']}")
            print(f"   Mode: {job['mode']} | Status: {job['status']}")
            print(f"   Progress: {job['progress_percent']}%")
            print()
    
    except Exception as e:
        print(f"❌ Failed to list jobs: {e}")

async def demo_all():
    """Run all demos in sequence."""
    print("\n🚀 AGOUTIC Launchpad - Comprehensive Demo\n")
    
    # Demo 1: Submit job
    run_uuid = await demo_submit_dna_job()
    
    if run_uuid:
        # Demo 2: Track progress
        await demo_track_job(run_uuid)
        
        # Demo 3: Get logs
        await demo_get_logs(run_uuid)
    
    # Demo 4: List jobs
    await demo_list_jobs()
    
    print("\n✅ Demo complete!\n")

async def demo_rna_job():
    """Demo: Submit RNA analysis job."""
    print("\n" + "="*60)
    print("🧬 DEMO: Submit RNA Analysis Job")
    print("="*60 + "\n")
    
    client = LaunchpadClient()
    
    print("📤 Submitting RNA analysis job...")
    try:
        result = await client.submit_job(
            project_id="demo_project_002",
            sample_name="demo_brain_rna",
            mode="RNA",
            input_directory="/data/samples/brain_rna",
            reference_genome="GRCh38",
            modifications="inosine_m6A,pseU,m5C",
        )
        
        print(f"✅ RNA job submitted successfully!")
        print(f"   Run UUID: {result['run_uuid']}")
        
        return result['run_uuid']
    
    except Exception as e:
        print(f"❌ Failed to submit: {e}")
        return None

async def demo_cdna_job():
    """Demo: Submit cDNA analysis job."""
    print("\n" + "="*60)
    print("🧬 DEMO: Submit cDNA Analysis Job")
    print("="*60 + "\n")
    
    client = LaunchpadClient()
    
    print("📤 Submitting cDNA analysis job...")
    try:
        result = await client.submit_job(
            project_id="demo_project_003",
            sample_name="demo_liver_cdna",
            mode="CDNA",
            input_directory="/data/samples/liver_cdna",
            reference_genome="GRCh38",
        )
        
        print(f"✅ cDNA job submitted successfully!")
        print(f"   Run UUID: {result['run_uuid']}")
        print(f"   (Note: No modification calling for cDNA mode)")
        
        return result['run_uuid']
    
    except Exception as e:
        print(f"❌ Failed to submit: {e}")
        return None

if __name__ == "__main__":
    print("""
╔════════════════════════════════════════════════════════════╗
║        AGOUTIC Launchpad - Integration Demo               ║
║   Dogme/Nextflow Job Execution Engine (Week 3)           ║
╚════════════════════════════════════════════════════════════╝
    """)
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "dna":
            asyncio.run(demo_submit_dna_job())
        elif command == "rna":
            asyncio.run(demo_rna_job())
        elif command == "cdna":
            asyncio.run(demo_cdna_job())
        elif command == "track":
            if len(sys.argv) > 2:
                run_uuid = sys.argv[2]
                asyncio.run(demo_track_job(run_uuid))
            else:
                print("Usage: python demo_launchpad.py track <run_uuid>")
        elif command == "logs":
            if len(sys.argv) > 2:
                run_uuid = sys.argv[2]
                asyncio.run(demo_get_logs(run_uuid))
            else:
                print("Usage: python demo_launchpad.py logs <run_uuid>")
        elif command == "list":
            asyncio.run(demo_list_jobs())
        else:
            print(f"Unknown command: {command}")
    else:
        asyncio.run(demo_all())
