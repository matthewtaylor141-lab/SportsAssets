# Why Derek has only 6 and 5 eligible fixtures (research-sql run 36806967659, 02:41Z)

Source: research/derek_evidence_funnel.sql (read-only; the observer's own eligibility rules). Threshold unchanged: 40 distinct labelled fixtures per cohort.

## The funnel
| stage | DISPLAYED_PRICE_AGE_UNKNOWN (CALIBRATION_ONLY) | EXECUTABLE_PRICE_CURRENT (ENTRY_DECISION) |
|---|---|---|
| stored valuations | 717 (2026-09-30 02:56Z to 10-01 02:26Z) | 1,126 (2026-09-24 14:23Z to 09-27 19:36Z) |
| refused: no Pinnacle price | 50 rows / 20 fixtures | 399 / 52 |
| refused: no venue price receipt | 0 | 716 / 53 |
| observable | 667 / 29 fixtures | 11 / 6 fixtures |
| labelled (settled, verified basis) | 26 rows / 6 fixtures | 10 rows / 5 fixtures |
| **eligible vs 40** | **6 (short 34)** | **5 (short 35)** |
| research observations the app recorded | 667 / 29 (matches) | 11 / 6 (matches) |

Labelled observable fixtures by settlement day: displayed 4 (09-30) + 4 (10-01, partial day); executable 12, 18, 22, 1 on 09-25..28 before the receipt filter, 5 after it.

## Causes, in order of size
1. **The executable cohort stopped growing on 2026-09-27 19:36Z.** No ENTRY_DECISION valuation has been written since. The latest cycle (E7) evaluated 0 and wrote 0 entry rows across 55 provider events. Refusals: NO_PINNACLE_ON_EVENT 33, QUOTE_STALE_ON_ARRIVAL 8, NO_VENUE_CONTRACT_FOR_EVENT 5, VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM 5, plus VENUE_BOOK_CURRENCY_NOT_ESTABLISHED 5 and VENUE_BOOK_READ_FAILED 2 (Derek's waiting_on). Only calibration-only records are written now. At the current rate the executable cohort gains 0 fixtures a day, so no date can be estimated until entry rows resume.
2. **716 of the 1,126 executable rows carry no price-receipt time, and the evidence was never recorded.** E9: 1,100 rows have no `freshness_evidence` at all. Most are `{"evaluated": false, "why": "no execution estimate"}` or predate the freshness instrumentation, which first appears 2026-09-26 03:03Z, with `venue_clock` only from 17:40Z. The observer refuses them correctly. Nothing is stored under another path, so these rows cannot be recovered without inventing a receipt. They are lost as executable evidence. This is not a current defect.
3. **Collection gaps.** There are no valuations of any kind from 2026-09-28 00:00Z to 09-30 02:56Z (about 51 h, cause not yet established). There are none from 09-30 21:00Z to 10-01 01:00Z (the database outage).
4. **The displayed cohort is young, not broken.** It started 09-30 02:56Z, and 641 of its 667 observable rows are unsettled events. It is labelling about 4 fixtures a day. If that holds and collection stays up, it reaches 40 in roughly 8–9 days. That is an extrapolation from 1.5 days of data, not a forecast.
5. **Small label-join gap.** 8 executable fixtures from 09-24 and 09-27 are still unlabelled although their events are 3+ days old (e.g. aec-mlb-az-col-2026-09-24, aec-mlb-bal-nyy-2026-09-27). Their outcome_known is false and they have no basis. Even fixed, this adds at most 8 observable-but-receipt-less fixtures, which remain ineligible.

## Engineering defects to investigate next (not yet proven)
- Why the entry lane writes no ENTRY_DECISION rows since 09-27: VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM and VENUE_BOOK_CURRENCY_NOT_ESTABLISHED look like identity/mapping and book-currency defects, not market facts. QUOTE_STALE_ON_ARRIVAL is processing delay. NO_PINNACLE_ON_EVENT may be provider coverage.
- The 51-hour gap on 09-28..30.
- The label join for the 8 old unlabelled fixtures.
