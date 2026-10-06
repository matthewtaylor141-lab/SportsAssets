"""DEREK'S COVERAGE CENSUS AND WORKSPACE, MEASURED AND TRUTHFUL.

The census partitions every listed Polymarket US contract into exactly one
final state, so the states sum to `listed`, and keeps unsupported sports,
periods, simulated and non-sports markets VISIBLE by reason. It reads only
what is stored; it never invents a Pinnacle price and never counts an
international contract.

The workspace endpoint on an empty database returns EMPTY or UNAVAILABLE with
a named reason for every data section -- never an empty table styled as
success.

Isolation: the census test runs on TEMPORARY tables that shadow the shared
ones for its own session only; the workspace test runs in its own schema.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from sportsassets.agents import coverage as COV
from sportsassets.agents import derek_policy as DP

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")
BACKEND = Path(__file__).resolve().parents[1]
NOW = 1790600000.0                       # a controlled clock
EMPTY_SCHEMA = "derek_ws_empty_test"


def _row(slug, sports_type, *, event="mlb-bos-nyy-2026-10-01",
         title="Boston Red Sox vs. New York Yankees", q=None, age_s=60.0):
    return (slug, event, title, q or "Will %s win?" % slug, sports_type,
            NOW - age_s)


CATALOGUE = [
    # inside the mandate and supported: MLB full-game moneylines
    _row("aec-mlb-bos-nyy-2026-10-01-nyy", "baseball_team_full_game_winner"),
    _row("aec-mlb-bos-nyy-2026-10-01-bos", "baseball_team_full_game_winner"),
    _row("aec-mlb-sea-hou-2026-10-01-hou", "baseball_team_full_game_winner",
         event="mlb-sea-hou-2026-10-01",
         title="Seattle Mariners vs. Houston Astros"),
    _row("aec-mlb-tex-oak-2026-10-01-oak", "baseball_team_full_game_winner",
         event="mlb-tex-oak-2026-10-01",
         title="Texas Rangers vs. Oakland Athletics"),
    # outside the mandate, each for its own named reason
    _row("aec-mlb-bos-nyy-2026-10-01-spread-nyy-1pt5",
         "baseball_team_full_game_spread"),
    _row("aec-mlb-bos-nyy-2026-10-01-f5-nyy",
         "baseball_team_first_half_winner"),
    _row("aec-epl-ars-che-2026-10-01-ars", "soccer_team_full_time_winner",
         event="epl-ars-che-2026-10-01", title="Arsenal vs. Chelsea"),
    _row("aec-efb-ars-che-2026-10-01-ars", "efootball_team_full_time_winner",
         event="efb-ars-che-2026-10-01",
         title="Arsenal (eFootball) vs. Chelsea (eFootball)"),
    _row("aec-politics-election-2026", None, event="election-2026",
         title="Who wins the election?"),
    # a sport placed IN the mandate for this test, with no probability source
    # for THIS league. PIN MOVED (P0 coverage, 2026-10-06): this was an NBA
    # row; the NBA money line is now admitted to the de-vig by league
    # (SUPPORTED_BY_LEAGUE), so a league it does NOT admit -- the venue's
    # WNBA board, whose team record is the city alone -- carries the
    # unsupported case.
    _row("aec-wnba-ny-atl-2026-10-01-ny",
         "basketball_team_full_game_winner",
         event="wnba-ny-atl-2026-10-01",
         title="New York vs. Atlanta"),
    # not re-seen by the sweep recently: not listed at all
    _row("aec-mlb-old-2026-09-01-old", "baseball_team_full_game_winner",
         event="mlb-old-2026-09-01", age_s=10 * 3600.0),
]


async def _temp_tables(conn):
    await conn.execute("""
        CREATE TEMP TABLE us_premap (identifier text, event_slug text,
            event_title text, market_slug text, question text,
            sports_type text, game_start timestamptz,
            updated_at timestamptz NOT NULL);
        CREATE TEMP TABLE ingestion_state (key text PRIMARY KEY, value text);
        CREATE TEMP TABLE external_valuations (id bigserial,
            us_market_slug text, record_purpose text, refusals text[],
            admissible boolean, decided_at timestamptz);
        CREATE TEMP TABLE ext_candidate_outcomes (id bigserial,
            us_market_slug text, stage text, outcome text,
            first_refusal text, cycle_at timestamptz);
        CREATE TEMP TABLE derek_entry_decisions (decision_id text,
            us_market_slug text, verdict text, refusal text,
            decided_at timestamptz);
        CREATE TEMP TABLE derek_coverage_census (census_id text PRIMARY KEY,
            at timestamptz, categories jsonb, blocked_by_reason jsonb,
            sample jsonb);
    """)
    for slug, ev, title, q, st, upd in CATALOGUE:
        await conn.execute(
            "INSERT INTO us_premap VALUES ($1,$2,$3,$1,$4,$5,"
            " to_timestamp($6 + 86400), to_timestamp($6))",
            slug, ev, title, q, st, upd)
    await conn.execute(
        "INSERT INTO ingestion_state VALUES ('premap_last', $1)",
        json.dumps({"at": NOW - 300}))
    # EVALUATED: an entry decision, and Derek's verdict on it
    await conn.execute(
        "INSERT INTO external_valuations (us_market_slug, record_purpose, "
        " refusals, admissible, decided_at) VALUES "
        " ('aec-mlb-bos-nyy-2026-10-01-nyy','ENTRY_DECISION','{}',true,"
        "  to_timestamp($1)),"
        " ('aec-mlb-sea-hou-2026-10-01-hou','CALIBRATION_ONLY',"
        "  ARRAY['VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'],false,"
        "  to_timestamp($1))", NOW - 100)
    await conn.execute(
        "INSERT INTO derek_entry_decisions VALUES ('d1',"
        " 'aec-mlb-bos-nyy-2026-10-01-nyy','ENTER',NULL,to_timestamp($1))",
        NOW - 99)
    # BLOCKED before any valuation: the provider does not price it yet
    await conn.execute(
        "INSERT INTO ext_candidate_outcomes (us_market_slug, stage, outcome, "
        " first_refusal, cycle_at) VALUES "
        " ('aec-mlb-tex-oak-2026-10-01-oak','1_PROBABILITY','REFUSED',"
        "  'NO_PINNACLE_ON_EVENT', to_timestamp($1)),"
        " ('aec-not-in-catalogue','3_IDENTITY','REFUSED','X',"
        "  to_timestamp($1))", NOW - 200)


@pg
async def test_the_census_partitions_every_listed_contract_and_hides_none(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets.workers import ext_pinnacle_loop as L
    # A SPORT IN THE MANDATE WITH NO PROBABILITY SOURCE, so UNSUPPORTED is
    # exercised (basketball h2h is admitted for the NBA only).
    monkeypatch.setattr(L, "SPORTS", tuple(L.SPORTS) + (
        ("basketball_nba", "basketball"),))
    conn = await asyncpg.connect(DSN)
    try:
        await _temp_tables(conn)
        got = await COV.census(conn, now=NOW)
        assert got["ok"] is True, got
        cats = got["categories"]
        assert cats["listed"] == 10                       # the stale row is out
        assert cats["final_states_sum"] == cats["listed"]
        assert cats["sums_to_listed"] is True
        fs = cats["final_states"]
        assert fs == {"OUTSIDE_MANDATE": 5, "UNSUPPORTED": 1, "BLOCKED": 2,
                      "EVALUATED": 1, "NOT_YET_EVALUATED": 1}
        # THE FUNNEL NARROWS AND NEVER GROWS.
        assert cats["listed"] >= cats["within_mandate"] >= \
            cats["supported"] >= cats["receiving_current_data"] >= \
            cats["evaluated"]
        assert (cats["within_mandate"], cats["supported"],
                cats["receiving_current_data"], cats["evaluated"]) == \
            (5, 4, 2, 1)
        assert cats["blocked"] == 8
        assert cats["derek_verdicts_on_evaluated"]["ENTER"] == 1
        # UNSUPPORTED AND NON-SPORTS MARKETS STAY VISIBLE, BY NAME.
        br = got["blocked_by_reason"]
        assert br["UNSUPPORTED:NO_PROBABILITY_SOURCE_FOR_THIS_MARKET:"
                  "basketball"] == 1
        assert br["OUTSIDE_MANDATE:NOT_A_SPORTS_FIXTURE"] == 1
        assert br["OUTSIDE_MANDATE:SIMULATED_OR_ELECTRONIC_FIXTURE"] == 1
        assert br["OUTSIDE_MANDATE:SPORT_NOT_IN_THE_ENTRY_MANDATE:soccer"] == 1
        assert br["OUTSIDE_MANDATE:PERIOD_NOT_IN_THE_ENTRY_MANDATE:"
                  "FIRST_HALF"] == 1
        assert br["OUTSIDE_MANDATE:MARKET_TYPE_NOT_IN_THE_ENTRY_MANDATE:"
                  "baseball_team_full_game_spread"] == 1
        assert br["BLOCKED:NO_PINNACLE_ON_EVENT"] == 1
        assert br["BLOCKED:VALUED_FOR_CALIBRATION_ONLY:"
                  "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"] == 1
        assert got["sample"]["NOT_YET_EVALUATED"][0]["us_market_slug"] == \
            "aec-mlb-bos-nyy-2026-10-01-bos"
        assert cats["lane_records_not_in_the_current_catalogue"] == 1
        assert got["polymarket_us_only"] is True
        # NO PROBABILITY IS INVENTED FOR ANY CONTRACT.
        assert "probability" not in json.dumps(got["sample"])
        cid = await COV.record(conn, got)
        assert cid and cid.startswith("census:")
        stored = await conn.fetchrow(
            "SELECT categories FROM derek_coverage_census WHERE census_id=$1",
            cid)
        assert json.loads(stored["categories"])["listed"] == 10
    finally:
        await conn.close()


@pg
async def test_a_stale_or_absent_catalogue_is_named_not_counted():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            "CREATE TEMP TABLE us_premap (market_slug text, event_slug text,"
            " event_title text, question text, sports_type text, "
            " game_start timestamptz, updated_at timestamptz NOT NULL)")
        await conn.execute("CREATE TEMP TABLE ingestion_state "
                           "(key text PRIMARY KEY, value text)")
        got = await COV.census(conn, now=NOW)
        assert got["ok"] is True
        assert got["catalogue_sweep_fresh"] is False
        assert got["categories"]["listed"] == 0
        assert got["categories"]["sums_to_listed"] is True
    finally:
        await conn.close()


# ── THE WORKSPACE ON AN EMPTY DATABASE ────────────────────────────────────

async def _make_empty_schema():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS %s CASCADE" % EMPTY_SCHEMA)
        await conn.execute("CREATE SCHEMA %s" % EMPTY_SCHEMA)
        await conn.execute("SET search_path TO %s" % EMPTY_SCHEMA)
        await conn.execute(
            (BACKEND / "migrations" / "153_derek.sql").read_text())
    finally:
        await conn.close()


async def _drop_empty_schema():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS %s CASCADE" % EMPTY_SCHEMA)
    finally:
        await conn.close()


@pg
def test_the_workspace_on_an_empty_database_is_truthful(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sportsassets.api import agents_derek as A

    asyncio.run(_make_empty_schema())
    pools: list = []

    async def fake_get_pool():
        if not pools:
            pools.append(await asyncpg.create_pool(
                DSN, min_size=1, max_size=2,
                server_settings={"search_path": EMPTY_SCHEMA}))
        return pools[0]

    monkeypatch.setattr(A, "get_pool", fake_get_pool)
    app = FastAPI()
    app.include_router(A.router)

    @app.on_event("shutdown")
    async def _close():
        for p in pools:
            await p.close()

    try:
        # WITHOUT A CREDENTIAL THE READ IS REFUSED (the app's own check).
        with TestClient(app) as client:
            r = client.get("/api/command/agents/derek")
            assert r.status_code in (401, 403), r.status_code
        app.dependency_overrides[A.require_read] = lambda: "test"
        with TestClient(app) as client:
            r = client.get("/api/command/agents/derek")
            assert r.status_code == 200, r.text
            assert r.headers["cache-control"] == "no-store"
            body = r.json()
            assert body["read_only"] is True
            assert body["agent"]["agent_id"] == "DEREK"
            secs = body["sections"]
            required = ("status", "versions", "coverage", "subscription",
                        "opportunity_queue", "decisions", "plans_fills",
                        "handoffs", "latency", "performance", "collection")
            for name in required:
                assert name in secs, name
                s = secs[name]
                assert set(s) >= {"status", "why", "data", "evidence"}
                assert s["status"] in ("OK", "EMPTY", "UNAVAILABLE")
            # EVERY DATA SECTION IS EMPTY OR UNAVAILABLE, WITH ITS REASON.
            for name in required:
                if name == "versions":
                    continue
                s = secs[name]
                assert s["status"] in ("EMPTY", "UNAVAILABLE"), (name, s)
                assert s["why"], name
            assert secs["decisions"]["status"] == "EMPTY"
            assert secs["coverage"]["status"] == "EMPTY"
            assert secs["plans_fills"]["status"] == "UNAVAILABLE"
            assert secs["handoffs"]["status"] == "UNAVAILABLE"
            assert "152" in secs["handoffs"]["why"]
            # VERSIONS ARE CODE FACTS, and the model is honestly absent.
            v = secs["versions"]["data"]
            assert v["policy"]["version"] == DP.POLICY_VERSION
            assert v["policy"]["params"]["min_gross_edge_pp"] == 0.05
            # the unit is unambiguous: a probability difference, 0.05 = 5 pp
            assert v["policy"]["param_units"]["min_gross_edge_pp"] == (
                "PROBABILITY_DIFFERENCE_ON_A_0_TO_1_DOLLAR_CONTRACT "
                "(0.05 == 5 percentage points; never 5)")
            assert v["internal_model"]["state"] in ("NONE", "UNAVAILABLE")
            r = client.get("/api/command/agents/derek/decisions/nope")
            assert r.status_code == 404
            assert r.json()["detail"]["reason"] == "NO_SUCH_DECISION"
    finally:
        asyncio.run(_drop_empty_schema())


def test_dependency_classes_are_the_owners():
    """The five classes, and where each blocker lands."""
    assert set(DP.DEPENDENCY_CLASSES) == {
        "ENGINEERING_CONFIGURATION", "EVIDENCE", "ENGINEERING",
        "ELAPSED_TIME", "OWNER_DECISION"}
    assert DP.classify_lane_code("EXPERIMENT_NOT_ARMED") == \
        DP.DEP_ENGINEERING_CONFIGURATION
    assert DP.classify_lane_code("NO_RAIL_HEADROOM_FOR_ANY_POSITION") == \
        DP.DEP_OWNER_DECISION
    assert DP.classify_lane_code("RISK_GATE_BLOCKED") == DP.DEP_ELAPSED_TIME
    assert DP.classify_lane_code("NO_ACTION_HAS_POSITIVE_NET_EDGE") == \
        DP.DEP_NONE_MARKET
    assert DP.DEP_NONE_MARKET not in DP.DEPENDENCY_CLASSES


@pg
async def test_the_subscription_section_classifies_its_blockers():
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets.api import agents_derek as A
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("CREATE TEMP TABLE ingestion_state "
                           "(key text PRIMARY KEY, value text)")
        await conn.execute(
            "INSERT INTO ingestion_state VALUES ('ext_pinnacle_last_cycle', "
            " $1)", json.dumps({"at": time.time(), "market_subscription": {
                "subscription_state": "DISABLED_BY_CONFIGURATION",
                "why": "BETTOR_MARKET_SUBSCRIPTION is not set"}}))
        s = await A.subscription(conn)
        assert s["status"] == "OK"
        deps = {d["what"]: d["class"] for d in s["data"]["dependencies"]}
        assert deps["market-data subscription"] == \
            DP.DEP_ENGINEERING_CONFIGURATION
        from sportsassets import bettor_stream_currency as SC
        for m in SC.MISSING_PRECONDITIONS:
            assert deps[m] == DP.DEP_EVIDENCE
    finally:
        await conn.close()
