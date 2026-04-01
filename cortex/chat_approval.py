"""Approval-flow helpers for chat and workflow plans.

Contains rejection handling, approval-gate creation, SSH auth resolution,
and plan step advancement — extracted from *app.py* for clarity.
"""
from __future__ import annotations

import datetime
import json

from sqlalchemy import select
from fastapi.concurrency import run_in_threadpool

from common.logging_config import get_logger
from cortex.agent_engine import AgentEngine
from cortex.llm_validators import get_block_payload
from cortex.models import ProjectBlock
from cortex.db import SessionLocal
from cortex.db_helpers import _create_block_internal
from cortex.task_service import sync_project_tasks
from cortex.remote_orchestration import (
    _resolve_ssh_profile_reference,
    _list_user_ssh_profiles,
    _get_ssh_profile_auth_session,
    _persist_workflow_plan,
)
import cortex.job_parameters as job_parameters

logger = get_logger(__name__)


async def handle_rejection(project_id: str, gate_block_id: str):
    """
    Background task to handle rejection of an approval gate.
    Passes feedback to LLM to regenerate plan. Limits to 3 attempts.
    """
    session = SessionLocal()
    
    try:
        # Get the rejected gate block
        gate_block = session.query(ProjectBlock).filter(ProjectBlock.id == gate_block_id).first()
        if not gate_block:
            logger.error("Gate block not found", gate_block_id=gate_block_id)
            return
        
        # Get owner_id from the gate block
        owner_id = gate_block.owner_id
        
        # Get rejection details from payload
        gate_payload = get_block_payload(gate_block)
        rejection_reason = gate_payload.get("rejection_reason", "No reason provided")
        attempt_number = gate_payload.get("attempt_number", 1)
        rejection_history = gate_payload.get("rejection_history", [])
        
        # Add to rejection history
        rejection_history.append({
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "reason": rejection_reason,
            "attempt": attempt_number
        })
        
        # Update gate block with history
        gate_payload["rejection_history"] = rejection_history
        gate_block.payload_json = json.dumps(gate_payload)
        session.commit()
        
        logger.info("Handling rejection", project_id=project_id, attempt=attempt_number, reason=rejection_reason)
        
        # Check attempt limit
        if attempt_number >= 3:
            logger.warning("Max attempts reached, creating manual parameter form", project_id=project_id)
            
            # Extract parameters for manual form
            params = await job_parameters.extract_job_parameters_from_conversation(session, project_id)
            
            # Create a manual parameter form block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": f"""### ⚠️ Manual Configuration Required (Attempt 3/3)

I've tried 3 times but couldn't meet your requirements. Please verify and edit these parameters manually:

**Extracted Parameters:**
- Sample Name: `{params.get('sample_name', 'unknown')}`
- Mode: `{params.get('mode', 'DNA')}`
- Input Type: `{params.get('input_type', 'pod5')}`
- Input Directory: `{params.get('input_directory', '/path/to/data')}`
- Reference Genome(s): `{', '.join(params.get('reference_genome', ['mm39']))}`
- Modifications: `{params.get('modifications', 'auto')}`

Please use the parameter editing form below to make corrections.""",
                    "skill": get_block_payload(gate_block).get("skill", "analyze_local_sample"),
                    "model": get_block_payload(gate_block).get("model", "default")
                },
                status="NEW",
                owner_id=owner_id
            )
            
            # Create a new approval gate with manual mode flag
            _create_block_internal(
                session,
                project_id,
                "APPROVAL_GATE",
                {
                    "label": "Manual Configuration - Verify Parameters",
                    "extracted_params": params,
                    "manual_mode": True,
                    "attempt_number": 3,
                    "rejection_history": rejection_history
                },
                status="PENDING",
                owner_id=owner_id
            )
            
            return
        
        # Build conversation history for LLM
        query = select(ProjectBlock)\
            .where(ProjectBlock.project_id == project_id)\
            .order_by(ProjectBlock.seq.asc())
        
        result = session.execute(query)
        blocks = result.scalars().all()
        
        conversation_history = []
        active_skill = None
        
        for block in blocks:
            block_payload = get_block_payload(block)
            if block.type == "USER_MESSAGE":
                conversation_history.append({
                    "role": "user",
                    "content": block_payload.get("text", "")
                })
            elif block.type == "AGENT_PLAN":
                conversation_history.append({
                    "role": "assistant",
                    "content": block_payload.get("markdown", "")
                })
                active_skill = block_payload.get("skill")
        
        # Add rejection feedback as user message
        conversation_history.append({
            "role": "user",
            "content": f"I'm rejecting your plan (attempt {attempt_number}/3). Please revise it.\n\nFeedback: {rejection_reason}\n\nPlease generate a new plan that addresses my concerns."
        })
        
        # Get the active skill and model
        gate_payload = get_block_payload(gate_block)
        skill_name = active_skill or gate_payload.get("skill", "analyze_local_sample")
        model_name = gate_payload.get("model", "default")
        
        # Call LLM to regenerate plan
        agent_engine = AgentEngine(model_name=model_name)
        
        try:
            llm_response = await run_in_threadpool(
                agent_engine.think,
                skill_name=skill_name,
                user_message=f"[REVISION REQUEST] {rejection_reason}",
                conversation_history=conversation_history
            )
            
            # Check if response needs approval or is asking for more info
            needs_approval = "[[APPROVAL_NEEDED]]" in llm_response
            clean_response = llm_response.replace("[[APPROVAL_NEEDED]]", "").strip()
            
            # Create new AGENT_PLAN block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": clean_response,
                    "skill": skill_name,
                    "model": model_name,
                    "revision_attempt": attempt_number + 1
                },
                status="DONE" if needs_approval else "NEW",
                owner_id=owner_id
            )
            
            # Only create approval gate if the agent explicitly requested it
            if needs_approval:
                # Extract parameters for new approval gate
                params = await job_parameters.extract_job_parameters_from_conversation(session, project_id)
                
                # Create new APPROVAL_GATE
                _create_block_internal(
                    session,
                    project_id,
                    "APPROVAL_GATE",
                    {
                        "label": f"Revised Plan (Attempt {attempt_number + 1}/3)",
                        "extracted_params": params,
                        "attempt_number": attempt_number + 1,
                        "rejection_history": rejection_history,
                        "skill": skill_name,
                        "model": model_name
                    },
                    status="PENDING",
                    owner_id=owner_id
                )
                logger.info("Generated revised plan", project_id=project_id, attempt=attempt_number + 1)
            else:
                logger.info("Agent requesting more information", project_id=project_id, attempt=attempt_number + 1)
            
        except Exception as e:
            logger.error("LLM failed during revision", error=str(e), project_id=project_id)
            # Create error block
            _create_block_internal(
                session,
                project_id,
                "AGENT_PLAN",
                {
                    "markdown": f"### ⚠️ Error\n\nFailed to generate revised plan: {str(e)}\n\nPlease try again or use manual configuration.",
                    "error": str(e)
                },
                status="ERROR",
                owner_id=owner_id
            )
    
    finally:
        session.close()


async def _ensure_workflow_plan_approval_gate(
    session,
    workflow_block: ProjectBlock,
    *,
    owner_id: str,
    model_name: str,
) -> ProjectBlock | None:
    """Create an approval gate when a workflow plan pauses at REQUEST_APPROVAL."""
    payload = get_block_payload(workflow_block)
    current_step_id = payload.get("current_step_id")
    if not current_step_id:
        return None

    current_step = next(
        (step for step in payload.get("steps", []) if step.get("id") == current_step_id),
        None,
    )
    if not current_step:
        return None
    if current_step.get("kind") != "REQUEST_APPROVAL" or current_step.get("status") != "WAITING_APPROVAL":
        return None

    existing_gate = session.execute(
        select(ProjectBlock)
        .where(
            ProjectBlock.project_id == workflow_block.project_id,
            ProjectBlock.type == "APPROVAL_GATE",
            ProjectBlock.parent_id == workflow_block.id,
            ProjectBlock.status == "PENDING",
        )
        .order_by(ProjectBlock.seq.desc())
    ).scalar_one_or_none()
    if existing_gate is not None:
        return existing_gate

    reconcile_context = _build_reconcile_plan_approval_context(workflow_block)
    if reconcile_context is not None:
        extracted_params = reconcile_context["extracted_params"]
        gate_action = reconcile_context["gate_action"]
        gate_skill = reconcile_context["skill"]
        gate_label = reconcile_context["label"]
        cache_preflight = reconcile_context.get("cache_preflight")
    else:
        extracted_params = await job_parameters.extract_job_parameters_from_conversation(session, workflow_block.project_id)
        gate_action = "job"
        gate_skill = payload.get("skill") or "welcome"
        if isinstance(extracted_params, dict):
            gate_action = extracted_params.get("gate_action") or gate_action
            if (extracted_params.get("execution_mode") or "local") == "slurm":
                gate_skill = "remote_execution"

        gate_label = "Do you authorize the Agent to proceed with this plan?"
        if gate_action == "remote_stage":
            gate_label = "Do you authorize the Agent to stage these input files to the selected remote profile?"
        cache_preflight = extracted_params.get("cache_preflight") if isinstance(extracted_params, dict) else None

    gate_block = _create_block_internal(
        session,
        workflow_block.project_id,
        "APPROVAL_GATE",
        {
            "label": gate_label,
            "extracted_params": extracted_params,
            "cache_preflight": cache_preflight,
            "gate_action": gate_action,
            "attempt_number": 1,
            "rejection_history": [],
            "skill": gate_skill,
            "model": model_name,
            "workflow_block_id": workflow_block.id,
        },
        status="PENDING",
        owner_id=owner_id,
    )
    gate_block.parent_id = workflow_block.id
    session.commit()
    session.refresh(gate_block)
    sync_project_tasks(session, workflow_block.project_id)
    return gate_block


async def _handle_workflow_plan_remote_profile_auth(
    session,
    workflow_block: ProjectBlock,
    *,
    owner_id: str,
    model_name: str,
) -> str | None:
    """Resolve or block the remote-profile authorization step for workflow plans."""
    payload = get_block_payload(workflow_block)
    current_step_id = payload.get("current_step_id")
    if not current_step_id:
        return None

    current_step = next(
        (step for step in payload.get("steps", []) if step.get("id") == current_step_id),
        None,
    )
    if not current_step or current_step.get("kind") != "CHECK_REMOTE_PROFILE_AUTH":
        return None

    job_params = await job_parameters.extract_job_parameters_from_conversation(session, workflow_block.project_id)
    if (job_params.get("execution_mode") or "local") != "slurm":
        current_step["status"] = "COMPLETED"
        current_step["auth_status"] = "not_required"
        current_step.setdefault("completed_at", datetime.datetime.utcnow().isoformat() + "Z")
        payload["status"] = "RUNNING"
        _persist_workflow_plan(session, workflow_block, payload)
        return "resume"

    ssh_profile_id, ssh_profile_nickname = await _resolve_ssh_profile_reference(
        owner_id,
        job_params.get("ssh_profile_id"),
        job_params.get("ssh_profile_nickname"),
    )
    profiles = await _list_user_ssh_profiles(owner_id)
    profile = next((item for item in profiles if item.get("id") == ssh_profile_id), None)

    if not profile:
        current_step["status"] = "FOLLOW_UP"
        current_step["error"] = "Select a saved SSH profile before remote staging can continue."
        payload["status"] = "FOLLOW_UP"
        _persist_workflow_plan(session, workflow_block, payload)
        _create_block_internal(
            session,
            workflow_block.project_id,
            "AGENT_PLAN",
            {
                "markdown": "Remote staging is paused because no saved SSH profile is selected. Pick a profile in the approval flow, then try again.",
                "skill": "remote_execution",
                "model": model_name,
            },
            status="DONE",
            owner_id=owner_id,
        )
        return "blocked"

    if not profile.get("local_username") or profile.get("auth_method") != "key_file":
        current_step["status"] = "COMPLETED"
        current_step["auth_status"] = "not_required"
        current_step.setdefault("completed_at", datetime.datetime.utcnow().isoformat() + "Z")
        payload["status"] = "RUNNING"
        _persist_workflow_plan(session, workflow_block, payload)
        return "resume"

    session_info = await _get_ssh_profile_auth_session(owner_id, ssh_profile_id)
    if session_info.get("active"):
        current_step["status"] = "COMPLETED"
        current_step["auth_status"] = "active"
        current_step["ssh_profile_id"] = ssh_profile_id
        current_step["ssh_profile_nickname"] = ssh_profile_nickname
        current_step.setdefault("completed_at", datetime.datetime.utcnow().isoformat() + "Z")
        payload["status"] = "RUNNING"
        _persist_workflow_plan(session, workflow_block, payload)
        return "resume"

    profile_label = profile.get("nickname") or ssh_profile_nickname or profile.get("ssh_host") or ssh_profile_id
    message = (
        f"Remote staging is paused because profile `{profile_label}` is locked. "
        "Open Remote Connection Profiles and unlock the session for that profile, then try again."
    )
    current_step["status"] = "FOLLOW_UP"
    current_step["error"] = message
    current_step["ssh_profile_id"] = ssh_profile_id
    current_step["ssh_profile_nickname"] = ssh_profile_nickname
    payload["status"] = "FOLLOW_UP"
    _persist_workflow_plan(session, workflow_block, payload)
    _create_block_internal(
        session,
        workflow_block.project_id,
        "AGENT_PLAN",
        {
            "markdown": message,
            "skill": "remote_execution",
            "model": model_name,
        },
        status="DONE",
        owner_id=owner_id,
    )
    return "blocked"


def _mark_workflow_plan_approval_complete(session, workflow_block: ProjectBlock, gate_block_id: str) -> None:
    """Mark the current approval step complete after its gate is approved."""
    payload = get_block_payload(workflow_block)
    current_step_id = payload.get("current_step_id")
    if not current_step_id:
        return

    for step in payload.get("steps", []):
        if step.get("id") != current_step_id:
            continue
        if step.get("kind") != "REQUEST_APPROVAL":
            return
        step["status"] = "COMPLETED"
        step["approval_gate_id"] = gate_block_id
        step.setdefault("completed_at", datetime.datetime.utcnow().isoformat() + "Z")
        payload["status"] = "RUNNING"
        _persist_workflow_plan(session, workflow_block, payload)
        return


def _advance_workflow_plan_to_step_kind(
    session,
    workflow_block: ProjectBlock,
    *,
    kind: str,
    approval_gate_id: str | None = None,
) -> None:
    """Move a workflow plan pointer to the next matching pending step."""
    payload = get_block_payload(workflow_block)
    for step in payload.get("steps", []):
        if step.get("kind") != kind:
            continue
        if step.get("status") not in {"PENDING", "WAITING_APPROVAL"}:
            continue
        payload["current_step_id"] = step.get("id")
        payload["status"] = "RUNNING"
        if approval_gate_id:
            step["approval_gate_id"] = approval_gate_id
        _persist_workflow_plan(session, workflow_block, payload)
        return


def _build_reconcile_plan_approval_context(workflow_block: ProjectBlock) -> dict | None:
    """Build a reconcile-specific approval gate payload from plan state."""
    from cortex.plan_replanner import _extract_reconcile_preflight_payload

    payload = get_block_payload(workflow_block)
    if payload.get("plan_type") != "reconcile_bams":
        return None

    preflight_step = next(
        (
            step for step in payload.get("steps", [])
            if step.get("kind") == "CHECK_EXISTING" and step.get("status") == "COMPLETED"
        ),
        None,
    )
    run_step = next(
        (step for step in payload.get("steps", []) if step.get("kind") == "RUN_SCRIPT"),
        None,
    )
    if not isinstance(run_step, dict):
        return None

    preflight_payload = _extract_reconcile_preflight_payload(preflight_step.get("result")) if isinstance(preflight_step, dict) else None
    tool_call = next(
        (
            call for call in (run_step.get("tool_calls") or [])
            if call.get("tool") == "run_allowlisted_script"
        ),
        None,
    )
    params = dict((tool_call or {}).get("params") or {})
    script_args = params.get("script_args") if isinstance(params.get("script_args"), list) else []

    output_root = payload.get("output_directory")
    output_prefix = payload.get("output_prefix") or "reconciled"
    reference = None
    annotation_gtf = None
    annotation_gtf_source = None
    execution_defaults = {}
    annotation_evidence = []
    bam_inputs = []
    if isinstance(preflight_payload, dict):
        reference = preflight_payload.get("reference")
        gtf_info = preflight_payload.get("gtf") or {}
        if isinstance(gtf_info, dict):
            annotation_gtf = gtf_info.get("path")
            annotation_gtf_source = gtf_info.get("source")
        annotation_evidence = preflight_payload.get("annotation_evidence") or []
        inputs_info = preflight_payload.get("inputs") or {}
        if isinstance(inputs_info, dict):
            bam_inputs = inputs_info.get("bams") or []
        execution_info = preflight_payload.get("execution_defaults") or {}
        if isinstance(execution_info, dict):
            execution_defaults = execution_info
        outputs_info = preflight_payload.get("outputs") or {}
        if isinstance(outputs_info, dict):
            output_root = outputs_info.get("output_root") or output_root
            output_prefix = outputs_info.get("output_prefix") or output_prefix

    extracted_params = {
        "plan_type": "reconcile_bams",
        "workflow_block_id": workflow_block.id,
        "run_type": "script",
        "script_id": params.get("script_id"),
        "script_path": params.get("script_path"),
        "script_args": script_args,
        "script_working_directory": params.get("script_working_directory"),
        "sample_name": output_prefix,
        "mode": "RNA",
        "input_type": "bam",
        "input_directory": output_root or ".",
        "output_directory": output_root,
        "output_prefix": output_prefix,
        "reference_genome": [reference] if reference else [],
        "reference": reference,
        "annotation_gtf": annotation_gtf,
        "annotation_gtf_source": annotation_gtf_source,
        "annotation_evidence": annotation_evidence,
        "bam_inputs": bam_inputs,
        "bam_count": len(bam_inputs),
        "gene_prefix": execution_defaults.get("gene_prefix") or "CONSG",
        "tx_prefix": execution_defaults.get("tx_prefix") or "CONST",
        "id_tag": execution_defaults.get("id_tag") or "TX",
        "gene_tag": execution_defaults.get("gene_tag") or "GX",
        "threads": execution_defaults.get("threads"),
        "exon_merge_distance": execution_defaults.get("exon_merge_distance") or 5,
        "min_tpm": execution_defaults.get("min_tpm") if execution_defaults.get("min_tpm") is not None else 1.0,
        "min_samples": execution_defaults.get("min_samples") or 2,
        "filter_known": bool(execution_defaults.get("filter_known")),
        "underlying_script_id": execution_defaults.get("underlying_script_id") or "reconcile_bams/reconcileBams",
        "preflight_summary": preflight_payload,
        "gate_action": "reconcile_bams",
    }
    return {
        "label": "Do you authorize the Agent to reconcile these annotated BAMs?",
        "extracted_params": extracted_params,
        "cache_preflight": None,
        "gate_action": "reconcile_bams",
        "skill": "reconcile_bams",
    }
