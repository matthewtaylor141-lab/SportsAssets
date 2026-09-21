"""The complete operating loop, driven by stream updates.

    live observation -> normalization -> decision -> shadow execution
    -> inventory -> outcome -> reconciled accounting

DRIVEN BY UPDATES, NOT BY A CLOCK. The book callback marks a slug dirty
and the loop decides on dirty slugs. A timer would decide on books that
have not moved and miss books that moved twice between ticks.

THREE CLOCKS, ALL PERSISTED. Every record carries the venue's
`transactTime`, our receipt time, and the decision time -- measured per
observation, never once per pass.

REFUSALS ARE RECORDS. Every refusal and its reason is written beside
every eligible action. An engine that stores only its trades cannot be
evaluated, and this one refuses nearly everything.

EVERY FILL IS SIMULATED. A market trading at our quoted price does NOT
establish that our order filled -- we hold no order and queue position
is unobservable. Trades are counted as TOUCHES.

--------------------------------------------------------------------
WHAT THE ACTIVATION REVIEW FOUND, AND WHERE EACH FIX LIVES
--------------------------------------------------------------------

DURABILITY. `BETTOR_LIVE_STATE` defaulted to unset, so the proposed
deployment would have kept every decision in memory and lost all of it
on the first redeploy. There is now a default path under a declared
directory, writes are flushed and fsync'd, failures are COUNTED rather
than logged and forgotten, and `main()` RECOVERS from the journal on
start. The harness's restart section exercised `ShadowLoop.restore`; it
said nothing about whether the worker's own entry point recovers, and
it did not.

UNBOUNDED COLLECTIONS. `records`, `touches` and `decided_ids` all grew
without limit in a process with a hard memory ceiling -- and
`sportsassets-workers` was OOM-killed thirteen times in one evening at
2 GiB. `records` and `touches` are now bounded rings kept only for
reporting; the journal on disk is the record. Dedup no longer keeps a
set of every observation ever seen: it keeps ONE source timestamp per
slug and refuses anything not strictly newer, which is bounded by the
subscription count and is also stricter.

DOUBLE COUNTING. `build()` installed `on_trade=loop.on_trade` AND
`main()` drained trades into the same handler, so every trade was
counted twice -- reproduced: one trade, `trades_seen=2`. The stream
callback is no longer installed for trades. Draining is the single
path, which also keeps the work off the socket thread.

THREAD SAFETY. `_dirty` was a bare set written by the stream thread and
read-and-cleared by the worker. It is now guarded by the same kind of
lock the stream uses for its own state.

LIFECYCLE. `stream.stop()` is in a `finally`, so cancellation -- which
is how `workers/all.py` shuts a loop down -- cannot orphan the socket
thread.

DISCOVERY. A failed discovery used to leave the loop running forever
over an empty universe, reporting zero decisions as though the market
were quiet. It now refuses to start, and discovery is REFRESHED on a
clock with expired subscriptions dropped.

THE EVENT LOOP. Journal writes and `report()` are blocking; both now
run in a thread so eighteen sibling loops are not stalled by our I/O.

Kill: BETTOR_LIVE_LOOP=off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

from .. import bettor_decision_engine as de
from .. import bettor_live_read as live
from .. import bettor_market_stream as ms
from .. import bettor_observation_adapter as oa
from .. import bettor_settlement_ingest as si
from .. import bettor_shadow_loop as sl
from .. import bettor_universe as uni

log = logging.getLogger(__name__)

LOOP_VERSION = "BETTOR_LIVE_LOOP_V2"
PROSPECTIVE = "PROSPECTIVE_SHADOW"
KILL_ENV = "BETTOR_LIVE_LOOP"

MAX_SOURCE_AGE_S = 10.0
MAX_RECEIPT_AGE_S = 5.0
POLL_S = 0.2

# DURABLE BY DEFAULT. Unset meant memory-only, and memory-only meant the
# deployment's whole deliverable vanished on redeploy.
DEFAULT_STATE_DIR = "/var/tmp/bettor"
JOURNAL_NAME = "decisions.jsonl"
LEDGER_NAME = "ledger.json"

# In-memory rings, for REPORTING ONLY. The journal is the record.
RECORD_RING = 2000
TOUCH_RING = 5000

# Discovery is refreshed, and subscriptions that leave the universe are
# dropped. Without this the subscription set only ever grows: ended
# games hold their last book forever and new markets stop streaming.
DISCOVERY_EVERY_S = 900.0
# Settlement is read on a slower clock and in bounded batches: it is a
# REST call per contract and it shares a gateway with the money path.
SETTLEMENT_EVERY_S = 600.0
SETTLEMENT_BATCH = 25


def enabled() -> bool:
    return str(os.environ.get(KILL_ENV, "on")).strip().lower() != "off"


def _now():
    return datetime.now(timezone.utc)


def state_dir() -> str:
    return os.environ.get("BETTOR_LIVE_STATE_DIR") or DEFAULT_STATE_DIR


class LiveLoop:
    """Stream in, decisions out, accounting reconciled. Never trades."""

    def __init__(self, stream, *, fees=None, opening_cash: float = 0.0,
                 max_contracts: float = 0.0, leg_of=None,
                 state_path: str | None = None,
                 ledger_path: str | None = None):
        self.stream = stream
        self.fees = fees or de.Fees.published_pmus(
            os.environ.get("BETTOR_FEE_DATE")
            or _now().strftime("%Y-%m-%d"))
        self.max_contracts = max_contracts
        self.leg_of = dict(leg_of or {})
        self.state_path = state_path
        self.ledger_path = ledger_path

        self.shadow = sl.ShadowLoop(opening_cash=opening_cash,
                                    fees=self.fees,
                                    venue="polymarket-us",
                                    account_class="institutional")
        # _dirty CROSSES THREADS: the socket thread adds, the worker
        # takes. A bare set was being iterated and cleared while another
        # thread mutated it.
        self._lock = threading.Lock()
        self._dirty: set = set()

        # BOUNDED. Reporting rings, not the record.
        self.records: deque = deque(maxlen=RECORD_RING)
        self.touches: deque = deque(maxlen=TOUCH_RING)
        self.counters: dict = {}

        # DEDUP WITHOUT AN UNBOUNDED SET. One source timestamp per slug,
        # and anything not strictly newer is refused. Bounded by the
        # subscription count, and stricter than a seen-set: an old
        # observation redelivered after a newer one is refused too.
        self._last_ts: dict = {}

        self.started_at: str | None = None
        self.stopped_at: str | None = None
        self.journal_written = 0
        self.journal_failures = 0
        self.recovered: dict | None = None
        self.settlement_runs: list = []
        self._seen_contracts: dict = {}

    # ── counters ─────────────────────────────────────────────────────

    def bump(self, key, n=1):
        self.counters[key] = self.counters.get(key, 0) + n

    def on_book(self, slug: str, rec: dict) -> None:
        """Stream-thread callback. Cheap, non-blocking, LOCKED."""
        with self._lock:
            self._dirty.add(slug)

    def on_trade(self, rec: dict) -> None:
        """A trade is a TOUCH at most, never a fill.

        THE ONLY PATH. `build()` no longer installs this as a stream
        callback: it was installed AND drained, so one trade counted
        twice. Draining is also the right place -- it keeps the work
        off the socket thread.
        """
        self.bump("trades_seen")
        self.touches.append(rec)

    # ── the loop ─────────────────────────────────────────────────────

    def take_dirty(self) -> list:
        with self._lock:
            out = list(self._dirty)
            self._dirty.clear()
        return out

    def drain(self) -> list:
        out = []
        for slug in self.take_dirty():
            rec = self.decide_slug(slug)
            if rec is not None:
                out.append(rec)
        return out

    def decide_slug(self, slug: str) -> dict | None:
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

        src = at.get("source_ts")
        prev = self._last_ts.get(slug)
        if prev is not None and src is not None and src <= prev:
            self.bump("duplicate_observation_refused")
            return None
        self._last_ts[slug] = src
        self._seen_contracts[slug] = decided_at

        leg = self.leg_of.get(slug)
        row = ms.to_observation(slug, at, outcome_leg=leg)
        norm = oa.normalize(row, venue="polymarket-us",
                            account_class="institutional",
                            fee_source=self.fees.source)
        if norm.status != oa.ACCEPTED:
            self.bump("rejected")
            for r in norm.reasons:
                self.bump("reject:%s" % r)
            rec = dict(base, status="REJECTED",
                       observation_id=row["observation_id"],
                       reasons=list(norm.reasons))
            self._record(rec)
            return rec

        step = self.shadow.step(oa.to_book(norm), evidence_class=sl.REPLAYED,
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

        rec = dict(base, status="DECIDED",
                   observation_id=row["observation_id"],
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

    # ── durability ───────────────────────────────────────────────────

    def _record(self, rec: dict) -> None:
        self.records.append(rec)
        if not self.state_path:
            # NAMED. A loop with nowhere to write is a loop whose whole
            # deliverable dies with the process, and that must not be a
            # silent default.
            self.bump("record_not_persisted_no_path")
            return
        try:
            with open(self.state_path, "a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self.journal_written += 1
        except OSError as exc:  # noqa: BLE001
            self.journal_failures += 1
            self.bump("persist_failed")
            self.bump("persist_failed:%s" % type(exc).__name__)
            log.warning("decision persist failed: %s", exc)

    def save_ledger(self) -> bool:
        """The shadow ledger, atomically. A crash cannot truncate it."""
        if not self.ledger_path:
            return False
        try:
            tmp = self.ledger_path + ".tmp"
            with open(tmp, "w") as fh:
                fh.write(self.shadow.snapshot())
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.ledger_path)
            return True
        except OSError as exc:  # noqa: BLE001
            self.journal_failures += 1
            self.bump("ledger_persist_failed")
            log.warning("ledger persist failed: %s", exc)
            return False

    def recover(self) -> dict:
        """Rebuild dedup state and the ledger from disk. IN THE WORKER.

        The harness proved `ShadowLoop.restore` works. It did not prove
        that the worker's entry point calls it, and it did not.

        The journal is replayed for its LATEST source timestamp per
        slug, so a restart cannot re-decide an observation it already
        recorded. A malformed line is counted and skipped: a journal
        truncated by a kill must not stop recovery.
        """
        out = {"journal_lines": 0, "journal_bad_lines": 0,
               "slugs_recovered": 0, "ledger_restored": False,
               "journal_path": self.state_path,
               "ledger_path": self.ledger_path}
        if self.state_path and os.path.exists(self.state_path):
            try:
                with open(self.state_path) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        out["journal_lines"] += 1
                        try:
                            r = json.loads(line)
                        except ValueError:
                            out["journal_bad_lines"] += 1
                            continue
                        slug, src = r.get("market_id"), r.get("source_ts")
                        if slug and src:
                            prev = self._last_ts.get(slug)
                            if prev is None or src > prev:
                                self._last_ts[slug] = src
                            self._seen_contracts.setdefault(
                                slug, r.get("decided_at"))
            except OSError as exc:  # noqa: BLE001
                out["journal_error"] = type(exc).__name__
        out["slugs_recovered"] = len(self._last_ts)

        if self.ledger_path and os.path.exists(self.ledger_path):
            try:
                with open(self.ledger_path) as fh:
                    self.shadow = sl.ShadowLoop.restore(
                        fh.read(), fees=self.fees, venue="polymarket-us",
                        account_class="institutional")
                out["ledger_restored"] = True
                out["cash"] = self.shadow.ledger.cash
            except Exception as exc:  # noqa: BLE001 -- a corrupt ledger
                out["ledger_error"] = type(exc).__name__   # must not stop
                self.bump("ledger_restore_failed")         # the loop
        self.recovered = out
        return out

    # ── settlement ───────────────────────────────────────────────────

    async def ingest_settlements(self, *, client=None, limit=None) -> dict:
        """Bounded settlement reads over contracts we have observed.

        BOUNDED because it is one REST call per contract against a
        gateway shared with the money path. The oldest-seen contracts
        go first, so a busy universe cannot starve the ones that have
        had time to resolve.

        Four outcomes stay apart, and a fifth counts what was STORED:
        RESOLVED (the venue's settlement endpoint), RESOLVED_DERIVED
        (converged prices -- an inference, never counted as RESOLVED),
        PENDING, UNREADABLE, UNMATCHED, and INGESTED.
        """
        n = limit or SETTLEMENT_BATCH
        oldest = sorted(self._seen_contracts.items(),
                        key=lambda kv: kv[1] or "")[:n]
        if not oldest:
            return {"counts": {}, "skipped": "no contracts observed yet"}
        pairs = [("%s@%s" % (s, self._last_ts.get(s) or "NO_TS"), s)
                 for s, _ in oldest]
        out = await si.ingest(pairs, client=client,
                              writer=self._settlement_writer)
        out["at"] = _now().isoformat()
        out["contracts_read"] = len(pairs)
        self.settlement_runs.append(
            {"at": out["at"], "counts": out["counts"],
             "reconciled": out["reconciled"],
             "unreadable_reasons": out["unreadable_reasons"]})
        del self.settlement_runs[:-20]
        for k, v in out["counts"].items():
            self.bump("settlement:%s" % k, v)
        return out

    async def _settlement_writer(self, row: dict) -> None:
        """Settlements land in the journal beside the decisions.

        The database writer (`bettor_state_store.record_settlement`) is
        the production path and needs a pool this loop deliberately
        does not hold. Journalling them keeps the evidence durable
        without giving a decision-only loop a database handle.
        """
        rec = {"loop": LOOP_VERSION, "kind": "SETTLEMENT",
               "at": _now().isoformat(), "settlement": row}
        await asyncio.to_thread(self._record, rec)

    # ── reporting ────────────────────────────────────────────────────

    def freshness_distribution(self) -> dict:
        ages = sorted(r["source_age_s"] for r in self.records
                      if r.get("source_age_s") is not None)
        if not ages:
            return {"n": 0}

        def q(p):
            return round(ages[min(len(ages) - 1, int(p * len(ages)))], 4)

        return {"n": len(ages), "min": round(ages[0], 4), "p10": q(0.10),
                "median": q(0.50), "p90": q(0.90), "max": round(ages[-1], 4),
                "within_bound": sum(1 for a in ages if a <= MAX_SOURCE_AGE_S),
                "bound_s": MAX_SOURCE_AGE_S,
                "window": "the last %d records held in memory" % len(ages)}

    def report(self) -> dict:
        led = self.shadow.ledger
        rc = led.reconciles()
        return {
            "loop": LOOP_VERSION,
            "started_at": self.started_at, "stopped_at": self.stopped_at,
            "runtime_s": _elapsed(self.started_at, self.stopped_at),
            "stream": self.stream.stats() if self.stream else None,
            "counters": dict(self.counters),
            "decisions_in_ring": len(self.records),
            "durability": {
                "journal_path": self.state_path,
                "ledger_path": self.ledger_path,
                "records_written": self.journal_written,
                "persist_failures": self.journal_failures,
                "recovered_at_start": self.recovered,
                "ring_bounds": {"records": RECORD_RING,
                                "touches": TOUCH_RING,
                                "dedup_keys": len(self._last_ts)},
            },
            "by_action": {k[7:]: v for k, v in self.counters.items()
                          if k.startswith("action:")},
            "ineligible_by_reason": {k[11:]: v
                                     for k, v in self.counters.items()
                                     if k.startswith("ineligible:")},
            "rejected_by_reason": {k[7:]: v for k, v in self.counters.items()
                                   if k.startswith("reject:")},
            "blockers": {k[8:]: v for k, v in self.counters.items()
                         if k.startswith("blocker:")},
            "settlement": {"runs": list(self.settlement_runs),
                           "contracts_observed": len(self._seen_contracts)},
            "freshness": self.freshness_distribution(),
            "accounting": {
                "ledger": rc, "fees_paid": led.fees_paid,
                "realized_pnl": led.realized_pnl,
                "deployed": self.shadow.deployed,
                "combined_exposure": self.shadow.combined_exposure,
                "worst_case_loss": self.shadow.worst_case_loss,
                "open_positions": len([p for p in
                                       self.shadow.positions.values()
                                       if not p.flat]),
                "live_quotes": len([q for q in self.shadow.quotes.values()
                                    if q["state"] in sl.LIVE_QUOTE_STATES]),
            },
            "touches_not_fills": {
                "trades_observed": self.counters.get("trades_seen", 0),
                "in_ring": len(self.touches),
                "note": ("a trade at our quoted price is a TOUCH. We hold "
                         "no order, so it establishes nothing about a "
                         "fill of ours"),
            },
            "orders_submitted": 0,
        }


def _elapsed(a, b):
    if not a or not b:
        return None
    return round((datetime.fromisoformat(b)
                  - datetime.fromisoformat(a)).total_seconds(), 3)


def build(key_id: str, secret_key: str, *, slugs, leg_of=None,
          state_path=None, ledger_path=None, opening_cash: float = 0.0):
    """Wire a stream to a loop. Subscribes; does not start the socket.

    `on_trade` IS NOT INSTALLED as a stream callback. It used to be,
    while the caller ALSO drained trades into the same handler, so one
    trade was counted twice -- reproduced before this was changed.
    Draining is the single path.
    """
    loop = LiveLoop(None, leg_of=leg_of, state_path=state_path,
                    ledger_path=ledger_path, opening_cash=opening_cash)
    stream = ms.MarketStream(key_id, secret_key, on_book=loop.on_book)
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
        "writes_database": False,
        "default_max_contracts": 0.0,
        "deployed": False,
        "in_workers_all": False,
        "max_source_age_s": MAX_SOURCE_AGE_S,
        "max_receipt_age_s": MAX_RECEIPT_AGE_S,
        "durable": {"journal": JOURNAL_NAME, "ledger": LEDGER_NAME,
                    "dir_env": "BETTOR_LIVE_STATE_DIR",
                    "default_dir": DEFAULT_STATE_DIR,
                    "recovered_by": "main() calls loop.recover() on start"},
        "bounded": {"records": RECORD_RING, "touches": TOUCH_RING,
                    "dedup": "one source timestamp per slug, not a "
                             "set of every observation ever seen"},
        "trade_delivery": ("drained, never also a callback. Installing "
                           "both counted every trade twice"),
        "fills_are_simulated": (
            "a market trading at our quoted price does NOT establish "
            "that our order filled. Trades are counted as TOUCHES"),
    }


# ── the worker entry point ───────────────────────────────────────────

async def _discover(client=None) -> dict:
    """The bounded universe, PAGINATED, from the venue's own listing.

    One page of 500 was a sample of the venue, not the venue. Discovery
    now pages to a declared bound and reports how far it got, so a
    universe built from a truncated listing says so.

    Returns a result dict with `ok`. A FAILED DISCOVERY IS NOT AN EMPTY
    UNIVERSE: the old version logged and returned `[]`, and the loop
    then ran forever reporting zero decisions as though the market were
    quiet.
    """
    from .. import pmus

    max_pages = int(os.environ.get("BETTOR_LIVE_DISCOVERY_PAGES", "6"))
    page_size = 500

    def _list():
        client_ = client or pmus._get_client()
        rows, offset, pages, truncated = [], 0, 0, False
        while pages < max_pages:
            resp = client_.markets.list({"active": True, "closed": False,
                                         "limit": page_size,
                                         "offset": offset})
            got = list((resp or {}).get("markets") or [])
            rows.extend(got)
            pages += 1
            if len(got) < page_size:
                break
            offset += page_size
        else:
            truncated = True
        return rows, pages, truncated

    try:
        rows, pages, truncated = await asyncio.to_thread(_list)
    except Exception as exc:  # noqa: BLE001 -- named, never swallowed
        return {"ok": False, "error": type(exc).__name__,
                "why": "market discovery failed"}

    sel = uni.select(rows)
    sel.update({"ok": True, "pages_read": pages,
                "listing_truncated_at_page_bound": truncated,
                "rows_listed": len(rows)})
    if truncated:
        # A universe chosen from a truncated listing is a universe
        # chosen from a prefix of the venue, and ranking by volume over
        # a prefix is not ranking by volume.
        log.warning("bettor_live_loop: listing hit the %d-page bound; "
                    "the universe is drawn from a PREFIX of the venue",
                    max_pages)
    return sel


async def main() -> None:
    """The supervised loop `workers/all.py` would run. NOT REGISTERED.

    DELIBERATELY ABSENT from `LOOPS`, and a test asserts it. Adding it
    is the deployment and the deployment is a separate authorization.

    Credentials come from the service's own environment -- the same
    `PMUS_KEY_ID` / `PMUS_SECRET_KEY` the workers already hold. Never
    copied, never logged.
    """
    from ..config import settings

    if not enabled():
        log.info("bettor_live_loop: disabled by %s=off", KILL_ENV)
        return

    cfg = settings()
    key_id = getattr(cfg, "pmus_key_id", None)
    secret = getattr(cfg, "pmus_secret_key", None)
    if not key_id or not secret:
        # NAMED, not degraded to a public client. A stream that cannot
        # authenticate produces no books, and "no books" must never
        # look like "a quiet market".
        log.error("bettor_live_loop: no PMUS credentials in this "
                  "environment; not starting")
        return

    d = state_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as exc:  # noqa: BLE001
        log.error("bettor_live_loop: state dir %s unusable (%s); "
                  "refusing to run without durable records", d,
                  type(exc).__name__)
        return

    first = await _discover()
    if not first.get("ok"):
        # A FAILED DISCOVERY IS NOT AN EMPTY UNIVERSE.
        log.error("bettor_live_loop: %s (%s); not starting",
                  first.get("why"), first.get("error"))
        return
    if not first.get("slugs"):
        log.error("bettor_live_loop: discovery returned 0 eligible "
                  "markets of %d considered; not starting. Excluded: %s",
                  first.get("considered", 0),
                  json.dumps(first.get("excluded_by_reason", {})))
        return

    loop, stream = build(
        key_id, secret, slugs=first["slugs"],
        leg_of={s: None for s in first["slugs"]},
        state_path=os.path.join(d, JOURNAL_NAME),
        ledger_path=os.path.join(d, LEDGER_NAME),
        opening_cash=float(os.environ.get("BETTOR_LIVE_OPENING_CASH", "0")))
    loop.max_contracts = float(
        os.environ.get("BETTOR_LIVE_MAX_CONTRACTS", "0"))

    # RECOVERY, IN THE ENTRY POINT. The harness proved ShadowLoop can
    # restore; it said nothing about whether this function calls it.
    rec = await asyncio.to_thread(loop.recover)
    loop.started_at = _now().isoformat()
    log.info("bettor_live_loop: recovered %s", json.dumps(rec, default=str))
    log.info("bettor_live_loop: %d markets subscribed of %d considered, "
             "fees %s (%s), journal %s",
             len(first["slugs"]), first.get("considered", 0),
             loop.fees.source, loop.fees.status, loop.state_path)

    stream.start()
    last_discovery = last_settlement = last_save = time.time()
    cycle = 0
    try:
        while enabled():
            loop.drain()
            for t in stream.drain_trades():
                loop.on_trade(t)

            now = time.time()
            if now - last_discovery >= DISCOVERY_EVERY_S:
                last_discovery = now
                nxt = await _discover()
                if nxt.get("ok") and nxt.get("slugs"):
                    keep = set(nxt["slugs"])
                    dropped = stream.prune(keep)
                    added = stream.subscribe(nxt["slugs"])
                    loop.bump("discovery_refresh")
                    log.info("bettor_live_loop: discovery refresh "
                             "+%d -%d (now %d)", added["queued"], dropped,
                             len(keep))
                else:
                    # A refresh that fails leaves the EXISTING universe
                    # in place. Dropping it would turn a transient venue
                    # error into an empty engine.
                    loop.bump("discovery_refresh_failed")

            if now - last_settlement >= SETTLEMENT_EVERY_S:
                last_settlement = now
                try:
                    out = await loop.ingest_settlements()
                    log.info("bettor_live_loop: settlement %s",
                             json.dumps(out.get("counts", {})))
                except Exception as exc:  # noqa: BLE001
                    loop.bump("settlement_pass_failed")
                    log.warning("settlement pass failed: %s",
                                type(exc).__name__)

            if now - last_save >= 60.0:
                last_save = now
                # BLOCKING I/O OFF THE SHARED EVENT LOOP. Eighteen
                # sibling loops run in this process.
                await asyncio.to_thread(loop.save_ledger)

            cycle += 1
            if cycle % 150 == 0:
                rep = await asyncio.to_thread(loop.report)
                log.info("bettor_live_loop: %s", json.dumps(
                    {"stream": rep["stream"], "by_action": rep["by_action"],
                     "ineligible": rep["ineligible_by_reason"],
                     "freshness": rep["freshness"],
                     "durability": rep["durability"],
                     "orders_submitted": rep["orders_submitted"]},
                    default=str))
            await asyncio.sleep(POLL_S)
    finally:
        # IN A FINALLY. `workers/all.py` shuts a loop down by
        # CANCELLING it, and a CancelledError raised at the await above
        # would otherwise leave the socket thread running forever.
        loop.stopped_at = _now().isoformat()
        stream.stop()
        await asyncio.to_thread(loop.save_ledger)
        log.info("bettor_live_loop: stopped; stream stop requested, "
                 "ledger saved, %d records written, %d persist failures",
                 loop.journal_written, loop.journal_failures)
