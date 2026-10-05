"""THE PER-AGENT FUNNEL RECEIPT AND THE REFUSAL ATTRIBUTION (P0 incident,
inc-edge router stream).

The owner asked whether hundreds of valid candidates upstream reach only a
few agents, and that no generic bucket hide a precise reason. Proved here:

  * `agent_funnel.receipt` (pure): per strategy RECEIVED / NOT_DECIDED /
    REJECTED_SOFTWARE / ELIGIBLE / REJECTED_ECONOMIC / REJECTED_UNCLASSIFIED /
    ENTER / ORDER / ENTER_WITHOUT_ORDER / FILL, every code classified by the
    one taxonomy; an unknown code is UNCLASSIFIED, never economic;
  * through the REAL writers (`ext.persist`, the real paper pass,
    `paper_benchmark.record_attempt`) on the recorded Greece v Germany
    production shape: an economic refusal, a software refusal (a
    mis-oriented probability), an ENTER with its PAPER order, and a timed-out
    attempt -- each lands in its bucket;
  * GET /api/command/agent-funnel: GET only, COMMAND session, one READ ONLY
    transaction under a statement timeout;
  * NO_PINNACLE_ON_EVENT's cause is kept: `pinnapi_primary.select` hands back
    its WS refusal reason even with no fallback, and the collector records the
    reason and the discovery payload's absence (the 2,534 rows/day that read
    as "Pinnacle has no price").
No real money, no venue order (the market-data client counts mutations).
"""
from __future__ import annotations

import ast
import copy
import pathlib
import time

import pytest

from sportsassets import agent_funnel as AF
from sportsassets import bettor_external_shadow as ext
from sportsassets import gross_edge_inputs as GEI
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_primary as PP
from sportsassets import refusal_taxonomy as RT
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.api import command_agent_funnel as API
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_the_other_side_of_the_contract_is_valued as OS

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
PKG = pathlib.Path(AF.__file__).resolve().parent
CG = PB.CG_STRATEGY


# ═════════════════════════════════════════════════════════════════════
# 1 · THE RECEIPT, PURE
# ═════════════════════════════════════════════════════════════════════

def test_the_receipt_buckets_every_decision_by_the_one_taxonomy():
    rows = [
        {"strategy": CG, "sport": "soccer", "complement": False,
         "verdict": "REFUSE", "refusals": [PB.R_EDGE], "n": 7},
        {"strategy": CG, "sport": "soccer", "complement": True,
         "verdict": "ENTER", "refusals": [], "n": 2},
        {"strategy": CG, "sport": "baseball", "complement": False,
         "verdict": "REFUSE", "refusals": [GEI.R_ORIENTATION, PB.R_EDGE],
         "n": 3},
        {"strategy": CG, "sport": "baseball", "complement": False,
         "verdict": "REFUSE", "refusals": ["A_CODE_NOBODY_CLASSIFIED"],
         "n": 1},
        {"strategy": "PINNACLE_EXPLORATION_PAPER", "sport": "soccer",
         "complement": False, "verdict": "REFUSE",
         "refusals": ["MARKET_NOT_IN_SUPPORTED_SET"], "n": 4}]
    r = AF.receipt(
        decisions=rows,
        received=[{"strategy": CG, "n": 15},
                  {"strategy": "PINNACLE_EXPLORATION_PAPER", "n": 4}],
        not_decided=[{"strategy": CG, "outcome": "TIMEOUT", "n": 2}],
        orders=[{"strategy": CG, "entered": 2, "orders": 2,
                 "enter_without_order": 0, "filled_orders": 1}],
        order_outcomes=[{"strategy": CG, "state": "EXPIRED",
                         "reason": PB.SIM.R_NO_READABLE_BOOK, "n": 1}])
    cg = next(a for a in r["agents"] if a["strategy"] == CG)
    rc = cg["receipt"]
    assert rc == {"RECEIVED": 15, "NOT_DECIDED": 2, "REJECTED_SOFTWARE": 5,
                  "ELIGIBLE": 9, "REJECTED_ECONOMIC": 7,
                  "REJECTED_UNCLASSIFIED": 1, "ENTER": 2, "ORDER": 2,
                  "ENTER_WITHOUT_ORDER": 0, "FILL": 1}
    assert cg["checks"]["decided"] == 13
    assert all(v for k, v in cg["checks"].items() if k != "decided")
    # the software decision binds on its SOFTWARE code, never on the edge
    binding = {b["code"]: b for b in cg["binding"]}
    assert binding[GEI.R_ORIENTATION]["n"] == 3
    assert binding[GEI.R_ORIENTATION]["class"] == RT.SOFTWARE
    assert binding["TIMEOUT"]["n"] == 2
    # the unknown code is named, UNCLASSIFIED, never economic
    codes = {c["code"]: c for c in cg["codes"]}
    assert codes["A_CODE_NOBODY_CLASSIFIED"]["class"] == RT.UNCLASSIFIED
    assert codes[PB.R_EDGE]["class"] == RT.ECONOMIC
    assert cg["complement_side"] == {"decided": 2, "enter": 2}
    assert cg["by_sport"]["soccer"]["ENTER"] == 2
    assert cg["unfilled_order_reasons"] == {
        "EXPIRED:%s" % PB.SIM.R_NO_READABLE_BOOK: 1}
    ex = next(a for a in r["agents"]
              if a["strategy"] == "PINNACLE_EXPLORATION_PAPER")
    assert ex["receipt"]["REJECTED_SOFTWARE"] == 4
    assert ex["receipt"]["ELIGIBLE"] == 0
    assert r["unclassified_is_never_economic"] is True
    assert r["orders_and_fills_are"] == "PAPER"


def test_the_window_is_bounded():
    s, u = AF.window(now=1000000.0)
    assert u == 1000000.0 and u - s == AF.DEFAULT_WINDOW_S
    s, u = AF.window(since=0.0, until=2000000.0, now=1000000.0)
    assert u == 2000000.0 and u - s == AF.MAX_WINDOW_S


def test_the_modules_import_no_paper_execution_or_funded_module_and_only_read():
    forbidden = ("paper", "funded", "execution", "pmus", "execmirror",
                 "live_parity", "venue")
    for rel in ("agent_funnel.py", "api/command_agent_funnel.py"):
        tree = ast.parse((PKG / rel).read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                assert not any(f in (m or "") for f in forbidden), (rel, m)
    for name in dir(AF):
        if name.endswith("_SQL"):
            sql = getattr(AF, name).strip().upper()
            assert sql.startswith("SELECT"), name
            for verb in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP "):
                assert verb not in sql, (name, verb)


# ═════════════════════════════════════════════════════════════════════
# 2 · NO_PINNACLE_ON_EVENT: THE CAUSE IS KEPT
# ═════════════════════════════════════════════════════════════════════

def test_select_hands_back_its_reason_even_with_no_fallback():
    why: dict = {}
    ev = {"id": "e", "home_team": "A FC", "away_team": "B FC",
          "commence_time": "2026-10-04T18:00:00Z", "bookmakers": []}
    assert PP.select(None, ev, None, family="soccer", sharp_books=(),
                     at=time.time(), runtime_id="r", explain=why) is None
    assert why["reason"] == F.R_NO_AUTHORITY
    # the collector records the WS reason, then the payload's absence
    assert loop.no_pinnacle_codes(why, ev) == [
        F.R_NO_AUTHORITY, loop.R_PAYLOAD_HAS_NO_PINNACLE]
    ev2 = dict(ev, bookmakers=[{"key": "pinnacle", "markets": [
        {"key": "spreads", "outcomes": []}]}])
    assert loop.pinnacle_absence_in_payload(ev2) == \
        loop.R_PINNACLE_HAS_NO_H2H
    # a sport the feed does not carry names itself
    class _Auth:
        granted = synced = True

    class _Cache:
        authority = _Auth()
        events = {}
    why2: dict = {}
    assert PP.select(_Cache(), ev, None, family="football", sharp_books=(),
                     at=time.time(), runtime_id="r", explain=why2) is None
    assert why2["reason"] == "PINNAPI_PRIMARY_SPORT_UNSUPPORTED"
    assert loop.no_pinnacle_codes(why2, ev)[0] == \
        "PINNAPI_PRIMARY_SPORT_UNSUPPORTED"
    # the generic code only when there is no reason at all
    assert loop.no_pinnacle_codes({}, ev2) == [loop.R_PINNACLE_HAS_NO_H2H]


def test_every_select_refusal_is_staged_classified_and_pinned_to_its_source():
    src = pathlib.Path(PP.__file__).read_text()
    tree = ast.parse(src)
    sel = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "select")
    reasons = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.startswith("PINNAPI_PRIMARY_"):
            reasons.add(node.value)
    # the reasons only validate() can raise are not select's
    validate_only = {"PINNAPI_PRIMARY_FIXTURE_CHANGED",
                     "PINNAPI_PRIMARY_INPUT_CHANGED", "PINNAPI_PRIMARY_H2H_V1"}
    assert sel is not None
    feed = {v for k, v in vars(F).items()
            if k.startswith("R_") and isinstance(v, str)
            and v.startswith("FEED_")}
    want = (reasons - validate_only) | feed
    assert want <= set(ext.PINNAPI_SELECT_REFUSALS), \
        sorted(want - set(ext.PINNAPI_SELECT_REFUSALS))
    for c in list(ext.PINNAPI_SELECT_REFUSALS) + [
            loop.R_PAYLOAD_HAS_NO_PINNACLE, loop.R_PINNACLE_HAS_NO_H2H]:
        assert ext.STAGE_OF[c] == "1_PROBABILITY", c
        assert ext.EVALUABILITY_OF[c] in (ext.EXTERNAL_DEPENDENCY,
                                          ext.COULD_NOT_EVALUATE), c
        assert RT.classify(c)["classified"], c


# ═════════════════════════════════════════════════════════════════════
# 3 · THROUGH THE REAL WRITERS
# ═════════════════════════════════════════════════════════════════════

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


ODDS = {"Draw": 3.40, "Greece": 4.54, "Germany": 1.95}
OFFERS = [OS._lv(0.25, 6000.0), OS._lv(0.26, 2400.0), OS._lv(0.27, 900.0)]
BIDS = [OS._lv(0.24, 5200.0), OS._lv(0.23, 3100.0), OS._lv(0.22, 800.0)]


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


@pg
async def test_the_receipt_over_real_decisions_orders_and_attempts(
        cg_on, monkeypatch):
    conn = await H.connect()
    slug = "atc-unl-gre-ger-2026-10-04-gre"
    slug2 = "atc-unl-gre-ger-2026-10-04-ger"
    try:
        now = time.time() + 5.0
        t0 = time.time() - 1.0
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "funnel", now=now)
        # the home side (economic refusal) and its complement (ENTER), as
        # the collector writes them
        rec, contract, evq, vq, extra = OS._home_record(
            slug, ODDS, OFFERS, BIDS, now=now - 2.0)
        comp, why = OS._complement(rec, contract, evq, vq, extra,
                                   now=now - 2.0)
        assert why is None
        # a second contract whose stored probability is NOT the held side's
        # (the double inversion) -- a software refusal
        rec2, *_ = OS._home_record(slug2, ODDS, OFFERS, BIDS, now=now - 2.0,
                                   selection="Greece")
        rec2 = copy.deepcopy(rec2)
        rec2["probability"] = 1.0 - rec2["probability"]
        ids = [await ext.persist(conn, copy.deepcopy(r))
               for r in (rec, comp, rec2)]
        assert all(ids)
        t = PL.Transport(now)
        for s in (slug, slug2):
            t.books[s] = {"offers": OFFERS, "bids": BIDS}
        client = PL.client(t)
        t.t = max(t.t, now)
        out = await PR.paper_pass(conn, now=now,
                                  account_id=acct["account_id"],
                                  market_data=client, config=acct["config"],
                                  force=True, fee_fn=None, sleep=_nosleep)
        assert out["ran"] and not out["errors"], out["errors"]
        assert client.mutation_attempts == 0
        # a timed-out attempt on a fourth valuation, by the real writer
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"]}
        await PB.record_attempt(conn, ctx, valuation_id=ids[2] + 100000,
                                strategy=CG, via="PAPER_PASS",
                                res={"timeout": True})

        monkeypatch.setattr(API, "_pool", lambda: _ret(_Pool(conn)))
        got = await API.agent_funnel(since=t0, until=time.time() + 60,
                                     account_id=acct["account_id"])
        assert got["status"] == "OK", got["why"]
        cg = next(a for a in got["data"]["agents"] if a["strategy"] == CG)
        rc = cg["receipt"]
        # OUR THREE VALUATIONS, each in its bucket (the pass also decides
        # whatever else is recent in the shared table; the receipt is checked
        # against an independent count below)
        mine = {r["valuation_id"]: r for r in await conn.fetch(
            "SELECT valuation_id, verdict, refusals FROM paper_decisions "
            " WHERE account_id=$1 AND strategy=$2 AND valuation_id = ANY($3)",
            acct["account_id"], CG, ids)}
        assert RT.decision_class(mine[ids[0]]["verdict"],
                                 mine[ids[0]]["refusals"]) == \
            RT.REJECTED_ECONOMIC                      # the home side: edge
        assert mine[ids[1]]["verdict"] == "ENTER"     # its complement
        assert list(mine[ids[2]]["refusals"]) == [GEI.R_ORIENTATION]
        assert RT.decision_class("REFUSE", mine[ids[2]]["refusals"]) == \
            RT.REJECTED_SOFTWARE                      # never a false ENTER
        # THE RECEIPT EQUALS AN INDEPENDENT COUNT over the same records
        allrows = await conn.fetch(
            "SELECT valuation_id, verdict, refusals FROM paper_decisions "
            " WHERE account_id=$1 AND strategy=$2 "
            "   AND decided_at >= to_timestamp($3)", acct["account_id"], CG,
            t0)
        want = {"ENTER": 0, "REJECTED_ECONOMIC": 0, "REJECTED_SOFTWARE": 0,
                "REJECTED_UNCLASSIFIED": 0}
        for r in allrows:
            want[RT.decision_class(r["verdict"], r["refusals"])] += 1
        received = await conn.fetchval(
            "SELECT count(DISTINCT v) FROM ("
            " SELECT valuation_id AS v FROM paper_decisions WHERE "
            "  account_id=$1 AND strategy=$2 AND decided_at >= to_timestamp($3)"
            " UNION SELECT valuation_id FROM paper_evaluation_attempts WHERE "
            "  account_id=$1 AND strategy=$2 AND at >= to_timestamp($3)) u",
            acct["account_id"], CG, t0)
        assert rc["RECEIVED"] == received >= 4
        assert rc["NOT_DECIDED"] >= 1
        assert cg["not_decided_by_outcome"].get("TIMEOUT", 0) >= 1
        assert rc["ENTER"] == want["ENTER"] >= 1
        assert rc["REJECTED_ECONOMIC"] == want["REJECTED_ECONOMIC"] >= 1
        assert rc["REJECTED_SOFTWARE"] == \
            want["REJECTED_SOFTWARE"] + rc["NOT_DECIDED"]
        assert rc["REJECTED_UNCLASSIFIED"] == want["REJECTED_UNCLASSIFIED"]
        assert rc["ELIGIBLE"] == want["ENTER"] + want["REJECTED_ECONOMIC"]
        orders = await conn.fetchval(
            "SELECT count(*) FROM paper_orders o JOIN paper_decisions d ON "
            " d.decision_id = o.decision_id WHERE d.account_id=$1 "
            "   AND d.verdict='ENTER' AND d.strategy=$2", acct["account_id"],
            CG)
        fills = await conn.fetchval(
            "SELECT count(DISTINCT f.order_id) FROM paper_fills f JOIN "
            " paper_orders o ON o.order_id=f.order_id JOIN paper_decisions d "
            " ON d.decision_id=o.decision_id WHERE d.account_id=$1 "
            "   AND d.strategy=$2", acct["account_id"], CG)
        assert rc["ORDER"] == orders >= 1
        assert rc["FILL"] == fills
        assert rc["ENTER_WITHOUT_ORDER"] == 0
        assert cg["complement_side"]["enter"] >= 1
        binding = {b["code"] for b in cg["binding"]}
        assert GEI.R_ORIENTATION in binding and "TIMEOUT" in binding
        vals = got["data"]["valuations_written"]
        sides = {v["side"] for v in vals if v["sport_family"] == "soccer"}
        assert sides == {"PRICED_OUTCOME", "COMPLEMENT"}
    finally:
        await PL.purge_everything(conn)
        await OS._clean(conn, slug)
        await OS._clean(conn, slug2)
        await conn.close()


async def _ret(v):
    return v


@pg
async def test_the_route_reads_in_a_read_only_transaction_with_a_timeout(
        monkeypatch):
    conn = await H.connect()
    seen = {}
    try:
        async def spy(c, **kw):
            seen["ro"] = await c.fetchval("SHOW transaction_read_only")
            seen["timeout"] = await c.fetchval("SHOW statement_timeout")
            seen.update(kw)
            return {"agents": []}
        monkeypatch.setattr(API, "_pool", lambda: _ret(_Pool(conn)))
        monkeypatch.setattr(API.AF, "read", spy)
        got = await API.agent_funnel(since=None, until=None, account_id=None)
        assert got["status"] == "OK"
        assert seen["ro"] == "on"
        assert seen["timeout"] == "%ds" % (API.STATEMENT_TIMEOUT_MS // 1000)
        assert seen["until"] - seen["since"] == AF.DEFAULT_WINDOW_S
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
        elif getattr(r, "path", "") == API.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(API.PATH).status_code == 401
    assert client.post(API.PATH).status_code in (401, 405)
