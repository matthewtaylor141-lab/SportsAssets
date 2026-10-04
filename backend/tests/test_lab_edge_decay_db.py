"""LAB-A EDGE DECAY ON PRODUCTION-SHAPED ROWS (Postgres).

Every row here is written by the REAL writers: the completed-game
INVESTMENT policy ENTERS on a real paper pass with the canonical hooks
installed (its decision, its book read, its canonical intent, its paper
order); later passes read the market's book through the real step and the
real simulator fills the order; the later Pinnacle valuation is inserted in
the collector's shape (tests/paper_live_fixture.valuation). Then:

  * the lab's DB reader + pure core reconstruct the opportunity: detection
    economics reconcile to the decision's own recorded EV, the horizons
    find the recorded books in their windows, the decay crosses its
    half-life, and the EV lost between detection and the simulator's
    execution book is measured in dollars at the decided quantity;
  * ANTI-LOOKAHEAD: a deliberately future-dated book row (observed inside a
    horizon window, recorded an hour later) is excluded by the SQL clock
    filter, trips the post-check when forced past it, and is never evidence
    at the horizon even when the reader's clock includes it;
  * the endpoint's builder runs in a READ ONLY transaction and serializes;
  * migration 242 is append-only, SHADOW_RESEARCH_ONLY, refuses
    FORWARD_VALIDATED, and its rollback refuses while a snapshot exists;
  * the fast-lane harness times the real components, its replica equals
    canonical_components.at_decision, and the concurrent run on one exported
    snapshot returns the SAME outputs as the sequential run.

SYNTHETIC: valuations and books from tests/paper_live_fixture.
"""
from __future__ import annotations

import json
import pathlib
import time

import asyncpg
import pytest

from sportsassets import live_parity as LP
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.api import command_lab_edge_decay as API
from sportsassets.lab import edge_decay as ED
from sportsassets.lab import edge_decay_reads as R
from sportsassets.lab import pit as PIT
from sportsassets.scripts import lab_fastlane_measure as FM
from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FEE = H.flat_fee(0.01)
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "242_lab_edge_decay.sql").read_text()
DOWN = (MIG / "rollback" / "242_lab_edge_decay.down.sql").read_text()


async def _nosleep(_):
    return None


async def _pass(conn, acct, transport, now, client):
    transport.t = max(transport.t, float(now))
    return await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                               market_data=client, config=acct["config"],
                               force=True, fee_fn=FEE, sleep=_nosleep)


@pytest.fixture
def cg_on_with_parity(monkeypatch, new_strategies_off):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    LP.install()
    yield
    LP.uninstall()
    PB._CONTEXT_CACHE.clear()


class _MovingBook(PL.Transport):
    """The real client's transport, whose book for a slug moves to the next
    set after each read: the decision reads the first, the pass's own
    post-delay read (paper_derek.step_after_delay, the simulator's execution
    book) the second."""

    def __init__(self, t0):
        super().__init__(t0)
        self.queue = {}

    def __call__(self, slug):
        got = super().__call__(slug)
        q = self.queue.get(slug)
        if q:
            self.books[slug] = q.pop(0)
        return got


async def _scenario(conn):
    """ENTER at `now` on offers .50 x1000 / .53 x1000 (p .62); by the
    simulator's post-delay read the book has moved to .53 x2000 (the order
    fills there: 1000 x .03 of EV lost); at +33 s it is .58 with a fresh
    p .60 recorded at +20 s."""
    now = time.time() + 5.0
    await PL.purge_everything(conn)
    await PL.purge_research_models(conn)
    ctl = await LP.control(conn)
    if ctl.get("halted"):
        await LP.clear_halt(conn, actor="test harness (human operator)",
                            reason="isolate this test")
    if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
        await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
    await conn.execute(
        "INSERT INTO execmirror_snapshots (at, balances, positions, "
        " open_orders) VALUES (now(), $1::jsonb, '[]', 0)",
        json.dumps([{"currency": "USD", "buyingPower": 500}]))
    acct = await PL.new_account(conn, "edgedecay", now=now)
    t = _MovingBook(now)
    v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                           compatibility="INCOMPATIBLE")
    slug = v["slug"]
    t.set(slug, offers=[(0.50, 1000), (0.53, 1000)], bids=[(0.48, 2000)])
    t.queue[slug] = [H.md(offers=[(0.53, 2000)], bids=[(0.48, 2000)])]
    client = PL.client(t)
    p1 = await _pass(conn, acct, t, now, client)
    assert p1["ran"] and not p1["errors"], p1["errors"]
    d = await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND valuation_id=$2"
        " AND strategy=$3", acct["session_id"], v["valuation_id"],
        PB.CG_STRATEGY)
    assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
    p2 = await _pass(conn, acct, t, now + 4.0, client)
    assert not p2["errors"], p2["errors"]
    await PL.valuation(conn, slug=slug, decided_at=now + 20.0, p_pin=0.60,
                       pin_age_s=1.0, compatibility="INCOMPATIBLE")
    t.set(slug, offers=[(0.58, 2000)], bids=[(0.48, 2000)])
    p3 = await _pass(conn, acct, t, now + 33.0, client)
    assert not p3["errors"], p3["errors"]
    return {"now": now, "acct": acct, "slug": slug, "decision": dict(d),
            "client": client}


@pg
async def test_edge_decay_on_rows_the_real_writers_wrote(cg_on_with_parity):
    conn = await H.connect()
    try:
        sc = await _scenario(conn)
        now, d = sc["now"], sc["decision"]
        data = await R.load_records(conn, now=now + 200.0, since=now - 60.0)
        rec = next(r for r in data["records"]
                   if r["d"]["decision_id"] == d["decision_id"])
        r = ED.evaluate(rec, fee_fn=FEE)
        assert r["qualification"]["class"] == ED.ENTER
        # detection = the decision's book receipt (read after the clock)
        b0 = await conn.fetchrow("SELECT observed_at FROM "
                                 "paper_book_observations WHERE obs_id=$1",
                                 d["book_obs_id"])
        assert r["t0"] == pytest.approx(max(d["decided_at"].timestamp(),
                                            b0["observed_at"].timestamp()),
                                        abs=1e-3)
        # the lab's detection economics ARE the decision's recorded EV
        assert r["initial"]["reconciliation_usd"] == pytest.approx(0.0,
                                                                   abs=1e-6)
        o0 = r["initial"]["decided_order"]
        assert o0["filled_qty"] == pytest.approx(float(d["proposed_qty"]))
        # the simulator's execution book (offers .53, its own post-delay
        # read) is the first recorded book after the order's eligible
        # instant: EV lost = 1000 x .03 at the decided quantity
        ex = r["execution"]
        assert ex["status"] == "MEASURED", ex
        fills = await conn.fetch(
            "SELECT f.book_obs_id FROM paper_fills f JOIN paper_orders o "
            " USING (order_id) WHERE o.decision_id = $1", d["decision_id"])
        assert {f["book_obs_id"] for f in fills} == {
            ex["execution_book_obs_id"]}
        assert ex["ev_lost_usd"] == pytest.approx(30.0, abs=1e-6)
        assert ex["realized_fill"]["filled_qty"] == pytest.approx(
            float(d["proposed_qty"]))
        assert ex["realized_fill"]["vwap"] == pytest.approx(0.53)
        # horizons: a book in its window is MEASURED, empty windows are
        # UNAVAILABLE and counted, nothing is carried
        hz = {h["h_s"]: h for h in r["horizons"]}
        meas = [h for h in r["horizons"] if h["status"] == "MEASURED"]
        assert hz[0]["status"] == "MEASURED" and len(meas) >= 3
        # +1 s: the simulator's post-delay read (offers .53): 72.7% retained
        assert hz[1]["book_obs_id"] == ex["execution_book_obs_id"]
        assert hz[1]["retention"] == pytest.approx(0.08 / 0.11)
        assert hz[2]["why"] == "NO_RECORDED_BOOK_IN_WINDOW"
        assert hz[10]["why"] == "NO_RECORDED_BOOK_IN_WINDOW"
        # the decay crosses its half-life once p .60 meets offers .58
        dec = r["decay"]["TOP_NET_EDGE"]
        assert dec["status"] == "MEASURED"
        assert dec["half_life"]["status"] == "CROSSED"
        assert 30.0 < dec["half_life"]["upper_s"] <= 40.0
        # forward: the canonical intent exists; this base stamps no stages
        lc = r["latency"]
        assert lc["source"] == "FORWARD_CANONICAL_INTENT"
        assert rec["intent"]["stage_column_present"] is False
        assert lc["unavailable"]["karen_complete"] == ED.NOT_STAMPED
        s = ED.summarize([r])
        assert s["ev_lost_during_processing"]["ev_lost_usd_total"] == \
            pytest.approx(30.0, abs=1e-6)
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_a_future_dated_row_is_never_evidence(cg_on_with_parity):
    """THE ANTI-LOOKAHEAD PROOF on the database: a book row observed inside
    the +5 s window but recorded an hour later (inserted directly: the real
    writer stamps recorded_at with the database clock, so a test must place
    the future stamp by hand)."""
    conn = await H.connect()
    try:
        sc = await _scenario(conn)
        now, d = sc["now"], sc["decision"]
        data = await R.load_records(conn, now=now + 200.0, since=now - 60.0)
        rec = next(x for x in data["records"]
                   if x["d"]["decision_id"] == d["decision_id"])
        t0 = ED.evaluate(rec, fee_fn=FEE)["t0"]
        fut = await conn.fetchval(
            "INSERT INTO paper_book_observations (us_market_slug, "
            " observed_at, source, bids, offers, read_basis, recorded_at) "
            "VALUES ($1, to_timestamp($2), 'TEST_FUTURE_DATED', '[]'::jsonb,"
            " $3::jsonb, 'TEST', to_timestamp($4)) RETURNING obs_id",
            sc["slug"], t0 + 3.0,
            json.dumps([{"px": {"value": "0.40"}, "qty": "5000"}]),
            t0 + 3600.0)
        # (1) the SQL clock filter excludes it
        got = await PIT.read(conn, "paper_book_observations",
                             clock=now + 200.0, columns=["obs_id"],
                             where="obs_id = $1", args=(fut,))
        assert got == []
        # (2) forced past the filter, the post-check refuses it
        raw = dict(await conn.fetchrow(
            "SELECT obs_id, observed_at, recorded_at FROM "
            "paper_book_observations WHERE obs_id = $1", fut))
        raw = {k: PIT.epoch(v) if k != "obs_id" else v
               for k, v in raw.items()}
        with pytest.raises(PIT.LookaheadViolation):
            PIT.post_check("paper_book_observations", [raw], now + 200.0)
        # (3) a reader whose clock DOES include it still never uses it at
        # the horizon whose instant precedes its recorded stamp
        late = await R.load_records(conn, now=t0 + 7200.0, since=now - 60.0)
        rec2 = next(x for x in late["records"]
                    if x["d"]["decision_id"] == d["decision_id"])
        assert any(b["obs_id"] == fut for b in rec2["books"])
        r2 = ED.evaluate(rec2, fee_fn=FEE)
        assert all(h.get("book_obs_id") != fut for h in r2["horizons"])
        assert all(s[0] != pytest.approx(3.0, abs=1e-3)
                   for s in r2["samples"]["venue_at_p0"])
    finally:
        async with conn.transaction():
            await conn.execute("SET LOCAL session_replication_role = replica")
            await conn.execute("DELETE FROM paper_book_observations "
                               " WHERE source = 'TEST_FUTURE_DATED'")
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_the_endpoint_builds_read_only_and_the_route_is_registered(
        cg_on_with_parity):
    from sportsassets.api import app as A
    from tests.test_agent_workspaces_show_runtime_records import _route_paths
    assert "/api/command/lab/edge-decay" in set(_route_paths(A.app.routes))
    conn = await H.connect()
    try:
        sc = await _scenario(conn)
        async with conn.transaction(readonly=True):
            out = await API.build(conn, now=sc["now"] + 200.0, days=1.0,
                                  limit=200, detail=True)
        json.dumps(out, default=str)
        assert out["authority"] == "SHADOW_RESEARCH_ONLY"
        assert out["evidence_status"] == "RETROSPECTIVE_ONLY"
        assert out["summary"]["qualified_by_class"]["ENTER"] >= 1
        pm = out["pm_answers"]
        assert "A_ev_lost_to_internal_latency" in pm
        assert "B_edge_half_life_by_league_strategy" in pm
        assert out["fast_lane"]["graph"]["status"] == "DERIVED"
        assert out["fast_lane"]["concurrency_estimate"]["status"] in (
            "UNAVAILABLE", "ESTIMATED")
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pg
async def test_migration_242_is_append_only_shadow_and_rolls_back_clean():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)                      # idempotent re-apply
        ins = ("INSERT INTO lab_edge_decay_snapshots (kind, version, "
               " evidence_status, source, payload, recorded_by, authority) "
               "VALUES ($1, 'V', $2, 'TEST', '{}'::jsonb, 'test', $3) "
               "RETURNING snapshot_id")
        sid = await conn.fetchval(ins, "COMPONENT_LATENCY", "TESTED",
                                  "SHADOW_RESEARCH_ONLY")

        async def refused(sql, *args):
            sp = conn.transaction()
            await sp.start()
            try:
                with pytest.raises(asyncpg.PostgresError):
                    await conn.execute(sql, *args)
            finally:
                await sp.rollback()
        await refused("UPDATE lab_edge_decay_snapshots SET payload='{}' "
                      " WHERE snapshot_id=$1", sid)
        await refused("DELETE FROM lab_edge_decay_snapshots "
                      " WHERE snapshot_id=$1", sid)
        await refused(ins, "COMPONENT_LATENCY", "FORWARD_VALIDATED",
                      "SHADOW_RESEARCH_ONLY")
        await refused(ins, "COMPONENT_LATENCY", "TESTED", "AUTHORITATIVE")
        await refused(ins, "A_DECISION", "TESTED", "SHADOW_RESEARCH_ONLY")
        await refused(DOWN)                         # a snapshot exists
        sp = conn.transaction()
        await sp.start()
        try:
            await conn.execute("SET LOCAL session_replication_role = replica")
            await conn.execute("DELETE FROM lab_edge_decay_snapshots")
            await conn.execute("SET LOCAL session_replication_role = origin")
            await conn.execute(DOWN)
            assert await conn.fetchval(
                "SELECT to_regclass('lab_edge_decay_snapshots')") is None
            await conn.execute(UP)                  # and it comes back
            assert await conn.fetchval(
                "SELECT to_regclass('lab_edge_decay_snapshots')") is not None
        finally:
            await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_fast_lane_harness_measures_the_real_components(
        cg_on_with_parity):
    conn = await H.connect()
    try:
        sc = await _scenario(conn)
        did = sc["decision"]["decision_id"]
        out = await FM.measure(H.DSN, decision_ids=[did], reps=2)
        assert out["runs"] == 2 and not out["refused"]
        # the replica IS canonical_components.at_decision's computation
        assert out["replica_equals_at_decision"] == {"equal": 2, "of": 2}
        # concurrency changes WHEN, never WHAT
        assert out["concurrent_equals_sequential"] == {"equal": 2, "of": 2}
        for k in FM.COMPONENTS:
            assert out["components"][k]["n"] == 2
            assert out["components"][k]["cold_p50_s"] is not None
        assert out["critical_path_estimate"]["status"] == "ESTIMATED"
        assert out["critical_path_estimate"]["concurrent_s"] <= \
            out["critical_path_estimate"]["sequential_s"]
        # recorded (SHADOW) and read back by the endpoint, then discarded
        tx = conn.transaction()
        await tx.start()
        try:
            sid = await FM.record(conn, out, recorded_by="test",
                                  source="TEST", code_sha=None)
            assert sid
            got = await API.build(conn, now=sc["now"] + 200.0, days=1.0,
                                  limit=50)
            ce = got["fast_lane"]["concurrency_estimate"]
            assert ce["status"] == "ESTIMATED"
        finally:
            await tx.rollback()
    finally:
        await PL.purge_everything(conn)
        await conn.close()
