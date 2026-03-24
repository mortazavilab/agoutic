import pytest

from cortex.plan_validation import PlanValidationError, validate_plan


_SAFE = frozenset({"LOCATE_DATA", "VALIDATE_INPUTS"})
_APPROVAL = frozenset({"REQUEST_APPROVAL", "SUBMIT_WORKFLOW", "RUN_SCRIPT"})
_ALLOWED = {
    "LOCATE_DATA",
    "VALIDATE_INPUTS",
    "WRITE_SUMMARY",
    "REQUEST_APPROVAL",
    "SUBMIT_WORKFLOW",
    "RUN_SCRIPT",
}


def _base_plan() -> dict:
    return {
        "title": "plan",
        "project_id": "proj-1",
        "plan_instance_id": "pi-1",
        "steps": [
            {
                "id": "step_a",
                "kind": "LOCATE_DATA",
                "title": "Locate",
                "depends_on": [],
                "requires_approval": False,
            },
            {
                "id": "step_b",
                "kind": "WRITE_SUMMARY",
                "title": "Summarize",
                "depends_on": ["step_a"],
                "requires_approval": False,
            },
        ],
    }


def _validate(payload: dict, *, expected_project_id: str | None = "proj-1") -> None:
    validate_plan(
        payload,
        allowed_kinds=_ALLOWED,
        safe_step_kinds=_SAFE,
        approval_step_kinds=_APPROVAL,
        expected_project_id=expected_project_id,
    )


def _codes(exc: PlanValidationError) -> set[str]:
    return {issue.code for issue in exc.issues}


def test_validate_plan_accepts_valid_payload() -> None:
    _validate(_base_plan())


def test_validate_plan_rejects_unknown_kind() -> None:
    payload = _base_plan()
    payload["steps"][1]["kind"] = "UNKNOWN"
    with pytest.raises(PlanValidationError) as exc:
        _validate(payload)
    assert "unknown_step_kind" in _codes(exc.value)


def test_validate_plan_rejects_duplicate_ids() -> None:
    payload = _base_plan()
    payload["steps"][1]["id"] = "step_a"
    with pytest.raises(PlanValidationError) as exc:
        _validate(payload)
    assert "duplicate_step_id" in _codes(exc.value)


def test_validate_plan_rejects_unknown_dependency() -> None:
    payload = _base_plan()
    payload["steps"][1]["depends_on"] = ["missing"]
    with pytest.raises(PlanValidationError) as exc:
        _validate(payload)
    assert "unknown_dependency" in _codes(exc.value)


def test_validate_plan_rejects_self_dependency() -> None:
    payload = _base_plan()
    payload["steps"][0]["depends_on"] = ["step_a"]
    with pytest.raises(PlanValidationError) as exc:
        _validate(payload)
    assert "self_dependency" in _codes(exc.value)


def test_validate_plan_rejects_dependency_cycle() -> None:
    payload = _base_plan()
    payload["steps"][0]["depends_on"] = ["step_b"]
    with pytest.raises(PlanValidationError) as exc:
        _validate(payload)
    assert "dependency_cycle" in _codes(exc.value)


def test_validate_plan_request_approval_requires_flag() -> None:
    payload = _base_plan()
    payload["steps"][1]["kind"] = "REQUEST_APPROVAL"
    payload["steps"][1]["requires_approval"] = False
    with pytest.raises(PlanValidationError) as exc:
        _validate(payload)
    assert "invalid_request_approval_step" in _codes(exc.value)


def test_validate_plan_accepts_provenance() -> None:
    payload = _base_plan()
    payload["steps"][0]["provenance"] = {
        "fragment_id": "frag_a",
        "fragment_version": "1",
        "source_template": "foo",
        "composed_at": "2026-03-24T00:00:00Z",
        "future_extra": {"x": 1},
    }
    _validate(payload)


def test_validate_plan_rejects_bad_provenance_type() -> None:
    payload = _base_plan()
    payload["steps"][0]["provenance"] = "bad"
    with pytest.raises(PlanValidationError) as exc:
        _validate(payload)
    assert "invalid_provenance_type" in _codes(exc.value)


def test_validate_plan_rejects_project_scope_mismatch() -> None:
    payload = _base_plan()
    payload["project_id"] = "proj-2"
    with pytest.raises(PlanValidationError) as exc:
        _validate(payload, expected_project_id="proj-1")
    assert "project_id_mismatch" in _codes(exc.value)
