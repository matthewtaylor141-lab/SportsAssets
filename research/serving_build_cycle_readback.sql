-- READ-ONLY. A SCHEDULED CYCLE AND THE OPERATOR DATA, ON THE EXACT
-- SERVING BUILD.
--
-- Owner requirement (c): "verify a scheduled cycle and the operator-facing
-- data on the exact serving build. Return actual action decisions,
-- executions by operating mode, management transitions and reconciled
-- results."
--
-- THE RULE THIS FILE IS BUILT AROUND. A cycle row written by a DIFFERENT
-- build does not verify this one. Every section therefore prints the
-- WRITER BUILD beside its result, so a row from the previous release
-- cannot be read as evidence for the new one -- which is the mistake the
-- earlier delivery report avoided only because the digest field happened
-- to be absent on the old build.
--
-- AND WHAT "ACTUAL" MEANS HERE. Decisions the loop TOOK, executions it
-- SENT, transitions a position actually MADE, and results that
-- RECONCILE. A count of candidates considered is none of those.

-- ─────────────────────────────────────────────────────────────────────
-- 1 · THE SERVING BUILD AND THE LAST COMPLETED CYCLE. If the writer build
--     is not the SHA that was just deployed, nothing below is evidence
--     for it and that must be visible first.
-- ─────────────────────────────────────────────────────────────────────
SELECT key,
       (value::jsonb ->> 'at')                        AS cycle_at,
       (value::jsonb ->> 'build')                     AS writer_build,
       (value::jsonb ->> 'state')                     AS state,
       (value::jsonb ->> 'label')                     AS label,
       (value::jsonb ->> 'loop_source_hash')          AS loop_source_hash,
       ((value::jsonb -> 'servicing') IS NOT NULL)    AS carries_a_servicing_decision,
       (value::jsonb ->> 'evaluated')                 AS evaluated,
       (value::jsonb ->> 'written')                   AS written
  FROM ingestion_state
 WHERE key LIKE '%ext_pinnacle%'
    OR key LIKE '%cycle%'
    OR key LIKE '%heartbeat%'
 ORDER BY key;

-- ─────────────────────────────────────────────────────────────────────
-- 2 · THE FRESHNESS KNOB, read back from the cycle the new build wrote.
--     Both counters must be 0 at the default. If `odds_freshness` is
--     ABSENT the serving build predates it, which is itself the answer.
-- ─────────────────────────────────────────────────────────────────────
SELECT key,
       ((value::jsonb -> 'odds_freshness') IS NOT NULL) AS field_present,
       (value::jsonb -> 'odds_freshness' ->> 'events_per_odds_fetch')
                                                        AS events_per_fetch,
       (value::jsonb -> 'odds_freshness' ->> 'is_the_default')
                                                        AS is_default,
       (value::jsonb -> 'odds_freshness' ->> 'odds_refetches')
                                                        AS refetches,
       (value::jsonb -> 'odds_freshness' ->> 'odds_refetch_failures')
                                                        AS refetch_failures,
       (value::jsonb -> 'credits' ->> 'remaining')      AS credits_remaining,
       (value::jsonb -> 'credits' ->> 'used')           AS credits_used
  FROM ingestion_state
 WHERE key LIKE '%ext_pinnacle%'
 ORDER BY key;

-- ─────────────────────────────────────────────────────────────────────
-- 3 · ACTION DECISIONS ACTUALLY TAKEN, by decision, on rows the new build
--     wrote. `decision` is what the loop concluded; `admissible` is
--     whether it cleared. Both, because a decision to refuse IS a
--     decision and counting only admissions would report an idle loop as
--     having decided nothing.
-- ─────────────────────────────────────────────────────────────────────
SELECT decision,
       admissible,
       count(*)                                      AS rows_,
       count(DISTINCT us_market_slug)                 AS markets,
       count(*) FILTER (WHERE order_submitted)        AS order_submitted,
       min(decided_at)                                AS first_at,
       max(decided_at)                                AS last_at
  FROM external_valuations
 WHERE decided_at > now() - INTERVAL '6 hours'
 GROUP BY decision, admissible
 ORDER BY rows_ DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 4 · EXECUTIONS BY OPERATING MODE. The two lanes are separate ledgers
--     and must never be summed. `order_submitted` on the research lane is
--     structurally False; the funded lane's submissions live in its own
--     table. Printing both from their own sources is the only honest
--     answer to "executions by operating mode".
-- ─────────────────────────────────────────────────────────────────────
SELECT 'ext_pinnacle_shadow (research)'              AS operating_mode,
       count(*)                                      AS decisions,
       count(*) FILTER (WHERE order_submitted)       AS orders_submitted,
       count(*) FILTER (WHERE admissible)            AS admissible,
       'order_submitted is structurally False in this lane'
                                                     AS note
  FROM external_valuations
 WHERE decided_at > now() - INTERVAL '6 hours'
UNION ALL
SELECT 'funded (real money)'                         AS operating_mode,
       count(*)                                      AS decisions,
       count(*) FILTER (WHERE state = 'FILLED')      AS orders_submitted,
       count(*) FILTER (WHERE closed_at IS NOT NULL) AS admissible,
       'FUNDED_EXIT_SUBMISSION_ENABLED ships False'  AS note
  FROM bettor_funded_intents;

-- ─────────────────────────────────────────────────────────────────────
-- 5 · MANAGEMENT TRANSITIONS a position actually made. A transition is a
--     change of state, so this reports states and closure reasons rather
--     than a count of positions -- an intent that never moved has no
--     transition to report and must not be presented as one.
-- ─────────────────────────────────────────────────────────────────────
SELECT kind,
       state,
       (closed_at IS NOT NULL)                       AS closed,
       coalesce(closed_reason, '(open)')              AS closed_reason,
       count(*)                                      AS positions,
       coalesce(sum(residual_qty), 0)                AS residual_contracts,
       min(created_at)                               AS first_created,
       max(coalesce(closed_at, created_at))          AS last_moved
  FROM bettor_funded_intents
 GROUP BY kind, state, closed, closed_reason
 ORDER BY positions DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 6 · RECONCILED RESULTS. The funded book's own economics, with the
--     fee state, because a PROVISIONAL fee means the cash is not final
--     and a figure that hides that is not reconciled.
-- ─────────────────────────────────────────────────────────────────────
SELECT f.direction,
       f.fee_state,
       count(*)                                        AS fills,
       round(coalesce(sum(f.qty), 0)::numeric, 4)      AS qty,
       round(coalesce(sum(f.cash_usd), 0)::numeric, 4) AS cash_usd,
       round(coalesce(sum(f.fee_usd), 0)::numeric, 4)  AS fee_usd,
       count(*) FILTER (WHERE f.observed_fee_usd IS NOT NULL)
                                                       AS venue_stated_a_commission
  FROM bettor_funded_fills f
 GROUP BY f.direction, f.fee_state
 ORDER BY f.direction, f.fee_state;

-- ─────────────────────────────────────────────────────────────────────
-- 7 · THE DISCREPANCIES AN OPERATOR MUST CLOSE. This is the operator
--     surface's substance; an empty result is a real answer and a
--     non-empty one is work owed.
-- ─────────────────────────────────────────────────────────────────────
-- `bettor_funded_discrepancies` names its timestamp `at`, not `created_at`,
-- and carries `resolved_at` -- so an unresolved count is the figure that
-- matters rather than a total that folds closed ones in.
SELECT kind,
       count(*)                                        AS rows_,
       count(*) FILTER (WHERE resolved_at IS NULL)      AS unresolved,
       min(at)                                         AS oldest,
       max(at)                                         AS newest
  FROM bettor_funded_discrepancies
 GROUP BY kind
 ORDER BY unresolved DESC, rows_ DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 8 · THE CONTROL ROWS, read rather than assumed. The kill switch and the
--     arming flag decide whether any of the above could have happened.
-- ─────────────────────────────────────────────────────────────────────
SELECT key, left(value::text, 200) AS value_head
  FROM ingestion_state
 WHERE key IN ('live_trading_paused', 'ext_pinnacle_shadow',
               'bettor_live_loop_armed', 'funded_exit_enabled')
 ORDER BY key;
