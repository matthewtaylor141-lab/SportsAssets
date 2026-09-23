"""THE SHADOW DESK. The link that was missing, built as one pure engine.

WHAT THE AUDIT FOUND (run 35868260156, 2026-09-23T13:36:43Z). Everything
up to the decision is live and continuous:

    bettor_state_observations   newest 37 s old
    shadow_decisions            25,995 rows, newest 1 s old

and everything after it is empty -- `shadow_executions` 0,
`shadow_positions` 0, no order table at all. `shadow_store
.record_execution()` and `record_position()` are written, correct, and
have ZERO CALLERS in the tree. This module is what calls them, and it
is pure so the same code runs live and in replay.

────────────────────────────────────────────────────────────────────
THE EXECUTION MODEL, AND THE THING IT REFUSES TO DO.

    A QUOTE TOUCHING OUR PRICE IS NOT A FILL.

A resting BUY at limit L fills only when a subsequent OBSERVED TRADE
prints at or below L in the same (condition, outcome). A print is
evidence that somebody actually crossed; a quote is evidence that
somebody was willing to, which is a different fact and the one that
flatters a backtest.

    PRINT_THROUGH_WITH_QUEUE_SHARE_V1

and the consumption ledger is what stops one print filling three
hypothetical orders. Every print has a finite size; each allocation
decrements it; an order can only take what is left. Without that, a
busy market fills an unlimited book and the demonstration measures
nothing.

QUEUE_SHARE IS AN ASSUMPTION AND IS LABELLED ONE EVERYWHERE. We were
not in the queue. `bettor_p_fill` holds P_FILL NOT_IDENTIFIED and this
module does not quietly identify it: `queue_share` is a declared
parameter, swept in the capital scenarios, and every result carries the
value it was produced under. It is never reported as a measured
execution probability.

────────────────────────────────────────────────────────────────────
MERGE IS NOT AVAILABLE AND IS NOT SIMULATED.

`bettor_merge.py`: `RETAIL_NATIVE_MERGE_AVAILABLE = NO`, and the venue
already nets -- buying NO at 0.51 IS selling YES at 0.49. So there is
no MERGE action here. Completing a pair is `COMPLETE_PAIR`, an ordinary
BUY of the complement, priced and costed as one, and the capital effect
is the venue's netting rather than a merge call. PMUS institutional
merge is NOT_IDENTIFIED and nothing here assumes it.

────────────────────────────────────────────────────────────────────
POLICY MATURITY, STATED PER RULE RATHER THAN AS ONE WORD.

    entry band          HAND_WRITTEN, Ferrari-inspired. Taken from the
                        decomposition's observation that Ferrari's
                        residual sits at a mean 0.4460 and that cheap
                        legs carry a measured deficit.
    pair completion     HAND_WRITTEN. Complete when the complement is
                        obtainable below (1 - entry), which is the
                        arithmetic of a pair that clears.
    residual exit       LEARNED. The frozen isotonic price->settlement
                        curve, fitted on TRAIN markets only, digest
                        recorded. This is the one learned rule.
    fill probability    ASSUMED. queue_share. Not learned, not
                        measured, not claimed.

Calling the whole thing "the Ferrari model" would be false. It is a
FERRARI-INSPIRED DEVELOPMENT POLICY with one learned component.
"""
from __future__ import annotations

import hashlib
import json
import math

VERSION = "BETTOR_DESK_V1"

# ── WHETHER FEES WERE EVER CHARGED ───────────────────────────────────
#
# `NO_FEE_SCHEDULE` is a GROSS book. Its realized P&L is a before-costs
# figure and must never be presented as a net one. It exists because a
# desk can legitimately be run without a schedule -- an engine test, a
# mechanism demonstration -- but the resulting numbers have to carry
# the label rather than look identical to a costed book.
FEE_BASIS_NONE = "NO_FEE_SCHEDULE_GROSS"
FEE_BASIS_APPLIED = "FEE_SCHEDULE_APPLIED"

# ── WHAT THE REPLAY TAPE ACTUALLY IS ─────────────────────────────────
#
# CORRECTED 2026-09-23 AFTER THE FIRST HANDOFF. I described the replay
# events as "prints" and as "recorded prints over 1,006 conditions",
# which reads as market-wide transactions. They are not.
#
# `trades` is keyed on `whale_id` and every row is ONE TRACKED
# ACCOUNT'S OWN EXECUTION (001_init.sql:56). The replay tape is
# ferrariChampions2026's fills and nothing else. It is a single
# participant's executed flow, not the market tape, and it therefore
# says nothing about the depth that stood beside Ferrari's orders or
# about what a DIFFERENT order at the same price would have met.
EVENT_CLASS = "SINGLE_ACCOUNT_EXECUTIONS"
EVENT_CLASS_NOTE = (
    "every replay event is one tracked account's own executed fill, "
    "read from `trades` which is keyed on whale_id. It is NOT the "
    "market tape and NOT a record of resting liquidity. Ferrari's "
    "execution at a price is not evidence that OUR order at that price "
    "would have filled -- it is evidence that Ferrari's did.")

# ── WHICH VENUE, AND WHICH FEE SCHEDULE ──────────────────────────────
#
# ALSO CORRECTED. The fills come from the on-chain listener --
# `ingestion/chain.py` uses polygon_ws_url, polygon_http_url,
# PM_EXCHANGE_V3_ADDRESSES and stamps ts_provenance
# "polygon_block_timestamp". That is Polymarket on Polygon, the GLOBAL
# venue. The fee arithmetic applied to it is the published PMUS
# schedule. Those are two different venues.
SOURCE_VENUE = "POLYMARKET_GLOBAL_POLYGON"
FEE_SCHEDULE_VENUE = "PMUS"
VENUE_TRANSFER = "TRANSFERRED_SCENARIO"
VENUE_TRANSFER_NOTE = (
    "the tape is GLOBAL-venue (Polygon) execution data and the fees and "
    "position mechanics applied to it are PMUS's. This is a TRANSFERRED "
    "SCENARIO -- what this policy would have cost under PMUS economics "
    "against global-venue flow -- and it is NOT same-venue execution "
    "evidence. Liquidity, tick structure, participant mix and fee "
    "incidence all differ between the two.")
FILL_MODEL = "PRINT_THROUGH_WITH_QUEUE_SHARE_V1"

POLICY_KEY = "ferrari_inspired_dev"
POLICY_VERSION = "FERRARI_INSPIRED_DEV_V1"

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── order states ─────────────────────────────────────────────────────
PROPOSED = "PROPOSED"
RESTING = "RESTING"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
FILLED = "FILLED"
CANCEL_PENDING = "CANCEL_PENDING"
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"
REJECTED = "REJECTED"
OPEN_STATES = (RESTING, PARTIALLY_FILLED, CANCEL_PENDING)
TERMINAL_STATES = (FILLED, CANCELLED, EXPIRED, REJECTED)

# ── actions ──────────────────────────────────────────────────────────
ENTER = "ENTER"
COMPLETE_PAIR = "COMPLETE_PAIR"
REDUCE = "REDUCE"
EXIT = "EXIT"
HOLD = "HOLD"
AMEND = "AMEND"
CANCEL = "CANCEL"
NO_TRADE = "NO_TRADE"

POLICY_MATURITY = {
    "entry_band": "HAND_WRITTEN",
    "pair_completion": "HAND_WRITTEN",
    "residual_exit": "LEARNED",
    "fill_probability": "ASSUMED",
    "note": "one learned rule. Calling this 'the Ferrari model' would "
            "be false; it is a Ferrari-INSPIRED development policy.",
}


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()[:16]


class Limits:
    """Capital, concentration and loss limits, enforced IN the decision
    path rather than checked afterwards.

    A limit that is validated after an order is written is a report, not
    a limit. Every one of these is consulted by `Desk.decide` before an
    order exists, and a refusal is a recorded decision with a reason.
    """

    def __init__(self, *, starting_cash=100000.0, max_position_usd=2500.0,
                 max_condition_usd=5000.0, max_committed_usd=60000.0,
                 max_open_orders=40, loss_limit_usd=10000.0,
                 max_order_usd=1000.0):
        self.starting_cash = float(starting_cash)
        self.max_position_usd = float(max_position_usd)
        self.max_condition_usd = float(max_condition_usd)
        self.max_committed_usd = float(max_committed_usd)
        self.max_open_orders = int(max_open_orders)
        self.loss_limit_usd = float(loss_limit_usd)
        self.max_order_usd = float(max_order_usd)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class Order:
    __slots__ = ("order_id", "condition_id", "outcome_index", "side",
                 "intent", "limit_price", "qty", "filled_qty", "fees",
                 "state", "state_reason", "placed_at", "expires_at",
                 "terminal_at", "events", "decision_id", "notional",
                 "fills")

    def __init__(self, order_id, *, condition_id, outcome_index, side,
                 intent, limit_price, qty, placed_at, expires_at,
                 decision_id):
        self.order_id = order_id
        self.condition_id = condition_id
        self.outcome_index = int(outcome_index)
        self.side = side
        self.intent = intent
        self.limit_price = float(limit_price)
        self.qty = float(qty)
        self.filled_qty = 0.0
        self.fees = 0.0
        self.state = PROPOSED
        self.state_reason = "created"
        self.placed_at = float(placed_at)
        self.expires_at = float(expires_at)
        self.terminal_at = None
        self.decision_id = decision_id
        self.notional = float(qty) * float(limit_price)
        self.events = []
        self.fills = []

    @property
    def remaining(self) -> float:
        return max(0.0, self.qty - self.filled_qty)

    @property
    def avg_fill_price(self):
        if self.filled_qty <= 0:
            return None
        return sum(f["qty"] * f["price"] for f in self.fills) / self.filled_qty

    def transition(self, to, at, reason, **detail):
        self.events.append({"at": float(at), "from": self.state, "to": to,
                            "reason": reason, "detail": detail})
        self.state = to
        self.state_reason = reason
        if to in TERMINAL_STATES:
            self.terminal_at = float(at)

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id, "condition_id": self.condition_id,
            "outcome_index": self.outcome_index, "side": self.side,
            "intent": self.intent, "limit_price": self.limit_price,
            "qty": self.qty, "filled_qty": self.filled_qty,
            "avg_fill_price": self.avg_fill_price, "fees_usd": self.fees,
            "state": self.state, "state_reason": self.state_reason,
            "placed_at": self.placed_at, "expires_at": self.expires_at,
            "terminal_at": self.terminal_at,
            "decision_id": self.decision_id,
            "notional_committed": self.notional,
            "events": self.events, "fills": self.fills,
        }


class Consumption:
    """One print, one finite size, allocated once.

    THE DEFECT THIS EXISTS TO PREVENT. Without it a single printed trade
    fills every resting order whose price it crosses, and a
    demonstration with a hundred orders looks a hundred times better
    than one with one. The ledger is keyed on the print's own id, so a
    print re-delivered by a second ingestion lane cannot be spent twice
    either.
    """

    def __init__(self):
        self.available = {}
        self.consumed = {}

    def offer(self, evidence_id, qty):
        if evidence_id not in self.available:
            self.available[evidence_id] = float(qty)
            self.consumed[evidence_id] = 0.0

    def take(self, evidence_id, want) -> float:
        left = self.available.get(evidence_id, 0.0) - self.consumed.get(
            evidence_id, 0.0)
        got = max(0.0, min(float(want), left))
        self.consumed[evidence_id] = self.consumed.get(evidence_id, 0.0) + got
        return got

    def remaining(self, evidence_id) -> float:
        return max(0.0, self.available.get(evidence_id, 0.0)
                   - self.consumed.get(evidence_id, 0.0))

    def to_dict(self) -> dict:
        return {"prints": len(self.available),
                "offered_qty": round(sum(self.available.values()), 4),
                "consumed_qty": round(sum(self.consumed.values()), 4),
                "rule": "one print, one finite size, allocated once; "
                        "keyed on the print id so a re-delivery by a "
                        "second lane cannot be spent twice"}


class Portfolio:
    """Cash, per-LEG inventory, cost basis, fees and realised P&L.

    THE TWO LEGS ARE NEVER NETTED. `bettor_inventory` records why: a
    pair is a claim the identity layer makes, not an arithmetic fact,
    and netting YES against NO is the defect that cost real money on the
    live mirror. Inventory here is keyed (condition, outcome).
    """

    def __init__(self, cash):
        self.starting_cash = float(cash)
        self.cash = float(cash)
        self.legs = {}
        self.realized = 0.0
        self.fees = 0.0

    def _leg(self, cond, oi):
        return self.legs.setdefault((cond, int(oi)), {
            "qty": 0.0, "cost": 0.0, "realized": 0.0, "fees": 0.0,
            "opened_at": None, "settled": False, "payout": None})

    def buy(self, cond, oi, qty, price, fee, at):
        """An ENTRY FEE IS EXPENSED IMMEDIATELY, not left in limbo.

        THE DEFECT THIS FIXES, found by the invariant on the first real
        replay (drift +301.72 on a $100k book). A buy fee left cash
        without entering either the cost basis or realised P&L, so it
        escaped the identity entirely -- and because the PMUS maker
        theta is a REBATE, the leak ran in our favour. An accounting
        error that flatters the book is the one worth catching.

        Expensing it matches how `sell` and `settle` already treat
        fees, and it means the realised figure management reads
        includes the cost of getting in rather than only the cost of
        getting out.
        """
        leg = self._leg(cond, oi)
        self.cash -= qty * price + fee
        leg["qty"] += qty
        leg["cost"] += qty * price
        leg["fees"] += fee
        leg["realized"] -= fee
        self.fees += fee
        self.realized -= fee
        if leg["opened_at"] is None:
            leg["opened_at"] = float(at)

    def sell(self, cond, oi, qty, price, fee, at):
        """A sell realises against the AVERAGE cost of that leg."""
        leg = self._leg(cond, oi)
        if qty > leg["qty"] + 1e-9:
            raise ValueError("cannot sell %s of %s held on (%s, %s)"
                             % (qty, leg["qty"], cond, oi))
        avg = (leg["cost"] / leg["qty"]) if leg["qty"] > 0 else 0.0
        pnl = qty * (price - avg) - fee
        self.cash += qty * price - fee
        leg["qty"] -= qty
        leg["cost"] -= qty * avg
        leg["realized"] += pnl
        leg["fees"] += fee
        self.realized += pnl
        self.fees += fee
        return pnl

    def settle(self, cond, oi, payout, at):
        """Settlement pays `payout` per share and closes the leg."""
        leg = self._leg(cond, oi)
        if leg["settled"] or leg["qty"] <= 0:
            return 0.0
        avg = leg["cost"] / leg["qty"]
        pnl = leg["qty"] * (payout - avg)
        self.cash += leg["qty"] * payout
        self.realized += pnl
        leg["realized"] += pnl
        leg["qty"] = 0.0
        leg["cost"] = 0.0
        leg["settled"] = True
        leg["payout"] = float(payout)
        return pnl

    def inventory_cost(self) -> float:
        return sum(l["cost"] for l in self.legs.values())

    def open_legs(self) -> list:
        return [(k, v) for k, v in self.legs.items() if v["qty"] > 1e-9]

    def mark(self, marks: dict):
        """Two DIFFERENT numbers, reported separately and never summed.

        INVENTORY MARK is the last observed price: what the book says
        the position is worth. EXECUTABLE LIQUIDATION is what we could
        actually get out at, which needs depth we usually do not have --
        and when we do not have it the answer is NOT_IDENTIFIED, not the
        mark used twice.
        """
        mark_v, exe_v, no_depth = 0.0, 0.0, 0
        for (cond, oi), leg in self.open_legs():
            m = marks.get((cond, oi))
            if m is None:
                no_depth += 1
                continue
            mark_v += leg["qty"] * float(m.get("price", 0.0))
            d = m.get("executable_qty")
            if d is None:
                no_depth += 1
            else:
                fillable = min(leg["qty"], float(d))
                exe_v += fillable * float(m.get("bid", m.get("price", 0.0)))
        return {
            "inventory_mark_usd": round(mark_v, 2),
            "executable_liquidation_usd": (round(exe_v, 2) if no_depth == 0
                                           else NOT_IDENTIFIED),
            "legs_without_depth": no_depth,
            "why": ("an executable liquidation estimate needs depth at a "
                    "price. Where depth is unknown the answer is "
                    "NOT_IDENTIFIED -- not the mark quoted a second time "
                    "under a different name."),
        }

    def to_dict(self) -> dict:
        return {
            "starting_cash": round(self.starting_cash, 2),
            "cash": round(self.cash, 2),
            "realized_pnl_usd": round(self.realized, 2),
            "fees_usd": round(self.fees, 2),
            "inventory_cost_usd": round(self.inventory_cost(), 2),
            "open_legs": len(self.open_legs()),
            "legs": {"%s:%d" % (c, o): {
                "qty": round(v["qty"], 4), "cost": round(v["cost"], 2),
                "avg_cost": (round(v["cost"] / v["qty"], 6)
                             if v["qty"] > 1e-9 else None),
                "realized": round(v["realized"], 2),
                "fees": round(v["fees"], 2),
                "settled": v["settled"], "payout": v["payout"],
                "opened_at": v["opened_at"],
            } for (c, o), v in self.legs.items()},
        }

    def invariant(self) -> dict:
        """cash + inventory cost + realised must reconstruct the start.

        Checked every cycle and STORED beside the numbers it checked, so
        a displayed total can be traced to the snapshot that produced it
        rather than re-derived by whoever is reading.
        """
        lhs = self.cash + self.inventory_cost() - self.realized
        drift = lhs - self.starting_cash
        return {
            "identity": "cash + inventory_cost - realized == starting_cash",
            "lhs": round(lhs, 6),
            "starting_cash": round(self.starting_cash, 6),
            "drift": round(drift, 6),
            "ok": abs(drift) < 0.01,
            # WHAT THIS CHECK DOES NOT COVER, said here rather than
            # left for a reader to discover. It is a CONSISTENCY check.
            # It passed for 37 minutes on a live book whose fee
            # schedule had never been supplied, because the identity
            # holds equally well when no fee was ever charged.
            "proves": "internal consistency of cash, inventory and "
                      "realized against the starting balance",
            "does_not_prove": [
                "that costs were charged -- see fee_basis on the "
                "snapshot; a gross book reconciles exactly as well as "
                "a net one",
                "that the valuations are right",
                "that the fill assumptions are right",
            ],
        }


class Policy:
    """The Ferrari-inspired development policy. One learned rule.

    ENTRY is hand-written from the decomposition, not fitted. Ferrari's
    residual sits at a mean price of 0.4460 and the fair-value work
    measured a ~5 cent deficit on legs priced 0.05-0.40, so this policy
    does NOT chase the cheap tail Ferrari kept buying. That is the one
    place it deliberately departs from the case study.

    PAIR COMPLETION is arithmetic: complete when the complement can be
    bought below (1 - our entry), because one YES and one NO pay exactly
    $1.00 and anything above that locks a loss. The decomposition found
    Ferrari locking one on 37% of its completed pairs.

    THE RESIDUAL RULE IS THE LEARNED ONE. `curve` is the frozen isotonic
    price->settlement map, fitted on TRAIN markets only, and it is
    consulted -- never refitted -- here. Its digest travels on every
    decision so a result can be tied to the exact artifact.
    """

    def __init__(self, *, curve=None, curve_sha=None,
                 entry_lo=0.40, entry_hi=0.65, order_usd=250.0,
                 expiry_s=900.0, min_clear=0.005, exit_edge=0.02):
        self.curve = curve
        self.curve_sha = curve_sha or NOT_IDENTIFIED
        self.entry_lo = float(entry_lo)
        self.entry_hi = float(entry_hi)
        self.order_usd = float(order_usd)
        self.expiry_s = float(expiry_s)
        self.min_clear = float(min_clear)
        self.exit_edge = float(exit_edge)

    def ev_hold(self, price):
        """E[payout] for a leg marked at `price`.

        The learned curve when there is one; otherwise the price itself,
        and the caller is told WHICH -- because "the market is fair" is
        a real answer and must not be mistaken for a model's answer.
        """
        if self.curve is None:
            return float(price), "IDENTITY_PRICE_IS_PROBABILITY"
        return float(self.curve.predict(float(price))), "LEARNED_ISOTONIC"

    def describe(self) -> dict:
        return {
            "policy_key": POLICY_KEY,
            "policy_version": POLICY_VERSION,
            "maturity": POLICY_MATURITY,
            "entry_band": [self.entry_lo, self.entry_hi],
            "order_usd": self.order_usd,
            "expiry_s": self.expiry_s,
            "min_clear": self.min_clear,
            "exit_edge": self.exit_edge,
            "learned_artifact_sha": self.curve_sha,
            "departs_from_ferrari": (
                "Ferrari's residual sits at a mean 0.4460 and the "
                "fair-value work measured a ~5 cent deficit on legs "
                "priced 0.05-0.40. This policy does not buy that tail. "
                "It is the one deliberate departure from the case study."),
        }


class Desk:
    """One engine, run identically live and in replay.

    `step(event)` consumes market evidence in time order and returns the
    decisions it made. Nothing here reads a clock, a database or a
    venue: the caller supplies the evidence and the instant, which is
    what lets the replay be the same code rather than a second
    implementation that drifts.
    """

    def __init__(self, *, policy, limits, fee_fn=None, queue_share=0.25,
                 desk_id="desk1"):
        self.policy = policy
        self.limits = limits
        self.pf = Portfolio(limits.starting_cash)
        self.cons = Consumption()
        self.orders = {}
        self.decisions = []
        self.queue_share = float(queue_share)
        self.desk_id = desk_id
        self._n = 0
        self.halted = False
        self.halt_reason = None
        self.last_price = {}
        # A ZERO-FEE BOOK MUST SAY SO. This was `fee_fn or (lambda ...:
        # 0.0)` -- an anonymous silent fallback -- and the live loop was
        # constructed without a schedule, so every live fill was booked
        # FREE for 37 minutes while the ledger reported `invariant_ok`.
        #
        # It reported ok because the identity
        #     cash + inventory_cost - realized == starting_cash
        # holds whether or not fees were ever charged. The invariant is
        # a CONSISTENCY check, not a COMPLETENESS one, and it cannot
        # detect an accounting input that was never supplied. So the
        # basis travels with the book instead, and every snapshot
        # carries it: a reader can no longer mistake a fee-free total
        # for a net one.
        self.fee_basis = FEE_BASIS_NONE if fee_fn is None else FEE_BASIS_APPLIED
        self.fee_fn = fee_fn or (lambda qty, px, maker: 0.0)

    # ── bookkeeping ──────────────────────────────────────────────────
    def _next_id(self, kind):
        self._n += 1
        return "%s-%s-%06d" % (self.desk_id, kind, self._n)

    def _record(self, at, action, reason, **kw):
        d = {"desk_decision_id": self._next_id("D"), "at": float(at),
             "action": action, "reason": reason,
             "policy_version": POLICY_VERSION,
             "learned_artifact_sha": self.policy.curve_sha,
             "fill_model": FILL_MODEL,
             "queue_share_ASSUMED": self.queue_share}
        d.update(kw)
        d["inputs_sha"] = _sha({k: d.get(k) for k in
                                ("condition_id", "outcome_index", "at",
                                 "price", "ev", "inventory")})
        self.decisions.append(d)
        return d

    def open_orders(self):
        return [o for o in self.orders.values() if o.state in OPEN_STATES]

    def committed_usd(self) -> float:
        return sum(o.remaining * o.limit_price for o in self.open_orders())

    # ── risk, consulted BEFORE an order exists ───────────────────────
    def _risk(self, cond, oi, qty, price):
        L, checks = self.limits, {}
        notional = qty * price
        leg = self.pf.legs.get((cond, int(oi)), {"cost": 0.0})
        cond_cost = sum(v["cost"] for (c, _), v in self.pf.legs.items()
                        if c == cond)
        checks["order_usd"] = (notional <= L.max_order_usd, notional,
                               L.max_order_usd)
        checks["position_usd"] = (leg["cost"] + notional <= L.max_position_usd,
                                  leg["cost"] + notional, L.max_position_usd)
        checks["condition_usd"] = (cond_cost + notional <= L.max_condition_usd,
                                   cond_cost + notional, L.max_condition_usd)
        checks["committed_usd"] = (
            self.committed_usd() + notional <= L.max_committed_usd,
            self.committed_usd() + notional, L.max_committed_usd)
        checks["open_orders"] = (len(self.open_orders()) < L.max_open_orders,
                                 len(self.open_orders()), L.max_open_orders)
        checks["cash"] = (notional <= self.pf.cash, notional, self.pf.cash)
        checks["loss_limit"] = (self.pf.realized > -L.loss_limit_usd,
                                self.pf.realized, -L.loss_limit_usd)
        failed = [k for k, v in checks.items() if not v[0]]
        return {"checks": {k: {"ok": v[0], "value": round(float(v[1]), 2),
                               "limit": round(float(v[2]), 2)}
                           for k, v in checks.items()},
                "failed": failed, "ok": not failed}

    def _place(self, at, cond, oi, side, intent, price, qty, decision_id):
        o = Order(self._next_id("O"), condition_id=cond, outcome_index=oi,
                  side=side, intent=intent, limit_price=price, qty=qty,
                  placed_at=at, expires_at=at + self.policy.expiry_s,
                  decision_id=decision_id)
        o.transition(RESTING, at, "placed")
        self.orders[o.order_id] = o
        return o

    # ── the execution model ──────────────────────────────────────────
    def _match(self, print_evt):
        """Allocate one observed PRINT against resting orders.

        A BUY fills when the print is at or below its limit, a SELL when
        at or above. Price improvement goes to us at the PRINT'S price,
        not our limit, because that is the price that actually traded.

        Orders are served OLDEST FIRST. That is an assumption about
        queue priority and it is named as one; we were not in the queue
        and no venue told us our position.
        """
        at = float(print_evt["at"])
        cond = print_evt["condition_id"]
        oi = int(print_evt["outcome_index"])
        px = float(print_evt["price"])
        eid = str(print_evt["evidence_id"])
        self.cons.offer(eid, float(print_evt["size"]) * self.queue_share)
        self.last_price[(cond, oi)] = px

        out = []
        cands = [o for o in self.open_orders()
                 if o.condition_id == cond and o.outcome_index == oi
                 and o.state != CANCEL_PENDING]
        cands.sort(key=lambda o: o.placed_at)
        for o in cands:
            crosses = (px <= o.limit_price if o.side == "BUY"
                       else px >= o.limit_price)
            if not crosses or o.remaining <= 1e-9:
                continue
            got = self.cons.take(eid, o.remaining)
            if got <= 1e-9:
                continue
            fee = float(self.fee_fn(got, px, True))
            if o.side == "BUY":
                if got * px + fee > self.pf.cash + 1e-9:
                    o.transition(REJECTED, at, "insufficient simulated cash")
                    continue
                self.pf.buy(cond, oi, got, px, fee, at)
            else:
                self.pf.sell(cond, oi, got, px, fee, at)
            o.filled_qty += got
            o.fees += fee
            o.fills.append({"at": at, "qty": round(got, 6), "price": px,
                            "fee_usd": round(fee, 6), "liquidity": "MAKER",
                            "evidence_id": eid, "evidence_kind": "PRINT",
                            "exec_model": FILL_MODEL})
            o.transition(FILLED if o.remaining <= 1e-9 else PARTIALLY_FILLED,
                         at, "print through at %.4f" % px,
                         qty=round(got, 6), evidence_id=eid)
            out.append({"order_id": o.order_id, "qty": got, "price": px,
                        "evidence_id": eid})
        return out

    def _expire(self, at):
        for o in self.open_orders():
            if at >= o.expires_at:
                o.transition(EXPIRED, at, "expiry reached with %.4f unfilled"
                             % o.remaining)

    # ── the decision cycle ───────────────────────────────────────────
    def step(self, evt) -> list:
        """One market event. Returns the decisions taken at it."""
        at = float(evt["at"])
        made = []
        self._expire(at)
        fills = self._match(evt) if evt.get("kind") == "PRINT" else []
        if fills:
            made.append(self._record(at, "FILLED", "print-through fills",
                                     condition_id=evt["condition_id"],
                                     outcome_index=evt["outcome_index"],
                                     fills=fills))
        if self.halted:
            return made

        cond = evt["condition_id"]
        oi = int(evt["outcome_index"])
        px = float(evt["price"])
        comp = 1 - oi
        held = self.pf.legs.get((cond, oi), {"qty": 0.0, "cost": 0.0})
        held_comp = self.pf.legs.get((cond, comp), {"qty": 0.0, "cost": 0.0})
        inv = {"leg_qty": round(held["qty"], 4),
               "leg_cost": round(held["cost"], 2),
               "complement_qty": round(held_comp["qty"], 4),
               "avg_cost": (round(held["cost"] / held["qty"], 6)
                            if held["qty"] > 1e-9 else None)}
        open_here = [o for o in self.open_orders()
                     if o.condition_id == cond]

        # 1. COMPLETE THE PAIR.
        #
        # THE DECISION IS ABOUT THE CONDITION, NOT ABOUT WHICHEVER LEG
        # HAPPENED TO TICK. An earlier version keyed this off the
        # event's own leg, so a print on the leg we did NOT hold fell
        # straight through to the entry rule and opened a second
        # position in a market we were already trying to pair. The long
        # leg is whichever one carries more inventory, and the leg we
        # need is the other one, regardless of which one this event is.
        long_oi = 0 if (self.pf.legs.get((cond, 0), {"qty": 0.0})["qty"]
                        >= self.pf.legs.get((cond, 1), {"qty": 0.0})["qty"]
                        ) else 1
        short_oi = 1 - long_oi
        long_leg = self.pf.legs.get((cond, long_oi),
                                    {"qty": 0.0, "cost": 0.0})
        short_leg = self.pf.legs.get((cond, short_oi),
                                     {"qty": 0.0, "cost": 0.0})

        if long_leg["qty"] > 1e-9 and short_leg["qty"] < long_leg["qty"] - 1e-9:
            need = long_leg["qty"] - short_leg["qty"]
            avg = long_leg["cost"] / long_leg["qty"]
            clears_below = 1.0 - avg - self.policy.min_clear
            comp_px = (px if oi == short_oi
                       else self.last_price.get((cond, short_oi)))
            if comp_px is not None and comp_px <= clears_below:
                qty = min(need, self.policy.order_usd / max(comp_px, 1e-6))
                already = [o for o in self.open_orders()
                           if o.condition_id == cond
                           and o.outcome_index == short_oi]
                if not already:
                    r = self._risk(cond, short_oi, qty, comp_px)
                    d = self._record(
                        at, COMPLETE_PAIR if r["ok"] else NO_TRADE,
                        ("complement at %.4f clears against our %.4f basis"
                         % (comp_px, avg)) if r["ok"]
                        else "pair completion blocked: %s" % ",".join(
                            r["failed"]),
                        condition_id=cond, outcome_index=short_oi,
                        price=comp_px,
                        proposed_qty=round(qty, 4), inventory=inv, risk=r,
                        ev={"pair_price": round(avg + comp_px, 6),
                            "gross_clear_per_share":
                                round(1.0 - (avg + comp_px), 6),
                            "p_fill": NOT_IDENTIFIED,
                            "merge": "NOT_AVAILABLE_ON_PMUS_RETAIL: the "
                                     "venue nets, so this is an ordinary "
                                     "BUY of the complement"},
                        alternatives=["HOLD_UNPAIRED", "EXIT_THE_LEG"])
                    made.append(d)
                    if r["ok"]:
                        self._place(at, cond, short_oi, "BUY", COMPLETE_PAIR,
                                    comp_px, qty, d["desk_decision_id"])
                    return made

        # 2. THE RESIDUAL DECISION -- the learned rule. Evaluated on the
        #    leg this event prices, when we hold any of it.
        if held["qty"] > 1e-9:
            ev_hold, basis = self.policy.ev_hold(px)
            fee = float(self.fee_fn(held["qty"], px, False))
            net_exit = px - fee / max(held["qty"], 1e-9)
            edge = net_exit - ev_hold
            act = EXIT if edge > self.policy.exit_edge else HOLD
            r = self._risk(cond, oi, 0.0, px) if act == EXIT else {
                "ok": True, "failed": [], "checks": {}}
            d = self._record(
                at, act,
                ("net exit %.4f beats E[payout] %.4f by %.4f"
                 % (net_exit, ev_hold, edge)) if act == EXIT else
                ("holding: E[payout] %.4f vs net exit %.4f (%s)"
                 % (ev_hold, net_exit, basis)),
                condition_id=cond, outcome_index=oi, price=px,
                inventory=inv, risk=r,
                ev={"ev_hold_per_share": round(ev_hold, 6),
                    "ev_hold_basis": basis,
                    "net_exit_per_share": round(net_exit, 6),
                    "edge_per_share": round(edge, 6),
                    "p_fill": NOT_IDENTIFIED,
                    "unknowns": ["P_FILL", "executable depth at our price"]},
                alternatives=[HOLD, EXIT, REDUCE, "WAIT_FOR_COMPLEMENT"])
            made.append(d)
            if act == EXIT and r["ok"] and not open_here:
                self._place(at, cond, oi, "SELL", EXIT, px, held["qty"],
                            d["desk_decision_id"])
            return made

        # 3. ENTRY. Only inside the band, only with nothing resting in
        #    this condition, and NEVER in a condition we already hold --
        #    acquiring the other leg of a market we are already in is
        #    pairing, priced by rule 1, not a fresh entry.
        if open_here:
            return made
        if long_leg["qty"] > 1e-9 or short_leg["qty"] > 1e-9:
            made.append(self._record(
                at, NO_TRADE,
                "already positioned in this condition; acquiring the other "
                "leg is pair completion, not entry",
                condition_id=cond, outcome_index=oi, price=px, inventory=inv,
                ev={"reason_code": "ALREADY_POSITIONED_IN_CONDITION"},
                alternatives=[COMPLETE_PAIR, HOLD, NO_TRADE]))
            return made
        if not (self.policy.entry_lo <= px <= self.policy.entry_hi):
            made.append(self._record(
                at, NO_TRADE,
                "price %.4f outside the entry band [%.2f, %.2f]"
                % (px, self.policy.entry_lo, self.policy.entry_hi),
                condition_id=cond, outcome_index=oi, price=px, inventory=inv,
                ev={"reason_code": "OUTSIDE_ENTRY_BAND"},
                alternatives=[ENTER, NO_TRADE]))
            return made

        qty = self.policy.order_usd / max(px, 1e-6)
        r = self._risk(cond, oi, qty, px)
        d = self._record(
            at, ENTER if r["ok"] else NO_TRADE,
            "entry inside the band at %.4f" % px if r["ok"]
            else "entry blocked: %s" % ",".join(r["failed"]),
            condition_id=cond, outcome_index=oi, price=px,
            proposed_qty=round(qty, 4), inventory=inv, risk=r,
            ev={"entry_price": px, "complement_must_clear_below":
                round(1.0 - px - self.policy.min_clear, 6),
                "p_fill": NOT_IDENTIFIED,
                "unknowns": ["P_FILL", "whether the complement will be "
                             "obtainable below the clearing price"]},
            alternatives=[ENTER, NO_TRADE])
        made.append(d)
        if r["ok"]:
            self._place(at, cond, oi, "BUY", ENTER, px, qty,
                        d["desk_decision_id"])
        return made

    def settle(self, cond, oi, payout, at):
        pnl = self.pf.settle(cond, oi, payout, at)
        self._record(at, "SETTLED", "settled at %.4f" % payout,
                     condition_id=cond, outcome_index=oi,
                     ev={"realized_usd": round(pnl, 2)})
        return pnl

    def snapshot(self, marks=None) -> dict:
        m = self.pf.mark(marks or {})
        return {
            "desk_version": VERSION, "desk_id": self.desk_id,
            "policy": self.policy.describe(),
            "limits": self.limits.to_dict(),
            "fill_model": FILL_MODEL,
            "queue_share_ASSUMED": self.queue_share,
            "p_fill": NOT_IDENTIFIED,
            # TRAVELS WITH EVERY SNAPSHOT. A gross book and a net book
            # are different numbers and must not render identically.
            "fee_basis": self.fee_basis,
            "pnl_is_net_of_fees": self.fee_basis == FEE_BASIS_APPLIED,
            "portfolio": self.pf.to_dict(),
            "marks": m,
            "consumption": self.cons.to_dict(),
            "orders": {"total": len(self.orders),
                       "open": len(self.open_orders()),
                       "by_state": {s: sum(1 for o in self.orders.values()
                                           if o.state == s)
                                    for s in (PROPOSED, RESTING,
                                              PARTIALLY_FILLED, FILLED,
                                              CANCELLED, EXPIRED, REJECTED)}},
            "committed_usd": round(self.committed_usd(), 2),
            "decisions": len(self.decisions),
            "invariant": self.pf.invariant(),
            "halted": self.halted, "halt_reason": self.halt_reason,
        }
