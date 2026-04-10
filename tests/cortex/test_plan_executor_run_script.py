import pytest

from cortex.plan_executor import STEP_TOOL_DEFAULTS, execute_step, should_auto_execute


class _FakeSession:
    def commit(self):
        return None

    def refresh(self, _obj):
        return None


class _FakeBlock:
    id = "wf-1"
    project_id = "proj-1"
    status = "PENDING"
    payload_json = "{}"


@pytest.mark.asyncio
async def test_execute_plan_rejects_invalid_plan_before_dispatch(monkeypatch):
    from cortex.plan_executor import execute_plan

    payload = {
        "title": "Invalid plan",
        "project_id": "proj-1",
        "steps": [
            {
                "id": "bad_1",
                "kind": "UNKNOWN_KIND",
                "title": "Unknown",
                "status": "PENDING",
                "depends_on": [],
                "requires_approval": False,
            }
        ],
    }
    persisted = {}

    def _fake_get_block_payload(_workflow_block):
        return payload

    def _fake_persist(_session, _workflow_block, plan_payload):
        persisted.update(plan_payload)

    async def _unexpected_execute_step(*_args, **_kwargs):
        raise AssertionError("execute_step should not run for invalid plans")

    monkeypatch.setattr("cortex.llm_validators.get_block_payload", _fake_get_block_payload)
    monkeypatch.setattr("cortex.plan_executor._persist_step_update", _fake_persist)
    monkeypatch.setattr("cortex.plan_executor.execute_step", _unexpected_execute_step)

    await execute_plan(_FakeSession(), _FakeBlock(), project_id="proj-1")

    assert persisted.get("status") == "FAILED"
    assert persisted.get("validation_error", {}).get("error") == "plan_validation_failed"


@pytest.mark.asyncio
async def test_execute_plan_rejects_project_scope_mismatch(monkeypatch):
    from cortex.plan_executor import execute_plan

    payload = {
        "title": "Mismatched plan",
        "project_id": "proj-2",
        "steps": [
            {
                "id": "s1",
                "kind": "LOCATE_DATA",
                "title": "Locate",
                "status": "PENDING",
                "depends_on": [],
                "requires_approval": False,
            }
        ],
    }
    persisted = {}

    def _fake_get_block_payload(_workflow_block):
        return payload

    def _fake_persist(_session, _workflow_block, plan_payload):
        persisted.update(plan_payload)

    monkeypatch.setattr("cortex.llm_validators.get_block_payload", _fake_get_block_payload)
    monkeypatch.setattr("cortex.plan_executor._persist_step_update", _fake_persist)

    await execute_plan(_FakeSession(), _FakeBlock(), project_id="proj-1")

    assert persisted.get("status") == "FAILED"
    issues = persisted.get("validation_error", {}).get("issues", [])
    assert any(issue.get("code") == "project_id_mismatch" for issue in issues)


@pytest.mark.asyncio
async def test_execute_step_run_script_returns_special_handling(monkeypatch):
    payload = {
        "steps": [
            {
                "id": "step1",
                "kind": "RUN_SCRIPT",
                "title": "Run script",
                "status": "PENDING",
                "requires_approval": True,
            }
        ]
    }

    # Keep this test focused on dispatch behavior only.
    monkeypatch.setattr("cortex.plan_executor._persist_step_update", lambda *_args, **_kwargs: None)

    result = await execute_step(
        _FakeSession(),
        _FakeBlock(),
        "step1",
        plan_payload=payload,
        project_id="proj-1",
    )

    assert result.success is True
    assert result.data == {"action": "special_handling", "kind": "RUN_SCRIPT"}
    assert payload["steps"][0]["status"] == "WAITING_APPROVAL"


def test_run_script_is_not_auto_executed_by_default():
    step = {"kind": "RUN_SCRIPT", "requires_approval": False}
    assert should_auto_execute(step) is False
    assert STEP_TOOL_DEFAULTS["RUN_SCRIPT"] is None


@pytest.mark.asyncio
async def test_execute_step_prepare_de_input_updates_follow_up_de_steps(monkeypatch, tmp_path):
    abundance_path = tmp_path / "reconciled_abundance.tsv"
    abundance_path.write_text(
        "gene_ID\ttranscript_ID\tgko\tjbh\tlwf\texc\n"
        "GENE1\tTX1\t10\t30\t20\t40\n"
        "GENE1\tTX2\t1\t3\t2\t4\n",
        encoding="utf-8",
    )
    payload = {
        "steps": [
            {
                "id": "prep1",
                "kind": "PREPARE_DE_INPUT",
                "title": "Prepare DE inputs",
                "status": "PENDING",
                "depends_on": [],
                "counts_path": str(abundance_path),
                "output_dir": str(tmp_path / "de_inputs"),
                "group_a_label": "AD",
                "group_a_samples": ["exc", "jbh"],
                "group_b_label": "control",
                "group_b_samples": ["gko", "lwf"],
                "level": "gene",
            },
            {
                "id": "run1",
                "kind": "RUN_DE_PIPELINE",
                "title": "Run DE",
                "status": "PENDING",
                "depends_on": ["prep1"],
                "tool_calls": [
                    {
                        "source_key": "edgepython",
                        "tool": "load_data",
                        "params": {"counts_path": "", "sample_info_path": "", "group_column": "condition"},
                    },
                    {
                        "source_key": "edgepython",
                        "tool": "exact_test",
                        "params": {"pair": ["x", "y"], "name": "placeholder"},
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
                "kind": "SAVE_RESULTS",
                "title": "Save results",
                "status": "PENDING",
                "depends_on": ["run1"],
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
                "kind": "GENERATE_DE_PLOT",
                "title": "Plot",
                "status": "PENDING",
                "depends_on": ["run1"],
                "tool_calls": [
                    {
                        "source_key": "edgepython",
                        "tool": "generate_plot",
                        "params": {"plot_type": "volcano", "result_name": "placeholder"},
                    }
                ],
            },
        ]
    }

    monkeypatch.setattr("cortex.plan_executor._persist_step_update", lambda *_args, **_kwargs: None)

    result = await execute_step(
        _FakeSession(),
        _FakeBlock(),
        "prep1",
        plan_payload=payload,
        project_id="proj-1",
    )

    assert result.success is True
    assert payload["steps"][0]["status"] == "COMPLETED"
    assert payload["steps"][1]["tool_calls"][0]["params"]["counts_path"].endswith("_counts.tsv")
    assert payload["steps"][1]["tool_calls"][0]["params"]["sample_info_path"].endswith("_sample_info.csv")
    assert payload["steps"][1]["tool_calls"][1]["params"]["pair"] == ["AD", "control"]
    assert payload["steps"][2]["tool_calls"][0]["params"]["name"].startswith("ad_vs_control")
    assert payload["steps"][3]["tool_calls"][0]["params"]["result_name"].startswith("ad_vs_control")


@pytest.mark.asyncio
async def test_execute_step_run_de_pipeline_executes_tool_calls(monkeypatch):
    payload = {
        "steps": [
            {
                "id": "de1",
                "kind": "RUN_DE_PIPELINE",
                "title": "Run DE",
                "status": "PENDING",
                "requires_approval": False,
                "depends_on": [],
                "tool_calls": [
                    {"source_key": "edgepython", "tool": "load_data", "params": {"counts_path": "/tmp/counts.tsv"}},
                    {"source_key": "edgepython", "tool": "get_top_genes", "params": {"name": "demo"}},
                ],
            }
        ]
    }

    monkeypatch.setattr("cortex.plan_executor._persist_step_update", lambda *_args, **_kwargs: None)
    async def _fake_call_mcp_tool(_source, _tool, _params):
        return {"data": "ok"}

    monkeypatch.setattr(
        "cortex.plan_executor._call_mcp_tool",
        _fake_call_mcp_tool,
    )

    result = await execute_step(
        _FakeSession(),
        _FakeBlock(),
        "de1",
        plan_payload=payload,
        project_id="proj-1",
    )

    assert result.success is True
    assert payload["steps"][0]["status"] == "COMPLETED"
    assert result.data["results"][0]["tool"] == "load_data"


@pytest.mark.asyncio
async def test_execute_step_locate_data_uses_completed_run_script_work_directory(monkeypatch):
    payload = {
        "steps": [
            {
                "id": "run1",
                "kind": "RUN_SCRIPT",
                "title": "Run reconcile",
                "status": "COMPLETED",
                "work_directory": "/tmp/project/workflow5",
            },
            {
                "id": "loc1",
                "kind": "LOCATE_DATA",
                "title": "Locate reconcile outputs",
                "status": "PENDING",
                "depends_on": ["run1"],
                "tool_calls": [
                    {
                        "source_key": "analyzer",
                        "tool": "list_job_files",
                        "params": {"work_dir": "/tmp/project", "extensions": ".tsv,.csv"},
                    }
                ],
            },
        ]
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr("cortex.plan_executor._persist_step_update", lambda *_args, **_kwargs: None)

    async def _fake_call_mcp_tool(source, tool, params):
        captured["source"] = source
        captured["tool"] = tool
        captured["params"] = dict(params)
        return {"success": True, "work_dir": params.get("work_dir"), "files": []}

    monkeypatch.setattr("cortex.plan_executor._call_mcp_tool", _fake_call_mcp_tool)

    result = await execute_step(
        _FakeSession(),
        _FakeBlock(),
        "loc1",
        plan_payload=payload,
        project_id="proj-1",
    )

    assert result.success is True
    assert captured["source"] == "analyzer"
    assert captured["tool"] == "list_job_files"
    assert captured["params"]["work_dir"] == "/tmp/project/workflow5"


@pytest.mark.asyncio
async def test_execute_step_check_existing_uses_plan_work_dir_for_find_file(monkeypatch):
    payload = {
        "plan_type": "run_de_pipeline",
        "work_dir": "/tmp/project/workflow6",
        "steps": [
            {
                "id": "check1",
                "kind": "CHECK_EXISTING",
                "title": "Check for existing DE results",
                "status": "PENDING",
                "tool_calls": [
                    {
                        "source_key": "analyzer",
                        "tool": "find_file",
                        "params": {"file_name": "de_results"},
                    }
                ],
            },
        ],
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr("cortex.plan_executor._persist_step_update", lambda *_args, **_kwargs: None)

    async def _fake_call_mcp_tool(source, tool, params):
        captured["source"] = source
        captured["tool"] = tool
        captured["params"] = dict(params)
        return {"success": True, "paths": []}

    monkeypatch.setattr("cortex.plan_executor._call_mcp_tool", _fake_call_mcp_tool)

    result = await execute_step(
        _FakeSession(),
        _FakeBlock(),
        "check1",
        plan_payload=payload,
        project_id="proj-1",
    )

    assert result.success is True
    assert captured["source"] == "analyzer"
    assert captured["tool"] == "find_file"
    assert captured["params"]["work_dir"] == "/tmp/project/workflow6"


@pytest.mark.asyncio
async def test_execute_step_check_existing_missing_file_is_nonfatal(monkeypatch):
    payload = {
        "plan_type": "run_de_pipeline",
        "work_dir": "/tmp/project/workflow6",
        "steps": [
            {
                "id": "check1",
                "kind": "CHECK_EXISTING",
                "title": "Check for existing DE results",
                "status": "PENDING",
                "tool_calls": [
                    {
                        "source_key": "analyzer",
                        "tool": "find_file",
                        "params": {"file_name": "de_results"},
                    }
                ],
            },
        ],
    }

    monkeypatch.setattr("cortex.plan_executor._persist_step_update", lambda *_args, **_kwargs: None)

    async def _fake_call_mcp_tool(_source, _tool, params):
        return {
            "success": False,
            "error": "File not found (checked result directories only, ignored work/ folder)",
            "search_term": params.get("file_name"),
            "work_dir": params.get("work_dir"),
        }

    monkeypatch.setattr("cortex.plan_executor._call_mcp_tool", _fake_call_mcp_tool)

    result = await execute_step(
        _FakeSession(),
        _FakeBlock(),
        "check1",
        plan_payload=payload,
        project_id="proj-1",
    )

    assert result.success is True
    wrapper = result.data["results"][0]["result"]
    assert wrapper["success"] is True
    assert wrapper["file_count"] == 0
    assert wrapper["paths"] == []
    assert wrapper["not_found"] is True


@pytest.mark.asyncio
async def test_execute_step_save_results_uses_workflow_scoped_output_dir(monkeypatch):
    payload = {
        "plan_type": "run_de_pipeline",
        "work_dir": "/tmp/project/workflow6",
        "steps": [
            {
                "id": "save1",
                "kind": "SAVE_RESULTS",
                "title": "Save results",
                "status": "PENDING",
                "tool_calls": [
                    {
                        "source_key": "edgepython",
                        "tool": "save_results",
                        "params": {"name": "ad_vs_control_gene", "format": "tsv"},
                    }
                ],
            },
        ],
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr("cortex.plan_executor._persist_step_update", lambda *_args, **_kwargs: None)

    async def _fake_call_mcp_tool(source, tool, params):
        captured["source"] = source
        captured["tool"] = tool
        captured["params"] = dict(params)
        return {"data": "ok"}

    monkeypatch.setattr("cortex.plan_executor._call_mcp_tool", _fake_call_mcp_tool)

    result = await execute_step(
        _FakeSession(),
        _FakeBlock(),
        "save1",
        plan_payload=payload,
        project_id="proj-1",
    )

    assert result.success is True
    assert captured["source"] == "edgepython"
    assert captured["tool"] == "save_results"
    assert captured["params"]["output_path"] == "/tmp/project/workflow6/de_results/de_results.tsv"
