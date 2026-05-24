"""Helpers for deriving workflow usage metrics from Nextflow trace files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_DURATION_TOKEN_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)(?P<unit>ms|s|m|h|d)")
_MEMORY_TOKEN_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?:\s*)(?P<unit>[KMGT](?:I?B)?|B)?$", re.IGNORECASE)
_SLURM_JOB_ID_RE = re.compile(r"^\d+(?:_\d+)?")
_GPU_TASK_SUFFIXES = {
    "doradoTask",
    "openChromatinTaskBg",
    "openChromatinTaskBed",
}


def _round_metric(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 3)


def parse_trace_duration_seconds(raw_value: str | None) -> float | None:
    """Parse a Nextflow trace duration/realtime value into seconds."""
    cleaned = str(raw_value or "").strip().lower().replace(",", "")
    if not cleaned or cleaned in {"-", "na", "n/a"}:
        return None

    if re.fullmatch(r"\d+(?::\d+){1,2}", cleaned):
        parts = [float(part) for part in cleaned.split(":")]
        if len(parts) == 2:
            minutes, seconds = parts
            return (minutes * 60.0) + seconds
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return (hours * 3600.0) + (minutes * 60.0) + seconds

    compact = cleaned.replace(" ", "")
    total_seconds = 0.0
    matched = False
    for match in _DURATION_TOKEN_RE.finditer(compact):
        matched = True
        value = float(match.group("value"))
        unit = match.group("unit")
        if unit == "ms":
            total_seconds += value / 1000.0
        elif unit == "s":
            total_seconds += value
        elif unit == "m":
            total_seconds += value * 60.0
        elif unit == "h":
            total_seconds += value * 3600.0
        elif unit == "d":
            total_seconds += value * 86400.0
    if matched:
        return total_seconds

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_trace_percent(raw_value: str | None) -> float | None:
    """Parse a Nextflow trace percent value such as ``320%``."""
    cleaned = str(raw_value or "").strip().rstrip("%")
    if not cleaned or cleaned in {"-", "na", "n/a"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_trace_memory_mb(raw_value: str | None) -> float | None:
    """Parse a Nextflow trace or sacct memory value into MiB-style megabytes."""
    cleaned = str(raw_value or "").strip().replace(",", "")
    if not cleaned or cleaned in {"-", "na", "n/a"}:
        return None

    match = _MEMORY_TOKEN_RE.fullmatch(cleaned)
    if not match:
        return None

    value = float(match.group("value"))
    unit = (match.group("unit") or "MB").upper()
    if unit == "B":
        return value / (1024.0 * 1024.0)
    if unit in {"K", "KB", "KIB"}:
        return value / 1024.0
    if unit in {"M", "MB", "MIB"}:
        return value
    if unit in {"G", "GB", "GIB"}:
        return value * 1024.0
    if unit in {"T", "TB", "TIB"}:
        return value * 1024.0 * 1024.0
    return None


def is_gpu_task_name(task_name: str | None) -> bool:
    """Return True when a workflow task is known to require a GPU."""
    cleaned = str(task_name or "").strip()
    if not cleaned:
        return False
    suffix = cleaned.rsplit(":", 1)[-1].strip()
    suffix = re.sub(r"\s*\(\d+\)$", "", suffix)
    return suffix in _GPU_TASK_SUFFIXES


def normalize_slurm_job_id(raw_value: str | None) -> str | None:
    """Normalize a SLURM job id from trace/sacct text."""
    cleaned = str(raw_value or "").strip()
    if not cleaned or cleaned in {"-", "na", "n/a"}:
        return None
    cleaned = cleaned.split(".", 1)[0]
    match = _SLURM_JOB_ID_RE.match(cleaned)
    return match.group(0) if match else None


def parse_nextflow_trace_task_records(trace_text: str) -> list[dict[str, Any]]:
    """Parse normalized task records from a Nextflow trace file."""
    lines = trace_text.splitlines() if trace_text else []
    if len(lines) <= 1:
        return []

    headers = [header.strip() for header in lines[0].split("\t")]
    index_by_header = {header: idx for idx, header in enumerate(headers)}
    name_idx = index_by_header.get("name")
    status_idx = index_by_header.get("status")
    if name_idx is None or status_idx is None:
        return []

    native_id_idx = index_by_header.get("native_id")
    realtime_idx = index_by_header.get("realtime")
    duration_idx = index_by_header.get("duration")
    percent_idx = index_by_header.get("%cpu")
    peak_rss_idx = index_by_header.get("peak_rss")
    peak_vmem_idx = index_by_header.get("peak_vmem")

    records: list[dict[str, Any]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.rstrip("\n").split("\t")
        task_name = parts[name_idx].strip() if name_idx < len(parts) else ""
        if ":" not in task_name:
            continue

        realtime_seconds = None
        if realtime_idx is not None and realtime_idx < len(parts):
            realtime_seconds = parse_trace_duration_seconds(parts[realtime_idx])
        if realtime_seconds is None and duration_idx is not None and duration_idx < len(parts):
            realtime_seconds = parse_trace_duration_seconds(parts[duration_idx])

        cpu_percent = None
        if percent_idx is not None and percent_idx < len(parts):
            cpu_percent = parse_trace_percent(parts[percent_idx])

        peak_rss_mb = None
        if peak_rss_idx is not None and peak_rss_idx < len(parts):
            peak_rss_mb = parse_trace_memory_mb(parts[peak_rss_idx])

        peak_vmem_mb = None
        if peak_vmem_idx is not None and peak_vmem_idx < len(parts):
            peak_vmem_mb = parse_trace_memory_mb(parts[peak_vmem_idx])

        native_id = None
        if native_id_idx is not None and native_id_idx < len(parts):
            native_id = normalize_slurm_job_id(parts[native_id_idx])

        records.append(
            {
                "task_name": task_name,
                "status": parts[status_idx].strip().upper() if status_idx < len(parts) else "",
                "native_id": native_id,
                "realtime_seconds": realtime_seconds,
                "cpu_percent": cpu_percent,
                "peak_rss_mb": peak_rss_mb,
                "peak_vmem_mb": peak_vmem_mb,
                "is_gpu_task": is_gpu_task_name(task_name),
            }
        )
    return records


def _summarize_nextflow_trace_records(
    records: list[dict[str, Any]],
    *,
    accounting_mode: str,
    trace_path: str | None = None,
) -> dict[str, Any] | None:
    if not records:
        return None

    completed_count = 0
    failed_count = 0
    cached_count = 0
    task_realtime_seconds = 0.0
    cpu_seconds = 0.0
    estimated_gpu_task_seconds = 0.0
    max_rss_mb = 0.0
    max_vmem_mb = 0.0

    for record in records:
        raw_status = str(record.get("status") or "").upper()
        if raw_status == "COMPLETED":
            completed_count += 1
        elif raw_status in {"FAILED", "ABORTED"}:
            failed_count += 1
        elif raw_status == "CACHED":
            cached_count += 1

        realtime_seconds = record.get("realtime_seconds")
        if realtime_seconds is not None:
            task_realtime_seconds += float(realtime_seconds)
            if record.get("is_gpu_task"):
                estimated_gpu_task_seconds += float(realtime_seconds)

        cpu_percent = record.get("cpu_percent")
        if realtime_seconds is not None and cpu_percent is not None:
            cpu_seconds += float(realtime_seconds) * (float(cpu_percent) / 100.0)

        peak_rss_mb = record.get("peak_rss_mb")
        if peak_rss_mb is not None:
            max_rss_mb = max(max_rss_mb, float(peak_rss_mb))

        peak_vmem_mb = record.get("peak_vmem_mb")
        if peak_vmem_mb is not None:
            max_vmem_mb = max(max_vmem_mb, float(peak_vmem_mb))

    summary: dict[str, Any] = {
        "source": "nextflow_trace",
        "accounting_mode": accounting_mode,
        "accounted_task_count": len(records),
        "completed_task_count": completed_count,
        "failed_task_count": failed_count,
        "cached_task_count": cached_count,
        "cpu_seconds": _round_metric(cpu_seconds),
        "task_realtime_seconds": _round_metric(task_realtime_seconds),
        "estimated_gpu_task_seconds": _round_metric(estimated_gpu_task_seconds),
        "max_rss_mb": _round_metric(max_rss_mb),
        "max_vmem_mb": _round_metric(max_vmem_mb),
    }
    if trace_path:
        summary["trace_path"] = trace_path
    return summary


def summarize_nextflow_trace_text(
    trace_text: str,
    *,
    accounting_mode: str,
    trace_path: str | None = None,
) -> dict[str, Any] | None:
    """Summarize workflow usage metrics from raw Nextflow trace text."""
    return _summarize_nextflow_trace_records(
        parse_nextflow_trace_task_records(trace_text),
        accounting_mode=accounting_mode,
        trace_path=trace_path,
    )


def summarize_nextflow_trace_file(trace_path: Path, *, accounting_mode: str) -> dict[str, Any] | None:
    """Summarize workflow usage metrics from a Nextflow trace file."""
    if not trace_path.exists():
        return None
    trace_text = trace_path.read_text(encoding="utf-8", errors="ignore")
    return summarize_nextflow_trace_text(
        trace_text,
        accounting_mode=accounting_mode,
        trace_path=str(trace_path),
    )


def collect_nextflow_trace_native_ids(trace_text: str) -> list[str]:
    """Return unique normalized native ids from a Nextflow trace file."""
    job_ids: list[str] = []
    seen: set[str] = set()
    for record in parse_nextflow_trace_task_records(trace_text):
        native_id = record.get("native_id")
        if not native_id or native_id in seen:
            continue
        seen.add(native_id)
        job_ids.append(native_id)
    return job_ids


def build_unavailable_workflow_usage(
    *,
    accounting_mode: str,
    message: str = "Usage statistics not available.",
) -> dict[str, Any]:
    """Build a stable placeholder when usage data cannot be recovered."""
    return {
        "source": "unavailable",
        "accounting_mode": accounting_mode,
        "usage_status": "unavailable",
        "usage_message": message,
    }


def _parse_slurm_elapsed_seconds(raw_value: str | None) -> float | None:
    cleaned = str(raw_value or "").strip()
    if not cleaned or cleaned in {"-", "na", "n/a"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return parse_trace_duration_seconds(cleaned)


def _parse_slurm_alloc_tres(raw_value: str | None) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for token in str(raw_value or "").split(","):
        cleaned = token.strip()
        if not cleaned or "=" not in cleaned:
            continue
        key, value = cleaned.split("=", 1)
        try:
            parsed[key.strip()] = float(value.strip())
        except ValueError:
            continue
    return parsed


def _allocated_gpu_count(alloc_tres: dict[str, float]) -> float:
    gpu_count = 0.0
    for key, value in alloc_tres.items():
        normalized_key = str(key or "").strip().lower()
        if normalized_key == "gpu" or normalized_key.startswith("gres/gpu"):
            gpu_count += float(value)
    return gpu_count


def _billing_resource_type_for_alloc_tres(alloc_tres: dict[str, float]) -> str:
    return "GPU" if _allocated_gpu_count(alloc_tres) > 0 else "CPU"


def summarize_slurm_workflow_usage(
    trace_text: str,
    sacct_text: str | None,
    *,
    trace_path: str | None = None,
    launcher_job_id: str | None = None,
) -> dict[str, Any] | None:
    """Merge Nextflow trace data with sacct accounting for remote SLURM usage."""
    records = parse_nextflow_trace_task_records(trace_text)
    trace_summary = _summarize_nextflow_trace_records(
        records,
        accounting_mode="slurm",
        trace_path=trace_path,
    )
    if trace_summary is None:
        return None

    jobs_by_native_id: dict[str, dict[str, Any]] = {}
    for record in records:
        native_id = record.get("native_id")
        if not native_id:
            continue
        job_details = jobs_by_native_id.setdefault(
            native_id,
            {
                "is_gpu_task": False,
            },
        )
        if record.get("is_gpu_task"):
            job_details["is_gpu_task"] = True

    summary = dict(trace_summary)
    summary["accounting_mode"] = "slurm"

    normalized_launcher_job_id = normalize_slurm_job_id(launcher_job_id)

    if not jobs_by_native_id and not normalized_launcher_job_id:
        summary["usage_status"] = "partial"
        summary["usage_message"] = "Using Nextflow trace only; scheduler accounting unavailable."
        return summary

    slurm_cpu_seconds = 0.0
    slurm_elapsed_seconds = 0.0
    slurm_gpu_seconds = 0.0
    slurm_billing_hours = 0.0
    slurm_billing_hours_by_account: dict[str, float] = {}
    slurm_billing_entries_by_key: dict[tuple[str, str], float] = {}
    slurm_max_rss_mb = 0.0
    accounted_job_ids: set[str] = set()
    accounted_task_job_ids: set[str] = set()
    launcher_accounted = False

    for line in (sacct_text or "").splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|", 6)]
        if len(parts) < 7:
            continue
        job_id = normalize_slurm_job_id(parts[0])
        is_launcher_job = bool(normalized_launcher_job_id and job_id == normalized_launcher_job_id)
        if (
            not job_id
            or job_id in accounted_job_ids
            or (job_id not in jobs_by_native_id and not is_launcher_job)
        ):
            continue

        accounted_job_ids.add(job_id)
        if is_launcher_job:
            launcher_accounted = True
        else:
            accounted_task_job_ids.add(job_id)
        account_name = str(parts[1] or "").strip()
        elapsed_seconds = _parse_slurm_elapsed_seconds(parts[3])
        total_cpu_seconds = parse_trace_duration_seconds(parts[4])
        max_rss_mb = parse_trace_memory_mb(parts[5])
        alloc_tres = _parse_slurm_alloc_tres(parts[6])
        resource_type = _billing_resource_type_for_alloc_tres(alloc_tres)

        if elapsed_seconds is not None:
            slurm_elapsed_seconds += elapsed_seconds
            billing_rate = alloc_tres.get("billing")
            if billing_rate is not None:
                billing_hours = float(billing_rate) * (elapsed_seconds / 3600.0)
                slurm_billing_hours += billing_hours
                if account_name:
                    slurm_billing_hours_by_account[account_name] = (
                        slurm_billing_hours_by_account.get(account_name, 0.0) + billing_hours
                    )
                billing_key = (resource_type, account_name)
                slurm_billing_entries_by_key[billing_key] = (
                    slurm_billing_entries_by_key.get(billing_key, 0.0) + billing_hours
                )
            gpu_count = _allocated_gpu_count(alloc_tres)
            if gpu_count > 0:
                slurm_gpu_seconds += elapsed_seconds * gpu_count

        if total_cpu_seconds is not None:
            slurm_cpu_seconds += total_cpu_seconds

        if max_rss_mb is not None:
            slurm_max_rss_mb = max(slurm_max_rss_mb, max_rss_mb)

    if not accounted_job_ids:
        summary["usage_status"] = "partial"
        summary["usage_message"] = "Using Nextflow trace only; scheduler accounting unavailable."
        return summary

    summary["source"] = "slurm_sacct+nextflow_trace"
    summary["slurm_task_job_count"] = len(jobs_by_native_id)
    summary["slurm_accounted_job_count"] = len(accounted_job_ids)
    summary["slurm_launcher_accounted"] = launcher_accounted

    if slurm_cpu_seconds > 0:
        summary["cpu_seconds"] = _round_metric(slurm_cpu_seconds)
    if slurm_elapsed_seconds > 0:
        summary["task_realtime_seconds"] = _round_metric(slurm_elapsed_seconds)
    if slurm_gpu_seconds > 0:
        rounded_gpu_seconds = _round_metric(slurm_gpu_seconds)
        summary["gpu_seconds"] = rounded_gpu_seconds
        summary["estimated_gpu_task_seconds"] = rounded_gpu_seconds
    if slurm_max_rss_mb > 0:
        summary["max_rss_mb"] = _round_metric(max(float(summary.get("max_rss_mb") or 0.0), slurm_max_rss_mb))
    if slurm_billing_hours > 0:
        summary["billing_units"] = _round_metric(slurm_billing_hours)
        summary["billing_label"] = "SLURM Billing Hours"
    if slurm_billing_hours_by_account:
        summary["billing_hours_by_account"] = {
            account_name: _round_metric(hours)
            for account_name, hours in sorted(slurm_billing_hours_by_account.items())
            if _round_metric(hours) not in (None, 0.0)
        }
    if slurm_billing_entries_by_key:
        resource_order = {"CPU": 0, "GPU": 1}
        billing_entries: list[dict[str, Any]] = []
        for (resource_type, account_name), hours in sorted(
            slurm_billing_entries_by_key.items(),
            key=lambda item: (resource_order.get(item[0][0], 99), item[0][1]),
        ):
            rounded_hours = _round_metric(hours)
            if rounded_hours in (None, 0.0):
                continue
            billing_entries.append(
                {
                    "resource_type": resource_type,
                    "account": account_name,
                    "billing_hours": rounded_hours,
                }
            )
        if billing_entries:
            summary["billing_entries"] = billing_entries

    missing_task_accounting = len(accounted_task_job_ids) < len(jobs_by_native_id)
    missing_launcher_accounting = bool(normalized_launcher_job_id and not launcher_accounted)
    if missing_task_accounting or missing_launcher_accounting:
        summary["usage_status"] = "partial"
        if missing_task_accounting and missing_launcher_accounting:
            summary["usage_message"] = "Using Nextflow trace only for some tasks, and launcher scheduler accounting was unavailable."
        elif missing_task_accounting:
            summary["usage_message"] = "Using Nextflow trace only for some tasks; scheduler accounting was unavailable for the rest."
        else:
            summary["usage_message"] = "Child task scheduler accounting is present, but launcher scheduler accounting was unavailable."
    else:
        summary["usage_status"] = "complete"
        summary.pop("usage_message", None)

    return summary