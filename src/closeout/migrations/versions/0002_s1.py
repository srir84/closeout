"""S1: towns, parcels, permits, permit_parcel, sync_runs, contractors, recipients, homes, grants."""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE towns (
        code text PRIMARY KEY,
        created_at timestamptz NOT NULL DEFAULT now(),
        name text NOT NULL,
        synthetic boolean NOT NULL,
        feed_filter text,
        clerk_recipient_id uuid,
        settings jsonb NOT NULL DEFAULT '{}'::jsonb
    );

    CREATE TABLE parcels (
        pin text PRIMARY KEY,
        created_at timestamptz NOT NULL DEFAULT now(),
        town text NOT NULL REFERENCES towns(code),
        block text NOT NULL,
        lot text NOT NULL,
        qualifier text NOT NULL DEFAULT '',
        address text,
        address_norm text,
        house_no text,
        unit text,
        street text,
        prop_class text,
        year_built integer,
        lat double precision,
        lon double precision
    );
    CREATE INDEX parcels_block_lot ON parcels (town, upper(block), upper(lot));
    CREATE INDEX parcels_addr ON parcels (town, street, house_no);

    CREATE TABLE permits (
        pk text PRIMARY KEY,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        town text NOT NULL REFERENCES towns(code),
        permit_no text,
        block text NOT NULL,
        lot text NOT NULL,
        permit_type text,
        trades text[] NOT NULL DEFAULT '{}',
        use_group text,
        issued_on date,
        final_on date,
        final_type text,
        cost numeric,
        sq_ft numeric,
        processed_on date,
        row_hash text NOT NULL,
        missing_from_source_since timestamptz,
        raw jsonb NOT NULL,
        bad_fields text[] NOT NULL DEFAULT '{}'
    );
    CREATE INDEX permits_block_lot ON permits (town, upper(block), upper(lot));

    CREATE TABLE permit_parcel (
        permit_pk text PRIMARY KEY REFERENCES permits(pk),
        created_at timestamptz NOT NULL DEFAULT now(),
        pin text REFERENCES parcels(pin),
        status text NOT NULL CHECK (status IN ('linked', 'several', 'none')),
        candidates text[] NOT NULL DEFAULT '{}'
    );
    CREATE INDEX permit_parcel_pin ON permit_parcel (pin);

    CREATE TABLE sync_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        town text NOT NULL,
        rows_seen integer NOT NULL,
        rows_new integer NOT NULL,
        rows_changed integer NOT NULL,
        rows_missing integer NOT NULL,
        finished_at timestamptz
    );

    CREATE TABLE recipients (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        person_id uuid NOT NULL REFERENCES people(id),
        source text NOT NULL CHECK (source IN ('owner_entered', 'registry', 'town_setting')),
        entered_by uuid REFERENCES accounts(id)
    );

    CREATE TABLE contractors (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        name text NOT NULL,
        license text NOT NULL,
        trades text[] NOT NULL DEFAULT '{}',
        recipient_id uuid REFERENCES recipients(id)
    );
    ALTER TABLE accounts ADD CONSTRAINT accounts_contractor_fk FOREIGN KEY (contractor_id) REFERENCES contractors(id);
    ALTER TABLE towns ADD CONSTRAINT towns_clerk_fk FOREIGN KEY (clerk_recipient_id) REFERENCES recipients(id);

    CREATE TABLE homes (
        pin text PRIMARY KEY REFERENCES parcels(pin),
        created_at timestamptz NOT NULL DEFAULT now(),
        owner_account_id uuid REFERENCES accounts(id),
        claim_code_hash text
    );

    CREATE TABLE grants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        account_id uuid NOT NULL REFERENCES accounts(id),
        actions text[] NOT NULL,
        daily_limit integer NOT NULL,
        version integer NOT NULL DEFAULT 1,
        expires_at timestamptz,
        revoked_at timestamptz
    );
    """)
