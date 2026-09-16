# BETA48 FORWARD — corrections of record

Every entry here is something this programme asserted and later found wrong.
They stay attached permanently. A correction that is only made in passing is
not a correction, because the wrong number outlives the conversation it was
said in.

---

## C-1. The three-tick discount figure (arithmetic)

**Asserted:** at `discountFactor = 0.30`, a quote three ticks back scores
**4.05%** of the same size at the touch.

**Correct:** **2.7%**. The ladder is a pure power of the factor:

| ticks from best | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `0.30^n` | 1.000 | 0.300 | 0.090 | **0.027** |

4.05% is not a term in this formula at all (it would be `0.30^2 x 0.45`).

**What survives:** the `~37x` size statement. `1 / 0.027 = 37.04`, so a quote
three ticks back still needs about thirty-seven times the size to match one at
the touch — far more inventory risk for the same score. The conclusion was
right; the percentage it was quoted with was not.

**Where the error was and was not.** The code was always correct:
`liquidity_score(500, 3, 0.30)` returned `13.500`, which is exactly 2.7% of
500. The wrong figure appeared only in narrative reporting. Pinned now in
`test_fees_v2.py::test_the_discount_ladder_exactly` and
`test_depth_panel.py::test_the_discount_ladder_is_exact`, both of which assert
`0.30^3 == 0.027` and explicitly assert it is *not* `0.0405`.

---

## C-2. "Whole population" language for a capped walk

**Asserted:** the 20,000-market board walk was "the whole population", "the
whole board", exchange-wide complete.

**Correct:** the walk stopped at the 200-page cap with
`DISCOVERY_LIST_EXHAUSTED = NO`. The honest statement is:

```
OBSERVED_PREFIX_MARKETS          >= 20,000 OPEN MARKETS
TRUE_ACTIVE_BOARD_SIZE           NOT_IDENTIFIED
BREADTH_IS_EXCHANGE_WIDE_COMPLETE NO
```

Breadth coverage is still **unbiased within the observed prefix** — there is no
selection step inside it — and that is worth having. It is not the same claim
as exchange-wide completeness, and calling it one would be the archaeology's
own error in a friendlier font.

`TRUE_ACTIVE_BOARD_SIZE` becomes a number only when discovery reaches a real
terminal boundary. Pinned in
`test_fwd_collect.py::test_a_capped_walk_is_a_prefix_and_says_so_in_those_words`.

---

## C-3. The zero-selection defect was misdiagnosed once

**Asserted:** the first live run selected zero markets because pagination was
not advancing, so duplicate pages made each event look like it had 200
outcomes.

**Correct:** 20,000 rows carried 20,000 **distinct** slugs. Pagination advanced
perfectly. The real defect was that no row carries `eventSlug` at all, so
grouping by it produced an empty dict. The dedupe guard added at the time was
harmless but was not the fix; it is retained as a guard against a failure mode
this venue does not exhibit, and its test says so.

---

## C-4. The incentives endpoint was read at the wrong level, and as a prefix

**Asserted:** the first incentives capture recorded a full row of nulls for
every programme field.

**Correct:** every economic field lives inside `timePeriods`, not on the
market, and markets carry 1, 4 or 5 periods with different pools and factors.
Separately, the endpoint is token-paginated and the first capture followed
`nextPageToken` zero times — the same prefix mistake as C-2, in a new place.
Both fixed; both pinned.

---

## C-5. A rotating page token inflated the incentive sample 200x

**Segment 35043611049 is VOID for incentive counts.** It reported:

```
incentive_pages_walked   200   (the cap)
INCENTIVES_LIST_EXHAUSTED NO
incentivized_markets     100
programs_parsed          53,400
```

100 markets cannot yield 53,400 distinct programme-periods. The endpoint
returned a **different `nextPageToken` on every request while serving the same
100 markets**, so the walk ran to the page cap and the same ~267 periods were
recorded 200 times over.

**Why the existing guard missed it.** C-4's fix stopped only when the token
*repeated*. A rotating cursor never repeats, so the guard never fired. The
board walk already terminates on **content** — a page contributing no new slug
ends the walk — and that lesson was not carried across to the token walk. It is
the third time in this programme that a prefix/duplication guard existed in one
place and was missing in another.

**Fixed both ends:**
- the walk ends when a page contributes no new
  `(marketSlug, programId, programType, period, start)`, recording
  `INCENTIVES_PAGINATION_ADVANCED`;
- records are deduped on the way out too, so a repeated page can never become a
  repeated record — a distribution over duplicates reads as a sample two
  hundred times larger than anything observed.

Pinned in `test_fwd_collect.py::test_a_rotating_page_token_over_identical_content_ends_the_walk`.

**What survives from that segment.** The *shape* of the distribution, which was
already established on the single clean page of segment 35042094434 and is
unchanged: discount factors are only 0.3 and 0.35; pools are 500 / 1000 / 1250
/ 10000; target sizes 500 / 1000 / 20000. The **counts** do not survive and are
not reported.

---

## C-6. "Tonight" — an off-by-one-day paraphrase of the fee cutover

**Said:** that the fee cutover was "tonight, 03:59 UTC", in reports written
from captures stamped 2026-09-16 in UTC.

**Correct:** the cutover is **23:59 ET on Wednesday 2026-09-16**, which is
**03:59 UTC on Thursday 2026-09-17**. At the time those reports were written
it was the evening of **Tuesday 2026-09-15** in ET, and the cutover was
**~26 hours away** — not that night, and not 03:59 UTC on the 16th.

**Where the error was and was not.** The code was right throughout:
`REGIME_CUTOVER_UTC = 2026-09-17 03:59Z` and the collector's
`FEE_REGIME_CUTOVER_UTC` both carried the correct instant, and every captured
segment was correctly labelled `JUL2026`. The error was in narration only.

**Why it matters more than a typo.** The ET date and the UTC date of this
cutover **differ**. Reading a UTC capture stamp (`2026-09-16T00:29Z`) and
assuming the cutover shares that date puts the new regime a full day early —
after which a correctly captured `feeCoefficient = 0.06` reads as though it
*contradicts* the documentation, when it is in fact the correct current value.
That is a defect that manufactures a false anomaly.

**Pinned** in `test_fees_v2.py::TheCutoverMoment`: the ET weekday and the UTC
weekday are asserted to be Wednesday and Thursday respectively, the two dates
asserted to differ, `03:59 UTC on the 16th` asserted to still be `JUL2026`,
every hour of the 16th UTC asserted `JUL2026`, and each of the four capture
timestamps we already hold asserted pre-cutover.

Preserved until a post-cutover observation:

```
CURRENT_CAPTURED_TAKER_THETA   0.06
UPCOMING_STANDARD_TAKER_THETA  0.0695
UPCOMING_EFFECTIVE_TIME        2026-09-16 23:59 ET (Wednesday)
UPCOMING_EFFECTIVE_TIME_UTC    2026-09-17 03:59 UTC (Thursday)
VERIFIED_FROM_CAPTURE          False
```

---

## C-7. The combo curve is UPCOMING, and its benefit was nearly prejudged

Two faults in one paragraph.

**(a) Schedule.** I presented the combo curve
`Fee = C × p × [0.0695(1-p) + 0.04(1-p)^4]` as though it described a current
cost. It does not: it sits under the page's "Upcoming fee changes" callout and
takes effect at the same cutover as the standard coefficient. Renamed
`UPCOMING_COMBO_TAKER_FEE_FORMULA`; `CURRENT_COMBO_TAKER_FEE_FORMULA` is
`NOT_IDENTIFIED` and is **not** back-formed by substituting 0.06.

**(b) Conclusion.** I wrote that a combo is "a more expensive way to acquire
the same legging risk". That prejudges the benefit as zero while
`COMBO_EXECUTION_ATOMICITY` and `COMBO_PARTIAL_FILL_BEHAVIOR` are both
unmeasured — the mirror image of the error this programme spends most of its
effort avoiding in the other direction. A venue charging a premium for a paired
instrument may be charging for real risk transfer.

Replaced with:

```
UPCOMING_COMBO_TAKER_COST_PREMIUM       VERIFIED_FROM_PRIMARY_DOCS
COMBO_ORPHAN_RISK_REDUCTION             NOT_IDENTIFIED
COMBO_EXECUTION_ATOMICITY               NOT_IDENTIFIED
COMBO_PARTIAL_FILL_BEHAVIOR             NOT_IDENTIFIED
COMBO_NET_VALUE_VS_SINGLE_LEG_EXECUTION NOT_IDENTIFIED
```

**(c) Precision.** The far-tail premium is **+0.06%** at p = 0.90, not the
+0.1% I reported. Rounding up nearly doubles it, and it is the one price where
the premium is negligible. Pinned to 0.0576% in
`test_fees_v2.py::TheUpcomingComboPremium`.
