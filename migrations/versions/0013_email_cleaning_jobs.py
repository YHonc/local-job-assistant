"""Add versioned email cleaning snapshots, multi-job entries and archive state."""

from alembic import op
import sqlalchemy as sa


revision = "0013_email_cleaning_jobs"
down_revision = "0012_llm_usage_events"
branch_labels = None
depends_on = None


def _remove_legacy_event_source_unique(bind):
    """Rebuild the SQLite event table without the old one-email unique key.

    Revision 0003 created both an unnamed unique constraint and a named unique
    index on ``source_email_id``.  Reflection plus batch recreation removes
    both forms safely while preserving existing event rows and indexes.
    """

    metadata = sa.MetaData()
    table = sa.Table("recruitment_events", metadata, autoload_with=bind)
    for constraint in list(table.constraints):
        if isinstance(constraint, sa.UniqueConstraint) and {
            column.name for column in constraint.columns
        } == {"source_email_id"}:
            table.constraints.remove(constraint)
    for index in list(table.indexes):
        if index.name == "ix_recruitment_events_source_email_id":
            table.indexes.remove(index)

    with op.batch_alter_table(
        "recruitment_events", recreate="always", copy_from=table
    ) as batch:
        batch.add_column(
            sa.Column(
                "email_job_entry_id",
                sa.Integer(),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_recruitment_events_email_job_entry",
            "email_job_entries",
            ["email_job_entry_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.add_column(
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.create_index(
            "ix_recruitment_events_source_email_id", ["source_email_id"]
        )
        batch.create_index("ix_recruitment_events_email_job_entry_id", ["email_job_entry_id"])
        batch.create_index("ix_recruitment_events_active", ["active"])


def upgrade():
    op.create_table(
        "email_cleaning_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_message_id", sa.Integer(), sa.ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cleaned_body", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("company_name", sa.String(200)),
        sa.Column("job_title", sa.String(300)),
        sa.Column("work_location", sa.String(500)),
        sa.Column("links", sa.JSON()),
        sa.Column("field_evidence", sa.JSON()),
        sa.Column("event_time", sa.DateTime(timezone=True)),
        sa.Column("deadline", sa.DateTime(timezone=True)),
        sa.Column("action_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actions", sa.JSON()),
        sa.Column("structured_data", sa.JSON()),
        sa.Column("error", sa.String(1000)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_processed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email_message_id", name="uq_email_cleaning_message"),
    )
    op.create_index("ix_email_cleaning_status", "email_cleaning_results", ["status"])
    op.create_index("ix_email_cleaning_results_email_message_id", "email_cleaning_results", ["email_message_id"], unique=True)
    op.create_index("ix_email_cleaning_results_event_time", "email_cleaning_results", ["event_time"])
    op.create_index("ix_email_cleaning_results_deadline", "email_cleaning_results", ["deadline"])
    op.create_table(
        "email_job_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("identity_key", sa.String(128), nullable=False, server_default=""),
        sa.Column("cleaning_result_id", sa.Integer(), sa.ForeignKey("email_cleaning_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="SET NULL")),
        sa.Column("recruitment_event_id", sa.Integer(), sa.ForeignKey("recruitment_events.id", ondelete="SET NULL")),
        sa.Column("company_name", sa.String(200)),
        sa.Column("job_title", sa.String(300)),
        sa.Column("work_location", sa.String(500)),
        sa.Column("links", sa.JSON()),
        sa.Column("field_evidence", sa.JSON()),
        sa.Column("event_type", sa.String(50), nullable=False, server_default="OTHER"),
        sa.Column("event_time", sa.DateTime(timezone=True)),
        sa.Column("deadline", sa.DateTime(timezone=True)),
        sa.Column("action_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actions", sa.JSON()),
        sa.Column("summary", sa.Text()),
        sa.Column("match_status", sa.String(30), nullable=False, server_default="unmatched"),
        sa.Column("match_reason", sa.String(1000)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("manually_associated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cleaning_result_id", "identity_key", name="uq_email_job_entry_cleaning_identity"),
    )
    for name, columns in (
        ("ix_email_job_entries_identity_key", ["identity_key"]),
        ("ix_email_job_entries_cleaning_result_id", ["cleaning_result_id"]),
        ("ix_email_job_entries_company_id", ["company_id"]),
        ("ix_email_job_entries_job_id", ["job_id"]),
        ("ix_email_job_entries_application_id", ["application_id"]),
        ("ix_email_job_entries_recruitment_event_id", ["recruitment_event_id"]),
        ("ix_email_job_entries_event_type", ["event_type"]),
        ("ix_email_job_entries_event_time", ["event_time"]),
        ("ix_email_job_entries_deadline", ["deadline"]),
        ("ix_email_job_entries_action_required", ["action_required"]),
        ("ix_email_job_entries_match_status", ["match_status"]),
        ("ix_email_job_entries_active", ["active"]),
        ("ix_email_job_entries_manually_associated", ["manually_associated"]),
    ):
        op.create_index(name, "email_job_entries", columns)

    op.create_table(
        "email_archives",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cleaning_result_id", sa.Integer(), sa.ForeignKey("email_cleaning_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cleaning_result_id", name="uq_email_archive_cleaning"),
    )
    op.create_index("ix_email_archives_status", "email_archives", ["status"])
    op.create_index("ix_email_archives_cleaning_result_id", "email_archives", ["cleaning_result_id"], unique=True)
    _remove_legacy_event_source_unique(op.get_bind())


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM email_cleaning_results")).scalar():
        raise RuntimeError("Cannot downgrade email cleaning data; export it before removing revision 0013")
    if bind.execute(sa.text("SELECT source_email_id FROM recruitment_events WHERE source_email_id IS NOT NULL GROUP BY source_email_id HAVING COUNT(*) > 1 LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade multiple events for one email")
    with op.batch_alter_table("recruitment_events", recreate="always") as batch:
        batch.drop_constraint("fk_recruitment_events_email_job_entry", type_="foreignkey")
        batch.drop_index("ix_recruitment_events_email_job_entry_id")
        batch.drop_index("ix_recruitment_events_active")
        batch.drop_index("ix_recruitment_events_source_email_id")
        batch.drop_column("email_job_entry_id")
        batch.drop_column("active")
        batch.create_index("ix_recruitment_events_source_email_id", ["source_email_id"], unique=True)
    op.drop_table("email_archives")
    op.drop_table("email_job_entries")
    op.drop_table("email_cleaning_results")
