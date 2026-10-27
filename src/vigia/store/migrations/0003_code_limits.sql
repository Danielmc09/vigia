-- Caps on email verification codes (D-028) and schema-qualified SECURITY DEFINER bodies.

ALTER TABLE verification_codes
    ADD COLUMN attempts int NOT NULL DEFAULT 0,  -- wrong guesses while the code is live
    ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX verification_codes_recent ON verification_codes (member_id, created_at);

-- Same functions as 0002, with tables qualified so a pinned search_path is not the only
-- thing keeping them on public. CREATE OR REPLACE keeps owner and grants.
CREATE OR REPLACE FUNCTION vigia_find_member(p_hash bytea)
RETURNS TABLE (member_id uuid, business_id uuid, role text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT m.id, m.business_id, m.role FROM public.members m
    WHERE m.phone_hash = p_hash OR m.email_hash = p_hash
$$;

CREATE OR REPLACE FUNCTION vigia_record_rejection(p_hash bytea, p_origin text)
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    INSERT INTO public.rejected_attempts (sender_hash, origin) VALUES (p_hash, p_origin)
$$;
