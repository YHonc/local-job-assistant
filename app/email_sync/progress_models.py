"""Durable progress suggestions and newly occurring application history."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, event, inspect
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database.models import Application
from app.database.session import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EmailProgressSuggestion(Base):
    __tablename__ = "email_progress_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "email_job_entry_id",
            "entry_version",
            "snapshot_key",
            name="uq_progress_suggestion_entry_app_snapshot",
        ),
        Index("ix_progress_suggestions_status", "status"),
        Index("ix_progress_suggestions_application_id", "application_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_job_entry_id: Mapped[int] = mapped_column(
        ForeignKey("email_job_entries.id", ondelete="CASCADE"), index=True
    )
    source_email_id: Mapped[int] = mapped_column(
        ForeignKey("email_messages.id", ondelete="CASCADE"), index=True
    )
    cleaning_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_cleaning_results.id", ondelete="SET NULL"), index=True
    )
    cleaning_version: Mapped[int | None] = mapped_column(Integer)
    entry_version: Mapped[int] = mapped_column(Integer)
    snapshot_key: Mapped[str] = mapped_column(String(200))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), index=True
    )
    application_version: Mapped[int | None] = mapped_column(Integer)
    expected_application_status: Mapped[str | None] = mapped_column(String(30))
    target_status: Mapped[str | None] = mapped_column(String(30))
    event_type: Mapped[str] = mapped_column(String(50))
    event_round: Mapped[str | None] = mapped_column(String(50))
    evidence: Mapped[dict | list | None] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    accept_expected_version: Mapped[int | None] = mapped_column(Integer)
    result_application_status: Mapped[str | None] = mapped_column(String(30))
    result_application_version: Mapped[int | None] = mapped_column(Integer)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    __mapper_args__ = {"version_id_col": version}


class EmailManualFactRequest(Base):
    """Idempotency ledger for entries created directly from a stored email."""

    __tablename__ = "email_manual_fact_requests"
    __table_args__ = (
        UniqueConstraint("source_email_id", "idempotency_key", name="uq_manual_fact_request_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_email_id: Mapped[int] = mapped_column(
        ForeignKey("email_messages.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    email_job_entry_id: Mapped[int] = mapped_column(
        ForeignKey("email_job_entries.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApplicationStatusHistory(Base):
    """A status change that actually occurred after this table was introduced."""

    __tablename__ = "application_status_history"
    __table_args__ = (
        Index("ix_application_status_history_application_time", "application_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    before_status: Mapped[str] = mapped_column(String(30))
    after_status: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(50), default="manual")
    result_version: Mapped[int | None] = mapped_column(Integer)
    suggestion_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_progress_suggestions.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


@event.listens_for(Session, "before_flush")
def _record_application_status_changes(session: Session, _flush_context, _instances) -> None:
    """Capture future ORM status changes, including writes through old APIs.

    This intentionally never infers or backfills a history from an existing
    status. Creation of an Application is not a transition.
    """

    source = session.info.get("application_status_source", "manual")
    suggestion_id = session.info.get("application_status_suggestion_id")
    for application in list(session.dirty):
        if not isinstance(application, Application):
            continue
        history = inspect(application).attrs.status.history
        if not history.has_changes() or not history.deleted or not history.added:
            continue
        before_status = history.deleted[0]
        after_status = history.added[0]
        if before_status == after_status:
            continue
        session.add(
            ApplicationStatusHistory(
                application_id=application.id,
                before_status=str(before_status),
                after_status=str(after_status),
                source=source,
                result_version=application.version + 1,
                suggestion_id=suggestion_id,
            )
        )
