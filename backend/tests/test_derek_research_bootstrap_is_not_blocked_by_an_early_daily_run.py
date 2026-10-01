"""AN EARLY DAILY RUN NO LONGER BLOCKS THE BOOTSTRAP FIT UNTIL THE NEXT UTC DAY.

THE ORDERING THIS PROVES IS HANDLED (reproduced on a fresh deploy):

  1. The paper path asks first. `paper_derek._context` finds no research
     candidate and calls `ensure_model_attempt` -> `daily_model_run` before
     any collection step ran. The observations table is still empty, so the
     day's run records INSUFFICIENT_LABELLED_FIXTURES (0/0).
  2. The scheduled collection step (`derek.research_step` ->
     `derek_research.observe_cycle`) then backfills stored, settled
     valuations, and one cohort reaches MIN_TRAIN_EVENTS labelled fixtures
     the SAME UTC day.
  3. Before migration 181, every later `daily_model_run` that day returned
     `already_ran` (UNIQUE (run_day), any outcome). Nothing was fitted until
     00:00Z, so the first candidate's prospective window opened up to a day
     late. A first run whose label read FAILED closed the day the same way.

WHAT MUST HOLD AFTER THE CORRECTION (each asserted below):

  * the original INSUFFICIENT / LABELS_UNREADABLE row stays exactly as
    written (append-only: no run or attempt row is ever updated or deleted);
  * the same-day run is a NEW row, made by the SCHEDULED step only
    (`derek.model_run_step`); the paper path never fits -- with a cohort
    ready it returns FIT_DEFERRED_TO_SCHEDULED_STEP and writes nothing;
  * a fit is attempted only when a cohort has reached MIN_TRAIN_EVENTS
    (threshold unchanged, read from the registry). A day still short writes
    nothing new and reports the current shortfall;
  * one DECISIVE run per UTC day: once a day has one, later calls return
    already_ran and nothing is refitted, even after the cohort has grown;
  * the training cutoff is the re-run instant; the evaluation cohort is
    declared at that instant, before the fit, and frozen with the model;
  * backfilled (RETROSPECTIVE_STORED) rows train but count for nothing
    prospective; the schedule never promotes;
  * a re-run at or before the day's last recorded run instant is refused
    by name and writes nothing; `latest_model_run` prefers the decisive row
    of a day;
  * concurrent callers are serialized per UTC day by an advisory lock held
    from the read of the day's rows through the attempt and the run insert,
    in ONE transaction: one attempt, one model, one decisive row, and a run
    insert that fails takes its attempt and model with it;
  * a schema without migration 181 refuses the re-run by name -- proved on
    an ISOLATED copy of the table (own schema, rolled back), never by
    altering the shared table;
  * migration 181 applies over a table that already holds same-day rows, is
    idempotent, and its down script restores UNIQUE (run_day) only when no
    UTC day holds two rows, refusing otherwise -- again on isolated copies,
    asserted unconditionally.

Every row here is SYNTHETIC test data on this file's own far-future UTC days,
driven through the real functions with an explicit clock (`now=` / `at=`).
"""

import asyncio
import datetime as _dt
import json
import pathlib
import time

import pytest

from sportsassets import bettor_funded_model as FM
from sportsassets.agents import derek as D
from sportsassets.agents import derek_research as DR
from sportsassets.agents import paper_derek as PD
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import test_derek_research_observations_break_the_deadlock as T

pg = T.pg
BACKEND = pathlib.Path(__file__).resolve().parents[1]
MIG = BACKEND / "migrations"
UP_181 = MIG / "181_derek_research_model_runs_decisive_run_per_day.sql"
DOWN_181 = (MIG / "rollback" /
            "181_derek_research_model_runs_decisive_run_per_day.down.sql")
TAG = "boot"
NON_DECISIVE = ("INSUFFICIENT_LABELLED_FIXTURES", "LABELS_UNREADABLE")


def _utc_midnight(epoch: float) -> float:
    d = _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).date()
    return _dt.datetime(d.year, d.month, d.day,
                        tzinfo=_dt.timezone.utc).timestamp()


#: THIS FILE'S OWN UTC DAYS: far from any real cycle's (wall-clock) day and
#: from the deadlock file's RUN_DAYS (+400 / +401 days). Computed once; they
#: never appear in a test id.
DAY0 = _utc_midnight(time.time() + 600 * 86400.0)
DAY1 = DAY0 + 86400.0
DAYS = (DR._day_of(DAY0), DR._day_of(DAY1))
#: the startup instants of DAY0 (seconds after 00:00 UTC)
T_FIRST_RUN = DAY0 + 300.0       # the paper path's first daily run
T_BACKFILL = DAY0 + 1200.0       # the scheduled collection step
T_RERUN = DAY0 + 1500.0          # the scheduled same-day daily run
T_LATER = DAY0 + 1800.0          # a later call the same day


def _auto_id(cohort, day) -> str:
    return "%s:%s:%s" % (DR.AUTO_MODEL_PREFIX, cohort, day)


async def _ensure_schema(conn):
    await T._ensure_schema(conn)
    # idempotent; a migrated database already has it
    if UP_181.exists():
        await conn.execute(UP_181.read_text())


async def _purge(conn):
    """Everything this file created, and nothing else (the deadlock file's
    purge covers the SYNTHETIC valuations, observations, attempts and entry
    models; this file's run days are added here)."""
    await T._purge(conn)
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM derek_research_model_runs "
                           " WHERE run_day = ANY($1::date[])", list(DAYS))


async def _stored_settled(conn, *, tag, rule, start, per=T.PER_PRICE,
                          settle_after_s=3600.0) -> list:
    """One STORED calibration-only valuation per fixture, settled by the
    production join's own statement `settle_after_s` after its decision.
    No observation exists for any of them yet (the backfill makes those).
    Returns [(valuation_id, cid, won, decided_at)]."""
    made = []
    for n, (price, won) in enumerate(T._outcomes(rule, per)):
        cid = "%s%s-%s-%03d" % (T.SYN, TAG, tag, n)
        decided = start + n
        vid = await T._calibration_row(conn, cid=cid, price=price,
                                       p_pin=min(0.97, price + 0.15),
                                       decided_at=decided)
        await conn.execute(loop.JOIN_RESOLVED_SQL, vid, int(won),
                           decided + settle_after_s, loop.B_SETTLEMENT_PRICE,
                           loop.SIDE_LONG, "1" if won else "0")
        made.append((vid, cid, won, decided))
    return made


async def _start_backfill_above(conn, made) -> None:
    """Start the one-time backfill's walk just above this test's rows."""
    await DR._save_state(conn, {"cursor_below_id":
                                max(v for v, *_ in made) + 1})


async def _day_rows(conn, day) -> list:
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM derek_research_model_runs WHERE run_day = $1 "
        " ORDER BY ran_at, recorded_at, run_id", day)]


async def _have(conn, at) -> dict:
    lab = await DR.labelled_observations(conn, through=at,
                                         outcomes_through=at)
    assert lab["ok"], lab
    return {c: b["have_labelled_fixtures"]
            for c, b in DR.labelled_counts(lab)["by_cohort"].items()}


async def _n_auto_attempts(conn) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM derek_research_model_attempts "
        " WHERE attempt_id LIKE $1", DR.AUTO_MODEL_PREFIX + ":%")


async def _n_entry_models(conn) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM bettor_funded_models WHERE model_key=$1",
        FM.KEY_ENTRY_PAYOUT)


async def _setup(conn, tag, rule=None, per=T.PER_PRICE):
    """Purge, check the premise (no cohort already at the minimum in this
    database), and store this test's settled valuations. Returns (made,
    prior backfill state)."""
    await _ensure_schema(conn)
    await _purge(conn)
    prior = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1",
        DR.BACKFILL_STATE_KEY)
    base = await _have(conn, T_RERUN)
    assert all(n < FM.MIN_TRAIN_EVENTS for n in base.values()), (
        "premise: this database already holds a cohort at the minimum "
        "before the test's rows exist: %s" % base)
    made = await _stored_settled(conn, tag=tag,
                                 rule=rule or T.RULE_INFORMATIVE, per=per,
                                 start=DAY0 - 3 * 86400.0)
    await _start_backfill_above(conn, made)
    return made, prior


async def _first_run_is_insufficient(conn) -> dict:
    """The startup ordering: the paper path's model attempt runs the day's
    first daily run before any collection step."""
    first = await PD.ensure_model_attempt(conn, at=T_FIRST_RUN)
    assert first["ran"] is True, first
    assert first["outcome"] == DR.RUN_INSUFFICIENT, first
    assert first["run_id"] == "derek-research-run:%s" % DAYS[0]
    disp = first["counts"]["by_cohort"][FM.COHORT_DISPLAYED]
    assert disp["have_labelled_fixtures"] < FM.MIN_TRAIN_EVENTS, disp
    assert disp["need"] == FM.MIN_TRAIN_EVENTS == 40
    return first


async def _backfill_reaches_the_minimum(conn, made, *, at=T_BACKFILL):
    """The scheduled collection step: the live window, then ONE bounded
    backfill step that turns the stored, settled valuations into
    RETROSPECTIVE_STORED observations."""
    res = await D.research_step(conn, now=at)
    bf = res["backfill"]
    assert bf["ok"] is True, bf
    assert bf["recorded"] >= len(made), bf
    got = {r["valuation_id"]: dict(r) for r in await conn.fetch(
        "SELECT valuation_id, collection_mode, evidence_class, cohort "
        "  FROM derek_research_observations "
        " WHERE valuation_id = ANY($1::bigint[])", [v for v, *_ in made])}
    assert len(got) == len(made)
    assert {(o["collection_mode"], o["evidence_class"], o["cohort"])
            for o in got.values()} == {(DR.MODE_BACKFILL,
                                        FM.EVIDENCE_CLASS_STORED,
                                        FM.COHORT_DISPLAYED)}
    have = await _have(conn, T_RERUN)
    assert have[FM.COHORT_DISPLAYED] >= FM.MIN_TRAIN_EVENTS, have
    return bf


async def _finish(conn, prior):
    if prior is not T._UNSET:
        await T._restore_backfill(conn, prior)
    await _purge(conn)


# ═════════════════════════════════════════════════════════════════════════
# 1 · THE REPRODUCTION: A SAME-DAY BACKFILL THAT REACHES THE MINIMUM IS FITTED
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_a_same_day_backfill_reaching_the_minimum_is_fitted_that_day():
    import asyncpg
    conn = await T._connect()
    prior = T._UNSET
    try:
        made, prior = await _setup(conn, "train")

        # ── 1 · THE PAPER PATH FIRST: INSUFFICIENT, BEFORE ANY COLLECTION ─
        first = await _first_run_is_insufficient(conn)
        original = await _day_rows(conn, DAYS[0])
        assert [r["run_id"] for r in original] == [first["run_id"]]
        assert original[0]["attempted_model_ids"] == []

        # ── 2 · THE SCHEDULED STEP BACKFILLS ENOUGH, THE SAME DAY ───────
        await _backfill_reaches_the_minimum(conn, made)

        # ── 3 · THE SCHEDULED SAME-DAY RUN MUST ATTEMPT THE FIT ─────────
        run = await D.model_run_step(conn, now=T_RERUN)
        assert run.get("already_ran") is not True, (
            "the day's early INSUFFICIENT run still blocks the fit: %s"
            % json.dumps({k: run.get(k) for k in (
                "run_day", "ran", "already_ran", "run_id", "outcome")}))
        assert run["ran"] is True, run
        assert run["outcome"] == DR.RUN_FITTED, run
        assert run["promoted"] is False
        mid = _auto_id(FM.COHORT_DISPLAYED, DAYS[0])
        f = run["fitted"][FM.COHORT_DISPLAYED]
        assert f["refit"] is True and f["ok"] is True, f
        assert f["model_id"] == mid
        assert f["fit_through_epoch_s"] == T_RERUN
        assert run["attempted_model_ids"] == [mid]
        # only the cohort that reached the minimum: the executable cohort is
        # neither fitted nor pooled in
        assert FM.COHORT_EXECUTABLE not in run["fitted"]
        # A NEW ROW; THE ORIGINAL IS UNCHANGED
        assert run["run_id"] == "derek-research-run:%s:2" % DAYS[0]
        rows = await _day_rows(conn, DAYS[0])
        assert [r["run_id"] for r in rows] == [first["run_id"],
                                               run["run_id"]]
        assert rows[0] == original[0]
        assert rows[1]["outcome"] == DR.RUN_FITTED
        assert list(rows[1]["attempted_model_ids"]) == [mid]
        assert rows[1]["promoted"] is False
        detail = json.loads(rows[1]["detail"])
        assert detail["same_day_rerun"]["after_run_ids"] == [first["run_id"]]

        # ── THE ATTEMPT: RECORDED, CUT OFF AT THE RE-RUN INSTANT ────────
        a = await conn.fetchrow(
            "SELECT *, extract(epoch FROM attempted_at) AS at_s, "
            "       extract(epoch FROM fit_through) AS ft_s "
            "  FROM derek_research_model_attempts WHERE attempt_id=$1", mid)
        assert a["outcome"] == DR.ATTEMPT_REGISTERED, dict(a)
        assert a["run_id"] == run["run_id"]
        assert float(a["at_s"]) == pytest.approx(T_RERUN, abs=1e-3)
        assert float(a["ft_s"]) == pytest.approx(T_RERUN, abs=1e-3)
        assert a["train_fixtures"] >= len(made) >= FM.MIN_TRAIN_EVENTS
        assert a["records_sha"] and a["attempted_set_sha"]
        ecoh = json.loads(a["evaluation_cohort"])
        assert ecoh["declared_before_fitting"] is True
        assert ecoh["declared_at_epoch_s"] == T_RERUN
        assert ecoh["cohorts"] == [FM.COHORT_DISPLAYED]
        assert ecoh["evidence_class"] == FM.EVIDENCE_CLASS_LIVE
        # ── THE MODEL: A CANDIDATE, FROZEN WITH ITS DECLARATION ─────────
        m = await conn.fetchrow(
            "SELECT state, approved_by, training_provenance, "
            "       extract(epoch FROM fit_through) AS ft_s "
            "  FROM bettor_funded_models WHERE model_id=$1", mid)
        assert m["state"] == FM.STATE_CANDIDATE and m["approved_by"] is None
        assert float(m["ft_s"]) == pytest.approx(T_RERUN, abs=1e-3)
        prov = json.loads(m["training_provenance"])
        assert prov["training_cutoff_epoch_s"] == pytest.approx(T_RERUN)
        assert prov["label_cutoff_epoch_s"] == pytest.approx(T_RERUN)
        assert prov["outcomes_available_through_epoch_s"] <= T_RERUN
        assert prov["windows"]["evaluation_cohort"] == ecoh
        pop = prov["training_population"]
        assert pop["training_cohorts"] == [FM.COHORT_DISPLAYED]
        # backfilled rows TRAIN...
        assert set(pop["evidence_class_mix"]) == {FM.EVIDENCE_CLASS_STORED}
        # ...and count for nothing prospective
        e = json.loads(rows[1]["evaluations"])[mid]
        assert e["prospective_fixtures"] == 0
        assert e["ok"] is False and e["refusal"] == FM.R_TOO_FEW_LABELS
        assert e["promoted"] is False
        # ── PAPER CAN USE IT; NOTHING IS PROMOTED ───────────────────────
        rm = await PD.research_model(conn, at=T_RERUN + 1.0)
        assert rm["ok"] is True, rm
        assert rm["model_id"] == mid
        assert rm["approval_status"] == FM.STATE_CANDIDATE
        assert rm["provenance_verified"] is True and rm["promoted"] is False
        assert (await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT))[
            "refusal"] == FM.R_NO_APPROVED_MODEL
        # ── APPEND-ONLY: RUN AND ATTEMPT ROWS ───────────────────────────
        for rid in (first["run_id"], run["run_id"]):
            with pytest.raises(asyncpg.PostgresError, match="append-only"):
                await conn.execute("UPDATE derek_research_model_runs SET "
                                   " outcome = outcome WHERE run_id=$1", rid)
            with pytest.raises(asyncpg.PostgresError, match="append-only"):
                await conn.execute("DELETE FROM derek_research_model_runs "
                                   " WHERE run_id=$1", rid)
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute("DELETE FROM derek_research_model_attempts "
                               " WHERE attempt_id=$1", mid)

        # ── 4 · ONE DECISIVE RUN PER DAY: NO REFIT AFTER IT ─────────────
        # The cohort grows past CANDIDATE_REFIT_MIN_NEW_EVENTS the same day
        # (live-window rows settled within a minute); nothing is refitted.
        grow = await _stored_settled(
            conn, tag="grow", rule={0.50: 3, 0.70: 4}, per=6,
            start=T_LATER - 200.0, settle_after_s=60.0)
        res = await D.research_step(conn, now=T_LATER - 50.0)
        assert res["recorded"] >= len(grow), res
        have = await _have(conn, T_LATER)
        assert have[FM.COHORT_DISPLAYED] - a["train_fixtures"] >= \
            FM.CANDIDATE_REFIT_MIN_NEW_EVENTS, have
        n_att, n_mod = await _n_auto_attempts(conn), await _n_entry_models(
            conn)
        later = await DR.daily_model_run(conn, now=T_LATER)
        assert later["already_ran"] is True and later["ran"] is False, later
        assert later["run_id"] == run["run_id"]
        assert later["outcome"] == DR.RUN_FITTED
        step = await D.model_run_step(conn, now=T_LATER + 60.0)
        assert step["already_ran"] is True and step["run_id"] == run["run_id"]
        assert len(await _day_rows(conn, DAYS[0])) == 2
        assert await _n_auto_attempts(conn) == n_att
        assert await _n_entry_models(conn) == n_mod
        # the workspace's latest run is the day's decisive row
        lr = await DR.latest_model_run(conn)
        assert lr["run_id"] == run["run_id"], lr

        # ── 5 · THE NEXT UTC DAY IS UNCHANGED: ONE RUN, THE REFIT RULE ──
        nxt = await D.model_run_step(conn, now=DAY1 + 300.0)
        assert nxt["ran"] is True, nxt
        assert nxt["run_id"] == "derek-research-run:%s" % DAYS[1]
        assert nxt["outcome"] == DR.RUN_FITTED, nxt
        assert nxt["attempted_model_ids"] == [
            _auto_id(FM.COHORT_DISPLAYED, DAYS[1])]
        assert len(await _day_rows(conn, DAYS[1])) == 1
        again = await DR.daily_model_run(conn, now=DAY1 + 400.0)
        assert again["already_ran"] is True
    finally:
        await _finish(conn, prior)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 2 · STILL SHORT: NOTHING NEW IS WRITTEN, THE EXACT SHORTFALL IS REPORTED
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_an_insufficient_day_still_short_writes_nothing_new():
    conn = await T._connect()
    prior = T._UNSET
    try:
        few, prior = await _setup(conn, "few",
                                  rule={0.30: 2, 0.50: 3, 0.70: 4}, per=4)
        first = await _first_run_is_insufficient(conn)
        before = await _have(conn, T_RERUN)
        res = await D.research_step(conn, now=T_BACKFILL)
        assert res["backfill"]["recorded"] >= len(few), res
        have = await _have(conn, T_RERUN)
        assert have[FM.COHORT_DISPLAYED] >= before[FM.COHORT_DISPLAYED] + \
            len(few)
        assert have[FM.COHORT_DISPLAYED] < FM.MIN_TRAIN_EVENTS, have
        run = await D.model_run_step(conn, now=T_RERUN)
        assert run["already_ran"] is True and run["ran"] is False, run
        assert run["run_id"] == first["run_id"]
        assert run["outcome"] == DR.RUN_INSUFFICIENT
        # the shortfall as of this call, not only as of the first run
        now_c = run["counts_now"]["by_cohort"][FM.COHORT_DISPLAYED]
        assert now_c["have_labelled_fixtures"] == have[FM.COHORT_DISPLAYED]
        assert now_c["shortfall"] == \
            FM.MIN_TRAIN_EVENTS - have[FM.COHORT_DISPLAYED]
        assert run.get("attempted_model_ids") in (None, [])
        assert len(await _day_rows(conn, DAYS[0])) == 1
        assert await _n_auto_attempts(conn) == 0
        assert await _n_entry_models(conn) == 0
    finally:
        await _finish(conn, prior)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 3 · A FIRST RUN WHOSE LABEL READ FAILED DOES NOT CLOSE THE DAY (D3)
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_a_first_run_whose_labels_were_unreadable_does_not_close_the_day(
        monkeypatch):
    import asyncpg
    conn = await T._connect()
    prior = T._UNSET
    try:
        made, prior = await _setup(conn, "unread")
        # THE LABEL READ FAILS IN THE DATABASE (a real SQL error, raised
        # inside the run's transaction -- not a stub that returns ok=False)
        real_sql = DR.LABEL_SQL
        monkeypatch.setattr(DR, "LABEL_SQL", real_sql.replace(
            "o.feature_sha,", "o.no_such_column_in_this_test,"))
        first = await D.model_run_step(conn, now=T_FIRST_RUN)
        assert first["ran"] is True, first
        assert first["outcome"] == DR.RUN_LABELS_UNREADABLE, first
        assert "no_such_column" in json.dumps(first["counts"])
        original = await _day_rows(conn, DAYS[0])
        assert [r["outcome"] for r in original] == [DR.RUN_LABELS_UNREADABLE]
        # still unreadable later the same day: NOTHING new is written
        again = await D.model_run_step(conn, now=T_FIRST_RUN + 60.0)
        assert again["ran"] is False and again["already_ran"] is True, again
        assert again["rerun_refusal"] == DR.R_RERUN_LABELS_UNREADABLE
        assert len(await _day_rows(conn, DAYS[0])) == 1
        # the label read recovers; the backfill brings the cohort to 40
        monkeypatch.setattr(DR, "LABEL_SQL", real_sql)
        await _backfill_reaches_the_minimum(conn, made)
        run = await D.model_run_step(conn, now=T_RERUN)
        assert run.get("already_ran") is not True, run
        assert run["ran"] is True and run["outcome"] == DR.RUN_FITTED, run
        mid = _auto_id(FM.COHORT_DISPLAYED, DAYS[0])
        assert run["attempted_model_ids"] == [mid]
        assert run["fitted"][FM.COHORT_DISPLAYED]["fit_through_epoch_s"] == \
            T_RERUN
        rows = await _day_rows(conn, DAYS[0])
        assert [r["outcome"] for r in rows] == [DR.RUN_LABELS_UNREADABLE,
                                                DR.RUN_FITTED]
        assert rows[0] == original[0]
        # THE DATABASE: LABELS_UNREADABLE is not decisive; FITTED is
        idx = await conn.fetchval(
            "SELECT pg_get_indexdef(to_regclass($1))", DR.RUNS_DECISIVE_INDEX)
        assert "UNIQUE" in idx and all(o in idx for o in NON_DECISIVE), idx
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO derek_research_model_runs (run_id, run_day, "
                " ran_at, outcome, counts) VALUES ($1, $2, to_timestamp($3),"
                " $4, '{}'::jsonb)", "derek-research-run:%s:x" % DAYS[0],
                DAYS[0], T_LATER, DR.RUN_EVALUATED)
    finally:
        await _finish(conn, prior)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 4 · THE PAPER PATH NEVER FITS; THE SCHEDULED STEP DOES; PAPER PICKS IT UP
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_the_paper_path_defers_the_fit_and_the_scheduled_step_makes_it(
        monkeypatch):
    conn = await T._connect()
    prior = T._UNSET
    key = "derek-research-bootstrap-test-session (SYNTHETIC)"
    real_attempt_fit = DR.attempt_fit
    calls: list = []

    async def _no_fit_on_the_paper_path(*a, **k):
        calls.append(k.get("model_id"))
        raise AssertionError("the paper path called attempt_fit")

    try:
        made, prior = await _setup(conn, "ctx")
        PD._CONTEXT_CACHE.clear()
        monkeypatch.setattr(DR, "attempt_fit", _no_fit_on_the_paper_path)
        # ── 1 · THE PER-VALUATION HOOK'S CONTEXT, BEFORE ANY COLLECTION ─
        t_ctx = T_BACKFILL - 100.0
        d1 = await PD._context(conn, {"now": t_ctx,
                                      "context_cache_key": key})
        assert d1["model"]["ok"] is False
        assert d1["model"]["refusal"] == PD.R_NO_RESEARCH_MODEL
        assert d1["model_attempt"]["ran"] is True
        assert d1["model_attempt"]["outcome"] == DR.RUN_INSUFFICIENT
        # ── 2 · COLLECTION REACHES THE MINIMUM; THE PAPER PATH ASKS AGAIN:
        #        it reads the day's run and never fits ──────────────────
        await _backfill_reaches_the_minimum(conn, made)
        pa = await PD.ensure_model_attempt(conn, at=T_BACKFILL + 10.0)
        assert pa["already_ran"] is True and pa["ran"] is False, pa
        assert pa["outcome"] == DR.RUN_INSUFFICIENT
        assert pa["rerun_refusal"] == DR.R_RERUN_LEFT_TO_THE_SCHEDULE
        assert calls == [] and await _n_auto_attempts(conn) == 0
        assert len(await _day_rows(conn, DAYS[0])) == 1
        # ── 3 · THE SCHEDULED STEP: research_step, then model_run_step ──
        monkeypatch.setattr(DR, "attempt_fit", real_attempt_fit)
        got = await D.after_cycle(conn, cycle={}, now=T_BACKFILL + 60.0)
        mr = got["model_run"]
        assert mr["ran"] is True and mr["outcome"] == DR.RUN_FITTED, mr
        mid = _auto_id(FM.COHORT_DISPLAYED, DAYS[0])
        assert mr["attempted_model_ids"] == [mid]
        # ── 4 · THE NEXT CONTEXT USES IT, INSIDE THE CACHE'S TTL ────────
        t2 = T_BACKFILL + 120.0
        assert t2 - t_ctx < PD.CONTEXT_TTL_S
        d2 = await PD._context(conn, {"now": t2, "context_cache_key": key})
        assert d2["model"]["ok"] is True, d2["model"]
        assert d2["model"]["model_id"] == mid
        assert d2["model"]["provenance_verified"] is True
        assert d2["model"]["approval_status"] == FM.STATE_CANDIDATE
        assert d2["model"]["promoted"] is False
        # a cached context that HAS a model is served from the cache
        d3 = await PD._context(conn, {"now": t2 + 1.0,
                                      "context_cache_key": key})
        assert d3 is d2

        # ── 5 · A DAY WHOSE FIRST CALLER IS PAPER, WITH A COHORT READY:
        #        a named deferral, NO row, NO attempt; the scheduled step
        #        then makes the day's first (and only, decisive) run -- here
        #        an evaluation without refit, since the cohort has not grown
        #        by CANDIDATE_REFIT_MIN_NEW_EVENTS since yesterday's fit ───
        monkeypatch.setattr(DR, "attempt_fit", _no_fit_on_the_paper_path)
        n_att = await _n_auto_attempts(conn)
        pd1 = await PD.ensure_model_attempt(conn, at=DAY1 + 120.0)
        assert pd1["ran"] is False, pd1
        assert pd1["deferral"] == DR.R_FIT_DEFERRED, pd1
        assert FM.COHORT_DISPLAYED in pd1["ready_cohorts"]
        assert pd1["outcome"] is None and not pd1.get("already_ran")
        assert calls == [] and await _n_auto_attempts(conn) == n_att
        assert await _day_rows(conn, DAYS[1]) == []
        monkeypatch.setattr(DR, "attempt_fit", real_attempt_fit)
        s1 = await D.model_run_step(conn, now=DAY1 + 180.0)
        assert s1["ran"] is True and s1["outcome"] == DR.RUN_EVALUATED, s1
        assert s1["run_id"] == "derek-research-run:%s" % DAYS[1]
        assert s1["fitted"][FM.COHORT_DISPLAYED]["refit"] is False
        assert s1["attempted_model_ids"] == []
        assert [r["outcome"] for r in await _day_rows(conn, DAYS[1])] == [
            DR.RUN_EVALUATED]
        later = await PD.ensure_model_attempt(conn, at=DAY1 + 240.0)
        assert later["already_ran"] is True and later["ran"] is False
        assert later["outcome"] == DR.RUN_EVALUATED
        assert (await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT))[
            "refusal"] == FM.R_NO_APPROVED_MODEL
    finally:
        PD._CONTEXT_CACHE.clear()
        await _finish(conn, prior)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 5 · CONCURRENT CALLERS ARE SERIALIZED PER UTC DAY (D4)
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_concurrent_daily_runs_make_one_attempt_one_model_one_decisive_run(
        monkeypatch):
    import asyncpg
    conn = await T._connect()
    c2 = await T._connect()
    c3 = await T._connect()
    prior = T._UNSET
    try:
        made, prior = await _setup(conn, "race")
        first = await _first_run_is_insufficient(conn)
        await _backfill_reaches_the_minimum(conn, made)

        # THE FIRST CALLER IS HELD INSIDE ITS FIT (the lock is taken)
        real_fit = FM.fit_from_records
        entered = asyncio.Event()
        fits: list = []

        async def _slow_fit(*a, **k):
            fits.append(k.get("cohorts"))
            entered.set()
            await asyncio.sleep(1.5)
            return await real_fit(*a, **k)

        monkeypatch.setattr(FM, "fit_from_records", _slow_fit)
        ta = asyncio.create_task(D.model_run_step(conn, now=T_RERUN))
        await asyncio.wait_for(entered.wait(), 30.0)
        # a second scheduled caller at the SAME instant waits for the lock
        tb = asyncio.create_task(D.model_run_step(c2, now=T_RERUN))
        await asyncio.sleep(0.4)
        assert not tb.done()
        waiting = await c3.fetchval(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "   AND NOT granted")
        assert waiting >= 1
        # the paper path does not wait inside its time bound: named, nothing
        t0 = time.monotonic()
        pc = await asyncio.wait_for(
            PD.ensure_model_attempt(c3, at=T_RERUN + 2.0), 1.0)
        assert time.monotonic() - t0 < 1.0
        assert pc["ran"] is False and pc["deferral"] == \
            DR.R_DAILY_RUN_IN_PROGRESS, pc
        # while the fit runs, nothing for the day is visible but the first row
        assert [r["run_id"] for r in await _day_rows(c3, DAYS[0])] == [
            first["run_id"]]
        a, b = await asyncio.gather(ta, tb)
        mid = _auto_id(FM.COHORT_DISPLAYED, DAYS[0])
        assert a["ran"] is True and a["outcome"] == DR.RUN_FITTED, a
        assert b["ran"] is False and b["already_ran"] is True, b
        assert b["run_id"] == a["run_id"] and b["outcome"] == DR.RUN_FITTED
        assert fits == [[FM.COHORT_DISPLAYED]]          # ONE fit, not two
        rows = await _day_rows(conn, DAYS[0])
        assert [(r["run_id"], r["outcome"]) for r in rows] == [
            (first["run_id"], DR.RUN_INSUFFICIENT),
            (a["run_id"], DR.RUN_FITTED)]
        assert list(rows[1]["attempted_model_ids"]) == [mid]
        att = await conn.fetch(
            "SELECT attempt_id, run_id FROM derek_research_model_attempts "
            " WHERE attempt_id LIKE $1", DR.AUTO_MODEL_PREFIX + ":%")
        assert [(r["attempt_id"], r["run_id"]) for r in att] == [
            (mid, a["run_id"])]
        assert await _n_entry_models(conn) == 1
        # THE DATABASE ITSELF: one decisive run per day
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO derek_research_model_runs (run_id, run_day, "
                " ran_at, outcome, counts) VALUES ($1, $2, to_timestamp($3),"
                " $4, '{}'::jsonb)", "derek-research-run:%s:x" % DAYS[0],
                DAYS[0], T_LATER, DR.RUN_EVALUATED)
    finally:
        await _finish(conn, prior)
        await c3.close()
        await c2.close()
        await conn.close()


@pg
async def test_a_run_insert_that_fails_takes_its_attempt_and_model_with_it(
        monkeypatch):
    conn = await T._connect()
    prior = T._UNSET
    try:
        made, prior = await _setup(conn, "atomic")
        first = await _first_run_is_insufficient(conn)
        await _backfill_reaches_the_minimum(conn, made)
        # the run row's insert fails in the database, after the fit
        monkeypatch.setattr(DR, "RUN_INSERT_SQL", DR.RUN_INSERT_SQL.replace(
            "INSERT INTO derek_research_model_runs",
            "INSERT INTO derek_research_model_runs_absent_in_this_test"))
        got = await D.model_run_step(conn, now=T_RERUN)
        assert got["ran"] is False, got
        assert got["refusal"].startswith("MODEL_RUN_RAISED"), got
        # NOTHING the run did survives: no attempt, no model, no row
        assert await _n_auto_attempts(conn) == 0
        assert await _n_entry_models(conn) == 0
        assert [r["run_id"] for r in await _day_rows(conn, DAYS[0])] == [
            first["run_id"]]
        # and the day is still open: the next scheduled call fits
        monkeypatch.undo()
        run = await D.model_run_step(conn, now=T_RERUN + 60.0)
        assert run["ran"] is True and run["outcome"] == DR.RUN_FITTED, run
        assert await _n_auto_attempts(conn) == 1
    finally:
        await _finish(conn, prior)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 6 · A RE-RUN NOT AFTER THE DAY'S LAST RUN IS REFUSED; THE DECISIVE ROW LEADS
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_a_rerun_not_after_the_days_last_run_is_refused_by_name():
    conn = await T._connect()
    prior = T._UNSET
    try:
        made, prior = await _setup(conn, "clock")
        first = await _first_run_is_insufficient(conn)
        await _backfill_reaches_the_minimum(conn, made)
        for at in (T_FIRST_RUN, T_FIRST_RUN - 60.0):
            got = await D.model_run_step(conn, now=at)
            assert got["ran"] is False and got["already_ran"] is True, got
            assert got["rerun_refusal"] == DR.R_RERUN_NOT_AFTER_LAST_RUN, got
            assert got["run_id"] == first["run_id"]
        assert len(await _day_rows(conn, DAYS[0])) == 1
        assert await _n_auto_attempts(conn) == 0
        run = await D.model_run_step(conn, now=T_RERUN)
        assert run["ran"] is True and run["outcome"] == DR.RUN_FITTED, run
        # LATEST: the decisive row of the newest day, even when a
        # non-decisive row of that day was recorded after it (SYNTHETIC
        # rows on this file's second day, written directly)
        for rid, at, outcome in (
                ("derek-research-run:%s" % DAYS[1], DAY1 + 100.0,
                 DR.RUN_FITTED),
                ("derek-research-run:%s:2" % DAYS[1], DAY1 + 200.0,
                 DR.RUN_INSUFFICIENT)):
            await conn.execute(
                "INSERT INTO derek_research_model_runs (run_id, run_day, "
                " ran_at, outcome, counts) VALUES ($1, $2, to_timestamp($3),"
                " $4, '{}'::jsonb)", rid, DAYS[1], at, outcome)
        lr = await DR.latest_model_run(conn)
        assert lr["run_day"] == str(DAYS[1])
        assert lr["run_id"] == "derek-research-run:%s" % DAYS[1], lr
        assert lr["outcome"] == DR.RUN_FITTED
    finally:
        await _finish(conn, prior)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 7 · WITHOUT MIGRATION 181 THE RE-RUN IS REFUSED -- ON AN ISOLATED COPY (D1)
# ═════════════════════════════════════════════════════════════════════════

async def _copy_as_170(conn, schema: str, *, rows_of_day=None) -> None:
    """`<schema>.derek_research_model_runs` in 170's shape: same columns,
    defaults and CHECKs, PRIMARY KEY (run_id), UNIQUE (run_day) under 170's
    name. Inside the caller's (rolled-back) transaction; the shared table is
    only read."""
    await conn.execute("CREATE SCHEMA %s" % schema)
    await conn.execute(
        "CREATE TABLE %s.derek_research_model_runs (LIKE "
        " public.derek_research_model_runs INCLUDING DEFAULTS "
        " INCLUDING CONSTRAINTS)" % schema)
    await conn.execute(
        "ALTER TABLE %s.derek_research_model_runs ADD PRIMARY KEY (run_id),"
        " ADD CONSTRAINT derek_research_model_runs_one_per_day "
        " UNIQUE (run_day)" % schema)
    if rows_of_day is not None:
        await conn.execute(
            "INSERT INTO %s.derek_research_model_runs SELECT * FROM "
            " public.derek_research_model_runs WHERE run_day = $1" % schema,
            rows_of_day)


async def _shared_table_shape(conn) -> tuple:
    return (
        await conn.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = "
            " 'public.derek_research_model_runs'::regclass "
            "   AND conname = 'derek_research_model_runs_one_per_day'"),
        await conn.fetchval(
            "SELECT pg_get_indexdef(to_regclass($1))",
            "public." + DR.RUNS_DECISIVE_INDEX))


@pg
async def test_without_migration_181_the_rerun_is_refused_on_an_isolated_copy():
    conn = await T._connect()
    prior = T._UNSET
    try:
        made, prior = await _setup(conn, "pre181")
        first = await _first_run_is_insufficient(conn)
        await _backfill_reaches_the_minimum(conn, made)
        shape = await _shared_table_shape(conn)
        assert shape[0] == 0 and shape[1], shape
        n_att, n_mod = await _n_auto_attempts(conn), await _n_entry_models(
            conn)
        tr = conn.transaction()
        await tr.start()
        try:
            await _copy_as_170(conn, "derek_boot_pre181",
                               rows_of_day=DAYS[0])
            await conn.execute(
                "SET LOCAL search_path = derek_boot_pre181, public")
            assert await conn.fetchval(
                "SELECT relnamespace::regnamespace::text FROM pg_class "
                " WHERE oid = to_regclass('derek_research_model_runs')") == \
                "derek_boot_pre181"
            run = await D.model_run_step(conn, now=T_RERUN)
            assert run["already_ran"] is True and run["ran"] is False, run
            assert run["run_id"] == first["run_id"]
            assert run["outcome"] == DR.RUN_INSUFFICIENT
            assert run["rerun_refusal"] == DR.R_RERUN_NEEDS_MIGRATION_181
            # NOTHING WAS FITTED, even inside this transaction
            assert await _n_auto_attempts(conn) == n_att
            assert await _n_entry_models(conn) == n_mod
            assert await conn.fetchval(
                "SELECT count(*) FROM derek_research_model_runs") == 1
        finally:
            await tr.rollback()
        # THE SHARED TABLE WAS NEVER ALTERED
        assert await _shared_table_shape(conn) == shape
        assert await conn.fetchval(
            "SELECT to_regnamespace('derek_boot_pre181')") is None
        # and on it, the same call fits
        run = await D.model_run_step(conn, now=T_RERUN)
        assert run["ran"] is True and run["outcome"] == DR.RUN_FITTED, run
    finally:
        await _finish(conn, prior)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 8 · MIGRATION 181 AND ITS DOWN SCRIPT, ON ISOLATED COPIES (D2, D7)
# ═════════════════════════════════════════════════════════════════════════

async def _insert_run(conn, rid, day, at, outcome):
    await conn.execute(
        "INSERT INTO derek_research_model_runs (run_id, run_day, ran_at, "
        " outcome, counts) VALUES ($1, $2, to_timestamp($3), $4, "
        " '{}'::jsonb)", rid, day, at, outcome)


async def _table_shape(conn) -> dict:
    """The constraint and unique indexes of whichever
    derek_research_model_runs the search path names."""
    return {
        "one_per_day": await conn.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = "
            " to_regclass('derek_research_model_runs') "
            "   AND conname = 'derek_research_model_runs_one_per_day'"),
        "partial_unique": [r["d"] for r in await conn.fetch(
            "SELECT pg_get_indexdef(indexrelid) AS d FROM pg_index "
            " WHERE indrelid = to_regclass('derek_research_model_runs') "
            "   AND indisunique AND indpred IS NOT NULL")]}


@pg
async def test_migration_181_applies_over_same_day_rows_and_its_down_refuses_them():
    import asyncpg
    up, down = UP_181.read_text(), DOWN_181.read_text()
    # no BEGIN/COMMIT of its own: the runner wraps it in a transaction, and
    # a COMMIT inside it would end the caller's (this test's) transaction
    stmts = " ".join(ln for ln in up.upper().splitlines()
                     if not ln.strip().startswith("--"))
    assert "BEGIN;" not in stmts and "COMMIT;" not in stmts
    conn = await T._connect()
    try:
        await _ensure_schema(conn)
        d0, d1 = DAYS
        tr = conn.transaction()
        await tr.start()
        try:
            # ── A · 170's SHAPE, ONE ROW PER DAY: 181 APPLIES, TWICE ────
            await _copy_as_170(conn, "derek_boot_mig_a")
            await conn.execute(
                "SET LOCAL search_path = derek_boot_mig_a, public")
            await _insert_run(conn, "r-a0", d0, DAY0 + 10,
                              DR.RUN_INSUFFICIENT)
            await _insert_run(conn, "r-a1", d1, DAY1 + 10, DR.RUN_FITTED)
            await conn.execute(up)
            await conn.execute(up)                       # idempotent
            sh = await _table_shape(conn)
            assert sh["one_per_day"] == 0, sh
            assert len(sh["partial_unique"]) == 1, sh
            assert "derek_boot_mig_a." in sh["partial_unique"][0]
            assert all(o in sh["partial_unique"][0] for o in NON_DECISIVE)
            # non-decisive same-day rows are accepted; a second decisive is
            # not
            await _insert_run(conn, "r-a0-2", d0, DAY0 + 20,
                              DR.RUN_LABELS_UNREADABLE)
            await _insert_run(conn, "r-a0-3", d0, DAY0 + 30, DR.RUN_FITTED)
            with pytest.raises(asyncpg.UniqueViolationError):
                async with conn.transaction():
                    await _insert_run(conn, "r-a0-4", d0, DAY0 + 40,
                                      DR.RUN_EVALUATED)
            # ── THE DOWN SCRIPT REFUSES OVER SAME-DAY ROWS ──────────────
            with pytest.raises(asyncpg.RaiseError) as e:
                async with conn.transaction():
                    await conn.execute(down)
            assert "not rolled back" in str(e.value)
            assert (await _table_shape(conn))["one_per_day"] == 0
            assert await conn.fetchval(
                "SELECT count(*) FROM derek_research_model_runs") == 4

            # ── B · A TABLE ALREADY HOLDING SAME-DAY ROWS (the earlier
            #        181 draft's shape: no UNIQUE (run_day), an index that
            #        treated LABELS_UNREADABLE as decisive): 181 applies ──
            await conn.execute("CREATE SCHEMA derek_boot_mig_b")
            await conn.execute(
                "CREATE TABLE derek_boot_mig_b.derek_research_model_runs "
                " (LIKE public.derek_research_model_runs INCLUDING DEFAULTS "
                "  INCLUDING CONSTRAINTS)")
            await conn.execute(
                "SET LOCAL search_path = derek_boot_mig_b, public")
            await conn.execute(
                "ALTER TABLE derek_research_model_runs ADD PRIMARY KEY "
                " (run_id)")
            await conn.execute(
                "CREATE UNIQUE INDEX "
                " derek_research_model_runs_one_decisive_per_day ON "
                " derek_research_model_runs (run_day) WHERE outcome <> "
                " 'INSUFFICIENT_LABELLED_FIXTURES'")
            await _insert_run(conn, "r-b0", d0, DAY0 + 10,
                              DR.RUN_INSUFFICIENT)
            await _insert_run(conn, "r-b0-2", d0, DAY0 + 20,
                              DR.RUN_INSUFFICIENT)
            await _insert_run(conn, "r-b0-3", d0, DAY0 + 30, DR.RUN_FITTED)
            await conn.execute(up)
            sh = await _table_shape(conn)
            assert sh["one_per_day"] == 0
            assert len(sh["partial_unique"]) == 1, sh
            assert DR.RUNS_DECISIVE_INDEX in sh["partial_unique"][0]
            assert all(o in sh["partial_unique"][0] for o in NON_DECISIVE)
            assert await conn.fetchval(
                "SELECT count(*) FROM derek_research_model_runs") == 3
            with pytest.raises(asyncpg.RaiseError):
                async with conn.transaction():
                    await conn.execute(down)

            # ── C · NO SAME-DAY PAIR: THE DOWN SCRIPT RESTORES 170 ──────
            await _copy_as_170(conn, "derek_boot_mig_c")
            await conn.execute(
                "SET LOCAL search_path = derek_boot_mig_c, public")
            await conn.execute(up)
            await _insert_run(conn, "r-c0", d0, DAY0 + 10,
                              DR.RUN_INSUFFICIENT)
            await _insert_run(conn, "r-c1", d1, DAY1 + 10,
                              DR.RUN_LABELS_UNREADABLE)
            assert (await _table_shape(conn))["one_per_day"] == 0
            await conn.execute(down)
            sh = await _table_shape(conn)
            assert sh == {"one_per_day": 1, "partial_unique": []}, sh
            with pytest.raises(asyncpg.UniqueViolationError):
                async with conn.transaction():
                    await _insert_run(conn, "r-c0-2", d0, DAY0 + 20,
                                      DR.RUN_INSUFFICIENT)
            # and the down script is itself safe to re-run
            await conn.execute(down)
            assert (await _table_shape(conn))["one_per_day"] == 1
        finally:
            await tr.rollback()
        # NONE OF IT TOUCHED THE SHARED TABLE
        shape = await _shared_table_shape(conn)
        assert shape[0] == 0 and shape[1], shape
        for s in ("derek_boot_mig_a", "derek_boot_mig_b", "derek_boot_mig_c"):
            assert await conn.fetchval("SELECT to_regnamespace($1)",
                                       s) is None
    finally:
        await conn.close()
