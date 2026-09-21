-- EXPORT REAL CAPTURED OBSERVATIONS FOR THE REPLAY. Read only.
--
-- One row is one OUTCOME LEG of one contract. The replay normalizes these
-- through bettor_observation_adapter under INSTITUTIONAL semantics -- not
-- the demo venue's independently-held-pair fixture -- and reports what it
-- accepted, what it rejected, and why.
\echo == REPLAY EXPORT: most recent observations, all columns the adapter reads ==
SELECT observation_id, market_id, event_id, outcome_leg, observed_at,
       book_source_ts, book_age_s, venue_state, book_readability_status,
       yes_bid, yes_ask, no_bid, no_ask
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '24 hours'
 ORDER BY observed_at DESC
 LIMIT 200;
