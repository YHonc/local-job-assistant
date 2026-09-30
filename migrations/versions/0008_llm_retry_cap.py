"""per-message LLM attempt counter to cap automatic retries"""
from alembic import op
import sqlalchemy as sa

revision = "0008_llm_retry"
down_revision = "0007_sync_lookback"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    columns = {c["name"] for c in sa.inspect(conn).get_columns("email_messages")}
    if "llm_attempt_count" in columns:
        return
    with op.batch_alter_table("email_messages") as b:
        b.add_column(
            sa.Column("llm_attempt_count", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade():
    conn = op.get_bind()
    columns = {c["name"] for c in sa.inspect(conn).get_columns("email_messages")}
    if "llm_attempt_count" not in columns:
        return
    with op.batch_alter_table("email_messages") as b:
        b.drop_column("llm_attempt_count")
