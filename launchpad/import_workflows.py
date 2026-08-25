from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from common.logging_config import get_logger
from launchpad import config as launchpad_config
from launchpad.config import REFERENCE_GENOMES


logger = get_logger(__name__)


_DOGME_RESULT_SYNC_DIRS = ("annot", "bams", "bedMethyl", "kallisto", "openChromatin", "stats")
_WF_PORE_C_RESULT_SYNC_DIRS = (
    "pairs",
    "cooler",
    "hi-c",
    "ingress_results",
    "paired_end",
    "paireds",
    "chromunity",
    "filtered_out",
)
_DOGME_RESULT_SYNC_FILE_PATTERNS = ("*.config", "*.html", "*.txt", "*.csv", "*.tsv")
_WF_PORE_C_RESULT_SYNC_FILE_PATTERNS = ("wf-pore-c-report.html", ".agoutic.workflow.json", "*.html", "*.txt", "*.csv", "*.tsv")
RESULT_SYNC_DIRS = _DOGME_RESULT_SYNC_DIRS
RESULT_SYNC_FILE_PATTERNS = _DOGME_RESULT_SYNC_FILE_PATTERNS

_CONFIG_ASSIGNMENT_RE = r"^\s*{name}\s*=\s*(['\"])(.*?)\1\s*$"
_GENOME_NAME_RE = re.compile(r"\[name:\s*'([^']+)'", re.IGNORECASE)
_SKIP_CONFIG_DIRS = {"work", ".rsync-partial", ".nextflow"}
_PARTIAL_MARKERS = (
    ".nextflow_running",
    ".nextflow_failed",
    ".nextflow_cancelled",
    ".launch_error",
    ".nextflow_error",
)


@dataclass(frozen=True)
class WorkflowImportMetadata:
    workflow_key: str
    sample_name: str | None
    mode: str | None
    reference_genome: list[str]
    modifications: str | None
    config_path: str | None
    source_complete: bool
    input_directory: str | None


def normalize_local_workflow_source(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def normalize_remote_workflow_source(path: str) -> str:
    normalized = str(PurePosixPath(str(path or "").strip()))
    if not normalized.startswith("/"):
        raise ValueError("Remote workflow paths must be absolute")
    return normalized


def parse_dogme_nextflow_config_text(config_text: str) -> dict[str, object]:
    sample_name = _match_assignment(config_text, "sample")
    mode = _match_assignment(config_text, "readType")
    modifications = _match_assignment(config_text, "modifications")
    genomes = _extract_reference_genomes(config_text)
    if not genomes:
        genomes = _infer_reference_genomes_from_paths(config_text)

    normalized_mode = str(mode or "").strip().upper() or None
    return {
        "sample_name": sample_name,
        "mode": normalized_mode,
        "reference_genome": genomes,
        "modifications": modifications,
    }


def infer_local_workflow_metadata(workflow_dir: Path) -> WorkflowImportMetadata:
    workflow_metadata = _infer_local_workflow_metadata_json(workflow_dir)
    if workflow_metadata is not None:
        return workflow_metadata

    # TODO(Phase 3): Add file-tree pattern fallback detection for foreign or
    # pre-metadata wf-pore-c output trees that do not carry .agoutic.workflow.json.

    config_path = find_local_workflow_config(workflow_dir)
    if config_path is None:
        raise ValueError(
            f"No Dogme .config file found in {workflow_dir}. "
            "Expected nextflow.config or another top-level *.config file."
        )

    parsed = parse_dogme_nextflow_config_text(config_path.read_text(encoding="utf-8", errors="ignore"))
    return WorkflowImportMetadata(
        workflow_key="dogme",
        sample_name=_clean_optional_text(parsed.get("sample_name")),
        mode=_clean_optional_text(parsed.get("mode")),
        reference_genome=_normalize_reference_genome_list(parsed.get("reference_genome")),
        modifications=_clean_optional_text(parsed.get("modifications"), preserve_empty=True),
        config_path=str(config_path),
        source_complete=_infer_local_workflow_complete(workflow_dir),
        input_directory=_infer_local_input_directory(workflow_dir, _clean_optional_text(parsed.get("sample_name"))),
    )


async def infer_remote_workflow_metadata(conn, workflow_dir: str) -> WorkflowImportMetadata:
    workflow_metadata = await _infer_remote_workflow_metadata_json(conn, workflow_dir)
    if workflow_metadata is not None:
        return workflow_metadata

    config_path = await find_remote_workflow_config(conn, workflow_dir)
    if not config_path:
        raise ValueError(
            f"No Dogme .config file found in remote workflow {workflow_dir}. "
            "Expected nextflow.config or another top-level *.config file."
        )

    result = await conn.run(f"cat {shlex.quote(config_path)}", check=True)
    parsed = parse_dogme_nextflow_config_text(result.stdout or "")
    return WorkflowImportMetadata(
        workflow_key="dogme",
        sample_name=_clean_optional_text(parsed.get("sample_name")),
        mode=_clean_optional_text(parsed.get("mode")),
        reference_genome=_normalize_reference_genome_list(parsed.get("reference_genome")),
        modifications=_clean_optional_text(parsed.get("modifications"), preserve_empty=True),
        config_path=config_path,
        source_complete=await _infer_remote_workflow_complete(conn, workflow_dir),
        input_directory=await _infer_remote_input_directory(conn, workflow_dir, _clean_optional_text(parsed.get("sample_name"))),
    )


def find_local_workflow_config(workflow_dir: Path) -> Path | None:
    preferred = workflow_dir / "nextflow.config"
    if preferred.is_file():
        return preferred

    top_level = sorted(
        child for child in workflow_dir.iterdir() if child.is_file() and child.suffix == ".config"
    ) if workflow_dir.exists() else []
    if top_level:
        return top_level[0]

    if not workflow_dir.exists():
        return None

    for child in sorted(workflow_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or child.name in _SKIP_CONFIG_DIRS or child.name.startswith("."):
            continue
        nested = sorted(grandchild for grandchild in child.iterdir() if grandchild.is_file() and grandchild.suffix == ".config")
        if nested:
            return nested[0]
    return None


async def find_remote_workflow_config(conn, workflow_dir: str) -> str | None:
    workflow_dir = normalize_remote_workflow_source(workflow_dir)
    preferred = str(PurePosixPath(workflow_dir) / "nextflow.config")
    if await conn.path_exists(preferred):
        return preferred

    find_cmd = (
        f"find {shlex.quote(workflow_dir)} "
        "\\( -path '*/work' -o -path '*/.rsync-partial' \\) -prune -o "
        "-maxdepth 2 -type f -name '*.config' -print | sort"
    )
    result = await conn.run(f"{find_cmd} 2>/dev/null || true")
    candidates = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not candidates:
        return None
    nextflow_candidates = [candidate for candidate in candidates if candidate.endswith("/nextflow.config")]
    return nextflow_candidates[0] if nextflow_candidates else candidates[0]


def discover_local_result_artifacts(source_dir: Path, *, full_copy: bool, workflow_key: str | None = None) -> dict[str, list[str]]:
    if full_copy:
        directories = sorted(
            child.name
            for child in source_dir.iterdir()
            if child.is_dir() and child.name != ".rsync-partial"
        )
        files = sorted(
            child.name
            for child in source_dir.iterdir()
            if child.is_file() or child.is_symlink()
        )
        return {"directories": directories, "files": files}

    effective_workflow_key = _infer_local_result_workflow_key(source_dir, workflow_key=workflow_key)
    directories = [
        dirname
        for dirname in result_sync_dirs_for_workflow(effective_workflow_key)
        if (source_dir / dirname).exists()
    ]
    files = [
        child.name
        for child in sorted(source_dir.iterdir(), key=lambda item: item.name.lower())
        if (child.is_file() or child.is_symlink()) and _matches_result_file_pattern(child.name, workflow_key=effective_workflow_key)
    ]
    return {"directories": directories, "files": files}


def copy_local_results_to_workflow(
    source_dir: Path,
    destination_dir: Path,
    *,
    full_copy: bool,
    workflow_key: str | None = None,
) -> dict[str, list[str]]:
    artifacts = discover_local_result_artifacts(source_dir, full_copy=full_copy, workflow_key=workflow_key)
    destination_dir.mkdir(parents=True, exist_ok=True)

    if full_copy:
        for child in sorted(source_dir.iterdir(), key=lambda item: item.name.lower()):
            if child.name == ".rsync-partial":
                continue
            _copy_path(child, destination_dir / child.name)
    else:
        for dirname in artifacts.get("directories", []):
            _copy_path(source_dir / dirname, destination_dir / dirname)
        for filename in artifacts.get("files", []):
            _copy_path(source_dir / filename, destination_dir / filename)

    verify_local_import_artifacts(destination_dir, artifacts)
    return artifacts


def verify_local_import_artifacts(destination_dir: Path, artifacts: dict[str, list[str]]) -> None:
    missing: list[str] = []
    for dirname in artifacts.get("directories", []):
        if not (destination_dir / dirname).exists():
            missing.append(dirname)
    for filename in artifacts.get("files", []):
        if not (destination_dir / filename).exists():
            missing.append(filename)
    if missing:
        raise RuntimeError(
            "Imported workflow is missing expected artifacts: " + ", ".join(sorted(missing))
        )


def import_warning_message(source_complete: bool | None) -> str | None:
    if source_complete is False:
        return "Imported from a workflow that does not look complete yet. Run /sync-workflow later to pull new outputs."
    return None


def _match_assignment(config_text: str, name: str) -> str | None:
    pattern = re.compile(_CONFIG_ASSIGNMENT_RE.format(name=re.escape(name)), re.MULTILINE)
    match = pattern.search(config_text)
    if not match:
        return None
    return match.group(2)


def _extract_reference_genomes(config_text: str) -> list[str]:
    genomes: list[str] = []
    for genome_name in _GENOME_NAME_RE.findall(config_text or ""):
        cleaned = genome_name.strip()
        if cleaned and cleaned not in genomes:
            genomes.append(cleaned)
    return genomes


def _infer_reference_genomes_from_paths(config_text: str) -> list[str]:
    normalized_text = str(config_text or "")
    genomes: list[str] = []
    for genome_name, ref_data in REFERENCE_GENOMES.items():
        if genome_name == "default" or not isinstance(ref_data, dict):
            continue
        for key in ("fasta", "gtf", "kallisto_index", "kallisto_t2g"):
            candidate = str(ref_data.get(key) or "")
            if candidate and candidate in normalized_text:
                genomes.append(genome_name)
                break
    return genomes


def _normalize_reference_genome_list(value) -> list[str]:
    if isinstance(value, list):
        raw_values = value
    elif value in (None, ""):
        raw_values = []
    else:
        raw_values = [value]

    normalized: list[str] = []
    for item in raw_values:
        cleaned = str(item or "").strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _clean_optional_text(value, *, preserve_empty: bool = False) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if cleaned == "" and not preserve_empty:
        return None
    return cleaned


def _infer_local_workflow_metadata_json(workflow_dir: Path) -> WorkflowImportMetadata | None:
    metadata_path = workflow_dir / ".agoutic.workflow.json"
    if not metadata_path.is_file():
        return None

    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to parse local workflow metadata", path=str(metadata_path), exc_info=True)
        return None

    summary_contract = payload.get("summary_contract") if isinstance(payload, dict) else {}
    if not isinstance(summary_contract, dict):
        summary_contract = {}
    validated_inputs = payload.get("validated_inputs") if isinstance(payload, dict) else {}
    if not isinstance(validated_inputs, dict):
        validated_inputs = {}

    workflow_key = _clean_optional_text(payload.get("workflow_key")) or _clean_optional_text(summary_contract.get("workflow_key"))
    if workflow_key != "wf_pore_c":
        return None

    return WorkflowImportMetadata(
        workflow_key="wf_pore_c",
        sample_name=_clean_optional_text(summary_contract.get("sample_name")) or _clean_optional_text(validated_inputs.get("sample_name")),
        mode=None,
        reference_genome=_infer_reference_genomes_from_paths(json.dumps(validated_inputs, sort_keys=True)),
        modifications=None,
        config_path=str(metadata_path),
        source_complete=_infer_local_workflow_complete(workflow_dir),
        input_directory=_clean_optional_text(validated_inputs.get("input_path")),
    )


async def _infer_remote_workflow_metadata_json(conn, workflow_dir: str) -> WorkflowImportMetadata | None:
    metadata_path = str(PurePosixPath(workflow_dir) / ".agoutic.workflow.json")
    if not await conn.path_exists(metadata_path):
        return None

    try:
        result = await conn.run(f"cat {shlex.quote(metadata_path)}", check=True)
        payload = json.loads(result.stdout or "")
    except Exception:
        logger.warning("Failed to parse remote workflow metadata", path=metadata_path, exc_info=True)
        return None

    summary_contract = payload.get("summary_contract") if isinstance(payload, dict) else {}
    if not isinstance(summary_contract, dict):
        summary_contract = {}
    validated_inputs = payload.get("validated_inputs") if isinstance(payload, dict) else {}
    if not isinstance(validated_inputs, dict):
        validated_inputs = {}

    workflow_key = _clean_optional_text(payload.get("workflow_key")) or _clean_optional_text(summary_contract.get("workflow_key"))
    if workflow_key != "wf_pore_c" or not bool(launchpad_config.WF_PORE_C_ENABLED):
        return None

    return WorkflowImportMetadata(
        workflow_key="wf_pore_c",
        sample_name=_clean_optional_text(summary_contract.get("sample_name")) or _clean_optional_text(validated_inputs.get("sample_name")),
        mode=None,
        reference_genome=_infer_reference_genomes_from_paths(json.dumps(validated_inputs, sort_keys=True)),
        modifications=None,
        config_path=metadata_path,
        source_complete=await _infer_remote_workflow_complete(conn, workflow_dir),
        input_directory=_clean_optional_text(validated_inputs.get("input_path")),
    )


def _infer_local_result_workflow_key(source_dir: Path, *, workflow_key: str | None) -> str | None:
    normalized = _normalize_workflow_key(workflow_key)
    if normalized:
        return normalized
    metadata = _infer_local_workflow_metadata_json(source_dir)
    return metadata.workflow_key if metadata is not None else None


def result_sync_dirs_for_workflow(workflow_key: str | None) -> tuple[str, ...]:
    normalized = _normalize_workflow_key(workflow_key)
    if normalized == "wf_pore_c" and bool(launchpad_config.WF_PORE_C_ENABLED):
        return _WF_PORE_C_RESULT_SYNC_DIRS
    return _DOGME_RESULT_SYNC_DIRS


def result_sync_file_patterns_for_workflow(workflow_key: str | None) -> tuple[str, ...]:
    normalized = _normalize_workflow_key(workflow_key)
    if normalized == "wf_pore_c" and bool(launchpad_config.WF_PORE_C_ENABLED):
        return tuple(dict.fromkeys(_WF_PORE_C_RESULT_SYNC_FILE_PATTERNS))
    return _DOGME_RESULT_SYNC_FILE_PATTERNS


def _matches_result_file_pattern(filename: str, *, workflow_key: str | None) -> bool:
    return any(
        fnmatch.fnmatch(filename, pattern)
        for pattern in result_sync_file_patterns_for_workflow(workflow_key)
    )


def _normalize_workflow_key(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower()
    return cleaned or None


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_symlink():
        if destination.exists() or destination.is_symlink():
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(os.readlink(source), destination)
        return

    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination, follow_symlinks=False)


def _infer_local_workflow_complete(workflow_dir: Path) -> bool:
    if (workflow_dir / ".nextflow_success").exists():
        return True
    return not any((workflow_dir / marker).exists() for marker in _PARTIAL_MARKERS)


async def _infer_remote_workflow_complete(conn, workflow_dir: str) -> bool:
    success_marker = str(PurePosixPath(workflow_dir) / ".nextflow_success")
    if await conn.path_exists(success_marker):
        return True
    for marker in _PARTIAL_MARKERS:
        if await conn.path_exists(str(PurePosixPath(workflow_dir) / marker)):
            return False
    return True


def _infer_local_input_directory(workflow_dir: Path, sample_name: str | None) -> str | None:
    pod5_path = workflow_dir / "pod5"
    if pod5_path.exists() or pod5_path.is_symlink():
        return _resolve_local_link_target(pod5_path)

    bam_dir = workflow_dir / "bams"
    if bam_dir.exists():
        matching = _match_local_unmapped_bam(bam_dir, sample_name)
        if matching is not None:
            return _resolve_local_link_target(matching)
    return None


async def _infer_remote_input_directory(conn, workflow_dir: str, sample_name: str | None) -> str | None:
    pod5_path = str(PurePosixPath(workflow_dir) / "pod5")
    if await conn.path_exists(pod5_path):
        resolved = await _read_remote_link_target(conn, pod5_path)
        return resolved or pod5_path

    bam_dir = str(PurePosixPath(workflow_dir) / "bams")
    find_parts = ["find", shlex.quote(bam_dir), "-maxdepth", "1", "-type", "f", "-name", "'*.unmapped.bam'", "-print", "|", "sort"]
    result = await conn.run(" ".join(find_parts) + " 2>/dev/null || true")
    candidates = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not candidates:
        return None

    selected = None
    normalized_sample = (sample_name or "").strip().lower()
    if normalized_sample:
        for candidate in candidates:
            if normalized_sample in PurePosixPath(candidate).name.lower():
                selected = candidate
                break
    if selected is None:
        selected = candidates[0]
    resolved = await _read_remote_link_target(conn, selected)
    return resolved or selected


def _resolve_local_link_target(path: Path) -> str | None:
    try:
        if path.is_symlink():
            return str(path.resolve(strict=False))
        if path.exists():
            return str(path.resolve())
    except Exception:
        logger.warning("Failed to resolve local workflow input path", path=str(path), exc_info=True)
    return str(path) if path.exists() or path.is_symlink() else None


def _match_local_unmapped_bam(bam_dir: Path, sample_name: str | None) -> Path | None:
    candidates = sorted(bam_dir.glob("*.unmapped.bam"))
    if not candidates:
        return None
    normalized_sample = (sample_name or "").strip().lower()
    if normalized_sample:
        for candidate in candidates:
            if normalized_sample in candidate.name.lower():
                return candidate
    return candidates[0]


async def _read_remote_link_target(conn, remote_path: str) -> str | None:
    command = (
        "python3 - <<'PY'\n"
        "import os\n"
        f"path = {remote_path!r}\n"
        "if os.path.islink(path):\n"
        "    print(os.path.realpath(path))\n"
        "elif os.path.exists(path):\n"
        "    print(os.path.realpath(path))\n"
        "PY"
    )
    result = await conn.run(command)
    resolved = (result.stdout or "").strip()
    return resolved or None