# The latency repair: four levers, and events-per-fetch is the worst of them

**The owner's instruction:** *"Do not assert that events-per-fetch is the only lever without establishing that."* I asserted it and had not established it. Having looked, **it is not the only lever and it is the most expensive one.** Three better levers exist and two cost nothing.

**And the state of the repair, stated first:** the knob shipped at its current-behaviour default, so **the production delay is unchanged and none of the 296 affected candidates has been recovered.** Nothing below is active. This is a repair plan with a measurement, not a repair.

---

## 1 · Where the 28.7 s actually goes

Per event, before the decision instant, the loop makes **paced** venue calls through `venue_pace.pace()` — one process-wide gate, `MIN_GAP_S = 0.35`, synchronous, claimant holds the gate through its sleep:

| Call site | Function | Per event? | Paced |
|---|---|:--:|:--:|
| `venue_quote` → `_read_book_blocking` | the executable book | **yes** | ✓ |
| `venue_settlement_evidence` → `_read_venue_rules_blocking` | the venue's rules | **cached** | ✓ on miss |
| `_read_resolution_blocking` | outcome join | once/cycle | ✓ |
| `venue_event_progress_probe(limit=1)` | progress | once/cycle | ✓ |

**The rules read is already cached** — `_RULES_CACHE` with `RULES_CACHE_TTL_S = 3600.0`. So I must not propose caching it; it is done. After warm-up the dominant per-event paced cost is **the book read**: 0.35 s of gate plus ~0.65 s of observed HTTP latency ≈ **1 s per event**, and `received_at` is stamped once per sport for up to `MAX_PER_CYCLE = 40` events. Event *N* therefore carries a quote aged by the preceding events' reads. Median measured our-lag 28.7 s is consistent with the median candidate sitting ~20–28 events into that queue, with cache misses on new fixtures adding a second read.

## 2 · Process-local pacing versus coordination across services — the distinction

**`venue_pace` is process-local.** Its own text: *"This gate is process-wide: a call blocks until MIN_GAP_S has passed since the last paced request ANYWHERE IN THE PROCESS."* Every site it cites (`price_path`, `mirror_shadow`, `mirror_live`) is in the same process.

**The venue's limit is not process-local.** It is an account/IP limit — the pacer exists because *"the venue 429'd a board walk above ~3 req/s"*, which is the venue counting **all** our requests.

**So there is no coordination across services at all.** The protected worker (`bettor_live_loop`, pinned `f5d1c05`) is a **separate Render service** — a separate process — and `venue_pace` cannot see its requests, nor it ours. Two consequences, and I previously stated neither:

- **The pacer is an approximation, not a bound.** Two services each pacing at 0.35 s can present ~5.7 req/s to the venue, which is exactly the failure its docstring describes happening *within* one process. Across services nothing prevents it.
- **Reducing this process's reads helps the shared budget**, and is therefore strictly better than any change that leaves read volume flat. That reframes which levers are good: **fewer reads beats better-timed reads.**

A real cross-service bound would need a shared token bucket (a database row or Redis). That is unbuilt, and I am not proposing it inside a latency fix — it is a separate piece of work with its own failure modes.

---

## 3 · The four levers

### Lever A — Do not spend a venue read on a candidate that is already doomed on freshness  ★ recommended

**Zero credits. Fewer venue requests. No relaxation of any rule.**

`provider_lag = received_at − observed_at` is computable **immediately after `pinnacle_h2h`**, before `resolve_venue_identity` and before the paced book read. If `provider_lag` already exceeds `MAX_QUOTE_AGE_S = 30`, the row **will** refuse `QUOTE_STALE` whatever happens next — the decision instant only moves later. Today the loop spends a paced book read on it anyway, then refuses.

**103 of 399 stale rows were already stale on arrival.** Each consumed a book read that could not have changed its outcome. Skipping them:

- returns pacer capacity to the events that *can* still qualify, shortening their queue and so **reducing their own lag**;
- returns venue requests to the shared account budget;
- changes no verdict — those rows still refuse, with the same code, recorded with the same evidence. **The refusal must still be written**, or coverage would silently shrink.

**Extension, with a margin:** skip when `provider_lag + expected_processing > 30`, where `expected_processing` is measured from the cycle's own history rather than assumed. A candidate with 29 s of provider lag cannot survive a 1 s read either. This needs care — too aggressive a margin discards candidates that would have qualified — so the margin starts at 0 (pure already-doomed filter) and is only raised on measured evidence.

### Lever B — Prioritise candidates so the fresh window is spent on plausible ones  ★ recommended

**Zero credits. Same read volume.**

Order matters because the quote ages down the queue. Today the order is the provider's array order — arbitrary. **674 of 727 rows that got a fair value refused with `NO_ACTION_HAS_POSITIVE_NET_EDGE`**; only 67 had a positive edge. If the events likeliest to qualify were evaluated **first**, they would get the freshest quotes.

Ordering can use the previous cycle's observations — *for ordering only, never for economics*. That distinction is the whole safety argument: a stale prior that mis-orders the queue costs nothing but ordering, whereas a stale prior in the edge calculation would be a fabricated advantage. **The economics keep reading only the current book.**

### Lever C — Reduce redundant reads within a cycle

**Zero credits.** Two events on the **same** venue market (a slug appearing twice across provider events) currently read the book twice. A per-cycle book cache keyed by slug, with a TTL far shorter than the freshness bound, removes the duplicate. **I have not measured how often this happens**, so the benefit is unquantified and it is listed third for that reason, not because it is unsound.

### Lever D — Fewer events per provider fetch  ✗ not recommended first

**This is the lever I wrongly presented as the only one.** It costs **~18–21 provider credits per extra fetch** (~60/cycle today across three sports). Halving events-per-fetch roughly doubles odds credits.

It is the **last** lever to reach for because A, B and C reduce the queue that creates the lag, whereas D pays money to re-stamp a queue that is still just as long. **Use D only after A–C are measured and the residual lag is still binding.**

### If credits do turn out to be necessary — the concrete decision

| | |
|---|---|
| Change | `EVENTS_PER_ODDS_FETCH` 40 → 10 |
| **Incremental calls** | 3 extra fetches per sport per cycle × 3 sports = **9 extra fetches/cycle** |
| **Incremental cost** | ~18–21 credits each ≈ **+170 credits/cycle**; ~60 → ~230/cycle; ~7.2k → **~27.6k/day** |
| Expected benefit | bounds our-lag at ~10 events of queue instead of ~40. **Expected, not measured.** |
| **Enforcement limit** | a hard per-cycle fetch ceiling, refusing further fetches and recording `odds_refetch_budget_exhausted`, so a bug cannot run the budget away |
| Decision | **yours.** I will not spend 4× the provider budget on an expectation. |

---

## 4 · The measurement, and what it is not allowed to claim

Verification is the same instrument that produced the diagnosis — `research/fair_value_chain_2026-09-28.sql` — re-run on cycles written by the new build, with three rules:

1. **Stale-on-arrival stays a separate count.** `already_stale_on_arrival` is a **coverage fact about the provider** and no engineering moves it. Folding it into a "freshness improved" figure would credit us with the provider's behaviour. Lever A *skips* those rows earlier; it does not make them fresh.
2. **The claim is bounded to evaluability.** The measurable outcome is *rows reaching the economics instead of refusing at freshness* — `we_made_it_stale` falling, and `rows_with_probability` rising.
3. **No admission is promised.** Of 727 rows that already got a fair value, **67 had a positive edge and 0 were admissible.** Recovering 296 rows would move them from *not evaluated* to *evaluated and refused for a named economic or settlement reason*. **That is progress in the measurement and not progress toward a trade**, and the previous report's framing came close to blurring it.

**Acceptance for the repair:** `we_made_it_stale` materially below 296 on a comparable cycle count, `already_stale_on_arrival` reported separately and unchanged in kind, provider credits per cycle **unchanged** (A–C are free), and venue requests per cycle **down** rather than flat.

---

## 5 · What is true right now

| | |
|---|---|
| Diagnosis | complete, on all 1,126 rows |
| **Mitigation in force** | **none.** The knob defaults to current behaviour |
| **Candidates recovered** | **0 of 296** |
| Levers A, B, C | designed, **not implemented** |
| Lever D | implemented, **inactive**, and not recommended first |
| Cross-service request bound | **does not exist**; the pacer is process-local |

Levers A and B are small, zero-cost, and require no owner decision. They are the next implementation, and they need their own gate before deployment — the release now going out contains none of them.
