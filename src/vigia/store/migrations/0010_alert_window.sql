-- Meta's 24 h window (T-18b, D-097): a member is reachable with free text only within 24 h
-- of their last message. The alert that could not go out is kept on the verdict, so the
-- panel says why instead of staying silent (specs/01).

ALTER TABLE members ADD COLUMN last_inbound_at timestamptz;
ALTER TABLE verdicts ADD COLUMN alert_rejections text[] NOT NULL DEFAULT '{}';

DROP FUNCTION vigia_panel_verdicts(integer);

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
    latency_ms integer,
    alert_rejections text[]
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT v.created_at, v.kind, v.brand, v.label, v.confidence, v.signal_types, v.tools,
           v.usd, v.latency_ms, v.alert_rejections
    FROM public.verdicts v
    ORDER BY v.created_at DESC
    LIMIT least(greatest(p_limit, 0), 200)
$$;

REVOKE ALL ON FUNCTION vigia_panel_verdicts(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION vigia_panel_verdicts(integer) TO vigia_app;
