-- Undo on the receipt and more time on a preview (ADR-021).
-- An executed action can be undone for a short window: the proposal becomes 'undone' and a Slack
-- post still waiting out that window is 'cancelled' instead of sent.
ALTER TABLE action_proposals DROP CONSTRAINT action_proposals_status_check;
ALTER TABLE action_proposals ADD CONSTRAINT action_proposals_status_check
  CHECK (status IN ('pending','executed','expired','superseded','failed','undone'));
ALTER TABLE action_proposals ADD COLUMN undone_at timestamptz;

ALTER TABLE outbox DROP CONSTRAINT outbox_status_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_status_check
  CHECK (status IN ('pending','sent','dead','cancelled'));
