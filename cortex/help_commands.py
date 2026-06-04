from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from cortex.skill_commands import resolve_skill_key
from cortex.skill_manifest import SKILL_MANIFESTS, SkillManifest


@dataclass
class HelpCommand:
    action: Literal["overview", "topic"]
    topic_ref: str = ""
    error: str = ""


@dataclass(frozen=True)
class HelpTopic:
    title: str
    summary: str
    what_to_provide: tuple[str, ...] = ()
    example_prompts: tuple[str, ...] = ()
    slash_commands: tuple[str, ...] = ()
    related_skills: tuple[str, ...] = ()
    internal_steps: tuple[str, ...] = ()
    advanced_note: str = ""


@dataclass(frozen=True)
class ResolvedHelpTopic:
    kind: Literal["overview", "topic", "command", "skill"]
    key: str
    title: str


@dataclass(frozen=True)
class SkillDocHints:
    description: str = ""
    handles: tuple[str, ...] = ()
    boundaries: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()


_HELP_SLASH_RE = re.compile(r"^/help(?:\s+(.+))?$", re.IGNORECASE)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_ROOT = _REPO_ROOT / "skills"

_PROMPT_PATTERNS = (
    re.compile(
        r"^(?:please\s+)?(?:how\s+do\s+i|how\s+should\s+i|how\s+can\s+i)\s+"
        r"(?:prompt\s+you|ask\s+you|tell\s+you)\s+(?:to|for)\s+(.+?)[?.!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:please\s+)?what\s+should\s+i\s+say\s+(?:to\s+you\s+)?(?:to|for)\s+(.+?)[?.!]*$",
        re.IGNORECASE,
    ),
)
_HOW_DO_I_PATTERNS = (
    re.compile(r"^(?:please\s+)?(?:how\s+do\s+i|how\s+can\s+i|how\s+should\s+i)\s+use\s+(.+?)[?.!]*$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?(?:how\s+do\s+i|how\s+can\s+i|how\s+should\s+i)\s+(.+?)[?.!]*$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?explain\s+how\s+to\s+(.+?)[?.!]*$", re.IGNORECASE),
)
_GENERIC_HELP_REQUESTS = {
    "help",
    "help me",
    "can you help me",
    "please help",
    "please help me",
    "i need help",
    "i need some help",
}

_REMOTE_KEYWORDS = ("slurm", "cluster", "remote", "hpc", "profile", "ssh", "staged sample", "remote data")
_SYNC_KEYWORDS = ("sync", "copy results back", "copy back", "copy-back", "resume sync", "sync back")
_RUN_KEYWORDS = ("run", "submit", "launch")

_COMMAND_CATALOG: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Help",
        (
            ("/help", "show prompt-coach topics and example questions"),
            ("/help <topic>", "show step-by-step guidance for a skill, task, or slash command"),
            ("/commands", "list deterministic slash commands by category"),
        ),
    ),
    (
        "Skills",
        (
            ("/skills", "list available skills"),
            ("/skill <skill_key>", "describe a specific skill"),
            ("/use-skill <skill_key>", "switch the active skill manually"),
        ),
    ),
    (
        "Inventory",
        (
            ("/list samples", "list your local sample inventory"),
            ("/list staged [--profile NAME]", "list staged remote samples"),
            ("/list imported", "list imported workflows across projects"),
            ("/list dfs", "list dataframes currently in chat state"),
            ("/list workflows", "list tracked and on-disk workflows in the active project"),
            ("/list files [target] [--project] [--depth N]", "list files in the active workflow or project root"),
        ),
    ),
    (
        "Workflows",
        (
            ("/use <workflow>", "switch the active workflow context"),
            ("/reanalyze [workflow[, workflow2, ...]]", "rerun post-run analysis for one or more workflows"),
            ("/rerun [workflow[, workflow2, ...]]", "rerun one or more workflows"),
            ("/delete [workflow[, workflow2, ...]]", "delete one or more workflows"),
            ("/clean [remote] [workflow[, workflow2, ...]]", "gzip loose bedMethyl BEDs individually and remove work/dor* folders for one or more workflows"),
            ("/sync-workflow [workflow[, workflow2, ...]]", "retry or resume remote/imported result sync"),
            ("/cancel-sync <workflow[, workflow2, ...]>", "stop an active copy-back"),
            ("/rename <workflow> <new_name>", "rename a workflow"),
            ("/import-workflow <path> [--remote]", "import an existing workflow into the active project"),
            ("/list-launchpad-workflows", "list tracked Launchpad workflow rows"),
        ),
    ),
    (
        "Haplotyping",
        (
            ("/haplotype <DNA|RNA|cDNA> <workflow> [vcf]", "haplotype workflow BAMs against a VCF; mouse founder-panel requests can omit the VCF and use the default mm39 founder VCF"),
        ),
    ),
    (
        "Files",
        (
            ("/read-file <path> [--lines N] [--mode auto|plain|markdown|html_text|html_raw]", "open a project or workflow file"),
        ),
    ),
    (
        "Differential Expression",
        (
            ("/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2", "run a deterministic DE request from the current context"),
        ),
    ),
    (
        "Memory",
        (
            ("/memories", "list memories"),
            ("/remember <text>", "save a project-scoped note"),
            ("/remember-global <text>", "save a cross-project note"),
            ("/remember-df DF5 as <name>", "remember a dataframe"),
            ("/pin #<id>", "pin a memory"),
            ("/unpin #<id>", "unpin a memory"),
            ("/restore #<id>", "restore a deleted memory"),
            ("/annotate <sample> key=value", "save sample metadata"),
            ("/search-memories <query>", "search memories"),
            ("/upgrade-to-global #<id>", "promote a memory to global scope"),
        ),
    ),
)

_TOPIC_GUIDES: dict[str, HelpTopic] = {
    "overview": HelpTopic(
        title="Prompting AGOUTIC",
        summary=(
            "Ask for the task you want, the data or workflow it should use, and any execution context that matters. "
            "AGOUTIC can coach you on local Dogme runs, remote SLURM stage-run-sync flows, results analysis, files, dataframes, differential expression, skills, and slash commands."
        ),
        what_to_provide=(
            "Your goal, such as run, stage, sync, analyze, list, compare, or plot.",
            "The main object, such as a sample name, input path, workflow, dataframe, or slash command.",
            "Execution context when relevant, such as local vs SLURM, SSH profile nickname, result destination, or reference genome.",
        ),
        example_prompts=(
            "How do I prompt you to run Dogme DNA on /data/tumor-a with GRCh38?",
            "How do I stage a sample on hpc3?",
            "How do I sync workflow12 back from the cluster?",
            "How do I haplotype workflow7 with a VCF?",
            "How do I haplotype mouse workflow7 without typing the founder VCF path?",
            "How do I use /list files?",
        ),
        slash_commands=("/help", "/help remote slurm", "/help /haplotype", "/commands", "/skills"),
        related_skills=("analyze_local_sample", "remote_execution", "analyze_job_results", "differential_expression", "haplotype_with_vcf"),
        internal_steps=(
            "Resolve your question to a task, skill, or slash command topic.",
            "Tell you the shortest reliable prompt pattern and what details are worth including.",
            "Point you to deterministic slash commands when a shortcut exists.",
        ),
        advanced_note='Ask "show me the first-pass system prompt" if you want the internal planning prompt as an advanced reference.',
    ),
    "slash-commands": HelpTopic(
        title="Using Slash Commands",
        summary="Slash commands are deterministic shortcuts. They are best when you already know the action you want and want AGOUTIC to skip broad interpretation.",
        what_to_provide=(
            "The command family, such as /list, /read-file, /skill, /sync-workflow, or /de.",
            "Any required arguments like a workflow name, file path, profile, or topic.",
        ),
        example_prompts=(
            "/commands",
            "/help /list files",
            "/help /haplotype",
            "/list staged --profile hpc3",
            "/sync-workflow workflow12",
        ),
        slash_commands=("/help <topic>", "/commands", "/skills", "/list files", "/read-file", "/haplotype"),
        related_skills=("welcome",),
        internal_steps=(
            "Parse the slash command before LLM planning runs.",
            "Route to the matching deterministic handler for files, workflows, memory, skills, or inventory.",
            "Return a direct response or ask for missing command arguments when needed.",
        ),
        advanced_note='Use `/help <slash command>` such as `/help /sync-workflow` to get command-specific guidance.',
    ),
    "skills-overview": HelpTopic(
        title="Using Skills",
        summary="You usually do not need to switch skills manually, but AGOUTIC can tell you what skills exist, describe a specific one, or let you pin one as the active skill.",
        what_to_provide=(
            "The skill name or the kind of task you want help with.",
            "Whether you want to browse all skills, inspect one skill, or switch the active skill.",
        ),
        example_prompts=(
            "What skills are available?",
            "Tell me about the remote execution skill.",
            "How do I use the differential expression skill?",
            "Use the IGVF Search skill.",
        ),
        slash_commands=("/skills", "/skill <skill_key>", "/use-skill <skill_key>"),
        related_skills=("welcome",),
        internal_steps=(
            "Look up skill manifests to describe the capability, inputs, outputs, and related commands.",
            "Keep the active skill if you only want help; switch it only when you explicitly ask.",
        ),
    ),
    "local-dogme": HelpTopic(
        title="Prompting A Local Dogme Run",
        summary="For a local Dogme run, say what kind of data you have, where it lives, and any analysis settings AGOUTIC should not infer automatically.",
        what_to_provide=(
            "Input path or sample folder.",
            "Mode or sample type when it is not obvious, such as DNA, RNA, cDNA, or Fiber-seq.",
            "Reference genome and any important execution constraints.",
        ),
        example_prompts=(
            "Run Dogme DNA on /data/tumor-a with GRCh38.",
            "Analyze RNA sample tumor-b from /data/tumor-b using mm39.",
            "How do I prompt you for a local Dogme run on POD5 data?",
        ),
        slash_commands=("/help local dogme",),
        related_skills=("analyze_local_sample", "run_dogme_dna", "run_dogme_rna", "run_dogme_cdna"),
        internal_steps=(
            "Inspect the sample path and infer file type where possible.",
            "Ask only for missing execution details before building an approval.",
            "Submit the job locally through Launchpad and monitor progress.",
        ),
    ),
    "remote-slurm": HelpTopic(
        title="Remote SLURM Stage, Run, And Sync",
        summary="Remote SLURM work usually has three pieces: stage data and references onto the cluster, submit the run with a profile and scheduler resources, then sync results back if you want local copies.",
        what_to_provide=(
            "An SSH profile or cluster nickname such as hpc3.",
            "The sample name or input path, plus mode and reference when AGOUTIC cannot infer them.",
            "Whether you want stage-only, a full run, or a sync or resume-sync action.",
        ),
        example_prompts=(
            "How do I stage, run, and sync jobs on SLURM?",
            "Stage tumor-a on hpc3, then run Dogme DNA with GRCh38 and sync results locally.",
            "How do I copy results back from the cluster after a remote run?",
        ),
        slash_commands=("/help remote slurm stage", "/help remote slurm run", "/help remote slurm sync", "/sync-workflow <workflow>"),
        related_skills=("remote_execution",),
        internal_steps=(
            "Look up saved SSH profiles and SLURM defaults before asking extra questions.",
            "Validate the connection, stage data if needed, collect approval for remote resources, and submit the run.",
            "Track scheduler state and start or resume copy-back when you ask to sync results.",
        ),
    ),
    "remote-slurm-stage": HelpTopic(
        title="Staging A Sample On SLURM",
        summary="Use a stage-only request when you want AGOUTIC to prepare remote data and references on the cluster without launching the workflow yet.",
        what_to_provide=(
            "The sample name or the input path to stage.",
            "The SSH profile or cluster nickname.",
            "Mode and reference genome if they are not already known from context or saved metadata.",
        ),
        example_prompts=(
            "Stage tumor-a on hpc3.",
            "Stage Jamshid on localCluster from /data/Jamshid without running it yet.",
            "How do I prompt you for stage-only remote sample preparation?",
        ),
        slash_commands=("/help remote slurm",),
        related_skills=("remote_execution",),
        internal_steps=(
            "List SSH profiles and load saved SLURM defaults for the chosen profile.",
            "Validate the SSH connection and prepare remote directories.",
            "Stage input data and references remotely without submitting a Dogme run.",
        ),
    ),
    "remote-slurm-run": HelpTopic(
        title="Running Dogme On SLURM",
        summary="For a remote SLURM run, state the cluster profile, the input source, and any scheduler settings AGOUTIC should confirm before approval.",
        what_to_provide=(
            "A profile nickname such as hpc3 or localCluster.",
            "Either a local sample path, a staged sample name, or a remote input directory already on the cluster.",
            "Mode, reference genome, and any CPU, memory, partition, account, walltime, or GPU constraints you care about.",
            "Result destination if you want results synced locally, kept remote, or both.",
        ),
        example_prompts=(
            "Run Dogme DNA on hpc3 using staged sample tumor-a with GRCh38.",
            "Run Jamshid on localCluster using remote data at /crsp/lab/share/pod5/Jamshid and sync results locally.",
            "How do I prompt you to run Dogme with a staged sample on SLURM?",
        ),
        slash_commands=("/help remote slurm", "/sync-workflow <workflow>"),
        related_skills=("remote_execution",),
        internal_steps=(
            "Reuse staged data when possible and ask only for missing resource or profile details.",
            "Show a remote submission summary for explicit approval before submitting the SLURM job.",
            "Monitor scheduler state and explain pending or failure reasons when they appear.",
        ),
    ),
    "remote-slurm-sync": HelpTopic(
        title="Syncing Remote Workflow Results",
        summary="Use sync or resume-sync requests when a remote or imported workflow already exists and you want AGOUTIC to copy results back into the active project.",
        what_to_provide=(
            "The workflow name, workflow number, or imported workflow you want to sync.",
            "A cluster or profile hint if multiple remote contexts could match.",
            "Whether you want to retry, resume, or cancel an active sync.",
        ),
        example_prompts=(
            "Sync workflow12 back from the cluster.",
            "Resume syncing workflow7 from hpc3.",
            "How do I copy results back from the cluster after a remote run?",
        ),
        slash_commands=("/sync-workflow <workflow>", "/cancel-sync <workflow>"),
        related_skills=("remote_execution",),
        internal_steps=(
            "Find the matching remote or imported workflow and check whether sync is already active.",
            "Start or resume copy-back asynchronously and keep reporting sync status.",
            "Let you cancel an active copy-back without re-importing the workflow later.",
        ),
    ),
    "workflow-import": HelpTopic(
        title="Importing And Syncing Existing Workflows",
        summary="Import a finished workflow when the run already exists locally or on a cluster and you want it tracked as the next workflow in the active project.",
        what_to_provide=(
            "The source workflow path.",
            "Whether the source is remote.",
            "Any sample or mode overrides if the workflow metadata is incomplete.",
        ),
        example_prompts=(
            "Import workflow /scratch/me/agoutic/project-alpha/workflow12 --remote.",
            "How do I import an existing remote Dogme workflow?",
            "How do I retry syncing an imported workflow?",
        ),
        slash_commands=("/import-workflow <path> [--remote]", "/sync-workflow <workflow>", "/cancel-sync <workflow>"),
        related_skills=("remote_execution",),
        internal_steps=(
            "Inspect the workflow metadata and allocate the next workflow folder in the active project.",
            "Track any remote copy-back separately so it can be retried or cancelled later.",
        ),
    ),
    "results-analysis": HelpTopic(
        title="Analyzing Completed Workflows",
        summary="When you want AGOUTIC to interpret outputs, mention the workflow or result file you care about and the question you want answered.",
        what_to_provide=(
            "A workflow name, workflow number, or the file you want opened.",
            "The kind of result you want, such as QC, alignment, methylation, counts, or a specific report.",
        ),
        example_prompts=(
            "Analyze results for workflow7.",
            "Read reconciled_summary.txt from workflow10.",
            "What was the alignment rate for the current workflow?",
        ),
        slash_commands=("/read-file <path>", "/reanalyze [workflow]", "/list files"),
        related_skills=("analyze_job_results",),
        internal_steps=(
            "Resolve the workflow context and browse or read relevant files through Analyzer tools.",
            "Summarize the outputs without repeating full raw tables already shown in the UI.",
        ),
    ),
    "differential-expression": HelpTopic(
        title="Prompting Differential Expression",
        summary="For DE, tell AGOUTIC which samples belong to each group and where the abundance or counts data should come from.",
        what_to_provide=(
            "The treated and control groups or the named contrasts you want compared.",
            "The source dataframe, workflow, or abundance table when AGOUTIC cannot infer it from context.",
            "Any gene-level or transcript-level preference.",
        ),
        example_prompts=(
            "Compare treated_1 and treated_2 to ctrl_1 and ctrl_2 from the current workflow abundance table.",
            "/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2",
            "How do I prompt you for DE from DF3 at transcript level?",
        ),
        slash_commands=("/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2", "/list dfs"),
        related_skills=("differential_expression",),
        internal_steps=(
            "Find or confirm the counts source, validate the group labels, and ask only for missing contrasts.",
            "Run the deterministic DE pipeline or build the required plan from the current context.",
        ),
    ),
    "haplotype-vcf": HelpTopic(
        title="Haplotyping Workflow BAMs With A VCF",
        summary="Use haplotype prompts when you want AGOUTIC to label long-read DNA, RNA, or cDNA workflow BAMs against a VCF and write haplotyped BAM outputs into a new workflow. Plain .vcf inputs and missing VCF indexes are prepared automatically when possible, and mouse/mm39 founder-panel requests can omit the VCF to use the default founder VCF beside the configured mm39 reference.",
        what_to_provide=(
            "The assay mode: DNA, RNA, or cDNA.",
            "A workflow name in the active project, a cross-project workflow reference like `otherproject:workflow7`, or explicit BAM context.",
            "A VCF path, unless the request is a mouse/mm39 founder-panel run that should use the default mm39 founder VCF. AGOUTIC can bgzip-compress plain .vcf inputs and build a .tbi or .csi index automatically when the source directory is writable.",
            "Optional VCF sample selection, founder pair restriction, label overrides for legacy one- or two-sample mode, or BAM-name narrowing when one workflow contains multiple BAMs.",
        ),
        example_prompts=(
            "/haplotype RNA workflow7 /data/parents.vcf.gz",
            "haplotype RNA workflow7 with file /data/parents.vcf.gz",
            "/haplotype RNA workflow7 --vcf-sample B6,CAST",
            "haplotype mouse sample B6 Cast F1 workflow7",
            "haplotype mouse between B6 and CAST workflow7",
            "haplotype B6CASTF1 RNA mouse sample otherproject:workflow7",
            "/haplotype DNA workflow5 /data/family.vcf.gz",
        ),
        slash_commands=("/haplotype <DNA|RNA|cDNA> <workflow> [vcf]", "/help /haplotype", "/list files"),
        related_skills=("haplotype_with_vcf", "analyze_job_results"),
        internal_steps=(
            "Locate eligible BAMs from the named workflow using mode-specific discovery rules.",
            "Run a preflight check to validate BAM indexes, auto-prepare VCF compression or indexing when needed, resolve the default mm39 founder VCF for omitted-VCF mouse founder requests, and confirm selectable VCF samples.",
            "Show an approval gate with the exact BAM names, selected founders or samples, labels, thresholds, resolved VCF path, and destination workflow before execution.",
        ),
        advanced_note="RNA and cDNA workflows use annotated BAMs from annot/, DNA workflows use mapped BAMs from bams/, reconcile outputs are treated as RNA-only annotated BAMs, and mouse founder aliases are case-insensitive while ignoring `/`, `_`, `-`, and spaces (`ref`, `B6`, `C57BL6`, and `C57BL6/J` all resolve to `C57BL_6J`).",
    ),
    "dataframes-plots": HelpTopic(
        title="Working With DataFrames And Plots",
        summary="When you want dataframe help, name the dataframe or workflow context and describe the transform, summary, or plot you want.",
        what_to_provide=(
            "A dataframe reference such as DF3 or a request to list available dataframes first.",
            "The operation you want, such as filter, rename, summarize, join, or plot.",
            "Any columns, groups, or chart types that matter.",
        ),
        example_prompts=(
            "List dfs.",
            "Head DF5 20.",
            "Plot DF3 by sample and color by condition.",
            "How do I prompt you to summarize a dataframe by sample and sum reads?",
        ),
        slash_commands=("/list dfs",),
        related_skills=("analyze_job_results", "differential_expression"),
        internal_steps=(
            "Find the dataframe in conversation state and apply an in-memory transform or plotting tag.",
            "Reuse the active workflow context when the dataframe came from a recent results step.",
        ),
    ),
}

_COMMAND_GUIDES: dict[str, HelpTopic] = {
    "help": HelpTopic(
        title="/help",
        summary="Use /help by itself for an overview, or add a topic to get prompt recipes and command guidance for a task, skill, or slash command.",
        what_to_provide=("An optional topic such as remote slurm, /list files, differential expression, or a skill name.",),
        example_prompts=("/help", "/help remote slurm", "/help /list files", "/help remote_execution"),
        slash_commands=("/help", "/help <topic>", "/commands"),
        related_skills=("welcome",),
        internal_steps=("Resolve the topic and return curated prompting guidance before normal LLM planning.",),
        advanced_note='You can still ask for "show me the first-pass system prompt" if you want the internal prompt directly.',
    ),
    "commands": HelpTopic(
        title="/commands",
        summary="Use /commands when you want the full slash-command catalog grouped by category instead of task-specific coaching.",
        what_to_provide=("No arguments are required.",),
        example_prompts=("/commands", "Show slash commands"),
        slash_commands=("/commands", "/help /commands"),
        related_skills=("welcome",),
        internal_steps=("Render the deterministic slash-command catalog without calling the LLM.",),
    ),
    "skills": HelpTopic(
        title="/skills",
        summary="Use /skills when you want a manifest-backed list of the registered skills and which one is currently active.",
        what_to_provide=("No arguments are required.",),
        example_prompts=("/skills", "What skills are available?"),
        slash_commands=("/skills", "/skill <skill_key>", "/use-skill <skill_key>"),
        related_skills=("welcome",),
        internal_steps=("Load the skill manifest registry and render the available capabilities.",),
    ),
    "skill": HelpTopic(
        title="/skill <skill_key>",
        summary="Use /skill when you want a manifest-backed description of one skill, including its category, source, inputs, and related slash commands.",
        what_to_provide=("The skill key or display name.",),
        example_prompts=("/skill remote_execution", "/skill differential_expression"),
        slash_commands=("/skill <skill_key>", "/skills"),
        related_skills=("welcome",),
        internal_steps=("Resolve the skill key against the skill manifest registry and render its metadata.",),
    ),
    "use-skill": HelpTopic(
        title="/use-skill <skill_key>",
        summary="Use /use-skill when you want to pin a specific skill as the active one for later turns.",
        what_to_provide=("The skill key or display name.",),
        example_prompts=("/use-skill remote_execution", "/use-skill differential_expression"),
        slash_commands=("/use-skill <skill_key>", "/skills"),
        related_skills=("welcome",),
        internal_steps=("Resolve the requested skill and store it as the active skill for subsequent turns.",),
    ),
    "list-files": HelpTopic(
        title="/list files",
        summary="Use /list files to inspect the active workflow directory by default, or add --project to browse from the project root.",
        what_to_provide=(
            "An optional target folder such as annot or workflow7/annot.",
            "Whether you want project-root scope with --project.",
            "An optional depth value if you need more than one level of listing.",
        ),
        example_prompts=(
            "/list files",
            "/list files annot --depth 2",
            "/list files workflow7/annot",
            "/list files results --project",
        ),
        slash_commands=("/list files [target] [--project] [--depth N]", "/read-file <path>"),
        related_skills=("analyze_job_results",),
        internal_steps=(
            "Resolve the active workflow or project directory from context.",
            "Call the analyzer file lister and render a compact inventory table.",
        ),
    ),
    "list-staged": HelpTopic(
        title="/list staged",
        summary="Use /list staged to review the staged remote samples AGOUTIC already knows about, optionally filtered to one profile.",
        what_to_provide=("An optional profile nickname with --profile.",),
        example_prompts=("/list staged", "/list staged --profile hpc3"),
        slash_commands=("/list staged [--profile NAME]", "/help remote slurm stage"),
        related_skills=("remote_execution",),
        internal_steps=("Query the staged-remote-sample inventory and optionally filter it to one profile.",),
    ),
    "list-imported": HelpTopic(
        title="/list imported",
        summary="Use /list imported to see imported workflows across projects you can access.",
        what_to_provide=("No arguments are required.",),
        example_prompts=("/list imported", "What imported samples do I have?"),
        slash_commands=("/list imported", "/sync-workflow <workflow>"),
        related_skills=("remote_execution",),
        internal_steps=("Query imported workflow rows across accessible projects and render a compact table.",),
    ),
    "clean": HelpTopic(
        title="/clean",
        summary="Use /clean to preserve each workflow folder while gzipping loose `bedMethyl/*.bed` files one by one and removing `work/` plus immediate-child `dor*` directories.",
        what_to_provide=(
            "One or more workflow references, or use `workflows` to target all tracked and untracked workflow folders in the active project.",
            "Add `remote` immediately after `/clean` when you want the cleanup to run on the remote workflow directory instead of locally.",
        ),
        example_prompts=("/clean workflow12", "/clean workflow7 workflow8", "/clean remote workflow12", "/clean workflows"),
        slash_commands=("/clean [remote] [workflow[, workflow2, ...]]", "/help /clean", "/list workflows"),
        related_skills=("remote_execution", "analyze_job_results"),
        internal_steps=(
            "Resolve the workflow references against tracked jobs and, for `workflows`, any untracked immediate-child `workflow*` folders in the current project.",
            "Gzip each loose `bedMethyl/*.bed` file individually, then remove `work/` and immediate-child `dor*` directories while keeping the workflow root and `bedMethyl/`.",
            "For `remote` cleanup, require a remote-capable workflow and return a clear message if remote metadata is unavailable.",
        ),
    ),
    "sync-workflow": HelpTopic(
        title="/sync-workflow",
        summary="Use /sync-workflow to retry or continue syncing results for a remote or imported workflow that is already known to AGOUTIC.",
        what_to_provide=("The workflow name or workflow number to sync.",),
        example_prompts=("/sync-workflow workflow12", "/sync-workflow workflow7, workflow8"),
        slash_commands=("/sync-workflow [workflow[, workflow2, ...]]", "/cancel-sync <workflow>"),
        related_skills=("remote_execution",),
        internal_steps=("Resolve the workflow identity, start or resume copy-back, and report ongoing sync state.",),
    ),
    "read-file": HelpTopic(
        title="/read-file",
        summary="Use /read-file when you know the report or output file you want to inspect and want AGOUTIC to render it directly.",
        what_to_provide=("A path relative to the active workflow or project context.", "Optional line limit and render mode."),
        example_prompts=("/read-file reconciled_summary.txt", "/read-file annot/report.html --mode html_text"),
        slash_commands=("/read-file <path> [--lines N] [--mode auto|plain|markdown|html_text|html_raw]", "/list files"),
        related_skills=("analyze_job_results",),
        internal_steps=("Resolve the file path within the workflow or project context and render the requested content mode.",),
    ),
    "de": HelpTopic(
        title="/de",
        summary="Use /de when you already know the treated and control sample groups and want a concise deterministic DE request.",
        what_to_provide=("The treated and control groups.", "The data source context if it is not the active workflow abundance table."),
        example_prompts=("/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2",),
        slash_commands=("/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2", "/list dfs"),
        related_skills=("differential_expression",),
        internal_steps=("Validate the supplied groups and route to the DE planning or execution flow.",),
    ),
    "haplotype": HelpTopic(
        title="/haplotype",
        summary="Use /haplotype when you already know the assay mode and workflow you want AGOUTIC to use for haplotyping. Plain .vcf inputs and missing VCF indexes are prepared automatically when possible, and mouse founder-panel requests can omit the VCF when they should use the default mm39 founder VCF.",
        what_to_provide=(
            "The assay mode: DNA, RNA, or cDNA.",
            "The workflow identity whose BAMs should be haplotyped, including cross-project references like `otherproject:workflow7`.",
            "The VCF path, unless the request is a mouse/mm39 founder-panel run that should use the default mm39 founder VCF.",
        ),
        example_prompts=(
            "/haplotype RNA workflow7 /data/parents.vcf.gz",
            "/haplotype RNA workflow7 --vcf-sample B6,CAST",
            "/haplotype RNA otherproject:workflow7 /data/parents.vcf.gz",
            "/haplotype DNA workflow5 /data/family.vcf.gz",
        ),
        slash_commands=("/haplotype <DNA|RNA|cDNA> <workflow> [vcf]", "/help /haplotype"),
        related_skills=("haplotype_with_vcf",),
        internal_steps=(
            "Parse the assay mode, workflow, VCF if present, and any founder restrictions from the command.",
            "Run preflight before approval and only execute after the approval gate is accepted.",
        ),
    ),
}

_SKILL_HELP_OVERRIDES: dict[str, HelpTopic] = {
    "remote_execution": HelpTopic(
        title="Remote SLURM Execution Skill",
        summary="Use this skill when the task is about staging data remotely, submitting Dogme to SLURM, monitoring scheduler state, or syncing remote results back.",
        what_to_provide=(
            "An SSH profile or cluster nickname.",
            "Sample or workflow identity plus any remote input path or staged sample name.",
            "Scheduler constraints and result destination when they matter.",
        ),
        example_prompts=(
            "How do I use the remote execution skill to stage tumor-a on hpc3?",
            "Use the remote execution skill to run Dogme DNA on localCluster with remote data at /remote/pod5/Jamshid.",
            "How do I use the remote execution skill to sync workflow12?",
        ),
        slash_commands=("/help remote slurm", "/sync-workflow <workflow>", "/cancel-sync <workflow>"),
        related_skills=("remote_execution",),
        internal_steps=(
            "Resolve the remote profile and any saved SLURM defaults before asking follow-ups.",
            "Handle stage-only, remote submission, status, and sync tasks through Launchpad's remote execution tools.",
        ),
    ),
    "analyze_local_sample": HelpTopic(
        title="Analyze Local Sample Skill",
        summary="Use this skill for local Dogme runs from sample paths on the AGOUTIC host.",
        what_to_provide=("A sample path or folder.", "Mode and reference when AGOUTIC cannot infer them."),
        example_prompts=(
            "How do I use the analyze local sample skill for a DNA POD5 run?",
            "Use the analyze local sample skill on /data/tumor-a with GRCh38.",
        ),
        slash_commands=("/help local dogme",),
        related_skills=("analyze_local_sample",),
        internal_steps=("Inspect the local sample, gather missing metadata, and build a local execution approval.",),
    ),
    "analyze_job_results": HelpTopic(
        title="Analyze Job Results Skill",
        summary="Use this skill to inspect completed workflow outputs, open reports, and summarize QC or downstream findings across Dogme, reconcile, haplotype, differential-expression, and wf-pore-c workflows.",
        what_to_provide=("A workflow or file to inspect.", "The kind of result you care about."),
        example_prompts=(
            "How do I use the analyze job results skill for workflow7?",
            "Use the analyze job results skill to summarize the QC report for workflow10.",
            "Use the analyze job results skill to summarize haplotype outputs for workflow12.",
            "Use the analyze job results skill to summarize DE outputs for workflow16.",
        ),
        slash_commands=("/read-file <path>", "/list files", "/reanalyze [workflow]"),
        related_skills=("analyze_job_results",),
        internal_steps=("Resolve workflow context, branch by workflow family, browse files, and summarize outputs from Analyzer tools.",),
    ),
    "differential_expression": HelpTopic(
        title="Differential Expression Skill",
        summary="Use this skill to compare named groups from workflow abundance tables or saved dataframes.",
        what_to_provide=("Treated and control groups.", "Workflow or dataframe source.", "Gene vs transcript preference if needed."),
        example_prompts=(
            "How do I use the differential expression skill from the current workflow abundance table?",
            "Use the differential expression skill on DF3 at transcript level comparing treated_1 to ctrl_1.",
        ),
        slash_commands=("/de treated=treated_1,treated_2 vs control=ctrl_1,ctrl_2", "/list dfs"),
        related_skills=("differential_expression",),
        internal_steps=("Resolve the data source, validate group membership, and route to the DE analysis flow.",),
    ),
    "haplotype_with_vcf": HelpTopic(
        title="Haplotype With VCF Skill",
        summary="Use this skill to haplotype long-read DNA, RNA, or cDNA workflow BAMs against a VCF with an approval-gated preflight, automatic compression/index preparation when needed, workflow-aware BAM discovery, and mouse founder-panel routing that can default the mm39 founder VCF when you omit it.",
        what_to_provide=(
            "The assay mode and workflow, including cross-project workflow references like `otherproject:workflow7`, plus a VCF path unless the request should use the default mm39 founder VCF.",
            "Optional BAM-name narrowing, founder pair restriction, or VCF sample selection when AGOUTIC should not auto-pick them.",
        ),
        example_prompts=(
            "How do I use the haplotype_with_vcf skill for workflow7?",
            "Use the haplotype_with_vcf skill to haplotype RNA workflow7 with /data/parents.vcf.gz.",
            "Use the haplotype_with_vcf skill to haplotype mouse sample B6 Cast F1 workflow7.",
            "Use the haplotype_with_vcf skill to haplotype RNA otherproject:workflow7 with /data/parents.vcf.gz.",
        ),
        slash_commands=("/haplotype <DNA|RNA|cDNA> <workflow> [vcf]", "/help /haplotype"),
        related_skills=("haplotype_with_vcf", "analyze_job_results"),
        internal_steps=(
            "Resolve workflow BAMs by mode, auto-prepare VCF compression or indexing when needed, resolve founder aliases or the default mm39 founder VCF when applicable, validate selectable samples, then build the approval gate.",
            "Run the allowlisted haplotype script locally and expose live per-BAM and per-chromosome status during execution.",
        ),
    ),
}


def parse_help_command(message: str) -> HelpCommand | None:
    msg = str(message or "").strip()
    if not msg.startswith("/"):
        return None

    match = _HELP_SLASH_RE.match(msg)
    if not match:
        return None

    topic_ref = str(match.group(1) or "").strip()
    if not topic_ref:
        return HelpCommand(action="overview")
    return HelpCommand(action="topic", topic_ref=topic_ref)


def detect_help_intent(message: str) -> HelpCommand | None:
    msg = str(message or "").strip()
    if not msg or msg.startswith("/"):
        return None

    normalized = _normalize_help_text(msg)
    if normalized in _GENERIC_HELP_REQUESTS:
        return HelpCommand(action="overview")

    for pattern in _PROMPT_PATTERNS + _HOW_DO_I_PATTERNS:
        match = pattern.match(msg)
        if match:
            topic_ref = _cleanup_topic_ref(match.group(1))
            if topic_ref:
                return HelpCommand(action="topic", topic_ref=topic_ref)
            return HelpCommand(action="overview")

    return None


def resolve_help_topic(topic_ref: str) -> ResolvedHelpTopic:
    raw_topic = str(topic_ref or "").strip()
    if not raw_topic:
        topic = _TOPIC_GUIDES["overview"]
        return ResolvedHelpTopic(kind="overview", key="overview", title=topic.title)

    command_key = _resolve_command_key(raw_topic)
    if command_key:
        topic = _COMMAND_GUIDES[command_key]
        return ResolvedHelpTopic(kind="command", key=command_key, title=topic.title)

    skill_key = _resolve_skill_topic(raw_topic)
    if skill_key:
        manifest = SKILL_MANIFESTS[skill_key]
        return ResolvedHelpTopic(
            kind="skill",
            key=skill_key,
            title=f"{manifest.display_name or skill_key} skill",
        )

    normalized = _normalize_help_text(raw_topic)
    if all(token in normalized for token in ("stage", "run", "sync")) and _contains_any(normalized, _REMOTE_KEYWORDS):
        return _resolved_topic("remote-slurm")
    if "slash command" in normalized:
        return _resolved_topic("slash-commands")
    if _contains_any(normalized, ("available skills", "what skills", "which skills", "use skills", "skill list")):
        return _resolved_topic("skills-overview")
    if _looks_like_stage_request(normalized) and (_contains_any(normalized, _REMOTE_KEYWORDS) or "staged sample" in normalized):
        return _resolved_topic("remote-slurm-stage")
    if _contains_any(normalized, _SYNC_KEYWORDS) and (_contains_any(normalized, _REMOTE_KEYWORDS) or "workflow" in normalized):
        return _resolved_topic("remote-slurm-sync")
    if _contains_any(normalized, _RUN_KEYWORDS) and (_contains_any(normalized, _REMOTE_KEYWORDS) or "staged sample" in normalized):
        return _resolved_topic("remote-slurm-run")
    if _contains_any(normalized, _REMOTE_KEYWORDS):
        return _resolved_topic("remote-slurm")
    if _contains_any(normalized, ("import workflow", "workflow import", "imported workflow")):
        return _resolved_topic("workflow-import")
    if _contains_any(normalized, ("haplotype", "haplotyping", "vcf", "genotype assignment")):
        return _resolved_topic("haplotype-vcf")
    if _contains_any(normalized, ("differential expression", "compare treated", "compare control", "de analysis")):
        return _resolved_topic("differential-expression")
    if _contains_any(normalized, ("dataframe", "dataframes", "plot", "plotting", "df ", " df")):
        return _resolved_topic("dataframes-plots")
    if _contains_any(normalized, ("results", "result analysis", "analyze results", "workflow output", "qc report", "read file", "read report")):
        return _resolved_topic("results-analysis")
    if _contains_any(normalized, ("dogme", "local sample", "run workflow", "pod5", "bam", "fastq")):
        return _resolved_topic("local-dogme")

    return _resolved_topic("overview")


def execute_help_command(command: HelpCommand, *, active_skill: str = "welcome") -> str:
    if command.error:
        return command.error

    if command.action == "overview":
        return _render_help_topic(_TOPIC_GUIDES["overview"])

    resolved = resolve_help_topic(command.topic_ref)
    if resolved.kind == "command":
        return _render_help_topic(_COMMAND_GUIDES[resolved.key])
    if resolved.kind == "skill":
        return _render_skill_help(SKILL_MANIFESTS[resolved.key], active_skill=active_skill)
    return _render_help_topic(_TOPIC_GUIDES[resolved.key])


def render_slash_commands_markdown() -> str:
    lines = [
        "### Slash Commands",
        "",
        "Use `/help <topic>` for task-specific prompting guidance and `/commands` any time to reopen this catalog.",
    ]
    for section, items in _COMMAND_CATALOG:
        lines.extend(["", f"**{section}**"])
        for command, description in items:
            lines.append(f"- `{command}` — {description}")
    return "\n".join(lines)


def _render_help_topic(topic: HelpTopic) -> str:
    lines = [f"### Help: {topic.title}", "", topic.summary]
    if topic.what_to_provide:
        lines.extend(["", "**What to provide**"])
        lines.extend(f"- {item}" for item in topic.what_to_provide)
    if topic.example_prompts:
        lines.extend(["", "**Example prompts**"])
        lines.extend(f"- `{item}`" for item in topic.example_prompts)
    if topic.slash_commands:
        lines.extend(["", "**Useful slash commands**"])
        lines.extend(f"- `{item}`" for item in topic.slash_commands)
    if topic.related_skills:
        lines.extend(["", "**Related skills**"])
        lines.extend(f"- `{item}`" for item in topic.related_skills)
    if topic.internal_steps:
        lines.extend(["", "**What AGOUTIC will do**"])
        lines.extend(f"- {item}" for item in topic.internal_steps)
    if topic.advanced_note:
        lines.extend(["", "**Advanced**", f"- {topic.advanced_note}"])
    return "\n".join(lines)


def _render_skill_help(manifest: SkillManifest, *, active_skill: str) -> str:
    override = _SKILL_HELP_OVERRIDES.get(manifest.key)
    doc_hints = _load_skill_doc_hints(manifest.key, manifest.skill_file)
    title = override.title if override else f"{manifest.display_name or manifest.key} skill"
    summary = override.summary if override else (doc_hints.description or manifest.description or "No description available.")
    what_to_provide = override.what_to_provide if override else _default_skill_inputs(manifest)
    example_prompts = _merge_unique(
        override.example_prompts if override else (),
        doc_hints.examples,
        _default_skill_examples(manifest),
        limit=5,
    )
    slash_commands = _merge_unique(
        override.slash_commands if override else (),
        manifest.slash_commands,
        _default_skill_commands(manifest),
    )
    related_skills = _merge_unique(
        override.related_skills if override else (),
        manifest.depends_on_skills,
        manifest.feeds_into,
        (manifest.key,),
    )
    internal_steps = override.internal_steps if override else _default_skill_internal_steps(manifest)
    advanced_note = override.advanced_note if override else ""

    lines = [f"### Help: {title}", "", summary]
    if manifest.key == active_skill:
        lines.extend(["", "- Status: `current active skill`"])
    if what_to_provide:
        lines.extend(["", "**What to provide**"])
        lines.extend(f"- {item}" for item in what_to_provide)
    if example_prompts:
        lines.extend(["", "**Example prompts**"])
        lines.extend(f"- `{item}`" for item in example_prompts)
    if slash_commands:
        lines.extend(["", "**Useful slash commands**"])
        lines.extend(f"- `{item}`" for item in slash_commands)
    if doc_hints.description and doc_hints.description != summary:
        lines.extend(["", "**Skill instruction summary**", f"- {doc_hints.description}"])
    if doc_hints.handles:
        lines.extend(["", "**Instruction highlights**"])
        lines.extend(f"- {item}" for item in doc_hints.handles)
    if doc_hints.boundaries:
        lines.extend(["", "**When this skill hands off**"])
        lines.extend(f"- {item}" for item in doc_hints.boundaries)
    lines.extend(["", "**Manifest facts**"])
    lines.append(f"- Skill key: `{manifest.key}`")
    lines.append(f"- Display name: `{manifest.display_name or manifest.key}`")
    lines.append(f"- Category: `{manifest.category}`")
    lines.append(f"- Source: `{manifest.source_type}:{manifest.source_key}`" if manifest.source_key and manifest.source_type else "- Source: `n/a`")
    lines.append(f"- Required services: {_format_tuple(manifest.required_services)}")
    lines.append(f"- Expected inputs: {_format_tuple(manifest.expected_inputs)}")
    lines.append(f"- Output types: {_format_enum_tuple(manifest.output_types)}")
    lines.append(f"- Sample types: {_format_enum_tuple(manifest.sample_types)}")
    lines.append(f"- Estimated runtime: `{manifest.estimated_runtime}`")
    if related_skills:
        lines.extend(["", "**Related skills**"])
        lines.extend(f"- `{item}`" for item in related_skills)
    if internal_steps:
        lines.extend(["", "**What AGOUTIC will do**"])
        lines.extend(f"- {item}" for item in internal_steps)
    if advanced_note:
        lines.extend(["", "**Advanced**", f"- {advanced_note}"])
    return "\n".join(lines)


def _resolved_topic(topic_key: str) -> ResolvedHelpTopic:
    topic = _TOPIC_GUIDES[topic_key]
    return ResolvedHelpTopic(kind="topic", key=topic_key, title=topic.title)


def _resolve_skill_topic(topic_ref: str) -> str | None:
    raw = str(topic_ref or "").strip()
    normalized = _normalize_help_text(raw)
    if normalized in {"skills", "skill"}:
        return None

    explicit_skill_ref = re.sub(r"\bskill\b", "", raw, flags=re.IGNORECASE).strip()
    explicit_skill_ref = re.sub(r"^the\s+", "", explicit_skill_ref, flags=re.IGNORECASE)
    if "skill" in normalized:
        return resolve_skill_key(explicit_skill_ref) or resolve_skill_key(normalized)

    return None


def _resolve_command_key(topic_ref: str) -> str | None:
    raw = str(topic_ref or "").strip().lower().strip("`\"'")
    normalized = _normalize_help_text(topic_ref)
    explicit_command = raw.startswith("/")

    if raw.startswith("/help") or normalized == "help":
        return "help"
    if "/commands" in raw or normalized in {"commands", "slash commands", "slash command"}:
        return "commands"
    if "/skills" in raw or normalized == "skills":
        return "skills"
    if raw.startswith("/skill") or normalized.startswith("skill ") or normalized == "skill":
        return "skill"
    if "/use-skill" in raw or normalized.startswith("use skill"):
        return "use-skill"
    if "/list files" in raw or normalized.startswith("list files"):
        return "list-files"
    if "/list staged" in raw or normalized.startswith("list staged"):
        return "list-staged"
    if "/list imported" in raw or normalized.startswith("list imported"):
        return "list-imported"
    if raw.startswith("/clean") or (explicit_command and normalized.startswith("clean ")) or normalized == "clean":
        return "clean"
    if "/sync-workflow" in raw or (explicit_command and normalized.startswith("sync workflow")):
        return "sync-workflow"
    if "/read-file" in raw or (explicit_command and normalized.startswith("read file")):
        return "read-file"
    if raw.startswith("/haplotype") or (explicit_command and normalized.startswith("haplotype ")) or normalized == "haplotype":
        return "haplotype"
    if raw.startswith("/de") or (explicit_command and (normalized.startswith("de ") or normalized == "de")):
        return "de"
    return None


def _cleanup_topic_ref(value: str) -> str:
    cleaned = str(value or "").strip().strip("`\"'")
    cleaned = re.sub(r"^the\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+please$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip().rstrip("?.!")


def _normalize_help_text(value: str) -> str:
    cleaned = str(value or "").strip().lower().strip("`\"'")
    cleaned = re.sub(r"[?.!,]+$", "", cleaned)
    cleaned = cleaned.replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _contains_any(text: str, options: tuple[str, ...]) -> bool:
    return any(option in text for option in options)


def _looks_like_stage_request(text: str) -> bool:
    return bool(re.search(r"\bstag(?:e|ing)(?:\s+only)?\b", text))


def _default_skill_inputs(manifest: SkillManifest) -> tuple[str, ...]:
    if manifest.expected_inputs:
        return tuple(f"{item.replace('_', ' ')}." for item in manifest.expected_inputs)
    return ("The dataset, workflow, or question you want the skill to handle.",)


def _default_skill_examples(manifest: SkillManifest) -> tuple[str, ...]:
    display = manifest.display_name or manifest.key
    examples = [
        f"Tell me about the {display} skill.",
        f"How do I use the {display} skill?",
    ]
    if manifest.slash_commands:
        examples.append(manifest.slash_commands[0])
    return tuple(examples)


def _default_skill_commands(manifest: SkillManifest) -> tuple[str, ...]:
    commands = list(manifest.slash_commands)
    commands.append(f"/skill {manifest.key}")
    return tuple(dict.fromkeys(commands))


def _default_skill_internal_steps(manifest: SkillManifest) -> tuple[str, ...]:
    source = f" through `{manifest.source_type}:{manifest.source_key}`" if manifest.source_key and manifest.source_type else ""
    return (
        f"Route your request to the `{manifest.key}` skill{source}.",
        "Collect missing inputs before planning or execution.",
    )


def _merge_unique(*groups: tuple[str, ...], limit: int | None = None) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            merged.append(text)
            seen.add(text)
            if limit is not None and len(merged) >= limit:
                return tuple(merged)
    return tuple(merged)


@lru_cache(maxsize=None)
def _load_skill_doc_hints(skill_key: str, skill_file: str) -> SkillDocHints:
    skill_path = (_SKILLS_ROOT / skill_file).resolve()
    if not skill_path.exists():
        return SkillDocHints()

    text = skill_path.read_text(encoding="utf-8")
    return SkillDocHints(
        description=_extract_markdown_paragraph(text, (r"description",)),
        handles=_extract_markdown_bullets(text, (r"this\s+skill\s+handles",), limit=4),
        boundaries=_extract_markdown_bullets(text, (r"does\s+not\s+handle",), limit=3),
        examples=_extract_example_questions(text, limit=4),
    )


def _extract_markdown_paragraph(text: str, heading_patterns: tuple[str, ...]) -> str:
    body = _extract_markdown_section_body(text, heading_patterns)
    if not body:
        return ""

    paragraph_lines: list[str] = []
    for line in body:
        stripped = line.strip()
        if not stripped:
            if paragraph_lines:
                break
            continue
        if stripped.startswith(("- ", "* ", "```")):
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(_clean_instruction_line(stripped))
    return re.sub(r"\s+", " ", " ".join(paragraph_lines)).strip()


def _extract_markdown_bullets(text: str, heading_patterns: tuple[str, ...], *, limit: int) -> tuple[str, ...]:
    body = _extract_markdown_section_body(text, heading_patterns)
    if not body:
        return ()

    items: list[str] = []
    for line in body:
        stripped = line.strip()
        if not stripped:
            if items:
                continue
            continue
        if stripped.startswith(("- ", "* ")):
            items.append(_clean_instruction_line(stripped))
            if len(items) >= limit:
                break
    return tuple(item for item in items if item)


def _extract_markdown_section_body(text: str, heading_patterns: tuple[str, ...]) -> list[str]:
    lines = text.splitlines()
    collecting = False
    section_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if collecting:
                break
            heading_text = stripped.lstrip("#").strip()
            if any(re.search(pattern, heading_text, re.IGNORECASE) for pattern in heading_patterns):
                collecting = True
            continue
        if collecting:
            section_lines.append(line)
    return section_lines


def _extract_example_questions(text: str, *, limit: int) -> tuple[str, ...]:
    lines = text.splitlines()
    collecting = False
    examples: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not collecting and re.search(r"example\s+questions", stripped, re.IGNORECASE):
            collecting = True
            continue
        if collecting:
            if not stripped:
                if examples:
                    break
                continue
            if stripped.startswith("#"):
                break
            if stripped.startswith(("- ", "* ")):
                examples.append(_clean_instruction_line(stripped))
                if len(examples) >= limit:
                    break
    return tuple(example for example in examples if example)


def _clean_instruction_line(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[*-]\s+", "", text)
    text = re.sub(r"\[\[SKILL_SWITCH_TO:\s*([^\]]+)\]\]", r"`\1`", text)
    text = text.replace("**", "")
    text = text.replace("__", "")
    return re.sub(r"\s+", " ", text).strip()


def _format_tuple(values: tuple[str, ...]) -> str:
    if not values:
        return "`n/a`"
    return ", ".join(f"`{value}`" for value in values)


def _format_enum_tuple(values: tuple[object, ...]) -> str:
    if not values:
        return "`n/a`"
    return ", ".join(f"`{getattr(value, 'value', value)}`" for value in values)