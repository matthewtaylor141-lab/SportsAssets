"""Items 3 and 4: the three questions, kept apart and answered separately.

The one these tests exist to prevent: a prediction landing in a ledger
and being read as a production valuation model. Every path below either
refuses, or records something explicitly labelled as not feeding entry.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_entry_gate as gate
from sportsassets import bettor_model_inventory as inv

T0 = 1_780_000_000.0
COND = "0xcond_model_inv"


def _fill(**kw):
    base = {"account": "A1", "condition_id": COND, "outcome_index": 0,
            "side": "BUY", "price": 0.60, "size": 100.0,
            "ts": T0, "detected_at": T0 + 5, "source": "chain"}
    base.update(kw)
    return base


# ── question 1: does an appropriate scorer exist? ───────────────────

def test_neither_fitted_target_qualifies_for_entry_and_the_reason_is_TARGET():
    for t in inv.FITTED_TARGETS:
        got = inv.qualifies_for_entry(t)
        assert got["ok"] is False, (t, got)
        assert got["refusal"] == "R_MODEL_TARGET_MISMATCH", got
        assert got["required"] == inv.T_SETTLEMENT


def test_the_settlement_target_is_declared_but_has_no_fitted_model():
    """A connected engine with no scorer is a DIFFERENT status from an
    unimplemented path, and the refusal codes must differ too."""
    got = inv.qualifies_for_entry(inv.T_SETTLEMENT)
    assert got["ok"] is False
    assert got["refusal"] == "R_NO_QUALIFIED_MODEL", got
    assert inv.TARGETS[inv.T_SETTLEMENT]["fitted"] is False


def test_an_unknown_target_is_refused_rather_than_defaulted():
    got = inv.qualifies_for_entry("SOMETHING_ELSE")
    assert got["ok"] is False and got["refusal"] == "UNKNOWN_TARGET"


def test_a_fitting_kernel_really_does_exist():
    """'Nothing is fitted anywhere' was wrong and the correction matters:
    four estimators plus a base-rate control, all in the standard
    library, all importable."""
    from sportsassets.learn import kernel as K
    for name in inv.ESTIMATORS:
        assert hasattr(K, name), name


# ── question 2: do the live inputs reach the decision function? ─────

def test_every_decision_time_feature_resolves_from_a_production_row():
    """MEASURED, not asserted. The inputs are the cohort fill and that
    market's earlier fills -- both already in hand in the prospective
    lane -- so the vector must build with nothing missing."""
    prior = [_fill(ts=T0 - 600, detected_at=T0 - 595, price=0.58, size=40),
             _fill(ts=T0 - 300, detected_at=T0 - 295, price=0.59, size=60,
                   outcome_index=1)]
    got = inv.feature_availability(_fill(), prior_fills=prior)
    assert got["row_built"] is True, got
    assert got["missing"] == [], got["missing"]
    assert got["all_present"] is True, got
    assert got["feature_count"] == len(got["present"])


def test_availability_is_reported_per_row_not_claimed_globally():
    """A lone first fill still builds, and the report says what it had."""
    got = inv.feature_availability(_fill())
    assert got["row_built"] is True, got
    assert isinstance(got["present"], list) and got["present"]
    assert got["features"]["is_first_fill_in_market"] in (0.0, 1.0, True, False)


# ── the separation that must hold ───────────────────────────────────

def test_the_entry_gate_still_refuses_and_the_ledger_cannot_change_that():
    """Wiring a prediction into the ledger must not open the entry path.

    The gate is asked with a model carrying a FITTED target, which is the
    strongest thing the ledger could ever offer it, and it must still
    refuse on target.
    """
    model = {"model_key": "rn1_complement_1h", "version": "2",
             "target": inv.T_COMPLETE, "status": "FROZEN",
             "prediction_validity": "VALID_AS_ENTRY_TIME"}
    q = gate.qualify_model(model)
    assert q["qualified"] is False, q
    # EVERY other requirement is satisfied here -- frozen status, valid
    # predictions -- so TARGET is the only thing left to refuse on, and
    # the assertion cannot pass for an incidental reason.
    assert q["refusals"] == [gate.R_MODEL_TARGET_MISMATCH], q
    assert inv.describe()["feeds_the_entry_gate"] is False


def test_describe_does_not_overstate_any_of_the_three_answers():
    d = inv.describe()
    assert d["three_questions"]["scorer_exists_for_entry"] is False
    assert d["three_questions"]["live_inputs_reach_the_decision_function"] \
        is True
    assert "unanswerable" in d["three_questions"][
        "evidence_qualifies_it_for_entry"]


# ── the ledger refuses what it cannot honestly hold ─────────────────

class _Conn:
    def __init__(self):
        self.calls = []

    async def execute(self, *a):
        self.calls.append(a)


@pytest.mark.asyncio
async def test_the_ledger_refuses_the_settlement_target():
    """A row claiming a settlement prediction would be the exact
    overstatement the directive forbids -- there is no model to make one."""
    c = _Conn()
    got = await inv.record_prediction(
        c, target=inv.T_SETTLEMENT, model_key="k", model_version="1",
        dataset_sha="d", condition_id=COND, source_trade_id=1,
        account="A1", predicted_at=T0, horizon_s=3600.0, p_hat=0.5,
        availability={"features": {}, "present": [], "missing": []})
    assert got["ok"] is False
    assert got["refusal"] == "TARGET_HAS_NO_FITTED_MODEL", got
    assert c.calls == [], "nothing may be written on a refusal"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan")])
async def test_the_ledger_refuses_a_p_hat_that_is_not_a_probability(bad):
    c = _Conn()
    got = await inv.record_prediction(
        c, target=inv.T_COMPLETE, model_key="k", model_version="1",
        dataset_sha="d", condition_id=COND, source_trade_id=1,
        account="A1", predicted_at=T0, horizon_s=3600.0, p_hat=bad,
        availability={"features": {}, "present": [], "missing": []})
    assert got["ok"] is False, got
    assert c.calls == []


def test_the_feature_sha_changes_when_the_vector_does():
    a = inv.feature_sha({"x": 1.0, "y": 2.0})
    assert a == inv.feature_sha({"y": 2.0, "x": 1.0}), "order must not matter"
    assert a != inv.feature_sha({"x": 1.0, "y": 2.5})


# ── against the real schema ─────────────────────────────────────────

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


@pg
@pytest.mark.asyncio
async def test_the_table_refuses_a_prediction_that_is_not_prospective():
    """The whole point of the ledger. A row inserted WITH its label is not
    a prediction, and the schema -- not just the writer -- must say so."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            open("migrations/102_rn1x_model_predictions.sql").read())
        await conn.execute("DELETE FROM rn1x_model_predictions "
                           "WHERE condition_id = $1", COND)

        # A LABEL PRESENT AT INSERT TIME. This is the ledger's whole
        # point and the CHECK constraints do NOT catch it -- a CHECK
        # cannot tell an insert from an update, so the trigger does. I
        # watched this assertion not raise before adding it.
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO rn1x_model_predictions (experiment_id, target, "
                "model_key, model_version, dataset_sha, feature_sha, "
                "condition_id, source_trade_id, account, predicted_at, "
                "horizon_s, p_hat, outcome_known, outcome, outcome_at) "
                "VALUES ('E','T','k','1','d','f',$1,1,'A1',now(),3600,0.5,"
                "TRUE,1,now())", COND + "_early")

        # a probability outside [0, 1]
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO rn1x_model_predictions (experiment_id, target, "
                "model_key, model_version, dataset_sha, feature_sha, "
                "condition_id, source_trade_id, account, predicted_at, "
                "horizon_s, p_hat) VALUES ('E','T','k','1','d','f',$1,2,"
                "'A1',now(),3600,1.5)", COND)

        # an outcome that predates the prediction it judges
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO rn1x_model_predictions (experiment_id, target, "
                "model_key, model_version, dataset_sha, feature_sha, "
                "condition_id, source_trade_id, account, predicted_at, "
                "horizon_s, p_hat, outcome_known, outcome, outcome_at) "
                "VALUES ('E','T','k','1','d','f',$1,3,'A1',now(),3600,0.5,"
                "TRUE,1,now() - interval '1 hour')", COND)

        # the real path: record, then join a label afterwards
        avail = inv.feature_availability(_fill())
        got = await inv.record_prediction(
            conn, target=inv.T_COMPLETE, model_key="rn1_complement_1h",
            model_version="2", dataset_sha="abc123", condition_id=COND,
            source_trade_id=99, account="A1", predicted_at=T0,
            horizon_s=3600.0, p_hat=0.42, availability=avail)
        assert got["ok"] is True, got

        row = await conn.fetchrow(
            "SELECT id, outcome_known, outcome, p_hat, features_present, "
            "feature_sha FROM rn1x_model_predictions "
            "WHERE condition_id = $1 AND source_trade_id = 99", COND)
        assert row["outcome_known"] is False, "recorded before the outcome"
        assert row["outcome"] is None
        assert float(row["p_hat"]) == pytest.approx(0.42)
        assert len(row["features_present"]) == avail["feature_count"]

        # idempotent: the same entry cannot inflate the sample
        await inv.record_prediction(
            conn, target=inv.T_COMPLETE, model_key="rn1_complement_1h",
            model_version="2", dataset_sha="abc123", condition_id=COND,
            source_trade_id=99, account="A1", predicted_at=T0,
            horizon_s=3600.0, p_hat=0.42, availability=avail)
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_model_predictions "
            "WHERE condition_id = $1 AND source_trade_id = 99", COND) == 1

        # the label arrives later, by UPDATE
        await conn.execute(inv.JOIN_OUTCOME, row["id"], 1, T0 + 3600.0)
        after = await conn.fetchrow(
            "SELECT outcome_known, outcome FROM rn1x_model_predictions "
            "WHERE id = $1", row["id"])
        assert after["outcome_known"] is True and after["outcome"] == 1
    finally:
        await conn.execute("DELETE FROM rn1x_model_predictions "
                           "WHERE condition_id LIKE $1", COND + "%")
        await conn.close()
