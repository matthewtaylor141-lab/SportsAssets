"""TRADER MODE ON A REAL DATABASE (developer pass integration).

The package proved its projection with doubles; these run the read model's
SQL (trader_readmodel.READ_SQL) and the score writer on Postgres with rows
the real ledger writers produce:

  * exact-position joins: a two-market group never attaches a group-only
    (legacy, position-less) review to either sibling
  * partial close: open quantity is the canonical bought - sold - settled
  * SHORT-side books: the held side's bid is the complement of the YES ask
  * protection: a cancelled order is not standing; a quantity that no longer
    matches the open quantity is not valid protection
  * no successful book read: the mark is not current, named
  * an invalid (future) probability source clock is never current
  * total count / truncation are honest
  * scores: append-only, deduplicated, ordered, rolled back as a unit
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import asyncpg
import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import trader_score_evidence as SE
from sportsassets.api import trader_readmodel as TR
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
T = H.T0


def _ctx(a, now):
    return {"account_id": a["account_id"], "session_id": a["session_id"],
            "config": a["config"], "now": now, "clock": lambda: now,
            "session": {"session_id": a["session_id"], "config": a["config"],
                        "reporting_tz": "America/New_York"},
            "fee_fn": H.zero_fee, "deadline": 1e18}


async def _fill(conn, a, *, slug, group, side="LONG", qty=100, limit=0.40,
                at=T, key="e"):
    o = H.order(a, key=key, qty=qty, limit=limit, slug=slug, at=at,
                group_id=group, holding_side=side)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got["ok"], got
    if side == "LONG":
        await H.observe(conn, slug, at + 3, offers=[(limit, qty)],
                        bids=[(limit - 0.02, qty)])
    else:   # a SHORT is bought from the YES bids: cost = 1 - bid
        await H.observe(conn, slug, at + 3, bids=[(1 - limit, qty)],
                        offers=[(1 - limit + 0.02, qty)])
    r = await SIM.simulate_order(conn, got["order"]["order_id"], now=at + 4,
                                 fee_fn=H.zero_fee)
    assert r["state"] == "FILLED", r
    return got["order"]["order_id"]


async def _review(conn, a, *, group, at, position_key=None, complete=True,
                  p_at=None, protection_order=None, recommendation="HOLD"):
    sel = {"management_packet": {
        "gate": {"complete": complete, "missing": [] if complete else
                 ["NO_FRESH_PROBABILITY"]},
        "protection": {"order_id": protection_order}}}
    if position_key is not None:
        sel["management_packet"]["position"] = {"position_key": position_key}
    rid = "paperrev:t%s" % uuid.uuid4().hex[:20]
    await conn.execute(
        "INSERT INTO paper_xavier_reviews (review_id, session_id, account_id,"
        " group_id, reviewed_at, trigger, recommendation, alternatives, "
        " selection, exposure, measure) VALUES ($1,$2,$3,$4,to_timestamp($5),"
        " 'SCHEDULED_BACKSTOP',$6,'{}'::jsonb,$7::jsonb,'{}'::jsonb,$8::jsonb)",
        rid, a["session_id"], a["account_id"], group, at, recommendation,
        json.dumps(sel), json.dumps({
            "p": 0.6, "probability": 0.6,
            "evidence_state": "FRESH_CURRENT_PROBABILITY",
            "probability_source_at": p_at if p_at is not None else at - 2}))
    return rid


async def _read(a, now, **kw):
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    try:
        return await TR.read(pool, account_id=a["account_id"], now=now, **kw)
    finally:
        await pool.close()


def _pid(a, group, slug, side):
    return "paperpos:%s:%s:%s:%s" % (a["account_id"], group, slug, side)


@pg
async def test_exact_position_review_protection_and_marks():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "trdr")
        g = "paper_g_%s_one" % a["account_id"][-10:]
        s = "%s:one" % a["account_id"]
        await _fill(conn, a, slug=s, group=g)
        [prot] = await H.protect(conn, _ctx(a, T + 5), g, at=T + 5)
        await H.observe(conn, s, T + 6, offers=[(0.42, 50)],
                        bids=[(0.41, 50)])
        pid = _pid(a, g, s, "LONG")
        await _review(conn, a, group=g, at=T + 7, position_key=pid,
                      protection_order=prot)
        snap = await _read(a, T + 8)
        assert snap["total_position_count"] == 1 and not snap["truncated"]
        [p] = snap["positions"]
        assert p["position_id"] == pid and p["qty"] == 100.0
        assert p["quote"]["current"] and p["quote"]["bid"] == 0.41
        assert [o["order_id"] for o in p["orders"]] == [prot]
        assert p["orders"][0]["gap"]["is_fill"] is False
        assert p["packet"]["complete"], p["packet"]
        assert p["packet"]["current_recommendation"] == "HOLD"
        # 31 s later the probability has expired: the mark stays current,
        # the recommendation is NOT current
        late = await _read(a, T + 7 + 31)
        [q] = late["positions"]
        assert q["quote"]["current"]
        assert not q["packet"]["complete"]
        assert "NO_FRESH_PROBABILITY" in q["packet"]["missing"]
        assert q["packet"]["current_recommendation"] is None
        assert q["packet"]["recorded_recommendation"] == "HOLD"
        # a future source clock is never current
        await _review(conn, a, group=g, at=T + 9, position_key=pid,
                      protection_order=prot, p_at=T + 60)
        fut = await _read(a, T + 10)
        assert not fut["positions"][0]["packet"]["probability_current"]
    finally:
        await conn.close()


@pg
async def test_a_two_market_group_never_borrows_a_group_only_review():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "trdr2")
        g = "paper_g_%s_two" % a["account_id"][-10:]
        s1, s2 = "%s:m1" % a["account_id"], "%s:m2" % a["account_id"]
        await _fill(conn, a, slug=s1, group=g, key="e1")
        await _fill(conn, a, slug=s2, group=g, key="e2", at=T + 1)
        await _review(conn, a, group=g, at=T + 7, position_key=None)
        snap = await _read(a, T + 8)
        assert snap["total_position_count"] == 2
        for p in snap["positions"]:
            assert p["review"] == {} or not p["review"], p["review"]
            assert "NO_POSITION_REVIEW" in p["packet"]["missing"]
        # an exact-position review attaches to its own market only
        await _review(conn, a, group=g, at=T + 9,
                      position_key=_pid(a, g, s2, "LONG"))
        snap = await _read(a, T + 10)
        by = {p["market_id"]: p for p in snap["positions"]}
        assert by[s2]["review"].get("review_id")
        assert not by[s1]["review"]
    finally:
        await conn.close()


@pg
async def test_partial_close_short_side_cancelled_and_mismatched_protection():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "trdr3")
        g = "paper_g_%s_sh" % a["account_id"][-10:]
        s = "%s:sh" % a["account_id"]
        await _fill(conn, a, slug=s, group=g, side="SHORT")
        [prot] = await H.protect(conn, _ctx(a, T + 5), g, at=T + 5)
        # YES 0.55 bid / 0.57 ask: the SHORT's exit (bid) is 1 - 0.57
        await H.observe(conn, s, T + 6, bids=[(0.55, 40)],
                        offers=[(0.57, 40)])
        pid = _pid(a, g, s, "SHORT")
        await _review(conn, a, group=g, at=T + 7, position_key=pid,
                      protection_order=prot)
        snap = await _read(a, T + 8)
        [p] = snap["positions"]
        assert abs(p["quote"]["bid"] - 0.43) < 1e-9
        # partial close: a 40-contract sale leaves 60 open, the 100-contract
        # protection no longer matches the open quantity
        await conn.execute(
            "UPDATE paper_orders SET state='CANCELED' WHERE order_id=$1",
            prot)
        sale = H.order(a, key="x", qty=40, limit=0.40, slug=s, at=T + 9,
                       group_id=g, holding_side="SHORT", direction="SELL",
                       role="EXIT")
        got = await L.submit_order(conn, sale, fee_fn=H.zero_fee, now=T + 9)
        assert got["ok"], got
        await H.observe(conn, s, T + 12, bids=[(0.50, 40)],
                        offers=[(0.52, 40)])
        r = await SIM.simulate_order(conn, got["order"]["order_id"],
                                     now=T + 13, fee_fn=H.zero_fee)
        assert r["state"] == "FILLED", r
        snap = await _read(a, T + 14)
        [p] = snap["positions"]
        assert p["qty"] == 60.0
        assert all(o["order_id"] != prot for o in p["orders"])
        assert "NO_VALID_ACTIVE_PROTECTION" in p["packet"]["missing"]
        assert not p["packet"]["complete"]
    finally:
        await conn.close()


@pg
async def test_no_successful_book_read_and_honest_truncation(monkeypatch):
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "trdr4")
        g = "paper_g_%s_nb" % a["account_id"][-10:]
        s = "%s:nb" % a["account_id"]
        await _fill(conn, a, slug=s, group=g)
        g2 = "paper_g_%s_nb2" % a["account_id"][-10:]
        await _fill(conn, a, slug="%s:nb2" % a["account_id"], group=g2,
                    key="e2")
        # 400 s later the only successful reads are past the 300 s limit
        snap = await _read(a, T + 400)
        for p in snap["positions"]:
            assert p["quote"]["current"] is False
            assert p["quote"]["why"]
            assert "NO_CURRENT_EXECUTABLE_BOOK" in p["packet"]["missing"]
        monkeypatch.setattr(TR, "MAX_POSITIONS", 1)
        cut = await _read(a, T + 400)
        assert cut["total_position_count"] == 2
        assert cut["returned_position_count"] == 1 and cut["truncated"]
    finally:
        await conn.close()


def _score(eid, at, seq=1, home=1, away=0):
    return {"event_id": eid, "provider_event_id": "p-" + eid,
            "home_id": "h", "away_id": "w", "sport": "BASEBALL",
            "league": "MLB", "source": "test-source", "source_at": at,
            "source_sequence": seq, "game_status": "LIVE", "home": "H",
            "away": "W", "home_score": home, "away_score": away,
            "inning": 3, "inning_half": "TOP", "outs": 1, "balls": 0,
            "strikes": 0, "bases": [False, False, False]}


def _bind(eid):
    return {"event_id": eid, "provider_event_id": "p-" + eid, "home_id": "h",
            "away_id": "w", "sport": "BASEBALL", "league": "MLB",
            "source": "test-source", "verified": True,
            "evidence_id": "audited-binding-test"}


@pg
async def test_scores_append_dedupe_order_and_roll_back():
    conn = await H.connect()
    eid = "ev-%s" % uuid.uuid4().hex[:12]
    now = time.time()
    try:
        a = await SE.record(conn, _score(eid, now - 2), mapping=_bind(eid),
                            now=now)
        assert a["recorded"], a
        dup = await SE.record(conn, _score(eid, now - 2), mapping=_bind(eid),
                              now=now)
        assert dup["recorded"] is False and dup["duplicate"]
        old = await SE.record(conn, _score(eid, now - 5, home=9),
                              mapping=_bind(eid), now=now)
        assert old == {"recorded": False,
                       "why": "SCORE_SOURCE_TIME_REGRESSION"}
        same = await SE.record(conn, _score(eid, now - 2, home=2),
                               mapping=_bind(eid), now=now)
        assert same["why"] == \
            "SCORE_SAME_TIME_CONFLICT_WITHOUT_ADVANCING_SEQUENCE"
        fixed = await SE.record(conn, _score(eid, now - 2, seq=2, home=2),
                                mapping=_bind(eid), now=now)
        assert fixed["recorded"], fixed
        unbound = dict(_bind(eid), verified=False)
        assert (await SE.record(conn, _score(eid, now - 1),
                                mapping=unbound, now=now))["why"] == \
            "CANONICAL_SCORE_MAPPING_NOT_VERIFIED"
        n = await conn.fetchval(
            "SELECT count(*) FROM market_plane_events WHERE "
            " kind='TRADER_GAME_STATE' AND contract_id=$1", "game:" + eid)
        assert n == 2
        # an enclosing transaction that fails takes the write with it
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                got = await SE.record(conn, _score(eid, now - 1, seq=3),
                                      mapping=_bind(eid), now=now)
                assert got["recorded"]
                raise RuntimeError("caller failed after the write")
        assert await conn.fetchval(
            "SELECT count(*) FROM market_plane_events WHERE "
            " kind='TRADER_GAME_STATE' AND contract_id=$1",
            "game:" + eid) == 2
    finally:
        await conn.close()


@pg
async def test_concurrent_score_writers_serialize_on_the_event():
    eid = "ev-%s" % uuid.uuid4().hex[:12]
    now = time.time()

    async def one(seq):
        c = await H.connect()
        try:
            return await SE.record(c, _score(eid, now - 1, seq=seq, home=seq),
                                   mapping=_bind(eid), now=now)
        finally:
            await c.close()
    got = await asyncio.gather(one(1), one(2))
    # same source time: exactly the advancing sequence can follow the first
    assert sum(1 for g in got if g["recorded"]) >= 1
    conn = await H.connect()
    try:
        rows = await conn.fetch(
            "SELECT payload FROM market_plane_events WHERE "
            " kind='TRADER_GAME_STATE' AND contract_id=$1", "game:" + eid)
        seqs = sorted(json.loads(r["payload"])["source_sequence"]
                      for r in rows)
        assert seqs in ([1], [2], [1, 2])
    finally:
        await conn.close()


def test_the_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient
    from sportsassets.api import app as APP
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == "/api/command/paper/trader-mode":
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert set(paths) == {"/api/command/paper/trader-mode"}
    assert paths["/api/command/paper/trader-mode"] <= {"GET", "HEAD"}
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get("/api/command/paper/trader-mode").status_code == 401
    assert client.post("/api/command/paper/trader-mode").status_code in (
        401, 405)


def test_the_read_model_holds_no_order_or_venue_authority():
    import ast
    import pathlib
    root = pathlib.Path(TR.__file__).resolve().parents[1]
    for rel in ("api/trader_readmodel.py", "trader_mode.py",
                "trader_score_evidence.py"):
        tree = ast.parse((root / rel).read_text())
        mods = {n.module or "" for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
            for a in n.names}
        for bad in ("pmus", "execmirror", "live_executor", "kalshi_orders",
                    "bettor_funded_execution", "pmx"):
            assert not any(m.split(".")[-1] == bad for m in mods), (rel, m)
