"""Whale P&L with MERGES counted as the exits they are.

Every whale number this desk has produced grades at RESOLUTION. That is
blind to the way these accounts actually take profit, and the blindness
is total: SIDES reports 860,669 buys and zero sells for swisstony,
EXITS reports round_trips 0 and exit_rate 0.0 for every copied whale,
and CUTCHECK has to print "NO EXIT DATA" because the cashout basis does
not exist. Three instruments, one blind spot, and three whales cut on
the strength of it.

The owner found the mechanism in Polymarket's global microdata: they
close by buying the COMPLEMENTARY leg. The venue holds one signed net
position per market, so N shares of the other side retire N shares of
the holding and return $N in cash — YES + NO is worth exactly $1 by
construction. That is a round trip, it is in our trades table already,
and nothing has ever read it as one.

THE REPLAY. Walk each (whale, condition) in (ts, id) order carrying
both legs' share balances and cost bases. On a BUY of leg L for `size`
shares at `price`:

    m     = min(size, balance_of_other_leg)      -> EXIT (merge)
    entry = size - m                             -> genuine new position

A merge of m shares realises

    m * (1 - avg_cost_of_held_leg - price_paid_now)

because the pair returns $1 per share against what both legs cost. It
burns m from BOTH balances. A plain SELL is handled as the ordinary
round trip it already is.

WHAT THIS IS NOT. It is not a claim about our own order semantics. The
question of whether OUR BUY_SHORT is booked as a sell is separate,
unsettled, and gated behind LIVE_SHORT_COST_MODEL. This module reads
the WHALE's fills, where the complement buy is visible directly and
needs no venue model at all.
"""

from __future__ import annotations

from typing import Any

# Below this many shares a leg is treated as flat — venue dust, not a
# position, and it otherwise leaves permanent 1e-9 balances that make
# every later buy look like a partial merge.
DUST = 1e-6


def replay(fills: list[dict]) -> dict:
    """Classify one whale's fills. Pure — no database, so it is testable.

    `fills` must be ordered (condition_id, ts, id) and carry:
        condition_id, outcome_index, side, size, price
    """
    per_cond: dict[str, list[list[float]]] = {}
    out = {
        "n_fills": 0, "n_entries": 0, "n_merges": 0, "n_sells": 0,
        "entry_notional": 0.0, "merge_shares": 0.0,
        "realized_merge_pnl": 0.0, "realized_sell_pnl": 0.0,
        "open_shares": 0.0, "open_cost": 0.0, "rows": [],
    }
    for f in fills:
        cid = str(f.get("condition_id") or "")
        try:
            idx = int(f.get("outcome_index") if f.get(
                "outcome_index") is not None else -1)
            size = float(f.get("size") or 0)
            price = float(f.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if not cid or idx not in (0, 1) or size <= 0:
            continue
        out["n_fills"] += 1
        # [shares_leg0, shares_leg1, cost_leg0, cost_leg1]
        st = per_cond.setdefault(cid, [[0.0, 0.0], [0.0, 0.0]])
        bal, cost = st[0], st[1]
        other = 1 - idx
        if (f.get("side") or "BUY").upper() == "SELL":
            q = min(size, bal[idx])
            if q > DUST:
                avg = cost[idx] / bal[idx] if bal[idx] > DUST else 0.0
                out["realized_sell_pnl"] += q * (price - avg)
                bal[idx] -= q
                cost[idx] -= q * avg
                out["n_sells"] += 1
            continue
        # A BUY. The part that meets an opposing balance is an EXIT.
        m = min(size, bal[other])
        if m > DUST:
            avg_other = (cost[other] / bal[other]
                         if bal[other] > DUST else 0.0)
            pnl = m * (1.0 - avg_other - price)
            out["realized_merge_pnl"] += pnl
            out["merge_shares"] += m
            out["n_merges"] += 1
            bal[other] -= m
            cost[other] -= m * avg_other
            out["rows"].append({
                "condition_id": cid, "merged_shares": round(m, 4),
                "held_avg": round(avg_other, 4),
                "complement_price": round(price, 4),
                "pnl": round(pnl, 2),
            })
        entry = size - m
        if entry > DUST:
            bal[idx] += entry
            cost[idx] += entry * price
            out["entry_notional"] += entry * price
            out["n_entries"] += 1
    for st in per_cond.values():
        out["open_shares"] += st[0][0] + st[0][1]
        out["open_cost"] += st[1][0] + st[1][1]
    for k in ("entry_notional", "merge_shares", "realized_merge_pnl",
              "realized_sell_pnl", "open_shares", "open_cost"):
        out[k] = round(out[k], 2)
    out["realized_total"] = round(
        out["realized_merge_pnl"] + out["realized_sell_pnl"], 2)
    out["roi_on_entries"] = (
        round(out["realized_total"] / out["entry_notional"], 4)
        if out["entry_notional"] > 0 else None)
    out["rows"] = sorted(out["rows"], key=lambda r: r["pnl"])[:5] + \
        sorted(out["rows"], key=lambda r: -r["pnl"])[:5]
    return out


async def whale_merge_pnl(pool: Any, whales: list[str],
                          since: str = "2026-08-01",
                          max_fills: int = 600000) -> dict:
    """Run the replay for each whale off the trades ledger.

    BOUNDED. The first probe of this endpoint came back "unavailable":
    seven whales, some with 860,000 fills, each needing an ordered walk
    the existing indexes do not serve. Migration 029 adds
    (whale_id, condition_id, ts, id); this cap is the belt to that
    braces, so a whale with an unexpected ledger cannot hang the probe.

    The cap is REPORTED, never silent — a truncated replay that reads
    as a complete one is a wrong P&L presented as a right one.
    """
    # asyncpg infers the parameter type from the CAST, so `$2::date`
    # expects a datetime.date and rejects a str with an opaque 500 —
    # which is exactly what the first two probes of this endpoint got.
    # api_true_edge_cashout already does this conversion (app.py:3578);
    # I wrote a new query instead of following the idiom next to it.
    import datetime as _dt

    since_d = (since if isinstance(since, _dt.date)
               else _dt.datetime.fromisoformat(str(since)).date())
    res: dict[str, Any] = {}
    for w in whales:
        rows = await pool.fetch(
            """
            SELECT t.condition_id, t.outcome_index, t.side,
                   t.size::float8 AS size, t.price::float8 AS price
              FROM trades t JOIN whales wh ON wh.id = t.whale_id
             WHERE lower(wh.username) = $1
               AND t.ts >= $2
               AND t.condition_id IS NOT NULL
               AND t.outcome_index IS NOT NULL
             ORDER BY t.condition_id, t.ts, t.id
             LIMIT $3
            """, w.lower(), since_d, max_fills)
        out = replay([dict(r) for r in rows])
        out["fills_read"] = len(rows)
        out["truncated"] = len(rows) >= max_fills
        if out["truncated"]:
            out["verdict_note"] = (
                f"TRUNCATED at {max_fills} fills — this is a partial "
                f"replay and the totals are floors, not totals")
        res[w] = out
    return res
