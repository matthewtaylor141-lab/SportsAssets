"""The complete operating loop, driven by stream updates.

    live observation -> normalization -> decision -> shadow execution
    -> inventory -> outcome -> reconciled accounting

DRIVEN BY UPDATES, NOT BY A CLOCK. The book callback marks a slug dirty
and the loop decides on dirty slugs. A timer would decide on books that
have not moved and miss books that moved twice between ticks; the whole
point of a stream is that the engine sees the move, not the interval.

THREE CLOCKS, ALL PERSISTED. Every decision record carries the venue's
`transactTime`, our receipt time, and the time the decision was taken --
measured per observation, never once per pass. A pass-start timestamp
understates the age of the last book by however long the pass took,
which is exactly the interval a freshness bound exists to catch.

REFUSALS ARE RECORDS. A refusal and its reason are persisted beside
every eligible action. An engine that stores only its trades cannot be
evaluated, and this one will refuse nearly everything: on the 16 real
books replayed it was NO_TRADE 16 times.

EVERY FILL IS SIMULATED AND SAYS SO. A market trading at our quoted
price does NOT establish that our order filled -- we have no order, and
queue position behind orders we cannot see is unobservable.
`bettor_shadow_execution` refuses to infer it and
`QUEUE_AHEAD_AT_T0_IS_NOT_A_BOUND` is preserved as a retracted claim.
Trades that touch a quote price are counted as TOUCHES, a separate and
weaker fact, and never as fills.

NO ORDER PATH. This module holds the stream, the engine and the shadow
loop. It imports no order function, and `max_contracts` defaults to
zero so a misconfiguration cannot size one.

Kill: BETTOR_LIVE_LOOP=off.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from .. import bettor_decision_engine as de
from .. import bettor_market_stream as ms
from .. import bettor_observation_adapter as oa
from .. import bettor_shadow_loop as sl
from .. import bettor_universe as uni

log = logging.getLogger(__name__)

LOOP_VERSION = "BETTOR_LIVE_LOOP_V1"
PROSPECTIVE = "PROSPECTIVE_SHADOW"
KILL_ENV = "BETTOR_LIVE_LOOP"

# The decision bound, from the venue's own clock, at the moment of
# decision. Not a default anywhere below it: `book_at` requires it.
MAX_SOURCE_AGE_S = 10.0
# How old our own receipt may be. Tighter than the source bound: if we
# received it 10 s ago the source stamp is older still.
MAX_RECEIPT_AGE_S = 5.0

POLL_S = 0.2


def enabled() -> bool:
    return str(os.environ.get(KILL_ENV, "on")).strip().lower() != "off"


def _now():
    return datetime.now(timezone.utc)


class LiveLoop:
    """Stream in, decisions out, accounting reconciled. Never trades."""

    def __init__(self, stream, *, fees=None, opening_cash: float = 0.0,
                 max_contracts: float = 0.0, leg_of=None,
                 state_path: str | None = None):
        self.stream = stream
        self.fees = fees or de.Fees.published_pmus(
            os.environ.get("BETTOR_FEE_DATE")
            or _now().strftime("%Y-%m-%d"))
        # ZERO BY DEFAULT. A loop that sized orders by default is one
        # configuration mistake away from sizing real ones.
        self.max_contracts = max_contracts
        # slug -> outcome leg. One row is one OUTCOME LEG of one
        # contract; a read that cannot say which leg it is cannot be
        # normalized, and the reader has no way to invent it.
        self.leg_of = dict(leg_of or {})
        self.state_path = state_path

        self.shadow = sl.ShadowLoop(opening_cash=opening_cash,
                                    fees=self.fees,
                                    venue="polymarket-us",
                                    account_class="institutional")
        self._dirty: set = set()
        self.records: list = []
        self.counters: dict = {}
        self.decided_ids: set = set()
        self.started_at: str | None = None
        self.stopped_at: str | None = None
        self.touches: list = []

    # ── counters ─────────────────────────────────────────────────────

    def bump(self, key, n=1):
        self.counters[key] = self.counters.get(key, 0) + n

    def on_book(self, slug: str, rec: dict) -> None:
        """Stream callback. Cheap and non-blocking, by contract."""
        self._dirty.add(slug)

    def on_trade(self, rec: dict) -> None:
        """A trade is a TOUCH at most, never a fill.

        Recorded because it is the only public signal about whether a
        resting order at that price would have traded -- and it is a
        signal about SOMEBODY's order, in a queue position we cannot
        see, not about ours.
        """
        self.bump("trades_seen")
        self.touches.append(rec)

    # ── the loop ─────────────────────────────────────────────────────

    def drain(self) -> list:
        """Decide on every dirty slug once. Returns the new records."""
        with_lock = list(self._dirty)
        self._dirty.difference_update(with_lock)
        out = []
        for slug in with_lock:
            rec = self.decide_slug(slug)
            if rec is not None:
                out.append(rec)
        return out

    def decide_slug(self, slug: str) -> dict | None:
        # DECISION TIME IS TAKEN HERE, PER OBSERVATION.
        decided_at = _now().isoformat()
        at = self.stream.book_at(slug, decided_at=decided_at,
                                 max_source_age_s=MAX_SOURCE_AGE_S,
                                 max_receipt_age_s=MAX_RECEIPT_AGE_S)
        self.bump("books_examined")
        base = {"loop": LOOP_VERSION, "evidence_class": PROSPECTIVE,
                "market_id": slug, "decided_at": decided_at,
                "source_ts": at.get("source_ts"),
                "received_at": at.get("received_at"),
                "source_age_s": at.get("source_age_s"),
                "receipt_age_s": at.get("receipt_age_s"),
                "venue_state": at.get("venue_state"),
                "stream_epoch": at.get("epoch"),
                "fee_schedule": self.fees.source,
                "fee_status": self.fees.status}

        if not at["eligible"]:
            self.bump("ineligible")
            self.bump("ineligible:%s" % at["reason"])
            rec = dict(base, status="INELIGIBLE", reasons=[at["reason"]])
            self._record(rec)
            return rec

        leg = self.leg_of.get(slug)
        row = ms.to_observation(slug, at, outcome_leg=leg)
        oid = row["observation_id"]
        if oid in self.decided_ids:
            # NOT an optimisation. Re-deciding the same venue timestamp
            # would multiply one observation into several records and
            # inflate every count computed from them.
            self.bump("duplicate_observation_refused")
            return None
        self.decided_ids.add(oid)

        norm = oa.normalize(row, venue="polymarket-us",
                            account_class="institutional",
                            fee_source=self.fees.source)
        if norm.status != oa.ACCEPTED:
            self.bump("rejected")
            for r in norm.reasons:
                self.bump("reject:%s" % r)
            rec = dict(base, status="REJECTED", observation_id=oid,
                       reasons=list(norm.reasons))
            self._record(rec)
            return rec

        book = oa.to_book(norm)
        step = self.shadow.step(book, evidence_class=sl.REPLAYED,
                                max_contracts=self.max_contracts)
        d = step.get("engine") or {}
        selected = step.get("decision") or d.get("selected")
        self.bump("decided")
        self.bump("action:%s" % selected)
        blockers = sorted({c["blocker"] for c in d.get("candidates", [])
                           if c.get("blocker")})
        for b in blockers:
            self.bump("blocker:%s" % b)
        if step.get("execution"):
            self.bump("executed")

        rec = dict(base, status="DECIDED", observation_id=oid,
                   outcome_leg=leg, selected=selected,
                   size_contracts=d.get("size_contracts", 0.0),
                   reason=d.get("reason"), blockers=blockers,
                   top_of_book={"bid": norm.bid, "ask": norm.ask,
                                "bid_size": norm.bid_size,
                                "ask_size": norm.ask_size,
                                "cumulative_ask_size":
                                    norm.cumulative_ask_size},
                   executed=bool(step.get("execution")),
                   cash_after=step.get("cash_after"))
        self._record(rec)
        return rec

    def _record(self, rec: dict) -> None:
        self.records.append(rec)
        if self.state_path:
            try:
                with open(self.state_path, "a") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
            except OSError as exc:  # noqa: BLE001 -- a full disk must not
                self.bump("persist_failed")   # silently stop the record
                log.warning("decision persist failed: %s", exc)

    def run_for(self, seconds: float) -> dict:
        """Run the loop for a bounded wall-clock window. Never unbounded."""
        self.started_at = _now().isoformat()
        deadline = time.time() + seconds
        while time.time() < deadline:
            if not enabled():
                self.bump("halted_by_kill_switch")
                break
            self.drain()
            for t in self.stream.drain_trades():
                self.on_trade(t)
            time.sleep(POLL_S)
        self.drain()
        self.stopped_at = _now().isoformat()
        return self.report()

    # ── reporting ────────────────────────────────────────────────────

    def freshness_distribution(self) -> dict:
        ages = sorted(r["source_age_s"] for r in self.records
                      if r.get("source_age_s") is not None)
        if not ages:
            return {"n": 0}
        def q(p):
            return round(ages[min(len(ages) - 1, int(p * len(ages)))], 4)
        return {"n": len(ages), "min": round(ages[0], 4), "p10": q(0.10),
                "median": q(0.50), "p90": q(0.90),
                "max": round(ages[-1], 4),
                "within_bound": sum(1 for a in ages if a <= MAX_SOURCE_AGE_S),
                "bound_s": MAX_SOURCE_AGE_S}

    def report(self) -> dict:
        led = self.shadow.ledger
        rc = led.reconciles()
        return {
            "loop": LOOP_VERSION,
            "started_at": self.started_at, "stopped_at": self.stopped_at,
            "runtime_s": _elapsed(self.started_at, self.stopped_at),
            "stream": self.stream.stats(),
            "counters": dict(self.counters),
            "decisions": len(self.records),
            "by_action": {k[len("action:"):]: v
                          for k, v in self.counters.items()
                          if k.startswith("action:")},
            "ineligible_by_reason": {k[len("ineligible:"):]: v
                                     for k, v in self.counters.items()
                                     if k.startswith("ineligible:")},
            "rejected_by_reason": {k[len("reject:"):]: v
                                   for k, v in self.counters.items()
                                   if k.startswith("reject:")},
            "blockers": {k[len("blocker:"):]: v
                         for k, v in self.counters.items()
                         if k.startswith("blocker:")},
            "freshness": self.freshness_distribution(),
            "accounting": {
                "ledger": rc, "fees_paid": led.fees_paid,
                "realized_pnl": led.realized_pnl,
                "deployed": self.shadow.deployed,
                "combined_exposure": self.shadow.combined_exposure,
                "worst_case_loss": self.shadow.worst_case_loss,
                "open_positions": len(
                    [p for p in self.shadow.positions.values()
                     if not p.flat]),
                "live_quotes": len(
                    [q for q in self.shadow.quotes.values()
                     if q["state"] in sl.LIVE_QUOTE_STATES]),
            },
            "touches_not_fills": {
                "trades_observed": len(self.touches),
                "note": ("a trade at our quoted price is a TOUCH. We hold "
                         "no order, so it establishes nothing about a "
                         "fill of ours"),
            },
            "orders_submitted": 0,
        }


def _elapsed(a, b):
    if not a or not b:
        return None
    from datetime import datetime as _dt
    return round((_dt.fromisoformat(b) - _dt.fromisoformat(a))
                 .total_seconds(), 3)


def build(key_id: str, secret_key: str, *, slugs, leg_of=None,
          state_path=None, opening_cash: float = 0.0):
    """Wire a stream to a loop. Subscribes; does not start the socket."""
    loop = LiveLoop(None, leg_of=leg_of, state_path=state_path,
                    opening_cash=opening_cash)
    stream = ms.MarketStream(key_id, secret_key,
                             on_book=loop.on_book, on_trade=loop.on_trade)
    loop.stream = stream
    stream.subscribe(list(slugs)[:uni.MAX_MARKETS])
    return loop, stream


def describe() -> dict:
    return {
        "loop": LOOP_VERSION,
        "chain": ("live observation -> normalization -> decision -> "
                  "shadow execution -> inventory -> outcome -> "
                  "reconciled accounting"),
        "driven_by": "book updates, not a timer",
        "submits_orders": False,
        "writes_accounting": False,
        "default_max_contracts": 0.0,
        "deployed": False,
        "in_workers_all": False,
        "max_source_age_s": MAX_SOURCE_AGE_S,
        "max_receipt_age_s": MAX_RECEIPT_AGE_S,
        "fills_are_simulated": (
            "a market trading at our quoted price does NOT establish "
            "that our order filled. We hold no order and queue position "
            "is unobservable. Trades are counted as TOUCHES"),
        "refusals_are_records": (
            "every refusal and its reason is persisted beside eligible "
            "actions; an engine that stores only its trades cannot be "
            "evaluated"),
    }


# ── the worker entry point ───────────────────────────────────────────

async def main() -> None:
    """The supervised loop `workers/all.py` would run. NOT REGISTERED.

    `all.py` supervises `(name, coroutine_fn)` pairs and restarts each
    one on crash. This is that coroutine. It is DELIBERATELY ABSENT
    from `LOOPS`, and a test asserts that: adding it is the deployment,
    and the deployment is a separate authorization.

    WHAT IT DOES WHEN RUN. Reads credentials from the service's own
    environment -- the same `PMUS_KEY_ID` / `PMUS_SECRET_KEY` the API
    and the workers already hold, never copied, never logged. Selects a
    bounded universe by liquidity and activity, subscribes, decides on
    updates, persists every decision and every refusal, and emits
    counters each cycle.

    IT PLACES NO ORDER. `max_contracts` is zero unless
    `BETTOR_LIVE_MAX_CONTRACTS` is set, and even a non-zero value only
    sizes a SIMULATED execution inside the shadow ledger.
    """
    from ..config import settings

    if not enabled():
        log.info("bettor_live_loop: disabled by %s=off", KILL_ENV)
        return

    cfg = settings()
    key_id = getattr(cfg, "pmus_key_id", None)
    secret = getattr(cfg, "pmus_secret_key", None)
    if not key_id or not secret:
        # NAMED, not silently degraded to a public client. A stream that
        # cannot authenticate produces no books, and "no books" must not
        # look like "a quiet market".
        log.error("bettor_live_loop: no PMUS credentials in this "
                  "environment; not starting")
        return

    slugs, legs = await _discover()
    loop, stream = build(key_id, secret, slugs=slugs, leg_of=legs,
                         state_path=os.environ.get("BETTOR_LIVE_STATE"),
                         opening_cash=float(
                             os.environ.get("BETTOR_LIVE_OPENING_CASH", "0")))
    loop.max_contracts = float(
        os.environ.get("BETTOR_LIVE_MAX_CONTRACTS", "0"))
    stream.start()
    log.info("bettor_live_loop: %d markets subscribed, fee schedule %s (%s)",
             len(slugs), loop.fees.source, loop.fees.status)

    import asyncio
    cycle = 0
    while enabled():
        loop.drain()
        for t in stream.drain_trades():
            loop.on_trade(t)
        cycle += 1
        if cycle % 150 == 0:            # ~30 s at POLL_S
            log.info("bettor_live_loop: %s",
                     json.dumps({"stream": stream.stats(),
                                 "decisions": len(loop.records),
                                 "by_action": loop.report()["by_action"],
                                 "freshness": loop.freshness_distribution()},
                                default=str))
        await asyncio.sleep(POLL_S)
    stream.stop()


async def _discover():
    """The bounded universe, from the venue's own listing.

    Liquidity and activity, per `bettor_universe`. A sweep is not a
    universe: measured p10 traded volume is 2.23 shares and only 116 of
    606 markets traded 1,000 or more in a day.
    """
    import asyncio

    from .. import pmus

    def _list():
        client = pmus._get_client()
        resp = client.markets.list({"active": True, "closed": False,
                                    "limit": 500})
        return list((resp or {}).get("markets") or [])

    try:
        rows = await asyncio.to_thread(_list)
    except Exception as exc:  # noqa: BLE001 -- named, never swallowed
        log.error("bettor_live_loop: market discovery failed: %s",
                  type(exc).__name__)
        return [], {}
    sel = uni.select(rows)
    log.info("bettor_live_loop: universe %s", json.dumps(
        {k: sel[k] for k in ("considered", "eligible", "selected",
                             "excluded_by_reason")}))
    legs = {d["slug"]: (d.get("outcome") or d.get("outcome_leg"))
            for d in sel["detail"]}
    return sel["slugs"], legs
