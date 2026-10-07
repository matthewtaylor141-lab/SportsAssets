"""THE VENUE CATALOGUE'S COMPLETENESS CONTRACT (R30A P0 incident, inc-catalogue).

Pure: no database, no network, no venue client. `workers/premap.refresh` (the
writer of `us_premap`, all three lanes: full, fast and calendar) and
`pmus._desk_sweep` (the desk's browse cache) drive `PageWalk`; premap also
drives the tally, the side-key guard and the listing rules. The tests drive it
with production-shaped pages.

THE OWNER'S QUESTION (2026-10-04): "We need the full tradable catalogue, not a
sample of it." Audit and repair pagination, result limits, market-per-event
limits, first-page-only and first-market-only behaviour, sport / league /
active-state / live-vs-pregame filters, and duplicate keys collapsing distinct
markets.

WHAT PRODUCTION SAID BEFORE THIS MODULE EXISTED (read-only evidence):

  * research-sql run 37233672878 (2026-10-04 20:51Z), K3a: of 40,094 rows the
    sweep re-saw in 90 minutes, ZERO started more than 12 h before their
    sighting and ZERO start more than 96 h after it. The start-time window
    [now-12h, now+96h] was the only door into the catalogue.
  * fetch-docs runs 37233823157 / 37233829391 (the venue's PUBLIC gateway,
    `GET /v1/events`, limit 2): the venue lists active, open, tradable sports
    events OUTSIDE that window -- `mlb-nlchamp-2026-09-27` "National League
    Champion" and "World Series Champion" (startTime 2026-09-07, four weeks
    back, every market MARKET_STATUS_OPEN, `live: false`, `period: "NS"`) and,
    beyond +96 h, events with startTime in November. None of them could ever
    reach `us_premap`: the futures family for the MLB post-season did not
    exist for BETTOR. K3b showed the in-window futures (ALDS/NLDS series
    winners, an ESL Pro League season winner at -11:53) falling out of the
    catalogue 12 hours after their start while still open.
  * the same payload carries the venue's OWN market count per event
    (`marketCounts.numMarkets`, on all four events read) and, on SOME events,
    its own live state: `live: false` with `period: "NS"` on the two MLB
    futures events, `period: "NS"` and NO `live` key on the two politics
    events, and `ended` on none of the four. Nothing of it was read, so a
    per-event cap on the inline market list, and the difference between a
    live market and a pregame one, were invisible. Because `live` is not on
    every event, the schedule estimate (SCHEDULE_ESTIMATE) is an expected
    path, not an exception, and every stored state names its source.
  * the payload's two end times differ, and only one is the event's: the
    EVENT's `endDate` is when its subject ends (National League Champion
    2026-10-20, World Series Champion 2026-11-01, the midterms 2026-11-03
    23:59 for a 2026-11-03 00:00 start), while a MARKET's `endDate` is its
    expiry (2026-11-06 on every MLB future, 2027-02-02 on the midterms) -- and
    on a single game it equals the game's start (`aec-mlb-az-col-2026-09-24`:
    endDate = gameStartTime = 2026-09-24T19:10:00Z, the settled-market
    fixture tests/fixtures/pmus_settled_market_2026_09_24_az_col.json).
    `event_end` therefore reads the event's own endDate and never a market's.
  * render-ops logs (sportsassets-api, 17:52-20:51Z): every desk sweep read
    `pages=14 events=1400/1400` -- a fixed 14-page cap -- while the premap
    sweep of the same board walked 18 pages (1,714-1,738 events); the desk's
    competition endpoint reported `"truncated": False` over it. One sweep
    (20:18:29Z) died on page 3 and replaced the 1,400-event board with 200.

WHAT THIS MODULE PROVIDES

  PageWalk           offset pagination that cannot silently become
                     first-page-only: a short FIRST page is confirmed by one
                     more read (an empty answer ends the pass, events prove a
                     venue page-size cap and paging continues at the venue's
                     size); every next page re-reads the last OVERLAP events of
                     the previous one, so up to OVERLAP events that shifted
                     across a page boundary while the board changed underneath
                     the sweep are caught instead of skipped -- and a page that
                     re-reads NONE of them (a shift larger than the overlap) is
                     detected and answered with one bounded re-read a page
                     back; a shift even that cannot cover is counted
                     (`shift_unrecovered`) and the pass is not COMPLETE. The
                     budget is a number of requests -- every request, the
                     failed probe rungs included -- and running out of it (or
                     of wall time) while pages are still full is TRUNCATED, by
                     name.
  CompletenessTally  every listing seen, kept or dropped -- with a precise
                     reason -- by pass, sport, league and market family; the
                     venue's own market count against what arrived inline; the
                     live / pregame state of every event and where that state
                     came from. Bounded: a receipt never grows with the board.
  SideKeyGuard       the catalogue's unique key is (identifier, side_norm).
                     Two sides of ONE market that normalise to the same text
                     (and carry different intents) collapsed into one row --
                     the last write won, so one side was silently unorderable
                     and the surviving row's text said nothing about which side
                     it was. The guard keeps both, qualifying each side_norm
                     with the venue's own long/short marker, and records the
                     collision. Production on 2026-10-04 held ZERO such
                     collapses (K2a: 20,033 two-sided markets, every one with 2
                     rows and 2 intents); the guard is what keeps it zero, and
                     the receipt is what proves it.

WHAT IT DOES NOT DO. It never invents a side, a line or a market: every row is
still built by `premap._market_rows` from the venue's own side expansion. It
never decides tradability beyond the venue's own words (closed, archived,
ended, live, period, the event's end date, market status). It raises no
economic threshold and lowers none.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

VERSION = "VENUE_CATALOGUE_COMPLETENESS_V2"

# ── passes: which slice of the venue's calendar a request walks ──────────
PASS_WINDOW = "WINDOW"                    # [now-back_h, now+fwd_h]: the sweep's own window
PASS_AHEAD = "AHEAD"                      # (now+fwd_h, now+AHEAD_DAYS]: futures, next week's slate
PASS_STARTED_EARLIER = "STARTED_EARLIER"  # [now-EARLIER_DAYS, now-back_h): still-running futures, live / multi-day events
PASS_FAST = "FAST"                        # the imminent window, every 180 s
PASS_MARKETS_FALLBACK = "MARKETS_FALLBACK"
PASSES = (PASS_WINDOW, PASS_AHEAD, PASS_STARTED_EARLIER, PASS_FAST,
          PASS_MARKETS_FALLBACK)
#: the two passes of the CALENDAR lane (premap.calendar_refresh), which runs
#: after the full sweep and outside its lock
CALENDAR_PASSES = (PASS_AHEAD, PASS_STARTED_EARLIER)

#: THE CALENDAR IS WALKED IN SLICES, NEAREST FIRST, never as one request over
#: months. The venue's event board has no ordering this module can rely on
#: (the SDK's `orderBy` takes field names nobody has measured, and PREMAP-GT
#: found an unbounded board LEADS with a stale historical catalogue), while its
#: `startTimeMin` / `startTimeMax` filter is proven (research-sql run
#: 37233672878 K3a: zero rows outside the sweep's window). So each pass is cut
#: into start-time slices from the window's edge outward, and the pass budget is
#: spent nearest-first: if it runs out, what goes unread is the OLDEST (or the
#: furthest ahead) slice, named in the receipt, never an arbitrary subset of
#: the whole range. Bounds in hours from now, with the window's own edge
#: (fwd_h / back_h) as the first bound and the pass's reach as the last.
AHEAD_SLICE_BOUNDS_H = (14 * 24.0, 60 * 24.0)
EARLIER_SLICE_BOUNDS_H = (36.0, 7 * 24.0, 30 * 24.0)


def calendar_slices(edge_h: float, reach_h: float, bounds) -> list:
    """[(lo_h, hi_h), ...] from the window's edge to the pass's reach, cut at
    `bounds` (hours from now, absolute values). Pure. A bound outside
    (edge_h, reach_h) is ignored; the slices tile the range exactly."""
    cuts = [float(edge_h)] + sorted(float(b) for b in bounds
                                    if float(edge_h) < float(b) < float(reach_h))
    cuts.append(float(reach_h))
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)
            if cuts[i + 1] > cuts[i]]

# ── market families, read off the venue's own sportsMarketType ───────────
F_WINNER = "WINNER"
F_SPREAD = "SPREAD"
F_TOTAL = "TOTAL"
F_TEAM_TOTAL = "TEAM_TOTAL"
F_PERIOD = "PERIOD"
F_PLAYER_PROP = "PLAYER_PROP"
F_FUTURES = "FUTURES"
F_YES_NO = "YES_NO"
F_MULTI_OUTCOME = "MULTI_OUTCOME"
F_OTHER = "OTHER"
F_NO_TYPE = "NO_TYPE"
FAMILIES = (F_WINNER, F_SPREAD, F_TOTAL, F_TEAM_TOTAL, F_PERIOD,
            F_PLAYER_PROP, F_FUTURES, F_YES_NO, F_MULTI_OUTCOME, F_OTHER,
            F_NO_TYPE)

# ── the listing state BETTOR stores on every row, and its source ─────────
S_LIVE = "LIVE"            # the venue says the event is in play
S_PREGAME = "PREGAME"      # not started (venue's own `period: NS`, or the start is ahead)
S_NOT_LIVE = "NOT_LIVE"    # the venue says not live after the start, and its period word is not a final one
                           # (a break, a delay, a suspension, or a period word this module does not know)
S_ENDED = "ENDED"          # the venue says the event ended (`ended`, or a final period word such as FT)
S_STARTED = "STARTED"      # schedule only: the start has passed, the venue stated nothing
S_UNKNOWN = "UNKNOWN"      # no venue flag and no start time
STATES = (S_LIVE, S_PREGAME, S_NOT_LIVE, S_ENDED, S_STARTED, S_UNKNOWN)

SRC_VENUE_LIVE_FLAG = "VENUE_LIVE_FLAG"
SRC_VENUE_ENDED_FLAG = "VENUE_ENDED_FLAG"
SRC_VENUE_PERIOD = "VENUE_PERIOD_WORD"
SRC_SCHEDULE = "SCHEDULE_ESTIMATE"
SRC_NONE = "NO_EVIDENCE"
STATE_SOURCES = (SRC_VENUE_LIVE_FLAG, SRC_VENUE_ENDED_FLAG, SRC_VENUE_PERIOD,
                 SRC_SCHEDULE, SRC_NONE)

#: The venue period words that mean the event is OVER. Only `NS` has been
#: observed from this venue (fetch-docs 37233829391 / 37233823157); these are
#: the final words the codebase already maps to FINAL for a progress feed
#: (bettor_progress_providers._STATUS_WORDS: ft, final, finished, ended, aet,
#: pen) plus their overtime / shoot-out spellings. A word NOT here is never
#: read as final: an unknown period after the start stays NOT_LIVE, which the
#: STARTED_EARLIER rule below drops anyway unless the venue says live or the
#: event's own end date is still ahead.
FINAL_PERIOD_WORDS = frozenset({"FT", "F", "FINAL", "FINISHED", "ENDED",
                                "AET", "PEN", "F/OT", "F/SO", "FT/OT",
                                "FT_PEN", "AP"})
PREGAME_PERIOD_WORDS = frozenset({"NS"})

# ── why a listing was not kept: one precise reason each, never "filtered" ─
D_EVENT_NO_SLUG = "EVENT_WITHOUT_SLUG"
D_EVENT_CLOSED = "EVENT_CLOSED_OR_ARCHIVED_BY_VENUE"
D_EVENT_ENDED = "EVENT_ENDED_BY_VENUE"
D_EVENT_NO_OPEN_MARKET = "EVENT_HAS_NO_OPEN_MARKET"
D_EVENT_OUT_OF_SCOPE_CATEGORY = "EVENT_CATEGORY_IS_NOT_SPORTS"
D_EVENT_ALREADY_READ = "EVENT_ALREADY_READ_THIS_REFRESH"
D_EVENT_FINISHED = "EVENT_FINISHED_PER_VENUE_PERIOD"
D_EVENT_NOT_LIVE_BEFORE_WINDOW = "EVENT_NOT_LIVE_AND_STARTED_BEFORE_THE_WINDOW"
D_EVENT_PAST_END = "EVENT_FUTURES_PAST_THEIR_EVENT_END_DATE"
D_EVENT_NO_ROW_WRITTEN = "EVENT_WROTE_NO_ROW"
D_EVENT_WRITE_FAILED = "EVENT_ROWS_FAILED_TO_WRITE"
D_EVENT_NOT_REACHED = "EVENT_NOT_REACHED_BEFORE_THE_PASS_STOPPED"
D_MARKET_CLOSED = "MARKET_CLOSED_BY_VENUE"
D_MARKET_GAME_FINISHED = "GAME_FINISHED_PER_VENUE_PERIOD"
D_MARKET_GAME_NOT_LIVE_BEFORE_WINDOW = "GAME_NOT_LIVE_AND_STARTED_BEFORE_THE_WINDOW"
D_MARKET_FUTURE_PAST_END = "FUTURE_PAST_ITS_EVENT_END_DATE"
D_MARKET_NO_ORDERABLE_SIDE = "MARKET_WITHOUT_AN_ORDERABLE_SIDE"
D_MARKET_ROWS_REFUSED_BY_SIDE_KEY = "MARKET_ROWS_ALL_REFUSED_BY_THE_SIDE_KEY"
D_MARKET_ROW_BUILD_FAILED = "MARKET_ROWS_FAILED_TO_BUILD_OR_WRITE"
D_MARKET_NOT_REACHED = "MARKET_NOT_REACHED_BEFORE_THE_PASS_STOPPED"
D_SIDE_INCOMPLETE = "SIDE_WITHOUT_IDENTIFIER_OR_DESCRIPTION"
D_SIDE_KEY_HELD_BY_ANOTHER_MARKET = "SIDE_KEY_ALREADY_HELD_BY_ANOTHER_MARKET"
D_FALLBACK_MARKET_WITHOUT_KEY = "DEGRADED_FALLBACK_MARKET_WITHOUT_LOOKUP_KEY"
DROP_REASONS = (D_EVENT_NO_SLUG, D_EVENT_CLOSED, D_EVENT_ENDED,
                D_EVENT_NO_OPEN_MARKET, D_EVENT_OUT_OF_SCOPE_CATEGORY,
                D_EVENT_ALREADY_READ, D_EVENT_FINISHED,
                D_EVENT_NOT_LIVE_BEFORE_WINDOW, D_EVENT_PAST_END,
                D_EVENT_NO_ROW_WRITTEN, D_EVENT_WRITE_FAILED,
                D_EVENT_NOT_REACHED, D_MARKET_CLOSED,
                D_MARKET_GAME_FINISHED, D_MARKET_GAME_NOT_LIVE_BEFORE_WINDOW,
                D_MARKET_FUTURE_PAST_END, D_MARKET_NO_ORDERABLE_SIDE,
                D_MARKET_ROWS_REFUSED_BY_SIDE_KEY, D_MARKET_ROW_BUILD_FAILED,
                D_MARKET_NOT_REACHED, D_SIDE_INCOMPLETE, D_SIDE_KEY_HELD_BY_ANOTHER_MARKET,
                D_FALLBACK_MARKET_WITHOUT_KEY)

#: An event whose every market was dropped carries the event-level form of
#: the market reason that emptied it, so "no open market" never stands in for
#: "finished", "not live" or "past its end" (adversarial review: the stale KBO
#: game, whose only market was MARKET_STATUS_OPEN, was reported as
#: EVENT_HAS_NO_OPEN_MARKET). Priority when the reasons are mixed: the first
#: listed here that any market carried.
EVENT_REASON_FOR_MARKETS = (
    (D_MARKET_GAME_FINISHED, D_EVENT_FINISHED),
    (D_MARKET_FUTURE_PAST_END, D_EVENT_PAST_END),
    (D_MARKET_GAME_NOT_LIVE_BEFORE_WINDOW, D_EVENT_NOT_LIVE_BEFORE_WINDOW),
    (D_MARKET_CLOSED, D_EVENT_NO_OPEN_MARKET),
)


def event_reason_for_markets(reasons) -> str:
    """The event-level reason for an event every market of which was dropped
    for `reasons` (market drop reasons). Pure. No market at all is
    EVENT_HAS_NO_OPEN_MARKET, as before."""
    got = set(reasons or ())
    for market_reason, event_reason in EVENT_REASON_FOR_MARKETS:
        if market_reason in got:
            return event_reason
    return D_EVENT_NO_OPEN_MARKET

# kept, with a note (never a drop)
K_KEPT_WITHOUT_EVENT_KEY = "KEPT_WITHOUT_EVENT_LOOKUP_KEY"
K_SIDE_KEY_QUALIFIED = "SIDE_KEY_QUALIFIED_BY_THE_VENUES_MARKER"

# ── how a pass ended ─────────────────────────────────────────────────────
STOP_SHORT_PAGE = "SHORT_PAGE"                 # the board ended on a page shorter than the venue's page size
STOP_EMPTY_PAGE = "EMPTY_PAGE"                 # the next offset answered nothing
STOP_BUDGET = "REQUEST_BUDGET_EXHAUSTED"       # pages were still full when the budget ran out: TRUNCATED
STOP_WALL_TIME = "WALL_TIME_BUDGET_EXHAUSTED"  # pages were still full when the time bound ran out: TRUNCATED
STOP_RATE_LIMITED = "RATE_LIMITED_BY_VENUE"    # a 429: the pass stops, the venue_pace circuit is applied
STOP_ERROR = "REQUEST_FAILED"                  # any other failed request
STOP_NO_VARIANT = "NO_PARAMETER_VARIANT_ANSWERED"
STOP_WRITE_FAILURES = "CATALOGUE_WRITES_FAILING"  # MAX_CONSECUTIVE_EVENT_FAILURES events in a row failed to write
STOP_NOT_RUN = "NOT_RUN"                       # an earlier pass stopped the lane (a 429) or the budget was spent
#: the next offset is past what the venue serves -- a configured ceiling
#: (`PageWalk(max_offset=...)`) or the venue's own refusal of the offset --
#: while pages were still full: TRUNCATED, and the window is partitioned
STOP_OFFSET_CEILING = "OFFSET_CEILING_REACHED"
#: a truncated time window recursively partitioned until every bucket ended
#: on a natural end of the venue's board (see WindowPartition)
STOP_PARTITION_COMPLETE = "PARTITIONED_TO_A_NATURAL_END"
#: a truncated time window whose partition left buckets unresolved (each
#: named with its window and why in `partition.unresolved`): TRUNCATED
STOP_PARTITION_UNRESOLVED = "PARTITION_LEFT_BUCKETS_UNRESOLVED"
#: (completion readiness) the process crossed its memory budget while pages
#: were still full (the shared workers were OOM-killed at 2 GiB while broad
#: catalogue work ran beside the capital-critical loops): TRUNCATED, never
#: complete -- the next cycle resumes on a lighter process
STOP_MEMORY_BUDGET = "MEMORY_BUDGET_EXHAUSTED"
STOPS = (STOP_SHORT_PAGE, STOP_EMPTY_PAGE, STOP_BUDGET, STOP_WALL_TIME,
         STOP_RATE_LIMITED, STOP_ERROR, STOP_NO_VARIANT, STOP_WRITE_FAILURES,
         STOP_NOT_RUN, STOP_OFFSET_CEILING, STOP_PARTITION_COMPLETE,
         STOP_PARTITION_UNRESOLVED, STOP_MEMORY_BUDGET)
NATURAL_ENDS = frozenset({STOP_SHORT_PAGE, STOP_EMPTY_PAGE,
                          STOP_PARTITION_COMPLETE})
TRUNCATING_STOPS = frozenset({STOP_BUDGET, STOP_WALL_TIME, STOP_OFFSET_CEILING,
                              STOP_PARTITION_UNRESOLVED, STOP_MEMORY_BUDGET})

#: How many events of the previous page each next request reads again. Offset
#: pagination over a board that changes while it is walked (games closing,
#: games listing) shifts every later event by one position per deletion ahead
#: of the cursor; without an overlap that event falls between two pages and is
#: never read. Five per page is ~5% more data per page, no extra request. It
#: catches a shift of AT MOST five per boundary; a larger one is detected (the
#: page re-reads none of the previous page's tail) and answered by REWIND.
PAGE_OVERLAP = 5
#: At most this many re-reads per pass for shifts larger than the overlap, each
#: one request inside the pass's own budget, reaching one page back.
MAX_REWINDS = 3

#: A refresh stops a pass after this many events IN A ROW failed to write (a
#: database that is down fails every event; one malformed listing fails one).
#: One failed event is isolated, counted with its markets, and the walk goes on.
MAX_CONSECUTIVE_EVENT_FAILURES = 5

#: A non-future event that started before the window, is not live, and whose
#: OWN end date is still ahead, is kept by STARTED_EARLIER only when the venue
#: scheduled it to span at least this long (endDate - start): a multi-day
#: event between its sessions -- a cricket test at stumps, a golf round
#: overnight. WHAT IS AND IS NOT OBSERVED: the midterms' EVENT spans 23 h 59 min
#: (fetch-docs 37233823157) and would not qualify; a single game's MARKET
#: endDate equals its start (the az-col fixture), but a single game's EVENT
#: endDate has not been observed. If the venue dates a game's event to end a
#: day or more after its start, this rule would keep that game until then --
#: so every listing kept this way is counted BY SPORT in the receipt
#: (`notes`, `started_earlier_kept:MULTI_DAY_SCHEDULE:<sport>`), and the first
#: production receipt shows whether anything but multi-day sports lands here.
MULTI_DAY_MIN_SPAN_H = 24.0

#: The receipt keeps at most this many (sport, league, family) cells by
#: listings seen; the rest are summed into one `_other` cell, so the receipt is
#: bounded however large the board grows and the totals still reconcile.
MAX_RECEIPT_CELLS = 300
MAX_EXAMPLES = 12

_SLUG_TOKEN_RE = re.compile(r"^[a-z0-9]+$")


# ── classification (pure) ───────────────────────────────────────────────

def sport_of(sports_market_type) -> str:
    """The venue sportsMarketType's leading word ('table_tennis' kept whole),
    or '(none)' when the venue states no type."""
    st = str(sports_market_type or "").strip().lower()
    if not st:
        return "(none)"
    if st.startswith("table_tennis"):
        return "table_tennis"
    return st.split("_", 1)[0] or "(none)"


def league_of(event_slug) -> str:
    """The venue event slug's first segment (`nfl-ind-was-2026-10-04` ->
    'nfl'), or '(none)'."""
    head = str(event_slug or "").strip().lower().split("-", 1)[0]
    return head if head and _SLUG_TOKEN_RE.match(head) else "(none)"


#: Whole-game yes/no props, by the venue's last word(s) (research-sql
#: 37238634518 R1: soccer_game_btts, football_game_overtime, hockey_game_shootout,
#: football_game_safety, football_game_tie, ufc_go_the_distance,
#: baseball_game_extra_innings).
_YES_NO_ENDINGS = ("_btts", "_overtime", "_shootout", "_safety", "_tie",
                   "_go_the_distance", "_extra_innings")
_PERIOD_WORDS = frozenset({"half", "quarter", "period"})
_PERIOD_NUMBERED = frozenset({"set", "frame", "leg", "map", "game"})


def _is_period(st: str) -> bool:
    """A market on a PART of the contest, read off the type's words.

    ANCHORED, NOT A SUBSTRING (adversarial review: the substring rule
    `_map|frame|_set_` ran before the total / spread checks and filed whole-
    match markets as periods -- esports_series_total_maps, tennis_match_
    sets_spread, snooker_frame_handicap). A part is: a half, quarter or
    period; a numbered inning (inning1..inning9) or the first five / first
    inning; or a NUMBERED set, frame, leg, map or game (tennis_set_1_winner,
    esports_map_winner_1, esports_map_total_rounds_2, esports_game_total_
    kills_3). `sets`, `maps`, `games` (plural) and an un-numbered
    `map_handicap` / `game_handicap` / `set_handicap` are the whole match."""
    words = st.split("_")
    if _PERIOD_WORDS & set(words):
        return True
    if any(re.fullmatch(r"inning\d+", w) for w in words):
        return True
    if "first_five" in st or "first_inning" in st:
        return True
    for i, w in enumerate(words[:-1]):
        if w in _PERIOD_NUMBERED and words[i + 1].isdigit():
            return True
    if words[-1].isdigit() and ({"map", "game"} & set(words)):
        return True
    return False


def family_of(sports_market_type, *, slug=None, n_sides=None) -> str:
    """The market family, from the venue's OWN type text.

    The order matters: a player prop is a prop before it is a total, a
    period market is a period before it is a winner or a total, a team total
    is a team total before it is a total. A market with no type but a yes/no
    pair is YES_NO; with none at all, NO_TYPE -- never guessed into a family
    it may not be. Checked against every one of the 215 sportsMarketType
    values production held on 2026-10-04 (research-sql run 37238634518 R1;
    tests/test_venue_catalogue_is_complete.py pins a sample of each family)."""
    st = str(sports_market_type or "").strip().lower()
    if not st:
        return F_YES_NO if n_sides == 2 and str(slug or "").startswith(
            ("cpc-", "paccc-")) else F_NO_TYPE
    if st == "futures" or st.startswith("futures"):
        return F_FUTURES
    if "_player_" in st:
        return F_PLAYER_PROP
    if _is_period(st):
        return F_PERIOD
    if "exact_score" in st or "correct_score" in st or "double_result" in st \
            or "exact_margin" in st or "margin_of_victory" in st:
        return F_MULTI_OUTCOME
    if "spread" in st or "handicap" in st:
        return F_SPREAD
    if "team_total" in st or "team_points" in st or (
            st.startswith(("football_team_total", "baseball_team_total",
                           "hockey_team_total", "basketball_team_total"))):
        return F_TEAM_TOTAL
    if "total" in st:
        return F_TOTAL
    if st.endswith("winner"):
        return F_WINNER
    if st in ("election",) or st.endswith(_YES_NO_ENDINGS):
        return F_YES_NO
    return F_OTHER


def _epoch(raw):
    """A venue instant (ISO with an offset, or epoch seconds) as epoch
    seconds, or None. A naive ISO string is no instant."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, datetime):
        return raw.timestamp() if raw.tzinfo is not None else None
    s = str(raw).strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        return None
    return d.timestamp() if d.tzinfo is not None else None


def event_start(ev: dict, market: dict | None = None):
    """The event's start as the venue states it (epoch seconds) or None:
    the market's gameStartTime first (what `us_premap.game_start` stores),
    then the event's startTime / startDate."""
    for raw in ((market or {}).get("gameStartTime"), (ev or {}).get("startTime"),
                (ev or {}).get("startDate")):
        e = _epoch(raw)
        if e is not None:
            return e
    return None


def event_end(ev: dict):
    """The EVENT's own end (`endDate`, else `endTime`) as epoch seconds, or
    None. Never a market's endDate: that is the contract's expiry (2026-11-06
    on the MLB futures whose events end 2026-10-20 / 2026-11-01), and on a
    single game it equals the game's start -- see the module docstring."""
    ev = ev or {}
    for raw in (ev.get("endDate"), ev.get("endTime")):
        e = _epoch(raw)
        if e is not None:
            return e
    return None


def period_word(ev: dict) -> str:
    """The venue's `period` word, upper-cased and stripped ('' when none)."""
    return str((ev or {}).get("period") or "").strip().upper().replace(" ", "")


def period_is_final(ev: dict) -> bool:
    return period_word(ev) in FINAL_PERIOD_WORDS


def listing_state(ev: dict, now: float, market: dict | None = None) -> tuple:
    """(state, source) for one event at `now`.

    The venue's own words first, in this order: `ended: true` is ENDED; `live:
    true` is LIVE; a FINAL period word (FT, FINAL, AET, ...) is ENDED by the
    venue's period (a finished game the venue has not closed is not "a
    break"); `period: "NS"` is PREGAME; `live: false` before the start is
    PREGAME and after it NOT_LIVE (a break, a delay, a suspension, or a period
    word not known here). `live` is NOT on every event (the two politics
    events of fetch-docs 37233823157 carried none), so with no venue flag and
    no known period word the schedule decides, and the source then says
    SCHEDULE_ESTIMATE so no reader mistakes an estimate for the venue's word.
    """
    ev = ev or {}
    start = event_start(ev, market)
    if ev.get("ended") is True:
        return S_ENDED, SRC_VENUE_ENDED_FLAG
    live = ev.get("live")
    if live is True:
        return S_LIVE, SRC_VENUE_LIVE_FLAG
    word = period_word(ev)
    if word in FINAL_PERIOD_WORDS:
        return S_ENDED, SRC_VENUE_PERIOD
    if isinstance(live, bool):
        if word in PREGAME_PERIOD_WORDS or (start is not None and start > now):
            return S_PREGAME, SRC_VENUE_LIVE_FLAG
        return S_NOT_LIVE, SRC_VENUE_LIVE_FLAG
    if word in PREGAME_PERIOD_WORDS:
        return S_PREGAME, SRC_VENUE_PERIOD
    if start is None:
        return S_UNKNOWN, SRC_NONE
    return (S_PREGAME if start > now else S_STARTED), SRC_SCHEDULE


def venue_market_count(ev: dict):
    """The venue's own count of markets on the event
    (`marketCounts.numMarkets`), or None when it states none."""
    mc = (ev or {}).get("marketCounts")
    if not isinstance(mc, dict):
        return None
    n = mc.get("numMarkets")
    if isinstance(n, bool):
        return None
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


#: THE CATEGORY RULE OF THE CALENDAR LANE: DENY ONLY WHAT IS KNOWN NOT TO BE
#: SPORT, AND NEVER AGAINST SPORTS EVIDENCE (adversarial review: the first pass
#: kept only `sports` / `esports` and dropped every other word, so a sports
#: event filed under a word nobody had observed -- `soccer`, `golf` -- would
#: have been dropped on a guess).
#:
#: An event is OUT OF SCOPE only when its category is one of the words below
#: AND it carries no sports evidence: no `sports` / `esports` category, no tag
#: (or sub-tag) with a sports slug, and no market whose sportsMarketType names
#: a sport. Observed: `politics` (the midterms, fetch-docs 37233823157, tags
#: `politics` / `midterms`, sportsMarketType `election`); `sports` (the MLB
#: futures, fetch-docs 37233829391, tags `sports`, `mlb`, `baseball`). The other
#: words are non-sports by their meaning. A category word on NEITHER list is
#: KEPT and counted by name (`notes`, `category_unrecognised_kept:<word>`), so
#: the first production receipt shows every word the venue uses; a dropped one
#: is counted the same way (`category_dropped:<word>`).
SPORTS_CATEGORIES = frozenset({"sports", "esports"})
SPORTS_TAG_SLUGS = frozenset({"sports", "esports"})
NON_SPORTS_CATEGORIES = frozenset({
    "politics", "elections", "election", "crypto", "cryptocurrency",
    "economics", "economy", "finance", "financials", "business", "companies",
    "culture", "pop-culture", "entertainment", "music", "movies", "awards",
    "weather", "climate", "science", "tech", "technology", "world",
    "geopolitics", "mentions", "commodities"})
#: sportsMarketType words that name no sport: the weather markets carry
#: `futures` (research-sql 37236398336 B1b: temp-nychigh-2026-10-04 and four
#: more), the midterms `election`.
_NON_SPORT_TYPES = frozenset({"futures", "election", "(none)"})


def _tag_slugs(tags) -> set:
    out: set = set()
    stack = list(tags or [])
    while stack and len(out) < 200:
        t = stack.pop()
        if not isinstance(t, dict):
            continue
        s = str(t.get("slug") or "").strip().lower()
        if s:
            out.add(s)
        stack.extend(t.get("subtags") or [])
    return out


def sports_evidence(ev: dict) -> str | None:
    """Which venue word says this event is sport, or None. Pure."""
    ev = ev or {}
    if str(ev.get("category") or "").strip().lower() in SPORTS_CATEGORIES:
        return "category"
    if _tag_slugs(ev.get("tags")) & SPORTS_TAG_SLUGS:
        return "tag"
    for m in ev.get("markets") or []:
        if isinstance(m, dict) and sport_of(
                m.get("sportsMarketType")) not in _NON_SPORT_TYPES:
            return "market_type"
    return None


def category_verdict(ev: dict) -> str:
    """'SPORTS' (evidence), 'NON_SPORTS' (a known non-sports word and no
    evidence), 'UNRECOGNISED' (a word on neither list, kept) or 'NONE' (no
    category, kept)."""
    if sports_evidence(ev):
        return "SPORTS"
    cat = str((ev or {}).get("category") or "").strip().lower()
    if not cat:
        return "NONE"
    return "NON_SPORTS" if cat in NON_SPORTS_CATEGORIES else "UNRECOGNISED"


def event_drop_reason(ev: dict, *, pass_name: str) -> str | None:
    """Why an event is not read at all, or None. Pure.

    Closed / archived / ended are the venue's own flags and drop on every
    pass. The CATEGORY rule applies only to the calendar lane's two passes
    (AHEAD, STARTED_EARLIER): beyond the sports window the venue's calendar
    holds politics (fetch-docs run 37233823157: the first events past +96 h
    were the U.S. House and Senate midterms), and reading them would spend the
    request budget on listings no supported sport can map. The WINDOW and FAST
    passes keep every category, exactly as before -- this repair removes no
    row the sweep wrote."""
    ev = ev or {}
    if not (ev.get("slug") or ev.get("eventSlug")):
        return D_EVENT_NO_SLUG
    if ev.get("closed") is True or ev.get("archived") is True:
        return D_EVENT_CLOSED
    if ev.get("ended") is True:
        return D_EVENT_ENDED
    if pass_name in CALENDAR_PASSES and category_verdict(ev) == "NON_SPORTS":
        return D_EVENT_OUT_OF_SCOPE_CATEGORY
    return None


def market_drop_reason(ev: dict, market: dict, *, pass_name: str,
                       now: float) -> str | None:
    """Why one inline market is not written, or None. Pure.

    `closed` is the venue's flag and was the only market filter before; it
    stays the only one on the WINDOW, FAST and AHEAD passes. A market whose
    status reads anything other than OPEN is KEPT and counted: an in-play
    suspension is a live market for a moment, and dropping it would lose the
    live counterpart exactly when it matters.

    STARTED_EARLIER reads events that started before the window's back edge
    (12 h ago and earlier). What it exists to keep, and nothing else:
      * a FUTURE (sportsMarketType `futures`) while its EVENT's end date is
        ahead or unstated -- "National League Champion" started 2026-09-07 and
        ends 2026-10-20. A future past its event's end (a series winner whose
        series is over, a qualifying session already run -- research-sql
        37236398336 B1b) is the venue's resolution backlog, not a tradable
        listing: FUTURE_PAST_ITS_EVENT_END_DATE. (The same-day weather
        markets carry the `futures` type too; they are left out by the
        category rule when the venue files them under a non-sports word, and
        counted by their word either way.)
      * a game market on an event the venue flags `live` (a long match still
        in play);
      * a game market on an event scheduled to span MULTI_DAY_MIN_SPAN_H or
        more whose own end date is still ahead (a multi-day event between
        sessions, which the venue need not flag live overnight).
    Every other game market is a game that is over or not running, and is
    dropped by name: GAME_FINISHED_PER_VENUE_PERIOD when the venue's period
    word is final, GAME_NOT_LIVE_AND_STARTED_BEFORE_THE_WINDOW otherwise.
    (The first pass kept such games for up to 48 h after their start under
    the label NOT_LIVE; the adversarial review measured that against the
    before/after estimate it contradicted.)
    """
    if not isinstance(market, dict) or market.get("closed") is True:
        return D_MARKET_CLOSED
    if pass_name != PASS_STARTED_EARLIER:
        return None
    ev = ev or {}
    end = event_end(ev)
    fam = family_of(market.get("sportsMarketType"), slug=market.get("slug"))
    if fam == F_FUTURES:
        if end is not None and end < now:
            return D_MARKET_FUTURE_PAST_END
        return None
    if ev.get("live") is True:
        return None
    if period_is_final(ev):
        return D_MARKET_GAME_FINISHED
    start = event_start(ev)
    if (end is not None and end > now and start is not None
            and end - start >= MULTI_DAY_MIN_SPAN_H * 3600.0):
        return None
    return D_MARKET_GAME_NOT_LIVE_BEFORE_WINDOW


K_SE_FUTURE_RUNNING = "FUTURE_EVENT_STILL_RUNNING"
K_SE_VENUE_LIVE = "VENUE_FLAGS_LIVE"
K_SE_MULTI_DAY = "MULTI_DAY_SCHEDULE"


def started_earlier_keep_rule(ev: dict, market: dict) -> str:
    """Which STARTED_EARLIER rule kept a market market_drop_reason passed
    (call it only for those). Pure. Counted by sport in the receipt so each
    rule's population is visible."""
    fam = family_of((market or {}).get("sportsMarketType"),
                    slug=(market or {}).get("slug"))
    if fam == F_FUTURES:
        return K_SE_FUTURE_RUNNING
    if (ev or {}).get("live") is True:
        return K_SE_VENUE_LIVE
    return K_SE_MULTI_DAY


# ── pagination ───────────────────────────────────────────────────────────

class PageWalk:
    """One pass's offset pagination as a state machine the caller drives.

        walk = PageWalk(limit=100, max_requests=120)
        walk.probe_failed()                      # each probe rung that failed
        walk.probe_rejected()                    # each rung that answered unusably
        walk.first(probe_events)                 # the winning probe's page, if any
        while (off := walk.next_offset()) is not None:
            walk.accept(fetch(off))              # or walk.fail(STOP_..., why)

    `accept` returns the events of that page not already read this pass, in
    order; `receipt()` says how the pass ended. Never raises.

    EVERY REQUEST IS COUNTED (adversarial review: probe rungs that failed or
    answered without live inline markets were sent but never counted, so the
    receipt said 3 requests for 4 sent, and 0 for a window probe that drew a
    429). `probe_failed` / `probe_rejected` count them inside the same budget,
    so `requests` equals the paced claims the pass made, on every path.

    THE WAYS OFFSET PAGINATION LOSES LISTINGS, AND THE ANSWER TO EACH:
      1. A page shorter than requested was read as the end of the board. If
         the venue ever caps its page size below `limit`, that rule turns the
         sweep into first-page-only and reports nothing. Here a short page
         ends the pass only when it is shorter than the largest page this
         pass has seen; a short page that is the largest so far (the first
         one, or a cap) is CONFIRMED by reading the next offset -- empty
         means the board really ended, events mean the venue's page size is
         smaller than asked and paging continues at that size.
      2. The board moved during the walk. Every next request starts
         PAGE_OVERLAP events before the end of the previous page; events seen
         twice are counted, never written twice, and an event first seen in
         the overlap is a shift the walk caught (`overlap_catches`). THE
         OVERLAP CATCHES AT MOST PAGE_OVERLAP SHIFTED EVENTS PER BOUNDARY. A
         page that re-reads NONE of the previous page's tail moved by the
         overlap or more (or its tail closed): the walk re-reads one page back
         (REWIND, at most MAX_REWINDS per pass, inside the request budget)
         and resumes where it was. A rewind page that holds no event the pass
         already read could not reach the shift either: `shift_unrecovered`,
         and the pass is not complete (`complete` False) even if the board
         then ends naturally.
      3. The budget ran out -- requests, or wall time when a deadline is set.
         That is REQUEST_BUDGET_EXHAUSTED / WALL_TIME_BUDGET_EXHAUSTED and
         truncated, never a quiet success.
    """

    def __init__(self, *, limit: int, max_requests: int,
                 overlap: int = PAGE_OVERLAP, start_offset: int = 0,
                 deadline: float | None = None, clock=None,
                 max_offset: int | None = None,
                 already_read: set | None = None,
                 memory_guard=None):
        import time as _time

        #: (completion readiness) a callable answering True when the process
        #: is over its memory budget: the walk then stops TRUNCATED
        #: (MEMORY_BUDGET_EXHAUSTED) before asking for another page
        self.memory_guard = memory_guard
        self.limit = max(1, int(limit))
        self.max_requests = max(1, int(max_requests))
        self.overlap = max(0, int(overlap))
        self.deadline = deadline
        #: the largest offset the venue serves, when one is known: the next
        #: offset past it stops the walk as OFFSET_CEILING_REACHED (truncated)
        #: instead of asking for a page the venue refuses
        self.max_offset = (int(max_offset) if max_offset not in (None, 0)
                           else None)
        #: event keys another walk of the SAME enumeration already read (a
        #: partition's parent window, its sibling buckets): such an event is
        #: counted (`already_read_elsewhere`) and never returned again, so
        #: overlapping buckets write and tally every event once. The set is
        #: shared and grows with every fresh event this walk reads.
        self._already_read = already_read
        self.already_read_elsewhere = 0
        self._clock = clock or _time.monotonic
        self._offset = int(start_offset)
        self._next = int(start_offset)
        self.requests = 0
        self.probe_requests_failed = 0
        self.probe_requests_rejected = 0
        self.pages = 0
        self.events_received = 0
        self.duplicates = 0
        self.overlap_catches = 0
        self.shift_suspected = 0
        self.rewind_reads = 0
        self.rewind_catches = 0
        self.shift_unrecovered = 0
        self.page_size_max = 0
        self.venue_page_cap = None
        self.stopped = None
        self.error = None
        self._seen: set = set()
        self._confirming = False
        self._last_tail: list = []
        self._rewinding = False
        self._resume = None
        self._resume_tail: list = []
        self._rewind_exclude: set = set()

    @staticmethod
    def _key(ev) -> str | None:
        if not isinstance(ev, dict):
            return None
        s = ev.get("slug") or ev.get("eventSlug") or ev.get("id")
        return str(s) if s else None

    def probe_failed(self) -> None:
        """A probe rung's request that raised (not a 429 the caller stops
        on -- that is `fail` after this): one request, no page."""
        self.requests += 1
        self.probe_requests_failed += 1

    def probe_rejected(self) -> None:
        """A probe rung that answered, but not with a usable page (no live
        inline markets): one request, no page."""
        self.requests += 1
        self.probe_requests_rejected += 1

    def first(self, events) -> list:
        """The probe page (offset 0), already fetched by the variant ladder:
        counted as a request and accepted like any page."""
        return self.accept(events)

    def next_offset(self):
        """The offset to request next, or None when the pass is over."""
        if self.stopped is not None:
            return None
        if self.requests >= self.max_requests:
            self.stopped = STOP_BUDGET
            return None
        if self.deadline is not None and self._clock() >= self.deadline:
            self.stopped = STOP_WALL_TIME
            return None
        if self.memory_guard is not None:
            try:
                over = bool(self.memory_guard())
            except Exception:                                 # noqa: BLE001
                over = False
            if over:
                self.stopped = STOP_MEMORY_BUDGET
                return None
        if self.max_offset is not None and self._next > self.max_offset:
            self.stopped = STOP_OFFSET_CEILING
            return None
        return self._next

    def seen_keys(self) -> set:
        """Every event key this walk read (a copy)."""
        return set(self._seen)

    def _elsewhere(self, k) -> bool:
        """True when another walk of the same enumeration already read `k`
        (counted); otherwise `k` is claimed for this walk."""
        if self._already_read is None:
            return False
        if k in self._already_read:
            self.already_read_elsewhere += 1
            return True
        self._already_read.add(k)
        return False

    def fail(self, stop: str, why: str | None = None) -> None:
        self.stopped = stop if stop in STOPS else STOP_ERROR
        self.error = (str(why)[:200] if why else None)

    def _accept_rewind(self, events) -> list:
        """The re-read one page back: its fresh events are the ones the
        shift had carried past the cursor. Paging then resumes where it was;
        a rewind never ends the pass (an empty or wholly unknown rewind page
        is a shift it could not reach, counted). REACHED means the rewind
        page holds an event read on an EARLIER page than the one that raised
        the alarm: only then does it join already-read ground, with no gap
        left between. (The page that raised the alarm always shares its first
        events with the rewind page, so those prove nothing.)"""
        self._rewinding = False
        fresh, known = [], 0
        for ev in events:
            k = self._key(ev)
            if k is None:
                fresh.append(ev)
                continue
            if k in self._seen:
                if k not in self._rewind_exclude:
                    known += 1
                self.duplicates += 1
                continue
            self._seen.add(k)
            self.rewind_catches += 1
            if self._elsewhere(k):
                continue
            fresh.append(ev)
        if events:
            self.pages += 1
            self.events_received += len(events)
        if known == 0:
            self.shift_unrecovered += 1
        self._next = self._resume
        self._last_tail = self._resume_tail
        self._resume, self._resume_tail = None, []
        self._rewind_exclude = set()
        return fresh

    def accept(self, events) -> list:
        events = [e for e in (events or []) if isinstance(e, dict)]
        self.requests += 1
        if self._rewinding:
            return self._accept_rewind(events)
        n = len(events)
        if n == 0:
            self.stopped = STOP_EMPTY_PAGE
            self._confirming = False
            return []
        self.pages += 1
        self.events_received += n
        overlap_region = set(self._last_tail)
        expected_overlap = len(overlap_region)
        fresh = []
        tail_hits = 0
        for i, ev in enumerate(events):
            k = self._key(ev)
            if k is None:
                fresh.append(ev)          # keyless: the caller drops it by name
                continue
            if k in overlap_region:
                tail_hits += 1
            if k in self._seen:
                self.duplicates += 1
                continue
            self._seen.add(k)
            if i < expected_overlap:
                # the head of this page re-reads the previous page's tail; an
                # event there that was NOT on the previous page moved across
                # the boundary while the board changed -- caught, not skipped
                self.overlap_catches += 1
            if self._elsewhere(k):
                continue
            fresh.append(ev)
        was_confirming = self._confirming
        self._confirming = False
        prior_max = self.page_size_max
        self.page_size_max = max(self.page_size_max, n)
        page_size = self.venue_page_cap or self.limit
        if n >= page_size:
            pass                                   # a full page: keep walking
        elif prior_max and n < prior_max:
            self.stopped = STOP_SHORT_PAGE         # shorter than pages already seen: the end
        elif was_confirming:
            # the confirmation read answered events: the venue serves pages of
            # at most `prior_max` (or this size) -- a cap, not the end
            self.venue_page_cap = max(prior_max, n)
        else:
            self._confirming = True                # the largest page so far is short: confirm
        tail_n = min(self.overlap, max(0, n // 2))
        tail = [self._key(e) for e in events[n - tail_n:]] if tail_n else []
        step = max(1, n - tail_n)
        if self._confirming:
            step = n                               # confirm at the very next offset, no overlap
            tail = []
        requested_at = self._next
        self._offset = requested_at
        self._next = requested_at + step
        self._last_tail = tail
        if (expected_overlap and tail_hits == 0 and self.stopped is None):
            # NONE of the previous page's tail came back: the board shifted by
            # the overlap or more since the last read (or that tail closed).
            # Re-read one page back before going on, within the budget.
            self.shift_suspected += 1
            if self.rewind_reads < MAX_REWINDS:
                self.rewind_reads += 1
                self._rewinding = True
                self._resume, self._resume_tail = self._next, self._last_tail
                self._rewind_exclude = {self._key(e) for e in events}
                back = max(1, (self.venue_page_cap or self.limit) - self.overlap)
                self._next = max(0, requested_at - back)
            else:
                self.shift_unrecovered += 1
        return fresh

    @property
    def truncated(self) -> bool:
        return self.stopped in TRUNCATING_STOPS

    @property
    def complete(self) -> bool:
        return self.stopped in NATURAL_ENDS and self.shift_unrecovered == 0

    def receipt(self) -> dict:
        return {"requests": self.requests, "pages_with_events": self.pages,
                "probe_requests_failed": self.probe_requests_failed,
                "probe_requests_rejected": self.probe_requests_rejected,
                "events_received": self.events_received,
                "events_unique": len(self._seen),
                "duplicates_across_pages": self.duplicates,
                "overlap": self.overlap,
                "overlap_catches": self.overlap_catches,
                "shift_suspected": self.shift_suspected,
                "rewind_reads": self.rewind_reads,
                "rewind_catches": self.rewind_catches,
                "shift_unrecovered": self.shift_unrecovered,
                "page_size_requested": self.limit,
                "page_size_max_seen": self.page_size_max,
                "venue_page_cap": self.venue_page_cap,
                "max_requests": self.max_requests,
                "max_offset": self.max_offset,
                "already_read_elsewhere": self.already_read_elsewhere,
                "stopped": self.stopped, "error": self.error,
                "natural_end": self.stopped in NATURAL_ENDS,
                "complete": self.complete,
                "truncated": self.truncated}


def rollup_slices(slices: list, *, not_read: list | None = None,
                  duration_s: float | None = None) -> dict:
    """One calendar pass's receipt from its slices' PageWalk receipts
    (nearest slice first). Pure.

    The pass is TRUNCATED when any slice ran out of budget or wall time;
    RATE_LIMITED / WRITES_FAILING / FAILED when a slice stopped that way (the
    slices after it are listed unread); TRUNCATED again when slices were left
    unread only because the budget was spent; otherwise it ends the way its
    last slice did (a natural end). Counters are summed, so the receipt's
    request total is every request the pass made."""
    slices = [dict(x) for x in (slices or [])]
    not_read = list(not_read or [])
    stops = [x.get("stopped") for x in slices]
    stopped = None
    for x in slices:
        if x.get("stopped") in TRUNCATING_STOPS:
            stopped = x.get("stopped")
            break
    if stopped is None:
        for st in (STOP_RATE_LIMITED, STOP_WRITE_FAILURES, STOP_ERROR):
            if st in stops:
                stopped = st
                break
    if stopped is None and not_read:
        # the slices left unread because the pass's request budget, or the
        # lane's wall time, was spent -- named by which
        whys = [str((x or {}).get("why") if isinstance(x, dict) else "")
                for x in not_read]
        stopped = next((w for w in whys if w in TRUNCATING_STOPS), STOP_BUDGET)
    if stopped is None:
        stopped = stops[-1] if stops else STOP_NOT_RUN
    summed = {}
    for k in ("requests", "pages_with_events", "events_received",
              "events_unique", "duplicates_across_pages", "overlap_catches",
              "shift_suspected", "rewind_reads", "rewind_catches",
              "shift_unrecovered", "probe_requests_failed",
              "probe_requests_rejected"):
        summed[k] = sum(int(x.get(k) or 0) for x in slices)
    err = next((x.get("error") for x in slices if x.get("error")), None)
    out = dict(summed, stopped=stopped, error=err,
               natural_end=stopped in NATURAL_ENDS,
               truncated=stopped in TRUNCATING_STOPS,
               slices=slices, slices_not_read=not_read)
    out["complete"] = out["natural_end"] and not summed["shift_unrecovered"]
    if duration_s is not None:
        out["duration_s"] = round(float(duration_s), 3)
    return out


# ── recursive partition of a truncated time window ───────────────────────
#
# THE OWNER (2026-10-06): "The catalogue walker must make truncation explicit
# and recursively partition the enumeration space until completeness is
# established or the API proves a genuine external limitation. A result
# marked truncated=true may NEVER be treated as a complete venue universe. No
# active market may disappear because it fell past an offset ceiling."
#
# A time-windowed walk (the WINDOW / FAST pass, each calendar slice) that ends
# TRUNCATED -- its request budget ran out, or the next offset is past what the
# venue serves, while pages were still full -- has read only the head of its
# window in the venue's offset order. WindowPartition splits that
# [startTimeMin, startTimeMax] window in halves and walks each half with the
# same PageWalk (and the same writer and tally), recursing into any half that
# is truncated again, until every bucket ends on a natural end of the board.
# A bucket still truncated when it is MAX_DEPTH splits deep or no wider than
# MIN_WINDOW_S cannot be cut further: it is named PROVIDER_BUCKET_REMAINS_
# TRUNCATED with its window (the venue's genuine limit, proven), and the pass
# stays TRUNCATED. The partition has its OWN request budget (and the lane's
# wall-time bound), recorded on the receipt; buckets left unwalked when it is
# spent are named, never assumed empty. Halves share their midpoint instant
# (the venue's bounds are treated as inclusive, so nothing falls between two
# buckets); an event read in two buckets is written and counted once
# (PageWalk `already_read`). Pure: the caller (premap.refresh) drives it.

P_REMAINS_TRUNCATED = "PROVIDER_BUCKET_REMAINS_TRUNCATED"
P_UNBOUNDED = "UNBOUNDED_VARIANT_CANNOT_BE_PARTITIONED"
P_SHIFT = "BOARD_SHIFT_UNRECOVERED_IN_BUCKET"
P_BUDGET = "PARTITION_BUDGET_EXHAUSTED"
#: at most this many halvings below the window first walked: a 108 h window
#: halved 10 times is ~6 min, below MIN_WINDOW_S anyway
PARTITION_MAX_DEPTH = 10
#: a bucket no wider than this is not split again (the same floor as
#: market_plane.catalogue.enumerate_complete)
PARTITION_MIN_WINDOW_S = 900.0
#: the receipt lists at most this many buckets (the rest are counted)
MAX_PARTITION_BUCKETS_LISTED = 256
_PARTITION_SUM_KEYS = ("requests", "pages_with_events", "events_received",
                       "duplicates_across_pages", "overlap_catches",
                       "shift_suspected", "rewind_reads", "rewind_catches",
                       "probe_requests_failed", "probe_requests_rejected")


def split_window(start: float, end: float) -> tuple:
    """((start, mid), (mid, end)) -- the two halves share their midpoint,
    rounded to a whole second (the venue's filter is second-resolution)."""
    a, b = float(start), float(end)
    mid = float(int((a + b) / 2.0))
    if not a < mid < b:
        mid = (a + b) / 2.0
    return (a, mid), (mid, b)


def _bucket_iso(t):
    return utc_iso(t) if t is not None else None


class WindowPartition:
    """The recursive partition of ONE pass's truncated time window(s), as a
    planner the caller drives:

        part = WindowPartition(pass_name="WINDOW", budget=120,
                               bucket_max_requests=120)
        part.record(lo, hi, 0, root_walk.receipt(), root=True)
        while (b := part.next_bucket()) is not None:
            lo, hi, depth, max_requests = b
            walk = PageWalk(limit=..., max_requests=max_requests,
                            already_read=shared)
            ... drive the walk over startTimeMin=lo, startTimeMax=hi ...
            part.record(lo, hi, depth, walk.receipt())

    `record` decides what a walked bucket means: complete, split into halves
    (truncated with room to split), or unresolved by name. `next_bucket`
    hands out the next unwalked bucket nearest-first while the partition's
    own budget and the deadline last; when either runs out (or a bucket
    stopped on a 429 / failed request / failing writes) every bucket still
    pending is named unresolved with why. Never raises."""

    def __init__(self, *, pass_name: str, budget: int,
                 bucket_max_requests: int,
                 max_depth: int = PARTITION_MAX_DEPTH,
                 min_window_s: float = PARTITION_MIN_WINDOW_S,
                 nearest_high: bool = False,
                 deadline: float | None = None, clock=None):
        import time as _time

        self.pass_name = pass_name
        self.budget = max(0, int(budget))
        self.bucket_max_requests = max(1, int(bucket_max_requests))
        self.max_depth = max(0, int(max_depth))
        self.min_window_s = float(min_window_s)
        self.nearest_high = bool(nearest_high)
        self.deadline = deadline
        self._clock = clock or _time.monotonic
        self.requests = 0                  # bucket walks only (not the roots)
        self.pending: list = []            # stack: the next bucket is the last
        self.buckets: list = []
        self.buckets_total = 0
        self.unresolved: list = []
        self.aborted = None
        self.splits = 0
        self.walks = 0
        self.root_truncated = False
        self.totals = {k: 0 for k in _PARTITION_SUM_KEYS}
        self.totals["shift_unrecovered"] = 0
        self.totals["already_read_elsewhere"] = 0
        self.totals["new_events"] = 0

    def _unresolved(self, start, end, depth, why, error=None) -> None:
        self.unresolved.append({"pass": self.pass_name,
                                "start": _bucket_iso(start),
                                "end": _bucket_iso(end), "depth": int(depth),
                                "why": why, **({"error": str(error)[:200]}
                                               if error else {})})

    def _push_halves(self, start, end, depth) -> None:
        left, right = split_window(start, end)
        self.splits += 1
        # the stack pops the LAST: push the farther half first
        order = (left, right) if self.nearest_high else (right, left)
        for a, b in order:
            self.pending.append((a, b, depth + 1))

    def add_unread(self, start, end) -> None:
        """A depth-0 window the primary walk never reached (a calendar slice
        left unread when the pass budget was spent): walked as a bucket,
        after every bucket already pending (it is farther out)."""
        self.root_truncated = True
        self.pending.insert(0, (float(start), float(end), 0))

    def record(self, start, end, depth: int, receipt: dict, *,
               root: bool = False, new_events: int | None = None) -> None:
        r = dict(receipt or {})
        stopped = r.get("stopped")
        if not root:
            self.walks += 1
            self.requests += int(r.get("requests") or 0)
            for k in self.totals:
                if k == "new_events":
                    continue
                self.totals[k] += int(r.get(k) or 0)
            if new_events is not None:
                self.totals["new_events"] += int(new_events)
        elif stopped in TRUNCATING_STOPS:
            self.root_truncated = True
        self.buckets_total += 1
        if len(self.buckets) < MAX_PARTITION_BUCKETS_LISTED:
            self.buckets.append({
                "pass": self.pass_name, "start": _bucket_iso(start),
                "end": _bucket_iso(end), "depth": int(depth),
                "root": bool(root),
                "truncated": stopped in TRUNCATING_STOPS,
                "stopped": stopped,
                "requests": int(r.get("requests") or 0),
                "events": int(r.get("events_unique") or 0),
                **({"new_events": int(new_events)}
                   if new_events is not None else {})})
        if stopped in NATURAL_ENDS:
            if int(r.get("shift_unrecovered") or 0) and not root:
                # the bucket ended but a board shift it could not re-read
                # may have carried an event past it: not established
                self._unresolved(start, end, depth, P_SHIFT)
            return
        if stopped == STOP_WALL_TIME:
            self._unresolved(start, end, depth, STOP_WALL_TIME)
            return
        if stopped == STOP_MEMORY_BUDGET:
            # (completion readiness) over the memory budget: this window is
            # unresolved and no further bucket is walked in this process --
            # never split (more walks) and never PARTITIONED_TO_A_NATURAL_END
            self._unresolved(start, end, depth, STOP_MEMORY_BUDGET)
            self.aborted = STOP_MEMORY_BUDGET
            return
        if stopped in (STOP_BUDGET, STOP_OFFSET_CEILING):
            if start is None or end is None:
                self._unresolved(start, end, depth, P_UNBOUNDED)
            elif (depth >= self.max_depth
                  or float(end) - float(start) <= self.min_window_s):
                self._unresolved(start, end, depth, P_REMAINS_TRUNCATED)
            else:
                self._push_halves(float(start), float(end), depth)
            return
        if root:
            # the primary walk failed (a 429, a failed request, failing
            # writes): that is the pass's own stop, not a partition matter
            return
        # a bucket walk stopped on a 429, a failed request or failing writes:
        # no further bucket is walked
        self._unresolved(start, end, depth, stopped or STOP_ERROR,
                         r.get("error"))
        self.aborted = stopped or STOP_ERROR

    def _drain(self, why) -> None:
        while self.pending:
            a, b, d = self.pending.pop()
            self._unresolved(a, b, d, why)

    def abort(self, why: str) -> None:
        """Walk no further bucket (the lane stopped): every pending bucket
        is named unresolved with `why`."""
        self._drain(why)

    def next_bucket(self):
        """(start, end, depth, max_requests) for the next bucket, or None."""
        if not self.pending:
            return None
        if self.aborted is not None:
            self._drain(STOP_NOT_RUN)
            return None
        left = self.budget - self.requests
        if left <= 0:
            self._drain(P_BUDGET)
            return None
        if self.deadline is not None and self._clock() >= self.deadline:
            self._drain(STOP_WALL_TIME)
            return None
        a, b, d = self.pending.pop()
        return a, b, d, min(left, self.bucket_max_requests)

    @property
    def complete(self) -> bool:
        return not self.unresolved and not self.pending

    def receipt(self) -> dict:
        return {"buckets": list(self.buckets),
                "buckets_total": self.buckets_total,
                "buckets_listed": len(self.buckets),
                "unresolved": list(self.unresolved),
                "complete": self.complete,
                "budget": self.budget, "requests": self.requests,
                "bucket_max_requests": self.bucket_max_requests,
                "max_depth": self.max_depth,
                "min_window_s": self.min_window_s,
                "splits": self.splits, "bucket_walks": self.walks,
                "aborted_by": self.aborted,
                "walk_totals": dict(self.totals)}


def apply_partition(pass_receipt: dict, part: WindowPartition) -> dict:
    """The pass's receipt after its partition. Pure.

    The bucket walks' requests and pages are added to the pass's own (so the
    receipt's request total is still every request sent). A pass that was
    truncated (or left calendar slices unread for budget or time) is
      * PARTITIONED_TO_A_NATURAL_END -- a natural end, not truncated -- only
        when every bucket ended on a natural end of the venue's board;
      * truncated otherwise, every unresolved bucket named with its window
        and why: WALL_TIME_BUDGET_EXHAUSTED / REQUEST_BUDGET_EXHAUSTED when
        that is every bucket's cause, PARTITION_LEFT_BUCKETS_UNRESOLVED
        (PROVIDER_BUCKET_REMAINS_TRUNCATED among them, or mixed causes)
        otherwise.
    A pass that was not truncated keeps its own stop."""
    out = dict(pass_receipt or {})
    pr = part.receipt()
    out["partition"] = pr
    tot = pr["walk_totals"]
    for k in _PARTITION_SUM_KEYS:
        out[k] = int(out.get(k) or 0) + int(tot.get(k) or 0)
    out["partition_requests"] = pr["requests"]
    if not part.root_truncated:
        # nothing was truncated: the pass keeps its own stop (a natural end,
        # or a 429 / failure that is not a partition matter)
        return out
    out["stopped_before_partition"] = out.get("stopped")
    if pr["complete"]:
        out["stopped"] = STOP_PARTITION_COMPLETE
        out["truncated"] = False
        out["natural_end"] = True
        # the window was read again, bucket by bucket: what the truncated
        # first read could not re-read is superseded by the buckets' own
        out["shift_unrecovered"] = int(tot.get("shift_unrecovered") or 0)
        out["complete"] = out["shift_unrecovered"] == 0
    else:
        # one cause for every unresolved bucket keeps its own name (the wall
        # time, the request budget); mixed causes are named per bucket
        whys = {u.get("why") for u in pr["unresolved"]} or {None}
        if whys == {STOP_WALL_TIME}:
            out["stopped"] = STOP_WALL_TIME
        elif whys == {STOP_MEMORY_BUDGET}:
            out["stopped"] = STOP_MEMORY_BUDGET
        elif whys <= {P_BUDGET, STOP_BUDGET}:
            out["stopped"] = STOP_BUDGET
        else:
            out["stopped"] = STOP_PARTITION_UNRESOLVED
        out["truncated"] = True
        out["natural_end"] = False
        out["complete"] = False
        if part.aborted and not out.get("error"):
            out["error"] = next((u.get("error") for u in pr["unresolved"]
                                 if u.get("error")), part.aborted)
    return out


# ── the side key ─────────────────────────────────────────────────────────

class SideKeyGuard:
    """Keeps (identifier, side_norm) -- the catalogue's unique key -- from
    collapsing two distinct sides into one row within one refresh.

    `admit(row)` returns the row to write (possibly with a qualified
    side_norm) or None (refused, recorded). Three cases:

      * a key not seen this refresh: admitted unchanged;
      * the SAME market's other side normalised to the same text with a
        different intent (the asc- family before C3 wrote 100+ such rows,
        every one ORDER_INTENT_BUY_SHORT, none orderable): both sides are
        kept, each side_norm qualified with the venue's own marker
        (`'10 50 [long]'` / `'10 50 [short]'`). Brackets never occur in a
        normalised pick, so no text match can land on either qualified row
        -- a reader that matched the collapsed row before now refuses,
        which is the safe direction -- while intent-keyed readers see both
        sides;
      * a key already written by ANOTHER market: the venue reused an
        identifier, and nothing BETTOR holds can say which market the row
        should name. The later one is refused by name, never overwritten.

    The first writer of a qualified pair has already been written unqualified
    when the second arrives; `requalify` names it so the caller rewrites it
    (and deletes the unqualified row the earlier write left)."""

    def __init__(self):
        self._held: dict = {}
        self.qualified = 0
        self.refused = 0
        self.examples: list = []

    @staticmethod
    def _marker(intent) -> str:
        i = str(intent or "").upper()
        if i.endswith("LONG"):
            return "long"
        if i.endswith("SHORT"):
            return "short"
        return "unmarked"

    def admit(self, row: dict):
        key = (row.get("identifier"), row.get("side_norm"))
        prior = self._held.get(key)
        if prior is None:
            self._held[key] = {"market_slug": row.get("market_slug"),
                               "intent": row.get("intent"), "row": row}
            return row, None
        if prior["market_slug"] != row.get("market_slug"):
            self.refused += 1
            if len(self.examples) < MAX_EXAMPLES:
                self.examples.append({"case": D_SIDE_KEY_HELD_BY_ANOTHER_MARKET,
                                      "identifier": key[0], "side_norm": key[1],
                                      "held_by": prior["market_slug"],
                                      "refused": row.get("market_slug")})
            return None, None
        if prior["intent"] == row.get("intent"):
            # the venue listed the same side twice: one row, nothing lost
            return None, None
        first = dict(prior["row"])
        first["side_norm"] = "%s [%s]" % (key[1], self._marker(first.get("intent")))
        second = dict(row)
        second["side_norm"] = "%s [%s]" % (key[1], self._marker(row.get("intent")))
        if first["side_norm"] == second["side_norm"]:
            self.refused += 1
            return None, None
        self.qualified += 1
        self._held[(key[0], first["side_norm"])] = {
            "market_slug": first.get("market_slug"),
            "intent": first.get("intent"), "row": first}
        self._held[(key[0], second["side_norm"])] = {
            "market_slug": second.get("market_slug"),
            "intent": second.get("intent"), "row": second}
        if len(self.examples) < MAX_EXAMPLES:
            self.examples.append({"case": K_SIDE_KEY_QUALIFIED,
                                  "identifier": key[0], "side_norm": key[1],
                                  "market_slug": row.get("market_slug")})
        # the caller rewrites `first` under its qualified key and deletes the
        # unqualified row its earlier write left behind
        return second, {"rewrite": first, "delete_side_norm": key[1]}

    def receipt(self) -> dict:
        return {"qualified_pairs": self.qualified,
                "refused_cross_market": self.refused,
                "examples": list(self.examples)}


# ── the tally ────────────────────────────────────────────────────────────

class CompletenessTally:
    """Listings seen / kept / dropped by pass, sport, league and family, with
    the venue's own market counts and every event's live / pregame state.

    The arithmetic is the receipt's contract and the migration-249 CHECKs
    restate it: events_kept + events_dropped == events_seen, and the same for
    markets. A dropped listing always carries one reason from DROP_REASONS."""

    def __init__(self, *, lane: str):
        self.lane = lane
        self.passes: dict = {}
        self.events_seen = 0
        self.events_kept = 0
        self.events_dropped: dict = {}
        self.markets_seen = 0
        self.markets_kept = 0
        self.markets_dropped: dict = {}
        self.sides_written = 0
        self.sides_incomplete = 0
        self.notes: dict = {}
        self.cells: dict = {}
        self.by_sport: dict = {}
        self.states: dict = {}
        self.state_sources: dict = {}
        self.market_counts = {"events_with_venue_count": 0,
                              "events_inline_fewer_than_venue_count": 0,
                              "markets_missing_inline": 0,
                              "detail_reads": 0,
                              "detail_reads_failed": 0,
                              "detail_reads_skipped_budget": 0,
                              "markets_recovered_by_detail": 0,
                              "events_still_short_after_detail": 0,
                              "markets_still_missing_after_detail": 0,
                              "examples": []}
        self.market_status: dict = {}
        #: requests made outside any pass's page walk (the per-event detail
        #: reads that repair an inline list shorter than the venue's count):
        #: counted into `requests` so the receipt equals the paced claims
        self.extra_requests: dict = {}
        self.write_failures = {"events": 0, "examples": []}

    def _cell(self, sport, league, family) -> dict:
        k = "%s/%s/%s" % (sport, league, family)
        c = self.cells.get(k)
        if c is None:
            c = self.cells[k] = {"seen": 0, "kept": 0, "dropped": {}}
        return c

    def _sport(self, sport) -> dict:
        s = self.by_sport.get(sport)
        if s is None:
            s = self.by_sport[sport] = {"events": 0, "markets_kept": 0,
                                        "markets_dropped": 0, "sides": 0,
                                        "live_events": 0, "pregame_events": 0}
        return s

    def note(self, what: str, n: int = 1) -> None:
        self.notes[what] = self.notes.get(what, 0) + int(n)

    def set_pass(self, name: str, receipt: dict) -> None:
        self.passes[name] = dict(receipt)

    def event_dropped(self, ev: dict, reason: str) -> None:
        self.events_seen += 1
        r = reason if reason in DROP_REASONS else D_EVENT_NO_SLUG
        self.events_dropped[r] = self.events_dropped.get(r, 0) + 1

    def event_kept(self, ev: dict, *, sport: str, state: tuple, now: float) -> None:
        self.events_seen += 1
        self.events_kept += 1
        st, src = state
        self.states[st] = self.states.get(st, 0) + 1
        self.state_sources[src] = self.state_sources.get(src, 0) + 1
        s = self._sport(sport)
        s["events"] += 1
        if st == S_LIVE:
            s["live_events"] += 1
        elif st == S_PREGAME:
            s["pregame_events"] += 1

    def extra_request(self, kind: str, n: int = 1) -> None:
        self.extra_requests[kind] = self.extra_requests.get(kind, 0) + int(n)

    def inline_vs_venue_count(self, ev: dict, inline_n: int,
                              after_detail_n: int | None = None) -> None:
        """The venue's own count of markets against what arrived inline.
        Fewer inline than counted is the signature of a per-event cap on the
        listing -- the market-per-event limit the owner asked about. It is
        recorded with examples and REPAIRED where the request budget allows:
        the writer reads the event by its slug (`after_detail_n` is what that
        read brought); what is still missing after it is counted too, never
        filled by guessing."""
        n = venue_market_count(ev)
        if n is None:
            return
        mc = self.market_counts
        mc["events_with_venue_count"] += 1
        if inline_n < n:
            mc["events_inline_fewer_than_venue_count"] += 1
            mc["markets_missing_inline"] += n - inline_n
            got = inline_n if after_detail_n is None else max(inline_n,
                                                              after_detail_n)
            mc["markets_recovered_by_detail"] += got - inline_n
            if got < n:
                mc["events_still_short_after_detail"] += 1
                mc["markets_still_missing_after_detail"] += n - got
            if len(mc["examples"]) < MAX_EXAMPLES:
                mc["examples"].append({"event": ev.get("slug") or ev.get("eventSlug"),
                                       "venue_count": n, "inline": inline_n,
                                       "after_detail": after_detail_n})

    def detail_read(self, outcome: str) -> None:
        """One per-event detail read: 'read', 'failed' or 'skipped_budget'."""
        mc = self.market_counts
        if outcome == "read":
            mc["detail_reads"] += 1
        elif outcome == "failed":
            mc["detail_reads_failed"] += 1
        else:
            mc["detail_reads_skipped_budget"] += 1

    def snapshot(self) -> tuple:
        """The counters an event moves, before it is written (see
        finish_failed_event)."""
        return (self.events_seen, self.markets_seen, self.markets_kept,
                sum(self.markets_dropped.values()))

    def finish_failed_event(self, ev: dict, before: tuple, why: str) -> None:
        """An event whose write failed OUTSIDE its own guard: whatever it
        left unresolved is resolved by difference against `before` -- markets
        seen but neither kept nor dropped become
        MARKET_ROWS_FAILED_TO_BUILD_OR_WRITE (by reason only: their cells are
        not known), the event, if it was never counted, becomes
        EVENT_ROWS_FAILED_TO_WRITE -- so the receipt reconciles."""
        ev0, ms0, mk0, md0 = before
        unresolved = ((self.markets_seen - ms0) - (self.markets_kept - mk0)
                      - (sum(self.markets_dropped.values()) - md0))
        if unresolved > 0:
            r = D_MARKET_ROW_BUILD_FAILED
            self.markets_dropped[r] = self.markets_dropped.get(r, 0) + unresolved
        if self.events_seen == ev0:
            self.event_dropped(ev, D_EVENT_WRITE_FAILED)
        self.write_failed(ev, why)

    def write_failed(self, ev: dict, why: str) -> None:
        wf = self.write_failures
        wf["events"] += 1
        if len(wf["examples"]) < MAX_EXAMPLES:
            wf["examples"].append({"event": (ev or {}).get("slug")
                                   or (ev or {}).get("eventSlug"),
                                   "why": str(why)[:160]})

    def market_seen(self, *, sport, league, family, status=None) -> None:
        self.markets_seen += 1
        self._cell(sport, league, family)["seen"] += 1
        if status:
            s = str(status)[:40]
            self.market_status[s] = self.market_status.get(s, 0) + 1

    def market_kept(self, *, sport, league, family, sides: int) -> None:
        self.markets_kept += 1
        self._cell(sport, league, family)["kept"] += 1
        sp = self._sport(sport)
        sp["markets_kept"] += 1
        sp["sides"] += int(sides)

    def market_dropped(self, *, sport, league, family, reason: str) -> None:
        r = reason if reason in DROP_REASONS else D_MARKET_NO_ORDERABLE_SIDE
        self.markets_dropped[r] = self.markets_dropped.get(r, 0) + 1
        c = self._cell(sport, league, family)
        c["dropped"][r] = c["dropped"].get(r, 0) + 1
        self._sport(sport)["markets_dropped"] += 1

    def complete(self) -> bool:
        """Every pass ended on a natural end of the board -- nothing
        truncated, nothing rate-limited, nothing failed -- with no board shift
        the walk could not recover, and no event that failed to write."""
        return bool(self.passes) and self.write_failures["events"] == 0 and all(
            p.get("stopped") in NATURAL_ENDS
            and not p.get("truncated")
            and not int(p.get("shift_unrecovered") or 0)
            and not (p.get("partition") or {}).get("unresolved")
            for p in self.passes.values())

    def partition(self) -> dict:
        """Every pass's partition buckets and unresolved buckets, in one
        place (bounded: MAX_PARTITION_BUCKETS_LISTED buckets in all)."""
        buckets, unresolved, total = [], [], 0
        for p in self.passes.values():
            pr = p.get("partition") or {}
            total += int(pr.get("buckets_total") or 0)
            for b in pr.get("buckets") or []:
                if len(buckets) < MAX_PARTITION_BUCKETS_LISTED:
                    buckets.append(b)
            unresolved.extend(pr.get("unresolved") or [])
        return {"buckets": buckets, "buckets_total": total,
                "unresolved": unresolved, "complete": not unresolved}

    def catalogue_complete(self) -> bool:
        """THE FIELD THE MARKET PLANE READS. True only when this refresh
        established its whole enumeration space: every pass on a natural end
        of the venue's board (`complete`), no pass truncated, and no
        partition bucket left unresolved. A truncated result is never a
        complete venue universe."""
        return (self.complete()
                and not any(p.get("truncated") for p in self.passes.values())
                and self.partition()["complete"])

    def outcome(self) -> str:
        if not self.passes:
            return "FAILED"
        if self.complete():
            return "COMPLETE"
        if any(p.get("truncated") for p in self.passes.values()):
            return "TRUNCATED"
        if self.events_seen > 0:
            return "PARTIAL"
        return "FAILED"

    def receipt(self) -> dict:
        cells = sorted(self.cells.items(), key=lambda kv: (-kv[1]["seen"], kv[0]))
        kept_cells = dict(cells[:MAX_RECEIPT_CELLS])
        rest = cells[MAX_RECEIPT_CELLS:]
        if rest:
            other = {"seen": 0, "kept": 0, "dropped": {}}
            for _, c in rest:
                other["seen"] += c["seen"]
                other["kept"] += c["kept"]
                for r, n in c["dropped"].items():
                    other["dropped"][r] = other["dropped"].get(r, 0) + n
            kept_cells["_other"] = other
        requests = (sum(int(p.get("requests") or 0) for p in self.passes.values())
                    + sum(int(v) for v in self.extra_requests.values()))
        pages = sum(int(p.get("pages_with_events") or 0) for p in self.passes.values())
        return {
            "version": VERSION, "lane": self.lane,
            "outcome": self.outcome(), "complete": self.complete(),
            "catalogue_complete": self.catalogue_complete(),
            "partition": self.partition(),
            "requests": requests, "pages_read": pages,
            "requests_outside_page_walks": dict(self.extra_requests),
            "passes": self.passes,
            "events": {"seen": self.events_seen, "kept": self.events_kept,
                       "dropped": sum(self.events_dropped.values()),
                       "dropped_by_reason": dict(self.events_dropped)},
            "markets": {"seen": self.markets_seen, "kept": self.markets_kept,
                        "dropped": sum(self.markets_dropped.values()),
                        "dropped_by_reason": dict(self.markets_dropped),
                        "status_seen": dict(self.market_status)},
            "sides_written": self.sides_written,
            "sides_incomplete_dropped": self.sides_incomplete,
            "notes": dict(self.notes),
            "states": dict(self.states), "state_sources": dict(self.state_sources),
            "venue_market_counts": dict(self.market_counts),
            "write_failures": dict(self.write_failures),
            "by_sport": dict(sorted(self.by_sport.items())),
            "by_sport_league_family": kept_cells,
            "cells_total": len(self.cells),
        }

    def compact(self) -> dict:
        """The receipt without the per-league cells: what the fast lane (every
        180 s) appends, so its history stays small."""
        r = self.receipt()
        r.pop("by_sport_league_family", None)
        return r


def describe() -> dict:
    return {"version": VERSION, "passes": list(PASSES),
            "families": list(FAMILIES), "states": list(STATES),
            "state_sources": list(STATE_SOURCES),
            "drop_reasons": list(DROP_REASONS), "stops": list(STOPS),
            "page_overlap": PAGE_OVERLAP, "max_rewinds": MAX_REWINDS,
            "partition_max_depth": PARTITION_MAX_DEPTH,
            "partition_min_window_s": PARTITION_MIN_WINDOW_S,
            "multi_day_min_span_h": MULTI_DAY_MIN_SPAN_H,
            "final_period_words": sorted(FINAL_PERIOD_WORDS),
            "non_sports_categories": sorted(NON_SPORTS_CATEGORIES),
            "max_receipt_cells": MAX_RECEIPT_CELLS}


def utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
