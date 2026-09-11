-- ============================================================================
-- SECTION 3 GATE: THREE CLOCKS, and whether any bid-side depth exists at all
-- (2026-09-11, read-only.)
--
-- WHY THE CLOCK QUESTION COMES FIRST. The stated reaction_s has a p50 of
-- -0.70 s since 2026-08-24 on ~95% of probes. A negative reaction is not
-- speed, it is two clocks disagreeing, and every interval built across those
-- two clocks inherits the error. Read from the code, this system stamps time
-- in THREE places and they are not the same clock:
--
--   VENUE / CHAIN CLOCK   trades.ts (ev.ts_epoch, the block or API stamp),
--                         copy_probes.fill_ts
--   WORKER PYTHON CLOCK   trades.detected_at  (pipeline.py:128,
--                         `datetime.now(tz=timezone.utc)`) and
--                         copy_probes.probe_at (copy_probe.py, same call)
--   DATABASE CLOCK        mirror_orders.placed_at, mirror_shadow.at,
--                         mirror_candidate_refusals.at, engine_fills.created_at,
--                         service_heartbeats.beat_at -- all DEFAULT now()
--
-- SO WHICH INTERVALS ARE VALID, and which are not:
--
--   VALID, same clock    probe_at - detected_at        worker vs worker
--                        any DB stamp minus any DB stamp
--   CROSS-CLOCK          detected_at - ts              worker vs venue
--                        reaction_s = probe_at - fill_ts   worker vs venue
--                        mirror_shadow.at - detected_at    DB vs worker
--
-- That last one matters beyond the latency question: the observation_delay in
-- my own completion test was DB-minus-worker, so it is cross-clock too and
-- must be labelled, not quoted as a delay.
--
-- The owner's rule is followed exactly: where no common clock exists, true
-- latency is left UNIDENTIFIED rather than corrected heuristically. Statement 3
-- looks for a row that carries BOTH a worker stamp and a DB stamp, because that
-- and only that would let the offset be measured rather than assumed.
--
-- THE SECOND QUESTION. copy_probes retains an ASK ladder only. Before SELL-side
-- completion economics can be called unmeasurable, engine_fills must be ruled
-- in or out: its `book` column is written as
-- {"asks": [...], "bids": [...]} by /api/engine/fills. The migration comment
-- calls it a "top-of-book snapshot", which if true means one level and no
-- ladder. Statements 4-6 settle how many bid levels it actually holds, whether
-- its keys reach RN1's conditions, and whether it is independent of BETTOR
-- choosing to trade.
--
-- Read-only: six SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. THE ONE VALID SAME-CLOCK INTERVAL: probe dispatch (worker vs worker) =='
-- Both stamps come from datetime.now(utc) in the same process family, so this
-- difference is real. It bounds the semaphore/dispatch wait -- but NOT the HTTP
-- GET, whose duration is never recorded. The book snapshot therefore lies in
-- [probe_at, probe_at + 8s], the 8 s being the httpx client timeout.
SELECT p.probe_at::date AS day, count(*) AS probes,
       round(avg(extract(epoch FROM p.probe_at - t.detected_at))::numeric, 4) AS mean_dispatch_s,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM p.probe_at - t.detected_at))::numeric, 4) AS p50,
       round(percentile_cont(0.95) WITHIN GROUP (
         ORDER BY extract(epoch FROM p.probe_at - t.detected_at))::numeric, 4) AS p95,
       round(percentile_cont(0.999) WITHIN GROUP (
         ORDER BY extract(epoch FROM p.probe_at - t.detected_at))::numeric, 4) AS p999,
       count(*) FILTER (WHERE p.probe_at < t.detected_at) AS probe_BEFORE_detection
  FROM copy_probes p JOIN trades t ON t.id = p.trade_id
  JOIN whales w ON w.id = p.whale_id
 WHERE lower(w.username) = 'rn1' AND p.probe_at >= timestamptz '2026-08-05 00:00Z'
 GROUP BY 1 ORDER BY 1 DESC LIMIT 14;


\echo '== 2. THE CROSS-CLOCK INTERVAL, shown as evidence of skew not speed =='
-- detected_at (worker) minus ts (venue). If the worker clock were aligned with
-- the venue this would be non-negative by construction: we cannot see a fill
-- before it happens. A large negative mass is the skew, measured per feed
-- because the two feeds carry different venue stamps.
SELECT CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue feed' ELSE 'cash feed' END AS feed,
       t.ts::date AS day, count(*) AS fills,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM t.detected_at - t.ts))::numeric, 3) AS p50_cross_clock_s,
       count(*) FILTER (WHERE t.detected_at < t.ts) AS detected_BEFORE_the_fill,
       round((100.0 * count(*) FILTER (WHERE t.detected_at < t.ts) / count(*))::numeric, 2)
         AS pct_impossible_ordering
  FROM trades t JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
   AND t.ts >= timestamptz '2026-09-01 00:00Z'
 GROUP BY 1, 2 ORDER BY 2 DESC, 1 LIMIT 24;


\echo '== 3. IS THERE A ROW CARRYING BOTH A WORKER STAMP AND A DB STAMP? =='
-- The only way to measure the worker-to-database offset rather than assume it.
-- service_heartbeats.beat_at is DEFAULT now() (database) and its detail JSONB is
-- built in the worker, so a numeric epoch key inside detail would be the pair.
SELECT service,
       to_char(beat_at, 'MM-DD HH24:MI:SS') AS beat_at_DB_clock,
       jsonb_object_keys(detail) AS detail_key
  FROM service_heartbeats
 WHERE detail IS NOT NULL AND jsonb_typeof(detail) = 'object'
 ORDER BY service LIMIT 60;


\echo '== 4. engine_fills: does it exist, and how many BID LEVELS does it keep? =='
SELECT venue, count(*) AS rows,
       to_char(min(ts), 'YYYY-MM-DD') AS first_ts,
       to_char(max(ts), 'YYYY-MM-DD') AS last_ts,
       count(book) AS with_book,
       count(*) FILTER (WHERE jsonb_typeof(book -> 'bids') = 'array') AS with_bids_array,
       count(*) FILTER (WHERE jsonb_array_length(COALESCE(book -> 'bids', '[]'::jsonb)) > 0)
         AS with_at_least_one_bid,
       max(jsonb_array_length(COALESCE(book -> 'bids', '[]'::jsonb))) AS MAX_BID_LEVELS,
       round(avg(jsonb_array_length(COALESCE(book -> 'bids', '[]'::jsonb)))::numeric, 2)
         AS mean_bid_levels,
       max(jsonb_array_length(COALESCE(book -> 'asks', '[]'::jsonb))) AS max_ask_levels
  FROM engine_fills GROUP BY 1 ORDER BY 2 DESC;


\echo '== 5. engine_fills KEY COMPATIBILITY: do its markets reach RN1 conditions? =='
-- market_id is documented as conditionId for Polymarket and outcome_id as the
-- token id, which is exactly how trades keys RN1. If the intersection is empty
-- the table cannot serve section 3 regardless of how good its book is.
WITH ef AS (
  SELECT DISTINCT market_id, outcome_id FROM engine_fills
   WHERE venue ILIKE '%poly%' AND ts >= timestamptz '2026-08-05 00:00Z'
), rn AS (
  SELECT DISTINCT t.condition_id, t.asset
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
)
SELECT (SELECT count(*) FROM ef) AS engine_market_outcome_pairs,
       (SELECT count(*) FROM rn) AS rn1_condition_asset_pairs,
       (SELECT count(*) FROM ef JOIN rn ON rn.condition_id = ef.market_id)
         AS shared_by_condition,
       (SELECT count(*) FROM ef JOIN rn ON rn.asset = ef.outcome_id)
         AS shared_by_TOKEN,
       (SELECT count(DISTINCT ef.market_id) FROM ef
         JOIN rn ON rn.condition_id = ef.market_id) AS distinct_shared_conditions;


\echo '== 6. engine_fills INDEPENDENCE: is its book conditioned on us trading? =='
-- The probe is fired by detection, not by our decision, which is why it is
-- usable. engine_fills is posted by the entry-sleeve engine on ITS OWN
-- candidates. If those candidates are chosen by an edge rule then its coverage
-- of RN1 events is selected on price, and it cannot be a neutral quote source.
SELECT date_trunc('day', ts)::date AS day, venue, count(*) AS rows,
       count(*) FILTER (WHERE would_fill) AS would_fill_true,
       count(*) FILTER (WHERE whale_alignment IS NOT NULL) AS with_whale_alignment,
       round(avg(edge)::numeric, 4) AS mean_stated_edge,
       round(min(limit_price)::numeric, 3) AS px_min,
       round(max(limit_price)::numeric, 3) AS px_max,
       round(avg(size_usd)::numeric, 0) AS mean_size_usd
  FROM engine_fills
 WHERE ts >= timestamptz '2026-08-05 00:00Z'
 GROUP BY 1, 2 ORDER BY 1 DESC LIMIT 20;
