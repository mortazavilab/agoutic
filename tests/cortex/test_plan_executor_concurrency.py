import asyncio
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.db_helpers import _create_block_internal
from cortex.models import Memory, ProjectBlock, ProjectTask
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


@pytest.mark.asyncio
async def test_parse_plot_interpret_steps_complete_with_inline_plot_payload(session_factory):
    payload = {
        "title": "Plot and interpret results for JamshidW",
        "project_id": "proj-plot",
        "plan_instance_id": "pi-plot",
        "plot_type": "bar",
        "steps": [
            {
                "id": "loc",
                "order_index": 0,
                "kind": "LOCATE_DATA",
                "title": "Locate result CSV files",
                "depends_on": [],
                "requires_approval": False,
                "status": "COMPLETED",
                "result": [{
                    "tool": "list_job_files",
                    "source_key": "analyzer",
                    "result": {
                        "success": True,
                        "work_dir": "/tmp/workflow1",
                        "files": [
                            {"path": "annot/JamshidW.mm39_final_stats.csv", "name": "JamshidW.mm39_final_stats.csv", "extension": ".csv"},
                            {"path": "qc_summary.csv", "name": "qc_summary.csv", "extension": ".csv"},
                        ],
                    },
                }],
            },
            {
                "id": "parse",
                "order_index": 1,
                "kind": "PARSE_OUTPUT_FILE",
                "title": "Parse key result CSV files",
                "depends_on": ["loc"],
                "requires_approval": False,
                "status": "COMPLETED",
                "result": [
                    {
                        "tool": "parse_csv_file",
                        "source_key": "analyzer",
                        "result": {
                            "success": True,
                            "work_dir": "/tmp/workflow1",
                            "file_path": "annot/JamshidW.mm39_final_stats.csv",
                            "columns": ["Category", "Count"],
                            "row_count": 3,
                            "data": [
                                {"Category": "Known genes detected", "Count": 1038},
                                {"Category": "Novel genes discovered", "Count": 1},
                                {"Category": "Reads - Known", "Count": 1305},
                            ],
                        },
                    },
                    {
                        "tool": "parse_csv_file",
                        "source_key": "analyzer",
                        "result": {
                            "success": True,
                            "work_dir": "/tmp/workflow1",
                            "file_path": "qc_summary.csv",
                            "columns": ["sample", "file_type", "n_reads", "mapped_pct", "mean_len"],
                            "row_count": 2,
                            "data": [
                                {"sample": "JamshidW", "file_type": "FASTQ", "n_reads": 1750, "mapped_pct": None, "mean_len": 1375.07},
                                {"sample": "JamshidW", "file_type": "BAM", "n_reads": 1750, "mapped_pct": 95.89, "mean_len": 1375.07},
                            ],
                        },
                    },
                ],
            },
            {
                "id": "plot",
                "order_index": 2,
                "kind": "GENERATE_PLOT",
                "title": "Generate bar chart",
                "depends_on": ["parse"],
                "requires_approval": False,
            },
            {
                "id": "interpret",
                "order_index": 3,
                "kind": "INTERPRET_RESULTS",
                "title": "Explain what the results mean",
                "depends_on": ["plot"],
                "requires_approval": False,
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-plot",
        owner_id="owner-plot",
        payload=payload,
    )

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-plot")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    assert final_payload["status"] == "COMPLETED"

    plot_step = next(step for step in final_payload["steps"] if step["id"] == "plot")
    assert plot_step["status"] == "COMPLETED"
    assert plot_step["result"]["selected_file"] == "annot/JamshidW.mm39_final_stats.csv"
    assert plot_step["result"]["charts"][0]["type"] == "bar"
    assert plot_step["result"]["charts"][0]["x"] == "Category"
    assert plot_step["result"]["charts"][0]["y"] == "Count"
    assert "JamshidW.mm39_final_stats.csv" in plot_step["result"]["_dataframes"]

    interpret_step = next(step for step in final_payload["steps"] if step["id"] == "interpret")
    assert interpret_step["status"] == "COMPLETED"
    assert "95.89%" in interpret_step["result"]["markdown"]
    assert "usable" in interpret_step["result"]["markdown"]


@pytest.mark.asyncio
async def test_parse_output_file_uses_located_csv_results(session_factory, monkeypatch):
    from cortex import plan_executor

    payload = {
        "title": "Parse located csvs",
        "project_id": "proj-parse",
        "plan_instance_id": "pi-parse",
        "steps": [
            {
                "id": "s_loc",
                "order_index": 0,
                "kind": "LOCATE_DATA",
                "title": "Locate csv results",
                "depends_on": [],
                "requires_approval": False,
                "tool_calls": [
                    {
                        "source_key": "analyzer",
                        "tool": "list_job_files",
                        "params": {"work_dir": "/tmp/workflow2", "extensions": ".csv"},
                    }
                ],
            },
            {
                "id": "s_parse",
                "order_index": 1,
                "kind": "PARSE_OUTPUT_FILE",
                "title": "Parse key result CSV files",
                "depends_on": ["s_loc"],
                "requires_approval": False,
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-parse",
        owner_id="owner-parse",
        payload=payload,
    )

    parse_calls: list[tuple[str, dict]] = []

    async def _fake_call(_source_key, tool_name, params):
        if tool_name == "list_job_files":
            return {
                "success": True,
                "work_dir": "/tmp/workflow2",
                "file_count": 3,
                "files": [
                    {"path": "annot/JamshidW.mm39_final_stats.csv", "name": "JamshidW.mm39_final_stats.csv", "extension": ".csv"},
                    {"path": "annot/JamshidW.mm39_qc_summary.csv", "name": "JamshidW.mm39_qc_summary.csv", "extension": ".csv"},
                    {"path": "qc_summary.csv", "name": "qc_summary.csv", "extension": ".csv"},
                ],
            }
        if tool_name == "parse_csv_file":
            parse_calls.append((tool_name, dict(params)))
            return {
                "success": True,
                "work_dir": params.get("work_dir"),
                "file_path": params.get("file_path"),
                "columns": ["metric", "value"],
                "row_count": 2,
                "preview_rows": 2,
                "data": [{"metric": "reads", "value": 1}],
                "metadata": {},
            }
        return {"success": True}

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-parse")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    parse_step = next(step for step in final_payload["steps"] if step["id"] == "s_parse")

    assert parse_step["status"] == "COMPLETED"
    assert [call[1].get("file_path") for call in parse_calls] == [
        "annot/JamshidW.mm39_qc_summary.csv",
        "qc_summary.csv",
        "annot/JamshidW.mm39_final_stats.csv",
    ]
    assert [entry["result"]["file_path"] for entry in parse_step["result"]] == [
        "annot/JamshidW.mm39_qc_summary.csv",
        "qc_summary.csv",
        "annot/JamshidW.mm39_final_stats.csv",
    ]
    assert all(call[1]["work_dir"] == "/tmp/workflow2" for call in parse_calls)


@pytest.mark.asyncio
async def test_parse_output_file_prioritizes_reconcile_outputs(session_factory, monkeypatch):
    from cortex import plan_executor

    payload = {
        "title": "Parse reconcile outputs",
        "project_id": "proj-reconcile-parse",
        "plan_type": "reconcile_bams",
        "plan_instance_id": "pi-reconcile-parse",
        "steps": [
            {
                "id": "s_loc",
                "order_index": 0,
                "kind": "LOCATE_DATA",
                "title": "Locate reconcile outputs",
                "depends_on": [],
                "requires_approval": False,
                "tool_calls": [
                    {
                        "source_key": "analyzer",
                        "tool": "list_job_files",
                        "params": {"work_dir": "/tmp/workflow6", "max_depth": 1},
                    }
                ],
            },
            {
                "id": "s_parse",
                "order_index": 1,
                "kind": "PARSE_OUTPUT_FILE",
                "title": "Parse reconcile tables",
                "depends_on": ["s_loc"],
                "requires_approval": False,
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-reconcile-parse",
        owner_id="owner-reconcile-parse",
        payload=payload,
    )

    parse_calls: list[tuple[str, dict]] = []

    async def _fake_call(_source_key, tool_name, params):
        if tool_name == "list_job_files":
            return {
                "success": True,
                "work_dir": "/tmp/workflow6",
                "file_count": 7,
                "files": [
                    {"path": "annot/lwf2.GRCh38_qc_summary.csv", "name": "lwf2.GRCh38_qc_summary.csv", "extension": ".csv"},
                    {"path": "reconciled_abundance.tsv", "name": "reconciled_abundance.tsv", "extension": ".tsv"},
                    {"path": "reconciled_novelty_by_sample.csv", "name": "reconciled_novelty_by_sample.csv", "extension": ".csv"},
                    {"path": "reconciled.inputs.tsv", "name": "reconciled.inputs.tsv", "extension": ".tsv"},
                    {"path": "exc.reconciled.mapping.tsv", "name": "exc.reconciled.mapping.tsv", "extension": ".tsv"},
                    {"path": "gko.reconciled.mapping.tsv", "name": "gko.reconciled.mapping.tsv", "extension": ".tsv"},
                    {"path": "qc_summary.csv", "name": "qc_summary.csv", "extension": ".csv"},
                ],
            }
        if tool_name == "parse_csv_file":
            parse_calls.append((tool_name, dict(params)))
            return {
                "success": True,
                "work_dir": params.get("work_dir"),
                "file_path": params.get("file_path"),
                "columns": ["metric", "value"],
                "row_count": 2,
                "preview_rows": 2,
                "data": [{"metric": "reads", "value": 1}],
                "metadata": {},
            }
        return {"success": True}

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-reconcile-parse")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    parse_step = next(step for step in final_payload["steps"] if step["id"] == "s_parse")

    assert parse_step["status"] == "COMPLETED"
    assert [call[1].get("file_path") for call in parse_calls] == [
        "reconciled_abundance.tsv",
        "reconciled_novelty_by_sample.csv",
        "reconciled.inputs.tsv",
        "exc.reconciled.mapping.tsv",
        "gko.reconciled.mapping.tsv",
    ]
    assert all(call[1]["work_dir"] == "/tmp/workflow6" for call in parse_calls)


@pytest.mark.asyncio
async def test_parse_output_file_falls_back_to_run_script_work_directory_for_reconcile(session_factory, monkeypatch):
    from cortex import plan_executor

    payload = {
        "title": "Parse reconcile outputs from runtime workflow",
        "project_id": "proj-reconcile-runtime-parse",
        "plan_type": "reconcile_bams",
        "plan_instance_id": "pi-reconcile-runtime-parse",
        "steps": [
            {
                "id": "run",
                "order_index": 0,
                "kind": "RUN_SCRIPT",
                "title": "Run reconcile",
                "depends_on": [],
                "requires_approval": False,
                "status": "COMPLETED",
                "work_directory": "/tmp/project/workflow7",
            },
            {
                "id": "loc",
                "order_index": 1,
                "kind": "LOCATE_DATA",
                "title": "Locate reconcile outputs",
                "depends_on": ["run"],
                "requires_approval": False,
                "status": "COMPLETED",
                "result": [
                    {
                        "tool": "list_job_files",
                        "source_key": "analyzer",
                        "result": {
                            "success": True,
                            "work_dir": "/tmp/project",
                            "file_count": 3,
                            "files": [
                                {"path": "workflow5/", "name": "workflow5/", "size": 0},
                                {"path": "workflow6/", "name": "workflow6/", "size": 0},
                                {"path": "workflow7/", "name": "workflow7/", "size": 0},
                            ],
                        },
                    }
                ],
            },
            {
                "id": "parse",
                "order_index": 2,
                "kind": "PARSE_OUTPUT_FILE",
                "title": "Parse reconcile tables",
                "depends_on": ["loc"],
                "requires_approval": False,
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-reconcile-runtime-parse",
        owner_id="owner-reconcile-runtime-parse",
        payload=payload,
    )

    parse_calls: list[tuple[str, dict]] = []
    locate_calls: list[dict] = []

    async def _fake_call(_source_key, tool_name, params):
        if tool_name == "list_job_files":
            locate_calls.append(dict(params))
            return {
                "success": True,
                "work_dir": "/tmp/project/workflow7",
                "file_count": 3,
                "files": [
                    {"path": "reconciled_abundance.tsv", "name": "reconciled_abundance.tsv", "size": 128},
                    {"path": "reconciled_novelty_by_sample.csv", "name": "reconciled_novelty_by_sample.csv", "size": 64},
                    {"path": "output/", "name": "output/", "size": 0},
                ],
            }
        if tool_name == "parse_csv_file":
            parse_calls.append((tool_name, dict(params)))
            return {
                "success": True,
                "work_dir": params.get("work_dir"),
                "file_path": params.get("file_path"),
                "columns": ["metric", "value"],
                "row_count": 2,
                "preview_rows": 2,
                "data": [{"metric": "reads", "value": 1}],
                "metadata": {},
            }
        return {"success": True}

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-reconcile-runtime-parse")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    parse_step = next(step for step in final_payload["steps"] if step["id"] == "parse")

    assert parse_step["status"] == "COMPLETED"
    assert locate_calls == [{
        "work_dir": "/tmp/project/workflow7",
        "max_depth": 1,
        "allow_missing": True,
    }]
    assert [call[1].get("file_path") for call in parse_calls] == [
        "reconciled_abundance.tsv",
        "reconciled_novelty_by_sample.csv",
    ]
    assert all(call[1]["work_dir"] == "/tmp/project/workflow7" for call in parse_calls)


@pytest.mark.asyncio
async def test_write_summary_reports_reconcile_outputs(session_factory):
    payload = {
        "title": "Summarize reconcile outputs",
        "project_id": "proj-reconcile-summary",
        "plan_type": "reconcile_bams",
        "plan_instance_id": "pi-reconcile-summary",
        "steps": [
            {
                "id": "run",
                "order_index": 0,
                "kind": "RUN_SCRIPT",
                "title": "Run reconcile",
                "depends_on": [],
                "requires_approval": False,
                "status": "COMPLETED",
                "work_directory": "/tmp/project/workflow6",
            },
            {
                "id": "loc",
                "order_index": 1,
                "kind": "LOCATE_DATA",
                "title": "Locate reconcile outputs",
                "depends_on": ["run"],
                "requires_approval": False,
                "status": "COMPLETED",
                "result": [{
                    "tool": "list_job_files",
                    "source_key": "analyzer",
                    "result": {
                        "success": True,
                        "work_dir": "/tmp/project/workflow6",
                        "files": [
                            {"path": "reconciled_abundance.tsv", "name": "reconciled_abundance.tsv", "extension": ".tsv"},
                            {"path": "reconciled_novelty_by_sample.csv", "name": "reconciled_novelty_by_sample.csv", "extension": ".csv"},
                            {"path": "reconciled.inputs.tsv", "name": "reconciled.inputs.tsv", "extension": ".tsv"},
                            {"path": "exc.reconciled.bam", "name": "exc.reconciled.bam", "extension": ".bam"},
                            {"path": "jbh.reconciled.bam", "name": "jbh.reconciled.bam", "extension": ".bam"},
                            {"path": "reconciled.gtf", "name": "reconciled.gtf", "extension": ".gtf"},
                            {"path": "reconciled_summary.txt", "name": "reconciled_summary.txt", "extension": ".txt"},
                        ],
                    },
                }],
            },
            {
                "id": "parse",
                "order_index": 2,
                "kind": "PARSE_OUTPUT_FILE",
                "title": "Parse reconcile tables",
                "depends_on": ["loc"],
                "requires_approval": False,
                "status": "COMPLETED",
                "result": [
                    {
                        "tool": "parse_csv_file",
                        "source_key": "analyzer",
                        "result": {
                            "success": True,
                            "work_dir": "/tmp/project/workflow6",
                            "file_path": "reconciled_abundance.tsv",
                            "columns": ["gene_ID", "exc", "jbh"],
                            "row_count": 2,
                            "data": [{"gene_ID": "GENE1", "exc": 10, "jbh": 12}],
                        },
                    },
                    {
                        "tool": "parse_csv_file",
                        "source_key": "analyzer",
                        "result": {
                            "success": True,
                            "work_dir": "/tmp/project/workflow6",
                            "file_path": "reconciled_novelty_by_sample.csv",
                            "columns": ["sample", "KNOWN", "NOVEL"],
                            "row_count": 2,
                            "data": [{"sample": "exc", "KNOWN": 100, "NOVEL": 5}],
                        },
                    },
                ],
            },
            {
                "id": "summary",
                "order_index": 3,
                "kind": "WRITE_SUMMARY",
                "title": "Summarize reconcile outputs",
                "depends_on": ["parse"],
                "requires_approval": False,
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-reconcile-summary",
        owner_id="owner-reconcile-summary",
        payload=payload,
    )

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-reconcile-summary")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    summary_step = next(step for step in final_payload["steps"] if step["id"] == "summary")

    assert summary_step["status"] == "COMPLETED"
    markdown = summary_step["result"]["markdown"]
    assert "workflow6" in markdown
    assert "reconciled_abundance.tsv" in markdown
    assert "reconciled_novelty_by_sample.csv" in markdown
    assert "2 reconciled BAM file(s)" in markdown
    assert "reconciled.gtf" in markdown


@pytest.mark.asyncio
async def test_run_de_plan_continues_after_check_existing_miss(session_factory, monkeypatch, tmp_path):
    from cortex import plan_executor

    abundance_path = tmp_path / "reconciled_abundance.tsv"
    abundance_path.write_text(
        "gene_ID\ttranscript_ID\texc\tjbh\tgko\tlwf2\n"
        "GENE1\tTX1\t10\t12\t4\t5\n",
        encoding="utf-8",
    )

    payload = {
        "title": "Grouped DE from reconcile workflow",
        "project_id": "proj-de-followup",
        "plan_type": "run_de_pipeline",
        "work_dir": str(tmp_path),
        "plan_instance_id": "pi-de-followup",
        "steps": [
            {
                "id": "check1",
                "order_index": 0,
                "kind": "CHECK_EXISTING",
                "title": "Check for existing DE results",
                "depends_on": [],
                "requires_approval": False,
                "tool_calls": [
                    {
                        "source_key": "analyzer",
                        "tool": "find_file",
                        "params": {"file_name": "de_results", "work_dir": str(tmp_path)},
                    }
                ],
            },
            {
                "id": "prep1",
                "order_index": 1,
                "kind": "PREPARE_DE_INPUT",
                "title": "Prepare DE inputs",
                "depends_on": ["check1"],
                "requires_approval": False,
                "counts_path": "",
                "work_dir": str(tmp_path),
                "group_a_label": "AD",
                "group_a_samples": ["exc", "jbh"],
                "group_b_label": "control",
                "group_b_samples": ["gko", "lwf2"],
                "level": "gene",
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-de-followup",
        owner_id="owner-de-followup",
        payload=payload,
    )

    async def _fake_call(_source_key, tool_name, params):
        if tool_name == "find_file":
            return {
                "success": False,
                "error": "File not found (checked result directories only, ignored work/ folder)",
                "search_term": params.get("file_name"),
                "work_dir": params.get("work_dir"),
            }
        return {"success": True}

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-de-followup")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    check_step = next(step for step in final_payload["steps"] if step["id"] == "check1")
    prep_step = next(step for step in final_payload["steps"] if step["id"] == "prep1")

    assert final_payload["status"] == "COMPLETED"
    assert check_step["status"] == "COMPLETED"
    assert check_step["result"][0]["result"]["file_count"] == 0
    assert prep_step["status"] == "COMPLETED"
    assert prep_step["result"]["counts_path"].startswith(str(tmp_path / "de_inputs"))


@pytest.mark.asyncio
async def test_run_de_plan_allocates_next_project_workflow_for_outputs(session_factory, monkeypatch, tmp_path):
    from cortex import plan_executor

    source_workflow = tmp_path / "workflow7"
    source_workflow.mkdir(parents=True)
    (tmp_path / "workflow1").mkdir()
    abundance_path = source_workflow / "reconciled_abundance.tsv"
    abundance_path.write_text(
        "gene_ID\ttranscript_ID\texc\tjbh\tgko\tlwf2\n"
        "GENE1\tTX1\t10\t12\t4\t5\n",
        encoding="utf-8",
    )

    payload = {
        "title": "Grouped DE allocates a new workflow",
        "project_id": "proj-de-next-workflow",
        "plan_type": "run_de_pipeline",
        "work_dir": str(source_workflow),
        "plan_instance_id": "pi-de-next-workflow",
        "steps": [
            {
                "id": "check1",
                "order_index": 0,
                "kind": "CHECK_EXISTING",
                "title": "Check for existing DE results",
                "depends_on": [],
                "requires_approval": False,
                "tool_calls": [
                    {
                        "source_key": "analyzer",
                        "tool": "find_file",
                        "params": {"file_name": "de_results", "work_dir": str(source_workflow)},
                    }
                ],
            },
            {
                "id": "prep1",
                "order_index": 1,
                "kind": "PREPARE_DE_INPUT",
                "title": "Prepare DE inputs",
                "depends_on": ["check1"],
                "requires_approval": False,
                "counts_path": "",
                "work_dir": str(source_workflow),
                "group_a_label": "AD",
                "group_a_samples": ["exc", "jbh"],
                "group_b_label": "control",
                "group_b_samples": ["gko", "lwf2"],
                "level": "gene",
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-de-next-workflow",
        owner_id="owner-de-next-workflow",
        payload=payload,
    )

    captured_find_work_dirs: list[str] = []

    async def _fake_call(_source_key, tool_name, params):
        if tool_name == "find_file":
            captured_find_work_dirs.append(str(params.get("work_dir") or ""))
            return {
                "success": False,
                "error": "File not found (checked result directories only, ignored work/ folder)",
                "search_term": params.get("file_name"),
                "work_dir": params.get("work_dir"),
            }
        return {"success": True}

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)
    monkeypatch.setattr("cortex.db_helpers._resolve_project_dir", lambda *_args, **_kwargs: tmp_path)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    user_stub = type("UserStub", (), {"id": "owner-de-next-workflow"})()
    await execute_plan(session, wf, project_id="proj-de-next-workflow", user=user_stub)
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    prep_step = next(step for step in final_payload["steps"] if step["id"] == "prep1")

    assert final_payload["status"] == "COMPLETED"
    assert final_payload["de_workflow_alias"] == "workflow8"
    assert final_payload["de_work_dir"] == str(tmp_path / "workflow8")
    assert (tmp_path / "workflow8").is_dir()
    assert captured_find_work_dirs == [str(tmp_path / "workflow8")]
    assert prep_step["status"] == "COMPLETED"
    assert prep_step["result"]["counts_path"].startswith(str(tmp_path / "workflow8" / "de_inputs"))


@pytest.mark.asyncio
async def test_run_de_autocaptures_memories_before_downstream_failure(session_factory, monkeypatch, tmp_path):
    from cortex import plan_executor

    source_workflow = tmp_path / "workflow7"
    output_workflow = tmp_path / "workflow8"
    source_workflow.mkdir(parents=True)
    output_workflow.mkdir(parents=True)
    abundance_path = source_workflow / "reconciled_abundance.tsv"
    abundance_path.write_text(
        "gene_ID\ttranscript_ID\texc\tjbh\tgko\tlwf2\n"
        "GENE1\tTX1\t10\t12\t4\t5\n",
        encoding="utf-8",
    )

    payload = {
        "title": "Grouped DE captures memory before failure",
        "project_id": "proj-de-memory",
        "plan_type": "run_de_pipeline",
        "workflow_type": "de_analysis",
        "work_dir": str(source_workflow),
        "de_work_dir": str(output_workflow),
        "steps": [
            {
                "id": "check1",
                "order_index": 0,
                "kind": "CHECK_EXISTING",
                "title": "Check for existing DE results",
                "depends_on": [],
                "requires_approval": False,
                "tool_calls": [
                    {
                        "source_key": "analyzer",
                        "tool": "find_file",
                        "params": {"file_name": "de_results", "work_dir": str(output_workflow)},
                    }
                ],
            },
            {
                "id": "prep1",
                "order_index": 1,
                "kind": "PREPARE_DE_INPUT",
                "title": "Prepare DE inputs",
                "depends_on": ["check1"],
                "requires_approval": False,
                "counts_path": "",
                "work_dir": str(source_workflow),
                "output_dir": str(output_workflow / "de_inputs"),
                "group_a_label": "AD",
                "group_a_samples": ["exc", "jbh"],
                "group_b_label": "control",
                "group_b_samples": ["gko", "lwf2"],
                "level": "gene",
            },
            {
                "id": "run1",
                "order_index": 2,
                "kind": "RUN_DE_PIPELINE",
                "title": "Run DE analysis",
                "depends_on": ["prep1"],
                "requires_approval": False,
                "tool_calls": [
                    {
                        "source_key": "edgepython",
                        "tool": "load_data",
                        "params": {"counts_path": "", "sample_info_path": "", "group_column": "group"},
                    },
                    {
                        "source_key": "edgepython",
                        "tool": "exact_test",
                        "params": {"pair": ["AD", "control"], "name": "placeholder"},
                    },
                    {
                        "source_key": "edgepython",
                        "tool": "get_top_genes",
                        "params": {"name": "placeholder", "n": 20, "fdr_threshold": 0.05},
                    },
                ],
            },
            {
                "id": "save1",
                "order_index": 3,
                "kind": "SAVE_RESULTS",
                "title": "Save full DE results",
                "depends_on": ["run1"],
                "requires_approval": False,
                "tool_calls": [
                    {
                        "source_key": "edgepython",
                        "tool": "save_results",
                        "params": {"name": "placeholder", "format": "tsv"},
                    }
                ],
            },
            {
                "id": "plot1",
                "order_index": 4,
                "kind": "GENERATE_DE_PLOT",
                "title": "Generate volcano plot",
                "depends_on": ["run1"],
                "requires_approval": False,
                "tool_calls": [
                    {
                        "source_key": "edgepython",
                        "tool": "generate_plot",
                        "params": {"plot_type": "volcano", "result_name": "placeholder"},
                    }
                ],
            },
            {
                "id": "fail1",
                "order_index": 5,
                "kind": "CHECK_EXISTING",
                "title": "Fail after artifacts",
                "depends_on": ["save1", "plot1"],
                "requires_approval": False,
                "tool_calls": [
                    {
                        "source_key": "analyzer",
                        "tool": "find_file",
                        "params": {"file_name": "must_fail", "work_dir": str(output_workflow)},
                    }
                ],
            },
        ],
    }
    block = _create_workflow_block(
        session_factory,
        project_id="proj-de-memory",
        owner_id="owner-de-memory",
        payload=payload,
    )

    async def _fake_call(_source_key, tool_name, params):
        if tool_name == "find_file":
            if params.get("file_name") == "de_results":
                return {
                    "success": False,
                    "error": "File not found (checked result directories only, ignored work/ folder)",
                    "search_term": params.get("file_name"),
                    "work_dir": params.get("work_dir"),
                }
            return {"error": "forced downstream failure"}
        if tool_name == "load_data":
            return {"data": "loaded"}
        if tool_name == "exact_test":
            return "Test: Exact\nDE genes (FDR < 0.05): 12 up, 4 down, 100 NS"
        if tool_name == "get_top_genes":
            return "Top genes by FDR\nGENE1\nGENE2"
        if tool_name == "save_results":
            return f"Saved results to: {output_workflow / 'de_results' / 'de_results.tsv'}"
        if tool_name == "generate_plot":
            return f"Volcano plot saved to: {output_workflow / 'de_results' / 'volcano_ad_vs_control_gene.png'}"
        return {"success": True}

    monkeypatch.setattr(plan_executor, "_call_mcp_tool", _fake_call)

    session = session_factory()
    wf = session.execute(select(ProjectBlock).where(ProjectBlock.id == block.id)).scalar_one()
    await execute_plan(session, wf, project_id="proj-de-memory")
    session.close()

    _final_block, final_payload = _load_payload(session_factory, block.id)
    assert final_payload["status"] == "FAILED"

    verify_session = session_factory()
    memories = list(
        verify_session.execute(
            select(Memory).where(Memory.project_id == "proj-de-memory").order_by(Memory.created_at)
        ).scalars().all()
    )
    verify_session.close()

    assert any("Prepared DE comparison for AD (exc, jbh) vs control (gko, lwf2)" in mem.content for mem in memories)
    assert any("DE result summary recorded for AD (exc, jbh) vs control (gko, lwf2)" in mem.content for mem in memories)
    assert any("DE results table saved for AD (exc, jbh) vs control (gko, lwf2)" in mem.content for mem in memories)
    assert any("Volcano plot saved for AD (exc, jbh) vs control (gko, lwf2)" in mem.content for mem in memories)

    structured_payloads = [json.loads(mem.structured_data) for mem in memories if mem.structured_data]
    assert any(payload.get("capture_type") == "de_comparison" for payload in structured_payloads)
    assert any(payload.get("capture_type") == "de_result_summary" for payload in structured_payloads)
    assert any(payload.get("capture_type") == "de_result_file" for payload in structured_payloads)
    assert any(payload.get("capture_type") == "de_plot" for payload in structured_payloads)