-- Panel repairs: one row per repaired panel, with before and after photos.
CREATE TABLE IF NOT EXISTS repairs (
    id          BIGSERIAL PRIMARY KEY,
    batch_id    uuid        NOT NULL,           -- made by the phone, so a repeat upload is recognised
    site        text        NOT NULL,
    block       integer     NOT NULL,
    tracker     integer     NOT NULL,
    panel       integer     NOT NULL,
    action      text        NOT NULL CHECK (action IN ('fix', 'replace')),
    username    text        NOT NULL,
    work_date   date        NOT NULL,
    notes       text        NOT NULL DEFAULT '',
    before_keys text[]      NOT NULL DEFAULT '{}',
    after_keys  text[]      NOT NULL DEFAULT '{}',
    repaired_at timestamptz NOT NULL DEFAULT now()
);

-- Backstop: the same batch can never save the same panel twice
CREATE UNIQUE INDEX IF NOT EXISTS repairs_batch_panel_uidx ON repairs (batch_id, panel);
CREATE INDEX IF NOT EXISTS repairs_location_idx ON repairs (site, block, tracker, panel);
CREATE INDEX IF NOT EXISTS repairs_user_day_idx ON repairs (username, site, work_date);
