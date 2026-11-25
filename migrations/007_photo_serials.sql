-- Panel serial numbers read from repair photos, one row per photo.
-- Filled only when an admin opens the client repair report, so each photo is read once.
CREATE TABLE IF NOT EXISTS photo_serials (
    photo_key text        PRIMARY KEY,            -- the photo's S3 key, as in repairs.before_keys / after_keys
    serial    text,                               -- the serial number, when it could be read
    status    text        NOT NULL CHECK (status IN ('read', 'unreadable', 'error')),
    read_at   timestamptz NOT NULL DEFAULT now()
);
