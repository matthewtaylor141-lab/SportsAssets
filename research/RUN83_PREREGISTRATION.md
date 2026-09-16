# RUN 83H — PROSPECTIVE ANALYSIS PRE-REGISTRATION

**Written before any forward observation is collected.** That is the whole point
of the document: once the curve exists, every choice recorded here would be a
choice made with knowledge of the answer.

Frozen at commit time. Any later change is an **amendment**, recorded below with
its date and reason, never a silent edit.

`mirror_live=false`. No orders. `ai_trades` / TRUEEDGE untouched.

---

## 1. THE PRIMARY QUESTION

> **How does executable acquisition price evolve after BETTOR first receives a
> source event?**

Note what the question is anchored to: **BETTOR first receipt**, not RN1's fill.
That anchor is chosen because it is the one instant we can stamp in a single
clock domain. Run 82 established that the source-fill anchor is not available
from retained data, and §7 below states the condition under which it could become
available prospectively.

## 2. PRIMARY SCENARIO

```
Q_A = 0.10 × source fill shares        PRIMARY
Q_B = source fill shares               SECONDARY
```

Carried unchanged from 81A-S / 81B so the forward curve and the historical
measurement are the same quantity. `Q_C` is **not** collected — it was a stress
scenario, and collecting it prospectively would invite it into a headline.

## 3. THE OFFSET GRID

Snapshots are scheduled from `bettor_first_receipt_monotonic`:

```
0 ms (immediate)  ·  100 ms  ·  250 ms  ·  500 ms  ·  1 s  ·  2 s
5 s  ·  10 s  ·  30 s  ·  60 s
```

Fixed here in advance. **No offset may be added, dropped or re-spaced after
looking at results.** A snapshot that fails to capture is recorded as a miss
with its reason — never backfilled from a neighbouring offset, and never
silently omitted.

**These are offsets from BETTOR FIRST RECEIPT.** They may be described as offsets
from RN1's true source fill only if §7 returns `IDENTIFIED` or `BOUNDED`, and
then only with the uncertainty attached.

## 4. PRIMARY ENDPOINTS

Per event, per offset `t`:

| endpoint | definition |
|---|---|
| `ASK_DETERIORATION_FROM_SOURCE(t)` | `best_ask(t) − p_h` |
| `QA_VWAP_DETERIORATION_FROM_SOURCE(t)` | `vwap_QA(t) − p_h` |
| `ASK_DETERIORATION_FROM_FIRST_OBS(t)` | `best_ask(t) − best_ask(0)` |
| `QA_VWAP_DETERIORATION_FROM_FIRST_OBS(t)` | `vwap_QA(t) − vwap_QA(0)` |
| **`FRACTION_ALREADY_PRESENT(t)`** | `(best_ask(0) − p_h) / (best_ask(t) − p_h)` |

`FRACTION_ALREADY_PRESENT` at t = 1 s, 2 s and 5 s is **the** number this run
exists to produce. It asks: of the deterioration that will have accumulated by
one second, how much had already happened before BETTOR could look at all?

Its denominator can be zero or negative. Pre-specified handling, so it cannot be
chosen later:

- denominator **> 0**: report the ratio.
- denominator **= 0** (exactly): `NOT DEFINED` for that event. Not dropped from
  the count — counted as `NOT DEFINED`.
- denominator **< 0** (price improved by `t`): `SIGN_INVERTED`, reported as its
  own category with its own count and dollar weight. **Never** folded into a mean
  with the positive cases, where it would silently offset them.

Aggregates are reported **three ways**, all of them, always: the equal-weighted
mean over events, the median, and the **dollar-weighted** figure
`Σ q·(…) / Σ q·(…)`. No single one of them is the headline.

## 5. SECONDARY ENDPOINTS

- the same five endpoints at `Q_B`, on rows where retained depth supports `Q_B`;
- `DEPTH_SUPPORT_RATE(t)` per scenario — the share of events whose retained
  ladder covers `q` at that offset;
- the top-of-book / depth split of `QA_VWAP_DETERIORATION_FROM_SOURCE(t)`, using
  81B's decomposition unchanged;
- `best_bid(t)` and the bid-ask spread, recorded but **not** an endpoint — they
  exist so a later question about exit-side economics does not require a second
  collection.

## 6. RULES THAT BIND THIS ANALYSIS

1. **No threshold is invented after seeing results.** This is the 81B correction,
   applied in advance rather than after. Conclusions are stated from the measured
   quantities; if a classification is wanted, the owner specifies the rule.
2. **`DEPTH_EXHAUSTED` is `NOT IDENTIFIED`** at that offset for that scenario.
   Never extrapolated, never imputed, never treated as a bound, never counted as
   zero deterioration.
3. **A missing snapshot is a miss**, carrying its reason. Miss rates are reported
   per offset beside every figure computed from that offset.
4. **Association, never latency cost.** Even a clean monotone curve would not
   establish that latency caused the deterioration: RN1's fill may itself carry
   information, market state may move contemporaneously, venue basis may differ,
   and busy markets may co-occur with slow handling.
5. **Fee and rebate economics are NOT IDENTIFIED** and are not modelled here.
6. **A book snapshot is not a fill.** Everything here is executable price
   *as observed*; queue position, partial fills, cancellation and our own market
   impact are all unmodelled. This is stated with every figure.
7. **Two independent implementations**, as 81A-S and 81B had — different numeric
   model and different control flow — with an event-identity digest, before any
   figure is reported.

## 7. THE SOURCE-FILL CLOCK QUESTION (83I)

Reported separately, and before the curve is described in source-anchored terms:

```
SOURCE_TO_RECEIPT_LATENCY = IDENTIFIED | BOUNDED | NOT IDENTIFIED
```

- **IDENTIFIED** — a source timestamp exists whose clock offset against BETTOR
  wall time is *measured* and retained, with uncertainty smaller than the
  intervals being claimed.
- **BOUNDED** — the offset is not measured but is provably confined to a range
  (e.g. a block timestamp constrained between two events we did stamp), and that
  range is narrower than the claim.
- **NOT IDENTIFIED** — otherwise. This is the current state, per run 82.

**Two timestamps rendering as UTC is not synchronisation.** No subtraction is
performed across domains merely because both formats parse. And no cross-clock
precision is ever claimed finer than the measured synchronisation uncertainty.

## 8. WHAT WOULD FALSIFY THE WORKING PICTURE

Stated now so it cannot be reinterpreted later. Run 82 leaves open whether the
deterioration is something a faster system could avoid. The forward curve
discriminates:

- If `FRACTION_ALREADY_PRESENT(1 s)` is **near 1** — the deterioration is
  essentially complete before BETTOR's first look — then speed after receipt
  cannot be the lever, and attention belongs on the receipt boundary itself or on
  not being reactive at all.
- If it is **well below 1** and the curve keeps rising through 1–5 s — much of the
  damage accrues *after* we could first see it — then acting faster after receipt
  has measurable value, and the size of that value is what the curve says.
- If the curve is **flat or falling** after the first observation, the premise
  that speed matters after receipt is contradicted for this population.

None of these three outcomes is the expected one. Writing all three down before
collection is what stops the observed one from being described as predicted.

---

## AMENDMENTS

*None. Any amendment appends here with date, reason, and what changed — the text
above is never edited in place.*
