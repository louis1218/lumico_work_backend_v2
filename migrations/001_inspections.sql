-- Run ONCE in pgAdmin as the postgres (master) user, on the `postgres` database,
-- BEFORE deploying the new API. It only adds things; nothing existing is changed.

-- 1. One row per tracker inspection (append-only; the report uses the latest per tracker per day)
CREATE TABLE IF NOT EXISTS inspections (
    id             bigserial PRIMARY KEY,
    site           text        NOT NULL,
    block          smallint    NOT NULL CHECK (block BETWEEN 1 AND 25),
    tracker        smallint    NOT NULL CHECK (tracker BETWEEN 1 AND 180),
    username       text        NOT NULL,
    work_date      date        NOT NULL,
    inspected_at   timestamptz NOT NULL DEFAULT now(),
    broken_panels  integer     NOT NULL DEFAULT 0 CHECK (broken_panels >= 0),
    defects        text[]      NOT NULL DEFAULT '{}',
    notes          text        NOT NULL DEFAULT '',
    photo_keys     text[]      NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS inspections_day_idx   ON inspections (site, work_date, block, tracker);
CREATE INDEX IF NOT EXISTS inspections_staff_idx ON inspections (username, work_date);

-- 2. Random login token, so admin pages can't be reached by guessing a session number
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS token text;
CREATE UNIQUE INDEX IF NOT EXISTS user_sessions_token_idx ON user_sessions (token) WHERE token IS NOT NULL;

-- 3. Permissions for the app user
GRANT SELECT, INSERT ON inspections TO lumico_app;
GRANT USAGE, SELECT ON SEQUENCE inspections_id_seq TO lumico_app;
