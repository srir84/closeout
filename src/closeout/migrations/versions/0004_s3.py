"""S3: jobs, documents, reads, facts, model_calls."""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE jobs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        origin text NOT NULL DEFAULT 'contractor' CHECK (origin IN ('contractor', 'homeowner')),
        contractor_id uuid REFERENCES contractors(id),
        batch_id uuid,
        job_ref text,
        pin text REFERENCES parcels(pin),
        homeowner_recipient_id uuid REFERENCES recipients(id),
        address_raw text,
        work_type text,
        stated_like_for_like boolean,
        stated_detached_1_2_family boolean,
        status text NOT NULL DEFAULT 'imported',
        round integer NOT NULL DEFAULT 1,
        content_rev integer NOT NULL DEFAULT 1,
        price_visible boolean NOT NULL DEFAULT false,
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX jobs_contractor_ref ON jobs (contractor_id, job_ref) WHERE job_ref IS NOT NULL;

    CREATE TABLE documents (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        job_id uuid REFERENCES jobs(id),
        town_request_id uuid,
        kind text NOT NULL CHECK (kind IN ('invoice', 'town_reply')),
        sha256 text NOT NULL,
        path text NOT NULL,
        mime text NOT NULL,
        pages integer NOT NULL DEFAULT 1,
        removed_at timestamptz
    );

    CREATE TABLE reads (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        document_id uuid NOT NULL REFERENCES documents(id),
        reader text NOT NULL CHECK (reader IN ('A', 'B')),
        model text NOT NULL,
        model_digest text NOT NULL,
        prompt_version text NOT NULL,
        output jsonb,
        valid boolean NOT NULL,
        errors text[] NOT NULL DEFAULT '{}',
        redacted_at timestamptz,
        UNIQUE (document_id, reader)
    );

    CREATE TABLE facts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        job_id uuid NOT NULL REFERENCES jobs(id),
        field text NOT NULL,
        value text,
        value_a text,
        value_b text,
        source text NOT NULL CHECK (source IN ('both_reads', 'one_read', 'registry', 'person', 'csv')),
        status text NOT NULL CHECK (status IN ('kept', 'asked', 'answered', 'unconfirmed')),
        reason text,
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (job_id, field)
    );

    CREATE TABLE model_calls (
        call_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        document_id uuid REFERENCES documents(id),
        reader text NOT NULL,
        purpose text NOT NULL,
        provider text NOT NULL,
        model text NOT NULL,
        model_digest text NOT NULL,
        replay_key text NOT NULL,
        response text,
        ms integer NOT NULL,
        tokens_in integer,
        tokens_out integer,
        cost_usd numeric NOT NULL DEFAULT 0,
        redacted_at timestamptz
    );
    """)
