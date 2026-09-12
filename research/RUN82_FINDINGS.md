# RUN 82 — SELECTION ROBUSTNESS AND TIMING IDENTIFIABILITY

`python3 research/gen/run82a.py` · `python3 research/gen/run82b.py`
Sealed bytes only, no database. `mirror_live=false`. `ai_trades` / TRUEEDGE untouched.

---

# 82A — IS THE DETERIORATION A SETTLEMENT-SELECTION ARTIFACT?

**No.** If anything the selected cohort is *milder* than the population it came
from.

The test is possible because `q·(best_ask − p_h)` contains no settlement term,
so it can be measured on events that could never enter 81B.

## Buckets — first-fail, mutually exclusive, closing to U2

```
sum of bucket events     214,609   control  214,609   MATCH
sum of bucket notional   48,327,293.76  control  48,327,293.76  MATCH
```

| bucket | events | source notional | Q_A cost | mean c/sh | med | %pos | Q_A deteri | det/cost |
|---|---|---|---|---|---|---|---|---|
| **FULL_U2_SNAPSHOT_V1** | 214,609 | $48,327,293.76 | $4,832,729.37 | 2.3245 | 1.0000 | 89.530% | $177,159.76 | **3.666%** |
| SETTLEMENT_ANALYZABLE_STRONG | 112,543 | $24,727,133.10 | $2,472,713.31 | 2.2659 | 1.0000 | 89.595% | $88,294.33 | **3.571%** |
| TIMING_QUARANTINE | 1,694 | $303,303.16 | $30,330.32 | 2.7340 | 1.0000 | 90.024% | $1,513.32 | 4.989% |
| RESOLVED_BUT_OTHERWISE_NOT_ANALYZABLE | **0** | $0.00 | — | — | — | — | — | — |
| UNRESOLVED | 78,587 | $19,910,944.73 | $1,991,094.47 | 2.3122 | 1.0000 | 90.456% | $73,066.97 | **3.670%** |
| NO_SETTLEMENT_METADATA | 3,963 | $466,829.18 | $46,682.92 | 2.6780 | 1.4000 | 71.158% | $2,604.53 | 5.579% |
| UNLINKED_CONDITION | 17,822 | $2,919,083.58 | $291,908.36 | 2.6307 | 1.0000 | 89.075% | $11,680.61 | 4.001% |
| STRUCTURALLY_INELIGIBLE_PRE_RESOLUTION | **0** | $0.00 | — | — | — | — | — | — |

> **A note on the bucket list.** `TOKEN_METADATA_MISSING` and
> `NOT_STRUCTURALLY_BINARY` fit none of the six requested buckets: the market row
> *is* present, and the ladder never reached the resolution test. They are
> reported under their own name rather than forced into a bucket whose label
> would then be false. Both are **zero**, so nothing turns on it.

## The key comparison — recomputed, not matched to priors

| | Q_A det / Q_A cost | % events `ask > p_h` |
|---|---|---|
| FULL_U2_SNAPSHOT_V1 | **3.6658%** | **89.5303%** |
| SETTLEMENT_ANALYZABLE_STRONG | **3.5707%** | **89.5951%** |
| UNRESOLVED | **3.6697%** | **90.4564%** |

Differences against FULL U2: the selected cohort is **−0.0951 pp / −2.594%
relative** on the deterioration rate and **+0.0648 pp / +0.072%** on the positive
share. `UNRESOLVED` — events that can *never* enter 81B — sits at **+0.0039 pp /
+0.105%**, i.e. indistinguishable from the whole.

The distributions agree even more plainly than the summary rates:

| bucket | p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|---|
| FULL U2 | 0.000 | 1.000 | 1.000 | 2.000 | 4.000 |
| SETTLEMENT_ANALYZABLE_STRONG | 0.000 | 1.000 | 1.000 | 2.000 | 4.000 |
| UNRESOLVED | 0.100 | 1.000 | 1.000 | 2.000 | 4.000 |

**Verdict: BROADLY SIMILAR.** Settlement selection does not manufacture the
deterioration; it slightly *understates* it. No post-hoc cutoff was applied —
the differences are reported and characterised from the distributions.

The one bucket that genuinely differs is `NO_SETTLEMENT_METADATA` (3,963 events):
p10 of **−3.000** cents and **19.657% negative**, against ~5% everywhere else.
Named, not explained — nothing here identifies why.

**Independent consistency check:** FULL U2 Q_A depth-supported total is
**$220,886.21**, reproducing 81A-S's Q_A figure exactly from a different program.

---

# 82B — TIMING IDENTIFIABILITY MAP

**Physical actionable latency is NOT IDENTIFIABLE from retained data, in every
lane.** The obstacle is clock domains, and it is not repairable after the fact.

## Clock provenance, traced from the write sites

| column | lane | clock | site |
|---|---|---|---|
| `trades.ts` | chain | REMOTE block timestamp **with a silent local wall-clock fallback** | `ingestion/chain.py:642` |
| `trades.ts` | poll | REMOTE Data-API trade timestamp | `ingestion/poller.py:107` |
| `trades.ts` | s1 | REMOTE block timestamp, **hash-verified, no fallback** | `ingestion/s1_emitter.py:1081` |
| `trades.detected_at` | all | **LOCAL Python wall clock** | `ingestion/pipeline.py:128` |
| `copy_probes.probe_at` | all | **LOCAL Python wall clock**, same process | `copy_probe.py:110` |
| `copy_probes.reaction_s` | all | **local wall − remote clock** | `copy_probe.py:135` |

Three defects beyond the domain crossing itself:

1. **`chain.py:642` has a silent fallback.** `result.get("timestamp", hex(int(time.time())))` — an RPC returning 200 with no timestamp substitutes **our own wall clock**, stored as though it were block time. **Nothing in the retained row distinguishes such rows.** This is 91.165% of U2.
2. **`detected_at` on the poll lane has two writers** — the live poller and the missed-fill reconciler — indistinguishable in the row. A reconciler row's `detected_at` is a catch-up time, arbitrarily far from `ts`.
3. **No monotonic clock appears anywhere in this path.**

## What the retained differences actually look like

**CROSS-DOMAIN — `detected_at − ts` — NOT an elapsed time:**

| lane | n | mean | p10 | p50 | p90 | min | max | **% < 0** |
|---|---|---|---|---|---|---|---|---|
| A_CHAIN | 195,648 | −0.588 | −1.166 | −0.708 | −0.137 | −1.301 | 119.138 | **92.04%** |
| A_POLL | 17,365 | 76.054 | 31.228 | 80.474 | 113.082 | −0.013 | 120.000 | 0.01% |
| A_S1 | 1,596 | 4.573 | 3.558 | 4.159 | 4.814 | 3.241 | 40.639 | 0.00% |

92% of the chain lane is **negative** — we "detected" the fill before it
happened. Elapsed time cannot be negative; clock disagreement can. The poll
lane's max is exactly **120.000**, the censor showing through in the data.

**SAME DOMAIN — `probe_at − detected_at` — the only valid elapsed time:**

| lane | n | mean | p50 | p90 | p99 | max |
|---|---|---|---|---|---|---|
| A_CHAIN | 195,648 | 0.016 | **0.007** | 0.028 | 0.106 | 67.412 |
| A_POLL | 17,365 | 0.170 | 0.017 | 0.402 | 2.395 | 7.694 |
| A_S1 | 1,596 | 0.014 | 0.008 | 0.021 | 0.091 | 2.007 |

**BETTOR goes from ingest to first book read in about 7 milliseconds.** That is
the single most consequential number in run 82.

`reaction_s` self-consistency: stored vs recomputed `probe_at − ts` agree to
**0.0005 s** across all 214,609 rows.

## U2 is a censored sample on the very quantity in question

A probe row exists only when **side == BUY** (`copy_probe.py:100`), the fill was
**fresher than 600 s** at ingest (`pipeline.py:274`), and computed detection
latency was **≤ 120 s** (`copy_probe.py:38, :104`). U2 cannot contain a slow
detection even if one occurred, and nothing in the data says so.

## S1 diagnostic

```
S1_PHYSICAL_LATENCY_ANALYSIS = NOT IDENTIFIED
```

The pre-specified bucket table is **not run**, under the rule given for it. S1's
`ts` is the strictest provenance in the tree — hash-verified block time, no wall
fallback — and it *still* fails, because the obstacle is the boundary between the
chain's clock and ours, not the quality of the parse. A better parser cannot fix
an unmeasured offset. S1 is also 1,596 events (0.744% of U2).

## Association on the one valid interval

Not the pre-specified table — that one needed source-fill boundaries. This is
`probe_at − detected_at`, BETTOR's own handling, offered because it is the only
elapsed time the data contains.

| bucket | n | source notional | med s | mean c/sh | % pos | Q_A deteri | det/cost |
|---|---|---|---|---|---|---|---|
| 0-1s | 213,779 | $48,229,653.76 | 0.007 | 2.3218 | 89.582% | $176,355.14 | 3.657% |
| 1-2s | 563 | $69,136.51 | 1.393 | 3.5143 | 78.686% | $795.94 | 11.513% |
| 2-5s | 239 | $27,010.41 | 2.614 | 2.1145 | 70.293% | −$9.98 | −0.370% |
| 5-10s | 27 | $1,484.44 | 5.663 | 0.7392 | 77.778% | $18.70 | 12.601% |
| 10-30s, 30-60s | 0 | — | — | — | — | — | NOT TESTED |
| >60s | 1 | $8.64 | 67.412 | −1.0000 | 0.000% | −$0.04 | −4.167% |

99.6% of events are in the first bucket. **The relationship is not monotone** —
which is itself worth stating: that is not the shape a clean latency effect
makes. This is an **association**, never a latency cost. No causal identification
exists.

---

# THE FOUR PROPOSITIONS

**A. Reactive-copy economics are materially worse by first retained
observation — SUPPORTED.** 81B's figures, robust to selection per 82A.

**B. The deterioration is primarily caused by BETTOR's internal processing
latency — NOT IDENTIFIED.** The one measurable internal interval is ~7 ms, far
too small to be a primary cause of anything. But the segment *before* it —
venue fill reaching us — is exactly the cross-domain boundary. If B were true,
the cause would have to lie entirely in the unmeasured segment.

**C. Reducing internal latency to 1–2 seconds would recover the edge —
CONTRADICTED, on the segment that is measurable.** BETTOR already goes from
ingest to first book read in **7 ms** — over two orders of magnitude faster than
the proposed target — and **the full deterioration is already present in the book
read taken at that instant**. A 1–2 second target is not an improvement on this
segment; it is a regression. Whether the unmeasured venue-to-ingest segment can
be shortened remains NOT IDENTIFIED.

**D. The opportunity is already gone before BETTOR could possibly observe his
fill — NOT IDENTIFIED.** This needs our first observation dated against the
*venue's* clock, which is the crossing the data does not contain. "Already gone"
cannot be separated from "gone while we were getting there."

# DECISION TABLE

| # | question | verdict | what would identify it |
|---|---|---|---|
| 1 | Is first-observation deterioration real? | **SUPPORTED** | already identified |
| 2 | Robust to settlement selection? | **SUPPORTED — broadly similar**; selected cohort is milder | already identified |
| 3 | Top-of-book or depth? | **IDENTIFIED**: 76.7% top-of-book at Q_A (77.2% full U2); depth co-dominant only far above copy size | depth beyond what was retained: NOT IDENTIFIED |
| 4 | Elapsed physical latency, historically? | **NOT IDENTIFIED** | paired timestamps in one domain + recorded clock-sync quality |
| 5 | Did engineering latency cause it? | **NOT IDENTIFIED** | as (4), plus a market-data snapshot stamped in our own domain |
| 6 | Would a 1–2 second system solve it? | **CONTRADICTED** on the measurable segment; NOT IDENTIFIED on venue→ingest | a measured venue-to-ingest time |
| 7 | Is reactive copying fundamentally too late? | **NOT IDENTIFIED** | forward A/B: same signal, two arrival paths, one-domain stamps, and *fills* not just book reads |
| 8 | What new data resolves 4–7? | the instrumentation spec below | — |

# MINIMUM FORWARD-LOOKING INSTRUMENTATION

**A specification, not a change.** Nothing is built, and collecting it must not
be a reason to activate trading — that needs separate approval.

Per copied source event, persisted immutably, never updated: source event/fill
id · source venue timestamp · **source timestamp provenance / clock domain** ·
BETTOR receipt timestamp from a **monotonic** clock · normalization-complete ·
decision · market-data request/send · market-data response · exact BBO/depth
snapshot timestamp · order submit · venue acknowledgement · first fill · complete
fill · cancel/replace · venue execution ids · superseding command id · market /
token mapping provenance · cross-venue mapping id · all prices, sizes and
fee/rebate fields available.

Three rules that separate this from what exists:

1. **Wall and monotonic are separate fields.** Every interval read as elapsed
   time is computed from monotonic readings in one process. The current tree has
   no monotonic clock in this path at all.
2. **Clock synchronisation quality is recorded, not assumed** — offset and
   dispersion against the reference at each event. An unmeasured offset is
   precisely what makes 92% of the chain lane negative today.
3. **Provenance travels with the value.** `chain.py:642`'s silent fallback is
   invisible today only because no field records which clock produced the value.

And one that is not a field: **record the censoring.** Today a probe exists only
for BUY fills under 120 s, so the slow tail is absent by construction and nothing
in the data says so. Count what is dropped.
