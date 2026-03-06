"""
Tests for cortex/user_jail.py — filesystem isolation and path traversal prevention.

Uses tmp_agoutic_data fixture so all paths resolve inside a temp dir.
"""

import os
import pytest
from pathlib import Path

from cortex.user_jail import (
    _validate_id,
    _validate_slug,
    _ensure_within_jail,
    resolve_user_path,
    get_user_project_dir,
    get_user_home_dir,
    resolve_user_path_by_uuid,
    get_user_project_dir_by_uuid,
    get_user_home_dir_by_uuid,
    rename_user_dir,
    rename_project_dir,
)


# ---------------------------------------------------------------------------
# _validate_id
# ---------------------------------------------------------------------------
class TestValidateId:
    def test_valid_alphanumeric(self):
        _validate_id("abc123", "test")  # Should not raise

    def test_valid_uuid(self):
        _validate_id("550e8400-e29b-41d4-a716-446655440000", "test")

    def test_valid_with_dots(self):
        _validate_id("some.value", "test")

    def test_empty_raises(self):
        with pytest.raises(PermissionError, match="must not be empty"):
            _validate_id("", "user_id")

    def test_slash_raises(self):
        with pytest.raises(PermissionError, match="disallowed characters"):
            _validate_id("abc/def", "user_id")

    def test_backslash_raises(self):
        with pytest.raises(PermissionError, match="disallowed characters"):
            _validate_id("abc\\def", "user_id")

    def test_null_byte_raises(self):
        with pytest.raises(PermissionError, match="disallowed characters"):
            _validate_id("abc\x00def", "user_id")

    def test_dotdot_raises(self):
        with pytest.raises(PermissionError, match="contains '..'"):
            _validate_id("..", "user_id")

    def test_dotdot_embedded_raises(self):
        with pytest.raises(PermissionError, match="contains '..'"):
            _validate_id("foo..bar", "test")


# ---------------------------------------------------------------------------
# _validate_slug
# ---------------------------------------------------------------------------
class TestValidateSlug:
    def test_valid_slug(self):
        _validate_slug("encode-experiment", "project")

    def test_valid_slug_with_underscore(self):
        _validate_slug("my_project", "project")

    def test_valid_short_slug(self):
        _validate_slug("a", "project")

    def test_empty_raises(self):
        with pytest.raises(PermissionError, match="must not be empty"):
            _validate_slug("", "project")

    def test_uppercase_raises(self):
        with pytest.raises(PermissionError, match="must match"):
            _validate_slug("MyProject", "project")

    def test_starts_with_hyphen_raises(self):
        with pytest.raises(PermissionError, match="must match"):
            _validate_slug("-bad-start", "project")

    def test_starts_with_underscore_raises(self):
        with pytest.raises(PermissionError, match="must match"):
            _validate_slug("_bad", "project")

    def test_too_long_raises(self):
        slug = "a" * 62  # Exceeds 61 char limit (1 start + 60 rest)
        with pytest.raises(PermissionError, match="must match"):
            _validate_slug(slug, "project")

    def test_max_length_ok(self):
        slug = "a" * 61  # 1 start + 60 rest = exactly at limit
        _validate_slug(slug, "project")  # Should not raise

    def test_special_chars_raise(self):
        with pytest.raises(PermissionError, match="must match"):
            _validate_slug("bad@name", "project")

    def test_spaces_raise(self):
        with pytest.raises(PermissionError, match="must match"):
            _validate_slug("bad name", "project")


# ---------------------------------------------------------------------------
# Slug-based path functions
# ---------------------------------------------------------------------------
class TestResolveUserPath:
    def test_basic_resolution(self, tmp_agoutic_data):
        path = resolve_user_path("alice", "myproject", "data/file.fastq")
        assert "users/alice/myproject/data/file.fastq" in str(path)

    def test_traversal_blocked(self, tmp_agoutic_data):
        with pytest.raises(PermissionError):
            resolve_user_path("alice", "myproject", "../../etc/passwd")

    def test_invalid_username(self, tmp_agoutic_data):
        with pytest.raises(PermissionError):
            resolve_user_path("Alice", "myproject", "file.txt")

    def test_invalid_project_slug(self, tmp_agoutic_data):
        with pytest.raises(PermissionError):
            resolve_user_path("alice", "../escape", "file.txt")


class TestGetUserProjectDir:
    def test_creates_directory(self, tmp_agoutic_data):
        d = get_user_project_dir("bob", "encode-run")
        assert d.exists()
        assert d.is_dir()
        assert "users/bob/encode-run" in str(d)

    def test_idempotent(self, tmp_agoutic_data):
        d1 = get_user_project_dir("bob", "encode-run")
        d2 = get_user_project_dir("bob", "encode-run")
        assert d1 == d2

    def test_invalid_slug(self, tmp_agoutic_data):
        with pytest.raises(PermissionError):
            get_user_project_dir("bob", "BAD")


class TestGetUserHomeDir:
    def test_creates_directory(self, tmp_agoutic_data):
        d = get_user_home_dir("carol")
        assert d.exists()
        assert d.is_dir()
        assert "users/carol" in str(d)

    def test_invalid_slug(self, tmp_agoutic_data):
        with pytest.raises(PermissionError):
            get_user_home_dir("Carol")


# ---------------------------------------------------------------------------
# Legacy UUID-based path functions
# ---------------------------------------------------------------------------
class TestResolveUserPathByUuid:
    def test_basic_resolution(self, tmp_agoutic_data):
        uid = "550e8400-e29b-41d4-a716-446655440000"
        pid = "project-uuid-123"
        path = resolve_user_path_by_uuid(uid, pid, "output/results.csv")
        assert uid in str(path)
        assert pid in str(path)

    def test_traversal_blocked(self, tmp_agoutic_data):
        with pytest.raises(PermissionError):
            resolve_user_path_by_uuid("user1", "proj1", "../../../etc/shadow")

    def test_empty_user_id(self, tmp_agoutic_data):
        with pytest.raises(PermissionError, match="must not be empty"):
            resolve_user_path_by_uuid("", "proj1", "file.txt")


class TestGetUserProjectDirByUuid:
    def test_creates_directory(self, tmp_agoutic_data):
        d = get_user_project_dir_by_uuid("uid1", "pid1")
        assert d.exists()
        assert d.is_dir()

    def test_dotdot_blocked(self, tmp_agoutic_data):
        with pytest.raises(PermissionError):
            get_user_project_dir_by_uuid("uid1", "..")


class TestGetUserHomeDirByUuid:
    def test_creates_directory(self, tmp_agoutic_data):
        d = get_user_home_dir_by_uuid("uid1")
        assert d.exists()
        assert "users/uid1" in str(d)


# ---------------------------------------------------------------------------
# Rename helpers
# ---------------------------------------------------------------------------
class TestRenameUserDir:
    def test_rename_existing_dir(self, tmp_agoutic_data):
        # Create old dir with content
        old_dir = get_user_home_dir("olduser")
        (old_dir / "project1").mkdir()
        (old_dir / "project1" / "file.txt").write_text("data")

        new_path = rename_user_dir("olduser", "newuser")
        assert new_path.exists()
        assert (new_path / "project1" / "file.txt").read_text() == "data"
        assert not old_dir.exists()

    def test_rename_nonexistent_creates_target(self, tmp_agoutic_data):
        # Old dir doesn't exist — should just create new one
        new_path = rename_user_dir("ghost", "newname")
        assert new_path.exists()

    def test_rename_to_occupied_dir_raises(self, tmp_agoutic_data):
        get_user_home_dir("user1")
        d2 = get_user_home_dir("user2")
        (d2 / "some-project").mkdir()
        (d2 / "some-project" / "file.txt").write_text("occupied")

        with pytest.raises(PermissionError, match="already has content"):
            rename_user_dir("user1", "user2")


class TestRenameProjectDir:
    def test_rename_existing_project(self, tmp_agoutic_data):
        old_dir = get_user_project_dir("alice", "oldproject")
        (old_dir / "data").mkdir()
        (old_dir / "data" / "sample.fastq").write_text("ACGT")

        new_path = rename_project_dir("alice", "oldproject", "newproject")
        assert new_path.exists()
        assert (new_path / "data" / "sample.fastq").read_text() == "ACGT"
        assert not old_dir.exists()

    def test_rename_nonexistent_creates_with_data_subdir(self, tmp_agoutic_data):
        # Source doesn't exist — creates new dir with data/ subdir
        new_path = rename_project_dir("alice", "nosuch", "freshproject")
        assert new_path.exists()
        assert (new_path / "data").exists()

    def test_rename_to_occupied_project_raises(self, tmp_agoutic_data):
        get_user_project_dir("alice", "proj1")
        d2 = get_user_project_dir("alice", "proj2")
        (d2 / "results.txt").write_text("data")

        with pytest.raises(PermissionError, match="already has content"):
            rename_project_dir("alice", "proj1", "proj2")
