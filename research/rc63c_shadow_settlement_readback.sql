-- READ-ONLY. RC6.3c lane pass-hardening, round 3 (rc6/pass-hardening): the
-- independent reviewer found that bettor_capital_authority.settle_shadows (pass
-- step `shadow_settlement`, which runs BEFORE `derek`) reads external_valuations
-- once per PENDING shadow (two statements per shadow when the first finds
-- nothing), up to SETTLE_PER_PASS = 200 shadows every RUN_EVERY_S = 600 s, and
-- that pending shadows never back off. This readback measures, on production:
--   P1 the last pass's heartbeat and EVERY step's elapsed seconds (the N2 design
--      answer needs the measured pre-derek timings, not only derek's and settle's);
--   P2 the recent pass ring: duration and slowest step per pass;
--   P3 the pending shadows: how many, per account / strategy / source, how old,
--      how many the step would examine, and how many of their contracts carry
--      any outcome row at all (those that do not stay pending and are re-read);
--   P4 external_valuations: size, rows with an outcome basis, its indexes;
--   P5 the per-contract statements the step runs today and the batched (= ANY)
--      forms that replace them, each EXPLAIN ANALYZEd once over the pending
--      shadows' own contracts (the production per-scan cost and the one-read cost).
-- SELECT and EXPLAIN only.

\echo == P1a the last pass heartbeat (ingestion_state paper_session_last_pass)
SELECT to_char(to_timestamp((h.value->>'written_at')::float), 'YYYY-MM-DD HH24:MI:SS') AS hb_written,
       h.value->>'ran' AS ran, h.value->>'trigger' AS trigger, h.value->>'refusal' AS refusal,
       h.value->>'elapsed_s' AS elapsed_s, h.value->>'exceeded_step' AS exceeded_step,
       h.value->>'budget_exhausted' AS budget_exhausted,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(h.value->'skipped_steps', '{}'::jsonb)) k) AS skipped,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(h.value->'errors', '{}'::jsonb)) k) AS error_keys,
       h.value->>'decisions_recorded' AS decisions, h.value->>'orders_submitted' AS orders,
       h.value->'pass_time' AS pass_time
  FROM ingestion_state h WHERE h.key = 'paper_session_last_pass';

\echo == P1b the last pass: every step elapsed seconds (heartbeat), slowest first
SELECT key AS step, value AS elapsed_s
  FROM jsonb_each(coalesce((SELECT value->'step_elapsed_s' FROM ingestion_state WHERE key = 'paper_session_last_pass'), '{}'::jsonb))
 ORDER BY (value::text)::float DESC NULLS LAST;

\echo == P1c the last pass: the step digests for shadow_settlement, books, simulate, turnaround, profitability_fit, profitability_quarantine, counterfactual_settlement, derek, settle
SELECT key AS step, left(value::text, 400) AS digest
  FROM jsonb_each(coalesce((SELECT value->'steps' FROM ingestion_state WHERE key = 'paper_session_last_pass'), '{}'::jsonb))
 WHERE key IN ('books', 'maker_maintain', 'simulate', 'turnaround', 'shadow_settlement', 'profitability_fit', 'profitability_quarantine', 'counterfactual_settlement', 'derek', 'settle')
 ORDER BY key;

\echo == P1d the last pass errors
SELECT key, left(value::text, 300) AS error
  FROM jsonb_each(coalesce((SELECT value->'errors' FROM ingestion_state WHERE key = 'paper_session_last_pass'), '{}'::jsonb));

\echo == P1e paper_session_health (newest heartbeat): passes, errors, last_pass step_elapsed_s
SELECT to_char(s.heartbeat_at, 'YYYY-MM-DD HH24:MI:SS') AS health_at, s.passes, s.errors, left(s.last_error, 200) AS last_error,
       s.last_pass->>'elapsed_s' AS last_elapsed_s, s.last_pass->'step_elapsed_s' AS last_step_elapsed_s
  FROM paper_session_health s ORDER BY s.heartbeat_at DESC NULLS LAST LIMIT 1;

\echo == P2 the recent pass ring (newest heartbeat row): at, elapsed, slowest step, ok -- newest last
SELECT to_char(to_timestamp((x->>'at')::float), 'MM-DD HH24:MI:SS') AS at, x->>'elapsed_s' AS elapsed_s,
       x->>'slowest_step' AS slowest_step, x->>'slowest_step_s' AS slowest_step_s, x->>'ok' AS ok,
       x->'summary'->>'budget_exhausted' AS budget_exhausted, x->'summary'->>'decisions_recorded' AS decisions
  FROM paper_session_health s, jsonb_array_elements(s.recent_heartbeats) WITH ORDINALITY AS t(x, n)
 WHERE s.session_id = (SELECT session_id FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 1)
 ORDER BY n;

\echo == P3a pending shadows (no outcome row) per account: count, distinct contracts, oldest and newest decided_at
SELECT s.account_id, count(*) AS pending, count(DISTINCT s.us_market_slug) AS contracts,
       to_char(min(s.decided_at), 'YYYY-MM-DD HH24:MI') AS oldest, to_char(max(s.decided_at), 'YYYY-MM-DD HH24:MI') AS newest,
       (SELECT count(*) FROM paper_shadow_counterfactuals t WHERE t.account_id = s.account_id) AS shadows_total,
       (SELECT count(*) FROM paper_shadow_counterfactuals t JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id) WHERE t.account_id = s.account_id) AS settled_total
  FROM paper_shadow_counterfactuals s
  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE o.shadow_id IS NULL
 GROUP BY s.account_id ORDER BY pending DESC;

\echo == P3b pending shadows per strategy / source / capital refusal
SELECT s.strategy, s.source, s.capital_refusal, count(*) AS pending, count(DISTINCT s.us_market_slug) AS contracts
  FROM paper_shadow_counterfactuals s
  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE o.shadow_id IS NULL
 GROUP BY 1, 2, 3 ORDER BY pending DESC LIMIT 30;

\echo == P3c the batch the step examines (per account: oldest 200 pending by decided_at): shadows, distinct contracts, contracts with ANY outcome-basis row, with ANY venue price-settlement row, with neither
WITH batch AS (
  SELECT s.account_id, s.shadow_id, s.us_market_slug,
         row_number() OVER (PARTITION BY s.account_id ORDER BY s.decided_at) AS rn
    FROM paper_shadow_counterfactuals s
    LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
   WHERE o.shadow_id IS NULL),
b AS (SELECT * FROM batch WHERE rn <= 200),
slugs AS (SELECT DISTINCT account_id, us_market_slug FROM b),
cls AS (
  SELECT sl.account_id, sl.us_market_slug,
         EXISTS (SELECT 1 FROM external_valuations v WHERE v.us_market_slug = sl.us_market_slug AND v.outcome_basis IS NOT NULL) AS has_outcome_row,
         EXISTS (SELECT 1 FROM external_valuations v WHERE v.us_market_slug = sl.us_market_slug AND v.outcome_basis IS NULL AND v.settlement_read IS NOT NULL AND v.settlement_read_at IS NOT NULL) AS has_venue_price_row
    FROM slugs sl)
SELECT c.account_id, (SELECT count(*) FROM b WHERE b.account_id = c.account_id) AS batch_shadows,
       count(*) AS contracts,
       count(*) FILTER (WHERE has_outcome_row) AS with_outcome_row,
       count(*) FILTER (WHERE has_venue_price_row) AS with_venue_price_row,
       count(*) FILTER (WHERE NOT has_outcome_row AND NOT has_venue_price_row) AS with_neither_stay_pending
  FROM cls c GROUP BY c.account_id ORDER BY batch_shadows DESC;

\echo == P3d outcomes written so far, per outcome
SELECT o.outcome, count(*) AS n, to_char(max(o.settled_at), 'YYYY-MM-DD HH24:MI') AS newest_settled_at
  FROM paper_shadow_counterfactual_outcomes o GROUP BY 1 ORDER BY n DESC;

\echo == P4a external_valuations: rows, rows with an outcome basis, rows with a venue price read, distinct contracts, table and index sizes
SELECT count(*) AS rows_total,
       count(*) FILTER (WHERE outcome_basis IS NOT NULL) AS with_outcome_basis,
       count(*) FILTER (WHERE outcome_basis IS NULL AND settlement_read IS NOT NULL AND settlement_read_at IS NOT NULL) AS venue_price_rows,
       count(DISTINCT us_market_slug) AS contracts,
       pg_size_pretty(pg_relation_size('external_valuations')) AS table_size,
       pg_size_pretty(pg_indexes_size('external_valuations')) AS indexes_size
  FROM external_valuations;

\echo == P4b external_valuations indexes
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'external_valuations' ORDER BY indexname;

\echo == P5a the per-contract outcome read the step runs today, once, for the OLDEST pending shadow contract (production per-scan cost)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT id, buy_intent, outcome, outcome_known, outcome_basis, outcome_at
  FROM external_valuations
 WHERE us_market_slug = (SELECT s.us_market_slug FROM paper_shadow_counterfactuals s LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id) WHERE o.shadow_id IS NULL ORDER BY s.decided_at LIMIT 1)
   AND outcome_basis IS NOT NULL
 ORDER BY id;

\echo == P5b the per-contract venue-price read the step runs today when the first finds nothing, once, for the same contract
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT id, buy_intent, settlement_read, settlement_read_at, settlement_comparison->>'venue_rules_text' AS rules
  FROM external_valuations
 WHERE us_market_slug = (SELECT s.us_market_slug FROM paper_shadow_counterfactuals s LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id) WHERE o.shadow_id IS NULL ORDER BY s.decided_at LIMIT 1)
   AND outcome_basis IS NULL AND settlement_read IS NOT NULL AND settlement_read_at IS NOT NULL
 ORDER BY id;

\echo == P5c the batched outcome read (us_market_slug = ANY) over the contracts of the oldest 200 pending shadows -- the one statement that replaces up to 200 per-contract reads
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
WITH b AS (
  SELECT s.us_market_slug FROM paper_shadow_counterfactuals s LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
   WHERE o.shadow_id IS NULL ORDER BY s.decided_at LIMIT 200),
arr AS (SELECT array_agg(DISTINCT us_market_slug) AS slugs FROM b)
SELECT id, us_market_slug, buy_intent, outcome, outcome_known, outcome_basis, outcome_at
  FROM external_valuations
 WHERE us_market_slug = ANY((SELECT slugs FROM arr)::text[]) AND outcome_basis IS NOT NULL
 ORDER BY id;

\echo == P5d the batched venue-price read (us_market_slug = ANY) over the same contracts
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
WITH b AS (
  SELECT s.us_market_slug FROM paper_shadow_counterfactuals s LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
   WHERE o.shadow_id IS NULL ORDER BY s.decided_at LIMIT 200),
arr AS (SELECT array_agg(DISTINCT us_market_slug) AS slugs FROM b)
SELECT id, us_market_slug, buy_intent, settlement_read, settlement_read_at, settlement_comparison->>'venue_rules_text' AS rules
  FROM external_valuations
 WHERE us_market_slug = ANY((SELECT slugs FROM arr)::text[]) AND outcome_basis IS NULL
   AND settlement_read IS NOT NULL AND settlement_read_at IS NOT NULL
 ORDER BY id;

\echo == P6 the paper_shadow_counterfactuals read the step starts with (its own cost)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT s.* FROM paper_shadow_counterfactuals s
  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE o.shadow_id IS NULL AND s.decided_at <= now() ORDER BY s.decided_at LIMIT 200;
