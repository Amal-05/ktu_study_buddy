from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"


def upgrade():
    op.create_table("blobs", sa.Column("key", sa.String(), primary_key=True),
                    sa.Column("content", sa.LargeBinary(), nullable=False))


def downgrade():
    op.drop_table("blobs")
