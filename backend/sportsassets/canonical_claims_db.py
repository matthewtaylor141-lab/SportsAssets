"""THE PERSISTED VENUE EVIDENCE (KALSHI x PMUS) -> CANONICAL CLAIMS (Kalshi Canonical
Venue V1). One assembler, two readers:

  * the shared-workers loop (workers/kalshi_market_data) after it persists
    books and fixtures: aliases + best-route receipts (314 tables);
  * Adriana's own runner (agents/adriana_runner, the API process): her
    claim-first scan, recorded into her migration-265 tables -- she remains
    the only writer of arbitrage records, and the shared workers never
    import her (agents.adriana reaches the funded layers through the agent
    registry; tests/test_workers_hold_no_venue_write.py).

READ ONLY: every statement here is a SELECT. Kalshi rules evidence is the
registry's own parse (market_plane_rules, contract_id 'kalshi:'+ticker);
PMUS evidence the same table's POLYMARKET_US row; PMUS books the paper
path's recorded observations (paper_book_observations).
"""
from __future__ import annotations

import json
import time
from decimal import Decimal

from . import canonical_claims as CC
from . import kalshi_claims as KCL
from . import kalshi_market_data as KMD

VERSION = "CANONICAL_CLAIMS_DB_V1"
WINDOW_BEHIND_S = 4 * 3600.0
WINDOW_AHEAD_S = 36 * 3600.0
PMUS_BOOK_WINDOW_S = 900.0
MAX_FIXTURES = 80


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _lv(raw) -> tuple:
    out = []
    for x in _j(raw) or []:
        try:
            out.append((Decimal(str(x[0])), int(x[1])))
        except Exception:                                     # noqa: BLE001
            continue
    return tuple(out)


def _pmus_levels(raw) -> list:
    out = []
    for x in _j(raw) or []:
        try:
            px = x["px"]["value"] if isinstance(x.get("px"), dict) \
                else x.get("px")
            out.append((Decimal(str(px)), int(Decimal(str(x["qty"])))))
        except Exception:                                     # noqa: BLE001
            continue
    return out


async def _has(conn, t: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t))


async def fixtures(conn, *, now: float) -> list:
    if not await _has(conn, "kalshi_fixtures_current"):
        return []
    rows = await conn.fetch(
        "SELECT * FROM kalshi_fixtures_current "
        " WHERE mapping_status = 'ESTABLISHED' AND start_at BETWEEN "
        "       to_timestamp($1) AND to_timestamp($2) "
        " ORDER BY start_at LIMIT $3",
        now - WINDOW_BEHIND_S, now + WINDOW_AHEAD_S, MAX_FIXTURES)
    out = []
    for r in rows:
        k = KMD.KalshiFixture(
            event_ticker=r["event_ticker"], series_ticker=r["series_ticker"],
            sport=r["sport"], league=r["league"],
            start_epoch=r["start_at"].timestamp() if r["start_at"] else None,
            home_id=r["home_id"], away_id=r["away_id"],
            home_code=r["home_code"], away_code=r["away_code"],
            tie_ticker=r["tie_ticker"],
            team_tickers=tuple(r["team_tickers"] or ()),
            outcome_kind=r["outcome_kind"], status=r["mapping_status"],
            reasons=tuple(_j(r["mapping_reasons"]) or ()),
            milestone_id=r["milestone_id"])
        out.append((k, r["pmus_slug"] if r["pmus_mapping_status"]
                    == "ESTABLISHED" else None))
    return out


async def kalshi_books(conn, tickers: list) -> dict:
    if not tickers or not await _has(conn, "kalshi_books_current"):
        return {}
    out = {}
    for r in await conn.fetch(
            "SELECT ticker, yes_asks, no_asks, readable, observed_at "
            "  FROM kalshi_books_current WHERE ticker = ANY($1::text[])",
            list(tickers)):
        out[r["ticker"]] = {"yes_asks": _lv(r["yes_asks"]),
                            "no_asks": _lv(r["no_asks"]),
                            "readable": bool(r["readable"]),
                            "observed_at": r["observed_at"].timestamp()}
    return out


async def rules_evidence(conn, contract_ids: list) -> dict:
    if not contract_ids:
        return {}
    out = {}
    for r in await conn.fetch(
            "SELECT contract_id, evidence, rules_sha256 FROM "
            " market_plane_rules WHERE contract_id = ANY($1::text[])",
            list(contract_ids)):
        ev = dict(_j(r["evidence"]) or {})
        # the rules fingerprint the claim is built (and certified) from
        ev["rules_sha256"] = r["rules_sha256"]
        out[r["contract_id"]] = ev
    return out


async def pmus_identity(conn, slug: str) -> dict | None:
    r = await conn.fetchrow(
        "SELECT market_slug, team_league, "
        "  max(team_abbr) FILTER (WHERE intent = 'ORDER_INTENT_BUY_LONG') a, "
        "  max(team_abbr) FILTER (WHERE intent = 'ORDER_INTENT_BUY_SHORT') b,"
        "  extract(epoch FROM min(game_start)) s "
        "  FROM us_premap WHERE market_slug = $1 GROUP BY 1, 2", slug)
    if r is None:
        return None
    return {"slug": r["market_slug"], "league": r["team_league"],
            "team_a": r["a"], "team_b": r["b"],
            "start_epoch": float(r["s"]) if r["s"] is not None else None}


async def pmus_book(conn, slug: str, *, now: float) -> dict | None:
    r = await conn.fetchrow(
        "SELECT bids, offers, extract(epoch FROM observed_at) AS at "
        "  FROM paper_book_observations WHERE us_market_slug = $1 "
        "   AND error IS NULL AND observed_at > to_timestamp($2) "
        " ORDER BY observed_at DESC LIMIT 1", slug, now - PMUS_BOOK_WINDOW_S)
    if r is None:
        return None
    return {"bids": _pmus_levels(r["bids"]),
            "offers": _pmus_levels(r["offers"]),
            "observed_at": float(r["at"])}


async def kalshi_fee_terms(conn, series_ticker: str, event_ticker: str, *,
                           now: float) -> dict | None:
    """The published Kalshi fee terms in force for this event at `now`
    (kalshi_fees.effective_terms over migration-315 kalshi_fee_terms), or
    None (the Kalshi aliases are then ineligible)."""
    from . import kalshi_fees as KF
    if not await _has(conn, "kalshi_fee_terms"):
        return None
    rows = [dict(r) for r in await conn.fetch(
        "SELECT term_id, kind, series_ticker, event_ticker, fee_type, "
        "       fee_multiplier, extract(epoch FROM scheduled_ts) sts, "
        "       extract(epoch FROM first_observed_at) seen "
        "  FROM kalshi_fee_terms WHERE series_ticker = $1 "
        "   AND (event_ticker IS NULL OR event_ticker = $2)",
        series_ticker, event_ticker)]
    series = [{"id": r["term_id"], "fee_type": r["fee_type"],
               "fee_multiplier": r["fee_multiplier"],
               "scheduled_ts": r["sts"], "series_ticker": series_ticker}
              for r in rows if r["kind"] == "SERIES_CHANGE"]
    events = [{"id": r["term_id"], "fee_type_override": r["fee_type"],
               "fee_multiplier_override": r["fee_multiplier"],
               "scheduled_ts": r["sts"], "event_ticker": r["event_ticker"],
               "series_ticker": series_ticker}
              for r in rows if r["kind"] == "EVENT_OVERRIDE"]
    obs = [r for r in rows if r["kind"] == "SERIES_OBSERVED"]
    seen = max(obs, key=lambda r: r["seen"]) if obs else None
    return KF.effective_terms(
        series_ticker=series_ticker, event_ticker=event_ticker, at=now,
        series_changes=series, event_changes=events,
        observed=None if seen is None else {
            "fee_type": seen["fee_type"],
            "fee_multiplier": seen["fee_multiplier"],
            "first_observed_at": seen["seen"]})


async def assemble(conn, *, now: float | None = None) -> list:
    """[(Fixture, built, instruments)] for every ESTABLISHED Kalshi fixture
    in the window that has at least one readable Kalshi book."""
    now = float(now if now is not None else time.time())
    out = []
    for k, slug in await fixtures(conn, now=now):
        tickers = list(k.team_tickers) + ([k.tie_ticker] if k.tie_ticker
                                          else [])
        books = await kalshi_books(conn, tickers)
        if not any(b.get("readable") for b in books.values()):
            continue
        ev = await rules_evidence(conn, ["kalshi:%s" % t for t in tickers]
                                  + ([slug] if slug else []))
        terms = await kalshi_fee_terms(conn, k.series_ticker,
                                       k.event_ticker, now=now)
        insts = KCL.kalshi_instruments(
            k, {}, books, {t: ev.get("kalshi:%s" % t) for t in tickers},
            fee_terms=terms)
        if slug:
            ident = await pmus_identity(conn, slug)
            if ident is not None:
                insts += KCL.pmus_instruments(
                    k, ident, evidence=ev.get(slug),
                    book=await pmus_book(conn, slug, now=now))
        fx = KCL.fixture_of(k)
        if fx is None:
            continue
        out.append((fx, CC.build_claims(fx, insts), insts))
    return out


async def claims_census(conn, *, now: float | None = None) -> dict:
    """Adriana's claim-first census over the persisted evidence, shaped for
    adriana.record (pure engine + pure claim layer; no write here)."""
    from .agents import adriana_claims as AC
    live = now is None
    now = float(now if now is not None else time.time())
    scans, aliases, fresh = [], 0, 0
    from .redteam import settlement as RTS
    assembled = await assemble(conn, now=now)
    if live:
        # evaluate as of the moment the books were READ: a book the workers
        # persisted while this pass was reading is not "in the future"
        # (production 088af82: 21 BOOK_TIME_IN_FUTURE of 139). A venue clock
        # ahead of ours still is; staleness only gets stricter.
        now = max(now, time.time())
    for fx, built, insts in assembled:
        # the settlement certificates, read only: an alias whose rules
        # fingerprint is not the certified one is no leg (red team)
        built, _cert = await RTS.apply(conn, built)
        aliases += len(insts)
        fresh += sum(1 for i in insts if i.observed_at is not None
                     and now - float(i.observed_at) <= KMD.BOOK_SLA_S)
        scans.append(AC.scan_fixture(fx, built, now=now))
    return AC.census_result(scans, markets_read=aliases, books_fresh=fresh,
                            skipped={})
