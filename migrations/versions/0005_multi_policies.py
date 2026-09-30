"""multiple notification policies with per-policy notification history"""
from alembic import op
import sqlalchemy as sa

revision = "0005_multi_policies"
down_revision = "0004_multi_llm"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    constraints = {c["name"] for c in inspector.get_check_constraints("notification_policies")}
    with op.batch_alter_table("notification_policies") as b:
        if "ck_notification_policy_singleton" in constraints:
            b.drop_constraint("ck_notification_policy_singleton", type_="check")
        b.add_column(sa.Column("name", sa.String(200), nullable=True))
        b.add_column(sa.Column("enabled", sa.Boolean(), nullable=True, server_default=sa.true()))
        b.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    conn.execute(
        sa.text(
            "UPDATE notification_policies SET name='默认策略', enabled=1, created_at=updated_at WHERE name IS NULL"
        )
    )
    with op.batch_alter_table("notification_policies") as b:
        for column in ("name", "enabled", "created_at"):
            b.alter_column(column, nullable=False)
    with op.batch_alter_table("notifications") as b:
        b.add_column(sa.Column("policy_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_notifications_policy_id",
            "notification_policies",
            ["policy_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    conn = op.get_bind()
    count = conn.execute(sa.text("SELECT COUNT(*) FROM notification_policies")).scalar_one()
    if count > 1:
        raise RuntimeError("cannot downgrade multi-policy configuration with more than one row")
    with op.batch_alter_table("notifications") as b:
        b.drop_constraint("fk_notifications_policy_id", type_="foreignkey")
        b.drop_column("policy_id")
    with op.batch_alter_table("notification_policies") as b:
        for column in ("created_at", "enabled", "name"):
            b.drop_column(column)
        b.create_check_constraint("ck_notification_policy_singleton", "id = 1")
