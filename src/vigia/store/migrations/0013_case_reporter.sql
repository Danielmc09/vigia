-- The owner panel's first question: who reported this case (D-127). Nullable: older rows have
-- none, and a removed member must not break the row.
ALTER TABLE cases ADD COLUMN member_id uuid REFERENCES members (id) ON DELETE SET NULL;
