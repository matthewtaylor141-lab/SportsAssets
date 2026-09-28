# Why the loop admits nothing — and a correction to my own census conclusion

**Source:** `research/why_the_input_path_is_blocked.sql` via `research-sql.yml`, production database. Run 36373917972, job 108775754879, `psql exit=0`, query sha256 `6030bddd03e0e58ae00a176509e69df2463ddcf2db6d4e699394544b9022b2a6`, read 2026-09-28T03:29:38Z.

---

## The correction first

In `CENSUS_CROSSTAB_2026-09-28.md` I wrote:

> *"`EXECUTION_ESTIMATE_NOT_IDENTIFIED` — 94.8% of evaluations never get an execution estimate. **Largest actionable cause; engineering, no external dependency identified yet.**"*

**That was too coarse, and the second half was wrong.** The census read the *gate's* generic code. The lane also records the execution **plan's own** codes, precisely so the two can be told apart — and they say the absence decomposes into two different things with two different owners.

Among the **1,067** rows missing the execution estimate:

| Co-occurring code | Rows | What it actually means |
|---|---:|---|
| `NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT` | **488** | The plan **ran**. It read the ladder. There was **no depth at a price inside break-even.** This is an **economic finding**, not a missing input. |
| `INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED` | **399** | No registered independent fair value. **Engineering/modelling.** |
| `NO_QUALIFIED_MODEL` | **399** | Same 399 rows — no qualified model to supply that fair value. |
| `QUOTE_STALE` | **399** | Freshness: the book's age was unestablished or too old. |
| `RISK_GATE_BLOCKED` | 825 | Downstream of the above. |
| `SIZING_POLICY_NOT_APPLICABLE` | 825 | Downstream — nothing to size. |

**So "no execution estimate" is not one problem.** On 488 rows the venue simply was not offering an affordable price, and no amount of engineering changes that. On 399 rows the blocker is upstream of execution entirely: there is no independent fair value, so break-even cannot be computed in the first place.

I said there was "no external dependency identified yet." There is one, and it is the largest single class: **the market's own depth.**

## It is intermittent, not structural

| Day | Evaluations | With an execution estimate |
|---|---:|---:|
| 2026-09-24 | 262 | 4 |
| 2026-09-25 | 431 | **21** |
| 2026-09-26 | 258 | **23** |
| 2026-09-27 | 175 | 10 |

58 rows across 25 distinct markets, spanning 2026-09-24T23:40:56Z → 2026-09-27T19:35:48Z.

**The execution plan works.** It produced estimates on all four days. This is not a broken component — it is a component that runs and frequently finds nothing to buy.

*(The census said 59 rows and this query says 58. The census counted rows where the estimate stage left any trace; this counts rows where `p_fill` is non-null specifically. The one-row difference is the stricter predicate, and I am reporting both rather than picking the larger.)*

## The revised priority order

| # | Blocker | Rows | Owner | Credible path |
|---|---|---:|---|---|
| 1 | **No independent fair value / no qualified model** | 399 | engineering | This is the real actionable engineering gap, and it is *upstream* of execution. Without a fair value there is no break-even limit, so the plan cannot even ask about depth. A registered, non-venue-derived fair value unlocks the arithmetic `edge = fair_value − ask − fee(ask)`. |
| 2 | **`QUOTE_STALE`** | 399 | engineering | `book_currency_evidence` returns no mechanism in production. Needs the M1 subscription or M2 revalidation contract live. |
| 3 | **No depth inside break-even** | 488 | **the market** | Not fixable by us. It can only be widened by accepting worse prices, which is the opposite of what the gates exist for. The honest response is that the opportunity was not there on those rows. |
| 4 | Settlement per-condition grading | 527 + 564 | venue | Never the *only* refusal on any row. |

**The circularity guard matters here and is correct.** `bettor_entry_gate` refuses a venue-derived fair value outright (`R_FV_IS_VENUE_PRICE`): *"A benchmark cannot be evidence against itself."* So blocker 1 cannot be closed by deriving fair value from the venue's own midpoint — which would be the easy and wrong fix.

## What this does and does not say

**It does not say there is no edge.** 488 rows found no affordable depth *at the break-even limit implied by a fair value that, on 399 other rows, does not exist*. Those populations overlap, and I am not going to convert them into a claim about available edge in either direction.

**It does say** that the largest single named cause of an idle loop is the market not offering a price inside break-even, and the largest *actionable* one is the absent independent fair value — which is a modelling dependency, not an execution one.

**And it says my earlier priority was wrong.** I told you the execution estimate was the largest actionable cause with no external dependency. The measurement says the fair value is the largest actionable cause, and the market's depth is a real external dependency.
