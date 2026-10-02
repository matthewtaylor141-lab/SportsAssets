-- 191: who asked (the verified management Slack user id) on each delivery, so
-- a research assignment made from Slack records its manager as the actor.
ALTER TABLE agent_slack_delivery ADD COLUMN IF NOT EXISTS requested_by text;
