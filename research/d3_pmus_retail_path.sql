-- READ-ONLY. SMALL LIVE PILOT V1, deliverable 3 audit: the Polymarket US
-- retail execution path. Every statement is a SELECT. No credential value is
-- read: credential rows below are names, shape enums and 8-character
-- fingerprint prefixes (sha256 of the key id) only.
--   P1 retail execution account (execmirror) control, emergency stop, caps
--   P2 newest authenticated account snapshots (identity fingerprint, buying power)
--   P3 execmirror events by kind and the newest events
--   P4 orders, fills, handoffs (venue writes ever made by the lane)
--   P5 process credential classes and PMUS census from the workers boot row
--   P6 red team CREDENTIAL_CLASSES receipts (newest), per process classes
--   P7 kill switch and global gate inputs
--   P8 governance: live approvals in force, live rule artifacts
--   P9 SMALL LIVE adapter (SHADOW) proposals: buying power current, plan exclusions
--   P10 execution intents: live eligible and refusals, admission facts and book verdicts
--   P11 PMUS book freshness: newest market plane priority universe snapshots
--   P12 lane loop health and funded account reconciliation reports

\echo P1 EXECMIRROR CONTROL (the actual lane switch, emergency stop, cap, scale; fingerprint prefix only)
SELECT enabled, stopped, stop_done_at, flatten_on_stop, scale, max_order_usd, rounding,
       cutover_at, left(account_fingerprint, 8) AS fp8, actor, revision, updated_at,
       baseline->>'at' AS baseline_at, baseline->'balances' AS baseline_balances,
       baseline->>'open_orders' AS baseline_open_orders,
       (SELECT count(*) FROM jsonb_object_keys(coalesce(baseline->'positions_net', '{}'::jsonb))) AS baseline_positions
  FROM execmirror_control;

\echo P2 EXECMIRROR SNAPSHOTS (authenticated account reads by the lane): count, newest, age in hours
SELECT count(*) AS snapshots, min(at) AS first_at, max(at) AS newest_at,
       round((extract(epoch FROM now() - max(at)) / 3600.0)::numeric, 2) AS newest_age_h
  FROM execmirror_snapshots;
SELECT snapshot_id, at, left(account_fingerprint, 8) AS fp8, balances,
       jsonb_array_length(positions) AS positions, open_orders, reconciliation
  FROM execmirror_snapshots ORDER BY at DESC LIMIT 3;

\echo P3 EXECMIRROR EVENTS by kind (all time) and the newest 25
SELECT kind, count(*) AS n, min(at) AS first_at, max(at) AS newest_at
  FROM execmirror_events GROUP BY 1 ORDER BY 4 DESC LIMIT 40;
SELECT event_id, at, kind, mirror_id, left(detail::text, 260) AS detail
  FROM execmirror_events ORDER BY at DESC LIMIT 25;

\echo P4 EXECMIRROR ORDERS by state role exclusion (venue order id present), FILLS, HANDOFFS
SELECT state, role, coalesce(exclusion, '-') AS exclusion, count(*) AS n,
       count(venue_order_id) AS with_venue_order_id, min(created_at) AS first_at, max(created_at) AS newest_at
  FROM execmirror_orders GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;
SELECT count(*) AS fills, coalesce(sum(qty), 0) AS qty, round(coalesce(sum(qty * price), 0)::numeric, 4) AS notional_usd,
       min(observed_at) AS first_fill, max(observed_at) AS newest_fill
  FROM execmirror_fills;
SELECT state, venue, count(*) AS handoffs, max(updated_at) AS newest FROM smalllive_handoffs GROUP BY 1, 2 ORDER BY 3 DESC;

\echo P5 WORKERS BOOT ROW: commit, venue writes lock, credential classes, PMUS census verdict (names and shape enums only)
SELECT value->>'at' AS boot_at, left(value->>'commit_sha', 12) AS commit_sha, value->>'venue_writes' AS venue_writes,
       value->'credential_classes' AS credential_classes,
       value->'pmus_credential_census'->>'verdict' AS census_verdict,
       value->'pmus_credential_census'->>'funded_slot_shape' AS funded_slot_shape,
       value->'pmus_credential_census'->'retail_ed25519_pairs_under_other_names' AS retail_pairs_elsewhere,
       value->'pmus_credential_census'->'funded_reconciliation'->>'state' AS funded_reconciliation
  FROM ingestion_state WHERE key = 'workers_boot';
SELECT p.key AS pair, p.value->>'shape' AS shape, p.value->>'role' AS role,
       p.value->'key_id_equals' AS key_id_equals, p.value->'secret_equals' AS secret_equals
  FROM ingestion_state i CROSS JOIN LATERAL jsonb_each(i.value->'pmus_credential_census'->'pairs') p
 WHERE i.key = 'workers_boot' ORDER BY 1;

\echo P6 RED TEAM CREDENTIAL_CLASSES receipts: newest 3 with blockers, implementation sha, per process classes, API census pairs
SELECT computed_at, left(implementation_sha, 12) AS impl_sha, status, blockers,
       evidence->'by_process' AS by_process, evidence->'verdicts' AS verdicts,
       evidence->'owner_actions' AS owner_actions
  FROM red_team_control_receipts WHERE control = 'CREDENTIAL_CLASSES'
 ORDER BY computed_at DESC LIMIT 3;
SELECT svc.key AS service, p.key AS pair, p.value->>'shape' AS shape, p.value->>'role' AS role,
       svc.value->>'verdict' AS census_verdict
  FROM (SELECT evidence FROM red_team_control_receipts WHERE control = 'CREDENTIAL_CLASSES'
         ORDER BY computed_at DESC LIMIT 1) r
       CROSS JOIN LATERAL jsonb_each(r.evidence->'pmus_census') svc
       CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(svc.value->'pairs') = 'object'
                                          THEN svc.value->'pairs' ELSE '{}'::jsonb END) p
 ORDER BY 1, 2;
SELECT status, count(*) AS receipts, min(computed_at) AS first_at, max(computed_at) AS newest_at
  FROM red_team_control_receipts WHERE control = 'CREDENTIAL_CLASSES'
   AND computed_at >= now() - interval '24 hours' GROUP BY 1 ORDER BY 1;

\echo P7 KILL SWITCH and gate inputs (ingestion_state live_trading_paused, mirror_loss_stop)
SELECT key, value::text AS value, jsonb_typeof(value) AS type FROM ingestion_state
 WHERE key IN ('live_trading_paused', 'mirror_loss_stop') ORDER BY 1;

\echo P8 GOVERNANCE: live approvals (all rows) and live rule artifacts (status, owner approval)
SELECT approval_id, subject_kind, subject_id, subject_version, left(config_sha256, 12) AS sha12,
       decision, approved_by, recorded_at
  FROM live_approvals ORDER BY recorded_at DESC LIMIT 20;
SELECT rule_id, version, status, left(sha256, 12) AS sha12, owner_approval_actor, owner_approved_at, status_changed_at
  FROM live_rule_artifacts ORDER BY rule_id, version;

\echo P9 SMALL LIVE ADAPTER (SHADOW) all time and 24 h: state, exclusion, buying power current, plan exclusion, cap
SELECT state, coalesce(exclusion, '-') AS exclusion, refs->>'buying_power_current' AS bp_current,
       coalesce(refs->>'plan_exclusion', '-') AS plan_exclusion, refs->>'max_order_usd' AS cap_usd,
       count(*) AS n, max(created_at) AS newest
  FROM canonical_intent_executions WHERE adapter = 'SMALL_LIVE'
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC LIMIT 25;
SELECT mode, count(*) AS n, count(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS n_24h
  FROM canonical_intent_executions WHERE adapter = 'SMALL_LIVE' GROUP BY 1;
SELECT created_at, state, exclusion, requested->>'qty' AS live_qty, requested->>'limit' AS limit_px,
       requested->>'wire' AS wire, requested->>'tif' AS tif, venue_params, left((refs->'detail')::text, 200) AS detail
  FROM canonical_intent_executions WHERE adapter = 'SMALL_LIVE' ORDER BY created_at DESC LIMIT 5;

\echo P10 EXECUTION INTENTS all time: live eligible, actual state, refusal; admission facts and book currency verdicts (24 h)
SELECT live_eligible, actual_state, coalesce(actual_refusal, '-') AS refusal, count(*) AS n, max(created_at) AS newest
  FROM execution_intents GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 20;
SELECT (evidence ? 'admission_facts') AS has_admission_facts,
       coalesce(evidence->'admission_facts'->'book'->'book_currency'->>'verdict', '-') AS book_verdict,
       count(*) AS n, max(created_at) AS newest
  FROM execution_intents WHERE created_at >= now() - interval '24 hours' GROUP BY 1, 2 ORDER BY 3 DESC;
SELECT count(*) AS intents_24h,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM decided_at - book_observed_at))::numeric, 2) AS book_age_at_decision_p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM decided_at - book_observed_at))::numeric, 2) AS book_age_at_decision_p90_s,
       count(*) FILTER (WHERE extract(epoch FROM decided_at - book_observed_at) <= 10) AS within_10s
  FROM execution_intents WHERE created_at >= now() - interval '24 hours' AND book_observed_at IS NOT NULL;

\echo P11 PMUS PRIORITY UNIVERSE: newest 4 market plane SNAPSHOT events (denominator, stream current, REST fallback, not current, rate)
SELECT e.at, pu->>'denominator' AS den, pu->>'current_pmx_stream' AS pmx_stream,
       pu->>'current_rest_fallback' AS rest_fallback, pu->>'not_current' AS not_current,
       pu->>'external_unavailable' AS external, pu->>'rate' AS rate,
       e.payload->'freshness'->'total_universe'->>'rate' AS total_rate
  FROM market_plane_events e
       CROSS JOIN LATERAL (SELECT e.payload->'freshness'->'priority_universe' AS pu) x
 WHERE e.kind = 'SNAPSHOT' AND e.at >= now() - interval '30 minutes'
 ORDER BY e.at DESC LIMIT 4;
SELECT service, status, beat_at, round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       detail->>'state' AS state, detail->>'symbols' AS symbols, detail->>'current_books' AS current_books
  FROM service_heartbeats WHERE service IN ('institutional_stream', 'pmx_stream', 'universal_market_plane', 'market_plane')
 ORDER BY 1;

\echo P12 LANE LOOP HEALTH (execmirror and actual lane) and FUNDED ACCOUNT reconciliation reports (newest 5)
SELECT process, loop_name, left(commit_sha, 7) AS commit, cadence_s, last_success_at,
       round(extract(epoch FROM now() - last_success_at)::numeric, 1) AS success_age_s,
       successes, errors, last_error_at, left(last_error, 120) AS last_error, left(detail::text, 160) AS detail
  FROM runtime_loop_health WHERE loop_name LIKE 'execmirror%' OR loop_name LIKE '%actual%'
 ORDER BY 1, 2;
SELECT recorded_at, venue, account_id, recorded_by, authoritative, would_be_eligible, blocking_count,
       left((report->'blocking')::text, 300) AS blocking
  FROM bettor_account_reconciliation_reports ORDER BY recorded_at DESC LIMIT 5;
