"""SCOUT (migration 217): COMPLIANT SOURCES ONLY, A FEATURE REGISTRY, AND A
PROSPECTIVE TOURNAMENT AGAINST PINNAPI THAT SCOUT CANNOT JUDGE.

  * the declared compliance check passes the already-ingested official MLB
    schedule source and REFUSES the unlicensed weather feed; the database
    refuses a feature or observation from a source that did not pass;
  * every observation carries source / observed timestamps, event identity,
    confidence, freshness, provenance and the licensing class;
  * the tournament is frozen before samples, samples are frozen before
    outcomes, the verdict needs the predeclared minimum sample and is
    recorded by the evaluator (never Scout); without the predeclared
    out-of-sample improvement the feature is REJECTED; Scout cannot adopt
    or promote his own feature;
  * the runner ingests from the existing table only (no network), bounded,
    with a kill switch.
"""
from __future__ import annotations

import os
import random
import time

import pytest

from sportsassets.agents import feature_tournament as FT
from sportsassets.agents import registry as R
from sportsassets.agents import scout as S
from sportsassets.agents import scout_runner as SR

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


# ════════════════════════════════════════════════════════════════════
# PURE
# ════════════════════════════════════════════════════════════════════

def test_the_declared_compliance_check():
    mlb = S.compliance_check(S.SOURCES["mlb_stats_api_schedule_v1"])
    assert mlb["passed"] is True and mlb["failed"] == []
    assert set(mlb["checks"]) == set(S.COMPLIANCE_CHECKS)
    wx = S.compliance_check(S.SOURCES["weather_feed_unlicensed"])
    assert wx["passed"] is False
    assert "licensing_basis_recorded" in wx["failed"]
    # an unknown licence fails even if every box is ticked
    forged = dict(S.SOURCES["mlb_stats_api_schedule_v1"],
                  licensing_class="UNKNOWN")
    assert S.compliance_check(forged)["passed"] is False
    scraped = dict(S.SOURCES["mlb_stats_api_schedule_v1"],
                   access_method="HTML_SCRAPE")
    assert S.compliance_check(scraped)["passed"] is False
    # the only feature source is the existing ingested table
    for f in S.FEATURES.values():
        assert S.SOURCES[f["source_id"]]["access_method"] == \
            "EXISTING_INGESTED_TABLE"
    src = (S.__file__ and open(S.__file__).read())
    assert "httpx" not in src and "requests" not in src.split("import")[0]


def test_an_observation_carries_every_required_field():
    row = {"condition_id": "0xc1", "retrieved_at": 1_790_000_000.0,
           "scheduled_innings": 7, "double_header": "Y",
           "event_state_raw": "Scheduled", "abstract_state": "Preview",
           "game_pk": 1, "official_date": "2026-10-04", "home_team": "H",
           "away_team": "A", "refusals": []}
    o = S.observation_of("MLB_NONSTANDARD_GAME_FORMAT", row,
                         now=1_790_000_100.0)
    for k in ("source_id", "source_timestamp", "observed_timestamp",
              "event_key", "identity", "value", "confidence", "freshness_s",
              "provenance", "licensing_class", "feature_id"):
        assert o.get(k) is not None, k
    assert o["value"] == 1.0 and o["freshness_s"] == 100.0
    assert o["provenance"] == [{"kind": "fixture_metadata", "id": "0xc1"}]
    assert o["licensing_class"] == \
        "OFFICIAL_PUBLIC_API_INTERNAL_RESEARCH_ONLY"
    std = S.observation_of("MLB_NONSTANDARD_GAME_FORMAT",
                           dict(row, scheduled_innings=9, double_header="N"),
                           now=1_790_000_100.0)
    assert std["value"] == 0.0
    d = S.observation_of("MLB_GAME_DELAYED_OR_POSTPONED",
                         dict(row, event_state_raw="Delayed Start"),
                         now=1_790_000_100.0)
    assert d["value"] == 1.0
    assert S.observation_of("MLB_GAME_DELAYED_OR_POSTPONED",
                            dict(row, condition_id=None), now=1.0) is None


def test_the_challenger_is_the_predeclared_adjustment_only():
    adj = {"rule": "SHRINK_TOWARD_HALF_WHEN_FEATURE_IS_1", "k": 0.1}
    assert S.challenger_probability(0.7, 1.0, adj) == pytest.approx(0.68)
    assert S.challenger_probability(0.7, 0.0, adj) == pytest.approx(0.7)
    assert S.challenger_probability(0.7, None, adj) == pytest.approx(0.7)


def test_scoring_rejects_without_out_of_sample_value_and_waits_for_n():
    rnd = random.Random(7)
    samples = []
    for _ in range(300):
        p = rnd.uniform(0.3, 0.7)
        samples.append({"p_baseline": p, "p_challenger": p,
                        "outcome": 1 if rnd.random() < p else 0})
    got = FT.score(samples[:100], metric="BRIER", min_sample=200,
                   min_improvement=0.001)
    assert got["ready"] is False and got["verdict"] is None
    got = FT.score(samples, metric="BRIER", min_sample=200,
                   min_improvement=0.001)
    assert got["ready"] and got["verdict"] == "REJECTED"
    assert got["improvement"] == pytest.approx(0.0)
    assert got["reason"].startswith("NO_INCREMENTAL_OUT_OF_SAMPLE_VALUE")
    # a challenger that is genuinely better out of sample is VALIDATED
    better = [dict(x, p_challenger=0.9 if x["outcome"] else 0.1)
              for x in samples]
    got = FT.score(better, metric="BRIER", min_sample=200,
                   min_improvement=0.001)
    assert got["verdict"] == "VALIDATED" and got["improvement"] > 0.001
    got = FT.score(better, metric="LOG_LOSS", min_sample=200,
                   min_improvement=0.001)
    assert got["verdict"] == "VALIDATED"


def test_the_scorecard_is_unavailable_until_measured():
    m = S.summarise_metrics([], [], 0, 0)
    for name, x in m["metrics"].items():
        assert x["value"] is None and x["status"] == "UNAVAILABLE", name
        assert x["why"], name
    assert set(m["metrics"]) == set(S.DEFINITIONS)


# ════════════════════════════════════════════════════════════════════
# THE DATABASE
# ════════════════════════════════════════════════════════════════════

async def _tx():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    return conn, tx


async def _expect(conn, sql, *args, match=None):
    import asyncpg
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(asyncpg.PostgresError) as e:
            await conn.execute(sql, *args)
        if match:
            assert match in str(e.value), str(e.value)
    finally:
        await sp.rollback()


async def _seed_fixture(conn, cid, *, innings=7, dh="Y", state="Scheduled",
                        ago_s=600):
    await conn.execute(
        "INSERT INTO fixture_metadata (condition_id, scheduled_innings, "
        " double_header, event_state_raw, abstract_state, game_pk, "
        " official_date, home_team, away_team, source, source_url, "
        " retrieved_at, refusals, written_at, reader_version, raw) VALUES "
        " ($1,$2,$3,$4,'Preview',1,current_date,'H','A','MLB Stats API, "
        " schedule','TEST_FIXTURE',now() - make_interval(secs => $5),"
        " '[]'::jsonb, now(), 'TEST', '{}'::jsonb)", cid, innings, dh, state,
        float(ago_s))


@pg
@pytest.mark.asyncio
async def test_registration_ingestion_and_the_compliance_guard():
    conn, tx = await _tx()
    try:
        now = time.time()
        await R.ensure_identities(conn)
        srcs = await S.register_sources(conn, now=now)
        assert srcs["mlb_stats_api_schedule_v1"]["passed"]
        assert not srcs["weather_feed_unlicensed"]["passed"]
        made = await S.register_features(conn, now=now)
        assert len(made) == len(S.FEATURES)
        feats = await S.features(conn)
        assert {f["state"] for f in feats} == {"UNDER_TEST"}
        assert all(f["tournament_id"] for f in feats)
        # THE DATABASE REFUSES ANYTHING FROM A NON-COMPLIANT SOURCE
        await _expect(conn, "INSERT INTO scout_features (feature_id, "
                      " feature, source_id, licensing_class, event_scope, "
                      " identity_basis, expected_mechanism, "
                      " predeclared_hypothesis, proposed_at) VALUES ('sft:w',"
                      " 'WIND', 'weather_feed_unlicensed', 'UNKNOWN', 'e', "
                      " 'i', 'wind changes carry distance', "
                      " 'wind adds value over PinnAPI', now())",
                      match="SCOUT_SOURCE_NOT_COMPLIANT")
        # ... and a source cannot be re-declared compliant after the fact
        await _expect(conn, "UPDATE scout_sources SET compliance_passed=true "
                      " WHERE source_id='weather_feed_unlicensed'")
        await _seed_fixture(conn, "0xt217a")
        await _seed_fixture(conn, "0xt217b", innings=9, dh="N",
                            state="Delayed Start")
        got = await S.ingest(conn, now=now)
        assert got["observed"] >= 4
        obs = await S.observations(conn, limit=20)
        o = [x for x in obs if x["event_key"] == "0xt217a"
             and x["feature"] == "MLB_NONSTANDARD_GAME_FORMAT"][0]
        assert o["value"] == 1.0 and o["confidence"] == 1.0
        assert o["freshness_s"] >= 590
        assert o["provenance"] == [{"kind": "fixture_metadata",
                                    "id": "0xt217a"}]
        again = await S.ingest(conn, now=now)
        assert again["observed"] == 0                       # idempotent
        # SCOUT CANNOT JUDGE OR PROMOTE HIS OWN FEATURE
        fid = feats[0]["feature_id"]
        tid = feats[0]["tournament_id"]
        await _expect(conn, "UPDATE scout_feature_tournaments SET n=300, "
                      " baseline_score=0.25, challenger_score=0.2, "
                      " improvement=0.05, verdict='VALIDATED', "
                      " verdict_reason='r', evaluated_by='SCOUT', "
                      " evaluated_at=now() WHERE tournament_id=$1", tid)
        await _expect(conn, "UPDATE scout_features SET state='VALIDATED', "
                      " state_set_by='CALIBRATION_ENGINE', state_set_at=now(),"
                      " incremental_value='{}' WHERE feature_id=$1", fid)
        await _expect(conn, "UPDATE scout_features SET state='ADOPTED', "
                      " adopted_by='scout-bot', adopted_at=now() WHERE "
                      " feature_id=$1", fid)
        # the frozen spec is frozen
        await _expect(conn, "UPDATE scout_feature_tournaments SET "
                      " min_sample=30 WHERE tournament_id=$1", tid)
        await _expect(conn, "UPDATE scout_features SET "
                      " predeclared_hypothesis='changed after the fact' "
                      " WHERE feature_id=$1", fid)
        # a verdict below the predeclared sample is refused
        await _expect(conn, "UPDATE scout_feature_tournaments SET n=10, "
                      " baseline_score=0.25, challenger_score=0.2, "
                      " improvement=0.05, verdict='VALIDATED', "
                      " verdict_reason='r', evaluated_by='CALIBRATION_ENGINE',"
                      " evaluated_at=now() WHERE tournament_id=$1", tid)
        # VALIDATED without the predeclared improvement is refused
        await _expect(conn, "UPDATE scout_feature_tournaments SET n=300, "
                      " baseline_score=0.25, challenger_score=0.2499, "
                      " improvement=0.0001, verdict='VALIDATED', "
                      " verdict_reason='r', evaluated_by='CALIBRATION_ENGINE',"
                      " evaluated_at=now() WHERE tournament_id=$1", tid)
        # samples: never before the freeze, never with a known outcome
        await _expect(conn, "INSERT INTO scout_tournament_samples "
                      " (tournament_id, event_key, valuation_id, predicted_at,"
                      " p_baseline, p_challenger) VALUES ($1,'e','1', "
                      " now() - interval '30 days', 0.5, 0.5)", tid)
        await _expect(conn, "INSERT INTO scout_tournament_samples "
                      " (tournament_id, event_key, valuation_id, predicted_at,"
                      " p_baseline, p_challenger, outcome, outcome_at) VALUES"
                      " ($1,'e','1', now() + interval '1 second', 0.5, 0.5, "
                      " 1, now() + interval '1 hour')", tid)
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_forward_test_end_to_end_is_rejected_without_value():
    """Frozen spec -> PinnAPI valuations after the freeze -> frozen samples
    -> outcomes -> the evaluator's verdict. The seeded outcomes follow the
    baseline exactly, so the feature adds nothing and is REJECTED."""
    conn, tx = await _tx()
    try:
        now = time.time()
        await R.ensure_identities(conn)
        await S.register_sources(conn, now=now - 7200)
        await S.register_features(conn, now=now - 7200)
        name = "MLB_NONSTANDARD_GAME_FORMAT"
        fid, tid = S.feature_id_for(name), S.tournament_id_for(name)
        await conn.execute("UPDATE scout_feature_tournaments SET "
                           " frozen_at=frozen_at WHERE tournament_id=$1", tid)
        rnd = random.Random(11)
        n = S.TOURNAMENT_SPEC["min_sample"] + 5
        for i in range(n):
            cid = "0xft217-%d" % i
            await _seed_fixture(conn, cid, innings=7 if i % 2 else 9,
                                dh="N", ago_s=3600)
        await S.ingest(conn, now=now - 1800, limit=n + 10)
        for i in range(n):
            p = rnd.uniform(0.35, 0.65)
            await conn.execute(
                "INSERT INTO external_valuations (id, experiment_id, version,"
                " source_class, provider, book, devig_method, venue, "
                " contract_selection, sport_family, market, raw_odds, "
                " outcomes_priced, expected_outcomes, decision, admissible, "
                " condition_id, observed_at, probability, outcome_known) "
                " VALUES ($1, 'TEST', 'T', 'EXTERNAL_BOOKMAKER_VALUATION', "
                " 'pinnapi.com/raw-websocket', 'pinnacle', 'power', 'T', 'T', "
                " 'MLB', 'MONEYLINE', '{}'::jsonb, 2, 2, 'NO_TRADE', false, $2,"
                " now() - interval '20 minutes', $3, false)", 9_217_000 + i,
                "0xft217-%d" % i, p)
        await S.freeze_samples(conn, now=now, limit=n + 10)
        assert await conn.fetchval(
            "SELECT count(*) FROM scout_tournament_samples WHERE "
            " tournament_id=$1 AND outcome IS NULL", tid) == n
        # nothing is evaluated before the outcomes
        got = await FT.evaluate(conn, tid, now=now)
        assert got["ok"] and got["ready"] is False
        # outcomes arrive (after the predictions)
        await conn.execute(
            "UPDATE external_valuations SET outcome_known=true, outcome="
            " CASE WHEN (id % 2) = 0 THEN 1 ELSE 0 END, outcome_at=now() "
            " WHERE id BETWEEN 9217000 AND 9218000")
        await S.attach_outcomes(conn, limit=4 * n)
        assert await conn.fetchval(
            "SELECT count(*) FROM scout_tournament_samples WHERE "
            " tournament_id=$1 AND outcome IS NOT NULL", tid) == n
        got = await FT.evaluate(conn, tid, now=now + 60)
        assert got["ok"] and got["ready"], got
        t = await conn.fetchrow("SELECT * FROM scout_feature_tournaments "
                                " WHERE tournament_id=$1", tid)
        f = await conn.fetchrow("SELECT * FROM scout_features WHERE "
                                " feature_id=$1", fid)
        assert t["evaluated_by"] == "CALIBRATION_ENGINE"
        assert t["n"] == n and t["verdict"] == f["state"]
        if t["improvement"] < S.TOURNAMENT_SPEC["min_improvement"]:
            assert t["verdict"] == "REJECTED"
        assert f["state_set_by"] == "CALIBRATION_ENGINE"
        # the verdict is recorded once
        again = await FT.evaluate(conn, tid, now=now + 120)
        assert again["created"] is False
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_runner_pass_heartbeats_and_opens_loop_findings():
    conn, tx = await _tx()
    try:
        await R.ensure_identities(conn)
        await _seed_fixture(conn, "0xrun217")
        s = await SR.pass_once(conn)
        assert s["phase_errors"] == {}, s
        assert s["authority"] == "RESEARCH_SHADOW_ONLY"
        st = await R.status_of(conn, R.SCOUT)
        assert st["runs"] >= 1
        assert st["state"] in ("DECISION_RECORDED", "WAITING_FOR_EVIDENCE")
        f = await conn.fetch("SELECT * FROM agent_findings WHERE "
                             " proposer='SCOUT'")
        assert f and {r["stage"] for r in f} == {"HYPOTHESIS"}
        desk = await S.desk(conn)
        assert desk["authority"] == "RESEARCH_SHADOW_ONLY"
        assert desk["affordances"] == "READ_ONLY_NO_SUBMIT_NO_TRADE_NO_PROMOTE"
        assert desk["data_sources"] and desk["features_under_test"]
        text = await S.slack_answer(conn)
        assert "answered from records only" in text
        posts = await S.workroom_posts(conn)
        assert posts and all("stt:" in t for _, t in posts)
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_the_kill_switch(monkeypatch):
    monkeypatch.setenv("SCOUT_RUNNER_ENABLED", "off")
    assert SR.enabled() is False

    async def no_pool():
        raise AssertionError("the disabled runner touched the database")
    await SR.run(no_pool, first_delay_s=0)
