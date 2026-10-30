-- Short case number per business (T-10b, D-091): the member sees "caso 14", never the UUID.
-- businesses.case_seq is the counter; the row lock of its UPDATE serializes the numbering of
-- one business, so two cases never share a number. Not PII: a count, under the same RLS.

ALTER TABLE businesses ADD COLUMN case_seq integer NOT NULL DEFAULT 0;

-- Cases stored before this migration keep ref NULL: their replies already went out.
ALTER TABLE cases ADD COLUMN ref integer;
CREATE UNIQUE INDEX cases_business_ref ON cases (business_id, ref);
