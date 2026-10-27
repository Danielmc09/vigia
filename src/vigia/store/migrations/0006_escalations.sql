-- Escalations of specs/05: irreversible actions waiting for "confirmar {id}" (T-07, B7).
-- The target is PII (a phone, an email), so it is sealed under the business DEK (specs/16).

CREATE TABLE escalations (
    id text NOT NULL,
    business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
    action text NOT NULL
        CHECK (action IN ('report', 'block_sender', 'delete_case', 'transfer_ownership')),
    target_enc bytea NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'confirmed', 'cancelled', 'expired')),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (business_id, id)
);

ALTER TABLE escalations ENABLE ROW LEVEL SECURITY;
ALTER TABLE escalations FORCE ROW LEVEL SECURITY;
CREATE POLICY business_isolation ON escalations
    USING (business_id = NULLIF(current_setting('app.business_id', true), '')::uuid)
    WITH CHECK (business_id = NULLIF(current_setting('app.business_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE ON escalations TO vigia_app;
