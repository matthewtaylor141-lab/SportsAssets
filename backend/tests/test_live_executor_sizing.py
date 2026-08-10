"""Per-whale copy sizing (owner directive 2026-08-10 evening: the
Polymarket deposit landed — PMUS clips match the Kalshi leg). RN1 and
SwissTony clip at $100 on their own settled records; everyone else
(HRH's young widened cells, out-of-season kch123, the newly promoted
0x2c33 wallet, any future whale) takes the $50 default. The underdog
cash-out sleeve is a separate $1 constant and must never inherit these."""

from sportsassets.live_executor import per_fill_usd


def test_proven_sleeves_clip_at_one_hundred_dollars():
    assert per_fill_usd("RN1") == 100.00
    assert per_fill_usd("rn1") == 100.00
    assert per_fill_usd("SwissTony") == 100.00
    assert per_fill_usd("swisstony") == 100.00


def test_default_is_fifty_dollars():
    assert per_fill_usd("homerunhazard") == 50.00
    assert per_fill_usd("kch123") == 50.00
    assert per_fill_usd(None) == 50.00
    assert per_fill_usd("someone-new") == 50.00
    assert per_fill_usd(
        "0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-1759935795465") == 50.00


def test_underdog_sleeve_keeps_its_own_dollar():
    from sportsassets.workers.underdog import PER_FILL_USD

    assert PER_FILL_USD == 1.00
