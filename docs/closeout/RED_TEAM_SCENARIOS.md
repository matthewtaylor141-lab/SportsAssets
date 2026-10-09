# BETTOR red-team scenarios and controls (RC6 lane `rc6/redteam-scenarios`)

Two questions are answered here, both from machine evidence:

1. **Which red-team controls fail on production, and why.** Each failing
   control of the scorecard's Red-team category (`backend/tools/scorecard_14.py`,
   `red_team.json` → `readiness.controls`) is classified by its exact rule and
   evidence path as one of: **software defect** (fixed here or by a named RC6
   lane), **forward evidence** (count needed, count now, accrual rate, earliest
   date), **economic** (a correct refusal of a strategy with no proven edge),
   or **owner action** (named exactly). No control, threshold or blocker was
   relaxed; a correctly enforced refusal is reported as such, beside the
   capability it leaves incomplete.
2. **Do the owner's seven adversarial scenarios hold.** Each scenario maps to
   the tests that prove it and the control that reads it in production. Cases
   that were missing were written; every case that failed was a defect and was
   fixed in this lane.

Production baseline: RC5, release `69a8a07e5335864bd3d70f7160494aed13305fcc`,
pm-acceptance run 37836393458 (2026-10-08 ~20:03Z). Read-only SQL readbacks:
`research/rc6_redteam_controls.sql` (research-sql run 37875195354) and
`research/rc6_redteam_karen_blocks.sql` (run 37875350538), both on
`claude/p0-closeout`, 2026-10-09 ~02:35Z. Code base: RC6 candidate `412c4962`.

Hard boundaries in force for every row: SMALL LIVE = SHADOW; Kalshi live money
NOT ACTIVATED; Adriana SHADOW ONLY; no order, cancel, funding, capital or
authority path added; no limit, SLA, gate or threshold changed; historical
PAPER untouched.

## 1. The Red-team category on production (RC5): 6 / 16 = 0.375

| Unit | Production status and blockers | Class | What decides it |
|---|---|---|---|
| ATTRIBUTION | GREEN | pass | — |
| CANONICAL_EXPOSURE | GREEN | pass | — |
| FEE_EVIDENCE | GREEN | pass | — |
| SETTLEMENT_CERTIFICATES | GREEN | pass | — |
| UI_TRUTH | GREEN | pass | — |
| VENUE_HEALTH | GREEN (held 3/3, Kalshi WS 145/145; priority 121/192 is reported, not this control's gate) | pass | — |
| CAPACITY | RED `NO_POSITIVE_CAPACITY` | **economic** | see 1.1 |
| PROFIT_BREAKERS | RED `MECHANISM_DISABLED:{DIRECTIONAL,EXECUTION_ALPHA,XAVIER_MANAGEMENT}`, `NO_MECHANISM_ELIGIBLE` | **economic** | see 1.2 |
| DIGITAL_TWIN | RED `FEWER_THAN_100_FRESH_ORDERS:7` | **forward** (rate-limited by economics) | see 1.3 |
| KAREN_VALUE | UNKNOWN `KAREN_COUNTERFACTUALS_UNMEASURED` | **software defect (fixed here)** + **owner policy** | see 1.4 |
| MULTIPLE_TESTING | RED `DSR_NOT_ACCEPTABLE`, `NO_PREREGISTERED_CANDIDATE_SET`, `PBO_NOT_ACCEPTABLE` | **capability incomplete** + **owner policy** + economic | see 1.5 |
| SAMPLE_INTEGRITY | RED `NO_EVENT_CLUSTERED_PARTITION_REGISTERED` | **capability incomplete** + **owner policy** | see 1.5 |
| CREDENTIAL_CLASSES | RED `CREDENTIAL_CLASS_MISMATCH:PMUS:POLYMARKET_EXCHANGE_RSA_M2M` | **owner action** (correct refusal) | see 1.6 |
| TRUTH_QUORUM | RED `MISSING_SOURCE:{AUDREY_RECONCILIATION,VENUE_BALANCE,VENUE_POSITIONS}`, `STALE_SOURCE:{AUDREY_RECONCILIATION,VENUE_BALANCE}` | **owner action** + RC6 lane + verify after deploy | see 1.7 |
| MIGRATION_INTEGRITY | UNKNOWN `FRESH_DB_RESULT_NOT_IN_THIS_PROCESS` | software, **fixed by the RC6 deploy-integrity lane** (judge's fresh-DB half) | proven only by the RC6 packet's `fresh_db.json` |
| RELEASE | UNKNOWN `NO_RELEASE_RECEIPT_FOR_THE_RUNNING_SHA` | software, **fixed by the RC6 judge / release lane** (`release_verdict.json`) | proven only by the RC6 packet |

Ceiling without owner action and without positive economics: the six passing
units plus MIGRATION_INTEGRITY and RELEASE after the RC6 release = **8 / 16 =
0.50**. The category needs 16 / 16 for >= 95 % (15 / 16 = 0.9375). It cannot
reach the target until (a) the owner provisions the PMUS retail key and
decides the Karen and preregistration policies below, **and** (b) the
strategies show positive, statistically supported edge (CAPACITY,
PROFIT_BREAKERS, the DSR half of MULTIPLE_TESTING). Profitability is never
manufactured; those three stay RED until the economics change.

### 1.1 CAPACITY — economic

Rule: `redteam.readiness.capacity_points` (14-day `paper_profitability_evaluations`,
event-clustered 90 % lower bound of EV per contract per size bucket) →
`red_team.capacity_guard.capacity_frontier` (a bucket counts only with
lower-bound EV > 0, fill probability >= 0.80 and expected net > 0).
Production (C1/C2): 1,719 evaluations in 14 days, **0** with EV per contract >
0; every bucket's mean is negative (1-10: −0.188 $/contract over 201 rows /
66 fixtures; 11-50: −0.227; 51-100: −0.222; 101-500: −0.202; 501+: −0.275;
summed all-in EV −$364,243). The refusal is correct. No date: it passes only
when a size bucket shows a positive lower bound.

### 1.2 PROFIT_BREAKERS — economic

Rule: `redteam.controls.profit_breakers` (per mechanism, residual realized −
expected per independent event, 90 % upper bound; a sleeve with upper bound <
0 over >= 30 events is DISABLED; GREEN needs one ELIGIBLE sleeve and none
DISABLED). Production: DIRECTIONAL upper bound −1.44 $/event (128 events,
residual −$21,609), EXECUTION_ALPHA −32.97 (128, −$5,419), XAVIER_MANAGEMENT
−34.83 (128, −$19,619); the arbitrage sleeves have **0** independent
observations (Adriana is SHADOW ONLY by directive, so no arbitrage settles in
PAPER). Correct refusal; no date.

### 1.3 DIGITAL_TWIN — forward evidence, accruing only through exits

Rule: `completion.evidence.read_twin` replays marketable (IOC / FOK) PAPER
ENTRY / EXIT / REDUCE orders that became eligible **after** the diagnosis
window end 2026-10-07T14:56:26Z; `redteam.controls.twin` needs >= 100 compared
(TWIN_MIN_COMPARED), agreement >= 0.95, optimistic false-fill rate <= 0.01, 0
lookahead (thresholds frozen, unchanged). Production: **7 compared, 7 matched**
(agreement 1.0, 0 false fills, 0 lookahead). T1: 3 orders on 10-07 (9.1 h after
the window end) and 4 on 10-08; the last became eligible 10-08T08:47Z and none
in the following ~17.8 h; **all 7 are EXIT IOC** — no ENTRY order has been
placed since the window end (T3: the only other orders are 175 resting GTD
STANDING_PROTECTION orders, which the marketable replay does not model and
must not count). Entries are refused upstream because no evaluation has
positive EV (1.1). Count needed: **93 more**. Mean rate since the window end
4.7 / day → earliest **~2026-10-28** at that rate; at the rate of the last 18 h
(0 / day) it never passes. Before the window (T2) the same predicate accrued
7–586 / day, all of it entry-driven, so the date moves forward only if entries
resume (an economic event) or exit volume rises. Readback that proves it:
`red_team.json` → `readiness.controls.DIGITAL_TWIN.evidence.compared` >= 100
with agreement >= 0.95.

### 1.4 KAREN_VALUE — software defect fixed; then an owner policy question

Rule: saved loss − false-block cost from the twin's `KAREN_BLOCK_ACCEPTED`
world (`twin.scorecards.karen` → `twin_agent_scorecards`).

**Defect (fixed in this lane).** The red-team reader asked for
`book = 'PAPER'`; the twin writes these metrics in book `COUNTERFACTUAL` (a
metric derived from a twin world names its basis book in the metric name).
K1/K2: 43 twin runs (2026-10-04 .. 10-09), every `loss_avoided_paper_basis` /
`profit_sacrificed_paper_basis` row is `COUNTERFACTUAL`. The control could
therefore **never** turn GREEN, whatever the twin measured (proved on base:
40 measured blocked positions read back as `(None, None)`). It also mixed the
two metrics across runs (DISTINCT ON metric), ignored the twin's own
`INSUFFICIENT_SAMPLE` verdict, and printed `"0"` for unmeasured values. Now:
both metrics from the newest run, book COUNTERFACTUAL, GREEN only when both are
`MEASURED`, otherwise UNKNOWN with the twin's own status and reason named
(`KAREN_TWIN_UNAVAILABLE:<metric>:<reason>`,
`KAREN_TWIN_INSUFFICIENT_SAMPLE:<metric>:<n>`), unmeasured values `null`.

**What remains (not forward).** Every Karen row of all 43 runs is
`UNAVAILABLE: NO_BLOCKED_POSITION_SCORED_IN_BOTH_WORLDS`. B1/B2: Karen has
4,701 challenges (4,424 Xavier reviews, 275 Audrey findings, 2 artifacts), all
UPHELD, **0 blocked** — `blocked` is set only when a loop PEER_CHALLENGE is
REFUTED, and she holds no blocking authority over entries
(`test_karen_identity_and_no_authority`). Her counterfactual is defined over
positions she blocked; she blocks none, so waiting produces nothing. **Owner
decision:** either give Karen a PAPER-only blocking role whose blocks the twin
can score (an authority change only the owner can make), or amend the
definition of Karen's value (a policy amendment). After the fix the unit is
classed FAIL, not FORWARD, which is the truthful class.

### 1.5 MULTIPLE_TESTING and SAMPLE_INTEGRITY — capability incomplete, owner policy first

Rule: `redteam.controls.holdout_registry` reads `red_team_holdout_registry`
(migration 315: PREREGISTER / HOLDOUT_OPEN / SELECTION rows, append-only);
MULTIPLE_TESTING needs a preregistered candidate set, no post-holdout
selection, `pbo_ok` and `dsr_ok`; SAMPLE_INTEGRITY needs event-clustered
train / test / holdout partitions registered (independent events are already
267 >= 100; row:event ratio 2.13).

Production H1: the registry holds **0 rows**. No code in the repository writes
it, and the reader never computes `pbo_ok` / `dsr_ok` (no PBO or DSR estimator
exists), so MULTIPLE_TESTING cannot be GREEN under any evidence: the capability
is incomplete. The refusal itself is correct — nothing has been preregistered.
It was **not** built in this lane because its first inputs are owner decisions:
the owner's package asks for "PBO/DSR controls for strategy mining where
applicable" without thresholds, and its own rule is that any threshold must be
statistically justified and frozen **before** results are seen. **Owner
decisions, in order:** (1) freeze a PBO maximum and a DSR minimum; (2) name the
study — the candidate set (e.g. the strategy tournament's) and the
event-clustered partitions — and register it before its holdout is read. Then
the software (a preregistration writer, CSCV PBO and deflated-Sharpe
estimators over the registered partitions) can be built and tested, and only
then can the controls pass — the DSR half additionally needs a candidate with
positive, significant deflated Sharpe (economic; every mechanism above has a
negative residual).

### 1.6 CREDENTIAL_CLASSES — owner action (refusal correctly enforced)

`PMUS_KEY_ID` / `PMUS_SECRET_KEY` on sportsassets-api **and**
sportsassets-workers hold the PMX institutional RSA client
(`PMUS_KEY_ID == PMX_CLIENT_ID`, an RSA PEM), not a Polymarket US retail
Ed25519 key. Every funded reader refuses by name before any request
(`PMUS_SECRET_SLOT_HOLDS_NO_ED25519_KEY`; `test_pmus_funded_readers_refuse`).
**Owner action:** create a retail API key at polymarket.us/developer for the
funded account and enter its key id and Ed25519 secret in `PMUS_KEY_ID` /
`PMUS_SECRET_KEY` on both services. Affected checks: CREDENTIAL_CLASSES,
TRUTH_QUORUM (VENUE_POSITIONS), Archer `retail_venue_confirmed`, Kalshi card
`credential_class_control`. With this lane deployed the control also names the
same root cause by key (`CREDENTIAL_REUSED_ACROSS_VENUES:api:PMUS_SECRET_KEY=
PMX_PRIVATE_KEY_B64` when the slot holds the PMX key pair); the remedy is the
same single action.

### 1.7 TRUTH_QUORUM — owner action, an RC6 lane, and a check after deploy

Sources required current (<= 900 s): INTERNAL_LEDGER, VENUE_POSITIONS,
VENUE_BALANCE, MARKET_DATA, AUDREY_RECONCILIATION. Production Q1:
`smalllive_reconciliations` 8 rows, newest **518,113 s (~6.0 days)** old;
`execmirror_snapshots` 29 rows, newest **518,118 s** old — both last written
when the execution-mirror lane was stopped by owner instruction
(2026-10-03T02:39Z). VENUE_POSITIONS is absent because the funded slot cannot
read (1.6). AUDREY_RECONCILIATION while the lane is stopped is the RC6
archer-lifecycle lane's change (deployed with RC6). VENUE_BALANCE: if the RC6
packet still shows `STALE_SOURCE:VENUE_BALANCE`, a read-only balance snapshot
of the execution-mirror account while the lane is stopped is the remaining
software follow-up (not in this lane). Readback: `red_team.json` →
`readiness.controls.TRUTH_QUORUM.blockers`.

## 2. The seven adversarial scenarios

`test_red_team_scenarios.py` holds the cases that were missing (★ = failed on
base `412c4962`, a defect fixed in this lane). `C01`–`C30` are
`tests/test_red_team_chaos.py::test_cNN_*`.

### 2.1 Stale data — never routed, never re-stamped

| Case | Test | Control / gate in production |
|---|---|---|
| a stale cheapest route loses to the current one | `test_red_team_chaos.py::test_c04_a_stale_cheapest_route_loses_to_the_current_one` | canonical routing (`max_age_s`) |
| held book stale stays RED whatever the global rate | `test_red_team_chaos.py::test_c21_held_stale_stays_red_whatever_the_global_rate` | VENUE_HEALTH freshness (held) |
| a stale cached green metric shows STALE / UNKNOWN | `test_red_team_chaos.py::test_c22_a_cached_green_metric_that_is_stale_shows_stale` | UI_TRUTH |
| stale truth sources block new capital after a restart | `test_red_team_chaos.py::test_c26_after_a_restart_no_new_capital_until_truth_quorum_returns` | TRUTH_QUORUM |
| a future book or a backwards venue clock refuses | `test_red_team_chaos.py::test_c14_a_future_book_or_a_backwards_venue_clock_refuses`; `test_a_future_stamp_is_never_fresh.py` (4 tests) | VENUE_HEALTH |
| no default age; 90 s book stale at a 10 s bound | `test_bettor_market_stream.py::TestNoDefaultAge` (3 tests) | PMUS stream eligibility |
| PMX silence / an old snapshot is stale | `test_institutional_stream.py::test_silence_past_the_liveness_bound_is_stale`, `::test_a_snapshot_past_the_bound_is_stale_even_with_heartbeats` | VENUE_HEALTH (PMX) |
| a quote the provider delivered stale is never refreshed into freshness | `test_quote_stale_on_arrival_is_not_self_inflicted.py::test_a_quote_the_provider_delivered_stale_is_never_refreshed` | entry freshness |
| a GAP Kalshi book is written unroutable with no new `observed_at`; only CURRENT books are re-asserted | `test_red_team_scenarios.py::test_stale_a_gap_book_is_written_unroutable_and_never_restamped` | `kalshi_books_current.readable` |

### 2.2 Mis-mapped data — refused, never guessed

| Case | Test | Control / gate |
|---|---|---|
| MLB doubleheader disambiguated by start or refused | `test_red_team_chaos.py::test_c05_an_mlb_doubleheader_is_disambiguated_by_start_or_refused` | mapping (`kalshi_claims.map_pmus`) |
| NBA / WNBA city collision refused | `test_red_team_chaos.py::test_c06_a_wnba_team_never_maps_to_the_nba_team_of_the_same_city` | mapping |
| settlement rules change invalidates the certificate | `test_red_team_chaos.py::test_c07_a_rules_change_invalidates_the_certificate_and_freezes_pairs` | SETTLEMENT_CERTIFICATES |
| three-way opponent NO never aliased to team A YES | `test_red_team_chaos.py::test_c08_a_three_way_opponent_no_is_never_aliased_to_team_a_yes` | CANONICAL_EXPOSURE (payoff identity) |
| Yankees YES market != Rays NO market | `test_kalshi_ws_market_data.py::test_yankees_yes_market_is_not_rays_no_market_at_the_book_level` | canonical claims |
| fuzzy / AEC mappings quarantined | `test_mapping_quarantine.py::test_fuzzy_mapping_is_quarantined`, `::test_aec_slug_is_quarantined_even_when_exact` | mapping quarantine |
| a partial identity match never maps | `test_venue_native_identity_matches_the_measured_fixture.py::test_a_partial_match_never_maps` | venue identity |
| PMUS stated home / away reversed vs Kalshi → never mapped | `test_red_team_scenarios.py::test_mismapped_a_reversed_stated_home_away_is_never_mapped` | mapping |
| LONG subject = PMUS `team_a`, never the slug's position; unknown team → nothing | `test_red_team_scenarios.py::test_mismapped_the_long_subject_is_the_pmus_team_a_never_guessed` | canonical claims |

### 2.3 Credential isolation — one venue's key never reaches another venue's client

| Case | Test | Control / gate |
|---|---|---|
| the PMX RSA (M2M) key in the PMUS slot blocks that path | `test_red_team_chaos.py::test_c29_an_rsa_key_in_the_pmus_slot_blocks_that_path` | CREDENTIAL_CLASSES |
| the PMUS M2M key is never used as retail: every funded reader refuses before any request; nothing signed or sent | `test_pmus_funded_readers_refuse.py` (the whole file, e.g. `::test_the_shared_client_refuses_every_authenticated_request_by_name`, `::test_the_shadow_never_reads_the_institutional_account`, `::test_no_other_account_or_source_is_ever_venue_confirmation`) | `pmus._install_credential_gate`, `venue_key.signing_secret`, `mirror_shadow.pmus_secret_unusable_reason` |
| an Ed25519 PEM is never called RSA; class by parsed key | `test_pmus_credential_census.py::test_an_ed25519_pem_is_never_called_rsa`; `test_kalshi_key_classes.py` | CREDENTIAL_CLASSES |
| the market plane refuses an order-capable credential | `test_market_plane_orderless_guard.py::test_an_order_capable_credential_makes_the_plane_refuse` | market_plane_guard |
| each venue's signer reads only its own env slots (source scan) | `test_red_team_scenarios.py::test_credentials_each_venue_signer_reads_only_its_own_slots` | by name |
| ★ a Kalshi Ed25519 PEM key file in `PMUS_SECRET_KEY` never signs a PMUS request (shared, read and order client; mirror walk; API readers) | `test_red_team_scenarios.py::test_credentials_a_kalshi_pem_key_in_the_pmus_slot_never_signs` | `venue_key.pmus_slot_refusal` → `PMUS_SECRET_SLOT_HOLDS_A_PEM_KEY_FILE` |
| the production RSA slot keeps its refusal; a genuine retail key still signs | `test_red_team_scenarios.py::test_credentials_the_production_rsa_slot_and_a_real_key_are_unchanged` | same gates |
| ★ one key pair in two venues' slots is found by the key itself (PMX RSA in the Kalshi slot: same shape, different venue) | `test_red_team_scenarios.py::test_credentials_one_key_pair_in_two_venues_slots_is_found_by_key` | CREDENTIAL_CLASSES `CREDENTIAL_REUSED_ACROSS_VENUES` |
| ★ the Kalshi signers (REST client, gate, WebSocket runtime) refuse another venue's key before signing or connecting | `test_red_team_scenarios.py::test_credentials_the_kalshi_signers_refuse_another_venues_key` | `KALSHI_KEY_REUSED_ACROSS_VENUES`; KALSHI_HEALTH `OWNER_ACTION_REQUIRED` |

### 2.4 Replay — duplicated or replayed messages and requests are idempotent

| Case | Test | Control / gate |
|---|---|---|
| an ambiguous ack never triggers a blind retry; deterministic client id | `test_red_team_chaos.py::test_c10_an_ambiguous_ack_never_triggers_a_blind_retry`; `test_kalshi_venue.py::test_an_ambiguous_submit_is_adopted_by_client_order_id`, `::test_the_client_order_id_is_deterministic_per_mirror_id` | sentinel / Kalshi reconciler |
| a duplicate fill is deduplicated by venue identity | `test_red_team_chaos.py::test_c11_a_duplicate_fill_is_deduplicated_by_venue_identity`; `test_kalshi_venue.py::test_fills_are_parsed_in_both_dialects_and_deduplicated`; `test_d1_fills_dedup.py` | two-leg sentinel, fill readers |
| out-of-order fills converge to one position | `test_red_team_chaos.py::test_c12_out_of_order_fills_converge_to_one_position` | two-leg sentinel |
| duplicates, retries and restarts never debit or credit twice | `test_paper_account_and_ledger.py::test_duplicates_retries_and_restarts_never_debit_or_credit_twice` | PAPER ledger (idempotency key) |
| duplicate / stale order-record reads never duplicate a fill | `test_chaos_the_venue_state_machine_recovers.py::test_duplicate_and_stale_order_record_reads_never_duplicate_a_fill` | funded state machine |
| an order POST is sent once (SDK retries off) | `test_order_posts_are_sent_once_and_429s_are_named.py::test_the_pinned_sdk_never_retries_a_post_even_at_its_own_default` | order clients |
| a replayed older PMX update is a clock gap | `test_institutional_stream.py::test_a_backwards_venue_clock_is_a_gap_until_the_order_is_restored` | VENUE_HEALTH (PMX) |
| a duplicate WS delivery never doubles a group | `test_s1_emitter.py::test_duplicate_ws_delivery_never_doubles_the_group` | emitter |
| ★ a replayed Kalshi snapshot never rolls a CURRENT book back | `test_red_team_scenarios.py::test_replay_a_replayed_snapshot_never_rolls_a_current_book_back` | Kalshi WS sequence (`KALSHI_WS_SNAPSHOT_OUT_OF_SEQUENCE`) |
| ★ a snapshot past a lost message gaps every market of the sid | `test_red_team_scenarios.py::test_replay_a_snapshot_past_a_lost_message_gaps_every_market_of_the_sid` | Kalshi WS sequence |
| a duplicated delta is never applied twice | `test_red_team_scenarios.py::test_replay_a_duplicated_delta_is_never_applied_twice` | Kalshi WS sequence |
| ★ a gapped sid is untrusted until re-announced; a re-announced sid is a new subscription | `test_red_team_scenarios.py::test_replay_a_reannounced_sid_is_a_new_subscription` | Kalshi WS sequence |
| ★ end to end: the subscriber resubscribes and serves only the fresh book | `test_red_team_scenarios.py::test_replay_end_to_end_the_subscriber_resubscribes_and_serves_only_fresh` | KALSHI_HEALTH / `kalshi_books_current` |

### 2.5 Reconnect — never serve pre-resync state

| Case | Test | Control / gate |
|---|---|---|
| PMX stream killed: GAP; reconnect needs a full update | `test_red_team_chaos.py::test_c01_kill_pmx_books_gap_reconnect_needs_a_full_update`; `test_institutional_stream.py::test_a_dropped_connection_is_a_gap_until_a_fresh_snapshot` | VENUE_HEALTH |
| connected without a snapshot is never current | `test_red_team_chaos.py::test_c13_connected_without_a_snapshot_is_never_current` | VENUE_HEALTH |
| Kalshi disconnect: every book GAP, never reused | `test_kalshi_ws_market_data.py::test_a_disconnect_marks_every_book_gap_and_never_reuses_it`, `::test_the_subscriber_resubscribes_after_a_gap_and_waits_for_the_snapshot` | KALSHI_HEALTH |
| PMUS stream: a book from a previous connection epoch is ineligible at any age | `test_bettor_market_stream.py::TestADisconnectInvalidatesEveryBook::test_a_book_from_a_previous_epoch_is_ineligible_at_any_age` | PMUS eligibility |
| Polymarket reconnect changes only Polymarket's health | `test_red_team_chaos.py::test_c03_a_polymarket_reconnect_changes_only_polymarket` | VENUE_HEALTH (per venue) |
| a dropped provider revokes the feed; its last price never enters | `test_chaos_provider_disconnect_and_delayed_settlement.py::test_a_silent_providers_last_price_never_enters_and_a_fresh_one_does`, `::test_a_dropped_provider_revokes_the_feed_and_blocks_xavier_on_market_data` | entry / Xavier freshness |
| the PAPER stream replays committed entries in sequence on reconnect | `test_paper_stream_and_account_routes.py::test_the_stream_publishes_committed_entries_in_sequence_and_replays_on_reconnect` | UI stream |
| no pre-resync Kalshi book is served or persisted; a delta before the new snapshot is ignored | `test_red_team_scenarios.py::test_reconnect_no_pre_resync_book_is_served_or_persisted` | `kalshi_books_current.readable` |
| end to end across two sessions | `test_red_team_scenarios.py::test_reconnect_end_to_end_two_sessions` | KALSHI_HEALTH |

### 2.6 Partial fills — accounting and protection on the filled quantity

| Case | Test | Control / gate |
|---|---|---|
| one leg 80 %, the other 0 %: repair, never "locked" | `test_red_team_chaos.py::test_c09_one_leg_80_percent_other_zero_is_repair_never_locked` | two-leg sentinel |
| a reservation is not a purchase; a partial fill debits only the filled share | `test_paper_account_and_ledger.py::test_a_reservation_is_not_a_purchase_and_a_partial_fill_debits_only_the_filled_share` | PAPER ledger |
| protection counts only the filled part; unprotected = position − filled protection | `test_resting_is_not_protection.py::test_partial_fill_counts_only_the_filled_part`, `::test_unprotected_is_position_minus_filled_protection` | Xavier protection |
| a reduce protects the half it does not sell | `test_xavier_sale_transition_keeps_the_remainder_protected.py::test_a_reduce_protects_the_half_it_does_not_sell_in_the_same_review` | Xavier management |
| partial exit P&L on the sold quantity; fees to the sold fraction | `test_partial_exit_pnl_is_separable.py::test_a_partial_loss_is_reported_on_the_sold_quantity`, `::test_fees_are_allocated_to_the_sold_fraction` | ATTRIBUTION |
| a partial fill leaves residual exposure under management | `test_bettor_mgmt_lifecycle.py::test_4_a_partial_fill_leaves_residual_exposure_under_management`, `::test_the_ledger_identity_holds_through_a_partial_completion` | management lifecycle |
| a partial fill during a working cancel is counted | `test_chaos_the_venue_state_machine_recovers.py::test_a_partial_fill_during_a_working_cancel_is_counted_and_the_cancel_is_not_forgotten` | funded state machine |
| a void after a partial exit refunds only what is held | `test_the_funded_lifecycle_is_complete.py::test_a_void_after_a_partial_exit_refunds_only_what_is_still_held` | settlement |
| IOC partial on the PMUS client | `test_pmus.py::test_submit_ioc_partial_fill` | order client |
| the twin grades an IOC partial on its quantity; FOK is all-or-none | `test_red_team_scenarios.py::test_partial_ioc_is_graded_on_the_filled_quantity_and_fok_is_all_or_none` | DIGITAL_TWIN |

### 2.7 Authority — no path beyond SHADOW

| Case | Test | Control / gate |
|---|---|---|
| a hostile environment grants nothing; small-live mode never read from the environment; the interlock never auto-activates | `test_red_team_env_cannot_grant_authority.py` (3 tests) | readiness `auto_activation` |
| only the Kalshi modules import the Kalshi client; the submit path is behind the gate | `test_kalshi_isolation.py` (5 tests) | Kalshi live money |
| the Kalshi gate refuses in order; the env switch defaults OFF | `test_kalshi_venue.py::test_the_gate_refuses_in_order_until_every_condition_holds`, `::test_the_env_switch_defaults_off_and_blocks_submission` | Kalshi live money |
| no runner constructs the Kalshi client | `test_kalshi_key_classes.py::test_no_runner_constructs_the_kalshi_client` | Kalshi live money |
| Adriana's tools hold no order credential, capital or approval | `test_adriana_agent.py::test_her_tools_hold_no_order_credential_capital_or_approval`, `::test_her_modules_import_no_order_path_and_write_only_her_tables` | Adriana SHADOW ONLY |
| the workers hold no venue write; the plane is process-locked | `test_workers_hold_no_venue_write.py`; `test_market_plane_orderless_guard.py::test_the_process_lock_refuses_every_venue_write_at_the_transport` | Risk card |
| small live: this build has no BETTOR-originated path | `test_small_live_truth.py::test_this_build_has_no_bettor_originated_path` | SMALL LIVE |
| every capital gate only refuses | `test_capital_authority.py::test_every_new_gate_only_refuses` | PAPER capital authority |
| ★ small live is READ from its gate (`execmirror_control`), never a literal | `test_red_team_scenarios.py::test_authority_small_live_is_read_from_its_gate` | `readiness.authority.small_live` |
| ★ Kalshi live money is READ from `kalshi_smalllive_control`; an env switch alone changes nothing | `test_red_team_scenarios.py::test_authority_kalshi_live_money_is_read_from_its_control_row` | `readiness.authority.kalshi_live_money` |
| ★ Adriana's authority is READ from her modules' own assertions | `test_red_team_scenarios.py::test_authority_adriana_is_read_from_her_own_assertions` | `readiness.authority.adriana` |
| ★ the pm-acceptance envelope carries what was read | `test_red_team_scenarios.py::test_authority_the_pm_acceptance_envelope_carries_what_was_read` | `/api/command/pm-acceptance` |
| ★ pm acceptance's `live_authority_shadow` reads the lane, not completion's literal | `test_red_team_scenarios.py::test_authority_pm_acceptance_reads_the_lane_not_the_literal` | pm-acceptance harness |
| the readiness on a migrated database with both control rows off is PAPER_SHADOW_ONLY with authority SHADOW / NOT_ACTIVATED / SHADOW_ONLY and its basis | `test_red_team_readiness_db.py::test_evaluate_with_nothing_proven_is_paper_shadow_only` | readiness |
| ★ an enabled, unstopped actual lane on a migrated database reads ACTUAL_LANE_ACTIVE, fails the interlock's live-authority check, and grants nothing | `test_red_team_readiness_db.py::test_a_running_actual_lane_is_read_as_such_and_grants_nothing` | readiness `checks.live_authority_shadow` |

## 3. Defects found by the scenarios and fixed in this lane

| # | Defect (base `412c4962`) | Fix | Regression |
|---|---|---|---|
| D1 | KAREN_VALUE read `book = 'PAPER'`; the twin writes COUNTERFACTUAL → the control could never pass; metrics mixed across runs; INSUFFICIENT_SAMPLE ignored; unmeasured shown as "0" | `redteam/readiness.karen_counterfactuals`, `redteam/controls.karen` | `test_red_team_karen_value_db.py` (5) |
| D2 | Kalshi WS applied a snapshot whatever its seq: a replay rolled a CURRENT book back; a snapshot past a lost message hid the gap for the sid's other markets | `kalshi_ws.WsBooks` (snapshots in sequence; gapped sids untrusted until re-announced) | `test_red_team_scenarios.py` replay (4 ★) |
| D3 | every PMUS signer gate accepted "an Ed25519 key in any encoding", so Kalshi's PEM key file in `PMUS_SECRET_KEY` signed Polymarket US requests (the class control already said MISMATCH_PATH_BLOCKED) | `venue_key.pmus_slot_refusal` used by `signing_secret`, `pmus._install_credential_gate`, `pmus._get_read_client`, `mirror_shadow.pmus_secret_unusable_reason` | credentials (★) |
| D4 | the PMX RSA key in `KALSHI_PRIVATE_KEY_PEM` loaded and signed Kalshi requests (no shape tells two RSA PEMs apart) | `credential_isolation` (public-key fingerprints); CREDENTIAL_CLASSES `CREDENTIAL_REUSED_ACROSS_VENUES`; `kalshi_venue` and the WS runtime refuse | credentials (★) |
| D5 | the readback's authority block was four literals and the interlock compared completion's literal `small_live = "SHADOW"`: it could not fail | `redteam/readiness` reads `execmirror_control` (via the completion gate), `kalshi_smalllive_control`, the Adriana assertions; UNREAD on failure; `authority_basis` evidence; pm-acceptance envelope and `pm_bind.acceptance` | authority (★); `test_red_team_readiness_db.py` (the shadow case now pins both control rows off for its read, restoring them, because other suites sharing the database enable them; the running-lane case is a new test) |

## 4. Production readbacks that prove the lane after deploy

- `red_team.json` → `readiness.controls.KAREN_VALUE`: `evidence.twin.book =
  COUNTERFACTUAL`, `evidence.twin.rows.*.run_id` = the newest twin run, blockers
  name `KAREN_TWIN_UNAVAILABLE:...:NO_BLOCKED_POSITION_SCORED_IN_BOTH_WORLDS`
  (expected while Karen blocks nothing), `saved_loss_usd: null`.
- `red_team.json` → `readiness.authority` = {SHADOW, NOT_ACTIVATED,
  SHADOW_ONLY, false} **and** `readiness.authority_basis` present, with
  `small_live.gate.value = true` and `kalshi_live_money.control.enabled /
  stopped` as read.
- `red_team.json` → `readiness.controls.CREDENTIAL_CLASSES.evidence.
  cross_venue_key_reuse` present; production expected to name
  `api:PMUS_SECRET_KEY=PMX_PRIVATE_KEY_B64` (and the workers' entry once the
  workers boot on this build) until the owner action in 1.6.
- `venues.json` → `health.KALSHI_HEALTH`: still `KALSHI_WS`, 145 / 145 current
  (no Kalshi key reuse on the plane; snapshots in sequence); the WS heartbeat's
  `ws.snapshots_out_of_sequence` / `ignored_gapped_sid` counters visible.
