-- Least privilege for the application role (security review, plan phase 2).
--
-- 0004 granted wf_app full DML on every public table. Three of them need much less:
--
--   schema_migrations  the migrator's ledger. Only the owner role (migrator job) touches it; if the app
--                      role could rewrite it, a tampered ledger could make the next deploy skip or repeat
--                      a migration.
--   workspaces         tenants. The app reads them (workers visit every workspace) and moves the demo
--                      clock, but never creates or deletes one: the seeder runs as the owner. With DELETE
--                      granted, one injected statement could erase a tenant and, by cascade, its data.
--   audit_log          the accountability trail. The app appends to it and the sweeper purges it after
--                      30 days. It never edits an entry, and nothing but retention may delete one: a
--                      compromised app must not be able to erase what it just did.
--
-- Everything here is a REVOKE, a column-level GRANT or a RESTRICTIVE policy for wf_app, so the owner
-- role (seeder, migrator, tests) is unaffected and the change is safe to apply to a live database.

REVOKE ALL ON schema_migrations FROM wf_app;

REVOKE INSERT, UPDATE, DELETE ON workspaces FROM wf_app;
GRANT UPDATE (clock_offset) ON workspaces TO wf_app;

REVOKE UPDATE ON audit_log FROM wf_app;

-- Restrictive policies are ANDed with the tenant policy from 0002. This one lets wf_app delete only
-- entries older than 7 days: below the 30-day retention purge, above anything worth hiding.
CREATE POLICY audit_retention_only ON audit_log AS RESTRICTIVE FOR DELETE TO wf_app
  USING (at < now() - interval '7 days');
