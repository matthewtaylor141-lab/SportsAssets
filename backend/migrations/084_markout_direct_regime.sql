-- THE MARKOUT TABLE WAS LEFT OUT OF THE DIRECT-REGIME WIDENING.
--
-- Found in production 2026-09-20 01:23Z. The first shadow position
-- opened at 00:58:01Z; its 30S markout computed correctly and OBSERVED,
-- and the insert was then rejected:
--
--   CheckViolationError: new row for relation
--   "bettor_experimental_markouts" violates check constraint
--   "bettor_exp_markout_regime"
--   Failing row contains (... 30S ... OBSERVED ...
--                         DIRECT_INSTITUTIONAL_WORKER ...)
--
-- Migration 081 widened `latency_regime` to admit
-- DIRECT_INSTITUTIONAL_WORKER on bettor_l2_evidence and on
-- bettor_experimental_decisions. It did not widen the THIRD table that
-- stores the same value. Nothing caught it because until 00:58 there
-- had never been a position to mark, so this row had never been
-- written in production at all.
--
-- ONE FAILURE, BOTH SYMPTOMS. The tick runs
-- sample -> drain -> take_markouts -> seal_population, so the raise
-- left the sample written (observations kept arriving) while the
-- markouts AND every subsequent decision stopped. The heartbeat
-- recorded it as tick_failed for twenty-five minutes with
-- markout_subjects=NOT_REACHED and seal_status=NOT_REACHED.
--
-- THE WIDENING IS THE SAME ONE 081 MADE, for the same reason: a book
-- the worker held in memory and a book a CI runner fetched minutes
-- late are different execution environments, so the three regimes stay
-- separable and the CHECK is widened rather than dropped.
--
-- NOTHING IS BACKDATED. No markout row is inserted here. The scorer
-- re-attempts every unmarked horizon on its next tick and will record
-- each one with its own OBSERVED or NOT_IDENTIFIED verdict, measured
-- against the books actually captured.

BEGIN;

ALTER TABLE bettor_experimental_markouts
    DROP CONSTRAINT IF EXISTS bettor_exp_markout_regime;
ALTER TABLE bettor_experimental_markouts
    ADD CONSTRAINT bettor_exp_markout_regime
        CHECK (latency_regime IS NULL
               OR latency_regime IN ('GITHUB_BRIDGE',
                                     'PERSISTENT_INSTITUTIONAL_WORKER',
                                     'DIRECT_INSTITUTIONAL_WORKER'));

COMMIT;
