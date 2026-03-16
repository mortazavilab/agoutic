"""
SLURM state mapping: raw sacct/squeue states → AGOUTIC job states.
"""

# sacct/squeue state → (AGOUTIC status, human-readable label)
SLURM_STATE_MAP: dict[str, tuple[str, str]] = {
    # Active / queued states
    "PENDING": ("PENDING", "Job is queued"),
    "CONFIGURING": ("PENDING", "Job is being configured"),
    "REQUEUED": ("PENDING", "Job was requeued"),
    "SUSPENDED": ("PENDING", "Job is suspended"),
    "RESIZING": ("RUNNING", "Job is resizing"),
    "RUNNING": ("RUNNING", "Job is running"),
    "COMPLETING": ("RUNNING", "Job is completing"),
    "STAGE_OUT": ("RUNNING", "Job is staging out data"),

    # Success states
    "COMPLETED": ("COMPLETED", "Job completed successfully"),

    # Failure states
    "FAILED": ("FAILED", "Job failed"),
    "NODE_FAIL": ("FAILED", "Job failed due to node failure"),
    "OUT_OF_MEMORY": ("FAILED", "Job ran out of memory"),
    "TIMEOUT": ("FAILED", "Job exceeded walltime limit"),
    "DEADLINE": ("FAILED", "Job missed its deadline"),
    "BOOT_FAIL": ("FAILED", "Job failed during node boot"),

    # Cancelled states
    "CANCELLED": ("CANCELLED", "Job was cancelled"),
    "PREEMPTED": ("CANCELLED", "Job was preempted"),
    "REVOKED": ("CANCELLED", "Job was revoked"),
}

# Common SLURM pending reasons and human-readable explanations
PENDING_REASONS: dict[str, str] = {
    "Priority": "Waiting for higher-priority jobs to finish",
    "Resources": "Waiting for resources to become available",
    "QOSMaxJobsPerUserLimit": "You have reached the max concurrent jobs for this QOS",
    "QOSResourceLimit": "QOS resource limit reached",
    "AssocMaxJobsLimit": "Account has reached max concurrent jobs",
    "ReqNodeNotAvail": "Requested nodes are not currently available",
    "PartitionNodeLimit": "Partition node limit reached",
    "PartitionTimeLimit": "Requested walltime exceeds partition limit",
    "Dependency": "Waiting for a dependent job to complete",
    "BeginTime": "Job has a future start time",
    "JobHeldUser": "Job is held by user",
    "JobHeldAdmin": "Job is held by administrator",
}

# Failure reasons from sacct
FAILURE_REASONS: dict[str, str] = {
    "NonZeroExitCode": "The job's main process exited with a non-zero exit code",
    "OOM": "The job was killed because it exceeded its memory allocation",
    "TimeLimit": "The job exceeded its walltime limit — try increasing --time",
    "NodeFail": "A compute node failed during execution — consider resubmitting",
    "SystemFailure": "A system-level failure occurred — contact cluster admin",
}


def map_slurm_state(raw_state: str) -> tuple[str, str]:
    """Map a raw SLURM state string to (AGOUTIC_status, human_message).

    Handles states with suffixes like 'CANCELLED by 12345'.
    """
    # SLURM sometimes appends info: "CANCELLED by 12345"
    base_state = raw_state.split()[0].upper() if raw_state else "UNKNOWN"
    if base_state in SLURM_STATE_MAP:
        return SLURM_STATE_MAP[base_state]
    return ("FAILED", f"Unknown scheduler state: {raw_state}")


def explain_pending_reason(reason: str) -> str:
    """Return human-readable explanation for a SLURM pending reason."""
    return PENDING_REASONS.get(reason, f"Pending reason: {reason}")


def explain_failure(reason: str) -> str:
    """Return human-readable explanation for a SLURM failure reason."""
    return FAILURE_REASONS.get(reason, f"Failure reason: {reason}")
