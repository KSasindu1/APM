-- Reference for the manually created Supabase public table; not auto-applied.
-- No grants, policies, projects foreign key, or Risk schema changes.
BEGIN;

CREATE TABLE IF NOT EXISTS public.monitoring_reports (
    report_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    report JSONB NOT NULL,
    CONSTRAINT monitoring_report_is_object CHECK (jsonb_typeof(report) = 'object')
);

CREATE INDEX IF NOT EXISTS monitoring_reports_project_date_idx
    ON public.monitoring_reports (project_id, generated_at DESC);

ALTER TABLE public.monitoring_reports ENABLE ROW LEVEL SECURITY;

COMMIT;

-- Append-only observation receipts. Apply manually; health only checks configuration.
-- No browser grants or permissive RLS policies: the backend authorizes each operation.
BEGIN;

CREATE TABLE IF NOT EXISTS public.monitoring_observations (
    observation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_on DATE NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL,
    submitted_by TEXT NOT NULL,
    receipt JSONB NOT NULL,
    CONSTRAINT monitoring_observation_identifiers CHECK (
        observation_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'
        AND project_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'
        AND request_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'
        AND source ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'
        AND submitted_by ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'
    ),
    CONSTRAINT monitoring_observation_receipt_binding CHECK ((
        jsonb_typeof(receipt) = 'object'
        AND receipt ?& ARRAY['observation_id', 'project_id', 'request_id', 'source',
                             'observed_on', 'accepted_at', 'submitted_by', 'actual_progress', 'persisted']
        AND receipt->>'observation_id' = observation_id
        AND receipt->>'project_id' = project_id
        AND receipt->>'request_id' = request_id
        AND receipt->>'source' = source
        AND (receipt->>'observed_on')::DATE = observed_on
        AND (receipt->>'accepted_at')::TIMESTAMPTZ = accepted_at
        AND receipt->>'submitted_by' = submitted_by
        AND jsonb_typeof(receipt->'actual_progress') = 'object'
        AND receipt->'actual_progress'->>'project_id' = project_id
        AND receipt->'persisted' = 'true'::JSONB
    ) IS TRUE)
);

CREATE INDEX IF NOT EXISTS monitoring_observations_project_date_idx
    ON public.monitoring_observations (project_id, accepted_at DESC, observation_id DESC);

ALTER TABLE public.monitoring_observations ENABLE ROW LEVEL SECURITY;

COMMIT;
