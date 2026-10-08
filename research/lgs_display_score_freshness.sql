-- READ-ONLY. BETTOR LIVE GAME STATE V1: SOURCE AND FRESHNESS OF THE DISPLAY
-- SCORES THE TRADER RESPONSE WOULD ATTACH, for the current held
-- POLYMARKET_US PAPER positions of paper_acct_main. REQUIRES MIGRATION 316
-- (trader_display_score_*); before 316 is applied every statement fails with
-- "relation does not exist" -- run lgs_held_fixture_census.sql instead.
--
-- DISPLAY FRESHNESS IS NOT TRADING FRESHNESS. These limits are the
-- package's display limits (core.display): 15 s for a live ESPN observation,
-- 45 s for a live Odds API observation, 300 s scheduled / postponed, 3600 s
-- final / canceled; a provider update time, when supplied, bounds expiry as
-- well. The book / probability gates and their thresholds are separate and
-- are neither read nor implied here.
--
-- RETRIEVAL IS NOT A PROVIDER UPDATE. ESPN scoreboards supply no event
-- update time: those observations have source_at NULL and are counted under
-- freshness_basis RETRIEVAL_ONLY. They are never counted as
-- provider-current.
--
-- The status rule is core.display rendered in SQL, evaluated at now(), in
-- its order: no row -> UNAVAILABLE (the collector's issue, else
-- NO_SCORE_PROVIDER_EVIDENCE_RECORDED); identity fields -> UNAVAILABLE
-- SCORE_EVENT_IDENTITY_UNPROVEN; provider -> SCORE_PROVENANCE_ABSENT;
-- clocks -> SCORE_TIMESTAMP_INVALID; then STALE for
-- ODDS_UPDATE_TIMESTAMP_MISSING, SCORE_EVIDENCE_STALE,
-- SCORE_GAME_STATE_UNDECLARED, SCORE_VALUE_MISSING; else CURRENT. The binding
-- digest itself is re-verified only by the API (sha256 over canonical JSON
-- is not reproduced here); a tampered digest would show CURRENT here and
-- UNAVAILABLE in the API, never the reverse.
--
-- The event a position is attached to is the readmodel's own: the newest
-- us_premap row of the held slug, its event_slug, venue POLYMARKET_US.
-- Every statement is a SELECT.

\echo == 1. collector heartbeat: leader liveness and its own last-cycle readback ==
SELECT worker, heartbeat_at,
       round(extract(epoch FROM now() - heartbeat_at)) AS heartbeat_age_s,
       payload ->> 'status' AS status,
       payload ->> 'held_fixture_count' AS held_fixture_count,
       payload ->> 'attempted' AS attempted,
       payload ->> 'successful' AS successful,
       payload ->> 'deferred_due' AS deferred_due,
       payload ->> 'state_overflow' AS state_overflow,
       payload -> 'source_counts' AS source_counts,
       payload -> 'errors' AS errors,
       payload -> 'fixture_census' AS fixture_census,
       payload -> 'provider' AS provider_counters,
       payload ->> 'rates_scope' AS rates_scope
  FROM trader_display_score_health;

\echo == 2. per held event: the display score the Trader response would attach now ==
WITH held AS (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
), acct AS (
    SELECT * FROM held WHERE account_id = 'paper_acct_main'
), events AS (
    SELECT pm.event_slug AS event_id, count(*) AS positions,
           string_agg(DISTINCT a.us_market_slug, ',') AS markets
      FROM acct a
      LEFT JOIN LATERAL (
        SELECT u.event_slug FROM us_premap u
         WHERE u.identifier = a.us_market_slug AND u.market_slug = a.us_market_slug
         ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
     GROUP BY pm.event_slug
), clocks AS (
    SELECT e.*, l.payload, l.issue, l.checked_at, l.observation_id,
           extract(epoch FROM now()) AS now_s,
           (l.payload ->> 'received_at')::float8 AS recv,
           (l.payload ->> 'observed_at')::float8 AS observed,
           (l.payload ->> 'source_at')::float8 AS source_at,
           l.payload ->> 'source' AS provider,
           l.payload ->> 'game_status' AS game_status,
           l.payload ->> 'league' AS league
      FROM events e
      LEFT JOIN trader_display_score_latest l
        ON l.venue = 'POLYMARKET_US' AND l.event_id = e.event_id
), limited AS (
    SELECT c.*,
           CASE c.game_status WHEN 'SCHEDULED' THEN 300 WHEN 'POSTPONED' THEN 300
                WHEN 'FINAL' THEN 3600 WHEN 'CANCELED' THEN 3600
                ELSE CASE WHEN c.provider = 'ESPN' THEN 15 ELSE 45 END END AS limit_s
      FROM clocks c
), judged AS (
    SELECT l.*,
           least(l.observed + l.limit_s, coalesce(l.source_at + l.limit_s, l.observed + l.limit_s)) AS expires_at,
           CASE
             WHEN l.event_id IS NULL THEN 'UNAVAILABLE:CANONICAL_VENUE_EVENT_MISSING'
             WHEN l.payload IS NULL THEN 'UNAVAILABLE:' || coalesce(l.issue, 'NO_SCORE_PROVIDER_EVIDENCE_RECORDED')
             WHEN l.payload ->> 'schema' IS DISTINCT FROM 'bettor.live_game.v1'
                  OR l.payload ->> 'venue' IS DISTINCT FROM 'POLYMARKET_US'
                  OR l.payload ->> 'event_id' IS DISTINCT FROM l.event_id
                  OR l.payload -> 'identity_verified' IS DISTINCT FROM 'true'::jsonb
                  OR l.payload -> 'identity' ->> 'venue' IS DISTINCT FROM 'POLYMARKET_US'
                  OR l.payload -> 'identity' ->> 'event_id' IS DISTINCT FROM l.event_id
                  OR coalesce(l.payload -> 'identity' ->> 'binding_id', '') = ''
                  OR l.payload ->> 'authority' IS DISTINCT FROM 'DISPLAY_ONLY_NOT_A_PRICE_OR_SETTLEMENT_SOURCE'
                  OR l.payload -> 'execution_authority' IS DISTINCT FROM 'false'::jsonb
               THEN 'UNAVAILABLE:SCORE_EVENT_IDENTITY_UNPROVEN'
             WHEN l.provider IS NULL OR l.provider NOT IN ('ESPN', 'THE_ODDS_API')
               THEN 'UNAVAILABLE:SCORE_PROVENANCE_ABSENT'
             WHEN l.recv IS NULL OR l.observed IS NULL OR l.observed > l.recv OR l.recv > l.now_s
                  OR (l.source_at IS NOT NULL AND l.source_at > l.now_s)
               THEN 'UNAVAILABLE:SCORE_TIMESTAMP_INVALID'
             WHEN l.provider = 'THE_ODDS_API' AND l.source_at IS NULL THEN 'STALE:ODDS_UPDATE_TIMESTAMP_MISSING'
             WHEN l.now_s > least(l.observed + l.limit_s, coalesce(l.source_at + l.limit_s, l.observed + l.limit_s))
               THEN 'STALE:SCORE_EVIDENCE_STALE'
             WHEN l.game_status = 'UNKNOWN' THEN 'STALE:SCORE_GAME_STATE_UNDECLARED'
             WHEN l.game_status NOT IN ('SCHEDULED', 'POSTPONED', 'CANCELED')
                  AND (coalesce(l.payload ->> 'home_score', '') !~ '^[0-9]+$'
                       OR coalesce(l.payload ->> 'away_score', '') !~ '^[0-9]+$')
               THEN 'STALE:SCORE_VALUE_MISSING'
             ELSE 'CURRENT'
           END AS display_state
      FROM limited l
)
SELECT event_id, positions, markets, league, provider, game_status,
       split_part(display_state, ':', 1) AS status,
       nullif(split_part(display_state, ':', 2), '') AS why,
       CASE WHEN payload IS NULL THEN NULL
            WHEN source_at IS NULL THEN 'RETRIEVAL_ONLY'
            ELSE 'PROVIDER_TIMESTAMP_AND_RETRIEVAL' END AS freshness_basis,
       CASE WHEN payload IS NULL OR source_at IS NULL THEN NULL
            ELSE display_state = 'CURRENT' END AS provider_current,
       round((now_s - recv)::numeric, 1) AS received_age_s,
       round((now_s - observed)::numeric, 1) AS observed_age_s,
       round((now_s - source_at)::numeric, 1) AS source_age_s,
       limit_s AS display_limit_s,
       issue AS collector_issue,
       round(extract(epoch FROM now() - checked_at)) AS checked_age_s,
       payload -> 'identity' ->> 'provider_event_id' AS provider_event_id,
       payload -> 'identity' ->> 'basis' AS binding_basis
  FROM judged
 ORDER BY status, league NULLS LAST, event_id;

\echo == 3. by league x provider x status: the held-event denominator, nothing excluded ==
WITH held AS (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
), acct AS (
    SELECT * FROM held WHERE account_id = 'paper_acct_main'
), events AS (
    SELECT pm.event_slug AS event_id, count(*) AS positions
      FROM acct a
      LEFT JOIN LATERAL (
        SELECT u.event_slug FROM us_premap u
         WHERE u.identifier = a.us_market_slug AND u.market_slug = a.us_market_slug
         ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
     GROUP BY pm.event_slug
), clocks AS (
    SELECT e.*, l.payload, l.issue,
           extract(epoch FROM now()) AS now_s,
           (l.payload ->> 'received_at')::float8 AS recv,
           (l.payload ->> 'observed_at')::float8 AS observed,
           (l.payload ->> 'source_at')::float8 AS source_at,
           l.payload ->> 'source' AS provider,
           l.payload ->> 'game_status' AS game_status,
           l.payload ->> 'league' AS league
      FROM events e
      LEFT JOIN trader_display_score_latest l
        ON l.venue = 'POLYMARKET_US' AND l.event_id = e.event_id
), limited AS (
    SELECT c.*,
           CASE c.game_status WHEN 'SCHEDULED' THEN 300 WHEN 'POSTPONED' THEN 300
                WHEN 'FINAL' THEN 3600 WHEN 'CANCELED' THEN 3600
                ELSE CASE WHEN c.provider = 'ESPN' THEN 15 ELSE 45 END END AS limit_s
      FROM clocks c
), judged AS (
    SELECT l.*,
           CASE
             WHEN l.event_id IS NULL OR l.payload IS NULL THEN 'UNAVAILABLE'
             WHEN l.payload ->> 'schema' IS DISTINCT FROM 'bettor.live_game.v1'
                  OR l.payload ->> 'event_id' IS DISTINCT FROM l.event_id
                  OR l.payload -> 'identity_verified' IS DISTINCT FROM 'true'::jsonb
                  OR l.provider IS NULL OR l.provider NOT IN ('ESPN', 'THE_ODDS_API')
                  OR l.recv IS NULL OR l.observed IS NULL OR l.observed > l.recv OR l.recv > l.now_s
                  OR (l.source_at IS NOT NULL AND l.source_at > l.now_s) THEN 'UNAVAILABLE'
             WHEN l.provider = 'THE_ODDS_API' AND l.source_at IS NULL THEN 'STALE'
             WHEN l.now_s > least(l.observed + l.limit_s, coalesce(l.source_at + l.limit_s, l.observed + l.limit_s)) THEN 'STALE'
             WHEN l.game_status = 'UNKNOWN' THEN 'STALE'
             WHEN l.game_status NOT IN ('SCHEDULED', 'POSTPONED', 'CANCELED')
                  AND (coalesce(l.payload ->> 'home_score', '') !~ '^[0-9]+$'
                       OR coalesce(l.payload ->> 'away_score', '') !~ '^[0-9]+$') THEN 'STALE'
             ELSE 'CURRENT'
           END AS status
      FROM limited l
)
SELECT coalesce(league, '(no display score)') AS league,
       coalesce(provider, '-') AS provider,
       status,
       count(*) AS events,
       sum(positions) AS positions,
       count(*) FILTER (WHERE payload IS NOT NULL AND source_at IS NULL) AS retrieval_only,
       count(*) FILTER (WHERE source_at IS NOT NULL) AS provider_time_known,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY now_s - observed) AS p50_observed_age_s,
       max(now_s - observed) AS max_observed_age_s
  FROM judged
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo == 4. the append-only ledgers: bindings and observations by provider (24 h and all time) ==
SELECT 'bindings' AS ledger, provider, venue, count(*) AS rows_all_time,
       count(*) FILTER (WHERE recorded_at > now() - interval '24 hours') AS rows_24h,
       max(recorded_at) AS newest
  FROM trader_display_score_bindings GROUP BY 2, 3
UNION ALL
SELECT 'observations', provider, venue, count(*),
       count(*) FILTER (WHERE received_at > now() - interval '24 hours'),
       max(received_at)
  FROM trader_display_score_observations GROUP BY 2, 3
 ORDER BY 1, 2, 3;
