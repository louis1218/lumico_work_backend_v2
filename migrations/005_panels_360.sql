-- Trackers now have up to 360 panels (was 90). Repairs have no limit in the database;
-- inspections had CHECK (panel BETWEEN 1 AND 90), so widen it.
ALTER TABLE inspections DROP CONSTRAINT IF EXISTS inspections_panel_check;
ALTER TABLE inspections ADD CONSTRAINT inspections_panel_check CHECK (panel BETWEEN 1 AND 360);
