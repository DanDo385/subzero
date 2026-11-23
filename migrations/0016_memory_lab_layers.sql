-- 0016_memory_lab_layers.sql
-- Which memory layers each triaged session feeds, and when each was done.
--
-- Reverse:
--   ALTER TABLE memory_triage DROP COLUMN IF EXISTS layers,
--     DROP COLUMN IF EXISTS extracted_at, DROP COLUMN IF EXISTS honcho_synced_at;
--   DELETE FROM schema_migrations WHERE version = '0016_memory_lab_layers';

ALTER TABLE memory_triage
    ADD COLUMN IF NOT EXISTS layers           TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS extracted_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS honcho_synced_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS memory_triage_layers_idx ON memory_triage USING gin (layers);
