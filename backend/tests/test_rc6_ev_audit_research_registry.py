"""THE RESEARCH REGISTRY (RC6 lane ev-audit): SAMPLE_INTEGRITY and
MULTIPLE_TESTING had no producer.

Production RC5 (red_team.json, 2026-10-08T20:03Z): SAMPLE_INTEGRITY RED on
NO_EVENT_CLUSTERED_PARTITION_REGISTERED; MULTIPLE_TESTING RED on
NO_PREREGISTERED_CANDIDATE_SET, PBO_NOT_ACCEPTABLE, DSR_NOT_ACCEPTABLE with
`candidates_tested: 0`, `pbo: UNMEASURED`, `dsr: UNMEASURED`; a research read
of red_team_holdout_registry returned 0 rows. Nothing in the repository wrote
that table and nothing computed PBO or DSR, so both controls were RED by
construction, whatever the evidence.

  §1  the partition: keyed-hash event slices, deterministic, disjoint
  §2  PBO by CSCV: ~0.5 on skill-less candidates, ~0 with a real edge
  §3  DSR: the published numerical example (Bailey & Lopez de Prado 2014:
      N = 100, V[SR] = 0.5 annualized, T = 1250, skew -3, kurtosis 10,
      SR 2.5 annualized -> DSR 0.9004)
  §4  the measurement excludes the holdout slice, counts unregistered
      strategies, and never accepts a losing selection
  §5  the controls: registered + measured evidence; an all-losing study stays
      RED on DSR (forward/economic), the partition makes SAMPLE_INTEGRITY
      answerable
  §6  over a migrated database: the runner preregisters ONCE, before the
      interlock reads, append-only, and the next evaluation reads it
"""
from __future__ import annotations

import asyncio
import math
import os
import random
import time

import pytest

from sportsassets.redteam import controls as C
from sportsassets.redteam import research_registry as RR

DSN = os.environ.get("RN1X_TEST_DSN")


# ── §1 the partition ─────────────────────────────────────────────────

def test_the_partition_is_deterministic_disjoint_and_outcome_blind():
    evs = ["fx-%d" % i for i in range(2000)]
    a, b = RR.partition(evs), RR.partition(list(reversed(evs)))
    assert a == b
    sets = [set(a[k]) for k in (RR.TRAIN, RR.TEST, RR.HOLDOUT)]
    assert not (sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
    assert sum(len(s) for s in sets) == 2000
    # the declared 60 / 20 / 20 cut, within sampling error
    assert 0.55 < len(sets[0]) / 2000 < 0.65
    assert 0.15 < len(sets[2]) / 2000 < 0.25
    # a later event lands where the rule puts it, registration or not
    assert RR.slice_of("fx-17") in a and "fx-17" in a[RR.slice_of("fx-17")]


def test_the_plan_names_every_declared_strategy_and_is_hashed():
    from sportsassets import bettor_strategy_lifecycle as LC
    assert RR.PLAN["candidates"] == sorted(LC.KNOWN_STRATEGIES)
    assert len(RR.PLAN_SHA) == 64
    e = RR.preregister_entry(events=["a", "b"], implementation_sha="abc",
                             at=1.0)
    assert e["kind"] == "PREREGISTER"
    assert e["candidate_count"] == len(LC.KNOWN_STRATEGIES)
    assert e["detail"]["plan_sha"] == RR.PLAN_SHA
    assert sorted(sum(e["detail"]["partitions"].values(), [])) == ["a", "b"]


# ── §2 PBO by CSCV ───────────────────────────────────────────────────

def test_pbo_is_about_one_half_for_skill_less_candidates():
    vals = []
    for s in range(120):
        rnd = random.Random(s)
        m = [[rnd.gauss(0, 1) for _ in range(6)] for _ in range(40)]
        vals.append(RR.pbo_cscv(m, blocks=8)["pbo"])
    assert 0.42 < sum(vals) / len(vals) < 0.58


def test_pbo_is_near_zero_when_one_candidate_has_a_real_edge():
    vals = []
    for s in range(60):
        rnd = random.Random(s)
        m = [[rnd.gauss(0, 1) + (0.8 if j == 2 else 0.0) for j in range(6)]
             for _ in range(40)]
        vals.append(RR.pbo_cscv(m, blocks=8)["pbo"])
    assert sum(vals) / len(vals) < 0.08


def test_pbo_refuses_too_few_days_or_candidates():
    assert RR.pbo_cscv([[1.0, 2.0]] * 3, blocks=RR.blocks_for(3))["why"] \
        == RR.U_FEW_DAYS
    assert RR.pbo_cscv([[1.0]] * 10, blocks=RR.blocks_for(10))["why"] \
        == RR.U_FEW_CANDIDATES
    assert RR.blocks_for(7) == 6 and RR.blocks_for(40) == 16
    assert RR.blocks_for(3) == 0


# ── §3 DSR ───────────────────────────────────────────────────────────

def test_dsr_reproduces_the_published_numerical_example():
    # Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio", §3:
    # annualized SR 2.5, V[SR_n] 0.5 annualized, N = 100 trials, T = 1250
    # daily observations, skewness -3, kurtosis 10 -> DSR 0.9004.
    got = RR.deflated_sharpe_from_moments(
        sharpe_hat=2.5 / math.sqrt(250), observations=1250, skew=-3.0,
        kurtosis=10.0, trials=100, sharpe_variance=0.5 / 250)
    assert got["dsr"] == pytest.approx(0.9004, abs=5e-4)
    # SR0 annualized for the same inputs is about 1.79
    assert RR.expected_max_sharpe(trials=100, sharpe_variance=0.5) == \
        pytest.approx(1.79, abs=0.01)


def test_more_trials_deflate_more():
    a = RR.deflated_sharpe_from_moments(sharpe_hat=0.1, observations=500,
                                        skew=0.0, kurtosis=3.0, trials=2,
                                        sharpe_variance=0.002)
    b = RR.deflated_sharpe_from_moments(sharpe_hat=0.1, observations=500,
                                        skew=0.0, kurtosis=3.0, trials=50,
                                        sharpe_variance=0.002)
    assert b["dsr"] < a["dsr"]


# ── §4 the measurement ───────────────────────────────────────────────

def _rows(strategy_pnl: dict, *, days: int, events_per_day: int = 3,
          t0: float = 1_790_000_000.0):
    rows, fx = [], {}
    i = 0
    for d in range(days):
        for s, f in strategy_pnl.items():
            for k in range(events_per_day):
                i += 1
                g = "g%d" % i
                fx[g] = "fx-%s-%d-%d" % (s, d, k)
                rows.append({"identity_claimed": True, "strategy": s,
                             "group_id": g, "decided_at": t0 + d * 86400,
                             "realized_pnl_usd": f(d, k)})
    return rows, fx


REG = {"registered_candidates": list(RR.PLAN["candidates"]),
       "study": RR.STUDY, "plan_sha": RR.PLAN_SHA, "preregistered": 5}


def test_unregistered_study_measures_nothing():
    rows, fx = _rows({"DEREK_ENTRY_POLICY_V2": lambda d, k: 1.0}, days=8)
    m = RR.measure({}, rows, fx)
    assert m["pbo"] is None and m["dsr"] is None
    assert m["pbo_why"] == RR.U_NOT_REGISTERED


def test_the_holdout_slice_is_never_read():
    rows, fx = _rows({"DEREK_ENTRY_POLICY_V2": lambda d, k: -10.0 + k,
                      "PINNACLE_EXPLORATION_PAPER": lambda d, k: -5.0 - k},
                     days=10)
    m = RR.measure(REG, rows, fx)
    held = sum(1 for g, e in fx.items() if RR.slice_of(e) == RR.HOLDOUT)
    assert held > 0 and m["excluded"][RR.HOLDOUT] == held
    assert m["events"] == len(fx) - held


def test_losing_candidates_are_never_accepted():
    rnd = random.Random(7)
    rows, fx = _rows({
        "DEREK_ENTRY_POLICY_V2": lambda d, k: rnd.gauss(-150, 80),
        "PINNACLE_COMPLETED_GAME_PAPER": lambda d, k: rnd.gauss(-20, 30),
        "PINNACLE_EXPLORATION_PAPER": lambda d, k: rnd.gauss(-180, 120)},
        days=12)
    m = RR.measure(REG, rows, fx)
    assert m["candidates_tested"] == 3 and not m["unregistered_tested"]
    assert m["pbo"] is not None and m["dsr"] is not None
    assert m["dsr"] < 0.05 and m["dsr_ok"] is False
    reg = dict(REG, measurement=m, pbo_ok=m["pbo_ok"], dsr_ok=m["dsr_ok"],
               candidates_tested=m["candidates_tested"])
    c = C.multiple_testing(reg)
    assert c["status"] == C.RED and "DSR_NOT_ACCEPTABLE" in c["blockers"]
    assert "NO_PREREGISTERED_CANDIDATE_SET" not in c["blockers"]
    assert c["evidence"]["dsr_value"] == m["dsr"]
    assert c["evidence"]["pbo_value"] == m["pbo"]


def test_a_strategy_the_registration_does_not_name_is_unregistered():
    rows, fx = _rows({"DEREK_ENTRY_POLICY_V2": lambda d, k: 1.0 + k,
                      "SOME_NEW_STRATEGY_V9": lambda d, k: 2.0 + k}, days=8)
    m = RR.measure(REG, rows, fx)
    assert m["unregistered_tested"] == ["SOME_NEW_STRATEGY_V9"]
    c = C.multiple_testing(dict(REG, measurement=m, pbo_ok=True,
                                dsr_ok=True, candidates_tested=2))
    assert "UNREGISTERED_CANDIDATES_TESTED" in c["blockers"]


def test_a_real_edge_passes_both_measurements():
    rnd = random.Random(11)
    rows, fx = _rows({
        "DEREK_ENTRY_POLICY_V2": lambda d, k: rnd.gauss(60, 20),
        "PINNACLE_COMPLETED_GAME_PAPER": lambda d, k: rnd.gauss(-5, 40),
        "PINNACLE_EXPLORATION_PAPER": lambda d, k: rnd.gauss(-8, 40)},
        days=40)
    m = RR.measure(REG, rows, fx)
    assert m["dsr_detail"]["selected"] == "DEREK_ENTRY_POLICY_V2"
    assert m["pbo_ok"] is True and m["dsr_ok"] is True, m
    c = C.multiple_testing(dict(REG, measurement=m, pbo_ok=True,
                                dsr_ok=True, candidates_tested=3))
    assert c["status"] == C.GREEN, c


# ── §5 the sample control reads the registered partition ────────────

def test_sample_integrity_needs_the_registered_partition():
    prob = {"decisions_scored": 570, "independent_events": 267}
    red = C.samples(prob, {})
    assert "NO_EVENT_CLUSTERED_PARTITION_REGISTERED" in red["blockers"]
    parts = RR.partition(["fx-%d" % i for i in range(300)])
    reg = {"partitions": parts, "holdout_opens": 0, "study": RR.STUDY,
           "plan_sha": RR.PLAN_SHA}
    green = C.samples(prob, reg)
    assert green["status"] == C.GREEN, green
    assert green["evidence"]["partition"]["counts"] == {
        k: len(v) for k, v in parts.items()}
    # the gate still refuses leakage, a second holdout opening, few events
    leak = dict(parts, test=parts["test"] + parts["train"][:1])
    assert "TRAIN_TEST_EVENT_LEAKAGE" in C.samples(
        prob, dict(reg, partitions=leak))["blockers"]
    assert "HOLDOUT_OPENED_MORE_THAN_ONCE" in C.samples(
        prob, dict(reg, holdout_opens=2))["blockers"]
    assert "INSUFFICIENT_INDEPENDENT_EVENTS" in C.samples(
        {"decisions_scored": 50, "independent_events": 40}, reg)["blockers"]


# ── §6 over a migrated database ──────────────────────────────────────

def _run(fn):
    """The runner commits its own transactions (as in production); the
    registry is append-only, so the row it writes stays in the scratch
    database -- which is what a second pass must find."""
    import asyncpg

    async def go():
        conn = await asyncpg.connect(DSN)
        try:
            return await fn(conn)
        finally:
            await conn.close()
    return asyncio.run(go())


@pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
def test_the_runner_preregisters_once_before_the_interlock_reads():
    from sportsassets.redteam import readiness as R
    from sportsassets.redteam import runner as RN

    async def fn(conn):
        n0 = await conn.fetchval(
            "SELECT count(*) FROM red_team_holdout_registry WHERE "
            " kind = 'PREREGISTER' AND study = $1", RR.STUDY)
        a = await RN.pass_once(conn, now=time.time())
        b = await RN.pass_once(conn, now=time.time() + 1)
        n2 = await conn.fetchval(
            "SELECT count(*) FROM red_team_holdout_registry WHERE "
            " kind = 'PREREGISTER' AND study = $1", RR.STUDY)
        row = await conn.fetchrow(
            "SELECT candidate_count, detail FROM red_team_holdout_registry "
            " WHERE entry_id = $1", "prereg:%s:%s" % (RR.STUDY,
                                                      RR.PLAN_SHA[:16]))
        res = await R.evaluate(conn, now=time.time())
        refused = []
        for sql in ("UPDATE red_team_holdout_registry SET study = study",
                    "DELETE FROM red_team_holdout_registry"):
            try:
                async with conn.transaction():
                    await conn.execute(sql)
            except Exception as exc:                            # noqa: BLE001
                refused.append(type(exc).__name__)
        return n0, n2, a, b, row, res, refused
    n0, n2, a, b, row, res, refused = _run(fn)
    assert n2 == max(n0, 1)                         # once per plan sha
    assert a["preregistration"]["status"] == "OK"
    assert b["preregistration"]["inserted"] is False
    assert row["candidate_count"] == len(RR.PLAN["candidates"])
    si = res["controls"]["SAMPLE_INTEGRITY"]
    mt = res["controls"]["MULTIPLE_TESTING"]
    assert "NO_EVENT_CLUSTERED_PARTITION_REGISTERED" not in si["blockers"]
    assert si["evidence"]["partition"]["plan_sha"] == RR.PLAN_SHA
    assert "NO_PREREGISTERED_CANDIDATE_SET" not in mt["blockers"]
    assert mt["evidence"]["preregistered"] == len(RR.PLAN["candidates"])
    assert mt["evidence"]["plan_sha"] == RR.PLAN_SHA
    assert len(refused) == 2                        # append-only
    assert res["status"] == "PAPER_SHADOW_ONLY"     # nothing granted


@pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
def test_a_new_plan_sha_is_a_new_registration_appended_once(monkeypatch):
    """The insertion path itself, under a plan sha no earlier run used (the
    scratch database keeps the real plan's row: the table is append-only),
    inside a transaction that is rolled back."""
    import asyncpg
    monkeypatch.setattr(RR, "PLAN_SHA", "f" * 48 + uuid_hex16())

    async def go():
        conn = await asyncpg.connect(DSN)
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                a = await RR.ensure_preregistered(
                    conn, implementation_sha="test", now=time.time())
                b = await RR.ensure_preregistered(
                    conn, implementation_sha="test", now=time.time())
                row = await conn.fetchrow(
                    "SELECT kind, candidate_count, detail FROM "
                    " red_team_holdout_registry WHERE entry_id = $1",
                    a["entry_id"])
                events = await RR.registry_events(conn)
                return a, b, row, events
            finally:
                await tr.rollback()
        finally:
            await conn.close()
    a, b, row, events = asyncio.run(go())
    assert a["inserted"] is True and b["inserted"] is False
    assert row["kind"] == "PREREGISTER"
    import json as _json
    d = row["detail"] if isinstance(row["detail"], dict) else \
        _json.loads(row["detail"])
    assert d["plan_sha"] == RR.PLAN_SHA
    assert sorted(sum(d["partitions"].values(), [])) == sorted(
        {str(e) for e in events})


def uuid_hex16():
    import uuid
    return uuid.uuid4().hex[:16]


def _direct_pbo(matrix, blocks):
    """CSCV written out directly (concatenated rows, statistics.stdev), as
    an independent check of the block-sum implementation."""
    import itertools
    import statistics
    t, n = len(matrix), len(matrix[0])
    base, extra = divmod(t, blocks)
    edges, at = [], 0
    for b in range(blocks):
        size = base + (1 if b < extra else 0)
        edges.append((at, at + size))
        at += size

    def sr(xs):
        m = sum(xs) / len(xs)
        sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
        if sd == 0:
            return 0.0 if m == 0 else math.copysign(math.inf, m)
        # the method's declared tie tolerance (RR.SHARPE_TIE_DECIMALS)
        return round(m / sd, RR.SHARPE_TIE_DECIMALS)

    def ranks(v):
        return [sum(1 for y in v if y < x) + (sum(1 for y in v if y == x)
                                              + 1) / 2.0 for x in v]
    below, combos = 0, list(itertools.combinations(range(blocks),
                                                   blocks // 2))
    for ins in combos:
        ir = [r for b in ins for r in matrix[edges[b][0]:edges[b][1]]]
        orr = [r for b in range(blocks) if b not in ins
               for r in matrix[edges[b][0]:edges[b][1]]]
        isr = [sr([row[j] for row in ir]) for j in range(n)]
        osr = [sr([row[j] for row in orr]) for j in range(n)]
        best = max(range(n), key=lambda j: (isr[j], -j))
        w = ranks(osr)[best] / (n + 1.0)
        below += math.log(w / (1 - w)) <= 0
    return below / len(combos)


def test_the_block_sum_cscv_equals_the_direct_computation():
    for s in range(30):
        rnd = random.Random(s)
        t, n = rnd.randint(4, 24), rnd.randint(2, 6)
        m = [[0.0 if rnd.random() < 0.3 else rnd.gauss(-1, 3)
              for _ in range(n)] for _ in range(t)]
        b = RR.blocks_for(t)
        assert RR.pbo_cscv(m, blocks=b)["pbo"] == pytest.approx(
            _direct_pbo(m, b), abs=1e-12), s


def test_the_measurement_runs_off_the_api_loop():
    import inspect
    from sportsassets.redteam import readiness as R
    assert "asyncio.to_thread(RREG.measure" in inspect.getsource(R.evaluate)
