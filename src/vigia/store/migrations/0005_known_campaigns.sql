-- Known scam campaigns (specs/10 §4, T-06b). Global: no business_id, shared by every
-- business, so only fingerprints, domains and brand are stored, never a message (D-017).
-- The application role reads it; only the schema owner writes it.

CREATE TABLE known_campaigns (
    id text PRIMARY KEY,
    country text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('text', 'domain', 'image')),
    brand text,
    simhash bytea CHECK (octet_length(simhash) = 8),
    phash bytea CHECK (octet_length(phash) = 8),
    domains text[] NOT NULL DEFAULT '{}',
    source_url text NOT NULL,
    source_type text NOT NULL
        CHECK (source_type IN ('alerta_oficial', 'banco', 'prensa', 'policia', 'foro', 'usuario')),
    seen_date date NOT NULL,
    times_seen int NOT NULL DEFAULT 1
);
CREATE INDEX known_campaigns_country ON known_campaigns (country);

GRANT SELECT ON known_campaigns TO vigia_app;
