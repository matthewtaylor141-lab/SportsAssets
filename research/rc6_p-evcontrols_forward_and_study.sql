-- READ-ONLY. RC6.2 lane p-evcontrols (EVIDENCE side of the EV category's
-- four failing red-team controls). SELECT only.
--  DIGITAL_TWIN      the twin population (terminal IOC/FOK ENTRY/EXIT/REDUCE
--                    paper orders eligible after 2026-10-07T14:56:26Z, epoch
--                    1791384986) per UTC day and its current rate; every
--                    order since then the twin does NOT read, by role / tif
--                    / type / terminal / state (is a member excluded?)
--  SAMPLE_INTEGRITY  the registry rows (expected 0 before RC6.1) and the
--  MULTIPLE_TESTING  keyed-hash partition RC6.1 would register over
--                    DISTINCT paper_fills.fixture; the study's input
--                    (intel.attribution PAPER rows, 60 d, 5000 newest ENTER
--                    decisions) rebuilt per UTC decided day x strategy x
--                    slice, so PBO / DSR can be forecast offline
--  CAPACITY          the bind's evaluations, 14 d: positive-EV and
--                    fill-probability >= 0.80 counts per size bucket

\echo == 0 read instant
SELECT now() AS read_at, extract(epoch FROM now()) AS read_epoch,
       round((extract(epoch FROM now()) - 1791384986) / 3600.0, 2) AS hours_since_twin_window_end;

\echo == 1 DIGITAL_TWIN population per UTC day (the twin own filter)
SELECT (eligible_at AT TIME ZONE 'UTC')::date AS d, role, time_in_force AS tif,
       state, count(*) AS n
  FROM paper_orders
 WHERE time_in_force IN ('IOC', 'FOK') AND terminal_at IS NOT NULL
   AND role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND eligible_at > to_timestamp(1791384986)
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

\echo == 2 DIGITAL_TWIN population: totals and rate windows
SELECT count(*) AS since_window,
       count(*) FILTER (WHERE eligible_at > now() - interval '24 hours') AS last_24h,
       count(*) FILTER (WHERE eligible_at > now() - interval '72 hours') AS last_72h,
       count(*) FILTER (WHERE eligible_at > now() - interval '7 days') AS last_7d,
       min(eligible_at) AS first_eligible, max(eligible_at) AS last_eligible
  FROM paper_orders
 WHERE time_in_force IN ('IOC', 'FOK') AND terminal_at IS NOT NULL
   AND role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND eligible_at > to_timestamp(1791384986);

\echo == 3 every paper order eligible since the window, twin member or not
SELECT role, time_in_force AS tif, order_type, (terminal_at IS NOT NULL) AS terminal,
       state,
       (time_in_force IN ('IOC', 'FOK') AND terminal_at IS NOT NULL
        AND role IN ('ENTRY', 'EXIT', 'REDUCE')) AS twin_member,
       count(*) AS n, min(eligible_at) AS first_at, max(eligible_at) AS last_at
  FROM paper_orders
 WHERE eligible_at > to_timestamp(1791384986)
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 6 DESC, 1, 2, 3, 4, 5;

\echo == 4 marketable (IOC/FOK) orders per UTC day since 2026-09-20, all roles (the historical production rate)
SELECT (eligible_at AT TIME ZONE 'UTC')::date AS d,
       count(*) FILTER (WHERE role = 'ENTRY') AS entry_n,
       count(*) FILTER (WHERE role = 'EXIT') AS exit_n,
       count(*) FILTER (WHERE role = 'REDUCE') AS reduce_n,
       count(*) FILTER (WHERE role = 'HEDGE') AS hedge_n,
       count(*) FILTER (WHERE role = 'STANDING_PROTECTION') AS protection_n,
       count(*) AS all_n
  FROM paper_orders
 WHERE time_in_force IN ('IOC', 'FOK') AND eligible_at >= '2026-09-20'
 GROUP BY 1 ORDER BY 1;

\echo == 5 open PAPER position groups now (the source of EXIT / REDUCE orders), by strategy
WITH q AS (
  SELECT f.group_id,
         sum(CASE WHEN f.direction = 'BUY' THEN f.qty ELSE -f.qty END) AS net
    FROM paper_fills f
   WHERE f.account_id = 'paper_acct_main'
     AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = f.group_id)
   GROUP BY f.group_id),
st AS (
  SELECT DISTINCT ON (o.group_id) o.group_id, coalesce(d.strategy, '-') AS strategy
    FROM paper_orders o LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
   WHERE o.role = 'ENTRY' ORDER BY o.group_id, o.created_at)
SELECT coalesce(st.strategy, '-') AS strategy, count(*) AS open_groups
  FROM q LEFT JOIN st ON st.group_id = q.group_id
 WHERE q.net > 1e-9 GROUP BY 1 ORDER BY 1;

\echo == 6 ENTER decisions and ENTRY orders per UTC day since 2026-10-01, by strategy
SELECT (d.decided_at AT TIME ZONE 'UTC')::date AS d, coalesce(d.strategy, '-') AS strategy,
       count(*) AS enter_decisions,
       count(o.order_id) AS with_entry_order
  FROM paper_decisions d
  LEFT JOIN LATERAL (SELECT x.order_id FROM paper_orders x
                      WHERE x.decision_id = d.decision_id AND x.role = 'ENTRY'
                      LIMIT 1) o ON true
 WHERE d.verdict = 'ENTER' AND d.decided_at >= '2026-10-01'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 7 strategy lifecycle: the latest event per strategy
SELECT DISTINCT ON (strategy) strategy, from_state, to_state, rule_id, actor, recorded_at
  FROM paper_strategy_lifecycle_events
 ORDER BY strategy, recorded_at DESC;

\echo == 8 the research registry today (production at 732cc0c6 has no writer)
SELECT to_regclass('red_team_holdout_registry') IS NOT NULL AS table_present;
SELECT kind, study, count(*) AS n, max(candidate_count) AS max_candidates,
       min(at) AS first_at, max(at) AS last_at
  FROM red_team_holdout_registry GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 9 the keyed-hash partition RC6.1 would register: DISTINCT paper_fills.fixture by slice
WITH ev AS (SELECT DISTINCT fixture FROM paper_fills WHERE fixture IS NOT NULL),
u AS (
  SELECT fixture,
         ('x' || substr(encode(sha256(convert_to(
             'BETTOR_PAPER_STRATEGY_SELECTION_V1|' || fixture, 'UTF8')), 'hex'), 1, 8))
           ::bit(32)::bigint / 4294967296.0 AS u
    FROM ev)
SELECT CASE WHEN u < 0.6 THEN 'train' WHEN u < 0.8 THEN 'test' ELSE 'holdout' END AS slice,
       count(*) AS events
  FROM u GROUP BY 1 ORDER BY 1;

\echo == 10 the study population size: ENTER decisions (paper_acct_main) in 60 d vs the reader 5000-row bound
SELECT count(*) AS enter_decisions_60d,
       min(decided_at) AS oldest, max(decided_at) AS newest
  FROM paper_decisions
 WHERE verdict = 'ENTER' AND decided_at >= now() - interval '60 days'
   AND account_id = 'paper_acct_main';

\echo == 11 the study input rebuilt (intel.attribution.load_paper + research_registry.measure): per UTC decided day x strategy x slice
WITH d AS (
  SELECT decision_id, decided_at, coalesce(strategy, 'DEREK_ENTRY_POLICY_V2') AS strategy,
         us_market_slug, holding_side::text AS holding_side, valuation_id,
         coalesce(p_blended, p_pinnacle, p_internal) AS p,
         coalesce((economics->'acquisition'->>'vwap')::float8,
                  (economics->'levels'->0->>'price')::float8,
                  limit_price::float8) AS dprice
    FROM paper_decisions
   WHERE verdict = 'ENTER' AND decided_at >= now() - interval '60 days'
     AND account_id = 'paper_acct_main'
   ORDER BY decided_at DESC LIMIT 5000),
g AS (
  SELECT DISTINCT ON (o.decision_id) o.decision_id, o.group_id
    FROM paper_orders o
   WHERE o.decision_id IN (SELECT decision_id FROM d) AND o.role = 'ENTRY'
   ORDER BY o.decision_id, o.created_at),
f AS (
  SELECT pf.group_id, pf.role, pf.direction, pf.holding_side::text AS holding_side,
         pf.us_market_slug, pf.qty::float8 AS qty, pf.price::float8 AS price,
         coalesce(pf.fee_usd, 0)::float8 AS fee, coalesce(pf.gross_usd, 0)::float8 AS gross,
         pf.fixture
    FROM paper_fills pf WHERE pf.group_id IN (SELECT group_id FROM g)),
fx AS (SELECT group_id, max(fixture) AS fixture FROM f GROUP BY 1),
s AS (
  SELECT DISTINCT ON (position_key) group_id, us_market_slug,
         holding_side::text AS holding_side, payout_per_contract::float8 AS pay
    FROM paper_settlements WHERE group_id IN (SELECT group_id FROM g)
   ORDER BY position_key, version DESC),
e AS (
  SELECT d.decision_id, d.decided_at, d.strategy, d.p, d.dprice, d.valuation_id,
         g.group_id,
         coalesce((SELECT f.us_market_slug FROM f WHERE f.group_id = g.group_id
                     AND f.role = 'ENTRY' AND f.direction = 'BUY' LIMIT 1),
                  d.us_market_slug) AS slug,
         coalesce((SELECT f.holding_side FROM f WHERE f.group_id = g.group_id
                     AND f.role = 'ENTRY' AND f.direction = 'BUY' LIMIT 1),
                  d.holding_side) AS side
    FROM d JOIN g ON g.decision_id = d.decision_id),
r AS (
  SELECT e.*,
         (SELECT sum(f.qty) FROM f WHERE f.group_id = e.group_id
             AND f.role = 'ENTRY' AND f.direction = 'BUY') AS q,
         (SELECT sum(f.qty * f.price) FROM f WHERE f.group_id = e.group_id
             AND f.role = 'ENTRY' AND f.direction = 'BUY') AS cost,
         (SELECT sum(f.fee) FROM f WHERE f.group_id = e.group_id
             AND f.role = 'ENTRY' AND f.direction = 'BUY') AS fees,
         (SELECT sum(f.qty) FROM f WHERE f.group_id = e.group_id
             AND f.direction = 'SELL' AND f.us_market_slug = e.slug
             AND f.holding_side = e.side) AS sold,
         (SELECT sum(f.qty * f.price - f.fee) FROM f WHERE f.group_id = e.group_id
             AND f.direction = 'SELL' AND f.us_market_slug = e.slug
             AND f.holding_side = e.side) AS sell_net,
         (SELECT sum(f.qty * s2.pay - (f.gross + f.fee)) FROM f
            LEFT JOIN s s2 ON s2.group_id = f.group_id AND s2.us_market_slug = f.us_market_slug
                          AND s2.holding_side = f.holding_side
           WHERE f.group_id = e.group_id AND f.role = 'HEDGE' AND f.direction = 'BUY') AS hedge_net,
         (SELECT count(*) FROM f
            LEFT JOIN s s2 ON s2.group_id = f.group_id AND s2.us_market_slug = f.us_market_slug
                          AND s2.holding_side = f.holding_side
           WHERE f.group_id = e.group_id AND f.role = 'HEDGE' AND f.direction = 'BUY'
             AND s2.pay IS NULL) AS hedge_unsettled,
         coalesce((SELECT s1.pay FROM s s1 WHERE s1.group_id = e.group_id
                     AND s1.us_market_slug = e.slug AND s1.holding_side = e.side LIMIT 1),
                  (SELECT v.outcome::float8 FROM external_valuations v
                    WHERE v.id = e.valuation_id AND v.outcome_known AND v.outcome IN (0, 1)
                      AND v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'))) AS payoff,
         coalesce((SELECT x.fixture FROM fx x WHERE x.group_id = e.group_id), e.slug) AS ev
    FROM e),
k AS (
  SELECT r.*,
         (q > 0 AND payoff IS NOT NULL AND p IS NOT NULL AND dprice IS NOT NULL
          AND coalesce(hedge_unsettled, 0) = 0) AS claimed,
         (coalesce(q, 0) - coalesce(sold, 0)) * payoff + coalesce(sell_net, 0)
           - coalesce(cost, 0) - coalesce(fees, 0) + coalesce(hedge_net, 0) AS realized,
         ('x' || substr(encode(sha256(convert_to(
             'BETTOR_PAPER_STRATEGY_SELECTION_V1|' || ev, 'UTF8')), 'hex'), 1, 8))
           ::bit(32)::bigint / 4294967296.0 AS u
    FROM r)
SELECT (decided_at AT TIME ZONE 'UTC')::date AS d, strategy,
       CASE WHEN u < 0.6 THEN 'train' WHEN u < 0.8 THEN 'test' ELSE 'holdout' END AS slice,
       count(*) AS positions, count(*) FILTER (WHERE claimed) AS claimed,
       count(DISTINCT ev) FILTER (WHERE claimed) AS events,
       round(coalesce(sum(realized) FILTER (WHERE claimed), 0)::numeric, 4) AS pnl
  FROM k GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo == 12 CAPACITY: the bind evaluations, 14 d, per size bucket (positive lower-bound inputs)
SELECT CASE WHEN qty_in <= 10 THEN '001-010' WHEN qty_in <= 50 THEN '011-050'
            WHEN qty_in <= 100 THEN '051-100' WHEN qty_in <= 500 THEN '101-500'
            ELSE '501+' END AS bucket,
       count(*) AS evaluations,
       count(DISTINCT coalesce(strategy, '-') || '|' || us_market_slug || '|' || holding_side) AS opportunities,
       count(*) FILTER (WHERE ev_per_contract_usd > 0) AS ev_positive,
       count(*) FILTER (WHERE fill_probability >= 0.8) AS fp_ge_080,
       count(*) FILTER (WHERE ev_per_contract_usd > 0 AND fill_probability >= 0.8
                          AND all_in_ev_usd > 0) AS eligible_rule_rows,
       round(max(ev_per_contract_usd)::numeric, 5) AS max_ev_pc,
       round(avg(fill_probability)::numeric, 4) AS mean_fp,
       min(evaluated_at) AS first_at, max(evaluated_at) AS last_at
  FROM paper_profitability_evaluations
 WHERE evaluated_at > now() - interval '14 days' AND qty_in IS NOT NULL
   AND ev_per_contract_usd IS NOT NULL
 GROUP BY 1 ORDER BY 1;

\echo == 13 CAPACITY: evaluations per UTC day, 14 d (is the bind still evaluating?)
SELECT (evaluated_at AT TIME ZONE 'UTC')::date AS d, stage, verdict, count(*) AS n,
       count(*) FILTER (WHERE ev_per_contract_usd > 0) AS ev_positive
  FROM paper_profitability_evaluations
 WHERE evaluated_at > now() - interval '14 days'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
