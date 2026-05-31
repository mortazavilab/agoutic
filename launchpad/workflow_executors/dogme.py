"""Dogme workflow-family executor."""

from __future__ import annotations

import shlex
from pathlib import Path, PurePosixPath
from typing import Any

from launchpad.config import DOGME_REPO, REFERENCE_GENOMES, DogmeMode
from launchpad.nextflow_executor import (
    NextflowConfig,
    resolve_dogme_profile_content,
    resolve_dogme_profile_task_runtime_exports,
)
from launchpad.workflow_executors.base import (
    WorkflowExecutor,
    WorkflowPreviewResult,
    ensure_path,
    sample_name_or_default,
)


_OPENCHROMATIN_WRAPPER_DIRNAME = ".agoutic-openchrom-bin"
_OPENCHROMATIN_RUNTIME_LIBRARY_CANDIDATE_GROUPS = (
    (
        "/lib64/libgomp.so.1",
        "/usr/lib64/libgomp.so.1",
        "/lib/x86_64-linux-gnu/libgomp.so.1",
        "/usr/lib/x86_64-linux-gnu/libgomp.so.1",
    ),
    (
        "/lib64/libstdc++.so.6",
        "/usr/lib64/libstdc++.so.6",
        "/lib/x86_64-linux-gnu/libstdc++.so.6",
        "/usr/lib/x86_64-linux-gnu/libstdc++.so.6",
    ),
    (
        "/lib64/libgcc_s.so.1",
        "/usr/lib64/libgcc_s.so.1",
        "/lib/x86_64-linux-gnu/libgcc_s.so.1",
        "/usr/lib/x86_64-linux-gnu/libgcc_s.so.1",
    ),
)
_DOGME_FASTQ_SINGLE_INPUT_ERROR = (
    "Dogme fastqCDNA currently supports one FASTQ file per sample. "
    "Concatenate your files manually, or contact the admin to enable multi-FASTQ support."
)


def _normalize_reference_id(reference_id: str) -> str:
    return (reference_id or "default").strip().lower()


def _derive_remote_roots(params: Any, profile: Any) -> dict[str, str]:
    remote_base_path = (params.remote_base_path or profile.remote_base_path or "").strip()
    if not remote_base_path:
        raise ValueError("SLURM execution requires remote_base_path on the request or SSH profile")

    base_path = PurePosixPath(remote_base_path)
    return {
        "remote_base_path": str(base_path),
        "ref_root": str(base_path / "ref"),
        "data_root": str(base_path / "data"),
    }


class DogmeWorkflowExecutor(WorkflowExecutor):
    workflow_key = "dogme"
    supports_submission = True

    def validate_submission(self, *, mode: str | None) -> None:
        cleaned_mode = str(mode or "").strip().upper()
        if not cleaned_mode:
            raise ValueError("workflow_key 'dogme' requires a mode such as DNA, RNA, or CDNA")

    def remote_validate_submission(self, *, request: Any) -> None:
        self.validate_submission(mode=getattr(request, "mode", None))

    async def remote_stage_inputs(
        self,
        *,
        request: Any,
        params: Any,
        profile: Any,
        conn: Any,
        run_uuid: str | None,
        on_progress: Any | None = None,
        transfer_id: str | None = None,
    ) -> dict[str, Any]:
        remote_work = str(getattr(params, "remote_work_dir", None) or "").strip()
        remote_input = str(getattr(params, "data_cache_path", None) or getattr(params, "staged_remote_input_path", None) or "").strip()
        if not remote_work or not remote_input:
            return {}

        input_type = (params.input_type or "pod5").strip().lower()
        link_dir_name = {
            "pod5": "pod5",
            "bam": "bams",
            "fastq": "fastqs",
            "fq": "fastqs",
        }.get(input_type, "pod5")
        link_path = str(PurePosixPath(remote_work) / link_dir_name)
        await conn.mkdir_p(remote_work)

        if input_type == "bam" and (params.entry_point or "").strip().lower() == "remap":
            sample_slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in (params.sample_name or "sample"))
            while "--" in sample_slug:
                sample_slug = sample_slug.replace("--", "-")
            sample_slug = sample_slug.strip("-") or "sample"
            alias_dir = link_path
            alias_path = str(PurePosixPath(alias_dir) / f"{sample_slug}.unmapped.bam")
            await conn.run(f"rm -rf {shlex.quote(alias_dir)}", check=True)
            await conn.run(f"mkdir -p {shlex.quote(alias_dir)}", check=True)
            await conn.run(f"ln -sfn {shlex.quote(remote_input)} {shlex.quote(alias_path)}", check=True)
        elif input_type == "fastq" and (params.entry_point or "").strip().lower() == "fastqcdna":
            approved_sample = sample_name_or_default(
                getattr(params, "sample_name", None) or getattr(request, "sample_name", None),
                fallback_path=remote_input,
            )
            alias_dir = link_path
            alias_prefix = str(PurePosixPath(alias_dir) / approved_sample)
            command = (
                f"rm -rf {shlex.quote(alias_dir)} && "
                f"mkdir -p {shlex.quote(alias_dir)} && "
                f"if [ -d {shlex.quote(remote_input)} ]; then "
                f"candidates=$(find {shlex.quote(remote_input)} -maxdepth 1 -type f \\( "
                f"-name '*.fastq' -o -name '*.fastq.gz' -o -name '*.fq' -o -name '*.fq.gz' \\) | LC_ALL=C sort); "
                f"candidate_count=$(printf '%s\\n' \"$candidates\" | sed '/^$/d' | wc -l | tr -d ' '); "
                f"if [ \"$candidate_count\" -eq 0 ]; then echo {shlex.quote(f'No FASTQ files found in directory: {remote_input}')} >&2; exit 1; fi; "
                f"if [ \"$candidate_count\" -ne 1 ]; then echo {shlex.quote(_DOGME_FASTQ_SINGLE_INPUT_ERROR)} >&2; exit 1; fi; "
                f"candidate=$(printf '%s\\n' \"$candidates\" | sed -n '1p'); "
                f"else candidate={shlex.quote(remote_input)}; fi; "
                f"case \"$candidate\" in "
                f"*.fastq.gz|*.fq.gz) alias_path={shlex.quote(alias_prefix + '.fastq.gz')} ;; "
                f"*.fastq|*.fq) alias_path={shlex.quote(alias_prefix + '.fastq')} ;; "
                f"*) echo {shlex.quote(f'Expected a FASTQ file for fastqCDNA input: {remote_input}')} >&2; exit 1 ;; "
                f"esac; ln -sfn \"$candidate\" \"$alias_path\""
            )
            await conn.run(command, check=True)
        else:
            await conn.run(f"ln -sfn {shlex.quote(remote_input)} {shlex.quote(link_path)}", check=True)

        return {"workflow_remote_input": link_path}

    async def remote_reference_assets(
        self,
        *,
        request: Any,
        params: Any,
        profile: Any,
        conn: Any,
        staged_inputs: dict[str, Any],
        run_uuid: str | None = None,
    ) -> dict[str, Any]:
        return {}

    def remote_work_dir_path(
        self,
        *,
        request: Any,
        params: Any,
        remote_paths: dict[str, str],
    ) -> str:
        return str(remote_paths["remote_work"])

    @staticmethod
    def _is_openchromatin_runtime_library_input(bind_path: str) -> bool:
        cleaned = str(bind_path or "").strip()
        return any(cleaned in candidate_group for candidate_group in _OPENCHROMATIN_RUNTIME_LIBRARY_CANDIDATE_GROUPS)

    async def _resolve_openchromatin_runtime_library_paths(self, *, conn: Any) -> list[str] | None:
        resolved_paths: list[str] = []

        for candidate_group in _OPENCHROMATIN_RUNTIME_LIBRARY_CANDIDATE_GROUPS:
            resolved_candidate = None
            for candidate in candidate_group:
                if await conn.path_exists(candidate):
                    resolved_candidate = candidate
                    break
            if not resolved_candidate:
                return None
            resolved_paths.append(resolved_candidate)

        return resolved_paths

    @staticmethod
    def _build_openchromatin_wrapper_runtime_exports(
        *,
        runtime_exports: list[str],
        remote_work: str,
    ) -> list[str]:
        wrapper_dir = f"{str(remote_work).rstrip('/')}/{_OPENCHROMATIN_WRAPPER_DIRNAME}"
        resolved_exports = [str(command or "").strip() for command in runtime_exports if str(command or "").strip()]
        resolved_exports.append(f"export PATH={wrapper_dir}:${{PATH}}")
        return resolved_exports

    @staticmethod
    def _openchromatin_wrapper_script_content() -> str:
        return (
            "#!/bin/bash\n"
            "set -e\n"
            "wrapper_dir=\"$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\"\n"
            "clean_path=\"${PATH#${wrapper_dir}:}\"\n"
            "real_modkit=\"$(PATH=\"$clean_path\" command -v modkit || true)\"\n"
            "if [[ -z \"$real_modkit\" ]]; then\n"
            "  echo 'Failed to locate the real modkit binary behind the AGOUTIC OpenChromatin wrapper.' >&2\n"
            "  exit 127\n"
            "fi\n"
            "if [[ \"${1:-}\" == \"open-chromatin\" && \"${2:-}\" == \"predict\" ]]; then\n"
            "  has_device=0\n"
            "  for arg in \"$@\"; do\n"
            "    if [[ \"$arg\" == \"--device\" || \"$arg\" == --device=* ]]; then\n"
            "      has_device=1\n"
            "      break\n"
            "    fi\n"
            "  done\n"
            "  if [[ $has_device -eq 0 ]]; then\n"
            "    exec \"$real_modkit\" open-chromatin predict --device 0 \"${@:3}\"\n"
            "  fi\n"
            "fi\n"
            "exec \"$real_modkit\" \"$@\"\n"
        )

    async def _stage_openchromatin_wrapper(self, *, conn: Any, remote_work: str) -> str:
        wrapper_dir = f"{str(remote_work).rstrip('/')}/{_OPENCHROMATIN_WRAPPER_DIRNAME}"
        wrapper_path = f"{wrapper_dir}/modkit"
        await conn.mkdir_p(wrapper_dir)
        await conn.run(
            f"cat > {shlex.quote(wrapper_path)} << 'AGOUTIC_EOF'\n"
            f"{self._openchromatin_wrapper_script_content()}"
            "AGOUTIC_EOF",
            check=True,
        )
        await conn.run(f"chmod 755 {shlex.quote(wrapper_path)}", check=True)
        return wrapper_dir

    async def _resolve_custom_dogme_bind_paths(
        self,
        *,
        conn: Any,
        extra_bind_paths: list[str] | None,
    ) -> list[str]:
        resolved_paths: list[str] = []

        for extra_bind_path in extra_bind_paths or []:
            cleaned = str(extra_bind_path or "").strip()
            if not cleaned:
                continue

            is_runtime_library_input = self._is_openchromatin_runtime_library_input(cleaned)
            if cleaned == "/lib64" or is_runtime_library_input:
                runtime_library_paths = await self._resolve_openchromatin_runtime_library_paths(conn=conn)
                if runtime_library_paths:
                    for runtime_library_path in runtime_library_paths:
                        if runtime_library_path not in resolved_paths:
                            resolved_paths.append(runtime_library_path)
                    continue

                if is_runtime_library_input:
                    for candidate_group in _OPENCHROMATIN_RUNTIME_LIBRARY_CANDIDATE_GROUPS:
                        if cleaned not in candidate_group:
                            continue
                        for candidate in candidate_group:
                            if await conn.path_exists(candidate):
                                cleaned = candidate
                                break
                        break

            if not is_runtime_library_input:
                for candidate_group in _OPENCHROMATIN_RUNTIME_LIBRARY_CANDIDATE_GROUPS:
                    if cleaned not in candidate_group:
                        continue
                    for candidate in candidate_group:
                        if await conn.path_exists(candidate):
                            cleaned = candidate
                            break
                    break

            if not await conn.path_exists(cleaned):
                raise FileNotFoundError(
                    f"Custom Dogme bind path does not exist on the remote host: {cleaned}"
                )
            if cleaned not in resolved_paths:
                resolved_paths.append(cleaned)

        return resolved_paths

    async def remote_config_artifacts(
        self,
        *,
        request: Any,
        params: Any,
        profile: Any,
        conn: Any,
        remote_work: str,
        staged_inputs: dict[str, Any],
        reference_assets: dict[str, Any],
    ) -> dict[str, str]:
        cpu_account = (params.slurm_account or profile.default_slurm_account or "default").strip() or "default"
        cpu_partition = (params.slurm_partition or profile.default_slurm_partition or "standard").strip() or "standard"
        gpu_account = (params.slurm_gpu_account or profile.default_slurm_gpu_account or cpu_account).strip() or cpu_account
        gpu_partition = (params.slurm_gpu_partition or profile.default_slurm_gpu_partition or cpu_partition).strip() or cpu_partition

        ref_overrides: dict[str, dict[str, str]] = {}
        remote_reference_paths = {
            str(key).strip().lower(): str(value)
            for key, value in (staged_inputs.get("remote_reference_paths") or {}).items()
            if key and value
        }

        if not remote_reference_paths and params.reference_cache_path and params.reference_genome:
            first_ref = _normalize_reference_id((params.reference_genome or ["default"])[0])
            remote_reference_paths[first_ref] = params.reference_cache_path

        if not remote_reference_paths and params.reference_genome:
            derived_ref_root = _derive_remote_roots(params, profile)["ref_root"]
            for genome_name in params.reference_genome or []:
                ref_id = _normalize_reference_id(genome_name)
                remote_reference_paths[ref_id] = str(PurePosixPath(derived_ref_root) / ref_id)

        lower_map = {key.lower(): key for key in REFERENCE_GENOMES.keys()}
        for genome_name in params.reference_genome or []:
            ref_id = _normalize_reference_id(genome_name)
            remote_ref_root = remote_reference_paths.get(ref_id)
            if not remote_ref_root:
                continue

            canonical_name = lower_map.get(str(genome_name).lower(), genome_name)
            ref_cfg = REFERENCE_GENOMES.get(canonical_name, REFERENCE_GENOMES.get("mm39", {}))
            fasta_src = ref_cfg.get("fasta")
            gtf_src = ref_cfg.get("gtf")
            if not fasta_src or not gtf_src:
                continue

            ref_overrides[str(genome_name)] = {
                "fasta": str(PurePosixPath(remote_ref_root) / Path(fasta_src).name),
                "gtf": str(PurePosixPath(remote_ref_root) / Path(gtf_src).name),
            }
            kallisto_src = ref_cfg.get("kallisto_index")
            t2g_src = ref_cfg.get("kallisto_t2g")
            if kallisto_src:
                ref_overrides[str(genome_name)]["kallisto_index"] = str(
                    PurePosixPath(remote_ref_root) / Path(kallisto_src).name
                )
            if t2g_src:
                ref_overrides[str(genome_name)]["kallisto_t2g"] = str(
                    PurePosixPath(remote_ref_root) / Path(t2g_src).name
                )

        remote_roots = _derive_remote_roots(params, profile)

        bind_paths: list[str] = [remote_work]
        modkit_bind_paths: list[str] = list(bind_paths)
        remote_input = str(staged_inputs.get("remote_input") or "").strip()
        if remote_input:
            bind_paths.append(remote_input)
            modkit_bind_paths.append(remote_input)
        for remote_ref_root in remote_reference_paths.values():
            cleaned = str(remote_ref_root or "").strip()
            if cleaned:
                bind_paths.append(cleaned)
                modkit_bind_paths.append(cleaned)
        if str(params.mode or "").strip().upper() == DogmeMode.DNA.value:
            modkit_bind_paths.extend(
                await self._resolve_custom_dogme_bind_paths(
                    conn=conn,
                    extra_bind_paths=params.custom_dogme_bind_paths,
                )
            )

        task_runtime_exports = resolve_dogme_profile_task_runtime_exports(
            params.mode,
            custom_profile=params.custom_dogme_profile,
        )
        if str(params.mode or "").strip().upper() == DogmeMode.DNA.value:
            await self._stage_openchromatin_wrapper(conn=conn, remote_work=remote_work)
            task_runtime_exports = self._build_openchromatin_wrapper_runtime_exports(
                runtime_exports=task_runtime_exports,
                remote_work=remote_work,
            )

        config = NextflowConfig.generate_config(
            sample_name=params.sample_name,
            mode=params.mode,
            input_dir=params.input_directory,
            reference_genome=params.reference_genome,
            reference_overrides=ref_overrides,
            modifications=params.modifications,
            modkit_filter_threshold=params.modkit_filter_threshold,
            min_cov=params.min_cov,
            per_mod=params.per_mod,
            accuracy=params.accuracy,
            max_gpu_tasks=params.max_gpu_tasks,
            execution_mode="slurm",
            slurm_cpu_partition=cpu_partition,
            slurm_gpu_partition=gpu_partition,
            slurm_cpu_account=cpu_account,
            slurm_gpu_account=gpu_account,
            slurm_cpus=params.slurm_cpus,
            slurm_memory_gb=params.slurm_memory_gb,
            slurm_walltime=params.slurm_walltime,
            slurm_bind_paths=bind_paths,
            modkit_task_runtime_exports=task_runtime_exports,
            slurm_modkit_bind_paths=modkit_bind_paths,
            apptainer_cache_dir=f"{remote_roots['remote_base_path']}/.nxf-apptainer-cache",
        )
        profile_content = resolve_dogme_profile_content(
            params.mode,
            custom_profile=params.custom_dogme_profile,
        )
        return {
            "nextflow.config": config,
            "dogme.profile": profile_content,
        }

    def remote_build_command(
        self,
        *,
        request: Any,
        params: Any,
        remote_work: str,
        remote_output: str,
        staged_inputs: dict[str, Any],
        reference_assets: dict[str, Any],
        rendered_files: dict[str, str],
        rerun_in_place: bool = False,
    ) -> str:
        genome_list = ",".join(params.reference_genome)
        config_path = rendered_files.get("nextflow.config")
        if not config_path:
            raise ValueError("Dogme remote execution requires a rendered nextflow.config artifact")
        remote_input = str(staged_inputs.get("workflow_remote_input") or staged_inputs.get("remote_input") or params.data_cache_path or "").strip()
        cmd_parts = [
            '"${AGOUTIC_NEXTFLOW_BIN:-nextflow}" run mortazavilab/dogme',
            f"--sample_name {shlex.quote(params.sample_name)}",
            f"--mode {shlex.quote(params.mode or '')}",
            f"--input {shlex.quote(remote_input)}",
            f"--outdir {shlex.quote(remote_output)}",
            f"--reference_genome {shlex.quote(genome_list)}",
            f"-c {shlex.quote(config_path)}",
        ]
        if params.modifications:
            cmd_parts.append(f"--modifications {shlex.quote(params.modifications)}")
        if params.entry_point:
            cmd_parts.append(f"-entry {shlex.quote(params.entry_point)}")
        if rerun_in_place:
            cmd_parts.append("-resume")
        return " \\\n+    ".join(cmd_parts)

    def remote_result_sync_spec(
        self,
        *,
        request: Any,
        params: Any,
        staged_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        return {"workflow_key": self.workflow_key}

    def remote_summary_contract(
        self,
        *,
        request: Any,
        params: Any,
        staged_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        return {"workflow_key": self.workflow_key}

    def build_local_submit_kwargs(
        self,
        *,
        run_uuid: str,
        request: Any,
        workflow_index: int | None,
        max_gpu_tasks: int | None,
    ) -> dict[str, Any]:
        self.validate_submission(mode=getattr(request, "mode", None))
        return {
            "run_uuid": run_uuid,
            "workflow_key": self.workflow_key,
            "sample_name": request.sample_name,
            "mode": request.mode,
            "input_type": request.input_type,
            "input_dir": request.input_directory,
            "reference_genome": request.reference_genome,
            "modifications": request.modifications,
            "entry_point": request.entry_point,
            "modkit_filter_threshold": request.modkit_filter_threshold,
            "min_cov": request.min_cov,
            "per_mod": request.per_mod,
            "accuracy": request.accuracy,
            "max_gpu_tasks": max_gpu_tasks,
            "local_max_task_cpus": request.local_max_task_cpus,
            "local_max_task_memory_gb": request.local_max_task_memory_gb,
            "custom_dogme_profile": request.custom_dogme_profile,
            "workflow_index": workflow_index,
            "user_id": request.user_id,
            "project_id": request.project_id,
            "username": request.username,
            "project_slug": request.project_slug,
            "resume_from_dir": request.resume_from_dir,
        }

    def build_backend_submit_params(
        self,
        *,
        request: Any,
        workflow_number: int | None,
        max_gpu_tasks: int | None,
    ) -> dict[str, Any]:
        self.validate_submission(mode=getattr(request, "mode", None))
        return {
            "workflow_executor": self,
            "project_id": request.project_id,
            "user_id": request.user_id,
            "username": request.username,
            "project_slug": request.project_slug,
            "workflow_key": self.workflow_key,
            "sample_name": request.sample_name,
            "mode": request.mode,
            "input_type": request.input_type,
            "input_directory": request.input_directory,
            "reference_genome": request.reference_genome,
            "modifications": request.modifications,
            "entry_point": request.entry_point,
            "modkit_filter_threshold": request.modkit_filter_threshold,
            "min_cov": request.min_cov,
            "per_mod": request.per_mod,
            "accuracy": request.accuracy,
            "max_gpu_tasks": max_gpu_tasks,
            "local_max_task_cpus": request.local_max_task_cpus,
            "local_max_task_memory_gb": request.local_max_task_memory_gb,
            "custom_dogme_profile": request.custom_dogme_profile,
            "custom_dogme_bind_paths": request.custom_dogme_bind_paths,
            "resume_from_dir": request.resume_from_dir,
            "parent_block_id": request.parent_block_id,
            "ssh_profile_id": request.ssh_profile_id,
            "slurm_account": request.slurm_account,
            "slurm_partition": request.slurm_partition,
            "slurm_gpu_account": request.slurm_gpu_account,
            "slurm_gpu_partition": request.slurm_gpu_partition,
            "slurm_cpus": request.slurm_cpus,
            "slurm_memory_gb": request.slurm_memory_gb,
            "slurm_walltime": request.slurm_walltime,
            "slurm_gpus": request.slurm_gpus,
            "slurm_gpu_type": request.slurm_gpu_type,
            "remote_base_path": request.remote_base_path,
            "remote_input_path": request.remote_input_path,
            "workflow_number": workflow_number,
            "staged_remote_input_path": request.staged_remote_input_path,
            "cache_preflight": request.cache_preflight,
            "result_destination": request.result_destination or "local",
        }

    def build_preview(self, **kwargs: Any) -> WorkflowPreviewResult:
        sample_name = sample_name_or_default(
            kwargs.get("sample_name"),
            fallback_path=kwargs.get("input_directory"),
        )
        mode = str(kwargs.get("mode") or "DNA").strip().upper() or "DNA"
        input_type = str(kwargs.get("input_type") or "pod5").strip().lower() or "pod5"
        input_directory = ensure_path(kwargs.get("input_directory"))
        command_joiner = " \\\n+" + "    "
        command = command_joiner.join(
            shlex.quote(part)
            for part in [
                "nextflow",
                "run",
                str(DOGME_REPO),
                f"--sample={sample_name}",
                f"--readType={mode}",
                f"--inputType={input_type}",
                f"--inputDir={input_directory or '<input_directory>'}",
            ]
        )
        preview_payload = {
            "workflow_key": self.workflow_key,
            "sample_name": sample_name,
            "mode": mode,
            "input_type": input_type,
            "input_directory": input_directory,
            "command": command,
        }
        preview_markdown = (
            "### Dogme Preview\n\n"
            f"- Sample: `{sample_name}`\n"
            f"- Mode: `{mode}`\n"
            f"- Input type: `{input_type}`\n"
            f"- Input directory: `{input_directory or '<input_directory>'}`\n\n"
            "```bash\n"
            f"{command}\n"
            "```\n"
        )
        return WorkflowPreviewResult(
            workflow_key=self.workflow_key,
            supports_submission=self.supports_submission,
            command=command,
            preview_markdown=preview_markdown,
            preview_payload=preview_payload,
        )

    def validate_inputs(self, *, request: Any) -> dict[str, Any]:
        raise NotImplementedError("Dogme local execution still uses the legacy submit path")

    def stage_inputs(
        self,
        *,
        request: Any,
        work_dir,
        validated_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError("Dogme local execution still uses the legacy submit path")

    def render_nextflow_config(
        self,
        *,
        request: Any,
        work_dir,
        staged_inputs: dict[str, Any],
        validated_inputs: dict[str, Any],
    ) -> dict[str, str]:
        raise NotImplementedError("Dogme local execution still uses the legacy submit path")

    def build_command(
        self,
        *,
        request: Any,
        work_dir,
        staged_inputs,
        rendered_files,
        validated_inputs: dict[str, Any],
    ) -> list[str]:
        raise NotImplementedError("Dogme local execution still uses the legacy submit path")

    def result_sync_spec(self, *, request: Any, validated_inputs: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Dogme local execution still uses the legacy submit path")

    def summary_contract(self, *, request: Any, validated_inputs: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Dogme local execution still uses the legacy submit path")