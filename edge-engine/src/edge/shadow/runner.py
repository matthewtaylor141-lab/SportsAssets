"""Shadow runner: the engine's full decision loop with orders replaced by
logging. This produces the ONLY evidence that gates capital.

Per would-be fill, log: venue, market, outcome, league, band, book snapshot
(top 5 levels), feed odds snapshot, fair value, threshold, intended size,
intended price, and whether the displayed book depth would have filled it.
At resolution, grader.py scores each shadow fill (payout - price) * size —
the exact methodology of the Phase 1 study, so results are apples-to-apples
with the reference account's measured curves.

Gate (config/risk.yaml): >= 60 days, >= 5,000 shadow fills per venue,
>= 1.5% ROI net of modeled fees and slippage. No gate, no capital.
"""
import json
import logging
import os
import queue
import threading
import time
import zlib
from pathlib import Path

log = logging.getLogger(__name__)

def _data_dir() -> Path:
    for c in (os.environ.get("EDGE_DATA_DIR"),
              Path(__file__).resolve().parents[3] / "data",
              Path.cwd() / "data"):
        if c and Path(c).is_dir():
            return Path(c)
    fallback = Path.cwd() / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _shadow_log_path() -> Path:
    """Resolved at call time (not import) so EDGE_DATA_DIR redirection —
    tests, mounted disks — always wins."""
    return _data_dir() / "shadow_fills.jsonl"


# Back-compat name; prefer _shadow_log_path() for current value.
SHADOW_LOG = _shadow_log_path()


def log_shadow_fill(intent, book, feed_snapshot, would_fill: bool, whale_alignment=None,
                    tag: str | None = None):
    rec = {
        "ts": time.time(),
        "tag": tag,  # None = threshold-clearing; 'exploration' = below-threshold study
        "venue": book.venue,
        "whale_alignment": whale_alignment,
        "market_id": intent.market_id,
        "outcome_id": intent.outcome_id,
        "league": intent.league,
        "band": intent.band,
        "limit_price": intent.limit_price,
        "size_usd": intent.size_usd,
        "fair_value": intent.fair_value,
        "edge": intent.edge,
        "book_asks": [(l.price, l.size) for l in book.asks[:5]],
        "book_bids": [(l.price, l.size) for l in book.bids[:5]],
        "feed": feed_snapshot,
        "would_fill": would_fill,
    }
    with open(_shadow_log_path(), "a") as f:
        f.write(json.dumps(rec) + "\n")
    _record_to_platform(rec)


# Identity of THIS process, stamped into every status post. Two different
# builds appeared to alternate on the status row on 2026-08-04 (funnel keys
# present at 19:27, absent at 20:00-21:07 while V2 orders demonstrably
# fired in between) — without a boot stamp, "which process said this" is
# unanswerable from outside.
import uuid as _uuid_mod

_BOOT = {"id": _uuid_mod.uuid4().hex[:10], "started": time.time(),
         "build": "copy-portfolio"}


def _strategy_live() -> bool:
    """Owner directive 2026-08-06: software-generated (engine-strategy)
    entries are OFF — the live book is whale copies plus guaranteed
    arbitrage only. Strategy intents still PAPER-log every cycle (the
    shadow record keeps grading the model for the day it earns its seat
    back); they place no live orders. EDGE_STRATEGY_LIVE=1 re-arms."""
    return os.environ.get("EDGE_STRATEGY_LIVE", "0") == "1"


def _post_status(status: str, detail: dict) -> None:
    """Cycle telemetry -> platform Admin panel (service row 'edge_engine').
    This is how an empty Engine tab becomes diagnosable: the funnel shows
    feed events, venue matches, and rejection reasons per cycle."""
    base = os.environ.get("EDGE_PLATFORM_API", "")
    token = os.environ.get("EDGE_INGEST_TOKEN", "")
    if not base or not token:
        return
    try:
        import requests

        requests.post(f"{base}/api/engine/status",
                      json={"status": status,
                            "detail": {**detail, "boot": dict(_BOOT)}},
                      headers={"X-Engine-Token": token}, timeout=5)
    except Exception as exc:  # noqa: BLE001
        log.debug("status post failed (non-fatal): %s", exc)


def _post_methodology(markdown: str, figures: dict) -> None:
    """Publish the generated document to the platform, where it is readable.

    Same fail-soft contract as the status post: reporting must never be able
    to interfere with trading, so every failure here is logged and swallowed.
    """
    base = os.environ.get("EDGE_PLATFORM_API", "")
    token = os.environ.get("EDGE_INGEST_TOKEN", "")
    if not base or not token:
        return
    try:
        import requests

        r = requests.post(f"{base}/api/engine/methodology",
                          json={"markdown": markdown, "figures": figures,
                                "generated_ts": time.time()},
                          headers={"X-Engine-Token": token}, timeout=15)
        if r.status_code != 200:
            # A rejected publish must be loud: this exact failure hid for
            # hours behind a debug-level log while the probe read "never
            # published".
            log.warning("methodology publish HTTP %s: %s",
                        r.status_code, r.text[:200])
    except Exception as exc:  # noqa: BLE001
        log.warning("methodology post failed (non-fatal): %s", exc)


# ── platform mirror: OFF the pricing loop ───────────────────────────────
#
# This used to be a synchronous POST per record, called from inside the
# per-outcome loop. At 18 sports that was survivable. Once coverage became
# every active sport with full spread/total ladders it became thousands of
# blocking round-trips per cycle — a pricing loop that spends its time
# waiting on an HTTP call is not pricing, and the symptom is exactly what it
# looked like: an engine that reports healthy and trades once a day.
#
# Now: bounded queue, one background sender. If the platform is slow or
# down, records are DROPPED (counted, reported) — the JSONL log is the
# grader's source of truth and telemetry must never be able to stall trading.

_MIRROR_Q: "queue.Queue[dict]" = queue.Queue(maxsize=20_000)
_MIRROR = {"started": False, "sent": 0, "dropped": 0, "failed": 0}


def mirror_stats() -> dict:
    return {**{k: v for k, v in _MIRROR.items() if k != "started"},
            "queued": _MIRROR_Q.qsize()}


def _mirror_loop() -> None:
    import requests

    sess = requests.Session()
    base = os.environ.get("EDGE_PLATFORM_API", "")
    token = os.environ.get("EDGE_INGEST_TOKEN", "")
    while True:
        rec = _MIRROR_Q.get()
        try:
            sess.post(
                f"{base}/api/engine/fills",
                json={
                    "ts": rec["ts"], "venue": rec["venue"], "market_id": rec["market_id"],
                    "outcome_id": rec["outcome_id"], "league": rec.get("league"),
                    "band": rec.get("band"), "limit_price": rec["limit_price"],
                    "size_usd": rec["size_usd"], "fair_value": rec.get("fair_value"),
                    "edge": rec.get("edge"), "would_fill": rec.get("would_fill", True),
                    "whale_alignment": rec.get("whale_alignment"),
                    "book_asks": rec.get("book_asks"), "book_bids": rec.get("book_bids"),
                },
                headers={"X-Engine-Token": token}, timeout=10)
            _MIRROR["sent"] += 1
        except Exception as exc:  # noqa: BLE001
            _MIRROR["failed"] += 1
            log.debug("platform mirror failed (non-fatal): %s", exc)


def mirror_side_channel_fill(*, venue: str, slug: str, price: float,
                             qty: float, league: str | None,
                             category: str) -> None:
    """Attribution mirror for PMUS orders placed OUTSIDE the intent loop
    (arb legs and other side channels). The public track record attributes
    the AI's fills by the slugs in the platform's engine_fills mirror,
    which log_shadow_fill feeds only from the intent path — so every
    side-channel PMUS fill landed in the record's 'unattributed' cohort
    (5→17 creep on 2026-08-04, the night the cross-venue watcher armed).
    Kalshi fills skip this: attribution reconciles the PMUS account."""
    if not (venue or "").startswith("polymarket"):
        return
    _record_to_platform({
        "ts": time.time(), "venue": venue, "market_id": slug,
        "outcome_id": slug, "league": league, "band": category,
        "limit_price": float(price), "size_usd": round(qty * price, 4),
        "would_fill": True})


def _record_to_platform(rec: dict) -> None:
    """Hand the record to the background sender. Never blocks, never raises,
    never waits on the network — this is called from the hot path."""
    if not os.environ.get("EDGE_PLATFORM_API") or not os.environ.get("EDGE_INGEST_TOKEN"):
        return
    if not _MIRROR["started"]:
        _MIRROR["started"] = True
        threading.Thread(target=_mirror_loop, daemon=True,
                         name="platform-mirror").start()
    try:
        _MIRROR_Q.put_nowait(rec)
    except queue.Full:
        _MIRROR["dropped"] += 1


# ── The decision loop (mode-aware; orders only via the executor) ────────


def discover_all(adapters, policy) -> dict:
    """Venue market discovery — heavier REST sweep, run on its own clock
    (EDGE_DISCOVERY_SECONDS), not per pricing cycle."""
    league_codes = set()
    for group in (policy.leagues.get("allowlist") or {}).values():
        league_codes.update(group)
    out = {}
    for adapter in adapters:
        try:
            out[adapter.name] = (adapter, adapter.discover_markets(league_codes))
            log.info("%s: %s candidate markets", adapter.name,
                     len(out[adapter.name][1]))
        except Exception as exc:  # noqa: BLE001
            log.warning("%s discovery failed: %s", adapter.name, exc)
    return out


def volume_verdict(funnel: dict, risk) -> str:
    """One sentence naming the single biggest thing standing between the
    engine and more trades.

    Every low-volume investigation in this project has cost days because the
    funnel reports twenty numbers and none of them says which one matters.
    The checks are ordered by what would have to be true first: a loop that
    cannot finish, then a mode that cannot trade, then a budget that is
    spent, then a universe that is empty, then — only then — the price rules
    doing their job."""
    if funnel.get("truncated"):
        t = funnel["truncated"]
        return (f"CYCLE OVERRUN: only {t['reached']} of {t['of']} events "
                f"priced in {funnel.get('cycle_s')}s — the loop is the limit, "
                f"not the edge")
    if funnel.get("halted"):
        return "HALTED: circuit breaker is live — trading resumes on its timer"
    if funnel.get("watchdog"):
        return f"WATCHDOG: {funnel['watchdog']} — inputs unhealthy, orders held"
    if funnel.get("not_armed"):
        return (f"NOT ARMED: running PAPER, want "
                f"{funnel['not_armed'].get('want')} — live orders are off")
    budget = funnel.get("budget") or {}
    if budget.get("fills_left") == 0:
        return (f"BUDGET SPENT: ${budget.get('spent')} of ${budget.get('day_cap')} "
                f"deployed today — fund the account to raise the ceiling")
    if not funnel.get("feed_events"):
        return "NO FEED EVENTS: the odds feed returned nothing for every sport"
    if not funnel.get("tradeable"):
        return (f"NOTHING MAPPED: {funnel['feed_events']} feed events, "
                f"0 matched to a venue market at the 0.95 gate")
    if not funnel.get("books_checked"):
        return "NO BOOKS: venue markets matched but none quoted an ask"
    if funnel.get("logged"):
        return f"TRADING: {funnel['logged']} order(s) placed this cycle"
    blockers = funnel.get("blockers") or {}
    if blockers:
        top, n = max(blockers.items(), key=lambda kv: kv[1])
        gaps = funnel.get("threshold_gap") or {}
        near = gaps.get("<0.5c", 0) + gaps.get("0.5-1c", 0)
        detail = f", {near} within 1c of clearing" if near else ""
        return (f"NO QUALIFYING EDGE: {funnel['books_checked']} books priced, "
                f"top blocker '{top}' x{n}{detail}")
    return (f"NO QUALIFYING EDGE: {funnel['books_checked']} books priced, "
            f"nothing cleared its threshold")


_ROTATION = {"i": 0}
# event_key -> last time a book in this event priced within 1c of its bar.
# Entries expire after 30 min: proximity decays with the odds.
_NEAR_THRESHOLD: dict[str, float] = {}
_LAST_SETTLE: dict = {}
_ACCOUNT_LINK: dict = {}
# Max age for the per-event payload (segments + props) behind a live order.
_PROP_MAX_AGE_S = float(os.environ.get("EDGE_PROP_MAX_AGE_S", "600"))

# The 24/7 cross-venue arbitrage watcher (see xv_watch.py). Installed by
# main() when armed; run_cycle publishes proven identity pairs into it.
_XV_WATCH = None
# The crypto-binary cross-venue scanner (see xv_crypto.py): Kalshi 15-min
# BTC over/unders vs Polymarket US short-interval binaries. Owns its whole
# pipeline (discovery -> identity join -> pricing); measurement-first.
_XV_CRYPTO = None
_KADD_STATS: dict = {}
_KCOPY_STATS: dict = {}
# {game_key: [whale outcome names]} — refreshed on the discovery clock.
_WHALE_MAP: dict = {}

# Boot beacon: where the process is and how big it is, posted every ~45s
# until the FIRST full cycle completes. Exists because of 2026-08-04: the
# service restart-looped with nothing but clean "startup" posts to show for
# it — no error status (the main loop catches Exceptions), so the death was
# uncatchable (OOM-kill class) and the only way to see WHERE it dies and
# at WHAT memory is to phone home continuously until proven healthy.
_BEACON = {"phase": "boot", "stop": False}


def _rss_mb() -> float | None:
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS"):
                return round(int(line.split()[1]) / 1024, 1)
    except Exception:  # noqa: BLE001 — telemetry never raises
        pass
    return None


def _boot_beacon() -> None:
    t0 = time.time()
    while not _BEACON["stop"]:
        time.sleep(45)
        if _BEACON["stop"]:
            break
        _post_status("beacon", {"up_s": int(time.time() - t0),
                                "rss_mb": _rss_mb(),
                                "phase": _BEACON["phase"]})

# Position reconciliation runs once per process: a restart is exactly when
# the ledger may have been wiped, and exactly when the caps need rebuilding.
_RECONCILED: dict = {}

# Entry prices below this are longshot claims and carry doubled evidence
# requirements (two sharp anchors, not one). The productive low bands are
# where both the return and the speed of proof concentrate — which is also
# exactly where a de-vig error is proportionally largest, so the leash
# tightens as the price falls.
LOW_PRICE_CUTOFF = 0.20


def _phase(market_key: str) -> int:
    """Stable per-market offset in [0, 3600) — spreads periodic work evenly
    over the hour instead of bunching it on the clock boundary."""
    return zlib.crc32(market_key.encode()) % 3600


_QUARANTINE_CACHE: dict = {"ts": 0.0, "value": set()}
_QUARANTINE_TTL_S = 60.0


def _quarantined(ledger) -> set:
    """Quarantine list, memoized for a minute. It changes on the scale of
    hours; a reactor firing several times a second must not re-query it."""
    now = time.time()
    if now - _QUARANTINE_CACHE["ts"] > _QUARANTINE_TTL_S:
        _QUARANTINE_CACHE["value"] = ledger.quarantined_slices(days=1)
        _QUARANTINE_CACHE["ts"] = now
    return _QUARANTINE_CACHE["value"]



def _try_arbitrage(*, ledger, ev, venue_legs, expected, sets, dry_run,
                   funnel, max_usd: float = 10.0) -> bool:
    """Look for a complete outcome set priced under $1 and buy all of it.

    Returns True if a book was attempted (so the caller can count it against
    the per-cycle budget).

    THE BUG THIS VERSION FIXES (observed live 2026-08-02, the day's real
    "unauthorized trader"). Legs used to be pooled per EVENT, and the only
    partition check was len(legs) == the event's outcome count — so any two
    cheap outcomes of a tennis match (a moneyline side plus a totals side)
    summed under $1 and were bought as a "riskless set", one set per
    qualifying cycle, with no per-market claim, on leagues the strategy
    filter would have blocked (legs are collected before it runs), and with
    partial fills placed at the venue but never ledger-recorded. Now: a
    dutch book may only be assembled from the outcome set of ONE venue
    market; every filled leg is ledger-recorded whether or not the set
    completed; and an attempted market is claimed so it is tried once.
    """
    from edge.analysis.consistency import find_dutch_book
    from edge.execution.arbitrage import execute_dutch_book
    from edge.fairvalue.lines import parse_outcome_line, split_segment
    from edge.fairvalue.props import parse_prop

    # Legs pool by BET FAMILY — (market, segment, kind, |line|) — because a
    # partition is the outcome set of one PROPOSITION. The venue groups a
    # whole event's sides (ML, totals, spreads, props) under one market_id,
    # so market_id alone is not a partition boundary. Prop-shaped outcomes
    # never qualify: two cheap player props are not sides of anything.
    pools: dict = {}
    for adapter, market_id, leg in venue_legs:
        bet, why = parse_prop(leg.outcome)
        if bet is not None or why in ("no_threshold", "ambiguous_stat"):
            continue
        seg, name = split_segment(leg.outcome)
        p = parse_outcome_line(name)
        if p.kind in ("total", "spread") and p.point is not None:
            family, need = (seg, p.kind, abs(p.point)), 2
        else:
            family, need = (seg, "moneyline", None), expected
        pools.setdefault((adapter, market_id, family), (need, []))[1].append(leg)
    adapter, book = None, None
    for (a, market_id, family), (need, ls) in pools.items():
        if need < 2 or len(ls) != need:
            continue                     # a side is missing its book
        claim = f"arb_tried:{market_id}:{family}"
        if ledger.get_state(claim):
            continue                     # one attempt per proposition, ever
        # REAL fees, not zero (audit 2026-08-04): Kalshi's taker fee is
        # ~1.75c at 50c — enough to turn every arb in the 1-2c window
        # into a guaranteed loss. Worst leg's fee, applied to every leg:
        # conservative by construction.
        fee_fn = getattr(a, "taker_fee", None)
        worst_fee = (max(fee_fn(l.price) for l in ls) if fee_fn else 0.0)
        b = find_dutch_book(event=claim, kind=str(family[1]), legs=ls,
                            expected_outcomes=need,
                            fee_per_contract=worst_fee)
        if b is not None:
            adapter, book = a, b
            break
    if book is None:
        return False
    funnel["arb_found"] = funnel.get("arb_found", 0) + 1
    funnel.setdefault("arb_books", []).append(
        {"event": f"{ev.home} v {ev.away}", "cost": round(book.cost, 4),
         "profit_per_set": book.profit_per_set, "legs": len(book.legs),
         "depth_sets": book.sets})
    # Dollar-capped, depth-bounded (audit 2026-08-04: the computed depth
    # was discarded and every arb bought exactly 1 contract — profit was
    # capped at pennies regardless of how much the book offered). Every
    # guarantee in execute_dutch_book is per-set and scale-invariant;
    # `sets` (config max_sets) stays as the floor.
    take = min(book.sets, max(sets, int(max_usd / max(book.cost, 0.01))))
    if not dry_run:
        # book.event IS the claim key (arb_tried:market:family).
        ledger.set_state(book.event, {"ts": time.time()})
    res = execute_dutch_book(adapter=adapter, book=book, sets=take,
                             dry_run=dry_run)
    funnel.setdefault("arb_status", {}).setdefault(res.status, 0)
    funnel["arb_status"][res.status] += 1
    if res.status == "INCOMPLETE_EXPOSED":
        # Loudest possible: a position nobody decided to take.
        funnel.setdefault("arb_exposed", []).append(
            {"event": f"{ev.home} v {ev.away}", "holding": res.exposed})
    if not dry_run:
        # EVERY filled leg reaches the ledger — complete sets and naked
        # rescues alike. Venue money moved; a ledger that only records the
        # flattering case is how today's positions appeared from nowhere.
        filled_orders = [o for o in res.orders
                         if o.get("ok") and float(o.get("count") or 0) > 0]
        legs_by_token = {l.token: l for l in book.legs}
        for o in filled_orders:
            token = o.get("token") or ""
            leg = legs_by_token.get(token)
            if leg is None:
                continue
            ledger.record_fill(
                fill_uid=f"arb-{book.event}-{leg.token}-{int(time.time())}",
                venue=adapter.name, market_key=f"{adapter.name}:{leg.token}",
                side="BUY", qty=float(take),
                price=float(o.get("price") or leg.price),
                league=ev.league_code, mode="LIVE_BETA", category="arb",
                decision={"arb": True, "cost_per_set": book.cost,
                          "profit_per_set": book.profit_per_set,
                          "status": res.status,
                          "legs": [l.outcome for l in book.legs]})
            mirror_side_channel_fill(
                venue=adapter.name, slug=leg.token,
                price=float(o.get("price") or leg.price), qty=float(take),
                league=ev.league_code, category="arb")
        if res.ok and res.complete:
            funnel["arb_profit"] = round(
                funnel.get("arb_profit", 0.0) + res.profit, 4)
    return True


def _kalshi_smoke(kalshi_a, ledger, is_live) -> None:
    """Owner-ordered connectivity proof (2026-08-04): ONE ~$0.50 taker
    order on the most liquid Kalshi market found, once ever — the ledger
    claim survives redeploys, so this can never become a drip. The result
    is posted as its own status and, on a fill, ledger-recorded under
    category "smoke" so no performance cohort inherits it.
    """
    import uuid as _uuid

    if ledger.get_state("kalshi_smoke_done"):
        return
    if not is_live():
        _post_status("kalshi_smoke", {"skipped": "not live"})
        return
    if not _strategy_live():
        _post_status("kalshi_smoke", {"skipped": "strategy_off"})
        return
    # The smoke probe is an ENGINE-class order: with the boot checklist no
    # longer demoting a mid-halt boot to PAPER, this is the one path that
    # would otherwise fire real money during an active halt.
    from edge.shadow.kalshi_guard import live_blocked

    blocked = live_blocked(ledger)
    if blocked:
        _post_status("kalshi_smoke", {"skipped": blocked})
        return
    try:
        for league in ("wnba", "mlb"):
            for vm in kalshi_a.discover_markets({league}):
                for name, ticker in vm.outcome_tokens.items():
                    book = kalshi_a.get_book(vm.market_id, ticker)
                    if book is None or not book.asks:
                        continue
                    ask = book.asks[0]
                    if not (0.05 <= ask.price <= 0.55 and ask.size >= 1):
                        continue
                    r = kalshi_a.place_order(
                        ticker, ask.price, 1,
                        client_order_id=str(_uuid.uuid4()), taker=True)
                    filled = (int(float(r.get("count") or 0))
                              if r.get("ok") else 0)
                    # Done ONLY on a real fill: the V2 endpoint can accept
                    # an IOC and fill zero (book moved), and "accepted,
                    # nothing bought" proves nothing — retry next boot with
                    # the venue's answer left readable either way.
                    state_key = ("kalshi_smoke_done"
                                 if r.get("ok") and filled > 0
                                 else "kalshi_smoke_last")
                    ledger.set_state(state_key, {
                        "ts": time.time(), "ticker": ticker,
                        "price": ask.price, "ok": bool(r.get("ok")),
                        "filled": filled,
                        "status": str(r.get("status"))[:200],
                        "raw_error": str((r.get("raw") or {}).get(
                            "error"))[:300],
                        "order_id": r.get("order_id")})
                    if filled:
                        if r.get("order_id"):
                            ledger.set_state(
                                f"kalshi_inline:{r['order_id']}",
                                {"ts": time.time()})
                        ledger.record_fill(
                            fill_uid=f"kalshi-smoke-{int(time.time())}",
                            venue="kalshi", market_key=f"kalshi:{ticker}",
                            side="BUY", qty=float(filled), price=ask.price,
                            fee=round(kalshi_a.taker_fee(ask.price) * filled,
                                      4),
                            league=league, mode="LIVE_BETA",
                            category="smoke",
                            decision={"smoke_test": True, "outcome": name})
                    _post_status("kalshi_smoke", {
                        "ticker": ticker, "outcome": name,
                        "price": ask.price, "requested": 1,
                        "filled": filled, "status": r.get("status"),
                        "order_id": r.get("order_id")})
                    log.warning("KALSHI SMOKE %s: %s (%s) @ %.2f filled=%s",
                                r.get("status"), ticker, name, ask.price,
                                filled)
                    return
        _post_status("kalshi_smoke",
                     {"error": "no active market with ask in 5-55c found"})
    except Exception as exc:  # noqa: BLE001 — a smoke test never breaks boot
        _post_status("kalshi_smoke",
                     {"error": f"{type(exc).__name__}: {str(exc)[:140]}"})


def _xv_feed_team(oc_name: str, ev) -> str | None:
    """Which of the event's two teams a venue outcome IS — or None.

    Cross-venue pooling joins legs from different venues by FEED team
    identity, because the venues' own strings never match each other
    ("LAL" vs "Los Angeles Lakers"). Full-game moneyline only; anything
    else returns None and stays out of the pool.
    """
    from edge.fairvalue.lines import is_draw, parse_outcome_line, split_segment
    from edge.venues.mapper import team_score
    from edge.venues.pmus_slug import CODE_PREFIX, resolve_side

    seg, name = split_segment(oc_name)
    if seg is not None or is_draw(name):
        return None
    if name.startswith(CODE_PREFIX):
        return resolve_side(name[len(CODE_PREFIX):], ev.home, ev.away)
    if parse_outcome_line(name).kind != "moneyline":
        return None
    hit = None
    for team in (ev.home, ev.away):
        if team_score(team, name) >= 0.95:
            if hit is not None:
                return None          # matches both: refuse, never guess
            hit = team
    return hit


def _try_cross_venue(*, ledger, ev, pool, min_profit, max_usd, dry_run,
                     funnel) -> bool:
    """Team A on one venue + Team B on the other, under $1 after fees.

    The arithmetic is the dutch book's; the extra risk is SETTLEMENT
    EQUIVALENCE — both venues must pay the same game result. The caller
    only feeds no-tie two-way leagues, and this function still refuses
    anything that is not exactly two teams on two different venues.
    """
    from edge.execution.arbitrage import (
        cross_venue_cost,
        execute_cross_venue,
    )

    if len(pool) != 2 or set(pool) != {ev.home, ev.away}:
        return False
    claim = f"xv_tried:{ev.event_key()}"
    if ledger.get_state(claim):
        return False
    best, best_cost = None, 10.0
    (t1, sides1), (t2, sides2) = pool.items()
    for v1, leg1 in sides1.items():
        for v2, leg2 in sides2.items():
            if v1 == v2:
                continue             # same-venue books have their own path
            cost = cross_venue_cost([leg1, leg2])
            if cost < best_cost:
                best, best_cost = [leg1, leg2], cost
    if best is None:
        return False
    profit = round(1.0 - best_cost, 4)
    funnel["xv_checked"] = funnel.get("xv_checked", 0) + 1
    # Near-miss telemetry: the evidence for tuning min_profit later.
    funnel["xv_best_seen"] = max(funnel.get("xv_best_seen", -1.0), profit)
    if profit < min_profit:
        return False
    funnel["xv_found"] = funnel.get("xv_found", 0) + 1
    funnel.setdefault("xv_books", []).append(
        {"event": f"{ev.home} v {ev.away}", "cost": best_cost,
         "profit_per_set": profit,
         "venues": [getattr(l.adapter, "name", "?") for l in best]})
    res = execute_cross_venue(
        event=f"{claim}", legs=best,
        max_sets=max(1, int(max_usd / best_cost)), dry_run=dry_run)
    funnel.setdefault("xv_status", {}).setdefault(res.status, 0)
    funnel["xv_status"][res.status] += 1
    if res.status == "INCOMPLETE_EXPOSED":
        funnel.setdefault("xv_exposed", []).append(
            {"event": f"{ev.home} v {ev.away}", "holding": res.exposed})
    if dry_run:
        return True
    # Claim AFTER the attempt, and only when venue money moved (audit:
    # claim-before-execution turned every transient no-fill into a
    # permanent blacklist). A clean miss may retry next sweep.
    if res.status not in ("no_fills", "nothing_to_do", "not_cross_venue",
                          "implausible_profit"):
        ledger.set_state(claim, {"ts": time.time(), "status": res.status})
    legs_by_token = {l.token: l for l in best}
    for o in res.orders:
        if not (o.get("ok") and float(o.get("count") or 0) > 0):
            continue
        leg = legs_by_token.get(o.get("token") or "")
        if leg is None:
            continue
        vname = getattr(leg.adapter, "name", "?")
        ledger.record_fill(
            fill_uid=f"xv-{claim}-{leg.token}-{int(time.time())}",
            venue=vname, market_key=f"{vname}:{leg.token}",
            side="BUY", qty=float(o.get("count") or res.sets),
            price=float(o.get("price") or leg.price),
            fee=round(leg.fee() * float(o.get("count") or res.sets), 4),
            league=ev.league_code, mode="LIVE_BETA", category="arb",
            decision={"arb": True, "cross_venue": True,
                      "cost_per_set": best_cost, "profit_per_set": profit,
                      "status": res.status,
                      "legs": [f"{getattr(l.adapter, 'name', '?')}:"
                               f"{l.outcome}" for l in best]})
        mirror_side_channel_fill(
            venue=vname, slug=leg.token,
            price=float(o.get("price") or leg.price),
            qty=float(o.get("count") or res.sets),
            league=ev.league_code, category="arb")
    if res.ok and res.complete:
        funnel["xv_profit"] = round(
            funnel.get("xv_profit", 0.0) + res.profit, 4)
    return True

def run_cycle(adapters, feed_client, policy, risk, ledger, sport_keys: list[str],
              candidates: dict | None = None, match_cache: dict | None = None,
              explored_seen: set | None = None, study_seen: set | None = None,
              only_slugs: set | None = None) -> dict:
    """One sweep across all venues: feed → map (0.95 gate) → de-vig → book →
    strategy filter → risk approve → execute (paper-log or place, by mode).
    Both venues are judged against the SAME fair values.

    candidates: pre-discovered {venue: (adapter, [VenueMarket])} — pass this
    on fast cycles so discovery runs on its own slow clock; None = discover
    inline (tests, first cycle).

    only_slugs: REACTIVE pass — re-price just these outcome tokens (books the
    venue stream said changed) and nothing else. Same decision code path, so
    a reactive fill is indistinguishable from a swept one; but the pass is a
    partial view of the world, so whole-cycle accounting (match-rate stats,
    divergence surveillance, circuit-breaker marks, watchdog) is left to the
    full sweeps — computing them from a handful of markets would be wrong."""
    from datetime import datetime, timezone

    from edge.execution.engine import strategy_filter
    from edge.execution.executor import build_decision_record, execute, market_key
    from edge.analysis.consistency import Leg as _ArbLeg
    from edge.analysis.consistency import find_dutch_book
    from edge.execution.arbitrage import execute_dutch_book
    from edge.fairvalue.devig import fair_value
    from edge.fairvalue.props import fair_for_prop, parse_prop
    from edge.fairvalue.lines import (
        is_draw,
        margin_to_spread,
        outcome_matches,
        pair_quotes,
        parse_outcome_line,
        split_segment,
        unit_conflicts,
    )
    from edge.venues.base import FillIntent
    from edge.venues.mapper import match_events_all, team_score
    from edge.venues.pmus_slug import CODE_PREFIX, resolve_side

    reactive = only_slugs is not None
    if candidates is not None:
        venue_candidates = candidates
    else:
        venue_candidates = discover_all(adapters, policy)
    if explored_seen is None:
        explored_seen = set()  # standalone call: dedupe within this cycle
    if study_seen is None and os.environ.get("EDGE_STUDY_ALL", "1") != "0":
        study_seen = set()

    events = []
    for sport_key in sport_keys:
        try:
            events.extend(feed_client.fetch_events(sport_key))
        except Exception as exc:  # noqa: BLE001 — one sport must not kill the sweep
            log.warning("feed fetch failed for %s: %s", sport_key, exc)

    now = time.time()
    day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    # Venues eligible for REAL orders when the engine is live; everything
    # else paper-logs even in LIVE_* modes (e.g. Kalshi evidence-gathering
    # with no Kalshi account).
    live_venues = {v.strip() for v in
                   os.environ.get("EDGE_LIVE_VENUES", "polymarket-us").split(",")
                   if v.strip()}
    cycle_started = time.time()
    # A cycle that cannot finish is indistinguishable from a cycle that finds
    # nothing — both report zero trades. The budget bounds the sweep and says
    # so out loud; the rotation stops the same tail of the slate being the
    # part that always gets cut.
    budget_s = float(os.environ.get("EDGE_CYCLE_BUDGET_S", "0")) or None
    funnel = {"mode": risk.mode, "feed_events": len(events), "matched": 0,
              "tradeable": 0, "books_checked": 0, "logged": 0, "rejects": {}}
    if events and not reactive:
        # Capital, not opportunity, is what binds a small book. We buy and
        # hold to resolution, so every dollar is locked until its game
        # settles: a bet on tonight's game can be recycled tomorrow, one on
        # next Sunday's cannot. Pricing the nearest kick-offs first therefore
        # turns the same bankroll over several times more often, at no extra
        # risk per trade — and if the cycle has to truncate, the games it
        # drops are the ones that would have tied money up longest.
        #
        # This replaces blind rotation, which existed to stop the same tail
        # being cut every cycle. Under settlement priority the cut is
        # deliberate rather than arbitrary, so fairness is no longer the
        # property we want. Rotation stays available for the case where
        # settlement times are unknown or identical.
        if os.environ.get("EDGE_SETTLEMENT_PRIORITY", "1") != "0" and any(
                getattr(e, "commence_ts", 0) for e in events):
            # Two-level priority: events that priced within 1c of their bar
            # last cycle jump the queue (staleness is the measured leak, and
            # these are the books where 25 seconds decides a fill), then
            # nearest kick-off as before. Stale proximity entries expire.
            for k, ts0 in list(_NEAR_THRESHOLD.items()):
                if now - ts0 > 1800:
                    del _NEAR_THRESHOLD[k]
            events = sorted(
                events,
                key=lambda e: (0 if e.event_key() in _NEAR_THRESHOLD else 1,
                               getattr(e, "commence_ts", 0) or float("inf")))
            funnel["order"] = "near-threshold+settlement"
            funnel["near_threshold_events"] = len(_NEAR_THRESHOLD)
        else:
            _ROTATION["i"] = (_ROTATION["i"] + 1) % max(len(events), 1)
            events = events[_ROTATION["i"]:] + events[:_ROTATION["i"]]
            funnel["order"] = "rotation"
    if reactive:
        funnel["reactive"] = len(only_slugs)
    if risk.is_live:
        funnel["live_venues"] = sorted(live_venues)
    marks: dict[str, float] = {}   # market_key -> best bid (circuit-breaker marks)
    # Slices whose prices systematically disagree with the venue's own —
    # barred from trading until the disagreement clears, whatever caused it.
    drift_due = set() if reactive else ledger.awaiting_drift()
    # What our own fills have already proved about the cost of being the
    # one who gets filled. Read once per cycle: the bar must move with the
    # evidence, but not mid-sweep, or two identical outcomes seen seconds
    # apart get judged by different rules.
    drift_pen = ledger.drift_penalties(days=7)
    # Free drift samples awaiting their second look. Priced outcomes cost
    # nothing to observe, so this keeps measuring even when the bar is high
    # enough that nothing trades — which is exactly when a fills-only meter
    # goes blind and the bar can never come back down.
    px_due = {} if reactive else ledger.awaiting_price_drift()
    # How much of an apparent edge is real, measured from those free samples.
    # None until there are enough of them across a wide enough range of
    # claims — until then the flat surcharge governs instead.
    min_anchors = int((policy.risk.get("min_anchor_books")
                       if policy.risk.get("min_anchor_books") is not None else 1))
    arb_cfg = policy.risk.get("arbitrage") or {}
    arb_on = bool(arb_cfg.get("enabled")) and risk.is_live
    arb_sets = int(arb_cfg.get("max_sets", 1))
    arb_budget = int(arb_cfg.get("max_books_per_cycle", 3))
    arb_done = 0
    # Cross-venue: Team A on one venue + Team B on the other, fee-loaded
    # asks summing under $1. Only no-tie two-way sports — the "guarantee"
    # rests entirely on both venues settling the same game result, so the
    # league allowlist is the safety, not a tuning knob.
    xv_cfg = arb_cfg.get("cross_venue") or {}
    xv_on = arb_on and bool(xv_cfg.get("enabled"))
    xv_min_profit = float(xv_cfg.get("min_profit", 0.02))
    xv_max_usd = float(xv_cfg.get("max_usd", 10.0))
    xv_budget = int(xv_cfg.get("max_books_per_cycle", 2))
    xv_leagues = set(xv_cfg.get("leagues") or ["nba", "wnba", "nhl", "mlb"])
    xv_done = 0
    rev = ledger.reversion(days=7)
    keep = rev.get("keep")
    if keep is not None:
        funnel["shrink"] = {"keep": keep, "slope": rev["slope"], "n": rev["n"]}
    if any(v > 0 for v in drift_pen.values()):
        funnel["drift_penalty"] = {k: v for k, v in drift_pen.items() if v > 0}
    quarantined = _quarantined(ledger)
    if quarantined:
        funnel["quarantined"] = sorted("/".join(q) for q in quarantined)[:8]

    def reject(bucket: str) -> None:
        funnel["rejects"][bucket] = funnel["rejects"].get(bucket, 0) + 1

    refreshed_sports: set[str] = set()
    for seen_events, ev in enumerate(events):
        # Where-am-I marker for the boot beacon: if the process dies
        # mid-sweep, the last beacon names the sport and position.
        _BEACON["phase"] = f"{ev.sport_key}:{seen_events}/{len(events)}"
        if budget_s and time.time() - cycle_started > budget_s:
            funnel["truncated"] = {"reached": seen_events, "of": len(events)}
            log.warning("cycle budget %.0fs exhausted after %s/%s events — "
                        "the rest run next cycle", budget_s, seen_events,
                        len(events))
            break
        # Long cycles age early-fetched quotes past the 30s freshness rule
        # before late events are processed. Refresh a sport AT MOST ONCE per
        # cycle (quota discipline), but when we DO pay for that fetch, apply
        # it to EVERY event of that sport still in this sweep — not just the
        # one that happened to trigger it.
        #
        # Refreshing only the triggering event was costing 525 stale_quote
        # rejections per cycle: a sport with 30 games would rescue one and
        # abandon 29, having already marked itself refreshed. Same HTTP call,
        # same credit, thirty times the rescued markets.
        if not ev.is_fresh(25, now=time.time()) and ev.sport_key not in refreshed_sports:
            refreshed_sports.add(ev.sport_key)
            try:
                fresh = {(f.home, f.away): f
                         for f in feed_client.fetch_events(ev.sport_key)}
                rescued = 0
                for stale in events:
                    if stale.sport_key != ev.sport_key:
                        continue
                    f = fresh.get((stale.home, stale.away))
                    if f is None or f.fetched_at <= stale.fetched_at:
                        continue
                    # EVERY per-category field moves together with the
                    # stamps. props and anchors were omitted here (audit
                    # 2026-08-04), so an event's props stayed at their
                    # first-fetch vintage forever while fetched_at was
                    # repeatedly renewed — stale quotes wearing a fresh
                    # timestamp, on the highest-volume category.
                    (stale.h2h, stale.totals, stale.spreads, stale.segments,
                     stale.props, stale.books, stale.anchors, stale.depth,
                     stale.fetched_at, stale.enriched_at) = (
                        f.h2h, f.totals, f.spreads, f.segments,
                        f.props, f.books, f.anchors,
                        getattr(f, "depth", {}) or {},
                        f.fetched_at, getattr(f, "enriched_at", 0.0))
                    rescued += 1
                if rescued:
                    funnel["refreshed"] = funnel.get("refreshed", 0) + rescued
            except Exception as exc:  # noqa: BLE001 — stale check below still guards
                log.debug("event refresh failed for %s: %s", ev.sport_key, exc)
        if len(ev.h2h) < 2:
            continue
        # De-vigging is the per-event arithmetic (moneyline + every paired
        # spread/total line). Computed ON DEMAND and memoized: a reactive
        # pass that touches none of this event's outcomes never pays for it,
        # which is what makes sub-second re-pricing affordable.
        priced: dict = {}

        def _price_event(ev=ev) -> bool:
            if priced:
                return priced["ok"]
            names = list(ev.h2h)
            try:
                def _sane_overround(odds: list[float], n: int) -> bool:
                    # Sanity band BEFORE de-vigging (audit 2026-08-04).
                    # The synthetic quote is a median across books, so its
                    # overround can be an artifact of book mixing: an
                    # UNDERROUND pair (sum < 1) pushes the power exponent
                    # above 1 and EXAGGERATES favourites; a heavy-vig pair
                    # (sum >> 1.15 two-way) drives the exponent toward the
                    # numerically meaningless. Both were silently accepted.
                    s = sum(1.0 / o for o in odds)
                    lo, hi = 0.99, 1.02 + 0.13 * (n - 1)
                    if lo <= s <= hi:
                        return True
                    b = funnel.setdefault("overround_rejected", {})
                    b[f"{n}way"] = b.get(f"{n}way", 0) + 1
                    return False

                def _pool(h2h, totals, spreads):
                    fairs = {}
                    if len(h2h) >= 2 and _sane_overround(
                            [h2h[n] for n in h2h], len(h2h)):
                        fairs = dict(zip(list(h2h), fair_value(
                            [h2h[n] for n in h2h])))
                    sides: list[tuple] = []  # (ParsedLine, fair, sample_key)
                    for kind, quotes in (("total", totals), ("spread", spreads)):
                        for pq in pair_quotes(quotes, kind):
                            if not _sane_overround([pq.a_odds, pq.b_odds], 2):
                                continue
                            fa, fb = fair_value([pq.a_odds, pq.b_odds])
                            sides.append((pq.a_parsed, fa, pq.a_name))
                            sides.append((pq.b_parsed, fb, pq.b_name))
                    return fairs, sides

                priced["fairs"], priced["deriv_sides"] = _pool(
                    ev.h2h, ev.totals, ev.spreads)
                # Each partial-game segment gets its OWN de-vigged pool. A
                # first-five-innings run line is priced against the sharp
                # first-five quote or not at all.
                priced["seg"] = {
                    seg: _pool(q.get("h2h") or {}, q.get("totals") or {},
                               q.get("spreads") or {})
                    for seg, q in (getattr(ev, "segments", None) or {}).items()}
                priced["ok"] = True
            except Exception as exc:  # noqa: BLE001 — one pathological odds set
                log.warning("fair value failed for %s vs %s (%s): %s",
                            ev.home, ev.away, ev.h2h, exc)
                priced["ok"] = False
            return priced["ok"]

        # Which sample key the last successful fair_for priced against —
        # the join into ev.depth for per-outcome consensus. Read immediately
        # after the call, same thread, so a plain dict is safe.
        matched_sample = {"key": None}

        def _finish_intent(it: dict) -> None:
            """Gate, size, approve and place one qualified intent.

            Called inline for PAPER, and from the router for LIVE — same
            arithmetic either way, so routing can never change WHAT gets
            checked, only WHERE the winning order goes.
            """
            adapter, book, ask = it["adapter"], it["book"], it["ask"]
            verdict, entry_px = it["verdict"], it["entry_px"]
            mkey, category = it["mkey"], it["category"]
            effective_mode = it["effective_mode"]
            # PER-FILL net-margin gate: the band threshold prices the EDGE;
            # this prices the BOOK actually in front of us —
            # (edge - paid_over_mid) / price >= floor. Maker entries price
            # below mid and pass more easily, correctly. A book with no bid
            # has no measurable mid: core proceeds on the band floor,
            # exploration refuses (the study tier never buys unmeasurable).
            net_floor = float(policy.risk.get("net_margin_floor", 0.02))
            best_bid = book.bids[0].price if book.bids else None
            if best_bid is not None and 0 < best_bid < ask.price:
                paid_over_mid = entry_px - (ask.price + best_bid) / 2
                net = (verdict.edge - paid_over_mid) / max(entry_px, 1e-6)
                if net < net_floor:
                    reject("net_margin")
                    nm = funnel.setdefault("net_margin_refused", {})
                    nm[category] = nm.get(category, 0) + 1
                    return
            elif verdict.tier == "exploration":
                reject("net_margin_unmeasurable")
                return
            # Stake in proportion to how far the edge clears its bar,
            # bounded by the same caps either way.
            want = risk.size_for_edge(verdict.edge, verdict.threshold,
                                      mode=effective_mode)
            # PROBATION: an unmeasured league trades at HALF ticket until
            # its own live settlements clear the bar (>=50 settled, net>0)
            # — floored at $1.00, the smallest executable trade (approve()
            # refuses anything under $1 and the venue sells whole
            # contracts). At the owner-directed $1 ticket (2026-08-05) the
            # halving therefore no-ops rather than silently blocking every
            # unmeasured league.
            if not policy.league_measured(ev.league_code):
                rec = _league_probation_cache.get(ev.league_code or "?")
                if rec is None:
                    rec = ledger.league_live_record(ev.league_code or "?")
                    _league_probation_cache[ev.league_code or "?"] = rec
                if rec["n"] < 50 or rec["net"] <= 0:
                    want = round(max(want / 2, 1.0), 2)
                    pv = funnel.setdefault("probation", {})
                    pv[ev.league_code or "?"] = rec
            approved, why = risk.approve(adapter.name, mkey, ev.event_key(),
                                         requested_usd=want, now=now,
                                         mode=effective_mode,
                                         tier=verdict.tier)
            if approved <= 0:
                reject(why.split(":")[0].split()[0])
                return
            claim = risk.claim_key(effective_mode, adapter.name, mkey,
                                   ev.event_key())
            decision = build_decision_record(
                fair=it["fair"], edge=verdict.edge,
                threshold=verdict.threshold,
                band=verdict.band, book=book,
                feed_snapshot={"h2h": ev.h2h, "home": ev.home,
                               "away": ev.away, "fetched_at": ev.fetched_at},
                approved_usd=approved, guard_reason=why,
            )
            decision["category"] = category
            decision["outcome"] = it["oc_name"]
            decision["entry"] = {"price": entry_px, "taker": it["taker"],
                                 "ask": ask.price}
            decision["tier"] = verdict.tier
            decision["trigger"] = "reactor" if reactive else "sweep"
            decision["consensus_books"] = it["eff_books"]
            # Whale alignment (moneyline only): a source whale is long the
            # same team in the same game. Graded at settlement; costs one
            # dict lookup here.
            if category == "moneyline" and _WHALE_MAP:
                try:
                    from edge.shadow.whale_align import aligned

                    decision["whale_aligned"] = aligned(
                        it["token"], it["oc_name"], _WHALE_MAP)
                except Exception:  # noqa: BLE001 — tag-only
                    pass
            funnel.setdefault("by_tier", {}).setdefault(verdict.tier, 0)
            funnel["by_tier"][verdict.tier] += 1
            decision["venue_market"] = {"title": it["match"].market.title,
                                        "id": it["match"].market.market_id,
                                        "match_score": round(it["match"].score, 3)}
            result = execute(adapter=adapter, ledger=ledger,
                             mode=effective_mode,
                             mkey=mkey, league=ev.league_code,
                             ask_price=ask.price, ask_size=ask.size,
                             size_usd=approved, edge=verdict.edge,
                             threshold=verdict.threshold, decision=decision,
                             ts=time.time(), entry_price=entry_px,
                             taker=it["taker"], event_key=claim,
                             category=it["drift_cat"],
                             # The BINDING ceiling from this approve() — not
                             # per_fill_max, which would let the contract
                             # round-up breach a tighter market/event/day
                             # room (audit 2026-08-05).
                             max_fill_usd=risk.last_fill_ceiling)
            if result["placed"]:
                ledger.record_entry_fair(mkey, round(entry_px, 4),
                                         round(it["fair"], 4),
                                         category=it["drift_cat"])
            funnel["logged"] += int(result["placed"])
            funnel.setdefault("by_category", {}).setdefault(category, 0)
            funnel["by_category"][category] += int(result["placed"])
            if not result["placed"]:
                reject(result["status"].split(":")[0])
                # approve() claimed this market before the attempt; nothing
                # filled, so the claim goes back — otherwise every unfilled
                # order retires a market from the universe forever.
                if claim:
                    ledger.release_event(claim)
                    funnel["reclaimed"] = funnel.get("reclaimed", 0) + 1
            intent = FillIntent(
                market_id=it["match"].market.market_id,
                outcome_id=it["token"],
                limit_price=entry_px, size_usd=approved,
                fair_value=round(it["fair"], 4),
                edge=round(it["fair"] - entry_px, 4),
                league=ev.league_code, band=verdict.band,
            )
            log_shadow_fill(intent, book,
                            {"h2h": ev.h2h, "home": ev.home, "away": ev.away},
                            ask.size * ask.price >= approved,
                            whale_alignment=None)

        def fair_for(oc_name: str):
            """(fair, category, reason) — reason names WHY no fair value was
            found, so the funnel can distinguish 'venue lists a line our
            sharp book doesn't quote' from 'we couldn't identify the side'."""
            matched_sample["key"] = None
            if not _price_event():
                return None, "moneyline", {"reason": "fair_error"}
            # Player props first: they are per-PLAYER, not per-team, so the
            # team matcher below would either fail or — worse — match a
            # player's surname against a club and price a strikeout market
            # off a moneyline. Tried before any team logic sees the text —
            # UNCONDITIONALLY. This used to run only when the event carried
            # prop quotes, which sent every prop-shaped outcome on a
            # quoteless event to the TEAM matcher: ~914 no_side_match
            # rejects a cycle filed under moneyline, and one real hazard —
            # "Cardinals over 4.5 runs" is a team total whose number sits
            # inside the alternate GAME-total ladder, so the totals matcher
            # could pair it against a different proposition and hand back a
            # confident price for the wrong bet. Prop-shaped text is the
            # prop parser's to price or to refuse, never anyone else's.
            bet, why = parse_prop(oc_name)
            if bet is not None:
                props = getattr(ev, "props", None)
                if not props:
                    return None, "prop", {"reason": "prop_no_feed_quotes",
                                          "market": bet.market_key,
                                          "venue_outcome": oc_name[:48]}
                # Props come from a separately cached per-event payload; the
                # 30s fetched_at rule below never sees its age. Ten minutes
                # is generous for prop lines pre-match and refuses the
                # half-hour-old vintage the cache could otherwise serve.
                enr = getattr(ev, "enriched_at", 0.0)
                if enr and time.time() - enr > _PROP_MAX_AGE_S:
                    return None, "prop", {"reason": "stale_prop_quote",
                                          "age_s": int(time.time() - enr),
                                          "venue_outcome": oc_name[:48]}
                fair, miss = fair_for_prop(bet, props)
                if fair is None:
                    return None, "prop", miss
                matched_sample["key"] = (bet.market_key, bet.player, bet.point)
                return fair, "prop", None
            if why in ("no_threshold", "ambiguous_stat"):
                # It named a stat we know but we could not pin the line.
                # That is a refusal worth counting, not a silent fall
                # through to team matching.
                return None, "prop", {"reason": f"prop_{why}",
                                      "venue_outcome": oc_name[:48]}
            segment, oc_name = split_segment(oc_name)
            # Margin prose -> the run-line spread it actually is, so it can
            # pair against the sharp spread quote for the SAME segment.
            oc_name = margin_to_spread(oc_name) or oc_name
            if segment is None:
                fairs, deriv_sides = priced["fairs"], priced["deriv_sides"]
            else:
                # Same per-event payload as props — same staleness hole.
                enr = getattr(ev, "enriched_at", 0.0)
                if enr and time.time() - enr > _PROP_MAX_AGE_S:
                    return None, "segment", {
                        "reason": "stale_segment_quote",
                        "age_s": int(time.time() - enr),
                        "venue_outcome": oc_name[:48]}
                pool = priced["seg"].get(segment)
                if not pool:
                    # We have no sharp quote for this part of the game. The
                    # full-game line is NOT a substitute — that is a different
                    # bet, and pricing one against the other is how phantom
                    # edges are manufactured.
                    return None, "segment", {
                        "reason": f"no_sharp_quote_segment_{segment}",
                        "venue_outcome": oc_name[:48],
                        "segments_priced": sorted(priced["seg"]) or ["none"]}
                fairs, deriv_sides = pool
            # Slug-code side marker: pick between the event's two teams by
            # code, or refuse. This is discrimination between two known
            # candidates, not open-ended identification.
            if oc_name.startswith(CODE_PREFIX):
                code = oc_name[len(CODE_PREFIX):]
                side = resolve_side(code, ev.home, ev.away)
                if side is None:
                    return None, "moneyline", {
                        "reason": "unresolved_slug_code",
                        "code": code, "home": ev.home[:24], "away": ev.away[:24]}
                oc_name = side
            p = parse_outcome_line(oc_name)
            if p.kind in ("total", "spread"):
                # A stated unit must be the one the sharp quote counts in.
                # "Over 23.5 games" IS the tennis totals market; "Over 2.5
                # sets" merely looks like it — same shape, different
                # proposition — and pairing them would price sets off a
                # games line. Unknown units fail closed.
                if unit_conflicts(p.unit, ev.sport_key):
                    return None, p.kind, {
                        "reason": f"{p.kind}_unit_mismatch",
                        "unit": p.unit, "sport": ev.sport_key,
                        "venue_outcome": oc_name[:48]}
                for side, f, sname in deriv_sides:
                    if side.kind == p.kind and outcome_matches(oc_name, side):
                        matched_sample["key"] = (f"[{segment}] {sname}"
                                                 if segment else sname)
                        return f, p.kind, None
                have = sorted({s.point for s, _, _n in deriv_sides
                               if s.kind == p.kind and s.point is not None})
                return None, p.kind, {
                    "reason": f"no_sharp_quote_{p.kind}",
                    "venue_line": p.point, "sharp_lines": have[:8],
                }
            # The draw, before any team matching. The venue writes it as
            # "Tie (Reg. Time)" and the feed as "Draw"; string similarity
            # puts that pair at 0.24, so every three-way soccer market was
            # silently losing its third outcome — 2,989 rejections in one
            # live cycle, the single largest loss in the funnel.
            if is_draw(oc_name):
                for team_name, f in fairs.items():
                    if is_draw(team_name):
                        matched_sample["key"] = (f"[{segment}] {team_name}"
                                                 if segment else team_name)
                        return f, "moneyline", None
                return None, "moneyline", {
                    "reason": "no_draw_quote",
                    "venue_outcome": oc_name[:48],
                    "feed_teams": [t[:24] for t in list(fairs)[:4]]}
            best_name, best_score = "", 0.0
            for team_name, f in fairs.items():
                if is_draw(team_name):
                    continue      # a team is never the draw, and vice versa
                s = team_score(team_name, oc_name)
                if s >= 0.95:
                    matched_sample["key"] = (f"[{segment}] {team_name}"
                                             if segment else team_name)
                    return f, "moneyline", None
                if s > best_score:
                    best_name, best_score = team_name, s
            # Name the NEAR MISS. "208 outcomes didn't match a side" is a
            # count; "'Guadalajara' scored 0.71 against 'CD Guadalajara'" is
            # a fix. Mapping is now the funnel's biggest single loss, so the
            # example has to carry enough to act on.
            return None, "moneyline", {
                "reason": "no_side_match_moneyline",
                "venue_outcome": oc_name[:48],
                "closest_feed_team": best_name[:32],
                "score": round(best_score, 3),
                "feed_teams": [t[:24] for t in list(fairs)[:4]],
            }

        # Cross-venue legs for THIS event, keyed by FEED team so venues'
        # different naming can meet: {team: {venue_name: XVLeg}}.
        xv_pool: dict = {}
        # Best-execution routing (owner, 2026-08-04: "if prices are better
        # on Kalshi, take them there — and vice versa"). LIVE intents that
        # clear the bar are NOT executed inline; they park here keyed by
        # bet identity (the feed sample key) until every venue has priced
        # the event, then the venue offering the largest fee-net edge wins
        # and the same real-world bet is never taken twice. PAPER keeps
        # executing inline on every venue — the paper record's job is the
        # venue comparison itself.
        route_pending: dict = {}
        for adapter, candidates in venue_candidates.values():
            # Fuzzy matching is CPU-heavy at 10s cadence; results only change
            # when discovery changes, so memoize per (venue, event) between
            # rediscoveries (main resets the cache on each discovery pass).
            mkey_cache = (adapter.name, ev.event_key())
            if match_cache is not None and mkey_cache in match_cache:
                matches = match_cache[mkey_cache]
            else:
                matches = match_events_all(ev.home, ev.away, ev.league_code, candidates)
                if match_cache is not None:
                    match_cache[mkey_cache] = matches
            best = matches[0] if matches else None
            if not reactive:  # partial passes must not skew the match-rate
                ledger.record_match_stat(day, adapter.name, ev.league_code or "?",
                                         mapped=best is not None,
                                         tradeable=bool(best and best.tradeable))
            if best is None:
                continue
            funnel["matched"] += 1
            if not best.tradeable:  # hard rule: <0.95 confidence = UNMAPPED
                reject("unmapped_low_confidence")
                continue
            funnel["tradeable"] += 1
            seen_tokens: set[str] = set()
            # Dutch-book legs for THIS event. Polymarket US lists one market
            # per outcome, so a complete partition spans several markets and
            # can only be assembled at the event level.
            arb_legs: list = []
            for match in matches:
                if not match.tradeable:
                    continue
                for oc_name, token in match.market.outcome_tokens.items():
                    # Reactive pass: only the books that actually moved. This
                    # is the whole speed win — everything else is skipped
                    # before any pricing work happens.
                    if reactive and token not in only_slugs:
                        continue
                    if token in seen_tokens:
                        continue
                    seen_tokens.add(token)
                    fair, category, miss = fair_for(oc_name)
                    if fair is None:
                        # Previously a silent skip — the single biggest blind
                        # spot in the funnel. Count it and keep one example.
                        reject(miss["reason"])
                        # Keep SEVERAL examples per reason, not one. A single
                        # sample of a 200-a-cycle failure shows a symptom; a
                        # handful shows the pattern.
                        ex = funnel.setdefault("unpriced_examples", {})
                        bucket = ex.setdefault(miss["reason"], [])
                        if isinstance(bucket, list) and len(bucket) < 5:
                            bucket.append(miss)
                        continue
                    if not ev.is_fresh(30, now=time.time()):  # hard rule: fresh only
                        reject("stale_quote")
                        continue
                    book = adapter.get_book(match.market.market_id, token)
                    if book is None or not book.asks:
                        reject("no_book")
                        continue
                    funnel["books_checked"] += 1
                    ask = book.asks[0]
                    if arb_on and ask.size >= 1:
                        # Carry the venue AND THE MARKET with the leg. A dutch
                        # book is the outcome set of ONE market: across venues
                        # the outcomes settle on different criteria, and across
                        # MARKETS of one event they are not a partition at all
                        # — a cheap moneyline plus a cheap total summed under
                        # $1 and was bought as "riskless" for most of
                        # 2026-08-02, one fake set per qualifying cycle, on
                        # leagues the strategy filter would have refused. Legs
                        # are pooled per market_id and a book may only be
                        # assembled inside one pool.
                        arb_legs.append((adapter, match.market.market_id,
                                         _ArbLeg(
                            outcome=oc_name, token=token, price=ask.price,
                            size=float(ask.size))))
                    if (xv_on and category == "moneyline"
                            and len(ev.h2h) == 2
                            and (ev.league_code or "") in xv_leagues
                            and ask.size >= 1):
                        team = _xv_feed_team(oc_name, ev)
                        if team is not None:
                            from edge.execution.arbitrage import XVLeg
                            side = xv_pool.setdefault(team, {})
                            cur = side.get(adapter.name)
                            # Keep the venue's CHEAPEST listing per team.
                            if cur is None or ask.price < cur.price:
                                side[adapter.name] = XVLeg(
                                    adapter=adapter, token=token,
                                    outcome=team, price=ask.price,
                                    size=float(ask.size))
                    mkey = market_key(adapter.name, token)
                    # Second observation for the adverse-selection detector.
                    # Free: this fair value was computed anyway.
                    if mkey in drift_due:
                        ledger.record_drift_later(mkey, round(fair, 4))
                        drift_due.discard(mkey)
                    if book.bids:
                        marks[mkey] = book.bids[0].price

                    # Pricing-integrity surveillance: how far is our fair
                    # value from the venue's own mid? Cents = agreement;
                    # tens of cents, systematically = we are pricing a
                    # different bet than the one listed.
                    if book.bids:
                        if not reactive:  # sampled on sweeps, not per tick
                            venue_mid = (book.bids[0].price + ask.price) / 2
                            ledger.record_divergence(day, adapter.name,
                                                     ev.league_code or "?",
                                                     category,
                                                     abs(fair - venue_mid))
                        # The BAN, unlike the sampling, always applies.
                        if (adapter.name, ev.league_code or "?",
                                category) in quarantined:
                            reject("quarantined_slice")
                            continue
                    effective_mode = risk.mode if (
                        risk.is_live and _strategy_live()
                        and adapter.name in live_venues) else "PAPER"
                    # Ask the venue where it would actually buy. A venue that
                    # can rest an order quotes inside the spread, so the
                    # threshold is judged at the price we'd really pay —
                    # which qualifies trades the ask alone would have killed.
                    # PAPER always crosses: a paper "fill" at a resting price
                    # assumes a queue we never actually joined, and inventing
                    # fills is how a shadow record starts lying about ROI.
                    entry_px, taker = (adapter.plan_entry(book)
                                       if effective_mode != "PAPER"
                                       else (ask.price, True))
                    fee = (adapter.taker_fee if taker else adapter.maker_fee)(entry_px)
                    # The bar this outcome must clear is its band threshold
                    # PLUS whatever adverse drift we have measured for its
                    # kind. Draws are scored apart from the two-way sides:
                    # the draw is the longshot leg of a three-way, which is
                    # where book margin concentrates and where de-vig methods
                    # disagree most, so blending it into the moneyline number
                    # hides it inside the average it is dragging down.
                    drift_cat = ("draw" if category == "moneyline"
                                 and is_draw(oc_name) else category)
                    # No reference-class book, no trade.
                    #
                    # Measured 2026-07-31: MLB props carry Pinnacle across 440
                    # outcomes a game; Liga MX props carry 191 outcomes and NOT
                    # ONE sharp book, and NHL is lowvig alone on 5 of 31 events.
                    # A fair value with no anchor is a weighted median of books
                    # that are themselves following someone else, and the
                    # largest apparent edges come from the thinnest consensus —
                    # so this is where fake edge is manufactured, and we hold
                    # open positions in exactly those leagues.
                    # Below 20c the bar doubles. A 3c threshold at a 7c price
                    # claims the venue is ~40% wrong about the probability —
                    # exactly where favorite-longshot bias pays, and exactly
                    # where OUR de-vig error is proportionally largest. One
                    # anchor may be one stale Pinnacle quote; a longshot claim
                    # needs two independent sharp books standing behind it or
                    # the "edge" is more likely ours than the market's.
                    need_anchors = (max(min_anchors, 2)
                                    if entry_px < LOW_PRICE_CUTOFF
                                    else min_anchors)
                    # Per-OUTCOME depth when the priced sample is known
                    # (audit 2026-08-04): the event-level counts let a deep
                    # alt rung quoted by one soft book inherit the
                    # moneyline's Pinnacle anchor — the guard was written
                    # per-event and the hazard is per-market. Event-level
                    # remains the fallback for outcomes with no sample key.
                    _d = (getattr(ev, "depth", None) or {}).get(
                        matched_sample["key"])
                    eff_books = _d[0] if _d else getattr(ev, "books", 0)
                    eff_anchors = _d[1] if _d else getattr(ev, "anchors", 0)
                    if eff_anchors < need_anchors:
                        reject("no_sharp_anchor")
                        ex = funnel.setdefault("unpriced_examples", {})
                        bucket = ex.setdefault("no_sharp_anchor", [])
                        if isinstance(bucket, list) and len(bucket) < 5:
                            bucket.append({"reason": "no_sharp_anchor",
                                           "league": ev.league_code,
                                           "books": eff_books,
                                           "price": round(entry_px, 3),
                                           "needed": need_anchors,
                                           "anchors": eff_anchors,
                                           "outcome": str(
                                               matched_sample["key"])[:40]})
                        continue
                    verdict = strategy_filter(policy, ev.league_code, entry_px, fair,
                                              venue_fee=fee, category=category,
                                              consensus_books=eff_books,
                                              drift_penalty=drift_pen.get(
                                                  drift_cat, drift_pen.get("*", 0.0)),
                                              keep=keep)
                    # Second look at a free sample taken a minute or more ago.
                    # Closed out on the PRICING path, not the study path: the
                    # study bucket only comes round once an hour, which is far
                    # too late to see a one-minute move.
                    obs = px_due.pop(mkey, None)
                    if obs is not None:
                        ledger.record_price_drift_later(obs, round(fair, 4))
                        funnel["px_closed"] = funnel.get("px_closed", 0) + 1

                    # ── STUDY RECORD ──────────────────────────────────
                    # Every priced outcome is observed, whether or not it
                    # clears the trading bar. The narrow rules protect
                    # CAPITAL; they must never decide what we get to LEARN.
                    # Sampled once per market per hour so the record tracks
                    # price evolution without flooding.
                    if study_seen is not None:
                        # Phase the hourly bucket per market. A shared
                        # boundary means EVERY market studies in the same
                        # cycle — a thundering herd that grew with coverage
                        # until one cycle could no longer finish. Spreading
                        # them evenly across the hour keeps the per-cycle
                        # cost flat no matter how many markets there are.
                        sbucket = f"{mkey}:{int((time.time() + _phase(mkey)) // 3600)}"
                        if sbucket not in study_seen:
                            study_seen.add(sbucket)
                            funnel["studied"] = funnel.get("studied", 0) + 1
                            # One free drift sample per market per hour. The
                            # bucket string is already unique per market-hour,
                            # so it doubles as the sample's identity.
                            ledger.record_price_observation(
                                sbucket, mkey, round(entry_px, 4),
                                round(fair, 4), category=drift_cat,
                                edge=round(verdict.raw_edge, 4))
                            study_intent = FillIntent(
                                market_id=match.market.market_id, outcome_id=token,
                                limit_price=ask.price, size_usd=10.0,
                                fair_value=round(fair, 4),
                                edge=round(verdict.edge, 4),
                                league=ev.league_code, band=verdict.band,
                            )
                            log_shadow_fill(
                                study_intent, book,
                                {"h2h": ev.h2h, "home": ev.home, "away": ev.away,
                                 "category": category, "outcome": oc_name,
                                 "threshold": verdict.threshold,
                                 "would_clear": verdict.ok,
                                 "blocked_by": None if verdict.ok else verdict.reason},
                                ask.size * ask.price >= 10.0, tag="study")

                    # Constraint attribution: which single rule is doing the
                    # blocking? This is what tells us where the funnel dies.
                    if not verdict.ok:
                        blocker = ("league" if "blocked" in verdict.reason
                                   else "band" if "dead/unproven" in verdict.reason
                                   else "implausible" if "implausible" in verdict.reason
                                   else "threshold")
                        att = funnel.setdefault("blockers", {})
                        att[blocker] = att.get(blocker, 0) + 1
                        if blocker == "threshold":
                            # How close? Bucketed so we can see the shape of
                            # the miss distribution, not just the count.
                            gap = (verdict.threshold or 0) - verdict.edge
                            b = ("<0.5c" if gap < 0.005 else "0.5-1c" if gap < 0.01
                                 else "1-2c" if gap < 0.02 else ">2c")
                            gaps = funnel.setdefault("threshold_gap", {})
                            gaps[b] = gaps.get(b, 0) + 1
                            # Near-threshold memory: this event gets priced
                            # FIRST next cycle. A book 0.4c under its bar is
                            # one tick from firing; reaching it 25s sooner is
                            # the cheapest latency win available.
                            if gap < 0.01:
                                _NEAR_THRESHOLD[ev.event_key()] = now
                    # Mispricing-distribution telemetry: every completed
                    # fair-vs-ask comparison is counted, with the best edge
                    # seen and near-misses — so "are there mispricings?" is
                    # answered with numbers, not vibes.
                    if verdict.threshold is not None or verdict.ok:
                        es = funnel.setdefault("edges", {"evaluated": 0,
                                                         "best_cents": -99.0,
                                                         "near_miss_1c": 0,
                                                         "explored": 0})
                        es["evaluated"] += 1
                        es["best_cents"] = max(es["best_cents"],
                                               round(verdict.edge * 100, 2))
                        if (not verdict.ok and verdict.threshold is not None
                                and 0 < verdict.threshold - verdict.edge <= 0.01):
                            es["near_miss_1c"] += 1
                    if not verdict.ok:
                        # Below-threshold STUDY (JSONL only — never the ledger,
                        # never orders): edges between half-threshold and
                        # threshold are logged tagged 'exploration' so the
                        # grader can measure whether small mispricings pay on
                        # these venues. Implausible edges are mapping errors,
                        # not study data, and are excluded. One record per
                        # market per discovery window (not once per 10s cycle).
                        max_edge = float(policy.bands.get("max_believable_edge", 0.08))
                        if (verdict.threshold is not None
                                and verdict.threshold / 2 <= verdict.edge <= max_edge
                                and explored_seen is not None
                                and mkey not in explored_seen):
                            explored_seen.add(mkey)
                            funnel.setdefault("edges", {}).setdefault("explored", 0)
                            funnel["edges"]["explored"] += 1
                            explore_intent = FillIntent(
                                market_id=match.market.market_id, outcome_id=token,
                                limit_price=ask.price, size_usd=10.0,
                                fair_value=round(fair, 4),
                                edge=round(fair - ask.price, 4),
                                league=ev.league_code, band=verdict.band,
                            )
                            log_shadow_fill(
                                explore_intent, book,
                                {"h2h": ev.h2h, "home": ev.home, "away": ev.away},
                                ask.size * ask.price >= 10.0, tag="exploration")
                        reject(verdict.reason.split()[0])
                        continue
                    if effective_mode != "PAPER" and \
                            policy.league_allowed(ev.league_code) != "allow":
                        # Hard rule: real money only on MEASURED leagues;
                        # shadow-only leagues keep paper-logging.
                        effective_mode = "PAPER"
                    if effective_mode != "PAPER":
                        # Band quarantine (2026-08-04): entry bands our OWN
                        # settled fills measured net-negative trade paper
                        # only until a fresh cohort clears them. 10-15c:
                        # -55% ROI on 113 settled while 5-10c ran +24%.
                        for lo, hi in (policy.risk.get("band_quarantine")
                                       or []):
                            if lo <= entry_px < hi:
                                effective_mode = "PAPER"
                                bq = funnel.setdefault(
                                    "band_quarantined", {})
                                key = f"{lo:.2f}-{hi:.2f}"
                                bq[key] = bq.get(key, 0) + 1
                                break
                    it = {"adapter": adapter, "book": book, "ask": ask,
                          "verdict": verdict, "entry_px": entry_px,
                          "taker": taker, "mkey": mkey, "token": token,
                          "oc_name": oc_name, "category": category,
                          "drift_cat": drift_cat, "fair": fair,
                          "eff_books": eff_books, "match": match,
                          "effective_mode": effective_mode}
                    if effective_mode == "PAPER":
                        # Paper fills on EVERY venue — the paper record's
                        # job is the venue comparison itself.
                        _finish_intent(it)
                    else:
                        # LIVE: park until every venue has priced this
                        # event; the router below sends the order to the
                        # venue with the largest fee-net edge and refuses
                        # the same real-world bet a second listing.
                        ident = matched_sample["key"] or (adapter.name, mkey)
                        route_pending.setdefault(ident, []).append(it)


            # Every market of this event is now priced, so the complete
            # outcome set — which spans markets on a per-outcome venue — can
            # finally be assembled and checked for a dutch book.
            if arb_on and arb_done < arb_budget and len(arb_legs) >= 2:
                try:
                    if _try_arbitrage(ledger=ledger, ev=ev,
                                      venue_legs=arb_legs,
                                      expected=len(ev.h2h),
                                      sets=arb_sets, dry_run=not risk.is_live,
                                      funnel=funnel,
                                      max_usd=float(
                                          arb_cfg.get("max_usd", 10.0))):
                        arb_done += 1
                except Exception as exc:  # noqa: BLE001
                    # Arbitrage is additive. A failure here must never cost
                    # the ordinary pricing pass that already ran.
                    log.warning('arb scan failed for %s: %s', ev.home, exc)
                    funnel['arb_errors'] = funnel.get('arb_errors', 0) + 1
        # Best-execution router: every venue has priced this event, so each
        # bet identity goes to the venue offering the largest fee-net edge
        # (verdict.edge is fair - entry - venue_fee, shrunk — comparable
        # across venues by construction). One identity, one order, ever:
        # the second-best listing is priced worse for the SAME real-world
        # outcome, and buying both would double exposure, not edge.
        for ident, its in route_pending.items():
            its.sort(key=lambda i: i["verdict"].edge, reverse=True)
            best = its[0]
            if len(its) > 1:
                rt = funnel.setdefault(
                    "routing", {"contested": 0, "to": {}, "saved_c": 0.0})
                rt["contested"] += 1
                vn = best["adapter"].name
                rt["to"][vn] = rt["to"].get(vn, 0) + 1
                rt["saved_c"] = round(
                    rt["saved_c"] + max(0.0, (best["verdict"].edge
                                              - its[1]["verdict"].edge) * 100),
                    2)
                for _ in its[1:]:
                    reject("routed_better_price")
            try:
                _finish_intent(best)
            except Exception as exc:  # noqa: BLE001 — one order, not the sweep
                log.warning("routed intent failed for %s: %s", ident, exc)
                funnel["route_errors"] = funnel.get("route_errors", 0) + 1
        # Feed the 24/7 watcher every proven two-venue identity join — it
        # re-prices these pairs on its own fast clock between sweeps.
        if _XV_WATCH is not None and len(xv_pool) == 2:
            try:
                _XV_WATCH.publish(ev.event_key(), f"{ev.home} v {ev.away}",
                                  ev.league_code, xv_pool)
            except Exception:  # noqa: BLE001 — telemetry, never fatal
                pass
        # Both venues have now contributed their legs for this event — the
        # cross-venue check runs where the whole pool is finally visible.
        if xv_on and xv_done < xv_budget and len(xv_pool) == 2:
            try:
                if _try_cross_venue(ledger=ledger, ev=ev, pool=xv_pool,
                                    min_profit=xv_min_profit,
                                    max_usd=xv_max_usd,
                                    dry_run=not risk.is_live, funnel=funnel):
                    xv_done += 1
            except Exception as exc:  # noqa: BLE001 — additive, never fatal
                log.warning('cross-venue scan failed for %s: %s', ev.home, exc)
                funnel['xv_errors'] = funnel.get('xv_errors', 0) + 1
    mirror = mirror_stats()
    if mirror["sent"] or mirror["queued"] or mirror["dropped"]:
        funnel["mirror"] = mirror
    if reactive:
        funnel["cycle_s"] = round(time.time() - cycle_started, 1)
        # A reactive pass saw a handful of markets. Marking the book from
        # that sample would report a portfolio-wide loss that isn't real, and
        # the watchdog's mapper-confidence ratio would be computed from a
        # slice. Health is a whole-sweep measurement; return the decisions.
        return funnel

    # Cycle health: marks for the circuit breaker (LIVE positions only —
    # paper marks must never halt real trading), watchdog inputs.
    marked_delta = 0.0
    for pos in ledger.open_positions(live_only=True):
        bid = marks.get(pos["market_key"])
        if bid is not None:
            marked_delta += (bid - pos["avg_cost"]) * pos["shares"]
    halted = risk.check_circuit_breaker(marked_delta_usd=marked_delta, now=time.time())

    quiet = {a.name: dict(getattr(a, "book_quiet", {}) or {})
             for a, _ in venue_candidates.values()
             if getattr(a, "book_quiet", None)}
    if quiet:
        funnel["books_quiet"] = quiet
    venue_errors = sum(sum((getattr(a, "book_errors", {}) or {}).values())
                       for a, _ in venue_candidates.values())
    feed_age = time.time() - max((ev.fetched_at for ev in events), default=0)
    skew = getattr(feed_client, "server_clock_skew_s", lambda: None)()
    n_matches = funnel["matched"] or 1
    tripped, wd_reason = risk.watchdog(
        feed_age_s=feed_age if events else 0.0,
        clock_skew_s=skew, venue_errors=venue_errors,
        tradeable_rate=(funnel["tradeable"] / n_matches) if funnel["matched"] else None,
        now=time.time(),
    )
    funnel["marked_delta"] = round(marked_delta, 2)
    funnel["halted"] = halted
    quota = getattr(feed_client, "quota", lambda: {})()
    if quota:
        funnel["feed_quota"] = quota  # odds-API budget, visible every cycle
    parked = getattr(feed_client, "parked_sports", lambda: [])()
    if parked:
        funnel["parked_sports"] = parked[:12]
    # Budget headroom: how much of today's deployment is left, and therefore
    # how many more $1 tickets can still be written today.
    spent_today = risk.day_deployed(now=time.time(),
                                    mode=risk.mode if risk.is_live else "PAPER")
    funnel["budget"] = {
        "bankroll": round(risk.bankroll or 0, 2),
        "day_cap": round(risk.caps.per_day, 2),
        "spent": round(spent_today, 2),
        "fills_left": int(max(risk.caps.per_day - spent_today, 0)
                          / max(risk.caps.per_fill_default, 0.01)),
        "halt_at": round(risk.caps.daily_loss_halt, 2),
    }
    if tripped:
        funnel["watchdog"] = wd_reason
    funnel["candidates"] = {name: len(c) for name, (_, c) in venue_candidates.items()}
    for _, (adapter, _c) in venue_candidates.items():
        census = getattr(adapter, "last_census", None)
        if census:
            funnel["census"] = {**funnel.get("census", {}),
                                **{f"{k}": v for k, v in list(census.items())[:10]}}
        errors = getattr(adapter, "book_errors", None)
        if errors:
            funnel.setdefault("book_errors", {})[adapter.name] = dict(errors)
            adapter.book_errors = {}
        sample = getattr(adapter, "last_book_sample", None)
        if sample:
            funnel.setdefault("book_sample", {})[adapter.name] = sample
            adapter.last_book_sample = None
        stream_stats = getattr(adapter, "stream_stats", None)
        if callable(stream_stats):
            stats = stream_stats()
            if stats:
                funnel.setdefault("stream", {})[adapter.name] = stats
    funnel["cycle_s"] = round(time.time() - cycle_started, 1)
    # The scorecard, from the engine's own books. Reported every cycle so
    # "is this making money" is answerable from telemetry alone, without
    # waiting on a separate service to redeploy.
    try:
        funnel["performance"] = ledger.performance(days=7, live_only=risk.is_live)
        funnel["edge_drift"] = ledger.drift_report(days=7)
        funnel["price_drift"] = ledger.price_drift_report(days=7)
        funnel["by_cat_performance"] = ledger.performance_by_category(
            days=7, live_only=risk.is_live)
        funnel["spread_cost"] = ledger.spread_report(days=7, live_only=risk.is_live)
        funnel["by_band"] = ledger.performance_by_band(days=7, live_only=risk.is_live)
        funnel["reversion"] = rev
        # Profit per dollar of turnover at entry — the steering number.
        # Settled P&L takes thousands of resolutions to speak; this is the
        # same quantity predicted from every fill's own book snapshot and
        # the minute-later drift, and it converges in hundreds.
        funnel["entry_margin"] = ledger.entry_margin(days=7,
                                                     live_only=risk.is_live)
    except Exception as exc:  # noqa: BLE001 — reporting must never break trading
        log.debug("performance snapshot failed: %s", exc)
    funnel["verdict"] = volume_verdict(funnel, risk)
    return funnel


def whale_alignment(condition_id: str, outcome_name: str):
    """Live whale positioning from the SportsAssets platform (top-6 tracker):
    do the measured top traders hold this outcome right now? Fail-soft —
    alignment is a logged feature, never a blocker."""
    base = os.environ.get("EDGE_PLATFORM_API", "")
    if not base:
        return None
    try:
        import requests

        resp = requests.get(f"{base}/api/signal/{condition_id}", timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        same_side, opposed = [], []
        for p in data.get("positions", []):
            entry = {"whale": p.get("username"), "value": p.get("value")}
            if (p.get("outcome") or "").lower() == outcome_name.lower():
                same_side.append(entry)
            else:
                opposed.append(entry)
        return {"same_side": same_side, "opposed": opposed,
                "recent_trades_48h": len(data.get("recent_trades", []))}
    except Exception:  # noqa: BLE001
        return None


_league_probation_cache: dict[str, dict] = {}


def settle_cycle(adapters, ledger) -> int:
    """Resolve open ledger positions from venue settlement endpoints — the
    same accounting that graded $21.45M of reference history."""
    settled = 0
    by_venue: dict[str, list] = {}
    for pos in ledger.open_positions():
        venue, _, outcome_id = pos["market_key"].partition(":")
        by_venue.setdefault(venue, []).append((pos["market_key"], outcome_id))
    for adapter in adapters:
        rows = by_venue.get(adapter.name) or []
        if not rows or not hasattr(adapter, "fetch_results"):
            continue
        try:
            results = adapter.fetch_results([oid for _, oid in rows])
        except Exception as exc:  # noqa: BLE001
            log.warning("%s settlement fetch failed: %s", adapter.name, exc)
            continue
        for mkey, oid in rows:
            if oid in results:
                r = ledger.record_resolution(mkey, results[oid])
                settled += int(r["applied"])
    return settled


def kalshi_open_snapshot(ledger, adapters) -> dict:
    """The live Kalshi book for the public site: every open venue-accepted
    position (ledger claims are venue truth since the fill_count fix).

    Rows whose market the venue reports as past trading but not yet
    resolved carry venue_status (last settle sweep's answer, free — no
    extra venue call), so the card can say "awaiting settlement" instead
    of presenting a finished game as LIVE."""
    k_status: dict = {}
    for a in adapters:
        if getattr(a, "name", "") == "kalshi":
            k_status = getattr(a, "last_market_status", None) or {}
    rows = []
    for p in ledger.open_positions(live_only=True):
        mkey = p.get("market_key") or ""
        if not mkey.startswith("kalshi:"):
            continue
        ticker = mkey.split(":", 1)[1]
        row = {"ticker": ticker,
               "qty": round(float(p["shares"]), 2),
               "avg_cost": round(float(p["avg_cost"]), 4),
               "cost": round(float(p["shares"]) * float(p["avg_cost"]), 2)}
        status = k_status.get(ticker)
        if status and status not in ("initialized", "unopened", "open",
                                     "active"):
            row["venue_status"] = status
        rows.append(row)
    rows = rows[:150]
    return {"n": len(rows),
            "cost": round(sum(r["cost"] for r in rows), 2),
            "rows": rows}


def main() -> None:
    # Crash-visible wrapper. The 2026-08-04 restart loop taught us that a
    # death outside the cycle try/except (boot code, MemoryError, any
    # BaseException) restarts the service with no trace anywhere a probe
    # can read. Whatever escapes _main_impl gets posted before the process
    # dies; a kernel SIGKILL still can't be caught, but then the boot
    # beacon's last phase+rss IS the diagnosis.
    try:
        _main_impl()
    except BaseException as exc:  # noqa: BLE001 — post, then die loudly
        import traceback

        _post_status("crash", {
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "phase": _BEACON.get("phase"),
            "rss_mb": _rss_mb(),
            "trace": traceback.format_exc()[-1200:]})
        raise


def _main_impl() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from datetime import datetime, timezone

    from edge.execution.engine import Policy
    from edge.execution.executor import (
        cancel_orphan_orders,
        reap_pmus_makers,
        reconcile_positions,
        sync_kalshi_fills,
        sync_pmus_fills,
    )
    from edge.execution.risk import RiskManager
    from edge.fairvalue.feed import TheOddsAPIClient
    from edge.ledger.service import Ledger
    from edge.venues.kalshi import KalshiAdapter
    from edge.venues.polymarket_us import PolymarketUSAdapter

    _shadow_log_path().parent.mkdir(parents=True, exist_ok=True)
    policy = Policy.load()
    ledger = Ledger(db_path=os.environ.get(
        "EDGE_LEDGER_DB", str(_data_dir() / "edge_ledger.sqlite3")))
    risk = RiskManager(ledger, policy.risk)

    # LIVE_* refuses to trade unless the go-live checklist exits clean.
    # When the config asks for a live mode but the checklist isn't clean yet,
    # run PAPER and re-check every 30 min — auto-arm on the first clean pass
    # (the human authorization already happened via the config edit; every
    # transition is logged and reported).
    configured_mode = risk.mode
    # Bogus-halt repair: a circuit-breaker halt recorded with ZERO live fills
    # in its window can only have come from paper numbers (fixed bug class) —
    # clear it so a fake loss can never block real trading. Halts backed by
    # actual live fills are untouched and keep their full 72h (no override).
    halt = ledger.get_state("halt_until")
    if halt and time.time() < float(halt.get("until", 0)):
        window_start = float(halt.get("tripped_at", 0)) - 86_400
        live_fills = ledger.live_fill_count_since(window_start)
        live_staked = ledger.live_staked_since(window_start)
        recorded_loss = abs(float(halt.get("day_pnl", 0) or 0))
        # A real loss is bounded by real money staked. If the recorded loss
        # exceeds what was ever put at risk live (or there were no live
        # fills at all), the halt provably came from paper numbers.
        bogus = live_fills == 0 or recorded_loss > live_staked + 0.01
        if bogus:
            ledger.set_state("halt_until", {
                "until": 0, "reason": "cleared",
                "cleared": f"bogus: recorded loss {recorded_loss:.2f} vs live "
                           f"staked {live_staked:.2f} ({live_fills} live fills)"})
            ledger.log_mode(risk.mode, "halt cleared: loss exceeds live money "
                                       "ever staked — paper contamination, not a loss")
            log.warning("cleared bogus circuit-breaker halt: recorded %.2f > "
                        "live staked %.2f", recorded_loss, live_staked)
    if risk.mode != "PAPER":
        from edge.cli import run_checklist

        clean, items = run_checklist(ledger, policy, risk)
        if not clean:
            log.error("check-live NOT clean — %s deferred, running PAPER:\n%s",
                      configured_mode, "\n".join(f"  ✗ {i}" for i in items))
            last_checklist_items = items
            risk.force_paper()
            ledger.log_mode("PAPER", f"deferred {configured_mode}: checklist failed "
                                     f"({'; '.join(items)[:180]})")
        else:
            ledger.log_mode(risk.mode, "checklist clean")
    else:
        ledger.log_mode("PAPER", "startup")

    adapters = []
    if os.environ.get("EDGE_KALSHI", "1") != "0":
        adapters.append(KalshiAdapter())
    if os.environ.get("EDGE_PMUS", "1") != "0":
        try:
            adapters.append(PolymarketUSAdapter())
        except Exception as exc:  # noqa: BLE001 — SDK missing, run kalshi-only
            log.warning("polymarket-us adapter unavailable: %s", exc)
    if os.environ.get("EDGE_GLOBAL_PM", "0") == "1":
        # Optional: the global-CLOB reader, for calibration comparison only
        # (the reference account's venue; we never trade it from the US).
        from edge.venues.polymarket import PolymarketAdapter

        adapters.append(PolymarketAdapter())

    feed = TheOddsAPIClient()
    env_keys = os.environ.get("EDGE_SPORT_KEYS", "")
    sport_keys = ([k for k in env_keys.split(",") if k] if env_keys
                  else feed.resolve_sport_keys(policy))
    cycle_seconds = int(os.environ.get("EDGE_CYCLE_SECONDS", "120"))
    live_venue_names = {v.strip() for v in
                        os.environ.get("EDGE_LIVE_VENUES", "polymarket-us").split(",")
                        if v.strip()}
    log.info("edge runner starting: mode=%s venues=%s, %s sports, %ss cycle",
             risk.mode, [a.name for a in adapters], len(sport_keys), cycle_seconds)

    # Account-link self check: verify venue credentials at startup and surface
    # the result in cycle telemetry (Engine tab) — the operator's confirmation
    # that live keys are valid and the account is reachable. Never logs keys.
    account_link: dict = {}
    for a in adapters:
        if not hasattr(a, "has_credentials"):
            continue
        if not a.has_credentials():
            account_link[a.name] = {"ok": False, "detail": "no credentials set"}
            continue
        try:
            auth = a.check_auth()
            account_link[a.name] = {
                "ok": bool(auth.get("ok")),
                "detail": auth.get("error")
                or (f"balance ${auth['balance_usd']:.2f}" if "balance_usd" in auth
                    else "authenticated"),
            }
        except Exception as exc:  # noqa: BLE001
            account_link[a.name] = {"ok": False,
                                    "detail": f"{type(exc).__name__}: {str(exc)[:120]}"}
        log.info("account link %s: %s", a.name, account_link[a.name])
    _ACCOUNT_LINK.update(account_link)
    _post_status("startup", {"mode": risk.mode, "account_link": account_link,
                             "venues": [a.name for a in adapters],
                             "sports": len(sport_keys),
                             "rss_mb": _rss_mb()})
    threading.Thread(target=_boot_beacon, daemon=True, name="beacon").start()

    # 24/7 cross-venue arbitrage watcher (owner directive 2026-08-04):
    # its own fast clock over every proven Kalshi<->Polymarket identity
    # pair, firing whenever the fee-loaded books sum under $1. Shares the
    # sweep path's once-per-event claims so the two can never double-fire.
    global _XV_WATCH
    xv_cfg_boot = (policy.risk.get("arbitrage") or {}).get("cross_venue") or {}
    if (os.environ.get("EDGE_XV_WATCH", "1") != "0"
            and bool(xv_cfg_boot.get("enabled")) and len(adapters) >= 2):
        from edge.shadow.xv_watch import XVWatch

        _XV_WATCH = XVWatch(
            ledger=ledger, is_live=lambda: risk.is_live,
            min_profit=float(xv_cfg_boot.get("min_profit", 0.02)),
            max_usd=float(xv_cfg_boot.get("max_usd", 10.0)),
            day_usd=float(xv_cfg_boot.get("watch_day_usd", 200.0)),
            poll_s=float(os.environ.get("EDGE_XV_WATCH_S", "3")))
        threading.Thread(target=_XV_WATCH.run, daemon=True,
                         name="xv-watch").start()
        log.warning("xv watch armed: %ss poll, min profit %.3f, day cap $%s",
                    _XV_WATCH.poll_s, _XV_WATCH.min_profit, _XV_WATCH.day_usd)

    # Crypto cross-venue scanner (owner-approved 2026-08-05): Kalshi's
    # 15-minute BTC over/under binaries vs Polymarket US short-interval
    # crypto binaries. MEASUREMENT FIRST — it counts locked dislocations;
    # execution stays behind EDGE_XV_CRYPTO_LIVE=1 (see xv_crypto.py for
    # the index-basis honesty constraint that sets the 3c/set floor).
    global _XV_CRYPTO
    if os.environ.get("EDGE_XV_CRYPTO", "1") != "0":
        xc_kalshi = next((a for a in adapters if a.name == "kalshi"), None)
        xc_pmus = next((a for a in adapters
                        if a.name == "polymarket-us"), None)
        if xc_kalshi is not None and xc_pmus is not None:
            from edge.shadow.xv_crypto import XVCryptoWatch

            _XV_CRYPTO = XVCryptoWatch(ledger=ledger, kalshi=xc_kalshi,
                                       pmus=xc_pmus,
                                       is_live=lambda: risk.is_live)
            threading.Thread(target=_XV_CRYPTO.run, daemon=True,
                             name="xv-crypto").start()
            log.warning(
                "xv crypto armed: %ss poll, min profit %.3f (basis buffer), "
                "live=%s, day cap $%s", _XV_CRYPTO.poll_s,
                _XV_CRYPTO.min_profit,
                os.environ.get("EDGE_XV_CRYPTO_LIVE", "0") == "1",
                _XV_CRYPTO.day_usd)

    # Better-price adds (owner directive 2026-08-04): open PMUS positions
    # that Kalshi currently sells cheaper (fee-loaded, >=2c) get a $1 add,
    # once per position, $50/day class budget, graded as its own category.
    # Adds are software-class, so they ride the strategy switch (owner
    # 2026-08-06: copies + guaranteed arbitrage only).
    if os.environ.get("EDGE_KALSHI_ADDS", "1") != "0" and _strategy_live() \
            and len(adapters) >= 2:
        kalshi_a = next((a for a in adapters if a.name == "kalshi"), None)
        pmus_a = next((a for a in adapters if a.name == "polymarket-us"), None)
        if kalshi_a is not None and pmus_a is not None \
                and kalshi_a.has_credentials() and pmus_a.has_credentials():
            def _kadd_loop() -> None:
                from edge.shadow.kalshi_adds import sweep as kadd_sweep

                time.sleep(240)   # after the first sweep has settled in
                every = float(os.environ.get("EDGE_KADD_EVERY_S", "7200"))
                while True:
                    try:
                        st = kadd_sweep(
                            pmus=pmus_a, kalshi=kalshi_a, ledger=ledger,
                            live=risk.is_live,
                            day_usd=float(os.environ.get(
                                "EDGE_KADD_DAY_USD", "50")))
                        _KADD_STATS.clear()
                        _KADD_STATS.update(st, at=time.time())
                        log.info("kalshi add sweep: %s", st)
                    except Exception as exc:  # noqa: BLE001 — never fatal
                        log.warning("kalshi add sweep failed: %s", exc)
                        _KADD_STATS.clear()
                        _KADD_STATS.update(
                            error=f"{type(exc).__name__}: {str(exc)[:140]}",
                            at=time.time())
                    time.sleep(every)

            threading.Thread(target=_kadd_loop, daemon=True,
                             name="kalshi-adds").start()
            log.warning("kalshi better-price adds armed (2h sweep)")

            # Kalshi COPY leg (owner directive 2026-08-04): the source
            # whales' open positions, expressed on Kalshi wherever it
            # lists the same proposition, at his price +2%. 2-minute
            # cadence (owner 2026-08-05: "need them rolling in"): a copy
            # is only priceable in the first minutes after his entry,
            # before the book moves past his+2% — at 10 minutes the sweep
            # arrived to an 11c gap every pass. The identities snapshot
            # refreshes server-side every 90s, so faster sweeps cost the
            # platform nothing; every price/freshness/cross-game gate is
            # unchanged.
            if os.environ.get("EDGE_KCOPY", "1") != "0":
                def _kcopy_loop() -> None:
                    from edge.shadow.kalshi_copies import sweep as kcopy
                    from edge.shadow.whale_align import fetch as widents

                    time.sleep(60)
                    every = float(os.environ.get("EDGE_KCOPY_EVERY_S", "120"))
                    while True:
                        try:
                            rows = widents(
                                os.environ.get("EDGE_PLATFORM_API", ""),
                                os.environ.get("EDGE_INGEST_TOKEN", ""))
                            # pmus armed alongside kalshi: the sweep
                            # peeks PMUS asks to route each copy to the
                            # best-priced venue (directive 2026-08-05).
                            st = kcopy(kalshi=kalshi_a, ledger=ledger,
                                       identities=rows, live=risk.is_live,
                                       pmus=pmus_a,
                                       day_usd=float(os.environ.get(
                                           "EDGE_KCOPY_DAY_USD", "inf")))
                            _KCOPY_STATS.clear()
                            _KCOPY_STATS.update(st, at=time.time())
                            log.info("kalshi copy sweep: %s", st)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("kalshi copy sweep failed: %s", exc)
                            # A sweep that dies only in service logs is
                            # invisible to every probe — observed 18:28Z
                            # when kalshi_copies vanished from the funnel.
                            _KCOPY_STATS.clear()
                            _KCOPY_STATS.update(
                                error=f"{type(exc).__name__}: "
                                      f"{str(exc)[:140]}",
                                at=time.time())
                        time.sleep(every)

                threading.Thread(target=_kcopy_loop, daemon=True,
                                 name="kalshi-copies").start()
                log.warning("kalshi copy leg armed (10min sweep)")

            # One-shot ~$0.50 connectivity proof (owner order 2026-08-04).
            def _smoke_thread() -> None:
                time.sleep(90)   # after the first books are streaming
                _kalshi_smoke(kalshi_a, ledger, lambda: risk.is_live)

            threading.Thread(target=_smoke_thread, daemon=True,
                             name="kalshi-smoke").start()

    # One-shot venue census (EDGE_CENSUS_DAYS=0 disables): how many sports
    # markets the venue actually listed per day over the trailing window —
    # the opportunity universe behind any volume estimate. Runs in a thread
    # so it never delays the first trading cycle.
    census_days = int(os.environ.get("EDGE_CENSUS_DAYS", "30"))
    if census_days > 0:
        def _run_census() -> None:
            try:
                from polymarket_us import PolymarketUS

                from edge.analysis.venue_census import census

                result = census(PolymarketUS(), days=census_days)
                log.warning("venue census (%sd): %s markets/day, %s liquid/day, "
                            "categories %s", census_days,
                            result["per_day_avg"]["markets"],
                            result["per_day_avg"]["liquid_markets"],
                            result["by_category"])
                ledger.set_state("venue_census", result)
                _post_status("census", {"census": {
                    "days": census_days,
                    "per_day_avg": result["per_day_avg"],
                    "by_category": result["by_category"],
                    "top_leagues": dict(list(result["by_league"].items())[:8]),
                }})
            except Exception as exc:  # noqa: BLE001 — diagnostics must never
                log.warning("venue census failed: %s", exc)  # break trading

        threading.Thread(target=_run_census, daemon=True, name="census").start()

    # Publish the methodology document once at startup as well as nightly.
    # Otherwise a fresh deploy serves whatever the last rollover produced,
    # and a config change (a new blocklist entry, a cap edit) would not reach
    # the document until the next midnight — which is exactly the kind of lag
    # that made the hand-written version untrustworthy.
    def _publish_methodology() -> None:
        try:
            from edge.reporting.export import write_bundle

            out = _data_dir() / "methodology"
            log.info("methodology bundle: %s",
                     write_bundle(ledger, policy, out))
            _post_methodology((out / "METHODOLOGY.md").read_text(),
                              json.loads((out / "figures.json").read_text()))
        except Exception as exc:  # noqa: BLE001 — reporting never breaks
            log.warning("methodology generation failed: %s", exc)  # trading

    threading.Thread(target=_publish_methodology, daemon=True,
                     name="methodology").start()

    # Event-driven reactor: the venue's book stream wakes the loop the moment
    # a subscribed book moves, and only the moved books get re-priced. The
    # full sweep below still runs on its own clock (discovery, health,
    # accounting); the reactor is what closes the gap BETWEEN sweeps.
    reactor = None
    if os.environ.get("EDGE_REACTOR", "1") != "0":
        from edge.shadow.reactor import Reactor

        reactor = Reactor(debounce_s=float(os.environ.get("EDGE_REACT_DEBOUNCE_S", "0.25")))
        hooked = [a.name for a in adapters
                  if hasattr(a, "add_book_listener") and a.add_book_listener(reactor.mark)]
        if hooked:
            log.warning("reactor armed on %s — repricing on book updates", hooked)
        else:
            reactor = None  # no stream to react to; plain polling
            log.info("no streaming venue available — polling only")
    reacted = {"passes": 0, "logged": 0}

    # Away-mode: the phone learns about a stall instead of the owner finding
    # it days later. Only transitions push; a per-cycle heartbeat would get
    # the channel muted, and a muted channel is no channel.
    from edge import notify

    verdict_watcher = notify.VerdictWatcher()
    if notify.enabled():
        notify.push("Edge engine started",
                    f"mode {risk.mode}, {len(sport_keys)} sports, "
                    f"venues {[a.name for a in adapters]}")
        log.info("phone alerts armed (ntfy)")

    last_report_day = ""
    last_recheck = time.time()
    # Fast pricing cycles; discovery + settlement on their own slow clocks
    # (10s cycles must not re-list every venue market or re-poll settlements).
    discovery_s = int(os.environ.get("EDGE_DISCOVERY_SECONDS", "300"))
    settle_s = int(os.environ.get("EDGE_SETTLE_SECONDS", "300"))
    # How long a resting maker order gets to fill before we pull it and
    # re-decide on a fresh book. Short enough that a quote can't sit through
    # a move it no longer likes (the adverse-selection risk of resting).
    maker_ttl_s = float(os.environ.get("EDGE_PMUS_MAKER_TTL_S", "90"))
    candidates: dict = {}
    match_cache: dict = {}
    explored_seen: set = set()
    study_seen: set = set()
    last_checklist_items: list[str] = []
    last_discovery = 0.0
    last_settle = 0.0
    while True:
        try:
            # Deferred live mode: re-run the checklist every 2 min (env
            # EDGE_CHECKLIST_RECHECK_S; floor ~60s — the probes are live API
            # calls); arm on the first clean pass. This clock only governs
            # recovery-to-armed, never trading frequency (that's the cycle).
            recheck_s = max(int(os.environ.get("EDGE_CHECKLIST_RECHECK_S", "120")), 30)
            if configured_mode != risk.mode and time.time() - last_recheck > recheck_s:
                from edge.cli import run_checklist

                last_recheck = time.time()
                clean, items = run_checklist(ledger, policy, risk)
                if clean:
                    risk.set_mode(configured_mode)
                    last_checklist_items = []
                    ledger.log_mode(configured_mode,
                                    "checklist clean — auto-armed per config")
                    log.warning("AUTO-ARMED: %s (checklist clean)", configured_mode)
                else:
                    last_checklist_items = items

            # Size the day budget from the live account, and keep the credit
            # budget solvent across the widened sport list. Both run on the
            # discovery clock — neither changes fast enough to be worth a
            # round-trip every 10s.
            if not candidates or time.time() - last_discovery > discovery_s:
                _BEACON["phase"] = "discovery"
                # Whale-alignment map, same clock: the engine's fills are
                # tagged whale_aligned at decision time so settlement can
                # grade the aligned cohort separately. Measurement only —
                # nothing trades differently on it yet.
                try:
                    from edge.shadow.whale_align import build_map, fetch

                    _WHALE_MAP.clear()
                    _WHALE_MAP.update(build_map(fetch(
                        os.environ.get("EDGE_PLATFORM_API", ""),
                        os.environ.get("EDGE_INGEST_TOKEN", ""))))
                except Exception as exc:  # noqa: BLE001 — tag-only, never fatal
                    log.debug("whale map refresh failed: %s", exc)
                for a in adapters:
                    bp = getattr(a, "buying_power", lambda: None)()
                    if bp is not None and a.name in live_venue_names:
                        risk.set_bankroll(bp)
                feed.rebalance_budget(sport_keys)
                candidates = discover_all(adapters, policy)
                match_cache = {}    # mappings only change with discovery
                explored_seen = set()  # one study record per market per window
                last_discovery = time.time()
            funnel = run_cycle(adapters, feed, policy, risk, ledger, sport_keys,
                               candidates=candidates, match_cache=match_cache,
                               explored_seen=explored_seen, study_seen=study_seen)
            funnel["account_link"] = {k: v["ok"] for k, v in account_link.items()}
            if reactor is not None:
                # Since the last sweep: how many book pushes arrived, how many
                # re-pricings they triggered, how fast we answered them, and
                # what those reactions actually placed.
                funnel["reactor"] = {**reactor.stats(), **reacted}
                reactor.reset_window()
                reacted = {"passes": 0, "logged": 0}
            if configured_mode != risk.mode:
                # Not armed: say WHY, every cycle, on the Engine tab.
                funnel["not_armed"] = {"want": configured_mode,
                                       "blocked_by": last_checklist_items[:6]
                                       or ["awaiting first recheck"]}
                funnel["verdict"] = volume_verdict(funnel, risk)   # outranks
            log.warning("VERDICT: %s", funnel["verdict"])
            verdict_watcher.observe(funnel["verdict"])
            if time.time() - last_settle > settle_s:
                _league_probation_cache.clear()   # graduation without restart
                _LAST_SETTLE.update(
                    settled=settle_cycle(adapters, ledger),
                    stats={a.name: getattr(a, "last_settle_stats", None)
                           for a in adapters},
                    at=time.time())
                last_settle = time.time()
            # The funnel is per-cycle but settlement runs 1-in-10 cycles —
            # without carrying the last pass forward, 90% of heartbeats
            # (and every probe that read them) showed no settle evidence
            # at all. Diagnosis-by-probe needs the LAST pass, always.
            if _LAST_SETTLE:
                funnel["settled"] = _LAST_SETTLE.get("settled")
                funnel["settle_stats"] = _LAST_SETTLE.get("stats")
            # Same carry-forward logic for the account link: the boot-time
            # credential check posts once as "startup" and scrolls away —
            # a probe that reads only recent heartbeats could never answer
            # "is the Kalshi account actually connected?".
            if _ACCOUNT_LINK:
                funnel["account_link"] = _ACCOUNT_LINK
            if _XV_WATCH is not None:
                funnel["xv_watch"] = {**_XV_WATCH.stats,
                                      "registered": _XV_WATCH.registered()}
            if _XV_CRYPTO is not None:
                funnel["xv_crypto"] = dict(_XV_CRYPTO.stats)
            if _KADD_STATS:
                funnel["kalshi_adds"] = dict(_KADD_STATS)
            if _KCOPY_STATS:
                funnel["kalshi_copies"] = dict(_KCOPY_STATS)
            # The venue's own response to our order attempts — the one
            # string that diagnoses a rejected order class remotely.
            smoke_state = (ledger.get_state("kalshi_smoke_done")
                           or ledger.get_state("kalshi_smoke_last"))
            if smoke_state:
                funnel["kalshi_smoke"] = smoke_state
            # The live Kalshi book, published for the public site — with
            # venue statuses from the last settle sweep, so finished-but-
            # unresolved games are flagged instead of shown as LIVE.
            try:
                funnel["kalshi_open"] = kalshi_open_snapshot(ledger, adapters)
            except Exception:  # noqa: BLE001 — telemetry never stalls trading
                pass
            # Account maintenance runs in EVERY mode, not just live ones.
            # It was gated on risk.is_live, which meant the PAPER halt left
            # resting GTC orders alive at the venue with nothing cancelling
            # them — they kept filling through the halt (observed live
            # 2026-08-02: positions grew $271 -> $316 during a PAPER
            # window). A halt that does not also stand down the venue's
            # standing orders is not a halt.
            for a in adapters:
                if not a.has_credentials():
                    continue
                if a.name == "kalshi":
                    if risk.is_live:
                        funnel["kalshi_fill_sync"] = sync_kalshi_fills(a, ledger, risk.mode)
                        from edge.execution.executor import reap_kalshi_makers
                        funnel["kalshi_reap"] = reap_kalshi_makers(ledger,
                                                                   adapter=a)
                elif a.name == "polymarket-us":
                        # Order matters, and getting it wrong cost us four
                        # runaway positions on 2026-08-02.
                        #
                        # ORPHAN SWEEP FIRST: cancel anything resting at the
                        # venue that no ledger context tracks. The contexts
                        # die with the ledger on every deploy, and an
                        # untracked GTC order is a standing instruction to
                        # buy at a stale price forever.
                        funnel["orphans"] = cancel_orphan_orders(a, ledger)
                        # Reconcile SECOND: rebuild what the venue says we own
                        # before anything reads a cap. Caps are enforced
                        # against the ledger, so a ledger missing a position
                        # re-grants that market's full room.
                        #
                        # EVERY CYCLE, not once per process. This was gated on
                        # a per-process flag, which made it a start-up repair
                        # rather than a control. The hole it is meant to
                        # cover — a fill the engine never learned about —
                        # opens continuously, so the cover has to be
                        # continuous too. It is idempotent (fill_uid dedupe)
                        # and bounded by `limit`, so the cost is one venue
                        # call per cycle.
                        funnel["reconciled"] = reconcile_positions(
                            a, ledger, risk.mode)
                        # Then attribute what we can to its decision record.
                        funnel["pmus_fill_sync"] = sync_pmus_fills(a, ledger, risk.mode)
                        # Reap LAST: it decides whether to hand a claim back,
                        # and that decision is only sound once the two steps
                        # above have told the ledger everything the venue
                        # knows.
                        funnel["makers"] = reap_pmus_makers(a, ledger, maker_ttl_s)
            log.info("cycle complete: %s", funnel)
            _post_status("ok", funnel)
            _BEACON["stop"] = True   # first full cycle proves the boot

            # Nightly report on day rollover (build step 7).
            today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            if today != last_report_day:
                # Market keys embed the date, so yesterday's study dedupe
                # entries can never match again — they are pure leak. This
                # set was one of the worker's unbounded growths.
                study_seen.clear()
                if last_report_day:
                    from edge.shadow.report import nightly_report

                    rep = nightly_report(ledger, policy)
                    log.info("nightly report: %s", {k: rep[k] for k in
                                                    ("summary", "alerts") if k in rep})
                    _post_status("report", {"report": {"date": rep.get("date"),
                                                       "alerts": rep.get("alerts", [])}})
                    # Regenerate the methodology document and its evidence
                    # from the ledger. Hand-maintained, it drifted — three
                    # different sample counts for one cohort, a mode line
                    # that outlived the config change. Generated nightly, it
                    # cannot: the numbers are read, never typed.
                    try:
                        from edge.reporting.export import write_bundle

                        out = _data_dir() / "methodology"
                        bundle = write_bundle(ledger, policy, out)
                        log.info("methodology bundle: %s", bundle)
                        _post_status("methodology", {"methodology": bundle})
                        # The worker's disk is not readable by a human, so
                        # publish the document where one can actually open
                        # it. Without this the whole thing is a file nobody
                        # sees.
                        _post_methodology(
                            (out / "METHODOLOGY.md").read_text(),
                            json.loads((out / "figures.json").read_text()))
                    except Exception as exc:  # noqa: BLE001
                        # Reporting must never be able to stop trading.
                        log.warning("methodology generation failed: %s", exc)
                    notify.push(f"Edge engine — daily {last_report_day}",
                                notify.daily_summary(ledger, risk))
                last_report_day = today
        except Exception as exc:  # noqa: BLE001
            log.exception("cycle failed; continuing")
            _post_status("error", {"error": str(exc)[:200]})

        # ── inter-sweep window: react, or sleep it out ──────────────────
        next_sweep = time.time() + cycle_seconds
        if reactor is None:
            time.sleep(max(next_sweep - time.time(), 0.0))
            continue
        while True:
            remaining = next_sweep - time.time()
            if remaining <= 0:
                break
            dirty = reactor.take(timeout=min(remaining, 1.0))
            if not dirty:
                continue
            try:
                rf = run_cycle(adapters, feed, policy, risk, ledger, sport_keys,
                               candidates=candidates, match_cache=match_cache,
                               explored_seen=explored_seen, study_seen=study_seen,
                               only_slugs=dirty)
                reacted["passes"] += 1
                reacted["logged"] += rf.get("logged", 0)
                if rf.get("logged"):
                    # A reaction that traded is news — report it immediately
                    # rather than waiting for the next sweep's status post.
                    log.warning("reactive fill: %s book updates -> %s placed",
                                len(dirty), rf["logged"])
                    _post_status("ok", {**rf, "trigger": "book_update"})
            except Exception:  # noqa: BLE001 — a bad reaction must not end
                log.exception("reactive pass failed; continuing")  # the loop


if __name__ == "__main__":
    main()
