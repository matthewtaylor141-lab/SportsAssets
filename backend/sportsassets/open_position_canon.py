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


#: EVERY OPEN PAPER POSITION, CANONICALLY: one row per (account, group,
#: market, holding side) with open = bought - sold - the latest settlement
#: version's qty, kept only when open > OPEN_QTY_EPS. No parameters; a
#: reader narrows it (account, group, slug) in its own outer query. This is
#: POSITIONS_SQL's arithmetic for all accounts at once.
CANONICAL_OPEN_POSITIONS_SQL = """
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
"""


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

#: ONE ROW PER CANONICALLY OPEN POSITION with its remaining cost at the
#: ledger's average cost; narrowed to the account in $1.
OPEN_EXPOSURE_ROWS_SQL = """
    SELECT c.account_id, c.group_id, c.us_market_slug, c.holding_side,
           b.fixture,
           c.open_qty * (b.buy_cost / nullif(b.bought, 0)) AS exposure_usd
      FROM (""" + CANONICAL_OPEN_POSITIONS_SQL + """) c
      JOIN (SELECT account_id, group_id, us_market_slug, holding_side,
                   max(fixture) AS fixture,
                   sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
                   sum(gross_usd + fee_usd) FILTER (WHERE direction = 'BUY')
                       AS buy_cost
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) b
        ON b.account_id = c.account_id AND b.group_id = c.group_id
       AND b.us_market_slug = c.us_market_slug
       AND b.holding_side = c.holding_side
     WHERE ($1::text IS NULL OR c.account_id = $1::text)
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

#: what an exposure input says about itself, recorded beside it
OPEN_EXPOSURE_BASIS = (
    "CANONICAL_OPEN_QUANTITY: open = bought - sold - latest settlement qty "
    "> OPEN_QTY_EPS per (account, group, market, side); exposure = open "
    "quantity x average buy cost per contract including fees (the paper "
    "ledger's cost basis); a position exited to zero or settled is not open")
