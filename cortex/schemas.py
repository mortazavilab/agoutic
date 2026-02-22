from pydantic import BaseModel, Field
from typing import Any, Optional
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
    collected_params: dict[str, str] = field(default_factory=dict)  # partial intake fields
    workflows: list[dict] = field(default_factory=list)
    active_workflow_index: int | None = None

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