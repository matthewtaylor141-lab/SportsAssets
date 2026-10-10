-- RC6.3c PROOF B: SETTLEMENT OUTCOMES ARE UNCHANGED BY THE BATCHED READS (SELECT only; nothing here writes).
--
-- PURPOSE. RC6.3c (dd25c588) makes agents/paper_xavier.py step_settle read the outcome evidence of EVERY position
-- in ONE statement (OUTCOME_ROWS_SQL: us_market_slug = ANY($1::text[]) AND outcome_basis IS NOT NULL ORDER BY id,
-- regrouped per contract in id order by outcome_rows_by_slug) where the running release read it once PER POSITION
-- (WHERE us_market_slug = $1 AND outcome_basis IS NOT NULL ORDER BY id); bettor_capital_authority.settle_shadows
-- likewise reads a batch's outcome rows and venue-price rows in two statements (batched_settlement) and judges
-- each shadow with xavier_management.settlement_from_rows, the same pure code. This file proves, on production:
--   b1  for every PAPER position step_settle iterates (bettor_paper_ledger.positions(include_closed=True): one row
--       per account, group, contract and holding side with any fill), the rows the OLD per-position statement
--       returns equal, row for row and in the same order, the rows the NEW batched statement returns for that
--       contract. Both are expressed in SQL: a LATERAL per-contract read versus one = ANY(array) read regrouped.
--   b2  the positions still open although their contract carries settlement evidence, with the reason the settle
--       code leaves each open (paper_xavier.outcome_for's verdict, recomputed in SQL below).
--   b3  every PAPER settlement recorded after 2026-10-09 00:00:00+00, checked against its contract's evidence: outcome =
--       the recomputed verdict, payout per contract by bettor_paper_ledger._payout_per (WON 1, LOST 0, VOID_REFUND
--       = BUY gross / bought, SETTLED_AT_VENUE_PRICE in (0, 1)), payout_usd = qty x per, the qty = the position's
--       bought - sold (version 1), and the ledger SETTLEMENT (or CORRECTION) entry that credits it.
--   b4  every SHADOW counterfactual outcome settled after 2026-10-09 00:00:00+00, checked the same way (settle_shadows:
--       NO_FILL when nothing filled; payout 1 / 0 / NULL; pnl = filled x payout - cost - fees, 0 for VOID / NO_FILL).
--   b4b the PENDING shadows (no outcome row yet) whose contract already carries a definite verdict: what the shadow
--       settle step must settle on its next run -- the shadow analogue of b2's would_settle_on_the_next_pass.
--   b5  the settle and shadow_settlement step_elapsed_s and digests of the last pass (heartbeat and health row),
--       the shadow_settlement digest decoded field by field with a FAIL flag.
--
-- PLACEHOLDER. 2026-10-09 00:00:00+00 : the deploy instant (ISO timestamp with zone), e.g. 2026-10-10 18:00:00+00.
--
-- WHY B5 IS DECODED, AND WHAT A SINGLE READ CANNOT SEE (bettor_capital_authority.py on dd25c588). The shadow step
-- runs settle_shadows at most once per RUN_EVERY_S = 600 s (:177, :1036-1037: the other passes return
-- {ran: false, why: RAN_WITHIN_RUN_EVERY_S}). settle_shadows SWALLOWS a failing batched read (:969-979: every shadow
-- stays pending, out.why = "SETTLEMENT_READ_FAILED:<Exc>", no raise) and a step-deadline cut (:956-963, :981-983:
-- out.cut = SHADOW_SETTLEMENT_CUT_AT_THE_STEP_DEADLINE, not_examined = n) into its RESULT, and paper_runtime counts
-- a step's own result as an error only for RESULT_ERROR_STEPS = {"audrey_coverage"} (paper_runtime.py:512), so such
-- a run leaves errors = 0, ok = true, a fast shadow_settlement_s and nothing written: b4 is then vacuously clean.
-- (The PAPER settle step has no such hole: a raise in outcome_rows_by_slug propagates and is counted.) Hence:
--   * B5 prints ran, why, cut, not_examined, refusal, examined, settled, pending, errors from the digest and flags
--     FAIL_SETTLEMENT_READ_FAILED / FAIL_CUT_AT_STEP_DEADLINE / FAIL_REFUSED / FAIL_ERRORS; a run is RAN_CLEAN only
--     when ran = true with none of them. NOT_DUE (ran = false, RAN_WITHIN_RUN_EVERY_S) says nothing about the last
--     run except that one was attempted within 600 s.
--   * The heartbeat holds the LAST pass only, and only one pass in ten runs the step, so a single read of B5 shows
--     RAN_CLEAN with probability about 1/10. research/rc63c_shadow_step_sampler.sql samples the heartbeat every 30 s
--     for 12 minutes in one research-sql run (24 statements; RUN_EVERY_S + one cadence = 660 s < 12 min, so it sees
--     at least one ran = true pass): run it once after the deploy, and again inside the 60-minute window; it must
--     show >= 1 RAN_CLEAN and no FAIL row.
--   * B4b is the functional check that does not depend on catching the run: a pending shadow whose contract has a
--     definite verdict (WON / LOST / VOID_REFUND for its side) is settled on the step's next run; one that persists
--     across two reads 600 s apart means the step is not settling (read failed, cut every run, or refused).
--
-- VACUITY (state it in the report, do not count it as evidence). b2 is vacuous when B0 open_positions = 0 for every
-- account (0 on 2026-10-10: every position settled); b3 when settlements_after_deploy = 0 (settlement needs a
-- contract to resolve: hours to days after the deploy); b4 when shadow_outcomes_after_deploy = 0; b4b when
-- pending_shadows = 0 (0 on 2026-10-10 20:03Z, run 38082216890 V4g). b1 (the equivalence of the two reads over the
-- whole position set) and b5 / the sampler are never vacuous.
--
-- THE VERDICT RULE (paper_xavier.outcome_for, LABEL_BASES = VENUE_SETTLEMENT_PRICE / VENUE_REPORTED_OUTCOME,
-- VOID_BASIS = CONFIRMED_VOID, derek_policy LONG = ORDER_INTENT_BUY_LONG, SHORT = ORDER_INTENT_BUY_SHORT):
-- a CONFIRMED_VOID row says VOID_REFUND; a label-basis row with outcome_known and outcome in (0, 1) says WON when
-- (outcome = 1) equals (buy_intent = our side's intent), else LOST; other rows are ignored; more than one distinct
-- verdict = CONFLICTING_SETTLEMENT_EVIDENCE; none = NO_AUTHORITATIVE_SETTLEMENT_YET.
--
-- PASS CRITERIA. b1: contracts_differing = 0 and positions_differing = 0, old_rows_total = new_rows_total.
-- b2: would_settle_on_the_next_pass = 0 once a pass has run on the new release (a position that keeps that
-- label across two reads means the settle step is not settling). b3: inconsistent_settlements = 0.
-- b4: inconsistent_shadow_outcomes = 0. b4b: pending_with_definite_verdict = 0 after a run with ran = true (a
-- shadow that keeps that label across two reads 600 s apart = the step is not settling); pending_conflicting is
-- counted, not failed. b5: step_elapsed_s.settle below 1.0 s on the new release (it was 30-43 s on 4534b43f,
-- research-sql 38077620702); the shadow_settlement digest, whenever ran = true (this read or any sampler row),
-- carries no why beginning SETTLEMENT_READ_FAILED, no cut, no refusal, errors = 0, and shadow_settlement_s below
-- 1.0 s; the sampler shows ran = true at least once per 600 s window sampled (>= 1 RAN_CLEAN row, 0 FAIL rows).
-- Report b2 / b3 / b4 / b4b as VACUOUS when their population is empty (see VACUITY above).

\echo B0 the position set step_settle iterates (all accounts): positions, open, settled, distinct contracts
WITH pos AS (
  SELECT account_id, group_id, us_market_slug, holding_side,
         coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS bought,
         coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold
    FROM paper_fills GROUP BY 1, 2, 3, 4),
st AS (SELECT DISTINCT ON (position_key) position_key, qty, outcome, version FROM paper_settlements ORDER BY position_key, version DESC),
p AS (SELECT pos.*, st.outcome AS settled, pos.bought - pos.sold - coalesce(st.qty, 0) AS open_qty
        FROM pos LEFT JOIN st ON st.position_key = 'paperpos:' || pos.account_id || ':' || pos.group_id || ':' || pos.us_market_slug || ':' || pos.holding_side)
SELECT account_id, count(*) AS positions, count(*) FILTER (WHERE open_qty > 1e-9) AS open_positions,
       count(*) FILTER (WHERE settled IS NOT NULL) AS with_settlement, count(DISTINCT us_market_slug) AS contracts
  FROM p GROUP BY 1 ORDER BY 1;

\echo B1 OLD per-position read (LATERAL, one scan per contract) versus NEW batched read (= ANY, regrouped in id order): rows must match
WITH pos AS (
  SELECT account_id, group_id, us_market_slug, holding_side FROM paper_fills GROUP BY 1, 2, 3, 4),
slugs AS (SELECT DISTINCT us_market_slug FROM pos),
old AS (
  SELECT s.us_market_slug, o.hs
    FROM slugs s CROSS JOIN LATERAL (
      SELECT coalesce(array_agg(md5(row(id, buy_intent, outcome, outcome_known, outcome_basis, outcome_at)::text) ORDER BY id), '{}'::text[]) AS hs
        FROM external_valuations WHERE us_market_slug = s.us_market_slug AND outcome_basis IS NOT NULL) o),
newb AS (
  SELECT us_market_slug, array_agg(h ORDER BY id) AS hs
    FROM (SELECT id, us_market_slug, md5(row(id, buy_intent, outcome, outcome_known, outcome_basis, outcome_at)::text) AS h
            FROM external_valuations
           WHERE us_market_slug = ANY((SELECT array_agg(us_market_slug) FROM slugs)::text[]) AND outcome_basis IS NOT NULL
           ORDER BY id) q
   GROUP BY us_market_slug),
cmp AS (SELECT o.us_market_slug, o.hs AS old_hs, coalesce(n.hs, '{}'::text[]) AS new_hs FROM old o LEFT JOIN newb n USING (us_market_slug))
SELECT (SELECT count(*) FROM pos) AS positions,
       count(*) AS contracts,
       count(*) FILTER (WHERE old_hs <> new_hs) AS contracts_differing,
       (SELECT count(*) FROM pos JOIN cmp USING (us_market_slug) WHERE cmp.old_hs <> cmp.new_hs) AS positions_differing,
       coalesce(sum(cardinality(old_hs)), 0) AS old_rows_total, coalesce(sum(cardinality(new_hs)), 0) AS new_rows_total,
       count(*) FILTER (WHERE cardinality(old_hs) > 0) AS contracts_with_evidence,
       count(*) FILTER (WHERE cardinality(old_hs) = 0) AS contracts_without_evidence,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM cmp;

\echo B1b the production cost of ONE old per-position read (one contract, the scan the old code ran once per position)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT id, buy_intent, outcome, outcome_known, outcome_basis, outcome_at
  FROM external_valuations
 WHERE us_market_slug = (SELECT us_market_slug FROM paper_fills ORDER BY filled_at DESC LIMIT 1) AND outcome_basis IS NOT NULL
 ORDER BY id;

\echo B1c the production cost of the ONE new batched read over the contracts of every position (OUTCOME_ROWS_SQL)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT id, us_market_slug, buy_intent, outcome, outcome_known, outcome_basis, outcome_at
  FROM external_valuations
 WHERE us_market_slug = ANY((SELECT array_agg(DISTINCT us_market_slug) FROM paper_fills)::text[]) AND outcome_basis IS NOT NULL
 ORDER BY id;

\echo B2 open positions whose contract carries evidence rows, with the reason the settle code leaves them open (counts, then rows)
WITH pos AS (
  SELECT account_id, group_id, us_market_slug, holding_side,
         coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS bought,
         coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold
    FROM paper_fills GROUP BY 1, 2, 3, 4),
st AS (SELECT DISTINCT ON (position_key) position_key, qty, outcome FROM paper_settlements ORDER BY position_key, version DESC),
p AS (SELECT pos.*, st.outcome AS settled, pos.bought - pos.sold - coalesce(st.qty, 0) AS open_qty
        FROM pos LEFT JOIN st ON st.position_key = 'paperpos:' || pos.account_id || ':' || pos.group_id || ':' || pos.us_market_slug || ':' || pos.holding_side),
verd AS (
  SELECT v.us_market_slug, sd.side, count(*) AS rows_n,
         bool_or(v.outcome_basis = 'CONFIRMED_VOID') AS any_void,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_won,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND NOT ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_lost
    FROM external_valuations v CROSS JOIN (VALUES ('LONG'), ('SHORT')) AS sd(side)
   WHERE v.outcome_basis IS NOT NULL AND v.us_market_slug = ANY((SELECT array_agg(DISTINCT us_market_slug) FROM pos)::text[])
   GROUP BY 1, 2),
vp AS (
  SELECT us_market_slug, count(*) AS venue_price_rows FROM external_valuations
   WHERE outcome_basis IS NULL AND settlement_read IS NOT NULL AND settlement_read_at IS NOT NULL
     AND us_market_slug = ANY((SELECT array_agg(DISTINCT us_market_slug) FROM pos)::text[]) GROUP BY 1),
j AS (
  SELECT p.*, coalesce(v.rows_n, 0) AS rows_n, coalesce(vp.venue_price_rows, 0) AS venue_price_rows,
         CASE WHEN v.us_market_slug IS NULL THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET'
              WHEN (v.any_void::int + v.any_won::int + v.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
              WHEN v.any_void THEN 'VOID_REFUND' WHEN v.any_won THEN 'WON' WHEN v.any_lost THEN 'LOST'
              ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END AS verdict
    FROM p LEFT JOIN verd v ON v.us_market_slug = p.us_market_slug AND v.side = p.holding_side
    LEFT JOIN vp ON vp.us_market_slug = p.us_market_slug
   WHERE p.open_qty > 1e-9 AND p.settled IS NULL),
r AS (
  SELECT j.*, CASE WHEN verdict = 'CONFLICTING_SETTLEMENT_EVIDENCE' THEN 'CONFLICTING_SETTLEMENT_EVIDENCE: counted in conflicts, finding written, stays open'
                   WHEN verdict IN ('WON', 'LOST', 'VOID_REFUND') THEN 'would_settle_on_the_next_pass'
                   WHEN rows_n > 0 THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET: rows exist but none is a known 0/1 label-basis or void row (waiting)'
                   WHEN venue_price_rows > 0 THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET: only venue price rows (completed-game policy path, else waiting)'
                   ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET: no outcome rows for the contract (waiting)' END AS reason
    FROM j)
SELECT reason, count(*) AS open_positions, round(sum(open_qty), 6) AS open_qty FROM r GROUP BY 1 ORDER BY 2 DESC;
WITH pos AS (
  SELECT account_id, group_id, us_market_slug, holding_side,
         coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS bought,
         coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold
    FROM paper_fills GROUP BY 1, 2, 3, 4),
st AS (SELECT DISTINCT ON (position_key) position_key, qty, outcome FROM paper_settlements ORDER BY position_key, version DESC),
p AS (SELECT pos.*, st.outcome AS settled, pos.bought - pos.sold - coalesce(st.qty, 0) AS open_qty
        FROM pos LEFT JOIN st ON st.position_key = 'paperpos:' || pos.account_id || ':' || pos.group_id || ':' || pos.us_market_slug || ':' || pos.holding_side),
verd AS (
  SELECT v.us_market_slug, sd.side, count(*) AS rows_n,
         bool_or(v.outcome_basis = 'CONFIRMED_VOID') AS any_void,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_won,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND NOT ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_lost
    FROM external_valuations v CROSS JOIN (VALUES ('LONG'), ('SHORT')) AS sd(side)
   WHERE v.outcome_basis IS NOT NULL AND v.us_market_slug = ANY((SELECT array_agg(DISTINCT us_market_slug) FROM pos)::text[])
   GROUP BY 1, 2)
SELECT p.account_id, left(p.group_id, 28) AS group_id, p.us_market_slug, p.holding_side, round(p.open_qty, 6) AS open_qty,
       coalesce(v.rows_n, 0) AS evidence_rows, v.any_void, v.any_won, v.any_lost,
       CASE WHEN v.us_market_slug IS NULL THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET'
            WHEN (v.any_void::int + v.any_won::int + v.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
            WHEN v.any_void THEN 'VOID_REFUND' WHEN v.any_won THEN 'WON' WHEN v.any_lost THEN 'LOST'
            ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END AS verdict
  FROM p LEFT JOIN verd v ON v.us_market_slug = p.us_market_slug AND v.side = p.holding_side
 WHERE p.open_qty > 1e-9 AND p.settled IS NULL
 ORDER BY (v.rows_n IS NOT NULL) DESC, p.us_market_slug LIMIT 40;

\echo B3 PAPER settlements recorded after 2026-10-09 00:00:00+00: each checked against its evidence (inconsistent count first, then rows)
WITH s AS (
  SELECT * FROM paper_settlements WHERE recorded_at >= TIMESTAMPTZ '2026-10-09 00:00:00+00'),
fills AS (
  SELECT account_id, group_id, us_market_slug, holding_side,
         coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS bought,
         coalesce(sum(gross_usd) FILTER (WHERE direction = 'BUY'), 0) AS buy_gross,
         coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold
    FROM paper_fills WHERE group_id IN (SELECT group_id FROM s) GROUP BY 1, 2, 3, 4),
verd AS (
  SELECT v.us_market_slug, sd.side, count(*) AS rows_n,
         bool_or(v.outcome_basis = 'CONFIRMED_VOID') AS any_void,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_won,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND NOT ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_lost
    FROM external_valuations v CROSS JOIN (VALUES ('LONG'), ('SHORT')) AS sd(side)
   WHERE v.outcome_basis IS NOT NULL AND v.us_market_slug IN (SELECT us_market_slug FROM s)
   GROUP BY 1, 2),
vp AS (
  SELECT us_market_slug, count(*) AS venue_price_rows FROM external_valuations
   WHERE outcome_basis IS NULL AND settlement_read IS NOT NULL AND settlement_read_at IS NOT NULL
     AND us_market_slug IN (SELECT us_market_slug FROM s) GROUP BY 1),
chk AS (
  SELECT s.settlement_id, s.recorded_at, s.account_id, s.us_market_slug, s.holding_side, s.outcome, s.version, s.qty,
         s.payout_per_contract AS per, s.payout_usd, s.evidence_source,
         CASE WHEN v.us_market_slug IS NULL THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET'
              WHEN (v.any_void::int + v.any_won::int + v.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
              WHEN v.any_void THEN 'VOID_REFUND' WHEN v.any_won THEN 'WON' WHEN v.any_lost THEN 'LOST'
              ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END AS verdict,
         coalesce(vp.venue_price_rows, 0) AS venue_price_rows,
         f.bought, f.sold, f.buy_gross,
         (SELECT count(*) FROM paper_ledger l WHERE l.kind = 'SETTLEMENT' AND l.settlement_key = s.position_key || ':' || s.settlement_event_key
                                             AND abs(l.cash_delta_usd - s.payout_usd) < 1e-6) AS ledger_settlement_rows,
         (SELECT count(*) FROM paper_ledger l WHERE l.kind = 'CORRECTION' AND l.position_key = s.position_key
                                             AND l.detail->>'settlement_id' = s.settlement_id) AS ledger_correction_rows
    FROM s
    LEFT JOIN verd v ON v.us_market_slug = s.us_market_slug AND v.side = s.holding_side
    LEFT JOIN vp ON vp.us_market_slug = s.us_market_slug
    LEFT JOIN fills f ON f.account_id = s.account_id AND f.group_id = s.group_id AND f.us_market_slug = s.us_market_slug AND f.holding_side = s.holding_side),
judged AS (
  SELECT chk.*,
         CASE WHEN outcome IN ('WON', 'LOST', 'VOID_REFUND') THEN outcome = verdict
              WHEN outcome = 'SETTLED_AT_VENUE_PRICE' THEN verdict = 'NO_AUTHORITATIVE_SETTLEMENT_YET' AND venue_price_rows > 0
              ELSE false END AS c_outcome,
         CASE outcome WHEN 'WON' THEN per = 1 WHEN 'LOST' THEN per = 0
              WHEN 'VOID_REFUND' THEN bought > 0 AND abs(per - buy_gross / bought) < 1e-6
              WHEN 'SETTLED_AT_VENUE_PRICE' THEN per > 0 AND per < 1 ELSE false END AS c_per,
         abs(payout_usd - round(qty * per, 6)) < 1e-6 AS c_payout,
         (version > 1) OR abs(qty - (coalesce(bought, 0) - coalesce(sold, 0))) < 1e-6 AS c_qty,
         CASE WHEN version = 1 THEN ledger_settlement_rows = 1 ELSE ledger_correction_rows = 1 END AS c_ledger
    FROM chk)
SELECT count(*) AS settlements_after_deploy,
       count(*) FILTER (WHERE NOT (c_outcome AND c_per AND c_payout AND c_qty AND c_ledger)) AS inconsistent_settlements,
       count(*) FILTER (WHERE NOT c_outcome) AS outcome_mismatch, count(*) FILTER (WHERE NOT c_per) AS per_contract_mismatch,
       count(*) FILTER (WHERE NOT c_payout) AS payout_mismatch, count(*) FILTER (WHERE NOT c_qty) AS qty_mismatch,
       count(*) FILTER (WHERE NOT c_ledger) AS ledger_entry_missing,
       count(*) FILTER (WHERE version > 1) AS corrections
  FROM judged;
WITH s AS (
  SELECT * FROM paper_settlements WHERE recorded_at >= TIMESTAMPTZ '2026-10-09 00:00:00+00'),
verd AS (
  SELECT v.us_market_slug, sd.side, count(*) AS rows_n,
         bool_or(v.outcome_basis = 'CONFIRMED_VOID') AS any_void,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_won,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND NOT ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_lost
    FROM external_valuations v CROSS JOIN (VALUES ('LONG'), ('SHORT')) AS sd(side)
   WHERE v.outcome_basis IS NOT NULL AND v.us_market_slug IN (SELECT us_market_slug FROM s)
   GROUP BY 1, 2)
SELECT to_char(s.recorded_at, 'MM-DD HH24:MI:SS') AS recorded, to_char(s.settled_at, 'MM-DD HH24:MI:SS') AS settled, s.us_market_slug, s.holding_side,
       s.outcome, s.version, round(s.qty, 4) AS qty, round(s.payout_per_contract, 6) AS per, round(s.payout_usd, 4) AS payout_usd, s.evidence_source,
       CASE WHEN v.us_market_slug IS NULL THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET'
            WHEN (v.any_void::int + v.any_won::int + v.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
            WHEN v.any_void THEN 'VOID_REFUND' WHEN v.any_won THEN 'WON' WHEN v.any_lost THEN 'LOST'
            ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END AS verdict_recomputed,
       coalesce(v.rows_n, 0) AS evidence_rows
  FROM s LEFT JOIN verd v ON v.us_market_slug = s.us_market_slug AND v.side = s.holding_side
 ORDER BY s.recorded_at DESC LIMIT 40;

\echo B4 SHADOW counterfactual outcomes settled after 2026-10-09 00:00:00+00: each checked against its evidence (inconsistent count first, then rows)
WITH o AS (
  SELECT o.*, s.us_market_slug, s.holding_side, s.strategy, s.account_id
    FROM paper_shadow_counterfactual_outcomes o JOIN paper_shadow_counterfactuals s USING (shadow_id)
   WHERE o.settled_at >= TIMESTAMPTZ '2026-10-09 00:00:00+00'),
verd AS (
  SELECT v.us_market_slug, sd.side, count(*) AS rows_n,
         bool_or(v.outcome_basis = 'CONFIRMED_VOID') AS any_void,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_won,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND NOT ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_lost
    FROM external_valuations v CROSS JOIN (VALUES ('LONG'), ('SHORT')) AS sd(side)
   WHERE v.outcome_basis IS NOT NULL AND v.us_market_slug IN (SELECT us_market_slug FROM o)
   GROUP BY 1, 2),
vp AS (
  SELECT us_market_slug, count(*) AS venue_price_rows FROM external_valuations
   WHERE outcome_basis IS NULL AND settlement_read IS NOT NULL AND settlement_read_at IS NOT NULL
     AND us_market_slug IN (SELECT us_market_slug FROM o) GROUP BY 1),
chk AS (
  SELECT o.outcome_id, o.settled_at, o.us_market_slug, o.holding_side, o.strategy, o.outcome, o.payout_per_contract AS per,
         o.filled_qty, o.exec_cost_usd, o.exec_fees_usd, o.counterfactual_pnl_usd AS pnl, o.execution_basis,
         o.evidence->>'settlement_outcome' AS ev_outcome,
         CASE WHEN v.us_market_slug IS NULL THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET'
              WHEN (v.any_void::int + v.any_won::int + v.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
              WHEN v.any_void THEN 'VOID_REFUND' WHEN v.any_won THEN 'WON' WHEN v.any_lost THEN 'LOST'
              ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END AS verdict,
         coalesce(vp.venue_price_rows, 0) AS venue_price_rows
    FROM o LEFT JOIN verd v ON v.us_market_slug = o.us_market_slug AND v.side = o.holding_side
    LEFT JOIN vp ON vp.us_market_slug = o.us_market_slug),
judged AS (
  SELECT chk.*,
         CASE WHEN outcome = 'NO_FILL' THEN filled_qty = 0 AND (ev_outcome = verdict OR (ev_outcome = 'SETTLED_AT_VENUE_PRICE' AND verdict = 'NO_AUTHORITATIVE_SETTLEMENT_YET'))
              WHEN outcome IN ('WON', 'LOST', 'VOID_REFUND') THEN outcome = verdict AND ev_outcome = verdict AND filled_qty > 0
              WHEN outcome = 'SETTLED_AT_VENUE_PRICE' THEN verdict = 'NO_AUTHORITATIVE_SETTLEMENT_YET' AND venue_price_rows > 0 AND filled_qty > 0
              ELSE false END AS c_outcome,
         CASE ev_outcome WHEN 'WON' THEN per = 1 WHEN 'LOST' THEN per = 0 WHEN 'VOID_REFUND' THEN per IS NULL
              WHEN 'SETTLED_AT_VENUE_PRICE' THEN per > 0 AND per < 1 ELSE false END AS c_per,
         CASE WHEN outcome IN ('NO_FILL', 'VOID_REFUND') THEN pnl = 0
              ELSE abs(pnl - round(filled_qty * per - exec_cost_usd - exec_fees_usd, 6)) < 1e-5 END AS c_pnl
    FROM chk)
SELECT count(*) AS shadow_outcomes_after_deploy,
       count(*) FILTER (WHERE NOT (c_outcome AND c_per AND c_pnl)) AS inconsistent_shadow_outcomes,
       count(*) FILTER (WHERE NOT c_outcome) AS outcome_mismatch, count(*) FILTER (WHERE NOT c_per) AS per_contract_mismatch,
       count(*) FILTER (WHERE NOT c_pnl) AS pnl_mismatch,
       count(*) FILTER (WHERE outcome = 'NO_FILL') AS no_fill, count(*) FILTER (WHERE outcome = 'WON') AS won,
       count(*) FILTER (WHERE outcome = 'LOST') AS lost, count(*) FILTER (WHERE outcome = 'VOID_REFUND') AS void_refund,
       count(*) FILTER (WHERE outcome = 'SETTLED_AT_VENUE_PRICE') AS at_venue_price
  FROM judged;
SELECT to_char(o.settled_at, 'MM-DD HH24:MI:SS') AS settled, s.us_market_slug, s.holding_side, s.strategy, o.outcome,
       o.payout_per_contract AS per, round(o.filled_qty, 4) AS filled, round(o.exec_cost_usd, 4) AS cost, round(o.exec_fees_usd, 4) AS fees,
       round(o.counterfactual_pnl_usd, 4) AS pnl, o.evidence->>'settlement_outcome' AS ev_outcome, o.execution_basis
  FROM paper_shadow_counterfactual_outcomes o JOIN paper_shadow_counterfactuals s USING (shadow_id)
 WHERE o.settled_at >= TIMESTAMPTZ '2026-10-09 00:00:00+00'
 ORDER BY o.settled_at DESC LIMIT 40;
SELECT count(*) AS shadows_total, count(o.shadow_id) AS settled_total, count(*) - count(o.shadow_id) AS pending_now,
       to_char(max(o.settled_at), 'YYYY-MM-DD HH24:MI:SS') AS newest_settled_at,
       to_char(max(s.decided_at), 'YYYY-MM-DD HH24:MI:SS') AS newest_shadow_decided_at,
       count(*) FILTER (WHERE s.decided_at >= TIMESTAMPTZ '2026-10-09 00:00:00+00') AS shadows_decided_after_deploy
  FROM paper_shadow_counterfactuals s LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id);

\echo B4b PENDING shadows whose contract already carries a definite verdict for their side (what the shadow step must settle on its next run; pending_with_definite_verdict must be 0 after a run with ran = true; VACUOUS when pending_shadows = 0)
WITH p AS (
  SELECT s.shadow_id, s.us_market_slug, s.holding_side, s.decided_at
    FROM paper_shadow_counterfactuals s LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
   WHERE o.shadow_id IS NULL),
verd AS (
  SELECT v.us_market_slug, sd.side, count(*) AS rows_n,
         bool_or(v.outcome_basis = 'CONFIRMED_VOID') AS any_void,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_won,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND NOT ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_lost
    FROM external_valuations v CROSS JOIN (VALUES ('LONG'), ('SHORT')) AS sd(side)
   WHERE v.outcome_basis IS NOT NULL AND v.us_market_slug IN (SELECT us_market_slug FROM p)
   GROUP BY 1, 2),
vp AS (
  SELECT us_market_slug, count(*) AS venue_price_rows FROM external_valuations
   WHERE outcome_basis IS NULL AND settlement_read IS NOT NULL AND settlement_read_at IS NOT NULL
     AND us_market_slug IN (SELECT us_market_slug FROM p) GROUP BY 1)
SELECT count(*) AS pending_shadows,
       count(*) FILTER (WHERE v.us_market_slug IS NOT NULL AND (v.any_void::int + v.any_won::int + v.any_lost::int) = 1) AS pending_with_definite_verdict,
       count(*) FILTER (WHERE v.us_market_slug IS NOT NULL AND (v.any_void::int + v.any_won::int + v.any_lost::int) > 1) AS pending_conflicting,
       count(*) FILTER (WHERE (v.us_market_slug IS NULL OR (v.any_void::int + v.any_won::int + v.any_lost::int) = 0) AND coalesce(vp.venue_price_rows, 0) > 0) AS pending_with_venue_price_rows_only,
       count(*) FILTER (WHERE v.us_market_slug IS NULL AND coalesce(vp.venue_price_rows, 0) = 0) AS pending_without_evidence,
       to_char(min(p.decided_at), 'MM-DD HH24:MI') AS oldest_pending, to_char(max(p.decided_at), 'MM-DD HH24:MI') AS newest_pending,
       CASE WHEN count(*) = 0 THEN 'VACUOUS: no pending shadow'
            WHEN count(*) FILTER (WHERE v.us_market_slug IS NOT NULL AND (v.any_void::int + v.any_won::int + v.any_lost::int) = 1) = 0 THEN 'ok'
            ELSE 'PENDING_WITH_DEFINITE_VERDICT: must be 0 after a run with ran = true' END AS verdict,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM p LEFT JOIN verd v ON v.us_market_slug = p.us_market_slug AND v.side = p.holding_side LEFT JOIN vp ON vp.us_market_slug = p.us_market_slug;
WITH p AS (
  SELECT s.shadow_id, s.us_market_slug, s.holding_side, s.strategy, s.decided_at
    FROM paper_shadow_counterfactuals s LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
   WHERE o.shadow_id IS NULL),
verd AS (
  SELECT v.us_market_slug, sd.side, count(*) AS rows_n,
         bool_or(v.outcome_basis = 'CONFIRMED_VOID') AS any_void,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_won,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND NOT ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_lost
    FROM external_valuations v CROSS JOIN (VALUES ('LONG'), ('SHORT')) AS sd(side)
   WHERE v.outcome_basis IS NOT NULL AND v.us_market_slug IN (SELECT us_market_slug FROM p)
   GROUP BY 1, 2)
SELECT to_char(p.decided_at, 'MM-DD HH24:MI:SS') AS decided, p.us_market_slug, p.holding_side, p.strategy, coalesce(v.rows_n, 0) AS evidence_rows,
       v.any_void, v.any_won, v.any_lost,
       CASE WHEN v.us_market_slug IS NULL THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET'
            WHEN (v.any_void::int + v.any_won::int + v.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
            WHEN v.any_void THEN 'VOID_REFUND' WHEN v.any_won THEN 'WON' WHEN v.any_lost THEN 'LOST'
            ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END AS verdict
  FROM p LEFT JOIN verd v ON v.us_market_slug = p.us_market_slug AND v.side = p.holding_side
 WHERE v.us_market_slug IS NOT NULL AND (v.any_void::int + v.any_won::int + v.any_lost::int) >= 1
 ORDER BY p.decided_at LIMIT 40;

\echo B5 the last pass: settle and shadow_settlement elapsed seconds and digests (heartbeat), the shadow digest decoded with its FAIL flag, then the health row last_pass
SELECT to_char(to_timestamp((value->>'written_at')::float8), 'YYYY-MM-DD HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_elapsed_s,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_settlement_s,
       left((value->'steps'->'settle')::text, 240) AS settle_digest, left((value->'steps'->'shadow_settlement')::text, 240) AS shadow_settlement_digest,
       value->>'exceeded_step' AS exceeded_step
  FROM ingestion_state WHERE key = 'paper_session_last_pass';
SELECT to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at, sd->>'ran' AS shadow_ran, sd->>'why' AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined,
       sd->>'refusal' AS refusal, sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       value->'step_elapsed_s'->>'shadow_settlement' AS shadow_settlement_s,
       CASE WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE (a run was attempted within the last 600 s; see the sampler)'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
SELECT to_char(heartbeat_at, 'YYYY-MM-DD HH24:MI:SS') AS health_at, passes, errors,
       last_pass->>'elapsed_s' AS pass_elapsed_s, last_pass->'step_elapsed_s'->>'settle' AS settle_s,
       last_pass->'step_elapsed_s'->>'shadow_settlement' AS shadow_settlement_s,
       left((last_pass->'steps'->'settle')::text, 240) AS settle_digest
  FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1;
