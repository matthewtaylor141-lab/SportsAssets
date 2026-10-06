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
