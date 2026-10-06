"""GET-only Command readback for the Universal Market Plane.

  GET /api/command/market-plane              the latest snapshot (universe,
      coverage terminal states, subscription plan / shards / overflow,
      sources, freshness, latency SLO, certification, catalogue
      completeness, Radar) + registry aggregates + a bounded registry page
  GET /api/command/market-plane/opportunity  one immutable canonical
      opportunity object for a contract (fail-closed with named gaps)

MARKET_DATA_ONLY_NO_ORDER_AUTHORITY: nothing here writes, trades, sizes or
promotes. A READ ONLY transaction with a statement timeout.
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Query

from ..market_plane import AUTHORITY, VERSION
from .agents_core import _pool, require_read

router = APIRouter()
BASE = "/api/command/market-plane"


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _row(r):
    d = dict(r)
    for k, v in list(d.items()):
        if hasattr(v, "timestamp"):
            d[k] = v.timestamp()
        elif k in ("ontology", "detail", "payload", "refdata"):
            d[k] = _j(v)
    return d


def _empty(why):
    return {"status": "EMPTY", "why": why, "version": VERSION,
            "authority": AUTHORITY, "label": "RESEARCH"}


@router.get(BASE, dependencies=[Depends(require_read)])
async def market_plane(limit: int = Query(default=200, ge=0, le=5000),
                       state: str | None = Query(default=None),
                       sport: str | None = Query(default=None)):
    pool = await _pool()
    async with pool.acquire() as c:
        if not await c.fetchval(
                "SELECT to_regclass('market_plane_registry') IS NOT NULL"):
            return _empty("MIGRATION_312_NOT_APPLIED")
        async with c.transaction(readonly=True):
            await c.execute("SET LOCAL statement_timeout = 15000")
            snap = await c.fetchrow(
                "SELECT payload, at FROM market_plane_events "
                " WHERE kind = 'SNAPSHOT' ORDER BY at DESC LIMIT 1")
            counts = dict(await c.fetchrow(
                "SELECT count(*) FILTER (WHERE active) AS active, "
                "       count(*) FILTER (WHERE active AND desired_subscription)"
                "           AS desired, "
                "       count(*) FILTER (WHERE active AND subscription_shard "
                "           IS NOT NULL) AS assigned, "
                "       count(*) AS total FROM market_plane_registry"))
            by_state = {r["s"]: r["n"] for r in await c.fetch(
                "SELECT coalesce(coverage_state, 'NOT_YET_CLASSIFIED') AS s, "
                "       count(*) AS n FROM market_plane_registry "
                " WHERE active GROUP BY 1")}
            by_sport = {}
            for r in await c.fetch(
                    "SELECT coalesce(sport, 'UNKNOWN') AS sp, "
                    "       coalesce(coverage_state, 'NOT_YET_CLASSIFIED') AS s,"
                    "       count(*) AS n FROM market_plane_registry "
                    " WHERE active GROUP BY 1, 2"):
                by_sport.setdefault(r["sp"], {})[r["s"]] = r["n"]
            cert = dict(await c.fetchrow(
                "SELECT count(*) FILTER (WHERE status = 'SUPPORTED') "
                "           AS supported, "
                "       count(*) FILTER (WHERE status = 'ACCUMULATING') "
                "           AS accumulating, count(*) AS total "
                "  FROM market_plane_certification"))
            rows = []
            if limit:
                where, args = ["active"], []
                if state:
                    args.append(state)
                    where.append("coverage_state = $%d" % len(args))
                if sport:
                    args.append(sport)
                    where.append("sport = $%d" % len(args))
                args.append(int(limit))
                rows = [_row(r) for r in await c.fetch(
                    "SELECT contract_id, venue, sport, competition, event_id, "
                    "       market_type, family, period, event_start, priority,"
                    "       required_reason, subscription_shard, "
                    "       coverage_state, coverage_why, last_seen_at, "
                    "       (refdata IS NOT NULL AND coalesce(refdata->>"
                    "        'unlisted','false') <> 'true') AS pmx_listed "
                    "  FROM market_plane_registry WHERE " + " AND ".join(where)
                    + " ORDER BY priority, contract_id LIMIT $%d" % len(args),
                    *args)]
    payload = _j(snap["payload"]) if snap else None
    return {"status": "OK" if payload else "EMPTY",
            "why": None if payload else "NO_MARKET_PLANE_SNAPSHOT_YET",
            "version": VERSION, "authority": AUTHORITY, "label": "RESEARCH",
            "computed_at": time.time(),
            "snapshot_at": snap["at"].timestamp() if snap else None,
            "snapshot": payload, "counts": counts,
            "coverage_by_state": by_state, "coverage_by_sport": by_sport,
            "certification": cert, "registry": rows,
            "terminal_states": ["PRICEABLE",
                                "MAPPED_BUT_NO_FAIR_VALUE_SOURCE",
                                "MAPPED_BUT_SETTLEMENT_NOT_PROVEN",
                                "EXTERNAL_DATA_UNAVAILABLE",
                                "CODE_CONTROLLED_GAP"]}


@router.get(BASE + "/opportunity", dependencies=[Depends(require_read)])
async def market_plane_opportunity(contract_id: str = Query(...,
                                                            max_length=200)):
    """THE CANONICAL OPPORTUNITY OBJECT for one contract (market_plane.
    opportunity.build): registry contract + ontology (both sides), canonical
    entities, the newest recorded book, the newest decision valuation's
    probability / settlement comparison / execution estimate. Content
    addressed; fail-closed with named gaps. Read only, no authority."""
    from ..market_plane import ontology as O
    from ..market_plane import opportunity as OPP
    pool = await _pool()
    async with pool.acquire() as c:
        if not await c.fetchval(
                "SELECT to_regclass('market_plane_registry') IS NOT NULL"):
            return _empty("MIGRATION_312_NOT_APPLIED")
        async with c.transaction(readonly=True):
            await c.execute("SET LOCAL statement_timeout = 8000")
            reg = await c.fetchrow(
                "SELECT * FROM market_plane_registry WHERE contract_id = $1",
                contract_id)
            if reg is None:
                return dict(_empty("CONTRACT_NOT_IN_REGISTRY"),
                            contract_id=contract_id)
            reg = _row(reg)
            book = await c.fetchrow(
                "SELECT obs_id, observed_at, venue_ts, source, bids, offers, "
                "       market_state FROM paper_book_observations "
                " WHERE us_market_slug = $1 "
                " ORDER BY observed_at DESC, obs_id DESC LIMIT 1",
                contract_id) if await c.fetchval(
                "SELECT to_regclass('paper_book_observations') IS NOT NULL") \
                else None
            val = await c.fetchrow(
                "SELECT id, probability, observed_at, decided_at, "
                "       settlement_comparison, execution_estimate, refusals, "
                "       record_purpose "
                "  FROM external_valuations WHERE us_market_slug = $1 "
                " ORDER BY decided_at DESC, id DESC LIMIT 1", contract_id)
    ont = reg.get("ontology") or {}
    bk = None
    if book is not None:
        def lv(levels):
            out = []
            for x in levels or ():
                try:
                    px = x.get("px")
                    px = px.get("value") if isinstance(px, dict) else px
                    out.append([float(px), float(x.get("qty"))])
                except (AttributeError, TypeError, ValueError):
                    continue
            return out
        bids, offers = lv(_j(book["bids"])), lv(_j(book["offers"]))
        bk = {"source": book["source"] or "REST_RECOVERY",
              "obs_id": book["obs_id"],
              "observed_at": book["observed_at"].timestamp(),
              "venue_ts": book["venue_ts"].timestamp()
              if hasattr(book["venue_ts"], "timestamp") else book["venue_ts"],
              "market_state": book["market_state"],
              "bids": bids, "offers": offers,
              "sides": {s: {a: O.side_price({"bids": bids, "offers": offers},
                                            s, a) for a in ("BUY", "SELL")}
                        for s in ("LONG", "SHORT")}}
    prob = sett = exe = None
    if val is not None:
        prob = {"valuation_id": val["id"],
                "probability": float(val["probability"])
                if val["probability"] is not None else None,
                "observed_at": val["observed_at"].timestamp()
                if val["observed_at"] else None,
                "decided_at": val["decided_at"].timestamp()
                if val["decided_at"] else None,
                "record_purpose": val["record_purpose"]}
        sett = _j(val["settlement_comparison"]) or None
        exe = _j(val["execution_estimate"]) or None
    opp = OPP.build(contract={"contract_id": contract_id,
                              "venue": reg.get("venue"),
                              "ontology": ont.get("meaning"),
                              "sides": ont.get("sides"),
                              "coverage_state": reg.get("coverage_state"),
                              "coverage_why": reg.get("coverage_why")},
                    entities=ont.get("entities") or {}, book=bk,
                    probability=prob, settlement=sett, execution=exe,
                    portfolio=None)
    return dict(opp, status="OK", authority=AUTHORITY, label="RESEARCH")
