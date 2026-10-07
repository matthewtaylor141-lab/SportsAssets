"""KALSHI x POLYMARKET US -> CANONICAL CLAIM INSTRUMENTS (Kalshi Canonical
Venue V1). Pure builders; no network, no database, no order path.

  kalshi_instruments   one ESTABLISHED Kalshi game fixture -> for every team
                       (and TIE) market, its YES and its NO as two separate
                       executable instruments (asks from the documented
                       reciprocal book), with the market's own parsed rules
  pmus_instruments     one PMUS game moneyline (one market, LONG = team a,
                       SHORT = BUY_SHORT on the same single-instrument book,
                       asks at 1 - bid -- the venue's own intent) -> YES / NO
  map_pmus             the PMUS moneyline for a Kalshi fixture: the package's
                       exact fixture contract over LEAGUE-NAMESPACED team
                       keys (MLB:MIL), the start within 15 minutes, exactly
                       one candidate (two = AMBIGUOUS, refused). PMUS states
                       no home / away, so the participant SET is compared
                       and the Kalshi milestone's home / away is carried;
                       a PMUS record that ever states home / away must agree.
Titles are never read for identity.
"""
from __future__ import annotations

from decimal import Decimal

from . import canonical_claims as CC
from . import kalshi_market_data as KMD
from .canonical_venue import mapping_contract as MC

VERSION = "KALSHI_CLAIMS_V1"
KALSHI = "KALSHI"
POLYMARKET_US = "POLYMARKET_US"
START_TOLERANCE_S = 15 * 60
PMUS_BOOK_BASIS = "PMUS_SINGLE_INSTRUMENT_BOOK_BUY_SHORT_AT_1_MINUS_BID"
#: PMUS league code (us_premap team_league) -> canonical league
PMUS_LEAGUE = {"mlb": "MLB", "nba": "NBA", "wnba": "WNBA", "nhl": "NHL",
               "nfl": "NFL", "cfb": "NCAAF", "cbb": "NCAAB", "epl": "EPL",
               "mls": "MLS"}


def event_key(league: str, start_epoch: float, away: str, home: str) -> str:
    import datetime as dt
    t = dt.datetime.fromtimestamp(float(start_epoch), dt.timezone.utc)
    return "%s:%s:%s@%s" % (league, t.strftime("%Y-%m-%dT%H:%MZ"),
                            away.upper(), home.upper())


def fixture_of(k: KMD.KalshiFixture) -> CC.Fixture | None:
    if k.status != "ESTABLISHED":
        return None
    kind = "THREE_WAY" if k.outcome_kind == "THREE_WAY" else "TWO_WAY"
    return CC.Fixture(
        event_key=event_key(k.league, k.start_epoch, k.away_code,
                            k.home_code),
        sport=k.sport, league=k.league, start_epoch=k.start_epoch,
        outcome_kind=kind, home=k.home_code, away=k.away_code,
        mapping_basis="KALSHI_MILESTONE_STRUCTURED_IDS")


def _settlement(evidence: dict | None) -> tuple:
    ev = evidence or {}
    st = "PROVEN" if ev.get("status") == "ESTABLISHED" else "NOT_PROVEN"
    s = dict(ev.get("settlement") or {})
    if ev.get("verification_sources"):
        s["verification_sources"] = list(ev["verification_sources"])
    return s, st


def kalshi_instruments(k: KMD.KalshiFixture, markets: dict, books: dict,
                       evidence: dict, *, sport: str | None = None) -> list:
    """`markets` {ticker: market}, `books` {ticker: book_from_orderbook},
    `evidence` {ticker: settlement_rule_registry.kalshi_rule_evidence}."""
    out = []
    role = {}
    for t in k.team_tickers:
        sid = KMD.strike_team_id(markets.get(t) or {})
        if sid is not None:
            role[t] = "HOME" if sid == k.home_id else "AWAY" \
                if sid == k.away_id else None
            continue
        # read back from the persisted fixture: the ticker's own code, which
        # fixture_from validated against the structured strike id
        code = t[len(k.event_ticker) + 1:].upper() \
            if t.startswith(k.event_ticker + "-") else None
        role[t] = "HOME" if code and code == k.home_code else "AWAY" \
            if code and code == k.away_code else None
    if k.tie_ticker:
        role[k.tie_ticker] = "DRAW"
    for t, subj in sorted(role.items()):
        if subj is None:
            continue
        s, st = _settlement(evidence.get(t))
        b = books.get(t) or {}
        code = (k.home_code if subj == "HOME" else k.away_code
                if subj == "AWAY" else "TIE")
        for side, asks in (("YES", b.get("yes_asks")),
                           ("NO", b.get("no_asks"))):
            out.append(CC.Instrument(
                venue=KALSHI, market_id=t, side=side, subject=subj,
                settlement=s, settlement_status=st,
                mapping_status=k.status,
                asks=tuple(asks or ()),
                observed_at=b.get("observed_at") if b.get("readable")
                else None,
                book_basis=KMD.ORDERBOOK_PROTOCOL["basis"]
                if b.get("readable") else None,
                sport=sport or k.sport, team_code=code,
                rules_sha256=(evidence.get(t) or {}).get("rules_sha256")))
    return out


def map_pmus(k: KMD.KalshiFixture, candidates: list) -> dict:
    """The one PMUS moneyline for this Kalshi fixture, or NOT_ESTABLISHED.
    candidates: [{slug, league (pmus code), team_a, team_b, start_epoch,
    home?, away?}] (team_a = the LONG side's abbreviation)."""
    if k.status != "ESTABLISHED":
        return {"status": "NOT_ESTABLISHED",
                "reasons": ["KALSHI_FIXTURE_NOT_ESTABLISHED"] +
                list(k.reasons)}
    canon = MC.FixtureIdentity(
        sport=k.sport, league=k.league,
        home_key="%s:%s" % (k.league, k.home_code),
        away_key="%s:%s" % (k.league, k.away_code),
        start_epoch=k.start_epoch)
    good, why = [], {}
    for c in candidates:
        lg = PMUS_LEAGUE.get(str(c.get("league") or "").lower())
        a = str(c.get("team_a") or "").upper()
        b = str(c.get("team_b") or "").upper()
        if c.get("home") and c.get("away"):
            home, away = str(c["home"]).upper(), str(c["away"]).upper()
            basis = "PMUS_STATED_HOME_AWAY"
        elif {a, b} == {k.home_code, k.away_code}:
            home, away = k.home_code, k.away_code
            basis = "PARTICIPANT_SET_PMUS_STATES_NO_HOME_AWAY"
        else:
            home, away = a, b
            basis = "PARTICIPANT_SET_PMUS_STATES_NO_HOME_AWAY"
        cand = MC.VenueFixture(
            venue=POLYMARKET_US, sport=k.sport if lg else None, league=lg,
            home_key="%s:%s" % (lg, home) if lg and home else None,
            away_key="%s:%s" % (lg, away) if lg and away else None,
            start_epoch=c.get("start_epoch"), structured_basis=basis)
        d = MC.map_fixture_exact(canon, cand,
                                 start_tolerance_s=START_TOLERANCE_S)
        if d.status == "ESTABLISHED":
            good.append(dict(c, basis=basis))
        else:
            why[str(c.get("slug"))] = list(d.reasons)
    if len(good) == 1:
        return {"status": "ESTABLISHED", "pmus": good[0], "reasons": []}
    if len(good) > 1:
        return {"status": "NOT_ESTABLISHED",
                "reasons": ["AMBIGUOUS:%d" % len(good)],
                "candidates": [g["slug"] for g in good]}
    return {"status": "NOT_ESTABLISHED", "reasons": ["NO_CANDIDATE"],
            "rejected": dict(list(why.items())[:10])}


def pmus_instruments(k: KMD.KalshiFixture, m: dict, *, evidence: dict | None,
                     book: dict | None, sport: str | None = None) -> list:
    """m = map_pmus(...)['pmus']: LONG is YES on team_a; SHORT is the NO of
    the same market (BUY_SHORT at 1 - bid, the venue's own intent)."""
    a = str(m.get("team_a") or "").upper()
    subj = "HOME" if a == k.home_code else "AWAY" if a == k.away_code \
        else None
    if subj is None:
        return []
    s, st = _settlement(evidence)
    b = book or {}
    offers = tuple(sorted(((Decimal(str(p)), int(q)) for p, q in
                           (b.get("offers") or ()) if int(q) >= 1),
                          key=lambda z: z[0]))
    short = tuple(sorted(((Decimal(1) - Decimal(str(p)), int(q)) for p, q in
                          (b.get("bids") or ()) if int(q) >= 1),
                         key=lambda z: z[0]))
    obs = b.get("observed_at")
    common = dict(venue=POLYMARKET_US, market_id=str(m["slug"]),
                  subject=subj, settlement=s, settlement_status=st,
                  mapping_status="ESTABLISHED", observed_at=obs,
                  book_basis=PMUS_BOOK_BASIS if obs is not None else None,
                  sport=sport or k.sport, team_code=a,
                  rules_sha256=(evidence or {}).get("rules_sha256"))
    return [CC.Instrument(side="YES", asks=offers, **common),
            CC.Instrument(side="NO", asks=short, **common)]
