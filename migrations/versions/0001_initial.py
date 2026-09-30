from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("companies", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(200), nullable=False), sa.Column("aliases", sa.JSON()), sa.Column("notes", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_companies_name", "companies", ["name"], unique=True)
    op.create_table("jobs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False), sa.Column("title", sa.String(300), nullable=False), sa.Column("location", sa.String(300)), sa.Column("employment_type", sa.String(100)), sa.Column("description", sa.Text()), sa.Column("source_url", sa.String(1000)), sa.Column("source_website", sa.String(200)), sa.Column("original_title", sa.String(500)), sa.Column("structured_data", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("applications", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("notes", sa.Text()), sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("job_id"))
    op.create_table("settings", sa.Column("key", sa.String(100), primary_key=True), sa.Column("value", sa.Text()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table("applications")
    op.drop_table("jobs")
    op.drop_index("ix_companies_name", table_name="companies")
    op.drop_table("companies")
    op.drop_table("settings")
