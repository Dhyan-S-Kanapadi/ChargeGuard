CREATE TABLE shipping_connectors (
    connector_id text PRIMARY KEY,
    merchant_id text NOT NULL REFERENCES merchants,
    provider text NOT NULL CHECK (provider IN ('shiprocket','delhivery')),
    status text NOT NULL CHECK (status IN ('pending','verified','invalid','disconnected')),
    credential_hint text NOT NULL,
    verified_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    last_error_code text,
    UNIQUE (merchant_id, connector_id),
    UNIQUE (merchant_id, provider, connector_id)
);
ALTER TABLE orders ADD COLUMN shipping_provider text CHECK (shipping_provider IN ('shiprocket','delhivery'));
CREATE UNIQUE INDEX active_shipping_per_provider
    ON shipping_connectors(merchant_id, provider) WHERE status='verified';
CREATE TABLE merchant_shipping_links (
    merchant_id text NOT NULL REFERENCES merchants,
    provider text NOT NULL,
    connector_id text NOT NULL,
    PRIMARY KEY (merchant_id, provider),
    FOREIGN KEY (merchant_id, provider, connector_id)
        REFERENCES shipping_connectors(merchant_id, provider, connector_id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE shipping_connector_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merchant_id text NOT NULL REFERENCES merchants,
    connector_id text NOT NULL,
    provider text NOT NULL,
    action text NOT NULL,
    created_at timestamptz NOT NULL,
    status text NOT NULL,
    error_code text,
    FOREIGN KEY (merchant_id, connector_id)
        REFERENCES shipping_connectors(merchant_id, connector_id)
);
