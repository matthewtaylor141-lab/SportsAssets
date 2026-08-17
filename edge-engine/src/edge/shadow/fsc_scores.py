"""Set-score verification for the FSC sleeve (owner 2026-08-17 night:
"How can we improve so that we dont miss anything when a favorite loses
the first set, can there be verification from one of our apis").

YES: the engine's own licensed odds feed (TheOddsAPI, the key already
in EDGE_ODDS_API_KEY) serves /v4/sports/{key}/scores for live tennis —
per-player SET counts with a last_update stamp. This module polls it on
a small, quota-guarded budget and gives the sleeve ground truth the
price path could only infer:

  - CONFIRM: a favorite at 0-1 in sets has lost the first set, fact —
    entry fires immediately, no second-sweep wait.
  - CATCH: a favorite the price path missed (never dipped into the
    band, gapped straight through it) still enters off the score.
  - VETO: a price collapse while the score is 0-0 is NOT a set loss
    (injury news, mid-set retirement risk) — money holds while a fresh
    score contradicts the price.

Coverage is the main tours the feed carries (ATP/WTA events); Kalshi's
challenger/ITF boards are not in the feed, so those matches keep the
price-only trigger unchanged. Fail-soft everywhere: no key, quota
floor, HTTP error, or unknown shape -> no rows plus a stats dict that
says why, and the sleeve behaves exactly as before this module existed.
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

BASE = "https://api.the-odds-api.com/v4"
EVERY_S = float(os.environ.get("EDGE_FSC_SCORES_EVERY_S", "120"))
# Never spend the feed below this remaining-credit floor — the fair
# value engine's own reserve philosophy (stale beats blind).
RESERVE = float(os.environ.get("EDGE_FSC_SCORES_RESERVE", "500"))
# A score older than this no longer confirms or vetoes anything.
FRESH_S = float(os.environ.get("EDGE_FSC_SCORES_FRESH_S", "600"))
_SPORTS_TTL_S = 3600.0

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "rows": [], "stats": {}}
_sports: dict = {"at": 0.0, "keys": []}
_quota: dict = {"remaining": None}


def _get(sess, path: str, params: dict) -> list | None:
    import requests

    try:
        r = sess.get(f"{BASE}{path}", params=params, timeout=15)
        rem = r.headers.get("x-requests-remaining")
        if rem is not None:
            try:
                _quota["remaining"] = float(rem)
            except ValueError:
                pass
        if r.status_code != 200:
            return None
        out = r.json()
        return out if isinstance(out, list) else None
    except requests.RequestException:
        return None


def _tennis_keys(sess, key: str, now: float) -> list[str]:
    if now - _sports["at"] < _SPORTS_TTL_S and _sports["keys"]:
        return _sports["keys"]
    rows = _get(sess, "/sports", {"apiKey": key}) or []
    keys = [r.get("key") for r in rows
            if str(r.get("key") or "").startswith("tennis")
            and r.get("active")]
    if keys:
        _sports.update(at=now, keys=keys)
    return keys


def poll(active_hint: bool = True) -> tuple[list[dict], dict]:
    """Cached score rows for live tennis, refreshed at most every
    EVERY_S. Row shape: {names: [a, b], sets: {name: int}, completed,
    last_update_ts}. Returns (rows, stats)."""
    import requests

    now = time.time()
    with _lock:
        if now - _cache["at"] < EVERY_S:
            return _cache["rows"], _cache["stats"]
        _cache["at"] = now      # even failures hold the cadence

    api_key = os.environ.get("EDGE_ODDS_API_KEY", "")
    stats: dict = {}
    rows: list[dict] = []
    if os.environ.get("EDGE_FSC_SCORES", "1") == "0":
        stats["disabled"] = True
    elif not api_key:
        stats["no_key"] = True
    elif not active_hint:
        stats["idle"] = True
    elif _quota["remaining"] is not None and _quota["remaining"] < RESERVE:
        stats["quota_hold"] = _quota["remaining"]
    else:
        sess = requests.Session()
        keys = _tennis_keys(sess, api_key, now)
        stats["sports"] = len(keys)
        for k in keys:
            batch = _get(sess, f"/sports/{k}/scores",
                         {"apiKey": api_key, "daysFrom": 1})
            if batch is None:
                stats["errors"] = stats.get("errors", 0) + 1
                continue
            for ev in batch:
                sc = ev.get("scores")
                if not sc:
                    continue        # not started / no data
                sets: dict = {}
                for side in sc:
                    try:
                        sets[str(side.get("name") or "").strip()] = \
                            int(float(side.get("score")))
                    except (TypeError, ValueError):
                        pass
                if len(sets) != 2:
                    stats["odd_shape"] = stats.get("odd_shape", 0) + 1
                    continue
                lu = ev.get("last_update")
                try:
                    from datetime import datetime, timezone
                    lu_ts = datetime.fromisoformat(
                        str(lu).replace("Z", "+00:00")).astimezone(
                        timezone.utc).timestamp() if lu else 0.0
                except ValueError:
                    lu_ts = 0.0
                rows.append({"names": list(sets), "sets": sets,
                             "completed": bool(ev.get("completed")),
                             "last_update_ts": lu_ts})
        stats["rows"] = len(rows)
        if _quota["remaining"] is not None:
            stats["quota_remaining"] = _quota["remaining"]
    with _lock:
        _cache.update(rows=rows, stats=stats)
    return rows, stats


def set_state(rows: list[dict], fav_name: str,
              other_name: str, now: float) -> dict | None:
    """Match a score row to this match's two venue outcome names.
    Returns {'fav_sets', 'opp_sets', 'fresh', 'completed'} or None when
    no row matches (uncovered tour, name mismatch, not started)."""
    from edge.venues.mapper import team_score

    for r in rows:
        names = r["names"]
        if len(names) != 2:
            continue
        f = max(names, key=lambda n: team_score(n, fav_name))
        if team_score(f, fav_name) < 0.9:
            continue
        o = [n for n in names if n != f][0]
        if team_score(o, other_name) < 0.9:
            continue
        return {"fav_sets": r["sets"][f], "opp_sets": r["sets"][o],
                "fresh": now - r["last_update_ts"] <= FRESH_S,
                "completed": r["completed"]}
    return None


# ── Pre-match favorite verification (owner 2026-08-17 late night:
# "use the odds verification... they have to be a pre-match favorite").
# The h2h odds for the covered tennis tours define the favorite by
# SPORTSBOOK CONSENSUS, frozen at the last observation BEFORE the
# match's commence time — Kalshi's own book (which armed the wrong
# player on an empty ITF board, the TONGEE incident) is demoted to a
# pricing venue, never the favorite oracle, wherever the feed reaches.

ODDS_EVERY_S = float(os.environ.get("EDGE_FSC_ODDS_EVERY_S", "300"))
_odds_cache: dict = {"at": 0.0, "stats": {}}
# {frozenset(lowered names): {"names": [...], "probs": {name: p},
#   "commence_ts": ts, "frozen": bool}} — probs update while pre-match,
# freeze at the last pre-commence observation.
_prematch: dict = {}


def _implied_pair(bookmakers: list) -> dict | None:
    """Consensus implied probabilities for a two-player event: mean of
    1/decimal across books, pair-normalized (the de-vig)."""
    sums: dict = {}
    n = 0
    for bm in bookmakers or []:
        for mkt in bm.get("markets") or []:
            if mkt.get("key") != "h2h":
                continue
            outs = mkt.get("outcomes") or []
            if len(outs) != 2:
                continue
            try:
                pair = {str(o["name"]).strip(): 1.0 / float(o["price"])
                        for o in outs}
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            for k, v in pair.items():
                sums[k] = sums.get(k, 0.0) + v
            n += 1
    if n == 0 or len(sums) != 2:
        return None
    tot = sum(sums.values())
    if tot <= 0:
        return None
    return {k: round(v / tot, 4) for k, v in sums.items()}


def poll_prematch(active_hint: bool = True) -> dict:
    """Refresh the pre-match favorites map from the odds feed (h2h,
    covered tennis tours). Returns stats; the map itself is served by
    prematch_favorite(). Fail-soft: errors leave the map as-is."""
    import requests

    now = time.time()
    with _lock:
        if now - _odds_cache["at"] < ODDS_EVERY_S:
            return _odds_cache["stats"]
        _odds_cache["at"] = now

    api_key = os.environ.get("EDGE_ODDS_API_KEY", "")
    stats: dict = {}
    if os.environ.get("EDGE_FSC_ODDS", "1") == "0":
        stats["disabled"] = True
    elif not api_key:
        stats["no_key"] = True
    elif not active_hint:
        stats["idle"] = True
    elif _quota["remaining"] is not None and _quota["remaining"] < RESERVE:
        stats["quota_hold"] = _quota["remaining"]
    else:
        sess = requests.Session()
        keys = _tennis_keys(sess, api_key, now)
        stats["sports"] = len(keys)
        fresh = 0
        for k in keys:
            batch = _get(sess, f"/sports/{k}/odds",
                         {"apiKey": api_key, "markets": "h2h",
                          "regions": "us,eu", "oddsFormat": "decimal"})
            if batch is None:
                stats["errors"] = stats.get("errors", 0) + 1
                continue
            for ev in batch:
                names = [str(ev.get("home_team") or "").strip(),
                         str(ev.get("away_team") or "").strip()]
                if not all(names):
                    continue
                try:
                    from datetime import datetime, timezone
                    cts = datetime.fromisoformat(
                        str(ev.get("commence_time")).replace(
                            "Z", "+00:00")).astimezone(
                        timezone.utc).timestamp()
                except (TypeError, ValueError):
                    continue
                key2 = frozenset(n.lower() for n in names)
                rec = _prematch.get(key2)
                if rec and rec.get("frozen"):
                    continue          # pre-match snapshot already final
                probs = _implied_pair(ev.get("bookmakers"))
                if not probs:
                    continue
                _prematch[key2] = {
                    "names": names, "probs": probs, "commence_ts": cts,
                    # The LAST observation taken pre-commence is the
                    # pre-match truth; once the clock passes commence
                    # it freezes and in-play drift can't rewrite it.
                    "frozen": now >= cts, "at": now}
                fresh += 1
        stats["events"] = len(_prematch)
        stats["updated"] = fresh
        if _quota["remaining"] is not None:
            stats["quota_remaining"] = _quota["remaining"]
    # Drop events long finished so the map cannot grow unbounded.
    cutoff = now - 2 * 86400
    for k2 in [k2 for k2, r in _prematch.items()
               if r["commence_ts"] < cutoff]:
        _prematch.pop(k2, None)
    with _lock:
        _odds_cache["stats"] = stats
    return stats


def prematch_favorite(name_a: str, name_b: str) -> dict | None:
    """The sportsbook-consensus pre-match favorite for this pairing, or
    None when the feed does not cover it (ITF/challenger tiers).
    Returns {'fav_name', 'prob', 'commence_ts', 'frozen'} with
    fav_name spelled as the FEED spells it — callers name-match."""
    from edge.venues.mapper import team_score

    best = None
    for rec in _prematch.values():
        fa, fb = rec["names"]
        direct = min(team_score(fa, name_a), team_score(fb, name_b))
        cross = min(team_score(fa, name_b), team_score(fb, name_a))
        score = max(direct, cross)
        if score >= 0.9 and (best is None or score > best[0]):
            best = (score, rec)
    if best is None:
        return None
    rec = best[1]
    fav = max(rec["probs"], key=rec["probs"].get)
    return {"fav_name": fav, "prob": rec["probs"][fav],
            "commence_ts": rec["commence_ts"], "frozen": rec["frozen"]}
