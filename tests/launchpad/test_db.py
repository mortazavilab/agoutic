"""Tests for launchpad/db.py."""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from launchpad.db import (
    add_log_entry,
    create_job,
    get_job,
    get_job_logs,
    get_remote_input_cache_entry,
    get_remote_reference_cache_entry,
    job_to_dict,
    upsert_remote_input_cache_entry,
    upsert_remote_reference_cache_entry,
    update_job_status,
)
from common.database import Base
from launchpad.models import DogmeJob, JobLog


@pytest.fixture()
async def async_session(tmp_path):
    db_path = tmp_path / "launchpad.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        yield session

    await engine.dispose()


class TestCreateJob:
    @pytest.mark.asyncio
    async def test_serializes_reference_genome_lists(self, async_session):
        job = await create_job(
            session=async_session,
            run_uuid="run-1",
            project_id="proj-1",
            sample_name="sample-a",
            mode="DNA",
            input_directory="/data/input",
            reference_genome=["GRCh38", "mm39"],
            modifications="5mC",
            parent_block_id="block-1",
            user_id="user-1",
        )

        assert job.run_uuid == "run-1"
        assert job.reference_genome == json.dumps(["GRCh38", "mm39"])
        loaded = await get_job(async_session, "run-1")
        assert loaded is not None
        assert loaded.parent_block_id == "block-1"
        assert loaded.user_id == "user-1"

    @pytest.mark.asyncio
    async def test_preserves_scalar_reference_genome(self, async_session):
        job = await create_job(
            session=async_session,
            run_uuid="run-2",
            project_id="proj-2",
            sample_name="sample-b",
            mode="RNA",
            input_directory="/data/input",
            reference_genome="GRCh38",
        )

        assert job.reference_genome == "GRCh38"


class TestUpdateJobStatus:
    @pytest.mark.asyncio
    async def test_updates_status_progress_and_error_message(self, async_session):
        await create_job(
            session=async_session,
            run_uuid="run-3",
            project_id="proj-3",
            sample_name="sample-c",
            mode="DNA",
            input_directory="/data/input",
        )

        updated = await update_job_status(
            session=async_session,
            run_uuid="run-3",
            status="FAILED",
            progress=87,
            error_message="pipeline crashed",
        )

        assert updated is not None
        assert updated.status == "FAILED"
        assert updated.progress_percent == 87
        assert updated.error_message == "pipeline crashed"

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_job(self, async_session):
        updated = await update_job_status(
            session=async_session,
            run_uuid="missing-run",
            status="COMPLETED",
        )

        assert updated is None


class TestJobToDict:
    def test_converts_dates_and_report_json(self):
        submitted = datetime(2026, 3, 7, 12, 0, 0)
        started = submitted + timedelta(minutes=5)
        completed = started + timedelta(hours=1)

        job = DogmeJob(
            run_uuid="run-4",
            project_id="proj-4",
            sample_name="sample-d",
            mode="CDNA",
            input_directory="/data/input",
            reference_genome='["GRCh38", "mm39"]',
            modifications=None,
            status="COMPLETED",
            progress_percent=100,
            submitted_at=submitted,
            started_at=started,
            completed_at=completed,
            output_directory="/data/output",
            report_json=json.dumps({"files": 3, "status": "ok"}),
            error_message=None,
        )

        result = job_to_dict(job)

        assert result["run_uuid"] == "run-4"
        assert result["submitted_at"] == submitted.isoformat()
        assert result["started_at"] == started.isoformat()
        assert result["completed_at"] == completed.isoformat()
        assert result["report"] == {"files": 3, "status": "ok"}
        assert result["reference_genome"] == '["GRCh38", "mm39"]'


class TestLogHelpers:
    @pytest.mark.asyncio
    async def test_add_log_entry_persists_log(self, async_session):
        log = await add_log_entry(
            session=async_session,
            run_uuid="run-5",
            level="INFO",
            message="started",
            source="nextflow",
        )

        assert log.id is not None
        logs = await get_job_logs(async_session, "run-5")
        assert len(logs) == 1
        assert logs[0]["message"] == "started"
        assert logs[0]["source"] == "nextflow"

    @pytest.mark.asyncio
    async def test_get_job_logs_orders_ascending_and_applies_limit(self, async_session):
        async_session.add_all(
            [
                JobLog(
                    run_uuid="run-6",
                    level="INFO",
                    message="third",
                    source="worker",
                    timestamp=datetime(2026, 3, 7, 12, 3, 0),
                ),
                JobLog(
                    run_uuid="run-6",
                    level="INFO",
                    message="first",
                    source="worker",
                    timestamp=datetime(2026, 3, 7, 12, 1, 0),
                ),
                JobLog(
                    run_uuid="run-6",
                    level="INFO",
                    message="second",
                    source="worker",
                    timestamp=datetime(2026, 3, 7, 12, 2, 0),
                ),
            ]
        )
        await async_session.commit()

        logs = await get_job_logs(async_session, "run-6", limit=2)

        assert [log["message"] for log in logs] == ["first", "second"]


class TestRemoteCacheHelpers:
    @pytest.mark.asyncio
    async def test_upsert_and_get_remote_reference_cache_entry(self):
        await upsert_remote_reference_cache_entry(
            user_id="user-1",
            ssh_profile_id="profile-1",
            reference_id="mm39",
            source_signature="sig-1",
            source_uri="/refs/mm39",
            remote_path="/scratch/u1/cache/references/mm39",
            status="READY",
            increment_use_count=True,
        )

        entry = await get_remote_reference_cache_entry("user-1", "profile-1", "mm39")

        assert entry is not None
        assert entry.remote_path.endswith("/references/mm39")
        assert entry.use_count >= 1

    @pytest.mark.asyncio
    async def test_upsert_and_get_remote_input_cache_entry(self):
        await upsert_remote_input_cache_entry(
            user_id="user-1",
            ssh_profile_id="profile-1",
            reference_id="mm39",
            input_fingerprint="abc123",
            remote_path="/scratch/u1/cache/data/mm39/abc123",
            status="READY",
            increment_use_count=True,
        )

        entry = await get_remote_input_cache_entry("user-1", "profile-1", "mm39", "abc123")

        assert entry is not None
        assert entry.remote_path.endswith("/data/mm39/abc123")
        assert entry.use_count >= 1
