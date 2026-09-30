"""optional dedicated LLM configuration for email classification"""
from alembic import op
import sqlalchemy as sa

revision = "0006_email_llm"
down_revision = "0005_multi_policies"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    columns = {c["name"] for c in sa.inspect(conn).get_columns("llm_routing_settings")}
    if "email_config_id" in columns:
        return
    with op.batch_alter_table("llm_routing_settings") as b:
        b.add_column(sa.Column("email_config_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_llm_routing_email_config_id",
            "llm_configurations",
            ["email_config_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    conn = op.get_bind()
    columns = {c["name"] for c in sa.inspect(conn).get_columns("llm_routing_settings")}
    if "email_config_id" not in columns:
        return
    with op.batch_alter_table("llm_routing_settings") as b:
        b.drop_column("email_config_id")
