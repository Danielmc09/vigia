-- Wazenda webhook (T-10, specs/01): idempotency by message id, and the owner's
-- "estafa confirmada" feeding known_campaigns (specs/10 §4, D-017).
--
-- webhook_events keeps only the HMAC of the channel message id (never the id in clear) and
-- the app role touches it only through vigia_webhook_first.

CREATE TABLE webhook_events (
    id_hash bytea PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_events FORCE ROW LEVEL SECURITY;

CREATE FUNCTION vigia_webhook_first(p_hash bytea)
RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    WITH ins AS (
        INSERT INTO public.webhook_events (id_hash) VALUES (p_hash)
        ON CONFLICT (id_hash) DO NOTHING
        RETURNING 1
    )
    SELECT EXISTS (SELECT 1 FROM ins)
$$;

-- Who confirmed a user campaign, as HMACs of business ids: it reaches other businesses
-- only once two different businesses confirmed it (D-082, campaign poisoning).
ALTER TABLE known_campaigns ADD COLUMN confirmed_by bytea[] NOT NULL DEFAULT '{}';

CREATE FUNCTION vigia_confirm_campaign(
    p_id text, p_country text, p_kind text, p_brand text, p_simhash bytea, p_domains text[],
    p_business bytea
)
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    INSERT INTO public.known_campaigns AS k (id, country, kind, brand, simhash, domains,
        source_url, source_type, seen_date, confirmed_by)
    VALUES (p_id, p_country, p_kind, p_brand, p_simhash, p_domains,
        'vigia://owner-confirmed', 'usuario', current_date, ARRAY[p_business])
    ON CONFLICT (id) DO UPDATE SET
        times_seen = k.times_seen + 1,
        seen_date = current_date,
        confirmed_by = CASE WHEN p_business = ANY (k.confirmed_by) THEN k.confirmed_by
                            ELSE k.confirmed_by || p_business END
    WHERE k.source_type = 'usuario'
$$;

REVOKE ALL ON FUNCTION vigia_webhook_first(bytea) FROM PUBLIC;
REVOKE ALL ON FUNCTION vigia_confirm_campaign(text, text, text, text, bytea, text[], bytea)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION vigia_webhook_first(bytea) TO vigia_app;
GRANT EXECUTE ON FUNCTION vigia_confirm_campaign(text, text, text, text, bytea, text[], bytea)
    TO vigia_app;
