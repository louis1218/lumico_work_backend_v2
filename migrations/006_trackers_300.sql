-- Blocks now have up to 300 trackers (was 180). Repairs have no limit in the database;
-- inspections had CHECK (tracker BETWEEN 1 AND 180), so widen it.
ALTER TABLE inspections DROP CONSTRAINT IF EXISTS inspections_tracker_check;
ALTER TABLE inspections ADD CONSTRAINT inspections_tracker_check CHECK (tracker BETWEEN 1 AND 300);
