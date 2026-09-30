"""Shared proxy address and per-model opt-in (existing models stay direct)."""
from alembic import op
import sqlalchemy as sa

revision = "0018_llm_local_proxy"
down_revision = "0017_status_and_model_health"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("llm_configurations", sa.Column("use_proxy", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "llm_proxy_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proxy_url", sa.String(1000)),
        sa.CheckConstraint("id = 1", name="ck_llm_proxy_singleton"),
    )


def downgrade():
    conn = op.get_bind()
    if conn.scalar(sa.text("SELECT COUNT(*) FROM llm_configurations WHERE use_proxy = 1")) or conn.scalar(sa.text("SELECT COUNT(*) FROM llm_proxy_settings WHERE proxy_url IS NOT NULL")):
        raise RuntimeError("Clear proxy settings and model opt-ins before downgrade")
    op.drop_table("llm_proxy_settings")
    with op.batch_alter_table("llm_configurations") as batch:
        batch.drop_column("use_proxy")
