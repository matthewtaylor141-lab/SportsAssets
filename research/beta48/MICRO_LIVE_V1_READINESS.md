# MICRO-LIVE V1 — READINESS

Plain English. Nothing here is a profitability claim, and the last two sections
say exactly what we still cannot do.

*No order has been placed. No capital is deployed. No trading credential has
been used or requested. `mirror_live = false`.*

---

**1. What the first live test will do**

Place **one** passive maker order, on **one** market, in **one** event, for a
tiny size, and watch what happens to it. Rest at a price that does not cross the
spread, wait, and record: whether it filled, how long it took, what the market
did in the seconds after, whether we could close the position as expected, and
what fees or rebates actually landed.

That is the whole test. It is an instrumentation experiment that happens to use
real money, not a trade we expect to profit from.

**2. What it will NOT do**

It will not cross the spread, chase a price, scale up after a loss, re-enter
after being stopped, run two orders at once, or use leverage. It will not run
unattended across many markets. It will not be allowed to grow into a strategy
because the first fill looked good.

**3. Maximum theoretical exposure**

`NOT_SET`, deliberately, and this is the one number management must supply
rather than read. The system does not choose the limit that restrains the
system. Fifteen risk gates are built and every one of them currently reads
`AUTHORIZATION_STATUS = NOT_SET`, which **blocks** submission exactly as a
breach would.

What we can say is the shape: exposure is bounded by `MAX_ORDER_NOTIONAL` on a
single order, and because the test is one order at a time on one market, the
maximum theoretical loss is the notional of that one order if the contract
settles worthless against us. There is no path by which a second order joins it
without a second authorization.

**4. What order type is used**

A post-only limit order — a resting maker quote that the venue must reject
rather than fill if it would cross. That rejection is a feature: it is the
venue proving the order never took liquidity. Taker execution is not part of
phase 1 and is off by code default.

**5. How the order can be cancelled**

By an explicit cancel request, and the design assumes the cancel can LOSE the
race. A fill that arrives after we asked to cancel is classified as
`LATE_FILL_AFTER_CANCEL_REQUEST`, freezes further execution, and is reconciled
before anything else happens. We own what the venue filled, whatever we
intended a second earlier.

**6. How inventory is handled**

If the order fills, we hold a real position. The plan for it is decided before
the order is sent, not after: a passive close at a declared price, with an
aggressive close available if the passive one does not work within a declared
window. Inventory that outlives `MAX_INVENTORY_AGE` is closed rather than held
hopefully. There is no "wait for it to come back".

**7. What triggers an immediate stop**

Any of fifteen fail-closed conditions: a stale book, a missing price, missing
event or market identity, an unknown EV-critical input, a code or config SHA
mismatch, a missing or breached risk limit, a duplicate order, unexpected
existing inventory, a venue response we do not understand, an inconsistent
position state, a telemetry failure, or the kill switch. Any one of them, and
execution stops.

Two design choices worth stating plainly. An **unevaluated** condition blocks
just as a tripped one does — a gate that only stops what it was asked about has
a hole in it. And an unrecognised condition name also blocks, so adding a new
risk to the list cannot silently do nothing.

**8. What metrics define success**

Phase 1 succeeds if the **machinery** is correct, whatever the money does:

- the order submitted correctly and the venue acknowledged it
- the fill (or the absence of one) was recorded correctly
- position accounting matched the venue exactly
- cancellation behaved as designed, including if it lost a race
- no risk limit was violated
- no duplicate order was created from a retry
- no orphan inventory was left behind
- telemetry was complete enough to compare predicted EV against realized outcome

**9. What metrics define failure**

Any mismatch between our books and the venue's; a duplicate order; an
unexplained fill; inventory we did not intend and cannot account for;
incomplete telemetry; or a risk gate that did not fire when it should have.

Note that "the trade lost money" is **not** on this list.

**10. What we learn even if the trade loses money**

The thing we have never been able to measure: whether a BETTOR maker order
actually fills, how long it waits, and what the market does immediately after.
Every fill-rate number in this programme so far has been inferred from somebody
else's trades on another venue. One real resting order produces the first
observation that is actually ours.

We also learn whether our pre-trade EV estimate bears any relationship to the
outcome — and if it does not, that is worth more than the stake.

**11. How many orders the first phase needs**

More than one and fewer than a strategy. A single fill tells us the plumbing
works; it tells us nothing statistical, because one observation has no
distribution. The honest sequence is: one order, fully reconciled and reported,
then a decision about whether to do a handful more.

We are **not** proposing a number of orders that would support a profitability
conclusion, because that number is large and we have not earned the right to
ask for it.

---

**12. The sandbox, and its one important limitation**

A genuine non-production environment exists and we already have an adapter for
it: the institutional **pre-production** exchange (E35, `backend/sportsassets/
pmx.py`), reached with its own credentials, with a host guard that refuses to
import if pointed anywhere else.

`VENUE_TEST_ENVIRONMENT_AVAILABLE = YES (PRE_PRODUCTION, MECHANICS_ONLY)`

The limitation matters and was recorded when the adapter was built: **preprod's
books are empty.** A market the retail venue quotes live comes back with no bids
and no offers. It has dummy funds and no counterparties. So preprod can certify
that we construct, send, acknowledge, cancel and account for an order correctly
— and it can never tell us whether an order fills, what spread we realize, or
what a rebate is worth. Those questions belong to production.

That is why the dry-run rehearsal, not the sandbox, is today's test bed for the
decision logic, and why the first fill question needs a real order eventually.

**13. The rehearsal we ran today**

Twelve candidates drawn from sealed public-book evidence, round-robin across six
markets in observation order — not ranked by how attractive the book looked,
which would have selected the moments that flatter the rehearsal.

**All twelve ended `NO_TRADE` / `WOULD_NOT_SUBMIT`. Zero orders were
constructed. That is the correct result.**

Two EV-critical inputs are genuinely absent: BETTOR has no independent fair
value wired into this harness, and no admitted fills of its own from which to
estimate a fill probability. The whale completion rate is *forbidden* as a
substitute — it measures whether somebody else's counterparty turned up, on a
different venue, for trades they chose to open. With those terms
`NOT_IDENTIFIED`, no valid EV comparison exists, and the code refuses rather
than substituting zero.

A rehearsal that produced confident `TRADE` verdicts from those inputs would be
reporting a number it had invented.

**14. The minimum-size constraint, reported separately as asked**

Maker rebates are typically quoted per contract and settle in whole cents. A
one-contract order can therefore produce a rebate that rounds to zero, which
would make the rebate leg of the experiment unmeasurable at the smallest
possible size. This is a **measurement** constraint, not a reason to trade
bigger: it means either the first order is sized so the rebate is at least one
cent, or phase 1 declares up front that it is not measuring rebates and leaves
`REALIZED_REBATES = NOT_IDENTIFIED`.

We have not chosen between those, because choosing the first one means choosing
a dollar size, and that is management's call. The venue's actual rebate schedule
and rounding rule are themselves `NOT_IDENTIFIED` in our evidence today.

---

**15. What this does not establish**

- `PUBLIC_TICK_FILL_IDENTIFICATION = NO` — public snapshots cannot identify our
  fills
- `ACTUAL_BETTOR_FILL_RATE = NOT_IDENTIFIED` — we have never observed one
- `REALIZED_MAKER_ECONOMICS = NOT_ESTABLISHED` — no realized spread, no realized
  rebate, no realized adverse selection
- `MICRO_LIVE_AUTHORIZED = NO`

The correct description of where we stand: *the execution harness is built and
refuses correctly; the inputs that would let it say yes are missing, and the
limits that would let it act have not been set.*
