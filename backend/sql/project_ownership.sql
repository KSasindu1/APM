-- One-time additive migration, applied deliberately with a privileged connection.
-- Prerequisites: public.project_schedules (schema.sql) and auth_accounts.sql.
BEGIN;

ALTER TABLE public.project_schedules
    ADD COLUMN owner_user_id uuid
        REFERENCES public.auth_accounts(user_id) ON DELETE SET NULL;

CREATE INDEX idx_project_schedules_owner_user_id
    ON public.project_schedules (owner_user_id);

-- Existing schedules remain NULL/unassigned until a verified admin assigns them.
ALTER TABLE public.project_schedules ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.project_schedules FROM PUBLIC, anon, authenticated;
-- Browser access goes through the backend, which applies verified owner scopes.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.project_schedules TO service_role;

COMMIT;
