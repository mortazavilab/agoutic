from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

import common.database as common_db


ROOT = Path(__file__).resolve().parents[2]


def _reset_database_state() -> None:
    common_db.reset_engines()
    common_db.SessionLocal.reset()
    common_db.AsyncSessionLocal.reset()


def test_workflow_key_migration_backfills_existing_dogme_rows(monkeypatch, tmp_path):
    db_path = tmp_path / "wf-pore-c-backfill.sqlite"
    db_url = f"sqlite:///{db_path}"

    monkeypatch.setattr(common_db, "DATABASE_URL", db_url)
    _reset_database_state()

    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    engine = None

    try:
        command.upgrade(alembic_cfg, "0f3c7d9b2a11")
        engine = create_engine(db_url)

        with engine.begin() as conn:
            columns = {
                row[1]: row[3]
                for row in conn.execute(text("PRAGMA table_info(dogme_jobs)"))
            }
            assert "workflow_key" not in columns
            assert columns["mode"] == 1

            conn.execute(
                text(
                    """
                    INSERT INTO dogme_jobs (
                        run_uuid,
                        project_id,
                        sample_name,
                        mode,
                        input_directory,
                        status,
                        progress_percent
                    ) VALUES (
                        :run_uuid,
                        :project_id,
                        :sample_name,
                        :mode,
                        :input_directory,
                        :status,
                        :progress_percent
                    )
                    """
                ),
                [
                    {
                        "run_uuid": "run-dna",
                        "project_id": "proj-1",
                        "sample_name": "sample-dna",
                        "mode": "DNA",
                        "input_directory": "/tmp/input-dna",
                        "status": "PENDING",
                        "progress_percent": 0,
                    },
                    {
                        "run_uuid": "run-rna",
                        "project_id": "proj-1",
                        "sample_name": "sample-rna",
                        "mode": "RNA",
                        "input_directory": "/tmp/input-rna",
                        "status": "RUNNING",
                        "progress_percent": 25,
                    },
                ],
            )

        command.upgrade(alembic_cfg, "8c5f4d3e2b1a")

        with engine.connect() as conn:
            columns = {
                row[1]: row[3]
                for row in conn.execute(text("PRAGMA table_info(dogme_jobs)"))
            }
            assert "workflow_key" in columns
            assert columns["workflow_key"] == 1
            assert columns["mode"] == 0

            rows = conn.execute(
                text(
                    "SELECT run_uuid, workflow_key, mode FROM dogme_jobs ORDER BY run_uuid"
                )
            ).mappings().all()

        assert rows == [
            {"run_uuid": "run-dna", "workflow_key": "dogme", "mode": "DNA"},
            {"run_uuid": "run-rna", "workflow_key": "dogme", "mode": "RNA"},
        ]
    finally:
        if engine is not None:
            engine.dispose()
        _reset_database_state()