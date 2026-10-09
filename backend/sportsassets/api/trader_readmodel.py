"""Read-only Trader Mode adapter over the existing canonical PAPER ledger.

One consistent transaction, exactly one latest review per position, all
standing orders for that position, current same-venue books. No order imports.
The API cache is process-local/single-flight and never makes a venue request.
"""
from __future__ import annotations
import asyncio
import json
import os
import time
from ..open_position_canon import CANONICAL_OPEN_POSITIONS_SQL
from .. import trader_mode as T
from ..live_game_state.integration import enrich_snapshot as enrich_live_game_snapshot
from ..simulated_account_context import selected_account

MAX_POSITIONS = 1000
CACHE_S = 2.0
_cache = {}
_lock = asyncio.Lock()

# THE CATALOGUE ROW IS READ THROUGH us_premap_ident_side (identifier,
# side_norm): production 2026-10-07 measured the market_slug-only lateral as a
# sequential scan of 181K rows per position (50 ms x 40 = 2.0 s of a 2.2 s
# read). identifier IS the market slug on every row (0 differ); market_slug
# equality is kept so the row read is exactly the same.
# Every join uses the canonical key. A group-only legacy review is accepted
# only if that group currently has exactly ONE open position. Never attach a
# sibling market's packet to make the denominator green.
READ_SQL = """
WITH open_positions AS (
""" + CANONICAL_OPEN_POSITIONS_SQL + """
), scoped AS (
 SELECT p.*, 'paperpos:'||p.account_id||':'||p.group_id||':'||
        p.us_market_slug||':'||p.holding_side AS position_id,
        count(*) OVER() AS all_open_count,
        count(*) OVER(PARTITION BY p.account_id,p.group_id) AS group_open_count
 FROM open_positions p WHERE p.account_id=$1
), selected AS (
 SELECT * FROM scoped ORDER BY group_id,us_market_slug,holding_side LIMIT $2
)
SELECT p.*, to_jsonb(ent) AS entry, to_jsonb(pm) AS premap,
       to_jsonb(rev) AS review, to_jsonb(bk) AS book,
       to_jsonb(sett) AS settlement,
       (SELECT coalesce(jsonb_agg(to_jsonb(f) ORDER BY f.filled_at,f.fill_id),'[]'::jsonb)
          FROM paper_fills f WHERE f.account_id=p.account_id
           AND f.group_id=p.group_id AND f.us_market_slug=p.us_market_slug
           AND f.holding_side=p.holding_side) AS fills,
       (SELECT coalesce(jsonb_agg(to_jsonb(o) ORDER BY o.created_at,o.order_id),'[]'::jsonb)
          FROM paper_orders o WHERE o.account_id=p.account_id
           AND o.group_id=p.group_id AND o.us_market_slug=p.us_market_slug
           AND o.holding_side=p.holding_side
           AND o.role IN ('STANDING_PROTECTION','EXIT','REDUCE')
           AND o.state IN ('RESTING','PARTIALLY_FILLED','CANCEL_PENDING','PENDING_SIMULATION')) AS orders,
       (SELECT coalesce(jsonb_agg(to_jsonb(h) ORDER BY h.observed_at),'[]'::jsonb) FROM
          (SELECT b.obs_id,b.observed_at,b.bids,b.offers FROM paper_book_observations b
            WHERE b.us_market_slug=p.us_market_slug AND b.error IS NULL
            ORDER BY b.observed_at DESC,b.obs_id DESC LIMIT 32) h) AS book_history
FROM selected p
LEFT JOIN LATERAL (SELECT o.* FROM paper_orders o WHERE o.account_id=p.account_id
  AND o.group_id=p.group_id AND o.us_market_slug=p.us_market_slug
  AND o.holding_side=p.holding_side AND o.role='ENTRY'
  ORDER BY o.created_at,o.order_id LIMIT 1) ent ON true
LEFT JOIN LATERAL (SELECT u.* FROM us_premap u WHERE u.identifier=p.us_market_slug
  AND u.market_slug=p.us_market_slug
  ORDER BY u.updated_at DESC,u.event_slug LIMIT 1) pm ON true
LEFT JOIN LATERAL (SELECT r.* FROM paper_xavier_reviews r
  WHERE r.account_id=p.account_id AND r.group_id=p.group_id AND r.reviewed_at<=to_timestamp($3)
  AND (r.selection #>> '{management_packet,position,position_key}'=p.position_id
       OR (p.group_open_count=1 AND
           r.selection #>> '{management_packet,position,position_key}' IS NULL))
  ORDER BY r.reviewed_at DESC,r.review_id DESC LIMIT 1) rev ON true
LEFT JOIN LATERAL (SELECT b.* FROM paper_book_observations b
  WHERE b.us_market_slug=p.us_market_slug AND b.error IS NULL
  ORDER BY b.observed_at DESC,b.obs_id DESC LIMIT 1) bk ON true
LEFT JOIN LATERAL (SELECT s.* FROM paper_settlements s
  WHERE s.position_key=p.position_id ORDER BY s.version DESC LIMIT 1) sett ON true
ORDER BY p.group_id,p.us_market_slug,p.holding_side
"""

GAME_SQL = """
SELECT DISTINCT ON (contract_id) contract_id,payload
FROM market_plane_events
WHERE kind='TRADER_GAME_STATE' AND contract_id=ANY($1::text[])
ORDER BY contract_id,at DESC,event_key DESC
"""


def _mark(obs, side, now):
    from .. import bettor_book_snapshot as BS
    o = T.object_value(obs)
    intent = 'ORDER_INTENT_BUY_' + str(side)
    if side not in ('LONG','SHORT'):
        return {"current": False, "why": "HELD_SIDE_UNKNOWN"}
    md = {"bids": T.array_value(o.get("bids")), "offers": T.array_value(o.get("offers"))}
    ex = BS.exit_ladder(md, held_intent=intent)
    acq = BS.acquisition_ladder(md, intent=intent)
    bid = T.price(ex.get("best_exit_price")) if ex.get("ok") else None
    ask = T.price(acq["levels"][0].get("acquisition_price")) if acq.get("ok") and acq.get("levels") else None
    read_fresh = T.fresh_at(o.get("observed_at"), now, T.BOOK_LIMIT_S)
    current = bid is not None and read_fresh
    # THE REASON IS THE READ'S OWN (production 2026-10-07: seven fresh reads
    # of OPEN markets whose exit side was empty were labelled
    # BOOK_ABSENT_OR_EXPIRED). No row -> absent; a row past the 300 s limit
    # -> expired; a fresh read with no executable level on the side a close
    # consumes -> that, by name (bettor_paper_freshness calls it UNMARKED).
    if current:
        why = None
    elif not o:
        why = "NO_SUCCESSFUL_BOOK_READ"
    elif not read_fresh:
        why = "BOOK_READ_OLDER_THAN_300S"
    else:
        why = ex.get("refusal") or "EXIT_SIDE_HAS_NO_EXECUTABLE_LEVEL"
    return {"bid": bid, "ask": ask, "at": T.epoch(o.get("observed_at")),
            "source": o.get("source"), "read_basis": o.get("read_basis"),
            "observation_id": o.get("obs_id"), "current": current,
            "depth_at_bid": T.num(ex.get("size_at_best")),
            "total_exit_depth": T.num(ex.get("displayed_depth")),
            "market_state": o.get("market_state"),
            "why": why}


def project_rows(raw, *, now, games=None):
    from ..position_rooms import slice_from_fills
    out = []
    games = games or {}
    for raw_row in raw:
        r = dict(raw_row)
        ent, pm, rev = [T.object_value(r.get(k)) for k in ("entry","premap","review")]
        if rev:
            rev["reviewed_at"] = T.epoch(rev.get("reviewed_at"))
        label = T.object_value(ent.get("label"))
        settlement = T.object_value(r.get("settlement")) or None
        fills = [dict(f, at=f.get("filled_at")) for f in T.array_value(r.get("fills"))]
        holding = slice_from_fills(fills, settlement)
        qty = float(r["open_qty"])
        agrees = T.num(holding.get("open_qty")) is not None and abs(holding["open_qty"]-qty) <= 1e-6
        fees_known = holding.get("fees_known") is True
        cost = holding.get("cost_basis_usd") if agrees and fees_known else None
        event_id = pm.get("event_slug")
        quote = _mark(r.get("book"), r["holding_side"], now)
        orders = []
        for o in T.array_value(r.get("orders")):
            orders.append({"position_id": r["position_id"], "order_id": o.get("order_id"),
                           "role": o.get("role"), "state": o.get("state"),
                           "direction": o.get("direction"), "limit_price": T.price(o.get("limit_price")),
                           "qty": T.num(o.get("qty")), "filled_qty": T.num(o.get("filled_qty")),
                           "remaining_qty": max(0, float(o.get("qty") or 0)-float(o.get("filled_qty") or 0)),
                           "created_at": T.epoch(o.get("created_at")),
                           "expires_at": T.epoch(o.get("expires_at"))})
        history = []
        for h in T.array_value(r.get("book_history")):
            m = _mark(h, r["holding_side"], now)
            if m.get("bid") is not None and m.get("at") is not None:
                history.append({"at": m["at"], "price": m["bid"], "observation_id": m.get("observation_id")})
        history.sort(key=lambda x: x["at"])
        out.append({"position_id": r["position_id"], "group_id": r["group_id"],
                    "account_id": r["account_id"], "venue": "POLYMARKET_US",
                    "market_id": r["us_market_slug"], "event_id": event_id,
                    "holding_side": r["holding_side"], "qty": qty,
                    "strategy": ent.get("strategy"), "entry_agent": "DEREK",
                    "management_agent": "XAVIER", "title": label.get("event_title") or label.get("title") or event_id or r["us_market_slug"],
                    "market_title": label.get("outcome") or label.get("selection") or r["us_market_slug"],
                    "sport": label.get("sport") or pm.get("sport") or "UNCLASSIFIED",
                    "family": label.get("market_type") or pm.get("sports_market_type") or "UNCLASSIFIED",
                    "entry_price": holding.get("avg_entry_price"), "cost_basis_usd": cost,
                    "cost_basis_reason": None if cost is not None else "RECONCILIATION_OR_FEE_EVIDENCE_MISSING",
                    "opened_at": T.epoch(holding.get("first_fill_at")),
                    "realized_usd": holding.get("realized_pnl_usd") if agrees and fees_known else None,
                    "quote": quote, "orders": orders, "review": rev,
                    "quote_history": history, "game": games.get(event_id),
                    "event_start": T.epoch(pm.get("game_start")),
                    "identity": {"basis": "CANONICAL_LEDGER_KEY_AND_EXACT_VENUE_CATALOGUE_ROW",
                                 "event_verified": bool(event_id)}})
    return out


async def read(pool, *, account_id=None, now=None):
    at = time.time() if now is None else float(now)
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True, isolation="repeatable_read"):
            account_id = account_id or await selected_account(conn)
            await conn.execute("SET LOCAL statement_timeout = 8000")
            rows = await conn.fetch(READ_SQL, account_id, MAX_POSITIONS, at)
            events = sorted({T.object_value(r.get("premap")).get("event_slug") for r in rows
                             if T.object_value(r.get("premap")).get("event_slug")})
            games, game_error = {}, None
            try:
                async with conn.transaction():
                    grows = await conn.fetch(GAME_SQL, ["game:"+e for e in events]) if events else []
                    games = {r["contract_id"][5:]: T.object_value(r["payload"]) for r in grows}
            except Exception as exc:
                game_error = "SCORE_EVIDENCE_READ_FAILED:" + type(exc).__name__
            evaluated_at = time.time() if now is None else at
            data = project_rows(rows, now=evaluated_at, games=games)
            total = int(rows[0]["all_open_count"]) if rows else 0
            snapshot = T.build_snapshot(data, now=evaluated_at, total_count=total,
                                        source_sha=os.getenv("RENDER_GIT_COMMIT"))
            snapshot["database_snapshot_at"] = at
            snapshot["score_feed"] = {"configured_evidence_events": len(games), "why": game_error or (
                None if games else "NO_SCORE_PROVIDER_EVIDENCE_RECORDED")}
            snapshot = await enrich_live_game_snapshot(conn, snapshot, now=evaluated_at)
            return snapshot


async def cached(pool, *, account_id=None):
    # Route authentication runs BEFORE this cache. Shared per account, never a
    # cache key supplied in the browser. No credentials are retained here.
    if account_id is None:
        async with pool.acquire() as conn:
            account_id = await selected_account(conn)
    async with _lock:
        at = time.monotonic()
        got = _cache.get(account_id)
        if got and at - got[0] < CACHE_S:
            return got[1]
        result = await read(pool, account_id=account_id)
        _cache[account_id] = (time.monotonic(), result)
        return result
