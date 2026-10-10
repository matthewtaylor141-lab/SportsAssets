"""rc6.3 route-book: the canonical route comparison gets a FRESH PMUS book.

Production (research-sql 37998929382, 2026-10-09 22:23Z): the worker's
route receipts costed every PMUS alias on the paper runtime's recorded REST
read within 900 s (canonical_claims_db.pmus_book), held to the 30 s route
bound. Of the 23 PMUS moneylines mapped to Kalshi fixtures in the claim
window, 0 had a paper read within 30 s and 18 none in 24 h; PMUS route
candidates in 24 h: NO_BOOK 80, STALE_BOOK 50, eligible 0. The plane
persists no PMUS book levels (PRIORITY_PMX_BOOKS: best bid / offer only, no
sizes, priority members only -- 5 of the 23).

canonical_claims_db.route_books gives every PMUS ROUTE CANDIDATE a judged
book at routing time -- the recorded read while it is fresh, else a bounded
on-demand keyless read -- with its own measured age, or a refusal BY NAME;
canonical_claims.route_claim never costs a refused book. Settlement and
contract identity are untouched (only aliases with one payoff fingerprint
and a holding certificate are routed together). Receipts stay SHADOW,
production_effect NONE. Every database write here is rolled back.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from decimal import Decimal

import pytest

from sportsassets import canonical_claims as CC
from sportsassets import canonical_claims_db as CDB
from sportsassets import kalshi_claims as KCL
from sportsassets import kalshi_market_data as KMD
from sportsassets.market_plane import rules as RULES
from sportsassets.workers import kalshi_market_data as W
from tests.test_kalshi_canonical_db import EV, TERMS, _Pool

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
SLUG = "aec-mlb-tb-nyy-2026-10-07"
PMUS = KCL.POLYMARKET_US
KALSHI = KCL.KALSHI
#: production's PMUS evidence for the mapped NHL / NBA / MLB / WNBA slugs
#: (research-sql 37999584576 F4): no draw rule, void at the last fair price
PROD_PMUS_TERMS = {"status": "ESTABLISHED", "settlement": {
    "overtime_included": True, "void_rule": "LAST_FAIR_PRICE"},
    "verification_sources": ["MLB"]}


def _lv(px, qty):
    return {"px": {"value": str(px), "currency": "USD"}, "qty": str(qty)}


def _md(*, offers=(("0.40", 100),), bids=(("0.38", 100),),
        state="MARKET_STATE_OPEN", slug=None):
    md = {"bids": [_lv(p, q) for p, q in bids],
          "offers": [_lv(p, q) for p, q in offers], "state": state,
          "transactTime": "2026-10-07T19:00:00Z"}
    if slug is not None:
        md["marketSlug"] = slug
    return md


class _Reader:
    """The on-demand reader, recorded: what it was asked and when."""

    def __init__(self, make):
        self.make = make
        self.calls = []

    async def __call__(self, slug, *, deadline_epoch_s, timeout_s):
        self.calls.append({"slug": slug, "deadline": deadline_epoch_s,
                           "timeout_s": timeout_s})
        return self.make(slug)


def _fresh(**kw):
    return lambda slug: {"marketData": _md(**kw), "error": None,
                         "observed_at": time.time(), "served_by": "TEST"}


async def _seed(c, *, pmus_terms=TERMS, paper_age_s=None, paper_md=None,
                kalshi_asks=None, pmus_sha=7):
    """One ESTABLISHED Kalshi MLB fixture (NYY home v TB) mapped to one PMUS
    moneyline (LONG = NYY), books on Kalshi fresh, the PMUS rules evidence
    `pmus_terms`, and optionally a recorded paper read `paper_age_s` old."""
    for t in ("canonical_route_receipts", "canonical_claim_aliases",
              "kalshi_books_current", "kalshi_fixtures_current"):
        await c.execute("DELETE FROM %s" % t)
    await c.execute("DELETE FROM us_premap WHERE market_slug = $1", SLUG)
    await W.persist_fee_terms(c, [{
        "id": "test-rc63-%d" % int(time.time() * 1e6),
        "fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1,
        "scheduled_ts": "2025-10-04T07:00:00Z",
        "series_ticker": "KXMLBGAME"}], kind="SERIES_CHANGE")
    now = time.time()
    start = now + 3 * 3600
    k = KMD.KalshiFixture(
        event_ticker=EV, series_ticker="KXMLBGAME", sport="BASEBALL",
        league="MLB", start_epoch=start, home_id="H", away_id="A",
        home_code="NYY", away_code="TB", tie_ticker=None,
        team_tickers=(EV + "-NYY", EV + "-TB"), outcome_kind="TWO_WAY",
        status="ESTABLISHED", reasons=(), milestone_id="m1")
    await W.persist_fixture(c, k, {"status": "ESTABLISHED",
                                   "pmus": {"slug": SLUG}, "reasons": []})
    for ident, side, intent, abbr in (
            ("rc63-long", "LONG", "ORDER_INTENT_BUY_LONG", "NYY"),
            ("rc63-short", "SHORT", "ORDER_INTENT_BUY_SHORT", "TB")):
        await c.execute(
            "INSERT INTO us_premap (identifier, side_norm, market_slug, "
            " intent, team_abbr, team_league, game_start) VALUES "
            " ($1,$2,$3,$4,$5,'mlb',to_timestamp($6))",
            ident, side, SLUG, intent, abbr, start)
    asks = kalshi_asks or {EV + "-NYY": ("0.4400", "0.5700"),
                           EV + "-TB": ("0.4500", "0.5600")}
    for t, (ya, na) in asks.items():
        ob = {"orderbook_fp": {
            "no_dollars": [[str(1 - Decimal(ya)), "100.00"]],
            "yes_dollars": [[str(1 - Decimal(na)), "100.00"]]}}
        await W.persist_book(c, t, EV, KMD.book_from_orderbook(
            ob, observed_at=now), {})
    rows = [{"contract_id": "kalshi:%s" % t, "venue": "KALSHI",
             "rules_published": True, "rules_field": "rules_primary",
             "rules_sha256": "%064d" % len(t),
             "rules_text": "test rules for %s" % t, "rules_secondary": None,
             "parse_status": "ESTABLISHED", "evidence": TERMS,
             "parser_version": "TEST", "source": "TEST"} for t in asks]
    rows.append({"contract_id": SLUG, "venue": "POLYMARKET_US",
                 "rules_published": True, "rules_field": "rules",
                 "rules_sha256": "%064d" % pmus_sha,
                 "rules_text": "pmus rules %d" % pmus_sha,
                 "rules_secondary": None, "parse_status": "ESTABLISHED",
                 "evidence": pmus_terms, "parser_version": "TEST",
                 "source": "TEST"})
    await RULES.upsert(c, rows, now=now)
    if paper_age_s is not None:
        md = paper_md or _md()
        await c.execute(
            "INSERT INTO paper_book_observations (us_market_slug, "
            " observed_at, source, bids, offers, market_state, read_basis) "
            " VALUES ($1, to_timestamp($2), 'TEST_PAPER', $3::jsonb, "
            " $4::jsonb, $5, 'TEST')", SLUG, now - paper_age_s,
            json.dumps(md["bids"]), json.dumps(md["offers"]), md["state"])
    return now


async def _receipts(c):
    out = []
    for r in await c.fetch(
            "SELECT claim_fingerprint, chosen, best_single, runner_up, "
            "       candidates, refusal, mode, production_effect "
            "  FROM canonical_route_receipts ORDER BY claim_fingerprint"):
        d = dict(r)
        for k in ("chosen", "best_single", "runner_up", "candidates"):
            d[k] = json.loads(d[k]) if isinstance(d[k], str) else d[k]
        out.append(d)
    return out


def _by_venue(rec):
    out = {}
    for x in rec["candidates"]:
        out.setdefault(x["venue"], []).append(x)
    return out


def _run(fn, monkeypatch):
    import asyncpg
    monkeypatch.setattr(RULES, "_SEEN", {})
    CDB.reset_route_book_memo()

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await fn(c)
        finally:
            await tr.rollback()
            await c.close()
            CDB.reset_route_book_memo()
    asyncio.run(go())


# ── pure ────────────────────────────────────────────────────────────────

def _book(**kw):
    b = {"slug": SLUG, "payload_slug": None,
         "bids": [(Decimal("0.38"), 100)], "offers": [(Decimal("0.40"), 100)],
         "state": "MARKET_STATE_OPEN", "observed_at": 1000.0, "error": None,
         "source": "TEST"}
    b.update(kw)
    return b


def test_the_judge_names_every_refusal_and_admits_a_fresh_book():
    j = CDB.judge_route_book
    assert j(SLUG, _book(), now=1010.0, max_age_s=30.0) is None
    assert j(SLUG, _book(), now=1031.0, max_age_s=30.0) == \
        CDB.R_PMUS_ROUTE_BOOK_STALE
    assert j(SLUG, _book(payload_slug="aec-mlb-bos-nyy-2026-10-07"),
             now=1001.0, max_age_s=30.0) == CDB.R_PMUS_ROUTE_BOOK_MISMATCH
    assert j(SLUG, _book(slug="aec-other"), now=1001.0, max_age_s=30.0) == \
        CDB.R_PMUS_ROUTE_BOOK_MISMATCH
    assert j(SLUG, _book(state="MARKET_STATE_EXPIRED"), now=1001.0,
             max_age_s=30.0) == CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN
    assert j(SLUG, _book(state="MARKET_STATE_SUSPENDED"), now=1001.0,
             max_age_s=30.0) == CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN
    assert j(SLUG, _book(bids=[(Decimal("0.41"), 5)]), now=1001.0,
             max_age_s=30.0) == CDB.R_PMUS_ROUTE_BOOK_CROSSED
    assert j(SLUG, _book(observed_at=None), now=1001.0, max_age_s=30.0) == \
        CDB.R_PMUS_ROUTE_BOOK_NO_RECEIPT
    assert j(SLUG, _book(error="RateLimitError"), now=1001.0,
             max_age_s=30.0) == CDB.R_PMUS_ROUTE_BOOK_READ_FAILED
    # nothing sent (our pre-read gate, or the venue gate before dispatch):
    # deferred, by its own name -- never a book, never costed
    for err in ("VENUE_HOLD_IN_FORCE", "VenueGateRefusal"):
        assert j(SLUG, _book(error=err), now=1001.0, max_age_s=30.0) == \
            CDB.R_PMUS_ROUTE_BOOK_READ_DEFERRED
    # the payload naming THIS slug (any case) is the alias market
    assert j(SLUG, _book(payload_slug=SLUG.upper()), now=1001.0,
             max_age_s=30.0) is None
    # a sub-contract level is no level: not crossed by it
    assert j(SLUG, _book(bids=[(Decimal("0.41"), 0)]), now=1001.0,
             max_age_s=30.0) is None


def test_the_not_open_states_are_the_freshness_windows_own():
    from sportsassets.market_plane import freshness_window as FW
    assert CDB.NOT_OPEN_STATES == FW.TERMINAL_STATES | FW.TRANSIENT_STATES


def test_the_not_open_hold_is_the_plane_refreshs_own():
    """(review 1) A market the venue says is not open is held as rc6.2
    p-freshness holds it: 900 s, and 3600 s when it has ENDED, over the
    same ENDED_STATES (CLOSED and the auction are not ended)."""
    from sportsassets.market_plane import active_refresh as AR
    assert CDB.ENDED_STATES == AR.ENDED_STATES
    assert CDB.ENDED_STATES < CDB.NOT_OPEN_STATES
    assert CDB.ROUTE_BOOK_NOT_OPEN_HOLD_S == AR.RETRY_NOT_OPEN_S
    assert CDB.ROUTE_BOOK_ENDED_HOLD_S == AR.RETRY_ENDED_S
    assert CDB.ROUTE_BOOK_ATTEMPTS_MAX > CDB.MAX_FIXTURES


def test_the_read_order_is_least_recently_tried_and_not_open_rejoins_last():
    """(review 1) PURE: the hold, the instant a market joins the read order
    and the order itself."""
    nop, j = CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN, CDB.judge_route_book
    closed = _book(state="MARKET_STATE_CLOSED", observed_at=1000.0)
    ended = _book(state="MARKET_STATE_EXPIRED", observed_at=1000.0)
    assert j(SLUG, closed, now=1500.0, max_age_s=30.0) == nop
    assert CDB.hold_left_s(closed, nop, now=1300.0) == 600.0
    assert CDB.hold_left_s(closed, nop, now=1900.0) == 0.0
    assert CDB.hold_left_s(ended, nop, now=1900.0) == 2700.0
    assert CDB.hold_left_s(ended, nop, now=4600.0) == 0.0
    # only the venue's not-open word holds; a failed or open read never does
    assert CDB.hold_left_s(_book(), None, now=1001.0) == 0.0
    assert CDB.hold_left_s(_book(error="X"), CDB.R_PMUS_ROUTE_BOOK_READ_FAILED,
                           now=1001.0) == 0.0
    # the instant a market joins the least-recently-attempted order: its
    # last read SENT; a not-open word's market at the END of its hold
    ra = CDB.read_after
    assert ra(None, None, sent_at=None) == (None, False)
    assert ra(_book(), None, sent_at=1000.0) == (1000.0, False)
    assert ra(_book(error="X"), CDB.R_PMUS_ROUTE_BOOK_READ_FAILED,
              sent_at=1000.0) == (1000.0, False)
    assert ra(closed, nop, sent_at=1000.0) == (1900.0, True)
    assert ra(ended, nop, sent_at=1000.0) == (4600.0, True)
    assert ra(closed, nop, sent_at=None) == (1900.0, True)  # the paper's
    assert ra(closed, nop, sent_at=2500.0) == (2500.0, False)  # later read
    k = CDB.read_order_key
    keys = {
        "never-cross-late": k(cross=True, after=None, start=9.0, slug="a"),
        "tried-cross-early": k(cross=True, after=5.0, start=1.0, slug="b"),
        "tried-long-ago": k(cross=True, after=1.0, start=8.0, slug="c"),
        "read-at-7": k(cross=True, after=7.0, start=0.0, slug="f"),
        "closed-hold-ended-at-7": k(cross=True, after=7.0, start=5.0,
                                    slug="e", joined_by_hold=True),
        "never-single": k(cross=False, after=None, start=0.0, slug="d")}
    assert sorted(keys, key=keys.get) == [
        "never-cross-late", "tried-long-ago", "tried-cross-early",
        "closed-hold-ended-at-7", "read-at-7", "never-single"]


def test_a_read_keeps_its_own_receipt_and_names_where_it_came_from():
    got = {"marketData": _md(slug=SLUG), "error": None, "observed_at": 1234.5,
           "served_by": "PUBLIC_GATEWAY_KEYLESS"}
    b = CDB.route_book_from_read(SLUG, got)
    assert b["observed_at"] == 1234.5 and b["payload_slug"] == SLUG
    assert b["offers"] == [(Decimal("0.40"), 100)]
    assert b["source"] == "PMUS_ON_DEMAND_READ:PUBLIC_GATEWAY_KEYLESS"
    bad = CDB.route_book_from_read(SLUG, {"marketData": None,
                                          "error": None})
    assert bad["error"] == "NO_MARKET_DATA"


def _inst(venue, market, side, asks, *, at=1000.0, refusal=None):
    return CC.Instrument(
        venue=venue, market_id=market, side=side, subject="HOME",
        settlement=None, settlement_status="PROVEN",
        mapping_status="ESTABLISHED", asks=tuple(asks), observed_at=at,
        book_basis="TEST", sport="BASEBALL", book_refusal=refusal,
        book_detail={"age_s": 1.0} if refusal else None,
        vector={"HOME_WIN": "1", "AWAY_WIN": "0"})


def test_route_claim_never_costs_a_refused_book_alone_or_in_a_split():
    fx = CC.Fixture(event_key="MLB:x", sport="BASEBALL", league="MLB",
                    start_epoch=2000.0, outcome_kind="TWO_WAY", home="NYY",
                    away="TB")
    k = _inst(KALSHI, EV + "-NYY", "YES", [(Decimal("0.44"), 100)])
    # the PMUS book is far cheaper but REFUSED (another market's book)
    p = _inst(PMUS, SLUG, "YES", [(Decimal("0.10"), 100)],
              refusal=CDB.R_PMUS_ROUTE_BOOK_MISMATCH)
    fees = {KALSHI: lambda n, px: Decimal("0"), PMUS: lambda n, px: Decimal("0")}
    r = CC.route_claim(fx, "fp", [k, p], qty=10, now=1001.0,
                       fee_by_venue=fees, max_age_s=30.0)
    cand = {x["venue"]: x for x in r["candidates"]}
    assert cand[PMUS]["eligible"] is False
    assert cand[PMUS]["reason"] == CDB.R_PMUS_ROUTE_BOOK_MISMATCH
    assert cand[PMUS]["book_detail"] == {"age_s": 1.0}
    assert all(a["venue"] == KALSHI for a in r["chosen"]["allocations"])
    assert r["best_single"]["venue"] == KALSHI and r["runner_up"] is None


def test_the_public_reader_defers_during_the_hold_without_a_request(
        monkeypatch):
    from sportsassets import institutional_same_book as SB
    from sportsassets import venue_request_gate as grt
    monkeypatch.setattr(grt, "normal_read_gate", lambda now=None: {
        "blocking": True, "reason": "VENUE_429", "seconds_left": 7.0})

    def boom(*a, **k):
        raise AssertionError("a request was made during the hold")
    monkeypatch.setattr(SB, "retail_book_read", boom)
    got = W.public_book_read_blocking(SLUG, deadline_epoch_s=time.time() + 3)
    assert got["marketData"] is None
    assert got["error"] == W.ROUTE_READ_DEFERRED
    assert W.ROUTE_READ_DEFERRED in CDB.READ_DEFERRED_ERRORS
    b = CDB.route_book_from_read(SLUG, got)
    assert CDB.judge_route_book(SLUG, b, now=time.time(), max_age_s=30.0) \
        == CDB.R_PMUS_ROUTE_BOOK_READ_DEFERRED


def test_a_read_the_venue_gate_refuses_before_dispatch_is_deferred(
        monkeypatch):
    """The cooldown outlasts the read's deadline: venue_request_gate raises
    VenueGateRefusal inside the transport and retail_book_read names it --
    nothing was sent, so the route book names a DEFERRAL; any other read
    error is a failed read, by name."""
    from sportsassets import institutional_same_book as SB
    from sportsassets import venue_request_gate as grt
    monkeypatch.setattr(grt, "normal_read_gate", lambda now=None: {
        "blocking": False})
    # (review 1) the gate's own refusal reasons are nothing sent as well:
    # a deferral keeps the market's place in the read order, a failure not
    gate_reasons = (grt.R_COOLDOWN_EXCEEDS_DEADLINE, grt.R_DEADLINE_PASSED,
                    grt.R_HOLD_EXCEEDS_UNDEADLINED_CAP)
    assert CDB.READ_DEFERRED_ERRORS == frozenset(
        {W.ROUTE_READ_DEFERRED, grt.VenueGateRefusal.__name__}
        | set(gate_reasons))
    for err, why in (("VenueGateRefusal", CDB.R_PMUS_ROUTE_BOOK_READ_DEFERRED),
                     *((r, CDB.R_PMUS_ROUTE_BOOK_READ_DEFERRED)
                       for r in gate_reasons),
                     ("RateLimitError", CDB.R_PMUS_ROUTE_BOOK_READ_FAILED),
                     ("ConnectError", CDB.R_PMUS_ROUTE_BOOK_READ_FAILED)):
        monkeypatch.setattr(SB, "retail_book_read",
                            lambda slug, client=None, e=err: {
                                "ok": False, "marketData": None, "error": e})
        got = W.public_book_read_blocking(SLUG,
                                          deadline_epoch_s=time.time() + 3)
        b = CDB.route_book_from_read(SLUG, got)
        assert CDB.judge_route_book(SLUG, b, now=time.time(),
                                    max_age_s=30.0) == why
    assert grt.current_read() is None            # the read is unbound


def test_the_public_reader_reads_keyless_with_its_deadline_and_receipt(
        monkeypatch):
    from sportsassets import institutional_same_book as SB
    from sportsassets import venue_request_gate as grt
    seen = {}
    monkeypatch.setattr(grt, "normal_read_gate", lambda now=None: {
        "blocking": False})

    def fake(slug, *, client=None):
        rid = grt.current_read()
        seen["deadline"] = (grt.read_state(rid) or {}).get("deadline_epoch_s")
        seen["slug"] = slug
        return {"ok": True, "marketData": _md(), "error": None}
    monkeypatch.setattr(SB, "retail_book_read", fake)
    t0 = time.time()
    got = W.public_book_read_blocking(SLUG, deadline_epoch_s=t0 + 3.0)
    assert seen == {"deadline": t0 + 3.0, "slug": SLUG}
    assert got["observed_at"] >= t0 and got["error"] is None
    assert got["served_by"] == W.ROUTE_READ_SERVED_BY
    assert grt.current_read() is None            # the read is unbound


def test_a_kalshi_ticker_never_reaches_the_retail_reader():
    from sportsassets import institutional_same_book as SB
    got = SB.retail_book_read(EV + "-NYY")
    assert got == {"ok": False, "marketData": None, "error": "SLUG_REFUSED"}


def test_the_route_book_reader_is_keyless_and_imports_no_paper_module():
    import ast
    import inspect
    src = inspect.getsource(W)
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods |= {n.module or ""} | {a.name for a in n.names}
        elif isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
    assert not any("paper" in m for m in mods), mods
    body = inspect.getsource(W.public_book_read_blocking)
    assert "retail_book_read" in body and "normal_read_gate" in body
    assert "_get_client" not in body and "pmus" not in body.replace(
        "PMUS", "")


# ── against Postgres ───────────────────────────────────────────────────

@pg
def test_fresh_books_on_both_venues_give_a_both_eligible_receipt(monkeypatch):
    """The claim (NYY wins) carried by Kalshi YES NYY, Kalshi NO TB and PMUS
    LONG NYY. The recorded paper read is 600 s old (production's shape);
    the on-demand read is fresh: both venues eligible, the cheaper all-in
    acquisition chosen, the PMUS book's own measured age on the receipt."""
    async def go(c):
        await _seed(c, paper_age_s=600.0)
        rd = _Reader(_fresh(offers=(("0.40", 100),), bids=(("0.38", 100),)))
        res = await W.claims_pass(_Pool(c), record=True, route_reader=rd)
        assert [x["slug"] for x in rd.calls] == [SLUG]   # one book, 2 sides
        assert rd.calls[0]["timeout_s"] == CDB.ROUTE_BOOK_READ_DEADLINE_S
        rb = res["pmus_route_books"]
        assert rb["routed_markets"] == 1 and rb["cross_venue_markets"] == 1
        assert rb["reads_made"] == 1 and rb["refused"] == {}
        recs = await _receipts(c)
        assert recs and all(r["mode"] == "SHADOW"
                            and r["production_effect"] == "NONE"
                            for r in recs)
        both = [r for r in recs if any(
            x["venue"] == PMUS and x["eligible"] for x in r["candidates"])
            and any(x["venue"] == KALSHI and x["eligible"]
                    for x in r["candidates"])]
        assert len(both) == 2, json.dumps(recs, default=str)[:2000]
        home = [r for r in both if any(
            x["venue"] == PMUS and x["side"] == "YES"
            for x in r["candidates"])]
        assert len(home) == 1
        r = home[0]
        cand = _by_venue(r)
        p = cand[PMUS][0]
        assert p["book_source"] == "PMUS_ON_DEMAND_READ:TEST"
        assert 0 <= p["book_age_s"] <= W.ROUTE_MAX_AGE_S
        # the cheapest TOTAL all-in acquisition wins (PMUS 0.40 + fee
        # against Kalshi 0.44 / 0.56 + fee)
        alls = sorted((Decimal(x["all_in"]), x["venue"])
                      for x in r["candidates"] if x["eligible"])
        assert alls[0][1] == PMUS
        assert r["best_single"]["venue"] == PMUS
        assert r["runner_up"]["venue"] == KALSHI
        assert {a["venue"] for a in r["chosen"]["allocations"]} == {PMUS}
        # THE HIGHER ALL-IN EXECUTABLE VALUE: every alias of one claim pays
        # the same in every state (one fingerprint), so RECEIPT_QTY's
        # executable value is qty x 1 - all_in on either venue -- the chosen
        # route carries the highest, and no eligible candidate beats it
        qty = Decimal(W.RECEIPT_QTY)
        val = {(x["venue"], x["market_id"], x["side"]):
               qty - Decimal(x["all_in"])
               for x in r["candidates"] if x["eligible"]}
        assert {k[0] for k in val} == {PMUS, KALSHI}
        best = (r["best_single"]["venue"], r["best_single"]["market_id"],
                r["best_single"]["side"])
        assert val[best] == max(val.values())
        assert val[best] > max(v for k, v in val.items() if k[0] == KALSHI)
        assert qty - Decimal(r["chosen"]["all_in"]) == val[best]
        # the alias row carries the book the route was costed on
        row = await c.fetchrow(
            "SELECT best_ask, extract(epoch FROM observed_at) AS at "
            "  FROM canonical_claim_aliases WHERE alias_key = $1",
            "%s|%s|YES" % (PMUS, SLUG))
        assert Decimal(str(row["best_ask"])) == Decimal("0.40")
        assert time.time() - float(row["at"]) <= W.ROUTE_MAX_AGE_S
    _run(go, monkeypatch)


@pg
def test_the_cheaper_venue_wins_whichever_it_is(monkeypatch):
    async def go(c):
        await _seed(c)
        rd = _Reader(_fresh(offers=(("0.52", 100),), bids=(("0.50", 100),)))
        await W.claims_pass(_Pool(c), record=True, route_reader=rd)
        recs = await _receipts(c)
        home = [r for r in recs if any(x["venue"] == PMUS and x["side"]
                                       == "YES" for x in r["candidates"])]
        assert len(home) == 1
        r = home[0]
        assert all(x["eligible"] for x in r["candidates"]), r["candidates"]
        assert r["best_single"]["venue"] == KALSHI          # 0.44 < 0.52
        assert {a["venue"] for a in r["chosen"]["allocations"]} == {KALSHI}
        lost = {(x["venue"], x["side"]) for x in r["candidates"]} - {
            (r["best_single"]["venue"], r["best_single"]["side"])}
        assert (PMUS, "YES") in lost
    _run(go, monkeypatch)


@pg
@pytest.mark.parametrize("make,why", [
    (lambda slug: {"marketData": _md(), "error": None,
                   "observed_at": time.time() - 120.0, "served_by": "T"},
     CDB.R_PMUS_ROUTE_BOOK_STALE),
    (lambda slug: {"marketData": _md(slug="aec-mlb-bos-nyy-2026-10-07"),
                   "error": None, "observed_at": time.time(),
                   "served_by": "T"},
     CDB.R_PMUS_ROUTE_BOOK_MISMATCH),
    (lambda slug: {"marketData": _md(state="MARKET_STATE_EXPIRED"),
                   "error": None, "observed_at": time.time(),
                   "served_by": "T"},
     CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN),
    (lambda slug: {"marketData": _md(bids=(("0.45", 10),)), "error": None,
                   "observed_at": time.time(), "served_by": "T"},
     CDB.R_PMUS_ROUTE_BOOK_CROSSED),
    (lambda slug: {"marketData": None, "error": W.ROUTE_READ_DEFERRED,
                   "observed_at": time.time(), "served_by": "T"},
     CDB.R_PMUS_ROUTE_BOOK_READ_DEFERRED),
    (lambda slug: {"marketData": None, "error": "ConnectError",
                   "observed_at": time.time(), "served_by": "T"},
     CDB.R_PMUS_ROUTE_BOOK_READ_FAILED),
])
def test_a_stale_or_mismatched_pmus_book_is_refused_by_name(
        monkeypatch, make, why):
    """The paper read is 600 s old and the on-demand read is refused: the
    PMUS candidates carry the refusal by name, no book, no cost; the Kalshi
    candidates keep routing; no receipt is both-venue eligible."""
    async def go(c):
        await _seed(c, paper_age_s=600.0)
        rd = _Reader(make)
        res = await W.claims_pass(_Pool(c), record=True, route_reader=rd)
        assert len(rd.calls) == 1
        assert res["pmus_route_books"]["refused"] == {why: 1}
        recs = await _receipts(c)
        pm = [x for r in recs for x in r["candidates"] if x["venue"] == PMUS]
        assert len(pm) == 2 and all(
            x["eligible"] is False and x["reason"] == why
            and x["ask"] is None and "all_in" not in x for x in pm), pm
        for r in recs:
            assert r["chosen"] and {a["venue"] for a in
                                    r["chosen"]["allocations"]} == {KALSHI}
            assert not any(x["venue"] == PMUS and x["eligible"]
                           for x in r["candidates"])
        row = await c.fetchrow(
            "SELECT best_ask, observed_at FROM canonical_claim_aliases "
            " WHERE alias_key = $1", "%s|%s|YES" % (PMUS, SLUG))
        assert row["best_ask"] is None and row["observed_at"] is None
    _run(go, monkeypatch)


@pg
def test_without_a_read_budget_a_stale_recorded_read_is_refused_by_name(
        monkeypatch):
    """Reads switched off (or a non-recording pass): no request; the 600 s
    old paper read is never costed -- PMUS_ROUTE_BOOK_NOT_READ_..."""
    async def go(c):
        await _seed(c, paper_age_s=600.0)
        monkeypatch.setenv(W.ROUTE_BOOK_ENV_FLAG, "off")

        async def never(*a, **k):
            raise AssertionError("no read may be made without a budget")
        res = await W.claims_pass(_Pool(c), record=True, route_reader=never)
        rb = res["pmus_route_books"]
        assert rb["reads_allowed"] == 0 and rb["reads_made"] == 0
        assert rb["refused"] == {CDB.R_PMUS_ROUTE_BOOK_NOT_READ: 1}
        recs = await _receipts(c)
        pm = [x for r in recs for x in r["candidates"] if x["venue"] == PMUS]
        assert pm and all(x["reason"] == CDB.R_PMUS_ROUTE_BOOK_NOT_READ
                          and x["book_detail"]["age_s"] >= 600.0 for x in pm)
        monkeypatch.delenv(W.ROUTE_BOOK_ENV_FLAG)
        # a NON-recording pass has no read budget either
        res2 = await W.claims_pass(_Pool(c), record=False, route_reader=never)
        assert res2["pmus_route_books"]["reads_allowed"] == 0
    _run(go, monkeypatch)


@pg
def test_a_fresh_recorded_read_is_used_without_a_request(monkeypatch):
    async def go(c):
        await _seed(c, paper_age_s=3.0)

        async def never(*a, **k):
            raise AssertionError("a fresh recorded read needs no request")
        res = await W.claims_pass(_Pool(c), record=True, route_reader=never)
        rb = res["pmus_route_books"]
        assert rb["reads_made"] == 0
        assert rb["accepted"] == {"PAPER_BOOK_OBSERVATION:TEST_PAPER": 1}
        recs = await _receipts(c)
        pm = [x for r in recs for x in r["candidates"] if x["venue"] == PMUS]
        assert len(pm) == 2 and all(x["eligible"] for x in pm), pm
        assert all(x["book_source"] == "PAPER_BOOK_OBSERVATION:TEST_PAPER"
                   for x in pm)
    _run(go, monkeypatch)


@pg
def test_settlement_identity_is_still_required_no_equivalence_assumed(
        monkeypatch):
    """(a) production's PMUS evidence (no draw rule, void at the last fair
    price): every PMUS alias is refused before routing -- never read for,
    in no receipt; (b) a PMUS void rule that differs from Kalshi's: PMUS
    routes in its OWN claim class -- no receipt ever carries both venues,
    however fresh both books are."""
    async def go(c):
        await _seed(c, pmus_terms=PROD_PMUS_TERMS, paper_age_s=600.0)
        rd = _Reader(_fresh())
        res = await W.claims_pass(_Pool(c), record=True, route_reader=rd)
        assert rd.calls == []
        rb = res["pmus_route_books"]
        assert rb["routed_markets"] == 0 and rb["reads_made"] == 0
        recs = await _receipts(c)
        assert recs and not any(x["venue"] == PMUS for r in recs
                                for x in r["candidates"])
        refused = await c.fetch(
            "SELECT refusals FROM canonical_claim_aliases WHERE venue = $1",
            PMUS)
        assert len(refused) == 2 and all(
            "UNKNOWN_STATES" in str(r["refusals"]) for r in refused)

        other = json.loads(json.dumps(TERMS))
        other["settlement"]["void_rule"] = "RESOLVES_NO"
        # (new rules, new fingerprint: the registry re-parses on a change)
        await _seed(c, pmus_terms=other, paper_age_s=600.0, pmus_sha=8)
        rd2 = _Reader(_fresh())
        res2 = await W.claims_pass(_Pool(c), record=True, route_reader=rd2)
        assert len(rd2.calls) == 1                   # a PMUS-only class
        assert res2["pmus_route_books"]["cross_venue_markets"] == 0
        recs2 = await _receipts(c)
        venues = [{x["venue"] for x in r["candidates"]} for r in recs2]
        assert {frozenset(v) for v in venues} == {frozenset({KALSHI}),
                                                  frozenset({PMUS})}
        fps = {r["claim_fingerprint"]: {x["venue"] for x in r["candidates"]}
               for r in recs2}
        assert not any(len(v) > 1 for v in fps.values())
    _run(go, monkeypatch)


@pg
def test_the_read_budget_is_bounded_and_spent_on_cross_venue_first(
        monkeypatch):
    """route_books directly: more routed PMUS markets than the budget --
    cross-venue first, every unread market refused by name, and the
    accepted read remembered (reused, aged honestly, on the next pass)."""
    async def go(c):
        now = time.time()
        fx = CC.Fixture(event_key="MLB:y", sport="BASEBALL", league="MLB",
                        start_epoch=now + 3600, outcome_kind="TWO_WAY",
                        home="NYY", away="TB")
        built_cls, insts = {}, {}
        for n in range(CDB.ROUTE_BOOK_MAX_READS + 3):
            slug = "aec-mlb-t%02d-nyy-2026-10-07" % n
            p = _inst(PMUS, slug, "YES", [], at=None)
            insts[slug] = p
            members = [p]
            if n >= 3:            # the last ones are cross-venue classes
                members.append(_inst(KALSHI, "K-%d" % n, "YES",
                                     [(Decimal("0.5"), 10)], at=now))
            built_cls["fp%d" % n] = members
        memo = {}
        rd = _Reader(_fresh())
        cen = await CDB.route_books(
            c, [(fx, {"classes": built_cls})], now=now, reader=rd,
            reads=CDB.ROUTE_BOOK_MAX_READS, memo=memo)
        assert cen["routed_markets"] == CDB.ROUTE_BOOK_MAX_READS + 3
        assert cen["reads_made"] == CDB.ROUTE_BOOK_MAX_READS
        read = [x["slug"] for x in rd.calls]
        cross = ["aec-mlb-t%02d-nyy-2026-10-07" % n
                 for n in range(3, CDB.ROUTE_BOOK_MAX_READS + 3)]
        assert read == cross                          # cross-venue first
        assert cen["refused"] == {CDB.R_PMUS_ROUTE_BOOK_NOT_READ: 3}
        for n in range(3):
            i = insts["aec-mlb-t%02d-nyy-2026-10-07" % n]
            assert i.book_refusal == CDB.R_PMUS_ROUTE_BOOK_NOT_READ
            assert i.asks == () and i.observed_at is None
        assert set(memo) == set(cross)
        # the next pass with no budget reuses the remembered reads (their
        # own receipt instants), reads nothing, and still names the rest
        for i in insts.values():
            i.book_refusal = None
        cen2 = await CDB.route_books(
            c, [(fx, {"classes": built_cls})], now=time.time(), reader=rd,
            reads=0, memo=memo)
        assert len(rd.calls) == CDB.ROUTE_BOOK_MAX_READS
        assert sum(cen2["accepted"].values()) == CDB.ROUTE_BOOK_MAX_READS
        assert cen2["refused"] == {CDB.R_PMUS_ROUTE_BOOK_NOT_READ: 3}
        assert all(insts[s].observed_at == memo[s]["observed_at"]
                   for s in cross)
        # past the bound the remembered read is refused by name
        late = await CDB.route_books(
            c, [(fx, {"classes": built_cls})], now=time.time() + 60.0,
            reader=None, reads=0, memo=memo)
        assert late["accepted"] == {}
        assert late["refused"] == {CDB.R_PMUS_ROUTE_BOOK_NOT_READ:
                                   CDB.ROUTE_BOOK_MAX_READS + 3}
    _run(go, monkeypatch)


#: production's mapped PMUS slugs at 2026-10-09 23:58Z (research-sql
#: 38006965666 P5): 23 in the claim window; the newest error-free paper read
#: 1 within 20 s, 4 older than 30 s (inside 900 s), 18 none in 900 s
PROD_SHAPE_PAPER_AGES = [5.0, 60.0, 136.0, 300.0, 538.0] + [None] * 18


@pg
def test_the_production_shape_rotates_the_bounded_reads_over_every_market(
        monkeypatch):
    """The 23 production-shaped markets, IF every one were a certified
    cross-venue class (today 0 are: every PMUS alias is refused before
    routing, UNKNOWN_STATES:DRAW...). One recording pass every 300 s: pass
    1 uses the one fresh paper read without a request, reads 6 and names
    16 PMUS_ROUTE_BOOK_NOT_READ_...; the never-read markets go first, so
    after 4 recording passes every market has had its own fresh book, and
    no pass ever reads more than 6."""
    async def go(c):
        base = time.time()
        clk = {"t": base}
        fxs, insts = [], {}
        await c.execute("DELETE FROM paper_book_observations "
                        " WHERE us_market_slug LIKE 'aec-nhl-rc63-%'")
        for n, age in enumerate(PROD_SHAPE_PAPER_AGES):
            slug = "aec-nhl-rc63-%02d-2026-10-10" % n
            fx = CC.Fixture(event_key="NHL:%d" % n, sport="HOCKEY",
                            league="NHL", start_epoch=base + 3600 + 600 * n,
                            outcome_kind="TWO_WAY", home="H", away="A")
            p = _inst(PMUS, slug, "YES", [], at=None)
            k = _inst(KALSHI, "KX-%d" % n, "YES", [(Decimal("0.5"), 10)],
                      at=base)
            insts[slug] = p
            fxs.append((fx, {"classes": {"fp%d" % n: [p, k]}}))
            if age is not None:
                md = _md()
                await c.execute(
                    "INSERT INTO paper_book_observations (us_market_slug, "
                    " observed_at, source, bids, offers, market_state, "
                    " read_basis) VALUES ($1, to_timestamp($2), "
                    " 'TEST_PAPER', $3::jsonb, $4::jsonb, $5, 'TEST')",
                    slug, base - age, json.dumps(md["bids"]),
                    json.dumps(md["offers"]), md["state"])

        def make(slug):
            return {"marketData": _md(), "error": None,
                    "observed_at": clk["t"], "served_by": "TEST"}
        memo, read_by_pass = {}, []
        for k in range(4):
            clk["t"] = base + 300.0 * k
            for i in insts.values():
                i.book_refusal = None
            rd = _Reader(make)
            cen = await CDB.route_books(
                c, fxs, now=clk["t"], reader=rd,
                reads=CDB.ROUTE_BOOK_MAX_READS, memo=memo,
                clock=lambda: clk["t"])
            assert cen["routed_markets"] == 23
            assert cen["cross_venue_markets"] == 23
            assert len(rd.calls) == cen["reads_made"] <= 6
            read_by_pass.append([x["slug"] for x in rd.calls])
            if k == 0:
                assert cen["accepted"] == {
                    "PAPER_BOOK_OBSERVATION:TEST_PAPER": 1,
                    "PMUS_ON_DEMAND_READ:TEST": 6}
                assert cen["refused"] == {CDB.R_PMUS_ROUTE_BOOK_NOT_READ: 16}
                fresh_paper = "aec-nhl-rc63-00-2026-10-10"
                assert fresh_paper not in read_by_pass[0]
                assert insts[fresh_paper].book_source == \
                    "PAPER_BOOK_OBSERVATION:TEST_PAPER"
            else:
                # 300 s later every earlier book is past the bound: only
                # this pass's own reads may cost a route
                assert cen["accepted"] == {"PMUS_ON_DEMAND_READ:TEST": 6}
                assert cen["refused"] == {CDB.R_PMUS_ROUTE_BOOK_NOT_READ: 17}
            for s, i in insts.items():
                if i.book_refusal is None:
                    assert clk["t"] - i.observed_at <= W.ROUTE_MAX_AGE_S
                else:
                    assert i.asks == () and i.observed_at is None
        # the reads never repeat a market while one was never read
        flat = [s for p in read_by_pass for s in p]
        assert len(flat) == 24 and len(set(flat[:23])) == 23
        assert set(flat) == set(insts)
    _run(go, monkeypatch)


# ── (review 1 of a9f9f54c) the bounded reads never starve a market ──────
#
# The reviewer's reproduction (production 2026-10-10 01:53Z: the 23 mapped
# slugs still held 10-09 games): a read that came back refused (not open,
# failed, crossed, deferred) was never remembered, so the same earlier-
# starting markets sorted as never read on every recording pass and spent
# the whole budget; the open, upcoming markets were refused
# PMUS_ROUTE_BOOK_NOT_READ_PASS_BUDGET_SPENT on every pass, never read.

#: what each refusing market's read answers, by mode ("mixed" cycles them)
DEAD_KINDS = ("closed", "ended", "failed", "raises")
DEAD_HOLD_PASSES = {"closed": 3, "ended": 12}    # 900 s / 3600 s, 300 s apart


def _dead_kind(mode, n):
    return DEAD_KINDS[n % len(DEAD_KINDS)] if mode == "mixed" else mode


def _shape(base, *, dead, live, prefix="aec-nhl-rc63s"):
    """`dead` cross-venue games that started 3 h ago (inside the claim
    window's 4 h lookback, so they sort first by start) and `live` ones
    starting in 2 h; every class is certified cross-venue."""
    fxs, insts, slugs_dead, slugs_live = [], {}, [], []
    for n in range(dead + live):
        is_dead = n < dead
        slug = "%s-%02d-2026-10-10" % (prefix, n)
        start = base - 3 * 3600 + 60 * n if is_dead else \
            base + 7200 + 60 * n
        fx = CC.Fixture(event_key="NHL:s%d" % n, sport="HOCKEY",
                        league="NHL", start_epoch=start,
                        outcome_kind="TWO_WAY", home="H", away="A")
        p = _inst(PMUS, slug, "YES", [], at=None)
        k = _inst(KALSHI, "KXS-%d" % n, "YES", [(Decimal("0.5"), 10)],
                  at=base)
        insts[slug] = p
        (slugs_dead if is_dead else slugs_live).append(slug)
        fxs.append((fx, {"classes": {"fps%d" % n: [p, k]}}))
    return fxs, insts, slugs_dead, slugs_live


@pg
@pytest.mark.parametrize("mode", ["closed", "ended", "failed", "raises",
                                  "mixed"])
def test_refusing_earlier_markets_never_starve_the_open_ones(
        monkeypatch, mode):
    """8 earlier-starting cross-venue markets whose every read is refused
    (the venue says CLOSED, or EXPIRED; the read fails, or raises) and 4
    open later ones, one recording pass every 300 s for 16 passes: every
    open market gets its OWN fresh on-demand book within ceil(12 / 6) = 2
    passes and again inside every 2 consecutive passes after that; no pass
    makes more than 6 reads; a market the venue says is not open is not
    read again inside its hold (named PMUS_ROUTE_BOOK_HELD_..., its own
    read's age on the receipt), is read again within 2 passes of the hold's
    end, and then behind every open market waiting since before that end;
    a market whose read fails is read again within 2 passes (behind the
    others, never starved, never starving)."""
    async def go(c):
        base = time.time()
        clk = {"t": base}
        n_dead, n_live = CDB.ROUTE_BOOK_MAX_READS + 2, 4
        fxs, insts, dead, live = _shape(base, dead=n_dead, live=n_live)
        await c.execute("DELETE FROM paper_book_observations "
                        " WHERE us_market_slug LIKE 'aec-nhl-rc63s-%'")
        kind = {s: _dead_kind(mode, n) for n, s in enumerate(dead)}

        def make(slug):
            k = kind.get(slug)
            if k in ("closed", "ended"):
                st = "MARKET_STATE_CLOSED" if k == "closed" \
                    else "MARKET_STATE_EXPIRED"
                return {"marketData": _md(offers=(("0.99", 100),),
                                          bids=(("0.01", 100),), state=st),
                        "error": None, "observed_at": clk["t"],
                        "served_by": "TEST"}
            if k == "failed":
                return {"marketData": None, "error": "HTTPStatusError",
                        "observed_at": clk["t"], "served_by": "TEST"}
            if k == "raises":
                raise RuntimeError("transport")
            return {"marketData": _md(), "error": None,
                    "observed_at": clk["t"], "served_by": "TEST"}
        memo, attempts = {}, {}
        n_pass = 16
        within = -(-(n_dead + n_live) // CDB.ROUTE_BOOK_MAX_READS)   # 2
        fresh_by = {s: [] for s in live}
        read_at = {s: [] for s in dead}
        for k in range(n_pass):
            clk["t"] = base + 300.0 * k
            for i in insts.values():
                i.book_refusal = None
            prev_live = {o: (fresh_by[o][-1] if fresh_by[o] else None)
                         for o in live}
            rd = _Reader(make)
            cen = await CDB.route_books(
                c, fxs, now=clk["t"], reader=rd,
                reads=CDB.ROUTE_BOOK_MAX_READS, memo=memo,
                attempts=attempts, clock=lambda: clk["t"])
            calls = [x["slug"] for x in rd.calls]
            assert len(calls) == cen["reads_made"] <= 6, (k, calls)
            assert [r["market"] for r in cen["reads"]] == calls
            for s in live:
                i = insts[s]
                if s in calls:
                    # its OWN read this pass, its own measured age
                    assert i.book_refusal is None and \
                        i.book_source == "PMUS_ON_DEMAND_READ:TEST"
                    assert i.observed_at == clk["t"] and i.asks
                    fresh_by[s].append(k)
                else:
                    assert i.book_refusal == CDB.R_PMUS_ROUTE_BOOK_NOT_READ
            for s in dead:
                i = insts[s]
                assert i.asks == () and i.observed_at is None
                assert i.book_refusal is not None
                hold = DEAD_HOLD_PASSES.get(kind[s])
                if s in calls:
                    if hold is not None and read_at[s]:
                        # read again LAST: behind every open market that
                        # has waited since before its hold ended
                        end = read_at[s][-1] + hold
                        for o in live:
                            if o in calls and (prev_live[o] is None
                                               or prev_live[o] < end):
                                assert calls.index(o) < calls.index(s), \
                                    (k, calls)
                    read_at[s].append(k)
                elif hold is not None and read_at[s] and \
                        k - read_at[s][-1] < hold:
                    last = read_at[s][-1]
                    assert i.book_refusal == \
                        CDB.R_PMUS_ROUTE_BOOK_HELD_NOT_OPEN, (k, s)
                    assert i.book_detail["age_s"] == 300.0 * (k - last)
                    assert i.book_detail["state"] in (
                        "MARKET_STATE_CLOSED", "MARKET_STATE_EXPIRED")
                    assert i.book_detail["hold_left_s"] == \
                        300.0 * (last + hold - k)
        for s in live:
            got = fresh_by[s]
            assert got and got[0] < within, (s, got)
            for k in range(n_pass - within + 1):
                assert any(k <= p < k + within for p in got), (s, k, got)
        for s in dead:
            ks = read_at[s]
            assert ks and ks[0] < within, (s, ks)    # every market read
            hold = DEAD_HOLD_PASSES.get(kind[s])
            lo, hi = (hold, hold + within - 1) if hold is not None \
                else (1, within)
            gaps = [b - a for a, b in zip(ks, ks[1:])]
            assert all(lo <= g <= hi for g in gaps), (s, ks)
            # never starved: due again inside the run => read again
            assert ks[-1] + hi > n_pass - 1, (s, ks)
        assert len(attempts) == n_dead + n_live
    _run(go, monkeypatch)


@pg
def test_a_deferred_read_keeps_its_place_and_a_failed_read_goes_behind(
        monkeypatch):
    """(review 1) A read deferred with nothing sent (the venue's hold or
    429 cooldown) moves nothing: the same markets are read first on the
    next pass. A read that was SENT and failed goes behind every market not
    yet tried."""
    async def go(c):
        base = time.time()
        clk = {"t": base}
        fxs, insts, _d, live = _shape(base, dead=0, live=8)
        answer = {"how": "deferred"}

        def make(slug):
            if answer["how"] == "deferred":
                return {"marketData": None, "error": W.ROUTE_READ_DEFERRED,
                        "observed_at": clk["t"], "served_by": "TEST"}
            if answer["how"] == "failed":
                return {"marketData": None, "error": "HTTPStatusError",
                        "observed_at": clk["t"], "served_by": "TEST"}
            return {"marketData": _md(), "error": None,
                    "observed_at": clk["t"], "served_by": "TEST"}
        memo, attempts, order = {}, {}, []
        for k, how in enumerate(("deferred", "failed", "ok")):
            clk["t"] = base + 300.0 * k
            answer["how"] = how
            rd = _Reader(make)
            cen = await CDB.route_books(
                c, fxs, now=clk["t"], reader=rd, reads=6, memo=memo,
                attempts=attempts, clock=lambda: clk["t"])
            order.append([x["slug"] for x in rd.calls])
            if how == "deferred":
                assert cen["refused"] == {
                    CDB.R_PMUS_ROUTE_BOOK_READ_DEFERRED: 6,
                    CDB.R_PMUS_ROUTE_BOOK_NOT_READ: 2}
        assert order[0] == live[:6]
        assert order[1] == live[:6]              # deferred: kept its place
        assert order[2] == live[6:] + live[:4]   # failed: behind the untried
        assert all(insts[s].book_source == "PMUS_ON_DEMAND_READ:TEST"
                   for s in order[2])
    _run(go, monkeypatch)


@pg
def test_an_accepted_read_keeps_its_place_after_its_book_is_forgotten(
        monkeypatch):
    """(review 1) The accepted book leaves the memo after 900 s; the market
    keeps its place in the least-recently-attempted order and does not
    jump ahead of a market never read (13 markets, 6 reads a pass, passes
    950 s and 300 s apart: every market read within ceil(13 / 6) = 3)."""
    async def go(c):
        base = time.time()
        clk = {"t": base}
        fxs, insts, _d, live = _shape(base, dead=0, live=13)

        def make(slug):
            return {"marketData": _md(), "error": None,
                    "observed_at": clk["t"], "served_by": "TEST"}
        memo, attempts, order = {}, {}, []
        for dt in (0.0, 950.0, 1250.0):
            clk["t"] = base + dt
            rd = _Reader(make)
            await CDB.route_books(
                c, fxs, now=clk["t"], reader=rd, reads=6, memo=memo,
                attempts=attempts, clock=lambda: clk["t"])
            order.append([x["slug"] for x in rd.calls])
            if dt == 950.0:
                # the first pass's books were forgotten (> 900 s old)
                assert not set(live[:6]) & set(memo)
                assert set(live[:6]) <= set(attempts)
        assert order[0] == live[:6] and order[1] == live[6:12]
        assert order[2] == [live[12]] + live[:5]  # the never-read one first
        assert set(order[0] + order[1] + order[2]) == set(live)
    _run(go, monkeypatch)


@pg
def test_a_newer_open_book_releases_a_not_open_hold(monkeypatch):
    """(review 1) The venue's newest word wins: our read said CLOSED, then
    the paper runtime recorded the market OPEN -- the hold is released and
    the market is read again at once (and a not-open word younger than the
    route bound still refuses an older open book)."""
    async def go(c):
        base = time.time()
        clk = {"t": base}
        fxs, insts, _d, live = _shape(base, dead=0, live=1)
        s = live[0]
        await c.execute("DELETE FROM paper_book_observations "
                        " WHERE us_market_slug = $1", s)
        state = {"st": "MARKET_STATE_CLOSED"}

        def make(slug):
            return {"marketData": _md(state=state["st"]), "error": None,
                    "observed_at": clk["t"], "served_by": "TEST"}
        memo, attempts = {}, {}

        async def one(dt, reads=1):
            clk["t"] = base + dt
            insts[s].book_refusal = None
            rd = _Reader(make)
            cen = await CDB.route_books(
                c, fxs, now=clk["t"], reader=rd, reads=reads, memo=memo,
                attempts=attempts, clock=lambda: clk["t"])
            return rd, cen
        rd, _ = await one(0.0)
        assert len(rd.calls) == 1 and insts[s].book_refusal == \
            CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN
        # a paper read 5 s OLDER than our CLOSED read, open, 8 s later:
        # still the not-open word (the newest), no read inside the bound
        md = _md()
        await c.execute(
            "INSERT INTO paper_book_observations (us_market_slug, "
            " observed_at, source, bids, offers, market_state, read_basis) "
            " VALUES ($1, to_timestamp($2), 'TEST_PAPER', $3::jsonb, "
            " $4::jsonb, $5, 'TEST')", s, base - 5.0,
            json.dumps(md["bids"]), json.dumps(md["offers"]), md["state"])
        rd, _ = await one(8.0)
        assert rd.calls == [] and insts[s].book_refusal == \
            CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN
        rd, cen = await one(300.0)
        assert rd.calls == [] and insts[s].book_refusal == \
            CDB.R_PMUS_ROUTE_BOOK_HELD_NOT_OPEN
        assert insts[s].book_detail["hold_left_s"] == 600.0
        # the paper runtime records the market OPEN after our read: released
        await c.execute(
            "INSERT INTO paper_book_observations (us_market_slug, "
            " observed_at, source, bids, offers, market_state, read_basis) "
            " VALUES ($1, to_timestamp($2), 'TEST_PAPER', $3::jsonb, "
            " $4::jsonb, $5, 'TEST')", s, base + 400.0,
            json.dumps(md["bids"]), json.dumps(md["offers"]), md["state"])
        state["st"] = "MARKET_STATE_OPEN"
        rd, cen = await one(600.0)
        assert len(rd.calls) == 1
        assert insts[s].book_refusal is None and \
            insts[s].book_source == "PMUS_ON_DEMAND_READ:TEST"
    _run(go, monkeypatch)


# ── (review 2 of b5290832) the newest word governs in the pass that reads ─
#
# b5290832 claimed "a newer not-open word is never passed over for an older
# open book", but in the pass that MADE the read route_books fell back to the
# older open book whenever that book was inside the route bound, also when
# the read just made answered with the venue's word about the market (not
# open, crossed): the alias was costed on the older book, eligible, and the
# receipt never named the venue's newer answer.

#: what the venue's newer word looks like on the read, and its refusal
NEWER_WORDS = {
    "closed": ({"state": "MARKET_STATE_CLOSED"},
               CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN),
    "suspended": ({"state": "MARKET_STATE_SUSPENDED"},
                  CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN),
    "crossed": ({"bids": (("0.45", 100),)}, CDB.R_PMUS_ROUTE_BOOK_CROSSED),
}


async def _paper_row(c, slug, at, md=None):
    md = md or _md()
    await c.execute(
        "INSERT INTO paper_book_observations (us_market_slug, "
        " observed_at, source, bids, offers, market_state, read_basis) "
        " VALUES ($1, to_timestamp($2), 'TEST_PAPER', $3::jsonb, "
        " $4::jsonb, $5, 'TEST')", slug, at, json.dumps(md["bids"]),
        json.dumps(md["offers"]), md["state"])


def test_the_venues_word_is_newest_at_one_instant_and_needs_a_receipt():
    """PURE. (review 2) `market_word` reads the venue's word whatever the
    book's age; `newest_route_book` puts that word before a book carrying
    none at one receipt instant; `said_newer` is True only for a word with
    our receipt instant that is not older than the book."""
    nop, crx = CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN, CDB.R_PMUS_ROUTE_BOOK_CROSSED
    closed = _book(state="MARKET_STATE_CLOSED")
    crossed = _book(bids=[(Decimal("0.45"), 100)])
    w = CDB.market_word
    assert w(SLUG, closed) == nop and w(SLUG, crossed) == crx
    assert w(SLUG, _book(state="MARKET_STATE_SUSPENDED",
                         observed_at=1.0)) == nop          # any age
    assert w(SLUG, _book(state="MARKET_STATE_CLOSED",
                         observed_at=None)) == nop         # untimed: a word
    for none in (None, _book(), _book(error="HTTPStatusError"),
                 _book(error="VENUE_HOLD_IN_FORCE", state="CLOSED"),
                 _book(state="MARKET_STATE_CLOSED",
                       payload_slug="aec-mlb-bos-nyy-2026-10-07")):
        assert w(SLUG, none) is None
    open_ = _book(observed_at=1000.0)
    nb = CDB.newest_route_book
    # newest by receipt; at one instant the word first, in either order
    assert nb(SLUG, [open_, _book(state="CLOSED", observed_at=999.0)]) \
        is open_
    tie = _book(state="MARKET_STATE_CLOSED", observed_at=1000.0)
    assert nb(SLUG, [open_, tie]) is tie and nb(SLUG, [tie, open_]) is tie
    assert nb(SLUG, [None, open_]) is open_ and nb(SLUG, [None]) is None
    sn = CDB.said_newer
    assert sn(SLUG, _book(state="CLOSED", observed_at=1001.0), open_)
    assert sn(SLUG, tie, open_)                          # one instant
    assert sn(SLUG, _book(bids=[(Decimal("0.45"), 9)], observed_at=1000.0),
              open_)
    assert sn(SLUG, tie, None)
    assert not sn(SLUG, _book(state="CLOSED", observed_at=999.0), open_)
    assert not sn(SLUG, _book(state="CLOSED", observed_at=None), open_)
    assert not sn(SLUG, _book(observed_at=1001.0), open_)  # no word
    assert not sn(SLUG, _book(error="HTTPStatusError", observed_at=1001.0),
                  open_)


@pg
@pytest.mark.parametrize("word", sorted(NEWER_WORDS))
def test_a_newer_not_open_or_crossed_read_never_costs_the_older_open_book(
        monkeypatch, word):
    """(review 2) The recorded paper read is OPEN, 0.38 / 0.40 x 100 and
    22 s old: inside the 30 s route bound but within the 10 s lead, so the
    recording pass reads the market again -- and the venue answers that it
    is CLOSED, SUSPENDED or crossed (bid 0.45 > offer 0.40). At b5290832
    the pass fell back to the older open book: both PMUS candidates costed
    at 0.40 and eligible, book_detail OPEN / PAPER_BOOK_OBSERVATION. The
    newer word governs: refused by its name, never costed, the read's own
    source and state on the receipt; Kalshi still routes; SHADOW / NONE."""
    kw, why = NEWER_WORDS[word]

    async def go(c):
        await _seed(c, paper_age_s=22.0)
        rd = _Reader(_fresh(**kw))
        res = await W.claims_pass(_Pool(c), record=True, route_reader=rd)
        assert [x["slug"] for x in rd.calls] == [SLUG]
        rb = res["pmus_route_books"]
        assert rb["reads"] == [{"market": SLUG, "outcome": why}]
        assert rb["accepted"] == {} and rb["refused"] == {why: 1}, rb
        recs = await _receipts(c)
        pm = [x for r in recs for x in r["candidates"] if x["venue"] == PMUS]
        assert len(pm) == 2
        for x in pm:
            assert x["eligible"] is False and x["reason"] == why, x
            assert x["ask"] is None and "all_in" not in x
            d = x["book_detail"]
            assert d["source"] == "PMUS_ON_DEMAND_READ:TEST", d
            assert d["state"] == kw.get("state", "MARKET_STATE_OPEN")
            assert d["age_s"] < 22.0                 # the read, not the book
        for r in recs:
            assert r["mode"] == "SHADOW" and r["production_effect"] == "NONE"
            assert r["chosen"] and {a["venue"] for a in
                                    r["chosen"]["allocations"]} == {KALSHI}
            assert not any(x["venue"] == PMUS and x["eligible"]
                           for x in r["candidates"])
        row = await c.fetchrow(
            "SELECT best_ask, observed_at FROM canonical_claim_aliases "
            " WHERE alias_key = $1", "%s|%s|YES" % (PMUS, SLUG))
        assert row["best_ask"] is None and row["observed_at"] is None
    _run(go, monkeypatch)


@pg
@pytest.mark.parametrize("word", sorted(NEWER_WORDS))
def test_the_newer_word_governs_in_the_read_pass_and_every_pass_after(
        monkeypatch, word):
    """(review 2) route_books at a controlled clock. An OPEN paper book 22 s
    old; the read at +0 answers the word: refused by its name in that pass
    (the read's age 0 on the receipt); at +5 with no read budget (a
    non-recording pass) and at +8 with one the word still refuses the open
    book (27-30 s old, inside the bound) and nothing is read; at +300 a
    not-open word is held, a crossed one is read again."""
    kw, why = NEWER_WORDS[word]

    async def go(c):
        base = float(int(time.time()))
        clk = {"t": base}
        fxs, insts, _d, live = _shape(base, dead=0, live=1,
                                      prefix="aec-nhl-rc63w")
        s = live[0]
        await c.execute("DELETE FROM paper_book_observations "
                        " WHERE us_market_slug = $1", s)
        await _paper_row(c, s, base - 22.0)
        ans = {"kw": kw}

        def make(slug):
            return {"marketData": _md(**ans["kw"]), "error": None,
                    "observed_at": clk["t"], "served_by": "TEST"}
        memo, attempts = {}, {}

        async def one(dt, reads):
            clk["t"] = base + dt
            insts[s].book_refusal = None
            rd = _Reader(make)
            cen = await CDB.route_books(
                c, fxs, now=clk["t"], reader=rd if reads else None,
                reads=reads, memo=memo, attempts=attempts,
                clock=lambda: clk["t"])
            return rd, cen, insts[s]
        rd, cen, i = await one(0.0, 6)
        assert len(rd.calls) == 1 and cen["reads"] == [
            {"market": s, "outcome": why}]
        assert i.book_refusal == why and i.asks == () and \
            i.observed_at is None and i.book_source is None
        assert i.book_detail["source"] == "PMUS_ON_DEMAND_READ:TEST"
        assert i.book_detail["age_s"] == 0.0
        assert i.book_detail["state"] == kw.get("state", "MARKET_STATE_OPEN")
        ans["kw"] = {}                              # would answer OPEN now
        for dt, reads in ((5.0, 0), (8.0, 6)):
            rd, cen, i = await one(dt, reads)
            assert rd.calls == [] and cen["reads_made"] == 0
            assert i.book_refusal == why and i.observed_at is None, (dt, i)
            assert i.book_detail["source"] == "PMUS_ON_DEMAND_READ:TEST"
            assert i.book_detail["age_s"] == dt
        rd, cen, i = await one(300.0, 6)
        if why == CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN:
            assert rd.calls == []
            assert i.book_refusal == CDB.R_PMUS_ROUTE_BOOK_HELD_NOT_OPEN
            assert i.book_detail["hold_left_s"] == 600.0
        else:                                       # crossed: not held
            assert len(rd.calls) == 1 and i.book_refusal is None
            assert i.book_source == "PMUS_ON_DEMAND_READ:TEST"
    _run(go, monkeypatch)


@pg
@pytest.mark.parametrize("case", [
    "tie-closed", "tie-crossed", "older-closed", "failed", "deferred",
    "mismatch", "no-receipt-closed", "stale-closed"])
def test_only_a_read_with_no_newer_word_falls_back_to_the_open_book(
        monkeypatch, case):
    """(review 2) The open paper book (22 s old) is costed after the read
    only when the read said nothing newer about the market: it failed, was
    deferred, answered another market, carries no receipt instant, or its
    receipt is older than the book (past the bound, or a word 6 s older --
    the newer open book governs, as on every later pass). A word at the
    book's very receipt instant governs, in the read pass and on the next
    pass without a read (27 s: the open book is still inside the bound)."""
    async def go(c):
        base = float(int(time.time()))
        clk = {"t": base}
        fxs, insts, _d, live = _shape(base, dead=0, live=1,
                                      prefix="aec-nhl-rc63f")
        s = live[0]
        await c.execute("DELETE FROM paper_book_observations "
                        " WHERE us_market_slug = $1", s)
        paper_at = base - 22.0
        await _paper_row(c, s, paper_at)
        closed = _md(state="MARKET_STATE_CLOSED")
        crossed = _md(bids=(("0.45", 100),))
        got = {
            "tie-closed": (closed, paper_at, None),
            "tie-crossed": (crossed, paper_at, None),
            "older-closed": (closed, base - 28.0, None),
            "failed": (None, base, "HTTPStatusError"),
            "deferred": (None, base, W.ROUTE_READ_DEFERRED),
            "mismatch": (_md(state="MARKET_STATE_CLOSED",
                             slug="aec-nhl-rc63f-99-2026-10-10"), base, None),
            "no-receipt-closed": (closed, None, None),
            "stale-closed": (closed, base - 120.0, None)}[case]

        def make(slug):
            md, at, err = got
            return {"marketData": md, "error": err, "observed_at": at,
                    "served_by": "TEST"}
        memo, attempts = {}, {}
        governs = case.startswith("tie-")
        for dt, reads in ((0.0, 6), (3.0, 0)):
            clk["t"] = base + dt
            insts[s].book_refusal = None
            rd = _Reader(make)
            cen = await CDB.route_books(
                c, fxs, now=clk["t"], reader=rd if reads else None,
                reads=reads, memo=memo, attempts=attempts,
                clock=lambda: clk["t"])
            i = insts[s]
            assert len(rd.calls) == (1 if reads else 0)
            if governs:
                why = CDB.R_PMUS_ROUTE_BOOK_CROSSED if "crossed" in case \
                    else CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN
                assert i.book_refusal == why and i.asks == () and \
                    i.observed_at is None, (case, dt, i)
                assert cen["accepted"] == {}
            else:
                assert i.book_refusal is None, (case, dt, i)
                assert i.book_source == "PAPER_BOOK_OBSERVATION:TEST_PAPER"
                assert i.observed_at == paper_at and i.asks
                assert i.book_detail["state"] == "MARKET_STATE_OPEN"
    _run(go, monkeypatch)


@pg
def test_a_paper_recorded_ended_word_holds_only_while_pmus_book_returns_it(
        monkeypatch):
    """(review 2, the claim narrowed to what is enforced) A word only the
    PAPER RUNTIME recorded is seen only while `pmus_book` returns it
    (PMUS_BOOK_WINDOW_S, 900 s): an ENDED word (EXPIRED) 100 s old holds the
    market until it drops out of that window at +800 s -- not for
    ROUTE_BOOK_ENDED_HOLD_S (3600 s) -- the market, never read on demand, is
    then read (nothing remembers a word of it), and from then on OUR OWN
    word governs, the 3600 s ENDED hold included."""
    assert CDB.PMUS_BOOK_WINDOW_S == 900.0 and \
        CDB.ROUTE_BOOK_ENDED_HOLD_S == 3600.0

    async def go(c):
        base = float(int(time.time()))
        clk = {"t": base}
        fxs, insts, _d, live = _shape(base, dead=0, live=1,
                                      prefix="aec-nhl-rc63p")
        s = live[0]
        await c.execute("DELETE FROM paper_book_observations "
                        " WHERE us_market_slug = $1", s)
        await _paper_row(c, s, base - 100.0, _md(state="MARKET_STATE_EXPIRED"))

        def make(slug):
            return {"marketData": _md(state="MARKET_STATE_EXPIRED"),
                    "error": None, "observed_at": clk["t"],
                    "served_by": "TEST"}
        memo, attempts = {}, {}

        async def one(dt):
            clk["t"] = base + dt
            insts[s].book_refusal = None
            rd = _Reader(make)
            await CDB.route_books(c, fxs, now=clk["t"], reader=rd, reads=6,
                                  memo=memo, attempts=attempts,
                                  clock=lambda: clk["t"])
            return rd, insts[s]
        held = CDB.R_PMUS_ROUTE_BOOK_HELD_NOT_OPEN
        for dt, left in ((0.0, 3500.0), (799.0, 2701.0)):   # the word is seen
            rd, i = await one(dt)
            assert rd.calls == [] and i.book_refusal == held, (dt, i)
            assert i.book_detail["hold_left_s"] == left
            assert i.book_detail["source"] == \
                "PAPER_BOOK_OBSERVATION:TEST_PAPER"
        rd, i = await one(801.0)               # dropped out of the window
        assert [x["slug"] for x in rd.calls] == [s]
        assert i.book_refusal == CDB.R_PMUS_ROUTE_BOOK_NOT_OPEN
        assert i.book_detail["source"] == "PMUS_ON_DEMAND_READ:TEST"
        rd, i = await one(1101.0)              # our own word: 3600 s, ENDED
        assert rd.calls == [] and i.book_refusal == held, i
        assert i.book_detail["hold_left_s"] == 3300.0
        assert i.book_detail["source"] == "PMUS_ON_DEMAND_READ:TEST"
    _run(go, monkeypatch)


@pg
def test_the_route_book_writes_nothing_and_places_nothing(monkeypatch):
    """No capital effect: the pass writes only its SHADOW evidence tables
    (aliases, receipts, certificates) -- no paper observation, order, fill
    or ledger row, and no Kalshi live table."""
    async def go(c):
        await _seed(c, paper_age_s=600.0)
        tables = [r["t"] for r in await c.fetch(
            "SELECT table_name AS t FROM information_schema.tables "
            " WHERE table_schema = 'public' AND (table_name LIKE 'paper_%' "
            "   OR table_name LIKE 'kalshi_live_%' "
            "   OR table_name LIKE 'execution_%' "
            "   OR table_name LIKE 'execmirror_%')")]
        assert "paper_book_observations" in tables
        before = {t: await c.fetchval('SELECT count(*) FROM "%s"' % t)
                  for t in tables}
        await W.claims_pass(_Pool(c), record=True,
                            route_reader=_Reader(_fresh()))
        after = {t: await c.fetchval('SELECT count(*) FROM "%s"' % t)
                 for t in tables}
        assert before == after
    _run(go, monkeypatch)
