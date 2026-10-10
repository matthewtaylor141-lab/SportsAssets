"""THE CANONICAL PAPER OPEN-POSITION RULE, NEUTRAL (stdlib only).

Canonical open quantity = bought - sold - the latest authoritative settlement
version's qty, per (account, group, market, holding side). A position whose
open quantity is <= OPEN_QTY_EPS (the ledger's existing 1e-9) is CLOSED:
out of Xavier's queue, the mark-refresh universe, protection replacement,
exposure and the freshness denominator.

This module imports nothing from the paper stack so every reader -- the
ledger itself (bettor_paper_ledger re-exports these names), the Command
floor, the work-state reader, the execution mirror view and the PinnAPI
held-market watch -- uses ONE rule instead of re-deriving "open" with its
own grouping, threshold or settlement test. Before this (P0 phantom-open
census, research run 37407847139) readers grouped by group only or by
(group, market) without the holding side, tested `> 0` instead of the
epsilon, and treated "any settlement in the group" as closed.
"""
from __future__ import annotations

#: THE ONE OPEN-QUANTITY EPSILON (the ledger's existing 1e-9): a position
#: whose canonical open quantity (bought - sold - authoritative settlement)
#: is <= this is CLOSED -- out of Xavier's queue, the mark-refresh universe,
#: protection replacement, exposure and the freshness denominator. Every
#: reader uses `is_open` or CANONICAL_OPEN_POSITIONS_SQL; none re-derives
#: "open" with its own grouping, threshold or settlement rule.
OPEN_QTY_EPS = 1e-9


def is_open(open_qty) -> bool:
    """Pure: the canonical open test."""
    try:
        return float(open_qty) > OPEN_QTY_EPS
    except (TypeError, ValueError):
        return False


#: THE ONE TEMPLATE the canonical open-position rule is written in. It is
#: expanded WITHOUT any scope into CANONICAL_OPEN_POSITIONS_SQL below (every
#: reader's statement, byte for byte what it was before the template existed:
#: tests/test_rc63c_allie_exposure_reads_one_account_in_one_pass.py pins the
#: text), and WITH the account scope and the cost columns into the open
#: EXPOSURE statement further down -- so the rule (BUY - SELL - the latest
#: settlement version's qty, per account / group / market / side, kept above
#: OPEN_QTY_EPS) is written ONCE and the exposure reads the same rule, not a
#: copy of it.
_OPEN_POSITIONS_TEMPLATE = """
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty%(select_extra)s
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold%(fills_extra)s
              FROM paper_fills%(fills_where)s
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements%(settlements_where)s
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
"""

#: the exposure variant's extra columns, taken from the SAME single pass over
#: paper_fills (the fixture, and what the BUYs cost including fees)
_EXPOSURE_SELECT_EXTRA = """,
           f.bought, f.fixture, f.buy_cost"""
_EXPOSURE_FILLS_EXTRA = """,
                   max(fixture) AS fixture,
                   sum(gross_usd + fee_usd) FILTER (WHERE direction = 'BUY')
                       AS buy_cost"""
#: the account scope, INSIDE each scan: paper_fills by its account column;
#: paper_settlements by the position key's own prefix (the rule joins on the
#: key string alone, so the key's text -- not the settlement's account
#: column -- is what can narrow it without changing what joins). $1 NULL
#: means every account.
_ACCOUNT_FILLS_WHERE = """
             WHERE ($1::text IS NULL OR account_id = $1::text)"""
_ACCOUNT_SETTLEMENTS_WHERE = """
                  WHERE ($1::text IS NULL OR starts_with(
                            position_key, 'paperpos:' || $1::text || ':'))"""

#: every text the template's expansions can add, so a reader (or a test) can
#: strip a scoped / costed expansion back to the bare canonical statement
OPEN_POSITIONS_EXPANSIONS = (_EXPOSURE_SELECT_EXTRA, _EXPOSURE_FILLS_EXTRA,
                             _ACCOUNT_FILLS_WHERE, _ACCOUNT_SETTLEMENTS_WHERE)


def open_positions_sql(*, account_scoped: bool = False,
                       with_cost: bool = False) -> str:
    """The canonical open-position rule as a statement. Bare (the default)
    it IS CANONICAL_OPEN_POSITIONS_SQL, byte for byte. `account_scoped`
    puts $1 (an account id, or NULL for every account) inside BOTH scans;
    `with_cost` adds the fixture and the BUY cost (incl. fees) of each
    position out of the same pass over paper_fills. The arithmetic, the
    grouping, the settlement rule and the epsilon are the template's and
    cannot differ between expansions."""
    return _OPEN_POSITIONS_TEMPLATE % {
        "select_extra": _EXPOSURE_SELECT_EXTRA if with_cost else "",
        "fills_extra": _EXPOSURE_FILLS_EXTRA if with_cost else "",
        "fills_where": _ACCOUNT_FILLS_WHERE if account_scoped else "",
        "settlements_where": (_ACCOUNT_SETTLEMENTS_WHERE if account_scoped
                              else "")}


#: EVERY OPEN PAPER POSITION, CANONICALLY: one row per (account, group,
#: market, holding side) with open = bought - sold - the latest settlement
#: version's qty, kept only when open > OPEN_QTY_EPS. No parameters; a
#: reader narrows it (account, group, slug) in its own outer query. This is
#: POSITIONS_SQL's arithmetic for all accounts at once.
CANONICAL_OPEN_POSITIONS_SQL = open_positions_sql()


# ═════════════════════════════════════════════════════════════════════
# OPEN EXPOSURE, FROM THE CANONICAL OPEN QUANTITY (RC6.3 allie-exposure)
# ═════════════════════════════════════════════════════════════════════
#
# THE DEFECT THIS REPLACED. Allie's book and fixture exposure inputs
# (canonical_components.allie_at_decision, restated by
# correlation_graph.ALLIE_FIXTURE_SQL) counted a filled ENTRY order as OPEN
# exposure for as long as its group had NO paper_settlements row. But a
# position exited to zero never gets that row: bettor_paper_ledger.settle
# refuses NO_OPEN_POSITION_TO_SETTLE for a position whose open quantity is
# already 0. Production readback (research-sql 38009262456): all 517
# positions closed, 0 open, while those reads counted 322 groups worth
# $148,234 as open. Once Allie is MEASURED her book / fixture inputs would
# have been wrong by that amount.
#
# THE RULE NOW: a position is open exactly when CANONICAL_OPEN_POSITIONS_SQL
# says so (bought - sold - the latest settlement version's qty > OPEN_QTY_EPS,
# per account, group, market and holding side), and its exposure is the
# REMAINDER at the ledger's own cost basis -- open quantity x the average
# cost per contract of its BUYs including fees
# (bettor_paper_ledger._position_from: avg_cost_per_contract_incl_fees x
# open_qty, the figure _exposure applies to R_PER_FIXTURE). An exited or
# settled position has open_qty <= 0, is not in the canonical rows and
# contributes nothing; a partially exited one contributes only what is left.
# It is open exposure at FULL cost, as allie_capital's caps are stated
# (FIXTURE_CAP_USD / BOOK_CAP_USD "summed at full cost"); working orders that
# have not filled are not exposure here, as before.
#
# $1 is the account (NULL: every account -- a caller that names none); $2 is
# the fixture. Neither statement writes.

# ── RC6.3c allie-exposure SCALE ─────────────────────────────────────────
#
# THE COST. allie_at_decision ran the fixture statement and the book
# statement on EVERY decision, uncached, under Allie's 2.0 s component
# timeout. Each aggregated the WHOLE of paper_fills twice (the canonical
# open-quantity subquery, then the average-cost subquery joined back to it)
# and the WHOLE of paper_settlements (DISTINCT ON over every account's
# settlements), and only then kept the account asked for in the outermost
# WHERE: 0.45 s a call at 120,000 fills (reviewer's measurement), growing
# with every account's history rather than the deciding account's, and paid
# twice per decision.
#
# THE SHAPE NOW. (1) The account sits INSIDE the scans: paper_fills by
# account_id, paper_settlements by the position-key PREFIX
# 'paperpos:<account>:' -- the key's own text, not the settlement's
# account_id column, because the rule joins on the key string alone
# (account 'x' with group 'y:g' and account 'x:y' with group 'g' build the
# same key, and one settlement row closes both); a prefix scope can only
# keep a superset of the rows an account's positions join to, so what joins
# is exactly what joined. (2) paper_fills is aggregated ONCE: bought, sold,
# the fixture and the BUY cost come out of the one GROUP BY, where the
# previous statement aggregated it a second time to get the cost and joined
# the two. (3) allie_at_decision asks for the book and the fixture in ONE
# statement (OPEN_EXPOSURE_BOOK_AND_FIXTURE_SQL): one read of the ledger a
# decision, not two. Results are identical: tests/test_rc63c_allie_exposure_
# reads_one_account_in_one_pass.py runs the previous statements (verbatim)
# against these on a seeded ledger across accounts -- open, exited, partly
# exited, settled, re-settled, short-side, colliding keys -- and on a
# random one, exact numerics, every account and none.
#
# `$1` NULL still means every account (a caller that names none).

#: ONE ROW PER CANONICALLY OPEN POSITION with its remaining cost at the
#: ledger's average cost; narrowed to the account in $1 INSIDE the scans
#: (open_positions_sql(account_scoped=True, with_cost=True)).
OPEN_EXPOSURE_ROWS_SQL = """
    SELECT c.account_id, c.group_id, c.us_market_slug, c.holding_side,
           c.fixture,
           c.open_qty * (c.buy_cost / nullif(c.bought, 0)) AS exposure_usd
      FROM (""" + open_positions_sql(account_scoped=True,
                                    with_cost=True) + """) c
"""

#: ALLIE'S FIXTURE INPUT: the groups still holding inventory on one fixture
#: and what that inventory cost. $1 account (or NULL), $2 fixture.
OPEN_EXPOSURE_FIXTURE_SQL = (
    "SELECT count(DISTINCT e.group_id) AS n, "
    "coalesce(sum(e.exposure_usd), 0) AS usd FROM ("
    + OPEN_EXPOSURE_ROWS_SQL + ") e WHERE e.fixture = $2")

#: ALLIE'S BOOK INPUT: the cost of all inventory still held. $1 account (or
#: NULL).
OPEN_EXPOSURE_BOOK_SQL = (
    "SELECT coalesce(sum(e.exposure_usd), 0) AS usd FROM ("
    + OPEN_EXPOSURE_ROWS_SQL + ") e")

#: BOTH INPUTS FROM ONE READ OF THE LEDGER (what allie_at_decision executes
#: when the decision names a fixture): the book's cost, and the fixture's
#: open groups and cost -- the same figures, to the numeric, as the two
#: statements above run separately. $1 account (or NULL), $2 fixture.
OPEN_EXPOSURE_BOOK_AND_FIXTURE_SQL = (
    "SELECT coalesce(sum(e.exposure_usd), 0) AS book_usd, "
    "count(DISTINCT e.group_id) FILTER (WHERE e.fixture = $2) AS fixture_n, "
    "coalesce(sum(e.exposure_usd) FILTER (WHERE e.fixture = $2), 0) "
    "AS fixture_usd FROM (" + OPEN_EXPOSURE_ROWS_SQL + ") e")

#: what an exposure input says about itself, recorded beside it
OPEN_EXPOSURE_BASIS = (
    "CANONICAL_OPEN_QUANTITY: open = bought - sold - latest settlement qty "
    "> OPEN_QTY_EPS per (account, group, market, side); exposure = open "
    "quantity x average buy cost per contract including fees (the paper "
    "ledger's cost basis); a position exited to zero or settled is not open")
