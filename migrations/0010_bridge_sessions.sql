-- Sessions and messages sent by a terminal coding harness to the browser.
-- Kept separate from provider conversations because bridge sessions are
-- short-lived transport state, not imported assistant history.

CREATE TABLE IF NOT EXISTS bridge_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider    TEXT NOT NULL,
    title       TEXT,
    model       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bridge_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES bridge_sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content         TEXT NOT NULL,
    content_sha256  TEXT NOT NULL,
    model           TEXT,
    sequence        INTEGER NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, sequence),
    UNIQUE (session_id, content_sha256)
);

CREATE INDEX IF NOT EXISTS bridge_messages_session_idx
    ON bridge_messages (session_id, sequence);
