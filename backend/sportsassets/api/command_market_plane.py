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
        elif k in ("ontology", "detail", "payload", "refdata",
                   "settlement_evidence", "evidence"):
            d[k] = _j(v)
    return d


def _empty(why):
    return {"status": "EMPTY", "why": why, "version": VERSION,
            "authority": AUTHORITY, "label": "RESEARCH"}


async def _held_freshness(pool) -> dict:
    """bettor_paper_freshness.read over the account's open positions, or
    UNREAD with its reason. Never raises."""
    try:
        from .. import bettor_paper_freshness as PMF
        from .. import bettor_paper_ledger as L
        async with pool.acquire() as c:
            async with c.transaction(readonly=True):
                await c.execute("SET LOCAL statement_timeout = 15000")
                got = await PMF.read(c, L.ACCOUNT_ID, rows_limit=0)
        return {"open_positions": got.get("open_positions"),
                "markable": got.get("markable"),
                "freshly_manageable": got.get("freshly_manageable"),
                "external_unavailable": ((got.get("counts") or {}).get(
                    "EXTERNAL_UNAVAILABLE") or {}).get("count"),
                "rate": got.get("fresh_rate"),
                "basis": "bettor_paper_freshness.read (FRESH + QUIET_VALID "
                         "/ markable; 300 s SLA)"}
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNREAD", "why": type(exc).__name__, "rate": None}


PARITY_WINDOW_S = 60.0
PARITY_SAMPLE = 20
#: = completion.read.SNAPSHOT_MAX_AGE_S (a test pins them equal): a plane
#: publication older than the readback's own snapshot bound is STALE
PARITY_STALE_AFTER_S = 600.0


def _top(levels, best) -> float | None:
    out = None
    for lv in levels or ():
        try:
            px = lv.get("px")
            v = float(px.get("value") if isinstance(px, dict) else px)
        except (TypeError, ValueError, AttributeError):
            continue
        out = v if out is None else (max(out, v) if best == "max"
                                     else min(out, v))
    return out


async def consumer_parity(pool) -> dict:
    """THE MARKET-PLANE CONSUMER BRIDGE, SHADOW (owner closeout item 7): the
    worker's latest PMX tops for the priority members against the REST /
    public book the paper runtime recorded for the SAME contract (exact
    identity: the venue slug) within PARITY_WINDOW_S of the PMX receipt.
    Same 300 s freshness rule on both sides; the REST read stays the
    consumer; no funded authority; read only -- nothing here reaches a
    decision."""
    try:
        async with pool.acquire() as c:
            return await parity_read(c)
    except Exception as exc:                                    # noqa: BLE001
        return dict(_parity_head(), status="UNREADABLE",
                    error=type(exc).__name__)


def _parity_head() -> dict:
    return {"mode": "SHADOW_PARITY_NO_DECISION_EFFECT",
            "consumer_of_record": "paper_book_observations (REST/public)",
            "identity": "EXACT_VENUE_SLUG", "window_s": PARITY_WINDOW_S}


async def parity_read(c) -> dict:
    out = _parity_head()
    ev = await c.fetchrow(
        "SELECT payload, at FROM market_plane_events WHERE kind = "
        " 'PRIORITY_PMX_BOOKS' ORDER BY at DESC LIMIT 1")
    if ev is None:
        return dict(out, status="NO_PMX_BOOKS_PUBLISHED_YET")
    books = (_j(ev["payload"]) or {}).get("books") or {}
    rest = {r["us_market_slug"]: r for r in await c.fetch(
        "SELECT DISTINCT ON (us_market_slug) us_market_slug, bids, "
        "       offers, observed_at, error FROM "
        " paper_book_observations WHERE us_market_slug = "
        " ANY($1::text[]) AND observed_at > now() - interval "
        " '15 minutes' ORDER BY us_market_slug, observed_at DESC",
        sorted(books))}
    n = {"pmx_books": len(books), "rest_in_window": 0, "rest_absent": 0,
         "top_equal": 0, "top_different": 0}
    diffs = []
    for slug, b in sorted(books.items()):
        r = rest.get(slug)
        rcv = b.get("received_at")
        if r is None or r["error"] or rcv is None or abs(
                r["observed_at"].timestamp() - float(rcv)) > PARITY_WINDOW_S:
            n["rest_absent"] += 1
            continue
        n["rest_in_window"] += 1
        rb = _top(_j(r["bids"]), "max")
        ro = _top(_j(r["offers"]), "min")
        same = all((x is None and y is None) or (
            x is not None and y is not None and abs(float(x) - float(y))
            < 1e-9) for x, y in ((rb, b.get("best_bid")),
                                 (ro, b.get("best_offer"))))
        n["top_equal" if same else "top_different"] += 1
        if not same and len(diffs) < PARITY_SAMPLE:
            diffs.append({"contract_id": slug, "pmx_bid": b.get("best_bid"),
                          "rest_bid": rb, "pmx_offer": b.get("best_offer"),
                          "rest_offer": ro,
                          "skew_s": round(r["observed_at"].timestamp()
                                          - float(rcv), 1)})
    cmp_ = n["rest_in_window"]
    # A PUBLICATION OF A PLANE THAT HAS SINCE STOPPED OR RESTARTED is not
    # evidence of now (RC5; the plane OOM-cycles): past the readback's own
    # snapshot bound it is STALE, its age stated, its counts kept as history
    pub_age = max(0.0, time.time() - ev["at"].timestamp())
    return dict(out, status=("OK" if pub_age <= PARITY_STALE_AFTER_S
                             else "STALE_PLANE_PUBLICATION"),
                published_at=ev["at"].timestamp(),
                published_age_s=round(pub_age, 1),
                counts=n, parity_rate=(round(n["top_equal"] / cmp_, 4)
                                       if cmp_ else None),
                differences=diffs)


@router.get(BASE, dependencies=[Depends(require_read)])
async def market_plane(limit: int = Query(default=200, ge=0, le=5000),
                       state: str | None = Query(default=None),
                       sport: str | None = Query(default=None),
                       venue: str | None = Query(default=None,
                                                 max_length=40),
                       settlement_state: str | None = Query(
                           default=None, max_length=60)):
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
            # (settlement rule registry) settlement state counts, by venue
            # and the bounded venue x sport x league x family breakdown, read
            # live from the registry; and the rules-text counts
            by_settlement, settlement_by_venue = {}, {}
            for r in await c.fetch(
                    "SELECT venue, coalesce(settlement_state, "
                    "       'NOT_YET_CLASSIFIED') AS s, count(*) AS n "
                    "  FROM market_plane_registry WHERE active "
                    " GROUP BY 1, 2"):
                by_settlement[r["s"]] = by_settlement.get(r["s"], 0) + r["n"]
                settlement_by_venue.setdefault(r["venue"], {})[r["s"]] = \
                    r["n"]
            breakdown = {}
            for r in await c.fetch(
                    "SELECT venue, coalesce(sport, 'UNKNOWN') AS sp, "
                    "       coalesce(competition, 'UNKNOWN') AS lg, "
                    "       coalesce(family, 'UNKNOWN') AS fa, "
                    "       coalesce(settlement_state, 'NOT_YET_CLASSIFIED')"
                    "           AS s, count(*) AS n "
                    "  FROM market_plane_registry WHERE active "
                    " GROUP BY 1, 2, 3, 4, 5 ORDER BY n DESC LIMIT 400"):
                breakdown.setdefault("%s|%s|%s|%s" % (
                    r["venue"], r["sp"], r["lg"], r["fa"]), {})[r["s"]] = \
                    r["n"]
            from ..market_plane import rules as RULES
            rules = await RULES.rules_counts(c)
            rows = []
            if limit:
                where, args = ["active"], []
                if state:
                    args.append(state)
                    where.append("coverage_state = $%d" % len(args))
                if sport:
                    args.append(sport)
                    where.append("sport = $%d" % len(args))
                if venue:
                    args.append(venue)
                    where.append("venue = $%d" % len(args))
                if settlement_state:
                    args.append(settlement_state)
                    where.append("settlement_state = $%d" % len(args))
                args.append(int(limit))
                rows = [_row(r) for r in await c.fetch(
                    "SELECT contract_id, venue, sport, competition, event_id, "
                    "       market_type, family, period, event_start, priority,"
                    "       required_reason, subscription_shard, "
                    "       coverage_state, coverage_why, last_seen_at, "
                    "       settlement_state, settlement_why, "
                    "       settlement_basis, "
                    "       (refdata IS NOT NULL AND coalesce(refdata->>"
                    "        'unlisted','false') <> 'true') AS pmx_listed "
                    "  FROM market_plane_registry WHERE " + " AND ".join(where)
                    + " ORDER BY priority, contract_id LIMIT $%d" % len(args),
                    *args)]
    payload = _j(snap["payload"]) if snap else None
    # THE HELD-POSITION DENOMINATOR, read here (the worker must not import
    # the paper ledger): bettor_paper_freshness.read over the open positions
    if isinstance(payload, dict) and isinstance(payload.get("freshness"),
                                                dict):
        payload["freshness"]["held_positions"] = await _held_freshness(pool)
    parity = await consumer_parity(pool)
    return {"status": "OK" if payload else "EMPTY",
            "consumer_parity": parity,
            "why": None if payload else "NO_MARKET_PLANE_SNAPSHOT_YET",
            "version": VERSION, "authority": AUTHORITY, "label": "RESEARCH",
            "computed_at": time.time(),
            "snapshot_at": snap["at"].timestamp() if snap else None,
            "snapshot": payload, "counts": counts,
            "coverage_by_state": by_state, "coverage_by_sport": by_sport,
            "certification": cert, "registry": rows,
            "settlement_by_state": by_settlement,
            "settlement_by_venue": settlement_by_venue,
            "settlement_breakdown_venue_sport_league_family": breakdown,
            "settlement_breakdown_bounded_to_rows": 400,
            "rules": rules,
            "settlement_states": ["SETTLEMENT_PROVEN_COMPATIBLE",
                                  "SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED",
                                  "MAPPED_BUT_SETTLEMENT_NOT_PROVEN",
                                  "SETTLEMENT_RULE_EVIDENCE_CONFLICT",
                                  "EXTERNAL_SETTLEMENT_DATA_UNAVAILABLE"],
            "settlement_authority_note": (
                "coverage evidence only: decision-time bettor_venue_"
                "settlement.attest remains the authority for trading"),
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
            # (settlement rule registry) the contract's captured CURRENT
            # rules block and its parse: provenance for the settlement state
            rrow = await c.fetchrow(
                "SELECT venue, rules_published, rules_field, rules_sha256, "
                "       parse_status, evidence, parser_version, source, "
                "       observed_at FROM market_plane_rules "
                " WHERE contract_id = $1", contract_id) if await c.fetchval(
                "SELECT to_regclass('market_plane_rules') IS NOT NULL") \
                else None
    ont = reg.get("ontology") or {}
    rr = _row(rrow) if rrow is not None else None
    rev = (rr or {}).get("evidence") or {}
    settlement_evidence = {
        "state": reg.get("settlement_state"),
        "why": reg.get("settlement_why"),
        "basis": reg.get("settlement_basis"),
        "classified_at": reg.get("settlement_at"),
        "evidence": reg.get("settlement_evidence") or {},
        "provenance": None if rr is None else {
            "venue": rr.get("venue"),
            "rules_published": rr.get("rules_published"),
            "rules_field": rr.get("rules_field"),
            "rules_sha256": rr.get("rules_sha256"),
            "parse_status": rr.get("parse_status"),
            "parser_version": rr.get("parser_version"),
            "source": rr.get("source"),
            "observed_at": rr.get("observed_at"),
            "matched": rev.get("matched"),
            "settlement": rev.get("settlement"),
            "special_conditions": rev.get("special_conditions"),
            "verification_sources": rev.get("verification_sources"),
            "conflicts": rev.get("conflicts") or {}},
        "authority_note": ("coverage evidence only: decision-time "
                           "bettor_venue_settlement.attest remains the "
                           "authority for trading")}
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
                    portfolio=None, settlement_evidence=settlement_evidence)
    return dict(opp, status="OK", authority=AUTHORITY, label="RESEARCH")
