-- ══════════════════════════════════════════════════════════════════════
-- 251 · ONE VALUATION PER OBSERVATION PER SIDE OF THE VENUE CONTRACT
-- ══════════════════════════════════════════════════════════════════════
--
-- THE DEFECT (P0 incident, inc-edge, 2026-10-04). The entry lane valued
-- only the provider's HOME team of every event, so the other side of each
-- binary venue contract -- the away team on an MLB money line, NO on a
-- soccer per-side contract -- was never valued and no paper strategy could
-- ever buy it. On the recorded production rows of the last 7 days, 17
-- completed-game V3 decisions (10 markets) whose home side was refused
-- BELOW_MIN_GROSS_EDGE had an OTHER side, on the same observed book, that
-- cleared the unchanged 0.5 pp threshold and was positive net of the venue
-- fee (research/incident_edge_inputs3.sql, run 37234196320).
--
-- The repair (bettor_complement_valuation, ext_pinnacle_loop) records the
-- other side as its own valuation row: the SAME contract identity, the
-- SAME selection (the de-vig's priced outcome) and the SAME source
-- observation instant, with the OTHER buy_intent and payout_is_complement.
-- Migration 106's uniqueness key -- (experiment, condition, slug,
-- selection, event, observed_at) -- has no side in it, so `persist`'s
-- untargeted ON CONFLICT DO NOTHING would silently swallow every
-- complement row as "already recorded".
--
-- THE KEY GAINS THE SIDE, NOTHING ELSE. Every existing row is unique under
-- the narrower key, so it is unique under the wider one: this deletes and
-- rewrites nothing. A re-read of an unchanged quote is still one row per
-- side (the reason migration 105 exists is kept). No threshold, no check
-- and no record purpose changes; a complement row is CALIBRATION_ONLY and
-- migration 144's constraints refuse it any executable price or admission.

DROP INDEX IF EXISTS external_valuations_one_per_observation;

CREATE UNIQUE INDEX IF NOT EXISTS external_valuations_one_per_observation
    ON external_valuations (
        experiment_id,
        coalesce(condition_id, ''),
        coalesce(us_market_slug, ''),
        contract_selection,
        coalesce(buy_intent, ''),
        coalesce(event_key, ''),
        coalesce(observed_at, '-infinity'::timestamptz));

COMMENT ON INDEX external_valuations_one_per_observation IS
    'One row per (experiment, contract identity, selection, venue side, '
    'event, source observation instant) -- migration 251 added the side '
    '(buy_intent), so the complement of a priced outcome on the other side '
    'of the same binary contract is its own row and not an "already '
    'recorded" duplicate of the priced side.';
