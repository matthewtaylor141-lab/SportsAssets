"""THE EXECUTOR: an authorized order lifecycle against a TEST venue.

WHAT THIS IS FOR. Everything upstream of here decided whether a submission
MAY happen: the canonical account binding, the owner-approved limits reduced
into effective rails, the readiness evidence and the recorded authorization.
None of that is an execution path. This module is the path -- it submits,
reads acknowledgements, takes partial fills, cancels, recovers after a
restart and reconciles the accounting -- and it asks
`bettor_entry_execution.authorize_submission` before EVERY action that could
reach a venue.

THE VENUE IS AN INTERFACE, AND THAT IS DELIBERATE.
`TestVenue` is a protocol with four methods. `SimulatedTestVenue` implements
it deterministically so the whole lifecycle can be exercised and asserted
without a credential; a real sandbox adapter implements the same four
methods and nothing else in this file changes. WHAT IS NOT CLAIMED: no
sandbox credential is connected, so no order has reached a venue's own
matching engine. That is stated here rather than implied by a green test.

AND FUNDED SUBMISSION STAYS OFF. The gate's last check is
`REAL_ORDER_SUBMISSION_ENABLED`, which is False in code. A FUNDED-class
venue is refused by this module before any adapter is touched, so the only
lifecycle reachable from here is a TEST-class one.

THE BOOK IS SEPARATE. Every row is written under
`TEST_VENUE_EXECUTION_V1`, which is not a strategy experiment, is not the
demonstration, and is excluded from strategy performance by the same
experiment-scoped reads that exclude the others.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Protocol

from . import bettor_desk as dk
from . import bettor_entry_execution as EX
from . import bettor_funded_activation as FA

VERSION = "BETTOR_TEST_VENUE_EXECUTOR_V1"

#: This executor's own book. Never a strategy experiment.
EXPERIMENT = "TEST_VENUE_EXECUTION_V1"

#: The DECLARED provenance for these rows (migration 123). An exercise of
#: the order lifecycle is not a decision the strategy took, and the two must
#: never read as one.
PROVENANCE = "TEST_VENUE_EXECUTION_LIFECYCLE"

#: The venue classes a lifecycle may run against. FUNDED is refused here,
#: before an adapter is constructed, so "test venue only" is not a
#: convention -- it is a check.
ALLOWED_VENUE_CLASSES = (FA.VENUE_TEST,)

R_VENUE_CLASS_NOT_ALLOWED = "ONLY_A_TEST_CLASS_VENUE_MAY_BE_EXECUTED_HERE"
R_NOT_AUTHORIZED = "THE_EXECUTION_BOUNDARY_REFUSED_THIS_SUBMISSION"
R_NO_OPEN_ORDER = "NO_OPEN_ORDER_TO_ACT_ON"
R_VENUE_REJECTED = "THE_VENUE_REJECTED_THE_ORDER"

#: A submission is DESCRIBED to the venue in these terms and no others, so
#: an adapter cannot quietly depend on our internals.
SUBMISSION_FIELDS = ("client_order_id", "condition_id", "outcome_index",
                     "side", "qty", "limit_price", "venue")


# ── 1 · THE VENUE INTERFACE ─────────────────────────────────────────

class TestVenue(Protocol):
    """Four methods. A real sandbox adapter implements exactly these."""

    name: str

    def submit(self, order: dict) -> dict:
        """Acknowledge or reject. Returns
        {ok, venue_order_id, state, rejected_because}."""

    def poll(self, venue_order_id: str) -> dict:
        """The venue's own view: {state, filled_qty, fills:[{qty, price}]}."""

    def cancel(self, venue_order_id: str) -> dict:
        """Request a cancel: {ok, state}."""

    def open_orders(self) -> list:
        """Every order the VENUE still considers live. This is what makes
        recovery possible: after a restart our rows are a claim and this is
        the truth."""


class SimulatedTestVenue:
    """A deterministic TEST venue. It is a SIMULATOR and says so.

    THE SCHEDULE IS FIXED so a test can assert exact numbers: the first poll
    after an acknowledgement fills `partial_ratio` of the order and leaves
    the rest resting; later polls fill nothing more; a cancel takes the
    remainder out. Rejection is available on demand (`reject_next`), because
    an executor that has never seen a rejection has not been exercised.

    IT HOLDS NO CREDENTIAL AND REACHES NO NETWORK.
    """

    name = "SIMULATED_TEST_VENUE"
    is_a_simulator = True
    reaches_a_real_venue = False

    def __init__(self, *, partial_ratio: float = 0.4, reject_next: bool = False,
                 fill_price_offset: float = 0.0):
        self.partial_ratio = float(partial_ratio)
        self.reject_next = bool(reject_next)
        self.fill_price_offset = float(fill_price_offset)
        self._orders: dict = {}
        self.calls: list = []

    # -- the interface ------------------------------------------------
    def submit(self, order: dict) -> dict:
        self.calls.append(("submit", order.get("client_order_id")))
        if self.reject_next:
            self.reject_next = False
            return {"ok": False, "venue_order_id": None, "state": dk.REJECTED,
                    "rejected_because": "SIMULATED_VENUE_REJECTION"}
        vid = "sim-%s" % uuid.uuid4().hex[:12]
        self._orders[vid] = {"venue_order_id": vid,
                             "client_order_id": order["client_order_id"],
                             "qty": float(order["qty"]),
                             "limit_price": float(order["limit_price"]),
                             "filled_qty": 0.0, "state": dk.RESTING,
                             "polls": 0, "fills": []}
        return {"ok": True, "venue_order_id": vid, "state": dk.RESTING,
                "rejected_because": None}

    def poll(self, venue_order_id: str) -> dict:
        self.calls.append(("poll", venue_order_id))
        o = self._orders.get(venue_order_id)
        if o is None:
            return {"state": "UNKNOWN_AT_THE_VENUE", "filled_qty": 0.0,
                    "fills": []}
        o["polls"] += 1
        new = []
        if o["polls"] == 1 and o["state"] == dk.RESTING:
            q = round(o["qty"] * self.partial_ratio, 6)
            if q > 0:
                px = round(o["limit_price"] + self.fill_price_offset, 6)
                new = [{"qty": q, "price": px}]
                o["filled_qty"] = round(o["filled_qty"] + q, 6)
                o["fills"].extend(new)
                o["state"] = (dk.FILLED if o["filled_qty"] >= o["qty"] - 1e-9
                              else dk.PARTIALLY_FILLED)
        return {"state": o["state"], "filled_qty": o["filled_qty"],
                "fills": new}

    def cancel(self, venue_order_id: str) -> dict:
        self.calls.append(("cancel", venue_order_id))
        o = self._orders.get(venue_order_id)
        if o is None:
            return {"ok": False, "state": "UNKNOWN_AT_THE_VENUE"}
        if o["state"] in (dk.FILLED, dk.CANCELLED):
            return {"ok": False, "state": o["state"]}
        o["state"] = dk.CANCELLED
        return {"ok": True, "state": dk.CANCELLED}

    def open_orders(self) -> list:
        return [dict(o) for o in self._orders.values()
                if o["state"] in dk.OPEN_STATES]


# ── 2 · FEES, FROM THE DEPLOYED SCHEDULE ────────────────────────────

def fee_for(qty: float, price: float, *, maker: bool = False) -> float:
    """The lane's own fee hook. Not a local constant."""
    from decimal import Decimal

    from . import bettor_fee_schedule as FEES

    return float(FEES.LATEST.fill_fee(Decimal(str(round(float(qty), 6))),
                                     Decimal(str(round(float(price), 6))),
                                     maker=bool(maker)))


# ── 3 · THE GATE, IN FRONT OF EVERY ACTION ──────────────────────────

async def _authorization(conn) -> dict:
    raw = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1",
        FA.AUTHORIZATION_KEY)
    return (json.loads(raw) if isinstance(raw, str) else raw) or {}


async def _approved_limits(conn) -> dict:
    raw = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1", FA.LIMITS_KEY)
    rec = (json.loads(raw) if isinstance(raw, str) else raw) or {}
    return dict(rec.get("proposed") or {}) if rec.get("approved") else {}


async def check_authorized(conn, *, account_id: str, venue: str) -> dict:
    """ASK THE EXECUTION BOUNDARY. Nothing here decides authorization.

    The venue CLASS is checked first and separately: this module executes a
    TEST-class venue only, and a FUNDED one is refused before any adapter
    exists, whatever the authorization says.
    """
    klass = FA.venue_class(venue)
    out = {"version": VERSION, "venue": venue, "venue_class": klass,
           "account_id": account_id}
    if klass not in ALLOWED_VENUE_CLASSES:
        return dict(out, ok=False, refusal=R_VENUE_CLASS_NOT_ALLOWED,
                    allowed=list(ALLOWED_VENUE_CLASSES),
                    why=("this executor runs a TEST-class venue only; a "
                         "funded venue is a different path and it is off "
                         "in code"))
    rec = await _authorization(conn)
    limits = await _approved_limits(conn)
    gate = EX.authorize_submission(account_id=account_id, venue=venue,
                                   authorization=rec,
                                   approved_limits=limits or None)
    out["gate"] = gate
    out["authorization_consumed"] = bool(gate.get("authorization_consumed"))
    # THE ONE REFUSAL THAT IS NOT A BLOCK. `REAL_ORDER_SUBMISSION_IS_
    # DISABLED_IN_CODE` means the authorization was read and matched, and
    # the only thing left is the funded-submission constant. A TEST-venue
    # lifecycle is exactly what may proceed from there -- it reaches the
    # simulator and never `live_orders`.
    if gate.get("refusal") == EX.R_SUBMISSION_DISABLED:
        return dict(out, ok=True, refusal=None,
                    proceeds_because=("the authorization is consumed and "
                                      "the remaining refusal is the funded "
                                      "submission constant, which a TEST "
                                      "venue does not need"),
                    funded_submission="DISABLED")
    return dict(out, ok=False, refusal=gate.get("refusal"),
                why=gate.get("why"))


# ── 4 · THE LIFECYCLE ───────────────────────────────────────────────

async def ensure_book(conn) -> None:
    from . import bettor_entry_inventory as inv

    await inv.ensure_experiment(conn, EXPERIMENT)


async def _position_id(conn, *, condition_id: str) -> str | None:
    return await conn.fetchval(
        "SELECT position_id FROM rn1x_positions WHERE experiment_id = $1 "
        "AND condition_id = $2 ORDER BY decision_ts DESC LIMIT 1",
        EXPERIMENT, condition_id)


async def open_position(conn, *, condition_id: str, slug: str,
                        account_id: str,
                        outcome_index: int = 0,
                        qty: float = 100.0, price: float = 0.50,
                        now: float | None = None) -> dict:
    """The position the lifecycle hangs off. One row, in this book."""
    at = float(now if now is not None else time.time())
    pid = await _position_id(conn, condition_id=condition_id)
    if pid:
        return {"position_id": pid, "case": "EXACT_REPLAY"}
    pid = "%s:%s" % (EXPERIMENT, condition_id[:18])
    await conn.execute(
        """
        INSERT INTO rn1x_positions
          (position_id, experiment_id, policy, source_account,
           condition_id, outcome_index,
           entry_kind, entry_kind_why, seed_qty, seed_price, seed_basis_usd,
           source_ts, detected_ts, decision_ts, decision_basis,
           clock_integrity, provenance, venue_market_slug, payout_event,
           venue, position_is_a_lower_bound)
        VALUES ($1,$2,$3,$13,$4,$5,'NEW',
                'a test-venue execution lifecycle',
                $6,$7,$8, to_timestamp($9), to_timestamp($9),
                to_timestamp($9),'RUNTIME_WALL_CLOCK','MEASURED',
                $10,$11,$12,'PMUS_TEST', FALSE)
        ON CONFLICT (position_id) DO NOTHING
        """, pid, EXPERIMENT, EXPERIMENT, condition_id, int(outcome_index),
        float(qty), float(price), round(float(qty) * float(price), 6), at,
        PROVENANCE, slug,
        "A test-venue execution lifecycle", account_id)
    return {"position_id": pid, "case": "WROTE"}


async def submit(conn, venue_adapter, *, account_id: str, venue: str,
                 condition_id: str, slug: str, side: str = "BUY",
                 qty: float = 100.0, limit_price: float = 0.50,
                 outcome_index: int = 0, now: float | None = None) -> dict:
    """SUBMIT, but only if the boundary says so. Then record the ACK.

    The client order id is ours and is written BEFORE the adapter is
    called, so a crash between the call and the acknowledgement leaves a
    row that recovery can find. An order the venue never acknowledged is
    not silently dropped.
    """
    at = float(now if now is not None else time.time())
    auth = await check_authorized(conn, account_id=account_id, venue=venue)
    out = {"version": VERSION, "action": "submit", "authorization": auth,
           "submitted": False}
    if not auth.get("ok"):
        return dict(out, ok=False, refusal=auth.get("refusal"),
                    why=auth.get("why"))

    await ensure_book(conn)
    pos = await open_position(conn, condition_id=condition_id, slug=slug,
                             account_id=account_id,
                             outcome_index=outcome_index, qty=qty,
                             price=limit_price, now=at)
    coid = "tvx-%s" % uuid.uuid4().hex[:14]
    did = "tvd-%s" % uuid.uuid4().hex[:14]
    # THE DECISION COMES FIRST, because an order with no decision behind it
    # is an order nobody can explain. `rn1x_orders.decision_id` is a foreign
    # key into this table.
    await conn.execute(
        """
        INSERT INTO rn1x_decisions
          (decision_id, position_id, decision_ts, selected_action,
           selection_reason, alternatives, ev_basis,
           conditional_on_our_fill, input_labels, selected_qty,
           policy_version, operating_state, eligibility)
        VALUES ($1,$2,to_timestamp($3),'ENTER',
                'an authorized TEST-venue execution lifecycle',
                '[]'::jsonb,'NOT_AN_EV_DECISION_AN_EXECUTION_EXERCISE',
                'true'::jsonb,'["CHOSEN_EXECUTION_PARAMETERS"]'::jsonb,$4,$5,
                'TEST_VENUE_EXECUTION','ELIGIBLE')
        """, did, pos["position_id"], at, float(qty), VERSION)
    await conn.execute(
        """
        INSERT INTO rn1x_orders
          (order_id, position_id, decision_id, condition_id, outcome_index,
           side, intent, liquidity, limit_price, qty, filled_qty, state,
           placed_at, updated_at, fill_basis, is_modelled)
        VALUES ($1,$2,$3,$4,$5,$6,'ENTER','TAKER',$7,$8,0,$9,
                to_timestamp($10), to_timestamp($10),
                'TEST_VENUE_ACKNOWLEDGEMENT', TRUE)
        """, coid, pos["position_id"], did, condition_id,
        int(outcome_index), side, float(limit_price), float(qty),
        dk.PROPOSED, at)

    order = {"client_order_id": coid, "condition_id": condition_id,
             "outcome_index": int(outcome_index), "side": side,
             "qty": float(qty), "limit_price": float(limit_price),
             "venue": venue}
    ack = venue_adapter.submit({k: order[k] for k in SUBMISSION_FIELDS})
    out["venue"] = {"name": getattr(venue_adapter, "name", "?"),
                    "is_a_simulator": getattr(venue_adapter,
                                              "is_a_simulator", None),
                    "ack": ack}
    if not ack.get("ok"):
        await conn.execute(
            "UPDATE rn1x_orders SET state = $2, updated_at = to_timestamp($3)"
            " WHERE order_id = $1", coid, dk.REJECTED, at)
        return dict(out, ok=False, refusal=R_VENUE_REJECTED,
                    order_id=coid, state=dk.REJECTED,
                    rejected_because=ack.get("rejected_because"))
    await conn.execute(
        "UPDATE rn1x_orders SET state = $2, venue_order_id = $3, "
        "updated_at = to_timestamp($4) WHERE order_id = $1",
        coid, dk.RESTING, ack["venue_order_id"], at)
    return dict(out, ok=True, submitted=True, order_id=coid,
                decision_id=did,
                venue_order_id=ack["venue_order_id"], state=dk.RESTING,
                position_id=pos["position_id"],
                funded_submission="DISABLED")


async def _open_orders(conn):
    return [dict(r) for r in await conn.fetch(
        "SELECT order_id, venue_order_id, condition_id, "
        "       position_id, side, qty::float8 AS qty, "
        "       filled_qty::float8 AS filled_qty, "
        "       limit_price::float8 AS limit_price, state "
        "  FROM rn1x_orders o "
        " WHERE o.position_id IN (SELECT position_id FROM rn1x_positions "
        "                          WHERE experiment_id = $1) "
        "   AND o.state = ANY($2::text[])", EXPERIMENT,
        list(dk.OPEN_STATES))]


async def poll_once(conn, venue_adapter, *, account_id: str, venue: str,
                    now: float | None = None) -> dict:
    """READ THE VENUE AND RECORD WHAT IT SAYS. Fills are the venue's.

    Every fill is written with its fee from the deployed schedule, and the
    order's state comes from the venue rather than from what we hoped.
    """
    at = float(now if now is not None else time.time())
    auth = await check_authorized(conn, account_id=account_id, venue=venue)
    out = {"version": VERSION, "action": "poll", "authorization": auth,
           "orders": []}
    if not auth.get("ok"):
        return dict(out, ok=False, refusal=auth.get("refusal"))
    rows = await _open_orders(conn)
    if not rows:
        return dict(out, ok=True, refusal=R_NO_OPEN_ORDER, orders=[])
    for r in rows:
        got = venue_adapter.poll(r["venue_order_id"])
        wrote = []
        for f in got.get("fills") or ():
            q, px = float(f["qty"]), float(f["price"])
            fee = fee_for(q, px)
            fid = "tvf-%s" % uuid.uuid4().hex[:14]
            # THE EVIDENCE FOR THIS FILL IS THE VENUE'S OWN REPORT, and the
            # row says so. `queue_share` is 1.0 because nothing was modelled:
            # the venue said it filled, it did not have to be inferred from a
            # print and a queue position.
            await conn.execute(
                "INSERT INTO rn1x_fills (fill_id, order_id, at, qty, price, "
                "fee_usd, evidence_id, evidence_qty, queue_share, "
                "fill_basis, is_modelled) VALUES "
                "($1,$2,to_timestamp($3),$4,$5,$6,$7,$4,1.0,"
                "'TEST_VENUE_REPORTED_FILL', TRUE)",
                fid, r["order_id"], at, q, px, fee,
                "venue:%s" % r["venue_order_id"])
            wrote.append({"fill_id": fid, "qty": q, "price": px,
                          "fee_usd": fee})
        state = str(got.get("state") or r["state"])
        await conn.execute(
            "UPDATE rn1x_orders SET filled_qty = $2, state = $3, "
            "updated_at = to_timestamp($4) WHERE order_id = $1",
            r["order_id"], float(got.get("filled_qty") or r["filled_qty"]),
            state, at)
        out["orders"].append({
            "order_id": r["order_id"],
            "venue_order_id": r["venue_order_id"],
            "venue_state": state,
            "filled_qty": float(got.get("filled_qty") or 0.0),
            "remaining": round(float(r["qty"])
                               - float(got.get("filled_qty") or 0.0), 6),
            "fills_written": wrote})
    return dict(out, ok=True, refusal=None)


async def cancel_open(conn, venue_adapter, *, account_id: str, venue: str,
                      now: float | None = None) -> dict:
    """CANCEL WHAT IS STILL WORKING, and record the venue's answer."""
    at = float(now if now is not None else time.time())
    auth = await check_authorized(conn, account_id=account_id, venue=venue)
    out = {"version": VERSION, "action": "cancel", "authorization": auth,
           "cancelled": []}
    if not auth.get("ok"):
        return dict(out, ok=False, refusal=auth.get("refusal"))
    rows = await _open_orders(conn)
    for r in rows:
        await conn.execute(
            "UPDATE rn1x_orders SET state = $2, updated_at = to_timestamp($3)"
            " WHERE order_id = $1", r["order_id"], dk.CANCEL_PENDING, at)
        got = venue_adapter.cancel(r["venue_order_id"])
        state = dk.CANCELLED if got.get("ok") else str(got.get("state"))
        await conn.execute(
            "UPDATE rn1x_orders SET state = $2, updated_at = to_timestamp($3)"
            " WHERE order_id = $1", r["order_id"], state, at)
        out["cancelled"].append({"order_id": r["order_id"],
                                 "requested": dk.CANCEL_PENDING,
                                 "venue_said": got, "state_now": state})
    return dict(out, ok=True, refusal=None if rows else R_NO_OPEN_ORDER)


async def recover(conn, venue_adapter, *, account_id: str, venue: str,
                  now: float | None = None) -> dict:
    """AFTER A RESTART, RECONCILE OUR ROWS AGAINST THE VENUE'S TRUTH.

    Our rows are a CLAIM; `open_orders()` is what the venue still holds.
    Three cases, each named:

      * we think it is open and the venue agrees   -> adopt the venue's
        filled quantity and state
      * we think it is open and the venue does not -> the venue is right;
        the order is closed here, and how it closed is recorded
      * the venue holds an order we have no row for -> reported as an
        ORPHAN and never adopted silently

    Nothing is re-submitted. A recovery that re-sends is how one order
    becomes two.
    """
    at = float(now if now is not None else time.time())
    auth = await check_authorized(conn, account_id=account_id, venue=venue)
    out = {"version": VERSION, "action": "recover", "authorization": auth,
           "resubmitted_anything": False, "reconciled": [], "orphans": []}
    if not auth.get("ok"):
        return dict(out, ok=False, refusal=auth.get("refusal"))
    ours = {r["venue_order_id"]: r for r in await _open_orders(conn)}
    theirs = {o["venue_order_id"]: o for o in venue_adapter.open_orders()}
    for vid, r in ours.items():
        if vid in theirs:
            v = theirs[vid]
            await conn.execute(
                "UPDATE rn1x_orders SET filled_qty = $2, state = $3, "
                "updated_at = to_timestamp($4) WHERE order_id = $1",
                r["order_id"], float(v["filled_qty"]), str(v["state"]), at)
            out["reconciled"].append({
                "order_id": r["order_id"], "case": "OPEN_AT_BOTH",
                "adopted_filled_qty": float(v["filled_qty"]),
                "adopted_state": str(v["state"])})
        else:
            got = venue_adapter.poll(vid)
            state = str(got.get("state") or dk.CANCELLED)
            await conn.execute(
                "UPDATE rn1x_orders SET state = $2, filled_qty = $3, "
                "updated_at = to_timestamp($4) WHERE order_id = $1",
                r["order_id"], state,
                float(got.get("filled_qty") or r["filled_qty"]), at)
            out["reconciled"].append({
                "order_id": r["order_id"],
                "case": "OPEN_HERE_CLOSED_AT_THE_VENUE",
                "venue_state": state})
    for vid in set(theirs) - set(ours):
        out["orphans"].append({"venue_order_id": vid,
                               "what": ("the venue holds an order this book "
                                        "has no row for. Reported, never "
                                        "adopted")})
    return dict(out, ok=True, refusal=None)


# ── 5 · THE ACCOUNTING, RECOMPUTED FROM THE ROWS ────────────────────

async def reconcile(conn) -> dict:
    """WHAT THE LEDGER SAYS, not what the executor remembers.

    Quantities, fees and notional are recomputed from `rn1x_orders` and
    `rn1x_fills`, and the identity that has to hold is stated and checked:
    filled + remaining + cancelled = submitted, per order.
    """
    orders = [dict(r) for r in await conn.fetch(
        "SELECT o.order_id, o.side, o.state, o.qty::float8 AS qty, "
        "       o.filled_qty::float8 AS filled_qty, "
        "       o.limit_price::float8 AS limit_price "
        "  FROM rn1x_orders o JOIN rn1x_positions p "
        "    ON p.position_id = o.position_id "
        " WHERE p.experiment_id = $1 ORDER BY o.placed_at", EXPERIMENT)]
    fills = [dict(r) for r in await conn.fetch(
        "SELECT f.order_id, f.qty::float8 AS qty, f.price::float8 AS price, "
        "       f.fee_usd::float8 AS fee_usd "
        "  FROM rn1x_fills f JOIN rn1x_orders o ON o.order_id = f.order_id "
        "  JOIN rn1x_positions p ON p.position_id = o.position_id "
        " WHERE p.experiment_id = $1", EXPERIMENT)]
    by_order: dict = {}
    for f in fills:
        by_order.setdefault(f["order_id"], []).append(f)

    filled_qty = round(sum(f["qty"] for f in fills), 6)
    fees = round(sum(f["fee_usd"] for f in fills), 6)
    notional = round(sum(f["qty"] * f["price"] for f in fills), 6)
    expected_fees = round(sum(fee_for(f["qty"], f["price"]) for f in fills), 6)

    per_order, discrepancies = [], []
    for o in orders:
        got = by_order.get(o["order_id"], [])
        fq = round(sum(f["qty"] for f in got), 6)
        # THE IDENTITY. Whatever did not fill is either still working or
        # was taken out; a terminal order with neither is a discrepancy.
        remaining = round(float(o["qty"]) - fq, 6)
        row = {"order_id": o["order_id"], "state": o["state"],
               "submitted_qty": float(o["qty"]),
               "filled_qty_from_fills": fq,
               "filled_qty_on_the_order": float(o["filled_qty"]),
               "remaining": remaining,
               "fees_usd": round(sum(f["fee_usd"] for f in got), 6)}
        if abs(fq - float(o["filled_qty"])) > 1e-6:
            discrepancies.append({
                "order_id": o["order_id"],
                "what": "the order's filled_qty disagrees with its fills",
                "on_the_order": float(o["filled_qty"]), "from_fills": fq})
        if o["state"] in dk.TERMINAL_STATES and remaining > 1e-6 \
                and o["state"] != dk.CANCELLED and o["state"] != dk.REJECTED:
            discrepancies.append({
                "order_id": o["order_id"],
                "what": ("terminal in %s with %s unfilled and not cancelled"
                         % (o["state"], remaining))})
        per_order.append(row)

    return {
        "version": VERSION, "experiment_id": EXPERIMENT,
        "orders": len(orders), "fills": len(fills),
        "filled_qty": filled_qty, "notional_usd": notional,
        "fees_usd": fees, "expected_fees_usd": expected_fees,
        "fees_reconcile": abs(fees - expected_fees) < 1e-6,
        "per_order": per_order,
        "discrepancies": discrepancies,
        "discrepancy_count": len(discrepancies),
        "states": {s: sum(1 for o in orders if o["state"] == s)
                   for s in sorted({o["state"] for o in orders})},
        "basis": ("recomputed from rn1x_orders and rn1x_fills, not from the "
                  "executor's own memory"),
        "this_book_is_not_strategy_performance": True,
    }


def describe() -> dict:
    return {
        "version": VERSION,
        "experiment_id": EXPERIMENT,
        "allowed_venue_classes": list(ALLOWED_VENUE_CLASSES),
        "asks_before_every_action":
            "bettor_entry_execution.authorize_submission",
        "venue_interface": ["submit", "poll", "cancel", "open_orders"],
        "what_is_not_claimed": (
            "no venue sandbox credential is connected, so no order here has "
            "reached a real matching engine. SimulatedTestVenue is a "
            "simulator and says so on the object."),
        "funded_submission": "DISABLED",
        "refusals": [R_VENUE_CLASS_NOT_ALLOWED, R_NOT_AUTHORIZED,
                     R_NO_OPEN_ORDER, R_VENUE_REJECTED],
        "recovery_never_resubmits": True,
    }
