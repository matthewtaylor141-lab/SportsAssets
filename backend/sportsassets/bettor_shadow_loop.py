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


@dataclass
class Position:
    """Inventory on one market, with the cash that created it."""
    market_id: str
    yes: float = 0.0
    no: float = 0.0
    cash_spent: float = 0.0          # positive = paid out
    opened_at: float | None = None

    @property
    def paired(self) -> float:
        return min(self.yes, self.no)

    @property
    def flat(self) -> bool:
        return self.yes == 0.0 and self.no == 0.0


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
        if pos.cash_spent + add_cash > self.max_cash_per_market:
            return "RISK_MAX_CASH_PER_MARKET"
        if deployed + add_cash > self.max_total_deployed:
            return "RISK_MAX_TOTAL_DEPLOYED"
        if pos.flat and open_markets >= self.max_open_markets:
            return "RISK_MAX_OPEN_MARKETS"
        if cash - add_cash < self.min_cash_reserve:
            return "RISK_MIN_CASH_RESERVE"
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
        self.settled: set[str] = set()

    # ── state ────────────────────────────────────────────────────────

    @property
    def deployed(self) -> float:
        return sum(p.cash_spent for p in self.positions.values())

    @property
    def open_markets(self) -> int:
        return sum(1 for p in self.positions.values() if not p.flat)

    def position(self, market_id: str) -> Position:
        return self.positions.setdefault(market_id, Position(market_id))

    # ── the loop ─────────────────────────────────────────────────────

    def step(self, book: de.Book, *, evidence_class: str = SYNTHETIC,
             max_contracts: float = 10.0,
             assume_unidentified_terms: str | None = None,
             reject_legs: tuple = ()) -> dict:
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
            blocked = None
            rec["risk"] = "NOT_APPLIED_ACTION_REDUCES_EXPOSURE"
        else:
            est_cash = size * ((de._f(book.yes_ask) or 0)
                               + (de._f(book.no_ask) or 0))
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
                                         evidence_class, reject_legs)
        rec["inventory_after"] = {"yes": pos.yes, "no": pos.no}
        rec["cash_after"] = round(self.ledger.cash, 6)
        self.trace.append(rec)
        return rec

    def _execute(self, decision: dict, book: de.Book, pos: Position,
                 evidence_class: str, reject_legs: tuple = ()) -> dict:
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
                    cost = r["filled"] * px
                    setattr(pos, leg, getattr(pos, leg) + r["filled"])
                    pos.cash_spent += cost
                    self.ledger.move("BUY_%s" % leg.upper(), -cost,
                                     "bought %.4g at %.4f" % (r["filled"], px),
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
                    proceeds = r["filled"] * px
                    setattr(pos, leg, getattr(pos, leg) - r["filled"])
                    pos.cash_spent -= proceeds
                    self.ledger.move("SELL_%s" % leg.upper(), proceeds,
                                     "sold %.4g at %.4f" % (r["filled"], px),
                                     book.market_id)

        # A ONE-LEG FILL IS THE FAILURE MODE, and it is named rather than
        # averaged away. The position is now directional on a venue where
        # directional exposure is not permitted deliberately.
        filled = [l for l in legs if l["filled"] > 0]
        one_leg = len(filled) == 1
        return {"legs": legs, "one_leg_only": one_leg,
                "unwound_exposure_required": one_leg,
                "note": ("a single-leg fill leaves naked directional "
                         "exposure; the loop records it rather than "
                         "netting it out of the average"
                         if one_leg else "")}

    # ── maker path ───────────────────────────────────────────────────

    def quote(self, book: de.Book, *, side: str, price: float,
              size: float, touched: bool = False,
              evidence_class: str = SYNTHETIC) -> dict:
        """Post a maker quote and record what happened to it."""
        econ = me.evaluate(
            me.MakerParams(name="live_book"),
            bid=de._f(book.yes_bid), ask=de._f(book.yes_ask))
        r = self.adapter.rest(price=price, want=size, touched=touched,
                              evidence_class=evidence_class)
        rec = {"loop": LOOP_VERSION, "market_id": book.market_id,
               "evidence_class": evidence_class, "decision": "MAKE_%s"
               % side.upper(), "execution": r,
               "maker_economics": {"status": econ.status,
                                   "missing": econ.missing},
               "cash_before": round(self.ledger.cash, 6),
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
        realised = payout - pos.cash_spent
        rec = {"loop": LOOP_VERSION, "market_id": market_id,
               "evidence_class": SYNTHETIC if market_id not in self.settled
               else SYNTHETIC,
               "decision": "SETTLE", "payout": round(payout, 6),
               "cost_basis": round(pos.cash_spent, 6),
               "realised": round(realised, 6),
               "cash_after": round(self.ledger.cash, 6)}
        pos.yes = pos.no = 0.0
        pos.cash_spent = 0.0
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
            "open_positions": {k: {"yes": v.yes, "no": v.no,
                                   "cash_spent": round(v.cash_spent, 6)}
                               for k, v in self.positions.items()
                               if not v.flat},
            "unsettled_cost_basis": round(self.deployed, 6),
            "ledger": self.ledger.reconciles(),
            "profitability_claim": (
                "NONE. Synthetic scenarios demonstrate software behaviour "
                "and establish nothing about profitability. Only "
                "PROSPECTIVE_SHADOW records could support a forward claim, "
                "and none are produced by a synthetic run."),
        }
