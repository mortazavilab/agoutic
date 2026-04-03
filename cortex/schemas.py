from pydantic import BaseModel, Field
from typing import Any, Optional, Literal
from dataclasses import dataclass, field, asdict
import json as _json


# ==================== Conversation State ====================

@dataclass
class ConversationState:
    """
    Structured per-conversation state, built from ProjectBlocks each turn.
    Injected as JSON into the user message so the LLM has an authoritative,
    machine-readable snapshot of everything it needs to know.
    """
    active_skill: str = ""
    active_project: str | None = None
    work_dir: str | None = None
    sample_name: str | None = None
    sample_type: str | None = None          # DNA / RNA / CDNA
    reference_genome: str | None = None
    active_experiment: str | None = None     # ENCSR accession from conversation
    active_file: str | None = None           # ENCFF accession from conversation
    known_dataframes: list[str] = field(default_factory=list)   # ["DF1 (12 BAM files)", ...]
    latest_dataframe: str | None = None   # "DF8" — most recent DF for "this/it" references
    collected_params: dict[str, str] = field(default_factory=dict)  # partial intake fields
    workflows: list[dict] = field(default_factory=list)
    active_workflow_index: int | None = None
    active_plan_id: str | None = None           # block ID of active WORKFLOW_PLAN
    active_plan_step: str | None = None         # current step ID within that plan
    pending_action_id: str | None = None
    pending_action_summary: str | None = None
    # --- Remote execution state (Phase 1) ---
    execution_mode: str | None = None           # "local" or "slurm"
    ssh_profile_id: str | None = None
    ssh_profile_nickname: str | None = None
    slurm_resources: dict | None = None         # {account, partition, cpus, memory_gb, walltime, gpus, gpu_type}
    remote_paths: dict | None = None            # {remote_input_path, remote_work_path, remote_output_path, remote_log_path}
    result_destination: str | None = None       # "remote", "local", "both"

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict, stripping None/empty values."""
        d = asdict(self)
        return {k: v for k, v in d.items()
                if v is not None and v != "" and v != [] and v != {}}

    def to_json(self) -> str:
        """Compact JSON for prompt injection."""
        return _json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationState":
        """Reconstruct from a stored dict (tolerant of missing/extra keys)."""
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


# ==================== Block Schemas ====================

class BlockCreate(BaseModel):
    project_id: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    status: str = "NEW"
    payload: Any
    parent_id: Optional[str] = None

class BlockOut(BaseModel):
    id: str
    project_id: str
    seq: int
    type: str
    status: str
    payload: Any
    parent_id: Optional[str] = None
    created_at: str

class BlockStreamOut(BaseModel):
    blocks: list[BlockOut]
    latest_seq: int

class BlockUpdate(BaseModel):
    status: Optional[str] = None
    payload: Optional[Any] = None


class ProjectTaskOut(BaseModel):
    id: str
    project_id: str
    kind: str
    title: str
    status: str
    priority: str
    source_key: str
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    action_label: Optional[str] = None
    action_target: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None
    archived_at: Optional[str] = None
    children: list["ProjectTaskOut"] = Field(default_factory=list)


class ProjectTaskSectionsOut(BaseModel):
    pending: list[ProjectTaskOut] = Field(default_factory=list)
    running: list[ProjectTaskOut] = Field(default_factory=list)
    follow_up: list[ProjectTaskOut] = Field(default_factory=list)
    completed: list[ProjectTaskOut] = Field(default_factory=list)


class ProjectTaskListOut(BaseModel):
    project_id: str
    sections: ProjectTaskSectionsOut


class ProjectTaskUpdate(BaseModel):
    action: str = Field(..., min_length=1)


# ==================== User Data / Central File Schemas ====================

class UserFileOut(BaseModel):
    """Response model for a file in the user's central data folder."""
    id: str
    filename: str
    md5_hash: Optional[str] = None
    size_bytes: Optional[int] = None
    source: str
    source_url: Optional[str] = None
    encode_accession: Optional[str] = None
    sample_name: Optional[str] = None
    organism: Optional[str] = None
    tissue: Optional[str] = None
    tags: Optional[dict] = None
    disk_path: str
    created_at: str
    updated_at: str
    # populated at query time
    projects: list[dict] = Field(default_factory=list)  # [{"project_id": ..., "project_name": ...}]


class UserFileUpdate(BaseModel):
    """Partial update for user file metadata."""
    sample_name: Optional[str] = None
    organism: Optional[str] = None
    tissue: Optional[str] = None
    tags: Optional[dict] = None


class UserFileLinkRequest(BaseModel):
    """Request to link a central file to a project."""
    project_id: str


class UserFileRedownloadRequest(BaseModel):
    """Request to force re-download a file from its original source."""
    force: bool = True


# ==================== Cross-Project Schemas ====================

class CrossProjectProjectOut(BaseModel):
    project_id: str
    project_name: str
    slug: Optional[str] = None
    role: str = "viewer"
    is_archived: bool = False
    is_public: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CrossProjectFileOut(BaseModel):
    project_id: str
    project_name: str
    relative_path: str
    name: str
    is_dir: bool
    category: str
    workflow_folder: Optional[str] = None
    file_type: str
    size: Optional[int] = None
    modified_time: Optional[str] = None


class CrossProjectBrowseResponse(BaseModel):
    project_id: str
    project_name: str
    subpath: str
    items: list[CrossProjectFileOut] = Field(default_factory=list)
    total_count: int = 0
    truncated: bool = False
    limit: int


class CrossProjectSearchResponse(BaseModel):
    query: str
    items: list[CrossProjectFileOut] = Field(default_factory=list)
    total_count: int = 0
    truncated: bool = False
    limit: int


class LogicalFileReference(BaseModel):
    project_name: Optional[str] = None
    workflow_name: Optional[str] = None
    sample_name: Optional[str] = None
    file_type: Optional[str] = None


class SelectedFileInput(BaseModel):
    source_project_id: str = Field(..., min_length=1)
    relative_path: Optional[str] = Field(default=None, min_length=1)
    logical_reference: Optional[LogicalFileReference] = None


class StageRequest(BaseModel):
    selected_files: list[SelectedFileInput] = Field(..., min_length=1)
    action_type: Literal["stage_workspace", "analyze_together", "compare_together"]
    destination_project_id: Optional[str] = None
    destination_project_name: Optional[str] = None


class StageResponse(BaseModel):
    stage_id: str
    destination_project_id: str
    destination_project_name: str
    created_at: str
    action_type: str
    staging_mode: str
    item_count: int
    manifest_path: str


class StageStatusResponse(BaseModel):
    stage_id: str
    destination_project_id: str
    destination_project_name: str
    created_at: str
    action_type: str
    staging_mode: str
    item_count: int
    manifest_relative_path: str


# ==================== Memory Schemas ====================

class MemoryCreate(BaseModel):
    """Request to create a memory entry."""
    content: str = Field(..., min_length=1)
    category: str = "custom"
    project_id: Optional[str] = None
    structured_data: Optional[dict] = None
    tags: Optional[dict] = None
    is_pinned: bool = False


class MemoryOut(BaseModel):
    """Response model for a memory entry."""
    id: str
    user_id: str
    project_id: Optional[str] = None
    category: str
    content: str
    structured_data: Optional[dict] = None
    source: str
    is_pinned: bool
    is_deleted: bool
    related_block_id: Optional[str] = None
    related_file_id: Optional[str] = None
    tags: Optional[dict] = None
    created_at: str


class MemoryUpdate(BaseModel):
    """Partial update for a memory entry."""
    content: Optional[str] = None
    tags: Optional[dict] = None
    is_pinned: Optional[bool] = None


class MemoryListOut(BaseModel):
    """Paginated list of memory entries."""
    memories: list[MemoryOut] = Field(default_factory=list)
    total: int = 0