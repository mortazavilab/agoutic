"""
Comprehensive test suite for Server 3.
Tests job submission, monitoring, and status tracking.
"""
import pytest
import asyncio
import json
import uuid
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest_asyncio

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

# Async fixtures
@pytest_asyncio.fixture
async def db_session():
    """Create a test database session."""
    from server3.db import SessionLocal, init_db
    await init_db()
    async with SessionLocal() as session:
        yield session

@pytest.fixture
def sample_job_request():
    """Sample DNA job submission request (based on GDNAmicro example config)."""
    return {
        "project_id": "test_project_dna_001",
        "sample_name": "GDNAmicro",
        "mode": "DNA",
        "input_directory": "/media/backup_disk/agoutic_root/testdata/GDNA/pod5",
        "reference_genome": "mm39",
        "modifications": "5mCG_5hmCG,6mA",
        "parent_block_id": "block_dna_001",
    }

@pytest.fixture
def sample_job_request_rna():
    """Sample RNA job submission request (based on DRNAmicro example config)."""
    return {
        "project_id": "test_project_rna_002",
        "sample_name": "DRNAmicro",
        "mode": "RNA",
        "input_directory": "/media/backup_disk/agoutic_root/testdata/DRNA/pod5",
        "reference_genome": "mm39",
        "modifications": "inosine_m6A_2OmeA,pseU_2OmeU,m5C_2OmeC,2OmeG",
    }

@pytest.fixture
def sample_job_request_cdna():
    """Sample cDNA job submission request (based on CDNAmicro example config)."""
    return {
        "project_id": "test_project_cdna_003",
        "sample_name": "CDNAmicro",
        "mode": "CDNA",
        "input_directory": "/media/backup_disk/agoutic_root/testdata/CDNA/pod5",
        "reference_genome": "mm39",
    }

# --- CONFIG TESTS ---
class TestNextflowConfig:
    """Test Nextflow configuration generation."""
    
    def test_generate_dna_config(self):
        """Test DNA mode configuration."""
        from server3.nextflow_executor import NextflowConfig
        
        config = NextflowConfig.generate_config(
            sample_name="test_dna",
            mode="DNA",
            input_dir="/data/test",
            reference_genome="GRCh38",
            modifications="5mCG_5hmCG,6mA",
        )
        
        assert isinstance(config, str)
        assert "readType = 'DNA'" in config
        assert "modifications = '5mCG_5hmCG,6mA'" in config
        assert "sample = 'test_dna'" in config
        assert "params {" in config
        assert "process {" in config
    
    def test_generate_rna_config(self):
        """Test RNA mode configuration."""
        from server3.nextflow_executor import NextflowConfig
        
        config = NextflowConfig.generate_config(
            sample_name="test_rna",
            mode="RNA",
            input_dir="/data/test",
            reference_genome="GRCh38",
            modifications="inosine_m6A",
        )
        
        assert isinstance(config, str)
        assert "readType = 'RNA'" in config
        assert "modifications = 'inosine_m6A'" in config
        assert "params {" in config
        assert "process {" in config
    
    def test_generate_cdna_config(self):
        """Test cDNA mode configuration (no modifications)."""
        from server3.nextflow_executor import NextflowConfig
        
        config = NextflowConfig.generate_config(
            sample_name="test_cdna",
            mode="CDNA",
            input_dir="/data/test",
            reference_genome="GRCh38",
        )
        
        assert isinstance(config, str)
        assert "readType = 'CDNA'" in config
        assert "modifications" not in config
        assert "params {" in config
        assert "process {" in config
    
    def test_generate_dna_default_mincov(self):
        """Test that DNA mode has minCov=1 by default."""
        from server3.nextflow_executor import NextflowConfig
        
        config = NextflowConfig.generate_config(
            sample_name="test_dna",
            mode="DNA",
            input_dir="/data/test",
            reference_genome="GRCh38",
        )
        
        assert "minCov = 1" in config
    
    def test_generate_rna_default_mincov(self):
        """Test that RNA mode has minCov=3 by default."""
        from server3.nextflow_executor import NextflowConfig
        
        config = NextflowConfig.generate_config(
            sample_name="test_rna",
            mode="RNA",
            input_dir="/data/test",
            reference_genome="GRCh38",
        )
        
        assert "minCov = 3" in config
    
    def test_generate_default_modifications(self):
        """Test that default modifications are applied when not provided."""
        from server3.nextflow_executor import NextflowConfig
        
        # DNA without explicit modifications
        config_dna = NextflowConfig.generate_config(
            sample_name="test_dna",
            mode="DNA",
            input_dir="/data/test",
            reference_genome="GRCh38",
        )
        assert "modifications = '5mCG_5hmCG,6mA'" in config_dna
        
        # RNA without explicit modifications
        config_rna = NextflowConfig.generate_config(
            sample_name="test_rna",
            mode="RNA",
            input_dir="/data/test",
            reference_genome="GRCh38",
        )
        assert "modifications = 'inosine_m6A_2OmeA,pseU_2OmeU,m5C_2OmeC,2OmeG'" in config_rna

# --- DATABASE TESTS ---
class TestDatabase:
    """Test database operations."""
    
    @pytest.mark.asyncio
    async def test_create_job(self, db_session, sample_job_request):
        """Test job creation."""
        from server3.db import create_job
        
        run_uuid = str(uuid.uuid4())
        job = await create_job(
            db_session,
            run_uuid=run_uuid,
            project_id=sample_job_request["project_id"],
            sample_name=sample_job_request["sample_name"],
            mode=sample_job_request["mode"],
            input_directory=sample_job_request["input_directory"],
            reference_genome=sample_job_request["reference_genome"],
            modifications=sample_job_request["modifications"],
        )
        
        assert job.run_uuid == run_uuid
        assert job.sample_name == "GDNAmicro"
        assert job.mode == "DNA"
        assert job.status == "PENDING"
    
    @pytest.mark.asyncio
    async def test_get_job(self, db_session, sample_job_request):
        """Test job retrieval."""
        from server3.db import create_job, get_job
        
        run_uuid = str(uuid.uuid4())
        await create_job(
            db_session,
            run_uuid=run_uuid,
            project_id=sample_job_request["project_id"],
            sample_name=sample_job_request["sample_name"],
            mode=sample_job_request["mode"],
            input_directory=sample_job_request["input_directory"],
        )
        
        retrieved = await get_job(db_session, run_uuid)
        assert retrieved is not None
        assert retrieved.run_uuid == run_uuid
    
    @pytest.mark.asyncio
    async def test_update_job_status(self, db_session, sample_job_request):
        """Test job status update."""
        from server3.db import create_job, update_job_status
        
        run_uuid = str(uuid.uuid4())
        await create_job(
            db_session,
            run_uuid=run_uuid,
            project_id=sample_job_request["project_id"],
            sample_name=sample_job_request["sample_name"],
            mode=sample_job_request["mode"],
            input_directory=sample_job_request["input_directory"],
        )
        
        updated = await update_job_status(
            db_session,
            run_uuid,
            "RUNNING",
            progress=50,
        )
        
        assert updated.status == "RUNNING"
        assert updated.progress_percent == 50
    
    @pytest.mark.asyncio
    async def test_add_log_entry(self, db_session, sample_job_request):
        """Test log entry creation."""
        from server3.db import create_job, add_log_entry, get_job_logs
        
        run_uuid = str(uuid.uuid4())
        await create_job(
            db_session,
            run_uuid=run_uuid,
            project_id=sample_job_request["project_id"],
            sample_name=sample_job_request["sample_name"],
            mode=sample_job_request["mode"],
            input_directory=sample_job_request["input_directory"],
        )
        
        log = await add_log_entry(
            db_session,
            run_uuid,
            "INFO",
            "Test log message",
            source="test",
        )
        
        assert log.run_uuid == run_uuid
        assert log.level == "INFO"
        
        logs = await get_job_logs(db_session, run_uuid)
        assert len(logs) == 1
        assert logs[0]["message"] == "Test log message"

# --- SCHEMA VALIDATION TESTS ---
class TestSchemas:
    """Test Pydantic schemas."""
    
    def test_submit_job_request_schema(self, sample_job_request):
        """Test SubmitJobRequest schema."""
        from server3.schemas import SubmitJobRequest
        
        req = SubmitJobRequest(**sample_job_request)
        assert req.project_id == "test_project_dna_001"
        assert req.sample_name == "GDNAmicro"
        assert req.mode == "DNA"
    
    def test_job_status_response_schema(self):
        """Test JobStatusResponse schema."""
        from server3.schemas import JobStatusResponse
        
        response = JobStatusResponse(
            run_uuid="test-uuid",
            status="RUNNING",
            progress_percent=50,
            message="In progress",
        )
        
        assert response.run_uuid == "test-uuid"
        assert response.progress_percent == 50
    
    def test_job_details_response_schema(self):
        """Test JobDetailsResponse schema."""
        from server3.schemas import JobDetailsResponse
        
        response = JobDetailsResponse(
            run_uuid="test-uuid",
            project_id="proj_001",
            sample_name="sample_001",
            mode="DNA",
            status="RUNNING",
            progress_percent=75,
            submitted_at="2025-01-22T12:00:00",
            started_at="2025-01-22T12:05:00",
            completed_at=None,
            output_directory="/results/sample_001",
            error_message=None,
            report=None,
        )
        
        assert response.sample_name == "sample_001"
        assert response.progress_percent == 75

# --- API ENDPOINT TESTS ---
class TestAPIEndpoints:
    """Test FastAPI endpoints."""
    
    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test health check endpoint."""
        from fastapi.testclient import TestClient
        from server3.app import app
        
        client = TestClient(app)
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.3.0"
    
    @pytest.mark.asyncio
    async def test_root_endpoint(self):
        """Test root endpoint."""
        from fastapi.testclient import TestClient
        from server3.app import app
        
        client = TestClient(app)
        response = client.get("/")
        
        assert response.status_code == 200
        data = response.json()
        assert "AGOUTIC Server 3" in data["service"]
        assert "endpoints" in data

# --- INTEGRATION TESTS ---
class TestIntegration:
    """Integration tests."""
    
    @pytest.mark.asyncio
    async def test_job_lifecycle(self, sample_job_request):
        """Test complete job lifecycle: submit -> track -> monitor."""
        from server3.db import (
            SessionLocal, init_db, create_job, get_job, update_job_status
        )
        from server3.config import JobStatus
        
        await init_db()
        session = SessionLocal()
        
        try:
            # 1. Submit job
            run_uuid = str(uuid.uuid4())
            job = await create_job(
                session,
                run_uuid=run_uuid,
                project_id=sample_job_request["project_id"],
                sample_name=sample_job_request["sample_name"],
                mode=sample_job_request["mode"],
                input_directory=sample_job_request["input_directory"],
                reference_genome=sample_job_request.get("reference_genome"),
                modifications=sample_job_request.get("modifications"),
            )
            
            assert job.status == "PENDING"
            
            # 2. Simulate job starting
            job = await update_job_status(session, run_uuid, JobStatus.RUNNING, progress=0)
            assert job.status == JobStatus.RUNNING
            
            # 3. Simulate job progress
            job = await update_job_status(session, run_uuid, JobStatus.RUNNING, progress=50)
            assert job.progress_percent == 50
            
            # 4. Simulate job completion
            job = await update_job_status(session, run_uuid, JobStatus.COMPLETED, progress=100)
            assert job.status == JobStatus.COMPLETED
            
        finally:
            await session.close()
    
    @pytest.mark.asyncio
    async def test_multi_mode_support(self):
        """Test that all three modes (DNA, RNA, CDNA) are supported."""
        from server3.nextflow_executor import NextflowConfig
        from server3.config import DogmeMode
        
        modes_tested = []
        
        for mode_enum in DogmeMode:
            config = NextflowConfig.generate_config(
                sample_name=f"test_{mode_enum.value}",
                mode=mode_enum.value,
                input_dir="/data/test",
            )
            # Config is now a string, so check for the mode in the string
            assert f"readType = '{mode_enum.value}'" in config
            assert "params {" in config
            assert "process {" in config
            modes_tested.append(mode_enum.value)
        
        assert "DNA" in modes_tested
        assert "RNA" in modes_tested
        assert "CDNA" in modes_tested

# --- EDGE CASE TESTS ---
class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_missing_input_directory(self):
        """Test that missing input directory raises error."""
        from server3.schemas import SubmitJobRequest
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            SubmitJobRequest(
                project_id="test",
                sample_name="test",
                mode="DNA",
                # Missing input_directory
            )
    
    def test_invalid_mode(self):
        """Test invalid Dogme mode."""
        from server3.schemas import SubmitJobRequest
        
        # This should work - validation happens in business logic
        req = SubmitJobRequest(
            project_id="test",
            sample_name="test",
            mode="INVALID_MODE",  # Invalid but schema accepts any string
            input_directory="/data/test",
        )
        
        # Actual validation would happen at execution time
        assert req.mode == "INVALID_MODE"
    
    @pytest.mark.asyncio
    async def test_nonexistent_job_retrieval(self):
        """Test retrieving a nonexistent job."""
        from server3.db import SessionLocal, init_db, get_job
        
        await init_db()
        session = SessionLocal()
        
        try:
            job = await get_job(session, "nonexistent_uuid")
            assert job is None
        finally:
            await session.close()

# --- CONFIGURATION TESTS ---
class TestConfiguration:
    """Test configuration loading and defaults."""
    
    def test_config_loading(self):
        """Test that config loads with defaults."""
        from server3 import config
        
        assert config.MAX_CONCURRENT_JOBS > 0
        assert config.JOB_POLL_INTERVAL > 0
        assert config.JOB_TIMEOUT > 0
        assert "DNA" in [m.value for m in config.DogmeMode]
    
    def test_reference_genome_config(self):
        """Test reference genome configuration."""
        from server3.config import REFERENCE_GENOMES
        
        assert "GRCh38" in REFERENCE_GENOMES
        assert "mm39" in REFERENCE_GENOMES
        assert "default" in REFERENCE_GENOMES
        
        grch38 = REFERENCE_GENOMES["GRCh38"]
        assert "fasta" in grch38
        assert "gtf" in grch38

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
