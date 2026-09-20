# SPRINT VERDICT: IS THERE A BETTOR STRATEGY WORTH TINY-CAPITAL VALIDATION?

Owner directive 2026-09-20: "optimize for TIME TO ECONOMIC EVIDENCE."

**Answer for the maker classes: INSUFFICIENT_EVIDENCE_TO_DECIDE.**
**Answer for the taker pair: falsified, on same-venue arithmetic.**

---

## CORRECTION TO AN EARLIER VERSION OF THIS FILE

An earlier revision reported:

```
BASE_CASE_EV = -0.0059/share          <-- RETRACTED
STRATEGY CLASS A = FALSIFIED          <-- RETRACTED
STRATEGY CLASS B = FALSIFIED          <-- RETRACTED
```

**All three are withdrawn.** They were produced by combining two
measurements that do not belong in the same arithmetic:

| term | venue | population | window |
|---|---|---|---|
| gross half-spread +0.0050 | **PMUS** | BETTOR's own observed universe | 2026-09-19..20 |
| adverse selection -0.0140 | **Polymarket global CLOB** | rows that exist *because RN1 traded them* | 2026-08-06..09-11 |

Different venues, different populations, different time windows,
different flow-selection regimes. Subtracting one from the other
produces a number that describes no population that exists.

The earlier version named both facts as limitations and then put the
combined figure in the headline and labelled the classes FALSIFIED.
Naming a limitation does not license the claim. The scope discipline
this programme runs on was broken in the one place it mattered most --
the conclusion.

`MAKER_FIRST_FINDINGS.md:85-87` states the scope of its own result,
and it is preserved here verbatim:

> "Nothing here says market making is unprofitable in general; it says
> **making a market to this particular informed flow, at the touch,
> loses money.**"

---

## THE CLASSIFICATION, BY EVIDENCE CLASS

### Current PMUS — BETTOR's actual venue and universe

```
CURRENT_PMUS_GROSS_MAKER_SPREAD              MEASURED
    median half-spread 0.0050/share
    1,644 observations, 980 distinct markets
    cohort: spread <= 5c, mid 0.05..0.95
    p25/p75 spread 0.0100 / 0.0200, median mid 0.4450

CURRENT_PMUS_UNCONDITIONAL_ADVERSE_SELECTION NOT_IDENTIFIED
CURRENT_PMUS_P_FILL                          NOT_IDENTIFIED
CURRENT_PMUS_MAKER_NET_EV                    NOT_IDENTIFIED
```

The net EV is NOT_IDENTIFIED because two of its three terms are. That
is the honest state and it is not a negative result.

### Historical Polymarket CLOB — a different, selected population

```
RN1_CONDITIONAL_ADVERSE_SELECTION            -0.0140/share
    MEASURED_HISTORICAL_SELECTED_FLOW
RN1_CONDITIONAL_MAKER_NET_BEFORE_REBATE      -0.0090/share
    95% CI [-0.0143, -0.0038], clustered by condition
    n = 9,337 independent conditions, 112,553 trades
    settlement strictly after trade; 1,684 already-resolved dropped
    SELECTION_MECHANISM = RN1 traded that token
    SELECTION_BIAS = SEVERE, not the tradable universe
```

This is used as **TOXIC_FLOW_STRESS_CASE** -- what the maker leg earns
when the counterparty is a specifically informed trader lifting at the
touch. It is a stress bound on one flow regime, not BETTOR's expected
economics, and it is not evidence about the unconditional population.

*(The owner's directive was cut off mid-token at `TOXIC_FLOW_ST`. The
name above is an inference from that prefix and should be corrected if
a different one was intended.)*

### The rebate arithmetic, which is venue-clean

```
Fee = THETA * C * p * (1 - p),  THETA_MAKER = -0.0125   [PMUS, VERIFIED]
MAX rebate at p = 0.50:  0.0125 * 0.25 = 0.003125/share (0.3125 c)
```

This is a fact about PMUS's published schedule and stands on its own.
What it does **not** do is settle any EV, because the term it would
offset is NOT_IDENTIFIED on PMUS.

Against the RN1 stress case specifically, the rebate covers 34.7% of
that case's 0.0090 deficit. That statement is scoped to the stress
case and to no other population.

Also verified and relevant at small size: fees round to the nearest
$0.01, banker's rounding, **per fill**. A 1-contract fill at p=0.445
earns a $0.00 rebate; a 10-contract fill earns $0.03.

---

## WHAT IS FALSIFIED, ON ITS OWN EVIDENCE

### Class C — taker complementary pair: FALSIFIED

Same venue, same population, no cross-venue term:

```
observations   3,732      (PMUS, BETTOR's own two-sided observations)
below par          0
at par             0
median basis  1.0400
min basis     1.0050
```

`ask + (1 - bid) = 1 + spread`. The cheapest pair observed cost half a
cent above par. No crossing arbitrage exists on this venue, and this
needs no fill model and no external population.

### Class D — Phase X cross-venue PMUS/Kalshi: FALSIFIED, LOCKED

`TRACK_P_GATE = P-C`: +10.5% TRAIN -> +0.5% VALIDATION -> **-11.1%
HOLDOUT**, 95% CI [-15.5%, -6.6%], 1,170 independent markets, negative
at **zero fees**. Its own holdout, its own population. Preserved as a
locked negative; must not be retuned.

---

## WHAT REMAINS OPEN, AND WHY

### Classes A and B — passive maker pair, maker first leg

```
A. passive same-venue complementary maker pair   INSUFFICIENT_EVIDENCE
B. maker first leg + controlled completion       INSUFFICIENT_EVIDENCE
```

Measured: the gross term, on PMUS, today -- 0.0050/share half-spread.
Not measured: adverse selection on an unselected PMUS population, and
P_FILL.

The missing term is not small relative to the gross term, so its sign
and size decide the answer. Nothing in the current evidence base fixes
it.

### Class E — directional taker on independent fair value

`FV_BETTOR_INDEPENDENT = NOT_IDENTIFIED`; delta log loss -0.00926;
`INCREMENTAL_SIGNAL_STATUS = NOT_DETECTED_AT_THIS_SAMPLE_SIZE`.
**NOT_COMPARABLE** -- no edge estimate exists to rank.

---

## THE OBSERVABILITY CONSTRAINT, UNCHANGED

The frozen P_FILL contract is not satisfiable from PMUS public feeds
(`bettor_evidence_matrix.py`, measured 2026-09-20):

- POSITIVE blocked by one fact: `PRINT_WAS_A_CLOB_EXECUTION_NOT_A_BLOCK`.
  No block flag on the tape; `block-trade-data.html` is HTTP 404.
- NEGATIVE blocked by five of seven, binding one
  `QUEUE_DEPLETION_FROM_CANCELLATIONS`. The book publishes SNAPSHOTS,
  not order events; the tape's volume is market-wide and sideless, so
  the mix of trades and cancels is not recoverable by subtraction.

This bounds how fast the open questions can be closed from public data
alone. It is not itself an economic result.

---

## THE BOOK IS BIMODAL, AND THE WIDE HALF IS EMPTY

```
A_TIGHT_LE_5C     2,011 obs   median spread 0.0100   median mid 0.39
B_WIDE_5_TO_20C     488 obs   median spread 0.1000   median mid 0.30
C_EMPTY_GT_20C    1,233 obs   median spread 0.8800   median mid 0.50
```

Regime C is books quoted ~0.03/0.97 -- no liquidity. Pooled with
genuine 1c markets they produce a "median spread" of 0.94 in the
0.40-0.50 price band, describing neither population. Every figure in
this file is taken on the tight cohort, not the pooled book.

---

## TURNOVER, MEASURED, AND NOT A VERDICT ON EV

- `BETA48_STATE.md` BLOCK_4: 22,297-share displayed bid queue against
  180 shares traded in 16 minutes, **zero touches**.
- `DECISION_REPORT_S10.md`: ~1 trade per 26 minutes per market; the
  bid-side queue implies **on the order of three hours to reach the
  front of the queue**.

This constrains how fast any edge -- positive or negative -- would
compound or be measured. It does not tell us the sign of the edge.

---

## CORRECTION 2: BOOK + SETTLEMENT DOES NOT IDENTIFY MAKER
## ADVERSE SELECTION

An earlier revision of this section said:

```
"Prospective PMUS capture of book + settlement on an unselected
 universe would measure unconditional adverse selection and does
 not need P_FILL."                                   <-- RETRACTED
```

**Too strong.** Adverse selection for a maker is conditional on
execution:

```
E[FUTURE VALUE - QUOTE PRICE | OUR RESTING QUOTE FILLED]
```

An unselected book + settlement dataset observes STATE, QUOTE and
FUTURE VALUE. It does **not** observe whether our hypothetical resting
order would have filled. So it removes STATE-selection bias and leaves
**FILL-selection bias exactly where it was**.

The quantity such a capture yields is therefore named

```
UNCONDITIONAL_QUOTE_TO_SETTLEMENT_VALUE
```

and the name `UNCONDITIONAL_MAKER_ADVERSE_SELECTION` is refused in code
(`bettor_state_capture.forbidden_name`), because the name is the part
of a statistic that survives into a summary.

### The four objects, kept apart

| | estimand | status |
|---|---|---|
| **A** state economics | `E[SETTLEMENT − QUOTE \| STATE]` | measurable prospectively, without P_FILL |
| **B** fill probability | `P(FILL \| STATE, QUOTE)` | NOT_IDENTIFIED |
| **C** fill-conditional adverse selection | `E[SETTLEMENT − QUOTE \| FILLED, STATE]` | NOT_IDENTIFIED |
| **D** maker EV | combination of B, C, fees, rebates, inventory | NOT_IDENTIFIED |

**Never substitute A for C. Never substitute A for D.**

---

## WHAT WOULD ACTUALLY RESOLVE A AND B

The binding unknown for classes A and B is object **C** -- what a
resting order earns against the flow it actually meets. Nothing
currently available identifies it.

On disk it cannot be measured at all. The PMUS book/outcome join is
literally zero rows (`TRACK_P_DATA_ARCHAEOLOGY.md:15-19`: 30 slugs with
a two-sided book, 10,257 with an outcome, intersection 0), and the only
depth+settlement dataset is ask-side only, on a different venue, and
selected by RN1's trades.

Two paths, and only the first is open:

1. **Prospective unselected PMUS capture** (`BETTOR_UNSELECTED_STATE_V1`,
   started 2026-09-20). Read-only. It identifies object **A** and
   builds the clean state frame -- which states later move adversely,
   which price levels and spread regimes precede adverse movement. It
   does **not** identify C, and it is not a substitute for it.
2. **BETTOR's own admitted fills.** The only thing that identifies C
   exactly. Requires an order path and capital, and neither is
   authorised.

---

## NO ML

Track 8's precondition is not met: the decisive term is unmeasured,
labels are not identifiable, and there is nothing worth modelling. No
model was trained.
