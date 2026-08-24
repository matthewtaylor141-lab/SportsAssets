"""Fuzzy-path side fidelity (wrong-side incident 2026-08-23/24).

Two proven leaks, both reproduced here so they can never return:
  1. A matchup title written 'A - B' or 'A @ B' slipped the ' vs' guard
     and containment scored EITHER team 1.0 — the parent (sideless)
     slug of a two-sided market won and the venue picked our side.
  2. The side loop took the argmax with no uniqueness rule — a tie fell
     to the venue's ordering instead of refusing.
"""

from sportsassets import pmus


def _two_sided(slug="aec-sra-fro-juv-2026-08-23",
               title="Frosinone Calcio - Juventus FC"):
    return {
        "slug": slug,
        "title": title,
        "question": "Who will win Frosinone Calcio - Juventus FC?",
        "marketSides": [
            {"identifier": f"{slug}-fro", "description": "Frosinone Calcio"},
            {"identifier": f"{slug}-juv", "description": "Juventus FC"},
        ],
    }


def _resolve(cands, outcome, monkeypatch):
    class _Markets:
        def retrieve_by_slug(self, slug):
            raise RuntimeError("404")

        def list(self, params):
            return {"markets": cands}

    class _Client:
        markets = _Markets()

    monkeypatch.setattr(pmus, "_get_client", lambda: _Client())
    return pmus.resolve_market(None, "ev-1", "Frosinone Calcio vs. Juventus FC",
                               None, outcome)


def test_two_sided_market_resolves_to_the_picked_side(monkeypatch):
    m = _resolve([_two_sided()], "Juventus FC", monkeypatch)
    assert m is not None
    assert m["market_slug"].endswith("-juv")


def test_other_side_resolves_to_other_identifier(monkeypatch):
    m = _resolve([_two_sided()], "Frosinone Calcio", monkeypatch)
    assert m is not None
    assert m["market_slug"].endswith("-fro")


def test_parent_slug_never_wins_when_sides_exist(monkeypatch):
    # even with a title-containment gift, the sideless parent must lose
    m = _resolve([_two_sided(title="Juventus FC special")],
                 "Juventus FC", monkeypatch)
    assert m is None or m["market_slug"] != "aec-sra-fro-juv-2026-08-23"


def test_ambiguous_sides_refuse(monkeypatch):
    amb = _two_sided()
    amb["marketSides"] = [
        {"identifier": "x-a", "description": "Manchester United"},
        {"identifier": "x-b", "description": "Leeds United"},
    ]
    # 'United' token boosts both sides to 0.9 — must refuse, not pick first
    m = _resolve([amb], "United", monkeypatch)
    assert m is None


def test_dash_matchup_title_never_votes():
    m = {"title": "Frosinone Calcio - Juventus FC"}
    assert pmus._outcome_score(m, "Juventus FC") < pmus.MATCH_FLOOR
    assert pmus._outcome_score(m, "Frosinone Calcio") < pmus.MATCH_FLOOR


def test_at_matchup_title_never_votes():
    m = {"title": "Yankees @ Red Sox"}
    assert pmus._outcome_score(m, "Red Sox") < pmus.MATCH_FLOOR


def test_plain_side_title_still_votes():
    m = {"title": "New York Yankees"}
    assert pmus._outcome_score(m, "New York Yankees") >= pmus.MATCH_FLOOR


def test_exact_path_never_returns_parent_with_sides(monkeypatch):
    # quarantine stream 2026-08-24: resolve_market_exact's first branch
    # returned parent aec- slugs when the parent itself passed the floor
    parent = _two_sided(slug="aec-wta-liltag-yulsta-2026-08-23",
                        title="Lilli Tagger")   # no ' vs' — title votes
    parent["marketSides"] = [
        {"identifier": "aec-wta-liltag-yulsta-2026-08-23-liltag",
         "description": "Lilli Tagger"},
        {"identifier": "aec-wta-liltag-yulsta-2026-08-23-yulsta",
         "description": "Yulia Starodubtseva"},
    ]

    class _Markets:
        def retrieve_by_slug(self, slug):
            return {"market": parent}

    class _Client:
        markets = _Markets()

    monkeypatch.setattr(pmus, "_get_client", lambda: _Client())
    m = pmus.resolve_market_exact(["aec-wta-liltag-yulsta-2026-08-23"],
                                  "Lilli Tagger")
    assert m is not None
    assert m["market_slug"].endswith("-liltag")   # the side, never the parent


def test_yes_no_pick_never_matches_a_team_side():
    assert pmus._outcome_score({"outcome": "Pumas UNAM"}, "No") == 0.0
    assert pmus._outcome_score({"outcome": "Pumas UNAM"}, "Yes") == 0.0
    assert pmus._outcome_score({"outcome": "No"}, "No") == 1.0
    assert pmus._outcome_score({"outcome": "Yes"}, "Yes") == 1.0
