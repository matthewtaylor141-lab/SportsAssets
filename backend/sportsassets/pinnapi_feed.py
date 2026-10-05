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

import collections
import math
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

    # ── lifecycle ────────────────────────────────────────────────────
    def new_connection(self, subscriptions) -> int:
        """A new socket epoch. Everything from earlier epochs is dropped: a
        reconnect never leaves an old price usable."""
        self.events.clear()
        self.quotes.clear()
        self.counts["epochs"] += 1
        return self.authority.grant(subscriptions)

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
        self._touched.clear()
        result = self._apply(msg, epoch=epoch, received_ms=received_ms)
        # Notify after the entire frame (including closed periods and bounds)
        # has applied. Consumers re-read authority; no callback can trade here.
        if self.on_change is not None:
            for key in self._touched:
                quote = self.quotes.get(key)
                if quote is not None and quote.change_ms is not None:
                    try:
                        quote.fixture_id = self.canonical_id(quote.event_id)
                        self.on_change(quote)
                    except Exception:
                        self.counts["change_notification_errors"] += 1
        return result

    def _apply(self, msg: dict, *, epoch: int,
               received_ms: Optional[float] = None) -> str:
        """Apply one envelope from connection `epoch`. Returns what it was."""
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
        self.receipt_to_eval.add(ev_ms - q.received_ms)
        if age < 0:
            return {"ok": False, "reason": R_FUTURE, "quote": q,
                    "provenance": prov}
        if age > max_age_s:
            return {"ok": False, "reason": R_STALE, "quote": q,
                    "provenance": prov}
        return {"ok": True, "quote": q, "provenance": prov}

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
                           "max_markets": self.max_markets, "ring": RING}}
