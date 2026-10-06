"""P0 MARKET-DATA FRESHNESS: ONE PAPER MARKET-DATA OWNER, HELD MARKS FIRST,
INSTITUTIONAL BOOKS ONLY WHERE PROVEN PER SYMBOL.

  §1  the owner (sportsassets.paper_market_data): held reads outrank
      discovery; N concurrent reads of one slug -> ONE dispatch; recent
      successful reads served from the cache with their original receipt;
      the venue's shared Retry-After hold is honoured by every paper reader
      (discovery deferred, a held read refused by the gate rather than sent
      past its deadline); a queued read past its deadline is never sent;
      every PaperMarketDataClient's default transport routes through it
  §2  the institutional held mark (pure): used ONLY with exact identity AND
      this symbol's own SUPPORTED same-book evidence; aggregate agreement
      alone, a missing / non-LONG orientation, a stale venue clock or
      receipt (against the 300 s SLA) and a non-current book are refused;
      a SHORT position marks off the institutional book exactly as off the
      equivalent retail book
  §3  per-symbol same-book evidence from the database (exact samples only)
  §4  the held-mark refresh's source order: institutional -> retail stream
      -> harvest -> paced REST, each recorded with its basis and receipt
      instant; 306's columns persisted
  §5  telemetry fields present; the focus universe keeps held paper
      positions inside its bound; the SLA constants are unchanged

Synthetic data, fakes for every venue / stream; no network, no order.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_ledger as L
from sportsassets import institutional_focus_universe as FU
from sportsassets import institutional_stream as IS
from sportsassets import p5_runtime as P5R
from sportsassets import paper_market_data as PMD
from sportsassets import venue_request_gate as GRT
from sportsassets.agents import paper_mark_refresh as PMR

from tests import paper_harness as H
from tests.test_institutional_contract_map import AEC

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


@pytest.fixture(autouse=True)
def _clean():
    GRT.clear_hold()
    PMD.reset()
    FU.note_held_first([])
    yield
    GRT.clear_hold()
    PMD.reset()
    FU.note_held_first([])


def _md(bid=0.45, ask=0.47):
    return {"bids": [{"px": {"value": str(bid)}, "qty": "10"}],
            "offers": [{"px": {"value": str(ask)}, "qty": "10"}]}


def _no_recent(*_a, **_k):
    return None


def _open_gate():
    return {"blocking": False, "seconds_left": 0.0, "reason": None}


# ═════════════════════════════════════════════════════════════════════
# §1 THE OWNER
# ═════════════════════════════════════════════════════════════════════

class _BlockingVenue:
    """The first dispatch blocks until released; every dispatch is logged."""

    def __init__(self):
        self.calls = []
        self.release = threading.Event()
        self.first_in = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, slug, *, deadline_epoch_s=None):
        with self._lock:
            self.calls.append(slug)
            first = len(self.calls) == 1
        if first:
            self.first_in.set()
            self.release.wait(5.0)
        return {"marketData": _md(), "observed_at": time.time(),
                "request_accounting": {"statuses": [200]}}


def _wait_queued(owner, n, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        q = owner.queue_depth()
        if sum(q[ln] for ln in PMD.LANES) >= n:
            return True
        time.sleep(0.01)
    return False


def test_held_reads_outrank_discovery_reads():
    v = _BlockingVenue()
    o = PMD.Owner(transport=v, recent=_no_recent, gate=_open_gate,
                  max_in_flight=1)
    o.set_held(["held-a", "held-b"])
    got = {}

    def go(slug, lane=None):
        got[slug] = o.read(slug, deadline_epoch_s=time.time() + 5,
                           lane_name=lane)

    ts = [threading.Thread(target=go, args=("blocker",))]
    ts[0].start()
    assert v.first_in.wait(2.0)
    # discovery reads queue FIRST, then the held reads arrive
    for s in ("disc-1", "disc-2"):
        t = threading.Thread(target=go, args=(s, PMD.LANE_DISCOVERY))
        t.start()
        ts.append(t)
    assert _wait_queued(o, 2)
    for s in ("held-a", "held-b"):
        # a caller naming DISCOVERY for a held slug is still HELD
        t = threading.Thread(target=go, args=(s, PMD.LANE_DISCOVERY))
        t.start()
        ts.append(t)
    assert _wait_queued(o, 4)
    v.release.set()
    for t in ts:
        t.join(5.0)
    assert v.calls[0] == "blocker"
    assert v.calls[1:3] == ["held-a", "held-b"]
    assert v.calls[3:] == ["disc-1", "disc-2"]
    assert got["held-a"]["owner_lane"] == PMD.LANE_HELD
    assert got["disc-1"]["owner_lane"] == PMD.LANE_DISCOVERY
    # MANAGE sits between them
    assert PMD._RANK[PMD.LANE_HELD] < PMD._RANK[PMD.LANE_MANAGE] < \
        PMD._RANK[PMD.LANE_DISCOVERY]


def test_the_lane_context_reaches_worker_threads():
    o = PMD.Owner(transport=lambda s, **k: {"marketData": _md()},
                  recent=_no_recent, gate=_open_gate)

    async def main():
        with PMD.lane(PMD.LANE_MANAGE):
            return await asyncio.to_thread(o.read, "pend-1")
    assert asyncio.run(main())["owner_lane"] == PMD.LANE_MANAGE
    assert o.read("pend-2")["owner_lane"] == PMD.LANE_DISCOVERY


def test_concurrent_reads_of_one_slug_share_one_dispatch():
    v = _BlockingVenue()
    o = PMD.Owner(transport=v, recent=_no_recent, gate=_open_gate)
    out = []
    lock = threading.Lock()

    def go():
        r = o.read("same-slug", deadline_epoch_s=time.time() + 5)
        with lock:
            out.append(r)

    ts = [threading.Thread(target=go) for _ in range(8)]
    for t in ts:
        t.start()
    assert v.first_in.wait(2.0)
    time.sleep(0.2)                 # every follower is waiting on the leader
    v.release.set()
    for t in ts:
        t.join(5.0)
    assert v.calls == ["same-slug"]                 # ONE request
    assert len(out) == 8
    assert sum(1 for r in out if r.get("coalesced")) == 7
    assert all(r["marketData"] == out[0]["marketData"] for r in out)
    assert all(r.get("shared_read") for r in out if r.get("coalesced"))
    t = o.telemetry()["totals"]
    assert t["rest_dispatches"] == 1 and t["coalesced_reads"] == 7


def test_a_failed_leader_is_not_shared():
    calls = []

    def venue(slug, **_k):
        calls.append(slug)
        if len(calls) == 1:
            time.sleep(0.2)
            return {"marketData": None, "error": "HTTPStatusError"}
        return {"marketData": _md(), "observed_at": time.time()}
    o = PMD.Owner(transport=venue, recent=_no_recent, gate=_open_gate)
    res = []
    ts = [threading.Thread(target=lambda: res.append(
        o.read("f-slug", deadline_epoch_s=time.time() + 5)))
        for _ in range(2)]
    ts[0].start()
    time.sleep(0.05)
    ts[1].start()
    for t in ts:
        t.join(5.0)
    assert len(calls) == 2
    assert sorted(bool(r.get("error")) for r in res) == [False, True]


def test_recent_reads_are_served_from_the_cache_with_their_receipt():
    calls = []
    seen = {"observed_at": time.time() - 3.0}

    def recent(slug, *, max_age_s):
        assert max_age_s == PMD.CACHE_MAX_AGE_S == G.SHARED_BOOK_MAX_AGE_S
        return {"marketData": _md(), "observed_at": seen["observed_at"],
                "shared_read": True}

    def venue(slug, **_k):
        calls.append(slug)
        return {"marketData": _md(), "observed_at": time.time()}
    o = PMD.Owner(transport=venue, recent=recent, gate=_open_gate)
    r = o.read("c-slug")
    assert calls == [] and r["served_by"] == "CACHE"
    assert r["observed_at"] == seen["observed_at"] and r["shared_read"]
    # a reader needing a book received AFTER the cached receipt is not
    # answered by it
    r2 = o.read("c-slug", not_before_epoch=time.time())
    assert calls == ["c-slug"] and r2["served_by"] == "REST"
    assert o.telemetry()["totals"]["cache_hits"] == 1


def test_the_shared_venue_hold_is_honoured_by_every_paper_reader():
    """A 429's Retry-After armed the PROCESS-WIDE not-before instant: every
    paper reader -- whatever module asks -- sees it. A discovery read is not
    dispatched at all; a held read reaches the gate, which refuses it by name
    rather than send it after its deadline (the venue sees nothing)."""
    sent = []

    def gated_venue(slug, *, deadline_epoch_s=None):
        try:            # what PacedTransport does immediately before a send
            GRT.check_before_dispatch(deadline_epoch_s=deadline_epoch_s)
        except GRT.VenueGateRefusal as ref:
            return {"marketData": None, "error": ref.refusal,
                    "refused_by": "OUR_REQUEST_GATE"}
        sent.append(slug)
        return {"marketData": _md(), "observed_at": time.time()}
    PMD.OWNER = PMD.Owner(transport=gated_venue, recent=_no_recent)
    PMD.set_held(["held-x"])
    GRT.hold_until(until_epoch_s=time.time() + 9.0,
                   reason="VENUE_429_ON_BOOK_READ")
    client = G.PaperMarketDataClient()      # the default transport -> owner

    async def main():
        a = await client.read_book("derek-cand", deadline_epoch_s=time.time()
                                   + 2.0, timeout_s=3.0)
        b = await client.read_book("bench-obs")
        c = await client.read_book("held-x", deadline_epoch_s=time.time()
                                   + 2.0, timeout_s=3.0)
        return a, b, c
    a, b, c = asyncio.run(main())
    assert a["error"] == PMD.R_DISCOVERY_DEFERRED
    assert b["error"] == PMD.R_DISCOVERY_DEFERRED
    assert c["error"] == GRT.R_COOLDOWN_EXCEEDS_DEADLINE
    assert sent == []
    tel = PMD.telemetry()
    assert tel["totals"]["discovery_deferred_during_hold"] == 2
    assert tel["venue_hold"]["blocking"] is True
    # the hold lifted: the same readers go out
    GRT.clear_hold()
    got = asyncio.run(client.read_book("derek-cand"))
    assert got["marketData"] and sent == ["derek-cand"]


def test_a_queued_read_past_its_deadline_is_never_sent():
    v = _BlockingVenue()
    o = PMD.Owner(transport=v, recent=_no_recent, gate=_open_gate,
                  max_in_flight=1)
    t = threading.Thread(target=lambda: o.read("blk", deadline_epoch_s=time.
                                               time() + 5))
    t.start()
    assert v.first_in.wait(2.0)
    r = o.read("late", deadline_epoch_s=time.time() + 0.3)
    v.release.set()
    t.join(5.0)
    assert r["error"] == PMD.R_QUEUE_DEADLINE and "late" not in v.calls
    assert o.telemetry()["totals"]["queue_deadline_refusals"] == 1


def test_the_default_paper_transport_is_the_owner(monkeypatch):
    seen = []

    def fake_read(slug, **kw):
        seen.append((slug, kw))
        return {"marketData": _md(), "observed_at": time.time()}
    monkeypatch.setattr(PMD, "read", fake_read)
    out = G._default_transport("s-1", deadline_epoch_s=5.0,
                               not_before_epoch=4.0)
    assert out["marketData"] and seen == [
        ("s-1", {"deadline_epoch_s": 5.0, "not_before_epoch": 4.0})]


# ═════════════════════════════════════════════════════════════════════
# §2 THE INSTITUTIONAL HELD MARK (PURE)
# ═════════════════════════════════════════════════════════════════════

SYM = "aec-mlb-sd-mil-2026-10-03"
SUPPORTED = {"status": "SUPPORTED", "detail": {"comparable": 40,
                                               "agree_rate": 1.0}}


def _ident(**kw):
    return dict({"status": "EXACT", "symbol": SYM,
                 "institutional_side": "LONG", "price_transform": "IDENTITY",
                 "price_scale": 1000, "qty_scale": 100}, **kw)


def _resident(symbol=SYM, *, bids=((450, 1000),), offers=((470, 500),),
              at=None):
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    b.set_instrument(symbol, dict(AEC, symbol=symbol))
    b.want([symbol])
    b.on_connected("grpc-test")
    b.on_update({"symbol": symbol, "bids": list(bids),
                 "offers": list(offers), "state": "INSTRUMENT_STATE_OPEN",
                 "transact_time": time.time() if at is None else at})
    return b


def test_an_institutional_book_with_every_proof_is_a_held_mark():
    b = _resident()
    now = time.time()
    cur = b.current(SYM, now=now)
    assert cur["ok"], cur
    got = PMD.institutional_held_book(SYM, identity=_ident(),
                                      same_book=SUPPORTED, current=cur,
                                      now=now, sla_s=PMF.SLA_S)
    assert got["ok"], got
    rd = got["read"]
    # the retail book's own wire shape, by the instrument's own scales
    assert rd["marketData"]["bids"] == [{"px": {"value": "0.45"},
                                         "qty": "10"}]
    assert rd["marketData"]["offers"] == [{"px": {"value": "0.47"},
                                           "qty": "5"}]
    # ITS receipt instant, never "now"
    assert rd["observed_at"] == cur["evidence"]["snapshot"]["received_at"]
    assert rd["orientation"]["book"] == "LONG_INSTRUMENT"
    assert PMD.INSTITUTIONAL_BASIS == "HELD_MARK_INSTITUTIONAL_STREAM"


def test_aggregate_agreement_alone_never_admits_a_symbol():
    b = _resident()
    now = time.time()
    cur = b.current(SYM, now=now)
    ib = PMD.InstitutionalBooks(identity_fn=lambda s, i: _ident(),
                                current_fn=b.current)
    # another symbol is SUPPORTED (and the aggregate would be); this one
    # has no evidence of its own
    ib.same_book = {"aec-other-2026-10-03": SUPPORTED}
    assert ib.book(SYM, now=now, sla_s=PMF.SLA_S) is None
    assert ib.refusals == {PMD.I_SAME_BOOK: 1}
    for st in ("INCONCLUSIVE", "CONTRADICTED", "UNTESTED", None):
        got = PMD.institutional_held_book(
            SYM, identity=_ident(), same_book={"status": st},
            current=cur, now=now, sla_s=PMF.SLA_S)
        assert got["refusal"] == PMD.I_SAME_BOOK
    ib.same_book = {SYM: SUPPORTED}
    assert ib.book(SYM, now=now, sla_s=PMF.SLA_S) is not None


def test_identity_and_orientation_must_be_explicit():
    b = _resident()
    now = time.time()
    cur = b.current(SYM, now=now)

    def go(ident):
        return PMD.institutional_held_book(SYM, identity=ident,
                                           same_book=SUPPORTED, current=cur,
                                           now=now, sla_s=PMF.SLA_S)
    assert go(None)["refusal"] == PMD.I_NO_IDENTITY
    assert go(_ident(status="UNAVAILABLE"))["refusal"] == PMD.I_NO_IDENTITY
    assert go(_ident(symbol="aec-x-2026"))["refusal"] == PMD.I_SYMBOL
    assert go(_ident(institutional_side=None))["refusal"] == \
        PMD.I_ORIENTATION
    assert go(_ident(institutional_side="SHORT"))["refusal"] == \
        PMD.I_ORIENTATION
    assert go(_ident(price_transform=None))["refusal"] == PMD.I_ORIENTATION
    assert go(_ident(price_transform="COMPLEMENT"))["refusal"] == \
        PMD.I_ORIENTATION
    assert go(_ident())["ok"]


def test_the_real_identity_mapper_proves_the_orientation(monkeypatch):
    from sportsassets import institutional_api_stream as IAS
    IAS.reset()
    try:
        monkeypatch.setattr(IAS, "running", lambda: True)
        assert IAS.identity_mapper(SYM, "YES") is None     # no refdata yet
        IAS.REFDATA[SYM] = {"record": AEC, "at": time.time()}
        ident = IAS.identity_mapper(SYM, "YES")
        assert ident["institutional_side"] == "LONG"
        assert ident["price_transform"] == "IDENTITY"
        # a NO leg is never mapped (no manufactured 1-YES book)
        assert IAS.identity_mapper(SYM, "NO") is None
        b = _resident()
        now = time.time()
        got = PMD.institutional_held_book(
            SYM, identity=ident, same_book=SUPPORTED,
            current=b.current(SYM, now=now), now=now, sla_s=PMF.SLA_S)
        assert got["ok"], got
    finally:
        IAS.reset()


def test_a_stale_or_non_current_institutional_book_is_refused():
    b = _resident()
    now = time.time()
    cur = b.current(SYM, now=now)
    snap = cur["evidence"]["snapshot"]

    def with_snap(**kw):
        c = dict(cur, evidence=dict(cur["evidence"],
                                    snapshot=dict(snap, **kw)))
        return PMD.institutional_held_book(SYM, identity=_ident(),
                                           same_book=SUPPORTED, current=c,
                                           now=now, sla_s=PMF.SLA_S)
    sla = PMF.SLA_S
    assert with_snap(received_at=now - sla - 1)["refusal"] == PMD.I_STALE
    old_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                            time.gmtime(now - sla - 5))
    assert with_snap(venue_ts=old_iso)["refusal"] == PMD.I_STALE
    assert with_snap(venue_ts=None)["refusal"] == PMD.I_STALE
    assert with_snap(received_at=now - sla + 1)["ok"]
    # the stream's own refusal stands
    gone = dict(cur, ok=False, refusal=IS.R_GAP_CONNECTION)
    got = PMD.institutional_held_book(SYM, identity=_ident(),
                                      same_book=SUPPORTED, current=gone,
                                      now=now, sla_s=sla)
    assert got["refusal"] == PMD.I_NOT_CURRENT
    b.on_disconnected("test")
    got = PMD.institutional_held_book(SYM, identity=_ident(),
                                      same_book=SUPPORTED,
                                      current=b.current(SYM, now=now),
                                      now=now, sla_s=sla)
    assert got["refusal"] == PMD.I_NOT_CURRENT


def test_a_short_position_marks_off_the_institutional_book_as_off_retail():
    b = _resident(bids=((450, 1000),), offers=((470, 500),))
    now = time.time()
    rd = PMD.institutional_held_book(
        SYM, identity=_ident(), same_book=SUPPORTED,
        current=b.current(SYM, now=now), now=now, sla_s=PMF.SLA_S)["read"]
    inst_obs = {"bids": rd["marketData"]["bids"],
                "offers": rd["marketData"]["offers"]}
    retail = H.md(bids=[(0.45, 10)], offers=[(0.47, 5)])
    retail_obs = {"bids": retail["bids"], "offers": retail["offers"]}
    for side in ("LONG", "SHORT"):
        mi = PMF.book_mark(inst_obs, side)
        mr = PMF.book_mark(retail_obs, side)
        assert mi["price"] == mr["price"] and mi["ask"] == mr["ask"]
    # LONG exits at the long bid; SHORT exits at the complement of the long
    # offer -- explicit, never a manufactured NO book
    assert PMF.book_mark(inst_obs, "LONG")["price"] == pytest.approx(0.45)
    assert PMF.book_mark(inst_obs, "SHORT")["price"] == pytest.approx(0.53)


# ═════════════════════════════════════════════════════════════════════
# §3 PER-SYMBOL SAME-BOOK EVIDENCE FROM THE DATABASE
# ═════════════════════════════════════════════════════════════════════

async def _probe_rows(conn, symbol, n, *, verdict="AGREE_TOP_N",
                      exact=True, changed=False):
    for _ in range(n):
        await conn.execute(
            "INSERT INTO institutional_same_book_probe (process_id, service,"
            " symbol, retail_slug, identity_ok, identity, verdict, "
            " stream_changed_in_window) VALUES ('t','test',$1,$1,$2,"
            " $3::jsonb,$4,$5)", symbol, exact,
            '{"institutional_symbol": "%s"}' % (symbol if exact else "x"),
            verdict, changed)


@pg
async def test_same_book_evidence_is_per_symbol_and_exact_only():
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    try:
        u = uuid.uuid4().hex[:8]
        a, b, c, d = ("aec-a-%s" % u, "aec-b-%s" % u, "aec-c-%s" % u,
                      "aec-d-%s" % u)
        await _probe_rows(conn, a, P5R.SAME_BOOK_MIN_COMPARABLE)
        await _probe_rows(conn, b, P5R.SAME_BOOK_MIN_COMPARABLE - 1)
        await _probe_rows(conn, c, 40, exact=False)     # not exact identity
        await _probe_rows(conn, d, 35)
        await _probe_rows(conn, d, 1, verdict="DISAGREE")   # stable: refuted
        got = await PMD.same_book_by_symbol(conn, [a, b, c, d,
                                                   "aec-none-%s" % u])
        assert got[a]["status"] == "SUPPORTED"
        assert got[b]["status"] == "INCONCLUSIVE"
        assert got[c]["status"] == "UNTESTED"
        assert got[d]["status"] == "CONTRADICTED"
        assert "aec-none-%s" % u not in got
        # asking for b alone never borrows a's evidence
        only_b = await PMD.same_book_by_symbol(conn, [b])
        assert set(only_b) == {b} and only_b[b]["status"] != "SUPPORTED"
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4 THE REFRESH'S SOURCE ORDER
# ═════════════════════════════════════════════════════════════════════

class _Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


class _Venue:
    def __init__(self, clock):
        self.clock, self.calls = clock, []

    def __call__(self, slug):
        self.calls.append(slug)
        return {"marketData": H.md(bids=[(0.41, 100)], offers=[(0.43, 100)]),
                "observed_at": self.clock()}


class _Stream:
    def __init__(self, ages):
        self.ages = dict(ages)

    def want(self, slugs):
        return {"queued": len(slugs)}

    def book(self, slug, *, now):
        age = self.ages.get(slug)
        if age is None:
            return None
        return {"marketData": H.md(bids=[(0.44, 50)], offers=[(0.46, 50)]),
                "observed_at": float(now) - age,
                "stream_receipt_age_s": age}


class _Inst(PMD.InstitutionalBooks):
    """The real per-symbol rule over fake identity / resident books; the
    same-book evidence is loaded from the database as in production."""


async def _seed(conn, a, n, at):
    from sportsassets import bettor_paper_simulator as SIM
    slugs = []
    for i in range(n):
        slug = "aec-pmd-%s-%d" % (a["account_id"][-8:], i)
        o = H.order(a, key="k%d" % i, qty=10, limit=0.50, slug=slug, at=at,
                    fixture="fx-%d" % i,
                    group_id="paper_g_%s_%d" % (a["account_id"][-8:], i))
        o["strategy"] = "PINNACLE_COMPLETED_GAME_PAPER"
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
        assert got["ok"], got
        await H.observe(conn, slug, at + 3, offers=[(0.50, 10)],
                        bids=[(0.48, 10)])
        f = await SIM.simulate_order(conn, got["order"]["order_id"],
                                     now=at + 4, fee_fn=H.zero_fee)
        assert f.get("filled_qty") or f.get("state") == "FILLED", f
        slugs.append(slug)
    return slugs


@pg
async def test_the_refresh_consults_institutional_then_stream_then_harvest_then_rest():
    assert PMR.SOURCE_ORDER == ("INSTITUTIONAL_STREAM", "RETAIL_STREAM",
                                "HARVEST", "PACED_REST")
    conn = await H.connect()
    try:
        PMR._HOURLY["reads"] = []
        a = await H.new_account(conn, "pmdorder")
        now = time.time()
        slugs = await _seed(conn, a, 5, now - 2000)
        s_inst, s_stream, s_harv, s_rest, s_unproven = slugs
        books = IS.ResidentBooks()
        books.set_state(IS.S_IDLE, "test")
        books.on_connected("grpc-test")
        for s in (s_inst, s_stream, s_harv, s_rest, s_unproven):
            books.set_instrument(s, dict(AEC, symbol=s))
            books.want([s])
            books.on_update({"symbol": s, "bids": [(420, 1000)],
                             "offers": [(440, 500)],
                             "state": "INSTRUMENT_STATE_OPEN",
                             "transact_time": now - 1})
        # PER-SYMBOL evidence: only s_inst is SUPPORTED. s_unproven has an
        # exact identity and a current book but no evidence of its own.
        tx = conn.transaction()
        await tx.start()
        try:
            await _probe_rows(conn, s_inst, P5R.SAME_BOOK_MIN_COMPARABLE)
            inst = _Inst(identity_fn=lambda s, i: _ident(symbol=s),
                         current_fn=books.current)
            now = time.time()       # after the books arrived: receipts <= now
            clk = _Clock(now)
            v = _Venue(clk)
            # every market except s_rest has a stream book / harvest
            st = _Stream({s_inst: 2.0, s_stream: 3.0, s_unproven: 3.0})

            def recent(slug, *, max_age_s):
                if slug in (s_inst, s_stream, s_harv):
                    return {"marketData": H.md(bids=[(0.4, 1)],
                                               offers=[(0.6, 1)]),
                            "observed_at": now - 5.0}
                return None
            got = await PMR.refresh(conn, account_id=a["account_id"],
                                    market_data=G.PaperMarketDataClient(v),
                                    now=now, clock=clk, recent=recent,
                                    gate=_open_gate, sleep=None, stream=st,
                                    institutional=inst)
            assert got.get("error") is None, got
            oc = got["outcomes"]
            assert oc[s_inst]["outcome"] == PMR.O_INSTITUTIONAL, (inst.refusals, got.get("institutional_evidence"))
            assert oc[s_stream]["outcome"] == PMR.O_STREAM
            assert oc[s_unproven]["outcome"] == PMR.O_STREAM
            assert oc[s_harv]["outcome"] == PMR.O_HARVESTED
            assert oc[s_rest]["outcome"] == PMR.O_READ_OK
            assert v.calls == [s_rest]          # REST only where no stream
            assert got["sources"] == {"institutional_stream": 1,
                                      "retail_stream": 2, "harvest": 1,
                                      "rest": 1, "public_gateway": 0}
            assert got["institutional_books"] == 1
            assert inst.refusals.get(PMD.I_SAME_BOOK) == 4
            row = await conn.fetchrow(
                "SELECT observed_at, venue_ts, read_basis, source, bids "
                "  FROM paper_book_observations WHERE obs_id = $1",
                oc[s_inst]["obs_id"])
            assert row["read_basis"] == "HELD_MARK_INSTITUTIONAL_STREAM"
            assert row["source"] == PMD.INSTITUTIONAL_SOURCE
            assert row["venue_ts"]                      # source timestamp
            recv = books.current(s_inst, now=now)["evidence"]["snapshot"][
                "received_at"]
            assert abs(row["observed_at"].timestamp() - recv) < 1e-3
            assert now - recv <= PMF.SLA_S
            # 306: the per-source counts are on the run record
            run = await conn.fetchrow(
                "SELECT * FROM paper_mark_refresh_runs WHERE run_id = $1",
                got["run_id"])
            if "institutional_books" in run.keys():
                assert run["institutional_books"] == 1
                assert run["stream_books"] == 2
                assert run["skipped_cooldown"] == 0
                assert H.j(run["sources"])["rest"] == 1
                assert "totals" in H.j(run["market_data"])
            # every position classified; held marks counted by source
            res = await PMF.read(conn, a["account_id"], now=now + 1)
            assert res["classified"] == res["open_positions"] == 5
            assert res["fresh_rate"] == 1.0
            f = res["feeds"]
            assert f["held_marks_by_source"]["INSTITUTIONAL_STREAM"] == 1
            assert f["held_marks_by_source"]["RETAIL_STREAM"] == 2
            assert f["held_marks_by_source"]["HARVEST"] == 1
            assert f["held_marks_by_source"]["REST"] == 1
            assert f["oldest_held_mark_age_s"] <= PMF.SLA_S
            # every held market was registered with the owner, held first
            assert set(PMD.held_priority()) == set(slugs)
            # ...and named to the institutional focus universe
            assert set(FU.held_first()) == set(slugs)
        finally:
            await tx.rollback()
    finally:
        await conn.close()


@pg
async def test_a_stale_institutional_book_falls_through_to_the_next_source():
    conn = await H.connect()
    try:
        PMR._HOURLY["reads"] = []
        a = await H.new_account(conn, "pmdstale")
        now = time.time()
        (s,) = await _seed(conn, a, 1, now - 2000)
        old = now - PMF.SLA_S - 30
        cur = {"ok": True, "symbol": s, "book": {"bids": [], "offers": []},
               "evidence": {"snapshot": {
                   "received_at": old,
                   "venue_ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                             time.gmtime(old))},
                   "market": {"price_scale": 1000, "qty_scale": 100}}}
        inst = _Inst(identity_fn=lambda x, i: _ident(symbol=x),
                     current_fn=lambda x, now=None: cur)

        async def load(_conn, _slugs):
            inst.same_book = {s: SUPPORTED}
            return {}
        inst.load = load
        clk = _Clock(now)
        v = _Venue(clk)
        got = await PMR.refresh(conn, account_id=a["account_id"],
                                market_data=G.PaperMarketDataClient(v),
                                now=now, clock=clk, recent=_no_recent,
                                gate=_open_gate, stream=None,
                                institutional=inst)
        assert got["outcomes"][s]["outcome"] == PMR.O_READ_OK
        assert v.calls == [s] and inst.refusals == {PMD.I_STALE: 1}
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §5 TELEMETRY, THE FOCUS UNIVERSE, THE UNCHANGED SLAs
# ═════════════════════════════════════════════════════════════════════

def test_the_owner_telemetry_names_every_field():
    o = PMD.Owner(transport=lambda s, **k: {
        "marketData": _md(), "request_accounting": {"statuses": [429, 200]}},
        recent=_no_recent, gate=_open_gate)
    o.read("t-1")
    t = o.telemetry()
    for k in ("rest_requests_per_min", "responses_2xx_per_min",
              "responses_429_per_min", "totals", "by_lane", "queue_depth",
              "venue_hold", "streams", "held_registered"):
        assert k in t, k
    for k in ("rest_dispatches", "responses_2xx", "responses_429",
              "cache_hits", "coalesced_reads", "queue_deadline_refusals",
              "discovery_deferred_during_hold"):
        assert k in t["totals"], k
    assert t["totals"]["responses_429"] == 1 and \
        t["totals"]["responses_2xx"] == 1
    assert t["rest_requests_per_min"] == 1
    assert set(t["queue_depth"]) == set(PMD.LANES) | {"in_flight"}
    assert set(t["streams"]) == {"retail", "institutional"}
    # a stream not running here is null with the reason, never 0
    assert t["streams"]["retail"]["updates"] is None


def test_the_freshness_endpoint_carries_the_market_data_telemetry():
    from sportsassets.api import command_paper as CP
    fresh = {"feeds": PMF.feed_summary([], now=time.time())}
    got = CP._market_data_telemetry(fresh)
    for k in ("requests_per_min", "responses_2xx", "responses_429",
              "stream_updates", "cache_hits", "coalesced_reads",
              "rest_fallbacks", "queue_depth", "oldest_held_mark_age_s",
              "held_marks_by_source", "fresh_marks_by_source"):
        assert k in got, k
    assert set(got["stream_updates"]) == {"retail", "institutional"}
    assert set(got["held_marks_by_source"]) == set(PMF.MARK_FEEDS)


def test_feed_summary_counts_sources_and_the_oldest_mark():
    now = 1000.0
    rows = [
        {"class": PMF.FRESH, "mark": {"feed": "INSTITUTIONAL_STREAM",
                                      "observed_at": 990.0}},
        {"class": PMF.STALE, "mark": {"feed": "REST", "observed_at": 500.0}},
        {"class": PMF.FEED_GAP, "mark": {"feed": None, "observed_at": None}},
        {"class": PMF.EXTERNAL_UNAVAILABLE,
         "mark": {"feed": "REST", "observed_at": 1.0}}]
    f = PMF.feed_summary(rows, now=now)
    assert f["held_marks_by_source"]["INSTITUTIONAL_STREAM"] == 1
    assert f["fresh_marks_by_source"]["INSTITUTIONAL_STREAM"] == 1
    assert f["held_marks_by_source"]["REST"] == 2
    assert f["oldest_held_mark_age_s"] == 500.0     # terminal excluded
    assert f["never_read_markable"] == 1 and f["oldest_within_sla"] is False
    assert PMF.mark_source({"read_basis": "HELD_MARK_STREAM"}) == \
        "RETAIL_STREAM"
    assert PMF.mark_source({"source": "PAPER_MARKET_DATA_CLIENT:SHARED_READ",
                            "read_basis": "X"}) == "HARVEST"


def test_held_paper_positions_keep_their_slots_in_the_focus_universe():
    def c(slug, **kw):
        return dict({"slug": slug, "why": "w"}, **kw)
    cands = {FU.T_ACTUAL: [c("act-%02d-x" % i) for i in range(4)],
             FU.T_CANDIDATE: [c("cand-%02d-x" % i) for i in range(30)],
             FU.T_UNIVERSE: [c("uni-%02d-x" % i) for i in range(30)],
             FU.T_EXPLORATION: [c("held-%02d-x" % i) for i in range(30)]}
    plain = FU.prioritize(cands)
    assert plain["per_tier"][FU.T_EXPLORATION] == 0     # starved before
    held_first = ["held-29-x", "held-28-x", "never-read-held-x"]
    u = FU.prioritize(cands, held_reserve=FU.HELD_PAPER_RESERVE,
                      held_first=held_first)
    slugs = [m["retail_slug"] for m in u["members"]]
    assert len(slugs) == FU.MAX_MEMBERS
    assert slugs[:4] == ["act-%02d-x" % i for i in range(4)]   # real money
    assert slugs[4:7] == held_first                 # the refresh's order
    assert u["per_tier"][FU.T_EXPLORATION] == FU.HELD_PAPER_RESERVE
    assert u["per_tier"][FU.T_CANDIDATE] == FU.MAX_MEMBERS - 4 - \
        FU.HELD_PAPER_RESERVE
    by = FU.by_slug(u)
    assert by["held-29-x"]["held_reserve"] is True
    assert by["held-29-x"]["tier"] == FU.T_EXPLORATION     # rank unchanged
    assert by["held-29-x"]["diagnostic_only"] is True
    assert by["never-read-held-x"]["tier"] == FU.T_EXPLORATION
    # actual positions are never displaced by the reserve
    many = dict(cands, **{FU.T_ACTUAL: [c("act-%02d-y" % i)
                                        for i in range(40)]})
    u2 = FU.prioritize(many, held_reserve=FU.HELD_PAPER_RESERVE)
    assert u2["per_tier"][FU.T_ACTUAL] == FU.MAX_MEMBERS


@pg
async def test_the_focus_universe_reads_every_open_paper_position_whatever_its_age():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "fuold")
        old = time.time() - 30 * 86400          # far outside any window
        (s,) = await _seed(conn, a, 1, old)
        # no fill-age window (unbounded LIMIT: the shared test database
        # holds other proofs' newer positions)
        rows = await FU._rows(conn, FU.PAPER_POSITIONS_SQL,
                              FU.INVESTMENT_STRATEGY, True, 10 ** 7)
        mine = [r for r in rows if r["us_market_slug"] == s]
        assert len(mine) == 1 and float(mine[0]["open_qty"]) > 0
    finally:
        await conn.close()


@pg
async def test_306_is_idempotent_additive_and_rolls_back_cleanly():
    import pathlib
    mig = pathlib.Path(__file__).resolve().parents[1] / "migrations"
    up = (mig / "306_paper_mark_refresh_market_data.sql").read_text()
    down = (mig / "rollback" /
            "306_paper_mark_refresh_market_data.down.sql").read_text()
    body = "\n".join(ln for ln in up.splitlines()
                     if not ln.lstrip().startswith("--"))
    assert "DROP " not in body and "UPDATE " not in body
    assert body.count("ADD COLUMN IF NOT EXISTS") == 6
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    try:
        cols = ("SELECT count(*) FROM information_schema.columns WHERE "
                " table_name = 'paper_mark_refresh_runs' AND column_name = "
                " ANY($1::text[])")
        await conn.execute(up)
        await conn.execute(up)                  # idempotent
        assert await conn.fetchval(cols, list(PMR.COLUMNS_306)) == 6
        assert await PMR._has_306(conn)
        await conn.execute(down)
        assert await conn.fetchval(cols, list(PMR.COLUMNS_306)) == 0
        assert not await PMR._has_306(conn)
    finally:
        await tx.rollback()
        await conn.close()


def test_the_sla_constants_are_unchanged():
    assert L.MARK_STALE_AFTER_S == 300.0 and PMF.SLA_S == 300.0
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    assert float(LOOP.PINNACLE_MAX_AGE_S) == 30.0
    assert PMR.REFRESH_AFTER_S < PMF.SLA_S
    assert P5R.SAME_BOOK_MIN_COMPARABLE == 30
    assert P5R.SAME_BOOK_MIN_AGREE_RATE == 0.95
