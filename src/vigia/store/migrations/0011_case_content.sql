-- A forwarded copy of a case (D-106): same content, new message id. content_hash is the keyed
-- hash of the text or of the file's bytes, so a fresh verdict is repeated instead of paying the
-- models again. Keyed like message_id_hash: equal content is visible, the content is not.

-- Cases stored before this migration keep NULL: they are never repeated, only reanalyzed.
ALTER TABLE cases ADD COLUMN content_hash bytea;
CREATE INDEX cases_content ON cases (business_id, content_hash);
