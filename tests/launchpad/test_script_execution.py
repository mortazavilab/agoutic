from pathlib import Path

import pytest

import launchpad.script_execution as script_execution


def test_resolve_allowlisted_script_by_id(monkeypatch, tmp_path):
    script_path = tmp_path / "reconcile_bams.py"
    script_path.write_text("print('ok')\n")

    monkeypatch.setattr(script_execution, "SCRIPT_ALLOWLIST_IDS", {"reconcileBams": script_path.resolve()})
    monkeypatch.setattr(script_execution, "SCRIPT_ALLOWLIST_ROOTS", [])

    resolved = script_execution.resolve_allowlisted_script(
        script_id="reconcileBams",
        script_path=None,
    )
    assert resolved.script_id == "reconcileBams"
    assert resolved.script_path == script_path.resolve()


def test_resolve_allowlisted_script_by_path_inside_root(monkeypatch, tmp_path):
    root = tmp_path / "allow"
    root.mkdir()
    script_path = root / "utility.py"
    script_path.write_text("print('ok')\n")

    monkeypatch.setattr(script_execution, "SCRIPT_ALLOWLIST_IDS", {})
    monkeypatch.setattr(script_execution, "SCRIPT_ALLOWLIST_ROOTS", [root.resolve()])

    resolved = script_execution.resolve_allowlisted_script(
        script_id=None,
        script_path=str(script_path),
    )
    assert resolved.script_path == script_path.resolve()


def test_resolve_allowlisted_script_rejects_missing_explicit_selector(monkeypatch):
    monkeypatch.setattr(script_execution, "SCRIPT_ALLOWLIST_IDS", {})
    monkeypatch.setattr(script_execution, "SCRIPT_ALLOWLIST_ROOTS", [])

    with pytest.raises(ValueError, match="Provide explicit script_id or explicit script_path"):
        script_execution.resolve_allowlisted_script(script_id=None, script_path=None)


def test_resolve_allowlisted_script_rejects_outside_allowlisted_root(monkeypatch, tmp_path):
    root = tmp_path / "allow"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('nope')\n")

    monkeypatch.setattr(script_execution, "SCRIPT_ALLOWLIST_IDS", {})
    monkeypatch.setattr(script_execution, "SCRIPT_ALLOWLIST_ROOTS", [root.resolve()])

    with pytest.raises(ValueError, match="outside allowlisted roots"):
        script_execution.resolve_allowlisted_script(script_id=None, script_path=str(outside))


def test_validate_script_working_directory_rejects_outside_allowlist(monkeypatch, tmp_path):
    root = tmp_path / "allow"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    monkeypatch.setattr(script_execution, "SCRIPT_ALLOWLIST_ROOTS", [root.resolve()])

    with pytest.raises(ValueError, match="outside allowlisted roots"):
        script_execution.validate_script_working_directory(str(other))


def test_normalize_script_args_rejects_non_strings():
    with pytest.raises(ValueError, match="contain only strings"):
        script_execution.normalize_script_args(["--ok", 1])
