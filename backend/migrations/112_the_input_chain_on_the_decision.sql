-- 112 — THE CAUSE OF A MISSING INPUT, ON THE DECISION ITSELF.
--
-- Every challenger decision on the production position records
-- `ev_basis = EV_HOLD_NOT_IDENTIFIED`, `input_available false` and an
-- empty `input_freshness`. That is a summary, not a cause: it cannot
-- distinguish
--
--   * no `markets` row for the held condition,
--   * the venue's catalogue carrying no contract for it,
--   * the held sport not being in the provider set at all,
--   * the fixture absent from the provider's payload,
--   * no Pinnacle h2h on that fixture,
--   * a quote older than the odds engine's own 30 s rule,
--   * or an unreadable venue book,
--
-- and those have completely different remedies. Reporting them as one
-- string is what let repeated blind holds look like a working lane.
--
-- `input_chain` carries the ordered links the management phase walked,
-- each with what it established or the refusal it hit, WITH the
-- identifiers (condition, global slug, venue slug, intent, provider event
-- id) and the timestamps (the bookmaker's observed_at, the age, the bound
-- it was aged against). `first_failing_link` names the one that stopped
-- it.
--
-- NOTHING IS REWRITTEN. Existing rows keep a NULL chain: they were taken
-- by a build that did not record one, and inventing it now from the same
-- generic summary would be a fabrication.

BEGIN;

ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS input_chain jsonb;

COMMENT ON COLUMN rn1x_decisions.input_chain IS
    'The ordered input links this decision walked: held exposure, venue '
    'contract and intent, provider fixture, probability, exit ladder. '
    'Each carries its identifiers, timestamps and, on failure, the '
    'refusal. `first_failing_link` names the link that stopped the chain. '
    'NULL on rows written before migration 112.';

COMMIT;
