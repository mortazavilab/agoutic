"""Workflow-family executors layered under Launchpad backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class WorkflowPreviewResult:
    """Structured preview returned by workflow executors."""

    workflow_key: str
    supports_submission: bool
    command: str
    preview_markdown: str
    preview_payload: dict[str, Any] = field(default_factory=dict)


class UnknownWorkflowKeyError(ValueError):
    """Raised when a workflow registry lookup fails."""


@runtime_checkable
class WorkflowExecutor(Protocol):
    """Workflow-family behavior layered under local/SLURM backends."""

    workflow_key: str
    supports_submission: bool

    def validate_submission(self, *, mode: str | None) -> None:
        """Validate a submission request for this workflow family."""
        ...

    def remote_validate_submission(self, *, request: Any) -> None:
        """Validate workflow-specific remote submission constraints."""
        ...

    async def remote_stage_inputs(
        self,
        *,
        request: Any,
        params: Any,
        profile: Any,
        conn: Any,
        run_uuid: str | None,
        on_progress: Any | None = None,
        transfer_id: str | None = None,
    ) -> dict[str, Any]:
        """Resolve or stage remote workflow inputs and return workflow-scoped paths."""
        ...

    async def remote_reference_assets(
        self,
        *,
        request: Any,
        params: Any,
        profile: Any,
        conn: Any,
        staged_inputs: dict[str, Any],
        run_uuid: str | None = None,
    ) -> dict[str, Any]:
        """Ensure workflow-required remote reference assets and sidecars exist."""
        ...

    def remote_work_dir_path(
        self,
        *,
        request: Any,
        params: Any,
        remote_paths: dict[str, str],
    ) -> str:
        """Return the workflow-specific remote Nextflow work dir."""
        ...

    async def remote_config_artifacts(
        self,
        *,
        request: Any,
        params: Any,
        profile: Any,
        conn: Any,
        remote_work: str,
        staged_inputs: dict[str, Any],
        reference_assets: dict[str, Any],
    ) -> dict[str, str]:
        """Return remote config/profile artifact contents keyed by relative path."""
        ...

    def remote_build_command(
        self,
        *,
        request: Any,
        params: Any,
        remote_work: str,
        remote_output: str,
        staged_inputs: dict[str, Any],
        reference_assets: dict[str, Any],
        rendered_files: dict[str, str],
        rerun_in_place: bool = False,
    ) -> str:
        """Build the concrete remote Nextflow command for the sbatch script."""
        ...

    def remote_result_sync_spec(
        self,
        *,
        request: Any,
        params: Any,
        staged_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Describe workflow-specific remote results for selective sync/import."""
        ...

    def remote_summary_contract(
        self,
        *,
        request: Any,
        params: Any,
        staged_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Describe workflow-specific remote summary metadata."""
        ...

    def build_local_submit_kwargs(
        self,
        *,
        run_uuid: str,
        request: Any,
        workflow_index: int | None,
        max_gpu_tasks: int | None,
    ) -> dict[str, Any]:
        """Build NextflowExecutor kwargs for local execution."""
        ...

    def build_backend_submit_params(
        self,
        *,
        request: Any,
        workflow_number: int | None,
        max_gpu_tasks: int | None,
    ) -> dict[str, Any]:
        """Build SubmitParams kwargs for backend execution."""
        ...

    def build_preview(self, **kwargs: Any) -> WorkflowPreviewResult:
        """Build a preview-only execution contract for the workflow family."""
        ...

    def validate_inputs(self, *, request: Any) -> dict[str, Any]:
        """Validate and normalize workflow-specific user inputs."""
        ...

    def stage_inputs(
        self,
        *,
        request: Any,
        work_dir: Path,
        validated_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Stage or normalize workflow inputs for local execution."""
        ...

    def render_nextflow_config(
        self,
        *,
        request: Any,
        work_dir: Path,
        staged_inputs: dict[str, Any],
        validated_inputs: dict[str, Any],
    ) -> dict[str, str]:
        """Render any workflow-scoped config artifacts keyed by relative path."""
        ...

    def build_command(
        self,
        *,
        request: Any,
        work_dir: Path,
        staged_inputs: dict[str, Any],
        rendered_files: dict[str, Path],
        validated_inputs: dict[str, Any],
    ) -> list[str]:
        """Build the concrete local Nextflow command."""
        ...

    def result_sync_spec(self, *, request: Any, validated_inputs: dict[str, Any]) -> dict[str, Any]:
        """Describe the workflow outputs that later sync/import code should care about."""
        ...

    def summary_contract(self, *, request: Any, validated_inputs: dict[str, Any]) -> dict[str, Any]:
        """Describe the workflow outputs Analyzer/Cortex will summarize later."""
        ...


def ensure_path(value: str | None) -> str:
    """Normalize a filesystem-like string for preview output."""

    return str(value or "").strip()


def sample_name_or_default(value: str | None, *, fallback_path: str | None = None) -> str:
    cleaned = str(value or "").strip()
    if cleaned:
        return cleaned
    if fallback_path:
        name = Path(fallback_path).name
        if name:
            return Path(name).stem or name
    return "sample"