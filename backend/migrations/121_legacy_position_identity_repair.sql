-- ── REPAIRING A POSITION OPENED BEFORE ITS IDENTITY WAS RECORDED ─────
--
-- Migration 119 put the venue identity ON the position and the entry
-- writer fills it. Positions opened BEFORE that carry NULL, and the
-- settlement consumer then has to resolve the held outcome from
-- `market_tokens` and find a valuation that NAMES it. For the acceptance
-- position that search failed by name, in production:
--
--     THE_ONLY_AVAILABLE_IDENTITY_DESCRIBES_A_DIFFERENT_OUTCOME
--     "2 venue identities are recorded for this condition and none of
--      them names the outcome this position holds ('Arizona
--      Diamondbacks'). They describe ['Colorado Rockies', 'None']."
--
-- That refusal is correct and it is also a dead end: the position holds
-- Arizona, the only recorded valuation names Colorado, and borrowing
-- Colorado's slug would settle the position against the opposite side.
--
-- THE REPAIR RE-ESTABLISHES THE BINDING FROM THE RESOLVER, NOT FROM A
-- VALUATION. `premap.resolve` is asked for the outcome the position
-- actually holds -- read from `market_tokens` at the position's own
-- `outcome_index` -- and returns the venue contract and the intent that
-- buys it. Nothing is copied from another side's row.
--
-- EVERY REPAIR IS AUDITABLE. `identity_repair` records what was asked,
-- what the resolver answered, which cross-checks were run and passed,
-- when, and under which version. A repair that cannot be re-derived from
-- its own record is not a repair, it is an edit.
--
-- WHAT A REPAIR MUST NEVER TOUCH: `provenance`, `entry_kind`,
-- `source_trade_id`, `source_account`, `seed_qty`, `seed_price`,
-- `seed_basis_usd`. The acceptance position stays synthetic, modelled and
-- unfunded, and nothing is reseeded. The writer only ever fills identity
-- columns that are NULL, which is enforced in the UPDATE's own WHERE
-- clause rather than promised in a comment.

ALTER TABLE rn1x_positions
    ADD COLUMN IF NOT EXISTS identity_repair jsonb;

COMMENT ON COLUMN rn1x_positions.identity_repair IS
    'The auditable record of a legacy-position identity repair: the '
    'outcome asked for and where it was read from, the resolver answer, '
    'the cross-checks and their verdicts, the version and the instant. '
    'NULL means the identity was recorded by the writer that opened the '
    'position and needed no repair.';
