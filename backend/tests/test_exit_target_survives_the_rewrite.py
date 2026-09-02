"""The second trim must measure against what we BOUGHT, not what is left.

mirror_exit sizes each trim to a TARGET rather than to a fraction,
exactly so repeated trims cannot compound:

    _orig        = what WE bought
    _target_hold = _orig * (1 - closed_frac)
    qty          = ours - _target_hold

The base it used was `row["qty"]` -- live_orders.filled_shares -- which
mirror_exit itself REWRITES to the post-sale remainder. So the second
trim applied the whale's cumulative fraction to an already-shrunken
base and compounded, which is the precise defect the target form exists
to prevent.

test_exit_fraction_is_a_stock already asserted the right numbers and
passed anyway, because it handed the second trim a FRESH Row(qty=200)
instead of the 160 mirror_exit had just written. A test that rebuilds
the state under test cannot see a bug in how that state is kept.

So these tests do the one thing that one could not: they FEED THE
SECOND CALL THE ROW THE FIRST CALL WROTE. The fake pool applies the
real UPDATE to its own row, so if the production code stops persisting
the original -- or goes back to reading the remainder -- the numbers
move and these fail.
"""
import asyncio

import pytest

from sportsassets import live_executor as le


class _Row(dict):
    def keys(self):
        return list(super().keys())


class _Pool:
    """A live_orders row that REMEMBERS what mirror_exit wrote to it."""

    def __init__(self, orig_shares=200.0):
        self.filled_shares = orig_shares
        self.orig_shares = None          # as a fresh row is: NULL
        self.updates: list[tuple] = []

    def row(self):
        """Exactly the projection mirror_exit's SELECT produces."""
        return _Row(
            id=1,
            us_market_slug="aec-atp-a-b-2026-09-01",
            qty=float(self.filled_shares),
            orig_qty=float(self.orig_shares
                           if self.orig_shares is not None
                           else self.filled_shares),
            entry=0.50,
            intent="ORDER_INTENT_BUY_LONG",
        )

    def apply_partial(self, remaining: float, pre_sale_qty: float):
        """What the production UPDATE does: filled_shares <- remaining,
        orig_shares <- COALESCE(orig_shares, pre-sale qty)."""
        self.updates.append((remaining, pre_sale_qty))
        self.filled_shares = remaining
        if self.orig_shares is None:
            self.orig_shares = pre_sale_qty


def _sizing_block() -> str:
    """The production sizing arithmetic, LIFTED from the real source.

    Deliberately not re-implemented. An earlier version of this helper
    copied `_target_hold` and `qty` by hand and only extracted `_orig`,
    which is the same mistake the test being replaced made: a test that
    carries its own copy of the arithmetic cannot fail when production's
    copy changes. Here the whole block is exec'd, so any edit to it --
    including a regression to the rewritten base -- moves these numbers.
    """
    import inspect
    import textwrap

    src = inspect.getsource(le.mirror_exit)
    # Back up to the START OF THE LINE. Slicing mid-line leaves the first
    # line unindented while the rest keep their 8 spaces, so dedent finds
    # no common prefix and the exec raises IndentationError.
    start = src.rindex("\n", 0, src.index("_orig = int(")) + 1
    end = src.index("if qty <= 0:", start)
    end = src.rindex("\n", 0, end) + 1
    return textwrap.dedent(src[start:end])


def _sized(row, ours: float, closed_frac: float) -> int:
    ns = {"row": row, "ours": ours, "closed_frac": closed_frac,
          "FULL_EXIT_FRAC": le.FULL_EXIT_FRAC, "int": int,
          "_row_get": le._row_get}
    exec(_sizing_block(), {"__builtins__": {"int": int}}, ns)  # noqa: S102
    return int(ns["qty"])


# ------------------------------------------------- the compounding bug

def test_two_trims_leave_us_where_the_whale_is():
    """THE BUG. Whale goes 20% out, then 40% out, of a 1,000-share book.
    We bought 200. After both trims we should hold 200*0.60 = 120.

    The old base gave 96 -- we leave 20% faster than he does."""
    pool = _Pool(orig_shares=200.0)

    # --- trim 1: he is 20% out -------------------------------------
    r1 = pool.row()
    ours1 = 200.0
    sell1 = _sized(r1, ours1, 0.20)
    assert sell1 == 40
    pool.apply_partial(remaining=ours1 - sell1, pre_sale_qty=r1["qty"])

    # --- trim 2: he is now 40% out, CUMULATIVE ---------------------
    r2 = pool.row()                      # the row trim 1 actually wrote
    assert r2["qty"] == 160.0            # filled_shares was rewritten
    assert r2["orig_qty"] == 200.0       # the original was preserved
    ours2 = 160.0
    sell2 = _sized(r2, ours2, 0.40)

    assert ours2 - sell2 == 120.0, (
        f"held {ours2 - sell2} where the whale's 60% of our 200 is 120 "
        f"-- the target compounded against the rewritten base")


def test_the_original_is_captured_once_and_never_moves():
    """A third trim must still measure against 200, not 160 or 120."""
    pool = _Pool(orig_shares=200.0)
    ours = 200.0
    for frac in (0.20, 0.40, 0.50):
        r = pool.row()
        assert r["orig_qty"] == 200.0
        sell = _sized(r, ours, frac)
        pool.apply_partial(remaining=ours - sell, pre_sale_qty=r["qty"])
        ours -= sell
    assert ours == 100.0                 # 200 * (1 - 0.50)


def test_a_single_trim_is_unchanged():
    """Strict generalisation: with nothing yet sold, orig_qty IS qty, so
    the first trim must produce exactly the number it always did."""
    pool = _Pool(orig_shares=200.0)
    r = pool.row()
    assert r["orig_qty"] == r["qty"]
    assert _sized(r, 200.0, 0.35) == 70


def test_a_row_predating_the_column_reads_as_before():
    """orig_shares is NULL on every row written before migration 040.
    COALESCE must make those behave exactly as they do today rather than
    sizing off a null."""
    pool = _Pool(orig_shares=140.0)
    pool.orig_shares = None
    r = pool.row()
    assert r["orig_qty"] == 140.0
    assert _sized(r, 140.0, 0.25) == 35


# --------------------------------------------------- the wiring itself

def test_the_select_reads_orig_shares_not_just_filled_shares():
    import inspect

    # The lookup lives in _position_row (round three: one helper for
    # both the first lookup and the post-wait re-query, so the
    # migration-040 fallback cannot be present on one and absent on the
    # other), and mirror_exit must actually call it.
    src = inspect.getsource(le._position_row)
    assert "COALESCE(orig_shares, filled_shares)" in src, (
        "the position lookup must project the preserved original, or "
        "the target silently goes back to compounding")
    assert "await _position_row(pool, asset, username, _sel_tail)" in \
        inspect.getsource(le.mirror_exit)


def test_the_partial_update_persists_the_original():
    import inspect

    src = inspect.getsource(le.mirror_exit)
    assert "orig_shares=COALESCE(orig_shares, $4)" in src, (
        "the partial-exit UPDATE is the only place the original still "
        "exists; if it does not capture it there, it is gone")


def test_the_sizing_base_is_orig_qty():
    import inspect

    src = inspect.getsource(le.mirror_exit)
    assert '_orig = int(_row_get(row, "orig_qty")' in src, (
        "the target must be anchored on what we bought")


def test_the_migration_ships_with_the_code():
    """A column the code reads and the database lacks is an outage, not
    a bug -- so the migration is part of this change, not a follow-up."""
    import pathlib

    root = pathlib.Path(le.__file__).resolve().parents[1]
    sql = root / "migrations" / "040_live_orders_orig_shares.sql"
    assert sql.exists(), f"missing migration at {sql}"
    body = sql.read_text()
    assert "ADD COLUMN IF NOT EXISTS orig_shares" in body
    assert "IF NOT EXISTS" in body, "must be re-runnable"


@pytest.mark.parametrize("frac,expected_hold", [
    (0.0, 200.0), (0.10, 180.0), (0.25, 150.0), (0.50, 100.0),
    (0.75, 50.0),
])
def test_one_trim_holds_his_fraction_of_our_book(frac, expected_hold):
    pool = _Pool(orig_shares=200.0)
    r = pool.row()
    assert 200.0 - _sized(r, 200.0, frac) == expected_hold


@pytest.mark.parametrize("ours", [1.0, 7.0, 30.0, 199.0, 200.0])
@pytest.mark.parametrize("frac", [0.0, 0.1, 0.5, 0.9, 0.99, 1.0])
def test_we_never_sell_more_than_the_venue_holds(ours, frac):
    """The sale is bounded by what the venue actually holds, whatever
    the ledger thinks we bought. Swept rather than probed at one point:
    the `qty > ours` clamp is unreachable through the target arithmetic
    (_target_hold is never negative), so the only thing that can breach
    `ours` is the full-exit branch -- and this pins that it does not."""
    pool = _Pool(orig_shares=200.0)
    r = pool.row()
    assert _sized(r, ours, frac) <= int(ours)


def test_a_full_exit_sells_the_whole_venue_position():
    """When he is out, we are out to the share -- even where our ledger
    disagrees with the venue about how much that is."""
    pool = _Pool(orig_shares=200.0)
    r = pool.row()
    assert _sized(r, 30.0, 1.0) == 30
    assert _sized(r, 30.0, le.FULL_EXIT_FRAC) == 30
