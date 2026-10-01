# Derek's research-evidence funnel, correctly scoped (v2; research-sql run 36808311057, 02:58Z)

**This replaces the v1 note.** v1 applied the backfill observer rules to every row and described the 09-28..09-30 silence as an outage. Both were wrong.
- The stored-timestamp requirements apply only to backfilled rows (`observation_from_row`: `if mode != MODE_LIVE`).
- The silence was the deliberate P5 currency rule (below), not a collection failure.

The threshold is unchanged: 40 distinct labelled fixtures per cohort (MIN_TRAIN_EVENTS).

## Counts and latest timestamps, separately
| series | rows | first | latest | last 24 h |
|---|---|---|---|---|
| ENTRY_DECISION valuations | 1,126 | 2026-09-24 14:23:52Z | **2026-09-27 19:36:11Z** | 0 |
| CALIBRATION_ONLY valuations | 723 | 2026-09-30 02:56:04Z | **2026-10-01 02:45:36Z** | 716 |
| research observations, displayed cohort, backfill | 653 / 28 fixtures | 09-30 02:56Z | decided 09-30 20:54:33Z (recorded 10-01 01:33Z) | — |
| research observations, displayed cohort, **live** | 19 / 8 fixtures | 10-01 01:30:57Z | decided **02:45:33Z**, recorded 02:49:31Z | 19 |
| research observations, executable cohort, backfill | 11 / 6 fixtures | 09-26 21:58Z | 09-27 19:36:11Z | 0 |

The "seven new valuations tonight" are CALIBRATION_ONLY rows. Calibration collection is running, and the live observer records them. ENTRY_DECISION rows are what stopped.

## Authoritative eligible count (the fit's own join: observations × verified settlement label)
| cohort | observations | labelled observations | eligible fixtures | short of 40 |
|---|---|---|---|---|
| DISPLAYED_PRICE_AGE_UNKNOWN | 672 | 27 | **6** | 34 |
| EXECUTABLE_PRICE_CURRENT | 11 | 10 | **5** | 35 |

## Exclusions, by kind
| kind | displayed cohort | executable cohort |
|---|---|---|
| **Missing original receipt timestamp** (backfill rule only; the time was never stored, so none is fabricated) | 0 | 716 valuations / 53 fixtures. Written 09-24..27 before the venue-clock receipt was instrumented. 1,100 of 1,126 rows carry no freshness evidence at all. |
| **Missing proof of executable freshness** (P5 rule, d66e89e, live from build ad95d69 at 09-27 19:37:17Z) | **Does not apply by design** (verified below) | Since 19:37Z no venue read can prove its prices current. The only accepted proof is a market-data subscription bound by a venue timing guarantee the venue has not published (bettor_venue_currency.py:146, bettor_stream_currency.py:431/522-526), and BETTOR_MARKET_SUBSCRIPTION is off. So no ENTRY_DECISION row can be written. This is the known restriction working as coded, not an outage. |
| **Missing verified settlement label** | 645 observations / 28 fixtures unsettled or not yet joined (events still pending) | 1 observation (1 fixture) unsettled or not joined. Separately, 7 pre-migration-108 rows with no recorded buy side are held from labelling on purpose. One 09-27 fixture settled at 0.485 and is deliberately not treated as a void. |
| **Cohort-specific: no Pinnacle probability** | 51 valuations / 20 fixtures | 399 / 52 |

## The displayed cohort still runs without executable freshness (verified)
- `cohort_of('CALIBRATION_ONLY') = DISPLAYED_PRICE_AGE_UNKNOWN`. In live mode `observation_from_row` checks only fixture, price in (0,1) and a Pinnacle probability. It does not check freshness.
- Every displayed observation records `price_basis = DISPLAYED_BOOK_CURRENCY_UNESTABLISHED` and `price_timing_basis = "book currency BOOK_CURRENCY_NOT_ESTABLISHED (mechanism NO_MECHANISM_AVAILABLE) …"` (10 rows: `CONTRADICTED_BY_THE_CONTRACT`).
- Unknown timing stays unknown. 653 rows have `price_source_ts_basis = NOT_CARRIED_ON_THE_VALUATION_RECORD`. The 19 newer rows carry the venue transact time labelled "provenance, not an age". No receipt or age was fabricated.
- Funded/entry admission is unchanged: Derek's paper decisions still apply the 30 s Pinnacle rule and the entry evidence rules, and the displayed cohort cannot authorise an entry.

## Accrual
Displayed-cohort labelled fixtures: 4 settled 09-30, 4 settled 10-01. That is 1.5 days of data, so any date to reach 40 is an extrapolation (about 8–9 days if collection and the settlement rate hold). The executable cohort gains nothing until the P5 currency proof exists. That is an owner and venue decision (the timing guarantee plus the subscription), detailed in research/derek_entry_lane_investigation_2026-10-01.md.

## Engineering defects that cost evidence (from the entry-lane investigation)
1. Book reads have no deadline: a 10 s timeout is charged to every later candidate, and the timing record is discarded on timeout. On 09-30, 257 of 448 stale-on-arrival refusals were our own processing delay.
2. No alias table for venue team spellings: 8 recurring fixtures (turkiye, czechia, fc bayern munchen, …) refused as VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM.
These add calibration evidence now, and entry evidence only after P5's proof exists.

## Paper experiment status
Zero simulated executions. Derek refuses every entry (NO_RESEARCH_MODEL_CANDIDATE_EXISTS) because neither cohort has 40 eligible fixtures.
