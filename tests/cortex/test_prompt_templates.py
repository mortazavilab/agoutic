from pathlib import Path


def test_second_pass_template_mentions_prompt_coach_fallback():
    template_path = Path(__file__).resolve().parents[2] / "cortex" / "prompt_templates" / "second_pass_system_prompt.md"
    markdown = template_path.read_text(encoding="utf-8")

    assert "how to prompt you" in markdown.lower()
    assert "/help <topic>" in markdown
    assert "stage, run, and sync lifecycle" in markdown.lower()