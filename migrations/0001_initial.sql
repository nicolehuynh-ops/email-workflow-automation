CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_runs (
  id TEXT PRIMARY KEY,
  campaign_slug TEXT NOT NULL,
  configuration_digest TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('dry_run', 'review', 'apply')),
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES campaign_runs(id),
  reply_contact_id TEXT,
  email TEXT NOT NULL,
  company_key TEXT,
  sequence_step_id TEXT,
  sender_email TEXT,
  source_json TEXT NOT NULL,
  UNIQUE(run_id, email)
);

CREATE TABLE IF NOT EXISTS source_evidence (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES campaign_runs(id),
  contact_email TEXT,
  source_type TEXT NOT NULL,
  source_id TEXT,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppression_decisions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES campaign_runs(id),
  contact_email TEXT,
  suppression_type TEXT NOT NULL CHECK (suppression_type IN ('exact_contact', 'domain_company')),
  match_key TEXT NOT NULL,
  reason TEXT NOT NULL,
  proposed_action TEXT NOT NULL CHECK (proposed_action IN ('finish', 'advance', 'hold_for_review', 'no_change')),
  confidence REAL,
  status TEXT NOT NULL CHECK (status IN ('dry_run', 'pending_review', 'approved', 'rejected', 'applied', 'failed')),
  idempotency_key TEXT NOT NULL UNIQUE,
  decision_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY,
  decision_id TEXT NOT NULL REFERENCES suppression_decisions(id),
  reviewer TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('approved', 'rejected')),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reply_actions (
  id INTEGER PRIMARY KEY,
  decision_id TEXT NOT NULL REFERENCES suppression_decisions(id),
  requested_at TEXT NOT NULL,
  status TEXT NOT NULL,
  request_json TEXT NOT NULL,
  response_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(decision_id)
);
