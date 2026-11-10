-- Device tokens gain scopes. Existing tokens (the browser extension's) keep
-- only what they could already do; agent routes need 'agents', granted only
-- by a pairing code made with `subzero pair-device --agents`.
ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT '{ingest}';
ALTER TABLE pairing_codes ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT '{ingest}';
