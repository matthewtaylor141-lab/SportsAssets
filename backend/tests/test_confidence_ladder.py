"""CAPITAL-CRITICAL (R30A, owner audit 2026-10-04): THE CONFIDENCE LADDER
(profitability/confidence_ladder.py, GET /api/command/confidence-ladder).

THE OWNER'S LEVELS, exactly as the audit names them:
  0 Research; 1 INVESTMENT PAPER only; 2 PAPER statistically supported
  (independent / event-clustered CIs); 3 LIVE SHADOW parity with zero
  unexplained logic divergence; 4 tiny live execution validation; 5 actual
  live economic validation after fees, slippage, drawdown, capital-hours;
  6 scale.

THE PROOFS.
  §1 THE SPEC. The level names and order are the owner's; the highest level
     reported is the highest with EVERY lower level met (never a skipped
     level); levels 4-6 are NOT_REACHED while SMALL LIVE is SHADOW, whatever
     the paper and parity evidence says; the legacy execution mirror's
     venue orders are reported beside, never counted.
  §2 LEVEL 2 HAS NO INVENTED SAMPLE COUNT. The power requirement is derived
     from the OBSERVED per-event mean and variance (n_power = ceil(((z_a +
     z_p) * sd / mean)^2)); a strong small sample meets level 2 below
     validation's fixed 30 (reported, not the gate); a weak sample of 40
     does not; fewer than two events or a zero variance is UNAVAILABLE --
     never a pass; no cutover -> no forward window -> NOT_MET.
  §3 SLEEVES. A TRAINING (or BENCHMARK, or UNCLASSIFIED) win or loss cannot
     raise or lower any INVESTMENT level or move its evidence; a TRAINING
     strategy stays at level 0 with SLEEVE_IS_TRAINING; every scope names
     book, sleeve, strategy, policy_version(s) and its confidence scope.
  §4 LEVEL 3 is live_parity's parity gate: the owner's minimum PARITY sample
     (30 decision / 30 management INVESTMENT intents), zero LOGIC_DIVERGENCE;
     its profitability blocker belongs to level 2.
  §5 AUTHORITY. The route is GET only, 401 without a command session; the
     pure module does no I/O; neither module holds a SQL write.
  §6 THE DATABASE. Over positions written by the REAL paper ledger (submit
     -> simulate -> settle; the migration-223 entry trigger classifies them)
     the endpoint answers inside one READ ONLY transaction: the INVESTMENT
     ladder from the INVESTMENT positions, and a large TRAINING win and loss
     change no INVESTMENT level, evidence or blocker.
ALL DATA HERE IS SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import ast
import copy
import math
import pathlib
import re
import time
import uuid
from contextlib import asynccontextmanager

import asyncpg
import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import live_parity as LP
from sportsassets.api import command_confidence_ladder as API
from sportsassets.profitability import common as C
from sportsassets.profitability import confidence_ladder as CL
from sportsassets.profitability import validation as V

try:
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
HOUR = 3600.0
DAY = 86400.0
NOW = 1_800_000_000.0
CUT = NOW - 20 * DAY
CG = "PINNACLE_COMPLETED_GAME_PAPER"
DEREK = "DEREK_ENTRY_POLICY_V2"
EXPLORE = "PINNACLE_EXPLORATION_PAPER"
BENCH = "PINNACLE_ONLY_PAPER_BENCHMARK"
SHADOW = {"small_live_mode": "SHADOW", "small_live_halted": False,
          "live_venue_order_events": 0,
          "canonical_live_executions_by_mode": {"SHADOW": 12},
          "execmirror_enabled": True, "execmirror_stopped": False,
          "execmirror_venue_orders_by_strategy": {CG: {
              "with_venue_order_id": 3, "filled_qty": 4.0}}}


def _pos(i, net, *, strategy=CG, sleeve="INVESTMENT", at=None, fixture=None,
         open_qty=0.0, pv="CG_V2"):
    at = CUT + (i + 1) * HOUR if at is None else at
    return {"position_key": "paperpos:t:g%s:%s:m:LONG" % (strategy, i),
            "group_id": "g-%s-%s" % (strategy, i), "sleeve": sleeve,
            "strategy": strategy, "policy_version": pv,
            "classifier_version": "PAPER_SLEEVE_V1",
            "fixture": fixture or "fx-%s-%s" % (strategy, i),
            "us_market_slug": "m-%s-%s" % (strategy, i),
            "first_fill_at": at, "released_at": at + HOUR,
            "open_qty": open_qty, "realized_pnl_usd": net,
            "unrealized_pnl_usd": None, "marked": False,
            "acquisition_cost_usd": 50.0, "gross_traded_usd": 50.0}


def _parity(n_dec, n_mgt, *, divergences=0, sleeve="INVESTMENT",
            strategy=CG):
    rows = []
    for i in range(n_dec):
        rows.append({"intent_kind": "DECISION", "sleeve": sleeve,
                     "strategy": strategy, "comparison": {},
                     "parity_state": (LP.DIVERGENCE if i < divergences
                                      else LP.MATCHED)})
    for _ in range(n_mgt):
        rows.append({"intent_kind": "MANAGEMENT", "sleeve": sleeve,
                     "strategy": strategy, "comparison": {},
                     "parity_state": LP.MATCHED})
    return LP.readiness(rows, halted=False)


# a strong small sample: 8 independent winning events of varying size
STRONG = [_pos(i, x) for i, x in enumerate(
    (10.0, 12.0, 9.0, 11.0, 10.0, 13.0, 8.0, 11.0))]


def _ladder(positions, *, parity=None, strategies=(CG,), cutover=CUT,
            live=SHADOW, presence=None, parity_why=None):
    parity = _parity(30, 30) if parity is None else parity
    return CL.compute(positions=positions, strategies=strategies,
                      cutover=cutover, parity_overall=parity,
                      parity_by_strategy={s: parity for s in strategies
                                          if C.strategy_sleeve(s)
                                          == C.INVESTMENT},
                      parity_why=parity_why, live=live, now=NOW,
                      presence=presence or {})


# ── §1 the spec ──────────────────────────────────────────────────────

def test_the_levels_are_the_owner_audits_in_order():
    assert CL.LEVELS == (
        (0, "RESEARCH"), (1, "INVESTMENT_PAPER_ONLY"),
        (2, "PAPER_STATISTICALLY_SUPPORTED"), (3, "LIVE_SHADOW_PARITY"),
        (4, "TINY_LIVE_EXECUTION_VALIDATION"), (5, "LIVE_ECONOMIC_VALIDATION"),
        (6, "SCALE"))
    assert set(CL.CRITERIA) == set(range(7))
    out = _ladder(STRONG)
    assert [s["level"] for s in out["levels_spec"]] == list(range(7))
    for scope in [out["overall"]] + list(out["strategies"].values()):
        assert [lv["level"] for lv in scope["levels"]] == list(range(7))


def test_full_paper_and_parity_evidence_stops_at_three_without_live_capital():
    out = _ladder(STRONG)
    ov = out["overall"]
    assert ov["level"] == 3 and ov["level_name"] == "LIVE_SHADOW_PARITY"
    assert ov["next_level"] == 4
    assert ov["blockers_for_next_level"] and all(
        b.startswith(CL.R_NO_LIVE_CAPITAL)
        for b in ov["blockers_for_next_level"])
    by = {lv["level"]: lv for lv in ov["levels"]}
    for lv in (4, 5, 6):
        assert by[lv]["status"] == CL.NOT_REACHED and not by[lv]["met"]
        assert by[lv]["blockers"]
    # the mirror's venue orders are on the record, and not counted
    assert by[4]["evidence"]["execmirror_venue_orders_by_strategy"][CG][
        "with_venue_order_id"] == 3
    assert by[4]["evidence"]["execmirror_role"].startswith(
        "REPORTED, NOT COUNTED")
    assert out["strategies"][CG]["level"] == 3


def test_a_level_is_never_skipped():
    # parity passes, but the paper evidence is weak: the ladder stops at 1
    weak = [_pos(i, x) for i, x in enumerate((10.0, -9.0, 8.0, -7.0))]
    ov = _ladder(weak)["overall"]
    by = {lv["level"]: lv for lv in ov["levels"]}
    assert by[3]["status"] == CL.MET               # its own evidence holds
    assert by[2]["status"] == CL.NOT_MET
    assert ov["level"] == 1 and ov["next_level"] == 2
    assert ov["blockers_for_next_level"] == by[2]["blockers"]
    # nothing at all recorded: not even level 0
    empty = _ladder([], strategies=(CG,))["strategies"][CG]
    assert empty["level"] == -1 and empty["level_name"] == "NONE"
    assert empty["blockers_for_next_level"] == ["NO_RECORD_OF_THIS_STRATEGY"]


def test_a_window_length_is_not_a_record():
    got = _ladder([], presence={CG: {"decisions_recent": 0,
                                     "decisions_recent_days": 30.0,
                                     "parity_rows": 0}})
    assert got["strategies"][CG]["levels"][0]["status"] == CL.NOT_MET
    got = _ladder([], presence={CG: {"decisions_recent": 4,
                                     "decisions_recent_days": 30.0}})
    s = got["strategies"][CG]
    assert s["level"] == 0
    assert "NO_INVESTMENT_PAPER_POSITION" in s["blockers_for_next_level"]


# ── §2 level 2: power from the observed variance ─────────────────────

def test_power_is_derived_from_the_observed_variance():
    xs = [10.0, 12.0, 9.0, 11.0, 10.0, 13.0, 8.0, 11.0]
    pw = CL.power(xs)
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    need = max(2, math.ceil(((CL.Z_ALPHA + CL.Z_POWER) * sd / m) ** 2))
    assert pw["n_power"] == need and pw["status"] == CL.MET
    assert pw["mean"] == pytest.approx(m) and pw["sd"] == pytest.approx(sd)
    # the observed-CI sample: the smallest n whose t lower bound clears 0
    k = pw["n_ci"]
    assert m - V.t_crit_95(k - 1) * sd / math.sqrt(k) > 0
    assert k == 2 or m - V.t_crit_95(k - 2) * sd / math.sqrt(k - 1) <= 0
    # a weak edge needs far more events than it has
    weak = [1.0 + (10.0 if i % 2 else -10.0) for i in range(40)]
    pw = CL.power(weak)
    assert pw["status"] == CL.NOT_MET
    assert pw["n_power"] > 40 and pw["shortfall"] == pw["n_power"] - 40
    assert "BELOW_THE_%d" % pw["n_power"] in pw["why"]
    # never a pass without an estimate
    assert CL.power([])["status"] == CL.UNAVAILABLE
    assert CL.power([5.0])["status"] == CL.UNAVAILABLE
    assert CL.power([5.0, 5.0, 5.0])["status"] == CL.UNAVAILABLE
    assert "ZERO_OBSERVED_VARIANCE" in CL.power([5.0, 5.0])["why"]
    assert CL.power([-1.0, -3.0])["status"] == CL.NOT_MET


def test_level_two_has_no_fixed_sample_count():
    lv2 = CL.level_two(STRONG, cutover=CUT, now=NOW)
    ev = lv2["evidence"]
    # MET on 8 independent events: validation's fixed 30/30 fail, and are
    # reported beside the ladder's result as NOT its gate
    assert lv2["status"] == CL.MET, lv2["blockers"]
    assert ev["validation_fixed_minimums"]["MIN_RESOLVED"]["passed"] is False
    assert ev["validation_fixed_minimums"]["MIN_INDEPENDENT_EVENTS"][
        "passed"] is False
    assert ev["validation_verdict"] == V.POSITIVE_BUT_INSUFFICIENT
    assert "PARITY" in ev["fixed_minimums_role"]
    assert ev["power"]["independent_events"] == 8
    assert all(ev["statistical_checks"].values())
    # forty WEAK events: no fixed count makes it pass
    weak = [_pos(i, 1.0 + (10.0 if i % 2 else -10.0)) for i in range(40)]
    lv2 = CL.level_two(weak, cutover=CUT, now=NOW)
    assert lv2["status"] == CL.NOT_MET
    assert "T_LOWER_BOUND_POSITIVE" in lv2["blockers"]
    assert any(b.startswith("POWER:") for b in lv2["blockers"])


def test_level_two_counts_events_not_positions():
    # eight winning positions on ONE fixture are one outcome: no variance
    one = [_pos(i, 10.0 + i, fixture="fx-same") for i in range(8)]
    lv2 = CL.level_two(one, cutover=CUT, now=NOW)
    assert lv2["evidence"]["power"]["independent_events"] == 1
    assert lv2["status"] == CL.NOT_MET
    assert any("FEWER_THAN_TWO" in b for b in lv2["blockers"])


def test_no_cutover_means_no_forward_window():
    ov = _ladder(STRONG, cutover=None)["overall"]
    by = {lv["level"]: lv for lv in ov["levels"]}
    assert by[2]["status"] == CL.NOT_MET
    assert by[2]["blockers"] == [V.R_NO_CUTOVER]
    assert ov["level"] == 1
    # pre-cutover evidence is never forward evidence
    old = [_pos(i, x, at=CUT - DAY + i * HOUR) for i, x in enumerate(
        (10.0, 12.0, 9.0, 11.0, 10.0, 13.0, 8.0, 11.0))]
    lv2 = CL.level_two(old, cutover=CUT, now=NOW)
    assert lv2["status"] == CL.NOT_MET
    assert lv2["evidence"]["forward_positions"] == 0


# ── §3 sleeves ───────────────────────────────────────────────────────

def _contamination():
    out = []
    for i, x in enumerate((5000.0, -4000.0, 3000.0, 2500.0, 4000.0)):
        out.append(_pos(100 + i, x, strategy=EXPLORE, sleeve="TRAINING",
                        pv="EXPLORE_V1"))
        out.append(_pos(200 + i, x, strategy=BENCH, sleeve="BENCHMARK",
                        pv="BENCH_V1"))
        # an UNCLASSIFIED group of an INVESTMENT strategy: never INVESTMENT
        out.append(_pos(300 + i, x, strategy=CG, sleeve=None))
    return out


def _strip(d):
    d = copy.deepcopy(d)
    d.pop("computed_at", None)
    return d


def test_a_training_win_or_loss_cannot_move_any_investment_level():
    for base in (STRONG, [_pos(i, x) for i, x in enumerate(
            (10.0, -9.0, 8.0, -7.0))]):
        clean = _ladder(base, strategies=(CG, EXPLORE, BENCH))
        dirty = _ladder(base + _contamination(),
                        strategies=(CG, EXPLORE, BENCH))
        # the overall INVESTMENT ladder is byte-for-byte the same
        assert _strip(dirty["overall"]) == _strip(clean["overall"])
        cg_c, cg_d = clean["strategies"][CG], dirty["strategies"][CG]
        for k in ("level", "blockers_for_next_level", "policy_versions"):
            assert cg_c[k] == cg_d[k], k
        assert cg_c["levels"][2] == cg_d["levels"][2]
        # the INVESTMENT strategy's unclassified groups are named, not
        # counted
        assert cg_d["levels"][1]["evidence"]["other_sleeve_positions"] == 5
    # a TRAINING strategy with a winning record stays at level 0
    tr = _ladder(_contamination() + STRONG,
                 strategies=(CG, EXPLORE, BENCH))["strategies"]
    for s, sleeve in ((EXPLORE, "TRAINING"), (BENCH, "BENCHMARK")):
        assert tr[s]["level"] == 0 and tr[s]["sleeve"] == sleeve
        assert tr[s]["confidence_scope"] == C.RESEARCH_SCOPE
        assert any(b.startswith("SLEEVE_IS_%s" % sleeve)
                   for b in tr[s]["blockers_for_next_level"])
        assert tr[s]["levels"][2]["evidence"]["forward_positions"] == 0


def test_unclassified_is_never_investment():
    un = [dict(p, sleeve=None) for p in STRONG]
    s = _ladder(un)["strategies"][CG]
    assert s["level"] == 0
    assert "NO_INVESTMENT_PAPER_POSITION" in s["blockers_for_next_level"]
    assert _ladder(un)["overall"]["level"] == -1


def test_every_scope_names_its_book_sleeve_strategy_and_policy():
    out = _ladder(STRONG + _contamination(), strategies=(CG, EXPLORE, DEREK))
    ov = out["overall"]
    assert (ov["book"], ov["sleeve"], ov["strategy"]) == (
        "PAPER", "INVESTMENT", None)
    assert ov["policy_versions"] == ["CG_V2"] and ov["policy_version"] == \
        "CG_V2"
    assert ov["confidence_scope"] == C.PRODUCTION
    assert ov["classifier_versions"] == ["PAPER_SLEEVE_V1"]
    for s, scope in out["strategies"].items():
        for k in ("book", "sleeve", "strategy", "policy_versions",
                  "policy_version", "policy_version_why",
                  "confidence_scope", "level", "next_level",
                  "blockers_for_next_level"):
            assert k in scope, (s, k)
        assert scope["strategy"] == s
    assert out["strategies"][DEREK]["policy_version"] is None
    assert out["strategies"][DEREK]["policy_version_why"] == \
        "NO_CLASSIFIED_POSITION_NAMES_A_POLICY_VERSION"
    assert out["label"] == "RESEARCH"
    assert out["authority"] == "SHADOW_NO_AUTHORITY"


# ── §4 level 3: live_parity's gate ───────────────────────────────────

def test_level_three_is_the_parity_gate():
    by = {lv["level"]: lv for lv in _ladder(
        STRONG, parity=_parity(29, 30))["overall"]["levels"]}
    assert by[3]["status"] == CL.NOT_MET
    assert by[3]["blockers"] == ["DECISION_SAMPLE:29_OF_30"]
    # one logic divergence anywhere in the sample fails it
    by = {lv["level"]: lv for lv in _ladder(
        STRONG, parity=_parity(31, 30, divergences=1))["overall"]["levels"]}
    assert by[3]["status"] == CL.NOT_MET
    assert "LOGIC_DIVERGENCES_IN_SAMPLE:1" in by[3]["blockers"]
    # TRAINING parity rows are not INVESTMENT parity evidence
    by = {lv["level"]: lv for lv in _ladder(
        STRONG, parity=_parity(40, 40, sleeve="TRAINING"))["overall"][
        "levels"]}
    assert by[3]["status"] == CL.NOT_MET
    # the gate's own profitability blocker is level 2's, not level 3's
    rep = _parity(30, 30)
    assert any(b.startswith("PROFITABILITY") for b in rep["blockers"])
    assert CL.level_three(rep)["status"] == CL.MET
    # an unread parity ledger is UNAVAILABLE with its reason
    lv3 = CL.level_three(None, why_unavailable="MIGRATION_225_NOT_APPLIED")
    assert lv3["status"] == CL.UNAVAILABLE
    assert lv3["blockers"] == ["MIGRATION_225_NOT_APPLIED"]
    no = CL.compute(positions=STRONG, strategies=(CG,), cutover=CUT,
                    parity_overall=None, parity_by_strategy={},
                    parity_why="MIGRATION_225_NOT_APPLIED", live=SHADOW,
                    now=NOW)
    assert no["overall"]["level"] == 2
    assert no["overall"]["blockers_for_next_level"] == [
        "MIGRATION_225_NOT_APPLIED"]
    assert no["strategies"][CG]["blockers_for_next_level"] == [
        "MIGRATION_225_NOT_APPLIED"]


# ── §5 authority ─────────────────────────────────────────────────────

WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+"
                   r"FROM|TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+"
                   r"TABLE)", re.I)
FORBIDDEN = ("execmirror", "kalshi", "pmus", "clob", "executor", "execution",
             "funded", "order", "submit", "venue", "small_live", "canonical")


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
    for rel in ("api/command_confidence_ladder.py",
                "profitability/confidence_ladder.py"):
        tree = ast.parse((PKG / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not WRITE.search(node.value), (rel, node.value[:60])
        for imp in _imports(PKG / rel):
            leaf = imp.rsplit(".", 1)[-1]
            assert not any(f in leaf for f in FORBIDDEN), (rel, imp)
    # the pure module does no I/O at all
    assert _imports(PKG / "profitability" / "confidence_ladder.py") <= {
        "__future__", "annotations", "math", "", "common", "validation"}
    # the route's reads run in a READ ONLY transaction under a timeout
    src = (PKG / "api" / "command_confidence_ladder.py").read_text()
    assert "readonly=not nested" in src
    assert "SET LOCAL statement_timeout" in src


# ── §6 the database ──────────────────────────────────────────────────

FEE = H.flat_fee(0.01)


@asynccontextmanager
async def _txn():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


async def _settled(conn, acct, *, strategy, at, qty, price, outcome):
    """A position written by the REAL paper ledger: an ENTRY order carrying
    its strategy (the migration-223 entry trigger classifies its group),
    filled by the simulator on a recorded book, settled once."""
    slug = "r30a-cl-%s" % uuid.uuid4().hex[:10]
    g = "paper_group_%s" % uuid.uuid4().hex[:12]
    o = dict(H.order(acct, key=uuid.uuid4().hex[:8], slug=slug, qty=qty,
                     limit=price, at=at, group_id=g, fixture="fx-" + slug),
             strategy=strategy)
    got = await L.submit_order(conn, o, fee_fn=FEE, now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3, offers=[(price, qty * 2)],
                    bids=[(round(price - 0.02, 2), qty * 2)])
    sim = await SIM.simulate_order(conn, got["order"]["order_id"],
                                   now=at + 4, fee_fn=FEE)
    assert not sim.get("refusal"), sim
    st = await L.settle(conn, account_id=acct["account_id"], group_id=g,
                        slug=slug, holding_side="LONG",
                        settlement_event_key="ev-" + slug, outcome=outcome,
                        evidence={}, evidence_source="TEST_EVIDENCE",
                        at=at + 2 * HOUR)
    assert st.get("ok", True), st
    return g


async def _cutover(conn, at: float) -> float:
    """The production cutover (recorded once in production by
    live_parity.record_cutover); here inside the rolled-back transaction."""
    have = await conn.fetchval(
        "SELECT extract(epoch FROM cutover_at)::float8 FROM "
        " live_parity_cutover WHERE id = 1")
    if have is not None:
        return float(have)
    iid = await conn.fetchval(
        "INSERT INTO live_parity_hook_installs (process, commit_sha, hooks) "
        " VALUES ('test', repeat('a', 40), ARRAY['X']) RETURNING install_id")
    await conn.execute(
        "INSERT INTO live_parity_cutover (cutover_at, release_sha, api_sha, "
        " workers_sha, migrations, hook_install_id, small_live_mode, "
        " small_live_halted, capital_activated, evidence, recorded_by) "
        " VALUES (to_timestamp($1), repeat('a', 40), repeat('a', 40), "
        " repeat('a', 40), ARRAY['225','226'], $2, 'SHADOW', false, false, "
        " '{}', 'test')", float(at), iid)
    return float(at)


@pg
async def test_the_endpoint_over_the_real_ledger(monkeypatch):
    now = time.time()
    async with _txn() as conn:
        acct = await H.new_account(conn, "r30acl", now=now - 30 * DAY)
        cut = await _cutover(conn, now - 10 * DAY)
        base = cut + 60.0
        assert base < now - 3 * DAY, "the recorded cutover is too recent"
        monkeypatch.setattr(L, "ACCOUNT_ID", acct["account_id"])

        class _Pool:
            @asynccontextmanager
            async def acquire(self):
                yield conn

        async def pool():
            return _Pool()
        monkeypatch.setattr(API, "_pool", pool)
        # five forward INVESTMENT wins and one loss: positive, but the
        # observed variance needs more independent events than six
        for i, (outcome, px) in enumerate(
                (("WON", 0.40), ("WON", 0.45), ("LOST", 0.50),
                 ("WON", 0.55), ("WON", 0.42), ("WON", 0.48))):
            await _settled(conn, acct, strategy=CG, at=base + i * HOUR,
                           qty=50, price=px, outcome=outcome)
        API._CACHE.clear()
        first = await API.confidence_ladder()
        assert first["status"] == "OK", first["why"]
        assert first["label"] == "RESEARCH"
        assert first["summed_across_sleeves"] is False
        d = first["data"]
        ov = d["overall"]
        assert ov["sleeve"] == "INVESTMENT" and ov["book"] == "PAPER"
        by = {lv["level"]: lv for lv in ov["levels"]}
        assert by[0]["status"] == CL.MET and by[1]["status"] == CL.MET
        assert by[1]["evidence"]["investment_positions"] == 6
        assert by[2]["evidence"]["forward_positions"] == 6
        assert by[2]["evidence"]["power"]["independent_events"] == 6
        assert by[2]["status"] == CL.NOT_MET
        assert any(b.startswith("POWER:") for b in by[2]["blockers"])
        assert ov["level"] == 1 and ov["next_level"] == 2
        # no parity row since the cutover: level 3's sample shortfall
        assert any(b.startswith("DECISION_SAMPLE:0_OF_")
                   for b in by[3]["blockers"])
        for lv in (4, 5, 6):
            assert by[lv]["status"] == CL.NOT_REACHED
        assert by[4]["evidence"]["small_live_mode"] == "SHADOW"
        cg = d["strategies"][CG]
        assert cg["level"] == 1
        # these entries carry no decision, so migration 223 recorded no
        # policy version: named as missing, never invented
        assert cg["policy_versions"] == [] and cg["policy_version"] is None
        assert cg["policy_version_why"] == \
            "NO_CLASSIFIED_POSITION_NAMES_A_POLICY_VERSION"
        assert set(C.STRATEGY_SLEEVE) <= set(d["strategies"])

        # ── a TRAINING win and a TRAINING loss, both large ─────────────
        await _settled(conn, acct, strategy=EXPLORE, at=base + 7 * HOUR,
                       qty=900, price=0.30, outcome="WON")
        await _settled(conn, acct, strategy=EXPLORE, at=base + 8 * HOUR,
                       qty=1500, price=0.60, outcome="LOST")
        API._CACHE.clear()
        second = await API.confidence_ladder()
        assert second["status"] == "OK", second["why"]
        d2 = second["data"]
        # NOT ONE INVESTMENT LEVEL, BLOCKER OR PIECE OF EVIDENCE MOVED
        assert _strip(d2["overall"]) == _strip(ov)
        for k in ("level", "blockers_for_next_level", "levels"):
            assert d2["strategies"][CG][k] == cg[k], k
        ex = d2["strategies"][EXPLORE]
        assert ex["level"] == 0 and ex["sleeve"] == "TRAINING"
        assert ex["levels"][0]["evidence"]["positions"] == 2
        assert any(b.startswith("SLEEVE_IS_TRAINING")
                   for b in ex["blockers_for_next_level"])
        # the read wrote nothing and left no transaction state behind
        assert conn.is_in_transaction()
