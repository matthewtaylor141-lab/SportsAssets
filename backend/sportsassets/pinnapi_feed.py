"""THE SHARED PINNAPI CURRENT-STATE CACHE (pure: no socket, no database).

One process owns the provider's single WebSocket (see pinnapi_owner); this
module is the state it feeds and the ONLY accessor Derek and Xavier read.

AUTHORITY. Every connection is an EPOCH. Quotes are stored with the epoch that
delivered them. `revoke()` is called synchronously the moment ownership is in
doubt (lease connection lost, lease no longer held, socket closed, gap); from
then on every read answers None with a named reason until a NEW epoch has
been granted AND has resynchronized (the provider's snapshot for each
subscribed (stream, sport) has arrived on that epoch). Quotes from an older
epoch are never served again.

TIME. The clocks are kept apart and never substituted for one another:
  source_change_ms  SOURCE TIME. The provider frame stamp (`ts`) of the frame
                    in which THIS market's price (or line) last CHANGED. A
                    snapshot, a re-sent unchanged price, a heartbeat or a
                    reconnect never sets it. A market first seen in a
                    snapshot has NO change time (None) until a frame changes
                    it.
  observed_change_ms  OBSERVATION TIME. OUR wall clock when we received the
                    frame in which we OBSERVED that change -- set on every
                    observed change, beside the source time, never instead of
                    it. It is the owner's "defensible local observation
                    timestamp when the value changes" (P0 incident,
                    2026-10-04), and it is the change instant ONLY when the
                    changing frame carried no provider stamp: then
                    `change_clock` says LOCAL_OBSERVATION_OF_THE_CHANGE and
                    the age is measured from our receipt of that frame (the
                    change happened at or before it, so nothing earlier is
                    assumed and nothing is guessed). With a provider stamp
                    the change instant is the stamp (PROVIDER_FRAME_TS).
  first_observed_ms OUR wall clock when this exact price was first held on
                    this epoch (the frame that changed it, or first sight).
                    Provenance only.
  confirmed_ms      MEASURED, NEVER A DECISION INPUT. The provider stamp of
                    the latest frame that asserted this same price is still
                    current: a live record carrying it, an authoritative
                    prematch_markets list carrying it, a prematch_matchups
                    frame whose matchup version equals the version the
                    markets were delivered at, or a snapshot RE-confirming a
                    price this epoch already held (our receipt time when the
                    frame has no stamp, named as such). The subscribe snapshot
                    alone is not a confirmation.
  confirmed_received_ms  our wall clock when that confirming frame arrived.
  frame_ts_ms       the provider stamp of the latest frame that carried the
                    market (snapshot or delta) -- provenance only.
  received_ms       our wall clock when that frame arrived -- provenance only.
  evaluated_ms      supplied by the consumer at read time.

THE 30 s RULE IS UNCHANGED (owner decision 2026-10-04: "Do NOT change ...
freshness requirements"). The caller's limit (30 s) is measured from the
last observed CHANGE, exactly as since C1: age = evaluated_ms - change
instant; no observed change => FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE,
never a guessed age. What the incident repair adds is only (a) the change
instant for a changing frame that carried no provider stamp (our observation
of the change, labelled), (b) a key that OPENS between two authoritative
prematch lists on one epoch is an observed change at the second list's stamp
(it was not in the first), and (c) every clock on every read's provenance,
so source time and observation time are never confused downstream.

The confirmation clock is CARRIED AND COUNTED ONLY. Measured 2026-10-04
20:31Z: 30,768 of 31,849 cached markets (96.6%) were age-unknown under the
change basis, though prematch_markets re-delivers every market of a matchup
each time its version moves. Measuring the 30 s rule from the confirmation
instead would change WHAT the freshness requirement measures -- an owner
decision the incident diagnosis said "must not ship silently" -- so it is
not a switch here: the census reports how many markets WOULD read fresh if
it were (`fresh_now_if_measured_from_confirmation`, labelled a
counterfactual), for the owner to decide on.

BOUNDS. The cache holds at most MAX_EVENTS events and MAX_MARKETS markets;
events the provider deletes or closes are dropped at once; the oldest-touched
events are evicted beyond the cap (and counted). Latency samples live in
fixed-size rings. Nothing here persists raw frames.

  CAPACITY FOR THE R30A SCOPE (sports 1-6), ARITHMETIC NOT HOPE. Measured:
  the production cache held 31,849 markets for soccer + baseball (2026-10-04
  20:31Z, ~1,300 events: the 2026-10-01 ws_sample snapshot was 1,291 soccer +
  9 baseball prematch events). The PinnAPI REST probe (run 37232918224) listed
  football 64 prematch + 3 live, tennis 165, basketball 62, hockey 106
  events, and per-event market counts from its sampled records of at most
  59 (NFL prematch, all periods), 51 (NFL live child), 17 (a tennis
  '(Games)' child), 34 (basketball) and 37 (NHL). Worst case, every event at
  its sport's largest sampled count: 64*59 + 12*51 (an NFL Sunday's in-play
  children) + 165*17 + 62*34 + 106*37 = 13,287 markets in ~430 events (plus
  their children). 31,849 + 13,287 = 45,136 markets (38% of MAX_MARKETS) and
  ~1,750 parents + children (well inside MAX_EVENTS 4,000), so NEITHER bound
  is raised. Memory: tracemalloc measured 681 bytes per cached market
  (50,000 synthetic markets, 25 per event, Python 3.12) -- ~31 MB expected,
  ~82 MB at the 120,000 cap, which is the bound that already existed.
  Frames are counted per (sport, frame type) in `frames_by_sport_type` so the
  real rates per sport are read from the heartbeat, not assumed.

PARSING. The provider forwards Pinnacle's own records. Their inner schema is
isolated in `extract_markets` (versioned PARSER_VERSION); a record that does
not validate is counted as UNPARSED and contributes nothing.
"""
from __future__ import annotations

import asyncio
import collections
import functools
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

PARSER_VERSION = "ARCADIA_RAW_V1_OBSERVED_2026_10_01"
#: the full-game (period 0) moneyline's market key in the observed schema
#: (see extract_markets); designations home / away (and draw on a 3-way)
FULL_GAME_MONEYLINE_KEY = "s;0;m"
MAX_EVENTS = 4000
MAX_MARKETS = 120_000
RING = 4096

# ── (RC6) THE SUBSCRIBE SNAPSHOT IS BUILT OFF THE EVENT LOOP ──────────
#
# A snapshot frame carries a whole (stream, sport): the 2026-10-01 soccer
# prematch snapshot was 1,291 events, the cache held 31,849 markets on
# 2026-10-04 and is sized for 45,136 (BOUNDS above). FeedCache.apply ran it
# synchronously on the API event loop -- the owner (pinnapi_owner._own)
# calls `cache.apply(msg)` inline after each recv -- at every connect and
# every resync, and the API loop watchdog has recorded that very frame
# holding the loop: 2.1 s and 2.2 s inside `_replace_event <- _apply <-
# apply <- pinnapi_owner._own` (EventIndexedQuotes' docstring, research-sql
# run 37231263685), before the per-event index; the index made each event
# O(its markets), and the whole snapshot is still one call.
#
# NOW: a snapshot of at least SNAPSHOT_OFFLOOP_MIN_EVENTS events, applied
# from a running event loop, is applied by the SAME `_apply` code to a
# private copy of the cache (`_shadow`: the stores copied, every event's
# metadata copied, counters as deltas) in a dedicated worker thread, and
# the result is SWAPPED IN WHOLE on the loop (`_commit`): readers see the
# cache before the snapshot or after it, never part of it. Every frame
# that arrives meanwhile is queued in arrival order and applied after the
# swap, exactly as if the snapshot had applied inline; the snapshot counts
# as seen for the epoch's resynchronisation only at the swap. A new
# connection discards the build and its queue (its epoch is gone); a build
# whose epoch was revoked meanwhile is discarded, never swapped in. The
# frame's receipt time is the one the owner passed with it, never the
# commit's. Smaller snapshots, and any call without a running loop, apply
# inline as before.
SNAPSHOT_OFFLOOP_MIN_EVENTS = 100
#: per-cache switch default (FeedCache.offload_snapshots)
OFFLOOP_SNAPSHOTS = True
#: notifications / queued frames handled per loop turn after a swap
COMMIT_CHUNK = 2000
#: apply()'s answer for a frame queued behind a build, and for the build
APPLY_QUEUED = "QUEUED_BEHIND_SNAPSHOT_BUILD"
APPLY_BUILDING = "SNAPSHOT_BUILDING_OFF_LOOP"
_BUILDER = None
_BUILDER_LOCK = threading.Lock()


def _builder():
    """ONE dedicated thread for snapshot builds: never queued behind the
    process's default executor (desk sweeps, intel cycles)."""
    global _BUILDER
    with _BUILDER_LOCK:
        if _BUILDER is None:
            from concurrent.futures import ThreadPoolExecutor
            _BUILDER = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="pinnapi-snapshot")
        return _BUILDER


class _ShadowAuthority:
    """The authority a private build answers to: the epoch it was started
    for, granted. The real authority is consulted at the swap."""
    granted = True

    def __init__(self, epoch):
        self.epoch = epoch

    def snapshot_seen(self, *_a) -> None:
        return None

# read refusals (named, never a silent None)
R_NO_AUTHORITY = "FEED_OWNERSHIP_NOT_HELD"
R_NOT_SYNCED = "FEED_EPOCH_NOT_RESYNCHRONIZED"
R_UNKNOWN_MARKET = "FEED_MARKET_NOT_IN_CURRENT_STATE"
R_OLD_EPOCH = "FEED_QUOTE_FROM_A_PREVIOUS_CONNECTION"
R_NO_CHANGE_TIME = "FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE"
R_FUTURE = "FEED_QUOTE_CHANGE_TIME_IN_THE_FUTURE"
R_STALE = "FEED_QUOTE_OLDER_THAN_LIMIT"
R_CLOSED = "FEED_MARKET_CLOSED"
#: the event's latest authoritative list (or live record) did not parse:
#: the prices it replaced are not known to be current (see `_unreadable`)
R_LAST_RECORD_UNPARSED = "FEED_EVENT_LAST_RECORD_UNPARSED_MARKETS_UNKNOWN"

# ── WHICH INSTANT A CHANGE IS DATED BY (see TIME above) ──────────────
#: the changing frame's own provider stamp (the rule since C1)
CHANGE_CLOCK_PROVIDER = "PROVIDER_FRAME_TS"
#: the changing frame carried no provider stamp: OUR receipt of the frame in
#: which the change was observed (observation time, never source time)
CHANGE_CLOCK_LOCAL = "LOCAL_OBSERVATION_OF_THE_CHANGE"
#: the only basis the 30 s rule is measured on (named on every provenance)
FRESHNESS_BASIS = "LAST_OBSERVED_PRICE_CHANGE"
#: the limit the census counts "fresh now" against (the collector's own
#: PINNACLE_MAX_AGE_S; a census figure only, never a decision input)
CENSUS_FRESH_S = 30.0

# ── what confirmed a price (Quote.confirmed_by) ──────────────────────
C_LIVE_REC = "LIVE_RECORD_CARRIED_THE_PRICE"
C_PREMATCH_MARKETS = "PREMATCH_MARKETS_AUTHORITATIVE_LIST"
C_MATCHUP_VERSION = "PREMATCH_MATCHUPS_VERSION_UNCHANGED"
C_SNAPSHOT_RECONFIRM = "SNAPSHOT_RECONFIRMED_THE_SAME_PRICE"
CLOCK_PROVIDER = "PROVIDER_FRAME_TS"
CLOCK_LOCAL = "LOCAL_RECEIPT_NO_PROVIDER_TS"
#: HELD-POSITION READS ONLY (owner closeout 2026-10-07: "if PinnAPI is
#: producing genuinely current frames or authoritative provider timestamps
#: and Xavier is failing to consume them, that is a software defect"): a
#: price that has not changed is current when a PROVIDER-STAMPED frame that
#: asserts the market's current state re-carried it within the SAME 30 s
#: limit. Admitted kinds: a live record carrying the market, the
#: authoritative prematch_markets list, and a prematch_matchups version equal
#: to the markets' version (PinnAPI: markets are re-sent whenever the matchup
#: version is bumped). NOT admitted: the subscribe snapshot (the provider's
#: stored mirror) and any confirmation dated by OUR receipt -- a new poll /
#: receipt time is never freshness.
PROVIDER_CONFIRMATIONS = (C_LIVE_REC, C_PREMATCH_MARKETS, C_MATCHUP_VERSION)
FRESHNESS_BASIS_CONFIRMED = "PROVIDER_STAMPED_CONFIRMATION_OF_UNCHANGED_PRICE"

# ── IN-PLAY: Pinnacle's live game is a CHILD matchup (R30A RC3) ──────
#: A child record is the LIVE PHASE of its parent only when every one of
#: these holds; anything else is named and never priced as the fixture.
CHILD_LIVE_PHASE = "LIVE_PHASE_OF_PARENT"
R_CHILD_PARENT_NOT_HELD = "CHILD_PARENT_NOT_HELD"
R_CHILD_OF_A_CHILD = "CHILD_PARENT_IS_ITSELF_A_CHILD"
R_CHILD_SPORT_DIFFERS = "CHILD_SPORT_DIFFERS_FROM_PARENT"
R_CHILD_NOT_LIVE = "CHILD_NOT_LIVE"
R_CHILD_SPECIAL = "CHILD_IS_A_SPECIAL_MARKET"
R_CHILD_UNITS = "CHILD_UNITS_NOT_REGULAR"
R_CHILD_PARTICIPANTS = "CHILD_PARTICIPANTS_DIFFER_FROM_PARENT"
R_CHILD_DERIVED_SUFFIX = "CHILD_PARTICIPANT_HAS_A_DERIVED_UNITS_SUFFIX"
R_LIVE_PHASE_AMBIGUOUS = "MORE_THAN_ONE_LIVE_PHASE_CHILD"
#: a live child whose prematch parent is not (or no longer) held: PinnAPI's
#: documentation says a live "del" is "event removed by Pinnacle (kicked off,
#: settled, voided)", so the prematch parent can leave the cache at kick-off
#: while its live child stays. Such a record is its OWN fixture when it is
#: the game itself by every test that does not need the parent.
LIVE_GAME_PARENT_NOT_HELD = "LIVE_GAME_WHOSE_PARENT_IS_NOT_HELD"
R_NO_TWO_PARTICIPANTS = "RECORD_DOES_NOT_NAME_TWO_PARTICIPANTS"
#: how a fixture record of `fixture_view` came to price its fixture
B_PREMATCH = "PREMATCH_MATCHUP"
B_LIVE_CHILD = "PARENT_WITH_ITS_LIVE_PHASE_CHILD"
B_ORPHAN_LIVE = "LIVE_CHILD_PARENT_NOT_HELD"
#: '(Games)' is a real tennis child (REST probe run 37232918224: "Holger Rune
#: (Games) v Kyrian Jacquet (Games)", parent 1637453397); '(Corners)' the
#: soccer one. A trailing parenthetical of a derived count is never the game.
DERIVED_SUFFIXES = ("games", "sets", "corners", "bookings", "cards",
                    "points", "maps", "rounds", "kills", "hits+runs+errors")


def _now_ms() -> float:
    return time.time() * 1000.0


@dataclass
class Quote:
    key: str
    event_id: int
    sport_id: Optional[int]
    stream: str                     # live | prematch
    period: Optional[int]
    market_type: Optional[str]
    side: Optional[str]
    line: Optional[float]
    prices: dict                    # designation -> provider price (raw)
    epoch: int
    source_change_ms: Optional[float]
    frame_ts_ms: Optional[float]
    received_ms: float
    open: bool = True
    alternate: Optional[bool] = None
    market_version: Optional[float] = None
    # R30A RC4: the observation clocks (see TIME in the module docstring)
    observed_change_ms: Optional[float] = None
    change_clock: Optional[str] = None
    first_observed_ms: Optional[float] = None
    confirmed_ms: Optional[float] = None
    confirmed_received_ms: Optional[float] = None
    confirmed_by: Optional[str] = None
    confirmed_clock: Optional[str] = None
    #: THE LINE OF EACH DESIGNATION, as Pinnacle states it on that price
    #: (R30A inc-families). `line` above is the FIRST price's points only --
    #: on a spread that is the home OR the away handicap, whichever the
    #: provider listed first, and the two have opposite signs. A line market
    #: is matched to a venue contract by the identical line AND side, so the
    #: line is kept per designation ({'home': -3.5, 'away': 3.5},
    #: {'over': 47.5, 'under': 47.5}); empty on a moneyline.
    points: dict = field(default_factory=dict)
    #: THE FIXTURE this record prices (R30A RC3): the prematch parent's id
    #: when this record is the parent's live-phase child, else its own id.
    #: Set at change notification so held watches and the reactive
    #: scheduler, keyed on fixtures, see a live child's changes.
    fixture_id: Any = None

    def decimal_prices(self) -> dict:
        return {d: american_to_decimal(p) for d, p in self.prices.items()}

    @property
    def change_ms(self) -> Optional[float]:
        """The instant of the last observed change the 30 s rule is
        measured from: the provider stamp; else, only when the changing
        frame had no stamp, our observation of it; else None (unknown)."""
        if self.source_change_ms is not None:
            return self.source_change_ms
        if self.change_clock == CHANGE_CLOCK_LOCAL:
            return self.observed_change_ms
        return None

    def confirm(self, *, frame_ts, rx, kind) -> None:
        """A frame asserted THIS price is current. Monotone: an older frame
        (out-of-order delivery) never moves the confirmation backwards."""
        at, clock = ((frame_ts, CLOCK_PROVIDER) if frame_ts is not None
                     else (rx, CLOCK_LOCAL))
        if at is None:
            return
        if self.confirmed_ms is None or at >= self.confirmed_ms:
            self.confirmed_ms, self.confirmed_received_ms = at, rx
            self.confirmed_by, self.confirmed_clock = kind, clock


@dataclass
class Ring:
    size: int = RING
    xs: collections.deque = field(default_factory=lambda: collections.deque(
        maxlen=RING))

    def add(self, v):
        if v is not None and math.isfinite(v):
            self.xs.append(float(v))

    def summary(self) -> dict:
        s = sorted(self.xs)
        if not s:
            return {"n": 0}

        def q(p):
            return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 1)
        return {"n": len(s), "p50": q(.5), "p95": q(.95), "p99": q(.99),
                "max": round(s[-1], 1)}


class FeedAuthority:
    """Which connection, if any, may publish usable prices."""

    def __init__(self):
        self.epoch = 0
        self.granted = False
        self.reason = R_NO_AUTHORITY
        self.revocations = 0
        self.expected_snapshots: set = set()
        self.seen_snapshots: set = set()

    def grant(self, subscriptions) -> int:
        """A NEW epoch for a freshly opened socket under a held lease.
        Usable only after resync (all expected snapshots seen)."""
        self.epoch += 1
        self.granted = True
        self.reason = R_NOT_SYNCED
        self.expected_snapshots = set(subscriptions)
        self.seen_snapshots = set()
        return self.epoch

    def revoke(self, reason: str = R_NO_AUTHORITY) -> None:
        if self.granted:
            self.revocations += 1
        self.granted = False
        self.reason = reason

    def snapshot_seen(self, epoch: int, stream: str, sport_id) -> None:
        if self.granted and epoch == self.epoch:
            self.seen_snapshots.add((stream, sport_id))

    @property
    def synced(self) -> bool:
        return self.granted and self.expected_snapshots <= self.seen_snapshots

    def state(self) -> dict:
        return {"epoch": self.epoch, "granted": self.granted,
                "synced": self.synced,
                "reason": None if self.synced else self.reason,
                "awaiting_snapshots": sorted(
                    "%s/%s" % x for x in
                    self.expected_snapshots - self.seen_snapshots),
                "revocations": self.revocations}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def american_to_decimal(a) -> Optional[float]:
    """Pinnacle's raw frames carry AMERICAN odds (observed 2026-10-01:
    -164 / 127). Decimal = 1 + a/100 for a >= 100, 1 + 100/|a| for
    a <= -100; anything in (-100, 100) is not an American price."""
    v = _num(a)
    if v is None or -100 < v < 100:
        return None
    return round(1.0 + (v / 100.0 if v > 0 else 100.0 / abs(v)), 6)


def participants(rec: dict) -> dict:
    """{'home': name, 'away': name} from `participants[].alignment`."""
    out = {}
    for p in rec.get("participants") or []:
        if isinstance(p, dict) and p.get("alignment") in ("home", "away") \
                and p.get("name"):
            out[p["alignment"]] = str(p["name"])
    return out


def extract_markets(rec: dict) -> Optional[list]:
    """Pinnacle record -> [(key, fields)], or None when the record does not
    validate. SCHEMA AS OBSERVED in the bounded ws_sample of 2026-10-01
    (PARSER_VERSION): `markets` is a list of dicts with `key` ("s;0;m",
    "s;0;s;0.25", ...), `type` (moneyline|spread|total|team_total),
    `period`, `status`, `isAlternate`, `version` (epoch seconds of the
    market record -- provenance only, never a price-change time) and
    `prices` = [{designation, price (AMERICAN), points?}]."""
    ms = rec.get("markets")
    if ms is None:
        return []
    if not isinstance(ms, list):
        return None
    out = []
    for m in ms:
        if not isinstance(m, dict) or not m.get("type"):
            return None
        prices = m.get("prices")
        pr, line, pts = {}, None, {}
        if isinstance(prices, list):
            for p in prices:
                if not isinstance(p, dict) or "designation" not in p:
                    return None
                pr[str(p["designation"])] = _num(p.get("price"))
                if p.get("points") is not None:
                    pts[str(p["designation"])] = _num(p.get("points"))
                if line is None and p.get("points") is not None:
                    line = _num(p.get("points"))
        elif prices is not None:
            return None
        period = m.get("period")
        side = m.get("side")
        key = m.get("key") or "%s|%s|%s|%s" % (m.get("type"), period or 0,
                                               side or "", "" if line is None
                                               else line)
        out.append((str(key), {
            "market_type": str(m.get("type")), "period": period,
            "side": side, "line": line, "prices": pr, "points": pts,
            "open": (m.get("status") in (None, "open")),
            "alternate": m.get("isAlternate"),
            "market_version": _num(m.get("version"))}))
    return out


def _moved(prev: "Quote", f: dict) -> bool:
    """A CHANGE is a new price OR a new line under the same market key.

    Pinnacle keys a spread or total by its line ("s;0;s;-3.5"), so a moved
    line is usually a new key -- but a market whose key does not carry the
    line (a team total keyed per side, the keyless fallback) can move its
    points while keeping its prices. Reading only the prices would carry the
    OLD line's change time onto the new line, and a line that has just moved
    would look as old as the line it replaced."""
    return (prev.prices != f["prices"]
            or dict(prev.points or {}) != dict(f.get("points") or {}))


def _observed_change(frame_ts, rx) -> tuple:
    """(source_change_ms, observed_change_ms, change_clock) for a change
    observed in a frame stamped `frame_ts` (None when the provider sent no
    stamp) and received by us at `rx`. The source time is NEVER filled from
    our clock: without a stamp it stays None and the change is dated by our
    observation of it, labelled as such."""
    if frame_ts is not None:
        return frame_ts, rx, CHANGE_CLOCK_PROVIDER
    return None, rx, CHANGE_CLOCK_LOCAL


def _carried_change(prev) -> tuple:
    """An unchanged price keeps the clocks of the change that set it."""
    return (prev.source_change_ms, prev.observed_change_ms,
            prev.change_clock)


def closed_periods(rec: dict) -> set:
    """Periods the record marks anything but open (closed / settled): every
    market of such a period is closed, listed or not."""
    ps = rec.get("periods")
    if not isinstance(ps, list):
        return set()
    return {p.get("period") for p in ps if isinstance(p, dict)
            and p.get("status") not in (None, "open")}


class EventIndexedQuotes(dict):
    """(event_id, key) -> Quote, WITH A PER-EVENT KEY INDEX kept on every
    mutation (R30A runtime, 2026-10-04).

    THE STALL THIS REMOVES. Every per-event operation of the cache found an
    event's quotes by scanning ALL of them -- `[k for k in self.quotes if
    k[0] == eid]` -- in `_replace_event` (each event of a snapshot, each
    prematch_markets frame), `_merge_event` (each live frame), `_drop_event`
    and `_bound`. With MAX_MARKETS 120,000 quotes, a snapshot of N events is
    N full scans: the API's own loop watchdog recorded the event loop held
    for 2.1 s (ended at 4.0 s) and 2.2 s (2.5 s) inside
    `pinnapi_feed._replace_event <- _apply <- apply <- pinnapi_owner._own`
    (ingestion_state api.loop_stalls, research-sql run 37231263685), and
    while the loop is held every 2-3 s budget in the process expires -- the
    reactive audit, the research tick, the feed heartbeat (render-ops logs
    15:40-20:10Z: loop stalls >= 2 s in the same minutes as all three
    timeout classes). Now an event's keys come from `by_event`, O(its own
    markets), whatever the cache holds.

    THE INDEX CANNOT DRIFT: it is maintained here, on the dict's own
    mutation methods, so every writer -- the cache, and the tests that set
    or clear `cache.quotes` directly -- keeps it exact. Iteration order, the
    values and every read are the plain dict's (tests replay frame sequences
    against the scanning implementation and compare).

    R30A inc-pinnapi (fix stage, 2026-10-05): the SAME class, verbatim, so
    the two streams converge on one store. On this branch the scan was worse
    than the runtime stream measured: the matchup-version confirmation
    (`_version_confirm`) ran it once per RECORD of every prematch_matchups
    frame -- every 5 s per sport -- 1.18 s of loop time per frame at 1,300
    events / 32,500 quotes (adversarial verification, verif/bench_matchups.py;
    base 96fd349: 0.00 s)."""

    __slots__ = ("by_event",)

    def __init__(self, *a, **kw):
        super().__init__()
        self.by_event: dict = {}
        if a or kw:
            self.update(*a, **kw)

    @staticmethod
    def _eid(k):
        return k[0] if isinstance(k, tuple) and k else k

    def _unindex(self, k):
        eid = self._eid(k)
        keys = self.by_event.get(eid)
        if keys is not None:
            keys.discard(k)
            if not keys:
                del self.by_event[eid]

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self.by_event.setdefault(self._eid(k), set()).add(k)

    def __delitem__(self, k):
        super().__delitem__(k)
        self._unindex(k)

    _MISSING = object()

    def pop(self, k, default=_MISSING):
        if k in self:
            v = super().pop(k)
            self._unindex(k)
            return v
        if default is EventIndexedQuotes._MISSING:
            raise KeyError(k)
        return default

    def popitem(self):
        k, v = super().popitem()
        self._unindex(k)
        return k, v

    def clear(self):
        super().clear()
        self.by_event.clear()

    def setdefault(self, k, default=None):
        if k not in self:
            self[k] = default
        return self[k]

    def update(self, *a, **kw):
        for k, v in dict(*a, **kw).items():
            self[k] = v

    def keys_of(self, eid) -> list:
        """This event's quote keys (a list copy: safe to delete while
        iterating), O(its own markets)."""
        return list(self.by_event.get(eid, ()))

    @classmethod
    def copy_of(cls, other: "EventIndexedQuotes") -> "EventIndexedQuotes":
        """An independent copy (RC6, the off-loop snapshot build): the same
        items and an index of its own, built at C speed (dict.update does
        not call the per-item __setitem__); the Quote values are shared --
        a snapshot build never mutates an existing Quote, it replaces it."""
        out = cls()
        dict.update(out, other)
        out.by_event = {e: set(ks) for e, ks in other.by_event.items()}
        return out


# ── IN-PLAY: WHICH CHILD RECORD IS THE LIVE GAME (R30A RC3) ──────────
#
# THE DEFECT. Pinnacle's in-play game is a CHILD matchup whose parentId is
# the prematch matchup (2026-10-01 ws_sample run 36940200143: live records
# 1637543257 -> parent 1637360364 and 1637550485 -> parent 1637451463, main
# market, units 'Regular', no 'special'; REST probe run 37232918224: all 3
# live NFL events carry a parent_id). The census view and the primary
# selector skipped every record with a parentId, so no in-play price was
# ever matched: the stale prematch parent was matched instead, or nothing.
#
# WHY EVERY CONDITION. Specials are children too, and carry the moneyline
# key: snapshot record 1637550528 ('Both Teams To Score?', key 's;0;m',
# participants Curacao / Trinidad and Tobago -- the SAME names as its game)
# is a prop; a filter on names alone would price it as the game. Its
# parentId is the live child, not the prematch matchup. Derived-count
# children rename the participants ('Holger Rune (Games)'). So the live
# phase is: parentId set, the parent held and itself a parent of the same
# sport, isLive true, no special market name, units 'Regular', and the
# participants exactly the parent's with no derived-units suffix.
def _derived_suffix(name) -> bool:
    s = str(name or "").strip().lower()
    return s.endswith(")") and any(s.endswith("(%s)" % w)
                                   for w in DERIVED_SUFFIXES)


def classify_child(child: dict, parent: Optional[dict]) -> str:
    """CHILD_LIVE_PHASE, or the named reason this child record is not the
    live phase of `parent` (the record its parentId names)."""
    if not isinstance(parent, dict):
        return R_CHILD_PARENT_NOT_HELD
    if parent.get("parentId"):
        return R_CHILD_OF_A_CHILD
    if child.get("sport_id") != parent.get("sport_id"):
        return R_CHILD_SPORT_DIFFERS
    if child.get("isLive") is not True:
        return R_CHILD_NOT_LIVE
    # A null 'special' names no special market; any value is a prop.
    if child.get("special") not in (None, ""):
        return R_CHILD_SPECIAL
    if child.get("units") != "Regular":
        return R_CHILD_UNITS
    cp, pp = participants(child), participants(parent)
    if any(_derived_suffix(n) for n in cp.values()):
        return R_CHILD_DERIVED_SUFFIX
    if len(cp) != 2 or cp != pp:
        return R_CHILD_PARTICIPANTS
    return CHILD_LIVE_PHASE


def children_by_parent(events) -> dict:
    """{parent id: [child ids]} in one pass over the cache's events."""
    out: dict = {}
    for eid, ev in events.items():
        pid = ev.get("parentId") if isinstance(ev, dict) else None
        if pid:
            out.setdefault(pid, []).append(eid)
    return out


def live_phase_of(events, parent_id, children=None) -> tuple:
    """(child id, None) when exactly one child is the live phase of
    `parent_id`; (None, None) when none is (the feed shows no live game);
    (None, MORE_THAN_ONE_LIVE_PHASE_CHILD) when two are -- never a guess."""
    parent = events.get(parent_id)
    kids = (children if children is not None
            else children_by_parent(events)).get(parent_id) or []
    live = [c for c in kids
            if classify_child(events.get(c) or {}, parent) == CHILD_LIVE_PHASE]
    if len(live) > 1:
        return None, R_LIVE_PHASE_AMBIGUOUS
    return (live[0], None) if live else (None, None)


def classify_orphan(child: dict) -> str:
    """LIVE_GAME_PARENT_NOT_HELD when a child record whose parent is not
    held is the live game itself by every test `classify_child` makes that
    does not need the parent (live, no special market, units 'Regular', two
    participants without a derived-units suffix); else the named reason."""
    if child.get("isLive") is not True:
        return R_CHILD_NOT_LIVE
    if child.get("special") not in (None, ""):
        return R_CHILD_SPECIAL
    if child.get("units") != "Regular":
        return R_CHILD_UNITS
    cp = participants(child)
    if any(_derived_suffix(n) for n in cp.values()):
        return R_CHILD_DERIVED_SUFFIX
    if len(cp) != 2 or cp.get("home") == cp.get("away"):
        return R_CHILD_PARTICIPANTS
    return LIVE_GAME_PARENT_NOT_HELD


def fixture_view(events) -> tuple:
    """([fixture record], Counter of records that price no fixture, by
    named reason) -- THE one reading of which cached record prices which
    fixture, shared by the census, the primary h2h selector and the
    PinnAPI-native discovery (R30A RC3).

    A fixture record is {"id": the fixture's identity (the prematch
    parent's id, or a parentless live child's own), "quote_id": the record
    whose markets price it NOW (its one live-phase child while in play, else
    itself), "sport_id", "home", "away", "startTime", "live", "basis",
    "parent_id", "league"}. Before this, every record with a parentId was
    skipped, so no in-play price was ever matched (2026-10-01 ws_sample:
    live 1637543257 -> parent 1637360364)."""
    kids = children_by_parent(events)
    out, skipped = [], collections.Counter()
    for eid, ev in events.items():
        if not isinstance(ev, dict):
            continue
        pid = ev.get("parentId")
        if pid:
            parent = events.get(pid)
            if parent is not None:
                # priced (or not) through its parent below; counted here
                why = classify_child(ev, parent)
                if why != CHILD_LIVE_PHASE:
                    skipped[why] += 1
                continue
            why = classify_orphan(ev)
            if why != LIVE_GAME_PARENT_NOT_HELD:
                skipped["%s|PARENT_NOT_HELD" % why] += 1
                continue
            p = participants(ev)
            out.append({"id": eid, "quote_id": eid,
                        "sport_id": ev.get("sport_id"),
                        "home": p["home"], "away": p["away"],
                        "startTime": ev.get("startTime"), "live": True,
                        "basis": B_ORPHAN_LIVE, "parent_id": pid,
                        "league": ev.get("league")})
            continue
        p = participants(ev)
        if not p.get("home") or not p.get("away"):
            skipped[R_NO_TWO_PARTICIPANTS] += 1
            continue
        child, why = live_phase_of(events, eid, kids)
        if why:
            skipped[why] += 1
            continue
        out.append({"id": eid, "quote_id": child if child is not None
                    else eid, "sport_id": ev.get("sport_id"),
                    "home": p["home"], "away": p["away"],
                    "startTime": ev.get("startTime"),
                    "live": child is not None or ev.get("isLive") is True,
                    "basis": B_LIVE_CHILD if child is not None
                    else B_PREMATCH, "parent_id": None,
                    "league": ev.get("league")})
    return out, skipped


def canonical_id(events, event_id):
    """The FIXTURE identity of a record: its parent's id when it is the
    live phase of a held parent (prematch -> live continuity), else its
    own."""
    ev = events.get(event_id)
    pid = ev.get("parentId") if isinstance(ev, dict) else None
    if pid and classify_child(ev, events.get(pid)) == CHILD_LIVE_PHASE:
        return pid
    return event_id


class FeedCache:
    def __init__(self, *, authority: Optional[FeedAuthority] = None,
                 extract: Callable = extract_markets,
                 max_events: int = MAX_EVENTS,
                 max_markets: int = MAX_MARKETS):
        self.authority = authority or FeedAuthority()
        self.extract = extract
        self.max_events, self.max_markets = max_events, max_markets
        # frames received per (PinnAPI sport id, frame type): the measured
        # per-sport rate the R30A scope widening is judged on (bounded:
        # at most 12 sports x the handful of frame types)
        self.frames_by_sport_type = collections.Counter()
        self.confirmations = collections.Counter()
        self.events: "collections.OrderedDict[int, dict]" = \
            collections.OrderedDict()
        # (event_id, key) -> Quote, indexed per event (EventIndexedQuotes)
        self.quotes: EventIndexedQuotes = EventIndexedQuotes()
        self.on_change = None  # synchronous, bounded notification; never I/O
        self._touched = set()
        self.counts = collections.Counter()
        self.provider_to_receipt = Ring()
        self.receipt_to_eval = Ring()
        self.last_frame_received_ms: Optional[float] = None
        self.last_change_received_ms: Optional[float] = None
        # (RC6) the off-loop snapshot build (see SNAPSHOT_OFFLOOP_MIN_EVENTS)
        self.offload_snapshots = OFFLOOP_SNAPSHOTS
        self._pending: Optional[dict] = None
        self._backlog: collections.deque = collections.deque()
        self.snapshot_build_ms = Ring()
        # (RC6.1 api-stall2) THE CACHE'S GENERATION: +1 whenever its events
        # or quotes may have changed -- every applied frame (`_apply`, inline
        # or drained), a new connection, a snapshot swapped in. The only
        # writers of `events` are those three, all on the event loop. A
        # reader that derives something from the events (pinnapi_primary.
        # current_index) may keep it while the generation is unchanged.
        self.generation = 0

    # ── lifecycle ────────────────────────────────────────────────────
    def new_connection(self, subscriptions) -> int:
        """A new socket epoch. Everything from earlier epochs is dropped: a
        reconnect never leaves an old price usable -- nor a snapshot still
        being built for the old socket, nor the frames queued behind it."""
        self._discard_pending("snapshot_builds_discarded_new_connection")
        self.generation += 1
        self.events.clear()
        self.quotes.clear()
        self.counts["epochs"] += 1
        return self.authority.grant(subscriptions)

    def _discard_pending(self, why: str) -> None:
        pend, self._pending = self._pending, None
        dropped = len(self._backlog)
        self._backlog.clear()
        if pend is None:
            return
        self.counts[why] += 1
        if dropped:
            self.counts["frames_dropped_with_a_discarded_build"] += dropped
        task = pend.get("task")
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if task is not None and not task.done() and task is not current:
            task.cancel()

    def lost(self, reason: str) -> None:
        self.authority.revoke(reason)

    # ── in-play identity (pure reads of the event metadata) ─────────
    def live_phase(self, fixture_id) -> tuple:
        """(live child id | None, reason | None) for a parent fixture."""
        return live_phase_of(self.events, fixture_id)

    def fixture_quote_id(self, fixture_id) -> tuple:
        """(the record whose quotes price the fixture NOW, reason): its one
        live-phase child while in play, else the fixture itself; (None,
        MORE_THAN_ONE_LIVE_PHASE_CHILD) when the live game is ambiguous."""
        child, why = self.live_phase(fixture_id)
        if why:
            return None, why
        return (child if child is not None else fixture_id), None

    def canonical_id(self, event_id):
        return canonical_id(self.events, event_id)

    # ── ingestion ────────────────────────────────────────────────────
    def apply(self, msg: dict, *, epoch: int,
              received_ms: Optional[float] = None) -> str:
        if self._pending is not None:
            # a snapshot is being built (or swapped in): every later frame
            # waits its turn, in arrival order (bounded by the build: one
            # snapshot's apply time of frames)
            self._backlog.append((msg, epoch, received_ms))
            self.counts["frames_queued_behind_snapshot_build"] += 1
            return APPLY_QUEUED
        if self._offloadable(msg, epoch):
            return self._start_build(msg, epoch, received_ms)
        return self._apply_inline(msg, epoch, received_ms)

    # ── (RC6) the off-loop snapshot build ──────────────────────────
    def _offloadable(self, msg, epoch) -> bool:
        if not self.offload_snapshots or not isinstance(msg, dict) or \
                msg.get("type") != "snapshot":
            return False
        evs = msg.get("events")
        if not isinstance(evs, list) or \
                len(evs) < SNAPSHOT_OFFLOOP_MIN_EVENTS:
            return False
        if epoch != self.authority.epoch or not self.authority.granted:
            return False                  # inline: counted and ignored
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        return True

    def _shadow(self, epoch) -> "FeedCache":
        """A private copy to build on: the two stores copied, every event's
        metadata dict copied (the build updates them in place), counters
        started empty (merged back as deltas). Runs on the loop: copying
        the stores is a C-speed pass, nothing like applying them."""
        sh = FeedCache(authority=_ShadowAuthority(epoch), extract=self.extract,
                       max_events=self.max_events,
                       max_markets=self.max_markets)
        sh.offload_snapshots = False
        sh.events = collections.OrderedDict(
            (k, dict(v) if isinstance(v, dict) else v)
            for k, v in self.events.items())
        sh.quotes = (EventIndexedQuotes.copy_of(self.quotes)
                     if isinstance(self.quotes, EventIndexedQuotes)
                     else type(self.quotes)(self.quotes))
        return sh

    def _start_build(self, msg, epoch, received_ms) -> str:
        loop = asyncio.get_running_loop()
        rx = received_ms if received_ms is not None else _now_ms()
        token = object()
        t0 = time.monotonic()
        sh = self._shadow(epoch)
        fut = loop.run_in_executor(_builder(), functools.partial(
            sh._apply, msg, epoch=epoch, received_ms=rx))
        self._pending = {"token": token, "epoch": epoch, "at": t0}
        self._pending["task"] = loop.create_task(
            self._commit(token, fut, sh, msg, epoch, rx, t0))
        self.counts["snapshot_builds_started"] += 1
        return APPLY_BUILDING

    def _ours(self, token) -> bool:
        return self._pending is not None and self._pending["token"] is token

    async def _commit(self, token, fut, sh, msg, epoch, rx, t0) -> None:
        try:
            await fut
            built = True
        except asyncio.CancelledError:
            raise
        except Exception:                                       # noqa: BLE001
            built = False
        if not self._ours(token):
            return                        # superseded: a new connection
        if not built:
            # the same code failed in the thread: apply it inline, as before
            # RC6. If that raises too, the snapshot was never seen: the epoch
            # stays unsynchronised and every read refuses by that name
            # (FEED_EPOCH_NOT_RESYNCHRONIZED) until the next connection
            self.counts["snapshot_builds_failed"] += 1
            try:
                self._apply_inline(msg, epoch, rx)
            except Exception:                                   # noqa: BLE001
                self.counts["snapshot_apply_errors"] += 1
        elif self.authority.granted and self.authority.epoch == epoch:
            self._swap_in(sh)
            if msg.get("sport_id") is not None:
                self.authority.snapshot_seen(epoch, msg.get("stream"),
                                             msg.get("sport_id"))
            self.counts["snapshot_builds_swapped_in"] += 1
            self.snapshot_build_ms.add((time.monotonic() - t0) * 1000.0)
            await self._notify(sh._touched, token)
        else:
            # revoked (or re-granted) while it was built: never swapped in
            self.counts["snapshot_builds_discarded_authority"] += 1
        await self._drain(token)

    def _swap_in(self, sh) -> None:
        self.generation += 1
        self.events, self.quotes = sh.events, sh.quotes
        self.counts.update(sh.counts)
        self.confirmations.update(sh.confirmations)
        for fk, n in sh.frames_by_sport_type.items():
            if fk in self.frames_by_sport_type or \
                    len(self.frames_by_sport_type) < 256:
                self.frames_by_sport_type[fk] += n
        if sh.last_frame_received_ms is not None:
            self.last_frame_received_ms = sh.last_frame_received_ms
        if sh.last_change_received_ms is not None:
            self.last_change_received_ms = sh.last_change_received_ms

    async def _notify(self, touched, token) -> None:
        """The snapshot's change notifications, as `apply` sends them, in
        bounded slices (frames keep queueing meanwhile)."""
        if self.on_change is None:
            return
        keys = list(touched)
        for i in range(0, len(keys), COMMIT_CHUNK):
            if i:
                await asyncio.sleep(0)
                if not self._ours(token) or self.on_change is None:
                    return
            self._notify_keys(keys[i:i + COMMIT_CHUNK])

    async def _drain(self, token) -> None:
        """The frames queued behind the build, in arrival order; a queued
        snapshot that is itself off-loadable takes the rest of the queue
        with it."""
        n = 0
        while self._backlog:
            if not self._ours(token):
                return
            m, e, r = self._backlog.popleft()
            if self._offloadable(m, e):
                rest = self._backlog
                self._backlog = collections.deque()
                self._pending = None
                self._start_build(m, e, r)
                self._backlog = rest
                return
            try:
                self._apply_inline(m, e, r)
            except Exception:                                   # noqa: BLE001
                self.counts["queued_frame_apply_errors"] += 1
            n += 1
            if n % COMMIT_CHUNK == 0:
                await asyncio.sleep(0)
        if self._ours(token):
            self._pending = None

    def _apply_inline(self, msg: dict, epoch: int,
                      received_ms: Optional[float] = None) -> str:
        self._touched.clear()
        result = self._apply(msg, epoch=epoch, received_ms=received_ms)
        # Notify after the entire frame (including closed periods and bounds)
        # has applied. Consumers re-read authority; no callback can trade here.
        if self.on_change is not None:
            self._notify_keys(self._touched)
        return result

    def _notify_keys(self, keys) -> None:
        # a held watch in the chain also hears PROVIDER-STAMPED
        # confirmations of unchanged prices (pinnapi_held); every other
        # consumer hears changes only, as before
        held = getattr(self.on_change, "_held_chain", False)
        for key in keys:
            quote = self.quotes.get(key)
            if quote is not None and (
                    quote.change_ms is not None or (
                        held and quote.confirmed_ms is not None
                        and quote.confirmed_clock == CLOCK_PROVIDER)):
                try:
                    quote.fixture_id = self.canonical_id(quote.event_id)
                    self.on_change(quote)
                except Exception:
                    self.counts["change_notification_errors"] += 1

    def _apply(self, msg: dict, *, epoch: int,
               received_ms: Optional[float] = None) -> str:
        """Apply one envelope from connection `epoch`. Returns what it was."""
        self.generation += 1
        rx = received_ms if received_ms is not None else _now_ms()
        if epoch != self.authority.epoch or not self.authority.granted:
            self.counts["frames_from_revoked_epoch"] += 1
            return "IGNORED_REVOKED_EPOCH"
        t = msg.get("type") if isinstance(msg, dict) else None
        self.counts["frames"] += 1
        self.last_frame_received_ms = rx
        ts = _num(msg.get("ts")) if isinstance(msg, dict) else None
        if isinstance(msg, dict) and msg.get("sport_id") is not None:
            fk = "%s|%s" % (str(msg.get("sport_id"))[:8], str(t)[:24])
            if fk in self.frames_by_sport_type or \
                    len(self.frames_by_sport_type) < 256:
                self.frames_by_sport_type[fk] += 1
        if t in ("ping", "pong", "subscribed", "unsubscribed"):
            return t                      # heartbeats never touch a quote
        if t == "error":
            self.counts["provider_errors"] += 1
            return "error"
        if t == "snapshot":
            stream, sport = msg.get("stream"), msg.get("sport_id")
            for ev in msg.get("events") or []:
                if isinstance(ev, dict) and ev.get("id") is not None:
                    # The subscribe snapshot is the provider's stored mirror:
                    # first sight, never a confirmation -- except of a price
                    # this epoch already held unchanged (a re-subscribe).
                    self._replace_event(ev, stream=stream, sport=sport,
                                        epoch=epoch, frame_ts=ts, rx=rx,
                                        as_change=False,
                                        reconfirm_kind=C_SNAPSHOT_RECONFIRM,
                                        markets_version=_num(
                                            ev.get("version")))
            if msg.get("sport_id") is not None:
                self.authority.snapshot_seen(epoch, stream, sport)
            return "snapshot"
        if t == "live":
            if ts is not None:
                self.provider_to_receipt.add(rx - ts)
            rec = msg.get("rec") or {}
            op = msg.get("op")
            eid = rec.get("id")
            if eid is None:
                self.counts["unparsed"] += 1
                return "UNPARSED"
            if op == "del":
                self._drop_event(eid)
                return "del"
            self._merge_event(rec, stream="live", sport=msg.get("sport_id"),
                              epoch=epoch, frame_ts=ts, rx=rx)
            return "live:%s" % op
        if t == "prematch_markets":
            eid = msg.get("matchup_id")
            data = msg.get("data")
            if eid is None or not isinstance(data, list):
                self.counts["unparsed"] += 1
                return "UNPARSED"
            # authoritative: replace this event's markets; a price that
            # differs from the one we held is a CHANGE at ts, an unchanged
            # one keeps its earlier change time (or none). R30A RC4 (d): a
            # key that is NEW to an event whose previous AUTHORITATIVE list
            # on this epoch did not carry it opened between the two lists --
            # an observed change at ts. First sight after a reconnect (no
            # earlier authoritative list on this epoch) stays first sight.
            #
            # ONLY A LIST THAT PARSED IS AUTHORITATIVE (adversarial
            # verification, finding 3). The "opened between two lists" rule
            # holds only when the earlier list is the one IMMEDIATELY before:
            # a list we could not read may itself have opened the key, so it
            # breaks the chain -- `_unreadable` clears the flag, and the next
            # parsed list is first sight again. Before, an unparsed list set
            # the flag and the next list dated a spread never seen to change
            # as a change at its own stamp (read `ok`, age 5 s; >= 240 s
            # understated).
            held = self.events.get(eid) or {}
            versions = {_num(m.get("version")) for m in data
                        if isinstance(m, dict)} - {None}
            parsed = self._replace_event(
                {"id": eid, "markets": data}, stream="prematch",
                sport=msg.get("sport_id"), epoch=epoch, frame_ts=ts, rx=rx,
                as_change=True, keep_meta=True,
                confirm_kind=C_PREMATCH_MARKETS,
                new_key_is_change=bool(held.get("_authoritative")),
                markets_version=(versions.pop() if len(versions) == 1
                                 else _num(held.get("version"))))
            if parsed and eid in self.events:
                self.events[eid]["_authoritative"] = True
            return "prematch_markets"
        if t == "prematch_matchups":
            for ev in msg.get("data") or []:
                if isinstance(ev, dict) and ev.get("id") is not None:
                    self._version_confirm(ev, epoch=epoch, frame_ts=ts,
                                          rx=rx)
                    self._touch_meta(ev, stream="prematch",
                                     sport=msg.get("sport_id"))
            return "prematch_matchups"
        self.counts["unknown_type"] += 1
        return "UNKNOWN"

    def _version_confirm(self, ev, *, epoch, frame_ts, rx):
        """A prematch_matchups record whose matchup version EQUALS the
        version this event's markets were delivered at: Pinnacle has not
        bumped the matchup since, so every price we hold for it is still
        its current price at this frame (PinnAPI docs: prematch_markets
        "fired only when Pinnacle bumped matchup.version"). A different
        version confirms nothing -- the refresh is pending -- and is
        counted, never guessed at."""
        held = self.events.get(ev["id"])
        v = _num(ev.get("version"))
        mv = None if held is None else held.get("_markets_version")
        if held is None or v is None or mv is None:
            return
        if v != mv:
            self.counts["matchup_version_advanced_markets_pending"] += 1
            return
        n = 0
        for k in self._keys_of(ev["id"]):
            q = self.quotes.get(k)
            if q is not None and q.epoch == epoch:
                q.confirm(frame_ts=frame_ts, rx=rx, kind=C_MATCHUP_VERSION)
                n += 1
        if n:
            self.confirmations[C_MATCHUP_VERSION] += n

    def _keys_of(self, eid) -> list:
        """This event's quote keys. Uses the store's per-event index when
        it keeps one (R30A runtime's EventIndexedQuotes), else scans."""
        idx = getattr(self.quotes, "keys_of", None)
        if callable(idx):
            return idx(eid)
        return [k for k in self.quotes if k[0] == eid]

    def _touch_meta(self, ev, *, stream, sport):
        eid = ev["id"]
        cur = self.events.get(eid) or {"id": eid, "stream": stream,
                                       "sport_id": sport}
        # 'units' and 'special' decide whether a child record is the live
        # game or a prop/derived-count matchup (classify_child).
        for k in ("participants", "league", "startTime", "status", "isLive",
                  "parentId", "periods", "version", "type", "units",
                  "special"):
            if k in ev:
                cur[k] = ev[k]
        self.events[eid] = cur
        self.events.move_to_end(eid)
        self._bound()

    def _drop_event(self, eid):
        self.events.pop(eid, None)
        for k in self._keys_of(eid):
            del self.quotes[k]
        self.counts["events_deleted"] += 1

    def _unreadable(self, eid, *, frame_ts, rx) -> None:
        """A record for a HELD event did not parse (adversarial
        verification, finding 3, fix stage 2026-10-05).

        An authoritative prematch_markets list REPLACES the event's markets
        and a live record may change any of them, so the prices we held for
        the event are no longer known to be current: before, they stayed
        readable, and a money line re-priced by a list we could not read
        still read `ok` inside 30 s of its earlier change. They leave the
        current state now (counted), the read names why
        (R_LAST_RECORD_UNPARSED), the "opened between two authoritative
        lists" chain is broken (`_authoritative` cleared) and no matchup
        version confirms anything until a list parses. The next record that
        parses is FIRST SIGHT for every key: no age until a change is
        observed -- never a guessed one, never a change dated at its stamp."""
        ev = self.events.get(eid)
        if ev is None:
            return
        n = 0
        for k in self._keys_of(eid):
            del self.quotes[k]
            n += 1
        if n:
            self.counts["markets_dropped_by_an_unparsed_record"] += n
        ev.pop("_authoritative", None)
        ev["_markets_version"] = None
        ev["_unparsed_since"] = frame_ts if frame_ts is not None else rx

    def _replace_event(self, ev, *, stream, sport, epoch, frame_ts, rx,
                       as_change, keep_meta=False, confirm_kind=None,
                       reconfirm_kind=None, new_key_is_change=False,
                       markets_version=None) -> bool:
        """Replace the event's markets with the record's. False (and the
        event's held prices withdrawn, `_unreadable`) when it did not
        parse."""
        eid = ev["id"]
        parsed = self.extract(ev)
        if parsed is None:
            self.counts["unparsed"] += 1
            self._unreadable(eid, frame_ts=frame_ts, rx=rx)
            return False
        if not keep_meta:
            self._touch_meta(ev, stream=stream, sport=sport)
        else:
            self.events.setdefault(eid, {"id": eid, "stream": stream,
                                         "sport_id": sport})
            self.events.move_to_end(eid)
        old = {k: self.quotes[k] for k in self._keys_of(eid)}
        for k in old:
            del self.quotes[k]
        closed = closed_periods(ev)
        for key, f in parsed:
            if not f["open"] or (f["period"] or 0) in closed:
                continue
            prev = old.get((eid, key))
            changed = prev is None or _moved(prev, f)
            same = prev is not None and not changed
            if as_change and changed and prev is not None:
                clocks = _observed_change(frame_ts, rx)
            elif as_change and prev is None and new_key_is_change:
                # this event's previous authoritative list on this epoch did
                # not carry the key: it opened between the two lists
                clocks = _observed_change(frame_ts, rx)
                self.counts["new_key_observed_as_change"] += 1
            elif same:
                clocks = _carried_change(prev)
            else:
                # first sight (a snapshot, or the first authoritative list
                # after a reconnect), or a snapshot that differs from what
                # we held: no change was observed, so the age is unknown
                clocks = (None, None, None)
            q = self._put(eid, key, f, stream, sport, epoch, clocks,
                          frame_ts, rx,
                          first_observed=(prev.first_observed_ms if same
                                          else rx))
            if same:
                self._carry_confirmation(q, prev)
            kind = confirm_kind or (reconfirm_kind if same else None)
            if kind is not None:
                q.confirm(frame_ts=frame_ts, rx=rx, kind=kind)
                self.confirmations[kind] += 1
        if eid in self.events:
            self.events[eid]["_markets_version"] = markets_version
            self.events[eid].pop("_unparsed_since", None)
        self._bound()
        return True

    def _merge_event(self, rec, *, stream, sport, epoch, frame_ts, rx):
        eid = rec["id"]
        self._touch_meta(rec, stream=stream, sport=sport)
        closed = closed_periods(rec)
        for k in [k for k in self._keys_of(eid)
                  if (self.quotes[k].period or 0) in closed]:
            del self.quotes[k]
        parsed = self.extract(rec)
        if parsed is None:
            self.counts["unparsed"] += 1
            self._unreadable(eid, frame_ts=frame_ts, rx=rx)
            return
        # after a record that did not parse, a key this record carries is
        # FIRST SIGHT, not "a change at this stamp": its earlier price was
        # withdrawn unseen (`_unreadable`), so nothing says it moved here
        after_unparsed = (self.events.get(eid) or {}).pop("_unparsed_since",
                                                          None) is not None
        for key, f in parsed:
            k = (eid, key)
            if not f["open"] or (f["period"] or 0) in closed:
                self.quotes.pop(k, None)
                self.counts["markets_closed"] += 1
                continue
            prev = self.quotes.get(k)
            changed = prev is None or _moved(prev, f)
            if prev is None and after_unparsed:
                clocks = (None, None, None)
                self.counts["first_sight_after_an_unparsed_record"] += 1
            else:
                clocks = (_observed_change(frame_ts, rx) if changed
                          else _carried_change(prev))
            if changed:
                self.counts["price_changes"] += 1
                self.last_change_received_ms = rx
            q = self._put(eid, key, f, stream, sport, epoch, clocks, frame_ts,
                          rx, first_observed=(rx if changed
                                              else prev.first_observed_ms))
            if not changed:
                self._carry_confirmation(q, prev)
            # a Pinnacle live push carrying this market open at this price
            q.confirm(frame_ts=frame_ts, rx=rx, kind=C_LIVE_REC)
            self.confirmations[C_LIVE_REC] += 1
        if rec.get("version") is not None and eid in self.events:
            self.events[eid]["_markets_version"] = _num(rec.get("version"))
        self._bound()

    @staticmethod
    def _carry_confirmation(q, prev) -> None:
        q.confirmed_ms, q.confirmed_received_ms = (prev.confirmed_ms,
                                                   prev.confirmed_received_ms)
        q.confirmed_by, q.confirmed_clock = (prev.confirmed_by,
                                             prev.confirmed_clock)

    def _put(self, eid, key, f, stream, sport, epoch, clocks, frame_ts, rx,
             first_observed=None):
        """`clocks` = (source_change_ms, observed_change_ms, change_clock)."""
        self._touched.add((eid, key))
        change, observed, clock = clocks
        if change is None and clock == CHANGE_CLOCK_LOCAL:
            self.counts["change_dated_by_local_observation"] += 1
        q = self.quotes[(eid, key)] = Quote(
            key=key, event_id=eid, sport_id=sport, stream=stream,
            period=f["period"], market_type=f["market_type"],
            side=f["side"], line=f["line"], prices=dict(f["prices"]),
            observed_change_ms=observed, change_clock=clock,
            epoch=epoch, source_change_ms=change, frame_ts_ms=frame_ts,
            received_ms=rx, open=True, alternate=f["alternate"],
            market_version=f.get("market_version"),
            points=dict(f.get("points") or {}),
            first_observed_ms=(rx if first_observed is None
                               else first_observed))
        return q

    def _bound(self):
        while len(self.events) > self.max_events:
            eid, _ = self.events.popitem(last=False)
            for k in self._keys_of(eid):
                del self.quotes[k]
            self.counts["events_evicted"] += 1
        if len(self.quotes) > self.max_markets:
            # evict whole least-recently-touched events until within bound
            for eid in list(self.events):
                if len(self.quotes) <= self.max_markets:
                    break
                self._drop_event(eid)
                self.counts["events_evicted"] += 1

    # ── the ONE read path for Derek and Xavier ──────────────────────
    def read(self, event_id, key, *, evaluated_ms: Optional[float] = None,
             max_age_s: float = 30.0) -> dict:
        ev_ms = evaluated_ms if evaluated_ms is not None else _now_ms()
        a = self.authority
        if not a.granted:
            return {"ok": False, "reason": a.reason or R_NO_AUTHORITY}
        if not a.synced:
            return {"ok": False, "reason": R_NOT_SYNCED}
        q = self.quotes.get((event_id, key))
        if q is None:
            ev = self.events.get(event_id)
            if isinstance(ev, dict) and \
                    ev.get("_unparsed_since") is not None:
                return {"ok": False, "reason": R_LAST_RECORD_UNPARSED,
                        "unparsed_since_ms": ev.get("_unparsed_since")}
            return {"ok": False, "reason": R_UNKNOWN_MARKET}
        if q.epoch != a.epoch:
            return {"ok": False, "reason": R_OLD_EPOCH}
        if not q.open:
            return {"ok": False, "reason": R_CLOSED}
        at = q.change_ms
        prov = {"source_change_ms": q.source_change_ms,
                "frame_ts_ms": q.frame_ts_ms, "received_ms": q.received_ms,
                "evaluated_ms": ev_ms, "epoch": q.epoch,
                "parser": PARSER_VERSION,
                # R30A RC4: every clock, apart, and which one dated the
                # change the 30 s rule is measured from
                "freshness_basis": FRESHNESS_BASIS,
                "change_ms": at, "change_clock": q.change_clock,
                "observed_change_ms": q.observed_change_ms,
                "first_observed_ms": q.first_observed_ms,
                "confirmed_ms": q.confirmed_ms,
                "confirmed_received_ms": q.confirmed_received_ms,
                "confirmed_by": q.confirmed_by,
                "confirmed_clock": q.confirmed_clock,
                "age_since_confirmation_s": (
                    None if q.confirmed_ms is None
                    else round((ev_ms - q.confirmed_ms) / 1000.0, 3)),
                "confirmation_is_a_decision_input": False}
        if at is None:
            # no change observed on this epoch (first seen in the subscribe
            # snapshot, or after a reconnect): the age is genuinely unknown
            return {"ok": False, "reason": R_NO_CHANGE_TIME, "quote": q,
                    "provenance": prov}
        age = (ev_ms - at) / 1000.0
        prov["quote_age_s"] = round(age, 3)
        prov["freshness_at_ms"] = at
        self.receipt_to_eval.add(ev_ms - q.received_ms)
        if age < 0:
            return {"ok": False, "reason": R_FUTURE, "quote": q,
                    "provenance": prov}
        if age > max_age_s:
            return {"ok": False, "reason": R_STALE, "quote": q,
                    "provenance": prov}
        return {"ok": True, "quote": q, "provenance": prov}

    def read_held(self, event_id, key, *,
                  evaluated_ms: Optional[float] = None,
                  max_age_s: float = 30.0) -> dict:
        """THE HELD-POSITION READ: `read` (the change rule) first; when it
        refuses only because no change was observed / the change is older
        than the limit, the price is admitted on its latest PROVIDER-STAMPED
        confirmation (PROVIDER_CONFIRMATIONS) within the SAME limit. The
        provenance names the basis and the instant the age is measured from
        (`freshness_at_ms`); the change clocks stay as observed."""
        got = self.read(event_id, key, evaluated_ms=evaluated_ms,
                        max_age_s=max_age_s)
        if got.get("ok") or got.get("reason") not in (R_NO_CHANGE_TIME,
                                                      R_STALE):
            return got
        q = got.get("quote")
        ev_ms = evaluated_ms if evaluated_ms is not None else _now_ms()
        if q is None or q.confirmed_ms is None or \
                q.confirmed_clock != CLOCK_PROVIDER or \
                q.confirmed_by not in PROVIDER_CONFIRMATIONS:
            return got
        age = (ev_ms - q.confirmed_ms) / 1000.0
        if age < 0 or age > max_age_s:
            return got
        self.counts["held_reads_admitted_on_provider_confirmation"] += 1
        prov = dict(got.get("provenance") or {},
                    freshness_basis=FRESHNESS_BASIS_CONFIRMED,
                    freshness_at_ms=q.confirmed_ms,
                    quote_age_s=round(age, 3),
                    change_rule_refusal=got.get("reason"),
                    confirmation_is_a_decision_input=True)
        return {"ok": True, "quote": q, "provenance": prov}

    def market_list(self, event_id) -> dict:
        """WHAT THIS RECORD'S CURRENT MARKET LIST HOLDS, bounded: the
        evidence beside a read that refused R_UNKNOWN_MARKET (red-team
        closeout). Pure; reads only this cache. Every market counted is
        OPEN, of the full game (period 0), on the CURRENT epoch -- so it
        can only have come from a record of this fixture that parsed on
        this connection (a new connection clears the cache). PinnAPI's
        docs (tests/fixtures/pinnapi_ws_subscription_docs_2026_10_04.json):
        a prematch_markets list "is the authoritative current snapshot from
        Pinnacle -- any market ... NOT in this list has been closed by
        Pinnacle"."""
        ev = self.events.get(event_id)
        held = isinstance(ev, dict)
        ev = ev if held else {}
        by_type: collections.Counter = collections.Counter()
        for k in self._keys_of(event_id):
            q = self.quotes.get(k)
            if q is None or not q.open or q.epoch != self.authority.epoch:
                continue
            if (q.period or 0) == 0 and k[1] != FULL_GAME_MONEYLINE_KEY:
                by_type[str(q.market_type)[:24]] += 1
        return {"record_held": held, "epoch": self.authority.epoch,
                "authority_synced": bool(self.authority.synced),
                "record_unparsed": ev.get("_unparsed_since") is not None,
                "authoritative_list": bool(ev.get("_authoritative")),
                "markets_version": ev.get("_markets_version"),
                "stream": ev.get("stream"), "is_live": ev.get("isLive"),
                "full_game_period_closed": 0 in closed_periods(ev),
                "money_line_key": FULL_GAME_MONEYLINE_KEY,
                "money_line_held": (event_id, FULL_GAME_MONEYLINE_KEY)
                in self.quotes,
                # a full-game money line held under ANOTHER key would be a
                # key-mapping question of ours, never Pinnacle's absence
                "full_game_moneyline_under_other_keys":
                    by_type.get("moneyline", 0),
                "other_full_game_open_markets": sum(by_type.values()),
                "other_full_game_open_by_type": dict(by_type)}

    # ── census / health (bounded, for the heartbeat) ────────────────
    def census(self, *, now_ms: Optional[float] = None) -> dict:
        """Bounded heartbeat view. `fresh_now` counts the markets that read
        fresh NOW (CENSUS_FRESH_S) under the unchanged change rule;
        `fresh_now_if_measured_from_confirmation` is the COUNTERFACTUAL
        count under the confirmation clock -- measured for the owner's
        decision, never a decision input."""
        now = _now_ms() if now_ms is None else now_ms
        by = collections.Counter()
        by_sport = collections.Counter()
        unknown_age = unconfirmed = local_dated = 0
        fresh = collections.Counter()
        lim = CENSUS_FRESH_S * 1000.0
        for q in self.quotes.values():
            by["%s|%s|%s" % (q.sport_id, q.market_type, q.stream)] += 1
            by_sport[str(q.sport_id)] += 1
            cm = q.change_ms
            unknown_age += cm is None
            unconfirmed += q.confirmed_ms is None
            local_dated += (q.source_change_ms is None and cm is not None)
            if cm is not None and 0 <= now - cm <= lim:
                fresh["change"] += 1
            if q.confirmed_ms is not None and \
                    0 <= now - q.confirmed_ms <= lim:
                fresh["confirmation"] += 1
        events_by_sport = collections.Counter(
            str(ev.get("sport_id")) for ev in self.events.values())
        kids = children_by_parent(self.events)
        child_classes = collections.Counter()
        for pid, cs in kids.items():
            parent = self.events.get(pid)
            for c in cs:
                child_classes[classify_child(self.events.get(c) or {},
                                             parent)] += 1
        return {"parser": PARSER_VERSION, "authority": self.authority.state(),
                "events": len(self.events), "markets": len(self.quotes),
                "markets_age_unknown": unknown_age,
                # R30A RC4: the three clocks, counted
                "freshness_basis": FRESHNESS_BASIS,
                "markets_change_dated_by_local_observation": local_dated,
                "markets_unconfirmed": unconfirmed,
                "fresh_now": fresh.get("change", 0),
                "fresh_now_if_measured_from_confirmation": {
                    "markets": fresh.get("confirmation", 0),
                    "status": "COUNTERFACTUAL_NOT_A_DECISION_INPUT"},
                "fresh_now_limit_s": CENSUS_FRESH_S,
                "confirmations": dict(self.confirmations),
                # R30A RC1: per-sport receive counters and holdings
                "frames_by_sport_type": dict(self.frames_by_sport_type),
                "markets_by_sport": dict(by_sport),
                "events_by_sport": dict(events_by_sport),
                # R30A RC3: every child record, classified
                "child_records": dict(child_classes),
                "markets_by_sport_type_phase": dict(by),
                "counts": dict(self.counts),
                "provider_stamp_to_receipt_ms":
                    self.provider_to_receipt.summary(),
                "receipt_to_evaluation_ms": self.receipt_to_eval.summary(),
                "bounds": {"max_events": self.max_events,
                           "max_markets": self.max_markets, "ring": RING},
                # (RC6) the off-loop snapshot build: on, building now, frames
                # queued behind it, and how long the swapped-in builds took
                "snapshot_build": {
                    "off_loop": bool(self.offload_snapshots),
                    "min_events": SNAPSHOT_OFFLOOP_MIN_EVENTS,
                    "building": self._pending is not None,
                    "queued_frames": len(self._backlog),
                    "build_ms": self.snapshot_build_ms.summary()}}
