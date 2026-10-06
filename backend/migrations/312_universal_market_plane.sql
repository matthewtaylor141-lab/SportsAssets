-- 312 · UNIVERSAL MARKET PLANE (MARKET DATA / RESEARCH ONLY)
-- Durable active-contract registry, same-book certification and append-only
-- operational evidence. NO order, cancel, sizing, funding or capital authority.

CREATE TABLE IF NOT EXISTS market_plane_registry (
    contract_id text PRIMARY KEY,
    venue text NOT NULL,
    sport text,
    competition text,
    event_id text,
    market_type text,
    ontology jsonb NOT NULL DEFAULT '{}'::jsonb,
    active boolean NOT NULL DEFAULT true,
    desired_subscription boolean NOT NULL DEFAULT true,
    subscription_shard integer,
    refdata jsonb,
    refdata_at timestamptz,
    updated_at timestamptz NOT NULL,
    -- integration (closeout): why the contract is required, its subscription
    -- priority (lower = first), the catalogue's own family / period / start,
    -- when the catalogue last listed it, and its terminal coverage state
    priority integer NOT NULL DEFAULT 100,
    required_reason text,
    family text,
    period text,
    event_start timestamptz,
    last_seen_at timestamptz,
    coverage_state text CHECK (coverage_state IS NULL OR coverage_state IN (
        'PRICEABLE','MAPPED_BUT_NO_FAIR_VALUE_SOURCE',
        'MAPPED_BUT_SETTLEMENT_NOT_PROVEN','EXTERNAL_DATA_UNAVAILABLE',
        'CODE_CONTROLLED_GAP')),
    coverage_why text,
    coverage_at timestamptz,
    content_sha text,
    -- (settlement rule registry) the contract's settlement state, from
    -- evidence only (market_plane.settlement): the latest decision attest
    -- verdict, else the captured CURRENT rules text compared condition ->
    -- payout against the captured bookmaker terms. Decision-time attest
    -- remains the authority for trading; this is coverage evidence.
    settlement_state text CHECK (settlement_state IS NULL OR settlement_state IN (
        'SETTLEMENT_PROVEN_COMPATIBLE','SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED',
        'MAPPED_BUT_SETTLEMENT_NOT_PROVEN','SETTLEMENT_RULE_EVIDENCE_CONFLICT',
        'EXTERNAL_SETTLEMENT_DATA_UNAVAILABLE')),
    settlement_why text,
    settlement_basis text,
    settlement_evidence jsonb,
    settlement_at timestamptz,
    label text NOT NULL DEFAULT 'RESEARCH' CHECK(label='RESEARCH'),
    authority text NOT NULL DEFAULT 'MARKET_DATA_ONLY_NO_ORDER_AUTHORITY'
      CHECK(authority='MARKET_DATA_ONLY_NO_ORDER_AUTHORITY')
);
CREATE INDEX IF NOT EXISTS market_plane_registry_active_idx
  ON market_plane_registry(active,desired_subscription,contract_id);
CREATE INDEX IF NOT EXISTS market_plane_registry_coverage_idx
  ON market_plane_registry(active,coverage_state);
CREATE INDEX IF NOT EXISTS market_plane_registry_settlement_idx
  ON market_plane_registry(active,settlement_state);
CREATE INDEX IF NOT EXISTS market_plane_registry_shard_idx
  ON market_plane_registry(subscription_shard,contract_id) WHERE active AND desired_subscription;

CREATE TABLE IF NOT EXISTS market_plane_certification (
    contract_id text NOT NULL,
    fingerprint text NOT NULL,
    status text NOT NULL CHECK(status IN('ACCUMULATING','SUPPORTED')),
    comparable integer NOT NULL CHECK(comparable>=0),
    agreeing integer NOT NULL CHECK(agreeing>=0 AND agreeing<=comparable),
    agreement double precision,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    certified_at timestamptz NOT NULL,
    label text NOT NULL DEFAULT 'RESEARCH' CHECK(label='RESEARCH'),
    authority text NOT NULL DEFAULT 'MARKET_DATA_ONLY_NO_ORDER_AUTHORITY'
      CHECK(authority='MARKET_DATA_ONLY_NO_ORDER_AUTHORITY'),
    PRIMARY KEY(contract_id,fingerprint)
);

-- (settlement rule registry) THE CURRENT RULES BLOCK OF EVERY CONTRACT, as
-- the venue published it: Polymarket US captured by the premap sweep from
-- the event board it already reads (no extra venue request), Kalshi from the
-- GET-only sports catalogue (rules_primary / rules_secondary). One row per
-- contract, rewritten ONLY when the fingerprint changes (a changed rules
-- block invalidates the prior interpretation; the change is appended to
-- market_plane_events as RULES_CHANGED). rules_published = false with a
-- NULL text is the venue's own listing carrying no rules field (evidence,
-- read ok) -- never "not read".
CREATE TABLE IF NOT EXISTS market_plane_rules (
    contract_id text PRIMARY KEY,
    venue text NOT NULL CHECK (venue IN ('POLYMARKET_US','KALSHI')),
    rules_published boolean NOT NULL,
    rules_field text,
    rules_sha256 text,
    rules_text text,
    rules_secondary text,
    parse_status text NOT NULL CHECK (parse_status IN (
        'ESTABLISHED','PARTIAL','ABSENT','CONFLICT')),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    parser_version text NOT NULL,
    source text NOT NULL,
    observed_at timestamptz NOT NULL,
    CHECK (rules_published = (rules_sha256 IS NOT NULL)),
    label text NOT NULL DEFAULT 'RESEARCH' CHECK(label='RESEARCH'),
    authority text NOT NULL DEFAULT 'MARKET_DATA_ONLY_NO_ORDER_AUTHORITY'
      CHECK(authority='MARKET_DATA_ONLY_NO_ORDER_AUTHORITY')
);
CREATE INDEX IF NOT EXISTS market_plane_rules_venue_idx
  ON market_plane_rules(venue,parse_status);

CREATE TABLE IF NOT EXISTS market_plane_events (
    event_key text PRIMARY KEY,
    contract_id text,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    at timestamptz NOT NULL,
    label text NOT NULL DEFAULT 'RESEARCH' CHECK(label='RESEARCH'),
    authority text NOT NULL DEFAULT 'MARKET_DATA_ONLY_NO_ORDER_AUTHORITY'
      CHECK(authority='MARKET_DATA_ONLY_NO_ORDER_AUTHORITY')
);

CREATE OR REPLACE FUNCTION market_plane_events_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'market_plane_events is append-only (%)',TG_OP; END $$;
DROP TRIGGER IF EXISTS market_plane_events_append_only_trg ON market_plane_events;
CREATE TRIGGER market_plane_events_append_only_trg
BEFORE UPDATE OR DELETE ON market_plane_events FOR EACH ROW
EXECUTE FUNCTION market_plane_events_append_only();
DROP TRIGGER IF EXISTS market_plane_events_no_truncate_trg ON market_plane_events;
CREATE TRIGGER market_plane_events_no_truncate_trg
BEFORE TRUNCATE ON market_plane_events FOR EACH STATEMENT
EXECUTE FUNCTION market_plane_events_append_only();
