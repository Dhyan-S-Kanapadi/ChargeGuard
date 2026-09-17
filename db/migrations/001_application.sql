CREATE TABLE merchants (
    merchant_id text PRIMARY KEY, name text NOT NULL, vertical text,
    payment_provider text, payment_connector_id text, device_risk_connector_id text,
    razorpay_account_id text UNIQUE, shipping_provider text, support_connector_ref text,
    freshdesk_domain text, gmail_user_id text, average_order_value numeric,
    chargeback_history_count integer, store_url text, storefront_platform text,
    platform_credential_verified boolean, platform_credential_verified_at timestamptz,
    platform_credential_verification_reason text
);
CREATE TABLE payment_connectors (
    connector_id text PRIMARY KEY, merchant_id text NOT NULL REFERENCES merchants,
    provider text NOT NULL CHECK (provider IN ('razorpay','stripe')),
    provider_account_id text, status text NOT NULL CHECK (status IN ('pending','verified','invalid','disconnected')),
    credential_hint text, verified_at timestamptz, created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL, last_error_code text,
    UNIQUE (merchant_id, connector_id), UNIQUE (merchant_id, provider, connector_id)
);
CREATE UNIQUE INDEX active_payment_per_provider ON payment_connectors(merchant_id, provider) WHERE status='verified';
CREATE UNIQUE INDEX active_payment_account ON payment_connectors(provider, provider_account_id) WHERE status='verified';
CREATE TABLE merchant_payment_links (
    merchant_id text NOT NULL REFERENCES merchants, provider text NOT NULL, connector_id text NOT NULL,
    PRIMARY KEY (merchant_id, provider),
    FOREIGN KEY (merchant_id, provider, connector_id) REFERENCES payment_connectors(merchant_id, provider, connector_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE merchant_volumes (
    merchant_id text NOT NULL REFERENCES merchants, network text NOT NULL,
    transaction_count bigint NOT NULL CHECK (transaction_count >= 0), PRIMARY KEY (merchant_id, network)
);
CREATE TABLE device_risk_connectors (
    connector_id text PRIMARY KEY, merchant_id text NOT NULL REFERENCES merchants,
    provider text NOT NULL CHECK (provider='seon'),
    status text NOT NULL CHECK (status IN ('verification_pending','verified','invalid','disconnected')),
    credential_hint text, verified_at timestamptz, last_success_at timestamptz,
    last_error_code text, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
    UNIQUE (merchant_id, connector_id)
);
CREATE UNIQUE INDEX active_device_per_provider ON device_risk_connectors(merchant_id,provider) WHERE status='verified';
ALTER TABLE merchants ADD CONSTRAINT merchant_payment_owner FOREIGN KEY (merchant_id,payment_connector_id)
    REFERENCES payment_connectors(merchant_id,connector_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE merchants ADD CONSTRAINT merchant_device_owner FOREIGN KEY (merchant_id,device_risk_connector_id)
    REFERENCES device_risk_connectors(merchant_id,connector_id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE payment_connector_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merchant_id text NOT NULL REFERENCES merchants, connector_id text NOT NULL,
    provider text NOT NULL, action text NOT NULL, created_at timestamptz NOT NULL,
    status text NOT NULL, error_code text,
    FOREIGN KEY (merchant_id,connector_id) REFERENCES payment_connectors(merchant_id,connector_id)
);
CREATE TABLE device_risk_connector_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    merchant_id text NOT NULL REFERENCES merchants, connector_id text NOT NULL,
    provider text NOT NULL, action text NOT NULL, created_at timestamptz NOT NULL,
    status text NOT NULL, error_code text,
    FOREIGN KEY (merchant_id,connector_id) REFERENCES device_risk_connectors(merchant_id,connector_id)
);
CREATE TABLE orders (
    merchant_id text NOT NULL REFERENCES merchants, order_id text NOT NULL,
    customer_email text NOT NULL, customer_ip text, user_agent text,
    shipping_address jsonb, order_date timestamptz NOT NULL,
    is_disputed boolean NOT NULL DEFAULT false, is_fraud_flagged boolean NOT NULL DEFAULT false,
    payment_provider text, provider_payment_id text, provider_order_id text,
    commerce_order_number text, tracking_id text, fulfillment_id text,
    PRIMARY KEY (merchant_id,order_id),
    UNIQUE (merchant_id,provider_payment_id), UNIQUE (merchant_id,provider_order_id)
);
-- Commerce numbers are not universally unique (e.g. Shopify number vs id).
-- Preserve existing behavior: ambiguous number lookup returns no match.
CREATE INDEX orders_commerce_number ON orders(merchant_id,commerce_order_number);
CREATE INDEX orders_history ON orders(merchant_id,lower(trim(customer_email)),order_date);
CREATE TABLE disputes (
    chargeback_id text PRIMARY KEY, merchant_id text NOT NULL REFERENCES merchants,
    status text NOT NULL CHECK (status IN ('received','processing','completed','failed')),
    state jsonb NOT NULL, error text, created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL, revision bigint NOT NULL DEFAULT 1,
    CHECK (state->>'chargeback_id' = chargeback_id),
    CHECK (state->'merchant_profile'->>'merchant_id' = merchant_id)
);
CREATE INDEX disputes_merchant ON disputes(merchant_id,created_at);
CREATE TABLE provider_events (
    event_id text PRIMARY KEY, provider text NOT NULL,
    event_type text, provider_dispute_id text, merchant_id text REFERENCES merchants,
    payment_id text, account_id text, payload_hash text, payload_sha256 text,
    event_id_source text, provider_event_timestamp bigint, event_data jsonb,
    processing_state text NOT NULL CHECK (processing_state IN (
        'received','queued','processing','scheduled','manual_review','updated','ignored',
        'unresolved','failed','stale','outcome_not_eligible')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count>=0),
    received_at timestamptz NOT NULL, last_attempt_at timestamptz,
    processed_at timestamptz, failure_reason text
);
CREATE INDEX provider_events_recovery ON provider_events(provider,processing_state,received_at);
CREATE INDEX provider_events_dispute ON provider_events(provider,provider_dispute_id);
-- Test/demo fixture snapshots only. Production rejects all writes here.
CREATE TABLE simulator_disputes (
    dispute_id text PRIMARY KEY, merchant_id text NOT NULL REFERENCES merchants,
    created_at timestamptz NOT NULL, snapshot jsonb NOT NULL
);
CREATE TABLE store_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
-- Account ownership also spans legacy merchant mappings and verified connectors.
CREATE FUNCTION check_account_ownership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM merchants m JOIN payment_connectors c
      ON c.provider='razorpay' AND c.status='verified' AND c.provider_account_id=m.razorpay_account_id
      WHERE c.merchant_id<>m.merchant_id) THEN
        RAISE EXCEPTION 'provider account ownership conflict' USING ERRCODE='23505';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER merchant_account_owner AFTER INSERT OR UPDATE ON merchants
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_account_ownership();
CREATE CONSTRAINT TRIGGER connector_account_owner AFTER INSERT OR UPDATE ON payment_connectors
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_account_ownership();
