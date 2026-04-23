"""
SLURM resource validation — configurable limits and safe defaults.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from launchpad.config import (
    SLURM_ALLOWED_ACCOUNTS,
    SLURM_ALLOWED_PARTITIONS,
    SLURM_MAX_CPUS,
    SLURM_MAX_GPUS,
    SLURM_MAX_MEMORY_GB,
    SLURM_MAX_WALLTIME_MINUTES,
    SLURM_MIN_WALLTIME_MINUTES,
)


@dataclass
class ResourceLimits:
    """Configurable limits for SLURM resource requests."""
    min_cpus: int = 1
    max_cpus: int = 128
    min_memory_gb: int = 1
    max_memory_gb: int = 1024
    min_walltime_minutes: int = 2880  # 48 hours
    max_walltime_minutes: int = 4320  # 72 hours
    max_gpus: int = 8
    allowed_partitions: list[str] | None = None  # None = any partition allowed
    allowed_accounts: list[str] | None = None  # None = any account allowed
    allowed_gpu_types: list[str] | None = None  # None = any GPU type allowed


# Default safe limits (can be overridden via config/env)
DEFAULT_LIMITS = ResourceLimits(
    max_cpus=SLURM_MAX_CPUS,
    max_memory_gb=SLURM_MAX_MEMORY_GB,
    min_walltime_minutes=SLURM_MIN_WALLTIME_MINUTES,
    max_walltime_minutes=SLURM_MAX_WALLTIME_MINUTES,
    max_gpus=SLURM_MAX_GPUS,
    allowed_partitions=SLURM_ALLOWED_PARTITIONS,
    allowed_accounts=SLURM_ALLOWED_ACCOUNTS,
)

# Safe presets for common use cases
SAFE_PRESETS: dict[str, dict] = {
    "small": {"cpus": 4, "memory_gb": 16, "walltime": "48:00:00", "gpus": 0},
    "medium": {"cpus": 16, "memory_gb": 64, "walltime": "48:00:00", "gpus": 0},
    "large": {"cpus": 32, "memory_gb": 128, "walltime": "72:00:00", "gpus": 0},
    "gpu_small": {"cpus": 8, "memory_gb": 32, "walltime": "48:00:00", "gpus": 1},
    "gpu_large": {"cpus": 16, "memory_gb": 64, "walltime": "72:00:00", "gpus": 4},
}


def parse_walltime(walltime: str) -> int:
    """Parse HH:MM:SS or D-HH:MM:SS walltime string to total minutes."""
    # D-HH:MM:SS format
    m = re.match(r"^(\d+)-(\d{1,2}):(\d{2}):(\d{2})$", walltime)
    if m:
        days, hours, mins, secs = int(m[1]), int(m[2]), int(m[3]), int(m[4])
        return days * 24 * 60 + hours * 60 + mins + (1 if secs > 0 else 0)

    # HH:MM:SS format
    m = re.match(r"^(\d{1,3}):(\d{2}):(\d{2})$", walltime)
    if m:
        hours, mins, secs = int(m[1]), int(m[2]), int(m[3])
        return hours * 60 + mins + (1 if secs > 0 else 0)

    raise ValueError(f"Invalid walltime format: {walltime!r}. Use HH:MM:SS or D-HH:MM:SS")


def validate_resources(
    cpus: int | None = None,
    memory_gb: int | None = None,
    walltime: str | None = None,
    gpus: int | None = None,
    gpu_type: str | None = None,
    partition: str | None = None,
    account: str | None = None,
    limits: ResourceLimits | None = None,
) -> list[str]:
    """Validate SLURM resource requests against limits.

    Returns a list of error messages. Empty list means all valid.
    """
    lim = limits or DEFAULT_LIMITS
    errors: list[str] = []

    if cpus is not None:
        if cpus < lim.min_cpus:
            errors.append(f"CPUs ({cpus}) below minimum ({lim.min_cpus})")
        if cpus > lim.max_cpus:
            errors.append(f"CPUs ({cpus}) exceeds maximum ({lim.max_cpus})")

    if memory_gb is not None:
        if memory_gb < lim.min_memory_gb:
            errors.append(f"Memory ({memory_gb} GB) below minimum ({lim.min_memory_gb} GB)")
        if memory_gb > lim.max_memory_gb:
            errors.append(f"Memory ({memory_gb} GB) exceeds maximum ({lim.max_memory_gb} GB)")

    if walltime is not None:
        try:
            minutes = parse_walltime(walltime)
            if minutes < lim.min_walltime_minutes:
                errors.append(f"Walltime ({walltime}) below minimum ({lim.min_walltime_minutes} minutes)")
            if minutes > lim.max_walltime_minutes:
                errors.append(f"Walltime ({walltime}) exceeds maximum ({lim.max_walltime_minutes} minutes)")
        except ValueError as e:
            errors.append(str(e))

    if gpus is not None:
        if gpus < 0:
            errors.append(f"GPUs ({gpus}) cannot be negative")
        if gpus > lim.max_gpus:
            errors.append(f"GPUs ({gpus}) exceeds maximum ({lim.max_gpus})")

    if gpu_type is not None and lim.allowed_gpu_types is not None:
        if gpu_type not in lim.allowed_gpu_types:
            errors.append(f"GPU type {gpu_type!r} not in allowed types: {lim.allowed_gpu_types}")

    if partition is not None and lim.allowed_partitions is not None:
        if partition not in lim.allowed_partitions:
            errors.append(f"Partition {partition!r} not in allowed partitions: {lim.allowed_partitions}")

    if account is not None and lim.allowed_accounts is not None:
        if account not in lim.allowed_accounts:
            errors.append(f"Account {account!r} not in allowed accounts: {lim.allowed_accounts}")

    return errors
