"""UI-local runtime defaults and environment-backed settings."""

import os


SLURM_DEFAULT_CPU_MEMORY_GB = int(os.getenv("SLURM_DEFAULT_CPU_MEMORY_GB", "64"))