-- RC6.3c PAPER proof PROBE (SELECT only): the production schema facts the five proof readbacks are written against --
-- every paper table with its estimated rows and size, its timestamp columns and primary key, its triggers, the venue /
-- control tables present and their columns, external_valuations size and indexes, the paper_session_health rows, and
-- the ingestion_state keys of interest. Nothing here is a verdict; it sizes and names what the proofs read.
\echo Q1 paper tables: estimated rows and total size
SELECT c.relname, c.reltuples::bigint AS est_rows, pg_total_relation_size(c.oid) AS total_bytes, pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname LIKE 'paper\_%'
 ORDER BY c.relname;
\echo Q2 paper tables: timestamp columns and primary key
SELECT c.table_name,
       string_agg(c.column_name, ',' ORDER BY c.ordinal_position) FILTER (WHERE c.data_type LIKE 'timestamp%') AS ts_cols,
       (SELECT string_agg(kcu.column_name, ',' ORDER BY kcu.ordinal_position)
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema AND kcu.table_name = tc.table_name
         WHERE tc.table_schema = 'public' AND tc.table_name = c.table_name AND tc.constraint_type = 'PRIMARY KEY') AS pk,
       count(*) AS cols
  FROM information_schema.columns c
 WHERE c.table_schema = 'public' AND c.table_name LIKE 'paper\_%'
 GROUP BY c.table_name ORDER BY 1;
\echo Q3 triggers on paper tables (event list per trigger)
SELECT event_object_table AS tbl, trigger_name, string_agg(event_manipulation, ',' ORDER BY event_manipulation) AS events, action_timing
  FROM information_schema.triggers
 WHERE event_object_schema = 'public' AND event_object_table LIKE 'paper\_%'
 GROUP BY 1, 2, 4 ORDER BY 1, 2;
\echo Q4 venue and control tables: present
SELECT t, to_regclass(t) IS NOT NULL AS present
  FROM unnest(ARRAY['execmirror_orders','execmirror_fills','execmirror_control','kalshi_live_intents','kalshi_live_fills','kalshi_live_events','kalshi_smalllive_control','small_live_order_events','small_live_control','small_live_control_events','live_approvals','canonical_intent_executions','execution_intents','bettor_funded_intents','bettor_funded_fills','mirror_orders','bettor_desk_orders','live_orders','red_team_control_receipts','paper_control','agent_status','adriana_arb_scans','adriana_arb_opportunities','smalllive_handoffs','runtime_loop_health','service_heartbeats','external_valuations','canonical_decision_intents','xavier_entry_theses','agent_runs']) AS t;
\echo Q5 columns of the venue order, fill and control tables
SELECT table_name, string_agg(column_name || ':' || data_type, ', ' ORDER BY ordinal_position) AS cols
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name IN ('execmirror_orders','execmirror_fills','execmirror_control','kalshi_live_intents','kalshi_live_fills','kalshi_live_events','kalshi_smalllive_control','small_live_order_events','small_live_control','small_live_control_events','live_approvals','canonical_intent_executions','execution_intents','bettor_funded_intents','bettor_funded_fills','mirror_orders','bettor_desk_orders','live_orders','red_team_control_receipts','paper_control','adriana_arb_scans','agent_status','paper_session_health','paper_sessions','paper_handoffs','paper_xavier_reviews','paper_audrey_reports','paper_audrey_findings','paper_decisions','paper_orders')
 GROUP BY table_name ORDER BY table_name;
\echo Q6 external_valuations: rows, outcome-basis rows, venue price rows, distinct contracts, indexes
SELECT count(*) AS rows_total, count(*) FILTER (WHERE outcome_basis IS NOT NULL) AS outcome_rows,
       count(*) FILTER (WHERE outcome_basis IS NULL AND settlement_read IS NOT NULL AND settlement_read_at IS NOT NULL) AS venue_price_rows,
       count(DISTINCT us_market_slug) AS contracts
  FROM external_valuations;
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'external_valuations' ORDER BY indexname;
\echo Q7 paper_session_health rows
SELECT session_id, passes, errors, mutation_attempts, heartbeat_at, jsonb_array_length(recent_heartbeats) AS ring_len FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST;
\echo Q8 paper_sessions
SELECT session_id, account_id, status, started_at, simulator_version FROM paper_sessions ORDER BY started_at DESC LIMIT 5;
\echo Q9 ingestion_state keys of interest
SELECT key, left(value::text, 240) AS value_head FROM ingestion_state
 WHERE key IN ('paper_session_last_pass','workers_boot','coverage_integrity_last')
    OR key ILIKE '%authority%' OR key ILIKE '%small_live%' OR key ILIKE '%smalllive%' OR key ILIKE '%adriana%' OR key ILIKE '%kalshi%live%' OR key ILIKE '%shadow%'
 ORDER BY key LIMIT 40;
\echo Q10 control rows: paper_control, small_live_control, execmirror_control, kalshi_smalllive_control
SELECT 'paper_control' AS src, left(row_to_json(p)::text, 300) AS row_head FROM paper_control p
UNION ALL SELECT 'small_live_control', left(row_to_json(s)::text, 300) FROM small_live_control s
UNION ALL SELECT 'execmirror_control', left(row_to_json(e)::text, 300) FROM execmirror_control e
UNION ALL SELECT 'kalshi_smalllive_control', left(row_to_json(k)::text, 300) FROM kalshi_smalllive_control k;
\echo Q11 red_team_control_receipts newest 2 (head of the row)
SELECT left(row_to_json(r)::text, 600) AS row_head FROM red_team_control_receipts r ORDER BY computed_at DESC LIMIT 2;
\echo Q12 agent_status rows for ADRIANA, XAVIER, AUDREY, ALLIE
SELECT left(row_to_json(a)::text, 400) AS row_head FROM agent_status a WHERE agent_id IN ('ADRIANA','XAVIER','AUDREY','ALLIE') ORDER BY agent_id;
\echo Q13 exact counts of the paper ledger tables (the digest inputs)
SELECT (SELECT count(*) FROM paper_ledger) AS ledger, (SELECT count(*) FROM paper_fills) AS fills, (SELECT count(*) FROM paper_orders) AS orders,
       (SELECT count(*) FROM paper_order_events) AS order_events, (SELECT count(*) FROM paper_settlements) AS settlements,
       (SELECT count(*) FROM paper_decisions) AS decisions, (SELECT count(*) FROM paper_book_observations) AS book_obs,
       (SELECT count(*) FROM paper_shadow_counterfactuals) AS shadows, (SELECT count(*) FROM paper_shadow_counterfactual_outcomes) AS shadow_outcomes,
       (SELECT count(*) FROM paper_xavier_reviews) AS xavier_reviews, (SELECT count(*) FROM paper_handoffs) AS handoffs,
       (SELECT count(*) FROM paper_liquidity_consumed) AS liquidity_consumed, (SELECT count(*) FROM paper_equity_snapshots) AS equity_snapshots;
\echo Q14 digest timing probe: md5 over paper_ledger and paper_fills, with the statement clock
SELECT 'paper_ledger' AS tbl, count(*) AS n, md5(string_agg(md5(t::text), '' ORDER BY md5(t::text))) AS digest, clock_timestamp() - statement_timestamp() AS took FROM paper_ledger t
UNION ALL SELECT 'paper_fills', count(*), md5(string_agg(md5(t::text), '' ORDER BY md5(t::text))), clock_timestamp() - statement_timestamp() FROM paper_fills t
UNION ALL SELECT 'paper_order_events', count(*), md5(string_agg(md5(t::text), '' ORDER BY md5(t::text))), clock_timestamp() - statement_timestamp() FROM paper_order_events t
UNION ALL SELECT 'paper_decisions', count(*), md5(string_agg(md5(t::text), '' ORDER BY md5(t::text))), clock_timestamp() - statement_timestamp() FROM paper_decisions t;
