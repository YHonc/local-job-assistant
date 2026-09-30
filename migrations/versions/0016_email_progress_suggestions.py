"""Add manual email facts and explicit progress suggestions."""

from alembic import op
import sqlalchemy as sa


revision = "0016_email_progress_suggestions"
down_revision = "0015_import_drafts"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("email_job_entries", recreate="always") as batch:
        batch.alter_column(
            "cleaning_result_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch.add_column(sa.Column("source_email_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("facts_source", sa.String(20), nullable=False, server_default="ai")
        )
        batch.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(sa.Column("event_round", sa.String(50), nullable=True))
        batch.create_foreign_key(
            "fk_email_job_entries_source_email_id_email_messages",
            "email_messages",
            ["source_email_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_email_job_entries_source_email_id", ["source_email_id"])
        batch.create_index("ix_email_job_entries_facts_source", ["facts_source"])
        batch.create_unique_constraint(
            "uq_email_job_entry_source_email_identity",
            ["source_email_id", "identity_key"],
        )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE email_job_entries SET source_email_id = "
            "(SELECT email_message_id FROM email_cleaning_results "
            "WHERE email_cleaning_results.id = email_job_entries.cleaning_result_id) "
            "WHERE cleaning_result_id IS NOT NULL AND source_email_id IS NULL"
        )
    )

    op.create_table(
        "email_progress_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "email_job_entry_id",
            sa.Integer(),
            sa.ForeignKey("email_job_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_email_id",
            sa.Integer(),
            sa.ForeignKey("email_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cleaning_result_id",
            sa.Integer(),
            sa.ForeignKey("email_cleaning_results.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cleaning_version", sa.Integer()),
        sa.Column("entry_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_key", sa.String(200), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="SET NULL")),
        sa.Column("application_version", sa.Integer()),
        sa.Column("expected_application_status", sa.String(30)),
        sa.Column("target_status", sa.String(30)),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_round", sa.String(50)),
        sa.Column("evidence", sa.JSON()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("accept_expected_version", sa.Integer()),
        sa.Column("result_application_status", sa.String(30)),
        sa.Column("result_application_version", sa.Integer()),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "email_job_entry_id",
            "entry_version",
            "snapshot_key",
            name="uq_progress_suggestion_entry_app_snapshot",
        ),
    )
    for name, table, columns in (
        ("ix_email_progress_suggestions_email_job_entry_id", "email_progress_suggestions", ["email_job_entry_id"]),
        ("ix_email_progress_suggestions_source_email_id", "email_progress_suggestions", ["source_email_id"]),
        ("ix_email_progress_suggestions_cleaning_result_id", "email_progress_suggestions", ["cleaning_result_id"]),
        ("ix_email_progress_suggestions_application_id", "email_progress_suggestions", ["application_id"]),
        ("ix_email_progress_suggestions_status", "email_progress_suggestions", ["status"]),
    ):
        op.create_index(name, table, columns)

    op.create_table(
        "email_manual_fact_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_email_id",
            sa.Integer(),
            sa.ForeignKey("email_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "email_job_entry_id",
            sa.Integer(),
            sa.ForeignKey("email_job_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_email_id", "idempotency_key", name="uq_manual_fact_request_key"
        ),
    )
    op.create_index(
        "ix_email_manual_fact_requests_source_email_id",
        "email_manual_fact_requests",
        ["source_email_id"],
    )
    op.create_index(
        "ix_email_manual_fact_requests_email_job_entry_id",
        "email_manual_fact_requests",
        ["email_job_entry_id"],
    )

    op.create_table(
        "application_status_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("before_status", sa.String(30), nullable=False),
        sa.Column("after_status", sa.String(30), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column(
            "suggestion_id",
            sa.Integer(),
            sa.ForeignKey("email_progress_suggestions.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_application_status_history_application_id",
        "application_status_history",
        ["application_id"],
    )
    op.create_index(
        "ix_application_status_history_suggestion_id",
        "application_status_history",
        ["suggestion_id"],
    )
    op.create_index(
        "ix_application_status_history_application_time",
        "application_status_history",
        ["application_id", "created_at"],
    )


def downgrade():
    bind = op.get_bind()
    for table in (
        "application_status_history",
        "email_manual_fact_requests",
        "email_progress_suggestions",
    ):
        if bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError(
                f"Cannot downgrade {revision} while {table} contains user data"
            )
    if bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM email_job_entries "
            "WHERE facts_source = 'manual' OR cleaning_result_id IS NULL"
        )
    ).scalar():
        raise RuntimeError(
            "Cannot downgrade manual email entries; export them before removing revision 0016"
        )

    op.drop_index("ix_application_status_history_application_time", table_name="application_status_history")
    op.drop_index("ix_application_status_history_suggestion_id", table_name="application_status_history")
    op.drop_index("ix_application_status_history_application_id", table_name="application_status_history")
    op.drop_table("application_status_history")
    op.drop_index("ix_email_manual_fact_requests_email_job_entry_id", table_name="email_manual_fact_requests")
    op.drop_index("ix_email_manual_fact_requests_source_email_id", table_name="email_manual_fact_requests")
    op.drop_table("email_manual_fact_requests")
    for name in (
        "ix_email_progress_suggestions_status",
        "ix_email_progress_suggestions_application_id",
        "ix_email_progress_suggestions_cleaning_result_id",
        "ix_email_progress_suggestions_source_email_id",
        "ix_email_progress_suggestions_email_job_entry_id",
    ):
        op.drop_index(name, table_name="email_progress_suggestions")
    op.drop_table("email_progress_suggestions")

    with op.batch_alter_table("email_job_entries", recreate="always") as batch:
        batch.drop_constraint("uq_email_job_entry_source_email_identity", type_="unique")
        batch.drop_index("ix_email_job_entries_source_email_id")
        batch.drop_index("ix_email_job_entries_facts_source")
        batch.drop_constraint(
            "fk_email_job_entries_source_email_id_email_messages", type_="foreignkey"
        )
        batch.drop_column("event_round")
        batch.drop_column("version")
        batch.drop_column("facts_source")
        batch.drop_column("source_email_id")
        batch.alter_column(
            "cleaning_result_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
