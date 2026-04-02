"""Tests for cortex/memory_commands.py — slash command parsing and NL intent detection."""

import pytest

from cortex.memory_commands import (
    parse_memory_command,
    detect_memory_intent,
    MemoryCommand,
    MemoryIntent,
)


# ---------------------------------------------------------------------------
# Slash command parsing
# ---------------------------------------------------------------------------


class TestParseMemoryCommand:
    def test_remember(self):
        cmd = parse_memory_command("/remember sample1 is an AD sample")
        assert cmd is not None
        assert cmd.action == "remember"
        assert cmd.text == "sample1 is an AD sample"

    def test_remember_global(self):
        cmd = parse_memory_command("/remember-global always use hg38")
        assert cmd is not None
        assert cmd.action == "remember_global"
        assert cmd.text == "always use hg38"

    def test_remember_global_underscore(self):
        cmd = parse_memory_command("/remember_global always use hg38")
        assert cmd is not None
        assert cmd.action == "remember_global"

    def test_forget_by_text(self):
        cmd = parse_memory_command("/forget sample1 annotation")
        assert cmd is not None
        assert cmd.action == "forget"
        assert cmd.text == "sample1 annotation"
        assert cmd.memory_id == ""

    def test_forget_by_id(self):
        cmd = parse_memory_command("/forget #abc12345")
        assert cmd is not None
        assert cmd.action == "forget"
        assert cmd.memory_id == "abc12345"

    def test_memories_list(self):
        cmd = parse_memory_command("/memories")
        assert cmd is not None
        assert cmd.action == "list"
        assert not cmd.global_only

    def test_memories_global(self):
        cmd = parse_memory_command("/memories --global")
        assert cmd is not None
        assert cmd.action == "list"
        assert cmd.global_only

    def test_pin(self):
        cmd = parse_memory_command("/pin #abc123")
        assert cmd is not None
        assert cmd.action == "pin"
        assert cmd.memory_id == "abc123"

    def test_pin_without_hash(self):
        cmd = parse_memory_command("/pin abc123")
        assert cmd is not None
        assert cmd.memory_id == "abc123"

    def test_unpin(self):
        cmd = parse_memory_command("/unpin abc123")
        assert cmd is not None
        assert cmd.action == "unpin"

    def test_restore(self):
        cmd = parse_memory_command("/restore abc123")
        assert cmd is not None
        assert cmd.action == "restore"

    def test_annotate(self):
        cmd = parse_memory_command("/annotate sample1 condition=AD replicate=1")
        assert cmd is not None
        assert cmd.action == "annotate"
        assert cmd.sample_name == "sample1"
        assert cmd.annotations == {"condition": "AD", "replicate": "1"}

    def test_annotate_with_commas(self):
        cmd = parse_memory_command("/annotate K562 condition=treated, batch=2")
        assert cmd is not None
        assert cmd.annotations["condition"] == "treated"
        assert cmd.annotations["batch"] == "2"

    def test_search(self):
        cmd = parse_memory_command("/search-memories AD samples")
        assert cmd is not None
        assert cmd.action == "search"
        assert cmd.text == "AD samples"

    def test_not_a_command(self):
        cmd = parse_memory_command("This is a regular message")
        assert cmd is None

    def test_unknown_slash_command(self):
        cmd = parse_memory_command("/unknown stuff")
        assert cmd is None


# ---------------------------------------------------------------------------
# Natural language intent detection
# ---------------------------------------------------------------------------


class TestDetectMemoryIntent:
    def test_remember_that(self):
        intent = detect_memory_intent("remember that we should use hg38 next time")
        assert intent is not None
        assert intent.action == "remember"
        assert "we should use hg38 next time" in intent.text

    def test_note_that(self):
        intent = detect_memory_intent("note that we should use hg38 next time")
        assert intent is not None
        assert intent.action == "remember"

    def test_forget_the_memory(self):
        intent = detect_memory_intent("forget the memory about sample1")
        assert intent is not None
        assert intent.action == "forget"
        assert "sample1" in intent.text

    def test_sample_is_annotation(self):
        intent = detect_memory_intent("sample K562 is an AD sample")
        assert intent is not None
        assert intent.action == "annotate"
        assert intent.sample_name == "K562"
        assert intent.annotations.get("condition") == "AD sample"

    def test_sample_is_control(self):
        intent = detect_memory_intent("sample HepG2 is a control")
        assert intent is not None
        assert intent.action == "annotate"
        assert intent.sample_name == "HepG2"
        assert intent.annotations.get("condition") == "control"

    def test_mark_sample_as(self):
        intent = detect_memory_intent("mark sample K562 as treated")
        assert intent is not None
        assert intent.action == "annotate"
        assert intent.sample_name == "K562"
        assert intent.annotations.get("condition") == "treated"

    def test_what_do_you_remember(self):
        intent = detect_memory_intent("what do you remember about K562?")
        assert intent is not None
        assert intent.action == "search"
        assert "K562" in intent.text

    def test_no_intent(self):
        intent = detect_memory_intent("Run a DNA alignment on my sample")
        assert intent is None

    def test_slash_command_not_detected_as_intent(self):
        intent = detect_memory_intent("/remember test")
        assert intent is None
