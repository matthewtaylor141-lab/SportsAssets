-- MANAGEMENT ACCOUNTING FOR THE BETTOR EV SHADOW LANE.
--
-- Owner directive 2026-09-19: "COMMAND must continuously answer: HOW
-- MUCH CAPITAL HAVE WE PLAYED THROUGH? HOW MUCH CAPITAL DID WE ACTUALLY
-- NEED? HOW MANY TIMES DID WE RECYCLE IT? HOW MUCH DID WE MAKE? WHAT
-- RETURN DID THAT CAPITAL PRODUCE?"
--
-- WHAT THIS MIGRATION DOES NOT DO. It does not create a second ledger.
-- shadow_positions, shadow_position_events and shadow_executions from
-- migration 068 already are the prospective shadow ledger, and the
-- directive says every figure must be "sourced from the actual
-- prospective shadow ledger." A parallel accounting ledger would be a
-- second version of the truth that could disagree with the first. So
-- this migration adds the DOLLAR facts the existing rows were missing
-- and freezes the sizing rule; everything else is derived by reading.
--
-- THE THREE CAPITAL NUMBERS ARE NOT ONE NUMBER. The directive is
-- explicit that they must not be collapsed, and the schema is what
-- keeps them apart:
--
--   ENTRY_NOTIONAL_PLAYED   executed ENTRY legs only.
--   GROSS_TRADING_TURNOVER  every executed position-changing leg.
--   CAPITAL_DEPLOYED        cost basis still tied up at an instant.
--
-- The first two are sums over executions and differ only because
-- execution_leg_kind tells them apart. The third is not a sum at all --
-- it is a step function over time, derived below from the position
-- events rather than sampled, so it cannot drift from the ledger it
-- describes.
--
-- INTENDED IS NOT EXECUTED. "STANDARD_BETTOR_SHADOW_NOTIONAL_USD = 1000
-- ... This is intended notional. It is NOT automatically filled
-- notional ... Never invent liquidity to reach $1,000." Three separate
-- columns, and a CHECK that makes the arithmetic impossible to get
-- wrong in the writer.
--
-- NOTHING HERE MAKES BETTOR TRADE. "The $1,000 assumption determines
-- sizing. It does NOT determine whether BETTOR trades." Every table and
-- column below is empty until the EV engine earns its first eligible
-- entry through the gates it already has.

BEGIN;

-- ── 1. THE FROZEN SIZING POLICY ─────────────────────────────────────
--
-- WHY THIS IS ITS OWN VERSION AND NOT PART OF BETTOR_EV_SHADOW_V1.
-- Three reasons, and they all point the same way.
--
--   1. BETTOR_EV_SHADOW_V1's frozen declaration says sizing is
--      NOT_APPLICABLE, and that is still TRUE: V1's action set is
--      exactly [NO_TRADE], so no V1 decision can ever size anything.
--      The statement does not become false because a sizing rule now
--      exists for the actions V1 cannot emit.
--
--   2. Editing that declaration would move POLICY_SHA, and
--      shadow_decisions_policy_frozen would then REFUSE every further
--      BETTOR decision -- the exact failure that cost 99 decisions
--      before the freeze landed. A frozen policy carrying rows is not
--      editable by design.
--
--   3. "Keep this policy versioned. If sizing changes later, preserve
--      the $1,000 cohort as a benchmark so performance remains
--      comparable over time." A benchmark cohort that outlives changes
--      to the EV policy has to be versioned independently of it.
--
-- So sizing is declared BESIDE the EV policy, not inside it, and every
-- position records which sizing version produced its intended notional.
CREATE TABLE IF NOT EXISTS bettor_sizing_policies (
    sizing_policy_version   TEXT PRIMARY KEY,
    -- The management-facing name of the standardized strategy view.
    cohort                  TEXT        NOT NULL,
    lane                    TEXT        NOT NULL,
    standard_notional_usd   NUMERIC     NOT NULL,
    policy_sha              TEXT        NOT NULL,
    declaration             JSONB       NOT NULL,
    frozen_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- RN1 economics and BETTOR economics are never combined, and a
    -- sizing rule is an economic rule.
    CONSTRAINT bettor_sizing_lane CHECK (lane = 'BETTOR_EV_SHADOW'),
    -- Intended notional is a positive dollar amount or it is not a
    -- sizing rule.
    CONSTRAINT bettor_sizing_positive CHECK (standard_notional_usd > 0)
);

DROP TRIGGER IF EXISTS bettor_sizing_policies_immutable
    ON bettor_sizing_policies;
CREATE TRIGGER bettor_sizing_policies_immutable
    BEFORE UPDATE OR DELETE ON bettor_sizing_policies
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- ── 2. THE DOLLAR FACTS ON AN EXECUTED LEG ──────────────────────────
--
-- shadow_executions already carried quantities, a vwap, slippage and
-- spread cost. What it could not answer is "how many DOLLARS, and were
-- they an entry or an exit" -- which is precisely the difference
-- between ENTRY_NOTIONAL_PLAYED and GROSS_TRADING_TURNOVER.
--
-- Every column is NULLABLE on purpose. A dollar figure nobody has
-- established must read NOT_IDENTIFIED on the screen, never 0: zero is
-- a measurement claim, and "Do not display 0.00% where there has been
-- no capital deployment" is the same instinct one level down.

ALTER TABLE shadow_executions
    ADD COLUMN IF NOT EXISTS execution_leg_kind      TEXT,
    ADD COLUMN IF NOT EXISTS sizing_policy_version   TEXT
        REFERENCES bettor_sizing_policies (sizing_policy_version),
    ADD COLUMN IF NOT EXISTS intended_notional_usd   NUMERIC,
    ADD COLUMN IF NOT EXISTS executed_notional_usd   NUMERIC,
    ADD COLUMN IF NOT EXISTS unfilled_notional_usd   NUMERIC,
    ADD COLUMN IF NOT EXISTS fees_usd                NUMERIC,
    ADD COLUMN IF NOT EXISTS spread_cost_usd         NUMERIC,
    ADD COLUMN IF NOT EXISTS slippage_cost_usd       NUMERIC,
    -- "ADVERSE SELECTION where identified" -- and only where identified.
    ADD COLUMN IF NOT EXISTS adverse_selection_usd   NUMERIC;

-- WHICH KIND OF LEG. The turnover definition in the directive is a list
-- of exactly these, so the list lives in a CHECK rather than in the
-- heads of whoever writes the next query.
--
--   "entries + exits + cash-outs + pair/complement executions + other
--    executed position-changing actions."
ALTER TABLE shadow_executions
    DROP CONSTRAINT IF EXISTS shadow_exec_leg_kind;
ALTER TABLE shadow_executions
    ADD CONSTRAINT shadow_exec_leg_kind CHECK (
        execution_leg_kind IS NULL OR execution_leg_kind IN (
            'ENTRY', 'EXIT', 'CASHOUT', 'PAIR', 'COMPLEMENT',
            'SETTLEMENT', 'OTHER_POSITION_CHANGING'));

-- INTENDED = EXECUTED + UNFILLED, always. The directive's own worked
-- example is 1000 = 650 + 350, and a writer that produced 1000/650/0
-- would silently overstate fill quality forever. Enforced only when all
-- three are present, because a partially-known leg is allowed to be
-- partially known.
ALTER TABLE shadow_executions
    DROP CONSTRAINT IF EXISTS shadow_exec_notional_balances;
ALTER TABLE shadow_executions
    ADD CONSTRAINT shadow_exec_notional_balances CHECK (
        intended_notional_usd IS NULL
        OR executed_notional_usd IS NULL
        OR unfilled_notional_usd IS NULL
        OR abs(intended_notional_usd
               - (executed_notional_usd + unfilled_notional_usd)) < 0.01);

-- NEVER INVENT LIQUIDITY. Executed can equal intended; it can never
-- exceed it.
ALTER TABLE shadow_executions
    DROP CONSTRAINT IF EXISTS shadow_exec_no_invented_liquidity;
ALTER TABLE shadow_executions
    ADD CONSTRAINT shadow_exec_no_invented_liquidity CHECK (
        intended_notional_usd IS NULL
        OR executed_notional_usd IS NULL
        OR executed_notional_usd <= intended_notional_usd + 0.01);

-- A dollar amount that is present is non-negative. Direction is carried
-- by the leg kind, never by the sign of a notional.
ALTER TABLE shadow_executions
    DROP CONSTRAINT IF EXISTS shadow_exec_notional_non_negative;
ALTER TABLE shadow_executions
    ADD CONSTRAINT shadow_exec_notional_non_negative CHECK (
        COALESCE(intended_notional_usd, 0) >= 0
        AND COALESCE(executed_notional_usd, 0) >= 0
        AND COALESCE(unfilled_notional_usd, 0) >= 0);

CREATE INDEX IF NOT EXISTS shadow_exec_leg_kind_idx
    ON shadow_executions (execution_leg_kind, created_at DESC);

-- ── 3. CAPITAL DEPLOYED, AS A STEP FUNCTION ─────────────────────────
--
-- "CAPITAL_DEPLOYED: Capital actually tied up in open shadow positions
-- at each moment. This answers: How much money would we actually have
-- needed?"
--
-- WHY A DERIVED TIMELINE AND NOT A SAMPLER. A periodic sampler would
-- miss a position that opened and closed between two samples, would
-- report a peak that depends on the sampling rate, and would keep
-- writing rows whether or not anything happened. This view reads the
-- position events that already exist, so PEAK_CAPITAL_DEPLOYED is the
-- true maximum of the true step function rather than the largest number
-- a sampler happened to catch.
--
-- THE MEASURE IS COST BASIS, not mark. What management would have had
-- to fund is what the shares cost, and marking the requirement to
-- market would make "capital we needed" move with P&L.
CREATE OR REPLACE VIEW bettor_capital_timeline AS
WITH bettor_positions AS (
    SELECT p.shadow_position_id, p.entry_price, p.entry_time
      FROM shadow_positions p
      JOIN shadow_decisions d
        ON d.shadow_decision_id = p.originating_decision_id
     -- THE LANE FILTER IS THE WHOLE POINT. "Do not combine RN1
     -- economics with BETTOR economics."
     WHERE d.lane = 'BETTOR_EV_SHADOW'
),
steps AS (
    SELECT e.at,
           e.shadow_position_id,
           -- Cost basis still held after this event.
           COALESCE(e.current_qty, 0) * COALESCE(bp.entry_price, 0)
               AS capital_after
      FROM shadow_position_events e
      JOIN bettor_positions bp
        ON bp.shadow_position_id = e.shadow_position_id
)
SELECT at,
       shadow_position_id,
       capital_after,
       -- The delta this event made to total deployed capital, so a
       -- running sum over `at` is the portfolio step function.
       capital_after - COALESCE(
           lag(capital_after) OVER (PARTITION BY shadow_position_id
                                    ORDER BY at), 0) AS capital_delta
  FROM steps;

COMMIT;
