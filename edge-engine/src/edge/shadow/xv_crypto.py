"""24/7 cross-venue crypto-binary scanner: Kalshi 15-minute BTC over/under
markets vs Polymarket US short-interval crypto binaries (owner-approved
2026-08-05). PHASE PRIORITY IS MEASUREMENT: count how many locked
dislocations actually occur before any capital chases them.

IDENTITY JOIN — a pair is valid ONLY when all three match:
  1. same underlying (BTC),
  2. same expiry timestamp, to the minute,
  3. same strike value (exact, with a 1-cent tolerance for the venues'
     "$114,249.99 or above" convention).
Direction is mapped EXPLICITLY, never guessed: every market is normalized
to what its YES pays on — "above" (settles at/over the strike) or "below"
— and a joinable pair is one of each. "Up or down" interval markets carry
an IMPLICIT strike (the interval's opening print), which is a different
proposition than a fixed-strike binary; with no numeric strike they are
refused at parse time.

INDEX BASIS — THE HONESTY CONSTRAINT. The venues settle on DIFFERENT
price indexes: Kalshi settles its BTC markets on its own reference index,
Polymarket US on its oracle's feed. Near the strike at expiry the two
indexes can land on opposite sides, so a "complete set" across these
venues is NOT the riskless $1 a same-venue dutch book is: BOTH legs can
lose. A cross-venue crypto pair is guaranteed only OUTSIDE index-basis
risk. Consequence: the minimum profit is 3c/set (EDGE_XV_CRYPTO_MIN_PROFIT,
default 0.03) — a buffer that must pay for the rare double-loss — versus
xv_watch's 2c for same-game sports pairs, and MAX_BELIEVABLE_PROFIT still
refuses fantasy gaps that are really mapping errors.

EXECUTION — MEASURE-ONLY unless EDGE_XV_CRYPTO_LIVE=1 AND the engine is
in a live mode. The live path copies the fixed xv_watch pattern exactly:
kalshi_guard.live_blocked honored, ATOMIC ledger claim BEFORE execution,
claim released on a clean miss, execute_cross_venue's all-or-nothing
sequencing, every fill ledger-recorded, day cap EDGE_XV_CRYPTO_DAY_USD
(default $100). The Kalshi same-game guard does not reach this path (it
governs a second Kalshi side of one sports game; a pair here holds ONE
Kalshi side, its complement lives on Polymarket US) and would pass the
pair naturally anyway — verified in tests. Kill switch: EDGE_XV_CRYPTO=0
disables the whole thread.

CONTENTION RULES (inherited from xv_watch): discovery runs on this
thread's OWN HTTP clients — a private requests.Session for Kalshi and a
private public-gateway client for Polymarket US — never the sweep's
shared sessions. PMUS books come from the push-stream cache first
(peek_book); the REST fallback also uses the private client. 429s are
backoff signals, never errors, and requests are paced.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from edge.execution.arbitrage import (
    MAX_BELIEVABLE_PROFIT,
    XVLeg,
    cross_venue_cost,
    execute_cross_venue,
)

log = logging.getLogger(__name__)

KALSHI_BASE = os.environ.get(
    "EDGE_KALSHI_BASE", "https://api.elections.kalshi.com/trade-api/v2")

# Fallback only: series discovery asks the venue (census-verified via the
# engine-diagnostic workflow); these are used when the listing endpoint is
# unreachable, and a wrong ticker fails loudly as a named census entry.
_FALLBACK_SERIES = ["KXBTC", "KXBTCD", "KXBTC15M"]


# ── identity ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CryptoBinary:
    """One venue market normalized to a fixed-strike binary identity."""

    venue: str        # "kalshi" | "polymarket-us"
    token: str        # market ticker (kalshi) / market slug (pmus)
    underlying: str   # "BTC" — the only underlying in scope
    expiry_ts: int    # unix seconds, venue close/settlement time
    strike: float     # dollars
    side: str         # what YES pays on: "above" | "below"
    title: str = ""

    @property
    def expiry_min(self) -> int:
        return int(self.expiry_ts // 60)


def _iso_ts(s) -> int | None:
    try:
        return int(datetime.fromisoformat(
            str(s).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def parse_kalshi_market(m: dict) -> CryptoBinary | None:
    """Kalshi nested-market dict -> CryptoBinary, or None (refused).

    Direction comes from the venue's own strike_type field — structural,
    not prose: greater* means YES pays at/over floor_strike ("above"),
    less* means YES pays at/under cap_strike ("below"). Anything else
    ("between", customized) is not a single-strike binary and is refused.
    """
    ticker = str(m.get("ticker") or "")
    if "BTC" not in ticker.upper():
        return None
    exp = _iso_ts(m.get("close_time") or m.get("expected_expiration_time")
                  or m.get("expiration_time"))
    if exp is None:
        return None
    st = str(m.get("strike_type") or "").lower()
    if st.startswith("greater"):
        side, strike = "above", m.get("floor_strike")
    elif st.startswith("less"):
        side, strike = "below", m.get("cap_strike")
    else:
        return None
    try:
        strike = float(strike)
    except (TypeError, ValueError):
        return None
    return CryptoBinary(
        venue="kalshi", token=ticker, underlying="BTC", expiry_ts=exp,
        strike=strike, side=side,
        title=str(m.get("yes_sub_title") or m.get("subtitle") or ticker)[:80])


_STRIKE_RE = re.compile(r"\$\s*([0-9][\d,]*(?:\.\d+)?)")
_ABOVE_WORDS = ("above", "higher", "over", "or more", ">=", "greater")
_BELOW_WORDS = ("below", "lower", "under", "or less", "<=")
# Bare complement outcomes take the EVENT proposition; True = inverted.
_COMPLEMENT = {"yes": False, "up": False, "no": True, "down": True}


def _direction(text: str) -> str | None:
    t = (text or "").lower()
    above = any(w in t for w in _ABOVE_WORDS)
    below = any(w in t for w in _BELOW_WORDS)
    if above == below:      # both or neither: ambiguous — refuse, never guess
        return None
    return "above" if above else "below"


def parse_pmus_market(ev: dict, m: dict,
                      census: dict | None = None) -> CryptoBinary | None:
    """Polymarket US (event, market) -> CryptoBinary, or None (refused).

    Every refusal is counted in `census` so the funnel shows WHY nothing
    joined instead of just a zero. up/down markets fall out at no_strike:
    their strike is the interval's opening print, which is not a number we
    can join on.
    """
    def note(k: str) -> None:
        if census is not None:
            census[k] = census.get(k, 0) + 1

    if m.get("closed"):
        note("closed")
        return None
    slug = m.get("slug")
    if not slug:
        note("no_slug")
        return None
    ev_title = str(ev.get("title") or "")
    m_text = " ".join(str(x) for x in (m.get("title"), m.get("outcome")) if x)
    combined = f"{ev_title} {m_text}".lower()
    if "btc" not in combined and "bitcoin" not in combined:
        note("not_btc")
        return None
    exp = None
    for src in (m, ev):
        for k in ("closeTime", "close_time", "endTime", "end_time",
                  "endDate", "end_date", "expirationTime", "closedTime"):
            if src.get(k):
                exp = _iso_ts(src.get(k))
                if exp:
                    break
        if exp:
            break
    if not exp:
        note("no_expiry")
        return None
    hit = _STRIKE_RE.search(m_text) or _STRIKE_RE.search(ev_title)
    if not hit:
        note("no_strike")     # the up/down class lands here — refused
        return None
    strike = float(hit.group(1).replace(",", ""))
    if strike < 100:          # a "$1" payout mention is not a BTC strike
        note("no_strike")
        return None
    low = m_text.strip().lower()
    if low in _COMPLEMENT:
        side = _direction(ev_title)
        if side is None:
            note("no_direction")
            return None
        if _COMPLEMENT[low]:
            side = "below" if side == "above" else "above"
    else:
        side = _direction(m_text) or _direction(ev_title)
        if side is None:
            note("no_direction")
            return None
    return CryptoBinary(venue="polymarket-us", token=slug, underlying="BTC",
                        expiry_ts=exp, strike=strike, side=side,
                        title=(m.get("title") or ev_title)[:80])


def joinable(a: CryptoBinary, b: CryptoBinary,
             strike_tol: float = 0.01) -> bool:
    """Same underlying, same expiry minute, same strike, complementary
    YES directions, different venues. Anything less is not a set."""
    return (a.venue != b.venue
            and a.underlying == b.underlying
            and a.expiry_min == b.expiry_min
            and abs(a.strike - b.strike) <= strike_tol + 1e-9
            and {a.side, b.side} == {"above", "below"})


def join_pairs(kalshi: list[CryptoBinary], pmus: list[CryptoBinary],
               strike_tol: float = 0.01,
               ) -> list[tuple[CryptoBinary, CryptoBinary]]:
    out = []
    for k in kalshi:
        for p in pmus:
            if joinable(k, p, strike_tol):
                out.append((k, p))
    return out


def pair_key(k: CryptoBinary, p: CryptoBinary) -> str:
    return f"xvc:{k.underlying}:{k.expiry_min}:{k.strike:g}"


# ── the scanner ─────────────────────────────────────────────────────────

class XVCryptoWatch:
    """Mirrors XVWatch's structure and pacing; see the module docstring
    for what differs (own discovery, 3c basis floor, measure-only gate)."""

    def __init__(self, *, ledger, kalshi, pmus, is_live,
                 pmus_pub=None, min_profit: float | None = None,
                 max_usd: float | None = None, day_usd: float | None = None,
                 poll_s: float | None = None,
                 discovery_s: float | None = None,
                 strike_tol: float | None = None,
                 horizon_s: float | None = None) -> None:
        env = os.environ.get

        def _f(explicit, key, default):
            return float(explicit if explicit is not None
                         else env(key, default))

        self._ledger = ledger
        self._kalshi = kalshi
        self._pmus = pmus
        self._is_live = is_live          # callable: () -> bool (engine mode)
        self._pub = pmus_pub             # False disables; None = lazy-create
        self.min_profit = _f(min_profit, "EDGE_XV_CRYPTO_MIN_PROFIT", "0.03")
        # max_usd is the TOTAL pair cost, BOTH legs combined (~$5/side —
        # owner directive 2026-08-05, guaranteed-profit class): sizing
        # divides it by cross_venue_cost([l1, l2]), the fee-loaded
        # two-leg cost of one set, never by a single leg's price.
        self.max_usd = _f(max_usd, "EDGE_XV_CRYPTO_MAX_USD", "10")
        self.day_usd = _f(day_usd, "EDGE_XV_CRYPTO_DAY_USD", "100")
        self.poll_s = _f(poll_s, "EDGE_XV_CRYPTO_POLL_S", "10")
        self.discovery_s = _f(discovery_s, "EDGE_XV_CRYPTO_DISCOVERY_S", "60")
        self.strike_tol = _f(strike_tol, "EDGE_XV_CRYPTO_STRIKE_TOL", "0.01")
        # 2 x 15-minute intervals + slack: the current and the next one.
        self.horizon_s = _f(horizon_s, "EDGE_XV_CRYPTO_HORIZON_S", "2100")
        self._sess = requests.Session()  # OWN session — no sweep contention
        self._lock = threading.Lock()
        self._series: list[str] | None = None
        self._series_at = 0.0
        self._pairs: list[tuple[CryptoBinary, CryptoBinary]] = []
        self._seen_pairs: set[str] = set()
        self._discovered_at = 0.0
        self.stats: dict = {"scans": 0, "pairs_seen": 0, "joined": 0,
                            "kalshi_mkts": 0, "pmus_mkts": 0,
                            "best_gap_c": -99.0, "would_fire": 0,
                            "fired": 0, "spent": 0.0, "last_hit": None}

    # ── discovery (network; stubbed in tests) ───────────────────────────

    def _btc_series(self) -> list[str]:
        override = os.environ.get("EDGE_XV_CRYPTO_KALSHI_SERIES", "")
        if override:
            return [s.strip() for s in override.split(",") if s.strip()]
        if self._series is not None and time.time() - self._series_at < 3600:
            return self._series
        found: list[str] = []
        for cat in ("Crypto", "Financials"):
            try:
                r = self._sess.get(f"{KALSHI_BASE}/series",
                                   params={"category": cat, "limit": 200},
                                   timeout=15)
                if r.status_code == 429:
                    time.sleep(2.0)      # backoff signal, never an error
                    continue
                if r.status_code != 200:
                    continue
                for s in (r.json().get("series") or []):
                    t = str(s.get("ticker") or "")
                    if "BTC" in t.upper() and t not in found:
                        found.append(t)
            except (requests.RequestException, ValueError) as exc:
                log.info("xv_crypto series listing (%s) failed: %s", cat, exc)
            time.sleep(0.2)
        if found:
            self._series, self._series_at = found[:16], time.time()
            self.stats["series"] = self._series
            return self._series
        return list(_FALLBACK_SERIES)

    def _discover_kalshi(self, now: float) -> list[CryptoBinary]:
        out: list[CryptoBinary] = []
        census: dict = {}
        for series in self._btc_series():
            try:
                r = self._sess.get(
                    f"{KALSHI_BASE}/events",
                    params={"series_ticker": series, "status": "open",
                            "with_nested_markets": "true", "limit": 50},
                    timeout=15)
            except requests.RequestException as exc:
                census[f"{series}_err"] = type(exc).__name__
                continue
            if r.status_code == 429:
                census["http_429"] = census.get("http_429", 0) + 1
                time.sleep(1.0)
                continue
            if r.status_code != 200:
                census[f"{series}_http"] = r.status_code
                continue
            try:
                data = r.json() or {}
            except ValueError:
                census[f"{series}_err"] = "bad_json"
                continue
            n = 0
            for ev in data.get("events") or []:
                for m in ev.get("markets") or []:
                    cb = parse_kalshi_market(m)
                    if cb is None:
                        continue
                    # Current + next interval only: the venue lists 96/day
                    # and everything past the horizon is stale-quote bait.
                    if not (now - 60 <= cb.expiry_ts <= now + self.horizon_s):
                        continue
                    out.append(cb)
                    n += 1
            census[series] = n
            time.sleep(0.2)   # pacing: never look like a burst to the venue
        self.stats["kalshi_census"] = census
        return out

    def _pmus_client(self):
        if self._pub is None:
            try:
                from polymarket_us import PolymarketUS

                self._pub = PolymarketUS()   # OWN public client
            except Exception as exc:  # noqa: BLE001 — measurement degrades, never dies
                log.info("xv_crypto: pmus public client unavailable: %s", exc)
                self._pub = False
        return self._pub or None

    def _discover_pmus(self, now: float) -> list[CryptoBinary]:
        pub = self._pmus_client()
        census: dict = {}
        out: list[CryptoBinary] = []
        if pub is None:
            self.stats["pmus_census"] = {"error": "no_public_client"}
            return out
        try:
            events, used = [], None
            for variant in ({"active": True, "closed": False,
                             "categories": ["crypto"]},
                            {"active": True, "closed": False,
                             "categories": ["Crypto"]},
                            {"active": True, "closed": False}):
                resp = pub.events.list({"limit": 100, **variant}) or {}
                events = resp.get("events") or []
                if events:
                    used = variant
                    break
            census["params"] = used if used is not None else "all_empty"
            census["events_seen"] = len(events)
            for ev in events:
                for m in ev.get("markets") or []:
                    cb = parse_pmus_market(ev, m, census)
                    if cb is None:
                        continue
                    if not (now - 60 <= cb.expiry_ts <= now + self.horizon_s):
                        census["out_of_window"] = \
                            census.get("out_of_window", 0) + 1
                        continue
                    out.append(cb)
        except Exception as exc:  # noqa: BLE001 — census carries the cause
            census["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        self.stats["pmus_census"] = census
        return out

    # ── books ───────────────────────────────────────────────────────────

    def _rest_pmus_book(self, slug: str):
        """REST fallback on the PRIVATE public client — used only when the
        push stream has no cache for this slug, so measurement still works
        when the streamer is down. Never touches the sweep's session."""
        pub = self._pmus_client()
        if pub is None:
            return None
        try:
            raw = pub.markets.book(slug) or {}
        except Exception:  # noqa: BLE001 — a missing book is a quiet market
            return None
        body = raw
        for key in ("marketData", "book"):
            if isinstance(raw.get(key), dict):
                body = raw[key]
                break
        from edge.venues.base import MarketBook
        from edge.venues.polymarket_us import PolymarketUSAdapter

        bids, asks = PolymarketUSAdapter._parse_levels(body)
        if not asks:
            return None
        return MarketBook(venue="polymarket-us", market_id=slug,
                          outcome_id=slug, bids=bids, asks=asks,
                          ts=time.time())

    def _fresh_leg(self, cb: CryptoBinary) -> XVLeg | None:
        if cb.venue == "kalshi":
            adapter = self._kalshi
            book = adapter.get_book(cb.token, cb.token)
        else:
            adapter = self._pmus
            peek = getattr(adapter, "peek_book", None)
            book = peek(cb.token) if peek else None
            if book is None:
                book = self._rest_pmus_book(cb.token)
        if book is None or not book.asks:
            return None
        ask = book.asks[0]
        if ask.size < 1:
            return None
        return XVLeg(adapter=adapter, token=cb.token,
                     outcome=f"BTC {cb.side} {cb.strike:g}",
                     price=ask.price, size=float(ask.size))

    # ── the 24/7 loop ───────────────────────────────────────────────────

    def run(self) -> None:
        while True:
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 — the watch never dies
                log.warning("xv crypto tick failed: %s", exc)
                self.stats["last_error"] = \
                    f"{type(exc).__name__}: {str(exc)[:140]}"
            time.sleep(self.poll_s)

    def _tick(self, now: float | None = None) -> None:
        now = now or time.time()
        self.stats["scans"] += 1
        if now - self._discovered_at >= self.discovery_s or not self._pairs:
            kalshi = self._discover_kalshi(now)
            pmus = self._discover_pmus(now)
            self.stats["kalshi_mkts"] = len(kalshi)
            self.stats["pmus_mkts"] = len(pmus)
            with self._lock:
                self._pairs = join_pairs(kalshi, pmus, self.strike_tol)
                for k, p in self._pairs:
                    self._seen_pairs.add(pair_key(k, p))
                self.stats["joined"] = len(self._seen_pairs)
                self.stats["joined_now"] = len(self._pairs)
            self._discovered_at = now
        with self._lock:
            pairs = list(self._pairs)
        for kcb, pcb in pairs:
            if kcb.expiry_ts <= now:   # expired between discovery passes
                continue
            self._check(kcb, pcb)

    def _check(self, kcb: CryptoBinary, pcb: CryptoBinary) -> None:
        l1 = self._fresh_leg(kcb)
        l2 = self._fresh_leg(pcb)
        if l1 is None or l2 is None:
            return
        self.stats["pairs_seen"] += 1
        cost = cross_venue_cost([l1, l2])
        profit = round(1.0 - cost, 4)
        self.stats["best_gap_c"] = max(self.stats["best_gap_c"],
                                       round(profit * 100, 2))
        # The 3c floor is the index-basis buffer (module docstring), and
        # MAX_BELIEVABLE_PROFIT still refuses gaps that are really
        # mapping/settlement mismatches wearing a costume.
        if profit < self.min_profit or profit > MAX_BELIEVABLE_PROFIT:
            return
        self.stats["would_fire"] += 1
        label = (f"BTC {kcb.strike:g} @ "
                 f"{datetime.fromtimestamp(kcb.expiry_ts, tz=timezone.utc):%H:%M}Z")
        hit = {"pair": label, "kalshi": kcb.token, "pmus": pcb.token,
               "cost_per_set": cost, "profit_per_set": profit,
               "mode": "measure",
               "at": datetime.now(tz=timezone.utc).strftime(
                   "%Y-%m-%dT%H:%M:%SZ")}
        live = (bool(self._is_live())
                and os.environ.get("EDGE_XV_CRYPTO_LIVE", "0") == "1")
        if not live:
            # MEASUREMENT: the whole point of this phase. Record and stop —
            # no dry-run order plumbing, no claim burned.
            self.stats["last_hit"] = hit
            log.warning("XV CRYPTO would fire (measure-only): %s "
                        "profit/set %.3f", label, profit)
            return

        # ── live path: the fixed xv_watch pattern, copied exactly ───────
        day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        spend = self._ledger.get_state("xv_crypto_day") or {}
        spent = float(spend.get("spent", 0.0)) if spend.get("day") == day \
            else 0.0
        self.stats["spent"] = round(spent, 2)
        if spent >= self.day_usd:
            self.stats["skipped_day_cap"] = \
                self.stats.get("skipped_day_cap", 0) + 1
            return
        from edge.shadow.kalshi_guard import live_blocked

        blocked = live_blocked(self._ledger)
        if blocked:
            self.stats["blocked_" + blocked] = \
                self.stats.get("blocked_" + blocked, 0) + 1
            return
        key = pair_key(kcb, pcb)
        claim = f"xv_crypto_tried:{key}"
        # ATOMIC claim BEFORE execution: get-then-set left a multi-second
        # window in which two paths both bought the full set (xv_watch
        # audit fix — same shape here).
        if not self._ledger.claim_event(claim, key, "xv_crypto"):
            return
        res = execute_cross_venue(
            event=claim, legs=[l1, l2],
            max_sets=max(1, int(min(self.max_usd, self.day_usd - spent)
                                / cost)),
            dry_run=False)
        self.stats["fired"] += 1
        hit.update(mode="live", status=res.status, sets=res.sets)
        self.stats["last_hit"] = hit
        log.warning("XV CRYPTO %s: %s profit/set %.3f sets %s",
                    res.status, label, profit, res.sets)
        if res.status in ("no_fills", "nothing_to_do", "not_cross_venue",
                          "implausible_profit"):
            # Clean miss: give the claim back so the pair stays retryable.
            self._ledger.release_event(claim)
        else:
            self._ledger.set_state(claim, {"ts": time.time(),
                                           "status": res.status,
                                           "via": "xv_crypto"})
        for o in res.orders:
            if o.get("ok") and o.get("order_id") \
                    and float(o.get("count") or 0) > 0:
                self._ledger.set_state(f"kalshi_inline:{o['order_id']}",
                                       {"ts": time.time()})
        if res.status == "INCOMPLETE_EXPOSED":
            self.stats["exposed"] = self.stats.get("exposed", 0) + 1
        legs_by_token = {l.token: l for l in (l1, l2)}
        filled_cost = 0.0
        for o in res.orders:
            if not (o.get("ok") and float(o.get("count") or 0) > 0):
                continue
            leg = legs_by_token.get(o.get("token") or "")
            if leg is None:
                continue
            vname = getattr(leg.adapter, "name", "?")
            qty = float(o.get("count") or res.sets)
            px = float(o.get("price") or leg.price)
            filled_cost += qty * px
            self._ledger.record_fill(
                fill_uid=f"xvc-{claim}-{leg.token}-{int(time.time())}",
                venue=vname, market_key=f"{vname}:{leg.token}",
                side="BUY", qty=qty, price=px,
                fee=round(leg.fee() * qty, 4),
                league="crypto", mode="LIVE_BETA", category="arb_crypto",
                decision={"arb": True, "cross_venue": True,
                          "via": "xv_crypto", "underlying": kcb.underlying,
                          "strike": kcb.strike, "expiry_ts": kcb.expiry_ts,
                          "cost_per_set": cost, "profit_per_set": profit,
                          "status": res.status,
                          "legs": [f"{getattr(l.adapter, 'name', '?')}:"
                                   f"{l.outcome}" for l in (l1, l2)]})
        if filled_cost:
            self._ledger.set_state("xv_crypto_day",
                                   {"day": day,
                                    "spent": round(spent + filled_cost, 2)})
            self.stats["spent"] = round(spent + filled_cost, 2)
        if res.ok and res.complete:
            self.stats["profit"] = round(
                self.stats.get("profit", 0.0) + res.profit, 4)
