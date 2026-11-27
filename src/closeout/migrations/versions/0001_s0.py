"""S0: clock, people, accounts, commands."""

from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE clock (
        id boolean PRIMARY KEY DEFAULT true CHECK (id),
        epoch_real timestamptz NOT NULL,
        epoch_virtual timestamptz NOT NULL,
        scale double precision NOT NULL CHECK (scale > 0),
        created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE people (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        display_name text,
        email text,
        phone text,
        email_hmac text,
        erased_at timestamptz
    );

    CREATE TABLE accounts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        kind text NOT NULL CHECK (kind IN ('contractor', 'homeowner', 'ops')),
        person_id uuid NOT NULL REFERENCES people(id),
        contractor_id uuid,
        demo_order integer NOT NULL DEFAULT 100
    );

    CREATE TABLE commands (
        command_id uuid PRIMARY KEY,
        created_at timestamptz NOT NULL DEFAULT now(),
        account_id uuid REFERENCES accounts(id),
        link_id uuid,
        target_workflow text NOT NULL,
        kind text NOT NULL,
        refs jsonb NOT NULL DEFAULT '{}'::jsonb,
        payload jsonb NOT NULL DEFAULT '{}'::jsonb,
        saved_at timestamptz NOT NULL DEFAULT now(),
        state text NOT NULL DEFAULT 'saved' CHECK (state IN ('saved', 'accepted', 'done', 'rejected')),
        result jsonb,
        updated_at timestamptz NOT NULL DEFAULT now(),
        sweep_attempts integer NOT NULL DEFAULT 0,
        redacted_at timestamptz
    );
    CREATE INDEX commands_saved ON commands (saved_at) WHERE state = 'saved';
    """)
