"""THE VENUE CATALOGUE'S COMPLETENESS CONTRACT (R30A P0 incident, inc-catalogue).

Pure: no database, no network, no venue client. `workers/premap.refresh` (the
writer of `us_premap`) and `pmus._desk_sweep` drive it; the tests drive it with
production-shaped pages.

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
    (`marketCounts.numMarkets`) and its OWN live state (`live`, `period`).
    Neither was read, so a per-event cap on the inline market list, and the
    difference between a live market and a pregame one, were invisible.
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
                     the previous one, so an event that shifted across a page
                     boundary while the board changed underneath the sweep is
                     caught instead of skipped; the budget is a number of
                     requests, and running out of it while pages are still full
                     is TRUNCATED, by name.
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
never decides tradability beyond the venue's own flags (closed, archived,
ended, market status). It raises no economic threshold and lowers none.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

VERSION = "VENUE_CATALOGUE_COMPLETENESS_V1"

# ── passes: which slice of the venue's calendar a request walks ──────────
PASS_WINDOW = "WINDOW"                    # [now-back_h, now+fwd_h]: the sweep's own window
PASS_AHEAD = "AHEAD"                      # (now+fwd_h, now+AHEAD_DAYS]: futures, next week's slate
PASS_STARTED_EARLIER = "STARTED_EARLIER"  # [now-EARLIER_DAYS, now-back_h): still-open futures, live multi-day events
PASS_FAST = "FAST"                        # the imminent window, every 180 s
PASS_MARKETS_FALLBACK = "MARKETS_FALLBACK"
PASSES = (PASS_WINDOW, PASS_AHEAD, PASS_STARTED_EARLIER, PASS_FAST,
          PASS_MARKETS_FALLBACK)

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
S_NOT_LIVE = "NOT_LIVE"    # the venue says not live, though the start has passed (break, delay, suspension)
S_ENDED = "ENDED"          # the venue says the event ended
S_STARTED = "STARTED"      # schedule only: the start has passed, the venue stated nothing
S_UNKNOWN = "UNKNOWN"      # no venue flag and no start time
STATES = (S_LIVE, S_PREGAME, S_NOT_LIVE, S_ENDED, S_STARTED, S_UNKNOWN)

SRC_VENUE_LIVE_FLAG = "VENUE_LIVE_FLAG"
SRC_VENUE_ENDED_FLAG = "VENUE_ENDED_FLAG"
SRC_SCHEDULE = "SCHEDULE_ESTIMATE"
SRC_NONE = "NO_EVIDENCE"
STATE_SOURCES = (SRC_VENUE_LIVE_FLAG, SRC_VENUE_ENDED_FLAG, SRC_SCHEDULE,
                 SRC_NONE)

# ── why a listing was not kept: one precise reason each, never "filtered" ─
D_EVENT_NO_SLUG = "EVENT_WITHOUT_SLUG"
D_EVENT_CLOSED = "EVENT_CLOSED_OR_ARCHIVED_BY_VENUE"
D_EVENT_ENDED = "EVENT_ENDED_BY_VENUE"
D_EVENT_NO_OPEN_MARKET = "EVENT_HAS_NO_OPEN_MARKET"
D_EVENT_OUT_OF_SCOPE_CATEGORY = "EVENT_CATEGORY_IS_NOT_SPORTS"
D_EVENT_ALREADY_READ = "EVENT_ALREADY_READ_THIS_REFRESH"
D_MARKET_CLOSED = "MARKET_CLOSED_BY_VENUE"
D_MARKET_PAST_PLAYABLE_SPAN = "GAME_MARKET_STARTED_BEFORE_ITS_PLAYABLE_SPAN"
D_MARKET_NO_ORDERABLE_SIDE = "MARKET_WITHOUT_AN_ORDERABLE_SIDE"
D_SIDE_INCOMPLETE = "SIDE_WITHOUT_IDENTIFIER_OR_DESCRIPTION"
D_SIDE_KEY_HELD_BY_ANOTHER_MARKET = "SIDE_KEY_ALREADY_HELD_BY_ANOTHER_MARKET"
D_FALLBACK_MARKET_WITHOUT_KEY = "DEGRADED_FALLBACK_MARKET_WITHOUT_LOOKUP_KEY"
DROP_REASONS = (D_EVENT_NO_SLUG, D_EVENT_CLOSED, D_EVENT_ENDED,
                D_EVENT_NO_OPEN_MARKET, D_EVENT_OUT_OF_SCOPE_CATEGORY,
                D_EVENT_ALREADY_READ, D_MARKET_CLOSED,
                D_MARKET_PAST_PLAYABLE_SPAN, D_MARKET_NO_ORDERABLE_SIDE,
                D_SIDE_INCOMPLETE, D_SIDE_KEY_HELD_BY_ANOTHER_MARKET,
                D_FALLBACK_MARKET_WITHOUT_KEY)

# kept, with a note (never a drop)
K_KEPT_WITHOUT_EVENT_KEY = "KEPT_WITHOUT_EVENT_LOOKUP_KEY"
K_SIDE_KEY_QUALIFIED = "SIDE_KEY_QUALIFIED_BY_THE_VENUES_MARKER"

# ── how a pass ended ─────────────────────────────────────────────────────
STOP_SHORT_PAGE = "SHORT_PAGE"                 # the board ended on a page shorter than the venue's page size
STOP_EMPTY_PAGE = "EMPTY_PAGE"                 # the next offset answered nothing
STOP_BUDGET = "REQUEST_BUDGET_EXHAUSTED"       # pages were still full when the budget ran out: TRUNCATED
STOP_RATE_LIMITED = "RATE_LIMITED_BY_VENUE"    # a 429: the pass stops, the venue_pace circuit is applied
STOP_ERROR = "REQUEST_FAILED"                  # any other failed request
STOP_NO_VARIANT = "NO_PARAMETER_VARIANT_ANSWERED"
STOPS = (STOP_SHORT_PAGE, STOP_EMPTY_PAGE, STOP_BUDGET, STOP_RATE_LIMITED,
         STOP_ERROR, STOP_NO_VARIANT)
NATURAL_ENDS = frozenset({STOP_SHORT_PAGE, STOP_EMPTY_PAGE})

#: How many events of the previous page each next request reads again. Offset
#: pagination over a board that changes while it is walked (games closing,
#: games listing) shifts every later event by one position per deletion ahead
#: of the cursor; without an overlap that event falls between two pages and is
#: never read. Five per page is ~5% more data per page, no extra request.
PAGE_OVERLAP = 5

#: A game market (not a future) on an event that started more than this long
#: ago, which the venue does NOT say is live, is past any game's playable span:
#: the STARTED_EARLIER pass drops it by name rather than keep a market the
#: venue has not yet closed out. A future (sportsMarketType `futures`) and any
#: market on an event the venue itself flags `live` are kept whatever the
#: start: series winners, tournament winners and multi-day matches are exactly
#: what that pass exists to retain.
GAME_PLAYABLE_SPAN_H = 48.0

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


def family_of(sports_market_type, *, slug=None, n_sides=None) -> str:
    """The market family, from the venue's OWN type text.

    The order matters and mirrors the coverage queries the incident reads
    (research/incident_venuecat_coverage.sql V4): a player prop is a prop
    before it is a total, a period market is a period before it is a winner,
    a team total is a team total before it is a total. A market with no type
    but a yes/no pair is YES_NO; with none at all, NO_TYPE -- never guessed
    into a family it may not be."""
    st = str(sports_market_type or "").strip().lower()
    if not st:
        return F_YES_NO if n_sides == 2 and str(slug or "").startswith(
            ("cpc-", "paccc-")) else F_NO_TYPE
    if st == "futures" or st.startswith("futures"):
        return F_FUTURES
    if "_player_" in st:
        return F_PLAYER_PROP
    if re.search(r"(first_five|inning|half|quarter|period|_set_|_set$|"
                 r"sets_|_map|frame|_leg_)", st):
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
    if st in ("election",):
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


def listing_state(ev: dict, now: float, market: dict | None = None) -> tuple:
    """(state, source) for one event at `now`.

    The venue's own words first: `live` (a boolean on every event of the
    gateway's payload) and `ended`. `period: "NS"` with `live: false` is
    PREGAME by the venue; `live: false` after the start is NOT_LIVE (a break,
    a delay, a suspension -- the venue did not say it is in play). Only with
    no venue flag at all does the schedule decide, and the source then says
    SCHEDULE_ESTIMATE so no reader mistakes an estimate for the venue's word.
    """
    ev = ev or {}
    start = event_start(ev, market)
    if ev.get("ended") is True:
        return S_ENDED, SRC_VENUE_ENDED_FLAG
    live = ev.get("live")
    if isinstance(live, bool):
        if live:
            return S_LIVE, SRC_VENUE_LIVE_FLAG
        period = str(ev.get("period") or "").strip().upper()
        if period == "NS" or (start is not None and start > now):
            return S_PREGAME, SRC_VENUE_LIVE_FLAG
        return S_NOT_LIVE, SRC_VENUE_LIVE_FLAG
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


#: The venue categories the two added passes read. `sports` is the venue's own
#: word on every sports event the gateway served (fetch-docs 37233829391: the
#: MLB futures); `esports` is admitted beside it because the venue lists esports
#: winners and series (cs2, lol, dota2, valorant) and its category word for them
#: was not observed -- a sport must never be dropped on a guess. An event with
#: no category is kept. Every dropped category is counted BY NAME in the
#: receipt (`notes`, `category_dropped:<word>`), so the first production receipt
#: shows whether a sports listing ever sits under another word.
SPORTS_CATEGORIES = frozenset({"sports", "esports"})


def event_drop_reason(ev: dict, *, pass_name: str) -> str | None:
    """Why an event is not read at all, or None. Pure.

    Closed / archived / ended are the venue's own flags and drop on every
    pass. The CATEGORY filter applies only to the two passes this module
    added (AHEAD, STARTED_EARLIER): beyond the sports window the venue's
    calendar is mostly politics, culture and crypto (fetch-docs run
    37233823157: the first events past +96 h were the U.S. House and Senate
    midterms), and reading them would spend the request budget on listings no
    supported sport can map. The WINDOW and FAST passes keep every category,
    exactly as before -- this repair removes no row the sweep wrote."""
    ev = ev or {}
    if not (ev.get("slug") or ev.get("eventSlug")):
        return D_EVENT_NO_SLUG
    if ev.get("closed") is True or ev.get("archived") is True:
        return D_EVENT_CLOSED
    if ev.get("ended") is True:
        return D_EVENT_ENDED
    if pass_name in (PASS_AHEAD, PASS_STARTED_EARLIER):
        cat = str(ev.get("category") or "").strip().lower()
        if cat and cat not in SPORTS_CATEGORIES:
            return D_EVENT_OUT_OF_SCOPE_CATEGORY
    return None


def market_drop_reason(ev: dict, market: dict, *, pass_name: str,
                       now: float) -> str | None:
    """Why one inline market is not written, or None. Pure.

    `closed` is the venue's flag and was the only market filter before; it
    stays the only one on the WINDOW and FAST passes. On STARTED_EARLIER a
    game market (not a future) whose event started more than
    GAME_PLAYABLE_SPAN_H ago and which the venue does not flag live is past
    any game's playable span -- the venue has simply not closed it yet -- and
    is dropped by name. A market whose status reads anything other than OPEN
    is KEPT and counted: an in-play suspension is a live market for a moment,
    and dropping it would lose the live counterpart exactly when it matters.
    """
    if not isinstance(market, dict) or market.get("closed") is True:
        return D_MARKET_CLOSED
    if pass_name == PASS_STARTED_EARLIER:
        fam = family_of(market.get("sportsMarketType"), slug=market.get("slug"))
        if fam != F_FUTURES and (ev or {}).get("live") is not True:
            start = event_start(ev, market)
            if start is not None and now - start > GAME_PLAYABLE_SPAN_H * 3600.0:
                return D_MARKET_PAST_PLAYABLE_SPAN
    return None


# ── pagination ───────────────────────────────────────────────────────────

class PageWalk:
    """One pass's offset pagination as a state machine the caller drives.

        walk = PageWalk(limit=100, max_requests=120)
        walk.first(probe_events)                 # the variant probe's page, if any
        while (off := walk.next_offset()) is not None:
            walk.accept(fetch(off))              # or walk.fail(STOP_..., why)

    `accept` returns the events of that page not already read this pass, in
    order; `receipt()` says how the pass ended. Never raises.

    THE THREE WAYS OFFSET PAGINATION LOSES LISTINGS, AND THE ANSWER TO EACH:
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
         the overlap is a shift the walk caught (`overlap_catches`).
      3. The budget ran out. That is REQUEST_BUDGET_EXHAUSTED and truncated,
         never a quiet success.
    """

    def __init__(self, *, limit: int, max_requests: int,
                 overlap: int = PAGE_OVERLAP, start_offset: int = 0):
        self.limit = max(1, int(limit))
        self.max_requests = max(1, int(max_requests))
        self.overlap = max(0, int(overlap))
        self._offset = int(start_offset)
        self._next = int(start_offset)
        self.requests = 0
        self.pages = 0
        self.events_received = 0
        self.duplicates = 0
        self.overlap_catches = 0
        self.page_size_max = 0
        self.venue_page_cap = None
        self.stopped = None
        self.error = None
        self._seen: set = set()
        self._confirming = False
        self._last_tail: list = []

    @staticmethod
    def _key(ev) -> str | None:
        if not isinstance(ev, dict):
            return None
        s = ev.get("slug") or ev.get("eventSlug") or ev.get("id")
        return str(s) if s else None

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
        return self._next

    def fail(self, stop: str, why: str | None = None) -> None:
        self.stopped = stop if stop in STOPS else STOP_ERROR
        self.error = (str(why)[:200] if why else None)

    def accept(self, events) -> list:
        events = [e for e in (events or []) if isinstance(e, dict)]
        self.requests += 1
        n = len(events)
        if n == 0:
            self.stopped = STOP_EMPTY_PAGE
            self._confirming = False
            return []
        self.pages += 1
        self.events_received += n
        overlap_region = set(self._last_tail)
        fresh = []
        for i, ev in enumerate(events):
            k = self._key(ev)
            if k is None:
                fresh.append(ev)          # keyless: the caller drops it by name
                continue
            if k in self._seen:
                self.duplicates += 1
                continue
            self._seen.add(k)
            if i < len(overlap_region):
                # the head of this page re-reads the previous page's tail; an
                # event there that was NOT on the previous page moved across
                # the boundary while the board changed -- caught, not skipped
                self.overlap_catches += 1
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
        self._last_tail = [self._key(e) for e in events[n - tail_n:]] if tail_n else []
        step = max(1, n - tail_n)
        if self._confirming:
            step = n                               # confirm at the very next offset, no overlap
            self._last_tail = []
        self._offset = self._next
        self._next = self._offset + step
        return fresh

    @property
    def truncated(self) -> bool:
        return self.stopped == STOP_BUDGET

    def receipt(self) -> dict:
        return {"requests": self.requests, "pages_with_events": self.pages,
                "events_received": self.events_received,
                "events_unique": len(self._seen),
                "duplicates_across_pages": self.duplicates,
                "overlap_catches": self.overlap_catches,
                "page_size_requested": self.limit,
                "page_size_max_seen": self.page_size_max,
                "venue_page_cap": self.venue_page_cap,
                "max_requests": self.max_requests,
                "stopped": self.stopped, "error": self.error,
                "natural_end": self.stopped in NATURAL_ENDS,
                "truncated": self.truncated}


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
                              "markets_missing_inline": 0, "examples": []}
        self.market_status: dict = {}

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

    def inline_vs_venue_count(self, ev: dict, inline_n: int) -> None:
        """The venue's own count of markets against what arrived inline.
        Fewer inline than counted is the signature of a per-event cap on the
        listing -- the market-per-event limit the owner asked about -- and is
        recorded with examples, never repaired by guessing."""
        n = venue_market_count(ev)
        if n is None:
            return
        mc = self.market_counts
        mc["events_with_venue_count"] += 1
        if inline_n < n:
            mc["events_inline_fewer_than_venue_count"] += 1
            mc["markets_missing_inline"] += n - inline_n
            if len(mc["examples"]) < MAX_EXAMPLES:
                mc["examples"].append({"event": ev.get("slug") or ev.get("eventSlug"),
                                       "venue_count": n, "inline": inline_n})

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
        """Every pass ended on a natural end of the board: nothing truncated,
        nothing rate-limited, nothing failed."""
        return bool(self.passes) and all(
            p.get("stopped") in NATURAL_ENDS for p in self.passes.values())

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
        requests = sum(int(p.get("requests") or 0) for p in self.passes.values())
        pages = sum(int(p.get("pages_with_events") or 0) for p in self.passes.values())
        return {
            "version": VERSION, "lane": self.lane,
            "outcome": self.outcome(), "complete": self.complete(),
            "requests": requests, "pages_read": pages,
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
            "page_overlap": PAGE_OVERLAP,
            "game_playable_span_h": GAME_PLAYABLE_SPAN_H,
            "max_receipt_cells": MAX_RECEIPT_CELLS}


def utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
