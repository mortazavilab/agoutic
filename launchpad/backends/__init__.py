"""
Launchpad execution backends.

Provides a unified ExecutionBackend protocol with local and SLURM implementations.
"""
from launchpad.backends.base import ExecutionBackend, SubmitParams, JobStatus
from launchpad.backends.local_backend import LocalBackend
from launchpad.backends.slurm_backend import SlurmBackend

_BACKENDS: dict[str, type[ExecutionBackend]] = {
    "local": LocalBackend,
    "slurm": SlurmBackend,
}


def get_backend(execution_mode: str) -> ExecutionBackend:
    """Return the appropriate backend for the given execution mode."""
    cls = _BACKENDS.get(execution_mode)
    if cls is None:
        raise ValueError(f"Unknown execution mode: {execution_mode!r}. Must be one of {list(_BACKENDS)}")
    return cls()
