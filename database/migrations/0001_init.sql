-- 0001: D1 persistent public catalog / private-capable normalized model.
-- No API tokens, source URLs, private target URLs or secrets stored in this schema.
-- Public D1 instance is for general reference data; owner-specific profiles
-- must be stored separately and protected by authenticated backend policies.

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS proxy_endpoints (
  ip TEXT NOT NULL,
  port INTEGER NOT NULL CHECK(port BETWEEN 1 AND 65535),
  protocol TEXT NOT NULL CHECK(protocol IN ('http','socks4','socks5')),
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY(ip,port,protocol)
);
CREATE INDEX IF NOT EXISTS idx_proxy_last_seen ON proxy_endpoints(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS proxy_sources (
  ip TEXT NOT NULL,
  port INTEGER NOT NULL,
  protocol TEXT NOT NULL,
  source TEXT NOT NULL CHECK(length(source) BETWEEN 1 AND 80),
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY(ip,port,protocol,source),
  FOREIGN KEY(ip,port,protocol)
    REFERENCES proxy_endpoints(ip,port,protocol) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_proxy_sources_source ON proxy_sources(source,last_seen_at DESC);

-- Only the approved fixed reference check. No arbitrary target URL here.
CREATE TABLE IF NOT EXISTS reference_observations (
  event_id TEXT PRIMARY KEY NOT NULL,
  ip TEXT NOT NULL,
  port INTEGER NOT NULL,
  protocol TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN ('success','proxy_error','unknown')),
  latency_ms REAL CHECK(latency_ms IS NULL OR latency_ms BETWEEN 0 AND 600000),
  checked_at TEXT NOT NULL,
  origin_run TEXT,
  FOREIGN KEY(ip,port,protocol)
    REFERENCES proxy_endpoints(ip,port,protocol) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reference_endpoint
  ON reference_observations(ip,port,protocol,checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_reference_retention
  ON reference_observations(checked_at);

-- Generic, private-capable observations keyed by opaque IDs only.
-- A separate protected DB is recommended for the user's real projects.
CREATE TABLE IF NOT EXISTS target_observations (
  event_id TEXT PRIMARY KEY NOT NULL,
  project_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  ip TEXT NOT NULL,
  port INTEGER NOT NULL,
  protocol TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN (
    'success','proxy_error','network_error','target_denied',
    'rate_limited','challenge','unknown')),
  latency_ms REAL CHECK(latency_ms IS NULL OR latency_ms BETWEEN 0 AND 600000),
  http_status INTEGER CHECK(http_status IS NULL OR http_status BETWEEN 100 AND 599),
  verified_https INTEGER NOT NULL DEFAULT 0 CHECK(verified_https IN (0,1)),
  checked_at TEXT NOT NULL,
  FOREIGN KEY(ip,port,protocol)
    REFERENCES proxy_endpoints(ip,port,protocol) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_target_rank
  ON target_observations(project_id,target_id,protocol,ip,port,checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_target_retention ON target_observations(checked_at);

-- Materialized aggregates survive the bounded raw-event retention period.
CREATE TABLE IF NOT EXISTS ranking_snapshots (
  scope TEXT NOT NULL CHECK(scope IN ('reference','target')),
  project_id TEXT NOT NULL DEFAULT '',
  target_id TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL,
  port INTEGER NOT NULL,
  protocol TEXT NOT NULL,
  successes INTEGER NOT NULL DEFAULT 0 CHECK(successes >= 0),
  failures INTEGER NOT NULL DEFAULT 0 CHECK(failures >= 0),
  restrictions INTEGER NOT NULL DEFAULT 0 CHECK(restrictions >= 0),
  unknown_count INTEGER NOT NULL DEFAULT 0 CHECK(unknown_count >= 0),
  wilson_score REAL CHECK(wilson_score IS NULL OR wilson_score BETWEEN 0 AND 100),
  last_outcome TEXT,
  last_checked_at TEXT,
  computed_at TEXT NOT NULL,
  PRIMARY KEY(scope,project_id,target_id,ip,port,protocol),
  FOREIGN KEY(ip,port,protocol)
    REFERENCES proxy_endpoints(ip,port,protocol) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ranking_score
  ON ranking_snapshots(scope,project_id,target_id,protocol,wilson_score DESC);

CREATE TABLE IF NOT EXISTS collection_runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  endpoints_seen INTEGER NOT NULL DEFAULT 0 CHECK(endpoints_seen >= 0),
  new_endpoints INTEGER NOT NULL DEFAULT 0 CHECK(new_endpoints >= 0),
  checks_attempted INTEGER NOT NULL DEFAULT 0 CHECK(checks_attempted >= 0),
  checks_passed INTEGER NOT NULL DEFAULT 0 CHECK(checks_passed >= 0),
  checks_failed INTEGER NOT NULL DEFAULT 0 CHECK(checks_failed >= 0),
  status TEXT NOT NULL CHECK(status IN ('running','completed','failed','partial'))
);
CREATE INDEX IF NOT EXISTS idx_collection_runs_started ON collection_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS daily_statistics (
  day TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN ('reference','target')),
  project_id TEXT NOT NULL DEFAULT '',
  target_id TEXT NOT NULL DEFAULT '',
  protocol TEXT NOT NULL,
  attempted INTEGER NOT NULL DEFAULT 0 CHECK(attempted >= 0),
  successes INTEGER NOT NULL DEFAULT 0 CHECK(successes >= 0),
  failures INTEGER NOT NULL DEFAULT 0 CHECK(failures >= 0),
  restrictions INTEGER NOT NULL DEFAULT 0 CHECK(restrictions >= 0),
  updated_at TEXT NOT NULL,
  PRIMARY KEY(day,scope,project_id,target_id,protocol)
);

INSERT OR IGNORE INTO schema_migrations(version,name)
VALUES(1,'persistent_catalog_reference_target_rankings');
