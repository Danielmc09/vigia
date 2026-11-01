-- Owner panel (T-17b): the owner writes "panel" and gets a one-time link; the link trades for
-- a 24 h session. Only the SHA-256 of each token is stored, never the token (specs/16).
--
-- The app role may only INSERT a link under its business context. Trading a link and reading
-- a session cross businesses (the browser carries no business id), so both go through
-- definers that return only the business id and never a token.

CREATE TABLE panel_tokens (
    token_hash bytea PRIMARY KEY,
    business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
    member_id uuid NOT NULL REFERENCES members (id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('link', 'session')),
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE panel_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE panel_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY business_isolation ON panel_tokens
    USING (business_id = NULLIF(current_setting('app.business_id', true), '')::uuid)
    WITH CHECK (business_id = NULLIF(current_setting('app.business_id', true), '')::uuid);
GRANT INSERT ON panel_tokens TO vigia_app;

-- Spend a live link once (the row lock makes a second caller find used_at set) and open a
-- session for the same member. NULL when the link is unknown, used or expired.
CREATE FUNCTION vigia_panel_enter(p_link bytea, p_session bytea, p_ttl interval)
RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    WITH spent AS (
        UPDATE public.panel_tokens SET used_at = now()
        WHERE token_hash = p_link AND kind = 'link' AND used_at IS NULL AND expires_at > now()
        RETURNING business_id, member_id
    ), opened AS (
        INSERT INTO public.panel_tokens (token_hash, business_id, member_id, kind, expires_at)
        SELECT p_session, business_id, member_id, 'session', now() + p_ttl FROM spent
        RETURNING business_id
    )
    SELECT business_id FROM opened
$$;

-- The business of a live session whose member is still its owner, else NULL.
CREATE FUNCTION vigia_panel_session(p_session bytea)
RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT t.business_id FROM public.panel_tokens t
    JOIN public.members m ON m.id = t.member_id AND m.business_id = t.business_id
    WHERE t.token_hash = p_session AND t.kind = 'session' AND t.expires_at > now()
      AND m.role = 'owner'
$$;

REVOKE ALL ON FUNCTION vigia_panel_enter(bytea, bytea, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION vigia_panel_session(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION vigia_panel_enter(bytea, bytea, interval) TO vigia_app;
GRANT EXECUTE ON FUNCTION vigia_panel_session(bytea) TO vigia_app;
