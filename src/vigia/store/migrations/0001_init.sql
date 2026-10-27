-- Initial schema. Every tenant table carries business_id and is isolated by RLS.
-- PII columns are *_enc (AES-GCM, specs/16, T-04); nothing is stored in clear.
-- The application role vigia_app (no superuser, no BYPASSRLS) must already exist.

CREATE TABLE businesses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
    text_enc bytea,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE businesses ENABLE ROW LEVEL SECURITY;
ALTER TABLE businesses FORCE ROW LEVEL SECURITY;
CREATE POLICY business_isolation ON businesses
    USING (id = NULLIF(current_setting('app.business_id', true), '')::uuid)
    WITH CHECK (id = NULLIF(current_setting('app.business_id', true), '')::uuid);

ALTER TABLE cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE cases FORCE ROW LEVEL SECURITY;
CREATE POLICY business_isolation ON cases
    USING (business_id = NULLIF(current_setting('app.business_id', true), '')::uuid)
    WITH CHECK (business_id = NULLIF(current_setting('app.business_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON businesses, cases TO vigia_app;
