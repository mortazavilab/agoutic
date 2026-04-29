"""UI-local runtime defaults and environment-backed settings."""

import os


SLURM_DEFAULT_CPU_MEMORY_GB = int(os.getenv("SLURM_DEFAULT_CPU_MEMORY_GB", "64"))
LOCAL_DEFAULT_MAX_TASK_MEMORY_GB = int(os.getenv("LOCAL_DEFAULT_MAX_TASK_MEMORY_GB", "64"))