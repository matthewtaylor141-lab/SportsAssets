"""Per-whale copy sizing after the TRUEEDGE cuts + upsize (owner order
2026-08-24, from the verified counterfactual table on the FULL detected
book, settled on each whale's own venue):

ROSTER RESET 2026-08-25 (owner-granted). The merge-inclusive re-grade
— the first whale P&L that can see how these accounts actually take
profit — inverted the previous decision: rn1 +$222,038 and ferrari
+$217,159 were the two BEST books and had been cut, while swisstony
-$187,613 was being copied. These fixtures follow the live roster.

Superseded, kept for the record:
- rn1, ferrarichampions2026, 0x2c33 were CUT — negative at their OWN
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
    """swisstony + homerunhazard REINSTATED 2026-08-27 (owner order):
    their 2026-08-25 cuts were graded on the merge-only instrument
    since proven blind to REDEEM exits; the venue ledger reads
    swisstony +$23.6M lifetime / +$1.36M 30d and homerunhazard +$2.32M
    / +$869k 30d. All reinstated books clip at the standard $250 —
    scale still follows real fills, the ceiling still caps."""
    assert per_fill_usd("0x076daa87") == 250.00
    assert per_fill_usd("RN1") == 250.00
    assert per_fill_usd("ferrarichampions2026") == 250.00
    assert per_fill_usd("HomeRunHazard") == 250.00
    assert per_fill_usd("swisstony") == 250.00


def test_cut_whales_clip_at_zero_everywhere():
    """TRUEEDGE cuts: the 0.00 clip is the second of three independent
    blocks (entry gate, clip, premap allowlist). Sport cells for cut
    whales are gone — a lingering (whale, sport) override would WIN
    over the 0.00 base, which is exactly the leak this test pins."""
    from sportsassets.live_executor import _W2C33

    assert per_fill_usd(_W2C33) == 0.00
    assert per_fill_usd(_W2C33.upper()) == 0.00, "case must not revive"
    # Multipliers never resurrect a cut whale (0 x anything = 0), and
    # the one whale still cut stays zero across every sport and type:
    assert per_fill_usd(_W2C33,
                        "aec-atp-rafjod-artfil-2026-08-11") == 0.00
    assert per_fill_usd(_W2C33,
                        "epl-ars-che-2026-08-20-1pt5") == 0.00


def test_cut_set_names_exactly_the_verified_negative_books():
    """Three literals encode this one decision — the clip map, the
    verified set, and this cut set — and a roster move that lands in
    two of the three is the exact shape of the 2026-08-24 bug where
    SwissTony was "resumed" everywhere except the list that mattered
    and placed 2,897 rejections with $0 deployed. HomeRunHazard joined
    2026-08-25."""
    from sportsassets.live_executor import (
        VERIFIED_PROFITABLE_DEFAULT, _W2C33)

    # swisstony + homerunhazard reinstated 2026-08-27 (owner order,
    # venue-ledger basis — see the record beside COPY_CUT_WHALES).
    assert COPY_CUT_WHALES == {_W2C33}
    # and the three literals must agree with each other
    verified = {w.strip() for w in
                VERIFIED_PROFITABLE_DEFAULT.lower().split(",")}
    assert not (verified & COPY_CUT_WHALES), \
        "a whale cannot be both verified-profitable and cut"
    for w in COPY_CUT_WHALES:
        assert per_fill_usd(w) == 0.00, f"{w} is cut but still clips"
    for w in verified:
        assert per_fill_usd(w) > 0, f"{w} is verified but clips at zero"


def test_every_whale_is_bounded_by_the_probe_ceiling():
    """No sport cell or multiplier may exceed the authorized $250 —
    and the ceiling CAPS, it never promotes a smaller clip."""
    assert per_fill_usd("RN1", "atc-epl-ars-che-2026-08-15-ars") == 250.00
    assert per_fill_usd("rn1", "epl-ars-che-2026-08-15") == 250.00
    assert per_fill_usd("someone-new", "epl-ars-che-2026-08-15") == 75.00
    assert per_fill_usd("kch123", "atc-nhl-tor-mtl-2026-10-15-tor") == 150.00


def test_hrh_sport_cells_cannot_outrank_the_ceiling():
    """A (whale, sport) cell WINS over the whale clip — his old cells
    carried 375/600-sized numbers. Reinstated 2026-08-27, the cells may
    resolve again, but the $250 ceiling caps every one of them: the
    owner authorization binds AFTER every override and multiplier."""
    assert per_fill_usd("homerunhazard",
                        "aec-mlb-nyy-bos-2026-08-24") == 250.00
    assert per_fill_usd("homerunhazard",
                        "aec-nfl-kc-buf-2026-09-07") == 250.00
    assert per_fill_usd("homerunhazard",
                        "asc-nba-lal-bos-2026-11-01-neg-2pt5") == 250.00


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
            "0x076daa87", "asc-nba-lal-bos-2026-11-01-neg-2pt5") == 250.00

    def test_moneylines_are_bounded_too(self):
        assert per_fill_usd("rn1",
                            "atc-epl-ars-che-2026-08-20-ars") == 250.00
        assert per_fill_usd("0x076daa87",
                            "aec-atp-rafjod-artfil-2026-08-21-raf") == 250.00

    def test_a_blocked_cell_is_never_unblocked_by_a_multiplier(self):
        from sportsassets.live_executor import _W2C33

        assert per_fill_usd(
            _W2C33, "asc-atp-rafjod-artfil-2026-08-21-neg-1pt5") == 0.00


class TestCopyLimitPrice:
    """OPTION A (owner order 2026-08-26): a capture tolerance for every
    whale, not RN1 alone.

    This class used to assert `copy_limit_price("swisstony", 0.474) ==
    0.47` under the heading "everyone stays same-or-better". That was a
    faithful test of the old rule, and the old rule was the defect: the
    limit was his price floored to the tick, so the book only reached it
    when the market had moved AGAINST him, and filling was conditioned
    on the whale being wrong. Measured, not theorised -- at_his, his own
    P&L on the subset we filled at HIS prices, was negative on all six
    whales (-$30,248) while price_drag was positive on all six
    (+$13,051): we filled cheaper than he did and still lost.

    So the assertions below invert deliberately. What must NOT change is
    everything the tolerance is not allowed to touch, and that is most
    of this class.
    """

    def test_a_fresh_copy_now_carries_the_tolerance(self):
        from sportsassets.live_executor import (copy_limit_price,
                                                tol_cents_for)

        cents = tol_cents_for("swisstony")
        assert cents > 0, "Option A did not reach the general whale"
        assert copy_limit_price("swisstony", 0.474) == round(
            0.47 + cents / 100.0, 2)

    def test_a_reclaim_still_pays_nothing(self):
        """Unchanged by Option A and load-bearing. The tolerance exists
        to capture a signal the market is moving with; paying over a
        days-old entry is adverse selection wearing the same clothes."""
        from sportsassets.live_executor import copy_limit_price

        assert copy_limit_price("swisstony", 0.474, fresh=False) == 0.47
        assert copy_limit_price("rn1", 0.474, fresh=False) == 0.47
        assert copy_limit_price(None, 0.335, fresh=False) == 0.33

    def test_the_floor_to_the_tick_survives(self):
        """The tolerance is added to the FLOORED price, not to his raw
        one. Adding first and flooring second would silently give back a
        fraction of a cent and make the knob mean something different at
        different prices."""
        from sportsassets.live_executor import (copy_limit_price,
                                                tol_cents_for)

        c = tol_cents_for("x") / 100.0
        assert copy_limit_price("x", 0.4799) == round(0.47 + c, 2)
        assert copy_limit_price("x", 0.4700) == round(0.47 + c, 2)

    def test_it_never_crosses_the_dollar(self):
        from sportsassets.live_executor import copy_limit_price

        for p in (0.98, 0.99, 0.995):
            assert copy_limit_price("x", p) <= 0.99

    def test_the_ceiling_clamps_a_fat_fingered_env(self, monkeypatch):
        """The only way this becomes a 10-cent overpay is an env typo,
        and 10c on a 50c contract is 20% of stake against a 1.5% edge."""
        from sportsassets import live_executor as le

        monkeypatch.setenv("PMUS_COPY_TOL_BY_WHALE", "greedy:99")
        assert le.tol_cents_for("greedy") == le.COPY_TOL_MAX_CENTS

    def test_a_named_zero_refuses_tolerance(self, monkeypatch):
        """An explicit 0 must beat a non-zero global default. Testing
        the override for truthiness rather than membership would silently
        pay on a whale someone deliberately excluded."""
        from sportsassets import live_executor as le

        monkeypatch.setenv("PMUS_COPY_TOL_BY_WHALE", "cautious:0")
        assert le.tol_cents_for("cautious") == 0.0
        assert le.copy_limit_price("cautious", 0.474) == 0.47

    def test_a_negative_env_cannot_bid_below_his_price(self, monkeypatch):
        """A negative tolerance would be same-or-better made stricter,
        which is the bug we are removing, not a safety feature."""
        from sportsassets import live_executor as le

        monkeypatch.setenv("PMUS_COPY_TOL_BY_WHALE", "odd:-5")
        assert le.tol_cents_for("odd") == 0.0

    def test_rn1_keeps_its_own_env_var(self, monkeypatch):
        """PMUS_RN1_TOL_CENTS is already set in the live environment.
        Option A must not silently change what it means."""
        from sportsassets import live_executor as le

        monkeypatch.delenv("PMUS_COPY_TOL_BY_WHALE", raising=False)
        assert le.tol_cents_for("rn1") == min(le.RN1_TOL_CENTS,
                                              le.COPY_TOL_MAX_CENTS)

    def test_the_default_is_one_cent(self):
        """On a 50c contract one cent is 2% of stake and rn1's whole
        measured edge is +1.50%. The default is the smallest step that
        breaks the conditioning at all; anything wider must be earned
        from graded evidence."""
        from sportsassets import live_executor as le

        assert le.COPY_TOL_CENTS == 1.0


class TestToleranceCohort:
    """Option A ships with its own grader. A change that cannot be
    graded is exactly how the previous rule survived for two weeks."""

    def test_a_fill_above_his_price_is_marginal(self):
        from sportsassets.live_executor import tolerance_cohort

        assert tolerance_cohort(0.47, 0.48) == "marginal"

    def test_a_fill_at_or_below_is_parity(self):
        """Same-or-better would have taken these too, so they say
        nothing about the change."""
        from sportsassets.live_executor import tolerance_cohort

        assert tolerance_cohort(0.47, 0.47) == "parity"
        assert tolerance_cohort(0.47, 0.46) == "parity"

    def test_missing_prices_are_not_guessed(self):
        from sportsassets.live_executor import tolerance_cohort

        assert tolerance_cohort(None, 0.48) == "unknown"
        assert tolerance_cohort(0.47, None) == "unknown"
        assert tolerance_cohort(0.47, "n/a") == "unknown"

    def test_the_split_is_what_answers_the_question(self):
        """Blending the cohorts is how this gets graded as harmless:
        parity fills dominate the count and drown the marginal signal.
        Only the marginal ones exist BECAUSE of Option A."""
        import inspect

        from sportsassets.live_executor import tolerance_cohort

        doc = inspect.getdoc(tolerance_cohort) or ""
        assert "marginal" in doc and "parity" in doc


class TestProbeAuthorization:
    """Owner authorization 2026-08-24 evening: "$250 per clip on the
    actually verified profitable whales". The authorization is a
    CEILING, enforced after every override and multiplier — a cell edit
    or a market-type multiplier must not be able to exceed it."""

    def test_verified_whales_clip_at_one_hundred(self):
        assert per_fill_usd("0x076daa87") == 250.00
        assert per_fill_usd("ferrarichampions2026") == 250.00

    def test_the_spread_multiplier_cannot_exceed_the_ceiling(self):
        # x1.5 would be $375 — the ceiling clamps it to the authorized
        # $250 rather than quietly overspending.
        assert per_fill_usd(
            "0x076daa87",
            "asc-nfl-kc-buf-2026-09-13-neg-3pt5") == 250.00

    def test_no_sport_cell_outranks_the_ceiling(self):
        assert per_fill_usd(
            "0x076daa87", "aec-mlb-nyy-bos-2026-08-24") == 250.00
        assert per_fill_usd(
            "0x076daa87", "aec-nfl-kc-buf-2026-09-07") == 250.00

    def test_cut_whales_are_still_zero(self):
        from sportsassets.live_executor import _W2C33

        assert per_fill_usd(_W2C33) == 0.00

    def test_the_ceiling_is_env_adjustable_for_promotion(self, monkeypatch):
        from sportsassets import live_executor as le

        monkeypatch.setattr(le, "LIVE_MAX_CLIP_USD", 500.0)
        assert le.per_fill_usd("0x076daa87") == 250.00, \
            "raising the ceiling alone must not raise the clip"


class TestRelativeToleranceCap:
    """Revenue audit 2026-08-29, tightening only: whatever cents the
    maps grant is bounded at 15% of his price (one-tick floor). +3c on
    a 10c book was +30% of stake — the PROOF2WORST drag shape."""

    def test_no_change_on_normal_books(self):
        from sportsassets.live_executor import (copy_limit_price,
                                                tol_cents_for)

        c = tol_cents_for("swisstony") / 100.0
        assert copy_limit_price("swisstony", 0.474) == round(0.47 + c, 2)

    def test_longshots_are_capped(self, monkeypatch):
        from sportsassets import live_executor as le

        monkeypatch.setenv("PMUS_COPY_TOL_BY_WHALE", "hot:3")
        # 10c book: 15% cap = 1c < 3c granted
        assert le.copy_limit_price("hot", 0.10) == 0.11
        # 20c book: cap = 3c = grant — exact boundary
        assert le.copy_limit_price("hot", 0.20) == 0.23

    def test_the_tick_floor_keeps_a_grant_alive(self, monkeypatch):
        from sportsassets import live_executor as le

        monkeypatch.setenv("PMUS_COPY_TOL_BY_WHALE", "hot:3")
        # 3c book: 15% would be 0.45c; the floor keeps one whole tick
        assert le.copy_limit_price("hot", 0.03) == 0.04


class TestCodeDefaultToleranceRefusals:
    """0x076daa87's marginal (tolerance-bought) cohort graded
    -74.98% ROI (-$653.17 on $904 staked, probe 2026-08-29 19:44Z) —
    the code now refuses tolerance on him by default. An explicit env
    override still wins: re-enabling is an operator decision."""

    def test_0x076daa87_pays_no_tolerance_by_default(self):
        from sportsassets.live_executor import (copy_limit_price,
                                                tol_cents_for)

        assert tol_cents_for("0x076daa87") == 0.0
        assert copy_limit_price("0x076daa87", 0.474) == 0.47

    def test_an_explicit_env_override_still_wins(self, monkeypatch):
        from sportsassets import live_executor as le

        monkeypatch.setenv("PMUS_COPY_TOL_BY_WHALE", "0x076daa87:1")
        assert le.tol_cents_for("0x076daa87") == 1.0

    def test_other_whales_keep_the_global_default(self):
        from sportsassets.live_executor import tol_cents_for

        assert tol_cents_for("homerunhazard") > 0
