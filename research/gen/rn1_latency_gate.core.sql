-- ============================================================================
-- RUN 79 -- THE LATENCY / EDGE-DECAY EVIDENCE GATE
-- (2026-09-11, read-only. Nothing here writes. mirror_live = false.)
--
-- APPROVED SCOPE, owner 2026-09-11: "Approved for RUN 79 ONLY."
--     evidence / provenance gate
--     raw clock-domain inventory
--     C1 source split
--     receipt / raw venue-timestamp probe
--     population attrition
--     NO ECONOMIC CONCLUSION
--
-- Run 80 does NOT follow automatically. These findings are returned first.
--
-- ---------------------------------------------------------------------------
-- THE INDEPENDENCE RULE (owner, 2026-09-11). Runs 79-83 must not use
-- ai_trades, or any TRUEEDGE-derived field, to repair clocks, select
-- population, define prices, define quantities, or calculate economics.
--
-- THE WORD ai_trades APPEARS NOWHERE BELOW EXCEPT IN THIS COMMENT. An earlier
-- draft proposed bridging the app clock to the database clock with
-- copy_probes.probe_at vs ai_trades.placed_at on the same trade_id. That is
-- withdrawn: it would calibrate the independent estimator's clock with a column
-- written by the system under audit, so a systematic error in the paper
-- trader's write path would be invisible to exactly the check meant to catch
-- it. An unrepaired clock reported CLOCK_UNRESOLVED is the weaker result and
-- the honest one.
--
-- ---------------------------------------------------------------------------
-- WITNESS DISCIPLINE, unchanged from run 78 and restated because it is the
-- rule this work has broken most often:
--
--     A ZERO VIOLATION COUNT BESIDE A ZERO WITNESS COUNT IS **NOT TESTED**.
--     It is never PASS, and it is never NOT APPLICABLE by default -- the
--     reason it could not fire has to be named.
--
-- Statement 6 collects every test in this run beside the number of rows it
-- actually examined, so a vacuous check cannot be read as a clean one.
--
-- ---------------------------------------------------------------------------
-- THE FIVE STAGES, NEVER COLLAPSED:
--     COLUMN EXISTS -> PRODUCTION WRITE SITE EXISTS -> HISTORICAL POPULATION
--       EXISTS -> SEMANTIC CONTENT VERIFIED -> ELIGIBLE FOR **THIS ESTIMATOR**
--
-- Stage 5 is NARROWER here than in run 78. mirror_shadow.mark is a verified
-- PMUS quote and is still ineligible to price an execution, because a mark is
-- not a depth. Attribution-eligible does not mean price-eligible.
--
-- A COLUMN DEFAULT IS A WRITE SITE. price_path.sampled_at is written by
-- DEFAULT now() and the worker's INSERT never names it, so the name rule alone
-- called it NO_WRITE_SITE while it is populated on every row. A FALSE
-- UNAVAILABLE is not the safe direction -- it silently deletes real evidence.
-- Stage 2 therefore has three verdicts: WRITE_SITE / DB_DEFAULT /
-- NO_WRITE_SITE, and DB_DEFAULT stays distinct because the AUTHOR differs:
-- the database, at commit, on the server's clock, not the application on the
-- app container's clock. For a clock field that distinction IS the
-- measurement.
-- ============================================================================

\echo '== 0. EVIDENCE GATE -- five stages, data side, for the latency estimator =='
WITH code(tbl, field, used_as, stage2, stage4) AS (
--%%CODEGATE%%
), rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), t AS MATERIALIZED (
  SELECT id, ts, detected_at, venue_seen_at, source, notional, condition_id, asset
    FROM trades WHERE whale_id IN (SELECT id FROM rn1)
), cp AS MATERIALIZED (
  -- the JSONB columns are reduced to booleans HERE, not carried: this set is
  -- materialised and copy_probes is the largest table on the disk (it filled
  -- it once), so dragging depth through the plan is how a gate query becomes
  -- an outage.
  SELECT trade_id, probe_at, fill_ts, reaction_s, best_ask,
         (depth IS NOT NULL) AS has_depth, book_ok, error
    FROM copy_probes WHERE whale_id IN (SELECT id FROM rn1)
), pp AS MATERIALIZED (
  SELECT p.t_s, p.ask, p.sampled_at
    FROM price_path p
    JOIN live_orders lo ON lo.id = p.row_id
   WHERE lower(COALESCE(lo.whale_username, '')) = 'rn1'
), mo AS MATERIALIZED (
  SELECT placed_at, updated_at, done_at, (receipt IS NOT NULL) AS has_receipt,
         ask_at_send
    FROM mirror_orders WHERE lower(whale) = 'rn1'
), meas(tbl, field, populated, universe, first_at, last_at) AS (
  SELECT 'trades', 'ts', count(ts), count(*), min(ts), max(ts) FROM t
  UNION ALL SELECT 'trades', 'detected_at', count(detected_at), count(*),
                   min(detected_at), max(detected_at) FROM t
  UNION ALL SELECT 'trades', 'venue_seen_at', count(venue_seen_at), count(*),
                   min(venue_seen_at), max(venue_seen_at) FROM t
  UNION ALL SELECT 'copy_probes', 'probe_at', count(probe_at), count(*),
                   min(probe_at), max(probe_at) FROM cp
  UNION ALL SELECT 'copy_probes', 'fill_ts', count(fill_ts), count(*),
                   min(fill_ts), max(fill_ts) FROM cp
  UNION ALL SELECT 'copy_probes', 'reaction_s', count(reaction_s), count(*),
                   min(probe_at), max(probe_at) FROM cp
  UNION ALL SELECT 'copy_probes', 'best_ask', count(best_ask), count(*),
                   min(probe_at), max(probe_at) FROM cp
  UNION ALL SELECT 'copy_probes', 'depth', count(*) FILTER (WHERE has_depth),
                   count(*), min(probe_at), max(probe_at) FROM cp
  UNION ALL SELECT 'copy_probes', 'book_ok', count(*) FILTER (WHERE book_ok),
                   count(*), min(probe_at), max(probe_at) FROM cp
  UNION ALL SELECT 'price_path', 't_s', count(t_s), count(*),
                   min(sampled_at), max(sampled_at) FROM pp
  UNION ALL SELECT 'price_path', 'ask', count(ask), count(*),
                   min(sampled_at), max(sampled_at) FROM pp
  UNION ALL SELECT 'price_path', 'sampled_at', count(sampled_at), count(*),
                   min(sampled_at), max(sampled_at) FROM pp
  UNION ALL SELECT 'mirror_orders', 'placed_at', count(placed_at), count(*),
                   min(placed_at), max(placed_at) FROM mo
  UNION ALL SELECT 'mirror_orders', 'updated_at', count(updated_at), count(*),
                   min(updated_at), max(updated_at) FROM mo
  UNION ALL SELECT 'mirror_orders', 'done_at', count(done_at), count(*),
                   min(placed_at), max(placed_at) FROM mo
  UNION ALL SELECT 'mirror_orders', 'receipt', count(*) FILTER (WHERE has_receipt),
                   count(*), min(placed_at), max(placed_at) FROM mo
  UNION ALL SELECT 'mirror_orders', 'ask_at_send', count(ask_at_send), count(*),
                   min(placed_at), max(placed_at) FROM mo
  UNION ALL SELECT 'service_heartbeats', 'beat_at', count(beat_at), count(*),
                   min(beat_at), max(beat_at) FROM service_heartbeats
)
SELECT c.tbl || '.' || c.field AS evidence_field,
       c.used_as,
       'yes (this query reads it)'        AS stage1_column_exists,
       c.stage2                           AS stage2_write_site,
       m.populated                        AS stage3_populated_rows,
       m.universe                         AS universe_rows,
       CASE WHEN COALESCE(m.universe, 0) = 0 THEN NULL
            ELSE round(100.0 * m.populated / m.universe, 3) END
                                          AS stage3_pct_populated,
       to_char(m.first_at, 'YYYY-MM-DD HH24:MI') AS first_populated,
       to_char(m.last_at,  'YYYY-MM-DD HH24:MI') AS last_populated,
       c.stage4                           AS stage4_semantics,
       CASE
         WHEN c.stage2 = 'NO_WRITE_SITE'
           THEN 'NOT IDENTIFIABLE FROM RETAINED DATA'
         WHEN COALESCE(m.universe, 0) = 0
           THEN 'NOT TESTED -- no universe rows'
         WHEN COALESCE(m.populated, 0) = 0
           THEN 'NOT IDENTIFIABLE FROM RETAINED DATA (zero rows)'
         WHEN c.stage4 LIKE 'UNVERIFIED%'
           THEN 'INELIGIBLE -- semantics unverified'
         WHEN c.stage4 LIKE '%MIXED_DOMAIN%'
           THEN 'ELIGIBLE ONLY SPLIT BY SOURCE (see statement 2)'
         WHEN c.stage4 LIKE '%CROSS_DOMAIN%'
           THEN 'ELIGIBLE AS AN INTERVAL ONLY -- CLOCK_UNRESOLVED'
         WHEN c.stage4 LIKE '%LOWER_BOUND%'
           THEN 'ELIGIBLE AS A LOWER BOUND ONLY'
         WHEN c.stage4 LIKE '%TRUNCATED%'
           THEN 'ELIGIBLE UP TO THE RETAINED DEPTH ONLY'
         WHEN c.stage4 LIKE '%PRE_SEND%'
           THEN 'ELIGIBLE AS A PRE-SEND CLOCK ONLY (not submit, not ack)'
         WHEN m.populated < m.universe
           THEN 'ELIGIBLE (partially populated)'
         ELSE 'ELIGIBLE'
       END                                AS stage5_estimator_eligibility
  FROM code c LEFT JOIN meas m ON m.tbl = c.tbl AND m.field = c.field
 ORDER BY 1;

\echo ''
\echo '== 1. RAW CLOCK-DOMAIN INVENTORY -- who generated each stamp =='
-- No subtraction happens here. This is the map that says which subtractions
-- statement 2 onward is ALLOWED to make. A pair of stamps from different
-- domains can be differenced only as an interval carrying CLOCK_UNRESOLVED.
WITH dom(clock_field, domain, author, event_represented, mutable, notes) AS (VALUES
  ('trades.ts', 'C1', 'chain block producer OR whale-venue API',
   'the source fill', 'no',
   'MIXED: block time on source=chain, server time on source=poll'),
  ('copy_probes.fill_ts', 'C1', 'the same source payload, carried',
   'the source fill', 'no', 'inherits the C1 mixture'),
  ('trades.detected_at', 'C2', 'BETTOR app process',
   'first sight by our pipeline', 'no', 'datetime.now(utc), app container'),
  ('copy_probes.probe_at', 'C2', 'BETTOR app process',
   'OBSERVATION START', 'no',
   'stamped before the semaphore AND before the book GET; fetch completion '
   'never retained, so it is a LOWER BOUND on observability'),
  ('trades.venue_seen_at', 'C3', 'Postgres now() in the upsert conflict branch',
   'the poll lane re-saw the fill', 'no',
   'stamped only when source=poll and same whale; first stamp wins, so NULL '
   'means chain-only or never re-seen, NEVER not-seen-by-the-venue'),
  ('mirror_orders.placed_at', 'C3', 'Postgres DEFAULT now()',
   'the row was INSERTed with state=placing', 'no',
   'BEFORE the venue send -- not a submit time and not an acknowledgement'),
  ('mirror_orders.updated_at', 'C3', 'Postgres now() on every write',
   'the most recent write of any kind', 'YES',
   'the state=open write is the nearest ack proxy and is overwritten'),
  ('mirror_orders.done_at', 'C3', 'Postgres now() at a terminal state',
   'the order reached a terminal state', 'no', ''),
  ('price_path.sampled_at', 'C3', 'Postgres DEFAULT now()',
   'the offset sample was written', 'no',
   'the app statement never names this column'),
  ('service_heartbeats.beat_at', 'C3', 'Postgres now()',
   'a service posted a heartbeat', 'YES (upsert)',
   'candidate half of a C2/C3 bridge -- statement 4'),
  ('mirror_shadow.at', 'C3', 'Postgres DEFAULT now()',
   'a shadow tick row was written', 'no',
   'scan-completion / page-walk ambiguity: not the instant each market was '
   'read'),
  ('mirror_fill_answers.at', 'C2', 'the mirror tick, epoch seconds',
   'the tick first NAMED a fill of his', 'no', 'double precision, not a stamp'),
  ('mirror_orders.receipt -> ?', 'C4', 'the PMUS venue, IF it sends one',
   'unknown', 'unknown',
   'NOTHING C4 IS KNOWN TO BE RETAINED -- statement 3 probes for it')
)
SELECT clock_field, domain, author, event_represented, mutable, notes,
       CASE WHEN domain = 'C4' THEN 'UNPROVEN -- probed in statement 3'
            ELSE 'same-domain differences only; anything else is an interval'
       END AS subtraction_rule
  FROM dom ORDER BY domain, clock_field;

\echo ''
\echo '== 2. C1 SOURCE SPLIT -- one column, two clocks =='
-- detected_at (C2) minus ts (C1) is a CROSS-DOMAIN difference and is reported
-- as such. It is printed split by source because pooling a block timestamp
-- with a venue API timestamp would average two different clocks into one
-- meaningless number. A NEGATIVE value is not a fast reaction: it is proof the
-- two clocks are not comparable.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), t AS MATERIALIZED (
  SELECT source, ts, detected_at, venue_seen_at
    FROM trades WHERE whale_id IN (SELECT id FROM rn1) AND ts IS NOT NULL
)
SELECT source                                            AS c1_source_lane,
       count(*)                                          AS witness_events,
       count(*) FILTER (WHERE detected_at IS NOT NULL)   AS with_detected_at,
       round(min(extract(epoch FROM detected_at - ts))::numeric, 3)  AS min_s,
       round(percentile_cont(0.10) WITHIN GROUP (
             ORDER BY extract(epoch FROM detected_at - ts))::numeric, 3) AS p10_s,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY extract(epoch FROM detected_at - ts))::numeric, 3) AS p50_s,
       round(percentile_cont(0.90) WITHIN GROUP (
             ORDER BY extract(epoch FROM detected_at - ts))::numeric, 3) AS p90_s,
       round(max(extract(epoch FROM detected_at - ts))::numeric, 3)  AS max_s,
       count(*) FILTER (WHERE detected_at < ts)          AS negative_lag_rows,
       CASE WHEN count(*) FILTER (WHERE detected_at IS NOT NULL) = 0
            THEN 'NOT TESTED -- no witness'
            WHEN count(*) FILTER (WHERE detected_at < ts) > 0
            THEN 'CLOCK_UNRESOLVED -- negative lag present'
            ELSE 'CLOCK_UNRESOLVED -- cross-domain, sign not diagnostic'
       END                                               AS verdict,
       count(venue_seen_at)                              AS venue_seen_rows
  FROM t GROUP BY source ORDER BY source;

\echo ''
\echo '== 3. VENUE-TIMESTAMP PROBE -- does any C4 stamp survive at all? =='
-- mirror_orders.receipt is the venue's placement response; live_orders.raw is
-- the venue's full API response. If either carries a venue-side time, the
-- submit->ack component becomes measurable. If neither does, components E and
-- F stay NOT IDENTIFIABLE and no proxy is substituted.
WITH r AS MATERIALIZED (
  SELECT 'mirror_orders.receipt' AS src, receipt AS body
    FROM mirror_orders
   WHERE lower(whale) = 'rn1' AND receipt IS NOT NULL
     AND jsonb_typeof(receipt) = 'object'
  UNION ALL
  SELECT 'live_orders.raw', raw
    FROM live_orders
   WHERE lower(COALESCE(whale_username, '')) = 'rn1' AND raw IS NOT NULL
     AND jsonb_typeof(raw) = 'object'
), k AS (
  SELECT src, jsonb_object_keys(body) AS key FROM r
), agg AS (
  SELECT src, key, count(*) AS rows_with_key FROM k GROUP BY src, key
)
SELECT src                        AS json_source,
       key                        AS top_level_key,
       rows_with_key,
       CASE WHEN key ~* '(time|ts|stamp|_at$|date|created|epoch|clock)'
            THEN 'TIME-SHAPED NAME -- candidate C4 stamp'
            ELSE 'not time-shaped' END AS classification
  FROM agg
 ORDER BY (key ~* '(time|ts|stamp|_at$|date|created|epoch|clock)') DESC,
          rows_with_key DESC, src, key;

\echo ''
\echo '== 3b. VENUE-TIMESTAMP PROBE -- witness counts, so an empty result reads right =='
SELECT (SELECT count(*) FROM mirror_orders
         WHERE lower(whale) = 'rn1' AND receipt IS NOT NULL)  AS receipts_present,
       (SELECT count(*) FROM mirror_orders
         WHERE lower(whale) = 'rn1')                          AS orders_total,
       (SELECT count(*) FROM live_orders
         WHERE lower(COALESCE(whale_username, '')) = 'rn1'
           AND raw IS NOT NULL)                               AS raws_present,
       (SELECT count(*) FROM live_orders
         WHERE lower(COALESCE(whale_username, '')) = 'rn1')   AS live_orders_total,
       CASE WHEN (SELECT count(*) FROM mirror_orders
                   WHERE lower(whale) = 'rn1' AND receipt IS NOT NULL) = 0
             AND (SELECT count(*) FROM live_orders
                   WHERE lower(COALESCE(whale_username, '')) = 'rn1'
                     AND raw IS NOT NULL) = 0
            THEN 'NOT TESTED -- no JSON bodies to probe'
            ELSE 'TESTED -- see statement 3 for the keys found'
       END                                                    AS probe_verdict;

\echo ''
\echo '== 4. INDEPENDENT C2<->C3 CLOCK BRIDGE -- search, not assumption =='
-- A qualifying bridge is a table carrying an APP-supplied timestamp AND a
-- DB-defaulted timestamp written by the SAME statement. Two candidates exist
-- in the code; neither may be ai_trades (independence rule).
--
--   copy_probes  -- probe_at is app-passed, and the table has no DB-stamped
--                   column written alongside it (its own DEFAULT never fires
--                   because a value is always supplied). DISQUALIFIED.
--   service_heartbeats -- beat_at is now() (C3) and detail is app-built JSON
--                   in the same statement. QUALIFIES ONLY IF some detail key
--                   holds an app-side time.
--
-- If nothing qualifies the answer is CLOCK_UNRESOLVED. It is not repaired by
-- guessing and not repaired by the audited system.
WITH hb AS MATERIALIZED (
  SELECT service, beat_at, detail FROM service_heartbeats
   WHERE detail IS NOT NULL AND jsonb_typeof(detail) = 'object'
), kv AS (
  SELECT service, beat_at, key, detail ->> key AS val
    FROM hb, LATERAL jsonb_object_keys(detail) AS key
   WHERE key ~* '(time|ts|stamp|_at$|epoch|clock)'
), parsed AS (
  SELECT service, beat_at, key, val,
         CASE
           WHEN val ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}'
             THEN val::timestamptz
           WHEN val ~ '^[0-9]{9,10}(\.[0-9]+)?$'
             THEN to_timestamp(val::float8)
           ELSE NULL
         END AS app_stamp
    FROM kv
)
SELECT count(*)                                     AS candidate_keys_seen,
       count(app_stamp)                             AS parsable_app_stamps,
       count(DISTINCT service) FILTER (WHERE app_stamp IS NOT NULL)
                                                    AS services_qualifying,
       round(min(extract(epoch FROM beat_at - app_stamp))::numeric, 3) AS min_skew_s,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY extract(epoch FROM beat_at - app_stamp))::numeric, 3)
                                                    AS p50_skew_s,
       round(max(extract(epoch FROM beat_at - app_stamp))::numeric, 3) AS max_skew_s,
       CASE WHEN count(app_stamp) = 0
            THEN 'CLOCK_UNRESOLVED -- NO INDEPENDENT C2<->C3 BRIDGE EXISTS '
                 'IN RETAINED DATA (0 witnesses: NOT TESTED, not zero skew)'
            ELSE 'BRIDGE FOUND -- skew is app-stamp to DB-stamp, one statement'
       END                                          AS bridge_verdict
  FROM parsed;

\echo ''
\echo '== 5. POPULATION ATTRITION U0 -> U4, with dollars =='
-- Every step prints what it drops. The two inherited selections are printed
-- beside the cohort rather than buried: copy_probes fires on BUY ONLY and only
-- when the detection was within 120 s, so U2 is biased toward our FASTEST
-- detections -- the direction that flatters us.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), t AS MATERIALIZED (
  SELECT id, ts, source, side, notional, condition_id, asset
    FROM trades WHERE whale_id IN (SELECT id FROM rn1)
), cp AS MATERIALIZED (
  SELECT trade_id, book_ok, error, reaction_s
    FROM copy_probes
   WHERE whale_id IN (SELECT id FROM rn1) AND trade_id IS NOT NULL
), mapped AS MATERIALIZED (
  SELECT DISTINCT condition_id FROM mirror_books WHERE lower(whale) = 'rn1'
  UNION
  SELECT DISTINCT condition_id FROM mirror_shadow WHERE lower(whale) = 'rn1'
), settled AS MATERIALIZED (
  SELECT mt.token_id
    FROM market_tokens mt JOIN markets m USING (condition_id)
   WHERE m.resolved AND mt.outcome_index IS NOT NULL
     AND m.resolved_prices IS NOT NULL
     AND jsonb_array_length(m.resolved_prices) > mt.outcome_index
), u AS (
  SELECT t.id, t.notional, t.condition_id, t.side,
         (t.ts IS NOT NULL AND t.source IN ('chain', 'poll'))       AS in_u1,
         (c.trade_id IS NOT NULL AND c.book_ok
          AND c.error IS NULL)                                      AS in_u2,
         (mp.condition_id IS NOT NULL)                              AS in_u3,
         (s.token_id IS NOT NULL)                                   AS in_u4,
         c.reaction_s
    FROM t
    LEFT JOIN cp c      ON c.trade_id = t.id
    LEFT JOIN mapped mp ON mp.condition_id = t.condition_id
    LEFT JOIN settled s ON s.token_id = t.asset
), steps(step_order, universe, events, conditions, notional) AS (
  SELECT 0, 'U0 RN1 fills retained', count(*), count(DISTINCT condition_id),
         round(sum(notional)::numeric, 2) FROM u
  UNION ALL
  SELECT 1, 'U1 + defensible source clock', count(*), count(DISTINCT condition_id),
         round(sum(notional)::numeric, 2) FROM u WHERE in_u1
  UNION ALL
  SELECT 2, 'U2 + exact fill-level book observation', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2)
    FROM u WHERE in_u1 AND in_u2
  UNION ALL
  SELECT 3, 'U3 + PMUS-mappable (EVER, coarse proxy)', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2)
    FROM u WHERE in_u1 AND in_u2 AND in_u3
  UNION ALL
  SELECT 4, 'U4 + settlement known', count(*), count(DISTINCT condition_id),
         round(sum(notional)::numeric, 2)
    FROM u WHERE in_u1 AND in_u2 AND in_u3 AND in_u4
  UNION ALL
  SELECT 5, 'COHORT U2 n U4 (the common-unit set)', count(*),
         count(DISTINCT condition_id), round(sum(notional)::numeric, 2)
    FROM u WHERE in_u1 AND in_u2 AND in_u4
)
SELECT universe, events, conditions, notional,
       events - lead(events) OVER (ORDER BY step_order)      AS events_dropped_next,
       notional - lead(notional) OVER (ORDER BY step_order)  AS notional_dropped_next
  FROM steps WHERE step_order <= 4
 UNION ALL
SELECT universe, events, conditions, notional, NULL, NULL
  FROM steps WHERE step_order = 5;

\echo ''
\echo '== 5b. THE INHERITED SELECTIONS, printed rather than buried =='
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), t AS MATERIALIZED (
  SELECT id, side, notional FROM trades WHERE whale_id IN (SELECT id FROM rn1)
), cp AS MATERIALIZED (
  SELECT trade_id, reaction_s, book_ok, error
    FROM copy_probes
   WHERE whale_id IN (SELECT id FROM rn1) AND trade_id IS NOT NULL
)
SELECT t.side                                              AS rn1_side,
       count(*)                                            AS fills,
       round(sum(t.notional)::numeric, 2)                  AS notional,
       count(c.trade_id)                                   AS with_a_probe,
       count(*) FILTER (WHERE c.book_ok)                   AS with_a_book,
       count(*) FILTER (WHERE c.error IS NOT NULL)         AS probe_errored,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY c.reaction_s)::numeric, 3)           AS reaction_p50_s,
       round(max(c.reaction_s)::numeric, 3)                AS reaction_max_s,
       CASE WHEN count(c.trade_id) = 0
            THEN 'NOT TESTED -- no probe fires on this side'
            ELSE 'probed cohort exists' END                AS note
  FROM t LEFT JOIN cp c ON c.trade_id = t.id
 GROUP BY t.side ORDER BY t.side;

\echo ''
\echo '== 6. WITNESS LEDGER -- every test in this run beside what it examined =='
-- A zero witness count is NOT TESTED. It is never PASS and never a silent
-- NOT APPLICABLE: the reason it could not fire is named in the last column.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), w(test_order, test_name, witnesses, why_if_zero) AS (
  SELECT 1, 'statement 0: evidence-gate fields measured', 18::bigint,
         'the registry is empty'
  UNION ALL
  SELECT 2, 'statement 2: RN1 fills with a source clock',
         (SELECT count(*) FROM trades
           WHERE whale_id IN (SELECT id FROM rn1) AND ts IS NOT NULL),
         'no RN1 fills retained'
  UNION ALL
  SELECT 3, 'statement 3: JSON bodies probed for a venue stamp',
         (SELECT count(*) FROM mirror_orders
           WHERE lower(whale) = 'rn1' AND receipt IS NOT NULL)
       + (SELECT count(*) FROM live_orders
           WHERE lower(COALESCE(whale_username, '')) = 'rn1' AND raw IS NOT NULL),
         'no receipt or raw body was ever stored'
  UNION ALL
  SELECT 4, 'statement 4: heartbeat rows with a JSON detail',
         (SELECT count(*) FROM service_heartbeats
           WHERE detail IS NOT NULL AND jsonb_typeof(detail) = 'object'),
         'no heartbeat carries a detail object'
  UNION ALL
  SELECT 5, 'statement 5: RN1 fills in U0',
         (SELECT count(*) FROM trades WHERE whale_id IN (SELECT id FROM rn1)),
         'no RN1 fills retained'
  UNION ALL
  SELECT 6, 'statement 5b: RN1 fills carrying a probe row',
         (SELECT count(*) FROM copy_probes
           WHERE whale_id IN (SELECT id FROM rn1) AND trade_id IS NOT NULL),
         'the probe lane never ran for RN1'
)
SELECT test_name, witnesses,
       CASE WHEN witnesses = 0 THEN 'NOT TESTED'
            ELSE 'TESTED' END          AS verdict,
       CASE WHEN witnesses = 0 THEN why_if_zero ELSE '' END AS reason_if_not_tested
  FROM w ORDER BY test_order;

\echo ''
\echo '== RUN 79 ENDS. No economic figure was computed. mirror_live=false. =='
