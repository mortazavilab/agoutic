from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Text, DateTime, func

class Base(DeclarativeBase):
    pass

class ProjectBlock(Base):
    __tablename__ = "project_blocks"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    project_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

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
