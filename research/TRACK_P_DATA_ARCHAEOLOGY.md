# TRACK P — DATA ASSET ARCHAEOLOGY

**Gate: `DATA-B — PARTIAL DATA EXISTS`**

Fast pass over the repository, sealed evidence, snapshots and retained
artifacts. Offline; no venue contact, no new collection, no model.

## The finding in one line

Every ingredient exists — **two-sided books, full depth, repeated
observations, settlement outcomes, game start times, market types** — but
**not on the same markets.** The join that the fair-value question needs is
literally empty.

```
PMUS slugs with a TWO-SIDED book observation : 30      (6,952 observations)
PMUS slugs with a RESOLVED outcome           : 10,257
INTERSECTION                                 : 0
```

The captures targeted markets that were **open**; the outcomes come from an
archive walk of markets that had already **resolved**. Nothing sits in both
sets.

## Datasets found

### 1. `AUDIT_SNAPSHOT_V1` — `u2_events_v1` + `settlement_v1`

| | |
|---|---|
| SOURCE | `research/snapshots/`, drawn 2026-09-12T02:53:56Z, read-only repeatable-read |
| VENUE | **Polymarket CLOB** (condition_id / ERC-1155 token ids) |
| DATE_RANGE | 2026-08-06 .. 2026-09-11 (35 days) |
| ROW_COUNT | 214,609 (trade, probe) pairs |
| UNIQUE_MARKETS | 17,752 conditions |
| SPORTS | Soccer 34%, Tennis 33%, unclassified 10%, Other 8%, Non-Sports 6%, MLB 5%, NFL 4%, NBA 0.4%, MMA 0.2% |
| BID_AVAILABLE | **NO** |
| ASK_AVAILABLE | YES (`best_ask` + ladder, median 8 levels, 100% of rows) |
| DEPTH_AVAILABLE | YES — ask side only |
| REPEATED_MARKET_OBSERVATIONS | YES (many probes per token) but **only at RN1's trade moments** |
| PRICE_TIME_SERIES | **NO** — irregular, event-triggered, one side |
| GAME_START / LIVE_STATE / SCORE_STATE | **NO** |
| OUTCOME / RESOLUTION_TIME | YES / YES |
| TIMESTAMP_CLOCK | BETTOR wall clock (`probe_at`), venue resolution time (`resolved_at`) |
| TIMESTAMP_SEMANTICS | `probe_at` = when we read the book; `ts` = RN1's trade |
| SELECTION_MECHANISM | **RN1 traded the token** |
| SELECTION_BIAS | **SEVERE** — not the tradable universe |
| SAFE_FOR_CAUSAL_TIME_ORDERING | YES (1,684 already-resolved rows excluded; 0 unknown) |
| SAFE_FOR_MODELING | Only as already used in Track P (`P-C`). Ask-only + RN1-selected. |

### 2. Run 85 Phase 2 capture segments + tarballs (PMUS)

| | |
|---|---|
| SOURCE | `research/evidence/capture/run85_phase2_segment_*`, `run85_phase2[a-g]*.tar.gz` |
| VENUE | **PMUS** (`gateway.polymarket.us`) |
| DATE_RANGE | 2026-09-13T02:16 .. 2026-09-15T09:04 |
| ROW_COUNT | 15,350 book reads in segments (+349 in tarballs) |
| **TWO-SIDED** | **6,737** segment observations (44%); 7,805 empty, 803 ask-only, 5 bid-only |
| UNIQUE_MARKETS | **12** in segments, 30 across everything |
| BID / ASK / DEPTH | **YES / YES / YES** — full ladders both sides |
| REPEATED_MARKET_OBSERVATIONS | **YES** — up to 5-hour segments on a 2.5 s floor |
| PRICE_TIME_SERIES | **YES**, but on 12 markets |
| GAME_START | YES (discovery rows carry `gameStartTime`) |
| LIVE_STATE / SCORE_STATE | NO |
| OUTCOME | **NO** — none of these 30 markets appears in any resolved set we hold |
| TIMESTAMP_CLOCK | `local_request_wall_utc` + `local_request_monotonic_ns`, venue `transactTime` |
| SELECTION_MECHANISM | Run 85's own scientific selection — **independent of RN1** |
| SELECTION_BIAS | Deliberate small cohort; not a board sample |
| SAFE_FOR_CAUSAL_TIME_ORDERING | YES — both clocks recorded per request |
| SAFE_FOR_MODELING | **NO** — 12–30 markets is not a predictive sample, and there is no label |

### 3. Track B-L blocks (PMUS)

`research/evidence/trackbl/BLOCK_1..4` — two-sided books, 2.5 s floor,
20-minute blocks, **4–6 markets per block**, 15 stage-2 probes. Same
strengths and the same fatal limit: tiny cohort, no outcomes.

### 4. PMUS resolved market rows (the outcome source)

From the BLOCK_2 archive walk and the current-universe walks:

| | |
|---|---|
| ROW_COUNT | 10,585 resolved rows / **10,257 distinct slugs** |
| FIELDS | `outcomes`, `outcomePrices` (1/0), `gameStartTime`, `endDate`, `sportsMarketTypeV2`, tags→league |
| OUTCOME / GAME_START / MARKET_TYPE / PARTICIPANTS | **YES / YES / YES / YES** |
| BID / ASK / DEPTH **BEFORE** RESOLUTION | **NO** — the row is read after settlement; its quotes are post-resolution |
| SELECTION_MECHANISM | Pagination prefix of the venue archive |
| SELECTION_BIAS | **Prefix-bounded** — `DISCOVERY_LIST_EXHAUSTED = NO` |
| SAFE_FOR_MODELING | As a **label table only** |

Observed settlement dates span 2025-10-31 .. 2027-04-25 across 85 days,
median 12 slugs/day. **`PMUS_SETTLEMENT_THROUGHPUT = NOT_IDENTIFIED`** —
these counts come from a bounded prefix of the archive, so they are a floor
on what the venue settles, never an estimate of it.

### 5. Run 83.6 CLOB protocol capture

4 tokens, 3 × 75 s sessions, 5,951 frames. Two-sided `book` snapshots exist
(63 of them) plus 5,838 `price_change` deltas — a genuine two-sided feed
shape, but it is a **protocol reconstruction study**, four minutes long.
Not a dataset.

### 6. `edge_ledger.sqlite3`, `run84_pair_candidates.csv.gz`, `u0_timing_witness_v1`

Engine ledger (our own fills, not market history); Run 84 pair candidates
(derived, no time series); the U0 witness is `trade_id / condition_id / ts /
source` with **no prices at all** — a timing quarantine artifact.

## Join provenance — why nothing merges

Three identity spaces, and they do not overlap in the way that matters:

- **Polymarket CLOB** — `condition_id` + ERC-1155 token id (dataset 1).
- **PMUS** — `marketSlug` + `marketSides[].id` (datasets 2, 3, 4).
- They are different venues with different books. A shared *game* is not a
  shared *price*, and Track B-L's `FINDING B-1` plus the Phase X1 side-
  orientation contradiction both show why a slug-level merge would be
  wrong.

Within PMUS, datasets 2/3 (books) and 4 (outcomes) share the slug space —
and **intersect in zero markets**. There is nothing to join, so the question
of contaminating time ordering does not even arise.

## The most important question, answered

> Can we test `P(outcome | information at t)` against a **two-sided** market
> state at `t`, without RN1 selecting the sample, without leakage, without
> assuming maker fills, queue position, or missing bid prices?

**No — with today's data.** We hold two-sided state on 30 markets and
outcomes on 10,257, and the overlap is empty.

## `DATA-B` — the single minimum additional dataset

One forward capture. Not a wishlist.

**PMUS two-sided pre-game BBO + depth snapshot, with settlement follow-up.**

| | |
|---|---|
| **FIELDS** | `marketSlug`, `marketSides[].id`, full `bids[]` and `offers[]` ladders (px + qty, all levels), `stats.lastTradePx`, `stats.sharesTraded`, `stats.openInterest`, `status`, `gameStartTime`, `endDate`, `sportsMarketTypeV2`, event tags (sport/league), `transactTime`, local wall + monotonic receipt clocks |
| **SAMPLING METHOD** | Enumerate the **whole** current sports board (`active=true&closed=false`, walked to a real terminal boundary — not a prefix), then snapshot **every** eligible market. No scoring, no shortlist, no activity filter: the selection rule must be "it was on the board", or the bias problem returns. |
| **CADENCE** | One snapshot per market at a fixed offset before `gameStartTime` (**T−60 min**), plus one at **T−10 min**. Two points, not a tick stream — the sample size is *markets*, not ticks, so cadence buys far less than breadth. |
| **MINIMUM DURATION** | Run until the settled-market target is met. Duration cannot be stated in advance because `PMUS_SETTLEMENT_THROUGHPUT = NOT_IDENTIFIED`; the capture measures it in its first days and the estimate follows from that. |
| **MINIMUM MARKETS** | **≈2,500 settled markets** with both a snapshot and an outcome. At p≈0.5 that resolves a 2 pp calibration deviation at 95% confidence (`n ≈ (1.96/0.02)²·0.25`). Fewer cannot distinguish a real 2 pp edge from noise; segmenting by sport or price band needs that count *per slice*. |
| **SETTLEMENT FOLLOW-UP** | Re-read each captured slug after `endDate` until `MARKET_STATUS_RESOLVED`, and store `outcomePrices` verbatim. This is the step whose absence is the entire gap. |
| **WHY IT RESOLVES THE QUESTION** | It supplies, on one venue in one identity space, the three things no current dataset holds together: a **two-sided** price (so fair value can be referenced to a midpoint rather than to an execution price), an **independent** selection rule (so the population is the board, not RN1's flow), and an **outcome** for the same market. That is exactly the input Track P's `P-C` result lacked. |

Everything else Track P would want — live/pregame state, score state, a
dense tick series — is deliberately **excluded** from this ask. They are
refinements to a question we cannot yet pose.

Estimated venue cost at the 2.5 s floor: two reads per market plus a
settlement re-read. Nothing about it needs a credential, an order, or a
production change — only the already-approved read-only PMUS research path,
from an environment that can reach `gateway.polymarket.us` (this container
currently cannot; see `PHASEX_EGRESS_REQUEST.md`).

## Maker warning — honoured

Nothing here treats *"the ask is systematically expensive"* as evidence that
posting bids is profitable. `BLOCK_4` already showed displayed maker edge
failing to become executable edge: 22,297 shares of bid queue against 180
shares traded in 16 minutes, and zero touches. **PREDICTIVE EDGE** and
**MAKER EXECUTION EDGE** stay in separate registers, and the capture above
measures neither — it measures whether predictive information exists at
all.

## Status

```
DATA_GATE              = DATA-B
TRACK_P_GATE           = P-C (locked, preserved as a negative result)
PHASE_X                = FROZEN at b5b2b63; both hosts connect_rejected
```

`mirror_live = false`. No capital, no orders, no production writes.
