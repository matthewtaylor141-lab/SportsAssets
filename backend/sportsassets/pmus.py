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
    if title and " vs" not in title.lower() and title.count("-") < 4:
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


def resolve_derivative_exact(global_slug: str,
                             outcome: str | None) -> dict | None:
    """Deterministic spread/total mapping (owner order 2026-08-12:
    'fix the mapping errors... without any leaks'). The funnel's own
    diagnostics show the venue LISTS these markets while the fuzzy
    pipeline fails to address them; this resolver goes grammar-to-
    grammar and accepts a side only when every fact corroborates:

      totals:  candidate 'tsc-<base>-<line>' — the line lives IN the
               slug (wrong-line is impossible); Over/Under chosen by
               the side description matching the feed outcome word.
      spreads: candidate 'asc-<base>-[team-][pos|neg-]<line>' with
               the feed's OWN side encoding preserved verbatim (his
               slug IS his side); the resolved side must repeat the
               line digits, and when the outcome is a team name the
               parent title/question must contain it.

    Anything short of full corroboration returns None and the fuzzy
    pipeline (with its own line-consistency guard) remains the
    fallback — this path can only ADD correctly-mapped copies, never
    substitute a guess."""
    fd = _feed_derivative(global_slug)
    if fd is None:
        return None
    client = _get_client()
    if fd["kind"] == "total":
        cands = [f"tsc-{fd['base']}-{fd['line']}"]
        want_word = "over" if fd["side"] == "o" else "under"
    else:
        suffix = "-".join(t for t in (fd["team"], fd["side"],
                                      fd["line"]) if t)
        cands = [f"asc-{fd['base']}-{suffix}"]
        want_word = None
    ol = _norm(outcome)
    for slug in cands:
        try:
            m = (client.markets.retrieve_by_slug(slug) or {}).get(
                "market") or {}
        except Exception:  # noqa: BLE001 — 404 is an answer
            continue
        if not m.get("slug") or m.get("closed"):
            continue
        title = m.get("question") or m.get("title") or ""
        sides = [s for s in (m.get("marketSides") or [])
                 if isinstance(s, dict) and s.get("identifier")
                 and s.get("description")]
        if fd["kind"] == "total":
            for s in sides:
                if want_word in _norm(s["description"]):
                    return {"market_slug": s["identifier"],
                            "title": title, "outcome": s["description"],
                            "matched_by": "derivative_exact",
                            "score": 1.0}
            continue
        # spread: the candidate slug already IS one side; require the
        # line digits to survive in what the venue resolved, and the
        # whale's team to appear in the parent text when we know it.
        if fd["line"] not in (m.get("slug") or slug):
            continue
        if ol and not any(w in _norm(title) for w in ol.split()
                          if len(w) > 3):
            continue
        return {"market_slug": m.get("slug") or slug, "title": title,
                "outcome": m.get("outcome") or outcome,
                "matched_by": "derivative_exact", "score": 1.0}
    return None


def resolve_market_exact(candidate_slugs: list[str],
                         outcome: str | None) -> dict | None:
    """Deterministic US-market resolution for the manual desk: try each
    candidate slug via direct lookup ONLY — no fuzzy fallback. The
    desk's first live ticket proved why (2026-08-07): the full-text
    search mapped 'Casper Ruud' onto an astatc PLAYER PROP instead of
    the match moneyline. A human's directed trade must map to exactly
    the market implied by the slug grammar or refuse outright."""
    client = _get_client()
    for slug in candidate_slugs:
        if not slug:
            continue
        try:
            m = (client.markets.retrieve_by_slug(slug) or {}).get("market") or {}
        except Exception:  # noqa: BLE001 — 404 is expected; next candidate
            continue
        if not m.get("slug") or m.get("closed"):
            continue
        score = _outcome_score(m, outcome)
        if score >= MATCH_FLOOR:
            return {"market_slug": m["slug"], "title": m.get("title"),
                    "outcome": m.get("outcome"),
                    "matched_by": "desk_exact", "score": score}
        # Two-sided markets (tennis aec- especially) score near zero on
        # the PARENT outcome — the tradable sides live in marketSides,
        # each side its own orderable slug (the copy sleeve's tennis
        # path since 2026-08-04). Order the side that IS the outcome.
        for side in (m.get("marketSides") or []):
            if not isinstance(side, dict):
                continue
            desc, ident = side.get("description"), side.get("identifier")
            if not desc or not ident:
                continue
            sscore = _outcome_score({"outcome": desc}, outcome)
            if sscore >= MATCH_FLOOR:
                return {"market_slug": ident,
                        "title": m.get("question") or m.get("title"),
                        "outcome": desc,
                        "matched_by": "desk_exact_side", "score": sscore}
    return None


def resolve_market(market_slug: str | None, event_slug: str | None,
                   market_title: str | None, event_title: str | None,
                   outcome: str | None) -> dict | None:
    """Map a global-CLOB trade to a US market. Returns
    {"market_slug", "title", "outcome", "matched_by", "score"} or None.

    Order of attempts (cheapest/most exact first):
      1. same market slug on the US venue
      2. markets list filtered by the same event slug
      3. full-text search on the event/market title
    Every hit must still pass the outcome-similarity floor.
    """
    client = _get_client()
    diag: list[str] = []

    # 1) direct slug parity
    if market_slug:
        try:
            m = (client.markets.retrieve_by_slug(market_slug) or {}).get("market") or {}
            score = _outcome_score(m, outcome)
            if m.get("slug") and score >= MATCH_FLOOR and not m.get("closed"):
                return {"market_slug": m["slug"], "title": m.get("title"),
                        "outcome": m.get("outcome"), "matched_by": "slug", "score": score}
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
            candidates = [m for m in got
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

    def _best(cands: list[dict]) -> tuple[dict | None, float]:
        top, top_score = None, 0.0
        for m in cands:
            if m.get("closed"):
                continue
            cand_lines = _lines((m.get("title") or "") + " "
                                + (m.get("question") or ""))
            line_adj = (-0.2 if src_lines != cand_lines
                        else (0.05 if src_lines else 0.0))
            if m.get("slug"):
                sc = _outcome_score(m, outcome) + line_adj
                if sc > top_score:
                    top, top_score = m, min(sc, 1.0)
            # marketSides (schema named by the 2026-08-04 trails): each
            # side of a two-sided market is its OWN market — description
            # names the side ("Dalma Galfi"), identifier is that side's
            # slug (aec-wta-...), orderable with the same BUY_LONG flow
            # as any other market. Matching the side kills the wrong-side
            # risk structurally: we order the slug that IS his outcome.
            for side in (m.get("marketSides") or []):
                if not isinstance(side, dict):
                    continue
                desc = side.get("description")
                ident = side.get("identifier")
                if not desc or not ident:
                    continue
                ssc = _outcome_score({"outcome": desc}, outcome) + line_adj
                if ssc > top_score:
                    top = {"slug": ident, "title": m.get("question"),
                           "outcome": desc, "closed": False}
                    top_score = min(ssc, 1.0)
        return top, top_score

    best, best_score = _best(candidates)
    if best is not None and best_score >= MATCH_FLOOR:
        return {"market_slug": best["slug"], "title": best.get("title"),
                "outcome": best.get("outcome"),
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
                if m.get("outcome") or m.get("team"):
                    candidates.append(m)
                    continue
                slug = m.get("slug")
                if not slug:
                    continue
                try:
                    full = (client.markets.retrieve_by_slug(slug) or {})                         .get("market") or {}
                    candidates.append(full or m)
                    hydrated += 1
                except Exception:  # noqa: BLE001 — score the skeleton instead
                    candidates.append(m)
            diag.append(f"hydrated:{hydrated}/{len(candidates)}")

    best2, best2_score = _best(candidates)
    if best2 is not None and best2_score >= MATCH_FLOOR:
        return {"market_slug": best2["slug"], "title": best2.get("title"),
                "outcome": best2.get("outcome"),
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


def submit_fok(us_market_slug: str, limit_price: float, quantity: int,
               sell: bool = False) -> dict:
    """Preview then place a FOK limit order. Returns the same normalized
    shape the global executor uses:
    {ok, order_id, status, fill_price, filled_shares, raw}.

    sell=True places SELL_LONG (underdog cash-out sleeve, owner directive
    2026-08-08) — the limit is then the MINIMUM acceptable price, so a
    fill can only ever realize at least the requested profit. The
    preview cost-tolerance guard is buy-shaped (it bounds what we PAY);
    a sell's preview reports proceeds, so the guard is skipped."""
    client = _get_client()
    params = {
        "marketSlug": us_market_slug,
        "intent": "ORDER_INTENT_SELL_LONG" if sell
                  else "ORDER_INTENT_BUY_LONG",
        "type": "ORDER_TYPE_LIMIT",
        "price": _amount(limit_price),
        "quantity": int(quantity),
        "tif": "TIME_IN_FORCE_FILL_OR_KILL",
        "synchronousExecution": True,
    }

    # The venue's own cost calculation must agree with ours before we commit.
    expected_cost = limit_price * quantity
    if not sell:
        preview = client.orders.preview(
            {"request": {k: v for k, v in params.items()
                         if k != "synchronousExecution"}})
        prev_order = (preview or {}).get("order") or {}
        prev_cost = _order_cost(prev_order, default=expected_cost)
        if prev_cost > expected_cost * PREVIEW_COST_TOLERANCE:
            return {"ok": False, "order_id": None,
                    "status": "preview_mismatch",
                    "fill_price": None, "filled_shares": 0.0,
                    "raw": {"preview": preview,
                            "expected_cost": expected_cost}}

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
    ok = filled > 0 and state in ("ORDER_STATE_FILLED", "ORDER_STATE_PARTIALLY_FILLED")
    fill_price = round(notional / filled, 4) if filled > 0 else None
    return {"ok": ok, "order_id": order_id,
            "status": state.replace("ORDER_STATE_", "").lower() or "unknown",
            "fill_price": fill_price, "filled_shares": filled,
            "raw": {"preview": prev_order, "response": resp}}


def _amount_value(a: Any) -> float:
    try:
        return float((a or {}).get("value") or 0)
    except (TypeError, ValueError):
        return 0.0


def _order_cost(order: dict, default: float) -> float:
    cash = _amount_value(order.get("cashOrderQty"))
    if cash > 0:
        return cash
    px = _amount_value(order.get("price"))
    qty = float(order.get("quantity") or 0)
    return px * qty if px and qty else default


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
