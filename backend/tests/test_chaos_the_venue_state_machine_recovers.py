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
import copy
import datetime as dt
import json
import os
import threading
import time
import types
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

def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat().replace(
        "+00:00", "Z")


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
        self.log_fault = None
        self.foreign_trades: list = []
        self.trade_reads = 0
        self._parser = None
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
        self.orders[vid]["createTime"] = _iso(self.created_at[vid])
        return vid

    def backdate(self, vid, seconds):
        """The venue created `vid` this much earlier (the test's clock moved
        the attempt's start back by _age; the venue's instants follow)."""
        self.created_at[vid] -= float(seconds)
        self.orders[vid]["createTime"] = _iso(self.created_at[vid])

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

    # the venue's ACTIVITY LOG, in the shapes the venue sends ------------
    #
    # PRODUCTION-SHAPED (R30A chaos review: "the doubles return already-parsed
    # dicts, the fabricated-shape pattern"). `own_trades` no longer hands the
    # recovery a parsed list: it serves `portfolio.activities` pages -- the
    # GetActivitiesResponse shape the pinned SDK types, the trade's own order
    # under aggressorExecution / passiveExecution as pmus.trade_own_order
    # reads production rows -- and the REAL execmirror.Venue.own_trades
    # parses them, exactly as in production. `log_fault` corrupts the next
    # page read in one of the ways the venue (or a proxy) has been seen to.

    def _activity(self, vid):
        o = self.orders[vid]
        return {"type": "ACTIVITY_TYPE_TRADE",
                "trade": {"id": "t-" + vid, "marketSlug": o["marketSlug"],
                          "state": "TRADE_STATE_CLEARED",
                          "createTime": _iso(self.created_at[vid]),
                          "price": o["price"], "qty": str(o["cumQuantity"]),
                          "isAggressor": True,
                          "aggressorExecution": {"order": {
                              "id": vid, "intent": o["intent"],
                              "price": o["price"], "quantity": o["quantity"],
                              "createTime": _iso(self.created_at[vid])}},
                          "passiveExecution": {"order": {
                              "id": "cp-" + vid,
                              "intent": "ORDER_INTENT_SELL_LONG"}}}}

    def activities(self, params):
        """portfolio.activities(params), newest first, paged by `limit`."""
        self.trade_reads += 1
        if self.trades_unreadable:
            raise RuntimeError("activities endpoint unavailable")
        slug = params.get("marketSlug")
        acts = [self._activity(v) for v, o in self.orders.items()
                if o["marketSlug"] == slug and o["cumQuantity"] > 0]
        acts += [a for a in self.foreign_trades
                 if (a.get("trade") or {}).get("marketSlug", slug) == slug]
        acts.sort(key=lambda a: M._iso_epoch(a["trade"]["createTime"]) or 0.0,
                  reverse=True)
        lim = int(params.get("limit") or 100)
        off = int(str(params.get("cursor") or "c0")[1:])
        page = acts[off:off + lim]
        more = off + lim < len(acts)
        body = {"activities": page, "eof": not more}
        if more:
            body["nextCursor"] = "c%d" % (off + lim)
        fault = self.log_fault
        if fault == "empty_body":
            return {}
        if fault == "none":
            return None
        if fault == "eof_string_false":
            return dict(body, eof="false", nextCursor="c%d" % (off + lim))
        if fault == "eof_false_no_cursor":
            return {"activities": page, "eof": False}
        if fault == "no_eof_no_cursor":
            return {"activities": page}
        if fault == "no_market_slug" and page:
            page = [dict(a, trade={k: v for k, v in a["trade"].items()
                                   if k != "marketSlug"}) for a in page]
            return dict(body, activities=page)
        if fault == "no_type" and page:
            return dict(body, activities=[{k: v for k, v in a.items()
                                           if k != "type"} for a in page])
        if fault == "time_unreadable" and page:
            page = [dict(a, trade=dict(a["trade"], createTime="soon"))
                    for a in page]
            return dict(body, activities=page)
        return body

    def own_trades(self, slug, since):
        """THE REAL PARSER over this venue's activity pages."""
        if self._parser is None:
            self._parser = M.Venue(client=types.SimpleNamespace(
                portfolio=types.SimpleNamespace(activities=self.activities)))
        return self._parser.own_trades(slug, since)

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


async def _env(monkeypatch, *, canonical_authorization_assumed=True) -> Env:
    """THE STATE MACHINE BELOW THE ONE-ORIGIN GATE (integration of the R30A
    intent and chaos streams). Since the intent stream made the canonical
    intent the ONE origin of live exposure, ActualLane._run asks
    live_parity.authorize_live_exposure before its claim, and that refuses in
    SHADOW (no LiveAuthorization can be issued) -- so on the merged release
    candidate every scenario here was REFUSED before the claim and the
    recovery machinery this file proves was never reached (29 failures).
    The faults below are injected AFTER that decision (the claim, the send,
    the reconciliation), so the honest unit is the machine under the
    authorization stated as an assumption -- the same test-only mechanism the
    intent stream's own lane-mechanics tests use
    (tests/admission_fixture.assume_canonical_live_authorization, as in
    tests/test_execmirror and tests/test_execution_intent_fanout). Nothing in
    production changes: SMALL LIVE stays SHADOW, live_parity issues no
    authorization, and execmirror.Venue.place (which ChaosVenue stands in
    for) still refuses every new order without one. The refusal on the real
    path is proven in this file
    (test_without_the_canonical_authorization_the_lane_claims_and_sends_
    nothing) and in tests/test_live_parity_convergence.py."""
    import asyncpg
    AF.approve_test_rule(monkeypatch)
    if canonical_authorization_assumed:
        AF.assume_canonical_live_authorization(monkeypatch)
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


# ─────────────────────────── the one-origin gate ────────────────────────

@pg
async def test_without_the_canonical_authorization_the_lane_claims_and_sends_nothing(
        monkeypatch):
    """BOTH OWNER REQUIREMENTS HOLD TOGETHER. With the canonical SMALL LIVE
    authorization NOT assumed -- the production state: SHADOW issues none --
    the very lane every scenario below drives refuses before its claim: no
    execmirror_orders row, nothing handed to the venue, no recovery work
    created, and a later runner pass sends nothing either. The recovery
    proofs that follow therefore exercise the machine BELOW the gate; they
    never open a path around it."""
    e = await _env(monkeypatch, canonical_authorization_assumed=False)
    try:
        it = await _intent(e, qty=3000)
        e.venue.script = [{"fill": 3}]
        got = await e.lane._run(e.conn, it["intent_id"])
        assert got["state"] == EI.A_REFUSED, got
        assert await _row(e, it["intent_id"]) is None
        assert e.venue.placed == []
        ir = await _intent_row(e, it["intent_id"])
        assert ir["actual_state"] == EI.A_REFUSED and ir["actual_refusal"], ir
        await _runner_pass(e.mirror, e.conn)
        await e.mirror.tick(e.conn)
        assert e.venue.placed == []
        assert await e.conn.fetchval(
            "SELECT count(*) FROM execmirror_orders") == 0
    finally:
        await _close(e)


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
        # (production shape: a trade print on this market whose own-order
        # side is not stated -- `isAggressor` absent -- so it names no order)
        e.venue.foreign_trades = [{"type": "ACTIVITY_TYPE_TRADE", "trade": {
            "id": "t-foreign", "marketSlug": r["us_market_slug"],
            "createTime": _iso(time.time()), "qty": "1",
            "price": {"value": "0.55", "currency": "USD"}}}]
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

    def _maybe_fail(self, sql):
        if "venue_order_id = $3" in sql and "accepted_at = now()" in sql:
            self.failed += 1
            raise ConnectionResetError("connection to the database was lost")

    # every statement method: the ack write is fenced (`... AND state =
    # 'SUBMITTING' RETURNING`), so it runs through fetchval, not execute
    async def execute(self, sql, *args, **kw):
        self._maybe_fail(sql)
        return await self._c.execute(sql, *args, **kw)

    async def fetchval(self, sql, *args, **kw):
        self._maybe_fail(sql)
        return await self._c.fetchval(sql, *args, **kw)

    async def fetchrow(self, sql, *args, **kw):
        self._maybe_fail(sql)
        return await self._c.fetchrow(sql, *args, **kw)


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


# ═════════════ R30A REVIEW ROUND: THE GAPS THE FIRST PASS LEFT ═════════════
#
# Each test below reproduced a defect an adversarial review found in the
# first pass (and failed against it); the fix is named in the docstring.

from sportsassets import bettor_funded_account as ACC          # noqa: E402

TRADE_LOG_FAULTS = ["empty_body", "none", "eof_string_false",
                    "eof_false_no_cursor", "no_eof_no_cursor",
                    "no_market_slug", "no_type", "time_unreadable"]


@pg
@pytest.mark.parametrize("fault", TRADE_LOG_FAULTS)
async def test_an_empty_or_unfinished_trade_log_never_rejects_a_filled_ioc(
        fault, monkeypatch):
    """THE REVIEW'S END-TO-END REPRODUCTION: an IOC that FILLED, its answer
    lost, and the venue's activity log answering an empty body / None / a
    truthy-string eof / an unfinished page / a row naming no market, no type
    or no readable time. The first reader took each as "no own trade since
    the attempt" and recovery REJECTED the order NOT_FOUND with 3 contracts
    unbooked. Now (execmirror.Venue.own_trades applies the funded lane's
    strict rules) every one leaves the row UNKNOWN, named by the strict
    reader's own code; when the log reads cleanly the fill is found."""
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000)
        e.venue.script = [{"timeout_after_send": True, "fill": 3}]
        await e.lane._run(e.conn, it["intent_id"])
        r = await _row(e, it["intent_id"])
        await _age(e, r["mirror_id"], PAST_GRACE)
        e.venue.log_fault = fault
        for _ in range(2):
            await e.mirror.recover(e.conn)
            _later(e, M.RECONCILE_RECHECK_S + 1)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "UNKNOWN", (fault, r["state"], _err(r))
        assert _err(r)["code"] == M.R_TRADE_LOG_UNREADABLE, _err(r)
        assert _err(r)["refusal"] in (
            ACC.R_ACTIVITY_RESPONSE_INCOMPLETE, ACC.R_ACTIVITY_FIELD_MALFORMED,
            ACC.R_ACTIVITY_TIME_UNREADABLE, ACC.R_PAGE_END_MALFORMED,
            ACC.R_PAGE_END_INCONSISTENT, ACC.R_PAGE_END_NOT_STATED), _err(r)
        assert await _fills(e, r["mirror_id"]) == 0
        assert (await _intent_row(e, it["intent_id"]))["actual_state"] == \
            EI.A_UNKNOWN
        # the log reads cleanly again: the venue order is found and booked
        e.venue.log_fault = None
        await e.mirror.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "FILLED", (r["state"], _err(r))
        assert await _fills(e, r["mirror_id"]) == Decimal(3)
        assert len(e.venue.placed) == 1
    finally:
        await _close(e)


# ── the two readers of the venue's trade log refuse the same pages ──

SINCE = 1_790_000_000.0
_SL = "aec-mlb-parity-x"


def _act(oid, at, *, slug=_SL, intent="ORDER_INTENT_BUY_LONG", **drop):
    t = {"id": "t-" + oid, "marketSlug": slug, "createTime": _iso(at),
         "qty": "3", "isAggressor": True,
         "aggressorExecution": {"order": {
             "id": oid, "intent": intent,
             "price": {"value": "0.55", "currency": "USD"}, "quantity": 3}},
         "passiveExecution": {"order": {"id": "cp-" + oid,
                                        "intent": "ORDER_INTENT_SELL_LONG"}}}
    for k, v in drop.items():
        if v is None:
            t.pop(k, None)
        else:
            t[k] = v
    return {"type": "ACTIVITY_TYPE_TRADE", "trade": t}


def _p(acts, **end):
    return dict({"activities": acts}, **end)


PARITY_CORPUS = {
    "one_complete_page": [_p([_act("a", SINCE + 60)], eof=True)],
    "two_pages": [_p([_act("a", SINCE + 60)], eof=False, nextCursor="c2"),
                  _p([_act("b", SINCE + 30)], eof=True)],
    "an_older_row_ends_the_walk": [
        _p([_act("a", SINCE + 60), _act("old", SINCE - 5)], eof=False,
           nextCursor="c2")],
    "another_market_is_skipped": [
        _p([_act("x", SINCE + 70, slug="other-slug"), _act("a", SINCE + 60)],
           eof=True)],
    "an_own_side_that_is_not_stated": [
        _p([_act("a", SINCE + 60, isAggressor=None)], eof=True)],
    "empty_body": [{}],
    "none": [None],
    "eof_string_false": [_p([_act("a", SINCE + 60)], eof="false",
                            nextCursor="c2"),
                         _p([_act("b", SINCE + 30)], eof=True)],
    "eof_false_without_a_cursor": [_p([], eof=False)],
    "neither_eof_nor_cursor": [_p([])],
    "eof_true_with_a_cursor": [_p([], eof=True, nextCursor="c9")],
    "a_cursor_that_repeats": [_p([_act("a", SINCE + 60)], eof=False,
                                 nextCursor="c2"),
                              _p([_act("b", SINCE + 50)], eof=False,
                                 nextCursor="c2")],
    "a_trade_naming_no_market": [_p([_act("a", SINCE + 60, marketSlug=None)],
                                    eof=True)],
    "an_activity_with_no_type": [
        {"activities": [{"trade": _act("a", SINCE + 60)["trade"]}],
         "eof": True}],
    "an_unreadable_time": [_p([_act("a", SINCE + 60, createTime="soon")],
                              eof=True)],
    "a_bool_for_a_time": [_p([_act("a", SINCE + 60, createTime=True)],
                             eof=True)],
    "rows_not_newest_first": [_p([_act("a", SINCE + 10), _act("b", SINCE + 60)],
                                 eof=True)],
    "the_walk_runs_out_of_pages": [
        _p([_act("p%d" % i, SINCE + 600 - i)], eof=False,
           nextCursor="c%d" % (i + 1)) for i in range(12)],
}


class _Pages:
    def __init__(self, pages):
        self._pages = copy.deepcopy(pages)

    def activities(self, params):
        return self._pages.pop(0) if self._pages else {}


@pytest.mark.parametrize("name", sorted(PARITY_CORPUS))
def test_the_mirror_and_the_funded_lane_read_the_same_trade_log_pages_alike(
        name, monkeypatch):
    """execmirror.Venue.own_trades now calls the funded lane's strict pure
    parts (page_end, strict_activity_ts); this pins that the WHOLE walk
    agrees with bettor_funded_account.read_own_trades_sync page for page:
    the same pages refused with the same code, the same own orders read."""
    monkeypatch.setattr(M, "PACE_S", 0.0)
    pages = PARITY_CORPUS[name]
    try:
        mine = M.Venue(client=types.SimpleNamespace(
            portfolio=_Pages(pages))).own_trades(_SL, SINCE)
        ok, code = True, None
    except M.TradeLogUnreadable as exc:
        mine, ok, code = None, False, exc.refusal
    theirs = ACC.read_own_trades_sync(
        types.SimpleNamespace(portfolio=_Pages(pages)), _SL, SINCE,
        paced_read=lambda call, endpoint=None: call())
    assert ok == theirs["ok"], (name, code, theirs.get("refusal"))
    if ok:
        assert [r["order_id"] for r in mine] == \
            [r["own_order_id"] for r in theirs["rows"]], name
        assert [r["at"] for r in mine] == [r["ts"] for r in theirs["rows"]]
    else:
        assert code == theirs["refusal"], (name, code, theirs)


# ── a lane that outlives its SUBMITTING lease ──

class HeldVenue(ChaosVenue):
    """place() blocks on the wire until `release`: with act="before" the
    venue acts at once (creates, fills `fill`) and only the RESPONSE is held;
    with act="after" the request itself is held and the venue acts only on
    release. The caller then gets the order id (answer="ack") or a timeout."""

    def __init__(self, *, act="before", answer="timeout", fill=3):
        super().__init__()
        self.act, self.answer, self.n_fill = act, answer, fill
        self.release = threading.Event()
        self.sent = threading.Event()

    def place(self, params):
        self.placed.append(params)
        self.sent.set()
        vid = self._act(params, {"fill": self.n_fill}) \
            if self.act == "before" else None
        self.release.wait(30)
        if self.act == "after":
            vid = self._act(params, {"fill": self.n_fill})
        if self.answer == "timeout":
            raise VenueTimeout("the response never came")
        return {"id": vid, "executions": []}


async def _until(ev: threading.Event, timeout=10.0) -> None:
    end = time.time() + timeout
    while not ev.is_set():
        assert time.time() < end, "the lane never reached the venue"
        await asyncio.sleep(0.02)


async def _held_lane(monkeypatch, **kw):
    import asyncpg
    e = await _env(monkeypatch)
    e.venue = HeldVenue(**kw)
    e.mirror._venue = None                 # the factory reads e.venue
    e.lane_conn = await asyncpg.connect(DSN)
    e.it = await _intent(e, qty=3000)      # IOC, 3 live contracts
    e.task = asyncio.create_task(e.lane._run(e.lane_conn, e.it["intent_id"]))
    await _until(e.venue.sent)
    r = await _row(e, e.it["intent_id"])
    assert r["state"] == "SUBMITTING"
    await _age(e, r["mirror_id"], PAST_GRACE)          # the lease is long gone
    return e


async def _close_held(e):
    e.venue.release.set()
    try:
        if not e.task.done():
            await asyncio.wait_for(e.task, 30)
    finally:
        await e.lane_conn.close()
        await _close(e)


@pg
async def test_a_lane_that_outlives_its_lease_never_overwrites_what_recovery_established(
        monkeypatch):
    """THE REVIEW'S REPRODUCTION: the venue acts and fills 3, the lane's
    response is held past its lease, recovery adopts the order from the trade
    log -- then the lane's call times out. The lane's write was unconditional,
    so it rewrote the row UNKNOWN and the next recovery REJECTED it NOT_FOUND
    with its 3 contracts booked. Now the write is fenced on SUBMITTING and
    the late answer is an event."""
    e = await _held_lane(monkeypatch, act="before", answer="timeout")
    try:
        await e.mirror.recover(e.conn)
        r = await _row(e, e.it["intent_id"])
        assert r["state"] == "FILLED" and r["venue_order_id"] == "cv1", \
            (r["state"], _err(r))
        e.venue.release.set()
        got = await asyncio.wait_for(e.task, 30)
        assert got["state"] == "LATE_UNKNOWN" and got["late"] == \
            "RECOVERY_OWNS_IT", got
        for _ in range(2):
            _later(e, 120)
            await _runner_pass(e.mirror, e.conn)
        r = await _row(e, e.it["intent_id"])
        assert r["state"] == "FILLED" and r["venue_order_id"] == "cv1"
        assert await _fills(e, r["mirror_id"]) == Decimal(3)
        i = await _intent_row(e, e.it["intent_id"])
        assert i["actual_state"] == EI.A_SUBMITTED and i["actual_refusal"] is None
        # the late lane's latency marks still reach the intent (evidence only)
        tl = i["timeline"] if isinstance(i["timeline"], dict) else json.loads(i["timeline"])
        assert tl.get("late_answer") == "RECOVERY_OWNS_IT" and "submit_error" in tl
        assert "LATE_SUBMIT_ANSWER" in await _events(e, r["mirror_id"])
        assert len(e.venue.placed) == 1
    finally:
        await _close_held(e)


@pg
async def test_a_late_acknowledgement_records_the_order_a_premature_not_found_wrote_off(
        monkeypatch):
    """The request is still on the wire when recovery reads an empty log and
    concludes NOT_FOUND; the venue then acts and the lane's acknowledgement
    arrives naming the order. Positive evidence supersedes the inference: the
    order is recorded, its fills booked from the venue's record, the intent
    REJECTED -> SUBMITTED, and the position handed to Xavier."""
    e = await _held_lane(monkeypatch, act="after", answer="ack")
    try:
        await e.mirror.recover(e.conn)
        r = await _row(e, e.it["intent_id"])
        assert r["state"] == "REJECTED" and \
            _err(r)["code"] == M.NOT_FOUND_AFTER_RECONCILE
        e.venue.release.set()
        got = await asyncio.wait_for(e.task, 30)
        assert got["late"] == "RECORDED_LATE_ACKNOWLEDGEMENT", got
        r = await _row(e, e.it["intent_id"])
        assert r["state"] == "FILLED" and r["venue_order_id"] == "cv1"
        assert await _fills(e, r["mirror_id"]) == Decimal(3)
        det = r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"])
        assert det["recorded_from"] == "LATE_ACKNOWLEDGEMENT"
        i = await _intent_row(e, e.it["intent_id"])
        assert i["actual_state"] == EI.A_SUBMITTED and i["actual_refusal"] is None
        h = await e.conn.fetchrow("SELECT * FROM smalllive_handoffs WHERE "
                                  " group_id = $1", r["group_id"])
        assert h is not None and h["live_held"] == 3
        assert len(e.venue.placed) == 1
    finally:
        await _close_held(e)


@pg
async def test_a_late_ambiguous_answer_reopens_a_not_found_drawn_while_the_send_was_in_flight(
        monkeypatch):
    e = await _held_lane(monkeypatch, act="after", answer="timeout")
    try:
        await e.mirror.recover(e.conn)
        assert (await _row(e, e.it["intent_id"]))["state"] == "REJECTED"
        e.venue.release.set()                 # the venue acts, then: timeout
        got = await asyncio.wait_for(e.task, 30)
        assert got["late"] == "REOPENED", got
        r = await _row(e, e.it["intent_id"])
        assert r["state"] == "UNKNOWN"
        assert _err(r)["code"] == M.R_CONCLUDED_WHILE_IN_FLIGHT
        assert (await _intent_row(e, e.it["intent_id"]))["actual_state"] == \
            EI.A_UNKNOWN
        await e.mirror.recover(e.conn)        # the log now holds the trade
        r = await _row(e, e.it["intent_id"])
        assert r["state"] == "FILLED" and r["venue_order_id"] == "cv1"
        assert await _fills(e, r["mirror_id"]) == Decimal(3)
        assert (await _intent_row(e, e.it["intent_id"]))["actual_state"] == \
            EI.A_SUBMITTED
        assert len(e.venue.placed) == 1
    finally:
        await _close_held(e)


@pg
async def test_absence_is_not_concluded_while_the_send_may_still_be_on_the_wire(
        monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000)
        e.venue.script = [{"timeout_after_send": True}]       # nothing traded
        await e.lane._run(e.conn, it["intent_id"])
        r = await _row(e, it["intent_id"])
        await _age(e, r["mirror_id"], M.UNKNOWN_GRACE_S + 20)
        await e.mirror.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "UNKNOWN", (r["state"], _err(r))
        assert _err(r)["code"] == M.R_SEND_MAY_STILL_BE_IN_FLIGHT
        await _age(e, r["mirror_id"], M.NOT_FOUND_MIN_AGE_S + 5)
        _later(e, M.RECONCILE_RECHECK_S + 1)
        await e.mirror.recover(e.conn)
        r = await _row(e, it["intent_id"])
        assert r["state"] == "REJECTED"
        assert _err(r)["evidence"]["attempt_older_than_s"] == M.NOT_FOUND_MIN_AGE_S
    finally:
        await _close(e)


# ── same-shape attempts on one market ──

def _one_market_rail_stated_absent(monkeypatch):
    """rc6.2 pmus-exec: the ACTUAL lane now refuses a second exposure on one
    market (execution_intent.R_MARKET_HAS_OPEN_ORDER / R_MARKET_ALREADY_HELD,
    proven in tests/test_rc62_pmus_exec_retail_path.py), so two of its claims
    can no longer be in flight on one market together. The three scenarios
    below prove RECOVERY among same-shape attempts on one market -- rows that
    can still meet there (a NOT_FOUND row reopened by its lane's late answer
    beside a later claim; rows written before this release) -- so the rail is
    stated absent for them, by the same test-only means as the canonical
    authorization above. Recovery itself is unchanged."""
    async def nothing_on_the_market(conn, slug):
        return {"non_terminal": [], "net_held": Decimal(0)}
    monkeypatch.setattr(M, "market_exposure", nothing_on_the_market)


@pg
async def test_a_later_same_shape_attempt_never_takes_the_earlier_attempts_fill(
        monkeypatch):
    """THE REVIEW'S PROBE, verbatim in shape: A traded and B did not, both
    the same market/intent/price/size. Pass 1 reads no log (A stays UNKNOWN,
    B is still in grace); pass 2, a minute later, has both readable. Rows
    were walked in no order and B adopted A's order. Now earliest attempt
    first: A owns the order, B has none left and is not concluded until its
    send can no longer be on the wire."""
    e = await _env(monkeypatch)
    _one_market_rail_stated_absent(monkeypatch)
    try:
        slug = "aec-mlb-chaos-same-%s" % uuid.uuid4().hex[:6]
        a = await _intent(e, qty=3000, slug=slug)
        b = await _intent(e, qty=3000, slug=slug)
        e.venue.script = [{"timeout_after_send": True, "fill": 3},
                          {"timeout_after_send": True}]
        await e.lane._run(e.conn, a["intent_id"])
        await e.lane._run(e.conn, b["intent_id"])
        ra, rb = await _row(e, a["intent_id"]), await _row(e, b["intent_id"])
        await _age(e, ra["mirror_id"], PAST_GRACE)
        await _age(e, rb["mirror_id"], 10)
        e.venue.trades_unreadable = True
        await e.mirror.recover(e.conn)
        assert _err(await _row(e, a["intent_id"]))["code"] == M.R_TRADE_LOG_UNREADABLE
        e.venue.trades_unreadable = False
        _later(e, 60)
        await e.mirror.recover(e.conn)
        ra, rb = await _row(e, a["intent_id"]), await _row(e, b["intent_id"])
        assert ra["venue_order_id"] == "cv1" and ra["state"] == "FILLED"
        assert await _fills(e, ra["mirror_id"]) == Decimal(3)
        det = ra["detail"] if isinstance(ra["detail"], dict) else json.loads(ra["detail"])
        assert det["attribution"]["basis"] == \
            "ATTRIBUTED_BY_TIME_ORDER_AMONG_SAME_SHAPE_ATTEMPTS"
        assert rb["venue_order_id"] is None and rb["state"] == "UNKNOWN"
        assert _err(rb)["code"] == M.R_SEND_MAY_STILL_BE_IN_FLIGHT
        assert await _fills(e, rb["mirror_id"]) == 0
    finally:
        await _close(e)


@pg
async def test_two_same_shape_attempts_that_both_traded_each_get_one_order_in_time_order(
        monkeypatch):
    e = await _env(monkeypatch)
    _one_market_rail_stated_absent(monkeypatch)
    try:
        slug = "aec-mlb-chaos-same-%s" % uuid.uuid4().hex[:6]
        a = await _intent(e, qty=3000, slug=slug)
        b = await _intent(e, qty=3000, slug=slug)
        e.venue.script = [{"timeout_after_send": True, "fill": 3},
                          {"timeout_after_send": True, "fill": 3}]
        await e.lane._run(e.conn, a["intent_id"])
        await e.lane._run(e.conn, b["intent_id"])
        ra, rb = await _row(e, a["intent_id"]), await _row(e, b["intent_id"])
        await _age(e, ra["mirror_id"], PAST_GRACE)
        await _age(e, rb["mirror_id"], PAST_GRACE - 5)
        await e.mirror.recover(e.conn)
        ra, rb = await _row(e, a["intent_id"]), await _row(e, b["intent_id"])
        assert (ra["venue_order_id"], rb["venue_order_id"]) == ("cv1", "cv2")
        assert await _fills(e, ra["mirror_id"]) == Decimal(3)
        assert await _fills(e, rb["mirror_id"]) == Decimal(3)
    finally:
        await _close(e)


@pg
async def test_an_unknown_row_waits_while_a_same_shape_claim_is_still_submitting(
        monkeypatch):
    """A's resting order is open; B (same shape) has claimed and its lane may
    be on the wire. Adopting the open order for A could take B's order (and
    B's acknowledgement would then collide with the unique venue order id),
    so recovery WAITS; once B's lease is over, earliest attempt first."""
    e = await _env(monkeypatch)
    _one_market_rail_stated_absent(monkeypatch)
    try:
        slug = "aec-mlb-chaos-same-%s" % uuid.uuid4().hex[:6]
        a = await _intent(e, qty=3000, slug=slug, tif="GTD", otype="RESTING")
        b = await _intent(e, qty=3000, slug=slug, tif="GTD", otype="RESTING")
        e.venue.script = [{"timeout_after_send": True, "rest": True},
                          {"die_before_send": True}]
        await e.lane._run(e.conn, a["intent_id"])
        with pytest.raises(asyncio.CancelledError):
            await e.lane._run(e.conn, b["intent_id"])
        ra, rb = await _row(e, a["intent_id"]), await _row(e, b["intent_id"])
        assert rb["state"] == "SUBMITTING"
        await _age(e, ra["mirror_id"], M.SUBMITTING_STALE_S + 8)
        await e.mirror.recover(e.conn)
        ra = await _row(e, a["intent_id"])
        assert ra["state"] == "UNKNOWN" and ra["venue_order_id"] is None
        assert _err(ra)["code"] == M.R_SAME_SHAPE_SEND_IN_FLIGHT
        await _age(e, rb["mirror_id"], M.SUBMITTING_STALE_S + 2)   # B's lease ends
        _later(e, M.RECONCILE_RECHECK_S + 1)
        await e.mirror.recover(e.conn)
        ra, rb = await _row(e, a["intent_id"]), await _row(e, b["intent_id"])
        assert ra["state"] == "OPEN" and ra["venue_order_id"] == "cv1"
        assert rb["state"] == "UNKNOWN" and rb["venue_order_id"] is None
        await _age(e, rb["mirror_id"], PAST_GRACE)
        _later(e, M.RECONCILE_RECHECK_S + 1)
        await e.mirror.recover(e.conn)
        rb = await _row(e, b["intent_id"])
        assert rb["state"] == "REJECTED"
        assert len(e.venue.placed) == 1
    finally:
        await _close(e)


def test_the_matcher_applies_its_own_time_rule_and_counts_one_order_once():
    start = dt.datetime.fromtimestamp(1_000_000.0, dt.timezone.utc)
    row = {"mirror_id": "m1", "us_market_slug": "s",
           "intent": "ORDER_INTENT_BUY_LONG", "wire_price": Decimal("0.55"),
           "live_qty": 3, "submit_started_at": start}
    t = {"order_id": "A", "intent": "ORDER_INTENT_BUY_LONG",
         "price": {"value": "0.55"}, "quantity": 3, "traded_qty": 3}
    # 299 s before the attempt began: not this attempt's
    assert M.match_unknown_trade(row, [dict(t, at=1_000_000.0 - 299)],
                                 mapped=set())["outcome"] == "NOT_FOUND"
    # inside the clock slack, or after: adopted
    assert M.match_unknown_trade(row, [dict(t, at=1_000_000.0 - 60)],
                                 mapped=set())["outcome"] == "ADOPT"
    # a trade with no instant cannot be placed after the attempt
    assert M.match_unknown_trade(row, [dict(t, at=None)],
                                 mapped=set())["outcome"] == "UNATTRIBUTED"
    # one order printed twice is ONE candidate (its earliest print)
    two = [dict(t, at=1_000_010.0, traded_qty=1), dict(t, at=1_000_005.0,
                                                       traded_qty=2)]
    got = M.match_unknown_trade(row, two, mapped=set())
    assert got["outcome"] == "ADOPT" and got["trade"]["at"] == 1_000_005.0
    # a same-shape SUBMITTING claim: wait
    live = dict(row, mirror_id="m2", state="SUBMITTING")
    assert M.match_unknown_trade(row, [dict(t, at=1_000_001.0)], mapped=set(),
                                 rivals=[live])["outcome"] == "WAIT"
    # an earlier same-shape UNKNOWN attempt owns the only order
    earlier = dict(row, mirror_id="m0", state="UNKNOWN",
                   submit_started_at=start - dt.timedelta(seconds=5))
    assert M.match_unknown_trade(row, [dict(t, at=1_000_001.0)], mapped=set(),
                                 rivals=[earlier])["outcome"] == "NOT_FOUND"
    assert M.match_unknown_trade(earlier, [dict(t, at=1_000_001.0)], mapped=set(),
                                 rivals=[dict(row, state="UNKNOWN")])["outcome"] == "ADOPT"


# ── a permanently lower venue read ──

@pg
async def test_a_permanently_lower_read_is_recorded_once_leaves_the_poll_queue_and_is_reconciled(
        monkeypatch):
    """THE REVIEW'S PROBE: after 2 contracts are booked the venue says 1 and
    CANCELED (a busted trade). The stale branch returned before recording the
    poll -- the row stayed first in the poll queue for ever, the venue's
    state was never recorded, and every tick wrote another event."""
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=3000, tif="GTD", otype="RESTING")
        await e.lane._run(e.conn, it["intent_id"])
        r = await _row(e, it["intent_id"])
        vid = r["venue_order_id"]
        e.venue.fill(vid, 2)
        await e.mirror.poll(e.conn)
        before = await _row(e, it["intent_id"])
        e.venue.orders[vid]["cumQuantity"] = 1
        e.venue.orders[vid]["state"] = "ORDER_STATE_CANCELED"
        for _ in range(3):
            await e.mirror.poll(e.conn)
            await e.mirror._refresh(e.conn, {"mirror_id": r["mirror_id"],
                                             "venue_order_id": vid})
        after = await _row(e, it["intent_id"])
        assert after["last_polled_at"] > before["last_polled_at"]
        assert after["venue_state"] == "ORDER_STATE_CANCELED"
        assert after["state"] == "CANCELLED"          # out of the poll set
        assert after["cum_qty"] == 2 and await _fills(e, r["mirror_id"]) == 2
        assert (await _events(e, r["mirror_id"])).count("POLL_STALE_READ") == 1
        await e.mirror.audrey_reconcile(e.conn)
        rec = await e.conn.fetchrow(
            "SELECT * FROM smalllive_reconciliations WHERE group_id=$1",
            r["group_id"])
        disc = json.loads(rec["discrepancies"]) if isinstance(
            rec["discrepancies"], str) else rec["discrepancies"]
        assert [d for d in disc if d["code"] == "VENUE_CUM_BELOW_RECORDED_FILLS"
                and d["venue"] == "1"], disc
    finally:
        await _close(e)


@pg
async def test_a_transient_lower_read_is_forgotten_once_the_venue_catches_up(
        monkeypatch):
    e = await _env(monkeypatch)
    try:
        it = await _intent(e, qty=4000, tif="GTD", otype="RESTING")
        await e.lane._run(e.conn, it["intent_id"])
        r = await _row(e, it["intent_id"])
        vid = r["venue_order_id"]
        e.venue.fill(vid, 3)
        await e.mirror.poll(e.conn)
        real = e.venue.order
        e.venue.order = lambda v: dict(real(v), cumQuantity=2)
        await e.mirror.poll(e.conn)
        det = (await _row(e, it["intent_id"]))["detail"]
        det = det if isinstance(det, dict) else json.loads(det)
        assert det["venue_cum_below_recorded"]["read_cum"] == "2"
        e.venue.order = real
        await e.mirror.poll(e.conn)
        det = (await _row(e, it["intent_id"]))["detail"]
        det = det if isinstance(det, dict) else json.loads(det)
        assert "venue_cum_below_recorded" not in det
        assert await _fills(e, r["mirror_id"]) == Decimal(3)
    finally:
        await _close(e)


# ── the acceptance instant of an adopted order ──

@pg
async def test_an_adopted_order_records_the_venues_own_instant_not_the_reconciliation_time(
        monkeypatch):
    """Adoption wrote accepted_at = now(): the reconciliation instant, at
    least UNKNOWN_GRACE_S after the send, reported by every latency reader as
    an acknowledgement latency. Now the venue's own instant for the order."""
    e = await _env(monkeypatch)
    try:
        lost = await _intent(e, qty=3000)                     # IOC, traded
        resting = await _intent(e, qty=3000, tif="GTD", otype="RESTING")
        e.venue.script = [{"timeout_after_send": True, "fill": 3},
                          {"timeout_after_send": True, "rest": True}]
        await e.lane._run(e.conn, lost["intent_id"])
        await e.lane._run(e.conn, resting["intent_id"])
        for it, vid in ((lost, "cv1"), (resting, "cv2")):
            r = await _row(e, it["intent_id"])
            await _age(e, r["mirror_id"], PAST_GRACE)
            e.venue.backdate(vid, PAST_GRACE - 0.5)   # it acted 0.5 s in
        await e.mirror.recover(e.conn)
        for it, basis in ((lost, "VENUE_ORDER_CREATE_TIME"),
                          (resting, "VENUE_ORDER_CREATE_TIME")):
            r = await _row(e, it["intent_id"])
            assert r["venue_order_id"], (r["state"], _err(r))
            ack_s = (r["accepted_at"] - r["submit_started_at"]).total_seconds()
            assert 0.0 < ack_s < 2.0, ack_s
            det = r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"])
            assert det["accepted_at_basis"] == basis
            assert det["reconciled_from"] in ("ACCOUNT_TRADE_LOG", "VENUE_OPEN_ORDERS")
    finally:
        await _close(e)
