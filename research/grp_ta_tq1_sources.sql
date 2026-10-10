-- READ-ONLY. Root-cause audit, group truth-agents, part 1: the five TRUTH_QUORUM
-- sources (INTERNAL_LEDGER / VENUE_POSITIONS / VENUE_BALANCE / MARKET_DATA /
-- AUDREY_RECONCILIATION) at the instant of the read. SELECT only. Account
-- balances and positions are NEVER printed (counts and instants only).

\echo == 1 service heartbeats: status and age
SELECT service, status, beat_at, round(extract(epoch FROM (now() - beat_at))::numeric, 1) AS age_s
  FROM service_heartbeats
 ORDER BY service;

\echo == 2 mirror_shadow heartbeat detail keys (positions_source, status, refusal, owner_blocker)
SELECT status, beat_at,
       detail ->> 'refusal' AS refusal,
       detail ->> 'owner_blocker' AS owner_blocker,
       detail ->> 'positions_authority' AS positions_authority,
       detail ->> 'venue_confirmed' AS venue_confirmed,
       detail ->> 'abandoned' AS abandoned,
       detail ->> 'positions_unreadable' AS positions_unreadable,
       detail ->> 'credential_class' AS credential_class,
       detail ->> 'primary_refusal' AS primary_refusal,
       detail -> 'positions_source' AS positions_source,
       left(detail::text, 1800) AS detail_head
  FROM service_heartbeats WHERE service = 'mirror_shadow';

\echo == 3 execmirror_control (the lane switch)
SELECT enabled, stopped, flatten_on_stop, stop_done_at, cutover_at,
       account_fingerprint IS NOT NULL AS has_account_fingerprint,
       scale, max_order_usd, actor, revision, updated_at
  FROM execmirror_control WHERE id = 1;

\echo == 4 execmirror_snapshots: the account snapshot history (counts and instants only)
SELECT count(*) AS snapshots, min(at) AS oldest, max(at) AS newest,
       round(extract(epoch FROM (now() - max(at)))::numeric / 3600, 2) AS newest_age_h,
       count(DISTINCT account_fingerprint) AS fingerprints
  FROM execmirror_snapshots;

\echo == 5 newest 5 snapshots: shape only
SELECT snapshot_id, at,
       jsonb_array_length(balances) AS balance_rows,
       jsonb_array_length(positions) AS position_rows,
       open_orders,
       reconciliation ->> 'reconciled' AS reconciled,
       (SELECT count(*) FROM jsonb_object_keys(coalesce(reconciliation -> 'differences', '{}'::jsonb))) AS differences
  FROM execmirror_snapshots ORDER BY at DESC LIMIT 5;

\echo == 6 execmirror_events: kinds in the last 14 days with first and last instant
SELECT kind, count(*) AS n, min(at) AS first_at, max(at) AS last_at
  FROM execmirror_events
 WHERE at >= now() - interval '14 days'
 GROUP BY kind ORDER BY max(at) DESC LIMIT 40;

\echo == 7 newest 8 SNAPSHOT_FAILED events (error text head)
SELECT at, left(detail::text, 300) AS detail_head
  FROM execmirror_events WHERE kind = 'SNAPSHOT_FAILED'
 ORDER BY at DESC LIMIT 8;

\echo == 8 execmirror_fills: all-time and net-held groups
SELECT count(*) AS fills, count(DISTINCT group_id) AS groups, min(observed_at) AS first_fill, max(observed_at) AS last_fill
  FROM execmirror_fills;
SELECT count(*) AS groups_with_nonzero_net
  FROM (SELECT group_id,
               sum(CASE WHEN intent ILIKE '%SELL%' THEN -qty ELSE qty END) AS held
          FROM execmirror_fills GROUP BY 1) x
 WHERE held <> 0;

\echo == 9 smalllive_handoffs by state, smalllive_reconciliations by status
SELECT state, count(*) AS n, max(updated_at) AS newest FROM smalllive_handoffs GROUP BY 1 ORDER BY 1;
SELECT status, count(*) AS n, min(reconciled_at) AS oldest, max(reconciled_at) AS newest,
       round(extract(epoch FROM (now() - max(reconciled_at)))::numeric / 3600, 2) AS newest_age_h
  FROM smalllive_reconciliations GROUP BY 1 ORDER BY 1;

\echo == 10 execmirror_orders by state (all-time)
SELECT state, count(*) AS n, max(created_at) AS newest FROM execmirror_orders GROUP BY 1 ORDER BY 2 DESC;
