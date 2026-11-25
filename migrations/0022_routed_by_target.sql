-- 0022: sigils removed; routed_by 'sigil' becomes 'target'.
-- Existing rows keep their value only where it still parses under the new
-- vocabulary: 'sigil' was an explicit target, so it maps to 'target'.

UPDATE dispatch_jobs SET routed_by = 'target' WHERE routed_by = 'sigil';

ALTER TABLE dispatch_jobs DROP CONSTRAINT dispatch_jobs_routed_by_check;

ALTER TABLE dispatch_jobs ADD CONSTRAINT dispatch_jobs_routed_by_check
    CHECK (routed_by IN ('target', 'jev', 'default'));
