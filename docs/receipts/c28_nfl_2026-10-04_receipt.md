# NFL production receipt, Sunday 2026-10-04 (ET service day)

Run 37199021838, job 111426762806, query `research/c28_coverage_receipt_final.sql` (commit c83c15a), sha256 `a0d648e47b84ea8336f529f1d4bc4664f6ce1d6d0646a1d4d4af3690dc02f8c1`, production read at 2026-10-04 11:31:59.450706+00, psql exit 0, receipt generated 2026-10-04T11:36:28Z.

Read-only production readback. PRODUCTION-READBACK only: nothing here is a code change.

| GAME | kickoff_utc | kickoff_et | EXPECTED | PROVIDER EVENT | NORMALIZED | VENUE CONTRACT | EXACT MAPPING | SETTLEMENT SUPPORTED | PROBABILITY SUPPORTED | DEREK EVALUATED | REFUSE/ENTER | PAPER ORDER | PAPER FILL | FIRST BLOCKER | STATUS |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| IND Colts vs. WAS Commanders | 2026-10-04 13:30Z | 2026-10-04 09:30 | Y (schedule evidence + venue us_premap now) | c9d8ed8aa4889486eaf10a630138ede0 | Y | aec-nfl-ind-was-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| ARI Cardinals vs. NY Giants | 2026-10-04 17:00Z | 2026-10-04 13:00 | Y (schedule evidence + venue us_premap now) | 664e6bda5721dc63be16bcf7d962a436 | Y | aec-nfl-ari-nyg-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| DAL Cowboys vs. HOU Texans | 2026-10-04 17:00Z | 2026-10-04 13:00 | Y (schedule evidence + venue us_premap now) | daf55e2df5341c007fee5a0fc5bbc2d4 | Y | aec-nfl-dal-hou-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| GB Packers vs. TB Buccaneers | 2026-10-04 17:00Z | 2026-10-04 13:00 | Y (schedule evidence + venue us_premap now) | 99a9bae4dc0dc2590496bd3102b211f2 | Y | aec-nfl-gb-tb-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| JAC Jaguars vs. CIN Bengals | 2026-10-04 17:00Z | 2026-10-04 13:00 | Y (schedule evidence + venue us_premap now) | 2ea94ddf20f66432b9024fe2e2eaba5f | Y | aec-nfl-jax-cin-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| LA Rams vs. PHI Eagles | 2026-10-04 17:00Z | 2026-10-04 13:00 | Y (schedule evidence + venue us_premap now) | 1b4f2e60ce83fa1d63a5dff83769d27c | Y | aec-nfl-lar-phi-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| NE Patriots vs. BUF Bills | 2026-10-04 17:00Z | 2026-10-04 13:00 | Y (schedule evidence + venue us_premap now) | 573cc0ef39631a4f6673ec843a8bedd4 | Y | aec-nfl-ne-buf-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| NY Jets vs. CHI Bears | 2026-10-04 17:00Z | 2026-10-04 13:00 | Y (schedule evidence + venue us_premap now) | 164e6eb68f31bac190381903c3cccd31 | Y | aec-nfl-nyj-chi-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| TEN Titans vs. BAL Ravens | 2026-10-04 17:00Z | 2026-10-04 13:00 | Y (schedule evidence + venue us_premap now) | f01589b0e7e6dd4f4ac1220a1db81f53 | Y | aec-nfl-ten-bal-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| MIA Dolphins vs. MIN Vikings | 2026-10-04 20:05Z | 2026-10-04 16:05 | Y (schedule evidence + venue us_premap now) | c327223d76a3b7b0db8c6c104442f44b | N | aec-nfl-mia-min-2026-10-04 | Y | - | - | N | - | 0 | 0 | FRESHNESS (stage 2, before valuation): QUOTE_STALE_ON_ARRIVAL in every ledger row (REFUSED:2_FRESHNESS:QUOTE_STALE_ON_ARRIVAL x25) | REFUSED_BEFORE_VALUATION |
| DEN Broncos vs. SF 49ers | 2026-10-04 20:25Z | 2026-10-04 16:25 | Y (schedule evidence + venue us_premap now) | a7e7efb75a9792ac63480ae6328dca18 | Y | aec-nfl-den-sf-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| KC Chiefs vs. LV Raiders | 2026-10-04 20:25Z | 2026-10-04 16:25 | Y (schedule evidence + venue us_premap now) | e0cbd266dcf997089203a1b33ba0333d | Y | aec-nfl-kc-lv-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |
| LA Chargers vs. SEA Seahawks | 2026-10-04 20:25Z | 2026-10-04 16:25 | Y (schedule evidence + venue us_premap now) | 96d2373cda647873b971f268482bced0 | N | aec-nfl-lac-sea-2026-10-04 | Y | - | - | N | - | 0 | 0 | FRESHNESS (stage 2, before valuation): QUOTE_STALE_ON_ARRIVAL in every ledger row (REFUSED:2_FRESHNESS:QUOTE_STALE_ON_ARRIVAL x25) | REFUSED_BEFORE_VALUATION |
| DET Lions vs. CAR Panthers | 2026-10-05 00:20Z | 2026-10-04 20:20 | Y (schedule evidence + venue us_premap now) | a73a76599a4f3422803e57bd8f61b626 | Y | aec-nfl-det-car-2026-10-04 | Y | N | N | Y | REFUSE:SETTLEMENT_NOT_SUPPORTED | 0 | 0 | SETTLEMENT: Derek REFUSE SETTLEMENT_NOT_SUPPORTED; valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE | REFUSING_BY_POLICY |

**Aggregate:** NFL_EXPECTED=14 NFL_PROVIDER=14 NFL_VENUE=14 NFL_MAPPED=14 NFL_SETTLEMENT=0 NFL_PROBABILITY=0 NFL_EVALUATED=12 NFL_REFUSE=12 NFL_ENTER=0 NFL_PAPER_ORDERS=0 NFL_FILLS=0

## London game and date handling

The 09:30 ET London game kicks off 13:30Z; 13:30Z minus 4 h (EDT) = 09:30 on 2026-10-04, so it lands on the 2026-10-04 ET service day and the 2026-10-04 UTC day alike. It is matched to its provider event, exactly mapped, evaluated and refused by Derek on settlement policy.

- kickoff 2026-10-04 13:30Z = 2026-10-04 09:30 EDT (UTC-4); ET service day 2026-10-04; UTC day 2026-10-04; provider event c9d8ed8aa4889486eaf10a630138ede0; 25 cycles 10-04 05:32..10-04 11:22 UTC; Derek REFUSE:SETTLEMENT_NOT_SUPPORTED at 10-04 07:16 UTC
- DET at CAR kicks off 00:20Z on 2026-10-05 = 20:20 ET on 2026-10-04: it belongs to the ET service day 2026-10-04 (14 games) but to UTC day 2026-10-05, so a UTC-day slate would show 13. The receipt uses the ET day.
- Excluded: ATL at NO, aec-nfl-atl-no-2026-10-05, 00:15Z 10-06 = 20:15 ET Monday 10-05.

## Where games disappeared

- MIA Dolphins vs. MIN Vikings and LA Chargers vs. SEA Seahawks: present at PROVIDER EVENT and EXACT MAPPING; first stage absent = NORMALIZED (freshness, ledger stage 2): QUOTE_STALE_ON_ARRIVAL in all 25 ledger rows since 05:32Z, so no valuation and no Derek evaluation. Named policy refusal; the 30 s freshness rule is unchanged.
- The other 12: evaluated by Derek, REFUSE SETTLEMENT_NOT_SUPPORTED (valuation refusals DRAW_HANDLING_NOT_RECONCILED, VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE; probability not supported: MARKET_NOT_IN_SUPPORTED_SET, NO_QUALIFIED_MODEL).
- Unexplained games: none.

## Definitions

- EXPECTED: research/nfl_2026_10_04_expected.json (14) FULL JOIN production us_premap now (14, all still listed) FULL JOIN provider events on the ET day (none unmatched).
- NORMALIZED: the provider quote passed the probability+freshness stage at least once (raw ledger stage >= 3) or a sealed valuation exists. The funnel's REACH convention counts a stage-2 row that carries a venue contract as reach 4, so the league funnel counts all 15 NFL events as normalized; per game the raw stage is shown.
- SETTLEMENT / PROBABILITY SUPPORTED: from the newest valuation's refusals and Derek's refusal; '-' = not reached.
- PAPER ORDER / FILL: paper_orders (role ENTRY) / paper_fills on the contract since 2026-10-02, any strategy.
