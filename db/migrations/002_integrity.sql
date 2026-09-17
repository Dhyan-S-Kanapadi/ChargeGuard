ALTER TABLE disputes DROP CONSTRAINT disputes_check;
ALTER TABLE disputes DROP CONSTRAINT disputes_check1;
ALTER TABLE disputes ADD CONSTRAINT dispute_state_identity
    CHECK ((state->>'chargeback_id') IS NOT DISTINCT FROM chargeback_id);
ALTER TABLE disputes ADD CONSTRAINT dispute_state_owner
    CHECK ((state->'merchant_profile'->>'merchant_id') IS NOT DISTINCT FROM merchant_id);
CREATE OR REPLACE FUNCTION check_account_ownership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Also serialize constraint checks for writes made outside the repository.
    PERFORM pg_advisory_xact_lock(724906015);
    IF EXISTS (SELECT 1 FROM merchants m JOIN payment_connectors c
      ON c.provider='razorpay' AND c.status='verified' AND c.provider_account_id=m.razorpay_account_id
      WHERE c.merchant_id<>m.merchant_id) THEN
        RAISE EXCEPTION 'provider account ownership conflict' USING ERRCODE='23505';
    END IF;
    RETURN NULL;
END $$;
