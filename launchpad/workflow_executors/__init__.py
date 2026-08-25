"""Workflow-family executor registry."""

from __future__ import annotations

from launchpad.workflow_executors.base import UnknownWorkflowKeyError, WorkflowExecutor
from launchpad.workflow_executors.dogme import DogmeWorkflowExecutor
from launchpad.workflow_executors.wf_pore_c import WfPoreCWorkflowExecutor


_REGISTRY: dict[str, WorkflowExecutor] = {
    "dogme": DogmeWorkflowExecutor(),
    "wf_pore_c": WfPoreCWorkflowExecutor(),
}


def normalize_workflow_key(value: str | None) -> str:
    cleaned = str(value or "dogme").strip().lower()
    return cleaned or "dogme"


def get_workflow_executor(workflow_key: str | None) -> WorkflowExecutor:
    normalized_key = normalize_workflow_key(workflow_key)
    executor = _REGISTRY.get(normalized_key)
    if executor is None:
        known = ", ".join(sorted(_REGISTRY))
        raise UnknownWorkflowKeyError(
            f"Unknown workflow_key '{normalized_key}'. Known workflow keys: {known}."
        )
    return executor


def workflow_executor_keys() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))