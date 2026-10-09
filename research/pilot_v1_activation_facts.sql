-- READ-ONLY. SMALL LIVE PILOT V1 -- facts for a separate, explicit owner
-- activation request (retail account identity, credential classes per
-- process, caps and limits in force, kill switches and their state, and the
-- execution / reconciliation evidence that exists for the Polymarket US
-- retail path). Every statement is a SELECT. No value of any credential is
-- read: the census rows below carry env NAMES and shape enums only, and the
-- account key fingerprint is printed as the 8-character prefix the Command
-- view itself shows (execmirror_view.account_fingerprint_prefix).
--   A  retail account identity (execution-mirror account) and snapshots
--   B  credential classes and the PMUS credential census per process
--   C  caps and limits in force (execmirror_control, small_live_control,
--      kalshi_smalllive_control, funded-stack approvals, ingestion_state)
--   D  kill switches: rows, values and their audit trail
--   E  execution evidence (all time) and shadow intents in the last 24 h
--   F  reconciliation evidence (Audrey, smalllive_*)
--   G  governance in force (live_approvals, live_rule_artifacts, cutover)

\echo A1 EXECUTION MIRROR CONTROL (the retail account the SMALL LIVE adapter and the ACTUAL lane read)
SELECT id, enabled, stopped, flatten_on_stop, stop_done_at, scale, rounding,
       max_order_usd, cutover_at, (account_fingerprint IS NOT NULL) AS fingerprint_recorded,
       left(account_fingerprint, 8) AS fingerprint_prefix, actor, revision, updated_at,
       baseline->>'at' AS baseline_at, baseline->'open_orders' AS baseline_open_orders,
       (SELECT count(*) FROM jsonb_object_keys(coalesce(baseline->'positions_net', '{}'::jsonb))) AS baseline_positions
  FROM execmirror_control ORDER BY id;

\echo A2 RETAIL ACCOUNT SNAPSHOTS (count, newest, fingerprints seen, newest USD balance fields)
SELECT count(*) AS snapshots, min(at) AS first_at, max(at) AS newest_at,
       round(extract(epoch FROM now() - max(at))::numeric, 0) AS newest_age_s,
       count(DISTINCT account_fingerprint) AS distinct_fingerprints,
       string_agg(DISTINCT left(account_fingerprint, 8), ',') AS fingerprint_prefixes
  FROM execmirror_snapshots;
SELECT s.at, left(s.account_fingerprint, 8) AS fingerprint_prefix, s.open_orders,
       jsonb_array_length(CASE WHEN jsonb_typeof(s.positions) = 'array' THEN s.positions ELSE '[]'::jsonb END) AS positions_rows,
       b->>'currency' AS currency, b->>'currentBalance' AS current_balance,
       b->>'buyingPower' AS buying_power, b->>'openOrders' AS open_orders_usd
  FROM (SELECT * FROM execmirror_snapshots ORDER BY at DESC LIMIT 1) s
  LEFT JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(s.balances) = 'array'
                                              THEN s.balances ELSE '[]'::jsonb END) b ON true;

\echo A3 CANONICAL ACCOUNT REGISTRY (bettor_desk_accounts; shadow desk books, not venue accounts unless stated)
SELECT account_id, desk_id, status, paused, accounting_status, opening_balance, opened_at, closed_at,
       left(note, 90) AS note
  FROM bettor_desk_accounts ORDER BY opened_at;

\echo B1 WORKERS BOOT: commit, venue-write process lock, credential classes per slot (names and classes only)
SELECT value->>'commit_sha' AS commit_sha, value->>'at' AS boot_at, value->>'venue_writes' AS venue_writes,
       left(value->>'lock_reason', 120) AS lock_reason
  FROM ingestion_state WHERE key = 'workers_boot';
SELECT c.key AS slot, c.value AS class
  FROM ingestion_state s, jsonb_each(coalesce(s.value->'credential_classes', '{}'::jsonb)) c
 WHERE s.key = 'workers_boot' ORDER BY 1;

\echo B2 WORKERS PMUS CREDENTIAL CENSUS: per pair label -> env names, role, shape (no values)
SELECT p.key AS pair, p.value->>'key_id_env' AS key_id_env, p.value->>'secret_env' AS secret_env,
       p.value->>'role' AS role, p.value->>'shape' AS shape,
       p.value->'key_id_equals' AS key_id_equals, p.value->'secret_equals' AS secret_equals
  FROM ingestion_state s, jsonb_each(coalesce(s.value->'pmus_credential_census'->'pairs', '{}'::jsonb)) p
 WHERE s.key = 'workers_boot' ORDER BY 1;
SELECT s.value->'pmus_credential_census'->>'version' AS census_version,
       s.value->'pmus_credential_census'->>'verdict' AS verdict,
       s.value->'pmus_credential_census'->>'funded_slot_shape' AS funded_slot_shape,
       s.value->'pmus_credential_census'->'retail_ed25519_pairs_under_other_names' AS retail_pairs_other_names,
       s.value->'pmus_credential_census'->'ed25519_material_under_unpaired_names' AS ed25519_unpaired,
       s.value->'pmus_credential_census'->'other_candidate_names' AS other_candidate_names,
       s.value->'pmus_credential_census'->'funded_reconciliation'->>'state' AS funded_reconciliation,
       s.value->'pmus_credential_census'->>'values_exposed' AS values_exposed
  FROM ingestion_state s WHERE s.key = 'workers_boot';

\echo B3 API PROCESS: newest CREDENTIAL_CLASSES red-team receipt, its status and blockers
SELECT computed_at, left(implementation_sha, 12) AS impl_sha, status, blockers
  FROM red_team_control_receipts WHERE control = 'CREDENTIAL_CLASSES'
 ORDER BY computed_at DESC LIMIT 1;
\echo B3b API PMUS CREDENTIAL CENSUS (from that receipt): per pair label -> env names, role, shape
SELECT p.key AS pair, p.value->>'key_id_env' AS key_id_env, p.value->>'secret_env' AS secret_env,
       p.value->>'role' AS role, p.value->>'shape' AS shape
  FROM (SELECT evidence FROM red_team_control_receipts WHERE control = 'CREDENTIAL_CLASSES'
         ORDER BY computed_at DESC LIMIT 1) r,
       jsonb_each(coalesce(r.evidence->'pmus_census'->'sportsassets-api'->'pairs', '{}'::jsonb)) p
 ORDER BY 1;
SELECT r.evidence->'pmus_census'->'sportsassets-api'->>'verdict' AS api_verdict,
       r.evidence->'pmus_census'->'sportsassets-api'->>'funded_slot_shape' AS api_funded_slot_shape,
       r.evidence->'pmus_census'->'sportsassets-api'->'retail_ed25519_pairs_under_other_names' AS api_retail_pairs_other_names,
       r.evidence->'pmus_census'->'sportsassets-api'->'other_candidate_names' AS api_other_candidate_names,
       r.evidence->'pmus_census'->'sportsassets-api'->'funded_reconciliation'->>'state' AS api_funded_reconciliation
  FROM (SELECT evidence FROM red_team_control_receipts WHERE control = 'CREDENTIAL_CLASSES'
         ORDER BY computed_at DESC LIMIT 1) r;
\echo B3c API credential classes per slot as the receipt recorded them (top-level evidence keys other than the census)
SELECT e.key, CASE WHEN jsonb_typeof(e.value) IN ('object', 'array') THEN left(e.value::text, 400) ELSE e.value #>> '{}' END AS value
  FROM (SELECT evidence FROM red_team_control_receipts WHERE control = 'CREDENTIAL_CLASSES'
         ORDER BY computed_at DESC LIMIT 1) r, jsonb_each(r.evidence) e
 WHERE e.key <> 'pmus_census' ORDER BY 1;

\echo C1 SMALL LIVE CONTROL (mode, halt) and its audit events, all time
SELECT id, mode, halted, halted_at, halt_reason, halt_parity_id, cleared_by, cleared_at, updated_at
  FROM small_live_control ORDER BY id;
SELECT action, count(*) AS events, min(at) AS first_at, max(at) AS last_at
  FROM small_live_control_events GROUP BY 1 ORDER BY 1;

\echo C2 KALSHI SMALL LIVE CONTROL (must stay disabled)
SELECT id, enabled, stopped, kalshi_env, (key_fingerprint IS NOT NULL) AS key_recorded, reconciliation_id,
       scale, max_order_usd, cutover_at, actor, revision, updated_at
  FROM kalshi_smalllive_control ORDER BY id;

\echo C3 INGESTION_STATE SWITCHES AND FUNDED-STACK RECORDS (key, type, value for booleans and small objects)
SELECT key, jsonb_typeof(value) AS type,
       CASE WHEN key IN ('live_trading_paused', 'mirror_loss_stop', 'bettor_live_observation',
                         'bettor_funded_limits_approved', 'bettor_funded_account_binding')
            THEN left(value::text, 600)
            ELSE '(present; content not printed)' END AS value
  FROM ingestion_state
 WHERE key IN ('live_trading_paused', 'mirror_loss_stop', 'bettor_live_observation',
               'bettor_funded_account_binding', 'bettor_funded_limits_approved',
               'bettor_funded_authorization', 'bettor_funded_owner_authorization')
 ORDER BY key;
\echo C3b owner authorization record, selected non-secret fields only (if present)
SELECT value->>'account_id' AS account_id, value->>'venue' AS venue, value->>'status' AS status,
       value->>'expires_at' AS expires_at, value->>'authorization_id' AS authorization_id
  FROM ingestion_state WHERE key = 'bettor_funded_owner_authorization';
SELECT count(*) AS owner_authorization_audit_rows FROM bettor_funded_owner_authorization_audit;

\echo D1 KILL-SWITCH AUDIT: execution-mirror control events, all time (kind, count, first, last)
SELECT kind, count(*) AS events, min(at) AS first_at, max(at) AS last_at
  FROM execmirror_events
 WHERE kind LIKE 'CONTROL_%' OR kind IN ('EMERGENCY_STOP_DONE', 'STOP_CANCEL_ALL_FAILED',
                                         'FLATTEN_FAILED', 'HALTED_ACCOUNT_CHANGED')
 GROUP BY 1 ORDER BY 1;
\echo D1b the ten newest control events (actor names only)
SELECT at, kind, detail->>'actor' AS actor, detail->>'flatten' AS flatten,
       detail->>'acknowledge_existing' AS acknowledge_existing
  FROM execmirror_events WHERE kind LIKE 'CONTROL_%' ORDER BY at DESC LIMIT 10;
\echo D1c EMERGENCY_STOP_DONE detail (cancelled count, closed slugs) newest three
SELECT at, detail->>'cancelled' AS cancelled, detail->'closed' AS closed
  FROM execmirror_events WHERE kind = 'EMERGENCY_STOP_DONE' ORDER BY at DESC LIMIT 3;

\echo E1 RETAIL VENUE ORDERS, ALL TIME (execmirror_orders by state and role; venue ids; fills)
SELECT state, role, count(*) AS orders, count(venue_order_id) AS with_venue_order_id,
       coalesce(sum(cum_qty), 0) AS cum_qty, min(created_at) AS first_at, max(created_at) AS last_at
  FROM execmirror_orders GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;
SELECT count(*) AS execmirror_fills_all_time, coalesce(sum(qty), 0) AS qty,
       coalesce(round(sum(qty * price)::numeric, 4), 0) AS notional_usd,
       coalesce(round(sum(fee_usd)::numeric, 4), 0) AS fees_usd, min(observed_at) AS first_at, max(observed_at) AS last_at
  FROM execmirror_fills;
SELECT exclusion, count(*) AS rows, max(created_at) AS last_at
  FROM execmirror_orders WHERE state = 'EXCLUDED' GROUP BY 1 ORDER BY 2 DESC LIMIT 15;
\echo E1b orders that carry a venue order id, newest ten (no account data, ids truncated)
SELECT created_at, role, state, left(venue_order_id, 10) AS venue_order_id_prefix, us_market_slug,
       live_qty, wire_price, cum_qty, avg_px, fees_usd, execution_intent_id IS NOT NULL AS from_intent
  FROM execmirror_orders WHERE venue_order_id IS NOT NULL ORDER BY created_at DESC LIMIT 10;

\echo E2 SMALL LIVE VENUE LIFECYCLE (small_live_order_events) and canonical adapter rows, all time
SELECT count(*) AS small_live_order_events_all_time, max(observed_at) AS newest FROM small_live_order_events;
SELECT adapter, mode, state, count(*) AS rows, min(created_at) AS first_at, max(created_at) AS last_at
  FROM canonical_intent_executions GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo E3 SHADOW (SMALL_LIVE) PROPOSALS IN THE LAST 24 H: state, exclusion, plan verdict, would-be size
SELECT state, coalesce(exclusion, '-') AS exclusion, coalesce(refs->>'plan_exclusion', '-') AS plan_exclusion,
       refs->>'plan_state' AS plan_state, refs->>'max_order_usd' AS max_order_usd_read,
       refs->>'buying_power_current' AS buying_power_current, capital_scale,
       count(*) AS rows,
       min((requested->>'qty')::numeric) AS min_live_qty, max((requested->>'qty')::numeric) AS max_live_qty,
       round(avg((requested->>'qty')::numeric * (requested->>'wire_price')::numeric), 4) AS avg_would_be_usd_long_basis,
       max(created_at) AS newest
  FROM canonical_intent_executions
 WHERE adapter = 'SMALL_LIVE' AND created_at >= now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY rows DESC LIMIT 25;
\echo E3b ten newest SMALL_LIVE shadow proposals (would-be order, its verdict)
SELECT e.created_at, e.intent_kind, e.state, e.exclusion, e.refs->>'plan_exclusion' AS plan_exclusion,
       e.requested->>'action' AS action, e.requested->>'us_market_slug' AS slug,
       e.requested->>'qty' AS live_qty, e.requested->>'wire_price' AS wire,
       e.requested->>'strategy' AS strategy, e.requested->>'strategy_version' AS version,
       e.refs->'detail'->>'cost_usd' AS cost_usd
  FROM canonical_intent_executions e WHERE e.adapter = 'SMALL_LIVE'
 ORDER BY e.created_at DESC LIMIT 10;
\echo E3c PAPER adapter rows in the last 24 h (for scale), and parity states
SELECT state, count(*) AS rows FROM canonical_intent_executions
 WHERE adapter = 'PAPER' AND created_at >= now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;
SELECT parity_state, intent_kind, count(*) AS rows, max(created_at) AS newest,
       count(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS rows_24h
  FROM live_parity_ledger GROUP BY 1, 2 ORDER BY 1, 2;

\echo E4 EXECUTION INTENTS (ACTUAL lane) last 24 h and all time: actual state, refusal, would-be live qty
SELECT actual_state, coalesce(actual_refusal, '-') AS actual_refusal, strategy, policy_version,
       count(*) AS intents, count(actual_mirror_id) AS with_mirror_id,
       min(live_qty) AS min_live_qty, max(live_qty) AS max_live_qty,
       round(avg(paper_target_qty)::numeric, 1) AS avg_paper_qty,
       round(avg(live_qty * wire_price)::numeric, 4) AS avg_would_be_usd_long_basis,
       max(created_at) AS newest
  FROM execution_intents WHERE created_at >= now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY intents DESC LIMIT 20;
SELECT actual_state, count(*) AS intents_all_time, count(actual_mirror_id) AS with_mirror_id,
       count(*) FILTER (WHERE live_eligible) AS live_eligible, min(created_at) AS first_at, max(created_at) AS last_at
  FROM execution_intents GROUP BY 1 ORDER BY 2 DESC;
\echo E4b live-eligible intents ever (strategy, version, refusal) -- admission evidence
SELECT strategy, policy_version, actual_state, coalesce(actual_refusal, '-') AS refusal, count(*) AS intents, max(created_at) AS newest
  FROM execution_intents WHERE live_eligible GROUP BY 1, 2, 3, 4 ORDER BY intents DESC LIMIT 15;
\echo E4c admission verdicts recorded on intents in the last 24 h (first failing requirement)
SELECT coalesce(live_eligibility->'admission'->>'verdict', '-') AS admission_verdict,
       coalesce(live_eligibility->'admission'->>'refusal', live_eligibility->'admission'->>'first_failure', '-') AS first_failure,
       count(*) AS intents
  FROM execution_intents WHERE created_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;

\echo E5 LEGACY LIVE COPY LANE (live_orders) and FUNDED STACK (bettor_funded_intents): any real order recently
SELECT count(*) AS live_orders_all_time, max(placed_at) AS newest_placed_at,
       count(*) FILTER (WHERE placed_at >= now() - interval '30 days') AS placed_30d,
       count(*) FILTER (WHERE placed_at >= now() - interval '30 days' AND status IN ('filled', 'settled', 'merged')) AS filled_30d
  FROM live_orders;
SELECT venue_class, state, count(*) AS intents, count(venue_order_id) AS with_venue_order_id, max(created_at) AS newest
  FROM bettor_funded_intents GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;

\echo F1 AUDREY RECONCILIATION (smalllive_reconciliations): status counts, newest, venue
SELECT venue, status, count(*) AS groups, max(reconciled_at) AS newest_reconciled_at,
       round(extract(epoch FROM now() - max(reconciled_at))::numeric, 0) AS newest_age_s,
       count(*) FILTER (WHERE reconciled_at >= now() - interval '24 hours') AS reconciled_24h
  FROM smalllive_reconciliations GROUP BY 1, 2 ORDER BY 1, 2;
\echo F1b discrepancy kinds named in the newest reconciliations (top codes)
SELECT coalesce(d->>'code', d->>'kind', left(d::text, 60)) AS discrepancy, count(*) AS n
  FROM smalllive_reconciliations r,
       jsonb_array_elements(CASE WHEN jsonb_typeof(r.discrepancies) = 'array' THEN r.discrepancies ELSE '[]'::jsonb END) d
 WHERE r.reconciled_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 12;
\echo F2 ACTUAL POSITIONS (smalllive_handoffs) and Xavier reviews of them, all time
SELECT venue, state, count(*) AS handoffs, coalesce(sum(live_held), 0) AS live_held, max(updated_at) AS newest
  FROM smalllive_handoffs GROUP BY 1, 2 ORDER BY 1, 2;
SELECT count(*) AS smalllive_reviews_all_time, max(reviewed_at) AS newest FROM smalllive_reviews;
\echo F3 execution-mirror tick health (API process)
SELECT process, loop_name, left(commit_sha, 12) AS commit, cadence_s, last_success_at,
       round(extract(epoch FROM now() - last_success_at)::numeric, 1) AS success_age_s, successes, errors,
       left(last_error, 120) AS last_error
  FROM runtime_loop_health WHERE loop_name LIKE 'execmirror%' OR loop_name LIKE '%actual%' ORDER BY 1, 2;

\echo G1 LIVE GOVERNANCE: owner approvals (policy versions, live gates) and live rule artifacts
SELECT subject_kind, subject_id, subject_version, decision, approved_by, recorded_at, left(config_sha256, 12) AS config_sha
  FROM live_approvals ORDER BY recorded_at DESC LIMIT 20;
SELECT count(*) AS live_approvals_rows FROM live_approvals;
SELECT rule_id, version, status, created_by, owner_approval_actor, owner_approved_at, status_changed_at, left(sha256, 12) AS sha
  FROM live_rule_artifacts ORDER BY status_changed_at DESC LIMIT 10;
\echo G2 LIVE PARITY CUTOVERS (release rows; SMALL LIVE mode and capital flag at each)
SELECT cutover_id, left(release_sha, 12) AS release_sha, left(decision_logic_hash, 12) AS logic_hash,
       small_live_mode, small_live_halted, capital_activated, recorded_by, recorded_at
  FROM live_parity_cutover ORDER BY recorded_at DESC LIMIT 5;
\echo G3 hook installs by process and commit, newest five
SELECT process, left(commit_sha, 12) AS commit, hooks, installed_at
  FROM live_parity_hook_installs ORDER BY installed_at DESC LIMIT 5;
