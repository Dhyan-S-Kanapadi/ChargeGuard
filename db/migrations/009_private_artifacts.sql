CREATE TABLE artifacts (
    artifact_id text PRIMARY KEY,
    merchant_id text NOT NULL REFERENCES merchants,
    chargeback_id text NOT NULL REFERENCES disputes(chargeback_id),
    artifact_type text NOT NULL CHECK (artifact_type IN ('rebuttal_pdf', 'rebuttal_facts', 'evidence_attachment')),
    object_key text NOT NULL UNIQUE,
    content_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    immutable_at timestamptz,
    retention_marked_at timestamptz,
    retention_reason text,
    UNIQUE (merchant_id, chargeback_id, artifact_id),
    CHECK (length(object_key) <= 1024),
    CHECK (object_key LIKE 'merchants/' || merchant_id || '/cases/' || chargeback_id || '/artifacts/%'),
    CHECK (retention_reason IS NULL OR retention_marked_at IS NOT NULL)
);
CREATE INDEX artifacts_case ON artifacts(merchant_id, chargeback_id, created_at);
CREATE OR REPLACE FUNCTION check_artifact_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM disputes d WHERE d.chargeback_id = NEW.chargeback_id AND d.merchant_id = NEW.merchant_id
    ) THEN
        RAISE EXCEPTION 'artifact merchant does not own dispute' USING ERRCODE='23503';
    END IF;
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER artifact_owner
    AFTER INSERT OR UPDATE ON artifacts DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_artifact_owner();
CREATE FUNCTION prevent_finalized_artifact_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.immutable_at IS NOT NULL AND (
        NEW.immutable_at IS DISTINCT FROM OLD.immutable_at
        OR NEW.merchant_id IS DISTINCT FROM OLD.merchant_id
        OR NEW.chargeback_id IS DISTINCT FROM OLD.chargeback_id
        OR NEW.artifact_type IS DISTINCT FROM OLD.artifact_type
        OR NEW.object_key IS DISTINCT FROM OLD.object_key
        OR NEW.content_type IS DISTINCT FROM OLD.content_type
        OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
        OR NEW.sha256 IS DISTINCT FROM OLD.sha256
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
    ) THEN
        RAISE EXCEPTION 'finalized artifact metadata is immutable' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER finalized_artifact_immutable
    BEFORE UPDATE ON artifacts FOR EACH ROW EXECUTE FUNCTION prevent_finalized_artifact_mutation();
CREATE FUNCTION prevent_artifact_deletion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'artifact deletion is forbidden; use retention marking' USING ERRCODE='55000';
END $$;
CREATE TRIGGER artifact_deletion_forbidden
    BEFORE DELETE ON artifacts FOR EACH ROW EXECUTE FUNCTION prevent_artifact_deletion();

CREATE TABLE artifact_download_grants (
    grant_id text PRIMARY KEY,
    artifact_id text NOT NULL REFERENCES artifacts(artifact_id),
    merchant_id text NOT NULL REFERENCES merchants,
    chargeback_id text NOT NULL REFERENCES disputes(chargeback_id),
    actor_id text NOT NULL,
    token_sha256 text NOT NULL CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL
);
CREATE INDEX artifact_download_grants_expiry ON artifact_download_grants(expires_at);
CREATE FUNCTION check_artifact_grant_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM artifacts a WHERE a.artifact_id = NEW.artifact_id
          AND a.merchant_id = NEW.merchant_id AND a.chargeback_id = NEW.chargeback_id
    ) THEN
        RAISE EXCEPTION 'artifact grant ownership mismatch' USING ERRCODE='23503';
    END IF;
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER artifact_grant_owner
    AFTER INSERT OR UPDATE ON artifact_download_grants DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_artifact_grant_owner();

CREATE TABLE artifact_access_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    artifact_id text NOT NULL REFERENCES artifacts(artifact_id),
    merchant_id text NOT NULL REFERENCES merchants,
    actor_id text NOT NULL,
    action text NOT NULL CHECK (action IN ('download_granted', 'download_redeemed')),
    created_at timestamptz NOT NULL
);
