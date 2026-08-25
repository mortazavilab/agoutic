"""wf-pore-c preview-only workflow executor for Phase 1."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import shlex
from pathlib import Path, PurePosixPath
from typing import Any

from launchpad import config as launchpad_config
from launchpad.workflow_executors.base import (
    WorkflowExecutor,
    WorkflowPreviewResult,
    ensure_path,
    sample_name_or_default,
)


_DEFAULT_WORKFLOW_REPO = "epi2me-labs/wf-pore-c"
_DEFAULT_WORKFLOW_VERSION = "v1.3.1"
_DEFAULT_REPORT_FILENAME = "wf-pore-c-report.html"
_SUPPORTED_INPUT_TYPES = {"bam", "fastq"}
_REMOTE_ARTIFACT_DIR = ".agoutic/wf-pore-c"
_REMOTE_STAGED_INPUTS_DIR = f"{_REMOTE_ARTIFACT_DIR}/staged-inputs"


def _wf_pore_c_flag_message() -> str:
    return "workflow_key 'wf_pore_c' requires WF_PORE_C_ENABLED=true for local submission"


def _workflow_repo(value: str | None) -> str:
    return str(value or _DEFAULT_WORKFLOW_REPO).strip() or _DEFAULT_WORKFLOW_REPO


def _workflow_version(value: str | None) -> str:
    return str(value or _DEFAULT_WORKFLOW_VERSION).strip() or _DEFAULT_WORKFLOW_VERSION


def _report_filename(value: str | None) -> str:
    return str(value or _DEFAULT_REPORT_FILENAME).strip() or _DEFAULT_REPORT_FILENAME


def _artifact_root(work_dir: Path) -> Path:
    return work_dir / ".agoutic" / "wf-pore-c"


def _staged_inputs_root(work_dir: Path) -> Path:
    return _artifact_root(work_dir) / "staged-inputs"


def _nextflow_work_dir(work_dir: Path) -> Path:
    return work_dir.parent / ".nextflow-work" / "wf-pore-c" / work_dir.name


def _remote_artifact_root(remote_work: str) -> str:
    return str(PurePosixPath(remote_work) / ".agoutic" / "wf-pore-c")


def _remote_support_dir(remote_work: str, category: str) -> str:
    return str(PurePosixPath(_remote_artifact_root(remote_work)) / "staged-inputs" / category)


def _remote_nextflow_work_dir(remote_paths: dict[str, str]) -> str:
    remote_work = PurePosixPath(remote_paths["remote_work"])
    project_root = PurePosixPath(remote_paths["project_root"])
    return str(project_root / ".nextflow-work" / "wf-pore-c" / remote_work.name)


def _file_fingerprint(local_path: str) -> str:
    candidate = Path(local_path).expanduser()
    stat = candidate.stat()
    hasher = hashlib.sha256()
    hasher.update(candidate.name.encode("utf-8"))
    hasher.update(str(stat.st_size).encode("utf-8"))
    hasher.update(str(stat.st_mtime_ns).encode("utf-8"))
    return hasher.hexdigest()


def _reference_sidecar_specs(reference_fasta: str) -> list[tuple[str, bool]]:
    specs = [(f"{reference_fasta}.fai", True)]
    if str(reference_fasta).endswith(".gz"):
        specs.append((f"{reference_fasta}.gzi", True))
    return specs


def _replace_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _stage_path(source: str, destination: Path) -> str:
    source_path = Path(source).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        _replace_path(destination)

    try:
        os.symlink(str(source_path), str(destination), target_is_directory=source_path.is_dir())
    except OSError:
        if source_path.is_dir():
            shutil.copytree(source_path, destination)
        else:
            shutil.copy2(source_path, destination)

    return str(destination)


def _output_flags(raw_flags: dict[str, Any] | None) -> dict[str, bool]:
    flags = {
        "pairs": bool((raw_flags or {}).get("pairs", True)),
        "mcool": bool((raw_flags or {}).get("mcool", True)),
        "hi_c": bool((raw_flags or {}).get("hi_c", False)),
        "bed": bool((raw_flags or {}).get("bed", False)),
        "chromunity": bool((raw_flags or {}).get("chromunity", False)),
        "coverage": bool((raw_flags or {}).get("coverage", False)),
        "paired_end": bool((raw_flags or {}).get("paired_end", False)),
    }
    if flags["bed"]:
        flags["paired_end"] = True
    return flags


def _preview_work_dir(output_directory: str, input_path: str, sample_name: str) -> str:
    if output_directory:
        output_path = Path(output_directory).expanduser()
        return str(output_path.parent / ".nextflow-work" / "wf-pore-c" / output_path.name)

    normalized_input = str(input_path or "").strip()
    if normalized_input:
        input_path_obj = Path(normalized_input).expanduser()
        parent = input_path_obj if input_path_obj.is_dir() else input_path_obj.parent
        return str(parent / ".nextflow-work" / "wf-pore-c" / sample_name)

    return str(Path(".").resolve() / ".nextflow-work" / "wf-pore-c" / sample_name)


class WfPoreCWorkflowExecutor(WorkflowExecutor):
    workflow_key = "wf_pore_c"

    @property
    def supports_submission(self) -> bool:
        return bool(launchpad_config.WF_PORE_C_ENABLED)

    def validate_submission(self, *, mode: str | None) -> None:
        if not self.supports_submission:
            raise ValueError(_wf_pore_c_flag_message())
        if mode is not None:
            raise ValueError("workflow_key 'wf_pore_c' requires mode to be omitted or null")

    def remote_validate_submission(self, *, request: Any) -> None:
        self.validate_submission(mode=getattr(request, "mode", None))
        input_type = str(getattr(request, "input_type", "") or "").strip().lower()
        input_directory = ensure_path(getattr(request, "input_directory", None))
        remote_input_path = ensure_path(getattr(request, "remote_input_path", None))
        staged_remote_input_path = ensure_path(getattr(request, "staged_remote_input_path", None))
        reference_fasta = ensure_path(getattr(request, "reference_fasta", None))
        vcf = ensure_path(getattr(request, "vcf", None))
        sample_sheet = ensure_path(getattr(request, "sample_sheet", None))

        if input_type not in _SUPPORTED_INPUT_TYPES:
            raise ValueError("workflow_key 'wf_pore_c' requires input_type 'bam' or 'fastq'")
        if not input_directory and not remote_input_path and not staged_remote_input_path:
            raise ValueError(
                "workflow_key 'wf_pore_c' requires input_directory unless remote_input_path or staged_remote_input_path is provided"
            )
        if not reference_fasta:
            raise ValueError("workflow_key 'wf_pore_c' requires reference_fasta for SLURM submission")

        for required_local_path, label in ((reference_fasta, "reference_fasta"), (vcf, "VCF"), (sample_sheet, "sample_sheet")):
            if not required_local_path:
                continue
            if not Path(required_local_path).expanduser().exists():
                raise ValueError(f"wf-pore-c {label} not found: {required_local_path}")

        if input_directory and not remote_input_path and not staged_remote_input_path:
            input_candidate = Path(input_directory).expanduser()
            if not input_candidate.exists():
                raise ValueError(f"wf-pore-c input path not found: {input_directory}")

    @staticmethod
    async def _stage_cached_file(
        *,
        local_path: str,
        profile: Any,
        conn: Any,
        remote_dir: str,
        remote_filename: str,
        transfer_id: str | None = None,
    ) -> tuple[str, str]:
        from launchpad.backends.file_transfer import FileTransferManager

        remote_path = str(PurePosixPath(remote_dir) / remote_filename)
        if await conn.path_exists(remote_path):
            return remote_path, "reused"

        await conn.mkdir_p(remote_dir)
        transfer_manager = FileTransferManager()
        upload_kwargs = {
            "profile": profile,
            "local_path": local_path,
            "remote_path": remote_path,
        }
        if transfer_id:
            upload_kwargs["transfer_id"] = transfer_id
        result = await transfer_manager.upload_inputs(**upload_kwargs)
        if not result["ok"]:
            raise RuntimeError(f"wf-pore-c remote stage failed for {local_path}: {result['message']}")
        return remote_path, "staged"

    async def _link_remote_input(self, *, conn: Any, params: Any, remote_input: str) -> str | None:
        remote_work = ensure_path(getattr(params, "remote_work_dir", None))
        if not remote_work:
            return None

        link_root = _remote_support_dir(remote_work, "input")
        target_name = PurePosixPath(remote_input).name or f"input.{params.input_type}"
        link_path = str(PurePosixPath(link_root) / target_name)
        await conn.mkdir_p(link_root)
        await conn.run(f"rm -rf {shlex.quote(link_path)}", check=True)
        await conn.run(f"ln -sfn {shlex.quote(remote_input)} {shlex.quote(link_path)}", check=True)
        return link_path

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
        self.remote_validate_submission(request=request)
        remote_base_path = (params.remote_base_path or profile.remote_base_path or "").strip()
        if not remote_base_path:
            raise ValueError("SLURM execution requires remote_base_path on the request or SSH profile")
        remote_roots = {
            "remote_base_path": str(PurePosixPath(remote_base_path)),
            "ref_root": str(PurePosixPath(remote_base_path) / "ref"),
            "data_root": str(PurePosixPath(remote_base_path) / "data"),
        }

        staged: dict[str, Any] = {}
        remote_input = ensure_path(getattr(params, "data_cache_path", None)) or ensure_path(getattr(params, "staged_remote_input_path", None))
        if remote_input:
            workflow_remote_input = await self._link_remote_input(conn=conn, params=params, remote_input=remote_input)
            if workflow_remote_input:
                staged["workflow_remote_input"] = workflow_remote_input

        reference_fasta = ensure_path(getattr(params, "reference_fasta", None))
        if reference_fasta:
            reference_hash = _file_fingerprint(reference_fasta)[:16]
            reference_dir = str(PurePosixPath(remote_roots["ref_root"]) / "wf-pore-c" / reference_hash)
            reference_remote_path, reference_status = await self._stage_cached_file(
                local_path=reference_fasta,
                profile=profile,
                conn=conn,
                remote_dir=reference_dir,
                remote_filename=Path(reference_fasta).name,
                transfer_id=transfer_id,
            )
            staged["reference_fasta_remote_path"] = reference_remote_path
            staged["reference_fasta_cache_status"] = reference_status

        vcf = ensure_path(getattr(params, "vcf", None))
        if vcf:
            vcf_hash = _file_fingerprint(vcf)[:16]
            vcf_dir = str(PurePosixPath(remote_roots["data_root"]) / "wf-pore-c" / "vcf" / vcf_hash)
            vcf_remote_path, vcf_status = await self._stage_cached_file(
                local_path=vcf,
                profile=profile,
                conn=conn,
                remote_dir=vcf_dir,
                remote_filename=Path(vcf).name,
                transfer_id=transfer_id,
            )
            staged["vcf_remote_path"] = vcf_remote_path
            staged["vcf_cache_status"] = vcf_status

        sample_sheet = ensure_path(getattr(params, "sample_sheet", None))
        if sample_sheet:
            sheet_hash = _file_fingerprint(sample_sheet)[:16]
            sheet_dir = str(PurePosixPath(remote_roots["data_root"]) / "wf-pore-c" / "sample-sheet" / sheet_hash)
            sheet_remote_path, sheet_status = await self._stage_cached_file(
                local_path=sample_sheet,
                profile=profile,
                conn=conn,
                remote_dir=sheet_dir,
                remote_filename=Path(sample_sheet).name,
                transfer_id=transfer_id,
            )
            staged["sample_sheet_remote_path"] = sheet_remote_path
            staged["sample_sheet_cache_status"] = sheet_status

        return staged

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
        self.remote_validate_submission(request=request)
        reference_fasta = ensure_path(getattr(params, "reference_fasta", None))
        remote_reference_fasta = str(staged_inputs.get("reference_fasta_remote_path") or "").strip()
        if not reference_fasta or not remote_reference_fasta:
            raise ValueError("wf-pore-c remote reference staging requires a staged reference_fasta")

        evidence = {
            "remote_path": remote_reference_fasta,
            "required_sidecars": {},
            "present_sidecars": {},
            "missing_required_sidecars": [],
        }
        for local_sidecar, required in _reference_sidecar_specs(reference_fasta):
            remote_sidecar = f"{remote_reference_fasta}{Path(local_sidecar).suffix}"
            sidecar_label = Path(local_sidecar).name.replace(Path(reference_fasta).name, "").lstrip(".") or Path(local_sidecar).name
            evidence["required_sidecars"][sidecar_label] = remote_sidecar
            if await conn.path_exists(remote_sidecar):
                evidence["present_sidecars"][sidecar_label] = remote_sidecar
                continue
            if not Path(local_sidecar).expanduser().exists():
                if required:
                    evidence["missing_required_sidecars"].append(sidecar_label)
                continue
            sidecar_path, _ = await self._stage_cached_file(
                local_path=local_sidecar,
                profile=profile,
                conn=conn,
                remote_dir=str(PurePosixPath(remote_reference_fasta).parent),
                remote_filename=Path(local_sidecar).name,
            )
            evidence["present_sidecars"][sidecar_label] = sidecar_path

        if evidence["missing_required_sidecars"]:
            missing_text = ", ".join(evidence["missing_required_sidecars"])
            raise RuntimeError(f"wf-pore-c reference FASTA sidecars are missing: {missing_text}")

        return {"wf_pore_c_reference_fasta": evidence}

    def remote_work_dir_path(
        self,
        *,
        request: Any,
        params: Any,
        remote_paths: dict[str, str],
    ) -> str:
        self.remote_validate_submission(request=request)
        return _remote_nextflow_work_dir(remote_paths)

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
        self.remote_validate_submission(request=request)
        payload = {
            "workflow_key": self.workflow_key,
            "workflow_repo": _workflow_repo(getattr(params, "workflow_repo", None)),
            "workflow_version": _workflow_version(getattr(params, "workflow_version", None)),
            "report_filename": _report_filename(getattr(params, "report_filename", None)),
            "output_flags": _output_flags(getattr(params, "output_flags", None)),
            "input_type": getattr(params, "input_type", None),
            "remote_input": staged_inputs.get("workflow_remote_input") or staged_inputs.get("remote_input"),
            "reference_fasta": staged_inputs.get("reference_fasta_remote_path"),
            "vcf": staged_inputs.get("vcf_remote_path"),
            "sample_sheet": staged_inputs.get("sample_sheet_remote_path"),
            "remote_output": getattr(params, "remote_output_dir", None),
            "remote_work_dir": staged_inputs.get("remote_nextflow_work_dir") or getattr(params, "remote_nextflow_work_dir", None),
            "reference_assets": reference_assets,
        }
        return {
            ".agoutic/wf-pore-c/remote-submit-config.json": json.dumps(payload, indent=2, sort_keys=True) + "\n"
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
        self.remote_validate_submission(request=request)
        remote_input = str(staged_inputs.get("workflow_remote_input") or staged_inputs.get("remote_input") or "").strip()
        reference_fasta = str(staged_inputs.get("reference_fasta_remote_path") or "").strip()
        if not remote_input:
            raise ValueError("wf-pore-c remote execution requires a staged remote input path")
        if not reference_fasta:
            raise ValueError("wf-pore-c remote execution requires a staged remote reference_fasta")

        command_parts: list[str] = [
            '"${AGOUTIC_NEXTFLOW_BIN:-nextflow}"',
            "run",
            _workflow_repo(getattr(params, "workflow_repo", None)),
            "-r",
            _workflow_version(getattr(params, "workflow_version", None)),
            f"--{str(getattr(params, 'input_type', '') or '').strip().lower()}",
            remote_input,
            "--ref",
            reference_fasta,
            "--out_dir",
            remote_output,
            "-work-dir",
            str(staged_inputs.get("remote_nextflow_work_dir") or getattr(params, "remote_nextflow_work_dir", None) or remote_work),
        ]
        sample_sheet = str(staged_inputs.get("sample_sheet_remote_path") or "").strip()
        if getattr(params, "sample_name", None) and not sample_sheet:
            command_parts.extend(["--sample", str(params.sample_name)])
        if sample_sheet:
            command_parts.extend(["--sample_sheet", sample_sheet])
        vcf = str(staged_inputs.get("vcf_remote_path") or "").strip()
        if vcf:
            command_parts.extend(["--vcf", vcf])
        cutter = str(getattr(params, "cutter", None) or "NlaIII").strip() or "NlaIII"
        if cutter:
            command_parts.extend(["--cutter", cutter])
        flags = _output_flags(getattr(params, "output_flags", None))
        for flag_name in ("pairs", "mcool", "hi_c", "bed", "chromunity", "coverage", "paired_end"):
            if flags[flag_name]:
                command_parts.append(f"--{flag_name}")
        command_parts.extend(["-profile", "standard"])
        if rerun_in_place:
            command_parts.append("-resume")
        return " \\\n+    ".join(shlex.quote(part) for part in command_parts)

    def remote_result_sync_spec(
        self,
        *,
        request: Any,
        params: Any,
        staged_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        self.remote_validate_submission(request=request)
        return self.result_sync_spec(
            request=request,
            validated_inputs={
                "report_filename": _report_filename(getattr(params, "report_filename", None)),
                "output_flags": _output_flags(getattr(params, "output_flags", None)),
            },
        )

    def remote_summary_contract(
        self,
        *,
        request: Any,
        params: Any,
        staged_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        self.remote_validate_submission(request=request)
        return {
            "workflow_key": self.workflow_key,
            "workflow_version": _workflow_version(getattr(params, "workflow_version", None)),
            "report_filename": _report_filename(getattr(params, "report_filename", None)),
            "output_flags": _output_flags(getattr(params, "output_flags", None)),
            "sample_name": sample_name_or_default(getattr(params, "sample_name", None), fallback_path=getattr(params, "input_directory", None)),
        }

    def validate_inputs(self, *, request: Any) -> dict[str, Any]:
        self.validate_submission(mode=getattr(request, "mode", None))
        input_path = ensure_path(getattr(request, "input_directory", None))
        input_type = str(getattr(request, "input_type", "") or "").strip().lower()
        reference_fasta = ensure_path(getattr(request, "reference_fasta", None))
        vcf = ensure_path(getattr(request, "vcf", None))
        sample_sheet = ensure_path(getattr(request, "sample_sheet", None))
        sample_name = sample_name_or_default(getattr(request, "sample_name", None), fallback_path=input_path)

        if input_type not in _SUPPORTED_INPUT_TYPES:
            raise ValueError("workflow_key 'wf_pore_c' requires input_type 'bam' or 'fastq'")
        if not input_path:
            raise ValueError("workflow_key 'wf_pore_c' requires input_directory")
        if not reference_fasta:
            raise ValueError("workflow_key 'wf_pore_c' requires reference_fasta")

        input_candidate = Path(input_path).expanduser()
        reference_candidate = Path(reference_fasta).expanduser()
        if not input_candidate.exists():
            raise ValueError(f"wf-pore-c input path not found: {input_path}")
        if not reference_candidate.exists():
            raise ValueError(f"wf-pore-c reference_fasta not found: {reference_fasta}")
        if vcf and not Path(vcf).expanduser().exists():
            raise ValueError(f"wf-pore-c VCF not found: {vcf}")
        if sample_sheet and not Path(sample_sheet).expanduser().exists():
            raise ValueError(f"wf-pore-c sample_sheet not found: {sample_sheet}")

        return {
            "sample_name": sample_name,
            "input_type": input_type,
            "input_path": str(input_candidate),
            "reference_fasta": str(reference_candidate),
            "vcf": str(Path(vcf).expanduser()) if vcf else None,
            "sample_sheet": str(Path(sample_sheet).expanduser()) if sample_sheet else None,
            "cutter": str(getattr(request, "cutter", None) or "NlaIII").strip() or "NlaIII",
            "workflow_repo": _workflow_repo(getattr(request, "workflow_repo", None)),
            "workflow_version": _workflow_version(getattr(request, "workflow_version", None)),
            "output_flags": _output_flags(getattr(request, "output_flags", None)),
            "report_filename": _report_filename(getattr(request, "report_filename", None)),
        }

    def stage_inputs(
        self,
        *,
        request: Any,
        work_dir: Path,
        validated_inputs: dict[str, Any],
    ) -> dict[str, Any]:
        staged_inputs_root = _staged_inputs_root(work_dir)
        nextflow_work_dir = _nextflow_work_dir(work_dir)
        nextflow_work_dir.parent.mkdir(parents=True, exist_ok=True)

        staged_primary_input = _stage_path(
            validated_inputs["input_path"],
            staged_inputs_root / "input" / Path(validated_inputs["input_path"]).name,
        )
        staged_reference_fasta = _stage_path(
            validated_inputs["reference_fasta"],
            staged_inputs_root / "reference" / Path(validated_inputs["reference_fasta"]).name,
        )
        staged_vcf = None
        if validated_inputs["vcf"]:
            staged_vcf = _stage_path(
                validated_inputs["vcf"],
                staged_inputs_root / "vcf" / Path(validated_inputs["vcf"]).name,
            )
        staged_sample_sheet = None
        if validated_inputs["sample_sheet"]:
            staged_sample_sheet = _stage_path(
                validated_inputs["sample_sheet"],
                staged_inputs_root / "sample-sheet" / Path(validated_inputs["sample_sheet"]).name,
            )

        return {
            **validated_inputs,
            "input_path": staged_primary_input,
            "reference_fasta": staged_reference_fasta,
            "vcf": staged_vcf,
            "sample_sheet": staged_sample_sheet,
            "output_directory": str(work_dir),
            "nextflow_work_dir": str(nextflow_work_dir),
            "artifact_root": str(_artifact_root(work_dir)),
        }

    def render_nextflow_config(
        self,
        *,
        request: Any,
        work_dir: Path,
        staged_inputs: dict[str, Any],
        validated_inputs: dict[str, Any],
    ) -> dict[str, str]:
        rendered_payload = {
            "workflow_key": self.workflow_key,
            "workflow_repo": staged_inputs["workflow_repo"],
            "workflow_version": staged_inputs["workflow_version"],
            "report_filename": staged_inputs["report_filename"],
            "output_flags": staged_inputs["output_flags"],
            "sample_name": staged_inputs["sample_name"],
            "input_type": staged_inputs["input_type"],
        }
        return {
            ".agoutic/wf-pore-c/submit-config.json": json.dumps(rendered_payload, indent=2, sort_keys=True) + "\n"
        }

    def build_command(
        self,
        *,
        request: Any,
        work_dir: Path,
        staged_inputs: dict[str, Any],
        rendered_files: dict[str, Path],
        validated_inputs: dict[str, Any],
    ) -> list[str]:
        command_parts: list[str] = [
            "nextflow",
            "run",
            staged_inputs["workflow_repo"],
            "-r",
            staged_inputs["workflow_version"],
            f"--{staged_inputs['input_type']}",
            staged_inputs["input_path"],
            "--ref",
            staged_inputs["reference_fasta"],
            "--out_dir",
            staged_inputs["output_directory"],
            "-work-dir",
            staged_inputs["nextflow_work_dir"],
        ]
        if staged_inputs["sample_name"] and not staged_inputs["sample_sheet"]:
            command_parts.extend(["--sample", staged_inputs["sample_name"]])
        if staged_inputs["sample_sheet"]:
            command_parts.extend(["--sample_sheet", staged_inputs["sample_sheet"]])
        if staged_inputs["vcf"]:
            command_parts.extend(["--vcf", staged_inputs["vcf"]])
        if staged_inputs["cutter"]:
            command_parts.extend(["--cutter", staged_inputs["cutter"]])
        for flag_name in ("pairs", "mcool", "hi_c", "bed", "chromunity", "coverage", "paired_end"):
            if staged_inputs["output_flags"][flag_name]:
                command_parts.append(f"--{flag_name}")
        command_parts.extend(["-profile", "standard"])
        return command_parts

    def result_sync_spec(self, *, request: Any, validated_inputs: dict[str, Any]) -> dict[str, Any]:
        flags = validated_inputs["output_flags"]
        outputs = [validated_inputs["report_filename"]]
        if flags["pairs"]:
            outputs.append("pairs/{alias}.pairs.gz")
        if flags["mcool"]:
            outputs.append("cooler/{alias}.mcool")
        if flags["hi_c"]:
            outputs.append("hi-c/{alias}.hic")
        return {
            "workflow_key": self.workflow_key,
            "report_filename": validated_inputs["report_filename"],
            "expected_outputs": outputs,
        }

    def summary_contract(self, *, request: Any, validated_inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "workflow_key": self.workflow_key,
            "workflow_version": validated_inputs["workflow_version"],
            "report_filename": validated_inputs["report_filename"],
            "output_flags": dict(validated_inputs["output_flags"]),
            "sample_name": validated_inputs["sample_name"],
        }

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
            "workflow_executor": self,
            "request": request,
            "sample_name": request.sample_name,
            "mode": None,
            "input_type": request.input_type,
            "input_dir": request.input_directory,
            "reference_genome": request.reference_genome,
            "reference_fasta": request.reference_fasta,
            "vcf": request.vcf,
            "sample_sheet": request.sample_sheet,
            "cutter": request.cutter,
            "workflow_repo": request.workflow_repo,
            "workflow_version": request.workflow_version,
            "output_flags": request.output_flags,
            "workflow_index": workflow_index,
            "user_id": request.user_id,
            "project_id": request.project_id,
            "username": request.username,
            "project_slug": request.project_slug,
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
            "mode": None,
            "input_type": request.input_type,
            "input_directory": request.input_directory,
            "reference_fasta": request.reference_fasta,
            "vcf": request.vcf,
            "sample_sheet": request.sample_sheet,
            "cutter": request.cutter,
            "workflow_repo": request.workflow_repo,
            "workflow_version": request.workflow_version,
            "report_filename": getattr(request, "report_filename", None),
            "output_flags": dict(request.output_flags or {}),
            "reference_genome": request.reference_genome,
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
            fallback_path=kwargs.get("input_path"),
        )
        workflow_repo = _workflow_repo(kwargs.get("workflow_repo"))
        workflow_version = _workflow_version(kwargs.get("workflow_version"))
        input_path = ensure_path(kwargs.get("input_path") or kwargs.get("input_directory"))
        input_type = str(kwargs.get("input_type") or "").strip().lower() or (
            "bam" if input_path.lower().endswith(".bam") else "fastq"
        )
        reference_fasta = ensure_path(kwargs.get("reference_fasta"))
        vcf = ensure_path(kwargs.get("vcf"))
        sample_sheet = ensure_path(kwargs.get("sample_sheet"))
        cutter = str(kwargs.get("cutter") or "NlaIII").strip() or "NlaIII"
        output_directory = ensure_path(kwargs.get("output_directory"))
        report_filename = _report_filename(kwargs.get("report_filename"))
        flags = _output_flags(kwargs.get("output_flags") if isinstance(kwargs.get("output_flags"), dict) else None)
        work_dir = _preview_work_dir(output_directory, input_path, sample_name)

        command_parts: list[str] = [
            "nextflow",
            "run",
            workflow_repo,
            "-r",
            workflow_version,
            f"--{input_type}",
            input_path,
            "--ref",
            reference_fasta,
            "--out_dir",
            output_directory,
            "-work-dir",
            work_dir,
        ]
        if sample_name and not sample_sheet:
            command_parts.extend(["--sample", sample_name])
        if sample_sheet:
            command_parts.extend(["--sample_sheet", sample_sheet])
        if vcf:
            command_parts.extend(["--vcf", vcf])
        if cutter:
            command_parts.extend(["--cutter", cutter])
        for flag_name in ("pairs", "mcool", "hi_c", "bed", "chromunity", "coverage", "paired_end"):
            if flags[flag_name]:
                command_parts.append(f"--{flag_name}")
        command_parts.extend(["-profile", "standard"])

        expected_outputs = ["bams/{alias}.cs.bam", report_filename]
        if flags["pairs"]:
            expected_outputs.append("pairs/{alias}.pairs.gz")
        if flags["mcool"]:
            expected_outputs.append("cooler/{alias}.mcool")
        if flags["hi_c"]:
            expected_outputs.append("hi-c/{alias}.hic")
        if flags["chromunity"]:
            expected_outputs.append("chromunity/")
        if flags["coverage"]:
            expected_outputs.append("coverage/")

        command_joiner = " \\\n" + "    "
        command = command_joiner.join(shlex.quote(part) for part in command_parts)
        preview_payload = {
            "workflow_key": self.workflow_key,
            "workflow_repo": workflow_repo,
            "workflow_version": workflow_version,
            "sample_name": sample_name,
            "input_type": input_type,
            "input_path": input_path,
            "reference_fasta": reference_fasta,
            "vcf": vcf or None,
            "sample_sheet": sample_sheet or None,
            "cutter": cutter,
            "output_directory": output_directory,
            "work_dir": work_dir,
            "report_filename": report_filename,
            "output_flags": flags,
            "expected_outputs": expected_outputs,
            "command": command,
            "notes": [
                (
                    "Submission is enabled when WF_PORE_C_ENABLED=true."
                    if self.supports_submission
                    else "Submission is disabled until WF_PORE_C_ENABLED=true."
                ),
                "Keep -work-dir outside --out_dir to avoid Nextflow work/output collisions.",
                "Schema-declared user inputs are BAM or FASTQ plus reference FASTA, with optional VCF and sample sheet.",
                "Large BAM/FASTQ staging is planned as symlink-first with copy fallback only when required.",
            ],
        }
        enabled_outputs = [name for name, enabled in flags.items() if enabled]
        outputs_text = ", ".join(enabled_outputs) if enabled_outputs else "none"
        optional_lines = []
        if vcf:
            optional_lines.append(f"- VCF: `{vcf}`")
        if sample_sheet:
            optional_lines.append(f"- Sample sheet: `{sample_sheet}`")
        optional_text = "\n".join(optional_lines) if optional_lines else "- Optional inputs: none"
        expected_outputs_text = "\n".join(f"- `{item}`" for item in expected_outputs)
        notes_text = "\n".join(f"- {item}" for item in preview_payload["notes"])
        preview_markdown = (
            "### wf-pore-c Dry-Run Preview\n\n"
            "No workflow was submitted. This card shows the current Launchpad execution draft for wf-pore-c.\n\n"
            f"- Sample: `{sample_name}`\n"
            f"- Input type: `{input_type}`\n"
            f"- Input path: `{input_path}`\n"
            f"- Reference FASTA: `{reference_fasta}`\n"
            f"{optional_text}\n"
            f"- Cutter: `{cutter}`\n"
            f"- Output directory: `{output_directory}`\n"
            f"- Work directory: `{work_dir}`\n"
            f"- Enabled outputs: `{outputs_text}`\n"
            f"- Report filename: `{report_filename}`\n\n"
            "Expected outputs:\n"
            f"{expected_outputs_text}\n\n"
            "```bash\n"
            f"{command}\n"
            "```\n\n"
            "Notes:\n"
            f"{notes_text}\n"
        )
        return WorkflowPreviewResult(
            workflow_key=self.workflow_key,
            supports_submission=self.supports_submission,
            command=command,
            preview_markdown=preview_markdown,
            preview_payload=preview_payload,
        )