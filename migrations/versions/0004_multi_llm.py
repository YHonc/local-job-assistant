"""migrate singleton LLM config to multi-config routing"""
from alembic import op
import sqlalchemy as sa

revision = "0004_multi_llm"
down_revision = "0003_v2_v3"
branch_labels = None
depends_on = None

def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    with op.batch_alter_table("llm_configurations") as b:
        constraints = {c["name"] for c in inspector.get_check_constraints("llm_configurations")}
        if "ck_llm_config_singleton" in constraints:
            b.drop_constraint("ck_llm_config_singleton", type_="check")
        b.add_column(sa.Column("display_name", sa.String(200), nullable=True))
        b.add_column(sa.Column("enabled", sa.Boolean(), nullable=True, server_default=sa.true()))
        b.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
        for n in ("call_count", "success_count", "failure_count", "total_duration_ms"):
            b.add_column(sa.Column(n, sa.Integer(), nullable=True, server_default="0"))
        b.add_column(sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    conn.execute(sa.text("UPDATE llm_configurations SET display_name='默认配置', enabled=1, created_at=updated_at, call_count=0, success_count=0, failure_count=0, total_duration_ms=0 WHERE display_name IS NULL"))
    with op.batch_alter_table("llm_configurations") as b:
        for n in ("display_name", "enabled", "created_at", "call_count", "success_count", "failure_count", "total_duration_ms"):
            b.alter_column(n, nullable=False)
    op.create_table("llm_routing_settings", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("auto_select_enabled", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("default_config_id", sa.Integer(), sa.ForeignKey("llm_configurations.id", ondelete="SET NULL")), sa.Column("max_parallel_calls", sa.Integer(), nullable=False, server_default="1"), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("id = 1", name="ck_llm_routing_singleton"), sa.CheckConstraint("max_parallel_calls >= 1 AND max_parallel_calls <= 8", name="ck_llm_routing_parallel"))
    conn.execute(sa.text("INSERT INTO llm_routing_settings (id,auto_select_enabled,default_config_id,max_parallel_calls,updated_at) SELECT 1,0,id,1,updated_at FROM llm_configurations WHERE id=1"))

def downgrade():
    conn = op.get_bind()
    count = conn.execute(sa.text("SELECT COUNT(*) FROM llm_configurations")).scalar_one()
    if count > 1:
        raise RuntimeError("cannot downgrade multi-LLM configuration with more than one row")
    if count == 1:
        config_id = conn.execute(sa.text("SELECT id FROM llm_configurations")).scalar_one()
        if config_id != 1:
            raise RuntimeError("cannot downgrade multi-LLM configuration with a row whose id is not 1")
    op.drop_table("llm_routing_settings")
    with op.batch_alter_table("llm_configurations") as b:
        b.create_check_constraint("ck_llm_config_singleton", "id = 1")
        for n in ("last_used_at", "total_duration_ms", "failure_count", "success_count", "call_count", "created_at", "enabled", "display_name"):
            b.drop_column(n)
