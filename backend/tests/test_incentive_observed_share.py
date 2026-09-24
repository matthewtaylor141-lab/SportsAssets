"""THE OBSERVATION DELIVERABLE: captured terms, OUR ladders, zero earned.

The opportunity script has only ever scored ladders from markets carrying
no incentive programme -- it says so in its own output. This module scores
the programme's own markets against the manifest's captured terms, through
the same `bettor_incentive_score` engine, and these tests pin the three
things that keep it honest:

    1 · it uses the SHIPPED engine or refuses -- it never re-implements
        the reward formula;
    2 · it reports the manifest/observed join BOTH ways, so a programme
        market we never saw and an observed market outside the programme
        are separate facts;
    3 · hypothetical share and earned rewards are separate fields, and
        earned is zero.
"""
import inspect
import os

import pytest

from sportsassets import bettor_incentive_observed_share as OS

RESEARCH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "research", "beta48")
MANIFEST = os.path.join(RESEARCH, "acceptance", "incentive_manifest.json")

pytestmark = pytest.mark.skipif(
    not os.path.exists(MANIFEST),
    reason="the captured manifest is not in this tree")


@pytest.fixture(autouse=True)
def _point_at_the_tree(monkeypatch):
    monkeypatch.setattr(OS, "RESEARCH_DIR", RESEARCH)
    monkeypatch.setattr(OS, "MANIFEST_PATH", MANIFEST)


class _Conn:
    """A journal holding ladders for the given slugs."""

    def __init__(self, slugs, frames=3, bids=None, offers=None):
        self._slugs = list(slugs)
        self._frames = frames
        self._bids = bids if bids is not None else [
            {"price": 0.40, "size": 500}, {"price": 0.39, "size": 800}]
        self._offers = offers if offers is not None else [
            {"price": 0.42, "size": 600}, {"price": 0.43, "size": 900}]

    async def fetch(self, sql, *args):
        if "GROUP BY slug" in sql:
            return [{"slug": s, "frames": self._frames,
                     "first_at": 1.0, "last_at": 2.0} for s in self._slugs]
        want = set(args[0]) if args else set(self._slugs)
        out = []
        for s in self._slugs:
            if s not in want:
                continue
            for i in range(self._frames):
                out.append({"slug": s, "at": 1.0 + i,
                            "payload": {"bids": self._bids,
                                        "offers": self._offers,
                                        "ladder_class": "UPDATE"}})
        return out


def _programme_slugs():
    import sys

    if RESEARCH not in sys.path:
        sys.path.insert(0, RESEARCH)
    import bettor_incentive_opportunity as opp

    return sorted(opp.programs_from_manifest(MANIFEST)["programs"])


def test_the_manifests_markets_are_the_ones_the_collector_observed():
    """If these ever diverge the deliverable is not available, and that is
    a finding rather than something to average over."""
    slugs = _programme_slugs()
    assert len(slugs) == 12
    assert all(s.startswith("ccpc-bilbrd-1album-any2026-") for s in slugs)


def test_it_scores_the_programme_ladders_and_earns_nothing():
    import asyncio

    slugs = _programme_slugs()
    got = asyncio.run(OS.observed_share(_Conn(slugs), clip=100.0,
                                        offsets=(0,)))
    assert got["ok"] is True, got
    assert got["matched_markets"] == slugs
    assert got["programme_markets_not_observed"] == []
    assert got["observed_markets_not_in_programme"] == []
    assert got["earned_rewards_usd"] == 0.0
    assert got["results"], "the engine produced no rows"
    for r in got["results"]:
        assert r["status"] == "HYPOTHETICAL_SHARE_ON_OBSERVED_PROGRAMME_LADDER"
        assert r["earned"] == 0.0
        # the engine's own counterfactual disclaimer travels with each row
        assert "not a forecast of earnings" in r["counterfactual"]


def test_the_captured_terms_are_reported_not_assumed():
    import asyncio

    slugs = _programme_slugs()
    got = asyncio.run(OS.observed_share(_Conn(slugs[:2]), offsets=(0,)))
    terms = got["captured_terms"]
    assert terms, "the terms must be shown with the numbers they produced"
    one = terms[sorted(terms)[0]]
    for field in ("program_id", "reward_pool", "discount_factor",
                  "target_size"):
        assert field in one, field


def test_a_market_outside_the_programme_is_named_not_scored():
    import asyncio

    slugs = _programme_slugs()
    conn = _Conn(slugs[:1] + ["aec-mlb-chc-mia-2026-09-24-cubs"])
    got = asyncio.run(OS.observed_share(conn, offsets=(0,)))
    assert got["observed_markets_not_in_programme"] == [
        "aec-mlb-chc-mia-2026-09-24-cubs"]
    assert all(r["market"] in slugs for r in got["results"]
               if "market" in r)


def test_no_intersection_refuses_rather_than_scoring_the_wrong_thing():
    import asyncio

    got = asyncio.run(OS.observed_share(_Conn(["not-a-programme-market"]),
                                        offsets=(0,)))
    assert got["ok"] is False
    assert got["refusal"] == "NO_OBSERVED_MARKET_IS_IN_THE_PROGRAMME"
    assert "scenario the script already labels" in got["why"]


def test_an_unreadable_level_is_dropped_not_defaulted_to_zero():
    """A level that cannot be read as two numbers must not become depth."""
    assert OS._levels([{"price": "x", "size": 1}]) == []
    assert OS._levels([{"price": 0.4, "size": 100}]) == [(0.4, 100.0)]
    assert OS._levels(None) == []
    assert OS._levels([[0.4, 100]]) == [(0.4, 100.0)]


def test_it_refuses_rather_than_reimplementing_the_scorer():
    src = inspect.getsource(OS)
    assert "SCORER_NOT_IN_IMAGE" in src
    assert "not re-implemented" in src
    # no local reward arithmetic
    assert "reward_pool *" not in src


def test_the_module_states_what_it_is_not_before_any_number():
    doc = OS.__doc__ or ""
    assert "NOTHING WAS EARNED" in doc
    assert "ONE PROGRAMME AND ONE EVENT" in doc
    assert "HYPOTHETICAL ORDER SHARE" in doc


def test_the_snapshot_read_is_bounded():
    assert OS.MAX_SNAPSHOTS_PER_MARKET <= 1000
    src = inspect.getsource(OS.observed_share)
    assert "MAX_SNAPSHOTS_PER_MARKET" in src
