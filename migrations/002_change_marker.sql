-- A proceeding can receive a later REPLIK event; its ID alone is not a change key.
ALTER TABLE changes ADD COLUMN IF NOT EXISTS change_marker text;
UPDATE changes SET change_marker = 'legacy:' || changed_at::text WHERE change_marker IS NULL;
ALTER TABLE changes ALTER COLUMN change_marker SET NOT NULL;
ALTER TABLE changes DROP CONSTRAINT IF EXISTS changes_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS changes_proceeding_state_idx ON changes(source_id, change_marker);
ALTER TABLE outbox DROP CONSTRAINT IF EXISTS outbox_source_count_check;
DO $$
BEGIN
  ALTER TABLE outbox ADD CONSTRAINT outbox_source_count_check CHECK (source_count >= 1);
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;
