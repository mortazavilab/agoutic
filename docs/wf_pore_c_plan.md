## Plan: wf-pore-c MVP Refactor

Remove the `mode=PORE_C` idea completely. Treat `mode` as Dogme-only data, introduce an execution-layer `workflow_key` for dispatch, and refactor every mode-based branch so it either keys off `workflow_key` or is explicitly fenced as Dogme-only. Because Cortex already uses `workflow_type` for plan/task taxonomy, the execution-layer discriminator should be `workflow_key` rather than another overloaded `workflow_type` field. Ship this in three phases behind `WF_PORE_C_ENABLED=false` by default.

## Status

- Phase 1: complete.
- Phase 2: complete as of 2026-05-19.
- Phase 2 closure verification:
	- `WF_PORE_C_ENABLED=false pytest tests/cortex tests/launchpad tests/analyzer -q` -> `1839 passed, 30 warnings`
	- `WF_PORE_C_ENABLED=true pytest tests/cortex tests/launchpad tests/analyzer -q` -> `1839 passed, 30 warnings`
	- Regression-count guardrail held: `1839 >= 1766 + 61 = 1827`, so no Dogme-path tests were silently lost or skipped while Analyzer coverage was included.
	- No Dogme-specific regression remained under either flag state at closure.
	- `WF_PORE_C_ENABLED` remains off by default in both `/Users/eli/code/agoutic/cortex/config.py` and `/Users/eli/code/agoutic/launchpad/config.py`.
	- Routing-drift fix landed in `/Users/eli/code/agoutic/cortex/job_polling.py` and `/Users/eli/code/agoutic/cortex/analysis_helpers.py`: the resolved `wf_pore_c_enabled` decision is now passed explicitly into the summary helpers so fallback rendering cannot drift back into the wf-pore-c card based only on Analyzer metadata such as `workflow_key="wf_pore_c"`.
- Phase 3: complete as of 2026-05-20.
- Phase 3 closure verification:
	- `WF_PORE_C_ENABLED=false pytest tests/cortex tests/launchpad tests/analyzer tests/ui -q` -> `1988 passed, 30 warnings`
	- `WF_PORE_C_ENABLED=true pytest tests/cortex tests/launchpad tests/analyzer tests/ui -q` -> `1988 passed, 29 warnings`
	- Regression-count guardrail held: `1988 > 1839`, so the Phase 3 remote and UI surfaces increased coverage over the Phase 2 baseline without silently losing or skipping Dogme-path tests.
	- Identical pass counts under both flag states confirmed that `WF_PORE_C_ENABLED` still controls behavior rather than test inclusion.
	- No Dogme remote execution, staging, sync, or UI regression remained under either flag state at closure.
	- `WF_PORE_C_ENABLED` remains off by default in both `/Users/eli/code/agoutic/cortex/config.py` and `/Users/eli/code/agoutic/launchpad/config.py`.
	- UI AST-harness compatibility now lives only in `/Users/eli/code/agoutic/tests/ui/test_app_source_helpers.py`; `/Users/eli/code/agoutic/ui/appui_block_part2.py` no longer carries a production fallback branch, so a missing helper fails loudly in production instead of silently degrading to stale Dogme metadata.
	- Backward-compat workflow normalization now runs in both conversation-state reconstruction paths in `/Users/eli/code/agoutic/cortex/conversation_state.py`: the slow history rebuild path and the fast cached-state restore path both backfill legacy mode-only state to `workflow_key="dogme"` so old chats and jobs continue to route correctly.

**Steps**
1. Add `WF_PORE_C_ENABLED` to `/Users/eli/code/agoutic/cortex/config.py` and `/Users/eli/code/agoutic/launchpad/config.py`, defaulting to off. With the flag off, new routing and submission paths stay inaccessible and existing Dogme behavior remains the default.
2. Introduce execution-layer `workflow_key` support in `/Users/eli/code/agoutic/launchpad/schemas.py`, `/Users/eli/code/agoutic/launchpad/models.py`, `/Users/eli/code/agoutic/launchpad/db.py`, `/Users/eli/code/agoutic/launchpad/app.py`, and `/Users/eli/code/agoutic/launchpad/backends/base.py`. Make `mode` optional and Dogme-scoped instead of a universal discriminator.
3. Add an Alembic migration in `/Users/eli/code/agoutic/alembic/versions/` using the existing batch pattern seen in `/Users/eli/code/agoutic/alembic/versions/4a7d9c2b1f0e_add_workflow_identity_fields.py`. The migration should add `workflow_key`, backfill existing Launchpad rows to `dogme`, relax `mode` to nullable for non-Dogme jobs, and keep a runtime fallback of `workflow_key or "dogme"` so old fixtures and pre-migration records still read cleanly.
4. Add a workflow-family abstraction under Launchpad, not more `if/elif` growth in the existing executors. Recommended shape: a new `WorkflowExecutor` protocol plus registry under `/Users/eli/code/agoutic/launchpad/workflow_executors/` with methods such as `validate_inputs()`, `stage_inputs()`, `render_nextflow_config()`, `build_command()`, `result_sync_spec()`, and `summary_contract()`. Keep the existing `ExecutionBackend` protocol in `/Users/eli/code/agoutic/launchpad/backends/base.py` for the local-vs-SLURM axis; `launchpad/app.py` should resolve both axes and pass the selected `WorkflowExecutor` into the backend.
5. Extract the current Dogme-specific logic from `/Users/eli/code/agoutic/launchpad/nextflow_executor.py` and `/Users/eli/code/agoutic/launchpad/backends/slurm_backend.py` into a `DogmeWorkflowExecutor` first. This is the foundation that removes latent mode leakage before Pore-C lands.
6. Phase 1 MVP: add `/Users/eli/code/agoutic/skills/run_wf_pore_c/manifest.yaml` and `/Users/eli/code/agoutic/skills/run_wf_pore_c/SKILL.md`, then wire routing and planning in `/Users/eli/code/agoutic/cortex/llm_validators.py`, `/Users/eli/code/agoutic/cortex/planner.py`, `/Users/eli/code/agoutic/cortex/plan_templates.py`, `/Users/eli/code/agoutic/cortex/tag_parser.py`, `/Users/eli/code/agoutic/cortex/job_parameters.py`, `/Users/eli/code/agoutic/cortex/chat_approval.py`, and `/Users/eli/code/agoutic/cortex/workflow_submission.py`. Phase 1 should only collect/validate parameters and render a dry-run command preview; it should not submit a real wf-pore-c job yet.
7. Phase 1 parameter handling must validate every local BAM, FASTQ, FASTA, VCF, and sample-sheet path through `/Users/eli/code/agoutic/cortex/user_jail.py`, reject traversal, and surface deterministic validation errors for missing reference sidecars. The dry-run command contract must include `nextflow run epi2me-labs/wf-pore-c -r v1.3.1`, the agreed default outputs, and the exact input modality chosen.
8. Phase 2 MVP: implement a `WfPoreCWorkflowExecutor` and enable local execution only. Use the existing `workflowN` output layout from `/Users/eli/code/agoutic/common/workflow_paths.py`, but move Nextflow `-work-dir` outside the output folder by reusing Launchpad’s existing local scratch/work-dir conventions instead of `workflowN/work`. Do not overwrite or reuse `dogme.profile`; use executor-scoped AGOUTIC-generated config/profile artifacts so Dogme and Pore-C settings cannot collide.
9. Phase 2 local execution must explicitly handle the operational items that are already visible in the repo surfaces: revision pinning to `v1.3.1`; reference preflight for FASTA plus `.fai` and chromsizes; symlink-by-default staging for large BAM/FASTQ inputs with copy fallback only when symlinks are unsafe; and correct report artifact naming as `wf-pore-c-report.html` rather than the current generic Dogme report assumptions.
10. Phase 2 also adds Analyzer recognition and the automatic summary card. Update `/Users/eli/code/agoutic/analyzer/analysis_engine.py`, `/Users/eli/code/agoutic/analyzer/schemas.py`, `/Users/eli/code/agoutic/cortex/job_polling.py`, and `/Users/eli/code/agoutic/cortex/analysis_helpers.py` so Pore-C never enters DNA/RNA/CDNA summary branches. The summary contract should include artifact presence for `pairs.gz`, `.mcool`, optional `.hic`, `wf-pore-c-report.html`, and other enabled outputs; parsed `pairs.stats.txt` metrics including total pairs, cis/trans ratio, and duplicate rate when present; inferred sample alias from `sample` or sample sheet; reference/cutter/revision metadata; and warnings for missing requested outputs or sparse metrics.
11. Phase 3 MVP: enable SLURM execution through the same `WorkflowExecutor` interface in `/Users/eli/code/agoutic/launchpad/backends/slurm_backend.py`. Add remote staging for BAM/FASTQ plus FASTA and optional VCF/sample sheet, preserve separate remote work and output paths, and ensure the remote command builder owns the same revision pin, work-dir placement, and report filename contract as local execution.
12. Phase 3 SLURM work must explicitly include container pre-pull or image staging for nodes without internet using the existing remote Apptainer cache roots already present in `slurm_backend.py`, plus workflow-specific result sync/import rules in `/Users/eli/code/agoutic/launchpad/import_workflows.py` and manual retry flows already exposed by the sync toolchain. Sync/import must include Pore-C directories such as `pairs`, `cooler`, `hi-c`, `ingress_results`, `paired_end`, `paireds`, `chromunity`, and `filtered_out`, along with `wf-pore-c-report.html`.
13. Phase 3 finishes UI and compatibility cleanup in `/Users/eli/code/agoutic/ui/appui_block_part1.py`, `/Users/eli/code/agoutic/ui/appui_block_part2.py`, `/Users/eli/code/agoutic/cortex/conversation_state.py`, `/Users/eli/code/agoutic/cortex/schemas.py`, `/Users/eli/code/agoutic/cortex/context_injection.py`, `/Users/eli/code/agoutic/cortex/data_call_generator.py`, `/Users/eli/code/agoutic/cortex/agent_engine.py`, `/Users/eli/code/agoutic/cortex/task_service.py`, `/Users/eli/code/agoutic/cortex/routes/conversations.py`, and `/Users/eli/code/agoutic/cortex/memory_service.py`. Existing chats and jobs that only carry `mode` must still resolve as Dogme through fallback inference, while new Pore-C runs use `workflow_key` end-to-end.
	Closure note: the production `ui/appui_block_part2.py` fallback branch was removed rather than preserved as a silent runtime escape hatch, and compatibility for AST-isolated helper tests was moved into the test harness. Conversation-state compatibility also landed on both the slow reconstruction path and the fast cached-state path so legacy mode-only state normalizes to Dogme before skill grouping or context injection runs.
14. Add tests phase-by-phase instead of as a monolithic tail step. Phase 1 should cover feature-flag gating, routing, parameter extraction, jailed path validation, migration/backfill helpers, and old-row compatibility. Phase 2 should cover local command building, work-dir placement, reference preflight, report naming, and summary-card generation. Phase 3 should cover SLURM staging, container cache/pre-pull behavior, result sync/import, manual sync retry, and UI round-tripping of Pore-C parameters.
15. Add workflow-abstraction contract tests separate from Pore-C behavior tests. Create a dedicated Launchpad test module for the `WorkflowExecutor` registry and protocol that verifies known keys resolve to the correct executor, every registered executor exposes the required methods, and unknown `workflow_key` values fail with a clean user-facing error.


**Relevant files**
- `/Users/eli/code/agoutic/cortex/config.py` — add `WF_PORE_C_ENABLED`
- `/Users/eli/code/agoutic/launchpad/config.py` — add `WF_PORE_C_ENABLED` and any workflow-executor config knobs
- `/Users/eli/code/agoutic/skills/run_wf_pore_c/manifest.yaml` — new manifest-backed skill
- `/Users/eli/code/agoutic/skills/run_wf_pore_c/SKILL.md` — new skill instructions and approval contract
- `/Users/eli/code/agoutic/cortex/llm_validators.py` — skill auto-switch routing and feature-flag gating
- `/Users/eli/code/agoutic/cortex/planner.py` — new deterministic plan dispatch
- `/Users/eli/code/agoutic/cortex/plan_templates.py` — `run_wf_pore_c` template
- `/Users/eli/code/agoutic/cortex/job_parameters.py` — Pore-C parameter extraction and normalization
- `/Users/eli/code/agoutic/cortex/user_jail.py` — local path validation and jail enforcement
- `/Users/eli/code/agoutic/cortex/chat_approval.py` — approval payloads without Dogme-only assumptions
- `/Users/eli/code/agoutic/cortex/workflow_submission.py` — generic workflow submit bridge
- `/Users/eli/code/agoutic/cortex/job_polling.py` — post-run auto-summary routing and memory capture compatibility
- `/Users/eli/code/agoutic/cortex/analysis_helpers.py` — summary formatting
- `/Users/eli/code/agoutic/cortex/conversation_state.py` — add workflow-key compatibility to reconstructed chat state
- `/Users/eli/code/agoutic/cortex/schemas.py` — add workflow-key field to `ConversationState`
- `/Users/eli/code/agoutic/cortex/context_injection.py` — job-context injection keyed by workflow
- `/Users/eli/code/agoutic/cortex/data_call_generator.py` — analysis skill/file browsing logic for new workflow
- `/Users/eli/code/agoutic/cortex/agent_engine.py` — available-skills display and analysis-skill grouping
- `/Users/eli/code/agoutic/cortex/task_service.py` — task projection and job metadata compatibility
- `/Users/eli/code/agoutic/cortex/routes/conversations.py` — linked-job payload compatibility for old/new rows
- `/Users/eli/code/agoutic/cortex/memory_service.py` — store workflow identity without mode leakage
- `/Users/eli/code/agoutic/launchpad/backends/base.py` — extend `SubmitParams` with `workflow_key` and executor reference
- `/Users/eli/code/agoutic/launchpad/workflow_executors/base.py` — new `WorkflowExecutor` protocol
- `/Users/eli/code/agoutic/launchpad/workflow_executors/__init__.py` — workflow executor registry/dispatch
- `/Users/eli/code/agoutic/launchpad/workflow_executors/dogme.py` — extracted Dogme implementation
- `/Users/eli/code/agoutic/launchpad/workflow_executors/wf_pore_c.py` — new Pore-C implementation
- `/Users/eli/code/agoutic/launchpad/schemas.py` — execution-layer `workflow_key`, nullable `mode`, workflow-specific params
- `/Users/eli/code/agoutic/launchpad/models.py` — persisted `workflow_key` and nullable `mode`
- `/Users/eli/code/agoutic/launchpad/db.py` — fallback helpers and API serialization
- `/Users/eli/code/agoutic/launchpad/mcp_tools.py` — preserve or extend workflow-keyed MCP submission/dry-run exposure
- `/Users/eli/code/agoutic/launchpad/mcp_server.py` — MCP surface should remain able to submit or preview wf-pore-c via `workflow_key`

- `/Users/eli/code/agoutic/launchpad/app.py` — two-axis dispatch: execution backend plus workflow executor
- `/Users/eli/code/agoutic/launchpad/nextflow_executor.py` — reduced to generic local driver
- `/Users/eli/code/agoutic/launchpad/backends/slurm_backend.py` — generic remote driver plus workflow-specific sync/config hooks
- `/Users/eli/code/agoutic/launchpad/import_workflows.py` — workflow-specific import/sync patterns
- `/Users/eli/code/agoutic/alembic/versions/` — migration for `workflow_key` and `mode` nullability
- `/Users/eli/code/agoutic/analyzer/analysis_engine.py` — Pore-C categorization and summary parsing
- `/Users/eli/code/agoutic/analyzer/schemas.py` — summary schema updates for non-Dogme workflows
- `/Users/eli/code/agoutic/ui/appui_block_part1.py` — generic approval editor
- `/Users/eli/code/agoutic/ui/appui_block_part2.py` — generic execution metadata display
- `/Users/eli/code/agoutic/common/workflow_paths.py` — preserve `workflowN` allocation while moving Nextflow work-dir elsewhere
- `/Users/eli/code/agoutic/tests/cortex/` — routing, migration compatibility, approval, and summary tests
- `/Users/eli/code/agoutic/tests/launchpad/` — executor, schema, local/SLURM, and sync tests
- `/Users/eli/code/agoutic/tests/analyzer/` — Pore-C summary tests
- `/Users/eli/code/agoutic/tests/ui/` — approval and run-card tests

**Verification**
1. Phase 1 acceptance: with `WF_PORE_C_ENABLED=false`, existing Dogme routing and submission behavior is unchanged; with the flag enabled, Pore-C prompts route to the new skill, parameter extraction collects BAM/FASTQ plus FASTA and optional VCF/sample-sheet inputs, jailed path validation rejects traversal, and dry-run output shows `nextflow run epi2me-labs/wf-pore-c -r v1.3.1` with the agreed defaults but does not launch a real job.
2. Phase 1 compatibility: pre-existing Launchpad rows without `workflow_key`, old `EXECUTION_JOB` blocks that only carry `mode`, and old conversation states that only carry `sample_type` still resolve as Dogme.
3. Phase 2 acceptance: local wf-pore-c execution uses `workflow_key`, not mode-branching; `mode` is absent or ignored for Pore-C rows; Nextflow `-work-dir` sits outside the workflow output folder; report artifact discovery expects `wf-pore-c-report.html`; reference preflight runs before launch; and Analyzer plus auto-summary produce the defined summary contract without entering DNA/RNA/CDNA branches.
	Closure note: on 2026-05-19 both `WF_PORE_C_ENABLED=false` and `WF_PORE_C_ENABLED=true` full Cortex + Launchpad + Analyzer sweeps finished with `1839 passed`, exceeding the minimum `1827` regression-count floor.
4. Phase 3 acceptance: SLURM execution stages explicit inputs safely, uses the existing remote cache roots for container/image preparation, preserves separate remote work and output paths, syncs the Pore-C result tree plus `wf-pore-c-report.html`, and manual sync retry still works.
	Closure note: on 2026-05-20 both `WF_PORE_C_ENABLED=false` and `WF_PORE_C_ENABLED=true` full Cortex + Launchpad + Analyzer + UI sweeps finished with `1988 passed`. That count exceeded the `1839` Phase 2 baseline and stayed identical under both flag states, confirming that Phase 3 added coverage without losing Dogme-path tests or reintroducing flag-gated test-inclusion drift.
5. UI acceptance: approval editing and run cards round-trip Pore-C parameters without hardcoded Dogme mode pickers or misleading Mode-only labels.
	Closure note: the UI helper fallback used only by the AST harness was removed from production code, so missing runtime helpers now fail loudly instead of silently falling back to Dogme metadata.
6. MCP acceptance: when the feature flag is enabled and the relevant phase is complete, the Launchpad MCP layer can preview or submit wf-pore-c using `workflow_key`, and an unknown key returns a clean error rather than silently falling back to Dogme.

**Decisions**
- Design note: `workflow_type` and `workflow_key` are intentionally adjacent but not interchangeable. `workflow_type` stays in Cortex as plan/task taxonomy such as `local_sample_intake` or `remote_sample_intake`, while `workflow_key` is the execution-family selector for Launchpad jobs such as `dogme` or `wf_pore_c`. A single plan can therefore have `workflow_type=local_sample_intake` and eventually submit a job with `workflow_key=wf_pore_c`; any translation between them must happen explicitly at the submit boundary, never by shared naming or implicit fallback.

- No `mode="PORE_C"` compatibility shim. `mode` becomes Dogme-only optional data; all workflow dispatch moves to `workflow_key` or explicit Dogme-only helpers.
- Do not overload execution semantics onto Cortex’s existing `workflow_type` plan/task field. Use `workflow_key` for execution and keep `workflow_type` for its current plan/task meaning.
- Use a `WorkflowExecutor` protocol plus registry for workflow-family behavior, layered under the existing local/SLURM `ExecutionBackend` abstraction.
- Keep the existing `dogme_jobs` table/model name during MVP if that avoids unnecessary churn; extend semantics first and rename only in a later cleanup.
- `WF_PORE_C_ENABLED` stays off by default until at least Phase 2 local execution is stable.
- The automatic summary card contract is defined up front and must include artifact inventory, parsed `pairs.stats.txt` metrics when present, sample alias, reference/cutter/revision metadata, and missing-output warnings.
- Revision pinning to `v1.3.1`, explicit report-name handling for `wf-pore-c-report.html`, container pre-pull/cache handling for SLURM, and reference-index preflight are required MVP items, not stretch goals.

**Scope boundaries**
- Included: feature-flagged workflow-key refactor, migration/backfill, dry-run planning, local execution, SLURM execution, result sync/import, Analyzer recognition, summary card, UI generalization, and phased test coverage.
- Excluded: ARM enablement, a generalized plugin marketplace, and broad renames such as changing every historical `dogme_jobs` identifier during the MVP.