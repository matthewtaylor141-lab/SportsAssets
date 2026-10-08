-- ECONOMIC FUNNEL (owner directive section 6), STEP 1b: THE JSON SHAPES
-- THE FUNNEL READS (pre-outcome economics on entry decisions, the bind's
-- attribution terms, the counterfactual variants' detail).
--
-- READ-ONLY. Every statement is a SELECT. Run through research-sql.yml.
-- Key names only and their frequencies -- no values are summed here.

\echo '== 1b.1 paper_decisions.economics top-level keys on ENTER decisions, per strategy'
SELECT d.strategy, k.key, count(*) AS decisions
  FROM paper_decisions d
  CROSS JOIN LATERAL jsonb_object_keys(
       CASE WHEN jsonb_typeof(d.economics) = 'object' THEN d.economics
            ELSE '{}'::jsonb END) AS k(key)
 WHERE d.verdict = 'ENTER'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 1b.2 paper_decisions.economics -> headline keys (ENTER), per strategy'
SELECT d.strategy, k.key, count(*) AS decisions
  FROM paper_decisions d
  CROSS JOIN LATERAL jsonb_object_keys(
       CASE WHEN jsonb_typeof(d.economics -> 'headline') = 'object'
            THEN d.economics -> 'headline' ELSE '{}'::jsonb END) AS k(key)
 WHERE d.verdict = 'ENTER'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 1b.3 one ENTER decision per strategy x policy_version: the economics document'
SELECT DISTINCT ON (d.strategy, d.policy_version) d.strategy, d.policy_version,
       d.decision_id, d.proposed_qty, d.limit_price, d.p_internal, d.p_pinnacle,
       d.p_blended, left(d.economics::text, 1800) AS economics
  FROM paper_decisions d
 WHERE d.verdict = 'ENTER'
 ORDER BY d.strategy, d.policy_version, d.decided_at DESC;

\echo '== 1b.4 paper_profitability_evaluations.detail -> attribution -> terms keys'
SELECT e.strategy, k.key, count(*) AS rows
  FROM paper_profitability_evaluations e
  CROSS JOIN LATERAL jsonb_object_keys(
       CASE WHEN jsonb_typeof(e.detail -> 'attribution' -> 'terms') = 'object'
            THEN e.detail -> 'attribution' -> 'terms' ELSE '{}'::jsonb END) AS k(key)
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 1b.5 paper_profitability_evaluations.detail -> bind keys'
SELECT k.key, count(*) AS rows
  FROM paper_profitability_evaluations e
  CROSS JOIN LATERAL jsonb_object_keys(
       CASE WHEN jsonb_typeof(e.detail -> 'bind') = 'object'
            THEN e.detail -> 'bind' ELSE '{}'::jsonb END) AS k(key)
 GROUP BY 1 ORDER BY 1;

\echo '== 1b.6 one evaluation document (the newest), detail trimmed'
SELECT e.eval_id, e.strategy, e.us_market_slug, e.holding_side, e.fixture,
       e.p_raw, e.p_used, e.market_price, e.qty_in, e.qty_out,
       e.all_in_ev_usd, e.fill_probability, e.expected_hold_hours,
       left(e.detail::text, 3500) AS detail
  FROM paper_profitability_evaluations e
 ORDER BY e.eval_id DESC LIMIT 1;

\echo '== 1b.7 paper_orders.label keys on ENTRY BUY orders that filled, per strategy'
SELECT o.strategy, k.key, count(*) AS orders
  FROM paper_orders o
  CROSS JOIN LATERAL jsonb_object_keys(
       CASE WHEN jsonb_typeof(o.label) = 'object' THEN o.label
            ELSE '{}'::jsonb END) AS k(key)
 WHERE o.role = 'ENTRY' AND o.filled_qty > 0
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 1b.8 does every filled ENTRY order name a decision that exists?'
SELECT o.strategy, count(*) AS filled_entry_orders,
       count(o.decision_id) AS with_decision_id,
       count(d.decision_id) AS decision_found,
       count(*) FILTER (WHERE d.economics IS NOT NULL) AS with_economics
  FROM paper_orders o
  LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
 WHERE o.role = 'ENTRY' AND o.filled_qty > 0
 GROUP BY 1 ORDER BY 1;

\echo '== 1b.9 fixture present on fills?'
SELECT strategy, count(*) AS fills, count(fixture) AS with_fixture,
       count(DISTINCT fixture) AS fixtures
  FROM paper_fills GROUP BY 1 ORDER BY 1;
