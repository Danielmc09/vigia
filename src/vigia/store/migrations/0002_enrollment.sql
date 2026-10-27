-- Enrollment, whitelist and encrypted PII (specs/01, specs/02, specs/16, T-04).
-- *_enc: AES-256-GCM under the business DEK; *_hash: HMAC-SHA256 under PII_HMAC_KEY.
-- No column holds a phone, email, name or message in clear.

ALTER TABLE businesses
    ADD COLUMN dek_enc bytea NOT NULL,
    ADD COLUMN name_enc bytea NOT NULL,
    ADD COLUMN country text NOT NULL,
    ADD COLUMN language text NOT NULL;

ALTER TABLE cases
    ADD COLUMN kind text NOT NULL,
    ADD COLUMN origin text NOT NULL,
    ADD COLUMN message_id_hash bytea;
CREATE UNIQUE INDEX cases_message_once ON cases (business_id, message_id_hash);

CREATE TABLE members (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('owner', 'employee')),
    phone_hash bytea NOT NULL UNIQUE,  -- one number belongs to one business
    phone_enc bytea NOT NULL,
    name_enc bytea,
    email_hash bytea UNIQUE,           -- set only once the email is verified
    email_enc bytea,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE suppliers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
    name_enc bytea NOT NULL,
    email_hash bytea,
    email_enc bytea,
    account_hash bytea,
    account_enc bytea,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE verification_codes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
    member_id uuid NOT NULL REFERENCES members (id) ON DELETE CASCADE,
    code_hash bytea NOT NULL,
    email_hash bytea NOT NULL,
    email_enc bytea NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE TABLE verdicts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
    case_id uuid REFERENCES cases (id) ON DELETE CASCADE,
    label text NOT NULL CHECK (label IN ('scam', 'legit', 'doubtful')),
    payload_enc bytea,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX verdicts_recent ON verdicts (business_id, created_at DESC);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['members', 'suppliers', 'verification_codes', 'verdicts'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY business_isolation ON %I'
            ' USING (business_id = NULLIF(current_setting(''app.business_id'', true), '''')::uuid)'
            ' WITH CHECK (business_id = NULLIF(current_setting(''app.business_id'', true), '''')::uuid)',
            t);
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON members, suppliers, verification_codes, verdicts
    TO vigia_app;

-- Senders that are not enrolled (specs/01). No business to scope them to, so RLS is
-- forced with no policy and vigia_app has no grants: only the function below writes here.
CREATE TABLE rejected_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sender_hash bytea NOT NULL,
    origin text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE rejected_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rejected_attempts FORCE ROW LEVEL SECURITY;

-- Whitelist lookup before the business is known (D-026). Runs as the schema owner, which
-- bypasses RLS, and returns only ids and role for an exact hash match.
CREATE FUNCTION vigia_find_member(p_hash bytea)
RETURNS TABLE (member_id uuid, business_id uuid, role text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT m.id, m.business_id, m.role FROM members m
    WHERE m.phone_hash = p_hash OR m.email_hash = p_hash
$$;

CREATE FUNCTION vigia_record_rejection(p_hash bytea, p_origin text)
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    INSERT INTO rejected_attempts (sender_hash, origin) VALUES (p_hash, p_origin)
$$;

REVOKE ALL ON FUNCTION vigia_find_member(bytea), vigia_record_rejection(bytea, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION vigia_find_member(bytea), vigia_record_rejection(bytea, text)
    TO vigia_app;
