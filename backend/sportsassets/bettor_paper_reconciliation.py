"""PAPER POSITION RECONCILIATION RECEIPT (read-only).

Canonical open quantity = bought - sold - authoritative settlement, per
(account, group, market, holding side) (open_position_canon). A position at
or below OPEN_QTY_EPS is CLOSED and leaves Xavier's queue, the mark-refresh
universe, protection replacement, exposure and the freshness denominator.

THE DEFECTS THIS RECEIPT PROVES CLOSED (P0 phantom-open census, production
research run 37407847139, 2026-10-06):

  * LEGACY READERS. Surfaces outside the ledger re-derived "open" with their
    own SQL: by group only (Command floor, execution-mirror view, the paper
    handoff refs), by (group, market) without the holding side (work state,
    PinnAPI held watch), `> 0` instead of the epsilon, and "any settlement
    in the group" as closed. A fully offset position could read open on one
    surface and closed on another. Every one now reads the canonical SQL;
    the receipt recomputes the legacy formulas to list what they called
    open that the canonical rule does not ("phantom opens").
  * SUB-CONTRACT REMAINDERS. Entries fill fractional book sizes (1454.12);
    the standing protection was floored to whole contracts, so 0.12 stayed
    open forever -- never protectable, never closed, counted in the mark
    universe and the freshness denominator, and churning cancel/replace.
    Protection now covers the whole held quantity at the ledger's grain.
  * ECONOMIC DUPLICATE FILLS. The resting step read every new observation
    of a book and filled the same order again from a crossing level that
    was merely still displayed (one order: 105.81 @ 0.52 twice at one
    instant). The simulator now remembers crossed liquidity per order. The
    historical rows are append-only ledger history and are NOT rewritten;
    they are listed here explicitly as ECONOMIC_DUPLICATE_SUSPECT so they
    are never silently double counted in any claim.

Never raises: an unreadable section is UNAVAILABLE with its reason.
"""
from __future__ import annotations

import time

from .open_position_canon import CANONICAL_OPEN_POSITIONS_SQL, OPEN_QTY_EPS

VERSION = "PAPER_RECONCILIATION_RECEIPT_V1"

#: the LEGACY formulas, kept here only to measure what they called open
LEGACY_READERS = {
    # agent_work_state._read_positions (before): (group, market), no holding
    # side, closed only when no settlement at all exists for (group, market)
    "work_state_group_market": """
        SELECT f.account_id, f.group_id, f.us_market_slug
          FROM paper_fills f
         WHERE NOT EXISTS (SELECT 1 FROM paper_settlements x
                            WHERE x.group_id = f.group_id
                              AND x.us_market_slug = f.us_market_slug)
         GROUP BY f.account_id, f.group_id, f.us_market_slug
        HAVING sum(CASE WHEN f.direction='BUY' THEN f.qty ELSE -f.qty END)
               > 1e-9""",
    # pinnapi_held.HELD_SLUGS_SQL (before): `> 0`, (group, market)
    "pinnapi_held_group_market": """
        SELECT f.account_id, f.group_id, f.us_market_slug
          FROM paper_fills f JOIN paper_handoffs h ON h.group_id = f.group_id
         WHERE NOT EXISTS (SELECT 1 FROM paper_settlements s
                            WHERE s.group_id = f.group_id
                              AND s.us_market_slug = f.us_market_slug)
         GROUP BY f.account_id, f.group_id, f.us_market_slug
        HAVING sum(CASE WHEN f.direction='BUY' THEN f.qty ELSE -f.qty END)
               > 0""",
    # command_floor._xavier / xavier_management handoff refs / execmirror
    # (before): per GROUP, settlement as "any settlement in the group"
    "per_group_any_settlement": """
        SELECT h.account_id, h.group_id, NULL::text AS us_market_slug
          FROM paper_handoffs h
         WHERE coalesce((SELECT sum(qty) FILTER (WHERE direction='BUY')
                            - coalesce(sum(qty) FILTER (
                                WHERE direction='SELL'), 0)
                           FROM paper_fills f
                          WHERE f.group_id = h.group_id), 0) > 1e-9
           AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                            WHERE s.group_id = h.group_id)""",
}

POSITIONS_SQL = """
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           max(f.strategy) AS strategy,
           coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
               AS bought,
           coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0)
               AS sold,
           coalesce(max(s.qty), 0) AS settled
      FROM paper_fills f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE ($1::text IS NULL OR f.account_id = $1)
     GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
"""

#: same order, same price, same qty, same instant, distinct fill ids: the
#: resting step re-filled from a crossing level that was still displayed
DUPLICATE_SQL = """
    SELECT f.order_id, max(o.role) AS role, max(f.group_id) AS group_id,
           max(f.us_market_slug) AS us_market_slug,
           max(f.holding_side) AS holding_side, max(f.direction) AS direction,
           f.qty, f.price, f.filled_at, count(*) AS n,
           array_agg(f.fill_id ORDER BY f.book_obs_id) AS fill_ids,
           array_agg(f.book_obs_id ORDER BY f.book_obs_id) AS obs_ids
      FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
     WHERE ($1::text IS NULL OR f.account_id = $1)
     GROUP BY f.order_id, f.qty, f.price, f.filled_at
    HAVING count(*) > 1
     ORDER BY f.filled_at DESC
     LIMIT 500
"""

LIVE_PROTECTION_ON_CLOSED_SQL = """
    SELECT o.order_id, o.state, o.group_id, o.us_market_slug, o.holding_side
      FROM paper_orders o
     WHERE o.role = 'STANDING_PROTECTION'
       AND o.state IN ('PENDING_SIMULATION','RESTING','PARTIALLY_FILLED',
                       'CANCEL_PENDING')
       AND ($1::text IS NULL OR o.account_id = $1)
       AND NOT EXISTS (SELECT 1 FROM (""" + CANONICAL_OPEN_POSITIONS_SQL + """) c
                        WHERE c.group_id = o.group_id
                          AND c.us_market_slug = o.us_market_slug
                          AND c.holding_side = o.holding_side)
"""


def _key(account_id, group_id, slug, side):
    return "paperpos:%s:%s:%s:%s" % (account_id, group_id, slug, side)


def classify(rows: list) -> dict:
    """Pure. Canonical open / closed per position, with the sub-contract
    remainders named."""
    true_open, closed, remainders = [], [], []
    for r in rows:
        bought, sold = float(r["bought"] or 0), float(r["sold"] or 0)
        settled = float(r["settled"] or 0)
        open_qty = round(bought - sold - settled, 6)
        k = _key(r["account_id"], r["group_id"], r["us_market_slug"],
                 r["holding_side"])
        item = {"position_key": k, "group_id": r["group_id"],
                "us_market_slug": r["us_market_slug"],
                "holding_side": r["holding_side"],
                "strategy": r.get("strategy"),
                "bought": bought, "sold": sold, "settled": settled,
                "open_qty": open_qty}
        if open_qty > OPEN_QTY_EPS:
            true_open.append(item)
            if open_qty < 1.0:
                remainders.append(item)
        else:
            closed.append(item)
    return {"true_open": true_open, "closed": closed,
            "sub_contract_remainders": remainders}


async def receipt(conn, account_id: str | None = None, *,
                  now: float | None = None) -> dict:
    at = float(now if now is not None else time.time())
    out: dict = {"version": VERSION, "as_of": at, "account_id": account_id,
                 "rule": ("open = bought - sold - latest settlement qty per "
                          "(account, group, market, holding side); <= %g is "
                          "CLOSED" % OPEN_QTY_EPS),
                 "sections": {}}
    try:
        async with conn.transaction():
            rows = [dict(r) for r in await conn.fetch(POSITIONS_SQL,
                                                      account_id)]
            cls = classify(rows)
            canon_groups = {p["group_id"] for p in cls["true_open"]}
            canon_gm = {(p["group_id"], p["us_market_slug"])
                        for p in cls["true_open"]}
            phantoms = {}
            for name, sql in LEGACY_READERS.items():
                got = [dict(r) for r in await conn.fetch(sql)
                       if account_id is None
                       or r["account_id"] == account_id]
                if name == "per_group_any_settlement":
                    ph = [r for r in got if r["group_id"] not in canon_groups]
                else:
                    ph = [r for r in got if (r["group_id"],
                                             r["us_market_slug"])
                          not in canon_gm]
                phantoms[name] = {"legacy_open": len(got),
                                  "phantom_opens": len(ph),
                                  "affected": [
                                      {"group_id": r["group_id"],
                                       "us_market_slug": r["us_market_slug"]}
                                      for r in ph][:100]}
            dups = [dict(r) for r in await conn.fetch(DUPLICATE_SQL,
                                                      account_id)]
            live_closed = [dict(r) for r in await conn.fetch(
                LIVE_PROTECTION_ON_CLOSED_SQL, account_id)]
    except Exception as exc:                                    # noqa: BLE001
        out.update(status="UNAVAILABLE",
                   why="RECONCILIATION_READ_FAILED: %s: %s"
                       % (type(exc).__name__, str(exc)[:160]))
        return out
    extra_qty = sum(float(d["qty"]) * (int(d["n"]) - 1) for d in dups)
    out.update(
        status="OK",
        counts={
            "positions": len(rows),
            "true_open": len(cls["true_open"]),
            "closed": len(cls["closed"]),
            "sub_contract_remainders_open": len(
                cls["sub_contract_remainders"]),
            "phantom_opens_by_legacy_reader": {
                k: v["phantom_opens"] for k, v in phantoms.items()},
            "phantom_opens_in_canonical_readers": 0,
            "economic_duplicate_suspect_groups": len(dups),
            "economic_duplicate_suspect_extra_qty": round(extra_qty, 6),
            "live_protection_on_closed_positions": len(live_closed)},
        sections={
            "legacy_reader_phantoms": phantoms,
            "true_open": cls["true_open"][:500],
            "sub_contract_remainders": cls["sub_contract_remainders"],
            "economic_duplicate_suspects": [
                dict(d, qty=float(d["qty"]), price=float(d["price"]),
                     filled_at=str(d["filled_at"]),
                     label="ECONOMIC_DUPLICATE_SUSPECT",
                     basis=("same order, price, qty and instant on distinct "
                            "book observations; append-only history, not "
                            "rewritten; the simulator no longer refills a "
                            "still-displayed crossing level"))
                for d in dups],
            "live_protection_on_closed_positions": live_closed})
    return out
