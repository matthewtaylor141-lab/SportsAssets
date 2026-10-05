"""CHAOS: A PROVIDER THAT GOES SILENT, AND A SETTLEMENT THAT ARRIVES LATE
(program section 28), against the real code and Postgres (RN1X_TEST_DSN).

PROVIDER DISCONNECT -> STALE -> WAITING_FOR_FRESH_EVIDENCE, NO DECISION ON
STALE DATA. The fault is injected where production loses the provider:

  * the PinnAPI socket owner (pinnapi_owner.FeedOwner) reaches OWNER_SYNCED on
    a fake provider socket, then the provider drops the connection and every
    reconnect fails. Its authority is revoked, its quotes are unreadable, and
    the heartbeat the feed runtime publishes (pinnapi_feed_runtime.
    _write_heartbeat over `digest()`, the real writer) says so -- and the
    agents' work-state reader (agent_work_state.read_facts / derive, the real
    reader of that row) turns it into BLOCKED_ON_MARKET_DATA for Xavier with
    open inventory: never IDLE, never REVIEWING on a dead feed;
  * the completed-game investment policy, run through the real paper pass,
    refuses to ENTER on a Pinnacle reading the silent provider left behind
    (older than the 30-second rule, never loosened) and records no order, no
    canonical intent and no execution intent -- then ENTERS once a fresh
    reading lands;
  * Xavier's review of a held position whose probability went stale records
    WAITING_FOR_FRESH_EVIDENCE, enqueues the reacquisition work, and sends no
    discretionary sale even though the book would pay well.

DELAYED SETTLEMENT. The game is over but the venue has not published a
settlement: the real settlement writer (ext_pinnacle_loop.join_outcomes, over
the real venue reader bettor_live_read.read_resolution, over the real SDK
client with only the socket replaced) records the attempt and NO outcome;
the real settlement reader (paper_xavier.step_settle) books nothing, the
position stays open and in Xavier's inventory. When the venue publishes, the
same writer records the outcome, the reader books it through the paper
ledger exactly once (re-running both books nothing more) and the ledger
stays consistent. This is also the settlement writer -> settlement reader ->
ledger CONTRACT: the reader reads the row the writer actually wrote, not a
hand-built outcome (the fixtures' `settle_valuation` shortcut is not used).

All market data is synthetic; nothing touches the live paper account; no
venue, no credential, no network.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx
import pytest

from sportsassets import agent_work_state as AWS
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as RT
from sportsassets import pinnapi_owner as O
from sportsassets import pmus
from sportsassets import venue_request_gate as GRT
from sportsassets import venue_pace as VP
from sportsassets import venue_sdk
from sportsassets import xavier_freshness as XF
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import work_queue as WQ
from sportsassets.workers import ext_pinnacle_loop as LOOP
from sportsassets import bettor_paper_ledger as L

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_pinnapi_feed_ownership as OWN
from tests import test_xavier_review_probability_freshness as XR

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FEE = H.flat_fee(0.01)
AT = XR.AT


async def _tx():
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    return conn, tx


# ═════════════ 1 · NO ENTRY ON THE LAST PRICE OF A SILENT PROVIDER ═══════

async def _nosleep(_):
    return None


@pytest.fixture
def cg_on(monkeypatch, new_strategies_off):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()


@pg
async def test_a_silent_providers_last_price_never_enters_and_a_fresh_one_does(cg_on):
    conn = await H.connect()
    now = time.time() + 5.0
    try:
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "silent", now=now)
        t = PL.Transport(now)
        # the provider's last reading: valued 10 s ago from a Pinnacle price
        # already 25 s old then -- 35 s old at the decision, past the rule
        stale = await PL.valuation(conn, decided_at=now - 10, pin_age_s=25.0,
                                   p_pin=0.62, compatibility="INCOMPATIBLE")
        t.set(stale["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        client = PL.client(t)

        async def run(at):
            t.t = max(t.t, float(at))
            return await PR.paper_pass(conn, now=at, account_id=acct["account_id"],
                                       market_data=client, config=acct["config"],
                                       force=True, fee_fn=FEE, sleep=_nosleep)
        p1 = await run(now)
        assert p1["ran"] and not p1["errors"], p1["errors"]
        d = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"],
            stale["valuation_id"], PB.CG_STRATEGY)
        # the decision is RECORDED, refused by name -- the stale probability
        assert d is not None, p1
        assert d["verdict"] != "ENTER"
        refusals = list(d["refusals"] or []) + [d["refusal"] or ""]
        assert PB.R_STALE in refusals, refusals
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE account_id=$1",
            acct["account_id"]) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM execution_intents WHERE us_market_slug=$1",
            stale["slug"]) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM canonical_decision_intents WHERE "
            " us_market_slug=$1", stale["slug"]) == 0
        # the provider reconnects: a fresh reading of the same contract
        fresh = await PL.valuation(conn, slug=stale["slug"], decided_at=now + 20,
                                   pin_age_s=3.0, p_pin=0.62,
                                   compatibility="INCOMPATIBLE")
        p2 = await run(now + 21)
        assert not p2["errors"], p2["errors"]
        d2 = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"],
            fresh["valuation_id"], PB.CG_STRATEGY)
        assert d2 is not None and d2["verdict"] == "ENTER", (
            d2 and (d2["refusal"], d2["refusals"]))
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE account_id=$1 AND "
            " role='ENTRY'", acct["account_id"]) == 1
    finally:
        await PL.purge_everything(conn)
        await conn.close()


# ═════════════ 2 · A DEAD FEED BLOCKS XAVIER; HIS STALE REVIEW WAITS ══════

class DroppingWS(OWN.FakeWS):
    """The provider's socket: delivers the snapshot and one change (the
    cache becomes authoritative), then the provider drops the connection."""

    def __init__(self, frames, drop):
        super().__init__(frames)
        self.drop = drop

    async def recv(self):
        if not self.frames and self.drop.is_set():
            raise ConnectionError("provider closed the connection")
        return await super().recv()


@pg
async def test_a_dropped_provider_revokes_the_feed_and_blocks_xavier_on_market_data(
        monkeypatch):
    import asyncpg
    monkeypatch.setenv("pinnapi_key", "k-test")
    drop, down = asyncio.Event(), asyncio.Event()
    sockets = []

    async def lease_factory():
        return await O.Lease.open(H.DSN)

    async def connect(url, key):
        if down.is_set():
            raise OSError("provider unreachable")
        ws = DroppingWS(OWN.frames_for(), drop)
        sockets.append(ws)
        return ws
    cache = F.FeedCache()
    owner = O.FeedOwner(cache, sport_ids=[6], lease_factory=lease_factory,
                        connect=connect, liveness_s=0.2, standby_s=0.2)
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    conn, tx = await _tx()
    before = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", RT.HEARTBEAT_KEY)
    prev_owner = RT._STATE.get("owner")
    task = asyncio.create_task(owner.run())
    try:
        RT._STATE["owner"] = owner
        assert await OWN.until(lambda: owner.state == "OWNER_SYNCED")
        assert cache.read(1, "s;0;m", evaluated_ms=2_000)["ok"] is True
        # the real heartbeat writer, then the real work-state reader
        await RT._write_heartbeat(pool, dict(RT.digest(), beat_at=time.time()))
        a, g, slug = await XR._held(conn, "feed", entry_age_s=3600)
        healthy = await AWS.read_facts(conn, now=time.time())
        feed = AWS.market_status(healthy["facts"]["XAVIER"]["market"],
                                 time.time())["feed"]
        assert feed["recorded"] and feed["blocked"] is False, feed
        # ── the provider drops the socket and stays unreachable ──
        down.set()
        drop.set()
        assert await OWN.until(lambda: not cache.authority.granted)
        assert cache.read(1, "s;0;m", evaluated_ms=2_000)["ok"] is False
        assert await OWN.until(lambda: owner.state != "OWNER_SYNCED"), owner.state
        # his review of the held position on the stale probability waits,
        # enqueues the reacquisition, and sells nothing (the 0.80 bid would
        # out-value holding at the entry's 0.62)
        await PX.review_group(conn, XR._ctx(a, AT), g, trigger=PX.T_BACKSTOP)
        rv = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                 " group_id=$1 ORDER BY reviewed_at DESC "
                                 " LIMIT 1", g)
        assert rv["recommendation"] == XF.REC_WAITING
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role "
            " IN ('EXIT','REDUCE')", g) == 0
        kinds = {r["kind"] for r in await conn.fetch(
            "SELECT kind FROM agent_work_open WHERE group_id=$1", g)}
        assert WQ.K_PROBABILITY in kinds, kinds
        # the dead feed, published by the real writer, read by the real reader
        await RT._write_heartbeat(pool, dict(RT.digest(), beat_at=time.time()))
        dead = await AWS.read_facts(conn, now=time.time())
        x = dead["facts"]["XAVIER"]
        feed = AWS.market_status(x["market"], time.time())["feed"]
        assert feed["blocked"] is True and feed["why"].startswith("FEED_"), feed
        assert feed["why"] != "FEED_OWNER_SYNCED"
        # THIS position (the shared test database also holds other proofs'
        # positions, which would decide the agent-level precedence): blocked
        # on market data, and Xavier holding only it is BLOCKED_ON_MARKET_DATA
        mine = [p for p in x["positions"] if p["group_id"] == g]
        assert len(mine) == 1
        cls, why = AWS.position_class(
            mine[0], market=AWS.market_status(x["market"], time.time()),
            now=time.time())
        assert cls == AWS.P_BLOCKED, (cls, why)
        st = AWS.derive("XAVIER", dict(x, positions=mine, open_positions=1),
                        now=time.time())
        assert st["state"] == AWS.BLOCKED, st
        assert len(sockets) == 1, "an unreachable provider opened no socket"
    finally:
        owner.stop()
        try:
            await asyncio.wait_for(task, 10)
        finally:
            RT._STATE["owner"] = prev_owner
            await tx.rollback()
            await conn.close()
            if before is None:
                await pool.execute("DELETE FROM ingestion_state WHERE key=$1",
                                   RT.HEARTBEAT_KEY)
            else:
                await pool.execute(
                    "UPDATE ingestion_state SET value=$2::jsonb WHERE key=$1",
                    RT.HEARTBEAT_KEY, before if isinstance(before, str)
                    else json.dumps(before))
            await pool.close()


# ═════════════ 3 · A DELAYED SETTLEMENT, BOOKED ONCE WHEN IT ARRIVES ══════

FAKE_KEY_ID = "00000000-0000-0000-0000-000000000000"
FAKE_SECRET = base64.b64encode(bytes(32)).decode()


class Venue:
    """The venue's settlement surface, scripted per slug: PENDING (the
    settlement endpoint 404s, as an unsettled market does) until `settle`."""

    def __init__(self):
        self.settled: dict = {}
        self.calls: list = []

    def settle(self, slug, long_price: str):
        self.settled[slug] = long_price

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/settlement"):
            slug = path.split("/")[-2]
            if slug not in self.settled:
                return httpx.Response(404, json={"message": "not settled"},
                                      request=request)
            return httpx.Response(200, json={
                "marketSlug": slug,
                "settlementPrice": {"value": self.settled[slug],
                                    "currency": "USD"},
                "settledAt": "2026-10-04T23:59:00Z"}, request=request)
        if path == "/v1/markets":
            slug = request.url.params.get("slug")
            if slug in self.settled:
                px = float(self.settled[slug])
                return httpx.Response(200, json={"markets": [{
                    "slug": slug, "closed": True,
                    "marketSides": [{"long": True, "price": px},
                                    {"long": False, "price": 1.0 - px}]}]},
                    request=request)
            return httpx.Response(200, json={"markets": []}, request=request)
        return httpx.Response(418, json={"message": "unexpected"},
                              request=request)


def _install_venue(monkeypatch, venue: Venue):
    client = pmus_client = None
    from polymarket_us import PolymarketUS
    client = PolymarketUS(key_id=FAKE_KEY_ID, secret_key=FAKE_SECRET,
                          gateway_base_url="https://gateway.test",
                          api_base_url="https://api.test",
                          **venue_sdk.client_kwargs())
    client._http = httpx.Client(transport=httpx.MockTransport(venue.handler))
    assert pmus._install_request_gate(client)["installed"] is True
    monkeypatch.setattr(pmus, "_client", client, raising=False)
    monkeypatch.setattr(VP, "MIN_GAP_S", 0.0)
    GRT.clear_hold()
    return pmus_client


async def _settlement_rows(conn, g):
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM paper_settlements WHERE group_id=$1", g)]


async def _ledger_settlements(conn, account_id):
    return await conn.fetchval(
        "SELECT count(*) FROM paper_ledger WHERE account_id=$1 AND "
        " kind='SETTLEMENT'", account_id)


@pg
async def test_a_delayed_settlement_keeps_the_position_open_and_is_booked_once_on_arrival(
        monkeypatch):
    venue = Venue()
    _install_venue(monkeypatch, venue)
    conn, tx = await _tx()
    try:
        a, g, slug = await XR._held(conn, "dset", entry_age_s=3 * 3600)
        ctx = XR._ctx(a, AT + 7200)
        pos = next(p for p in await L.positions(conn, a["account_id"])
                   if p["group_id"] == g)
        qty = pos["open_qty"]
        assert qty > 0
        # ── the game is over; the venue has not settled ──
        j1 = await LOOP.join_outcomes(conn, limit=10_000)
        assert j1["ran"], j1
        row = await conn.fetchrow(
            "SELECT outcome_known, outcome_basis, settlement_read_at FROM "
            " external_valuations WHERE us_market_slug=$1", slug)
        assert row["outcome_known"] is False and row["outcome_basis"] is None
        assert row["settlement_read_at"] is not None, "the attempt is stamped"
        s1 = await PX.step_settle(conn, ctx)
        assert s1["waiting"] >= 1 and s1["settled"] == 0, s1
        assert await _settlement_rows(conn, g) == []
        assert await _ledger_settlements(conn, a["account_id"]) == 0
        still = next(p for p in await L.positions(conn, a["account_id"])
                     if p["group_id"] == g)
        assert still["open_qty"] == qty            # unknown is never "closed"
        # Xavier still owns it: never idle with unsettled inventory
        facts = await AWS.read_facts(conn, now=AT + 7200)
        assert any(p["group_id"] == g for p in
                   facts["facts"]["XAVIER"]["positions"])
        assert AWS.derive("XAVIER", facts["facts"]["XAVIER"],
                          now=AT + 7200)["state"] != AWS.IDLE
        # ── hours later the venue publishes: the long side paid 1 ──
        venue.settle(slug, "1")
        await conn.execute(
            "UPDATE external_valuations SET settlement_read_at = NULL "
            " WHERE us_market_slug=$1", slug)      # due for its next read
        j2 = await LOOP.join_outcomes(conn, limit=10_000)
        assert j2["resolved"] >= 1, j2
        row = await conn.fetchrow(
            "SELECT outcome_known, outcome, outcome_basis FROM "
            " external_valuations WHERE us_market_slug=$1", slug)
        assert row["outcome_known"] is True and row["outcome"] == 1
        assert row["outcome_basis"] in PX.LABEL_BASES, row["outcome_basis"]
        s2 = await PX.step_settle(conn, ctx)
        assert s2["settled"] == 1, s2
        rows = await _settlement_rows(conn, g)
        assert len(rows) == 1 and rows[0]["outcome"] == "WON"
        assert float(rows[0]["payout_usd"]) == pytest.approx(float(qty))
        assert await _ledger_settlements(conn, a["account_id"]) == 1
        # ── re-running writer and reader books nothing more ──
        await LOOP.join_outcomes(conn, limit=10_000)
        s3 = await PX.step_settle(conn, ctx)
        assert s3["settled"] == 0
        assert len(await _settlement_rows(conn, g)) == 1
        assert await _ledger_settlements(conn, a["account_id"]) == 1
        closed = [p for p in await L.positions(conn, a["account_id"])
                  if p["group_id"] == g]
        assert not closed or closed[0]["open_qty"] <= 1e-9
        b = await L.balances(conn, a["account_id"], now=AT + 7300)
        assert b["ledger_consistent"] is True
    finally:
        await tx.rollback()
        await conn.close()
        GRT.clear_hold()
