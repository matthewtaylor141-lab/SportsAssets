"""Per-whale copy sizing: RN1 clips at $3, everyone else at $2."""

from sportsassets.live_executor import per_fill_usd


def test_rn1_clips_at_three_dollars():
    assert per_fill_usd("RN1") == 3.00
    assert per_fill_usd("rn1") == 3.00


def test_default_stays_two_dollars():
    assert per_fill_usd("swisstony") == 2.00
    assert per_fill_usd(None) == 2.00
    assert per_fill_usd("someone-new") == 2.00
