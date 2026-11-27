-- 0023: the sigil vocabulary is gone. 'sigil' rows from the old flow are
-- already historical; map them to 'explicit' (a sigil was an explicit
-- target choice), then tighten the check to the current vocabulary.
UPDATE dispatch_jobs SET routed_by = 'explicit' WHERE routed_by = 'sigil';

ALTER TABLE dispatch_jobs DROP CONSTRAINT dispatch_jobs_routed_by_check;

ALTER TABLE dispatch_jobs ADD CONSTRAINT dispatch_jobs_routed_by_check
    CHECK (routed_by IN ('explicit', 'jev', 'default'));
