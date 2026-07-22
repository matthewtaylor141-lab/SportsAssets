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
import time
from pathlib import Path

log = logging.getLogger(__name__)

SHADOW_LOG = Path(__file__).resolve().parents[3] / "data" / "shadow_fills.jsonl"


def log_shadow_fill(intent, book, feed_snapshot, would_fill: bool, whale_alignment=None):
    rec = {
        "ts": time.time(),
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
    with open(SHADOW_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    _record_to_platform(rec)


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
                      json={"status": status, "detail": detail},
                      headers={"X-Engine-Token": token}, timeout=5)
    except Exception as exc:  # noqa: BLE001
        log.debug("status post failed (non-fatal): %s", exc)


def _record_to_platform(rec: dict) -> None:
    """Mirror the shadow fill into the platform's internal database
    (POST /api/engine/fills) so the Engine tab shows it. Fail-soft: the
    JSONL log remains the grader's source of truth."""
    base = os.environ.get("EDGE_PLATFORM_API", "")
    token = os.environ.get("EDGE_INGEST_TOKEN", "")
    if not base or not token:
        return
    try:
        import requests

        requests.post(
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
            headers={"X-Engine-Token": token},
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("platform mirror failed (non-fatal): %s", exc)


# ── The decision loop (orders replaced by logging) ─────────────────────


def run_cycle(adapters, feed_client, policy, exposure, sport_keys: list[str]) -> int:
    """One sweep across all venues: feed → map → de-vig → book → decide → log.
    Returns fills logged. Both venues are judged against the SAME fair values
    (dual-venue shadow is the spec's venue-choice experiment)."""
    from edge.execution.engine import decide
    from edge.fairvalue.devig import fair_value
    from edge.venues.base import FillIntent
    from edge.venues.mapper import match_event

    league_codes = set()
    for group in (policy.leagues.get("allowlist") or {}).values():
        league_codes.update(group)
    venue_candidates = {}
    for adapter in adapters:
        try:
            venue_candidates[adapter.name] = (adapter, adapter.discover_markets(league_codes))
            log.info("%s: %s candidate markets", adapter.name,
                     len(venue_candidates[adapter.name][1]))
        except Exception as exc:  # noqa: BLE001
            log.warning("%s discovery failed: %s", adapter.name, exc)

    # Fetch feed once; evaluate every venue against it.
    events = []
    for sport_key in sport_keys:
        try:
            events.extend(feed_client.fetch_events(sport_key))
        except Exception as exc:  # noqa: BLE001 — one sport must not kill the sweep
            log.warning("feed fetch failed for %s: %s", sport_key, exc)

    funnel = {"feed_events": len(events), "matched": 0, "books_checked": 0,
              "logged": 0, "rejects": {}}
    logged = 0
    for ev in events:
        if len(ev.h2h) < 2:
            continue
        names = list(ev.h2h)
        fairs = dict(zip(names, fair_value([ev.h2h[n] for n in names])))
        for adapter, candidates in venue_candidates.values():
            match = match_event(ev.home, ev.away, ev.league_code, candidates)
            if match is None:
                continue
            funnel["matched"] += 1
            for side_name, oc_name in (
                (ev.home, match.home_outcome), (ev.away, match.away_outcome)
            ):
                if oc_name is None:
                    continue
                fair = next((f for n, f in fairs.items() if n.lower() in side_name.lower()
                             or side_name.lower() in n.lower()), None)
                if fair is None:
                    continue
                token = match.market.outcome_tokens[oc_name]
                book = adapter.get_book(match.market.market_id, token)
                if book is None or not book.asks:
                    funnel["rejects"]["no_book"] = funnel["rejects"].get("no_book", 0) + 1
                    continue
                funnel["books_checked"] += 1
                ask = book.asks[0]
                decision = decide(policy, exposure, match.market.market_id,
                                  ev.league_code, ask.price, fair,
                                  venue_fee=adapter.taker_fee(ask.price))
                if not decision.trade:
                    bucket = decision.reason.split()[0]
                    funnel["rejects"][bucket] = funnel["rejects"].get(bucket, 0) + 1
                    continue
                intent = FillIntent(
                    market_id=match.market.market_id, outcome_id=token,
                    limit_price=ask.price, size_usd=decision.size_usd,
                    fair_value=round(fair, 4), edge=round(fair - ask.price, 4),
                    league=ev.league_code, band=decision.band,
                )
                would_fill = ask.size * ask.price >= decision.size_usd
                alignment = whale_alignment(match.market.market_id, oc_name) \
                    if adapter.name == "polymarket" else None
                log_shadow_fill(intent, book, {"h2h": ev.h2h, "home": ev.home, "away": ev.away},
                                would_fill, whale_alignment=alignment)
                exposure.add(match.market.market_id, decision.size_usd)
                logged += 1
                funnel["logged"] = logged
    funnel["candidates"] = {name: len(c) for name, (_, c) in venue_candidates.items()}
    for _, (adapter, _c) in venue_candidates.items():
        census = getattr(adapter, "last_census", None)
        if census:
            funnel[f"{adapter.name}_league_census"] = census
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from edge.execution.engine import ExposureBook, Policy
    from edge.fairvalue.feed import SPORT_KEY_LEAGUE, TheOddsAPIClient
    from edge.venues.kalshi import KalshiAdapter
    from edge.venues.polymarket import PolymarketAdapter

    SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
    policy = Policy.load()
    adapters = [PolymarketAdapter()]
    if os.environ.get("EDGE_KALSHI", "1") != "0":
        adapters.append(KalshiAdapter())
    feed = TheOddsAPIClient()
    env_keys = os.environ.get("EDGE_SPORT_KEYS", "")
    sport_keys = [k for k in env_keys.split(",") if k] if env_keys else feed.resolve_sport_keys()
    cycle_seconds = int(os.environ.get("EDGE_CYCLE_SECONDS", "120"))
    log.info("shadow runner starting: venues=%s, %s sports, %ss cycle — NO ORDERS, logging only",
             [a.name for a in adapters], len(sport_keys), cycle_seconds)
    while True:
        exposure = ExposureBook()  # caps reset per cycle-day granularity v1
        try:
            funnel = run_cycle(adapters, feed, policy, exposure, sport_keys)
            log.info("cycle complete: %s", funnel)
            _post_status("ok", funnel)
        except Exception as exc:  # noqa: BLE001
            log.exception("cycle failed; continuing")
            _post_status("error", {"error": str(exc)[:200]})
        time.sleep(cycle_seconds)


if __name__ == "__main__":
    main()
