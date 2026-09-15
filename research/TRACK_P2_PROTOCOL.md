# TRACK P2 — FORWARD NATIVE FAIR-VALUE CAPTURE

**Status: `PRE-REGISTERED AND FROZEN. AWAITING PMUS GET ACCESS.`**

```
PROTOCOL_SHA256  fa931ae4dc2c5cfbbba4ce943e43df0a436a694a6012ebdd5f2a522fd57e73b6
```

Hashed before a single observation exists. Any change after outcomes begin
arriving creates `TRACKP2-2`, not an edit.

## What is being tested

Are contemporaneous **two-sided** PMUS prices systematically miscalibrated
against eventual settlement? That is **REGISTER 1** — predictive
information. Not a taker backtest, not a maker claim. The primary statistic
never touches an executable price.

## Population and capture

Whole current sports board, `active=true&closed=false`, walked to a **real**
terminal boundary (a page returning zero events). No RN1, activity,
liquidity, spread, price or performance filter; no manual shortlist.
Reaching a page ceiling is **not** a boundary and marks the capture
`PREFIX_BOUNDED`. Eight exclusion reasons are enumerated in the spec and
every exclusion is counted.

Two snapshots per market, **T−60 min** (primary) and **T−10 min**
(secondary), against `gameStartTime` — `endDate` is **never** substituted;
a market without verified start-time semantics carries
`TIME_TO_EVENT_STATUS = NOT_IDENTIFIED`. Full two-sided depth, both clocks,
raw venue body retained.

One identity — the market slug — carries unchanged from **discovery →
snapshot → settlement follow-up**. That single-identity rule is the direct
lesson of the archaeology: two datasets about the same venue whose market
sets never met, giving a zero-row join.

`MIDPOINT = (BEST_BID + BEST_ASK)/2`; one-sided book ⇒ `NOT_IDENTIFIED` and
out of the primary. The midpoint is a fair-market reference and is **never
called executable**.

Settlement follow-up is **mandatory**: re-read the same identity after
`endDate` until terminal, store `outcomePrices`/`result`/`status` verbatim,
classify `RESOLVED / PENDING / VOID / CANCELED / NOT_IDENTIFIED`. Outcomes
are never inferred from a price; VOID and CANCELED are counted and excluded,
never silently dropped or read as 0.

## Sequential design — why this is not peeking

Checkpoints at **N = 250 / 500 / 1,000 / 2,500 settled markets**, the same
frozen analysis at each. The looks are paid for in advance with
**Haybittle–Peto** boundaries: interim looks need **|z| ≥ 3.0**
(p < 0.0027), only the final look uses 1.98, so overall two-sided α stays
≈0.05. Haybittle–Peto rather than a spending function because it is exact,
needs no multivariate-normal integration, and cannot be quietly
mis-implemented in a way that flatters a result.

**Futility is checked first.** Stop `P2-C` as soon as the interval excludes
anything that could matter — answering "it cannot be big enough" early is a
real result and cheaper than more capture.

### What "matters", derived once, in advance

```
MEANINGFUL_EFFECT = 0.02
  verified PMUS taker fee at p=0.5   0.06 × 0.5 × 0.5 = 0.0150
  half of a one-tick spread          0.01 / 2         = 0.0050
                                                       ------
                                                       0.0200
```

A deviation smaller than that cannot become a taker trade even if perfectly
real. Fixing it before any data is what stops it being chosen later to fit
whatever the data shows. It is a **sizing bridge only** — never itself a
tradability claim. A unit test asserts the constant equals its derivation.

## Clustering — the thing most likely to overstate significance

One game emits many markets (moneyline, spreads, totals, props) whose
outcomes are strongly dependent. **N is counted in settled markets** as
instructed, but every interval is bootstrapped over **events**, so a
12-market NFL game contributes one draw and not twelve. Both counts are
always reported. The offline gate pins this: the same data clustered
12-to-an-event must produce a materially wider interval than the same data
treated as independent.

## Final confirmation

**20%** of markets are reserved as `P2_FINAL_CONFIRMATION`, assigned by a
deterministic salted hash of the slug **at capture time, before any outcome
exists**. Hash-based rather than by capture order so a capture interrupted
mid-slate cannot bias which markets are reserved. The checkpoint path
excludes them in the eligibility filter, not by convention.

## Segmentation

Primary: **all eligible sports markets, T−60 only**. Predeclared secondary,
reported but never gating: T−60 vs T−10, three broad price bands
(`[0.02,0.35) / [0.35,0.65) / [0.65,0.98)`), and sport. No league, market
type, finer band, time window or model variant at any checkpoint. A subgroup
noticed after seeing results is labelled `HYPOTHESIS_GENERATING`.

## Gates

| | |
|---|---|
| `P2-A` | \|z\| ≥ boundary **and** \|mean residual\| ≥ 0.02. Authorizes designing the next **execution** experiment. **Does not prove profitability.** |
| `P2-B` | Interesting but uncertain — continue to the next checkpoint |
| `P2-C` | CI excludes \|effect\| ≥ 0.02 in both directions — stop |
| `P2-D` | Identity, timing, settlement, selection or capture defect — stop and repair |

Three registers stay separate: **1 predictive** (what P2 measures),
**2 executable taker**, **3 maker execution**. P2 measures neither 2 nor 3.
`BLOCK_4` remains binding evidence that displayed maker edge need not become
executable edge.

## A defect the offline gate caught

The first cut of the capture runner reported, against the blocked host:

```
DISCOVERY_LIST_EXHAUSTED = YES
markets added = 0
```

An **unreachable** board reading as an **exhausted** board with zero
markets — downstream indistinguishable from "the venue lists no sports
today". Same defect class as the one already fixed in the Phase X driver:
`BL.discover()` treats a failed page as a terminal boundary, which is right
for a walk that ends and wrong for one that never began. The runner now
raises on a first page that never returned 200, and the gate pins that the
guard precedes the terminal verdict. Today it correctly prints:

```
VENUE UNREACHABLE: PMUS discovery never returned a page:
  first page status=None error=ProxyError -- ACCESS FAILURE, not an empty board
```

**24 offline tests pass** (`research/test_trackp2.py`), under pytest and the
self-runner.

## Run order once access exists

```
python3 research/trackp2_capture.py --stage discover     # board -> registry
python3 research/trackp2_capture.py --stage snapshot     # T-60 / T-10, on a timer
python3 research/trackp2_capture.py --stage settle       # after endDate, until terminal
python3 research/trackp2_capture.py --stage analyse --checkpoint 0   # at N=250
```

`--stage analyse` refuses to open a checkpoint before its N is reached.
`P2_FINAL_CONFIRMATION` opens only with `--final-confirmation`, once.

## Blocker

```
gateway.polymarket.us:443      connect_rejected
api.elections.kalshi.com:443   connect_rejected
```

Both serious paths are blocked on the same thing. The operational priority
is GET-only research access to both hosts from one controlled environment —
see `PHASEX_EGRESS_REQUEST.md`. No workaround was built and none will be.

`mirror_live = false`. No capital, no orders, no production writes.
