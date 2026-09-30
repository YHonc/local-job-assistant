"""add singleton LLM configuration"""
from alembic import op
import sqlalchemy as sa

revision = "0002_v1_llm_config"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "llm_configurations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("api_base", sa.String(length=1000)),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("credential_id", sa.String(length=200)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_llm_config_singleton"),
    )

def downgrade():
    op.drop_table("llm_configurations")
