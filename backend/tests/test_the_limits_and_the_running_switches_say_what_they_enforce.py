"""THE LIMIT NAMES AN OWNER TYPES SAY WHAT THEY ENFORCE; THE RUNNING PROCESS
REPORTS ITS OWN SUBMISSION SWITCHES AND BUILD."""
from __future__ import annotations

import json
import os

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

CANONICAL = {"capital_usd": 250, "per_order_usd": 25, "event_exposure_usd": 50,
             "max_exposure_usd": 250, "daily_loss_stop_usd": 75}
ACCURATE = {"capital_usd": 250, "per_market_usd": 25,
            "event_exposure_usd": 50, "max_exposure_usd": 250,
            "cumulative_loss_stop_usd": 75}


def test_the_accurate_names_map_to_the_same_rails_and_digest():
    a = EX.effective_limits(FA.normalise_limit_keys(dict(CANONICAL)))
    b = EX.effective_limits(FA.normalise_limit_keys(dict(ACCURATE)))
    assert a["effective_digest"] == b["effective_digest"]
    assert a["effective"] == b["effective"]


def test_every_owner_field_states_its_enforced_meaning():
    assert set(FA.LIMIT_MEANINGS) == set(CANONICAL)
    assert "one market" in FA.LIMIT_MEANINGS["per_order_usd"]
    assert "No daily reset" in FA.LIMIT_MEANINGS["daily_loss_stop_usd"]
    assert "SAME total as capital_usd" in FA.LIMIT_MEANINGS["max_exposure_usd"]


def test_capital_and_correlated_exposure_measure_the_same_number():
    rows = [{"condition_id": "c1", "event_key": "e1", "cost_usd": 20.0,
             "qty": 30, "opened_at": 0}]
    got = EX.exposure_from_rows(rows, condition_id="c2", event_key="e2",
                                proposed_cost_usd=15.0, proposed_qty=20,
                                now=10.0)
    obs = got["observed"]
    assert obs["MAX_CAPITAL_DEPLOYED"] == obs["MAX_CORRELATED_EXPOSURE"]


@pg
@pytest.mark.asyncio
async def test_the_limit_writer_accepts_the_accurate_names_and_records_meanings():
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_desk_controls as CTL
    conn = await asyncpg.connect(DSN)
    try:
        prior = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", FA.LIMITS_KEY)
        got = await CTL.set_limits(conn, by="test", proposed=dict(ACCURATE))
        assert got["ok"] is True, got
        rec = got["stored"]
        assert set(rec["proposed"]) == set(CANONICAL)
        assert rec["proposed"]["per_order_usd"] == 25
        assert rec["proposed"]["daily_loss_stop_usd"] == 75
        assert "one market" in rec["meanings"]["per_order_usd"]
    finally:
        if prior is None:
            await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                               FA.LIMITS_KEY)
        else:
            await conn.execute(
                "UPDATE ingestion_state SET value=$2::jsonb WHERE key=$1",
                FA.LIMITS_KEY, prior if isinstance(prior, str)
                else json.dumps(prior))
        await conn.close()


def test_the_running_process_reports_its_three_switches():
    from sportsassets.api import app as A
    sw = A.running_switches()
    assert set(sw) == {"FUNDED_SUBMISSION_ENABLED",
                       "REAL_ORDER_SUBMISSION_ENABLED",
                       "FUNDED_EXIT_SUBMISSION_ENABLED"}
    assert not any(sw.values())      # the shipped state
