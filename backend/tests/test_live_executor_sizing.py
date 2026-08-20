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
    assert per_fill_usd("RN1") == 225.00
    assert per_fill_usd("rn1") == 225.00
    assert per_fill_usd("SwissTony") == 300.00
    assert per_fill_usd("swisstony") == 300.00


def test_swisstony_soccer_limits_at_one_hundred():
    """Owner order 2026-08-12: '$100 per event on soccer' — the sport
    override beats his $200 whale clip on soccer slugs only; other
    sports and other whales are untouched by it."""
    assert per_fill_usd("SwissTony", "atc-epl-ars-che-2026-08-15-ars") == 150.00
    assert per_fill_usd("swisstony", "epl-ars-che-2026-08-15") == 150.00
    # The misc bucket (table tennis etc.) classifies as soccer — the
    # conservative clip rides along:
    assert per_fill_usd("swisstony", "setkameua-pakser-sydand-2026-08-11") == 150.00
    # Non-soccer swisstony stays $200; other whales ignore the override.
    assert per_fill_usd("swisstony", "aec-atp-rafjod-artfil-2026-08-11") == 300.00
    assert per_fill_usd("rn1", "atc-epl-ars-che-2026-08-15-ars") == 300.00  # soccer cell (2026-08-17)
    assert per_fill_usd("someone-new", "epl-ars-che-2026-08-15") == 75.00


def test_default_is_fifty_dollars():
    # HRH graduated to $112.50 (owner mandate 2026-08-20 midday).
    assert per_fill_usd("homerunhazard") == 112.50
    # kch123 graduated from the default to a studied $150 clip
    # (owner go 2026-08-20, allocation re-cut).
    assert per_fill_usd("kch123") == 150.00
    assert per_fill_usd(None) == 75.00
    assert per_fill_usd("someone-new") == 75.00
    # 0x2c33: $225 -> $300 (owner mandate 2026-08-20 midday — best
    # residual ROI at our latency on the board); tennis cell BLOCK
    # unchanged.
    assert per_fill_usd(
        "0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-1759935795465") == 300.00
    assert per_fill_usd(
        "0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-1759935795465",
        "aec-atp-rafjod-artfil-2026-08-11") == 0.00


def test_underdog_sleeve_keeps_its_own_stake():
    """v2 (owner 2026-08-12): the sleeve's flat stake is $2, and it is
    NOT the copy sizing map — changing whale clips never touches it."""
    from sportsassets.workers.underdog import PER_FILL_USD

    assert PER_FILL_USD == 2.00


class TestTypeMultipliers:
    """Allocation re-cut (owner go 2026-08-20 morning): spreads x1.5 for
    every whale — the one invariant that held across all five lifetime
    type calibrations — RN1 BTTS x1.5 (+7.2%, his best type), kch123
    spreads to the studied $300. Multipliers scale the resolved clip;
    they never unblock a 0.00 cell."""

    def test_spreads_scale_up_for_every_whale(self):
        # swisstony soccer spread: $150 soccer cap x 1.5.
        assert per_fill_usd(
            "swisstony", "asc-epl-ars-che-2026-08-20-neg-1pt5") == 225.00
        # rn1 whale-feed spread grammar (bare line after date): his
        # $300 soccer sport-override x 1.5.
        assert per_fill_usd(
            "rn1", "epl-ars-che-2026-08-20-1pt5") == 450.00
        # Unknown whale: default $75 x 1.5.
        assert per_fill_usd(
            "someone-new", "asc-epl-ars-che-2026-08-20-neg-1pt5") == 112.50

    def test_kch123_spread_hits_the_studied_three_hundred(self):
        assert per_fill_usd(
            "kch123", "asc-nba-lal-bos-2026-11-01-neg-2pt5") == 300.00
        # His non-spread cells ride the $150 base.
        assert per_fill_usd(
            "kch123", "atc-nhl-tor-mtl-2026-10-15-tor") == 150.00

    def test_rn1_btts_scales_up(self):
        # $300 soccer override x 1.5 — BTTS is a soccer market.
        assert per_fill_usd(
            "rn1", "astatc-epl-ars-che-2026-08-20-ftts") == 450.00

    def test_moneylines_are_untouched(self):
        assert per_fill_usd(
            "rn1", "aec-atp-rafjod-artfil-2026-08-21-raf") == 112.50
        assert per_fill_usd("swisstony",
                            "atc-epl-ars-che-2026-08-20-ars") == 150.00

    def test_a_blocked_cell_is_never_unblocked_by_a_multiplier(self):
        from sportsassets.live_executor import _W2C33

        assert per_fill_usd(
            _W2C33, "asc-atp-rafjod-artfil-2026-08-21-neg-1pt5") == 0.00


class TestCopyLimitPrice:
    """RN1 capture tolerance (owner mandate 2026-08-20): his +2c, RN1
    only — misses on every other whale graded negative, so the strict
    same-or-better limit stays everywhere it is saving money."""

    def test_everyone_else_stays_same_or_better(self):
        from sportsassets.live_executor import copy_limit_price

        assert copy_limit_price("swisstony", 0.474) == 0.47
        assert copy_limit_price("0xwhoever", 0.50) == 0.50
        assert copy_limit_price(None, 0.335) == 0.33

    def test_rn1_gets_two_cents_of_capture(self):
        from sportsassets.live_executor import copy_limit_price

        assert copy_limit_price("rn1", 0.474) == 0.49   # floor 47 + 2
        assert copy_limit_price("RN1", 0.50) == 0.52
        assert copy_limit_price("rn1", 0.985) == 0.99   # capped below 1

    def test_env_zero_restores_strict_rule(self, monkeypatch):
        from sportsassets import live_executor as le

        monkeypatch.setattr(le, "RN1_TOL_CENTS", 0.0)
        assert le.copy_limit_price("rn1", 0.474) == 0.47
