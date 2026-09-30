from datetime import date, datetime, timezone
from enum import Enum
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.job_categories import JobCategory
from .session import Base

def now() -> datetime:
    return datetime.now(timezone.utc)

class ApplicationStatus(str, Enum):
    待面试 = "待面试"
    面试1 = "1面"
    面试2 = "2面"
    面试3 = "3面"
    收藏 = "收藏"
    待投递 = "待投递"
    已投递 = "已投递"
    测评 = "测评"
    笔试 = "笔试"
    一面 = "一面"
    二面 = "二面"
    HR面 = "HR面"
    Offer = "Offer"
    拒绝 = "拒绝"
    放弃 = "放弃"


class RecruitmentEventType(str, Enum):
    APPLICATION_RECEIVED = "APPLICATION_RECEIVED"
    ASSESSMENT = "ASSESSMENT"
    WRITTEN_TEST = "WRITTEN_TEST"
    INTERVIEW = "INTERVIEW"
    INTERVIEW_CHANGED = "INTERVIEW_CHANGED"
    OFFER = "OFFER"
    REJECTED = "REJECTED"
    RECRUITMENT_MARKETING = "RECRUITMENT_MARKETING"
    OTHER = "OTHER"

class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    aliases: Mapped[list[str] | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    jobs: Mapped[list["Job"]] = relationship(back_populates="company", passive_deletes=True)

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(300))
    employment_type: Mapped[str | None] = mapped_column(String(100), index=True)
    job_category: Mapped[str] = mapped_column(
        String(50),
        default=JobCategory.OTHER.value,
        server_default=JobCategory.OTHER.value,
        index=True,
    )
    category_review_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    organize_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )
    salary_text: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    description: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_website: Mapped[str | None] = mapped_column(String(200))
    original_title: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[date | None] = mapped_column(Date, index=True)
    structured_data: Mapped[dict | list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    company: Mapped[Company] = relationship(back_populates="jobs")
    application: Mapped["Application | None"] = relationship(back_populates="job", uselist=False, passive_deletes=True)

    # Every ORM mutation, including legacy endpoints and import/email paths,
    # advances the version and rejects a stale UPDATE at flush time.
    __mapper_args__ = {"version_id_col": version}

    @property
    def direction(self) -> str:
        from app.job_directions import direction_for
        return direction_for(self.title, self.tags)

class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("job_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default=ApplicationStatus.收藏.value)
    notes: Mapped[str | None] = mapped_column(Text)
    status_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    job: Mapped[Job] = relationship(back_populates="application")

    __mapper_args__ = {"version_id_col": version}

    @property
    def legacy_status(self) -> str | None:
        from app.application_statuses import CURRENT_STATUSES
        return self.status if self.status not in CURRENT_STATUSES else None


class WorkbenchIdempotencyRecord(Base):
    """Idempotency ledger for atomic workbench job creation."""

    __tablename__ = "workbench_idempotency_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[int] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class LLMConfiguration(Base):
    __tablename__ = "llm_configurations"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(200), default="默认配置")
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    api_base: Mapped[str | None] = mapped_column(String(1000))
    use_proxy: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    timeout_seconds: Mapped[int] = mapped_column(default=60)
    credential_id: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    call_count: Mapped[int] = mapped_column(default=0)
    success_count: Mapped[int] = mapped_column(default=0)
    failure_count: Mapped[int] = mapped_column(default=0)
    total_duration_ms: Mapped[int] = mapped_column(default=0)
    # 服务端返回的用量按次累计；不返回 usage 的兼容服务保持为 0。
    total_prompt_tokens: Mapped[int] = mapped_column(default=0)
    total_completion_tokens: Mapped[int] = mapped_column(default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class LLMProxySettings(Base):
    __tablename__ = "llm_proxy_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_llm_proxy_singleton"),)
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    proxy_url: Mapped[str | None] = mapped_column(String(1000))


class LLMUsageEvent(Base):
    __tablename__ = "llm_usage_events"
    __table_args__ = (Index("ix_llm_usage_config_time", "config_id", "recorded_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("llm_configurations.id", ondelete="CASCADE"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    success: Mapped[bool] = mapped_column(Boolean)
    duration_ms: Mapped[int]
    prompt_tokens: Mapped[int]
    completion_tokens: Mapped[int]
    purpose: Mapped[str] = mapped_column(String(20), default="business", server_default="business")


class LLMHealthState(Base):
    __tablename__ = "llm_health_states"
    config_id: Mapped[int] = mapped_column(ForeignKey("llm_configurations.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), default="inherit", server_default="inherit")
    interval_minutes: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(20), default="never", server_default="never")
    error: Mapped[str | None] = mapped_column(String(500))
    elapsed_ms: Mapped[int | None]
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(default=0, server_default="0")


class LLMRoutingSettings(Base):
    __tablename__ = "llm_routing_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_llm_routing_singleton"), CheckConstraint("max_parallel_calls >= 1 AND max_parallel_calls <= 8", name="ck_llm_routing_parallel"))
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    auto_select_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    default_config_id: Mapped[int | None] = mapped_column(ForeignKey("llm_configurations.id", ondelete="SET NULL"))
    email_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_configurations.id", ondelete="SET NULL")
    )
    max_parallel_calls: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_address: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    provider_type: Mapped[str] = mapped_column(String(30), index=True)
    imap_host: Mapped[str] = mapped_column(String(255))
    imap_port: Mapped[int]
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    username: Mapped[str] = mapped_column(String(320))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    check_interval_minutes: Mapped[int] = mapped_column(default=15)
    # 只回看最近 N 天（按 IMAP 内部日期）；None 表示不限，首次同步可能全量回补。
    sync_lookback_days: Mapped[int | None]
    uid_validity: Mapped[int | None]
    last_uid: Mapped[int] = mapped_column(default=0)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_sync_status: Mapped[str] = mapped_column(String(30), default="never")
    last_sync_error: Mapped[str | None] = mapped_column(String(1000))
    credential_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    messages: Mapped[list["EmailMessage"]] = relationship(
        back_populates="account",
        passive_deletes=True,
    )


class EmailMessage(Base):
    __tablename__ = "email_messages"
    __table_args__ = (
        UniqueConstraint(
            "email_account_id",
            "uid_validity",
            "uid",
            name="uq_email_message_account_uid_validity_uid",
        ),
        UniqueConstraint(
            "email_account_id",
            "message_id",
            name="uq_email_message_account_message_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_account_id: Mapped[int] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"),
        index=True,
    )
    message_id: Mapped[str | None] = mapped_column(String(998))
    uid_validity: Mapped[int] = mapped_column(default=0)
    uid: Mapped[int]
    sender: Mapped[str] = mapped_column(String(500))
    subject: Mapped[str] = mapped_column(String(1000))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    body_text: Mapped[str | None] = mapped_column(Text)
    pre_filtered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    llm_processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    llm_classification: Mapped[str | None] = mapped_column(String(50), index=True)
    # 连续 LLM 识别失败次数；达到 MAX_LLM_ATTEMPTS 后自动同步不再重试，人工处理仍可。
    llm_attempt_count: Mapped[int] = mapped_column(default=0)
    processing_error: Mapped[str | None] = mapped_column(String(1000))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    account: Mapped[EmailAccount] = relationship(back_populates="messages")


class EmailCleaningResult(Base):
    """Durable, versioned structured output for one locally stored email.

    ``EmailMessage`` remains the IMAP/deduplication record and
    ``RecruitmentEvent`` remains the legacy one-event compatibility record.
    New parsing writes here first, then creates one or more ``EmailJobEntry``
    rows.  Keeping the result separate means a failed retry is visible and can
    be resumed without refetching the message or silently invoking the model
    again for an already successful version.
    """

    __tablename__ = "email_cleaning_results"
    __table_args__ = (
        UniqueConstraint("email_message_id", name="uq_email_cleaning_message"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_message_id: Mapped[int] = mapped_column(
        ForeignKey("email_messages.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    # Incremented only by explicit reprocess requests.  Ordinary retry keeps
    # the same version and therefore cannot duplicate an AI result.
    version: Mapped[int] = mapped_column(default=1)
    cleaned_body: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    company_name: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(300))
    work_location: Mapped[str | None] = mapped_column(String(500))
    links: Mapped[list | None] = mapped_column(JSON)
    field_evidence: Mapped[dict | list | None] = mapped_column(JSON)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    action_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    actions: Mapped[list | None] = mapped_column(JSON)
    # Preserve the provider payload after local schema validation for audit and
    # future rendering.  It never contains credentials or raw MIME headers.
    structured_data: Mapped[dict | list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(1000))
    attempt_count: Mapped[int] = mapped_column(default=0)
    ai_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    email_message: Mapped[EmailMessage] = relationship()
    job_entries: Mapped[list["EmailJobEntry"]] = relationship(
        back_populates="cleaning_result",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EmailJobEntry.id",
    )


class EmailJobEntry(Base):
    """One job/event extracted from a cleaning result.

    A single email may contain several positions or several recruitment
    events.  Matching state belongs to this row, so one ambiguous position
    does not block a reliable match for another one.
    """

    __tablename__ = "email_job_entries"
    __table_args__ = (
        UniqueConstraint(
            "cleaning_result_id",
            "identity_key",
            name="uq_email_job_entry_cleaning_identity",
        ),
        UniqueConstraint(
            "source_email_id",
            "identity_key",
            name="uq_email_job_entry_source_email_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable hash of the extracted company/title/event facts.  It is scoped to
    # one cleaning result and prevents a repeated provider response from
    # creating a second logical entry.
    identity_key: Mapped[str] = mapped_column(String(128), default="", index=True)
    cleaning_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_cleaning_results.id", ondelete="CASCADE"),
        index=True,
    )
    # Manual facts can be attached directly to an email without creating an
    # AI cleaning snapshot. AI-created entries continue to use cleaning_result_id.
    source_email_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_messages.id", ondelete="CASCADE"), index=True
    )
    facts_source: Mapped[str] = mapped_column(
        String(20), default="ai", server_default="ai", index=True
    )
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), index=True
    )
    # Every entry can point to one compatibility event; unlike the old schema,
    # source_email_id is intentionally non-unique so one mail can have many.
    recruitment_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("recruitment_events.id", ondelete="SET NULL"), index=True
    )
    company_name: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(300))
    work_location: Mapped[str | None] = mapped_column(String(500))
    links: Mapped[list | None] = mapped_column(JSON)
    field_evidence: Mapped[dict | list | None] = mapped_column(JSON)
    event_type: Mapped[str] = mapped_column(String(50), default="OTHER", index=True)
    event_round: Mapped[str | None] = mapped_column(String(50))
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    action_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    actions: Mapped[list | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    match_status: Mapped[str] = mapped_column(String(30), default="unmatched", index=True)
    match_reason: Mapped[str | None] = mapped_column(String(1000))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Human association is an invariant: all automatic matching paths must
    # leave this flag and the selected IDs intact.
    manually_associated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    cleaning_result: Mapped[EmailCleaningResult] = relationship(back_populates="job_entries")
    source_email: Mapped[EmailMessage | None] = relationship()

    __mapper_args__ = {"version_id_col": version}


class EmailArchive(Base):
    """Idempotent local archive state for a cleaning result.

    Archiving is deliberately represented as a recoverable state machine.  No
    remote mailbox operation is implied by this table; provider-specific
    deletion/move can be added later without changing the parsing contract.
    """

    __tablename__ = "email_archives"
    __table_args__ = (
        UniqueConstraint("cleaning_result_id", name="uq_email_archive_cleaning"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cleaning_result_id: Mapped[int] = mapped_column(
        ForeignKey("email_cleaning_results.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class RecruitmentEvent(Base):
    __tablename__ = "recruitment_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"),
        index=True,
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"),
        index=True,
    )
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"),
        index=True,
    )
    suggested_application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"),
        index=True,
    )
    source_email_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_messages.id", ondelete="SET NULL"), index=True
    )
    email_job_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_job_entries.id", ondelete="SET NULL"), index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    company_name: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(300))
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    location: Mapped[str | None] = mapped_column(String(500))
    meeting_url: Mapped[str | None] = mapped_column(String(1000))
    action_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confidence: Mapped[float] = mapped_column(Float)
    ai_summary: Mapped[str] = mapped_column(Text)
    suggested_application_status: Mapped[str | None] = mapped_column(String(30))
    association_status: Mapped[str] = mapped_column(String(30), default="unmatched", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class NotificationPolicy(Base):
    __tablename__ = "notification_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="默认策略")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    desktop_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    push_provider: Mapped[str | None] = mapped_column(String(30))
    push_credential_id: Mapped[str | None] = mapped_column(String(200))
    important_event_types: Mapped[list[str]] = mapped_column(JSON)
    notify_actionable_deadlines: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("recruitment_events.id", ondelete="CASCADE"),
        index=True,
    )
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_policies.id", ondelete="SET NULL"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(1000))
    attempt_count: Mapped[int] = mapped_column(default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    delivery_token: Mapped[str | None] = mapped_column(String(36))
    sending_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
