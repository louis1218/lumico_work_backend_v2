-- Run ONCE in pgAdmin as the postgres (master) user, on the `postgres` database.
-- Safe to run before OR after deploying the new API. It only adds an index; nothing is changed.
--
-- Backstop for offline uploads: one tracker check (batch_id) can never contain the same panel
-- twice, even if a phone retries at the exact same moment. The "no issues" row (panel NULL)
-- counts as panel 0, so it is also limited to one per batch.
CREATE UNIQUE INDEX IF NOT EXISTS inspections_batch_panel_uidx
    ON inspections (batch_id, (COALESCE(panel, 0)));
