"""The end-to-end shadow loop. Observation -> decision -> execution ->
inventory -> settlement -> accounting, with every step recorded.

WHY THIS EXISTS. The components were written and not connected: fair
value had zero non-test callers, the replay harness had none at all, and
`bettor_rehearsal` imported eleven modules while being imported by none.
Nothing took an observation and carried it through to a reconciled cash
balance. This does.

THE ISOLATION RULE. `ShadowAdapter` is the only execution path here and
it CANNOT reach a venue: it has no client, no credentials and no
network. It is not the production adapter with a flag flipped -- it is a
different object, so there is no configuration mistake that turns this
loop into a live one. `submits_orders()` returns False and a test
asserts the module contains no submit path.

A PRICE TOUCH IS NOT A FILL. The simulator will not fill a resting quote
merely because the market printed through it. A maker fill requires
queue position to be consumed, and queue position is NOT_IDENTIFIED, so
the simulator's maker fills are DECLARED SYNTHETIC and are labelled as
such on every record they produce. They demonstrate that the software
handles a fill; they are not evidence that a fill would have happened.

WHAT IS SYNTHETIC AND WHAT IS NOT. Every record carries `evidence_class`:

    SYNTHETIC_SCENARIO   a hand-built book used to exercise a code path.
                         Demonstrates behaviour. Establishes nothing
                         about profitability, ever.
    REPLAYED_OBSERVATION a real captured book, replayed. Decisions are
                         real; fills are still simulated.
    PROSPECTIVE_SHADOW   a live observation decided on in real time with
                         no order sent. The only class that can support
                         a forward-looking claim.
    LIVE                 never produced by this module.

Mixing them in one performance number is the error this field exists to
prevent.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict

from . import bettor_decision_engine as de
from . import bettor_maker_economics as me
from . import bettor_venue_contract as vc

LOOP_VERSION = "BETTOR_SHADOW_LOOP_V1"

SYNTHETIC = "SYNTHETIC_SCENARIO"
REPLAYED = "REPLAYED_OBSERVATION"
PROSPECTIVE = "PROSPECTIVE_SHADOW"

# Fill outcomes the simulator can produce.
FILLED_FULL = "FILLED_FULL"
FILLED_PARTIAL = "FILLED_PARTIAL"
NO_FILL = "NO_FILL"
CANCELLED = "CANCELLED"
REPRICED = "REPRICED"
REJECTED = "REJECTED"

# A quote in either of these states is a LIVE ORDER. Its unfilled
# remainder is still working, still reserves cash, and is still
# eligible for a fill, a cancel or a replace. Treating PARTIAL as
# finished is how a half-filled order stops being tracked while it is
# still in the market.
LIVE_QUOTE_STATES = ("RESTING", "PARTIAL")


@dataclass
class Position:
    """Inventory, its REMAINING basis, and realized P&L kept apart.

    THE DEFECT THIS REPLACES, reproduced by review. The old version held
    one `cash_spent` field and subtracted sale proceeds from it. Buy 10
    YES at 0.47 and 10 NO at 0.50 (9.70 spent), sell at 0.55 and 0.48
    (10.30 proceeds), and cash_spent became -0.60: inventory zero,
    DEPLOYED CAPITAL NEGATIVE. Profit was being recorded as unspent
    capital, which then EXPANDED the risk limits -- a winning trade
    bought itself more room to trade.

    Four quantities, separated, because they answer four questions:

        yes / no            how many contracts we hold
        *_basis             what the REMAINING contracts cost. Removed
                            proportionally on a sale, so a fully closed
                            position has exactly zero basis.
        realized_pnl        profit, which is NOT capital available to
                            deploy and never reduces basis
        deployed            sum of remaining basis, floored at zero
    """
    market_id: str
    yes: float = 0.0
    no: float = 0.0
    yes_basis: float = 0.0           # cost of the REMAINING yes contracts
    no_basis: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    opened_at: float | None = None

    @property
    def paired(self) -> float:
        return min(self.yes, self.no)

    @property
    def flat(self) -> bool:
        return self.yes == 0.0 and self.no == 0.0

    @property
    def basis(self) -> float:
        """Remaining acquisition cost. Never negative."""
        return max(0.0, self.yes_basis + self.no_basis)

    @property
    def directional(self) -> float:
        """Unmatched contracts. A pair is flat; a stub is exposure."""
        return abs(self.yes - self.no)

    def buy(self, leg: str, qty: float, px: float, fee: float) -> None:
        setattr(self, leg, getattr(self, leg) + qty)
        b = "%s_basis" % leg
        setattr(self, b, getattr(self, b) + qty * px + fee)
        self.fees_paid += fee

    def sell(self, leg: str, qty: float, px: float, fee: float) -> float:
        """Remove basis PROPORTIONALLY and book the difference as P&L."""
        held = getattr(self, leg)
        if held <= 0 or qty <= 0:
            return 0.0
        qty = min(qty, held)
        b = "%s_basis" % leg
        basis_total = getattr(self, b)
        basis_out = basis_total * (qty / held)
        proceeds = qty * px - fee
        realized = proceeds - basis_out
        setattr(self, leg, held - qty)
        setattr(self, b, basis_total - basis_out)
        # Float residue must not leave a phantom basis behind a zero
        # position: a closed position has zero basis, exactly.
        if getattr(self, leg) <= 1e-12:
            setattr(self, leg, 0.0)
            setattr(self, b, 0.0)
        self.realized_pnl += realized
        self.fees_paid += fee
        return realized


@dataclass
class Ledger:
    """One reconciled cash model. Every movement lands here exactly once.

    `cash` starts at the opening balance and is the only running total.
    Positions are carried at ZERO valuation until they settle -- an
    unsettled position is not cash and is never counted as if it were,
    which is how a shadow book talks itself into a profit it has not
    made.
    """
    opening_cash: float
    cash: float = 0.0
    fees_paid: float = 0.0
    realized_pnl: float = 0.0
    settlements: float = 0.0
    movements: list = field(default_factory=list)

    def __post_init__(self):
        if self.cash == 0.0:
            self.cash = self.opening_cash

    def move(self, kind: str, amount: float, why: str, ref: str = "") -> None:
        self.cash += amount
        self.movements.append({"kind": kind, "amount": round(amount, 6),
                               "cash_after": round(self.cash, 6),
                               "why": why, "ref": ref})

    def reconciles(self) -> dict:
        """opening + every movement == cash. Residual printed, not assumed."""
        total = sum(m["amount"] for m in self.movements)
        residual = (self.opening_cash + total) - self.cash
        return {"opening_cash": round(self.opening_cash, 6),
                "movements": len(self.movements),
                "sum_of_movements": round(total, 6),
                "closing_cash": round(self.cash, 6),
                "residual_must_be_0": round(residual, 9),
                "reconciled": abs(residual) < 1e-9}


@dataclass
class RiskLimits:
    """Hard stops. Checked BEFORE execution, never after.

    A limit checked after the order is sent is not a limit, it is a
    report. Every one of these refuses the action.
    """
    max_position_contracts: float = 100.0
    max_cash_per_market: float = 500.0
    max_total_deployed: float = 2000.0
    max_open_markets: int = 10
    min_cash_reserve: float = 0.0

    def check(self, *, pos: Position, add_contracts: float, add_cash: float,
              deployed: float, open_markets: int, cash: float) -> str | None:
        if add_contracts < 0 or not math.isfinite(add_contracts):
            return "RISK_INVALID_SIZE"
        if pos.yes + pos.no + add_contracts > self.max_position_contracts:
            return "RISK_MAX_POSITION_CONTRACTS"
        if pos.basis + add_cash > self.max_cash_per_market:
            return "RISK_MAX_CASH_PER_MARKET"
        if deployed + add_cash > self.max_total_deployed:
            return "RISK_MAX_TOTAL_DEPLOYED"
        if pos.flat and open_markets >= self.max_open_markets:
            return "RISK_MAX_OPEN_MARKETS"
        if cash - add_cash < self.min_cash_reserve:
            return "RISK_MIN_CASH_RESERVE"
        return None

    def check_reduction(self, *, pos: Position, action: str, qty: float,
                        book) -> str | None:
        """Checks that still apply when the action REDUCES exposure.

        Entry limits are exempt -- an entry limit that blocks an exit
        freezes the position at exactly the moment the limit says it is
        too large. These are different checks, and skipping them was the
        overcorrection: a sell can still be invalid, oversized, or
        exposure-INCREASING in disguise.
        """
        if qty is None or not math.isfinite(qty) or qty <= 0:
            return "RISK_REDUCTION_INVALID_QUANTITY"
        if action in ("SELL_YES", "SELL_NO"):
            leg = "yes" if action == "SELL_YES" else "no"
            if qty > getattr(pos, leg) + 1e-9:
                return "RISK_REDUCTION_EXCEEDS_HOLDING"
            # SELLING ONE SIDE OF A MATCHED PAIR CREATES DIRECTIONAL
            # EXPOSURE. The position gets smaller and riskier at once.
            after = abs((pos.yes - qty if leg == "yes" else pos.yes)
                        - (pos.no - qty if leg == "no" else pos.no))
            if after > pos.directional + 1e-9:
                return "RISK_REDUCTION_CREATES_DIRECTIONAL_EXPOSURE"
        if action == "PAIR_SELL" and qty > pos.paired + 1e-9:
            return "RISK_REDUCTION_EXCEEDS_PAIRED_HOLDING"
        return None


class ShadowAdapter:
    """Execution simulation. NO VENUE, NO CREDENTIALS, NO NETWORK.

    Deliberately a separate class from the production adapter rather
    than the same one in a mode, so no flag, environment variable or
    configuration error can make this send an order.
    """

    name = "SHADOW_ONLY_NO_VENUE"

    def submits_orders(self) -> bool:
        return False

    def take(self, *, price: float, want: float, depth: float,
             evidence_class: str) -> dict:
        """Cross the spread. Fills against DISPLAYED depth only.

        Partial fills are the normal case, not an edge case: depth is
        what is showing, and wanting more than is showing gets what is
        showing.
        """
        if depth <= 0:
            return {"outcome": NO_FILL, "filled": 0.0, "price": price,
                    "why": "no displayed depth",
                    "evidence_class": evidence_class}
        filled = min(want, depth)
        return {"outcome": FILLED_FULL if filled >= want else FILLED_PARTIAL,
                "filled": filled, "price": price,
                "why": ("took %.4g of %.4g displayed" % (filled, depth)),
                "evidence_class": evidence_class}

    def rest(self, *, price: float, want: float, queue_ahead=None,
             touched: bool = False, evidence_class: str) -> dict:
        """Post a passive quote. A TOUCH IS NOT A FILL.

        Being touched means the market traded at our price. Whether WE
        filled depends on queue position, which is NOT_IDENTIFIED, so
        this returns NO_FILL with the reason rather than a fill it
        cannot justify. Callers that need to exercise the filled branch
        use `force_fill`, which stamps the record SYNTHETIC so the
        distinction survives into the output.
        """
        if not touched:
            return {"outcome": NO_FILL, "filled": 0.0, "price": price,
                    "why": "market did not trade at this level",
                    "evidence_class": evidence_class}
        return {"outcome": NO_FILL, "filled": 0.0, "price": price,
                "why": ("price was touched, but queue position is "
                        "NOT_IDENTIFIED so a fill is not established; a "
                        "touch is not a fill"),
                "queue_ahead": vc.UNKNOWN,
                "evidence_class": evidence_class}

    def force_fill(self, *, price: float, want: float, reason: str) -> dict:
        """Exercise the post-fill code path. ALWAYS SYNTHETIC."""
        return {"outcome": FILLED_FULL, "filled": want, "price": price,
                "why": "DECLARED SYNTHETIC FILL: %s" % reason,
                "synthetic_fill": True, "evidence_class": SYNTHETIC}

    def cancel(self, *, order_ref: str) -> dict:
        return {"outcome": CANCELLED, "filled": 0.0, "ref": order_ref,
                "why": "cancelled before fill"}

    def reprice(self, *, order_ref: str, old: float, new: float) -> dict:
        return {"outcome": REPRICED, "filled": 0.0, "ref": order_ref,
                "from": old, "to": new,
                "why": "cancel/replace; queue position is lost and the new "
                       "quote starts at the back"}


class ShadowLoop:
    """One process: observations in, decisions and cash movements out."""

    def __init__(self, *, opening_cash: float = 1000.0,
                 limits: RiskLimits | None = None,
                 fees: de.Fees | None = None,
                 venue: str = "polymarket-us",
                 account_class: str = "institutional"):
        self.adapter = ShadowAdapter()
        self.ledger = Ledger(opening_cash=opening_cash)
        self.limits = limits or RiskLimits()
        self.fees = fees if fees is not None else de.Fees(
            source="OMITTED_BY_CALLER")
        self.venue = venue
        self.account_class = account_class
        self.positions: dict[str, Position] = {}
        self.trace: list[dict] = []
        self.quotes: dict = {}
        self.settled: set[str] = set()

    # ── state ────────────────────────────────────────────────────────

    @property
    def deployed(self) -> float:
        # Remaining basis only. Realized profit is NOT capital
        # available to deploy, so it can never reduce this and can
        # never widen a limit.
        return sum(p.basis for p in self.positions.values())

    @property
    def open_markets(self) -> int:
        return sum(1 for p in self.positions.values() if not p.flat)

    def position(self, market_id: str) -> Position:
        return self.positions.setdefault(market_id, Position(market_id))

    # ── the loop ─────────────────────────────────────────────────────

    def step(self, book: de.Book, *, evidence_class: str = SYNTHETIC,
             max_contracts: float = 10.0,
             assume_unidentified_terms: str | None = None,
             reject_legs: tuple = (),
             recovery_book: de.Book | None = None) -> dict:
        """One observation through the whole chain.

        `assume_unidentified_terms` exists because the engine's honest
        answer is NO_TRADE almost always, and a loop that can only be
        exercised by a profitable opportunity would never demonstrate
        execution, inventory, settlement or recovery at all. Waiting for
        one is not an option either: the terms it waits on may never be
        identified.

        So the caller may DECLARE an assumption in writing. The engine is
        not weakened -- its refusal and its blocker are recorded
        unchanged -- and the loop then proceeds under the named
        assumption with every downstream record stamped
        SYNTHETIC_SCENARIO. The assumption travels in the trace, so no
        reader can mistake the resulting cash for evidence.

        It cannot override a REJECTED book or a BLOCKED action: bad data
        and a measured refutation are not assumptions, they are answers.
        """
        pos = self.position(book.market_id)
        inv = de.Inventory(yes_contracts=pos.yes, no_contracts=pos.no)

        decision = de.decide(
            book, inventory=inv, fees=self.fees,
            max_contracts=max_contracts, venue=self.venue,
            account_class=self.account_class)

        assumed = None
        if (assume_unidentified_terms
                and decision["selected"] in (de.NO_TRADE, de.HOLD)
                and decision["data_quality"] == "OK"):
            cand = next((c for c in decision["candidates"]
                         if c["status"] == de.NOT_IDENTIFIED
                         and c["conditional_payoff"] is not None), None)
            if cand is not None:
                assumed = {
                    "assumption": assume_unidentified_terms,
                    "action": cand["action"],
                    "engine_status": cand["status"],
                    "engine_blocker": cand["blocker"],
                    "conditional_payoff": cand["conditional_payoff"],
                    "warning": ("the engine did NOT select this. It is "
                                "executed under a declared assumption to "
                                "exercise the downstream path, and every "
                                "record it produces is SYNTHETIC."),
                }
                decision = dict(decision, selected=cand["action"],
                                size_contracts=cand["size_contracts"])
                evidence_class = SYNTHETIC

        rec = {"loop": LOOP_VERSION, "market_id": book.market_id,
               "evidence_class": evidence_class,
               "decision": decision["selected"],
               "reason": decision["reason"],
               "data_quality": decision["data_quality"],
               "size_requested": decision["size_contracts"],
               "execution": None, "risk": None,
               "assumed": assumed,
               "inventory_before": {"yes": pos.yes, "no": pos.no},
               "inventory_after": None,
               "cash_before": round(self.ledger.cash, 6)}

        if decision["selected"] in (de.NO_TRADE, de.HOLD):
            rec["inventory_after"] = {"yes": pos.yes, "no": pos.no}
            rec["cash_after"] = round(self.ledger.cash, 6)
            self.trace.append(rec)
            return rec

        # RISK BEFORE EXECUTION -- AND ONLY ON EXPOSURE-INCREASING ACTIONS.
        #
        # Caught by running the loop: PAIR_SELL was refused by
        # RISK_MAX_CASH_PER_MARKET. A sell REDUCES exposure, and an
        # entry limit that blocks an exit is the same trap as a kill
        # switch that blocks cancels: it freezes the position at exactly
        # the moment the limit says the position is too large. Limits
        # bind what goes on; they must never bind what comes off.
        size = decision["size_contracts"]
        reduces = decision["selected"] in (de.PAIR_SELL, de.SELL_YES,
                                           de.SELL_NO)
        if reduces:
            # EXEMPT FROM ENTRY LIMITS, NOT FROM ALL CHECKS. A sell is
            # not automatically safe: selling ONE side of a matched pair
            # converts a flat book into directional exposure, and a
            # quantity larger than the holding is not a reduction at all.
            blocked = self.limits.check_reduction(
                pos=pos, action=decision["selected"], qty=size, book=book)
            rec["risk"] = (blocked or
                           "REDUCTION_CHECKS_PASSED_ENTRY_LIMITS_EXEMPT")
        else:
            est_cash = (size * ((de._f(book.yes_ask) or 0)
                                + (de._f(book.no_ask) or 0))
                        + self.fees.fill_fee(size, maker=False) * 2)
            blocked = self.limits.check(
                pos=pos, add_contracts=2 * size, add_cash=est_cash,
                deployed=self.deployed, open_markets=self.open_markets,
                cash=self.ledger.cash)
        if blocked:
            rec["risk"] = blocked
            rec["decision"] = "%s_REFUSED_BY_RISK" % decision["selected"]
            rec["inventory_after"] = {"yes": pos.yes, "no": pos.no}
            rec["cash_after"] = round(self.ledger.cash, 6)
            self.trace.append(rec)
            return rec

        rec["execution"] = self._execute(decision, book, pos,
                                         evidence_class, reject_legs,
                                         recovery_book)
        rec["inventory_after"] = {"yes": pos.yes, "no": pos.no}
        rec["cash_after"] = round(self.ledger.cash, 6)
        self.trace.append(rec)
        return rec

    def _execute(self, decision: dict, book: de.Book, pos: Position,
                 evidence_class: str, reject_legs: tuple = (),
                 recovery_book: de.Book | None = None) -> dict:
        # `reject_legs` injects a venue rejection on a named leg. It is
        # how the ONE-LEG FILL branch gets exercised: in reality the
        # second leg fails because the price moved between the two
        # sends, and no arrangement of displayed depth reproduces that.
        # Injecting it is honest; contriving depth to fake it is not.
        action = decision["selected"]
        size = decision["size_contracts"]
        legs = []
        if action == de.PAIR_BUY:
            for leg, px, depth in (("yes", de._f(book.yes_ask),
                                    book.yes_ask_size),
                                   ("no", de._f(book.no_ask),
                                    book.no_ask_size)):
                if leg in reject_legs:
                    r = {"outcome": REJECTED, "filled": 0.0, "price": px,
                         "why": "DECLARED SYNTHETIC REJECTION: the price "
                                "moved between the two sends",
                         "evidence_class": SYNTHETIC}
                else:
                    r = self.adapter.take(price=px, want=size, depth=depth,
                                          evidence_class=evidence_class)
                r["leg"] = leg
                legs.append(r)
                if r["filled"] > 0:
                    # FEES ARE APPLIED PER FILL. The old path moved only
                    # qty x price and left fees_paid at zero whatever
                    # schedule was supplied.
                    fee = self.fees.fill_fee(r["filled"], maker=False)
                    cost = r["filled"] * px + fee
                    r["fee"] = round(fee, 6)
                    pos.buy(leg, r["filled"], px, fee)
                    self.ledger.fees_paid += fee
                    self.ledger.move("BUY_%s" % leg.upper(), -cost,
                                     "bought %.4g at %.4f, fee %.4f"
                                     % (r["filled"], px, fee),
                                     book.market_id)
        elif action == de.PAIR_SELL:
            for leg, px, depth in (("yes", de._f(book.yes_bid),
                                    book.yes_bid_size),
                                   ("no", de._f(book.no_bid),
                                    book.no_bid_size)):
                r = self.adapter.take(price=px, want=size, depth=depth,
                                      evidence_class=evidence_class)
                r["leg"] = leg
                legs.append(r)
                if r["filled"] > 0:
                    fee = self.fees.fill_fee(r["filled"], maker=False)
                    proceeds = r["filled"] * px - fee
                    realized = pos.sell(leg, r["filled"], px, fee)
                    r["fee"] = round(fee, 6)
                    r["realized"] = round(realized, 6)
                    self.ledger.fees_paid += fee
                    self.ledger.realized_pnl += realized
                    self.ledger.move("SELL_%s" % leg.upper(), proceeds,
                                     "sold %.4g at %.4f, fee %.4f, "
                                     "realized %+.4f"
                                     % (r["filled"], px, fee, realized),
                                     book.market_id)

        # A ONE-LEG FILL IS THE FAILURE MODE, and it is named rather than
        # averaged away. The position is now directional on a venue where
        # directional exposure is not permitted deliberately.
        filled = [l for l in legs if l["filled"] > 0]
        one_leg = len(filled) == 1
        out = {"legs": legs, "one_leg_only": one_leg,
               "unwound_exposure_required": one_leg,
               "note": ("a single-leg fill leaves naked directional "
                        "exposure; the loop records it rather than "
                        "netting it out of the average"
                        if one_leg else "")}
        if one_leg:
            # RECOVERY READS THE MARKET AGAIN. The second leg failed
            # because the price moved, so recovering against the book
            # that was already stale when the leg was rejected would be
            # deciding on the market that no longer exists. A caller
            # supplies the re-read; absent one, the entry book stands in
            # and the record says which was used.
            fresh = recovery_book is not None
            rb = recovery_book if fresh else book
            out["recovery_book"] = ("RE_READ:%s@%s"
                                    % (rb.market_id, rb.age_s) if fresh
                                    else "ENTRY_BOOK_REUSED_STALE")
            out["recovery"] = self._recover_one_leg(
                rb, pos, filled[0], evidence_class, fresh=fresh)
        return out

    # ── autonomous recovery from a one-leg fill ──────────────────────

    def _recover_one_leg(self, book, pos: Position, leg: dict,
                         evidence_class: str, *,
                         fresh: bool = False) -> dict:
        """Decide and ACT on naked exposure, from INCREMENTAL cash only.

        TWO DEFECTS THIS REPLACES, both found by review.

        The old threshold was `own_basis + other_ask < 1`. `own_basis`
        is what the held leg COST, which is sunk: it is identical under
        every action available now and therefore cannot discriminate
        between them. Including it meant an expensive entry made the
        engine less willing to complete, which is backwards. The
        completion FEE was missing from the threshold as well.

        The comparison is now between the two feasible actions, from the
        state we are actually in:

            COMPLETE   pay (other_ask x qty + fee) now, receive par at
                       settlement:   +qty - qty x other_ask - fee
            EXIT       receive (own_bid x qty - fee) now and give up the
                       held leg's settlement value, which is
                       NOT_IDENTIFIED for a single leg -- that is
                       exactly the exposure we are trying to remove.

        So COMPLETE is chosen when its incremental cash is positive AND
        beats the exit's certain proceeds. A COMPLETION THAT LOCKS AN
        OVERALL LOSS MAY STILL BE THE BETTER ACTION, because the loss is
        already incurred; what is being chosen is only what happens
        next.

        AND THE PAIR ARITHMETIC IS GATED ON THE ACCOUNT. If the venue
        NETS the complement against the held leg instead of creating an
        independent pair, buying it CLOSES rather than completes and the
        par model does not describe the cash at all.
        """
        side = leg["leg"]
        other = "no" if side == "yes" else "yes"
        qty = leg["filled"]

        # STALE OR UNIDENTIFIED RECOVERY DATA IS NOT A DECISION INPUT.
        # The second leg failed because the price moved; recovering on
        # the book that was already wrong is deciding on a market that
        # no longer exists.
        if not fresh:
            return {"action": "UNRESOLVED_EXPOSURE", "leg": side, "qty": qty,
                    "blocker": "NO_FRESH_RECOVERY_OBSERVATION",
                    "why": ("recovery requires its own observation with an "
                            "identity and a timestamp; the entry book is "
                            "known to be stale because the second leg was "
                            "rejected on it"),
                    "directional_contracts": pos.directional,
                    "evidence_class": evidence_class}
        bad = book.unreadable_reason() if hasattr(book, "unreadable_reason") \
            else "recovery observation is not a book"
        if bad:
            return {"action": "UNRESOLVED_EXPOSURE", "leg": side, "qty": qty,
                    "blocker": "RECOVERY_OBSERVATION_UNUSABLE",
                    "why": "recovery read rejected: %s" % bad,
                    "directional_contracts": pos.directional,
                    "evidence_class": evidence_class}

        caps = vc.capabilities(self.venue, self.account_class)
        pair_ok = caps.permits("holds_both_legs_independently")

        other_ask = de._f(getattr(book, "%s_ask" % other))
        other_depth = getattr(book, "%s_ask_size" % other)
        own_bid = de._f(getattr(book, "%s_bid" % side))
        own_bid_depth = getattr(book, "%s_bid_size" % side)

        complete_net = None
        if pair_ok and other_ask is not None and other_depth >= qty:
            fee = self.fees.fill_fee(qty, maker=False)
            complete_net = qty * (1.0 - other_ask) - fee
        exit_net = None
        if own_bid is not None and own_bid_depth > 0:
            sell_qty = min(qty, own_bid_depth)
            exit_net = sell_qty * own_bid - self.fees.fill_fee(
                sell_qty, maker=False)

        considered = {
            "complete_net_incremental": (round(complete_net, 6)
                                         if complete_net is not None else None),
            "exit_net_proceeds": (round(exit_net, 6)
                                  if exit_net is not None else None),
            "pair_capability": caps.holds_both_legs_independently,
            "sunk_basis_excluded": round(pos.basis, 6),
        }

        if complete_net is not None and (exit_net is None
                                         or complete_net > exit_net):
            fee = self.fees.fill_fee(qty, maker=False)
            cost = qty * other_ask + fee
            pos.buy(other, qty, other_ask, fee)
            self.ledger.fees_paid += fee
            self.ledger.move("RECOVER_BUY_%s" % other.upper(), -cost,
                             "completed the pair at %.4f" % other_ask,
                             book.market_id)
            return {"action": "COMPLETE", "leg": other, "qty": qty,
                    "price": other_ask, "fee": round(fee, 6),
                    "considered": considered,
                    "why": ("completing nets %+.6f incremental against an "
                            "exit worth %s; sunk basis excluded"
                            % (complete_net,
                               ("%+.6f" % exit_net) if exit_net is not None
                               else "nothing executable")),
                    "evidence_class": evidence_class}

        if exit_net is not None:
            sell_qty = min(qty, own_bid_depth)
            fee = self.fees.fill_fee(sell_qty, maker=False)
            realized = pos.sell(side, sell_qty, own_bid, fee)
            proceeds = sell_qty * own_bid - fee
            self.ledger.fees_paid += fee
            self.ledger.realized_pnl += realized
            self.ledger.move("RECOVER_SELL_%s" % side.upper(), proceeds,
                             "flattened %.4g at %.4f, realized %+.4f"
                             % (sell_qty, own_bid, realized),
                             book.market_id)
            # A PARTIAL EXIT IS NOT A RESOLUTION. If depth covered only
            # part of the stub, the rest is still naked and the policy
            # keeps running on it.
            remaining = qty - sell_qty
            out = {"action": "EXIT", "leg": side, "qty": sell_qty,
                   "price": own_bid, "realized": round(realized, 6),
                   "considered": considered,
                   "remaining_exposed": remaining,
                   "resolved": remaining <= 1e-9,
                   "why": ("exiting nets %+.6f against completion worth %s"
                           % (exit_net,
                              ("%+.6f" % complete_net)
                              if complete_net is not None else "unavailable")),
                   "evidence_class": evidence_class}
            if remaining > 1e-9:
                out["next"] = {
                    "action": "UNRESOLVED_EXPOSURE",
                    "qty": remaining,
                    "why": ("only %.4g of %.4g cleared at the bid; the "
                            "remainder is still naked and the policy "
                            "continues on it" % (sell_qty, qty)),
                    "directional_contracts": pos.directional}
            return out

        return {"action": "UNRESOLVED_EXPOSURE", "leg": side, "qty": qty,
                "blocker": "NO_EXECUTABLE_ACTION",
                "considered": considered,
                "why": ("neither completion nor exit is executable: "
                        "complement %s, own bid %s"
                        % ("unavailable" if other_ask is None
                           else "capability %s" % caps.holds_both_legs_independently,
                           "absent" if own_bid is None else "no depth")),
                "directional_contracts": pos.directional,
                "evidence_class": evidence_class}

    def quote(self, book: de.Book, *, side: str, price: float,
              size: float, evidence_class: str = SYNTHETIC) -> dict:
        """Post a maker quote. STATEFUL: it lives until it resolves.

        The old version called a simulator that always returned NO_FILL
        and returned a message; cancel and reprice returned their own
        isolated messages touching nothing. A quote that cannot change
        state is not a lifecycle.

        A live quote holds risk budget while it rests, so it is counted
        against limits on creation and released when it resolves.
        """
        qid = "q%d" % (len(self.quotes) + 1)
        blocked = self._admit_quote(book.market_id, price, size)
        rec = {"loop": LOOP_VERSION, "market_id": book.market_id,
               "evidence_class": evidence_class, "quote_id": qid,
               "decision": "MAKE_%s" % side.upper(), "price": price,
               "size": size, "risk": blocked,
               "cash_before": round(self.ledger.cash, 6),
               "cash_after": round(self.ledger.cash, 6)}
        if blocked:
            rec["state"] = "REFUSED"
            self.trace.append(rec)
            return rec
        self.quotes[qid] = {"id": qid, "market_id": book.market_id,
                            "side": side, "price": price, "size": size,
                            "state": "RESTING", "filled": 0.0,
                            "evidence_class": evidence_class,
                            "history": ["RESTING at %.4f" % price]}
        rec["state"] = "RESTING"
        self.trace.append(rec)
        return rec

    @property
    def quoted_exposure(self) -> float:
        """Cash every LIVE quote would consume if its remainder filled.

        Two defects, both reproduced. PARTIAL was excluded, so filling 6
        of 10 dropped the reservation on the remaining 4 to zero while
        that remainder was still working in the market. And the fee that
        the remainder would incur was never reserved, so a quote could
        fill into a cash balance that could not pay for it.
        """
        total = 0.0
        for q in self.quotes.values():
            if q["state"] not in LIVE_QUOTE_STATES:
                continue
            rem = q["size"] - q["filled"]
            if rem <= 0:
                continue
            total += rem * q["price"] + self.fees.fill_fee(rem, maker=True)
        return total

    def quote_reservation(self, price: float, size: float) -> float:
        """What one quote of this size reserves, fee included."""
        return size * price + self.fees.fill_fee(size, maker=True)

    def _admit_quote(self, market_id: str, price: float,
                     size: float) -> str | None:
        """One admission check, used by BOTH creation and replacement.

        A replacement used to bypass this entirely: repricing ten shares
        from 0.45 to 0.99 with five dollars of cash was accepted and
        reserved 9.90. A cancel/replace is a NEW order and has to clear
        the same bar the original did.

        Aggregate reservations are included, so a second quote cannot be
        admitted against cash the first one has already spoken for.
        """
        if size is None or not math.isfinite(size) or size <= 0:
            return "RISK_QUOTE_INVALID_SIZE"
        if price is None or not math.isfinite(price) or not 0 < price < 1:
            return "RISK_QUOTE_INVALID_PRICE"
        held = self.position(market_id)
        reserve = self.quote_reservation(price, size)
        # Per-market and position limits must see the market's own live
        # quotes as well as its inventory.
        market_quoted = sum(
            self.quote_reservation(q["price"], q["size"] - q["filled"])
            for q in self.quotes.values()
            if q["market_id"] == market_id
            and q["state"] in LIVE_QUOTE_STATES
            and q["size"] - q["filled"] > 0)
        market_quoted_contracts = sum(
            q["size"] - q["filled"] for q in self.quotes.values()
            if q["market_id"] == market_id
            and q["state"] in LIVE_QUOTE_STATES)
        quoted_markets = {q["market_id"] for q in self.quotes.values()
                          if q["state"] in LIVE_QUOTE_STATES}
        open_markets = len(
            {k for k, v in self.positions.items() if not v.flat}
            | quoted_markets)
        return self.limits.check(
            pos=held,
            add_contracts=size + market_quoted_contracts,
            add_cash=reserve + market_quoted,
            deployed=self.deployed + self.quoted_exposure,
            open_markets=open_markets, cash=self.ledger.cash)

    def touch(self, quote_id: str) -> dict:
        """The market traded at our price. THAT IS NOT A FILL."""
        q = self.quotes[quote_id]
        q["history"].append("TOUCHED (queue position NOT_IDENTIFIED)")
        rec = {"loop": LOOP_VERSION, "quote_id": quote_id,
               "market_id": q["market_id"], "decision": "TOUCH",
               "evidence_class": q["evidence_class"], "state": q["state"],
               "outcome": NO_FILL, "queue_ahead": vc.UNKNOWN,
               "why": ("a touch is not a fill; whether we filled depends "
                       "on queue position, which is NOT_IDENTIFIED"),
               "cash_before": round(self.ledger.cash, 6),
               "cash_after": round(self.ledger.cash, 6)}
        self.trace.append(rec)
        return rec

    def fill_quote(self, quote_id: str, *, qty: float, reason: str) -> dict:
        """Fill a resting quote and carry it into inventory and cash.

        ALWAYS SYNTHETIC. Queue position is NOT_IDENTIFIED, so no fill
        this simulator produces is an observed execution -- and the
        record says so even when the BOOK it was quoted against was a
        real captured observation. A fresh observation does not make its
        simulated fill real.
        """
        q = self.quotes[quote_id]
        if q["state"] not in LIVE_QUOTE_STATES:
            return {"quote_id": quote_id, "refused": "NOT_LIVE",
                    "state": q["state"]}
        if q["size"] - q["filled"] <= 0:
            return {"quote_id": quote_id, "refused": "NO_REMAINDER",
                    "state": q["state"]}
        qty = min(qty, q["size"] - q["filled"])
        pos = self.position(q["market_id"])
        leg = "yes" if q["side"].lower() == "yes" else "no"
        fee = self.fees.fill_fee(qty, maker=True)
        cost = qty * q["price"] + fee
        pos.buy(leg, qty, q["price"], fee)
        self.ledger.fees_paid += fee
        self.ledger.move("MAKER_FILL_%s" % leg.upper(), -cost,
                         "maker fill %.4g at %.4f, fee %.4f"
                         % (qty, q["price"], fee), q["market_id"])
        q["filled"] += qty
        q["state"] = "FILLED" if q["filled"] >= q["size"] else "PARTIAL"
        q["history"].append("%s %.4g at %.4f" % (q["state"], qty, q["price"]))
        rec = {"loop": LOOP_VERSION, "quote_id": quote_id,
               "market_id": q["market_id"], "decision": "MAKER_FILL",
               # The FILL is synthetic whatever the book's provenance.
               "evidence_class": SYNTHETIC,
               "book_evidence_class": q["evidence_class"],
               "synthetic_fill": True, "why": "DECLARED SYNTHETIC: %s" % reason,
               "state": q["state"], "filled": qty, "fee": round(fee, 6),
               "inventory_after": {"yes": pos.yes, "no": pos.no},
               "cash_after": round(self.ledger.cash, 6)}
        self.trace.append(rec)
        return rec

    def cancel_quote(self, quote_id: str) -> dict:
        q = self.quotes[quote_id]
        if q["state"] not in ("RESTING", "PARTIAL"):
            return {"quote_id": quote_id, "refused": "NOT_CANCELLABLE",
                    "state": q["state"]}
        q["state"] = CANCELLED
        q["history"].append("CANCELLED with %.4g unfilled"
                            % (q["size"] - q["filled"]))
        rec = {"loop": LOOP_VERSION, "quote_id": quote_id,
               "market_id": q["market_id"], "decision": "CANCEL",
               "evidence_class": q["evidence_class"], "state": CANCELLED,
               "released_exposure": round(q["price"]
                                          * (q["size"] - q["filled"]), 6),
               "cash_after": round(self.ledger.cash, 6)}
        self.trace.append(rec)
        return rec

    def reprice_quote(self, quote_id: str, *, new_price: float) -> dict:
        """Cancel/replace. QUEUE POSITION IS LOST, and that is the cost.

        A reprice is not an edit. It is a cancel and a new order at the
        back of the queue, so the new quote's fill probability is not
        the old one's, and the lifecycle records it as a NEW quote
        rather than a changed field.
        """
        old = self.quotes[quote_id]
        if old["state"] not in ("RESTING", "PARTIAL"):
            return {"quote_id": quote_id, "refused": "NOT_REPRICEABLE",
                    "state": old["state"]}
        remaining = old["size"] - old["filled"]
        if remaining <= 0:
            return {"quote_id": quote_id, "refused": "NO_REMAINDER"}
        # THE REPLACEMENT IS A NEW ORDER. Check it as one -- but exclude
        # the order being replaced from the aggregate, since its
        # reservation is released by the cancel half of cancel/replace.
        held_state = old["state"]
        old["state"] = "REPLACING"
        blocked = self._admit_quote(old["market_id"], new_price, remaining)
        if blocked:
            old["state"] = held_state
            rec = {"loop": LOOP_VERSION, "quote_id": quote_id,
                   "market_id": old["market_id"], "decision": "REPRICE",
                   "evidence_class": old["evidence_class"],
                   "refused": blocked, "from": old["price"],
                   "to": new_price, "size": remaining,
                   "why": ("a cancel/replace is a NEW order and must clear "
                           "the same checks the original did"),
                   "cash_after": round(self.ledger.cash, 6)}
            self.trace.append(rec)
            return rec
        old["state"] = REPRICED
        old["history"].append("REPRICED to %.4f" % new_price)
        nid = "q%d" % (len(self.quotes) + 1)
        self.quotes[nid] = {"id": nid, "market_id": old["market_id"],
                            "side": old["side"], "price": new_price,
                            "size": remaining, "state": "RESTING",
                            "filled": 0.0,
                            "evidence_class": old["evidence_class"],
                            "history": ["RESTING at %.4f (repriced from %s, "
                                        "queue position lost)"
                                        % (new_price, quote_id)]}
        rec = {"loop": LOOP_VERSION, "quote_id": quote_id,
               "new_quote_id": nid, "market_id": old["market_id"],
               "decision": "REPRICE", "evidence_class": old["evidence_class"],
               "from": old["price"], "to": new_price, "size": remaining,
               "why": ("cancel/replace: the new quote starts at the BACK "
                       "of the queue, so its fill probability is not the "
                       "old one's"),
               "cash_after": round(self.ledger.cash, 6)}
        self.trace.append(rec)
        return rec

    # ── settlement ───────────────────────────────────────────────────

    def settle(self, market_id: str, *, yes_wins: bool) -> dict:
        """Resolve a market. A pair pays par; a naked leg pays 0 or 1."""
        pos = self.position(market_id)
        if market_id in self.settled:
            return {"market_id": market_id, "skipped": "ALREADY_SETTLED"}
        payout = (pos.yes * (1.0 if yes_wins else 0.0)
                  + pos.no * (0.0 if yes_wins else 1.0))
        self.ledger.move("SETTLE", payout,
                         "yes=%.4g no=%.4g, yes_wins=%s"
                         % (pos.yes, pos.no, yes_wins), market_id)
        self.ledger.settlements += payout
        basis = pos.basis
        realised = payout - basis
        pos.realized_pnl += realised
        self.ledger.realized_pnl += realised
        rec = {"loop": LOOP_VERSION, "market_id": market_id,
               "evidence_class": SYNTHETIC,
               "decision": "SETTLE", "payout": round(payout, 6),
               "cost_basis": round(basis, 6),
               "realised": round(realised, 6),
               "position_realized_pnl": round(pos.realized_pnl, 6),
               "cash_after": round(self.ledger.cash, 6)}
        pos.yes = pos.no = 0.0
        pos.yes_basis = pos.no_basis = 0.0
        self.settled.add(market_id)
        self.trace.append(rec)
        return rec

    # ── restart recovery ─────────────────────────────────────────────

    def snapshot(self) -> str:
        return json.dumps({
            "version": LOOP_VERSION,
            "cash": self.ledger.cash,
            "opening_cash": self.ledger.opening_cash,
            "movements": self.ledger.movements,
            "settled": sorted(self.settled),
            "quotes": self.quotes,
            "positions": {k: asdict(v) for k, v in self.positions.items()},
        }, sort_keys=True)

    @classmethod
    def restore(cls, blob: str, **kw) -> "ShadowLoop":
        """Rebuild from a snapshot. Cash and inventory must both survive.

        A loop that restarts with its cash but not its positions would
        report a profit equal to everything it still owns.
        """
        d = json.loads(blob)
        loop = cls(opening_cash=d["opening_cash"], **kw)
        loop.ledger.cash = d["cash"]
        loop.ledger.movements = list(d["movements"])
        loop.settled = set(d["settled"])
        loop.quotes = dict(d.get("quotes") or {})
        loop.positions = {k: Position(**v) for k, v in d["positions"].items()}
        return loop

    # ── report ───────────────────────────────────────────────────────

    def report(self) -> dict:
        by_class: dict[str, int] = {}
        for t in self.trace:
            c = t.get("evidence_class", "UNCLASSIFIED")
            by_class[c] = by_class.get(c, 0) + 1
        decisions: dict[str, int] = {}
        for t in self.trace:
            decisions[t["decision"]] = decisions.get(t["decision"], 0) + 1
        return {
            "loop": LOOP_VERSION,
            "steps": len(self.trace),
            "decisions": decisions,
            "by_evidence_class": by_class,
            "fees_paid": round(self.ledger.fees_paid, 6),
            "realized_pnl": round(self.ledger.realized_pnl, 6),
            "open_positions": {k: {"yes": v.yes, "no": v.no,
                                       "basis": round(v.basis, 6),
                                   "realized_pnl": round(v.realized_pnl, 6),
                                   "directional": v.directional}
                               for k, v in self.positions.items()
                               if not v.flat},
            "unsettled_cost_basis": round(self.deployed, 6),
            "live_quotes": {k: v for k, v in self.quotes.items()
                            if v["state"] in LIVE_QUOTE_STATES},
            "quoted_exposure": round(self.quoted_exposure, 6),
            "ledger": self.ledger.reconciles(),
            "profitability_claim": (
                "NONE. Synthetic scenarios demonstrate software behaviour "
                "and establish nothing about profitability. Only "
                "PROSPECTIVE_SHADOW records could support a forward claim, "
                "and none are produced by a synthetic run."),
        }
