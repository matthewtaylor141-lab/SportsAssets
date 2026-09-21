-- REPLAY EXPORT v3 -- the bounded row export, WITH the five-level ladder.
--
-- The findings that accompany this export live in
-- research/bettor_depth_findings.sql; they were split out because this
-- section's output is thousands of lines and pushed them past the end of the
-- retrievable log.
--
-- WHY v3 EXISTS. v2 added `yes_depth` to the export and the normalizer was
-- changed in the same commit to REQUIRE depth. The committed sample was never
-- regenerated, so every one of its 36 rows is now rejected NO_DEPTH_REPORTED.
-- And the column v2 exported was the wrong one: `yes_depth.ask` is the SUM
-- across five levels, not the quantity at the quote. The export now carries
-- the LADDER, which is the only thing an order can be sized from.
--
-- READ ONLY.

\echo == 8. THE EXPORT, as JSON, freshest first, WITH the ladder ==
-- COMPACT, NOT PRETTY, AND ON ONE LINE. psql's aligned output pads
-- every line to the widest column, so a pretty-printed 66 KB array
-- rendered as 600 KB of log and was truncated before it could be
-- retrieved. One line costs no padding.
SELECT jsonb_agg(r)::text AS rows_json
  FROM (SELECT o.observation_id, o.market_id, o.event_id, o.instrument_id,
               o.outcome_leg, o.market_type, o.sport, o.league,
               o.observed_at, o.book_source_ts, o.book_received_ts,
               o.book_age_s, o.venue_state, o.book_readability_status,
               o.yes_bid, o.yes_ask, o.no_bid, o.no_ask,
               o.yes_depth, o.multi_level_depth,
               o.time_to_event_s, o.live_status, o.identity_status
          FROM bettor_state_observations o
         WHERE o.observed_at > now() - interval '2 days'
           AND o.book_age_s ~ '^[0-9.]+$'
           AND o.venue_state = 'MARKET_STATE_OPEN'
           AND o.book_readability_status = 'READABLE'
           AND o.yes_bid ~ '^[0-9.]+$' AND o.yes_ask ~ '^[0-9.]+$'
           AND o.multi_level_depth ? 'ask'
         ORDER BY o.book_age_s::numeric ASC
         LIMIT 16) r;
