"""The exits were never missing from the feed. They were wearing a BUY.

Owner, 2026-08-25, after reading Polymarket's global microdata:
"shorts, in the context of this data, means selling."

The venue holds ONE SIGNED net position per market. You cannot be long
and short the same market at once, so buying the complementary leg of
something you hold does not open a second bet — it retires the first,
share for share. That is why these whales show 860,669 buys and zero
sells across every ingestion source.

Until this shipped, the system did something worse than miss them.
execute_copy tested `side == "BUY"` and handed a complement buy to
maybe_execute, which sized a fresh clip and bought the leg he was
ABANDONING. Our exposure to the original outcome did not fall to zero;
it went to 2x — his leg we never closed, plus the opposite leg at a
price that necessarily sums to ~1.00 with his entry. Every exit he made
cost us twice, and the loss was active, not passive.

Misreading an ENTRY as an EXIT sells a position we meant to hold, so
every refusal here fails closed.
"""

import asyncio
import inspect

from sportsassets import live_executor as le


class _Pool:
    """Minimal stub: canned sibling rows and a canned holding."""

    def __init__(self, siblings=(("TOKEN_B",),), open_sh=1000.0,
                 raise_on=None, mine=0.0):
        self._siblings = [{"token_id": s[0]} for s in siblings]
        # `open_sh` is now DERIVED: net(sibling) = gross(sibling) -
        # gross(this leg), because on a merge venue a holding is
        # retired by buying the complement and the sibling's own sum
        # never decrements. The stub carries both legs so it exercises
        # the query the executor actually runs.
        self._sib = open_sh
        self._mine = mine
        self._raise_on = raise_on

    async def fetch(self, _sql, *_a):
        if self._raise_on == "fetch":
            raise RuntimeError("db down")
        return self._siblings

    async def fetchrow(self, _sql, *_a):
        if self._raise_on == "fetchrow":
            raise RuntimeError("db down")
        return {"sib": self._sib, "mine": self._mine}


def _c(pool, asset="TOKEN_A", whale="swisstony", size=500.0):
    return asyncio.run(le.classify_exit(pool, asset, whale, size))


class TestItRecognisesTheExit:
    def test_a_complement_buy_against_a_holding_is_an_exit(self):
        out = _c(_Pool(open_sh=1000.0), size=800.0)
        assert out is not None
        assert out["asset"] == "TOKEN_B", (
            "the position to sell is the SIBLING — the leg we hold and "
            "he is leaving, not the one he just bought")
        assert out["exit_via_asset"] == "TOKEN_A"

    def test_the_fraction_is_a_pure_share_ratio(self):
        """A complement share retires a held share 1:1, so no price
        conversion enters the fraction. That is what makes it safe to
        compute on the fast path."""
        out = _c(_Pool(open_sh=1000.0), size=250.0)
        assert out["closed_frac"] == 0.25

    def test_a_full_close_reads_as_one(self):
        assert _c(_Pool(open_sh=1000.0), size=1000.0)["closed_frac"] == 1.0

    def test_an_oversized_buy_is_capped_at_one(self):
        """Buying more complement than he holds is an exit of everything
        PLUS a new entry. We mirror the exit part and cap there rather
        than selling more of ours than he closed of his."""
        assert _c(_Pool(open_sh=1000.0), size=4000.0)["closed_frac"] == 1.0


class TestEveryRefusalFailsClosed:
    def test_no_sibling_is_not_an_exit(self):
        assert _c(_Pool(siblings=())) is None

    def test_two_siblings_refuses_rather_than_guessing(self):
        """Multi-outcome markets and negative-risk baskets have no
        single complement. Picking one would sell a real position on a
        coin flip."""
        assert _c(_Pool(siblings=(("B",), ("C",)))) is None

    def test_he_must_actually_hold_the_sibling(self):
        """No holding means this is a genuine new bet on the other side.
        Treating it as an exit would sell a position on a fresh entry
        signal — the failure this whole path exists to stop, inverted."""
        assert _c(_Pool(open_sh=0.0)) is None

    def test_a_net_flat_or_short_holding_is_not_an_exit(self):
        assert _c(_Pool(open_sh=-500.0)) is None

    def test_a_database_failure_is_not_an_exit(self):
        assert _c(_Pool(raise_on="fetch")) is None
        assert _c(_Pool(raise_on="fetchrow")) is None

    def test_missing_identifiers_are_not_an_exit(self):
        assert _c(_Pool(), asset="") is None
        assert _c(_Pool(), whale="") is None

    def test_a_zero_or_negative_size_is_not_an_exit(self):
        assert _c(_Pool(), size=0) is None
        assert _c(_Pool(), size=-10) is None

    def test_an_unparseable_size_is_not_an_exit(self):
        assert _c(_Pool(), size="banana") is None


class TestItIsWiredWhereThePoolAlreadyExists:
    """The first version called get_pool() inside execute_copy — a path
    that had never needed a pool — and added 105 seconds of connect
    retry to the copy path. The reaction-stamp test caught it reading
    135s where it expected 30s. Latency is the thing we are trying to
    REDUCE; a classifier that costs a connection is a regression even
    when its logic is right."""

    def test_it_runs_inside_maybe_execute_after_the_pool_exists(self):
        src = inspect.getsource(le.maybe_execute)
        assert "classify_exit(" in src
        assert src.index("pool = await get_pool()") < src.index(
            "classify_exit(")

    def test_execute_copy_does_not_acquire_a_pool_to_classify(self):
        src = inspect.getsource(le.execute_copy)
        assert "classify_exit" not in src
        assert "get_pool" not in src

    def test_it_classifies_before_any_order_is_sized(self):
        src = inspect.getsource(le.maybe_execute)
        assert src.index("classify_exit(") < src.index("pmus.submit_fok")

    def test_a_classified_exit_never_falls_through_to_the_buy(self):
        src = inspect.getsource(le.maybe_execute)
        i = src.index("await mirror_exit(")
        assert "return" in src[i:i + 200]


class TestTheMoneyGates:
    def test_the_exit_claims_its_row_atomically(self):
        """Five complement fills in one second spawn five execute_copy
        tasks. Without an atomic claim all five read the same 'filled'
        row and all five sell."""
        src = inspect.getsource(le.mirror_exit)
        assert "SET status='exiting'" in src
        assert "AND status='filled' RETURNING id" in src

    def test_a_lost_claim_stops_the_second_caller(self):
        src = inspect.getsource(le.mirror_exit)
        assert "already claimed by another task" in src

    def test_every_refusal_after_the_claim_releases_it(self):
        """A row stranded in 'exiting' is invisible to the settlement
        sweep and blocked from re-entry — a guard doing its job must not
        leave the position frozen."""
        src = inspect.getsource(le.mirror_exit)
        after = src[src.index("SET status='exiting'"):]
        assert after.count("_release_exit_claim") >= 4

    def test_the_sweep_cannot_re_buy_what_we_exited(self):
        from sportsassets.workers import copy_sweep

        src = inspect.getsource(copy_sweep)
        assert "'settled','cashed_out'" in src.replace("\n", "").replace(
            "                                              ", "")
        assert "exiting" in src

    def test_a_short_position_is_sellable(self):
        """netPosition is SIGNED; a short is negative. `qty <= 0` read
        every short as 'nothing here', which made shorts unsellable and
        turned a pricing bug into stranded inventory."""
        src = inspect.getsource(le._pm_held)
        assert "abs(_amt(p.get(\"netPosition\")))" in src

    def test_an_expired_market_still_returns_nothing(self):
        src = inspect.getsource(le._pm_held)
        assert 'p.get("expired")' in src


class TestTheHoldingIsNETOFTHEMERGES:
    """`open_sh` was buys − sells on the sibling ALONE.

    On this venue nobody sells: a holding is retired by BUYING the
    complement, which lands as a BUY on the OTHER asset and never
    decrements the sibling's sum. So open_sh was lifetime GROSS BUYS of
    that leg and could only go up.

    After one completed round trip that does not merely lose precision,
    IT INVERTS THE ANSWER. He buys A, exits by buying B — both legs are
    zero on the venue — but the sum still reads 100 of B. His next
    fresh entry on A then classifies as an EXIT and we SELL a position
    we were meant to hold. That is the one direction this classifier
    must never fail in, and it is the failure the classifier was
    written to prevent, reappearing through its own denominator.

    The venue nets the pair, so the holding does too:
        net(sibling) = gross(sibling) − gross(this leg)
    """

    def test_a_completed_round_trip_leaves_NO_holding(self):
        """Bought A 100, exited by buying B 100. Both legs flat. A
        fresh buy of A must NOT read as an exit."""
        assert _c(_Pool(open_sh=100.0, mine=100.0), size=50.0) is None

    def test_the_old_formula_would_have_called_it_an_exit(self):
        """Same state, sibling gross alone: 100 > 0 -> 'he holds it'."""
        assert _c(_Pool(open_sh=100.0, mine=0.0), size=50.0) is not None

    def test_a_genuine_first_exit_still_classifies(self):
        out = _c(_Pool(open_sh=100.0, mine=0.0), size=60.0)
        assert out is not None
        assert out["his_open_shares"] == 100.0
        assert out["closed_frac"] == 0.6

    def test_adding_to_a_position_is_not_an_exit(self):
        """He holds A and buys more A. gross(sibling)=0, gross(mine)>0,
        so the net is negative and clamps to zero."""
        assert _c(_Pool(open_sh=0.0, mine=100.0), size=50.0) is None

    def test_a_partially_exited_position_reports_what_is_LEFT(self):
        """Bought A 100, closed 40 of it. He holds 60 of A. A further
        complement buy must size against 60, not 100."""
        out = _c(_Pool(open_sh=100.0, mine=40.0), size=30.0)
        assert out["his_open_shares"] == 60.0
        assert out["closed_frac"] == 0.5

    def test_the_net_can_never_go_negative(self):
        out = _c(_Pool(open_sh=10.0, mine=999.0), size=5.0)
        assert out is None

    def test_the_query_reads_BOTH_legs(self):
        import inspect

        src = inspect.getsource(le.classify_exit)
        assert "t.asset IN ($1, $2)" in src
        assert 'AS sib' in src and 'AS mine' in src

    def test_the_current_fill_is_excluded_by_id(self):
        """It is already in `trades` when we are called, and it is a buy
        of THIS leg — leaving it in subtracts his own exit from the
        position it is closing."""
        import inspect

        src = inspect.getsource(le.classify_exit)
        assert "t.id <> COALESCE($4::bigint, -1)" in src

    def test_the_caller_passes_the_trade_id(self):
        import inspect

        src = inspect.getsource(le.maybe_execute)
        i = src.index("classify_exit(")
        assert 'payload.get("id")' in src[i:i + 260]

    def test_a_missing_trade_id_still_works(self):
        """COALESCE(-1) means 'exclude nothing', which is the old
        behaviour and safe — it can only make open_sh smaller."""
        out = asyncio.run(le.classify_exit(
            _Pool(open_sh=100.0, mine=0.0), "TOKEN_A", "w", 50.0))
        assert out is not None


class TestAFullCloseDoesNotErasePartialPnl:
    """The partial branch accumulated (COALESCE(pnl,0)+$3) and the
    terminal branch four lines later ASSIGNED (pnl=$2). So the moment a
    whale who had already trimmed finally closed out, every dollar
    realised by his earlier partial exits was erased from the row.

    Reachable on exactly the flow this desk exists to copy: scale out,
    then close. The owner's own worked example was buy $5,000, sell
    $7,500, re-enter at $60."""

    @staticmethod
    def _code():
        """mirror_exit with COMMENTS STRIPPED.

        Three of my assertions today have matched text that my own
        explanatory comments contained — the comment above this fix
        quotes both SQL fragments on purpose. A source-reading test has
        to read code."""
        import inspect

        return "\n".join(
            l for l in inspect.getsource(le.mirror_exit).splitlines()
            if not l.strip().startswith("#"))

    def test_both_branches_accumulate(self):
        assert self._code().count("COALESCE(pnl,0)+$") == 2, \
            "one branch still assigns instead of accumulating"

    def test_the_terminal_branch_is_the_one_that_changed(self):
        code = self._code()
        seg = code[code.index("status='cashed_out'"):][:200]
        assert "COALESCE(pnl,0)+$2" in seg
        assert "pnl=$2," not in seg

    def test_a_null_pnl_contributes_zero_not_null(self):
        """An unpriced final leg must not wipe the row to NULL."""
        code = self._code()
        seg = code[code.index("status='cashed_out'"):][:320]
        assert "pnl or 0" in seg
