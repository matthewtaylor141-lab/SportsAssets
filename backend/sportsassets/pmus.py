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
    src_nums = set(re.findall(r"\d+(?:\.\d+)?", market_title or ""))

    def _best(cands: list[dict]) -> tuple[dict | None, float]:
        top, top_score = None, 0.0
        for m in cands:
            if m.get("closed") or not m.get("slug"):
                continue
            sc = _outcome_score(m, outcome)
            cand_nums = set(re.findall(r"\d+(?:\.\d+)?", m.get("title") or ""))
            if src_nums != cand_nums:
                sc -= 0.2
            elif src_nums:
                sc = min(1.0, sc + 0.05)
            if sc > top_score:
                top, top_score = m, sc
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
        diag.append("sample:" + str({k: c0.get(k) for k in
                                     ("title", "outcome", "team")})[:90])
    # The trail rides the exception-free path out via last_resolve_diag so
    # the caller can store WHY in the audit row without a signature break.
    resolve_market.last_diag = "; ".join(diag)[:280]
    return None


def submit_fok(us_market_slug: str, limit_price: float, quantity: int) -> dict:
    """Preview then place a BUY_LONG FOK limit order. Returns the same
    normalized shape the global executor uses:
    {ok, order_id, status, fill_price, filled_shares, raw}."""
    client = _get_client()
    params = {
        "marketSlug": us_market_slug,
        "intent": "ORDER_INTENT_BUY_LONG",
        "type": "ORDER_TYPE_LIMIT",
        "price": _amount(limit_price),
        "quantity": int(quantity),
        "tif": "TIME_IN_FORCE_FILL_OR_KILL",
        "synchronousExecution": True,
    }

    # The venue's own cost calculation must agree with ours before we commit.
    expected_cost = limit_price * quantity
    preview = client.orders.preview({"request": {k: v for k, v in params.items()
                                                 if k != "synchronousExecution"}})
    prev_order = (preview or {}).get("order") or {}
    prev_cost = _order_cost(prev_order, default=expected_cost)
    if prev_cost > expected_cost * PREVIEW_COST_TOLERANCE:
        return {"ok": False, "order_id": None, "status": "preview_mismatch",
                "fill_price": None, "filled_shares": 0.0,
                "raw": {"preview": preview, "expected_cost": expected_cost}}

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
