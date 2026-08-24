"""Per-whale copy sizing after the TRUEEDGE cuts + upsize (owner order
2026-08-24, from the verified counterfactual table on the FULL detected
book, settled on each whale's own venue):

- rn1, ferrarichampions2026, 0x2c33 are CUT — negative at their OWN
  prices, not copyable at any speed. Their clips are 0.00 blocks and
  COPY_CUT_WHALES refuses them at entry.
- homerunhazard (+26,076 cf / +15,051 paper) and 0x076 (+6,189 cf /
  +5,772 paper) are upsized to the $300 clip the formerly-largest
  sleeves carried; HRH's per-sport cells scale with his base (x2.667).
- swisstony keeps his $300/$225-soccer clips but is admitted to live
  copying only via LIVE_PREMAP_WHALES once his paper cohort at the new
  sub-second detection grades positive.

The underdog cash-out sleeve is a separate $2 constant and must never
inherit these."""

from sportsassets.live_executor import COPY_CUT_WHALES, per_fill_usd


def test_verified_profitable_whales_clip_at_the_probe_size():
    """SUPERSEDED by the owner's 2026-08-24 evening authorization: the
    resume is a bounded proof at $100 per clip, not a return to $300.
    Scale follows real fills."""
    assert per_fill_usd("HomeRunHazard") == 250.00
    assert per_fill_usd("homerunhazard") == 250.00
    assert per_fill_usd("0x076daa87") == 250.00
    assert per_fill_usd("SwissTony") == 250.00


def test_cut_whales_clip_at_zero_everywhere():
    """TRUEEDGE cuts: the 0.00 clip is the second of three independent
    blocks (entry gate, clip, premap allowlist). Sport cells for cut
    whales are gone — a lingering (whale, sport) override would WIN
    over the 0.00 base, which is exactly the leak this test pins."""
    from sportsassets.live_executor import _W2C33

    assert per_fill_usd("rn1") == 0.00
    assert per_fill_usd("RN1") == 0.00
    assert per_fill_usd("ferrarichampions2026") == 0.00
    assert per_fill_usd(_W2C33) == 0.00
    # The old rn1 sport cells (tennis 112.50 / baseball 375 / soccer
    # 300) must not survive anywhere:
    assert per_fill_usd("rn1", "aec-atp-rafjod-artfil-2026-08-21-raf") == 0.00
    assert per_fill_usd("rn1", "aec-mlb-nyy-bos-2026-08-24") == 0.00
    assert per_fill_usd("rn1", "atc-epl-ars-che-2026-08-15-ars") == 0.00
    # Multipliers never resurrect a cut whale (0 x anything = 0):
    assert per_fill_usd("rn1", "epl-ars-che-2026-08-20-1pt5") == 0.00
    assert per_fill_usd(_W2C33,
                        "aec-atp-rafjod-artfil-2026-08-11") == 0.00
    assert per_fill_usd("ferrarichampions2026",
                        "aec-atp-rafjod-artfil-2026-08-11") == 0.00


def test_cut_set_names_exactly_the_three_verified_negative_books():
    from sportsassets.live_executor import _W2C33

    assert COPY_CUT_WHALES == {"rn1", "ferrarichampions2026", _W2C33}


def test_every_whale_is_bounded_by_the_probe_ceiling():
    """No sport cell or multiplier may exceed the authorized $250 —
    and the ceiling CAPS, it never promotes a smaller clip."""
    assert per_fill_usd("SwissTony", "atc-epl-ars-che-2026-08-15-ars") == 250.00
    assert per_fill_usd("swisstony", "epl-ars-che-2026-08-15") == 250.00
    assert per_fill_usd("someone-new", "epl-ars-che-2026-08-15") == 75.00
    assert per_fill_usd("kch123", "atc-nhl-tor-mtl-2026-10-15-tor") == 150.00


def test_hrh_sport_cells_are_retired_for_the_probe():
    """A (whale, sport) cell WINS over the whale clip, so a surviving
    $600 baseball cell would have overridden the $100 authorization."""
    assert per_fill_usd("homerunhazard", "aec-mlb-nyy-bos-2026-08-24") == 250.00
    assert per_fill_usd("homerunhazard",
                        "aec-nfl-kc-buf-2026-09-07") == 250.00


def test_default_and_kch123_unchanged():
    # kch123 is out of season and not in the verified set; his map
    # entry is untouched but the ceiling bounds him if he ever fires.
    assert per_fill_usd("kch123") == 150.00
    assert per_fill_usd(None) == 75.00
    assert per_fill_usd("someone-new") == 75.00


def test_underdog_sleeve_keeps_its_own_stake():
    """v2 (owner 2026-08-12): the sleeve's flat stake is $2, and it is
    NOT the copy sizing map — changing whale clips never touches it."""
    from sportsassets.workers.underdog import PER_FILL_USD

    assert PER_FILL_USD == 2.00


class TestTypeMultipliers:
    """Spreads x1.5 for every whale; kch123 spreads to the studied $300.
    Multipliers scale the resolved clip; they never unblock a 0.00
    cell — and after the TRUEEDGE cuts, never resurrect a cut whale."""

    def test_spreads_still_scale_but_never_past_the_ceiling(self):
        # the multiplier still applies UNDER the ceiling for whales
        # whose base is small enough to see it
        assert per_fill_usd(
            "someone-new", "asc-epl-ars-che-2026-08-20-neg-1pt5") == 112.50
        assert per_fill_usd(
            "homerunhazard", "asc-nba-lal-bos-2026-11-01-neg-2pt5") == 250.00

    def test_moneylines_are_bounded_too(self):
        assert per_fill_usd("swisstony",
                            "atc-epl-ars-che-2026-08-20-ars") == 250.00
        assert per_fill_usd("0x076daa87",
                            "aec-atp-rafjod-artfil-2026-08-21-raf") == 250.00

    def test_a_blocked_cell_is_never_unblocked_by_a_multiplier(self):
        from sportsassets.live_executor import _W2C33

        assert per_fill_usd(
            _W2C33, "asc-atp-rafjod-artfil-2026-08-21-neg-1pt5") == 0.00


class TestCopyLimitPrice:
    """The strict same-or-better limit is now universal in practice:
    RN1's +3c capture tolerance is dead code while he is cut (kept for
    the map's history; his entry gate refuses long before pricing)."""

    def test_everyone_stays_same_or_better(self):
        from sportsassets.live_executor import copy_limit_price

        assert copy_limit_price("swisstony", 0.474) == 0.47
        assert copy_limit_price("homerunhazard", 0.50) == 0.50
        assert copy_limit_price("0x076daa87", 0.335) == 0.33
        assert copy_limit_price(None, 0.335) == 0.33


class TestProbeAuthorization:
    """Owner authorization 2026-08-24 evening: "$250 per clip on the
    actually verified profitable whales". The authorization is a
    CEILING, enforced after every override and multiplier — a cell edit
    or a market-type multiplier must not be able to exceed it."""

    def test_verified_whales_clip_at_one_hundred(self):
        assert per_fill_usd("HomeRunHazard") == 250.00
        assert per_fill_usd("0x076daa87") == 250.00

    def test_the_spread_multiplier_cannot_exceed_the_ceiling(self):
        # x1.5 would be $375 — the ceiling clamps it to the authorized
        # $250 rather than quietly overspending.
        assert per_fill_usd(
            "homerunhazard",
            "asc-nfl-kc-buf-2026-09-13-neg-3pt5") == 250.00

    def test_no_sport_cell_outranks_the_ceiling(self):
        assert per_fill_usd(
            "homerunhazard", "aec-mlb-nyy-bos-2026-08-24") == 250.00
        assert per_fill_usd(
            "homerunhazard", "aec-nfl-kc-buf-2026-09-07") == 250.00

    def test_cut_whales_are_still_zero(self):
        from sportsassets.live_executor import _W2C33

        assert per_fill_usd("rn1") == 0.00
        assert per_fill_usd("ferrarichampions2026") == 0.00
        assert per_fill_usd(_W2C33) == 0.00

    def test_the_ceiling_is_env_adjustable_for_promotion(self, monkeypatch):
        from sportsassets import live_executor as le

        monkeypatch.setattr(le, "LIVE_MAX_CLIP_USD", 500.0)
        assert le.per_fill_usd("homerunhazard") == 250.00, \
            "raising the ceiling alone must not raise the clip"
