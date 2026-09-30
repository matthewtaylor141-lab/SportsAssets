"""AUDREY'S DAILY AUDIT (agents/audrey_audit.py, migration 155).

What these pin, against real ledger rows in a migrated database (all
SYNTHETIC, dated 2032, prefixed `audt-`):

  * THE DAY IS THE CONFIGURED LOCAL DAY (AUDREY_TIMEZONE, default
    America/New_York, boundary 00:00 local), recorded on the report; a DST
    day is 23 hours; a decision at 23:30 local belongs to that local day.
  * IDEMPOTENT AND VERSIONED: the same day on the same evidence is the same
    report id and writes nothing; new evidence writes version + 1 naming
    what it supersedes; a report row is never rewritten.
  * THE WATERMARK: only completed days past it are audited; a rerun is a
    no-op; a restart resumes.
  * NO_CHANGE: a day whose evidence justifies nothing says so explicitly.
  * A COLLECTION BOTTLENECK files ONE deterministic improvement task.
  * PROOF 13: actual P&L, hypothetical execution on a known settlement,
    simulated values and unevaluable alternatives are four categories that
    are never merged; a refused alternative whose contract settled
    profitably is never counted as actual or as filled.
  * PROOF 15: repeated decisions on one fixture are one example.
  * ATTRIBUTION sums exactly; a good decision that lost and a poor one that
    won are named as such.
  * THE WORKSPACE route: every section, truthful states, the provider key's
    presence only.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest

from sportsassets.agents import audrey_audit as AA
from sportsassets.agents import improvement as IMP
from tests import audrey_helpers as H

pg = H.pg
NY = "America/New_York"
#: THE AUDITED LOCAL DAY: Tuesday 2032-03-16 (after the 2032-03-14 DST
#: change). Its local midnight is 04:00 UTC.
D = dt.date(2032, 3, 16)


def _bounds(day=D):
    from zoneinfo import ZoneInfo
    return AA.day_bounds(day, ZoneInfo(NY))


D0, D1 = _bounds()


@pytest.fixture(autouse=True)
def _tz(monkeypatch):
    monkeypatch.setenv(AA.TZ_ENV, NY)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)


@pytest.fixture()
async def db():
    if not H.DSN:
        pytest.skip("needs RN1X_TEST_DSN")
    async with H.connect() as c:
        if not await AA.has_schema(c):
            pytest.skip("migration 155 is not in this database")
        await H.ensure_core_tables(c)
        await H.purge(c)
        yield c
        await H.purge(c)


# ═════════════════════════════════════════════════════════════════════
# 0 · THE CLOCK (pure)
# ═════════════════════════════════════════════════════════════════════

def test_the_day_is_the_configured_local_day_and_dst_is_honoured(monkeypatch):
    name, tz, note = AA.audit_timezone()
    assert name == NY and note is None
    s, e = AA.day_bounds(D, tz)
    assert dt.datetime.fromtimestamp(s, dt.timezone.utc).hour == 4
    assert e - s == 86400.0
    # THE DST DAY (2032-03-14) is 23 hours long in New York
    s2, e2 = AA.day_bounds(dt.date(2032, 3, 14), tz)
    assert e2 - s2 == 23 * 3600.0
    # 23:30 local on D is 03:30 UTC on D+1 and still day D
    late = D1 - 1800.0
    assert AA.local_day(late, tz) == D
    assert AA.last_completed_day(D1 + 3600.0, tz) == D
    assert AA.report_id_for(D, name) == "audrey:America/New_York:2032-03-16"
    monkeypatch.setenv(AA.TZ_ENV, "Europe/London")
    assert AA.audit_timezone()[0] == "Europe/London"
    monkeypatch.setenv(AA.TZ_ENV, "Not/AZone")
    name, _, note = AA.audit_timezone()
    assert name == AA.DEFAULT_TZ and "Not/AZone" in note


def test_attribution_sums_exactly_and_refuses_to_split_without_a_mark():
    a = AA.attribute(total=4.9, entry_cash=-5.1, handoff_mark=5.9)
    assert a["derek_credit_usd"] == pytest.approx(0.8)
    assert a["xavier_credit_usd"] == pytest.approx(4.1)
    assert a["sums_exactly"] is True
    assert AA.attribute(total=4.9, entry_cash=-5.1,
                        handoff_mark=None)["status"] == "UNATTRIBUTED"
    assert AA.attribute(total=None, entry_cash=-5.1,
                        handoff_mark=5.9)["status"] == "UNATTRIBUTED"


def test_quality_is_judged_on_decision_time_evidence():
    assert AA.quality(True, -5.0) == AA.Q_GOOD_LOST
    assert AA.quality(False, 5.0) == AA.Q_POOR_WON
    assert AA.quality(True, None) == AA.Q_GOOD_PENDING
    assert AA.quality(None, 5.0) == AA.Q_UNJUDGEABLE


def test_the_evidence_sha_ignores_when_it_was_computed():
    r = {"a": 1, "computed_at": 1.0, "prior_improvements": {"x": 1},
         "run": {"y": 2}}
    r2 = dict(r, computed_at=2.0, prior_improvements={"x": 9}, run={})
    assert AA.evidence_sha(r) == AA.evidence_sha(r2)
    assert AA.evidence_sha(r) != AA.evidence_sha(dict(r, a=2))


# ═════════════════════════════════════════════════════════════════════
# 1 · IDEMPOTENT, VERSIONED, APPEND-ONLY
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_same_evidence_same_report_new_evidence_new_version(db):
    import asyncpg
    pos = await H.position(db, 1, fill_at=D0 + 3600)       # open
    await H.decide(db, pos, decided=D0 + 7200, p=0.6)
    now = D1 + 3600
    v1 = await AA.audit_day(db, day=D, now=now)
    assert v1["written"] and v1["version"] == 1
    assert v1["report_id"] == "audrey:America/New_York:2032-03-16"
    rep = v1["report"]
    assert rep["timezone"] == NY and rep["day_boundary"] == "00:00"
    assert rep["boundary_settles_nothing"] is True
    assert rep["positions"]["states"][AA.S_PENDING] == 1
    again = await AA.audit_day(db, day=D, now=now + 900)
    assert again["written"] is False and again["version"] == 1
    assert again["why"] == "SAME_EVIDENCE_SAME_REPORT"
    # A RESTART: a fresh connection finds the same report
    async with H.connect() as fresh:
        r3 = await AA.audit_day(fresh, day=D, now=now + 1800)
    assert r3["written"] is False and r3["version"] == 1
    # NEW EVIDENCE: the venue settles the position two days later
    settle = D1 + 2 * H.DAY
    await db.execute(
        "UPDATE bettor_funded_intents SET residual_qty=0, closed_at=$2, "
        " closed_reason='SETTLED_BY_THE_VENUE', settlement=$3::jsonb "
        " WHERE intent_id=$1", pos["intent_id"], H.ts(settle),
        json.dumps({"payout_price": 1.0, "at": settle,
                    "terminal_reading": "REPORTED_SETTLEMENT"}))
    await db.execute(
        "INSERT INTO bettor_funded_economics (event_id, intent_id, at, kind, "
        " amount_usd, qty, basis) VALUES ($1,$2,$3,'SETTLEMENT',10,10,"
        " 'TEST_SYNTHETIC')", "fev:%s:SETTLEMENT:REPORTED_SETTLEMENT"
        % pos["intent_id"], pos["intent_id"], H.ts(settle))
    v2 = await AA.audit_day(db, day=D, now=settle + 60)
    assert v2["written"] and v2["version"] == 2
    assert v2["change"]["supersedes"] == 1
    assert "positions" in v2["change"]["changed_sections"]
    assert v2["report"]["positions"]["states"][AA.S_FINAL] == 1
    assert v2["report"]["positions"]["states"][AA.S_PENDING] == 0
    rows = await db.fetch(
        "SELECT version, supersedes_version, evidence_sha FROM "
        " audrey_audit_reports WHERE report_id=$1 ORDER BY version",
        v1["report_id"])
    assert [(r["version"], r["supersedes_version"]) for r in rows] == [
        (1, None), (2, 1)]
    assert rows[0]["evidence_sha"] == v1["evidence_sha"]
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db.execute("UPDATE audrey_audit_reports SET summary='x' "
                         " WHERE report_id=$1", v1["report_id"])
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db.execute("DELETE FROM audrey_audit_reports "
                         " WHERE report_id=$1", v1["report_id"])


@pg
async def test_run_due_audits_completed_days_past_the_watermark(db):
    # A DECISION AT 23:30 LOCAL ON D (03:30 UTC ON D+1) belongs to D
    pos = await H.position(db, 1, fill_at=D0 + 3600)
    xid = await H.decide(db, pos, decided=D1 - 1800, p=0.6)
    mid_next = D1 + 12 * 3600                    # noon-ish local on D+1
    first = await AA.run_due(db, now=mid_next)
    assert first["ok"], first
    assert [a["report_id"] for a in first["audited"]] == [
        "audrey:America/New_York:2032-03-16"]
    rep = await AA.report(db, "audrey:America/New_York:2032-03-16")
    ids = [r["xavier_decision_id"] for r in
           rep["report"]["xavier"]["decision_rows"]]
    assert ids == [xid]
    wm = await db.fetchrow("SELECT * FROM audrey_audit_watermarks")
    assert wm["last_completed_day"] == D and wm["timezone"] == NY
    # THE SAME CALL AGAIN: nothing due, nothing written
    again = await AA.run_due(db, now=mid_next + 900)
    assert again["audited"] == [] and "nothing due" in again["why"]
    # THREE DAYS LATER: D+1 and D+2 are audited, in order, and D+1 does
    # not contain the 23:30 decision
    later = await AA.run_due(db, now=D1 + 2 * H.DAY + 12 * 3600)
    assert [a["report_id"][-10:] for a in later["audited"]] == [
        "2032-03-17", "2032-03-18"]
    nxt = await AA.report(db, "audrey:America/New_York:2032-03-17")
    assert nxt["report"]["xavier"]["decisions"] == 0
    wm = await db.fetchrow("SELECT * FROM audrey_audit_watermarks")
    assert wm["last_completed_day"] == D + dt.timedelta(days=2)


@pg
async def test_a_day_without_justified_change_records_no_change(db):
    got = await AA.run_due(db, now=D1 + 3600)
    assert got["ok"], got
    rep = await AA.report(db, got["audited"][0]["report_id"])
    imp = rep["report"]["improvements"]
    assert imp["verdict"] == AA.NO_CHANGE
    assert imp["proposals"] == []
    assert {c["rule"] for c in imp["checked"]} >= {
        "COLLECTION_THROUGHPUT", "DEREK_CALIBRATION",
        "XAVIER_EVIDENCE_GAPS"}
    assert got["tasks"] == []
    assert await db.fetchval("SELECT count(*) FROM agent_tasks "
                             " WHERE kind='IMPROVEMENT'") == 0
    # EMPTY IS NOT SUCCESS: every empty section names its reason
    assert rep["report"]["derek"]["entries"]["status"] == "EMPTY"
    assert "no entry decision" in rep["report"]["derek"]["entries"]["why"]
    assert rep["report"]["xavier"]["status"] == "EMPTY"


async def _beat(c, *, n, at, offered=8, attempted=3, left=5, recent=4,
                stopped=False, elapsed=20.0, budget=120.0):
    po = {"ran": True, "ok": True, "pass_id": "%spass-%d" % (H.PFX, n),
          "at": at, "elapsed_s": elapsed, "budget_s": budget,
          "stopped_for_deadline": stopped,
          "candidates": {"offered": offered, "attempted": attempted,
                         "not_attempted": {
                             "LIMIT_PER_PASS": left, "PASS_DEADLINE": 0,
                             "FIXTURE_ATTEMPTED_RECENTLY_AND_REFUSED":
                                 recent}}}
    await c.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
        AA.HEARTBEAT_KEY, json.dumps({"pair_observation": po}))
    return await AA.sample_collection(c, now=at + 5)


@pg
async def test_a_collection_bottleneck_files_one_deterministic_task(db):
    for i in range(4):
        got = await _beat(db, n=i, at=D0 + 3600 * (i + 1))
        assert got["sampled"] is True
    # the same pass sampled again is not a second sample
    dup = await _beat(db, n=3, at=D0 + 3600 * 4)
    assert dup["sampled"] is False
    out = await AA.run_due(db, now=D1 + 3600)
    assert out["ok"], out
    tid = "imp:collection_pass_limit:2032-03-16"
    assert tid in [t["task_id"] for t in out["tasks"]]
    task = await IMP.read_task(db, tid)
    assert task["assignee"] == "DEREK" and task["status"] == "OPEN"
    assert task["spec"]["change_class"] == "COLLECTION_PASS_LIMIT"
    ev = task["spec"]["evidence"]["collection"]
    assert ev["passes_with_candidates_left_for_limit"] == 4
    assert ev["candidates_left_for_limit"] == 20
    rep = await AA.report(db, "audrey:America/New_York:2032-03-16")
    assert rep["report"]["improvements"]["verdict"] == "CHANGE_PROPOSED"
    classes = {p["change_class"] for p in
               rep["report"]["improvements"]["proposals"]}
    # 16 skipped as recently refused >= 2 x 3: the refusal memory too
    assert classes == {"COLLECTION_PASS_LIMIT", "COLLECTION_REFUSAL_MEMORY"}
    # A RE-AUDIT OF THE SAME EVIDENCE files nothing new
    n_before = await db.fetchval("SELECT count(*) FROM agent_tasks")
    await AA.audit_day(db, day=D, now=D1 + 7200)
    await AA.run_due(db, now=D1 + 7200)
    assert await db.fetchval("SELECT count(*) FROM agent_tasks") == n_before


# ═════════════════════════════════════════════════════════════════════
# 2 · PROOF 13: FOUR CATEGORIES, NEVER MERGED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_proof13_actual_hypothetical_simulated_and_unknown_are_apart(db):
    settle = D0 + 20 * 3600
    # P1: HELD AND SETTLED AT 1 -- actual executed P&L from the book; its
    # DIRECT_EXIT alternative (not taken) is a known-settlement estimate,
    # its REDUCE was blocked (unevaluable)
    p1 = await H.position(db, 1, fill_at=D0 + 3600, settle_at=settle)
    x1 = await H.decide(db, p1, decided=D0 + 7200, p=0.6)
    # A REFUSED VALUATION WHOSE CONTRACT LATER SETTLED PROFITABLY: cost
    # 0.40, outcome 1. No order existed.
    v_missed = await H.valuation(
        db, fixture=H.PFX + "fx-missed", decided=D0 + 3 * 3600, p=0.55,
        price=0.40, cost=0.40, edge=0.005, outcome=1, outcome_at=settle)
    # A BUY (unplaced: submission is disabled) whose outcome is not known
    v_sim = await H.valuation(
        db, fixture=H.PFX + "fx-sim", decided=D0 + 4 * 3600,
        decision="BUY", p=0.6, price=0.5, cost=0.51, edge=0.09, size=10)
    # A REFUSED VALUATION WITH NO COST RECORDED AND NO OUTCOME
    v_unev = await H.valuation(
        db, fixture=H.PFX + "fx-unev", decided=D0 + 5 * 3600,
        refusals=("QUOTE_UNREADABLE",), p=0.5)
    got = await AA.audit_day(db, day=D, now=D1 + 3600)
    ev = got["report"]["evidence"]
    assert ev["never_merged"] is True
    assert set(AA.CATEGORIES) <= set(ev)
    actual = ev[AA.ACTUAL]
    # ACTUAL IS THE BOOK'S AND ONLY THE BOOK'S
    assert [r.get("xavier_decision_id") for r in actual["rows"]] == [x1]
    assert not any("valuation_id" in r for r in actual["rows"])
    assert actual["rows"][0]["value_usd"] == pytest.approx(5.0)
    assert actual["book_realized_net_usd"] == pytest.approx(4.9)
    ks = ev[AA.KNOWN_SETTLEMENT]
    assert ks["could_have_filled"] == "UNPROVEN"
    missed = [r for r in ks["rows"] if r.get("valuation_id") == v_missed]
    assert missed and missed[0]["refused"] is True
    assert missed[0]["could_have_filled"] == "UNPROVEN"
    assert missed[0]["value_per_contract"] == pytest.approx(0.6)
    exit_est = [r for r in ks["rows"] if r.get("action") == "DIRECT_EXIT"]
    assert exit_est and exit_est[0]["could_have_filled"] == "UNPROVEN"
    # the exit's frozen decision-time evidence travels with it
    fz = exit_est[0]["frozen"]
    assert fz["executable_price"] == 0.6 and fz["depth"] == 25
    assert fz["model_version"] == "synthetic-model-v1"
    # THE UNPLACED BUY WITH NO OUTCOME YET: simulated, assumptions stated
    sim = ev[AA.SIMULATED]
    assert [r["valuation_id"] for r in sim["rows"]
            if "valuation_id" in r] == [v_sim]
    assert "assumptions" in sim["rows"][0]
    assert v_sim not in [r.get("valuation_id") for r in ks["rows"]]
    ent = got["report"]["derek"]["entries"]
    assert ent["simulated"]["count"] == 1
    assert ent["missed_opportunities"]["count"] == 1
    assert ent["missed_opportunities"]["rows"][0]["order_placed"] is False
    assert ent["unevaluable"]["count"] == 1
    un = ev[AA.UNEVALUABLE]
    assert "usd" not in un and "per_contract" not in un
    assert v_unev in [r.get("valuation_id") for r in un["rows"]]
    # the blocked REDUCE is unevaluable by its blocker, not zero
    assert "BLOCKED:NO_EXECUTABLE_DEPTH" in un["reasons"]
    # NO CATEGORY'S TOTAL CONTAINS ANOTHER'S ROWS: the missed opportunity's
    # +0.60 is nowhere in ACTUAL, and ACTUAL's usd is P1's alone
    assert actual["usd"]["fixture_sum"] == pytest.approx(5.0)
    # THE BOOK SECTION: realized (non-provisional) apart from provisional
    bk = got["report"]["book"]
    assert bk["realized"]["net_usd"] == pytest.approx(4.9)
    assert bk["provisional"]["events"] == 0
    assert bk["fees_and_costs"]["fees_usd"] == pytest.approx(0.1)


@pg
async def test_attribution_and_good_decisions_that_lost_and_poor_ones_that_won(db):
    settle = D0 + 20 * 3600
    p1 = await H.position(db, 1, fill_at=D0 + 3600, settle_at=settle)
    await H.decide(db, p1, decided=D0 + 7200, p=0.6)
    # GOOD DECISION, LOSING OUTCOME: HOLD ranked best (value 1.0 vs 0.9),
    # the contract settled at 0
    p2 = await H.position(db, 2, fill_at=D0 + 3600, settle_at=settle,
                          long_price=0.0)
    x2 = await H.decide(db, p2, decided=D0 + 7200, p=0.6)
    # POOR DECISION, WINNING OUTCOME: an exit chosen though HOLD ranked
    # higher; the exit was not sent (submission disabled), the held
    # contract settled at 1
    p3 = await H.position(db, 3, fill_at=D0 + 3600, settle_at=settle)
    x3 = await H.decide(db, p3, decided=D0 + 7200, p=0.6,
                        chosen="DIRECT_EXIT",
                        digest="pd-%s-%d" % (p3["intent_id"],
                                             int(D0 + 7200)))
    got = await AA.audit_day(db, day=D, now=D1 + 3600)
    rows = {r["xavier_decision_id"]: r
            for r in got["report"]["xavier"]["decision_rows"]}
    assert rows[x2]["quality"] == AA.Q_GOOD_LOST
    assert rows[x3]["quality"] == AA.Q_POOR_WON
    pos = {r["position"]: r for r in got["report"]["positions"]["rows"]}
    a1 = pos[p1["group_id"]]["attribution"]
    # handoff mark = the first decision's executable exit cash (5.90);
    # entry cash = -5.00 - 0.10; total = 10 - 5.10
    assert a1["status"] == "ATTRIBUTED"
    assert a1["handoff_mark_basis"] == "EXECUTABLE_LIQUIDATION_VALUE"
    assert a1["derek_credit_usd"] == pytest.approx(0.8)
    assert a1["xavier_credit_usd"] == pytest.approx(4.1)
    tot = got["report"]["positions"]["attribution"]["totals"]
    assert tot["positions"] == 3 and tot["sums_exactly"] is True
    assert tot["derek_credit_usd"] + tot["xavier_credit_usd"] == \
        pytest.approx(tot["total_usd"])
    # the benchmark compares ACTUAL with a KNOWN-SETTLEMENT estimate and
    # says so
    b = got["report"]["xavier"]["benchmarks"]
    assert b["HOLD_TO_SETTLEMENT"]["could_have_filled"] == "UNPROVEN"
    assert b["DECISION_TIME_EXIT"]["status"] == "INSUFFICIENT_EVIDENCE"


# ═════════════════════════════════════════════════════════════════════
# 3 · PROOF 15: FIXTURES, NOT ROWS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_proof15_repeated_decisions_on_one_fixture_are_one_example(db):
    settle = D0 + 22 * 3600
    # FORTY valuations of ONE fixture, and one each of two others
    for i in range(40):
        await H.valuation(db, fixture=H.PFX + "fx-many",
                          decided=D0 + 60 * (i + 1), p=0.9, price=0.5,
                          cost=0.5, edge=0.005, outcome=0, outcome_at=settle)
    await H.valuation(db, fixture=H.PFX + "fx-b", decided=D0 + 7200, p=0.3,
                      price=0.5, cost=0.5, edge=0.005, outcome=1,
                      outcome_at=settle)
    await H.valuation(db, fixture=H.PFX + "fx-c", decided=D0 + 7300, p=0.6,
                      price=0.5, cost=0.5, edge=0.005, outcome=1,
                      outcome_at=settle)
    # THREE Xavier reviews of one position
    p = await H.position(db, 1, fill_at=D0 + 600, settle_at=settle)
    for i in range(3):
        await H.decide(db, p, decided=D0 + 3600 * (i + 1), p=0.6)
    got = await AA.audit_day(db, day=D, now=D1 + 3600)
    ent = got["report"]["derek"]["entries"]
    cal = ent["calibration"]
    assert ent["decisions"] == 42 and ent["fixtures"] == 3
    assert cal["rows"] == 42 and cal["fixtures"] == 3
    assert cal["status"] == "INSUFFICIENT_EVIDENCE"
    assert cal["weighting"] == "ONE_WEIGHT_PER_FIXTURE"
    # one weight per fixture: (0.81 + 0.49 + 0.16) / 3, not dominated by
    # the forty rows of the first fixture
    assert cal["brier"] == pytest.approx((0.81 + 0.49 + 0.16) / 3, abs=1e-6)
    x = got["report"]["xavier"]
    assert x["decisions"] == 3 and x["fixtures"] == 1
    ks = got["report"]["evidence"][AA.KNOWN_SETTLEMENT]
    assert ks["per_contract"]["fixtures"] == 3
    assert ks["per_contract"]["rows"] == 42
    # 42 rows over 3 fixtures is BELOW the floor: no calibration proposal
    checked = {c["rule"]: c for c in
               got["report"]["improvements"]["checked"]}
    assert checked["DEREK_CALIBRATION"]["fixtures"] == 3
    assert "DEREK_ENTRY_THRESHOLD" not in {
        p["change_class"] for p in got["report"]["improvements"][
            "proposals"]}


# ═════════════════════════════════════════════════════════════════════
# 4 · THE WORKSPACE ROUTE
# ═════════════════════════════════════════════════════════════════════

class _Cfg:
    admin_token = "admin-secret-for-the-audrey-test"
    desk_password = "desk"
    operator_password = "operator"
    command_read_password = "desk"
    funded_resolution_key = ""
    funded_resolution_operator = ""


def _client(monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    import contextlib

    import asyncpg
    from fastapi import FastAPI

    from sportsassets.api import agents_audrey as AU
    from sportsassets.api import app as A

    @contextlib.asynccontextmanager
    async def _conn():
        c = await asyncpg.connect(H.DSN)
        try:
            yield c
        finally:
            await c.close()

    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    monkeypatch.setattr(AU, "_connection", _conn)
    app = FastAPI()
    app.include_router(AU.router)
    return starlette.TestClient(app, raise_server_exceptions=False)


@pg
def test_the_workspace_names_every_section_and_discloses_no_key(monkeypatch):
    async def seed():
        async with H.connect() as c:
            await H.ensure_core_tables(c)
            await H.purge(c)
            p = await H.position(c, 1, fill_at=D0 + 3600,
                                 settle_at=D0 + 20 * 3600)
            await H.decide(c, p, decided=D0 + 7200, p=0.6)
            await AA.audit_day(c, day=D, now=D1 + 3600)

    async def clean():
        async with H.connect() as c:
            await H.purge(c)

    asyncio.run(seed())
    try:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-never-shown-12345")
        c = _client(monkeypatch)
        assert c.get("/api/command/agents/audrey").status_code == 401
        hdr = {"X-Admin-Token": _Cfg.admin_token}
        r = c.get("/api/command/agents/audrey", headers=hdr)
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        assert "sk-never-shown-12345" not in r.text
        got = r.json()
        assert got["read_only"] is True
        want = {"status", "versions", "daily_reports", "findings", "outcomes",
                "cohort_quality", "tasks", "candidates", "evaluations",
                "releases", "directives", "conversations", "provider"}
        assert set(got["sections"]) == want
        s = got["sections"]
        for name, sec in s.items():
            assert sec["status"] in ("OK", "EMPTY", "UNAVAILABLE"), name
            if sec["status"] != "OK":
                assert sec["why"], name
        assert s["provider"]["data"] == {
            "configured": True, "variable": "ANTHROPIC_API_KEY",
            "value_disclosed": False}
        assert s["directives"]["status"] == "UNAVAILABLE"
        assert s["directives"]["why"] == "chat stream not merged"
        assert s["conversations"]["why"] == "chat stream not merged"
        assert s["daily_reports"]["status"] == "OK"
        rid = s["daily_reports"]["data"][0]["report_id"]
        assert s["daily_reports"]["evidence"][0]["href"].endswith(rid)
        assert s["candidates"]["status"] == "EMPTY"
        assert s["outcomes"]["data"]["never_merged"] is True
        one = c.get("/api/command/agents/audrey/reports/%s" % rid,
                    headers=hdr)
        assert one.status_code == 200
        assert one.json()["report"]["timezone"] == NY
        assert one.json()["versions"][0]["version"] == 1
        assert c.get("/api/command/agents/audrey/reports/nope",
                     headers=hdr).status_code == 404
        assert c.get("/api/command/agents/audrey/candidates/nope",
                     headers=hdr).status_code == 404
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        r2 = c.get("/api/command/agents/audrey", headers=hdr).json()
        assert r2["sections"]["provider"]["data"]["configured"] is False
    finally:
        asyncio.run(clean())


def test_the_hook_never_raises_without_the_tables():
    class _NoTable:
        async def fetchval(self, *a, **k):
            return None

    got = asyncio.run(AA.run_due(_NoTable(), now=D1))
    assert got["ok"] is False and got["refusal"] == AA.R_SCHEMA
    got = asyncio.run(IMP.run_due(_NoTable(), now=D1))
    assert got["ok"] is False and got["refusal"] == IMP.R_SCHEMA
