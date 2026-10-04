"""CHAOS / RECOVERY OF THE ONE VENUE STATE MACHINE (program section 28).

The actual lane (execution_intent.ActualLane) and the execution mirror's
runner (execmirror.Mirror) are the venue state machine the canonical SMALL
LIVE adapter would drive. SMALL LIVE is SHADOW and legacy origination is
retired (execmirror.Venue.place raises LegacyOriginationRetired), so the
machine is driven here through its existing test double -- a fake venue
object handed to `Mirror(venue_factory=...)`, exactly as tests/test_execmirror
does -- and the faults are injected at the two boundaries where production
fails: the VENUE TRANSPORT (a timeout after the request left, a task killed
mid-call, a venue that cancels while it fills) and the DATABASE (a write that
fails after the venue accepted). No real venue, no credential, no network.

THE PROPERTIES, each against the real code and Postgres (RN1X_TEST_DSN):

  ambiguous submission   a timeout after send leaves the order UNKNOWN; it is
                         never re-sent (ticks, restarts, re-dispatch); it is
                         reconciled against the venue -- the open orders, and
                         when the order is no longer open (an IOC that traded)
                         the account's own TRADE LOG -- and only evidence of
                         absence turns it REJECTED. An unreadable trade log or
                         an own trade that cannot be attributed keeps it
                         UNKNOWN: unknown is never "nothing happened".
  duplicate fills        re-reading the venue's order record, from a stale
                         caller snapshot or twice over, never books a fill
                         twice; a stale (lower) read never moves cum backwards.
  worker death           a lane killed mid-submit leaves the claim SUBMITTING;
                         after the lease (SUBMITTING_STALE_S) recovery decides
                         it from venue evidence; the intent's single claim
                         makes a second submission impossible, also for two
                         lanes racing on one intent.
  DB failure after       the venue accepted and the acknowledgement write
  venue accept           failed: the venue order is recovered and recorded
                         (fills from the venue's record, Xavier's handoff),
                         never orphaned, never re-sent.
  partial fill during    a cancel that the venue is still working keeps the
  cancel                 row CANCEL_REQUESTED (it is not forgotten), and the
                         fills that landed before the cancel are counted.
  restart with UNKNOWN   a fresh runner resolves every UNKNOWN row from venue
                         evidence and sends nothing.

Every row this file writes is removed at the end of each test and the control
row is restored, so later files (test_live_parity's cutover checks count
venue orders) see the database they expect.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import time
import uuid
from decimal import Decimal

import pytest

from sportsassets import execmirror as M
from sportsassets import execmirror_probe as EP
from sportsassets import execution_intent as EI

try:    # pytest collects tests/ as a package; unittest discovery does not
    from tests import admission_fixture as AF
    from tests.test_execmirror import KID, SEC
except ImportError:                                           # pragma: no cover
    import admission_fixture as AF
    from test_execmirror import KID, SEC

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

CG = "PINNACLE_COMPLETED_GAME_PAPER"
CG3 = "PINNACLE_COMPLETED_GAME_PAPER_V3"
IOC, GTD = M.TIF["IOC"], M.TIF["GTD"]


# ─────────────────────────── the venue double ───────────────────────────

class VenueTimeout(Exception):
    """What the SDK raises when the response never came (APITimeoutError
    carries no HTTP status): the request may or may not have been acted on."""


class VenueRateLimited(Exception):
    status_code = 429


class ChaosVenue:
    """A venue that keeps its own order records and trade log, and fails on
    cue. `script` is consumed one entry per `place`:
        {"fill": n}                      accept; n contracts trade at once
        {"timeout_after_send": True,
         "fill": n, "rest": bool}        the venue ACTS (creates, maybe fills),
                                         then the caller sees a timeout
        {"die_before_send": True}        the calling task is killed before
                                         the request leaves (nothing exists)
        {"die_after_send": True,
         "rest": bool, "fill": n}        the venue acts, then the task dies
    """

    def __init__(self):
        self.orders: dict = {}
        self.created_at: dict = {}
        self.placed: list = []
        self.script: list = []
        self.cancel_calls: list = []
        self.pending_cancel: set = set()
        self.fill_on_cancel: dict = {}
        self.trades_unreadable = False
        self.foreign_trades: list = []
        self.trade_reads = 0
        self.bp = 100.0
        self._n = 0

    # the order record -------------------------------------------------
    def _new(self, params, *, rest=None):
        self._n += 1
        vid = "cv%d" % self._n
        self.orders[vid] = {
            "id": vid, "marketSlug": params["marketSlug"],
            "intent": params["intent"], "quantity": params["quantity"],
            "price": params["price"], "tif": params["tif"],
            "state": "ORDER_STATE_NEW", "cumQuantity": 0, "avgPx": None,
            "commissionNotionalTotalCollected": {"value": "0"}}
        self.created_at[vid] = time.time()
        return vid

    def fill(self, vid, qty, fee_per=0.01):
        o = self.orders[vid]
        o["cumQuantity"] = min(o["quantity"], o["cumQuantity"] + qty)
        o["avgPx"] = o["price"]
        o["commissionNotionalTotalCollected"] = {
            "value": "%.4f" % (o["cumQuantity"] * fee_per)}
        if o["state"] in ("ORDER_STATE_NEW", "ORDER_STATE_PARTIALLY_FILLED"):
            o["state"] = ("ORDER_STATE_FILLED" if o["cumQuantity"] >= o["quantity"]
                          else "ORDER_STATE_PARTIALLY_FILLED")

    def _act(self, params, b):
        vid = self._new(params)
        if b.get("fill"):
            self.fill(vid, b["fill"])
        immediate = params["tif"] in (IOC, M.TIF["FOK"])
        if immediate and not b.get("rest") and \
                self.orders[vid]["state"] != "ORDER_STATE_FILLED":
            self.orders[vid]["state"] = "ORDER_STATE_CANCELED"
        return vid

    # the venue surface execmirror.Venue exposes -------------------------
    def place(self, params):
        b = self.script.pop(0) if self.script else {}
        if b.get("die_before_send"):
            raise asyncio.CancelledError()
        self.placed.append(params)
        vid = self._act(params, b)
        if b.get("timeout_after_send"):
            raise VenueTimeout("the response never came")
        if b.get("die_after_send"):
            raise asyncio.CancelledError()
        return {"id": vid, "executions": []}

    def cancel(self, vid, slug):
        self.cancel_calls.append(vid)
        if vid in self.fill_on_cancel:
            self.fill(vid, self.fill_on_cancel.pop(vid))
        o = self.orders[vid]
        if o["state"] == "ORDER_STATE_FILLED":
            raise Exception("order already filled")
        o["state"] = ("ORDER_STATE_PENDING_CANCEL" if vid in self.pending_cancel
                      else "ORDER_STATE_CANCELED")

    def finish_cancel(self, vid):
        self.orders[vid]["state"] = "ORDER_STATE_CANCELED"

    def cancel_all(self):
        return {"canceledOrderIds": []}

    def close(self, slug, bips=300):
        return {"id": "close-" + slug}

    def order(self, vid):
        return dict(self.orders[vid])

    def open_orders(self, slugs=None):
        return [dict(o) for o in self.orders.values()
                if o["state"] in ("ORDER_STATE_NEW", "ORDER_STATE_PARTIALLY_FILLED",
                                  "ORDER_STATE_PENDING_CANCEL")
                and (not slugs or o["marketSlug"] in slugs)]

    def own_trades(self, slug, since):
        """The account's own trades on one market since `since` (epoch s):
        one row per executed own order, {order_id, intent, price, quantity,
        traded_qty, at}. Raises when the log cannot be read."""
        self.trade_reads += 1
        if self.trades_unreadable:
            raise RuntimeError("activities endpoint unavailable")
        out = []
        for vid, o in self.orders.items():
            if o["marketSlug"] == slug and o["cumQuantity"] > 0 and \
                    self.created_at[vid] >= since:
                out.append({"order_id": vid, "intent": o["intent"],
                            "price": o["price"], "quantity": o["quantity"],
                            "traded_qty": o["cumQuantity"],
                            "at": self.created_at[vid]})
        out.extend(t for t in self.foreign_trades if t.get("slug", slug) == slug)
        return out

    def balances(self):
        return [{"currency": "USD", "currentBalance": self.bp,
                 "buyingPower": self.bp}]

    def positions(self):
        net = {}
        for o in self.orders.values():
            sgn = 1 if "BUY" in o["intent"] else -1
            net[o["marketSlug"]] = net.get(o["marketSlug"], 0) + sgn * o["cumQuantity"]
        return {s: {"netPosition": str(n), "cashValue": {"value": "0"},
                    "cost": {"value": "0"}, "expired": False}
                for s, n in net.items() if n}

    def quote(self, slug):
        return {"bid": "0.40", "ask": "0.45", "state": "MARKET_STATE_OPEN",
                "error": None}

    def bbo(self, slug):
        return {"bestBid": {"value": "0.40"}, "bestAsk": {"value": "0.45"}}


# ─────────────────────────── the environment ────────────────────────────

class Env:
    pass


async def _env(monkeypatch) -> Env:
    import asyncpg
    AF.approve_test_rule(monkeypatch)
    monkeypatch.setenv(EP.KEY_ID_ENV, KID)
    monkeypatch.setenv(EP.SECRET_ENV, SEC)
    e = Env()
    e.conn = await asyncpg.connect(DSN)
    e.ctl_before = dict(await e.conn.fetchrow(
        "SELECT * FROM execmirror_control WHERE id = 1") or {})
    await _wipe(e.conn)
    await e.conn.execute(
        """UPDATE execmirror_control SET enabled = true, stopped = false,
             stop_done_at = NULL, flatten_on_stop = false,
             cutover_at = now() - interval '5 seconds',
             account_fingerprint = $1, baseline = '{}'::jsonb,
             max_order_usd = 25, scale = 1000 WHERE id = 1""",
        EP.fingerprint(KID))
    e.venue = ChaosVenue()
    e.mirror = M.Mirror(lambda: e.venue, paper_account="paper_test_chaos_none")
    e.lane = EI.ActualLane(None, e.mirror)
    await e.mirror.snapshot(e.conn, await M.control(e.conn))
    e.intents = []
    return e


async def _wipe(conn) -> None:
    await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                       "smalllive_reconciliations")
    await conn.execute("TRUNCATE execmirror_fills, execmirror_events, "
                       "execmirror_snapshots, execmirror_orders")
    await conn.execute("DELETE FROM execution_intents")


async def _close(e: Env) -> None:
    try:
        await _wipe(e.conn)
        c = e.ctl_before
        if c:
            await e.conn.execute(
                """UPDATE execmirror_control SET enabled = $1, stopped = $2,
                     stop_done_at = $3, flatten_on_stop = $4, cutover_at = $5,
                     account_fingerprint = $6, max_order_usd = $7, scale = $8
                   WHERE id = 1""",
                c["enabled"], c["stopped"], c["stop_done_at"],
                c["flatten_on_stop"], c["cutover_at"], c["account_fingerprint"],
                c["max_order_usd"], c["scale"])
    finally:
        await e.conn.close()


async def _intent(e: Env, *, qty=3000, wire=0.55, tif="IOC", otype="MARKETABLE",
                  slug=None) -> dict:
    did = "chaos_dec_%s" % uuid.uuid4().hex[:12]
    slug = slug or "aec-mlb-chaos-%s" % uuid.uuid4().hex[:6]
    now = time.time()
    it = await EI.create(
        e.conn, decision_id=did, valuation_id=None, strategy=CG,
        policy_version=CG3, slug=slug, order_intent="ORDER_INTENT_BUY_LONG",
        holding_side="LONG", group_id="chaos_group_%s" % did[-8:],
        order_type=otype, time_in_force=tif, paper_target_qty=qty,
        limit_price=wire, wire_price=wire, book_obs_id=None,
        book_observed_at=now, decided_at=now,
        evidence={"admission_facts": AF.admissible_facts(
            slug=slug, order_intent="ORDER_INTENT_BUY_LONG", wire=wire)},
        timeline={})
    assert it["live_eligible"] and it["actual_state"] == EI.A_DISPATCHED, it
    e.intents.append(it["intent_id"])
    return it


async def _row(e: Env, intent_id: str) -> dict | None:
    r = await e.conn.fetchrow(
        "SELECT * FROM execmirror_orders WHERE execution_intent_id = $1",
        intent_id)
    return None if r is None else dict(r)


async def _intent_row(e: Env, intent_id: str) -> dict:
    return dict(await e.conn.fetchrow(
        "SELECT * FROM execution_intents WHERE intent_id = $1", intent_id))


async def _fills(e: Env, mirror_id: str) -> Decimal:
    return Decimal(str(await e.conn.fetchval(
        "SELECT coalesce(sum(qty), 0) FROM execmirror_fills WHERE mirror_id = $1",
        mirror_id)))


async def _age(e: Env, mirror_id: str, seconds: float) -> None:
    """THE LEASE / GRACE WINDOW PASSES: the claim's start moves into the past
    (the clock the recovery reads is the database's own now())."""
    await e.conn.execute(
        "UPDATE execmirror_orders SET submit_started_at = now() - "
        "make_interval(secs => $2) WHERE mirror_id = $1", mirror_id,
        float(seconds))


def _later(e: Env, seconds: float) -> None:
    """The runner's clock moves on (a row the log could not decide is asked
    again only after RECONCILE_RECHECK_S)."""
    base = e.mirror._now
    e.mirror._now = lambda: base() + seconds


def _err(row) -> dict:
    v = row.get("error")
    return json.loads(v) if isinstance(v, str) else (v or {})


async def _events(e: Env, mirror_id: str) -> list:
    return [r["kind"] for r in await e.conn.fetch(
        "SELECT kind FROM execmirror_events WHERE mirror_id = $1 ORDER BY event_id",
        mirror_id)]


PAST_GRACE = M.UNKNOWN_GRACE_S + M.SUBMITTING_STALE_S + 60


async def _runner_pass(mirror, conn) -> None:
    """The runner's recovery-relevant steps of one tick, in tick order:
    reconcile ambiguous claims, read every working order's venue record,
    hand filled positions to Xavier. (The full tick also plans orphan
    closes; the synthetic decisions here have no paper sibling, which in
    production always exists, so that step would read a phantom 'paper
    closed' and is left out of the recovery proofs.)"""
    await mirror.recover(conn)
    await mirror.poll(conn)
    await mirror.live_handoffs(conn)


# ─────────────────────────── ambiguous submission ───────────────────────

@pg
async def test_a_timeout_after_send_stays_unknown_is_never_resent_and_the_trade_log_recovers_the_fill(
        monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000)                    # 3 live contracts, IOC
        e.venue.script = [{"timeout_after_send": True, "fill": 3}]
        got = await e.lane._run(e.conn, it["intent_id"])
        assert got["state"] == EI.A_UNKNOWN
        r = await _row(e, it["intent_id"])
        assert r["state"] == "UNKNOWN" and r["venue_order_id"] is None
        assert (await _intent_row(e, it["intent_id"]))["actual_state"] == EI.A_UNKNOWN
        # the order traded and is no longer open: inside the grace window the
        # runner keeps asking and never sends again
        for _ in range(4):
            await e.mirror.tick(e.conn)
        assert len(e.venue.placed) == 1
        assert (await _row(e, it["intent_id"]))["state"] == "UNKNOWN"
        # a re-dispatched intent finds its one claim (and is put back where
        # it was: the duplicate path writes nothing)
        await e.conn.execute("UPDATE execution_intents SET actual_state = "
                             "'DISPATCHED' WHERE intent_id = $1", it["intent_id"])
        assert (await e.lane._run(e.conn, it["intent_id"]))["state"] == "DUPLICATE"
        assert len(e.venue.placed) == 1
        await e.conn.execute("UPDATE execution_intents SET actual_state = "
                             "'UNKNOWN' WHERE intent_id = $1", it["intent_id"])
        # past the grace window: the order is not open, so the account's own
        # trade log is read -- the venue order is found and its fills booked
        await _age(e, r["mirror_id"], PAST_GRACE)
        await e.mirror.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "FILLED", (r["state"], _err(r))
        assert r["venue_order_id"] == "cv1"
        assert await _fills(e, r["mirror_id"]) == Decimal(3)
        assert (await M.live_inventory(e.conn, r["group_id"]))["held"] == 3
        assert "RECONCILED_FROM_TRADE_LOG" in await _events(e, r["mirror_id"])
        it2 = await _intent_row(e, it["intent_id"])
        assert it2["actual_state"] == EI.A_SUBMITTED and it2["actual_refusal"] is None
        # the recovered position is handed to Xavier: never orphaned
        await _runner_pass(e.mirror, e.conn)
        h = await e.conn.fetchrow("SELECT * FROM smalllive_handoffs WHERE "
                                  " group_id = $1", r["group_id"])
        assert h is not None and h["state"] == "OPEN" and h["live_held"] == 3
        assert len(e.venue.placed) == 1
    finally:
        await _close(e)


@pg
async def test_only_evidence_of_absence_rejects_an_ambiguous_order(monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000)
        # the venue cancelled the IOC unfilled: nothing open, nothing traded
        e.venue.script = [{"timeout_after_send": True}]
        await e.lane._run(e.conn, it["intent_id"])
        r = await _row(e, it["intent_id"])
        await _age(e, r["mirror_id"], PAST_GRACE)
        # 1 · the trade log cannot be read: NOT a rejection
        e.venue.trades_unreadable = True
        await e.mirror.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "UNKNOWN", (r["state"], _err(r))
        assert _err(r)["code"] == M.R_TRADE_LOG_UNREADABLE
        # still counted as in flight: unknown is never zero
        assert await e.conn.fetchval(
            "SELECT count(*) FROM execmirror_orders WHERE state = ANY($1)",
            list(M.OPEN_STATES)) == 1
        # 2 · an own trade on the market that names no order of ours cannot
        #     be attributed: NOT a rejection either
        e.venue.trades_unreadable = False
        e.venue.foreign_trades = [{"order_id": None, "intent": None,
                                   "price": None, "quantity": None,
                                   "traded_qty": 1, "at": time.time()}]
        reads = e.venue.trade_reads
        await e.mirror.recover(e.conn)                    # asked moments ago:
        assert e.venue.trade_reads == reads               # not asked again yet
        _later(e, M.RECONCILE_RECHECK_S + 1)
        await e.mirror.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "UNKNOWN"
        assert _err(r)["code"] == M.R_UNATTRIBUTED_OWN_TRADE
        # 3 · open orders read, trade log read, nothing of ours: REJECTED, with
        #     the evidence it rests on, and never retried
        e.venue.foreign_trades = []
        _later(e, M.RECONCILE_RECHECK_S + 1)
        await e.mirror.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "REJECTED"
        err = _err(r)
        assert err["code"] == "NOT_FOUND_AFTER_RECONCILE" and err["retried"] is False
        assert err["evidence"]["open_orders"] == "NO_MATCHING_OPEN_ORDER"
        assert err["evidence"]["trade_log"] == "NO_OWN_TRADE_SINCE_THE_ATTEMPT"
        i = await _intent_row(e, it["intent_id"])
        assert i["actual_state"] == EI.A_REJECTED
        assert i["actual_refusal"] == "NOT_FOUND_AFTER_RECONCILE"
        assert len(e.venue.placed) == 1
    finally:
        await _close(e)


# ─────────────────────────── duplicate fills ────────────────────────────

@pg
async def test_duplicate_and_stale_order_record_reads_never_duplicate_a_fill(monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000, tif="GTD", otype="RESTING")
        await e.lane._run(e.conn, it["intent_id"])
        r = await _row(e, it["intent_id"])
        assert r["state"] == "OPEN" and r["venue_order_id"] == "cv1"
        vid, mid = r["venue_order_id"], r["mirror_id"]
        e.venue.fill(vid, 2)
        await e.mirror.poll(e.conn)
        assert await _fills(e, mid) == Decimal(2)
        e.venue.fill(vid, 1)                              # cum 3 at the venue
        # THE CALLBACK THAT CARRIES A STALE SNAPSHOT: the actual lane refreshes
        # its order right after the acknowledgement with cum 0, and the runner
        # (another connection) may already have booked fills from it
        stale = {"mirror_id": mid, "venue_order_id": vid, "cum_qty": 0,
                 "avg_px": None, "fees_usd": 0, "group_id": r["group_id"],
                 "us_market_slug": r["us_market_slug"], "intent": r["intent"],
                 "live_qty": 3, "state": "OPEN"}
        await e.mirror._refresh(e.conn, stale)
        assert await _fills(e, mid) == Decimal(3), \
            "a stale caller snapshot booked the venue's fills twice"
        # the same callback, again and again: idempotent
        for _ in range(3):
            await e.mirror._refresh(e.conn, stale)
            await e.mirror.poll(e.conn)
        assert await _fills(e, mid) == Decimal(3)
        row = await _row(e, it["intent_id"])
        assert row["state"] == "FILLED" and row["cum_qty"] == 3
        assert (await M.live_inventory(e.conn, r["group_id"]))["held"] == 3
    finally:
        await _close(e)


@pg
async def test_a_stale_lower_read_never_moves_cum_backwards(monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=4000, tif="GTD", otype="RESTING")
        await e.lane._run(e.conn, it["intent_id"])
        r = await _row(e, it["intent_id"])
        vid, mid = r["venue_order_id"], r["mirror_id"]
        e.venue.fill(vid, 3)
        await e.mirror.poll(e.conn)
        assert await _fills(e, mid) == Decimal(3)
        # a lagging read of the same record says 2
        real = e.venue.order

        def lagging(v):
            o = real(v)
            return dict(o, cumQuantity=2, state="ORDER_STATE_PARTIALLY_FILLED")
        e.venue.order = lagging
        await e.mirror.poll(e.conn)
        assert (await _row(e, it["intent_id"]))["cum_qty"] == 3
        e.venue.order = real
        e.venue.fill(vid, 1)                              # cum 4 at the venue
        await e.mirror.poll(e.conn)
        assert await _fills(e, mid) == Decimal(4), \
            "a backwards read re-booked contracts already counted"
    finally:
        await _close(e)


# ─────────────────────────── worker death mid-submit ────────────────────

@pg
async def test_a_lane_killed_before_the_send_is_decided_after_its_lease_and_never_submits_twice(
        monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000)
        e.venue.script = [{"die_before_send": True}]
        with pytest.raises(asyncio.CancelledError):
            await e.lane._run(e.conn, it["intent_id"])
        r = await _row(e, it["intent_id"])
        assert r["state"] == "SUBMITTING" and e.venue.placed == []
        # a fresh runner (the process restarted) inside the lease leaves the
        # claim alone: the lane may still be mid-call
        fresh = M.Mirror(lambda: e.venue, paper_account="paper_test_chaos_none")
        await fresh.recover(e.conn)
        assert (await _row(e, it["intent_id"]))["state"] == "SUBMITTING"
        # the lease expires: the claim becomes UNKNOWN and is decided by the
        # venue's evidence (nothing open, nothing traded) -- not by a resend
        await _age(e, r["mirror_id"], PAST_GRACE)
        await fresh.recover(e.conn)
        await fresh.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "REJECTED", (r["state"], _err(r))
        assert "SUBMISSION_AMBIGUOUS" in await _events(e, r["mirror_id"])
        assert e.venue.placed == []
        # the intent's one claim forbids any second submission
        await e.conn.execute("UPDATE execution_intents SET actual_state = "
                             "'DISPATCHED', actual_refusal = NULL "
                             "WHERE intent_id = $1", it["intent_id"])
        assert (await e.lane._run(e.conn, it["intent_id"]))["state"] == "DUPLICATE"
        assert e.venue.placed == []
    finally:
        await _close(e)


@pg
async def test_a_lane_killed_after_the_send_is_adopted_not_resent(monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000, tif="GTD", otype="RESTING")
        e.venue.script = [{"die_after_send": True, "rest": True}]
        with pytest.raises(asyncio.CancelledError):
            await e.lane._run(e.conn, it["intent_id"])
        r = await _row(e, it["intent_id"])
        assert r["state"] == "SUBMITTING" and len(e.venue.placed) == 1
        await _age(e, r["mirror_id"], M.SUBMITTING_STALE_S + 5)
        fresh = M.Mirror(lambda: e.venue, paper_account="paper_test_chaos_none")
        await fresh.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "OPEN" and r["venue_order_id"] == "cv1"
        assert (await _intent_row(e, it["intent_id"]))["actual_state"] == EI.A_SUBMITTED
        for _ in range(3):
            await fresh.tick(e.conn)                      # nothing filled yet
        assert len(e.venue.placed) == 1
    finally:
        await _close(e)


@pg
async def test_two_lanes_racing_on_one_intent_make_one_submission(monkeypatch):
    import asyncpg
    e = await _env(monkeypatch)
    other = await asyncpg.connect(DSN)
    try:
        it = await _intent(e, qty=3000)
        e.venue.script = [{"fill": 3}, {"fill": 3}]
        lane2 = EI.ActualLane(None, e.mirror)
        got = await asyncio.gather(e.lane._run(e.conn, it["intent_id"]),
                                   lane2._run(other, it["intent_id"]),
                                   return_exceptions=True)
        assert len(e.venue.placed) == 1, got
        states = sorted(str((g or {}).get("state")) for g in got
                        if isinstance(g, dict))
        assert EI.A_SUBMITTED in states
        assert set(states) <= {EI.A_SUBMITTED, "DUPLICATE", "NOT_DISPATCHED"}, got
        assert await e.conn.fetchval(
            "SELECT count(*) FROM execmirror_orders WHERE execution_intent_id = $1",
            it["intent_id"]) == 1
    finally:
        await other.close()
        await _close(e)


# ─────────────────────────── DB failure after venue accept ──────────────

class AckWriteFails:
    """The connection the lane holds, failing exactly at the acknowledgement
    write (the UPDATE that records the venue order id) -- the database
    boundary fault, after the venue has accepted."""

    def __init__(self, conn):
        self._c = conn
        self.failed = 0

    def __getattr__(self, name):
        return getattr(self._c, name)

    async def execute(self, sql, *args, **kw):
        if "venue_order_id = $3" in sql and "accepted_at = now()" in sql:
            self.failed += 1
            raise ConnectionResetError("connection to the database was lost")
        return await self._c.execute(sql, *args, **kw)


@pg
async def test_a_db_failure_after_the_venue_accepted_is_recovered_and_recorded_never_orphaned(
        monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000)                    # IOC, trades at once
        e.venue.script = [{"fill": 3}]
        broken = AckWriteFails(e.conn)
        with pytest.raises(ConnectionResetError):
            await e.lane._run(broken, it["intent_id"])
        assert broken.failed == 1 and len(e.venue.placed) == 1
        r = await _row(e, it["intent_id"])
        assert r["state"] == "SUBMITTING" and r["venue_order_id"] is None
        assert await _fills(e, r["mirror_id"]) == 0
        # the runner after the lease: SUBMITTING -> UNKNOWN -> the order is
        # not open (it traded) -> the trade log names it -> recorded
        await _age(e, r["mirror_id"], PAST_GRACE)
        fresh = M.Mirror(lambda: e.venue, paper_account="paper_test_chaos_none")
        await fresh.recover(e.conn)
        await fresh.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "FILLED", (r["state"], _err(r))
        assert r["venue_order_id"] == "cv1"
        assert await _fills(e, r["mirror_id"]) == Decimal(3)
        i = await _intent_row(e, it["intent_id"])
        assert i["actual_state"] == EI.A_SUBMITTED
        await _runner_pass(fresh, e.conn)
        h = await e.conn.fetchrow("SELECT * FROM smalllive_handoffs WHERE "
                                  " group_id = $1", r["group_id"])
        assert h is not None and h["live_held"] == 3
        # the venue position equals the recorded fills: nothing orphaned
        snap = await fresh.snapshot(e.conn, await M.control(e.conn))
        assert snap["reconciled"] is True, snap
        assert len(e.venue.placed) == 1
    finally:
        await _close(e)


@pg
async def test_a_db_failure_after_a_resting_accept_is_adopted_from_the_open_orders(monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000, tif="GTD", otype="RESTING")
        broken = AckWriteFails(e.conn)
        with pytest.raises(ConnectionResetError):
            await e.lane._run(broken, it["intent_id"])
        r = await _row(e, it["intent_id"])
        await _age(e, r["mirror_id"], M.SUBMITTING_STALE_S + 5)
        await e.mirror.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "OPEN" and r["venue_order_id"] == "cv1"
        e.venue.fill("cv1", 3)
        await e.mirror.poll(e.conn)
        assert await _fills(e, r["mirror_id"]) == Decimal(3)
        assert len(e.venue.placed) == 1
    finally:
        await _close(e)


# ─────────────────────────── partial fill during cancel ─────────────────

@pg
async def test_a_partial_fill_during_a_working_cancel_is_counted_and_the_cancel_is_not_forgotten(
        monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000, tif="GTD", otype="RESTING")
        await e.lane._run(e.conn, it["intent_id"])
        r = await _row(e, it["intent_id"])
        vid = r["venue_order_id"]
        # one contract trades as the cancel arrives; the venue is still
        # working the cancel when the row is re-read
        e.venue.fill_on_cancel[vid] = 1
        e.venue.pending_cancel.add(vid)
        await e.mirror._cancel(e.conn, r, "ACTUAL_CANCEL")
        r = await _row(e, it["intent_id"])
        assert r["state"] == "CANCEL_REQUESTED", r["state"]
        assert r["cum_qty"] == 1 and await _fills(e, r["mirror_id"]) == Decimal(1)
        # a second contract races in, then the venue finishes the cancel
        e.venue.fill(vid, 1)
        e.venue.finish_cancel(vid)
        await e.mirror.poll(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "CANCELLED"
        assert await _fills(e, r["mirror_id"]) == Decimal(2)
        assert (await M.live_inventory(e.conn, r["group_id"]))["held"] == 2
        assert e.venue.cancel_calls == [vid]
    finally:
        await _close(e)


# ─────────────────────────── restart with UNKNOWN orders ────────────────

@pg
async def test_a_restart_resolves_every_unknown_order_from_venue_evidence_and_sends_nothing(
        monkeypatch):
    e = await _env(monkeypatch)
    try:
        resting = await _intent(e, qty=3000, tif="GTD", otype="RESTING")
        traded = await _intent(e, qty=2000)
        nothing = await _intent(e, qty=2000)
        e.venue.script = [{"timeout_after_send": True, "rest": True},
                          {"timeout_after_send": True, "fill": 2},
                          {"timeout_after_send": True}]
        for it in (resting, traded, nothing):
            assert (await e.lane._run(e.conn, it["intent_id"]))["state"] == \
                EI.A_UNKNOWN
        assert len(e.venue.placed) == 3
        for it in (resting, traded, nothing):
            await _age(e, (await _row(e, it["intent_id"]))["mirror_id"], PAST_GRACE)
        # the process restarts: a new runner, no memory of the attempts
        fresh = M.Mirror(lambda: e.venue, paper_account="paper_test_chaos_none")
        for _ in range(3):
            await _runner_pass(fresh, e.conn)
        a, b, c = [await _row(e, it["intent_id"]) for it in (resting, traded, nothing)]
        assert a["state"] == "OPEN" and a["venue_order_id"]
        assert b["state"] == "FILLED" and await _fills(e, b["mirror_id"]) == Decimal(2)
        assert c["state"] == "REJECTED"
        assert [(await _intent_row(e, it["intent_id"]))["actual_state"]
                for it in (resting, traded, nothing)] == [
            EI.A_SUBMITTED, EI.A_SUBMITTED, EI.A_REJECTED]
        assert len(e.venue.placed) == 3
    finally:
        await _close(e)


# ─────────────────────────── the pure matcher ───────────────────────────

def test_the_trade_log_matcher_adopts_only_an_unmapped_order_of_this_shape():
    row = {"us_market_slug": "s", "intent": "ORDER_INTENT_BUY_LONG",
           "wire_price": Decimal("0.55"), "live_qty": 3}
    own = {"order_id": "v9", "intent": "ORDER_INTENT_BUY_LONG",
           "price": {"value": "0.5500"}, "quantity": 3, "traded_qty": 3,
           "at": 1.0}
    got = M.match_unknown_trade(row, [own], mapped=set())
    assert got["outcome"] == "ADOPT" and got["order_id"] == "v9"
    assert M.match_unknown_trade(row, [own], mapped={"v9"})["outcome"] == "NOT_FOUND"
    assert M.match_unknown_trade(row, [], mapped=set())["outcome"] == "NOT_FOUND"
    # same market, an own trade that is not this order's shape: it cannot be
    # attributed, so it forbids the conclusion "nothing happened"
    for other in (dict(own, quantity=5), dict(own, price={"value": "0.56"}),
                  dict(own, order_id=None), dict(own, intent=None)):
        assert M.match_unknown_trade(row, [other], mapped=set())["outcome"] == \
            "UNATTRIBUTED", other
    # an own trade of the OPPOSITE intent is another order's business
    sell = dict(own, order_id="v10", intent="ORDER_INTENT_SELL_LONG")
    assert M.match_unknown_trade(row, [sell], mapped=set())["outcome"] == "NOT_FOUND"
