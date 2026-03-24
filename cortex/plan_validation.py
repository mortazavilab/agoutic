from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlanValidationIssue:
    code: str
    path: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "context": dict(self.context),
        }


class PlanValidationError(Exception):
    def __init__(self, issues: list[PlanValidationIssue]) -> None:
        self.issues = issues
        super().__init__(f"Plan validation failed with {len(issues)} issue(s)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "plan_validation_failed",
            "issues": [issue.to_dict() for issue in self.issues],
        }


def validate_plan(
    plan_payload: dict[str, Any],
    *,
    allowed_kinds: set[str] | frozenset[str],
    safe_step_kinds: set[str] | frozenset[str],
    approval_step_kinds: set[str] | frozenset[str],
    expected_project_id: str | None = None,
) -> None:
    issues: list[PlanValidationIssue] = []

    if not isinstance(plan_payload, dict):
        issues.append(_issue(
            "invalid_plan_type",
            "plan",
            "Plan payload must be an object.",
            actual_type=type(plan_payload).__name__,
        ))
        raise PlanValidationError(issues)

    if expected_project_id:
        plan_project_id = plan_payload.get("project_id")
        if plan_project_id is not None and str(plan_project_id) != str(expected_project_id):
            issues.append(_issue(
                "project_id_mismatch",
                "project_id",
                "Plan project_id does not match workflow block project scope.",
                expected_project_id=str(expected_project_id),
                actual_project_id=str(plan_project_id),
            ))

    overlap = set(safe_step_kinds).intersection(set(approval_step_kinds))
    if overlap:
        issues.append(_issue(
            "policy_overlap",
            "policy",
            "Safe and approval-required step kind sets overlap.",
            overlapping_kinds=sorted(overlap),
        ))

    steps = plan_payload.get("steps")
    if not isinstance(steps, list):
        issues.append(_issue(
            "invalid_steps_type",
            "steps",
            "Plan steps must be a list.",
            actual_type=type(steps).__name__,
        ))
        raise PlanValidationError(issues)

    seen_ids: set[str] = set()
    dep_graph: dict[str, list[str]] = {}

    for index, step in enumerate(steps):
        step_path = f"steps[{index}]"
        if not isinstance(step, dict):
            issues.append(_issue(
                "invalid_step_type",
                step_path,
                "Step must be an object.",
                actual_type=type(step).__name__,
            ))
            continue

        step_id = step.get("id")
        if not isinstance(step_id, str) or not step_id.strip():
            issues.append(_issue(
                "missing_step_id",
                f"{step_path}.id",
                "Step id is required and must be a non-empty string.",
            ))
        elif step_id in seen_ids:
            issues.append(_issue(
                "duplicate_step_id",
                f"{step_path}.id",
                "Duplicate step id.",
                step_id=step_id,
            ))
        else:
            seen_ids.add(step_id)

        kind = step.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            issues.append(_issue(
                "missing_step_kind",
                f"{step_path}.kind",
                "Step kind is required and must be a non-empty string.",
                step_id=step_id,
            ))
        elif kind not in allowed_kinds:
            issues.append(_issue(
                "unknown_step_kind",
                f"{step_path}.kind",
                "Unknown step kind.",
                step_id=step_id,
                kind=kind,
                allowed_kinds=sorted(allowed_kinds),
            ))

        title = step.get("title")
        if not isinstance(title, str) or not title.strip():
            issues.append(_issue(
                "missing_step_title",
                f"{step_path}.title",
                "Step title is required and must be a non-empty string.",
                step_id=step_id,
            ))

        requires_approval = step.get("requires_approval")
        if requires_approval is not None and not isinstance(requires_approval, bool):
            issues.append(_issue(
                "invalid_requires_approval",
                f"{step_path}.requires_approval",
                "requires_approval must be a boolean when present.",
                step_id=step_id,
                actual_type=type(requires_approval).__name__,
            ))

        if kind == "REQUEST_APPROVAL" and step.get("requires_approval") is not True:
            issues.append(_issue(
                "invalid_request_approval_step",
                step_path,
                "REQUEST_APPROVAL steps must set requires_approval=true.",
                step_id=step_id,
            ))

        depends_on = step.get("depends_on", [])
        if depends_on is None:
            depends_on = []
        if not isinstance(depends_on, list):
            issues.append(_issue(
                "invalid_depends_on_type",
                f"{step_path}.depends_on",
                "depends_on must be a list of step ids.",
                step_id=step_id,
                actual_type=type(depends_on).__name__,
            ))
            depends_on = []

        seen_deps: set[str] = set()
        norm_deps: list[str] = []
        for dep_index, dep_id in enumerate(depends_on):
            dep_path = f"{step_path}.depends_on[{dep_index}]"
            if not isinstance(dep_id, str) or not dep_id.strip():
                issues.append(_issue(
                    "invalid_dependency_id",
                    dep_path,
                    "Dependency id must be a non-empty string.",
                    step_id=step_id,
                ))
                continue
            if dep_id in seen_deps:
                issues.append(_issue(
                    "duplicate_dependency",
                    dep_path,
                    "Duplicate dependency reference.",
                    step_id=step_id,
                    dependency_id=dep_id,
                ))
                continue
            seen_deps.add(dep_id)
            norm_deps.append(dep_id)
            if isinstance(step_id, str) and dep_id == step_id:
                issues.append(_issue(
                    "self_dependency",
                    dep_path,
                    "Step cannot depend on itself.",
                    step_id=step_id,
                ))

        if isinstance(step_id, str) and step_id.strip():
            dep_graph[step_id] = norm_deps

        tool_calls = step.get("tool_calls")
        if tool_calls is not None and not isinstance(tool_calls, list):
            issues.append(_issue(
                "invalid_tool_calls_type",
                f"{step_path}.tool_calls",
                "tool_calls must be a list when present.",
                step_id=step_id,
                actual_type=type(tool_calls).__name__,
            ))
            tool_calls = []

        if isinstance(tool_calls, list):
            for tc_idx, tool_call in enumerate(tool_calls):
                tc_path = f"{step_path}.tool_calls[{tc_idx}]"
                if not isinstance(tool_call, dict):
                    issues.append(_issue(
                        "invalid_tool_call",
                        tc_path,
                        "Each tool call must be an object.",
                        step_id=step_id,
                        actual_type=type(tool_call).__name__,
                    ))
                    continue
                source_key = tool_call.get("source_key")
                tool_name = tool_call.get("tool")
                if not isinstance(source_key, str) or not source_key.strip():
                    issues.append(_issue(
                        "invalid_tool_call_source_key",
                        f"{tc_path}.source_key",
                        "tool_call.source_key must be a non-empty string.",
                        step_id=step_id,
                    ))
                if not isinstance(tool_name, str) or not tool_name.strip():
                    issues.append(_issue(
                        "invalid_tool_call_tool",
                        f"{tc_path}.tool",
                        "tool_call.tool must be a non-empty string.",
                        step_id=step_id,
                    ))

        provenance = step.get("provenance")
        if provenance is not None:
            prov_path = f"{step_path}.provenance"
            if not isinstance(provenance, dict):
                issues.append(_issue(
                    "invalid_provenance_type",
                    prov_path,
                    "provenance must be an object when present.",
                    step_id=step_id,
                    actual_type=type(provenance).__name__,
                ))
            else:
                fragment_id = provenance.get("fragment_id")
                if not isinstance(fragment_id, str) or not fragment_id.strip():
                    issues.append(_issue(
                        "invalid_provenance_fragment_id",
                        f"{prov_path}.fragment_id",
                        "provenance.fragment_id must be a non-empty string.",
                        step_id=step_id,
                    ))
                for key in ("fragment_version", "source_template", "composed_at"):
                    value = provenance.get(key)
                    if value is not None and not isinstance(value, str):
                        issues.append(_issue(
                            "invalid_provenance_field",
                            f"{prov_path}.{key}",
                            f"provenance.{key} must be a string when present.",
                            step_id=step_id,
                            actual_type=type(value).__name__,
                        ))

    for step_id, deps in dep_graph.items():
        for dep in deps:
            if dep not in seen_ids:
                issues.append(_issue(
                    "unknown_dependency",
                    "steps",
                    "Dependency references a missing step id.",
                    step_id=step_id,
                    dependency_id=dep,
                ))

    if not any(issue.code == "unknown_dependency" for issue in issues):
        cycle = _find_cycle(dep_graph)
        if cycle:
            issues.append(_issue(
                "dependency_cycle",
                "steps",
                "Step dependencies contain a cycle.",
                cycle=cycle,
            ))

    if issues:
        raise PlanValidationError(issues)


def _find_cycle(graph: dict[str, list[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        if node in visiting:
            if node in stack:
                idx = stack.index(node)
                return stack[idx:] + [node]
            return [node, node]
        if node in visited:
            return None

        visiting.add(node)
        stack.append(node)
        for dep in graph.get(node, []):
            found = dfs(dep)
            if found:
                return found
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return None

    for node in graph:
        found = dfs(node)
        if found:
            return found
    return []


def _issue(code: str, path: str, message: str, **context: Any) -> PlanValidationIssue:
    return PlanValidationIssue(code=code, path=path, message=message, context=context)
