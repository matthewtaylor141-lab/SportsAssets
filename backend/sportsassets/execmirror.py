"""1:1,000 EXECUTION MIRROR.

The paper experiment decides; this lane copies each NEW paper order (after
the cutover) to a fresh, separately identified Polymarket US account whose
credential lives only in the API environment (PMUS_EXECMIRROR_KEY_ID /
PMUS_EXECMIRROR_SECRET_KEY). Management sees the paper order, the live
order derived from it, the venue's fills and the difference.

THE RULES (owner: "set the rules to achieve the closest outcome")
  quantity   live = paper qty / scale (1,000), rounded to the NEAREST whole
             contract (the venue's quantity is an integer); the rounding is
             recorded on the row. A paper order under half a live contract
             is EXCLUDED as BELOW_VENUE_MINIMUM, never rounded up to one.
  intent     copied: same contract (us_market_slug), same venue intent
             (BUY_LONG / BUY_SHORT / SELL_LONG / SELL_SHORT), same wire
             price, limit order, same time in force (IOC / FOK / GTD with
             the paper expiry), RESTING paper orders are post-only.
  exits      a paper EXIT / REDUCE / STANDING_PROTECTION sells the SAME
             FRACTION of the group's live inventory that the paper order
             sells of the paper inventory, capped by live inventory not
             already committed to another live exit; an order that closes
             the paper position closes the live one. No live inventory:
             EXCLUDED NO_LIVE_INVENTORY (a protection is re-planned when
             live inventory arrives). Never sells what was not bought.
  hedges     a paper HEDGE is a BUY of an additional leg, never a sale of
             the original position: it is live only for a group whose
             live-eligible ENTRY acquired live inventory (venue fills);
             otherwise EXCLUDED NO_LIVE_INVENTORY -- a hedge of nothing would
             be new, unprotected exposure.
  fills      only from the venue's order record (cumulative quantity,
             average price, commission); an accepted order is not a fill.
  orphans    live inventory left after the paper position closed by order
             (not by settlement) is closed with an IOC at the venue bid,
             recorded as ORPHAN_CLOSE.
  xavier     each OPEN actual position (smalllive_handoffs) is reviewed on
             the tick that hands it off, on a change of held quantity and
             every MANAGEMENT_EVERY_S -- also while the lane is disabled or
             stopped (a review is a record: a BBO read, a probability read,
             a row; it places and cancels nothing); one failing review never
             stops the others.
  safety     a separate durable control (off by default), an emergency
             stop that cancels every open mirror order (and, if asked,
             closes positions), a per-order notional cap, an account
             fingerprint checked every cycle, one runner (advisory lock),
             and an ambiguous submission is reconciled against the venue
             before anything is retried.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from . import execmirror_probe as EP
from . import venue_pace

log = logging.getLogger(__name__)

VERSION = "EXECMIRROR_V1"
LOCK_KEY = 0x45584D31          # 'EXM1'
TICK_S = 2.0
SNAPSHOT_EVERY_S = 60.0
UNKNOWN_GRACE_S = 45.0
SUBMITTING_STALE_S = 30.0
PACE_S = 0.25
MAX_PROTECTION_ROWS = 12
MANAGEMENT_EVERY_S = 60.0
VENUE = "POLYMARKET"
PAPER_ACCOUNT = "paper_acct_main"
BUY_ROLES = ("ENTRY", "HEDGE")
SELL_ROLES = ("EXIT", "REDUCE", "STANDING_PROTECTION")
OPEN_STATES = ("PLANNED", "SUBMITTING", "UNKNOWN", "OPEN", "PARTIALLY_FILLED",
               "CANCEL_REQUESTED")
LIVE_STATES = ("OPEN", "PARTIALLY_FILLED", "CANCEL_REQUESTED")
PAPER_DEAD = ("CANCELED", "EXPIRED", "REJECTED", "CANCEL_PENDING")
TIF = {"IOC": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
       "FOK": "TIME_IN_FORCE_FILL_OR_KILL",
       "GTD": "TIME_IN_FORCE_GOOD_TILL_DATE"}
VENUE_TERMINAL = {"ORDER_STATE_FILLED": "FILLED",
                  "ORDER_STATE_CANCELED": "CANCELLED",
                  "ORDER_STATE_EXPIRED": "EXPIRED",
                  "ORDER_STATE_REJECTED": "REJECTED",
                  "ORDER_STATE_REPLACED": "CANCELLED"}
EXIT_FOR = {"ORDER_INTENT_BUY_LONG": "ORDER_INTENT_SELL_LONG",
            "ORDER_INTENT_BUY_SHORT": "ORDER_INTENT_SELL_SHORT"}

# Exclusion codes (precise, never silent)
BELOW_VENUE_MINIMUM = "BELOW_VENUE_MINIMUM"
NO_LIVE_INVENTORY = "NO_LIVE_INVENTORY"
INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
ABOVE_ORDER_CAP = "ABOVE_ORDER_CAP"
INTENT_MISMATCH = "INTENT_MISMATCH"
UNSUPPORTED_ORDER = "UNSUPPORTED_ORDER"
INVENTORY_COMMITTED = "INVENTORY_COMMITTED"
STRATEGY_NOT_LIVE_ELIGIBLE = "STRATEGY_NOT_LIVE_ELIGIBLE"
INTENT_STALE = "INTENT_STALE"
PAPER_ORDER_ENDED = "PAPER_ORDER_ENDED_BEFORE_SUBMIT"

# PRE-SUBMIT REVALIDATION. A live order copies ONE paper decision; it is never
# re-interpreted with newer economics. The paper decision's probability is
# admissible only inside the collector's Pinnacle freshness rule
# (ext_pinnacle_loop.PINNACLE_MAX_AGE_S, 30 s; pinned equal by a test), so an
# intent older than that at submission is EXCLUDED as INTENT_STALE, as is one
# whose paper order has already ended or expired. Nothing is recomputed here.
MAX_INTENT_AGE_S = 30.0

# LIVE ELIGIBILITY: an explicit allowlist of strategy -> exact policy version.
# Small-live execution copies ECONOMICALLY QUALIFIED investment-policy
# decisions only (owner, 2026-10-03). The completed-game policy's ENTER
# requires the gross edge to clear its threshold AND modelled net profit
# after fees > 0 on a current observed book (paper_benchmark.decide_one).
# Everything else is PAPER ONLY and fails closed: exploration (selection
# relaxes the edge and after-fee requirements by design -- a research
# cost), training, calibration-only, the strict benchmark, the maker
# experiment, Derek's own lane until promoted, and any unknown strategy or
# version. A new version is NOT eligible until it is added here through the
# approved policy process.
LIVE_ELIGIBLE = {
    # V3 (owner 2026-10-03): PinnAPI is the sole probability authority. V2
    # stays eligible: its orders met the stricter multi-book floor.
    "PINNACLE_COMPLETED_GAME_PAPER": ("PINNACLE_COMPLETED_GAME_PAPER_V2",
                                      "PINNACLE_COMPLETED_GAME_PAPER_V3"),
}
PAPER_ONLY_CLASS = {
    "PINNACLE_EXPLORATION_PAPER": "EXPLORATION_RESEARCH_COST_PAPER_ONLY",
    "PINNACLE_ONLY_PAPER_BENCHMARK": "BENCHMARK_RESEARCH_ONLY",
    "PINNACLE_COMPLETED_GAME_MAKER_PAPER": "EXPERIMENT_NOT_PROMOTED",
}


def live_eligibility(order: dict) -> tuple[bool, dict]:
    """(eligible, evidence). Fail closed: an order is live-eligible only when
    its strategy is allowlisted and, for a BUY (new exposure), the deciding
    policy version is one of that strategy's allowlisted versions. A SELL of
    an allowlisted strategy's group is eligible (it can only ever reduce live
    inventory that an eligible entry created; plan_sell enforces that)."""
    strategy = order.get("strategy") or order.get("decision_strategy")
    version = order.get("decision_policy_version")
    ev = {"strategy": strategy, "policy_version": version,
          "role": order.get("role"),
          "allowlist": {k: list(v) for k, v in LIVE_ELIGIBLE.items()}}
    if strategy not in LIVE_ELIGIBLE:
        ev["class"] = PAPER_ONLY_CLASS.get(str(strategy), "UNKNOWN_STRATEGY")
        return False, ev
    if order.get("role") in BUY_ROLES and version not in LIVE_ELIGIBLE[strategy]:
        ev["class"] = "POLICY_VERSION_NOT_PROMOTED"
        return False, ev
    ev["class"] = "INVESTMENT_POLICY"
    return True, ev


# ─────────────────────────── pure rules ───────────────────────────

def scale_qty(paper_qty, scale=1000) -> tuple[Decimal, int, Decimal]:
    """(exact scaled qty, live whole contracts, rounding delta)."""
    exact = Decimal(str(paper_qty)) / Decimal(str(scale))
    live = int(exact.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    return exact, live, Decimal(live) - exact


def collateral_per_contract(intent: str, wire_price) -> Decimal:
    """What one contract ties up: a long pays the wire price, a short buy
    the complement (live_executor.wire_limit's convention)."""
    p = Decimal(str(wire_price))
    return (Decimal(1) - p) if intent == "ORDER_INTENT_BUY_SHORT" else p


@dataclass
class Plan:
    state: str                         # PLANNED | EXCLUDED
    live_qty: int = 0
    scaled_qty: Decimal | None = None
    rounding_delta: Decimal | None = None
    exclusion: str | None = None
    params: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


def venue_params(order: dict, live_qty: int) -> dict:
    tif = TIF.get(str(order["time_in_force"]))
    p = {"marketSlug": order["us_market_slug"], "intent": order["intent"],
         "type": "ORDER_TYPE_LIMIT",
         "price": {"value": "%.4f" % float(order["wire_price"]), "currency": "USD"},
         "quantity": int(live_qty), "tif": tif}
    if tif == TIF["GTD"]:
        exp = order.get("expires_at")
        p["goodTillTime"] = (exp.isoformat() if hasattr(exp, "isoformat")
                             else str(exp))
    if str(order.get("order_type")) == "RESTING":
        p["participateDontInitiate"] = True
    return p


def plan_buy(order: dict, *, scale, buying_power, max_order_usd) -> Plan:
    if str(order.get("time_in_force")) not in TIF:
        return Plan("EXCLUDED", exclusion=UNSUPPORTED_ORDER,
                    detail={"time_in_force": order.get("time_in_force")})
    exact, live, delta = scale_qty(order["qty"], scale)
    base = dict(scaled_qty=exact, rounding_delta=delta)
    if live < 1:
        return Plan("EXCLUDED", exclusion=BELOW_VENUE_MINIMUM, **base,
                    detail={"why": "paper qty / %s rounds to 0 whole contracts"
                            % scale})
    cost = collateral_per_contract(order["intent"], order["wire_price"]) * live
    if cost > Decimal(str(max_order_usd)):
        return Plan("EXCLUDED", exclusion=ABOVE_ORDER_CAP, live_qty=live, **base,
                    detail={"cost_usd": str(cost), "cap_usd": str(max_order_usd)})
    if buying_power is not None and cost > Decimal(str(buying_power)):
        return Plan("EXCLUDED", exclusion=INSUFFICIENT_CASH, live_qty=live, **base,
                    detail={"cost_usd": str(cost),
                            "buying_power_usd": str(buying_power)})
    return Plan("PLANNED", live_qty=live, params=venue_params(order, live),
                detail={"cost_usd": str(cost)}, **base)


def plan_sell(order: dict, *, scale, paper_open_qty, live_held: int,
              live_committed: int, opened_intent: str | None) -> Plan:
    """A paper exit sells the same fraction of live inventory."""
    if str(order.get("time_in_force")) not in TIF:
        return Plan("EXCLUDED", exclusion=UNSUPPORTED_ORDER)
    exact, _, delta = scale_qty(order["qty"], scale)
    if live_held <= 0:
        return Plan("EXCLUDED", exclusion=NO_LIVE_INVENTORY, scaled_qty=exact,
                    detail={"why": "the live entry for this group holds nothing"})
    if opened_intent and EXIT_FOR.get(opened_intent) != order["intent"]:
        return Plan("EXCLUDED", exclusion=INTENT_MISMATCH, scaled_qty=exact,
                    detail={"opened_with": opened_intent, "paper": order["intent"]})
    available = live_held - live_committed
    if available <= 0:
        return Plan("EXCLUDED", exclusion=INVENTORY_COMMITTED, scaled_qty=exact,
                    detail={"held": live_held, "committed": live_committed})
    q = Decimal(str(order["qty"]))
    po = Decimal(str(paper_open_qty or 0))
    closes = po <= 0 or q >= po
    if closes:
        live = available
        frac = Decimal(1)
    else:
        frac = q / po
        live = int((frac * live_held).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        live = min(live, available)
    if live < 1:
        return Plan("EXCLUDED", exclusion=BELOW_VENUE_MINIMUM, scaled_qty=exact,
                    detail={"fraction": str(frac), "held": live_held})
    return Plan("PLANNED", live_qty=live, scaled_qty=exact,
                rounding_delta=Decimal(live) - exact,
                params=venue_params(order, live),
                detail={"fraction_of_inventory": str(frac), "held": live_held,
                        "committed": live_committed, "closes": closes})


def fill_delta(prev_cum, prev_avg, prev_fee, cum, avg, fee):
    """A new fill from two reads of one venue order record, or None."""
    pc, nc = Decimal(str(prev_cum or 0)), Decimal(str(cum or 0))
    if nc <= pc:
        return None
    pa = Decimal(str(prev_avg or 0))
    na = Decimal(str(avg if avg is not None else prev_avg or 0))
    qty = nc - pc
    price = (na * nc - pa * pc) / qty if nc else na
    fee_d = Decimal(str(fee or 0)) - Decimal(str(prev_fee or 0))
    return {"qty": qty, "price": price.quantize(Decimal("0.000001")),
            "fee": max(fee_d, Decimal(0))}


def _quote_px(m: dict, *keys: str):
    for k in keys:
        v = m.get(k)
        if isinstance(v, dict):
            v = v.get("value")
        if v is None:
            continue
        try:
            px = Decimal(str(v))
        except Exception:                                     # noqa: BLE001
            continue
        if 0 < px < 1:
            return px
    return None


def _amt(v):
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return None if v in (None, "") else Decimal(str(v))
    except Exception:                                         # noqa: BLE001
        return None


def match_unknown(row: dict, open_orders: list) -> dict | None:
    """Find the venue order an ambiguous submission created: same market,
    intent, price and quantity, created after the attempt began, not
    already mapped."""
    want_px = Decimal(str(row["wire_price"])).quantize(Decimal("0.0001"))
    for o in open_orders:
        if (o.get("marketSlug") == row["us_market_slug"]
                and o.get("intent") == row["intent"]
                and int(o.get("quantity") or 0) == int(row["live_qty"])
                and (_amt(o.get("price")) or Decimal(-1)).quantize(Decimal("0.0001")) == want_px):
            return o
    return None


# ─────────────────────────── venue adapter ───────────────────────────

class Venue:
    """The mirror account's client. Built ONLY from the execution-mirror
    credential; paced; every call is synchronous (run in a thread)."""

    def __init__(self, client=None):
        if client is None:
            from polymarket_us import PolymarketUS
            kid, sec = EP._env(EP.KEY_ID_ENV), EP._env(EP.SECRET_ENV)
            if not (kid and sec):
                raise RuntimeError("EXECMIRROR_CREDENTIAL_ABSENT")
            client = PolymarketUS(key_id=kid, secret_key=sec, timeout=15.0,
                                  max_retries=1)
        self._c = client
        self._last = 0.0

    def _pace(self):
        wait = PACE_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def place(self, params: dict) -> dict:
        self._pace()
        return self._c.orders.create(dict(params, synchronousExecution=True))

    def cancel(self, venue_order_id: str, slug: str) -> None:
        self._pace()
        self._c.orders.cancel(venue_order_id, {"marketSlug": slug})

    def cancel_all(self) -> dict:
        self._pace()
        return self._c.orders.cancel_all({})

    def close(self, slug: str, bips: int = 300) -> dict:
        self._pace()
        return self._c.orders.close_position(
            {"marketSlug": slug, "synchronousExecution": True,
             "slippageTolerance": {"bips": int(bips)}})

    def order(self, venue_order_id: str) -> dict:
        self._pace()
        return (self._c.orders.retrieve(venue_order_id) or {}).get("order") or {}

    def open_orders(self, slugs=None) -> list:
        self._pace()
        return (self._c.orders.list({"slugs": list(slugs)} if slugs else None)
                or {}).get("orders") or []

    def balances(self) -> list:
        self._pace()
        return (self._c.account.balances() or {}).get("balances") or []

    def positions(self) -> dict:
        out, cursor = {}, None
        for _ in range(EP.MAX_POSITION_PAGES):
            self._pace()
            p = {"limit": 100}
            if cursor:
                p["cursor"] = cursor
            r = self._c.portfolio.positions(p) or {}
            out.update(r.get("positions") or {})
            cursor = r.get("nextCursor")
            if r.get("eof", True) or not cursor:
                break
        return out

    def bbo(self, slug: str) -> dict:
        self._pace()
        return self._c.markets.bbo(slug) or {}

    def quote(self, slug: str) -> dict:
        """{"bid", "ask", "state", "error"} from the mirror account's own
        `markets.bbo(slug)["marketData"]`, read the way pmus.bbo_read reads
        it (a quote is `{"value": "0.78", ...}` or a bare number, kept only
        inside (0, 1)). Parsed here, not imported: this lane never imports
        pmus, whose module carries the funded credential."""
        self._pace()
        d = (self._c.markets.bbo(slug) or {}).get("marketData") or {}
        if not isinstance(d, dict):
            return {"bid": None, "ask": None, "state": None,
                    "error": "marketData:%s" % type(d).__name__}
        return {"bid": _quote_px(d, "bestBid", "best_bid", "bid"),
                "ask": _quote_px(d, "bestAsk", "best_ask", "ask"),
                "state": d.get("state"), "error": None}


def _classify(exc: Exception) -> str:
    """'REJECTED' when the venue answered and refused (nothing placed);
    'UNKNOWN' when we cannot know whether an order exists."""
    status = getattr(exc, "status_code", None)
    if status in (400, 401, 403, 404, 422):
        return "REJECTED"
    return "UNKNOWN"


# ─────────────────────────── database loop ───────────────────────────

def _j(v) -> str:
    return json.dumps(v, default=str)


async def _event(conn, kind, *, mirror_id=None, paper_order_id=None, **detail):
    await conn.execute(
        "INSERT INTO execmirror_events (mirror_id, paper_order_id, kind, detail)"
        " VALUES ($1,$2,$3,$4::jsonb)", mirror_id, paper_order_id, kind, _j(detail))


async def control(conn) -> dict:
    r = await conn.fetchrow("SELECT * FROM execmirror_control WHERE id = 1")
    return dict(r) if r else {}


async def live_inventory(conn, group_id) -> dict:
    """Filled live contracts for a group, from venue-confirmed fills only,
    and the live sell quantity already committed to open exits."""
    r = await conn.fetchrow(
        """SELECT
             coalesce(sum(qty) FILTER (WHERE intent LIKE 'ORDER_INTENT_BUY%'), 0) AS bought,
             coalesce(sum(qty) FILTER (WHERE intent LIKE 'ORDER_INTENT_SELL%'), 0) AS sold
           FROM execmirror_fills WHERE group_id = $1""", group_id)
    committed = await conn.fetchval(
        """SELECT coalesce(sum(live_qty - cum_qty), 0) FROM execmirror_orders
            WHERE group_id = $1 AND intent LIKE 'ORDER_INTENT_SELL%'
              AND state IN ('PLANNED','SUBMITTING','UNKNOWN','OPEN',
                            'PARTIALLY_FILLED','CANCEL_REQUESTED')""", group_id)
    opened = await conn.fetchval(
        """SELECT intent FROM execmirror_orders WHERE group_id = $1
              AND intent LIKE 'ORDER_INTENT_BUY%' AND cum_qty > 0
            ORDER BY created_at LIMIT 1""", group_id)
    held = int(Decimal(str(r["bought"])) - Decimal(str(r["sold"])))
    return {"held": max(held, 0), "committed": int(committed or 0),
            "opened_intent": opened}


async def live_entry_qty(conn, group_id) -> Decimal:
    """Venue-confirmed live contracts bought by the group's ENTRY orders only
    (a hedge's own fills never count as the inventory it protects)."""
    q = await conn.fetchval(
        """SELECT coalesce(sum(f.qty), 0) FROM execmirror_fills f
             JOIN execmirror_orders m USING (mirror_id)
            WHERE f.group_id = $1 AND m.role = 'ENTRY'
              AND f.intent LIKE 'ORDER_INTENT_BUY%'""", group_id)
    return Decimal(str(q or 0))


async def paper_open_qty(conn, group_id) -> Decimal:
    r = await conn.fetchrow(
        """SELECT coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS b,
                  coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS s
             FROM paper_fills WHERE group_id = $1""", group_id)
    return Decimal(str(r["b"])) - Decimal(str(r["s"]))


#: THE ACTUAL POSITION'S PROBABILITY EVIDENCE WHEN NONE COULD BE READ. The
#: reader (paper_xavier.live_position_evidence) is handed in by the process
#: that starts the lane -- this module imports no paper module -- and a
#: missing reader or a failed read records PROBABILITY_UNAVAILABLE: null,
#: never 0, and never a reason to sell.
UNAVAILABLE_PROBABILITY = {
    "evidence_state": "PROBABILITY_UNAVAILABLE", "probability": None,
    "probability_source": None, "probability_source_at": None,
    "probability_received_at": None, "probability_age_s": None,
    "probability_limit_s": MAX_INTENT_AGE_S,
    "probability_limitation": (
        "no probability could be read for this actual position; none is "
        "invented and its absence alone never liquidates the position; the "
        "action follows the paper decision"),
    "current_hold_value_usd": None, "entry_time_hold_value_usd": None}


#: XAVIER'S ACTUAL-POSITION REVIEW TRIGGERS (per handoff, every tick): the
#: tick that hands a position off reviews it (FIRST_FILL), a change of held
#: quantity re-reviews it (FILL_EVENT), and every MANAGEMENT_EVERY_S it is
#: re-reviewed (SCHEDULED_BACKSTOP). Records only -- a review places,
#: cancels and sizes nothing.
LIVE_T_FIRST, LIVE_T_FILL, LIVE_T_BACKSTOP = ("FIRST_FILL", "FILL_EVENT",
                                              "SCHEDULED_BACKSTOP")
LIVE_TRIGGER_PRIORITY = {LIVE_T_FIRST: 0, LIVE_T_FILL: 1, LIVE_T_BACKSTOP: 2}


def live_review_trigger(*, last_reviewed_at, last_held, held,
                        now: float) -> str | None:
    """Which review an OPEN actual position is due, or None. Pure."""
    if last_reviewed_at is None:
        return LIVE_T_FIRST
    if last_held is None or Decimal(str(last_held)) != Decimal(str(held)):
        return LIVE_T_FILL
    if now - float(last_reviewed_at) >= MANAGEMENT_EVERY_S:
        return LIVE_T_BACKSTOP
    return None


class Mirror:
    def __init__(self, venue_factory=Venue, *, now=time.time,
                 paper_account: str = PAPER_ACCOUNT, probability_reader=None,
                 management_assessor=None):
        self.paper_account = paper_account
        self._probability_reader = probability_reader
        # Xavier's assessment of each actual review (thesis, alternatives,
        # shadow REALLOCATE): handed in by the process that starts the lane
        # (agents.xavier_management.actual_review_hook), record only.
        self._management_assessor = management_assessor
        self._venue_factory = venue_factory
        self._venue = None
        self._now = now
        self._last_management = 0.0
        self._last_snapshot = 0.0
        self._buying_power = None

    def venue(self):
        if self._venue is None:
            self._venue = self._venue_factory()
        return self._venue

    async def call(self, fn, *a):
        return await asyncio.to_thread(fn, *a)

    # --- one cycle -------------------------------------------------------
    async def tick(self, conn) -> dict:
        ctl = await control(conn)
        if not ctl.get("enabled"):
            # ACTUAL POSITIONS ARE STILL MANAGED WHILE THE LANE IS OFF: the
            # review is a record (a BBO read, a probability read, a row);
            # nothing is planned, submitted or cancelled here.
            return {"state": "DISABLED",
                    "xavier_live_reviews": await self._reviews_only(conn)}
        fp = EP.keys_present()["key_fingerprint"]
        if ctl.get("account_fingerprint") and fp != ctl["account_fingerprint"]:
            await conn.execute("UPDATE execmirror_control SET enabled = false,"
                               " updated_at = now() WHERE id = 1")
            await _event(conn, "HALTED_ACCOUNT_CHANGED", expected=ctl["account_fingerprint"],
                         found=fp)
            return {"state": "HALTED_ACCOUNT_CHANGED"}
        if ctl.get("stopped"):
            out = await self.emergency_stop(conn, ctl)
            if out.get("state") == "STOPPED" and ctl.get("stop_done_at"):
                # after the stop has completed: reviews only (records)
                out["xavier_live_reviews"] = await self._reviews_only(conn)
            return out
        out = {"state": "RUNNING"}
        if self._buying_power is None:
            out["snapshot"] = await self.snapshot(conn, ctl)
        out["recovered"] = await self.recover(conn)
        out["planned"] = await self.plan_new(conn, ctl)
        out["submitted"] = await self.submit_planned(conn)
        out["cancelled"] = await self.propagate_cancels(conn)
        out["polled"] = await self.poll(conn)
        out["protection"] = await self.resync_protection(conn, ctl)
        out["orphans"] = await self.close_orphans(conn)
        out["handoffs"] = await self.live_handoffs(conn)
        # every tick: only the positions that are DUE (first / fill /
        # cadence) are reviewed, so a new position is reviewed on the tick
        # that hands it off instead of waiting for a process-wide timer
        out["xavier_live_reviews"] = await self.xavier_live_reviews(conn)
        if self._now() - self._last_management >= MANAGEMENT_EVERY_S:
            self._last_management = self._now()
            out["audrey_reconciled"] = await self.audrey_reconcile(conn)
        if self._now() - self._last_snapshot >= SNAPSHOT_EVERY_S:
            out["snapshot"] = await self.snapshot(conn, ctl)
        return out

    # --- recovery ----------------------------------------------------------
    async def recover(self, conn) -> int:
        n = 0
        stale = await conn.fetch(
            """UPDATE execmirror_orders SET state = 'UNKNOWN', updated_at = now()
                WHERE state = 'SUBMITTING'
                  AND submit_started_at < now() - make_interval(secs => $1)
            RETURNING mirror_id""", SUBMITTING_STALE_S)
        for r in stale:
            await _event(conn, "SUBMISSION_AMBIGUOUS", mirror_id=r["mirror_id"])
        rows = await conn.fetch("SELECT * FROM execmirror_orders WHERE state = 'UNKNOWN'")
        if not rows:
            return 0
        slugs = sorted({r["us_market_slug"] for r in rows})
        try:
            venue_open = await self.call(self.venue().open_orders, slugs)
        except Exception as exc:                              # noqa: BLE001
            await _event(conn, "RECONCILE_READ_FAILED", error=str(exc)[:200])
            return 0
        mapped = {r["venue_order_id"] for r in await conn.fetch(
            "SELECT venue_order_id FROM execmirror_orders WHERE venue_order_id IS NOT NULL")}
        for r in rows:
            cand = match_unknown(dict(r), [o for o in venue_open
                                           if o.get("id") not in mapped])
            if cand:
                await conn.execute(
                    """UPDATE execmirror_orders SET state = 'OPEN', venue_order_id = $2,
                         venue_state = $3, accepted_at = now(), updated_at = now()
                       WHERE mirror_id = $1""", r["mirror_id"], cand["id"], cand.get("state"))
                mapped.add(cand["id"])
                await _event(conn, "RECONCILED_ADOPTED", mirror_id=r["mirror_id"],
                             venue_order_id=cand["id"])
                n += 1
            elif (self._now() - r["submit_started_at"].timestamp()) > UNKNOWN_GRACE_S:
                # Not resting after the grace window. An IOC/FOK leaves no open
                # order whether or not it traded, so it is NOT retried; it is
                # marked for the account reconciliation (positions) to settle.
                await conn.execute(
                    """UPDATE execmirror_orders SET state = 'REJECTED',
                         error = $2::jsonb, updated_at = now() WHERE mirror_id = $1""",
                    r["mirror_id"], _j({"code": "NOT_FOUND_AFTER_RECONCILE",
                                        "retried": False}))
                await _event(conn, "RECONCILED_NOT_FOUND", mirror_id=r["mirror_id"])
                n += 1
        return n

    # --- planning ----------------------------------------------------------
    async def plan_new(self, conn, ctl) -> int:
        rows = await conn.fetch(
            """SELECT o.*, d.strategy AS decision_strategy,
                      coalesce(d.policy_version, o.label->>'policy_version')
                        AS decision_policy_version
                 FROM paper_orders o LEFT JOIN paper_decisions d USING (decision_id)
                WHERE o.account_id = $1 AND o.decided_at >= $2
                  AND o.role <> 'ENTRY'
                  AND NOT EXISTS (SELECT 1 FROM execmirror_orders m
                                   WHERE m.paper_order_id = o.order_id)
                ORDER BY o.decided_at, o.order_id LIMIT 25""",
            self.paper_account, ctl["cutover_at"])
        n = 0
        for o in rows:
            o = dict(o)
            eligible, why = live_eligibility(o)
            if not eligible:
                plan = Plan("EXCLUDED", exclusion=STRATEGY_NOT_LIVE_ELIGIBLE,
                            detail={"live_eligibility": why})
            elif o["role"] == "HEDGE" and await live_entry_qty(
                    conn, o["group_id"]) <= 0:
                plan = Plan("EXCLUDED", exclusion=NO_LIVE_INVENTORY,
                            detail={"why": "a hedge protects live inventory; "
                                           "this group's live-eligible entry "
                                           "acquired none"})
            elif o["role"] in BUY_ROLES:
                plan = plan_buy(o, scale=ctl["scale"], buying_power=self._buying_power,
                                max_order_usd=ctl["max_order_usd"])
            elif o["role"] in SELL_ROLES:
                inv = await live_inventory(conn, o["group_id"])
                plan = plan_sell(o, scale=ctl["scale"],
                                 paper_open_qty=await paper_open_qty(conn, o["group_id"]),
                                 live_held=inv["held"], live_committed=inv["committed"],
                                 opened_intent=inv["opened_intent"])
            else:
                plan = Plan("EXCLUDED", exclusion=UNSUPPORTED_ORDER)
            await self._insert(conn, o, plan)
            if plan.state == "PLANNED" and self._buying_power is not None and o["role"] in BUY_ROLES:
                self._buying_power = float(Decimal(str(self._buying_power))
                                           - Decimal(plan.detail.get("cost_usd", "0")))
            n += 1
        return n

    async def _insert(self, conn, o, plan: Plan, *, mirror_id=None, role=None,
                      parent=None):
        mid = mirror_id or "em:" + o["order_id"]
        await conn.execute(
            """INSERT INTO execmirror_orders (mirror_id, paper_order_id, parent_mirror_id,
                 group_id, role, strategy, us_market_slug, intent, order_type, tif,
                 post_only, wire_price, good_till, paper_qty, scaled_qty, live_qty,
                 rounding_delta, state, exclusion, paper_decided_at, detail)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                       $18,$19,$20,$21::jsonb)
               ON CONFLICT (mirror_id) DO NOTHING""",
            mid, None if mirror_id else o["order_id"], parent, o["group_id"],
            role or o["role"], o.get("strategy") or o.get("decision_strategy"),
            o["us_market_slug"], o["intent"], o.get("order_type") or "MARKETABLE",
            o.get("time_in_force") or "IOC",
            bool(plan.params.get("participateDontInitiate")),
            o.get("wire_price"), o.get("expires_at"), o.get("qty"),
            plan.scaled_qty, plan.live_qty, plan.rounding_delta, plan.state,
            plan.exclusion, o.get("decided_at"),
            _j(dict(plan.detail, params=plan.params, label=o.get("label"))))
        await _event(conn, "PLANNED" if plan.state == "PLANNED" else "EXCLUDED",
                     mirror_id=mid, paper_order_id=o.get("order_id"),
                     exclusion=plan.exclusion, live_qty=plan.live_qty)

    # --- submission --------------------------------------------------------
    async def submit_planned(self, conn) -> int:
        rows = await conn.fetch(
            "SELECT * FROM execmirror_orders WHERE state = 'PLANNED' ORDER BY created_at LIMIT 10")
        n = 0
        for r in rows:
            params = (json.loads(r["detail"]) if isinstance(r["detail"], str)
                      else r["detail"]).get("params") or {}
            stale = await self._revalidate(conn, r)
            if stale is not None:
                n += 1
                continue
            claimed = await conn.fetchval(
                """UPDATE execmirror_orders SET state = 'SUBMITTING', attempts = attempts + 1,
                     submit_started_at = now(), updated_at = now()
                   WHERE mirror_id = $1 AND state = 'PLANNED' RETURNING mirror_id""",
                r["mirror_id"])
            if not claimed:
                continue
            t0 = time.monotonic()
            try:
                resp = await self.call(self.venue().place, params)
            except Exception as exc:                          # noqa: BLE001
                state = _classify(exc)
                await conn.execute(
                    """UPDATE execmirror_orders SET state = $2, error = $3::jsonb,
                         latency_ms = $4, updated_at = now() WHERE mirror_id = $1""",
                    r["mirror_id"], state,
                    _j({"error": type(exc).__name__,
                        "status": getattr(exc, "status_code", None),
                        "detail": EP._error(exc)["detail"]}),
                    int((time.monotonic() - t0) * 1000))
                await _event(conn, "SUBMIT_" + state, mirror_id=r["mirror_id"],
                             error=EP._error(exc))
                n += 1
                continue
            vid = (resp or {}).get("id")
            lat = int((time.monotonic() - t0) * 1000)
            await conn.execute(
                """UPDATE execmirror_orders SET state = $2, venue_order_id = $3,
                     accepted_at = now(), latency_ms = $4, updated_at = now(),
                     detail = detail || $5::jsonb
                   WHERE mirror_id = $1""",
                r["mirror_id"], "OPEN" if vid else "UNKNOWN", vid, lat,
                _j({"submit_executions": len((resp or {}).get("executions") or []),
                    "decision_to_accept_ms": (int((self._now() - r["paper_decided_at"].timestamp()) * 1000)
                                              if r["paper_decided_at"] else None)}))
            await _event(conn, "ACCEPTED" if vid else "SUBMISSION_AMBIGUOUS",
                         mirror_id=r["mirror_id"], venue_order_id=vid, latency_ms=lat)
            if vid:
                await self._refresh(conn, dict(r, venue_order_id=vid, cum_qty=0,
                                               avg_px=None, fees_usd=0))
            n += 1
        return n

    async def _revalidate(self, conn, r) -> str | None:
        """Exclude a PLANNED order whose intent is no longer the decision it
        copies. Returns the exclusion code, or None when it may be sent."""
        now = self._now()
        why, ev = None, {"checked_at": now, "max_intent_age_s": MAX_INTENT_AGE_S}
        if r["paper_order_id"]:
            p = await conn.fetchrow(
                "SELECT state, expires_at FROM paper_orders WHERE order_id = $1",
                r["paper_order_id"])
            ev["paper_state"] = None if p is None else p["state"]
            if p is None or p["state"] in PAPER_DEAD:
                why = PAPER_ORDER_ENDED
            elif p["expires_at"] is not None and p["expires_at"].timestamp() <= now:
                why, ev["paper_expired_at"] = INTENT_STALE, p["expires_at"].timestamp()
        if why is None and r["role"] in BUY_ROLES:
            decided = r["paper_decided_at"]
            age = None if decided is None else now - decided.timestamp()
            ev["intent_age_s"] = None if age is None else round(age, 3)
            if age is None or age > MAX_INTENT_AGE_S or age < -1.0:
                why = INTENT_STALE
        if why is None:
            return None
        done = await conn.fetchval(
            """UPDATE execmirror_orders SET state = 'EXCLUDED', exclusion = $2,
                 detail = detail || $3::jsonb, updated_at = now()
               WHERE mirror_id = $1 AND state = 'PLANNED' RETURNING mirror_id""",
            r["mirror_id"], why, _j({"revalidation": dict(ev, refused=why)}))
        if done:
            await _event(conn, "EXCLUDED", mirror_id=r["mirror_id"],
                         paper_order_id=r["paper_order_id"], exclusion=why)
        return why

    # --- cancellation --------------------------------------------------------
    async def propagate_cancels(self, conn) -> int:
        rows = await conn.fetch(
            """SELECT m.* FROM execmirror_orders m JOIN paper_orders p
                   ON p.order_id = m.paper_order_id
                WHERE m.state IN ('OPEN','PARTIALLY_FILLED') AND p.state = ANY($1)""",
            list(PAPER_DEAD))
        n = 0
        for r in rows:
            n += await self._cancel(conn, dict(r), "PAPER_ORDER_ENDED")
        return n

    async def _cancel(self, conn, r, why) -> int:
        try:
            await self.call(self.venue().cancel, r["venue_order_id"], r["us_market_slug"])
        except Exception as exc:                              # noqa: BLE001
            # A cancel race (already filled / expired) is settled by the poll.
            await _event(conn, "CANCEL_FAILED", mirror_id=r["mirror_id"],
                         why=why, error=EP._error(exc))
        await conn.execute(
            """UPDATE execmirror_orders SET state = 'CANCEL_REQUESTED', updated_at = now()
                WHERE mirror_id = $1 AND state IN ('OPEN','PARTIALLY_FILLED')""",
            r["mirror_id"])
        await _event(conn, "CANCEL_REQUESTED", mirror_id=r["mirror_id"], why=why)
        await self._refresh(conn, r)
        return 1

    # --- fills ---------------------------------------------------------------
    async def poll(self, conn) -> int:
        rows = await conn.fetch(
            """SELECT * FROM execmirror_orders WHERE venue_order_id IS NOT NULL
                  AND state IN ('OPEN','PARTIALLY_FILLED','CANCEL_REQUESTED')
                ORDER BY last_polled_at NULLS FIRST LIMIT 15""")
        for r in rows:
            await self._refresh(conn, dict(r))
        return len(rows)

    async def _refresh(self, conn, r) -> None:
        try:
            o = await self.call(self.venue().order, r["venue_order_id"])
        except Exception as exc:                              # noqa: BLE001
            await _event(conn, "POLL_FAILED", mirror_id=r["mirror_id"],
                         error=EP._error(exc))
            return
        cum = Decimal(str(o.get("cumQuantity") or 0))
        avg = _amt(o.get("avgPx"))
        fee = _amt(o.get("commissionNotionalTotalCollected")) or Decimal(0)
        d = fill_delta(r.get("cum_qty"), r.get("avg_px"), r.get("fees_usd"), cum, avg, fee)
        if d:
            await conn.execute(
                """INSERT INTO execmirror_fills (fill_key, mirror_id, venue_order_id,
                     group_id, us_market_slug, intent, qty, price, fee_usd)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING""",
                "%s:%s" % (r["venue_order_id"], cum), r["mirror_id"], r["venue_order_id"],
                r.get("group_id"), r["us_market_slug"], r["intent"], d["qty"],
                d["price"], d["fee"])
            await _event(conn, "LIVE_FILL", mirror_id=r["mirror_id"], qty=str(d["qty"]),
                         price=str(d["price"]), fee=str(d["fee"]))
        vstate = o.get("state")
        if vstate in VENUE_TERMINAL:
            state = VENUE_TERMINAL[vstate]
            if state == "CANCELLED" and cum >= Decimal(r.get("live_qty") or 0) > 0:
                state = "FILLED"
        elif cum > 0:
            state = "PARTIALLY_FILLED" if r.get("state") != "CANCEL_REQUESTED" else "CANCEL_REQUESTED"
        else:
            state = r.get("state") if r.get("state") in LIVE_STATES else "OPEN"
        await conn.execute(
            """UPDATE execmirror_orders SET cum_qty = $2, avg_px = $3, fees_usd = $4,
                 venue_state = $5, state = $6, last_polled_at = now(), updated_at = now()
               WHERE mirror_id = $1""",
            r["mirror_id"], cum, avg, fee, vstate, state)

    # --- protection follows live inventory -----------------------------------
    async def resync_protection(self, conn, ctl) -> int:
        """A paper standing protection that is still live is held on the
        live side at the same fraction of LIVE inventory. The latest live
        row for that paper order decides: an open live protection whose
        size no longer matches live inventory is cancelled; once nothing is
        working, a new live protection is placed at the current size (a
        child row, `detail.for_paper_order`). At most MAX_PROTECTION_ROWS
        per paper order, and none within a minute of a refusal."""
        papers = await conn.fetch(
            """SELECT p.* FROM paper_orders p
                WHERE p.role = 'STANDING_PROTECTION' AND p.account_id = $1
                  AND p.state IN ('PENDING_SIMULATION','RESTING','PARTIALLY_FILLED')
                  AND p.decided_at >= $2""", self.paper_account, ctl["cutover_at"])
        n = 0
        for p in papers:
            p = dict(p)
            rows = await conn.fetch(
                """SELECT * FROM execmirror_orders
                    WHERE paper_order_id = $1 OR detail->>'for_paper_order' = $1
                    ORDER BY created_at DESC""", p["order_id"])
            if not rows:
                continue                     # plan_new has not reached it yet
            cur = dict(rows[0])
            if cur["state"] in ("PLANNED", "SUBMITTING", "UNKNOWN", "CANCEL_REQUESTED"):
                continue                     # something is in flight
            inv = await live_inventory(conn, p["group_id"])
            mine_open = (int(cur["live_qty"]) - int(cur["cum_qty"] or 0)
                         if cur["state"] in ("OPEN", "PARTIALLY_FILLED") else 0)
            plan = plan_sell(p, scale=ctl["scale"],
                             paper_open_qty=await paper_open_qty(conn, p["group_id"]),
                             live_held=inv["held"],
                             live_committed=inv["committed"] - mine_open,
                             opened_intent=inv["opened_intent"])
            want = plan.live_qty if plan.state == "PLANNED" else 0
            if cur["state"] in ("OPEN", "PARTIALLY_FILLED"):
                if want != mine_open:
                    await self._cancel(conn, cur, "PROTECTION_RESIZE")
                    n += 1
                continue
            if want <= 0 or len(rows) >= MAX_PROTECTION_ROWS:
                continue
            if cur["state"] == "REJECTED" and (
                    self._now() - cur["updated_at"].timestamp()) < 60:
                continue
            child = "em:%s:r%d" % (p["order_id"], len(rows))
            plan.detail["for_paper_order"] = p["order_id"]
            plan.detail["replaces"] = cur["mirror_id"]
            await self._insert(conn, p, plan, mirror_id=child,
                               role="STANDING_PROTECTION", parent=cur["mirror_id"])
            n += 1
        return n

    # --- orphaned live inventory ---------------------------------------------
    async def close_orphans(self, conn) -> int:
        groups = await conn.fetch(
            """SELECT DISTINCT f.group_id, f.us_market_slug FROM execmirror_fills f
                WHERE f.group_id IS NOT NULL""")
        n = 0
        for g in groups:
            gid = g["group_id"]
            inv = await live_inventory(conn, gid)
            if inv["held"] <= 0 or inv["committed"] > 0:
                continue
            if await paper_open_qty(conn, gid) > 0:
                continue
            if await conn.fetchval(
                    """SELECT 1 FROM paper_orders WHERE group_id = $1 AND state IN
                       ('PENDING_SIMULATION','RESTING','PARTIALLY_FILLED','CANCEL_PENDING')
                       LIMIT 1""", gid):
                continue
            if await conn.fetchval("SELECT 1 FROM paper_settlements WHERE group_id = $1 LIMIT 1", gid):
                continue          # the venue settles the live side too
            recent = await conn.fetchval(
                """SELECT 1 FROM execmirror_orders WHERE group_id = $1 AND role = 'ORPHAN_CLOSE'
                     AND created_at > now() - interval '2 minutes' LIMIT 1""", gid)
            if recent:
                continue
            try:
                b = await self.call(self.venue().bbo, g["us_market_slug"])
            except Exception as exc:                          # noqa: BLE001
                await _event(conn, "ORPHAN_BBO_FAILED", error=EP._error(exc), group_id=gid)
                continue
            exit_intent = EXIT_FOR.get(inv["opened_intent"] or "")
            # SELL_LONG hits the bid; SELL_SHORT (buying the long back) lifts the ask
            side = (b.get("bbo") or b)
            px = _amt((side.get("bestBid") or side.get("bid")) if exit_intent == "ORDER_INTENT_SELL_LONG"
                      else (side.get("bestAsk") or side.get("ask")))
            if not exit_intent or px is None:
                await _event(conn, "ORPHAN_NO_PRICE", group_id=gid, bbo_keys=sorted(side.keys())[:10])
                continue
            o = {"order_id": None, "group_id": gid, "us_market_slug": g["us_market_slug"],
                 "intent": exit_intent, "order_type": "MARKETABLE", "time_in_force": "IOC",
                 "wire_price": px, "qty": None, "decided_at": None, "strategy": None}
            plan = Plan("PLANNED", live_qty=inv["held"],
                        params=venue_params(o, inv["held"]),
                        detail={"why": "paper position closed by order; live inventory remained"})
            await self._insert(conn, o, plan, mirror_id="em:orphan:%s:%d" % (gid, int(self._now())),
                               role="ORPHAN_CLOSE")
            n += 1
        return n

    # --- account snapshot and reconciliation ---------------------------------
    async def snapshot(self, conn, ctl) -> dict:
        self._last_snapshot = self._now()
        try:
            bal = await self.call(self.venue().balances)
            pos = await self.call(self.venue().positions)
            oo = await self.call(self.venue().open_orders)
        except Exception as exc:                              # noqa: BLE001
            await _event(conn, "SNAPSHOT_FAILED", error=EP._error(exc))
            return {"ok": False}
        if bal:
            self._buying_power = bal[0].get("buyingPower")
        ours = {r["us_market_slug"]: int(Decimal(str(r["net"]))) for r in await conn.fetch(
            """SELECT us_market_slug,
                      sum(CASE WHEN intent LIKE 'ORDER_INTENT_BUY%' THEN qty ELSE -qty END) AS net
                 FROM execmirror_fills GROUP BY 1""")}
        venue_net = {}
        for slug, p in pos.items():
            try:
                venue_net[slug] = abs(int(Decimal(str(p.get("netPosition") or 0))))
            except Exception:                                 # noqa: BLE001
                venue_net[slug] = None
        base = (ctl.get("baseline") or {})
        base = json.loads(base) if isinstance(base, str) else base
        base_net = {k: int(v) for k, v in (base.get("positions_net") or {}).items()}
        settled = {r["us_market_slug"] for r in await conn.fetch(
            "SELECT DISTINCT us_market_slug FROM paper_settlements")}
        expired = {s for s, p in pos.items() if p.get("expired")}
        diffs = {}
        for slug in set(ours) | set(venue_net):
            if slug in expired or (slug in settled and not venue_net.get(slug)):
                continue          # resolved at the venue: the live side settled
            if venue_net.get(slug, 0) != abs(ours.get(slug, 0) + base_net.get(slug, 0)):
                diffs[slug] = {"mirror_fills_net": ours.get(slug, 0),
                               "baseline": base_net.get(slug, 0),
                               "venue": venue_net.get(slug)}
        rec = {"reconciled": not diffs, "differences": diffs}
        await conn.execute(
            """INSERT INTO execmirror_snapshots (account_fingerprint, balances, positions,
                 open_orders, reconciliation) VALUES ($1,$2::jsonb,$3::jsonb,$4,$5::jsonb)""",
            ctl.get("account_fingerprint"),
            _j([EP._pick(b, EP.BALANCE_FIELDS) for b in bal]),
            _j([dict(slug=s, **EP._pick(p, EP.POSITION_FIELDS)) for s, p in pos.items()]),
            len(oo), _j(rec))
        if diffs:
            await _event(conn, "RECONCILIATION_DIFFERENCE", **rec)
        return rec

    # --- Xavier owns the ACTUAL position ------------------------------------
    async def live_handoffs(self, conn) -> int:
        """One handoff per paper group with ACTUAL filled inventory (venue
        fills only). Updated as fills arrive; CLOSED when held reaches 0."""
        rows = await conn.fetch(
            """SELECT f.group_id, min(f.us_market_slug) AS slug,
                      coalesce(sum(f.qty) FILTER (WHERE f.intent LIKE 'ORDER_INTENT_BUY%'), 0) AS bought,
                      coalesce(sum(f.qty) FILTER (WHERE f.intent LIKE 'ORDER_INTENT_SELL%'), 0) AS sold,
                      sum(f.qty * f.price) FILTER (WHERE f.intent LIKE 'ORDER_INTENT_BUY%') AS cost,
                      coalesce(sum(f.fee_usd), 0) AS fees, min(f.observed_at) AS first_at
                 FROM execmirror_fills f WHERE f.group_id IS NOT NULL GROUP BY 1""")
        n = 0
        for r in rows:
            bought = Decimal(str(r["bought"]))
            if bought <= 0:
                continue
            held = bought - Decimal(str(r["sold"]))
            entry = await conn.fetchrow(
                """SELECT mirror_id, intent FROM execmirror_orders WHERE group_id = $1
                      AND role IN ('ENTRY','HEDGE') AND cum_qty > 0
                    ORDER BY created_at LIMIT 1""", r["group_id"])
            if entry is None:
                continue
            ph = await conn.fetchval(
                "SELECT handoff_id FROM paper_handoffs WHERE group_id = $1", r["group_id"])
            avg = (Decimal(str(r["cost"])) / bought) if r["cost"] is not None else None
            hid = "livehand:%s:%s" % (VENUE.lower(), r["group_id"])
            await conn.execute(
                """INSERT INTO smalllive_handoffs (handoff_id, venue, group_id, us_market_slug,
                     entry_mirror_id, paper_handoff_id, opened_intent, live_held, live_bought,
                     avg_entry_px, fees_usd, first_live_fill_at, state)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                   ON CONFLICT (handoff_id) DO UPDATE SET live_held = EXCLUDED.live_held,
                     live_bought = EXCLUDED.live_bought, avg_entry_px = EXCLUDED.avg_entry_px,
                     fees_usd = EXCLUDED.fees_usd, state = EXCLUDED.state,
                     paper_handoff_id = coalesce(smalllive_handoffs.paper_handoff_id,
                                                 EXCLUDED.paper_handoff_id),
                     updated_at = now()
                   WHERE smalllive_handoffs.live_held IS DISTINCT FROM EXCLUDED.live_held
                      OR smalllive_handoffs.fees_usd IS DISTINCT FROM EXCLUDED.fees_usd
                      OR smalllive_handoffs.state IS DISTINCT FROM EXCLUDED.state
                      OR (smalllive_handoffs.paper_handoff_id IS NULL
                          AND EXCLUDED.paper_handoff_id IS NOT NULL)""",
                hid, VENUE, r["group_id"], r["slug"], entry["mirror_id"], ph, entry["intent"],
                held, bought, avg, Decimal(str(r["fees"])), r["first_at"],
                "OPEN" if held > 0 else "CLOSED")
            n += 1
        return n

    async def _live_probability(self, conn, h: dict, held) -> dict:
        """The evidence state of the actual position's probability. Never
        raises; never sells."""
        if self._probability_reader is None:
            return dict(UNAVAILABLE_PROBABILITY,
                        why="NO_PROBABILITY_READER_IN_THIS_PROCESS")
        try:
            return await self._probability_reader(conn, h, at=self._now(), qty=held)
        except Exception as exc:                              # noqa: BLE001
            return dict(UNAVAILABLE_PROBABILITY, why="PROBABILITY_READ_FAILED",
                        error=type(exc).__name__)

    async def _reviews_only(self, conn) -> int:
        """Reviews of OPEN actual positions while the lane is off or stopped:
        records only; never raises into the runner."""
        try:
            if not await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM smalllive_handoffs "
                    " WHERE state = 'OPEN')"):
                return 0
            return await self.xavier_live_reviews(conn)
        except Exception:                                     # noqa: BLE001
            log.exception("xavier live reviews (lane off) failed")
            return 0

    async def xavier_live_reviews(self, conn, *, due_only: bool = True) -> int:
        """Xavier's review of each OPEN actual position that is DUE (the
        tick that hands it off, a change of held quantity, every
        MANAGEMENT_EVERY_S), most urgent first, with a FRESH venue quote.
        The actual position is managed by the same management decisions
        Xavier makes on the paper position (mirrored exits and protection at
        the live fraction) and by the orphan close; this record states the
        actual state those decisions act on. A resting live protection is
        reported separately from filled protection. One failing review never
        stops the others."""
        now = self._now()
        hs = await conn.fetch(
            """SELECT h.*, extract(epoch FROM lr.reviewed_at)::float8 AS last_reviewed_at,
                      lr.live_held AS last_reviewed_held
                 FROM smalllive_handoffs h
                 LEFT JOIN LATERAL (
                     SELECT reviewed_at, live_held FROM smalllive_reviews r
                      WHERE r.handoff_id = h.handoff_id
                      ORDER BY reviewed_at DESC, review_id DESC LIMIT 1) lr ON true
                WHERE h.state = 'OPEN'""")
        due = []
        for h in hs:
            h = dict(h)
            trig = live_review_trigger(
                last_reviewed_at=h.get("last_reviewed_at"),
                last_held=h.get("last_reviewed_held"), held=h["live_held"], now=now)
            if trig is None and due_only:
                continue
            trig = trig or LIVE_T_BACKSTOP
            due_at = (h["first_live_fill_at"].timestamp()
                      if trig == LIVE_T_FIRST and h.get("first_live_fill_at") is not None
                      else (h.get("updated_at").timestamp()
                            if trig == LIVE_T_FILL and h.get("updated_at") is not None
                            else (None if h.get("last_reviewed_at") is None
                                  else h["last_reviewed_at"] + MANAGEMENT_EVERY_S)))
            due.append((LIVE_TRIGGER_PRIORITY[trig], due_at or now, h["handoff_id"],
                        h, trig, due_at))
        due.sort(key=lambda x: x[:3])
        n = 0
        for _, _, _, h, trig, due_at in due:
            try:
                await self._review_one(conn, h, trigger=trig, due_at=due_at)
                n += 1
            except Exception as exc:                          # noqa: BLE001
                log.exception("xavier live review failed for %s", h.get("handoff_id"))
                try:
                    await _event(conn, "XAVIER_LIVE_REVIEW_FAILED",
                                 group_id=h.get("group_id"),
                                 handoff_id=h.get("handoff_id"),
                                 error=type(exc).__name__)
                except Exception:                             # noqa: BLE001
                    pass
        return n

    async def _review_one(self, conn, h: dict, *, trigger: str, due_at) -> str:
        """ONE review of one actual position (record only)."""
        inv = await live_inventory(conn, h["group_id"])
        resting = await conn.fetchval(
            """SELECT coalesce(sum(live_qty - cum_qty), 0) FROM execmirror_orders
                WHERE group_id = $1 AND role = 'STANDING_PROTECTION'
                  AND state IN ('OPEN','PARTIALLY_FILLED')""", h["group_id"])
        filled_prot = await conn.fetchval(
            """SELECT coalesce(sum(cum_qty), 0) FROM execmirror_orders
                WHERE group_id = $1 AND role = 'STANDING_PROTECTION'""", h["group_id"])
        quote, mark = {"read": False}, None
        try:
            q = await self.call(self.venue().quote, h["us_market_slug"])
            bid, ask = _amt(q.get("bid")), _amt(q.get("ask"))
            quote = {"read": q.get("error") is None, "at": self._now(),
                     "bid": None if bid is None else str(bid),
                     "ask": None if ask is None else str(ask),
                     "state": q.get("state"), "error": q.get("error"),
                     "basis": "the mirror account's own venue BBO read at review time"}
            long_side = str(h.get("opened_intent") or "").endswith("BUY_LONG")
            exit_px = bid if long_side else (None if ask is None else Decimal(1) - ask)
            if exit_px is not None:
                mark = exit_px * Decimal(inv["held"])
        except Exception as exc:                              # noqa: BLE001
            quote = {"read": False, "error": EP._error(exc)}
        # cost per contract follows collateral_per_contract: a long
        # paid the price, a short buy the complement
        cost = (None if h["avg_entry_px"] is None else
                collateral_per_contract(str(h.get("opened_intent") or ""),
                                        h["avg_entry_px"]) * Decimal(inv["held"]))
        unreal = None if (mark is None or cost is None) else mark - cost - Decimal(str(h["fees_usd"]))
        # THE PROBABILITY'S FRESHNESS FOR THE ACTUAL POSITION: a fresh
        # PinnAPI probability is attempted for the held contract on the
        # same reader as the paper review; its evidence state is
        # recorded, never used to sell (the action follows the paper
        # decision; an unavailable probability liquidates nothing).
        prob = await self._live_probability(conn, h, inv["held"])
        pr = await conn.fetchrow(
            """SELECT review_id, recommendation FROM paper_xavier_reviews
                WHERE group_id = $1 ORDER BY reviewed_at DESC LIMIT 1""", h["group_id"])
        paper_open = await paper_open_qty(conn, h["group_id"])
        if inv["held"] <= 0:
            action = "NOTHING_HELD"
        elif paper_open <= 0:
            action = "ORPHAN_CLOSE_PENDING"
        elif inv["committed"] > 0:
            action = "EXIT_WORKING_FOLLOWS_PAPER_DECISION"
        else:
            action = "HOLD_FOLLOWS_PAPER_DECISION"
        now = self._now()
        rid = "liverev:%s:%d" % (h["group_id"], int(now * 1000))
        # THE MANAGEMENT POLICY THIS REVIEW RAN UNDER (the owner-approved
        # artifact with its exact hash, else READY_FOR_OWNER_APPROVAL)
        from .agents import xavier_small_live_policy as XSP
        mpol = await XSP.load_review_record(conn)
        mgmt = {"state": "NO_MANAGEMENT_ASSESSOR_IN_THIS_PROCESS"}
        if self._management_assessor is not None:
            try:
                mgmt = await self._management_assessor(
                    conn, h, review_id=rid, at=now, trigger=trigger, due_at=due_at,
                    cadence_s=MANAGEMENT_EVERY_S, quote=quote, prob=prob,
                    held=inv["held"],
                    paper_recommendation=None if pr is None else pr["recommendation"],
                    policy=mpol)
            except Exception as exc:                          # noqa: BLE001
                mgmt = {"ok": False, "why": "ASSESSOR_FAILED:%s" % type(exc).__name__}
        first_at = h.get("first_live_fill_at")
        lat = (None if due_at is None else round(now - float(due_at), 3))
        await conn.execute(
            """INSERT INTO smalllive_reviews (review_id, handoff_id, reviewed_at, live_held,
                 committed_exit_qty, resting_protection_qty, filled_protection_qty, quote,
                 mark_value_usd, cost_basis_usd, unrealized_usd, paper_review_id,
                 paper_recommendation, action, detail)
               VALUES ($1,$2,to_timestamp($3),$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14,
                       $15::jsonb)
               ON CONFLICT (review_id) DO NOTHING""",
            rid, h["handoff_id"], now, inv["held"], inv["committed"], resting or 0,
            filled_prot or 0, _j(quote), mark, cost, unreal,
            None if pr is None else pr["review_id"],
            None if pr is None else pr["recommendation"], action,
            _j({"paper_open_qty": str(paper_open), "position": "ACTUAL",
                "venue": VENUE, "mark_basis": "exit side of the fresh venue BBO",
                "resting_protection_is_not_filled_protection": True,
                "evidence_state": prob["evidence_state"],
                "probability_evidence": prob,
                "review_trigger": trigger, "due_at": due_at,
                "review_latency_s": lat,
                "first_live_fill_at": (first_at.timestamp()
                                       if hasattr(first_at, "timestamp") else first_at),
                "cadence_s": MANAGEMENT_EVERY_S,
                "management_policy": mpol,
                "management": mgmt}))
        return rid

    # --- Audrey reconciles the chain independently ---------------------------
    async def audrey_reconcile(self, conn) -> int:
        groups = await conn.fetch(
            """SELECT DISTINCT group_id FROM execmirror_orders
                WHERE group_id IS NOT NULL AND (venue_order_id IS NOT NULL OR state = 'EXCLUDED')
                  AND created_at > now() - interval '14 days'
               UNION
               -- a decision whose ACTUAL branch sent nothing (paper only, refused,
               -- or no lane): reconciled too, so a missed or refused actual
               -- execution is recorded beside its paper sibling
               SELECT DISTINCT group_id FROM execution_intents
                WHERE actual_state IN ('PAPER_ONLY', 'REFUSED', 'LANE_NOT_RUNNING')
                  AND created_at > now() - interval '14 days'""")
        snap = await conn.fetchrow(
            "SELECT reconciliation, at FROM execmirror_snapshots ORDER BY at DESC LIMIT 1")
        n = 0
        for g in groups:
            gid = g["group_id"]
            rows = [dict(r) for r in await conn.fetch(
                """SELECT m.*, p.decision_id, p.qty AS p_qty, p.state AS p_state
                     FROM execmirror_orders m LEFT JOIN paper_orders p ON p.order_id = m.paper_order_id
                    WHERE m.group_id = $1 ORDER BY m.created_at""", gid)]
            if not rows:
                # no actual order exists: the intent's own refusal is the link
                for i in await conn.fetch(
                        """SELECT i.*, p.order_id AS p_order_id FROM execution_intents i
                             LEFT JOIN paper_orders p ON p.decision_id = i.decision_id
                                                     AND p.role = 'ENTRY'
                            WHERE i.group_id = $1""", gid):
                    rows.append({"mirror_id": None, "role": "ENTRY",
                                 "execution_intent_id": i["intent_id"],
                                 "paper_order_id": i["p_order_id"],
                                 "decision_id": i["decision_id"], "_sibling": True,
                                 "state": "EXCLUDED", "exclusion": i["actual_refusal"]
                                 or i["actual_state"], "venue_order_id": None,
                                 "live_qty": i["live_qty"] or 0, "cum_qty": None,
                                 "paper_qty": None, "scaled_qty": i["live_raw_qty"],
                                 "us_market_slug": i["us_market_slug"],
                                 "avg_px": None, "accepted_at": None,
                                 "paper_decided_at": i["decided_at"]})
            disc, chain = [], []
            for m in rows:
                if m.get("execution_intent_id") and not m.get("paper_order_id") \
                        and m.get("mirror_id"):
                    # an ACTUAL sibling of a decision: its paper sibling is the
                    # ENTRY paper order of the same decision (never its parent)
                    ei = await conn.fetchrow(
                        """SELECT i.decision_id, p.order_id, p.qty AS p_qty,
                                  p.state AS p_state
                             FROM execution_intents i LEFT JOIN paper_orders p
                               ON p.decision_id = i.decision_id AND p.role = 'ENTRY'
                            WHERE i.intent_id = $1""", m["execution_intent_id"])
                    if ei is not None:
                        m = dict(m, decision_id=ei["decision_id"],
                                 paper_order_id=ei["order_id"], p_qty=ei["p_qty"],
                                 p_state=ei["p_state"], _sibling=True)
                pf = await conn.fetchrow(
                    "SELECT coalesce(sum(qty), 0) AS q, avg(price) AS px, coalesce(sum(fee_usd),0) AS fee "
                    "FROM paper_fills WHERE order_id = $1", m["paper_order_id"]) if m["paper_order_id"] else None
                lf = await conn.fetchrow(
                    "SELECT coalesce(sum(qty), 0) AS q, coalesce(sum(fee_usd),0) AS fee "
                    "FROM execmirror_fills WHERE mirror_id = $1", m["mirror_id"]) \
                    if m.get("mirror_id") else {"q": Decimal(0), "fee": Decimal(0)}
                dec = None
                if m.get("decision_id"):
                    dec = await conn.fetchrow(
                        "SELECT decision_id, verdict, strategy, policy_version FROM paper_decisions "
                        "WHERE decision_id = $1", m["decision_id"])
                link = {"mirror_id": m["mirror_id"], "role": m["role"],
                        "execution_intent_id": m.get("execution_intent_id"),
                        "relation": ("SIBLING_OF_ONE_DECISION" if m.get("_sibling")
                                     else "MIRRORED_FROM_PAPER_ORDER"),
                        "paper_order_id": m["paper_order_id"], "decision_id": m.get("decision_id"),
                        "decision_found": dec is not None,
                        "paper_fill_qty": None if pf is None else str(pf["q"]),
                        "live_state": m["state"], "exclusion": m["exclusion"],
                        "venue_order_id": m["venue_order_id"],
                        "live_intended_qty": m["live_qty"], "live_fill_qty": str(lf["q"]),
                        "venue_cum_qty": None if m["cum_qty"] is None else str(m["cum_qty"]),
                        "live_fees_usd": str(lf["fee"])}
                if m.get("execution_intent_id"):
                    # PAPER vs ACTUAL of ONE decision: what the simulator
                    # predicted beside what the venue did (neither waits)
                    pp = None if pf is None or pf["px"] is None else Decimal(str(pf["px"]))
                    lp = None if m.get("avg_px") is None else Decimal(str(m["avg_px"]))
                    link["divergence"] = {
                        "paper_fill_px": None if pp is None else str(pp),
                        "live_avg_px": None if lp is None else str(lp),
                        "price_difference": (None if pp is None or lp is None
                                             else str(lp - pp)),
                        "paper_fees_usd": None if pf is None else str(pf["fee"]),
                        "live_fees_usd": str(lf["fee"]),
                        "paper_qty_scaled": (None if m.get("scaled_qty") is None
                                             else str(m["scaled_qty"])),
                        "live_qty": m["live_qty"],
                        "decision_to_accept_ms": (
                            None if not (m.get("accepted_at") and m.get("paper_decided_at"))
                            else int((m["accepted_at"] - m["paper_decided_at"])
                                     .total_seconds() * 1000)),
                        "paper_sibling": ("PRESENT" if m["paper_order_id"]
                                          else "ABSENT")}
                    if dec is None and m.get("mirror_id"):
                        disc.append({"code": "ACTUAL_ORDER_WITHOUT_DECISION",
                                     "mirror_id": m["mirror_id"],
                                     "execution_intent_id": m["execution_intent_id"]})
                chain.append(link)
                # an excluded row sent nothing: there is no live order to orphan
                if m["role"] in BUY_ROLES and m["paper_order_id"] and dec is None \
                        and m["state"] != "EXCLUDED":
                    disc.append({"code": "LIVE_ORDER_WITHOUT_PAPER_DECISION", "mirror_id": m["mirror_id"]})
                if m["venue_order_id"] and m["cum_qty"] is not None and \
                        Decimal(str(m["cum_qty"])) != Decimal(str(lf["q"])):
                    disc.append({"code": "VENUE_CUM_QTY_NOT_EQUAL_RECORDED_FILLS",
                                 "mirror_id": m["mirror_id"], "venue": str(m["cum_qty"]),
                                 "fills": str(lf["q"])})
                if Decimal(str(lf["q"])) > Decimal(m["live_qty"] or 0):
                    disc.append({"code": "LIVE_FILLED_MORE_THAN_INTENDED", "mirror_id": m["mirror_id"]})
                if m["paper_qty"] is not None and m["scaled_qty"] is not None and m["live_qty"]:
                    if abs(Decimal(m["live_qty"]) - Decimal(str(m["scaled_qty"]))) > Decimal("0.5"):
                        disc.append({"code": "LIVE_QTY_NOT_THE_ROUNDED_SCALED_QTY",
                                     "mirror_id": m["mirror_id"]})
                if pf is not None and Decimal(str(lf["q"])) > 0 and Decimal(str(pf["q"])) == 0 \
                        and m.get("p_state") in PAPER_DEAD:
                    disc.append({"code": "LIVE_FILLED_PAPER_DID_NOT", "mirror_id": m["mirror_id"],
                                 "note": "an execution difference, recorded; not an error by itself"})
            held = (await live_inventory(conn, gid))["held"]
            hrow = await conn.fetchrow(
                "SELECT handoff_id FROM smalllive_handoffs WHERE group_id = $1", gid)
            if held > 0 and hrow is None:
                disc.append({"code": "ACTUAL_POSITION_WITHOUT_XAVIER_HANDOFF"})
            rec = (snap["reconciliation"] if snap else None)
            rec = json.loads(rec) if isinstance(rec, str) else rec
            slug = rows[0]["us_market_slug"] if rows else None
            if rec and slug in (rec.get("differences") or {}):
                disc.append({"code": "VENUE_POSITION_DIFFERS_FROM_MIRROR_FILLS",
                             "detail": rec["differences"][slug]})
            pending = any(c["live_state"] in OPEN_STATES for c in chain)
            # Every row excluded before submission, no venue order, no live
            # fill: there is no actual leg to match -- paper only by design.
            paper_only = bool(chain) and all(
                c["live_state"] == "EXCLUDED" and not c["venue_order_id"]
                and Decimal(c["live_fill_qty"]) == 0 for c in chain)
            status = ("DISCREPANCY" if disc else "PENDING" if pending
                      else "NOT_MIRRORED" if paper_only else "MATCHED")
            chain_doc = {"links": chain, "live_held": held,
                         "handoff_id": None if hrow is None else hrow["handoff_id"],
                         "account_snapshot_at": None if snap is None else snap["at"],
                         "paper_and_actual_pnl_are_separate": True}
            prev = await conn.fetchval(
                "SELECT status FROM smalllive_reconciliations WHERE group_id = $1", gid)
            await conn.execute(
                """INSERT INTO smalllive_reconciliations (group_id, venue, status, discrepancies, chain)
                   VALUES ($1,$2,$3,$4::jsonb,$5::jsonb)
                   ON CONFLICT (group_id) DO UPDATE SET status = EXCLUDED.status,
                     discrepancies = EXCLUDED.discrepancies, chain = EXCLUDED.chain,
                     reconciled_at = now(),
                     changed_at = CASE WHEN smalllive_reconciliations.status
                                       IS DISTINCT FROM EXCLUDED.status
                                       THEN now() ELSE smalllive_reconciliations.changed_at END""",
                gid, VENUE, status, _j(disc), _j(chain_doc))
            if prev != status:
                await _event(conn, "AUDREY_RECONCILIATION_" + status, group_id=gid,
                             discrepancies=disc)
            n += 1
        return n

    # --- emergency stop -------------------------------------------------------
    async def emergency_stop(self, conn, ctl) -> dict:
        if ctl.get("stop_done_at"):
            return {"state": "STOPPED"}
        out = {"state": "STOPPING", "cancelled": 0, "closed": []}
        for r in await conn.fetch(
                """SELECT * FROM execmirror_orders WHERE venue_order_id IS NOT NULL
                      AND state IN ('OPEN','PARTIALLY_FILLED','CANCEL_REQUESTED')"""):
            out["cancelled"] += await self._cancel(conn, dict(r), "EMERGENCY_STOP")
        try:
            await self.call(self.venue().cancel_all)
        except Exception as exc:                              # noqa: BLE001
            await _event(conn, "STOP_CANCEL_ALL_FAILED", error=EP._error(exc))
        await conn.execute(
            """UPDATE execmirror_orders SET state = 'EXCLUDED', exclusion = 'EMERGENCY_STOP',
                 updated_at = now() WHERE state = 'PLANNED'""")
        if ctl.get("flatten_on_stop"):
            for s in await conn.fetch(
                    """SELECT us_market_slug FROM execmirror_fills GROUP BY 1
                        HAVING sum(CASE WHEN intent LIKE 'ORDER_INTENT_BUY%' THEN qty ELSE -qty END) > 0"""):
                try:
                    await self.call(self.venue().close, s["us_market_slug"])
                    out["closed"].append(s["us_market_slug"])
                except Exception as exc:                      # noqa: BLE001
                    await _event(conn, "FLATTEN_FAILED", slug=s["us_market_slug"],
                                 error=EP._error(exc))
        await conn.execute("UPDATE execmirror_control SET stop_done_at = now(),"
                           " updated_at = now() WHERE id = 1")
        await _event(conn, "EMERGENCY_STOP_DONE", **out)
        out["state"] = "STOPPED"
        return out


async def run(get_pool, *, probability_reader=None, management_assessor=None) -> None:
    """The API's background lane. One runner across processes (advisory
    lock); disabled until the control row is enabled (actual positions are
    still REVIEWED while it is off -- records only). `probability_reader` is
    the actual position's probability-evidence reader and
    `management_assessor` Xavier's assessment of each review (both record
    only)."""
    mirror = Mirror(probability_reader=probability_reader,
                    management_assessor=management_assessor)
    # ONE DECISION -> PAPER + ACTUAL: the actual entry lane of this process
    # shares this runner's retail venue client, recovery and fill ingestion.
    from . import execution_intent as EI
    EI.start(get_pool, mirror)
    while True:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
                    await asyncio.sleep(15)
                    continue
                try:
                    while True:
                        try:
                            # the small-live money path: priority lane of the
                            # venue gate in this process (venue_pace E11)
                            with venue_pace.priority_claims():
                                await mirror.tick(conn)
                        except asyncio.CancelledError:
                            raise
                        except Exception:                     # noqa: BLE001
                            log.exception("execution mirror tick failed")
                        await asyncio.sleep(TICK_S)
                finally:
                    await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        except asyncio.CancelledError:
            raise
        except Exception:                                     # noqa: BLE001
            log.exception("execution mirror runner failed")
            await asyncio.sleep(15)
