"""S4: import_batches, review_items."""

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE import_batches (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        account_id uuid NOT NULL REFERENCES accounts(id),
        status text NOT NULL DEFAULT 'saved' CHECK (status IN ('saved', 'running', 'done', 'failed')),
        counts jsonb NOT NULL DEFAULT '{}'::jsonb,
        rejected jsonb NOT NULL DEFAULT '[]'::jsonb,
        town text NOT NULL DEFAULT '9999',
        finished_at timestamptz
    );
    ALTER TABLE import_batches ADD COLUMN files jsonb NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE jobs ADD CONSTRAINT jobs_batch_fk FOREIGN KEY (batch_id) REFERENCES import_batches(id);
    ALTER TABLE jobs ADD COLUMN csv_finish_date date, ADD COLUMN csv_total numeric, ADD COLUMN town text,
        ADD COLUMN needs_reason text, ADD COLUMN permit_result text, ADD COLUMN permit_pks text[] NOT NULL DEFAULT '{}';

    CREATE TABLE review_items (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        kind text NOT NULL,
        ref text NOT NULL,
        reason text NOT NULL,
        evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
        dedupe_key text UNIQUE,
        resolved_by uuid REFERENCES accounts(id),
        resolved_at timestamptz,
        resolution text
    );
    CREATE INDEX review_items_open ON review_items (created_at) WHERE resolved_at IS NULL;
    """)
