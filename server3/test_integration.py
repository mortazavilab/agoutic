"""
Integration test: Server 1 (Agent) → Server 3 (Executor)
Simulates the complete workflow for week 3.
"""
import asyncio
import json
import uuid
from pathlib import Path

# Simulate Server 1 agent making a request to Server 3

class SimulatedServer1Agent:
    """Simulates Server 1 Agent behavior."""
    
    def __init__(self, server3_url: str = "http://localhost:8001"):
        self.server3_url = server3_url
    
    async def request_dna_analysis(
        self,
        sample_id: str,
        sample_path: str,
        project_id: str,
    ) -> dict:
        """
        Simulates Server 1 Agent requesting DNA analysis via Server 3.
        
        This is what happens when the LLM (from Server 1) detects
        an [[APPROVAL_NEEDED]] tag and gets user approval.
        """
        import httpx
        
        print(f"\n🔌 Server 1 → Server 3")
        print(f"   Submitting DNA analysis job...")
        
        payload = {
            "project_id": project_id,
            "sample_name": sample_id,
            "mode": "DNA",
            "input_directory": sample_path,
            "reference_genome": "GRCh38",
            "modifications": "5mCG_5hmCG,6mA",
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.server3_url}/jobs/submit",
                    json=payload,
                    timeout=30.0,
                )
                
                if response.status_code != 200:
                    print(f"   ❌ Error: {response.status_code}")
                    print(f"   Response: {response.text}")
                    return None
                
                result = response.json()
                print(f"   ✅ Job submitted successfully!")
                print(f"   Run UUID: {result['run_uuid']}")
                
                return result
        
        except Exception as e:
            print(f"   ❌ Connection failed: {e}")
            return None
    
    async def check_job_progress(self, run_uuid: str) -> dict:
        """Poll Server 3 for job status."""
        import httpx
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.server3_url}/jobs/{run_uuid}/status",
                    timeout=10.0,
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    return None
        
        except Exception as e:
            print(f"   ❌ Status check failed: {e}")
            return None
    
    async def get_results(self, run_uuid: str) -> dict:
        """Retrieve analysis results from Server 3."""
        import httpx
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.server3_url}/jobs/{run_uuid}",
                    timeout=10.0,
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    return None
        
        except Exception as e:
            print(f"   ❌ Failed to get results: {e}")
            return None


async def test_week3_workflow():
    """
    Complete week 3 workflow test:
    
    1. Server 1 receives user request for DNA analysis
    2. Agent generates plan with [[APPROVAL_NEEDED]]
    3. User approves
    4. Server 1 submits job to Server 3
    5. Server 3 returns job UUID
    6. Server 1 polls status until completion
    7. Server 1 retrieves results
    """
    
    print("\n" + "="*70)
    print("WEEK 3 INTEGRATION TEST: Server 1 ↔ Server 3 Workflow")
    print("="*70)
    
    # Setup
    agent = SimulatedServer1Agent(server3_url="http://localhost:8001")
    project_id = f"test_project_{uuid.uuid4().hex[:8]}"
    
    print(f"\n📋 Workflow Parameters:")
    print(f"   Project ID: {project_id}")
    print(f"   Sample Type: DNA")
    print(f"   Reference: GRCh38")
    
    # Step 1: Submit job
    print(f"\n📤 Step 1: Server 1 submits job to Server 3...")
    result = await agent.request_dna_analysis(
        sample_id="demo_liver_dna",
        sample_path="/data/samples/liver_dna",
        project_id=project_id,
    )
    
    if not result:
        print("❌ Failed to submit job. Is Server 3 running?")
        print("   Run: uvicorn server3.app:app --port 8001")
        return False
    
    run_uuid = result["run_uuid"]
    
    # Step 2-4: Poll status
    print(f"\n📊 Step 2-4: Monitoring job progress...")
    
    max_polls = 10
    for poll_count in range(1, max_polls + 1):
        print(f"\n   Poll #{poll_count}/{max_polls}")
        
        status = await agent.check_job_progress(run_uuid)
        if not status:
            print("   ⚠️  Failed to check status")
            await asyncio.sleep(2)
            continue
        
        print(f"   Status: {status['status']}")
        print(f"   Progress: {status['progress_percent']}%")
        print(f"   Message: {status['message']}")
        
        # Check if job finished
        if status['status'] in ("COMPLETED", "FAILED"):
            print(f"\n✅ Job finished with status: {status['status']}")
            break
        
        # Wait before next poll
        await asyncio.sleep(3)
    
    # Step 5: Get results
    print(f"\n📥 Step 5: Retrieving analysis results...")
    
    job_details = await agent.get_results(run_uuid)
    if job_details:
        print(f"   ✅ Results retrieved:")
        print(f"   Status: {job_details['status']}")
        print(f"   Completed at: {job_details['completed_at']}")
        if job_details['output_directory']:
            print(f"   Output: {job_details['output_directory']}")
    else:
        print(f"   ⚠️  Could not retrieve results")
    
    print(f"\n" + "="*70)
    print("✅ Week 3 Integration Test Complete")
    print("="*70)
    
    return True


async def test_multi_mode_workflow():
    """Test all three analysis modes in sequence."""
    
    print("\n" + "="*70)
    print("MULTI-MODE TEST: DNA, RNA, cDNA Analysis")
    print("="*70)
    
    agent = SimulatedServer1Agent(server3_url="http://localhost:8001")
    project_id = f"multi_mode_{uuid.uuid4().hex[:8]}"
    
    modes = [
        {
            "name": "DNA",
            "sample_id": "liver_dna",
            "path": "/data/samples/dna",
            "modifications": "5mCG_5hmCG,6mA",
        },
        {
            "name": "RNA",
            "sample_id": "brain_rna",
            "path": "/data/samples/rna",
            "modifications": "inosine_m6A,pseU,m5C",
        },
        {
            "name": "cDNA",
            "sample_id": "liver_cdna",
            "path": "/data/samples/cdna",
            "modifications": None,
        },
    ]
    
    submitted_jobs = []
    
    # Submit all jobs
    print(f"\n📤 Submitting all jobs for project: {project_id}")
    
    for mode_info in modes:
        print(f"\n  Submitting {mode_info['name']} job...")
        
        import httpx
        
        payload = {
            "project_id": project_id,
            "sample_name": mode_info["sample_id"],
            "mode": mode_info["name"],
            "input_directory": mode_info["path"],
            "reference_genome": "GRCh38",
        }
        
        if mode_info["modifications"]:
            payload["modifications"] = mode_info["modifications"]
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "http://localhost:8001/jobs/submit",
                    json=payload,
                    timeout=10.0,
                )
                
                if response.status_code == 200:
                    job = response.json()
                    submitted_jobs.append({
                        "mode": mode_info["name"],
                        "run_uuid": job["run_uuid"],
                    })
                    print(f"  ✅ {mode_info['name']} submitted: {job['run_uuid'][:12]}...")
                else:
                    print(f"  ❌ {mode_info['name']} failed: {response.status_code}")
        
        except Exception as e:
            print(f"  ❌ {mode_info['name']} error: {e}")
    
    if not submitted_jobs:
        print("❌ No jobs submitted successfully")
        return False
    
    print(f"\n✅ Submitted {len(submitted_jobs)} jobs")
    
    # Monitor all jobs
    print(f"\n📊 Monitoring all jobs...")
    
    for _ in range(5):
        print(f"\n  Status check:")
        
        import httpx
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "http://localhost:8001/jobs",
                    params={"project_id": project_id},
                    timeout=10.0,
                )
                
                if response.status_code == 200:
                    jobs_resp = response.json()
                    print(f"  Total jobs: {jobs_resp['total_count']}")
                    print(f"  Running: {jobs_resp['running_count']}")
                    
                    for job in jobs_resp["jobs"]:
                        print(f"    - {job['sample_name']}: {job['status']} ({job['progress_percent']}%)")
        
        except Exception as e:
            print(f"  ⚠️  Error: {e}")
        
        await asyncio.sleep(3)
    
    print(f"\n" + "="*70)
    print("✅ Multi-Mode Test Complete")
    print("="*70)
    
    return True


async def test_error_handling():
    """Test error scenarios."""
    
    print("\n" + "="*70)
    print("ERROR HANDLING TEST")
    print("="*70)
    
    agent = SimulatedServer1Agent(server3_url="http://localhost:8001")
    
    # Test 1: Invalid mode
    print(f"\n1️⃣  Test: Invalid mode")
    print(f"   Submitting with invalid mode...")
    
    import httpx
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8001/jobs/submit",
                json={
                    "project_id": "test",
                    "sample_name": "test",
                    "mode": "INVALID_MODE",
                    "input_directory": "/data/test",
                },
                timeout=10.0,
            )
            
            if response.status_code != 200:
                print(f"   ✅ Correctly rejected: {response.status_code}")
            else:
                print(f"   ⚠️  Accepted invalid mode")
    
    except Exception as e:
        print(f"   ⚠️  Error: {e}")
    
    # Test 2: Missing required field
    print(f"\n2️⃣  Test: Missing required field")
    print(f"   Submitting without input_directory...")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8001/jobs/submit",
                json={
                    "project_id": "test",
                    "sample_name": "test",
                    "mode": "DNA",
                    # Missing input_directory
                },
                timeout=10.0,
            )
            
            if response.status_code != 200:
                print(f"   ✅ Correctly rejected: {response.status_code}")
            else:
                print(f"   ⚠️  Accepted invalid payload")
    
    except Exception as e:
        print(f"   Error (expected): {e}")
    
    # Test 3: Nonexistent job
    print(f"\n3️⃣  Test: Nonexistent job")
    print(f"   Querying nonexistent job...")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "http://localhost:8001/jobs/nonexistent_uuid",
                timeout=10.0,
            )
            
            if response.status_code == 404:
                print(f"   ✅ Correctly returned 404")
            else:
                print(f"   ⚠️  Unexpected response: {response.status_code}")
    
    except Exception as e:
        print(f"   Error: {e}")
    
    print(f"\n" + "="*70)
    print("✅ Error Handling Test Complete")
    print("="*70)


async def main():
    """Run all integration tests."""
    
    print("""
╔════════════════════════════════════════════════════════════╗
║        AGOUTIC Week 3 - Integration Tests                ║
║   Server 1 (Agent) ↔ Server 3 (Executor)                 ║
╚════════════════════════════════════════════════════════════╝
    """)
    
    # Check if server is running
    import httpx
    
    print("🔍 Checking if Server 3 is running...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "http://localhost:8001/health",
                timeout=5.0,
            )
            if response.status_code == 200:
                print("✅ Server 3 is running\n")
            else:
                print(f"⚠️  Server 3 returned: {response.status_code}\n")
    except Exception as e:
        print(f"❌ Server 3 not responding: {e}")
        print("   Start Server 3 first: uvicorn server3.app:app --port 8001\n")
        return
    
    # Run tests
    print("\n" + "="*70)
    print("Running Integration Tests")
    print("="*70)
    
    await test_week3_workflow()
    await test_error_handling()
    
    print("""
╔════════════════════════════════════════════════════════════╗
║        ✅ All Tests Complete                              ║
║                                                            ║
║  Next Steps:                                               ║
║  - Review logs: tail -f /media/.../server3_logs/*.log    ║
║  - Check database: sqlite3 .../agoutic_v23.sqlite        ║
║  - Run demo: python server3/demo_server3.py              ║
╚════════════════════════════════════════════════════════════╝
    """)


if __name__ == "__main__":
    asyncio.run(main())
