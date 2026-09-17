CREATE TABLE app_users (
    user_id uuid PRIMARY KEY,
    issuer text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    platform_admin boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE merchant_memberships (
    merchant_id text NOT NULL REFERENCES merchants,
    user_id uuid NOT NULL REFERENCES app_users,
    role text NOT NULL CHECK (role IN ('owner','reviewer','read_only')),
    PRIMARY KEY (merchant_id,user_id)
);
CREATE TABLE app_sessions (
    session_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES app_users,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX app_sessions_user ON app_sessions(user_id);
CREATE TABLE access_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id uuid REFERENCES app_users,
    session_id uuid,
    merchant_id text,
    action text NOT NULL,
    result text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
-- The application never updates or deletes these records. Apply restricted DB grants
-- before deployment; retention/export policy is part of the operations stage.
