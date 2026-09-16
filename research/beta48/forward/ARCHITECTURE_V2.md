# BETTOR ARCHITECTURE V2 — execution modes, capital, data

Architecture/research only. Nothing here was activated, authenticated, or
traded. No combos created, no RFQs, no quotes, no WebSocket connected.

Every claim below is marked with how it is known:

- **[CAPTURED]** — read out of bytes this programme fetched and hashed.
- **[MEASURED]** — computed from captured market rows.
- **[RELAYED]** — told to us; not yet in our own evidence.
- **[NOT_IDENTIFIED]** — not known, and not inferred.

---

## 1. COMBOS + RFQ

### The one thing captured so far — a dated COST, not a settled verdict

**[CAPTURED]** from `https://docs.polymarket.us/fees`, sha256
`25223cc8…0c8227`, fetched 2026-09-16 00:57:12Z, asserted in
`test_fee_page_capture.py` against the stored bytes:

> "**Upcoming fee changes.** … **Combos** — the combo taker fee curve
> **becomes** `Fee = C × p × [0.0695(1 - p) + 0.04(1 - p)^4]`"

**This is the UPCOMING schedule, not today's.** It sits under the page's
"Upcoming fee changes" callout, beside the sentence that the schedule below it
"describe[s] the fees in effect today". So:

```
UPCOMING_COMBO_TAKER_FEE_FORMULA = C × p × [0.0695(1-p) + 0.04(1-p)^4]
                                   effective 2026-09-16 23:59 ET
CURRENT_COMBO_TAKER_FEE_FORMULA  = NOT_IDENTIFIED
```

The current curve was **not** captured, and is not back-formed by swapping
0.0695 for 0.06 — nothing on the page says the surcharge term exists today.
`upcoming_combo_taker_fee()` raises for any other regime.

Against the **upcoming** single-leg fee (both sides post-cutover, so the two
regimes are never blended), it is that fee plus a surcharge:

```
combo = 0.0695·C·p·(1-p)  +  0.04·C·p·(1-p)^4
      = single-leg fee    +  a strictly positive surcharge
```

The surcharge is maximised at p = 0.2 and is worth **$0.0032768 per contract**
there. Measured against the single-leg taker fee at the same price:

| p | single-leg | combo | surcharge | premium |
|---|---|---|---|---|
| 0.05 | $3.30 | $4.93 | $1.63 | **+49.3%** |
| 0.10 | $6.26 | $8.88 | $2.62 | +42.0% |
| 0.20 | $11.12 | $14.40 | $3.28 | +29.5% |
| 0.30 | $14.60 | $17.48 | $2.88 | +19.7% |
| 0.50 | $17.38 | $18.63 | $1.25 | +7.2% |
| 0.90 | $6.2550 | $6.2586 | $0.0036 | **+0.06%** |

*(per 1,000 contracts, both columns on the UPCOMING schedule)*

The far-tail figure is **+0.06%**, not the +0.1% I first reported — rounding it
up nearly doubles it, and it is the one price where the premium is negligible,
which is a real fact about where combos cost least.

**What this establishes and what it does not.** The venue will charge a taker
premium for combos; that is verified from primary docs and dated. Whether the
premium buys materially lower execution or orphan risk is **exactly the
unresolved benefit**, and it is not recorded as zero:

```
UPCOMING_COMBO_TAKER_COST_PREMIUM       VERIFIED_FROM_PRIMARY_DOCS
COMBO_ORPHAN_RISK_REDUCTION             NOT_IDENTIFIED
COMBO_EXECUTION_ATOMICITY               NOT_IDENTIFIED
COMBO_PARTIAL_FILL_BEHAVIOR             NOT_IDENTIFIED
COMBO_NET_VALUE_VS_SINGLE_LEG_EXECUTION NOT_IDENTIFIED
```

### Field answers

| field | value | basis |
|---|---|---|
| `UPCOMING_COMBO_TAKER_FEE_FORMULA` | `C × p × [0.0695(1-p) + 0.04(1-p)^4]`, effective 2026-09-16 23:59 ET | **[CAPTURED]** |
| `CURRENT_COMBO_TAKER_FEE_FORMULA` | `NOT_IDENTIFIED` | not on the page; `upcoming_combo_taker_fee` **raises** rather than substitute 0.06 |
| `COMBO_MAKER_REBATE_TREATMENT` | `NOT_IDENTIFIED` | the page says maker rebates are unchanged *by the September update* — a statement about the update, not about combo legs |
| `COMBO_INCENTIVE_ELIGIBILITY` | `NOT_IDENTIFIED` | no combo row observed in `/v1/incentives` |
| `PAIR_SUBMISSION_SEMANTICS` | `NOT_IDENTIFIED` | **[RELAYED]** only |
| `COMBO_EXECUTION_ATOMICITY` | `NOT_IDENTIFIED` | unresolved in **both** directions |
| `COMBO_PARTIAL_FILL_BEHAVIOR` | `NOT_IDENTIFIED` | |
| `COMBO_REST_REMAINDER_BEHAVIOR` | `NOT_IDENTIFIED` | |
| `COMBO_LAST_LOOK_RISK` | `NOT_IDENTIFIED` | **[RELAYED]** that a last-look window exists. Its magnitude, and whether it is net-costly once the risk it removes is counted, are unmeasured |
| `EXECUTION_ATTRIBUTION_SOURCE` | `DROP_COPY` | **[RELAYED]** |
| `REQUIRED_AUTH_SCOPE` | `NOT_IDENTIFIED` | |
| `RETAIL_OR_INSTITUTIONAL_ACCESS` | `NOT_IDENTIFIED` | |

`comboEnabled` **[MEASURED]**: present on 20,000/20,000 board rows, **TRUE on
5,113 (25.6%)**. Combo-eligible markets are mostly *not* team markets
(3,535 without a team object vs 1,578 with), so the eligible set is not the
sports set.

### CAN_COMBO_RFQ_REDUCE_ORPHAN_RISK = **NOT_IDENTIFIED**

The **evidential asymmetry** is the thing to hold onto: the cost is captured
and dated, the benefit is relayed and unmeasured. That is a statement about
what we know, not about which is larger.

"Paired orders submitted" and "quote_executed" are submission events, not
fills — Drop Copy being the source of truth for fills is itself evidence that
the earlier states are not. So the mechanism by which combos *would* reduce
orphan risk is precisely what remains unestablished.

**Not concluded:** that a combo is merely a more expensive way to acquire the
same legging risk. A venue charging a premium for a paired instrument may well
be charging for real risk transfer, and pricing that away before measuring it
would be prejudging the benefit as zero — the same error as prejudging a cost
as zero, pointed the other way. `COMBO_NET_VALUE_VS_SINGLE_LEG_EXECUTION`
stays open.

Preserved as a future mode, not a plan: `EV_COMBO_RFQ`, alongside
`EV_MAKER_SINGLE`, `EV_TAKER_SINGLE`, `EV_PAIR_CONVERSION`, `EV_CROSS_VENUE`,
`EV_NO_TRADE`.

---

## 2. PORTFOLIO COLLATERAL RETURN

**[CAPTURED]** — the doc nav names two pages at exact URLs:
`/market-structure/collateral-and-margin` and
`/market-structure/mutually-exclusive-collateral-return`. Both are now in
`DOC_URLS` and will be fetched and hashed on the next segment.

Everything else here is **[RELAYED]** and stays so until that capture lands:

```
MARGIN_WITHOUT_OFFSET                 NOT_IDENTIFIED
MARGIN_WITH_OFFSET                    NOT_IDENTIFIED
BUYING_POWER_FREED                    NOT_IDENTIFIED
CAPITAL_REUSE_ALLOWED                 NOT_IDENTIFIED
CAPITAL_REUSE_RESTRICTIONS            NOT_IDENTIFIED
CLOSE_POSITION_COLLATERAL_REQUIREMENT NOT_IDENTIFIED
NET_DOLLARS_PER_CAPITAL_DOLLAR_PER_HOUR NOT_IDENTIFIED
```

**The objective function is accepted and changes what we optimise.** Not
`ROI_PER_TRADE` but

```
EXPECTED_NET_DOLLARS / WORKING_CAPITAL / TIME,  subject to bounded risk
```

Two consequences worth stating now, before any numbers:

1. **Collateral return reduces a margin requirement, not economic risk.** A
   capital-efficiency gain multiplies whatever the per-trade economics are. It
   multiplies a negative just as faithfully. It can make a profitable engine
   better; it cannot make a losing one profitable, and the ledger's
   `TRADING_NET_EX_INCENTIVES` is what decides which one we have.
2. **The reuse restriction interacts badly with concentration.** Freed buying
   power may not increase exposure in the same event that generated it, so the
   efficiency gain is only realised by a book spread across *many* events —
   which is the opposite of what a single strong signal would ask for.

---

## 3. REAL-TIME MARKET WEBSOCKET

```
WEBSOCKET_DATA_VALUE                               HIGH
RETAIL_READ_ONLY_KEY_SCOPE_DOCUMENTED              NO
READ_ONLY_CREDENTIAL_ISOLATION                     NOT_IDENTIFIED
SAFE_TO_CONNECT_WITH_EXISTING_TRADING_CAPABLE_KEY  NO
WEBSOCKET_CONNECTED                                False
```

High value does **not** imply permission, and a test asserts the two
independently. The authenticated retail docs require an API key for trading,
portfolio **and** WebSocket endpoints, and **no documented retail read-only
scope was found**. Three routes could change that, all open questions, none
attempted:

```
A_SCOPE_LIMITED_KEYS_IN_DEVELOPER_PORTAL        NOT_IDENTIFIED
B_INSTITUTIONAL_OAUTH_READ_ONLY_SCOPE           NOT_IDENTIFIED
C_FIXED_FUNCTION_BROKER_PLUS_PATH_RESTRICTION   NOT_IDENTIFIED
```

Route C is ours rather than the venue's: a fixed-function signing broker plus
network-path restriction could make the *capability* non-trading even if the
underlying credential is broader. That is a **containment** argument and it is
weaker than a scope the venue enforces — it has to be argued on its own merits,
not assumed equivalent to A or B.

**Not connected. No credential requested.** `/api-reference/websocket/overview`
is now in `DOC_URLS`, so the scope question gets a captured answer next segment.

**The blocker is preserved explicitly.** The current capability boundary is not
a policy, it is a property: `fwd_collect.py` has no code path that could build
an auth header, imports no `os`, and is proved so by AST scan as a gate before
any venue contact. An API-keyed WebSocket would **dissolve that property**, and
a key that can read is the same key that can write unless the venue scopes them
apart. So:

> If read-only capability cannot be cryptographically or scopably isolated from
> order writes, the WebSocket stays unavailable to this programme regardless of
> how valuable its data is.

That is a real cost. The WebSocket is the only plausible source for
`TRADE_AGGRESSOR`, `QUEUE_DEPLETION_PROXY`, `TOUCH_TO_TRADE`,
`POST_TRADE_MARKOUT`, `PASSIVE_FILL_INFERENCE` — which is to say, for four of
the five terms the maker case currently cannot measure. Naming that early: **if
isolation is impossible, the forward programme has a ceiling it cannot pass by
polling.**

---

## 4. PMUS FORWARD SPORTS METADATA — measured, not assumed

**[MEASURED]** over the 20,000-row observed prefix (segment 35042094434).
Marked `AVAILABLE` only where captured data proves population.

| field | status | evidence |
|---|---|---|
| `PMUS_FORWARD_SPORT` | **AVAILABLE** | `category` 20,000/20,000; `sports` 13,092 (65.5%), `politics` 6,213, `culture` 448, `finance` 77, … |
| `PMUS_FORWARD_LEAGUE` | **AVAILABLE_ON_SUBSET** | `marketSides[].team.league` on **3,501/20,000 (17.5%)**, 30 distinct leagues (cfb 2,662, nfl 2,312, mlb 346, epl 320, …) |
| `PMUS_FORWARD_GAME_START` | **POPULATED_SEMANTICS_UNPROVEN** | `gameStartTime` 20,000/20,000 — but 14,873 rows are `SPORTS_MARKET_TYPE_FUTURE`, where a "game start" is not a game start. Population ≠ meaning. |
| `PMUS_FORWARD_LIVE_STATE` | **ABSENT_ON_THIS_ENDPOINT** | field present on **0** rows |
| `PMUS_FORWARD_SCORE` | **ABSENT_ON_THIS_ENDPOINT** | **0** rows |
| `PMUS_FORWARD_ELAPSED` | **ABSENT_ON_THIS_ENDPOINT** | **0** rows |
| `PMUS_FORWARD_SPREAD_TOTAL` | **AVAILABLE_ON_SUBSET** | `line` 4,306 (21.5%); `spreadTotalSuffix` 2,121 (10.6%); `sportsMarketTypeV2` 20,000/20,000 with SPREAD 1,470 and TOTAL 651 |
| `PMUS_FORWARD_PROVIDER_IDS` | **AVAILABLE_ON_SUBSET** | `team.providerIds` on 3,491 (17.5%); `team.providerId` 6,805 side-entries |

**A correction to the relay.** Live state, score and elapsed time are **not**
on `/v1/markets` — measured absent on all 20,000 rows, which is a stronger
statement than `NOT_IDENTIFIED`. They may exist on an endpoint we have not
called. The old whale dataset's `LEAGUE = NOT_IDENTIFIED` should indeed **not**
be carried forward — league is available on 17.5% of the prospective board —
but neither should the richer claim be accepted wholesale.

This does materially help cross-venue matching: `team.providerIds` plus a
league code is a far better join key than the slug grammar the coverage
programme has been fighting.

---

## 5. INCENTIVE ECONOMICS — can we estimate without account history?

Four channels, never merged, each bound in `ledger.py` to the economic basis it
is actually paid for.

| estimate | answer | why |
|---|---|---|
| `EXPECTED_LIQUIDITY_REWARD` | **PARTIALLY** | pool, target size, discount factor and period are **[CAPTURED]** per market. What is missing is the **total qualifying score across all participants through time** — the denominator. The depth panel can say whether our quote would be *inside the scoring range*; it cannot say what share it would win. |
| `EXPECTED_FILL_REWARD` | **NO** | requires a fill probability for our own resting orders. `MAKER_FILL_PROBABILITY = NOT_IDENTIFIED`, and BLOCK_4 stands (22,297-share displayed queue, 180 shares traded in 16 min, **zero touches**). |
| `EXPECTED_VOLUME_REWARD` | **NO** | paid on taker-side notional. Estimating it needs our own prospective taker volume, which is a decision we have not made and must not assume. |
| `NEGOTIATED_MM` | **NO** | `NOT_IDENTIFIED`, and bound to a basis no credit can match. |

`INCENTIVES_ENDPOINT_COVERS_ALL_PROGRAMS = NOT_IDENTIFIED` — only
`liquidityProgram` rows have ever been observed. Its silence about the other
three is not evidence they are not running.

---

## THE REQUESTED NOTE

**`BETTOR_EXECUTION_MODES_V2`**
`EV_MAKER_SINGLE` (primary) · `EV_TAKER_SINGLE` (measured worse than
no-trade at zero edge, 23/23 cells) · `EV_PAIR_CONVERSION` ·
`EV_COMBO_RFQ` (**new, preserved, cost captured / benefit unproven**) ·
`EV_CROSS_VENUE` · `EV_NO_TRADE` (default).

**`CAPITAL_EFFICIENCY_FEATURES`**
Mutually-exclusive and directional collateral return, classified
`CAPITAL_EFFICIENCY_MULTIPLIER` — explicitly **not** an `ALPHA_SOURCE`.
Objective moves to `NET_PNL_PER_WORKING_CAPITAL_DOLLAR_PER_HOUR`, to be
computed **with and without** collateral return. All magnitudes
`NOT_IDENTIFIED` pending the captured margin pages.

**The hazard, recorded as a binding flag rather than a footnote:** freed buying
power spent in other events can make the offsetting leg **uncloseable** exactly
when we most want to close it — the close may require restoring the collateral,
and may be *rejected* if that capital is deployed. That converts a margin
optimisation into a liquidity obligation, under stress, which is the shape of
every forced-exit failure this programme has already recorded. Hence
`ALLOCATOR_MUST_RESERVE_UNWIND_CAPACITY = True`.

**`NEW_DATA_AVAILABLE`** (all read-only, no new auth)
`comboEnabled` (25.6% TRUE) · category/sport · league on 17.5% ·
`team.providerIds` for cross-venue joins · `line` / `spreadTotalSuffix` /
`sportsMarketTypeV2` · full depth ladders both sides · three tick sizes ·
the captured fee page itself.

**`NEW_AUTH_REQUIREMENTS`**
Markets WebSocket → API key. Combo/RFQ quoting → authenticated + probably
institutional. Drop Copy → authenticated. **None requested. None held.**

**`WHAT_CAN_BE_TESTED_READ_ONLY`** (no approval needed; boundary unchanged)
1. Capture and hash the margin/collateral, incentives-overview, API-reference
   and WebSocket-overview pages → answers §2 and §3 from evidence. *Already
   wired; lands next segment.*
2. Combo instrument surface on the public board — how many combo-enabled
   markets, in which categories, at which ticks.
3. The whole depth panel: Target Size scoring-range eligibility, size ahead,
   frequency with which the target is already met at the touch.
4. Sports metadata coverage and the provider-ID join, measured not assumed.
5. Fee-regime cutover: the board's own `feeCoefficient` should move 0.06 →
   0.0695 at **2026-09-16 23:59 ET = 2026-09-17 03:59 UTC** — roughly 26 hours
   after the first captures, **not** the same night. **That is the cleanest
   possible `VERIFIED_FROM_CAPTURE` test and it costs nothing** (we already
   poll the board every two hours), but the first eligible segment is the one
   that starts **after** that instant. Every segment before it is expected to
   read 0.06, and reading 0.06 early is confirmation, not contradiction.

**`WHAT_REQUIRES_USER_APPROVAL`**
Any API key, even read-only · any WebSocket connection · any combo, RFQ or
quote · Drop Copy · anything touching Track A · any capital.

**`WHICH_FINDING_CAN_SHORTEST_TIME_TO_PROFITABILITY`**

**The WebSocket — if and only if a read-only key can be isolated.**

Reasoning, stated so it can be argued with: the maker case is not blocked on
*ideas*, it is blocked on four unmeasured terms — fill probability, queue
position, adverse selection, and touch-to-trade. Polling a book every few
seconds cannot measure any of them; the events are sub-second and the identity
of the aggressor is not in a snapshot. The WebSocket carries maker side, taker
side and both intents, which is exactly that missing set. It is the only item
on this list that converts `NOT_IDENTIFIED` into a number for the terms that
currently decide the whole question.

Ranked against it:

- **Collateral return** is a multiplier on an unknown sign. High value *after*
  the engine is proven, near-zero value before.
- **Combos/RFQ** currently have captured costs and relayed benefits. On present
  evidence they lengthen the path rather than shorten it.
- **Sports metadata** is real and cheap and improves fair value and
  cross-venue matching — but fair value is Engine B's grave, and this
  programme's live question is execution, not prediction.

So: **C first, and if C is blocked by scope isolation, say so plainly and
accept that the forward programme has a polling ceiling.** That blocker is
worth discovering in the next capture rather than after weeks of polling.
