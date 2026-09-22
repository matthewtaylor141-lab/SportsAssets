# The frozen selection rule, measured against the live venue

One line from the worker, 2026-09-22T11:41:57.156Z:

```
no eligible markets; not starting.
COVERAGE candidates=3000 probed=40 enriched=40 {"ENRICHED": 40}
RULE     considered=40 excluded={"ONE_SIDED_BOOK": 28, "SPREAD_BELOW_MIN_TICKS": 12}
```

## What it says

| | |
|---|---|
| markets the listing returned | **3,000** (6 pages; the bound was hit and the set is a PREFIX) |
| markets probed for a book | **40** — exactly the approved distinct ceiling |
| markets successfully enriched | **40 of 40** |
| markets admitted by `BETTOR_UNIVERSE_V1` | **0** |
| excluded `ONE_SIDED_BOOK` | **28** (70%) |
| excluded `SPREAD_BELOW_MIN_TICKS` | **12** (30%) |

**Every probed market returned a usable BBO.** The seven HTTP 429s were
retried and all succeeded: `enriched=40` with a single `ENRICHED: 40`
bucket and no failure bucket. Within this probe's bounds the rate limit
cost **time, not data**.

## What it means, and what it does not

**Means:** on this 40-market sample of the live venue, the rule's two
binding exclusions are a **one-sided book** and a **spread under two
ticks**. Neither is "the market is empty" — a one-sided book is a market
where only one side is quoted, and a sub-two-tick spread is a market too
*tight* for the rule, not too wide.

The 12 sub-two-tick markets are interesting against the holdout finding
that 493 of 788 archived observations sit on a **half-cent** grid: at a
0.005 tick, `MIN_SPREAD_TICKS = 2` demands a full cent of spread. A
market quoted 0.600/0.605 is one tick wide and is refused.

**Does not mean** the venue has no tradable markets. This is:

- **40 markets**, not 3,000. They are the first 40 of a listing prefix,
  ordered by whatever the venue returns first — not a random sample.
- **one moment**, 11:38–11:42 UTC on a Tuesday.
- **one family**. The probed slugs are dominated by
  `aachc-mlb-bavg-2026-09-29-leader-*` — MLB batting-average leader
  markets, which are long-dated, many-outcome futures. That is close to
  the worst case for two-sided quoting, and it is an artefact of listing
  order, not a choice.

**No selection rule was changed**, before, during or after this run, and
the exclusion counts above are the frozen rule's own output.

## The follow-up this suggests

The probe spent its entire 40-market allowance on one unrepresentative
slice because the listing prefix delivered one. A future probe should
either sample across the listing rather than take its head, or filter
the candidate set by market family before spending distinct slots. Both
are changes to *what is probed*, not to what is *admitted*, so neither
touches the selection rule.
