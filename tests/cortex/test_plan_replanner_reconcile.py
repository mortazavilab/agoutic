import json

from cortex.plan_replanner import _extract_reconcile_preflight_payload


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
