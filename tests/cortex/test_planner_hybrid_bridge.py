import pytest

from cortex.planner import generate_plan
from cortex.schemas import ConversationState


class _Engine:
    def __init__(self, raw_response=None, *, raises: Exception | None = None):
        self._raw = raw_response
        self._raises = raises
        self.calls = 0

    def plan(self, _message, _state_json, _history):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._raw, {}


def _hybrid_plan_payload(plan_type: str) -> str:
    return (
        "[[PLAN:{"
        f"\"plan_type\":\"{plan_type}\","
        "\"title\":\"Hybrid\","
        "\"goal\":\"goal\","
        "\"steps\":[{"
        "\"id\":\"s1\","
        "\"kind\":\"LOCATE_DATA\","
        "\"title\":\"Locate\","
        "\"depends_on\":[]"
        "}]"
        "}]]"
    )


@pytest.mark.parametrize(
    ("plan_type", "message", "template_symbol"),
    [
        ("compare_samples", "compare sample_a and sample_b", "_template_compare_samples"),
        ("download_analyze", "download abc from encode and analyze", "_template_download_analyze"),
        ("summarize_results", "summarize the results", "_template_summarize_results"),
        ("parse_plot_interpret", "parse and plot my run", "_template_parse_plot_interpret"),
        ("compare_workflows", "compare workflows a and b", "_template_compare_workflows"),
        ("search_compare_to_local", "download abc from encode and compare to local", "_template_search_compare_to_local"),
    ],
)
def test_scoped_flows_use_hybrid_first_without_fallback_on_success(
    monkeypatch,
    plan_type,
    message,
    template_symbol,
):
    import cortex.planner as planner

    monkeypatch.setattr(planner, "_detect_plan_type", lambda _msg: plan_type)
    monkeypatch.setattr(planner, "_extract_plan_params", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        planner,
        template_symbol,
        lambda _params: (_ for _ in ()).throw(AssertionError("deterministic fallback should not run")),
    )

    engine = _Engine(_hybrid_plan_payload(plan_type))
    state = ConversationState(active_skill="welcome", active_project="proj-1", work_dir="/tmp")

    plan = generate_plan(message, "welcome", state, engine, conversation_history=[])

    assert engine.calls == 1
    assert plan is not None
    assert plan["plan_type"] == plan_type
    assert plan["project_id"] == "proj-1"
    assert isinstance(plan.get("plan_instance_id"), str)


@pytest.mark.parametrize(
    ("raw_response", "raises"),
    [
        (None, RuntimeError("engine boom")),
        ("", None),
        ("no plan tag", None),
        (
            "[[PLAN:{\"plan_type\":\"compare_samples\",\"title\":\"Hybrid\",\"goal\":\"g\","
            "\"fragments\":[{\"fragment_alias\":\"f1\",\"steps\":[{\"id\":\"x\",\"kind\":\"LOCATE_DATA\","
            "\"title\":\"X\",\"depends_on\":[\"missing\"]}]}]}]]",
            None,
        ),
        (
            "[[PLAN:{\"plan_type\":\"compare_samples\",\"title\":\"Hybrid\",\"goal\":\"g\","
            "\"steps\":[{\"id\":\"s1\",\"kind\":\"UNKNOWN_KIND\",\"title\":\"Bad\",\"depends_on\":[]}]}]]",
            None,
        ),
    ],
)
def test_scoped_flow_falls_back_to_deterministic_for_explicit_hybrid_failures(monkeypatch, raw_response, raises):
    import cortex.planner as planner

    monkeypatch.setattr(planner, "_detect_plan_type", lambda _msg: "compare_samples")
    monkeypatch.setattr(planner, "_extract_plan_params", lambda *_args, **_kwargs: {})

    deterministic_called = {"value": False}

    def _fake_template(_params):
        deterministic_called["value"] = True
        return {
            "plan_type": "compare_samples",
            "title": "deterministic",
            "goal": "g",
            "status": "PENDING",
            "auto_execute_safe_steps": True,
            "steps": [
                {
                    "id": "d1",
                    "kind": "LOCATE_DATA",
                    "title": "Locate",
                    "depends_on": [],
                    "requires_approval": False,
                }
            ],
            "artifacts": [],
        }

    monkeypatch.setattr(planner, "_template_compare_samples", _fake_template)

    engine = _Engine(raw_response, raises=raises)
    state = ConversationState(active_skill="welcome", active_project="proj-1")

    plan = generate_plan("compare a and b", "welcome", state, engine, conversation_history=[])

    assert plan is not None
    assert plan["plan_type"] == "compare_samples"
    assert deterministic_called["value"] is True


def test_scoped_flow_falls_back_on_plan_type_mismatch(monkeypatch):
    import cortex.planner as planner

    monkeypatch.setattr(planner, "_detect_plan_type", lambda _msg: "compare_samples")
    monkeypatch.setattr(planner, "_extract_plan_params", lambda *_args, **_kwargs: {})

    deterministic_called = {"value": False}

    def _fake_template(_params):
        deterministic_called["value"] = True
        return {
            "plan_type": "compare_samples",
            "title": "deterministic",
            "goal": "g",
            "status": "PENDING",
            "auto_execute_safe_steps": True,
            "steps": [
                {
                    "id": "d1",
                    "kind": "LOCATE_DATA",
                    "title": "Locate",
                    "depends_on": [],
                    "requires_approval": False,
                }
            ],
            "artifacts": [],
        }

    monkeypatch.setattr(planner, "_template_compare_samples", _fake_template)

    raw = _hybrid_plan_payload("download_analyze")
    engine = _Engine(raw)
    state = ConversationState(active_skill="welcome", active_project="proj-1")

    plan = generate_plan("compare a and b", "welcome", state, engine, conversation_history=[])

    assert plan is not None
    assert plan["plan_type"] == "compare_samples"
    assert deterministic_called["value"] is True


def test_core_deterministic_flows_do_not_attempt_hybrid(monkeypatch):
    import cortex.planner as planner

    monkeypatch.setattr(planner, "_detect_plan_type", lambda _msg: "run_de_pipeline")
    monkeypatch.setattr(planner, "_extract_plan_params", lambda *_args, **_kwargs: {})

    engine = _Engine(_hybrid_plan_payload("run_de_pipeline"))
    state = ConversationState(active_skill="welcome", active_project="proj-1")

    plan = generate_plan("run de analysis", "welcome", state, engine, conversation_history=[])

    assert plan is not None
    assert plan["plan_type"] == "run_de_pipeline"
    assert engine.calls == 0


def test_summarize_results_hybrid_success_returns_finalized_shape(monkeypatch):
    import cortex.planner as planner

    monkeypatch.setattr(planner, "_detect_plan_type", lambda _msg: None)

    engine = _Engine(_hybrid_plan_payload("summarize_results"))
    state = ConversationState(active_skill="welcome", active_project="proj-1", work_dir="/tmp/run")

    plan = generate_plan("summarize the results", "welcome", state, engine, conversation_history=[])

    assert plan is not None
    assert plan["plan_type"] == "summarize_results"
    assert plan["status"] == "PENDING"
    assert plan["current_step_id"] == "s1"
    assert plan["steps"][0]["status"] == "PENDING"
    assert plan["steps"][0]["order_index"] == 0
    assert isinstance(plan.get("plan_instance_id"), str)
