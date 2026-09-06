# REFUTER NOTES — lens 'shorts', angle 'market' (read-only; HEAD c2249ce; 2026-09-02 ~23:40Z)

Parse script: tee/refute_market_parse.py (MIRRORMKT over probe_336*.txt). Python probes run against the imported modules; outputs quoted below verbatim.

## Numbers re-derived
- 8 files (the lens's set, 33664216770..33686724064): mapped latest 33, his_net<0 18, ledger<0 14; by family {aec neg 11, aec pos 6, atc pos 5, atc neg 3, tsc pos 4, tsc neg 4}; 'short side not admitted' per file 0,0,2,0,3,7,3,4. MATCHES the lens.
- +probe_33690960366 (22:32-23:15Z): mapped 38, neg 20, ledger<0 15.
- atc- rows (latest, 8): osn-fcb (dfb), cue-gua (ecu1), mac-man (ecu1), bur-mid (eflch), qpr-car (eflch), wba-cha (eflch), dfc-joh (scp), gcz-stg (swsl) — ALL soccer.

## The venue's soccer shape (the lens's per-side finding is wrong here)
probe_33686724064.txt:1827-1832 NAMEDML-Q: `atc-eflch-bur-mid-2026-09-02-bur` = "Will Burnley FC win ...?" with side yes AND side no, both identifier == the slug; `-mid` = "Will Middlesbrough FC win ...?" yes/no; `-draw` = "... end in a draw?" yes/no. Same for wba-cha (:1833-1838).
python: `_us_game_key` of -bur, -mid, -draw all == `eflch-bur-mid-2026-09-02`; `market_type_of('...-draw')` == 'moneyline'.
premap.side_intent :2066-2068 reads the side's `long` bool → yes=BUY_LONG, no=BUY_SHORT on ONE slug → mirror_shadow._choose_long standard branch (python: `_choose_long long+short` → no per_side key). The two-longs branch (per_side=True, larger side is the long) needs two BUY_LONG rows on two slugs, i.e. a two-outcome team/team condition — none of the 8 mapped atc- rows.
MAPA :1429 `rn1 pick=No slug=atc-scp-dfc-joh-2026-09-02-dfc` → his soccer token is the NO of one team's question; SHORTROW :1956 shows the copy lane already expressed it as BUY_SHORT on that slug (venue ORDER_SIDE_SELL).
Consequence: "B-long is economically A-short" and "EITHER BUY_SHORT on A's slug OR BUY_LONG on B's slug" (lens §4) are FALSE on soccer: BUY_SHORT -bur pays on Mid OR draw; BUY_LONG -mid pays on Mid only. The real per-side gap: his multi-condition soccer structure (two MIRRORMKT rows on one game_key) is refused by one-per-game (le:8645-8660, OR lane='mirror'; 047:76).

## sell_price vs the executor's short wire (lens §1 "byte-equal")
python: `sell_wire(1-0.18)=0.82  sell_price(1-0.18,0.01)=0.83  rest_tick(wire_limit(0.18,SHORT))=0.82  repr(1-0.18)=0.8200000000000001`
`sell_price(1-q,0.01) != rest_tick(wire_limit(q)) at q:` 19 of 98 exact cents [0.18,0.41,0.42,0.43,0.57,0.58,0.59,0.69,0.7,0.71,0.82,0.83,0.84,0.85,0.94,0.95,0.96,0.97,0.98]; 0 of 98 with round(1-q,4); 0 of 20000 random 6-decimal q.
Cause: mirror_live_rules.sell_price :587-588 steps the cent UP when `w < h` and h carries float noise above the cent (post-condition "wire >= his equivalent at ANY precision"). sell_wire alone (:525) equals wire_limit. The worker's wire is sell_price (docstring :572), and the same path prices P1's own SELL_LONG reductions.

## Long-denominated sizing on a short leg
mirror_live_rules.room_scale :632 `shares = cash / w`; python `room_scale(1000,0.83,50,1250,1e6,1250)=60` vs 294 at the true 0.17 cost/share. le:6303 `wire_usd is shares x the wire price, added to requested_usd`. Both must use (1 - wire) on a short.

## Settlement split sign (lens §5b "right")
engine.py:399-416 allocate_venue_pnl: pro-rata by filled_usd, no intent, no sign; :555-556 target = venue realized - cashed_out. On a slug holding a long row and a short row both get the same sign of the NET position's realized: sums to the venue, wrong per row.

## Commission (unmeasured)
probe_33686724064.txt:2043-2044 execution keys include commissionNotionalCollected, commissionSpreadPx; `grep -rn commission sportsassets/` = 0 hits. Values never logged (16 key-only occurrences across probes).

## Post-only never sent on any intent
probe_33686724064.txt:3719 `post_only: false`, :3780 `post_only:` (empty); 0 `participateDontInitiate` placements. The 373 short proofs (:1320) are IOC/FOK fills (le:9023 IOC; submit_fok default FOK).

## SELL_SHORT wire (lens §5c) — confirmed unproved
grep SELL_SHORT: 0 lines in every probe_*.txt and 0 in probe4*.log (Aug 24). le:1373-1382 prices the partial short exit at sell_limit_price(1 - bestAsk) (short-leg space) and pmus:2211 sends it verbatim; census :1602 mx_venue_unfilled: 4 (unattributed).

## Preview guard on a BUY_SHORT
pmus:2233 expected_cost = limit_price(wire) x qty; :2254 refuses only when venue cost > expected x tol. On a short the venue books a SELL (:1951) so the compared figure is proceeds, not what we pay; a maker rest / IOC at >= wire bounds pay = 1 - price by construction, so harmless, but not a pay bound.
