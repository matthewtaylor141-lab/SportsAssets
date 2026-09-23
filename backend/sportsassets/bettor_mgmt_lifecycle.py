"""DRIVING A SELECTED ACTION THROUGH `bettor_desk.Order` / `Portfolio`.

NO NEW BOOK, NO NEW ORDER TYPE, NO NEW ACCOUNTING. Everything here is a
call into machinery that already exists:

    bettor_desk.Order        the state machine: PROPOSED -> RESTING ->
                             PARTIALLY_FILLED -> FILLED / CANCELLED /
                             EXPIRED / REJECTED, with `events`, `fills`,
                             `remaining` and `avg_fill_price`
    bettor_desk.Portfolio    cash, per-leg qty and basis, realised P&L,
                             fees, settle(), invariant()
    bettor_desk.Consumption  one print, one finite size, allocated once
                             and keyed on the print id
    bettor_mgmt_router.route  the matched/residual split and the engines
    bettor_mgmt_select        the declared ranking over priced actions

WHAT THIS ADDS is the wiring: turning a selected action into an order,
feeding observed prints to it, and keeping the residual under management
as the order's state changes.

────────────────────────────────────────────────────────────────────
ONE ACTIVE MANAGEMENT ORDER PER RESIDUAL POSITION. A DOCUMENTED CHOICE.

The first release maintains AT MOST ONE open management order per
residual leg. Simultaneous coordinated orders -- a resting completion and
a resting exit at once -- are NOT supported, and this is a decision
rather than an oversight:

    Two open orders on one position can BOTH fill before either
    cancellation is acknowledged. The book would then be over-managed:
    the exit sold inventory the completion was pairing, leaving a short
    or a double spend of the same contracts. Handling that needs a
    cancel/fill race protocol and an atomic resize, and doing it badly is
    worse than not doing it.

    So: before a new action is placed, any existing management order on
    that leg is CANCELLED and its acknowledgement is awaited. The
    cancel/fill race is still handled -- a print can fill an order in
    CANCEL_PENDING, and that fill is REAL and is booked -- but only one
    order is ever exposed to it.

`supports_simultaneous_orders` is False in the returned state so no
reader can infer otherwise from the absence of a statement.
────────────────────────────────────────────────────────────────────

AN ORDER MAY NEVER EXCEED REMAINING INVENTORY. A sell is capped at the
leg's held quantity and a completion at the unpaired remainder, checked
at placement AND again at each fill, because inventory can change under
an order that is already resting.

FUTURE OUTCOMES ARE NOT AVAILABLE HERE. `settle()` is called only by
`settle_at_observed_payout`, which is a separate entry point taking an
observed payout. Nothing in the decision path can reach it.
"""

from __future__ import annotations

from . import bettor_desk as dk
from . import bettor_mgmt_select as sel

VERSION = "BETTOR_MGMT_LIFECYCLE_V1"

# Intents, mapped to the engine's own action vocabulary so the order
# records which evaluated action produced it.
INTENT_FOR = {
    "TAKE_COMPLEMENT": "COMPLETE_PAIR",
    "POST_COMPLEMENT": "COMPLETE_PAIR",
    "DIRECT_EXIT": "EXIT",
    "REDUCE": "REDUCE",
}

SUPPORTS_SIMULTANEOUS = False
SIMULTANEOUS_NOTE = (
    "ONE active management order per residual leg. Two open orders can "
    "both fill before either cancel is acknowledged, which would sell "
    "inventory a completion was pairing. Before placing a new action any "
    "existing management order on that leg is cancelled first.")


class Managed:
    """One residual leg under management, with its order lifecycle.

    Holds a `bettor_desk.Portfolio` and `Consumption` -- it does not
    reimplement either.
    """

    def __init__(self, *, condition_id, outcome_index, seed_qty,
                 seed_price, at, fee_fn, queue_share=0.25,
                 expiry_s=900.0, account="rn1x"):
        self.condition_id = condition_id
        self.leg = int(outcome_index)
        self.fee_fn = fee_fn
        self.queue_share = float(queue_share)
        self.expiry_s = float(expiry_s)
        self.account = account
        # THE ASSIGNED ENTRY. Booked through Portfolio.buy at zero fee,
        # because the seed is ASSIGNED at RN1's own fill price -- it is
        # not an execution of ours and no fee of ours was paid.
        self.pf = dk.Portfolio(0.0)
        self.pf.buy(condition_id, self.leg, float(seed_qty),
                    float(seed_price), 0.0, float(at))
        self.seed_basis = self.pf.inventory_cost()
        self.cons = dk.Consumption()
        self.orders = {}
        self.decisions = []
        self._n = 0
        self.seed_qty = float(seed_qty)
        self.seed_price = float(seed_price)

    # ── inventory ────────────────────────────────────────────────────
    def held(self, leg=None) -> float:
        lg = self.pf._leg(self.condition_id,
                          self.leg if leg is None else leg)
        return float(lg["qty"])

    def other_leg(self) -> int:
        return 1 - self.leg

    def matched(self) -> float:
        return min(self.held(self.leg), self.held(self.other_leg()))

    def residual(self) -> float:
        """The UNPAIRED quantity on the seeded leg.

        This is what stays under management. A partial completion reduces
        it; it does not remove the position from management.
        """
        return max(0.0, self.held(self.leg) - self.matched())

    def open_orders(self) -> list:
        return [o for o in self.orders.values() if o.state in dk.OPEN_STATES]

    # ── placing, under the one-order rule ────────────────────────────
    def _oid(self, at) -> str:
        self._n += 1
        return "%s-O-%d-%03d" % (self.account, int(at), self._n)

    def place(self, action, *, at, price, qty, decision_id,
              liquidity="TAKER") -> dict:
        """Place ONE management order, cancelling any incumbent first."""
        cancelled = []
        for o in self.open_orders():
            o.transition(dk.CANCEL_PENDING, at,
                         "superseded by %s" % action)
            cancelled.append(o.order_id)

        side = "SELL" if action in ("DIRECT_EXIT", "REDUCE") else "BUY"
        leg = self.leg if side == "SELL" else self.other_leg()
        # THE CAP. A sell may not exceed the held quantity; a completion
        # may not exceed the unpaired remainder. Checked here and again
        # at fill time, because inventory moves under a resting order.
        cap = self.held(self.leg) if side == "SELL" else self.residual()
        want = min(float(qty), cap)
        if want <= 1e-9:
            return {"placed": None, "cancelled": cancelled,
                    "refused": "NO_REMAINING_INVENTORY",
                    "why": ("a %s was selected but the cap is %.4f: an "
                            "order may never exceed remaining inventory"
                            % (action, cap))}
        o = dk.Order(self._oid(at), condition_id=self.condition_id,
                     outcome_index=leg, side=side,
                     intent=INTENT_FOR.get(action, action),
                     limit_price=float(price), qty=want,
                     placed_at=float(at),
                     expires_at=float(at) + self.expiry_s,
                     decision_id=decision_id)
        o.transition(dk.RESTING, at, "placed as %s (%s)" % (action,
                                                            liquidity))
        self.orders[o.order_id] = o
        return {"placed": o.order_id, "cancelled": cancelled,
                "qty": want, "capped": want < float(qty) - 1e-12,
                "liquidity": liquidity, "refused": None}

    def acknowledge_cancels(self, at) -> list:
        """CANCEL_PENDING -> CANCELLED. Separate from requesting the
        cancel, because the gap between the two is where the race lives.
        An order still in CANCEL_PENDING can still fill."""
        done = []
        for o in self.orders.values():
            if o.state == dk.CANCEL_PENDING:
                o.transition(dk.CANCELLED, at, "cancel acknowledged")
                done.append(o.order_id)
        return done

    def expire(self, at) -> list:
        out = []
        for o in self.open_orders():
            if at >= o.expires_at:
                o.transition(dk.EXPIRED, at,
                             "expiry reached with %.4f unfilled" % o.remaining)
                out.append(o.order_id)
        return out

    # ── fills, from observed prints ──────────────────────────────────
    def on_print(self, *, at, outcome_index, price, size, evidence_id):
        """Allocate one OBSERVED print against our open orders.

        THE CANCEL/FILL RACE IS REAL AND IS BOOKED. An order in
        CANCEL_PENDING has not been cancelled yet, so a print that
        crosses it fills it. Ignoring that would under-report our
        inventory and the accounting would not reconcile.
        """
        self.cons.offer(evidence_id, float(size) * self.queue_share)
        out = []
        cands = [o for o in self.orders.values()
                 if o.state in dk.OPEN_STATES
                 and o.outcome_index == int(outcome_index)]
        cands.sort(key=lambda o: o.placed_at)
        for o in cands:
            crosses = (price <= o.limit_price if o.side == "BUY"
                       else price >= o.limit_price)
            if not crosses or o.remaining <= 1e-9:
                continue
            # RE-CHECK THE CAP AT FILL TIME.
            cap = (self.held(self.leg) if o.side == "SELL"
                   else self.residual())
            want = min(o.remaining, cap)
            if want <= 1e-9:
                o.transition(dk.CANCELLED, at,
                             "inventory no longer supports this order")
                out.append({"order_id": o.order_id, "qty": 0.0,
                            "why": "CAP_EXHAUSTED"})
                continue
            got = self.cons.take(evidence_id, want)
            if got <= 1e-9:
                continue
            fee = float(self.fee_fn(qty=got, price=price))
            raced = o.state == dk.CANCEL_PENDING
            if o.side == "BUY":
                self.pf.buy(self.condition_id, o.outcome_index, got,
                            price, fee, at)
            else:
                self.pf.sell(self.condition_id, o.outcome_index, got,
                             price, fee, at)
            o.filled_qty += got
            o.fees += fee
            o.fills.append({"at": at, "qty": round(got, 6), "price": price,
                            "fee_usd": round(fee, 6),
                            "evidence_id": evidence_id,
                            "raced_a_pending_cancel": raced,
                            "exec_model": dk.FILL_MODEL})
            o.transition(dk.FILLED if o.remaining <= 1e-9
                         else (dk.CANCEL_PENDING if raced
                               else dk.PARTIALLY_FILLED),
                         at,
                         "print through at %.4f%s"
                         % (price, " WHILE CANCEL PENDING" if raced else ""),
                         qty=round(got, 6), evidence_id=evidence_id)
            out.append({"order_id": o.order_id, "qty": got, "price": price,
                        "fee_usd": fee, "raced_a_pending_cancel": raced,
                        "remaining_after": o.remaining,
                        "residual_after": self.residual()})
        return out

    # ── settlement, reachable only from here ────────────────────────
    def settle_at_observed_payout(self, payouts: dict, at) -> dict:
        """SEPARATE ENTRY POINT. Nothing in the decision path calls it."""
        done = {}
        for leg in (self.leg, self.other_leg()):
            q = self.held(leg)
            if q <= 1e-9:
                continue
            p = payouts.get(leg)
            if p is None:
                done[leg] = {"qty": q, "settled": None,
                             "why": "NO_OBSERVED_PAYOUT_FOR_THIS_LEG"}
                continue
            self.pf.settle(self.condition_id, leg, float(p), at)
            done[leg] = {"qty": q, "payout": float(p),
                         "settled_usd": float(p) * q}
        return done

    # ── the reconciled state ────────────────────────────────────────
    def state(self) -> dict:
        d = self.pf.to_dict()
        inv = self.pf.invariant()
        open_o = self.open_orders()
        return {
            "version": VERSION,
            "supports_simultaneous_orders": SUPPORTS_SIMULTANEOUS,
            "simultaneous_note": SIMULTANEOUS_NOTE,
            "seed": {"qty": self.seed_qty, "price": self.seed_price,
                     "basis_usd": self.seed_basis,
                     "label": ("ASSIGNED ENTRY -- not evidence that we "
                               "could have obtained this fill")},
            "held": {self.leg: self.held(self.leg),
                     self.other_leg(): self.held(self.other_leg())},
            "matched_qty": self.matched(),
            "residual_qty": self.residual(),
            "residual_note": ("the unpaired remainder stays under "
                              "management: a partial completion reduces "
                              "it, it does not remove the position"),
            "open_orders": [o.to_dict() for o in open_o],
            "all_orders": [o.to_dict() for o in self.orders.values()],
            "portfolio": d,
            "invariant": inv,
            "open_inventory_valuation": {
                "inventory_cost_usd": self.pf.inventory_cost(),
                "mark_usd": "NOT_IDENTIFIED",
                "why": ("no contemporaneous book is retained for these "
                        "instants, so open inventory is reported at COST "
                        "and separately from realised P&L, never marked"),
            },
        }
