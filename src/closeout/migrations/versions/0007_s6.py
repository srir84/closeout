"""S6: approvals, outbox, links, suppression, audit_log (add-only)."""

from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE approvals (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        nonce text NOT NULL UNIQUE,
        account_id uuid NOT NULL REFERENCES accounts(id),
        action text NOT NULL,
        target text NOT NULL,
        round integer,
        step text,
        content_rev integer,
        recipient_id uuid REFERENCES recipients(id),
        to_address text,
        subject text,
        body_template text,
        claims jsonb NOT NULL,
        token text NOT NULL,
        expires_at timestamptz NOT NULL,
        used_by_command uuid,
        used_at timestamptz,
        voided_at timestamptz,
        redacted_at timestamptz
    );
    CREATE INDEX approvals_target ON approvals (target, round);

    CREATE TABLE outbox (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        command_id uuid NOT NULL UNIQUE,
        message_id text NOT NULL UNIQUE,
        approval_id uuid NOT NULL REFERENCES approvals(id),
        job_id uuid REFERENCES jobs(id),
        town_request_id uuid,
        round integer NOT NULL,
        step text NOT NULL,
        recipient_id uuid NOT NULL REFERENCES recipients(id),
        grant_id uuid REFERENCES grants(id),
        to_address text,
        subject text,
        body_template text,
        state text NOT NULL DEFAULT 'pending'
            CHECK (state IN ('pending', 'sending', 'sent', 'failed', 'unknown', 'cancelled')),
        attempt_id uuid,
        lease_until timestamptz,
        attempts integer NOT NULL DEFAULT 0,
        next_attempt_at timestamptz NOT NULL DEFAULT now(),
        last_error_code text,
        sent_at timestamptz,
        cancel_reason text,
        fingerprint text NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        redacted_at timestamptz
    );
    CREATE INDEX outbox_due ON outbox (next_attempt_at) WHERE state = 'pending';
    CREATE INDEX outbox_lease ON outbox (lease_until) WHERE state = 'sending';

    CREATE TABLE links (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        token_hash text NOT NULL UNIQUE,
        purpose text NOT NULL CHECK (purpose IN ('answer', 'unsubscribe', 'share', 'town_reply', 'contractor_confirm')),
        target text NOT NULL,
        round integer,
        outbox_id uuid REFERENCES outbox(id),
        recipient_id uuid REFERENCES recipients(id),
        role text,
        fields_allowed text[],
        expires_at timestamptz NOT NULL,
        revoked_at timestamptz
    );

    CREATE TABLE suppression (
        email_hmac text PRIMARY KEY,
        reason text NOT NULL,
        added_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE audit_log (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        actor_account_id uuid,
        action text NOT NULL,
        target text NOT NULL,
        approval_id uuid,
        command_id uuid,
        details jsonb NOT NULL DEFAULT '{}'::jsonb
    );
    CREATE TRIGGER audit_log_add_only BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION add_only();
    """)
