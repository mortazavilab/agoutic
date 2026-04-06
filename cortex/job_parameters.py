import re

from sqlalchemy import select

from common.logging_config import get_logger
from cortex.config import AGOUTIC_DATA, GENOME_ALIASES
from cortex.llm_validators import get_block_payload
from cortex.models import Project, ProjectBlock, User
from cortex.remote_orchestration import _prepare_remote_execution_params
from cortex.user_jail import get_user_data_dir

logger = get_logger(__name__)

_REMOTE_INPUT_PATTERNS = [
    re.compile(
        r"\b(?:use|using|with|from|at)\s+(?:the\s+)?remote\s+(?:data|folder|path|input(?:\s+folder)?|directory)\s+(?:at|in)?\s*(/[\w./-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bremote\s+(?:data|folder|path|input(?:\s+folder)?|directory)\s+(?:at|in)?\s*(/[\w./-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:use|using|with|from)\s+(?:data\s+)?on\s+(?:the\s+)?cluster\s+(?:at|in)?\s*(/[\w./-]+)",
        re.IGNORECASE,
    ),
]


def _extract_remote_input_path(user_text: str) -> str | None:
    for pattern in _REMOTE_INPUT_PATTERNS:
        match = pattern.search(user_text or "")
        if match:
            return match.group(1).rstrip('.,;:!?')
    return None


def _resolve_relative_input_path(
    *,
    cleaned_path: str,
    owner_user: User | None,
    owner_id: str | None,
    project: Project | None,
    project_id: str,
):
    """Resolve relative input paths against project or central user data roots."""
    if cleaned_path.startswith("/"):
        return cleaned_path

    path_parts = cleaned_path.split("/", 1)
    first_component = path_parts[0].lower() if path_parts else ""

    username = getattr(owner_user, "username", None) if owner_user else None
    project_slug = getattr(project, "slug", None) if project else None

    if username and project_slug:
        project_dir = AGOUTIC_DATA / "users" / username / project_slug
    elif owner_id:
        project_dir = AGOUTIC_DATA / "users" / owner_id / project_id
    else:
        return cleaned_path

    project_candidate = project_dir / cleaned_path

    if first_component == "data" and len(path_parts) == 2 and username:
        central_candidate = get_user_data_dir(username) / path_parts[1]
        if project_candidate.exists():
            return str(project_candidate)
        if central_candidate.exists():
            return str(central_candidate)

    return str(project_candidate)


async def extract_job_parameters_from_conversation(session, project_id: str) -> dict:
    """
    Extract job parameters from conversation history using heuristics.

    Scopes to the **most recent submission cycle** -- only blocks after the
    last EXECUTION_JOB or APPROVAL_GATE (approved). This prevents stale
    parameters from earlier jobs (e.g. a previous sample name) bleeding
    into a new submission.

    Analyzes USER_MESSAGE and AGENT_PLAN blocks to determine:
    - sample_name
    - mode (DNA/RNA/CDNA)
    - input_directory (path to pod5 files)
    - reference_genome (GRCh38, mm39, etc.)
    - modifications (optional)
    """
    # Fetch all blocks for this project
    query = select(ProjectBlock)\
        .where(ProjectBlock.project_id == project_id)\
        .order_by(ProjectBlock.seq.asc())

    result = session.execute(query)
    blocks = result.scalars().all()

    # --- Scope to the most recent submission cycle ---
    # Find the last EXECUTION_JOB or approved APPROVAL_GATE block.
    # Only consider blocks AFTER that point for parameter extraction.
    last_boundary_idx = -1
    for i, block in enumerate(blocks):
        if block.type == "EXECUTION_JOB":
            last_boundary_idx = i
        elif block.type == "APPROVAL_GATE" and block.status == "APPROVED":
            last_boundary_idx = i

    recent_blocks = blocks[last_boundary_idx + 1:] if last_boundary_idx >= 0 else blocks

    slurm_reuse_seed = {}
    if last_boundary_idx >= 0:
        for block in reversed(blocks[: last_boundary_idx + 1]):
            seed_params = {}
            if block.type == "APPROVAL_GATE" and block.status == "APPROVED":
                payload = get_block_payload(block)
                seed_params = payload.get("edited_params") or payload.get("extracted_params") or {}
            elif block.type == "EXECUTION_JOB":
                seed_params = get_block_payload(block)

            if (seed_params.get("execution_mode") or "local") != "slurm":
                continue

            slurm_reuse_seed = {
                "execution_mode": "slurm",
                "ssh_profile_id": seed_params.get("ssh_profile_id"),
                "ssh_profile_nickname": seed_params.get("ssh_profile_nickname"),
                "slurm_account": seed_params.get("slurm_account"),
                "slurm_partition": seed_params.get("slurm_partition"),
                "slurm_gpu_account": seed_params.get("slurm_gpu_account"),
                "slurm_gpu_partition": seed_params.get("slurm_gpu_partition"),
                "slurm_cpus": seed_params.get("slurm_cpus"),
                "slurm_memory_gb": seed_params.get("slurm_memory_gb"),
                "slurm_walltime": seed_params.get("slurm_walltime"),
                "slurm_gpus": seed_params.get("slurm_gpus"),
                "slurm_gpu_type": seed_params.get("slurm_gpu_type"),
                "remote_base_path": seed_params.get("remote_base_path"),
                "result_destination": seed_params.get("result_destination"),
                "max_gpu_tasks": seed_params.get("max_gpu_tasks"),
            }
            break

    # Build conversation context from recent blocks only
    conversation = []
    user_messages = []
    for block in recent_blocks:
        if block.type == "USER_MESSAGE":
            text = get_block_payload(block).get("text", "")
            conversation.append(f"User: {text}")
            user_messages.append(text)
        elif block.type == "AGENT_PLAN":
            conversation.append(f"Agent: {get_block_payload(block).get('markdown', '')}")

    if not conversation:
        return None

    conversation_text = "\n".join(conversation)
    all_user_text_original = " ".join(user_messages)  # Keep original case for path extraction
    all_user_text = all_user_text_original.lower()  # Lowercase for keyword matching
    explicit_local_requested = bool(
        re.search(r"\b(local|locally|on\s+this\s+machine|run\s+local(?:ly)?)\b", all_user_text)
    )

    # First, use simple heuristics for quick detection
    params = {
        "sample_name": None,
        "mode": None,
        "input_directory": None,
        "input_directory_explicit": False,
        "remote_input_path": None,
        "input_type": "pod5",  # Default to pod5
        "entry_point": None,  # Dogme entry point
        "reference_genome": [],  # Now a list for multi-genome support
        "modifications": None,
        # Advanced parameters (optional)
        "modkit_filter_threshold": None,  # Will use default 0.9 if not specified
        "min_cov": None,  # Will default based on mode if not specified
        "per_mod": None,  # Will use default 5 if not specified
        "accuracy": None,  # Will use default "sup" if not specified
        "max_gpu_tasks": slurm_reuse_seed.get("max_gpu_tasks"),  # Will use default 1 if not specified
        "execution_mode": slurm_reuse_seed.get("execution_mode") or "local",
        "ssh_profile_id": slurm_reuse_seed.get("ssh_profile_id"),
        "ssh_profile_nickname": slurm_reuse_seed.get("ssh_profile_nickname"),
        "slurm_account": slurm_reuse_seed.get("slurm_account"),
        "slurm_partition": slurm_reuse_seed.get("slurm_partition"),
        "slurm_gpu_account": slurm_reuse_seed.get("slurm_gpu_account"),
        "slurm_gpu_partition": slurm_reuse_seed.get("slurm_gpu_partition"),
        "slurm_cpus": slurm_reuse_seed.get("slurm_cpus"),
        "slurm_memory_gb": slurm_reuse_seed.get("slurm_memory_gb"),
        "slurm_walltime": slurm_reuse_seed.get("slurm_walltime"),
        "slurm_gpus": slurm_reuse_seed.get("slurm_gpus"),
        "slurm_gpu_type": slurm_reuse_seed.get("slurm_gpu_type"),
        "remote_base_path": slurm_reuse_seed.get("remote_base_path"),
        "staged_remote_input_path": None,
        "remote_staged_sample": None,
        "result_destination": slurm_reuse_seed.get("result_destination"),
        "remote_action": "job",
        "gate_action": "job",
    }

    if re.search(r"\b(slurm|sbatch|cluster|remote(?:ly|\s+execution)?)\b", all_user_text):
        params["execution_mode"] = "slurm"

    remote_input_path = _extract_remote_input_path(all_user_text_original)
    if remote_input_path:
        params["execution_mode"] = "slurm"
        params["remote_input_path"] = remote_input_path

    profile_target_match = re.search(
        r"\b(?:on\s+(?!the\b|slurm\b|remote\b|local\b|my\b|your\b|this\b|that\b)([a-zA-Z0-9_-]+)(?:\s+profile)?(?:[?.!,]|$)|(?:using|via)\s+(?:the\s+)?([a-zA-Z0-9_-]+)\s+profile)\b",
        all_user_text_original,
        re.IGNORECASE,
    )
    if profile_target_match:
        params["execution_mode"] = "slurm"
        params["ssh_profile_nickname"] = profile_target_match.group(1) or profile_target_match.group(2)

    if re.search(r"\bstage(?:\s+only)?\b", all_user_text) and (
        re.search(r"\b(slurm|cluster|remote)\b", all_user_text) or profile_target_match
    ):
        params["execution_mode"] = "slurm"
        params["remote_action"] = "stage_only"
        params["gate_action"] = "remote_stage"

    profile_match = re.search(
        r"\b(?:using|via)\s+(?:the\s+)?([a-zA-Z0-9_-]+)\s+profile\b",
        all_user_text_original,
        re.IGNORECASE,
    )
    if profile_match:
        params["execution_mode"] = "slurm"
        params["ssh_profile_nickname"] = profile_match.group(1)

    if explicit_local_requested:
        params["execution_mode"] = "local"
        params["remote_action"] = "job"
        params["gate_action"] = "job"
        params["ssh_profile_id"] = None
        params["ssh_profile_nickname"] = None

    account_match = re.search(r"\baccount\s+([a-zA-Z0-9_-]+)\b", all_user_text, re.IGNORECASE)
    if account_match:
        params["slurm_account"] = account_match.group(1)

    gpu_account_match = re.search(r"\bgpu\s+account\s+([a-zA-Z0-9_-]+)\b", all_user_text, re.IGNORECASE)
    if gpu_account_match:
        params["slurm_gpu_account"] = gpu_account_match.group(1)

    _invalid_partition_tokens = {
        "and", "or", "cpu", "gpu", "account", "partition", "queue",
        "override", "default", "defaults", "for", "on", "in", "with",
    }
    partition_match = re.search(r"(?<!/)\b(?:partition|queue)\s+([a-zA-Z0-9_-]+)\b", all_user_text, re.IGNORECASE)
    if partition_match:
        _cand_partition = partition_match.group(1)
        if _cand_partition.lower() not in _invalid_partition_tokens:
            params["slurm_partition"] = _cand_partition

    gpu_partition_match = re.search(r"\bgpu\s+(?:partition|queue)\s+([a-zA-Z0-9_-]+)\b", all_user_text, re.IGNORECASE)
    if gpu_partition_match:
        _cand_gpu_partition = gpu_partition_match.group(1)
        if _cand_gpu_partition.lower() not in _invalid_partition_tokens:
            params["slurm_gpu_partition"] = _cand_gpu_partition

    cpus_match = re.search(r"\b(\d+)\s*(?:cpus?|cores?)\b", all_user_text, re.IGNORECASE)
    if cpus_match:
        params["slurm_cpus"] = int(cpus_match.group(1))

    memory_match = re.search(r"\b(\d+)\s*(?:gb|gib)\b", all_user_text, re.IGNORECASE)
    if memory_match:
        params["slurm_memory_gb"] = int(memory_match.group(1))

    walltime_match = re.search(r"\b(\d{1,2}:\d{2}:\d{2}|\d+-\d{2}:\d{2}:\d{2})\b", all_user_text)
    if walltime_match:
        params["slurm_walltime"] = walltime_match.group(1)

    gpus_match = re.search(r"\b(\d+)\s*gpus?\b", all_user_text, re.IGNORECASE)
    if gpus_match:
        params["slurm_gpus"] = int(gpus_match.group(1))

    # Detect Dogme entry point from conversation
    if "only basecall" in all_user_text or "just basecalling" in all_user_text or "basecall only" in all_user_text:
        params["entry_point"] = "basecall"
        params["input_type"] = "pod5"
    elif "call modifications" in all_user_text or "run modkit" in all_user_text or "extract modifications" in all_user_text:
        params["entry_point"] = "modkit"
        params["input_type"] = "bam"  # modkit needs mapped BAM
    elif "generate report" in all_user_text or "create summary" in all_user_text or "reports only" in all_user_text:
        params["entry_point"] = "reports"
    elif "annotate" in all_user_text and ("transcripts" in all_user_text or "rna" in all_user_text):
        params["entry_point"] = "annotateRNA"
        params["input_type"] = "bam"  # annotateRNA needs mapped BAM
    elif "unmapped bam" in all_user_text or "remap" in all_user_text:
        params["entry_point"] = "remap"
        params["input_type"] = "bam"
    elif "downloaded bam" in all_user_text or "from bam" in all_user_text or ("bam" in all_user_text and "from data" in all_user_text):
        # User wants to run Dogme from downloaded BAM files in project data/
        params["entry_point"] = "remap"
        params["input_type"] = "bam"
    elif ".bam" in all_user_text_original:
        # Detect if BAM is mapped or unmapped based on context
        if "mapped" in all_user_text and "unmapped" not in all_user_text:
            params["input_type"] = "bam"
            # Will need to determine entry point based on other keywords
        else:
            params["entry_point"] = "remap"
            params["input_type"] = "bam"
    elif ".fastq" in all_user_text_original or ".fq" in all_user_text_original or "fastq" in all_user_text:
        params["input_type"] = "fastq"

    # Detect genome from keywords - support multiple genomes
    genome_keywords = ["human", "mouse", "hg38", "mm39", "mm10", "grch38"]
    found_genomes = set()

    # Find all genome mentions
    for keyword in genome_keywords:
        if keyword in all_user_text:
            canonical = GENOME_ALIASES.get(keyword, keyword)
            found_genomes.add(canonical)

    # Check for "both X and Y" pattern
    multi_genome_pattern = r'both\s+(\w+)\s+and\s+(\w+)'
    multi_match = re.search(multi_genome_pattern, all_user_text)
    if multi_match:
        g1 = GENOME_ALIASES.get(multi_match.group(1), multi_match.group(1))
        g2 = GENOME_ALIASES.get(multi_match.group(2), multi_match.group(2))
        found_genomes.add(g1)
        found_genomes.add(g2)

    # Convert to list, default to mouse if none found
    # For modkit/annotateRNA, do NOT auto-default genome -- the user must specify
    # the genome the BAM was actually mapped to.
    if found_genomes:
        params["reference_genome"] = list(found_genomes)
    elif params["entry_point"] in ("modkit", "annotateRNA"):
        params["reference_genome"] = []  # Force the intake skill to ask
    else:
        params["reference_genome"] = ["mm39"]

    # Detect mode from keywords
    if "rna" in all_user_text and "cdna" not in all_user_text:
        params["mode"] = "RNA"
    elif "cdna" in all_user_text:
        params["mode"] = "CDNA"
    elif "dna" in all_user_text or "genomic" in all_user_text or "fiber" in all_user_text:
        params["mode"] = "DNA"
    else:
        params["mode"] = "DNA"  # Default to DNA

    # Look for paths in user messages (use ORIGINAL case to preserve path)
    # First try: relative paths with known sequencing extensions (data/ENCFF921XAH.bam)
    _rel_path_pattern = r'(?<!/)\b([\w.-]+/[\w./-]+\.(?:bam|pod5|fastq|fq|fast5))\b'
    _rel_paths = re.findall(_rel_path_pattern, all_user_text_original)
    # Second try: absolute paths
    # Avoid false positives like "account/partition" by requiring a sensible prefix.
    _abs_path_pattern = r'(?:(?<=^)|(?<=[\s"\'(]))(/[^\s,]+(?:/[^\s,]+)*)'
    _abs_paths = re.findall(_abs_path_pattern, all_user_text_original)

    filtered_abs_paths = [
        candidate for candidate in _abs_paths
        if not remote_input_path or candidate.rstrip('.,;:!?') != remote_input_path
    ]

    if filtered_abs_paths:
        cleaned_path = filtered_abs_paths[0].rstrip('.,;:!?')
        params["input_directory"] = cleaned_path
        params["input_directory_explicit"] = True
    elif _rel_paths:
        cleaned_path = _rel_paths[0].rstrip('.,;:!?')
        params["input_directory_explicit"] = True
        # Resolve relative path against project directory
        _proj = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
        _owner_id = None
        # Find the project owner from blocks
        for block in blocks:
            if block.owner_id:
                _owner_id = block.owner_id
                break
        if _owner_id:
            _owner_user = session.execute(select(User).where(User.id == _owner_id)).scalar_one_or_none()
            params["input_directory"] = _resolve_relative_input_path(
                cleaned_path=cleaned_path,
                owner_user=_owner_user,
                owner_id=_owner_id,
                project=_proj,
                project_id=project_id,
            )
        else:
            params["input_directory"] = cleaned_path
    elif remote_input_path:
        params["input_directory"] = f"remote:{remote_input_path}"
    else:
        params["input_directory"] = "/data/samples/test"

    # Extract sample name from context
    # Search user messages in REVERSE order (most recent first) so a new
    # submission request wins over older ones in the same cycle.
    explicit_patterns = [
        r'([a-zA-Z0-9_-]+)\s+is\s+(?:the\s+)?sample\s+name',  # "Jamshid is sample name"
        r'sample\s+name\s+is\s+([a-zA-Z0-9_-]+)',  # "sample name is Jamshid"
        r'named\s+([a-zA-Z0-9_-]+)',  # "named Ali1"
        r'called\s+([a-zA-Z0-9_-]+)',  # "called Ali1"
        r'(?:the\s+)?sample\s+([a-zA-Z0-9_-]+)',  # "the sample c2c12r1" / "sample c2c12r1"
        r'analyze\s+(?:the\s+)?(?:sample\s+)?([a-zA-Z0-9_-]+)\s+using',  # "analyze c2c12r1 using"
        r'analyze\s+(?:the\s+)?(?:sample\s+)?([a-zA-Z0-9_-]+)\s+on',  # "analyze Jamshid on hpc3"
    ]
    _skip_words = {"is", "the", "a", "an", "this", "that", "it", "at", "in", "on",
                   "mm39", "grch38", "hg38", "mm10", "name", "type", "data", "file",
                   "using", "with", "from", "for", "my", "new", "rna", "dna", "cdna"}
    for msg in reversed(user_messages):
        for pattern in explicit_patterns:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                candidate = match.group(1)
                if candidate.lower() not in _skip_words:
                    params["sample_name"] = candidate
                    break
        if params["sample_name"]:
            break

    # If not found via explicit pattern, check standalone answers (but filter more carefully)
    if not params["sample_name"]:
        for msg in reversed(user_messages):
            msg_lower = msg.lower().strip()
            # Check if this is a short, standalone response (likely an answer to a question)
            if len(msg.split()) <= 5 and len(msg) < 50:
                # Extract just the name part if it says "X is sample name"
                name_match = re.search(r'^([a-zA-Z0-9_-]+)(?:\s+is\s+sample\s+name)?$', msg, re.IGNORECASE)
                if name_match:
                    potential_name = name_match.group(1)
                    # Not a path, not a common word, not a genome name
                    if (potential_name.lower() not in ["dna", "rna", "cdna", "mm39", "grch38", "hg38", "mm10", "human", "mouse", "yes", "no", "sup", "hac", "fast"]
                        and "/" not in potential_name):
                        params["sample_name"] = potential_name
                        break

    if not params["sample_name"]:
        # Use genome type + project timestamp as default
        genome_type = "mouse" if "mm39" in params["reference_genome"] else "human"
        params["sample_name"] = f"{genome_type}_sample_{project_id.split('_')[-1]}"

    # Extract advanced parameters if mentioned
    # modkit_filter_threshold (handle "threshold of 0.85" or "threshold: 0.85")
    threshold_pattern = r'(?:modkit\s+)?(?:threshold|filter)(?:[:\s]+of\s+|[:\s]+)([0-9.]+)'
    threshold_match = re.search(threshold_pattern, all_user_text)
    if threshold_match:
        try:
            params["modkit_filter_threshold"] = float(threshold_match.group(1))
        except ValueError:
            pass

    # min_cov (handle "coverage of 10" or "min cov: 10")
    mincov_pattern = r'(?:min[_\s]*cov|minimum[_\s]*coverage)(?:[:\s]+of\s+|[:\s]+)(\d+)'
    mincov_match = re.search(mincov_pattern, all_user_text)
    if mincov_match:
        params["min_cov"] = int(mincov_match.group(1))

    # per_mod (handle "per mod of 8" or "per_mod: 8")
    permod_pattern = r'(?:per[_\s]*mod|percentage)(?:[:\s]+of\s+|[:\s]+)(\d+)'
    permod_match = re.search(permod_pattern, all_user_text)
    if permod_match:
        params["per_mod"] = int(permod_match.group(1))

    # accuracy (sup, hac, fast)
    if "accuracy" in all_user_text:
        if "hac" in all_user_text:
            params["accuracy"] = "hac"
        elif "fast" in all_user_text:
            params["accuracy"] = "fast"
        elif "sup" in all_user_text:
            params["accuracy"] = "sup"

    # max_gpu_tasks (handle "max gpu tasks 2", "limit dorado to 3", "run 2 gpu tasks at a time")
    gpu_task_patterns = [
        r'max[_\s]*gpu[_\s]*tasks?[:\s]+(?:of\s+)?(\d+)',
        r'limit\s+(?:dorado|gpu)\s+(?:tasks?\s+)?to\s+(\d+)',
        r'(\d+)\s+(?:concurrent|simultaneous|parallel)\s+(?:dorado|gpu)\s+tasks?',
        r'(?:run|allow)\s+(\d+)\s+(?:dorado|gpu)\s+tasks?',
    ]
    for gp in gpu_task_patterns:
        gpu_match = re.search(gp, all_user_text)
        if gpu_match:
            params["max_gpu_tasks"] = int(gpu_match.group(1))
            break

    owner_id = None
    project = session.execute(select(Project).where(Project.id == project_id)).scalar_one_or_none()
    if project:
        owner_id = project.owner_id

    if owner_id:
        params = await _prepare_remote_execution_params(session, project_id, owner_id, params)

    logger.info("Extracted parameters", method="heuristics", params=params)
    return params
