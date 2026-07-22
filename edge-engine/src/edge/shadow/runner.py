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


def log_shadow_fill(intent, book, feed_snapshot, would_fill: bool):
    rec = {
        "ts": time.time(),
        "venue": book.venue,
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


# ── The decision loop (orders replaced by logging) ─────────────────────


def run_cycle(adapter, feed_client, policy, exposure, sport_keys: list[str]) -> int:
    """One sweep: feed → map → de-vig → book → decide → log. Returns fills logged."""
    from edge.execution.engine import decide
    from edge.fairvalue.devig import fair_value
    from edge.venues.base import FillIntent
    from edge.venues.mapper import match_event

    league_codes = set()
    for group in (policy.leagues.get("allowlist") or {}).values():
        league_codes.update(group)
    candidates = adapter.discover_markets(league_codes)
    log.info("discovered %s venue markets across allowlist leagues", len(candidates))

    logged = 0
    for sport_key in sport_keys:
        try:
            events = feed_client.fetch_events(sport_key)
        except Exception as exc:  # noqa: BLE001 — one sport must not kill the sweep
            log.warning("feed fetch failed for %s: %s", sport_key, exc)
            continue
        for ev in events:
            match = match_event(ev.home, ev.away, ev.league_code, candidates)
            if match is None or len(ev.h2h) < 2:
                continue
            names = list(ev.h2h)
            fairs = dict(zip(names, fair_value([ev.h2h[n] for n in names])))
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
                    continue
                ask = book.asks[0]
                decision = decide(policy, exposure, match.market.market_id,
                                  ev.league_code, ask.price, fair,
                                  venue_fee=adapter.taker_fee(ask.price))
                if not decision.trade:
                    continue
                intent = FillIntent(
                    market_id=match.market.market_id, outcome_id=token,
                    limit_price=ask.price, size_usd=decision.size_usd,
                    fair_value=round(fair, 4), edge=round(fair - ask.price, 4),
                    league=ev.league_code, band=decision.band,
                )
                would_fill = ask.size * ask.price >= decision.size_usd
                log_shadow_fill(intent, book, {"h2h": ev.h2h, "home": ev.home, "away": ev.away},
                                would_fill)
                exposure.add(match.market.market_id, decision.size_usd)
                logged += 1
    return logged


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from edge.execution.engine import ExposureBook, Policy
    from edge.fairvalue.feed import SPORT_KEY_LEAGUE, TheOddsAPIClient
    from edge.venues.polymarket import PolymarketAdapter

    SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
    policy = Policy.load()
    adapter = PolymarketAdapter()
    feed = TheOddsAPIClient()
    sport_keys = [k for k in os.environ.get(
        "EDGE_SPORT_KEYS", ",".join(SPORT_KEY_LEAGUE)).split(",") if k]
    cycle_seconds = int(os.environ.get("EDGE_CYCLE_SECONDS", "120"))
    log.info("shadow runner starting: %s sports, %ss cycle — NO ORDERS, logging only",
             len(sport_keys), cycle_seconds)
    while True:
        exposure = ExposureBook()  # caps reset per cycle-day granularity v1
        try:
            n = run_cycle(adapter, feed, policy, exposure, sport_keys)
            log.info("cycle complete: %s shadow fills logged", n)
        except Exception:  # noqa: BLE001
            log.exception("cycle failed; continuing")
        time.sleep(cycle_seconds)


if __name__ == "__main__":
    main()
