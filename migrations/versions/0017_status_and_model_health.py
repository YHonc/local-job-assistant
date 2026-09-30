"""Progress audit trail and persistent model health checks.

Historical progress values are intentionally preserved for manual review.
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_status_and_model_health"
down_revision = "0016_email_progress_suggestions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("application_status_history", sa.Column("result_version", sa.Integer()))
    op.create_table(
        "llm_health_states",
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("llm_configurations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("mode", sa.String(20), nullable=False, server_default="inherit"),
        sa.Column("interval_minutes", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="never"),
        sa.Column("error", sa.String(500)),
        sa.Column("elapsed_ms", sa.Integer()),
        sa.Column("last_test_at", sa.DateTime(timezone=True)),
        sa.Column("next_test_at", sa.DateTime(timezone=True)),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("llm_usage_events", sa.Column("purpose", sa.String(20), nullable=False, server_default="business"))


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM application_status_history WHERE result_version IS NOT NULL")) or connection.scalar(sa.text("SELECT COUNT(*) FROM llm_health_states")) or connection.scalar(sa.text("SELECT COUNT(*) FROM llm_usage_events WHERE purpose <> 'business'")):
        raise RuntimeError("Cannot discard status history or model health settings; export and review before downgrade")
    with op.batch_alter_table("llm_usage_events") as batch:
        batch.drop_column("purpose")
    op.drop_table("llm_health_states")
    with op.batch_alter_table("application_status_history") as batch:
        batch.drop_column("result_version")
