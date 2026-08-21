"""Owner report 2026-08-21 evening: every prop's No side was dead on
the desk. The venue lists props as mirrored pairs ('Q — Yes' and
'Q — No') with a book only on each listing's Yes token; the desk must
route a bookless side through the sibling listing's complementary
token — the identical bet on the token that actually trades."""

from sportsassets.api.app import _mirror_dead_prop_sides


def _mkt(title, yes_ask, no_ask, tag):
    return {
        "slug": f"slug-{tag}", "title": title, "event_title": "ATL vs MIL",
        "outcomes": [
            {"outcome": "Yes", "asset": f"{tag}-yes-token",
             "ask": yes_ask, "bid": yes_ask and round(yes_ask - 0.01, 2)},
            {"outcome": "No", "asset": f"{tag}-no-token",
             "ask": no_ask, "bid": None},
        ],
    }


def test_dead_no_routes_to_sibling_yes():
    q = "Will Matt Olson record at least 2 home runs in ATL vs MIL?"
    yes_m = _mkt(f"{q} — Yes", 0.01, None, "y")
    no_m = _mkt(f"{q} — No", 0.02, None, "n")
    _mirror_dead_prop_sides([yes_m, no_m])
    # No on the Yes-listing == Yes on the No-listing (live at 2c).
    dead_no = yes_m["outcomes"][1]
    assert dead_no["asset"] == "n-yes-token"
    assert dead_no["ask"] == 0.02
    assert dead_no["via_sibling"] is True
    # And symmetrically: No on the No-listing == Yes on the Yes-listing.
    dead_no2 = no_m["outcomes"][1]
    assert dead_no2["asset"] == "y-yes-token"
    assert dead_no2["ask"] == 0.01
    # Priced sides are NEVER touched.
    assert yes_m["outcomes"][0]["asset"] == "y-yes-token"
    assert no_m["outcomes"][0]["asset"] == "n-yes-token"


def test_no_sibling_stays_honestly_dead():
    lone = _mkt("Will X happen? — Yes", 0.55, None, "lone")
    _mirror_dead_prop_sides([lone])
    assert lone["outcomes"][1]["ask"] is None
    assert "via_sibling" not in lone["outcomes"][1]


def test_bookless_sibling_side_is_not_borrowed():
    q = "Will Y happen?"
    yes_m = _mkt(f"{q} — Yes", None, None, "y2")   # entirely dark
    no_m = _mkt(f"{q} — No", None, None, "n2")
    _mirror_dead_prop_sides([yes_m, no_m])
    for m in (yes_m, no_m):
        for o in m["outcomes"]:
            assert o["ask"] is None


def test_non_mirrored_titles_untouched():
    ml = {
        "slug": "atc-mlb-atl-mil", "title": "Braves vs Brewers",
        "event_title": None,
        "outcomes": [
            {"outcome": "Atlanta Braves", "asset": "a", "ask": 0.58, "bid": 0.57},
            {"outcome": "Milwaukee Brewers", "asset": "b", "ask": None, "bid": None},
        ],
    }
    _mirror_dead_prop_sides([ml])
    assert ml["outcomes"][1]["ask"] is None
