"""Tests for reconcile thread clamping in workflow_submission._build_reconcile_script_args."""

import os
from unittest.mock import patch

import pytest

from cortex.workflow_submission import _build_reconcile_script_args, _bounded_reconcile_threads


def _base_job_params(**overrides):
    params = {
        "bam_inputs": [{"path": "/data/sample.GRCh38.annotated.bam"}],
        "annotation_gtf": "/refs/GRCh38/annotation.gtf",
        "output_prefix": "reconciled",
        "output_directory": "/data/output",
    }
    params.update(overrides)
    return params


class TestBoundedReconcileThreads:
    def test_default_without_env(self, monkeypatch):
        monkeypatch.delenv("RECONCILE_BAMS_DEFAULT_THREADS", raising=False)
        monkeypatch.delenv("RECONCILE_BAMS_MAX_THREADS", raising=False)
        assert _bounded_reconcile_threads() == 4

    def test_caps_high_value(self, monkeypatch):
        monkeypatch.delenv("RECONCILE_BAMS_DEFAULT_THREADS", raising=False)
        monkeypatch.delenv("RECONCILE_BAMS_MAX_THREADS", raising=False)
        assert _bounded_reconcile_threads(64) == 8

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("RECONCILE_BAMS_DEFAULT_THREADS", "2")
        monkeypatch.setenv("RECONCILE_BAMS_MAX_THREADS", "6")
        assert _bounded_reconcile_threads() == 2
        assert _bounded_reconcile_threads(10) == 6

    def test_floor_is_one(self, monkeypatch):
        monkeypatch.delenv("RECONCILE_BAMS_DEFAULT_THREADS", raising=False)
        monkeypatch.delenv("RECONCILE_BAMS_MAX_THREADS", raising=False)
        assert _bounded_reconcile_threads(0) == 1


class TestBuildReconcileScriptArgs:
    def test_threads_always_emitted(self, monkeypatch):
        monkeypatch.delenv("RECONCILE_BAMS_DEFAULT_THREADS", raising=False)
        monkeypatch.delenv("RECONCILE_BAMS_MAX_THREADS", raising=False)
        args = _build_reconcile_script_args(_base_job_params())
        assert "--threads" in args
        idx = args.index("--threads")
        assert args[idx + 1] == "4"  # default when not specified

    def test_threads_clamped_when_high(self, monkeypatch):
        monkeypatch.delenv("RECONCILE_BAMS_DEFAULT_THREADS", raising=False)
        monkeypatch.delenv("RECONCILE_BAMS_MAX_THREADS", raising=False)
        args = _build_reconcile_script_args(_base_job_params(threads=128))
        idx = args.index("--threads")
        assert args[idx + 1] == "8"

    def test_threads_passthrough_when_within_cap(self, monkeypatch):
        monkeypatch.delenv("RECONCILE_BAMS_DEFAULT_THREADS", raising=False)
        monkeypatch.delenv("RECONCILE_BAMS_MAX_THREADS", raising=False)
        args = _build_reconcile_script_args(_base_job_params(threads=6))
        idx = args.index("--threads")
        assert args[idx + 1] == "6"

    def test_threads_not_duplicated_in_scalar_flags(self, monkeypatch):
        monkeypatch.delenv("RECONCILE_BAMS_DEFAULT_THREADS", raising=False)
        monkeypatch.delenv("RECONCILE_BAMS_MAX_THREADS", raising=False)
        args = _build_reconcile_script_args(_base_job_params(threads=4))
        assert args.count("--threads") == 1
