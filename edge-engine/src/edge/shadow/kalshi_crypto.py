"""Kalshi CRYPTO copy leg (owner order 2026-08-21 afternoon).

"Take a zero off their trades": the two verified crypto systematics
from the non-sports dossier (0xf705fa04 +$2.28M lifetime,
JnStrtPrdctnMrkts +$799k/5mo) are copied onto Kalshi at 1/10th of
their notional — his $100 is our $10 — in crypto price markets only.

The rules are MATH, derived so the source's profitability survives
translation (owner: "ensure fees don't impact profitability... only
profitably"):

1. EXACT-TWIN MAPPING, FAIL CLOSED. A copy fires only when Kalshi
   lists the mathematically identical contract: same asset, same
   comparator, strike equal within 2 basis points (covering the
   venues' 67,499.99-vs-67,500 cent convention, never a different
   band), and settlement time equal within 5 minutes. Everything else
   is refused with a named funnel reason: path-dependent markets
   ("reach"/"dip to" — touch is a different bet than settle-price),
   open-referenced up/down hourlies, monthlies, and his SELL/below
   flow (the V2 book is YES-denominated and this desk never shorts).

2. FEE-LOADED SAME-OR-BETTER. Kalshi's taker fee is
   0.07*p*(1-p) per contract. Our IOC limit is the highest whole cent
   satisfying  limit + fee(limit) <= his_price  — so every filled
   contract costs us, fees INCLUDED, at most what he paid. If his
   entry is +EV at his price, ours is at least as +EV per contract by
   construction. No fill is ever accepted at worse-than-his terms.

3. 1/10 SIZING, BOUNDED. clip = min(his_notional/10, $250 cap);
   whole contracts; skip if that buys zero contracts.

4. BAND + FRESHNESS. His price must sit in [0.10, 0.95] (extreme
   tails carry the worst fee-to-edge ratio and the least strike
   liquidity), and the fill must be <= 90s old — crypto moves; a
   stale copy is a different trade.

5. NEVER-ADD + NO-CROSS. One position per Kalshi market ever
   (ledger.position is the referee), and never both sides of one
   event.

6. LEG BREAKER. Realized losses over any rolling 24h at or beyond
   $300 pause the leg by itself (env EDGE_KCRYPTO_HALT_USD). Global
   stops (kill switch / watchdog) bind through live_blocked. Master
   arm: EDGE_KCRYPTO (default on), poll EDGE_KCRYPTO_POLL_S (2.5s).

Grading: every fill is ledger category "kalshi_crypto" with the
source whale in the decision, so the funnel and the per-whale
scorecard read this leg on its own line from day one.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("edge.kalshi_crypto")

WHALES = ("0xf705fa04", "jnstrtprdctnmrkts")
PER_NOTIONAL_DIVISOR = 10.0          # "take a zero off"
MAX_USD = float(os.environ.get("EDGE_KCRYPTO_MAX_USD", "250"))
MAX_AGE_S = float(os.environ.get("EDGE_KCRYPTO_MAX_AGE_S", "90"))
PRICE_MIN = 0.10
PRICE_MAX = 0.95
STRIKE_TOL_BP = 2.0                  # 2 basis points of the strike
DEADLINE_TOL_S = 300.0
HALT_USD = float(os.environ.get("EDGE_KCRYPTO_HALT_USD", "300"))
SERIES = [s.strip().upper() for s in os.environ.get(
    "EDGE_KCRYPTO_SERIES",
    "KXBTC,KXBTCD,KXETH,KXETHD,KXSOL,KXSOLD,KXXRP,KXXRPD,KXDOGED"
).split(",") if s.strip()]

_ASSET_WORDS = (
    ("bitcoin", "BTC"), ("btc", "BTC"),
    ("ethereum", "ETH"), ("eth", "ETH"),
    ("solana", "SOL"), ("sol", "SOL"),
    ("xrp", "XRP"), ("doge", "DOGE"), ("dogecoin", "DOGE"),
)
_SERIES_ASSET = (("KXBTC", "BTC"), ("KXETH", "ETH"), ("KXSOL", "SOL"),
                 ("KXXRP", "XRP"), ("KXDOGE", "DOGE"))

# Path-dependent / structurally different market families: a touch
# ("reach", "dip to") settles on the extreme of the path; Kalshi crypto
# settles on the price AT close. Copying one as the other is a leak.
_REFUSE_WORDS = ("reach", "dip", "touch", "hit-", "all-time", "ath",
                 "updown", "up-or-down", "best-month", "flip",
                 "etf", "market-cap", "fdv", "before-")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"))}

funnel: dict[str, Any] = {
    "seen": 0, "placed": 0, "filled": 0, "contracts": 0,
    "deployed_usd": 0.0, "fees_usd": 0.0,
    "refused": {}, "by_whale": {}, "last_error": None,
    "halted": False,
}


def _refuse(reason: str) -> None:
    funnel["refused"][reason] = funnel["refused"].get(reason, 0) + 1


def taker_fee(p: float) -> float:
    """Kalshi taker fee per contract at price p."""
    return 0.07 * p * (1.0 - p)


def fee_loaded_limit(his_price: float) -> float | None:
    """Highest whole-cent limit with limit + fee(limit) <= his_price.

    Guarantees every filled contract costs at most his entry price,
    fees included — the copy can never be worse-per-contract than the
    source. None when no cent in (0, his_price] satisfies it."""
    # Walk down from his own cent: the first cent that satisfies the
    # bound is maximal by construction (the fee is at most 1.75c, so
    # this terminates within a few steps).
    limit = math.floor(his_price * 100) / 100.0
    for _ in range(6):
        if limit <= 0:
            return None
        if limit + taker_fee(limit) <= his_price + 1e-9:
            return round(limit, 2)
        limit = round(limit - 0.01, 2)
    return None


def _num(tok: str) -> float | None:
    """'82pt5k' -> 82500, '67500' -> 67500, '1pt6' -> 1.6, '0pt55' -> .55"""
    tok = tok.lower().strip("$,")
    mult = 1.0
    if tok.endswith("k"):
        mult, tok = 1000.0, tok[:-1]
    tok = tok.replace("pt", ".")
    try:
        return float(tok) * mult
    except ValueError:
        return None


def _deadline(s: str) -> tuple[float | None, str]:
    """Settlement epoch from the slug/title, ET-aware.

    Explicit '...-at-5pm-et-...' beats everything; a bare
    'on-august-21(-2026)' is the venue's daily class, which resolves
    at 12:00 ET — tagged 'daily-noon' so grading can split the cohort.
    Anything else (monthly, 'by-<date>' windows) returns None."""
    m = re.search(r"at-(\d{1,2})(?:-(\d{2}))?-?(am|pm)-et(?:-|\b)", s)
    hour = None
    minute = 0
    if m:
        hour = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        minute = int(m.group(2) or 0)
    elif re.search(r"\d-?(am|pm)(-|\b)", s):
        # The slug names a clock time this parser did NOT understand
        # (format drift: missing -et, new separator, …). Defaulting to
        # daily-noon here matched the whale's 8pm bet onto the noon
        # market (audit 2026-08-21) — refuse instead; fail closed.
        return None, "unparsed-hour"
    dm = re.search(
        r"on-(" + "|".join(_MONTHS) + r")-(\d{1,2})(?:-(\d{4}))?", s)
    if not dm:
        return None, "no-deadline"
    month = _MONTHS[dm.group(1)]
    day = int(dm.group(2))
    year = int(dm.group(3) or datetime.now(timezone.utc).year)
    klass = "explicit-hour" if hour is not None else "daily-noon"
    if hour is None:
        hour = 12
    try:
        from zoneinfo import ZoneInfo
        dt = datetime(year, month, day, hour, minute,
                      tzinfo=ZoneInfo("America/New_York"))
    except ValueError:
        return None, "bad-date"
    return dt.timestamp(), klass


def parse_candidate(slug: str, title: str) -> dict | None:
    """PM crypto market -> {asset, kind, strikes, deadline_ts, klass}
    or None (with a funnel reason) when it has no exact Kalshi twin
    class. Fail closed everywhere."""
    s = f"{(slug or '').lower()} {(title or '').lower()}"
    s = s.replace(" ", "-")
    for w in _REFUSE_WORDS:
        if w in s:
            _refuse(f"class:{w.strip('-')}")
            return None
    asset = next((a for w, a in _ASSET_WORDS if w in s), None)
    if asset is None:
        _refuse("no-asset")
        return None
    deadline, klass = _deadline(s)
    if deadline is None:
        _refuse(f"deadline:{klass}")
        return None
    rng = re.search(r"between-\$?([0-9a-z.]+)-(?:and-)?\$?([0-9a-z.]+)", s)
    if rng:
        lo, hi = _num(rng.group(1)), _num(rng.group(2))
        if lo is None or hi is None or not (0 < lo < hi):
            _refuse("bad-range")
            return None
        return {"asset": asset, "kind": "range", "strikes": (lo, hi),
                "deadline_ts": deadline, "klass": klass}
    thr = re.search(r"(above|below|under|over)-\$?([0-9a-z.]+)", s)
    if not thr:
        _refuse("no-comparator")
        return None
    strike = _num(thr.group(2))
    if strike is None or strike <= 0:
        _refuse("bad-strike")
        return None
    if thr.group(1) in ("below", "under"):
        # The V2 book is YES-denominated and this desk never shorts:
        # his 'below' has no exact executable twin for us.
        _refuse("below-unsupported")
        return None
    return {"asset": asset, "kind": "above", "strikes": (strike,),
            "deadline_ts": deadline, "klass": klass}


# ── Kalshi side ──────────────────────────────────────────────────────
_mkt_cache: dict[str, Any] = {"ts": 0.0, "markets": []}
_MKT_TTL_S = 45.0


def _parse_close(v: Any) -> float | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(
            str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def kalshi_crypto_markets(client) -> list[dict]:
    """Open crypto markets across the configured series, shaped as
    {ticker, asset, kind, strikes, close_ts}. Strikes come from the
    venue's own floor_strike/cap_strike fields — a market without
    explicit strikes is skipped (never guessed from the ticker)."""
    import requests

    from edge.venues.kalshi import BASE

    now = time.time()
    if now - _mkt_cache["ts"] < _MKT_TTL_S:
        return _mkt_cache["markets"]
    out: list[dict] = []
    for series in SERIES:
        asset = next((a for p, a in _SERIES_ASSET
                      if series.startswith(p)), None)
        if asset is None:
            continue
        cursor = ""
        for _ in range(4):
            try:
                resp = client._sess.get(
                    f"{BASE}/events",
                    params={"series_ticker": series, "status": "open",
                            "with_nested_markets": "true", "limit": 100,
                            **({"cursor": cursor} if cursor else {})},
                    timeout=12)
            except requests.RequestException as exc:
                funnel["last_error"] = f"{series}: {type(exc).__name__}"
                break
            if resp.status_code != 200:
                funnel["refused"][f"series-http:{series}"] = \
                    resp.status_code
                break
            data = resp.json() or {}
            for ev in data.get("events") or []:
                for mm in ev.get("markets") or []:
                    if (mm.get("status") or "open") not in (
                            "open", "active"):
                        continue
                    close_ts = _parse_close(mm.get("close_time"))
                    if close_ts is None or not mm.get("ticker"):
                        continue
                    floor = mm.get("floor_strike")
                    cap = mm.get("cap_strike")
                    stype = (mm.get("strike_type") or "").lower()
                    try:
                        floor = float(floor) if floor is not None else None
                        cap = float(cap) if cap is not None else None
                    except (TypeError, ValueError):
                        continue
                    if stype in ("between",) and floor is not None \
                            and cap is not None:
                        out.append({"ticker": mm["ticker"],
                                    "asset": asset, "kind": "range",
                                    "strikes": (floor, cap),
                                    "close_ts": close_ts})
                    elif floor is not None and cap is None:
                        out.append({"ticker": mm["ticker"],
                                    "asset": asset, "kind": "above",
                                    "strikes": (floor,),
                                    "close_ts": close_ts})
            cursor = data.get("cursor") or ""
            if not cursor:
                break
    if out or now - _mkt_cache["ts"] > 300:
        _mkt_cache.update(ts=now, markets=out)
    return _mkt_cache["markets"]


def _strike_eq(a: float, b: float) -> bool:
    """Equal within 2bp of the strike (venue cent conventions like
    67,499.99 vs 67,500), never a neighboring band.

    Purely RELATIVE (audit 2026-08-21): the old $0.02 absolute floor
    was sized for the cent convention at BTC scale but spanned real
    strike spacing on sub-dollar assets — DOGE $0.12 vs $0.13 matched,
    i.e. a strictly different bet. 2bp of the strike covers every cent
    convention at every scale (67,499.99 vs 67,500 is 0.15bp; 0.12999
    vs 0.13 is 0.8bp) and can never reach a neighboring band."""
    return abs(a - b) <= abs(b) * STRIKE_TOL_BP / 10_000.0 + 1e-9


def match_market(cand: dict, markets: list[dict]) -> dict | None:
    """The CLOSEST passing twin, never merely the first (audit
    2026-08-21): with a tolerance that admits more than one listed
    strike, listing order must not decide which bet we copy — a tie
    inside tolerance is ambiguity, and ambiguity is a refusal."""
    hits: list[tuple[float, dict]] = []
    for m in markets:
        if m["asset"] != cand["asset"] or m["kind"] != cand["kind"]:
            continue
        if abs(m["close_ts"] - cand["deadline_ts"]) > DEADLINE_TOL_S:
            continue
        if cand["kind"] == "above":
            if _strike_eq(m["strikes"][0], cand["strikes"][0]):
                hits.append((abs(m["strikes"][0] - cand["strikes"][0]), m))
        else:
            if _strike_eq(m["strikes"][0], cand["strikes"][0]) and \
                    _strike_eq(m["strikes"][1], cand["strikes"][1]):
                hits.append((abs(m["strikes"][0] - cand["strikes"][0])
                             + abs(m["strikes"][1] - cand["strikes"][1]), m))
    if not hits:
        return None
    hits.sort(key=lambda h: h[0])
    # Tie = ambiguity = refusal. The threshold is relative to the
    # strike so float noise at BTC scale (0.01 vs 0.01 computed as
    # 1.0000000002e-2 vs 9.999999997e-3) still reads as a tie.
    tie_eps = max(1e-9, abs(cand["strikes"][0]) * 1e-9)
    if len(hits) > 1 and abs(hits[0][0] - hits[1][0]) < tie_eps:
        return None
    return hits[0][1]


def plan(cand_price: float, cand_notional: float) -> tuple[float, int] | None:
    """(limit, contracts) under the fee-loaded rule and 1/10 sizing."""
    if not (PRICE_MIN <= cand_price <= PRICE_MAX):
        _refuse("price-band")
        return None
    limit = fee_loaded_limit(cand_price)
    if limit is None or limit < 0.01:
        _refuse("fee-eats-price")
        return None
    usd = min(cand_notional / PER_NOTIONAL_DIVISOR, MAX_USD)
    contracts = int(usd / limit)
    if contracts < 1:
        _refuse("too-small")
        return None
    return limit, contracts


def _event_key(ticker: str) -> str:
    return ticker.rsplit("-", 1)[0] if "-" in ticker else ticker


def sweep(client, ledger, risk, candidates: list[dict]) -> None:
    """One pass over fresh candidates. Every skip is a named funnel
    count; every order is idempotent by trade id."""
    from edge.shadow.kalshi_guard import live_blocked

    if os.environ.get("EDGE_KCRYPTO", "1") != "1":
        funnel["halted"] = "EDGE_KCRYPTO=0"
        return
    blocked = live_blocked(ledger, scope="manual")
    if blocked:
        funnel["halted"] = f"global: {blocked}"
        return
    realized_24h = ledger.realized_pnl_since_for_category(
        time.time() - 86400, "kalshi_crypto")
    if realized_24h <= -HALT_USD:
        funnel["halted"] = f"leg breaker: {realized_24h:.2f} <= -{HALT_USD}"
        return
    # NO strategy-mode gate, LIVE labels always (audit 2026-08-21, then
    # root-caused the same evening): the audit's real finding was that
    # this leg placed real orders while LABELING the fills PAPER — the
    # leg breaker (realized_pnl filters PAPER out) could never see the
    # losses. The first fix held the leg whenever the engine strategy
    # sat in PAPER, but the ENGINE class defers to paper on a stale
    # odds-feed checklist that has nothing to do with this leg (its
    # candidates and prices come from the platform, not the feed) — and
    # the owner armed it explicitly. So: the leg runs regardless of
    # strategy mode, every fill is recorded LIVE_BETA (it IS real
    # money), and the breaker always sees it. Kill switch and watchdog
    # (live_blocked above) still stop it cold.
    funnel["halted"] = False
    markets = None
    now = time.time()
    # One snapshot per sweep (audit 2026-08-21): open_positions ran a
    # full table query per CANDIDATE for the event-cross guard. Fills
    # within this sweep append to it manually below.
    open_keys = [p["market_key"]
                 for p in ledger.open_positions(live_only=True)]
    for c in candidates:
        funnel["seen"] += 1
        w = (c.get("username") or "").lower()
        bw = funnel["by_whale"].setdefault(w, {"seen": 0, "filled": 0})
        bw["seen"] += 1
        if w not in WHALES:
            _refuse("unknown-whale")
            continue
        # Direction is the one field that inverts the bet: the platform
        # endpoint filters side='BUY' in SQL, but this leg must not be
        # fail-open on it (audit 2026-08-21) — a SELL copied as our BUY
        # is the opposite of the whale's intent.
        if (c.get("side") or "").upper() != "BUY":
            _refuse("not-a-buy")
            continue
        ts = float(c.get("ts_epoch") or 0)
        if not ts or now - ts > MAX_AGE_S:
            _refuse("stale")
            continue
        # A missing id would collapse every candidate onto fill_uid
        # 'kcr-None': the first fill records, later ones are silently
        # dropped by INSERT OR IGNORE and never-add can't see them.
        if c.get("id") is None:
            _refuse("no-id")
            continue
        fill_uid = f"kcr-{c.get('id')}"
        cand = parse_candidate(c.get("market_slug") or "",
                               c.get("market_title") or "")
        if cand is None:
            continue
        price = float(c.get("price") or 0)
        notional = float(c.get("notional") or 0)
        planned = plan(price, notional)
        if planned is None:
            continue
        limit, contracts = planned
        if markets is None:
            markets = kalshi_crypto_markets(client)
        m = match_market(cand, markets)
        if m is None:
            _refuse("no-kalshi-twin")
            continue
        # Never-add / no-cross: one position per market, and never the
        # other side of an event we already hold.
        if ledger.position(f"kalshi:{m['ticker']}"):
            _refuse("never-add")
            continue
        held_event = any(
            k.startswith("kalshi:")
            and _event_key(k.split(":", 1)[1]) == _event_key(m["ticker"])
            for k in open_keys)
        if held_event:
            _refuse("event-cross")
            continue
        pr = client.place_order(m["ticker"], limit, contracts,
                                client_order_id=fill_uid, taker=True)
        funnel["placed"] += 1
        filled = int(pr.get("filled_count") or pr.get("count") or 0) \
            if pr.get("ok") else 0
        if pr.get("ok") and filled > 0:
            fee = round(math.ceil(taker_fee(limit) * filled * 100)
                        / 100, 2)
            # Mark the order as inline-recorded BEFORE record_fill so
            # sync_kalshi_fills never re-records the same contracts
            # under its own kalshi-fill-{trade_id} uid — the exact
            # double-position bug the 2026-08-04 audit fixed for the
            # engine class (audit 2026-08-21 found this leg missing it).
            if pr.get("order_id"):
                ledger.set_state(f"kalshi_inline:{pr['order_id']}",
                                 {"ts": time.time(), "uid": fill_uid})
            ledger.record_fill(
                fill_uid=fill_uid, venue="kalshi",
                market_key=f"kalshi:{m['ticker']}",
                side="BUY", qty=float(filled), price=limit, fee=fee,
                league=cand["asset"].lower(),
                mode="LIVE_BETA",
                category="kalshi_crypto",
                decision={"whale": w, "his_price": price,
                          "his_notional": notional,
                          "limit": limit, "klass": cand["klass"],
                          "pm_slug": c.get("market_slug")})
            open_keys.append(f"kalshi:{m['ticker']}")
            funnel["filled"] += 1
            funnel["contracts"] += filled
            funnel["deployed_usd"] = round(
                funnel["deployed_usd"] + filled * limit, 2)
            funnel["fees_usd"] = round(funnel["fees_usd"] + fee, 2)
            bw["filled"] += 1
            log.warning("KCRYPTO fill %s x%d @ %.2f (his %.2f, %s)",
                        m["ticker"], filled, limit, price, w)
        elif pr.get("ok"):
            _refuse("unfilled-at-limit")
        else:
            _refuse("order-error")
            funnel["last_error"] = str(pr.get("status"))[:120]
