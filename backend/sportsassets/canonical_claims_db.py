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
path's recorded observations (paper_book_observations) -- and, for the
worker's ROUTES only (rc6.3 route-book, `route_books`), a judged book read
at routing time: the recorded read while it is fresh, else a bounded
on-demand GET through the reader the worker hands in (its keyless, paced
public book read), each with its own measured age or a named refusal.
"""
from __future__ import annotations

import asyncio
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
        "SELECT bids, offers, extract(epoch FROM observed_at) AS at, "
        "       market_state, source "
        "  FROM paper_book_observations WHERE us_market_slug = $1 "
        "   AND error IS NULL AND observed_at > to_timestamp($2) "
        " ORDER BY observed_at DESC LIMIT 1", slug, now - PMUS_BOOK_WINDOW_S)
    if r is None:
        return None
    # (rc6.3 route-book) the read's own state and writer ride along for the
    # route book's judge; pmus_instruments reads bids / offers / observed_at
    return {"bids": _pmus_levels(r["bids"]),
            "offers": _pmus_levels(r["offers"]),
            "observed_at": float(r["at"]),
            "state": r["market_state"], "source": r["source"]}


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


# ── (rc6.3 route-book) THE PMUS ROUTE BOOK, READ AT ROUTING TIME ─────────
#
# THE DEFECT (production, research-sql 37998929382, 2026-10-09 22:23Z). The
# worker's route receipts costed every PMUS alias on `pmus_book` above: the
# paper runtime's newest recorded REST read within 900 s, then held to the
# 30 s route bound. Of the 23 PMUS moneylines mapped to Kalshi fixtures in
# the claim window, 0 had a paper read within 30 s, 1 within 300 s and 18
# none in 24 h; PMUS route candidates in 24 h: NO_BOOK 80, STALE_BOOK 50,
# eligible 0. The paper runtime reads what IT decides on, not what routing
# compares. The dedicated market plane holds the PMX books in its own
# process and persists only PRIORITY_PMX_BOOKS tops (best bid / offer, no
# sizes, once per snapshot, priority members only -- paper_pmx_books names
# why that is no consumer source); no table holds a current PMUS book.
# (research-sql 38006965666, 2026-10-09 23:58Z: the newest tops event held
# 5 of the 23 mapped slugs, receipts 12.9-15.4 s old, no sizes: a top with
# no quantity cannot cost RECEIPT_QTY, so it is not an executable book.)
#
# THE RULE (`route_books`, the worker's claims pass only). For every PMUS
# alias that IS a route candidate (a member of a certified claim class --
# an alias refused before routing is never read for), ONE book per market
# (its YES at the offers, its NO at 1 - bid: kalshi_claims.pmus_asks):
#   1. the market's newest book (`_newest_book` by OUR receipt instant; at
#      one instant the venue's word about the market first): the recorded
#      paper read, this process's last accepted on-demand read and its last
#      on-demand read that was the venue's word about the market (not open,
#      crossed). An open book is used as is while it is inside the route
#      bound by more than ROUTE_BOOK_LEAD_S (no request); a word inside the
#      bound refuses the alias by its name, unread;
#   2. otherwise a BOUNDED ON-DEMAND READ through the injected reader --
#      in production the workers' existing paced KEYLESS retail client
#      (workers.kalshi_market_data.public_book_read_blocking ->
#      institutional_same_book.retail_book_read: no key, GET
#      /v1/markets/{slug}/book only, venue_request_gate's write lock, 429
#      cooldown, hard gate and venue_pace; deferred by name, nothing sent,
#      while the venue's hold or cooldown is in force:
#      PMUS_ROUTE_BOOK_READ_DEFERRED_VENUE_HOLD). At most ROUTE_BOOK_MAX_READS
#      reads a recording pass (the worker records receipts every 300 s; a
#      non-recording pass has no read budget), each under its own
#      deadline, all inside ROUTE_BOOK_PASS_BUDGET_S. THE READ ORDER
#      (`read_order_key`; independent review 1 of a9f9f54c: refused reads
#      were never remembered, so the same refusing markets sorted first on
#      every pass and spent the whole budget -- open markets later in the
#      order were never read): cross-venue classes first; then the LEAST
#      RECENTLY ATTEMPTED -- every read SENT is remembered per market
#      (`_attempt_put`, bounded), accepted, refused or failed, so a market
#      just read goes behind every market not yet tried and an accepted
#      book forgotten after PMUS_BOOK_WINDOW_S keeps its place; a read
#      deferred with nothing sent keeps its place -- then the soonest
#      start. A market held as not open on our own word (2a) joins that
#      order at the END of its hold (`read_after`): read again LAST, behind
#      every market waiting since before then (a word only the paper
#      runtime recorded: 2a). Every cross-venue market that may be
#      read therefore gets a read within ceil(N / ROUTE_BOOK_MAX_READS)
#      recording passes of joining the order (N the cross-venue markets
#      that may be read; while each pass makes its ROUTE_BOOK_MAX_READS
#      reads), whatever the others answer; a market of a single-venue
#      class is read from what the cross-venue ones leave (unchanged);
#   2a. a market the venue says is NOT OPEN (the newest book of it, ours or
#      the paper runtime's, states it) is not read again for
#      ROUTE_BOOK_NOT_OPEN_HOLD_S, or ROUTE_BOOK_ENDED_HOLD_S when that
#      state says it has ENDED (rc6.2 p-freshness's RETRY_NOT_OPEN_S /
#      RETRY_ENDED_S over the same ENDED_STATES), and is then read last
#      (2. above). Inside the route bound it is refused
#      PMUS_ROUTE_BOOK_MARKET_NOT_OPEN, past it while held
#      PMUS_ROUTE_BOOK_HELD_VENUE_SAID_NOT_OPEN (the read's own age and
#      the hold left ride on the receipt). A newer book of the market
#      releases the hold at once. A word only the PAPER RUNTIME recorded is
#      seen only while `pmus_book` returns it (PMUS_BOOK_WINDOW_S, 900 s
#      from its receipt, the same length as the not-open hold): such an
#      ENDED word therefore holds the market until then, not for
#      ROUTE_BOOK_ENDED_HOLD_S (the hold_left_s on the receipt counts that
#      word's own clock and so can overstate), and a market never read on
#      demand rejoins the order at its release as never sent (first, not
#      last) -- bounded: one read, after which our own word governs, the
#      ENDED hold included;
#   2b. THE NEWEST WORD GOVERNS IN THE PASS THAT READS IT (independent
#      review 2 of b5290832: the read branch fell back to the older open
#      book whenever that book was still inside the route bound, also when
#      the read just made answered CLOSED, SUSPENDED or crossed, so the
#      alias was costed and eligible on a market the venue had just called
#      not open). A read that is the venue's word about the market (not
#      open, crossed) with OUR receipt instant at least as new as the book
#      it would replace (`said_newer`) refuses the alias by that name in
#      that very pass -- the older open book is never costed over it, and
#      the receipt's book_detail is the read's -- exactly as `_newest_book`
#      keeps it on every pass after. The older book still holds only when
#      the read said nothing newer about the market: deferred (nothing
#      sent), failed or timed out, another market's book, no receipt
#      instant (the production reader stamps every answer), or a receipt
#      older than that book (the newer book governs, as on every pass);
#   3. every book used is JUDGED (`judge_route_book`) at the read-time
#      clock: it is this alias's market (a payload naming another slug is
#      MISMATCH), it has our receipt instant, its own state does not say
#      the market is not open, it is not crossed, and its OWN measured age
#      (now - OUR receipt instant, the repository's book-age rule; a
#      recorded read keeps its original receipt -- nothing is made fresher
#      than it is; the venue's transactTime rides along in the receipt's
#      book_detail, never judged: a quiet book's last venue instant can be
#      old while the book itself is current) is
#      inside the route bound. A refused or absent book makes the alias a
#      candidate WITH NO BOOK and the refusal BY NAME on the receipt
#      (canonical_claims.route_claim): never costed, alone or in a split.
# Venue isolation holds: only POLYMARKET_US aliases are read for, the
# retail reader refuses anything but a lower-case retail slug before any
# request (a Kalshi ticker is SLUG_REFUSED), Kalshi books come from
# kalshi_books_current only, and nothing here writes. Settlement and
# contract identity are untouched: only aliases already grouped by an
# identical payoff fingerprint, with a holding settlement certificate, are
# routed together (no equivalence is assumed). Receipts stay SHADOW,
# production_effect NONE (migration 314 CHECKs). Adriana's claim scan
# (claims_census, the API process) is NOT changed: it still reads
# `pmus_book`, exactly as before.

ROUTE_BOOK_VERSION = "PMUS_ROUTE_BOOK_V1"
#: on-demand PMUS book reads one RECORDING pass may make (<= 6 / 300 s).
#: Small on purpose: the keyless public gateway already answers the same
#: process's same-book probe with 429s (production 2026-10-09, research-sql
#: 38006965666 P9: 10 of 735 retail reads in the 23Z hour, 120 of 192 in
#: the 19Z hour), and a 429 arms the process-wide cooldown every normal
#: read in the shared workers then defers under. 6 / 300 s adds ~72 reads
#: an hour to the probe's ~735.
ROUTE_BOOK_MAX_READS = 6
#: each read's own deadline (handed to the venue gate with the read)
ROUTE_BOOK_READ_DEADLINE_S = 3.0
#: the reads of one pass together stop being started after this long
ROUTE_BOOK_PASS_BUDGET_S = 10.0
#: a book this close to the route bound is re-read when the budget allows
ROUTE_BOOK_LEAD_S = 10.0
#: the accepted on-demand reads kept (newest per market), bounded
ROUTE_BOOK_MEMO_MAX = 256
#: (review 1) the markets whose last on-demand read is remembered (when
#: it was sent and what it answered), bounded: more than MAX_FIXTURES (80)
#: routed markets, so no market in the route set is forgotten
ROUTE_BOOK_ATTEMPTS_MAX = 512
#: (review 1) a market the venue says is not open is not read again for
#: this long (active_refresh.RETRY_NOT_OPEN_S; a test pins it): CLOSED,
#: the match-and-close auction, halted, suspended, pre-open can open again
ROUTE_BOOK_NOT_OPEN_HOLD_S = 900.0
#: ... and for this long when its state says it has ENDED (ENDED_STATES;
#: active_refresh.RETRY_ENDED_S, a test pins it)
ROUTE_BOOK_ENDED_HOLD_S = 3600.0
#: how many refused markets the census names (all are counted)
ROUTE_BOOK_NAMED_MAX = 12
SRC_PAPER = "PAPER_BOOK_OBSERVATION"
SRC_READ = "PMUS_ON_DEMAND_READ"

#: the route book's refusals, by name (refusal_taxonomy_table)
R_PMUS_ROUTE_BOOK_STALE = "PMUS_ROUTE_BOOK_OLDER_THAN_THE_ROUTE_BOUND"
R_PMUS_ROUTE_BOOK_MISMATCH = "PMUS_ROUTE_BOOK_IS_NOT_THE_ALIAS_MARKET"
R_PMUS_ROUTE_BOOK_NOT_OPEN = "PMUS_ROUTE_BOOK_MARKET_NOT_OPEN"
R_PMUS_ROUTE_BOOK_CROSSED = "PMUS_ROUTE_BOOK_CROSSED"
R_PMUS_ROUTE_BOOK_NO_RECEIPT = "PMUS_ROUTE_BOOK_HAS_NO_RECEIPT_INSTANT"
R_PMUS_ROUTE_BOOK_READ_FAILED = "PMUS_ROUTE_BOOK_READ_FAILED"
#: nothing was sent: the venue's hold or the 429 cooldown was in force (the
#: reader's own pre-read gate), or the venue gate refused the read before
#: dispatch because the cooldown outlasts the read's deadline
R_PMUS_ROUTE_BOOK_READ_DEFERRED = "PMUS_ROUTE_BOOK_READ_DEFERRED_VENUE_HOLD"
R_PMUS_ROUTE_BOOK_NOT_READ = "PMUS_ROUTE_BOOK_NOT_READ_PASS_BUDGET_SPENT"
#: (review 1) the venue's newest word says the market is not open, that word
#: is older than the route bound and younger than the hold: not read again
#: yet (ROUTE_BOOK_NOT_OPEN_HOLD_S / ROUTE_BOOK_ENDED_HOLD_S)
R_PMUS_ROUTE_BOOK_HELD_NOT_OPEN = "PMUS_ROUTE_BOOK_HELD_VENUE_SAID_NOT_OPEN"
#: the reader's error words that mean "deferred, nothing sent": the
#: worker's pre-read gate (workers.kalshi_market_data.ROUTE_READ_DEFERRED,
#: a test pins the equality) and the venue gate's refusal before dispatch
#: (venue_request_gate.VenueGateRefusal, as retail_book_read names it) --
#: and (review 1: a deferral keeps the market's place in the read order, so
#: it must never be taken for a read that was sent) that gate's own
#: refusal reasons, which name a refusal before dispatch wherever a caller
#: records the reason instead of the type (production 2026-10-10 03:01Z,
#: research-sql 38019095990 R4: the keyless probe's errors in 6 h include
#: 289 VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE and 34
#: DECISION_DEADLINE_PASSED_BEFORE_DISPATCH); restated, a test pins them
READ_DEFERRED_ERRORS = frozenset({
    "VENUE_HOLD_IN_FORCE", "VenueGateRefusal",
    "VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE",
    "DECISION_DEADLINE_PASSED_BEFORE_DISPATCH",
    "VENUE_COOLDOWN_EXCEEDS_THE_UNDEADLINED_WAIT_CAP"})
#: refusals that are the venue's word about the MARKET: inside the bound
#: such a book is not re-read (the budget goes to markets that can price),
#: whether the paper runtime recorded it or our on-demand read answered it
#: (review 1: `_attempt_put` keeps such a read as the market's newest book;
#: review 2: in the pass that reads it too, `said_newer`); a not-open word
#: is also held past the bound (`hold_left_s`)
_MARKET_REFUSALS = frozenset({R_PMUS_ROUTE_BOOK_NOT_OPEN,
                              R_PMUS_ROUTE_BOOK_CROSSED})

_ROUTE_BOOK_MEMO: dict = {}
#: (review 1) {slug: the market's last on-demand read attempt}: `sent_at`
#: (our clock when the last read that was SENT came back; the read
#: order's least-recently-attempted key), `why` (its refusal, None when
#: accepted), `state`, `book` (kept only for the venue's word about the
#: market: not open / crossed), `deferred_at` (the last read deferred with
#: nothing sent; it moves nothing) and `tried_at` (either; the bound's
#: eviction key)
_ROUTE_BOOK_ATTEMPTS: dict = {}


def reset_route_book_memo() -> None:
    """Tests only."""
    _ROUTE_BOOK_MEMO.clear()
    _ROUTE_BOOK_ATTEMPTS.clear()


#: THE VENUE'S STATES THAT SAY A MARKET IS NOT OPEN: market_plane.
#: freshness_window.TERMINAL_STATES | TRANSIENT_STATES, restated (a test pins
#: the equality) because importing that module would put its lazy imports
#: in this module's closure, which Adriana's runner reads and which must
#: hold no venue order path (tests/test_kalshi_canonical_claims.py).
NOT_OPEN_STATES = frozenset({
    "MARKET_STATE_EXPIRED", "MARKET_STATE_CLOSED", "MARKET_STATE_TERMINATED",
    "MARKET_STATE_MATCH_AND_CLOSE_AUCTION", "MARKET_STATE_SETTLED",
    "MARKET_STATE_RESOLVED", "EXPIRED", "CLOSED", "SETTLED", "RESOLVED",
    "INSTRUMENT_STATE_CLOSED", "INSTRUMENT_STATE_EXPIRED",
    "INSTRUMENT_STATE_TERMINATED",
    "INSTRUMENT_STATE_MATCH_AND_CLOSE_AUCTION",
    "MARKET_STATE_HALTED", "MARKET_STATE_SUSPENDED", "MARKET_STATE_PREOPEN",
    "MARKET_STATE_PAUSED", "HALTED", "SUSPENDED", "PREOPEN", "PAUSED",
    "INSTRUMENT_STATE_PREOPEN", "INSTRUMENT_STATE_SUSPENDED",
    "INSTRUMENT_STATE_HALTED", "INSTRUMENT_STATE_PENDING"})
#: (review 1) THE STATES THAT SAY A MARKET HAS ENDED (held the longer
#: ROUTE_BOOK_ENDED_HOLD_S): market_plane.active_refresh.ENDED_STATES,
#: restated for the same reason (a test pins the equality). CLOSED and the
#: match-and-close auction are NOT in it: a market read so can open again.
ENDED_STATES = frozenset({
    "INSTRUMENT_STATE_EXPIRED", "INSTRUMENT_STATE_TERMINATED",
    "MARKET_STATE_EXPIRED", "MARKET_STATE_TERMINATED",
    "MARKET_STATE_SETTLED", "MARKET_STATE_RESOLVED",
    "EXPIRED", "SETTLED", "RESOLVED"})


def _ended(state) -> bool:
    return str(state or "").strip().upper() in ENDED_STATES


def _not_open(state) -> bool:
    """The venue's own state says the market is not open (market_plane.
    populate.paper_book_counts' rule over the same sets). No state word is
    not a refusal."""
    st = str(state or "").strip().upper()
    return bool(st) and st in NOT_OPEN_STATES


def judge_route_book(slug: str, book: dict, *, now: float,
                     max_age_s: float) -> str | None:
    """PURE. None when `book` may cost a route of PMUS market `slug` at
    `now`; otherwise the refusal, by name. A book IN THE FUTURE is left to
    canonical_venue.quotes (BOOK_TIME_IN_FUTURE), which re-checks every
    book at the route's own instant."""
    if book.get("error"):
        return (R_PMUS_ROUTE_BOOK_READ_DEFERRED
                if str(book["error"]) in READ_DEFERRED_ERRORS
                else R_PMUS_ROUTE_BOOK_READ_FAILED)
    ps = book.get("payload_slug")
    if str(book.get("slug") or "") != str(slug) or (
            ps not in (None, "") and str(ps).strip().lower()
            != str(slug).strip().lower()):
        return R_PMUS_ROUTE_BOOK_MISMATCH
    at = book.get("observed_at")
    if at is None:
        return R_PMUS_ROUTE_BOOK_NO_RECEIPT
    if _not_open(book.get("state")):
        return R_PMUS_ROUTE_BOOK_NOT_OPEN
    bids = [p for p, q in book.get("bids") or () if int(q) >= 1]
    offers = [p for p, q in book.get("offers") or () if int(q) >= 1]
    if bids and offers and max(bids) >= min(offers):
        return R_PMUS_ROUTE_BOOK_CROSSED
    if float(now) - float(at) > float(max_age_s):
        return R_PMUS_ROUTE_BOOK_STALE
    return None


def route_book_from_read(slug: str, got) -> dict:
    """The reader's answer ({marketData, error, observed_at, served_by,
    ...}) as a route book: the levels, the venue's state and clock, OUR
    receipt instant as the reader returned it, and where it was served
    from."""
    got = got if isinstance(got, dict) else {}
    md = got.get("marketData")
    err = got.get("error")
    if not isinstance(md, dict):
        err = err or "NO_MARKET_DATA"
        md = {}
    served = got.get("served_by") or "UNNAMED_READER"
    at = got.get("observed_at")
    return {"slug": str(slug),
            "payload_slug": md.get("marketSlug") or md.get("slug"),
            "bids": _pmus_levels(md.get("bids")),
            "offers": _pmus_levels(md.get("offers")),
            "state": md.get("state") or md.get("marketState"),
            "venue_ts": md.get("transactTime") or md.get("timestamp"),
            "observed_at": None if at is None else float(at),
            "error": None if err is None else str(err)[:120],
            "source": "%s:%s" % (SRC_READ, served)}


def route_book_from_paper(slug: str, obs: dict) -> dict:
    """`pmus_book`'s recorded paper read as a route book."""
    return {"slug": str(slug), "payload_slug": None,
            "bids": list(obs.get("bids") or ()),
            "offers": list(obs.get("offers") or ()),
            "state": obs.get("state"), "venue_ts": None,
            "observed_at": obs.get("observed_at"), "error": None,
            "source": "%s:%s" % (SRC_PAPER, obs.get("source") or "?")}


async def _read_once(reader, slug: str, *, clock) -> dict:
    dl = clock() + ROUTE_BOOK_READ_DEADLINE_S
    try:
        return await asyncio.wait_for(
            reader(slug, deadline_epoch_s=dl,
                   timeout_s=ROUTE_BOOK_READ_DEADLINE_S),
            ROUTE_BOOK_READ_DEADLINE_S + 1.0)
    except asyncio.TimeoutError:
        return {"marketData": None, "error": "TimeoutError"}
    except Exception as exc:                                  # noqa: BLE001
        return {"marketData": None, "error": type(exc).__name__}


def _memo_put(memo: dict, slug: str, book: dict, *, now: float) -> None:
    memo[slug] = book
    for s in [s for s, b in memo.items()
              if now - float(b.get("observed_at") or 0.0)
              > PMUS_BOOK_WINDOW_S]:
        memo.pop(s, None)
    while len(memo) > ROUTE_BOOK_MEMO_MAX:
        memo.pop(min(memo, key=lambda s: float(
            memo[s].get("observed_at") or 0.0)), None)


def _attempt_put(attempts: dict, slug: str, book: dict, why, *,
                 at: float) -> None:
    """(review 1) Remember ONE on-demand read attempt of `slug`, whatever
    it answered. A read SENT -- accepted, refused or failed -- moves the
    market behind every market attempted longer ago (`sent_at`, the read
    order's key); a read deferred with nothing sent moves nothing
    (`deferred_at` only). The book is kept only when it is the venue's
    word about the market (not open, crossed): it then competes as the
    market's newest book. Bounded: the market tried longest ago is
    forgotten first."""
    rec = dict(attempts.get(slug) or {})
    if why == R_PMUS_ROUTE_BOOK_READ_DEFERRED:
        rec["deferred_at"] = float(at)
    else:
        rec.update(sent_at=float(at), why=why, state=book.get("state"),
                   book=dict(book) if why in _MARKET_REFUSALS else None)
    rec["tried_at"] = float(at)
    attempts[slug] = rec
    while len(attempts) > ROUTE_BOOK_ATTEMPTS_MAX:
        attempts.pop(min(attempts, key=lambda s: float(
            attempts[s].get("tried_at") or 0.0)), None)


def market_word(slug: str, book: dict | None) -> str | None:
    """PURE. (review 2) The venue's word about market `slug` that `book`
    carries -- R_PMUS_ROUTE_BOOK_NOT_OPEN or R_PMUS_ROUTE_BOOK_CROSSED --
    WHATEVER its receipt instant or age; None when it carries none (no
    book, a failed or deferred read, another market's book, or an open,
    uncrossed book). The judge's own rule, read as if timed now."""
    if book is None:
        return None
    why = judge_route_book(slug, dict(book, observed_at=0.0), now=0.0,
                           max_age_s=float("inf"))
    return why if why in _MARKET_REFUSALS else None


def newest_route_book(slug: str, books) -> dict | None:
    """PURE. (review 2) The newest of `books` by OUR receipt instant; at
    one instant the venue's word about the market (not open, crossed)
    before a book that carries none -- so a not-open or crossed word at
    least as new as an open book is never passed over for it, in any
    pass."""
    cands = [b for b in books if b is not None]
    return max(cands, key=lambda b: (
        float(b.get("observed_at") or 0.0),
        market_word(slug, b) is not None)) if cands else None


def said_newer(slug: str, read: dict | None, book: dict | None) -> bool:
    """PURE. (review 2) True when the on-demand read `read` is the venue's
    word about market `slug` (`market_word`: not open, crossed), carries
    OUR receipt instant, and is the newest of it and `book`
    (`newest_route_book`: not older; at one instant the word first). Such
    a read governs in the very pass that made it: `book` is never costed
    over it, exactly as `_newest_book` keeps it on every later pass. A
    read with no receipt instant is refused by its own name
    (PMUS_ROUTE_BOOK_HAS_NO_RECEIPT_INSTANT) like a failed read: it cannot
    be ordered by the book-age rule, and the production reader stamps
    every answer (workers.kalshi_market_data.public_book_read_blocking)."""
    if read is None or read.get("observed_at") is None or \
            market_word(slug, read) is None:
        return False
    return newest_route_book(slug, (book, read)) is read


def _newest_book(slug: str, obs, memo: dict, attempts: dict):
    """The market's newest book (`newest_route_book`): the recorded paper
    read, this process's last accepted on-demand read, and the last
    on-demand read that was the venue's word about the market (not open,
    crossed) -- so a newer word that the market is not open is never
    passed over for an older open book, and a newer open book releases a
    not-open hold. (The pass that makes a read applies the same rule to
    the read it just made: `route_books`, review 2.)"""
    cands = []
    if obs is not None:
        cands.append(route_book_from_paper(slug, obs))
    if memo.get(slug) is not None:
        cands.append(memo[slug])
    rec = attempts.get(slug) or {}
    if rec.get("book") is not None and rec.get("why") in _MARKET_REFUSALS:
        cands.append(rec["book"])
    return newest_route_book(slug, cands)


def hold_left_s(book: dict | None, why: str | None, *, now: float) -> float:
    """PURE. (review 1) How long a market whose newest book is `book`
    (judged `why`) stays out of the read plan: the venue says it is not
    open -- ROUTE_BOOK_NOT_OPEN_HOLD_S from that book's receipt, or
    ROUTE_BOOK_ENDED_HOLD_S when its state says it has ENDED. 0.0 when it
    may be read."""
    if book is None or why != R_PMUS_ROUTE_BOOK_NOT_OPEN or \
            book.get("observed_at") is None:
        return 0.0
    hold = ROUTE_BOOK_ENDED_HOLD_S if _ended(book.get("state")) \
        else ROUTE_BOOK_NOT_OPEN_HOLD_S
    return max(0.0, float(book["observed_at"]) + hold - float(now))


def read_after(book: dict | None, why: str | None, *, sent_at) -> tuple:
    """PURE. (review 1) (instant, joined_by_hold): the instant a market
    joins the least-recently-attempted read order -- its last read that was
    SENT (None: never sent, it goes first). When the venue's newest word
    says the market is not open, the END OF THAT WORD'S HOLD
    (`hold_left_s`) instead, if later, and joined_by_hold is True: the
    market is read again LAST -- behind every market waiting since before
    its hold ended (a market read at that very instant goes behind it) --
    and can never be starved. (Not an absolute last rank: with 300 s
    recording passes and a 30 s route bound every open market needs a read
    on every pass, so a last rank is never reached once
    ROUTE_BOOK_MAX_READS open markets are routed, and a market read
    SUSPENDED or HALTED once would never be read again.) A word only the
    paper runtime recorded is seen only while `pmus_book` returns it
    (PMUS_BOOK_WINDOW_S, 900 s from its receipt): an ENDED word of it holds
    900 s, not ROUTE_BOOK_ENDED_HOLD_S, and once it drops out a market
    never read on demand counts as never sent and goes first, not last --
    one read, after which our own word governs."""
    at = None if sent_at is None else float(sent_at)
    if book is not None and why == R_PMUS_ROUTE_BOOK_NOT_OPEN and \
            book.get("observed_at") is not None:
        end = float(book["observed_at"]) + (
            ROUTE_BOOK_ENDED_HOLD_S if _ended(book.get("state"))
            else ROUTE_BOOK_NOT_OPEN_HOLD_S)
        if at is None or end >= at:
            return end, True
    return at, False


def read_order_key(*, cross: bool, after, start, slug: str,
                   joined_by_hold: bool = False):
    """PURE. (review 1) The on-demand read order: cross-venue classes
    first; then the market that joined the order longest ago
    (`read_after`; never sent: first; at one instant, a market whose hold
    ended then before one read then); then the soonest start; the slug."""
    return (not cross, -1.0 if after is None else float(after),
            0 if joined_by_hold else 1,
            9e18 if start is None else float(start), str(slug))


async def route_books(conn, assembled, *, now: float, reader=None,
                      reads: int = 0, max_age_s: float = KMD.BOOK_SLA_S,
                      memo: dict | None = None, attempts: dict | None = None,
                      clock=time.time) -> dict:
    """THE PMUS ROUTE BOOK (the rule above): give every PMUS route
    candidate of `assembled` [(Fixture, built)] -- after the settlement
    certificates -- a judged book or a named refusal, IN PLACE on its
    instruments. `reader(slug, *, deadline_epoch_s, timeout_s)` is the
    bounded on-demand read (None: none is made); `reads` the read budget of
    this pass; `memo` the accepted reads and `attempts` every read's last
    attempt (default: this process's). Returns the census (counted by
    outcome, refused markets named, this pass's reads listed). Reads only:
    one SELECT per routed market and the reader."""
    memo = _ROUTE_BOOK_MEMO if memo is None else memo
    attempts = _ROUTE_BOOK_ATTEMPTS if attempts is None else attempts
    order, by = [], {}
    for fx, built in assembled:
        for _fp, members in (built.get("classes") or {}).items():
            pm = [i for i in members if i.venue == KCL.POLYMARKET_US]
            if not pm:
                continue
            cross = any(i.venue != KCL.POLYMARKET_US for i in members)
            for i in pm:
                e = by.get(i.market_id)
                if e is None:
                    e = by[i.market_id] = {"insts": [], "cross": False,
                                           "start": fx.start_epoch}
                    order.append(i.market_id)
                e["insts"].append(i)
                e["cross"] = e["cross"] or cross
    t0 = clock()
    # every routed market's recorded read first (the SELECT the loop made
    # before): the read order needs each market's newest word
    t_pre = max(float(now), clock())
    for slug in order:
        by[slug]["obs"] = await pmus_book(conn, slug, now=t_pre)
        b = _newest_book(slug, by[slug]["obs"], memo, attempts)
        by[slug]["after"], by[slug]["by_hold"] = read_after(
            b, None if b is None else judge_route_book(
                slug, b, now=t_pre, max_age_s=max_age_s),
            sent_at=(attempts.get(slug) or {}).get("sent_at"))
    order.sort(key=lambda s: read_order_key(
        cross=by[s]["cross"], after=by[s]["after"], start=by[s]["start"],
        slug=s, joined_by_hold=by[s]["by_hold"]))
    census = {"version": ROUTE_BOOK_VERSION,
              "routed_markets": len(order),
              "cross_venue_markets": sum(1 for s in order if by[s]["cross"]),
              "reads_allowed": int(reads), "reads_made": 0,
              "max_age_s": float(max_age_s), "lead_s": ROUTE_BOOK_LEAD_S,
              "accepted": {}, "refused": {}, "refused_named": [],
              "reads": []}
    for slug in order:
        t = max(float(now), clock())
        best = _newest_book(slug, by[slug]["obs"], memo, attempts)
        why_best = None if best is None else judge_route_book(
            slug, best, now=t, max_age_s=max_age_s)
        held = hold_left_s(best, why_best, now=t)
        chosen, why, tried = None, None, None
        if best is not None and why_best is None and \
                t - float(best["observed_at"]) <= max_age_s - ROUTE_BOOK_LEAD_S:
            chosen = best
        elif best is not None and why_best in _MARKET_REFUSALS and \
                t - float(best["observed_at"]) <= max_age_s:
            why = why_best           # the venue's word, inside the bound
        elif held > 0.0:
            why = R_PMUS_ROUTE_BOOK_HELD_NOT_OPEN   # not read again yet
        elif reader is not None and census["reads_made"] < int(reads) and \
                clock() - t0 < ROUTE_BOOK_PASS_BUDGET_S:
            census["reads_made"] += 1
            tried = route_book_from_read(
                slug, await _read_once(reader, slug, clock=clock))
            at = clock()
            why_rd = judge_route_book(slug, tried, now=max(float(now), at),
                                      max_age_s=max_age_s)
            # remembered WHATEVER it answered (review 1)
            _attempt_put(attempts, slug, tried, why_rd, at=at)
            census["reads"].append({"market": slug,
                                    "outcome": why_rd or "ACCEPTED"})
            if why_rd is None:
                chosen = tried
                _memo_put(memo, slug, tried, now=at)
            elif best is not None and why_best is None and \
                    not said_newer(slug, tried, best):
                # the read said nothing newer about this market than the
                # book: deferred (nothing sent), failed, another market's
                # book, no receipt instant, its own receipt past the bound,
                # or the venue's word with an OLDER receipt -- the book
                # still holds
                chosen = best
            else:
                # (review 2) refused by the read's own name -- and when it
                # is the venue's word about the market (not open, crossed)
                # at least as new as the open book, that book is NEVER
                # costed: the newest word governs in the pass that read it
                why = why_rd
        elif best is not None and why_best is None:
            chosen = best            # inside the bound, not re-read
        else:
            why = R_PMUS_ROUTE_BOOK_NOT_READ
        ref = chosen if chosen is not None else (tried or best)
        detail = {"source": None if ref is None else ref.get("source"),
                  "age_s": None if ref is None or ref.get("observed_at")
                  is None else round(t - float(ref["observed_at"]), 3),
                  "state": None if ref is None else ref.get("state"),
                  "venue_ts": None if ref is None else ref.get("venue_ts"),
                  "error": None if ref is None else ref.get("error")}
        if why == R_PMUS_ROUTE_BOOK_HELD_NOT_OPEN:
            detail["hold_left_s"] = round(held, 1)
        for i in by[slug]["insts"]:
            if chosen is not None:
                yes, no = KCL.pmus_asks(chosen)
                i.asks = yes if i.side == "YES" else no
                i.observed_at = float(chosen["observed_at"])
                i.book_basis = KCL.PMUS_BOOK_BASIS
                i.book_source = chosen["source"]
                i.book_refusal = None
            else:
                i.asks, i.observed_at, i.book_basis = (), None, None
                i.book_source = None
                i.book_refusal = why
            i.book_detail = dict(detail)
        if chosen is not None:
            k = str(chosen["source"])
            census["accepted"][k] = census["accepted"].get(k, 0) + 1
        else:
            census["refused"][why] = census["refused"].get(why, 0) + 1
            if len(census["refused_named"]) < ROUTE_BOOK_NAMED_MAX:
                census["refused_named"].append({"market": slug, "why": why})
    census["attempts_remembered"] = len(attempts)
    return census


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
