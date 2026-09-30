-- ══════════════════════════════════════════════════════════════════════
-- 151 · OBSERVING A PAIR IS NOT APPROVING ITS ACQUISITION
-- ══════════════════════════════════════════════════════════════════════
--
-- THE GAP. The non-funded observer recorded only structures funded
-- discovery ADMITS, so a correctly identified pair whose one undetermined
-- region is the fixture-cancelled cell -- the venue's cancellation wording
-- unread or not a fixed payout -- left no observation at all. 120 such
-- siblings over 18 production fixtures were examined and discarded on
-- 2026-09-30, and the prospective sample stayed at zero.
--
-- WHAT THIS ADDS. An observation now states whether discovery admitted it
-- (ADMITTED_BY_DISCOVERY, every existing row) or it was recorded WITHOUT
-- admission because the cancellation treatment is unresolved
-- (OBSERVED_NOT_ADMITTED_CANCELLATION_UNRESOLVED), which facts are
-- unresolved, and each leg's cancellation clause as read. Such a row claims
-- no floor and authorizes nothing: funded discovery still refuses the pair,
-- and model training reads admitted rows only.
--
-- ADDITIVE. Existing rows keep their meaning through the default.

BEGIN;

ALTER TABLE bettor_pair_observations
    ADD COLUMN IF NOT EXISTS admission_status text NOT NULL
        DEFAULT 'ADMITTED_BY_DISCOVERY',
    ADD COLUMN IF NOT EXISTS unresolved jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS cancellation_terms jsonb;

ALTER TABLE bettor_pair_observations
    DROP CONSTRAINT IF EXISTS bettor_pair_obs_admission_ck;
ALTER TABLE bettor_pair_observations
    ADD CONSTRAINT bettor_pair_obs_admission_ck CHECK (
        admission_status IN ('ADMITTED_BY_DISCOVERY',
                             'OBSERVED_NOT_ADMITTED_CANCELLATION_UNRESOLVED')
        AND (admission_status = 'ADMITTED_BY_DISCOVERY'
             OR jsonb_array_length(unresolved) > 0));

COMMIT;
