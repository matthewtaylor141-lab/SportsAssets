"""BOUNDED ACTIVE REFRESH: A FRESH BOOK FOR A PRIORITY MEMBER THE STREAM HAS
GONE QUIET ON (RC6, the freshness lane). Read only; no order path.

THE MISS (production, pm-acceptance 37836393458, release 69a8a07e). The
14-category scorecard's "Data freshness and latency" binds on
priority_members_fresh 121/192 (0.630; target 0.95). The dedicated plane's
own snapshot (computed 2026-10-08 ~20:07Z) reads 114 PMX + 1 REST of 186
(0.618), and its census names the 72 members not current: 56
STREAM:SNAPSHOT_OLDER_THAN_THE_BOUND and 2 AWAITING_FIRST_SNAPSHOT -- quiet
PREGAME candidates (atc-fnl-han-pri-2026-10-10-han, atc-irl1-bra-wex-2026-
10-09-bra, aec-nfl-bal-atl-2026-10-11 ...) -- plus 14 the stream updated
between the pass's fresh set and its census. The subscribe-all stream was
healthy (one connection since boot, 5,708,277 filtered updates, 16,317 books
current): it publishes a book when the book CHANGES, so a quiet book's last
full update ages past the plane's 300 s bound and nothing re-proves it; the
paper runtime's own REST read of the same members was 850-21,600 s old.
Held positions were 3/3.

WHY NOT A STREAM RE-REQUEST. The venue's documented way to force a snapshot
is unsubscribe + resubscribe (streaming-apis.md, cited in research/
p5_live_book_currency_review.md); `snapshot_only` is read from a
connection's FIRST request only. This client puts exactly two commands on
the wire, `subscribe` and `keepalive` (institutional_stream.
OUTBOUND_COMMANDS; an unsubscribe is refused, pinned by
tests/test_market_plane_full_mode_boot.py), and a subscribe-all transport
sends ONE subscribe per connection. A re-request would add an outbound
command whose effect on a subscribe-all subscription the venue does not
document, and a reconnect gaps every one of ~48,700 books to re-prove a few
dozen. So the fresh snapshot is the venue's own REST book: the allow-listed
`book` read (GET /v1/orderbook/{symbol}, pmx_institutional.READ_ONLY_PATHS;
read:l2marketdata is granted in production, research/institutional/
PRODUCTION_VERIFICATION_20260919.md), the endpoint the workers'
institutional_md sweep already reads on the same credential (8 reads per
29.5 s sweep, 0 failed, venue p50 74.9 ms at 21:44:59Z, research-sql run
37848924155).

WHO IS REFRESHED, IN WHAT ORDER (`ActiveRefresh.plan`). The priority members
(open paper positions + evaluated candidates: registry priority <=
P_CANDIDATE, the census's members) that the plane's books hold, whose stream
read at the plane bound is refused FOR SNAPSHOT CURRENCY ONLY (REFRESHABLE:
older than the bound, no snapshot on this connection yet, a connection or
clock gap awaiting one, the connection silent or closed, no venue clock) and
whose own last refresh is not current. A stream refusal that is evidence
about the MARKET (not open, hidden, crossed, refused, no scales) is never
refreshed past. Order (RC6 D1, FAIR): HELD before CANDIDATE; inside a tier,
EARLIEST LAPSE FIRST -- the instant the member's last current book (stream
receipt or refresh receipt, + the bound) stopped counting, never-current
first; then by event start (in play first, then the soonest pregame start,
then started > 4 h ago, then no start). RC6 ordered candidates by event
start before age: when more members are quiet than the budget can hold
(12 reads x 285 s / 60 = 57 at once) the soonest starts were re-read every
285 s and the later ones were never read at all; earliest-lapse-first
spreads the same reads over every quiet member (with enough budget the two
orders keep the same members current). A member whose read is IN FLIGHT is
never planned twice.

WHO DRIVES IT (RC6 D1, reliability). Production's plane pass is not 2 s: its
SNAPSHOT events -- one per pass at most -- arrived p50 92 s, p90 248 s, p99
739 s apart over 24 h, and every one of the newest 40 more than 197 s apart
(research-sql run 37870039455, 2026-10-09 01:29Z: coverage and
certification run every pass). One read per pass made RC6's 12-a-minute
budget about 0.25 a minute in production. So `step` is also driven by the
plane's freshness task (workers.universal_market_plane.freshness_loop) every
second, beside the pass, and the plane's PMX client is shared through one
lock (`lock`): reads never overlap, the budget is the same object, and a
slow coverage or certification pass no longer stops the reads.

WHAT COUNTS AS CURRENT (`judge`, then `current`). A 200 whose body is an
object with bids / offers lists, echoes the member's symbol, carries the
venue's transactTime, states INSTRUMENT_STATE_OPEN and is not crossed --
the stream's own checks, applied to the REST book. Anything else is a named
outcome and not current. A current refresh stands for exactly the plane's
bound measured from ITS RECEIPT INSTANT (the stream's rule: receipt age), is
never extended, and stops counting the moment the stream contradicts it with
newer evidence about the market (a refusal outside REFRESHABLE). It is
REST_RECOVERY in the coverage pass, labelled PLANE_ACTIVE_REFRESH beside the
paper runtime's PAPER_BOOK_OBSERVATION; it is never put into the stream's
books, never a PRIORITY_PMX_BOOKS parity book and never a decision input.

THE BUDGET, NEVER EXCEEDED. At most `per_min` read STARTS in any WINDOW_S
(a sliding window: a slow plane pass -- coverage, certification -- is caught
up on the next ones without ever passing the figure), at least MIN_GAP_S
apart, MAX_READS_PER_PASS per pass. per_min =
UMP_BOOK_REFRESH_PER_MIN, capped at BOOK_READS_PER_MIN_MAX = 12: the venue's
GetOrderBook figure (research/institutional/PRODUCTION_ONBOARDING_REQUEST.md:
"GetBBO and GetOrderBook 12/min"; pmx.BUCKETS["book"] is the same 12). The
refdata budget (5 of the venue's 6 ListInstruments a minute) is neither
touched nor shared. A 429 holds every read for BACKOFF_429_S (the client
returns no Retry-After) and is counted; a failed read is retried after
RETRY_FAILED_S, a market the REST book shows not open after
RETRY_NOT_OPEN_S (its budget goes to members that can be current).

THE BOUND IS NOT CHANGED. The plane's FRESH_SLA_S (300 s, the held-mark SLA)
is passed in by the caller and is the only age used; REFRESH_LEAD_S only
decides when a refresh still inside it is re-read, never how long one counts.

MEMORY. One small record per priority member (no book levels are kept), the
member list re-read every MEMBERS_EVERY_S, records of members that left the
list dropped at that read; MAX_TRACKED bounds both. The plane's working set
does not grow with the universe.
"""
from __future__ import annotations

import collections
import logging
import os
import time
from datetime import datetime

from .. import institutional_stream as IS

log = logging.getLogger(__name__)

VERSION = "PRIORITY_ACTIVE_REFRESH_V1"
ENV_FLAG = "UMP_ACTIVE_REFRESH"
PER_MIN_ENV = "UMP_BOOK_REFRESH_PER_MIN"
#: the venue's GetOrderBook figure (12/min); this refresh never asks for more
BOOK_READS_PER_MIN_MAX = 12
BOOK_READS_PER_MIN_DEFAULT = 12
WINDOW_S = 60.0
#: never more than one book read a second, whatever the window allows
MIN_GAP_S = 1.0
#: reads are made inline (the refdata slot's rule: no second thread on the
#: plane's one PMX client), so a slow read delays the pass; one per pass
MAX_READS_PER_PASS = 1
BACKOFF_429_S = 60.0
#: a current refresh is re-read once it is this close to the bound (one slot
#: plus a read's latency), so a quiet member does not lapse between reads
REFRESH_LEAD_S = 15.0
RETRY_FAILED_S = 60.0
RETRY_NOT_OPEN_S = 900.0
MEMBERS_EVERY_S = 30.0
MAX_TRACKED = 2000
#: the registry's started-long-ago boundary, as the census phases it
STARTED_LONG_AGO_S = 4 * 3600.0

#: the stream refusals a REST book may stand in for: SNAPSHOT CURRENCY only
REFRESHABLE = frozenset({
    IS.R_SNAPSHOT_OLD, IS.R_SNAPSHOT_PENDING, IS.R_GAP_CONNECTION,
    IS.R_GAP_CLOCK, IS.R_SILENT, IS.R_NO_CONNECTION, IS.R_NO_VENUE_TS})

ORIGIN_PAPER = "PAPER_BOOK_OBSERVATION"
ORIGIN_PLANE = "PLANE_ACTIVE_REFRESH"

#: the outcome of a read that made the member current
CURRENT = "CURRENT"
#: why a read did not make the member current (each counted by name)
R_REFRESH_HTTP_429 = "ACTIVE_REFRESH_BOOK_READ_ANSWERED_429"
R_REFRESH_NOT_200 = "ACTIVE_REFRESH_BOOK_READ_NOT_200"
R_REFRESH_TRANSPORT = "ACTIVE_REFRESH_BOOK_READ_TRANSPORT_FAILED"
R_REFRESH_NO_TOKEN = "ACTIVE_REFRESH_NO_BEARER_TOKEN"
R_REFRESH_NOT_A_BOOK = "ACTIVE_REFRESH_RESPONSE_IS_NOT_A_BOOK"
R_REFRESH_SYMBOL = "ACTIVE_REFRESH_BOOK_SYMBOL_IS_NOT_THE_MEMBER"
R_REFRESH_NO_VENUE_TS = "ACTIVE_REFRESH_BOOK_CARRIES_NO_VENUE_TIMESTAMP"
R_REFRESH_STATE_UNKNOWN = "ACTIVE_REFRESH_BOOK_STATE_UNKNOWN"
R_REFRESH_NOT_OPEN = "ACTIVE_REFRESH_MARKET_NOT_OPEN"
R_REFRESH_CROSSED = "ACTIVE_REFRESH_BOOK_CROSSED"
#: why a member that needed a fresh book did not get a read this pass
R_REFRESH_BUDGET = "ACTIVE_REFRESH_DEFERRED_BOOK_READ_BUDGET_SPENT"
R_REFRESH_HOLD_429 = "ACTIVE_REFRESH_HELD_AFTER_A_VENUE_429"
R_REFRESH_RETRY_WAIT = "ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ"
R_REFRESH_OFF = "ACTIVE_REFRESH_OFF_BY_SWITCH"
R_REFRESH_NO_STREAM = "ACTIVE_REFRESH_NO_PMX_STREAM_ARMED"

#: an outcome after which the member waits RETRY_NOT_OPEN_S, not
#: RETRY_FAILED_S: the venue said what the market is
_MARKET_OUTCOMES = frozenset({R_REFRESH_NOT_OPEN, R_REFRESH_STATE_UNKNOWN,
                              R_REFRESH_CROSSED})

TIER_HELD, TIER_CANDIDATE = "HELD", "CANDIDATE"
PHASE_RANK = {"IN_PLAY_OR_RECENT": 0, "PREGAME": 1, "STARTED_GT_4H": 2,
              "NO_START": 3}


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def per_min(env=None) -> int:
    """The refresh's book reads a minute: UMP_BOOK_REFRESH_PER_MIN, never
    above BOOK_READS_PER_MIN_MAX, never below 1."""
    env = os.environ if env is None else env
    try:
        v = int(str(env.get(PER_MIN_ENV, BOOK_READS_PER_MIN_DEFAULT)))
    except (TypeError, ValueError):
        v = BOOK_READS_PER_MIN_DEFAULT
    return max(1, min(BOOK_READS_PER_MIN_MAX, v))


def _epoch(v):
    """A registry timestamp / the venue's ISO-8601 clock -> epoch seconds,
    or None. A string is read by the same-book evidence's parser (UTC when
    unzoned, sub-microsecond digits dropped), the one the PMX REST book's
    transactTime is already read with."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return float(v.timestamp()) if v.tzinfo else None
    if isinstance(v, (int, float)):
        return float(v)
    from .. import institutional_same_book as SB
    return SB._epoch_of(v)


def phase_of(start, *, now: float) -> str:
    """The census's phase of a member's event (same boundaries)."""
    st = _epoch(start)
    if st is None:
        return "NO_START"
    if now - st > STARTED_LONG_AGO_S:
        return "STARTED_GT_4H"
    if st <= now:
        return "IN_PLAY_OR_RECENT"
    return "PREGAME"


def tier_of(priority) -> str:
    from .populate import P_HELD
    try:
        return TIER_HELD if int(priority) <= P_HELD else TIER_CANDIDATE
    except (TypeError, ValueError):
        return TIER_CANDIDATE


def _px(level):
    try:
        return int(str((level or {}).get("px")).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def judge(symbol: str, row: dict) -> dict:
    """PURE. One `book` read result -> {outcome, venue_ts, levels}. CURRENT
    only for a 200 book of THIS symbol, with the venue's clock, OPEN, not
    crossed (best-first sides, observed: shadow_l2); otherwise the named
    reason it is not."""
    from .. import shadow_l2 as l2
    r = row if isinstance(row, dict) else {}
    st = r.get("status")
    out = {"outcome": None, "venue_ts": None, "levels": None,
           "status": st, "ms": r.get("ms")}
    if st == 429:
        return dict(out, outcome=R_REFRESH_HTTP_429)
    if st is None:
        if r.get("transportError"):
            return dict(out, outcome=R_REFRESH_TRANSPORT,
                        detail=str(r.get("transportError"))[:60])
        return dict(out, outcome=R_REFRESH_NO_TOKEN)
    if st != 200:
        return dict(out, outcome=R_REFRESH_NOT_200)
    body = r.get("body")
    # an empty side may be absent (the venue's JSON omits an empty repeated
    # field), never another type
    if not isinstance(body, dict) or not isinstance(
            body.get(l2.SIDE_BIDS) or [], list) or not isinstance(
            body.get(l2.SIDE_OFFERS) or [], list):
        return dict(out, outcome=R_REFRESH_NOT_A_BOOK)
    if str(body.get("symbol") or "") != str(symbol):
        return dict(out, outcome=R_REFRESH_SYMBOL)
    bids = body.get(l2.SIDE_BIDS) or []
    offers = body.get(l2.SIDE_OFFERS) or []
    out["levels"] = [len(bids), len(offers)]
    vts = _epoch(body.get(l2.F_TRANSACT_TIME))
    if vts is None:
        return dict(out, outcome=R_REFRESH_NO_VENUE_TS)
    out["venue_ts"] = vts
    state = body.get("state")
    if not state:
        return dict(out, outcome=R_REFRESH_STATE_UNKNOWN)
    if str(state) not in IS.OPEN_STATES:
        return dict(out, outcome=R_REFRESH_NOT_OPEN, state=str(state)[:40])
    bb, bo = (_px(bids[0]) if bids else None), (_px(offers[0]) if offers
                                                else None)
    if bb is not None and bo is not None and bb >= bo:
        return dict(out, outcome=R_REFRESH_CROSSED)
    return dict(out, outcome=CURRENT)


class ActiveRefresh:
    """The refresh's state: the priority members, one small record each, the
    read starts inside the budget window, the 429 hold and the totals."""

    def __init__(self, *, per_minute: int = BOOK_READS_PER_MIN_DEFAULT,
                 clock=time.time):
        self.per_min = max(1, min(BOOK_READS_PER_MIN_MAX, int(per_minute)))
        self._clock = clock
        self.members: list = []          # [(symbol, tier, phase, start)]
        self.members_at = None
        self.entries: dict = {}
        self._starts: collections.deque = collections.deque(
            maxlen=self.per_min)
        #: (RC6 D1) symbols whose read has started and is not yet recorded:
        #: the two drivers (the pass and the freshness task) never read one
        #: member twice at once
        self.inflight: set = set()
        self.hold_until = 0.0
        self.last_pass: dict = {}
        self.totals = {"reads": 0, "current": 0, "not_current": 0,
                       "by_outcome": {}}
        #: (RC6 D1) the origin of each member's newest CURRENT read: REST
        #: (this module's `book` read) or SNAPSHOT (snapshot_refresh's
        #: snapshot-only gRPC call); kept apart from the entries' shape
        self.origins: dict = {}

    # -- the members -------------------------------------------------------

    def set_members(self, rows, *, now: float) -> None:
        """Registry rows {contract_id, priority, event_start} -> the member
        list (bounded); a record of a member that left is dropped."""
        out = []
        for r in rows or ():
            s = str(r["contract_id"])
            out.append((s, tier_of(r["priority"]),
                        phase_of(r["event_start"], now=now),
                        _epoch(r["event_start"])))
            if len(out) >= MAX_TRACKED:
                break
        self.members = out
        self.members_at = now
        keep = {m[0] for m in out}
        for s in [s for s in self.entries if s not in keep]:
            del self.entries[s]
        for s in [s for s in self.origins if s not in keep]:
            del self.origins[s]

    def members_due(self, now: float) -> bool:
        return self.members_at is None or \
            now - self.members_at >= MEMBERS_EVERY_S

    # -- the budget --------------------------------------------------------

    def slot(self, now: float):
        """(True, None) when one read may start now; else (False, why)."""
        if now < self.hold_until:
            return False, R_REFRESH_HOLD_429
        st = self._starts
        while st and now - st[0] >= WINDOW_S:
            st.popleft()
        if len(st) >= self.per_min:
            return False, R_REFRESH_BUDGET
        if st and now - st[-1] < MIN_GAP_S:
            return False, R_REFRESH_BUDGET
        return True, None

    def reads_in_window(self, now: float) -> int:
        return sum(1 for t in self._starts if now - t < WINDOW_S)

    # -- the plan ----------------------------------------------------------

    def _stream(self, mgr, s, *, now: float, bound: float):
        """(ok, refusal, received_at) of the stream's read at the plane
        bound -- received_at the receipt instant of the newest full update
        the books hold for it, or None -- or None when the plane's books do
        not hold the symbol."""
        if s not in (getattr(mgr, "symbol_to_shard", None) or {}):
            return None
        try:
            r = mgr.current(s, now=now, max_snapshot_age_s=bound) or {}
        except Exception as exc:                                # noqa: BLE001
            return False, "READ_RAISED:%s" % type(exc).__name__, None
        rcv = (((r.get("evidence") or {}).get("snapshot") or {})
               .get("received_at"))
        try:
            rcv = None if rcv is None else float(rcv)
        except (TypeError, ValueError):
            rcv = None
        return bool(r.get("ok")), r.get("refusal"), rcv

    def lapse_at(self, s, *, stream_received_at, bound: float):
        """The instant the member's newest current book stops counting: its
        stream receipt or its refresh receipt (the later), + the bound;
        None when it never had one (it lapsed first of all)."""
        at = [x for x in (stream_received_at,
                          (self.entries.get(s) or {}).get("ok_at"))
              if x is not None]
        return (max(at) + bound) if at else None

    def _refresh_current(self, e, *, now: float, bound: float) -> bool:
        at = (e or {}).get("ok_at")
        return at is not None and 0.0 <= now - at <= bound

    def current(self, mgr, *, now: float, bound: float) -> dict:
        """{symbol: receipt instant} of the members CURRENT VIA THE REFRESH
        at `now`: a CURRENT read received within `bound`, the stream not
        current, and the stream refusing for snapshot currency only (newer
        stream evidence about the market wins). Read-only."""
        out = {}
        if mgr is None:
            return out
        for s, _t, _p, _st in self.members:
            e = self.entries.get(s)
            if not self._refresh_current(e, now=now, bound=bound):
                continue
            got = self._stream(mgr, s, now=now, bound=bound)
            if got is None or got[0] or got[1] not in REFRESHABLE:
                continue
            out[s] = e["ok_at"]
        return out

    def current_for_coverage(self, mgr, *, now: float,
                             bound: float) -> dict:
        """`current` in the coverage pass's shape: a REST read's receipt
        instant, or (receipt, "SNAPSHOT") for a snapshot-only gRPC read
        (populate.coverage_pass counts that one PMX_GRPC, labelled)."""
        return {s: ((at, "SNAPSHOT") if self.origins.get(s) == "SNAPSHOT"
                    else at)
                for s, at in self.current(mgr, now=now,
                                          bound=bound).items()}

    def plan(self, mgr, *, now: float, bound: float,
             lead_s: float | None = None) -> tuple:
        """([symbol, ...] due now in refresh order, {reason: count}) over the
        members. Due: held by the plane's books, the stream refusing for
        snapshot currency, no current refresh that is not yet within
        `lead_s` (REFRESH_LEAD_S) of the bound, not waiting to retry, no
        read of it in flight. Order: HELD first, then earliest lapse, then
        event start (module docstring)."""
        counts: dict = {}
        due = []
        lead = REFRESH_LEAD_S if lead_s is None else float(lead_s)

        def n(k):
            counts[k] = counts.get(k, 0) + 1
        for s, tier, phase, start in self.members:
            got = self._stream(mgr, s, now=now, bound=bound)
            if got is None:
                n("NOT_HELD_BY_THE_PLANE_BOOKS")
                continue
            ok, refusal, stream_rcv = got
            if ok:
                n("STREAM_CURRENT")
                continue
            if refusal not in REFRESHABLE:
                n("STREAM_REFUSAL_NOT_REFRESHABLE")
                continue
            if s in self.inflight:
                n("READ_IN_FLIGHT")
                continue
            e = self.entries.get(s) or {}
            if self._refresh_current(e, now=now, bound=bound - lead):
                n("REFRESH_CURRENT")
                continue
            tried = e.get("tried_at")
            wait = (RETRY_NOT_OPEN_S if e.get("outcome") in _MARKET_OUTCOMES
                    else RETRY_FAILED_S)
            if e.get("outcome") not in (None, CURRENT) and tried is not None \
                    and now - tried < wait:
                n(R_REFRESH_RETRY_WAIT)
                continue
            if self._refresh_current(e, now=now, bound=bound):
                n("REFRESH_CURRENT_DUE_FOR_RE_READ")
            lapse = self.lapse_at(s, stream_received_at=stream_rcv,
                                  bound=bound)
            due.append(((0 if tier == TIER_HELD else 1),
                        float("-inf") if lapse is None else lapse,
                        PHASE_RANK.get(phase, 3),
                        start if start is not None else float("inf"),
                        s))
        due.sort()
        return [d[-1] for d in due], counts

    # -- one read ----------------------------------------------------------

    def start(self, now: float, symbol: str | None = None) -> None:
        self._starts.append(now)
        if symbol is not None:
            self.inflight.add(str(symbol))

    def record(self, symbol: str, row: dict, *, at: float) -> dict:
        """Account one finished read (received at `at`)."""
        self.inflight.discard(str(symbol))
        j = judge(symbol, row)
        t = self.totals
        t["reads"] += 1
        o = j["outcome"]
        t["by_outcome"][o] = t["by_outcome"].get(o, 0) + 1
        e = self.entries.setdefault(symbol, {"ok_at": None, "tries": 0})
        e.update(tried_at=at, outcome=o, tries=e["tries"] + 1,
                 status=j.get("status"))
        if o == CURRENT:
            t["current"] += 1
            e.update(ok_at=at, venue_ts=j["venue_ts"], levels=j["levels"])
            self.origins[str(symbol)] = "REST"
        else:
            t["not_current"] += 1
            if o in _MARKET_OUTCOMES:
                # the venue's newer word about the market (not open, no
                # state, crossed) ends an earlier current read at once; a
                # read that failed says nothing about the book and does not
                e["ok_at"] = None
        if o == R_REFRESH_HTTP_429:
            self.hold_until = max(self.hold_until, at + BACKOFF_429_S)
        return j

    def record_snapshot(self, symbol: str, j: dict, *, at: float) -> None:
        """(RC6 D1) Account one snapshot-only gRPC result (judged by
        snapshot_refresh.judge_update, received at `at`) exactly like a REST
        book: CURRENT stands for the bound from its receipt (origin
        SNAPSHOT); the venue's word about the market (not open, no state,
        crossed) ends an earlier current read and waits RETRY_NOT_OPEN_S. A
        symbol the call did not return is NOT recorded here, so the REST
        read stays free to try it. The REST totals are not touched."""
        o = j.get("outcome")
        e = self.entries.setdefault(symbol, {"ok_at": None, "tries": 0})
        e.update(tried_at=at, outcome=o, tries=e["tries"] + 1,
                 status=j.get("status"))
        if o == CURRENT:
            e.update(ok_at=at, venue_ts=j.get("venue_ts"),
                     levels=j.get("levels"))
            self.origins[str(symbol)] = "SNAPSHOT"
        elif o in _MARKET_OUTCOMES:
            e["ok_at"] = None

    def origin_of(self, symbol: str) -> str | None:
        return self.origins.get(str(symbol))

    def outcome_of(self, symbol: str) -> str | None:
        return (self.entries.get(symbol) or {}).get("outcome")

    # -- the evidence ------------------------------------------------------

    def digest(self, *, now: float, bound: float, mgr=None) -> dict:
        cur = self.current(mgr, now=now, bound=bound) if mgr is not None \
            else {}
        by_tier: dict = {}
        by_origin: dict = {}
        tiers = {m[0]: m[1] for m in self.members}
        for s in cur:
            by_tier[tiers.get(s, TIER_CANDIDATE)] = by_tier.get(
                tiers.get(s, TIER_CANDIDATE), 0) + 1
            o = self.origins.get(s) or "REST"
            by_origin[o] = by_origin.get(o, 0) + 1
        return {"version": VERSION, "enabled": True,
                "endpoint": "GET /v1/orderbook/{symbol} (pmx_institutional "
                            "`book`, allow-listed)",
                "bound_s": bound, "refresh_lead_s": REFRESH_LEAD_S,
                "budget": {"per_min": self.per_min,
                           "per_min_max": BOOK_READS_PER_MIN_MAX,
                           "window_s": WINDOW_S, "min_gap_s": MIN_GAP_S,
                           "max_reads_per_pass": MAX_READS_PER_PASS,
                           "reads_in_window": self.reads_in_window(now),
                           "hold_429_s": round(max(0.0, self.hold_until
                                                   - now), 1),
                           "basis": "venue GetOrderBook 12/min (onboarding "
                                    "request Q10); refdata budget not "
                                    "shared"},
                "members": len(self.members),
                "current_via_refresh": len(cur),
                "current_via_refresh_by_tier": by_tier,
                "current_via_refresh_by_origin": by_origin,
                "last_pass": dict(self.last_pass),
                "totals": {k: (dict(v) if isinstance(v, dict) else v)
                           for k, v in self.totals.items()},
                "authority": "FRESHNESS_EVIDENCE_ONLY_NO_DECISION_INPUT"}


def off_digest(why: str = R_REFRESH_OFF) -> dict:
    """The digest of a refresh that is not running here, with why (off by
    UMP_ACTIVE_REFRESH, or no stream armed to refresh beside)."""
    return {"version": VERSION, "enabled": False, "why": why,
            "current_via_refresh": 0}


MEMBERS_SQL = (
    "SELECT contract_id, priority, event_start FROM market_plane_registry "
    " WHERE active AND priority <= $1 "
    " ORDER BY priority, event_start NULLS LAST, contract_id LIMIT $2")


class _NoLock:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


async def step(pool, client, ref: ActiveRefresh, mgr, *, bound: float,
               clock=time.time, read=None, lock=None) -> dict:
    """ONE PASS OF THE REFRESH (the plane's run loop after the refdata slot,
    and the plane's freshness task every second): re-read the member list
    when due, plan, and spend at most MAX_READS_PER_PASS reads the budget
    allows on the members due, in order. Each read is the allow-listed
    `book` read in a worker thread, made holding `lock` (the plane's one PMX
    client, shared with the refdata slot and the other driver): the budget
    is checked and the member re-checked UNDER the lock, so two drivers
    never pass one slot or read one member twice. Returns this pass's
    digest. Raises nothing it can name."""
    import asyncio
    from .populate import P_CANDIDATE
    now = clock()
    members_error = None
    if ref.members_due(now):
        try:
            async with pool.acquire() as c:
                rows = await c.fetch(MEMBERS_SQL, P_CANDIDATE, MAX_TRACKED)
            ref.set_members(rows, now=now)
            del rows
        except Exception as exc:                                # noqa: BLE001
            # the last member list stands; re-read on the next pass
            members_error = "MEMBERS_READ_FAILED:%s" % type(exc).__name__
    due, counts = ref.plan(mgr, now=now, bound=bound)
    reads, why_stopped = [], None
    read = read or (lambda s: client.read("book", s))
    guard = lock if lock is not None else _NoLock()
    for s in due:
        if len(reads) >= MAX_READS_PER_PASS:
            why_stopped = "MAX_READS_PER_PASS"
            break
        async with guard:
            ok, why = ref.slot(clock())
            if not ok:
                why_stopped = why
                break
            if s in ref.inflight or ref._refresh_current(
                    ref.entries.get(s), now=clock(),
                    bound=bound - REFRESH_LEAD_S):
                # the other driver read it while this one waited
                continue
            ref.start(clock(), s)
            try:
                row = await asyncio.to_thread(read, s)
            except asyncio.CancelledError:
                # the driver is stopping: nothing was recorded, nothing
                # stays in flight
                ref.inflight.discard(s)
                raise
            except Exception as exc:                            # noqa: BLE001
                row = {"status": None, "transportError": "RAISED:%s"
                       % type(exc).__name__}
            j = ref.record(s, row, at=clock())
        reads.append({"symbol": s, "outcome": j["outcome"],
                      "status": j.get("status"), "ms": j.get("ms")})
        if j["outcome"] == R_REFRESH_HTTP_429:
            # one line per hold (no read is made inside it): the venue's
            # answer to this budget is readable in the service log
            log.warning("market plane active refresh: book read answered "
                        "429; book reads held %.0f s (reads in window %d)",
                        BACKOFF_429_S, ref.reads_in_window(clock()))
            why_stopped = R_REFRESH_HOLD_429
            break
    deferred = max(0, len(due) - len(reads))
    if deferred and why_stopped in (None, "MAX_READS_PER_PASS"):
        why_stopped = why_stopped or R_REFRESH_BUDGET
    ref.last_pass = {"at": now, "due": len(due), "read": len(reads),
                     "deferred": deferred,
                     "deferred_why": why_stopped if deferred else None,
                     "by_reason": counts, "reads": reads[:MAX_READS_PER_PASS]}
    if members_error:
        ref.last_pass["members_error"] = members_error
    return dict(ref.last_pass)
