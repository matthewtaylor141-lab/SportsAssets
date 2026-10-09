"""RC6 api-responsive: the API loop-holders production NAMED, moved off the
event loop -- each at its production size, each proven by the thread that
ran the work and by the loop's own longest gap while it ran.

THE EVIDENCE. The RC5 loop watchdog persists every thread's stack for the
last 20 API loop stalls (ingestion_state['api.loop_stalls']); research-sql
rc6_api-responsive_loop_stalls.sql read it on 2026-10-09 01:31Z (stalls
00:52-01:29Z, 2.3-3.5 s each in full):
  * 4/20  paper_runtime -> paper_derek._context -> research_model ->
          bettor_funded_model.verify_provenance -> _records_sha (json.dumps)
  * 3/20  ext_pinnacle cycle -> derek.after_cycle -> agents/coverage.census
          -> classify_listing (the whole venue catalogue)
  * 2/20  ext_pinnacle cycle -> pinnapi_reactive.register -> match_event ->
          fixture_view / names.absence (one full scan per seed;
          test_rc6_api_responsive_reactive)
  * 1/20  redteam readiness -> completion read -> capital_readiness feeds ->
          capital authority acceptance_view -> blocker_census
  * 4/20  intel calibration load_records (moved off the loop by 21e29bfd)
  * 6/20  inside desk sweeps (test_rc6_api_responsive_desk)
and research-sql rc6_api-responsive_workload_sizes.sql gave the sizes these
tests run at: 72,533 catalogue rows per census, 83,477 refused entries since
the cutover, 28,303 records named by the newest research model.

Where the moved work runs -- one CPU-lane thread for all of it -- is pinned
in test_rc6_api_responsive_cpu_lane.

THE BOUND. A test here fails when the work runs on the loop thread, and
when the loop's longest gap while it runs exceeds LOOP_GAP_BOUND_S. 0.25 s
here is a 2 s production hold at the measured slow-down of the 1-CPU API
(the intel normalisation: 0.42 s on this class of machine, 2.7-3.4 s in the
production ring), i.e. the watchdog's own stall threshold.
"""
from __future__ import annotations

import asyncio
import gc
import json
import os
import random
import threading
import time

import pytest

LOOP_GAP_BOUND_S = 0.25


async def _measured(coro):
    """(result, longest loop gap in s, loop thread id) while `coro` runs.

    A gap is charged to the work only for the time the garbage collector
    did not hold it: a full collection stops every thread wherever the
    work runs, and late in a long test session (a big heap) one alone can
    pass the bound -- seen once in a 12,000-test run, 0.48 s. The
    collector's pauses are timed (gc.callbacks) and taken out of the gaps
    they overlap; the work's own placement is the spy's assertion."""
    gaps = [0.0]
    pauses: list = []
    began = [None]

    def collector(phase, info):
        if phase == "start":
            began[0] = time.perf_counter()
        elif began[0] is not None:
            pauses.append((began[0], time.perf_counter()))
            began[0] = None

    def collected(a, b):
        return sum(max(0.0, min(b, e) - max(a, s)) for s, e in pauses)

    done = asyncio.Event()

    async def tick():
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.002)
            now = time.perf_counter()
            gaps[0] = max(gaps[0], now - last - collected(last, now))
            last = now

    gc.callbacks.append(collector)
    t = asyncio.create_task(tick())
    await asyncio.sleep(0.01)
    try:
        out = await coro
    finally:
        done.set()
        await t
        gc.callbacks.remove(collector)
    return out, gaps[0], threading.get_ident()


BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh(name: str) -> dict:
    """Run this module's scenario `name` in a fresh interpreter and return
    what it measured. A loop-gap bound read late in a long test session
    measures the session -- its heap, the threads earlier tests left
    behind -- as much as the work: 0.56 s and 0.68 s holds appeared in a
    12,000-test run for work a fresh process holds the loop well under the
    bound for. The placement (which thread did the work) is measured in the
    same fresh process."""
    import subprocess
    import sys
    code = ("import json, sys; sys.path[:0] = [%r, %r]; "
            "import test_rc6_api_responsive_offloop as T; "
            "print('RESULT ' + json.dumps(T.%s()))"
            % (BACKEND, os.path.join(BACKEND, "tests"), name))
    proc = subprocess.run([sys.executable, "-c", code], cwd=BACKEND,
                          capture_output=True, text=True, timeout=900,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    got = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert got, proc.stderr[-3000:]
    return json.loads(got[-1][len("RESULT "):])


def _spied(mod, name: str):
    """(thread ids that ran mod.name, undo)."""
    real = getattr(mod, name)
    seen: list = []

    def spy(*a, **k):
        seen.append(threading.get_ident())
        return real(*a, **k)
    setattr(mod, name, spy)
    return seen, (lambda: setattr(mod, name, real))


# ═════════════════════════════════════════════════════════════════════
# 1 · THE RESEARCH MODEL'S PROVENANCE CHECK (paper session, 4 of 20)
# ═════════════════════════════════════════════════════════════════════

N_RESEARCH = 28_303


class _LabelConn:
    """derek_research.labelled_observations' one read, answered with
    `rows`; `records` answers the mismatch diagnostic's registry read."""

    def __init__(self, rows, records=None):
        self.rows, self.records = rows, records
        self.registry_reads = 0

    async def fetch(self, sql, *args):
        assert "derek_research_observations" in sql
        return self.rows

    async def fetchval(self, sql, *args):
        assert "training_provenance->'records'" in sql
        self.registry_reads += 1
        return self.records


def _label_rows(n=N_RESEARCH, seed=7):
    """Research label rows, each vector stored with ITS OWN identity (as
    every production writer stores it: bettor_funded_model.feature_sha; the
    check refuses a vector that does not hash to it). The random draw the
    stand-in identity used to take is still drawn, so every other value is
    the one it always was."""
    from sportsassets import bettor_funded_model as FM
    rng = random.Random(seed)
    out = []
    for i in range(n):
        feats = {"acquisition_price": rng.random(),
                 "payout_is_complement": float(i % 2),
                 "pinnacle_p": rng.random()}
        rng.getrandbits(64)
        out.append({
            "observation_id": "obs:%06d" % i, "fixture": "fx:%d" % (i // 3),
            "features": json.dumps(feats),
            "feature_sha": FM.feature_sha(feats),
            "price": rng.random(), "price_basis": "DISPLAYED",
            "cohort": "C%d" % (i % 4), "pinnacle_p": rng.random(),
            "record_purpose": "CALIBRATION_ONLY",
            "evidence_class": "RESEARCH_OBSERVATION",
            "recorded_epoch": 1.79e9 + i, "decided_epoch": 1.79e9 + i,
            "valuation_id": 1000 + i, "outcome": i % 2,
            "outcome_basis": "VENUE_SETTLEMENT",
            "outcome_epoch": 1.79e9 + 7200 + i})
    return out


def _model(rows, *, sha=None, with_records=False):
    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import derek_research as DR

    lab = asyncio.run(DR.labelled_observations(_LabelConn(rows),
                                               decision_ids=None))
    recs = FM._training_records(lab)
    prov = {"kind": FM.PROVENANCE_RECORDS,
            "decision_ids": [r["observation_id"] for r in rows],
            "records_sha": sha or FM._records_sha(recs),
            "source": FM.SOURCE_RESEARCH_OBSERVATIONS}
    if with_records:
        prov["records"] = recs
    return {"model_id": "derek-research-auto:TEST", "model_key":
            FM.KEY_ENTRY_PAYOUT, "training_provenance": prov}, recs


def test_the_records_sha_is_the_one_shot_digest_record_by_record():
    """THE SAME DIGEST: '[' + each record's dumps joined by ', ' + ']' is
    exactly json.dumps(list); fed incrementally, so the lock can change hands
    between records."""
    import datetime
    import decimal
    import hashlib

    from sportsassets import bettor_funded_model as FM
    rng = random.Random(3)

    def one_shot(r):
        return hashlib.sha256(json.dumps(r, sort_keys=True,
                                         default=str).encode()).hexdigest()
    for n in (0, 1, 2, 7, 300):
        recs = [{"decision_id": "d%d" % i, "label": rng.random(),
                 "push": bool(i % 2), "none": None, "nested": {"b": [1, {
                     "z": "é☃"}], "a": 2},
                 "dec": decimal.Decimal("1.25"),
                 "at": datetime.datetime(2026, 10, 9, 1, 2, 3),
                 "nan": float("nan")} for i in range(n)]
        assert FM._records_sha(recs) == one_shot(recs), n


def scenario_verify() -> dict:
    from sportsassets import bettor_funded_model as FM

    rows = _label_rows()
    model, _recs = _model(rows)
    seen, undo = _spied(FM, "_training_records")
    try:
        async def go():
            return await _measured(FM.verify_provenance(_LabelConn(rows),
                                                        model))
        got, gap, loop_tid = asyncio.run(go())
    finally:
        undo()
    return {"ok": got["ok"], "records": len(got.get("records") or ()),
            "calls": len(seen),
            "on_loop": sum(1 for t in seen if t == loop_tid), "gap": gap}


def test_verify_provenance_rehashes_off_the_loop_at_production_size():
    got = _fresh("scenario_verify")
    assert got["ok"] is True, got
    assert got["records"] == N_RESEARCH
    assert got["calls"] and got["on_loop"] == 0, \
        "the training records were rebuilt on the event loop thread"
    assert got["gap"] < LOOP_GAP_BOUND_S, \
        "the loop was held %.3f s" % got["gap"]


def test_a_mismatch_still_names_the_changed_records_from_the_registry():
    """The model no longer carries its stored records (research_model leaves
    them in the registry); a mismatch reads them there, on that path alone,
    and names the first changed decisions exactly as before."""
    from sportsassets import bettor_funded_model as FM

    rows = _label_rows(400)
    model, recs = _model(rows)
    stored = [dict(r) for r in recs]
    stored[3] = dict(stored[3], label=1.0 - stored[3]["label"])
    stored[9] = dict(stored[9], fixture="moved")
    model["training_provenance"]["records_sha"] = FM._records_sha(stored)
    conn = _LabelConn(rows, records=json.dumps(stored, default=str))
    got = asyncio.run(FM.verify_provenance(conn, model))
    assert got["ok"] is False
    assert got["refusal"] == FM.R_TRAINING_RECORDS_DO_NOT_REPRODUCE
    assert got["changed_records"] == [recs[3]["decision_id"],
                                      recs[9]["decision_id"]]
    assert conn.registry_reads == 1
    # a model that still carries its records never reads the registry
    model2, _ = _model(rows, sha=model["training_provenance"]["records_sha"],
                       with_records=True)
    model2["training_provenance"]["records"] = stored
    conn2 = _LabelConn(rows)
    got2 = asyncio.run(FM.verify_provenance(conn2, model2))
    assert got2["changed_records"] == got["changed_records"]
    assert conn2.registry_reads == 0


DSN = os.environ.get("RN1X_TEST_DSN", "") or os.environ.get("DATABASE_URL", "")
needs_pg = pytest.mark.skipif(not DSN.startswith("postgres"),
                              reason="needs DATABASE_URL (a real Postgres)")


@needs_pg
def test_the_registry_read_leaves_the_stored_records_behind():
    """research_model reads every member of the newest research model but the
    stored training records; the decision ids come back in their stored
    order; the column list is the table's own (a new column fails here)."""
    import asyncpg

    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import paper_derek as PD

    ids = ["obs:%05d" % i for i in range(2500)][::-1]   # stored order kept
    prov = {"kind": FM.PROVENANCE_RECORDS, "decision_ids": ids,
            "records_sha": "f" * 64, "source": FM.SOURCE_RESEARCH_OBSERVATIONS,
            "weighting": "EVENT_BALANCED", "n_events": 3,
            "records": [{"decision_id": i, "label": 1.0} for i in ids]}

    async def go():
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                " WHERE table_name = 'bettor_funded_models' "
                " ORDER BY ordinal_position")]
            await conn.execute(
                "INSERT INTO bettor_funded_models (model_id, model_key, "
                " model_version, state, kernel, estimator, features, params, "
                " fit_through, train_rows, training_provenance, created_at) "
                "VALUES ('rc6-test', $1, 'rc6-test', 'CANDIDATE', 'k', 'e', "
                " ARRAY['acquisition_price'], '{\"w\": [1]}'::jsonb, now(), "
                " 2500, $2::jsonb, now() - interval '1 second')",
                FM.KEY_ENTRY_PAYOUT, json.dumps(prov))
            got = await PD.research_model(conn, at=time.time(), verify=False)
            return cols, got
        finally:
            await tr.rollback()
            await conn.close()

    cols, got = asyncio.run(go())
    assert set(PD.REGISTRY_COLUMNS) == set(cols) - {"training_provenance"}
    assert got["ok"] is True and got["model_id"] == "rc6-test"
    tp = got["model"]["training_provenance"]
    assert tp["decision_ids"] == ids
    assert "records" not in tp
    assert {k: v for k, v in tp.items() if k != "decision_ids"} == \
        {k: v for k, v in prov.items() if k not in ("records",
                                                    "decision_ids")}
    assert got["model"]["params"] == {"w": [1]}


# ═════════════════════════════════════════════════════════════════════
# 2 · THE COVERAGE CENSUS (after every ext_pinnacle cycle, 3 of 20)
# ═════════════════════════════════════════════════════════════════════

N_CATALOGUE = 72_533


class _CensusConn:
    def __init__(self, rows):
        self.rows = rows

    async def fetchval(self, sql, *args):
        if "to_regclass" in sql:
            return True
        return None                      # no collector heartbeat

    async def fetch(self, sql, *args):
        if "FROM us_premap" in sql:
            return self.rows
        return []


def _catalogue(n=N_CATALOGUE, seed=11):
    rng = random.Random(seed)
    types = ["baseball_team_full_game_moneyline",
             "soccer_team_full_time_winner", "basketball_team_spread",
             "football_team_full_game_total", "futures_outright",
             "hockey_team_full_game_moneyline", ""]
    lg = ["mlb", "epl", "nba", "nfl", "cfb", "nhl", "atp"]
    out = []
    for i in range(n):
        k = rng.randrange(len(types))
        ev = "%s-t%d-t%d-2026-10-%02d" % (lg[k], i % 97, i % 89, 10 + i % 9)
        out.append({"market_slug": "aec-%s-%d" % (ev, i), "event_slug": ev,
                    "event_title": "Team %d vs Team %d" % (i % 97, i % 89),
                    "question": "Team %d vs Team %d" % (i % 97, i % 89),
                    "sports_type": types[k], "game_start": None,
                    "updated_at": None})
    return out


def scenario_census() -> dict:
    from sportsassets.agents import coverage as COV
    # the API imports the collector at boot (its lifespan starts the
    # ext_pinnacle loop): in production the census's own `from ..workers
    # import ext_pinnacle_loop` is a lookup, never a first import on the loop
    from sportsassets.workers import ext_pinnacle_loop  # noqa: F401

    rows = _catalogue()
    seen, undo = _spied(COV, "classify_listing")
    try:
        async def go():
            return await _measured(COV.census(_CensusConn(rows),
                                              now=time.time()))
        got, gap, loop_tid = asyncio.run(go())
    finally:
        undo()
    return {"ok": got["ok"], "listed": got["categories"]["listed"],
            "sums": got["categories"]["sums_to_listed"], "calls": len(seen),
            "on_loop": sum(1 for t in seen if t == loop_tid), "gap": gap}


def test_the_census_classifies_the_catalogue_off_the_loop():
    got = _fresh("scenario_census")
    assert got["ok"] is True, got
    assert got["listed"] == N_CATALOGUE and got["sums"] is True
    assert got["calls"] == N_CATALOGUE
    assert got["on_loop"] == 0, \
        "the catalogue was classified on the event loop thread"
    assert got["gap"] < LOOP_GAP_BOUND_S, \
        "the loop was held %.3f s" % got["gap"]


# ═════════════════════════════════════════════════════════════════════
# 3 · THE CAPITAL AUTHORITY'S BLOCKER CENSUS (red-team readiness, 1 of 20)
# ═════════════════════════════════════════════════════════════════════

N_REFUSALS = 83_477


class _RefusalConn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *args):
        assert "paper_entry_refusal_census" in sql
        return self.rows


def _refusals(n=N_REFUSALS, seed=5):
    rng = random.Random(seed)
    refusals = ["EDGE_BELOW_THRESHOLD", "NO_DEPTH_AT_THE_BEST_LEVEL",
                "STALE_BOOK", "PAYOFF_FLOOR_BELOW_COST"]
    return [{"strategy": "S%d" % (i % 3), "stage": "DECISION",
             "refusal": refusals[i % 4],
             "us_market_slug": "aec-mlb-x-%d" % (i % 1465),
             "holding_side": "LONG" if i % 2 else "SHORT",
             "fixture": "fx:%d" % (i % 700), "line": None, "scope": "FULL",
             "gross_edge_pp": rng.random(), "edge_shortfall_pp": rng.random(),
             "expected_fees_usd": 0.01, "slippage_usd": 0.0,
             "adverse_selection_usd": 0.0,
             "total_executable_ev_usd": rng.random() - 0.5,
             "refused_at": None} for i in range(n)]


def scenario_blockers() -> dict:
    from sportsassets import bettor_capital_authority as CA
    from sportsassets.profitability import opportunity_funnel as OF

    rows = _refusals()
    seen, undo = _spied(OF, "opportunity_key")
    try:
        async def go():
            return await _measured(CA.blocker_census(_RefusalConn(rows),
                                                     "paper_acct_main"))
        got, gap, loop_tid = asyncio.run(go())
    finally:
        undo()
    return {"refused": got["refused_entries"],
            "sides": got["distinct_contract_sides"], "calls": len(seen),
            "on_loop": sum(1 for t in seen if t == loop_tid), "gap": gap}


def test_the_blocker_census_tallies_off_the_loop():
    got = _fresh("scenario_blockers")
    assert got["refused"] == N_REFUSALS
    # (slug i % 1465, side i % 2): 1465 is odd, so every pairing occurs
    assert got["sides"] == 2 * 1465
    assert got["calls"] == N_REFUSALS
    assert got["on_loop"] == 0, \
        "the refusal census was tallied on the event loop thread"
    assert got["gap"] < LOOP_GAP_BOUND_S, \
        "the loop was held %.3f s" % got["gap"]
