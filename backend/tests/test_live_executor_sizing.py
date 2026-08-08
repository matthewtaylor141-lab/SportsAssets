"""Per-whale copy sizing (owner directive 2026-08-08): RN1 and
SwissTony clip at $10 — the $5->$10 promotion gated on their own settled
cohorts — while everyone else (HRH's young widened cells, out-of-season
kch123, any future whale) stays at the $5 default."""

from sportsassets.live_executor import per_fill_usd


def test_proven_sleeves_clip_at_ten_dollars():
    assert per_fill_usd("RN1") == 10.00
    assert per_fill_usd("rn1") == 10.00
    assert per_fill_usd("SwissTony") == 10.00
    assert per_fill_usd("swisstony") == 10.00


def test_default_stays_at_five_dollars():
    assert per_fill_usd("homerunhazard") == 5.00
    assert per_fill_usd("kch123") == 5.00
    assert per_fill_usd(None) == 5.00
    assert per_fill_usd("someone-new") == 5.00
