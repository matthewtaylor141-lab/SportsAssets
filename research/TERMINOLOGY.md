# Locked terminology — venue actions and window edges

Owner instruction, 2026-09-11. **Binding on every file, commit and report from
here forward.** These locks exist because one English word was carrying two
different objects, and I reported a result that depended on the confusion.

---

## 1. The two things that were both called "SELL"

### `RN1_SOURCE_FILL_SIDE`

The observed fill vocabulary of **his** source venue.

- In this window it takes exactly **one** value: `BUY`. 408,094 rows.
- **Exposure reduction on his venue happens by BUYING THE COMPLEMENTARY TOKEN.**
  A reduction therefore books a `BUY` row on the other token and never a
  `SELL` row.

### `BETTOR_REQUIRED_ACTION`

The target action on **PMUS**, under one-signed-position accounting.

- Takes `BUY` / `SELL` / `HOLD`.
- **The 32,847 SELL-required events of run 59 belong here**, and only here.

### The rule

> **Never write a bare "SELL" again.** Every use carries one of the two
> prefixes above.

The same discipline applies to "BUY": `RN1_SOURCE_FILL_SIDE=BUY` is his fill;
`BETTOR_REQUIRED_ACTION=BUY` is our required action. They coincide often and
are still not the same object.

---

## 2. What this invalidates, explicitly

Step B's clean cohort filtered on `sell_qty = 0`, i.e. on
`RN1_SOURCE_FILL_SIDE=SELL`. **That quantity is vacuously zero on his venue by
construction.** A filter testing a condition that cannot fail retains ~100% of
the population and has measured nothing.

So the 99.707% retention rate is **evidence that the filter is not binding on
the mechanism that matters**, and is NOT evidence that the mechanism is absent.

**RETRACTED:** "the representable contaminants are measured and immaterial" and
"the clean cohort is essentially the whole settled population." Both were
written on the vacuous filter and are withdrawn.

### What is actually measured and immaterial, stated narrowly

| claim | status |
|---|---|
| literal `RN1_SOURCE_FILL_SIDE=SELL` contamination | **zero** — and vacuously so |
| directly observed window overlap | **~0.30% of acquisition cost** |

### What is NOT yet cleared, kept separate

| open item | why it is open |
|---|---|
| **complement BUYs that reduce or reverse directional exposure** | not yet measured; this is the real form of the contamination on his venue |
| **invisible complete-set conversions / redemptions** | structurally unrepresentable — see §4 |

---

## 3. The contamination Step B must actually test

Not literal source SELLs. **Complement-reduction**, reconstructed
chronologically.

For each `RN1_SOURCE_FILL_SIDE=BUY` of token X, using inventory state
**immediately before that fill**:

    matched_component          = dM
    new_directional_component  = fill_size - dM

- If opposite-token inventory exists, some or all of the fill raises matched
  quantity — **`PAIR_FORMATION`**.
- If the fill exceeds what is needed to match the opposite inventory, the
  excess creates new directional exposure on X.
- A complement BUY against existing directional inventory on the other side is
  **`DIRECTIONAL_REVERSAL_OR_REDUCTION`** — economically the same event the
  SELL test was trying to find.

The full chronological path then answers whether the end-of-window residual was
ever **reduced**, **flipped**, or **rebuilt** — which a per-condition aggregate
of `min(qY,qN)` cannot see, because it collapses the path to its endpoint.

---

## 4. The structural exclusion, stated once and kept

`trades.side` is `CHECK (side IN ('BUY','SELL'))`. There is no row shape for a
complete-set conversion back to collateral, and none for an early redemption.

> **`TRUE_CASH_PATH = NOT IDENTIFIABLE FROM THIS LEDGER`**

This holds **even if** acquisition-time matched margin and end-state residual
economics are identifiable. Identifying those two does not identify the cash
path, and no combination of them may be presented as having done so.

Reported as **unquantifiable**, never as zero, never as small.

---

## 5. Window-edge nomenclature

| label | meaning |
|---|---|
| `PREWINDOW_INVENTORY_PROVEN` | **only** where in-window evidence *proves* prior inventory |
| `PREWINDOW_INVENTORY_NOT_PROVEN` | everything else |

> **`FULLY_IN_WINDOW` is retired as a label.** Observing no pre-window fill is
> absence of evidence, not proof of no earlier inventory. The measured overlap
> is small; that does not upgrade it.

Run 64 statement 3's "1 FULLY INSIDE THE WINDOW" (26,171 conditions) is to be
read as `PREWINDOW_INVENTORY_NOT_PROVEN`.

---

## 6. Attribution sensitivity is mandatory, not optional

The residual leg was acquired at multiple prices in **~84%** of residual
conditions (6,777 of 8,031; p50 4 distinct prices, p90 15–16). So the
matched/directional split is a live accounting choice, not a formality.

Report under **FIFO**, **LIFO** and **average-cost pool**:

- directional P&L under each method
- spread between methods **in dollars**
- spread as **% of total trading P&L**
- **number and % of conditions whose directional P&L changes sign** across
  methods

Total condition P&L and `M` are **invariant** across methods; only the
attribution moves. If sign changes are common, directional attribution is
**method-sensitive** and must be described that way. If aggregate dollars are
stable, a robust *aggregate* statement is still available — but not a
per-condition one.

`mirror_live=false`.
