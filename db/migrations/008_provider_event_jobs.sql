CREATE TABLE provider_event_jobs (
    event_id text PRIMARY KEY REFERENCES provider_events(event_id),
    provider text NOT NULL,
    job_state text NOT NULL CHECK (job_state IN ('queued', 'processing', 'completed', 'dead_letter')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at timestamptz NOT NULL,
    lease_token text,
    lease_expires_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CHECK (
        (job_state = 'processing' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (job_state <> 'processing' AND lease_token IS NULL AND lease_expires_at IS NULL)
    )
);
CREATE INDEX provider_event_jobs_ready
    ON provider_event_jobs(provider, job_state, available_at, created_at);
