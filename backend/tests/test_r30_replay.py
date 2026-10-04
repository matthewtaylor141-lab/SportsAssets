"""THE HISTORICAL R30 DECISION REPLAY (specs/HISTORICAL_REPLAY.md): only the
evidence recorded by each replay clock; REPLAY_NOT_FORWARD_EVIDENCE; no
authority.

  §1 THE CHOKE POINT (pit.Reader). A deliberately FUTURE-DATED row (recorded
     after the clock, claiming an earlier observation) is invisible at the
     clock and visible after it; a row forced past the filter raises
     HindsightViolation; unregistered tables, hidden columns, filters on
     not-yet-revealed columns, subqueries / joins and clocks past the
     horizon are refused; an order's terminal state, a Karen challenge's
     response and resolution and a rewritten pre-map row are revealed only
     at their own stamps; a snapshot answers exactly what a direct read
     answers.
  §2 ONE CHOKE POINT, NO AUTHORITY (static + runtime). No replay module but
     pit.py and store.py holds a query or touches the connection; store.py
     writes r30_replay_* only; the endpoint module holds no SQL; the replay
     imports no order / venue / execution / funded module, statically or at
     runtime.
  §3 THE END-TO-END REPLAY on a synthetic book: every component at the
     decision clock (provider observation, receipt, mapping, valuation,
     venue book, Derek, Karen, Eddie, Allie, opportunity score, canonical
     intent and its parity with the intent recorded at the time), the PAPER
     execution, Xavier's management actual vs canonical, the settlement /
     release timeline, every counterfactual, per INDEPENDENT event the
     decision / sizing / execution / management deltas, capital-hours,
     marked drawdown, opportunity cost, lost-opportunity classes and the
     Alpha Attribution V2 identity; UNAVAILABLE with reasons where an input
     was never recorded; the anti-hindsight audit finds no violation.
  §4 PERSISTENCE (migration 237): one transaction, append-only, every
     anti-hindsight / label / authority CHECK enforced by the database; the
     rollback applies and refuses while a run exists.
  §5 THE ENDPOINT: GET only, command auth, READ ONLY transaction, bounded,
     labelled; EMPTY with a reason when no run exists.
"""
from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys
from contextlib import asynccontextmanager

import asyncpg
import pytest

from sportsassets.replay import LABEL
from sportsassets.replay import pit as P
from sportsassets.replay import runner as RN
from sportsassets.replay import store as ST

try:
    from tests import paper_harness as H
    from tests import replay_fixture as F
except ImportError:                                             # pragma: no cover
    import paper_harness as H
    import replay_fixture as F

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
REPLAY = sorted((PKG / "replay").glob("*.py"))
MIG = ROOT / "migrations"
UP = (MIG / "237_r30_replay.sql").read_text()
DOWN = (MIG / "rollback" / "237_r30_replay.down.sql").read_text()


async def _tx():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    return conn, tx


async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


# ═════════════════════════════════════════════════════════════════════
# §1 THE CHOKE POINT
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_future_dated_row_is_invisible_at_the_clock():
    conn, tx = await _tx()
    try:
        acct, B = await F.account(conn)
        slug = F.uid("pit-")
        early = await F.book(conn, slug, observed_at=B - 5, recorded_at=B - 5,
                             bids=((0.5, 10),), offers=((0.52, 10),))
        # THE DELIBERATELY FUTURE-DATED ROW: observed "before" the clock but
        # recorded after it
        late = await F.book(conn, slug, observed_at=B - 1, recorded_at=B + 1,
                            bids=((0.1, 1),), offers=((0.2, 1),))
        R = P.Reader(conn, horizon=B + 10)
        cols = ("obs_id", "observed_at")
        where = "us_market_slug = $1 AND observed_at <= to_timestamp($2)"
        at = await R.rows("paper_book_observations", clock=B, cols=cols,
                          where=where, args=(slug, B),
                          order="observed_at DESC", limit=5, scope="s")
        assert [r["obs_id"] for r in at] == [early]
        assert R.scope("s")["max_recorded_at"] <= B
        after = await R.rows("paper_book_observations", clock=B + 2,
                             cols=cols, where=where, args=(slug, B),
                             order="observed_at DESC", limit=5)
        assert [r["obs_id"] for r in after] == [late, early]
        # a snapshot read once answers each clock exactly as a direct read
        snap = await R.snapshot("paper_book_observations", until=B + 10,
                                cols=cols, where="us_market_slug = $1",
                                args=(slug,))
        assert [r["obs_id"] for r in snap.at(B)] == [early]
        assert sorted(r["obs_id"] for r in snap.at(B + 2)) == sorted(
            [early, late])
        with pytest.raises(P.HindsightViolation):
            snap.at(B + 11)                       # past what it read
        with pytest.raises(P.HindsightViolation):
            await R.rows("paper_book_observations", clock=B + 11, cols=cols,
                         limit=1)                 # past the horizon
    finally:
        await tx.rollback()
        await conn.close()


class _FakeConn:
    """Returns a row recorded after the clock regardless of the filter: the
    Python post-check must stop it."""

    def __init__(self, rec):
        self.rec = rec

    async def fetch(self, sql, *args):
        assert "<= to_timestamp(" in sql and " JOIN " not in sql.upper()
        return [{"obs_id": 1, "__recorded": self.rec}]


async def test_a_row_forced_past_the_filter_raises_hindsight_violation():
    R = P.Reader(_FakeConn(1000.5), horizon=2000)
    with pytest.raises(P.HindsightViolation):
        await R.rows("paper_book_observations", clock=1000.0,
                     cols=("obs_id",), limit=1)
    ok = await P.Reader(_FakeConn(999.0), horizon=2000).rows(
        "paper_book_observations", clock=1000.0, cols=("obs_id",), limit=1)
    assert ok[0]["__recorded_at"] == 999.0


async def test_the_reader_refuses_every_way_around_the_clock():
    R = P.Reader(_FakeConn(1.0), horizon=2000)
    bad = [
        dict(table="paper_ledger_shadow", cols=("x",)),          # unregistered
        dict(table="paper_orders", cols=("updated_at",)),        # hidden
        dict(table="karen_challenges", cols=("state",)),         # hidden
        dict(table="paper_orders", cols=("order_id",),
             where="state = 'FILLED'"),                          # not revealed
        dict(table="external_valuations", cols=("id",),
             where="outcome_known"),                             # outcome
        dict(table="paper_fills", cols=("fill_id",),
             where="order_id IN (SELECT order_id FROM paper_orders)"),
        dict(table="paper_fills", cols=("fill_id",),
             where="TRUE; DELETE FROM paper_fills"),
        dict(table="paper_fills", cols=("fill_id, 1",)),         # expression
        dict(table="paper_fills", cols=("fill_id",), limit=P.MAX_LIMIT + 1),
    ]
    for b in bad:
        with pytest.raises(P.HindsightViolation):
            await R.rows(b["table"], clock=1000.0, cols=b["cols"],
                         where=b.get("where", ""),
                         limit=b.get("limit", 10))


@pg
async def test_late_written_columns_are_revealed_only_at_their_own_stamps():
    conn, tx = await _tx()
    try:
        acct, B = await F.account(conn)
        slug = F.uid("pit-o-")
        d = await F.decision(conn, acct, slug=slug, at=B, recorded_at=B)
        await F.order(conn, acct, decision_id=d, group_id=F.uid("g-"),
                      slug=slug, qty=10.0, limit=0.5, at=B,
                      terminal_at=B + 50)
        R = P.Reader(conn, horizon=B + 1000)
        cols = ("order_id", "state", "filled_qty", "terminal_at")
        o1 = (await R.rows("paper_orders", clock=B + 10, cols=cols,
                           where="decision_id = $1", args=(d,), limit=1))[0]
        assert o1["state"] is None and o1["filled_qty"] is None
        assert o1["state_at_clock"] == "OPEN_AT_THE_CLOCK"
        assert set(o1["__masked"]) >= {"state", "filled_qty", "terminal_at"}
        o2 = (await R.rows("paper_orders", clock=B + 60, cols=cols,
                           where="decision_id = $1", args=(d,), limit=1))[0]
        assert o2["state_at_clock"] == "FILLED" and o2["filled_qty"] == 10.0
        # Karen: OPEN, then RESPONDED, then UPHELD -- each at its own stamp
        cid = await F.karen(conn, target_id=d, challenged_at=B + 1,
                            created_at=B + 1)
        await conn.execute(
            "UPDATE karen_challenges SET state='RESPONDED', "
            " responded_by='DEREK', responded_at=$2, "
            " response_stance='CONCEDE', response='conceded' "
            " WHERE challenge_id=$1", cid, F.ts(B + 100))
        await conn.execute(
            "UPDATE karen_challenges SET state='UPHELD', outcome='UPHELD', "
            " resolved_by='owner-test', resolved_at=$2, "
            " outcome_reason='synthetic' WHERE challenge_id=$1", cid,
            F.ts(B + 200))
        kc = ("challenge_id", "responded_at", "resolved_at", "outcome")

        async def state(c):
            got = await R.rows("karen_challenges", clock=c, cols=kc,
                               where="challenge_id = $1", args=(cid,),
                               limit=1)
            return got[0]["state_at_clock"] if got else None
        assert [await state(c) for c in (B, B + 50, B + 150, B + 250)] == [
            None, "OPEN", "RESPONDED", "UPHELD"]
        # a pre-map row rewritten after the clock is invisible at it
        await F.premap(conn, slug, game_start=B + 3600, updated_at=B + 30)
        pm = ("market_slug", "game_start")
        assert await R.rows("us_premap", clock=B + 10, cols=pm,
                            where="market_slug = $1", args=(slug,),
                            limit=1) == []
        assert len(await R.rows("us_premap", clock=B + 40, cols=pm,
                                where="market_slug = $1", args=(slug,),
                                limit=1)) == 1
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §2 ONE CHOKE POINT, NO AUTHORITY
# ═════════════════════════════════════════════════════════════════════

import re as _re

#: a query, not prose: SELECT ... FROM, INSERT INTO, UPDATE x SET, DELETE
#: FROM, JOIN x ON, TRUNCATE
SQL = _re.compile(r"(?is)\bSELECT\b.*\bFROM\b|\bINSERT\s+INTO\b|"
                  r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|"
                  r"\bJOIN\s+\w+\b.*\bON\b|\bTRUNCATE\b")
CONN_CALLS = {"fetch", "fetchrow", "fetchval", "execute", "executemany",
              "copy_records_to_table", "cursor"}


def _strings(path):
    """Every string constant except docstrings (prose)."""
    tree = ast.parse(path.read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant):
                docs.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def _calls(path):
    return {n.func.attr for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def test_only_the_choke_point_and_the_store_touch_the_database():
    for path in REPLAY:
        if path.name in ("pit.py", "store.py"):
            continue
        for s in _strings(path):
            assert not SQL.search(s), (path.name, s[:80])
        assert not (_calls(path) & CONN_CALLS), (path.name,
                                                 _calls(path) & CONN_CALLS)
    # the choke point issues SELECTs only
    write = _re.compile(r"(?is)\bINSERT\s+INTO\b|\bUPDATE\s+\w+\s+SET\b|"
                        r"\bDELETE\s+FROM\b|\bTRUNCATE\b")
    for s in _strings(PKG / "replay" / "pit.py"):
        assert not write.search(s), s[:80]


def test_the_store_writes_only_the_replay_tables_and_the_route_holds_no_sql():
    import re
    writes = []
    for s in _strings(PKG / "replay" / "store.py"):
        for kw, table in re.findall(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)"
                                    r"\s+([a-z0-9_]+)", s):
            writes.append(table)
        for table in re.findall(r"\bFROM\s+([a-z0-9_]+)", s):
            assert table.startswith("r30_replay_"), (table, s[:80])
    assert writes and all(t.startswith("r30_replay_") for t in writes)
    for s in _strings(PKG / "api" / "command_r30_replay.py"):
        assert not SQL.search(s), s[:80]


FORBIDDEN = ("execmirror", "kalshi", "pmus", "clob", "executor", "execution",
             "funded", "venue", "live_", "submit", "order_state", "smalllive",
             "paper_benchmark", "paper_xavier", "paper_derek",
             "bettor_paper_ledger", "bettor_paper_simulator")


def test_the_replay_imports_no_order_venue_execution_or_funded_module():
    allowed_top = {"__future__", "datetime", "re", "dataclasses", "decimal",
                   "hashlib", "json", "math", "time", "argparse", "asyncio",
                   "os", "subprocess", "sys", "asyncpg", "fastapi"}
    files = REPLAY + [PKG / "api" / "command_r30_replay.py",
                      PKG / "scripts" / "r30_replay.py"]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [("." * node.level) + (node.module or "")] + [
                    a.name for a in node.names]
            for n in names:
                leaf = n.rsplit(".", 1)[-1]
                assert not any(f in leaf for f in FORBIDDEN), (path.name, n)
                if not n.startswith(".") and "." not in n and \
                        n not in allowed_top and node.__class__ is ast.Import:
                    raise AssertionError((path.name, n))


def test_importing_the_replay_loads_no_execution_module_at_runtime():
    code = (
        "import sys\n"
        "import sportsassets.replay.runner, sportsassets.replay.store\n"
        "import sportsassets.intel.attribution_v2\n"
        "import sportsassets.research_ref.marginal_capital_value\n"
        "bad = [m for m in sys.modules if m.startswith('sportsassets') and "
        "any(f in m.rsplit('.', 1)[-1] for f in %r)]\n"
        "print(bad)\n" % (("execmirror", "execution", "live_", "funded",
                           "venue", "kalshi", "pmus", "clob", "executor",
                           "submit", "live_parity"),))
    got = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=120)
    assert got.returncode == 0, got.stderr[-2000:]
    assert got.stdout.strip() == "[]", got.stdout


# ═════════════════════════════════════════════════════════════════════
# §3 THE END-TO-END REPLAY
# ═════════════════════════════════════════════════════════════════════

def _item(built, did):
    return next(it for it in built["items"]
                if it["rec"]["decision"]["decision_id"] == did)


@pg
async def test_the_end_to_end_replay_uses_only_what_each_clock_knew():
    conn, tx = await _tx()
    try:
        sc = await F.scenario(conn)
        B = sc["B"]
        built = await RN.run(conn, start=B - 2000, end=sc["end"],
                             account_id=sc["acct"]["account_id"],
                             now=sc["now"])
        s = built["summary"]
        assert s["label"] == LABEL and s["decisions"] == 5
        assert s["promotion"].startswith("NONE")
        ah = s["anti_hindsight"]
        assert ah["violations"] == 0 and ah["decisions_checked"] == 5
        for it in built["items"]:
            ev = it["rec"]["evidence"]
            assert ev["decision"]["max_recorded_at"] is None or \
                ev["decision"]["max_recorded_at"] <= it["rec"]["clock"]
            assert ev["outcome"]["max_recorded_at"] <= sc["end"]

        # ── d1: the INVESTMENT position, component by component ──────
        d1 = _item(built, sc["d1"])
        c = d1["rec"]["components"]
        assert d1["rec"]["clock"] == pytest.approx(B + 0.2, abs=1e-3)
        po = c["provider_observation"]
        assert po["status"] == "MEASURED" and po["valuation_id"] == sc["v1"]
        assert c["bettor_receipt"]["receipt_latency_s"] == pytest.approx(3.0)
        assert c["mapping"]["event_start_at"] == pytest.approx(B + 3600)
        assert c["valuation"]["probability"] == pytest.approx(0.62)
        assert c["valuation"]["outcome_columns"].startswith("masked")
        # the FUTURE-DATED book (recorded after the clock) is not used
        assert c["venue_book"]["obs_id"] == sc["b1"] != sc["b_future"]
        assert c["derek"]["verdict"] == "ENTER"
        # Karen: the challenge recorded before the clock is OPEN; the one
        # recorded after it does not exist yet
        assert c["karen"]["open_on_market"] == 1
        assert c["karen"]["open_total"] == 1
        assert c["eddie"]["status"] == "MEASURED"
        assert c["eddie"]["replay_basis"].startswith("RECOMPUTED")
        a = c["allie"]
        assert a["status"] == "MEASURED"
        assert a["authority"] == "SHADOW_PENDING_OWNER_APPROVAL"
        # the six lag samples of the scenario (the shared test database may
        # hold other suites' settled markets too: settlements are market
        # facts, not scoped to the account)
        assert a["evidence"]["settlement_lag_samples"] >= 6
        assert a["final_allocatable_usd"] == pytest.approx(56.1)
        assert "POSITION_TRUTH" in a["replay_inputs"]["exposure_basis"] or \
            "PAPER_FILLS" in a["replay_inputs"]["exposure_basis"]
        assert c["canonical_intent"]["status"] == "BUILT"
        assert c["canonical_intent"]["verifies"] is True
        assert d1["rec"]["intent_parity"]["state"] == "REPRODUCED"
        # the alternatives: the qualified refusal on m2 shortly before
        assert len(d1["rec"]["tape"]["alternatives"]) == 1
        # counterfactuals (V1 realized: 100 bought at 0.554 + 0.70 fees, 40
        # sold at 0.70 - 0.20, 60 paid 1.00)
        cf = d1["eval"]["counterfactuals"]
        assert cf["CURRENT_ACTION"]["pnl_usd"] == pytest.approx(31.7)
        assert cf["HOLD_TO_SETTLEMENT"]["pnl_usd"] == pytest.approx(43.9)
        assert cf["NO_TRADE"]["pnl_usd"] == 0.0
        assert cf["R30_CANONICAL_ACTION"]["pnl_usd"] == pytest.approx(31.7)
        assert cf["ACTUAL_XAVIER_MANAGEMENT"]["pnl_usd"] == pytest.approx(31.7)
        assert cf["CANONICAL_XAVIER_MANAGEMENT"]["pnl_usd"] == \
            pytest.approx(31.7)
        assert cf["CANONICAL_XAVIER_MANAGEMENT"]["basis"].startswith(
            "CANONICAL_MATCHED_EVERY_REVIEW")
        mg = d1["eval"]["management"]
        assert mg["reviews"] == 2 and mg["divergent"] == 0
        assert [r["canonical_action"] for r in mg["per_review"]] == [
            "SELL_REDUCE", "MAINTAIN_STANDING_PROTECTION"]
        for k in ("ROI_ONLY_ALLOCATION", "CAPITAL_HOUR_ALLOCATION",
                  "ALLIE_ALLOCATION_SHADOW"):
            assert cf[k]["pnl_usd"] is not None, (k, cf[k])
        assert cf["CURRENT_ACTION"]["capital_hours"] > 0
        assert d1["eval"]["lost_opportunity"]["classification"] == "TAKEN"
        # Alpha Attribution V2: the full identity, exact to float rounding
        v2 = d1["v2"]
        assert v2["identity"]["claimed"] and v2["identity"]["level"] == "FULL"
        assert abs(v2["identity"]["check"]["residual_usd"]) <= 1e-9
        assert v2["realized_pnl_usd"] == pytest.approx(31.7)
        ce = v2["capital_efficiency"]
        assert ce["cash_unavailable_by_prior_allocations"]["n"] == 1
        assert ce["missed_executable_ev_from_occupied_capital"][
            "total_usd"] == pytest.approx(1.5)

        # ── d2: a qualified refusal ────────────────────────────────────
        d2 = _item(built, sc["d2"])
        lo = d2["eval"]["lost_opportunity"]
        assert lo["classification"] == "GOOD_REFUSAL"
        assert lo["hypothetical_pnl"]["label"] == "HYPOTHETICAL"
        assert lo["hypothetical_pnl"]["value"] == pytest.approx(-25.4)
        assert d2["v2"] is None
        # its pre-map row was rewritten after the clock: UNAVAILABLE, named
        assert d2["rec"]["event_start_basis"].startswith(
            "NO_EVENT_START_RECORDED_AT_OR_BEFORE_THE_CLOCK")
        assert d2["rec"]["components"]["allie"]["status"] == "UNAVAILABLE"
        assert "expected_hours_to_capital_release" in \
            d2["rec"]["components"]["allie"]["why"]
        assert d2["rec"]["components"]["provider_observation"][
            "why"] == "DECISION_RECORDS_NO_VALUATION_ID"

        # ── d0: before the ledger existed -> capital UNAVAILABLE ──────
        d0 = _item(built, sc["d0"])
        assert d0["rec"]["components"]["karen"]["state"] == "UNAVAILABLE"
        assert d0["rec"]["components"]["karen"]["why"].startswith(
            "NO_KAREN_RECORD_AT_OR_BEFORE_THE_CLOCK")
        assert d0["rec"]["allocations"]["EQUAL_ALLOCATION"]["why"] == \
            "IDLE_CAPITAL_UNMEASURED_AT_THE_CLOCK"

        # ── d3: refused by the ledger for cash ─────────────────────────
        d3 = _item(built, sc["d3"])
        assert d3["eval"]["lost_opportunity"]["classification"] == \
            "CASH_UNAVAILABLE_BY_PRIOR_ALLOCATION"
        assert d3["rec"]["sleeve"] == "TRAINING"

        # ── d4: no canonical intent -> no new INVESTMENT exposure ──────
        d4 = _item(built, sc["d4"])
        assert d4["rec"]["components"]["canonical_intent"]["status"] == \
            "UNAVAILABLE"
        cf4 = d4["eval"]["counterfactuals"]
        assert cf4["CURRENT_ACTION"]["pnl_usd"] == pytest.approx(-4.55)
        assert cf4["R30_CANONICAL_ACTION"]["pnl_usd"] == 0.0
        assert "NO_NEW_INVESTMENT_EXPOSURE" in \
            cf4["R30_CANONICAL_ACTION"]["basis"]

        # ── per INDEPENDENT event ──────────────────────────────────────
        ev = {e["event_key"]: e for e in built["events"]}
        assert len(ev) == 3                       # fx-m1, fx-m2, fx-m4
        e1 = ev["fx-" + sc["m1"]]
        assert e1["decisions"] == 3 and e1["label"] == LABEL
        assert e1["realized_pnl_usd"] == pytest.approx(31.7)
        assert e1["pnl"]["HOLD_TO_SETTLEMENT"]["pnl_usd"] == \
            pytest.approx(43.9)
        assert e1["marked_drawdown_usd"] > 0
        assert e1["deltas"]["management"]["divergent"] == 0
        assert e1["deltas"]["execution"]["planned_qty"] == pytest.approx(100)
        assert e1["deltas"]["sizing"]["legacy_usd"] == pytest.approx(55.4)
        assert e1["lost_opportunity"] == {
            "CASH_UNAVAILABLE_BY_PRIOR_ALLOCATION": 1, "TAKEN": 1,
            "UNKNOWABLE": 1}
        assert e1["attribution"]["INVESTMENT"]["identity_full"]["holds"]
        assert e1["evidence"]["every_decision_input_recorded_by_its_clock"]
        e4 = ev["fx-" + sc["m4"]]
        assert e4["deltas"]["decision"]["differ_n"] == 1
        assert e4["pnl"]["R30_CANONICAL_ACTION"]["pnl_usd"] == 0.0
        # the run: INVESTMENT apart from the research block
        inv = s["investment"]["counterfactuals"]
        assert inv["CURRENT_ACTION"]["pnl_usd"] == pytest.approx(31.7 - 4.55)
        assert inv["R30_CANONICAL_ACTION"]["pnl_usd"] == pytest.approx(31.7)
        assert s["research_all_sleeves"]["decisions"] == 5
        assert s["attribution"]["INVESTMENT"]["identity_full"]["holds"]
        assert s["unavailable_census"]["karen"][
            "NO_KAREN_RECORD_AT_OR_BEFORE_THE_CLOCK"] >= 1

        # ── §4 persisted, append-only, CHECKed ─────────────────────────
        got = await ST.record_run(conn, built, code_sha="a" * 40,
                                  triggered_by="test")
        assert got["recorded"], got
        rid = got["run_id"]
        n = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM r30_replay_decisions WHERE run_id=$1)"
            " AS d, (SELECT count(*) FROM r30_replay_events WHERE run_id=$1)"
            " AS e", rid)
        assert (n["d"], n["e"]) == (5, 3)
        X = asyncpg.IntegrityConstraintViolationError
        for t in ("r30_replay_runs", "r30_replay_decisions",
                  "r30_replay_events"):
            await _expect(conn, X, "UPDATE %s SET label = label "
                          "WHERE run_id = $1" % t, rid)
            await _expect(conn, X, "DELETE FROM %s WHERE run_id = $1" % t, rid)
        await _expect(conn, X, "TRUNCATE r30_replay_events")
        C = asyncpg.CheckViolationError
        ins = ("INSERT INTO r30_replay_decisions (run_id, decision_id, "
               " event_key, sleeve, verdict, decision_clock, clock_end, "
               " decision_evidence_max_recorded_at, payload, label) VALUES "
               " ($1,$2,'e','INVESTMENT','ENTER',to_timestamp($3),"
               " to_timestamp($4),to_timestamp($5),$6::jsonb,$7)")
        ok = json.dumps({"label": LABEL})
        # THE DATABASE REFUSES HINDSIGHT: an input recorded after the clock
        await _expect(conn, C, ins, rid, "x1", B, B + 10, B + 1, ok, LABEL)
        await _expect(conn, C, ins, rid, "x2", B + 20, B + 10, B, ok, LABEL)
        await _expect(conn, C, ins, rid, "x3", B, B + 10, B,
                      json.dumps({"label": "FORWARD"}), LABEL)
        await _expect(conn, C, ins, rid, "x4", B, B + 10, B, ok,
                      "FORWARD_EVIDENCE")
        await conn.execute(ins, rid, "x5", B, B + 10, B, ok, LABEL)
        run = await conn.fetchrow("SELECT * FROM r30_replay_runs WHERE "
                                  "run_id = $1", rid)
        assert run["code_sha"] == "a" * 40 and run["label"] == LABEL
        assert run["production_effect"] == "NONE"
        assert json.loads(run["parameters"])["hurdle_quantile"] == 0.75
        assert run["clock_end"].timestamp() == pytest.approx(sc["end"])
        await _expect(conn, C, "INSERT INTO r30_replay_runs (run_id, "
                      " run_version, code_sha, parameters, params_sha, "
                      " clock_start, clock_end, started_at, finished_at, "
                      " decisions_n, events_n, summary, triggered_by) VALUES "
                      " ('r30rp_000000000000000000000000','v','a','{}',$1,"
                      " now(),now(),now(),now(),0,0,'{\"label\":"
                      "\"REPLAY_NOT_FORWARD_EVIDENCE\"}','t')", "b" * 64)
        await _expect(conn, C, "INSERT INTO r30_replay_runs (run_id, "
                      " run_version, code_sha, parameters, params_sha, "
                      " clock_start, clock_end, started_at, finished_at, "
                      " decisions_n, events_n, summary, triggered_by) VALUES "
                      " ('r30rp_000000000000000000000001','v',$2,'{}',$1,"
                      " now(),now() + interval '1 hour',now(),now(),0,0,"
                      " '{\"label\":\"REPLAY_NOT_FORWARD_EVIDENCE\"}','t')",
                      "b" * 64, "c" * 40)           # horizon in the future
        # ── §5 the endpoint reads it (READ ONLY, bounded, labelled) ───
        from sportsassets.api import command_r30_replay as API

        class _Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        async def pool():
            return _Pool()
        orig = API._pool
        API._pool = pool
        try:
            out = await API.r30_replay(run_id=None, limit=2,
                                       investment_only=False,
                                       decision_id=sc["d1"])
            assert out["label"] == LABEL and out["status"] == "OK"
            assert out["run"]["run_id"] == rid
            assert len(out["events"]) == 2                     # bounded
            assert out["decision"]["payload"]["label"] == LABEL
            assert out["decision"]["payload"]["counterfactuals"][
                "CURRENT_ACTION"]["pnl_usd"] == pytest.approx(31.7)
            inv_only = await API.r30_replay(run_id=rid, limit=100,
                                            investment_only=True,
                                            decision_id=None)
            assert all(e["investment_only"] for e in inv_only["events"])
            missing = await API.r30_replay(run_id="r30rp_" + "f" * 24,
                                           limit=5, investment_only=False,
                                           decision_id=None)
            assert missing["status"] == "NOT_FOUND"
        finally:
            API._pool = orig
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_failing_decision_is_named_and_a_hindsight_violation_stops_the_run(
        monkeypatch):
    from sportsassets.replay import reconstruct as RC
    conn, tx = await _tx()
    try:
        sc = await F.scenario(conn)
        real = RC.karen_at

        async def boom(ctx, dec, clock, scope):
            if dec["decision_id"] == sc["d2"]:
                raise ValueError("synthetic unreadable record")
            return await real(ctx, dec, clock, scope)
        monkeypatch.setattr(RC, "karen_at", boom)
        built = await RN.run(conn, start=sc["B"] - 2000, end=sc["end"],
                             account_id=sc["acct"]["account_id"],
                             now=sc["now"])
        assert built["summary"]["decisions"] == 4
        assert built["summary"]["decisions_not_replayed"] == 1
        bad = built["summary"]["notes"]["DECISIONS_NOT_REPLAYED"]
        assert bad == [{"decision_id": sc["d2"], "error":
                        "ValueError: synthetic unreadable record"}]

        async def leak(ctx, dec, clock, scope):
            raise P.HindsightViolation("forced")
        monkeypatch.setattr(RC, "karen_at", leak)
        with pytest.raises(P.HindsightViolation):
            await RN.run(conn, start=sc["B"] - 2000, end=sc["end"],
                         account_id=sc["acct"]["account_id"], now=sc["now"])
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_run_refuses_a_future_horizon_and_an_unbounded_window():
    conn = await asyncpg.connect(H.DSN)
    try:
        with pytest.raises(RN.ReplayRefused):
            await RN.run(conn, start=0, end=2e9, now=1.9e9)
        with pytest.raises(RN.ReplayRefused):
            await RN.run(conn, start=0, end=1.9e9, now=1.9e9)
        with pytest.raises(RN.ReplayRefused):
            await RN.run(conn, start=10, end=5, now=1.9e9)
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4 THE MIGRATION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_237_is_idempotent_and_its_rollback_applies_and_guards_runs():
    conn, tx = await _tx()
    try:
        await conn.execute(UP)
        await conn.execute(UP)                          # idempotent
        await conn.execute(DOWN)                        # empty: applies
        assert not await conn.fetchval(
            "SELECT to_regclass('r30_replay_runs') IS NOT NULL")
        await conn.execute(UP)
        await conn.execute(
            "INSERT INTO r30_replay_runs (run_id, run_version, code_sha, "
            " parameters, params_sha, clock_start, clock_end, started_at, "
            " finished_at, decisions_n, events_n, summary, triggered_by) "
            "VALUES ('r30rp_000000000000000000000002','v',$1,'{}',$2,"
            " now() - interval '1 day',now() - interval '1 hour',now(),now(),"
            " 0,0,'{\"label\":\"REPLAY_NOT_FORWARD_EVIDENCE\"}','t')",
            "UNAVAILABLE:TEST", "d" * 64)
        await _expect(conn, asyncpg.RaiseError, DOWN)    # refuses
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §5 THE ENDPOINT
# ═════════════════════════════════════════════════════════════════════

def test_the_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    from sportsassets.api import command_r30_replay as API
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
    assert client.get(API.PATH + "?limit=5").status_code == 401
    assert client.post(API.PATH).status_code in (401, 405)


@pg
async def test_the_route_answers_empty_with_a_reason_and_reads_read_only():
    from sportsassets.api import command_r30_replay as API
    conn, tx = await _tx()
    try:
        has_runs = await conn.fetchval("SELECT count(*) FROM r30_replay_runs")

        class _Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        async def pool():
            return _Pool()
        orig = API._pool
        API._pool = pool
        try:
            out = await API.r30_replay(run_id=None, limit=5,
                                       investment_only=False,
                                       decision_id=None)
        finally:
            API._pool = orig
        assert out["label"] == LABEL
        if not has_runs:
            assert out["status"] == "EMPTY"
            assert out["why"] == "NO_REPLAY_RUN_RECORDED"
            assert out["events"] == [] and out["run"] is None
    finally:
        await tx.rollback()
        await conn.close()


async def test_the_route_reads_in_a_read_only_transaction():
    from sportsassets.api import command_r30_replay as API
    seen = {}

    class _Tx:
        def __init__(self, ro):
            seen["readonly"] = ro

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Conn:
        def transaction(self, readonly=False):
            return _Tx(readonly)

        async def execute(self, sql):
            seen["sql"] = sql

        async def fetchval(self, sql, *a):
            return False                           # migration absent

    class _Pool:
        @asynccontextmanager
        async def acquire(self):
            yield _Conn()

    async def pool():
        return _Pool()
    orig = API._pool
    API._pool = pool
    try:
        out = await API.r30_replay(run_id=None, limit=5,
                                   investment_only=False, decision_id=None)
    finally:
        API._pool = orig
    assert seen["readonly"] is True
    assert "statement_timeout" in seen["sql"]
    assert out["status"] == "UNAVAILABLE"
    assert out["why"] == "MIGRATION_237_NOT_APPLIED"
