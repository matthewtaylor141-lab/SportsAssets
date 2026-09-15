-- ============================================================================
-- SCHEMA VALIDATION AND POPULATION CENSUS (2026-09-10, read-only).
--
-- Three questions that must be settled before ANY section 5 result is
-- interpreted. None of them is answerable from naming conventions, so all of
-- them are read from the records.
--
-- ---------------------------------------------------------------------------
-- Q1. IS A SOCCER HOME/DRAW/AWAY EVENT ONE THREE-OUTCOME conditionId, OR
--     THREE BINARY conditionIds EACH WITH YES/NO TOKENS?
--
-- This decides what min(Y,N) even means. If a condition is the binary
-- proposition "Will Lazio win?" with YES and NO tokens, then min(Y,N) is a
-- true matched pair and outcome_index 0/1 are settlement complements. If a
-- condition is the whole three-way event, then indices 0 and 1 are Home and
-- Draw, both can settle at zero, and every "matched" figure computed on them
-- is wrong.
--
-- Statement 1 answers it structurally: how many DISTINCT condition_ids does
-- one event_slug carry, and what are their titles. Statement 2 shows the raw
-- rows -- token id, index, human label, payout -- so the semantics are read
-- rather than inferred. Statement 3 tests complementarity on EVERY settled
-- condition rather than a sample.
--
-- A CONSEQUENCE WORTH NAMING EVEN IF THE ANSWER IS "THREE BINARIES": a
-- per-condition analysis cannot see CROSS-CONDITION hedging inside one event.
-- Buying YES-Lazio and YES-Draw is economically a partial hedge; scored per
-- condition it reads as two independent directional positions, overstating
-- his directional exposure. Statement 4 measures how often he holds more than
-- one condition of the same event, which bounds that error.
--
-- ---------------------------------------------------------------------------
-- Q2. WHAT POPULATION IS OUR trades TABLE, ACTUALLY?
--
-- The forensic report counts 4,615,349 RN1 fills. Ours holds far fewer. No
-- figure may be called "lifetime" until the gap is accounted for, so
-- statements 5 to 8 characterise our rows exactly: by source, by side, by
-- month (including the months with NO rows at all), by what each analysis
-- filter removes, and by how much D1's collapse of (tx_hash, asset, side)
-- across the chain and poll paths reduces a raw count.
--
-- Every one of those is a candidate explanation and each is measured
-- separately so the reconciliation is arithmetic rather than narrative.
--
-- ---------------------------------------------------------------------------
-- Q3. DOES THE SETTLEMENT PAYOUT PAIR SUM TO ONE?
--
-- The A-vs-B equality proved out as A - B = M x (SY + SN - 1), where SY and
-- SN are SETTLEMENT payouts and not acquisition prices. Acquisition prices
-- do not sum to one -- vY + vN < 1 IS the matched edge. Statement 3 tests the
-- settlement claim directly and by sport, so the equality rests on a
-- measurement instead of an assumption.
--
-- Read-only: eight SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. Q1 STRUCTURAL: how many conditions does ONE event carry? =='
WITH f AS (
  SELECT DISTINCT t.event_slug, t.condition_id, t.sport, t.market_title, t.market_slug
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.event_slug IS NOT NULL
     AND t.condition_id IS NOT NULL AND t.ts >= now() - interval '30 days'
), e AS (
  SELECT event_slug, min(sport) AS sport, count(DISTINCT condition_id) AS conditions
    FROM f GROUP BY event_slug
)
SELECT COALESCE(sport, 'ALL') AS sport,
       count(*) AS events,
       count(*) FILTER (WHERE conditions = 1) AS one_condition,
       count(*) FILTER (WHERE conditions = 2) AS two_conditions,
       count(*) FILTER (WHERE conditions = 3) AS three_conditions,
       count(*) FILTER (WHERE conditions > 3) AS more_than_three,
       round(avg(conditions)::numeric, 2) AS avg_conditions_per_event,
       max(conditions) AS max_conditions
  FROM e GROUP BY ROLLUP (sport) ORDER BY count(*) DESC LIMIT 20;


\echo '== 2. Q1 RAW ROWS: token, index, label and payout, one sample per family =='
WITH f AS (
  SELECT t.condition_id, t.asset, t.outcome_index, t.outcome, t.market_title,
         t.market_slug, t.event_slug, t.sport,
         CASE WHEN t.market_slug ~ 'btts|ftts' THEN 'e btts'
              WHEN t.market_slug ~ '-(o|u)\d|total'      THEN 'c totals'
              WHEN t.market_slug ~ '(pos|neg)-?\d|spread|handicap' THEN 'd spread'
              WHEN t.sport = 'Tennis'                    THEN 'f tennis'
              WHEN t.sport = 'NBA'                       THEN 'g nba'
              WHEN t.market_slug ~ 'draw'                THEN 'b draw'
              WHEN t.sport = 'Soccer'                    THEN 'a soccer winner'
              ELSE 'h other' END AS family
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.ts >= now() - interval '21 days'
), pick AS (
  SELECT DISTINCT ON (family) family, condition_id, market_title, event_slug, sport
    FROM f ORDER BY family, condition_id
)
SELECT p.family, left(p.market_title, 46) AS title, left(p.event_slug, 34) AS event_slug,
       f.outcome_index AS idx, left(f.outcome, 22) AS outcome_label,
       left(f.asset, 18) || '...' AS token_id_head,
       (SELECT count(DISTINCT asset) FROM f f2 WHERE f2.condition_id = p.condition_id) AS tokens_in_condition,
       CASE WHEN m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
            THEN jsonb_array_length(m.resolved_prices) END AS payout_slots,
       CASE WHEN m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
            THEN (m.resolved_prices ->> f.outcome_index) END AS this_token_payout,
       CASE WHEN m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
            THEN round(((m.resolved_prices ->> 0)::float8
                        + (m.resolved_prices ->> 1)::float8)::numeric, 4) END AS payout_0_plus_1
  FROM pick p
  JOIN (SELECT DISTINCT condition_id, asset, outcome_index, outcome FROM f) f
    ON f.condition_id = p.condition_id
  LEFT JOIN markets m ON m.condition_id = p.condition_id
 ORDER BY p.family, f.outcome_index LIMIT 40;


\echo '== 3. Q3: do the two settlement payouts sum to ONE, by sport? =='
-- The A-vs-B equality rests on SY + SN = 1. This is that claim, measured.
WITH c AS (
  SELECT DISTINCT t.condition_id, t.sport
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
)
SELECT COALESCE(c.sport, 'ALL') AS sport,
       count(*) AS settled_conditions,
       count(*) FILTER (WHERE jsonb_array_length(m.resolved_prices) = 2) AS exactly_two_slots,
       count(*) FILTER (WHERE jsonb_array_length(m.resolved_prices) > 2) AS more_than_two_slots,
       round(avg((m.resolved_prices ->> 0)::float8
                 + (m.resolved_prices ->> 1)::float8)::numeric, 5) AS avg_S0_plus_S1,
       count(*) FILTER (WHERE abs((m.resolved_prices ->> 0)::float8
                                  + (m.resolved_prices ->> 1)::float8 - 1.0) < 0.0001) AS sums_to_one,
       count(*) FILTER (WHERE abs((m.resolved_prices ->> 0)::float8
                                  + (m.resolved_prices ->> 1)::float8 - 1.0) >= 0.0001) AS DOES_NOT_SUM,
       count(*) FILTER (WHERE (m.resolved_prices ->> 0)::float8
                              NOT IN (0.0, 1.0)) AS non_binary_payout
  FROM c JOIN markets m ON m.condition_id = c.condition_id
 WHERE m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
 GROUP BY ROLLUP (c.sport) ORDER BY count(*) DESC LIMIT 20;


\echo '== 4. THE BLIND SPOT: does he hold several conditions of the SAME event? =='
-- A per-condition analysis cannot see a hedge spread across two conditions of
-- one event. This bounds how much of his book that could be.
WITH f AS (
  SELECT t.event_slug, t.condition_id, t.sport, t.notional::float8 AS usd
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.event_slug IS NOT NULL AND t.condition_id IS NOT NULL
     AND t.ts >= now() - interval '30 days'
), e AS (
  SELECT event_slug, min(sport) AS sport, count(DISTINCT condition_id) AS conds,
         sum(usd) AS usd
    FROM f GROUP BY event_slug
)
SELECT COALESCE(sport, 'ALL') AS sport,
       count(*) AS events,
       count(*) FILTER (WHERE conds > 1) AS multi_condition_events,
       round((100.0 * count(*) FILTER (WHERE conds > 1) / NULLIF(count(*), 0))::numeric, 1) AS pct_events,
       round(sum(usd)::numeric, 0) AS usd,
       round(sum(usd) FILTER (WHERE conds > 1)::numeric, 0) AS usd_multi_condition,
       round((100.0 * sum(usd) FILTER (WHERE conds > 1) / NULLIF(sum(usd), 0))::numeric, 1)
         AS PCT_USD_INVISIBLE_TO_PER_CONDITION
  FROM e GROUP BY ROLLUP (sport) ORDER BY sum(usd) DESC NULLS LAST LIMIT 20;


\echo '== 5. Q2 POPULATION: every RN1 row we hold, by source and side =='
SELECT COALESCE(t.source, '(null)') AS source, t.side,
       count(*) AS rows,
       count(DISTINCT t.condition_id) AS conditions,
       count(DISTINCT t.dedupe_key) AS distinct_dedupe_keys,
       count(*) FILTER (WHERE t.outcome_index IS NULL) AS null_outcome_index,
       count(*) FILTER (WHERE t.condition_id IS NULL) AS null_condition,
       count(*) FILTER (WHERE t.outcome_index >= 2) AS index_two_or_more,
       round(sum(t.notional)::numeric, 0) AS usd,
       min(t.ts)::date AS first_day, max(t.ts)::date AS last_day
  FROM trades t JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1'
 GROUP BY 1, 2 ORDER BY 3 DESC;


\echo '== 6. Q2 COVERAGE: month by month, INCLUDING months with no rows =='
WITH months AS (
  SELECT generate_series(date_trunc('month', (SELECT min(ts) FROM trades)),
                         date_trunc('month', now()), interval '1 month') AS mo
), r AS (
  SELECT date_trunc('month', t.ts) AS mo, count(*) AS fills,
         count(DISTINCT t.condition_id) AS conditions,
         round(sum(t.notional)::numeric, 0) AS usd,
         count(DISTINCT date_trunc('day', t.ts)) AS days_with_fills
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' GROUP BY 1
)
SELECT to_char(months.mo, 'YYYY-MM') AS month,
       COALESCE(r.fills, 0) AS fills,
       COALESCE(r.conditions, 0) AS conditions,
       COALESCE(r.usd, 0) AS usd,
       COALESCE(r.days_with_fills, 0) AS days_with_fills,
       CASE WHEN r.fills IS NULL THEN 'NO DATA AT ALL' ELSE '' END AS gap
  FROM months LEFT JOIN r ON r.mo = months.mo ORDER BY 1;


\echo '== 7. Q2 FILTER ATTRITION: what did each analysis filter remove? =='
-- The section 5 population was built by a chain of filters. Each line is the
-- count surviving it, so the difference between our number and the report is
-- attributable rather than asserted.
WITH t AS (
  SELECT t.* FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
)
SELECT (SELECT count(*) FROM t) AS step0_all_rn1_rows,
       (SELECT count(*) FROM t WHERE condition_id IS NOT NULL) AS step1_has_condition,
       (SELECT count(*) FROM t WHERE condition_id IS NOT NULL
                            AND outcome_index IS NOT NULL) AS step2_has_outcome_index,
       (SELECT count(*) FROM t WHERE condition_id IS NOT NULL
                            AND outcome_index IN (0, 1)) AS step3_index_is_0_or_1,
       (SELECT count(*) FROM t WHERE condition_id IS NOT NULL AND outcome_index IN (0, 1)
                            AND side = 'BUY') AS step4_buys_only,
       (SELECT count(*) FROM t JOIN markets m ON m.condition_id = t.condition_id
         WHERE t.outcome_index IN (0, 1)) AS step5_market_row_exists,
       (SELECT count(*) FROM t JOIN markets m ON m.condition_id = t.condition_id
         WHERE t.outcome_index IN (0, 1) AND m.resolved
           AND jsonb_typeof(m.resolved_prices) = 'array') AS step6_settled_and_gradeable,
       (SELECT count(DISTINCT condition_id) FROM t) AS conditions_all,
       (SELECT count(DISTINCT t.condition_id) FROM t
          JOIN markets m ON m.condition_id = t.condition_id
         WHERE m.resolved AND jsonb_typeof(m.resolved_prices) = 'array') AS conditions_settled;


\echo '== 8. Q2 DEDUP: how much does the D1 collapse reduce a raw fill count? =='
-- D1 collapses the chain and poll paths on (tx_hash, asset, side). If the
-- report counts raw venue fills and we count collapsed ones, that is a
-- multiplier on the population gap and it is measurable here.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.size, t.ts, t.source, t.dedupe_key
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
)
SELECT count(*) AS rows_stored,
       count(DISTINCT dedupe_key) AS distinct_dedupe_key,
       count(DISTINCT (tx_hash, asset, side)) AS distinct_tx_asset_side,
       round((count(*)::numeric / NULLIF(count(DISTINCT (tx_hash, asset, side)), 0)), 4)
         AS rows_per_tx_asset_side,
       count(DISTINCT tx_hash) AS distinct_tx,
       round((count(*)::numeric / NULLIF(count(DISTINCT tx_hash), 0)), 2) AS rows_per_tx,
       count(*) FILTER (WHERE source = 'chain') AS via_chain,
       count(*) FILTER (WHERE source = 'poll') AS via_poll,
       count(*) FILTER (WHERE source = 'backfill') AS via_backfill,
       count(*) FILTER (WHERE source = 's1') AS via_s1
  FROM t;
