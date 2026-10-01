# Why the Pinnacle-devig entry lane writes no ENTRY_DECISION rows (2026-10-01)

DRAFT. Questions 1 and 3 are answered from code and deploy history. Production SQL (research/derek_entry_lane_investigation.sql) is pending.

## Q1. What changed at 2026-09-27 19:36Z

**Deploy history** (render-ops `deploys`, run 36807198866):
- 2026-09-27 12:55Z: 1d2db59 deployed. It does not contain d66e89e or ff2c87f.
- **2026-09-27 19:36:06Z: ad95d69 deployed (live 19:37:17Z). It contains ff2c87f (13:21Z) and d66e89e (14:16Z).** The last ENTRY_DECISION row (19:36Z) was written by the outgoing process.
- 09-28 03:32Z f9f63d8, 09-28 13:03Z c3d0cfc, 09-29 21:15Z aca3564: none contain 2c97c6e.
- **2026-09-30 02:52Z: 093168f deployed. It contains 2c97c6e (calibration-only writer) and 8074042.** The first CALIBRATION_ONLY row is at 02:56Z.

**Code at ddd4050.** d66e89e replaced the venue-clock age gate with a "book currency" verdict:
- `ext_pinnacle_loop.py:2830` calls `vc.evaluate(...)`. At `:2867`, any verdict other than ESTABLISHED returns `VENUE_BOOK_CURRENCY_NOT_ESTABLISHED` with `ok: False`.
- `bettor_venue_currency.py:146`: `ESTABLISHING_MECHANISMS = (M1_LIVE_SUBSCRIPTION,)`. M2 (a 304 revalidation) and M3 (Date minus Age) are partial and can never admit (`:150`).
- `bettor_stream_currency.py:431`: `P5_DOCUMENTED_TIMING` has `"available": False`. Because of that, `:522-526` sets `M1_STATUS = M1_NOT_AVAILABLE_ON_THIS_FEED`, and `evidence_for()` returns no subscription input (`M1_FEED_TIMING_NOT_DOCUMENTED_P5`, `:825`).
- `ext_pinnacle_loop.py:4609 book_currency_evidence` documents the result: "every venue read reaches BOOK_CURRENCY_NOT_ESTABLISHED and refuses".
- An ENTRY_DECISION row is persisted only after an ok venue read. After a currency refusal, 2c97c6e routes the same evaluate/persist path to a sealed CALIBRATION_ONLY record (`:1408 CALIBRATION_ONLY_AFTER`, `:7814`).

**Conclusion (pending SQL confirmation).** The lane is not stopped by a control row (`_running`, `:1445`). It is not stopped by upstream refusals either, and it is not a misclassification. It is a deliberate, hard-coded evidentiary policy. Since ad95d69, no venue read can be admitted, because the only establishing mechanism (M1) requires a venue-published timing contract (P5), and that contract does not exist. This is a policy fact, not a defect. No upstream repair can produce a single ENTRY_DECISION row while P5 is unavailable.

## Q3. The silent window 09-27 19:37Z .. 09-30 02:56Z
The silent window is the same cause as Q1, before the calibration-only writer existed. With every read refused on currency, and no writer for refused reads until 2c97c6e (deployed 09-30 02:52Z), nothing reached `external_valuations`. 2c97c6e's own commit message says so: "The calibration cohort stopped growing on 2026-09-27 ... since d66e89e every read refuses ... so no row was written". The window starts at 19:37Z on 09-27, not at 00:00Z on 09-28. The earlier report's 09-28 boundary was a day-bucket artefact.
