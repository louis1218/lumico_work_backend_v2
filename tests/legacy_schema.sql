-- TEST ONLY: stand-ins for the two existing production tables the new code touches.
CREATE TABLE users (
    username text PRIMARY KEY, password_hash text NOT NULL, first_name text, last_name text,
    role text NOT NULL DEFAULT 'staff', is_active boolean NOT NULL DEFAULT true, updated_at timestamptz
);
CREATE TABLE user_sessions (
    id serial PRIMARY KEY, username text NOT NULL, site text, login_time timestamptz NOT NULL DEFAULT now(),
    logout_time timestamptz
);
CREATE TABLE timesheet (id serial PRIMARY KEY, username text, site text, sign_in timestamptz, sign_out timestamptz);
-- Same grants the production lumico_app user got
CREATE ROLE lumico_app LOGIN PASSWORD 'testpw';
GRANT CONNECT ON DATABASE lumico_test TO lumico_app;
GRANT USAGE ON SCHEMA public TO lumico_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO lumico_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lumico_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO lumico_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO lumico_app;
