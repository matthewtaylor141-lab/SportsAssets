"""Polymarket US adapter (build step 5a) — the regulated US exchange.

This is a DIFFERENT venue from the global CLOB the reference account trades
on: per-outcome markets (one market per team, grouped by event), own slugs,
own liquidity. Calibration from the global book transfers as a prior only;
the PAPER grader measures this venue's books directly.

Market data: public gateway via the official polymarket-us SDK (no auth).
Orders: Ed25519 API keys (EDGE_PMUS_KEY_ID / EDGE_PMUS_SECRET_KEY, minted at
polymarket.us/developer) — FOK limit, preview-verified, LIVE_* modes only.
Credentials are never logged; absent credentials => RuntimeError.

Fees: taker commission is configurable (EDGE_PMUS_TAKER_FEE, probability
units), default 0.0 per the current fee schedule. Every order response's
commission fields are stored raw in the ledger decision record, and the
nightly report recomputes net edge from actual charged commissions — the
hard rule is "log fee assumptions per venue per fill", encoded here.
"""

from __future__ import annotations

import logging
import os
import time

from .base import BookLevel, FillIntent, MarketBook, VenueAdapter
from .mapper import VenueMarket

log = logging.getLogger(__name__)


TICK = 0.01   # venue prices are whole cents


class PolymarketUSAdapter(VenueAdapter):
    name = "polymarket-us"

    def __init__(self) -> None:
        from polymarket_us import PolymarketUS

        self._pub = PolymarketUS()  # public gateway (market data)
        self._auth = None
        self.book_errors: dict[str, int] = {}
        self._taker_fee = float(os.environ.get("EDGE_PMUS_TAKER_FEE", "0.0"))
        self._maker_fee_rate = float(os.environ.get("EDGE_PMUS_MAKER_FEE", "0.0"))
        # Maker-first is OPT-IN. It was shipped default-on without ever being
        # exercised against the live venue, which made an unverified order
        # type the only path real money could take: if the venue refuses GTC
        # post-only, every order fails and the engine goes quiet while its
        # telemetry still reports orders "placed". Prove it with
        # EDGE_PMUS_MAKER_FIRST=1 and watch the maker fill count first.
        self._maker_first = os.environ.get("EDGE_PMUS_MAKER_FIRST", "0") == "1"
        self._force_taker: dict[str, float] = {}   # slug -> cross-until ts
        # Live book stream (push-latency books). Needs API keys for the WS
        # handshake; fail-soft — REST remains the fallback path.
        self._stream = None
        if self.has_credentials() and os.environ.get("EDGE_PMUS_WS", "1") != "0":
            try:
                from .pmus_stream import BookStreamer

                self._stream = BookStreamer(os.environ["EDGE_PMUS_KEY_ID"],
                                            os.environ["EDGE_PMUS_SECRET_KEY"])
            except Exception as exc:  # noqa: BLE001
                log.warning("book stream unavailable, using REST: %s", exc)

    def stream_stats(self) -> dict | None:
        return self._stream.stats() if self._stream else None

    def add_book_listener(self, fn) -> bool:
        """Subscribe to book-change notifications (slug per update). Returns
        False when there is no live stream — the caller then keeps polling."""
        if self._stream is None:
            return False
        self._stream.add_listener(fn)
        return True

    # ── credentials / auth ─────────────────────────────────────────────

    @staticmethod
    def has_credentials() -> bool:
        return bool(os.environ.get("EDGE_PMUS_KEY_ID")
                    and os.environ.get("EDGE_PMUS_SECRET_KEY"))

    def _client(self):
        if self._auth is None:
            if not self.has_credentials():
                raise RuntimeError("Polymarket US live credentials absent")
            from polymarket_us import PolymarketUS

            self._auth = PolymarketUS(
                key_id=os.environ["EDGE_PMUS_KEY_ID"],
                secret_key=os.environ["EDGE_PMUS_SECRET_KEY"],
            )
        return self._auth

    def check_auth(self) -> dict:
        """check-live probe: balances call proves key validity + funding."""
        try:
            bal = self._client().account.balances()
            out = {"ok": True, "balances": bal}
            bp = self._buying_power_from(bal)
            if bp is not None:
                out["balance_usd"] = bp
            return out
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:150]}"}

    @staticmethod
    def _buying_power_from(payload: dict) -> float | None:
        """Cash actually available to deploy — buyingPower when the venue
        reports it, else the current balance. Unsettled funds and money
        already committed to resting orders are excluded by the venue."""
        for b in (payload or {}).get("balances") or []:
            for field in ("buyingPower", "currentBalance"):
                if b.get(field) is not None:
                    try:
                        return float(b[field])
                    except (TypeError, ValueError):
                        continue
        return None

    def buying_power(self) -> float | None:
        """Live deployable cash, or None if the venue can't be reached. The
        day budget is sized from this, so 'how much can we trade' answers
        itself as the account grows or shrinks — no config edit."""
        try:
            return self._buying_power_from(self._client().account.balances())
        except Exception as exc:  # noqa: BLE001
            log.info("buying power unavailable (keeping last known): %s", exc)
            return None

    # ── discovery / books ──────────────────────────────────────────────

    # Param variants tried in order until one yields events — the gateway's
    # exact filter semantics are verified empirically via the census.
    # Primary variant adds a 72h start-time window: census showed the
    # unwindowed sports listing leads with season-long futures ("World
    # Series Champion"), starving the 1,000-event page budget of games.
    @staticmethod
    def _list_variants() -> tuple:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        window = {"startTimeMin": iso(now - timedelta(hours=6)),
                  "startTimeMax": iso(now + timedelta(hours=72))}
        return (
            {"active": True, "closed": False, "categories": ["sports"], **window},
            {"active": True, "closed": False, "categories": ["sports"]},
            {"active": True, "closed": False},
            {"active": True},
            {},
        )

    def discover_markets(self, league_codes: set[str]) -> list[VenueMarket]:
        """Active events with nested per-outcome markets. League assignment
        happens at match time (mapper league filter passes None here).
        Every skip reason is counted in last_census — the funnel shows WHY
        discovery found nothing instead of just '0 candidates'."""
        from edge.fairvalue.lines import apply_slug_line, bet_identity, canonical_outcome

        census: dict[str, Any] = {"events_seen": 0, "skipped_no_title": 0,
                                  "skipped_lt2_outcomes": 0, "markets_seen": 0,
                                  "markets_closed": 0, "markets_no_outcome": 0,
                                  "outcome_from_title": 0, "samples": [],
                                  "market_samples": []}
        out: list[VenueMarket] = []
        try:
            variant_used = None
            for variant in self._list_variants():
                probe = self._pub.events.list({"limit": 100, **variant}) or {}
                if probe.get("events"):
                    variant_used = variant
                    break
            census["params"] = variant_used if variant_used is not None else "all_empty"
            if variant_used is None:
                self.last_census = census
                return out

            offset = 0
            for _ in range(10):  # bounded paging
                resp = self._pub.events.list(
                    {"limit": 100, "offset": offset, **variant_used}) or {}
                events = resp.get("events") or []
                census["events_seen"] += len(events)
                for ev in events:
                    if len(census["samples"]) < 3 and ev.get("title"):
                        census["samples"].append(ev["title"][:60])
                    outcomes = {}
                    for m in ev.get("markets") or []:
                        census["markets_seen"] += 1
                        if len(census["market_samples"]) < 3 and m.get("title"):
                            census["market_samples"].append(m["title"][:50])
                        oc = m.get("outcome") or (m.get("team") or {}).get("name")
                        if m.get("closed"):
                            census["markets_closed"] += 1
                            continue
                        if not oc and m.get("title"):
                            # Census finding (2026-07-24): event listings carry
                            # no `outcome` field — the market TITLE names the
                            # side ("Mets", "Spread: Eagles (-7.5)"). Use it;
                            # the mapper's 0.95 team gate and the line parser
                            # discard anything that isn't actually a side.
                            oc = m["title"].strip()
                            census["outcome_from_title"] += 1
                        if not (oc and m.get("slug")):
                            census["markets_no_outcome"] += 1
                            continue
                        # Canonical key carries the line ("Over 8.5",
                        # "Eagles -7.5") so ML/spread/total outcomes of one
                        # event never collide. The venue often exposes the
                        # handicap ONLY in the slug (…-neg-2pt5), so apply
                        # that too — otherwise a spread market gets priced
                        # against the moneyline fair value.
                        ident = bet_identity(m["slug"], m.get("title") or "", oc)
                        if not ident.tradeable:
                            census["identity_conflict"] = census.get(
                                "identity_conflict", 0) + 1
                            if ident.conflict and "conflict_example" not in census:
                                census["conflict_example"] = {
                                    "slug": m["slug"][:60],
                                    "why": ident.conflict[:80]}
                            continue  # never guess which bet this is
                        key = apply_slug_line(
                            canonical_outcome(m.get("title") or "", oc), m["slug"])
                        outcomes[key] = m["slug"]
                    if not ev.get("title"):
                        census["skipped_no_title"] += 1
                    elif len(outcomes) < 2:
                        census["skipped_lt2_outcomes"] += 1
                    else:
                        out.append(VenueMarket(
                            market_id=ev.get("slug") or ev["title"],
                            title=ev["title"],
                            league_code=None,   # resolved by the mapper's fuzzy match
                            outcome_tokens=outcomes,
                        ))
                if len(events) < 100:
                    break
                offset += 100
        except Exception as exc:  # noqa: BLE001
            self._book_err(f"discovery_{type(exc).__name__}")
            census["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
            log.warning("polymarket-us discovery failed: %s", exc)
        self.last_census = census
        # Stream every discovered outcome book — books arrive by push before
        # the pricing loop ever asks for them.
        if self._stream is not None and out:
            self._stream.ensure([slug for m in out
                                 for slug in m.outcome_tokens.values()])
        return out

    def _book_err(self, cause: str) -> None:
        self.book_errors[cause] = self.book_errors.get(cause, 0) + 1

    @staticmethod
    def _parse_levels(body: dict) -> tuple[list[BookLevel], list[BookLevel]]:
        def levels(rows, reverse):
            out = []
            for lvl in rows or []:
                try:
                    px = float((lvl.get("px") or {}).get("value") or 0)
                    qty = float(lvl.get("qty") or 0)
                except (TypeError, ValueError, AttributeError):
                    continue
                if 0 < px < 1 and qty > 0:
                    out.append(BookLevel(px, qty))
            return sorted(out, key=lambda x: -x.price if reverse else x.price)

        return (levels(body.get("bids"), reverse=True),
                levels(body.get("offers") or body.get("asks"), reverse=False))

    def get_book(self, market_id: str, market_slug: str) -> MarketBook | None:
        # Push-latency path: serve from the live stream cache when current.
        if self._stream is not None:
            md = self._stream.get(market_slug)
            if md is not None:
                bids, asks = self._parse_levels(md)
                if asks:
                    return MarketBook(venue=self.name, market_id=market_id,
                                      outcome_id=market_slug, bids=bids,
                                      asks=asks, ts=time.time())
            else:
                self._stream.ensure([market_slug])  # stream it from now on

        try:
            raw = self._pub.markets.book(market_slug) or {}
        except Exception as exc:  # noqa: BLE001
            self._book_err(f"exc_{type(exc).__name__}")
            return None

        # Measured venue shape (funnel book-sample 2026-07-24): the payload
        # nests under "marketData". Accept that, "book", or top-level.
        body = raw
        for key in ("marketData", "book"):
            if isinstance(raw.get(key), dict):
                body = raw[key]
                break
        bids, asks = self._parse_levels(body)

        if not asks:
            # Depth book empty — fall back to the venue's BBO endpoint
            # (thin markets often quote a best ask without visible depth).
            try:
                bbo = self._pub.markets.bbo(market_slug) or {}
                best_ask = float((bbo.get("bestAsk") or {}).get("value") or 0)
                ask_qty = float(bbo.get("askDepth") or 0)
                if 0 < best_ask < 1 and ask_qty > 0:
                    asks = [BookLevel(best_ask, ask_qty)]
                    self._book_err("asks_from_bbo")
                best_bid = float((bbo.get("bestBid") or {}).get("value") or 0)
                bid_qty = float(bbo.get("bidDepth") or 0)
                if not bids and 0 < best_bid < 1 and bid_qty > 0:
                    bids = [BookLevel(best_bid, bid_qty)]
            except Exception:  # noqa: BLE001
                pass
        if not asks:
            self._book_err("no_asks")
            # Keep ONE raw sample per cycle so telemetry shows the actual
            # response shape instead of just a counter.
            if not getattr(self, "last_book_sample", None):
                self.last_book_sample = {"slug": market_slug,
                                         "keys": sorted(raw.keys()),
                                         "snippet": str(raw)[:220]}
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=market_slug, bids=bids, asks=asks,
                          ts=time.time())

    def taker_fee(self, price: float) -> float:
        return self._taker_fee

    def maker_fee(self, price: float) -> float:
        return self._maker_fee_rate

    # ── maker-first entry pricing ──────────────────────────────────────

    def plan_entry(self, book: MarketBook) -> tuple[float, bool]:
        """(entry_price, taker) — where we try to buy.

        Crossing the spread means paying the ask, and an ask only two ticks
        rich of fair kills an otherwise-good trade. Resting one tick inside
        the spread buys at a better price, which BOTH widens the edge on
        trades we were already taking and qualifies trades that could not
        clear the threshold at the ask. The cost is fill probability, which
        the reaper bounds: an order that hasn't filled by its TTL is pulled
        and the decision is made again on a fresh book.

        Never crosses (that would make us the taker at a worse price than we
        asked for) and never prices below the best bid (there is no reason to
        queue behind the whole book when joining the front is free)."""
        if not book.asks:
            return 0.0, True
        ask = round(book.asks[0].price, 2)
        if not self._maker_first or self._crossing(book.outcome_id):
            return ask, True
        bid = round(book.bids[0].price, 2) if book.bids else 0.0
        px = round(max(ask - TICK, bid), 2)
        if px <= 0 or px >= ask:      # one-tick market: no room to rest
            return ask, True
        return px, False

    def mark_force_taker(self, market_slug: str) -> None:
        """Cross on this market for a while — its queue didn't come to us.

        Without this, a market we can never get filled on as maker would be
        quoted, reaped, quoted, reaped, forever, and traded never. Resting is
        supposed to buy a better price on trades we'd take anyway, not to
        replace taking. One failed attempt and we go back to crossing."""
        self._force_taker[market_slug] = time.time() + float(
            os.environ.get("EDGE_PMUS_FORCE_TAKER_S", "600"))

    def _crossing(self, market_slug: str) -> bool:
        until = self._force_taker.get(market_slug)
        if until is None:
            return False
        if time.time() >= until:      # cool-off elapsed: try resting again
            self._force_taker.pop(market_slug, None)
            return False
        return True

    # ── live orders (FOK limit, preview-verified) ──────────────────────

    def place_order(self, market_slug: str, price: float, quantity: int,
                    preview: bool = True, tif: str = "TIME_IN_FORCE_FILL_OR_KILL",
                    post_only: bool = False) -> dict:
        """BUY_LONG limit at whole-cent price. preview=False skips the venue
        cost pre-check round-trip — safe for micro orders because the LIMIT
        price already hard-bounds cost at price*quantity; the executor keeps
        the preview for larger sizes.

        tif=TIME_IN_FORCE_GOOD_TILL_CANCEL with post_only=True is the maker
        path: the venue's participateDontInitiate flag makes it reject rather
        than cross, so a resting order can never turn into a taker fill at a
        price we never approved."""
        client = self._client()
        params = {
            "marketSlug": market_slug,
            "intent": "ORDER_INTENT_BUY_LONG",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": f"{price:.2f}", "currency": "USD"},
            "quantity": int(quantity),
            "tif": tif,
        }
        if post_only:
            params["participateDontInitiate"] = True
        expected = price * quantity
        prev = {}
        if preview:
            prev = (client.orders.preview({"request": params}) or {}).get("order") or {}
            try:
                prev_cost = float((prev.get("cashOrderQty") or {}).get("value") or 0)
            except (TypeError, ValueError):
                prev_cost = 0.0
            if prev_cost and prev_cost > expected * 1.02:
                return {"ok": False, "status": "preview_mismatch", "order_id": None,
                        "price": price, "count": quantity, "taker": True,
                        "raw": {"preview": prev, "expected": expected}}

        resting = tif == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
        resp = client.orders.create(
            {**params, "synchronousExecution": not resting}) or {}
        filled, notional, state = 0.0, 0.0, ""
        for ex in resp.get("executions") or []:
            state = (ex.get("order") or {}).get("state") or state
            try:
                px = float((ex.get("lastPx") or {}).get("value") or 0)
                sh = float(ex.get("lastShares") or 0)
            except (TypeError, ValueError):
                continue
            if ex.get("type") in ("EXECUTION_TYPE_FILL", "EXECUTION_TYPE_PARTIAL_FILL") and px:
                filled += sh
                notional += sh * px
        status = state.replace("ORDER_STATE_", "").lower() or "unknown"
        # A resting order succeeds by EXISTING, not by filling — its fills
        # arrive later and are reconciled from the activity feed.
        accepted = resp.get("id") and state not in ("ORDER_STATE_REJECTED",
                                                    "ORDER_STATE_EXPIRED")
        ok = bool(accepted) if resting else filled > 0
        return {"ok": ok, "order_id": resp.get("id"),
                "status": ("resting" if resting and accepted else status),
                "resting": bool(resting and accepted),
                "price": round(notional / filled, 4) if filled else price,
                "count": filled, "taker": not resting,
                "raw": {"preview": prev, "response": resp}}

    # ── resting-order lifecycle ────────────────────────────────────────

    def cancel_order(self, order_id: str, market_slug: str) -> bool:
        try:
            self._client().orders.cancel(order_id, {"marketSlug": market_slug})
            return True
        except Exception as exc:  # noqa: BLE001 — already gone counts as done
            log.info("cancel %s failed (treating as closed): %s", order_id, exc)
            return False

    def open_orders(self) -> list[dict]:
        try:
            return list((self._client().orders.list() or {}).get("orders") or [])
        except Exception as exc:  # noqa: BLE001
            self._book_err(f"open_orders_{type(exc).__name__}")
            return []

    def recent_trades(self, limit: int = 100) -> list[dict]:
        """Executed trades, newest first — the reconciliation source for
        resting orders (there is no fills endpoint; trades live in the
        activity feed)."""
        try:
            resp = self._client().portfolio.activities({
                "limit": int(limit), "types": ["ACTIVITY_TYPE_TRADE"],
                "sortOrder": "SORT_ORDER_DESCENDING"}) or {}
        except Exception as exc:  # noqa: BLE001
            self._book_err(f"activities_{type(exc).__name__}")
            return []
        return [a["trade"] for a in (resp.get("activities") or []) if a.get("trade")]

    async def subscribe_books(self, market_ids: list[str]):
        raise NotImplementedError("v1 uses REST polling")

    async def place(self, intent: FillIntent):
        raise RuntimeError("use place_order via the mode-gated executor path")

    async def settlements(self):
        raise NotImplementedError("grader pulls settlements in batch")

    def fetch_results(self, slugs: list[str]) -> dict[str, float]:
        """market slug -> payout for settled markets (grader input)."""
        out: dict[str, float] = {}
        for slug in slugs:
            try:
                s = self._pub.markets.settlement(slug) or {}
                px = (s.get("settlementPrice") or {}).get("value")
                if px is not None:
                    out[slug] = float(px)
            except Exception:  # noqa: BLE001 — unsettled/unknown: skip
                continue
        return out
