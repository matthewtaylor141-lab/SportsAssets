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

from sportsassets.analytics.merge_pnl import DUST, replay


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
        # the cast must be gone from the SQL itself, comments aside
        sql = src[src.index('"""\n            SELECT'):src.index('", w.lower()')]
        assert "::date" not in sql

    def test_the_cashflow_query_binds_a_date_object(self):
        import inspect

        from sportsassets.api import app as app_mod

        src = inspect.getsource(app_mod.api_whale_merge_pnl)
        assert ".date())" in src
        sql = src[src.index("SELECT COALESCE(sum("):src.index('", name.lower()')]
        assert "::date" not in sql

    def test_a_date_object_passes_straight_through(self):
        import asyncio
        import datetime as dt

        from sportsassets.analytics.merge_pnl import whale_merge_pnl

        seen = {}

        class _P:
            async def fetch(self, _sql, *a):
                seen["bound"] = a[1]
                return []

        asyncio.run(whale_merge_pnl(_P(), ["w"], dt.date(2026, 8, 1)))
        assert seen["bound"] == dt.date(2026, 8, 1)

    def test_a_string_is_converted_not_rejected(self):
        import asyncio
        import datetime as dt

        from sportsassets.analytics.merge_pnl import whale_merge_pnl

        seen = {}

        class _P:
            async def fetch(self, _sql, *a):
                seen["bound"] = a[1]
                return []

        asyncio.run(whale_merge_pnl(_P(), ["w"], "2026-08-01"))
        assert seen["bound"] == dt.date(2026, 8, 1)


class TestTruncationIsReportedNotSilent:
    def test_a_capped_replay_says_so(self):
        import asyncio

        from sportsassets.analytics.merge_pnl import whale_merge_pnl

        class _P:
            async def fetch(self, _sql, *_a):
                return [{"condition_id": "c", "outcome_index": 0,
                         "side": "BUY", "size": 1, "price": 0.5}] * 3

        out = asyncio.run(whale_merge_pnl(_P(), ["w"], "2026-08-01",
                                          max_fills=3))
        assert out["w"]["truncated"] is True
        assert "floors, not totals" in out["w"]["verdict_note"]

    def test_an_uncapped_replay_does_not_claim_truncation(self):
        import asyncio

        from sportsassets.analytics.merge_pnl import whale_merge_pnl

        class _P:
            async def fetch(self, _sql, *_a):
                return [{"condition_id": "c", "outcome_index": 0,
                         "side": "BUY", "size": 1, "price": 0.5}]

        out = asyncio.run(whale_merge_pnl(_P(), ["w"], "2026-08-01",
                                          max_fills=100))
        assert out["w"]["truncated"] is False
        assert "verdict_note" not in out["w"]
