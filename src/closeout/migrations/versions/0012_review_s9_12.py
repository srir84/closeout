"""Review fixes for S9-S12.

A town request holds its permit until its workflow has ended (after the 60-day late window), not just until it
says no_reply: otherwise a new request for the permit could block the first one's late reply.
"""

from alembic import op

revision = "0012"
down_revision = "0011"


def upgrade() -> None:
    op.execute("""
    ALTER TABLE town_requests ADD COLUMN ended_at timestamptz;
    UPDATE town_requests SET ended_at = closed_at WHERE status = 'closed';
    DROP INDEX town_requests_open;
    CREATE UNIQUE INDEX town_requests_open ON town_requests (permit_pk) WHERE ended_at IS NULL;
    ALTER TABLE privacy_requests ADD COLUMN email_hmac text;
    """)
