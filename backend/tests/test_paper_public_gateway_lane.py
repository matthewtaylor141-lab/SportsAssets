"""P0 MARKET-DATA FRESHNESS: THE KEYLESS PUBLIC-GATEWAY HELD-MARK LANE.

  * nothing but GET /v1/markets/<slug>/book leaves the keyless client (no
    key header; any other method or path refused before it is sent)
  * the lane has its OWN pace and Retry-After hold: a 429 on the public
    gateway never holds the authenticated key and vice versa; each honours
    its own Retry-After; the gap backs off x2 per 429 (capped)
  * lane selection: held reads only -- discovery / entry reads never use it
  * the held-mark refresh splits held markets across the AUTHENTICATED and
    PUBLIC_GATEWAY lanes, records HELD_MARK_PUBLIC_GATEWAY with both
    timestamps, and a lane on a long hold never starves the other
  * per-lane telemetry: requests / min, 2xx, 429, Retry-After, current hold

Fakes only: an httpx MockTransport for the gateway, fake clocks; no network,
no order.
"""
from __future__ import annotations

import time

import httpx
import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_guard as G
from sportsassets import institutional_same_book as SB
from sportsassets import paper_market_data as PMD
from sportsassets import venue_request_gate as GRT
from sportsassets.agents import paper_mark_refresh as PMR

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
SLUG = "aec-mlb-sd-mil-2026-10-03"


@pytest.fixture(autouse=True)
def _clean():
    GRT.clear_hold()
    PMD.reset()
    yield
    GRT.clear_hold()
    PMD.reset()


def _md(bid="0.45", ask="0.47"):
    return {"bids": [{"px": {"value": bid}, "qty": "10"}],
            "offers": [{"px": {"value": ask}, "qty": "10"}],
            "transactTime": "2026-10-06T12:00:00Z"}


class _FakeClock:
    def __init__(self, t=1000.0):
        self.t = float(t)
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(float(s), 6))
        self.t += float(s)


def _client(lane, seen, *, status=200, headers=None):
    def handler(request):
        seen.append((request.method, request.url.path,
                     {k.lower() for k in request.headers}))
        if status == 200:
            return httpx.Response(200, json={"marketData": _md()})
        return httpx.Response(status, headers=headers or {}, json={})
    return PMD.public_client(lane, inner=httpx.MockTransport(handler))


# ── nothing but the GET book ─────────────────────────────────────────────

def test_the_keyless_client_sends_nothing_but_get_book(monkeypatch):
    monkeypatch.setenv("PMUS_KEY_ID", "NOT-REAL")
    clk = _FakeClock()
    lane = PMD.AuthLaneState(PMD.AUTH_PUBLIC, clock=clk, sleep=clk.sleep)
    seen = []
    c = _client(lane, seen)
    assert c.key_id is None and c.secret_key is None
    assert isinstance(c._http._transport, SB.ReadOnlyTransport)
    r = SB.retail_book_read(SLUG, client=c)
    assert r["ok"] and seen[0][:2] == ("GET", "/v1/markets/%s/book" % SLUG)
    assert not seen[0][2] & {"x-pm-access-key", "x-pm-signature",
                             "authorization"}
    # every other method, and every other GET, is refused before sending
    for call in (lambda: c.post("/v1/orders", body={"marketSlug": SLUG}),
                 lambda: c.delete("/v1/orders/x"),
                 lambda: c._http.get("/v1/portfolio/positions"),
                 lambda: c._http.get("/v1/markets/%s" % SLUG)):
        with pytest.raises(Exception):
            call()
    assert len(seen) == 1
    t = PMD.PublicGatewayTransport(
        httpx.MockTransport(lambda r: httpx.Response(200)), lane)
    with pytest.raises(SB.WriteRefused):
        t.handle_request(httpx.Request("POST", "https://x/v1/markets/%s/book"
                                       % SLUG))
    with pytest.raises(SB.WriteRefused):
        t.handle_request(httpx.Request("GET", "https://x/v1/orders/open"))
    assert lane.telemetry()["totals"]["refused_not_a_book_read"] >= 2


# ── its own pace, its own hold, x2 backoff ───────────────────────────────

def test_a_public_429_holds_only_the_public_lane_with_its_retry_after():
    clk = _FakeClock()
    lane = PMD.AuthLaneState(PMD.AUTH_PUBLIC, clock=clk, sleep=clk.sleep)
    seen = []
    c = _client(lane, seen, status=429, headers={"Retry-After": "9"})
    r = SB.retail_book_read(SLUG, client=c)
    assert not r["ok"] and len(seen) == 1     # one request: no SDK retry
    h = lane.hold()
    assert h["blocking"] and h["seconds_left"] == pytest.approx(9.0)
    tel = lane.telemetry()
    assert tel["totals"]["responses_429"] == 1
    assert tel["retry_after_last_s"] == 9.0
    assert tel["totals"]["retry_after_seen"] == 1
    # the AUTHENTICATED key's not-before instant is untouched
    assert GRT.gate_state()["blocking"] is False
    # a short Retry-After is floored, and a hold only extends
    lane.on_response(429, "1")
    assert lane.hold()["seconds_left"] == pytest.approx(9.0)


def test_an_authenticated_hold_does_not_hold_the_public_lane():
    GRT.hold_until(until_epoch_s=time.time() + 30, reason="KEYED_429")
    lane = PMD.AuthLaneState(PMD.AUTH_PUBLIC)
    assert lane.hold()["blocking"] is False
    sent = []
    o = PMD.Owner(transport=lambda s, **k: {"marketData": _md()},
                  recent=lambda *a, **k: None,
                  public_transport=lambda s, lane=None: sent.append(s) or {
                      "marketData": _md(), "observed_at": time.time()},
                  public_state=lane)
    o.set_held([SLUG])
    got = o.read(SLUG, deadline_epoch_s=time.time() + 2,
                 auth=PMD.AUTH_PUBLIC)
    assert got["marketData"] and sent == [SLUG]
    assert got["auth_lane"] == PMD.AUTH_PUBLIC
    assert got["served_by"] == "PUBLIC_GATEWAY"


def test_the_public_gap_starts_conservative_and_doubles_on_each_429():
    clk = _FakeClock()
    lane = PMD.AuthLaneState(PMD.AUTH_PUBLIC, clock=clk, sleep=clk.sleep)
    assert PMD.PUBLIC_MIN_GAP_S >= 1.0
    assert lane.gap_s() == PMD.PUBLIC_MIN_GAP_S
    lane.pace()
    lane.pace()
    assert clk.slept == [pytest.approx(1.0)]       # one gap between two
    lane.on_response(429, None)
    assert lane.gap_s() == 2.0 * PMD.PUBLIC_MIN_GAP_S
    assert lane.hold()["seconds_left"] == pytest.approx(
        PMD.PUBLIC_HOLD_FLOOR_S)                    # no Retry-After: floor
    lane.on_response(429, "7")
    assert lane.gap_s() == 4.0 * PMD.PUBLIC_MIN_GAP_S
    for _ in range(5):
        lane.on_response(429, "7")
    assert lane.gap_s() == PMD.PUBLIC_MAX_MULT * PMD.PUBLIC_MIN_GAP_S
    clk.t += PMD.PUBLIC_PENALTY_S + 1               # the penalty expires
    assert lane.gap_s() == PMD.PUBLIC_MIN_GAP_S


def test_a_public_hold_longer_than_the_deadline_sends_nothing():
    clk = _FakeClock(time.time())
    lane = PMD.AuthLaneState(PMD.AUTH_PUBLIC, clock=clk, sleep=clk.sleep)
    lane.on_response(429, "9")
    sent = []
    o = PMD.Owner(public_transport=lambda s, lane=None: sent.append(s),
                  public_state=lane, clock=clk, recent=lambda *a, **k: None)
    o.set_held([SLUG])
    got = o.read(SLUG, deadline_epoch_s=clk.t + 3, auth=PMD.AUTH_PUBLIC)
    assert got["error"] == PMD.R_PUBLIC_HOLD and sent == []


# ── lane selection: held only ────────────────────────────────────────────

def test_discovery_and_entry_reads_never_use_the_public_lane(monkeypatch):
    pub, keyed = [], []
    o = PMD.Owner(transport=lambda s, **k: keyed.append(s) or {
        "marketData": _md()},
        recent=lambda *a, **k: None,
        public_transport=lambda s, lane=None: pub.append(s) or {
            "marketData": _md()})
    # a market that is not held is refused on the public lane by name
    got = o.read("disc-x", auth=PMD.AUTH_PUBLIC)
    assert got["error"] == PMD.R_PUBLIC_HELD_ONLY and pub == []
    with PMD.lane(PMD.LANE_MANAGE):
        got = o.read("entry-x", auth=PMD.AUTH_PUBLIC)
    assert got["error"] == PMD.R_PUBLIC_HELD_ONLY and pub == []
    # every PaperMarketDataClient read (Derek, benchmark, pass, work
    # queue) goes through the keyed lane: it cannot name the public one
    monkeypatch.setattr(PMD, "OWNER", o)
    import asyncio
    asyncio.run(G.PaperMarketDataClient().read_book("disc-y"))
    assert keyed == ["disc-y"] and pub == []
    # a public-gateway book is not cached for (nor coalesced into) others
    o.set_held(["held-z"])
    o.read("held-z", auth=PMD.AUTH_PUBLIC)
    assert pub == ["held-z"]
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    assert LOOP.recent_book("held-z", max_age_s=60) is None


def test_per_lane_telemetry_fields():
    t = PMD.telemetry()
    assert set(t["auth_lanes"]) == {PMD.AUTH_KEYED, PMD.AUTH_PUBLIC}
    for name in PMD.AUTH_LANES:
        lane = t["auth_lanes"][name]
        for k in ("requests_per_min", "responses_2xx_per_min",
                  "responses_429_per_min", "retry_after_last_s", "hold",
                  "totals"):
            assert k in lane, (name, k)
        assert "blocking" in lane["hold"] and "seconds_left" in lane["hold"]
    from sportsassets.api import command_paper as CP
    assert "auth_lanes" in CP._market_data_telemetry({})
    assert PMF.mark_source({"read_basis": "HELD_MARK_PUBLIC_GATEWAY"}) == \
        "PUBLIC_GATEWAY"


# ── the refresh: two parallel REST lanes ─────────────────────────────────

class _Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


class _KeyedVenue:
    def __init__(self, clock):
        self.clock, self.calls = clock, []

    def __call__(self, slug):
        self.calls.append(slug)
        return {"marketData": H.md(bids=[(0.41, 100)], offers=[(0.43, 100)]),
                "observed_at": self.clock()}


class _FakePublic:
    """The refresh's public-lane handle with a fake gateway."""

    def __init__(self, clock, *, hold_s=0.0):
        self.clock, self.calls, self.hold_s = clock, [], float(hold_s)

    def hold(self):
        return {"blocking": self.hold_s > 0, "seconds_left": self.hold_s,
                "reason": "PUBLIC_GATEWAY_429"}

    async def read(self, slug, *, deadline):
        self.calls.append(slug)
        return {"marketData": dict(H.md(bids=[(0.44, 10)],
                                        offers=[(0.46, 10)]),
                                   transactTime="2026-10-06T12:00:00Z"),
                "observed_at": self.clock() - 0.5, "auth_lane": "PUBLIC"}


async def _seed(conn, a, n, at):
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_simulator as SIM
    slugs = []
    for i in range(n):
        slug = "aec-pgw-%s-%d" % (a["account_id"][-8:], i)
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


def _open_gate():
    return {"blocking": False, "seconds_left": 0.0, "reason": None}


@pg
async def test_held_markets_split_across_both_rest_lanes():
    conn = await H.connect()
    try:
        PMR._HOURLY["reads"] = []
        a = await H.new_account(conn, "pgwsplit")
        now = time.time()
        slugs = await _seed(conn, a, 6, now - 2000)
        clk = _Clock(now)
        keyed, pub = _KeyedVenue(clk), _FakePublic(clk)
        got = await PMR.refresh(conn, account_id=a["account_id"],
                                market_data=G.PaperMarketDataClient(keyed),
                                now=now, clock=clk,
                                recent=lambda *a, **k: None,
                                gate=_open_gate, stream=None,
                                institutional=None, public=pub)
        assert got.get("error") is None, got
        assert got["public_lane"] == "RUNNING"
        assert keyed.calls and pub.calls                 # both lanes served
        assert sorted(keyed.calls + pub.calls) == sorted(slugs)
        assert not set(keyed.calls) & set(pub.calls)     # one read each
        assert got["sources"]["public_gateway"] == len(pub.calls)
        assert got["sources"]["rest"] == len(keyed.calls)
        assert got["lane_reads"] == {"AUTHENTICATED": len(keyed.calls),
                                     "PUBLIC_GATEWAY": len(pub.calls)}
        s = pub.calls[0]
        row = await conn.fetchrow(
            "SELECT observed_at, venue_ts, read_basis, source FROM "
            " paper_book_observations WHERE obs_id = $1",
            got["outcomes"][s]["obs_id"])
        assert row["read_basis"] == "HELD_MARK_PUBLIC_GATEWAY"
        assert row["source"] == PMD.PUBLIC_SOURCE
        assert row["venue_ts"] == "2026-10-06T12:00:00Z"    # venue clock
        assert abs(row["observed_at"].timestamp() - (now - 0.5)) < 1e-3
        res = await PMF.read(conn, a["account_id"], now=now + 1)
        assert res["fresh_rate"] == 1.0
        assert res["feeds"]["held_marks_by_source"]["PUBLIC_GATEWAY"] == \
            len(pub.calls)
    finally:
        await conn.close()


@pg
async def test_a_lane_on_a_long_hold_never_starves_the_other():
    conn = await H.connect()
    try:
        PMR._HOURLY["reads"] = []
        a = await H.new_account(conn, "pgwhold")
        now = time.time()
        slugs = await _seed(conn, a, 4, now - 2000)
        clk = _Clock(now)
        # the KEYED lane is held for 60 s (longer than any wait): every
        # market goes out on the public gateway
        keyed, pub = _KeyedVenue(clk), _FakePublic(clk)
        got = await PMR.refresh(
            conn, account_id=a["account_id"],
            market_data=G.PaperMarketDataClient(keyed), now=now, clock=clk,
            recent=lambda *a, **k: None,
            gate=lambda: {"blocking": True, "seconds_left": 60.0,
                          "reason": "KEYED_429"},
            stream=None, institutional=None, public=pub)
        assert keyed.calls == [] and sorted(pub.calls) == sorted(slugs)
        assert got["skipped_cooldown"] == 0 and got["read_ok"] == 4
        # and the other way round: the PUBLIC lane held, the keyed serves
        PMR._HOURLY["reads"] = []
        b = await H.new_account(conn, "pgwhold2")
        slugs2 = await _seed(conn, b, 3, now - 2000)
        keyed2, pub2 = _KeyedVenue(clk), _FakePublic(clk, hold_s=60.0)
        got2 = await PMR.refresh(
            conn, account_id=b["account_id"],
            market_data=G.PaperMarketDataClient(keyed2), now=now, clock=clk,
            recent=lambda *a, **k: None, gate=_open_gate, stream=None,
            institutional=None, public=pub2)
        assert pub2.calls == [] and sorted(keyed2.calls) == sorted(slugs2)
        assert got2["read_ok"] == 3
        # both held: nothing is sent, every market recorded SKIPPED with
        # both lanes' reasons -- FEED_GAP, never fresh
        PMR._HOURLY["reads"] = []
        c = await H.new_account(conn, "pgwhold3")
        slugs3 = await _seed(conn, c, 2, now - 2000)
        keyed3, pub3 = _KeyedVenue(clk), _FakePublic(clk, hold_s=60.0)
        got3 = await PMR.refresh(
            conn, account_id=c["account_id"],
            market_data=G.PaperMarketDataClient(keyed3), now=now, clock=clk,
            recent=lambda *a, **k: None,
            gate=lambda: {"blocking": True, "seconds_left": 60.0,
                          "reason": "KEYED_429"},
            stream=None, institutional=None, public=pub3)
        assert keyed3.calls == [] and pub3.calls == []
        assert got3["skipped_cooldown"] == 2
        for s in slugs3:
            why = got3["outcomes"][s]["why"]
            assert "AUTHENTICATED" in why and "PUBLIC_GATEWAY" in why
    finally:
        await conn.close()


def test_a_caller_with_its_own_client_gets_no_public_lane(monkeypatch):
    """The lane is the process read path's only: a refresh handed its own
    market-data client (every test stand-in) runs one keyed lane unless it
    names a public lane itself; the lane can be switched off alone."""
    called = []
    monkeypatch.setattr(PMD, "default_public_lane",
                        lambda: called.append(1) or None)
    import inspect
    src = inspect.getsource(PMR.refresh)
    assert "if public is None and market_data is None" in src
    monkeypatch.setenv(PMD.PUBLIC_ENV_FLAG, "off")
    assert PMD.public_lane_enabled() is False
    monkeypatch.setenv(PMD.PUBLIC_ENV_FLAG, "on")
    assert PMD.public_lane_enabled() is True
