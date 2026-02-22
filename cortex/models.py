from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Text, DateTime, Boolean, func

class Base(DeclarativeBase):
    pass

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
