"""Review fixes after S7: a job's workflow closing is recorded, so links can say the request has closed."""

from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN closed_at timestamptz")
