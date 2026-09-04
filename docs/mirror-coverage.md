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
