# THE TO-A-TEE PROGRAM — revision 2 (mirroring RN1 on Polymarket US, built shadow-first)

Revision 2 folds in the critic's round-1 gaps (18 items) and the six new lenses gap_r1_1..6 with their
engineering refutations, on top of the six original lenses and their two refuters each. Revision 1 is kept
verbatim at tee/program.rev1.md. Repo HEAD 76b68b4 (read-only). The WORKING TREE is step 9's and is NOT
HEAD: `git diff --stat` = engine-diagnostic.yml +481, analytics/mirror_report.py +580, api/app.py +15,
workers/all.py 14 lines, workers/mirror_shadow.py +400, tests/test_mirror_live_handoff.py 48 lines; untracked
workers/mirror_live.py and tests/test_mirror_live_worker.py (neither read). Migrations top at 047.

Abbreviations as in revision 1: `le` = live_executor.py, `rules` = analytics/mirror_live_rules.py, `mi` =
analytics/mirror.py, `ms` = workers/mirror_shadow.py, `mr` = analytics/mirror_report.py, `wx` =
workers/whale_exits.py, `cs` = copy_sports.py, `probe` = probe_33686724064.txt (21:52Z), `probe9` =
probe_33690960366.txt (22:44Z), `terms1/terms2` = tee/mirrorterms_run_33702067773 / _33702442462 (the runner
job gap_r1_2 ran at 01:03Z/01:08Z on 2026-09-03), `spec` = p1_panel_synthesis.md, `add` = p1_addendum_p0_findings.md.
Every number is a probe line, a file:line, or a probe run in this session (§A) or by a lens (tee/*.out).

## 0. Where P1 stands against "to a tee" (revision 2 readings)

| fact | number | source |
|---|---|---|
| Shadow window, 24 h | 443 markets / 99 mapped (22.3%); would-fill 2586/5275 = 0.4902; drift p90 0.458; frozen 343 (01:03Z); 447/99, 2651/5403 = 0.4907 at 01:08Z; 334/63 at 22:44Z | terms1 (gap_r1_2.md §1); terms2 (§1b); probe9:1658 |
| Spec P0→P1 gate | would_fill ≥ 0.50: straddled (0.4902-0.5296 across reads; binomial [0.4714, 0.5100] on 1265/2578); drift p90 ≤ 0.05: FAIL; frozen == 0: FAIL; tennis-ML unmapped ≤ 20%: now printable (MIRRORCOVER job in the tree) | probe9:1658; §A |
| RN1 snapshot | partial on every probe (every probe carries a mapped row with one token `n/a`; zero rows carry `0.0`); truncated_books=2 of 7 | tee/lifecycle.refute.market.md:11-27; probe:1542; probe9:1478 |
| Consequence | admission `snapshot_stale` on every RN1 candidate (spec:177; rules:465-466) → P1 as specified opens no RN1 book | tee/lifecycle.md:11 |
| Mapping source under the quarantine | ledger-sourced by construction on 43/62 mapped readings (69.4%), $141,849 of his net-at-mark dollars (97.9%); QUARANTINE_RESUME_SRC={premap,exact}; not incremental to `legacy_row` (same rows) | tee/gap_r1_5.md G8; tee/gap_r1_5.refute.engineering.md F2; le:2087 |
| His side vs our long token | 18 of 33 mapped markets (55%) his_net < 0; 41.9% of Σ\|his_net\| shares over 38 mapped markets | tee/shorts.md:9; tee/verification_refute_market.out:14 |
| Tick grid | 42/98 mapped markets (42.9%) on orderPriceMinTickSize 0.005 = 30.0% of his mapped 24 h dollars, 49.3% of his OPEN dollars (50.4% of open moneyline $); every wire function hardcodes 0.01 and `_amount` formats `%.2f` (ROUNDS: 0.525 → "0.53") | terms1/2; tee/gap_r1_2.md §1-1b; tee/gap_r1_2.refute.engineering.md probe |
| Min qty / fee / status | minimumTradeQty 0.01 on 98/98; feeCoefficient 0.06 on 98/98 and 4,055/4,055 venue-wide; `active` True on all 62 RESOLVED markets (not a liveness flag) | terms1 |
| In-play | 98/98 mapped markets had started at 01:03Z; 82/82 joinable fills after gameStartTime (one-match-dominated 3.4 h sample); pre-game ≤ 3.0% of his marked buys (423/14,090) | tee/gap_r1_4.md §2a; probe:1679 DECOMPREAD |
| 'no mark: book unreadable' | 9 post-final empty + 6 decided one-sided (0.96-0.995 bid, no ask); 0 pre-open, 0 halted, 0 404 — LIKELY, status read 3-5 h later; `_bbo_quotes` swallows HTTP errors so a 429 pair reads the same | tee/gap_r1_4.md §2c; tee/gap_r1_4.refute.engineering.md |
| Slug date vs play date | 29 of 98 mapped slugs carry a venue gameStartTime after the slug date (10+1+8+1 listing-date, 9 a UTC/EDT artifact) | tee/gap_r1_4.refute.engineering.md |
| First sight | ≥ 52.9% of his long dollars on the 17 marked long-net slugs existed at the first PROBE print (a BOUND: probes 40-72 min apart, shadow ticks 30 s); the number is one SQL over 046 rows away | tee/gap_r1_6_probe.out; tee/gap_r1_6.refute.engineering.md F2 |
| Inherited gates that cost RN1 today | soccer floor: 1/13 soccer readings ($389, 0.27% of mapped $) at his level, 47-65% of his non-exit soccer FILLS by count, and every YES-on-draw/dog book by construction; side_band 2/33 BUY plans (both stale-level); everything else 0 | tee/gap_r1_5.md §1-2; tee/gap_r1_5.refute.engineering.md |
| The account | buying_power $31,502.13 at 21:45Z; no money path reads it | probe:77; api/pmus_account.py:321 |
| Ratio and cap | ratio 50/25.55 → clamped 1.0; weighted 0.018974; $250 cap binds on 68.7-85.1% of his long dollars at r=1.9% | probe:1714; tee/shape_probe.out2; tee/sizing.refute.engineering.md:14-22 |
| His money / pace | ≥ $250 lots = 86.8% of stake (only bucket positive at 95%); entries $1,363,663/day; ~660 markets/day; 95.5% dated yesterday..tomorrow | probe:1686; probe:1843; probe:1714; probe:1544 |
| Short wire | 373 sign-verified BUY_SHORT fills, 0 mismatch; 0 SELL_SHORT ever; post-only never sent | probe:1320, :1950-1951; tee/shorts.refute.market.md:31-35 |
| P&L proof horizon | sigma_per_dollar 1.302 → 31,052 games for +2.07% at 95%/80%; ±46.6 pts at 30 games | probe:1703; §A |
| Our RN1 per-fill copies | 2026-09-02 51W-58L −$23.28 on $4,737.60; 2026-09-01 62W-83L −$972.86 on $14,865.12; 15 days with stake ≥ $1k: daily ROI sd 0.282 of stake | probe9 COPYD; §A |
| Venue transport | WebSocket depth/tape/private-order streams exist in the SDK and are unprobed (0 uses in backend); the "~3 req/s 429" figure is unsourced, the two 429 incidents are real (2026-08-15, 2026-08-23) | tee/gap_r1_1.md §1; tee/gap_r1_1.refute.engineering.md F2 |
| Modify endpoint | `orders.modify` (`/v1/order/{id}/modify`, price/quantity/tif/participateDontInitiate/goodTillTime) exists, 0 uses; `keep_or_replace` replaces at age ≥ TTL on an unchanged cent | SDK resources/orders.py:52-58, types/orders.py:135-144; rules:731-734 |
| Step 9's tree | already carries revision 1's Phase 0: unmapped detail, family/per_side/snapshot/ledger class, parallel short reading, touched_s + depth (`_paced_depth`), non-legacy split with market-clustered intervals, would-P&L, snapshot census, dead-band dollars, `/api/admin/mirror-cover`, the `mirror-cover` job and MIRRORCOVER*/SRC/SNAP/SIGN/SHORT/FILL/FAM/DEAD/WOULD/UNMAP lines | `git diff` (this session) |

Reading: the three blockers of revision 1 stand (snapshot → Phase 1; admissible mapping source → Phase 2; the
ratio that makes the $250 cap the only sizer → Phase 3). Round 1 adds four facts that change the build:
his flow is in-play and the mirror reads no venue lifecycle field (Phase 0b/4); 43% of his mapped markets sit
on a half-cent grid the wire cannot express (Phase 4); the account funds r ≈ 0.126% on the only readable
denominator, at which the existing $1,250 day cap is almost proportional (decision 1); and the shadow can
compute first sight, in-play share, cross-venue basis and the exit leg from rows it already has (Phase 0c).

## 1. Dropped and amended (both refuters killed it, or one killed it on evidence the other did not beat)

D1-D12 carry from revision 1 unchanged (per-side-slug shorts; the clip scale as a gate; IOC at p+1c; the
12/h replace collision; peak-fraction mirroring; the vanish path on fills-say-held; the dispatch below the
mapping block; "tennis not listed"; round()+r-scaled dead band; promotion on fidelity clauses; the pro-rata
settlement split; sign hysteresis). Round 1 adds:

D13. gap_r1_2 C1 `game_started` refusal ("gameStartTime in the past on a moneyline") — killed: it refuses
98/98 mapped markets and 100% of the joinable fills (terms2 `started in_play_or_past n=98 usd=819395.19`);
no in-play refusal exists in spec/addendum. In-play is admitted by name (decision 16).

D14. gap_r1_2 C4 fee-aware `at_or_better_net` as the P2 clause-(3) reading — killed: clause (3) is a
wire-bound invariant (spec:438 "must be 1.0 by construction"); a net figure is < 1.0 on every fill; the fee
formula behind 0.06 is unread and commission VALUES have never been observed (keys only, probe:2043-2044).
Kept as logging (Phase 7 rung 10, M17).

D15. gap_r1_2 C3 body-text classifier of the post-only 4xx — killed: no venue refusal text exists in any
log; tests/test_pmus_post_only.py:320-333 pin every 4xx → `post_only_rejected`; `take_arms` arms on int 400
only (rules:818). Amended: carry status+body into a `refusal_text` census bucket; `take_arms` also reads the
market `state` (Phase 4, rung 16) so a PREOPEN/SUSPENDED 400 never arms a take.

D16. gap_r1_2 C1's `minimumTradeQty` reader refusing "not a whole number ≥ 1" — killed: the venue's value is
0.01 on 98/98, the reader would refuse the whole mapped set. Amended: `min_qty = max(1, ceil(value))` (the
SDK sends `int(quantity)`, types/orders.py:118); a refusal name `min_qty` only for markets whose value exceeds 1.

D17. gap_r1_3 C4 "closed AND prices sum to 1 → resolved as a split" — killed: a closed-but-pending market
carries its last mids (`["0.62","0.38"]` closed → the lens's own test would book 0.62 as a payout); the
upsert is sticky (gamma.py:241-243); three-way markets split at 1/3. Amended (Phase 9): a `split_candidate`
census (closed, not resolved, prices exactly [0.5,0.5] for ≥ 48 h) that never sets `resolved`; settlement
price derived from the raw POSITION_RESOLUTION archive `(after.realized − before.realized + before.cost) /
before.netPosition` (tee/gap_r1_3.refute.engineering.md F2), `/settlement` read as the cross-check.

D18. gap_r1_3 §1 parity table as "proven" — downgraded to LIKELY: every rule row is a search-engine snippet
(the lens says so, gap_r1_3.md header); ITF's "last fair price" is an inference from an exception clause;
ITF is $66 of $101.5k shadow dollars (0.1%) and 176/8,389 listed rows (probe:2080).

D19. gap_r1_4 G2 "venue_state == OPEN required on every INCREASE" — killed: addendum §10 makes the increase
re-check the starred clauses only and `test_increase_recheck_is_the_starred_clauses_only` (:1561-1565) pins
`market_closed=None` admitting on increase. Amended: `venue_state`/`phase` are NEW-book admission facts and
step-M (`market_closed_or_resolved`) facts; the worker's own "no increase unless closed/resolved read is
False" guard is the increase-side check.

D20. gap_r1_4 §3 "wake-driven requotes exempt from the hourly cap; re-quote any rest older than 5 s" —
killed: ~43 cancel+create writes/h/book at 21.6 clusters/h against MIRROR_MAX_ORDER_OPS_PER_TICK=6 and the
shared 429 budget; capped_env is downward-only by design. Amended: measure the DIRECTIONAL staleness first
(his next different cent inside 600 s is UP 18 / DOWN 28 of 46; only a rest ABOVE his new level pays more
than him), then raise MIRROR_MAX_REPLACES_PER_HOUR by CODE to the measured down-move rate.

D21. gap_r1_4 G1 changing `_paced_bbo` to a 3-tuple — killed as not parallel-safe: spec routes every worker
venue read through `ms._paced_bbo` (spec ~:110) and the in-flight worker unpacks two values;
test_mirror_shadow:120-124 and :640 pin the shape and the pacing source text. Amended: sibling
`_paced_bbo_state` returning (bid, ask, state, err); `_paced_bbo` untouched.

D22. gap_r1_5's per-side table (13/62 readings, $45,033 refused `per_side_unsupported`) — killed: an atc-
contract's two sides share ONE identifier with a `long` bool (probe:1866); per_side needs two BUY_LONG rows
on two slugs (tests/test_mirror_shadow.py:668-705). Consequence kept and STRONGER: the soccer floor is a
live P1 clause on RN1's soccer, not a dominated one.

D23. gap_r1_5 decision 11 (the $2 underdog sleeve vs the mirror) as an owner decision — killed: the sleeve is
OFF by owner order 2026-08-24 ("only copies flow", underdog.py:49-55) and its restart is blocked by a
side-selection defect. `underdog_coholds` stays as written; revisit only if the sleeve is re-armed.

D24. gap_r1_5 decision 12(b) "admit a 'ledger' map when the ledger row was side-echo-verified" — killed:
no per-row mapping source and no per-row echo verdict is persisted on live_orders (echo verdicts live only in
ingestion_state keys, le:5612-5613). Only Phase 2's mirror-owned exact lane admits.

D25. gap_r1_6 Rule LE's flow-first ratchet (`block_t = max(0, min(block_{t-1}, net_t))`) — killed: he trims
8.3% (12,000 → 11,000) and we sell 50% of our book; at 9,000 we hold 0 (tee/gap_r1_6_refute_ratchet.out).
Amended to PRO-RATA on reductions: `block_t = block_{t-1} × net_t/net_{t-1}` when net falls, unchanged on
increases, 0 on a crossing to ≤ 0. And both targets (shadow and live) switch to flow in ONE change, else
`shadow_live_disagree` trips on every pre-built book.

D26. gap_r1_6's "+15.9%/+16.4% (Nakashima), −17.5% (tometc)" as measured costs of today's rule — killed:
the Nakashima first sight "after fill #14" is a constructed scenario (the shadow's first tick sees fill #1 at
+0.0%); tometc's −17.5% is the PROBE's 20-min lag, and the 518@0.71 plan is refused `venue_already_holds`
(ledger −169). Kept: the structural fact (no first-sight state anywhere) and M21 as a metric, amended so
`his_vwap_since` runs from max(first_seen_at, this episode's opened_at) to match the ledger's avg_cost reset.

D27. gap_r1_6 option-B dollars ("$2,543 over 13 slugs, $1,250 bought into finished positions per turn") —
killed: on the window's rows P1 admits 0 of the 16 pre-built slugs (venue_already_holds / drift ≥ 0.155 /
per_side / snapshot_stale; tee/gap_r1_6_refute_overlay.out). Decision 13 stands with the bound only.

D28. gap_r1_1 "run ws_probe.py on the engine host" — killed as a mechanism: the key lives on the Render
services (render.yaml:40-42, shared by web and worker); every probe runs workflow → admin endpoint; no host
shell exists. Amended: parts 0/1/3 behind a `require_admin` endpoint printed by the workflow (Phase 7 rung 13).
Also killed: `md_slug_coverage=60/60` as an ADOPT criterion (a quiet market on a delta feed reads as a cap
failure) → coverage over markets whose REST bbo moved inside the window.

D29. gap_r1_1 "REST ceiling = 60 books at 1 read/book" as a ceiling — killed: the 0.35-s pacer is
per-process and only three modules use it (mirror_shadow, price_path, mirror_live); premap, the positions
walk and the web service read the same key outside it — two overlapping 0.35-s loops = 5.7 req/s, the exact
incident venue_pace.py:8 records. And the binding constraint today is a SLOT cap: unmapped markets are charged
a tick slot at ms:650 before any read (ms:430-437) — ~11 of 20 slots per tick go to markets that cost 0
reads. Amended: count VENUE reads, not markets; a key-wide bucket before any raise (Phase 0c, decision 14).

D30. gap_r1_1 WS fill consumer keyed on `(order_id, seq)` — killed: `seq` is not a venue field (addendum
§9 derives it from raw.adds under the book lock); a replayed execution books twice. Amended: execution-id
dedupe stored in raw.adds, reconciled against `cumQuantity`, poll stays authoritative; probe part 2 only.

D31. gap_r1_1 "M10's haircut has no instrument until WS" — killed: depth at our cent is REST-readable now
(`markets.book` levels {px, qty}; `_bbo_quotes` already falls through to `book`, pmus.py:598-600) and step
9's tree carries `_paced_depth`; M10 itself is `filled_rest/placed_rest` on live rests. Only the public TAPE
is WS-only. Kept: a `queue_consumed` judge beside touch (Phase 0c) as a pre-live estimate.

D32. gap_r1_5 G6 first-fill re-key inside admission — amended: re-keying `first_fill_ok` to a mirror echo
that only a mirror fill can write deadlocks the first book; the rung that writes `side_echo_mirror` runs
through the probe endpoint outside admission (Phase 7 rung 17).

## 2. COLLISION REGISTER with step 9 (critic gap 15)

Facts: step 9's uncommitted tree touches six files (header). The three files it OWNS by the brief are
workers/mirror_live.py, workers/all.py, tests/test_mirror_live_worker.py. Three more it has MODIFIED but does
not own — workers/mirror_shadow.py (+400: census helpers, `short_reading`, `_paced_depth`, `_write(...,
pmus=)`, `_resolve_previous(..., census=)`), analytics/mirror_report.py (+580: `phase0_census`,
`rate_with_ci`, `settle_would_pnl`, `candidate_slugs`, `ADMISSIBLE_SRC`, `mirror_cover_report`),
api/app.py (+15: `/api/admin/mirror-cover`), engine-diagnostic.yml (+481: `mirror-cover` job) and
tests/test_mirror_live_handoff.py (`test_the_real_worker_is_woken_quietly`).

Rules this revision applies:
1. `parallel_safe_with_step9=TRUE` means: no file in the phase is one of the three owned files AND no function
   the worker calls (spec §2: `ms.his_fills`, `ms.map_market(pool, fills)`, `ms.snapshot_sizes`,
   `ms._paced_bbo`, `ms.account_positions`, `ms.his_level`, `rules.mirror_target`, `rules.admission`,
   `rules.keep_or_replace`, `rules.take_arms`, `rules.select_flatten`, `rules.drift_rule`,
   `rules.episode_close_reason`, `le._open_mirror_book`, `le._book_mirror_buy/_sell`, `wx._confirm_gone`,
   `pmus.submit_fok`) changes its accepted inputs or return shape. Additive kwargs with defaults are safe;
   arity/return-shape changes are FALSE.
2. Phases that edit mirror_shadow.py / mirror_report.py / app.py / engine-diagnostic.yml are built ON the
   working tree (rebase after step 9 commits), never on HEAD; they are flagged `rebase_on_step9_tree` in the
   phase header. They stay TRUE by file ownership.
3. Migration numbers reserved here so step 9 cannot collide: 048 = Phase 0b (`markets.rules_text,
   resolution_kind`), 049 = Phase 4 (`mirror_books` first-sight/basis/terms columns, `mirror_orders`
   trigger/commission columns, `trades.taker`), 050 = Phase 5 (shorts), 051 = Phase 6 (game claims). Step 9
   claims none (its tree has no migration). 047 stays CREATE-only (test_mirror_live_migration.py:128-132).
4. The `_Pool` fake in tests/test_mirror_shadow.py is the fake the worker tests extend (add §7). Phases that
   add a query branch to it (3a) do so additively (new SQL prefix → new branch; existing branches untouched).
5. CONTRACT TEST (the one item only step 9's owner can finish): `tests/test_mirror_live_contract.py` —
   `inspect.signature` pins for every symbol in rule 1 as the worker calls them, `ast` walk of
   workers/mirror_live.py collecting every `ms.`/`rules.`/`le.`/`wx.`/`pmus.` attribute and asserting each is
   in the pinned set, the migration number step 9 claims (expected: none), and the MIRRORHB/heartbeat keys
   the worker emits. Until it exists, every FALSE flag below is a claim, not a proof. Prompt for step 9's
   owner: list every imported symbol with the signature used; every fixture imported from test_mirror_shadow;
   the heartbeat keys; write the contract test; mark each phase's flag from that list.

## 3. THE PHASES (ordered; files, the rule in prose, tests, the numeric gate; shadow before live)

### Phase 0a — INSTRUMENTS ALREADY IN THE TREE (land them with step 9; zero money)
Files: workers/mirror_shadow.py; analytics/mirror_report.py; api/app.py (`/api/admin/mirror-cover`);
.github/workflows/engine-diagnostic.yml (`mirror-cover` job); backend/tests/test_mirror_shadow.py;
backend/tests/test_mirror_report.py; backend/tests/test_mirror_cover.py.
Depends on: nothing. parallel_safe_with_step9: TRUE (already in the tree; land as one commit with step 9).

Rule. Revision 1's Phase 0 as written, verified against the diff: on an UNMAPPED row `detail` carries slug,
title, sport, family, `explain`, `his_gross_usd`, `outcome_null`; on a MAPPED row family, per_side, snapshot
state, `ledger_legacy`, `map_class` (the ledger row's own mapping class read by `ledger_facts`); a PARALLEL
short reading (`short_reading`, judged on the SELL side over the same TTL) beside a byte-identical `target`;
`touched_s` and the depth at the touch (`_paced_depth`) into `detail`; `summarize` gains `latest` with
detail, unmapped by family and by usd, mapped by SOURCE with `admissible` / `admissible_usd` /
`admissible_share`, the would-fill split into NON-LEGACY and legacy plans (`is_legacy_plan`) each with a
market-clustered `rate_with_ci` (`proof.roi_with_ci` applied to a proportion — one market is one cluster,
which answers the timing refuters' "one touch resolves every open row"), `settle_would_pnl` with a
game-clustered interval, the snapshot census, dead-band dollars, `neg_share_*`. The `mirror-cover` job
classifies every market as mapped_premap | mapped_exact | mapped_ledger:<class> | listed_on_us_but_unmapped:<step>
| listed_closed | not_listed_on_us (exact-404 AND search-0 AND no board-index hit) | undiagnosed | undated |
null_condition, dollars from usdcSize and GROSS at marks with the paired share, all candidates tried.
Tests: the diff's own tests plus: `test_the_shadow_never_touches_an_order` green; 046 column test unchanged
(JSONB only); `ADMISSIBLE_SRC == le.QUARANTINE_RESUME_SRC` pinned; every jq line parses on an empty endpoint.
Gate (24 h, ≥ 30 mapped markets): MIRRORCOVER-TOTAL `undiagnosed` ≤ 10% of usd; MIRRORSRC prints
ledger/premap/exact and `admissible_share`; MIRRORSNAP prints RN1 fresh-complete share; MIRRORFILL prints
the non-legacy rate with its clustered interval; MIRRORSHORT over ≥ 30 markets; MIRRORWOULD prints a
game-clustered would-P&L interval.

### Phase 0b — VENUE-FACT INSTRUMENTS: phase, terms, rules text, market state (shadow only, zero money)
Files: workers/mirror_shadow.py (`_market_record(pmus, slug)` one paced `retrieve_by_slug` per (whale,
condition) under a TTL of the `_unmapped_until` shape; `detail.phase`, `detail.terms`, `detail.rules`;
`_paced_bbo_state` sibling; `_resolved_until` TTL; `no_mark:*` reason classes; miss = state unread OR
bbo_error); pmus.py (`_bbo_state(client, slug) -> (bid, ask, state, err)` sibling of `_bbo_quotes`, 2-tuple
contract untouched; `_market_terms(rec) -> dict|None` pure); analytics/mirror_live_rules.py (pure
`market_terms(rec) -> Terms|None`: tick ∈ [0.001, 0.1] finite else None; `min_qty = max(1, ceil(v))`;
fee_coef, game_start_ts, status, ep3, closed carried as read); gamma.py (`parse_market` keeps
`raw.get("description")` as `rules_text`; `upsert_market` writes it); backend/migrations/048_markets_rules.sql
(`ALTER TABLE markets ADD rules_text TEXT, resolution_kind TEXT`); analytics/mirror_report.py (`by_phase`,
terms census, `rules_unreadable`); api/app.py; .github/workflows/engine-diagnostic.yml (MIRRORPHASE,
MIRRORTERMS folded into the `mirror-cover` job from tee/mirror-terms-probe.yml, MIRRORRULES); tests.
Depends on: 0a. parallel_safe_with_step9: TRUE (`rebase_on_step9_tree`; no worker-called signature changes;
`_paced_bbo` keeps its 2-tuple).

Rule. Every mapped shadow row carries the venue's own lifecycle and contract facts, read once per market and
cached: `phase ∈ {pre_open, in_play, decided, expired, resolved, unknown}` from gameStartTime (pre_open = now
< start), status/ep3Status/closed (RESOLVED/EXPIRED/closed → resolved; RESOLVING → expired), and the BBO
call's `state` (SUSPENDED/HALTED/PREOPEN/EXPIRED/TERMINATED named as read; `decided` = OPEN with one side ≥
0.99 and the other absent); `terms = {tick, min_qty, fee_coef, game_start, status, ep3}`; `rules = {global:
markets.rules_text, venue: rec.description}` stored and PRINTED, no classifier yet (the classifier and any
admission clause wait for the 24 h unreadable gate, Phase 10). `no mark: book unreadable` splits into
`no_mark:resolved | expired | decided | halted:<state> | pre_open | bbo_error:<exc> | empty_open` (the only
class that is a venue-liquidity fact). A RESOLVED/EXPIRED market leaves the 20/tick read set (`_resolved_until`)
so its empty book no longer abandons the tick as a venue miss (the 21:52:12Z beat: rows 19 / markets 20 /
abandoned, probe:1728; ms:669-676) — but a miss is still counted when the state could not be read, so the
429-storm abandon invariant (ms docstring :27-34; tests:296-303) survives.
Tests: `test_bbo_state_is_named_and_never_a_miss` (SUSPENDED → `no_mark:halted:MARKET_STATE_SUSPENDED`, no
abandon); `test_resolved_market_leaves_the_read_set_and_never_abandons_the_tick`; `test_an_unread_state_is_a_miss`
(a raising client → miss, abandon after 3 — the pinned invariant); `test_phase_from_game_start_status_and_state`
(slug 08-30 with gameStartTime 09-02 → in_play; 09-02 slug starting 00:10Z 09-03 → in_play, not future);
`test_market_terms_reads_the_venue_record_and_fails_closed` (0.005 / 0.01 / "0.01" / NaN / 0 / missing;
min_qty 0.01 → 1, 5 → 5); `test_summarize_by_phase`; `test_048_adds_rules_text_only`; 046 column test unchanged.
Gate (24 h): 0 rows in `no mark: book unreadable` unclassified; `phase=unknown` ≤ 2% of mapped rows;
`terms` readable on 100% of mapped rows (today 98/98 on the runner); `rules_unreadable` (either text
absent) ≤ 10% of mapped usd — else Phase 10's classifier is not started; MIRRORPHASE prints would-fill per
phase per family (the P0→P1 0.50 clause is re-read on in-play plans only, decision 16).

### Phase 0c — SQL INSTRUMENTS: first sight, in-play, basis, exit leg, ladders, shadow coverage of live books
Files: workers/mirror_shadow.py (tick order: `mirror_books` state<>'closed' markets FIRST every tick, then
newest candidates under the cap; unmapped markets NOT charged a slot; `read_budget_used` = count of `pace()`
claims; `first_seen_*` stamped into `detail` per (whale, condition) — the stamp is kept only because Rule LE
goes live in Phase 3b); analytics/mirror_report.py (`first_sight` block from 046 rows: `first_seen_at =
min(at)` over rows with us_market_slug NOT NULL, `first_seen_net = his_net at that row`, pre_existing_frac,
target_flow beside target; `basis_x` block: `basis_t = mid_t − his_last_px` and `bid_t − his_last_px` by
family, phase, seconds-since-fill bucket, fill-size bucket, share of his dollars with basis > 1c persisting >
600 s; `exit_leg` block: SELL plans would-fill and time-to-touch by family; `ladders`: his fills on one market
spanning ≥ 2 distinct cents inside 600 s, by count and dollars; `track` block: per (book, tick)
|ledger_net − target_flow| / max(target, 1) p50/p90 excluding frozen and dead-band ticks;
`shadow_rows_per_book_tick`, `book_ticks_unshadowed`; `inherited_refused_usd_share` per clause (M22);
`inplay_fills` SQL over trades × market_starts with the `game_start IS NULL` share printed); api/app.py;
.github/workflows/engine-diagnostic.yml (MIRRORBASIS, MIRRORBASIS-X, MIRRORINPLAY, MIRROREXIT, MIRRORLADDER,
MIRRORTRACK, MIRRORWHY, MIRRORCASH from pmus_account's `buying_power` read); tests.
Depends on: 0a. parallel_safe_with_step9: TRUE (`rebase_on_step9_tree`; the shadow reads `mirror_books`, a
table the worker writes — read-only, no signature change).

Rule. The shadow reads the live books' markets before anything else, so `shadow_live_disagree` and M6 can
never pass vacuously (today the shadow reads the newest 20 of ~420 with skipped_markets 381-430, ms:59,
:155-167, :644-651; a live book outside the newest 20 would never be shadowed). Every instrument here is a
query over rows the shadow already writes or the trades table: first sight is `min(at)` per market (no wait,
no new column — tee/gap_r1_6.refute.engineering.md F2); in-play share is one SQL over `trades ×
market_starts` (044) with the NULL share beside it (market_starts covers marked trades only, 14,090 of
326,705 buys, so the NULL share will be large and is printed first); cross-venue basis is `his_last_px` vs
`bid/mid` on the same 046 row (the 7 of 22 BUY plans resting 0.5c-16c under the bid, 41.9% of dollars on
those rows, tee/refute_market_rest.out, split into stale-level vs basis by the age of the trigger); the exit
leg is the SELL plans' touch and time-to-touch; ladders are his own fills. The slot fix (count venue reads,
not markets) is the cheapest coverage lever there is (D29).
Tests: `test_a_live_book_outside_the_newest_20_is_still_read`; `test_unmapped_markets_cost_no_slot_and_no_read`;
`test_first_sight_is_min_at_per_market_and_a_reopen_does_not_restamp`; `test_basis_x_splits_stale_level_from_basis`
(trigger > 600 s old → stale; fresh trigger with mid − his > 1c → basis); `test_track_excludes_frozen_and_dead_band`;
`test_inplay_sql_prints_the_null_share`; `test_ladders_count_distinct_cents_inside_600s`;
`test_the_shadow_never_touches_an_order` green.
Gate (24 h): `book_ticks_unshadowed / live_book_ticks` ≤ 0.05 once books exist (a Phase 9 precondition);
MIRRORBASIS prints pre_existing_frac on ≥ 30 mapped markets (the 52.9% bound becomes a number);
MIRRORINPLAY prints after_start share by count and usd with the NULL share; MIRRORBASIS-X prints the share of
his dollars with basis > 1c for > 600 s by family; MIRRORLADDER prints the 30-day dollar share of ≥ 2-cent
clusters; MIRROREXIT prints SELL would-fill by family with a clustered interval over ≥ 30 markets;
`read_budget_used` ≤ 60 per tick with the 20-slot cap (today: 20 slots, ~11 to 0-read markets).

### Phase 1 — THE POSITION SOURCE: a per-market venue read, drift on the net, his basis
Files: workers/whale_exits.py (new `market_positions(http, address, cid)` beside `_confirm_gone`, reusing
`/positions?user&market=<cid>&sizeThreshold=0` at wx:495-497, returning BOTH tokens with `size` AND
`avgPrice`; the raw snapshot (wx:800-806) additionally stores avgPrice per token); workers/mirror_shadow.py;
analytics/mirror_live_rules.py (`drift_net_rule` beside the pinned `drift_rule`; admission's `snapshot_stale`
accepting a per-market fresh-complete read); analytics/mirror_report.py; tests.
Depends on: 0a (MIRRORSNAP), owner decision 8. parallel_safe_with_step9: FALSE (the worker's step B must
read `market_positions` instead of `snapshot_sizes`; rules gain a function the worker calls).

Rule. Unchanged from revision 1 (one paced call per book per tick, both tokens, fresh AND complete for THAT
market; drift on the NET so an equal-leg merge is drift 0; `snap_partial` still passed to `select_flatten`),
plus: the read carries `avgPrice` per token → `his_vwap_open` for M21 and decision 13's option C. The venue
`avgPrice` semantics (current holding after merges vs lifetime average) are unread; the first read on a
market with a redemption decides whether option C may key on it.
Tests: as revision 1 plus `market_positions` returns avgPrice and marks complete; a 4xx/None → None.
Gate (24 h): MIRRORSNAP RN1 per-market fresh-complete share ≥ 0.95 of planned markets (today 0); net drift
p90 ≤ 0.05 (today whole-book 0.4531, probe9:1658); `snapshot_stale` on RN1 candidates < 5% of ticks;
`his_vwap_open` readable on ≥ 0.95 of booked markets.

### Phase 2 — COVERAGE THE MIRROR OWNS (premap fixes + the exact lane inside map_market + the slot fix)
Files: workers/premap.py; copy_sports.py; workers/mirror_shadow.py; live_executor.py (re-export of the moved
grammar); tests/test_premap*.py; tests/test_copy_sports*.py; tests/test_mirror_shadow.py;
tests/test_tennis_league_gate.py.
Depends on: 0a (MIRRORCOVER says which class is ours). parallel_safe_with_step9: TRUE (`map_market(pool,
fills, pmus=None)` keeps its two-arg form).

Rule. Unchanged from revision 1 (a)-(g): phantom-line fix; the yes/no IDENTITY branch (source 'premap'); the
exact lane inside `map_market` over ALL candidates behind its own TTL and `MIRROR_MAP_READS_PER_TICK=12`,
labelled 'exact'; the ledger source filtered `status <> 'rejected' AND error NOT LIKE 'quarantined%'` and
carrying the row's class; the grammar moved to `copy_sports`; `market_type_of` typing segment totals as
'prop'; `COALESCE(t.outcome, mt.outcome)`. Two amendments from round 1: the 'exact' label is legitimate only
because the mirror calls the SAME `pmus.resolve_market_exact` the copy lane labels 'exact' at le:8184-8341
(the refuter's "class laundering" objection holds for anything else) — a source-inspection test pins that
`map_market`'s exact hits come from that function and from no other; and the mirror's mapping reads count
against the key-wide budget of D29, never against the tick's market slots.
Tests: as revision 1 plus `test_map_market_exact_hits_come_only_from_resolve_market_exact` (inspect.getsource).
Gate (shadow, 24 h): MIRRORCOVER-CELL tennis|moneyline listed_unmapped/(mapped + listed_unmapped) ≤ 20% by
markets AND usd (spec:441; today unprinted); MIRRORSRC `admissible_share` ≥ 0.90 (today: the ledger source
carries 69.4% of readings); soccer per-team markets appear as mapped_premap.

### Phase 3a — THE BANKROLL RATIO, measured in the shadow (with Rule LE's flow target beside it)
Files: analytics/mirror.py (`ratio_bankroll`; pure `flow_net`, `pre_existing_ratchet` (pro-rata), `vwap_since`);
workers/mirror_shadow.py (`compute_ratio` second query: 30-day open cost on unresolved markets;
`MIRROR_SHADOW_RATIO_MODE`; `target_flow` beside `target`); analytics/mirror_report.py; engine-diagnostic.yml
(MIRRORRATIO prints ratio_bankroll and deployed_usd_30d; MIRRORSHAPE; MIRRORCASH beside it); tests
(`_Pool.fetch` gains an ADDITIVE branch keyed on the new SQL prefix).
Depends on: 0a, 0c, owner decisions 1 and 20. parallel_safe_with_step9: TRUE (`rebase_on_step9_tree`).

Rule. Unchanged from revision 1 (deployed_usd_30d overstates him → r too small never too large;
`ratio_bankroll = min(RATIO_MAX, MIRROR_BANKROLL_USD / deployed_usd_30d)`; shape ratio, cap-bound share,
dead-band dollars per mode from the DB; `int()` stays), plus the two round-1 readings that decide r: the
account's buying power ($31,502.13, probe:77) bounds the fundable bankroll, and the MERGEPNL denominator
($25,086,278, probe:1843, includes resolved balances) bounds r from below at 0.126% — the true 30-day open
cost is smaller and makes the same $31.5k a larger r; the shadow prints both denominators. `target_flow` is
computed under Rule LE (pro-rata ratchet, first sight from Phase 0c) beside the byte-identical `target`, so
the dollar effect of following his flow rather than his cumulative net is a number before it is a rule.
Tests: ratio_bankroll = B/D and None on D ≤ 0; MODE per row; the shape statistic on a fixture;
`test_flow_since_first_sight_ratchets_pro_rata_on_reductions` (12,000 → 11,000: block 11,000 × 10/12 →
we hold 8.3% less, not 50%); `test_a_crossing_zeroes_the_block`; `test_mirror.py:52-55` int pins unchanged.
Gate (shadow, MODE=bankroll, 24 h, ≥ 30 admitted markets): shape ratio within [0.9, 1.1] on ≥ 95% of his
admitted dollars (today 0.34x-52.63x); cap-bound share of his dollars ≤ 5% (today 68.7-85.1%); dead-band
share printed at the chosen r (at r=0.126% the $5 band drops his markets under $3,968 — decision 21);
`deployed_usd_30d` readable on every hourly refresh; ratio drift < 10%; `target_flow` vs `target` dollar gap
printed (the pre-existing block never bought under (A), decision 13).

### Phase 3b — THE BANKROLL RATIO, live rules: cost-basis cap, reservation, cash room, Rule LE, the constants at r
Files: analytics/mirror_live_rules.py (`MIRROR_BANKROLL_USD` MEASURE/PROMOTED; `mirror_target(...,
bankroll_room_usd, pre_existing_net)`; `first_sight_unreadable`; admission `late_entry` (new-book only) and
`insufficient_cash`; the constants table below); live_executor.py (a mirror reservation lock/counter copied
from `_REST_LOCK/_REST_RESERVED_USD`, le:5874-5875, :7067-7085; `_MIRROR_BOOK_INSERT_SQL` writes
first_seen_at/first_seen_net/pre_existing_net); api/pmus_account.py (a pure `cash_room(bal, reserved)`
beside the existing `buying_power` read at :321, the SDK's `/v1/account/balances`, resources/account.py:16);
backend/tests/test_mirror_live_rules.py; backend/tests/test_mirror_live_ledger.py.
Depends on: Phase 3a gate, decisions 1, 13, 15, 20. parallel_safe_with_step9: FALSE (`mirror_target` gains
inputs the worker passes; the INSERT gains columns from 049).

Rule. As revision 1 (`ratio_eff = ratio_bankroll × min(1, clip/50)`; `cap_usd = min(MIRROR_NET_CAP_USD,
bankroll_room)` on COST basis; reservation under a mirror lock; `MIRROR_DAY_USD` a downward handle beside
r × his entries/day; a book's ratio fixed at open), plus: (i) `cash_room = min(bankroll_room, buyingPower −
reserved_by_other_lanes)` read once per tick from `/v1/account/balances` and refused by name
`insufficient_cash` (today no money path reads buyingPower; a bankroll the account does not hold would
surface as venue `place_refused`); (ii) Rule LE: `target = ratio_eff × flow_net` with the pro-rata ratchet,
`pre_existing_net=None` → `first_sight_unreadable` (no plan), `late_entry` at NEW-book admission when
`ratio_eff × flow_net × mark < MIN_MOVE_USD` (never on an open book: there flow → 0 is a reduce), a
`late_entry` refusal does not consume `opened_today`; shadow `target` switches to flow in the SAME change
(D25); (iii) the blast-radius constants become code defaults derived from r (decision 15) — each remains a
downward env handle. The constants at r (his entries $1,363,663/day, probe:1843; ~660 markets/day; daily
copy-ROI sd 0.282 of stake over 15 days ≥ $1k, §A):

| constant | today | at r_measure = 0.126% ($31.5k on the MERGEPNL denominator) | at r = 1% | arithmetic |
|---|---|---|---|---|
| MIRROR_DAY_USD (gross buys/day) | $1,250 (rules:214) | $1,718 | $13,637 | r × $1,363,663 |
| MIRROR_MAX_BOOKS_PER_DAY | 5 (rules:212) | bounded by the read budget: ≤ 60 (1 read/book/tick) until the key-wide bucket exists (D29) | same | 85.7 paced reads/tick − 5 positions − 20 candidates |
| MIRROR_MAX_LIVE_BOOKS | 5 (rules:211) | same bound | same | same |
| LIVE_MAX_CLIP_USD per order | $250 (le:2610) | $250 (a $737 target at r=1.9% is 3 sequential rests under 047:113-114 one-open-per-book) | raise with the ladder decision 22 | — |
| MIRROR_LOSS_STOP_USD (24 h realized) | $250 (rules:217) | stop = k × sigma_day, sigma_day ≈ sigma_per_dollar × day_gross / sqrt(books_day): at 1.302 × $1,718 / sqrt(20) = $500 → a 2-sigma stop is $1,000 | 1.302 × $13,637 / sqrt(60) = $2,292 → 2-sigma $4,584 | a $250 stop at $13,637/day is 1.8% of a day's gross; with daily ROI sd 0.28 of stake it trips on ~47% of days (§A) |
| PMUS_LOSS_BREAKER_USD (global, terminal rows) | $5,000 (le:2674-2675) | unchanged | unchanged | shared with the per-fill sleeve; the mirror's stop sits under it |
| MIRROR_MAX_ORDER_OPS_PER_TICK | 6 (rules:222) | 6 until the key-wide bucket; then books × 2 per simultaneous kickoff | same | 5 books cancel+place at one kickoff = 10 ops (tee/lifecycle.refute.engineering.md missed 2) |
| MIRROR_MAX_REPLACES_PER_HOUR | 12 (rules:223) | the measured DOWN-move rate on in-play books (28 of 46 600-s moves are down on the one read book; 15.3 cent-changes/h) → 16-20 by code, after MIRRORLADDER prints | same | D20 |
| MIN_MOVE_USD | $5 (mi:64) | drops his markets under $3,968 | under $500 | $5 / r; decision 21 |

Tests: `bankroll_cap` and `insufficient_cash` by name; room on cost basis; reservation released on refusal
and on terminal; `test_caps_carry_the_spec_defaults` pins updated to the table; a demoted clip still zeroes
increases; `test_mirror_target_sizes_the_flow_and_an_unread_first_sight_is_no_plan`;
`test_late_entry_is_named_at_admission_only_and_never_consumes_opened_today`;
`test_late_fills_by_timestamp_raise_the_block_not_the_flow`; `test_open_stores_first_sight_in_the_one_transaction`.
Gate (live, after Phase 7 rungs 1-4, 7-8, 14): per-book shape ratio within [0.9, 1.1] on ≥ 95% of his
dollars on the books held; `bankroll_cap` never binds while Σ books < bankroll; day cap binds at the
proportional figure; 0 venue rejections for funds over 24 h at r_measure (`insufficient_cash` names them
first); MIRRORCASH printed per tick; `mirror_loss_stop` false trips = 0 over 7 days at the 2-sigma stop.

### Phase 4 — LIFECYCLE FIXES a LONG book needs: its own trigger, level freshness, the market's tick, in-play facts, ranking, reopen, fidelity, the modify question
Files: ingestion/pipeline.py (fan-out of fresh fills for `mirror_whales()` to a reconciler queue,
INDEPENDENT of the copy lane); live_executor.py (`_mirror_notify` for a mirrored whale issued ABOVE the exit
dispatch at le:7639 AND above `probe_disabled`/`halted` at :7527-7532 — today `execute_copy` returns at
le:132 when `copy_probe_enabled` is False and `maybe_execute` refuses at :7527-7534 before the notify at
:7662, so turning the per-fill lane off silently degrades the mirror to 30-s polling; `_CENT_TOL` (le:5966)
→ `tick/2 + 1e-4`; `rest_tick(wire, intent, tick=0.01)`); analytics/mirror_live_rules.py (`level_stale`;
`opened_today` reopen exemption; `buy_wire/sell_wire/buy_price/sell_price/plan_wire/_cent/keep_or_replace/
at_or_through/room_scale` gain `tick=0.01` (default keeps every existing test green); `room_scale(...,
min_qty=1)`; `take_arms(status, state)`; admission NEW-book facts `venue_state`, `phase`, `game_start_ts`
(`market_state:<state>`, `game_start_unread` fallback to the slug date); `whale_cut` clause (spec §5 :421
names it; rules has none; a DB clip map can override the 0 clip, le:3180-3186 — only the env allowlist
stops a cut whale today); `keep_or_replace` returns `keep` on an unchanged cent under GTD after rung 12
reads priority); pmus.py (`_amount(price, tick=None)`: decimals from the tick, REFUSES an off-tick price
(`off_tick`) instead of rounding; `submit_fok(..., tick=None)` keeps today's bytes for every existing caller;
`_post_only_refusal` carries status+body into `refusal_text`; preview `expected_cost` from the FORMATTED
wire); workers/mirror_shadow.py (`his_level_fill()`; candidate ranking by his dollars); migration
049_mirror_fidelity.sql (mirror_orders.trigger_trade_id, his_fill_ts, first_fill_at, commission_usd,
commission_px, wire tick; mirror_books.first_seen_at, first_seen_net, pre_existing_net, his_vwap_since,
his_vwap_open, terms JSONB, game_start_ts, venue_state_last, phase_last; trades.taker BOOLEAN NULL);
api/app.py (MIRRORFIDELITY payload); engine-diagnostic.yml; tests incl. test_mirror_live_migration.py
(049 is an ALTER; 047 stays CREATE-only), test_mirror_live_handoff.py, test_pmus_post_only.py, test_pmus.py.
Depends on: Phase 1, 0b, 0c, decisions 16, 22. parallel_safe_with_step9: FALSE (rules the worker calls gain
inputs; the le hand-off region; the worker writes 049 columns; the pipeline fan-out is the worker's wake).

Rule. (i) TRIGGER: the reconciler's wake comes from the ingestion fan-out (fresh fills of MIRROR_WHALES by
`condition_id`) and, as a fallback, from a cursor over `trades` since its last tick — never only from the
copy lane's hand-off; `wake_source ∈ {fill, poll, cursor, none}` is a census field. The hand-off pins
(test_mirror_live_handoff.py:117-125) pin only the gate block and stay green. (ii) LEVEL FRESHNESS as revision
1 (`MIRROR_LEVEL_MAX_AGE_S=600` OR within 1c of the bid, else `level_stale`, re-checked on increases;
`side_band` stays open-only), now split by Phase 0c's stale-vs-basis reading. (iii) THE TICK: the wire is
priced on the market's own `orderPriceMinTickSize` — floor = `floor(round(p/tick, 6)) × tick`, step-down
`round(w − tick, 6)`, top tick `1 − tick`, on-ladder `tick ≤ w ≤ 1 − tick` — because on 42.9% of his mapped
markets a cent wire rests half a tick behind the bid and `_amount` ROUNDS 0.525 to "0.53" (a BUY above the
computed wire: the preview guard compares the venue's cost to the unrounded float and passes, pmus.py:2233);
the post-condition is `wire ≤ his`, `wire ≤ bid`, on the ladder, and `wire ≥ min(his, bid) − tick`
(the refuter's correction of the lens's "≥ his − tick"). (iv) IN-PLAY: admission of a NEW book requires
`venue_state == MARKET_STATE_OPEN` and `phase ∈ {pre_open, in_play}` read, else `market_state:<state>`;
step-M's `market_closed_or_resolved` is TRUE when the venue says RESOLVED/EXPIRED/closed OR our table does
(either closes, neither opens); a live book reading SUSPENDED/HALTED cancels its rests and holds
(`halted:<state>`); `take_arms` never arms on a 400 while the state is not OPEN. (v) RANKING by his dollars
(Phase 1 shares × his VWAP, or burst dollars), not recency; dollars-at-the-mark ranking stays refused (450
BBO reads). (vi) REOPEN exempt from `opened_today`. (vii) FIDELITY as revision 1 (dollar-weighted fill
FRACTION per HIS fill; `at ≤ his price` carries no information). (viii) QUEUE: after rung 12 reads whether
`orders.modify` keeps priority, `keep_or_replace` keeps an unchanged-cent rest until its GTD expiry instead
of replacing at TTL (today rules:731-734 cancels+places every 600 s = back of the FIFO and 1 of 12 replaces),
and uses modify-in-place for a cent change if priority survives a price modify (rung 12 decides; the replace
budget follows).
Tests: `copy_probe_enabled=False` and `copy_halted()` still wake; a 281-s-old poll fill wakes; `wake_source`
emitted; `level_stale` truth table; `test_wires_on_a_half_cent_tick` (property sweep at tick 0.005:
`wire ≤ his`, `≤ bid`, on-ladder, `≥ min(his,bid) − tick`); `test_default_tick_keeps_every_cent_rule` (the
existing 70,099-point and full-precision sweeps unchanged); `test_amount_carries_the_market_tick_and_refuses_off_tick`
(`_amount(0.525, tick=0.005) == "0.525"`, `_amount(0.525, tick=0.01)` → None, `_amount(0.52)` byte-identical
"0.52"); `test_params_unchanged_without_a_tick` (the post-only fixture `_PRICE "0.30"` unchanged);
`test_a_400_from_a_non_open_state_never_arms_the_take`; `test_admission_refuses_unless_the_venue_state_is_open_by_name`;
`test_venue_resolved_closes_the_episode_before_our_table_does`; `test_game_too_far_out_reads_the_venue_start_before_the_slug`;
`test_increase_recheck_is_the_starred_clauses_only` unchanged (venue_state is not starred);
`test_a_cut_whale_never_opens_or_increases_a_mirror_book_by_name`; `test_room_scale_min_qty`;
`test_keep_on_unchanged_cent_under_gtd` (gated on the rung flag); 049 ALTER pins; MIRRORFIDELITY SQL on a fixture.
Gate (shadow first): plans resting > 1c under the bid with a trigger older than 600 s = 0 (today 7/22); the 20
markets read per tick carry ≥ 80% of his 6 h gross dollars; 0 increases on a market whose state was not read
OPEN; `halted:*` printed (expected 0; any non-zero is the first observation). Live (after Phase 7): wake
fires on 100% of his fills on booked markets with the copy lane DISABLED (`wake_source=fill` share ≥ 0.85,
the chain-lane share, probe:1548); reduction wake-to-plan p50 ≤ 10 s; MIRRORFIDELITY by_usd ≥ 0.5 inside
600 s on in-play fills with react_p50 ≤ 10 s, p90 ≤ 30 s (his clusters at 5 s: 21.6/h; a rest ≥ 30 s old is
off his level for 20% of fills, ≥ 600 s for 70%); `off_tick` = 0 and rung 11 reads a half-cent rest back;
`replace_capped + take_capped` = 0 on in-play books over 24 h.

### Phase 5 — SHORTS (P2): books in leg space, his other token as BUY_SHORT on the same slug
Files: analytics/mirror_live_rules.py; live_executor.py; migrations/050_mirror_shorts.sql; analytics/engine.py;
workers/whale_exits.py; workers/mirror_shadow.py; tests as revision 1.
Depends on: Phases 1, 3b, 4 (tick-aware wire), Phase 7 rung 5, Phase 0a's MIRRORSHORT gate, decisions 2, 5.
parallel_safe_with_step9: FALSE.

Rule. Unchanged from revision 1 in full (book intent fixed at open; `sign_flip` episode close; leg-space
BookState; `_state_nums` admits a short leg; wire `sell_price(round(1−q, 6), ask)` with intent BUY_SHORT;
the take as an IOC with intent BUY_SHORT, sell=False; a short REDUCE only via `close_position` when sole
holder until rung 5 reads a resting SELL_SHORT back, else `short_reduce_unproven`; collateral-space sizing
`(1 − wire) × qty` in room_scale/wire_usd/day cap; `gross_buy_usd += (1 − px) × q`; `OpenOrder.intent`
compare; drift and `_confirm_gone` on the token carrying his net; `wrong_sign_trip = sign(venue) ≠
sign(ledger)`; own settlement figure per leg with mixed-sign `book_settle_disagree`; `short_model_disarmed`
when `le.short_model_confirmed()` is False; the preview guard bounds nothing on a short so the mirror bounds
pay itself), with the tick from Phase 4 applied to `sell_price`.
Tests: as revision 1.
Gate: shadow (Phase 0a columns, 7 d): `would_fill_short` clustered lower bound ≥ 0.50 over ≥ 30 markets;
rung 5 lines read from the venue; live: ≥ 30 closed short books with `at_or_better` = 1.0 on the short leg,
`wrong_sign_trip` = 0, `book_settle_disagree` = 0, `overfill` = 0; short-side dollar coverage ≥ 0.95 of his
negative-net dollars on mapped admitted markets (today 0; 55% of his mapped markets).

### Phase 6 — SOCCER: per-condition books on three-way games, and the 2026-08-12 price floor by name
Files: analytics/mirror_live_rules.py (per-CONDITION claim; the two-longs shape keeps `per_side_unsupported`
pinned at open); live_executor.py (the one-per-game read at le:8645-8660 and the never-add prior le:8609-8630:
a Draw book is not "the game already copied" for the mirror's sibling conditions; per-fill copies on the game
stay refused); migrations/051_mirror_game_claims.sql (per-condition claim for lane='mirror'; a game-level cap
column); workers/mirror_shadow.py (`_choose_long` pins the long token at book open; per-side printed;
`floor_refused_usd` / `floor_refused_markets` split -draw / dog / favourite as a Phase 0-style instrument);
copy_sports.py untouched (the floor stays where the owner made it) — the mirror's price fact carries
`MIRROR_SOCCER_FLOOR=inherit|open_only|off` (default inherit = fail closed) in the WORKER's cell-price fact,
rules unchanged; tests as revision 1 plus the floor tests below.
Depends on: Phase 5 (his No = BUY_SHORT on the same slug), decisions 4 and 10. parallel_safe_with_step9: FALSE.

Rule. As revision 1 (an atc- contract is one condition whose two sides share the identifier; up to three
books per game under one GAME-LEVEL cap Σ(book cost) ≤ r × his game dollars; the referee claims per condition
for mirror books). Round 1 adds the fact the program never named: `cell_gate_soccer_price_floor` (cs:91-99,
owner-approved resume design 2026-08-12, starred = re-checked on every increase at HIS latest BUY) refuses
every YES-on-draw book (a draw prices 0.20-0.35; `copy_verdict('rn1', '…-draw', 0.28)` → floor; the venue
account itself holds `atc-epl-bha-lee-…-draw` at 0.2716, probe:257-264) and every dog book by construction,
while costing ~1% of his soccer dollars at his level today (1/13 readings, $389). Phase 6 therefore runs
under decision 10: (c) `open_only` for P1 (judged at the book's entry level, never re-judged on increases,
lifted by name for `-draw` conditions), (b) `off` for lane='mirror' only with the Phase 6 numbers in hand.
Tests: three books on one game_key admitted for the mirror, a fourth refused `game_cap`; a per-fill copy on
the game still refused by name; `_us_game_key` equality pinned for -bur/-mid/-draw;
`test_mirror_floor_switch_defaults_to_inherit`; `test_open_only_judges_the_floor_at_the_books_entry_level`;
`test_off_skips_only_the_floor_clause` (PAUSED/HALTED/BLOCKED still re-judged);
`test_a_draw_condition_book_is_refused_by_the_floor_under_inherit`.
Gate: shadow: his soccer games with > 1 mapped condition read a target on every condition (today 12
distinct -draw slugs seen, none targeted); `floor_refused_usd` printed by class for 24 h before (b) is chosen;
live: ≥ 30 closed soccer condition-books, `book_settle_disagree` = 0, `game_cap` breaches = 0.

### Phase 7 — VENUE 1-SHARE PROBES and the reads only the venue can answer (rungs 1-17)
Files: .github/workflows/engine-diagnostic.yml (`mirror-probe` step, MIRRORPROBE/WSPROBE lines); api/app.py
(a `require_admin` probe endpoint that runs tee/ws_probe.py parts 0/1/3 on the service that holds the key,
render.yaml:40-42, and part 2 only with PROBE_PLACE=1; the first-fill echo writer `side_echo_mirror`);
pmus.py (`take_arms` input shape: 200 + ORDER_STATE_REJECTED as well as HTTP 400, SDK types/orders.py:31,:40;
commission fields read into the execution record; `markets.settlement(slug)` reader; `account.balances`
reader); analytics/mirror_live_rules.py (`take_arms` accepts both shapes); tests/test_pmus_post_only.py;
tests/test_mirror_live_rules.py; a scratchpad runbook.
Depends on: step 9 (worker) and step 10 (endpoints) landed. parallel_safe_with_step9: TRUE for the files
(additive); the rungs RUN after step 9.

Rule. No default rides before its rung prints its line from the venue, all under `MIRROR_MAX_LIVE_BOOKS=1,
MIRROR_NET_CAP_USD=25`: rungs 1-10 as revision 1 (post-only both refusal shapes; GTD raw read-back; cancel
read-back; partial booked once; SHORT rest/SELL_SHORT/BUY_LONG-against-negative reads; per-side game key;
settlement to the cent ≤ $0.05; reaper isolation; wrong-sign trip; commission fields on 100% of executions);
new: (11) TICK — on a 0.005 market rest 1 share post-only at a half-cent one tick under the bid, read
`order_status().price` back (`MIRRORPROBE tick_rest sent=0.xx5 read=0.xx5`); whether the venue accepts a
3-decimal `price.value` is untested (`Amount.value` is `str`); (12) QUEUE PRIORITY — rest 1 share, then (a)
modify quantity down at the same price, (b) modify price, (c) cancel+replace; read createdAt/updatedAt and
infer priority by a second 1-share order placed after each (which fills first when the level trades; or by
book position if MARKET_DATA carries order ids); the reading decides Phase 4 (viii); (13) TRANSPORT — the WS
probe through the admin endpoint: `WSPROBE P1 N=5/20/60` message rate, depth levels/side, tape presence with
maker/taker side on markets we hold no order in, CONNCAP, REST 429 count while sockets are open; `P2`
private ORDER fill latency vs `order_status` polling on one 1-share rest (execution.order.intent printed to
confirm the leg); `P3` events.list bestBidQuote/bestAskQuote vs markets.bbo agreement and `updatedAt` age on
30 paired reads (if ≥ 95% agree and move with the bbo, one 6-page board walk replaces up to 420 BBO reads as
the ranking mark); (14) CASH — `account.balances` read: balance, buyingPower, unsettledFunds, and their
consistency against Σ open orders + positions on two reads 60 s apart; (15) SETTLEMENT — `/v1/markets/{slug}
/settlement` on one settled slug from the account's history and on one ITF walkover if one exists
(`settlementPrice` ∈ {0,1} or not); (16) STATE — a 1-share post-only rest on a market read
PREOPEN/SUSPENDED if one appears in the window (accepted / 400 / queued), and the take never arms; (17)
FIRST-FILL — the probe endpoint writes `side_echo_mirror.ok` after reading rung 4's fill back, so Phase 4's
`first_fill_ok` re-key cannot deadlock the first book (D32). Rate limit: log status + Retry-After +
X-RateLimit-* at the two existing catch sites (pmus_account.py:545; premap.py) the next time the venue 429s;
never step the gap down on the live key.
Tests: both post-only refusal shapes → the refusal dict and `take_arms` True; commission fields parsed;
settlement reader parses a non-0/1 `settlementPrice`; the probe step's jq parses on absent lines; the probe
endpoint refuses without PROBE_PLACE for part 2.
Gate: every MIRRORPROBE line prints as specified; rung 7 gap ≤ $0.05; commission non-null on 100% of mirror
executions; rung 11 reads the half-cent back; rung 12 yields a priority verdict for each of (a)(b)(c); rung 13
yields the transport decision (decision 14) with its numbers; rung 14 two consistent balance reads; rung 15 a
`settlementPrice` read.

### Phase 8 — THE TAKER FLAG (measurement only; no rule keys on it)
Files: ingestion/reconciler.py; migration 049 (trades.taker, shared with Phase 4); api/app.py (TAKERSHARE;
MIRRORFIDELITY maker_fid/taker_fid); engine-diagnostic.yml; tests/test_reconciler*.py.
Depends on: Phase 4 (049). parallel_safe_with_step9: TRUE.
Rule, tests, gate: unchanged from revision 1 (one extra `takerOnly=true` page per hourly walk intersected by
dedupe_key; `match_rate` ≥ 0.9 else NULL; by count 2/66 on one match, by dollars anywhere in [~0, 68%]).

### Phase 9 — THE SHADOW-vs-LIVE CROSS-CHECK, the proof horizon, HIS P&L ON OUR FRACTION, settlement classes
Files: tests/test_mirror_live_e2e.py (new: `mirror_shadow.tick_once` and the live tick on ONE fake
pool/venue, `shadow_live_disagree == 0`); analytics/mirror_grade.py (step 10's module: `proof.assess`'s
`n_needed_at_target / n_still_needed / half-width`; the M18 replay; `settle_class`); analytics/resolution_rules.py
(new, pure: `settle_class(settlement_px, global_payout, profile)`); analytics/engine.py (`settle_px` derived
from the raw archive per D17; `live_orders.payout` written from it; `raw.settle_class`);
analytics/merge_pnl.py (no change: `_replay_stepper` :144 and `replay` :428 are pure and reused);
engine-diagnostic.yml (MIRRORGRADE prints `settled=<x> own=<y> gap=<z> class=<c> px=<settlement_px>
payout=<global_payout>` and `his_long_only_pnl`, `his_capscaled_pnl` beside capture; RESCORE gains
`split_candidates=<n>`); gamma.py (`split_candidate` census, never sets resolved); tests.
Depends on: step 9, step 10, Phase 0b (rules text), Phase 0c (shadow coverage of live books).
parallel_safe_with_step9: FALSE (imports the worker).

Rule. (i) The live book does what the shadow measured, proved by one test on one fake, and `shadow_live_disagree
= 0` counts only when Phase 0c's shadow coverage of live-book ticks is ≥ 0.95. (ii) MIRRORGRADE prints the
proof horizon so D3's interval is never mistaken for a fidelity reading (31,052 games at his edge; ±46.6 pts
at 30 games). (iii) M18's denominator gets its builder: per book, `merge_pnl._replay_stepper` over his fills
on the condition cut to the episode window [opened_at, closed_at] and to the SIGN the book held, the resulting
position path scaled by the book's r and by the cap-bound target path (`target_shares` per tick from the
shadow rows beside the book), settled at the same resolved price → `his_long_only_pnl` and `his_capscaled_pnl`
published beside `capture`; validated on the Nakashima fixture (fill-ordered locked $2,938.93 on 28,163 pairs,
88.4% of pair P&L before the crossing, tee/lifecycle.refute.market.md:31-36) and on one MLB book. Failure
modes, each fail-closed: his fills missing SELLs/merges (drift p90 0.45) → the path is bounded by the Phase 1
per-market net at each tick and the row prints `path_drift`; a book whose shadow rows are missing for > 5% of
its ticks → `capture_unreadable`. (iv) Settlement classes: `settle_class ∈ {clean, side_flip, venue_void,
global_void, both_void, global_unresolved, venue_unread}` from the venue settlement price (derived per D17,
`/settlement` as the cross-check once rung 15 reads) and the global payout; `book_settle_disagree` counts
ONLY `clean` books with |own − venue| > $0.05; every other class increments `settle_gap_rule:<class>`;
`side_flip` on a book with no rule mismatch is the wrong-side incident's shape and trips `mirror_live=false`
as `wrong_side_settle`; a void book is reported in `capture_ex_void`. (v) The void branch fails closed:
`split_candidate` (closed, not resolved, exactly [0.5,0.5] for ≥ 48 h) is a census count and an owner-visible
list; nothing sets `resolved` until the list is audited (the 2026-08-24 rescore fell +$317.91 → +$85.88 on
187 RN1 rows with no cause split, probe403.log:2334-2335; 101 of 2,721 settled slugs have no venue verdict,
probe:1461).
Tests: the e2e test; `settle_class` matrix incl. NaN/None/str → `venue_unread`; `book_settle_disagree`
increments only on `clean`; `side_flip` with empty mismatch → `wrong_side_settle`; the replay on the Nakashima
fixture reproduces $2,938.93 and the pre-crossing 88.4%; `capture_unreadable` on missing shadow rows;
`split_candidate` never resolves a market.
Gate: `shadow_live_disagree` = 0 over 24 h live with shadow coverage ≥ 0.95; MIRRORGRADE prints
`n_needed_at_target`, `his_capscaled_pnl` and `class` on every closed book; `settle_gap_rule:*` and
`split_candidates` printed.

### Phase 10 — RESOLUTION-RULE PARITY: the classifier and the `rule_mismatch` admission clause
Files: analytics/resolution_rules.py (`rule_profile(text) -> Profile|None` keyed on the quoted sentences of
tee/gap_r1_3.md §1 as the fixture set; `rule_mismatch(g, us) -> list[str]`; ITF from the venue slug);
analytics/mirror_live_rules.py (`AdmissionFacts.rules_ok/rules_why`; clause `rule_mismatch:<field>` ordered
after `per_side_unsupported`, before `market_closed`; not in the increase set; the `_admitted` test helper
gains `rules_ok=True`); analytics/mirror_report.py (`rule_mismatch` by family with usd); engine-diagnostic.yml
(MIRRORRULES); tests/test_resolution_rules.py; tests/test_mirror_live_rules.py.
Depends on: Phase 0b's 24 h `rules_unreadable ≤ 10%` gate; decision 17. parallel_safe_with_step9: FALSE
(admission facts the worker passes).

Rule. Both venues' rule texts (stored by Phase 0b) are classified into a profile (retire, walkover, cancel,
window_days, basis, shortened); an unmatched sentence leaves the field None → mismatch by name (fail closed).
Under decision 17's recommended policy (b): REFUSE `basis` and `retire` mismatches (a sign can flip — a cup
match's US two-way contract may include ET/pens while his token is regulation time), FLAG `walkover/cancel/
window` mismatches (bounded by |0.50 − p_last| × shares ≤ the cap, rare) and carry the flag into
`settle_class` so M16 names them. The everyday events read as parity in the snippets (retirement after the
first serve on ATP/WTA IF the first global phrasing is the moneyline's — UNREAD until one Gamma description
is stored; league soccer 90 min; MLB official result); the divergent class is void-shaped (he is paid 0.50,
we are paid the last mark).
Tests: `rule_profile` on each §1 sentence; the ITF exception; `rule_mismatch` matrix; admission order and the
increase re-check ignoring rules; `rules_ok=None` → `rule_mismatch:unreadable`.
Gate (24 h shadow): MIRRORRULES prints mismatch clauses by family with usd; `unreadable` ≤ 10% of mapped usd;
0 books opened on a `basis`/`retire` mismatch; the dollar share refused printed (today: ITF 0.1% of shadow
dollars, cup soccer dfb $21 in the sample — the count of triggering events is unmeasured because voids are
invisible in `markets`).

### Phase 11 — DERIVATIVES AND THE REST (totals, spreads, segment props, undated) — behind their own shadow gate
Files: analytics/mirror_live_rules.py (`MIRROR_FAMILIES` widened per family behind `MIRROR_FAMILY_<fam>=on`
handles, default off; game-level cap shared with Phase 6's column); copy_sports.py (`market_type_of` segment
fix from Phase 2); workers/mirror_shadow.py (family-split instruments already in 0a); premap.py
(`resolve_derivative_exact` lane inside `map_market` for tsc-/asc- when the family handle is on); tests.
Depends on: Phases 2, 3b, 6 (game cap), decision 19. parallel_safe_with_step9: FALSE.

Rule. A per-market book per derivative under the game-level cap, admitted only for a family whose own
shadow gate has read clean for 7 days. Numbers: the total family is $25,357.54 of his $819,395.19 mapped 24 h
dollars (3.1%; terms2), and $3,228 of $218,142 at the mark in the shadow sample (1.5%,
tee/lifecycle.refute.market.md:20-23); his MLB moneyline carries $40,154 vs $3,228 in totals (93%/7%); the
TYP census reads moneyline 68,313 / total 43,578 / spread 9,165 / prop 2,840 / exact_score 2,318 / unknown
19,428 rows (probe:2110-2116) — a count mix, not a dollar one; MLB totals are two-sided on 8/16 open lines
(77.7% of their dollars) with spreads up to 31c (tee/gap_r1_4.md §2b); 28 of 33 mapped totals sit on the
0.005 tick. Undated/futures markets: none in the 24 h window (SWEEPMIX undated=0 is tautological; the
MIRRORCOVER `undated` class is the number).
Tests: a family handle off refuses `family` as today; on, a tsc- book is admitted under the game cap; the
1H-total typing pin; `MIRRORCOVER undated` counted.
Gate: per family, 7 d shadow: would-fill (non-legacy, in-play) clustered lower bound ≥ 0.50 over ≥ 30 markets,
two-sided share ≥ 0.80 by markets, mapped share ≥ 0.80 by usd; then the same live gates as a moneyline book.
M1-total (all families) printed beside M1.

## 3b. GATE STATISTICS (critic gap 11): estimator, clustering unit, minimum n, the rule that passes

| gate / metric | estimator | cluster | min n | interval rule | reachable inside its window? |
|---|---|---|---|---|---|
| would-fill (M8, Phases 0a/2/5/11) | proportion over resolved NON-LEGACY plans | market (`rate_with_ci` in the tree: proof.roi_with_ci on a 0/1 stake — one touch resolves every open row of a market) | ≥ 30 markets | clustered 95% lower bound ≥ 0.50 | yes: 5,275 resolved plans over 99 markets in 24 h (terms1); binomial today [0.4714, 0.5100] on 1265/2578, clustered is wider → today FAILS |
| would-P&L (M9) | Σ(payout − would_px) × qty on would_fill=true plans | game | ≥ 30 games | lower bound > 0 reported, never gated | ≥ 30 resolved games within ~2 days at ~50 mapped/day |
| drift (M5), tracking error (M6), basis (M21), shape (M7) | nearest-rank p90 (mr `_p`) | market | ≥ 30 markets AND ≥ 24 h (day and night books) | point p90 ≤ threshold; the p90's own bootstrap interval printed, not gated | yes |
| coverage shares (M1, M2, M3, M13, M22, M24) | dollar-weighted share | market | ≥ 30 markets | point value with the paired share printed; MIRRORCOVER dollars are a lower bound when the crawl truncates (terms2 page 13) | yes |
| in-play two-sided share (Phase 0b/4) | share of in-play mapped markets with a two-sided book | market | ≥ 30 in-play mapped markets | point ≥ 0.80 by markets AND ≥ 0.90 by usd | yes: 98 mapped/24 h |
| terms readability / phase unknown / no_mark unclassified | counts | market | all rows | exact 100% / ≤ 2% / 0 | deterministic |
| Phase 1 snapshot usability (M4) | share of planned markets with a fresh-complete per-market read | market | ≥ 30 | ≥ 0.95 point | yes |
| Phase 3a shape ratio within [0.9, 1.1] on ≥ 95% of dollars | dollar-weighted share | market | ≥ 30 admitted markets | point; recomputed from the DB per mode | yes |
| Phase 4 live fidelity by_usd ≥ 0.5, react p50/p90 | dollar-weighted fraction; nearest-rank | book (his fills on one book are one cluster) | ≥ 30 books | clustered lower bound ≥ 0.5 for the fraction; point for p50/p90 | ≥ 30 books = 6+ days at 5/day; 1 day at 30+/day |
| M10 live rest fill rate, at_or_better, maker_share | proportion | book | ≥ 30 books | clustered lower bound ≥ 0.40; at_or_better = 1.00 exact (an invariant) | as above |
| Phase 5 short gates | as M8 (shadow) and M10 (live) | market / book | ≥ 30 | as above | ≥ 30 closed short books = 6+ days at 5/day |
| Phase 6 `game_cap` breaches, Phase 9 integrity counters, rung lines | counts | — | — | exactly 0 / the line prints | deterministic |
| M19 book cohort ci95 (D3) | proof.roi_with_ci on peak_exposure_usd | game | 30 books AND 30 games (rules:1300-1306) | lower bound > 0 | NOT inside any phase window: 31,052 games at his edge (17 years at 5 books/day) |
| rung 7 settlement ≤ $0.05 | per-book gap | — | 1 book (the rung), then every closed book | exact | deterministic |

## 4. FIDELITY METRICS — definition, source, today, target (M1-M20 from revision 1 kept; amended rows and M21-M28 added)

| # | metric | definition | source | today | target |
|---|---|---|---|---|---|
| M1 | Dollar coverage (admitted families) | Σ his GROSS dollars on markets where a live book holds ≥ 50% of target ÷ Σ over ALL his markets in the window, by family and sign | MIRRORCOVER-TOTAL | 22.3% mapped by count (99/443, terms1); admissible under the quarantine: the premap-sourced share only | ≥ 0.95 of his dollars in admitted families |
| M1-total | the same over ALL families | MIRRORCOVER-TOTAL + MIRRORFAM | derivatives are 3.1% of his mapped 24 h dollars | reported beside M1 |
| M2 | Tennis-ML listed-unmapped share | listed_on_us_but_unmapped ÷ (mapped + listed_on_us_but_unmapped) | MIRRORCOVER-CELL | now printable (job in the tree) | ≤ 20% by markets and usd |
| M3 | Admissible mapping share | mapped rows with source ∈ {premap, exact} ÷ mapped | MIRRORSRC `admissible_share` | ledger-sourced on 69.4% of readings by construction | ≥ 90% |
| M4 | Snapshot usability | fresh (≤ 300 s) COMPLETE per-market read share | MIRRORSNAP | 0 | ≥ 0.95 |
| M5 | Net drift | \|(his_long − his_other) − (snap_long − snap_other)\| / max(...), p90 | MIRROR line | 0.4531 whole-book per token | ≤ 0.05 |
| M6 | Position tracking error | per (book, tick) \|ledger_net − target_flow\| / max(target, 1), p50/p90, excl. frozen and dead-band ticks; requires shadow coverage of book ticks ≥ 0.95 | MIRRORTRACK (Phase 0c) | none | p90 ≤ 0.05, p50 ≤ 0.02 |
| M7 | Shape ratio | per book (target × mark)/(r × his_net × mark) | MIRRORSHAPE | 0.34x-52.63x at r=1.9% | [0.9, 1.1] on ≥ 95% of dollars; cap-bound ≤ 5% |
| M8 | Would-fill, non-legacy, by phase | would_fill=true ÷ resolved, clustered by market, in-play plans separately | MIRRORFILL / MIRRORPHASE | 0.4902-0.4907 all plans (terms1/2); phase split unprinted | clustered lower bound ≥ 0.50 on in-play plans over ≥ 30 markets |
| M9 | Would-P&L | Σ(payout − would_px) × qty, game-clustered | MIRRORWOULD | in the tree, unread | reported; lower bound > 0 is the shadow's economic reading |
| M10 | Live rest fill rate; queue haircut | filled_rest ÷ placed_rest; haircut = touch rate − live rate; `queue_consumed` (depth at our cent fell by ≥ Q + qty, or the ask crossed below) as the pre-live estimate | /api/admin/mirror; Phase 0c | none; depth at the touch in the tree (`_paced_depth`); joining 0.38 behind 616 sh with 35 sh = 95% of the queue ahead (tee/gap_r1_1.md §3) | ≥ 0.40; at_or_better = 1.00; maker_share ≥ 0.5 |
| M11 | Fill fidelity per HIS fill | dollar-weighted fraction of his BUY fills on booked long tokens matched by our fill inside 600 s; react p50/p90; by phase | MIRRORFIDELITY | none | by_usd ≥ 0.5; in-play react p50 ≤ 10 s, p90 ≤ 30 s |
| M12 | Stale-level plans | plans > 1c under the bid with a trigger older than 600 s | census `level_stale` | 7 of 22 BUY plans, 41.9% of their dollars | 0 |
| M13 | Short-side coverage | as revision 1 | MIRRORSIGN / MIRRORSHORT | 0 | ≥ 0.95 after Phase 5 |
| M14 | Reduce fill share | planned reductions filled within one TTL ÷ planned | MIRROREXIT (shadow), census `reduce_unfilled` (live) | 3 would-flatten rows in 51; EXITVALUE his exits worth $122,187 over holding on the graded half (probe:1939) | threshold set from MIRROREXIT (decision 18); ≥ 0.5 until then |
| M15 | Integrity | wrong_sign_trip, order_lost, overfill, reaper_touched_mirror, book_settle_disagree (clean class only), shadow_live_disagree; frozen ticks | rules:270-271 | none | all 0; frozen < 1% |
| M16 | Settlement to the cent, by class | \|own − venue\| per `clean` book; `settle_gap_rule:<class>` otherwise | MIRRORGRADE (Phase 9) | none; the venue settlement price is read by nothing today | ≤ $0.05 on clean books; every non-clean book named |
| M17 | Commission | fields present on mirror executions | Phase 7 rung 10 | keys only, never a value | 100% present |
| M18 | P&L capture | our_book_pnl ÷ (r × his_capscaled_pnl) with the Phase 9 replay | MIRRORGRADE | none | ≥ 0.5 reported, never gated |
| M19 | Book cohort interval (D3) | ci95 lower bound, game-clustered, with n_needed | MIRRORGRADE | none | > 0 (locked); 31,052 games at his edge |
| M20 | Taker share | as revision 1 | TAKERSHARE | 2/66 by count; dollars ∈ [~0, 68%] | reported, match_rate ≥ 0.9 |
| M21 | Cost-basis fidelity | per book `basis_gap = our_avg_cost − his_vwap_since` (his long-token BUYs after max(first_seen_at, opened_at)); dollar-weighted by peak_exposure_usd; p50/p90; `books_above_1c`; `basis_unreadable`; a build leg's gap separately | MIRRORBASIS (shadow: would-fill-weighted would_px per MARKET, not per plan) / MIRRORGRADE | none; the 52.9% pre-existing bound | dollar-weighted \|gap\| ≤ 1c; `books_above_1c` = 0 on the flow leg; p90 ≤ 2c; unreadable = 0 |
| M22 | Inherited-refused dollar share | Σ his net-at-mark $ on mapped markets whose FIRST named refusal is an inherited clause ÷ Σ mapped, per clause | MIRRORWHY | legacy_row 61.7%, short 4.5%, family 2.7%, floor 0.27%, side_band 1.3% of BUY-plan $ (tee/gap_r1_5.md §4.13, per_side corrected to 0 by D22) | every clause ≤ 1% except the ones decided by name (10, 12, 13) |
| M23 | Terms readability | mapped rows with tick/min_qty read | MIRRORTERMS | 98/98 on the runner; 0 readers in code | 100%; `off_tick` = 0; `min_qty` refusals named |
| M24 | In-play parity | share of his fills (count, usd) after gameStartTime with the NULL share; two-sided share of in-play mapped markets | MIRRORINPLAY | 82/82 in a one-match sample; two-sided 17/20 open moneylines (95.6% of dollars) | printed with the NULL share; two-sided ≥ 0.80 markets / ≥ 0.90 usd |
| M25 | Cross-venue basis | mid_t − his fill price at the nearest tick, by family/phase/age; share of his dollars with basis > 1c persisting > 600 s | MIRRORBASIS-X | 7 of 22 BUY plans under the bid, unsplit | printed; feeds decision 7's allowance question |
| M26 | Rule-mismatch dollar share | his dollars on mapped markets whose profiles differ, by clause | MIRRORRULES | unread texts | printed; 0 books on basis/retire mismatches |
| M27 | Cash room | buyingPower − reserved, per tick; venue funds rejections | MIRRORCASH | $31,502.13 at 21:45Z, read by no money path | 0 funds rejections over 24 h |
| M28 | Shadow coverage of live books | book_ticks_unshadowed ÷ live_book_ticks | MIRRORTRACK | none | ≤ 0.05 before shadow_live_disagree=0 counts |

## 5. OWNER DECISIONS (only he can make these; each with a recommendation and the number)

1. THE RATIO r AND THE BANKROLL IT IMPLIES — amended by the account reading. The account's buying power is
   $31,502.13 (probe:77). On the only readable denominator (MERGEPNL open $25,086,278, probe:1843, which
   INCLUDES resolved balances and so overstates him) that is r ≤ 0.126%; the 30-day open-cost query (Phase
   3a) will read smaller and make the same $31.5k a larger r. At r=0.126%: gross entries $1,718/day (the
   existing $1,250 day cap is proportional at 0.0917% — nearly right at the account's own scale); the $5
   dead band drops his markets under $3,968 and one share at p=0.5 needs his$ ≥ $397 (≤ 86.8% of his stake
   is in lots ≥ $250, probe:1686, so the dollar reach at this r is bounded by his lot sizes, decision 21). At
   r=1% ($250,863 — not fundable today): $13,637/day, dead band under $500. RECOMMENDATION: name the sleeve
   he will fund (MIRROR_BANKROLL_USD ≤ buying power minus the per-fill lane's room, decision 20); r follows
   from Phase 3a's denominator, read for 24 h in the shadow with `target_flow` beside `target` before any
   book; keep the $250 per-market cap only as the rollout handle (with it the effective ratio on his
   $6.4k-$25.8k books is 0.0097-0.039 whatever r is).
2. WHETHER THE $50 CLIP COHORT STILL GOVERNS PROMOTION (D1/D3) — unchanged: D1 stays for the per-fill sleeve;
   the mirror promotes by the BOOK cohort's interval with MIRROR_BANKROLL_USD carrying MEASURE and PROMOTED values.
3. D3's PROOF HORIZON AND ITS GAME FLOOR — unchanged horizon (31,052 games = 17 years at 5 books/day; ±46.6
   pts at 30). NEW: the mirror's gate uses proof.MIN_PROOF_CLUSTERS = 30 games (rules:1305-1306, :71) where
   locked D3 says 20 (roster_rules.py:51 MIN_CLUSTERS_PROMOTE=20, :126-127); MIN_N_PROMOTE=30 books is the
   same in both. At 5 books/day, 30 books = 6 days and 30 games adds 0 days while each book is its own game
   (one book per market, one market per game in P1); it binds only when books share a game (Phase 6's three
   per game). RECOMMENDATION: 30 governs the mirror (it is D6's own 95% floor, proof.py:87) — say so under §4
   rather than present it as D3.
4. THREE-WAY SOCCER — unchanged in shape (per-condition books under one game cap), SEQUENCED AFTER decision 10.
5. SHORTS SEQUENCING AND THE 14 LEGACY BUY_SHORT ROWS — unchanged.
6. THE SWITCH-ON DAY (legacy per-fill rows) — unchanged: admit a slug only once its legacy row is cashed_out/settled.
7. QUEUE PRIORITY — unchanged answer (no p+1c by default; 2% of stake on a 50c contract against +2.07%).
   NEW sub-item: after rung 12, choose keep-until-expiry under GTD and modify-in-place if priority survives a
   modify — the one lever inside our control that nobody measured; every 600-s replace today resets FIFO.
8. RN1'S POSITIONS WALK — unchanged (raise WHALE_EXIT_POS_MAX; Phase 1's per-market read is the source) plus
   the read carries avgPrice for decision 13(C).
9. THE RESIDUALS HE MUST ACCEPT (§6).
10. THE 2026-08-12 SOCCER PRICE FLOOR ON MIRROR BOOKS. It refuses every YES-on-draw and dog book by
    construction (12 distinct -draw slugs across the probes; `copy_verdict('rn1','…-draw',0.28)` → floor),
    costs ~1% of his soccer dollars at his level in the shadow (1 of 13 readings, $389) and 47-65% of his
    non-exit soccer FILLS by count (110-170 per boot). Options: (a) inherit; (b) off for lane='mirror' (the
    cap and one-book-per-market are the protections); (c) open-only at the book's entry level, lifted for
    -draw in Phase 6. RECOMMENDATION: (c) for P1; (b) decided with Phase 6's `floor_refused_usd` numbers.
11. (no decision now) the $2 underdog sleeve is OFF by his 2026-08-24 order and blocked by a side defect;
    `underdog_coholds` stays; revisit only if the sleeve is re-armed (D23).
12. THE MAPPING SOURCE THE QUARANTINE ADMITS — engineering-owned, owner-visible: with the quarantine on, a
    ledger-sourced map (69.4% of readings, 97.9% of mapped $ — the same rows `legacy_row` refuses next) is
    refused at admission; only Phase 2's mirror-owned exact lane (the same `resolve_market_exact` the copy lane
    labels 'exact') admits. RECOMMENDATION: (a) Phase 2; option (b) is unbuildable (D24).
13. THE SWITCH-ON STATE AND LATE ENTRY (new). (A) follow only his flow from first sight (Rule LE, pro-rata
    ratchet); (B) build r × his_net into his open book at his newest level (today's code); (C) follow flow and
    rest a build leg only at ≤ his venue basis (avgPrice, Phase 1). Numbers: ≥ 52.9% of his long dollars on
    the window's 17 marked long-net slugs existed at first sight (a BOUND); on the window's rows P1 admits 0
    of the 16 pre-built slugs anyway (D27), so the dollar effect is a Phase 3 (bankroll-r) question; under (A)
    the pre-existing block is never bought ($59,986 × r on the sample: $76 at 0.126%, $600 at 1%).
    RECOMMENDATION: (A) now (two numbers and a ratchet, no venue read); (C) after Phase 1 reads avgPrice
    semantics; (B) never.
14. TRANSPORT (T1, new). Run the WS probe (rung 13) through an admin endpoint on the service that holds the
    key. ADOPT WS for market data + private orders if: N=60 coverage over markets whose REST bbo moved,
    depth ≥ 2 levels/side p50, a public trade tape with the aggressor side, CONNCAP k ≥ 2 clean, REST 429s
    = 0 while sockets are open, private fill push < the 30-s poll. Then the book ceiling = min(WS cap,
    blast radius) instead of the REST slot cap, the P0 judge gains fill-through, M10's haircut is measured
    pre-live. STAY REST otherwise: then a KEY-WIDE rate bucket (premap, the positions walk, the web service
    and the mirror on one key; two 0.35-s loops = 5.7 req/s = the 2026-08-23 incident) is the precondition
    for any raise of MIRROR_MAX_MARKETS, and the honest ceiling is ≤ 60 books at 1 read/book/tick.
15. THE BLAST-RADIUS CONSTANTS AT r (new) — sign each row of Phase 3b's table; in particular the loss stop:
    $250 at $13,637/day is 1.8% of a day's gross and, at our copies' daily ROI sd of 0.28 of stake (15 days
    ≥ $1k), trips on ~47% of days; RECOMMENDATION: stop = 2 × sigma_day ($1,000 at r=0.126% with 20
    books/day; $4,584 at r=1% with 60), reported beside PMUS_LOSS_BREAKER_USD=$5,000.
16. IN-PLAY ADMITTED (new). 100% of his mapped flow is in-play (98/98 markets started; 82/82 joinable
    fills; ≤ 3% pre-game); a `game_started` refusal would refuse every dollar (D13). RECOMMENDATION: admit
    in-play tennis and MLB moneylines (two-sided 6/7 and 6/8 at the two ticks, 100%/91.4% of his OPEN
    dollars), soccer moneylines under decision 10, refuse totals by family; halts refused by name; the
    in-play cadence requirement (react p50 ≤ 10 s, p90 ≤ 30 s; replaces ≥ the measured down-move rate) is
    the Phase 4 gate.
17. RESOLUTION-RULE MISMATCH POLICY (new). (a) refuse every mismatch clause — costs every ITF book
    (0.1% of shadow dollars, 29% of his fills) and every cup-soccer book; (b) refuse `basis`/`retire`
    mismatches (a sign can flip), flag `walkover/cancel/window` (bounded by |0.50 − p_last| × shares ≤ the
    cap). RECOMMENDATION: (b), after Phase 0b stores the texts and Phase 10's classifier reads ≤ 10% unreadable.
18. THE EXIT LEG (new). For RN1 every exit is a `flatten_paired` rest at max(1−q, ask) (rules:881-882);
    the slippage path is unreachable (64 sells ever; fills never see merges); his exits are worth $122,187
    over holding on the graded half (EXITVALUE, coverage 0.517). Policies: hold to resolution vs a bounded
    take at bid−1c/−2c after N TTLs. RECOMMENDATION: hold (no slippage) until MIRROREXIT prints the SELL-side
    touch rate and time-to-touch by family and the 30-day replay prices both policies on his exits with a
    game-clustered interval; set M14's threshold from that reading; the take is an owner line with dollars.
19. DERIVATIVES (new). Totals/spreads/segment props are 3.1% of his mapped 24 h dollars (1.5% at the mark
    in the shadow sample); his MLB moneyline carries 93% of his MLB dollars. RECOMMENDATION: exclude from
    P1-P3; Phase 11 per family behind its own 7-day shadow gate; report M1-total beside M1.
20. CASH ROOM (new). The mirror, the per-fill lane and the desk place into one account (buying power
    $31,502.13); no money path reads it. RECOMMENDATION: MIRROR_BANKROLL_USD ≤ buyingPower − the per-fill
    lane's reservation, read per tick, `insufficient_cash` by name; decide the split between lanes.
21. MIN_MOVE_USD AT THE FUNDABLE r (new). $5 drops his markets under $5/r ($3,968 at 0.126%, $500 at 1%);
    the venue's own floor is 0.01 share and no minimum notional was seen (D16). RECOMMENDATION: keep $5 for
    P1 (each order is a venue write against a 6-ops tick and a 12/h replace budget) and print dead_band
    dollars per r; lower to $1 only with the key-wide bucket of decision 14.
22. LADDERS (new). One resting order per book (047:113-114) against his ladders: 68.5% of his dollars on
    the one read book arrive in same-second ascending-cent clusters; 46 of 66 fills are followed by a
    different cent within 600 s. A k-rest book costs k × (cancel+create) inside 6 ops/tick and 12/h.
    RECOMMENDATION: one rest per book in P1; decide k after MIRRORLADDER prints the 30-day dollar share of
    ≥ 2-cent clusters; until then M11 charges the miss to queue and it is a §6 residual.

## 6. WHAT "TO A TEE" CANNOT MEAN — the honest residuals, with numbers

| residual | number | source |
|---|---|---|
| Queue position | our rest queues behind PMUS depth at his cent (top-of-book 20-616 sh vs plans of 35-2,272 sh; joining 0.38 behind 616 sh with 35 sh leaves 95% of the queue ahead); every TTL replace resets FIFO until rung 12 | probe:1856-1874; tee/gap_r1_1.md §3; rules:731-734 |
| Ladders | one rest per book vs 68.5% of his dollars in same-second ascending-cent clusters; 46/66 fills followed by a different cent inside 600 s | tee/timing.refute.market.md:10-16; tee/gap_r1_4.md §3 |
| In-play cadence | 21.6 clusters/h and 15.3 cent-changes/h on one book vs 12 replaces/h and 6 ops/tick; a rest ≥ 30 s old is off his level for 20% of fills | tee/gap_r1_4.md §3; rules:222-223 |
| Venue listing gaps | whatever MIRRORCOVER books as `not_listed_on_us`; today 77.7% unmapped by count with the split now printable | terms1 |
| His pair-capture economics | 42.8% of his shares since 08-01 are merged pair legs; his +2.07% is a closed-lot blend the long-only net mirror cannot earn | probe:1843; tee/refute_market_arith.out:1 |
| Late entry | the pre-existing block at first sight (≥ 52.9% of his long dollars on the window's candidates, a bound) is never bought under (A); his standing 10k+ book outside the 6 h window is never a candidate | tee/gap_r1_6_probe.out; wx:675-677 |
| Latency | chain lane send_p50 1.66 s / p90 9.17 s; poll lane ~281 s for ~15% of his fills; the mirror adds ≤ ~10 s of paced reads with a wake | probe:1703; probe:1548 |
| Maker adverse selection | a rest fills when the ask comes DOWN to his level; PATHCURVE +0.62c at 30 s [−0.40, +1.65], every offset contains zero | probe:1652-1657 |
| Cross-venue basis | 7 of 22 BUY plans rest 0.5c-16c under the PMUS bid (41.9% of their dollars); how much is basis rather than staleness is Phase 0c's number | tee/refute_market_rest.out |
| The tick | until rung 11 reads a half-cent back, 42.9% of his mapped markets (49.3% of his open dollars) are priced on a grid the wire may not carry | terms1/2 |
| Fees | feeCoefficient 0.06 on every market, the dollar formula unread, commission values never observed | terms1; probe:2043-2044 |
| Void-class settlement | he is paid 0.50 where we are paid the last mark (ITF walkover/cancel, no-make-up games, out-of-window matches); bounded by |0.50 − p_last| × shares ≤ the cap per book; frequency unmeasured because splits are invisible in `markets` (gamma.py:59-61) | tee/gap_r1_3.md §1, §0.1 |
| Dead band | $5 = 2% of a $250 book; at r=0.126% it drops his markets under $3,968 | mi:64-66; §A |
| Reductions may not fill | reduce_unfilled has no bound but the cap; the vanish path is unreachable for RN1 | rules:878-882; probe:1988 |
| Post-only semantics | cannot be read back (SDK Order has no participateDontInitiate); behaviour only | polymarket_us/types/orders.py:70-92 |
| Tick throughput | 20 slots per tick, ~11 to 0-read markets; a key-wide rate bucket does not exist; the 429 figure is unsourced | probe9:1673; ms:59, :650; tee/gap_r1_1.refute.engineering.md F2-F3 |
| Transport | WS depth/tape/private streams unprobed | tee/gap_r1_1.md |
| His taker share by dollars | unknown, [~0, 68%] until Phase 8 | tee/timing.refute.market.md:10-16 |
| Sign churn on a two-sided quoter | 4 of 19 twice-read slugs flipped sign inside an hour; two swung > 10x | tee/refute_market_rest.out:15-22 |
| Sizing under the $250 cap | until Phase 3 the effective ratio on his big books is 0.0097-0.039 whatever r is | tee/verification_refute_market.out:29-31 |
| Bankroll | the account funds r ≈ 0.126% on the overstated denominator; "every dollar" at r=1% needs $250,863 | probe:77; probe:1843 |
| P&L proof | ±46.6 ROI points at 30 games; 31,052 games for +2.07% | §A |
| Rule text | neither venue's rule text is stored today; the parity table is snippets | tee/gap_r1_3.md |

## A. Arithmetic probes run in this session (PYTHONPATH=backend; pure modules and probe-log parsing)

```
ratio clamp 50/25.55 = 1.957 -> 1.0 ; weighted 50/2635.2 = 0.018974
MIRROR_DAY_USD 1250 / (44881024.17/32.9 per day) = 0.0917 %
buying_power 31502.13 (probe:77) / MERGEPNL open 25086278.4 = 0.1256 %  -> gross entries/day at that r = 1718 ; MIN_MOVE 5/r = 3968 ; one share at p=0.5 needs his$ >= 397
r=1.0%: bankroll on MERGEPNL open = 250,863 ; gross entries/day 13,642 ; MIN_MOVE drops his$ < 500
r=2.0%: 501,726 ; 27,283/day ; < 250
COPYD RN1 daily series (last-per-day over every probe): 23 days; 15 days with stake >= 1000: mean roi 0.2505, sd 0.2822, min -0.212
  stop 250 at 13,642/day = 0.0183 of stake -> P(day loss > stop) ~ 0.47 ; stop 250 at 1,250/day = 0.20 -> ~0.24
sigma_day = 1.302 x day_gross / sqrt(books/day): 1.302 x 1718 / sqrt(20) = 500 ; 1.302 x 13637 / sqrt(60) = 2292
derivative dollar share (terms2): 25357.54 / 819395.19 = 0.0309
D3 floors at 5 books/day: 30 books = 6.0 days ; 20 games = 4.0 days ; games <= books so the 30-game floor adds 0 days on distinct games
required_n edge 0.0207 = 31052 ; half-width at 30 games = 0.466 ; 17.0 years at 5 books/day
binomial 1265/2578 ci: [0.4714, 0.51]
target_shares(1.0, 39295, 0.9875, cap 250) = 253 capped ; target_shares(0.01, ...) = 253 capped
```
