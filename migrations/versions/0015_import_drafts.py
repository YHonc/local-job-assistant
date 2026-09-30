"""Persist browser/text import drafts and explicit confirmation state."""

from alembic import op
import sqlalchemy as sa


revision = "0015_import_drafts"
down_revision = "0014_workbench_core"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "import_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_url", sa.String(1000)),
        sa.Column("source_website", sa.String(200)),
        sa.Column("original_title", sa.String(500)),
        sa.Column("captured_text", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "user_fields",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("suggested_fields", sa.JSON()),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column(
            "confirmed_job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
        ),
        sa.Column("confirmation_fingerprint", sa.String(64)),
        sa.Column("error", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'extracted', 'confirmed')",
            name="ck_import_drafts_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_import_drafts_version"),
    )
    op.create_index(
        "ix_import_drafts_idempotency_key",
        "import_drafts",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_import_drafts_confirmed_job_id", "import_drafts", ["confirmed_job_id"]
    )
    op.create_index(
        "ix_import_drafts_status_created",
        "import_drafts",
        ["status", "created_at"],
    )


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM import_drafts")).scalar():
        raise RuntimeError(
            "Cannot downgrade persisted import drafts; export or preserve them before removing revision 0015"
        )
    op.drop_index("ix_import_drafts_status_created", table_name="import_drafts")
    op.drop_index("ix_import_drafts_confirmed_job_id", table_name="import_drafts")
    op.drop_index("ix_import_drafts_idempotency_key", table_name="import_drafts")
    op.drop_table("import_drafts")
