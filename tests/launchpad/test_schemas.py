"""
Tests for launchpad/schemas.py — Pydantic request/response validation.
"""

import pytest
from pydantic import ValidationError

from launchpad.schemas import (
    SubmitJobRequest,
    StageRemoteSampleRequest,
    JobStatusResponse,
    JobDetailsResponse,
    JobSubmitResponse,
)


class TestSubmitJobRequest:
    def test_minimal_valid(self):
        req = SubmitJobRequest(
            project_id="proj-1",
            sample_name="sample1",
            mode="DNA",
            input_directory="/data/pod5",
        )
        assert req.project_id == "proj-1"
        assert req.sample_name == "sample1"
        assert req.mode == "DNA"
        assert req.input_type == "pod5"  # default

    def test_genome_string_normalised_to_list(self):
        req = SubmitJobRequest(
            project_id="p", sample_name="s", mode="DNA",
            input_directory="/d", reference_genome="GRCh38",
        )
        assert req.reference_genome == ["GRCh38"]

    def test_genome_list_passthrough(self):
        req = SubmitJobRequest(
            project_id="p", sample_name="s", mode="DNA",
            input_directory="/d", reference_genome=["GRCh38", "mm39"],
        )
        assert req.reference_genome == ["GRCh38", "mm39"]

    def test_empty_project_id_rejected(self):
        with pytest.raises(ValidationError):
            SubmitJobRequest(
                project_id="", sample_name="s", mode="DNA",
                input_directory="/d",
            )

    def test_empty_sample_name_rejected(self):
        with pytest.raises(ValidationError):
            SubmitJobRequest(
                project_id="p", sample_name="", mode="DNA",
                input_directory="/d",
            )

    def test_invalid_input_type_rejected(self):
        with pytest.raises(ValidationError):
            SubmitJobRequest(
                project_id="p", sample_name="s", mode="DNA",
                input_directory="/d", input_type="invalid",
            )

    def test_all_optional_fields(self):
        req = SubmitJobRequest(
            project_id="p",
            sample_name="MySample",
            mode="RNA",
            input_directory="/data/pod5",
            user_id="user-1",
            username="testuser",
            project_slug="my-project",
            modifications="m6A",
            entry_point="remap",
            parent_block_id="blk-abc",
            modkit_filter_threshold=0.75,
            min_cov=3,
            per_mod=10,
            accuracy="hac",
            max_gpu_tasks=2,
        )
        assert req.accuracy == "hac"
        assert req.min_cov == 3
        assert req.modifications == "m6A"

    def test_default_values(self):
        req = SubmitJobRequest(
            project_id="p", sample_name="s", mode="DNA",
            input_directory="/d",
        )
        assert req.modkit_filter_threshold == 0.9
        assert req.per_mod == 5
        assert req.accuracy == "sup"
        assert req.max_gpu_tasks is None

    def test_max_gpu_tasks_range_validation(self):
        with pytest.raises(ValidationError):
            SubmitJobRequest(
                project_id="p",
                sample_name="s",
                mode="DNA",
                input_directory="/d",
                max_gpu_tasks=17,
            )

    def test_slurm_requires_user_and_profile(self):
        with pytest.raises(ValidationError):
            SubmitJobRequest(
                project_id="p",
                sample_name="s",
                mode="DNA",
                input_directory="/d",
                execution_mode="slurm",
            )

    def test_slurm_accepts_remote_base_path(self):
        req = SubmitJobRequest(
            project_id="p",
            user_id="user-1",
            sample_name="s",
            mode="DNA",
            input_directory="/d",
            execution_mode="slurm",
            ssh_profile_id="prof-1",
            slurm_account="lab",
            slurm_partition="gpu",
            remote_base_path="/remote/agoutic",
            result_destination="both",
        )
        assert req.execution_mode == "slurm"
        assert req.ssh_profile_id == "prof-1"
        assert req.remote_base_path == "/remote/agoutic"
        assert req.result_destination == "both"

    def test_slurm_accepts_staged_remote_input_reuse(self):
        req = SubmitJobRequest(
            project_id="p",
            user_id="user-1",
            sample_name="s",
            mode="DNA",
            input_directory="/d",
            execution_mode="slurm",
            ssh_profile_id="prof-1",
            staged_remote_input_path="/remote/agoutic/data/abc123",
        )
        assert req.staged_remote_input_path == "/remote/agoutic/data/abc123"

    def test_slurm_accepts_remote_input_path_without_local_input_directory(self):
        req = SubmitJobRequest(
            project_id="p",
            user_id="user-1",
            sample_name="s",
            mode="DNA",
            input_directory="",
            execution_mode="slurm",
            ssh_profile_id="prof-1",
            remote_input_path="/remote/agoutic/incoming/sample-a",
        )
        assert req.remote_input_path == "/remote/agoutic/incoming/sample-a"

    def test_local_execution_rejects_empty_input_directory(self):
        with pytest.raises(ValidationError):
            SubmitJobRequest(
                project_id="p",
                sample_name="s",
                mode="DNA",
                input_directory="",
            )

    def test_script_run_requires_explicit_script_selector(self):
        with pytest.raises(ValidationError):
            SubmitJobRequest(
                project_id="p",
                sample_name="script-job",
                mode="DNA",
                input_directory="/tmp",
                run_type="script",
            )

    def test_script_run_rejects_slurm_execution_mode(self):
        with pytest.raises(ValidationError):
            SubmitJobRequest(
                project_id="p",
                sample_name="script-job",
                mode="DNA",
                input_directory="/tmp",
                run_type="script",
                script_id="reconcileBams",
                execution_mode="slurm",
                user_id="user-1",
                ssh_profile_id="profile-1",
            )

    def test_script_run_accepts_script_id_or_script_path(self):
        req = SubmitJobRequest(
            project_id="p",
            sample_name="script-job",
            mode="DNA",
            input_directory="/tmp",
            run_type="script",
            script_id="reconcileBams",
            script_args=["--dry-run"],
        )
        assert req.run_type == "script"
        assert req.script_id == "reconcileBams"
        assert req.script_args == ["--dry-run"]


class TestStageRemoteSampleRequest:
    def test_normalizes_reference_genome_to_list(self):
        req = StageRemoteSampleRequest(
            project_id="proj-1",
            user_id="user-1",
            sample_name="Jamshid",
            mode="CDNA",
            input_directory="/data/pod5",
            ssh_profile_id="profile-1",
            reference_genome="mm39",
        )
        assert req.reference_genome == ["mm39"]

    def test_accepts_remote_input_path_without_local_input_directory(self):
        req = StageRemoteSampleRequest(
            project_id="proj-1",
            user_id="user-1",
            sample_name="Jamshid",
            mode="CDNA",
            input_directory="",
            remote_input_path="/remote/agoutic/incoming/Jamshid",
            ssh_profile_id="profile-1",
            reference_genome="mm39",
        )
        assert req.remote_input_path == "/remote/agoutic/incoming/Jamshid"


class TestJobStatusResponse:
    def test_valid(self):
        resp = JobStatusResponse(
            run_uuid="abc-123",
            status="RUNNING",
            progress_percent=45,
            message="Processing...",
        )
        assert resp.progress_percent == 45

    def test_with_tasks(self):
        resp = JobStatusResponse(
            run_uuid="abc",
            status="RUNNING",
            progress_percent=50,
            message="ok",
            tasks={"basecall": "COMPLETED", "align": "RUNNING"},
        )
        assert resp.tasks["basecall"] == "COMPLETED"


class TestJobDetailsResponse:
    def test_minimal(self):
        resp = JobDetailsResponse(
            run_uuid="abc",
            project_id="proj-1",
            sample_name="s1",
            mode="DNA",
            status="COMPLETED",
            progress_percent=100,
            submitted_at=None,
            started_at=None,
            completed_at=None,
            output_directory=None,
            error_message=None,
            report=None,
        )
        assert resp.status == "COMPLETED"
        assert resp.error_message is None


class TestJobSubmitResponse:
    def test_valid(self):
        resp = JobSubmitResponse(
            run_uuid="abc",
            sample_name="test",
            status="PENDING",
            work_directory="/data/work/test",
        )
        assert resp.work_directory == "/data/work/test"
