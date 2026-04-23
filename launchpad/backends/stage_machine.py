"""
Run stage state machine for tracking detailed execution progress.
"""
from __future__ import annotations

from enum import Enum


class RunStage(str, Enum):
    """Detailed run stages for both local and remote execution."""
    # Preparation stages
    AWAITING_DETAILS = "awaiting_details"
    AWAITING_APPROVAL = "awaiting_approval"
    VALIDATING_CONNECTION = "validating_connection"
    PREPARING_REMOTE_DIRS = "preparing_remote_dirs"
    TRANSFERRING_INPUTS = "transferring_inputs"

    # Execution stages
    SUBMITTING_JOB = "submitting_job"
    QUEUED = "queued"
    RUNNING = "running"

    # Post-execution stages
    COLLECTING_OUTPUTS = "collecting_outputs"
    SYNCING_RESULTS = "syncing_results"

    # Terminal stages
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Valid transitions: from_stage → set of allowed next stages
VALID_TRANSITIONS: dict[RunStage | None, set[RunStage]] = {
    None: {RunStage.AWAITING_DETAILS, RunStage.SUBMITTING_JOB},
    RunStage.AWAITING_DETAILS: {RunStage.AWAITING_APPROVAL, RunStage.SUBMITTING_JOB, RunStage.VALIDATING_CONNECTION, RunStage.CANCELLED},
    RunStage.AWAITING_APPROVAL: {RunStage.VALIDATING_CONNECTION, RunStage.SUBMITTING_JOB, RunStage.CANCELLED},
    RunStage.VALIDATING_CONNECTION: {RunStage.PREPARING_REMOTE_DIRS, RunStage.FAILED},
    RunStage.PREPARING_REMOTE_DIRS: {RunStage.TRANSFERRING_INPUTS, RunStage.SUBMITTING_JOB, RunStage.FAILED},
    RunStage.TRANSFERRING_INPUTS: {RunStage.SUBMITTING_JOB, RunStage.FAILED},
    RunStage.SUBMITTING_JOB: {RunStage.QUEUED, RunStage.RUNNING, RunStage.FAILED},
    RunStage.QUEUED: {RunStage.RUNNING, RunStage.CANCELLED, RunStage.FAILED},
    RunStage.RUNNING: {RunStage.COLLECTING_OUTPUTS, RunStage.COMPLETED, RunStage.FAILED, RunStage.CANCELLED},
    RunStage.COLLECTING_OUTPUTS: {RunStage.SYNCING_RESULTS, RunStage.COMPLETED, RunStage.FAILED},
    RunStage.SYNCING_RESULTS: {RunStage.COMPLETED, RunStage.FAILED},
    # Terminal stages allow no transitions
    RunStage.COMPLETED: set(),
    RunStage.FAILED: set(),
    RunStage.CANCELLED: set(),
}

# Human-readable labels for UI display
STAGE_LABELS: dict[RunStage, str] = {
    RunStage.AWAITING_DETAILS: "Awaiting details",
    RunStage.AWAITING_APPROVAL: "Awaiting approval",
    RunStage.VALIDATING_CONNECTION: "Validating connection",
    RunStage.PREPARING_REMOTE_DIRS: "Preparing remote directories",
    RunStage.TRANSFERRING_INPUTS: "Transferring inputs",
    RunStage.SUBMITTING_JOB: "Submitting job",
    RunStage.QUEUED: "Queued on scheduler",
    RunStage.RUNNING: "Running",
    RunStage.COLLECTING_OUTPUTS: "Collecting outputs",
    RunStage.SYNCING_RESULTS: "Syncing results to local",
    RunStage.COMPLETED: "Completed",
    RunStage.FAILED: "Failed",
    RunStage.CANCELLED: "Cancelled",
}


def can_transition(from_stage: RunStage | None, to_stage: RunStage) -> bool:
    """Check if a stage transition is valid."""
    allowed = VALID_TRANSITIONS.get(from_stage, set())
    return to_stage in allowed


def get_stage_label(stage: RunStage | str | None) -> str:
    """Get human-readable label for a stage."""
    if stage is None:
        return "Not started"
    if isinstance(stage, str):
        try:
            stage = RunStage(stage)
        except ValueError:
            return stage
    return STAGE_LABELS.get(stage, str(stage))


def is_terminal(stage: RunStage | str | None) -> bool:
    """Check if the stage is terminal (no further transitions possible)."""
    if stage is None:
        return False
    if isinstance(stage, str):
        try:
            stage = RunStage(stage)
        except ValueError:
            return False
    return stage in {RunStage.COMPLETED, RunStage.FAILED, RunStage.CANCELLED}
