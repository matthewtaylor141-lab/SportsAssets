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

Phase is a schedule estimate (VENUE_START_TIME), not verified in-play status.
Identity uses both structured venue team names; display titles are not IDs.
An event/family match does not establish contract settlement or eligibility.
`contract_match` is that same classification for ONE contract (Xavier's
held-position read in pinnapi_feed_runtime.held_moneyline uses it), built
from the same grouped structured rows through `event_identity`.
Pure: the caller supplies rows and the feed's event view.
"""
from __future__ import annotations

import collections
import math
from datetime import datetime, timezone
from typing import Optional

START_TOLERANCE_S = 90 * 60.0
IN_PLAY_WINDOW_S = 5 * 3600.0
MAX_CONTRACTS = 20000
#: the venue catalogue sweep's own forward window (workers/premap.refresh
#: fwd_h): the census's population, now stated (see _base_where)
CENSUS_HORIZON_H = 96

#: ("football", 5) PRECEDES ("americanfootball", 5) ON PURPOSE (R30A).
#: `sport_family_of` returns the FIRST name for an id, and that name is the
#: key `bettor_pinnacle_devig.SUPPORTED` is read with: "americanfootball"
#: first made every held NFL read refuse MARKET_NOT_IN_SUPPORTED_SET. The
#: order changes nothing for `sport_id_of` (no venue sports_type starting
#: "americanfootball" also starts "football", or the reverse).
SPORT_IDS = (("baseball", 6), ("soccer", 1), ("basketball", 3),
             ("hockey", 4), ("icehockey", 4), ("football", 5),
             ("americanfootball", 5), ("tennis", 2), ("mma", 8),
             ("boxing", 9), ("esports", 11), ("golf", 12), ("rugby", 7))

#: the venue's football money-line sports type (R30A; admitted in
#: `family_of` only for a league the de-vig admits by measurement: the NFL,
#: and since the P0 incident the college board, `cfb`). The name is kept:
#: the venue spells its NFL and college winner types the same way.
NFL_MONEYLINE_TYPE = "football_team_full_game_winner"
#: the census refusal for a football money line of a league the de-vig has
#: NOT admitted (or whose structured league and slug disagree). Renamed in the
#: P0 incident from FOOTBALL_MONEYLINE_ADMITTED_FOR_THE_NFL_ONLY, which became
#: untrue the moment the college board was admitted by its own measurement.
R_FOOTBALL_LEAGUE_NOT_ADMITTED = (
    "FOOTBALL_MONEYLINE_LEAGUE_NOT_ADMITTED_BY_MEASUREMENT")

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
    # THE CENSUS'S HORIZON IS STATED, NOT INHERITED (R30A inc-catalogue). The
    # census counted every row with game_start > now-6h and needed no upper
    # bound only because the catalogue writer never read past +96 h. The
    # writer now walks the venue's calendar on both sides of its window
    # (workers/premap AHEAD / STARTED_EARLIER: futures, next week's slate), and
    # without this bound those listings -- which the feed does not carry yet
    # -- would read as NO_FEED_EVENT and push the subscribed rows past
    # MAX_CONTRACTS. The census keeps measuring the population it always
    # measured; the catalogue itself is no longer cut to it.
    return ("""game_start > now() - interval '6 hours'
   AND game_start <= now() + interval '%d hours'
   %s
   %s""" % (CENSUS_HORIZON_H, prose, types))


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
       team_name, team_id, team_league, question, signed,
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


def family_of(kind: Optional[str], line, sports_type=None, row=None) -> tuple:
    """The venue's explicit type wins over generic kind='side'.

    A blank line is also used by player props and inning winners. It is
    NOT evidence of a full-game moneyline. 'supported' is a family-level
    census label only; settlement/price/contract eligibility is downstream.
    """
    st = str(sports_type or '').lower()
    if st == NFL_MONEYLINE_TYPE:
        # R30A · THE NFL FULL-GAME MONEY LINE, FOR THE NFL ONLY. The venue's
        # own type for its NFL winner contract (its listing's
        # sportsMarketType, tests/fixtures/pmus_nfl_listing_2026_10_04.json).
        # Football is admitted to the de-vig BY LEAGUE
        # (bettor_pinnacle_devig.SUPPORTED_BY_LEAGUE), so this label is
        # granted by the same league read the de-vig makes -- the row's
        # structured team league and its venue-native slug, which must agree
        # -- and never by the sports type alone: the college board DOES share
        # the spelling. It is admitted since the P0 incident by its OWN
        # measurement (SUPPORTED_BY_LEAGUE cfb: research-sql run 37241503567,
        # 9/9 cfb lines two-way) and its own cited terms
        # (bettor_ncaaf_settlement); its catalogue rows carry the same clock
        # line (research-sql run 37241920748, C1/C2: line '00' / '30',
        # tests/fixtures/pmus_cfb_catalogue_rows_2026_10_04.json), proved by
        # the same side-aware clock proof below. Before this, every
        # held NFL contract was MATCHED_UNSUPPORTED_FAMILY
        # (VENUE_MARKET_TYPE_NOT_PROVED), so the held read, the held-first
        # queue and the on-demand Xavier read refused all NFL inventory even
        # with football subscribed.
        #
        # THE LINE IS PROVED, NOT IGNORED. Production's NFL catalogue rows
        # carry a NON-blank line on every row (research-sql run 37231923822,
        # W2: 28 rows / 14 contracts, line_blank false): the venue question
        # ("... scheduled for October 4, 2026 at 1:30 PM UTC?") is the clock
        # the premap sweep stamps. So the NFL type takes the SAME side-aware
        # clock proof baseball and soccer take below -- a line the side
        # itself states still vetoes it -- and nothing is stripped.
        from . import bettor_pinnacle_devig as devig
        r = row if isinstance(row, dict) else {}
        league = devig.league_of_contract(
            {"league": r.get("team_league"),
             "us_market_slug": r.get("identifier")})
        if devig.expected_outcomes("football", "h2h", league=league) is None:
            return ('MONEYLINE', False, R_FOOTBALL_LEAGUE_NOT_ADMITTED)
    if st in ('baseball_team_full_game_winner',
              'soccer_team_full_time_winner', NFL_MONEYLINE_TYPE):
        if line not in (None, '') and row is not None:
            # Reuse the existing side-aware clock proof. Never just strip
            # numeric zero: real signed/side lines veto this correction.
            from .market_clock_artifact import _clock_artifact
            if _clock_artifact(str(line), row):
                return 'MONEYLINE', True, 'PROVED_CLOCK_ARTIFACT'
        return ('MONEYLINE', True, None) if line in (None, '') else (
            'UNKNOWN', False, 'WINNER_WITH_UNEXPECTED_LINE')
    if st:
        if '_player_' in st:
            return 'PLAYER_PROP', False, 'PLAYER_PROP_GRADING_NOT_PROVED'
        if any(w in st for w in ('first_five', 'inning', 'half', 'quarter',
                                  '_period', '_set')):
            return 'PERIOD', False, 'PERIOD_GRADING_NOT_PROVED'
        if 'spread' in st or 'handicap' in st:
            return 'SPREAD', False, 'SPREAD_SIGN_AND_PUSH_NOT_PROVED'
        if 'total' in st:
            return 'TOTAL', False, 'TOTAL_SCOPE_AND_GRADING_NOT_PROVED'
        return 'UNKNOWN', False, 'VENUE_MARKET_TYPE_NOT_PROVED'
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
    return "UNKNOWN", False, "VENUE_MARKET_TYPE_MISSING"


def _epoch(v) -> Optional[float]:
    """Postgres extract(epoch) is numeric (asyncpg: Decimal); the feed's
    starts are floats. One numeric type, or None."""
    try:
        n = None if v is None else float(v)
        return n if n is not None and math.isfinite(n) else None
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
    if game_start is None:
        return 'START_TIME_MISSING', None
    # Structured full names: no token containment or invented nickname alias.
    norm = lambda s: ' '.join(X._fold(s).split())
    a, b = (norm(s) for s in sides)
    if not a or not b or a == b:
        return 'STRUCTURED_PARTICIPANTS_NOT_TWO', None
    hits = []
    for e in feed_events:
        start = _epoch(e.get('start'))
        if start is None or abs(start - game_start) > START_TOLERANCE_S:
            continue
        h, w = norm(e.get('home')), norm(e.get('away'))
        if (a == h and b == w) or (a == w and b == h):
            hits.append(e["id"])
    if not hits:
        return S_NO_FEED_EVENT, None
    if len(set(hits)) > 1:
        return S_AMBIGUOUS, None
    return S_SUPPORTED, hits[0]


def event_identity(teams, leagues, starts, event_slug, game_start,
                   feed_events) -> tuple:
    """(state, feed_event_id) for ONE venue event from its grouped
    structured rows: both participants' team names, one league, one start.
    The census and the held-contract read both call this, so they agree on
    which provider event a contract is (or name why there is none)."""
    sides = sorted(teams)
    if len(leagues) > 1:
        return 'VENUE_EVENT_LEAGUE_CONFLICT', None
    if not event_slug or len(sides) != 2:
        return 'STRUCTURED_PARTICIPANTS_NOT_TWO', None
    if None in starts or len(starts) != 1:
        return 'VENUE_EVENT_START_CONFLICT', None
    return match_event(sides, game_start, feed_events)


def group_event(rows) -> tuple:
    """(teams, leagues, starts) of one event's rows, as `census` groups."""
    teams, leagues, starts = set(), set(), set()
    for r in rows:
        if r.get('team_league'):
            leagues.add(str(r['team_league']).lower())
        if str(r.get('team_name') or '').strip():
            teams.add(str(r['team_name']).strip())
        starts.add(_epoch(r.get('game_start')))
    return teams, leagues, starts


def contract_match(row, event_rows, feed_view: dict, *, subscribed_sports,
                   synced: bool) -> tuple:
    """(state, feed_event_id, sport_id) for ONE contract `row`, given every
    catalogue row of its event (`event_rows`, the same base filter as the
    census) -- the same states, in the same order, as `census` gives it."""
    sid = sport_id_of(row.get("sports_type"))
    if sid is None:
        return S_UNMAPPED_SPORT, None, None
    if sid not in subscribed_sports:
        return S_OUT_OF_SCOPE, None, sid
    if not synced:
        return S_FEED_NOT_SYNCED, None, sid
    same = [r for r in event_rows
            if sport_id_of(r.get('sports_type')) == sid
            and r.get('event_slug') == row.get('event_slug')] or [row]
    teams, leagues, starts = group_event(same)
    state, eid = event_identity(teams, leagues, starts, row.get('event_slug'),
                                _epoch(row.get("game_start")),
                                feed_view.get(sid, []))
    if state == S_SUPPORTED and not family_of(
            row.get("kind"), row.get("line"), row.get('sports_type'),
            row)[1]:
        return S_UNSUPPORTED, None, sid
    return state, eid, sid


def sport_family_of(sid) -> Optional[str]:
    """The first SPORT_IDS name for a PinnAPI sport id ('soccer' for 1,
    'baseball' for 6), the spelling bettor_pinnacle_devig.SUPPORTED uses."""
    return next((p for p, s in SPORT_IDS if s == sid), None)


def designation_of(outcome, feed_event: dict) -> Optional[str]:
    """Which moneyline designation of a matched provider event an outcome
    name is: 'draw' for a draw, else the ONE of home/away that is the same
    team under ext_pinnacle_loop's _team_tokens/_same_team; None when it is
    neither or both."""
    from .workers import ext_pinnacle_loop as X
    t = X._team_tokens(outcome or "")[0]
    if t == frozenset(("draw",)):
        return "draw"
    hits = [d for d in ("home", "away") if X._same_team(
        t, X._team_tokens(feed_event.get(d) or "")[0])]
    return hits[0] if len(hits) == 1 else None


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
    # Group before matching: each venue row describes one selection; both
    # participants come from the event's structured team records. Never
    # strip a 'Game 1:' title prefix or invent city abbreviations here.
    rows = list(rows)
    teams = collections.defaultdict(set)
    starts = collections.defaultdict(set)
    leagues = collections.defaultdict(set)
    for r in rows:
        ek = (sport_id_of(r.get('sports_type')), r.get('event_slug'))
        if r.get('team_league'):
            leagues[ek].add(str(r['team_league']).lower())
        if str(r.get('team_name') or '').strip():
            teams[ek].add(str(r['team_name']).strip())
        starts[ek].add(_epoch(r.get('game_start')))
    total = 0
    for r in rows:
        total += 1
        sid = sport_id_of(r.get("sports_type"))
        fam, supported, why = family_of(r.get("kind"), r.get("line"),
                                         r.get('sports_type'), r)
        gs = _epoch(r.get("game_start"))
        ph = phase_of(gs, now)
        if sid is None:
            state = S_UNMAPPED_SPORT
        elif sid not in subscribed_sports:
            state = S_OUT_OF_SCOPE
        elif not synced:
            state = S_FEED_NOT_SYNCED
        else:
            ek = (sid, r.get('event_slug'))
            if ek not in by_event:
                titles[ek] = (r.get("event_title"), gs, ph)
                by_event[ek] = event_identity(
                    teams[ek], leagues[ek], starts[ek], ek[1], gs,
                    feed_view.get(sid, []))
            state = by_event[ek][0]
            if state == S_SUPPORTED and not supported:
                state = S_UNSUPPORTED
        key = "%s|%s|%s|%s" % (sid, fam, ph, state)
        counts[key] += 1
        if state == S_UNSUPPORTED:
            counts["reason|%s" % why] += 1
        if why == 'PROVED_CLOCK_ARTIFACT':
            counts['clock_artifact'] += 1
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
        t = _epoch(t)
        try:
            return None if t is None else datetime.fromtimestamp(t, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    unmatched = []
    for ek, (st, _) in by_event.items():
        if st != S_SUPPORTED and \
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
        if not k.startswith("reason|") and k != 'clock_artifact':
            states[k.rsplit("|", 1)[1]] += n
    return {"total_contracts": total,
            "identity_basis": "VENUE_STRUCTURED_TEAM_NAMES_AND_START_TIME",
            "decision_eligibility_proven": False,
            "proved_clock_artifact_rows": counts.get('clock_artifact', 0),
            "by_sport_family_phase_state": {k: v for k, v in counts.items()
                                            if not k.startswith("reason|") and k != 'clock_artifact'},
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
