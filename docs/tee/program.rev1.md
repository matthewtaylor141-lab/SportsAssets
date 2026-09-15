# THE TO-A-TEE PROGRAM — mirroring RN1 on Polymarket US, built shadow-first

Synthesis of six lenses (coverage, shorts, sizing, timing, lifecycle, verification) and two refuters
per lens (engineering, market), all under scratchpad/tee/. Repo HEAD c2249ce (read-only; working
tree carries step 9's in-flight `workers/mirror_live.py`, `workers/all.py`, `tests/test_mirror_live_worker.py`
and `api/track_record.py` + its tests — none read, none touched by any phase below unless the phase is
marked `parallel_safe_with_step9=false`). Every number is a probe-log line, a file:line, or a probe
run in this session (`tee/*.out`, and the arithmetic probe in §A).

Abbreviations: `le` = backend/sportsassets/live_executor.py, `rules` = analytics/mirror_live_rules.py,
`mi` = analytics/mirror.py, `ms` = workers/mirror_shadow.py, `mr` = analytics/mirror_report.py,
`wx` = workers/whale_exits.py, `cs` = copy_sports.py, `probe` = scratchpad/probe_33686724064.txt
(21:52Z) unless another run id is named; `probe9` = probe_33690960366.txt (22:44Z, newest).
`spec` = scratchpad/p1_panel_synthesis.md, `add` = scratchpad/p1_addendum_p0_findings.md.

## 0. Where P1 stands against "to a tee" (the numbers that order the phases)

| fact | number | source |
|---|---|---|
| Shadow window: markets read / mapped | 334 / 63 = 18.9% mapped (81.1% unmapped) at 22:44Z; 309/56 at 21:52Z | probe9:1658; probe:1713 |
| Shadow would-fill (touch rate, TTL 600 s) | 1265/2578 = 0.4907, binomial 95% [0.4714, 0.5100]; 0.5296 at 21:52Z | probe9:1658; probe:1713; §A |
| Spec P0→P1 gate reads | would_fill ≥ 0.50: FAIL at 22:44Z; drift_p90 ≤ 0.05: FAIL (0.4531, 2253 > 5%); frozen_rows == 0: FAIL (302); tennis-ML unmapped ≤ 20%: not printed; ratio present: yes | spec:441; probe9:1658-1659 |
| RN1 snapshot | every probe carries a mapped row with one token `n/a` (fresh + partial signature, ms:444-449); zero rows carry a `0.0` (the complete-read signature); WHALEEXIT truncated_books=2 of 7 | probe:1542; probe9:1478; tee/lifecycle.refute.market.md:11-27; tee/verification.refute.engineering.md:29 |
| Consequence | admission `snapshot_stale` on every RN1 candidate (spec:177 `fresh = ... and not snap_partial`; rules:465-466) → P1 as specified opens no RN1 book | tee/lifecycle.md:11; tee/verification.md:337-341 |
| His side vs our long token | 18 of 33 mapped markets (55%) read his_net < 0; 41.9% of Σ|his_net| shares over 38 distinct mapped markets; 61.7% on the 21:52Z 12-row list | tee/shorts.md:9; tee/verification_refute_market.out:14; probe:1715-1726 |
| Ratio and cap | ratio = 50/25.55 = 1.957 → clamped 1.0 (mi:56,:145); weighted 50/2635.2 = 0.018974; $250 cap binds on 68.7-85.1% of his long dollars in the sample at r=1.9%; effective ratio on his $6.4k-$25.8k books 0.0097-0.039 | probe:1714; §A; tee/shape_probe.out2; tee/sizing.refute.engineering.md:14-22; tee/verification_refute_market.out:29-31 |
| His money | ≥ $250 lots = 86.8% of 30-d stake, the only bucket positive at 95% (+2.52% [+0.40%, +4.63%]); whale edge +2.07% [+0.07%, +4.08%] over 140,803 closed lots | probe:1686; probe:1924 |
| His pace | entries $44,881,024 since 08-01 over 32.9 d = $1,363,663/day; 130,084 merges, sells=$0.0; 19,813 markets/30 d ≈ 660/day; med_new 1263.5 assets/day, 95.5% dated yesterday..tomorrow | probe:1843; probe:1714; probe:1544 |
| MIRROR_DAY_USD=$1,250 is proportional only at r = 0.0917% | 1250 / 1,363,663 | rules:214; §A |
| Copy lane state | quarantine ON (LANEWHY chain 1376 `quarantined: mapping class unverified`; OSHAPE 7d 1411); QUARANTINE_RESUME_SRC = {premap, exact} — a 'ledger'-sourced mirror map is refused at P1 admission (spec:198-199) | probe:1735, :2067; le:2087 |
| Short wire | 373 sign-verified BUY_SHORT fills, 0 mismatch; SHORTTRUTH 50/50 ORDER_SIDE_SELL; 0 SELL_SHORT ever on the wire; post-only never sent on any intent | probe:1320, :1950-1951; tee/shorts.refute.market.md:31-35 |
| P&L proof horizon | sigma_per_dollar 1.302 (LANEEXEC chain ci95 [-0.1057, +0.0855], g=713) → 31,052 games for +2.07% at 95%/80% power; half-width ±46.6 pts at 30 games; 17 years at 5 books/day | probe:1703; §A; tee/verification_refute_market.out |
| Our RN1 per-fill copies | today 51W-57L +$57.51; yesterday 62W-83L -$972.86 | probe:1236, :1239 (per tee/verification.md:326) |

Reading: three things block any book on RN1 before sizing or sides matter — the snapshot (Phase 1),
the mapping source the quarantine admits (Phase 2), and the ratio that makes the $250 cap the only
sizer (Phase 3). Everything is measured in the shadow first (Phase 0 instruments), and every live
mechanic is proved with one share (Phase 7) before a default rides.

## 1. What was dropped, and why (both refuters killed it, or one killed it on evidence the other did not beat)

D1. shorts F4 "one book per side-slug; a negative target as BUY_SHORT on the pinned long slug / BUY_LONG on the sibling"
— killed by BOTH. The venue's atc- team contract shares ONE identifier between its long and short sides
with an explicit `long` bool (probe:1866), premap.side_intent reads it first (premap.py:2066-2068), so his
No token is BUY_SHORT on the SAME slug and `_choose_long` takes the standard branch (no per_side key;
tee/shorts.refute.engineering.md:20). "B-long is economically A-short" is false: every soccer game carries
a `-draw` contract (probe:1831-1832; 8 distinct -draw slugs across probes), P(not A) = P(B) + P(draw).
Replaced by Phase 6 (per-condition books, three-way owner decision).

D2. sizing F5 "the $50 clip scale is a gate wearing a multiplier's shape; drop it; D3 on the book cohort at 20 games"
— killed by BOTH. `mirror_target(0.1, 1000, 0.5, 25.0)` → ratio_eff 0.05 (test_mirror_live_rules.py:433-435):
the scale is a real multiplier below $50 and a downward-only lever (spec §5); 047:43 stores it; the P2 gate is
≥ 30 books AND ≥ 30 games (rules:174-180; spec:443), not 20. Replaced: keep gate + scale, feed it the bankroll
ratio (Phase 3b).

D3. timing §4(b) "his taker fill → one IOC at p+1c" — killed by BOTH. Contradicts rules:55-60 ("never
IOC-first"), buy_price's post-condition (rules:529-566) and test_take_property_never_pays_above_him; it IS
today's rn1 copy lane (tol 3c, le:2696/:2819-2822) and that lane reads flat/negative: IMPACT (0,1c] +0.12%
[-15.2%, +15.4%], (1c,2c] -1.05%, (2c,3c] -10.5% (probe:1646-1648); COPYD 51W-58L -$23.28 / 62W-83L -$972.86
(probe9:1129, :1132). Kept: the taker FLAG as a census column (Phase 8), no rule keys on it.

D4. timing §4(d) "12 replaces/h binds against his 15.3 cent-changes/h" — killed by BOTH. One book = one
long token: ~8/h < 12 (tee/timing.refute.engineering.md:22); the 15.3/h are both sides of a pair-capture
book P1 does not run (tee/timing.refute.market.md:67-68).

D5. lifecycle F3's change "when capped, mirror the FRACTION of his episode peak" — killed by BOTH. Peak by
fills never falls (mi:82-101), peak by snapshot is partial (Phase 1's fact); a 46% fills-over-read would sell
half a book; his ±3% oscillations become SELL/re-BUY every tick spending gross day room (rules:213-214) and the
replace budget. The MECHANISM (16/20 positive-target rows cap-bound at ratio 1.0; 4/20 at 0.019,
tee/lifecycle.refute.market.md:18-19) is real and is removed by Phase 3, not by a peak rule.

D6. lifecycle F5's change "allow the vanish (slippage) path when the snapshot reads both tokens 0 and
_confirm_gone is True even though fills say he holds" — killed by the market refuter on evidence the
engineering refuter's amendment does not beat: `markets.closed` lags the end of play (wx:626-633), so a
post-game redemption reads as gone on a row still marked live, and flatten_vanished is the ONE slippage path
(rules:839-850) → a $1 payout sold at the bid. Engineering's amended rule keeps "market live" as a clause,
which reads the lagging field. Kept: flatten_paired only for RN1; reduce_unfilled is a residual bounded by
the cap and reported, never by slippage.

D7. coverage F1 proposal (3) "move the mirror_mode dispatch below the mapping block so the ledger keeps
carrying maps" — killed by BOTH. The mapping block runs on the row INSERTed at le:8029 with status
'submitting'; leaving it would collide with the mirror's standing-row claim (045 one_inflight_per_asset,
`_MIRROR_ROW_INSERT_SQL` le:6137-6145 → 'asset_claimed') and fall into `_reap_stale_submitting`; the spec put
the dispatch above the INSERT for this reason (spec:304; test_mirror_live_handoff.py:169-172). The mirror owns
its mapping instead (Phase 2).

D8. coverage F3's verdict "most of RN1's tennis is not listed on the US venue" — killed by the market refuter;
engineering kept the grammar fact and made the key change conditional. 'listed'/'0ev' are the FUZZY lane's
top-5 title-search verdict over lifetime rows for all whales (pmus.py:1966-1980; app.py:6401-6417), the tennis
exact lane was dead before 08-26 (le:3488-3510), and the shadow itself maps aec-itfwo-* (probe:1725). The
grammar fact stands (slug keys can never meet, le:3470-3479); "not listed" becomes a MIRRORCOVER class that
requires exact-404 AND search-0 AND no board-index hit (Phase 0).

D9. sizing F3's change "round() instead of int(); MIN_MOVE_USD scaled by r" — killed by BOTH. round()
breaches the cap (250/0.99 = 252.53 → 253 × 0.99 = $250.47) and three test pins; Python round is half-to-even;
the r-scaled band is circular (263 = 5/0.019); the venue takes integer contracts (pmus.py:2212). Kept: the
dead band as a reported number (dead_band dollars, Phase 0/3a).

D10. verification F2's change "promote on the fidelity clauses instead of ci_lo > 0" — killed by the
engineering refuter on locked decisions D3/D6 and spec clause 5 ("reported, not gated"); the market refuter
added that capture ≥ 0.5 is unreachable under the cap unless the denominator is cap-scaled. Not a phase; an
owner decision with the 17-year number attached (§4, decision 3).

D11. shorts risk-note 5b "the pro-rata settlement split is right for the venue's single netted realized" —
killed by the market refuter (engine.py:399-416 allocate_venue_pnl carries no intent or sign; on a slug holding
a long row and a short row both get the NET position's sign: sums to the venue, wrong per row). Replaced by
own-figure settlement per leg in Phase 5.

D12. lifecycle F9's "hysteresis on the SIGN of his net before flattening" — killed by the engineering
refuter (contradicts the flatten's dead-band exemption, mi:237-244; no probe shows the replace budget
burning); the market refuter offered no counter-evidence for the hysteresis. Kept: the reopen-slot cost
(le:6121-6126; rules:470-474) → `opened_today` exemption for a reopen of the same (whale, slug) (Phase 4).

Amended, not dropped (carried into phases with the refuters' shape): coverage F1(1)(2) (mirror-owned
mapping with its own TTL + read cap, 'exact' label only), coverage F2 (phantom-line fix + identity rule,
source stays 'premap'), coverage F5/F6 (MIRRORCOVER with all candidates, source class, open/closed split,
gross dollars), shorts F1/F2/F3/F5/F6 (short_model_confirmed gate, parallel shadow column, sign-flip episode,
leg-space books, collateral-space sizing, take as BUY_SHORT IOC, exact-cent sell_price), sizing F1/F2/F4/F6/F7
(bankroll ratio via the 30-day open-cost query; cost-basis bankroll cap with reservation; MARKET_NET_CAP_USD
stays env-free; book count bounded by the read budget; snapshot as the sizing input), timing F1 (flag in the
reconciler only), F2 (non-legacy gate, would-P&L, time-to-touch, level_stale narrower than claimed), F5
(dollar-weighted fidelity; his_level_fill sibling), lifecycle F1/F2/F4/F6/F7/F8, verification F1/F3/F4/F5.

## 2. THE PHASES (ordered; each: files, the rule in prose, tests, the numeric gate; shadow before live)

### Phase 0 — INSTRUMENTS: the census every later gate reads (shadow only, zero money)
Files: backend/sportsassets/workers/mirror_shadow.py; backend/sportsassets/analytics/mirror_report.py;
backend/sportsassets/api/app.py (one read-only route `/api/admin/mirror-cover`); .github/workflows/engine-diagnostic.yml
(new job `mirror-cover`, lines MIRRORCOVER*, MIRRORUNMAP, MIRRORSRC, MIRRORFAM, MIRRORSIGN, MIRRORSHORT, MIRRORSNAP,
MIRRORWOULD); backend/tests/test_mirror_shadow.py; backend/tests/test_mirror_report.py; backend/tests/test_mirror_cover.py (new).
Depends on: nothing. parallel_safe_with_step9: TRUE (no worker, no rules signature).

Rule. The shadow keeps its long-only `target` (the value P1's `shadow_live_disagree` compares) and writes
beside it everything the gates cannot read today: on an UNMAPPED row `detail = {his_slug, title, event_title,
sport, family: market_type_of(his_slug), explain: premap.resolve_explain(...)['step'], notional_6h, outcome_null}`
(today the row carries only the two assets, ms:432-437, and the endpoint drops `detail`, mr:69-74); on a MAPPED
row `detail.map` (already there) plus `family, per_side, snap_age_s, snap_partial, fills_since_snap`; a PARALLEL
short reading `target_short / would_side_short / would_px_short / would_fill_short` computed with
`allow_short=True` and judged on the SELL side against the bid (never the live-compared target, else
`p2_verdict` fails on `shadow_live_disagree` by construction — tee/shorts.refute.engineering.md:22); the judge
UPDATE appends `touched_s` and the BBO depth at the touch into `detail` (a new 046 column breaks
`test_migration_046_columns_match_the_insert_and_the_report_select`, so JSONB only). `summarize` gains: `latest`
with `detail`; unmapped by family and by usd; mapped by SOURCE (ledger / premap / exact, and for ledger the row's
own mapping class) so the share P1 can admit under the quarantine is a number; would-fill split into
NON-LEGACY plans (exclude reason LIKE 'short side not admitted%' and every plan against a negative or legacy
ledger) and legacy; a would-P&L settle for would_fill=true plans `pnl_would = (payout − would_px) × qty` from
market_tokens × resolved_prices (engine.py:361-376 shape) with a game-clustered interval; the snapshot census
(RN1 rows fresh+partial, per-token n/a, age). The runner job `mirror-cover` is coverage.md §5 with the
refuters' amendments: build the board index from `events.list` (the sweep's own rung, premap.py:2280-2283),
test ALL candidates (6 tennis + 2 us-slug + his slug + aec-/atc- event slug — `cands[:3]` would misclass ITF
men, tee/coverage.refute.market.md:39), classify `mapped_premap | mapped_exact | mapped_ledger:<class> |
listed_on_us_but_unmapped:<step> | listed_closed | not_listed_on_us` (only exact-404 AND search-0 AND no index
key hit) `| undiagnosed | undated | null_condition`, dollars from `usdcSize` (usd24h) and GROSS (long + other)
at marks with the paired share printed, plus an "admissible under the current quarantine" total beside "mapped".
Tests: detail keys on unmapped and mapped rows; `latest` carries detail; family/sign/source splits on a fixture;
non-legacy filter excludes the three legacy shapes; would-P&L on a fixture with resolved prices; the parallel
short columns leave `target` byte-identical; 046 column test unchanged; `test_the_shadow_never_touches_an_order`
green; route carries `dependencies=` (AST test); every jq line parses on an empty endpoint.
Gate (24 h, ≥ 30 mapped markets): MIRRORCOVER-TOTAL prints with `undiagnosed` ≤ 10% of usd; MIRRORSRC prints
ledger/premap/exact counts; MIRRORSNAP prints RN1 fresh-complete share; MIRRORREAD prints the non-legacy
would-fill with a clustered interval; MIRRORSHORT prints would_fill_short over ≥ 30 markets; MIRRORWOULD prints
a game-clustered would-P&L interval. (Readability gates: the thresholds these feed are in Phases 1-6.)

### Phase 1 — THE POSITION SOURCE: a per-market venue read, drift on the net
Files: backend/sportsassets/workers/whale_exits.py (new `market_positions(http, address, cid)` beside
`_confirm_gone`, reusing the `/positions?user&market=<cid>&sizeThreshold=0` call at wx:495-497);
backend/sportsassets/workers/mirror_shadow.py; backend/sportsassets/analytics/mirror_live_rules.py (new
`drift_net_rule` beside the pinned `drift_rule`; admission's `snapshot_stale` clause accepting a per-market
fresh-complete read); backend/sportsassets/analytics/mirror_report.py; backend/tests/test_whale_exits*.py;
backend/tests/test_mirror_shadow.py; backend/tests/test_mirror_live_rules.py.
Depends on: Phase 0 (MIRRORSNAP), owner decision 8 (raise WHALE_EXIT_POS_MAX first).
parallel_safe_with_step9: FALSE (the worker's step B must read `market_positions` instead of `snapshot_sizes`
— one call site, applied by step 9's owner after this lands; rules gain a function the worker calls).

Rule. His position on a booked market is read from the venue's per-market positions endpoint — one paced
call per book per tick (≤ 5 books), both tokens of the condition in one page, stamped fresh AND complete for
THAT market — because the whole-book walk of a 10k+-row whale is partial on every probe (§0) and the spec
makes a complete snapshot the position source (add:13-17; spec:177). The whole-book walk stays the drift
reference and the vanish confirmation; fills stay the trigger (wake) and the price (his level). Drift is
computed on the NET, `|(his_long − his_other) − (snap_long − snap_other)| / max(...)`, so an equal-leg merge
he closes on-chain is drift 0 instead of a lifelong `drift` lock-out (rules:1087-1095 `drift_of` uses
`_size`, which refuses negatives — hence a NEW function, the pinned one untouched). `snap_partial=True` is
still passed unchanged to `select_flatten` so the vanish path stays strict (tee/verification.refute.market.md:37-39).
The shadow prints `target_fills` and `target_snap` side by side with snapshot age and fills-since-snapshot so
the 73% tometc gap (36,082 fills vs 20,823 net, probe:1724) separates into ingest miss vs lag (a book built at
1,098 sh/min lags ≤ 5,489 sh in 300 s, tee/refute_market_arith.out:6).
Tests: `market_positions` returns both tokens and marks complete; a 4xx/None → None (fail closed);
`drift_net_rule` equal-leg merge → 0.0, one-sided add → the per-token number; admission with a per-market
fresh read admits, with the whole-book partial only → `snapshot_stale`; the shadow writes both targets.
Gate (24 h): MIRRORSNAP RN1 per-market fresh-complete share ≥ 0.95 of planned markets (today 0: every probe
partial); net drift p90 ≤ 0.05 (today whole-book drift_p90 0.4531, 2253 of 3912 reads > 5%, probe9:1658);
admission `snapshot_stale` on RN1 candidates < 5% of ticks.

### Phase 2 — COVERAGE THE MIRROR OWNS (premap fixes + the exact lane inside map_market)
Files: backend/sportsassets/workers/premap.py; backend/sportsassets/copy_sports.py; backend/sportsassets/workers/mirror_shadow.py;
backend/sportsassets/live_executor.py (a two-line re-export of the moved grammar so `from live_executor import
_tennis_candidates` and underdog.py:378 keep working); backend/tests/test_premap*.py; backend/tests/test_copy_sports*.py;
backend/tests/test_mirror_shadow.py; backend/tests/test_tennis_league_gate.py.
Depends on: Phase 0 (MIRRORCOVER says which class is ours). parallel_safe_with_step9: TRUE (`map_market(pool,
fills)` keeps its two-arg form; the venue lane is a `pmus=None` kwarg the worker may pass later).

Rule. (a) `premap.match_side` computes his lines from `_lines_of(_QDATE_RE.sub(' ', his_title))` — the ISO
date in "Will NEOM SC win on 2026-09-03?" currently yields phantom lines ['03','09'] (premap.py:84-86,
:1833-1834) that `_yn_line_ok` refuses at :1876-1877; reproduced by both refuters (tee/coverage.refute.market.md:7-12).
(b) A yes/no IDENTITY branch: admit a per-team row only when `identifier == 'atc-' + his_slug` byte-for-byte,
the row's question fullmatches `pmus._YN_Q_PATTERNS` with `_yn_date_ok`, and `_bridge_title_subject(his_title,
his_slug)` names the subject; Yes → the yes row (LONG), No → the no row (SHORT); no opponent witness needed
(the slug identity pins league, both teams, date and side); source stays 'premap' (the class the quarantine
admits, le:2087). (c) `map_market(pool, fills, pmus=None)`: after premap and only when `pmus` is given, run
`pmus.resolve_market_exact` over ALL candidates (`_tennis_candidates + _us_slug_candidates + [his slug]`), each
read paced, behind a TTL INSIDE map_market keyed (whale, condition) and a per-tick budget
`MIRROR_MAP_READS_PER_TICK=12` (20 unmapped × up to 18 reads × 0.35 s = 126 s > POLL_S 30 s otherwise); label
'exact' (in QUARANTINE_RESUME_SRC); never `resolve_team_yesno_exact` (its class 'yesno_exact' certifies under its
own key, le:2094, and is refused at admission today). (d) The ledger source filters `AND status <> 'rejected'
AND COALESCE(error,'') NOT LIKE 'quarantined%'` (us_market_slug is written at le:8378 BEFORE the quarantine
refusal at :8415, so a fuzzy, refused mapping is a trusted source today — a wrong-market vector) and carries
the row's mapping class. (e) `_abbrev_player/_tennis_candidates/_us_slug_candidates` move to `copy_sports`
(pure) so mirror, copy lane and runner import one grammar. (f) `market_type_of` returns 'prop' when the suffix
names a segment (first/half/1h/2h/fh/sh/quarter/q1-q4/period/set) before the total/spread tests — a 1H total
typed as the game total (cs:322-324) is a wrong-market hazard. (g) `his_fills` passes
`COALESCE(t.outcome, mt.outcome)` (chain rows are inserted with outcome NULL and enriched later,
pipeline.py:142-145; `match_side` returns None on '' at premap.py:1827-1829).
Tests: the NEOM fixture resolves yes/LONG and no/SHORT; a '-kha' slug resolves only the -kha identifier; a
different date refuses; a titled line still refuses an unlined row; `map_market(pool, fills)` (no pmus) makes
zero venue calls (existing `_Pmus` fakes have no `retrieve_by_slug`); reads per tick ≤ 12; TTL honoured; a
'rejected'/'quarantined' ledger row is not a source; `inspect.getsource(_tennis_candidates)` pins in
test_tennis_league_gate.py:66-76 stay green through the re-export; `market_type_of('...-first-half-total-0pt5')
== 'prop'`.
Gate (shadow, 24 h): MIRRORCOVER-CELL tennis|moneyline `listed_unmapped / (mapped + listed_unmapped)` ≤ 20% by
markets AND by usd (spec:441; add:33-37; today unprinted, all-family unmapped 81.1% by count); MIRRORSRC: mapped
rows with source ∈ {premap, exact} ≥ 90% (today: the 8 atc- soccer slugs in the sample are ledger-sourced,
tee/coverage.refute.engineering.md:48, i.e. refused at P1 admission); soccer per-team markets appear as
mapped_premap in MIRRORCOVER.

### Phase 3a — THE BANKROLL RATIO, measured in the shadow
Files: backend/sportsassets/analytics/mirror.py; backend/sportsassets/workers/mirror_shadow.py;
backend/sportsassets/analytics/mirror_report.py; .github/workflows/engine-diagnostic.yml (MIRRORRATIO prints
`ratio_bankroll`, `deployed_usd_30d`; MIRRORSHAPE line); backend/tests/test_mirror.py; backend/tests/test_mirror_shadow.py.
Depends on: Phase 0 (dollar instrument), owner decision 1 (the bankroll). parallel_safe_with_step9: TRUE.

Rule. `compute_ratio` gains a second query: his 30-day OPEN COST on unresolved markets (Σ per condition of
BUY notional − SELL notional, floored at 0, JOIN markets resolved=false) → `deployed_usd_30d`; it overstates
him (his SELLs are 64 of 856,392 fills, probe:1988; merges invisible) so r comes out too SMALL, never too large.
`mi.mirror_ratio` emits `ratio_bankroll = min(RATIO_MAX, MIRROR_BANKROLL_USD / deployed_usd_30d)` beside the
median and weighted ratios, None when unreadable or under MIN_MARKETS (fail closed). The shadow plans under
`MIRROR_SHADOW_RATIO_MODE ∈ {median, weighted, bankroll}` written per row, so the CAP-BOUND share of his dollars,
the dead-band dollars (`MIN_MOVE_USD=5` drops his markets under $5/r: $500 at r=1%, $263 at 1.9%, §A) and the
per-market shape ratio `(target × mark)/(r × his_net × mark)` are read per mode from the DB, not from probe
excerpts (every headline percentage moved when one probe landed, tee/sizing.refute.engineering.md:12-17).
`int()` stays (floor when capped, D9). The sizing input is the Phase 1 per-market net.
Tests: ratio_bankroll = B/D and None on D ≤ 0 or None; MODE selection writes the mode per row; the shape
statistic on a fixture; `test_mirror.py:52-55` int pins unchanged.
Gate (shadow, MODE=bankroll, 24 h, ≥ 30 admitted markets): shape ratio within [0.9, 1.1] on ≥ 95% of his
admitted dollars (today 0.34x-52.63x at r=1.9%, tee/shape_probe.out2); cap-bound share of his dollars ≤ 5%
(today 68.7-85.1%); dead-band share ≤ 2%; `deployed_usd_30d` readable on every hourly refresh; ratio drift
`max|r_new/r_old − 1|` < 10% across refreshes.

### Phase 3b — THE BANKROLL RATIO, live rules (cost-basis bankroll cap, reservation, read-budget books)
Files: backend/sportsassets/analytics/mirror_live_rules.py; backend/sportsassets/live_executor.py (a mirror
reservation lock/counter copied from the rest lane's `_REST_LOCK/_REST_RESERVED_USD` pattern, le:7067-7085);
backend/tests/test_mirror_live_rules.py; backend/tests/test_mirror_live_ledger.py.
Depends on: Phase 3a gate, owner decision 1. parallel_safe_with_step9: FALSE (`mirror_target` gains
`bankroll_room_usd`; the worker passes it).

Rule. `MIRROR_BANKROLL_USD = capped_env(...)` (downward-only) with a MEASURE and a PROMOTED value (the
roster_rules MEASURE/PROMOTED shape, roster_rules.py:48-49). `ratio_eff = ratio_bankroll × min(1, clip/50)`
— the clip scale stays as the downward lever (D2). The per-market cap passed by the rules layer is
`cap_usd = min(MIRROR_NET_CAP_USD, bankroll_room)` where `bankroll_room = MIRROR_BANKROLL_USD − Σ_live_books
(ledger_net × avg_cost) − reserved` on COST basis (no per-book mark read; peak_exposure_usd is already the
stake, 047:62); `MARKET_NET_CAP_USD` stays the env-free analytics constant (rules imports it; test :76 forbids
restating it) and `capped_env`'s positive floor stays (a zero cap never means uncapped). Placements reserve
under a mirror lock so concurrent lanes cannot exceed the room by one clip each (add:70-72). `MIRROR_DAY_USD`
stays a downward handle reported beside the proportional figure r × his entries/day ($13,642/day at r=1%, §A).
`MIRROR_MAX_LIVE_BOOKS` is bounded by the READ budget — books × 2 paced reads + new-market reads ≤ ~86 per
30 s tick shared with the copy lane (rules:218-222) — and the census prints `read_budget_used`. A book's ratio
stays fixed at open (add:62-65); `ratio_drift` degrades the heartbeat above 10%.
Tests: `bankroll_cap` refusal by name; room on cost basis; reservation released on refusal and on terminal;
`test_caps_carry_the_spec_defaults` pins updated; a demoted clip still zeroes increases (`clip_zero`).
Gate (live, after Phase 7 rungs 1-4,7-8): per-book shape ratio within [0.9, 1.1] on ≥ 95% of his dollars on
the books held; `bankroll_cap` never binds while Σ books < bankroll (0 false refusals); day-cap binds at the
proportional figure, not at $1,250 (today $1,250 binds after 4.8% of his day at r=1.9%, 9.2% at r=1%).

### Phase 4 — LIFECYCLE FIXES a LONG book needs (wake, reopen, ranking, level freshness, fidelity instrument)
Files: backend/sportsassets/live_executor.py (`_mirror_notify` for a mirrored whale issued ABOVE the exit
dispatch's `return _copy_stop("was_an_exit_pending")` at le:7639 — a wake, no money decision; the hand-off pins
test_mirror_live_handoff.py:117-125 pin only the gate block); backend/sportsassets/analytics/mirror_live_rules.py
(`level_stale`; `opened_today` reopen exemption); backend/sportsassets/workers/mirror_shadow.py (`his_level_fill()`
sibling returning (ts, price, trade_id); candidate ranking by his dollars); backend/migrations/048_mirror_fidelity.sql
(mirror_orders.trigger_trade_id, his_fill_ts, first_fill_at; trades.taker BOOLEAN NULL for Phase 8);
backend/sportsassets/api/app.py (read-only `MIRRORFIDELITY` payload); .github/workflows/engine-diagnostic.yml;
backend/tests/test_mirror_live_rules.py; backend/tests/test_mirror_shadow.py; backend/tests/test_mirror_live_migration.py
(048 is an ALTER; 047 stays CREATE-only per test :128-132); backend/tests/test_mirror_live_handoff.py.
Depends on: Phase 1 (snapshot shares for ranking), Phase 0. parallel_safe_with_step9: FALSE (rules the worker
calls; le hand-off region; the worker writes the 048 columns).

Rule. (i) WAKE: classify_exit reads his sibling holding from HIS fills (le:800-812), so on the mirror's own
two-sided books every reducing fill is an exit → `mx_mirror_owns_market` → `was_an_exit_pending` above the
hand-off (le:7639 vs :7661): reductions never wake the reconciler today (COPYCENSUS was_an_exit_pending|rn1: 282,
probe:1561). For a mirrored whale the wake fires before the exit dispatch; the dispatch itself is unchanged.
(ii) LEVEL FRESHNESS: a rest is placed or kept only while the triggering fill is ≤ `MIRROR_LEVEL_MAX_AGE_S=600`
old OR his level is within 1c of the venue bid; otherwise refuse by name `level_stale` and hold under target —
`his_level` has no age bound (ms:379-408) and 7 of 22 BUY plans, carrying 41.9% of his dollars on those rows,
rest 0.5c-16c UNDER the bid (tee/refute_market_rest.out:3-13); `side_band` covers only 2 of them and is not
re-checked on increases (rules:455 returns before :463). (iii) RANKING: new candidates ranked by his dollars
(Phase 1 snapshot shares × his VWAP, or opening-burst dollars), not recency — 20 of ~420 in-window markets are
read per tick (MIRRORHB skipped_markets 381-430; 4.8%), and the count is in $25 markets while the money is in
≥ $2.6k bursts (probe:1714). Dollars-at-the-mark ranking is NOT used (≈450 BBO reads/tick, 157 s > POLL_S).
(iv) REOPEN: a reopen of the same (whale, slug) inside the day is exempt from `opened_today` and counted in
`flat_reopens` (047:47). (v) FIDELITY: `mirror_orders` carries `trigger_trade_id, his_fill_ts, first_fill_at`;
`MIRRORFIDELITY rn1 24h: his_fills=N | reacted=R react_p50/p90 | filled_le_his=F partial=P unfilled=X late=L
missed=Z | fidelity=F/N by_usd=$F/$N` — dollar-weighted fill FRACTION (not "≥ 1 share"); `at ≤ his price` is
true by construction and carries no information (rules:543-549).
Tests: the wake is issued for a mirrored whale on the exit path and never for another whale; `level_stale`
truth table incl. the 1c exception; increase re-check includes `level_stale`; reopen exemption; ranking on a
fixture; `his_level()` float contract unchanged (test:330-338) with `his_level_fill()` beside it; 048 ALTER
pins; MIRRORFIDELITY SQL on a fixture.
Gate (shadow first): plans resting > 1c under the bid with a trigger older than 600 s = 0 (today 7/22); the 20
markets read per tick carry ≥ 80% of his 6 h gross dollars (today: recency order). Live (after Phase 7):
reduction wake-to-plan p50 ≤ 10 s on his complement buys; MIRRORFIDELITY by_usd ≥ 0.5 on booked long tokens
inside 600 s (honest threshold: queue position is not ours to control, §3).

### Phase 5 — SHORTS (P2): books in leg space, his other token as BUY_SHORT on the same slug
Files: backend/sportsassets/analytics/mirror_live_rules.py; backend/sportsassets/live_executor.py
(`_open_mirror_book`, `_book_mirror_buy/_sell`, `OpenOrder.intent` from `pmus._norm_order`, the short_model gate);
backend/migrations/049_mirror_shorts.sql (mirror_books.intent per book; mirror_orders.intent TEXT with all four
wire intents; kind/side CHECKs widened or a `leg` column; avg_cost documented as LEG cost);
backend/sportsassets/analytics/engine.py (short book own figure at settlement); backend/sportsassets/workers/whale_exits.py
(`_confirm_gone` on the token carrying his net); backend/sportsassets/workers/mirror_shadow.py (`his_level`
sign-aware: reads his SELL of the other token for a short-book reduce); backend/tests/test_mirror_live_rules.py;
backend/tests/test_mirror_live_ledger.py; backend/tests/test_venue_settle.py; backend/tests/test_mirror_shadow.py;
backend/tests/test_mirror_live_migration.py.
Depends on: Phase 1, Phase 3b, Phase 7 rung 5 (short rest read-back), Phase 0's MIRRORSHORT gate, owner
decisions 2 (D3 for the P2 gate), 5 (legacy BUY_SHORT rows). parallel_safe_with_step9: FALSE.

Rule. A book carries an intent (BUY_LONG or BUY_SHORT) fixed at open, chosen by the sign of the target;
`mirror_target` outputs it and `short_side_refused` stays the name while the P2 gate is shut. A live book
whose target changes sign flattens and, once flat with no open order, closes its episode IMMEDIATELY with
reason `sign_flip` (today the only live close is flat-for-3600 s, rules:246, under `mirror_books_one_open_per_market`
047:72-73 — the Nakashima shape would wait an hour or never). `BookState` lives in LEG space: `leg_shares =
|ledger_net|`, `avg_cost` = the leg's contract cost (1 − fill_price on a short, le.fill_cash :3092), realized by
the short formula (le.realized_pnl :3021: (entry − exit) × n); a reduce past the leg is an overfill freeze as
today; `_state_nums` admits the negative ledger of a short leg (today None → `bad_state`, so a short book could
never close, tee/shorts.refute.engineering.md:23). Wire: a short INCREASE rests `submit_fok(slug,
sell_price(round(1−q, 6), ask), qty, intent=BUY_SHORT, post_only=True, good_till)` — `sell_wire(1−q) ==
rest_tick(wire_limit(q, BUY_SHORT))` over 70,099 ladder points (tee/refute_probe2), but `sell_price` steps UP one
cent on 19 of 98 exact cents from float noise at rules:587-588 (also P1's own SELL_LONG reductions) — fixed by
rounding the equivalent to 6 decimals; its TAKE is an IOC with `intent=BUY_SHORT, sell=False` (never P1's
SELL_LONG-shaped take: `_exit_intent` pmus.py:2202-2209 would sell a side we do not hold). A short REDUCE is
`close_position` when sole holder (pmus.py:2300-2359, sign-agnostic) and otherwise refused `short_reduce_unproven`
until rung 5 reads a resting SELL_SHORT's price/side/intent back. Sizing and caps on a short leg use
`(1 − wire) × qty` (room_scale divides by the wire, rules:632: 51 shares per $50 at 0.97 vs 1000 at 0.03;
wire_usd le:6303 likewise); `gross_buy_usd += (1 − px) × q` on the opening sell so `episode_close_reason` reads
cashed_out. `keep_or_replace` compares `OpenOrder.intent` (a resting BUY_SHORT reads side 'BUY', intent BUY_SHORT
in `_norm_order` :2394-2409) and fingerprints by (intent, cent); the lost-order search expects trade side SELL
on the short leg (`_lost_fill_is_ours` already does). Drift and `_confirm_gone` key on the token that carries
his net (his_other for a short). `wrong_sign_trip = sign(venue) ≠ sign(ledger)` while both non-zero.
Settlement: a short book's own figure `realized + remainder × ((1 − payout) − avg_cost_short)`, and any other-
sign row on the slug is a named `book_settle_disagree` (D11). Every short branch refuses `short_model_disarmed`
when `le.short_model_confirmed()` is False (LIVE_SHORT_COST_MODEL=off silently inverts wire and P&L,
tee/shorts.refute.engineering.md:26); the preview guard bounds nothing on a short (pmus.py:2233, :2254 compare
proceeds, not pay) so the mirror bounds pay = (1 − wire) × qty itself. A short book opens only under
`_short_gate` (le:5705-5733), `LIVE_ALLOW_SHORT=on`, and the P2 gate.
Tests: sign_flip close; leg-space booking (buy/sell/overfill) on both legs; `_state_nums` on a short; wire
equality at every exact cent; take intent on a short; `short_reduce_unproven`; collateral-space room_scale;
settlement own figure on a short and the mixed-sign disagreement; `OpenOrder.intent` compare; wrong-sign both
directions; `short_model_disarmed`; the never-add prior fires on `raw.preview.intent = BUY_SHORT` rows.
Gate: shadow (Phase 0 columns, 7 d): would_fill_short clustered lower bound ≥ 0.50 over ≥ 30 markets; rung 5
lines read from the venue; live: ≥ 30 closed short books with `at_or_better` = 1.0 on the short leg (fill
≥ 1 − q), `wrong_sign_trip` = 0, `book_settle_disagree` = 0, `overfill` = 0; short-side dollar coverage ≥ 0.95
of his negative-net dollars on mapped admitted markets (today 0; 55% of his mapped markets, 41.9% of Σ|his_net|
shares).

### Phase 6 — SOCCER: per-condition books on three-way games
Files: backend/sportsassets/analytics/mirror_live_rules.py (admission: per-CONDITION claim; the two-longs
shape keeps `per_side_unsupported` with long_asset/us_slug pinned at open); backend/sportsassets/live_executor.py
(the one-per-game read at le:8645-8660 and the never-add prior le:8609-8630: a Draw book is not "the game
already copied" for the mirror's own sibling conditions; still refuses any per-fill copy on the game);
backend/migrations/050_mirror_game_claims.sql (per-condition claim replacing the per-game claim for lane='mirror';
a game-level cap column); backend/sportsassets/workers/mirror_shadow.py (`_choose_long` pins the long token at
book open; per-side flag printed); backend/tests/test_mirror_live_rules.py; backend/tests/test_mirror_live_le_consumers.py;
backend/tests/test_mirror_live_migration.py; backend/tests/test_mirror_shadow.py.
Depends on: Phase 5 (his No = BUY_SHORT on the same slug), owner decision 4. parallel_safe_with_step9: FALSE.

Rule. An atc- team contract is one condition whose two sides share the identifier: his Yes is BUY_LONG and
his No is BUY_SHORT on that slug (premap.py:2066-2068; probe:1866; proven live on four atc- slugs booked
ORDER_SIDE_SELL, probe:1956-1961); a sibling-slug expression is never used. A three-outcome game may carry up
to three books (A win / B win / draw), each its own condition, under one GAME-LEVEL cap Σ(book cost) ≤ r × his
game dollars; the referee claims per condition for mirror books while the per-fill lane stays refused per game.
Without this his soccer structure (several MIRRORMKT rows per game_key: bur/mid/draw → 'eflch-bur-mid-2026-09-02')
is unmirrorable by construction (tee/shorts.refute.market.md:15).
Tests: three books on one game_key admitted for the mirror, a fourth refused; a per-fill copy on the game
still refused by name; game cap breach refused `game_cap`; the two-longs shape refused `per_side_unsupported`
with the long pinned; `_us_game_key` equality pinned for -bur/-mid/-draw.
Gate: shadow: his soccer games with > 1 mapped condition read a target on every condition (today: 8 -draw slugs
seen, none targeted); live: ≥ 30 closed soccer condition-books, `book_settle_disagree` = 0, `game_cap` breaches = 0.

### Phase 7 — VENUE 1-SHARE PROBES and the commission read (the rollout rungs, one share each)
Files: .github/workflows/engine-diagnostic.yml (a `mirror-probe` step printing the MIRRORPROBE lines);
backend/sportsassets/pmus.py (`take_arms` input shape: a crossing post-only may return 200 + ORDER_STATE_REJECTED
with EXECUTION_TYPE_REJECTED instead of HTTP 400 — SDK types/orders.py:31,:40; `submit_fok` then returns
status 'rejected' WITH an order_id, pmus.py:2286-2303; read `commissionNotionalCollected` / `commissionSpreadPx`
into the execution record — probe:2043-2044 carry the keys, `grep -rn commission backend/sportsassets/` = 0);
backend/sportsassets/analytics/mirror_live_rules.py (`take_arms` accepts both shapes); backend/tests/test_pmus_post_only.py;
backend/tests/test_mirror_live_rules.py; scratchpad runbook (spec §7.12 shape).
Depends on: step 9 (worker) and step 10 (endpoints) landed. parallel_safe_with_step9: TRUE for the files
listed (additive), the rungs RUN after step 9.

Rule. No default rides before its rung prints its line from the venue (never from our own row), all under
`MIRROR_MAX_LIVE_BOOKS=1, MIRROR_NET_CAP_USD=25`: (1) post-only — a 1-share BUY at the ask comes back
`post_only_rejected` (400 OR 200+REJECTED, both accepted), a 1-share BUY at bid−1c returns an id with
executions=[] and an open state (the flag cannot be read back: SDK `Order` has no `participateDontInitiate`);
(2) GTD — `goodTillTime` read RAW and a terminal state within TTL+60 s; (3) cancel read-back within 3 reads;
(4) a partial fill booked once (`venue cumQuantity == mirror_orders.booked_filled == standing filled_shares`);
(5) SHORT — one post-only BUY_SHORT rest read back; hold a 1-share short and rest one SELL_SHORT at an off-market
price, read `order_status().price/side/intent`, cancel; send one BUY_LONG against the negative net to learn
whether it nets (never sent before; `_exit_intent` chose SELL_SHORT deliberately); (6) per-side — `_us_game_key`
equality and a 1-share position reading on its own slug only; (7) settlement to the cent — own figure vs venue
realized gap ≤ $0.05 (`MIRRORGRADE` prints both, stake = peak_exposure_usd); (8) reaper isolation live
(`reaper_touched_mirror = 0`); (9) wrong-sign trip (`mirror_live=false` with the receipt); (10) commission —
every mirror execution's commission fields logged and the settled figure reconciled net of them.
Tests: both post-only refusal shapes → the refusal dict and `take_arms` True; commission fields parsed; the
probe step's jq parses on absent lines.
Gate: every MIRRORPROBE line prints as specified (tee/verification.md §3 table); rung 7 gap ≤ $0.05; commission
non-null on 100% of mirror executions over the probe window.

### Phase 8 — THE TAKER FLAG (measurement only; no rule keys on it)
Files: backend/sportsassets/ingestion/reconciler.py (one extra `/trades?user=&takerOnly=true` page per hourly
walk, intersected by dedupe_key — NOT in the poller: data_api_max_rps=6.0 is shared, config.py:53);
backend/migrations/048_mirror_fidelity.sql (trades.taker, shared with Phase 4); backend/sportsassets/api/app.py
(TAKERSHARE line by count and by dollars; MIRRORFIDELITY split maker_fid/taker_fid); .github/workflows/engine-diagnostic.yml;
backend/tests/test_reconciler*.py.
Depends on: Phase 4 (048). parallel_safe_with_step9: TRUE.

Rule. `trades.taker` is a census column (NULL = unknown); the census reports `match_rate = matched / true_page_rows`
and refuses the reading under 0.9 (if takerOnly=true aggregates a taker order over N makers the sizes differ and
every row silently reads NULL). Nowhere is stored today (001_init.sql:56-77; chain.py:388-389 drops the topics);
the only reading is 2 of 66 by count on one match (rn1_match.txt:10) and by DOLLARS it is anywhere in
[~0, 68%] — four same-second ascending-cent clusters carry $24,847 of $36,279 (tee/timing.refute.market.md:10-16).
Tests: intersection by dedupe_key; NULL under the floor; the poll cycle length unchanged (no poller change).
Gate: TAKERSHARE rn1 30 d prints by count and by dollars with match_rate ≥ 0.9.

### Phase 9 — THE SHADOW-vs-LIVE CROSS-CHECK and the grade line's proof horizon
Files: backend/tests/test_mirror_live_e2e.py (new: `mirror_shadow.tick_once` and the live tick on ONE fake
pool/venue, asserting `shadow_live_disagree == 0`); backend/sportsassets/analytics/mirror_grade.py (step 10's
module: prints `proof.assess`'s `n_needed_at_target / n_still_needed / half-width` beside the interval,
proof.py:230-241); .github/workflows/engine-diagnostic.yml (MIRRORGRADE).
Depends on: step 9, step 10. parallel_safe_with_step9: FALSE (imports the worker).

Rule. The live book does what the shadow measured, proved by one test that runs both against the same fake;
and the grade line prints the proof horizon so D3's interval is never mistaken for a fidelity reading
(at sigma 1.302 a +2.07% edge needs 31,052 games; a 30-game interval is ±46.6 pts; 17 years at 5 books/day, §A).
Gate: `shadow_live_disagree` = 0 over 24 h live; MIRRORGRADE prints `n_needed_at_target`.

## 3. FIDELITY METRICS — definition, source, today, target

| # | metric | definition | source | today | target |
|---|---|---|---|---|---|
| M1 | Dollar coverage (admitted families) | Σ his GROSS dollars (long + other at marks) on markets where a live book holds ≥ 50% of target ÷ Σ over ALL his markets in the window, by family and by sign; paired share printed | MIRRORCOVER-TOTAL / mirror_shadow detail (Phase 0) | count-only: 18.9% mapped (probe9:1658); P1-admissible ledger 0 (tee/lifecycle.md:11) | ≥ 0.95 of his dollars in admitted families |
| M2 | Tennis-ML listed-unmapped share | listed_on_us_but_unmapped ÷ (mapped + listed_on_us_but_unmapped), tennis moneyline, by markets and usd | MIRRORCOVER-CELL | not printed (all-family unmapped 81.1%) | ≤ 20% both ways (spec:441) |
| M3 | Admissible mapping share | mapped rows with source ∈ {premap, exact} ÷ mapped | MIRRORSRC | unprinted; the sample's 8 atc- slugs are ledger-sourced | ≥ 90% |
| M4 | Snapshot usability | share of planned RN1 markets with a fresh (≤ 300 s) COMPLETE per-market read | MIRRORSNAP | 0 (partial every probe; truncated_books=2/7, probe:1542) | ≥ 0.95 |
| M5 | Net drift | \|(his_long − his_other) − (snap_long − snap_other)\| / max(...), p90 | MIRROR line | 0.4531 per-token whole-book (probe9:1658) | ≤ 0.05 (rules:248) |
| M6 | Position tracking error | per (book, tick) \|ledger_net − target\| / max(target, 1), p50/p90, excluding frozen and dead-band ticks | shadow rows beside the book (spec 1e) | none (no books) | p90 ≤ 0.05, p50 ≤ 0.02; floor = the 2% dead band (mi:64-66) |
| M7 | Shape ratio | per book (target × mark)/(r × his_net × mark) | MIRRORSHAPE (Phase 3a) | 0.34x-52.63x at r=1.9% (tee/shape_probe.out2) | within [0.9, 1.1] on ≥ 95% of his admitted dollars; cap-bound dollars ≤ 5% (today 68.7-85.1%) |
| M8 | Would-fill (touch rate), non-legacy | would_fill=true ÷ resolved over plans not against a legacy/negative ledger, clustered by market | MIRRORREAD (Phase 0) | 0.4907 [0.4714, 0.5100] all plans (probe9:1658) | clustered lower bound ≥ 0.50 over ≥ 30 markets (spec:441) |
| M9 | Would-P&L | Σ (payout − would_px) × qty on would_fill=true plans, game-clustered interval | MIRRORWOULD | none | reported; lower bound > 0 is the shadow's economic reading |
| M10 | Live rest fill rate | filled_rest ÷ placed_rest; at_or_better; maker_share | /api/admin/mirror census (step 10) | none | ≥ 0.40 (the shadow's 0.49 minus a measured queue haircut); at_or_better = 1.00; maker_share ≥ 0.5 (spec:443) |
| M11 | Fill fidelity per HIS fill | dollar-weighted fraction of his BUY fills on booked long tokens matched by our fill inside 600 s; react_p50/p90 | MIRRORFIDELITY (Phase 4) | none | by_usd ≥ 0.5; react_p50 ≤ 10 s chain lane (LANEEXEC send_p50 1.662 s, probe:1703) |
| M12 | Stale-level plans | plans resting > 1c under the bid with a trigger older than 600 s | MIRRORMKT / census `level_stale` | 7 of 22 BUY plans, 41.9% of their dollars (tee/refute_market_rest.out) | 0 |
| M13 | Short-side coverage | Σ his negative-net dollars carried by a short book ÷ Σ his negative-net dollars on mapped admitted markets | MIRRORSIGN / MIRRORSHORT | 0 (55% of mapped markets negative; 41.9% of Σ\|his_net\|) | ≥ 0.95 after Phase 5 |
| M14 | Reduce fill share | planned reductions filled within one TTL ÷ planned reductions | census `reduce_unfilled` complement | reported only (spec §6) | ≥ 0.5, gated in P2 |
| M15 | Integrity | wrong_sign_trip, order_lost, overfill, reaper_touched_mirror, book_settle_disagree, shadow_live_disagree; frozen ticks | rules:270-271 | none | all 0; frozen < 1% (rules:268) |
| M16 | Settlement to the cent | \|own figure − venue realized allocation\| per book | MIRRORGRADE | none | ≤ $0.05 |
| M17 | Commission | commission fields present on mirror executions; settled figure net of them | Phase 7 | 0 readers in code | 100% present |
| M18 | P&L capture | our_book_pnl ÷ (r × his book pnl on the fraction we could hold), pooled | MIRRORGRADE (spec:437) | none | ≥ 0.5 reported, never gated; denominator cap-scaled |
| M19 | Book cohort interval (D3) | ci95 lower bound of ROI on peak_exposure_usd, game-clustered | MIRRORGRADE with n_needed | none | > 0 (locked D3); 31,052 games at his edge (§A) |
| M20 | Taker share | his taker fills by count and dollars, 30 d | TAKERSHARE (Phase 8) | 2/66 by count on one match; dollars ∈ [~0, 68%] | reported with match_rate ≥ 0.9 |

## 4. OWNER DECISIONS (only he can make these; each with a recommendation and the number)

1. THE RATIO r AND THE BANKROLL IT IMPLIES. r = MIRROR_BANKROLL_USD / his 30-day open cost (Phase 3a's query).
   The only readable proxy today is MERGEPNL open=$25,086,278 (probe:1843), which INCLUDES resolved balances
   (merge_pnl.py:370-372) so it overstates the denominator. At r=1% (the number he floated, add:24-25) that is a
   $250,863 sleeve and $13,642/day of gross entries; at r=2% $501,726 and $27,283/day; at r=0.1% $25,086 and
   $1,364/day (§A). Dollar reach: r=2% mirrors every $50+ market at any price (≥ 96.7% of his stake — proven,
   probe:1686 buckets); r=1% likely ≥ 95%; at r=0.1% the $5 dead band drops his markets under $5,000 and one share
   at p=0.5 needs his$ ≥ $500. Today's MIRROR_DAY_USD=$1,250 is proportional only at r=0.0917%. RECOMMENDATION:
   name the sleeve he will fund (MIRROR_BANKROLL_USD); let r follow from the measured denominator; read
   MIRRORRATIO ratio_bankroll and MIRRORSHAPE for 24 h in the shadow before any book; keep the $250 per-market
   cap ONLY as the rollout probe's handle, because with it the effective ratio on his $6.4k-$25.8k books is
   0.0097-0.039 whatever r is (tee/verification_refute_market.out:29-31).
2. WHETHER THE $50 CLIP COHORT STILL GOVERNS PROMOTION (D1/D3). The clip scale stays as a downward lever
   (D2); the mirror's proof cohort is the BOOK at r (stake = peak_exposure_usd, 047:62; proof.cohort_assess
   excludes lane='mirror'). RECOMMENDATION: D1 stays for the per-fill sleeve; the mirror promotes by the book
   cohort's interval with MIRROR_BANKROLL_USD carrying a MEASURE and a PROMOTED value; D3 unchanged.
3. D3's PROOF HORIZON. At the per-fill lane's dispersion (sigma 1.302, probe:1703) ci95 lower bound > 0 at 30
   books needs ~47% ROI; his +2.07% needs 31,052 games = 17 years at 5 books/day; RN1's own interval cleared
   zero by +0.07% on 140,803 lots (probe:1924). RECOMMENDATION: keep D3 as locked (promote trigger and demote
   trigger), prove "to a tee" on the fidelity metrics M1-M17, print the P&L interval with n_needed on every
   MIRRORGRADE line, and accept that the P&L clause is a multi-year instrument unless the book count rises
   (bounded by the read budget, ~86 paced reads per 30 s tick shared with the copy lane).
4. THREE-WAY SOCCER. Up to three books per game (A / B / draw) under one game-level cap, or one book per game
   (which drops his other two conditions). Numbers: every mapped atc- game carries a -draw slug (8 distinct);
   atc- = 8/33 mapped slugs (24%); soccer refused rows 7 d epl 1228, lal 800, sea 550 (probe:2080-2094).
   RECOMMENDATION: per-condition books under one game cap (Phase 6), after shorts (Phase 5).
5. SHORTS SEQUENCING AND THE 14 LEGACY BUY_SHORT ROWS. His other-token side is 55% of his mapped markets and
   41.9% of Σ|his_net| shares; the P2 gate sits behind D3; 14 of 33 mapped slugs carry a legacy per-fill
   BUY_SHORT of ours (e.g. benshe-hubhur −604 vs venue +3,458; tometc-jacfea −169 vs his +36,082; tee/shorts.md:60).
   RECOMMENDATION: measure shorts in the shadow now (Phase 0 parallel column), open no short book before rung 5
   and D3; let the per-fill exit lane close the 14 legacy rows first (admission `venue_already_holds` refuses
   them anyway, rules:459-460) rather than adopting them into books.
6. THE SWITCH-ON DAY. 36 of 51 mapped rows carry a per-fill copy row today (legacy_row refusal); the 409
   precondition on `/mirror-live/on` is advisory (spec §3.6). RECOMMENDATION: wait for his legacy rows to
   settle; admit a slug only once its legacy row is cashed_out/settled.
7. QUEUE PRIORITY (D-new). Posting at p+1c jumps the whole level but costs 2% of stake on a 50c contract
   against his +2.07% edge and makes at_or_better false by construction. RECOMMENDATION: no; never by default;
   report queue depth (Phase 0) and the live fill rate (M10) instead.
8. RN1'S POSITIONS WALK. WHALE_EXIT_POS_MAX=24,000 (wx:90, env); truncated_books=2 of 7 on every beat
   (probe:1542; probe9:1478); RN1 at 10k+ rows (add:94-98). RECOMMENDATION: raise the env now and read
   MIRRORSNAP; if the walk still cannot complete, Phase 1's per-market read is the position source (it is
   the proven call: partial_vanished_confirmed=2).
9. THE RESIDUALS HE MUST ACCEPT (§5) — each is a number he signs off on, not a bug.

## 5. WHAT "TO A TEE" CANNOT MEAN — the honest residuals, with numbers

| residual | number | source |
|---|---|---|
| Queue position | our rest queues behind PMUS depth at his cent (top-of-book 20-616 sh vs plans of 397-2,272 sh); the shadow's 0.49 is a touch rate, an upper bound w.r.t. queue; every TTL replace (600 s) resets priority | probe:1860-1874; rules:729-737; tee/timing.refute.market.md:39 |
| Venue listing gaps | whatever MIRRORCOVER books as `not_listed_on_us` after exact-404 + search-0 + no index hit; today 81.1% unmapped by count with no split | probe9:1658 |
| His pair-capture economics | 42.8% of the shares he bought since 08-01 are merged pair legs (39.8M of 93.1M); 130,084 merges, sells=$0.0; his +2.07% is a closed-lot blend the long-only net mirror cannot earn; the 24,423 residual on the one read book is "leftover inventory" | probe:1843; tee/refute_market_arith.out:1; rn1_match.txt (per tee/lifecycle.refute.market.md:97) |
| Latency | chain lane send_p50 1.66 s / p90 9.17 s; poll lane ~281 s for ~15% of his fills (chain 7861/9236 today); the mirror adds POLL_S 30 s without a wake, ≤ ~10 s of paced reads with one | probe:1703; probe:1548; chain.py:462-475 |
| Maker adverse selection | a rest fills when the ask comes DOWN to his level; PATHCURVE +0.62c at 30 s [-0.40, +1.65], every offset "contains zero" today | probe:1652-1657 |
| Half-cent grid | 12-16% of quoted BBO values sit on x.xx5; buy_wire floors to the cent, so we rest half a tick behind the bid there | tee/refute_market_arith.out:7; rules:482-490; pmus.py:208-209 |
| Dead band | 2% of the cap ($5) and 2% of target: tracking error never reads below it | mi:64-66 |
| Reductions may not fill | reduce_unfilled has no bound but the cap and the vanish path, which is unreachable for RN1 (fills never see merges; 64 sells ever) | rules:878-882; probe:1988 |
| Commissions | unread in code (0 hits) until Phase 7 | probe:2043-2044 |
| Post-only semantics | cannot be read back (SDK Order has no participateDontInitiate); proven by behaviour only | polymarket_us/types/orders.py:70-92 |
| Tick throughput | 20 of ~420 in-window markets per tick (4.8%); plans on unread markets never resolve | probe9:1673; ms:59, :532-533 |
| His taker share by dollars | unknown, [~0, 68%] until Phase 8 | tee/timing.refute.market.md:10-16 |
| Sign churn on a two-sided quoter | 4 of 19 twice-read slugs flipped sign inside an hour; two swung > 10x (phi-az 780 → 30,567 sh in 52 min) | tee/refute_market_rest.out:15-22 |
| Sizing shape under the $250 cap | until Phase 3 the effective ratio on his big books is 0.0097-0.039 whatever r is | tee/verification_refute_market.out:29-31 |
| P&L proof | ±46.6 ROI points at 30 games; 31,052 games for +2.07% | §A |
| Book count | bounded by the venue read budget (~86 paced reads per 30 s tick, shared), not by the bankroll | rules:218-222; ms:58 |

## A. Arithmetic probe run in this session (PYTHONPATH=backend, mirror.py + proof.py, pure)

```
ratio clamp 50/25.55 = 1.957 -> min(RATIO_MAX,...)= 1.0
weighted 50/2635.2 = 0.018974
MIRROR_DAY_USD 1250 / his entries per day 44881024.17/32.9 = 0.0916 %
r=2.0%: bankroll on MERGEPNL open $25,086,278 = $501,726; gross entries/day = $27,283; cap $250 binds when his$ > $12,500; MIN_MOVE $5 drops his$ < $250
r=1.0%: bankroll on MERGEPNL open $25,086,278 = $250,863; gross entries/day = $13,642; cap $250 binds when his$ > $25,000; MIN_MOVE $5 drops his$ < $500
r=0.5%: bankroll on MERGEPNL open $25,086,278 = $125,431; gross entries/day = $6,821; cap $250 binds when his$ > $50,000; MIN_MOVE $5 drops his$ < $1,000
r=0.1%: bankroll on MERGEPNL open $25,086,278 = $25,086; gross entries/day = $1,364; cap $250 binds when his$ > $250,000; MIN_MOVE $5 drops his$ < $5,000
required_n edge 0.0207 = 31052 ; 0.0104 = 123017 ; 0.05 = 5323 ; 0.1 = 1331
half-width at 30 games = 0.466 ; 100 = 0.255 ; 1000 = 0.081
years at 5 books/day for 31061 games = 17.0
target_shares(1.0, 39295, 0.9875, cap 250) = {'target': 253, 'capped': True}   # aec-mlb-atl-wsh, his $38,804
target_shares(0.01, 39295, 0.9875, cap 250) = {'target': 253, 'capped': True}  # r does nothing while the cap binds
target_shares(1.0,-10038.5,0.49,allow_short=True) = {'target': -490, 'capped': True}
binomial 1265/2578 ci: [0.4714, 0.51]
```
