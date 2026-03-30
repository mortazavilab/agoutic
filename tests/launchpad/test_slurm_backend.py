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


@pytest.mark.asyncio
async def test_reuse_pre_staged_input_raises_when_remote_path_missing():
    backend = SlurmBackend()
    params = SubmitParams(
        staged_remote_input_path="/remote/agoutic/data/fingerprint123456",
        reference_cache_path="/remote/agoutic/ref/mm39",
        reference_genome=["mm39"],
    )
    fake_conn = _FakeStageConn(existing_paths=set())

    with pytest.raises(FileNotFoundError, match="no longer exists"):
        await backend._reuse_pre_staged_input("run-1", params, conn=fake_conn)


@pytest.mark.asyncio
async def test_reuse_pre_staged_input_removes_symlink_cache_and_raises():
    backend = SlurmBackend()
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
        await backend._reuse_pre_staged_input("run-1", params, conn=fake_conn)

    assert (f"rm -rf {remote_input}", True) in fake_conn.run_calls


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


@pytest.mark.asyncio
async def test_copy_selected_results_accepts_verified_local_artifacts_after_rsync_warning(monkeypatch, tmp_path):
    backend = SlurmBackend()
    job = SimpleNamespace(
        nextflow_work_dir=str(tmp_path / "workflow1"),
        remote_work_dir="/remote/workflow1",
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

    async def fake_update(run_uuid, state):
        states.append(state)

    async def fake_discover(**kwargs):
        return {"directories": ["annot"], "files": ["Jamshid4_trace.txt"]}

    async def fake_download_outputs(**kwargs):
        (local_root / "annot").mkdir(parents=True, exist_ok=True)
        (local_root / "Jamshid4_trace.txt").write_text("trace")
        return {"ok": False, "message": "download failed: rsync exited 23", "bytes_transferred": 123}

    monkeypatch.setattr(backend, "_update_job_transfer_state", fake_update)
    monkeypatch.setattr(backend, "_discover_remote_result_artifacts", fake_discover)
    monkeypatch.setattr(backend._transfer_manager, "download_outputs", fake_download_outputs)

    await backend._copy_selected_results_to_local(run_uuid="run-1", job=job, profile=profile)

    assert states == ["downloading_outputs", "outputs_downloaded"]


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

    assert result["success"] is True
    assert result["status"] == "outputs_downloaded"
    assert result["transfer_state"] == "outputs_downloaded"


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
        }
    ]
    assert (f"rm -rf {data_cache_path}", True) in fake_conn.run_calls
