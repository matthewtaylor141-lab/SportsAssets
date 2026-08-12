"""Per-whale copy sizing. RN1 clips at $150 (owner approval
2026-08-11 round 4 - profitable every day since Aug 3);
SwissTony at $200 (owner decision 2026-08-11, profitability round 3 —
strongest measured earner, +$760 on 345 settled at $100) EXCEPT soccer,
which limits at $100 per event (owner order 2026-08-12, the soccer
resume); everyone else (the paused HRH, out-of-season kch123, the newly
promoted 0x2c33 wallet, any future whale) takes the $50 default. The
underdog cash-out sleeve is a separate $2 constant and must never
inherit these."""

from sportsassets.live_executor import per_fill_usd


def test_proven_sleeves_clip_at_their_owner_set_sizes():
    assert per_fill_usd("RN1") == 150.00
    assert per_fill_usd("rn1") == 150.00
    assert per_fill_usd("SwissTony") == 200.00
    assert per_fill_usd("swisstony") == 200.00


def test_swisstony_soccer_limits_at_one_hundred():
    """Owner order 2026-08-12: '$100 per event on soccer' — the sport
    override beats his $200 whale clip on soccer slugs only; other
    sports and other whales are untouched by it."""
    assert per_fill_usd("SwissTony", "atc-epl-ars-che-2026-08-15-ars") == 100.00
    assert per_fill_usd("swisstony", "epl-ars-che-2026-08-15") == 100.00
    # The misc bucket (table tennis etc.) classifies as soccer — the
    # conservative clip rides along:
    assert per_fill_usd("swisstony", "setkameua-pakser-sydand-2026-08-11") == 100.00
    # Non-soccer swisstony stays $200; other whales ignore the override.
    assert per_fill_usd("swisstony", "aec-atp-rafjod-artfil-2026-08-11") == 200.00
    assert per_fill_usd("rn1", "atc-epl-ars-che-2026-08-15-ars") == 150.00
    assert per_fill_usd("someone-new", "epl-ars-che-2026-08-15") == 50.00


def test_default_is_fifty_dollars():
    assert per_fill_usd("homerunhazard") == 50.00
    assert per_fill_usd("kch123") == 50.00
    assert per_fill_usd(None) == 50.00
    assert per_fill_usd("someone-new") == 50.00
    assert per_fill_usd(
        "0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-1759935795465") == 50.00


def test_underdog_sleeve_keeps_its_own_stake():
    """v2 (owner 2026-08-12): the sleeve's flat stake is $2, and it is
    NOT the copy sizing map — changing whale clips never touches it."""
    from sportsassets.workers.underdog import PER_FILL_USD

    assert PER_FILL_USD == 2.00
