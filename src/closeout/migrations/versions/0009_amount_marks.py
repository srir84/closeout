"""Reads have a purpose (invoice, town_reply, amount_marks): one row per document, reader and purpose."""

from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade() -> None:
    op.execute("""
    ALTER TABLE reads ADD COLUMN purpose text NOT NULL DEFAULT 'invoice';
    ALTER TABLE reads DROP CONSTRAINT reads_document_id_reader_key;
    ALTER TABLE reads ADD CONSTRAINT reads_document_reader_purpose UNIQUE (document_id, reader, purpose);
    """)
