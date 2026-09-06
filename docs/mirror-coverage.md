# COVERAGE LENS — why 81% of RN1's markets read "unmapped" in the P0 shadow, what they are, what maps them

Read-only review of commit cedbae0 (HEAD) against probe logs 33670859137 (19:23Z) … 33686724064 (22:32Z, latest).
All paths relative to /home/user/SportsAssets/backend/sportsassets unless noted. Probe = scratchpad/probe_33686724064.txt unless noted.
Sandbox facts that bound this report: the venue gateway is NOT reachable from here (`curl https://api.polymarket.us` → `CONNECT tunnel failed, response 403`), the DB is not reachable, and the WBLOB_rn1 activity blobs in every probe file are masker-corrupted (gunzip yields 0.6–23 KB of a 4.8 MB file; the workflow says so itself at .github/workflows/engine-diagnostic.yml:3568 "unmasked — the log blobs are not"); the engine-export artifact (id 9869152566, 6.17 MB) exists on GitHub but its Azure blob host is denied by the egress proxy. So DOLLAR numbers below are denominators from the logs; the numerator needs the MIRRORCOVER step in §5.

## 0. Headline numbers (all from logs)

| reading | value | source |
|---|---|---|
| P0 window 24h: markets read / mapped | 309 / 56 = 18.1% mapped, 81.9% unmapped | probe:1713 `MIRROR rows=5577 markets=309 mapped=56` |
| Series of the same reading across runs | 28/144 (19.4%) 19:23Z; 44/284 (15.5%) 20:34Z; 51/303 (16.8%) 21:16Z; 54/305 (17.7%) 21:44Z; 56/309 (18.1%) 22:32Z | `grep "MIRROR rows=" probe_*.txt` |
| Latest tick | markets=20 unmapped=1 skipped_unmapped=7 (TTL cache working) | probe:1728 MIRRORHB |
| Per-tick cap | MAX_MARKETS_PER_TICK=20, `skipped_markets` 381→430 per tick until the abandon tick | workers/mirror_shadow.py:59; MIRRORHB lines |
| RN1 new positions/day, and how many carry a slug date inside yesterday..tomorrow | med_new=1263.5, med_playable=1206.5 (95.5%) | probe:1544 WHALERATE; api/app.py:7132-7135,7173-7178 |
| RN1 notional 2026-09-02 (UTC day, RN1 only) | $2,400,134.97 on 9,236 BUY rows, 1,255 distinct assets | probe:1548 WRDAY (endpoint `/api/admin/whale-rate?whale=rn1`, yml:916) |
| Where his dollars sit | lots ≥ $250 = 86.8% of his stake (30d) | probe:1686 SIZEEDGE |
| Copy-sweep candidate pool by game date | past=971 future=0 undated=0 today_tomorrow=1249 | probe:1590 SWEEPMIX; workers/copy_sweep.py:302-319 |
| Premap sweep completeness | full: events=1694 rows=82670 pages=17/120 window=[12,96] complete; fast: 554 ev, 12558 rows, 6/25 pages complete | probe:1393-1394 PREMAPSWEEP |
| Copy-lane refusals 30d attributed to `unmapped` | refused=45715, 65.15% of all refusals | probe:1759 GATEEDGE |
| Copy-lane unmapped census (48h, 400 sampled, ALL whales) | type_prefix_filter_emptied 175 (43.8%), no_side_match 83 (20.8%), resolves 77 (19.3%), no_key_intersection 65 (16.3%) | probe:1797-1800 UNMAPSTEP |
| League-alias probe | 0 of 400 would be found by dropping the league token | probe:1795 UNMAPALIAS |
| Named-tennis bridge (dark) | would_resolve=0 (not_named 33, wrong_type 21, outcome_thin 1) | probe:1805 NAMEDML |
| RN1 refused rows by league, 7d (proxy for his mix) | itf 2453, mlb 2066, atp 1820, epl 1228, lal 800, wta 603, nfl 556, sea 550 | probe:2080-2094 WL |
| Venue-listed vs not, per league (all whales, lifetime rows) | atp listed=54 0ev=814 x404=25; itf 195/1476/606; wta 11/451/12; mlb 3707/100/50; epl 161/149/76 | probe:2118-2135 LGE |

Reading: tennis (itf+atp+wta = 4,876 of RN1's 10,076 league-attributed refused rows in 7d = 48%) is the biggest slice and the copy lane's own diagnosis says the venue mostly does NOT list it (0ev ≫ listed); MLB is the opposite (listed ≫ 0ev → our mapper's miss, and LGE says mlb rows are moneyline 5419 / total 4879 / spread 1861, so a large part is derivatives P1 would refuse anyway). Soccer per-team yes/no (epl, lal, sea, eflch…) is listed by the venue and refused by premap for wording (§2.f).

## 1. The exact mapping path and every way it returns None

### 1.0 Before mapping: which markets are even considered
- `active_conditions` (mirror_shadow.py:155-167): `WHERE lower(w.username)=$1 AND t.condition_id IS NOT NULL AND t.ts >= now() - 6h`, newest first. A trade whose `condition_id` is NULL (chain leg not yet enriched, or a token Gamma never knows: ingestion/pipeline.py:314-318 `no metadata for token … will enrich on refresh`) is INVISIBLE to the shadow — not counted unmapped, not counted at all. Size today: probe:2104 `NOSLUG rows=17215 catalog_has_token=2 token_unknown=17213` lifetime, but `LGE (no slug): 17215 (7d 1)` → negligible in the last week.
- `tick_once` (mirror_shadow.py:644-651): the per-tick cap (20) takes the NEWEST markets; a market cached in `_unmapped_until` (15 min, :73-74, set at :660-662) is skipped and counted `skipped_unmapped`. The 309 markets in the window are therefore the newest ~309 of his 6h activity, not a random sample; `skipped_markets` (381-430 per tick) never got read in the window at all.

### 1.1 `map_market(pool, fills)` (mirror_shadow.py:249-298) — returns None at
| # | line | condition | class |
|---|---|---|---|
| N1 | :256-258 | no `asset` on any fill | impossible in practice |
| N2 | :264-277 | live_orders rows on his tokens exist (`us_market_slug IS NOT NULL`, intent LONG/SHORT, newest 20) but `_choose_long` returns None | ledger-ambiguous |
| N3 | :280-282 | `from . import premap` raises | deploy fault |
| N4 | :288-297 | every per-token `premap.resolve(...)` returned None or lacked `market_slug`/`intent` | **premap miss (the 81%)** |
| N5 | :298 | premap answered but `_choose_long` returns None | premap-ambiguous |

The ledger query (:264-271) is the copy sleeve's own rows: `us_market_slug` is written only after a mapping succeeded (live_executor.py:8378-8381), on every path — premap, exact, yes/no exact, AND fuzzy. It does not read `mapping_src`; the copy lane itself trades only `QUARANTINE_RESUME_SRC = {"premap","exact"}` under quarantine (live_executor.py:2087). So the mirror's ledger source can hand it a fuzzy mapping the copy lane refused to trade (risk, not coverage).

Note the context passed to premap: `by_asset` takes the FIRST fill per token (:284-287) and passes `market_title, event_title, outcome, market_slug` — `outcome` is `t.outcome` only (no `COALESCE(t.outcome, mt.outcome)` as api_unmapped_census does at api/app.py:4913); `_norm(None)` → '' → `match_side` returns None (premap.py:1827-1829).

### 1.2 `_choose_long` (mirror_shadow.py:217-246) — None at
- :232-234 two BUY_LONG candidates on the SAME slug (ambiguous); :246 no LONG and no SHORT candidate.
- per_side=True (:235-237) only when both tokens resolve BUY_LONG on DIFFERENT slugs. Evidence that the venue's soccer per-team yes/no markets are NOT this shape: probe:1809-1838 NAMEDML-Q rows show side "yes" and side "no" both with identifier `atc-spl-neo-kha-2026-09-03-neo`; premap.side_intent (premap.py:2066-2073) names them LONG/SHORT from `long`/`marketSideType`. So a mapped soccer "Will X win?" condition yields {long=Yes token, other=No token}, per_side False — P1's `per_side_unsupported` (analytics/mirror_live_rules.py:436-437) does not bite there.

### 1.3 `his_fills` join (mirror_shadow.py:170-189)
`COALESCE(t.market_title, m.title)`, `m.event_title` (markets table only), `COALESCE(t.market_slug, m.slug)`, `t.outcome`. `trades.market_slug` is Gamma's market slug written at enrichment (ingestion/pipeline.py:323-334 `market_slug=$6` ← `meta["slug"]`; gamma.py:84-86). Whale-side shapes seen in the data-api rows salvaged from the corrupted blob (tee/w_rn1_probe_33670859137.txt.json, 26 rows): `elc-qpr-car-2026-09-02-car` title "Will Cardiff City FC win on 2026-09-02?" outcomes Yes/No; `mlb-sd-cin-2026-09-02-total-9pt5` "San Diego Padres vs. Cincinnati Reds: O/U 9.5"; tennis per tests/test_mirror_shadow.py:25-27 `atp-nakashi-michels-2026-09-02` title "US Open ATP: Brandon Nakashima vs Alex Michelsen", outcomes = player names.

### 1.4 `premap.resolve` (premap.py:2906-3026) — None at, in order
| step | line | trigger | RN1 shapes that hit it |
|---|---|---|---|
| R1 no date on his slug | :2938-2940 | `date_of(global_slug)==''` | slugless trades; undated event-level markets ("us-open-2026-winner" → probe run below: date='' keys=[]); TYP unknown 19428 lifetime (probe:2112) |
| R2 no keys | :2949-2950 | titles/slug yield nothing | rare |
| R3 no key intersection | :2960-2961 | `event_keys && $1` empty | venue does not list the game inside now-12h..now+96h, or key grammar: slug keys can NEVER meet on tennis (whale `atp-nakashi-michels-2026-09-02` vs venue `aec-atp-branak-alemic-2026-09-02`, live_executor.py:3471-3479), so tennis rides on TITLE keys only (`brandon nakashima vs alex michelsen@2026-09-02`, run below) which need the venue event title to carry both full names AND the same date. League aliasing measured 0/400 (probe:1795). |
| R4 unknown market type | :2969-2971 | `PREFIX_FOR_TYPE.get(market_type_of(slug))` None | exact_score (`elc-mot-dun-2026-09-02-es-3-0` → exact_score, want=None, run below), prop, unknown |
| R5 type prefix emptied | :2979-2981 | event captured, no row of that family | 43.8% of the copy-lane sample; e.g. `itc-udi-ven-2026-09-02-first-half-total-0pt5` → typed `total` (copy_sports.py:322-324 returns total before the >4-char guard) → "6 rows on this event, none with a ['tsc'] prefix" (probe:1801) |
| R6 no side match | :2982,:3009-3010 | `match_side` None (named lane dark: PREMAP_NAMED_LANE off, :2984-2985) | §2.f: soccer per-team yes/no wording + the phantom-line bug; tennis name spelling/ambiguity |
| R7 side has no intent | :3016-3021 | sweep could not name LONG/SHORT | GATEEDGE no_side_intent 480 rows/30d (copy lane) |
| R0 query failed | :2958-2959 | table absent/degraded | deploy fault |

### 1.5 The unmapped TTL cache
`_unmapped_until[(whale, cid)] = now + 900` (mirror_shadow.py:660-662) whenever `reason` startswith "unmapped"; checked at :649-651. Never invalidated by a premap fast-lane write (180 s cadence, premap.py:50-55) or a new ledger row — worst-case 15 min lag, acceptable. It is process-local (a restart forgets it) and unbounded in size (one entry per (whale, cid); ~1,300 assets/day → fine).

THE TERMINAL MEMO (D1, 2026-09-06, live worker only): `mirror_live._terminal_until[(whale, cid)] = now + ms.UNMAPPED_TTL_S` whenever a CANDIDATE's quote read (`_tick_candidate` → `_read_market` → `_bbo`) carried a state in `ms.STATE_TERMINAL` (`MARKET_STATE_EXPIRED`, `CLOSED`, `TERMINATED`, `MATCH_AND_CLOSE_AUCTION` — a market that has ended, a per-market fact); the candidate walk skips such a market under census `cand_terminal_skipped` until the TTL runs. Why: the walk spends its `MAX_MARKETS_PER_TICK` (20) reads newest-touched first, and his newest-touched mapped markets are matches he trades to settlement — the 19:02Z census read `venue_halted` on 26 of 29 candidate reads, every one `MARKET_STATE_EXPIRED`, so an open market behind them never reached a slot (25 expired candidates ahead of 1 open one: the first tick reads 20 expired as before, the second tick skips them and reads the open one; `tests/test_d1_fills_dedup.py`). NEVER written for `HALTED` / `SUSPENDED` / `PREOPEN` (those reopen, and keep counting toward the miss streak), NEVER from an existing book's read (`_tick_book` does not touch it: a book on an ended market is managed every tick until it closes), and never for a read that failed or named no state. Same TTL, same process-local, unbounded shape as `_unmapped_until`. `cand_terminal_skipped` is appended LAST in `CENSUS_KEYS` (past the served 40-key prefix) and does not ride on `integ`, which is at its 39-key ceiling: read it off the raw heartbeat row. The shadow's own walk keeps re-reading terminal markets (it measures; not changed here).

### 1.6 What the copy lane has that the mirror does not
live_executor.py:8184-8341, in order: premap.resolve → `_tennis_candidates(title, slug) + _us_slug_candidates(slug, outcome)` through `pmus.resolve_market_exact` (moneyline) / `resolve_derivative_exact` (spread,total) → his own slug verbatim (:8294-8301) → `resolve_team_yesno_exact` (:8309-8320) → `resolve_market` fuzzy (:8334-8341). The mirror runs ONLY premap.resolve (Postgres) + the ledger (mirror_shadow.py docstring :12-15 "no venue call"). Consequence: the mirror's coverage on tennis and soccer is bounded by (a) premap title keys and (b) whatever the COPY LANE already mapped into live_orders. Once RN1 is in mirror mode the copy lane stops at dispatch (`if mirror_mode(username): … return _copy_stop("mirror_mode", username)`, scratchpad/p1_panel_synthesis.md:300-302, and :304 "ABOVE every entry gate, sizing, the INSERT and both submit sites") and source (b) dries up for every NEW market — P1's own unmapped share will be HIGHER than the shadow's 81%, not lower, unless §4 lands first.

Sandbox proof of the candidate grammar (PYTHONPATH=backend, no DB, import in 0.2 s):
```
_tennis_candidates('US Open ATP: Brandon Nakashima vs. Alex Michelsen','atp-nakashi-michels-2026-09-02')
 -> ['aec-atp-branak-alemic-2026-09-02', 'aec-atp-alemic-branak-2026-09-02']
_us_slug_candidates('elc-qpr-car-2026-09-02-car','Yes') -> ['aec-elc-qpr-car-2026-09-02', 'elc-qpr-car-2026-09-02-car']
_us_slug_candidates('mlb-sd-cin-2026-09-02','San Diego Padres') -> ['aec-mlb-sd-cin-2026-09-02', 'mlb-sd-cin-2026-09-02']
```
and of premap's key construction on RN1 shapes (same run):
```
atp-nakashi-michels-2026-09-02  type=moneyline want={'aec','atc'} keys=['alex michelsen vs brandon nakashima@2026-09-02','atp-nakashi-michels-2026-09-02','brandon nakashima vs alex michelsen@2026-09-02','michelsen vs nakashima@2026-09-02','nakashima vs michelsen@2026-09-02']
elc-qpr-car-2026-09-02-car      type=moneyline keys=['cardiff city vs qpr@…','elc-qpr-car-2026-09-02','qpr vs cardiff city@…','will cardiff city fc win on 2026 09 02@2026-09-02',…]
mlb-sd-cin-2026-09-02-total-9pt5 type=total want={'tsc'}
itc-udi-ven-2026-09-02-first-half-total-0pt5 type=total (a 1H total typed as the game total)
elc-mot-dun-2026-09-02-es-3-0   type=exact_score want=None  -> R4
us-open-2026-winner             date='' -> R1
```

## 2. What the unmapped markets ARE

The shadow itself cannot say: an unmapped row carries `long_asset/other_asset` only (mirror_shadow.py:432-437) and the MIRRORMKT line prints `condition_id[:16]` (yml:1195); `summarize.latest` (analytics/mirror_report.py:69-74) drops `detail` (map source, per_side) and there is no title/slug/sport/notional column. The `latest 12` sort also under-represents unmapped markets: they are written once per 15 min (TTL) while mapped ones are rewritten every tick, so 11 of the 12 printed rows are mapped tennis/MLB and one is `0x2a43b8656d565a … unmapped` (probe:1715-1726). Classification therefore comes from the copy lane's own funnel on the same whale, same feed:

a. **Tennis singles moneylines the venue does not list** — RN1 7d: itf 2453, atp 1820, wta 603 refused rows; LGE says 0ev (search found no event) dominates listed by 15:1 (atp 814:54), 7.6:1 (itf 1476:195), 41:1 (wta 451:11). Exact-lane 404 trails: probe:1433 `unmapped: exact[aec-wta-taytow-taypre-2026-09-02 6x404,1xyn] slug:NotFoundError; event:20/20; search[…]:0ev`. Game-level, moneyline, plausibly NOT listed (ITF/challenger depth). This is the family P1 admits (MIRROR_FAMILIES={'moneyline'}, mirror_live_rules.py:255) and where RN1's shadow-mapped markets already sit (probe:1715-1726: aec-atp/wta/itfwo).
b. **Tennis the venue DOES list but premap misses** — key grammar (R3: slug keys never meet; title keys need the venue's event title = "First Last vs First Last" on the same date — venue shape evidence tests/test_pmus.py:285 `"Dalma Galfi vs Ella Seidel"`, tests/test_memory_census.py:45 `"Harry Wendelken vs Stefano Travaglia"` with `aec-atp-harwen-stetra-2026-08-24`), name spelling (R6: apostrophes, surname collisions), and DATE DRIFT between the two venues' slug dates for late-night ET matches (unmeasured — open question). The copy lane maps these via `_tennis_candidates` (first3+last3 grammar, live_executor.py:3453-3540) — the exact path the mirror lacks. The named premap lane that would do it network-free is dark and measured would_resolve=0 (probe:1805).
c. **Soccer per-team "Will X win on DATE?" (atc- family)** — RN1 7d: epl 1228, lal 800, sea 550 (+eflch, ecu1, scp, atbl, spl seen in MAPA lines probe:1428-1431, 1450-1456 as MAPPED by the copy lane → via `resolve_team_yesno_exact`/fuzzy, not premap). premap refuses them twice over (§2.f). Game-level, moneyline, venue LISTS them (NAMEDML-Q shows the rows in us_premap). This class becomes P1-relevant the moment the copy lane stops (§1.6).
d. **MLB/soccer totals, spreads, first-half totals, exact scores, "leading at half"** — R4/R5 (43.8% of the copy-lane sample; probe:1435 Motherwell exact score, :1440 Udinese 2-2, :1444 "Burnley FC leading at half" all `search …:0ev`). Outside MIRROR_FAMILIES anyway; some are not listed at all (derivative depth), some are listed but typed wrong (1H total → `total`).
e. **Event-level / undated** (tournament winners, futures) — R1; TYP unknown 19,428 lifetime rows across whales; no window can catch an undated slug. Not measured for RN1's last 24h; the probe classifies them.
f. **The soccer yes/no premap refusal, proved in the sandbox** (`PYTHONPATH=backend python`, premap + pmus only):
   - his title `Will NEOM SC win on 2026-09-03?` → `_lines_of` = ['03','09'] (the `-09`/`-03` of the ISO date match `_LINE_CTX` `[+-]\s*(\d+)`, premap.py:84-86), slug_lines=∅.
   - venue question (probe:1809) → `_question_line` = '' (dates stripped, premap.py:2118), `_questions_agree(norm(his), norm(venue))` = False (premap.py:351-355 needs equality/containment; "Will NEOM SC win on 2026-09-03?" vs "Will NEOM SC win against Al Khaleej Saudi Club in the Saudi Pro League match scheduled for Sep 3, 2026?").
   - `match_side(rows,'Yes',his_title,slug)` = None. With a DATELESS title it resolves; with an AGREEING question but the dated title it STILL returns None because `_yn_line_ok` refuses `bool(rl) != bool(his_lines)` (premap.py:1876-1877) on the phantom lines. Two independent blockers; the second is a bug (the whale side never strips dates the way the venue side does at :2118). probe:1804 UNMAPEG shows it live: `spl-neo-kha-2026-09-03-neo outcome=Yes … his_lines=['03','09']`.

Venue plausibility by class: (a) not listed (copy-lane search 0ev, exact 404) — cannot be mapped by any code; (b),(c) listed — mappable; (d) mostly not listed and out of family; (e) unknown.

Does RN1 trade "5 days out"? No evidence he does: 95.5% of his new assets carry a slug date within yesterday..tomorrow (WHALERATE med_playable/med_new = 1206.5/1263.5), the copy-sweep pool has future=0 (probe:1590), and the premap board walk is COMPLETE at 17 of 120 pages over now-12h..now+96h (probe:1393; premap.py:2241-2245, 2457-2469). Widening the board window is not a lever. The `past=971` bucket (game date before today) is in-play/finished games still in the sweep's retry pool; the sweep's back_h=12 covers in-play (premap.py:2241) and PRUNE_HOURS=26 (premap.py:65) ages the rest.

## 3. Share of his DOLLARS in the unmapped set

Not computable from any artifact this sandbox can read (see header). What exists:
- Denominator: RN1 BUY notional 2026-09-02 = $2,400,134.97 (probe:1548; RN1-only per api/app.py:7110,7180 `sum(t.notional)`), 1,255 distinct assets; 12-day medians 11,360 trades / 1,328.5 assets per day (probe:1544).
- Concentration: 86.8% of his 30d stake is in lots ≥ $250 (probe:1686), i.e. the dollar answer is decided by a few hundred markets/day, not by the count — a count-based 81% can be anything in dollars.
- The mirror knows his_net only for MAPPED markets (mirror_shadow.py:438-441); unmapped rows carry no size. `mirror_shadow` has no notional column (migrations/046_mirror_shadow.sql:33-62).
- The copy lane's gate-edge cohort DOES carry dollars per gate (`roi_with_ci` returns `staked`, analytics/proof.py:114-117; `admin_gate_edge` api/app.py:5635-5646) but the probe prints only refused/share/roi (yml:1215): `GATEEDGE unmapped: refused=45715 share=0.6515` — its `.gates.unmapped.staked` vs `.taken_at_his_price.staked` is a one-line jq away (copy-lane rows scored at HIS price, 30d, whale-filterable with `?whale=rn1`).
- The MIRRORCOVER step (§5) computes the true number from the data-api (`usdcSize` per fill, field confirmed in the salvaged rows) joined to the shadow's map/unmap verdict.

## 4. Per failure class: the code change that maps it, or the proof it cannot be mapped

| class | mappable? | change |
|---|---|---|
| (a) tennis not listed on US | No — venue gap (search 0ev + exact 404 on both player orders and all tour codes) | none; the probe must PROVE the gap per market (§5) so the 81% splits into "cannot" and "did not" |
| (b) tennis listed, premap miss | Yes | 1) `map_market`: after premap, run the copy lane's exact lane — `_tennis_candidates + _us_slug_candidates + [his slug]` through `pmus.resolve_market_exact`, paced via `venue_pace.pace(READ_PACING_S)` and behind `_unmapped_until` so a miss costs ≤ 4-6 reads per 15 min per market; source label "exact" so P1's `mapping_ok` reads the same class the copy lane trades under quarantine (`QUARANTINE_RESUME_SRC`). 2) Move `_abbrev_player/_tennis_candidates/_us_slug_candidates` (live_executor.py:3448-3577) into a pure module (copy_sports.py) so the mirror, the copy lane and the runner import ONE grammar. 3) premap side: at sweep time also emit event keys from the SIDE DESCRIPTIONS ("<a> vs <b>@date" both orders from `marketSides[].description`, premap.py:2149-2165) so a venue event whose title is not "A vs B" still meets the whale's title key; flip the named lane only after its audit reads zero-mismatch (its own gate, premap.py:2984-2999). |
| (c) soccer per-team yes/no | Yes | 1) bug fix: `match_side` computes `his_lines` from `_lines_of(_QDATE_RE.sub(" ", his_title))` (mirror the venue-side stripping at premap.py:2118) — the sandbox run shows an agreeing question then resolves. 2) wording: widen `_questions_agree` for the yes/no branch by the closed template the executor already validates (`resolve_team_yesno_exact` W-gates, pmus.py:1218-1258: his slug `<lg>-<a>-<b>-<date>-<t>` + "Will <team> win" subject) — or, cheaper and already-reviewed, call `pmus.resolve_team_yesno_exact` from `map_market` on the moneyline+`Yes/No` shape exactly as live_executor.py:8309-8320 does, paced and TTL-cached. |
| (d) derivatives | Partly listed, out of family | none for P1 (family gate); fix the typing leak `first-half-total` → `total` (copy_sports.py:322-324 runs before the `>4-char unknown-word` guard at :343) so a 1H total is `prop`, not a game total — a wrong-market guard, not coverage |
| (e) undated / event-level | Unknown | probe classifies; premap cannot (R1 by design, premap.py:2915-2937); if the venue lists futures, a separate dated-key-free lane with its own audit — not P1 |
| N2/N5 ambiguity | n/a | keep refusing; count it (add `detail.map_refusal`) |
| §1.0 NULL condition_id | Yes | none needed now (7d 1 row); the probe counts it |
| §1.6 copy lane stops in mirror mode | Yes | the mirror must own its mapping (rows above) BEFORE `mirror_mode` flips for RN1, or keep the copy lane's MAPPING (not its orders) running for mirrored whales: dispatch after `us_market_slug` is written, i.e. move the `mirror_mode` stop below the mapping block (live_executor.py:8184-8381) so live_orders keeps carrying the mapping the mirror's ledger path reads |
| measurement | Yes | `shadow_market` unmapped branch: write `detail={'his_slug','title','event_title','sport','family','explain':resolve_explain(...)['step'],'notional_6h'}`; `summarize.latest` include `detail`; MIRRORMKT print `\(.detail.family)/\(.detail.explain)`; add per-family unmapped split the P0→P1 gate needs (p1_panel_synthesis.md:441 "unmapped share of his tennis moneyline activity ≤ 20%") |

Risk note on the ledger source: `map_market` trusts any live_orders row with `us_market_slug`, including rows the copy lane mapped by the fuzzy resolver and then refused (`quarantined: … (src=fuzzy, slug=…)`, live_executor.py:7349-7358). Filter `AND COALESCE(error,'') NOT LIKE 'quarantined%'` or persist `mapping_src` on the row and require it in `QUARANTINE_RESUME_SRC ∪ {'yesno_exact'}`.

## 5. MIRRORCOVER — runner-side probe step (own job, like `side-truth`, yml:3760-3767, because the probe job is at its 32-minute budget, yml:49-62)

Inputs: the checkout (yml:67), `pip install polymarket-us` (+ `pip install -e backend` until the grammar lives in a pure module — live_executor imports asyncpg/settings at module level, live_executor.py:36-41), `ADMIN_TOKEN`, `BASE` (yml:70-75).

Step A — server facts, one call: `GET $BASE/api/admin/mirror-cover?whale=rn1&hours=24` (new, read-only; ~40 lines beside `admin_mirror_shadow` api/app.py:5663-5672): for every condition in `active_conditions(pool,'rn1',24)`: `condition_id, his_slug, event_slug, title, event_title, outcomes, sport, family=market_type_of(his_slug), buy_usd_24h=sum(notional) FILTER side='BUY', n_fills, map=map_market(...)→{source,us_slug,per_side} or None, explain=resolve_explain(...)['step'/'detail'] when None, ttl_cached=bool`. Fallback when not deployed: crawl `https://data-api.polymarket.com/activity?user=0x2005d16a84ceefa912d4e380cd32e7ff827875ea&limit=500&offset=N` (the weekly-report crawl, yml:2311-2331; fields conditionId, slug, eventSlug, title, outcome, side, type, usdcSize confirmed) until `timestamp < now-24h`, aggregate per conditionId, and read `us_market_slug` per condition from `$BASE/api/admin/mirror-shadow?hours=24&whale=rn1` (source unknown → print `mapped_?`).

Step B — venue index, no per-slug calls: `pub.events.list({"limit":100,"offset":k*100,"active":True,"closed":False,"startTimeMin":now-12h,"startTimeMax":now+96h})` — the sweep's own rung (premap.py:2280-2283) — paged at 0.35 s until a short page (17 pages today ≈ 6 s): dict `slug→market`, `event_keys_for(title, slug)→event`, and every `marketSides[].description`.

Step C — per condition classify (Python, on the runner):
```
cands = _tennis_candidates(title, his_slug) + _us_slug_candidates(his_slug, outcome) + [his_slug, "aec-"+event_slug, "atc-"+event_slug]
hit = first cand in index  |  else title-key hit (event_keys_for(title,his_slug) ∩ index keys)
if map: line = mapped_ledger | mapped_premap (map.source)
elif hit and not hit.closed: line = listed_on_us_but_unmapped:<explain.step>:<slug_hit|title_hit>
elif hit and hit.closed: listed_closed
else: for cand in cands[:3]: retrieve_by_slug (pace 0.35s; cap 150 markets ranked by buy_usd_24h desc)
      -> found: listed_on_us_but_unmapped:<explain.step>:direct ; all 404 and search.query(title) 0 events: not_listed_on_us
```
Output, one line per market, then totals:
```
MIRRORCOVER <class> rn1 <cid[:10]> his=<his_slug> us=<us_slug|-> sport=<> family=<> usd24h=<0.00> fills=<n> why=<explain.step|-> hit=<slug|title|direct|-> dates=<his_date>/<venue_date>
MIRRORCOVER-TOTAL markets=<n> usd=<sum> mapped_ledger=<n>/$<usd> mapped_premap=<n>/$ listed_unmapped=<n>/$ not_listed=<n>/$ listed_closed=<n>/$ undated=<n>/$ null_condition=<n>
MIRRORCOVER-CELL <sport>|<family> markets= usd= mapped= listed_unmapped= not_listed=
MIRRORCOVER-WHY <explain.step>: markets= usd=      (for listed_unmapped only)
```
Budget: index ≈ 6 s; data-api ≤ 12 pages × 0.4 s; ≤ 150 × 3 direct reads × 0.35 s ≈ 160 s; `timeout-minutes: 10`. Every venue read goes through the same 0.35 s pacing the sweep uses (premap.py:64, the 2026-08-23 429 fix).

Acceptance for the P0→P1 coverage gate (addendum §3): `MIRRORCOVER-CELL tennis|moneyline` listed_unmapped/(mapped+listed_unmapped) ≤ 20% by markets AND by usd; `not_listed` is reported beside it and is not ours to fix.

## 6. Open questions the probe answers
1. Dollar share of (a) vs (b)+(c) — the only number that says whether the 81% matters.
2. Venue tennis event-title shape and slug-date agreement with his slug date (late-ET matches) — decides whether title keys can carry tennis without the exact lane.
3. How many of the 56 mapped came from the ledger vs premap (`detail.map` exists in the row, not in the endpoint) — decides how much coverage disappears when `mirror_mode` stops the copy lane.
4. Whether the venue lists any undated/event-level market RN1 trades (class e).

## 7. C1 (2026-09-06) — the mirror maps what the copy lane already maps

Owner order 2026-09-06 ~14:00Z: "Let's remove those caps so we start copying his actual book." Shadow probe 16:37Z: 1,217 of his markets in 6 h ($2.15M of his flow) unmapped — no_side_match 577/$1.68M, no_key_intersection 376/$329k, type_prefix_filter_emptied 165/$72k, unknown_market_type 99/$68k — while the per-fill copy lane (§1.6) had been mapping the same feed through four more resolvers. Patch C1 (against 78da1a1); tests in `backend/tests/test_mirror_maps_the_copy_lane.py`.

### 7.1 What the mirror maps now, in order

`mirror_shadow.map_market(pool, fills, pmus, whale=, condition_id=, budget=, out=)` — the one mapper both the shadow (`shadow_market`) and the live worker (`mirror_live._tick_candidate`) call; `analytics/mirror_report.mirror_cover_report` still calls it without a venue module and stays table-only.

| # | step | source | where |
|---|---|---|---|
| 1 | our own ledger rows on his tokens (unchanged) | `ledger` | `map_market` |
| 2 | `premap.resolve` per token (unchanged; `PREMAP_YN_IDENTITY=on` still answers here as `premap`) | `premap` | `map_market` |
| 3 | `_tennis_candidates(title, slug) + _us_slug_candidates(slug, outcome)` through `pmus.resolve_market_exact` — moneyline only; one candidate per call; or `pmus.resolve_derivative_exact` + `_dh_sibling_guard` for spread/total | `exact` | `map_lane.exact_lane` |
| 4 | his slug verbatim through `resolve_market_exact` — moneyline only | `exact` | `map_lane.exact_lane` |
| 5 | `pmus.resolve_team_yesno_exact` + `_dh_sibling_guard_ml` — moneyline only | `yesno` | `map_lane.exact_lane` |
| 6 | MIRROR-ONLY: the aec code-order side (`map_lane.aec_code_side`), run only when step 3 reported the `aec-` candidate LISTED and refused its side | `grammar` | `map_lane.exact_lane(grammar=True)` |
| — | the fuzzy `pmus.resolve_market` | never | — |

Steps 3–5 are the copy lane's own (live_executor's copy block, same order, same gates, same 20 s box per call). The copy block itself is byte-identical to 78da1a1: its own tests pin its text (`test_exact_resolver_diagnostics` counts three `_ex_diag.append("timeout")`, `test_identity_slug_is_exact` pins `[src_slug], ctx.get("outcome")`, `test_live_executor_mapping` pins the tennis call within 600 chars of `mtype == "moneyline"`), so it does not call `map_lane`; instead `test_the_copy_lane_and_the_shared_lane_make_the_same_resolver_calls` drives BOTH with the same recording resolvers on tennis, college football, per-team yes/no, total and spread contexts and asserts identical resolver calls in identical order on identical candidates, the copy lane's list being exactly one entry longer (its fuzzy step). Either side moving fails that test.

Per market the lane runs over his tokens larger position first and stops when one token's answer names both sides (an `aec-` identifier — both sides share it, the other token is the other intent — or a yes/no contract); a per-side family (distinct identifiers) reads both so `_choose_long` can pick his directional side. When the two tokens' sources differ the market's source is the LESS certified one (`MAP_SOURCE_RANK`).

### 7.2 The college-football side (`grammar`)

The venue's own payload for `aec-cfb-bayl-aubrn-2026-09-05` (probe 2026-09-03, the account's positions row) has `marketMetadata.title = "Baylor vs. Auburn"` — the SCHOOLS, in slug order — and `outcome = "Bears"`: the sides are the mascots. His feed says "Baylor". Step 3 finds the market by slug and refuses the side by name — correctly: the copy lane's `aec-` side rule is name similarity on `marketSides[].description` plus the venue's long marker (`pmus.resolve_market_exact` :1936-1961), it has NO team-code or order rule, and the 2,012 echoes on `side_echo_last` certify name+marker, not order. `map_lane.aec_code_side` (round-2 rules, review A/B) chooses the side from HIS OWN signal, never from the mascot, and every fact must agree: his outcome names exactly one of his slug's two codes by the WHOLE-WORD / FIRST-WORD rule (`code_hits`: the code is a whole word, or the first word starts with it, or it starts with the first word (≥3 letters); no substring, no subsequence — "Michigan State" hits `mich` by its first word and the index refuses it; `aubrn` never names Auburn); his `outcome_index` is PRESENT and equals the code's position (mandatory: `side_code_no_index` when absent); the venue slug echoes his under `aec-` with exactly two sides sharing it; the intent is the venue's long/short marker on the side at that position. His OTHER token, when in the fills, is judged by the pair rule (`pair_agrees`): complementary `outcome_index` and no claim on the mapped code, no venue read — Auburn (index 1, names neither code) is the other side of Baylor's contract by construction. Refusals, all on the row (`detail.explain`) and in MIRRORUNMAP's `why:`, and ANY of them on either token refuses the whole market (review E): `side_code_unmatched` (names neither code — "Sooners"; pair-resolvable only when the sibling maps), `side_code_ambiguous` (both codes as whole words), `side_code_no_index`, `side_code_conflict` (index ≠ position; his slug's own side token names the other team; a venue side's description names the other position's code and not its own, or names BOTH codes; the venue's matchup title names the codes in the opposite order), `side_code_pair` (the sibling's index is not the complement), `side_code_shape`, `side_code_nointent`. What remains unproven at mapping time — that `marketSides[i]` is the team at slug position i — is exactly what the class's live certification checks against venue-only truth (7.7).

### 7.3 Venue budget, pacing, cache

Every resolver call goes through `venue_pace.pace(READ_PACING_S)` (the `_paced_bbo` gate; one slot per call — `resolve_market_exact` is called one candidate at a time so each slot is one lookup; a `resolve_team_yesno_exact` call's own ≤2 lookups ride one slot) and counts one unit of the tick's `MapBudget`. THE KNOB, exactly: `mirror_shadow.MAP_READS_PER_TICK = int(rules.capped_env("MIRROR_MAP_READS", 10.0, floor=0.0))` — env name `MIRROR_MAP_READS`, default 10 resolver calls per tick, a shell may only LOWER it (`50` reads 10, `3` reads 3, junk reads 10; pinned by `test_the_environment_may_only_lower_the_map_read_budget`). Past it the market gets NO verdict this tick: shadow row reason `map reads capped: no verdict this tick`, `detail.explain = map_reads_capped`, never TTL-skipped, read again next tick; live: `_mirror_stop("map_reads_capped")`, no `_unmapped_until`. Verdicts (hit or miss) are remembered per (whale, condition_id) per token for `MAP_CACHE_TTL_S` = 900 s (`_map_cache`), and a candidate slug the venue answered 404 is remembered under the same TTL (`_slug_404_until`) so a lane the budget cut resumes where it stopped; a market the budget cut gets NO entry, and both memories are pruned on the TTL at every map (`_prune_maps`, review C). The `aec-` payload is read once per market per call for both tokens. Shadow stats: `mapped_by {ledger, premap, exact, grammar, yesno}`, `map_venue_reads`, `map_cache_hit`, `map_reads_capped`; row detail: `map_venue_reads`, `map_cache_hit`, `map_lane` (`unavailable` when the venue module carries no resolvers, or the exception name). Live census (appended to `CENSUS_KEYS`, past the served 40-key prefix): `map_reads_capped`, `map_source_unverified`, `map_venue_read`, `map_cache_hit`, and the grammar class's own `grammar_echo_unreadable`, `grammar_tripped`, `grammar_probation`, `grammar_echo_unverified`, `grammar_echo_ok`, `side_echo_mismatch`; SERVED on `integ` (review D, (4)): `map_source_unverified`, `map_reads_capped`, `side_echo_mismatch` (the block is 39 keys, under the sanitizer's 40).

### 7.4 Which sources the live mirror admits, and why

`mirror_books.map_source` (047, TEXT, no CHECK) now carries `'ledger' | 'premap' | 'exact' | 'yesno' | 'grammar'`. `_tick_candidate` refuses any source outside `mirror_live.MIRROR_LIVE_MAP_SRC = {ledger, premap, exact, yesno}` that is not `grammar` by name (`map_source_unverified`); `grammar` goes through its own certification first (`_grammar_admission`, 7.7) and then, like the four others, through `_admit_source` → `le._mapping_admitted` (open and every increase, `_increase_recheck` reads `book.map_source`; a grammar book's increase also re-reads the class's trip):

| source | while the mapping quarantine holds | quarantine lifted | why |
|---|---|---|---|
| `premap`, `exact` | admitted on the resume lane (`QUARANTINE_RESUME_SRC`, `LIVE_PREMAP_WHALES`, `premap_live`) | admitted | the copy lane's own resume classes; `exact` is the same `resolve_market_exact`/`resolve_derivative_exact` output the copy lane trades |
| `yesno` | refused by name: `quarantined: mapping class unverified … (src=yesno, …)` | admitted | the copy lane's `yesno_exact`: refused-but-recorded there too and certified under `YESNO_CERT_KEY`; the mirror goes live on it the same day the copy lane does (a one-token change to `QUARANTINE_RESUME_SRC`, or the quarantine lifting) |
| `ledger` | refused (not in `QUARANTINE_RESUME_SRC`) | admitted | unchanged |
| `grammar` | admitted on the resume lane's switches (`_mapping_admitted` as for `exact`: circuit, verified set, hold, `LIVE_PREMAP_WHALES` + `premap_live`; the refusal text says `src=grammar`) AND ONLY under its own certification (7.7): venue-truth check ok before the open, one book at a time until its first fill is echoed, refused `grammar_tripped` after any mismatch | the same: the certification is not a quarantine clause | the copy lane has no side rule this class could inherit, so it earns its own — every gate is a census name and the trip is `side_echo_mismatch`, served on `integ` |

`analytics/mirror_report.ADMISSIBLE_SRC` stays `{premap, exact}` (pinned equal to `QUARANTINE_RESUME_SRC`), so MIRRORSRC's `admissible=` is the share P1 can open today; `yesno` and `grammar` appear in `by_source` beside it.

### 7.5 Explain names and the two measurement changes

`explain_unmapped(pool, ctx, refusal)`: a lane refusal (`side_code_*`, `map_reads_capped`) names the row; otherwise premap's own step, unchanged — a market the new steps also fail keeps `no_side_match` etc., and a market they MAP is no longer in MIRRORUNMAP by construction. `unknown_market_type` now carries its split as `unknown_market_type:unparsed` (the slug grammar named no family — a parser gap) or `unknown_market_type:family_not_listed` (prop / exact_score / btts / crypto — a venue-family gap); `premap.resolve_explain` keeps `step == "unknown_market_type"` (the copy lane's census buckets on it) and adds `split`. `his_fills` reads `COALESCE(t.outcome, mt.outcome)` (and `outcome_index`) from `market_tokens`, as `api/app.py`'s unmapped census does, so a chain row not yet enriched is not filed as `no_side_match`.

### 7.6 What this does NOT map (left out, by design)

- Tennis the venue does not list (§2.a): every candidate 404s; `premap`'s name stays. The 404 memo makes the second tick free.
- `bun-wer-lei-2026-09-05-lei` ("Will RB Leipzig win on 2026-09-05?"): the copy lane's yes/no lane refuses it pre-network (`yn:title-code`: "rb leipzig" does not prefix `lei` under `_code_prefix_hit`), so the copy lane only ever mapped it through FUZZY. The mirror never runs fuzzy; the network-free mapper for this row is premap's identity branch (`PREMAP_YN_IDENTITY=on`, scoping lever #1, source `premap`, tested here) — the owner's flip, not a code change.
- LA LIGA, as his feed actually spells it (probe MIRRORUNMAPMKT 2026-09-06: `lal-bet-rea-2026-09-04-bet`, "Will Real Betis Balompié win on 2026-09-04?"; venue SHORTROW: `atc-lal-bet-rma-2026-09-04-bet`): the two feeds spell Real Madrid's code differently (`rea` vs `rma`). The identity branch needs `atc-` + his slug byte for byte; the yes/no lane builds `atc-lal-bet-rea-…-bet` / `atc-lal-rea-bet-…-bet` (both unlisted) and refuses his title first (`yn:title-code`: "real betis" does not prefix `bet`, and "Real Betis Balompié" would not either). NEITHER path maps it; premap's `no_side_match` stands (its title keys do find the venue rows). With the coordinator's spelling (`lal-bet-rma-…`, title "Will Real Betis win …") the identity branch maps it (source `premap`) and the yes/no lane still refuses at `yn:title-code` — the wording that lane needs is a subject whose collapsed distinctive form starts with the code ("Betis Sevilla" would pass; "Real Betis" cannot: `real` is not one of the ten `GENERIC_CLUB_TOKENS`). What would map the feed's row is a code alias read off the venue's own row (his side code + league + date pin the venue slug `atc-lal-<a>-<b>-<date>-bet` uniquely; the opponent's venue code is then READ, never guessed) — a C2 item, not in this patch.
- Derivative families map (`exact`, with the doubleheader guard) but their books still answer to `rules.admission`'s family gate as before.
- `mirror_cover_report` (MIRRORCOVER) is unchanged and table-only.

### 7.7 Round 2 (2026-09-06 18:25Z, "college football and la liga should be very easy to map"): the grammar class goes live under its own certification

**What the copy lane's side rule is, exactly.** `pmus.resolve_market_exact` :1936-1961 on a two-sided market: score each `marketSides[].description` against his outcome (`_outcome_score`, name similarity), take the unique side at or over `MATCH_FLOOR`, intent from `order_intent_for` → `premap.side_intent` (the venue's `long` marker). The post-fill echo (`_side_echo_verify` :5611) re-derives through `premap.match_side` (name-based), then checks the position SIGN against the intent (sole-leg attribution), then `_independent_check` (name-based). No code rule, no order rule: on mascots every one of them abstains or refuses. So `side_echo_last ok=2012` certifies "name + marker", and `grammar` cannot inherit it — this is the branch the coordinator's order names ("if the copy lane has NO side rule for aec-, say so").

**The certification (`mirror_live`), every step a census name:**
1. `_grammar_admission` before the open: the state key `mirror_grammar_echo` readable (else `grammar_echo_unreadable`), not tripped (else `grammar_tripped`), no other grammar book still awaiting its first-fill echo (else `grammar_probation` — one at a time, never TTL-skipped), then VENUE-ONLY TRUTH (`map_lane.grammar_truth`, review (2)): the venue's per-side contract for the chosen code, `atc-<lg>-<a>-<b>-<date>-<code>` (`contract_slug`), must name in its own `outcome`/`title`/`team` fields EXACTLY the `aec` side description the rule chose (`Bears`). Both strings are the venue's; agreement pins side i to the code with no order assumption. Unlisted → `grammar_echo_unverified` (refused, nothing tripped); listed and different → `side_echo_mismatch` (refused, the class TRIPPED). Ok → `grammar_echo_ok`, the chosen side recorded under `pending[slug]`.
2. `_admit_source`: the resume lane's own gates through `le._mapping_admitted(pool, w, "exact", slug)` — circuit, verified set, hold, `LIVE_PREMAP_WHALES` + `premap_live` — with the refusal text renamed `src=grammar`. `grammar` never joins `QUARANTINE_RESUME_SRC`.
3. `_grammar_fill_check` in `_tick_book`, every tick the book holds shares until verified: the venue's positions payload (`_position_echo`, `pmus.position_side`'s walk with `marketMetadata` kept) must name the recorded side (`outcome == "Bears"`) with the book's sign, `|net|` within 5%+1 of our booked fill (the copy lane's sole-leg rule; else `grammar_echo_unverified` and the book waits). Ok → the book joins `verified` (the next grammar book may open); mismatch → `_freeze(book, "side_echo_mismatch")`, open order cancelled, the class tripped; `_increase_recheck` on any grammar book re-reads the trip.
Clearing a trip is an admin write to `mirror_grammar_echo` (`tripped=false`, `mismatch=0`) — no shell switch exists on purpose.

**Live-admitted sources after round 2 and the evidence for each:** `premap` and `exact` — `QUARANTINE_RESUME_SRC`, the copy lane's 691/0 shadow-cert streak and `side_echo_last ok=2012`; `yesno` — the copy lane's `yesno_exact` under `YESNO_CERT_KEY`, refused while the quarantine holds; `ledger` — the copy lane's own traded rows; `grammar` — the venue-truth check per market before the open plus the first-fill echo per book, counted on `mirror_grammar_echo`, both unfalsified in the suite only (no live count yet: `ok=0` until the first CFB book).

**Expected mapping rates (the shadow's unmapped list, last 6 h, probe 1577 — the endpoint prints the 12 largest by dollars, the rest is not readable from here):** `cfb-*`: 0 of the 12 printed rows; the scoping's four cfb samples (`cfb-bayl-aubrn`, `cfb-ucla-cah`, `cfb-boise-ore`, `cfb-unlv-hawaii`) all map by grammar when his outcome names a first-word code and the index agrees (Baylor/`bayl`, UCLA/`ucla`, Boise State/`boise`, UNLV/`unlv`, Hawaii/`hawaii`, Oregon/`ore`) — 4/4 in the shadow; LIVE they open only where the venue lists the per-side `atc-cfb-…-<code>` contract naming the mascot side (the venue does list per-side cfb contracts: `atc-cfb-hawaii-stan-2026-08-29-h` in the probes), so the live rate is the venue's per-side listing rate, unknown from here. `lal-*`: 5 of the 12 printed rows ($189k of $956k) — the two moneylines (`-bet`, `-rea`), two totals and a spread — and 0 of 5 map now: every one carries the feed's `rea` where the venue has `rma` (the totals' `tsc-lal-bet-rea-…` and the spread's `asc-…` candidates are unlisted for the same reason). The C2 code-alias rule above would recover the two moneylines through `yesno`'s admission class and, with the same alias in the derivative candidates, the three derivatives through `exact`.

### 7.8 Round 3 (2026-09-06, conditional review of round 2)

1. **Single-token fills** (`mirror_shadow._catalogue_pair`): nothing in a one-token fill pins the feed's `outcome_index` order to the slug's team order, so a first-word collision ("Michigan State" naming `mich`) would map the wrong side on the index alone. A grammar map with no sibling in his fills now needs the market's own `market_tokens` rows: exactly the condition's two tokens, the mapped token at the code's position, the sibling at the complement with no claim on the mapped code (`pair_agrees` on the catalogue row) — else `side_code_pair`; an unreadable catalogue or no `condition_id` refuses the same way. Both tokens in the fills: the pair rule reads the fills, the catalogue is never read.
2. **`grammar_truth` trips only on the other side's name**: `mismatch` (refuse + trip) only when the per-side contract names `sides[1-i].description` in its outcome/title/team fields; a yes/no-shaped contract, a name that is neither side's, or no name at all is `unverified` (no book, no trip).
3. **The venue's own suffix** (`mirror_live._contract_candidates`): the per-side contract is found in `us_premap` (`identifier LIKE 'atc-<lg>-<a>-<b>-<date>-%'`), single-token suffix only (segment/prop rows never fit), fitting his code exactly, or prefixing his code and not the other's (`…-h` for `hawaii` beside `stan`), or whose question names the aec side description whole; EXACTLY one distinct slug, else `grammar_echo_unverified`. When the sweep lists no row, his own code's slug (`contract_slug`) is the one candidate the venue must confirm.
4. **A book whose first fill is unverified WAITS**: `_grammar_fill_check` answers `ok | wait | frozen`; an unreadable certification state, an unreadable or unattributable echo, or a missing `pending[slug]` record is `wait` — `_tick_book` writes a `grammar_echo_unverified` no-plan and the book is neither traded further nor frozen; a missing record can never trip.
5. Pinned: "Bayl Aubrn" (index 0) beside "Aubrn" (index 1) refuses `side_code_ambiguous`; a one-letter first word ("A&M Aggies") never names a code.

### 7.9 Round-3 review fold (2026-09-06 19:4xZ, landed with round 3)

Two fixes from the adversarial review of round 3, both fail-closed, both pinned in
`tests/test_c1_round3.py`:

- `mirror_shadow._catalogue_pair`: a catalogue sibling whose `outcome` is NULL/blank is
  `side_code_pair`. The catalogue's indices come from the same feed as his fill's, so only the
  sibling's NAME pins the feed's index order to the slug's team order; with it empty, the pair
  rule would pass on the index complement alone and a swapped feed order opens the wrong side.
- `mirror_live._contract_candidates` takes the OTHER side's description (read off the aec
  market's `marketSides`) and its by-question fit requires the question to name our side and
  NOT the other's: the opponent's per-side row ("Will Tigers win against Bears?") no longer
  fits ours, so a sweep that lists only the opponent's row cannot become a false trip.

Residual, accepted and dated: a grammar book whose first-fill echo stays `wait` plans nothing,
reductions included (only the closing auction and `_maybe_close_episode` run before the hook);
a grammar book that cannot certify cannot follow his exit until the echo reads. To revisit when
the first grammar book has opened (none has; college football is the class, and Sunday lists
three games).


## 8. C2 (2026-09-06) — the per-team yes/no identity branch maps his soccer

Owner 18:25Z: "I need you to map more of his trades ... la liga should be very easy to map"; 18:57Z: "we need more volume". In the two hours to 19:04Z the shadow read 260 markets ($544k) and mapped 3; the bulk was soccer per-team yes/no. The venue's own rows for five of his events (preset `premap-rows`, 19:09Z; 216 `atc-` rows) reproduced every refusal offline. Patch C2 against 2dc3204 (C1 rounds 1-3 + the review fold); tests in `backend/tests/test_c2_yesno_alias.py` (the dump's per-team and draw rows are embedded verbatim). Every admission below is IDENTITY (byte-equal team codes, date and side token, plus name corroboration on both teams) — never fuzzy; `PREMAP_YN_IDENTITY=on` still gates all of it, and the wording arm is byte-identical.

### 8.1 The four gates that refused the seven feed rows, and what each does now

| gate | before | now | where |
|---|---|---|---|
| league slot | `_yn_slot_bad` ran the CLUB scope screen on the `lg` group: the single-letter rule refused `serie a` — every Serie A market | `pmus._yn_league_slot_bad`: non-empty, ≤ 5 tokens, one token of ≥ 3 letters, no scope token/stem (bridge list + `_YN_SCOPE_EXTRA` + `_YN_LEAGUE_SCOPE`: primavera, youth, u21, esoccer, srl, friendly …), adjacent-run joins; a single letter only as `a` or before `league` (`k league 1`); a lone digit is a league number | `pmus.py` (`_YN_LEAGUE_SCOPE`, `_yn_league_slot_bad`), used by `premap._yn_team_row/_yn_draw_row` AND the copy lane's `resolve_team_yesno_exact` (`yn:league-slot`) |
| names with digits | `_YN_Q_PATTERNS` slots were `[a-z ]+?`: `Bologna FC 1909` never matched (Mainz 05, 1899 Hoffenheim …) | the three slots are `[a-z0-9 ]+?`; digits stay in the name and go through `_yn_name_match` as tokens (`1909 ≠ 1919`) | `pmus._YN_Q_PATTERNS` (still one template) |
| diacritics | `_norm` is NFKD→ascii-ignore: `Tromsø` → `troms` (ø, ł, đ, ß have no decomposition) and `_folds_away` refused the title outright | `pmus.fold_latin` maps those letters explicitly (ø→o, ł→l, đ→d, ß→ss, æ→ae …) and deletes apostrophes (`Newell's` → `newells`, not `newell s`); applied before `_norm` on the yes/no name channels (his anchor `_bridge_title_subject`, the venue subject/opponent/draw slots, the copy lane's W2/W4/W7) and in `event_keys_for` on both sides; `_folds_away` reads through it, so a letter OUTSIDE the table still refuses by name. `_norm` itself is untouched (it produces `side_norm`, half of the `us_premap` unique index) | `pmus.py` (`_LATIN_FOLD`, `fold_latin`, `_norm_folded`), `premap.py` (`_folds_away`, `_bridge_title_subject`, `event_keys_for`) |
| league code | `_yn_his_identifiers` = `atc-` + his slug byte for byte; his `nor`/`arg`/`pol`/`fl1`/`por` are the venue's `els`/`lpa`/`ekst`/`lg1`/`lpb` | the alias rule below (`premap._yn_alias_pick`) | `premap.py` |

### 8.2 The alias rule (no table)

`_yn_pick(rows, outcome, his_title, his_slug, his_event_title)`: identity first (unchanged); then, ONLY when no row carries his own identifier, a row `atc-<LG>-<a>-<b>-<date>-<t>` is his when ALL of: (i) his slug parses as `<lg>-<a>-<b>-<date>-<t>` with `t ∈ {a, b}` and the venue row carries exactly that suffix in that order; (ii) exactly ONE venue league code carries the suffix among the rows his keys fetched — two is `yn:league-ambiguous` (a same-day women's/youth/esoccer twin lands here even though its own league slot would refuse it); (iii) the question fullmatches the per-team template on his slug's date, its subject name-matches his title's anchor (`_bridge_title_subject`), its opponent name-matches the OTHER side of HIS event title (`_yn_event_sides`, the witness — no event title is `yn:alias-unwitnessed`, a title naming another game `yn:event-shear` / `yn:opp-witness`), the league slot passes the league rule; (iv) `side_norm`/intent are the venue's own for his Yes/No; the line guard and the identity veto of `match_side` apply as to every row. The alias observed rides the hit (`league_alias: "nor->els"`) and `resolve` labels it `matched_by 'premap_alias'`; source stays `'premap'`. His own identifier on the board and refused is never aliased around. The DRAW (`_yn_draw_pick`): his `<lg>-<a>-<b>-<date>-draw` + Yes/No → the `-draw` row under the same identity, its question the venue's dated draw template naming BOTH his event's teams in either order (`yn:draw-names`), his title a closed veto (`_yn_title_is_his_draw`: says `draw`, never `win`, any date his slug's, every non-furniture token one of his two teams' — the feed's draw wording is unattested, so no grammar is guessed); same league code → `premap_identity`, another → `premap_alias`.

### 8.3 Refusal names (census: `resolve_explain` → `yn_c2.refusal`, and the step's `split`, so `explain_unmapped` prints `no_side_match:yn:…`)

`yn:league-slot`, `yn:league-ambiguous`, `yn:name-digits` (a digit in HIS subject — the bridge's `subject_has_digit` pin, kept), `yn:no-row`, `yn:alias-unwitnessed`, `yn:event-shear`, `yn:opp-witness`, `yn:identity-refused`, `yn:shape`, `yn:qdate`, `yn:scope`, `yn:subj`, `yn:side`, `yn:intent`, `yn:folds`, `yn:title-folds`, `yn:title-<why>`, `yn:draw-unwitnessed`, `yn:draw-title-<why>`, `yn:draw-names`; dark, a would-resolve reads `yn:would-resolve`. `yn_identity`'s pinned dict is unchanged.

### 8.4 The seven feed rows against the 19:09Z dump (flag on, offline)

| his slug | outcome | venue identifier | intent | matched_by | note |
|---|---|---|---|---|---|
| `sea-juv-mil-2026-09-06-juv` | Yes | `atc-sea-juv-mil-2026-09-06-juv` | BUY_LONG | premap_identity | league slot `serie a` |
| `sea-bol-sas-2026-09-06-sas` | Yes | `atc-sea-bol-sas-2026-09-06-sas` | BUY_LONG | premap_identity | + opponent `Bologna FC 1909` |
| `lal-esp-sev-2026-09-06-esp` | No | `atc-lal-esp-sev-2026-09-06-esp` | BUY_SHORT | premap_identity | mapped before too; production's 19:02:02Z `no_side_match` was the memo (8.5) |
| `nor-kbk-tro-2026-09-06-tro` | Yes | `atc-els-kbk-tro-2026-09-06-tro` | BUY_LONG | premap_alias `nor->els` | `Tromsø` → `tromso` |
| `arg-ros-new-2026-09-06-new` | Yes | `atc-lpa-ros-new-2026-09-06-new` | BUY_LONG | premap_alias `arg->lpa` | `Newell's` → `newells` |
| `fl1-olm-pfc-2026-09-06-olm` | Yes | — | — | `no_key_intersection` | the dump carries no `olm-pfc` rows (the query named five events); with the venue's wording under `lg1` (listed tonight, 3 games) it aliases `fl1->lg1` — pinned as an expectation |
| `por-gui-cas-2026-09-06-gui` | Yes | — | — | `no_key_intersection` | same: under `lpb` (3 games tonight) it aliases `por->lpb` |

Also pinned: `pol->ekst` (`Jagiellonia Białystok` / `Śląsk Wrocław`, Polish letters both sides), the draw on all five events (`sea-juv-mil-…-draw` → `atc-sea-juv-mil-…-draw`, `nor-…-draw` → `atc-els-…-draw`), and the refusals: a suffix under two league codes, the NEOM shear (a wrong-team title on the right suffix), a lined row, a swapped intent, another date, the reversed team order, no event title.

### 8.5 The premap poller's cadence and the unmapped memo

`workers/premap.py`: the full sweep runs every `REFRESH_SECONDS` = 1800 s over now-12h..now+96h (120 pages at 0.35 s), the fast lane every `FAST_REFRESH_SECONDS` = 180 s over now-3h..now+14h (25 pages), and the fast lane SKIPS a cycle while the full sweep holds `_SWEEP_LOCK` (~42 s per full sweep). A market the venue lists at 18:59Z for a 19:00Z kickoff is in `us_premap` within one fast cycle, two when one is skipped — and the shadow's `UNMAPPED_TTL_S` = 900 s memoised the 19:02:02Z `no_side_match` for the whole first half. No kickoff time is readable by the tick (`us_premap` stores no start time; his feed carries a date, not a clock), so the rule keys on what the tick has: `mirror_shadow.unmapped_memo_s(explain, seen_before)` — a market's FIRST unmapped verdict, when the verdict is one the poller can change (`no_key_intersection`, `no_side_match`), is remembered `UNMAPPED_FRESH_TTL_S` = 360 s (two fast cycles); every later miss, and every other verdict, keeps 900 s. Cost: at most one extra read per new market, ever. Applied in the shadow's `tick_once` (it carries the explain); `mirror_live._tick_candidate` computes no explain and is unchanged.

### 8.6 What this does NOT do (left out)

- A digit in HIS OWN title subject (`Will Mainz 05 win …`) still refuses at the bridge's `subject_has_digit` (pinned in `test_mapping_identity`); it is now counted as `yn:name-digits`.
- The copy lane's yes/no lane (`resolve_team_yesno_exact`) keeps every gate of its own (`yn:title-code`, `yn:opp-title-code`, the thin/twin floors); it only gained the fold, the digit slots and the league rule — `Kristiansund BK` still refuses there because `kbk` does not prefix it.
- No alias for derivative candidates (`tsc-`/`asc-` under another league code): the exact lane's candidate grammar is untouched.
- `_norm` is not changed globally; `side_norm` and the unique index keep the old fold. Existing `event_keys` rows for ø/ł clubs re-key at the next sweep (30 min, 3 min in the fast window); until then the lookup, not the decision, is the old one.
- The live worker's memo (`mirror_live`) is unchanged: it has no explain in reach at the refusal.
