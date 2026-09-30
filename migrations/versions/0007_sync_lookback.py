"""optional per-account lookback window limiting how far back IMAP sync reads"""
from alembic import op
import sqlalchemy as sa

revision = "0007_sync_lookback"
down_revision = "0006_email_llm"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    columns = {c["name"] for c in sa.inspect(conn).get_columns("email_accounts")}
    if "sync_lookback_days" in columns:
        return
    with op.batch_alter_table("email_accounts") as b:
        b.add_column(sa.Column("sync_lookback_days", sa.Integer(), nullable=True))


def downgrade():
    conn = op.get_bind()
    columns = {c["name"] for c in sa.inspect(conn).get_columns("email_accounts")}
    if "sync_lookback_days" not in columns:
        return
    with op.batch_alter_table("email_accounts") as b:
        b.drop_column("sync_lookback_days")
