-- BETTOR live-game display sidecar. No PAPER, trading, control or settlement table is altered.
-- Installer allocates a NEW migration number; never amend an applied migration.
CREATE OR REPLACE FUNCTION trader_display_score_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE='integrity_constraint_violation';
END;
$$;

CREATE TABLE IF NOT EXISTS trader_display_score_bindings (
  binding_id text PRIMARY KEY,
  venue text NOT NULL CHECK(venue IN ('POLYMARKET_US','KALSHI')),
  event_id text NOT NULL CHECK(length(event_id) BETWEEN 1 AND 250),
  provider text NOT NULL CHECK(provider IN ('ESPN','THE_ODDS_API')),
  fixture_fingerprint text NOT NULL,
  payload jsonb NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  CHECK(octet_length(payload::text) <= 32768)
);
CREATE INDEX IF NOT EXISTS trader_display_score_binding_identity
  ON trader_display_score_bindings(venue,event_id,provider,fixture_fingerprint,recorded_at DESC);
CREATE TRIGGER trader_display_score_bindings_immutable BEFORE UPDATE OR DELETE
  ON trader_display_score_bindings FOR EACH ROW EXECUTE FUNCTION trader_display_score_append_only();

CREATE TABLE IF NOT EXISTS trader_display_score_observations (
  observation_id text PRIMARY KEY,
  binding_id text NOT NULL REFERENCES trader_display_score_bindings(binding_id),
  venue text NOT NULL CHECK(venue IN ('POLYMARKET_US','KALSHI')),
  event_id text NOT NULL,
  provider text NOT NULL CHECK(provider IN ('ESPN','THE_ODDS_API')),
  received_at timestamptz NOT NULL,
  source_at timestamptz,
  payload jsonb NOT NULL,
  authority text NOT NULL DEFAULT 'DISPLAY_ONLY_NOT_A_PRICE_OR_SETTLEMENT_SOURCE'
    CHECK(authority='DISPLAY_ONLY_NOT_A_PRICE_OR_SETTLEMENT_SOURCE'),
  CHECK(octet_length(payload::text) <= 32768)
);
CREATE INDEX IF NOT EXISTS trader_display_score_observation_event
  ON trader_display_score_observations(venue,event_id,received_at DESC);
CREATE TRIGGER trader_display_score_observations_immutable BEFORE UPDATE OR DELETE
  ON trader_display_score_observations FOR EACH ROW EXECUTE FUNCTION trader_display_score_append_only();

-- Rebuildable CURRENT VIEW, not a ledger. Only the display worker updates it.
CREATE TABLE IF NOT EXISTS trader_display_score_latest (
  venue text NOT NULL CHECK(venue IN ('POLYMARKET_US','KALSHI')),
  event_id text NOT NULL,
  observation_id text REFERENCES trader_display_score_observations(observation_id),
  payload jsonb,
  issue text,
  checked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(venue,event_id),
  CHECK(payload IS NULL OR octet_length(payload::text) <= 32768)
);
CREATE TABLE IF NOT EXISTS trader_display_score_health (
  worker text PRIMARY KEY,
  heartbeat_at timestamptz NOT NULL,
  payload jsonb NOT NULL,
  CHECK(octet_length(payload::text) <= 32768)
);
COMMENT ON TABLE trader_display_score_latest IS
  'Display only. A FINAL sports score is not venue settlement and cannot authorize an order.';
