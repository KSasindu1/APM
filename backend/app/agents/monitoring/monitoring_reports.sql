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
