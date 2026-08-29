"""Polymarket US venue adapter (the regulated DCM behind the US mobile app).

The US exchange is a separate venue from the global CLOB: its own order books,
per-outcome markets (one market per team/outcome, grouped by eventSlug), its
own identifiers, and Ed25519 API-key auth (keys minted at polymarket.us/developer
with the same login as the mobile app). The source whale trades on the global
CLOB, so every copy needs a mapping step from the global market/outcome to a
US market slug — a copy is only placed when that mapping is verified.

Safety on top of the executor's caps/kill-switch:
  * LONG-side orders only. If the whale's outcome only exists as the other
    team's market, we skip — short-contract price semantics are not assumed.
  * orders.preview() first: the venue's own costing must agree with ours
    (within 2%) before any real order is created.
  * FOK limit orders only, integer contracts, whole-cent prices.

All functions here are sync (the SDK is httpx-based); callers run them in a
thread. No function is reachable unless PMUS_KEY_ID/PMUS_SECRET_KEY are set.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .config import settings

log = logging.getLogger(__name__)

_client = None
MATCH_FLOOR = 0.85  # minimum similarity for a verified outcome match
PREVIEW_COST_TOLERANCE = 1.02  # venue-computed cost may exceed ours by ≤2%


# ── No-stack referee (owner 2026-08-08: "trades are higher than $10 per
# trade") ────────────────────────────────────────────────────────────
# Copies and the edge engine run separate ledgers, so each respected its
# own $10 clip while together building $13-16 positions on the same
# outcome. The venue account is the one referee every sleeve can see:
# before any autonomous buy, ask it whether the market is already held.
_pos_cache: dict = {"ts": 0.0, "slugs": frozenset()}
_POS_TTL = 20.0
# Fail-open was designed for 20-second blips; the 2026-08-11 venue
# maintenance stretched "the last snapshot" to HOURS and the no-stack
# guard answered all morning with 6am truth while positions stacked to
# $318. Beyond this bound the snapshot refuses instead of guessing.
_POS_MAX_STALE_S = 600.0


def position_side(us_market_slug: str) -> float | None:
    """Signed net position on this market, or None when unreadable.

    THE LAST LINK (venue ground truth 2026-08-24): every two-sided
    market on this venue shares ONE identifier between its sides,
    distinguished only by long/short, so `intent` is what selects a
    side. That means the ONLY proof that BUY_SHORT actually bought the
    short side is the position we end up holding: a long fill nets
    positive, a short fill nets negative. Read fresh — this is
    verification, not a cache lookup."""
    try:
        client = _get_client()
        cursor = ""
        for _ in range(5):
            resp = client.portfolio.positions(
                {"limit": 100,
                 **({"cursor": cursor} if cursor else {})}) or {}
            for slug, p in (resp.get("positions") or {}).items():
                if str(slug).lower() == us_market_slug.lower():
                    try:
                        return float((p or {}).get("netPosition") or 0)
                    except (TypeError, ValueError):
                        return None
            cursor = resp.get("nextCursor") or ""
            if not cursor:
                break
    except Exception:  # noqa: BLE001 — unreadable is not a verdict
        return None
    return None


def account_holds(us_market_slug: str) -> bool:
    """True when the account already holds ANY open position on this US
    market. A read error reuses the last snapshot (a BLIP must not starve
    the profitable copy sleeve) — but only within _POS_MAX_STALE_S: past
    that bound the answer is HELD (refuse), because a snapshot hours old
    is not truth, it is the 2026-08-11 stacking incident."""
    import time as _t

    now = _t.time()
    if now - _pos_cache["ts"] >= _POS_TTL:
        try:
            client = _get_client()
            held: set[str] = set()
            cursor = ""
            for _ in range(5):  # bounded paging
                resp = client.portfolio.positions(
                    {"limit": 100,
                     **({"cursor": cursor} if cursor else {})}) or {}
                for slug, p in (resp.get("positions") or {}).items():
                    try:
                        if float((p or {}).get("netPosition") or 0) > 0:
                            held.add(slug)
                    except (TypeError, ValueError):
                        held.add(slug)
                cursor = resp.get("nextCursor") or ""
                if resp.get("eof") or not cursor:
                    break
            _pos_cache.update(ts=now, slugs=frozenset(held))
        except Exception:  # noqa: BLE001 — stale snapshot beats blindness
            log.warning("account_holds: positions read failed; using "
                        "stale snapshot", exc_info=True)
    if now - _pos_cache["ts"] > _POS_MAX_STALE_S:
        # Too stale to trust either answer: report HELD so the caller
        # refuses the buy. A venue we cannot read for 10+ minutes is an
        # outage, and an outage must starve the sleeve, not blind it.
        log.warning("account_holds: snapshot %.0fs stale — failing "
                    "CLOSED for %s", now - _pos_cache["ts"], us_market_slug)
        return True
    return us_market_slug in _pos_cache["slugs"]


def _get_client():
    global _client
    if _client is not None:
        return _client
    from polymarket_us import PolymarketUS

    cfg = settings()
    if cfg.pmus_key_id and cfg.pmus_secret_key:
        _client = PolymarketUS(key_id=cfg.pmus_key_id, secret_key=cfg.pmus_secret_key)
    else:
        _client = PolymarketUS()  # public endpoints only (market data, mapping)
    return _client


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def _sim(a: str | None, b: str | None) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _outcome_score(us_market: dict, outcome: str | None) -> float:
    """How well a US per-outcome market matches the whale's bought outcome."""
    team = us_market.get("team") or {}
    candidates = [us_market.get("outcome"), team.get("name"), team.get("alias"),
                  team.get("safeName"), team.get("abbreviation")]
    # On the real venue many per-outcome markets carry NO outcome field —
    # the market TITLE names the side ("New York Yankees", "Eagles -7.5").
    # The engine's own adapter learned this the hard way (see
    # edge/venues/polymarket_us.py "outcome_from_title"). Guard: a title
    # that reads like a matchup ("A vs B") names BOTH sides and must
    # never vote, and slug-looking strings are noise.
    title = us_market.get("title") or ""
    # Matchup shapes name BOTH sides and must never vote — and the venue
    # writes matchups with more separators than ' vs': 'A - B' and
    # 'A @ B' both let containment score EITHER team 1.0 (wrong-side
    # incident 2026-08-23: sideless parent orders on two-sided markets).
    _tl = f" {title.lower()} "
    if title and " vs" not in _tl and " - " not in _tl \
            and " @ " not in _tl and title.count("-") < 4:
        candidates.append(title)
    # The REAL schema (named by the 2026-08-04 trails): markets carry a
    # natural-language `question` — "Will the Los Angeles Dodgers cover
    # -1.5 vs the Chicago Cubs...", "Will CA Huracan win against CA
    # Tucuman...". The subject of cover/win IS the side; extract it.
    # "Who will win X vs Y" questions name no side and never vote.
    q = us_market.get("question") or ""
    if q:
        mq = re.search(r"^will (?:the )?(.+?) (?:cover|win)", _norm(q))
        if mq:
            candidates.append(mq.group(1))
    best = max((_sim(c, outcome) for c in candidates if c), default=0.0)
    # Player-name robustness: "Bianca Andreescu" vs the venue's
    # "Andreescu, B." scores ~0.68 on raw similarity — below the floor —
    # while the surname is an exact token match. A distinctive surname
    # (>3 chars) appearing as a whole token scores 0.9: above the floor,
    # below a full-string match. Known blind spot: two players sharing a
    # surname in one event (sisters); the FOK-at-his-price order bounds
    # the cost of that rare wrong-sibling case to one contract.
    out_last = (_norm(outcome).split() or [""])[-1] if outcome else ""
    if len(out_last) > 3:
        for c in candidates:
            if c and out_last in _norm(c).split():
                best = max(best, 0.9)
    # A bare Yes/No pick is a POSITION on the market's statement, not a
    # name — it must never similarity-match a named side (quarantine
    # stream 2026-08-24: pick=No mapped onto the team's OWN side, the
    # exact inverse of the whale's bet). Only a literal Yes/No side
    # may match a Yes/No pick.
    on_full = _norm(outcome)
    if on_full in ("yes", "no"):
        return 1.0 if any(c and _norm(c) == on_full
                          for c in candidates) else 0.0
    return best


def _amount(price: float) -> dict:
    return {"value": f"{price:.2f}", "currency": "USD"}


def _clean_title(t: str | None) -> str | None:
    """A searchable matchup from a global market title.

    The whales' titles carry market decorations the US venue's search
    chokes on ("Spread: Atlanta Dream (-2.5)", "Halmstads BK vs. IK
    Sirius: O/U 3.5", "Celtic FC vs. Dundee FC - More Markets"). All 305
    of the first live copy attempts died in search with these — strip to
    the matchup itself.
    """
    if not t:
        return None
    t = t.split(" - More Markets")[0]
    t = re.sub(r"^(Spread|Total|Moneyline|O/U)\s*:\s*", "", t)
    if ":" in t:
        # Keep the side holding the matchup: "Canadian Open: A vs B" wants
        # the right side; "A vs. B: O/U 3.5" wants the left.
        parts = t.split(":")
        vs = [p for p in parts if " vs" in p.lower()]
        t = vs[-1] if vs else max(parts, key=len)
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -")
    return t or None


def _surname_matchup(t: str | None) -> str | None:
    """"Bartunkova vs Andreescu" from "Nikola Bartunkova vs Bianca
    Andreescu" — venues abbreviate or reorder first names, and every
    tennis copy attempt on 2026-08-03 missed the match floor over
    exactly that."""
    c = _clean_title(t)
    if not c or " vs" not in c.lower():
        return None
    sides = re.split(r"\s+vs\.?\s+", c, flags=re.I)
    if len(sides) != 2:
        return None
    a = sides[0].strip().split()[-1] if sides[0].strip() else ""
    b = sides[1].strip().split()[-1] if sides[1].strip() else ""
    if len(a) > 2 and len(b) > 2:
        return f"{a} vs {b}"
    return None


def event_board(event_slug: str) -> list[dict]:
    """Every ACTIVE market the US venue lists for one event — the full
    game board (moneyline, spreads, totals, segments, props), each row
    orderable by its OWN slug (owner order 2026-08-12: "All markets
    that are available on Kalshi or Polymarket for every single game
    needs to be shown"). Two-sided markets expand one row per
    marketSide: the side identifier IS the orderable slug (the tennis
    path's discovery, 2026-08-04). The venue quietly ignores filters
    it doesn't recognize, so positive eventSlug mismatches are dropped
    exactly like resolve_market does."""
    client = _get_client()
    try:
        resp = client.markets.list({"eventSlug": [event_slug],
                                    "active": True})
        got = list((resp or {}).get("markets") or [])
    except Exception:  # noqa: BLE001 — an empty board, never a 500
        return []
    rows: list[dict] = []

    def _px(v) -> float | None:
        try:
            f = float(v)
            return f if 0 < f < 1 else None
        except (TypeError, ValueError):
            return None

    for m in got:
        if (m.get("eventSlug") or m.get("event_slug")) not in (None,
                                                               event_slug):
            continue
        if m.get("closed"):
            continue
        title = (_clean_title(m.get("question") or m.get("title"))
                 or m.get("slug") or "")
        sides = [s for s in (m.get("marketSides") or [])
                 if isinstance(s, dict)]
        if sides:
            for s in sides:
                ident, desc = s.get("identifier"), s.get("description")
                if not ident or not desc:
                    continue
                rows.append({"us_slug": ident,
                             "label": f"{title} — {desc}",
                             "price": _px(s.get("price"))})
        elif m.get("slug"):
            px = next((p for p in (_px(m.get(k)) for k in
                                   ("bestAsk", "best_ask", "price"))
                       if p is not None), None)
            label = (f"{title} — {m['outcome']}" if m.get("outcome")
                     else title)
            rows.append({"us_slug": m["slug"], "label": label,
                         "price": px})
    return rows


# ── Venue-native desk listing (owner order 2026-08-21: the desk must
# navigate like the venue itself) ────────────────────────────────────
# The catalog-first board joined venue events against GLOBAL slugs and
# silently dropped every event whose slug spelling differed — tennis
# most of all (alternate totals and game/set spreads never rendered).
# The venue's own event listing is the desk's source of truth now:
# every event it lists, every market on each event, labeled with the
# venue's own words. 30s cache: the desk re-quotes at order time, so
# browse staleness is cosmetic.
_desk_cache: dict = {"ts": 0.0, "events": []}
_DESK_TTL_S = 30.0


def _ev_volume_usd(ev: dict) -> float | None:
    """Traded volume in dollars from the venue's own event row (desk v8
    feed cards sort on it). The listing payloads have carried the figure
    under several spellings across venue revisions — probe them
    defensively, TOTAL volume preferred, liquidity as the last resort.
    None when the venue doesn't say: volume is never invented."""
    for k in ("volume", "volumeNum", "volume24hr", "liquidity"):
        v = ev.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def list_desk_events() -> list[dict]:
    """Every active event on the US venue with its full market board.

    [{slug, title, league, volume_usd, close_time,
      markets: [{us_slug, label, price, kind}]}]
    kind is the venue slug-grammar prefix (atc/aec moneyline, asc
    spread, tsc total, astatc prop, ...) — the desk groups by it.
    volume_usd/close_time come straight off the venue's event row
    (null when absent) — the v8 feed sorts and labels cards with
    them."""
    import time as _t

    now = _t.time()
    if now - _desk_cache["ts"] < _DESK_TTL_S and _desk_cache["events"]:
        return _desk_cache["events"]
    client = _get_client()

    def _px(v) -> float | None:
        try:
            f = float(v)
            return f if 0 < f < 1 else None
        except (TypeError, ValueError):
            return None

    # The venue's event listing answers EMPTY without the right param
    # variant (the edge adapter's census proved this in production —
    # its _list_variants probe exists for exactly this reason). Probe
    # the same ladder, most specific first, then page with the winner.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    _now2 = _dt.now(_tz.utc)
    variants = (
        {"active": True, "closed": False,
         "startTimeMin": _iso(_now2 - _td(hours=12)),
         "startTimeMax": _iso(_now2 + _td(hours=96))},
        {"active": True, "closed": False},
        {"active": True},
        {},
    )
    variant = None
    for v in variants:
        try:
            probe = client.events.list({"limit": 100, **v}) or {}
        except Exception:  # noqa: BLE001
            continue
        if probe.get("events"):
            variant = v
            break
    if variant is None:
        return _desk_cache["events"]
    events: dict[str, dict] = {}
    offset = 0
    for _ in range(14):                      # bounded paging
        try:
            resp = client.events.list(
                {"limit": 100, "offset": offset, **variant}) or {}
        except Exception:  # noqa: BLE001 — stale cache beats a 500
            break
        got = resp.get("events") or []
        if not got:
            break
        for ev in got:
            eslug = ev.get("slug") or ev.get("eventSlug") or ""
            if not eslug:
                continue
            e = events.setdefault(eslug, {
                "slug": eslug,
                "title": _clean_title(ev.get("title")) or eslug,
                "league": (eslug.split("-", 1)[0] or "").lower(),
                "start": ev.get("startTime") or ev.get("startDate"),
                "volume_usd": _ev_volume_usd(ev),
                "close_time": (ev.get("endTime") or ev.get("endDate")
                               or None),
                "markets": []})
            for m in ev.get("markets") or []:
                if m.get("closed"):
                    continue
                title = (_clean_title(m.get("question")
                                      or m.get("title"))
                         or m.get("slug") or "")
                sides = [x for x in (m.get("marketSides") or [])
                         if isinstance(x, dict)]
                if sides:
                    for x in sides:
                        ident = x.get("identifier")
                        desc = x.get("description")
                        if not ident or not desc:
                            continue
                        e["markets"].append({
                            "us_slug": ident,
                            "kind": (ident.split("-", 1)[0]
                                     or "").lower(),
                            "label": f"{title} — {desc}",
                            "price": _px(x.get("price"))})
                elif m.get("slug"):
                    px = next((p for p in (_px(m.get(k)) for k in
                               ("bestAsk", "best_ask", "price"))
                               if p is not None), None)
                    e["markets"].append({
                        "us_slug": m["slug"],
                        "kind": (m["slug"].split("-", 1)[0]
                                 or "").lower(),
                        "label": (f"{title} — {m['outcome']}"
                                  if m.get("outcome") else title),
                        "price": px})
        if len(got) < 100:
            break
        offset += 100
    out = [e for e in events.values() if e["markets"]]
    if out:
        _desk_cache.update(ts=now, events=out)
    return _desk_cache["events"] if _desk_cache["events"] else out


def slug_ask(us_slug: str) -> float | None:
    """Live ask for one orderable US slug, tolerant of both market
    shapes (marketSide identifier or plain market). None when the
    venue has no readable quote — the caller refuses, never guesses."""
    client = _get_client()
    try:
        m = (client.markets.retrieve_by_slug(us_slug) or {}).get(
            "market") or {}
    except Exception:  # noqa: BLE001 — 404s are an answer
        return None
    for s in (m.get("marketSides") or []):
        if isinstance(s, dict) and s.get("identifier") == us_slug:
            try:
                px = float(s.get("price"))
                if 0 < px < 1:
                    return px
            except (TypeError, ValueError):
                pass
    for k in ("bestAsk", "best_ask", "ask", "price"):
        try:
            v = m.get(k)
            if v is not None:
                px = float(v)
                if 0 < px < 1:
                    return px
        except (TypeError, ValueError):
            continue
    return None


def side_ask(us_slug: str, intent: str | None) -> float | None:
    """Live ask for the SIDE our intent names — not merely the slug.

    slug_ask() matches `identifier == us_slug`, which on the aec-
    family returns whichever side happens to come first: BOTH sides
    carry the market slug as their identifier. That is the same
    shared-identifier trap as the original wrong-side incident, sitting
    in the price reader this time.

    `intent` names the leg (BUY_LONG -> long=True). Returns None when
    the venue has no readable quote for THAT leg, and the caller
    refuses rather than guessing — a price we cannot attribute to a
    side is not a price we can trade on.
    """
    if intent not in ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"):
        return None
    want_long = intent == "ORDER_INTENT_BUY_LONG"
    client = _get_client()
    try:
        m = (client.markets.retrieve_by_slug(us_slug) or {}).get(
            "market") or {}
    except Exception:  # noqa: BLE001 — 404s are an answer
        return None
    for s in (m.get("marketSides") or []):
        if not isinstance(s, dict):
            continue
        lng = s.get("long")
        if lng is None or bool(lng) != want_long:
            continue
        for k in ("bestAsk", "best_ask", "ask", "price"):
            try:
                v = s.get(k)
                if v is not None:
                    px = float(v)
                    if 0 < px < 1:
                        return px
            except (TypeError, ValueError):
                continue
    return None


def slug_complement(us_slug: str) -> str | None:
    """The OTHER side's identifier for a two-sided US market, from the
    venue's own marketSides array — or None.

    Extracted 2026-08-26 from the mirror logic slug_bid already trusts
    (`identifier != us_slug` over the same array), because the pair
    completion carve-out needed a venue-confirmed complement and instead
    consumed `token_siblings` — a map keyed CTF-token-id -> CTF-token-id
    built from the WHALE'S global positions. A PMUS slug is never a key
    in that map, so the lookup returned '' on every call and the
    carve-out shipped inert: it compiled, its tests passed, and it could
    never fire. The venue's own market record is the only source that
    answers this question in the right key space.

    None on anything unreadable — the caller refuses, never guesses.
    """
    client = _get_client()
    try:
        m = (client.markets.retrieve_by_slug(us_slug) or {}).get(
            "market") or {}
    except Exception:  # noqa: BLE001 — 404s are an answer
        return None
    sides = [s for s in (m.get("marketSides") or []) if isinstance(s, dict)]
    if len(sides) != 2:
        return None
    other = next((s for s in sides
                  if s.get("identifier") and s.get("identifier") != us_slug),
                 None)
    if other is None:
        return None
    # Sanity: us_slug itself must be one of the two sides, or this
    # market record is not the market we were asked about.
    if not any(s.get("identifier") == us_slug for s in sides):
        return None
    return str(other["identifier"])


def slug_bid(us_slug: str) -> float | None:
    """Live best BID for one orderable US slug (desk cash-out, owner
    directive 2026-08-22). Explicit bid fields first; on a two-sided
    market with no bid field the sibling side's quoted price mirrors
    through $1 (a resting YES bid IS a NO ask). None when the venue has
    no readable bid — the caller refuses, never guesses."""
    client = _get_client()
    try:
        m = (client.markets.retrieve_by_slug(us_slug) or {}).get(
            "market") or {}
    except Exception:  # noqa: BLE001 — 404s are an answer
        return None
    for k in ("bestBid", "best_bid", "bid"):
        try:
            v = m.get(k)
            if v is not None:
                px = float(v)
                if 0 < px < 1:
                    return px
        except (TypeError, ValueError):
            continue
    sides = [s for s in (m.get("marketSides") or []) if isinstance(s, dict)]
    if len(sides) == 2:
        other = next((s for s in sides
                      if s.get("identifier") != us_slug), None)
        if other is not None:
            try:
                px = float(other.get("price"))
                if 0 < px < 1:
                    return round(1 - px, 4)
            except (TypeError, ValueError):
                pass
    return None


_LINE_TOKEN = re.compile(r"^(?:o|u)?(?:pos-|neg-)?(\d+(?:pt\d)?)$")


def _feed_derivative(global_slug: str) -> dict | None:
    """Parse a kindless feed slug's derivative suffix. Returns
    {base, kind ('total'|'spread'), line ('8pt5'), side ('o'/'u' for
    totals, 'pos'/'neg'/None for spreads), team (code or None)} or
    None when the slug is not a recognizable single-line derivative."""
    s = (global_slug or "").lower()
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if not m:
        return None
    base = s[: m.end()].strip("-")
    if len([t for t in base[: m.start()].strip("-").split("-") if t]) != 3:
        return None
    suffix = [t for t in s[m.end():].strip("-").split("-") if t]
    if not suffix:
        return None
    # totals: single token 'o8pt5' / 'u10'
    if len(suffix) == 1 and suffix[0][:1] in ("o", "u"):
        mt = _LINE_TOKEN.match(suffix[0])
        if mt and suffix[0][1:] == mt.group(1):
            return {"base": base, "kind": "total",
                    "line": mt.group(1), "side": suffix[0][0],
                    "team": None}
    # spreads: [team]? (pos|neg)? line — the feed's own side encoding
    team = None
    toks = list(suffix)
    if len(toks) >= 2 and toks[0].isalpha() and toks[0] not in ("pos",
                                                               "neg"):
        team, toks = toks[0], toks[1:]
    side = None
    if toks and toks[0] in ("pos", "neg"):
        side, toks = toks[0], toks[1:]
    if len(toks) == 1 and re.fullmatch(r"\d+(?:pt\d)?", toks[0]):
        return {"base": base, "kind": "spread", "line": toks[0],
                "side": side, "team": team}
    return None


_SIGNED_LINE = re.compile(r"[+−-]\s?\d+(?:\.\d+)?")


def _signed_lines(text: str | None) -> set[float]:
    """Every signed handicap token in a string, as floats. '+1.5',
    '-1.5' and the unicode minus all normalize; unsigned numbers
    (scores, dates, 'O/U 8.5') never match — the sign IS the datum."""
    out: set[float] = set()
    for tok in _SIGNED_LINE.findall(text or ""):
        try:
            out.add(float(tok.replace("−", "-").replace(" ", "")))
        except ValueError:
            continue
    return out


def _line_value(line_token: str) -> float | None:
    """'1pt5' -> 1.5, '2' -> 2.0."""
    try:
        return float(line_token.replace("pt", "."))
    except (ValueError, AttributeError):
        return None


def _spread_exact(fd: dict, outcome: str | None,
                  his_title: str | None) -> dict | None:
    """Spread mapping WITHOUT interpreting the venue's pos/neg token.

    The 2026-08-12 review excluded spreads from the exact resolver
    because the feed's pos/neg-vs-team convention was unverified, and a
    misread buys the MIRROR of the whale's bet. This branch never
    answers that question — it refuses to rely on the token at all.
    Every accepted mapping is corroborated text-to-text between the
    whale's OWN metadata and the venue's OWN words:

      1. His market_title must carry exactly ONE signed line ('-1.5'),
         and its magnitude must equal the line in his slug. No signed
         line in his title -> refuse (fuzzy keeps the trade).
      2. The candidate's question must carry exactly ONE signed line,
         EQUAL to his — sign included. '+1.5' vs '-1.5' refuses.
      3. The question with its line stripped must match his outcome
         team at the same floor as every exact path. A question naming
         BOTH teams ('A vs B: handicap') dilutes below the floor and
         refuses — the review's title-names-both-teams defeat is the
         DESIGNED-IN refusal here, not a blind spot.
      4. The side ordered is the one whose description matches his
         outcome, or the literal 'Yes' side of a corroborated
         question. No such side -> refuse.

    pos/neg appears ONLY in candidate slug enumeration (his suffix
    verbatim, never the flipped sign), where a wrong guess is a 404,
    not a trade."""
    his_lines = _signed_lines(his_title)
    if len(his_lines) != 1:
        return None
    his_line = next(iter(his_lines))
    mag = _line_value(fd["line"])
    if mag is None or abs(his_line) != abs(mag):
        return None
    # HIS TITLE MUST FRAME HIS OUTCOME (review 2026-08-13, confirmed
    # critical): spread titles frame ONE side ('Spread: Nationals
    # (-1.5)') while the whale can buy EITHER outcome token. When he
    # buys the non-title side (Mets +1.5), the title's -1.5 is the
    # WRONG sign for his bet — and the only venue question able to
    # pass a sign check anchored to it is his team at the mirror
    # line. So the exact path handles ONLY title-side buys, where the
    # sign provably belongs to his team; non-title buys refuse here
    # and keep the fuzzy pipeline, exactly as before this feature.
    on = _norm(outcome)
    t_body = (his_title or "").split(":", 1)[-1]
    t_team = _norm(_SIGNED_LINE.sub(" ", t_body))
    if not on or not t_team or \
            SequenceMatcher(None, t_team, on).ratio() < MATCH_FLOOR:
        return None
    # Unknown suffix qualifiers refuse OUTRIGHT (review 2026-08-13,
    # confirmed major): 'corners'/'cards'/segment tokens parse into
    # fd['team'], and dropping them from a candidate maps a corners
    # handicap onto the GAME's goal spread at the same line. A team
    # token is only a team token if it appears in the slug base.
    base_head = fd["base"].split("-")
    base_teams = set(base_head[1:3]) if len(base_head) >= 4 else set()
    if fd["team"] and fd["team"] not in base_teams:
        return None
    parts = [fd["team"], fd["side"], fd["line"]]
    suffix_full = "-".join(p for p in parts if p)
    cands = [f"asc-{fd['base']}-{suffix_full}"]
    if fd["team"]:
        cands.append(f"asc-{fd['base']}-" + "-".join(
            p for p in (fd["side"], fd["line"]) if p))
    if fd["side"]:
        cands.append(f"asc-{fd['base']}-" + "-".join(
            p for p in (fd["team"], fd["line"]) if p))
    if fd["team"] and fd["side"]:
        cands.append(f"asc-{fd['base']}-{fd['line']}")
    client = _get_client()
    for slug in dict.fromkeys(cands):
        try:
            m = (client.markets.retrieve_by_slug(slug) or {}).get(
                "market") or {}
        except Exception:  # noqa: BLE001 — 404 is an answer
            continue
        if not m.get("slug") or m.get("closed"):
            continue
        q = m.get("question") or m.get("title") or ""
        q_lines = _signed_lines(q)
        if len(q_lines) != 1 or next(iter(q_lines)) != his_line:
            continue
        # Team anchoring — STRICTER than _outcome_score, deliberately.
        # Its two leniencies are exactly this branch's leaks (both
        # caught by this change's own tests before shipping):
        #   - the shared-word token boost scores 'Leeds United' 0.9
        #     against 'Manchester United' via 'united';
        #   - _sim's containment rule scores a question naming BOTH
        #     teams 1.0 because his team is a substring of it.
        # So: matchup-format questions refuse outright, and the
        # remainder must BE his team by raw ratio — no boost, no
        # substring shortcut. 'Will X cover ...' extracts X first.
        qn = _norm(_SIGNED_LINE.sub(" ", q))
        if " vs " in f" {qn} ":
            continue
        q_texts = [qn]
        mq = re.search(r"will (?:the )?(.+?) (?:cover|win)", qn)
        if mq:
            q_texts.append(mq.group(1))
        q_team = q_texts[-1]
        if not any(SequenceMatcher(None, t, on).ratio() >= MATCH_FLOOR
                   for t in q_texts if t):
            continue
        # SIDE SELECTION with the same strictness as the question
        # check (review 2026-08-13, confirmed critical: reusing
        # _outcome_score here let 'Leeds United +1.5' score 0.9
        # against 'Manchester United' via the shared token, and
        # first-past-the-floor made the VENUE'S ORDERING pick the
        # side). Raw ratio on the line-stripped description, every
        # side scored, and the winner must be UNIQUE — two passing
        # sides is ambiguity, and ambiguity refuses. A side carrying
        # its own signed line must carry HIS.
        named = []
        yes_side = None
        for s in (m.get("marketSides") or []):
            if not (isinstance(s, dict) and s.get("identifier")
                    and s.get("description")):
                continue
            desc = s["description"]
            d_lines = _signed_lines(desc)
            if d_lines and d_lines != {his_line}:
                continue
            stripped = _norm(_SIGNED_LINE.sub(" ", desc))
            if stripped == "yes":
                yes_side = s
                continue
            if stripped and SequenceMatcher(
                    None, stripped, on).ratio() >= MATCH_FLOOR:
                named.append(s)
        # THE INTENT STAMP (2026-08-29): this resolver's mappings ride
        # mapping_src="exact" straight past the quarantine, then died
        # at the executor's no-side-intent refusal — every one —
        # because the winning side was returned without the intent
        # that buys it. Stamped from the venue's own side markers
        # (order_intent_for, judged against the market's full lookup —
        # never a skeleton here). A side whose intent the venue never
        # named carries intent None and the executor refuses it,
        # exactly as it refused this whole class before the stamp.
        if len(named) == 1:
            return {"market_slug": named[0]["identifier"], "title": q,
                    "outcome": named[0]["description"],
                    "intent": order_intent_for(m, named[0]),
                    "matched_by": "spread_exact", "score": 1.0}
        if not named and yes_side is not None:
            # The question IS his statement (team + signed line both
            # corroborated above); 'Yes' is its affirmative side.
            return {"market_slug": yes_side["identifier"], "title": q,
                    "outcome": q_team.strip() or outcome,
                    "intent": order_intent_for(m, yes_side),
                    "matched_by": "spread_exact_yes", "score": 1.0}
        return None
    return None


def resolve_derivative_exact(global_slug: str,
                             outcome: str | None,
                             his_title: str | None = None) -> dict | None:
    """Deterministic spread/total mapping (owner order 2026-08-12:
    'fix the mapping errors... without any leaks'). The funnel's own
    diagnostics show the venue LISTS these markets while the fuzzy
    pipeline fails to address them; this resolver goes grammar-to-
    grammar and accepts a side only when every fact corroborates:

      totals:  candidate 'tsc-<base>-<line>' — the line lives IN the
               slug (wrong-line is impossible); Over/Under chosen by
               the side description matching the feed outcome word.
      spreads: (owner order 2026-08-13, unmapping recovery) mapped by
               _spread_exact — signed-line + team text corroboration
               between HIS metadata and the venue's question, never by
               interpreting the pos/neg token. See its docstring.

    Anything short of full corroboration returns None and the fuzzy
    pipeline (with its own line-consistency guard) remains the
    fallback — this path can only ADD correctly-mapped copies, never
    substitute a guess."""
    fd = _feed_derivative(global_slug)
    if fd is None:
        return None
    if fd["kind"] == "spread":
        return _spread_exact(fd, outcome, his_title)
    if fd["kind"] != "total":
        return None
    client = _get_client()
    slug = f"tsc-{fd['base']}-{fd['line']}"
    slug_word = "over" if fd["side"] == "o" else "under"
    try:
        m = (client.markets.retrieve_by_slug(slug) or {}).get(
            "market") or {}
    except Exception:  # noqa: BLE001 — 404 is an answer
        return None
    if not m.get("slug") or m.get("closed"):
        return None
    title = m.get("question") or m.get("title") or ""
    sides = [s for s in (m.get("marketSides") or [])
             if isinstance(s, dict) and s.get("identifier")
             and s.get("description")]
    # The side is chosen by the WHALE'S OUTCOME against the venue's
    # side descriptions (same floor as every exact path) — never by
    # the slug token alone (review: the outcome must drive the side).
    best, best_score = None, 0.0
    for s in sides:
        sc = _outcome_score({"outcome": s["description"]}, outcome)
        if sc > best_score:
            best, best_score = s, sc
    if best is None or best_score < MATCH_FLOOR:
        return None
    # Cross-check: the chosen side's over/under word must agree with
    # the feed slug's o/u token — WORD-boundary matched, so 'overtime'
    # can never read as 'over'. Disagreement refuses outright.
    side_words = set(re.findall(r"[a-z]+", _norm(best["description"])))
    if slug_word not in side_words:
        return None
    other_word = "under" if slug_word == "over" else "over"
    if other_word in side_words:
        return None
    # THE INTENT STAMP (2026-08-29): same as _spread_exact — an exact
    # totals mapping without its intent stamp was refused wholesale at
    # the executor's no-side-intent guard. The guard stays; the stamp
    # is what was missing. Unnameable sides carry None and refuse
    # downstream, unchanged.
    return {"market_slug": best["identifier"], "title": title,
            "outcome": best["description"],
            "intent": order_intent_for(m, best),
            "matched_by": "derivative_exact", "score": best_score}



def order_intent_for(market: dict, side: dict | None) -> str | None:
    """The ORDER INTENT that buys this side of this market, or None when
    the venue does not say unambiguously (REFUSE).

    Venue ground truth 2026-08-24: on the aec- family both sides carry
    the SAME identifier (equal to the market slug) and the order payload
    has no side field, so `intent` (BUY_LONG vs BUY_SHORT) is the only
    thing that selects a side. Every resolver must state it or refuse —
    ordering an unnamed side hands the choice to the venue, which is the
    wrong-side incident's root cause."""
    sides = [x for x in (market.get("marketSides") or [])
             if isinstance(x, dict)]
    if side is None:
        # a single-outcome contract: the slug itself is the position
        return None if sides else "ORDER_INTENT_BUY_LONG"
    from .workers.premap import side_intent

    return side_intent(side, sides)


def resolve_market_exact(candidate_slugs: list[str],
                         outcome: str | None,
                         diag_out: list | None = None) -> dict | None:
    """Deterministic US-market resolution for the manual desk: try each
    candidate slug via direct lookup ONLY — no fuzzy fallback. The
    desk's first live ticket proved why (2026-08-07): the full-text
    search mapped 'Casper Ruud' onto an astatc PLAYER PROP instead of
    the match moneyline. A human's directed trade must map to exactly
    the market implied by the slug grammar or refuse outright.

    WHY IT MISSED, WHEN IT MISSES (2026-08-26). This returned None
    silently and recorded nothing, so a failure here was indistinguishable
    from never having been tried. Only resolve_market (the fuzzy
    fallback) carried a last_diag, which is why every diagnostic anyone
    has ever read about an unmapped row describes the FUZZY attempt —
    and tennis, at 48% of the recent unmapped funnel and 4,919 ATP rows
    in seven days, dies HERE, one step earlier, invisibly.

    diag_out collects one compact code per candidate:
      404      the venue does not list that slug
      closed   listed but closed
      low:S    a market, but the outcome scored S below MATCH_FLOOR
      parent   scored, but it carries sides — not itself orderable
      amb:A/B  two sides both plausible; refusing beats a coin flip
      noint    sides share an identifier and no long/short is named
      ok       matched

    A LIST PARAMETER, NOT A FUNCTION ATTRIBUTE. resolve_market's
    last_diag is shared mutable state and this runs under
    asyncio.to_thread with four copies in flight, so an attribute would
    hand one row's reason to another row — a diagnostic that lies is
    worse than none, and this file has paid for that lesson already.
    """
    def _note(code: str) -> None:
        if diag_out is not None and len(diag_out) < 24:
            diag_out.append(code)

    client = _get_client()
    for slug in candidate_slugs:
        if not slug:
            continue
        try:
            m = (client.markets.retrieve_by_slug(slug) or {}).get("market") or {}
        except Exception:  # noqa: BLE001 — 404 is expected; next candidate
            _note("404")
            continue
        if not m.get("slug"):
            _note("404")
            continue
        if m.get("closed"):
            _note("closed")
            continue
        score = _outcome_score(m, outcome)
        # A market that CARRIES sides is a two-outcome contract: its
        # parent slug is not a position, and ordering it sideless hands
        # side selection to the venue (wrong-side incident 2026-08-23;
        # the quarantine stream caught this branch still returning
        # parent aec- slugs on 2026-08-24 — a parent passing the floor
        # must fall through to side selection, never be ordered).
        _has_sides = any(isinstance(s, dict) and s.get("identifier")
                         and s.get("description")
                         for s in (m.get("marketSides") or []))
        if score >= MATCH_FLOOR and not _has_sides:
            _note("ok")
            return {"market_slug": m["slug"], "title": m.get("title"),
                    "outcome": m.get("outcome"),
                    "intent": order_intent_for(m, None),
                    "matched_by": "desk_exact", "score": score}
        if not _has_sides:
            # Listed, not closed, no sides — so the only thing between
            # this and a mapping is the outcome score. Recording the
            # NUMBER is the point: a floor problem and a wrong-market
            # problem look identical without it.
            _note(f"low:{score:.2f}")
            continue
        # Two-sided markets (tennis aec- especially) score near zero on
        # the PARENT outcome — the tradable sides live in marketSides,
        # each side its own orderable slug (the copy sleeve's tennis
        # path since 2026-08-04). Order the side that IS the outcome —
        # and ONLY when exactly one side is (review 2026-08-13,
        # confirmed: first-past-the-floor let a surname-only outcome
        # like 'Ito' score 1.0 against BOTH 'Aoi Ito' and 'Mai Saito'
        # via _sim's containment rule, so the venue's side ORDERING
        # picked the player). Ambiguity refuses; the fuzzy pipeline
        # or nothing is strictly better than a coin flip.
        best_side, best_sc, second_sc = None, 0.0, 0.0
        for side in (m.get("marketSides") or []):
            if not isinstance(side, dict):
                continue
            desc, ident = side.get("description"), side.get("identifier")
            if not desc or not ident:
                continue
            sscore = _outcome_score({"outcome": desc}, outcome)
            if sscore > best_sc:
                best_side, best_sc, second_sc = side, sscore, best_sc
            elif sscore > second_sc:
                second_sc = sscore
        if (best_side is not None and best_sc >= MATCH_FLOOR
                and second_sc < MATCH_FLOOR):
            _int = order_intent_for(m, best_side)
            if _int is None:
                # sides share an identifier and the venue named no
                # long/short: unorderable, never a coin flip
                _note("noint")
                continue
            _note("ok")
            return {"market_slug": best_side["identifier"],
                    "title": m.get("question") or m.get("title"),
                    "outcome": best_side["description"],
                    "intent": _int,
                    "matched_by": "desk_exact_side", "score": best_sc}
        _note(f"amb:{best_sc:.2f}/{second_sc:.2f}")
    return None


def resolve_market(market_slug: str | None, event_slug: str | None,
                   market_title: str | None, event_title: str | None,
                   outcome: str | None) -> dict | None:
    """Map a global-CLOB trade to a US market. Returns
    {"market_slug", "title", "outcome", "intent", "matched_by", "score"}
    or None.

    Order of attempts (cheapest/most exact first):
      1. same market slug on the US venue
      2. markets list filtered by the same event slug
      3. full-text search on the event/market title
    Every hit must still pass the outcome-similarity floor.

    THE INTENT STAMP (2026-08-29). The live executor refuses any
    mapping whose `intent` is empty — correctly, because on
    shared-identifier families intent is the only side selector. This
    resolver picked a UNIQUE side and then dropped it on the floor:
    none of its return sites stamped `intent`, so every mapping it
    produced was refused downstream as "no side intent" — the largest
    single class in the live reject stream. The stamp comes from
    order_intent_for (the venue's own side markers, identical to the
    exact resolver's rule), and ONLY from a verified market shape — a
    search skeleton whose sides may simply be omitted never gets a
    contract stamp. Candidate SELECTION is unchanged: a winner whose
    intent cannot be named still returns, with intent None, and the
    executor's refuse-on-unknown guard — untouched — rejects it
    exactly as it rejected this entire class before the stamp.
    """
    client = _get_client()
    diag: list[str] = []

    # 1) direct slug parity
    _parity_seed: list[dict] = []
    if market_slug:
        try:
            m = (client.markets.retrieve_by_slug(market_slug) or {}).get("market") or {}
            score = _outcome_score(m, outcome)
            _p_sides = [s for s in (m.get("marketSides") or [])
                        if isinstance(s, dict)]
            if m.get("slug") and score >= MATCH_FLOOR and not m.get("closed") \
                    and not _p_sides:
                return {"market_slug": m["slug"], "title": m.get("title"),
                        "outcome": m.get("outcome"),
                        "intent": order_intent_for(m, None),
                        "matched_by": "slug", "score": score}
            if m.get("slug") and not m.get("closed") and _p_sides:
                # A parent that CARRIES sides is not a position — ordering
                # it sideless hands side selection to the venue (incident
                # 2026-08-23). It goes through the side-selection loop
                # below with the same uniqueness rule as every candidate.
                _parity_seed.append(m)
                diag.append("slug:parent-with-sides")
        except Exception as exc:  # noqa: BLE001 — 404 is expected; fall through
            log.debug("pmus slug lookup miss (%s): %s", market_slug, exc)
            diag.append(f"slug:{type(exc).__name__}")

    # 2) shared event slug → per-outcome siblings
    candidates: list[dict] = []
    if event_slug:
        try:
            resp = client.markets.list({"eventSlug": [event_slug], "active": True})
            got = list((resp or {}).get("markets") or [])
            # The venue quietly ignores filters it doesn't recognize and
            # returns a default page — 20 unrelated markets scoring 0
            # "satisfied" this step and blocked the title search on every
            # one of the first 700 copy attempts. Drop positive
            # mismatches; markets without an eventSlug field stay (their
            # zero scores no longer block the search below).
            # UNVERIFIED SHAPE, same as the search lane (adversarial
            # review 2026-08-29): this endpoint is already documented
            # above as returning junk default pages, and an abbreviated
            # row that OMITS marketSides is indistinguishable from a
            # sideless contract — stamping BUY_LONG on it would make a
            # two-sided PARENT orderable. Rows keep the mark; a winner
            # that needs a contract stamp gets one full lookup below.
            candidates = [{**m, "_unverified_shape": True} for m in got
                          if (m.get("eventSlug") or m.get("event_slug"))
                          in (None, event_slug)]
            diag.append(f"event:{len(got)}/{len(candidates)}")
        except Exception as exc:  # noqa: BLE001 — fall through to search
            candidates = []
            diag.append(f"event:{type(exc).__name__}")

    # Line-consistency: his "Spread: Nationals (-1.5)" must not map to the
    # Nationals MONEYLINE just because the team name matches. Numbers in
    # the source market title (the line) must agree with numbers in the
    # candidate's title: agreement is a nudge up, disagreement (including
    # line-vs-no-line) drops the candidate below the floor.
    # Line numbers are the HALF-POINT decimals (-1.5, 10.5): question text
    # also carries dates ("Aug 3, 2026") that a naive number-set equality
    # falsely penalized. Compare lines only.
    def _lines(text: str | None) -> set[str]:
        return {n for n in re.findall(r"\d+\.5", text or "")}

    src_lines = _lines(market_title)

    def _best(cands: list[dict]) -> tuple[dict | None, float, str | None]:
        top, top_score, top_intent = None, 0.0, None
        for m in cands:
            if m.get("closed"):
                continue
            cand_lines = _lines((m.get("title") or "") + " "
                                + (m.get("question") or ""))
            line_adj = (-0.2 if src_lines != cand_lines
                        else (0.05 if src_lines else 0.0))
            sides = [s for s in (m.get("marketSides") or [])
                     if isinstance(s, dict)
                     and s.get("description") and s.get("identifier")]
            # A market that CARRIES sides is a two-outcome contract: its
            # parent slug is not a position, and ordering it sideless
            # hands side selection to the venue's default (wrong-side
            # incident 2026-08-23 — a coin flip with real money). The
            # parent may never win; only a uniquely-matched side may.
            if m.get("slug") and not sides:
                # THE STAMP NEEDS A VERIFIED SHAPE. Search returns
                # SKELETONS — a two-sided market whose marketSides the
                # search response simply omits would look like a
                # sideless contract here, and stamping BUY_LONG on it
                # would make a PARENT orderable (the venue then picks
                # the side — incident 2026-08-23). Candidates whose
                # shape was never confirmed by a full lookup keep
                # intent None: selection is unchanged, and the
                # executor's no-side-intent guard refuses them exactly
                # as it refused every fuzzy mapping before the stamp
                # existed. order_intent_for itself judges against the
                # UNFILTERED side list, so a market whose sides all
                # lack descriptions also stays None here.
                _c_int = (None if m.get("_unverified_shape")
                          else order_intent_for(m, None))
                sc = _outcome_score(m, outcome) + line_adj
                if sc > top_score:
                    top, top_score, top_intent = m, min(sc, 1.0), _c_int
            if sides:
                # marketSides (schema named by the 2026-08-04 trails):
                # each side is its OWN orderable market — description
                # names the side ("Dalma Galfi"), identifier is that
                # side's slug. Same uniqueness rule as the exact path:
                # exactly ONE side may pass the floor; two passing
                # sides is ambiguity and the market contributes
                # nothing — a tie must never fall to venue ordering.
                s_top, s_bsc, s_2nd = None, 0.0, 0.0
                for side in sides:
                    ssc = min(_outcome_score({"outcome":
                                              side["description"]},
                                             outcome) + line_adj, 1.0)
                    if ssc > s_bsc:
                        s_top, s_bsc, s_2nd = side, ssc, s_bsc
                    elif ssc > s_2nd:
                        s_2nd = ssc
                if (s_top is not None and s_bsc >= MATCH_FLOOR
                        and s_2nd < MATCH_FLOOR and s_bsc > top_score):
                    # Candidate SELECTION is deliberately unchanged: a
                    # winning side whose intent the venue never named
                    # (shared identifier, no marker) still wins and is
                    # returned with intent None — the executor refuses
                    # it, same as before the stamp existed. Skipping it
                    # here would silently promote a LOWER-scored match
                    # into a live order, which is a substitution this
                    # resolver must never make.
                    top = {"slug": s_top["identifier"],
                           "title": m.get("question"),
                           "outcome": s_top["description"],
                           "closed": False}
                    top_score = s_bsc
                    top_intent = order_intent_for(m, s_top)
        return top, top_score, top_intent

    # LATE VERIFICATION for a winner without a stamp: one full lookup.
    # A contract candidate whose shape was never confirmed carries
    # intent None out of _best; if the FULL market really has no sides,
    # the stamp is earned here from the venue's own expansion. A full
    # market that turns out to carry sides stays None — the winner was
    # a parent (or an unnameable side) and the executor's guard refuses
    # it, unchanged. Unreadable lookups also stay None: fail closed.
    def _late_stamp(slug: str | None) -> str | None:
        if not slug:
            return None
        try:
            fm = (client.markets.retrieve_by_slug(slug) or {}).get(
                "market") or {}
        except Exception:  # noqa: BLE001 — unverifiable: no stamp
            return None
        if not fm.get("slug") or fm.get("closed"):
            return None
        return order_intent_for(fm, None)

    best, best_score, best_intent = _best(_parity_seed + candidates)
    if best is not None and best_score >= MATCH_FLOOR:
        return {"market_slug": best["slug"], "title": best.get("title"),
                "outcome": best.get("outcome"),
                "intent": best_intent or _late_stamp(best.get("slug")),
                "matched_by": "event", "score": best_score}

    # 3) title search → events with nested markets. Queries are tried
    # cleanest-first: the raw titles carry market decorations the venue's
    # search does not match. ALWAYS reached when the event step produced
    # no verified match — unverified candidates must not block it.
    candidates = []
    if event_title or market_title:
        queries = []
        for q in (_clean_title(event_title), _clean_title(market_title),
                  _surname_matchup(event_title), _surname_matchup(market_title),
                  event_title, market_title):
            if q and q not in queries:
                queries.append(q)
        best_ev, best_ev_score = None, 0.0
        for q in queries:
            try:
                resp = client.search.query(
                    {"query": q, "limit": 5, "status": "active"})
                found = 0
                for ev in (resp or {}).get("events") or []:
                    ev_score = max(_sim(ev.get("title"), event_title),
                                   _sim(ev.get("title"), market_title),
                                   _sim(ev.get("title"), _clean_title(market_title)),
                                   _sim(_surname_matchup(ev.get("title")),
                                        _surname_matchup(market_title)
                                        or _surname_matchup(event_title)))
                    if ev_score >= MATCH_FLOOR:
                        found += 1
                        if ev_score > best_ev_score:
                            best_ev, best_ev_score = ev, ev_score
                diag.append(f"search[{q[:24]}]:{found}ev")
            except Exception as exc:  # noqa: BLE001
                diag.append(f"search[{q[:24]}]:{type(exc).__name__}")
            if best_ev is not None:
                break
        # Search returns SKELETON markets (slug + title, no outcome/team) —
        # scoring them is scoring air: "New York Yankees" scored 0.0 against
        # 298 markets that certainly included the Yankees ML (2026-08-03).
        # Hydrate the best-matching event's markets with full lookups, then
        # score fields that actually exist. Bounded to one event (~20-40
        # markets) so a copy attempt costs a bounded number of API calls.
        if best_ev is not None:
            hydrated = 0
            for m in (best_ev.get("markets") or [])[:40]:
                # A search market that skipped or failed hydration has
                # an UNVERIFIED shape: absent marketSides may mean
                # "sideless contract" or may mean "the search response
                # omits sides". The mark keeps _best from stamping a
                # contract intent on it — see the stamp note there.
                if m.get("outcome") or m.get("team"):
                    candidates.append({**m, "_unverified_shape": True})
                    continue
                slug = m.get("slug")
                if not slug:
                    continue
                try:
                    full = (client.markets.retrieve_by_slug(slug) or {})                         .get("market") or {}
                    candidates.append(full
                                      or {**m, "_unverified_shape": True})
                    hydrated += 1
                except Exception:  # noqa: BLE001 — score the skeleton instead
                    candidates.append({**m, "_unverified_shape": True})
            diag.append(f"hydrated:{hydrated}/{len(candidates)}")

    best2, best2_score, best2_intent = _best(candidates)
    if best2 is not None and best2_score >= MATCH_FLOOR:
        return {"market_slug": best2["slug"], "title": best2.get("title"),
                "outcome": best2.get("outcome"),
                "intent": best2_intent or _late_stamp(best2.get("slug")),
                "matched_by": "search", "score": best2_score}
    diag.append(f"best_outcome_score:{round(max(best_score, best2_score), 2)}")
    if candidates:
        c0 = candidates[0]
        # Keys first (names the schema), then identity values. Kept ahead
        # of nothing — the audit column truncates at 300 chars and the
        # sample is the payload that matters most on a zero score.
        # marketSides is the side-carrier on two-sided markets and the
        # last unknown in the tennis mapping — when present it gets the
        # whole diagnostic budget (the keys list already did its job).
        if c0.get("marketSides") is not None:
            diag.insert(0, "sides:" + str(c0["marketSides"])[:240])
        else:
            ident = {k: c0.get(k) for k in
                     ("title", "outcome", "team", "name", "question")
                     if c0.get(k) is not None}
            diag.insert(0, "keys:" + ",".join(sorted(c0.keys()))[:120])
            diag.insert(1, "ident:" + str(ident)[:110])
    # The trail rides the exception-free path out via last_resolve_diag so
    # the caller can store WHY in the audit row without a signature break.
    resolve_market.last_diag = "; ".join(diag)[:280]
    return None


def _exit_intent(us_market_slug: str, opened_with: str | None) -> str:
    """SELL_LONG or SELL_SHORT for closing this position."""
    if opened_with == "ORDER_INTENT_BUY_SHORT":
        return "ORDER_INTENT_SELL_SHORT"
    if opened_with == "ORDER_INTENT_BUY_LONG":
        return "ORDER_INTENT_SELL_LONG"
    net = position_side(us_market_slug)
    if net is not None and net < 0:
        return "ORDER_INTENT_SELL_SHORT"
    return "ORDER_INTENT_SELL_LONG"


def submit_fok(us_market_slug: str, limit_price: float, quantity: int,
               sell: bool = False,
               tif: str = "TIME_IN_FORCE_FILL_OR_KILL",
               intent: str | None = None) -> dict:
    """Preview then place a limit order. Returns the same normalized
    shape the global executor uses:
    {ok, order_id, status, fill_price, filled_shares, raw}.

    sell=True places SELL_LONG (underdog cash-out sleeve, owner directive
    2026-08-08) — the limit is then the MINIMUM acceptable price, so a
    fill can only ever realize at least the requested profit. The
    preview cost-tolerance guard is buy-shaped (it bounds what we PAY);
    a sell's preview reports proceeds, so the guard is skipped.

    tif=TIME_IN_FORCE_IMMEDIATE_OR_CANCEL takes whatever quantity rests
    at or below the limit and cancels the remainder (owner order
    2026-08-21: a copy takes the book's available size at his price or
    better, up to the clip, instead of all-or-nothing). The preview
    guard stays valid: an IOC can only cost LESS than the full-quantity
    preview it was checked against."""
    client = _get_client()
    # THE SIDE SELECTOR (venue ground truth 2026-08-24): on market
    # families whose two sides share one identifier — every aec- match
    # — the slug does NOT name a side and CreateOrderParams carries no
    # side field, so `intent` is the ONLY thing that distinguishes
    # BUYING Marko Topo from BUYING Miguel Damas (BUY_LONG vs
    # BUY_SHORT). Hardcoding BUY_LONG, as this did until now, let the
    # venue pick our side on that whole family — the wrong-side
    # incident's root cause. Callers pass the intent the venue's own
    # side object named; the default stays BUY_LONG for the families
    # whose side identifiers are distinct.
    if intent is not None and intent not in ("ORDER_INTENT_BUY_LONG",
                                             "ORDER_INTENT_BUY_SHORT"):
        return {"ok": False, "order_id": None,
                "status": "bad_intent", "fill_price": None,
                "filled_shares": 0.0, "raw": {"intent": intent}}
    _buy_intent = intent
    if _buy_intent is None and not sell:
        # LAST-GATE BACKSTOP (2026-08-24): three live paths — the
        # underdog sleeve and both desk paths — call this without an
        # intent, and the old default silently bought side 0. Since
        # EVERY two-sided market on this venue shares one identifier
        # between its sides, that default was a coin flip. When the
        # caller does not name a side, ask the venue: a market whose
        # sides are ambiguous is REFUSED here, so no caller can forget
        # its way into a wrong-side order.
        try:
            _m = (_get_client().markets.retrieve_by_slug(us_market_slug)
                  or {}).get("market") or {}
        except Exception as exc:  # noqa: BLE001 — unreadable: refuse
            return {"ok": False, "order_id": None,
                    "status": "side_unverifiable", "fill_price": None,
                    "filled_shares": 0.0, "raw": {"error": str(exc)[:200]}}
        _sides = [x for x in (_m.get("marketSides") or [])
                  if isinstance(x, dict)]
        _idents = [str(x.get("identifier") or "").lower() for x in _sides]
        if len(_sides) > 1 and len(set(_idents)) < len(_sides):
            return {"ok": False, "order_id": None,
                    "status": "ambiguous_side", "fill_price": None,
                    "filled_shares": 0.0,
                    "raw": {"slug": us_market_slug,
                            "sides": [x.get("description") for x in _sides],
                            "why": "sides share an identifier; the caller "
                                   "must pass the intent that names the "
                                   "side"}}
        _buy_intent = "ORDER_INTENT_BUY_LONG"
    params = {
        "marketSlug": us_market_slug,
        # EXITING A SHORT (leak-hunt round 3, 2026-08-24): a position
        # opened with BUY_SHORT is closed with SELL_SHORT — sending
        # SELL_LONG would try to sell a side we do not hold. The caller
        # names the exit side by passing the BUY intent it opened with;
        # absent that, the position's own sign decides, and an
        # unreadable position REFUSES rather than guessing.
        "intent": (_exit_intent(us_market_slug, intent) if sell
                   else _buy_intent),
        "type": "ORDER_TYPE_LIMIT",
        "price": _amount(limit_price),
        "quantity": int(quantity),
        "tif": tif,
        "synchronousExecution": True,
    }

    # The venue's own cost calculation must agree with ours before we commit.
    # prev_order must exist on every path: the sell branch skips the preview,
    # and referencing it unbound AFTER orders.create would raise with a real
    # sell already executed at the venue (audit 2026-08-21 — the cash-out
    # sweep sold, crashed, left the row 'filled', and retried the sell).
    expected_cost = limit_price * quantity
    prev_order: dict = {}
    if not sell:
        preview = client.orders.preview(
            {"request": {k: v for k, v in params.items()
                         if k != "synchronousExecution"}})
        prev_order = (preview or {}).get("order") or {}
        prev_cost = _order_cost(prev_order)
        # FAIL CLOSED (2026-08-25). An unreadable preview is not
        # agreement — it is the absence of a second opinion on a real
        # order, and it is exactly the state in which the overspend got
        # through. Refuse and let the row record why.
        if prev_cost is None:
            return {"ok": False, "order_id": None,
                    "status": "preview_unreadable",
                    "fill_price": None, "filled_shares": 0.0,
                    "raw": {"preview": preview,
                            "expected_cost": expected_cost,
                            "why": "venue preview stated no cost; "
                                   "refusing rather than assuming it "
                                   "agrees with ours"}}
        if prev_cost > expected_cost * PREVIEW_COST_TOLERANCE:
            return {"ok": False, "order_id": None,
                    "status": "preview_mismatch",
                    "fill_price": None, "filled_shares": 0.0,
                    "raw": {"preview": preview,
                            "expected_cost": expected_cost,
                            "venue_cost": prev_cost}}

    resp = client.orders.create(params)
    order_id = (resp or {}).get("id")
    executions = (resp or {}).get("executions") or []
    filled, notional = 0.0, 0.0
    state = ""
    for ex in executions:
        state = (ex.get("order") or {}).get("state") or state
        px = _amount_value((ex.get("lastPx") or {}))
        sh = float(ex.get("lastShares") or 0)
        if ex.get("type") in ("EXECUTION_TYPE_FILL", "EXECUTION_TYPE_PARTIAL_FILL") and px:
            filled += sh
            notional += sh * px
    # Shares that explicitly executed ARE the fill, whatever the order's
    # terminal state: an IOC that partially fills and then cancels the
    # remainder ends CANCELED, and keying ok on the last execution's state
    # turned that real fill into filled=0 — the row read 'unfilled', the
    # sweep bought the market again, and never-add was violated (audit
    # 2026-08-21). The edge adapter has always used filled > 0 alone.
    ok = filled > 0
    fill_price = round(notional / filled, 4) if filled > 0 else None
    return {"ok": ok, "order_id": order_id,
            "status": state.replace("ORDER_STATE_", "").lower() or "unknown",
            "fill_price": fill_price, "filled_shares": filled,
            "raw": {"preview": prev_order, "response": resp}}


def close_position(us_slug: str, *, slippage_bips: int) -> dict:
    """Flatten a whole position in one call. The venue's own exit.

    POST /v1/order/close-position takes ONLY marketSlug — no side, no
    intent, no quantity, no limit — and returns the same
    {id, executions[]} shape as orders.create.

    THIS IS WHY IT MATTERS HERE. mirror_exit priced its sells off
    slug_bid, and slug_bid returns None whenever the venue exposes no
    bid field and the market is not a readable two-sided pair. Every
    such refusal was an exit we DETECTED and then declined to take,
    leaving us holding a position the whale had already left. A close
    that needs no price cannot be blocked by a missing one.

    It also works on either sign, which matters because netPosition is
    signed here and a short reads negative — the leg that three
    separate guards were treating as "nothing held".

    SLIPPAGE IS MANDATORY, not defaulted. This call carries no limit
    price and cannot be previewed (orders.preview takes CreateOrderParams
    only), so sending it bare is an unbounded market order. A caller
    that has not decided how much slippage it will accept has not
    decided to trade.
    """
    if not us_slug:
        return {"ok": False, "order_id": None, "status": "no_slug",
                "fill_price": None, "filled_shares": 0.0, "raw": {}}
    if not slippage_bips or int(slippage_bips) <= 0:
        return {"ok": False, "order_id": None,
                "status": "no_slippage_bound",
                "fill_price": None, "filled_shares": 0.0,
                "raw": {"why": "close-position carries no limit price; "
                               "refusing to send an unbounded market "
                               "order"}}
    client = _get_client()
    try:
        resp = client.orders.close_position({
            "marketSlug": us_slug,
            "slippageTolerance": {"bips": int(slippage_bips)},
            "synchronousExecution": True,
        })
    except Exception as exc:  # noqa: BLE001 — a refusal, not a crash
        return {"ok": False, "order_id": None, "status": "close_failed",
                "fill_price": None, "filled_shares": 0.0,
                "raw": {"error": str(exc)[:200], "slug": us_slug}}
    executions = (resp or {}).get("executions") or []
    filled, notional, state = 0.0, 0.0, ""
    for ex in executions:
        state = (ex.get("order") or {}).get("state") or state
        px = _amount_value((ex.get("lastPx") or {}))
        sh = float(ex.get("lastShares") or 0)
        if ex.get("type") in ("EXECUTION_TYPE_FILL",
                              "EXECUTION_TYPE_PARTIAL_FILL") and px:
            filled += sh
            notional += sh * px
    return {"ok": filled > 0, "order_id": (resp or {}).get("id"),
            "status": state.replace("ORDER_STATE_", "").lower() or "unknown",
            "fill_price": (round(notional / filled, 4)
                           if filled > 0 else None),
            "filled_shares": filled, "raw": {"response": resp}}


def _amount_value(a: Any) -> float:
    try:
        return float((a or {}).get("value") or 0)
    except (TypeError, ValueError):
        return 0.0


def _order_cost(order: dict, default: float | None = None) -> float | None:
    """The venue's OWN cost for a previewed order, or None if it did not
    state one.

    THIS USED TO FAIL OPEN (2026-08-25). The signature was
    `_order_cost(order, default=expected_cost)`, so a preview carrying
    no readable cost returned OUR number — and the caller's guard,
    `prev_cost > expected_cost * TOLERANCE`, compared expected against
    expected and passed. The single control positioned to catch a
    venue charging more than we authorized was silently inert whenever
    the venue said least. Five fills took 1.15x-3.87x the clip through
    it.

    None now means "the venue did not tell us", which is not the same
    as "it agrees with us", and the caller must refuse on it."""
    cash = _amount_value(order.get("cashOrderQty"))
    if cash > 0:
        return cash
    px = _amount_value(order.get("price"))
    qty = float(order.get("quantity") or 0)
    if px and qty:
        return px * qty
    return default


def _norm_order(o: dict) -> dict:
    """One venue order -> the desk's open-order row shape."""
    md = o.get("marketMetadata") or {}
    return {
        "order_id": o.get("id"),
        "us_market_slug": o.get("marketSlug"),
        "intent": o.get("intent"),
        "side": ("SELL" if "SELL" in str(o.get("intent") or "") else "BUY"),
        "price": _amount_value(o.get("price") or {}),
        "quantity": float(o.get("quantity") or 0),
        "filled_shares": float(o.get("cumQuantity") or 0),
        "leaves": float(o.get("leavesQuantity") or 0),
        "avg_px": (_amount_value(o.get("avgPx") or {}) or None),
        "state": (str(o.get("state") or "")
                  .replace("ORDER_STATE_", "").lower() or "unknown"),
        "title": _clean_title(md.get("title") or md.get("question")
                              or md.get("name")),
        "created_at": o.get("createTime") or o.get("insertTime"),
        "tif": str(o.get("tif") or "").replace("TIME_IN_FORCE_", ""),
    }


# Venue states that mean "this order is still working the book".
OPEN_ORDER_STATES = frozenset({"new", "pending_new", "partially_filled",
                               "pending_replace", "pending_risk", "open"})


def open_orders(slugs: list[str] | None = None) -> list[dict]:
    """The account's RESTING orders, venue truth (owner order
    2026-08-28, venue parity: the desk shows and manages the same open
    orders the venue app would). Read-only; raises to the caller."""
    client = _get_client()
    params = {"slugs": slugs} if slugs else None
    resp = client.orders.list(params) or {}
    return [_norm_order(o) for o in (resp.get("orders") or [])
            if isinstance(o, dict)]


def order_status(order_id: str) -> dict | None:
    """One order by id, normalized; None if the venue has no record."""
    client = _get_client()
    resp = client.orders.retrieve(order_id) or {}
    o = resp.get("order")
    return _norm_order(o) if isinstance(o, dict) else None


def cancel_order(order_id: str, us_market_slug: str) -> dict:
    """Cancel one resting order. The venue's cancel returns no body;
    success is the absence of an error. Never raises."""
    try:
        _get_client().orders.cancel(order_id,
                                    {"marketSlug": us_market_slug})
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 — the desk reports, never 500s
        return {"ok": False, "error": f"{type(exc).__name__}: "
                                      f"{str(exc)[:160]}"}


def probe() -> dict:
    """Connectivity/diag probe usable from the deployed API (admin panel):
    unauthenticated market list + whether creds are configured. Never orders."""
    from polymarket_us import PolymarketUS

    cfg = settings()
    out: dict[str, Any] = {"creds_configured": bool(cfg.pmus_key_id and cfg.pmus_secret_key)}
    try:
        pub = PolymarketUS()
        resp = pub.markets.list({"limit": 3, "active": True})
        markets = (resp or {}).get("markets") or []
        out["gateway_ok"] = True
        out["sample_markets"] = [{"slug": m.get("slug"), "title": m.get("title"),
                                  "outcome": m.get("outcome")} for m in markets]
    except Exception as exc:  # noqa: BLE001
        out["gateway_ok"] = False
        out["gateway_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    if out["creds_configured"]:
        try:
            bal = _get_client().account.balances()
            out["auth_ok"] = True
            out["balances"] = bal
        except Exception as exc:  # noqa: BLE001
            out["auth_ok"] = False
            out["auth_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return out
