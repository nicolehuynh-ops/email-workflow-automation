CREATE TABLE IF NOT EXISTS apply_locks (
  campaign_slug TEXT PRIMARY KEY,
  token TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
