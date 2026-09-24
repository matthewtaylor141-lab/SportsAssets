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
        # Fills that exceeded remaining inventory. Recorded,
        # never silently trimmed away.
        self.discrepancies = []
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
            if o.state != dk.CANCEL_PENDING:
                o.transition(dk.CANCEL_PENDING, at,
                             "superseded by %s" % action)
            cancelled.append(o.order_id)

        # Pending cancellation is still executable. Re-evaluate quantity
        # after acknowledgement, including any intervening fills.
        if cancelled:
            return {"placed": None, "cancelled": cancelled, "qty": 0.0,
                    "refused": "WAIT_CANCEL_ACK"}

        side = "SELL" if action in ("DIRECT_EXIT", "REDUCE") else "BUY"
        leg = self.leg if side == "SELL" else self.other_leg()
        # THE CAP. A sell may not exceed the held quantity; a completion
        # may not exceed the unpaired remainder. Checked here and again
        # at fill time, because inventory moves under a resting order.
        cap = self.residual()
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

    def manage_policy(self, *, at, decision_id, sport=None, progress=None,
                      bid=None, bid_size=None, policy_params=None) -> dict:
        """Management's frozen policy; prints are never executable bids.

        Callers may supply bid/depth only from a contemporaneous book.
        No sport is currently admitted by the event-phase registry.

        `policy_params` IS FOR CHALLENGER EVALUATION ONLY and defaults to
        None, which is the frozen policy exactly. Production callers omit
        it. A challenger must be able to vary the one number it questions
        without editing a frozen module -- an edit is how a frozen policy
        quietly becomes a tuned one. Every decision records which
        parameters it ran under, so a row cannot be mistaken for the
        champion's.
        """
        from . import bettor_rn1x_policy as policy

        pp = dict(policy_params or {})
        managed = pp.pop("manage", True)
        q = self.residual()
        rec = {"at": at, "policy_id": policy.POLICY_ID,
               "residual_qty": q, "acted": False,
               "execution_secured": False,
               "policy_params": dict(pp) or None,
               "is_champion_policy": not pp and managed}
        if not managed:
            # THE NULL ARM. Assigned inventory, no management at all --
            # which is a legitimate comparator, not an error state.
            rec["operating_state"] = "HOLD_TO_SETTLEMENT_UNMANAGED"
            self.decisions.append(rec)
            return rec
        if q <= 1e-9:
            rec["operating_state"] = "NO_RESIDUAL"
            self.decisions.append(rec)
            return rec
        basis = self.pf._leg(self.condition_id, self.leg)["cost"] / self.held()
        phase = policy.event_phase(progress=progress, sport=sport)
        stop = policy.loss_trigger(
            allocated_cost_usd=q * basis, qty=q, bid=bid,
            bid_size=bid_size, fee_fn=self.fee_fn,
            trigger_fraction=pp.get("trigger_fraction"))
        target = policy.pair_limit(basis, q, fee_fn=self.fee_fn,
                                   target_cost=pp.get("target_cost"))
        rec.update(phase=phase, loss_trigger=stop, pair_target=target)
        if phase["loss_exit_available"] and stop["fired"]:
            action, price, qty = "DIRECT_EXIT", bid, stop["sellable_qty"]
        elif target["feasible"]:
            action, price, qty = "POST_COMPLEMENT", target["limit"], q
        else:
            rec["operating_state"] = "HOLD_NO_FEASIBLE_PAIR"
            self.decisions.append(rec)
            return rec
        side = "SELL" if action == "DIRECT_EXIT" else "BUY"
        existing = self.open_orders()
        # Keep queue priority; do not cancel/repost an unchanged intent.
        if (len(existing) == 1 and existing[0].state != dk.CANCEL_PENDING
                and existing[0].side == side
                and abs(existing[0].limit_price - price) < 1e-12
                and abs(existing[0].remaining - qty) < 1e-9):
            rec.update(operating_state="ORDER_WORKING",
                       order_id=existing[0].order_id)
        else:
            placed = self.place(action, at=at, price=price, qty=qty,
                                decision_id=decision_id,
                                liquidity="MAKER" if side == "BUY" else "TAKER")
            rec.update(placement=placed, selected_action=action,
                       acted=placed["placed"] is not None,
                       operating_state=placed["refused"] or "ORDER_WORKING")
        self.decisions.append(rec)
        return rec

    # ── THE CONNECTION THAT DID NOT EXIST ────────────────────────────
    #
    # This module imported `bettor_mgmt_select` and never called it. The
    # lifecycle was driven only by test code handing it actions, so
    # "selector -> order manager" was an unused import rather than a
    # connection. `decide_and_act` is that connection.
    def decide_and_act(self, *, at, last_price=None, bid=None,
                       bid_size=None, complement_ask=None,
                       complement_ask_size=None, seconds_open=None,
                       decision_id=None) -> dict:
        """WHETHER to close, then HOW, then place it. In that order.

        The two questions are separate and the order matters: a priceable
        exit existing is not a reason to take it, so the trigger runs
        first and HOLD stands until it fires.
        """
        q = self.residual()
        if q <= 1e-9:
            return {"at": at, "operating_state": "NO_RESIDUAL",
                    "acted": False,
                    "why": "no unpaired exposure on the seeded leg"}
        basis_per = (self.pf._leg(self.condition_id, self.leg)["cost"]
                     / max(self.held(self.leg), 1e-12))

        # 1. WHETHER.
        trig = sel.exposure_trigger(basis_per_contract=basis_per,
                                    last_price=last_price,
                                    seconds_open=seconds_open)
        rec = {"at": at, "residual_qty": q,
               "basis_per_contract": basis_per,
               "trigger": trig, "operating_state": trig["operating_state"]}
        if not trig["fired"]:
            rec.update(acted=False, method=None,
                       why=("HOLD remains the operating state: %s"
                            % trig["reason"]))
            self.decisions.append(rec)
            return rec

        # 2. HOW -- only now, and over the residual quantity alone.
        rank = sel.rank_priced_actions(
            q, basis_per, bid=bid, bid_size=bid_size,
            complement_ask=complement_ask,
            complement_ask_size=complement_ask_size, fee_fn=self.fee_fn)
        rec["method"] = rank
        if not rank.get("selected"):
            rec.update(acted=False,
                       why=("the trigger fired but no method is "
                            "executable: %s" % rank["selection_reason"]))
            self.decisions.append(rec)
            return rec

        # 3. PLACE. The order is sized to the residual, not the leg.
        chosen = rank["priced_actions"][0]
        px = (bid if chosen["action"] == "DIRECT_EXIT" else complement_ask)
        placed = self.place(chosen["action"], at=at, price=px,
                            qty=chosen["qty"],
                            decision_id=decision_id or "d-%s" % int(at))
        rec.update(acted=placed.get("placed") is not None, placement=placed,
                   why=rank["selection_reason"])
        self.decisions.append(rec)
        return rec

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
                 and o.placed_at < float(at) < o.expires_at
                 and o.outcome_index == int(outcome_index)]
        cands.sort(key=lambda o: o.placed_at)
        for o in cands:
            crosses = (price <= o.limit_price if o.side == "BUY"
                       else price >= o.limit_price)
            if not crosses or o.remaining <= 1e-9:
                continue
            # A REPORTED FILL IS NEVER CLIPPED. THIS WAS A BUG I SHIPPED.
            #
            # The previous version computed `want = min(o.remaining, cap)`
            # and took only that much from the print -- silently trimming
            # a fill to fit the inventory it expected. That is not how an
            # execution works. RESIZING AN ORDER BEFORE SUBMISSION is
            # valid and happens in `place()`. SILENTLY CLIPPING A
            # REPORTED FILL hides a real discrepancy: the venue filled
            # what it filled, and a book that quietly records less than
            # was executed is wrong in the direction that looks tidy.
            #
            # So the whole execution is taken and recorded. If it exceeds
            # what remaining inventory supports, the surplus is reported
            # as an INVENTORY DISCREPANCY on the fill and in the state,
            # rather than deleted.
            got = self.cons.take(evidence_id, o.remaining)
            if got <= 1e-9:
                continue
            fee = float(self.fee_fn(qty=got, price=price))
            raced = o.state == dk.CANCEL_PENDING
            # THE DISCREPANCY, MEASURED BEFORE THE BOOK MOVES. A SELL
            # cannot exceed the held quantity and a completion cannot
            # exceed the unpaired remainder; a fill that does is recorded
            # in full and the surplus is surfaced.
            cap = (self.held(self.leg) if o.side == "SELL"
                   else self.residual())
            surplus = max(0.0, got - cap)
            if surplus > 1e-9:
                self.discrepancies.append({
                    "at": at, "order_id": o.order_id,
                    "evidence_id": evidence_id,
                    "filled_qty": got, "inventory_cap": cap,
                    "surplus_qty": surplus, "side": o.side,
                    "why": ("the execution exceeded what remaining "
                            "inventory supported. It is recorded IN FULL "
                            "rather than clipped, and this is the "
                            "resulting discrepancy")})
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
                            "inventory_surplus_qty": round(surplus, 6),
                            "was_clipped": False,
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
                        "inventory_surplus_qty": surplus,
                        "remaining_after": o.remaining,
                        "residual_after": self.residual()})
        return out

    # ── settlement, reachable only from here ────────────────────────
    def settle_at_observed_payout(self, payouts: dict, at) -> dict:
        """SEPARATE ENTRY POINT. Nothing in the decision path calls it."""
        for order in self.open_orders():
            order.transition(dk.EXPIRED, at, "observed settlement ends modelled order")
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
            "inventory_discrepancies": list(self.discrepancies),
            "inventory_discrepancy_qty": round(
                sum(d["surplus_qty"] for d in self.discrepancies), 6),
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
