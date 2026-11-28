"""S11: town_requests (made early: the sale report shows their results)."""

from alembic import op

revision = "0010"
down_revision = "0009"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE town_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        home_pin text NOT NULL REFERENCES parcels(pin),
        permit_pk text NOT NULL REFERENCES permits(pk),
        account_id uuid NOT NULL REFERENCES accounts(id),
        status text NOT NULL DEFAULT 'drafted'
            CHECK (status IN ('drafted', 'approved', 'sent', 'reminded', 'replied', 'no_reply', 'closed')),
        result text CHECK (result IN ('final_on_file', 'no_record', 'unclear')),
        result_date date,
        round integer NOT NULL DEFAULT 1,
        content_rev integer NOT NULL DEFAULT 1,
        closed_at timestamptz,
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX town_requests_open ON town_requests (permit_pk) WHERE status NOT IN ('no_reply', 'closed');
    ALTER TABLE documents ADD CONSTRAINT documents_town_request_fk FOREIGN KEY (town_request_id)
        REFERENCES town_requests(id);
    ALTER TABLE outbox ADD CONSTRAINT outbox_town_request_fk FOREIGN KEY (town_request_id) REFERENCES town_requests(id);
    """)
