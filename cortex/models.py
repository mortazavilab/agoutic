from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Text, DateTime, Boolean, Float, func
from common.database import Base

class User(Base):
    """User account with Google OAuth support"""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    google_sub_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # Unique filesystem-safe username (e.g. "eli-garcia"). Set once at onboarding,
    # only changeable by admins.  Pattern: ^[a-z0-9][a-z0-9_-]{1,30}$
    username: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="user")  # 'user' or 'admin'
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_project_id: Mapped[str | None] = mapped_column(String, nullable=True)  # Last active project
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_login: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Token quota — NULL means unlimited; set by admins to cap usage
    token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

class Session(Base):
    """Session tokens for authentication"""
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Session token
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

class Project(Base):
    """Project metadata with ownership"""
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Filesystem-safe slug derived from name (e.g. "my-encode-project").
    # Unique per owner: (owner_id, slug) is unique.
    slug: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

class ProjectAccess(Base):
    """Track user's project access and role (owner/editor/viewer)"""
    __tablename__ = "project_access"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="owner")  # 'owner', 'editor', 'viewer'
    last_accessed: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

class Conversation(Base):
    """Conversation history for each project"""
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

class ConversationMessage(Base):
    """Individual messages in a conversation"""
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # Message order
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Token usage — populated on assistant messages only; NULL for pre-tracking messages
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String, nullable=True)

class JobResult(Base):
    """Links jobs to conversations for easy access"""
    __tablename__ = "job_results"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    run_uuid: Mapped[str] = mapped_column(String, index=True, nullable=False)
    sample_name: Mapped[str] = mapped_column(String, nullable=False)
    workflow_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class UserFile(Base):
    """Central file registry — one row per physical file in the user's data folder.

    Stores provenance (source URL, ENCODE accession), content hash for dedup,
    and user-editable metadata (sample_name, organism, tissue, freeform tags).
    Disk path points to ``AGOUTIC_DATA/users/{username}/data/{filename}``.
    """
    __tablename__ = "user_files"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    md5_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "encode", "url", "upload", "local_intake"
    source: Mapped[str] = mapped_column(String, nullable=False, default="url")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    encode_accession: Mapped[str | None] = mapped_column(String, nullable=True)  # ENCFF...
    # User-editable metadata
    sample_name: Mapped[str | None] = mapped_column(String, nullable=True)
    organism: Mapped[str | None] = mapped_column(String, nullable=True)
    tissue: Mapped[str | None] = mapped_column(String, nullable=True)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # freeform {"key":"val",...}
    # Absolute path on disk
    disk_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class UserFileProjectLink(Base):
    """Junction table: which projects reference a central UserFile via symlink."""
    __tablename__ = "user_file_project_links"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_file_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    symlink_path: Mapped[str] = mapped_column(Text, nullable=False)  # absolute symlink path
    linked_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
class DeletedProjectTokenUsage(Base):
    """Lifetime token totals preserved after permanent project deletion."""
    __tablename__ = "deleted_project_token_usage"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    project_name: Mapped[str | None] = mapped_column(String, nullable=True)
    conversation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assistant_message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class DeletedProjectTokenDaily(Base):
    """Daily token aggregates preserved after permanent project deletion."""
    __tablename__ = "deleted_project_token_daily"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    usage_date: Mapped[str] = mapped_column(String, index=True, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assistant_message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

class ProjectBlock(Base):
    __tablename__ = "project_blocks"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    project_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

    seq: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="NEW")

    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ProjectTask(Base):
    """Persistent user-facing task rows derived from workflow state."""
    __tablename__ = "project_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    project_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

    kind: Mapped[str] = mapped_column(String, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, index=True, nullable=False, default="PENDING")
    priority: Mapped[str] = mapped_column(String, nullable=False, default="normal")

    source_key: Mapped[str] = mapped_column(String, index=True, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String, nullable=True)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(String, nullable=True)

    action_label: Mapped[str | None] = mapped_column(String, nullable=True)
    action_target: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    completed_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserExecutionPreference(Base):
    """Saved execution preferences per user (default mode, profile, destination)."""
    __tablename__ = "user_execution_preferences"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    preferred_execution_mode: Mapped[str | None] = mapped_column(String, nullable=True)  # "local" or "slurm"
    preferred_ssh_profile_id: Mapped[str | None] = mapped_column(String, nullable=True)
    preferred_result_destination: Mapped[str | None] = mapped_column(String, nullable=True)  # "remote","local","both"
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Memory(Base):
    """Persistent memory entries — user-global or project-scoped.

    Stores results, sample annotations, pipeline steps, preferences,
    findings, and freeform notes.  Supports soft-delete for recovery.
    """
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    # NULL = user-global memory; set = project-scoped
    project_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    # result | sample_annotation | pipeline_step | preference | finding | custom
    category: Mapped[str] = mapped_column(String, nullable=False, default="custom")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Machine-parseable payload (sample annotations, step details, etc.)
    structured_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    # user_manual | auto_step | auto_result | system
    source: Mapped[str] = mapped_column(String, nullable=False, default="user_manual")
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optional links to originating block or file
    related_block_id: Mapped[str | None] = mapped_column(String, nullable=True)
    related_file_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    # Freeform user tags for custom categorization
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
