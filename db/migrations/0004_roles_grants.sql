-- Database roles (DD §13.3). Roles are created NOLOGIN here; credentials are set
-- outside migrations (db/migrate.py --dev-passwords locally, Secrets Manager in AWS).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wf_app') THEN
    CREATE ROLE wf_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wf_auth') THEN
    CREATE ROLE wf_auth NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wf_readonly') THEN
    CREATE ROLE wf_readonly NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

-- wf_app: DML on public tables, subject to forced RLS. No access to the auth schema.
GRANT USAGE ON SCHEMA public TO wf_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO wf_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO wf_app;
REVOKE ALL ON SCHEMA auth FROM wf_app;

-- wf_auth: the authorization server, auth schema only.
GRANT USAGE ON SCHEMA auth TO wf_auth;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA auth TO wf_auth;

-- wf_readonly: dashboards and debugging; RLS still applies.
GRANT USAGE ON SCHEMA public TO wf_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO wf_readonly;
