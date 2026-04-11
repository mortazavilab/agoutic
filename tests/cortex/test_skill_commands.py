"""Tests for cortex.skill_commands.py — slash command parsing and execution."""

from cortex.skill_commands import (
    SkillCommand,
    detect_skill_intent,
    execute_skill_command,
    parse_skill_command,
    resolve_skill_key,
)


class TestParseSkillCommand:
    def test_parse_list_skills(self):
        cmd = parse_skill_command("/skills")

        assert cmd == SkillCommand(action="list")

    def test_parse_list_skills_alias(self):
        cmd = parse_skill_command("/list-skills")

        assert cmd == SkillCommand(action="list")

    def test_parse_describe_skill(self):
        cmd = parse_skill_command("/skill differential_expression")

        assert cmd == SkillCommand(action="describe", skill_ref="differential_expression")

    def test_parse_describe_skill_alias(self):
        cmd = parse_skill_command("/describe-skill ENCODE_Search")

        assert cmd == SkillCommand(action="describe", skill_ref="ENCODE_Search")

    def test_parse_use_skill(self):
        cmd = parse_skill_command("/use-skill analyze_local_sample")

        assert cmd == SkillCommand(action="use", skill_ref="analyze_local_sample")

    def test_parse_switch_skill_alias(self):
        cmd = parse_skill_command("/switch-skill IGVF_Search")

        assert cmd == SkillCommand(action="use", skill_ref="IGVF_Search")

    def test_non_skill_command_returns_none(self):
        assert parse_skill_command("/unknown stuff") is None


class TestResolveSkillKey:
    def test_resolves_case_insensitive_key(self):
        assert resolve_skill_key("encode_search") == "ENCODE_Search"

    def test_resolves_display_name(self):
        assert resolve_skill_key("Differential Expression") == "differential_expression"

    def test_returns_none_for_unknown_skill(self):
        assert resolve_skill_key("not_a_skill") is None


class TestDetectSkillIntent:
    def test_detect_list_skills_question(self):
        cmd = detect_skill_intent("what skills are available?")

        assert cmd == SkillCommand(action="list")

    def test_detect_show_available_skills(self):
        cmd = detect_skill_intent("show available skills")

        assert cmd == SkillCommand(action="list")

    def test_detect_describe_skill(self):
        cmd = detect_skill_intent("tell me about the differential expression skill")

        assert cmd == SkillCommand(action="describe", skill_ref="differential expression")

    def test_detect_what_does_skill_do(self):
        cmd = detect_skill_intent("what does ENCODE_Search skill do?")

        assert cmd == SkillCommand(action="describe", skill_ref="ENCODE_Search")

    def test_detect_switch_to_skill(self):
        cmd = detect_skill_intent("switch to the differential expression skill")

        assert cmd == SkillCommand(action="use", skill_ref="differential expression")

    def test_detect_set_active_skill(self):
        cmd = detect_skill_intent("set active skill to IGVF_Search")

        assert cmd == SkillCommand(action="use", skill_ref="IGVF_Search")

    def test_detect_skill_intent_ignores_slash_commands(self):
        assert detect_skill_intent("/skills") is None

    def test_detect_skill_intent_ignores_normal_task_requests(self):
        assert detect_skill_intent("run differential expression on this counts matrix") is None


class TestExecuteSkillCommand:
    def test_list_skills_includes_core_entries(self):
        markdown = execute_skill_command(SkillCommand(action="list"), active_skill="welcome")

        assert "Available skills" in markdown
        assert "differential_expression" in markdown
        assert "welcome" in markdown

    def test_list_skills_marks_active_skill(self):
        markdown = execute_skill_command(SkillCommand(action="list"), active_skill="IGVF_Search")

        assert "IGVF_Search" in markdown
        assert "current" in markdown.lower()

    def test_describe_skill_reports_manifest_metadata(self):
        markdown = execute_skill_command(
            SkillCommand(action="describe", skill_ref="differential_expression"),
            active_skill="welcome",
        )

        assert "Differential Expression" in markdown
        assert "edgepython" in markdown.lower()
        assert "run_de_pipeline" in markdown
        assert "/de" in markdown

    def test_describe_skill_unknown(self):
        markdown = execute_skill_command(
            SkillCommand(action="describe", skill_ref="missing_skill"),
            active_skill="welcome",
        )

        assert "Unknown skill" in markdown

    def test_use_skill_returns_new_active_skill(self):
        markdown = execute_skill_command(
            SkillCommand(action="use", skill_ref="differential_expression"),
            active_skill="welcome",
        )

        assert "Switched active skill" in markdown
        assert "differential_expression" in markdown

    def test_use_skill_rejects_unknown_skill(self):
        markdown = execute_skill_command(
            SkillCommand(action="use", skill_ref="missing_skill"),
            active_skill="welcome",
        )

        assert "Unknown skill" in markdown