-- Blocks now have up to 500 trackers (was 300). Repairs have no limit in the database;
-- widen the inspections CHECK to match.
ALTER TABLE inspections DROP CONSTRAINT IF EXISTS inspections_tracker_check;
ALTER TABLE inspections ADD CONSTRAINT inspections_tracker_check CHECK (tracker BETWEEN 1 AND 500);
