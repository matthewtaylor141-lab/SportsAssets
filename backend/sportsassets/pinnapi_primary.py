"""Atomic Pinnacle h2h source selection from the existing leased WS cache.

No socket, database, venue client, order or policy control lives here. The
collector still proves venue identity, settlement, executable depth and fees.
The other odds provider supplies discovery and genuinely independent books;
its Pinnacle entry is replaced, never counted as a second Pinnacle book.
"""
from __future__ import annotations

from datetime import datetime
import math
import re
import unicodedata

from . import pinnapi_feed as F

PROVIDER = "pinnapi.com/raw-websocket"
LEGACY_PROVIDER = "the-odds-api.com/v4"
VERSION = "PINNAPI_PRIMARY_H2H_V1"
START_TOLERANCE_S = 90 * 60
SPORTS = {"baseball": 6, "soccer": 1}


def epoch(value):
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (float, int)):
            out = float(value)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return None
            out = dt.timestamp()
        return out if math.isfinite(out) else None
    except (ValueError, TypeError, OverflowError):
        return None


def name(value):
    # Full structured names only. No substring/abbreviation/geographic guess.
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", text))


def match_event(cache, event, family):
    sid = SPORTS.get(family)
    start = epoch(event.get("commence_time"))
    home, away = name(event.get("home_team")), name(event.get("away_team"))
    if sid is None:
        return None, "PINNAPI_PRIMARY_SPORT_UNSUPPORTED"
    if start is None or not home or not away or home == away:
        return None, "PINNAPI_PRIMARY_FIXTURE_UNPROVED"
    hits = []
    for eid, meta in cache.events.items():
        if meta.get("sport_id") != sid or meta.get("parentId"):
            continue
        other_start = epoch(meta.get("startTime"))
        if other_start is None or abs(start - other_start) > START_TOLERANCE_S:
            continue
        teams = F.participants(meta)
        h, a = name(teams.get("home")), name(teams.get("away"))
        if h != a and {home, away} == {h, a}:
            hits.append((eid, {"home": event["home_team"] if home == h
                               else event["away_team"],
                               "away": event["away_team"] if away == a
                               else event["home_team"]}))
    if len(hits) != 1:
        return None, ("PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS" if hits
                      else "PINNAPI_PRIMARY_NO_EXACT_FIXTURE")
    return hits[0], None


def book_depth(event, prices, sharp_books, *, at, max_age_s):
    """Distinct, finite, current independent books for EACH outcome.

    PinnAPI and the-odds-api's Pinnacle are ONE book. Repeated payload rows
    cannot satisfy the two-book minimum. Old/unknown second-book timestamps
    do not corroborate a fresh WS price.
    """
    evidence = {n: {"pinnacle"} for n in prices}
    wanted = {name(n): n for n in prices}
    for b in event.get("bookmakers") or []:
        key = b.get("key")
        if key == "pinnacle" or key not in sharp_books:
            continue
        for m in b.get("markets") or []:
            stamp = epoch(m.get("last_update") or b.get("last_update"))
            if m.get("key") != "h2h" or stamp is None:
                continue
            if not 0 <= at - stamp <= max_age_s:
                continue
            for o in m.get("outcomes") or []:
                n = wanted.get(name(o.get("name")))
                try:
                    p = float(o["price"])
                except (KeyError, ValueError, TypeError):
                    continue
                if n is not None and math.isfinite(p) and p > 1:
                    evidence[n].add(key)
    return ({n: len(bs) for n, bs in evidence.items()},
            {n: sorted(bs) for n, bs in evidence.items()})


def select(cache, event, fallback, *, family, sharp_books, at,
           max_age_s=30.0, runtime_id=None, explain=None):
    """Prefer one complete current WS outcome set; otherwise named fallback.

    A fallback keeps its original clocks. It still faces all the collector's
    freshness rules; selecting it never certifies it as currently usable.

    `explain` (a dict, optional) receives the WS refusal reason whenever the
    WS price is not used -- ALSO when there is no fallback and None is
    returned. Without it that reason was discarded and the collector recorded
    the event as NO_PINNACLE_ON_EVENT, as if Pinnacle had no price (P0
    incident: 2,534 rows/day whose cause was a feed refusal -- no exact
    fixture, sport not subscribed, age unknown -- not an absent price).
    """
    def fail(reason, provenance=None):
        if isinstance(explain, dict):
            explain["reason"] = reason
            explain["provenance"] = provenance
        if fallback is None:
            return None
        out = dict(fallback)
        out["reference_input"] = {"version": VERSION,
                                  "provider": LEGACY_PROVIDER,
                                  "preferred_provider": PROVIDER,
                                  "fallback_reason": reason,
                                  "feed_read": provenance}
        return out

    if epoch(at) is None or epoch(max_age_s) is None or max_age_s < 0:
        return fail("PINNAPI_PRIMARY_CLOCK_INVALID")
    if cache is None or not cache.authority.granted or not cache.authority.synced:
        return fail(F.R_NO_AUTHORITY)
    if not isinstance(runtime_id, str) or not runtime_id:
        return fail("PINNAPI_PRIMARY_RUNTIME_UNIDENTIFIED")
    hit, reason = match_event(cache, event, family)
    if reason:
        return fail(reason)
    eid, labels = hit
    got = cache.read(eid, F.FULL_GAME_MONEYLINE_KEY,
                     evaluated_ms=at * 1000, max_age_s=max_age_s)
    if not got.get("ok"):
        return fail(got.get("reason"), got.get("provenance"))
    q = got["quote"]
    if (epoch(q.source_change_ms) is None or epoch(q.received_ms) is None
            or q.received_ms > at * 1000):
        return fail("PINNAPI_PRIMARY_CLOCK_INVALID")
    if (q.market_type != "moneyline" or q.period != 0 or q.alternate
            or q.line is not None or q.sport_id != SPORTS[family]):
        return fail("PINNAPI_PRIMARY_NOT_FULL_GAME_H2H")
    expected = {"home", "away", "draw"} if family == "soccer" else {"home", "away"}
    odds = q.decimal_prices()
    if set(odds) != expected or any(v is None or not math.isfinite(v) or v <= 1
                                   for v in odds.values()):
        return fail("PINNAPI_PRIMARY_INCOMPLETE_OUTCOMES")
    live = cache.events[eid].get("isLive")
    if not isinstance(live, bool) or q.stream != ("live" if live else "prematch"):
        return fail("PINNAPI_PRIMARY_PHASE_UNPROVED")
    labels["draw"] = "Draw"
    prices = {labels[d]: p for d, p in odds.items()}
    depth, evidence = book_depth(event, prices, sharp_books,
                                  at=at, max_age_s=max_age_s)
    prov = dict(got["provenance"], version=VERSION, provider=PROVIDER,
                runtime_id=runtime_id, feed_event_id=eid,
                market_key=q.key, stream=q.stream, raw_odds=dict(q.prices),
                outcome_labels=labels, independent_books=evidence,
                discovery_event_id=event.get("id"), family=family,
                discovery_home=event["home_team"], discovery_away=event["away_team"],
                discovery_start=event["commence_time"])
    return {"prices": prices, "depth": depth,
            "observed_at": q.source_change_ms / 1000,
            "received_at": q.received_ms / 1000,
            "home": event["home_team"], "away": event["away_team"],
            "event_id": event.get("id"),
            "commence_time": event["commence_time"], "reference_input": prov}


def validate(cache, quote, *, at, max_age_s=30.0, runtime_id=None):
    """Recheck after awaited venue/rules reads, before any valuation can act.

    Never silently value old prices after an epoch, fixture or price change.
    Caller must remove actionable probability on failure: paper policies may
    intentionally disregard the collector's other admissibility refusals.
    """
    p = quote.get("reference_input") or {}
    if p.get("provider") != PROVIDER:
        return {"ok": True, "provider": LEGACY_PROVIDER}
    if cache is None or not runtime_id or runtime_id != p.get("runtime_id"):
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    if epoch(at) is None or epoch(max_age_s) is None or max_age_s < 0:
        return {"ok": False, "reason": "PINNAPI_PRIMARY_CLOCK_INVALID"}
    hit, why = match_event(cache, {
        "home_team": p["discovery_home"], "away_team": p["discovery_away"],
        "commence_time": p["discovery_start"]}, p["family"])
    if (why or hit[0] != p["feed_event_id"] or
            any(hit[1][d] != p["outcome_labels"].get(d) for d in ("home", "away"))):
        return {"ok": False, "reason": why or "PINNAPI_PRIMARY_FIXTURE_CHANGED"}
    got = cache.read(p["feed_event_id"], p["market_key"],
                     evaluated_ms=at * 1000, max_age_s=max_age_s)
    if not got.get("ok"):
        return {"ok": False, "reason": got.get("reason"),
                "provenance": got.get("provenance")}
    q = got["quote"]
    if (epoch(q.source_change_ms) is None or epoch(q.received_ms) is None
            or q.received_ms > at * 1000):
        return {"ok": False, "reason": "PINNAPI_PRIMARY_CLOCK_INVALID"}
    live = cache.events[p["feed_event_id"]].get("isLive")
    if (q.epoch != p["epoch"] or q.source_change_ms != p["source_change_ms"]
            or q.prices != p["raw_odds"] or q.stream != p["stream"]
            or not isinstance(live, bool)
            or q.stream != ("live" if live else "prematch")
            or q.market_type != "moneyline" or q.period != 0 or q.alternate
            or q.line is not None or q.sport_id != SPORTS[p["family"]]):
        return {"ok": False, "reason": "PINNAPI_PRIMARY_INPUT_CHANGED"}
    return {"ok": True, "provider": PROVIDER,
            "provenance": got["provenance"],
            "receipt_to_evaluation_ms": at * 1000 - p["received_ms"]}


def stamp_record(rec, quote, check):
    """Provider identity and failure must survive the persisted-row boundary."""
    source = dict(quote.get("reference_input") or {})
    if not source:
        return
    source["decision_check"] = check
    rec.setdefault("settlement_comparison", {})["reference_input"] = source
    if source.get("provider") == PROVIDER:
        rec["provider"] = PROVIDER
        rec["valuation"]["provider"] = PROVIDER
    if not check.get("ok"):
        # A mere extra_refusal is insufficient: paper benchmark intentionally
        # reads probabilities even where other collector gates have refused.
        rec["probability"] = rec["probability_of_selection"] = None
        rec["valuation"]["probability"] = None
        rec["admissible"] = False
        rec["decision"] = "NO_TRADE"
        reason = check["reason"]
        rec["refusals"] = list(dict.fromkeys([reason] + list(rec.get("refusals") or [])))
        rec["why"] = reason + "; " + str(rec.get("why") or "")
