import json
from types import SimpleNamespace

from cortex.plan_replanner import _extract_reconcile_preflight_payload, replan_with_new_info


def test_extract_reconcile_preflight_payload_detects_manual_gtf_request():
    payload = {
        "status": "needs_manual_gtf",
        "required_input": {"field": "annotation_gtf", "reason": "No default GTF"},
    }
    results = [
        {
            "tool": "run_allowlisted_script",
            "result": {
                "script_id": "reconcile_bams/reconcile_bams",
                "stdout": json.dumps(payload),
            },
        }
    ]

    extracted = _extract_reconcile_preflight_payload(results)
    assert extracted == payload


def test_extract_reconcile_preflight_payload_ignores_other_scripts():
    results = [
        {
            "tool": "run_allowlisted_script",
            "result": {
                "script_id": "reconcile_bams/check_workflow_references",
                "stdout": json.dumps({"ok": True}),
            },
        }
    ]

    assert _extract_reconcile_preflight_payload(results) is None


def test_replan_with_new_info_splits_reconcile_plan_by_reference_group():
    split_payload = {
        "success": True,
        "status": "split_by_reference",
        "reference_groups": [
            {
                "status": "preflight_ready",
                "reference": "GRCh38",
                "gtf": {"path": "/refs/GRCh38.gtf", "source": "default"},
                "inputs": {
                    "count": 1,
                    "bams": [
                        {"sample": "a", "reference": "GRCh38", "path": "/proj/workflow2/annot/a.GRCh38.annotated.bam"}
                    ],
                },
                "outputs": {"output_prefix": "reconciled", "output_root": "/proj"},
            },
            {
                "status": "preflight_ready",
                "reference": "mm39",
                "gtf": {"path": "/refs/mm39.gtf", "source": "default"},
                "inputs": {
                    "count": 1,
                    "bams": [
                        {"sample": "b", "reference": "mm39", "path": "/proj/workflow3/annot/b.mm39.annotated.bam"}
                    ],
                },
                "outputs": {"output_prefix": "reconciled", "output_root": "/proj"},
            },
        ],
        "outputs": {"output_prefix": "reconciled", "output_root": "/proj"},
    }
    workflow_block = SimpleNamespace(
        payload_json=json.dumps(
            {
                "plan_type": "reconcile_bams",
                "status": "RUNNING",
                "current_step_id": "preflight",
                "goal": "Reconcile annotated BAM outputs using a shared reference",
                "output_prefix": "reconciled",
                "output_directory": "/proj",
                "steps": [
                    {"id": "locate", "kind": "LOCATE_DATA", "status": "COMPLETED", "order_index": 0, "depends_on": []},
                    {"id": "preflight", "kind": "CHECK_EXISTING", "status": "COMPLETED", "order_index": 1, "depends_on": ["locate"]},
                    {"id": "approve", "kind": "REQUEST_APPROVAL", "status": "PENDING", "order_index": 2, "depends_on": ["preflight"]},
                    {"id": "run", "kind": "RUN_SCRIPT", "status": "PENDING", "order_index": 3, "depends_on": ["approve"]},
                ],
            }
        )
    )

    class _Session:
        def commit(self):
            return None

        def refresh(self, _block):
            return None

    updated = replan_with_new_info(
        _Session(),
        workflow_block,
        "preflight",
        {
            "results": [
                {
                    "tool": "run_allowlisted_script",
                    "result": {
                        "script_id": "reconcile_bams/reconcile_bams",
                        "stdout": json.dumps(split_payload),
                    },
                }
            ]
        },
    )

    assert updated is not None
    assert updated["status"] == "WAITING_APPROVAL"
    assert updated["reference_groups"] == ["GRCh38", "mm39"]
    approval_titles = [step["title"] for step in updated["steps"] if step.get("kind") == "REQUEST_APPROVAL"]
    assert approval_titles == [
        "Approve reconcile BAM execution for all detected references (GRCh38, mm39)",
        "Approve reconcile BAM execution for mm39",
    ]
    approval_steps = [step for step in updated["steps"] if step.get("kind") == "REQUEST_APPROVAL"]
    assert approval_steps[0]["shared_reconcile_authorization"] is True
    assert approval_steps[1]["auto_approve_from_shared_reconcile_authorization"] is True
    run_steps = [step for step in updated["steps"] if step.get("kind") == "RUN_SCRIPT"]
    assert run_steps[0]["output_directory"] == "/proj/workflow1"
    assert run_steps[1]["output_directory"] == "/proj/workflow2"
