from pydantic import BaseModel, Field
from typing import Any, Optional

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
