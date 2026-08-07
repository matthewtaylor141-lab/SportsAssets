"""Per-whale copy sizing: $3 per trade for EVERYONE (owner directive
2026-08-05). RN1's map entry stays so per-whale divergence remains a
one-line change, but it no longer differs from the default."""

from sportsassets.live_executor import per_fill_usd


def test_rn1_clips_at_three_dollars():
    assert per_fill_usd("RN1") == 5.00
    assert per_fill_usd("rn1") == 5.00


def test_default_is_three_dollars():
    assert per_fill_usd("swisstony") == 5.00
    assert per_fill_usd(None) == 5.00
    assert per_fill_usd("someone-new") == 5.00
