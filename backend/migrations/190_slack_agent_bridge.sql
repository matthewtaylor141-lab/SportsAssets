-- Transport only: existing persona service and recorded peer reviews.
CREATE TABLE IF NOT EXISTS agent_slack_delivery (
 delivery_id text PRIMARY KEY,
 agent text NOT NULL CHECK(agent IN ('derek','xavier','audrey')),
 team_id text NOT NULL,
 channel_id text NOT NULL,
 thread_ts text,
 source_key text NOT NULL,
 question text,
 answer text,
 message_id text,
 conversation_id text,
 state text NOT NULL DEFAULT 'QUEUED' CHECK(state IN ('QUEUED','WORKING','READY','SENDING','SENT','FAILED','DELIVERY_UNKNOWN')),
 claim_token text,
 lease_until timestamptz,
 attempts integer NOT NULL DEFAULT 0,
 slack_ts text,
 error_code text,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(agent,team_id,source_key)
);
CREATE INDEX IF NOT EXISTS agent_slack_pending ON agent_slack_delivery(state,created_at);
INSERT INTO ingestion_state(key,value) VALUES ('agent.slack.bridge','{"enabled":false}') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS agent_slack_control_audit (
 audit_id bigserial PRIMARY KEY,
 enabled boolean NOT NULL,
 actor text NOT NULL,
 changed_at timestamptz NOT NULL DEFAULT now()
);
