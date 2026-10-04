"""CAPITAL-CRITICAL: THE LEARNING LAYER (migration 218) THROUGH ITS REAL
CYCLE AND ITS DATABASE GUARDS.

  §1 A FULL FORWARD TOURNAMENT. Synthetic history (outcomes known before
     registration) -> cycle 1 registers the champion, the trainable
     challengers, the placeholders, the agent variants, the meta-models and
     the experiments -> forward valuations -> cycle 2 captures them and
     records a forecast for EVERY active registration on EVERY opportunity
     (the same events), assigns experiment units -> outcomes -> cycle 3
     scores forward only, decides the fixed-sample verdict, Karen
     challenges, Audrey re-computes independently -> the candidate waits
     for a HUMAN. Each cycle leaves every production table unchanged.
  §2 REGISTRATIONS are immutable, hashed and cannot be back-dated.
  §3 FORECASTS are recorded before outcomes and after registration only.
  §4 THE LADDER cannot be skipped and the human approval cannot be written
     by an agent.
  §5 EXPERIMENTS: no design change after start, no start without Karen, no
     assignment but the seeded one, no reassignment, no deletion, no outcome
     before its assignment.

ALL DATA IS SYNTHETIC (tests/poslearn_fixture.py), in a rolled-back
transaction.
"""
from __future__ import annotations

import random
import time
from contextlib import asynccontextmanager

import asyncpg
import pytest

from sportsassets.poslearn import common as C
from sportsassets.poslearn import experiments as EX
from sportsassets.poslearn import models as M
from sportsassets.poslearn import runner as RUN

try:
    from tests import poslearn_fixture as P
except ImportError:                                             # pragma: no cover
    import poslearn_fixture as P

pg = pytest.mark.skipif(not P.DSN, reason="needs RN1X_TEST_DSN")
FORWARD = 120


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


async def _regs(conn) -> dict:
    return {r["subject_id"]: dict(r) for r in await conn.fetch(
        "SELECT DISTINCT ON (subject_id) subject_id, registration_id, "
        "       status, status_reason, role, kind "
        "  FROM poslearn_registrations ORDER BY subject_id, version DESC")}


# ═════════════════════════════════════════════════════════════════════
# §1 THE FULL FORWARD TOURNAMENT
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_full_forward_tournament_reaches_the_human_gate(monkeypatch):
    monkeypatch.setattr(M, "MIN_SAMPLE", 100)
    monkeypatch.setattr(M, "MIN_SAMPLE_SPORT", 100)
    conn, tr = await P.open_tx()
    try:
        T = time.time()
        await P.seed_history(conn, now=T)
        before = await P.protected_counts(conn)
        c1 = await RUN.run_cycle(conn, now=T, force_training=True)
        assert c1["ran"] and set(c1["components"].values()) == {"OK"}, c1
        assert await P.protected_counts(conn) == before
        regs = await _regs(conn)
        for s in ("M0_PINNAPI_RAW", "M1_PINNAPI_CALIBRATED",
                  "M5_MLB_SPECIFIC", "EDGE_CONFIDENCE", "AVOIDANCE",
                  "DEREK_V1", "DEREK_CHALLENGER_A", "XAVIER_V1",
                  "ALLOCATOR_CHALLENGER_B"):
            assert regs[s]["status"] == "ACTIVE_FORWARD", (s, regs.get(s))
        assert regs["M0_PINNAPI_RAW"]["role"] == "CHAMPION"
        assert regs["M3_PINNAPI_PLUS_SCOUT"]["status"] == "AWAITING_FEATURES"
        assert regs["M4_CROSS_MARKET_CONSENSUS"]["status"] == \
            "AWAITING_SOURCE"
        for v in ("EDDIE_V1", "EDDIE_CHALLENGER_A", "EDDIE_CHALLENGER_B"):
            assert regs[v]["status"] == "AWAITING_INTERFACE"
        # untrainable challengers are NOT registered, with the reason
        assert "M2_PINNAPI_PLUS_MICROSTRUCTURE" not in regs
        assert "M5_NFL_SPECIFIC" not in regs
        reg_run = await conn.fetchval(
            "SELECT summary FROM poslearn_runs WHERE run_id=$1 AND "
            " component='REGISTER'", c1["run_id"])
        reasons = C.jload(reg_run)["not_registered"]
        assert reasons["M2_PINNAPI_PLUS_MICROSTRUCTURE"].startswith(
            "INSUFFICIENT_TRAINING_DATA")
        thr = await conn.fetchval(
            "SELECT document->'decision_rule'->>'threshold_pp' FROM "
            " poslearn_registrations WHERE subject_id='M0_PINNAPI_RAW'")
        assert float(thr) == 0.5            # the active approved version
        exps = {r["experiment_id"]: r["status"] for r in await conn.fetch(
            "SELECT experiment_id, status FROM poslearn_experiments")}
        assert set(exps) == {s["experiment_id"] for s in EX.PLAN}
        assert set(exps.values()) == {"REGISTERED"}
        assert await conn.fetchval(
            "SELECT count(*) FROM poslearn_experiment_reviews WHERE "
            " kind='KAREN_DESIGN_CHALLENGE' AND outcome='NOT_BLOCKED'") == 2

        # ── forward data, after registration
        rng = random.Random(99)
        fwd = []
        for i in range(FORWARD):
            p = (0.03, 0.97)[i % 2]
            q = P.truth(p)
            v = await P.valuation(conn, at=T + 1 + i * 0.5, p=p,
                                  price=round(q - 0.02, 4))
            fwd.append((v, 1 if rng.random() < q else 0))
        before = await P.protected_counts(conn)
        c2 = await RUN.run_cycle(conn, now=T + 100)
        assert set(c2["components"].values()) == {"OK"}, c2
        assert await P.protected_counts(conn) == before
        assert await conn.fetchval(
            "SELECT count(*) FROM poslearn_opportunities") == FORWARD
        # THE SAME EVENTS: every active registration has a row (forecast or
        # named abstention) for every opportunity
        active = await conn.fetchval(
            "SELECT count(*) FROM poslearn_registrations "
            " WHERE status='ACTIVE_FORWARD'")
        per_opp = await conn.fetch(
            "SELECT opportunity_id, count(*) AS n FROM poslearn_forecasts "
            " GROUP BY opportunity_id")
        assert len(per_opp) == FORWARD
        assert {r["n"] for r in per_opp} == {active}
        assert await conn.fetchval(
            "SELECT count(*) FROM poslearn_forecasts WHERE action='ABSTAIN' "
            "   AND (abstain_reason IS NULL OR abstain_reason='')") == 0
        assert await conn.fetchval(
            "SELECT bool_and(applied = false AND label = 'SHADOW') "
            "  FROM poslearn_forecasts")
        exps = {r["experiment_id"]: r["status"] for r in await conn.fetch(
            "SELECT experiment_id, status FROM poslearn_experiments")}
        assert set(exps.values()) == {"RUNNING"}
        assert await conn.fetchval(
            "SELECT count(*) FROM poslearn_experiment_assignments") == \
            2 * FORWARD

        # ── outcomes arrive
        for v, y in fwd:
            await P.resolve(conn, v["id"], y, at=T + 150)
        before = await P.protected_counts(conn)
        c3 = await RUN.run_cycle(conn, now=T + 200)
        assert set(c3["components"].values()) == {"OK"}, c3
        assert await P.protected_counts(conn) == before
        assert await conn.fetchval(
            "SELECT count(*) FROM poslearn_outcomes "
            " WHERE outcome_class='RESOLVED'") == FORWARD
        snap = C.jload(await conn.fetchval(
            "SELECT payload FROM poslearn_snapshots WHERE run_id=$1 AND "
            " component='MODEL_TOURNAMENT'", c3["run_id"]))
        models = {m["subject_id"]: m for m in snap["models"]}
        champ = models["M0_PINNAPI_RAW"]
        assert champ["resolved"] == FORWARD and champ["brier"] is not None
        assert champ["entries"] == FORWARD // 2
        m1 = models["M1_PINNAPI_CALIBRATED"]
        assert m1["versus_champion"]["verdict"] == "CRITERIA_MET", m1
        assert m1["brier"] < champ["brier"]
        assert m1["coverage"] == 1.0
        # the ladder: runner -> Karen -> Audrey, and it STOPS for a human
        steps = [(r["step"], r["actor"], r["outcome"]) for r in
                 await conn.fetch(
                     "SELECT step, actor, outcome FROM "
                     " poslearn_promotion_steps WHERE registration_id=$1 "
                     " ORDER BY step_id",
                     regs["M1_PINNAPI_CALIBRATED"]["registration_id"])]
        assert steps == [("CRITERIA_MET", "POS_LEARN_RUNNER", "CRITERIA_MET"),
                         ("KAREN_CHALLENGE", "KAREN", "NOT_BLOCKED"),
                         ("AUDREY_EVALUATION", "AUDREY", "PASS")], steps
        assert await conn.fetchval(
            "SELECT count(*) FROM poslearn_human_approvals") == 0
        audrey = C.jload(await conn.fetchval(
            "SELECT evidence FROM poslearn_promotion_steps WHERE "
            " registration_id=$1 AND step='AUDREY_EVALUATION'",
            regs["M1_PINNAPI_CALIBRATED"]["registration_id"]))
        assert audrey["agrees_with_runner"] is True
        # the agent tournament scored the same opportunities
        asnap = C.jload(await conn.fetchval(
            "SELECT payload FROM poslearn_snapshots WHERE run_id=$1 AND "
            " component='AGENT_TOURNAMENT'", c3["run_id"]))
        v = {x["subject_id"]: x for x in asnap["variants"]}
        assert v["DEREK_V1"]["metrics"]["n"] == FORWARD
        assert v["DEREK_V1"]["metrics"]["net_economics_usd"] is not None
        assert v["EDDIE_V1"]["status"] == "AWAITING_INTERFACE"
        assert v["XAVIER_CHALLENGER_A"]["metrics"]["path_unobserved"] == \
            FORWARD // 2
        # experiment outcomes follow their assignments
        assert await conn.fetchval(
            "SELECT count(*) FROM poslearn_experiment_outcomes") == \
            2 * FORWARD
        assert await conn.fetchval(
            "SELECT count(*) FROM poslearn_experiment_reviews WHERE "
            " kind='AUDREY_RANDOMIZATION_AUDIT' AND outcome='PASS'") == 2
        # edge confidence and avoidance forecast forward, in shadow
        ec = C.jload(await conn.fetchval(
            "SELECT payload FROM poslearn_snapshots WHERE run_id=$1 AND "
            " component='EDGE_CONFIDENCE'", c3["run_id"]))
        assert ec["status"] == "SHADOW_FORWARD"
        assert ec["forward_evaluation"]["n"] == FORWARD
        assert ec["future_sizing_formula"]["status"] == "NOT_ACTIVE"
        av = C.jload(await conn.fetchval(
            "SELECT payload FROM poslearn_snapshots WHERE run_id=$1 AND "
            " component='AVOIDANCE'", c3["run_id"]))
        assert av["forward_evaluation"]["champion_entries"] == FORWARD // 2

        # ── THE HUMAN GATE: an agent cannot pass it; a person can
        rid = regs["M1_PINNAPI_CALIBRATED"]["registration_id"]
        for who in ("AUDREY", "KAREN", "agent:xavier", "POS_LEARN_RUNNER",
                    "claude-code", "Claude", "migration 218", "SYSTEM",
                    "EDDIE", "eddie-bot", "DEREK_V1"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO poslearn_human_approvals (approval_id, "
                    " registration_id, decision, approver, statement) VALUES "
                    " ($1,$2,'APPROVE_PROMOTION',$3,"
                    " 'I approve this promotion after reading the evidence.')",
                    P.uid("appr"), rid, who)
            await sp.rollback()
        await conn.execute(
            "INSERT INTO poslearn_human_approvals (approval_id, "
            " registration_id, decision, approver, statement) VALUES "
            " ('appr-owner', $1, 'APPROVE_PROMOTION', 'Matt (owner)', "
            " 'Synthetic test approval: the forward evidence was read.')",
            rid)
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "UPDATE poslearn_human_approvals SET approver='Someone' "
                " WHERE approval_id='appr-owner'")
        await sp.rollback()

        # ── the reads
        from sportsassets.api import command_learning_os as API

        async def pool():
            return _Pool(conn)

        monkeypatch.setattr(API, "_pool", pool)
        got = await API.tournament_models()
        assert got["status"] == "OK" and got["label"] == "SHADOW"
        m1v = {m["subject_id"]: m for m in got["data"]["models"]}[
            "M1_PINNAPI_CALIBRATED"]
        assert m1v["promotion_stage"] == \
            "HUMAN_APPROVED_AWAITING_SEPARATE_OWNER_RELEASE"
        assert got["production_effect"] == "NONE"
        for fn in (API.tournament_agents, API.profitability_edge_confidence,
                   API.profitability_avoidance, API.experiments):
            r = await fn()
            assert r["status"] == "OK", (fn.__name__, r.get("why"))
            assert r["authority"] == C.AUTHORITY
        e = await API.experiments()
        assert all(x["interim_estimate"].startswith("NOT_SHOWN")
                   for x in e["data"]["experiments"])
        assert e["assignment_effect"] == "PAPER_SHADOW_ONLY"
    finally:
        await P.close_tx(conn, tr)


@pg
async def test_reads_are_empty_with_a_reason_before_any_run(monkeypatch):
    from sportsassets.api import command_learning_os as API

    conn, tr = await P.open_tx()
    try:
        async def pool():
            return _Pool(conn)

        monkeypatch.setattr(API, "_pool", pool)
        await conn.execute("DELETE FROM poslearn_snapshots")
        for fn in (API.tournament_models, API.tournament_agents,
                   API.profitability_edge_confidence,
                   API.profitability_avoidance, API.experiments):
            got = await fn()
            assert got["status"] == "EMPTY", fn.__name__
            assert got["why"] == "NO_LEARNING_RUN_YET"
            assert got["data"] is None
    finally:
        await P.close_tx(conn, tr)


@pg
async def test_without_history_nothing_is_trained_and_nothing_is_zero():
    conn, tr = await P.open_tx()
    try:
        T = time.time()
        got = await RUN.run_cycle(conn, now=T, force_training=True)
        assert set(got["components"].values()) == {"OK"}, got
        regs = await _regs(conn)
        assert regs["M0_PINNAPI_RAW"]["status"] == "ACTIVE_FORWARD"
        assert "M1_PINNAPI_CALIBRATED" not in regs
        assert "EDGE_CONFIDENCE" not in regs
        ec = C.jload(await conn.fetchval(
            "SELECT payload FROM poslearn_snapshots WHERE run_id=$1 AND "
            " component='EDGE_CONFIDENCE'", got["run_id"]))
        assert ec["status"] == "NOT_REGISTERED"
        assert ec["why"].startswith("INSUFFICIENT_TRAINING_DATA")
        assert ec["forward_evaluation"]["brier_q"] is None
        snap = C.jload(await conn.fetchval(
            "SELECT payload FROM poslearn_snapshots WHERE run_id=$1 AND "
            " component='MODEL_TOURNAMENT'", got["run_id"]))
        champ = [m for m in snap["models"]
                 if m["subject_id"] == "M0_PINNAPI_RAW"][0]
        assert champ["brier"] is None and champ["coverage"] is None
        assert champ["unmeasured"]["coverage"] == \
            "NO_OPPORTUNITY_OFFERED_SINCE_REGISTRATION"
        # a second cycle within TRAIN_EVERY_S does not retrain
        again = await RUN.run_cycle(conn, now=T + 10)
        summ = C.jload(await conn.fetchval(
            "SELECT summary FROM poslearn_runs WHERE run_id=$1 AND "
            " component='REGISTER'", again["run_id"]))
        assert summ["training_rows"] is None
        assert summ["not_registered"]["M1_PINNAPI_CALIBRATED"] == \
            "AWAITING_NEXT_TRAINING_ATTEMPT"
    finally:
        await P.close_tx(conn, tr)


@pg
async def test_a_changed_approved_threshold_registers_a_new_version():
    conn, tr = await P.open_tx()
    try:
        T = time.time()
        await RUN.run_cycle(conn, now=T)
        r1 = await _regs(conn)
        # an owner decision changes the active paper policy (synthetic)
        await conn.execute(
            "INSERT INTO paper_policy_parameter_versions (version_id, "
            " policy_key, version_no, params, params_sha256, source, "
            " approved_by, created_at) VALUES "
            " ('paperparam:PINNACLE_COMPLETED_GAME_PAPER:VTEST', "
            "  'PINNACLE_COMPLETED_GAME_PAPER', 99, "
            "  '{\"min_gross_edge_pp\": 1.0}'::jsonb, 'x', 'OWNER_DECISION', "
            "  'test owner', now())")
        await conn.execute(
            "INSERT INTO paper_policy_parameter_activations (activation_id, "
            " policy_key, kind, version_id, previous_version_id, actor, at) "
            " VALUES ('paperact:VTEST', 'PINNACLE_COMPLETED_GAME_PAPER', "
            " 'ROLLBACK', 'paperparam:PINNACLE_COMPLETED_GAME_PAPER:VTEST', "
            " 'paperparam:PINNACLE_COMPLETED_GAME_PAPER:V1', 'test', now())")
        await conn.execute(
            "UPDATE paper_policy_parameter_heads SET active_version_id = "
            " 'paperparam:PINNACLE_COMPLETED_GAME_PAPER:VTEST', "
            " activation_id = 'paperact:VTEST'")
        await RUN.run_cycle(conn, now=T + 5)
        r2 = await _regs(conn)
        assert r2["M0_PINNAPI_RAW"]["registration_id"] == "M0_PINNAPI_RAW@2"
        old = await conn.fetchrow(
            "SELECT status, status_reason FROM poslearn_registrations "
            " WHERE registration_id = $1",
            r1["M0_PINNAPI_RAW"]["registration_id"])
        assert old["status"] == "RETIRED"
        assert old["status_reason"].startswith(RUN.RETIRE_POLICY)
        thr = await conn.fetchval(
            "SELECT document->'parameters'->>'threshold_pp' FROM "
            " poslearn_registrations WHERE registration_id = "
            " 'DEREK_CHALLENGER_A@2'")
        assert float(thr) == 1.5
    finally:
        await P.close_tx(conn, tr)


# ═════════════════════════════════════════════════════════════════════
# §2 REGISTRATIONS
# ═════════════════════════════════════════════════════════════════════

def _doc(subject="M9_TEST_MODEL", version=1, **over):
    d = {"subject_id": subject, "version": version, "kind": "MODEL",
         "family": "TEST", "role": "CHALLENGER",
         "training_window": "NOT_TRAINED", "features": ["p"],
         "parameters": {"a": 1}, "validation_method": "forward",
         "minimum_sample": 10, "promotion_threshold": {"x": 1},
         "failure_threshold": {"y": 1}, "hypothesis_family": "TEST",
         "family_size": 3}
    d.update(over)
    return d


async def _insert_reg(conn, doc, *, registered_at="now()", sha=None,
                      status="ACTIVE_FORWARD", start=None, end=None):
    text = C.canonical(doc)
    return await conn.execute(
        "INSERT INTO poslearn_registrations (registration_id, kind, "
        " subject_id, version, family, role, canonical_json, document, "
        " sha256, min_sample, family_size, registered_at, status, "
        " training_window_start, training_window_end) VALUES ($1,$2,$3,$4,"
        " $5,$6,$7::text,$7::text::jsonb,$8,$9,$10," + registered_at + ",$11,$12,$13)",
        "%s@%d" % (doc["subject_id"], doc["version"]), doc["kind"],
        doc["subject_id"], doc["version"], doc["family"], doc["role"], text,
        sha or C.sha256_text(text), doc["minimum_sample"],
        doc["family_size"], status, start, end)


@pg
async def test_a_registration_is_immutable_hashed_and_not_backdated():
    import datetime as dt

    conn, tr = await P.open_tx()
    try:
        await _insert_reg(conn, _doc())
        for sql in (
                "UPDATE poslearn_registrations SET min_sample = 1 "
                " WHERE subject_id='M9_TEST_MODEL'",
                "UPDATE poslearn_registrations SET document = "
                " document || '{\"minimum_sample\": 1}' "
                " WHERE subject_id='M9_TEST_MODEL'",
                "UPDATE poslearn_registrations SET registered_at = "
                " registered_at - interval '1 day' "
                " WHERE subject_id='M9_TEST_MODEL'",
                "UPDATE poslearn_registrations SET status = "
                " 'AWAITING_FEATURES' WHERE subject_id='M9_TEST_MODEL'",
                "DELETE FROM poslearn_registrations "
                " WHERE subject_id='M9_TEST_MODEL'"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises((asyncpg.CheckViolationError,
                                asyncpg.RaiseError)):
                await conn.execute(sql)
            await sp.rollback()
        # status may move forward
        await conn.execute(
            "UPDATE poslearn_registrations SET status='RETIRED', "
            " status_reason='test' WHERE subject_id='M9_TEST_MODEL'")
        bad = [
            (_doc("M9_B"), dict(registered_at="now() - interval '1 day'")),
            (_doc("M9_C"), dict(sha="0" * 64)),
            ({k: v for k, v in _doc("M9_D").items()
              if k != "promotion_threshold"}, {}),
            (_doc("M9_E", parameters={"capital_usd": 5}), {}),
            (_doc("M9_F"), dict(status="RETIRED")),
            (_doc("M9_G"), dict(
                start=dt.datetime.now(dt.timezone.utc),
                end=dt.datetime.now(dt.timezone.utc)
                + dt.timedelta(days=1))),
        ]
        for doc, kw in bad:
            sp = conn.transaction()
            await sp.start()
            with pytest.raises((asyncpg.CheckViolationError,
                                asyncpg.RaiseError)):
                await _insert_reg(conn, doc, **kw)
            await sp.rollback()
    finally:
        await P.close_tx(conn, tr)


# ═════════════════════════════════════════════════════════════════════
# §3 FORECASTS
# ═════════════════════════════════════════════════════════════════════

async def _opp(conn, vid, *, at_sql="now()", oid=None):
    oid = oid or "opp:test:%d" % vid
    await conn.execute(
        "INSERT INTO poslearn_opportunities (opportunity_id, source_kind, "
        " source_id, unit, opportunity_at, features_as_of, captured_at, "
        " sport, league, market, live_state, features) VALUES ($1, "
        " 'EXTERNAL_VALUATION', $2, $1, " + at_sql + ", " + at_sql + ", "
        " now() + interval '1 hour', 'baseball', 'UNKNOWN', 'h2h', "
        " 'PREGAME', '{}'::jsonb)", oid, vid)
    return oid


async def _fc(conn, rid, oid, *, predicted="now() + interval '2 hours'",
              applied="false"):
    await conn.execute(
        "INSERT INTO poslearn_forecasts (registration_id, opportunity_id, "
        " probability, action, predicted_at, applied) VALUES ($1,$2,0.5,"
        " 'SCORE'," + predicted + "," + applied + ")", rid, oid)


@pg
async def test_a_forecast_is_before_its_outcome_and_after_registration():
    conn, tr = await P.open_tx()
    try:
        T = time.time()
        await _insert_reg(conn, _doc())
        rid = "M9_TEST_MODEL@1"
        v1 = await P.valuation(conn, at=T + 30, p=0.6, price=0.5)
        o1 = await _opp(conn, v1["id"], at_sql="now() + interval '30 s'")
        await _fc(conn, rid, o1)                       # fine: forward
        refused = []
        # (a) predates the registration
        v2 = await P.valuation(conn, at=T - 3600, p=0.6, price=0.5)
        o2 = await _opp(conn, v2["id"], at_sql="now() - interval '1 hour'")
        refused.append(("predates", lambda: _fc(conn, rid, o2)))
        # (b) the source valuation already knows its outcome
        v3 = await P.valuation(conn, at=T + 40, p=0.6, price=0.5)
        o3 = await _opp(conn, v3["id"], at_sql="now() + interval '40 s'")
        await P.resolve(conn, v3["id"], 1, at=T + 100)
        refused.append(("source known", lambda: _fc(conn, rid, o3)))
        # (c) an outcome is recorded here
        v4 = await P.valuation(conn, at=T + 50, p=0.6, price=0.5)
        o4 = await _opp(conn, v4["id"], at_sql="now() + interval '50 s'")
        await conn.execute(
            "INSERT INTO poslearn_outcomes (opportunity_id, outcome_class, "
            " outcome) VALUES ($1, 'RESOLVED', 1)", o4)
        refused.append(("outcome here", lambda: _fc(conn, rid, o4)))
        # (d) applied = true can never be stored
        v5 = await P.valuation(conn, at=T + 60, p=0.6, price=0.5)
        o5 = await _opp(conn, v5["id"], at_sql="now() + interval '60 s'")
        refused.append(("applied", lambda: _fc(conn, rid, o5,
                                               applied="true")))
        # (e) a placeholder registration forecasts nothing
        await _insert_reg(conn, _doc("M9_WAIT"), status="AWAITING_FEATURES")
        refused.append(("placeholder", lambda: _fc(conn, "M9_WAIT@1", o5)))
        for name, fn in refused:
            sp = conn.transaction()
            await sp.start()
            with pytest.raises((asyncpg.CheckViolationError,
                                asyncpg.RaiseError)):
                await fn()
            await sp.rollback()
        for sql in ("UPDATE poslearn_forecasts SET probability = 0.9",
                    "DELETE FROM poslearn_forecasts",
                    "UPDATE poslearn_opportunities SET p_reference = 0.1",
                    "DELETE FROM poslearn_outcomes"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises((asyncpg.CheckViolationError,
                                asyncpg.RaiseError)):
                await conn.execute(sql)
            await sp.rollback()
        # an opportunity whose source already knows its outcome is refused,
        # and no outcome-bearing feature can be stored
        v6 = await P.valuation(conn, at=T + 70, p=0.6, price=0.5)
        await P.resolve(conn, v6["id"], 0, at=T + 100)
        for sql, args in (
                ("INSERT INTO poslearn_opportunities (opportunity_id, "
                 " source_kind, source_id, unit, opportunity_at, "
                 " features_as_of, captured_at, sport, league, market, "
                 " live_state, features) VALUES ('o6','EXTERNAL_VALUATION',"
                 " $1,'u6',now(),now(),now(),'s','l','m','PREGAME','{}')",
                 (v6["id"],)),
                ("INSERT INTO poslearn_opportunities (opportunity_id, "
                 " source_kind, source_id, unit, opportunity_at, "
                 " features_as_of, captured_at, sport, league, market, "
                 " live_state, features) VALUES ('o7','EXTERNAL_VALUATION',"
                 " -7,'u7',now(),now(),now(),'s','l','m','PREGAME',"
                 " '{\"outcome\": 1}')", ()),
                ("INSERT INTO poslearn_opportunities (opportunity_id, "
                 " source_kind, source_id, unit, opportunity_at, "
                 " features_as_of, captured_at, sport, league, market, "
                 " live_state, features) VALUES ('o8','EXTERNAL_VALUATION',"
                 " -8,'u8',now(),now() + interval '1 s',now(),'s','l','m',"
                 " 'PREGAME','{}')", ())):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises((asyncpg.CheckViolationError,
                                asyncpg.RaiseError)):
                await conn.execute(sql, *args)
            await sp.rollback()
    finally:
        await P.close_tx(conn, tr)


# ═════════════════════════════════════════════════════════════════════
# §4 THE LADDER AND THE HUMAN GATE
# ═════════════════════════════════════════════════════════════════════

async def _step(conn, rid, step, actor, outcome):
    await conn.execute(
        "INSERT INTO poslearn_promotion_steps (registration_id, step, actor,"
        " outcome) VALUES ($1,$2,$3,$4)", rid, step, actor, outcome)


async def _approve(conn, rid, approver="Matt (owner)"):
    await conn.execute(
        "INSERT INTO poslearn_human_approvals (approval_id, registration_id,"
        " decision, approver, statement) VALUES ($1,$2,'APPROVE_PROMOTION',"
        " $3,'A synthetic statement for the test of the gate.')",
        P.uid("a"), rid, approver)


@pg
async def test_the_ladder_cannot_be_skipped_or_finished_by_an_agent():
    conn, tr = await P.open_tx()
    try:
        await _insert_reg(conn, _doc())
        await _insert_reg(conn, _doc("M9_CHAMP", role="CHAMPION"))
        rid = "M9_TEST_MODEL@1"
        attempts = [
            lambda: _approve(conn, rid),                       # no Audrey
            lambda: _step(conn, rid, "KAREN_CHALLENGE", "KAREN",
                          "NOT_BLOCKED"),                      # no criteria
            lambda: _step(conn, rid, "AUDREY_EVALUATION", "AUDREY", "PASS"),
            lambda: _step(conn, rid, "CRITERIA_MET", "DEREK",
                          "CRITERIA_MET"),                     # wrong actor
            lambda: _step(conn, "M9_CHAMP@1", "CRITERIA_MET",
                          "POS_LEARN_RUNNER", "CRITERIA_MET"),  # champion
        ]
        for fn in attempts:
            sp = conn.transaction()
            await sp.start()
            with pytest.raises((asyncpg.CheckViolationError,
                                asyncpg.RaiseError)):
                await fn()
            await sp.rollback()
        await _step(conn, rid, "CRITERIA_MET", "POS_LEARN_RUNNER",
                    "CRITERIA_MET")
        for actor in ("AUDREY", "DEREK", "agent:karen"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await _step(conn, rid, "KAREN_CHALLENGE", actor,
                            "NOT_BLOCKED")
            await sp.rollback()
        await _step(conn, rid, "KAREN_CHALLENGE", "KAREN", "NOT_BLOCKED")
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await _step(conn, rid, "AUDREY_EVALUATION", "KAREN", "PASS")
        await sp.rollback()
        await _step(conn, rid, "AUDREY_EVALUATION", "AUDREY", "PASS")
        for who in ("AUDREY", "KAREN", "XAVIER", "DEREK", "EDDIE", "SCOUT",
                    "agent:audrey", "slack:derek", "system:cron",
                    "claude-opus", "CLAUDE", "migration 218",
                    "POS_LEARN_RUNNER", "pos_learn runner", "  ", "x"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await _approve(conn, rid, who)
            await sp.rollback()
        await _approve(conn, rid, "Karen Smith (owner's delegate)")
        for sql in ("UPDATE poslearn_human_approvals SET decision = "
                    " 'REJECT_PROMOTION'",
                    "DELETE FROM poslearn_human_approvals",
                    "UPDATE poslearn_promotion_steps SET outcome = 'FAIL'",
                    "DELETE FROM poslearn_promotion_steps"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises((asyncpg.CheckViolationError,
                                asyncpg.RaiseError)):
                await conn.execute(sql)
            await sp.rollback()
    finally:
        await P.close_tx(conn, tr)


# ═════════════════════════════════════════════════════════════════════
# §5 EXPERIMENTS
# ═════════════════════════════════════════════════════════════════════

async def _exp(conn, eid, *, started_hours_ago=None):
    import datetime as dt
    from sportsassets.poslearn import store as ST

    now = time.time()
    d = EX.design(dict(EX.PLAN[0], experiment_id=eid), now=now,
                  policy_versions={"CONTROL": "a", "TREATMENT": "b"})
    reg_at = now
    if started_hours_ago is not None:
        reg_at = now - (started_hours_ago + 1) * 3600.0
        d["start_at"] = now - started_hours_ago * 3600.0
    await ST.insert_experiment(conn, d, registered_at=reg_at)
    del dt
    return d


@pg
async def test_an_experiments_design_is_fixed_once_started():
    conn, tr = await P.open_tx()
    try:
        d = await _exp(conn, "EXP_T_FUTURE")
        # BEFORE START a registered design may still be corrected
        await conn.execute(
            "UPDATE poslearn_experiments SET hypothesis = hypothesis || "
            " ' (clarified)' WHERE experiment_id='EXP_T_FUTURE'")
        # no start without a Karen design challenge
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "UPDATE poslearn_experiments SET status='RUNNING' "
                " WHERE experiment_id='EXP_T_FUTURE'")
        await sp.rollback()
        await _exp(conn, "EXP_T_STARTED", started_hours_ago=1)
        for col, val in (("hypothesis", "'changed after start'"),
                         ("primary_metric", "'{\"name\":\"other\","
                                            "\"direction\":\"INCREASE\"}'"),
                         ("secondary_metrics", "'[]'"),
                         ("stopping_rule", "'{}'"),
                         ("failure_criteria", "'{}'"),
                         ("min_sample", "10"), ("seed", "'otherseed123'"),
                         ("arms", "'[]'")):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises((asyncpg.CheckViolationError,
                                asyncpg.RaiseError)):
                await conn.execute(
                    "UPDATE poslearn_experiments SET %s = %s WHERE "
                    " experiment_id='EXP_T_STARTED'" % (col, val))
            await sp.rollback()
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("DELETE FROM poslearn_experiments")
        await sp.rollback()
        del d
    finally:
        await P.close_tx(conn, tr)


@pg
async def test_assignment_is_the_seeded_one_once_and_before_the_outcome():
    from sportsassets.poslearn import store as ST

    conn, tr = await P.open_tx()
    try:
        d = await _exp(conn, "EXP_T_ASSIGN", started_hours_ago=1)
        eid = d["experiment_id"]
        T = time.time()
        v = await P.valuation(conn, at=T, p=0.6, price=0.5)
        oid = await _opp(conn, v["id"], at_sql="now()")
        unit = "opp:test:%d" % v["id"]
        draw = EX.draw(d["seed"], eid, unit)
        arm = EX.arm_for(draw, d["arms"])
        wrong = "TREATMENT" if arm == "CONTROL" else "CONTROL"
        # not RUNNING yet
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await ST.insert_assignment(conn, eid, unit_id=unit,
                                       opportunity_id=oid, arm=arm,
                                       draw=draw, assigned_at=T)
        await sp.rollback()
        await ST.insert_review(conn, eid, kind="KAREN_DESIGN_CHALLENGE",
                               actor="KAREN", outcome="NOT_BLOCKED",
                               findings={})
        await ST.set_experiment_status(conn, eid, "RUNNING", "test")
        for a, dr in ((wrong, draw), (arm, (draw + 0.25) % 1.0)):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await ST.insert_assignment(conn, eid, unit_id=unit,
                                           opportunity_id=oid, arm=a,
                                           draw=dr, assigned_at=T)
            await sp.rollback()
        # an outcome cannot precede its assignment
        sp = conn.transaction()
        await sp.start()
        with pytest.raises((asyncpg.CheckViolationError,
                            asyncpg.ForeignKeyViolationError)):
            await ST.insert_experiment_outcome(conn, eid, unit, {"x": 1},
                                               outcome_at=T)
        await sp.rollback()
        await ST.insert_assignment(conn, eid, unit_id=unit,
                                   opportunity_id=oid, arm=arm, draw=draw,
                                   assigned_at=T)
        for sql in ("UPDATE poslearn_experiment_assignments SET arm = '%s'"
                    % wrong,
                    "DELETE FROM poslearn_experiment_assignments",
                    "UPDATE poslearn_experiment_reviews SET outcome='BLOCKED'"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises((asyncpg.CheckViolationError,
                                asyncpg.RaiseError)):
                await conn.execute(sql)
            await sp.rollback()
        await ST.insert_experiment_outcome(conn, eid, unit, {"x": 1},
                                           outcome_at=T)
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("DELETE FROM poslearn_experiment_outcomes")
        await sp.rollback()
        # a unit whose outcome is already known cannot be assigned
        v2 = await P.valuation(conn, at=T, p=0.6, price=0.5)
        o2 = await _opp(conn, v2["id"], at_sql="now()")
        await P.resolve(conn, v2["id"], 1, at=T + 60)
        u2 = "opp:test:%d" % v2["id"]
        d2 = EX.draw(d["seed"], eid, u2)
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await ST.insert_assignment(conn, eid, unit_id=u2,
                                       opportunity_id=o2,
                                       arm=EX.arm_for(d2, d["arms"]),
                                       draw=d2, assigned_at=T)
        await sp.rollback()
        # the database's draw is the module's draw
        assert await conn.fetchval("SELECT poslearn_draw($1,$2,$3)",
                                   d["seed"], eid, unit) == \
            pytest.approx(draw, abs=1e-15)
    finally:
        await P.close_tx(conn, tr)


@pg
async def test_xavier_variants_replay_the_recorded_plan_over_the_real_path():
    from tests import paper_harness as H

    conn, tr = await P.open_tx()
    try:
        T = time.time()
        await RUN.run_cycle(conn, now=T)
        v = await P.valuation(conn, at=T + 5, p=0.9, price=0.6)
        # the later path: a book, an edge-losing PinnAPI quote, a better book
        await H.observe(conn, v["slug"], T + 10, bids=((0.65, 50),),
                        offers=((0.67, 50),))
        later = await P.valuation(conn, at=T + 12, p=0.5, price=0.6,
                                  slug=v["slug"], event_key=v["event_key"])
        await H.observe(conn, v["slug"], T + 20, bids=((0.80, 50),),
                        offers=((0.82, 50),))
        await RUN.run_cycle(conn, now=T + 30)
        assert await conn.fetchval(
            "SELECT count(*) FROM poslearn_opportunities") == 1
        await P.resolve(conn, v["id"], 0, at=T + 40)
        await P.resolve(conn, later["id"], 0, at=T + 40)
        got = await RUN.run_cycle(conn, now=T + 60)
        snap = C.jload(await conn.fetchval(
            "SELECT payload FROM poslearn_snapshots WHERE run_id=$1 AND "
            " component='AGENT_TOURNAMENT'", got["run_id"]))
        x = {s["subject_id"]: s["metrics"] for s in snap["variants"]}
        assert x["XAVIER_V1"]["net_economics_usd"] == pytest.approx(-0.61)
        assert x["XAVIER_CHALLENGER_A"]["net_economics_usd"] == \
            pytest.approx(0.80 - 0.61)
        assert x["XAVIER_CHALLENGER_B"]["net_economics_usd"] == \
            pytest.approx(0.65 - 0.61)
        assert x["XAVIER_CHALLENGER_A"]["path_unobserved"] == 0
    finally:
        await P.close_tx(conn, tr)
