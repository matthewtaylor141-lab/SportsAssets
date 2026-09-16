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
| `POSITION_LEVEL_N` | **must come from `effective_n()`**, which refuses fill counts |
| `SUPPORTING_ACCOUNTS` | count of *independent* **primary** accounts on the **same** mechanism |
| `MECHANISM_AGREEMENT` | do they agree on the sign, not the magnitude |
| `IN_DISTRIBUTION` | is this state inside the region they traded |
| `VENUE_MECHANISM_EQUIVALENCE` | STRONG / PARTIAL / NONE / NOT_IDENTIFIED |
| ~~`N_FILLS`~~ | **reported, never used**; `RAW_FILL_COUNT_ALONE_CANNOT_PRODUCE_STRONG = True` |
| ~~`EFFECTIVE_N`~~ | **the name is withdrawn**; it claimed independence the data cannot support |

Preregistered thresholds — deliberately blunt, because a finer scale would imply
a precision the aggregate artefacts do not have:

```
STRONG_MIN_POSITION_LEVEL_N     1000
MODERATE_MIN_POSITION_LEVEL_N    200
WEAK_MIN_POSITION_LEVEL_N         30
STRONG_MIN_SUPPORTING_ACCOUNTS     2
```

**Clearing a threshold is necessary and never sufficient.** The count read is a
POSITION count, which bounds the independent count from above rather than
measuring it, so every payload carries `INDEPENDENT_EFFECTIVE_N =
NOT_IDENTIFIED` and `SUPPORT_LEVEL_IS_CAPPED_BY =
POSITION_LEVEL_N_IS_AN_UPPER_BOUND_ON_INDEPENDENT_N`. Every payload also carries
`SELECTION_CONDITION = OBSERVED_WHALE_ENTERED_POSITIONS_ONLY`.

**The supporting-account count is over PRIMARY accounts only**: rn1,
ferrarichampions2026 and homerunhazard. swisstony is
`GHOST_PRIOR_SENSITIVITY_ONLY` and contributes **nothing** to the total, even in
a sensitivity run — otherwise its exclusion would be cosmetic, since it is the
account that would turn MODERATE into STRONG.

## The four rules that do the work

**1. One account cannot reach STRONG, however large.** 50,000 effective
observations from RN1 alone returns MODERATE. One account's habit is not a
market regularity; it may be one desk's idiosyncrasy, and the whole point of
having four accounts is to tell those apart.

**2. Fill count cannot buy a level.** `effective_n(n_fills=4692866)` returns
`POSITION_LEVEL_N = NOT_IDENTIFIED`. The caller must supply positions, markets or
events. RN1's 18.1 fills per position is exactly the inflation this blocks — and
positions are themselves only an upper bound, since several can belong to one
event and no event id exists to group them.

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
`ACCOUNT × TIME_UNPAIRED_INTERVAL`, plus `× FILL_SIZE_BUCKET` and `× ISO_WEEK`
from the blobs_v3 probe — all marginals, never a cross product):

- **The merge-sign consensus below 0.50** has three agreeing primary accounts
  and position counts in the tens of thousands per band. On the sign, that is
  plausibly STRONG for the COMPLETION mechanism at `VENUE_EQUIVALENCE = PARTIAL`.
  swisstony agrees and adds nothing to the count.
- **Any cell requiring sport, league, market type, time-to-event or
  pregame/live is `NOT_IDENTIFIED`** — not WEAK, not OUT_OF_DISTRIBUTION.
  There is no measurement, so there is no level. The blobs_v3 probe confirmed
  this rather than relieving it.
- **Any cell crossing two measured marginals is also `NOT_IDENTIFIED`.**
  `PRICE_TIME_JOINT_PRIOR = NOT_IDENTIFIED`, and `cell_support` returns the
  refusal rather than a product, an interpolation or a sum.
- **Magnitudes are not supported at any level.** `CONSENSUS_IS_ON =
  SIGN_ONLY_NOT_MAGNITUDE`. A support level attaches to a direction, and the
  size of the effect remains BETTOR's to measure.

That last point is the operative limit of this whole model: it can tell BETTOR
*which way* the whales' evidence points in a coarse cell — among the positions
those whales chose to open — and it cannot tell BETTOR *how much*.
