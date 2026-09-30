"""Record dated LLM usage without inventing history from cumulative counters."""

from alembic import op
import sqlalchemy as sa

revision = "0012_llm_usage_events"
down_revision = "0011_job_published_at"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("llm_configurations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
    )
    op.create_index("ix_llm_usage_config_time", "llm_usage_events", ["config_id", "recorded_at"])


def downgrade():
    op.drop_index("ix_llm_usage_config_time", table_name="llm_usage_events")
    op.drop_table("llm_usage_events")
