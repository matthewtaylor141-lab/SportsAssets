# Frozen market-selection rule

| | |
|---|---|
| **Frozen at** | **2026-09-22T18:05Z** |
| **Frozen in commit** | the commit that adds this table (see `git log` for this file) |
| **Status of the rule** | HYPOTHESIS. Not validated, not an edge. |
| **Status of the two profitable markets** | **DEVELOPMENT EVIDENCE.** They generated the hypothesis; they cannot also test it. |

## Data inspected while selecting this rule — all of it disqualified as validation

A dataset is fresh validation only if it was **not inspected** while the
rule was being chosen. Everything below was, so none of it can validate
this rule at any later date:

| inspected | what was looked at |
|---|---|
| BBO capture 2026-09-13T16:55Z → 09-20T15:52Z, 12 markets, 30,590 rows | per-market spreads, ladders, replay outcomes, per-market net |
| Time & Sales 20260913–20260920, 8 files, 121,723 prints for those 12 markets | per-market print counts, per-interval print fractions — **the rule's own F1 input** |
| `mirror_orders` 2026-09-06 → 09-10 | fill rates, exposure, distances, per-market residuals |
| `mirror_shadow`, `copy_probes`, `trades` | row counts, date ranges and coverage only — **no row-level book or price data** |

**Consequence:** the frozen rule cannot be tested on any 2026-09-13..20
data, nor on the mirror-order window. The two football markets that
suggested it are development evidence and stay that way permanently.

---


**This rule is frozen BEFORE it is evaluated on any data it has not
already been fitted to. It is registered here precisely because it was
suggested by results I had already seen, which is exactly the condition
under which a rule must be frozen rather than reported.**

## What happened, stated plainly

In `EXECUTION_CALIBRATION.md` §5 I ranked twelve markets by the fraction
of replay intervals containing a real print, and observed that the only
two positive markets were the two with the highest fraction:

| market | intervals with a print | net $ |
|---|---:|---:|
| aec-cfb-portst-ore-2026-09-18 | 34.7% | +6.56 |
| aec-cfb-kentst-ohiost-2026-09-19 | 17.3% | +0.55 |
| *(the other ten)* | ≤ 11.8% | all negative |

**That is a pattern noticed after the fact, on the same twelve markets
the result was computed from.** Two markets out of twelve, worth +$7.11
combined, chosen by looking. It is not a validated opportunity, it is
not an edge, and it must not be promoted to one. The correct status is
**hypothesis**.

## The rule, frozen

```
SELECT a market for quoting iff, over the trailing 24 hours:

  F1  print_interval_fraction >= 0.15
      the fraction of BBO observation intervals containing at least one
      Time & Sales print for that market

  F2  median_spread_ticks <= 2

  F3  distinct_print_minutes >= 60
      trading is spread through the window rather than in one burst

Quote under C0 parameters. Size and all other policy parameters are
unchanged from the declared set; this rule governs MARKET ADMISSION
only.
```

Thresholds are fixed now. `0.15` sits below both positive markets
(0.347, 0.173) and above the best negative one (0.118) — it was chosen
to separate the observed cases, which is exactly why the rule cannot be
credited until it is tested somewhere else.

## What would count as a test

- **Fresh chronological data only.** The 2026-09-13..20 capture and its
  eight Time & Sales files are **used** and cannot validate this rule.
  A test requires capture days outside that window.
- **Pre-declared endpoint:** net per capital-hour of admitted markets,
  against the same quantity for markets the rule rejects. Both arms
  reported, whatever the sign.
- **Minimum evidence:** enough distinct event clusters that the result
  is not two football games again. Under a market-clustered variance the
  twelve-market corpus supports almost nothing; §4 of the calibration
  report measured a design effect of **2.27** on a much larger sample.
- **No threshold adjustment.** If 0.15 fails, the rule fails. Moving it
  and re-reporting is a search, and the register of variants exists so
  that a search is visible as one.

## What this rule is not

It is not evidence that flow predicts profitability. The twelve-market
observation is consistent with that hypothesis and equally consistent
with two lucky markets: +$7.11 across two events is inside the noise of
a corpus whose per-candidate totals range over ±$240.
