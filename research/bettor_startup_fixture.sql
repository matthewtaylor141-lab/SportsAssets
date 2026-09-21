-- REAL ROWS FOR THE STARTUP-PATH DEMONSTRATION.
--
-- The previous harness fed 16 captured books that the frozen universe
-- rule rejects, so it demonstrated the decision chain with inputs
-- handed to it -- not discovery, not selection, not subscription.
--
-- This exports the LATEST observation per market, in full, for markets
-- whose book is numerically readable and whose traded-volume counter
-- is present: everything `bettor_universe.assess` reads, plus the
-- five-level ladder `bettor_observation_adapter` needs, so the whole
-- path from a paginated listing to a decision runs on venue data and
-- nothing is synthesised.
--
-- Bounded to 250 markets so the output is one line the log can carry.
--
-- READ-ONLY. Every statement is a SELECT.

\echo
\echo ===== how many markets are numerically readable at all
SELECT count(*) AS markets
  FROM (SELECT DISTINCT ON (market_id) market_id, yes_bid, yes_ask,
                                        stats_shares_traded
          FROM bettor_state_observations
         ORDER BY market_id, observed_at DESC) t
 WHERE yes_bid ~ '^[0-9]+(\.[0-9]+)?$'
   AND yes_ask ~ '^[0-9]+(\.[0-9]+)?$'
   AND stats_shares_traded ~ '^[0-9]+(\.[0-9]+)?$';

\echo
\echo ===== the rows themselves, one JSON line
WITH latest AS (
    SELECT DISTINCT ON (market_id)
           market_id, event_id, instrument_id, outcome_leg, sport, league,
           market_type, identity_status, venue_state, live_status,
           time_to_event_s, book_source_ts, book_received_ts, book_age_s,
           yes_bid, yes_ask, yes_depth, no_bid, no_ask, spread, mid,
           multi_level_depth, stats_shares_traded, book_readability_status,
           observed_at, observation_id
      FROM bettor_state_observations
     ORDER BY market_id, observed_at DESC),
ok AS (
    SELECT * FROM latest
     WHERE yes_bid ~ '^[0-9]+(\.[0-9]+)?$'
       AND yes_ask ~ '^[0-9]+(\.[0-9]+)?$'
       AND stats_shares_traded ~ '^[0-9]+(\.[0-9]+)?$'
       AND multi_level_depth IS NOT NULL
     ORDER BY stats_shares_traded::numeric DESC
     LIMIT 250)
SELECT jsonb_agg(to_jsonb(ok))::text AS rows_json FROM ok;
