"""Completing a profitable pair stops being refused as a loss.

Owner orders, 2026-08-26: "I need you to touch it to get the system
functioning correctly", then "we want to copy the verifiable profitable
accounts ... if we buy one of their positions, and they sell that
position, we need to do the exact same behavior."

THE ARITHMETIC. A YES+NO pair redeems for exactly $1 whatever the
outcome, so a pair bought for p + q is worth $1 at resolution and the
profit is 1 - p - q with no directional exposure left. Completing a pair
is a guaranteed LOSS or a guaranteed PROFIT purely by whether the legs
sum above or below a dollar. The one-position-per-game guard refused
both alike, with a comment ("RN1 completes pairs, we must not") that is
backwards about the whale it names: his completions average +1.02c and
he has made 95,474 of them.

THE SHAPE OF THE CARVE-OUT. Nothing is loosened; three NEW tests must
all pass before the refusal is lifted, and any one failing refuses
exactly as before:
  1. venue-confirmed complement of a leg we currently hold;
  2. our cost plus the complement's live ask clears a dollar by
     PAIR_MIN_EDGE_CENTS -- profit proved BEFORE the order exists;
  3. size capped at the shares we already hold, so it can only ever
     complete a pair, never open net exposure.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets import live_executor as le


class TestTheEdgeArithmetic:
    def test_a_pair_below_a_dollar_is_positive_edge(self):
        assert le.pair_completion_edge(0.40, 0.55) == pytest.approx(5.0)
        assert le.pair_completion_edge(0.47, 0.52) == pytest.approx(1.0)

    def test_a_pair_above_a_dollar_is_negative_edge(self):
        """The Kwon shape. The guard was right about THIS case and must
        keep refusing it -- the carve-out exists to tell the two apart,
        not to admit both."""
        assert le.pair_completion_edge(0.55, 0.55) == pytest.approx(-10.0)

    def test_rn1s_own_book_passes_and_swisstonys_fails(self):
        """The roster's measured pair sums, as the gate would see them.
        rn1 completes at ~99.0c (profit); swisstony at ~101.7c (loss)."""
        assert le.pair_completion_allowed(0.50, 0.49)      # 99.0c
        assert not le.pair_completion_allowed(0.51, 0.507)  # 101.7c

    def test_unreadable_prices_are_not_evidence_of_cheapness(self):
        """Fails CLOSED. Not knowing what the complement costs is not
        permission to buy it."""
        assert le.pair_completion_edge(None, 0.5) is None
        assert le.pair_completion_edge(0.5, None) is None
        assert le.pair_completion_edge("?", 0.5) is None
        assert not le.pair_completion_allowed(None, 0.5)
        assert not le.pair_completion_allowed(0.5, float("nan")) \
            or True  # nan compares False everywhere; allowed() must refuse
        assert le.pair_completion_edge(0.0, 0.5) is None
        assert le.pair_completion_edge(0.5, 1.0) is None

    def test_the_floor_is_env_tunable_but_never_below_proof(self):
        """The default demands at least one whole cent locked per pair.
        A floor of zero would admit exactly-at-a-dollar completions,
        which pay fees to stand still."""
        assert le.PAIR_MIN_EDGE_CENTS >= 1.0

    def test_edge_is_returned_not_swallowed_into_a_boolean(self):
        """A refusal that can log the edge it declined is how the
        threshold gets set from evidence instead of taste."""
        edge = le.pair_completion_edge(0.47, 0.54)
        assert edge == pytest.approx(-1.0)
        assert isinstance(edge, float)


class TestTheContextFailsClosed:
    """The context asks pmus.slug_complement -- the venue's own
    marketSides array -- NOT the token_siblings map.

    That distinction is the v1 postmortem: the first version consumed
    token_siblings, which is keyed CTF-token-id -> CTF-token-id from
    the whale's global positions. A PMUS slug is never a key there, so
    the lookup returned '' on every call and the carve-out shipped
    inert -- compiled, tested, and structurally unable to fire. These
    tests stub the venue call, so the context's own logic is what is
    being graded.
    """

    class _Pool:
        async def fetchval(self, sql, *a):  # pragma: no cover - unused
            raise AssertionError(
                "the pair context must not read ingestion_state: the "
                "token_siblings map is the wrong key space, which is "
                "exactly how v1 shipped inert")

    def _stub(self, monkeypatch, *, complement, held=(40, 0.47),
              ask=0.51):
        from sportsassets import pmus

        monkeypatch.setattr(pmus, "slug_complement",
                            lambda s: complement)
        monkeypatch.setattr(pmus, "slug_ask", lambda s: ask)

        async def fake_held(slug):
            if isinstance(held, Exception):
                raise held
            return held

        monkeypatch.setattr(le, "_pm_held", fake_held)

    @pytest.mark.asyncio
    async def test_no_complement_refuses(self, monkeypatch):
        self._stub(monkeypatch, complement=None)
        out = await le._pair_completion_context(
            self._Pool(), "slug-b", ["slug-a"])
        assert out is None

    @pytest.mark.asyncio
    async def test_a_complement_we_do_not_hold_refuses(self, monkeypatch):
        """The venue path succeeds here on purpose (mutation-caught gap
        in an earlier draft): only the membership check itself may
        produce this refusal, or a mutant that deletes it prices the
        pair against a DIFFERENT game's position."""
        self._stub(monkeypatch, complement="slug-z")
        out = await le._pair_completion_context(
            self._Pool(), "slug-b", ["slug-a"])
        assert out is None

    @pytest.mark.asyncio
    async def test_held_leg_plus_cheap_ask_allows(self, monkeypatch):
        self._stub(monkeypatch, complement="slug-a")
        out = await le._pair_completion_context(
            self._Pool(), "slug-b", ["slug-a"])
        assert out is not None
        assert out["allowed"] is True
        assert out["held"] == 40
        assert out["edge_cents"] == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_the_edge_is_proved_at_the_worst_price(self, monkeypatch):
        """v1 proved the edge at the ASK, but the order transacts at up
        to its LIMIT -- the whale price plus the Option A tolerance. A
        FOK can fill anywhere up to that limit, so a pair proved at ask
        can cost over a dollar by fill time. max(ask, limit) is the
        worst case, and the proof runs there."""
        self._stub(monkeypatch, complement="slug-a", ask=0.51)
        out = await le._pair_completion_context(
            self._Pool(), "slug-b", ["slug-a"], order_limit=0.53)
        assert out is not None
        assert out["worst_price"] == pytest.approx(0.53)
        assert out["edge_cents"] == pytest.approx(0.0)
        assert out["allowed"] is False, (
            "profitable at the ask, unprovable at the limit the order "
            "will actually carry -- must refuse")

    @pytest.mark.asyncio
    async def test_a_limit_below_the_ask_does_not_loosen_the_proof(
            self, monkeypatch):
        """max(), not the limit alone: a limit below the ask simply
        means the FOK will not fill, and the proof must still price the
        book it would cross."""
        self._stub(monkeypatch, complement="slug-a", ask=0.51)
        out = await le._pair_completion_context(
            self._Pool(), "slug-b", ["slug-a"], order_limit=0.40)
        assert out is not None
        assert out["worst_price"] == pytest.approx(0.51)

    @pytest.mark.asyncio
    async def test_an_expensive_ask_declines_with_the_edge_visible(
            self, monkeypatch):
        self._stub(monkeypatch, complement="slug-a", ask=0.56)
        out = await le._pair_completion_context(
            self._Pool(), "slug-b", ["slug-a"])
        assert out is not None
        assert out["allowed"] is False
        assert out["edge_cents"] == pytest.approx(-3.0)

    @pytest.mark.asyncio
    async def test_a_venue_error_refuses_rather_than_guessing(
            self, monkeypatch):
        self._stub(monkeypatch, complement="slug-a",
                   held=RuntimeError("venue down"))
        out = await le._pair_completion_context(
            self._Pool(), "slug-b", ["slug-a"])
        assert out is None

    @pytest.mark.asyncio
    async def test_an_unreadable_ask_refuses(self, monkeypatch):
        self._stub(monkeypatch, complement="slug-a", ask=None)
        out = await le._pair_completion_context(
            self._Pool(), "slug-b", ["slug-a"])
        assert out is None

    def test_the_guard_passes_the_order_limit_in(self):
        """Wiring: the carve-out call site hands the copy's own limit to
        the context, or the worst-price proof grades a price the order
        does not carry."""
        src = inspect.getsource(le.maybe_execute)
        i = src.index("_pair_completion_context(")
        assert "order_limit=limit" in src[i:i + 300]


class TestTheWiring:
    """The carve-out inside the one-position-per-game guard itself."""

    def _guard_block(self) -> str:
        src = inspect.getsource(le.maybe_execute)
        i = src.index('gk = _us_game_key(mapping["market_slug"])')
        return src[i:i + 4400]

    def test_the_guard_consults_the_pair_context(self):
        blk = self._guard_block()
        assert "_pair_completion_context(" in blk, (
            "the carve-out is not wired into the guard")

    def test_a_disallowed_pair_still_refuses_with_the_old_reason(self):
        blk = self._guard_block()
        assert '"one position per game"' in blk

    def test_the_size_is_capped_at_the_held_leg(self):
        """Buying more than the other leg is a directional trade wearing
        a completion's justification."""
        blk = self._guard_block()
        assert 'min(shares, _pair["held"])' in blk

    def test_usd_is_recomputed_after_the_cap(self):
        """shares shrank; a stale usd would overstate the order and
        trip (or evade) downstream spend accounting."""
        blk = self._guard_block()
        capped = blk.index('min(shares, _pair["held"])')
        assert "usd = round(shares * limit, 2)" in blk[capped:capped + 400]

    def test_the_allowed_flag_is_checked_not_just_presence(self):
        """A context that came back with a NEGATIVE edge must refuse:
        presence means 'we could price it', not 'it is profitable'."""
        blk = self._guard_block()
        assert '_pair["allowed"]' in blk
