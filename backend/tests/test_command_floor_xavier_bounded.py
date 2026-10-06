"""CAPITAL-CRITICAL: GET /api/command/floor/xavier IS BOUNDED.

Production saw the Command surface's Xavier workspace answer 499 (the
client gave up). Root cause: the work-state read behind every floor route
fetched each held market's latest venue book with

    SELECT DISTINCT ON (us_market_slug) ... FROM paper_book_observations
     WHERE us_market_slug = ANY($1) ORDER BY us_market_slug, observed_at
     DESC, obs_id DESC

which reads and sorts EVERY observation ever recorded for every held market
-- a history scan that grows with each book read (0.54 s locally at 600
reads per market for 500 markets; production records a read per held market
every few seconds, past the 5 s statement timeout). Beside it the route
carried every review's full selection, and read a day of collaboration
edges row by row.

  §1 BOUNDED QUERIES. A seeded account with many positions and a long
     review history is read in a FIXED number of queries: doubling the
     positions and the history per position adds none (no N+1, no
     per-position sub-query from Python).
  §2 BOUNDED SIZE. No review's stored blobs (alternatives, the full
     selection, the measure) reach the payload; the payload stays far under
     MAX_DETAIL_BYTES; the position book is cut at MAX_DETAIL_POSITIONS with
     `positions_truncated`; over the byte bound the longest lists are cut
     with each cut named under `truncated`.
  §3 SEMANTICS KEPT. Each open position carries its ONE current review
     (the newest, never a superseded one), its complete packet fields and
     its protection continuity; a CURRENT EXIT / REDUCE shows as the action,
     a non-current one only as recorded.
  §4 THE BOOK READ IS ONE INDEX PROBE PER SLUG and picks the same row the
     old query did (newest observed_at, ties to the newest obs_id).
  §5 EDGES AGGREGATED IN SQL keep their exact counts; the detail route's
     short cache serves repeats inside DETAIL_CACHE_S.
  §6 LISTED on the capital-critical list.
"""
from __future__ import annotations

import json
import pathlib
import time
import uuid

import pytest

from sportsassets import agent_work_state as W
from sportsassets.api import command_floor as FL
from tests import paper_harness as H

ROOT = pathlib.Path(__file__).resolve().parents[1]
pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
STRATEGY = "PINNACLE_EXPLORATION_PAPER"
PAD = "BLOB_THAT_MUST_NOT_REACH_THE_PAYLOAD_"


class Counting:
    """An asyncpg connection that counts every statement sent."""

    def __init__(self, conn):
        self._c = conn
        self.sql: list = []

    def __getattr__(self, k):
        return getattr(self._c, k)

    def _wrap(name):
        async def f(self, sql, *a, **kw):
            self.sql.append(sql)
            return await getattr(self._c, name)(sql, *a, **kw)
        return f
    fetch = _wrap("fetch")
    fetchrow = _wrap("fetchrow")
    fetchval = _wrap("fetchval")
    execute = _wrap("execute")


async def _position(conn, acct, gid, slug, at):
    oid = "paperord:xb%s" % uuid.uuid4().hex[:12]
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, fixture, label, order_type, time_in_force, "
        " allow_partial, qty, limit_price, wire_price, filled_qty, state, "
        " decided_at, eligible_at, expires_at, simulator_version, strategy, "
        " terminal_at) VALUES ($1,$1,$2,$3,$4,'ENTRY','BUY','LONG',"
        " 'ORDER_INTENT_BUY_LONG',$5,$6,'{}'::jsonb,'MARKETABLE','IOC',true,"
        " 100,0.40,0.40,100,'FILLED',to_timestamp($7),to_timestamp($7),"
        " to_timestamp($7 + 90),'PAPER_SIM_V1',$8,to_timestamp($7 + 2))",
        oid, acct["account_id"], acct["session_id"], gid, slug, "fx-" + slug,
        float(at), STRATEGY)
    fid = "paperfill:xb%s" % uuid.uuid4().hex[:12]
    await conn.execute(
        "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
        " account_id, session_id, group_id, role, direction, holding_side, "
        " us_market_slug, fixture, label, qty, price, wire_price, fee_usd, "
        " gross_usd, filled_at, basis, simulator_version, strategy) VALUES "
        " ($1,$1,$2,$3,$4,$5,'ENTRY','BUY','LONG',$6,$7,'{}'::jsonb,100,0.40,"
        " 0.40,0,40,to_timestamp($8),'DEPTH_WALK_WITHIN_LIMIT',"
        " 'PAPER_SIM_V1',$9)",
        fid, oid, acct["account_id"], acct["session_id"], gid, slug,
        "fx-" + slug, float(at) + 2, STRATEGY)


def _packet(complete: bool, protection_state: str) -> dict:
    present = {"residual": True, "probability": True, "book": True,
               "exit_depth": True, "settlement": True,
               "protection": protection_state == "PROTECTED_RESTING"}
    if not complete:
        present["probability"] = False
    missing = [code for k, code in FL.PACKET_ELEMENTS if not present[k]]
    return {"version": "XAVIER_MANAGEMENT_PACKET_V2",
            "residual": {"open_qty": 100, "ledger_open_qty": 100,
                         "present": present["residual"]},
            "probability": {"evidence_state": "FRESH_CURRENT_PROBABILITY",
                            "valuation_id": 7,
                            "present": present["probability"]},
            "book": {"mark_class": "FRESH", "present": present["book"]},
            "exit_depth": {"at_mark": 50, "present": present["exit_depth"]},
            "settlement": {"fingerprint": "fp", "present":
                           present["settlement"]},
            "protection": {"state": protection_state, "order_id": "po-1",
                           "present": present["protection"]},
            "gate": {"complete": not missing, "missing": missing,
                     "refusal": None if not missing else
                     "XAVIER_MANAGEMENT_PACKET_INCOMPLETE"},
            "pad": PAD * 20}


async def _seed(conn, *, n_pos: int, n_rev: int, now: float, tag: str):
    """n_pos open positions, each with n_rev reviews (oldest first). The
    NEWEST review of position i recommends EXIT (i % 3 == 0), REDUCE
    (i % 3 == 1) or HOLD, on a FRESH valuation (CURRENT) for even i and an
    expired one for odd i; every older review is superseded history."""
    acct = await H.new_account(conn, "xb" + tag, now=now - 86400 * 5)
    expected = {}
    rows = []
    for i in range(n_pos):
        gid = "paper_0000xb_%s_%04d" % (tag, i)
        slug = "xb-%s-%04d" % (tag, i)
        await _position(conn, acct, gid, slug, now - 86400 * 2 + i)
        for k in range(n_rev):
            newest = k == n_rev - 1
            rec = ("EXIT", "REDUCE", "HOLD")[i % 3] if newest else "HOLD"
            at = now - 60 * (n_rev - k) - 1
            fresh = newest and i % 2 == 0
            src = now - 5 if fresh else now - 4000
            rid = "paperrev:xb%s_%04d_%03d" % (tag, i, k)
            measure = {"p": 0.55, "stale": not fresh,
                       "evidence_state": "FRESH_CURRENT_PROBABILITY"
                       if fresh else "STALE_PROBABILITY",
                       "pad": PAD * 40}
            sel = {"selected": rec, "valuation": {
                "source": "PINNACLE_DEVIG_V1", "probability": 0.55,
                "source_at": src, "limit_s": 60.0, "valuation_id": 7},
                "management_packet": _packet(
                    complete=i % 4 != 3,
                    protection_state="PROTECTED_RESTING" if i % 5
                    else "UNPROTECTED_NO_STANDING_ORDER"),
                "mechanical_selection": {"pad": PAD * 40}}
            rows.append((rid, acct["session_id"], acct["account_id"], gid,
                         at, rec, json.dumps(measure),
                         json.dumps({"pad": PAD * 80}), json.dumps(sel)))
            if newest:
                expected[gid] = {"review_id": rid, "rec": rec,
                                 "fresh": fresh,
                                 "complete": i % 4 != 3,
                                 "protected": bool(i % 5)}
    rows.sort(key=lambda r: r[4])
    await conn.executemany(
        "INSERT INTO paper_xavier_reviews (review_id, session_id, "
        " account_id, group_id, reviewed_at, trigger, recommendation, "
        " measure, alternatives, selection, exposure, strategy) VALUES "
        " ($1,$2,$3,$4,to_timestamp($5),'SCHEDULED_BACKSTOP',$6,"
        " $7::jsonb,$8::jsonb,$9::jsonb,'{}'::jsonb,'%s')" % STRATEGY, rows)
    return expected


async def _detail(conn, now):
    c = Counting(conn)
    t = time.perf_counter()
    got = await FL.build_agent_detail(c, "xavier", now=now)
    return got, len(c.sql), time.perf_counter() - t


def _mine(got, tag):
    return {p["group_id"]: p for p in got["position_book"]["positions"]
            if p["group_id"].startswith("paper_0000xb_%s_" % tag)}


# ── §1 + §2 + §3: bounded queries, bounded size, semantics kept ────────

@pg
async def test_xavier_floor_query_count_does_not_grow_with_positions_or_history():
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        await _seed(conn, n_pos=20, n_rev=5, now=now, tag="a")
        _got, small, _ = await _detail(conn, now)
        await _seed(conn, n_pos=60, n_rev=20, now=now, tag="b")
        got, large, elapsed = await _detail(conn, now)
        assert large == small, (small, large)
        # one fixed set of section reads, never one per position
        assert large < 140, large
        assert len(_mine(got, "a")) == 20 and len(_mine(got, "b")) == 60
        assert elapsed < 10.0, elapsed
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_xavier_floor_payload_is_bounded_and_carries_no_review_blobs():
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        await _seed(conn, n_pos=40, n_rev=15, now=now, tag="s")
        got, _n, _t = await _detail(conn, now)
        body = json.dumps(got, default=str)
        assert PAD not in body          # no alternatives / selection / measure
        assert len(body) < FL.MAX_DETAIL_BYTES
        assert got["size_bytes"] <= FL.MAX_DETAIL_BYTES
        assert got["truncated"] == {}
        # per position: a few hundred bytes, whatever the review history
        per = len(json.dumps(list(_mine(got, "s").values()))) / 40
        assert per < 3000, per
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_each_position_carries_its_current_review_packet_and_protection():
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        expected = await _seed(conn, n_pos=24, n_rev=12, now=now, tag="c")
        got, _n, _t = await _detail(conn, now)
        mine = _mine(got, "c")
        assert set(mine) == set(expected)
        assert got["position_book"]["open"] >= 24
        for gid, exp in expected.items():
            p = mine[gid]
            cr = p["current_review"]
            # THE ONE CURRENT REVIEW: the newest, never superseded history
            assert cr["review_id"] == exp["review_id"], gid
            assert cr["superseded_by"] is None
            assert cr["recommendation_state"] != "SUPERSEDED"
            assert cr["recorded_recommendation"] == exp["rec"]
            pk = p["packet"]
            assert set(pk) >= {"version", "complete", "missing", "refusal",
                               "elements", "protection", "gate_basis"}
            assert set(pk["elements"]) == {k for k, _ in FL.PACKET_ELEMENTS}
            assert pk["version"] == "XAVIER_MANAGEMENT_PACKET_V2"
            assert pk["complete"] is (exp["complete"] and exp["protected"])
            if not pk["complete"]:
                assert pk["refusal"] == "XAVIER_MANAGEMENT_PACKET_INCOMPLETE"
                assert pk["missing"]
            # protection continuity, as recorded
            assert p["protection"]["present"] is exp["protected"]
            assert p["protection"]["state"] == (
                "PROTECTED_RESTING" if exp["protected"]
                else "UNPROTECTED_NO_STANDING_ORDER")
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_exit_and_reduce_recommendations_are_present_and_gated():
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        expected = await _seed(conn, n_pos=12, n_rev=6, now=now, tag="r")
        got, _n, _t = await _detail(conn, now)
        mine = _mine(got, "r")
        shown = {"EXIT": 0, "REDUCE": 0}
        for gid, exp in expected.items():
            cr = mine[gid]["current_review"]
            if exp["fresh"]:
                # CURRENT: the action word itself
                assert cr["recommendation_state"] == "CURRENT", cr
                assert cr["recommendation"] == exp["rec"]
                if exp["rec"] in shown:
                    shown[exp["rec"]] += 1
            else:
                # not current: shown only as recorded, never as the action
                assert cr["recommendation_state"] != "CURRENT"
                assert cr["recommendation"] != exp["rec"] or \
                    exp["rec"] in ("WAITING_FOR_FRESH_EVIDENCE",)
                assert cr["recorded_recommendation"] == exp["rec"]
        assert shown["EXIT"] >= 1 and shown["REDUCE"] >= 1, shown
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_position_book_is_cut_at_its_bound_and_says_so(monkeypatch):
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        await _seed(conn, n_pos=9, n_rev=2, now=now, tag="t")
        monkeypatch.setattr(FL, "MAX_DETAIL_POSITIONS", 4)
        got, _n, _t = await _detail(conn, now)
        pb = got["position_book"]
        assert pb["shown"] == 4 and len(pb["positions"]) == 4
        assert pb["open"] >= 9 and pb["positions_truncated"] is True
        assert pb["bound"] == 4
    finally:
        await tx.rollback()
        await conn.close()


def test_over_the_byte_bound_lists_are_cut_with_named_markers():
    out = {"timeline": [{"x": "a" * 500}] * 40,
           "outputs": [{"x": "b" * 100}] * 10, "queue": [],
           "position_book": {"positions": [{"x": "c" * 400}] * 50,
                             "positions_truncated": False, "shown": 50},
           "challenges": {"given": [], "received": [], "evaluated": []}}
    got = FL.bound_detail(out, max_bytes=20000)
    assert got["size_bytes"] <= 20000
    assert got["truncated"], got["truncated"]
    for path, cut in got["truncated"].items():
        assert cut["kept"] < cut["total"], path
    if "position_book.positions" in got["truncated"]:
        assert got["position_book"]["positions_truncated"] is True
        assert got["position_book"]["shown"] == len(
            got["position_book"]["positions"])
    small = FL.bound_detail({"timeline": [1, 2]}, max_bytes=20000)
    assert small["truncated"] == {} and small["timeline"] == [1, 2]


def test_packet_view_keeps_every_element_and_derives_an_unrecorded_gate():
    from sportsassets import xavier_packet as XPK
    assert [c for _k, c in FL.PACKET_ELEMENTS] == list(XPK.ELEMENTS)
    assert tuple(FL.PACKET_ELEMENTS) == tuple(XPK._KEYS)
    pk = _packet(complete=True, protection_state="PROTECTION_EXPIRED")
    pk.pop("gate")
    v = FL.packet_view(pk)
    assert v["gate_basis"] == "DERIVED_FROM_ELEMENTS"
    assert v["complete"] is False
    assert v["missing"] == XPK.gate(pk)["missing"] == [XPK.P_PROTECTION]
    assert v["protection"] == {"state": "PROTECTION_EXPIRED",
                               "order_id": "po-1", "present": False}
    assert FL.packet_view(None) is None and FL.packet_view({}) is None


# ── §4 the book read ───────────────────────────────────────────────────

def test_the_book_read_is_one_index_probe_per_slug():
    import inspect
    src = inspect.getsource(W._read_positions)
    assert "CROSS JOIN LATERAL" in src and "LIMIT 1) b" in src
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))
    assert "DISTINCT ON (us_market_slug)" not in code
    # the review read carries only the selection's valuation
    assert "jsonb_build_object('valuation'" in W.REVIEW_SELECTION_SQL


@pg
async def test_the_book_read_picks_the_newest_observation_per_slug():
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        acct = await H.new_account(conn, "xbk", now=now - 86400)
        slug = "xb-book-%s" % uuid.uuid4().hex[:8]
        await _position(conn, acct, "paper_0000xb_k_0001", slug,
                        now - 3600)
        for k in range(200):
            await H.observe(conn, slug, now - 2000 + k, offers=[(0.5, 10)])
        tie = [await H.observe(conn, slug, now - 10, offers=[(0.5, 10)])
               for _ in range(3)]
        s = W._Sections(conn)
        got = await W._read_positions(s, now)
        p = next(x for x in got["positions"]
                 if x["group_id"] == "paper_0000xb_k_0001")
        assert p["book"]["obs_id"] == max(tie)
        assert abs(p["book"]["observed_at"] - (now - 10)) < 1e-3
    finally:
        await tx.rollback()
        await conn.close()


# ── §5 edges, cache ────────────────────────────────────────────────────

def test_pre_aggregated_edges_keep_their_exact_counts():
    ev = [{"kind": "agent_conversation_messages", "id": str(i)}
          for i in range(7)]
    raw = [{"from": "DEREK", "to": "XAVIER", "kind": "HANDOFF", "at": 100.0,
            "first_at": 10.0, "count": 40, "evidence_list": ev,
            "summary": "newest"},
           {"from": "DEREK", "to": "XAVIER", "kind": "HANDOFF", "at": 50.0,
            "evidence": {"kind": "x", "id": "r"}, "summary": "raw"}]
    got = FL.merge_edges(raw)
    assert len(got) == 1
    e = got[0]
    assert e["count"] == 41 and e["at"] == 100.0 and e["first_at"] == 10.0
    assert e["summary"] == "newest" and len(e["evidence"]) == 5
    assert e["evidence"][0]["id"] == "0"


@pg
async def test_message_agents_are_the_check_constraint_agents():
    import asyncpg
    conn = await asyncpg.connect(H.DSN)
    try:
        d = await conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            " WHERE conname = 'agent_conv_agents_ck'")
        if d is None:
            pytest.skip("migration 224 not applied")
        frm = d.split("to_agent")[0]
        allowed = set(__import__("re").findall(r"'([A-Z_]+)'::text", frm))
        assert allowed == set(FL.MESSAGE_AGENTS), (allowed, d)
    finally:
        await conn.close()


async def test_the_detail_route_serves_repeats_from_its_short_cache(
        monkeypatch):
    calls = []

    async def fake_read_only(fn):
        calls.append(1)
        return {"agent": {"agent": "XAVIER"}, "n": len(calls)}

    class R:
        headers: dict = {}
    monkeypatch.setattr(FL, "_read_only", fake_read_only)
    monkeypatch.setattr(FL, "_DETAIL_CACHE", {})
    a = await FL.floor_agent("xavier", R())
    b = await FL.floor_agent("xavier", R())
    assert len(calls) == 1 and a["n"] == b["n"] == 1
    assert a["cache_age_s"] == 0.0 and b["cache_age_s"] >= 0.0
    assert b["cached_at"] == a["cached_at"]
    # past the window: read again
    t0 = time.time()
    monkeypatch.setattr(FL.time, "time",
                        lambda: t0 + FL.DETAIL_CACHE_S + 1)
    c = await FL.floor_agent("xavier", R())
    assert len(calls) == 2 and c["n"] == 2
    assert FL.DETAIL_CACHE_S <= 15.0


# ── §6 listed ──────────────────────────────────────────────────────────

def test_this_proof_is_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_command_floor_xavier_bounded.py" in listed.splitlines()
