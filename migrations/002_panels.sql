-- Run ONCE in pgAdmin as the postgres (master) user, on the `postgres` database,
-- AFTER 001_inspections.sql and BEFORE deploying the new API. It only adds things.

-- One row per broken panel (panel 1 to 90). A tracker with nothing broken is a single row with panel = NULL.
ALTER TABLE inspections ADD COLUMN IF NOT EXISTS panel smallint CHECK (panel BETWEEN 1 AND 90);

-- Rows saved together in one tracker check share a batch_id, so a re-check replaces the whole earlier check.
ALTER TABLE inspections ADD COLUMN IF NOT EXISTS batch_id uuid NOT NULL DEFAULT gen_random_uuid();
CREATE INDEX IF NOT EXISTS inspections_batch_idx ON inspections (batch_id);
