from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd


_DEFAULT_ABUNDANCE_NAMES = (
    "reconciled_abundance.tsv",
    "reconciled_abundance.csv",
    "abundance.tsv",
    "abundance.csv",
)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "group")).strip("_").lower()
    return normalized or "group"


def resolve_abundance_source(
    source_path: str | None = None,
    work_dir: str | None = None,
) -> Path:
    if source_path:
        candidate = Path(source_path).expanduser()
        if candidate.exists():
            return candidate
        if work_dir and not candidate.is_absolute():
            work_candidate = Path(work_dir).expanduser() / source_path
            if work_candidate.exists():
                return work_candidate
        raise ValueError(f"DE source file was not found: {source_path}")

    if not work_dir:
        raise ValueError("No abundance file or workflow directory was provided.")

    work_path = Path(work_dir).expanduser()
    for name in _DEFAULT_ABUNDANCE_NAMES:
        candidate = work_path / name
        if candidate.exists():
            return candidate

    for pattern in ("*abundance*.tsv", "*abundance*.csv"):
        matches = sorted(work_path.glob(pattern))
        if matches:
            return matches[0]

    expected = ", ".join(_DEFAULT_ABUNDANCE_NAMES)
    raise ValueError(
        f"No abundance table was found in {work_dir}. Expected one of: {expected}"
    )


def dataframe_payload_to_frame(payload: dict[str, Any]) -> pd.DataFrame:
    columns = payload.get("columns") or []
    rows = payload.get("data") or []
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ValueError("The dataframe payload is missing columns or row data.")
    return pd.DataFrame(rows, columns=columns)


def _load_source_frame(source: str | dict[str, Any], work_dir: str | None = None) -> tuple[pd.DataFrame, str]:
    if isinstance(source, dict):
        frame = dataframe_payload_to_frame(source)
        label = (
            ((source.get("metadata") or {}).get("label"))
            or ((source.get("metadata") or {}).get("file_name"))
            or "dataframe"
        )
        return frame, str(label)

    source_path = resolve_abundance_source(source, work_dir=work_dir)
    separator = "," if source_path.suffix.lower() == ".csv" else "\t"
    frame = pd.read_csv(source_path, sep=separator)
    return frame, source_path.name


def _normalize_sample_columns(frame: pd.DataFrame, sample_columns: list[str]) -> tuple[list[str], list[str]]:
    column_lookup = {str(column).strip().lower(): str(column) for column in frame.columns}
    resolved: list[str] = []
    missing: list[str] = []
    for sample in sample_columns:
        key = str(sample).strip().lower()
        column = column_lookup.get(key)
        if column is None:
            missing.append(str(sample))
        else:
            resolved.append(column)
    return resolved, missing


def prepare_de_inputs(
    source: str | dict[str, Any] | None,
    *,
    output_dir: str,
    group_a_label: str,
    group_a_samples: list[str],
    group_b_label: str,
    group_b_samples: list[str],
    level: str = "gene",
    work_dir: str | None = None,
) -> dict[str, Any]:
    if not source and not work_dir:
        raise ValueError("No abundance source was provided for DE preparation.")

    source_value = source or ""
    frame, source_label = _load_source_frame(source_value, work_dir=work_dir)

    if not group_a_samples or not group_b_samples:
        raise ValueError("Both comparison groups must include at least one sample column.")

    overlap = sorted(set(group_a_samples).intersection(group_b_samples))
    if overlap:
        raise ValueError(
            f"The same sample columns cannot appear in both groups: {', '.join(overlap)}"
        )

    requested_samples = list(group_a_samples) + list(group_b_samples)
    resolved_columns, missing = _normalize_sample_columns(frame, requested_samples)
    if missing:
        raise ValueError(
            "These requested sample columns were not found in the abundance table: "
            + ", ".join(missing)
        )

    counts_only = frame.loc[:, resolved_columns].apply(pd.to_numeric, errors="coerce").fillna(0)

    level_normalized = str(level or "gene").strip().lower()
    if level_normalized == "transcript":
        feature_column = "transcript_ID" if "transcript_ID" in frame.columns else "gene_ID"
    else:
        level_normalized = "gene"
        feature_column = "gene_ID"

    if feature_column not in frame.columns:
        raise ValueError(
            f"The abundance table does not contain the required column '{feature_column}'."
        )

    indexed = pd.concat([frame[[feature_column]], counts_only], axis=1)
    indexed = indexed[indexed[feature_column].notna()].copy()
    indexed[feature_column] = indexed[feature_column].astype(str).str.strip()
    indexed = indexed[indexed[feature_column] != ""]

    if level_normalized == "gene":
        counts_matrix = indexed.groupby(feature_column, dropna=False)[resolved_columns].sum()
    else:
        counts_matrix = indexed.groupby(feature_column, dropna=False)[resolved_columns].sum()

    counts_matrix.index.name = feature_column

    sample_info = pd.DataFrame(
        {
            "sample": resolved_columns,
            "group": [str(group_a_label)] * len(group_a_samples)
            + [str(group_b_label)] * len(group_b_samples),
        }
    )

    output_root = Path(output_dir).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)

    result_name = f"{_slug(group_a_label)}_vs_{_slug(group_b_label)}_{level_normalized}"
    counts_path = output_root / f"{result_name}_counts.tsv"
    sample_info_path = output_root / f"{result_name}_sample_info.csv"

    counts_matrix.to_csv(counts_path, sep="\t")
    sample_info.to_csv(sample_info_path, index=False)

    return {
        "counts_path": str(counts_path),
        "sample_info_path": str(sample_info_path),
        "group_column": "group",
        "contrast": f"{group_a_label} - {group_b_label}",
        "pair": [str(group_a_label), str(group_b_label)],
        "group_a_label": str(group_a_label),
        "group_b_label": str(group_b_label),
        "group_a_samples": list(group_a_samples),
        "group_b_samples": list(group_b_samples),
        "result_name": result_name,
        "level": level_normalized,
        "feature_column": feature_column,
        "n_features": int(counts_matrix.shape[0]),
        "n_samples": int(counts_matrix.shape[1]),
        "source_label": source_label,
    }
