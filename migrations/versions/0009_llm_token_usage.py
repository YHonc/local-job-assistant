"""cumulative prompt/completion token counters for LLM configurations"""
from alembic import op
import sqlalchemy as sa

revision = "0009_llm_tokens"
down_revision = "0008_llm_retry"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    columns = {c["name"] for c in sa.inspect(conn).get_columns("llm_configurations")}
    with op.batch_alter_table("llm_configurations") as b:
        if "total_prompt_tokens" not in columns:
            b.add_column(
                sa.Column("total_prompt_tokens", sa.Integer(), nullable=False, server_default="0")
            )
        if "total_completion_tokens" not in columns:
            b.add_column(
                sa.Column("total_completion_tokens", sa.Integer(), nullable=False, server_default="0")
            )


def downgrade():
    conn = op.get_bind()
    columns = {c["name"] for c in sa.inspect(conn).get_columns("llm_configurations")}
    with op.batch_alter_table("llm_configurations") as b:
        if "total_completion_tokens" in columns:
            b.drop_column("total_completion_tokens")
        if "total_prompt_tokens" in columns:
            b.drop_column("total_prompt_tokens")
