# WHALE SUPPORT MODEL V1

How much whale evidence stands behind a given BETTOR opportunity, and what a
level does and does not license. Code: `shadow/whale_bridge.py`.

```
OFFLINE. VENUE_CONTACT = 0  ORDERS = 0  CAPITAL = 0  mirror_live = false
```

---

## The five levels

```
STRONG              multiple accounts agree on ONE mechanism, adequate effective n
MODERATE            adequate effective n, but not multi-account agreement
WEAK                thin but non-empty, or no PMUS venue equivalence
OUT_OF_DISTRIBUTION effectively no whale observation in this region
NOT_IDENTIFIED      effective n or distributional support not established
```

## The inputs, and the one that is refused

| Input | Role |
|---|---|
| `EFFECTIVE_N` | **must come from `effective_n()`**, which refuses fill counts |
| `SUPPORTING_ACCOUNTS` | count of *independent* accounts on the **same** mechanism |
| `MECHANISM_AGREEMENT` | do they agree on the sign, not the magnitude |
| `IN_DISTRIBUTION` | is this state inside the region they traded |
| `VENUE_MECHANISM_EQUIVALENCE` | STRONG / PARTIAL / NONE / NOT_IDENTIFIED |
| ~~`N_FILLS`~~ | **reported, never used**; `RAW_FILL_COUNT_ALONE_CANNOT_PRODUCE_STRONG = True` |

Preregistered thresholds — deliberately blunt, because a finer scale would imply
a precision the aggregate artefacts do not have:

```
STRONG_MIN_EFFECTIVE_N          1000
MODERATE_MIN_EFFECTIVE_N         200
WEAK_MIN_EFFECTIVE_N              30
STRONG_MIN_SUPPORTING_ACCOUNTS     2
```

## The four rules that do the work

**1. One account cannot reach STRONG, however large.** 50,000 effective
observations from RN1 alone returns MODERATE. One account's habit is not a
market regularity; it may be one desk's idiosyncrasy, and the whole point of
having four accounts is to tell those apart.

**2. Fill count cannot buy a level.** `effective_n(n_fills=4692866)` returns
`EFFECTIVE_N = NOT_IDENTIFIED`. The caller must supply positions, markets or
events. RN1's 18.1 fills per position is exactly the inflation this blocks.

**3. No venue equivalence caps support at WEAK.** Even 10⁶ effective
observations with four agreeing accounts returns WEAK if
`VENUE_MECHANISM_EQUIVALENCE = NONE`. Evidence about a mechanism that does not
exist here is not evidence about here.

**4. Out of distribution short-circuits everything.** It is checked before n,
before accounts, before agreement — because interpolating into a region nobody
traded is the failure this level exists to name.

## What a level licenses

| Level | Licenses |
|---|---|
| STRONG | Route A admission in `DAY1_WHALE_ANCHORED_MODE`, **with** positive BETTOR EV |
| MODERATE | Route A admission, same conjunction |
| WEAK | shadow only |
| OUT_OF_DISTRIBUTION | shadow only, unless independently promoted via Route B |
| NOT_IDENTIFIED | shadow only |

**No level licenses a trade on its own.** Every route requires whale support
**and** positive current BETTOR EV, or an independently validated structural
opportunity. Whale support is never sufficient.

## Where the levels actually land today

Given the cell space that exists (`ACCOUNT × PRICE_BAND`,
`ACCOUNT × TIME_UNPAIRED_INTERVAL`, not their cross product):

- **The merge-sign consensus below 0.50** has four agreeing accounts and
  position counts in the tens of thousands per band. On the sign, that is
  plausibly STRONG for the COMPLETION mechanism at `VENUE_EQUIVALENCE = PARTIAL`.
- **Any cell requiring sport, league, market type, time-to-event or
  pregame/live is `NOT_IDENTIFIED`** — not WEAK, not OUT_OF_DISTRIBUTION.
  There is no measurement, so there is no level.
- **Magnitudes are not supported at any level.** `CONSENSUS_IS_ON =
  SIGN_ONLY_NOT_MAGNITUDE`. A support level attaches to a direction, and the
  size of the effect remains BETTOR's to measure.

That last point is the operative limit of this whole model: it can tell BETTOR
*which way* the whales' evidence points in a coarse cell, and it cannot tell
BETTOR *how much*.
