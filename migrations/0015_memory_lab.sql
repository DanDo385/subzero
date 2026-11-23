-- 0015_memory_lab.sql
-- Memory lab: one triage decision per conversation, and the note classes the
-- PARA layout needs (area, archive, permanent) plus generated experiment notes.
--
-- Reverse (nothing else depends on these):
--   DELETE FROM notes WHERE id IN (SELECT note_id FROM memory_triage WHERE note_id IS NOT NULL);
--   DELETE FROM notes WHERE note_class = 'experiment';
--   DROP TABLE IF EXISTS memory_triage;
--   ALTER TABLE notes DROP CONSTRAINT IF EXISTS notes_note_class_check;
--   ALTER TABLE notes ADD CONSTRAINT notes_note_class_check CHECK (note_class IN
--     ('project','research','wiki','concept','resource','daily','inbox','session_index'));
--   DELETE FROM schema_migrations WHERE version = '0015_memory_lab';

ALTER TABLE notes DROP CONSTRAINT IF EXISTS notes_note_class_check;
ALTER TABLE notes ADD CONSTRAINT notes_note_class_check
    CHECK (note_class IN ('project','research','wiki','concept','resource','daily','inbox',
                          'session_index','area','archive','permanent','experiment'));

CREATE TABLE IF NOT EXISTS memory_triage (
    conversation_id         UUID PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    classification_event_id UUID REFERENCES classification_events(id) ON DELETE SET NULL,
    questions_version       TEXT NOT NULL,
    outcome                 TEXT NOT NULL CHECK (outcome IN ('note','inbox','log','discard')),
    bucket                  TEXT,
    reason                  TEXT NOT NULL,
    gate                    TEXT NOT NULL,
    candidates              TEXT[] NOT NULL DEFAULT '{}',
    scores                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    note_id                 UUID REFERENCES notes(id) ON DELETE SET NULL,
    -- Set when a person (or the weekly analysis) moves an inbox draft on.
    promoted_at             TIMESTAMPTZ,
    decided_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS memory_triage_outcome_idx ON memory_triage (outcome, decided_at DESC);
