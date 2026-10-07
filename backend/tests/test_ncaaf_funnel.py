"""THE NCAAF FUNNEL NAMES EVERY LOSS, PER EVENT (closeout).

Production 2026-10-06: 108 venue cfb events, 37 matched to a PinnAPI
fixture, 3 entered, and no per-event account of the rest. The funnel is
built stage by stage from the provider's own receipts (the discovery watch
row), the venue catalogue, the collector's valuations and the paper
decisions; every loss from one stage to the next is named and reconciles,
and the venue events no provider fixture matched are reported apart, each
with its reason."""
from __future__ import annotations

import asyncio
import json
import os

import asyncpg
import pytest

from sportsassets import ncaaf_funnel as NF

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

W = "football_team_full_game_winner"
SP = "football_team_full_game_spread"


def _watch():
    return {"provider": [
        {"fixture_id": 1, "state": "MATCHED", "venue_event_slug": "cfb-a"},
        {"fixture_id": 2, "state": "MATCHED", "venue_event_slug": "cfb-b"},
        {"fixture_id": 3, "state": "MATCHED", "venue_event_slug": "cfb-c"},
        {"fixture_id": 4, "state": "MATCHED", "venue_event_slug": "cfb-d"},
        {"fixture_id": 5, "state": "PARTICIPANT_MISMATCH",
         "candidates": ["cfb-e"], "home": "Miami (OH)", "away": "UMass"},
        {"fixture_id": 6, "state": "NO_VENUE_COUNTERPART"},
        {"fixture_id": 7, "state": "NORMALIZATION_FAILURE",
         "reason": "SPORT_NOT_MAPPED"}],
        "computed_at": 1.0, "pass_state": "COMPLETE"}


def _venue():
    return {s: {"contracts": [{"sports_type": W}, {"sports_type": SP}],
                "game_start": 2.0}
            for s in ("cfb-a", "cfb-b", "cfb-c", "cfb-d", "cfb-e", "cfb-f")}


def _built():
    vals = {
        # a: entered
        "cfb-a": [{"probability": 0.55, "age_s": 3.0,
                   "executable_price": 0.5, "refusals": []}],
        # b: valued, no executable book
        "cfb-b": [{"probability": 0.55, "age_s": 3.0,
                   "executable_price": None,
                   "refusals": ["VENUE_BOOK_READ_FAILED"]}],
        # c: never valued
        # d: settlement refused everywhere
        "cfb-d": [{"probability": 0.5, "age_s": 3.0, "executable_price": 0.5,
                   "refusals": ["VOID_ABANDONMENT_RULE_CONFLICTS_WITH_"
                                "BOOK_RULE"]}],
    }
    decs = {"cfb-a": [{"verdict": "ENTER", "refusal": None, "refusals": [],
                       "book_obs_id": 9, "pin_qualified": "true"}],
            "cfb-d": [{"verdict": "REFUSE",
                       "refusal": "SETTLEMENT_NOT_SUPPORTED", "refusals": [],
                       "book_obs_id": None, "pin_qualified": "true"}]}
    return NF.build(watch=_watch(), venue=_venue(), valuations=vals,
                    decisions=decs, now=10.0)


def test_every_stage_counts_and_every_loss_is_named_and_reconciles():
    out = _built()
    st = {r["stage"]: r for r in out["stages"]}
    assert st["PROVIDER_EVENT"]["count"] == 7
    assert st["FIXTURE_IDENTIFIED"]["count"] == 6
    assert st["FIXTURE_IDENTIFIED"]["losses"] == {"SPORT_NOT_MAPPED": 1}
    assert st["VENUE_EVENT_FOUND"]["count"] == 4
    assert st["VENUE_EVENT_FOUND"]["losses"] == {
        "PARTICIPANT_MISMATCH": 1, "NO_VENUE_COUNTERPART": 1}
    assert st["VENUE_CONTRACTS"]["count"] == 4
    assert st["ONTOLOGY_MAPPED"]["count"] == 4
    assert st["SETTLEMENT_SUPPORTED"]["count"] == 2
    assert st["SETTLEMENT_SUPPORTED"]["losses"] == {
        NF.R_NOT_VALUED: 1,
        "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE": 1}
    assert st["CURRENT_BOOK"]["losses"] == {"VENUE_BOOK_READ_FAILED": 1}
    assert st["APPROVED"]["count"] == 1
    assert out["unexplained_losses"] == []


def test_the_venue_side_names_why_each_unmatched_event_is_unmatched():
    vs = _built()["venue_side"]
    assert vs["venue_events"] == 6 and vs["matched"] == 4
    why = {u["event_slug"]: u["reason"] for u in vs["unmatched_events"]}
    assert why == {"cfb-e": NF.R_NAMED_BY_MISMATCH,
                   "cfb-f": NF.R_NO_PROVIDER_FIXTURE}
    e = next(u for u in vs["unmatched_events"] if u["event_slug"] == "cfb-e")
    assert e["provider_receipts"][0]["home"] == "Miami (OH)"


def test_without_the_watch_row_nothing_is_invented():
    out = NF.build(watch=None, venue=_venue(), valuations={}, decisions={},
                   now=1.0)
    assert out["watch"]["recorded"] is False
    assert {u["reason"] for u in out["venue_side"]["unmatched_events"]} == {
        NF.R_WATCH_UNAVAILABLE}


def test_freshness_is_the_unchanged_30s_rule():
    assert NF.FRESH_LIMIT_S == 30.0
    p = NF.event_progress(
        contracts=[{"sports_type": W}],
        valuations=[{"probability": 0.5, "age_s": 31.0,
                     "executable_price": 0.5,
                     "refusals": ["FEED_QUOTE_OLDER_THAN_LIMIT"]}],
        decisions=[])
    assert p["lost_at"] == "PROBABILITY_FRESH"
    assert p["reason"] == "FEED_QUOTE_OLDER_THAN_LIMIT"


def test_the_discovery_watch_keeps_every_watched_receipt():
    from sportsassets import pinnapi_discovery as PD
    rec = [{"fixture_id": i, "league": "NCAA", "state": "MATCHED",
            "venue_event_slug": "cfb-%d" % i} for i in range(5)] + [
        {"fixture_id": 99, "league": "Spain - La Liga", "state": "MATCHED"}]
    venue = {5: [{"slug": "cfb-1"}, {"slug": "cfb-x"}, {"slug": "nfl-y"}]}
    w = PD.watch_receipts(rec, venue, {(5, "cfb-1")}, sports={5})
    assert len(w["provider"]) == 5
    assert w["venue_unmatched"] == ["cfb-x"]


@pg
def test_the_read_runs_on_the_real_schema():
    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await c.execute(
                "INSERT INTO ingestion_state (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                "pinnapi_discovery_watch", json.dumps(_watch()))
            return await NF.read(c)
        finally:
            await tr.rollback()
            await c.close()
    out = asyncio.run(go())
    assert out["version"] == NF.VERSION and out["watch"]["recorded"]
    assert out["stages"][0]["count"] == 7
