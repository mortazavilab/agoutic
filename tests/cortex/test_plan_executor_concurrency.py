import asyncio
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.db_helpers import _create_block_internal
from cortex.models import ProjectBlock, ProjectTask
from cortex.plan_executor import execute_plan


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _make_plan_payload(*, project_id: str, plan_instance_id: str, step_prefix: str) -> dict:
    return {
        "title": "Concurrent Workflow",
        "project_id": project_id,
        "plan_instance_id": plan_instance_id,
        "steps": [
            {
                "id": f"{step_prefix}_s1",
                "kind": "FIND_DATA_CACHE",
                "title": "Check data cache",
                "depends_on": [],
                "requires_approval": False,
            },
            {
                "id": f"{step_prefix}_s2",
                "kind": "FIND_REFERENCE_CACHE",
                "title": "Check reference cache",
                "depends_on": [f"{step_prefix}_s1"],
                "requires_approval": False,
            },
        ],
    }


def _load_payload(session_factory, block_id: str) -> tuple[ProjectBlock, dict]:
    session = session_factory()
    block = session.execute(
        select(ProjectBlock).where(ProjectBlock.id == block_id)
    ).scalar_one()
    payload = json.loads(block.payload_json)
    session.close()
    return block, payload


def _create_workflow_block(session_factory, *, project_id: str, owner_id: str, payload: dict):
    setup_session = session_factory()
    block = _create_block_internal(
        setup_session,
        project_id,
        "WORKFLOW_PLAN",
        payload,
        status="PENDING",
        owner_id=owner_id,
    )
    setup_session.close()
    return block


@pytest.mark.asyncio
async def test_execute_plan_concurrent_projects_remain_isolated(session_factory, monkeypatch):
    from cortex import plan_executor

    setup_session = session_factory()
    block_a = _create_block_internal(
        setup_session,
        "proj-conc-a",
        "WORKFLOW_PLAN",
        _make_plan_payload(project_id="proj-conc-a", plan_instance_id="plan-a", step_prefix="a"),
        status="PENDING",
        owner_id="owner-a",
    )
    block_b = _create_block_internal(
        setup_session,
        "proj-conc-b",
        "WORKFLOW_PLAN",
        _make_plan_payload(project_id="proj-conc-b", plan_instance_id="plan-b", step_prefix="b"),
        status="PENDING",
        owner_id="owner-b",
    )
    setup_session.close()

    original_execute_step = plan_executor.execute_step

    async def _delayed_execute_step(*args, **kwargs):
        # Force scheduler interleaving so both plans advance concurrently.
        await asyncio.sleep(0.01)
        return await original_execute_step(*args, **kwargs)

    monkeypatch.setattr(plan_executor, "execute_step", _delayed_execute_step)

    session_a = session_factory()
    session_b = session_factory()
    wf_a = session_a.execute(select(ProjectBlock).where(ProjectBlock.id == block_a.id)).scalar_one()
    wf_b = session_b.execute(select(ProjectBlock).where(ProjectBlock.id == block_b.id)).scalar_one()

    await asyncio.gather(
        execute_plan(session_a, wf_a, project_id="proj-conc-a"),
        execute_plan(session_b, wf_b, project_id="proj-conc-b"),
    )
    session_a.close()
    session_b.close()

    block_a_final, payload_a = _load_payload(session_factory, block_a.id)
    block_b_final, payload_b = _load_payload(session_factory, block_b.id)

    assert block_a_final.project_id == "proj-conc-a"
    assert block_b_final.project_id == "proj-conc-b"
    assert block_a_final.status == "DONE"
    assert block_b_final.status == "DONE"
    assert payload_a["status"] == "COMPLETED"
    assert payload_b["status"] == "COMPLETED"
    assert payload_a["project_id"] == "proj-conc-a"
    assert payload_b["project_id"] == "proj-conc-b"
    assert payload_a["plan_instance_id"] == "plan-a"
    assert payload_b["plan_instance_id"] == "plan-b"
    assert {s["id"] for s in payload_a["steps"]} == {"a_s1", "a_s2"}
    assert {s["id"] for s in payload_b["steps"]} == {"b_s1", "b_s2"}
    assert all(step["status"] == "COMPLETED" for step in payload_a["steps"])
    assert all(step["status"] == "COMPLETED" for step in payload_b["steps"])


@pytest.mark.asyncio
async def test_execute_plan_concurrent_instances_same_project_keep_plan_instance_scope(session_factory, monkeypatch):
    from cortex import plan_executor

    setup_session = session_factory()
    block_1 = _create_block_internal(
        setup_session,
        "proj-conc-shared",
        "WORKFLOW_PLAN",
        _make_plan_payload(project_id="proj-conc-shared", plan_instance_id="plan-1", step_prefix="p1"),
        status="PENDING",
        owner_id="owner-shared",
    )
    block_2 = _create_block_internal(
        setup_session,
        "proj-conc-shared",
        "WORKFLOW_PLAN",
        _make_plan_payload(project_id="proj-conc-shared", plan_instance_id="plan-2", step_prefix="p2"),
        status="PENDING",
        owner_id="owner-shared",
    )
    setup_session.close()

    original_execute_step = plan_executor.execute_step

    async def _delayed_execute_step(*args, **kwargs):
        await asyncio.sleep(0.01)
        return await original_execute_step(*args, **kwargs)

    monkeypatch.setattr(plan_executor, "execute_step", _delayed_execute_step)

    session_1 = session_factory()
    session_2 = session_factory()
    wf_1 = session_1.execute(select(ProjectBlock).where(ProjectBlock.id == block_1.id)).scalar_one()
    wf_2 = session_2.execute(select(ProjectBlock).where(ProjectBlock.id == block_2.id)).scalar_one()

    await asyncio.gather(
        execute_plan(session_1, wf_1, project_id="proj-conc-shared"),
        execute_plan(session_2, wf_2, project_id="proj-conc-shared"),
    )
    session_1.close()
    session_2.close()

    block_1_final, payload_1 = _load_payload(session_factory, block_1.id)
    block_2_final, payload_2 = _load_payload(session_factory, block_2.id)

    assert block_1_final.status == "DONE"
    assert block_2_final.status == "DONE"
    assert payload_1["status"] == "COMPLETED"
    assert payload_2["status"] == "COMPLETED"
    assert payload_1["plan_instance_id"] == "plan-1"
    assert payload_2["plan_instance_id"] == "plan-2"
    assert {s["id"] for s in payload_1["steps"]} == {"p1_s1", "p1_s2"}
    assert {s["id"] for s in payload_2["steps"]} == {"p2_s1", "p2_s2"}
    assert all(step["status"] == "COMPLETED" for step in payload_1["steps"])
    assert all(step["status"] == "COMPLETED" for step in payload_2["steps"])

    verify_session = session_factory()
    workflow_tasks = verify_session.execute(
        select(ProjectTask).where(
            ProjectTask.project_id == "proj-conc-shared",
            ProjectTask.kind == "workflow_plan",
        )
    ).scalars().all()
    step_tasks = verify_session.execute(
        select(ProjectTask).where(
            ProjectTask.project_id == "proj-conc-shared",
            ProjectTask.source_key.like("workflow-step:%"),
        )
    ).scalars().all()
    verify_session.close()

    assert len(workflow_tasks) == 2
    assert {task.source_id for task in workflow_tasks} == {block_1.id, block_2.id}
    assert len(step_tasks) >= 4
    assert any(f"workflow-step:{block_1.id}:p1_s1" == task.source_key for task in step_tasks)
    assert any(f"workflow-step:{block_2.id}:p2_s1" == task.source_key for task in step_tasks)


@pytest.mark.asyncio
async def test_parallel_safe_steps_run_concurrently_and_persist_in_plan_order(session_factory, monkeypatch):
    from cortex import plan_executor

    payload = {
        "title": "Parallel safe",
        "project_id": "proj-par",
        "plan_instance_id": "pi-par",
        "steps": [
            {
                "id": "b_step",
                "order_index": 0,
                "kind": "SEARCH_ENCODE",
                "title": "Search",
                "depends_on": [],
                "requires_approval": False,
            },
            {
                "id": "a_step",
                "order_index": 1,
                "kind": "LOCATE_DATA",
                "title": "Locate",
                "depends_on": [],
                "requires_approval": False,
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-par",
        owner_id="owner-par",
        payload=payload,
    )

    active = {"count": 0, "max": 0}
    completed_order: list[str] = []
    completed_seen: set[str] = set()

    async def _fake_call(source_key, tool_name, params):
        active["count"] += 1
        active["max"] = max(active["max"], active["count"])
        await asyncio.sleep(0.03 if tool_name == "search_by_biosample" else 0.005)
        active["count"] -= 1
        return {"ok": True, "tool": tool_name, "source_key": source_key, "params": params}

    original_persist = plan_executor._persist_step_update

    def _capturing_persist(session, workflow_block, plan_payload):
        for step in plan_payload.get("steps", []):
            step_id = step.get("id")
            if step.get("status") == "COMPLETED" and isinstance(step_id, str) and step_id not in completed_seen:
                completed_seen.add(step_id)
                completed_order.append(step_id)
        return original_persist(session, workflow_block, plan_payload)

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)
    monkeypatch.setattr(plan_executor, "_persist_step_update", _capturing_persist)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-par")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    assert final_payload["status"] == "COMPLETED"
    assert active["max"] >= 2
    # Deterministic persistence order follows plan order, not completion timing.
    assert completed_order[:2] == ["b_step", "a_step"]


@pytest.mark.asyncio
async def test_dependent_step_waits_until_parallel_dependencies_complete(session_factory, monkeypatch):
    from cortex import plan_executor

    payload = {
        "title": "Deps wait",
        "project_id": "proj-deps",
        "plan_instance_id": "pi-deps",
        "steps": [
            {
                "id": "s_loc",
                "order_index": 0,
                "kind": "LOCATE_DATA",
                "title": "Locate",
                "depends_on": [],
                "requires_approval": False,
            },
            {
                "id": "s_search",
                "order_index": 1,
                "kind": "SEARCH_ENCODE",
                "title": "Search",
                "depends_on": [],
                "requires_approval": False,
            },
            {
                "id": "s_check",
                "order_index": 2,
                "kind": "CHECK_EXISTING",
                "title": "Check",
                "depends_on": ["s_loc", "s_search"],
                "requires_approval": False,
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-deps",
        owner_id="owner-deps",
        payload=payload,
    )

    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    async def _fake_call(_source_key, tool_name, _params):
        loop_time = asyncio.get_running_loop().time()
        starts[tool_name] = loop_time
        await asyncio.sleep(0.02 if tool_name == "search_by_biosample" else 0.005)
        ends[tool_name] = asyncio.get_running_loop().time()
        return {"ok": True}

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-deps")
    session.close()

    assert "find_file" in starts
    assert starts["find_file"] >= ends["list_job_files"]
    assert starts["find_file"] >= ends["search_by_biosample"]


@pytest.mark.asyncio
async def test_sequential_ready_step_is_not_parallelized_when_safe_batch_exists(session_factory, monkeypatch):
    from cortex import plan_executor

    payload = {
        "title": "Mixed batch",
        "project_id": "proj-mixed",
        "plan_instance_id": "pi-mixed",
        "steps": [
            {
                "id": "s_loc",
                "order_index": 0,
                "kind": "LOCATE_DATA",
                "title": "Locate",
                "depends_on": [],
                "requires_approval": False,
            },
            {
                "id": "s_script",
                "order_index": 1,
                "kind": "RUN_SCRIPT",
                "title": "Run script",
                "depends_on": [],
                "requires_approval": False,
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-mixed",
        owner_id="owner-mixed",
        payload=payload,
    )

    seen = {"run_script_called": False}
    original_execute_step = plan_executor.execute_step

    async def _fake_call(_source_key, _tool_name, _params):
        return {"ok": True}

    async def _wrapped_execute_step(*args, **kwargs):
        step_id = args[2]
        plan_payload = kwargs.get("plan_payload") or {}
        if step_id == "s_script":
            locate = next(s for s in plan_payload.get("steps", []) if s.get("id") == "s_loc")
            assert locate.get("status") == "COMPLETED"
            seen["run_script_called"] = True
        return await original_execute_step(*args, **kwargs)

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)
    monkeypatch.setattr(plan_executor, "execute_step", _wrapped_execute_step)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-mixed")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    assert seen["run_script_called"] is True
    assert next(s for s in final_payload["steps"] if s["id"] == "s_loc")["status"] == "COMPLETED"
    assert next(s for s in final_payload["steps"] if s["id"] == "s_script")["status"] == "PENDING"


@pytest.mark.asyncio
async def test_parallel_batch_failure_triggers_existing_failure_path(session_factory, monkeypatch):
    from cortex import plan_executor

    payload = {
        "title": "Parallel fail",
        "project_id": "proj-fail",
        "plan_instance_id": "pi-fail",
        "steps": [
            {
                "id": "s_loc",
                "order_index": 0,
                "kind": "LOCATE_DATA",
                "title": "Locate",
                "depends_on": [],
                "requires_approval": False,
            },
            {
                "id": "s_search",
                "order_index": 1,
                "kind": "SEARCH_ENCODE",
                "title": "Search",
                "depends_on": [],
                "requires_approval": False,
            },
            {
                "id": "s_downstream",
                "order_index": 2,
                "kind": "PARSE_OUTPUT_FILE",
                "title": "Parse",
                "depends_on": ["s_search"],
                "requires_approval": False,
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-fail",
        owner_id="owner-fail",
        payload=payload,
    )

    async def _fake_call(_source_key, tool_name, _params):
        if tool_name == "search_by_biosample":
            return {"error": "boom"}
        return {"ok": True}

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-fail")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    assert final_payload["status"] == "FAILED"
    assert next(s for s in final_payload["steps"] if s["id"] == "s_search")["status"] == "FAILED"
    assert next(s for s in final_payload["steps"] if s["id"] == "s_downstream")["status"] == "SKIPPED"


@pytest.mark.asyncio
async def test_approval_semantics_unchanged_with_parallel_safe_step(session_factory, monkeypatch):
    from cortex import plan_executor

    payload = {
        "title": "Approval pause",
        "project_id": "proj-approval",
        "plan_instance_id": "pi-approval",
        "steps": [
            {
                "id": "s_loc",
                "order_index": 0,
                "kind": "LOCATE_DATA",
                "title": "Locate",
                "depends_on": [],
                "requires_approval": False,
            },
            {
                "id": "s_gate",
                "order_index": 1,
                "kind": "REQUEST_APPROVAL",
                "title": "Approve",
                "depends_on": [],
                "requires_approval": True,
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-approval",
        owner_id="owner-approval",
        payload=payload,
    )

    async def _fake_call(_source_key, _tool_name, _params):
        return {"ok": True}

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-approval")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    assert final_payload["status"] == "WAITING_APPROVAL"
    assert next(s for s in final_payload["steps"] if s["id"] == "s_loc")["status"] == "COMPLETED"
    assert next(s for s in final_payload["steps"] if s["id"] == "s_gate")["status"] == "WAITING_APPROVAL"