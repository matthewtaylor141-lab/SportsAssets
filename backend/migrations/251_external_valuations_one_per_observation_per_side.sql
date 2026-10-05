-- ══════════════════════════════════════════════════════════════════════
-- 251 · ONE VALUATION PER OBSERVATION PER SIDE OF THE VENUE CONTRACT
-- ══════════════════════════════════════════════════════════════════════
--
-- Two measured defects in migration 106's uniqueness key, (experiment,
-- condition, slug, selection, event, coalesce(observed_at, -infinity)), both
-- repaired by widening it. Nothing is deleted or rewritten.
--
-- 1. THE KEY HAD NO SIDE (P0 incident, inc-edge, 2026-10-04). The entry lane
--    valued only the provider's HOME team of every event, so the other side
--    of each binary venue contract -- the away team on an MLB money line, NO
--    on a soccer per-side contract -- was never valued and no paper strategy
--    could ever buy it (57 valued opportunities/day where ~140 exist). On
--    the recorded production rows of the last 7 days, 17 completed-game V3
--    decisions (10 markets) whose home side was refused BELOW_MIN_GROSS_EDGE
--    had an OTHER side, on the same observed book, that cleared the
--    unchanged 0.5 pp threshold and was positive net of the venue fee
--    (research/incident_edge_inputs3.sql, run 37234196320). The repair
--    (bettor_complement_valuation, ext_pinnacle_loop) records the other side
--    as its own row: the SAME contract identity, selection and source
--    observation instant, with the OTHER buy_intent and
--    payout_is_complement. Without the side in the key, `persist`'s
--    untargeted ON CONFLICT DO NOTHING would swallow every complement row as
--    "already recorded".
--
-- 2. A VALUATION WITH NO SOURCE INSTANT COLLAPSED FOREVER. observed_at is
--    NULL when the de-vig refuses before its aging step
--    (MARKET_NOT_IN_SUPPORTED_SET, PINNACLE_NOT_IN_THIS_PAYLOAD, a contract
--    disagreement, OUTCOME_SET_INCOMPLETE, ODDS_NOT_A_PRICE, a quote with no
--    readable instant). coalesce(NULL, -infinity) made every such row of a
--    contract the SAME observation, so after the first one every later
--    evaluation of it was discarded as a duplicate -- for ever. Production
--    2026-10-04: the NFL slate's valuations stopped at 13:28Z (146 NFL and
--    54 NCAAF evaluation instances lost that day; incident root cause
--    "observation clock collapse on refused de-vig"). The de-vig now records
--    the quote's own source instant on those refusals
--    (bettor_pinnacle_devig.valuation, observed_at_basis
--    QUOTE_SOURCE_INSTANT_NOT_AGED), so a re-read of an unchanged quote is
--    still one row and a moved quote is a new one -- exactly migration
--    105's rule. Only a row with NO instant of any kind falls back to our
--    receipt instant and then to the decision instant: with no provider
--    clock, our own observation is the only honest identity it has (one row
--    per evaluation, never one row for ever).
--
-- HISTORY IS KEPT AS IT IS. The `observed_at IS NULL` term keeps every
-- NULL-instant row apart from every timed row, and among NULL-instant rows
-- the old key allowed at most one per identity, so every existing row is
-- unique under the new key: the index builds without deleting, re-stamping
-- or merging anything. No threshold, check, freshness rule or record
-- purpose changes; a complement row is CALIBRATION_ONLY and migration 144's
-- constraints refuse it any executable price or admission.

DROP INDEX IF EXISTS external_valuations_one_per_observation;

CREATE UNIQUE INDEX IF NOT EXISTS external_valuations_one_per_observation
    ON external_valuations (
        experiment_id,
        coalesce(condition_id, ''),
        coalesce(us_market_slug, ''),
        contract_selection,
        coalesce(buy_intent, ''),
        coalesce(event_key, ''),
        (observed_at IS NULL),
        coalesce(observed_at, received_at, decided_at));

COMMENT ON INDEX external_valuations_one_per_observation IS
    'One row per (experiment, contract identity, selection, venue side '
    '(buy_intent), event, source observation instant). Migration 251 added '
    'the side, so the complement of a priced outcome on the other side of '
    'the same binary contract is its own row; and a row with no source '
    'instant is keyed on our receipt / decision instant instead of '
    'collapsing onto -infinity for ever (the NFL slate stopped valuing at '
    '13:28Z on 2026-10-04).';
