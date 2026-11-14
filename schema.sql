PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS races (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  pattern TEXT NOT NULL,
  winner TEXT NOT NULL,
  side TEXT NOT NULL,
  price_ticks INTEGER NOT NULL,
  qty INTEGER NOT NULL,
  book_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_calls (
  id INTEGER PRIMARY KEY,
  race_id INTEGER REFERENCES races(id),
  provider TEXT NOT NULL,
  role TEXT NOT NULL,
  method TEXT NOT NULL,
  http_status INTEGER,
  ok INTEGER NOT NULL,
  duration_ms REAL NOT NULL,
  result_preview TEXT,
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_calls_provider_role
  ON api_calls(provider, role);

DROP VIEW IF EXISTS provider_stats;
CREATE VIEW provider_stats AS
SELECT
  provider,
  role,
  count(*) AS calls,
  sum(ok) AS ok_calls,
  round(avg(duration_ms), 2) AS avg_ms,
  round(min(duration_ms), 2) AS best_ms,
  round(max(duration_ms), 2) AS worst_ms
FROM api_calls
GROUP BY provider, role;

DROP VIEW IF EXISTS scoreboard;
CREATE VIEW scoreboard AS
SELECT winner AS provider, count(*) AS fills
FROM races
WHERE winner IN ('alchemy', 'infura')
GROUP BY winner;
