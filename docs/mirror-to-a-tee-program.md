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
`_paced_bbo_state` returning (bid, ask, state, err); `_paced_bbo` untouched. SUPERSEDED 2026-09-06 (U9,
after the 2026-09-05 venue-wide halt read as `no_quote` for five hours): `_paced_bbo` now returns
`pmus.bbo_read`'s dict `{bid, ask, state, error}`, both of its callers (mirror_shadow.shadow_market,
mirror_live._bbo) were adapted in the same change, the `pace(READ_PACING_S)` source pin holds, and no
`_paced_bbo_state` sibling is built.

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
`_paced_bbo` keeps its 2-tuple). AMENDED 2026-09-06 (U9): the `_paced_bbo_state` / `_bbo_state` sibling
plan above is superseded -- `_paced_bbo` returns `pmus.bbo_read`'s dict (`{bid, ask, state, error}`),
both callers were adapted with it, the `pace(READ_PACING_S)` pin holds, and this phase reads the BBO
call's `state` off that dict rather than building a sibling.

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
| MIRROR_DAY_USD (gross buys/day) | UNBOUNDED since U12b (2026-09-06; was $1,250). Env may only lower it (`unbounded_env`); the sleeve's daily room no longer binds the mirror either | no day cap: the owner's order, not r's | same | worst case bounded by the loss stop and the venue balance |
| MIRROR_MAX_BOOKS_PER_DAY | UNBOUNDED by default since U12 (2026-09-06; was 5). Env may only lower it (`unbounded_env`) | not a count | same | the candidate walk keeps its own 20-read budget; the book walk is unbounded and `tick_s` on the stats is what stretches |
| MIRROR_MAX_LIVE_BOOKS | UNBOUNDED by default since U12 (was 5); env may only lower it (rung S3's probe runs at 1) | same | same | same |
| MIRROR_NET_CAP_USD / mi.MARKET_NET_CAP_USD (per event = one book = one side of one game; at the mark on a long, in collateral on a short; across every fill) | $2,500 since U12b (2026-09-06; was $250) | unchanged: the owner's number, not r's | unchanged | scales, never declines; the shadow sizes from the same constant |
| MIRROR_RATIO / MIRROR_SMALL_BET_USD (decided at open, stored on the book) | 10% above $10 of his dollars at the mark, exact copy under it (U12b/U12c) | the owner's number; the bankroll ratio stays a diagnostic | same | env may only lower either |
| MIRROR_CLIP_USD per order (the mirror lane's own; LIVE_MAX_CLIP_USD $250 stays the copy lane's) | $2,500 since U12b | a $2,500 target is one rest | same | `per_fill_usd` stays the admission gate, never the size |
| MIRROR_LOSS_STOP_USD (24 h realized) | $250 (rules:217) | stop = k × sigma_day, sigma_day ≈ sigma_per_dollar × day_gross / sqrt(books_day): at 1.302 × $1,718 / sqrt(20) = $500 → a 2-sigma stop is $1,000 | 1.302 × $13,637 / sqrt(60) = $2,292 → 2-sigma $4,584 | a $250 stop at $13,637/day is 1.8% of a day's gross; with daily ROI sd 0.28 of stake it trips on ~47% of days (§A) |
| PMUS_LOSS_BREAKER_USD (global, terminal rows) | $5,000 (le:2674-2675) | unchanged | unchanged | shared with the per-fill sleeve; the mirror's stop sits under it |
| MIRROR_MAX_ORDER_OPS_PER_TICK | 6 (rules:222) | 6 until the key-wide bucket; then books × 2 per simultaneous kickoff | same | 5 books cancel+place at one kickoff = 10 ops (tee/lifecycle.refute.engineering.md missed 2) |
| MIRROR_MAX_REPLACES_PER_HOUR | 12 (rules:223) | the measured DOWN-move rate on in-play books (28 of 46 600-s moves are down on the one read book; 15.3 cent-changes/h) → 16-20 by code, after MIRRORLADDER prints | same | D20 |
| MIN_MOVE_USD | $0 since U12c (2026-09-06; was $5): one whole share is the only floor, small bets copy whole | — | — | decision 21 closed by the owner's order |

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

### 5a. DECISIONS TAKEN 2026-09-05 (asked as multiple choice, the program's numbers beside each option)

- SHORTS (decision 5): "longs first, shorts follow" — long books go live the moment the venue resumes
  trading; the short side (P2: BUY_SHORT intent, short books, short exits) is built and reviewed now and
  joins at the same rails once it passes. The owner's words: "we also need to make sure that we are
  mirroring shorts".
- LOSS STOP (decision 15): $1,000 at the full rails (was $250; MIRROR_LOSS_STOP_USD's code default).
  AMENDED 2026-09-06 ~22:30Z: $5,000. The $1,000 stop tripped at 20:47:34Z on a genuine night (his
  Juventus/Milan draw short and Espanyol/Sevilla under 1.5 settled against him; our 10% copies
  -$1,214 and -$975; the 24 h sum -$1,553 at the trip, -$2,445 by 22:22Z after the Cruzeiro short
  settled). Told that a re-arm alone re-trips on the first tick because the stop re-reads the
  trailing 24 h every tick, and asked "$3,000 / $5,000 / keep $1,000" with the figures beside each,
  Matt: "Re arm" then "$5,000" -- about half of that day's $10,666 peak stake at 10% of his book.
  Raised in code (env can only lower it), re-armed by mirror-rearm once the build was live.
  E3 (day reconciliation, 2026-09-06 23:10Z): the 24 h figure the stop reads (`_SQL_LOSS_SUM`) DOUBLE-COUNTED
  a settled book's sales. It summed realized_pnl over every book updated in 24 h PLUS settled_pnl over every
  book closed-settled in 24 h, but settled_pnl is the venue's whole-position figure (the one `_close_settled`
  checks `own = realized + shares × (payout − avg)` against under `book_settle_disagree`), so it already holds
  the realized part: book 16 (realized −244.75, settled −315.40 = sales 349.25 − cost 664.65 + 157 × 0),
  3 (+2.48 / +156.17), 19 (−2.11 / −41.46) and 22 (+14.64 / +19.74) put the 22:22Z reading at −2,445 where
  the truth was ≈ −2,215 — it failed closed (too pessimistic), but the number the owner was told was wrong.
  THE RULE NOW, a dollar counts once: lost = Σ settled_pnl over books with state 'closed' AND settled_pnl not
  null AND closed_at in 24 h (updated_at where a hand-edited row has no closed_at: every close the worker
  writes stamps both), PLUS Σ realized_pnl over every OTHER book updated in 24 h (open / frozen /
  closing books, and closes that were cashed out or cancelled with settled_pnl NULL, whose P&L lives only in
  realized_pnl); a closed-settled book counts by its settled figure or not at all. The `books` count, the
  $5,000 stop and the stop write are unchanged. The render-ops `mirror-pnl` preset's 'today' row prints
  `day_pnl` by the same rule beside the raw realized and settled sums, so the hourly status quotes one true
  number; the statement executes against real Postgres in tests/test_mirror_loss_sum_real_pg.
- THE SLEEVE (decisions 1 and 20): $10,000 of the account's $31,502 buying power is the mirror's pot.
  Today the per-market ($250) and per-day ($1,250) caps size every order and the pot is not binding; it
  becomes the ratio's denominator when Phase 3a reads his deployed capital (`why_bankroll` is still
  `deployed_unreadable` in the mirror_ratio row).
- FAMILIES (decisions 16 and 19): "everything he trades" — every sports family the slug grammar names
  (moneyline, spread, total, prop, btts, exact_score) is admitted; crypto and unknown never are. The
  per-side referee, the closed/resolved reads, the mapping quarantine and the side band still stand in
  front of every family; decision 17's mismatch policy and 19's per-family fill behaviour are accepted
  residuals.
- RAMP: straight to the full size (5 books, $250 per market, $1,250 per day, the $1,000 stop) as soon
  as the venue accepts the first order at the first rung (1 book, $25, $50).
  DONE 2026-09-06. The venue reopened at ~00:40Z after a venue-wide halt (~17:38Z-00:40Z, every bbo read
  MARKET_STATE_HALTED). The first order was accepted on the first ON tick: workers' log 00:40:38.048Z
  "mirror book 1 opened for rn1 on aec-wta-yulsta-eleryb-2026-09-05: standing row 411040 (episode 1,
  target 188 @ 0.12, ratio 1.0)", 00:40:43.552Z `POST api.polymarket.us/v1/orders "HTTP/1.1 200 OK"`,
  mirror_orders open=1 at 00:42:43Z. The three rung keys (MIRROR_MAX_LIVE_BOOKS, MIRROR_NET_CAP_USD,
  MIRROR_DAY_USD) were deleted from sportsassets-workers at 00:44:14-18Z (render-ops env-del, HTTP 204
  each), so the code defaults now ride: 5 books, $250 per market, $1,250 per day, $1,000 stop.
- OWNER DECISION, 2026-09-06 19:2xZ -- the Paul/Alcaraz short is HIS. The venue held a 4,817-share
  short on aec-atp-tompau-caralc-2026-09-06 that no mirror book placed (census `venue_already_holds`
  1; the shadow row `frozen: venue and ledger disagree`, venue -4817 / ledger 0), so the mirror
  refused every candidate read of his $49k on that market. Asked "Tell me to flatten it or leave it",
  Matt: "Leave it, I manually placed it". So: the position is the owner's own, the mirror keeps
  refusing that slug by rule (a position the book cannot explain is never traded against or
  flattened), nothing is placed or cancelled on it by any lane, and the refusal is expected on
  every tick until the market settles. Recorded here so the next reader does not file the
  refusal as a defect.
- SIGN-FLIP REOPEN, 2026-09-06 (owner 19:33Z "I need more trades firing in the mirror sleeve! We
  need to be mirroring a larger percentage of RN1s positions"). Book 27 (aec-atp-medvedev-tiafoe):
  he opened short, our short book followed, then he flipped to +$18.7k long; our book flattened to
  0 under `sign_flip` and then sat FLAT behind the 3600 s flat close (MIRROR_FLAT_CLOSE_S), and the
  one-open-per-market index kept the long book from opening for that hour. Q5 (a) had said "the
  flat wait is not shortened". Amended: in `rules.episode_close_reason`, `sign_flipped=True` on a
  FLAT book with ZERO open orders is a close clause beside "market closed" and "vanish confirmed"
  -- the episode closes on that tick (`cashed_out` when it ever bought, else `cancelled`) and the
  opposite side opens as episode 2 on the next tick under the ordinary admission. The worker
  hands the flip to the close only on a tick whose OWN venue reading of the slug is 0
  (`_maybe_close_episode(venue_flat=...)`): a book flattened by a fill booked inside the tick
  (close_position, an IOC take) waits one more tick for the venue's read-back, so a venue
  residual after a cover is frozen `venue_ledger_disagree` on the live book instead of orphaned
  behind a closed one (review finding M-1). The flat wait
  exists so a book he may re-buy is not closed and reopened for nothing; a flip is the opposite
  reading -- `sign_flip()` needs a NONZERO whole target of the other sign at the ratio, so his net
  is materially the other way and the next episode is the other side, never this one. While a
  flatten rest is still open the reading stays `orders_open`; while shares are held it stays
  `sign_flip`; nothing else about the close moved. Pinned in test_mirror_short_sign_flip (both
  directions, end to end) and test_mirror_live_rules.
- U12 / U12b / U12c, 2026-09-06 -- THREE OWNER ORDERS, verbatim, and the rails they leave.
  13:36Z: "I don't want to cap books opened at all. I want max trade on one side of an event to be $1000
  between all fills." ~14:00Z: "Let's remove those caps so we start copying his actual book. Just trade
  10% of what he puts on everything he takes (with a hard cap of no single event having more than $2.5k
  on it) this limitation should never force us to decline any of the possible copies." ~14:10Z: "Bets
  under $10, take the full position (exact copy)" and "I want shorts live as well". The later orders
  supersede the earlier where they overlap (the $1,000 per side became $2,500 per event; the saved
  day-cap question, task 25, is answered: no day cap).
  THE RAILS NOW. (1) RATIO: 10% of what he puts on (`rules.MIRROR_RATIO`, env may only lower), EXACT
  COPY (ratio 1.0) when his dollars at the mark are under $10 (`MIRROR_SMALL_BET_USD`, env may lower;
  his dollars are |net| x mark on a long and |net| x (1 - mark) -- collateral -- on a short). The ratio
  is DECIDED WHEN THE BOOK OPENS (`rules.open_ratio`) and STORED on the book (`mirror_books.ratio`); an
  open book sizes on its stored ratio for its whole life and is never re-targeted at another -- a
  position that crosses $10 must not flip between 100% and 10% every tick -- so the two books open
  today keep the 1.0 they opened at. `ms.refresh_ratios` keeps running and its anchor/bankroll readings
  stay reported as diagnostics; the shadow keeps sizing from its own readings (it measures). (2) PER
  EVENT: $2,500 (`mi.MARKET_NET_CAP_USD` 250 -> 2500) on the book's net at the mark (long) or in
  collateral, (1 - mark) x shares (short), across every fill; one mirror book is one side of one game
  (the one-per-game claim keys on game_key), so "a single event" is the book. The cap SCALES the target
  (his 100,000 sh @ 0.60 at 10% is 10,000 raw -> 4,166 sh = $2,500) and NEVER declines a copy: every
  refusal `mirror_target` can name is about a cap at or under zero, an unreadable input or the short
  door, never a capped target (pinned). The shadow shares the constant. (3) NO COUNT CAPS:
  `MIRROR_MAX_LIVE_BOOKS` and `MIRROR_MAX_BOOKS_PER_DAY` default to UNBOUNDED (`math.inf`,
  `rules.unbounded_env`); the environment may still LOWER them (rung S3's 1-share probe runs with
  `MIRROR_MAX_LIVE_BOOKS=1`), `rules.admission` bites `max_books` only on a finite cap, and an
  unreadable book COUNT still refuses, fail closed, under its own name `books_unreadable` (appended
  last to CENSUS_KEYS, served on `integ`). (4) NO DAY CAP: `MIRROR_DAY_USD` defaults to UNBOUNDED (env
  may lower; a finite cap bites `mirror_day_cap` on what filled, an unreadable spend read bites it
  whatever the cap); the published `mirror_day_room` is null and the mode line prints `day=none`. The
  copy sleeve's DAILY room (`live_max_daily_usd`, $11,000) no longer binds the mirror -- that was a day
  cap by another road -- so the copy lane's daily cap and the mirror's are now separate: the copy lane
  is off, and if it is turned back on the two lanes no longer share a day budget (addendum section 7's
  concurrent-placement guard is then per lane, the rest lane's reservations coming off the sleeve's
  TOTAL room, which still binds). (5) THE MIRROR'S OWN CLIP: `MIRROR_CLIP_USD` $2,500 per order (env
  may lower, floor $1) sizes every rest; the copy lane's `LIVE_MAX_CLIP_USD` ($250) and per-whale
  `per_fill_usd` no longer size the mirror -- a $2,500 target is one rest, not ten -- and the copy
  lane's constants are untouched; `per_fill_usd` stays the ADMISSION gate (a whale demoted to $0 opens
  no book, `clip_zero`). (6) DEAD BAND $0 (`mi.MIN_MOVE_USD` 5 -> 0): the only floor is one whole
  share; `MIN_MOVE_FRAC` (2% hysteresis on adjustments) unchanged. (7) SHORTS ON: `MIRROR_SHORTS`
  defaults to True (`MIRROR_SHORTS=off` still turns it off), `MIRROR_SHORT_MAX_SHARES` defaults to
  UNBOUNDED (env may lower; 0 refuses every short by `short_share_cap`), and a short sizes by the same
  rule as a long. THE PRE-S4 EXIT RULE, STATED PLAINLY: a short exits WHOLE when he leaves the side
  (close_position when sole holder, the only proven short exit); his PARTIAL short reductions are HELD
  and counted (`short_reduce_unproven`) until a 1-share resting SELL_SHORT has been read back at rung
  S4, which is still to run and needs a short book to exist; no SELL_SHORT rest is enabled by this
  change. `le.short_model_confirmed()` is True by construction and SH1 (the pmus preview bound in
  collateral space) is in HEAD. (8) UNCHANGED: the $1,000 loss stop (reduce-only until re-armed by
  hand), `MIRROR_MAX_ORDER_OPS_PER_TICK` 6 (the `ops_capped` census name already counts every op the
  budget refused, so the throttle is visible), `MAX_MARKETS_PER_TICK` 20 -- which is no longer a book
  cap by another road: the candidate walk has its OWN quote budget (`t.cand_reads`, `capped_tick` as
  before) and per-market read budget (`t.cand_mkt_reads`, `snap_market_capped`); every live book is
  read every tick, unbounded -- exits must be managed -- and the stat `tick_s` (wall time, 1 dp) is
  what an operator watches as books grow (the 0.35 s pacer bounds the venue rate, not the tick).
  THE WORST CASE, said plainly: with no count cap and no day cap it is bounded by the loss stop and the
  venue balance, and per event by the $2,500 cap -- not by a count of books and not by a day figure.
  REVIEW OF U12c (same day), three fixes. (a) THE ONE-WAY STEP: the exact-copy ratio is decided from
  ONE read at open, so a book opened on the first $8 fill of a larger burst would follow him at 100% up
  to the $2,500 cap. When a book's stored ratio is 1.0 and his dollars at the mark (collateral on a
  short) EXCEED twice the small-bet line -- $20, derived from `MIRROR_SMALL_BET_USD`, never a second
  knob -- the row's ratio is written to `MIRROR_RATIO` for the book's life (`rules.step_ratio`,
  census `ratio_stepped`, served on `integ`); the target drops from 100% to 10% and the normal reduce
  path sells the excess (opened at $8 with 16 sh held, his position now 60 sh @ 0.50 = $30: ratio
  0.10, target 6, a SELL_LONG of 10). Never up; a 1.0 book at $15 does not step; a 0.10 book never
  steps; a failed write steps nothing. (b) THE SMALLEST ORDER: `MIRROR_MIN_ORDER_USD` $1 (env may
  lower to 0). His positions under $1 at the mark are not copied: with no dead band and exact copies,
  his 1 sh @ 0.05 became a $0.05 BUY on the wire, the venue's minimum notional is unknown, and a refused
  order would burn one of the six ops per tick for as long as the book stood. An order whose notional
  (wire x qty on a long, collateral on a short) is under the line is not sent, named
  `under_min_notional`, the book held before any read or op is spent. CONSEQUENCE FOR THE PROBES: one
  share of a 0.32 contract is $0.68 of collateral, so the 1-share probe of rung S3/S4 runs with
  `MIRROR_MIN_ORDER_USD=0` beside `MIRROR_SHORT_MAX_SHARES=1` (a long probe under $1 the same). A
  FLATTEN IS EXEMPT from the minimum (re-verification, FIX-2b): a position that is leaving leaves at
  any size -- the flatten kinds, the sign-flip flatten and any plan toward target 0 -- because a
  refused flatten rest never wrote the order row `_flatten_vanished` keys its rest clock on, so the rest
  was re-attempted and re-refused every tick and close_position was never reached. (c) THE SHADOW CHECK
  compares across ratios ON RAW ARITHMETIC (FIX-3, re-verified as FIX-3b): the shadow's `target` is
  capped at $2,500 at ITS ratio and truncated to whole shares, so scaling that output falsely named
  ordinary books (his 24,000 sh @ 0.50: shadow 5,000 at 1.0 against the uncapped 0.10 book at 2,400; a
  0.058 shadow truncated to 0 against an exact-copy book at 16) -- and `shadow_live_disagree` is a P2
  integrity counter, any non-zero failing the verdict. Now the shadow's raw is reconstructed from the
  row (ratio x net when the row is capped or agrees with its stated raw; the stated raw only when it
  diverges, which is the divergence to name), scaled by book.ratio / shadow.ratio, then the SAME cap
  the live book applies (at the mark, in collateral on a short), the same short door and whole-share
  truncation, and only then compared with a one-share tolerance. A row without the fields (`target_raw`,
  `capped`, a positive ratio, a mark on the ladder) is `shadow_check_skipped` by name, never a disagree;
  a different net is not compared, as before. Both false-positive cases are pinned as agreeing and the
  true divergence (a stated raw of 3,000 against 1.0 x 2,400, live 240) still fires.
- E1, 2026-09-06 -- THE $2,500 CAP IS PER GAME, ACROSS EVERY MARKET OF THE GAME. Two owner orders,
  verbatim: ~14:00Z "Just trade 10% of what he puts on everything he takes (with a hard cap of no single
  event having more than $2.5k on it) this limitation should never force us to decline any of the
  possible copies"; 22:3xZ "I just want to make sure the per game cap is at 2500 per game (never more)".
  WHAT THE CODE DID: `mi.MARKET_NET_CAP_USD` was applied PER BOOK (`mi.target_shares`, cap_usd / px at
  the mark), and each market of a game -- the moneyline per team, the draw, every total line, btts, the
  spreads -- is its own book, so a game with six markets could carry $15,000. On the night of the order
  (mirror-pnl 22:22Z) Espanyol/Sevilla held ~$2,347 across five books (26, 43, 46, 48, 49) and
  Juventus/Milan ~$1,714 across 30/32/35/37: under $2,500 by luck, not by rule. THE RULE NOW
  (`rules.book_exposure`, `game_room`, `game_capped`; the worker's `_game_exposure` and the walk):
  (1) a book's EXPOSURE is dollars at risk at cost -- ledger x avg_cost on a long, |ledger| x (1 -
  avg_cost), the collateral, on a short (mirror-pnl's `open_cost`) -- plus the unfilled notional of its
  resting BUY-side increase; avg_cost NULL on a held book reads the mark (more counted, never less), and
  a figure nobody can read is no room -- that includes a book with a NON-TERMINAL order the tick could
  not read (a rest whose cancel did not land, 'unknown'; a 'placing' row with no id; a lost placement):
  it may still stand on the venue and fill, so it is None, the game's room is 0 for every sibling and
  the book's own sized figure is None (the adversarial review's repro: a 3,600 @ 0.49 rest past the TTL
  whose cancel failed read as $0 and the sibling took the whole $2,500 -- $3,724 at cost on one game).
  (2) GAME EXPOSURE is the sum over every non-closed book of the same `mirror_books.game_key` this
  tick, WHOEVER THE WHALE: the order is "no single EVENT", so two whales' books of one game share the
  one $2,500 (the first cut keyed on (whale, game_key) and let them hold $5,000). (3) A book's ROOM is
  $2,500 less the OTHER books' exposure, clamped to [0, 2500], and it is applied AT COST: the cap handed
  to `mi.target_shares` for the room reading is the shares the book already holds valued at the MARK
  plus what the room leaves after the book's own held cost, so the increase costs at most `room -
  held_cost` and a mark that has fallen under the cost never lets a book average down past the game's
  $2,500 at cost (the re-review's X1: A 3,600 @ 0.49, B 1,400 @ 0.50, the mark at 0.30 -- B's room is
  $736 and its increase $36, 120 sh, where the first cut sized 736 / 0.30 = 2,453 in total, bought 1,053
  and put the game at $2,769). The PER-MARKET cap keeps its at-the-mark reading (cap / mark shares in
  total, `mi.target_shares` as before), unchanged. The increase is the same `mi.target_shares` scaling,
  so it is SCALED and never refused (`game_cap_scaled`); with no room -- or with room whose target is
  under what the book holds -- the target is what the book already holds: an increase of 0,
  `game_cap_full` (or `game_unreadable`, below), and NEVER a reduce: the cap is not a reason to sell,
  and reductions and flattens follow his book exactly as before. (4) The walk: GAMES in order of the
  oldest `updated_at` among their books -- `_write_plan`
  bumps it, so a tick abandoned at book k resumes next tick with the games it did not reach first, the
  round-robin the books had before E1 -- and WITHIN a game book id ascending, ONE fixed order (a woken
  market brings its whole game forward, the games and each game's books still in that order); a book
  sized this tick counts what it holds at cost plus the larger of its resting increase and its new
  target's increase at the mark against the later books of its game. That sized figure is read BEFORE
  the sign flip, so a book about to flip to a flatten counts its pre-flip target's increase for one
  tick: more counted than will stand, never less, and left so. (5) A book with no game_key is its own
  game (the per-market cap as before) -- a KNOWN GAP for a slug the grammar cannot key
  (`le._us_game_key` None: a non-grammar slug, a mapping by ledger or venue), whose markets each keep
  their own $2,500 until the game_key is filled. (6) A CANDIDATE on a game with no room opens nothing
  this tick (`game_cap_full`); with part of the room left it opens at that part. A game read FULL is
  then remembered for `GAME_FULL_MEMO_S` (60 s, `_game_full_until`, the shape of D1's terminal memo)
  and every candidate on it is skipped BEFORE its venue read under `cand_game_full_skipped` until the
  memo runs -- the first cut re-read every un-opened market of a full game on every tick, a venue read
  and one of the 20 candidate slots each; a game that frees up is read again within the minute. An
  UNREADABLE game is never memoised: its cause is a sibling's order nobody could read this tick, which
  step O retries next tick, so its markets are read again then and open the moment it reads. (7) The
  shadow check (`_shadow_check`) is handed the cap the book was sized at, so a target the game room
  scaled is not `shadow_live_disagree`; E5's comparison of the unclamped arithmetic (before the ledger
  floor, the sign flip and the short share cap) stands. (8) An UNREADABLE game is told apart from a full
  one: `game_unreadable` on the census (counted once per book at the read, and at a candidate's
  refusal) and as the plan's `game_cap` when it held an increase, beside the null `game_exposure`;
  `game_cap_full` names only a game that is genuinely full. The plan row carries `game_room`,
  `game_exposure` (null when unreadable) and, when it bit, `game_cap`; the four census names sit past
  the served 40-key prefix (raw heartbeat and plan only). The numbers, pinned end to end in
  test_mirror_live_worker section 19: A holds 3,600 @ 0.50 ($1,800) on the total line and B's 10% on
  the moneyline is 3,000 sh ($1,500 at the mark) -> B is sized to $700 / 0.50 = 1,400 sh; the same
  with A another whale's book; a short of 2,500 at a 0.72 contract counts $700 of collateral; a full
  game holds B at its 200 and sells nothing, and so does $50 of room (a room target of 100 under B's
  200); a reduce of 200 on a full game still rests; a resting 3,600 @ 0.49 counts $1,764, and one
  whose cancel failed is no room for the sibling, the candidate and the book itself (`game_unreadable`);
  the mark at 0.30 under B's 0.50 cost sizes B's increase at $36 = 120 sh and the game reads $2,498.80;
  two candidates of one game in one tick open at 4,000 and 1,000; a full game's candidate is skipped
  for the memo's minute; two games are independent; a game is walked oldest-touched first, its books in
  id order, and an abandoned tick's unreached games come first on the next.
- E2, 2026-09-06 -- LATENCY: THE MIRROR FIRES AS OFTEN AS THE WALK CAN. Owner, ~22:50Z, verbatim: "We
  need to be mapping and mirroring a larger percentage of his orders and positions, I want this firing
  as frequently as his. Make the latency as low as possible and do whatever you have to do to get us
  rolling and operating at the highest level possible." The rails stay: ratio 10% (exact under $10),
  $2,500 per game (E1), the $5,000 24 h loss stop, identity-only mapping, fail closed. BEFORE (the
  heartbeats and the code, 2026-09-06): his fill reached our trades table in -0.7 s p50 on the chain
  lane (not the bottleneck); the loop polled every 30 s; a tick took 45-52 s with 28 books and 162 s
  with 46 (20:48Z) because `_tick_book` walked the open books ONE AT A TIME with each book's 2-4 reads
  in series (~3.5 s a book), so a fill of his waited up to a tick plus a poll (~3.5 min at 50 books);
  `MIRROR_MAX_ORDER_OPS_PER_TICK` was 6 and 16 of 41 books waited a tick at 20:35Z (`ops_capped` 16);
  the candidate walk read 20 markets a tick; the bounded take waited 120 s and placed 0 IOCs all night
  (`placed_take` 0 on every heartbeat; on 22:22Z's books 9 of 26 had ever filled anything). WHAT
  CHANGED: (1) `_walk_books` -- the open books tick under an `asyncio.Semaphore` of
  `rules.MIRROR_BOOK_CONCURRENCY` (default 6, `capped_env`: env lowers only, 1 is the old walk). The
  unit of concurrency is a GAME: the walk order `_woken_first` gives (games oldest-touched first, the
  woken game first, ids ascending inside a game) is grouped by `_game_key_of` and a game's books run
  one after another under the per-book lock, so E1's rule 4 (one fixed order for a game's room)
  holds; games start in walk order. Every shared counter a book reads and writes across an await was
  audited: the ops budget is reserved at the check (`_op_slot`) and committed at the write, released on
  a refusal before it (`ops` still counts writes); an add's quantity is re-scaled on the room as it
  stands and the room TAKEN before the venue call (`_room_take`), given back on an outright refusal and
  KEPT on a lost response (money that may have filled stays counted), so two books cannot each spend
  the last clip; the read-once caches (open orders, protected ids, his snapshots, whale addresses) sit
  under per-tick locks; the grammar class's read-judge-write of its state is under a lock;
  `_lock_for`'s sweep never drops a held lock; `_place` and the flatten's slippage leg refuse a book
  still in flight when another abandoned the tick (`abandoned_in_flight`, named; NO plan is written
  for it, so its updated_at stays and E1's walk reads it as unreached). THE MISS STREAK IS JUDGED IN
  WALK ORDER (`_walk_streak`, review MEDIUM-4): the live streak counted consecutive misses in arrival
  order, and under the walk three failing reads landing before three slow good ones abandoned a tick
  the sequential walk never would; a book's read now records its outcome by slug and the run is judged
  over the walk's own order as each read lands, the candidates starting from the walk's trailing run.
  Every `t.<counter> += 1` with no await between read and write is atomic under asyncio and was left
  as it is. (2) THE WRITES ARE PACED (review HIGH-1): pmus has no pace() on submit_fok, cancel_order or
  close_position, and under the walk six books finishing their reads together placed together --
  twelve HTTP inside one 0.35 s gap, and a write 429 is `rate_limited`, the tick abandoned and every
  exit unmanaged for the 60 s backoff. Every cancel, placement and close of this lane now claims its
  gaps on the process-wide pacer (`_paced`, one gap per HTTP request: a BUY placement is a preview and
  a create, two), so the venue sees at most one request per gap from this process whatever N is. Ops
  per tick 6 -> 20 (still `capped_env`; a bound on what a tick spends, ~40 requests, not on the rate --
  the rate is the pacer's). Every venue REQUEST the tick makes is one `venue_calls` census event
  (paced reads, cancels, placements at two for a BUY, closes, every positions page of the tick's walk
  and of the flatten's own reading `ml._pm_held`, the mapping lane's resolver calls; review MEDIUM-3),
  and the soft guard `rules.MIRROR_VENUE_CALLS_PER_TICK` (default 60, `capped_env`) counts the tick's
  WRITES and CANDIDATE reads alone (review MEDIUM-5: the open books' reads are the tick's fixed cost,
  and counting them left 12 candidate reads at 46 books): at 60 the CANDIDATE walk stops
  (`venue_calls_capped`, `capped_tick`) -- never the books, never an exit. 60 is the 40 candidate reads
  plus the 20 writes. (3) POLL_S STAYS 30 s, the shadow's too: the brief allowed 10 s only with the
  tick under 10 s on the live book count, and the tick's floor is the pacer's -- every read AND now
  every write queues on `venue_pace`'s one-per-0.35 s gate, so 46 books are >= 16 s of quote reads
  before a candidate is read. The parallel walk takes the tick from ~3.5 s a book to the pacer's
  0.35 s a request; it does not get under 10 s. (4) `MIRROR_TAKE_AFTER_S` default 120 s -> 20 s
  (`min_wait_env`: env lengthens only); the PRICE RULE IS UNCHANGED -- one IOC at the same wire, only
  with the book at or through his level, never chased -- and the two verdicts are counted:
  `take_at_his_level`, `take_refused_price`. AN EXIT IS NEVER GATED BY THE REPLACE BUDGET (review
  MEDIUM-2): at 20 s a rest -> take (IOC, 0 fill) -> rest cycle spends the hour's 12 replaces in ~13
  minutes and the book is `take_capped` (entry churn, still bounded); a reduce, a flatten or a plan on
  the OTHER side of the rest (`_exit_or_flip`) cancels and goes out whatever the count -- it was
  `replace_capped` until the rest's TTL, an exit held up to 600 s behind an entry budget. The take's
  cancel of an IOC row is not a replace (`_SQL_REPLACES` filters GTC/GTD). The stale-arm window is
  max(2 x wait, `TAKE_ARM_STALE_MIN_S` 60 s): twice a 20 s wait is 40 s, and at a 0 s wait every arm
  would have been stale on the next tick. (5) `MIRROR_MAX_MARKETS` 20 -> 40 candidate reads a tick,
  the D1 terminal memo and the C2 unmapped memo unchanged; reachable at 46 books under the default
  guard (the reads the guard counts are the candidates' own). AFTER (the tests, on a fake venue whose
  quote read sleeps 0.05 s, the pacer patched out as in every worker test): 50 books tick in 2.6 s
  sequential and 0.5 s at N=6 with every stat equal, on a clean venue and on one whose every other read
  fails; 25 books in flight place exactly 20 and `ops_capped` 5; three $90 rests against a $200 room
  place 300 + 300 + 66 shares whatever the arrival order; six BUY placements in flight land one pacer
  gap apart, never a burst; a 20 s rest with the ask at his level takes ONE IOC at the same wire,
  three ticks above it none, never twice for one rest; 46 books and 41 candidates read 40 at the
  default guard. LIVE EXPECTATION, stated plainly: with 46 books the tick should land near the pacer
  floor (~16 s of book reads plus the candidates' reads and ~0.35 s per write request) instead of
  162 s, i.e. a fill of his waits ~30-45 s instead of ~3 min. Measure it on the census: `tick_s`,
  `venue_calls`, `venue_calls_capped`, `ops_capped`, `placed_take`, `take_at_his_level`,
  `take_refused_price`, `abandoned_in_flight`, `map_reads_capped`. Left out, by design: the 0.35 s
  pacer itself (it is what the venue's ~3 req/s budget is measured against and the copy and exit lanes
  share it -- lowering it is a venue-rate decision, not a latency one); a shorter poll (see (3));
  parallel candidates (they share the read budget and the soft guard, and their opens are placements).
- E4, 2026-09-06 -- EXITS FOLLOW HIS EXITS AT HIS PRICE, WITHIN ONE CENT. Owner, ~23:24Z, verbatim:
  "we should exit when he exits at his price or within 1c variance (tolerance)". THE HISTORY HE WAS
  ANSWERING, book 29 (tsc-cfb-washst-wash total 46.5, LONG 63 shares): his net flipped short at
  ~20:30Z; our flatten rest sat at his level -- 0.46 = max(his equivalent 0.4595, ask),
  `rules.sell_price` -- while the US market fell to bid 0.01 / ask 0.02; the TTL cancelled and
  re-quoted it 14 times at the same cent and it never filled; the bounded take
  (`MIRROR_TAKE_AFTER_S`, "only at/through his level") never fired because the bid never came back
  to the rest; the position went to settlement. THE RULE (every exit: a reduce, the paired flatten,
  the vanish flatten, the sign-flip flatten, a short book's cover; entries are NOT given it):
  (1) an exit is planned the tick his reduce is seen, as before, and its take waits for nothing.
  (2) our exit price is HIS EXIT PRICE for the token -- `his_px`, his reduce fill on the token as
  `_his_level` reads it; for a short cover his buy-back in long space, 1 - his price on the other
  token -- worse by at most `rules.MIRROR_EXIT_TOL` (0.01; `capped_env`, the environment may only
  TIGHTEN it, floor 0). `rules.exit_terms` derives every price from that one figure: a long book's
  SELL has a floor of his price less the tolerance, RESTS at the cent ceil(his price) (post-only;
  no longer max(his, ask) -- the ask never lifts the rest above him) and TAKES one IOC at the
  lowest cent at or above the floor the tick the bid is at or through it, whether a rest stands
  (cancelled first) or not; the IOC's limit is that cent, not floor-to-cent of the floor, because an
  off-cent floor floored (0.4595 - 0.01 = 0.4495 -> 0.44) would admit a fill 1.95c under him; on a
  cent floor the two agree. A short book's cover (close_position, the one short exit before rung S4)
  has a ceiling of his price plus the tolerance: `_flatten_send` reads the tick's ask against it
  BEFORE the position read and the close, refuses `exit_out_of_tol` above it (held, no freeze, read
  again next tick) and closes as before at or under it; a short's partial reduce stays
  `short_reduce_unproven`. He gave no exit price (a snapshot-driven reduce, a vanish with no fill
  of his inside the lookback, the admin flatten): the plan keeps its old prices and its old
  slippage leg under `exit_px_src: 'none'`, never a guessed level. (3) NEVER CHASE PAST THE CENT:
  outside the tolerance the rest stands (`exit_out_of_tol`, the plan carries bid/ask/floor), the
  take does not fire, and a rest past its TTL at the same cent is NOT cancelled and re-placed --
  `_reconcile_open`'s TTL clause skips an exit rest, `keep_or_replace(stands=True)` keeps it, the
  no-op is named `requote_same_wire`, and under the GTD flag an exit rest carries no good-till so
  the venue cannot expire it into the re-quote. The C16 slippage leg of a vanish (close_position /
  the IOC at bid less slippage after `MIRROR_FLATTEN_REST_S`) runs only for an unpriced vanish: on
  a priced one it would sell past the cent, so the book is held at his cent instead. Book 29 under
  this rule: taken at 0.45+ at ~20:30Z while the bid was there; once the market fell, held. (4) the
  replace budget (`MIRROR_MAX_REPLACES_PER_HOUR`) never blocks an exit's take or rest: the
  `_requotes_this_hour` read at the keep and replace paths is skipped for an exit or a side change
  -- E2 v2's `_exit_or_flip` (review MEDIUM-2a), the one mechanism; a take on an exit is not a
  replace, and an IOC row is never one (`_SQL_REPLACES` counts GTC/GTD cancels). (5) the loss stop and every increase refusal never block an exit, as
  before, pinned on the new take path. (6) census: `exit_take`, `exit_out_of_tol`,
  `requote_same_wire`; plan fields `exit_px`, `exit_px_src`, `exit_floor` / `exit_rest` /
  `exit_take` on a long book, `exit_ceiling` / `exit_cover` on a short, and `exit_out_of_tol`
  {bid, ask, floor|ceiling, at} while held. (7) the shadow's exit leg records the same floor, rest
  and take cents beside `would_px` (`exit_floor`, `exit_rest_px`, `exit_take_px`); the live/shadow
  comparison (`_shadow_check`) compares the TARGET alone and reads no price, so the new price can
  name no disagreement. THE ADDENDUM, ~23:38Z, verbatim: "Remove the 20 second wait on entires
  too". `MIRROR_TAKE_AFTER_S` default 20 s -> 0 (`min_wait_env`: the environment may only LENGTHEN
  it). An increase with the ask at or through his level -- the entry price rule, unchanged: at or
  through the wire, never above him, NO tolerance -- sends ONE IOC at the wire for the plannable
  quantity FIRST (`take_first`, on the no-rest path, never through the armed path) and rests the
  unfilled remainder post-only at the same wire (`_entry_take`); the ask above his level rests as
  before and the take fires the tick the ask arrives, with no age condition on the rest. The
  take-first IOC cancels no rest and so consumes no replace (`_SQL_REPLACES` counts GTC/GTD
  cancels only); an entry's take + rest is two ops against the tick's budget; the room is reserved
  once for the plan -- the IOC's unfilled part is given back before the remainder is sized
  (`_place_reserved`), so a $45 room places an IOC of 150 that fills 50 and a rest of 100, never
  `over_room`; never twice for one plan (the IOC's remainder is the venue's cancel, the rest is the
  only standing order). Under a LENGTHENED wait E2's rest-first take and the arm's staleness bound
  (`TAKE_ARM_STALE_WAITS`, read only under a positive wait) run exactly as they did. PINS
  (test_mirror_live_worker section 21): a long reduce at bid = his - 0.01 takes one IOC that tick
  and at his - 0.02 rests at his cent, `exit_out_of_tol`, then takes the tick the bid comes back;
  the book-29 replay rests at 0.46 against 0.01/0.02 and stands five TTL periods with one order
  row and `requote_same_wire`, then takes at 0.45; a short cover at ask 0.32 against his 0.30 is
  held and closes at 0.31; exits ignore the replace budget and the loss stop; an unpriced vanish and
  the admin flatten keep today's prices under `'none'`; a partial exit IOC leaves nothing resting;
  the entry take-first spends its room once with two ops and no replace; every entry pin (E2
  section 20, the arm bounds of sections 13/14 under a lengthened wait) is green. PINS UPDATED for
  the behaviour the owner changed: the paired-out rest (was max(1 - q, ask): now his cent, the
  take within a cent), the section-7/20 take pins (was a 20 s wait), nine vanish-slippage pins now
  run on an unpriced vanish (`_unpriced`), three short-cover pins put the ask inside the ceiling,
  one pre-050 intent pin puts the bid two cents under him. Left out: a fresh venue read of the ask
  before a short's close (the tick's own read, seconds old, is what every other price decision
  reads; a second paced read a tick is the same budget the venue-call guard exists for); a rest for
  the remainder after a partial EXIT IOC in the same tick (the brief's (i): the next tick plans it,
  and the bid still there is another take); `flattened` on the stats line counts the flatten rows
  only, not an exit take that empties a flatten book (read `exit_take` beside it).
  REVIEW FOLD (2026-09-07, E4 review round 1: the exit half held every attack, the addendum half did
  not). HIGH-1: the entry's take-first never fired in a normal book -- it was judged at the REST's
  wire, buy_price(his, bid) = floor-to-cent of min(his, bid), at or under the bid, and `at_or_through`
  needs ask <= wire, impossible with bid < ask (only a locked book fired; `placed_take` 0 all night).
  Now the entry's take is judged and SENT at HIS cent, `rules.buy_wire(his)` (floored, never above
  him), on the standing-rest path and the no-rest path alike; the rest keeps its wire and
  `keep_or_replace` compares the rest's wire; a short book's add stays on the rest path (its wire is
  `_short_wire`'s contract cent; no take-first is built for it). Pins in a normal book: his 0.30,
  bid 0.29 / ask 0.30 -> one IOC at 0.30 first, the remainder rests at 0.29; ask 0.29 (through) ->
  the IOC at 0.30; ask 0.31 -> the rest alone, and the ask arriving at 0.30 later cancels and takes;
  his 0.2999 with the ask at 0.30 rests (his cent is 0.29). E2's pins that read "the IOC at the same
  wire" now read his cent (0.31 on the fixture's his 0.31, the rest at 0.30). MEDIUM-2: a priced
  short cover judged on the tick's ask then sent close_position with 300 bips and no limit could fill
  past the ceiling; now the ask is read again, paced, immediately before the close (one extra read,
  on a priced cover alone), judged against the ceiling again, and the close is sent with
  min(EXIT_SLIPPAGE_BIPS, max(1, floor((ceiling / ask - 1) x 10000))) bips -- at the ceiling itself
  ONE bip (the adapter refuses 0), a hundredth of a cent the ladder cannot express, so the fill is at
  the ceiling cent: the rule is "at the ceiling, never a cent past it"; his 0.30 -> ceiling 0.31: ask
  0.31 -> 1 bip, ask 0.30 -> 300 (333 capped), a fresh ask of 0.33 -> held with the fresh quote on the
  plan. An unpriced cover keeps the adapter's slippage and one read. MEDIUM-3: the priced vanish
  never running the slippage leg is pinned (the reviewer's a7, ported). LOW-4: `exit_take` is counted
  where the IOC is SENT (`_place_reserved`, beside `take_placed`), not at the decision an ops-capped
  cancel could refuse. LOW-6: `rules.exit_terms` reads `MIRROR_EXIT_TOL` at call time (the default is
  None, never a value bound at import), so the environment's tightening and a test's monkeypatch
  both reach the worker.
  REBASE ONTO E2 v4 (2026-09-07). E2's round-3 fold dropped the pacer's slot reservation (one
  claim per request; the adapter claims its own gap before the create), skipped the 60 s backoff on
  a placement 429 while the circuit holds, and made a TTL/replace cancel and its re-rest ONE op
  (`_Tick.requote_credit`, spent in `_place` by a non-IOC alone). E4 adds no write path of its own,
  so the first two need nothing of it. The credit meets E4 in three places, each pinned: the
  same-wire path sends no cancel (a reduce rest past its TTL is left to the plan and
  `keep_or_replace(stands)` keeps it), so no credit is granted there -- nothing to clear, none to go
  stale, and a `_Tick` is built per tick so an unspent credit never outlives one; an IOC -- an
  exit's take, an entry's take-first -- never spends it and is its own op (cancel + IOC is two ops,
  cancel + IOC + the remainder's rest is two); and an IOC the budget refused with the credit standing
  rests the plannable quantity on the credit instead (`_entry_take`), so a TTL cohort's crossing
  book is not left bare for the tick, which is what the credit is for.
  REVIEW FOLD (2026-09-07, E4 review round 3: one MEDIUM, three LOWs; every attack on the cent, the
  one-IOC-per-rest rule, his price moving, the cover's sources and fresh read, the entry's cent, the
  frozen books, pacing and the credit held). M-1 (MEDIUM): the exit's take off a standing rest is a
  cancel and an IOC, two ops with no credit, and at the budget's LAST op the cancel went and the IOC
  was `ops_capped` -- the book had NO exit order for the tick, the one thing an exit is never (E2
  LOW-6: "an exit is never shed"). Decision: EXITS ARE EXEMPT FROM THE PER-TICK OPS BUDGET.
  `_op_slot(exit=True)` hands back a slot whatever the count -- reserved, committed, counted in `ops`,
  on `venue_calls` and the soft guard at the write, never refused -- and every op on an exit's path
  takes one: the take's cancel and IOC on the keep path, the replace's cancel and the IOC or the
  re-rest after it, the named cancel under an exit plan, the exit's rest and IOC with no rest
  standing (`_place` reads exit-ness off the side's leg action on the book, so a short book's SELL --
  an add -- stays bounded and its BUY -- the cover -- is exempt), the flatten's cancel before its
  close or slippage leg and that close or IOC (`_flatten_vanished`), the short cover's close, and
  step O's TTL re-quote of a reduce rest (the first half of an exit's re-quote; its re-rest rides the
  credit). It mirrors how the replace budget (`_exit_or_flip`) and the loss stop already exempt
  exits. Entries are bounded exactly as before, E2's take-first fallback on the credit included.
  Pins (`test_e4_r3_an_exit_is_exempt_from_the_ops_budget...`): the reviewer's c1 / c2 shapes at
  budget 1 AND at budget 0 send the cancel and the IOC (`ops` 2, `exit_take` 1, `ops_capped` 0), the
  replace outside the cent sends the cancel and the credited re-rest (`ops` 1), the no-rest IOC and
  rest each one op, an ENTRY at the same budget is still `ops_capped` (its IOC at 0, its remainder's
  rest at 1, a plain rest at 0), and at budget 0 the sign-flip flatten's take, the unpriced vanish's
  cancel + close and the short cover's close all go, each counted. D-1 / D-2 (LOW): step O's TTL
  clause skipped EVERY reduce rest, so an UNPRICED reduce's TTL re-quote ran through
  `keep_or_replace`'s age clause under the reason `replace` -- which `_SQL_REPLACES` counts against
  the book's ENTRY budget (a `ttl` never was) -- and on an abandoned tick, which never plans, the
  unpriced rest stood past its TTL for the whole backoff. Narrowed to PRICED exit rests:
  `_priced_exit_rest` -- a reduce rest under a book whose last plan says `exit_px_src: 'his_fill'`,
  the same predicate `_place_reserved` reads for the no-good-till; the plan is read off the book
  because step O runs before any book is planned and the order row records no price source. With
  NO plan on file (a row placed before its plan was written, a book from before E4) the row's own
  facts decide: a long book's rest whose wire is the cent of the level it was placed against
  (`his_level` on the row, `rules.sell_wire`) is at his cent; a rest the ask lifted, a rest with no
  level of his and a short book's are unpriced (fail closed: a TTL re-quote never sheds, its re-rest
  rides the credit). An unpriced reduce rest keeps today's `ttl` re-quote (reason `ttl`, `requotes`
  1, never `replace`, `_SQL_REPLACES` 0) and is TTL'd through an abandoned tick as before E4; a
  priced rest stands (`requote_same_wire`, the book-29 replay unchanged) and stands through an
  abandoned tick. Pins: the reviewer's d1 / d2 shapes read `ttl` / cancelled on the abandoned tick;
  the priced rest stands on both; with no plan on file a rest at his cent stands and a rest the ask
  lifted (or with no level of his) takes ONE `ttl` re-quote to his cent and then stands on the plan
  it wrote; the predicate on every shape, the plan winning over the row. L-3 (LOW): `mirror_live._his_level` handed the
  other-token equivalent back unrounded (0.47996 -> 0.5200400000000001) and `mirror_shadow.his_level`
  to 4 places (0.52), so the shadow's `exit_rest_px` was 0.52, a cent under the live rest at 0.53.
  Both now round the same way, `round(1 - p, 6)` -- the executor's own precision, which keeps
  0.52004 (the rest 0.53) and absorbs float noise (1 - 0.77 reads 0.23 in both) -- so the shadow's
  rest cent IS the live rest's on every fixture (pinned with the reviewer's i1 numbers, and on a
  tick: his other-token BUY at 0.47996 rests at 0.53 with `exit_px` 0.52004). L-4 (LOW): with no op
  left the take's cancel was refused and the plan read `take` with nothing sent. `_cancel_outcome`
  names it `cancel_refused:<reason>` (`cancel_refused:ops_capped`: reachable on an entry's take or
  replace at the cap, unreachable on an exit since exits are exempt -- pinned by a refusal forced
  onto the exit path), and a cancel that went out and left the order unknown reads `cancel_pending`
  -- the freeze's own name -- on the take paths as the replace path always read it. The reviewer's
  spec notes stand unchanged: the exit IOC at the floor cent when the bid sits at his cent, the
  entry IOC at his cent, a priced vanish held when the market never returns within a cent. Two
  source pins moved with the reconcile clause (`not _priced_exit_rest(o, book)` for
  `_order_action(o, book) != "reduce"`); no other pin moved. Census: no new key; `ops` above the
  budget reads as exits going out (docs/mirror-coverage.md §13).

- E2 review round 2 fold (2026-09-07). HIGH-A: `_paced(slots=2)` claimed its two gaps as two pace()
  calls with the gate released between, so six contending BUYs fired twelve requests inside ~five gaps;
  now ONE locked claim -- `venue_pace.pace(gap, slots=n)` claims the slot and RESERVES the next n-1
  (each a gap on), and the adapter takes the reserved slot between its preview and its create
  (`pmus.submit_fok(paced_pair=True)`, what `_guarded` passes; off for every other caller, whose pair
  stays back to back and never queues behind measurement) -- so every request of this lane is one gap
  from any other, the create one gap after the preview, and two writers cannot interleave. HIGH-B: the
  venue answered five HTML 429s in 1.5 h at ~1 req/s (22:48:48, 22:49:10, 23:35:51, 00:10:32, 00:11:33
  in the worker log, 'walk failed') BEFORE E2 raised the rate, so (1) a 429 CIRCUIT ON THE GAP:
  `venue_pace.penalize()` doubles the pacer's gap (PENALTY_MULT 2) for PENALTY_S (600 s) from the last
  429, for every lane on the gate, and it expires on its own; called from every site that reads a 429
  -- a placement (with the abandon, as before), a quote read (`_bbo`: now named `rate_limited`, never
  `no_quote`, the market refused for the tick and the outage streak untouched), a cancel whose error
  names 429/RateLimit (named, the reads decide the order as for any failed cancel), and the positions
  walk (`ms.account_positions_walk`, per call: positions, pages, rate-limited) -- each counted
  `rate_limited` on the census; (2) the live lane's OWN candidate budget, `mirror_live.MAX_MARKETS_PER_TICK`
  (env MIRROR_LIVE_MAX_MARKETS, 40, lowers only), the shadow's `ms.MAX_MARKETS_PER_TICK` back at 20 (one
  shared name had doubled the shadow's reads too); (3) the guard default 60 -> 80: 20 BUYs are 40
  requests, and 40 + 40 is what leaves the candidates their whole 40 (at 60 they had 20). LOWs: the
  positions page count is per call (b); the grammar echo pages through the pacer and counts per page
  (d); the flip's ADD half stays exempt from the replace budget, documented on `_exit_or_flip` (e); a
  raise inside the walk-order streak judge is named `walk_error` and logged, the game's walk goes on
  (f); E1's short-book collateral sizing test folded into section 19. RATE, RE-MEASURED (46 books, 20
  BUY placements, 41 candidates, N=6, on the fakes): 46 book reads + 2 (open orders, positions) + 40
  placement requests + 40 candidate reads = 128 requests a tick (measured, `venue_calls`);
  every one claims a 0.35 s gap, so a tick is ~45 s of pacer time and the sustained rate is the
  pacer's 2.86 req/s (171/min) whatever N is -- 1.43 req/s (86/min) while the 429 circuit holds, and
  the walk's concurrency only decides how much of that time overlaps the database and data-API reads.
  Before E2 the same tick was ~48 requests (6 ops, 20 candidates) over 162 s = 0.3 req/s; the venue's
  ~3 req/s limit is the pacer's own bound, and the circuit is the answer to the 429s it already sent
  under it.

- E2 review round 3 fold (2026-09-07). HIGH-1: round 2's RESERVED second slot recorded the create at
  its reserved time, not when it fired, so a preview whose HTTP took longer than the gap (live latency
  ~0.65 s a request against a 0.35 s gap -- the common case) fired its create late and the next claimant
  landed a fraction of a gap after it. The reservation is GONE: `venue_pace.pace(gap)` is one claim per
  REQUEST -- `_paced` claims before the preview, `pmus.submit_fok(paced_pair=True)` claims its own gap
  before the create -- so pairwise spacing holds at any latency (pinned on the REAL gate, real sleep,
  preview slower than the gap, six writers and three readers). MEDIUM-2: `penalize()` took the gate's
  lock, which every pacer holds through its sleep, so a 429 handled on the event loop stalled the loop
  a gap or more; it is one float store under its own tiny lock now (< 5 ms with six threads asleep in
  the gate). MEDIUM-3: a placement 429 was triple-punished (circuit x2, the abandon, a 60 s backoff
  with every exit unmanaged): the abandon stays, the backoff is skipped while the circuit holds
  (`backoff_skipped_circuit`); an outage abandon still backs off. LOW-4: `is_rate_limit` matched '429'
  anywhere in free text (an order id, a slug, a price); now the SDK's RateLimitError class, a
  status_code of 429, or a text that STARTS with the name / '429' / 'Too Many Requests'. LOW-6: a TTL
  cohort's cancels ate the whole ops budget in step O before any book was planned (46 expired rests ->
  20 cancels, 0 re-rests, 20 books bare for a tick); a TTL or replace cancel and its re-rest are ONE op
  (`requote_credit`: the cancel's op covers the rest that follows on that book this tick; a take's
  cancel is not credited), so the same tick cancels 20 AND re-rests 20, the 26 others `ops_capped`
  with their rests standing -- an exit is never shed. Rate table, re-measured on the fakes (46 books,
  41 candidates, N=6, one 0.35 s gap per request): 20 fresh BUYs -> 128 requests, ~45 s of pacer time,
  2.86 req/s = 171/min sustained (86/min under the circuit); the TTL-cohort tick (46 expired rests) ->
  20 cancels + 20 re-rests (60 write requests) + 46 + 40 quotes + 2 = ~148 requests, ~52 s of pacer
  time; every tick's rate is the pacer's whatever N is, and the poll adds 30 s.

- E2 review round 4 fold (2026-09-07). HIGH-1: round 2's "every site that reads a 429 trips the
  circuit" was false for half of every BUY's requests. `pmus.submit_fok` wraps only the CREATE (and
  only under post_only), so the SDK's `RateLimitError` raised by a BUY's PREVIEW -- or by an IOC take's
  create, sent with post_only False -- crossed `_guarded` into `_place_reserved`'s except and was read
  as a LOST response: no `rate_limited`, no circuit, one more paced read into the limited venue (the
  open-orders search) and the book FROZEN `placement_lost` on a 'placing' row for twenty minutes though
  nothing had been sent. A 429 is the venue refusing the request before it processed it: the except
  now reads `ms.is_rate_limit` on the raise first (`_place_rate_limited`) -- `rate_limited` and the
  circuit, the row refused `place_refused:rate_limited` with a receipt naming the raise, the room
  given back, the tick abandoned `rate_limited` as on the create's 429 (mid-tick consistency; the
  backoff skipped while the circuit holds); no freeze, no search; the next tick places the book. The
  same road for any placement raise the match names. Pinned through the REAL adapter on the SDK's own
  exception (a preview 429, an IOC create 429, the post-only create 429). MEDIUM-2: the placement
  sites still matched '429' as a substring -- `"429" in raw.error` on a post-only rejection and
  `"429" in json.dumps(raw)` on any refusal without an id -- so a `preview_mismatch` whose
  expected_cost was $429.xx (per book, recurring every tick) or a 400 saying "would cross at 0.429"
  abandoned the tick and halved every lane's rate for ten minutes. Both sites read the raw's NAMED
  fields now (`_raw_rate_limit`: `status_code` 429, `error_type` naming RateLimitError -- the adapter's
  4xx refusal carries the exception's class beside its text, as close_position's does -- or `error`'s
  head through the anchored match); the pin that enshrined the old text is replaced. LOW-3: the
  re-quote credit's `tif != "IOC"` clause is pinned by behaviour (a credited book placing an IOC spends
  its own op and keeps the credit; the GTC rest after it spends the credit and no op), not by its text.
  LOW-4/5: the positions walk's 429 called `_rate_limited` (the circuit) and abandoned
  `positions_unreadable`, so the 60 s backoff applied on top of the circuit and the next tick was
  skipped while a placement 429 skipped it. `_abandon` (and `_abandon_reconciled`) take an explicit
  keyword flag, `rate_limited`, passed by the three placement sites and the walk's 429 site (the
  reason keeps its name); the skip is `rate_limited and penalty_left() > 0.0` -- the name alone no
  longer decides, and the circuit clause stays because every site that passes the flag calls
  `_rate_limited` first in the same block (a flagged abandon with no circuit still backs off). An
  outage abandon while an earlier circuit holds still backs off. LOW-6: the woken-first pin read the
  fake venue's call log, which two worker threads append to after a wall-clock sleep each, so the
  first slot's read could land second under load; it reads the walk's ENTRY order on the event loop
  now (the order the tasks were created in and the semaphore hands slots out in), on explicit
  updated_ts values, and pins the whole order -- the woken game first, then the games oldest
  updated_at first, not id order. No rate-table change: a refused preview is one request, as before.
- E2 review round 5 fold (2026-09-07). c2/c3: round 4's seam sat in `_place_reserved` alone; the
  flatten's two placements had none. The SOLE-HOLDER CLOSE: `pmus.close_position` catches every
  exception into a `close_failed` raw naming `error_type` (4a5da1f), and for the SDK's
  `RateLimitError` that raw reads as a 429 by name -- but `_flatten_send` never asked: it re-wrapped
  the raw's text into a RuntimeError for `_lost_response`, which froze the EXIT book `placement_lost`
  on a 'placing' CLOSE row, and `_reconcile_lost_close` kept the freeze ("nothing_sold") until the
  1200 s window marked it lost -- the whale gone and our shares held behind a frozen book for twenty
  minutes, over a request the venue refused before it processed anything. The CO-HELD IOC: its create
  (sell=True: no preview, post_only False, no 4xx wrapper) raised through `_flatten_send`'s own except
  straight into `_lost_response` -- one more paced read into the limited venue, then the same freeze.
  Both now go the round-4 road (`_place_rate_limited`, which takes the adapter's raw beside a raise):
  `rate_limited` and the circuit, the row refused `place_refused:rate_limited` with the raw (or the
  raise) as its receipt, the tick abandoned `rate_limited` with the backoff skipped, no freeze, no
  search; the next tick runs and the flatten begins again on a live book (the vanish clock restarts:
  the SELL rests its MIRROR_FLATTEN_REST_S first, then the slippage leg). A socket reset on the same
  create is still the search and the freeze: only a refusal the venue NAMED is a refusal. d5 (LOW):
  `_raw_rate_limit` and `ms.is_rate_limit` read an int `status_code` as AUTHORITATIVE -- 429 is a
  rate limit and any other int is not, whatever `error_type` or the text's head says (a
  BadRequestError(400) whose message begins "429 contracts exceeds the maximum order size" is a size
  refusal that arms the take and abandons nothing; on v5 it abandoned the tick and halved every lane
  for ten minutes, recurring every tick); only a raw or a raise with NO int status (close_failed's
  shape, a cancel's error string, the walk's RuntimeError) falls back to the name and the anchored
  text. The reviewer's three FINDING tests are in section 20f verbatim, beside the full-road pins.

### 5b. OPERATOR NOTES (2026-09-05): reading `venue_halted`

`abandon_reason: venue_halted` (census key `venue_halted`, the WARNING `mirror_live: tick abandoned
(venue_halted: MARKET_STATE_HALTED), backing off 60.0s`, the mode line's `venue=MARKET_STATE_HALTED
abandon=venue_halted`) means the venue answered our quote reads and said, in its own words, that the
market is not open for trading. The string after the colon is the venue's own `marketData.state` as it
spells it (`MARKET_STATE_HALTED`, `MARKET_STATE_SUSPENDED`, `MARKET_STATE_PREOPEN`, `MARKET_STATE_CLOSED`,
`MARKET_STATE_EXPIRED`, ...), read from the same `bbo`/`book` payload the quotes come from; it is not a
word of ours. It is a different fact from `no_quote`, which now means only "the read failed on every
feed (the error is named on the `BBO for <slug> unreadable (...)` line)" or "an OPEN market with an
empty book". On the night of 2026-09-05 the venue was halted venue-wide for five hours with HTTP 200 on
every read, and the mirror's fail-closed answer — abandon, back off, place nothing — was right while its
name for it (`no_quote`) cost forty minutes and an external probe. A quote on a non-OPEN market (a settled
market's stale resting book) is counted `venue_halted` too and is never traded on -- and the flatten's
slippage leg (a sole holder's `close_position`, the co-held IOC at the bid) refuses under the same name
on a slug whose quote read that tick carried a non-OPEN state, before it reads a bid or a position: its
own bid read (`slug_bid`) carries no state, so without this a settled market's stale rests were a
tradeable bid to it (2026-09-06). Nothing about this is
sticky: the mirror resumes on its own, on the next tick after the backoff, the moment the state reads
`MARKET_STATE_OPEN` and a quote is present. A tick that falls inside the backoff prints
`mode=<the mode the worker holds> ... backoff=<seconds left>` on the mode line, never `mode=safe` unless the
mode is safe.

2026-09-06: with the venue OPEN, an empty book is a per-market refusal (`no_quote`), not a venue miss; only
non-OPEN states and unreadable reads count toward the three-miss abandon -- and of the non-OPEN states, only
a non-terminal one (`MARKET_STATE_HALTED`, `MARKET_STATE_SUSPENDED`, `MARKET_STATE_PREOPEN`, ...) read on a
NEW candidate: a read on an existing book never counts (the book's own handling names it `venue_halted`,
cancels its rests, plans nothing and closes the book once the markets row reads closed; an abandon inside the
book walk would skip every book after it), and a terminal state (`MARKET_STATE_EXPIRED`, `MARKET_STATE_CLOSED`,
`MARKET_STATE_TERMINATED`, and since the U10 review `MARKET_STATE_MATCH_AND_CLOSE_AUCTION`, a market's own closing
phase; `mirror_shadow.STATE_TERMINAL`) counts nowhere, because a market that has ended is
a per-market fact and a venue cannot expire every market (the 12:32Z `tick abandoned (venue_halted:
MARKET_STATE_EXPIRED)` between placements was one book on an ended market plus his morning's expired markets
still inside the candidate lookback, not an outage). Every such read still counts `venue_halted` or `no_quote`
on the census and publishes the state; three HALTED candidates in a row still abandon under that name. The
shadow's streak reads by the same rule.

2026-09-06: a sale within one venue lot of the ledger is dust (census `ledger_dust`), booked to the ledger
and never a trip; the trip stays for a sale more than a lot past the ledger. The standing row holds the
venue's FRACTIONAL fills (book 1 on `aec-wta-yulsta-eleryb-2026-09-05`: 182.76 then `mirror row 411040
booked BUY 231.0 @ 0.12 ($27.72, order CAM68KQR8PMV seq 0): now 413.76 shares`) while
`mirror_books.ledger_net` is an integer column and read 414; the flatten sized its SELL off the integer,
the venue sold 414.0, and the 0.24-share rounding gap was booked and tripped as an overfill:
`01:11:57Z mirror row 411040 booked SELL 413.76 @ 0.12 (entry 0.12, pnl +0.0000, OVERFILL: venue sold
414.0)` then `MIRROR LIVE TRIPPED OFF: overfill {'book': 1, 'order': 33, 'sold': 414.0, 'ledger': 0}`, and
the mirror was exits-only from 01:13Z on a number that is neither a short nor a wrong sign. Now
(`SELL_DUST_SHARES = 1.0` in the rules module, one venue lot, not an env dial): the executor's line reads
`..., DUST: venue sold 414.0, ledger held 413.76` and books the ceiling, the rules booking carries
`dust=0.24` with `overfill=False`, the worker counts `ledger_dust` (appended LAST in `CENSUS_KEYS`, so no
served index moved) and notes a `dust` entry in `recent`, the book stays live and `mirror_live` is not
written. A sale MORE than a lot past the ledger (415.5 on 413.76) is the overfill exactly as before --
frozen, tripped, and the receipt now carries all three numbers: `sold`, `ledger` (after the booking) and
`held` (the standing row's shares before the sale). The SELL is also sized from the row now, for the IOC,
rest, take and reduce legs: `qty = min(plan, ledger, ceil(held))` off the same standing-row read
`_tick_book` already makes, taken once per tick (414 with 413.76 held places 414, and the one-lot overrun
onto the fractional row is exactly the dust above, so the book reaches flat -- ledger 0, row 0.0 -- after
one flatten and takes its flat close); NOT floored (the first cut's floor(413.76) = 413 left ledger 1 /
row 0.76 / venue 0.76 that nothing could sell: `under_one_share` every tick, no flat close). With the row
unreadable that tick the sizing stands on the ledger as before. The sole-holder `close_position` is never
clamped and never gated: it closes the whole slug, fraction included, and its `mirror_orders` row keeps
`qty = ledger` so a lost close reconstructs `sold = qty - int(held)` as the whole ledger. Dust ACCUMULATES
per order: each SELL delta's dust is added to the order's `dust_total` (kept on its `receipt` JSON, re-read
with the row each poll, no column), and the delta that takes one order's total past `SELL_DUST_SHARES` is
the overfill -- a flat row taking 1.0-share deltas per poll trips on the second delta, 0.6 then 0.6 trips
on the second, a single 0.24 never -- with `dust_total` on the trip receipt beside `sold`, `ledger` and
`held`. `ledger_dust` also rides on the served `integ` block (`_INTEG_CENSUS_KEYS`), since it sits past
the sanitizer's 40-key cap in `CENSUS_KEYS`.

2026-09-06, D1 (owner 18:57Z: "I don't see trades firing on POLYMARKET and we need more volume"): HIS
FILLS WERE COUNTED TWICE ACROSS THE CHAIN AND POLL PATHS, AND THE MIRROR NOW READS THE VENUE-EXACT
POSITION. Book 16, `aec-wta-markos-linnos-2026-09-06` (his `wta-kostyuk-noskova-2026-09-06`), read from
the production `trades` table at 19:13Z: chain BUY Noskova 50 rows / 29,555.0 sh, chain BUY Kostyuk 37
rows / 49,483.5, poll BUY Noskova 2 rows / 10,224.4, poll BUY Kostyuk 10 rows / 42,661.7. The chain path
(`ingestion/chain.py`, `_wallet_1155_legs`) writes ONE row per tx per token -- the wallet's net 1155 legs,
size = the whole taker order, price = its average -- and the Data-API path (`ingestion/poller.py`, the
reconciler's re-sweep, `backfill`) writes ONE ROW PER MAKER MATCH, so the ingest dedupe key
(`ingestion/dedupe.py`: tx, asset, side, size, price, ts) never collapses them when a taker order matched
more than one maker: tx `0x5446ded9b2e157ec` Kostyuk BUY is chain 15,164.0 @ 0.563 AND poll 4,996 @ 0.560
+ 5,172 @ 0.560 + 4,996 @ 0.570 = 15,164.0, the same fill twice (likewise `0x9cf14ff1b7711eb6` 14,777.0,
`0xaa52fe6c5a12901d` 10,224.4, `0xf66028301c277143` 6,210.8); 46,376.2 extra shares on this one market.
THE RULE, read-side, in `mirror_shadow.his_fills` (SQL, a window over the key, deterministic): ONE reading
per (tx_hash, asset, side) -- the net-leg row (`source` in `chain`/`s1`, `FILLS_NET_LEG_SOURCES`) when the
key holds one, else every per-match row (a poll-only tx the chain path missed is kept whole; two poll legs
of one tx with no chain row are both kept; a poll leg that does NOT sum to the chain row still collapses --
the chain row is the wallet's net legs, the truth). THE PROOF it is the venue's own number: the exit
worker's snapshot of his wallet (19:10Z-19:13Z, `fills_since` 0) read long 55,993 / other 29,555;
collapsed, the fills read 49,483.5 + 6,509.9 (the two poll-only Kostyuk txs) = 55,993.4 long and 29,555.0
other -- the snapshot, to the share -- where the raw table read 92,145 / 39,779, drift 0.44 against the
snapshot, so `rules.admission` refused every increase on his most active book under `drift`
(MIRROR_DRIFT_MAX 0.05): 157 shares held against a target of ~2,900. Every reader of his position goes
through `his_fills` and gets the collapse: `shadow_market`, `mirror_live._tick_candidate` / `_tick_book`
(`net_positions`, `_his_level`, `fills_since`), `mirror_report.mirror_cover_report` (`notional_in_window`,
`gross_sh`, `paired_sh`); the two raw `trades` reads left in the shadow derive no position (`compute_ratio`'s
opening bursts -- a diagnostic since U12b -- and `active_conditions`). What was dropped is counted on
every call (`his_fills_dedup`): the shadow sums it per tick as `fills_dedup_rows` / `fills_dedup_shares`
(and per row on `detail`); the live tick as ONE nested block `fills_dedup = {rows, shares}` appended after
every other key -- `integ` sits at 39 keys under the sanitizer's 40-key cap and the top level one slot
short of it, so the block is what a tick that also appends `capped_tick` or `abandon_reason` loses from the
served surface, never those. S1 rows (`ingestion/s1_emitter.py`) are the second chain source: an `agg`
record is the same aggregate view as the chain row, the ingest probes on both paths (`SQL_PROBE`,
`_handle_v3`'s pre-probe, `source IN ('chain', 's1')` per (tx, whale, asset)) keep an s1 row and a chain
row off the same fill, and an s1 row CAN share a tx with poll rows (the venue re-delivers the fill; a
multi-maker split lands under other keys), so they follow the same rule. Known residual of that rule: the
emitter's `same_asset_entry` deferral (a taker sweep filling two of his resting orders on one token in one
tx: s1 emits leg 1, the poller carries leg 2) collapses leg 2 under the s1 row -- an UNDER-read by one leg,
counted on the emitter's beat as `s1.abstain.same_asset_entry`. The direction of that error depends on the side (review D1, minor 2): a lost BUY leg under-reads his position (the mirror holds less: conservative), a lost SELL leg over-reads it (the mirror keeps holding what he sold). The partition also carries `whale_id` (review minor 1): `whales.username` is not unique, so a second wallet filed under one username in the same batched tx cannot have its poll legs swallowed by the first wallet's chain row.
THE INGESTION-SIDE FIX IS NOT IN THIS CHANGE: the table is shared with the copy lane, the edge analytics
and the reconciler, and a wrong dedupe at ingest loses fills; the durable fix is a dedupe on (tx, asset,
side) at ingest with a sum check. His day by source at 19:13Z: chain 9,659 rows / $1.87M, poll 1,297 rows
/ $813k, s1 109 rows / $167k. Tests: `tests/test_d1_fills_dedup.py` executes the SQL against a scratch
Postgres on exactly the rows above (skips visibly without one, the `test_s1_sql_real_pg` shape).
Beside it, the live candidate walk's TERMINAL MEMO (`mirror_live._terminal_until`,
`cand_terminal_skipped`; docs/mirror-coverage.md §1.5): the 19:02Z census read `venue_halted` on 26 of 29
candidate reads, every one `MARKET_STATE_EXPIRED`, and the 20 slots per tick went to re-reading them.

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
