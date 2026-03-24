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