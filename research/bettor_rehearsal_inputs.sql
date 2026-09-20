-- EXPORT REAL CAPTURED STATES AS REHEARSAL INPUTS.
--
-- One JSON object per line, each a row the capture actually wrote,
-- carrying its own observation id, observed_at, universe_version and
-- rule_sha so every rehearsal evaluation can name the input it ran on.
--
-- READABLE ROWS ONLY, and that is a deliberate, declared restriction
-- rather than a silent filter: the rehearsal exercises the decision
-- chain, and a market whose book was refused carries no book for the
-- chain to read. It means the rehearsal's inputs inherit the
-- readability caveat in full -- they are NOT a sample of the universe,
-- they are a sample of what was readable. The rehearsal is a wiring
-- demonstration, not an estimate of anything.
--
-- A handful of unreadable rows are exported too, deliberately, so the
-- rehearsal is forced to handle the unreadable case rather than only
-- the happy path.
--
-- Read only. Descriptive. Carries no settlement term.

SELECT row_to_json(t)::text
  FROM (
    (SELECT observation_id, observed_at::text AS observed_at,
            universe_version, rule_sha, selection_cycle,
            event_id, market_id, instrument_id, outcome_leg,
            sport, league, market_type,
            time_to_event_s, live_status,
            yes_bid, yes_ask, no_bid, no_ask, spread, mid,
            yes_depth, multi_level_depth, book_imbalance,
            recent_price_move, realised_volatility,
            stats_shares_traded, venue_state,
            book_readability_status, identity_status,
            book_source_ts, book_age_s,
            missing_field_reasons, fill_status,
            'READABLE_COHORT' AS export_cohort
       FROM bettor_state_observations
      WHERE book_readability_status = 'READABLE'
      ORDER BY observed_at DESC
      LIMIT 12)
    UNION ALL
    (SELECT observation_id, observed_at::text,
            universe_version, rule_sha, selection_cycle,
            event_id, market_id, instrument_id, outcome_leg,
            sport, league, market_type,
            time_to_event_s, live_status,
            yes_bid, yes_ask, no_bid, no_ask, spread, mid,
            yes_depth, multi_level_depth, book_imbalance,
            recent_price_move, realised_volatility,
            stats_shares_traded, venue_state,
            book_readability_status, identity_status,
            book_source_ts, book_age_s,
            missing_field_reasons, fill_status,
            'UNREADABLE_COHORT'
       FROM bettor_state_observations
      WHERE book_readability_status <> 'READABLE'
      ORDER BY observed_at DESC
      LIMIT 4)
  ) t
