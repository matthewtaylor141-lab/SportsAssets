# The venue's own collateral mechanics — captured, and what they settle

**Source:** the venue's published documentation, retrieved through `fetch-docs.yml` on a GitHub runner (this container's egress to `docs.polymarket.us` is denied). Read-only: no secrets, no credentials, no writes.

| Page | Run | sha256 of retrieved bytes |
|---|---|---|
| `/concepts/orders.md` | 36371942058 | `ebe5d70c6820a175e761dbc2a7b88798a292bd45e6dd9397b8a6c78e41299635` |
| `/market-structure/mutually-exclusive-collateral-return.md` | 36371906029 | `7b56aad70dc5ea4550e31b429c052d7f2600774eb5940b330866f1f257a83164` |
| `/market-structure/directional-collateral-return` | 36371871386 | *(HTML render; see run log)* |
| `docs.polymarket.us/` index | 36371843660 | *(index listing)* |

---

## 0 · THE LIQUIDITY QUESTION IS SETTLED, and against my own earlier framing

`/concepts/orders`, verbatim:

> "You don't trade YES and NO as separate things. **There's only one instrument per market — the YES side.** To trade against an outcome, you **sell** YES (which is the same as buying NO)."

> "when you place an order, **the price always refers to the YES side.** If you want to buy NO at \$0.40, you're really selling YES at \$0.60 — the system handles this, but you need to understand it to set the right price."

**So "take the complement" and "sell our long" are not two routes to compare. They are the same order on the same book, quoted in complementary units.** There is exactly one book: the YES book.

This resolves three things at once:

1. **`ONE_SIGNED_NET_POSITION_PER_MARKET` is now established from the venue**, not from our design. "A long position means you own YES contracts; a short position means you've sold YES contracts." One instrument, one signed position.
2. **The unequal-proceeds example was an input inconsistency**, exactly as suspected when I was told *"Do not count the same depth twice or manufacture an advantage through inconsistent prices."* With one instrument, buying NO at `a` is selling YES at `1−a`, so the proceeds are equal **by construction**. There was no advantage to find.
3. **It did not need venue access.** I had recorded that settling this required "reading both marketSides' books in one venue snapshot, which needs venue access," and had asked whether to specify an endpoint and credential capability. Neither was necessary — the answer was in the public documentation. That estimate was wrong.

**What changed in the code:** `same_liquidity_risk` now carries `status: ESTABLISHED_ONE_BOOK`, and the ineligibility code moved from `ADVANTAGE_RESTS_ON_UNESTABLISHED_LIQUIDITY` to **`ADVANTAGE_IS_THE_SAME_BOOK_QUOTED_TWICE`** — ineligible on *present* evidence rather than *absent* evidence, which is a stronger and different claim. The old code is retained as `supersedes_code` so stored rows stay readable. The candidate is still priced and still annotated, because a visible price inconsistency is how a bad input gets found; it simply cannot win on a difference that is not executable.

**The two tests that asserted otherwise are placed, not deleted.** `bettor_hedge_tax` and `test_the_cheaper_exit_ladder_wins_and_is_still_a_reduction` are valid arithmetic for a **TWO_TOKEN** venue where YES and NO are separate instruments with their own books. They are not evidence about PMUS, and `WHERE_TWO_ROUTES_ARE_REAL` says so. This follows the instruction directly: *"Keep the unequal-price example as a mathematical test for a venue model that explicitly supports independent executable routes."*

### And `/concepts/market-data` closes it completely

`/concepts/market-data.md`, run 36372574713, sha256 `5f96ff5b577b978179003bcdb1959a0c1178b0e535082ca524e4c7622632a021`:

> "Polymarket US runs a **central limit order book**… The order book has two sides: **Bids** — buy orders, the prices traders are willing to pay. **Asks (offers)** — sell orders, the prices traders are willing to accept."
> "The **BBO (best bid and offer)** is the tightest price on each side."

**One** central limit order book, with bids and asks on the **one** instrument. So selling our YES long consumes the *bid*; "buying NO" is also selling YES, so it consumes **the same bid**. Not two books, not two pools of depth — one side of one book, reached by one order. The question is closed on both halves: one instrument (from `/concepts/orders`) and one book with two sides (from here).

This also confirms `pmus.slug_bid`'s measured quote shape was reporting exactly that: `long.price == bestAsk` and `short.price == 1 − bestBid` are two views of the same BBO, which is why five of five agreed.

### Order mechanics also confirmed

Time in force: **GTC, GTD, IOC, FOK** — FOK *"must fill completely or not at all"*, which is what `pmus.submit_fok` sends. Order lifecycle: **Pending → Open → Partially filled → Filled → Canceled / Expired / Rejected**. Market states: **Open / Pre-open / Suspended / Halted / Expired**. Maker/taker is as assumed: a crossing limit order fills immediately, otherwise it rests.

Settlement: *"every contract settles at either \$1.00 (YES won) or \$0.00 (NO won)"* — binary, with no partial payout on this page. That does **not** resolve the per-condition grading questions (draw, overtime, void/abandonment), which live in the rulebook and remain as the settlement census recorded them.

This replaces inference from our own tests. The owner's instruction was explicit: *"Stop treating the existing tests as authority for venue mechanics… Establish the liquidity model from authoritative mechanics."*

---

## 1 · There are two collateral-return mechanisms, and neither is "complete a pair"

**Directional collateral return** — for events where *lower thresholds must be true if higher ones are* (spread ladders, e.g. `asc-nfl-kc-phi-2026-02-09-neg-3pt5` vs `-neg-6pt5`):

> "A lower-ranked long offsets a higher-ranked short (not the reverse)"
> "One long position can offset multiple short positions across higher ranks"

**Mutually exclusive collateral return** — for events where only one outcome can occur (elections, championships):

> "reduces your margin requirement when you hold offsetting **short** positions in instruments from the same event where only one outcome can occur"
> "your margin requirement is reduced by the smaller position size"

**Neither describes buying the complement of a held long to form a settled pair that releases collateral.** The directional mechanism pairs a long against a *higher-ranked short*; the mutually-exclusive mechanism pairs *shorts against shorts*. So:

- `MERGE_MECHANISM` stays **NOT_IDENTIFIED**. The venue publishes no merge or netting-to-cash on a completed binary pair in either page.
- `TAKE_COMPLEMENT`'s claimed capital release remains **unestablished**, and its `collateral_release: NOT_IDENTIFIED` annotation is correct.
- Both actions stay **OPEN** management-report requirements. Nothing here qualifies them for execution.

## 2 · Both mechanisms are margin optimization, not risk reduction

Stated identically on both pages:

> "This is a portfolio margin optimization, **not a reduction in actual risk**"

So a pairing strategy that treats freed buying power as a reduced-risk position is misreading the mechanism. The margin falls; the exposure does not.

## 3 · Three facts that bear on our lane and are not modelled

### 3.1 An exit order can be **rejected** because freed collateral was redeployed

> "When you close one of the offsetting positions, you must 'return' the collateral that was freed up… If that buying power is already deployed elsewhere, **the order will be rejected**."

`submit_exit` does not model this. On an account with collateral return enabled and freed buying power deployed elsewhere, a perfectly-formed exit can be refused *by the venue* for a reason our refusal vocabulary has no code for. It would currently surface as a generic venue error.

**Status: OPEN.** Named here rather than patched, because the account's collateral-return entitlement is unknown (see §4) and inventing a refusal code for a mechanism we have not observed would be guessing.

### 3.2 Collateral return is not computed from intended orders

> "Hypothetical collateral return from new orders is not factored into buying power checks. The exchange only considers your **current positions**."

Any sizing that assumes a complement purchase partly self-funds through immediate collateral release is wrong. The release follows the position, not the order.

### 3.3 Freed buying power cannot be redeployed into the same event

> "you cannot use this freed-up buying power to increase your position in the same directional event that generated the collateral return"

## 4 · What is still NOT established

- **Whether collateral return is enabled on our account.** Both pages condition on *"When collateral return is enabled on your account"* — it is an account-level entitlement, not a universal property. **NOT_IDENTIFIED**; it requires reading the account, which needs a credential this container does not hold.
- ~~Whether buying the complement nets against a held long.~~ **Settled — see §0.** One instrument per market.
- ~~Whether the two actions reach different matching liquidity.~~ **Settled — see §0.** They are the same order on the same book. `TAKE_COMPLEMENT` stays selection-ineligible, now on established mechanics.
- **Depth and fee comparison at actual quantity** was to be the empirical test of the two-route hypothesis. It is moot: there is one route. No such comparison is needed or meaningful.
- **Cross-event pairs.** The Bears/Panthers "both win" reasoning in the case studies spans **two separate games**. Mutually-exclusive collateral return explicitly requires *"instruments from the same event"*, so it does **not** apply. Whatever that construction is, it is not this mechanism.

## 5 · What changed in my position

I previously had no authoritative source for any of this and said so. I now have one for the two collateral-return mechanisms, and it **narrows rather than widens** the pairing scope: the venue's published capital efficiencies are long-vs-higher-short on threshold ladders and short-vs-short on one-winner events. The complement-completion pair the pairing brief describes is not among them, and the freed capital it would notionally release is not something these documents support.

This is a finding against the pairing design's assumed mechanism, not a finding that pairing is impossible. `/concepts/orders`, `/concepts/market-data` and the combos FAQ remain unread on the netting and liquidity questions.
