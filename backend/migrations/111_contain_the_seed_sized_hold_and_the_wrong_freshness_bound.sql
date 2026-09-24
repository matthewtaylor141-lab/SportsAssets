-- 111 — HOLD THE CHALLENGER DECISIONS THE FIFTH DEFECT COULD HAVE CHANGED.
--
-- Migration 110 held everything the 5b19bc5 connection produced. The four
-- repairs released as 12260cb fixed the reported defects, but independent
-- inspection of 12260cb found a FIFTH, and it lives in the ordering:
--
--  5 HOLD WAS VALUED ON THE SEED, RANKED AGAINST THE RESIDUAL.
--    `manage_open_positions` computed the hold value from
--    `pos["seed_qty"]` / `pos["seed_price"]` BEFORE `manage_open_position`
--    reloaded the portfolio and applied newly admitted fills.
--    `decide_challenger` then ranked the actual residual against that
--    seed-sized number. After a partial exit, HOLD carried the value of
--    contracts that had already been sold.
--
--    THE REPORTED CASE, before fees:
--        seed 100 @ .57 · residual 80 · p .70 · exit .72 on all 80
--        shipped HOLD 13.00 (100 contracts)  -> HOLD selected
--        correct HOLD 10.40 ( 80 contracts)  -> DIRECT_EXIT at 12.00 wins
--    The selection itself was wrong, not merely the reported number.
--
--    SCOPE IS EXACT, AND NARROWER THAN THE ARM. A decision taken while
--    the residual still equalled the seed was valued on the right
--    quantity by coincidence of the arithmetic, and its selection stands.
--    Only a decision taken after inventory had moved could differ, so the
--    predicate below is `residual <> seed`, read off the row's own
--    `resulting_inventory`. A row that records no residual cannot be
--    shown to be sound, so it is held too.
--
--  6 THE FRESHNESS BOUND WAS TAKEN FROM THE WRONG FEED.
--    `bettor_hold_value` replaced the unrestricted 1,800 s with 120 s and
--    cited `bettor_progress_feed.MAX_AGE_S`. That threshold bounds a
--    PERIOD OR CLOCK OBSERVATION, which is a different input. The
--    applicable rule for an odds quote is the odds engine's own
--    `bettor_pinnacle_devig.MAX_QUOTE_AGE_S` = 30 s. The progress feed's
--    threshold establishes nothing about odds freshness.
--
--    So a decision admitted under the 120 s bound on a probability older
--    than 30 s was priced by a quote the applicable rule rejects. Those
--    rows are held. A decision whose quote was inside 30 s satisfies the
--    applicable rule whatever bound was printed beside it, and is left
--    eligible -- the bound was wrong on paper, but the input was sound.
--
-- HELD, NOT DELETED, for the same reason as 110: the inputs, refusals and
-- reasons on the row are the evidence about the connection.
--
-- THE FROZEN BENCHMARK IS UNTOUCHED AGAIN. MANAGEMENT_PAIR_091_STOP_16_V1
-- never calls `bettor_hold_value`, never reads the exit ladder and never
-- ran through `manage_open_positions`; it has no hold value to mis-size.
-- Its rows are deliberately not considered here.

BEGIN;

-- 5 · the seed-sized hold value
UPDATE rn1x_decisions d
   SET eligibility = 'INELIGIBLE_HOLD_VALUED_ON_THE_SEED_NOT_THE_RESIDUAL',
       ineligible_reason =
           'taken under deployed 12260cb/8272872, where the worker '
           'computed the hold value from seed_qty/seed_price BEFORE the '
           'reload and the newly admitted fills, and the ranking then '
           'compared the actual residual against that seed-sized number. '
           'On the reported case -- seed 100 @ .57, residual 80, p .70, '
           'exit .72 -- HOLD was reported as 13.00 and selected, where '
           'the residual HOLD is 10.40 and DIRECT_EXIT at 12.00 wins. '
           'This row''s residual differs from its position''s seed (or is '
           'not recorded), so its HOLD value and its selection cannot be '
           'shown to be sound. Held, not deleted.'
  FROM rn1x_positions p
 WHERE p.position_id = d.position_id
   AND d.eligibility = 'ELIGIBLE'
   AND p.policy = 'SHADOW_CHALLENGER_HOLD_RANKED_V1'
   AND d.hold_value_usd IS NOT NULL
   AND (d.resulting_inventory->>'residual_qty' IS NULL
        OR abs((d.resulting_inventory->>'residual_qty')::float8
               - p.seed_qty::float8) > 1e-9);

-- 6 · admitted by the progress feed's bound, stale under the odds rule
UPDATE rn1x_decisions d
   SET eligibility = 'INELIGIBLE_PROBABILITY_STALE_UNDER_THE_ODDS_RULE',
       ineligible_reason =
           'admitted under a 120 s freshness bound taken from '
           'bettor_progress_feed.MAX_AGE_S, which bounds a period or '
           'clock observation, not an odds quote. The applicable rule is '
           'the odds engine''s own bettor_pinnacle_devig.MAX_QUOTE_AGE_S '
           '= 30 s, and this row''s probability was older than that at '
           'its decision instant, so the quote that priced the hold is '
           'one the applicable rule rejects. Held, not deleted.'
  FROM rn1x_positions p
 WHERE p.position_id = d.position_id
   AND d.eligibility = 'ELIGIBLE'
   AND p.policy = 'SHADOW_CHALLENGER_HOLD_RANKED_V1'
   AND (d.input_freshness->>'age_from_observation_s')::float8 > 30.0;

COMMIT;
