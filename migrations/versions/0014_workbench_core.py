"""Add job organization fields, optimistic versions, and workbench idempotency."""

from alembic import op
import sqlalchemy as sa


revision = "0014_workbench_core"
down_revision = "0013_email_cleaning_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "jobs",
        sa.Column("category_review_status", sa.String(20), nullable=False, server_default="pending"),
    )
    op.add_column(
        "jobs",
        sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "jobs",
        sa.Column("organize_status", sa.String(20), nullable=False, server_default="pending"),
    )
    op.add_column("jobs", sa.Column("salary_text", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_jobs_category_review_status", "jobs", ["category_review_status"])
    op.create_index("ix_jobs_organize_status", "jobs", ["organize_status"])

    op.add_column(
        "applications",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "workbench_idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        # Keep a tombstone-like reference without a foreign key so deleting a
        # job cannot silently make a previously consumed key reusable.
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_workbench_idempotency_key"),
    )
    op.create_index(
        "ix_workbench_idempotency_records_job_id",
        "workbench_idempotency_records",
        ["job_id"],
    )


def downgrade():
    bind = op.get_bind()
    existing_keys = bind.execute(
        sa.text("SELECT COUNT(*) FROM workbench_idempotency_records")
    ).scalar_one()
    if existing_keys:
        raise RuntimeError("Cannot downgrade while workbench idempotency records exist")

    meaningful_job_data = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM jobs WHERE category_review_status != 'pending' "
            "OR tags != '[]' OR organize_status != 'pending' "
            "OR salary_text IS NOT NULL OR notes IS NOT NULL"
        )
    ).scalar_one()
    if meaningful_job_data:
        raise RuntimeError("Cannot downgrade while workbench job fields contain data")

    op.drop_index("ix_workbench_idempotency_records_job_id", table_name="workbench_idempotency_records")
    op.drop_table("workbench_idempotency_records")
    op.drop_column("applications", "version")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_organize_status")
        batch.drop_index("ix_jobs_category_review_status")
        batch.drop_column("version")
        batch.drop_column("notes")
        batch.drop_column("salary_text")
        batch.drop_column("organize_status")
        batch.drop_column("tags")
        batch.drop_column("category_review_status")
