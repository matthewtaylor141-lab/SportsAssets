# Where the fair-value chain breaks — 2026-09-28

**Priority 2:** *"Trace and repair the absent fair-value inputs. Identify where provider evidence, mapping, devig, calibration, persistence or consumer lookup fails. Separate missing engineering from unavailable evidence using current candidate records."*

Measured on **all 1,126 current candidate records**, not a sample. Query: `research/fair_value_chain_2026-09-28.sql`, run read-only against the production database through `research-sql` (run 245, `psql exit=0`, file `sha256 e5c194d5…`).

---

## 1 · The answer, in one line

**Five of the six links you named do not fail at all.** Provider evidence, mapping, the de-vig, calibration, persistence and consumer lookup account for **zero** of the 399 rows. Every one fails at a link you did not list: **quote freshness**.

| Link | Rows failing first here |
|---|---:|
| Provider scope / book absent / partial outcomes / bad odds | **0** |
| **Quote freshness (`QUOTE_STALE`)** | **399** |
| Mapping absent / ambiguous / selection unmatched | **0** |
| Period, line or settlement-rule mismatch | **0** |
| De-vig method | **0** |
| No named cause *(would be a gap in the codes)* | **0** |

`bettor_pinnacle_devig.valuation()` **returns early** on `QUOTE_STALE`, before `map_selection`. So a stale quote yields no probability, hence no fair value, hence no break-even limit and no execution estimate. The downstream absences are consequences, not independent failures.

### This corrects my own census

I previously reported blocker 1 as *"no independent fair value / no qualified model — 399 rows — engineering"* and blocker 2 as *"`QUOTE_STALE` — 399 rows — engineering"*, as **two blockers with separate entries**. The cross-tab settles it — equal counts do not establish equal sets, so I checked rather than assumed:

| `no_fair_value` | `QUOTE_STALE` | `QUOTE_HAS_NO_TIMESTAMP` | rows |
|:--:|:--:|:--:|---:|
| f | f | f | 727 |
| **t** | **t** | f | **399** |

Two rows in the cross-tab and no others. They are **the same 399 rows** — one cause and its effect, counted twice in my blocker table.

### And the consumer is not the problem either

**Zero** rows carry a probability *and* any of `INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED`, `FAIR_VALUE_IS_THE_VENUE_BENCHMARK`, `NO_QUALIFIED_MODEL` or `BREAK_EVEN_LIMIT_NOT_COMPUTABLE_WITHOUT_A_VALUATION`. Wherever a fair value is produced, it is read. A persisted-but-unread fair value would have been a different failure; it is not this one.

---

## 2 · Missing engineering vs unavailable evidence — the split you asked for

`pinnacle_age_s` is a **sum**: how old the price already was when the provider gave it to us, plus how long we then took. Both clocks are already on every row, so it decomposes exactly:

```
provider_lag = received_at − observed_at      (old on arrival)
our_lag      = age_s − provider_lag           (what we then added)
```

| | min | median | p95 | max |
|---|---:|---:|---:|---:|
| **Provider lag** | 1.4 s | **14.6 s** | 352.4 s | 822.7 s |
| **Our processing delay** | 0.0 s | **28.7 s** | 64.7 s | 94.8 s |

| Verdict | Rows | Share | What it is |
|---|---:|---:|---|
| **Already stale on arrival** (`provider_lag > 30 s`) | **103** | 26% | **Evidence unavailable at the required freshness.** No engineering here changes it. |
| **We made it stale** (`provider_lag ≤ 30 s`, `age_s > 30 s`) | **296** | **74%** | **Ours.** |

On the median row the provider handed us a quote with roughly **15 seconds of the 30-second budget still unspent**, and we then spent **29 seconds** before the decision instant. The module's own freshness note already said which of the two is ours — *"a slow provider is a coverage fact, our own delay is ours to fix"* — and this is the first time the split has been measured across the population.

**It is structural, not intermittent.** Per day, the provider's median lag never moves and our contribution never stops:

| Day | Rows | Already stale on arrival | **We made it stale** | Fresh enough | Provider lag (median) |
|---|---:|---:|---:|---:|---:|
| 2026-09-24 | 262 | 9 | **58** | 195 | 10.0 s |
| 2026-09-25 | 431 | 57 | **84** | 290 | 11.4 s |
| 2026-09-26 | 258 | 24 | **87** | 147 | 9.7 s |
| 2026-09-27 | 175 | 13 | **67** | 95 | 11.1 s |

---

## 3 · Why our 29 seconds is not a defect, and concurrency cannot remove it

I expected to find an avoidable serialisation. I found a deliberate one.

`venue_pace.pace` is a **process-wide serial gate**: a call blocks until `MIN_GAP_S` (0.35 s) has passed since the last paced request *anywhere in the process*. It exists because **the venue 429s a board walk above ~3 req/s**, and it is shared with the protected collector so that measurement reads cannot starve the money path.

Each event needs several paced venue reads — `venue_quote`, the settlement-evidence catalogue read — **before** its decision instant. And `received_at` is stamped **once per sport**, then up to `MAX_PER_CYCLE = 40` events are evaluated from it. So event *N* carries a quote aged by every preceding event's reads as well as its own. Median position in that queue × ~2–3 s of paced reads ≈ the 28.7 s measured.

Three things I checked and rejected:

- **Making the venue reads concurrent.** The gate would serialise them anyway, and widening it would starve the copy lane's quote and send.
- **Moving the decision instant earlier.** The instant is taken *after* the network reads on purpose, and correctly: *"a decision cannot be stamped fresher than the work that produced it."* Moving it would manufacture freshness rather than achieve it.
- **Computing the de-vig before the venue read.** Tempting, since the de-vig needs no venue book. But the 30-second rule governs the age **at the decision**, which is the right rule for trading: what matters is how old the price is when you act on it, not when you first read it.

---

## 4 · The repair, and its price

**The only lever is how many events share one provider fetch — and it is paid for in credits.** The provider bills per request × market × region: one `fetch_odds` is ~18–21 credits, about 60 per cycle across three sports today. Halving the events per fetch roughly halves our accumulated delay and roughly doubles the odds credits.

So I built the lever and **defaulted it to today's behaviour**:

```python
EVENTS_PER_ODDS_FETCH = MAX_PER_CYCLE     # 40 — no extra fetch is ever issued
```

At the default the credit spend is unchanged, so the change carries **no resource decision with it**. Lowered deliberately, the loop re-fetches the sport's odds mid-loop and refreshes the events still to come. Every cycle row now reports `odds_freshness` — the setting, whether it is the default, the re-fetch count and the failure count — so a change in credit spend is attributable rather than guessed at.

**Three properties the implementation holds, each with a test:**

1. **A refresh refreshes the prices, not only the stamp.** Taking the new `received_at` without the new payload's outcomes would be a fresh-looking age on an old quote — precisely the false certificate this module's freshness note warns about twice.
2. **A failed re-fetch keeps the existing quote** and lets it age normally, so `QUOTE_STALE` refuses it by name. Dropping the sport would turn a provider hiccup into missing coverage.
3. **It never re-decides an evaluated event.** It is not a retry.

### A bug I wrote and caught before it shipped

My first version iterated `for event in got["events"]` and rebound `got` on a refresh. Python binds the list once, so the fresh payload would have been **fetched, paid for in credits, and then ignored** for every event but the current one — full cost, no freshness. The loop now iterates a local list by index and overwrites the entries from the current position onward. `test_a_refresh_replaces_the_events_still_to_come` asserts that shape directly.

### What I am not claiming

I have **not** measured that lowering the knob converts those 296 rows. The arithmetic says it should — 14.6 s of provider lag plus a bounded share of our delay fits inside 30 s — but that is a prediction, and the only thing that settles it is a cycle run at a lower setting with the split re-measured. **That run costs credits and is a resource decision, so it is yours to authorise.** I have made the knob, the reporting and the tests ready for it.

---

## 5 · What the 727 rows that *did* get a fair value then hit

| | |
|---|---:|
| Rows with a probability | **727** |
| Markets · sports | 53 · 1 |
| Quote age: min / median / max | 1.2 s / 15.9 s / **30.0 s** *(censored at the limit, as expected)* |
| With an execution estimate | 543 |
| With a computed edge | 727 |
| **With a positive edge** | **67** |
| **Admissible** | **0** |

The next blockers, on rows that cleared the fair value:

| Code | Rows |
|---|---:|
| `NO_ACTION_HAS_POSITIVE_NET_EDGE` | 674 |
| `EXECUTION_ESTIMATE_NOT_IDENTIFIED` | 668 |
| `RISK_GATE_BLOCKED` | 547 |
| `NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT` | 488 |
| `SIZING_POLICY_NOT_APPLICABLE` | 488 |
| `VOID_ABANDONMENT_RULE_NOT_ESTABLISHED` | 406 |
| `VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE` | 306 |
| `OVERTIME_RULE_NOT_ESTABLISHED` | 180 |
| `VOID_ABANDONMENT_BOOK_RULE_NOT_HELD` | 15 |

**67 candidates had a positive edge and none was admissible.** That is the honest state of the economics: the fair value is not the last obstacle, and fixing freshness would move rows from "not evaluated" into "evaluated and refused for a named economic or settlement reason" — which is progress in the measurement, **not** progress toward a profitable trade. I am not presenting it as the latter.

`VOID_ABANDONMENT_RULE_NOT_ESTABLISHED` at 406 rows and `..._CONFLICTS_WITH_BOOK_RULE` at 306 are the largest settlement gaps and remain owned by the venue, per the earlier settlement census.
