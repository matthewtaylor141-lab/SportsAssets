-- RC6.3c INDEPENDENT VERIFICATION OF THE PROOF INSTRUMENTS A-E (SELECT only; nothing here writes).
--
-- PURPOSE. Check, on production, the assumptions the five rc63c_*.sql proof files rest on, so that none of them
-- can say PASS while its property is false, or FAIL for a reason that is not the property:
--   V0  the session settings every text-rendered digest depends on (TimeZone, DateStyle, float digits), replica lag.
--   V1  every production table named paper%, and the append-only triggers Proof A relies on (enabled, before-row,
--       firing on rewrite and removal) -- straight from pg_trigger, not from the migration files.
--   V2  the evidence column types the SQL verdict rule compares (outcome integer, outcome_known boolean) and the
--       check constraints the orphan / settlement rules assume.
--   V3  how far a row's cutoff clock can precede the instant it becomes visible: the margin Proof A's cutoff needs
--       before the BEFORE run (a row stamped before the cutoff but visible only after the before-run would read as
--       a back-dated row).
--   V4  settlement equivalence restated independently (full-row signatures per contract), the position set, and
--       Proof B's b3 / b4 consistency rules applied to EVERY settlement and shadow outcome ever recorded: a flagged
--       row in that known-good population is the rule's defect, not the data's; plus the late-evidence sensitivity
--       (verdict against all current rows versus the rows the settlement itself names).
--   V5  the pass ring: unrecorded 60 s slots implied by the gaps, the newest beat versus heartbeat_at, cadence config.
--   V6  order integrity: role x decision_id presence, and venue-side tables Proof D does not name.
--   V7  PAPER execution / decision history kept outside paper%, and whether it is trigger-protected.
--   V8  settlements whose position key joins no fills-derived position (would silently drop out of F6a and B0).
-- Every statement prints took_s (seconds since it started) and finishes far inside the 600 s statement timeout.

\echo V0 session settings the text digests depend on, replica state and replay lag
SELECT current_setting('TimeZone') AS tz, current_setting('DateStyle') AS datestyle,
       current_setting('extra_float_digits') AS extra_float_digits, left(version(), 40) AS pg,
       pg_is_in_recovery() AS replica, to_char(now(), 'YYYY-MM-DD HH24:MI:SS') AS tx_now,
       round(extract(epoch FROM now() - pg_last_xact_replay_timestamp())::numeric, 1) AS replay_lag_s;

\echo V1a every production table named paper% (public schema) with the planner's row estimate
SELECT c.relname, c.reltuples::bigint AS est_rows
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname LIKE 'paper%' ORDER BY 1;

\echo V1b triggers on paper% tables: enabled flag (O origin, A always, R replica, D disabled), before-row, fires on upd / del / ins, function
SELECT c.relname, t.tgname, t.tgenabled, (t.tgtype & 2) <> 0 AS before_row, (t.tgtype & 16) <> 0 AS on_upd,
       (t.tgtype & 8) <> 0 AS on_del, (t.tgtype & 4) <> 0 AS on_ins, p.proname
  FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid
 WHERE NOT t.tgisinternal AND c.relname LIKE 'paper%' ORDER BY 1, 2;

\echo V1c paper% tables WITHOUT an enabled before-row trigger that fires on both upd and del (expected exactly: orders, handoffs, audrey_findings, recommendations, exit_intents, hook_failures, session_health, control, liquidity_consumed, policy_parameter_heads)
SELECT c.relname
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname LIKE 'paper%'
   AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal AND t.tgenabled <> 'D'
                     AND (t.tgtype & 2) <> 0 AND (t.tgtype & 16) <> 0 AND (t.tgtype & 8) <> 0)
 ORDER BY 1;

\echo V2 evidence column types on external_valuations; the check constraints the rules assume
SELECT column_name, data_type, is_nullable FROM information_schema.columns
 WHERE table_name = 'external_valuations'
   AND column_name IN ('id', 'us_market_slug', 'outcome', 'outcome_known', 'outcome_basis', 'outcome_at', 'buy_intent', 'settlement_read', 'settlement_read_at')
 ORDER BY 1;
SELECT conrelid::regclass AS tbl, conname, pg_get_constraintdef(oid) AS def FROM pg_constraint
 WHERE conname IN ('paper_settlements_outcome_ck', 'paper_decisions_verdict_ck', 'paper_orders_role_ck', 'paper_orders_state_ck', 'cie_mode_ck', 'paper_xavier_reviews_trigger_ck')
 ORDER BY 1, 2;

\echo V3a how far a cutoff clock can precede its own commit: ledger committed_at (clock_timestamp) minus the SAME transaction's fills.recorded_at / orders.created_at (now() = transaction start)
SELECT 'fill -> ledger entry' AS pair, count(*) AS n,
       round(max(extract(epoch FROM l.committed_at - f.recorded_at))::numeric, 3) AS max_s,
       round((percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM l.committed_at - f.recorded_at)))::numeric, 3) AS p99_s,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM paper_ledger l JOIN paper_fills f ON f.fill_id = l.fill_id WHERE l.kind IN ('FILL', 'SALE')
UNION ALL
SELECT 'order -> ledger entry', count(*),
       round(max(extract(epoch FROM l.committed_at - o.created_at))::numeric, 3),
       round((percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM l.committed_at - o.created_at)))::numeric, 3),
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2)
  FROM paper_ledger l JOIN paper_orders o ON o.order_id = l.order_id WHERE l.kind = 'ORDER_SUBMITTED';

\echo V3b code clocks (the pass start) versus the row's own transaction start: recorded_at - filled_at / settled_at; created_at - decided_at (F6a filters on filled_at and settled_at)
SELECT 'fills recorded_at - filled_at' AS gap, count(*) AS n, round(max(extract(epoch FROM recorded_at - filled_at))::numeric, 1) AS max_s,
       round((percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM recorded_at - filled_at)))::numeric, 1) AS p99_s FROM paper_fills
UNION ALL SELECT 'settlements recorded_at - settled_at', count(*), round(max(extract(epoch FROM recorded_at - settled_at))::numeric, 1),
       round((percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM recorded_at - settled_at)))::numeric, 1) FROM paper_settlements
UNION ALL SELECT 'orders created_at - decided_at', count(*), round(max(extract(epoch FROM created_at - decided_at))::numeric, 1),
       round((percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM created_at - decided_at)))::numeric, 1) FROM paper_orders
UNION ALL SELECT 'equity snapshots: at versus ledger newest (info)', count(*), NULL, NULL FROM paper_equity_snapshots;

\echo V4a OLD per-slug read versus NEW batched read, restated independently: the full-row signature per contract (every column outcome_for reads, id order)
WITH slugs AS (SELECT DISTINCT us_market_slug FROM paper_fills),
old AS (
  SELECT s.us_market_slug,
         (SELECT coalesce(string_agg(v.id::text || ':' || coalesce(v.buy_intent, '') || ':' || coalesce(v.outcome::text, '') || ':' || v.outcome_known::text || ':' || coalesce(v.outcome_basis, '') || ':' || coalesce(v.outcome_at::text, ''), '|' ORDER BY v.id), '')
            FROM external_valuations v WHERE v.us_market_slug = s.us_market_slug AND v.outcome_basis IS NOT NULL) AS sig
    FROM slugs s),
newb AS (
  SELECT us_market_slug,
         string_agg(id::text || ':' || coalesce(buy_intent, '') || ':' || coalesce(outcome::text, '') || ':' || outcome_known::text || ':' || coalesce(outcome_basis, '') || ':' || coalesce(outcome_at::text, ''), '|' ORDER BY id) AS sig
    FROM external_valuations
   WHERE us_market_slug = ANY((SELECT array_agg(us_market_slug) FROM slugs)::text[]) AND outcome_basis IS NOT NULL
   GROUP BY 1)
SELECT count(*) AS contracts, count(*) FILTER (WHERE o.sig <> coalesce(n.sig, '')) AS contracts_differing,
       count(*) FILTER (WHERE o.sig = '') AS contracts_without_rows, sum(length(o.sig)) AS old_chars, sum(length(coalesce(n.sig, ''))) AS new_chars,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM old o LEFT JOIN newb n USING (us_market_slug);

\echo V4b the position set: POSITIONS_SQL grouping per account (what step_settle iterates) versus the all-account grouping B0 / B1 use; accounts on record
SELECT a.account_id,
       (SELECT count(*) FROM (SELECT group_id, us_market_slug, holding_side FROM paper_fills WHERE account_id = a.account_id GROUP BY 1, 2, 3) q) AS positions_sql_rows,
       (SELECT count(*) FROM (SELECT account_id, group_id, us_market_slug, holding_side FROM paper_fills GROUP BY 1, 2, 3, 4) q WHERE q.account_id = a.account_id) AS b0_rows,
       (SELECT count(*) FROM paper_accounts) AS paper_accounts_rows
  FROM (SELECT DISTINCT account_id FROM paper_fills) a ORDER BY 1;

\echo V4c Proof B's b3 rules applied to EVERY PAPER settlement ever recorded (a known-good population): any row flagged here is the rule's defect
WITH s AS (SELECT * FROM paper_settlements),
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
  SELECT s.settlement_id, s.recorded_at, s.us_market_slug, s.holding_side, s.outcome, s.version, s.qty,
         s.payout_per_contract AS per, s.payout_usd,
         CASE WHEN v.us_market_slug IS NULL THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET'
              WHEN (v.any_void::int + v.any_won::int + v.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
              WHEN v.any_void THEN 'VOID_REFUND' WHEN v.any_won THEN 'WON' WHEN v.any_lost THEN 'LOST'
              ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END AS verdict,
         coalesce(vp.venue_price_rows, 0) AS venue_price_rows, f.bought, f.sold, f.buy_gross,
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
SELECT count(*) AS settlements_all_time,
       count(*) FILTER (WHERE NOT (c_outcome AND c_per AND c_payout AND c_qty AND c_ledger)) AS flagged_by_b3_rules,
       count(*) FILTER (WHERE NOT c_outcome) AS outcome_mismatch, count(*) FILTER (WHERE NOT c_per) AS per_mismatch,
       count(*) FILTER (WHERE NOT c_payout) AS payout_mismatch, count(*) FILTER (WHERE NOT c_qty) AS qty_mismatch,
       count(*) FILTER (WHERE NOT c_ledger) AS ledger_missing, count(*) FILTER (WHERE version > 1) AS corrections,
       count(*) FILTER (WHERE outcome = 'SETTLED_AT_VENUE_PRICE') AS at_venue_price,
       count(*) FILTER (WHERE outcome = 'VOID_REFUND') AS void_refunds,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM judged;

\echo V4d the flagged settlements, if any (newest first, 20)
WITH s AS (SELECT * FROM paper_settlements),
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
SELECT to_char(s.recorded_at, 'MM-DD HH24:MI:SS') AS recorded, s.us_market_slug, s.holding_side, s.outcome, s.version,
       round(s.qty, 4) AS qty, round(s.payout_per_contract, 6) AS per, round(s.payout_usd, 4) AS payout_usd,
       CASE WHEN v.us_market_slug IS NULL THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET'
            WHEN (v.any_void::int + v.any_won::int + v.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
            WHEN v.any_void THEN 'VOID_REFUND' WHEN v.any_won THEN 'WON' WHEN v.any_lost THEN 'LOST'
            ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END AS verdict_now, coalesce(v.rows_n, 0) AS evidence_rows_now,
       CASE WHEN jsonb_typeof(s.evidence->'rows') = 'array' THEN jsonb_array_length(s.evidence->'rows') ELSE 0 END AS evidence_rows_named
  FROM s LEFT JOIN verd v ON v.us_market_slug = s.us_market_slug AND v.side = s.holding_side
 WHERE s.outcome IN ('WON', 'LOST', 'VOID_REFUND')
   AND s.outcome IS DISTINCT FROM (CASE WHEN v.us_market_slug IS NULL THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET'
            WHEN (v.any_void::int + v.any_won::int + v.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
            WHEN v.any_void THEN 'VOID_REFUND' WHEN v.any_won THEN 'WON' WHEN v.any_lost THEN 'LOST'
            ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END)
 ORDER BY s.recorded_at DESC LIMIT 20;

\echo V4e late-evidence sensitivity: the verdict from the rows the settlement itself names (evidence->rows valuation_id) versus from ALL current rows of the contract; contracts that gained contributing rows since
WITH s AS (
  SELECT settlement_id, us_market_slug, holding_side, outcome, version, evidence FROM paper_settlements
   WHERE outcome IN ('WON', 'LOST', 'VOID_REFUND')),
ev AS (
  SELECT s.settlement_id, CASE WHEN e->>'valuation_id' ~ '^[0-9]+$' THEN (e->>'valuation_id')::bigint END AS vid
    FROM s, jsonb_array_elements(CASE WHEN jsonb_typeof(s.evidence->'rows') = 'array' THEN s.evidence->'rows' ELSE '[]'::jsonb END) e),
named AS (
  SELECT s.settlement_id, count(v.id) AS rows_named,
         bool_or(v.outcome_basis = 'CONFIRMED_VOID') AS any_void,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND ((v.outcome = 1) = (v.buy_intent = CASE s.holding_side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_won,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND NOT ((v.outcome = 1) = (v.buy_intent = CASE s.holding_side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_lost
    FROM s LEFT JOIN ev ON ev.settlement_id = s.settlement_id LEFT JOIN external_valuations v ON v.id = ev.vid
   GROUP BY 1),
cur AS (
  SELECT v.us_market_slug, sd.side,
         count(*) FILTER (WHERE v.outcome_basis = 'CONFIRMED_VOID'
                             OR (v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1))) AS contributing_rows_now,
         bool_or(v.outcome_basis = 'CONFIRMED_VOID') AS any_void,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_won,
         bool_or(v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME') AND v.outcome_known AND v.outcome IN (0, 1)
                 AND NOT ((v.outcome = 1) = (v.buy_intent = CASE sd.side WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG' ELSE 'ORDER_INTENT_BUY_SHORT' END))) AS any_lost
    FROM external_valuations v CROSS JOIN (VALUES ('LONG'), ('SHORT')) AS sd(side)
   WHERE v.outcome_basis IS NOT NULL AND v.us_market_slug IN (SELECT us_market_slug FROM s)
   GROUP BY 1, 2),
j AS (
  SELECT s.settlement_id, s.outcome, n.rows_named, c.contributing_rows_now,
         CASE WHEN n.rows_named = 0 THEN 'NO_ROWS_NAMED'
              WHEN (n.any_void::int + n.any_won::int + n.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
              WHEN n.any_void THEN 'VOID_REFUND' WHEN n.any_won THEN 'WON' WHEN n.any_lost THEN 'LOST' ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END AS verdict_named,
         CASE WHEN c.us_market_slug IS NULL THEN 'NO_AUTHORITATIVE_SETTLEMENT_YET'
              WHEN (c.any_void::int + c.any_won::int + c.any_lost::int) > 1 THEN 'CONFLICTING_SETTLEMENT_EVIDENCE'
              WHEN c.any_void THEN 'VOID_REFUND' WHEN c.any_won THEN 'WON' WHEN c.any_lost THEN 'LOST' ELSE 'NO_AUTHORITATIVE_SETTLEMENT_YET' END AS verdict_now
    FROM s JOIN named n USING (settlement_id) LEFT JOIN cur c ON c.us_market_slug = s.us_market_slug AND c.side = s.holding_side)
SELECT count(*) AS settlements, count(*) FILTER (WHERE outcome <> verdict_named) AS mismatch_vs_named_rows,
       count(*) FILTER (WHERE outcome <> verdict_now) AS mismatch_vs_all_current_rows,
       count(*) FILTER (WHERE contributing_rows_now > rows_named) AS contracts_with_rows_added_since_settlement,
       count(*) FILTER (WHERE rows_named = 0) AS settlements_naming_no_rows,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM j;

\echo V4f Proof B's b4 rules applied to EVERY shadow counterfactual outcome ever recorded (known-good population): flagged = the rule's defect
WITH o AS (
  SELECT o.*, s.us_market_slug, s.holding_side, s.strategy, s.account_id
    FROM paper_shadow_counterfactual_outcomes o JOIN paper_shadow_counterfactuals s USING (shadow_id)),
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
  SELECT o.outcome_id, o.settled_at, o.us_market_slug, o.holding_side, o.outcome, o.payout_per_contract AS per,
         o.filled_qty, o.exec_cost_usd, o.exec_fees_usd, o.counterfactual_pnl_usd AS pnl,
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
SELECT count(*) AS shadow_outcomes_all_time,
       count(*) FILTER (WHERE NOT (c_outcome AND c_per AND c_pnl)) AS flagged_by_b4_rules,
       count(*) FILTER (WHERE NOT c_outcome) AS outcome_mismatch, count(*) FILTER (WHERE NOT c_per) AS per_mismatch,
       count(*) FILTER (WHERE NOT c_pnl) AS pnl_mismatch,
       count(*) FILTER (WHERE outcome = 'NO_FILL') AS no_fill, count(*) FILTER (WHERE outcome = 'WON') AS won,
       count(*) FILTER (WHERE outcome = 'LOST') AS lost, count(*) FILTER (WHERE outcome = 'VOID_REFUND') AS void_refund,
       count(*) FILTER (WHERE outcome = 'SETTLED_AT_VENUE_PRICE') AS at_venue_price,
       count(*) FILTER (WHERE ev_outcome IS NULL) AS evidence_without_settlement_outcome,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM judged;

\echo V4g PENDING shadows whose contract already carries a definite verdict (what the shadow settle step should settle on its next run): the shadow analogue of B2 would_settle_on_the_next_pass
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
       to_char(min(p.decided_at), 'MM-DD HH24:MI') AS oldest_pending,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM p LEFT JOIN verd v ON v.us_market_slug = p.us_market_slug AND v.side = p.holding_side LEFT JOIN vp ON vp.us_market_slug = p.us_market_slug;

\echo V5a the ring: unrecorded 60 s slots implied by the gaps, beats over 60 s, newest beat versus heartbeat_at and the newest attempt
WITH h AS (SELECT recent_heartbeats, heartbeat_at, passes, errors FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1),
ring AS (SELECT (b->>'at')::float8 AS at_s, b FROM h, jsonb_array_elements(h.recent_heartbeats) b),
g AS (SELECT at_s, b, at_s - lag(at_s) OVER (ORDER BY at_s) AS gap_s FROM ring)
SELECT (SELECT count(*) FROM g) AS beats,
       (SELECT coalesce(sum(greatest(round(gap_s / 60.0) - 1, 0)), 0) FROM g WHERE gap_s IS NOT NULL) AS implied_unrecorded_60s_slots,
       (SELECT count(*) FROM g WHERE (b->>'elapsed_s')::float8 > 60) AS beats_over_60s,
       (SELECT count(*) FROM g WHERE b->>'ok' <> 'true') AS not_ok_beats,
       (SELECT to_char(to_timestamp(max(at_s)), 'HH24:MI:SS') FROM g) AS newest_beat,
       (SELECT to_char(heartbeat_at, 'HH24:MI:SS') FROM h) AS heartbeat_at,
       (SELECT passes FROM h) AS passes, (SELECT errors FROM h) AS errors,
       (SELECT to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') FROM ingestion_state WHERE key = 'paper_session_last_pass') AS last_attempt_at,
       (SELECT value->>'ran' FROM ingestion_state WHERE key = 'paper_session_last_pass') AS last_attempt_ran,
       (SELECT value->>'refusal' FROM ingestion_state WHERE key = 'paper_session_last_pass') AS last_attempt_refusal;

\echo V5b the active session's cadence config (xavier_backstop_s, pass_budget_s) and status
SELECT session_id, status, to_char(started_at, 'YYYY-MM-DD HH24:MI') AS started, config->'cadence' AS cadence FROM paper_sessions ORDER BY started_at DESC LIMIT 2;

\echo V6a paper_orders: role x decision_id presence, all time (management roles are submitted with decision_id NULL; ENTRY never)
SELECT role, count(*) AS orders, count(decision_id) AS with_decision_id, count(*) - count(decision_id) AS without_decision_id,
       count(*) FILTER (WHERE filled_qty > 0) AS with_fills
  FROM paper_orders GROUP BY 1 ORDER BY 1;

\echo V6b venue-side and desk tables Proof D6 does not name: rows all time and newest (bettor_desk is the SHADOW desk ledger per migration 094)
SELECT 'bettor_desk_fills' AS tbl, count(*) AS rows_all, NULL::text AS newest FROM bettor_desk_fills
UNION ALL SELECT 'bettor_desk_order_events', count(*), NULL FROM bettor_desk_order_events
UNION ALL SELECT 'bettor_standing_order_plans', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI:SS') FROM bettor_standing_order_plans
UNION ALL SELECT 'bettor_standing_order_events', count(*), to_char(max(recorded_at), 'YYYY-MM-DD HH24:MI:SS') FROM bettor_standing_order_events
UNION ALL SELECT 'execution_intents', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI:SS') FROM execution_intents
UNION ALL SELECT 'execution_intents [actual_mirror_id set]', count(*) FILTER (WHERE actual_mirror_id IS NOT NULL), to_char(max(created_at) FILTER (WHERE actual_mirror_id IS NOT NULL), 'YYYY-MM-DD HH24:MI:SS') FROM execution_intents
UNION ALL SELECT 'agent_handoff_fills', count(*), to_char(max(recorded_at), 'YYYY-MM-DD HH24:MI:SS') FROM agent_handoff_fills
ORDER BY 1;

\echo V7 PAPER execution and decision history kept outside paper%: rows by adapter / mode, and whether the tables carry an enabled before-row upd+del trigger
SELECT adapter, mode, state, count(*) AS rows_n, to_char(max(created_at), 'YYYY-MM-DD HH24:MI:SS') AS newest
  FROM canonical_intent_executions GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
SELECT 'canonical_decision_intents' AS tbl, count(*) AS rows_n, to_char(max(created_at), 'YYYY-MM-DD HH24:MI:SS') AS newest FROM canonical_decision_intents
UNION ALL SELECT 'canonical_management_intents', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI:SS') FROM canonical_management_intents
UNION ALL SELECT 'live_parity_ledger', count(*), to_char(max(created_at), 'YYYY-MM-DD HH24:MI:SS') FROM live_parity_ledger
UNION ALL SELECT 'xavier_value_add', count(*), to_char(max(computed_at), 'YYYY-MM-DD HH24:MI:SS') FROM xavier_value_add;
SELECT c.relname,
       count(t.oid) FILTER (WHERE NOT t.tgisinternal AND t.tgenabled <> 'D' AND (t.tgtype & 2) <> 0 AND (t.tgtype & 16) <> 0 AND (t.tgtype & 8) <> 0) AS before_upd_del_triggers,
       count(t.oid) FILTER (WHERE NOT t.tgisinternal AND t.tgenabled <> 'D' AND (t.tgtype & 2) <> 0 AND (t.tgtype & 16) <> 0) AS before_upd_triggers
  FROM pg_class c LEFT JOIN pg_trigger t ON t.tgrelid = c.oid
 WHERE c.relname IN ('canonical_intent_executions', 'canonical_decision_intents', 'canonical_management_intents', 'live_parity_ledger', 'xavier_value_add', 'xavier_entry_theses', 'external_valuations')
 GROUP BY 1 ORDER BY 1;

\echo V8 settlements whose position key joins no fills-derived position (must be 0: such a row drops out of F6a and of B0's open quantity)
SELECT count(*) AS settlements,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM paper_fills f
                                           WHERE 'paperpos:' || f.account_id || ':' || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side = s.position_key)) AS unjoined,
       count(DISTINCT settlement_event_key) AS distinct_event_key_patterns,
       count(*) FILTER (WHERE settlement_event_key <> 'venue-final:' || us_market_slug) AS event_key_not_venue_final,
       round(extract(epoch FROM clock_timestamp() - statement_timestamp())::numeric, 2) AS took_s
  FROM paper_settlements s;
