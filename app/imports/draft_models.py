from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import now
from app.database.session import Base


class ImportDraft(Base):
    """A durable browser/text capture and its explicitly reviewed job facts."""

    __tablename__ = "import_drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'extracted', 'confirmed')",
            name="ck_import_drafts_status",
        ),
        CheckConstraint("version >= 1", name="ck_import_drafts_version"),
        Index("ix_import_drafts_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_website: Mapped[str | None] = mapped_column(String(200))
    original_title: Mapped[str | None] = mapped_column(String(500))
    captured_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # User-entered or explicitly accepted facts are kept separately from AI output.
    user_fields: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    suggested_fields: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    confirmed_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    # Hash of the confirmation inputs, excluding expected_version so an HTTP
    # retry after a lost response can return the exact previous result.
    confirmation_fingerprint: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )
