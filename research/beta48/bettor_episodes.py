"""EPISODES: whole trading lifecycles, with the inventory carried.

WHY THIS FILE EXISTS. Every economic number BETTOR has produced so far
was CONDITIONAL ARITHMETIC on a single book snapshot: "if both legs
fill, the pair earns +0.0086." A fill is not the end of a trade and a
cancellation is not the end of a trade. If inventory remains, the
episode is still running and its result is not yet known.

So this module defines an episode with a beginning and an end, runs it
on the time-ordered tape, and CARRIES EVERY REMAINING POSITION INTO
THE RESULT. No episode is dropped for being unfinished; unfinished is
a reported outcome.

────────────────────────────────────────────────────────────────────
THE EPISODE, DEFINED
────────────────────────────────────────────────────────────────────

BEGINS   at an observation where the market is OPEN, the book is
         two-sided and the spread is at least one tick. Two resting
         orders go up, both priced BY THE ENGINE
         (`incremental_ev("QUOTE_BID"/"QUOTE_OFFER")`):

           YES leg   buy YES  at the engine's bid quote,  size S
           NO  leg   buy NO   at 1 - the engine's offer,  size S

         The NO leg IS our YES offer. PMUS carries
         `shortQuote = 1 - bestBid`, so the NO book is the exact
         mirror of the YES book and a resting YES offer at `po` is a
         resting NO bid at `1 - po`. Modelling it as two long legs
         keeps the cash unambiguous: one YES plus one NO pays exactly
         1 at settlement, whatever happens.

         Collateral is committed at this instant, before any fill.

PARTIAL  each leg fills in pieces. Every fill is its own event with
FILLS    its own size, its own price and ITS OWN BANKER-ROUNDED FEE.
         A four-contract order that fills as 1+1+1+1 pays four
         separately-rounded fees, which is not the same number as one
         four-contract fill. That difference is the whole of section 3
         of the directive and it is applied here, not asserted.

CANCEL   at the quoting horizon the unfilled legs are cancelled. The
ACK      cancel is NOT effective at the instant it is requested: it
         takes effect from the NEXT observation, so any print in the
         interval containing the request can still fill us. The
         cancellation race is charged AGAINST us, every time.

RECOVERY unmatched inventory is not abandoned. The policy rests a
         maker exit on the opposite side for R_WAIT observations, and
         if that does not fill, crosses out as a taker. Matched pairs
         are left to settle: they pay exactly 1 and completing them
         early can only cost fees.

ENDS     at the first of:
           FLAT_PAIRED        both legs filled; a matched pair remains,
                              which settles at 1 with certainty
           FLAT_EXITED_MAKER  residual exited on a resting quote
           FLAT_EXITED_TAKER  residual crossed out
           CARRIED_SETTLED    the market expired while we held; valued
                              at the OBSERVED settlement
           CARRIED_OPEN       the capture ended while we held; valued
                              at the touch we could actually hit
           NEVER_FILLED       neither leg filled; the episode still
                              consumed collateral for its whole life

────────────────────────────────────────────────────────────────────
WHAT IS MODELLED AND WHAT IS ASSUMED -- the assumptions, named
────────────────────────────────────────────────────────────────────

A1  PRINTS -- NO LONGER AN ASSUMPTION WHERE THE TAPE COVERS US.
    This previously read: `sharesTraded` is cumulative, its increase
    between two observations is that interval's volume, `lastTradePx`
    gives ONE price for it, and "this cuts both ways and cannot be
    corrected from a BBO feed." The last clause was wrong. The venue
    publishes daily Time & Sales, all eight days of the capture window
    were retrieved complete, and `bettor_prints.py` now supplies the
    ACTUAL prints -- time, price and quantity -- for each interval.

    Measured against that tape the snapshot TOTAL was accurate (ratio
    1.00 on eight of twelve markets) and the PRICE ATTRIBUTION was not:
    one interval carries up to 120 distinct prices. So the defect was
    never the volume, it was pricing all of it at one price.

    WHAT THE TAPE STILL DOES NOT SETTLE. It has four columns and
    carries no side, no aggressor flag and no counterparty. A print
    establishes that trading reached a price. It does not establish who
    initiated it, so it is used only to test whether trading reached
    our quote, never to claim we know who lifted whom.

    When the tape is absent the snapshot path still runs, and every
    result records `tape_intervals` and `snapshot_intervals` so the two
    can never be silently mixed.

A2  QUEUE. Quoting strictly inside the spread puts us alone at our
    price, so we are at the front. Quoting at the touch puts us behind
    the observed depth. Both variants are reported, and a third
    (`QUEUE_BEHIND`) assumes we are behind the depth even when inside,
    because a real book may have hidden or simultaneous improvement.

A3  OUR OWN QUOTE IS NOT IN THE TAPE. The recorded book never saw our
    order. A resting quote inside the spread would have changed what
    others did. This is the standard replay limitation and it is not
    fixable from recorded data -- only by resting real orders.

A4  NO REBATE ELIGIBILITY IS VERIFIED. Everything is computed TWICE:
    once with the published maker schedule applied, and once with
    every rebate set to zero. If the two disagree about the sign, the
    policy's edge is the rebate and the rebate is unverified.

Run:  python research/beta48/bettor_episodes.py
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_policy_ev as ev                                   # noqa: E402
import bettor_prints as prints_mod                              # noqa: E402
import bettor_tape as tape                                      # noqa: E402

# ── the policy's parameters, frozen here so they are visible ──────────
QUOTE_HORIZON = 20      # observations the pair of quotes rests for
RECOVERY_WAIT = 10      # observations a maker exit rests before crossing
COOLDOWN = 1            # observations between episodes in one market


# ── THE SHARED POLICY ────────────────────────────────────────────────
#
# Imported, not copied. `backend/sportsassets/bettor_policy.py` is the
# ONE definition of every decision this replay makes, and the live loop
# imports the same module. A backtest of a policy the runtime does not
# run is a description of a program nobody executes.
_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
from sportsassets import bettor_policy as shared            # noqa: E402


def _book_of(row, tick, flow_per_s=None, vol_per_sqrt_s=None):
    """A replay row -> the shared policy's decision-time Book."""
    return shared.Book(bid=row.get("bid"), ask=row.get("ask"),
                       tick=tick or 0.01, state=row.get("state"),
                       depth_bid=row.get("bid_depth"),
                       depth_ask=row.get("ask_depth"),
                       flow_per_s=flow_per_s,
                       vol_per_sqrt_s=vol_per_sqrt_s)


LADDER_LEVELS = 10


def _ladder_of(row):
    """The book's own levels at the entry instant, bounded and dated.

    Bounded because an episode record carrying 63 levels x 420 episodes
    is a different artifact from a decision summary. Dated because the
    ladder is joined from a nearby snapshot rather than being the same
    message as the BBO -- `age_s` travels so a reader can see how near.
    """
    lad = row.get("ladder") or {}
    if not lad:
        return None
    return {"bids": [list(x) for x in (lad.get("bids") or ()
                                       )[:LADDER_LEVELS]],
            "offers": [list(x) for x in (lad.get("offers") or ()
                                         )[:LADDER_LEVELS]],
            "age_s": lad.get("age_s"), "causal": lad.get("causal"),
            "levels_kept": LADDER_LEVELS,
            "bid_levels_total": len(lad.get("bids") or ()),
            "offer_levels_total": len(lad.get("offers") or ())}


def trailing_vol(rows, i, window_s):
    """Realised mid volatility per root second, ending at row i.

    STRICTLY BACKWARD-LOOKING, for the same reason `trailing_flow` is:
    a live engine has the past and nothing else at the moment it
    quotes. Measured as the root-mean-square of the mid's increments
    inside the window, divided by the root of the mean increment --
    which is the per-root-second figure `vol_cover` scales to the
    horizon.

    Returns None when the window cannot be filled or too few usable
    mids are present, and None is a REFUSAL upstream, never a pass.
    """
    t_now = rows[i].get("t")
    if t_now is None:
        return None
    j = i
    while j > 0 and (t_now - (rows[j].get("t") or t_now)) < window_s:
        j -= 1
    if (t_now - (rows[j].get("t") or t_now)) < 0.5 * window_s:
        return None
    mids, ts = [], []
    for k in range(j, i + 1):
        b, a, t = rows[k].get("bid"), rows[k].get("ask"), rows[k].get("t")
        if b is None or a is None or t is None:
            continue
        mids.append(0.5 * (b + a))
        ts.append(t)
    if len(mids) < 3:
        return None
    ss, dt_total = 0.0, 0.0
    n = 0
    for k in range(1, len(mids)):
        dt = ts[k] - ts[k - 1]
        if dt <= 0:
            continue
        ss += (mids[k] - mids[k - 1]) ** 2
        dt_total += dt
        n += 1
    if n == 0 or dt_total <= 0:
        return None
    # sum of squared increments / total elapsed time is the variance
    # per second; its root is the per-root-second volatility.
    return (ss / dt_total) ** 0.5


def trailing_flow(rows, i, window_s):
    """Traded shares per second over the window ENDING at row i.

    STRICTLY BACKWARD-LOOKING, which is the whole point: this is the
    only form of the quantity a live engine could have at the moment it
    decides. `shares_traded` is a CUMULATIVE counter, so the flow is a
    difference -- and a difference that comes out negative means the
    counter restarted, which is not flow and is reported as unknown
    rather than as zero.

    Returns None when the window cannot be filled, and None is a
    REFUSAL upstream, never a pass.
    """
    t_now = rows[i].get("t")
    c_now = rows[i].get("shares_traded")
    if t_now is None or c_now is None:
        return None
    j = i
    while j > 0 and (t_now - (rows[j].get("t") or t_now)) < window_s:
        j -= 1
    t_then, c_then = rows[j].get("t"), rows[j].get("shares_traded")
    if t_then is None or c_then is None:
        return None
    span = t_now - t_then
    # A window we could not actually fill is not a measurement of it.
    if span < 0.5 * window_s:
        return None
    d = c_now - c_then
    if d < 0:
        return None
    return d / span


def decision_time_size(policy, base_size, row):
    """The clip for THIS book, from decision-time inputs only.

    Returns (size, why). `why` travels onto the episode so a reader can
    see which rule produced the number rather than inferring it.
    """
    if policy is None:
        return float(base_size), "FIXED"
    # THE SHARED IMPLEMENTATION DECIDES. This function is a thin
    # adapter from a replay row to the policy's Book, and nothing else.
    r = shared.quote_size(_as_shared(policy), _book_of(row, row.get("tick")),
                          base_size)
    return r["size"], "%s x%.2f -- %s" % (r["rule"], r["mult"], r["why"])


def _as_shared(policy):
    """The replay's Policy -> the shared Policy. Field for field.

    They carry the same names deliberately, so this cannot silently
    drop a knob: a field added to one and not the other raises here
    rather than quietly changing behaviour in only one consumer.
    """
    if isinstance(policy, shared.Policy):
        return policy
    kw = {}
    for f in dataclasses.fields(shared.Policy):
        if hasattr(policy, f.name):
            kw[f.name] = getattr(policy, f.name)
    return shared.Policy(**kw)


@dataclasses.dataclass(frozen=True)
class Policy:
    """EVERY DECISION THE REPLAY MAKES, in one frozen object.

    The first version of this file hard-coded one policy and then the
    result was reported as though it were a fact about two-sided market
    making. It is not: it is a fact about ONE entry rule, ONE quote
    placement, ONE inventory rule, ONE recovery path and ONE settlement
    treatment. Every one of those is a decision that could have been
    made differently, and several are decisions the case studies make
    differently.

    Each field below is therefore a knob, every combination that was
    run is recorded in the sweep, and the unsuccessful ones stay in the
    record rather than being quietly dropped.
    """
    name: str = "BASE"

    # ENTRY
    min_spread_ticks: int = 1        # do not quote a book tighter than this
    max_mid: float = 1.0             # do not quote above this mid
    min_mid: float = 0.0

    # THE FLOW GATE. Aimed at the measured constraint rather than at
    # the spread: 358 of 420 episodes never fill, and 88% of committed
    # capital-hours rest behind a quote that is never reached. Zero
    # leaves the gate off, which is every policy evaluated before it
    # existed -- so a comparison against those is still like for like.
    min_flow_cover: float = 0.0
    flow_window_s: float = 1800.0

    # THE VOLATILITY GATE. Where the flow gate asks whether a quote
    # will be REACHED, this asks whether being reached is worth
    # anything: a passive quote earns the spread and loses the move
    # between its two fills. Zero leaves it off.
    min_vol_cover: float = 0.0
    vol_window_s: float = 1800.0

    # QUOTE PLACEMENT
    #   ENGINE     whatever incremental_ev returns (improve by one tick
    #              where there is room, else at the touch)
    #   AT_TOUCH   always join the touch, never improve
    #   DEEPER_1   one tick BEHIND the touch -- worse fill odds, better
    #              conditional price
    placement: str = "ENGINE"

    # INVENTORY
    #   when one leg fills, does the other stay up?
    cancel_other_on_fill: bool = False
    #   stop quoting at all once |unmatched| reaches this multiple of size
    max_unmatched_mult: float = 1.0

    # TIME -- IN SECONDS. Observation counts were a proxy for time that
    # silently changed meaning between markets: the boxing market was
    # polled every ~112 s and the NFL markets every ~4,500 s, so
    # "20 observations" was 37 minutes in one and 25 hours in another.
    quote_horizon_s: float = 2400.0      # 40 minutes
    recovery_wait_s: float = 1200.0      # 20 minutes

    # QUEUE. Our position WITHIN a price level is not observable: the
    # venue publishes aggregate ladder quantity, not per-order queues.
    # This is the fraction of the quantity at our own price assumed to
    # be ahead of us. Everything strictly better than us is ALWAYS
    # ahead. Swept rather than asserted.
    queue_ahead_fraction: float = 1.0

    # EXECUTION SCENARIO -- how the queue AHEAD of us is consumed.
    #
    # Snapshot depletion is NOT executed volume. A level can shrink
    # because orders cancelled or repriced, and it can stay flat while
    # executions happened underneath replenishment. So depletion is not
    # a bound on fills in either direction, and the honest treatment is
    # a small set of LABELLED scenarios rather than one rule:
    #
    #   TRADE_ONLY        only printed volume advances our queue.
    #                     Cancellations never help us. Pessimistic.
    #   TRADE_OR_DEPLETION  the queue advances by whichever is larger.
    #                     Treats cancels and repricing as if they
    #                     cleared the queue. Optimistic.
    #   TRADE_PLUS_HALF   trades, plus half of any excess depletion.
    #
    # IN EVERY SCENARIO, ONLY TRADED VOLUME CAN FILL US. A cancellation
    # ahead of us moves us up the queue; it cannot buy our contracts.
    execution_scenario: str = "TRADE_ONLY"

    # RECOVERY -- what to do with unmatched inventory
    #   MAKER_THEN_TAKER  rest an exit, then cross out
    #   TAKER_NOW         cross out immediately
    #   COMPLETE_PAIR     buy the complement as a taker (mechanism 3,
    #                     in its time-dependent form)
    #   HOLD              keep it to settlement
    recovery: str = "MAKER_THEN_TAKER"

    # ── DECISION-TIME SIZING ─────────────────────────────────────────
    #
    # `size` was a run-wide constant, which the management
    # demonstration reported as NOT SUPPORTED. It now has a rule, and
    # the rule uses ONLY what is knowable when the quote is placed.
    #
    #   FIXED        the constant. Kept as the comparison baseline.
    #   EDGE_SCALED  scale the clip by the edge the book is offering,
    #                measured in ticks of spread over the minimum this
    #                policy will quote. A 4-tick book pays twice what a
    #                2-tick book pays for the same capital and the same
    #                queue risk, so it earns more size.
    #
    # WHAT IT DELIBERATELY DOES NOT USE: depth (the corpus has none),
    # realised fill rates (an outcome), and anything about what the
    # price later did. A rule that needed those could not be run by a
    # live engine at the moment it quotes.
    #
    # AND SIZING DOES NOT CHANGE THE RATE. Edge and capital both scale
    # with size, so per-capital-hour is invariant to a uniform clip.
    # What sizing changes is WHICH BOOKS get the capital -- it is an
    # allocation rule, not a profitability lever, and it is evaluated
    # as one.
    size_rule: str = "FIXED"
    size_min_mult: float = 0.5      # floor, as a multiple of the clip
    size_max_mult: float = 2.0      # ceiling, as a multiple of the clip

    # ── CAPITAL RELEASE ──────────────────────────────────────────────
    #
    # A matched YES/NO pair is worth exactly 1 at settlement and is
    # NOT cash until then, because no merge/netting call has been
    # demonstrated at this venue. The feasible alternative is to sell
    # BOTH legs back into the market and take the spread as the cost of
    # getting the capital back now.
    #
    # True does that. The comparison it enables is the one that
    # matters: is releasing capital at a cost of the spread better than
    # holding it to settlement for free?
    release_matched: bool = False

    # SETTLEMENT
    #   False  inventory may ride through expiry
    #   True   cross out at the last OPEN observation before expiry
    hard_flatten: bool = False


BASE_POLICY = Policy()

# Episode end states
FLAT_PAIRED = "FLAT_PAIRED"
FLAT_EXITED_MAKER = "FLAT_EXITED_MAKER"
FLAT_EXITED_TAKER = "FLAT_EXITED_TAKER"
CARRIED_SETTLED = "CARRIED_SETTLED"
CARRIED_OPEN = "CARRIED_OPEN_AT_HORIZON"
NEVER_FILLED = "NEVER_FILLED"

QUEUE_MODELS = ("QUEUE_FRONT_IF_INSIDE", "QUEUE_BEHIND_ALWAYS")


def _maker_fee(px, n, rebates_on, at_epoch=None):
    """Signed cash from the maker schedule on ONE fill of n contracts.

    The maker coefficient is -0.0125 in BOTH published regimes, so
    `at_epoch` changes nothing here. It is threaded anyway so the two
    fee paths look the same and a future maker change cannot be missed.
    """
    if not rebates_on:
        return 0.0
    return ev.fee(px, n, maker=True, at_epoch=at_epoch)


def _taker_fee(px, n, at_epoch=None):
    """THE REGIME IS SELECTED BY THE FILL'S OWN TIMESTAMP.

    theta_taker moved 0.06 -> 0.0695 at 2026-09-17T03:59Z and this
    capture runs 09-13 to 09-20, so a single constant is wrong for one
    side of the corpus whichever value it takes.
    """
    return ev.fee(px, n, maker=False, at_epoch=at_epoch)


class Episode:
    """One quoting lifecycle in one market. Nothing is dropped."""

    def __init__(self, slug, event, i0, row, size, tick, rebates_on,
                 queue_model, policy=None, prints=None,
                 entry_flow_per_s=None, entry_vol_per_sqrt_s=None):
        self.slug, self.event = slug, event
        self.prints = prints
        # RECORDED WHETHER OR NOT THE GATE IS ARMED, so the gate's
        # effect can be measured against episodes it did not filter.
        self.entry_flow_per_s = entry_flow_per_s
        self.entry_vol_per_sqrt_s = entry_vol_per_sqrt_s
        self.i0, self.t0 = i0, row["t_iso"]
        self.t0_epoch = row.get("t")
        # THE CLIP IS DECIDED HERE, from this book, at this instant.
        self.size, self.size_why = decision_time_size(policy, size, row)
        self.base_size = float(size)
        self.tick = tick
        self.rebates_on = rebates_on
        self.queue_model = queue_model
        self.policy = policy or BASE_POLICY
        # THE SHARED POLICY OBJECT, built once. Every decision below
        # goes through `sportsassets.bettor_policy`; nothing in this
        # file decides anything the runtime would decide differently,
        # because neither of them holds its own copy of the rule.
        self._sp = _as_shared(self.policy)

        book = ev.Book(slug=slug, bid=row["bid"], ask=row["ask"], tick=tick)
        qb = ev.incremental_ev("QUOTE_BID", book, ev.Inventory(),
                               contracts=self.size, as_fill=0.0, p_fill=1.0,
                               rebate_eligible=rebates_on)
        qo = ev.incremental_ev("QUOTE_OFFER", book, ev.Inventory(),
                               contracts=self.size, as_fill=0.0, p_fill=1.0,
                               rebate_eligible=rebates_on)
        # PLACEMENT IS A POLICY DECISION, not the engine's. The engine
        # prices whatever we quote; where to quote is ours to choose,
        # and the base rule ("improve by one tick where there is room")
        # is the one that lands a 2-tick book EXACTLY ON THE MID and
        # captures nothing. The alternatives exist so that defect can
        # be measured instead of argued about.
        pl = self.policy.placement
        if pl == "AT_TOUCH":
            self.pb, self.po = row["bid"], row["ask"]
        elif pl == "DEEPER_1":
            self.pb = round(row["bid"] - tick, 6)
            self.po = round(row["ask"] + tick, 6)
        else:
            self.pb = qb.terms["quote_price"]      # our YES bid
            self.po = qo.terms["quote_price"]      # our YES offer
        self.entry_book = {"bid": row["bid"], "ask": row["ask"],
                           "spread": round(row["ask"] - row["bid"], 6),
                           "spread_ticks": int(round(
                               (row["ask"] - row["bid"]) / tick)),
                           "mid": round(0.5 * (row["bid"] + row["ask"]), 6),
                           "bid_at_touch": self.pb == row["bid"],
                           "offer_at_touch": self.po == row["ask"],
                           # THE SIZES, WHICH WERE ALWAYS THERE. This
                           # summary carried prices only, and a report
                           # that read it concluded "the reward is not
                           # computable from this corpus -- no sizes at
                           # any level". That was a fact about THIS
                           # DICT, not about the capture: all 30,590
                           # tape rows carry touch depth AND a full
                           # ladder (median 5 bid levels, median join
                           # age 2.5s). The ladder is what the
                           # incentive score walks, so it travels with
                           # the episode now.
                           "bid_depth": row.get("bid_depth"),
                           "ask_depth": row.get("ask_depth"),
                           "ladder": _ladder_of(row)}

        # position, in LONG legs -- one YES + one NO pays exactly 1
        self.yes = 0.0
        self.no = 0.0
        self.cash = 0.0            # realised cash, fees included
        self.fills = []
        self.rebates_paid = 0.0
        self.taker_fees_paid = 0.0

        self.open_yes = self.size   # unfilled size on the YES bid
        self.open_no = self.size    # unfilled size on the NO bid
        self.cancel_requested_at = None
        self.cancel_effective_from = None
        self.race_fills = 0

        # ── PERSISTENT PER-ORDER QUEUE ────────────────────────────
        #
        # Set ONCE, from the causal ladder at entry, and thereafter only
        # decremented. Recomputing it from each snapshot -- which the
        # previous version did -- silently re-queued us behind every new
        # order that arrived at our price, which is the opposite of
        # price-time priority.
        #
        # Only the SAME-PRICE component persists. Quantity at a STRICTLY
        # BETTER price is price priority, not queue position, so it is
        # re-read every interval: a new order that betters our price
        # really does execute ahead of us.
        self.q_ahead_same_price = {"YES": None, "NO": None}
        self.q_ahead_at_entry = {"YES": None, "NO": None}
        self.queue_advance = {"YES": 0.0, "NO": 0.0}
        self.ladder_bounded_intervals = 0
        self.unbounded_intervals = 0
        self.no_ladder_intervals = 0
        # HOW EACH INTERVAL WAS PRICED, counted so a result can
        # never silently mix the venue's tape with the snapshot
        # proxy it replaces.
        self.tape_intervals = 0
        self.snapshot_intervals = 0
        self.unliquidated = 0.0
        lad0 = row.get("ladder")
        for leg, side, our in (("YES", "BID", self.pb),
                               ("NO", "OFFER", self.po)):
            if lad0:
                at_ours = sum(q for px, q in
                              (lad0["bids"] if side == "BID"
                               else lad0["offers"])
                              if abs(px - our) < 1e-9)
                v = self.policy.queue_ahead_fraction * at_ours
            else:
                v = None
            self.q_ahead_same_price[leg] = v
            self.q_ahead_at_entry[leg] = v

        self.status = None
        self.end_i = None
        self.end_t = None
        self.residual_value = 0.0
        self.residual_basis = None
        self.settlement = None
        self.notes = []

    # ── the shared policy's view of this episode ──────────────────────
    def _inv_of(self, elapsed_s, unmatched_since=None):
        """This episode's state in the SHARED policy's terms.

        The runtime builds the same object from its own position
        keeping. Both then call the same functions, which is the point:
        an adapter per caller, one rule for everybody.
        """
        return shared.Inventory(
            yes=self.yes, no=self.no,
            open_yes=self.open_yes, open_no=self.open_no,
            clip=self.size, elapsed_s=float(elapsed_s or 0.0),
            unmatched_since_s=unmatched_since)

    # ── collateral ────────────────────────────────────────────────────
    def collateral_now(self):
        """Cash the venue is holding against us RIGHT NOW.

        Two readings, both reported, because which one PMUS uses for
        RESTING orders is UNDOCUMENTED and was never read back from the
        venue:

          filled_only  only the legs that actually filled
          with_resting filled legs PLUS the unfilled resting size
        """
        filled = self.yes_cost + self.no_cost
        resting = self.pb * self.open_yes + (1.0 - self.po) * self.open_no
        return filled, filled + resting

    @property
    def yes_cost(self):
        return sum(f["cash_out"] for f in self.fills if f["leg"] == "YES")

    @property
    def no_cost(self):
        return sum(f["cash_out"] for f in self.fills if f["leg"] == "NO")

    # ── fills ─────────────────────────────────────────────────────────
    def _fill(self, leg, px, n, i, row, maker, tag):
        """Record ONE fill. The fee is computed on THIS fill's size."""
        if n <= 0:
            return
        te = row.get("t")
        f = (_maker_fee(px, n, self.rebates_on, te) if maker
             else _taker_fee(px, n, te))
        if maker:
            self.rebates_paid += f
        else:
            self.taker_fees_paid += f
        self.cash += -px * n + f
        if leg == "YES":
            self.yes += n
        else:
            self.no += n
        self.fills.append({
            "leg": leg, "px": px, "n": n, "maker": maker, "tag": tag,
            "i": i, "t": row["t_iso"], "fee_cash": round(f, 6),
            "fee_per_contract": round(f / n, 8),
            "cash_out": px * n,
            "raw_fee_before_rounding": round(
                (ev.THETA_MAKER if maker else ev.THETA_TAKER)
                * n * px * (1.0 - px), 8)})

    def _sell(self, leg, px, n, i, row, maker, tag, levels=None):
        """Sell an owned leg (reduces the position, brings cash in).

        `levels` is the (price, qty) list this ONE aggressive order
        actually swept. The published rule caps an order's total taker
        commission at the banker's rounding of its CUMULATIVE exact
        fee, so the cap must be applied per ORDER, across that order's
        own fills -- never across unrelated orders. When no ladder walk
        is available the order is treated as a single fill, which is
        what it is.
        """
        if n <= 0:
            return
        te = row.get("t")
        if maker:
            f = _maker_fee(px, n, self.rebates_on, te)
        elif levels:
            f = ev.taker_fee_for_order(levels, at_epoch=te)
        else:
            f = _taker_fee(px, n, te)
        if maker:
            self.rebates_paid += f
        else:
            self.taker_fees_paid += f
        self.cash += px * n + f
        if leg == "YES":
            self.yes -= n
        else:
            self.no -= n
        self.fills.append({
            "leg": leg, "px": px, "n": -n, "maker": maker, "tag": tag,
            "i": i, "t": row["t_iso"], "fee_cash": round(f, 6),
            "fee_per_contract": round(f / n, 8), "cash_out": -px * n,
            "raw_fee_before_rounding": round(
                (ev.THETA_MAKER if maker else ev.THETA_TAKER)
                * n * px * (1.0 - px), 8)})

    def _taker_exit(self, leg, n, i, row, tag):
        """Cross out through the ACTUAL ladder, as one aggressive order.

        Two things this does that a touch-priced exit cannot:

        * IT CAN FAIL. A 100-contract exit into a book holding 40
          contracts fills 40 and leaves 60 UNLIQUIDATED, which then
          rides to settlement. "Hard-flatten" is a request, not a
          guarantee, and this is where that shows up.
        * The levels it sweeps are ONE order, so the published
          cumulative-rounding cap applies across them and nothing else.
        """
        lad = row.get("ladder")
        book_side = "BID" if leg == "YES" else "OFFER"
        if lad is None:
            self.unliquidated += n
            self.notes.append("no ladder at exit; %g %s UNLIQUIDATED"
                              % (n, leg))
            return
        levels = lad["bids"] if book_side == "BID" else lad["offers"]
        need, swept, cash = float(n), [], 0.0
        for px, q in levels:
            if need <= 1e-9:
                break
            take = min(need, q)
            # our YES sale happens at the bid price; our NO sale is the
            # mirror, at 1 - the ask we hit
            eff = px if leg == "YES" else 1.0 - px
            swept.append((eff, take))
            cash += eff * take
            need -= take
        filled = float(n) - need
        if filled <= 1e-9:
            self.unliquidated += n
            self.notes.append("empty book at exit; %g %s UNLIQUIDATED"
                              % (n, leg))
            return
        if need > 1e-9:
            self.unliquidated += need
            self.notes.append("ladder exhausted; %g %s UNLIQUIDATED"
                              % (need, leg))
        f = ev.taker_fee_for_order(swept, at_epoch=row.get("t"))
        self.taker_fees_paid += f
        self.cash += cash + f
        if leg == "YES":
            self.yes -= filled
        else:
            self.no -= filled
        self.fills.append({
            "leg": leg, "px": round(cash / filled, 6), "n": -filled,
            "maker": False, "tag": tag, "i": i, "t": row["t_iso"],
            "fee_cash": round(f, 6),
            "fee_per_contract": round(f / filled, 8),
            "cash_out": -cash, "levels_swept": len(swept),
            "raw_fee_before_rounding": round(
                sum(ev.THETA_TAKER * q * p * (1.0 - p) for p, q in swept),
                8)})

    # ── the fill model: PERSISTENT QUEUE, LABELLED SCENARIOS ──────────
    def _available(self, side, prev, row, dvol, print_px, tprints=None):
        """Contracts of this interval's PRINTED volume that reach us.

        THE TWO DEFECTS THIS REPLACES, both real:

        1. The queue was recomputed from every snapshot, so any new
           order arriving at our price re-queued us behind it. Under
           price-time priority it should have queued BEHIND us. The
           same-price queue now persists from entry and only shrinks.

        2. Ladder depletion was used as a bound on fills. It is not
           one: a level shrinks on cancellation and repricing too, and
           it can stay flat while executions happen underneath
           replenishment. Depletion now feeds only the QUEUE ADVANCE,
           under a labelled scenario, and never fills us by itself.

        ONLY TRADED VOLUME FILLS. A cancellation ahead of us moves us
        up the queue; it cannot buy our contracts.
        """
        our = self.pb if side == "YES" else self.po
        book_side = "BID" if side == "YES" else "OFFER"
        lad_a, lad_b = prev.get("ladder"), row.get("ladder")
        if lad_a is None:
            self.no_ladder_intervals += 1
            return 0.0
        if self.q_ahead_same_price[side] is None:
            self.no_ladder_intervals += 1
            return 0.0

        levels_a = lad_a["bids"] if book_side == "BID" else lad_a["offers"]
        if book_side == "BID":
            strictly_better = sum(q for px, q in levels_a
                                  if px > our + 1e-9)
        else:
            strictly_better = sum(q for px, q in levels_a
                                  if px < our - 1e-9)

        # WHAT REACHED OUR PRICE.
        #
        # With the venue's tape we test EVERY print in the interval
        # against our own quote and sum only the ones that reach it. A
        # single interval carries up to 120 distinct prices, so the old
        # test -- one snapshot delta priced at `last_trade_px`, the most
        # recent print -- decided the whole interval on whichever side
        # of our quote one arbitrary print happened to land.
        #
        # The tape has no side and no aggressor flag, so a print is used
        # only to establish that trading REACHED a price. It is never
        # read as evidence about who initiated it.
        # `tprints is None` means NO TAPE. An EMPTY TUPLE means the tape
        # covers this interval and says NOTHING TRADED -- which is the
        # common case, and the opposite of "no information". Testing
        # truthiness conflated the two and sent every quiet interval
        # back to the snapshot proxy, where a sharesTraded delta could
        # still fill us. That is precisely the defect the tape was
        # retrieved to remove.
        if tprints is not None:
            if side == "YES":
                traded = sum(q for _, px, q in tprints if px <= our + 1e-9)
            else:
                traded = sum(q for _, px, q in tprints if px >= our - 1e-9)
            self.tape_intervals += 1
        else:
            crosses = False
            if print_px is not None and dvol > 0:
                crosses = (print_px <= our + 1e-9) if side == "YES" \
                    else (print_px >= our - 1e-9)
            traded = dvol if crosses else 0.0
            self.snapshot_intervals += 1

        # depletion at our price or better, between the two CAUSAL
        # ladders. Used for queue advance only.
        before = tape.qty_at_or_better(lad_a, book_side, our) or 0.0
        after = (tape.qty_at_or_better(lad_b, book_side, our)
                 if lad_b else before)
        depletion = max(0.0, before - (after or 0.0))

        sc = self.policy.execution_scenario
        if sc == "TRADE_ONLY":
            advance = traded
        elif sc == "TRADE_OR_DEPLETION":
            advance = max(traded, depletion)
        elif sc == "TRADE_PLUS_HALF":
            advance = traded + 0.5 * max(0.0, depletion - traded)
        else:
            raise ValueError("unknown execution_scenario %r" % (sc,))

        # WHAT REACHES US. Price priority is re-read; queue position
        # persists. Only `traded` can fill.
        ahead_now = strictly_better + self.q_ahead_same_price[side]
        filled = max(0.0, traded - ahead_now)

        # the queue then advances, by the scenario's measure
        self.q_ahead_same_price[side] = max(
            0.0, self.q_ahead_same_price[side] - advance)
        self.queue_advance[side] += advance
        self.ladder_bounded_intervals += 1
        return filled

    # ── the run ───────────────────────────────────────────────────────
    def run(self, rows, settlement):
        n = len(rows)
        i = self.i0
        recovery_started = None
        recovery_started_t = None
        exit_quote = None            # (leg, price) of a resting maker exit

        while True:
            i += 1
            if i >= n:
                self._finish_open(rows[-1], n - 1)
                return self
            prev, row = rows[i - 1], rows[i]

            # ── expiry / settlement ───────────────────────────────────
            if row["state"] == tape.EXPIRED:
                # HARD FLATTEN crosses out on the LAST OPEN observation
                # rather than riding through expiry. It pays a taker fee
                # every time in exchange for removing the +-0.40 tail
                # that dominates the variance.
                if self.policy.hard_flatten and prev["state"] == tape.OPEN \
                        and prev["bid"] is not None \
                        and prev["ask"] is not None:
                    matched = min(self.yes, self.no)
                    ey, en = self.yes - matched, self.no - matched
                    if ey > 0:
                        self._taker_exit("YES", ey, i - 1, prev,
                                         "FLATTEN_PRE_EXPIRY")
                    if en > 0:
                        self._taker_exit("NO", en, i - 1, prev,
                                         "FLATTEN_PRE_EXPIRY")
                    if matched > 0:
                        self._finish_paired(prev, i - 1, settlement,
                                            FLAT_EXITED_TAKER)
                    else:
                        self._finish_flat(prev, i - 1, FLAT_EXITED_TAKER)
                    self.notes.append("hard-flattened on the last open "
                                      "observation before expiry")
                    return self
                self._finish_expired(row, i, settlement)
                return self

            dvol = 0.0
            if (row["shares_traded"] is not None
                    and prev["shares_traded"] is not None):
                dvol = max(0.0, row["shares_traded"] - prev["shares_traded"])
            print_px = row["last_trade_px"]
            # THE REAL PRINTS IN (t_prev, t_now]. Empty tuple means the
            # tape says NOTHING traded in this interval -- which is the
            # common case: most intervals contain no print at all.
            tprints = (self.prints.between(prev.get("t"), row.get("t"))
                       if self.prints is not None else None)

            age = (row["t"] - self.t0_epoch) if (
                row["t"] is not None and self.t0_epoch is not None) else 0.0
            cancelled = (self.cancel_effective_from is not None
                         and i >= self.cancel_effective_from)

            # ── entry fills (the two resting legs) ────────────────────
            if not cancelled and (self.open_yes > 0 or self.open_no > 0):
                got = self._available("YES", prev, row, dvol, print_px,
                                      tprints)
                take = min(self.open_yes, got)
                if take > 0:
                    self._fill("YES", self.pb, take, i, row, True, "ENTRY")
                    self.open_yes -= take
                    if self.cancel_requested_at is not None:
                        self.race_fills += 1
                got = self._available("NO", prev, row, dvol, print_px,
                                      tprints)
                take = min(self.open_no, got)
                if take > 0:
                    self._fill("NO", 1.0 - self.po, take, i, row, True,
                               "ENTRY")
                    self.open_no -= take
                    if self.cancel_requested_at is not None:
                        self.race_fills += 1
                # INVENTORY RULE. A one-sided fill is the state that
                # costs money, so one alternative is to stop trying for
                # the pair the moment a leg prints and go straight to
                # recovery. The cancel still takes effect only from the
                # NEXT observation -- the race is charged against us
                # here exactly as it is at the horizon.
                if (self.cancel_requested_at is None
                        and (self.yes > 0) != (self.no > 0)):
                    d = shared.on_fill(self._sp, self._inv_of(age))
                    if d["decision"] == shared.D_CANCEL_OTHER:
                        self.cancel_requested_at = i
                        self.cancel_effective_from = i + 1
                        self.notes.append("cancelled the resting leg on "
                                          "the first fill -- %s"
                                          % d["why"])
                        continue

            # ── both legs done: a matched pair, settles at 1 ──────────
            if (self.open_yes <= 1e-9 and self.open_no <= 1e-9
                    and recovery_started is None
                    and min(self.yes, self.no) >= self.size - 1e-9):
                self._finish_paired(row, i, settlement)
                return self

            # ── the quoting horizon: request the cancel ───────────────
            expired = shared.horizon_expired(self._sp, self._inv_of(age))
            if (expired and self.cancel_requested_at is None
                    and (self.open_yes > 0 or self.open_no > 0)):
                self.cancel_requested_at = i
                # RACE: effective only from the NEXT observation, so a
                # print in this interval still fills us.
                self.cancel_effective_from = i + 1
                continue
            if expired and self.cancel_requested_at is None:
                self.cancel_requested_at = i
                self.cancel_effective_from = i

            if not cancelled:
                continue

            # ── RECOVERY ──────────────────────────────────────────────
            matched = min(self.yes, self.no)
            excess_yes = self.yes - matched
            excess_no = self.no - matched
            if excess_yes <= 1e-9 and excess_no <= 1e-9:
                if matched <= 0:
                    self._finish_never_filled(row, i)
                    return self
                d = shared.release_action(self._sp, self._inv_of(age),
                                          _book_of(row, self.tick))
                if d["decision"] == shared.D_RELEASE:
                    # SELL THE PAIR BACK rather than hold it. Both legs
                    # cross the book as takers, so the cost is the
                    # spread plus two taker fees and the capital is
                    # free immediately instead of at settlement.
                    self._release_pair(matched, i, row)
                else:
                    self._finish_paired(row, i, settlement)
                return self

            if row["bid"] is None or row["ask"] is None:
                continue

            # ── THE RECOVERY PATH IS A POLICY CHOICE ──────────────────
            #
            # Four of them, because the case studies do not all do the
            # same thing with unmatched inventory and rejecting one is
            # not rejecting the others.
            #
            # ONE CALL DECIDES ALL FOUR. `recovery_action` is the same
            # function the runtime calls; what differs between them is
            # only how the Inventory was assembled.
            _since = (recovery_started_t - self.t0_epoch
                      if (recovery_started_t is not None
                          and self.t0_epoch is not None) else None)
            rd = shared.recovery_action(
                self._sp, self._inv_of(age, unmatched_since=_since),
                _book_of(row, self.tick))
            dec = rd["decision"]
            if dec == shared.D_EXIT_TAKER and recovery_started is None:
                if excess_yes > 0:
                    self._taker_exit("YES", excess_yes, i, row,
                                     "EXIT_TAKER")
                if excess_no > 0:
                    self._taker_exit("NO", excess_no, i, row,
                                     "EXIT_TAKER")
                if matched > 0:
                    self._finish_paired(row, i, settlement,
                                        FLAT_EXITED_TAKER)
                else:
                    self._finish_flat(row, i, FLAT_EXITED_TAKER)
                return self
            if dec == shared.D_COMPLETE_PAIR:
                # MECHANISM 3 IN ITS TIME-DEPENDENT FORM. Buy the
                # COMPLEMENT as a taker so the position becomes a
                # matched pair that settles at 1, instead of selling
                # the leg we hold. The engine prices it; the cash is
                # the complement's price plus a taker fee.
                if excess_yes > 0:
                    self._fill("NO", 1.0 - row["bid"], excess_yes, i, row,
                               False, "COMPLETE_PAIR")
                if excess_no > 0:
                    self._fill("YES", row["ask"], excess_no, i, row,
                               False, "COMPLETE_PAIR")
                self._finish_paired(row, i, settlement, FLAT_PAIRED)
                self.notes.append("completed the pair as a taker rather "
                                  "than exiting the leg")
                return self
            if dec == shared.D_HOLD:
                # Ride it to settlement. The episode ends when the
                # market expires or the capture does -- the loop's own
                # expiry and horizon handlers take it from here.
                continue

            if recovery_started is None:
                recovery_started = i
                recovery_started_t = row["t"]
                book = ev.Book(slug=self.slug, bid=row["bid"],
                               ask=row["ask"], tick=self.tick)
                if excess_yes > 0:
                    q = ev.incremental_ev(
                        "QUOTE_OFFER", book, ev.Inventory(), contracts=1.0,
                        as_fill=0.0, p_fill=1.0,
                        rebate_eligible=self.rebates_on)
                    exit_quote = ("YES", q.terms["quote_price"])
                else:
                    q = ev.incremental_ev(
                        "QUOTE_BID", book, ev.Inventory(), contracts=1.0,
                        as_fill=0.0, p_fill=1.0,
                        rebate_eligible=self.rebates_on)
                    # selling NO at (1 - engine bid) means someone buys
                    # YES at the engine's bid quote
                    exit_quote = ("NO", 1.0 - q.terms["quote_price"])
                continue

            # maker exit resting
            leg, xpx = exit_quote
            want = excess_yes if leg == "YES" else excess_no
            if leg == "YES":
                hit = (print_px is not None and print_px >= xpx - 1e-9)
                inside = row["ask"] is not None and xpx < row["ask"] - 1e-9
                depth = row["ask_depth"] or 0
            else:
                yes_px = 1.0 - xpx
                hit = (print_px is not None and print_px <= yes_px + 1e-9)
                inside = row["bid"] is not None and yes_px > row["bid"] + 1e-9
                depth = row["bid_depth"] or 0
            if hit and dvol > 0:
                ahead = 0.0 if (self.queue_model == "QUEUE_FRONT_IF_INSIDE"
                                and inside) else float(depth)
                take = min(want, max(0.0, dvol - ahead))
                if take > 0:
                    self._sell(leg, xpx, take, i, row, True, "EXIT_MAKER")
                    matched = min(self.yes, self.no)
                    if (self.yes - matched) <= 1e-9 and \
                            (self.no - matched) <= 1e-9:
                        if matched > 0:
                            self._finish_paired(row, i, settlement,
                                                FLAT_EXITED_MAKER)
                        else:
                            self._finish_flat(row, i, FLAT_EXITED_MAKER)
                        return self
                    continue

            # THE WAIT EXPIRED -- decided by the same shared call above,
            # which read `unmatched_since_s` and compared it to
            # `recovery_wait_s`. No second copy of that comparison.
            if dec == shared.D_EXIT_TAKER:
                # cross out as a taker
                matched = min(self.yes, self.no)
                ey, en = self.yes - matched, self.no - matched
                if ey > 0:
                    self._taker_exit("YES", ey, i, row, "EXIT_TAKER")
                if en > 0:
                    self._taker_exit("NO", en, i, row, "EXIT_TAKER")
                if matched > 0:
                    self._finish_paired(row, i, settlement,
                                        FLAT_EXITED_TAKER)
                else:
                    self._finish_flat(row, i, FLAT_EXITED_TAKER)
                return self

    # ── endings ───────────────────────────────────────────────────────
    def _stamp(self, row, i, status):
        self.status, self.end_i, self.end_t = status, i, row["t_iso"]

    def _release_pair(self, matched, i, row):
        """Unwind a matched pair into cash NOW, at the cost of the spread.

        THE ALTERNATIVE TO A CAPABILITY WE DO NOT HAVE. A merge call
        would return the pair's full 1.00 with no market cost. Nothing
        like it has been demonstrated at this venue, so this is the
        feasible substitute: sell the YES at the bid and the NO at
        (1 - ask), both as takers, through the real ladder.

        UNEQUAL LIQUIDATION IS THE DEFECT THIS NOW HANDLES.
        `_taker_exit` sweeps the ACTUAL ladder and can fill only part
        of what it is asked for. The first version sold YES then NO and
        accepted whatever came back -- so a deep YES side and a thin NO
        side turned a RISKLESS MATCHED PAIR into a NAKED DIRECTIONAL
        POSITION, created by the very operation whose purpose was to
        remove risk. Worse, it did it silently: `min(yes, no)` still
        reported a tidy pair count and the naked excess simply rode to
        settlement.

        The repair is to size the second leg to what the FIRST one
        actually achieved, and then to re-pair anything left over:

          1. sell the smaller side first is not enough -- we cannot
             know which side is thinner until we try. So sell YES,
             measure, then ask the NO side for EXACTLY what the YES
             side gave us.
          2. if the NO side then gives less, we are long YES-short by
             the difference; that residue is put BACK into a matched
             pair by buying the complement, which is the same
             mechanism `COMPLETE_PAIR` uses and is known to work.
          3. whatever remains is reported as a pair that could not be
             released, never as flat.

        The invariant this maintains: THE RELEASE NEVER INCREASES
        DIRECTIONAL EXPOSURE. `test_release_never_creates_naked_risk`
        asserts it over a sweep of asymmetric ladders.
        """
        before_yes, before_no = self.yes, self.no
        net_before = self.yes - self.no

        # 1. Sell the YES leg and MEASURE what actually came back.
        self._taker_exit("YES", matched, i, row, "RELEASE_PAIR")
        sold_yes = before_yes - self.yes

        # 2. Ask the NO side for exactly that much -- never more. Asking
        #    for `matched` when the YES side only managed half is what
        #    created the naked leg.
        if sold_yes > 1e-9:
            before_no2 = self.no
            self._taker_exit("NO", sold_yes, i, row, "RELEASE_PAIR")
            sold_no = before_no2 - self.no
        else:
            sold_no = 0.0

        # 3. Re-pair the residue. If the two legs came back unequal we
        #    are directionally exposed by the difference; buying the
        #    complement puts it back into a pair rather than leaving it
        #    naked.
        gap = round(sold_yes - sold_no, 9)
        repaired = 0.0
        if abs(gap) > 1e-9 and row.get("bid") is not None \
                and row.get("ask") is not None:
            if gap > 0:
                # sold more YES than NO -> short YES relative to NO ->
                # we hold excess NO. Buy YES back to re-pair it.
                self._fill("YES", row["ask"], gap, i, row, False,
                           "RELEASE_REPAIR")
            else:
                self._fill("NO", 1.0 - row["bid"], -gap, i, row, False,
                           "RELEASE_REPAIR")
            repaired = abs(gap)

        net_after = self.yes - self.no
        left = min(self.yes, self.no)
        self.notes.append(
            "RELEASE: sold %.4f YES and %.4f NO as takers; re-paired "
            "%.4f of unequal liquidation; %.4f pair(s) could not be "
            "released and ride to settlement. Net directional %.4f -> "
            "%.4f." % (sold_yes, sold_no, repaired, left,
                       net_before, net_after))
        if abs(net_after) > abs(net_before) + 1e-6:
            # THE INVARIANT, CHECKED AT RUNTIME AND NOT ONLY IN A TEST.
            self.notes.append(
                "RELEASE_INCREASED_DIRECTIONAL_EXPOSURE -- this is a "
                "defect, recorded rather than smoothed over")
        if left > 1e-9:
            self._finish_paired(row, i, None, FLAT_PAIRED)
        else:
            self._finish_flat(row, i, FLAT_EXITED_TAKER)

    def _finish_paired(self, row, i, settlement, status=FLAT_PAIRED):
        """A matched pair remains. It pays EXACTLY 1 per pair."""
        self._stamp(row, i, status)
        matched = min(self.yes, self.no)
        self.residual_value = matched * 1.0
        self.residual_basis = ("MATCHED_PAIR_PAYS_1 -- certain, but not "
                               "cash until settlement")
        self.settlement = settlement

    def _finish_flat(self, row, i, status):
        self._stamp(row, i, status)
        self.residual_value = 0.0
        self.residual_basis = "FLAT"

    def _finish_never_filled(self, row, i):
        self._stamp(row, i, NEVER_FILLED)
        self.residual_value = 0.0
        self.residual_basis = "FLAT -- neither leg filled"

    def _finish_expired(self, row, i, settlement):
        if not self.fills:
            # NOTHING EVER FILLED. The market merely expired underneath
            # a quote that never traded. Labelling it CARRIED_SETTLED
            # put two episodes in the carried population that had no
            # position to carry, and broke the reconciliation
            # never_filled + any_fill == all_episodes.
            self._finish_never_filled(row, i)
            return
        self._stamp(row, i, CARRIED_SETTLED)
        self.settlement = settlement
        if settlement == tape.SETTLEMENT_NOT_OBSERVED or settlement is None:
            self._stamp(row, i, CARRIED_OPEN)
            self.residual_value = 0.0
            self.residual_basis = ("EXPIRED BUT OUTCOME NOT PUBLISHED IN "
                                   "THE CAPTURE -- residual UNVALUED")
            self.notes.append("settlement unobserved; residual carried at 0 "
                              "and flagged, NOT marked")
            return
        s = float(settlement)
        self.residual_value = self.yes * s + self.no * (1.0 - s)
        self.residual_basis = ("OBSERVED SETTLEMENT %.4f -- yes*%0.4f + "
                               "no*%0.4f" % (s, s, 1.0 - s))

    def _finish_open(self, row, i):
        if not self.fills:
            self._finish_never_filled(row, i)
            return
        self._stamp(row, i, CARRIED_OPEN)
        matched = min(self.yes, self.no)
        ey, en = self.yes - matched, self.no - matched
        v = matched * 1.0
        basis = ["matched pair %g -> 1.0 each" % matched] if matched else []
        if row["bid"] is not None and ey > 0:
            v += row["bid"] * ey + _taker_fee(row["bid"], ey, row.get("t"))
            basis.append("excess YES %g marked at the BID %.4f net of the "
                         "taker fee" % (ey, row["bid"]))
        elif ey > 0:
            basis.append("excess YES %g UNMARKED -- no bid" % ey)
        if row["ask"] is not None and en > 0:
            px = 1.0 - row["ask"]
            v += px * en + _taker_fee(px, en, row.get("t"))
            basis.append("excess NO %g marked at 1-ASK %.4f net of the taker "
                         "fee" % (en, px))
        elif en > 0:
            basis.append("excess NO %g UNMARKED -- no ask" % en)
        self.residual_value = v
        self.residual_basis = ("MARKED AT THE TOUCH WE COULD HIT: "
                               + "; ".join(basis) if basis else "FLAT")

    # ── the result ────────────────────────────────────────────────────
    def result(self):
        matched = min(self.yes, self.no)
        filled_col, with_resting = self.collateral_now()
        settled = self.status == CARRIED_SETTLED
        return {
            "slug": self.slug, "event": self.event,
            "t0": self.t0, "t_end": self.end_t,
            "observations": (self.end_i - self.i0) if self.end_i else 0,
            "status": self.status,
            "queue_model": self.queue_model,
            "rebates": "PUBLISHED" if self.rebates_on else "EXCLUDED",

            # HOW EACH INTERVAL WAS PRICED. Carried on every episode so
            # a tape-backed result and a snapshot-proxy result can never
            # be added together without the mixture being visible.
            "tape_intervals": self.tape_intervals,
            "snapshot_intervals": self.snapshot_intervals,
            "size": self.size,
            "base_size": self.base_size,
            "size_rule": self.size_why,
            "entry_flow_per_s": self.entry_flow_per_s,
            "entry_vol_per_sqrt_s": self.entry_vol_per_sqrt_s,
            "quote_bid": self.pb, "quote_offer": self.po,
            "entry_book": self.entry_book,

            # the three quantities kept APART, as the directive requires
            "realised_cash": round(self.cash, 6),
            "residual_contracts": {"yes": round(self.yes, 4),
                                   "no": round(self.no, 4),
                                   "matched_pairs": round(matched, 4),
                                   "net_directional": round(
                                       self.yes - self.no, 4)},
            "residual_value": round(self.residual_value, 6),
            "residual_basis": self.residual_basis,
            "residual_is_cash": self.status in (FLAT_EXITED_MAKER,
                                                FLAT_EXITED_TAKER,
                                                NEVER_FILLED),
            "residual_is_settled": settled,
            "total_if_residual_realises": round(
                self.cash + self.residual_value, 6),

            "fills": len(self.fills),
            "entry_fills": sum(1 for f in self.fills if f["tag"] == "ENTRY"),
            # WHICH LEGS ACTUALLY FILLED AS MAKER ENTRIES. The end state
            # is not a safe proxy for this: the COMPLETE_PAIR recovery
            # turns a one-sided fill into FLAT_PAIRED by BUYING the
            # complement as a taker, which would read as a 98.8%
            # double-fill rate if the label were trusted.
            "entry_legs_filled": sorted({
                f["leg"] for f in self.fills
                if f["tag"] == "ENTRY" and f["n"] > 0}),
            "fill_sizes": [f["n"] for f in self.fills],
            "rebates_received": round(self.rebates_paid, 6),
            "taker_fees_paid": round(self.taker_fees_paid, 6),
            "cancel_requested_at_obs": (
                None if self.cancel_requested_at is None
                else self.cancel_requested_at - self.i0),
            "fills_after_cancel_request": self.race_fills,
            "collateral_filled_only": round(filled_col, 4),
            "collateral_incl_resting": round(with_resting, 4),
            "settlement": self.settlement,
            "notes": self.notes,
        }


def run_market(slug, rows, size, rebates_on, queue_model,
               max_episodes=None, policy=None, prints=None):
    """Non-overlapping episodes across one market's whole tape."""
    pol = policy or BASE_POLICY
    sp = _as_shared(pol)
    settlement, _ = tape.settlement_label(rows)
    tick = tape.market_tick(rows)
    event = tape.event_of(slug)
    out, i, n = [], 0, len(rows)
    while i < n:
        r = rows[i]
        # THE ENTRY FILTER IS PART OF THE POLICY, so a variant that
        # only quotes wide books or only quotes a price band is a
        # different policy and is recorded as one.
        #
        # THIS USED TO BE AN INLINE COPY of the shared rule -- the exact
        # drift the shared module exists to prevent, and the copy had
        # already fallen behind (it had no market-state vocabulary and
        # no flow gate). `admit` decides now.
        flow = (trailing_flow(rows, i, sp.flow_window_s)
                if sp.min_flow_cover > 0 else None)
        vol = (trailing_vol(rows, i, sp.vol_window_s)
               if sp.min_vol_cover > 0 else None)
        adm = shared.admit(sp, _book_of(r, tick, flow, vol), clip=size)
        if adm["decision"] != shared.D_QUOTE_BOTH:
            i += 1
            continue
        ep = Episode(slug, event, i, r, size, tick, rebates_on,
                     queue_model, policy=pol, prints=prints,
                     entry_flow_per_s=flow, entry_vol_per_sqrt_s=vol)
        ep.run(rows, settlement)
        out.append(ep.result())
        i = max(ep.end_i or i, i) + COOLDOWN + 1
        if max_episodes and len(out) >= max_episodes:
            break
    return out


_TAPE_CACHE = {}


def run_all(size=100.0, rebates_on=True,
            queue_model="QUEUE_FRONT_IF_INSIDE", max_episodes=None,
            policy=None, use_tape=True):
    if "t" not in _TAPE_CACHE:
        by, _f = tape.load_tape()
        by, stats = tape.attach_ladders(by)
        _TAPE_CACHE["t"] = by
        _TAPE_CACHE["ladder_stats"] = stats
    by_slug = _TAPE_CACHE["t"]
    if "prints" not in _TAPE_CACHE:
        _TAPE_CACHE["prints"] = prints_mod.load_prints()
    all_prints = _TAPE_CACHE["prints"] if use_tape else {}
    eps = []
    for slug, rows in sorted(by_slug.items()):
        idx = (prints_mod.index_for(all_prints, slug)
               if all_prints else None)
        eps.extend(run_market(slug, rows, size, rebates_on, queue_model,
                              max_episodes, policy=policy, prints=idx))
    return eps


if __name__ == "__main__":
    import collections
    import statistics as st

    print(__doc__.split("Run:")[0].strip()[:0] or "", end="")
    print("=" * 74)
    print("EPISODE ACCOUNTING -- every position carried, nothing dropped")
    print("=" * 74)
    for rebates_on in (True, False):
        for qm in QUEUE_MODELS:
            eps = run_all(size=100.0, rebates_on=rebates_on, queue_model=qm)
            tot = [e["total_if_residual_realises"] for e in eps]
            real = [e["realised_cash"] for e in eps]
            byst = collections.Counter(e["status"] for e in eps)
            carried = [e for e in eps
                       if e["residual_contracts"]["net_directional"] != 0]
            print()
            print("rebates=%-9s queue=%-22s episodes=%d" % (
                "PUBLISHED" if rebates_on else "EXCLUDED", qm, len(eps)))
            print("  statuses: %s" % dict(byst))
            print("  markets=%d  events=%d" % (
                len({e["slug"] for e in eps}), len({e["event"] for e in eps})))
            if tot:
                print("  TOTAL (realised + residual): sum %+0.2f  "
                      "median %+0.4f  mean %+0.4f  positive %d/%d" % (
                          sum(tot), st.median(tot), st.fmean(tot),
                          sum(1 for x in tot if x > 0), len(tot)))
                print("  realised cash only:          sum %+0.2f  "
                      "median %+0.4f" % (sum(real), st.median(real)))
                print("  episodes ending with UNMATCHED inventory: %d" %
                      len(carried))
