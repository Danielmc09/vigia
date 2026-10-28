-- Operator panel (T-13, D-072): the verdict columns that are not PII, in clear, and two
-- SECURITY DEFINER readers that cross businesses without ever returning business_id,
-- payload_enc or anything sealed. The full verdict stays in payload_enc (specs/16).

ALTER TABLE verdicts
    ADD COLUMN kind text,
    ADD COLUMN brand text,
    ADD COLUMN confidence real,
    ADD COLUMN signal_types text[] NOT NULL DEFAULT '{}',
    ADD COLUMN tools text[] NOT NULL DEFAULT '{}',
    ADD COLUMN usd double precision NOT NULL DEFAULT 0,
    ADD COLUMN latency_ms integer NOT NULL DEFAULT 0;

CREATE INDEX verdicts_panel ON verdicts (created_at DESC);

CREATE FUNCTION vigia_panel_verdicts(p_limit integer)
RETURNS TABLE (
    created_at timestamptz,
    kind text,
    brand text,
    label text,
    confidence real,
    signal_types text[],
    tools text[],
    usd double precision,
    latency_ms integer
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT v.created_at, v.kind, v.brand, v.label, v.confidence, v.signal_types, v.tools,
           v.usd, v.latency_ms
    FROM public.verdicts v
    ORDER BY v.created_at DESC
    LIMIT least(greatest(p_limit, 0), 200)
$$;

CREATE FUNCTION vigia_panel_today()
RETURNS TABLE (label text, total bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT v.label, count(*) FROM public.verdicts v
    WHERE v.created_at >= date_trunc('day', now() AT TIME ZONE 'America/Bogota')
        AT TIME ZONE 'America/Bogota'
    GROUP BY v.label
$$;

REVOKE ALL ON FUNCTION vigia_panel_verdicts(integer), vigia_panel_today() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION vigia_panel_verdicts(integer), vigia_panel_today() TO vigia_app;
