"""Three instruments agreed there were no exits. All three were the same
instrument.

SIDES read the trade feed for side='SELL' and found 0 across 860,669
fills. EXITS defined a round trip as same-asset BUY-then-SELL and found
0. CUTCHECK needed a cashout basis, could not build one, and printed
"NO EXIT DATA". That is not three confirmations — it is one definition
of "sale" queried three ways, and three whales were cut on it.

A merge is the sale. Buying N of the complementary leg retires N held
shares and returns $N, because YES + NO is worth exactly $1. The
realised P&L is therefore

    N * (1 - avg_cost_of_the_held_leg - price_paid_for_the_complement)

and every input has been sitting in the trades table the whole time.
"""

import pytest

from sportsassets.analytics import merge_pnl as mp
from sportsassets.analytics.merge_pnl import DUST, replay


class _FakePool:
    """A pool that speaks the CURSOR protocol the replay now uses.

    whale_merge_pnl streams via `acquire -> transaction -> cursor`
    since 2026-08-25, because materialising 600,000 dicts per whale
    across seven whales 502'd the API at ~545MB RSS. A stub that only
    answers `fetch` would silently stop exercising the real path — the
    shape of test that keeps passing while production breaks.
    """

    def __init__(self, rows, payouts=None):
        self._rows = list(rows)
        self._payouts = list(payouts or [])
        self.bound = None
        self.batches = 0

    # --- cursor protocol -------------------------------------------
    class _Cursor:
        def __init__(self, pool):
            self._pool = pool
            self._i = 0

        async def fetch(self, n):
            self._pool.batches += 1
            out = self._pool._rows[self._i:self._i + n]
            self._i += len(out)
            return out

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Conn:
        def __init__(self, pool):
            self._pool = pool

        def transaction(self):
            return _FakePool._Tx()

        async def cursor(self, _sql, *a):
            self._pool.bound = a[1] if len(a) > 1 else None
            return _FakePool._Cursor(self._pool)

    class _Acq:
        def __init__(self, pool):
            self._pool = pool

        async def __aenter__(self):
            return _FakePool._Conn(self._pool)

        async def __aexit__(self, *a):
            return False

    def acquire(self):
        return _FakePool._Acq(self)

    # --- the payout lookup -----------------------------------------
    async def fetch(self, _sql, *_a):
        return self._payouts




def _f(cid, idx, side, size, price):
    return {"condition_id": cid, "outcome_index": idx, "side": side,
            "size": size, "price": price}


class TestTheMergeIsRecognised:
    def test_the_worked_example(self):
        """Buy 1000 YES @0.23, then 800 NO @0.70. The 800 pair returns
        $800 against 800*(0.23+0.70) = $744 of cost."""
        r = replay([_f("c1", 0, "BUY", 1000, 0.23),
                    _f("c1", 1, "BUY", 800, 0.70)])
        assert r["n_merges"] == 1
        assert r["merge_shares"] == 800.0
        assert r["realized_merge_pnl"] == round(800 * (1 - 0.23 - 0.70), 2)

    def test_the_merge_burns_both_legs(self):
        """After merging 800, only 200 YES remain — a later complement
        buy must not merge against shares that no longer exist."""
        r = replay([_f("c1", 0, "BUY", 1000, 0.23),
                    _f("c1", 1, "BUY", 800, 0.70),
                    _f("c1", 1, "BUY", 500, 0.60)])
        assert r["merge_shares"] == 1000.0, "800 then 200, not 1300"
        assert r["open_shares"] == 300.0, "300 NO left over"

    def test_an_oversized_complement_buy_splits(self):
        """Buying more complement than held is an exit of everything
        PLUS a new position on the other side."""
        r = replay([_f("c1", 0, "BUY", 100, 0.40),
                    _f("c1", 1, "BUY", 250, 0.55)])
        assert r["merge_shares"] == 100.0
        assert r["open_shares"] == 150.0

    def test_a_losing_merge_is_reported_as_a_loss(self):
        """1 - 0.60 - 0.55 is negative. An exit basis that could only
        produce gains would be the settlement bug in a new costume."""
        r = replay([_f("c1", 0, "BUY", 100, 0.60),
                    _f("c1", 1, "BUY", 100, 0.55)])
        assert r["realized_merge_pnl"] == round(100 * (1 - 0.60 - 0.55), 2)
        assert r["realized_merge_pnl"] < 0


class TestItDoesNotInventExits:
    def test_two_buys_on_the_same_leg_are_both_entries(self):
        r = replay([_f("c1", 0, "BUY", 100, 0.40),
                    _f("c1", 0, "BUY", 100, 0.45)])
        assert r["n_merges"] == 0 and r["n_entries"] == 2
        assert r["open_shares"] == 200.0

    def test_opposite_legs_of_DIFFERENT_markets_never_merge(self):
        r = replay([_f("c1", 0, "BUY", 100, 0.40),
                    _f("c2", 1, "BUY", 100, 0.55)])
        assert r["n_merges"] == 0

    def test_a_complement_buy_with_no_holding_is_an_entry(self):
        r = replay([_f("c1", 1, "BUY", 100, 0.55)])
        assert r["n_merges"] == 0 and r["open_shares"] == 100.0

    def test_order_matters_the_way_time_does(self):
        """The complement must already be held. Reversing the two fills
        makes the first a plain entry."""
        a = replay([_f("c1", 0, "BUY", 100, 0.40),
                    _f("c1", 1, "BUY", 100, 0.55)])
        b = replay([_f("c1", 1, "BUY", 100, 0.55),
                    _f("c1", 0, "BUY", 100, 0.40)])
        assert a["n_merges"] == b["n_merges"] == 1

    def test_dust_does_not_count_as_a_position(self):
        r = replay([_f("c1", 0, "BUY", DUST / 2, 0.40),
                    _f("c1", 1, "BUY", 100, 0.55)])
        assert r["n_merges"] == 0


class TestPlainSellsStillWork:
    def test_a_real_sell_is_graded_against_its_cost(self):
        r = replay([_f("c1", 0, "BUY", 100, 0.40),
                    _f("c1", 0, "SELL", 100, 0.60)])
        assert r["n_sells"] == 1
        assert r["realized_sell_pnl"] == round(100 * (0.60 - 0.40), 2)

    def test_selling_more_than_held_only_grades_what_was_held(self):
        r = replay([_f("c1", 0, "BUY", 100, 0.40),
                    _f("c1", 0, "SELL", 500, 0.60)])
        assert r["realized_sell_pnl"] == round(100 * (0.60 - 0.40), 2)

    def test_a_sell_with_nothing_held_realises_nothing(self):
        r = replay([_f("c1", 0, "SELL", 100, 0.60)])
        assert r["n_sells"] == 0 and r["realized_sell_pnl"] == 0.0


class TestMalformedRowsAreSkippedNotGuessed:
    @pytest.mark.parametrize("bad", [
        {"condition_id": None, "outcome_index": 0, "side": "BUY",
         "size": 10, "price": 0.5},
        {"condition_id": "c", "outcome_index": None, "side": "BUY",
         "size": 10, "price": 0.5},
        {"condition_id": "c", "outcome_index": 7, "side": "BUY",
         "size": 10, "price": 0.5},
        {"condition_id": "c", "outcome_index": 0, "side": "BUY",
         "size": 0, "price": 0.5},
        {"condition_id": "c", "outcome_index": 0, "side": "BUY",
         "size": "x", "price": 0.5},
    ])
    def test_it_skips_rather_than_crashing(self, bad):
        assert replay([bad])["n_entries"] == 0

    def test_an_empty_ledger_reports_no_roi_rather_than_zero(self):
        """0.0 would read as 'this whale broke even'. None reads as
        'there is nothing here', which is what is true."""
        assert replay([])["roi_on_entries"] is None


class TestTheRoiDenominatorIsEntriesOnly:
    def test_merged_out_capital_is_not_double_counted(self):
        """The complement leg of a merge is not new capital at risk —
        it is the cost of closing. Counting it as an entry would
        understate ROI by inflating the denominator."""
        r = replay([_f("c1", 0, "BUY", 1000, 0.23),
                    _f("c1", 1, "BUY", 1000, 0.70)])
        assert r["entry_notional"] == 230.0
        assert r["roi_on_entries"] == round(
            r["realized_total"] / 230.0, 4)


class TestTheQueryTakesADateNotAString:
    """Two probes of this endpoint returned 500 before anyone knew why.

    asyncpg infers a parameter's type from the CAST it sits under, so
    `$2::date` expects a datetime.date and rejects a str. The codebase
    already had the idiom — api_true_edge_cashout converts with
    `_dt.fromisoformat(since_day).date()` before binding — and I wrote a
    new query beside it without following it.

    The first probe said only "unavailable", which is why it took two
    rounds: the line could not tell a 500 from a timeout from an empty
    body. HTTP status and body are printed now.
    """

    def test_the_replay_query_binds_a_date_object(self):
        import inspect

        from sportsassets.analytics import merge_pnl as m

        src = inspect.getsource(m.whale_merge_pnl)
        assert "since_d" in src
        assert "fromisoformat" in src
        # The cast must be gone from the SQL itself, comments aside.
        #
        # Sliced by REGEX rather than by literal surrounding text: the
        # first version keyed on the exact indentation and the exact
        # argument line, and broke the moment the query moved into a
        # streaming cursor. A test that breaks on reformatting is a
        # test that will be edited to pass rather than read.
        import re as _re

        m2 = _re.search(r"SELECT t\.condition_id.*?ORDER BY[^\"]*",
                        src, _re.S)
        assert m2, "the replay query is no longer recognisable"
        sql = m2.group(0)
        assert "::date" not in sql

    def test_the_cashflow_query_binds_a_date_object(self):
        import inspect

        from sportsassets.api import app as app_mod

        import re as _re

        src = inspect.getsource(app_mod.api_whale_merge_pnl)
        assert ".date()" in src, "the string must still be converted"
        # Both branches — whole-book and windowed. Sliced by regex, not
        # by surrounding literals: the windowed form now sits beside an
        # unwindowed one, and a slice keyed on the old neighbour text
        # would silently check nothing.
        sqls = _re.findall(r"SELECT COALESCE\(sum\(.*?\)\)", src, _re.S)
        assert len(sqls) == 2, f"expected both branches, got {len(sqls)}"
        for sql in sqls:
            assert "::date" not in sql

    def test_the_cashflow_shares_the_replays_window(self):
        """Two numbers on one row measured over different spans is how
        a reader draws a conclusion neither supports. And with the new
        whole-book default, fromisoformat("") would simply raise."""
        import inspect

        from sportsassets.api import app as app_mod

        src = inspect.getsource(app_mod.api_whale_merge_pnl)
        assert "_since_d = (" in src
        assert "if since else None" in src
        assert "if _since_d is None:" in src

    def test_a_date_object_passes_straight_through(self):
        import asyncio
        import datetime as dt

        from sportsassets.analytics.merge_pnl import whale_merge_pnl

        seen = {}

        pool = _FakePool([])
        asyncio.run(whale_merge_pnl(pool, ["w"], dt.date(2026, 8, 1)))
        seen["bound"] = pool.bound
        assert seen["bound"] == dt.date(2026, 8, 1)

    def test_a_string_is_converted_not_rejected(self):
        import asyncio
        import datetime as dt

        from sportsassets.analytics.merge_pnl import whale_merge_pnl

        seen = {}

        pool = _FakePool([])
        asyncio.run(whale_merge_pnl(pool, ["w"], "2026-08-01"))
        seen["bound"] = pool.bound
        assert seen["bound"] == dt.date(2026, 8, 1)


class TestTruncationIsReportedNotSilent:
    def test_a_capped_replay_says_so(self):
        import asyncio

        from sportsassets.analytics.merge_pnl import whale_merge_pnl

        pool = _FakePool([{"condition_id": "c", "outcome_index": 0,
                           "side": "BUY", "size": 1, "price": 0.5}] * 3)
        out = asyncio.run(whale_merge_pnl(pool, ["w"], "2026-08-01",
                                          max_fills=3))
        assert out["w"]["truncated"] is True
        assert "floors, not totals" in out["w"]["verdict_note"]

    def test_an_uncapped_replay_does_not_claim_truncation(self):
        import asyncio

        from sportsassets.analytics.merge_pnl import whale_merge_pnl

        pool = _FakePool([{"condition_id": "c", "outcome_index": 0,
                           "side": "BUY", "size": 1, "price": 0.5}])
        out = asyncio.run(whale_merge_pnl(pool, ["w"], "2026-08-01",
                                          max_fills=100))
        assert out["w"]["truncated"] is False
        assert "verdict_note" not in out["w"]


# ────────────────────────────────────────────────────────────────────
# THE COUNTERFACTUAL (owner question 2026-08-25).
#
# Every whale number this desk has published grades at RESOLUTION —
# a world in which the exit never happens. The owner's position is that
# for a number of the copied whales the exits ARE the edge, and the
# probe's TRUEEDGE table supports it: on our detected flow, rn1 and
# ferrari grade NEGATIVE held to settlement while their merge-inclusive
# books are +$222k and +$217k, and HomeRunHazard and SwissTony grade
# POSITIVE held to settlement while their real books are -$35k and
# -$188k. Four of six flip sign, in both directions.
#
# That comparison was across two different populations (our detected
# subsample vs their full book), which makes it evidence and not proof.
# This measures both worlds over the SAME fills, so the difference is
# the exits and nothing else.
#
#     actual          q * (exit_price - avg_cost)
#     held to settle  q * (payout     - avg_cost)
#     EXIT VALUE      q * (exit_price - payout)
#
# avg_cost cancels: the exit value does not depend on what he paid,
# only on where he got out versus where it finished.

class TestTheExitValueArithmetic:
    def _fills(self, entry_px, complement_px, size=100.0):
        """Buy leg 0, then close it by buying leg 1 (a merge)."""
        return [
            {"condition_id": "c", "outcome_index": 0, "side": "BUY",
             "size": size, "price": entry_px},
            {"condition_id": "c", "outcome_index": 1, "side": "BUY",
             "size": size, "price": complement_px},
        ]

    def test_exiting_a_position_that_would_have_LOST_is_worth_money(self):
        """Bought leg0 at 0.40, closed it by buying leg1 at 0.30 — so he
        sold leg0 at 0.70. Leg0 then lost (payout 0).

        actual   100 * (0.70 - 0.40) = +30
        held     100 * (0.00 - 0.40) = -40
        exit     100 * (0.70 - 0.00) = +70
        """
        r = mp.replay(self._fills(0.40, 0.30), {"c": [0.0, 1.0]})
        assert r["cf_actual_on_graded"] == 30.0
        assert r["cf_hold_on_graded"] == -40.0
        assert r["exit_value"] == 70.0

    def test_exiting_a_position_that_would_have_WON_costs_money(self):
        """Same trade, but leg0 wins (payout 1).

        actual   100 * (0.70 - 0.40) = +30
        held     100 * (1.00 - 0.40) = +60
        exit     100 * (0.70 - 1.00) = -30
        """
        r = mp.replay(self._fills(0.40, 0.30), {"c": [1.0, 0.0]})
        assert r["cf_actual_on_graded"] == 30.0
        assert r["cf_hold_on_graded"] == 60.0
        assert r["exit_value"] == -30.0

    def test_the_entry_price_cancels_out_of_the_exit_value(self):
        """The property that makes this measurement clean."""
        a = mp.replay(self._fills(0.40, 0.30), {"c": [0.0, 1.0]})
        b = mp.replay(self._fills(0.10, 0.30), {"c": [0.0, 1.0]})
        assert a["exit_value"] == b["exit_value"] == 70.0
        assert a["cf_actual_on_graded"] != b["cf_actual_on_graded"]

    def test_a_plain_SELL_is_graded_the_same_way(self):
        fills = [
            {"condition_id": "c", "outcome_index": 0, "side": "BUY",
             "size": 100.0, "price": 0.40},
            {"condition_id": "c", "outcome_index": 0, "side": "SELL",
             "size": 100.0, "price": 0.70},
        ]
        r = mp.replay(fills, {"c": [0.0, 1.0]})
        assert r["exit_value"] == 70.0

    def test_the_merge_exit_price_is_ONE_MINUS_the_complement(self):
        """A merge is a sale of the held leg at (1 - complement price),
        because the pair returns exactly $1. Getting this backwards
        would invert every exit value."""
        r = mp.replay(self._fills(0.40, 0.30), {"c": [0.0, 1.0]})
        # exit at 0.70, not 0.30
        assert r["cf_actual_on_graded"] == 30.0


class TestUnknownPayoutsAreExcludedNotAssumed:
    """A missing resolution silently read as 'it lost' would manufacture
    exit value out of nothing — the exact shape of the number this is
    built to check."""

    def _fills(self):
        return [
            {"condition_id": "c", "outcome_index": 0, "side": "BUY",
             "size": 100.0, "price": 0.40},
            {"condition_id": "c", "outcome_index": 1, "side": "BUY",
             "size": 100.0, "price": 0.30},
        ]

    def test_no_payout_means_no_grade(self):
        r = mp.replay(self._fills(), {})
        assert r["cf_graded_shares"] == 0.0
        assert r["cf_ungraded_shares"] == 100.0
        assert r["exit_value"] == 0.0

    def test_the_exclusion_is_reported(self):
        r = mp.replay(self._fills(), {})
        assert "EXCLUDED" in r["cf_note"]
        assert r["cf_coverage"] == 0.0

    def test_full_coverage_reports_one(self):
        r = mp.replay(self._fills(), {"c": [0.0, 1.0]})
        assert r["cf_coverage"] == 1.0
        assert "cf_note" not in r

    def test_a_malformed_payout_is_unknown_not_zero(self):
        for bad in ({"c": "x"}, {"c": []}, {"c": [None, None]},
                    {"c": ["a", "b"]}):
            r = mp.replay(self._fills(), bad)
            assert r["cf_graded_shares"] == 0.0, bad
            assert r["exit_value"] == 0.0, bad


class TestOnlyCLOSEDSharesAreCompared:
    def test_an_open_position_is_not_graded_in_either_world(self):
        """It never exited in either world, so it cancels. Grading it
        would measure his open book, not his exits."""
        fills = [{"condition_id": "c", "outcome_index": 0, "side": "BUY",
                  "size": 100.0, "price": 0.40}]
        r = mp.replay(fills, {"c": [1.0, 0.0]})
        assert r["cf_closed_shares"] == 0.0
        assert r["exit_value"] == 0.0
        assert r["open_shares"] == 100.0

    def test_a_partial_exit_grades_only_the_part_that_closed(self):
        fills = [
            {"condition_id": "c", "outcome_index": 0, "side": "BUY",
             "size": 100.0, "price": 0.40},
            {"condition_id": "c", "outcome_index": 1, "side": "BUY",
             "size": 30.0, "price": 0.30},
        ]
        r = mp.replay(fills, {"c": [0.0, 1.0]})
        assert r["cf_closed_shares"] == 30.0
        assert r["exit_value"] == round(30 * 0.70, 2)


class TestItIsOffByDefault:
    def test_no_payouts_argument_leaves_the_old_result_shape(self):
        fills = [{"condition_id": "c", "outcome_index": 0, "side": "BUY",
                  "size": 100.0, "price": 0.40}]
        r = mp.replay(fills)
        assert r["exit_value"] == 0.0
        assert "cf_note" not in r
        assert r["realized_total"] == 0.0

    def test_the_actual_pnl_is_unchanged_by_supplying_payouts(self):
        fills = [
            {"condition_id": "c", "outcome_index": 0, "side": "BUY",
             "size": 100.0, "price": 0.40},
            {"condition_id": "c", "outcome_index": 1, "side": "BUY",
             "size": 100.0, "price": 0.30},
        ]
        a = mp.replay(fills)
        b = mp.replay(fills, {"c": [0.0, 1.0]})
        assert a["realized_total"] == b["realized_total"]
        assert a["realized_merge_pnl"] == b["realized_merge_pnl"]


class TestTheQueryCannotFabricateCoverage:
    def test_a_missing_market_row_does_not_drop_the_fill(self):
        import inspect

        src = inspect.getsource(mp.whale_merge_pnl)
        # The payout lookup is a SUBQUERY now, not an explicit id list:
        # collecting the condition ids from the fills was the other
        # reason a full pass had to be materialised, and materialising
        # is what took the API to 1,133MB and 502'd every endpoint.
        # What matters is unchanged — the fills are not INNER JOINed to
        # markets, so a market row we lack cannot drop his fill from
        # the replay; it reads as an unknown payout, which the
        # counterfactual excludes and reports.
        assert "SELECT DISTINCT t.condition_id FROM trades t" in src
        assert "resolved, false) = true" in src
        assert "JOIN markets" not in src.split("async def _rows")[1], \
            "the fills query must not join markets"

    def test_a_failed_payout_lookup_yields_NO_counterfactual(self):
        import inspect

        src = inspect.getsource(mp.whale_merge_pnl)
        assert "payouts = {}" in src
        assert "_errors" in src


class TestTheErrorCannotBecomeAnEighthWhale:
    """A "_errors" key beside the whales reads as another whale to
    every caller that iterates the map — the diagnostic's own jq does
    exactly that. A fabricated row in a P&L table is worse than the
    error it was reporting."""

    def test_the_error_lives_on_the_whales_own_result(self):
        import inspect

        src = inspect.getsource(mp.whale_merge_pnl)
        assert 'out["cf_error"] = cf_error' in src
        assert 'res.setdefault("_errors"' not in src

    def test_the_endpoint_verdict_never_claims_a_priced_comparison(self):
        import inspect

        from sportsassets.api import app as A

        src = inspect.getsource(A.api_whale_merge_pnl)
        assert "NO GRADED EXITS" in src
        assert "NOT evidence that the exits were worthless" in src

    def test_thin_coverage_is_flagged_not_hidden(self):
        import inspect

        from sportsassets.api import app as A

        src = inspect.getsource(A.api_whale_merge_pnl)
        assert "thin coverage" in src


class TestTheTwoAccountingsAgree:
    """The counterfactual re-derives the ACTUAL side independently of
    the realized-P&L path. At full coverage the two must agree exactly
    — if they drift, one of them is wrong and the exit value inherits
    the error silently."""

    def test_at_full_coverage_actual_equals_realized(self):
        fills = [
            {"condition_id": "a", "outcome_index": 0, "side": "BUY",
             "size": 100.0, "price": 0.40},
            {"condition_id": "a", "outcome_index": 1, "side": "BUY",
             "size": 100.0, "price": 0.30},
            {"condition_id": "b", "outcome_index": 1, "side": "BUY",
             "size": 250.0, "price": 0.62},
            {"condition_id": "b", "outcome_index": 0, "side": "BUY",
             "size": 90.0, "price": 0.31},
            {"condition_id": "c", "outcome_index": 0, "side": "BUY",
             "size": 40.0, "price": 0.15},
            {"condition_id": "c", "outcome_index": 0, "side": "SELL",
             "size": 40.0, "price": 0.55},
        ]
        pay = {"a": [0.0, 1.0], "b": [1.0, 0.0], "c": [1.0, 0.0]}
        r = mp.replay(fills, pay)
        assert r["cf_coverage"] == 1.0
        assert r["cf_actual_on_graded"] == pytest.approx(
            r["realized_total"], abs=0.02)

    def test_exit_value_is_actual_minus_held(self):
        fills = [
            {"condition_id": "a", "outcome_index": 0, "side": "BUY",
             "size": 100.0, "price": 0.40},
            {"condition_id": "a", "outcome_index": 1, "side": "BUY",
             "size": 100.0, "price": 0.30},
        ]
        r = mp.replay(fills, {"a": [0.0, 1.0]})
        assert r["exit_value"] == pytest.approx(
            r["cf_actual_on_graded"] - r["cf_hold_on_graded"])

    def test_a_whale_who_never_exits_has_zero_exit_value(self):
        fills = [{"condition_id": "a", "outcome_index": 0, "side": "BUY",
                  "size": 100.0, "price": 0.40}] * 3
        r = mp.replay(fills, {"a": [1.0, 0.0]})
        assert r["n_merges"] == 0
        assert r["exit_value"] == 0.0


class TestTheWhalesOwnEdgeGetsAnInterval:
    """'rn1 is profitable' has been asserted from a total all day.
    +$231,495 on $24.5M of entries is +0.94% on dollar deployed, and
    whether 94 basis points is real or noise depends entirely on the
    dispersion behind it — which nothing was carrying.

    Same ratio estimator the copy sleeve is judged by, so the whale's
    edge and ours land on one scale and can be compared directly."""

    def _book(self, n, complement_px, entry_px=0.40, size=100):
        f = []
        for i in range(n):
            c = f"c{i}"
            f += [{"condition_id": c, "outcome_index": 0, "side": "BUY",
                   "size": size, "price": entry_px},
                  {"condition_id": c, "outcome_index": 1, "side": "BUY",
                   "size": size, "price": complement_px(i)}]
        return f

    def test_a_consistent_winner_is_called_profitable(self):
        r = mp.replay(self._book(2000, lambda i: 0.55 if i % 2 else 0.57))
        assert r["edge_ci95"][0] > 0
        assert "PROFITABLE at 95%" in r["edge_verdict"]

    def test_a_consistent_loser_is_called_losing(self):
        r = mp.replay(self._book(2000, lambda i: 0.65 if i % 2 else 0.67))
        assert r["edge_ci95"][1] < 0
        assert "LOSING at 95%" in r["edge_verdict"]

    def test_a_thin_edge_in_heavy_noise_is_NOT_DEMONSTRATED(self):
        """The case that matters: a small positive point estimate that
        the sample cannot support. This is where 'rn1 is profitable'
        has to be able to fail."""
        r = mp.replay(self._book(40, lambda i: 0.20 if i % 2 else 0.95))
        assert r["edge_ci95"][0] < 0 < r["edge_ci95"][1]
        assert "NOT DEMONSTRATED" in r["edge_verdict"]
        assert "contains zero" in r["edge_verdict"]

    def test_a_book_with_no_exits_cannot_be_graded(self):
        r = mp.replay([{"condition_id": "c", "outcome_index": 0,
                        "side": "BUY", "size": 100, "price": 0.4}])
        assert r["edge_roi"] is None
        assert "INSUFFICIENT" in r["edge_verdict"]

    def test_the_stake_is_what_he_had_AT_RISK_on_the_closed_lot(self):
        """Not his entry notional across the whole book — the lot's own
        cost basis, so the ratio is a return on the dollars that
        actually closed."""
        r = mp.replay(self._book(1, lambda i: 0.55))
        assert r["edge_deployed"] == pytest.approx(40.0)

    def test_the_roi_matches_the_realized_total_over_the_deployed(self):
        r = mp.replay(self._book(500, lambda i: 0.55 if i % 2 else 0.57))
        assert r["edge_roi"] == pytest.approx(
            r["realized_total"] / r["edge_deployed"], rel=1e-4)

    def test_it_is_computed_in_constant_memory(self):
        """These books run to 90,000 merges each; keeping the lots to
        compute a variance is not available."""
        import inspect

        src = inspect.getsource(mp._replay_stepper)
        assert "lot_ss" in src and "lot_ps" in src
        r = mp.replay(self._book(50, lambda i: 0.55))
        assert "lots_list" not in r and "lot_rows" not in r

    def test_float_cancellation_cannot_raise(self):
        """sum(p^2) - 2R*sum(ps) + R^2*sum(s^2) can go slightly
        negative on a large book. An exception in a P&L report is
        worse than a degenerate interval."""
        o = {"lots": 5, "lot_s": 100.0, "lot_p": 1.0,
             "lot_ss": 2000.0, "lot_pp": 0.2, "lot_ps": 20.0}
        got = mp._lot_interval(o)
        assert got["edge_ci95"] is not None
        assert got["edge_se"] >= 0

    def test_the_response_shape_does_not_change_with_the_verdict(self):
        """A shape that changes with the news makes every reader
        special-case the bad case, which is where readers stop looking."""
        good = mp.replay(self._book(500, lambda i: 0.55))
        thin = mp.replay(self._book(1, lambda i: 0.55))
        none_ = mp.replay([])
        for k in ("edge_lots", "edge_deployed", "edge_roi", "edge_se",
                  "edge_ci95", "edge_verdict"):
            assert k in good and k in thin and k in none_, k


class TestThePreWindowPositionWasBookedAsAnEntry:
    """The replay seeds every balance at zero, so a windowed run turns
    every pre-window position's EXIT into a fresh ENTRY.

    Three errors from one omission, all pushing measured ROI toward
    zero on the numbers the ROSTER is graded with:
      * the realised P&L of that exit vanishes
      * its cost inflates entry_notional — the ROI DENOMINATOR
      * the phantom position inflates open_shares

    Found by an adversarial review, and it is the reason `since`
    defaults to the whole book now.
    """

    def _closed_inside_only(self):
        """The complement buy alone — its entry predates the window."""
        return [{"condition_id": "c", "outcome_index": 1, "side": "BUY",
                 "size": 100.0, "price": 0.30}]

    def _the_whole_truth(self):
        return [{"condition_id": "c", "outcome_index": 0, "side": "BUY",
                 "size": 100.0, "price": 0.40}] + self._closed_inside_only()

    def test_the_exit_reads_as_an_entry_when_the_open_is_out_of_window(self):
        r = mp.replay(self._closed_inside_only())
        assert r["n_merges"] == 0
        assert r["n_entries"] == 1
        assert r["realized_total"] == 0.0

    def test_the_same_trade_is_a_merge_with_its_open_in_scope(self):
        r = mp.replay(self._the_whole_truth())
        assert r["n_merges"] == 1
        assert r["realized_total"] == 30.0

    def test_the_denominator_is_inflated_by_the_phantom_entry(self):
        clipped = mp.replay(self._closed_inside_only())
        whole = mp.replay(self._the_whole_truth())
        assert clipped["entry_notional"] == 30.0    # the exit, miscounted
        assert whole["entry_notional"] == 40.0      # the real entry
        assert clipped["open_shares"] == 100.0      # phantom position
        assert whole["open_shares"] == 0.0

    def test_the_default_is_now_the_whole_book(self):
        import inspect

        sig = inspect.signature(mp.whale_merge_pnl)
        assert sig.parameters["since"].default is None

    def test_no_date_filter_is_applied_when_since_is_none(self):
        import asyncio

        pool = _FakePool([{"condition_id": "c", "outcome_index": 0,
                           "side": "BUY", "size": 1, "price": 0.5}])
        out = asyncio.run(mp.whale_merge_pnl(pool, ["w"], None))
        assert pool.bound is None, "a bound date means a filter was applied"
        assert out["w"]["window"] == "whole book"

    def test_a_windowed_call_still_works_and_SAYS_WHAT_IT_COSTS(self):
        import asyncio

        pool = _FakePool([{"condition_id": "c", "outcome_index": 0,
                           "side": "BUY", "size": 1, "price": 0.5}])
        out = asyncio.run(mp.whale_merge_pnl(pool, ["w"], "2026-08-01"))
        assert "2026-08-01" in out["w"]["window"]
        assert "booked as a fresh entry" in out["w"]["window_warning"]

    def test_the_whole_book_run_carries_no_warning(self):
        import asyncio

        pool = _FakePool([])
        out = asyncio.run(mp.whale_merge_pnl(pool, ["w"], None))
        assert "window_warning" not in out["w"]

    def test_the_benchmark_publisher_uses_the_whole_book(self):
        import inspect

        from sportsassets.workers import analytics as an

        src = inspect.getsource(an.publish_whale_benchmark)
        assert "whale_merge_pnl(pool, list(COPY_WHALES), None)" in src
        assert "2026-08-01" not in src


class TestItActuallyStreamsThisTime:
    """The first "streaming" version replaced pool.fetch with a
    server-side cursor and then appended every row to a list — the same
    peak memory with more machinery. It shipped described as streamed,
    and the API went on dying:

        22:42:54  rss_mb 1133.8
        PROOFHTTP code=502   MERGEHTTP code=502   SHORTHTTP code=502
        MEMCENSUS unreadable   UNMAPPED unreadable

    Every instrument built today to prove profitability was answering
    502 because of the query behind it. swisstony alone has 283,748
    fills; seven whales of dicts is several hundred megabytes on a
    ~512MB container."""

    def test_no_list_of_fills_is_built(self):
        import inspect

        src = inspect.getsource(mp.whale_merge_pnl)
        assert "fills.append" not in src
        assert "fills = []" not in src

    def test_the_rows_come_from_an_async_generator(self):
        import inspect

        src = inspect.getsource(mp.whale_merge_pnl)
        assert "async def _rows()" in src
        assert "yield r" in src
        assert "await replay_stream(_rows()" in src

    def test_the_payouts_no_longer_need_the_fills(self):
        """Collecting condition ids from a materialised pass was the
        other reason the whole book had to be held."""
        import inspect

        src = inspect.getsource(mp.whale_merge_pnl)
        i = src.index("async def _rows()")
        assert "SELECT DISTINCT t.condition_id" in src[:i], \
            "payouts must be resolved BEFORE the stream begins"

    def test_both_forms_drive_the_SAME_stepper(self):
        """Two implementations of one replay is how they drift, and
        this arithmetic is what the entire roster is graded on."""
        import inspect

        for fn in (mp.replay, mp.replay_stream):
            assert "_replay_stepper(payouts)" in inspect.getsource(fn)

    def test_the_streaming_form_agrees_with_the_pure_one(self):
        import asyncio

        fills = [
            {"condition_id": "a", "outcome_index": 0, "side": "BUY",
             "size": 100.0, "price": 0.40},
            {"condition_id": "a", "outcome_index": 1, "side": "BUY",
             "size": 100.0, "price": 0.30},
            {"condition_id": "b", "outcome_index": 1, "side": "BUY",
             "size": 250.0, "price": 0.62},
            {"condition_id": "b", "outcome_index": 0, "side": "BUY",
             "size": 90.0, "price": 0.31},
            {"condition_id": "c", "outcome_index": 0, "side": "BUY",
             "size": 40.0, "price": 0.15},
            {"condition_id": "c", "outcome_index": 0, "side": "SELL",
             "size": 40.0, "price": 0.55},
        ]
        pay = {"a": [0.0, 1.0], "b": [1.0, 0.0], "c": [1.0, 0.0]}

        async def _gen():
            for f in fills:
                yield f

        a = mp.replay(fills, pay)
        b = asyncio.run(mp.replay_stream(_gen(), pay))
        assert a == b

    def test_the_fill_COUNT_comes_from_the_stream_not_a_list(self):
        import inspect

        src = inspect.getsource(mp.whale_merge_pnl)
        assert 'out["fills_read"] = n_read' in src
        assert "len(rows)" not in src

    def test_a_SELL_still_ends_that_fill(self):
        """The loop body became a per-fill function, so every `continue`
        had to become `return`. A missed one would fall through into
        the BUY branch and double-book the fill."""
        fills = [
            {"condition_id": "c", "outcome_index": 0, "side": "BUY",
             "size": 100.0, "price": 0.40},
            {"condition_id": "c", "outcome_index": 0, "side": "SELL",
             "size": 40.0, "price": 0.70},
        ]
        r = mp.replay(fills)
        assert r["n_sells"] == 1
        assert r["n_entries"] == 1, "the SELL must not also book an entry"
        assert r["n_merges"] == 0
        assert r["realized_sell_pnl"] == pytest.approx(12.0)

    def test_an_unparseable_fill_is_skipped_not_partially_booked(self):
        r = mp.replay([{"condition_id": "c", "outcome_index": 0,
                        "side": "BUY", "size": "x", "price": 0.4}])
        assert r["n_fills"] == 0 and r["n_entries"] == 0
