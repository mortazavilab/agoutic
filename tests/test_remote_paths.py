"""Tests for path validation result types."""
import pytest

from launchpad.backends.path_validator import PathValidationResult, check_all_paths_ok


class TestCheckAllPathsOk:
    def test_all_paths_ok(self):
        results = {
            "work": PathValidationResult(path="/scratch/work", exists=True, writable=True),
            "output": PathValidationResult(path="/scratch/out", exists=True, writable=True),
        }
        ok, errors = check_all_paths_ok(results)
        assert ok is True
        assert errors == []

    def test_one_path_with_error(self):
        results = {
            "work": PathValidationResult(path="/scratch/work", exists=True, writable=True),
            "output": PathValidationResult(path="/scratch/out", error="Permission denied"),
        }
        ok, errors = check_all_paths_ok(results)
        assert ok is False
        assert len(errors) == 1
        assert "Permission denied" in errors[0]

    def test_empty_path_returns_error(self):
        results = {
            "work": PathValidationResult(path="", error="Path is empty"),
        }
        ok, errors = check_all_paths_ok(results)
        assert ok is False
        assert any("empty" in e.lower() for e in errors)

    def test_path_exists_but_not_writable(self):
        results = {
            "data": PathValidationResult(path="/readonly/dir", exists=True, writable=False),
        }
        ok, errors = check_all_paths_ok(results)
        assert ok is False
        assert any("not writable" in e for e in errors)

    def test_multiple_failures_returns_all_errors(self):
        results = {
            "work": PathValidationResult(path="", error="Path is empty"),
            "output": PathValidationResult(path="/bad", error="No such directory"),
            "logs": PathValidationResult(path="/readonly", exists=True, writable=False),
        }
        ok, errors = check_all_paths_ok(results)
        assert ok is False
        assert len(errors) == 3
