# FROZEN PRE-REGISTRATION — maker / inventory candidate set v1

**Frozen at SHA `b27672b`, before any evaluation outcome was read.** Nothing
below may be changed after an outcome is observed; a changed rule produces a
new version with its own name, and both stay on the record.

This is a **shadow research track**. Nothing in it can acquire live authority.
Promotion requires a separate, explicitly authorized decision that this
document cannot grant.

---

## 1. WHY THESE CANDIDATES AND NOT OTHERS

Two classes are already closed and stay closed:

| | |
|---|---|
| **Class C — taker complementary pair** | **FALSIFIED** within its tested scope: 3,732 observations, 0 at or below par, median basis 1.0400, min 1.0050. `ask + (1 − bid) = 1 + spread` is an identity on a venue publishing one long contract per slug. Not re-proposed. |
| **Class D — Phase X cross-venue** | **FALSIFIED, LOCKED.** +10.5% TRAIN → +0.5% VALIDATION → **−11.1% HOLDOUT**, 95% CI [−15.5%, −6.6%], 1,170 independent markets, negative at zero fees. Preserved as a locked negative; must not be retuned. |
| **Directional (independent FV)** | `P_BETTOR_INDEPENDENT_V3`: blend **worse** than market by 0.00926 log loss, 95% event-clustered CI [−0.00222, +0.02036], `NOT_DETECTED_AT_THIS_SAMPLE_SIZE`. **The interval crosses zero** — this is *not* proof that all directional edge is absent, and it is not licence to trade one. |

What remains open, from the sprint verdict, is Classes A and B — both
`INSUFFICIENT_EVIDENCE`, both with a measured gross term of **0.0050/share
half-spread** on PMUS.

## 2. THE CANDIDATE SET (exactly three; no others may be added to v1)

| id | hypothesis | research support |
|---|---|---|
| **M1** | A passive quote at the near touch, **held to settlement**, earns more than it loses to adverse selection. | Class A open; the exit route is the term that decides it, and holding removes the crossing cost entirely. |
| **M2** | A passive first leg followed by **controlled completion** of the complement is positive net of fees. | Class B open; requires `holds_both_legs_independently`, which is UNKNOWN on the institutional account. |
| **I1** | Given existing inventory, the **completion-vs-exit** rule selects the higher incremental-cash action more often than a fixed policy. | Inventory mechanics are measurable without any forecast; this is a policy comparison, not an edge claim. |

**No fourth candidate, no blends, no parameter sweeps.** A sweep over an
unmeasured term is a search for a number that flatters the result.

## 3. EXECUTION ASSUMPTIONS, FROZEN

| term | value | source |
|---|---|---|
| Taker fee | 0.02 / contract | **HYPOTHETICAL** — no verified schedule |
| Maker fee | 0.01 / contract | **HYPOTHETICAL** |
| Rebate | **0.00** | unverified incentives are zero in the base case |
| Fee rounding | nearest $0.01, banker's, **per fill** | MEASURED (sprint verdict) |
| Half-spread | 0.0050 / share | MEASURED — a **book statistic**, not a P&L term |
| `p_fill` | **NOT_IDENTIFIED** | no BETTOR resting order has ever existed |
| `conditional_reference_move` | **NOT_IDENTIFIED** | requires our own fills to measure |
| `E[settlement given our fill]` | **NOT_IDENTIFIED** | a fill selects the outcomes; no substitute permitted |
| Depth | **ABSENT_IN_CAPTURE_SCHEMA** | no size column exists |
| Decision staleness bound | 10 s | engine parameter |

**Three of the four terms M1 needs are NOT_IDENTIFIED.** That is stated here,
before any result, so no later finding can be presented as though the inputs
had been available.

## 4. CONSERVATIVE EXECUTION SCENARIOS (where fills cannot be identified)

Since `p_fill` is unmeasurable, each candidate is evaluated across a fixed,
pre-declared grid rather than at a chosen value:

- `p_fill ∈ {0.01, 0.05, 0.20, 0.50}`
- `conditional_reference_move ∈ {0.000, −0.005, −0.010, −0.020}` (adverse for a buy)
- exit route ∈ {`AGGRESSIVE_AT_BID`, `HOLD_TO_SETTLEMENT`}

The **conservative** case is `p_fill = 0.50` with `move = −0.020`: a quote that
fills often, and fills because the market is moving against it.

## 5. ACCEPTANCE RULES — FROZEN

A candidate is **SUPPORTED** only if all four hold:

1. Conditional round-trip per contract is **positive at every point** on the
   grid in §4, including the conservative corner.
2. It is positive using **only MEASURED or explicitly bounded** inputs — a
   candidate that needs a NOT_IDENTIFIED term to clear zero is `UNRESOLVED`,
   never supported.
3. **≥ 200 independent contracts** (distinct `market_id`, not distinct
   observations) contribute.
4. The 95% interval, clustered by **event**, excludes zero.

A candidate failing 1 or 2 is **REFUTED**. A candidate meeting 1 and 2 but not
3 or 4 is **UNRESOLVED — INSUFFICIENT SAMPLE**, which is an honest outcome and
must not be reported as a positive one.

**No rule in §5 may be relaxed to produce a result.** If nothing qualifies,
that is the finding.

## 6. WHAT WOULD FALSIFY EACH CANDIDATE

| | |
|---|---|
| **M1** | A negative conditional round trip at any grid point, or an `E[settlement given fill]` measurement below the entry price. |
| **M2** | `holds_both_legs_independently` resolving to UNSUPPORTED, or completion cost exceeding par net of fees. |
| **I1** | The rule selecting the lower-incremental-cash action on any constructed case where both are identified. |

## 7. WHAT THIS EVALUATION CANNOT DO

It cannot establish a positive edge. Every fill it considers is **assumed**,
not observed, because BETTOR has never rested an order. The output is a
**feasibility envelope**: the region of the assumption space in which each
candidate would be positive, and whether the conservative corner is inside it.

A feasibility envelope is not evidence of profitability, and this document
will not be cited as though it were.
