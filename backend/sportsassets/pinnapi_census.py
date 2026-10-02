"""COVERAGE CENSUS: every current venue contract against the feed's state.

A connection is not coverage. This reads the venue's own catalogue (us_premap,
simulated families excluded with bettor_venue_realism's markers) and gives
EVERY contract exactly one named state; the counts reconcile to the catalogue
total or the census reports itself inconsistent.

States (one per contract):
  FEED_NOT_SYNCED                the feed has no current authority/resync
  OUT_OF_FEED_SCOPE_SPORT        the sport is not subscribed
  UNMAPPED_SPORT                 no PinnAPI sport id for the venue sports_type
  NO_TWO_SIDED_TITLE             the event title does not name two sides
  NO_FEED_EVENT                  no provider event with both teams within
                                 START_TOLERANCE_S of the venue start
  AMBIGUOUS_FEED_EVENT           more than one provider event matches
  MATCHED_UNSUPPORTED_FAMILY     matched, but the market family's mapping and
                                 grading are not proved (totals, spreads,
                                 alternates, team totals, periods)
  MATCHED_SUPPORTED              matched, full-game moneyline (the only family
                                 the existing de-vig and grading support)

Phase comes from the venue start time (basis VENUE_START_TIME). Team identity
uses ext_pinnacle_loop's own _team_tokens/_same_team (squad qualifiers, one-to-
one), so the census and any later decision agree on what "the same event" is.
Pure: the caller supplies rows and the feed's event view.
"""
from __future__ import annotations

import collections
from datetime import datetime, timezone
from typing import Optional

START_TOLERANCE_S = 90 * 60.0
IN_PLAY_WINDOW_S = 5 * 3600.0
MAX_CONTRACTS = 20000

SPORT_IDS = (("baseball", 6), ("soccer", 1), ("basketball", 3),
             ("hockey", 4), ("icehockey", 4), ("americanfootball", 5),
             ("football", 5), ("tennis", 2), ("mma", 8), ("boxing", 9),
             ("esports", 11), ("golf", 12), ("rugby", 7))

S_FEED_NOT_SYNCED = "FEED_NOT_SYNCED"
S_OUT_OF_SCOPE = "OUT_OF_FEED_SCOPE_SPORT"
S_UNMAPPED_SPORT = "UNMAPPED_SPORT"
S_NO_SIDES = "NO_TWO_SIDED_TITLE"
S_NO_FEED_EVENT = "NO_FEED_EVENT"
S_AMBIGUOUS = "AMBIGUOUS_FEED_EVENT"
S_UNSUPPORTED = "MATCHED_UNSUPPORTED_FAMILY"
S_SUPPORTED = "MATCHED_SUPPORTED"


def _norm_sql() -> str:
    """sport_id_of's normalisation, in SQL."""
    return ("replace(replace(lower(coalesce(sports_type, '')), '_', ''), "
            "'-', '')")


def _base_where() -> str:
    from . import bettor_venue_realism as vreal
    for m in vreal.SIMULATED_MARKERS:
        assert m.replace("-", "").isalpha() and m.islower(), m
    for pfx in vreal.SIMULATED_SPORTS_TYPE_PREFIXES:
        assert pfx.rstrip("_").isalpha() and pfx.islower(), pfx
    prose = "\n   ".join(
        "AND lower(coalesce(event_title, '') || ' ' || coalesce(question, "
        "'')) NOT LIKE '%%%s%%'" % m for m in vreal.SIMULATED_MARKERS)
    types = "\n   ".join(
        "AND coalesce(sports_type, '') NOT LIKE '%s%%'" % p
        for p in vreal.SIMULATED_SPORTS_TYPE_PREFIXES)
    return ("""game_start > now() - interval '6 hours'
   %s
   %s""" % (prose, types))


def catalogue_sql(sport_ids=None) -> str:
    """Contract rows. With sport_ids, ONLY those sports' rows (the limit then
    applies to the subscribed sports alone, so a large unsubscribed catalogue
    cannot truncate them); the rest is counted by catalogue_totals_sql."""
    scope = ""
    if sport_ids is not None:
        pfx = sorted({p for p, sid in SPORT_IDS if sid in set(sport_ids)})
        for p in pfx:
            assert p.isalpha() and p.islower(), p
        scope = ("AND (%s)" % " OR ".join(
            "%s LIKE '%s%%'" % (_norm_sql(), p) for p in pfx)
            if pfx else "AND false")
    return ("""SELECT identifier, side_norm, event_slug, event_title, kind,
       line, sports_type, extract(epoch FROM game_start)::float8 AS game_start
  FROM us_premap
 WHERE %s
   %s
 ORDER BY game_start LIMIT %d""" % (_base_where(), scope, MAX_CONTRACTS))


def catalogue_totals_sql() -> str:
    """Every current contract counted by venue sports_type (no rows)."""
    return ("""SELECT coalesce(sports_type, '') AS sports_type, count(*) AS n
  FROM us_premap
 WHERE %s
 GROUP BY 1""" % _base_where())


def sport_id_of(sports_type: Optional[str]) -> Optional[int]:
    s = (sports_type or "").lower().replace("_", "").replace("-", "")
    for prefix, sid in SPORT_IDS:
        if s.startswith(prefix):
            return sid
    return None


def family_of(kind: Optional[str], line) -> tuple:
    """(family, supported, reason) from the venue's own kind/line."""
    k = (kind or "").lower()
    if line not in (None, "") or any(w in k for w in ("total", "over",
                                                      "under")):
        if "team" in k:
            return "TEAM_TOTAL", False, "TEAM_TOTAL_GRADING_NOT_PROVED"
        if "total" in k or "over" in k or "under" in k:
            return "TOTAL", False, "TOTALS_GRADING_NOT_PROVED"
        if "spread" in k or "handicap" in k:
            return "SPREAD", False, "SPREAD_SIGN_AND_PUSH_NOT_PROVED"
        return "LINE_MARKET", False, "LINE_MARKET_GRADING_NOT_PROVED"
    if any(w in k for w in ("half", "period", "inning", "quarter", "set")):
        return "PERIOD", False, "PERIOD_NOT_FULL_GAME"
    return "MONEYLINE", True, None


def _epoch(v) -> Optional[float]:
    """Postgres extract(epoch) is numeric (asyncpg: Decimal); the feed's
    starts are floats. One numeric type, or None."""
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def phase_of(game_start: Optional[float], now: float) -> str:
    if game_start is None:
        return "UNKNOWN"
    if now < game_start:
        return "PRE_GAME"
    return "IN_PLAY" if now - game_start < IN_PLAY_WINDOW_S else "ENDED?"


def _sides(title):
    from .workers import ext_pinnacle_loop as X
    return X._sides_of(title or "")


def match_event(sides, game_start, feed_events) -> tuple:
    """(state, feed_event_id) for one venue event against provider events
    [{id, home, away, start}] -- both teams, either orientation, one-to-one,
    within START_TOLERANCE_S."""
    from .workers import ext_pinnacle_loop as X
    game_start = _epoch(game_start)
    a, b = (X._team_tokens(s)[0] for s in sides)
    hits = []
    for e in feed_events:
        if game_start is not None and e.get("start") is not None and abs(
                e["start"] - game_start) > START_TOLERANCE_S:
            continue
        h, w = X._team_tokens(e["home"])[0], X._team_tokens(e["away"])[0]
        if (X._same_team(a, h) and X._same_team(b, w)) or (
                X._same_team(a, w) and X._same_team(b, h)):
            hits.append(e["id"])
    if not hits:
        return S_NO_FEED_EVENT, None
    if len(set(hits)) > 1:
        return S_AMBIGUOUS, None
    return S_SUPPORTED, hits[0]


def _iso_epoch(v) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")
                                      ).astimezone(timezone.utc).timestamp()
    except Exception:                                           # noqa: BLE001
        return None


def feed_event_view(cache) -> dict:
    """{sport_id: [{id, home, away, start, live}]} from the feed's events
    (parent matchups only: child records carry periods/props)."""
    from . import pinnapi_feed as F
    out = collections.defaultdict(list)
    for eid, ev in cache.events.items():
        if ev.get("parentId"):
            continue
        p = F.participants(ev)
        if not p.get("home") or not p.get("away"):
            continue
        out[ev.get("sport_id")].append({
            "id": eid, "home": p["home"], "away": p["away"],
            "start": _iso_epoch(ev.get("startTime")),
            "live": bool(ev.get("isLive"))})
    return dict(out)


def census(rows, feed_view: dict, *, subscribed_sports, synced: bool,
           now: float, others=None) -> dict:
    """`rows` are contract rows (the subscribed sports when `others` is
    given); `others` = [(sports_type, n)] for the WHOLE catalogue, of which
    the non-subscribed part is counted here as OUT_OF_FEED_SCOPE_SPORT /
    UNMAPPED_SPORT (family ALL, phase ANY) so the total still reconciles."""
    counts = collections.Counter()
    by_event: dict = {}
    titles: dict = {}
    total = 0
    for r in rows:
        total += 1
        sid = sport_id_of(r.get("sports_type"))
        fam, supported, why = family_of(r.get("kind"), r.get("line"))
        gs = _epoch(r.get("game_start"))
        ph = phase_of(gs, now)
        if sid is None:
            state = S_UNMAPPED_SPORT
        elif sid not in subscribed_sports:
            state = S_OUT_OF_SCOPE
        elif not synced:
            state = S_FEED_NOT_SYNCED
        else:
            ek = r.get("event_slug")
            if ek not in by_event:
                titles[ek] = (r.get("event_title"), gs, ph)
                sides = _sides(r.get("event_title"))
                by_event[ek] = ((S_NO_SIDES, None) if not sides else
                                match_event(sides, gs,
                                            feed_view.get(sid, [])))
            state = by_event[ek][0]
            if state == S_SUPPORTED and not supported:
                state = S_UNSUPPORTED
        key = "%s|%s|%s|%s" % (sid, fam, ph, state)
        counts[key] += 1
        if state == S_UNSUPPORTED:
            counts["reason|%s" % why] += 1
    rows_total = total
    for st, n in (others or []):
        sid = sport_id_of(st)
        if sid is not None and sid in subscribed_sports:
            continue                      # already counted row by row
        state = S_UNMAPPED_SPORT if sid is None else S_OUT_OF_SCOPE
        counts["%s|ALL|ANY|%s" % (sid, state)] += int(n)
        total += int(n)
    ev_states = collections.Counter(st for st, _ in by_event.values())
    def _iso(t):
        return (None if t is None else
                datetime.fromtimestamp(t, timezone.utc).isoformat())
    unmatched = []
    for ek, (st, _) in by_event.items():
        if st in (S_NO_FEED_EVENT, S_AMBIGUOUS, S_NO_SIDES) and \
                len(unmatched) < 10:
            title, gs_ev, ph_ev = titles.get(ek, (None, None, None))
            unmatched.append({"title": title, "venue_start": _iso(gs_ev),
                              "phase": ph_ev, "state": st})
    feed_sample = [{"home": e.get("home"), "away": e.get("away"),
                    "start": _iso(e.get("start")),
                    "live": e.get("live")}
                   for sid in sorted(subscribed_sports)
                   for e in (feed_view.get(sid) or [])][:10]
    states = collections.Counter()
    for k, n in counts.items():
        if not k.startswith("reason|"):
            states[k.rsplit("|", 1)[1]] += n
    return {"total_contracts": total,
            "by_sport_family_phase_state": {k: v for k, v in counts.items()
                                            if not k.startswith("reason|")},
            "unsupported_reasons": {k[7:]: v for k, v in counts.items()
                                    if k.startswith("reason|")},
            "states": dict(states),
            "reconciled": sum(states.values()) == total,
            "matched_events": sum(1 for s, _ in by_event.values()
                                  if s == S_SUPPORTED),
            "subscribed_rows": rows_total,
            "events_by_state": dict(ev_states),
            "unmatched_event_sample": unmatched,
            "feed_event_sample": feed_sample,
            "truncated_at": (MAX_CONTRACTS if rows_total >= MAX_CONTRACTS
                             else None),
            "phase_basis": "VENUE_START_TIME"}
