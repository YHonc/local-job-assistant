"""add nullable job publication date"""

from alembic import op
import sqlalchemy as sa


revision = "0011_job_published_at"
down_revision = "0010_job_category"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    columns = {column["name"] for column in sa.inspect(conn).get_columns("jobs")}
    if "published_at" not in columns:
        with op.batch_alter_table("jobs") as batch:
            batch.add_column(sa.Column("published_at", sa.Date(), nullable=True))

    indexes = {index["name"] for index in sa.inspect(conn).get_indexes("jobs")}
    if "ix_jobs_published_at" not in indexes:
        op.create_index("ix_jobs_published_at", "jobs", ["published_at"], unique=False)


def downgrade():
    conn = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(conn).get_indexes("jobs")}
    if "ix_jobs_published_at" in indexes:
        op.drop_index("ix_jobs_published_at", table_name="jobs")
    columns = {column["name"] for column in sa.inspect(conn).get_columns("jobs")}
    if "published_at" in columns:
        with op.batch_alter_table("jobs") as batch:
            batch.drop_column("published_at")
