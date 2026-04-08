import pytest
from types import SimpleNamespace
from pathlib import Path

from launchpad.backends.slurm_backend import SlurmBackend
from launchpad.backends.base import SubmitParams
from launchpad.backends.ssh_manager import SSHProfileData


class _FakeConn:
    def __init__(self):
        self.paths = []
        self.closed = False

    async def list_dir(self, path):
        self.paths.append(path)
        return [{"name": "data", "type": "dir", "size": 0}]

    async def close(self):
        self.closed = True


class _FakeTaskStatusConn:
    def __init__(self, responses):
        self._responses = list(responses)
        self.run_calls = []

    async def run(self, command, check=False):
        self.run_calls.append((command, check))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeWriteConn:
    def __init__(self):
        self.mkdir_calls = []
        self.run_calls = []

    async def mkdir_p(self, path):
        self.mkdir_calls.append(path)

    async def run(self, command, check=False):
        self.run_calls.append((command, check))


@pytest.mark.asyncio
async def test_ensure_workflow_input_links_uses_unmapped_alias_for_bam_remap():
    backend = SlurmBackend()
    params = SubmitParams(
        sample_name="C2C12r1",
        input_type="bam",
        entry_point="remap",
    )
    conn = _FakeWriteConn()

    await backend._ensure_workflow_input_links(
        conn=conn,
        params=params,
        remote_work="/remote/project/workflow1",
        remote_input="/remote/cache/fingerprint1234",
    )

    commands = [command for command, _ in conn.run_calls]
    assert any("rm -rf /remote/project/workflow1/bams" in command for command in commands)
    assert any("mkdir -p /remote/project/workflow1/bams" in command for command in commands)
    assert any("c2c12r1.unmapped.bam" in command for command in commands)


@pytest.mark.asyncio
async def test_ensure_workflow_input_links_preserves_default_linking_for_non_remap_bam():
    backend = SlurmBackend()
    params = SubmitParams(
        sample_name="C2C12r1",
        input_type="bam",
        entry_point="annotateRNA",
    )
    conn = _FakeWriteConn()

    await backend._ensure_workflow_input_links(
        conn=conn,
        params=params,
        remote_work="/remote/project/workflow1",
        remote_input="/remote/cache/fingerprint1234",
    )

    assert conn.run_calls == [
        ("ln -sfn /remote/cache/fingerprint1234 /remote/project/workflow1/bams", True),
    ]


class _FakeStageConn:
    def __init__(self, *, existing_paths=None, listings=None):
        self.existing_paths = set(existing_paths or [])
        self.listings = listings or {}
        self.mkdir_calls = []
        self.run_calls = []

    async def path_exists(self, path):
        return path in self.existing_paths

    async def list_dir(self, path):
        return self.listings.get(path, [])

    async def mkdir_p(self, path):
        self.mkdir_calls.append(path)
        self.existing_paths.add(path)

    async def run(self, command, check=False):
        self.run_calls.append((command, check))
        return SimpleNamespace(stdout="", stderr="", exit_status=0)


def _make_profile() -> SSHProfileData:
    return SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )


@pytest.mark.asyncio
async def test_reuse_pre_staged_input_raises_when_remote_path_missing():
    backend = SlurmBackend()
    profile = _make_profile()
    params = SubmitParams(
        staged_remote_input_path="/remote/agoutic/data/fingerprint123456",
        reference_cache_path="/remote/agoutic/ref/mm39",
        reference_genome=["mm39"],
    )
    fake_conn = _FakeStageConn(existing_paths=set())

    with pytest.raises(FileNotFoundError, match="no longer exists"):
        await backend._reuse_pre_staged_input("run-1", params, profile=profile, conn=fake_conn)


@pytest.mark.asyncio
async def test_reuse_pre_staged_input_removes_symlink_cache_and_raises():
    backend = SlurmBackend()
    profile = _make_profile()
    remote_input = "/remote/agoutic/data/fingerprint123456"
    params = SubmitParams(
        staged_remote_input_path=remote_input,
        reference_cache_path="/remote/agoutic/ref/mm39",
        reference_genome=["mm39"],
    )
    fake_conn = _FakeStageConn(
        existing_paths={remote_input},
        listings={
            remote_input: [
                {"name": "ENCFF921XAH.bam", "type": "symlink", "size": 72},
            ]
        },
    )

    with pytest.raises(RuntimeError, match="contained symlinks"):
        await backend._reuse_pre_staged_input("run-1", params, profile=profile, conn=fake_conn)

    assert (f"rm -rf {remote_input}", True) in fake_conn.run_calls


@pytest.mark.asyncio
async def test_detect_remote_input_type_uses_remote_listing():
    backend = SlurmBackend()
    fake_conn = _FakeStageConn(
        existing_paths={"/remote/agoutic/incoming/Jamshid"},
        listings={
            "/remote/agoutic/incoming/Jamshid": [
                {"name": "Jamshid_rep1.fastq", "type": "file", "size": 100},
            ]
        },
    )

    detected = await backend._detect_remote_input_type(
        fake_conn,
        "/remote/agoutic/incoming/Jamshid",
        fallback="pod5",
    )

    assert detected == "fastq"


@pytest.mark.asyncio
async def test_reuse_pre_staged_input_registers_user_remote_folder_without_removing_symlinks(monkeypatch):
    backend = SlurmBackend()
    profile = _make_profile()
    remote_input = "/remote/agoutic/incoming/Jamshid"
    params = SubmitParams(
        user_id="user-1",
        sample_name="Jamshid",
        mode="DNA",
        input_type="pod5",
        remote_base_path="/remote/agoutic",
        remote_input_path=remote_input,
        staged_remote_input_path=remote_input,
        cache_preflight={
            "data_action": {"action": "use_remote_path", "cache_path": remote_input},
            "reference_actions": [{"reference_id": "mm39", "cache_path": "/remote/agoutic/ref/mm39"}],
        },
        reference_genome=["mm39"],
    )
    fake_conn = _FakeStageConn(
        existing_paths={remote_input},
        listings={
            remote_input: [
                {"name": "sample1.bam", "type": "file", "size": 42},
                {"name": "shared_link", "type": "symlink", "size": 0},
            ]
        },
    )
    registered = {}

    async def fake_upsert_remote_staged_sample(**kwargs):
        registered.update(kwargs)
        return None

    monkeypatch.setattr("launchpad.db.upsert_remote_staged_sample", fake_upsert_remote_staged_sample)

    result = await backend._reuse_pre_staged_input(None, params, profile=profile, conn=fake_conn)

    assert result["remote_input"] == remote_input
    assert result["detected_input_type"] == "bam"
    assert registered["source_path"] == remote_input
    assert registered["remote_data_path"] == remote_input
    assert registered["remote_reference_paths"] == {"mm39": "/remote/agoutic/ref/mm39"}
    assert not any(command.startswith("rm -rf") for command, _ in fake_conn.run_calls)


@pytest.mark.asyncio
async def test_list_remote_files_resolves_relative_path_under_remote_base(monkeypatch):
    backend = SlurmBackend()
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    fake_conn = _FakeConn()

    async def fake_load_profile(profile_id, user_id):
        return profile

    async def fake_connect(profile_obj):
        assert profile_obj is profile
        return fake_conn

    monkeypatch.setattr(backend, "_load_profile", fake_load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", fake_connect)

    result = await backend.list_remote_files(
        ssh_profile_id="profile-1",
        user_id="user-1",
        path="data",
    )

    assert fake_conn.paths == ["/remote/agoutic/data"]
    assert result["path"] == "/remote/agoutic/data"
    assert fake_conn.closed is True


@pytest.mark.asyncio
async def test_list_remote_files_preserves_absolute_path(monkeypatch):
    backend = SlurmBackend()
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    fake_conn = _FakeConn()

    async def fake_load_profile(profile_id, user_id):
        return profile

    async def fake_connect(profile_obj):
        return fake_conn

    monkeypatch.setattr(backend, "_load_profile", fake_load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", fake_connect)

    result = await backend.list_remote_files(
        ssh_profile_id="profile-1",
        user_id="user-1",
        path="/scratch/shared",
    )

    assert fake_conn.paths == ["/scratch/shared"]
    assert result["path"] == "/scratch/shared"


def test_resolve_remote_browse_path_rejects_parent_traversal():
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )

    with pytest.raises(ValueError, match="cannot contain '..'"):
        SlurmBackend._resolve_remote_browse_path(profile, "../other")


def test_result_sync_include_patterns_cover_required_folders_and_file_types():
    patterns = SlurmBackend._build_result_sync_include_patterns()

    assert "annot/***" in patterns
    assert "bams/***" in patterns
    assert "bedMethyl/***" in patterns
    assert "kallisto/***" in patterns
    assert "openChromatin/***" in patterns
    assert "stats/***" in patterns
    assert "*.config" in patterns
    assert "*.html" in patterns
    assert "*.txt" in patterns
    assert "*.csv" in patterns
    assert "*.tsv" in patterns


def test_needs_local_result_copy_only_for_local_or_both_destinations():
    remote_only = SimpleNamespace(result_destination="remote", remote_work_dir="/remote/workflow2", nextflow_work_dir="/local/workflow2")
    local_dest = SimpleNamespace(result_destination="local", remote_work_dir="/remote/workflow2", nextflow_work_dir="/local/workflow2")
    both_dest = SimpleNamespace(result_destination="both", remote_work_dir="/remote/workflow2", nextflow_work_dir="/local/workflow2")
    missing_local = SimpleNamespace(result_destination="local", remote_work_dir="/remote/workflow2", nextflow_work_dir=None)

    assert SlurmBackend._needs_local_result_copy(remote_only) is False
    assert SlurmBackend._needs_local_result_copy(local_dest) is True
    assert SlurmBackend._needs_local_result_copy(both_dest) is True
    assert SlurmBackend._needs_local_result_copy(missing_local) is False


def test_parse_task_status_texts_uses_stdout_progress_hint_when_trace_missing():
    stdout = "\n".join(
        [
            "executor > slurm (729)",
            "[bf/6ad31d] mainWorkflow:doradoTask(520) | 417 of 729",
            "[80/9041d1] mainWorkflow:softwareVTask | 1 of 1 ✔",
        ]
    )

    progress, tasks, message = SlurmBackend._parse_task_status_texts(
        trace_content="",
        stdout_content=stdout,
        scheduler_status="RUNNING",
    )

    assert progress == int((417 / 729) * 90)
    assert tasks["total"] == 730
    assert tasks["completed_count"] == 418
    assert tasks["remaining_count"] == 312
    assert message == "Pipeline: 418/730 completed, 312 remaining"


@pytest.mark.asyncio
async def test_read_remote_task_status_falls_back_to_stdout_when_trace_read_fails():
    backend = SlurmBackend()
    fake_conn = _FakeTaskStatusConn(
        responses=[
            RuntimeError("Separator is found, but chunk is longer than limit"),
            SimpleNamespace(
                stdout="executor > slurm (729)\n[bf/6ad31d] mainWorkflow:doradoTask(520) | 417 of 729\n",
                stderr="",
                exit_status=0,
            ),
        ]
    )

    progress, tasks, message = await backend._read_remote_task_status(
        conn=fake_conn,
        remote_work="/remote/workflow9",
        slurm_job_id="12345",
        scheduler_status="RUNNING",
    )

    assert len(fake_conn.run_calls) == 2
    assert fake_conn.run_calls[0][0].startswith("tail -n 5000 ")
    assert fake_conn.run_calls[1][0] == "tail -n 500 /remote/workflow9/slurm-12345.out 2>/dev/null || true"
    assert progress == int((417 / 729) * 90)
    assert tasks["completed_count"] == 417
    assert tasks["total"] == 729
    assert tasks["remaining_count"] == 312
    assert message == "Pipeline: 417/729 completed, 312 remaining"


def test_parse_task_status_texts_adds_prior_completed_families_to_pipeline_total():
    progress, tasks, message = SlurmBackend._parse_task_status_texts(
        trace_content=(
            "task_id\thash\tnative_id\tname\tstatus\texit\n"
            "1\t71/22d996\t50052915\tmainWorkflow:doradoDownloadTask\tCOMPLETED\t0\n"
            "2\td0/606400\t50052916\tmainWorkflow:softwareVTask\tCOMPLETED\t0\n"
        ),
        stdout_content=(
            "executor >  slurm (554)\n"
            "[d1/5209d5] mainWorkflow:doradoTask (553) | 452 of 729\n"
        ),
        scheduler_status="RUNNING",
    )

    assert progress == int((454 / 731) * 90)
    assert tasks["completed_count"] == 454
    assert tasks["total"] == 731
    assert tasks["remaining_count"] == 277
    assert message == "Pipeline: 454/731 completed, 277 remaining"


def test_parse_scheduler_queue_text_counts_running_and_pending_jobs():
    counts = SlurmBackend._parse_scheduler_queue_text(
        queue_output=(
            "50052939|RUNNING|agoutic-sample-abc123|/remote/workflow5\n"
            "60000001|RUNNING|nf-mainW|/remote/workflow5/work/aa/bb\n"
            "60000002|PENDING|nf-mainW|/remote/workflow5/work/cc/dd\n"
            "60000003|COMPLETING|nf-mainW|/remote/workflow5/work/ee/ff\n"
            "60000004|RUNNING|manual-copy|/remote/workflow5\n"
            "60000005|RUNNING|nf-mainW|/remote/other-workflow/work/gg/hh\n"
        ),
        parent_job_id="50052939",
        remote_work="/remote/workflow5",
    )

    assert counts == {
        "scheduler_running_count": 2,
        "scheduler_pending_count": 1,
    }


def test_parse_scheduler_queue_text_accepts_short_slurm_states():
    counts = SlurmBackend._parse_scheduler_queue_text(
        queue_output=(
            "50052939|R|nf-mainW|/remote/workflow9/work/aa/bb\n"
            "50052940|PD|nf-mainW|/remote/workflow9/work/cc/dd\n"
            "50052941|CG|nf-mainW|/remote/workflow9/work/ee/ff\n"
        ),
        parent_job_id="99999999",
        remote_work="/remote/workflow9",
    )

    assert counts == {
        "scheduler_running_count": 2,
        "scheduler_pending_count": 1,
    }


@pytest.mark.asyncio
async def test_copy_selected_results_fails_fast_on_rsync_error(monkeypatch, tmp_path):
    """When rsync reports failure, copy-back should fail immediately with the rsync error."""
    backend = SlurmBackend()
    job = SimpleNamespace(
        nextflow_work_dir=str(tmp_path / "workflow1"),
        remote_work_dir="/remote/workflow1",
        input_directory=str(tmp_path / "input"),
    )
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    states = []
    local_root = Path(job.nextflow_work_dir)
    local_root.mkdir(parents=True, exist_ok=True)

    async def fake_update(run_uuid, state, **kwargs):
        states.append(state)

    async def fake_discover(**kwargs):
        return {"directories": ["annot"], "files": ["Jamshid4_trace.txt"]}

    download_call_count = 0

    async def fake_download_outputs(**kwargs):
        nonlocal download_call_count
        download_call_count += 1
        # First call is for the annot directory — report failure
        (local_root / "annot").mkdir(parents=True, exist_ok=True)
        (local_root / "Jamshid4_trace.txt").write_text("trace")
        return {"ok": False, "message": "download failed: rsync exited 23", "bytes_transferred": 123}

    monkeypatch.setattr(backend, "_update_job_transfer_state", fake_update)
    monkeypatch.setattr(backend, "_discover_remote_result_artifacts", fake_discover)
    monkeypatch.setattr(backend._transfer_manager, "download_outputs", fake_download_outputs)

    result = await backend._copy_selected_results_to_local(run_uuid="run-1", job=job, profile=profile)

    assert states == ["downloading_outputs", "transfer_failed"]
    assert result["success"] is False
    assert "rsync exited 23" in result["message"]


@pytest.mark.asyncio
async def test_copy_selected_results_uses_remote_output_dir_when_set(tmp_path, monkeypatch):
    """When the job has remote_output_dir set, discovery and rsync should use it directly."""
    backend = SlurmBackend()
    job = SimpleNamespace(
        nextflow_work_dir=str(tmp_path / "workflow1"),
        remote_work_dir="/remote/workflow1",
        remote_output_dir="/remote/workflow1/output",
        input_directory=str(tmp_path / "input"),
    )
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    local_root = Path(job.nextflow_work_dir)
    local_root.mkdir(parents=True, exist_ok=True)

    discover_kwargs_received = {}

    async def fake_discover(**kwargs):
        discover_kwargs_received.update(kwargs)
        return {"directories": ["bams"], "files": []}

    async def fake_update(run_uuid, state, **kwargs):
        pass

    async def fake_download_outputs(**kwargs):
        (local_root / "bams").mkdir(parents=True, exist_ok=True)
        return {"ok": True, "bytes_transferred": 42}

    monkeypatch.setattr(backend, "_update_job_transfer_state", fake_update)
    monkeypatch.setattr(backend, "_discover_remote_result_artifacts", fake_discover)
    monkeypatch.setattr(backend._transfer_manager, "download_outputs", fake_download_outputs)

    result = await backend._copy_selected_results_to_local(run_uuid="run-1", job=job, profile=profile)

    assert result["success"] is True
    # Discovery should have received remote_output_dir, not remote_work_dir
    assert discover_kwargs_received.get("remote_output_dir") == "/remote/workflow1/output"
    assert "remote_work_dir" not in discover_kwargs_received


@pytest.mark.asyncio
async def test_copy_selected_results_derives_output_dir_for_legacy_jobs(tmp_path, monkeypatch):
    """When remote_output_dir is None (pre-migration), derive it as remote_work_dir/output."""
    backend = SlurmBackend()
    job = SimpleNamespace(
        nextflow_work_dir=str(tmp_path / "workflow1"),
        remote_work_dir="/remote/workflow1",
        input_directory=str(tmp_path / "input"),
        # No remote_output_dir attribute — simulates pre-migration job
    )
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    local_root = Path(job.nextflow_work_dir)
    local_root.mkdir(parents=True, exist_ok=True)

    discover_kwargs_received = {}

    async def fake_discover(**kwargs):
        discover_kwargs_received.update(kwargs)
        return {"directories": ["stats"], "files": []}

    async def fake_update(run_uuid, state, **kwargs):
        pass

    async def fake_download_outputs(**kwargs):
        (local_root / "stats").mkdir(parents=True, exist_ok=True)
        return {"ok": True, "bytes_transferred": 10}

    monkeypatch.setattr(backend, "_update_job_transfer_state", fake_update)
    monkeypatch.setattr(backend, "_discover_remote_result_artifacts", fake_discover)
    monkeypatch.setattr(backend._transfer_manager, "download_outputs", fake_download_outputs)

    result = await backend._copy_selected_results_to_local(run_uuid="run-1", job=job, profile=profile)

    assert result["success"] is True
    # Should have derived remote_output_dir = remote_work_dir + "/output"
    assert discover_kwargs_received.get("remote_output_dir") == "/remote/workflow1/output"


@pytest.mark.asyncio
async def test_copy_selected_results_fails_on_empty_artifacts(tmp_path, monkeypatch):
    """When remote has no result artifacts at all, sync should fail instead of silently succeeding."""
    backend = SlurmBackend()
    job = SimpleNamespace(
        nextflow_work_dir=str(tmp_path / "workflow1"),
        remote_work_dir="/remote/workflow1",
        remote_output_dir="/remote/workflow1/output",
        input_directory=str(tmp_path / "input"),
    )
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    states = []

    async def fake_update(run_uuid, state, **kwargs):
        states.append(state)

    discover_calls = []

    async def fake_discover(**kwargs):
        discover_calls.append(kwargs.get("remote_output_dir"))
        return {"directories": [], "files": []}

    monkeypatch.setattr(backend, "_update_job_transfer_state", fake_update)
    monkeypatch.setattr(backend, "_discover_remote_result_artifacts", fake_discover)

    result = await backend._copy_selected_results_to_local(run_uuid="run-1", job=job, profile=profile)

    assert result["success"] is False
    assert "No result artifacts found" in result["message"]
    assert discover_calls == ["/remote/workflow1/output", "/remote/workflow1"]
    assert states == ["downloading_outputs", "transfer_failed"]


@pytest.mark.asyncio
async def test_copy_selected_results_falls_back_to_workflow_root_when_output_dir_empty(tmp_path, monkeypatch):
    backend = SlurmBackend()
    job = SimpleNamespace(
        nextflow_work_dir=str(tmp_path / "workflow1"),
        remote_work_dir="/remote/workflow1",
        remote_output_dir="/remote/workflow1/output",
        input_directory=str(tmp_path / "input"),
    )
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    local_root = Path(job.nextflow_work_dir)
    local_root.mkdir(parents=True, exist_ok=True)

    discover_calls = []
    download_calls = []

    async def fake_discover(**kwargs):
        remote_dir = kwargs.get("remote_output_dir")
        discover_calls.append(remote_dir)
        if remote_dir == "/remote/workflow1/output":
            return {"directories": [], "files": []}
        return {"directories": ["bams"], "files": ["run.txt"]}

    async def fake_update(run_uuid, state, **kwargs):
        return None

    async def fake_download_outputs(**kwargs):
        download_calls.append(kwargs.get("remote_path"))
        remote_path = kwargs.get("remote_path")
        if remote_path == "/remote/workflow1/bams":
            (local_root / "bams").mkdir(parents=True, exist_ok=True)
        elif remote_path == "/remote/workflow1":
            (local_root / "run.txt").write_text("ok")
        return {"ok": True, "bytes_transferred": 42}

    monkeypatch.setattr(backend, "_update_job_transfer_state", fake_update)
    monkeypatch.setattr(backend, "_discover_remote_result_artifacts", fake_discover)
    monkeypatch.setattr(backend._transfer_manager, "download_outputs", fake_download_outputs)

    result = await backend._copy_selected_results_to_local(run_uuid="run-1", job=job, profile=profile)

    assert result["success"] is True
    assert discover_calls == ["/remote/workflow1/output", "/remote/workflow1"]
    assert download_calls == ["/remote/workflow1/bams", "/remote/workflow1"]


@pytest.mark.asyncio
async def test_sync_results_to_local_returns_not_applicable_for_remote_only_destination(monkeypatch):
    backend = SlurmBackend()
    job = SimpleNamespace(
        result_destination="remote",
        remote_work_dir="/remote/workflow1",
        nextflow_work_dir="/local/workflow1",
        transfer_state="",
    )

    async def fake_get_job(_run_uuid):
        return job

    monkeypatch.setattr("launchpad.db.get_job_by_uuid", fake_get_job)

    result = await backend.sync_results_to_local(run_uuid="run-1")

    assert result["success"] is False
    assert result["status"] == "not_applicable"


@pytest.mark.asyncio
async def test_sync_results_to_local_retries_copy_when_force_enabled(monkeypatch):
    backend = SlurmBackend()
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    job = SimpleNamespace(
        result_destination="local",
        remote_work_dir="/remote/workflow1",
        nextflow_work_dir="/local/workflow1",
        transfer_state="outputs_downloaded",
        ssh_profile_id="profile-1",
        user_id="user-1",
    )
    refreshed = SimpleNamespace(transfer_state="outputs_downloaded")

    calls = {"count": 0}

    async def fake_get_job(_run_uuid):
        calls["count"] += 1
        return job if calls["count"] == 1 else refreshed

    async def fake_load_profile(_profile_id, _user_id):
        return profile

    async def fake_copy(*, run_uuid, job, profile):
        return {
            "success": True,
            "status": "outputs_downloaded",
            "message": "ok",
            "run_uuid": run_uuid,
            "remote_work_dir": job.remote_work_dir,
            "local_work_dir": job.nextflow_work_dir,
        }

    monkeypatch.setattr("launchpad.db.get_job_by_uuid", fake_get_job)
    monkeypatch.setattr(backend, "_load_profile", fake_load_profile)
    monkeypatch.setattr(backend, "_copy_selected_results_to_local", fake_copy)

    result = await backend.sync_results_to_local(run_uuid="run-1", force=True)

    # sync_results_to_local now fires a background task and returns immediately
    assert result["success"] is True
    assert result["status"] == "sync_started"
    assert result["transfer_state"] == "downloading_outputs"


@pytest.mark.asyncio
async def test_write_remote_nextflow_config_uses_profile_cpu_defaults_separately_from_gpu(monkeypatch):
    backend = SlurmBackend()
    params = SubmitParams(
        sample_name="sample-1",
        mode="RNA",
        input_directory="/remote/input",
        reference_genome=["mm39"],
        slurm_account="SEYEDAM_LAB_GPU",
        slurm_partition="gpu",
    )
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
        default_slurm_account="SEYEDAM_LAB",
        default_slurm_partition="standard",
        default_slurm_gpu_account="SEYEDAM_LAB_GPU",
        default_slurm_gpu_partition="gpu",
    )
    conn = _FakeWriteConn()
    captured = {}

    def fake_generate_config(**kwargs):
        captured.update(kwargs)
        return "process {}"

    monkeypatch.setattr("launchpad.backends.slurm_backend.NextflowConfig.generate_config", fake_generate_config)

    config_path = await backend._write_remote_nextflow_config(
        params=params,
        profile=profile,
        conn=conn,
        remote_work="/remote/agoutic/workflow1",
        cache_resolution={},
    )

    assert config_path == "/remote/agoutic/workflow1/nextflow.config"
    assert captured["slurm_cpu_account"] == "SEYEDAM_LAB"
    assert captured["slurm_cpu_partition"] == "standard"
    assert captured["slurm_gpu_account"] == "SEYEDAM_LAB_GPU"
    assert captured["slurm_gpu_partition"] == "gpu"
    assert captured["apptainer_cache_dir"] == "/remote/agoutic/.nxf-apptainer-cache"


@pytest.mark.asyncio
async def test_stage_sample_inputs_refreshes_remote_cache_when_top_level_symlink_is_present(monkeypatch):
    backend = SlurmBackend()
    profile = SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc3",
        ssh_host="example.org",
        ssh_port=22,
        ssh_username="alice",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        is_enabled=True,
        remote_base_path="/remote/agoutic",
    )
    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        sample_name="C2C12r1",
        mode="RNA",
        input_directory="data/ENCFF921XAH.bam",
        reference_genome=["mm39"],
    )
    data_cache_path = "/remote/agoutic/data/fingerprint123456"
    fake_conn = _FakeStageConn(
        existing_paths={data_cache_path},
        listings={
            data_cache_path: [
                {"name": "ENCFF921XAH.bam", "type": "symlink", "size": 72},
            ]
        },
    )
    uploaded = []

    async def fake_get_remote_reference_cache_entry(*_args, **_kwargs):
        return None

    async def fake_get_remote_input_cache_entry(*_args, **_kwargs):
        return SimpleNamespace(remote_path=data_cache_path)

    async def fake_upsert_remote_reference_cache_entry(**_kwargs):
        return None

    async def fake_upsert_remote_input_cache_entry(**_kwargs):
        return None

    async def fake_upsert_remote_staged_sample(**_kwargs):
        return None

    async def fake_upload_inputs(**kwargs):
        uploaded.append(kwargs)
        return {"ok": True, "message": "Upload completed", "bytes_transferred": 123}

    monkeypatch.setattr("launchpad.db.get_remote_reference_cache_entry", fake_get_remote_reference_cache_entry)
    monkeypatch.setattr("launchpad.db.get_remote_input_cache_entry", fake_get_remote_input_cache_entry)
    monkeypatch.setattr("launchpad.db.upsert_remote_reference_cache_entry", fake_upsert_remote_reference_cache_entry)
    monkeypatch.setattr("launchpad.db.upsert_remote_input_cache_entry", fake_upsert_remote_input_cache_entry)
    monkeypatch.setattr("launchpad.db.upsert_remote_staged_sample", fake_upsert_remote_staged_sample)
    monkeypatch.setattr(backend, "_resolve_reference_source_dir", lambda _ref: None)
    monkeypatch.setattr(backend, "_compute_input_fingerprint", lambda _path: "fingerprint1234567890")
    monkeypatch.setattr(backend._transfer_manager, "upload_inputs", fake_upload_inputs)

    result = await backend._stage_sample_inputs(
        params=params,
        profile=profile,
        conn=fake_conn,
        run_uuid=None,
    )

    assert result["data_cache_status"] == "refreshed"
    assert uploaded == [
        {
            "profile": profile,
            "local_path": "data/ENCFF921XAH.bam",
            "remote_path": data_cache_path,
            "on_progress": None,
        }
    ]
    assert (f"rm -rf {data_cache_path}", True) in fake_conn.run_calls


def test_resolve_local_symlinks_replaces_broken_with_local_match(tmp_path):
    """Broken remote symlinks should be replaced with links to matching local input files."""
    # Setup: local input directory with the actual file
    input_dir = tmp_path / "input" / "44b4ae3bfee1a768"
    input_dir.mkdir(parents=True)
    real_bam = input_dir / "ENCFF921XAH.bam"
    real_bam.write_text("BAM_DATA")

    # Setup: workflow dir with a broken symlink (as rsync would deliver)
    workflow_dir = tmp_path / "workflow1"
    bams_dir = workflow_dir / "bams"
    bams_dir.mkdir(parents=True)
    broken_link = bams_dir / "ENCFF921XAH.bam"
    broken_link.symlink_to("/share/crsp/lab/seyedam/share/agoutic/data/ENCFF921XAH.bam")
    assert not broken_link.exists()  # target doesn't exist → broken

    # Also place a valid regular file — should be left alone
    (bams_dir / "sample.bam").write_text("REAL")

    SlurmBackend._resolve_local_symlinks(
        local_work_dir=str(workflow_dir),
        input_directory=str(input_dir),
        directories=["bams"],
    )

    # Broken link should now point to the local input file
    assert broken_link.is_symlink()
    assert broken_link.exists()
    assert broken_link.resolve() == real_bam.resolve()
    # Regular file untouched
    assert (bams_dir / "sample.bam").read_text() == "REAL"


def test_resolve_local_symlinks_removes_broken_with_no_match(tmp_path):
    """Broken symlinks with no matching local file should be removed."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    workflow_dir = tmp_path / "workflow1"
    annot_dir = workflow_dir / "annot"
    annot_dir.mkdir(parents=True)
    broken_link = annot_dir / "mystery.bam"
    broken_link.symlink_to("/nonexistent/path/mystery.bam")

    SlurmBackend._resolve_local_symlinks(
        local_work_dir=str(workflow_dir),
        input_directory=str(input_dir),
        directories=["annot"],
    )

    assert not broken_link.exists()
    assert not broken_link.is_symlink()


def test_resolve_local_symlinks_skips_valid_symlinks(tmp_path):
    """Symlinks that resolve correctly should be left untouched."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    workflow_dir = tmp_path / "workflow1"
    bams_dir = workflow_dir / "bams"
    bams_dir.mkdir(parents=True)
    target = bams_dir / "real_target.bam"
    target.write_text("DATA")
    good_link = bams_dir / "alias.bam"
    good_link.symlink_to(target)

    SlurmBackend._resolve_local_symlinks(
        local_work_dir=str(workflow_dir),
        input_directory=str(input_dir),
        directories=["bams"],
    )

    assert good_link.is_symlink()
    assert good_link.exists()
    assert good_link.read_text() == "DATA"
