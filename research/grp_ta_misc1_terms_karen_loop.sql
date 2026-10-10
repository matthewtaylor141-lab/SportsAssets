-- READ-ONLY. Root-cause audit, group truth-agents, part 4: (a) the Kalshi
-- contract-terms registry and what the soccer series state about a
-- postponement, (b) the census source: paper books by slug family, (c) the
-- terms the registry holds for the supported Polymarket US families whether
-- or not a book was recorded, (d) Karen's challenges and the twin Karen rows,
-- (e) the mirror_shadow loop record. SELECT only.

\echo == 1 kalshi_contract_terms state: series to rulebook, and each rulebook status (no PDF bytes)
SELECT s.key AS series, s.value AS rulebook
  FROM ingestion_state i, LATERAL jsonb_each_text(coalesce(i.value::jsonb -> 'series', '{}'::jsonb)) AS s(key, value)
 WHERE i.key = 'kalshi_contract_terms' AND s.key IN ('KXEPLGAME','KXEPL1H','KXEPL2H','KXMLSGAME','KXBUNDESLIGAGAME','KXNHLGAME','KXNBAGAME','KXNFLGAME','KXMLBGAME','KXNHL1PSPREAD')
 ORDER BY 1;
SELECT r.key AS rulebook, r.value ->> 'status' AS status, to_timestamp((r.value ->> 'fetched_at')::numeric) AS fetched_at,
       left(r.value ->> 'sha256', 12) AS sha12
  FROM ingestion_state i, LATERAL jsonb_each(coalesce(i.value::jsonb -> 'rulebooks', '{}'::jsonb)) AS r(key, value)
 WHERE i.key = 'kalshi_contract_terms' ORDER BY 1;

\echo == 2 the sentences about postponement, cancellation or suspension in the soccer market texts (one ticker per series)
WITH t AS (
  SELECT DISTINCT ON (split_part(contract_id, '-', 1)) contract_id, rules_text, rules_secondary
    FROM market_plane_rules
   WHERE contract_id IN (SELECT 'kalshi:' || f.team_tickers[1] FROM kalshi_fixtures_current f
                          WHERE f.series_ticker IN ('KXEPLGAME','KXEPL1H','KXEPL2H','KXMLSGAME','KXMLS1H','KXMLS2H','KXBUNDESLIGAGAME')
                            AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours')
   ORDER BY split_part(contract_id, '-', 1), contract_id)
SELECT contract_id,
       (SELECT string_agg(x.m[1], ' | ') FROM regexp_matches(coalesce(rules_text, '') || ' ' || coalesce(rules_secondary, ''),
            '([^.]*(postpone|cancel|suspend|abandon|not (completed|played|started))[^.]*\.)', 'gi') AS x(m)) AS sentences,
       length(coalesce(rules_secondary, '')) AS secondary_len
  FROM t ORDER BY 1;

\echo == 3 paper book observations in the last 900 s and 1 h by slug family
SELECT split_part(us_market_slug, '-', 1) AS family, count(*) FILTER (WHERE observed_at > now() - interval '900 seconds') AS rows_900s,
       count(*) FILTER (WHERE observed_at > now() - interval '3600 seconds') AS rows_1h, max(observed_at) AS newest
  FROM paper_book_observations WHERE observed_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 3 DESC LIMIT 12;
SELECT date_trunc('hour', observed_at) AS hr, count(*) AS tsc_asc_rows, count(DISTINCT us_market_slug) AS slugs
  FROM paper_book_observations
 WHERE observed_at > now() - interval '30 hours' AND split_part(us_market_slug, '-', 1) IN ('tsc', 'asc')
 GROUP BY 1 ORDER BY 1 DESC LIMIT 32;

\echo == 4 the rules the registry holds for Polymarket US contracts by family (a book is not needed to read terms)
SELECT split_part(contract_id, '-', 1) AS family, parse_status, count(*) AS contracts,
       count(*) FILTER (WHERE evidence -> 'settlement' ->> 'void_rule' IS NOT NULL
                          AND evidence -> 'settlement' ->> 'postponement_payout' IS NOT NULL
                          AND evidence -> 'settlement' ->> 'postponement_window_hours' IS NOT NULL) AS terms_complete,
       max(observed_at) AS newest_capture
  FROM market_plane_rules WHERE venue = 'POLYMARKET_US'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo == 5 karen challenges by detector and blocked
SELECT detector, target_agent, target_kind, blocked, count(*) AS n, min(challenged_at) AS first_at, max(challenged_at) AS last_at
  FROM karen_challenges GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 12;

\echo == 6 twin Karen rows: the newest run and the 8 runs before it (counterfactual metrics)
SELECT run_id, metric, book, status, reason, sample_n, to_timestamp(extract(epoch FROM computed_at)) AS computed_at
  FROM twin_agent_scorecards
 WHERE agent = 'KAREN' AND metric IN ('loss_avoided_paper_basis', 'profit_sacrificed_paper_basis', 'loss_avoided_actual_basis', 'profit_sacrificed_actual_basis')
 ORDER BY computed_at DESC, metric LIMIT 16;
SELECT status, reason, count(*) AS n, min(computed_at) AS first_at, max(computed_at) AS last_at
  FROM twin_agent_scorecards WHERE agent = 'KAREN' AND book = 'COUNTERFACTUAL' GROUP BY 1, 2 ORDER BY 3 DESC;

\echo == 7 runtime_loop_health for mirror_shadow and the other workers loops that read UNHEALTHY
SELECT loop_name, process, starts, successes, errors, last_start_at, last_success_at, last_error_at, left(coalesce(last_error, ''), 200) AS last_error
  FROM runtime_loop_health WHERE loop_name IN ('mirror_shadow', 'kalshi_market_data', 'reconciler', 'analytics') ORDER BY 1, 2;

\echo == 8 mirror_shadow: rows written per hour by the shadow lane (it keeps planning on the ledger source)
SELECT date_trunc('hour', at) AS hr, count(*) AS rows FROM mirror_shadow
 WHERE at > now() - interval '12 hours' GROUP BY 1 ORDER BY 1 DESC LIMIT 12;
