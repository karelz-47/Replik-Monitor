CREATE TABLE IF NOT EXISTS changes (
  ingest_id bigserial UNIQUE NOT NULL,
  source_id text PRIMARY KEY,
  company_ico text NOT NULL,
  changed_at timestamptz NOT NULL,
  title text NOT NULL,
  url text NOT NULL,
  discovered_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS changes_incremental_identity_idx ON changes(ingest_id);
CREATE TABLE IF NOT EXISTS outbox (
  id bigserial PRIMARY KEY,
  mode text NOT NULL CHECK (mode IN ('historical','incremental')),
  subject text NOT NULL,
  body text NOT NULL,
  source_count integer NOT NULL CHECK (source_count BETWEEN 1 AND 500),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','sent','failed')),
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  provider_id text,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz
);
CREATE TABLE IF NOT EXISTS checkpoints (
  name text PRIMARY KEY,
  value timestamptz NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS monitor_state (
  name text PRIMARY KEY,
  value text NOT NULL
);
