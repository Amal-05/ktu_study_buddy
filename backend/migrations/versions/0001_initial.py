from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None


def upgrade():
    op.create_table("subjects", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("code", sa.String(40), nullable=False), sa.Column("name", sa.String, nullable=False),
                    sa.Column("semester", sa.Integer, nullable=False), sa.Column("branch", sa.String, nullable=False),
                    sa.Column("exam_date", sa.String, nullable=False), sa.Column("outline", sa.JSON, nullable=False))
    op.create_table("uploads", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("subject_id", sa.Integer, sa.ForeignKey("subjects.id", ondelete="CASCADE")),
                    sa.Column("name", sa.String, nullable=False), sa.Column("tag", sa.String, nullable=False),
                    sa.Column("pages", sa.JSON, nullable=False), sa.Column("metadata_json", sa.JSON, nullable=False))
    op.create_table("chunks", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("upload_id", sa.Integer, sa.ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("page", sa.Integer, nullable=False), sa.Column("text", sa.String, nullable=False),
                    sa.Column("module", sa.String), sa.Column("topic", sa.String), sa.Column("vector", sa.LargeBinary))
    op.create_index("ix_chunks_upload_id", "chunks", ["upload_id"])
    op.create_table("providers", sa.Column("id", sa.Integer, primary_key=True),
                    *[sa.Column(name, sa.String, nullable=False) for name in ("kind", "name", "base_url", "model", "last_four", "status")],
                    sa.Column("secret", sa.LargeBinary, nullable=False), sa.Column("position", sa.Integer, nullable=False),
                    sa.Column("enabled", sa.Boolean, nullable=False), sa.Column("cooldown", sa.Float, nullable=False),
                    sa.Column("calls", sa.Integer, nullable=False), sa.Column("tokens", sa.Integer, nullable=False),
                    sa.Column("limits", sa.JSON, nullable=False))
    op.create_table("records", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("kind", sa.String, nullable=False), sa.Column("key", sa.String, nullable=False),
                    sa.Column("data", sa.JSON, nullable=False), sa.UniqueConstraint("kind", "key"))


def downgrade():
    for table in ("records", "providers", "chunks", "uploads", "subjects"):
        op.drop_table(table)
