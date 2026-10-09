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
from . import kalshi_contract_terms as KCT
from . import kalshi_market_data as KMD

VERSION = "CANONICAL_CLAIMS_DB_V1"
WINDOW_BEHIND_S = 4 * 3600.0
WINDOW_AHEAD_S = 36 * 3600.0
PMUS_BOOK_WINDOW_S = 900.0
#: the claim scan reads at most this many ESTABLISHED Kalshi fixtures per
#: pass (Adriana's runner is in the API process: a 45 s phase bound inside a
#: 120 s pass bound; production 2026-10-09, 284 passes in 24 h: p50 3.0 s,
#: p95 9.9 s, max 72.9 s -- research-sql 37888029447 C). UNCHANGED. Since
#: RC6 the cap is spent cross-venue (PMUS-mapped) fixtures first, then those
#: with a readable Kalshi book, then by start time, and what it cuts is
#: counted and named in the scan's `scope`, never dropped silently.
MAX_FIXTURES = 80
#: how many identifiers each scope gap names (all are counted)
SCOPE_NAMED_MAX = 12
#: the order the cap is spent in, stated in every scope record
FIXTURE_ORDER = ("cross-venue (PMUS-mapped) first, then a readable Kalshi "
                 "book, then start time")


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


def new_scope() -> dict:
    """(RC6) What the claim scan's fixture read covered: every ESTABLISHED
    Kalshi fixture in the window, how many the MAX_FIXTURES cap read and
    cut (cross-venue among them), and every later skip, counted and named.
    Filled by fixtures() and assemble()."""
    return {"max_fixtures": MAX_FIXTURES, "order": FIXTURE_ORDER,
            "window_s": [-WINDOW_BEHIND_S, WINDOW_AHEAD_S],
            "in_window": 0, "in_window_cross_venue": 0,
            "read": 0, "read_cross_venue": 0,
            "cut_by_cap": 0, "cut_by_cap_cross_venue": 0,
            "cut_by_cap_readable": 0, "cut_by_cap_named": [],
            "no_readable_kalshi_book": 0, "no_readable_kalshi_book_named": [],
            "not_a_fixture": 0, "not_a_fixture_named": [],
            "pmus_identity_missing": 0, "pmus_identity_missing_named": [],
            "scanned": 0, "scanned_cross_venue": 0}


def _name(scope: dict, key: str, ident) -> None:
    scope[key] += 1
    named = scope[key + "_named"]
    if len(named) < SCOPE_NAMED_MAX:
        named.append(ident)


async def fixtures(conn, *, now: float, scope: dict | None = None) -> list:
    """[(KalshiFixture, mapped PMUS slug | None)] for at most MAX_FIXTURES
    ESTABLISHED fixtures in the window, cross-venue first (FIXTURE_ORDER).
    Every fixture in the window is read for the count; the ones past the cap
    are counted and named in `scope` (cut_by_cap*)."""
    scope = scope if scope is not None else new_scope()
    if not await _has(conn, "kalshi_fixtures_current"):
        scope["source"] = "kalshi_fixtures_current ABSENT"
        return []
    readable = ("EXISTS (SELECT 1 FROM kalshi_books_current b "
                "         WHERE b.readable AND (b.ticker = ANY(f.team_tickers)"
                "               OR b.ticker = f.tie_ticker))"
                if await _has(conn, "kalshi_books_current") else "false")
    rows = await conn.fetch(
        "SELECT f.*, coalesce(f.pmus_mapping_status = 'ESTABLISHED' "
        "       AND f.pmus_slug IS NOT NULL, false) AS _cross_venue, "
        "       coalesce(" + readable + ", false) AS _readable "
        "  FROM kalshi_fixtures_current f "
        " WHERE f.mapping_status = 'ESTABLISHED' AND f.start_at BETWEEN "
        "       to_timestamp($1) AND to_timestamp($2) "
        " ORDER BY _cross_venue DESC, _readable DESC, f.start_at, "
        "          f.event_ticker",
        now - WINDOW_BEHIND_S, now + WINDOW_AHEAD_S)
    kept, cut = rows[:MAX_FIXTURES], rows[MAX_FIXTURES:]
    scope.update(
        source="kalshi_fixtures_current", in_window=len(rows),
        in_window_cross_venue=sum(1 for r in rows if r["_cross_venue"]),
        read=len(kept),
        read_cross_venue=sum(1 for r in kept if r["_cross_venue"]),
        cut_by_cap=len(cut),
        cut_by_cap_cross_venue=sum(1 for r in cut if r["_cross_venue"]),
        cut_by_cap_readable=sum(1 for r in cut if r["_readable"]),
        cut_by_cap_named=[r["event_ticker"] for r in cut][:SCOPE_NAMED_MAX])
    out = []
    for r in kept:
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
            "SELECT contract_id, evidence, rules_sha256, rules_text, "
            "       rules_secondary FROM market_plane_rules "
            " WHERE contract_id = ANY($1::text[])", list(contract_ids)):
        ev = dict(_j(r["evidence"]) or {})
        # the rules fingerprint the claim is built (and certified) from
        ev["rules_sha256"] = r["rules_sha256"]
        ev["_rules_primary"] = r["rules_text"]
        ev["_rules_secondary"] = r["rules_secondary"]
        out[r["contract_id"]] = ev
    return out


#: a rulebook hash older than this is not a live verification
CONTRACT_TERMS_MAX_AGE_S = 48 * 3600.0


async def contract_terms_state(conn, *, now: float) -> dict:
    """The Kalshi worker's record of each game series' rulebook and the live
    hash of its PDF (ingestion_state 'kalshi_contract_terms')."""
    if not await _has(conn, "ingestion_state"):
        return {}
    st = _j(await conn.fetchval("SELECT value FROM ingestion_state WHERE "
                                " key = $1", KCT.STATE_KEY)) or {}
    books = {}
    for name, r in (st.get("rulebooks") or {}).items():
        fresh = (r.get("status") == "OK" and r.get("fetched_at") is not None
                 and now - float(r["fetched_at"]) <= CONTRACT_TERMS_MAX_AGE_S)
        books[name] = r.get("sha256") if fresh else None
    return {"series": dict(st.get("series") or {}), "observed": books}


def bind_contract_terms(k, evidence: dict, state: dict) -> dict:
    """{ticker: evidence} with the venue's explicit clauses bound
    (kalshi_contract_terms): market text first, then the verified rulebook."""
    rb = (state.get("series") or {}).get(k.series_ticker)
    obs = (state.get("observed") or {}).get(rb) if rb else None
    out = {}
    for t, ev in evidence.items():
        if ev is None:
            out[t] = None
            continue
        out[t] = _strip(KCT.bind(
            ev, rules_primary=ev.get("_rules_primary"),
            rules_secondary=ev.get("_rules_secondary"), rulebook=rb,
            observed_sha256=obs, has_tie_strike=bool(k.tie_ticker)))
    return out


def _strip(ev):
    """Evidence without the raw rules text read beside it."""
    if ev is None:
        return None
    return {k: v for k, v in ev.items() if not k.startswith("_rules_")}


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


async def assemble(conn, *, now: float | None = None,
                   scope: dict | None = None) -> list:
    """[(Fixture, built, instruments)] for every ESTABLISHED Kalshi fixture
    the read covers (fixtures(): at most MAX_FIXTURES, cross-venue first)
    that has at least one readable Kalshi book. (RC6) Every fixture it does
    not price is counted and named in `scope` by why: cut by the cap, no
    readable Kalshi book, not a fixture the claim layer models, or a mapped
    PMUS slug with no premap identity (the PMUS leg then is not read)."""
    now = float(now if now is not None else time.time())
    scope = scope if scope is not None else new_scope()
    out = []
    cterms = await contract_terms_state(conn, now=now)
    for k, slug in await fixtures(conn, now=now, scope=scope):
        tickers = list(k.team_tickers) + ([k.tie_ticker] if k.tie_ticker
                                          else [])
        books = await kalshi_books(conn, tickers)
        if not any(b.get("readable") for b in books.values()):
            _name(scope, "no_readable_kalshi_book", k.event_ticker)
            continue
        ev = await rules_evidence(conn, ["kalshi:%s" % t for t in tickers]
                                  + ([slug] if slug else []))
        terms = await kalshi_fee_terms(conn, k.series_ticker,
                                       k.event_ticker, now=now)
        insts = KCL.kalshi_instruments(
            k, {}, books, bind_contract_terms(
                k, {t: ev.get("kalshi:%s" % t) for t in tickers}, cterms),
            fee_terms=terms)
        cross = False
        if slug:
            ident = await pmus_identity(conn, slug)
            if ident is not None:
                cross = True
                insts += KCL.pmus_instruments(
                    k, ident, evidence=_strip(ev.get(slug)),
                    book=await pmus_book(conn, slug, now=now))
            else:
                _name(scope, "pmus_identity_missing", slug)
        fx = KCL.fixture_of(k)
        if fx is None:
            _name(scope, "not_a_fixture", k.event_ticker)
            continue
        scope["scanned"] += 1
        scope["scanned_cross_venue"] += int(cross)
        out.append((fx, CC.build_claims(fx, insts), insts))
    return out


def book_census(insts, *, now: float, sla_s: float = KMD.BOOK_SLA_S) -> dict:
    """(RC6) Per venue: aliases read, with a book, with a book inside the
    engine's own age bound -- and the PMUS markets mapped to a Kalshi
    fixture that have NO recorded book in the window, by name (the
    cross-venue scan's missing leg is a named gap, never a silent one)."""
    out: dict = {}
    for i in insts:
        v = out.setdefault(i.venue, {"aliases": 0, "with_book": 0,
                                     "fresh": 0, "markets_without_book": []})
        v["aliases"] += 1
        if i.observed_at is None:
            if i.market_id not in v["markets_without_book"]:
                v["markets_without_book"].append(i.market_id)
            continue
        v["with_book"] += 1
        if now - float(i.observed_at) <= sla_s:
            v["fresh"] += 1
    for v in out.values():
        v["markets_without_book_n"] = len(v["markets_without_book"])
        v["markets_without_book"] = v["markets_without_book"][:12]
    return out


def merge_book_census(parts) -> dict:
    out: dict = {}
    for p in parts:
        for venue, v in p.items():
            o = out.setdefault(venue, {"aliases": 0, "with_book": 0,
                                       "fresh": 0, "markets_without_book": [],
                                       "markets_without_book_n": 0})
            for k in ("aliases", "with_book", "fresh",
                      "markets_without_book_n"):
                o[k] += v.get(k, 0)
            room = 12 - len(o["markets_without_book"])
            if room > 0:
                o["markets_without_book"] += v["markets_without_book"][:room]
    return out


#: what each venue's book is read from in this scan (venue support, named)
BOOK_SOURCE = {"KALSHI": "kalshi_books_current (the WebSocket runtime, REST "
                         "bootstrap / recovery)",
               "POLYMARKET_US": "paper_book_observations (the paper path's "
                                "recorded reads, newest within %ds)"
                                % int(PMUS_BOOK_WINDOW_S)}


def venue_support(books: dict) -> dict:
    out = {}
    for venue, src in BOOK_SOURCE.items():
        v = books.get(venue) or {}
        n = v.get("aliases", 0)
        out[venue] = {"status": "SUPPORTED" if v.get("with_book") else
                      "UNAVAILABLE",
                      "why": None if v.get("with_book") else (
                          "NO_ALIAS_OF_THIS_VENUE_IN_THE_SCAN" if not n else
                          "NO_RECORDED_BOOK_FOR_ANY_ALIAS_IN_THE_WINDOW"),
                      "source": src, "aliases": n,
                      "with_book": v.get("with_book", 0),
                      "fresh": v.get("fresh", 0),
                      "markets_without_book_n": v.get(
                          "markets_without_book_n", 0)}
    return out


async def claims_census(conn, *, now: float | None = None) -> dict:
    """Adriana's claim-first census over the persisted evidence, shaped for
    adriana.record (pure engine + pure claim layer; no write here)."""
    from .agents import adriana_claims as AC
    live = now is None
    now = float(now if now is not None else time.time())
    scans, aliases, fresh = [], 0, 0
    vts, bks = [], []
    from .redteam import settlement as RTS
    scope = new_scope()
    assembled = await assemble(conn, now=now, scope=scope)
    if live:
        # evaluate as of the moment the books were READ: a book the workers
        # persisted while this pass was reading is not "in the future"
        # (production 088af82: 21 BOOK_TIME_IN_FUTURE of 139). A venue clock
        # ahead of ours still is; staleness only gets stricter.
        now = max(now, time.time())
    todo = []
    for fx, built, insts in assembled:
        # the void terms of every alias READ (before the certificates strip
        # any: an alias is read whether or not it may be a leg)
        vts.append(AC.alias_void_terms(insts, built["states"]))
        bks.append(book_census(insts, now=now))
        # the settlement certificates, read only: an alias whose rules
        # fingerprint is not the certified one is no leg (red team)
        built, _cert = await RTS.apply(conn, built)
        aliases += len(insts)
        fresh += sum(1 for i in insts if i.observed_at is not None
                     and now - float(i.observed_at) <= KMD.BOOK_SLA_S)
        todo.append((fx, built))
    # (RC6) the engine runs (complementary pairs and the near complements'
    # labelled conditional economics) are pure CPU: off the event loop of
    # the process that reads them (Adriana's runner is in the API process),
    # in one worker thread, results identical
    import asyncio
    scans = await asyncio.to_thread(
        lambda: [AC.scan_fixture(fx, built, now=now) for fx, built in todo])
    books = merge_book_census(bks)
    return AC.census_result(scans, markets_read=aliases, books_fresh=fresh,
                            skipped={}, void_terms=AC.merge_void_terms(vts),
                            book_sources=books, venues=venue_support(books),
                            scope=scope)
