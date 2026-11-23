-- The browser extension captures Muse (Meta's assistant at muse.ai). Ingest only
-- accepts providers listed here, so without this row every Muse capture was
-- refused with "unknown provider".
INSERT INTO providers (id, name) VALUES ('muse', 'Muse') ON CONFLICT (id) DO NOTHING;
