"""add email sync, recruitment events, and notifications"""

from alembic import op
import sqlalchemy as sa


revision = "0003_v2_v3"
down_revision = "0002_v1_llm_config"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_address", sa.String(320), nullable=False),
        sa.Column("provider_type", sa.String(30), nullable=False),
        sa.Column("imap_host", sa.String(255), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False),
        sa.Column("use_ssl", sa.Boolean(), nullable=False),
        sa.Column("username", sa.String(320), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("check_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("uid_validity", sa.Integer()),
        sa.Column("last_uid", sa.Integer(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("sync_lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_sync_status", sa.String(30), nullable=False),
        sa.Column("last_sync_error", sa.String(1000)),
        sa.Column("credential_id", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_email_accounts_email_address", "email_accounts", ["email_address"], unique=True)
    op.create_index("ix_email_accounts_provider_type", "email_accounts", ["provider_type"])
    op.create_index("ix_email_accounts_enabled", "email_accounts", ["enabled"])
    op.create_index("ix_email_accounts_sync_lease_until", "email_accounts", ["sync_lease_until"])

    op.create_table(
        "email_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_account_id", sa.Integer(), sa.ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.String(998)),
        sa.Column("uid_validity", sa.Integer(), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("sender", sa.String(500), nullable=False),
        sa.Column("subject", sa.String(1000), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("body_text", sa.Text()),
        sa.Column("pre_filtered", sa.Boolean(), nullable=False),
        sa.Column("llm_processed", sa.Boolean(), nullable=False),
        sa.Column("llm_classification", sa.String(50)),
        sa.Column("processing_error", sa.String(1000)),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email_account_id", "uid_validity", "uid", name="uq_email_message_account_uid_validity_uid"),
        sa.UniqueConstraint("email_account_id", "message_id", name="uq_email_message_account_message_id"),
    )
    op.create_index("ix_email_messages_email_account_id", "email_messages", ["email_account_id"])
    op.create_index("ix_email_messages_received_at", "email_messages", ["received_at"])
    op.create_index("ix_email_messages_pre_filtered", "email_messages", ["pre_filtered"])
    op.create_index("ix_email_messages_llm_processed", "email_messages", ["llm_processed"])
    op.create_index("ix_email_messages_llm_classification", "email_messages", ["llm_classification"])

    op.create_table(
        "recruitment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="SET NULL")),
        sa.Column("suggested_application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="SET NULL")),
        sa.Column("source_email_id", sa.Integer(), sa.ForeignKey("email_messages.id", ondelete="SET NULL"), unique=True),
        sa.Column("company_name", sa.String(200)),
        sa.Column("job_title", sa.String(300)),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True)),
        sa.Column("deadline", sa.DateTime(timezone=True)),
        sa.Column("location", sa.String(500)),
        sa.Column("meeting_url", sa.String(1000)),
        sa.Column("action_required", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=False),
        sa.Column("suggested_application_status", sa.String(30)),
        sa.Column("association_status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("company_id", "job_id", "application_id", "suggested_application_id", "event_type", "event_time", "deadline", "action_required", "association_status"):
        op.create_index(f"ix_recruitment_events_{column}", "recruitment_events", [column])
    op.create_index("ix_recruitment_events_source_email_id", "recruitment_events", ["source_email_id"], unique=True)

    op.create_table(
        "notification_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("desktop_enabled", sa.Boolean(), nullable=False),
        sa.Column("push_enabled", sa.Boolean(), nullable=False),
        sa.Column("push_provider", sa.String(30)),
        sa.Column("push_credential_id", sa.String(200)),
        sa.Column("important_event_types", sa.JSON(), nullable=False),
        sa.Column("notify_actionable_deadlines", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_notification_policy_singleton"),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("recruitment_events.id", ondelete="CASCADE")),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(1000)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("delivery_token", sa.String(36)),
        sa.Column("sending_until", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notifications_event_id", "notifications", ["event_id"])
    op.create_index("ix_notifications_provider", "notifications", ["provider"])
    op.create_index("ix_notifications_status", "notifications", ["status"])
    op.create_index("ix_notifications_next_retry_at", "notifications", ["next_retry_at"])
    op.create_index("ix_notifications_sending_until", "notifications", ["sending_until"])
    op.create_index("ix_notifications_idempotency_key", "notifications", ["idempotency_key"], unique=True)


def downgrade():
    op.drop_table("notifications")
    op.drop_table("notification_policies")
    op.drop_table("recruitment_events")
    op.drop_table("email_messages")
    op.drop_table("email_accounts")
