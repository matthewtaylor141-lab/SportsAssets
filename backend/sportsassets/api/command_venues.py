"""GET /api/command/venues -- KALSHI AND POLYMARKET US AS CANONICAL VENUES.

One authenticated, READ-ONLY readback (Kalshi Canonical Venue V1):

  health       KALSHI_HEALTH and POLYMARKET_HEALTH, each from its own
               records and never blended (a Kalshi 429 cannot make Polymarket
               stale, a Polymarket reconnect cannot make Kalshi healthy)
  catalogue    the Kalshi sports catalogue census (COMPLETE / TRUNCATED)
  coverage     Kalshi mapping coverage by sport x family (numerator /
               denominator), fixture-structured and mapped-to-PMUS apart
  freshness    Kalshi book freshness (numerator / denominator)
  claims       canonical claims with every executable alias (venue,
               contract, side, best ask, fee, all-in, fresh) and the BEST
               ALL-IN ROUTE, the runner-up and why each other path lost
  arbitrage    Adriana's claim-first scans (migration 265): leg claims, the
               contracts chosen, topology, size, principal, fees, slippage,
               buffers, guaranteed payout, guaranteed net profit, ROI, book
               ages, skew, settlement status
It changes nothing: no order, cancel, credential or capital path.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Response

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/venues"
CACHE_S = 30.0
STATEMENT_TIMEOUT_MS = 15000
LABEL = "SHADOW"
AUTHORITY = "READBACK_ONLY_NO_ORDER_NO_CAPITAL_AUTHORITY"
_CACHE: dict = {}
MAX_EVENTS = 12


def envelope(status, why=None, *, data=None, computed_at=None) -> dict:
    return {"label": LABEL, "authority": AUTHORITY, "status": status,
            "why": why, "computed_at": computed_at, "data": data}


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


async def _has(c, t):
    return bool(await c.fetchval("SELECT to_regclass($1) IS NOT NULL", t))


async def kalshi_health(c, *, now: float) -> dict:
    r = await c.fetchrow("SELECT status, detail, beat_at FROM "
                         "service_heartbeats WHERE service = "
                         "'kalshi_market_data'")
    if r is None:
        return {"domain": "KALSHI_HEALTH", "state": "NOT_RUNNING"}
    d = _j(r["detail"]) or {}
    age = now - r["beat_at"].timestamp()
    return {"domain": "KALSHI_HEALTH",
            "state": "STALE" if age > 300 else r["status"].upper(),
            "heartbeat_age_s": round(age, 1), "health": d.get("health"),
            "catalogue": d.get("catalogue"), "coverage": d.get("coverage"),
            "fixtures": d.get("fixtures"), "freshness": d.get("freshness"),
            "tracked_markets": d.get("tracked_markets"),
            "claims": d.get("claims")}


async def polymarket_health(c, *, now: float) -> dict:
    """POLYMARKET_HEALTH from the Polymarket path's own records only."""
    r = await c.fetchrow(
        "SELECT count(*) FILTER (WHERE error IS NULL) AS ok, "
        "       count(*) FILTER (WHERE error IS NOT NULL) AS errors, "
        "       extract(epoch FROM now() - max(observed_at) FILTER "
        "               (WHERE error IS NULL)) AS newest_age_s "
        "  FROM paper_book_observations "
        " WHERE observed_at > now() - interval '15 minutes'")
    ok, err = int(r["ok"] or 0), int(r["errors"] or 0)
    return {"domain": "POLYMARKET_HEALTH",
            "state": ("NO_RECENT_BOOKS" if ok == 0 else
                      "DEGRADED" if err > ok else "OK"),
            "books_15m": ok, "errors_15m": err,
            "newest_book_age_s": (round(float(r["newest_age_s"]), 1)
                                  if r["newest_age_s"] is not None else None),
            "source": "paper_book_observations (the PMUS read path)"}


async def claims(c) -> list:
    if not await _has(c, "canonical_claim_aliases"):
        return []
    evs = [r["event_key"] for r in await c.fetch(
        "SELECT event_key, max(updated_at) u FROM canonical_claim_aliases "
        " GROUP BY 1 ORDER BY 2 DESC LIMIT $1", MAX_EVENTS)]
    if not evs:
        return []
    aliases = await c.fetch(
        "SELECT * FROM canonical_claim_aliases WHERE event_key = "
        " ANY($1::text[]) ORDER BY event_key, claim_fingerprint NULLS LAST, "
        " venue, market_id, side", evs)
    routes = {r["claim_fingerprint"]: r for r in await c.fetch(
        "SELECT DISTINCT ON (claim_fingerprint) * FROM "
        " canonical_route_receipts WHERE event_key = ANY($1::text[]) "
        " ORDER BY claim_fingerprint, computed_at DESC", evs)}
    out: dict = {}
    for a in aliases:
        e = out.setdefault(a["event_key"], {"event_key": a["event_key"],
                                            "claims": {}, "refused": []})
        row = {"venue": a["venue"], "market_id": a["market_id"],
               "side": a["side"], "subject": a["subject"],
               "best_ask": None if a["best_ask"] is None
               else str(a["best_ask"]), "depth": a["depth"],
               "book_basis": a["book_basis"],
               "observed_at": a["observed_at"].timestamp()
               if a["observed_at"] else None,
               "settlement_status": a["settlement_status"],
               "refusals": _j(a["refusals"]) or [],
               "payoff": _j(a["payoff"]) or {}}
        fp = a["claim_fingerprint"]
        if not fp:
            e["refused"].append(row)
            continue
        cl = e["claims"].setdefault(fp, {"fingerprint": fp, "aliases": [],
                                         "route": None})
        cl["aliases"].append(row)
        rr = routes.get(fp)
        if rr is not None and cl["route"] is None:
            cl["route"] = {"computed_at": rr["computed_at"].timestamp(),
                           "qty": rr["qty"], "chosen": _j(rr["chosen"]),
                           "best_single": _j(rr["best_single"]),
                           "runner_up": _j(rr["runner_up"]),
                           "lost": _j(rr["lost"]),
                           "candidates": _j(rr["candidates"]),
                           "refusal": rr["refusal"]}
    return [dict(v, claims=list(v["claims"].values())) for v in out.values()]


async def arbitrage(c) -> dict:
    if not await _has(c, "adriana_arb_scans"):
        return {"status": "MIGRATION_265_NOT_APPLIED"}
    s = await c.fetchrow(
        "SELECT scan_id, started_at, status, why, structures_considered, "
        "       opportunities, refusals_total, by_code FROM "
        "adriana_arb_scans WHERE scan_id LIKE 'adr-claims-%' "
        " ORDER BY started_at DESC LIMIT 1")
    if s is None:
        return {"status": "NO_CLAIM_SCAN_YET"}
    opps = [dict(r) for r in await c.fetch(
        "SELECT opportunity_id, structure_kind, event_key, venues, legs, "
        "       max_qty, min_payout_usd, total_cost_usd, net_profit_usd, "
        "       economics, books FROM adriana_arb_opportunities "
        " WHERE scan_id = $1 ORDER BY net_profit_usd DESC LIMIT 20",
        s["scan_id"])]
    refs = [dict(r) for r in await c.fetch(
        "SELECT event_key, venues, legs, codes, primary_code, detail FROM "
        " adriana_arb_refusals WHERE scan_id = $1 LIMIT 40", s["scan_id"])]

    def view(o, eco, detail=None):
        eco = _j(eco) or {}
        legs = eco.get("legs") or []
        cost = eco.get("total_cost")
        net = eco.get("worst_case_net_profit")
        try:
            roi = (float(net) / float(cost)) if net is not None and cost \
                else None
        except (TypeError, ValueError, ZeroDivisionError):
            roi = None
        cp = eco.get("claim_pair") or (_j(detail) or {}).get("claim_pair") \
            or {}
        return {"event_key": o.get("event_key"),
                "topology": cp.get("topology"), "basis": cp.get("basis"),
                "claim_a": cp.get("claim_a"), "claim_b": cp.get("claim_b"),
                "legs": [{"venue": x.get("venue"),
                          "market_id": x.get("market_id"),
                          "side": x.get("side"), "qty": x.get("qty"),
                          "notional": x.get("notional"), "fee": x.get("fee"),
                          "slippage": x.get("slippage"),
                          "fixed_cost": x.get("fixed_cost")} for x in legs],
                "qty": eco.get("qty"),
                "principal": sum(float(x.get("notional") or 0)
                                 for x in legs) if legs else None,
                "fees": sum(float(x.get("fee") or 0) for x in legs)
                if legs else None,
                "slippage_buffers": sum(float(x.get("slippage") or 0)
                                        for x in legs) if legs else None,
                "guaranteed_payout": eco.get("floor_payout_total"),
                "guaranteed_net_profit": net, "roi": roi,
                "fair_price_substitution": cp.get(
                    "fair_price_substitution")}
    return {"status": "OK", "scan": {
        "scan_id": s["scan_id"], "at": s["started_at"].timestamp(),
        "status": s["status"], "why": s["why"],
        "structures": s["structures_considered"],
        "opportunities": s["opportunities"],
        "refusals": s["refusals_total"], "by_code": _j(s["by_code"])},
        "opportunities": [view(o, o["economics"]) for o in opps],
        "refusals": [dict(view(r, (_j(r["detail"]) or {}).get("economics"),
                               r["detail"]), primary_code=r["primary_code"],
                          codes=list(r["codes"] or []),
                          reasons=((_j(r["detail"]) or {}).get("reasons")
                                   or [])[:4]) for r in refs]}


async def read(c, *, now: float) -> dict:
    return {"version": "VENUES_READBACK_V1", "as_of": now,
            "mode": "MARKET_DATA_MAPPING_ROUTING_ARB_SHADOW",
            "kalshi_live_money": "NOT_ACTIVATED",
            "adriana": "SHADOW_ONLY", "authority_changed": False,
            "health": {"KALSHI_HEALTH": await kalshi_health(c, now=now),
                       "POLYMARKET_HEALTH": await polymarket_health(
                           c, now=now)},
            "claims": await claims(c), "arbitrage": await arbitrage(c)}


@router.get(PATH, dependencies=[Depends(require_read)])
async def venues(response: Response) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    now = time.time()
    hit = _CACHE.get("main")
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire(timeout=5.0) as conn:
            async with conn.transaction(readonly=True):
                await conn.execute("SET LOCAL statement_timeout = %d"
                                   % STATEMENT_TIMEOUT_MS)
                data = await read(conn, now=now)
    except Exception as exc:                                    # noqa: BLE001
        return envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:160]),
                        computed_at=now)
    out = envelope("OK", data=data, computed_at=now)
    _CACHE.clear()
    _CACHE["main"] = (now, out)
    return out
