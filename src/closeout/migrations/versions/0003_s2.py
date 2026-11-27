"""S2: events (add-only)."""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE events (
        event_id uuid PRIMARY KEY,
        created_at timestamptz NOT NULL DEFAULT now(),
        stream text NOT NULL,
        seq integer NOT NULL CHECK (seq > 0),
        kind text NOT NULL,
        v integer NOT NULL DEFAULT 1,
        payload jsonb NOT NULL,
        actor_account_id uuid,
        event_key text NOT NULL,
        UNIQUE (stream, seq)
    );
    CREATE INDEX events_kind ON events (kind);

    CREATE FUNCTION add_only() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'table % is add-only', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
    END $$;

    CREATE TRIGGER events_add_only BEFORE UPDATE OR DELETE ON events
        FOR EACH ROW EXECUTE FUNCTION add_only();
    """)
