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
    with open(_shadow_log_path(), "a") as f:
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


# ── The decision loop (mode-aware; orders only via the executor) ────────


def run_cycle(adapters, feed_client, policy, risk, ledger, sport_keys: list[str]) -> dict:
    """One sweep across all venues: feed → map (0.95 gate) → de-vig → book →
    strategy filter → risk approve → execute (paper-log or place, by mode).
    Both venues are judged against the SAME fair values."""
    from datetime import datetime, timezone

    from edge.execution.engine import strategy_filter
    from edge.execution.executor import build_decision_record, execute, market_key
    from edge.fairvalue.devig import fair_value
    from edge.fairvalue.lines import outcome_matches, pair_quotes, parse_outcome_line
    from edge.venues.base import FillIntent
    from edge.venues.mapper import match_events_all, team_score

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
    funnel = {"mode": risk.mode, "feed_events": len(events), "matched": 0,
              "tradeable": 0, "books_checked": 0, "logged": 0, "rejects": {}}
    if risk.is_live:
        funnel["live_venues"] = sorted(live_venues)
    marks: dict[str, float] = {}   # market_key -> best bid (circuit-breaker marks)

    def reject(bucket: str) -> None:
        funnel["rejects"][bucket] = funnel["rejects"].get(bucket, 0) + 1

    for ev in events:
        if len(ev.h2h) < 2:
            continue
        names = list(ev.h2h)
        try:
            fairs = dict(zip(names, fair_value([ev.h2h[n] for n in names])))
            # Derivative sides: de-vig each two-sided line at its exact point.
            deriv_sides: list[tuple] = []  # (ParsedLine, fair)
            for kind, quotes in (("total", ev.totals), ("spread", ev.spreads)):
                for pq in pair_quotes(quotes, kind):
                    fa, fb = fair_value([pq.a_odds, pq.b_odds])
                    deriv_sides.append((pq.a_parsed, fa))
                    deriv_sides.append((pq.b_parsed, fb))
        except Exception as exc:  # noqa: BLE001 — one pathological odds set
            log.warning("fair value failed for %s vs %s (%s): %s",
                        ev.home, ev.away, ev.h2h, exc)
            reject("fair_error")
            continue

        def fair_for(oc_name: str):
            """(fair, category) for a canonical venue outcome, or None."""
            p = parse_outcome_line(oc_name)
            if p.kind in ("total", "spread"):
                for side, f in deriv_sides:
                    if side.kind == p.kind and outcome_matches(oc_name, side):
                        return f, p.kind
                return None
            for team_name, f in fairs.items():
                if team_score(team_name, oc_name) >= 0.95:
                    return f, "moneyline"
            return None

        for adapter, candidates in venue_candidates.values():
            matches = match_events_all(ev.home, ev.away, ev.league_code, candidates)
            best = matches[0] if matches else None
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
            for match in matches:
                if not match.tradeable:
                    continue
                for oc_name, token in match.market.outcome_tokens.items():
                    if token in seen_tokens:
                        continue
                    seen_tokens.add(token)
                    fv = fair_for(oc_name)
                    if fv is None:
                        continue
                    fair, category = fv
                    if not ev.is_fresh(30, now=time.time()):  # hard rule: fresh only
                        reject("stale_quote")
                        continue
                    book = adapter.get_book(match.market.market_id, token)
                    if book is None or not book.asks:
                        reject("no_book")
                        continue
                    funnel["books_checked"] += 1
                    ask = book.asks[0]
                    mkey = market_key(adapter.name, token)
                    if book.bids:
                        marks[mkey] = book.bids[0].price
                    effective_mode = risk.mode if (
                        risk.is_live and adapter.name in live_venues) else "PAPER"
                    verdict = strategy_filter(policy, ev.league_code, ask.price, fair,
                                              venue_fee=adapter.taker_fee(ask.price),
                                              category=category)
                    if not verdict.ok:
                        reject(verdict.reason.split()[0])
                        continue
                    if effective_mode != "PAPER" and \
                            policy.league_allowed(ev.league_code) != "allow":
                        # Hard rule: real money only on MEASURED leagues;
                        # shadow-only leagues keep paper-logging.
                        effective_mode = "PAPER"
                    approved, why = risk.approve(adapter.name, mkey, ev.event_key(),
                                                 requested_usd=1e9, now=now,
                                                 mode=effective_mode)
                    if approved <= 0:
                        reject(why.split(":")[0].split()[0])
                        continue
                    decision = build_decision_record(
                        fair=fair, edge=verdict.edge, threshold=verdict.threshold,
                        band=verdict.band, book=book,
                        feed_snapshot={"h2h": ev.h2h, "home": ev.home,
                                       "away": ev.away, "fetched_at": ev.fetched_at},
                        approved_usd=approved, guard_reason=why,
                    )
                    decision["category"] = category
                    decision["outcome"] = oc_name
                    result = execute(adapter=adapter, ledger=ledger, mode=effective_mode,
                                     mkey=mkey, league=ev.league_code,
                                     ask_price=ask.price, ask_size=ask.size,
                                     size_usd=approved, edge=verdict.edge,
                                     threshold=verdict.threshold, decision=decision,
                                     ts=time.time())
                    funnel["logged"] += int(result["placed"])
                    funnel.setdefault("by_category", {}).setdefault(category, 0)
                    funnel["by_category"][category] += int(result["placed"])
                    if not result["placed"]:
                        reject(result["status"].split(":")[0])
                    # Legacy shadow JSONL + platform mirror (grader history).
                    intent = FillIntent(
                        market_id=match.market.market_id, outcome_id=token,
                        limit_price=ask.price, size_usd=approved,
                        fair_value=round(fair, 4), edge=round(fair - ask.price, 4),
                        league=ev.league_code, band=verdict.band,
                    )
                    would_fill = ask.size * ask.price >= approved
                    log_shadow_fill(intent, book,
                                    {"h2h": ev.h2h, "home": ev.home, "away": ev.away},
                                    would_fill, whale_alignment=None)

    # Cycle health: marks for the circuit breaker, watchdog inputs.
    marked_delta = 0.0
    for pos in ledger.open_positions():
        bid = marks.get(pos["market_key"])
        if bid is not None:
            marked_delta += (bid - pos["avg_cost"]) * pos["shares"]
    halted = risk.check_circuit_breaker(marked_delta_usd=marked_delta, now=time.time())

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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from datetime import datetime, timezone

    from edge.execution.engine import Policy
    from edge.execution.executor import sync_kalshi_fills
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
    if risk.mode != "PAPER":
        from edge.cli import run_checklist

        clean, items = run_checklist(ledger, policy, risk)
        if not clean:
            log.error("check-live NOT clean — %s deferred, running PAPER:\n%s",
                      configured_mode, "\n".join(f"  ✗ {i}" for i in items))
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
    sport_keys = [k for k in env_keys.split(",") if k] if env_keys else feed.resolve_sport_keys()
    cycle_seconds = int(os.environ.get("EDGE_CYCLE_SECONDS", "120"))
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
    _post_status("startup", {"mode": risk.mode, "account_link": account_link,
                             "venues": [a.name for a in adapters],
                             "sports": len(sport_keys)})

    last_report_day = ""
    last_recheck = time.time()
    while True:
        try:
            # Deferred live mode: re-run the checklist every 30 min; arm on
            # the first clean pass.
            if configured_mode != risk.mode and time.time() - last_recheck > 1800:
                from edge.cli import run_checklist

                last_recheck = time.time()
                clean, items = run_checklist(ledger, policy, risk)
                if clean:
                    risk.set_mode(configured_mode)
                    ledger.log_mode(configured_mode,
                                    "checklist clean — auto-armed per config")
                    log.warning("AUTO-ARMED: %s (checklist clean)", configured_mode)
                    _post_status("mode", {"mode": configured_mode,
                                          "note": "auto-armed after clean checklist"})
                else:
                    _post_status("checklist_pending", {"unchecked": items[:8]})

            funnel = run_cycle(adapters, feed, policy, risk, ledger, sport_keys)
            funnel["account_link"] = {k: v["ok"] for k, v in account_link.items()}
            funnel["settled"] = settle_cycle(adapters, ledger)
            if risk.is_live:
                for a in adapters:
                    if a.name == "kalshi" and a.has_credentials():
                        funnel["kalshi_fill_sync"] = sync_kalshi_fills(a, ledger, risk.mode)
            log.info("cycle complete: %s", funnel)
            _post_status("ok", funnel)

            # Nightly report on day rollover (build step 7).
            today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            if today != last_report_day:
                if last_report_day:
                    from edge.shadow.report import nightly_report

                    rep = nightly_report(ledger, policy)
                    log.info("nightly report: %s", {k: rep[k] for k in
                                                    ("summary", "alerts") if k in rep})
                    _post_status("report", {"report": {"date": rep.get("date"),
                                                       "alerts": rep.get("alerts", [])}})
                last_report_day = today
        except Exception as exc:  # noqa: BLE001
            log.exception("cycle failed; continuing")
            _post_status("error", {"error": str(exc)[:200]})
        time.sleep(cycle_seconds)


if __name__ == "__main__":
    main()
