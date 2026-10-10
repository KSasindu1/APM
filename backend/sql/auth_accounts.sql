-- Apply deliberately using a privileged migration connection, never from app startup.
BEGIN;

CREATE TABLE public.auth_accounts (
    user_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username text NOT NULL UNIQUE CHECK (
        char_length(username) BETWEEN 3 AND 50
        AND username COLLATE "C" ~ '^[a-z0-9][a-z0-9_.-]{2,49}$'
    ),
    password_hash text NOT NULL CHECK (
        char_length(password_hash) <= 256
        AND password_hash ~ '^pbkdf2_sha256\$(600000|[6-9][0-9]{5}|1[0-9]{6}|2000000)\$[A-Za-z0-9+/]+=*\$[A-Za-z0-9+/]+=*$'
    ),
    role text NOT NULL CHECK (role IN ('user', 'admin')),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.auth_admin_invitations (
    invitation_hash text PRIMARY KEY CHECK (invitation_hash ~ '^[0-9a-f]{64}$'),
    username text NOT NULL CHECK (
        char_length(username) BETWEEN 3 AND 50
        AND username COLLATE "C" ~ '^[a-z0-9][a-z0-9_.-]{2,49}$'
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL CHECK (expires_at > created_at),
    consumed_at timestamptz,
    CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);

ALTER TABLE public.auth_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_admin_invitations ENABLE ROW LEVEL SECURITY;
-- No browser/user policies: only the trusted service role can access these tables.
REVOKE ALL ON TABLE public.auth_accounts, public.auth_admin_invitations
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.auth_accounts TO service_role;
-- All writes must pass through the synchronized RPCs, even with a service key.
CREATE INDEX auth_admin_invitations_pending_username
    ON public.auth_admin_invitations (username) WHERE consumed_at IS NULL;

CREATE FUNCTION public.create_user_account(
    p_username text, p_password_hash text
) RETURNS SETOF public.auth_accounts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    -- Every account/invitation writer uses this same transaction-scoped key.
    -- Hash collisions only serialize unrelated usernames; they cannot bypass it.
    PERFORM pg_advisory_xact_lock(hashtextextended('apm-auth-account:' || p_username, 0));
    IF EXISTS (
        SELECT 1 FROM public.auth_admin_invitations
        WHERE username = p_username
          AND consumed_at IS NULL
          AND expires_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Account already exists' USING ERRCODE = '23505';
    END IF;

    RETURN QUERY
    INSERT INTO public.auth_accounts (username, password_hash, role)
    VALUES (p_username, p_password_hash, 'user')
    RETURNING *;
END;
$$;

CREATE FUNCTION public.issue_admin_invitation(
    p_username text, p_invitation_hash text, p_expires_at timestamptz
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('apm-auth-account:' || p_username, 0));
    IF EXISTS (SELECT 1 FROM public.auth_accounts WHERE username = p_username) THEN
        RAISE EXCEPTION 'Account already exists' USING ERRCODE = '23505';
    END IF;
    -- Wall-clock checks happen after waiting, not at transaction start.
    IF p_expires_at IS NULL OR p_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'Invalid invitation expiry' USING ERRCODE = '23514';
    END IF;

    INSERT INTO public.auth_admin_invitations (username, invitation_hash, expires_at)
    VALUES (p_username, p_invitation_hash, p_expires_at);
END;
$$;

CREATE FUNCTION public.consume_admin_invitation(
    p_username text, p_password_hash text, p_invitation_hash text
) RETURNS SETOF public.auth_accounts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('apm-auth-account:' || p_username, 0));
    -- UPDATE takes a row lock. A concurrent second consumer sees consumed_at set
    -- after the first commits. Any failed INSERT rolls this update back too.
    UPDATE public.auth_admin_invitations
    SET consumed_at = clock_timestamp()
    WHERE invitation_hash = p_invitation_hash
      AND username = p_username
      AND consumed_at IS NULL
      AND expires_at > clock_timestamp();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Invalid invitation' USING ERRCODE = 'P0001';
    END IF;

    RETURN QUERY
    INSERT INTO public.auth_accounts (username, password_hash, role)
    VALUES (p_username, p_password_hash, 'admin')
    RETURNING *;
END;
$$;

REVOKE ALL ON FUNCTION public.create_user_account(text, text),
    public.issue_admin_invitation(text, text, timestamptz),
    public.consume_admin_invitation(text, text, text)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.create_user_account(text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.issue_admin_invitation(text, text, timestamptz) TO service_role;
GRANT EXECUTE ON FUNCTION public.consume_admin_invitation(text, text, text) TO service_role;

COMMIT;
