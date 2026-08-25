"""Batch DOGME parameter extraction tests."""

import datetime
import uuid
from types import SimpleNamespace

from cortex.models import Project, ProjectAccess, User
from cortex.plan_params import _extract_plan_params, resolve_reconcile_project_workflow_paths


def test_extract_dogme_batch_sample_pairs_and_shared_settings():
    params = _extract_plan_params(
        "Run DOGME DNA on tumor: /data/tumor and normal=/data/normal for GRCh38 on SLURM with parallelism 2",
        SimpleNamespace(sample_name=None, work_dir=None),
        "run_dogme_batch",
    )

    assert params["batch_samples"] == [
        {"sample_id": "1", "sample_name": "tumor", "input_directory": "/data/tumor"},
        {"sample_id": "2", "sample_name": "normal", "input_directory": "/data/normal"},
    ]
    assert params["shared_params"] == {
        "mode": "DNA",
        "reference_genome": ["GRCh38"],
        "execution_mode": "slurm",
    }
    assert params["requested_max_parallel"] == 2


def test_extract_cdna_fastq_batch_sets_fastq_entry_point():
    params = _extract_plan_params(
        "Run DOGME cDNA on sample-a: /data/a.fastq.gz and sample-b=/data/b.fq with parallelism 2",
        SimpleNamespace(sample_name=None, work_dir=None),
        "run_dogme_batch",
    )

    assert params["shared_params"] == {
        "mode": "CDNA",
        "input_type": "fastq",
        "entry_point": "fastqCDNA",
    }


def test_extract_cdna_fastq_batch_preserves_hpc3_as_shared_slurm_target():
    params = _extract_plan_params(
        "Run DOGME cDNA on hpc3 for GRCh38 with sample-a: /data/a.fastq.gz and sample-b: /data/b.fastq.gz",
        SimpleNamespace(sample_name=None, work_dir=None),
        "run_dogme_batch",
    )

    assert params["shared_params"] == {
        "mode": "CDNA",
        "input_type": "fastq",
        "entry_point": "fastqCDNA",
        "reference_genome": ["GRCh38"],
        "execution_mode": "slurm",
        "ssh_profile_nickname": "hpc3",
    }


def test_resolve_reconcile_paths_uses_shared_project_owner_directory(db_session, tmp_agoutic_data):
    now = datetime.datetime.utcnow()
    requester = User(id="user-requester", email="requester@example.com", username="requester", role="user")
    owner = User(id="user-owner", email="owner@example.com", username="project-owner", role="user")
    owned_project = Project(id="project-owned", name="Owned", slug="owned-project", owner_id=requester.id)
    shared_project = Project(id="project-shared", name="Shared", slug="shared-project", owner_id=owner.id)
    db_session.add_all([requester, owner, owned_project, shared_project])
    db_session.add(ProjectAccess(
        id=str(uuid.uuid4()),
        user_id=requester.id,
        project_id=shared_project.id,
        project_name=shared_project.name,
        role="viewer",
        created_at=now,
        updated_at=now,
        last_accessed=now,
    ))
    db_session.commit()

    paths = resolve_reconcile_project_workflow_paths(
        "reconcile owned-project:workflow2 and shared-project:workflow7",
        db_session,
        requester,
    )

    assert paths == {
        ("owned-project", "workflow2"): str(tmp_agoutic_data / "users" / "requester" / "owned-project" / "workflow2"),
        ("shared-project", "workflow7"): str(tmp_agoutic_data / "users" / "project-owner" / "shared-project" / "workflow7"),
    }


def test_resolve_reconcile_paths_owner_qualifies_duplicate_project_slug(db_session, tmp_agoutic_data):
    now = datetime.datetime.utcnow()
    requester = User(id="user-requester", email="requester@example.com", username="requester", role="user")
    owner = User(id="user-owner", email="owner@example.com", username="shared-owner", role="user")
    owned_project = Project(id="project-owned", name="Analysis", slug="analysis", owner_id=requester.id)
    shared_project = Project(id="project-shared", name="Analysis", slug="analysis", owner_id=owner.id)
    db_session.add_all([requester, owner, owned_project, shared_project])
    db_session.add(ProjectAccess(
        id=str(uuid.uuid4()),
        user_id=requester.id,
        project_id=shared_project.id,
        project_name=shared_project.name,
        role="viewer",
        created_at=now,
        updated_at=now,
        last_accessed=now,
    ))
    db_session.commit()

    paths = resolve_reconcile_project_workflow_paths(
        "reconcile requester:analysis:workflow2 and shared-owner:analysis:workflow7",
        db_session,
        requester,
    )

    assert paths == {
        ("requester", "analysis", "workflow2"): str(tmp_agoutic_data / "users" / "requester" / "analysis" / "workflow2"),
        ("shared-owner", "analysis", "workflow7"): str(tmp_agoutic_data / "users" / "shared-owner" / "analysis" / "workflow7"),
    }
