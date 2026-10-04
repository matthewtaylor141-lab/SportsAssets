"""CAPITAL-CRITICAL (R30A, owner audit 2026-10-04): THE UNIQUE OPPORTUNITY
FUNNEL (profitability/opportunity_funnel.py, GET /api/command/opportunity-
funnel).

THE DEFECT. Blockers were ranked by decision ROWS: every paper policy
re-evaluates a live market on each valuation, so one market refused two
hundred times outranked twenty markets refused once each.

THE PROOFS.
  §1 THE KEY. One opportunity = (fixture, us_market_slug, holding_side, line,
     period); a row without a market or side is UNKEYED, counted apart.
  §2 THE RANKING. Re-evaluations never create opportunities: a blocker that
     refused one market 200 times ranks BELOW one that refused 20 markets
     once; the old row ranking is reported beside it; the binding blocker is
     the latest evaluation's; an opportunity entered later binds nothing.
  §3 EXECUTABLE EV AT THE STRATEGY'S OWN FRESHNESS. Measured on the
     decision's own book at its recorded age, else the latest observation at
     or before the decision within the strategy's executable bound; a stale
     probability, an older book, a book observed after the decision, or no
     fee schedule is UNAVAILABLE with its reason -- never a zero, never an
     older observation. Missed EV is each opportunity's BEST evaluation, not
     the sum of its re-evaluations; a near miss is a never-entered
     opportunity with positive executable EV.
  §4 SCOPE AND AUTHORITY. Every funnel names book, sleeve, strategy,
     policy_version(s), confidence scope (INVESTMENT production, the rest
     research); the route is GET only, 401 without a session, READ ONLY, and
     neither module writes.
  §5 THE REAL WRITER (Postgres). Decisions written by the REAL paper pass of
     the completed-game policy (paper_live_fixture valuations; the real
     SIM.record_book observation writer for the markets whose own book read
     was unreadable): BELOW_MIN_GROSS_EDGE on one market re-evaluated three
     times, FEES on two markets, a STALE probability re-evaluated four times,
     an unreadable-book refusal with a fresh probability and a readable book
     recorded 3 s earlier (a near miss), the same refusal on a market whose
     only readable book is older than the entry bound (UNAVAILABLE), and an
     ENTER.
ALL DATA HERE IS SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import ast
import pathlib
import re
import time

import pytest

from sportsassets.api import command_opportunity_funnel as API
from sportsassets.profitability import capacity as CP
from sportsassets.profitability import common as C
from sportsassets.profitability import opportunity_funnel as FN

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
NOW = 1_800_000_000.0
CG = "PINNACLE_COMPLETED_GAME_PAPER"
EXPLORE = "PINNACLE_EXPLORATION_PAPER"
FEE = 0.01                                     # per contract, flat (pure)


def _fee(px):
    return FEE


def _dec(i, *, slug, refusal="BELOW_MIN_GROSS_EDGE", at=None, p=0.60,
         pin_ok=True, pin_age=5.0, book_obs_id=None, book_age=None,
         strategy=CG, line=None, scope="FULL_GAME", fixture="fx",
         side="LONG", pv="CG_V3"):
    return {"decision_id": "paperdec:%s:%s" % (slug, i),
            "strategy": strategy, "policy_version": pv,
            "verdict": "ENTER" if refusal is None else "REFUSE",
            "refusal": refusal, "refusals": [refusal] if refusal else [],
            "us_market_slug": slug, "holding_side": side,
            "fixture": fixture, "line": line, "scope": scope,
            "p_blended": None, "p_pinnacle": p, "p_internal": None,
            "pinnacle": {"qualified": pin_ok, "age_s": pin_age,
                         "limit_s": 30.0,
                         "refusal": None if pin_ok else
                         "PROBABILITY_EVIDENCE_STALE"},
            "book_obs_id": book_obs_id, "decision_book_age_s": book_age,
            "decided_at": NOW + i if at is None else at}


def _book(obs_id, at, *, offer=0.50, qty=100):
    md = H.md(offers=[(offer, qty)], bids=[(round(offer - 0.02, 2), qty)])
    return {"obs_id": obs_id, "observed_at": at, "bids": md["bids"],
            "offers": md["offers"]}


# ── §1 the key ───────────────────────────────────────────────────────

def test_the_key_is_event_market_side_line_scope():
    d = _dec(0, slug="m1", line="-1.5", scope="FIRST_HALF")
    assert FN.opportunity_key(d) == ("fx", "m1", "LONG", "-1.5",
                                     "FIRST_HALF")
    assert FN.key_fields(FN.opportunity_key(d)) == {
        "fixture": "fx", "us_market_slug": "m1", "holding_side": "LONG",
        "line": "-1.5", "scope": "FIRST_HALF"}
    # a different line, period or side of one market is another opportunity
    keys = {FN.opportunity_key(_dec(0, slug="m1", line=ln, scope=sc,
                                    side=sd))
            for ln in ("-1.5", "+1.5") for sc in ("FULL_GAME", "FIRST_HALF")
            for sd in ("LONG", "SHORT")}
    assert len(keys) == 8
    assert FN.opportunity_key(_dec(0, slug=None)) is None
    assert FN.opportunity_key(_dec(0, slug="m", side=None)) is None
    out = FN.compute([_dec(0, slug=None), _dec(1, slug="m")],
                     sleeve="INVESTMENT", strategy=None)
    assert out["totals"]["unique_opportunities"] == 1
    assert out["totals"]["unkeyed_evaluations"] == 1
    assert out["totals"]["unkeyed_why"] == FN.R_UNKEYED


# ── §2 the ranking ───────────────────────────────────────────────────

def test_re_evaluations_never_create_opportunities():
    rows = [_dec(i, slug="hot", refusal="PROBABILITY_EVIDENCE_STALE")
            for i in range(200)]
    rows += [_dec(1000 + i, slug="m%d" % i, refusal="BELOW_MIN_GROSS_EDGE")
             for i in range(20)]
    out = FN.compute(rows, sleeve="INVESTMENT", strategy=None)
    t = out["totals"]
    assert t["unique_opportunities"] == 21 and t["evaluations"] == 220
    assert t["re_evaluations"] == 199
    first, second = out["blockers"]
    assert first["blocker"] == "BELOW_MIN_GROSS_EDGE"
    assert first["unique_opportunities"] == 20 and first["re_evaluations"] == 0
    assert second["blocker"] == "PROBABILITY_EVIDENCE_STALE"
    assert second["unique_opportunities"] == 1
    assert second["evaluations"] == 200 and second["re_evaluations"] == 199
    assert second["evaluations_per_opportunity"] == 200.0
    # the old row ranking, reported for comparison, had them the other way
    assert out["row_ranking_for_comparison"] == [
        "PROBABILITY_EVIDENCE_STALE", "BELOW_MIN_GROSS_EDGE"]
    top = out["most_reevaluated"][0]
    assert top["us_market_slug"] == "hot" and top["re_evaluations"] == 199


def test_the_binding_blocker_is_the_latest_evaluation():
    rows = [_dec(0, slug="a", refusal="PROBABILITY_EVIDENCE_STALE"),
            _dec(1, slug="a", refusal="BELOW_MIN_GROSS_EDGE"),
            # refused twice, then entered: binds nothing
            _dec(2, slug="b", refusal="BELOW_MIN_GROSS_EDGE"),
            _dec(3, slug="b", refusal="BELOW_MIN_GROSS_EDGE"),
            _dec(4, slug="b", refusal=None)]
    out = FN.compute(rows, sleeve="INVESTMENT", strategy=None)
    by = {b["blocker"]: b for b in out["blockers"]}
    assert by["BELOW_MIN_GROSS_EDGE"]["unique_opportunities"] == 1   # a
    assert by["BELOW_MIN_GROSS_EDGE"]["unique_opportunities_ever"] == 2
    assert by["BELOW_MIN_GROSS_EDGE"]["evaluations"] == 3
    assert by["PROBABILITY_EVIDENCE_STALE"]["unique_opportunities"] == 0
    assert by["PROBABILITY_EVIDENCE_STALE"]["missed_executable_ev_why"] \
        .startswith("NOT_BINDING")
    assert out["totals"]["entered_unique"] == 1
    assert out["totals"]["refused_unique"] == 1
    st = {s["stage"]: s["unique_opportunities"] for s in out["stages"]}
    assert st["EVALUATED"] == 2 and st["ENTERED"] == 1
    assert [s["stage"] for s in out["stages"]] == list(FN.STAGES)


# ── §3 executable EV at the strategy's own freshness ─────────────────

def test_executable_ev_on_the_decisions_own_fresh_book():
    d = _dec(0, slug="m", p=0.60, book_obs_id=7, book_age=2.0)
    ev = FN.evaluate(d, book=_book(7, NOW - 2.0), fee_fn=_fee)
    assert ev["status"] == C.MEASURED, ev
    # 100 contracts x (0.60 - 0.50 - 0.01)
    assert ev["executable_ev_usd"] == pytest.approx(9.0)
    assert ev["positive"] is True
    assert ev["book_freshness"]["source"] == CP.OWN_BOOK
    assert ev["book_freshness"]["bound_s"] == CP.executable_bound(CG)
    # a book whose every level costs more than p is a measured zero
    ev0 = FN.evaluate(dict(d, p_pinnacle=0.505), book=_book(7, NOW - 2.0),
                      fee_fn=_fee)
    assert ev0["status"] == C.MEASURED and ev0["executable_ev_usd"] == 0.0
    assert ev0["positive"] is False


def test_never_an_older_book_and_never_a_stale_probability():
    good = _book(7, NOW - 2.0)
    # the probability was stale at the decision: never priced
    ev = FN.evaluate(_dec(0, slug="m", pin_ok=False, pin_age=45.0,
                          book_obs_id=7, book_age=2.0), book=good,
                     fee_fn=_fee)
    assert ev["status"] == C.UNAVAILABLE and ev["executable_ev_usd"] is None
    assert ev["why"].startswith(FN.R_PROBABILITY_NOT_FRESH)
    ev = FN.evaluate(_dec(0, slug="m", pin_ok=True, pin_age=31.0,
                          book_obs_id=7, book_age=2.0), book=good,
                     fee_fn=_fee)
    assert ev["why"].startswith(FN.R_PROBABILITY_NOT_FRESH)
    ev = FN.evaluate(dict(_dec(0, slug="m"), p_pinnacle=None), book=good,
                     fee_fn=_fee)
    assert ev["why"] == FN.R_NO_PROBABILITY
    # the decision's own book, recorded older than the entry bound
    bound = CP.executable_bound(CG)
    ev = FN.evaluate(_dec(0, slug="m", book_obs_id=7, book_age=bound + 0.5),
                     book=_book(7, NOW - bound - 0.5), fee_fn=_fee)
    assert ev["status"] == C.UNAVAILABLE
    assert ev["why"] == CP.R_NOT_EXECUTABLE_FRESH
    # no own book: the latest observation is older than the bound (the
    # research capacity model's 300 s would have priced it)
    old = _book(9, NOW - 60.0)
    assert 60.0 < CP.MAX_BOOK_AGE_S
    ev = FN.evaluate(_dec(0, slug="m"), book=old, fee_fn=_fee)
    assert ev["status"] == C.UNAVAILABLE
    assert ev["why"] == CP.R_NOT_EXECUTABLE_FRESH
    # an observation AFTER the decision is not its book
    ev = FN.evaluate(_dec(0, slug="m"), book=_book(9, NOW + 1.0),
                     fee_fn=_fee)
    assert ev["why"] == CP.R_BOOK_AFTER_DECISION
    # nothing read at all
    ev = FN.evaluate(_dec(0, slug="m"), book=None, fee_fn=_fee)
    assert ev["why"] == CP.R_NO_BOOK
    # no fee schedule for the decision date
    ev = FN.evaluate(_dec(0, slug="m", book_obs_id=7, book_age=2.0),
                     book=good, fee_fn=None)
    assert ev["status"] == C.UNAVAILABLE and ev["why"] == CP.R_NO_FEE
    # within the bound, the latest observation stands
    ev = FN.evaluate(_dec(0, slug="m"), book=_book(9, NOW - 3.0),
                     fee_fn=_fee)
    assert ev["status"] == C.MEASURED
    assert ev["book_freshness"]["source"] == CP.LATEST_BOOK


def test_missed_ev_is_the_best_evaluation_not_the_sum_of_re_evaluations():
    rows = []
    for i, (p, at_obs) in enumerate(((0.60, 2.0), (0.58, 2.0), (0.62, 2.0),
                                     (0.70, 60.0))):
        d = _dec(i, slug="m", refusal="SETTLEMENT_NOT_SUPPORTED", p=p)
        d["ev"] = FN.evaluate(d, book=_book(i, d["decided_at"] - at_obs),
                              fee_fn=_fee)
        rows.append(d)
    out = FN.compute(rows, sleeve="INVESTMENT", strategy=None)
    b = out["blockers"][0]
    # the best FRESH evaluation (0.62 -> 11.0); the 0.70 one used a book
    # older than the bound and is not priced; never 9 + 7 + 11
    assert b["missed_executable_ev_usd"] == pytest.approx(11.0)
    assert b["near_misses"] == 1
    assert out["totals"]["near_misses"] == 1
    assert out["totals"]["missed_executable_ev_usd"] == pytest.approx(11.0)
    top = out["most_reevaluated"][0]
    assert top["ev_evaluations_measured"] == 3
    assert top["ev_unavailable_why"] == {CP.R_NOT_EXECUTABLE_FRESH: 1}
    st = {s["stage"]: s["unique_opportunities"] for s in out["stages"]}
    assert st["POSITIVE_EXECUTABLE_EV"] == 1 and st["ENTERED"] == 0
    # an entered opportunity is not a near miss, whatever it offered
    rows.append(_dec(9, slug="m", refusal=None))
    out = FN.compute(rows, sleeve="INVESTMENT", strategy=None)
    assert out["totals"]["near_misses"] == 0
    assert out["totals"]["missed_executable_ev_usd"] is None


def test_an_unpriced_blocker_is_unavailable_with_its_reasons():
    rows = []
    for i in range(3):
        d = _dec(i, slug="s", refusal="PROBABILITY_EVIDENCE_STALE",
                 pin_ok=False, pin_age=50.0)
        d["ev"] = FN.evaluate(d, book=None, fee_fn=_fee)
        rows.append(d)
    out = FN.compute(rows, sleeve="INVESTMENT", strategy=None)
    b = out["blockers"][0]
    assert b["missed_executable_ev_usd"] is None
    assert b["missed_executable_ev_why"].startswith("UNAVAILABLE")
    assert b["missed_ev_unavailable_why"] == {FN.R_PROBABILITY_NOT_FRESH: 3}
    assert out["totals"]["missed_executable_ev_usd"] is None
    st = {s["stage"]: s["unique_opportunities"] for s in out["stages"]}
    assert st["PROBABILITY_FRESH"] == 0


def test_the_ev_bound_prices_the_latest_fresh_evaluations():
    rows = [_dec(i, slug="m") for i in range(12)]
    rows += [_dec(100 + i, slug="s", pin_ok=False) for i in range(3)]
    got = API.choose_for_ev(rows, per_key=5, cap=100)
    assert got == {"paperdec:m:%d" % i for i in range(7, 12)}
    assert len(API.choose_for_ev(rows, per_key=5, cap=3)) == 3


# ── §4 scope and authority ───────────────────────────────────────────

def test_every_funnel_names_its_scope():
    out = FN.compute([_dec(0, slug="m")], sleeve="INVESTMENT",
                     strategy=None)
    assert (out["book"], out["sleeve"], out["strategy"]) == (
        "PAPER", "INVESTMENT", "ALL")
    assert out["policy_versions"] == ["CG_V3"]
    assert out["policy_version"] == "CG_V3"
    assert out["confidence_scope"] == C.PRODUCTION
    tr = FN.compute([_dec(0, slug="m", strategy=EXPLORE, pv="EX_V1")],
                    sleeve="TRAINING", strategy=EXPLORE)
    assert tr["confidence_scope"] == C.RESEARCH_SCOPE
    assert tr["strategy"] == EXPLORE
    assert FN.compute([], sleeve="INVESTMENT", strategy=None)[
        "policy_version_why"] == "NO_DECISION_IN_SCOPE"
    assert out["label"] == "RESEARCH"
    assert out["authority"] == "SHADOW_NO_AUTHORITY"


WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+"
                   r"FROM|TRUNCATE\s+[a-z_]|ALTER\s+TABLE|DROP\s+TABLE|"
                   r"CREATE\s+TABLE)", re.I)
FORBIDDEN = ("execmirror", "kalshi", "pmus", "clob", "executor", "execution",
             "funded", "order", "submit", "venue", "live_", "paper")


def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


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


def test_the_modules_hold_no_write_and_no_authority_import():
    for rel in ("api/command_opportunity_funnel.py",
                "profitability/opportunity_funnel.py"):
        tree = ast.parse((PKG / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not WRITE.search(node.value), (rel, node.value[:60])
        for imp in _imports(PKG / rel):
            leaf = imp.rsplit(".", 1)[-1]
            assert not any(f in leaf for f in FORBIDDEN), (rel, imp)
    assert _imports(PKG / "profitability" / "opportunity_funnel.py") <= {
        "__future__", "annotations", "intel", "attribution", "capacity",
        "common", ""}
    src = (PKG / "api" / "command_opportunity_funnel.py").read_text()
    assert "readonly=not nested" in src
    assert "SET LOCAL statement_timeout" in src


# ── §5 the real writer ───────────────────────────────────────────────

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
    yield
    PB._CONTEXT_CACHE.clear()


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
async def test_the_funnel_over_decisions_the_real_writer_recorded(
        cg_on, monkeypatch):
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_runtime as PR

    from tests import paper_live_fixture as PL
    conn = await H.connect()
    try:
        await PL.purge_everything(conn)
        now = time.time() + 5.0
        acct = await PL.new_account(conn, "r30afun", now=now)
        t = PL.Transport(now)
        slugs: dict = {}
        seen: dict = {}

        async def val(name, p, *, offer=None, pin_age=5.0):
            # each re-evaluation is a NEW Pinnacle observation of the market
            # (one valuation per observation instant), 0.5 s apart
            k = seen[name] = seen.get(name, -1) + 1
            # (the recorded venue wording, as the completed-game proofs use)
            v = await PL.valuation(conn, slug=slugs.get(name),
                                   decided_at=now - 10 - 0.5 * k, p_pin=p,
                                   pin_age_s=pin_age,
                                   compatibility="INCOMPATIBLE")
            slugs[name] = v["slug"]
            if offer is not None:
                t.set(v["slug"], offers=[(offer, 2000)],
                      bids=[(round(offer - 0.02, 4), 2000)])
            return v
        # BELOW_MIN_GROSS_EDGE on ONE market, evaluated three times
        for _ in range(3):
            await val("below", 0.5049, offer=0.50)
        # the fees consume the edge on TWO markets, once each
        await val("fees1", 0.515, offer=0.50)
        await val("fees2", 0.515, offer=0.50)
        # a STALE probability on one market, evaluated four times
        for _ in range(4):
            await val("stale", 0.62, offer=0.50, pin_age=45.0)
        # the policy's own book read is UNREADABLE (no venue book for the
        # slug) -> refused; the market's book WAS recorded (by the real
        # observation writer) 3 s before the decision, inside the entry
        # bound: priced on that book, a near miss
        for _ in range(2):
            await val("near", 0.62)
        # the same refusal on a market whose only readable book is 60 s old
        await val("old", 0.62)
        # and one that enters
        await val("enter", 0.62, offer=0.50)
        # (the pass decides at its own clock, `now`)
        await H.observe(conn, slugs["near"], now - 3.0,
                        offers=[(0.50, 500)], bids=[(0.48, 500)])
        await H.observe(conn, slugs["old"], now - 60.0,
                        offers=[(0.50, 500)], bids=[(0.48, 500)])
        client = PL.client(t)
        t.t = max(t.t, now)
        out = await PR.paper_pass(conn, now=now,
                                  account_id=acct["account_id"],
                                  market_data=client, config=acct["config"],
                                  force=True, fee_fn=None, sleep=_nosleep)
        assert out["ran"] and not out["errors"], out["errors"]
        assert client.mutation_attempts == 0
        recorded = {r["us_market_slug"]: r for r in await conn.fetch(
            "SELECT us_market_slug, count(*) AS n, "
            "       array_agg(DISTINCT coalesce(refusal, 'ENTER')) AS why "
            "  FROM paper_decisions WHERE session_id = $1 AND strategy = $2"
            " GROUP BY us_market_slug", acct["session_id"], CG)}
        want = {"below": 3, "fees1": 1, "fees2": 1, "stale": 4, "near": 2,
                "old": 1, "enter": 1}
        assert {k: recorded[s]["n"] for k, s in slugs.items()} == want, \
            {k: dict(recorded[s]) for k, s in slugs.items()}

        monkeypatch.setattr(C, "PAPER_ACCOUNT", acct["account_id"])
        dec_at = [r["t"] for r in await conn.fetch(
            "SELECT extract(epoch FROM decided_at)::float8 AS t "
            "  FROM paper_decisions WHERE session_id = $1", acct["session_id"])]
        assert max(dec_at) <= now + 1.0

        class _Clock:                     # the route reads after the pass
            @staticmethod
            def time():
                return now + 60.0
        monkeypatch.setattr(API, "time", _Clock)

        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(API, "_pool", pool)
        API._CACHE.clear()
        got = await API.opportunity_funnel(sleeve="INVESTMENT", strategy=CG,
                                           hours=1.0)
        assert got["status"] == "OK", got["why"]
        assert got["label"] == "RESEARCH"
        assert got["confidence_scope"] == C.PRODUCTION
        f = got["data"]["overall"]
        assert (f["book"], f["sleeve"], f["strategy"]) == (
            "PAPER", "INVESTMENT", CG)
        assert f["policy_version"] == PB.CG_VERSION
        tot = f["totals"]
        assert tot["unique_opportunities"] == 7
        assert tot["evaluations"] == 13 and tot["re_evaluations"] == 6
        assert tot["entered_unique"] == 1
        by = {b["blocker"]: b for b in f["blockers"]}
        rec = {k: recorded[s]["why"][0] for k, s in slugs.items()}
        assert rec["below"] == PB.R_EDGE
        assert rec["fees1"] == PB.R_FEES_CONSUME_EDGE
        stale, settle = rec["stale"], rec["near"]
        assert rec["old"] == settle and settle not in (PB.R_EDGE, stale)
        # BY UNIQUE OPPORTUNITIES: fees (2) and the settlement refusal (2)
        # above the stale probability (1) and the gross edge (1) -- the rows
        # ranked the stale probability (4) first
        assert by[PB.R_FEES_CONSUME_EDGE]["unique_opportunities"] == 2
        assert by[settle]["unique_opportunities"] == 2
        assert by[stale]["unique_opportunities"] == 1
        assert by[stale]["evaluations"] == 4
        assert by[stale]["re_evaluations"] == 3
        assert by[PB.R_EDGE]["unique_opportunities"] == 1
        assert by[PB.R_EDGE]["re_evaluations"] == 2
        assert f["row_ranking_for_comparison"][0] == stale
        assert {b["blocker"] for b in f["blockers"][:2]} == {
            PB.R_FEES_CONSUME_EDGE, settle}
        # the stale probability is never priced
        assert by[stale]["missed_executable_ev_usd"] is None
        assert set(by[stale]["missed_ev_unavailable_why"]) == {
            FN.R_PROBABILITY_NOT_FRESH}
        # the gross-edge and fee refusals: priced on their own fresh books,
        # a measured zero (the levels cost more than p after fees)
        assert by[PB.R_EDGE]["missed_executable_ev_usd"] == 0.0
        assert by[PB.R_FEES_CONSUME_EDGE]["missed_executable_ev_usd"] == 0.0
        # the settlement refusal: one near miss on the fresh book (500 x
        # (0.62 - 0.50 - fee)), the 60 s book never used
        s = by[settle]
        assert s["near_misses"] == 1, s
        assert s["missed_ev_measured"] == 1 and s["missed_ev_unavailable"] == 1
        assert set(s["missed_ev_unavailable_why"]) == {CP.R_NO_BOOK}
        assert 0 < s["missed_executable_ev_usd"] < 500 * 0.12
        assert tot["near_misses"] == 1
        st = {x["stage"]: x["unique_opportunities"] for x in f["stages"]}
        assert st["EVALUATED"] == 7 and st["ENTERED"] == 1
        assert st["POSITIVE_EXECUTABLE_EV"] == 2       # near + enter
        # the TRAINING funnel holds none of it
        API._CACHE.clear()
        tr = await API.opportunity_funnel(sleeve="TRAINING", strategy=None,
                                          hours=1.0)
        assert tr["status"] == "OK", tr["why"]
        assert tr["data"]["overall"]["totals"]["unique_opportunities"] == 0
        assert tr["confidence_scope"] == C.RESEARCH_SCOPE
        # a strategy outside the sleeve is refused by name
        API._CACHE.clear()
        bad = await API.opportunity_funnel(sleeve="TRAINING", strategy=CG,
                                           hours=1.0)
        assert bad["status"] == "UNAVAILABLE"
        assert "IS_NOT_IN_THE_TRAINING_SLEEVE" in bad["why"]
    finally:
        await PL.purge_everything(conn)
        await conn.close()
