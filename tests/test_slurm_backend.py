"""Tests for SLURM backend pure-logic modules (no SSH/DB needed)."""
import pytest

from launchpad.backends.resource_validator import (
    ResourceLimits,
    parse_walltime,
    validate_resources,
)
from launchpad.backends.slurm_states import (
    explain_failure,
    explain_pending_reason,
    map_slurm_state,
)
from launchpad.backends.stage_machine import (
    RunStage,
    can_transition,
    get_stage_label,
    is_terminal,
)
from launchpad.backends.sbatch_generator import generate_sbatch_script


# ── Resource validation ──────────────────────────────────────────


class TestValidateResources:
    def test_valid_resources_pass(self):
        errors = validate_resources(cpus=4, memory_gb=16, walltime="04:00:00", gpus=0)
        assert errors == []

    def test_cpus_too_high(self):
        errors = validate_resources(cpus=999)
        assert any("CPUs" in e and "exceeds" in e for e in errors)

    def test_memory_too_low(self):
        errors = validate_resources(memory_gb=0)
        assert any("Memory" in e and "below" in e for e in errors)

    def test_invalid_walltime_format(self):
        errors = validate_resources(walltime="not-a-time")
        assert any("Invalid walltime" in e for e in errors)

    def test_negative_gpus(self):
        errors = validate_resources(gpus=-1)
        assert any("negative" in e for e in errors)

    def test_partition_not_in_whitelist(self):
        limits = ResourceLimits(allowed_partitions=["standard", "gpu"])
        errors = validate_resources(partition="secret", limits=limits)
        assert any("not in allowed partitions" in e for e in errors)

    def test_account_not_in_whitelist(self):
        limits = ResourceLimits(allowed_accounts=["lab_a", "lab_b"])
        errors = validate_resources(account="lab_z", limits=limits)
        assert any("not in allowed accounts" in e for e in errors)

    def test_custom_resource_limits(self):
        limits = ResourceLimits(max_cpus=8, max_memory_gb=32)
        errors = validate_resources(cpus=16, memory_gb=64, limits=limits)
        assert len(errors) == 2


# ── Walltime parsing ─────────────────────────────────────────────


class TestParseWalltime:
    def test_four_hours(self):
        assert parse_walltime("04:00:00") == 240

    def test_day_and_half(self):
        assert parse_walltime("1-12:00:00") == 2160

    def test_thirty_minutes(self):
        assert parse_walltime("00:30:00") == 30

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            parse_walltime("4hours")


# ── SLURM state mapping ─────────────────────────────────────────


class TestMapSlurmState:
    def test_pending(self):
        status, _ = map_slurm_state("PENDING")
        assert status == "PENDING"

    def test_running(self):
        status, _ = map_slurm_state("RUNNING")
        assert status == "RUNNING"

    def test_completed(self):
        status, _ = map_slurm_state("COMPLETED")
        assert status == "COMPLETED"

    def test_failed(self):
        status, _ = map_slurm_state("FAILED")
        assert status == "FAILED"

    def test_cancelled_with_suffix(self):
        status, _ = map_slurm_state("CANCELLED by 12345")
        assert status == "CANCELLED"

    def test_out_of_memory(self):
        status, _ = map_slurm_state("OUT_OF_MEMORY")
        assert status == "FAILED"

    def test_unknown_state(self):
        status, _ = map_slurm_state("UNKNOWN_STATE")
        assert status == "FAILED"


# ── Pending reason explanation ───────────────────────────────────


class TestExplainPendingReason:
    def test_known_reason(self):
        explanation = explain_pending_reason("Resources")
        assert "available" in explanation.lower()

    def test_unknown_reason(self):
        explanation = explain_pending_reason("SomeNewReason")
        assert "SomeNewReason" in explanation


# ── Failure explanation ──────────────────────────────────────────


class TestExplainFailure:
    def test_known_failure(self):
        explanation = explain_failure("OOM")
        assert "memory" in explanation.lower()

    def test_unknown_failure(self):
        explanation = explain_failure("WeirdError")
        assert "WeirdError" in explanation


# ── Stage transitions ────────────────────────────────────────────


class TestCanTransition:
    def test_none_to_submitting_valid(self):
        assert can_transition(None, RunStage.SUBMITTING_JOB) is True

    def test_none_to_completed_invalid(self):
        assert can_transition(None, RunStage.COMPLETED) is False

    def test_running_to_completed_valid(self):
        assert can_transition(RunStage.RUNNING, RunStage.COMPLETED) is True

    def test_completed_to_running_invalid(self):
        assert can_transition(RunStage.COMPLETED, RunStage.RUNNING) is False

    def test_queued_to_cancelled_valid(self):
        assert can_transition(RunStage.QUEUED, RunStage.CANCELLED) is True


# ── Stage labels ─────────────────────────────────────────────────


class TestGetStageLabel:
    def test_all_stages_have_labels(self):
        for stage in RunStage:
            label = get_stage_label(stage)
            assert isinstance(label, str)
            assert len(label) > 0

    def test_none_returns_not_started(self):
        assert get_stage_label(None) == "Not started"


# ── Terminal detection ───────────────────────────────────────────


class TestIsTerminal:
    @pytest.mark.parametrize("stage", [RunStage.COMPLETED, RunStage.FAILED, RunStage.CANCELLED])
    def test_terminal_stages(self, stage):
        assert is_terminal(stage) is True

    @pytest.mark.parametrize("stage", [RunStage.RUNNING, RunStage.QUEUED, RunStage.AWAITING_DETAILS])
    def test_non_terminal_stages(self, stage):
        assert is_terminal(stage) is False


# ── Sbatch script generation ────────────────────────────────────


class TestGenerateSbatchScript:
    def test_includes_all_directives(self):
        script = generate_sbatch_script(
            job_name="test_job",
            account="my_account",
            partition="standard",
            cpus=8,
            memory_gb=32,
            walltime="12:00:00",
        )
        assert "#SBATCH --account=my_account" in script
        assert "#SBATCH --partition=standard" in script
        assert "#SBATCH --cpus-per-task=8" in script
        assert "#SBATCH --mem=32G" in script
        assert "#SBATCH --time=12:00:00" in script
        assert "#SBATCH --output=" in script
        assert "#SBATCH --error=" in script

    def test_gpu_gres_when_gpus_positive(self):
        script = generate_sbatch_script(
            job_name="gpu_job",
            account="acc",
            partition="gpu",
            gpus=2,
        )
        assert "#SBATCH --gres=gpu:2" in script

    def test_no_gres_when_gpus_zero(self):
        script = generate_sbatch_script(
            job_name="cpu_job",
            account="acc",
            partition="standard",
            gpus=0,
        )
        assert "--gres" not in script

    def test_work_dir_sets_chdir(self):
        script = generate_sbatch_script(
            job_name="wd_job",
            account="acc",
            partition="standard",
            work_dir="/scratch/user/work",
        )
        assert "#SBATCH --chdir=/scratch/user/work" in script

    def test_module_loads_included(self):
        script = generate_sbatch_script(
            job_name="mod_job",
            account="acc",
            partition="standard",
            module_loads=["java/17", "singularity"],
        )
        assert "module load java/17" in script
        assert "module load singularity" in script

    def test_conda_env_included(self):
        script = generate_sbatch_script(
            job_name="conda_job",
            account="acc",
            partition="standard",
            conda_env="dogme_env",
        )
        assert "conda activate dogme_env" in script
