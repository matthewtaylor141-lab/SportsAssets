# All-sports coverage, Sunday 2026-10-04 (ET day)

Run 37199021838, job 111426762806, query `research/c28_coverage_receipt_final.sql` (commit c83c15a), sha256 `a0d648e47b84ea8336f529f1d4bc4664f6ce1d6d0646a1d4d4af3690dc02f8c1`, production read at 2026-10-04 11:31:59.450706+00, psql exit 0, receipt generated 2026-10-04T11:36:28Z.

Status = production rule `coverage_integrity.lane_scope` + `classify_status`, applied to the persisted funnel row read in this run (counts are provider events, unit of the funnel; entries counted across paper strategies exactly as the funnel does; DEREK ENTER from Derek's own decisions).

| LEAGUE | family | VENUE (ET day) | PROVIDER | NORMALIZED | VENUE DISCOVERED | MAPPED | SETTLEMENT | EVALUATED | DECIDED | ENTER (any paper strategy) | DEREK ENTER | REFUSE | PAPER ORDERS | PAPER FILLS | STATUS | reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MLB | baseball | 2 | 5 | 5 | 5 | 5 | NULL (unmeasured) | 4 | 4 | 2 | 0 | 2 | 2 | 2 | HEALTHY | 2 of 4 decided event(s) ENTERED |
| NFL | football | 14 | 28 | 15 | 15 | 15 | NULL (unmeasured) | 13 | 13 | 0 | 0 | 13 | 0 | 0 | REFUSING_BY_POLICY | all 13 decided event(s) REFUSED by a named policy |
| NCAAF | football | 1 | 39 | 5 | 5 | 5 | NULL (unmeasured) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | COVERAGE_INCIDENT | 39 provider event(s); none reached evaluated and nothing reached a later stage |
| NBA | basketball | 2 | 0 | 0 | 0 | 0 | NULL (unmeasured) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | EXPLICITLY_UNSUPPORTED | FAMILY_NOT_IN_COLLECTOR_SCOPE: no basketball provider key is requested by the collector (ext_pinnacle_loop maps none) and basketball h2h is not in the measured de-vig set |
| WNBA | basketball | 2 | 0 | 0 | 0 | 0 | NULL (unmeasured) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | EXPLICITLY_UNSUPPORTED | FAMILY_NOT_IN_COLLECTOR_SCOPE: no basketball provider key is requested by the collector (ext_pinnacle_loop maps none) and basketball h2h is not in the measured de-vig set |
| NCAAB | basketball | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | EXPLICITLY_UNSUPPORTED | FAMILY_NOT_IN_COLLECTOR_SCOPE: no basketball provider key is requested by the collector (ext_pinnacle_loop maps none) and basketball h2h is not in the measured de-vig set |
| NHL | hockey | 5 | 0 | 0 | 0 | 0 | NULL (unmeasured) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | EXPLICITLY_UNSUPPORTED | FAMILY_NOT_IN_COLLECTOR_SCOPE: no hockey provider key is requested by the collector and hockey h2h is not in the measured de-vig set |
| UEFA_NATIONS_LEAGUE | soccer | 8 | 22 | 13 | 13 | 13 | NULL (unmeasured) | 13 | 13 | 6 | 0 | 7 | 6 | 4 | HEALTHY | 6 of 13 decided event(s) ENTERED |
| MLS | soccer | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | UNAVAILABLE | NO_PROVIDER_EVENTS: NOT_A_CANDIDATE_THIS_CYCLE (not on the collector's board); venue lists 0 event(s) |
| MEXICO_LIGAMX | soccer | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | UNAVAILABLE | NO_PROVIDER_EVENTS: NOT_A_CANDIDATE_THIS_CYCLE (not on the collector's board); venue lists 0 event(s) |
| UEFA_CHAMPS_LEAGUE_WOMEN | soccer | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | UNAVAILABLE | NO_PROVIDER_EVENTS: NOT_A_CANDIDATE_THIS_CYCLE (not on the collector's board); venue lists 0 event(s) |
| CONCACAF_NATIONS_LEAGUE | soccer | 3 | 0 | 0 | 0 | 0 | NULL (unmeasured) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | UNAVAILABLE | NO_PROVIDER_EVENTS: PROVIDER_REFUSED: PROVIDER_DOES_NOT_LIST_THIS_COMPETITION; venue lists 3 event(s) |
| USA_USL_CHAMPIONSHIP | soccer | 1 | 0 | 0 | 0 | 0 | NULL (unmeasured) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | UNAVAILABLE | NO_PROVIDER_EVENTS: NOT_A_CANDIDATE_THIS_CYCLE (not on the collector's board); venue lists 1 event(s) |
| ARGENTINA_PRIMERA_NACIONAL | soccer | 10 | 0 | 0 | 0 | 0 | NULL (unmeasured) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | UNAVAILABLE | NO_PROVIDER_EVENTS: PROVIDER_REFUSED: PROVIDER_DOES_NOT_LIST_THIS_COMPETITION; venue lists 10 event(s) |
| BRAZIL_SERIE_B | soccer | 0 | 10 | 6 | 6 | 6 | NULL (unmeasured) | 6 | 6 | 0 | 0 | 6 | 0 | 0 | REFUSING_BY_POLICY | all 6 decided event(s) REFUSED by a named policy |
| COLOMBIA_PRIMERA_A | soccer | 5 | 0 | 0 | 0 | 0 | NULL (unmeasured) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | UNAVAILABLE | NO_PROVIDER_EVENTS: PROVIDER_REFUSED: PROVIDER_DOES_NOT_LIST_THIS_COMPETITION; venue lists 5 event(s) |
| URUGUAY_PRIMERA_DIVISION | soccer | 4 | 0 | 0 | 0 | 0 | NULL (unmeasured) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | UNAVAILABLE | NO_PROVIDER_EVENTS: PROVIDER_REFUSED: PROVIDER_DOES_NOT_LIST_THIS_COMPETITION; venue lists 4 event(s) |
| USA_NWSL | soccer | 3 | 0 | 0 | 0 | 0 | NULL (unmeasured) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | UNAVAILABLE | NO_PROVIDER_EVENTS: PROVIDER_REFUSED: PROVIDER_DOES_NOT_LIST_THIS_COMPETITION; venue lists 3 event(s) |

**Summary:** HEALTHY 2, REFUSING_BY_POLICY 2, EXPLICITLY_UNSUPPORTED 4, COVERAGE_INCIDENT 1, UNAVAILABLE 9

## Every other venue token listed on the ET day (by family, same rule)

| FAMILY (unmapped venue tokens) | tokens (events ET day) | events | STATUS | reason class |
|---|---|---|---|---|
| tennis | itfme(220), itfwo(182), atp(71), wta(35), utr(21), wtadb(15), atpdb(3) | 547 | EXPLICITLY_UNSUPPORTED x7 | FAMILY_NOT_IN_COLLECTOR_SCOPE |
| table | setkameua(235), czechligapro(55), setkamecz(51), setkamemd(36), wtt(32), setkawoua(15) | 424 | EXPLICITLY_UNSUPPORTED x6 | VENUE_TOKEN_NOT_MAPPED |
| soccer | ncaaws(74), ncaams(12), ngnpfl(10), gtasc(8), wsl(6), ghpl(6), ven2(6), j2(5), lal2(5), ch1cl(5), sercb(5), sercc(4), intf(4), lpa(4), serca(4), ligaf(4), norw1(3), fbl(3), bolcup(3), mne2(3), be1vv(2), lng(2), svn3e(2), u19f(2), minw(1), lexp(1), nor1(1), cpach(1), uslcp(1), svk2(1), be1acff(1) | 189 | EXPLICITLY_UNSUPPORTED x31 | DELIBERATELY_EXCLUDED, VENUE_TOKEN_NOT_MAPPED |
| efootball | ebfwca(40), ebfsa(28), ebfwcb(20), ebfcwc(4) | 92 | EXPLICITLY_UNSUPPORTED x4 | SIMULATED_COMPETITION |
| basketball | jpbl(13), ita2(8), acb(6), autbl(5), bbl(4), lba(4), vtb(3), bsl(3), nbl(2), lnb(2), lkl(2), kbl(2), gbl(2), slb(1) | 57 | EXPLICITLY_UNSUPPORTED x14 | FAMILY_NOT_IN_COLLECTOR_SCOPE |
| esports | cs2(21), r6(8), lol(7), ow(7), dota2(3), valorant(2) | 48 | EXPLICITLY_UNSUPPORTED x6 | SIMULATED_COMPETITION |
| (none) | btc(38) | 38 | EXPLICITLY_UNSUPPORTED x1 | VENUE_TOKEN_NOT_MAPPED |
| hockey | ahl(9), del(7), cehl(6), khl(3), snhl(2) | 27 | EXPLICITLY_UNSUPPORTED x5 | FAMILY_NOT_IN_COLLECTOR_SCOPE |
| futures | f1(6), temp(5), pres(2), presmov(1), nascar(1) | 15 | EXPLICITLY_UNSUPPORTED x5 | FAMILY_NOT_IN_COLLECTOR_SCOPE |
| baseball | kbo(5), npb(3) | 8 | EXPLICITLY_UNSUPPORTED x2 | VENUE_TOKEN_NOT_MAPPED |
| pickleball | ppa(5) | 5 | EXPLICITLY_UNSUPPORTED x1 | VENUE_TOKEN_NOT_MAPPED |
| rugby | top14(1), nrlw(1), nrl(1), prem(1) | 4 | EXPLICITLY_UNSUPPORTED x4 | VENUE_TOKEN_NOT_MAPPED |
| cricket | t20icr(2) | 2 | EXPLICITLY_UNSUPPORTED x1 | VENUE_TOKEN_NOT_MAPPED |
| darts | pdcdarts(1) | 1 | EXPLICITLY_UNSUPPORTED x1 | VENUE_TOKEN_NOT_MAPPED |

## NCAAF incident: what the rows show

The NCAAF COVERAGE_INCIDENT is raised by the rule on the funnel row, but the rows show no game of the 2026-10-04 ET slate was lost: the 5 mapped events are Saturday (10-03 ET) games that had already kicked off (01:35Z-04:05Z) and were valued on 10-03 (valuations 4970-5131, decided 00:23Z-03:37Z); the other 34 are games dated 10-06..10-10 ET with no Pinnacle price yet. The funnel attributes provider events to a day by CYCLE time, evaluations by DECISION time, so the two windows disagree for games that span midnight ET. NCAAF has been budget_dropped by the collector since the 07:15Z cycle.

## Provider > 0 with a downstream 0: incident audit (deliverable 3)

| case | provider | downstream zero | incident row | Audrey finding | first loss named | verdict |
|---|---|---|---|---|---|---|
| NCAAF (league, ET 10-04) | 39 | evaluated 0 (mapped 5, settlement NULL) | covalert:41b73a6967ed9bc42f739952, detected 04:16Z | paperfind:2fc3e31e3052cdfa5a113aba (CRITICAL) | stage_to evaluated; stage_from settlement_supported with previous_stage_count null | incident present, but its FIRST LOSS names an unmeasured stage: should be mapped (5) -> evaluated (0). Fixed in code on claude/c28-coverage (first_loss). The row-level evidence also shows the incident is a day-attribution artefact, not a lost game (see above) |
| NFL (league) | 28 | none unexpected: 13 evaluated, 13 decided, 0 ENTER, so 0 orders is expected | none | none | n/a | correct: REFUSING_BY_POLICY |
| NFL MIA@MIN, LAC@SEA (per game) | 1 each | evaluated 0 | none (the funnel is per league) | none | n/a | explained by a named freshness refusal (QUOTE_STALE_ON_ARRIVAL, 25/25 ledger rows); per-game losses inside a flowing league are outside the league funnel by design |
| Brazil Serie B, MLB, UEFA NL (league) | 10 / 5 / 22 | none: each reached decisions | none today | n/a | n/a | correct |

Older alerts (10-02, 10-03, all `none reached settlement_supported`, raised 00:22Z-00:37Z on 10-04 by the pre-cand24 rule) sit on days whose snapshots show decisions flowing (10-02: MLB decided 4, Serie B 7, UEFA NL 14; 10-03: NCAAF 9, MLB 7, Serie B 10, UEFA NL 18): settlement-stage false positives of the kind cand24 already stopped (settlement is now NULL when unmeasured). No such alert has been raised since.

## Status rule (in order)

- EXPLICITLY_UNSUPPORTED: lane_scope says the league/token is outside the collector's declared maps (family not priced, token unmapped, deliberately excluded, mapping refuted, simulated)
- UNAVAILABLE: in scope but provider events = 0 (collector heartbeat stale / provider refused / budget-dropped / not on board), or the venue lists none of the provider's events, or no paper decision path recorded anything
- COVERAGE_INCIDENT: provider events > 0 and an expected stage (normalized, venue_discovered, mapped, settlement_supported, evaluated, decided-when-live) reads 0 with nothing after it; NULL = unmeasured, never absent; a zero followed by later flow is a measurement gap
- HEALTHY: reached decisions and at least one ENTER (any paper strategy, as the funnel counts)
- REFUSING_BY_POLICY: reached decisions, every one REFUSED with a named policy code
