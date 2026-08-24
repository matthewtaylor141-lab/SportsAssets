"""Pre-map: deterministic keys and unique-side matching (owner order
2026-08-24: copy-time mapping becomes a lookup; every precision rule
here encodes a shipped wrong-side incident)."""

from sportsassets.workers import premap


def _rows():
    return [
        {"identifier": "aec-wta-liltag-yulsta-2026-08-24-liltag",
         "side_norm": "lilli tagger", "kind": "side", "line": ""},
        {"identifier": "aec-wta-liltag-yulsta-2026-08-24-yulsta",
         "side_norm": "yulia starodubtseva", "kind": "side", "line": ""},
    ]


def test_keys_symmetric_for_both_player_orders():
    k1 = premap.event_keys_for("Lilli Tagger vs Yulia Starodubtseva")
    k2 = premap.event_keys_for("Yulia Starodubtseva vs Lilli Tagger")
    assert set(k1) & set(k2)


def test_keys_strip_tournament_prefix():
    k = premap.event_keys_for("WTA Cleveland: Lilli Tagger vs Yulia Starodubtseva")
    assert any("tagger" in x for x in k)


def test_named_pick_matches_unique_side():
    hit = premap.match_side(_rows(), "Lilli Tagger", None)
    assert hit and hit["identifier"].endswith("-liltag")


def test_surname_token_matches_abbreviated_side():
    rows = [
        {"identifier": "x-a", "side_norm": "tagger l", "kind": "side", "line": ""},
        {"identifier": "x-b", "side_norm": "starodubtseva y", "kind": "side", "line": ""},
    ]
    hit = premap.match_side(rows, "Lilli Tagger", None)
    assert hit and hit["identifier"] == "x-a"


def test_shared_surname_is_ambiguous_and_refuses():
    rows = [
        {"identifier": "x-a", "side_norm": "aoi ito", "kind": "side", "line": ""},
        {"identifier": "x-b", "side_norm": "mai ito", "kind": "side", "line": ""},
    ]
    assert premap.match_side(rows, "Ito", None) is None


def test_yes_no_only_matches_literal_yes_no():
    """A Yes/No pick never matches a NAMED side (inversion incident
    2026-08-24), and since 2026-08-24 round 3 it also requires the
    QUESTION to correspond: every derivative on an event shares one key
    set, so a bare 'Yes' names nothing on its own."""
    q = "Will Pumas win?"
    rows = [
        {"identifier": "x-pum", "side_norm": "pumas unam", "kind": "side",
         "line": "", "question": q},
        {"identifier": "x-nec", "side_norm": "necaxa", "kind": "side",
         "line": "", "question": q},
    ]
    assert premap.match_side(rows, "No", q) is None
    rows.append({"identifier": "x-no", "side_norm": "no", "kind": "side",
                 "line": "", "question": q})
    hit = premap.match_side(rows, "No", q)
    assert hit and hit["identifier"] == "x-no"


def test_a_yes_no_pick_without_a_question_is_unbettable():
    """No question means no proposition — refuse rather than guess
    which of the event's derivatives the whale meant."""
    rows = [{"identifier": "x-no", "side_norm": "no", "kind": "side",
             "line": "", "question": "Will Pumas win?"}]
    assert premap.match_side(rows, "No", None) is None


def test_a_yes_no_pick_never_crosses_to_another_proposition():
    """The reproduction: a whale's Yes on 'Will both teams score?'
    matched the CLEAN SHEET market's Yes — a different bet entirely."""
    rows = [{"identifier": "astatc-cleansheet", "side_norm": "yes",
             "kind": "side", "line": "",
             "question": "Will Arsenal keep a clean sheet?"}]
    assert premap.match_side(rows, "Yes",
                             "Will both teams score?") is None


def test_over_under_requires_line_equality():
    rows = [
        {"identifier": "t-05-o", "side_norm": "over 0 5", "kind": "side", "line": "0.5"},
        {"identifier": "t-25-o", "side_norm": "over 2 5", "kind": "side", "line": "2.5"},
    ]
    hit = premap.match_side(rows, "Over", "Fulham vs Chelsea: O/U 2.5")
    assert hit and hit["identifier"] == "t-25-o"
    assert premap.match_side(rows, "Over", "Fulham vs Chelsea") is None


def test_over_with_line_in_outcome():
    rows = [
        {"identifier": "t-25-o", "side_norm": "over 2 5", "kind": "side", "line": "2.5"},
        {"identifier": "t-25-u", "side_norm": "under 2 5", "kind": "side", "line": "2.5"},
    ]
    hit = premap.match_side(rows, "Over 2.5", "Fulham vs Chelsea")
    assert hit and hit["identifier"] == "t-25-o"


def test_lined_row_never_matches_unlined_named_pick():
    rows = [
        {"identifier": "sp-fro", "side_norm": "frosinone calcio", "kind": "side", "line": "1.5"},
        {"identifier": "ml-fro", "side_norm": "frosinone calcio", "kind": "side", "line": ""},
    ]
    hit = premap.match_side(rows, "Frosinone Calcio", None)
    assert hit and hit["identifier"] == "ml-fro"
