"""CAPITAL-CRITICAL (rc6.3 pmus-exec, review round 2): THE RETAIL ACTUAL PATH
-- ActualLane (execution_intent.py) -> execmirror.Venue on the PMUS_EXECMIRROR
retail account -- after the second independent review of 3fa9d7a9.

Each regression in §1-§3 reproduces a reviewer probe and FAILS on 3fa9d7a9:

  §1  a risk-reducing SELL is bounded by the venue's own record as well as
      by our fills: execmirror_fills never books a close-position (the stop's
      flatten), so after a stop with flatten and a resume the group read as
      held 2 with the venue flat, and an exit of 2 was sent -- a SELL_LONG
      with nothing held, a short of 2 (probe P4). Now the claim EXCLUDES it by
      name while the newest snapshot names the market
      (VENUE_POSITION_DISAGREES_WITH_MIRROR_FILLS) or predates a close-position
      requested there (RISK_REDUCING_SELL_VENUE_POSITION_NOT_EVIDENCED -- the
      first tick after a resume runs before the next snapshot); against a
      snapshot taken after the close that agrees, the exit goes out;
  §2  the aggregate cap no longer counts a holding whose live side RESOLVED
      at the venue (expired, or paper-settled with no venue position left):
      after the pilot's first $1.00 position settled, every later BUY was
      refused against the $1.00 default cap (probe P-S);
  §3  an emergency stop's in-process memory belongs to ONE stop: after a
      resume during an INCOMPLETE stop, a second stop with flatten skipped the
      close of a holding the first had asked to flatten and reported STOPPED
      with closed [] (probe P-F) -- forgotten when the lane is seen running,
      and when the control's revision moved (a resume and a stop between two
      ticks).

§4 proves the 1:1000 linked sizing END TO END on the wire (the real
execmirror.Venue on the pinned SDK over httpx.MockTransport): a $1,500 PAPER
intention at 0.50 is a 3-contract / $1.50 live target, refused ABOVE_ORDER_CAP
at a $1.00 cap and sent at a $1.50 cap; $300 is BELOW_VENUE_MINIMUM and never
enlarged; the live fill's price and fee are recorded from the venue's order
record, independently of the PAPER fill; in SHADOW (as shipped) nothing at all
reaches the wire.

THE MODE NEVER CHANGES OUTSIDE A TEST: SMALL_LIVE_MODE stays SHADOW in
live_authorization and live_parity (tests/test_rc62_pmus_exec_retail_path.py
pins it); the non-SHADOW mode exists only inside the tests that monkeypatch it.
Fake venue, recording client or mock transport only: no credential, no
network, no order anywhere. Own Postgres (RN1X_TEST_DSN); the control row and
the lane's tables are restored after every test.
"""
from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal

import httpx
import pytest

from sportsassets import execmirror as M
from sportsassets import live_parity as LP

from tests import test_execmirror as TE
from tests import test_rc62_pmus_exec_retail_path as R

pg = TE.pg
#: every test here leaves execmirror_control and the lane's tables as found
_restore_control = R._restore_control


def _j(v):
    return json.loads(v) if isinstance(v, str) else (v or {})


class FlattenVenue(TE.FakeVenue):
    """close-position sells every contract held on the market (as the venue
    does when it can), so the venue is flat afterwards -- and, as on the
    venue, that close is not one of the lane's own orders."""

    def close(self, slug, bips=300):
        self.closed.append(slug)
        net = sum((o["cumQuantity"] if o["intent"] == "ORDER_INTENT_BUY_LONG"
                   else -o["cumQuantity"]) for o in self.orders.values()
                  if o["marketSlug"] == slug)
        if net > 0:
            vid = self._new({"marketSlug": slug, "intent": "ORDER_INTENT_SELL_LONG",
                             "quantity": net, "price": {"value": "0.40"},
                             "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"})
            self.fill(vid, net)
        return {"id": "close-" + slug}


async def _held_then_stopped_with_flatten(conn, monkeypatch, venue):
    """An ACTUAL position of 2 (entry through the lane, paper sibling
    filled), the mode flipped off SHADOW in-test, then the owner's stop with
    flatten carried out to STOPPED, then the owner's resume."""
    acct, _fake, mirror = await TE._setup(conn, monkeypatch)
    mirror._venue_factory = lambda: venue
    mirror._venue = None
    entry = await TE._paper_order(conn, acct, qty=2000)
    venue.behaviour = [{"fill": 2}]
    await mirror.tick(conn)
    await TE._paper_fill(conn, acct, entry, qty=2000)
    assert await R._held(conn, entry["group_id"]) == 2
    R._live_mode(monkeypatch)
    await conn.execute("UPDATE execmirror_control SET stopped = true,"
                       " stop_done_at = NULL, flatten_on_stop = true,"
                       " revision = revision + 1 WHERE id = 1")
    out = await mirror.tick(conn)
    assert out["state"] == "STOPPED" and out["closed"] == [entry["slug"]], out
    # the owner resumes (api/app.py 'resume')
    await conn.execute("UPDATE execmirror_control SET stopped = false,"
                       " stop_done_at = NULL, flatten_on_stop = false,"
                       " revision = revision + 1 WHERE id = 1")
    return acct, mirror, entry


async def _exit(conn, acct, entry):
    return await TE._paper_order(conn, acct, role="EXIT", direction="SELL",
                                 intent="ORDER_INTENT_SELL_LONG", qty=2000,
                                 group=entry["group_id"])


async def _flatten_on_record(conn, entry):
    """The stop's close-position request was written before it was sent."""
    (fl,) = await R._events(conn, "FLATTEN_REQUESTED")
    assert fl == {"slug": entry["slug"], "net": "2.000000"}
    assert M.FLATTEN_REQUESTED == "FLATTEN_REQUESTED"


# ═════════════════════════════════════════════════════════════════════
# §1 THE VENUE'S RECORD BOUNDS A RISK-REDUCING SELL
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_an_exit_after_a_flatten_is_refused_while_the_snapshot_disagrees(
        monkeypatch):
    """PROBE P4 INVERTED. After the flatten the venue is flat; the snapshot
    taken after the resume says so (unreconciled, naming the market); our
    fills still read held 2. The exit of 2 is EXCLUDED by name and nothing is
    sent. 3fa9d7a9: FILLED, a SELL_LONG of 2 sent, the venue short 2."""
    conn = await TE._conn()
    try:
        fv = FlattenVenue()
        acct, mirror, entry = await _held_then_stopped_with_flatten(
            conn, monkeypatch, fv)
        assert fv.positions() == {}                     # the venue is flat
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec["reconciled"] is False and entry["slug"] in rec["differences"]
        prod = R.ProdPlace(fv)
        mirror._venue = prod
        ex = await _exit(conn, acct, entry)
        fv.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        r = await TE._row(conn, ex["order_id"])
        assert (r["state"], r["exclusion"]) == (
            "EXCLUDED", "VENUE_POSITION_DISAGREES_WITH_MIRROR_FILLS"), r
        d = _j(r["detail"])["risk_reducing_sell"]
        assert (d["held"], d["available"]) == (2, 2)    # what the fills say
        assert d["venue_position"]["difference"] == {
            "mirror_fills_net": 2, "baseline": 0, "venue": None, "signed": True}
        assert prod.rec.created == []                   # nothing reached the client
        assert fv.positions() == {}                     # no short was opened
        (ev,) = await R._events(conn, "EXCLUDED", r["mirror_id"])
        assert ev["exclusion"] == "VENUE_POSITION_DISAGREES_WITH_MIRROR_FILLS"
        await _flatten_on_record(conn, entry)
    finally:
        await conn.close()


@pg
async def test_the_first_exit_after_a_resume_waits_for_a_snapshot_taken_after_the_flatten(
        monkeypatch):
    """The tick after a resume submits BEFORE its next snapshot (a stopped
    lane takes none -- OWNER_SNAPSHOT_WHILE_STOPPED), so the newest snapshot
    is the one from before the stop, reconciled. A close-position was
    requested on the market after it: the exit is EXCLUDED as not evidenced,
    nothing sent. 3fa9d7a9: the SELL of 2 was sent and the venue went short 2."""
    conn = await TE._conn()
    try:
        fv = FlattenVenue()
        acct, mirror, entry = await _held_then_stopped_with_flatten(
            conn, monkeypatch, fv)
        newest = _j(await conn.fetchval(
            "SELECT reconciliation FROM execmirror_snapshots ORDER BY at DESC LIMIT 1"))
        assert newest == {"reconciled": True, "differences": {}}   # pre-stop
        prod = R.ProdPlace(fv)
        mirror._venue = prod
        ex = await _exit(conn, acct, entry)
        fv.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        r = await TE._row(conn, ex["order_id"])
        assert (r["state"], r["exclusion"]) == (
            "EXCLUDED", "RISK_REDUCING_SELL_VENUE_POSITION_NOT_EVIDENCED"), r
        assert prod.rec.created == []
        assert fv.positions() == {}
        vp = _j(r["detail"])["risk_reducing_sell"]["venue_position"]
        assert vp["flatten_requests_since_snapshot"] == 1
        await _flatten_on_record(conn, entry)
    finally:
        await conn.close()


@pg
async def test_an_exit_is_sent_once_a_snapshot_after_the_flatten_agrees(monkeypatch):
    """The close-position was answered but did not trade (the venue still
    holds 2). The first exit after the resume is not evidenced and is
    EXCLUDED; once a snapshot taken after the close agrees with our fills,
    the next exit goes out -- the rule never leaves a held position without
    an exit. 3fa9d7a9: the first exit was sent without that evidence."""
    conn = await TE._conn()
    try:
        venue = TE.FakeVenue()                          # close: 2xx, no trade
        acct, mirror, entry = await _held_then_stopped_with_flatten(
            conn, monkeypatch, venue)
        prod = R.ProdPlace(venue)
        mirror._venue = prod
        first = await _exit(conn, acct, entry)
        await mirror.tick(conn)
        r1 = await TE._row(conn, first["order_id"])
        assert r1["exclusion"] == "RISK_REDUCING_SELL_VENUE_POSITION_NOT_EVIDENCED", r1
        assert prod.rec.created == []
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec == {"reconciled": True, "differences": {}}   # venue holds 2
        second = await _exit(conn, acct, entry)
        venue.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        r2 = await TE._row(conn, second["order_id"])
        assert r2["state"] == "FILLED", r2
        assert _j(r2["detail"])["risk_reducing_sell"]["admitted"] is True
        assert [(p["intent"], p["quantity"]) for p in prod.rec.created] == [
            ("ORDER_INTENT_SELL_LONG", 2)]
        assert await R._held(conn, entry["group_id"]) == 0
        assert venue.positions() == {}
        await _flatten_on_record(conn, entry)
    finally:
        await conn.close()


@pg
async def test_a_difference_on_another_market_never_blocks_this_markets_exit(
        monkeypatch):
    """The SELL rule is per market: a venue position on ANOTHER market that
    our fills do not explain (the snapshot is unreconciled) leaves this
    market's exit admitted (the BUY lane's refusal stays account-wide)."""
    conn = await TE._conn()
    try:
        acct, venue, mirror, entry = await R._held_position(conn, monkeypatch)
        R._live_mode(monkeypatch)
        vid = venue._new({"marketSlug": "mlb-foreign-0002",
                          "intent": "ORDER_INTENT_BUY_LONG", "quantity": 5,
                          "price": {"value": "0.50"}, "tif": "x"})
        venue.fill(vid, 5)
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec["reconciled"] is False
        assert list(rec["differences"]) == ["mlb-foreign-0002"]
        prod = R.ProdPlace(venue)
        mirror._venue = prod
        ex = await _exit(conn, acct, entry)
        venue.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        r = await TE._row(conn, ex["order_id"])
        assert r["state"] == "FILLED", r
        assert [(p["intent"], p["quantity"]) for p in prod.rec.created] == [
            ("ORDER_INTENT_SELL_LONG", 2)]
    finally:
        await conn.close()


@pg
async def test_in_shadow_the_exit_after_a_flatten_is_refused_exactly_as_before(
        monkeypatch):
    """SHADOW (as shipped): the admission is never reached; the exit is
    claimed and refused by the adapter (REJECTED LegacyOriginationRetired),
    nothing reaches the client."""
    conn = await TE._conn()
    try:
        fv = FlattenVenue()
        acct, _fake, mirror = await TE._setup(conn, monkeypatch)
        mirror._venue_factory = lambda: fv
        mirror._venue = None
        entry = await TE._paper_order(conn, acct, qty=2000)
        fv.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        await TE._paper_fill(conn, acct, entry, qty=2000)
        await conn.execute("UPDATE execmirror_control SET stopped = true,"
                           " flatten_on_stop = true WHERE id = 1")
        assert (await mirror.tick(conn))["state"] == "STOPPED"
        await conn.execute("UPDATE execmirror_control SET stopped = false,"
                           " stop_done_at = NULL WHERE id = 1")
        prod = R.ProdPlace(fv)
        mirror._venue = prod
        ex = await _exit(conn, acct, entry)
        await mirror.tick(conn)
        r = await TE._row(conn, ex["order_id"])
        assert r["state"] == "REJECTED"
        assert _j(r["error"])["error"] == "LegacyOriginationRetired"
        assert "risk_reducing_sell" not in _j(r["detail"])
        assert prod.rec.created == []
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §2 A RESOLVED HOLDING NO LONGER USES THE AGGREGATE CAP
# ═════════════════════════════════════════════════════════════════════

async def _settle(conn, acct, po, qty, outcome="WON"):
    await conn.execute(
        """INSERT INTO paper_settlements (settlement_id, account_id, position_key,
             settlement_event_key, version, group_id, us_market_slug, holding_side,
             qty, outcome, payout_per_contract, payout_usd, evidence,
             evidence_source, settled_at)
           VALUES ($1,$2,$3,'ev',1,$4,$5,'LONG',$6,$7,1,$6,'{}'::jsonb,'TEST',now())""",
        "paper_s_%s" % uuid.uuid4().hex[:8], acct["account_id"],
        "paperpos:%s:%s:%s:LONG" % (acct["account_id"], po["group_id"], po["slug"]),
        po["group_id"], po["slug"], qty, outcome)


@pytest.mark.parametrize("venue_after,why", [
    ({"netPosition": "0", "expired": True}, "VENUE_POSITION_EXPIRED"),
    (None, "PAPER_SETTLED_AND_NO_VENUE_POSITION"),      # the venue drops it
])
@pg
async def test_a_resolved_position_no_longer_counts_against_the_aggregate_cap(
        monkeypatch, venue_after, why):
    """PROBE P-S INVERTED. Cap NULL -> max_order_usd $1.00. A $1.00 ACTUAL
    position fills; while the venue still holds it the cap counts it (a
    $0.50 decision elsewhere is refused). The market resolves: once the
    venue's own record says so, the holding no longer counts and the next
    $0.50 decision on another market is sent. 3fa9d7a9: refused for ever
    (held_usd 1.00)."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch, cap=1)
        await mirror.snapshot(conn, await M.control(conn))
        po = await TE._paper_order(conn, acct, qty=2000, wire=0.50)
        venue.behaviour = [{"fill": 2}]
        assert (await mirror.lane._run(conn, po["intent_id"]))["state"] == "SUBMITTED"
        await TE._paper_fill(conn, acct, po, qty=2000, price=0.50)
        await _settle(conn, acct, po, 2000)
        # paper settled; the venue has not yet (it still reports 2 held)
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec == {"reconciled": True, "differences": {}}
        nt = await M.open_and_held_notional(conn)
        assert nt["held_usd"] == Decimal("1.00")
        assert nt.get("held_resolved_excluded", {}) == {}
        early = await TE._paper_order(conn, acct, qty=1000, wire=0.50)
        r = await mirror.lane._run(conn, early["intent_id"])
        assert (r["state"], r["refusal"]) == (
            "REFUSED", "ACTUAL_OPEN_AND_HELD_NOTIONAL_ABOVE_CAP"), r
        # the venue settles the live side
        venue.positions = (lambda: {po["slug"]: dict(venue_after)}) \
            if venue_after else (lambda: {})
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec == {"reconciled": True, "differences": {}}
        nt = await M.open_and_held_notional(conn)
        assert nt["held_usd"] == 0
        assert nt["held_resolved_excluded"] == {po["slug"]: why}
        later = await TE._paper_order(conn, acct, qty=1000, wire=0.50)
        r = await mirror.lane._run(conn, later["intent_id"])
        assert r["state"] == "SUBMITTED", r
        assert [p["marketSlug"] for p in venue.placed] == [po["slug"], later["slug"]]
    finally:
        await conn.close()


@pg
async def test_a_position_the_venue_dropped_without_a_settlement_still_counts(
        monkeypatch):
    """Fail closed: a venue position gone with NO paper settlement (a flatten
    the fills never booked, a manual close) is not a resolution -- the cap
    keeps counting it and the snapshot disagrees (the lane refuses by name)."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch, cap=1)
        await mirror.snapshot(conn, await M.control(conn))
        po = await TE._paper_order(conn, acct, qty=2000, wire=0.50)
        venue.behaviour = [{"fill": 2}]
        assert (await mirror.lane._run(conn, po["intent_id"]))["state"] == "SUBMITTED"
        venue.positions = lambda: {}
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec["reconciled"] is False and po["slug"] in rec["differences"]
        nt = await M.open_and_held_notional(conn)
        assert nt["held_usd"] == Decimal("1.00")
        assert nt.get("held_resolved_excluded", {}) == {}
        nxt = await TE._paper_order(conn, acct, qty=1000, wire=0.50)
        r = await mirror.lane._run(conn, nxt["intent_id"])
        assert r["refusal"] == "VENUE_POSITION_DISAGREES_WITH_MIRROR_FILLS", r
        assert len(venue.placed) == 1
    finally:
        await conn.close()


@pg
async def test_a_snapshot_older_than_the_fill_never_reads_as_a_resolution(
        monkeypatch):
    """The venue's absence counts only from a snapshot taken AFTER the
    market's newest fill: a snapshot that predates the buy (it shows no
    position because there was none yet) never resolves the holding."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch, cap=1)
        await mirror.snapshot(conn, await M.control(conn))      # before the buy
        po = await TE._paper_order(conn, acct, qty=2000, wire=0.50)
        venue.behaviour = [{"fill": 2}]
        assert (await mirror.lane._run(conn, po["intent_id"]))["state"] == "SUBMITTED"
        await _settle(conn, acct, po, 2000)
        nt = await M.open_and_held_notional(conn)
        assert nt["held_usd"] == Decimal("1.00")
        assert nt.get("held_resolved_excluded", {}) == {}
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §3 AN EMERGENCY STOP'S MEMORY BELONGS TO ONE STOP
# ═════════════════════════════════════════════════════════════════════

async def _incomplete_stop_with_flatten(conn, monkeypatch):
    """A 2-lot ACTUAL position; stop 1 with flatten: the cancel-all fails
    (503) so the stop is INCOMPLETE, and the close-position is answered 2xx
    but does not trade (nothing inside the slippage band)."""
    acct, venue, mirror = await TE._setup(conn, monkeypatch)
    t = [time.time()]
    mirror._now = lambda: t[0]
    po = await TE._paper_order(conn, acct, qty=2000)
    venue.behaviour = [{"fill": 2}]
    await mirror.tick(conn)
    assert await R._held(conn, po["group_id"]) == 2
    real_ca = venue.cancel_all

    def boom():
        raise TE._Err(503, "unavailable")
    venue.cancel_all = boom
    await conn.execute("UPDATE execmirror_control SET stopped = true,"
                       " stop_done_at = NULL, flatten_on_stop = true WHERE id = 1")
    out = await mirror.tick(conn)
    assert out["state"] == "STOP_INCOMPLETE", out
    assert venue.closed == [po["slug"]]
    return venue, mirror, po, t, real_ca


@pg
async def test_a_stop_after_a_resume_during_an_incomplete_stop_flattens_again(
        monkeypatch):
    """PROBE P-F INVERTED (the reviewer's exact steps, raw control updates):
    resume during the incomplete stop, one RUNNING tick, then stop 2 with
    flatten on a healthy venue. The close is sent again. 3fa9d7a9: STOPPED
    with closed [] -- the holding stayed, the stop was reported done."""
    conn = await TE._conn()
    try:
        venue, mirror, po, t, real_ca = await _incomplete_stop_with_flatten(
            conn, monkeypatch)
        await conn.execute("UPDATE execmirror_control SET stopped = false,"
                           " stop_done_at = NULL, flatten_on_stop = false WHERE id = 1")
        t[0] += 60
        assert (await mirror.tick(conn))["state"] == "RUNNING"
        venue.cancel_all = real_ca
        await conn.execute("UPDATE execmirror_control SET stopped = true,"
                           " stop_done_at = NULL, flatten_on_stop = true WHERE id = 1")
        t[0] += 60
        out = await mirror.tick(conn)
        assert out["state"] == "STOPPED", out
        assert out["closed"] == [po["slug"]], out
        assert venue.closed == [po["slug"], po["slug"]]
        assert len(await R._events(conn, M.FLATTEN_REQUESTED)) == 2
    finally:
        await conn.close()


@pg
async def test_a_resume_and_a_stop_between_two_ticks_still_flatten(monkeypatch):
    """The owner resumes and stops again (with flatten) before the lane's
    next tick, through the admin route, which moves the control's revision on
    each action. The stop met under a new revision starts clean: it runs at
    once (not held by the first stop's retry instant) and sends the close
    again. 3fa9d7a9: held to the retry instant, then STOPPED with closed []."""
    conn = await TE._conn()
    try:
        venue, mirror, po, t, real_ca = await _incomplete_stop_with_flatten(
            conn, monkeypatch)
        venue.cancel_all = real_ca
        # api/app.py 'resume' then 'stop' (flatten), no tick in between
        await conn.execute("UPDATE execmirror_control SET stopped = false,"
                           " stop_done_at = NULL, flatten_on_stop = false,"
                           " revision = revision + 1 WHERE id = 1")
        await conn.execute("UPDATE execmirror_control SET stopped = true,"
                           " stop_done_at = NULL, flatten_on_stop = true,"
                           " revision = revision + 1 WHERE id = 1")
        t[0] += 1                                       # < STOP_RETRY_S
        out = await mirror.tick(conn)
        assert out["state"] == "STOPPED", out
        assert out["closed"] == [po["slug"]], out
        assert venue.closed == [po["slug"], po["slug"]]
        assert (await M.control(conn))["stop_done_at"] is not None
    finally:
        await conn.close()


@pg
async def test_one_incomplete_stop_still_flattens_each_holding_once(monkeypatch):
    """The memory still does its job WITHIN one stop: retried passes of the
    same incomplete stop (same revision, never seen running) do not re-send
    the close of a holding they already flattened."""
    conn = await TE._conn()
    try:
        venue, mirror, po, t, real_ca = await _incomplete_stop_with_flatten(
            conn, monkeypatch)
        for _ in range(3):
            t[0] += M.STOP_RETRY_S
            assert (await mirror.tick(conn))["state"] == "STOP_INCOMPLETE"
        assert venue.closed == [po["slug"]]
        venue.cancel_all = real_ca
        t[0] += M.STOP_RETRY_S
        out = await mirror.tick(conn)
        assert out["state"] == "STOPPED" and out["closed"] == [], out
        assert venue.closed == [po["slug"]]
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4 THE 1:1000 LINKED SIZING, END TO END ON THE WIRE
# ═════════════════════════════════════════════════════════════════════
#
# THE RULE (execmirror.entry_live_qty over the control's scale, 1,000): the
# live target is the PAPER quantity / 1,000 exactly, in whole contracts; below
# one contract BELOW_VENUE_MINIMUM (never enlarged); a cost above
# max_order_usd ABOVE_ORDER_CAP (never shrunk). The live fill's price and fee
# are the venue's order record's (avgPx, commissionNotionalTotalCollected),
# booked into execmirror_fills; the PAPER fill is the simulator's, in
# paper_fills; neither is derived from the other.

LIVE_PX, LIVE_FEE = "0.4900", "0.0300"          # the venue's record
PAPER_PX = 0.50                                 # the simulator's fill


def _priced(wire):
    """The scripted API, its executions priced by the venue: a create's
    record carries avgPx LIVE_PX (better than the 0.50 limit, as an IOC
    LIMIT may fill) and a commission of LIVE_FEE."""
    def handler(request):
        resp = wire.handler(request)
        if (request.method, request.url.path) == ("POST", "/v1/orders"):
            with wire.lock:
                o = wire.orders["ord-%d" % wire.n]
                o["avgPx"] = {"value": LIVE_PX, "currency": "USD"}
                o["commissionNotionalTotalCollected"] = {"value": LIVE_FEE,
                                                         "currency": "USD"}
        return resp
    return handler


async def _decide(conn, acct, paper_usd):
    """A PAPER intention of `paper_usd` at 0.50: paper_usd / 0.50 contracts."""
    return await TE._paper_order(
        conn, acct, qty=int(Decimal(paper_usd) / Decimal("0.50")), wire=0.50)


async def _intent(conn, po):
    return await conn.fetchrow(
        """SELECT paper_target_qty, live_scale, live_raw_qty, live_qty,
                  rounding_delta, actual_state, actual_refusal
             FROM execution_intents WHERE intent_id = $1""", po["intent_id"])


async def _sizing_refusals(conn, mirror, acct):
    """$1,500 at a $1.00 cap -> ABOVE_ORDER_CAP (3 contracts, $1.50 recorded,
    never shrunk); $300 -> BELOW_VENUE_MINIMUM (0.6 -> 0, never enlarged)."""
    big = await _decide(conn, acct, 1500)
    rb = await mirror.lane._run(conn, big["intent_id"])
    assert (rb["state"], rb["refusal"]) == ("REFUSED", "ABOVE_ORDER_CAP"), rb
    assert (Decimal(rb["cost_usd"]), Decimal(rb["cap_usd"])) == (
        Decimal("1.50"), Decimal("1.00"))
    it = await _intent(conn, big)
    assert (it["paper_target_qty"], it["live_scale"], it["live_raw_qty"],
            it["live_qty"]) == (Decimal(3000), Decimal(1000), Decimal(3), 3)
    small = await _decide(conn, acct, 300)
    rs = await mirror.lane._run(conn, small["intent_id"])
    assert (rs["state"], rs["refusal"]) == ("REFUSED", "BELOW_VENUE_MINIMUM"), rs
    it = await _intent(conn, small)
    assert (it["paper_target_qty"], it["live_raw_qty"], it["live_qty"]) == (
        Decimal(600), Decimal("0.6"), 0)


@pg
async def test_the_one_to_one_thousand_rule_end_to_end_on_the_wire(monkeypatch):
    conn = await TE._conn()
    try:
        acct, mirror, wire = await R._wire_world(conn, monkeypatch)   # cap $1.00
        mirror._venue._c._http = httpx.Client(
            transport=httpx.MockTransport(_priced(wire)))
        issued = R._canonical_stand_in(monkeypatch)

        # ── SHADOW, AS SHIPPED: refused at every rail, nothing on the wire ──
        await R._snapshot_row(conn, 1)                  # current, reconciled
        await _sizing_refusals(conn, mirror, acct)
        await conn.execute("UPDATE execmirror_control SET max_order_usd = 1.50"
                           " WHERE id = 1")
        sh = await _decide(conn, acct, 1500)
        rsh = await mirror.lane._run(conn, sh["intent_id"])
        assert (rsh["state"], rsh["refusal"]) == ("REFUSED", LP.R_NO_LIVE_AUTHORIZATION)
        assert (await _intent(conn, sh))["live_qty"] == 3
        assert wire.seen == [] and issued == []
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0

        # ── TEST-ONLY NON-SHADOW MODE ──
        await conn.execute("UPDATE execmirror_control SET max_order_usd = 1.00"
                           " WHERE id = 1")
        R._live_mode(monkeypatch)
        rec = await mirror.snapshot(conn, await M.control(conn))     # on the wire
        assert rec["reconciled"] is True and mirror._buying_power == 100
        await _sizing_refusals(conn, mirror, acct)
        assert ("POST", "/v1/orders") not in wire.seen  # neither was sent
        # the same $1,500 intention at a $1.50 cap: 3 contracts, $1.50, sent
        await conn.execute("UPDATE execmirror_control SET max_order_usd = 1.50"
                           " WHERE id = 1")
        po = await _decide(conn, acct, 1500)
        wire.fills = [3]
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == "SUBMITTED", res
        assert len(issued) == 1
        creates = [b for s, b in zip(wire.seen, wire.bodies)
                   if s == ("POST", "/v1/orders")]
        assert creates == [{"marketSlug": po["slug"],
                            "intent": "ORDER_INTENT_BUY_LONG",
                            "type": "ORDER_TYPE_LIMIT",
                            "price": {"value": "0.5000", "currency": "USD"},
                            "quantity": 3,
                            "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
                            "synchronousExecution": True}]
        it = await _intent(conn, po)
        assert (it["paper_target_qty"], it["live_scale"], it["live_raw_qty"],
                it["live_qty"], it["rounding_delta"], it["actual_state"]) == (
            Decimal(3000), Decimal(1000), Decimal(3), 3, Decimal(0), "SUBMITTED")
        row = await TE._row(conn, po["order_id"])
        assert (row["state"], row["live_qty"], row["cum_qty"]) == ("FILLED", 3, 3)
        assert (row["paper_qty"], row["scaled_qty"], row["wire_price"]) == (
            Decimal(3000), Decimal(3), Decimal("0.5"))
        assert Decimal(_j(row["detail"])["cost_usd"]) == Decimal("1.50")
        # the LIVE fill, from the venue's order record (read right after the
        # acknowledgement) -- before any PAPER fill exists
        assert await conn.fetchval("SELECT count(*) FROM paper_fills"
                                   " WHERE group_id = $1", po["group_id"]) == 0
        fills = [(f["qty"], f["price"], f["fee_usd"], f["source"]) for f in
                 await conn.fetch("SELECT * FROM execmirror_fills"
                                  " WHERE mirror_id = $1", row["mirror_id"])]
        assert fills == [(Decimal(3), Decimal(LIVE_PX), Decimal(LIVE_FEE),
                          "VENUE_ORDER_RECORD")]
        assert (row["avg_px"], row["fees_usd"]) == (Decimal(LIVE_PX),
                                                    Decimal(LIVE_FEE))
        # the PAPER fill, the simulator's own: 3,000 contracts at 0.50, no fee
        await TE._paper_fill(conn, acct, po, qty=3000, price=PAPER_PX)
        paper = await conn.fetchrow("SELECT qty, price, fee_usd FROM paper_fills"
                                    " WHERE group_id = $1", po["group_id"])
        assert (paper["qty"], paper["price"], paper["fee_usd"]) == (
            Decimal(3000), Decimal("0.5"), Decimal(0))
        assert paper["qty"] / fills[0][0] == 1000       # 1:1000, as recorded
        # a later read of the venue record changes nothing; the paper fill
        # never moved the live one, nor the live one the paper one
        await mirror._refresh(conn, dict(row))
        assert [(f["qty"], f["price"], f["fee_usd"]) for f in await conn.fetch(
            "SELECT * FROM execmirror_fills WHERE mirror_id = $1",
            row["mirror_id"])] == [(Decimal(3), Decimal(LIVE_PX), Decimal(LIVE_FEE))]
        assert wire.auth_failures == []
    finally:
        await conn.close()
