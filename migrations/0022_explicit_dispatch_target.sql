-- Explicit target fields have their own routing label. Keep older job history
-- readable and allow older servers to write their existing routing label.
ALTER TABLE dispatch_jobs DROP CONSTRAINT dispatch_jobs_routed_by_check;
ALTER TABLE dispatch_jobs ADD CONSTRAINT dispatch_jobs_routed_by_check
    CHECK (routed_by IN ('explicit', 'sigil', 'jev', 'default'));
