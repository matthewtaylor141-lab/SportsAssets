"""KALSHI MARKET DATA + CANONICAL CLAIMS + CLAIM-FIRST ADRIANA (SHADOW).

One bounded loop in the shared workers (KALSHI_MARKET_DATA, default on):

  every CATALOGUE_EVERY_S  the COMPLETE open sports catalogue
                           (kalshi_market_data.walk_open_sports, in a thread),
                           persisted into the market-plane registry and rules
                           (market_plane.populate.populate_kalshi, unchanged)
                           and its census kept; the game events among it.
  per pass                 milestones for the nearest game events without a
                           fixture (structured home / away / league / start),
                           books for the ESTABLISHED fixtures starting inside
                           LOOKAHEAD_S (nearest first, MAX_BOOKS_PER_PASS), the
                           PMUS moneyline each maps to (exact, fail-closed),
                           canonical claims, the best all-in route of every
                           claim at RECEIPT_QTY, and Adriana's claim-first
                           scan recorded in the migration-265 tables.
  heartbeat                kalshi_market_data -- domain KALSHI_HEALTH, with
                           the catalogue census, mapping coverage by sport /
                           family (numerator / denominator), Kalshi book
                           freshness (numerator / denominator), 429 / backoff.

MARKET DATA ONLY: GET-only Kalshi reads, read-only Polymarket evidence, and
writes to its own evidence tables (314), the registry, and Adriana's SHADOW
records. No order, cancel, credential or capital path is reachable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

from .. import canonical_claims as CC
from .. import kalshi_claims as KCL
from .. import kalshi_market_data as KMD
from ..db import get_pool, heartbeat
from .loop_contract import LOOP_DISABLED

log = logging.getLogger(__name__)
SERVICE = "kalshi_market_data"
ENV_FLAG = "KALSHI_MARKET_DATA"
INTERVAL_S = 30.0
CATALOGUE_EVERY_S = 900.0
LOOKAHEAD_S = 36 * 3600.0
LOOKBACK_S = 4 * 3600.0
MAX_MILESTONES_PER_PASS = 20
MAX_BOOKS_PER_PASS = 120
RECEIPT_QTY = 10
#: route receipts and Adriana's claim scan are RECORDED at most this often
#: (computed every pass; append-only volume stays bounded)
RECORD_EVERY_S = 300.0
PMUS_BOOK_WINDOW_S = 900.0
ROUTE_MAX_AGE_S = KMD.BOOK_SLA_S


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def _j(v):
    return json.dumps(v, default=str)


def _levels_json(levels) -> str:
    return _j([[str(p), int(q)] for p, q in levels or ()])


def _rough_start(markets) -> float | None:
    """A pre-milestone ordering key only (never identity): the earliest
    venue expiry time among the event's markets."""
    ts = [KMD._ts(m.get("expected_expiration_time") or m.get("close_time"))
          for m in markets]
    ts = [t for t in ts if t]
    return min(ts) if ts else None


async def pmus_candidates(conn, k: KMD.KalshiFixture) -> list:
    """PMUS moneylines of the same league inside +-2 h of the start, as
    structured identity rows (LONG side = team_a)."""
    lg = [p for p, c in KCL.PMUS_LEAGUE.items() if c == k.league]
    if not lg or k.start_epoch is None:
        return []
    rows = await conn.fetch(
        "SELECT market_slug, team_league, "
        "       max(team_abbr) FILTER (WHERE intent = 'ORDER_INTENT_BUY_LONG')"
        "         AS team_a, "
        "       max(team_abbr) FILTER (WHERE intent = 'ORDER_INTENT_BUY_SHORT')"
        "         AS team_b, "
        "       extract(epoch FROM min(game_start)) AS start_epoch "
        "  FROM us_premap WHERE market_slug LIKE 'aec-%' "
        "   AND team_league = ANY($1::text[]) "
        "   AND game_start BETWEEN to_timestamp($2) - interval '2 hours' "
        "                      AND to_timestamp($2) + interval '2 hours' "
        " GROUP BY 1, 2", lg, float(k.start_epoch))
    return [{"slug": r["market_slug"], "league": r["team_league"],
             "team_a": r["team_a"], "team_b": r["team_b"],
             "start_epoch": float(r["start_epoch"])
             if r["start_epoch"] is not None else None} for r in rows]


async def persist_fixture(conn, k: KMD.KalshiFixture, pm: dict | None):
    await conn.execute(
        "INSERT INTO kalshi_fixtures_current (event_ticker, series_ticker, "
        " sport, league, start_at, home_id, away_id, home_code, away_code, "
        " tie_ticker, team_tickers, outcome_kind, mapping_status, "
        " mapping_reasons, pmus_slug, pmus_mapping_status, "
        " pmus_mapping_reasons, milestone_id, updated_at) VALUES ($1,$2,$3,$4,"
        " to_timestamp($5),$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15,$16,"
        " $17::jsonb,$18, now()) ON CONFLICT (event_ticker) DO UPDATE SET "
        " sport=excluded.sport, league=excluded.league, "
        " start_at=excluded.start_at, home_id=excluded.home_id, "
        " away_id=excluded.away_id, home_code=excluded.home_code, "
        " away_code=excluded.away_code, tie_ticker=excluded.tie_ticker, "
        " team_tickers=excluded.team_tickers, "
        " outcome_kind=excluded.outcome_kind, "
        " mapping_status=excluded.mapping_status, "
        " mapping_reasons=excluded.mapping_reasons, "
        " pmus_slug=excluded.pmus_slug, "
        " pmus_mapping_status=excluded.pmus_mapping_status, "
        " pmus_mapping_reasons=excluded.pmus_mapping_reasons, "
        " milestone_id=excluded.milestone_id, updated_at=now()",
        k.event_ticker, k.series_ticker, k.sport, k.league, k.start_epoch,
        k.home_id, k.away_id, k.home_code, k.away_code, k.tie_ticker,
        list(k.team_tickers), k.outcome_kind, k.status, _j(list(k.reasons)),
        ((pm or {}).get("pmus") or {}).get("slug"),
        (pm or {}).get("status"), _j((pm or {}).get("reasons")),
        k.milestone_id)


async def persist_book(conn, ticker: str, event: str, b: dict, market: dict):
    await conn.execute(
        "INSERT INTO kalshi_books_current (ticker, event_ticker, "
        " series_ticker, yes_bids, no_bids, yes_asks, no_asks, book_basis, "
        " record_quotes, readable, error, observed_at, updated_at) VALUES "
        " ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9::jsonb,$10,"
        " $11, to_timestamp($12), now()) ON CONFLICT (ticker) DO UPDATE SET "
        " yes_bids=excluded.yes_bids, no_bids=excluded.no_bids, "
        " yes_asks=excluded.yes_asks, no_asks=excluded.no_asks, "
        " book_basis=excluded.book_basis, "
        " record_quotes=excluded.record_quotes, readable=excluded.readable, "
        " error=excluded.error, observed_at=excluded.observed_at, "
        " updated_at=now()",
        ticker, event, event.split("-")[0],
        _levels_json(b.get("yes_bids")), _levels_json(b.get("no_bids")),
        _levels_json(b.get("yes_asks")), _levels_json(b.get("no_asks")),
        b.get("basis"),
        _j(KMD.quote_agreement(market, b) if b.get("readable") else None),
        bool(b.get("readable")), b.get("error"),
        float(b.get("observed_at") or time.time()))


async def persist_claims(conn, fx: CC.Fixture, built: dict, routes: list,
                         *, now: float, record: bool = True):
    for i in [x for c in built["classes"].values() for x in c] + \
            built["refused"]:
        await conn.execute(
            "INSERT INTO canonical_claim_aliases (alias_key, event_key, "
            " claim_fingerprint, venue, market_id, side, subject, "
            " mapping_status, settlement_status, refusals, payoff, best_ask, "
            " depth, book_basis, observed_at, updated_at) VALUES ($1,$2,$3,"
            " $4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12,$13,$14,"
            " to_timestamp($15), now()) ON CONFLICT (alias_key) DO UPDATE "
            " SET claim_fingerprint=excluded.claim_fingerprint, "
            " event_key=excluded.event_key, subject=excluded.subject, "
            " mapping_status=excluded.mapping_status, "
            " settlement_status=excluded.settlement_status, "
            " refusals=excluded.refusals, payoff=excluded.payoff, "
            " best_ask=excluded.best_ask, depth=excluded.depth, "
            " book_basis=excluded.book_basis, "
            " observed_at=excluded.observed_at, updated_at=now()",
            "%s|%s|%s" % (i.venue, i.market_id, i.side), fx.event_key,
            i.fingerprint, i.venue, i.market_id, i.side, i.subject,
            i.mapping_status, i.settlement_status, _j(i.refusals),
            _j(i.vector), (min(Decimal(str(p)) for p, _q in i.asks)
                           if i.asks else None),
            sum(int(q) for _p, q in i.asks) if i.asks else 0, i.book_basis,
            i.observed_at)
    if not record:
        return
    bucket = int(now // RECORD_EVERY_S)
    for r in routes:
        await conn.execute(
            "INSERT INTO canonical_route_receipts (receipt_id, computed_at, "
            " event_key, claim_fingerprint, qty, aliases, chosen, "
            " best_single, runner_up, lost, candidates, refusal) VALUES ($1,"
            " to_timestamp($2),$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,"
            " $10::jsonb,$11::jsonb,$12) ON CONFLICT (receipt_id) DO NOTHING",
            "route-%s-%d-%d" % (r["claim_fingerprint"][:24], r["qty"],
                                bucket), now, r["event_key"],
            r["claim_fingerprint"], r["qty"], r["aliases"],
            _j(r["chosen"]), _j(r["best_single"]), _j(r["runner_up"]),
            _j(r["lost"]), _j(r["candidates"]), r["refusal"])


def coverage(fixtures: dict) -> dict:
    """Mapping coverage by sport x family (game moneylines), numerator /
    denominator, Kalshi-structured and to-PMUS separately."""
    by: dict = {}
    for k, pm in fixtures.values():
        key = "%s|%s" % (k.sport or "UNMAPPED_LEAGUE:%s" % (k.league or "?"),
                         "MONEYLINE_GAME")
        row = by.setdefault(key, {"kalshi_events": 0,
                                  "fixture_established": 0,
                                  "pmus_established": 0,
                                  "not_established_reasons": {}})
        row["kalshi_events"] += 1
        if k.status == "ESTABLISHED":
            row["fixture_established"] += 1
        for r in k.reasons:
            r0 = r.split(":")[0]
            row["not_established_reasons"][r0] = row[
                "not_established_reasons"].get(r0, 0) + 1
        if (pm or {}).get("status") == "ESTABLISHED":
            row["pmus_established"] += 1
    return by


async def claims_pass(pool, *, now: float, record: bool = True) -> dict:
    """Canonical claims over the PERSISTED evidence (canonical_claims_db, the
    same assembler Adriana's runner reads): aliases, the best all-in route
    of every claim at RECEIPT_QTY, and the claim-first structures for the
    digest. Adriana records her own scan in her runner; nothing here writes
    an arbitrage record."""
    from .. import canonical_claims_db as KCDB
    from ..agents import adriana_claims as AC
    routes_n, aliases_n, equivalences, best_routes = 0, 0, [], []
    scans = []
    async with pool.acquire() as c:
        assembled = await KCDB.assemble(c, now=now)
        for fx, built, insts in assembled:
            aliases_n += len(insts)
            fees = CC.fee_functions(at=now, sport=fx.sport)
            routes = [CC.route_claim(fx, fp, members, qty=RECEIPT_QTY,
                                     now=now, fee_by_venue=fees,
                                     max_age_s=ROUTE_MAX_AGE_S)
                      for fp, members in built["classes"].items()]
            kal = [i for i in insts if i.venue == KCL.KALSHI]
            for y in [i for i in kal if i.side == "YES"
                      and i.subject in ("HOME", "AWAY")][:1]:
                for o in [i for i in kal if i.side == "NO" and i.subject
                          not in (y.subject, "DRAW")][:1]:
                    equivalences.append(CC.equivalence_receipt(
                        fx, y, o, built["states"]))
            best_routes += [r for r in routes if r["chosen"]][:2]
            await persist_claims(c, fx, built, routes, now=now,
                                 record=record)
            routes_n += len(routes)
            scans.append(AC.scan_fixture(fx, built, now=now))
    census = AC.census_result(scans, markets_read=aliases_n, books_fresh=0,
                              skipped={})["census"]
    return {"fixtures_priced": len(scans), "aliases": aliases_n,
            "routes": routes_n, "structures": census,
            "equivalence_receipts": equivalences[:6],
            "best_route_receipts": best_routes[:4]}


async def run() -> None:
    if not enabled():
        log.info("kalshi_market_data: off by switch (%s)", ENV_FLAG)
        return LOOP_DISABLED  # off by configuration: not restarted (loop_contract)
    from ..market_plane import populate as POP
    health = KMD.KalshiHealth()
    pacer = KMD.KC._Pacer(KMD.DEFAULT_PACING_S, time.sleep, time.monotonic)
    tx = KMD.KC.GetOnlyTransport()
    last_cat, census, games = 0.0, {}, {}
    markets, fixtures, books, milestones_tried = {}, {}, {}, {}
    claims, last_record = {}, 0.0
    while True:
        try:
            now = time.time()
            pool = await get_pool()
            if now - last_cat >= CATALOGUE_EVERY_S:
                res = await asyncio.to_thread(KMD.walk_open_sports, tx,
                                              health=health)
                census = KMD.census(res)
                if res.get("markets"):
                    async with pool.acquire() as c:
                        census["registry"] = await POP.populate_kalshi(
                            c, res, now=now)
                gm = KMD.game_candidates(res.get("markets") or [])
                games = {e: ms for e, ms in gm.items()}
                markets = {m["ticker"]: m for ms in games.values()
                           for m in ms}
                last_cat = now
                del res
            # milestones for the nearest game events without a fixture
            order = sorted(games, key=lambda e: _rough_start(games[e]) or 9e18)
            todo = [e for e in order if e not in fixtures and
                    now - milestones_tried.get(e, 0) > 1800
                    and (_rough_start(games[e]) or 0) < now + LOOKAHEAD_S
                    + 12 * 3600][:MAX_MILESTONES_PER_PASS]
            for e in todo:
                milestones_tried[e] = now
                ms, err = await asyncio.to_thread(
                    KMD.read_milestone, e, tx, health=health, pacer=pacer)
                k = KMD.fixture_from(e, games[e], ms)
                pm = None
                if k.status == "ESTABLISHED":
                    async with pool.acquire() as c:
                        pm = KCL.map_pmus(k, await pmus_candidates(c, k))
                fixtures[e] = (k, pm)
                async with pool.acquire() as c:
                    await persist_fixture(c, k, pm)
            # books for ESTABLISHED fixtures inside the lookahead, nearest
            live = sorted((kp for kp in fixtures.values()
                           if kp[0].status == "ESTABLISHED"
                           and kp[0].start_epoch is not None
                           and now - LOOKBACK_S <= kp[0].start_epoch
                           <= now + LOOKAHEAD_S),
                          key=lambda kp: kp[0].start_epoch)
            tracked = [(k.event_ticker, t) for k, _pm in live
                       for t in list(k.team_tickers) +
                       ([k.tie_ticker] if k.tie_ticker else [])]
            for ev, t in tracked[:MAX_BOOKS_PER_PASS]:
                if health.blocked():
                    break
                b = await asyncio.to_thread(KMD.read_orderbook, t, tx,
                                            health=health, pacer=pacer)
                books[t] = b
                async with pool.acquire() as c:
                    await persist_book(c, t, ev, b, markets.get(t) or {})
            fresh = KMD.freshness(books, [t for _e, t in tracked], now=now)
            rec_now = now - last_record >= RECORD_EVERY_S
            claims = await claims_pass(pool, now=time.time(), record=rec_now)
            if rec_now:
                last_record = now
            # drop finished fixtures from memory (bounded)
            for e in [e for e, (k, _p) in fixtures.items()
                      if k.start_epoch and k.start_epoch < now - LOOKBACK_S]:
                fixtures.pop(e, None)
            status = "ok" if census.get("complete") and fresh["rate"] else \
                "degraded"
            await heartbeat(SERVICE, status, {
                "domain": KMD.HEALTH_DOMAIN, "health": health.digest(),
                "catalogue": census, "game_events": len(games),
                "fixtures": {"total": len(fixtures), "established": sum(
                    1 for k, _p in fixtures.values()
                    if k.status == "ESTABLISHED"),
                    "pmus_mapped": sum(1 for _k, p in fixtures.values()
                                       if (p or {}).get("status")
                                       == "ESTABLISHED")},
                "coverage": coverage(fixtures), "freshness": fresh,
                "tracked_markets": len(tracked), "claims": claims,
                "authority": "MARKET_DATA_SHADOW_NO_ORDER_AUTHORITY"})
            await asyncio.sleep(INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            log.exception("kalshi market data pass failed")
            try:
                await heartbeat(SERVICE, "error", {
                    "domain": KMD.HEALTH_DOMAIN,
                    "error": type(exc).__name__,
                    "health": health.digest()})
            except Exception:                                   # noqa: BLE001
                pass
            await asyncio.sleep(30)


def describe() -> dict:
    return {"service": SERVICE, "domain": KMD.HEALTH_DOMAIN,
            "switch": ENV_FLAG, "interval_s": INTERVAL_S,
            "catalogue_every_s": CATALOGUE_EVERY_S,
            "lookahead_s": LOOKAHEAD_S,
            "max_books_per_pass": MAX_BOOKS_PER_PASS,
            "receipt_qty": RECEIPT_QTY,
            "now": datetime.now(timezone.utc).isoformat()}
