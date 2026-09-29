"""ONLY A CURRENT MEASUREMENT OPENS THE CALIBRATION GATE; A WAIVED DECISION
NEVER REACHES THE FUNDED CONNECTOR.

Two holes from the production-prerequisite investigation (2026-09-29):

  * `source_calibration` read the NEWEST row and counted it, whatever wrote it:
    a hand-inserted row, one from a superseded evaluator, one below the
    evaluator's own minimum sample, or one measured long ago all opened
    MODEL_TRUST_DRIFT.
  * the research waiver applies whenever the calibration read is not a
    measurement -- including a READ FAILURE -- and `plan_from_decision` read
    only `admissible`, so a waived decision could be offered to the funded
    connector during an authorized window.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_source_calibration as CAL
from sportsassets.workers import ext_pinnacle_loop as L

DSN = os.environ.get("RN1X_TEST_DSN", "")
SOURCE = "CALIBRATION_GATE_TEST_SOURCE"


async def _seed(conn, *, evaluator=CAL.VERSION, sample=412, age_days=1.0,
                provenance=None):
    await conn.execute("DELETE FROM external_source_calibration "
                       " WHERE source_version=$1", SOURCE)
    prov = provenance if provenance is not None else (
        {"evaluator": evaluator, "supplied_by": "TEST_FIXTURE"}
        if evaluator is not None else {"note": "hand-inserted"})
    await conn.execute(
        "INSERT INTO external_source_calibration (source_version, measured_at,"
        " window_start, window_end, sample_size, metric, score, tolerance, "
        " within_tolerance, measured_by, provenance) VALUES ($1, now() - "
        " make_interval(secs => $2), now() - interval '90 days', now(), $3, "
        " 'BRIER', 0.21, 0.24, TRUE, 'CALIBRATION_GATE_TEST', $4::jsonb)",
        SOURCE, float(age_days) * 86400.0, int(sample), json.dumps(prov))


@pytest.mark.skipif(not DSN, reason="needs a migrated database")
@pytest.mark.parametrize("kw,refusal", [
    (dict(evaluator=None), "CALIBRATION_ROW_NOT_FROM_THE_CURRENT_EVALUATOR"),
    (dict(evaluator="EXTERNAL_SOURCE_CALIBRATION_V1"),
     "CALIBRATION_ROW_NOT_FROM_THE_CURRENT_EVALUATOR"),
    (dict(provenance="not json"),
     "CALIBRATION_ROW_NOT_FROM_THE_CURRENT_EVALUATOR"),
    (dict(sample=CAL.MIN_RESOLVED_EVENTS - 1),
     "CALIBRATION_ROW_BELOW_THE_EVALUATORS_MINIMUM"),
    (dict(age_days=L.CALIBRATION_MAX_AGE_S / 86400.0 + 1),
     "CALIBRATION_ROW_IS_STALE"),
])
async def test_a_row_that_is_not_a_current_measurement_does_not_open_the_gate(
        kw, refusal):
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        if kw.get("provenance") == "not json":
            kw = dict(kw, provenance="not json")
        await _seed(conn, **kw)
        got = await L.source_calibration(conn, SOURCE)
        assert got["measured"] is False, got
        assert got["error"] == refusal, got
        assert got["newest_row"]["measured_by"] == "CALIBRATION_GATE_TEST"
    finally:
        await conn.execute("DELETE FROM external_source_calibration "
                           " WHERE source_version=$1", SOURCE)
        await conn.close()


@pytest.mark.skipif(not DSN, reason="needs a migrated database")
async def test_a_current_measurement_opens_it():
    """THE POSITIVE CONTROL: the current evaluator, enough sample, recent."""
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        got = await L.source_calibration(conn, SOURCE)
        assert got["measured"] is True, got
        assert got["age_s"] == pytest.approx(86400.0, abs=120)
    finally:
        await conn.execute("DELETE FROM external_source_calibration "
                           " WHERE source_version=$1", SOURCE)
        await conn.close()


def _admitted(**over):
    rec = {"admissible": True, "us_market_slug": "aec-x", "event_key": "ev-x",
           "order_intent": FX.LONG,
           "execution_plan": {"execution": {"size": 10, "vwap": 0.5,
                                            "limit_price": 0.5}}}
    rec.update(over)
    return rec


@pytest.mark.parametrize("where", ["plan", "top", "risk"])
@pytest.mark.parametrize("authorised", [True, False])
def test_a_decision_that_rested_on_the_research_waiver_is_not_fundable(
        where, authorised):
    w = {"authorised": authorised, "waived": ["MODEL_TRUST_DRIFT"]}
    if where == "plan":
        rec = _admitted()
        rec["execution_plan"]["research_waiver"] = w
    elif where == "top":
        rec = _admitted(research_waiver=w)
    else:
        rec = _admitted()
        rec["execution_plan"]["risk"] = {"research_waiver": w}
    got = FX.plan_from_decision(rec)
    assert got["ok"] is False
    assert got["refusal"] == FX.R_RESEARCH_WAIVER_IS_NOT_FUNDABLE
    assert got["waived"] == ["MODEL_TRUST_DRIFT"]


def test_an_empty_waiver_record_is_not_a_waiver():
    """THE CONTROL: a record that CONSIDERED the waiver and waived nothing is
    judged on its other facts, not refused for mentioning it."""
    rec = _admitted()
    rec["execution_plan"]["research_waiver"] = {"authorised": True,
                                                "waived": []}
    got = FX.plan_from_decision(rec)
    assert got.get("refusal") != FX.R_RESEARCH_WAIVER_IS_NOT_FUNDABLE, got
