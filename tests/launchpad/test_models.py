"""
Tests for launchpad/models.py — DogmeJob and JobLog ORM models.
"""

import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from launchpad.models import DogmeJob, JobLog


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


class TestDogmeJob:
    def test_create_minimal(self, db):
        job = DogmeJob(
            run_uuid="test-uuid-1",
            project_id="proj-1",
            sample_name="sample1",
            mode="DNA",
            input_directory="/data/pod5",
            status="PENDING",
            progress_percent=0,
        )
        db.add(job)
        db.commit()

        loaded = db.get(DogmeJob, "test-uuid-1")
        assert loaded.sample_name == "sample1"
        assert loaded.mode == "DNA"
        assert loaded.status == "PENDING"
        assert loaded.workflow_index is None

    def test_full_fields(self, db):
        job = DogmeJob(
            run_uuid="test-uuid-2",
            project_id="proj-1",
            user_id="user-1",
            workflow_index=2,
            workflow_alias="workflow2",
            workflow_folder_name="workflow2",
            workflow_display_name="sample2-renamed",
            sample_name="sample2",
            mode="RNA",
            input_directory="/data/pod5",
            reference_genome="GRCh38",
            modifications="m6A",
            nextflow_work_dir="/work/dir",
            nextflow_config_path="/conf/nextflow.config",
            nextflow_process_id=12345,
            status="RUNNING",
            progress_percent=50,
            output_directory="/output",
            error_message=None,
            parent_block_id="blk-123",
        )
        db.add(job)
        db.commit()

        loaded = db.get(DogmeJob, "test-uuid-2")
        assert loaded.nextflow_process_id == 12345
        assert loaded.reference_genome == "GRCh38"
        assert loaded.workflow_alias == "workflow2"
        assert loaded.workflow_display_name == "sample2-renamed"

    def test_status_update(self, db):
        job = DogmeJob(
            run_uuid="test-uuid-3",
            project_id="proj-1",
            sample_name="s",
            mode="DNA",
            input_directory="/d",
            status="PENDING",
        )
        db.add(job)
        db.commit()

        job.status = "COMPLETED"
        job.progress_percent = 100
        job.completed_at = datetime.utcnow()
        db.commit()

        loaded = db.get(DogmeJob, "test-uuid-3")
        assert loaded.status == "COMPLETED"
        assert loaded.progress_percent == 100


class TestJobLog:
    def test_create_log(self, db):
        log = JobLog(
            run_uuid="job-1",
            level="INFO",
            message="Starting basecall",
            source="dorado",
        )
        db.add(log)
        db.commit()

        logs = db.query(JobLog).filter_by(run_uuid="job-1").all()
        assert len(logs) == 1
        assert logs[0].message == "Starting basecall"
        assert logs[0].source == "dorado"

    def test_multiple_logs(self, db):
        for i in range(5):
            db.add(JobLog(
                run_uuid="job-2",
                level="INFO" if i < 4 else "ERROR",
                message=f"Step {i}",
            ))
        db.commit()

        logs = db.query(JobLog).filter_by(run_uuid="job-2").all()
        assert len(logs) == 5
        error_logs = [l for l in logs if l.level == "ERROR"]
        assert len(error_logs) == 1
