CREATE TABLE support_connectors (
    connector_id text PRIMARY KEY, merchant_id text NOT NULL REFERENCES merchants,
    provider text NOT NULL CHECK (provider IN ('gmail', 'freshdesk')),
    status text NOT NULL CHECK (status IN ('verified', 'invalid', 'disconnected')),
    verified_at timestamptz, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
    last_error_code text,
    CHECK (status <> 'verified' OR verified_at IS NOT NULL),
    UNIQUE (merchant_id, connector_id)
);
CREATE UNIQUE INDEX active_support_per_provider ON support_connectors(merchant_id, provider) WHERE status='verified';
CREATE TABLE support_connector_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merchant_id text NOT NULL REFERENCES merchants, connector_id text NOT NULL,
    provider text NOT NULL, action text NOT NULL, created_at timestamptz NOT NULL,
    status text NOT NULL, error_code text,
    FOREIGN KEY (merchant_id, connector_id) REFERENCES support_connectors(merchant_id, connector_id)
);
