"""Kalshi adapter — shadow mode (public market-data REST; auth only needed
for live orders, which shadow mode never places).

Discovery: GET /events?series_ticker=...&with_nested_markets=true — one
VenueMarket per game event, outcome names from each market's yes side.
Books: GET /markets/{ticker}/orderbook — resting YES/NO bids in cents; the
executable YES ask is (100 - best NO bid).
Fees: taker ≈ 0.07 * p * (1-p) per contract (maker-first is the live-mode
answer; shadow logs taker economics so the gate is conservative).
"""

from __future__ import annotations

import logging
import os
import time

import requests

from .base import BookLevel, FillIntent, MarketBook, VenueAdapter
from .mapper import VenueMarket

log = logging.getLogger(__name__)

BASE = os.environ.get("EDGE_KALSHI_BASE", "https://api.elections.kalshi.com/trade-api/v2")

# league code -> Kalshi series tickers ("+"-separated: game/spread/total
# series are distinct on Kalshi), env-overridable:
#   EDGE_KALSHI_SERIES="nba:KXNBAGAME+KXNBASPREAD,epl:KXEPLGAME"
_DEFAULT_SERIES = {
    # Spread/total series added 2026-08-04: the strategy blocks MONEYLINE on
    # these leagues (category_blocks), so a *GAME-only map made the Kalshi
    # edge surface zero by construction. Tickers follow the venue's
    # {series}SPREAD / {series}TOTAL pattern; a wrong one fails loudly as a
    # named census entry ({series}_http: 404), never silently, and the
    # untagged_spread / untagged_total identity gates refuse any outcome
    # that lost its line.
    "nba": "KXNBAGAME+KXNBASPREAD+KXNBATOTAL",
    "wnba": "KXWNBAGAME+KXWNBASPREAD+KXWNBATOTAL",
    "epl": "KXEPLGAME+KXEPLSPREAD+KXEPLTOTAL",
    "nhl": "KXNHLGAME+KXNHLSPREAD+KXNHLTOTAL",
    "nfl": "KXNFLGAME+KXNFLSPREAD+KXNFLTOTAL",
    # MLB added 2026-08-04: in August it is the only deep sport in season
    # (NBA/NHL are dark, NFL is preseason, EPL starts mid-month). Without
    # it the Kalshi surface is WNBA-only and cross-venue arbitrage has
    # almost nothing to scan. MLB stays league-blocked for the EDGE
    # strategy (measured flat) — but arbitrage is model-free arithmetic
    # and the blocklist does not apply to it.
    "mlb": "KXMLBGAME+KXMLBSPREAD+KXMLBTOTAL",
    # Tennis (census 2026-08-04): KXWTACHALLENGERMATCH verified live with
    # full player names as outcomes. The MATCH-winner series for the main
    # tours are listed tentatively — a wrong ticker fails as a named
    # census entry (…_http: 404), never silently, so tentative is safe.
    # Set-winner series are deliberately EXCLUDED: a set is a different
    # proposition than the match, and mapping one to the other is the
    # wrong-bet class the identity gates exist to prevent.
    "atp": "KXATPMATCH+KXATPCHALLENGERMATCH",
    "wta": "KXWTAMATCH+KXWTACHALLENGERMATCH",
}


def _series_map() -> dict[str, list[str]]:
    raw = os.environ.get("EDGE_KALSHI_SERIES", "")
    base = {k: v.split("+") for k, v in _DEFAULT_SERIES.items()}
    if not raw:
        return base
    out: dict[str, list[str]] = {}
    for pair in raw.split(","):
        if ":" in pair:
            code, series = pair.split(":", 1)
            out[code.strip()] = [s.strip() for s in series.split("+") if s.strip()]
    return out or base


class KalshiAdapter(VenueAdapter):
    name = "kalshi"

    def __init__(self) -> None:
        self._sess = requests.Session()
        self.book_errors: dict[str, int] = {}
        # Quiet books (404 / empty side): market states, never watchdog
        # inputs. Surfaced in telemetry so thinness stays measurable.
        self.book_quiet: dict[str, int] = {}
        self.last_census: dict = {}
        # Markets whose maker quote expired unfilled: cross for a cool-off
        # (mirrors PMUS mark_force_taker; see reap_kalshi_makers).
        self._force_taker: dict[str, float] = {}   # ticker -> cross-until ts

    def _book_err(self, cause: str) -> None:
        self.book_errors[cause] = self.book_errors.get(cause, 0) + 1

    def discover_markets(self, league_codes: set[str]) -> list[VenueMarket]:
        out: list[VenueMarket] = []
        self.last_census = {}
        for code, series_list in _series_map().items():
            if code not in league_codes:
                continue
            for series in series_list:
                self._discover_series(out, code, series)
        return out

    def _census(self, key: str) -> None:
        self.last_census[key] = self.last_census.get(key, 0) + 1

    def _discover_series(self, out: list[VenueMarket], code: str, series: str) -> None:
        from edge.fairvalue.lines import (
            canonical_outcome,
            parse_outcome_line,
            tag_segment,
            title_segment,
        )

        cursor = ""
        for _ in range(10):  # bounded paging
            try:
                resp = self._sess.get(
                    f"{BASE}/events",
                    params={"series_ticker": series, "status": "open",
                            "with_nested_markets": "true", "limit": 100,
                            **({"cursor": cursor} if cursor else {})},
                    timeout=15,
                )
                if resp.status_code != 200:
                    log.info("kalshi discovery %s -> HTTP %s (series unavailable?)",
                             series, resp.status_code)
                    # Named in the census so a wrong series ticker is a
                    # probe-readable fact, not a silent zero.
                    self.last_census[f"{series}_http"] = resp.status_code
                    break
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                log.warning("kalshi discovery failed for %s: %s", series, exc)
                break
            sser = series.upper()
            for ev in data.get("events") or []:
                outcomes = {}
                for m in ev.get("markets") or []:
                    raw_name = m.get("yes_sub_title") or m.get("subtitle") or m.get("ticker", "")
                    if raw_name and m.get("ticker"):
                        # Canonical form carries the line: "Over 45.5",
                        # "Eagles -7.5", or a plain team for moneyline.
                        title = m.get("title") or ev.get("title", "")
                        key = canonical_outcome(title, raw_name)
                        parsed = parse_outcome_line(key)
                        # Identity gate (audit 2026-08-04). A total whose
                        # subtitle dropped the number matches ANY sharp rung
                        # downstream — pair matching lets point-less sides
                        # through and the lowest alternate rung wins, so the
                        # "edge" is the gap between rungs, not a mispricing.
                        if parsed.kind == "total" and parsed.point is None:
                            self._census("bare_total")
                            continue
                        # A spread/total-series outcome that parses as a
                        # plain team name lost its line somewhere — priced
                        # against the MONEYLINE fair value it is a different
                        # bet wearing the team's name. Never guess.
                        if "SPREAD" in sser and parsed.kind != "spread":
                            self._census("untagged_spread")
                            continue
                        if ("TOTAL" in sser or "POINTS" in sser) and \
                                parsed.kind != "total":
                            self._census("untagged_total")
                            continue
                        # Segment tag: a first-half line must never collide
                        # with — or be priced against — the full-game line.
                        key = tag_segment(key, title_segment(title)
                                          or title_segment(ev.get("title") or ""))
                        outcomes[key] = m["ticker"]
                # Census samples: the ACTUAL outcome-name strings this
                # series produces, so a mapper mismatch is fixed against
                # real forms instead of guesses (2026-08-04: 41 MLB events
                # discovered, ~3/35 matched — the strings are the suspect).
                if outcomes and len(self.last_census.get(
                        f"{series}_samples", [])) < 3:
                    self.last_census.setdefault(f"{series}_samples", [])
                    self.last_census[f"{series}_samples"].append(
                        {"title": (ev.get("title") or "")[:48],
                         "outcomes": [k[:36] for k in list(outcomes)[:2]]})
                if len(outcomes) >= 2:
                    out.append(VenueMarket(
                        market_id=ev.get("event_ticker", ""),
                        title=ev.get("title", ""),
                        league_code=code,
                        outcome_tokens=outcomes,
                    ))
            self.last_census[f"{series}_events"] = (
                self.last_census.get(f"{series}_events", 0)
                + len(data.get("events") or []))
            cursor = data.get("cursor") or ""
            if not cursor:
                break

    def get_book(self, market_id: str, market_ticker: str) -> MarketBook | None:
        try:
            resp = self._sess.get(f"{BASE}/markets/{market_ticker}/orderbook", timeout=10)
        except requests.RequestException as exc:
            self._book_err(f"exc_{type(exc).__name__}")
            return None
        if resp.status_code == 404:
            # A market with no orderbook yet is a MARKET STATE, not an
            # input-health failure. Counting these as venue errors tripped
            # the watchdog the moment coverage widened (2026-08-04: 63
            # "errors"/cycle of quiet tennis/MLB books froze ALL orders,
            # both venues). Quiet books are tallied separately.
            self.book_quiet["http_404"] = self.book_quiet.get("http_404", 0) + 1
            return None
        if resp.status_code == 429:
            # Rate limit: a backoff signal, never a venue error — a burst
            # of 429s from background pollers once fed the watchdog and
            # froze ALL orders. Pause this session's book reads briefly.
            self.book_quiet["http_429"] = self.book_quiet.get("http_429", 0) + 1
            time.sleep(1.0)
            return None
        if resp.status_code != 200:
            self._book_err(f"http_{resp.status_code}")
            log.info("kalshi book %s for %s: %s", resp.status_code, market_ticker,
                     resp.text[:120])
            return None
        try:
            data = resp.json() or {}
        except ValueError:
            self._book_err("bad_json")
            return None
        # Kalshi migrated the orderbook payload to "orderbook_fp":
        # dollar-string prices and decimal contract quantities
        # ({"no_dollars": [["0.0100","28945.00"], ...]}). Our parser read
        # the legacy "orderbook" key, found nothing, and reported EVERY
        # book empty — 448 "empty" reads during live WTA sessions while
        # the raw payloads showed five-figure walls (probe ground truth
        # 2026-08-04 18:44Z). Both formats are accepted; legacy stays for
        # compatibility if the venue serves it anywhere.
        fp = data.get("orderbook_fp") or {}
        ob = data.get("orderbook") or {}

        def _fp_levels(key: str) -> list[tuple[float, float]]:
            out = []
            for pq in fp.get(key) or []:
                try:
                    out.append((float(pq[0]), float(pq[1])))
                except (TypeError, ValueError, IndexError):
                    continue
            return out

        if fp:
            no_lv = _fp_levels("no_dollars")
            yes_lv = _fp_levels("yes_dollars")
        else:
            no_lv = [(p / 100.0, float(q)) for p, q in (ob.get("no") or [])]
            yes_lv = [(p / 100.0, float(q)) for p, q in (ob.get("yes") or [])]
        if not no_lv:
            # An empty NO side is a thin market, not a broken venue. The
            # runner already rejects bookless outcomes by name (no_book);
            # the watchdog must not starve on it.
            self.book_quiet["empty_no_side"] = \
                self.book_quiet.get("empty_no_side", 0) + 1
        yes_bids = sorted(
            (BookLevel(p, q) for p, q in yes_lv),
            key=lambda level: -level.price,
        )
        # Executable YES asks come from resting NO bids at (1 - price).
        yes_asks = sorted(
            (BookLevel(round(1.0 - p, 4), q) for p, q in no_lv),
            key=lambda level: level.price,
        )
        return MarketBook(venue=self.name, market_id=market_id, outcome_id=market_ticker,
                          bids=yes_bids, asks=yes_asks, ts=time.time())

    def taker_fee(self, price: float) -> float:
        return 0.07 * price * (1.0 - price)

    # ── live orders (maker-first; LIVE_* modes only) ───────────────────
    # Auth: Kalshi API key id + RSA private key; signature = RSA-PSS-SHA256
    # over "{timestamp_ms}{METHOD}{path}". Credentials come only from env
    # (EDGE_KALSHI_KEY_ID, EDGE_KALSHI_PRIVATE_KEY or _PATH) and are never
    # logged. Absent credentials => RuntimeError, so LIVE_* cannot start.

    def has_credentials(self) -> bool:
        return bool(os.environ.get("EDGE_KALSHI_KEY_ID")
                    and (os.environ.get("EDGE_KALSHI_PRIVATE_KEY")
                         or os.environ.get("EDGE_KALSHI_PRIVATE_KEY_PATH")))

    @staticmethod
    def _normalize_pem(pem: str) -> str:
        """Repair the newlines an env-var paste destroys.

        Observed live 2026-08-04: the key pasted into Render's env editor
        arrived as one line and load_pem_private_key refused it. A PEM's
        base64 body is newline-wrapped by spec, but every common paste
        format is mechanically recoverable, so recover instead of asking
        the operator to guess quoting rules: literal backslash-n escapes
        become newlines, and a single-line key (newlines collapsed to
        spaces or nothing) is re-wrapped at 64 columns between its
        BEGIN/END armor. A properly formatted key passes through as-is.
        """
        import re

        pem = pem.strip().strip('"').strip("'")
        if "\\n" in pem and "\n" not in pem:
            pem = pem.replace("\\n", "\n")
        if "\n" not in pem:
            m = re.match(
                r"^(-----BEGIN [A-Z0-9 ]+-----)(.*?)(-----END [A-Z0-9 ]+-----)$",
                pem)
            if m:
                head, body, tail = m.groups()
                body = re.sub(r"\s+", "", body)
                wrapped = "\n".join(body[i:i + 64]
                                    for i in range(0, len(body), 64))
                pem = f"{head}\n{wrapped}\n{tail}\n"
        return pem

    def _private_key(self):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        pem = os.environ.get("EDGE_KALSHI_PRIVATE_KEY", "")
        if not pem:
            path = os.environ.get("EDGE_KALSHI_PRIVATE_KEY_PATH", "")
            if not path:
                raise RuntimeError("Kalshi live credentials absent")
            with open(path, "rb") as f:
                pem = f.read().decode()
        return load_pem_private_key(self._normalize_pem(pem).encode(),
                                    password=None)

    def _auth_headers(self, method: str, path: str) -> dict:
        import base64

        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.hashes import SHA256

        key_id = os.environ.get("EDGE_KALSHI_KEY_ID", "")
        if not key_id:
            raise RuntimeError("Kalshi live credentials absent")
        ts_ms = str(int(time.time() * 1000))
        msg = f"{ts_ms}{method.upper()}{path}".encode()
        sig = self._private_key().sign(
            msg,
            padding.PSS(mgf=padding.MGF1(SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            SHA256(),
        )
        return {"KALSHI-ACCESS-KEY": key_id,
                "KALSHI-ACCESS-TIMESTAMP": ts_ms,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}

    def check_auth(self) -> dict:
        """check-live probe: balance call proves key validity + funding."""
        path = "/trade-api/v2/portfolio/balance"
        resp = self._sess.get(f"{BASE}/portfolio/balance",
                              headers=self._auth_headers("GET", path), timeout=10)
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:150]}"}
        bal = (resp.json() or {}).get("balance")
        return {"ok": True, "balance_usd": (bal or 0) / 100.0}

    def plan_entry(self, book) -> tuple[float, bool]:
        """(entry_price, taker) — maker-first, matching what execution does.

        Without this override the engine priced every Kalshi entry as a
        fee-paying taker at the ask, then EXECUTED maker (fee-free) one
        tick under it: the threshold carried a 0.7-1.75c fee that was
        never paid, suppressing most qualifying Kalshi volume and biasing
        the cross-venue router toward Polymarket at genuinely worse
        prices (audit 2026-08-04). Rest one tick under the ask (never
        below the bid); a one-tick market crosses and pays the real fee —
        which the runner then correctly charges, because taker=True."""
        if not book.asks:
            return 0.0, True
        ask = round(book.asks[0].price, 2)
        if self._crossing(book.outcome_id):
            return ask, True
        bid = round(book.bids[0].price, 2) if book.bids else 0.0
        px = round(max(ask - 0.01, bid), 2)
        if px <= 0 or px >= ask:
            return ask, True
        return px, False

    def mark_force_taker(self, market_ticker: str) -> None:
        """Cross on this market for a while — its queue didn't come to us.

        A reaped unfilled maker (reap_kalshi_makers) proves the queue never
        reached us; without this the next look requotes maker into the same
        dead queue forever and the taker fallback stays dead code."""
        self._force_taker[market_ticker] = time.time() + float(
            os.environ.get("EDGE_KALSHI_FORCE_TAKER_S", "600"))

    def _crossing(self, market_ticker: str) -> bool:
        # getattr: tests build adapters via __new__ without __init__.
        until = getattr(self, "_force_taker", {}).get(market_ticker)
        if until is None:
            return False
        if time.time() >= until:      # cool-off elapsed: try resting again
            self._force_taker.pop(market_ticker, None)
            return False
        return True

    def plan_maker_order(self, limit_price: float, best_ask: float,
                         edge: float, threshold: float,
                         market_ticker: str | None = None,
                         edge_is_fee_net: bool = False) -> tuple[float, bool] | None:
        """Maker-first pricing (pure; unit-tested):
        Post at our limit BELOW the ask -> rests as maker, fee-free.
        Cross the ask ONLY if edge net of the taker fee still clears the
        threshold — or if the market is marked force-taker, because a maker
        quote already expired unfilled there. Returns (price, is_taker) or
        None for no order.

        edge_is_fee_net: the caller already judged the threshold at a taker
        entry, so the taker fee is inside `edge` (plan_entry returned
        taker=True and strategy_filter subtracted taker_fee). Deducting it
        again here charged every forced cross the fee TWICE — threshold plus
        2x fee (audit 2026-08-05)."""
        tick = 0.01
        if market_ticker is None or not self._crossing(market_ticker):
            maker_px = round(min(limit_price, best_ask - tick), 2)
            if maker_px >= 0.01 and maker_px < best_ask:
                return maker_px, False
        net = edge if edge_is_fee_net else edge - self.taker_fee(best_ask)
        if net >= threshold:
            return round(best_ask, 2), True
        return None

    def place_order(self, market_ticker: str, price: float, count: int,
                    client_order_id: str, taker: bool,
                    sell: bool = False, rest_s: int = 900) -> dict:
        """POST a YES limit via the V2 events-orders endpoint. The legacy
        /portfolio/orders path now answers HTTP 410 deprecated_v1_order_endpoint
        (observed live 2026-08-04); V2 is a single YES-denominated book where a
        buy is side "bid" and a sale of held YES contracts is side "ask" —
        prices/counts travel as fixed-point strings, the same dialect the
        orderbook_fp payload speaks. Crossing orders are immediate_or_cancel;
        resting maker orders carry a 15-minute expiration_time so stale quotes
        never linger. Sells are only ever sized to contracts we hold (the
        underdog exit) — never a short."""
        path = "/trade-api/v2/portfolio/events/orders"
        body = {
            "ticker": market_ticker, "client_order_id": client_order_id,
            "side": "ask" if sell else "bid", "count": f"{int(count)}.00",
            "price": f"{price:.4f}",
            "self_trade_prevention_type": "taker_at_cross",
        }
        if taker:
            body["time_in_force"] = "immediate_or_cancel"
        else:
            body["time_in_force"] = "good_till_canceled"
            body["expiration_time"] = int(time.time()) + int(rest_s)
        try:
            resp = self._sess.post(
                f"{BASE}/portfolio/events/orders", json=body,
                headers=self._auth_headers("POST", path), timeout=10)
        except requests.RequestException as exc:
            # An ambiguous network failure must read as NOT filled — the
            # order may rest at the venue, and sync_kalshi_fills will
            # reconcile any real fill by trade id.
            return {"ok": False, "order_id": None, "status": "network_error",
                    "price": price, "count": count, "taker": taker,
                    "sell": sell,
                    "raw": {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}}
        ok = resp.status_code in (200, 201)
        try:
            data = resp.json() or {}
        except ValueError:
            data = {}
        order = data.get("order") or data
        # FAIL CLOSED on fills: the spec requires fill_count in every
        # response, so a payload without one is an unknown shape — assuming
        # "fully filled" there once meant buying the closing leg of an arb
        # against contracts we never owned. remaining_count is the backup
        # signal (count - remaining is what actually matched on an IOC).
        filled = 0
        for key in ("filled_count", "fill_count", "matched_count"):
            if order.get(key) is not None:
                try:
                    filled = int(float(order[key]))
                except (TypeError, ValueError):
                    filled = 0
                break
        else:
            if order.get("remaining_count") is not None:
                try:
                    filled = max(0, count - int(float(order["remaining_count"])))
                except (TypeError, ValueError):
                    filled = 0
            elif ok:
                log.warning("kalshi order %s: response carries no fill field"
                            " — treating as 0 filled: %s",
                            order.get("order_id"), str(data)[:200])
        return {"ok": ok, "order_id": order.get("order_id") or order.get("id"),
                "status": order.get("status", f"http_{resp.status_code}"),
                "price": price, "count": filled if ok else count, "taker": taker,
                "sell": sell,
                "raw": data if ok else {"error": resp.text[:300]}}

    async def subscribe_books(self, market_ids: list[str]):
        raise NotImplementedError("v1 uses REST polling")

    async def place(self, intent: FillIntent):
        raise RuntimeError("use place_order via the mode-gated executor path")

    async def settlements(self):
        raise NotImplementedError("grader pulls settlements in batch; see shadow/grader.py")

    # Batch settlement lookup used by the settle sweep and the grader.
    def fetch_results(self, tickers: list[str]) -> dict[str, float]:
        """market ticker -> payout (1.0 yes / 0.0 no) for resolved markets.

        Instrumented like the PMUS adapter (audit 2026-08-05): the probe
        read settle_stats {"kalshi": null} for DAYS because this method
        kept no counters — whether it even ran, what the venue answered,
        and why nothing settled were all unanswerable from telemetry.
        Two staleness causes fixed here:
        - only status == "settled" priced. Kalshi holds a finished market
          at determined/finalized (result already known) before financial
          settlement completes, so realized P&L lagged the game by however
          long the venue took to pay out. The result field is authoritative
          the moment it is yes/no; price on it. Voids stay unsettled — a
          void pays cost back, not $1/$0, and guessing either is wrong.
        - non-200 answers were silently skipped: a rate-limited sweep read
          exactly like "no results". Now every page is counted, errors keep
          their first cause, and pages are paced 0.3s apart so deep books
          cannot trip the limiter in the first place.
        last_market_status (ticker -> venue status) is kept for every market
        checked, so the kalshi_open card can flag finished-but-unresolved
        rows instead of presenting a dead game as LIVE.
        """
        out: dict[str, float] = {}
        stats = {"checked": len(tickers), "priced": 0, "no_price": 0,
                 "errors": 0, "pages": 0, "by_status": {}}
        first_err = None
        statuses: dict[str, str] = {}
        for i in range(0, len(tickers), 100):
            chunk = tickers[i : i + 100]
            if i:
                # Gentle on the venue: full-speed paging is how the PMUS
                # sweep tripped Cloudflare and lost the rest of the pass.
                time.sleep(0.3)
            try:
                resp = self._sess.get(f"{BASE}/markets",
                                      params={"tickers": ",".join(chunk)}, timeout=15)
                if resp.status_code != 200:
                    stats["errors"] += 1
                    if first_err is None:
                        first_err = f"HTTP {resp.status_code}: {resp.text[:120]}"
                    continue
                stats["pages"] += 1
                for m in resp.json().get("markets") or []:
                    status = str(m.get("status") or "?")
                    stats["by_status"][status] = \
                        stats["by_status"].get(status, 0) + 1
                    if m.get("ticker"):
                        statuses[m["ticker"]] = status
                    if status in ("determined", "finalized", "settled") and \
                            m.get("result") in ("yes", "no"):
                        out[m["ticker"]] = 1.0 if m["result"] == "yes" else 0.0
            except (requests.RequestException, ValueError) as exc:
                stats["errors"] += 1
                if first_err is None:
                    first_err = f"{type(exc).__name__}: {str(exc)[:120]}"
                log.warning("kalshi settlement fetch failed: %s", exc)
        stats["priced"] = len(out)
        stats["no_price"] = stats["checked"] - len(out)
        stats["first_error"] = first_err
        self.last_settle_stats = stats
        self.last_market_status = statuses
        log.info("kalshi fetch_results: %s", stats)
        return out
