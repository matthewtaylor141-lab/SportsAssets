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
import threading
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
    from edge.fairvalue.devig import fair_value
    from edge.fairvalue.lines import outcome_matches, pair_quotes, parse_outcome_line
    from edge.venues.base import FillIntent
    from edge.venues.mapper import match_events_all, team_score

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
    funnel = {"mode": risk.mode, "feed_events": len(events), "matched": 0,
              "tradeable": 0, "books_checked": 0, "logged": 0, "rejects": {}}
    if reactive:
        funnel["reactive"] = len(only_slugs)
    if risk.is_live:
        funnel["live_venues"] = sorted(live_venues)
    marks: dict[str, float] = {}   # market_key -> best bid (circuit-breaker marks)
    # Slices whose prices systematically disagree with the venue's own —
    # barred from trading until the disagreement clears, whatever caused it.
    quarantined = _quarantined(ledger)
    if quarantined:
        funnel["quarantined"] = sorted("/".join(q) for q in quarantined)[:8]

    def reject(bucket: str) -> None:
        funnel["rejects"][bucket] = funnel["rejects"].get(bucket, 0) + 1

    refreshed_sports: set[str] = set()
    for ev in events:
        # Long cycles age early-fetched quotes past the 30s freshness rule
        # before late events are processed. Refresh a sport AT MOST ONCE per
        # cycle (quota discipline — the per-25s TTL alone burned the odds
        # budget); events still stale after one refresh are dropped by the
        # 30s hard rule rather than re-fetched.
        if not ev.is_fresh(25, now=time.time()) and ev.sport_key not in refreshed_sports:
            refreshed_sports.add(ev.sport_key)
            try:
                for fresh_ev in feed_client.fetch_events(ev.sport_key):
                    if fresh_ev.home == ev.home and fresh_ev.away == ev.away:
                        ev.h2h, ev.totals, ev.spreads = (
                            fresh_ev.h2h, fresh_ev.totals, fresh_ev.spreads)
                        ev.fetched_at = fresh_ev.fetched_at
                        break
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
                priced["fairs"] = dict(
                    zip(names, fair_value([ev.h2h[n] for n in names])))
                sides: list[tuple] = []  # (ParsedLine, fair)
                for kind, quotes in (("total", ev.totals), ("spread", ev.spreads)):
                    for pq in pair_quotes(quotes, kind):
                        fa, fb = fair_value([pq.a_odds, pq.b_odds])
                        sides.append((pq.a_parsed, fa))
                        sides.append((pq.b_parsed, fb))
                priced["deriv_sides"] = sides
                priced["ok"] = True
            except Exception as exc:  # noqa: BLE001 — one pathological odds set
                log.warning("fair value failed for %s vs %s (%s): %s",
                            ev.home, ev.away, ev.h2h, exc)
                priced["ok"] = False
            return priced["ok"]

        def fair_for(oc_name: str):
            """(fair, category, reason) — reason names WHY no fair value was
            found, so the funnel can distinguish 'venue lists a line our
            sharp book doesn't quote' from 'we couldn't identify the side'."""
            if not _price_event():
                return None, "moneyline", {"reason": "fair_error"}
            fairs, deriv_sides = priced["fairs"], priced["deriv_sides"]
            p = parse_outcome_line(oc_name)
            if p.kind in ("total", "spread"):
                for side, f in deriv_sides:
                    if side.kind == p.kind and outcome_matches(oc_name, side):
                        return f, p.kind, None
                have = sorted({s.point for s, _ in deriv_sides
                               if s.kind == p.kind and s.point is not None})
                return None, p.kind, {
                    "reason": f"no_sharp_quote_{p.kind}",
                    "venue_line": p.point, "sharp_lines": have[:8],
                }
            for team_name, f in fairs.items():
                if team_score(team_name, oc_name) >= 0.95:
                    return f, "moneyline", None
            return None, "moneyline", {"reason": "no_side_match_moneyline",
                                       "outcome": oc_name[:40]}

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
                        ex = funnel.setdefault("unpriced_examples", {})
                        if miss["reason"] not in ex:
                            ex[miss["reason"]] = miss
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
                    mkey = market_key(adapter.name, token)
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
                        risk.is_live and adapter.name in live_venues) else "PAPER"
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
                    verdict = strategy_filter(policy, ev.league_code, entry_px, fair,
                                              venue_fee=fee, category=category)

                    # ── STUDY RECORD ──────────────────────────────────
                    # Every priced outcome is observed, whether or not it
                    # clears the trading bar. The narrow rules protect
                    # CAPITAL; they must never decide what we get to LEARN.
                    # Sampled once per market per hour so the record tracks
                    # price evolution without flooding.
                    if study_seen is not None:
                        sbucket = f"{mkey}:{int(time.time() // 3600)}"
                        if sbucket not in study_seen:
                            study_seen.add(sbucket)
                            funnel["studied"] = funnel.get("studied", 0) + 1
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
                    approved, why = risk.approve(adapter.name, mkey, ev.event_key(),
                                                 requested_usd=1e9, now=now,
                                                 mode=effective_mode)
                    if approved <= 0:
                        reject(why.split(":")[0].split()[0])
                        continue
                    claim = risk.claim_key(effective_mode, adapter.name, mkey,
                                           ev.event_key())
                    decision = build_decision_record(
                        fair=fair, edge=verdict.edge, threshold=verdict.threshold,
                        band=verdict.band, book=book,
                        feed_snapshot={"h2h": ev.h2h, "home": ev.home,
                                       "away": ev.away, "fetched_at": ev.fetched_at},
                        approved_usd=approved, guard_reason=why,
                    )
                    decision["category"] = category
                    decision["outcome"] = oc_name
                    # Maker vs taker entry, recorded per fill so the nightly
                    # report can measure whether resting actually paid.
                    decision["entry"] = {"price": entry_px, "taker": taker,
                                         "ask": ask.price}
                    # Mapping provenance — makes 'why this fair value?'
                    # answerable from the record alone.
                    decision["venue_market"] = {"title": match.market.title,
                                                "id": match.market.market_id,
                                                "match_score": round(match.score, 3)}
                    result = execute(adapter=adapter, ledger=ledger, mode=effective_mode,
                                     mkey=mkey, league=ev.league_code,
                                     ask_price=ask.price, ask_size=ask.size,
                                     size_usd=approved, edge=verdict.edge,
                                     threshold=verdict.threshold, decision=decision,
                                     ts=time.time(), entry_price=entry_px,
                                     taker=taker, event_key=claim)
                    funnel["logged"] += int(result["placed"])
                    funnel.setdefault("by_category", {}).setdefault(category, 0)
                    funnel["by_category"][category] += int(result["placed"])
                    if not result["placed"]:
                        reject(result["status"].split(":")[0])
                    # Legacy shadow JSONL + platform mirror (grader history).
                    intent = FillIntent(
                        market_id=match.market.market_id, outcome_id=token,
                        limit_price=entry_px, size_usd=approved,
                        fair_value=round(fair, 4), edge=round(fair - entry_px, 4),
                        league=ev.league_code, band=verdict.band,
                    )
                    would_fill = ask.size * ask.price >= approved
                    log_shadow_fill(intent, book,
                                    {"h2h": ev.h2h, "home": ev.home, "away": ev.away},
                                    would_fill, whale_alignment=None)

    if reactive:
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
    from edge.execution.executor import (
        reap_pmus_makers,
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
    _post_status("startup", {"mode": risk.mode, "account_link": account_link,
                             "venues": [a.name for a in adapters],
                             "sports": len(sport_keys)})

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
            if time.time() - last_settle > settle_s:
                funnel["settled"] = settle_cycle(adapters, ledger)
                last_settle = time.time()
            if risk.is_live:
                for a in adapters:
                    if not a.has_credentials():
                        continue
                    if a.name == "kalshi":
                        funnel["kalshi_fill_sync"] = sync_kalshi_fills(a, ledger, risk.mode)
                    elif a.name == "polymarket-us":
                        # Fills FIRST, then reap: the reaper only returns an
                        # event claim when the market holds no position, so
                        # it must see this cycle's fills before deciding.
                        funnel["pmus_fill_sync"] = sync_pmus_fills(a, ledger, risk.mode)
                        funnel["makers"] = reap_pmus_makers(a, ledger, maker_ttl_s)
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
