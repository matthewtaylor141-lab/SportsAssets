"""The owner asked for a RATE. Every existing counter is a stock.

"How many different copy orders should we be getting on a daily basis
from RN1? How many cash outs should we have based on how many trades we
are copying?" (2026-08-31)

Nothing in the system answered that. What existed:

  * TRUEEDGE detected=112,555 — all-time, not per day
  * WINWHALE missed 7d=15,073 — live_orders ROWS, and the sweep retries
    the same trade every 2 minutes for days, so this counts retries, not
    opportunities
  * SWEEPMIX pool today_tomorrow=645 — an instant snapshot of a
    standing backlog

Dividing any of those by a window to get a daily rate is the
stock-vs-flow error, which has already produced two wrong readings in
one day (the undiagnosed residual that turned out to be frozen history,
and the BUY_SHORT ban that turned out dormant for six days).

So these tests pin the properties that keep this endpoint a FLOW:

  * new_assets counts a token on the day of its FIRST-EVER buy, so a
    whale adding to a position for a week is one opportunity, not seven
  * today is excluded from the medians, because a partial day drags
    every average toward zero
  * medians, not means — the flow is bursty and a mean describes no
    real day
"""
import ast
import asyncio
import datetime as dt
import inspect

from sportsassets.api import app as app_mod


def _node():
    tree = ast.parse(inspect.getsource(app_mod))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and n.name == "api_whale_rate":
            return n
    raise AssertionError("api_whale_rate not found")


def _code_only():
    n = _node()
    body = n.body[1:] if (n.body and isinstance(n.body[0], ast.Expr)
                          and isinstance(n.body[0].value, ast.Constant)) \
        else n.body
    return "\n".join(ast.unparse(x) for x in body)


class _Pool:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    async def fetch(self, sql, *a):
        self.queries.append((sql, a))
        return self.rows

    async def execute(self, sql, *a):
        self.queries.append((sql, a))


def _row(day, trades, distinct, new, playable, notional=0.0):
    return {"day": day, "trades": trades, "distinct_assets": distinct,
            "new_assets": new, "dated_playable": playable,
            "notional": notional}


def _call(rows, whale="rn1", days=14):
    pool = _Pool(rows)

    async def _get_pool():
        return pool

    orig = app_mod.get_pool
    app_mod.get_pool = _get_pool
    try:
        return asyncio.run(app_mod.api_whale_rate(whale=whale, days=days)), pool
    finally:
        app_mod.get_pool = orig


D = dt.date(2026, 8, 31)


class TestItMeasuresAFlowNotAStock:
    def test_new_assets_keys_off_the_first_ever_buy(self):
        """A whale adding to one position across seven days is ONE copy
        opportunity. Keying off the day's own buys would call it seven."""
        src = _code_only()
        assert "firsts" in src and "min(t.ts)" in src

    def test_it_is_bounded_by_a_day_not_just_a_start(self):
        """`first_ts >= day` alone marks a token new on every later day
        too. The upper bound is what makes it a single day's arrivals."""
        src = _code_only()
        assert "interval '1 day'" in src

    def test_today_is_excluded_from_the_medians(self):
        """A partial day is not a day. Including it drags every median
        toward zero and understates the rate the owner asked for."""
        out, _ = _call([_row(D, 2, 2, 2, 2), _row(D - dt.timedelta(1), 40, 20, 18, 16),
                        _row(D - dt.timedelta(2), 44, 22, 20, 18)])
        assert out["summary"]["days_counted"] == 2
        assert out["summary"]["median_new_assets"] == 19

    def test_a_single_day_is_still_reported_rather_than_dropped(self):
        out, _ = _call([_row(D, 10, 5, 5, 4)])
        assert out["summary"]["days_counted"] == 1


class TestTheMedianIsAMedian:
    def test_a_burst_day_does_not_move_the_typical_day(self):
        """Slate nights are many times a Tuesday. A mean would describe
        no actual day."""
        rows = [_row(D, 0, 0, 0, 0)]
        rows += [_row(D - dt.timedelta(i), 10, 5, 5, 4) for i in range(1, 6)]
        rows.append(_row(D - dt.timedelta(6), 10000, 900, 900, 900))
        out, _ = _call(rows)
        assert out["summary"]["median_new_assets"] == 5, \
            "a single burst day moved the central estimate"

    def test_an_even_count_averages_the_middle_pair(self):
        rows = [_row(D, 0, 0, 0, 0),
                _row(D - dt.timedelta(1), 1, 1, 10, 1),
                _row(D - dt.timedelta(2), 1, 1, 20, 1)]
        out, _ = _call(rows)
        assert out["summary"]["median_new_assets"] == 15.0


class TestTheExitSideIsStatedAsAnIdentity:
    def test_the_reading_ties_cash_outs_to_entries(self):
        """At this venue a whale exits by BUYING the complement, so in
        steady state every position closes exactly once: cash-outs per
        day EQUAL entries per day, lagged. Inventing a separate exit
        count would be a second estimate of a number already determined."""
        out, _ = _call([_row(D, 1, 1, 1, 1)])
        r = out["reading"].lower()
        assert "steady state" in r and "closes exactly once" in r

    def test_the_trim_multiplier_is_reported(self):
        """The one honest source of error on the exit side: a partial
        trim is an extra cash-out event."""
        out, _ = _call([_row(D, 0, 0, 0, 0),
                        _row(D - dt.timedelta(1), 40, 10, 10, 8),
                        _row(D - dt.timedelta(2), 40, 10, 10, 8)])
        assert out["summary"]["adds_per_asset"] == 4.0

    def test_no_division_by_zero_on_a_silent_whale(self):
        out, _ = _call([_row(D, 0, 0, 0, 0), _row(D - dt.timedelta(1), 0, 0, 0, 0)])
        assert "adds_per_asset" not in out["summary"]


class TestScoping:
    def test_it_filters_to_the_named_whale(self):
        _, pool = _call([_row(D, 1, 1, 1, 1)], whale="RN1")
        sql, params = pool.queries[0]
        assert "lower(wh.username) = $1" in sql
        assert params[0] == "rn1", "the whale name is not normalised"

    def test_only_buys_are_counted(self):
        """A SELL is not a new position, and at this venue an exit
        arrives as a BUY of the complement anyway."""
        _, pool = _call([_row(D, 1, 1, 1, 1)])
        assert "t.side = 'BUY'" in pool.queries[0][0]

    def test_the_window_is_bounded(self):
        out, _ = _call([_row(D, 1, 1, 1, 1)], days=9999)
        assert out["days"] <= 60

    def test_it_only_reads(self):
        _, pool = _call([_row(D, 1, 1, 1, 1)])
        for sql, _ in pool.queries:
            up = " ".join(sql.upper().split())
            for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ",
                         "ALTER ", "TRUNCATE ", "CREATE "):
                assert verb not in up
