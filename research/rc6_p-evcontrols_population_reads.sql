-- READ-ONLY. RC6.2 lane p-evcontrols (FIX stage). SELECT only.
-- The populations three evidence reads must hold WHOLE, measured today:
--  A  intel.attribution.load_paper (ATTRIBUTION, PROFIT_BREAKERS, the
--     MULTIPLE_TESTING study, the forward scoreboard): ENTER decisions WITH
--     an ENTRY paper order (positions) in paper_acct_main, all time and 60 d,
--     against the ENTER decisions without one that crowd its 5,000 bound
--  B  completion.evidence.read_ev (executable_ev): ENTER decisions with a
--     book in its declared 7-day window, against its old 2,000 bound
--  C  completion.evidence.read_twin (DIGITAL_TWIN): fresh twin orders
--     (eligible after 2026-10-07T14:56:26Z, epoch 1791384986) vs its 800 bound
--  D  the table sizes an unwindowed positions read depends on

\echo == 0 read instant
SELECT now() AS read_at, extract(epoch FROM now()) AS read_epoch;

\echo == A1 positions (ENTER decisions with an ENTRY order), paper_acct_main
SELECT count(*) AS positions_all_time,
       count(*) FILTER (WHERE d.decided_at >= now() - interval '60 days')
         AS positions_60d,
       min(d.decided_at) AS oldest, max(d.decided_at) AS newest
  FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.account_id = 'paper_acct_main'
   AND EXISTS (SELECT 1 FROM paper_orders o
                WHERE o.decision_id = d.decision_id AND o.role = 'ENTRY');

\echo == A2 ENTER decisions, paper_acct_main: with / without an ENTRY order; the 5,000 newest of 60 d (the old bound)
WITH e AS (
  SELECT d.decision_id, d.decided_at,
         EXISTS (SELECT 1 FROM paper_orders o
                  WHERE o.decision_id = d.decision_id
                    AND o.role = 'ENTRY') AS has_order
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.account_id = 'paper_acct_main'),
newest AS (SELECT * FROM e
            WHERE decided_at >= now() - interval '60 days'
            ORDER BY decided_at DESC LIMIT 5000)
SELECT (SELECT count(*) FROM e) AS enter_all_time,
       (SELECT count(*) FROM e WHERE NOT has_order) AS orderless_all_time,
       (SELECT count(*) FROM e
         WHERE decided_at >= now() - interval '60 days') AS enter_60d,
       (SELECT count(*) FROM newest) AS newest_5000_read,
       (SELECT count(*) FROM newest WHERE has_order) AS positions_in_newest_5000,
       (SELECT min(decided_at) FROM newest) AS newest_5000_reach_back_to;

\echo == A3 ENTER decisions per UTC day x strategy since 2026-10-06, with / without an ENTRY order (the crowding rate)
SELECT (d.decided_at AT TIME ZONE 'UTC')::date AS day, d.strategy,
       count(*) AS enter_decisions,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM paper_orders o
          WHERE o.decision_id = d.decision_id
            AND o.role = 'ENTRY')) AS with_entry_order
  FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.account_id = 'paper_acct_main'
   AND d.decided_at >= '2026-10-06'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == B1 executable EV population: ENTER decisions with a book in 7 d (all accounts, as read_ev reads)
SELECT count(*) AS population_in_window_7d, min(decided_at) AS oldest,
       max(decided_at) AS newest, count(DISTINCT strategy) AS strategies
  FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
   AND d.decided_at > now() - make_interval(days => 7);

\echo == B2 the 2,000 newest of them (the old bound): how far back, which strategies
WITH n AS (SELECT decided_at, strategy FROM paper_decisions d
            WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
              AND d.decided_at > now() - make_interval(days => 7)
            ORDER BY decided_at DESC LIMIT 2000)
SELECT count(*) AS read, min(decided_at) AS reach_back_to,
       string_agg(DISTINCT strategy, ',') AS strategies
  FROM n;

\echo == B3 executable EV population per UTC day x strategy, 7 d
SELECT (decided_at AT TIME ZONE 'UTC')::date AS day, strategy, count(*) AS n
  FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
   AND d.decided_at > now() - make_interval(days => 7)
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == C1 the twin population vs its 800 bound
SELECT count(*) AS twin_population, min(eligible_at) AS oldest,
       max(eligible_at) AS newest
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp(1791384986);

\echo == D1 table sizes (planner statistics)
SELECT relname, reltuples::bigint AS est_rows,
       pg_size_pretty(pg_total_relation_size(oid)) AS total_size
  FROM pg_class
 WHERE relname IN ('paper_decisions', 'paper_orders', 'paper_fills',
                   'paper_settlements', 'paper_book_observations')
   AND relkind = 'r'
 ORDER BY relname;

\echo == D2 paper_decisions by verdict, paper_orders by role (exact)
SELECT 'paper_decisions' AS tbl, verdict AS k, count(*) AS n,
       min(decided_at) AS oldest
  FROM paper_decisions GROUP BY 1, 2
UNION ALL
SELECT 'paper_orders', role, count(*), min(created_at)
  FROM paper_orders GROUP BY 1, 2
 ORDER BY 1, 2;
