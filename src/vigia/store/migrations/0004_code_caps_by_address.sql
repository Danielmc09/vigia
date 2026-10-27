-- Email code caps per destination address and per business (D-028, incident adv-relay-correo).

DROP INDEX verification_codes_recent;
CREATE INDEX verification_codes_by_business ON verification_codes (business_id, created_at);
CREATE INDEX verification_codes_by_email ON verification_codes (email_hash, created_at);

-- Codes sent to one address in the last hour, across every business (RLS would hide the
-- other businesses). Returns only a count: no business, member or code leaves the function.
CREATE FUNCTION vigia_recent_email_codes(p_hash bytea)
RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT count(*) FROM public.verification_codes v
    WHERE v.email_hash = p_hash AND v.created_at > now() - interval '1 hour'
$$;

REVOKE ALL ON FUNCTION vigia_recent_email_codes(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION vigia_recent_email_codes(bytea) TO vigia_app;
