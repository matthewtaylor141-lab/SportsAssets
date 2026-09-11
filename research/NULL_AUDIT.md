# Missing-leg NULL audit — research SQL, runs 58 onward

Owner instruction, 2026-09-11, after run 68's telescoping check failed.

**The hazard.** PostgreSQL `LEAST`/`GREATEST` **ignore NULL** — the result is NULL
only if *every* argument is NULL. Two aggregate shapes are NULL over a non-empty
group: `agg(...) FILTER (...)` and `max/min(CASE WHEN ... THEN ...)` with no
`ELSE`. Combine them and a single-leg condition silently yields the **other
leg's full quantity** where zero is meant.

**Canonical form, required from here forward:**

```sql
qy = COALESCE(sum(...) FILTER (WHERE outcome_index = 0), 0)
qn = COALESCE(sum(...) FILTER (WHERE outcome_index = 1), 0)
M  = LEAST(qy, qn)
```

Same for costs, counts and shares wherever absence of a leg means **zero**
rather than **unknown**.

**Safety was not inferred from headline stability.** Every classification below
is from reading the consumption path, and the two NULL_BUG findings are
*measured* — run 69 statement 4 for the first, run 70 statement 3 for the second.

| file (run) | site | class | consequence |
|---|---|---|---|
| `rn1_selection_audits` (62) s2 | `mq = LEAST(qy,qn)`, nullable | **NULL_BUG** | `matched_quantity` overstated |
| `rn1_selection_audits` (62) s2 | matched cost / edge / % of acq | NULL_SAFE | `pair_cost` NULL kills the term; `sum()` skips |
| `rn1_selection_audits` (62) s3 | `residual_roi_pct` | **NULL_BUG** | every single-leg settled condition silently dropped from **both** numerator and denominator |
| `rn1_selection_audits` (62) s3 | `total_roi_pct` | NULL_SAFE | but on a **different population** than the two above |
| `rn1_selection_audits` (62) s4 | `sum(LEAST(qy,qn))` | **NULL_BUG** | retention-class `matched_qty` overstated |
| `rn1_selection_audits` (62) s6 | sport composition by matched cost | NULL_SAFE | cost-based |
| `rn1_structural_eligibility` (63) s2 | coverage by matched cost | NULL_SAFE | cost-based |
| `rn1_structural_eligibility` (63) s3 | `matched_qty = sum(mq)` | **NULL_BUG** | overstated |
| `rn1_structural_eligibility` (63) s3 | matched cost / gross / ROI / p50 pair cost | NULL_SAFE | `pair_cost` NULL excludes single-leg |
| `rn1_remainder_contamination` (64) s2 | `LEAST(COALESCE(qy,0), COALESCE(qn,0))` | NULL_SAFE | explicit |
| `rn1_remainder_contamination` (64) s3–6 | `COALESCE(LEAST(qy,qn) * CASE(...), 0)` | NULL_SAFE | outer COALESCE gives the correct 0 |
| `rn1_direct_directional` (65/67) s2–5 | `cl` has `WHERE qy > 0 AND qn > 0` | NULL_SAFE **by filter** | correct, but incidental — not by construction |
| `rn1_direct_directional` s1 | COALESCEd `cond` | NULL_SAFE | explicit |
| `rn1_contamination_cohorts` (66) | COALESCEd `cond` | NULL_SAFE | explicit |
| `rn1_complement_reduction` (69) s3–4 | `m_run68_buggy`, `mq_as_published` | NOT_RELEVANT | deliberate side-by-side reproductions |
| `rn1_bridge` / `rn1_bridge_sell` (56–59) | `dm` over cumulative `CASE ... ELSE 0` sums | NULL_SAFE | never NULL |
| `rn1_envelope_coverage_selection` | `GREATEST(LEAST(...))` | NOT_RELEVANT | not used in any run from 58 onward |

## Two distinct defects, not one

1. **`matched_qty` overstated.** Measured (run 69 s4): all-eligible
   51,113,813 → **43,167,280**; bridged 10,492,139 → 9,908,017; unbridged
   40,621,675 → 33,259,263. **Every matched ROI reproduced exactly** under
   corrected arithmetic — 1.383% / 0.804% / 1.556%, matched cost $42,578,503,
   matched gross P&L $588,777.

2. **`residual_roi_pct` computed on a silently truncated population.** Not a
   `LEAST` defect — the same family through a different door, which is why the
   audit could not stop at `LEAST`/`GREATEST`. Being measured in run 70.

## The blast radius, kept in the permanent record

Measured by run 71 statement 2 — what the retracted non-matched calculation
silently omitted:

| | conditions omitted | % of cohort | acq cost omitted | % acq cost | % of abs P&L |
|---|---|---|---|---|---|
| BRIDGED | 531 | 28.89% | $191,833 | 2.00% | 3.06% |
| UNBRIDGED | **5,729** | **45.95%** | $1,993,574 | 6.17% | **10.96%** |
| **total** | **6,260** | | **$2,185,407** | | |

**Omitting 45.95% of unbridged settled conditions from a calculation presented
as the non-matched economics** is the fact that settles the process question.
It was invisible: no value looked wrong, no error was raised, and the figure was
quoted in a verdict.

## Row/population closure is now MANDATORY, not optional QA

Every decomposition from here forward must, in its own output:

1. state each term's row count so population equality is **visible**, never
   asserted in a header;
2. measure the closure identity **per row and in aggregate**, expecting zero
   violations;
3. COALESCE every term to its economically correct zero rather than leaving a
   NULL that `sum()` will drop.

This is not a quality gate to be run when convenient. A decomposition without
closure is not a weaker result — it is an **unverified** one, and both retracted
figures passed every other kind of review while failing this.

## The permanent guard

`research/check_sql.py` now flags `LEAST`/`GREATEST` over nullable leg
aggregates. It was **installed only after being seen to fail on the real bug**:
the first version matched `FuncCall` and could never fire, because PostgreSQL
parses `LEAST`/`GREATEST` into `MinMaxExpr`. A guard that cannot fail is the
vacuous-filter error in tooling form. Intentional reproductions are marked
`lint: allow-missing-leg` so measuring a defect never requires deleting the
guard.

`mirror_live=false`.
