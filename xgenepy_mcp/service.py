"""Core local XgenePy execution service with strict input safety controls."""

from __future__ import annotations

import importlib
import importlib.util
import json
import platform
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import pandas as pd

_CANONICAL_FILES = [
    "fit_summary.json",
    "assignments.tsv",
    "proportion_cis.tsv",
    "model_metadata.json",
    "run_manifest.json",
]
_REQUIRED_METADATA = {"sample_id", "strain", "allele"}
_OPTIONAL_METADATA = {"condition", "replicate"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_windows_absolute(path_str: str) -> bool:
    return len(path_str) > 1 and path_str[1] == ":" and path_str[0].isalpha()


def _validate_relative_reference(path_str: str, field_name: str) -> str:
    if not isinstance(path_str, str) or not path_str.strip():
        raise ValueError(f"{field_name} must be a non-empty relative path")

    value = path_str.strip().replace("\\", "/")
    if "\x00" in value:
        raise ValueError(f"{field_name} contains an invalid null byte")
    if value.startswith("/") or _is_windows_absolute(value):
        raise ValueError(f"{field_name} must be project-relative, absolute paths are not allowed")

    pure = PurePosixPath(value)
    if any(part == ".." for part in pure.parts):
        raise ValueError(f"{field_name} cannot contain path traversal segments")

    if str(pure) in {"", "."}:
        raise ValueError(f"{field_name} must resolve to a file path")

    return str(pure)


def _resolve_under_root(root: Path, rel_ref: str, field_name: str) -> Path:
    safe_ref = _validate_relative_reference(rel_ref, field_name)
    candidate = (root / safe_ref).resolve()
    root_resolved = root.resolve()
    if not str(candidate).startswith(str(root_resolved)):
        raise ValueError(f"{field_name} escapes project root")
    return candidate


def _read_table(path: Path) -> pd.DataFrame:
    sep = "\t" if path.suffix.lower() == ".tsv" else ","
    return pd.read_csv(path, sep=sep)


def _detect_versions(xgenepy_module: Any) -> dict[str, str]:
    xgenepy_version = getattr(xgenepy_module, "__version__", "unknown")
    return {
        "python": platform.python_version(),
        "xgenepy": str(xgenepy_version),
        "pandas": str(pd.__version__),
    }


def _ensure_metadata_contract(metadata: pd.DataFrame) -> None:
    columns = {str(col).strip() for col in metadata.columns}
    missing = sorted(_REQUIRED_METADATA - columns)
    if missing:
        raise ValueError(
            "Metadata is missing required columns: "
            + ", ".join(missing)
            + ". Required: sample_id, strain, allele. Optional: condition, replicate."
        )


def _save_figure(fig: Any, target: Path) -> bool:
    if fig is None:
        return False
    try:
        fig.savefig(target, dpi=160, bbox_inches="tight")
        return True
    except Exception:
        return False


def run_xgenepy_analysis(
    *,
    project_dir: str,
    counts_path: str,
    metadata_path: str,
    output_subdir: str | None = None,
    trans_model: str = "log_additive",
    fields_to_test: list[str] | None = None,
    combo: str | None = None,
    alpha: float = 0.05,
    execution_mode: str = "local",
    project_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute a local XgenePy workflow and emit canonical outputs."""
    started_at = _utc_now()

    if execution_mode != "local":
        return {
            "success": False,
            "error": "XgenePy Phase 1 supports local execution only.",
            "execution_mode": execution_mode,
        }

    root = Path(project_dir).expanduser()
    if not root.is_absolute():
        return {
            "success": False,
            "error": "project_dir must be an absolute project directory path.",
        }
    if not root.exists() or not root.is_dir():
        return {
            "success": False,
            "error": f"project_dir does not exist: {project_dir}",
        }

    try:
        safe_counts_ref = _validate_relative_reference(counts_path, "counts_path")
        safe_metadata_ref = _validate_relative_reference(metadata_path, "metadata_path")
        run_subdir = output_subdir or f"xgenepy_runs/run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        safe_output_ref = _validate_relative_reference(run_subdir, "output_subdir")

        counts_abs = _resolve_under_root(root, safe_counts_ref, "counts_path")
        metadata_abs = _resolve_under_root(root, safe_metadata_ref, "metadata_path")
        out_dir_abs = _resolve_under_root(root, safe_output_ref, "output_subdir")
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    if not counts_abs.exists():
        return {"success": False, "error": f"counts_path not found: {safe_counts_ref}"}
    if not metadata_abs.exists():
        return {"success": False, "error": f"metadata_path not found: {safe_metadata_ref}"}

    if importlib.util.find_spec("xgenepy") is None:
        return {
            "success": False,
            "error": (
                "XgenePy dependency is missing. Install it in the active environment, "
                "for example: conda env create -f <XgenePy_repo>/environment.yaml "
                "&& conda activate xgenepy && pip install -e <XgenePy_repo>"
            ),
        }

    try:
        xgenepy_mod = importlib.import_module("xgenepy")
        FitObject = getattr(xgenepy_mod, "FitObject")
        fit_edgepython = getattr(xgenepy_mod, "fit_edgepython")
        get_assignments_and_plot = getattr(xgenepy_mod, "get_assignments_and_plot")
        plot_pval_histograms = getattr(xgenepy_mod, "plot_pval_histograms", None)
        plot_regulatory_histogram = getattr(xgenepy_mod, "plot_regulatory_histogram", None)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Failed to import XgenePy runtime symbols: {exc}",
        }

    try:
        counts_df = _read_table(counts_abs)
        metadata_df = _read_table(metadata_abs)
        _ensure_metadata_contract(metadata_df)

        out_dir_abs.mkdir(parents=True, exist_ok=True)
        plots_dir = out_dir_abs / "plots"
        plots_dir.mkdir(parents=True, exist_ok=True)

        fit_kwargs: dict[str, Any] = {
            "counts": counts_df,
            "metadata": metadata_df,
            "trans_model": trans_model,
        }
        if fields_to_test:
            fit_kwargs["fields_to_test"] = fields_to_test

        fit_obj = FitObject(**fit_kwargs)
        fit_obj = fit_edgepython(fit_obj)

        assignment_kwargs: dict[str, Any] = {"make_plot": True, "alpha": alpha}
        if combo:
            assignment_kwargs["combo"] = combo
        assignment_results = get_assignments_and_plot(fit_obj, **assignment_kwargs)

        assignments_df = assignment_results.dataframe.copy()
        assignments_path = out_dir_abs / "assignments.tsv"
        assignments_df.to_csv(assignments_path, sep="\t", index=False)

        cis_col = "cis_prop" if "cis_prop" in assignments_df.columns else None
        gene_col = "gene" if "gene" in assignments_df.columns else assignments_df.columns[0]
        if cis_col is not None:
            proportion_df = assignments_df[[gene_col, cis_col]].copy()
        else:
            proportion_df = assignments_df[[gene_col]].copy()
        proportion_path = out_dir_abs / "proportion_cis.tsv"
        proportion_df.to_csv(proportion_path, sep="\t", index=False)

        raw_pvals = getattr(fit_obj, "raw_pvals", None)
        if isinstance(raw_pvals, pd.DataFrame):
            fit_summary = {
                "row_count": int(len(raw_pvals)),
                "columns": [str(col) for col in raw_pvals.columns],
                "preview": json.loads(raw_pvals.head(20).to_json(orient="records", default_handler=str)),
            }
        else:
            fit_summary = {
                "row_count": 0,
                "columns": [],
                "preview": [],
            }

        fit_summary_path = out_dir_abs / "fit_summary.json"
        fit_summary_path.write_text(json.dumps(fit_summary, indent=2), encoding="utf-8")

        model_metadata = {
            "trans_model": trans_model,
            "fields_to_test": fields_to_test or [],
            "combo": combo,
            "alpha": float(alpha),
            "count_shape": [int(counts_df.shape[0]), int(counts_df.shape[1])],
            "metadata_columns": [str(col) for col in metadata_df.columns],
            "required_metadata": sorted(_REQUIRED_METADATA),
            "optional_metadata": sorted(_OPTIONAL_METADATA),
        }
        model_metadata_path = out_dir_abs / "model_metadata.json"
        model_metadata_path.write_text(json.dumps(model_metadata, indent=2), encoding="utf-8")

        saved_plots: list[str] = []
        assignment_plot_path = plots_dir / "assignments.png"
        if _save_figure(getattr(assignment_results, "figure", None), assignment_plot_path):
            saved_plots.append(f"{safe_output_ref}/plots/{assignment_plot_path.name}")

        if callable(plot_pval_histograms):
            try:
                pval_fig = plot_pval_histograms(fit_obj, combo=combo) if combo else plot_pval_histograms(fit_obj)
                pval_path = plots_dir / "pval_histograms.png"
                if _save_figure(pval_fig, pval_path):
                    saved_plots.append(f"{safe_output_ref}/plots/{pval_path.name}")
            except Exception:
                pass

        if callable(plot_regulatory_histogram):
            try:
                reg_fig = plot_regulatory_histogram(assignments_df, title=combo or "XgenePy assignments")
                reg_path = plots_dir / "regulatory_histogram.png"
                if _save_figure(reg_fig, reg_path):
                    saved_plots.append(f"{safe_output_ref}/plots/{reg_path.name}")
            except Exception:
                pass

        completed_at = _utc_now()
        manifest = {
            "schema_version": "1.0",
            "service": "xgenepy_mcp",
            "execution_mode": "local",
            "timestamps": {
                "started_at": started_at,
                "completed_at": completed_at,
            },
            "versions": _detect_versions(xgenepy_mod),
            "project_context": {
                "project_id": project_id or "",
                "project_ref": root.name,
                "run_id": run_id or "",
            },
            "parameters": {
                "trans_model": trans_model,
                "fields_to_test": fields_to_test or [],
                "combo": combo,
                "alpha": float(alpha),
            },
            "inputs": {
                "counts_path": safe_counts_ref,
                "metadata_path": safe_metadata_ref,
            },
            "outputs": {
                "run_dir": safe_output_ref,
                "fit_summary": f"{safe_output_ref}/fit_summary.json",
                "assignments": f"{safe_output_ref}/assignments.tsv",
                "proportion_cis": f"{safe_output_ref}/proportion_cis.tsv",
                "model_metadata": f"{safe_output_ref}/model_metadata.json",
                "manifest": f"{safe_output_ref}/run_manifest.json",
                "plots": saved_plots,
            },
        }

        manifest_path = out_dir_abs / "run_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        missing_artifacts = [
            name for name in _CANONICAL_FILES if not (out_dir_abs / name).exists()
        ]
        if not plots_dir.exists() or not plots_dir.is_dir():
            missing_artifacts.append("plots/")

        if missing_artifacts:
            return {
                "success": False,
                "error": "XgenePy run finished but required canonical outputs are missing.",
                "missing_outputs": missing_artifacts,
                "run_dir": safe_output_ref,
            }

        return {
            "success": True,
            "message": "XgenePy analysis completed successfully.",
            "run_dir": safe_output_ref,
            "canonical_outputs": [f"{safe_output_ref}/{name}" for name in _CANONICAL_FILES] + [f"{safe_output_ref}/plots/"],
            "manifest": manifest,
        }

    except Exception as exc:
        return {
            "success": False,
            "error": f"XgenePy execution failed: {exc}",
            "run_dir": output_subdir or "",
        }
