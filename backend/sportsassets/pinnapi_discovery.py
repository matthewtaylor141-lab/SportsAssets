"""PINNAPI-NATIVE DISCOVERY: every subscribed Pinnacle fixture, matched to the
venue's own events, with a receipt for each (R30A P0 incident, RC2).

THE DEFECT. Every decision path started from the METERED discovery provider:
at most four competition keys per 900 s cycle, h2h only, chosen through
hand-written venue-token maps (ext_pinnacle_loop.MAX_METERED_SPORTS_PER_CYCLE,
VENUE_TOKEN_TO_PROVIDER_KEY). The PinnAPI WebSocket -- unmetered, already
connected and leased -- carried the fixtures, but a WS price change could only
be evaluated for an event the metered cycle had registered first
(pinnapi_reactive: NO_CONFIRMED_DISCOVERY, 13,307 on one runtime). Measured
2026-10-04: at least ~25 PinnAPI-matched soccer/baseball venue events a day
never reached an agent, NCAAF was budget-dropped behind NFL (absent for the
whole 2026-10-03 slate), and tennis, hockey and basketball had no lane at all.

WHAT THIS DOES. For every fixture the feed holds in a subscribed sport
(`pinnapi_feed.fixture_view`: prematch matchups and, in play, the parent's
live-phase child -- never dropped for carrying a parentId), it finds the ONE
venue event whose two structured participants are that fixture's, inside the
start tolerance, in the same sport, one-to-one -- and says, by name, what it
found:

  MATCHED                 exactly one venue event; both participants
                          assigned one-to-one; start within tolerance
  AMBIGUOUS               more than one venue event qualifies
  DUPLICATE_CANDIDATES    two or more feed fixtures claim the same venue
                          event (none of them is seeded)
  TIME_MISMATCH           the venue lists both participants, but only
                          outside the start tolerance
  PARTICIPANT_MISMATCH    a venue event inside the tolerance carries one of
                          the two participants, not the other
  NO_VENUE_COUNTERPART    no venue event of the sport carries either
                          participant in the listing window
  NORMALIZATION_FAILURE   the fixture cannot be read as a two-sided game
                          (missing or identical participants, a derived-units
                          suffix, an unreadable start, an unmapped sport)

STABLE CANONICAL IDENTITIES FIRST. The venue's structured team record (its
`team_name`, and `team_safe_name` + `team_name` where the record splits the
place from the nickname -- "golden state" + "warriors") compared EXACTLY after
folding is the identity. The fuzzy layer is a CONTROLLED FALLBACK, used only
when no exact match exists, only inside the start tolerance and the sport,
only one-to-one, and labelled MATCHED_BY_CONTROLLED_FALLBACK on the receipt:
the venue-native resolver's own token containment with a shared distinctive
token (`bettor_venue_native_identity.same_team`), and for football token
EQUALITY only (containment would read "Ohio" as "Ohio State"), the NFL's
closed 32-team table included.

THE METERED PROVIDER IS THE FALLBACK. The scheduled collector still fetches
its keys; a fixture it already seeded keeps its seed (its independent books
corroborate). Discovery seeds the reactive scheduler for every MATCHED
fixture, so a PinnAPI price change is evaluated whether or not a metered
cycle ever listed the competition.

NOTHING HERE PRICES OR TRADES. A MATCHED receipt is an identity, not a
settlement or price admission: the cycle still re-proves the venue contract
(bettor_venue_native_identity, confined to the discovered venue event), the
de-vig's supported set, the settlement comparison, freshness, depth and fees,
each refusing by its own name. Pure apart from the bounded catalogue read.
"""
from __future__ import annotations

import collections
import math
from datetime import datetime, timezone
from typing import Optional

from . import pinnapi_feed as F

VERSION = "PINNAPI_NATIVE_DISCOVERY_V1"

MATCHED = "MATCHED"
AMBIGUOUS = "AMBIGUOUS"
NO_VENUE_COUNTERPART = "NO_VENUE_COUNTERPART"
NORMALIZATION_FAILURE = "NORMALIZATION_FAILURE"
TIME_MISMATCH = "TIME_MISMATCH"
PARTICIPANT_MISMATCH = "PARTICIPANT_MISMATCH"
DUPLICATE_CANDIDATES = "DUPLICATE_CANDIDATES"
STATES = (MATCHED, AMBIGUOUS, NO_VENUE_COUNTERPART, NORMALIZATION_FAILURE,
          TIME_MISMATCH, PARTICIPANT_MISMATCH, DUPLICATE_CANDIDATES)

MATCHED_BY_EXACT = "EXACT_STRUCTURED_TEAM_RECORD"
MATCHED_BY_FALLBACK = "MATCHED_BY_CONTROLLED_FALLBACK"

#: NORMALIZATION_FAILURE sub-reasons (the receipt names which)
N_PARTICIPANTS = "FIXTURE_DOES_NOT_NAME_TWO_DISTINCT_PARTICIPANTS"
N_DERIVED = "FIXTURE_PARTICIPANT_HAS_A_DERIVED_UNITS_SUFFIX"
N_START = "FIXTURE_START_TIME_NOT_READABLE"
N_SPORT = "FIXTURE_SPORT_NOT_IN_THE_DISCOVERY_SCOPE"

#: PinnAPI sport id -> the family name the collector, the de-vig and the
#: venue-native resolver use, and the venue sportsMarketType prefix that is
#: the same sport (pinnapi_census.SPORT_IDS reads the same prefixes). Ids
#: from PinnAPI's documentation (pinnapi_feed_runtime.PINNAPI_SPORT_IDS).
FAMILY_OF_SPORT = {1: "soccer", 2: "tennis", 3: "basketball", 4: "hockey",
                   5: "football", 6: "baseball"}
VENUE_PREFIXES_OF_SPORT = {1: ("soccer",), 2: ("tennis",),
                           3: ("basketball",), 4: ("hockey", "icehockey"),
                           5: ("football", "americanfootball"),
                           6: ("baseball",)}

#: the same tolerance the census, the primary selector and the venue-native
#: resolver use (bettor_venue_native_identity.START_TOLERANCE_S: ~9x the
#: largest measured agreement, below a doubleheader's separation)
START_TOLERANCE_S = 90 * 60.0
#: a venue row not re-listed within three catalogue sweeps is not a current
#: listing (bettor_venue_native_identity.RESEEN_WITHIN_S)
RESEEN_WITHIN_S = 3 * 1800.0
#: the discovery listing window ahead (in play: the last 6 h, the census's)
HORIZON_AHEAD_S = 4 * 86400.0
MAX_VENUE_ROWS = 20000
#: receipts kept per state for the heartbeat (counts are never sampled)
RECEIPT_SAMPLE = 8


def _norm_sql() -> str:
    return ("replace(replace(lower(coalesce(sports_type, '')), '_', ''), "
            "'-', '')")


def venue_events_sql(sport_ids) -> str:
    """ONE bounded read: every current venue team record of the subscribed
    sports, one row per (event, team record), under the census's own
    real-competition filter. Fixed identifiers only."""
    from . import pinnapi_census as C
    prefixes = sorted({p for s in sport_ids
                       for p in VENUE_PREFIXES_OF_SPORT.get(int(s), ())})
    for p in prefixes:
        assert p.isalpha() and p.islower(), p
    scope = ("AND (%s)" % " OR ".join("%s LIKE '%s%%'" % (_norm_sql(), p)
                                      for p in prefixes)
             if prefixes else "AND false")
    # ONE ROW PER (EVENT, TEAM RECORD), not per contract type: an NFL game
    # carries its team records on the winner, every spread rung and the
    # team totals, and grouping by the type as well multiplied the rows by
    # the venue's whole ladder (the six-sport scope lists ~1,500 events), so
    # the LIMIT would have cut the latest-starting events silently. The sport
    # is the same for every type of one event (`min` names one of them).
    return ("""SELECT event_slug, team_name, team_safe_name, team_abbr,
       team_id, team_league, min(sports_type) AS sports_type,
       count(DISTINCT sports_type) AS sports_types,
       extract(epoch FROM game_start)::float8 AS game_start,
       count(*) AS rows
  FROM us_premap
 WHERE %s
   %s
   AND coalesce(team_name, '') <> ''
   AND coalesce(event_slug, '') <> ''
   AND game_start < now() + make_interval(secs => %d)
   AND updated_at > now() - make_interval(secs => %d)
 GROUP BY event_slug, team_name, team_safe_name, team_abbr, team_id,
          team_league, game_start
 ORDER BY game_start, event_slug
 LIMIT %d""" % (C._base_where(), scope, int(HORIZON_AHEAD_S),
                int(RESEEN_WITHIN_S), MAX_VENUE_ROWS))


def sport_of_venue_type(sports_type) -> Optional[int]:
    s = str(sports_type or "").lower().replace("_", "").replace("-", "")
    for sid, prefixes in VENUE_PREFIXES_OF_SPORT.items():
        if any(s.startswith(p) for p in prefixes):
            return sid
    return None


def _epoch(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        out = float(v)
    else:
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            try:
                out = float(v)
            except (TypeError, ValueError):
                return None
        else:
            if d.tzinfo is None:
                return None
            out = d.timestamp()
    return out if math.isfinite(out) else None


def _fold(text) -> str:
    from . import bettor_venue_native_identity as vnat
    return vnat.fold(text)


def _league_name(lg) -> Optional[str]:
    if isinstance(lg, dict):
        return str(lg.get("name") or lg.get("id") or "") or None
    return None if lg in (None, "") else str(lg)


# ── THE VENUE SIDE: events and their two structured participants ───────

def venue_events(rows) -> dict:
    """{sport id: [venue event]} from `venue_events_sql` rows. A venue event
    is {slug, start, league_tokens, participants: [record, record],
    problems: [...]}; each record carries the renderings compared EXACTLY
    (`exact`) and the raw fields the fallback reads. An event whose rows do
    not give exactly two participants or one start is kept with its problem
    named, so a fixture that would have matched it is never silently
    unmatched."""
    from . import bettor_venue_native_identity as vnat
    by = collections.defaultdict(lambda: {"names": {}, "starts": set(),
                                          "leagues": set(), "types": set()})
    for r in rows or ():
        r = dict(r)
        sid = sport_of_venue_type(r.get("sports_type"))
        if sid is None:
            continue
        ev = by[(sid, str(r["event_slug"]))]
        name = " ".join(str(r.get("team_name") or "").split())
        # ONE PARTICIPANT PER VENUE TEAM RECORD: keyed by the venue's own
        # team id where the row carries it (two contract types may render
        # one team's name differently), else by the folded name
        key = (("id", r.get("team_id")) if r.get("team_id") is not None
               else ("name", _fold(name)))
        rec = ev["names"].setdefault(key, {
            "team_name": name, "team_safe_name": r.get("team_safe_name"),
            "team_abbr": r.get("team_abbr"), "team_id": r.get("team_id"),
            "names": set(), "safe_names": set()})
        rec["names"].add(name)
        if r.get("team_safe_name"):
            rec["safe_names"].add(str(r["team_safe_name"]))
        if not rec.get("team_safe_name") and r.get("team_safe_name"):
            rec["team_safe_name"] = r.get("team_safe_name")
        ev["starts"].add(_epoch(r.get("game_start")))
        if r.get("team_league"):
            ev["leagues"].add(str(r["team_league"]).lower())
        ev["types"].add(str(r.get("sports_type") or ""))
    out = collections.defaultdict(list)
    for (sid, slug), ev in sorted(by.items()):
        problems = []
        recs = list(ev["names"].values())
        if len(recs) != 2:
            problems.append("VENUE_EVENT_PARTICIPANTS_NOT_TWO:%d" % len(recs))
        starts = {s for s in ev["starts"] if s is not None}
        if len(starts) != 1 or None in ev["starts"]:
            problems.append("VENUE_EVENT_START_NOT_ONE_INSTANT")
        for rec in recs:
            rend = set()
            for nm in sorted(rec.pop("names")) or [rec["team_name"]]:
                for sf in sorted(rec["safe_names"]) or [None]:
                    rend |= _renderings({"team_name": nm,
                                         "team_safe_name": sf})
            rec.pop("safe_names")
            rec["exact"] = sorted(rend)
        leagues = sorted(ev["leagues"]) or [vnat.league_token(slug)]
        out[sid].append({"slug": slug,
                         "start": min(starts) if starts else None,
                         "league_tokens": leagues, "participants": recs,
                         "sports_types": sorted(ev["types"])[:6],
                         "problems": problems})
    return dict(out)


def _renderings(rec) -> set:
    """The venue team record's own renderings, folded: its name; its safe
    name when that is the fuller rendering of the same record ("mammoth" /
    "utah mammoth"); and safe name + name when the record splits the place
    from the nickname ("golden state" + "warriors"). Never an invented
    alias: every rendering is built from the record's own two fields."""
    name = _fold(rec.get("team_name"))
    out = {name} if name else set()
    safe = _fold(rec.get("team_safe_name"))
    if safe and name:
        n, s = set(name.split()), set(safe.split())
        if n < s:
            out.add(safe)
        elif not s <= n:
            out.add("%s %s" % (safe, name))
    return out


# ── THE COMPARISON ─────────────────────────────────────────────────────

def _exact(provider_name: str, rec: dict) -> bool:
    return _fold(provider_name) in set(rec.get("exact") or ())


def _fallback(provider_name: str, rec: dict, family: str) -> Optional[dict]:
    """The controlled fallback, or None. Football: token EQUALITY of the
    canonical profiles (the NFL table and the college rewrites are the
    venue-native resolver's own); every other sport: that resolver's
    containment rule with a shared distinctive token and equal squad
    qualifiers, against each of the record's renderings."""
    from . import bettor_venue_native_identity as vnat
    for rendering in [rec.get("team_name")] + [
            r for r in (rec.get("exact") or ()) if r != _fold(
                rec.get("team_name"))]:
        if not rendering:
            continue
        if family == "football":
            p = vnat.team_profile(provider_name, "football")
            v = vnat.team_profile(rendering, "football")
            if p["tokens"] and p["tokens"] == v["tokens"] and \
                    p["qualifiers"] == v["qualifiers"]:
                return {"rule": "FOOTBALL_CANONICAL_TOKENS_EQUAL",
                        "rendering": rendering,
                        "tokens": sorted(p["tokens"])}
            continue
        got = vnat.same_team(vnat.team_profile(provider_name),
                             vnat.team_profile(rendering))
        if got.get("same"):
            return {"rule": "CONTAINED_WITH_A_SHARED_DISTINCTIVE_TOKEN",
                    "rendering": rendering,
                    "shared": got.get("shared_distinctive")}
    return None


def _assign(home, away, ev, family, *, allow_fallback) -> Optional[dict]:
    """The one-to-one assignment of the fixture's two participants to the
    venue event's two records, or None. Exact first; the fallback only
    when `allow_fallback` and only for a participant with no exact
    record."""
    recs = ev["participants"]
    if len(recs) != 2:
        return None
    best = None
    for a, b in ((0, 1), (1, 0)):
        how = []
        ok = True
        for name, rec in ((home, recs[a]), (away, recs[b])):
            if _exact(name, rec):
                how.append({"by": MATCHED_BY_EXACT,
                            "venue": rec["team_name"]})
                continue
            fb = _fallback(name, rec, family) if allow_fallback else None
            if fb is None:
                ok = False
                break
            how.append(dict(fb, by=MATCHED_BY_FALLBACK,
                            venue=rec["team_name"]))
        if ok:
            if best is not None:
                return None             # both orientations fit: not shown
            best = {"home": recs[a], "away": recs[b], "how": how,
                    "orientation": "SAME" if a == 0 else "SWAPPED"}
    return best


def _touches(name, ev, family) -> bool:
    return any(_exact(name, rec) or _fallback(name, rec, family) is not None
               for rec in ev["participants"])


def match_fixture(fx: dict, venue: list, *, family: str) -> dict:
    """The receipt for ONE feed fixture against its sport's venue events.
    Pure; never raises."""
    out = {"fixture_id": fx.get("id"), "quote_id": fx.get("quote_id"),
           "sport_id": fx.get("sport_id"), "family": family,
           "league": _league_name(fx.get("league")),
           "home": fx.get("home"), "away": fx.get("away"),
           "start": fx.get("startTime"), "live": bool(fx.get("live")),
           "basis": fx.get("basis"), "state": None}
    home, away = str(fx.get("home") or ""), str(fx.get("away") or "")
    if not home.strip() or not away.strip() or _fold(home) == _fold(away):
        out.update(state=NORMALIZATION_FAILURE, reason=N_PARTICIPANTS)
        return out
    if F._derived_suffix(home) or F._derived_suffix(away):
        out.update(state=NORMALIZATION_FAILURE, reason=N_DERIVED)
        return out
    start = _epoch(fx.get("startTime"))
    if start is None:
        out.update(state=NORMALIZATION_FAILURE, reason=N_START)
        return out
    inside = [e for e in venue if e["start"] is not None
              and abs(e["start"] - start) <= START_TOLERANCE_S]
    # 1 · EXACT STRUCTURED IDENTITY, inside the tolerance
    exact = [(e, a) for e in inside
             for a in [_assign(home, away, e, family, allow_fallback=False)]
             if a is not None]
    hits = exact
    # 2 · THE CONTROLLED FALLBACK, only where no exact identity exists
    if not hits:
        hits = [(e, a) for e in inside
                for a in [_assign(home, away, e, family,
                                  allow_fallback=True)] if a is not None]
    if len(hits) > 1:
        out.update(state=AMBIGUOUS, candidates=[e["slug"] for e, _ in hits][
            :6])
        return out
    if len(hits) == 1:
        ev, a = hits[0]
        if ev["problems"]:
            out.update(state=AMBIGUOUS, venue_event_slug=ev["slug"],
                       reason="VENUE_EVENT_NOT_ONE_FIXTURE:%s"
                       % ",".join(ev["problems"]))
            return out
        fallback = any(h["by"] == MATCHED_BY_FALLBACK for h in a["how"])
        # THE VENUE TEAM RECORD OF EACH PINNACLE DESIGNATION, carried so a
        # line contract's team (a spread's covering side, a team total's
        # team) is bound to Pinnacle's home/away by the venue's own record
        # -- its team id first, its own names otherwise (bettor_market_
        # family.designation_of_team) -- never re-matched by name later.
        out["venue_records"] = {
            d: {k: a[d].get(k) for k in (
                "team_name", "team_safe_name", "team_abbr", "team_id")}
            for d in ("home", "away")}
        out.update(state=MATCHED, venue_event_slug=ev["slug"],
                   venue_league_tokens=list(ev["league_tokens"]),
                   venue_start=ev["start"],
                   start_offset_s=round(ev["start"] - start, 1),
                   matched_by=MATCHED_BY_FALLBACK if fallback
                   else MATCHED_BY_EXACT, orientation=a["orientation"],
                   assignment=a["how"],
                   venue_participants={"home": a["home"]["team_name"],
                                       "away": a["away"]["team_name"]})
        return out
    # 3 · WHY NOT: both participants elsewhere in time, one of them here,
    #     or neither anywhere
    elsewhere = [e for e in venue if e not in inside and e["start"] is not
                 None and _assign(home, away, e, family, allow_fallback=True)]
    if elsewhere:
        near = min(elsewhere, key=lambda e: abs(e["start"] - start))
        out.update(state=TIME_MISMATCH, venue_event_slug=near["slug"],
                   start_offset_s=round(near["start"] - start, 1),
                   tolerance_s=START_TOLERANCE_S)
        return out
    partial = [e for e in inside if _touches(home, e, family)
               or _touches(away, e, family)]
    if partial:
        out.update(state=PARTICIPANT_MISMATCH,
                   candidates=[e["slug"] for e in partial][:6])
        return out
    out.update(state=NO_VENUE_COUNTERPART, venue_events_in_sport=len(venue))
    return out


def discover(events, venue_rows, *, sport_ids) -> dict:
    """Every subscribed fixture's receipt, counted by sport / league /
    state, and the MATCHED ones as seeds. `events` is the feed cache's
    events dict; `venue_rows` the rows of `venue_events_sql`. Pure."""
    sports = {int(s) for s in sport_ids}
    venue = venue_events(venue_rows)
    view, skipped = F.fixture_view(events)
    receipts = []
    for fx in view:
        sid = fx.get("sport_id")
        if sid not in sports:
            continue
        fam = FAMILY_OF_SPORT.get(sid)
        if fam is None:
            receipts.append({"fixture_id": fx.get("id"), "sport_id": sid,
                             "state": NORMALIZATION_FAILURE,
                             "reason": N_SPORT})
            continue
        receipts.append(match_fixture(fx, venue.get(sid, []), family=fam))
    # DUPLICATE CANDIDATES: one venue event claimed by two fixtures
    claims = collections.defaultdict(list)
    for r in receipts:
        if r["state"] == MATCHED:
            claims[(r["sport_id"], r["venue_event_slug"])].append(r)
    for rs in claims.values():
        if len(rs) > 1:
            ids = [r["fixture_id"] for r in rs]
            for r in rs:
                r.update(state=DUPLICATE_CANDIDATES, claimed_by=ids[:6])
    counts = collections.Counter()
    by_sport_league = collections.Counter()
    samples = collections.defaultdict(list)
    for r in receipts:
        counts[r["state"]] += 1
        by_sport_league["%s|%s|%s" % (r.get("family") or r.get("sport_id"),
                                      r.get("league") or "?",
                                      r["state"])] += 1
        if len(samples[r["state"]]) < RECEIPT_SAMPLE:
            samples[r["state"]].append({k: r.get(k) for k in (
                "fixture_id", "family", "league", "home", "away", "start",
                "live", "venue_event_slug", "matched_by", "reason",
                "candidates", "start_offset_s")})
    seeds = [seed_event(r) for r in receipts if r["state"] == MATCHED]
    matched_venue = {(sid, s) for (sid, s), rs in claims.items()
                     if len(rs) == 1}
    venue_unmatched = sum(1 for sid, evs in venue.items() if sid in sports
                          for e in evs if (sid, e["slug"]) not in
                          matched_venue)
    # THE LINE LANE'S LOOKUP: venue event -> the one fixture matched to it
    # (a venue event two fixtures claimed is DUPLICATE_CANDIDATES above and
    # is not here), so a line contract of a venue event reached by ANY
    # discovery path is priced against that fixture's Pinnacle markets.
    by_venue_event = {r["venue_event_slug"]: identity_of(r)
                      for r in receipts if r["state"] == MATCHED}
    return {"version": VERSION, "fixtures": len(receipts),
            "by_venue_event": by_venue_event,
            "venue_rows_truncated": len(venue_rows or ()) >= MAX_VENUE_ROWS,
            "states": {s: counts.get(s, 0) for s in STATES},
            "by_sport_league_state": dict(by_sport_league),
            "records_pricing_no_fixture": dict(skipped),
            "venue_events": sum(len(v) for s, v in venue.items()
                                if s in sports),
            "venue_events_without_a_fixture": venue_unmatched,
            "receipt_sample": dict(samples),
            "receipts": receipts, "seeds": seeds}


def seed_event(receipt: dict) -> dict:
    """The discovery event the collector's cycle evaluates: the provider
    fields it reads (id, home_team, away_team, commence_time, bookmakers)
    with the FIXTURE's own Pinnacle names -- so the primary selector's exact
    name match finds this very fixture -- and the discovery's identity under
    `pinnapi_native` (the venue event it matched, its league token, how)."""
    start = _epoch(receipt.get("start"))
    return {"id": "pinnapi:%s" % receipt["fixture_id"],
            "home_team": receipt["home"], "away_team": receipt["away"],
            "commence_time": (datetime.fromtimestamp(start, timezone.utc)
                              .isoformat().replace("+00:00", "Z")
                              if start is not None else None),
            "sport_title": receipt.get("league"), "bookmakers": [],
            "pinnapi_native": {
                "version": VERSION, "fixture_id": receipt["fixture_id"],
                "quote_id": receipt.get("quote_id"),
                "sport_id": receipt.get("sport_id"),
                "family": receipt.get("family"),
                "league": receipt.get("league"),
                "venue_event_slug": receipt["venue_event_slug"],
                "venue_league_tokens": list(
                    receipt.get("venue_league_tokens") or ()),
                "matched_by": receipt.get("matched_by"),
                "orientation": receipt.get("orientation"),
                "start_offset_s": receipt.get("start_offset_s"),
                "venue_records": receipt.get("venue_records"),
                "live": receipt.get("live")}}


def sport_key_for(family) -> str:
    """The collector's sport key for a PinnAPI-native seed (its funnel row
    and event-ledger key): never a metered provider key."""
    return "pinnapi_%s" % family


def digest(result: dict) -> dict:
    """The bounded heartbeat view (no full receipt list)."""
    if not isinstance(result, dict):
        return {"state": "UNAVAILABLE"}
    return {k: result.get(k) for k in (
        "version", "fixtures", "states", "by_sport_league_state",
        "records_pricing_no_fixture", "venue_events",
        "venue_events_without_a_fixture", "venue_rows_truncated",
        "receipt_sample", "registered", "line_census", "computed_at",
        "took_ms", "error")}


#: the line census's bounded catalogue read (every line contract of the
#: matched venue events; two rows per market)
MAX_LINE_ROWS = 20000


def line_rows_sql() -> str:
    """ONE bounded read of the matched venue events' line contracts, for the
    line census (bettor_market_family.census). $1 venue event slugs, $2 the
    venue line types, $3 the re-seen window, $4 the row cap."""
    return """SELECT market_slug, event_slug, side_norm, intent, line, signed,
       team_name, team_safe_name, team_abbr, team_id, sports_type
  FROM us_premap
 WHERE event_slug = ANY($1::text[])
   AND sports_type = ANY($2::text[])
   AND market_slug IS NOT NULL
   AND updated_at > now() - make_interval(secs => $3)
 ORDER BY event_slug, market_slug, intent
 LIMIT $4"""


def identity_of(receipt: dict) -> dict:
    """The part of a MATCHED receipt the line lane needs: the fixture, the
    record that prices it now, the sport, and each Pinnacle designation's
    venue team record."""
    return {k: receipt.get(k) for k in (
        "fixture_id", "quote_id", "sport_id", "family", "league",
        "venue_event_slug", "venue_records", "orientation", "matched_by",
        "live")}


def identity_for(venue_event_slug, result=None) -> Optional[dict]:
    """The latest discovery's identity for a venue event (the runtime's
    last `discover` result unless one is passed), or None when no fixture is
    matched to it. In-process; never raises."""
    try:
        if result is None:
            from . import pinnapi_feed_runtime as FR
            result = FR._STATE.get("discovery")
        got = ((result or {}).get("by_venue_event") or {}).get(
            venue_event_slug)
        return dict(got) if isinstance(got, dict) else None
    except Exception:                                          # noqa: BLE001
        return None
