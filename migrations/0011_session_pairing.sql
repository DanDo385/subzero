-- Authenticated pairing and one-way publication for bridge sessions.
--
-- A bridge session now has an owner (the publisher that created it), a
-- declared capture scope, and typed events. A browser reads a session only
-- through an active grant, and a grant is created only when the session's
-- owner redeems a short-lived, single-use challenge that the browser's own
-- device token requested. Grants carry one capability: read. There is no
-- browser-to-agent write path in this schema.

-- Existing rows were created with the harness key, so they belong to it.
ALTER TABLE bridge_sessions
    ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'harness-key';
-- New rows must name their owner explicitly; a missing owner fails closed.
ALTER TABLE bridge_sessions ALTER COLUMN owner DROP DEFAULT;
-- What the publisher promises to capture. Existing sessions carried messages.
ALTER TABLE bridge_sessions
    ADD COLUMN IF NOT EXISTS capture TEXT[] NOT NULL DEFAULT '{message}';
CREATE INDEX IF NOT EXISTS bridge_sessions_owner_idx ON bridge_sessions (owner, created_at DESC);

-- Messages become typed events. The table keeps its name so the receive
-- contract (GET /api/v1/sessions/{id} returns "messages") is unchanged.
ALTER TABLE bridge_messages
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'message'
        CHECK (kind IN ('message', 'tool_call', 'patch', 'test_result', 'approval')),
    ADD COLUMN IF NOT EXISTS event_key TEXT,
    ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS completeness TEXT NOT NULL DEFAULT 'complete'
        CHECK (completeness IN ('complete', 'partial')),
    ADD COLUMN IF NOT EXISTS omissions TEXT[] NOT NULL DEFAULT '{}';

-- The old idempotency key was the content hash; keep it for existing rows so
-- a harness that resends an old message still gets "duplicate".
UPDATE bridge_messages SET event_key = 'sha256:' || content_sha256 WHERE event_key IS NULL;
ALTER TABLE bridge_messages ALTER COLUMN event_key SET NOT NULL;
ALTER TABLE bridge_messages
    DROP CONSTRAINT IF EXISTS bridge_messages_session_id_content_sha256_key;
CREATE UNIQUE INDEX IF NOT EXISTS bridge_messages_event_key_idx
    ON bridge_messages (session_id, event_key);

-- A challenge is requested by one browser (device token) for one session and
-- is redeemed by that session's owner. Only a hash of the code is stored.
CREATE TABLE IF NOT EXISTS bridge_pairing_challenges (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID NOT NULL REFERENCES bridge_sessions(id) ON DELETE CASCADE,
    device_id     TEXT NOT NULL REFERENCES device_tokens(device_id) ON DELETE CASCADE,
    code_sha256   TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    redeemed_at   TIMESTAMPTZ,
    redeemed_by   TEXT,
    cancelled_at  TIMESTAMPTZ,
    CHECK (expires_at > created_at),
    CHECK (redeemed_at IS NULL OR cancelled_at IS NULL)
);

-- At most one open challenge per browser per session: a new request cancels
-- the previous one first.
CREATE UNIQUE INDEX IF NOT EXISTS bridge_pairing_challenges_open_idx
    ON bridge_pairing_challenges (session_id, device_id)
    WHERE redeemed_at IS NULL AND cancelled_at IS NULL;
CREATE INDEX IF NOT EXISTS bridge_pairing_challenges_code_idx
    ON bridge_pairing_challenges (session_id, code_sha256);
CREATE INDEX IF NOT EXISTS bridge_pairing_challenges_device_idx
    ON bridge_pairing_challenges (device_id, created_at);

-- Read access for one browser to one session.
CREATE TABLE IF NOT EXISTS bridge_session_grants (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID NOT NULL REFERENCES bridge_sessions(id) ON DELETE CASCADE,
    device_id     TEXT NOT NULL REFERENCES device_tokens(device_id) ON DELETE CASCADE,
    challenge_id  UUID NOT NULL UNIQUE REFERENCES bridge_pairing_challenges(id) ON DELETE CASCADE,
    capability    TEXT NOT NULL DEFAULT 'read' CHECK (capability = 'read'),
    granted_by    TEXT NOT NULL,
    granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at    TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS bridge_session_grants_active_idx
    ON bridge_session_grants (session_id, device_id)
    WHERE revoked_at IS NULL;

-- Wrong codes offered for a session, for the redemption rate limit.
CREATE TABLE IF NOT EXISTS bridge_pairing_failures (
    id            BIGSERIAL PRIMARY KEY,
    session_id    UUID NOT NULL REFERENCES bridge_sessions(id) ON DELETE CASCADE,
    attempted_by  TEXT NOT NULL,
    attempted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS bridge_pairing_failures_session_idx
    ON bridge_pairing_failures (session_id, attempted_at);
