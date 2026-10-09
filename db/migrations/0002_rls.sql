-- Row-level security on every tenant table (DD §13.2-13.3).
-- Each request transaction runs `SELECT set_config('app.workspace_id', $1, true)`.
-- If the setting is missing the policy matches nothing: the failure mode is closed.
-- FORCE makes the policy apply to the table owner too. Superusers still bypass RLS,
-- so services must connect as wf_app (0004), never as the owner.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'users','user_prefs','accounts','contacts','deals','activities','deal_history',
    'risk_scores','risk_score_daily','tasks','standing_orders','alerts',
    'action_proposals','triage_jobs','outbox','audit_log'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY ws_isolation ON %I
         USING (workspace_id = current_setting(''app.workspace_id'', true))
         WITH CHECK (workspace_id = current_setting(''app.workspace_id'', true))', t);
  END LOOP;
END $$;
