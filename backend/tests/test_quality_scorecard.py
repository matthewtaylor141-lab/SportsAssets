"""THE QUALITY SCORECARD: five domains; every metric carries numerator,
denominator, sample, CI where applicable, trend, last measured, blocker and
next improvement; an unmeasured metric is UNAVAILABLE with a null value and a
reason -- never 0; profitability is UNPROVEN until the PREDECLARED forward
sample is sufficient.

ALL DATA IS SYNTHETIC TEST DATA, inside a transaction that is rolled back.
"""
from __future__ import annotations

import json
import time

import asyncpg
import pytest

from sportsassets.agents import quality_scorecard as Q
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
KEYS = {"id", "name", "value", "unit", "numerator", "denominator", "sample",
        "min_sample", "ci", "trend", "last_measured_at", "status", "why",
        "blocker", "next_improvement", "detail"}
STATUSES = {"MEASURED", "UNAVAILABLE", "INSUFFICIENT_SAMPLE"}


def test_the_forward_sample_rule_is_declared_in_code_before_its_sample():
    r = Q.FORWARD_SAMPLE_RULE
    assert r["verdict_until_sufficient"] == "UNPROVEN"
    assert Q._epoch_iso(r["forward_start"]) > Q._epoch_iso(r["declared_at"])
    assert r["min_positions"] >= 300 and r["min_calendar_days"] >= 30
    assert "one position per fixture" in r["independence"]


def test_profitability_stays_unproven_until_the_sample_is_sufficient():
    start = Q.FORWARD_START
    rows = [{"opened_at": start + i * 3600, "closed_at": start + i * 3600 + 1,
             "fixture": "fx-%d" % i, "realized_pnl_usd": 5.0}
            for i in range(299)]
    v = Q.forward_verdict(rows, now=start + 60 * 86400)
    assert v["verdict"] == "UNPROVEN" and not v["sufficient"]
    # positions opened BEFORE the forward start never count
    early = [{"opened_at": start - 10, "closed_at": start + 5,
              "fixture": "old-%d" % i, "realized_pnl_usd": 100.0}
             for i in range(500)]
    assert Q.forward_verdict(early, now=start + 60 * 86400)["positions"] == 0
    # dependent positions on one fixture count once
    dup = rows + [dict(rows[0], closed_at=rows[0]["closed_at"] + 5)]
    assert Q.forward_verdict(dup, now=start + 60 * 86400)["positions"] == 299
    # sufficient in count but not in days: still UNPROVEN
    full = rows + [dict(rows[0], fixture="fx-last")]
    assert Q.forward_verdict(full, now=start + 5 * 86400)["verdict"] == \
        "UNPROVEN"
    noisy = [dict(r, realized_pnl_usd=(5.0 if i % 2 else -4.0))
             for i, r in enumerate(full)]
    got = Q.forward_verdict(noisy, now=start + 60 * 86400)
    assert got["sufficient"] and got["verdict"] == \
        "SUPPORTED_BY_FORWARD_SAMPLE"
    losing = [dict(r, realized_pnl_usd=(-5.0 if i % 2 else 4.0))
              for i, r in enumerate(full)]
    assert Q.forward_verdict(losing, now=start + 60 * 86400)["verdict"] == \
        "NOT_SUPPORTED_BY_FORWARD_SAMPLE"


def test_intervals_and_trends():
    w = Q.wilson(8, 10)
    assert 0 < w["low"] < 0.8 < w["high"] < 1 and w["method"] == "WILSON"
    assert Q.wilson(0, 0) is None
    assert Q.mean_ci([1.0]) is None
    assert Q.median_ci([1, 2, 3]) is None
    m = Q.median_ci(list(range(100)))
    assert m["low"] < 49.5 < m["high"]
    assert Q.trend_of(0.5, 0.4)["direction"] == "IMPROVING"
    assert Q.trend_of(0.5, 0.4, higher_is_better=False)["direction"] == \
        "DETERIORATING"
    assert Q.trend_of(0.5, None)["why"] == "PRIOR_PERIOD_UNMEASURED"


def test_brier_is_paired_and_lower_is_better():
    rows = [{"p": 0.9, "b": 0.6, "y": 1}, {"p": 0.1, "b": 0.4, "y": 0},
            {"p": 0.8, "b": 0.5, "y": 1}]
    b = Q.brier(rows)
    assert b["n"] == 3 and b["model"] < b["baseline"] and b["diff"] < 0
    assert Q.brier([])["diff"] is None


def test_sha_alignment_never_guesses():
    full = "a" * 40
    assert Q.sha_alignment(full, full) == {"verdict": "ALIGNED",
                                           "matched_how": "EXACT"}
    assert Q.sha_alignment(full, "b" * 40)["verdict"] == "MISALIGNED"
    assert Q.sha_alignment(full, "aaaaaaa")["matched_how"] == "PREFIX_7"
    assert Q.sha_alignment(None, full)["verdict"] is None
    assert Q.sha_alignment(full, "?")["why"] == "WORKERS_SHA_UNKNOWN"


def test_gate_artifacts_are_read_when_present(tmp_path):
    none = Q.gate_metric(path_spec="")
    assert [m["status"] for m in none] == ["UNAVAILABLE", "UNAVAILABLE"]
    assert all(m["value"] is None for m in none)
    p = tmp_path / "run_report.json"
    p.write_text(json.dumps({"session_complete": True, "exitstatus": 1,
                             "counts": {"passed": 90, "failed": 3},
                             "executed_count": 93, "finished_at": 1.0e9}))
    got = {m["id"]: m for m in Q.gate_metric(path_spec=str(tmp_path /
                                                           "*_report.json"))}
    assert got["failing_tests"]["value"] == 3
    assert got["failing_tests"]["denominator"] == 93
    assert got["gate_result"]["value"] == "COMPLETE"


async def _valuation(conn, ev, *, p, price, y, at):
    vid = await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, "
        " contract_selection, sport_family, market, raw_odds, "
        " outcomes_priced, expected_outcomes, decision, admissible, "
        " record_purpose, event_key, us_market_slug, decided_at, probability,"
        " executable_price) VALUES ('TEST_QUALITY','v',"
        " 'EXTERNAL_BOOKMAKER_VALUATION','test','pinnacle','power',"
        " 'polymarket_us','home','baseball','h2h','{}'::jsonb,2,2,'NO_TRADE',"
        " false,'ENTRY_DECISION',$1,$2,to_timestamp($3),$4,$5) RETURNING id",
        ev, "us-q-%s" % ev, at, p, price)
    await conn.execute(
        "UPDATE external_valuations SET outcome_known=true, outcome=$2, "
        " outcome_at=to_timestamp($3) WHERE id=$1", vid, y, at + 3600)


@pg
@pytest.mark.asyncio
async def test_the_scorecard_shape_and_its_honesty():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        now = time.time()
        # a calibration sample the decision probability wins on
        for i in range(40):
            y = i % 2
            await _valuation(conn, "q-%d" % i, p=0.8 if y else 0.2,
                             price=0.5, y=y, at=now - 86400 - i)
        card = await Q.scorecard(conn, now=now)
        assert set(card["domains"]) == set(Q.DOMAINS)
        ids = set()
        for dom, ms in card["domains"].items():
            assert ms, dom
            for m in ms:
                assert set(m) == KEYS, (dom, m["id"])
                assert m["status"] in STATUSES, m
                ids.add(m["id"])
                if m["status"] == "UNAVAILABLE":
                    # NEVER INVENTED: no value, a named reason
                    assert m["value"] is None or m["id"].startswith(
                        "forward_sample"), m
                    assert m["why"] or m["blocker"], m
                if m["ci"] is not None:
                    assert m["ci"]["method"] and m["ci"]["level"] == 0.95
        for need in ("gate_result", "failing_tests", "deploy_sha_alignment",
                     "review_latency_s", "reviews_fresh_share",
                     "peer_challenge_refuted_share", "karen_challenges",
                     "calibration_brier_skill", "paper_fill_rate",
                     "actual_fill_rate", "paper_slippage_cents",
                     "admission_refusal_share", "forward_sample_paper",
                     "forward_sample_actual"):
            assert need in ids, need
        cal = next(m for m in card["domains"]["INVESTMENT_INTELLIGENCE"]
                   if m["id"] == "calibration_brier_skill")
        assert cal["status"] == "MEASURED" and cal["sample"] >= 40
        assert cal["value"] < 0 and cal["ci"]["high"] < 0
        assert cal["trend"]["prior_value"] is None or \
            isinstance(cal["trend"]["prior_value"], float)
        assert card["profitability"]["paper"] == "UNPROVEN"
        assert card["profitability"]["actual"] == "UNPROVEN"
        assert card["profitability"]["rule"]["id"] == \
            "BETTOR_FORWARD_SAMPLE_V1"
        if not await conn.fetchval(
                "SELECT count(*) FROM pg_class WHERE relname LIKE 'karen%'"):
            k = next(m for m in card["domains"]["AGENTS"]
                     if m["id"] == "karen_challenges")
            assert k["status"] == "UNAVAILABLE" and k["value"] is None
    finally:
        await tx.rollback()
        await conn.close()
