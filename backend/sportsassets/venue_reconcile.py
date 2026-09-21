"""Reconcile the internal ledgers against the venue. READ ONLY.

WHAT THIS ANSWERS, AND WHY THE QUESTION IS OPEN.

On 2026-09-21 the internal ledger reported 52 rows in status='filled'
carrying $16,180.53 of cost. That figure was then quoted as current
exposure. It is not: it is a sum of ACQUISITION COSTS over fill records,
and three separate things have to be true before it means anything about
money at risk now --

  * each fill actually happened at the venue, in that quantity, at that
    price, with those fees;
  * the position was not subsequently sold or resolved;
  * nothing exists at the venue that the ledger never recorded.

None of the three is established by reading our own tables more
carefully. Two internal facts already say the figure is wrong: 37 of the
52 sit on markets our own `markets` table marks closed, and
`mirror_books` -- the reconciler's own view -- reports nothing open at
all. A market marked closed is not proof that the position settled or
that cash arrived, which is exactly why this reads the venue.

WHAT IT DOES NOT DO. It does not write. It does not adjust a ledger to
make two numbers agree. Where the venue and our tables disagree the
difference is reported as a difference, and any repair is proposed
separately with the evidence attached. A reconciliation that edits its
own inputs is not a reconciliation.

CREDENTIALS. The venue client is built by pmus._get_client() from the
process environment. Nothing here reads, prints, logs or returns a
credential, and the JSON it emits carries none.

VALUATION. Stated at the point of use and never mixed:

  ACQUISITION COST   what was paid, from the venue's own fill records.
                     A historical fact. Does not change.
  COST BASIS         the venue's all-in basis for a position still held
                     (position_basis: cost, baseCost, fees).
  MARK TO BID        remaining holdings valued at the live best bid at a
                     stated instant. This is what could be realised now,
                     not what was paid, and it moves.
  SETTLED PROCEEDS   cash from POSITION_RESOLUTION activities. Realised.

Run through the venue-reconcile workflow; see that file for the
reservation and concurrency rules.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

# The read surface. Nothing order-capable is imported, and
# test_venue_reconcile_is_read_only.py proves it by AST over this file.
from . import pmus
from .api.pmus_account import _amt, _any_ts

MAX_PAGES = 80          # 100 rows a page; the book starts 2026-08-04
PAGE = 100


# ── venue reads ──────────────────────────────────────────────────────

def venue_positions() -> dict:
    """Every position the account holds, by slug. Venue truth."""
    client = pmus._get_client()
    out: dict[str, dict] = {}
    cursor = ""
    for _ in range(MAX_PAGES):
        resp = client.portfolio.positions(
            {"limit": PAGE, **({"cursor": cursor} if cursor else {})}) or {}
        for slug, p in (resp.get("positions") or {}).items():
            out[str(slug)] = p or {}
        cursor = resp.get("nextCursor") or ""
        if resp.get("eof") or not cursor:
            break
    return out


def venue_open_orders() -> list[dict]:
    """Resting orders: commitments that are not yet fills."""
    return pmus.open_orders()


def venue_activities(types: list[str], since_ts: float) -> list[dict]:
    """Account-wide activity, NOT scoped to one market.

    pmus.recent_trades pages by marketSlug, which is right for its job
    and wrong for this one: a position the ledger never recorded has no
    slug for us to ask about. The whole point is to find rows we do not
    know to look for, so this reads the account.
    """
    client = pmus._get_client()
    out: list[dict] = []
    cursor = ""
    for _ in range(MAX_PAGES):
        resp = client.portfolio.activities(
            {"limit": PAGE, "sortOrder": "SORT_ORDER_DESCENDING",
             "types": types,
             **({"cursor": cursor} if cursor else {})}) or {}
        acts = resp.get("activities") or []
        if not acts:
            break
        oldest = None
        for act in acts:
            ts = float(_any_ts(act) or 0.0)
            if ts:
                oldest = ts if oldest is None else min(oldest, ts)
            out.append(act)
        cursor = resp.get("nextCursor") or ""
        if resp.get("eof") or not cursor:
            break
        # stop once the page is entirely older than the window
        if oldest and oldest < since_ts:
            break
    return out


def venue_bid(slug: str) -> float | None:
    try:
        return pmus.slug_bid(slug)
    except Exception:                                      # noqa: BLE001
        return None


# ── internal reads ───────────────────────────────────────────────────

LIVE_ORDERS_SQL = """
    SELECT id, trade_id, whale_username, asset, condition_id, side,
           us_market_slug, order_id, status, limit_price,
           requested_usd, requested_shares, filled_shares, fill_price,
           filled_usd, placed_at, settled_at, pnl, payout, error
      FROM live_orders
     WHERE status IN ('filled', 'settled', 'cashed_out')
     ORDER BY placed_at
"""

MIRROR_BOOKS_SQL = """
    SELECT id, us_market_slug, state, opened_at, closed_at
      FROM mirror_books
     ORDER BY opened_at
"""


async def internal_rows(pool):
    lo = [dict(r) for r in await pool.fetch(LIVE_ORDERS_SQL)]
    try:
        mb = [dict(r) for r in await pool.fetch(MIRROR_BOOKS_SQL)]
    except Exception as exc:                               # noqa: BLE001
        mb = []
        print("mirror_books unreadable: %s" % str(exc)[:160], file=sys.stderr)
    return lo, mb


# ── matching ─────────────────────────────────────────────────────────

def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def index_venue_trades(acts: list[dict]) -> dict[str, dict]:
    """Venue trades by the ORDER id that produced them.

    live_orders.order_id is what we recorded when we submitted, so it is
    the only key that can join our row to the venue's. Trades are summed
    per order because one order can fill in several executions and the
    ledger records one row for the order.
    """
    by_order: dict[str, dict] = {}
    for act in acts:
        if act.get("type") != "ACTIVITY_TYPE_TRADE":
            continue
        t = act.get("trade") or {}
        own = pmus.trade_own_order(t) if hasattr(pmus, "trade_own_order") else {}
        oid = str(own.get("id") or "") or str((t.get("order") or {}).get("id") or "")
        if not oid:
            continue
        shares = _amt(t.get("quantity") or t.get("size") or t.get("shares"))
        px = _amt(t.get("price"))
        rec = by_order.setdefault(oid, {
            "order_id": oid, "executions": 0, "shares": 0.0,
            "notional": 0.0, "first_ts": None, "last_ts": None,
            "slug": str(t.get("marketSlug") or ""), "trade_ids": [],
        })
        rec["executions"] += 1
        rec["shares"] += shares
        rec["notional"] += shares * px
        ts = float(_any_ts(act) or 0.0)
        if ts:
            rec["first_ts"] = ts if rec["first_ts"] is None else min(rec["first_ts"], ts)
            rec["last_ts"] = ts if rec["last_ts"] is None else max(rec["last_ts"], ts)
        if t.get("id"):
            rec["trade_ids"].append(str(t["id"]))
    for rec in by_order.values():
        rec["avg_price"] = (rec["notional"] / rec["shares"]
                            if rec["shares"] else None)
    return by_order


def index_resolutions(acts: list[dict]) -> dict[str, dict]:
    """Settlement events by market slug: proceeds actually received."""
    by_slug: dict[str, dict] = {}
    for act in acts:
        if act.get("type") != "ACTIVITY_TYPE_POSITION_RESOLUTION":
            continue
        pr = act.get("positionResolution") or {}
        slug = str(pr.get("marketSlug") or "")
        if not slug:
            continue
        rec = by_slug.setdefault(slug, {
            "slug": slug, "events": 0, "proceeds": 0.0,
            "shares": 0.0, "ts": None})
        rec["events"] += 1
        rec["proceeds"] += _amt(pr.get("payout") or pr.get("proceeds")
                                or pr.get("amount"))
        rec["shares"] += _amt(pr.get("quantity") or pr.get("shares"))
        ts = float(_any_ts(act) or 0.0)
        if ts:
            rec["ts"] = ts if rec["ts"] is None else max(rec["ts"], ts)
    return by_slug


TOL_SHARES = 0.01
TOL_USD = 0.01


def reconcile_rows(lo: list[dict], by_order: dict[str, dict]) -> list[dict]:
    """One output row per internal fill record, with its verdict."""
    rows = []
    for r in lo:
        oid = str(r.get("order_id") or "")
        v = by_order.get(oid)
        row = {
            "live_order_id": r["id"],
            "order_id": oid or None,
            "slug": r.get("us_market_slug"),
            "status": r.get("status"),
            "side": r.get("side"),
            "placed_at": str(r.get("placed_at")),
            "internal_shares": _f(r.get("filled_shares")),
            "internal_usd": _f(r.get("filled_usd")),
            "internal_price": _f(r.get("fill_price")) or None,
        }
        if not oid:
            row["verdict"] = "NO_ORDER_ID_RECORDED"
            row["note"] = ("the ledger row names no venue order, so it "
                           "cannot be matched in either direction")
            rows.append(row)
            continue
        if v is None:
            row["verdict"] = "NOT_FOUND_AT_VENUE"
            row["note"] = ("no venue trade carries this order id within "
                           "the window read")
            rows.append(row)
            continue
        row.update({
            "venue_shares": round(v["shares"], 6),
            "venue_notional": round(v["notional"], 6),
            "venue_price": (round(v["avg_price"], 6)
                            if v["avg_price"] is not None else None),
            "venue_executions": v["executions"],
            "venue_trade_ids": v["trade_ids"][:8],
            "venue_first_ts": v["first_ts"],
            "venue_last_ts": v["last_ts"],
        })
        d_sh = row["internal_shares"] - v["shares"]
        d_usd = row["internal_usd"] - v["notional"]
        row["delta_shares"] = round(d_sh, 6)
        row["delta_usd"] = round(d_usd, 6)
        if abs(d_sh) <= TOL_SHARES and abs(d_usd) <= TOL_USD:
            row["verdict"] = "MATCH"
        elif abs(d_sh) <= TOL_SHARES:
            row["verdict"] = "PRICE_OR_FEE_DIFFERENCE"
            row["note"] = ("same quantity, different money: the venue "
                           "notional excludes fees our row may include, "
                           "or vice versa")
        else:
            row["verdict"] = "QUANTITY_MISMATCH"
        rows.append(row)
    return rows


def summarise(rows, positions, open_ord, resolutions, mb, bids, asof):
    """Account level. Each figure names its own valuation method."""
    acq = sum(r["internal_usd"] for r in rows
              if r["status"] in ("filled", "settled", "cashed_out"))
    acq_filled = sum(r["internal_usd"] for r in rows if r["status"] == "filled")

    held, held_cost, held_bid = {}, 0.0, 0.0
    for slug, p in positions.items():
        net = _f(p.get("netPosition"))
        if abs(net) < 1e-9:
            continue
        basis = {}
        try:
            basis = pmus.position_basis(p) or {}
        except Exception:                                  # noqa: BLE001
            basis = {}
        cost = basis.get("cost")
        b = bids.get(slug)
        held[slug] = {"net": net, "cost": cost, "fees": basis.get("fees"),
                      "bid": b,
                      "mark_to_bid": (net * b if b is not None else None)}
        if cost:
            held_cost += float(cost)
        if b is not None:
            held_bid += net * b

    commitments = 0.0
    for o in open_ord:
        commitments += _f(o.get("size") or o.get("quantity")) * _f(o.get("price"))

    proceeds = sum(r["proceeds"] for r in resolutions.values())

    verdicts = defaultdict(int)
    for r in rows:
        verdicts[r["verdict"]] += 1

    internal_open_slugs = {r["slug"] for r in rows if r["status"] == "filled"}
    venue_open_slugs = set(held)
    mb_open = [b for b in mb if str(b.get("state")) not in ("closed",)]

    return {
        "as_of": asof,
        "valuation_methods": {
            "historical_acquisition_cost":
                "sum of filled_usd on internal fill records; a historical "
                "fact, not exposure",
            "remaining_holdings_cost_basis":
                "venue position_basis cost (all-in, fees included)",
            "remaining_holdings_mark_to_bid":
                "net shares x live best bid at as_of; what could be "
                "realised now, and it moves",
            "settled_proceeds":
                "sum of POSITION_RESOLUTION payouts; realised cash",
            "outstanding_commitments":
                "resting order size x limit price; not yet spent",
        },
        "historical_acquisition_cost_all_records": round(acq, 2),
        "historical_acquisition_cost_status_filled": round(acq_filled, 2),
        "remaining_holdings_count": len(held),
        "remaining_holdings_cost_basis": round(held_cost, 2),
        "remaining_holdings_mark_to_bid": round(held_bid, 2),
        "settled_proceeds": round(proceeds, 2),
        "settled_markets": len(resolutions),
        "outstanding_order_commitments": round(commitments, 2),
        "open_orders_count": len(open_ord),
        "row_verdicts": dict(verdicts),
        "ledger_disagreement": {
            "live_orders_says_open": len(internal_open_slugs),
            "venue_says_open": len(venue_open_slugs),
            "mirror_books_says_open": len(mb_open),
            "internal_open_not_at_venue":
                sorted(internal_open_slugs - venue_open_slugs)[:60],
            "at_venue_not_in_live_orders":
                sorted(venue_open_slugs - internal_open_slugs)[:60],
        },
    }


async def run(pool, *, since_ts: float | None = None) -> dict:
    asof = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if since_ts is None:
        since_ts = time.time() - 75 * 86400

    lo, mb = await internal_rows(pool)
    positions = venue_positions()
    open_ord = venue_open_orders()
    acts = venue_activities(["ACTIVITY_TYPE_TRADE",
                             "ACTIVITY_TYPE_POSITION_RESOLUTION"], since_ts)
    by_order = index_venue_trades(acts)
    resolutions = index_resolutions(acts)
    rows = reconcile_rows(lo, by_order)

    bids = {}
    for slug in list(positions)[:80]:
        if abs(_f(positions[slug].get("netPosition"))) > 1e-9:
            bids[slug] = venue_bid(slug)

    matched_orders = {r["order_id"] for r in rows if r.get("order_id")}
    orphan = [v for oid, v in by_order.items() if oid not in matched_orders]

    return {
        "summary": summarise(rows, positions, open_ord, resolutions, mb,
                             bids, asof),
        "rows": rows,
        "venue_positions_raw_count": len(positions),
        "venue_activities_read": len(acts),
        "venue_orders_not_in_live_orders": orphan[:60],
        "settled": list(resolutions.values())[:120],
        "open_orders": [
            {"id": o.get("id"), "slug": o.get("us_market_slug") or o.get("marketSlug"),
             "side": o.get("side"), "size": o.get("size"),
             "price": o.get("price"), "status": o.get("status")}
            for o in open_ord][:60],
    }


def main() -> int:
    import asyncio

    from .db import get_pool

    async def go():
        pool = await get_pool()
        return await run(pool)

    try:
        out = asyncio.run(go())
    except Exception as exc:                               # noqa: BLE001
        print("RECONCILE_FAILED %s: %s"
              % (type(exc).__name__, str(exc)[:300]), file=sys.stderr)
        print(json.dumps({"error": type(exc).__name__,
                          "detail": str(exc)[:300]}))
        return 2

    path = os.environ.get("RECONCILE_OUT")
    if path:
        with open(path, "w") as fh:
            json.dump(out, fh, indent=2, default=str)

    s = out["summary"]
    print("== VENUE RECONCILIATION as of %s ==" % s["as_of"])
    print()
    for k in ("historical_acquisition_cost_all_records",
              "historical_acquisition_cost_status_filled",
              "remaining_holdings_count",
              "remaining_holdings_cost_basis",
              "remaining_holdings_mark_to_bid",
              "settled_proceeds", "settled_markets",
              "outstanding_order_commitments", "open_orders_count"):
        print("  %-44s %s" % (k, s[k]))
    print()
    print("  row verdicts: %s" % json.dumps(s["row_verdicts"]))
    print()
    d = s["ledger_disagreement"]
    print("  LEDGER DISAGREEMENT")
    print("    live_orders says open  : %s" % d["live_orders_says_open"])
    print("    venue says open        : %s" % d["venue_says_open"])
    print("    mirror_books says open : %s" % d["mirror_books_says_open"])
    print("    internal-open not at venue (%d): %s"
          % (len(d["internal_open_not_at_venue"]),
             ", ".join(d["internal_open_not_at_venue"][:6]) or "none"))
    print("    at venue, not in live_orders (%d): %s"
          % (len(d["at_venue_not_in_live_orders"]),
             ", ".join(d["at_venue_not_in_live_orders"][:6]) or "none"))
    print()
    print("  venue activities read: %d" % out["venue_activities_read"])
    print("  venue orders with no live_orders row: %d"
          % len(out["venue_orders_not_in_live_orders"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
