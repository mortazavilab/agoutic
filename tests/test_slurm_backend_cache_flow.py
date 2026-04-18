"""Behavioral tests for SLURM cache flow (hit/miss/refresh/fallback)."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from launchpad.backends.base import SubmitParams
from launchpad.backends.slurm_backend import SlurmBackend
from launchpad.backends.ssh_manager import SSHProfileData
from launchpad.config import REFERENCE_GENOMES, DOGME_DNA_MODKITBASE, DOGME_DNA_MODKITMODEL


class _FakeConn:
    def __init__(self, existing_paths=None, dir_entries=None):
        self.existing_paths = set(existing_paths or [])
        self.dir_entries = dir_entries or {}
        self.commands = []

    async def path_exists(self, path: str) -> bool:
        return path in self.existing_paths

    async def mkdir_p(self, path: str) -> None:
        self.existing_paths.add(path)

    async def list_dir(self, path: str):
        return self.dir_entries.get(path, [])

    async def run(self, command: str, check: bool = False):
        self.commands.append(command)
        return SimpleNamespace(stdout="", stderr="", exit_status=0)

    async def run_checked(self, command: str) -> str:
        self.commands.append(command)
        if "sbatch --parsable" in command:
            return "12345\n"
        return ""

    async def close(self) -> None:
        return None


class _FakeStatusConn(_FakeConn):
    def __init__(self, sacct_output: str = "", squeue_output: str = "", trace_output: str = "", slurm_out_output: str = ""):
        super().__init__()
        self.sacct_output = sacct_output
        self.squeue_output = squeue_output
        self.trace_output = trace_output
        self.slurm_out_output = slurm_out_output

    async def run(self, command: str, check: bool = False):
        self.commands.append(command)
        if "sacct -j" in command:
            return SimpleNamespace(stdout=self.sacct_output, stderr="", exit_status=0)
        if "squeue -j" in command:
            return SimpleNamespace(stdout=self.squeue_output, stderr="", exit_status=0)
        if "tail -n 5000" in command and ("*_trace.txt" in command or "/trace.txt" in command):
            return SimpleNamespace(stdout=self.trace_output, stderr="", exit_status=0)
        if "tail -n 500" in command and "slurm-" in command and ".out" in command:
            return SimpleNamespace(stdout=self.slurm_out_output, stderr="", exit_status=0)
        return await super().run(command, check=check)


@pytest.fixture()
def profile() -> SSHProfileData:
    return SSHProfileData(
        id="profile-1",
        user_id="user-1",
        nickname="hpc",
        ssh_host="cluster.example.edu",
        ssh_port=22,
        ssh_username="eli",
        auth_method="ssh_agent",
        key_file_path=None,
        local_username=None,
        remote_base_path="/remote/eli/agoutic",
        is_enabled=True,
        default_slurm_account="cpu-default",
        default_slurm_partition="cpu-part-default",
        default_slurm_gpu_account="gpu-default",
        default_slurm_gpu_partition="gpu-part-default",
    )


@pytest.mark.asyncio
async def test_resolve_staging_cache_reuses_reference_and_data(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn(
        existing_paths={
            "/remote/eli/agoutic/ref/mm39",
            "/remote/eli/agoutic/data/fp1234567890abcd",
        }
    )

    params = SubmitParams(
        user_id="user-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        project_slug="project-a",
        workflow_number=1,
        remote_base_path="/remote/eli/agoutic",
    )

    monkeypatch.setattr(backend, "_resolve_reference_source_dir", lambda _: SimpleNamespace())

    async def _compute_directory_signature_async(_):
        return "sig-1"

    monkeypatch.setattr(backend, "_compute_directory_signature_async", _compute_directory_signature_async)
    monkeypatch.setattr(backend, "_compute_input_fingerprint", lambda _: "fp1234567890abcd")

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_transfer_state", _noop)

    upload_calls = []

    async def _upload_inputs(**kwargs):
        upload_calls.append(kwargs)
        return {"ok": True, "message": "ok", "bytes_transferred": 0}

    monkeypatch.setattr(backend._transfer_manager, "upload_inputs", _upload_inputs)

    from launchpad import db as launchpad_db

    async def _get_ref(*args, **kwargs):
        return SimpleNamespace(
            remote_path="/remote/eli/agoutic/ref/mm39",
            source_signature="sig-1",
            last_validated_at=None,
        )

    async def _get_data(*args, **kwargs):
        return SimpleNamespace(
            remote_path="/remote/eli/agoutic/data/fp1234567890abcd",
            last_used_at=None,
        )

    monkeypatch.setattr(launchpad_db, "get_remote_reference_cache_entry", _get_ref)
    monkeypatch.setattr(launchpad_db, "get_remote_input_cache_entry", _get_data)
    monkeypatch.setattr(launchpad_db, "upsert_remote_reference_cache_entry", _noop)
    monkeypatch.setattr(launchpad_db, "upsert_remote_input_cache_entry", _noop)
    monkeypatch.setattr(launchpad_db, "upsert_remote_staged_sample", _noop)
    monkeypatch.setattr(launchpad_db, "update_job_fields", _noop)

    result = await backend._resolve_staging_cache("run-1", params, profile, conn)

    assert result["reference_cache_status"] == "reused"
    assert result["data_cache_status"] == "reused"
    assert upload_calls == []


@pytest.mark.asyncio
async def test_resolve_staging_cache_refreshes_stale_reference(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn(existing_paths={"/remote/eli/agoutic/data/fpdeadbeefcafebabe"})

    params = SubmitParams(
        user_id="user-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        project_slug="project-a",
        workflow_number=1,
        remote_base_path="/remote/eli/agoutic",
    )

    monkeypatch.setattr(backend, "_resolve_reference_source_dir", lambda _: SimpleNamespace())

    async def _compute_directory_signature_async(_):
        return "new-sig"

    monkeypatch.setattr(backend, "_compute_directory_signature_async", _compute_directory_signature_async)
    monkeypatch.setattr(backend, "_compute_input_fingerprint", lambda _: "fpdeadbeefcafebabe")

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_transfer_state", _noop)

    upload_calls = []

    async def _upload_inputs(**kwargs):
        upload_calls.append(kwargs)
        return {"ok": True, "message": "ok", "bytes_transferred": 0}

    monkeypatch.setattr(backend._transfer_manager, "upload_inputs", _upload_inputs)

    from launchpad import db as launchpad_db

    async def _get_ref(*args, **kwargs):
        return SimpleNamespace(
            remote_path="/scratch/eli/agoutic/.agoutic_cache/user-1/profile-1/references/mm39",
            source_signature="old-sig",
            last_validated_at=None,
        )

    async def _get_data(*args, **kwargs):
        return None

    monkeypatch.setattr(launchpad_db, "get_remote_reference_cache_entry", _get_ref)
    monkeypatch.setattr(launchpad_db, "get_remote_input_cache_entry", _get_data)
    monkeypatch.setattr(launchpad_db, "upsert_remote_reference_cache_entry", _noop)
    monkeypatch.setattr(launchpad_db, "upsert_remote_input_cache_entry", _noop)
    monkeypatch.setattr(launchpad_db, "upsert_remote_staged_sample", _noop)
    monkeypatch.setattr(launchpad_db, "update_job_fields", _noop)

    result = await backend._resolve_staging_cache("run-2", params, profile, conn)

    assert result["reference_cache_status"] == "refreshed"
    assert result["data_cache_status"] == "staged"
    assert len(upload_calls) >= 2


@pytest.mark.anyio
async def test_submit_uses_fallback_when_cache_resolution_fails(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn()

    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        project_slug="proj-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        slurm_account="acc",
        slurm_partition="standard",
        workflow_number=1,
        remote_base_path="/remote/eli/agoutic",
    )

    async def _load_profile(*args, **kwargs):
        return profile

    async def _raise_cache(*args, **kwargs):
        raise RuntimeError("metadata unavailable")

    async def _fallback(*args, **kwargs):
        return {
            "remote_input": "/scratch/eli/agoutic/fallback/input",
            "reference_cache_status": "fallback",
            "data_cache_status": "fallback",
        }

    async def _noop(*args, **kwargs):
        return None

    async def _connect(*args, **kwargs):
        return conn

    async def _ensure_assets(*args, **kwargs):
        return ({}, {})

    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)
    monkeypatch.setattr(backend, "_resolve_staging_cache", _raise_cache)
    monkeypatch.setattr(backend, "_fallback_stage_inputs", _fallback)
    monkeypatch.setattr(backend, "_ensure_reference_assets_present", _ensure_assets)
    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_slurm_info", _noop)
    monkeypatch.setattr(backend, "_build_nextflow_command", lambda *args, **kwargs: "echo ok")

    from launchpad.backends import slurm_backend as slurm_module

    async def _validate_remote_paths(*args, **kwargs):
        return {}

    monkeypatch.setattr(slurm_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(slurm_module, "check_all_paths_ok", lambda *_: (True, []))
    monkeypatch.setattr(slurm_module, "generate_sbatch_script", lambda **kwargs: "#!/bin/bash\necho hi\n")

    run_uuid = await backend.submit("run-3", params)

    assert run_uuid == "run-3"


@pytest.mark.anyio
async def test_submit_writes_remote_config_and_references_it(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn(existing_paths={"/share/crsp/lab/seyedam/share/igvf_packages/modkit_v0.5.0/dist_modkit_v0.5.0_5120ef7_candle"})

    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        project_slug="proj-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        slurm_account="cpu-request",
        slurm_partition="cpu-part-request",
        workflow_number=4,
        remote_base_path="/remote/eli/agoutic",
        custom_dogme_profile=(
            "export MODKITBASE=/share/crsp/lab/seyedam/share/igvf_packages/modkit_v0.5.0/dist_modkit_v0.5.0_5120ef7_candle\n"
            "export PATH=${MODKITBASE}:${PATH}\n"
            "export MODKITMODEL=${MODKITBASE}/models/r1041_e82_400bps_hac_v5.2.0@v0.1.0\n"
        ),
        custom_dogme_bind_paths=["/share/crsp/lab/seyedam/share/igvf_packages/modkit_v0.5.0/dist_modkit_v0.5.0_5120ef7_candle"],
    )

    async def _load_profile(*args, **kwargs):
        return profile

    async def _fallback(*args, **kwargs):
        return {
            "remote_input": "/remote/eli/agoutic/data/fallback-input",
            "reference_cache_status": "fallback",
            "data_cache_status": "fallback",
            "remote_reference_paths": {
                "mm39": "/remote/eli/agoutic/ref/mm39",
            },
        }

    async def _noop(*args, **kwargs):
        return None

    async def _connect(*args, **kwargs):
        return conn

    async def _ensure_assets(*args, **kwargs):
        return ({
            "mm39": {
                "requires_kallisto": False,
                "missing_required_assets": [],
                "all_required_present": True,
            }
        }, {"mm39": "fallback"})

    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)
    monkeypatch.setattr(backend, "_resolve_staging_cache", _fallback)
    monkeypatch.setattr(backend, "_ensure_reference_assets_present", _ensure_assets)
    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_slurm_info", _noop)

    from launchpad.backends import slurm_backend as slurm_module

    async def _validate_remote_paths(*args, **kwargs):
        return {}

    monkeypatch.setattr(slurm_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(slurm_module, "check_all_paths_ok", lambda *_: (True, []))
    monkeypatch.setattr(slurm_module, "generate_sbatch_script", lambda **kwargs: kwargs["nextflow_command"])

    await backend.submit("run-4", params)

    config_write = [c for c in conn.commands if "nextflow.config" in c and "cat >" in c]
    assert config_write, "Expected remote nextflow.config write command"
    assert "/share/crsp/lab/seyedam/share/igvf_packages/modkit_v0.5.0/dist_modkit_v0.5.0_5120ef7_candle" in config_write[0]

    dogme_profile_write = [c for c in conn.commands if "cat >" in c and "/dogme.profile <<" in c]
    assert dogme_profile_write, "Expected remote dogme.profile write command"
    assert f"export MODKITBASE=${{MODKITBASE:-{DOGME_DNA_MODKITBASE}}}" in dogme_profile_write[0]
    assert f"export MODKITMODEL=${{MODKITMODEL:-${{MODKITBASE}}/models/{DOGME_DNA_MODKITMODEL.name}}}" in dogme_profile_write[0]
    assert "dist_modkit_v0.5.0_5120ef7_candle" not in dogme_profile_write[0]
    assert "export PATH=${MODKITBASE}:${PATH}" not in dogme_profile_write[0]
    assert "LIBTORCH" not in dogme_profile_write[0]
    assert "DYLD_LIBRARY_PATH" not in dogme_profile_write[0]
    assert "LD_LIBRARY_PATH" not in dogme_profile_write[0]

    sbatch_cmds = [c for c in conn.commands if "sbatch --parsable" in c]
    assert sbatch_cmds, "Expected sbatch submission command"

    # The generated batch script content should include nextflow -c pointing to remote workflow config.
    submit_script_payloads = [c for c in conn.commands if "submit_run-4.sh" in c and "cat >" in c]
    assert submit_script_payloads
    assert '"${AGOUTIC_NEXTFLOW_BIN:-nextflow}" run mortazavilab/dogme' in submit_script_payloads[0]
    assert "-c /remote/eli/agoutic/proj-1/workflow4/nextflow.config" in submit_script_payloads[0]

    # Controller CPU values come from profile defaults; GPU values also use profile defaults.
    assert "cpuAccount = 'cpu-default'" in config_write[0]
    assert "cpuPartition = 'cpu-part-default'" in config_write[0]
    assert "gpuAccount = 'gpu-default'" in config_write[0]
    assert "gpuPartition = 'gpu-part-default'" in config_write[0]

    # Remote staged reference cache should be used in genome_annot_refs.
    mm39_cfg = REFERENCE_GENOMES["mm39"]
    assert f"/remote/eli/agoutic/ref/mm39/{Path(mm39_cfg['fasta']).name}" in config_write[0]
    assert f"/remote/eli/agoutic/ref/mm39/{Path(mm39_cfg['gtf']).name}" in config_write[0]

    # Staged input cache should be symlinked to workflow-local pod5 directory.
    symlink_cmds = [c for c in conn.commands if "ln -sfn" in c and "/workflow4/pod5" in c]
    assert symlink_cmds
    assert "/remote/eli/agoutic/data/fallback-input" in symlink_cmds[0]


@pytest.mark.anyio
async def test_submit_ignores_custom_profile_fields_for_non_dna(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn()

    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        project_slug="proj-1",
        sample_name="sample",
        mode="RNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        workflow_number=4,
        remote_base_path="/remote/eli/agoutic",
        custom_dogme_profile="export MODKITBASE=/cluster/modkit\n",
        custom_dogme_bind_paths=["/cluster/modkit"],
    )

    async def _load_profile(*args, **kwargs):
        return profile

    async def _fallback(*args, **kwargs):
        return {
            "remote_input": "/remote/eli/agoutic/data/fallback-input",
            "reference_cache_status": "fallback",
            "data_cache_status": "fallback",
            "remote_reference_paths": {
                "mm39": "/remote/eli/agoutic/ref/mm39",
            },
        }

    async def _noop(*args, **kwargs):
        return None

    async def _connect(*args, **kwargs):
        return conn

    async def _ensure_assets(*args, **kwargs):
        return ({
            "mm39": {
                "requires_kallisto": True,
                "missing_required_assets": [],
                "all_required_present": True,
            }
        }, {"mm39": "fallback"})

    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)
    monkeypatch.setattr(backend, "_resolve_staging_cache", _fallback)
    monkeypatch.setattr(backend, "_ensure_reference_assets_present", _ensure_assets)
    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_slurm_info", _noop)

    from launchpad.backends import slurm_backend as slurm_module

    async def _validate_remote_paths(*args, **kwargs):
        return {}

    monkeypatch.setattr(slurm_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(slurm_module, "check_all_paths_ok", lambda *_: (True, []))
    monkeypatch.setattr(slurm_module, "generate_sbatch_script", lambda **kwargs: kwargs["nextflow_command"])

    await backend.submit("run-rna", params)

    config_write = [c for c in conn.commands if "nextflow.config" in c and "cat >" in c]
    assert config_write, "Expected remote nextflow.config write command"
    assert "/cluster/modkit" not in config_write[0]

    dogme_profile_write = [c for c in conn.commands if "cat >" in c and "/dogme.profile <<" in c]
    assert dogme_profile_write, "Expected remote dogme.profile write command"
    assert "export MODKITBASE=/cluster/modkit" not in dogme_profile_write[0]


@pytest.mark.anyio
async def test_submit_scopes_custom_dogme_bind_paths_to_openchromatin_tasks(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn(
        existing_paths={
            "/cluster/modkit",
            "/lib64",
            "/lib64/libgomp.so.1",
            "/lib64/libstdc++.so.6",
            "/lib64/libgcc_s.so.1",
        }
    )

    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        project_slug="proj-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        workflow_number=4,
        remote_base_path="/remote/eli/agoutic",
        custom_dogme_profile="export MODKITBASE=/cluster/modkit\n",
        custom_dogme_bind_paths=["/cluster/modkit", "/lib64/libgomp.so.1"],
    )

    async def _load_profile(*args, **kwargs):
        return profile

    async def _fallback(*args, **kwargs):
        return {
            "remote_input": "/remote/eli/agoutic/data/fallback-input",
            "reference_cache_status": "fallback",
            "data_cache_status": "fallback",
            "remote_reference_paths": {
                "mm39": "/remote/eli/agoutic/ref/mm39",
            },
        }

    async def _noop(*args, **kwargs):
        return None

    async def _connect(*args, **kwargs):
        return conn

    async def _ensure_assets(*args, **kwargs):
        return ({
            "mm39": {
                "requires_kallisto": False,
                "missing_required_assets": [],
                "all_required_present": True,
            }
        }, {"mm39": "fallback"})

    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)
    monkeypatch.setattr(backend, "_resolve_staging_cache", _fallback)
    monkeypatch.setattr(backend, "_ensure_reference_assets_present", _ensure_assets)
    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_slurm_info", _noop)

    from launchpad.backends import slurm_backend as slurm_module

    async def _validate_remote_paths(*args, **kwargs):
        return {}

    monkeypatch.setattr(slurm_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(slurm_module, "check_all_paths_ok", lambda *_: (True, []))
    monkeypatch.setattr(slurm_module, "generate_sbatch_script", lambda **kwargs: kwargs["nextflow_command"])

    await backend.submit("run-modkit-scope", params)

    config_write = [c for c in conn.commands if "nextflow.config" in c and "cat >" in c]
    assert config_write, "Expected remote nextflow.config write command"
    config_text = config_write[0]
    assert "containerOptions = '--no-mount hostfs --bind /remote/eli/agoutic/proj-1/workflow4,/remote/eli/agoutic/data/fallback-input,/remote/eli/agoutic/ref/mm39'" in config_text
    assert "withName: 'modkitTask' {" in config_text
    assert "withName: 'modkitTask' {\n        memory = '32 GB'\n        cpus = 12\n        containerOptions = '--no-mount hostfs --bind /remote/eli/agoutic/proj-1/workflow4,/remote/eli/agoutic/data/fallback-input,/remote/eli/agoutic/ref/mm39'" in config_text
    assert "containerOptions = '--nv --no-mount hostfs --bind /remote/eli/agoutic/proj-1/workflow4,/remote/eli/agoutic/data/fallback-input,/remote/eli/agoutic/ref/mm39'" in config_text
    assert "withName: 'openChromatinTaskBg' {" in config_text
    assert "withName: 'openChromatinTaskBed' {" in config_text
    assert config_text.count("containerOptions = '--nv --no-mount hostfs --bind /remote/eli/agoutic/proj-1/workflow4,/remote/eli/agoutic/data/fallback-input,/remote/eli/agoutic/ref/mm39,/cluster/modkit,/lib64/libgomp.so.1,/lib64/libstdc++.so.6,/lib64/libgcc_s.so.1 --env \\\'MODKITBASE=/cluster/modkit,PREPEND_PATH=/remote/eli/agoutic/proj-1/workflow4/.agoutic-openchrom-bin,LD_LIBRARY_PATH=/lib64:\\\\$LD_LIBRARY_PATH\\\''") == 2
    wrapper_write = [c for c in conn.commands if "/.agoutic-openchrom-bin/modkit << 'AGOUTIC_EOF'" in c]
    assert wrapper_write, "Expected OpenChromatin modkit wrapper to be staged"


@pytest.mark.asyncio
async def test_submit_scopes_custom_dogme_runtime_exports_to_openchromatin_only(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn(
        existing_paths={
            "/cluster/modkit",
            "/lib64",
            "/lib64/libgomp.so.1",
            "/lib64/libstdc++.so.6",
            "/lib64/libgcc_s.so.1",
        }
    )

    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        project_slug="proj-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        workflow_number=4,
        remote_base_path="/remote/eli/agoutic",
        custom_dogme_profile=(
            "export MODKITBASE=/cluster/modkit\n"
            "export PATH=${MODKITBASE}:${PATH}\n"
            "export MODKITMODEL=${MODKITBASE}/models/r1041_e82_400bps_hac_v5.2.0@v0.1.0\n"
            "export LIBTORCH=${MODKITBASE}/libtorch\n"
            "export LD_LIBRARY_PATH=${LIBTORCH}/lib:${LD_LIBRARY_PATH:-}\n"
            "export DYLD_LIBRARY_PATH=${LIBTORCH}/lib:${DYLD_LIBRARY_PATH:-}\n"
        ),
        custom_dogme_bind_paths=["/cluster/modkit", "/lib64/libgomp.so.1"],
    )

    async def _load_profile(*args, **kwargs):
        return profile

    async def _fallback(*args, **kwargs):
        return {
            "remote_input": "/remote/eli/agoutic/data/fallback-input",
            "reference_cache_status": "fallback",
            "data_cache_status": "fallback",
            "remote_reference_paths": {
                "mm39": "/remote/eli/agoutic/ref/mm39",
            },
        }

    async def _noop(*args, **kwargs):
        return None

    async def _connect(*args, **kwargs):
        return conn

    async def _ensure_assets(*args, **kwargs):
        return ({
            "mm39": {
                "requires_kallisto": False,
                "missing_required_assets": [],
                "all_required_present": True,
            }
        }, {"mm39": "fallback"})

    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)
    monkeypatch.setattr(backend, "_resolve_staging_cache", _fallback)
    monkeypatch.setattr(backend, "_ensure_reference_assets_present", _ensure_assets)
    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_slurm_info", _noop)

    from launchpad.backends import slurm_backend as slurm_module

    async def _validate_remote_paths(*args, **kwargs):
        return {}

    monkeypatch.setattr(slurm_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(slurm_module, "check_all_paths_ok", lambda *_: (True, []))
    monkeypatch.setattr(slurm_module, "generate_sbatch_script", lambda **kwargs: kwargs["nextflow_command"])

    await backend.submit("run-runtime-scope", params)

    config_write = [c for c in conn.commands if "nextflow.config" in c and "cat >" in c]
    assert config_write, "Expected remote nextflow.config write command"
    config_text = config_write[0]
    assert config_text.count("--bind /remote/eli/agoutic/proj-1/workflow4,/remote/eli/agoutic/data/fallback-input,/remote/eli/agoutic/ref/mm39,/cluster/modkit,/lib64/libgomp.so.1,/lib64/libstdc++.so.6,/lib64/libgcc_s.so.1 --env \\\'MODKITBASE=/cluster/modkit,PREPEND_PATH=/remote/eli/agoutic/proj-1/workflow4/.agoutic-openchrom-bin:/cluster/modkit,MODKITMODEL=/cluster/modkit/models/r1041_e82_400bps_hac_v5.2.0@v0.1.0,LIBTORCH=/cluster/modkit/libtorch,LD_LIBRARY_PATH=/lib64:/cluster/modkit/libtorch/lib:\\\\$LD_LIBRARY_PATH,DYLD_LIBRARY_PATH=/cluster/modkit/libtorch/lib:\\\\$DYLD_LIBRARY_PATH\\\'") == 2
    assert config_text.count("beforeScript = 'export PATH=/opt/conda/bin:$PATH'") == 1
    wrapper_write = [c for c in conn.commands if "/.agoutic-openchrom-bin/modkit << 'AGOUTIC_EOF'" in c]
    assert wrapper_write, "Expected OpenChromatin modkit wrapper to be staged"

    dogme_profile_write = [c for c in conn.commands if "cat >" in c and "/dogme.profile <<" in c]
    assert dogme_profile_write, "Expected remote dogme.profile write command"
    assert f"export MODKITBASE=${{MODKITBASE:-{DOGME_DNA_MODKITBASE}}}" in dogme_profile_write[0]
    assert f"export MODKITMODEL=${{MODKITMODEL:-${{MODKITBASE}}/models/{DOGME_DNA_MODKITMODEL.name}}}" in dogme_profile_write[0]
    assert "export MODKITBASE=/cluster/modkit" not in dogme_profile_write[0]
    assert "export PATH=${MODKITBASE}:${PATH}" not in dogme_profile_write[0]
    assert "export MODKITMODEL=${MODKITBASE}/models/r1041_e82_400bps_hac_v5.2.0@v0.1.0" not in dogme_profile_write[0]
    assert "LIBTORCH" not in dogme_profile_write[0]
    assert "LD_LIBRARY_PATH" not in dogme_profile_write[0]
    assert "DYLD_LIBRARY_PATH" not in dogme_profile_write[0]


@pytest.mark.asyncio
async def test_stage_remote_sample_dna_does_not_require_kallisto_sidecars(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn(
        dir_entries={
            "/remote/eli/agoutic/ref/mm39": [
                {"name": Path(REFERENCE_GENOMES["mm39"]["fasta"]).name, "type": "file", "size": 1},
                {"name": Path(REFERENCE_GENOMES["mm39"]["gtf"]).name, "type": "file", "size": 1},
            ]
        }
    )
    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        remote_base_path="/remote/eli/agoutic",
    )

    async def _load_profile(*args, **kwargs):
        return profile

    async def _connect(*args, **kwargs):
        return conn

    async def _stage_inputs(*args, **kwargs):
        return {
            "remote_input": "/remote/eli/agoutic/data/fp1",
            "data_cache_status": "reused",
            "reference_cache_statuses": {"mm39": "reused"},
            "remote_reference_paths": {"mm39": "/remote/eli/agoutic/ref/mm39"},
        }

    async def _validate_remote_paths(*args, **kwargs):
        return {}

    from launchpad.backends import slurm_backend as slurm_module

    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)
    monkeypatch.setattr(backend, "_stage_sample_inputs", _stage_inputs)
    monkeypatch.setattr(slurm_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(slurm_module, "check_all_paths_ok", lambda *_: (True, []))

    result = await backend.stage_remote_sample(params)

    evidence = result["reference_asset_evidence"]["mm39"]
    assert evidence["requires_kallisto"] is False
    assert evidence["missing_required_assets"] == []
    assert evidence["all_required_present"] is True
    assert evidence["optional_assets"] == {
        "kallisto_index": Path(REFERENCE_GENOMES["mm39"]["kallisto_index"]).name,
        "kallisto_t2g": Path(REFERENCE_GENOMES["mm39"]["kallisto_t2g"]).name,
    }


@pytest.mark.asyncio
async def test_stage_remote_sample_cdna_requires_kallisto_sidecars(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn(
        dir_entries={
            "/remote/eli/agoutic/ref/mm39": [
                {"name": Path(REFERENCE_GENOMES["mm39"]["fasta"]).name, "type": "file", "size": 1},
                {"name": Path(REFERENCE_GENOMES["mm39"]["gtf"]).name, "type": "file", "size": 1},
            ]
        }
    )
    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        sample_name="sample",
        mode="CDNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        remote_base_path="/remote/eli/agoutic",
    )

    async def _load_profile(*args, **kwargs):
        return profile

    async def _connect(*args, **kwargs):
        return conn

    async def _stage_inputs(*args, **kwargs):
        return {
            "remote_input": "/remote/eli/agoutic/data/fp1",
            "data_cache_status": "reused",
            "reference_cache_statuses": {"mm39": "reused"},
            "remote_reference_paths": {"mm39": "/remote/eli/agoutic/ref/mm39"},
        }

    async def _validate_remote_paths(*args, **kwargs):
        return {}

    from launchpad.backends import slurm_backend as slurm_module
    from launchpad import db as launchpad_db

    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)
    monkeypatch.setattr(backend, "_stage_sample_inputs", _stage_inputs)
    monkeypatch.setattr(slurm_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(slurm_module, "check_all_paths_ok", lambda *_: (True, []))

    upload_calls = []

    async def _upload_inputs(**kwargs):
        upload_calls.append(kwargs)
        conn.dir_entries["/remote/eli/agoutic/ref/mm39"] = [
            {"name": Path(REFERENCE_GENOMES["mm39"]["fasta"]).name, "type": "file", "size": 1},
            {"name": Path(REFERENCE_GENOMES["mm39"]["gtf"]).name, "type": "file", "size": 1},
            {"name": Path(REFERENCE_GENOMES["mm39"]["kallisto_index"]).name, "type": "file", "size": 1},
            {"name": Path(REFERENCE_GENOMES["mm39"]["kallisto_t2g"]).name, "type": "file", "size": 1},
        ]
        return {"ok": True, "message": "ok", "bytes_transferred": 42}

    async def _upsert_ref(*args, **kwargs):
        return None

    monkeypatch.setattr(backend._transfer_manager, "upload_inputs", _upload_inputs)
    monkeypatch.setattr(launchpad_db, "upsert_remote_reference_cache_entry", _upsert_ref)
    monkeypatch.setattr(backend, "_resolve_reference_source_dir", lambda _: Path("/tmp/mm39"))
    monkeypatch.setattr(backend, "_compute_directory_signature", lambda _: "sig-mm39")

    result = await backend.stage_remote_sample(params)

    evidence = result["reference_asset_evidence"]["mm39"]
    assert evidence["requires_kallisto"] is True
    assert evidence["missing_required_assets"] == []
    assert evidence["all_required_present"] is True
    assert result["reference_cache_statuses"]["mm39"] == "refreshed"
    assert len(upload_calls) == 1


@pytest.mark.asyncio
async def test_stage_remote_sample_cdna_fails_when_reference_assets_still_missing_after_refresh(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn(
        dir_entries={
            "/remote/eli/agoutic/ref/mm39": [
                {"name": Path(REFERENCE_GENOMES["mm39"]["fasta"]).name, "type": "file", "size": 1},
                {"name": Path(REFERENCE_GENOMES["mm39"]["gtf"]).name, "type": "file", "size": 1},
            ]
        }
    )
    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        sample_name="sample",
        mode="CDNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        remote_base_path="/remote/eli/agoutic",
    )

    async def _load_profile(*args, **kwargs):
        return profile

    async def _connect(*args, **kwargs):
        return conn

    async def _stage_inputs(*args, **kwargs):
        return {
            "remote_input": "/remote/eli/agoutic/data/fp1",
            "data_cache_status": "reused",
            "reference_cache_statuses": {"mm39": "reused"},
            "remote_reference_paths": {"mm39": "/remote/eli/agoutic/ref/mm39"},
        }

    async def _validate_remote_paths(*args, **kwargs):
        return {}

    from launchpad.backends import slurm_backend as slurm_module
    from launchpad import db as launchpad_db

    async def _upload_inputs(**kwargs):
        return {"ok": True, "message": "ok", "bytes_transferred": 0}

    async def _upsert_ref(*args, **kwargs):
        return None

    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)
    monkeypatch.setattr(backend, "_stage_sample_inputs", _stage_inputs)
    monkeypatch.setattr(slurm_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(slurm_module, "check_all_paths_ok", lambda *_: (True, []))
    monkeypatch.setattr(backend._transfer_manager, "upload_inputs", _upload_inputs)
    monkeypatch.setattr(launchpad_db, "upsert_remote_reference_cache_entry", _upsert_ref)
    monkeypatch.setattr(backend, "_resolve_reference_source_dir", lambda _: Path("/tmp/mm39"))
    monkeypatch.setattr(backend, "_compute_directory_signature", lambda _: "sig-mm39")

    with pytest.raises(RuntimeError, match="Remote reference cache verification failed after refresh"):
        await backend.stage_remote_sample(params)


@pytest.mark.asyncio
async def test_check_status_includes_sacct_failure_reason(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeStatusConn(sacct_output="FAILED|127:0|NonZeroExitCode\n")

    from launchpad import db as launchpad_db

    async def _get_job_by_uuid(*args, **kwargs):
        return SimpleNamespace(
            run_uuid="run-5",
            status="RUNNING",
            run_stage="queued",
            slurm_job_id="50042924",
            transfer_state=None,
            result_destination="local",
            ssh_profile_id="profile-1",
            user_id="user-1",
            slurm_state=None,
        )

    updates = []

    async def _update_job_slurm_state(run_uuid, raw_state, agoutic_status, *, error_message=None):
        updates.append(
            {
                "run_uuid": run_uuid,
                "raw_state": raw_state,
                "agoutic_status": agoutic_status,
                "error_message": error_message,
            }
        )

    async def _connect(*args, **kwargs):
        return conn

    async def _load_profile(*args, **kwargs):
        return profile

    monkeypatch.setattr(launchpad_db, "get_job_by_uuid", _get_job_by_uuid)
    monkeypatch.setattr(backend, "_update_job_slurm_state", _update_job_slurm_state)
    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)

    status = await backend.check_status("run-5")

    assert status.status == "FAILED"
    assert status.slurm_state == "FAILED"
    assert "exit code 127:0" in status.message
    assert "non-zero exit code" in status.message.lower()
    assert updates[0]["error_message"] == status.message


@pytest.mark.asyncio
async def test_check_status_reports_remote_trace_progress(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeStatusConn(
        sacct_output="RUNNING|0:0|\n",
        trace_output=(
            "task_id\thash\tnative_id\tname\tstatus\texit\n"
            "1\tda/2fa490\t50043101\tmainWorkflow:doradoDownloadTask\tCOMPLETED\t0\n"
            "2\t5/abc123\t50043106\tmainWorkflow:softwareVTask\tCOMPLETED\t0\n"
        ),
        slurm_out_output=(
            "executor >  slurm (3)\n"
            "[fe/e700c5] mainWorkflow:doradoTask (1) | 0 of 1\n"
        ),
    )

    from launchpad import db as launchpad_db

    async def _get_job_by_uuid(*args, **kwargs):
        return SimpleNamespace(
            run_uuid="run-6",
            status="RUNNING",
            progress_percent=0,
            run_stage="running",
            slurm_job_id="50043100",
            transfer_state=None,
            result_destination="local",
            ssh_profile_id="profile-1",
            user_id="user-1",
            slurm_state=None,
            remote_work_dir="/remote/eli/agoutic/proj-1/workflow7",
        )

    async def _connect(*args, **kwargs):
        return conn

    async def _load_profile(*args, **kwargs):
        return profile

    async def _noop_update(*args, **kwargs):
        return None

    monkeypatch.setattr(launchpad_db, "get_job_by_uuid", _get_job_by_uuid)
    monkeypatch.setattr(backend, "_update_job_slurm_state", _noop_update)
    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)

    status = await backend.check_status("run-6")

    assert status.status == "RUNNING"
    assert status.progress_percent == 60
    assert status.tasks["completed_count"] == 2
    assert status.tasks["total"] == 3
    assert status.tasks["running"] == ["mainWorkflow:doradoTask (1)"]
    assert "2/3 completed" in status.message


def test_parse_task_status_texts_excludes_numbered_tasks_already_completed_in_trace():
    progress, tasks, message = SlurmBackend._parse_task_status_texts(
        trace_content=(
            "task_id\thash\tnative_id\tname\tstatus\texit\n"
            "1\t71/22d996\t50052915\tmainWorkflow:doradoDownloadTask\tCOMPLETED\t0\n"
            "2\td0/606400\t50052916\tmainWorkflow:softwareVTask\tCOMPLETED\t0\n"
            "3\t46/b026bb\t50052917\tmainWorkflow:doradoTask (2)\tCOMPLETED\t0\n"
            "4\t79/ee1ac3\t50052918\tmainWorkflow:doradoTask (1)\tCOMPLETED\t0\n"
        ),
        stdout_content=(
            "executor >  slurm (5)\n"
            "[71/22d996] mainWorkflow:doradoDownloadTask\n"
            "[d0/606400] mainWorkflow:softwareVTask\n"
            "[46/b026bb] mainWorkflow:doradoTask (2)\n"
            "[79/ee1ac3] mainWorkflow:doradoTask (1)\n"
        ),
        scheduler_status="RUNNING",
    )

    assert progress == 72
    assert tasks["completed_count"] == 4
    assert tasks["running"] == []
    assert message == "Pipeline: 4/5 completed, 1 remaining"


def test_parse_task_status_texts_no_double_count_across_naming_schemes():
    """Trace uses 'basecall:basecallWorkflow:X' but stdout uses 'mainWorkflow:X'.
    Tasks should not be double-counted."""
    progress, tasks, message = SlurmBackend._parse_task_status_texts(
        trace_content=(
            "task_id\thash\tnative_id\tname\tstatus\texit\n"
            "1\t86/1307f7\t50292868\tbasecall:basecallWorkflow:doradoDownloadTask\tCOMPLETED\t0\n"
            "2\tf6/18f1aa\t50292869\tbasecall:basecallWorkflow:doradoTask (1)\tCOMPLETED\t0\n"
            "5\t38/164ccd\t50292870\tbasecall:basecallWorkflow:softwareVTask\tCOMPLETED\t0\n"
        ),
        stdout_content=(
            "executor >  slurm (5)\n"
            "[86/1307f7] Submitted process > mainWorkflow:doradoDownloadTask\n"
            "[86/1307f7] process > mainWorkflow:doradoDownloadTask [100%] 1 of 1 ✔\n"
            "[f6/18f1aa] Submitted process > mainWorkflow:doradoTask (1)\n"
            "[f6/18f1aa] process > mainWorkflow:doradoTask (1) [100%] 1 of 1 ✔\n"
            "[38/164ccd] Submitted process > mainWorkflow:softwareVTask\n"
            "[38/164ccd] process > mainWorkflow:softwareVTask [100%] 1 of 1 ✔\n"
            "[7b/70cc3f] Submitted process > mainWorkflow:doradoTask (3)\n"
            "[af/c0825c] Submitted process > mainWorkflow:doradoTask (2)\n"
        ),
        scheduler_status="RUNNING",
    )

    assert tasks["completed_count"] == 3
    assert tasks["running"] == ["mainWorkflow:doradoTask (3)", "mainWorkflow:doradoTask (2)"]
    assert tasks["total"] == 5


def test_parse_task_status_texts_uses_latest_stdout_event_per_hash():
    progress, tasks, message = SlurmBackend._parse_task_status_texts(
        trace_content="",
        stdout_content=(
            "executor >  local (2)\n"
            "[aa/bbccdd] mainWorkflow:doradoTask (1)\n"
            "[aa/bbccdd] mainWorkflow:doradoTask (1) ✔\n"
            "[ee/ff0011] mainWorkflow:softwareVTask\n"
        ),
        scheduler_status="RUNNING",
    )

    assert progress == 45
    assert tasks["completed_count"] == 1
    assert tasks["completed"] == ["mainWorkflow:doradoTask (1)"]
    assert tasks["total"] == 2
    assert tasks["running"] == ["mainWorkflow:softwareVTask"]
    assert message == "Pipeline: 1/2 completed, 1 remaining"


def test_parse_task_status_texts_stdout_only_no_trace():
    """When the trace file is missing/empty, stdout ✔ events drive completed counts."""
    progress, tasks, message = SlurmBackend._parse_task_status_texts(
        trace_content="",
        stdout_content=(
            "executor >  slurm (4)\n"
            "[71/22d996] mainWorkflow:doradoDownloadTask | 1 of 1 ✔\n"
            "[d0/606400] mainWorkflow:softwareVTask | 0 of 1\n"
            "[46/b026bb] mainWorkflow:doradoTask (2) | 0 of 3\n"
            "[79/ee1ac3] mainWorkflow:doradoTask (3) | 0 of 3\n"
        ),
        scheduler_status="RUNNING",
    )

    assert tasks["completed_count"] == 1
    assert tasks["completed"] == ["mainWorkflow:doradoDownloadTask"]
    assert len(tasks["running"]) == 3
    assert tasks["total"] == 5
    assert tasks["failed_count"] == 0


def test_parse_task_status_texts_slurm_submitted_process_format():
    """SLURM Nextflow output prefixes 'Submitted process >' before task names."""
    progress, tasks, message = SlurmBackend._parse_task_status_texts(
        trace_content="",
        stdout_content=(
            "executor >  slurm (3)\n"
            "[fe/89c0d3] Submitted process > mainWorkflow:doradoTask (2)\n"
            "[5d/6607d2] Submitted process > mainWorkflow:softwareVTask\n"
            "[48/13d7f7] Submitted process > mainWorkflow:doradoTask (3)\n"
        ),
        scheduler_status="RUNNING",
    )

    assert tasks["completed_count"] == 0
    assert len(tasks["running"]) == 3
    assert "mainWorkflow:doradoTask (2)" in tasks["running"]
    assert "mainWorkflow:softwareVTask" in tasks["running"]
    assert "mainWorkflow:doradoTask (3)" in tasks["running"]
    assert message == "Pipeline: 0/3 completed, 3 remaining"


def test_parse_task_status_texts_slurm_process_gt_completion():
    """SLURM -ansi-log false writes 'process > taskName ... ✔' on completion."""
    _, tasks, _ = SlurmBackend._parse_task_status_texts(
        trace_content="",
        stdout_content=(
            "executor >  slurm (3)\n"
            "[fe/89c0d3] Submitted process > mainWorkflow:doradoTask (2)\n"
            "[fe/89c0d3] process > mainWorkflow:doradoTask (2) [100%] 1 of 1 ✔\n"
            "[5d/6607d2] Submitted process > mainWorkflow:softwareVTask\n"
            "[48/13d7f7] Submitted process > mainWorkflow:doradoTask (3)\n"
        ),
        scheduler_status="RUNNING",
    )

    assert tasks["completed_count"] == 1
    assert tasks["completed"] == ["mainWorkflow:doradoTask (2)"]
    assert len(tasks["running"]) == 2
    assert "mainWorkflow:softwareVTask" in tasks["running"]
    assert "mainWorkflow:doradoTask (3)" in tasks["running"]


def test_parse_task_status_texts_stdout_failed_goes_to_failed():
    """FAILED events from stdout should land in failed_tasks, not completed."""
    _, tasks, _ = SlurmBackend._parse_task_status_texts(
        trace_content="",
        stdout_content=(
            "[aa/bbccdd] process > mainWorkflow:doradoTask (1) FAILED\n"
            "[ee/ff0011] process > mainWorkflow:softwareVTask ✔\n"
        ),
        scheduler_status="RUNNING",
    )

    assert tasks["completed_count"] == 1
    assert tasks["failed_count"] == 1
    assert "mainWorkflow:doradoTask (1)" not in tasks["completed"]
    assert "mainWorkflow:softwareVTask" in tasks["completed"]


def test_parse_task_status_texts_retry_attempts_do_not_count_as_terminal_failures():
    progress, tasks, message = SlurmBackend._parse_task_status_texts(
        trace_content=(
            "task_id\thash\tnative_id\tname\tstatus\texit\n"
            "1\taa/bb0011\t1001\tmainWorkflow:doradoDownloadTask\tCOMPLETED\t0\n"
            "2\tcc/dd0022\t1002\tmainWorkflow:softwareVTask\tCOMPLETED\t0\n"
            "3\tee/ff0033\t1003\tmainWorkflow:doradoTask (1)\tFAILED\t1\n"
            "4\tgg/hh0044\t1004\tmainWorkflow:doradoTask (1)\tCOMPLETED\t0\n"
        ),
        stdout_content=(
            "executor >  slurm (554)\n"
            "[10/9019] mainWorkflow:doradoTask (695) | 611 of 729, retries: 68\n"
        ),
        scheduler_status="RUNNING",
    )

    assert progress == int((613 / 731) * 90)
    assert tasks["completed_count"] == 613
    assert tasks["total"] == 731
    assert tasks["remaining_count"] == 118
    assert tasks["failed_count"] == 0
    assert tasks["retried_count"] == 68
    assert message == "Pipeline: 613/731 completed, 118 remaining, 68 retries"


def test_parse_task_status_texts_prefers_unique_family_totals_over_executor_attempt_count():
    progress, tasks, message = SlurmBackend._parse_task_status_texts(
        trace_content=(
            "task_id\thash\tnative_id\tname\tstatus\texit\n"
            "1\taa/bb0011\t1001\tmainWorkflow:doradoDownloadTask\tCOMPLETED\t0\n"
            "2\tcc/dd0022\t1002\tmainWorkflow:softwareVTask\tCOMPLETED\t0\n"
        ),
        stdout_content=(
            "executor >  slurm (796)\n"
            "[f1/a17ed] mainWorkflow:doradoTask (693) | 626 of 729, retries: 68\n"
            "[80/9041d1] mainWorkflow:softwareVTask | 1 of 1 ✔\n"
            "[ae/d5c965] mainWorkflow:doradoDownloadTask | 1 of 1 ✔\n"
        ),
        scheduler_status="RUNNING",
    )

    assert progress == int((628 / 731) * 90)
    assert tasks["completed_count"] == 628
    assert tasks["total"] == 731
    assert tasks["remaining_count"] == 103
    assert tasks["retried_count"] == 68
    assert message == "Pipeline: 628/731 completed, 103 remaining, 68 retries"


def test_parse_task_status_texts_strips_ansi_escape_codes():
    """ANSI cursor-control codes in SLURM stdout must be stripped before parsing."""
    _, tasks, _ = SlurmBackend._parse_task_status_texts(
        trace_content="",
        stdout_content=(
            "executor >  slurm (2)\n"
            "\x1b[?1h\x1b[3A\x1b[K[fe/89c0d3] process > mainWorkflow:doradoTask (1) [100%] 1 of 1 ✔\n"
            "\x1b[K[5d/6607d2] process > mainWorkflow:softwareVTask [  0%] 0 of 1\r\n"
        ),
        scheduler_status="RUNNING",
    )

    assert tasks["completed_count"] == 1
    assert tasks["completed"] == ["mainWorkflow:doradoTask (1)"]
    assert tasks["running"] == ["mainWorkflow:softwareVTask"]


def test_controller_resources_prefer_cpu_defaults(profile):
    backend = SlurmBackend()
    params = SubmitParams(
        slurm_account="gpu-request",
        slurm_partition="gpu-request",
        slurm_gpus=1,
    )

    account, partition = backend._resolve_controller_resources(params, profile)

    assert account == "cpu-default"
    assert partition == "cpu-part-default"


@pytest.mark.anyio
async def test_submit_derives_reference_paths_when_cache_metadata_missing(monkeypatch, profile):
    backend = SlurmBackend()
    conn = _FakeConn()

    params = SubmitParams(
        project_id="proj-1",
        user_id="user-1",
        project_slug="proj-1",
        sample_name="sample",
        mode="DNA",
        input_directory="/tmp/input",
        reference_genome=["mm39"],
        ssh_profile_id="profile-1",
        slurm_account="cpu-request",
        slurm_partition="cpu-part-request",
        workflow_number=5,
        remote_base_path="/remote/eli/agoutic",
    )

    async def _load_profile(*args, **kwargs):
        return profile

    async def _resolve_stage(*args, **kwargs):
        # Simulate reuse/fallback metadata path where reference mappings are absent.
        return {
            "remote_input": "/remote/eli/agoutic/data/reused-input",
            "reference_cache_status": "reused",
            "data_cache_status": "reused",
        }

    async def _noop(*args, **kwargs):
        return None

    async def _connect(*args, **kwargs):
        return conn

    async def _ensure_assets(*args, **kwargs):
        return ({}, {})

    monkeypatch.setattr(backend, "_load_profile", _load_profile)
    monkeypatch.setattr(backend._ssh_manager, "connect", _connect)
    monkeypatch.setattr(backend, "_resolve_staging_cache", _resolve_stage)
    monkeypatch.setattr(backend, "_ensure_reference_assets_present", _ensure_assets)
    monkeypatch.setattr(backend, "_update_job_stage", _noop)
    monkeypatch.setattr(backend, "_update_job_slurm_info", _noop)

    from launchpad.backends import slurm_backend as slurm_module

    async def _validate_remote_paths(*args, **kwargs):
        return {}

    monkeypatch.setattr(slurm_module, "validate_remote_paths", _validate_remote_paths)
    monkeypatch.setattr(slurm_module, "check_all_paths_ok", lambda *_: (True, []))
    monkeypatch.setattr(slurm_module, "generate_sbatch_script", lambda **kwargs: kwargs["nextflow_command"])

    await backend.submit("run-5", params)

    config_write = [c for c in conn.commands if "nextflow.config" in c and "cat >" in c]
    assert config_write, "Expected remote nextflow.config write command"

    mm39_cfg = REFERENCE_GENOMES["mm39"]
    assert f"/remote/eli/agoutic/ref/mm39/{Path(mm39_cfg['fasta']).name}" in config_write[0]
    assert f"/remote/eli/agoutic/ref/mm39/{Path(mm39_cfg['gtf']).name}" in config_write[0]
