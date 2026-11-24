-- The worker asks the server how long to wait before its next lease, and the
-- server answers from how recently work arrived. That answer is one EXISTS over
-- sync_jobs.created_at on every poll, so give it an index: the table has no
-- retention and grows without bound, and a seq scan on every poll is exactly the
-- cost this change is meant to remove.
CREATE INDEX IF NOT EXISTS sync_jobs_created_idx ON sync_jobs (created_at DESC);
