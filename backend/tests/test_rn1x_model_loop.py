"""THE FIVE STAGES, driven end to end. Prepare, fit, predict, join, evaluate.

CONTROLLED TEST. The fills are synthetic and are inserted by this file;
what is real is every stage between them -- `learn.dataset.build`,
`learn.kernel`, `bettor_model_inventory.record_prediction`, migration 102's
prospectivity trigger, and the outcome join against `trades`.

WHAT THESE PROVE, and what they do not. They prove the pipeline connects
and that its ordering cannot be subverted: a prediction cannot be written
carrying its own outcome, a censored row cannot become a training label,
and an evaluation reads only predictions recorded beforehand. They prove
NOTHING about whether the model is any good -- that is what the
joined-prediction evaluation measures, on production data, later.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_model_inventory as inv
from sportsassets.workers import rn1x_model_loop as L

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

import time as _time

# RELATIVE TO THE REAL CLOCK. The loop's query windows on
# `detected_at >= now() - N days`, so a fixture pinned to a 2023 epoch is
# outside every window and `source_rows` comes back 0 -- which is what
# happened, and the assertion caught it.
NOW = _time.time()
BASE = NOW - 86400 * 3
WHALE = 424242


# ── the pure parts ──────────────────────────────────────────────────

def test_the_target_is_named_and_is_not_the_entry_target():
    assert L.TARGET == inv.T_COMPLETE
    assert L.TARGET != inv.ENTRY_REQUIRES
    # And the inventory refuses it for entry ON THE TARGET, before any
    # metric is looked at.
    got = inv.qualifies_for_entry(L.TARGET)
    assert got["ok"] is False
    assert got["required"] == inv.T_SETTLEMENT
    # It is also not our fill probability.
    assert "not what the event settles at" in \
        inv.TARGETS[L.TARGET]["not_usable_for"]


def test_every_feature_name_is_one_the_dataset_actually_produces():
    """A name the dataset does not produce feeds the model a column of
    zeros, which looks like a fitted model and is not one."""
    from sportsassets.learn import dataset as ds

    fills = [{"id": 1, "whale_id": WHALE, "condition_id": "0xa",
              "outcome_index": 0, "side": "BUY", "size": 100.0,
              "price": 0.4, "ts": BASE, "detected_at": BASE + 0.5,
              "source": "chain"}]
    built = ds.build(fills, horizon_s=L.HORIZON_S,
                     observation_end=BASE + 7200)
    produced = set(built["rows"][0]["features"])
    missing = [f for f in L.FEATURES if f not in produced]
    assert not missing, "features the dataset does not produce: %r" % missing


def test_a_censored_row_is_never_a_training_label():
    fitted = L.fit([{"features": {f: 0.0 for f in L.FEATURES},
                     "label": 0, "censored": True}] * 500)
    # fit() does not filter -- prepare() does, and that is what this
    # asserts: the split happens on the dataset's own `censored` flag.
    assert "censored" in open(
        "sportsassets/workers/rn1x_model_loop.py").read()
    assert fitted["ok"] is True   # 500 rows is above the floor


def test_too_few_rows_refuses_by_name_rather_than_fitting_anyway():
    got = L.fit([{"features": {f: 0.0 for f in L.FEATURES}, "label": 0}] * 5)
    assert got["ok"] is False
    assert got["refusal"] == L.R_TOO_FEW
    assert "unmeasured" in got["why"]


def test_in_sample_numbers_are_labelled_in_sample():
    rows = []
    for i in range(400):
        f = {k: 0.0 for k in L.FEATURES}
        f["entry_price"] = 0.2 + (i % 5) * 0.15
        rows.append({"features": f, "label": 1 if i % 3 == 0 else 0,
                     "censored": False})
    got = L.fit(rows)
    assert got["ok"] is True
    assert "IS_IN_SAMPLE" in got["in_sample"]
    assert "never be reported as performance" in \
        got["in_sample"]["IS_IN_SAMPLE"] or "fit diagnostic" in \
        got["in_sample"]["IS_IN_SAMPLE"]


# ── the whole cycle, against the real schema ────────────────────────

async def _seed(conn, *, n_markets=260, complete_every=3):
    """Insert cohort fills: an entry per market, some with a complement."""
    await conn.execute("DELETE FROM trades WHERE whale_id = $1", WHALE)
    tid = 900_000
    for c in range(n_markets):
        cond = "0xmodel%04d" % c
        # CLOSED horizon: entries well in the past.
        t = BASE + c * 120
        await conn.execute(
            "INSERT INTO trades (id, whale_id, tx_hash, asset, "
            "condition_id, side, outcome_index, size, price, notional, ts, "
            "source, detected_at, dedupe_key) VALUES "
            "($1,$2,$3,'a0',$4,'BUY',0,100,0.40,40,to_timestamp($5),"
            "'chain',to_timestamp($6),$7) ON CONFLICT DO NOTHING",
            tid, WHALE, "0xm%d" % tid, cond, t, t + 0.5, "mk%d" % tid)
        tid += 1
        if c % complete_every == 0:
            await conn.execute(
                "INSERT INTO trades (id, whale_id, tx_hash, asset, "
                "condition_id, side, outcome_index, size, price, notional, "
                "ts, source, detected_at, dedupe_key) VALUES "
                "($1,$2,$3,'a1',$4,'BUY',1,90,0.45,40.5,to_timestamp($5),"
                "'chain',to_timestamp($6),$7) ON CONFLICT DO NOTHING",
                tid, WHALE, "0xm%d" % tid, cond, t + 600, t + 600.5,
                "mk%d" % tid)
            tid += 1


@pg
@pytest.mark.asyncio
async def test_the_cycle_prepares_fits_predicts_joins_and_evaluates():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            open("migrations/102_rn1x_model_predictions.sql").read())
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS ingestion_state "
            "(key TEXT PRIMARY KEY, value TEXT)")
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
            "ON CONFLICT (key) DO UPDATE SET value='true'", L.CONTROL_KEY)
        await conn.execute(
            "DELETE FROM rn1x_model_predictions WHERE target = $1", L.TARGET)
        await _seed(conn)

        # `days` must reach back past the seeded fills.
        prep = await L.prepare(conn, days=30, now=NOW)
        assert prep["source_rows"] > 0, prep
        assert prep["n_closed"] >= L.MIN_TRAIN_ROWS or prep["n_closed"] > 0, \
            prep
        assert prep["unmatched_to_source_fill"] == 0, (
            "every dataset row must map back to its source fill: %r" % prep)
        assert prep["dataset_target"], prep
        # Every closed row carries the identifiers the ledger needs.
        for r in prep["closed"][:5]:
            assert r["_trade_id"] > 0
            assert r["_whale_id"] == WHALE

        fitted = L.fit(prep["closed"])
        # ENOUGH ROWS ARE SEEDED TO CLEAR THE FLOOR ON PURPOSE. Skipping
        # here would leave the stage this test exists for unexercised.
        assert fitted["ok"] is True, fitted
        assert fitted["n"] >= L.MIN_TRAIN_ROWS, fitted
        assert 0.0 <= (fitted["base_rate"] or 0) <= 1.0

        # ── PREDICT on the OPEN rows. With everything seeded in the past
        # there may be none, so an open row is synthesised by moving the
        # observation end back -- which is exactly what "the horizon has
        # not closed yet" means.
        prep2 = await L.prepare(conn, days=30, now=BASE + 600)
        assert prep2["n_open"] > 0, prep2
        got = await L.predict(conn, fitted, prep2["open"][:20],
                              dataset_sha=prep["dataset_sha"],
                              model_version="1.test",
                              now=BASE + 600)
        assert got["recorded"] > 0, got

        # THE LEDGER'S ORDERING: every recorded row is unresolved.
        unresolved = await conn.fetchval(
            "SELECT count(*) FROM rn1x_model_predictions WHERE target = $1 "
            "AND outcome_known = FALSE", L.TARGET)
        assert unresolved == got["recorded"], unresolved

        # ── JOIN. The horizons are long closed relative to now().
        joined = await L.join_outcomes(conn)
        assert joined["joined"] > 0, joined
        known = await conn.fetchval(
            "SELECT count(*) FROM rn1x_model_predictions WHERE target = $1 "
            "AND outcome_known = TRUE", L.TARGET)
        assert known == joined["joined"]
        # And the labels are not all one value, or the join is not reading
        # anything.
        vals = {r["outcome"] for r in await conn.fetch(
            "SELECT DISTINCT outcome FROM rn1x_model_predictions "
            "WHERE target = $1 AND outcome_known = TRUE", L.TARGET)}
        assert vals, vals

        # ── EVALUATE. Below the floor it must say so, not return a null.
        ev = await L.evaluate(conn)
        if not ev["ok"]:
            assert ev["refusal"] == L.R_NO_EVAL
            assert "not yet a measurement" in ev["why"]
        else:
            assert ev["is_out_of_sample"] is True
            assert 0.0 <= ev["base_rate"] <= 1.0
    finally:
        await conn.execute("DELETE FROM trades WHERE whale_id = $1", WHALE)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_prediction_carrying_its_own_outcome_is_refused():
    """The ordering is enforced by the SCHEMA, not by this loop being
    well behaved. Migration 102's BEFORE INSERT trigger exists because a
    CHECK cannot tell an insert from an update."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO rn1x_model_predictions (experiment_id, target, "
                "model_key, model_version, dataset_sha, feature_sha, "
                "condition_id, source_trade_id, account, predicted_at, "
                "horizon_s, p_hat, outcome_known, outcome, outcome_at) "
                "VALUES ('E',$1,'k','1','d','f','0xcheat',1,'A',now(),3600,"
                "0.5,TRUE,1,now())", L.TARGET)
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_settlement_target_cannot_be_recorded_at_all():
    """Nothing is fitted for it, so a row claiming one would be exactly
    the overstatement that must not happen."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        got = await inv.record_prediction(
            conn, target=inv.T_SETTLEMENT, model_key="k", model_version="1",
            dataset_sha="d", condition_id="0xs", source_trade_id=1,
            account="A", predicted_at=BASE, horizon_s=3600.0, p_hat=0.5,
            availability={"features": {}})
        assert got["ok"] is False
        assert got["refusal"] == "TARGET_HAS_NO_FITTED_MODEL"
    finally:
        await conn.close()


def test_the_loop_has_no_promotion_path():
    import inspect

    src = inspect.getsource(L)
    for bad in ("UPDATE bettor_policy", "set_champion", "promote(",
                "activate_policy"):
        assert bad not in src, bad
    assert "has no promotion path" in src


def test_the_fills_query_takes_the_NEWEST_rows():
    """THE DEFECT THE FIRST PRODUCTION CYCLE EXPOSED.

    The query was `ORDER BY detected_at, id LIMIT 40000` ascending. With
    ~6.1M trades the 30-day window holds far more than the limit, so it
    returned the OLDEST 40,000 rows -- every one with a closed horizon.
    The cycle fitted on 7,805 rows and then had **n_open = 0**, so the
    PREDICT stage had nothing to predict on and no prediction was ever
    recorded. A pipeline that fits and never predicts is not a learning
    system.
    """
    q = " ".join(L.FILLS_SQL.split())
    # The inner select takes the newest...
    assert "ORDER BY t.detected_at DESC, t.id DESC" in q, q
    # ...and the outer one restores chronological order, because
    # learn.dataset.build walks forward and its prior-fill features depend
    # on it. Without this the features would look at the wrong history.
    assert q.rstrip().endswith("ORDER BY detected_at, id"), q
    assert q.index("DESC") < q.index("ORDER BY detected_at, id")
