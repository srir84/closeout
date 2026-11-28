"""S12: privacy requests (IDs only)."""

from alembic import op

revision = "0011"
down_revision = "0010"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE privacy_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        person_id uuid NOT NULL REFERENCES people(id),
        requested_by uuid REFERENCES accounts(id),
        status text NOT NULL DEFAULT 'running',
        finished_at timestamptz
    );
    """)
