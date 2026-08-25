from cortex.help_commands import (
    HelpCommand,
    detect_help_intent,
    execute_help_command,
    parse_help_command,
    render_slash_commands_markdown,
    resolve_help_topic,
)


class TestParseHelpCommand:
    def test_parse_help_overview(self):
        cmd = parse_help_command("/help")

        assert cmd == HelpCommand(action="overview")

    def test_parse_help_topic(self):
        cmd = parse_help_command("/help remote slurm stage")

        assert cmd == HelpCommand(action="topic", topic_ref="remote slurm stage")

    def test_non_help_slash_returns_none(self):
        assert parse_help_command("/commands") is None


class TestDetectHelpIntent:
    def test_detect_generic_help_phrase(self):
        cmd = detect_help_intent("help me!")

        assert cmd == HelpCommand(action="overview")

    def test_detect_prompt_coach_request(self):
        cmd = detect_help_intent("how do i prompt you to run dogme with a staged sample?")

        assert cmd == HelpCommand(action="topic", topic_ref="run dogme with a staged sample")

    def test_detect_slash_command_help(self):
        cmd = detect_help_intent("how do i use /list files?")

        assert cmd == HelpCommand(action="topic", topic_ref="/list files")

    def test_detect_skill_help(self):
        cmd = detect_help_intent("how do i use the remote execution skill?")

        assert cmd == HelpCommand(action="topic", topic_ref="remote execution skill")


class TestResolveHelpTopic:
    def test_resolve_remote_stage_topic(self):
        topic = resolve_help_topic("stage a sample on hpc3")

        assert topic.kind == "topic"
        assert topic.key == "remote-slurm-stage"

    def test_resolve_sync_topic(self):
        topic = resolve_help_topic("sync workflow12 back from the cluster")

        assert topic.kind == "topic"
        assert topic.key == "remote-slurm-sync"

    def test_resolve_command_topic(self):
        topic = resolve_help_topic("/list files")

        assert topic.kind == "command"
        assert topic.key == "list-files"

    def test_resolve_clean_slash_command(self):
        topic = resolve_help_topic("/clean")

        assert topic.kind == "command"
        assert topic.key == "clean"

    def test_resolve_skill_topic(self):
        topic = resolve_help_topic("remote execution skill")

        assert topic.kind == "skill"
        assert topic.key == "remote_execution"

    def test_resolve_haplotype_topic(self):
        topic = resolve_help_topic("how do i haplotype workflow7 with a vcf")

        assert topic.kind == "topic"
        assert topic.key == "haplotype-vcf"


class TestExecuteHelpCommand:
    def test_overview_mentions_remote_slurm_lifecycle(self):
        markdown = execute_help_command(HelpCommand(action="overview"), active_skill="welcome")

        assert "stage a sample on hpc3" in markdown.lower()
        assert "sync workflow12 back from the cluster" in markdown.lower()
        assert "/help remote slurm" in markdown

    def test_generic_help_phrase_renders_prompting_overview(self):
        markdown = execute_help_command(detect_help_intent("help me!"), active_skill="welcome")

        assert "Prompting AGOUTIC" in markdown
        assert "how do i stage a sample on hpc3" in markdown.lower()

    def test_remote_run_help_mentions_profile_resources_and_sync(self):
        markdown = execute_help_command(
            HelpCommand(action="topic", topic_ref="run dogme with a staged sample on slurm"),
            active_skill="welcome",
        )

        assert "Running Dogme On SLURM" in markdown
        assert "profile nickname" in markdown.lower()
        assert "sync-workflow" in markdown

    def test_list_files_help_mentions_project_and_workflow_scope(self):
        markdown = execute_help_command(
            HelpCommand(action="topic", topic_ref="/list files"),
            active_skill="welcome",
        )

        assert "/list files" in markdown
        assert "project root" in markdown.lower()
        assert "active workflow" in markdown.lower()

    def test_remote_execution_skill_help_uses_override_examples(self):
        markdown = execute_help_command(
            HelpCommand(action="topic", topic_ref="remote execution skill"),
            active_skill="welcome",
        )

        assert "Remote SLURM Execution Skill" in markdown
        assert "stage tumor-a on hpc3" in markdown.lower()
        assert "remote_execution" in markdown

    def test_skill_help_includes_instruction_highlights_and_routing_boundaries(self):
        markdown = execute_help_command(
            HelpCommand(action="topic", topic_ref="remote execution skill"),
            active_skill="welcome",
        )

        assert "Instruction highlights" in markdown
        assert "Execution mode selection (local vs. SLURM)" in markdown
        assert "When this skill hands off" in markdown
        assert "Analyzing job results" in markdown

    def test_local_sample_skill_help_uses_skill_doc_summary(self):
        markdown = execute_help_command(
            HelpCommand(action="topic", topic_ref="analyze local sample skill"),
            active_skill="welcome",
        )

        assert "Local Sample Intake skill" in markdown
        assert "intake wizard" in markdown.lower()
        assert "Sample types" in markdown

    def test_haplotype_command_help_mentions_auto_prepared_vcf_and_approval(self):
        markdown = execute_help_command(
            HelpCommand(action="topic", topic_ref="/haplotype"),
            active_skill="welcome",
        )

        assert "/haplotype" in markdown
        assert "vcf" in markdown.lower()
        assert "automatically" in markdown.lower()
        assert "approval" in markdown.lower()
        assert "mm39 founder vcf" in markdown.lower()
        assert "b6,cast" in markdown.lower()

    def test_haplotype_skill_help_uses_override_examples(self):
        markdown = execute_help_command(
            HelpCommand(action="topic", topic_ref="haplotype_with_vcf skill"),
            active_skill="welcome",
        )

        assert "Haplotype With VCF Skill" in markdown
        assert "workflow7" in markdown.lower()
        assert "/haplotype" in markdown
        assert "b6 cast f1" in markdown.lower()
        assert "mm39 founder vcf" in markdown.lower()
        assert "otherproject:workflow7" in markdown.lower()


class TestRenderSlashCommandsMarkdown:
    def test_slash_catalog_mentions_help_entry(self):
        markdown = render_slash_commands_markdown()

        assert "/help <topic>" in markdown
        assert "/commands" in markdown

    def test_slash_catalog_mentions_haplotype_command(self):
        markdown = render_slash_commands_markdown()

        assert "/haplotype <DNA|RNA|cDNA> <workflow> [vcf]" in markdown

    def test_slash_catalog_mentions_clean_command(self):
        markdown = render_slash_commands_markdown()

        assert "/clean [remote] [workflow[, workflow2, ...]]" in markdown