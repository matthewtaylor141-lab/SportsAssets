"""Whale-alignment join: order-insensitive game keys, mapper-bar sides."""

from edge.shadow.whale_align import aligned, build_map, game_key


def test_game_key_survives_prefix_and_order_differences():
    # Platform slug (no prefix) and venue slug (aec- prefix, possibly a
    # different team order) must land on the same key.
    assert game_key("mlb-stl-cin-2026-08-06") == \
        game_key("aec-mlb-cin-stl-2026-08-06")
    assert game_key("aec-mlb-stl-cin-2026-08-06-f5") == \
        game_key("mlb-stl-cin-2026-08-06")


def test_undated_or_bare_slugs_produce_no_key():
    assert game_key("mlb-stl-cin") is None
    assert game_key("") is None


def test_alignment_needs_same_game_and_same_team():
    wm = build_map([{"slug": "mlb-stl-cin-2026-08-06",
                     "outcome": "St. Louis Cardinals"}])
    assert aligned("aec-mlb-stl-cin-2026-08-06", "St. Louis Cardinals", wm)
    # Other side of the same game: NOT aligned.
    assert not aligned("aec-mlb-stl-cin-2026-08-06", "Cincinnati Reds", wm)
    # Same team, different day: NOT aligned.
    assert not aligned("aec-mlb-stl-cin-2026-08-07", "St. Louis Cardinals", wm)


def test_build_map_dedupes_outcomes():
    rows = [{"slug": "mlb-a-b-2026-08-06", "outcome": "Team A"},
            {"slug": "mlb-a-b-2026-08-06", "outcome": "Team A"}]
    wm = build_map(rows)
    assert list(wm.values()) == [["Team A"]]
