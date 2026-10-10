"""PRODUCTION-SHAPED CONTRACT TESTS (program section 22): THE REAL WRITER
PERSISTS, THE REAL READER READS THE PERSISTED ROW.

WHY THIS FILE EXISTS. Source comments in this repository record bugs that
passed their tests because the tests handed a reader a shape production never
wrote:
  * bettor_funded_management (THE DEFECT THIS CLOSES): "The funded lifecycle
    tests did not see it because they seed `settlement_rule` as an
    attestation dict -- a shape the real entry lane does not write."
  * bettor_funded_management `defer_dispatch`: "Proving 'one ranking precedes
    the action' by calling `pass_once` directly with a supplied fixture did
    not test that, because production never calls it that way."
  * api/command_rn1x: "My own local check had hand-parsed these fields before
    running the report against them -- mimicking a shape the API does not
    emit -- which is why it looked verified" (asyncpg returns jsonb as TEXT).
  * live_executor's exit census: "the same failure mode as a probe reading a
    column production does not write."
  * bettor_funded_hedge_supply: "Exercised against a fixture of that shape,
    that is exactly what happened."
Every contract below therefore runs the production writer against Postgres
and hands the persisted row -- jsonb as asyncpg returns it, numerics as
Decimals, instants as timestamptz -- to the production reader. No reader
input is a hand-built dict.

THE CONTRACTS (each named in its test):
  K1 paper decision -> canonical intent -> both adapters -> parity ledger ->
     the live readiness gate (live_parity.readiness_report) and the intent
     route's sha verification
  K2 Xavier's review -> the canonical management intent -> both adapters,
     verified from its persisted row by the production verifier
     (canonical_intent.verify_management_intent) and the intent route
  K3 paper order / fill / settlement -> ledger positions -> the INVESTMENT
     profitability validation reader
  K4 the actual lane's order record (execution_intent / execmirror) ->
     Audrey's reconciliation and the management view readers, including an
     order recovered from the trade log
  K5 agent_work_requests (written by Xavier's review) -> the floor workspace
     queue and the identity route's work state
  K6 PinnAPI ingest -> the collector's valuation record -> the completed-game
     decision's probability authority (and nothing once the feed's
     authority is gone or the reading is past the 30-second rule)
  K6b a WS frame -> the real reactive collector cycle persists the PinnAPI
     valuation -> the real completed-game decision on THAT row: ENTER when
     fresh, refused past 30 s, refused once the feed's authority is revoked
  K7 the collector's entry-decision row -> the entry-evidence route, jsonb
     returned as objects (the command_rn1x defect above)
  (settlement writer -> settlement reader -> ledger is
   tests/test_chaos_provider_disconnect_and_delayed_settlement.py)

Synthetic data only; rolled back or removed; no venue, no credential.
"""
from __future__ import annotations

import copy
import json
import time
import types
from decimal import Decimal

import pytest

from sportsassets import canonical_intent as CI
from sportsassets import execmirror as M
from sportsassets import execmirror_view as V
from sportsassets import execution_intent as EI
from sportsassets import live_parity as LP
from sportsassets import xavier_freshness as XF
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import work_queue as WQ

from tests import paper_harness as H
from tests import test_chaos_the_venue_state_machine_recovers as CH
from tests import test_xavier_review_probability_freshness as XR

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XR.AT


async def _tx():
    conn = await H.connect()
    tx = conn.transaction()
    await tx.start()
    return conn, tx


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


# ═════════════ K4 · THE ACTUAL ORDER RECORD -> RECONCILIATION + VIEW ═════

@pg
async def test_k4_the_actual_order_record_reads_back_through_audrey_and_the_management_view(
        monkeypatch):
    e = await CH._env(monkeypatch)
    try:
        clean = await CH._intent(e, qty=3000)                  # accepted, fills 3
        e.venue.script = [{"fill": 3}]
        assert (await e.lane._run(e.conn, clean["intent_id"]))["state"] == \
            EI.A_SUBMITTED
        lost = await CH._intent(e, qty=2000)                   # timeout, traded 2
        e.venue.script = [{"timeout_after_send": True, "fill": 2}]
        assert (await e.lane._run(e.conn, lost["intent_id"]))["state"] == \
            EI.A_UNKNOWN
        await CH._age(e, (await CH._row(e, lost["intent_id"]))["mirror_id"],
                      CH.PAST_GRACE)
        await CH._runner_pass(e.mirror, e.conn)               # recovered
        # ── Audrey reads the persisted chain ──
        assert await e.mirror.audrey_reconcile(e.conn) >= 2
        for it, qty in ((clean, 3), (lost, 2)):
            row = await CH._row(e, it["intent_id"])
            rec = await e.conn.fetchrow(
                "SELECT * FROM smalllive_reconciliations WHERE group_id=$1",
                row["group_id"])
            assert rec is not None, it["intent_id"]
            disc = _j(rec["discrepancies"])
            # the recorded fills equal the venue's own cumulative quantity
            assert not [d for d in disc if d["code"] in (
                "VENUE_CUM_QTY_NOT_EQUAL_RECORDED_FILLS",
                "LIVE_FILLED_MORE_THAN_INTENDED",
                "ACTUAL_POSITION_WITHOUT_XAVIER_HANDOFF")], disc
            chain = _j(rec["chain"])
            link = next(x for x in chain["links"]
                        if x["execution_intent_id"] == it["intent_id"])
            assert link["venue_order_id"] == row["venue_order_id"]
            assert Decimal(link["live_fill_qty"]) == qty
            assert Decimal(link["venue_cum_qty"]) == qty
            assert link["relation"] == "SIBLING_OF_ONE_DECISION"
            assert chain["live_held"] == qty
        # ── the management view reads the same rows ──
        dec = await V.decisions(e.conn)
        assert dec["status"] == "OK"
        rows = {r["execution_intent_id"]: r for r in dec["rows"]}
        for it, qty in ((clean, 3), (lost, 2)):
            actual = rows[it["intent_id"]]["actual"]
            row = await CH._row(e, it["intent_id"])
            assert actual["venue_order_id"] == row["venue_order_id"], actual
            assert actual["filled_qty"] == pytest.approx(float(qty)), actual
            # the intent the order serves reads SUBMITTED once recovered,
            # never a submission still in flight
            assert actual["state"] == EI.A_SUBMITTED, actual
        # and the per-order management view lists both venue orders
        view = json.dumps(await V.view(e.conn), default=str)
        for it in (clean, lost):
            assert (await CH._row(e, it["intent_id"]))["venue_order_id"] in view
    finally:
        await CH._close(e)


# ═════════════ K5 · WORK REQUESTS -> FLOOR WORKSPACE + IDENTITY ══════════

@pg
async def test_k5_xaviers_work_requests_read_back_through_the_floor_and_identity_routes(
        monkeypatch):
    """Runs at F.ISOLATED, a time no other proof commits at. The floor's
    Xavier queue is the NEWEST 25 open requests (ORDER BY enqueued_at DESC
    LIMIT 25) of every account; at this file's fixed 2026-09 epoch a database
    that already holds 25 or more later open requests -- the second
    capital-critical run on one database does -- pushed this test's own
    request off the page and the assertion below that every request it wrote
    is listed failed. Dated after everything any proof commits, its requests
    are the newest and on the page whatever the neighbours left. Every
    assertion is unchanged; only the instant moved (the held position and the
    review are built relative to XR.AT, which this test sets)."""
    from sportsassets.api import agents_identity as AID
    from sportsassets.api import command_floor as FL
    from tests import agent_ops_fixture as AOF
    at = AOF.ISOLATED
    monkeypatch.setattr(XR, "AT", at)
    conn, tx = await _tx()
    try:
        a, g, slug = await XR._held(conn, "k5", entry_age_s=3600)
        t1 = at + 100                 # the review's probability is stale
        await PX.review_group(conn, XR._ctx(a, t1), g, trigger=PX.T_BACKSTOP)
        written = {r["request_id"]: dict(r) for r in await conn.fetch(
            "SELECT r.* FROM agent_work_open o JOIN agent_work_requests r "
            "  ON r.request_id = o.request_id WHERE o.group_id=$1", g)}
        assert written, "the stale review enqueued no reacquisition"
        assert {r["kind"] for r in written.values()} >= {WQ.K_PROBABILITY}
        # the floor's Xavier workspace queue: every open request, as written
        detail = await FL.build_agent_detail(conn, "xavier", now=t1 + 5)
        assert detail is not None
        queue = [q for q in (detail.get("queue") or [])
                 if q.get("kind") == "agent_work_requests"]
        listed = {q["id"]: q for q in queue}
        for rid, r in written.items():
            assert rid in listed, (rid, sorted(listed)[:5])
            q = listed[rid]
            assert q["title"] == "Acquire %s for %s" % (r["kind"], g)
            assert q["status"] == "OPEN · %s" % r["reason"]
            assert q["status"].endswith(XF.REC_WAITING)
            assert q["expires_at"] == pytest.approx(r["expires_at"].timestamp())
        # the identity route's work state for Xavier reads the same rows
        ws = await AID._work_state(conn, "XAVIER", t1 + 5)
        assert ws.get("state") not in (None, "IDLE_NO_OPEN_WORK"), ws
        counts = ws.get("counts") or {}
        assert (counts.get("open_requests_by_kind") or {}).get(
            WQ.K_PROBABILITY, 0) >= 1, counts
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_k5_the_floors_xavier_queue_is_the_newest_25_open_requests(
        monkeypatch):
    """THE LEAK K5 ABOVE IS ISOLATED FROM, MADE ON PURPOSE. The floor's
    Xavier queue is the newest 25 open requests by enqueued_at, of every
    account. Here this test's own stale-review request is written at one
    instant and 25 requests of other accounts are enqueued after it through
    the real queue writer: the floor lists those 25 and the older one is not
    on the page. That is what a database holding 25 later open requests does
    to a proof dated at an ordinary epoch -- the second capital-critical run
    on one database -- and why K5 runs at F.ISOLATED. (The page bound is the
    floor's, deliberate: a workspace is the newest 25, not a backlog.)

    This proof runs in its OWN isolated band, a day after K5's (F.ISOLATED
    + 86400): the 25 it enqueues must be the newest open requests in the
    database for the page to be exactly them, so on a database that already
    holds wall-clock-dated open requests the ordinary epoch would have put
    the neighbours' requests on the page instead of these -- the very leak
    it demonstrates."""
    from sportsassets.api import command_floor as FL
    from tests import agent_ops_fixture as AOF
    at = AOF.ISOLATED + 86400.0
    monkeypatch.setattr(XR, "AT", at)
    conn, tx = await _tx()
    try:
        a, g, slug = await XR._held(conn, "k5n", entry_age_s=3600)
        t1 = at + 100
        await PX.review_group(conn, XR._ctx(a, t1), g, trigger=PX.T_BACKSTOP)
        mine = {r["request_id"] for r in await conn.fetch(
            "SELECT r.request_id FROM agent_work_open o JOIN "
            " agent_work_requests r ON r.request_id = o.request_id "
            " WHERE o.group_id=$1", g)}
        assert mine, "the stale review enqueued no reacquisition"
        later = []
        for i in range(25):
            got = await WQ.enqueue(
                conn, kind=WQ.K_PROBABILITY, group_id="paper_g_k5n_%d" % i,
                reason="WAITING_FOR_FRESH_EVIDENCE", at=t1 + 1 + i,
                batch_id="k5n-%d" % i, slug="k5n-slug-%d" % i,
                detail={"account_id": "k5n-%d" % i})
            assert got["enqueued"], got
            later.append(got["request_id"])
        detail = await FL.build_agent_detail(conn, "xavier", now=t1 + 60)
        listed = [q["id"] for q in (detail.get("queue") or [])
                  if q.get("kind") == "agent_work_requests"]
        assert len(listed) == 25
        assert set(listed) == set(later)
        assert not (mine & set(listed))
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════ K2 · XAVIER'S REVIEW -> THE MANAGEMENT INTENT ═════════════

class _RoutePool:
    """The intent route's pool, answering with the rows the TEST connection
    reads from Postgres inside its rolled-back transaction (the route's own
    pool cannot see uncommitted rows). Every row handed to the route is the
    asyncpg Record exactly as persisted; only the connection is substituted."""

    def __init__(self, conn):
        self._c = conn

    def acquire(self):
        pool = self

        class _Acq:
            async def __aenter__(self):
                return _RouteConn(pool._c)

            async def __aexit__(self, *a):
                return False
        return _Acq()


class _RouteConn:
    def __init__(self, conn):
        self._c = conn

    def transaction(self, **kw):              # the route asks for READ ONLY
        class _Tx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False
        return _Tx()

    async def execute(self, sql, *a):
        if sql.lstrip().upper().startswith("SET LOCAL"):
            return "SET"
        return await self._c.execute(sql, *a)

    async def fetchrow(self, sql, *a):
        return await self._c.fetchrow(sql, *a)

    async def fetch(self, sql, *a):
        return await self._c.fetch(sql, *a)


async def _intent_route(conn, monkeypatch, intent_id: str) -> dict:
    """GET /api/command/live-parity/intent/{id} -- the production handler --
    over the persisted rows."""
    from sportsassets.api import command_live_parity as CLP

    async def pool():
        return _RoutePool(conn)
    monkeypatch.setattr(CLP, "_pool", pool)
    return await CLP.live_parity_intent(intent_id, _auth="contract-test")


@pg
@pytest.mark.parametrize("fresh", [False, True], ids=["stale", "fresh"])
async def test_k2_xaviers_review_writes_a_management_intent_that_verifies_from_its_row(
        fresh, monkeypatch):
    """R30A review: the verifier is now PRODUCTION code
    (canonical_intent.verify_management_intent, called by the intent route
    for every cmi_ id, which served sha_verified=None before), and the
    adapters' consumption is asserted exactly: both adapters, each with the
    persisted sha."""
    conn, tx = await _tx()
    LP.install()
    try:
        a, g, slug = await XR._held(conn, "k2%s" % int(fresh), entry_age_s=3600)
        t1 = AT + 100
        if fresh:
            await XR._reading(conn, slug, decided_at=t1 - 2, pin_age_s=3.0,
                              p=0.71)
        await PX.review_group(conn, XR._ctx(a, t1), g, trigger=PX.T_BACKSTOP)
        rv = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                 " group_id=$1 ORDER BY reviewed_at DESC "
                                 " LIMIT 1", g)
        rec = await conn.fetchrow(
            "SELECT * FROM canonical_management_intents WHERE review_id=$1",
            rv["review_id"])
        assert rec is not None, "the review recorded no management intent"
        mi = dict(rec)
        assert mi["intent_id"] == CI.management_intent_id(rv["review_id"])
        # the PRODUCTION verifier, on the persisted row
        assert CI.verify_management_intent(mi), (
            "the persisted management intent does not verify against its own "
            "content: a reader cannot prove it is the intent both adapters "
            "consumed")
        # tamper-evident on the row as on the decision intent
        assert not CI.verify_management_intent(
            dict(mi, action=CI.ACT_EXIT if mi["action"] != CI.ACT_EXIT
                 else CI.ACT_NONE))
        assert not CI.verify_management_intent(
            dict(mi, reason=json.dumps({"tampered": True})))
        # the PRODUCTION reader: the intent route, on the persisted row
        got = await _intent_route(conn, monkeypatch, mi["intent_id"])
        assert got["sha_verified"] is True, got["sha_verified"]
        assert got["intent"]["intent_id"] == mi["intent_id"]
        # the review names the intent it produced, and BOTH adapters
        # consumed exactly that sha -- not "at most" that sha
        assert _j(rv["action"]).get("canonical_intent_id") == mi["intent_id"]
        ex = await conn.fetch("SELECT * FROM canonical_intent_executions "
                              " WHERE intent_id=$1", mi["intent_id"])
        assert sorted(r["adapter"] for r in ex) == ["PAPER", "SMALL_LIVE"], \
            [dict(r) for r in ex]
        assert {r["intent_sha"] for r in ex} == {mi["content_sha"]}
        assert {r["intent_kind"] for r in ex} == {"MANAGEMENT"}
        assert sorted(x["adapter"] for x in got["executions"]) == \
            ["PAPER", "SMALL_LIVE"]
        assert mi["sleeve"] == "INVESTMENT"
        if not fresh:
            assert mi["evidence_state"] != XF.E_FRESH
            assert mi["action"] not in (CI.ACT_EXIT, CI.ACT_REDUCE)
    finally:
        LP.uninstall()
        await tx.rollback()
        await conn.close()


# ═════════════ K3 · PAPER ORDER / FILL / SETTLEMENT -> PROFITABILITY ═════

@pg
async def test_k3_the_ledger_written_by_the_paper_book_reads_back_as_investment_evidence():
    from sportsassets import bettor_paper_ledger as L
    from sportsassets.api import command_validation as CV
    from sportsassets.profitability import validation as PV
    conn, tx = await _tx()
    try:
        # the real paper book: order -> simulated fill -> handoff
        a, g, slug = await XR._held(conn, "k3", entry_age_s=3600)
        acct = a["account_id"]
        pos = next(p for p in await L.positions(conn, acct) if p["group_id"] == g)
        cost = float(pos["acquisition_cost_usd"])
        qty = float(pos["open_qty"])
        # the real ledger's settlement writer
        got = await L.settle(conn, account_id=acct, group_id=g, slug=slug,
                             holding_side="LONG",
                             settlement_event_key="venue-final:%s" % slug,
                             outcome="WON", evidence={"contract": "K3"},
                             evidence_source="external_valuations.outcome_basis",
                             at=AT + 7200, session_id=a["session_id"])
        assert got["ok"] and not got["duplicate"], got
        # the validation reader over the persisted book
        data, unavailable = await CV.gather(conn, acct, now=AT + 7300)
        assert data is not None, unavailable
        mine = [p for p in data["positions"] if p["group_id"] == g]
        assert len(mine) == 1
        p = mine[0]
        # the sleeve is the DURABLE classification the entry's insert wrote,
        # not a reader's guess
        assert p["sleeve"] == "INVESTMENT", p
        assert p["open_qty"] == pytest.approx(0.0)
        assert p["released_at"] is not None
        assert p["realized_pnl_usd"] == pytest.approx(qty * 1.0 - cost)
        out = PV.compute(data, now=AT + 7300, since=None, cutover=None,
                         since_source="TEST", sources=unavailable)
        inv = out["sleeves"]["INVESTMENT"]["windows"][PV.ALL_TIME]
        assert inv["positions"] == 1
        rn = inv["metrics"]["REALIZED_NET_USD"]
        assert rn["value"] == pytest.approx(qty - cost), rn
        assert rn["detail"]["resolved_positions"] == 1
        for other in ("TRAINING", "BENCHMARK", "UNCLASSIFIED"):
            assert out["sleeves"][other]["windows"][PV.ALL_TIME]["positions"] == 0
        # no cutover recorded in this book: no forward evidence, no claim
        assert out["profitability_verdict"]["verdict"] == PV.NOT_ESTABLISHED
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════ K1 · DECISION -> INTENT -> ADAPTERS -> LEDGER -> GATE ═════

SHA = "c" * 40


async def _nosleep(_):
    return None


@pytest.fixture
def cg_on(monkeypatch, new_strategies_off):
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_derek as PD
    from tests import paper_live_fixture as PL
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


@pg
async def test_k1_the_parity_ledger_the_paper_pass_writes_is_what_the_readiness_gate_reads(cg_on):
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_runtime as PR
    from tests import paper_live_fixture as PL
    from tests import test_live_parity as TLP
    conn, tx = await _tx()
    now = time.time() + 5.0
    try:
        # the production cutover, recorded by the real writer with its
        # server-side checks (inside this test's transaction)
        await conn.execute("UPDATE execmirror_control SET enabled = false")
        await TLP._cutover_world(conn, sha=SHA, workers=SHA)
        cut = await LP.record_cutover(conn, release_sha=SHA,
                                      recorded_by="release engineer",
                                      api_sha=SHA,
                                      hooks_here=list(LP.HOOK_NAMES))
        assert cut["recorded"] is True, cut
        await conn.execute(
            "INSERT INTO execmirror_snapshots (at, balances, positions, "
            " open_orders) VALUES (now(), $1::jsonb, '[]', 0)",
            json.dumps([{"currency": "USD", "buyingPower": 500}]))
        # the real paper pass: the completed-game policy ENTERS
        acct = await PL.new_account(conn, "k1", now=now)
        t = PL.Transport(now)
        v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                               compatibility="INCOMPATIBLE")
        t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
        t.t = now
        p = await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                                market_data=PL.client(t), config=acct["config"],
                                force=True, fee_fn=H.flat_fee(0.01),
                                sleep=_nosleep)
        assert p["ran"] and not p["errors"], p["errors"]
        d = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"],
            v["valuation_id"], PB.CG_STRATEGY)
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        it = dict(await conn.fetchrow(
            "SELECT * FROM canonical_decision_intents WHERE decision_id=$1",
            d["decision_id"]))
        # the intent route's reader verifies the persisted row
        assert LP.verify_intent(it)
        led = dict(await conn.fetchrow(
            "SELECT * FROM live_parity_ledger WHERE intent_id=$1",
            it["intent_id"]))
        assert led["sleeve"] == "INVESTMENT"
        # ── the readiness gate reads the ledger the adapters wrote ──
        rep = await LP.readiness_report(conn)
        cut_at = (await LP.production_cutover(conn))["cutover_at"]
        n_dec = await conn.fetchval(
            "SELECT count(*) FROM live_parity_ledger WHERE sleeve='INVESTMENT' "
            "   AND intent_kind='DECISION' AND created_at >= $1", cut_at)
        assert n_dec >= 1
        assert rep["candidate_count"] == n_dec, rep
        # the comparison's per-field verdicts survive the jsonb round trip
        # into the gate's exact-match rates
        side = rep["exact_side_match"]
        assert side["compared"] == n_dec and side["matched"] == n_dec, side
        for f in ("limit_price", "wire_price", "time_in_force", "order_type"):
            r = rep["exact_limit_policy_match"][f]
            assert r["compared"] == n_dec and r["matched"] == n_dec, (f, r)
        assert rep["logic_divergences"] == 0
        assert rep["recommendation"] == LP.NOT_READY       # sample, not proof
        assert any(b.startswith("DECISION_SAMPLE:") for b in rep["blockers"])
        assert rep["observation_1"] is not None
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════ K6 · PINNAPI INGEST -> VALUATION -> COMPLETED-GAME ════════

def _seed_feed(at: float):
    """A real PinnAPI FeedCache that has synced a prematch snapshot of one
    baseball moneyline and then seen it MOVE (only an observed change makes
    a price current), plus the discovery event the collector holds -- the
    message shapes of tests/test_pinnapi_primary_source, on the real
    package."""
    from sportsassets import pinnapi_feed as F
    teams = ("Atlanta Braves", "Los Angeles Dodgers")
    market = {"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline",
              "period": 0, "status": "open",
              "prices": [{"designation": "home", "price": -125},
                         {"designation": "away", "price": 110}]}
    start = at + 3600

    def iso(t):
        import datetime as _dt
        return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat()
    event = {"id": 123, "startTime": iso(start), "isLive": False,
             "participants": [{"name": teams[0], "alignment": "home"},
                              {"name": teams[1], "alignment": "away"}],
             "markets": [market]}
    c = F.FeedCache()
    e = c.new_connection([("prematch", 6)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 6,
             "ts": (at - 60) * 1000, "events": [event]}, epoch=e,
            received_ms=(at - 60) * 1000 + 5)
    moved = copy.deepcopy(market)
    moved["prices"][0]["price"] = -120
    c.apply({"type": "prematch_markets", "matchup_id": 123, "data": [moved],
             "sport_id": 6, "ts": (at - 2) * 1000}, epoch=e,
            received_ms=(at - 2) * 1000 + 5)
    books = [{"key": k, "markets": [{"key": "h2h", "last_update": iso(at - 5),
                                     "outcomes": [{"name": teams[0], "price": 1.5},
                                                  {"name": teams[1], "price": 2.5}]}]}
             for k in ("pinnacle", "betfair_ex_eu")]
    discovery = {"id": "discovery-123", "home_team": teams[0],
                 "away_team": teams[1], "commence_time": iso(start),
                 "bookmakers": books}
    return c, discovery


def _collector_record(at: float, *, purpose: str):
    """The collector's own steps for one PinnAPI-priced candidate, in its
    order (ext_pinnacle_loop): select the WS source, re-age independent
    depth, re-validate the reference, evaluate through the real gate, stamp
    the provider and its provenance onto the record. Returns (rec, cache)."""
    from sportsassets import bettor_external_shadow as EXT
    from sportsassets import bettor_valuation_purpose as VP
    from sportsassets import pinnapi_primary as PP
    from sportsassets import pinnapi_feed_runtime as RT
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    cache, event = _seed_feed(at)
    # the deciding process's owner and runtime (the caller restores them)
    RT._STATE.update(owner=types.SimpleNamespace(cache=cache),
                     runtime_id="k6-runtime")
    quote = LOOP.primary_pinnacle_h2h(event, received_at=at - 4,
                                      family="baseball", at=at)
    assert quote["reference_input"]["provider"] == PP.PROVIDER, quote
    quote["depth"], indep = PP.book_depth(event, quote["prices"],
                                          LOOP.SHARP_BOOKS, at=at,
                                          max_age_s=LOOP.PINNACLE_MAX_AGE_S)
    quote["reference_input"]["independent_books"] = indep
    check = LOOP.validate_primary_pinnacle(quote, at=at)
    assert check["ok"], check
    slug = "aec-mlb-atl-lad-k6-%d-%s" % (int(at), purpose[:3].lower())
    contract = {"venue": "PMUS", "condition_id": None, "us_market_slug": slug,
                "buy_intent": "ORDER_INTENT_BUY_LONG",
                "selection": quote["home"], "payout_event": quote["home"],
                "ladder_side": "ASK", "sport_family": "baseball",
                "market": "h2h", "period": "FULL_GAME", "line": None,
                "event_key": quote["event_id"]}
    cal = purpose == VP.CALIBRATION_ONLY
    rec = EXT.evaluate(
        contract=contract,
        quote={"book": "pinnacle", "outcomes": quote["prices"],
               "observed_at": quote["observed_at"],
               "received_at": quote["received_at"],
               "event_key": quote["event_id"], "period": "FULL_GAME",
               "line": None},
        market_state={"ask": 0.40, "api_price": 0.40, "readable": True,
                      "depth": 500, "side_consumed": "ASK"},
        execution_estimate={"p_fill": None, "basis": "P_FILL_NOT_IDENTIFIED",
                            "crossing": True},
        size=None, risk={"permitted": False,
                         "reason": "NO_EXECUTION_PLAN_WAS_BUILT"},
        fee_fn=lambda *a, **k: 0.0, now=at,
        outcome_books=quote["depth"].get(str(quote["home"])), armed=True,
        record_purpose=purpose,
        calibration_only_evidence=({
            "venue_read_refusal": "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
            "book_currency": "NOT_ESTABLISHED",
            "displayed_quote": {"ask": 0.40},
            "decision_instant_epoch_s": at, "decision_lag_s": 0.1}
            if cal else None))
    PP.stamp_record(rec, quote, check)
    return rec, cache


@pg
async def test_k6_a_pinnapi_valuation_persisted_by_the_collector_is_the_sole_authority_the_decision_reads():
    from sportsassets import bettor_external_shadow as EXT
    from sportsassets import bettor_valuation_purpose as VP
    from sportsassets import pinnapi_feed_runtime as RT
    from sportsassets import pinnapi_primary as PP
    from sportsassets.agents import derek_policy as DP
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_derek as PD
    at = time.time()
    prev = dict(RT._STATE)
    conn, tx = await _tx()
    try:
        rec, cache = _collector_record(at, purpose=VP.CALIBRATION_ONLY)
        vid = await EXT.persist(conn, rec)
        assert vid is not None
        # ── the completed-game decision's own read of the persisted row ──
        rows = await conn.fetch(PB.CANDIDATES_SQL, PB.EXPERIMENT_ID,
                                at - 3600, at + 3600, "k6-no-session", 50,
                                PB.CG_STRATEGY)
        row = next(dict(r) for r in rows if r["id"] == vid)
        assert row["provider"] == PP.PROVIDER
        cand = DP.candidate_from_row(row)
        assert (cand["pinnacle"]["reference_input"] or {}).get("provider") == \
            PP.PROVIDER, "the provenance did not survive the persisted row"
        pin = PD._pinnacle(cand, at=at + 1, max_age=30.0)
        assert pin["qualified"] is True, pin
        assert pin["provider"] == PP.PROVIDER
        auth = PB.pinnapi_sole_authority(cand, row)
        assert auth["applies"] is True, auth
        assert auth["outcome_books"] == row["outcome_books"] >= 1
        # ── past the 30-second rule the same row is not current ──
        late = PD._pinnacle(DP.candidate_from_row(row), at=at + 40,
                            max_age=30.0)
        assert late["qualified"] is False, late
        # ── the feed loses its authority: the same row decides nothing ──
        cache.authority.revoke()
        gone = PD._pinnacle(cand, at=at + 1, max_age=30.0)
        assert gone["qualified"] is False and gone["p"] is None, gone
    finally:
        RT._STATE.clear()
        RT._STATE.update(prev)
        await tx.rollback()
        await conn.close()


# ═════════════ K6b · PINNAPI INGEST -> COLLECTOR ROW -> THE CG DECISION ════
#
# R30A REVIEW (MINOR): K6 stopped at the probability authority, and every test
# here that ran the completed-game decision fed it a hand-INSERTed valuation
# (paper_live_fixture.valuation: provider the-odds-api, raw_odds '{}') of a
# shape the PinnAPI collector never writes. K6b runs the REAL writer -- a WS
# frame on the real FeedCache wakes the real reactive scheduler, which runs
# the real collector `cycle()` (identity, rules, fixture scope, venue book,
# reference re-validation, `bettor_external_shadow.persist`) -- and then the
# REAL completed-game decision on THAT persisted row, three ways: fresh ->
# ENTER; the same row past the 30-second rule -> refused, never entered; the
# feed's authority revoked -> refused, never entered. (The environment is
# tests/test_pinnapi_reactive_integration's, reused, not restated.)

from tests.test_pinnapi_reactive_integration import env  # noqa: E402,F401


async def _cg_pass_on(e, *, tag: str, at: float) -> dict:
    """The REAL paper pass, for a fresh scratch account (so the candidate
    query offers the persisted row again), at the decision instant `at`."""
    from sportsassets import bettor_paper_guard as G
    from sportsassets.agents import paper_runtime as PR
    from tests import paper_live_fixture as PL
    acct = await PL.new_account(e.conn, tag, now=at)
    out = await PR.paper_pass(e.conn, now=at, account_id=acct["account_id"],
                              market_data=G.PaperMarketDataClient(e.venue),
                              config=acct["config"], force=True,
                              fee_fn=H.flat_fee(0.01), sleep=_nosleep)
    assert out["ran"], out
    return acct


@pg
async def test_k6b_the_completed_game_decision_reads_the_row_the_pinnapi_collector_persisted(env):
    from sportsassets import pinnapi_feed as F
    from sportsassets import pinnapi_primary as PP
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    from tests import test_pinnapi_reactive_integration as RI
    e = env
    rows = await RI._one_reactive(e)
    assert [r["state"] for r in rows] == ["COMPLETED"], rows
    # (integration with inc-edge) each evaluation now ALSO records the other
    # side of the same contract (the complement, CALIBRATION_ONLY); the round
    # trip below is the priced side's, read as RI._priced reads it
    all_vids = rows[0]["detail"]["valuation_ids"]
    vids = await RI._priced(e, all_vids)
    assert len(vids) == 1 and len(all_vids) == 2, rows[0]["detail"]
    v = await RI._valuation(e, vids[0])
    # ── the writer's row: the collector's own PinnAPI record ──
    assert v["provider"] == PP.PROVIDER
    ref = H.j(v["settlement_comparison"])["reference_input"]
    assert ref["provider"] == PP.PROVIDER and ref["decision_check"]["ok"] is True
    # ── FRESH: the decision on THAT row enters, reading the row's own
    #    probability and provenance ──
    d = RI._cg(await RI._decisions(e, vids))
    assert len(d) == 1 and d[0]["verdict"] == "ENTER", \
        [(x["refusal"], x["refusals"]) for x in d]
    d = d[0]
    assert d["valuation_id"] == v["id"]
    pin = H.j(d["pinnacle"])
    assert pin["qualified"] is True and pin["provider"] == PP.PROVIDER
    assert pin["reference_input"]["feed_event_id"] == ref["feed_event_id"]
    assert pin["valuation_decided_at"] == pytest.approx(
        v["decided_at"].timestamp(), abs=1e-3)
    observed = float(pin["at"])
    # ── STALE: the SAME persisted row, decided past the 30-second rule ──
    late = observed + LOOP.PINNACLE_MAX_AGE_S + 1.0
    acct = await _cg_pass_on(e, tag="k6b-stale", at=late)
    ds = [x for x in await e.conn.fetch(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND valuation_id=$2"
        "   AND strategy=$3", acct["session_id"], v["id"], PB.CG_STRATEGY)]
    assert len(ds) == 1 and ds[0]["verdict"] == "REFUSE", [dict(x) for x in ds]
    lp = H.j(ds[0]["pinnacle"])
    assert lp["qualified"] is False and lp["p"] is None
    assert lp["refusal"] == F.R_STALE, lp
    # ── REVOKED: the feed loses its authority; a fresh instant ──
    e.cache.authority.revoke()
    acct = await _cg_pass_on(e, tag="k6b-revoked", at=observed + 2.0)
    ds = [x for x in await e.conn.fetch(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND valuation_id=$2"
        "   AND strategy=$3", acct["session_id"], v["id"], PB.CG_STRATEGY)]
    assert len(ds) == 1 and ds[0]["verdict"] == "REFUSE", [dict(x) for x in ds]
    rp = H.j(ds[0]["pinnacle"])
    assert rp["qualified"] is False and rp["p"] is None, rp
    assert rp["refusal"] == (e.cache.authority.reason or F.R_NO_AUTHORITY), rp
    # nothing but the fresh decision ever entered on this contract
    assert await RI._enters_on_contract(e) == 1


@pg
async def test_k7_the_entry_evidence_route_returns_the_collectors_jsonb_as_objects():
    """api/command_rn1x documents a report that 'looked verified' because the
    check hand-parsed jsonb a shape the API never emitted. The route now
    parses; here the collector's REAL entry-decision row is read back
    through it."""
    from sportsassets import bettor_external_shadow as EXT
    from sportsassets import bettor_valuation_purpose as VP
    from sportsassets import pinnapi_primary as PP
    from sportsassets import pinnapi_feed_runtime as RT
    from sportsassets.api import command_rn1x as RX
    at = time.time()
    prev = dict(RT._STATE)
    conn, tx = await _tx()
    try:
        rec, _cache = _collector_record(at, purpose=VP.ENTRY_DECISION)
        vid = await EXT.persist(conn, rec)
        got = await RX.entry_evidence(conn, hours=1, limit=200)
        row = next(c for c in got["candidates"] if c["id"] == vid)
        for k in ("execution_estimate", "risk_verdict",
                  "settlement_comparison"):
            assert not isinstance(row[k], str), (k, type(row[k]))
        assert row["settlement_comparison"]["reference_input"]["provider"] == \
            PP.PROVIDER
        assert isinstance(row["refusals"], list)
        json.dumps(got, default=str)
    finally:
        RT._STATE.clear()
        RT._STATE.update(prev)
        await tx.rollback()
        await conn.close()
