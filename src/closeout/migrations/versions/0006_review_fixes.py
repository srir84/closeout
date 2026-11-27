"""Review fixes after S4: failed model calls are logged too."""

from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    op.execute("""
    ALTER TABLE model_calls ADD COLUMN error text, ALTER COLUMN replay_key DROP NOT NULL,
        ALTER COLUMN model_digest DROP NOT NULL;
    """)
