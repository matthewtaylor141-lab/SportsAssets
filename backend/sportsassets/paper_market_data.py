"""THE ONE PAPER MARKET-DATA OWNER PER VENUE PER PROCESS (read-only).

THE DEFECT (production 2026-10-05/06). PAPER mark freshness was 0.56 against a
0.95 target. The authenticated REST book endpoint answers 429 with a
Retry-After of 7-10 s, and 429s were seen at only ~0.23 req/s -- below what
~100 held markets need inside the 300 s mark SLA. Every paper reader (the
pass's step_books / read_books / entry_fill / held_review, Derek's
read_book_within_deadline, the benchmark's book_for, the work queue, the
held-mark refresh) went to the venue on its own: N independent pollers on
one key, a held mark competing equally with a discovery read for the same
scarce requests.

THE OWNER. Every paper REST book read in the process reaches the venue
through `read()` here -- `bettor_paper_guard._default_transport`, the one
transport every `PaperMarketDataClient` uses, delegates to it -- and nothing
else. It adds no venue, key or request path; it orders and shares the reads
that already existed:

  1. CACHE. A successful read this process made within CACHE_MAX_AGE_S
     (`ext_pinnacle_loop.recent_book`, the existing _RECENT_BOOKS) answers
     with its ORIGINAL receipt instant (`shared_read`). Nothing is made
     fresher than it is; a reader that needs a book received at or after an
     instant (`not_before_epoch`) is never answered by an older one.
  2. COALESCING. Concurrent reads of ONE slug share ONE in-flight request:
     the first is the leader, the rest wait for its answer (bounded by their
     own deadlines) and receive a copy marked `coalesced` + `shared_read`.
     A leader that failed is not shared -- a follower then reads for itself,
     through the same gate (which then holds it for the venue's window).
  3. PRIORITY LANES. Dispatch slots (MAX_IN_FLIGHT) are granted in lane
     order: HELD (an open paper position's market -- registered by the
     held-mark refresh, or named by the caller) before MANAGE (pending
     entries / resting orders) before DISCOVERY (entry candidates,
     observation quotes, pair observations, probes); FIFO inside a lane. A
     queued read whose deadline passes is refused BY NAME, never sent late.
  4. THE VENUE'S HOLD, SHARED. venue_request_gate (the process-wide
     not-before instant armed from every 429's Retry-After) and venue_pace
     (the process-wide gap) still sit under every request. On top of that a
     DISCOVERY read is not dispatched at all while the hold is in force
     (refused by name: research yields first), so the window the venue gave
     us is spent on held marks.

AND THE TELEMETRY (`telemetry()`), on GET /api/command/paper/freshness:
requests / min, 2xx, 429, REST dispatches, cache hits, coalesced reads,
queue depth per lane, refusals, the hold, the retail and institutional
stream update counts.

PAPER ONLY, READ ONLY. One function reaches the network: the transport the
caller hands in (by default `ext_pinnacle_loop._read_book_blocking`, a GET).
No order, cancel, key or threshold is touched; the 300 s mark SLA and every
gate are unchanged.
"""
from __future__ import annotations

import contextlib
import contextvars
import heapq
import itertools
import os
import re
import threading
import time
from collections import deque

VERSION = "PAPER_MARKET_DATA_OWNER_V1"
VENUE = "POLYMARKET_US_RETAIL"

LANE_HELD = "HELD"
LANE_MANAGE = "MANAGE"
LANE_DISCOVERY = "DISCOVERY"
LANES = (LANE_HELD, LANE_MANAGE, LANE_DISCOVERY)
_RANK = {LANE_HELD: 0, LANE_MANAGE: 1, LANE_DISCOVERY: 2}

#: = bettor_paper_guard.SHARED_BOOK_MAX_AGE_S (a test pins them equal).
CACHE_MAX_AGE_S = 6.0
#: REST book reads in flight at once from the paper path. venue_pace spaces
#: their STARTS process-wide; this bounds how many paper readers hold a
#: request open, so the queue (not the venue) decides who goes next.
MAX_IN_FLIGHT = 2
#: The longest a read with NO deadline waits in the queue (the request
#: gate's own undeadlined cap is 20 s).
MAX_UNDEADLINED_QUEUE_S = 20.0
#: How long a held slug stays registered after the refresh last named it.
HELD_TTL_S = 900.0

R_QUEUE_DEADLINE = "PAPER_MARKET_DATA_QUEUE_WAIT_EXCEEDED_THE_DEADLINE"
R_DISCOVERY_DEFERRED = "PAPER_DISCOVERY_READ_DEFERRED_DURING_VENUE_HOLD"
R_COALESCE_DEADLINE = "PAPER_COALESCED_READ_DEADLINE_EXCEEDED"
#: VENUE ISOLATION. This owner, its cache, its coalescing, its lanes, its
#: pace / Retry-After state and its held registry are POLYMARKET US only
#: (retail slugs are lower-case, e.g. `aec-mlb-nyy-bos-2026-10-06`). A
#: Kalshi market ticker (upper-case, e.g. `KXMLBGAME-26OCT06NYYBOS-NYY`) is
#: another venue: refused by name before any cache, queue, pace or request,
#: and never registered as held. Kalshi has its own client, transport and
#: (absent) book path (kalshi_venue; position_rooms R_KALSHI_BOOK).
R_FOREIGN_VENUE = "NOT_A_POLYMARKET_US_MARKET_KALSHI_TICKER_REFUSED"
_KALSHI_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9_.\-]{1,200}$")


def is_foreign_ticker(slug) -> bool:
    """True for an upper-case exchange ticker (Kalshi's shape): it has an
    upper-case letter and no lower-case one. A Polymarket US slug never
    does."""
    s = str(slug or "").strip()
    return bool(s) and bool(_KALSHI_TICKER.match(s)) and \
        any(c.isalpha() for c in s)

_lane_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "paper_market_data_lane", default=None)


@contextlib.contextmanager
def lane(name: str):
    """Every paper book read made inside this block -- by this task and by
    every worker thread asyncio.to_thread starts for it (the context is
    copied) -- is in lane `name` unless the slug is held (HELD wins)."""
    tok = _lane_ctx.set(name if name in _RANK else LANE_DISCOVERY)
    try:
        yield
    finally:
        _lane_ctx.reset(tok)


# ═════════════════════════════════════════════════════════════════════
# THE KEYLESS PUBLIC-GATEWAY LANE (held marks only)
# ═════════════════════════════════════════════════════════════════════
#
# WHY. In production the retail stream cannot run (no dedicated market-data
# key, and credentials are not ours to add), so REST is the bottleneck: the
# AUTHENTICATED book endpoint is rate-limited per key (429, Retry-After
# 7-10 s, at ~0.23 req/s). The PUBLIC gateway serves the same documented
# read, GET /v1/markets/{slug}/book, with NO key (institutional_same_book's
# keyless client already reads it for the probe), and may carry a separate
# budget. So held marks get a second REST lane through it.
#
# ITS OWN STATE, KEYED BY AUTH CLASS. The public lane never consults the
# authenticated key's not-before instant or pace (venue_request_gate /
# venue_pace), and a 429 here never arms them: a 429 on one budget is not
# evidence about the other. Each honours ITS OWN Retry-After. The lane
# starts conservatively (PUBLIC_MIN_GAP_S between requests); every 429 sets
# a hard hold of max(Retry-After, PUBLIC_HOLD_FLOOR_S) and doubles the gap
# (x2, x4, ... up to PUBLIC_MAX_MULT) for PUBLIC_PENALTY_S, like
# venue_pace's penalty.
#
# NOTHING BUT THAT GET. The keyless client's every transport (default and
# proxy mounts) is wrapped by institutional_same_book.install_read_only
# (GET-only outermost) around PublicGatewayTransport, which refuses any
# request that is not exactly GET /v1/markets/<slug>/book and passes the
# process write lock. No key is ever attached.
#
# HELD ONLY. The owner refuses a public-lane read of a market that is not a
# registered held market; discovery / entry readers never reach it (their
# PaperMarketDataClient path cannot name it), and a public-gateway book is
# not put in the shared read cache nor coalesced with a keyed read.

AUTH_KEYED = "AUTHENTICATED"
AUTH_PUBLIC = "PUBLIC_GATEWAY"
AUTH_LANES = (AUTH_KEYED, AUTH_PUBLIC)
PUBLIC_BASIS = "HELD_MARK_PUBLIC_GATEWAY"
PUBLIC_SOURCE = "PAPER_PUBLIC_GATEWAY"
PUBLIC_MIN_GAP_S = 1.0
PUBLIC_HOLD_FLOOR_S = 5.0
PUBLIC_PENALTY_S = 300.0
PUBLIC_MAX_MULT = 8.0
#: off switch for the lane alone (PAPER_PUBLIC_GATEWAY_LANE=off)
PUBLIC_ENV_FLAG = "PAPER_PUBLIC_GATEWAY_LANE"
R_PUBLIC_HELD_ONLY = "PUBLIC_GATEWAY_LANE_SERVES_HELD_MARKS_ONLY"
R_PUBLIC_HOLD = "PUBLIC_GATEWAY_HOLD_EXCEEDS_THE_DEADLINE"
R_PUBLIC_NOT_A_BOOK_READ = "PUBLIC_GATEWAY_REFUSES_ALL_BUT_GET_BOOK"

_BOOK_PATH = re.compile(r"^/v1/markets/[a-z0-9][a-z0-9.\-]{2,200}/book$")


def _retry_after_s(v):
    """Retry-After as seconds (delta-seconds or an HTTP date), or None."""
    if v is None:
        return None
    try:
        return max(0.0, float(str(v).strip()))
    except (TypeError, ValueError):
        pass
    try:
        from email.utils import parsedate_to_datetime
        return max(0.0, parsedate_to_datetime(str(v)).timestamp()
                   - time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


class AuthLaneState:
    """One auth class's pace + Retry-After hold + counters. Thread-safe."""

    def __init__(self, name: str, *, min_gap_s: float = PUBLIC_MIN_GAP_S,
                 hold_floor_s: float = PUBLIC_HOLD_FLOOR_S,
                 penalty_s: float = PUBLIC_PENALTY_S,
                 max_mult: float = PUBLIC_MAX_MULT, clock=time.time,
                 sleep=time.sleep):
        self.name = name
        self.min_gap_s = float(min_gap_s)
        self.hold_floor_s = float(hold_floor_s)
        self.penalty_s = float(penalty_s)
        self.max_mult = float(max_mult)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._pace_lock = threading.Lock()
        self._last = 0.0
        self._mult = 1.0
        self._penalty_until = 0.0
        self._not_before = 0.0
        self._reason = None
        self._retry_after_last = None
        self._events: deque = deque(maxlen=5000)
        self._totals = {k: 0 for k in (
            "dispatched", "responses_2xx", "responses_429",
            "responses_other", "retry_after_seen", "refused_hold",
            "refused_queue", "refused_not_held", "refused_not_a_book_read")}

    def sleep(self, s: float) -> None:
        if s > 0:
            self._sleep(float(s))

    def note(self, kind: str) -> None:
        with self._lock:
            self._totals[kind] = self._totals.get(kind, 0) + 1
            if kind in ("dispatched", "responses_2xx", "responses_429"):
                self._events.append((self._clock(), kind))

    def gap_s(self, now=None) -> float:
        at = self._clock() if now is None else float(now)
        with self._lock:
            if at >= self._penalty_until:
                self._mult = 1.0
            return self.min_gap_s * self._mult

    def pace(self) -> float:
        """Block until this lane's gap has passed since its last request,
        then claim. Its own gap -- never venue_pace's."""
        with self._pace_lock:
            wait = self._last + self.gap_s() - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            return max(0.0, wait)

    def hold(self, now=None) -> dict:
        at = self._clock() if now is None else float(now)
        with self._lock:
            nb, why = self._not_before, self._reason
        return {"blocking": nb > at, "seconds_left": max(0.0, nb - at),
                "reason": why, "auth_lane": self.name}

    def on_response(self, status, retry_after=None) -> None:
        st = int(status) if isinstance(status, int) else None
        if st is not None and 200 <= st < 300:
            self.note("responses_2xx")
            return
        if st != 429:
            self.note("responses_other")
            return
        self.note("responses_429")
        ra = _retry_after_s(retry_after)
        now = self._clock()
        with self._lock:
            if ra is not None:
                self._totals["retry_after_seen"] += 1
                self._retry_after_last = ra
            until = now + max(self.hold_floor_s, ra or 0.0)
            if until > self._not_before:            # extends, never shortens
                self._not_before = until
                self._reason = "%s_429%s" % (
                    self.name, "" if ra is None else "_RETRY_AFTER_%gs" % ra)
            # x2 per 429 inside the penalty window, capped
            self._mult = min(self.max_mult, (self._mult * 2.0)
                             if now < self._penalty_until else 2.0)
            self._penalty_until = now + self.penalty_s

    def telemetry(self, *, now=None) -> dict:
        at = self._clock() if now is None else float(now)
        with self._lock:
            tot = dict(self._totals)
            ev = [k for t, k in self._events if at - t <= 60.0]
            ra = self._retry_after_last
        return {"auth_lane": self.name,
                "requests_per_min": ev.count("dispatched"),
                "responses_2xx_per_min": ev.count("responses_2xx"),
                "responses_429_per_min": ev.count("responses_429"),
                "totals": tot, "retry_after_last_s": ra,
                "gap_s": round(self.gap_s(now=at), 3),
                "min_gap_s": self.min_gap_s,
                "hold": self.hold(now=at),
                "state": "OWN pace + Retry-After hold (never the keyed "
                         "lane's, never armed by it)"}


class PublicGatewayTransport:
    """The keyless lane's request gate (inside install_read_only's GET-only
    wrapper): exactly GET /v1/markets/<slug>/book, the process write lock,
    this lane's pace, every dispatch and response counted, a 429's
    Retry-After armed on THIS lane only."""

    def __init__(self, inner, lane: AuthLaneState):
        self._inner = inner
        self._lane = lane

    def handle_request(self, request):
        method = str(getattr(request, "method", "")).upper()
        path = str(getattr(getattr(request, "url", None), "path", ""))
        if method != "GET" or not _BOOK_PATH.match(path):
            self._lane.note("refused_not_a_book_read")
            from .institutional_same_book import WriteRefused
            raise WriteRefused("%s: %s %s" % (R_PUBLIC_NOT_A_BOOK_READ,
                                              method, path))
        from . import venue_request_gate as _grt
        _grt.check_write_lock(method, path)
        if _prepaced.get():
            _prepaced.set(False)        # paced by the owner, for ONE request
        else:
            self._lane.pace()
        self._lane.note("dispatched")
        resp = self._inner.handle_request(request)
        hdrs = getattr(resp, "headers", None) or {}
        self._lane.on_response(getattr(resp, "status_code", None),
                               hdrs.get("retry-after"))
        return resp

    def close(self):
        try:
            self._inner.close()
        except Exception:                                      # noqa: BLE001
            pass


def public_client(lane: AuthLaneState, *, inner=None):
    """The retail SDK client with NO key, every transport wrapped GET-only
    (install_read_only) around this lane's PublicGatewayTransport. `inner`
    (tests) replaces the network transport. Built by the same-book probe's
    keyless constructor (the one census-listed keyless client)."""
    from . import institutional_same_book as SB

    def wrap(t):
        return PublicGatewayTransport(inner if inner is not None else t,
                                      lane)
    return SB._keyless_client(wrap_with=wrap)


_PUBLIC_CLIENT: dict = {"client": None, "lane": None}


def _default_public_rest(slug, *, lane: AuthLaneState) -> dict:
    """ONE keyless public-gateway book read. Our receipt instant is
    `observed_at`; the venue's own clock travels in marketData.transactTime.
    Never raises."""
    from . import institutional_same_book as SB
    if _PUBLIC_CLIENT["client"] is None or _PUBLIC_CLIENT["lane"] is not lane:
        _PUBLIC_CLIENT.update(client=public_client(lane), lane=lane)
    asked = time.time()
    r = SB.retail_book_read(slug, client=_PUBLIC_CLIENT["client"])
    got = time.time()
    return {"marketData": r.get("marketData") if r.get("ok") else None,
            "error": None if r.get("ok") else (r.get("error")
                                                or "PUBLIC_READ_FAILED"),
            "observed_at": got, "asked_at": asked, "feed": AUTH_PUBLIC}


def _keyed_retry_after():
    try:
        from . import venue_pace as VP
        return (VP.cooldown_state() or {}).get("retry_after_s")
    except Exception:                                          # noqa: BLE001
        return None


class Owner:
    """The cache / coalescing / priority-queue owner. One per process
    (`OWNER`); tests construct their own with fakes."""

    def __init__(self, *, transport=None, recent=None, gate=None,
                 clock=time.time, max_in_flight: int = MAX_IN_FLIGHT,
                 cache_max_age_s: float = CACHE_MAX_AGE_S,
                 public_transport=None, public_state=None,
                 same_book_tap=None):
        self._transport = transport
        self._recent = recent
        self._gate = gate
        self._clock = clock
        # THE PUBLIC-GATEWAY LANE: its own transport, its own pace / hold
        # state, one request at a time, HELD reads only
        self._public_transport = public_transport
        self.public = public_state if public_state is not None else \
            AuthLaneState(AUTH_PUBLIC, clock=clock)
        self._public_slot = threading.Lock()
        # THE HELD-MARK SAME-BOOK TAP (None: the process's default, which is
        # inert unless the institutional stream runs here; False: off)
        self._tap = same_book_tap
        self.max_in_flight = int(max_in_flight)
        self.cache_max_age_s = float(cache_max_age_s)
        self._cv = threading.Condition()
        self._heap: list = []
        self._seq = itertools.count()
        self._in_flight = 0
        self._flights: dict = {}
        self._held: dict = {}
        self._held_order: list = []
        self._events: deque = deque(maxlen=20000)
        self._totals = {k: 0 for k in (
            "reads", "cache_hits", "coalesced_reads", "rest_dispatches",
            "responses_2xx", "responses_429", "responses_other",
            "read_errors", "queue_deadline_refusals",
            "discovery_deferred_during_hold", "coalesce_deadline_refusals",
            "refused_foreign_venue")}
        self._by_lane = {ln: {"reads": 0, "dispatches": 0,
                              "queue_wait_s": 0.0} for ln in LANES}

    # ── the held registry ────────────────────────────────────────────
    def set_held(self, slugs, *, now=None) -> int:
        """The held-mark refresh names every held market (in its own
        priority order: due and not stream-covered first)."""
        at = float(now if now is not None else self._clock())
        order = [str(s) for s in slugs or ()
                 if s and not is_foreign_ticker(s)]
        with self._cv:
            for s in order:
                self._held[s] = at
            for s in [s for s, t in self._held.items()
                      if at - t > HELD_TTL_S]:
                self._held.pop(s, None)
            self._held_order = order
            return len(self._held)

    def is_held(self, slug, *, now=None) -> bool:
        at = float(now if now is not None else self._clock())
        with self._cv:
            t = self._held.get(str(slug or ""))
        return t is not None and at - t <= HELD_TTL_S

    def held_priority(self) -> list:
        with self._cv:
            return list(self._held_order)

    def lane_for(self, slug, explicit=None) -> str:
        if self.is_held(slug):
            return LANE_HELD
        ln = explicit or _lane_ctx.get()
        return ln if ln in _RANK else LANE_DISCOVERY

    def tap_for(self, slug):
        """The same-book tap for a HELD read of `slug`, or None."""
        if self._tap is False or not self.is_held(slug):
            return None
        tap = self._tap
        if tap is None:
            try:
                tap = default_same_book_tap()
            except Exception:                                  # noqa: BLE001
                return None
        return tap if tap is not None and tap.applies(slug) else None

    # ── accounting ───────────────────────────────────────────────────
    def _note(self, kind: str, n: int = 1) -> None:
        with self._cv:
            self._totals[kind] = self._totals.get(kind, 0) + n
            if kind in ("rest_dispatches", "responses_2xx", "responses_429"):
                self._events.append((self._clock(), kind))

    def _note_response(self, out: dict) -> None:
        acc = (out or {}).get("request_accounting") or {}
        statuses = [int(s) for s in acc.get("statuses") or ()
                    if isinstance(s, int)]
        if not statuses:
            det = ((out or {}).get("diagnostic") or {})
            st = det.get("status")
            if isinstance(st, int):
                statuses = [st]
            elif isinstance((out or {}).get("marketData"), dict) and \
                    not (out or {}).get("error"):
                statuses = [200]
        for st in statuses:
            if 200 <= st < 300:
                self._note("responses_2xx")
            elif st == 429:
                self._note("responses_429")
            else:
                self._note("responses_other")

    # ── the queue ────────────────────────────────────────────────────
    def _acquire(self, ln: str, deadline_epoch_s) -> bool:
        """A dispatch slot, in lane order. False when the caller's deadline
        (or the undeadlined cap) passes first."""
        t0 = self._clock()
        limit = (float(deadline_epoch_s) if deadline_epoch_s is not None
                 else t0 + MAX_UNDEADLINED_QUEUE_S)
        token = (_RANK[ln], next(self._seq))
        with self._cv:
            heapq.heappush(self._heap, token)
            try:
                while not (self._in_flight < self.max_in_flight
                           and self._heap[0] == token):
                    left = limit - self._clock()
                    if left <= 0:
                        self._heap.remove(token)
                        heapq.heapify(self._heap)
                        self._cv.notify_all()
                        return False
                    self._cv.wait(timeout=min(left, 0.25))
                heapq.heappop(self._heap)
                self._in_flight += 1
                self._by_lane[ln]["queue_wait_s"] += max(
                    0.0, self._clock() - t0)
                return True
            except BaseException:
                if token in self._heap:
                    self._heap.remove(token)
                    heapq.heapify(self._heap)
                self._cv.notify_all()
                raise

    def _release(self) -> None:
        with self._cv:
            self._in_flight = max(0, self._in_flight - 1)
            self._cv.notify_all()

    def queue_depth(self) -> dict:
        with self._cv:
            out = {ln: 0 for ln in LANES}
            for rank, _ in self._heap:
                out[LANES[rank]] += 1
            return dict(out, in_flight=self._in_flight)

    # ── the one read ─────────────────────────────────────────────────
    def _cached(self, slug, not_before_epoch):
        recent = self._recent or _default_recent
        try:
            got = recent(slug, max_age_s=self.cache_max_age_s)
        except Exception:                                      # noqa: BLE001
            got = None
        if got is None or not isinstance(got.get("marketData"), dict):
            return None
        if not_before_epoch is not None and float(
                got.get("observed_at") or 0.0) < float(not_before_epoch):
            return None
        return got

    def read(self, slug: str, *, deadline_epoch_s=None, not_before_epoch=None,
             lane_name=None, auth: str = None) -> dict:
        """ONE paper book read (blocking; run off the event loop). Never
        raises. Returns the transport's {marketData, error, observed_at, ...}
        or a cache / coalesced copy (original receipt instant) or a named
        refusal. `auth` = AUTH_PUBLIC sends it through the keyless public
        gateway lane -- refused unless the read is HELD."""
        slug = str(slug or "")
        if is_foreign_ticker(slug):
            self._note("refused_foreign_venue")
            return {"marketData": None, "error": R_FOREIGN_VENUE,
                    "observed_at": self._clock(), "owner_lane": None,
                    "refused_by": "PAPER_MARKET_DATA_OWNER"}
        ln = self.lane_for(slug, lane_name)
        auth = AUTH_PUBLIC if auth == AUTH_PUBLIC else AUTH_KEYED
        if auth == AUTH_PUBLIC and not self.is_held(slug):
            self.public.note("refused_not_held")
            return {"marketData": None, "error": R_PUBLIC_HELD_ONLY,
                    "observed_at": self._clock(), "owner_lane": ln,
                    "auth_lane": AUTH_PUBLIC,
                    "refused_by": "PAPER_MARKET_DATA_OWNER"}
        self._note("reads")
        with self._cv:
            self._by_lane[ln]["reads"] += 1
        # 1. THE CACHE
        hit = self._cached(slug, not_before_epoch)
        if hit is not None:
            self._note("cache_hits")
            return dict(hit, owner_lane=ln, served_by="CACHE")
        # 2. COALESCING
        while True:
            with self._cv:
                fl = self._flights.get((auth, slug))
                if fl is None:
                    fl = {"event": threading.Event(), "result": None,
                          "started_at": self._clock()}
                    self._flights[(auth, slug)] = fl
                    leader = True
                else:
                    leader = False
            if leader:
                break
            wait = (None if deadline_epoch_s is None
                    else float(deadline_epoch_s) - self._clock())
            if wait is not None and wait <= 0:
                self._note("coalesce_deadline_refusals")
                return {"marketData": None, "error": R_COALESCE_DEADLINE,
                        "observed_at": self._clock(), "owner_lane": ln}
            fl["event"].wait(timeout=MAX_UNDEADLINED_QUEUE_S
                             if wait is None else wait)
            res = fl.get("result")
            if res is None:
                self._note("coalesce_deadline_refusals")
                return {"marketData": None, "error": R_COALESCE_DEADLINE,
                        "observed_at": self._clock(), "owner_lane": ln}
            if isinstance(res.get("marketData"), dict) and \
                    not res.get("error") and (
                    not_before_epoch is None
                    or float(res.get("observed_at") or 0.0)
                    >= float(not_before_epoch)):
                self._note("coalesced_reads")
                return dict(res, shared_read=True, coalesced=True,
                            owner_lane=ln, served_by="COALESCED")
            # the leader failed (or answered too early): read for ourselves
            hit = self._cached(slug, not_before_epoch)
            if hit is not None:
                self._note("cache_hits")
                return dict(hit, owner_lane=ln, served_by="CACHE")
        out = None
        try:
            out = (self._dispatch_public(slug, ln, deadline_epoch_s)
                   if auth == AUTH_PUBLIC
                   else self._dispatch(slug, ln, deadline_epoch_s))
            return out
        finally:
            with self._cv:
                fl["result"] = out
                self._flights.pop((auth, slug), None)
            fl["event"].set()

    def _dispatch(self, slug, ln, deadline_epoch_s) -> dict:
        # 4. THE VENUE'S HOLD: discovery yields while it is in force
        if ln == LANE_DISCOVERY:
            g = (self._gate or _default_gate)() or {}
            if g.get("blocking"):
                self._note("discovery_deferred_during_hold")
                return {"marketData": None, "error": R_DISCOVERY_DEFERRED,
                        "observed_at": self._clock(), "owner_lane": ln,
                        "refused_by": "PAPER_MARKET_DATA_OWNER",
                        "gate": {"seconds_left": g.get("seconds_left"),
                                 "reason": g.get("reason")}}
        # 3. THE PRIORITY QUEUE
        if not self._acquire(ln, deadline_epoch_s):
            self._note("queue_deadline_refusals")
            return {"marketData": None, "error": R_QUEUE_DEADLINE,
                    "observed_at": self._clock(), "owner_lane": ln,
                    "refused_by": "PAPER_MARKET_DATA_OWNER"}
        try:
            self._note("rest_dispatches")
            with self._cv:
                self._by_lane[ln]["dispatches"] += 1
            tr = self._transport or _default_rest

            def call():
                try:
                    return tr(slug, deadline_epoch_s=deadline_epoch_s)
                except TypeError:
                    return tr(slug)
            tap = self.tap_for(slug)
            try:
                out = tap.wrap(slug, call) if tap is not None else call()
            except Exception as exc:                           # noqa: BLE001
                out = {"marketData": None, "error": type(exc).__name__}
            out = dict(out or {})
            out.setdefault("observed_at", self._clock())
            self._note_response(out)
            if out.get("error"):
                self._note("read_errors")
            out["owner_lane"] = ln
            out["served_by"] = "REST"
            out["auth_lane"] = AUTH_KEYED
            return out
        finally:
            self._release()

    def _dispatch_public(self, slug, ln, deadline_epoch_s) -> dict:
        """THE KEYLESS PUBLIC-GATEWAY LANE: its OWN Retry-After hold (never
        the authenticated key's, and a 429 here never holds that one), its
        own pace (inside the transport, per request), one request at a
        time. A hold it cannot wait out inside the deadline is refused by
        name -- nothing is sent."""
        def refuse(name, **kw):
            return dict({"marketData": None, "error": name,
                         "observed_at": self._clock(), "owner_lane": ln,
                         "auth_lane": AUTH_PUBLIC,
                         "refused_by": "PAPER_MARKET_DATA_OWNER"}, **kw)
        limit = (float(deadline_epoch_s) if deadline_epoch_s is not None
                 else self._clock() + MAX_UNDEADLINED_QUEUE_S)
        h = self.public.hold()
        if h["blocking"]:
            if self._clock() + h["seconds_left"] >= limit:
                self.public.note("refused_hold")
                return refuse(R_PUBLIC_HOLD, hold=h)
            self.public.sleep(h["seconds_left"])
        left = limit - self._clock()
        if left <= 0 or not self._public_slot.acquire(timeout=left):
            self.public.note("refused_queue")
            return refuse(R_QUEUE_DEADLINE)
        try:
            tr = self._public_transport or _default_public_rest

            def call():
                try:
                    return tr(slug, lane=self.public)
                except TypeError:
                    return tr(slug)
            tap = self.tap_for(slug)
            try:
                if tap is None:
                    out = call()
                else:
                    # the lane's pace BEFORE stream read #1; the transport
                    # skips its own pace for this one request
                    self.public.pace()
                    tok = _prepaced.set(True)
                    try:
                        out = tap.wrap(slug, call)
                    finally:
                        _prepaced.reset(tok)
            except Exception as exc:                           # noqa: BLE001
                out = {"marketData": None, "error": type(exc).__name__}
            out = dict(out or {})
            out.setdefault("observed_at", self._clock())
            # NOT remembered in the shared read cache: a public-gateway book
            # serves the held mark it was read for, never a discovery /
            # entry reader (which must not use this lane, even second-hand)
            out["owner_lane"] = ln
            out["served_by"] = "PUBLIC_GATEWAY"
            out["auth_lane"] = AUTH_PUBLIC
            return out
        finally:
            self._public_slot.release()

    # ── telemetry ────────────────────────────────────────────────────
    def telemetry(self, *, now=None) -> dict:
        at = float(now if now is not None else self._clock())
        with self._cv:
            tot = dict(self._totals)
            lanes = {k: dict(v, queue_wait_s=round(v["queue_wait_s"], 3))
                     for k, v in self._by_lane.items()}
            ev = [(t, k) for t, k in self._events if at - t <= 60.0]
            held = len(self._held)
        per_min = {"rest_requests_per_min": 0, "responses_2xx_per_min": 0,
                   "responses_429_per_min": 0}
        for _, k in ev:
            key = {"rest_dispatches": "rest_requests_per_min",
                   "responses_2xx": "responses_2xx_per_min",
                   "responses_429": "responses_429_per_min"}[k]
            per_min[key] += 1
        g = {}
        try:
            g = (self._gate or _default_gate)() or {}
        except Exception:                                      # noqa: BLE001
            g = {}
        return dict(per_min, version=VERSION, venue=VENUE,
                    scope="PROCESS_SINCE_IMPORT", totals=tot,
                    by_lane=lanes, queue_depth=self.queue_depth(),
                    held_registered=held,
                    max_in_flight=self.max_in_flight,
                    cache_max_age_s=self.cache_max_age_s,
                    auth_lanes={AUTH_KEYED: dict(
                        auth_lane=AUTH_KEYED,
                        requests_per_min=per_min["rest_requests_per_min"],
                        responses_2xx_per_min=per_min[
                            "responses_2xx_per_min"],
                        responses_429_per_min=per_min[
                            "responses_429_per_min"],
                        totals={k: tot.get(k) for k in (
                            "rest_dispatches", "responses_2xx",
                            "responses_429", "responses_other")},
                        retry_after_last_s=_keyed_retry_after(),
                        hold={"blocking": bool(g.get("blocking")),
                              "seconds_left": round(float(
                                  g.get("seconds_left") or 0.0), 3),
                              "reason": g.get("reason")},
                        state="venue_request_gate + venue_pace (process-"
                              "wide, shared with every keyed read)"),
                        AUTH_PUBLIC: self.public.telemetry(now=at)},
                    venue_hold={"blocking": bool(g.get("blocking")),
                                "seconds_left": round(float(
                                    g.get("seconds_left") or 0.0), 3),
                                "reason": g.get("reason")},
                    streams=stream_updates())


def _default_recent(slug, *, max_age_s):
    from .workers import ext_pinnacle_loop as LOOP
    return LOOP.recent_book(slug, max_age_s=max_age_s)


def _default_rest(slug, *, deadline_epoch_s=None):
    from .workers import ext_pinnacle_loop as LOOP
    return LOOP._read_book_blocking(slug, deadline_epoch_s=deadline_epoch_s)


def _default_gate() -> dict:
    try:
        from . import venue_request_gate as GRT
        return GRT.gate_state()
    except Exception:                                          # noqa: BLE001
        return {"blocking": False, "seconds_left": 0.0, "reason": None}


def stream_updates() -> dict:
    """The two streams' update counters in THIS process. Never raises; a
    stream not running here is null with the reason, never 0."""
    out: dict = {}
    try:
        from . import bettor_market_subscription as MSUB
        sub = MSUB.active()
        st = getattr(sub, "stream", None) if sub is not None else None
        out["retail"] = ({"running": False, "updates": None,
                          "why": "BETTOR_MARKET_SUBSCRIPTION_NOT_RUNNING_HERE"}
                         if st is None else
                         {"running": True,
                          "updates": int(getattr(st, "updates", 0) or 0),
                          "state": getattr(sub, "state", None)})
    except Exception as exc:                                   # noqa: BLE001
        out["retail"] = {"running": False, "updates": None,
                         "why": type(exc).__name__}
    try:
        from . import institutional_stream as IS
        d = IS.digest()
        run = d.get("state") in IS.RUNNING
        start = d.get("start") or {}
        inst = {"running": run, "state": d.get("state"),
                "why": d.get("why"),
                # the guard's refusal with the colliding env NAMES (never a
                # value) when the credential was refused
                "start_detail": start.get("detail"),
                "updates": d.get("messages") if run else None,
                "book_updates": d.get("book_updates") if run else None,
                "connected": d.get("connected"),
                "connection_seq": d.get("connection_seq"),
                "symbols": d.get("symbols"),
                "by_refusal": d.get("by_refusal"),
                # PMX gRPC PRIMARY evidence of THIS process (the deciding
                # one): venue acks on this connection, resident L2 books
                # current under the decision / held-mark bounds, their ages,
                # what the per-process bound refused or evicted
                "at": d.get("at"), "target": d.get("target"),
                "subscription_mode": d.get("subscription_mode"),
                "acked": d.get("acked"),
                "refused_symbols": d.get("refused_symbols"),
                "current_books": d.get("current_books"),
                "held_mark_current_books": d.get("held_mark_current_books"),
                "book_age_s": d.get("book_age_s"),
                "venue_receipt_lag_s": d.get("venue_receipt_lag_s"),
                "dropped_at_cap": d.get("dropped_at_cap"),
                "evicted": d.get("evicted")}
        try:
            from . import institutional_api_stream as IAS
            dd = IAS.describe()
            inst["api_stream"] = {k: dd.get(k) for k in (
                "running", "held_symbol_budget", "held_wanted",
                "held_subscribed", "bootstrap_backlog", "refdata_reads",
                "refdata_failures", "last_error")}
        except Exception as exc:                               # noqa: BLE001
            inst["api_stream"] = {"error": type(exc).__name__}
        tap = _TAP.get("tap")
        inst["same_book_tap"] = (tap.telemetry() if tap is not None else
                                 {"version": TAP_VERSION, "tapped": 0,
                                  "state": "NOT_ARMED_IN_THIS_PROCESS"})
        out["institutional"] = inst
    except Exception as exc:                                   # noqa: BLE001
        out["institutional"] = {"running": False, "updates": None,
                                "why": type(exc).__name__}
    return out


# ═════════════════════════════════════════════════════════════════════
# THE INSTITUTIONAL (PMX gRPC) BOOK AS A HELD MARK -- PER SYMBOL, PROVEN
# ═════════════════════════════════════════════════════════════════════
#
# An institutional stream book may stand for a held position's mark ONLY
# where, FOR THAT SYMBOL:
#   (a) the retail slug maps to the institutional symbol EXACTLY
#       (institutional_contract_map, via the API process's identity mapper on
#       the refdata it holds) -- the same registered contract, the YES leg =
#       the instrument's LONG side, price_transform IDENTITY, its own scales;
#   (b) the same-book probe (institutional_same_book_probe, exact-identity
#       samples only) supports equivalence for THIS symbol under
#       p5_runtime.same_book_status: >= SAME_BOOK_MIN_COMPARABLE comparable
#       samples, agree rate >= SAME_BOOK_MIN_AGREE_RATE, no stable
#       disagreement. The AGGREGATE over other symbols is never consulted;
#   (c) the stream's own current() answers ok, and BOTH the venue's
#       transact_time and our receipt are inside the mark SLA.
# ORIENTATION. The book recorded is the LONG instrument's book in the retail
# book's own price space (the retail /v1/markets/{slug}/book is the same
# instrument's book; the probe compared exactly these levels). It is recorded
# exactly like a retail read of the slug, so a SHORT position's mark is the
# complement of this book's long side, computed by the same
# bettor_book_snapshot.exit_ladder as for a retail book -- nothing here
# manufactures a NO book. An identity whose side is not LONG or whose price
# transform is not IDENTITY is refused.

INSTITUTIONAL_BASIS = "HELD_MARK_INSTITUTIONAL_STREAM"
INSTITUTIONAL_SOURCE = "PAPER_INSTITUTIONAL_STREAM"

I_NO_IDENTITY = "INSTITUTIONAL_IDENTITY_NOT_PROVEN_EXACT"
I_SYMBOL = "INSTITUTIONAL_SYMBOL_IS_NOT_THE_RETAIL_SLUG"
I_ORIENTATION = "INSTITUTIONAL_ORIENTATION_NOT_ESTABLISHED"
I_SAME_BOOK = "INSTITUTIONAL_SAME_BOOK_NOT_PROVEN_FOR_THIS_SYMBOL"
I_NOT_CURRENT = "INSTITUTIONAL_BOOK_NOT_CURRENT"
I_STALE = "INSTITUTIONAL_BOOK_OUTSIDE_THE_MARK_SLA"
I_LEVELS = "INSTITUTIONAL_BOOK_LEVELS_UNREADABLE"

# the per-symbol evidence reader lives with the probe it reads
# (institutional_same_book), so the workers' probe can order by it without
# importing this module
from .institutional_same_book import (  # noqa: E402,F401
    SAME_BOOK_SYMBOL_SQL, SAME_INSTANT_SQL, same_book_by_symbol)


def _iso_epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")
                                      ).timestamp()
    except (TypeError, ValueError):
        return None


def institutional_held_book(slug: str, *, identity, same_book, current,
                            now: float, sla_s: float) -> dict:
    """PURE: {"ok": True, "read": <record_book read>} or {"ok": False,
    "refusal", "why"}. `identity` is the identity mapper's answer for
    (slug, YES) (None when not exact), `same_book` THIS symbol's
    {"status", "detail"} (None when absent), `current` the stream's
    current(symbol) answer."""
    from decimal import Decimal

    from . import institutional_same_book as SB

    def refuse(name, why):
        return {"ok": False, "refusal": name, "why": why}

    ident = identity if isinstance(identity, dict) else None
    if not ident or ident.get("status") != "EXACT":
        return refuse(I_NO_IDENTITY, "no exact retail->institutional identity"
                      " for %r" % slug)
    sym = ident.get("symbol") or ident.get("institutional_symbol")
    if sym != slug:
        return refuse(I_SYMBOL, "identity names %r, not %r" % (sym, slug))
    if ident.get("institutional_side") != "LONG" or \
            ident.get("price_transform") != "IDENTITY":
        return refuse(I_ORIENTATION, "side %r / transform %r: the book is not"
                      " shown to be the retail long-side book in its price "
                      "space" % (ident.get("institutional_side"),
                                 ident.get("price_transform")))
    sbk = same_book if isinstance(same_book, dict) else {}
    if sbk.get("status") != "SUPPORTED":
        return refuse(I_SAME_BOOK, "same-book evidence for %s is %s (%s)" % (
            slug, sbk.get("status") or "ABSENT", sbk.get("detail")))
    cur = current if isinstance(current, dict) else {}
    if not cur.get("ok") or cur.get("symbol") not in (None, slug):
        return refuse(I_NOT_CURRENT, "stream current(): %s" % (
            cur.get("refusal") or "NO_ANSWER"))
    snap = ((cur.get("evidence") or {}).get("snapshot") or {})
    recv = snap.get("received_at")
    src = _iso_epoch(snap.get("venue_ts"))
    if recv is None or src is None:
        return refuse(I_STALE, "receipt %r / venue clock %r unknown" % (
            recv, snap.get("venue_ts")))
    receipt_age = float(now) - float(recv)
    source_age = float(now) - float(src)
    if not (0.0 <= receipt_age <= sla_s) or not (
            -5.0 <= source_age <= sla_s):
        return refuse(I_STALE, "receipt age %.1fs / source age %.1fs vs SLA "
                      "%.0fs" % (receipt_age, source_age, sla_s))
    lv = SB.stream_levels(cur)
    if lv is None:
        return refuse(I_LEVELS, "levels not scaled by the instrument's own "
                      "scales")

    def wire(levels):
        return [{"px": {"value": format(p.normalize(), "f")},
                 "qty": format(q.normalize(), "f")}
                for p, q in levels if isinstance(p, Decimal)]
    mkt = ((cur.get("evidence") or {}).get("market") or {})
    return {"ok": True, "read": {
        "marketData": {"bids": wire(lv["bids"]), "offers": wire(lv["offers"]),
                       "state": mkt.get("state"),
                       "transactTime": snap.get("venue_ts")},
        "observed_at": float(recv),
        "institutional_receipt_age_s": round(receipt_age, 3),
        "institutional_source_age_s": round(source_age, 3),
        "orientation": {"book": "LONG_INSTRUMENT",
                        "price_transform": "IDENTITY",
                        "short_marked_as": "COMPLEMENT_OF_THE_LONG_BOOK_"
                                           "(bettor_book_snapshot.exit_ladder)"},
        "same_book": sbk.get("detail")}}


class InstitutionalBooks:
    """The held-mark refresh's institutional source in THIS process: the
    API stream's identity mapper + the resident books + per-symbol same-book
    evidence loaded once per run (`load`)."""

    def __init__(self, *, identity_fn, current_fn):
        self._identity = identity_fn
        self._current = current_fn
        self.same_book: dict = {}
        self.refusals: dict = {}

    async def load(self, conn, slugs) -> dict:
        self.same_book = await same_book_by_symbol(conn, slugs)
        self.durable = await durable_certifications(conn, slugs)
        return {"symbols_with_evidence": len(self.same_book),
                "supported": sum(1 for v in self.same_book.values()
                                 if v.get("status") == "SUPPORTED"),
                "durable_certificates": len(self.durable)}

    def book(self, slug: str, *, now: float, sla_s: float):
        try:
            ident = self._identity(slug, "YES")
        except Exception:                                      # noqa: BLE001
            ident = None
        cur = None
        if ident:
            try:
                cur = self._current(slug, now=now)
            except Exception:                                  # noqa: BLE001
                cur = None
        got = institutional_held_book(
            slug, identity=ident,
            same_book=effective_same_book(self.same_book.get(slug),
                                          getattr(self, "durable", {}).get(
                                              slug), ident),
            current=cur, now=now, sla_s=sla_s)
        if not got["ok"]:
            self.refusals[got["refusal"]] = \
                self.refusals.get(got["refusal"], 0) + 1
            return None
        return got["read"]


# ── DURABLE SAME-BOOK CERTIFICATION (Universal Market Plane, 312) ──────
#
# The window evidence above (30 comparable same-instant samples at >= 95%,
# exact identity, 24 h) stays the primary rule and is unchanged. A certificate
# the market plane persisted (market_plane_certification) answers SUPPORTED
# for a symbol ONLY when (a) the window holds no contradiction for it (a
# CONTRADICTED window always wins), and (b) the certificate's fingerprint
# equals the fingerprint of the identity in force now (same contract,
# scales, transform, ontology and book schema). So a restart, or a quiet day
# that ages samples out of the window, no longer erases a proven
# equivalence; any relevant change invalidates it.

DURABLE_SQL = """
    SELECT contract_id, fingerprint, comparable, agreeing, agreement,
           certified_at
      FROM market_plane_certification
     WHERE status = 'SUPPORTED' AND contract_id = ANY($1::text[])
"""


async def durable_certifications(conn, slugs) -> dict:
    syms = sorted({str(s) for s in slugs or () if s})
    if not syms:
        return {}
    try:
        if not await conn.fetchval(
                "SELECT to_regclass('market_plane_certification') "
                "IS NOT NULL"):
            return {}
        rows = await conn.fetch(DURABLE_SQL, syms)
    except Exception:                                          # noqa: BLE001
        return {}
    out: dict = {}
    for r in rows:
        out.setdefault(r["contract_id"], []).append(dict(r))
    return out


def effective_same_book(window, durable, ident) -> dict | None:
    """PURE. The window evidence, or -- when it is not SUPPORTED and not
    CONTRADICTED -- a durable certificate whose fingerprint matches the
    identity in force now. Never SUPPORTED without one or the other."""
    w = window if isinstance(window, dict) else None
    if w and w.get("status") in ("SUPPORTED", "CONTRADICTED"):
        return w
    if not durable or not isinstance(ident, dict) or \
            ident.get("status") != "EXACT":
        return w
    from .market_plane import certification as CERT
    fp = CERT.fingerprint(CERT.identity_for(
        ident.get("symbol"), price_scale=ident.get("price_scale"),
        qty_scale=ident.get("qty_scale"),
        price_transform=ident.get("price_transform")))
    for c in durable:
        if c.get("fingerprint") == fp:
            return {"status": "SUPPORTED",
                    "detail": {"basis": "DURABLE_CERTIFICATION",
                               "fingerprint": fp,
                               "comparable": c.get("comparable"),
                               "agreeing": c.get("agreeing"),
                               "certified_at": str(c.get("certified_at")),
                               "window": (w or {}).get("detail")}}
    return w


def default_institutional():
    """The institutional source when the PMX stream AND the API process's
    refdata / identity task run here; None otherwise (fail closed)."""
    try:
        from . import institutional_api_stream as IAS
        from . import institutional_stream as IS
        if not IAS.running():
            return None
        # the held-mark read: the mark SLA's snapshot bound, every other
        # currency check of current() unchanged
        return InstitutionalBooks(identity_fn=IAS.identity_mapper,
                                  current_fn=IS.current_for_held_mark)
    except Exception:                                          # noqa: BLE001
        return None


# ═════════════════════════════════════════════════════════════════════
# THE HELD-MARK SAME-BOOK TAP -- per-symbol evidence at no extra venue read
# ═════════════════════════════════════════════════════════════════════
#
# WHY. A held symbol may be marked from the institutional book only once ITS
# OWN same-book evidence is SUPPORTED (>= SAME_BOOK_MIN_COMPARABLE comparable
# samples at >= SAME_BOOK_MIN_AGREE_RATE in the window). The workers' probe
# takes one sample per focus member per minute with its OWN keyless retail
# read -- one more request on a gateway that already answers 429. But every
# held-mark REST read in THIS process already fetches exactly the retail book
# the probe compares (GET /v1/markets/{slug}/book). The tap makes that read
# the probe's retail read: `institutional_same_book.sample` runs AROUND it
# (stream read #1, the held read, stream read #2) -- the same function, the
# same 1 s window, the same verdicts -- so held symbols accrue evidence at the
# held-mark cadence, held first, and no request is added.
#
# ONLY WHERE IT MEANS SOMETHING: the slug is a registered held market, the
# institutional stream runs in this process and the slug maps EXACTLY on the
# refdata this process holds. Otherwise the read runs untouched. The read's
# own answer is returned unchanged (the tap never alters, retries or delays
# it beyond the two in-memory stream reads). On the public-gateway lane the
# lane's pace is taken BEFORE stream read #1 (and the transport then skips
# its own, once), so the window measures the request, not the pacing.
# Rows the stream could not answer (identity / stream / scale) are counted,
# not persisted; everything else is persisted by the held-mark refresh.

TAP_VERSION = "HELD_MARK_SAME_BOOK_TAP_V1"
TAP_WHY = "HELD_MARK_READ_TAP: the held-mark refresh's own retail read"
TAP_MAX_PENDING = 2000
_prepaced: contextvars.ContextVar = contextvars.ContextVar(
    "paper_public_lane_prepaced", default=False)


class SameBookTap:
    """Wraps one held REST read in an institutional_same_book sample."""

    def __init__(self, *, eligible, current, record_for, clock=time.time,
                 max_pending: int = TAP_MAX_PENDING):
        self._eligible = eligible
        self._current = current
        self._record_for = record_for
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: deque = deque(maxlen=int(max_pending))
        self.counts = {"tapped": 0, "persistable": 0, "by_verdict": {}}

    def applies(self, slug) -> bool:
        try:
            return bool(self._eligible(slug))
        except Exception:                                      # noqa: BLE001
            return False

    def wrap(self, slug, call):
        """Run `call()` (the held read) inside one same-book sample; return
        exactly what `call()` returned (or raise what it raised)."""
        from . import institutional_same_book as SB
        try:
            rec = self._record_for(slug)
        except Exception:                                      # noqa: BLE001
            rec = None
        if rec is None:
            return call()
        got: dict = {}

        def retail_read(_s):
            try:
                out = call()
            except BaseException as exc:                       # noqa: BLE001
                got["exc"] = exc
                raise
            got["out"] = out
            o = out if isinstance(out, dict) else {}
            md = o.get("marketData")
            err = o.get("error")
            return {"ok": isinstance(md, dict) and not err,
                    "marketData": md if isinstance(md, dict) else None,
                    "error": err}

        row = None
        try:
            row = SB.sample(slug, record=rec, books_current=self._current,
                            retail_read=retail_read, clock=self._clock,
                            focus={"why": TAP_WHY})
        except Exception:                                      # noqa: BLE001
            row = None
        if "exc" in got:
            raise got["exc"]
        if "out" not in got:
            # the sample stopped before the read (identity not exact here)
            return call()
        if row is not None:
            self._note(row)
        return got["out"]

    def _note(self, row: dict) -> None:
        from . import institutional_same_book as SB
        v = row.get("verdict")
        why = row.get("verdict_reason")
        key = v if v != SB.V_NC else "%s:%s" % (v, why)
        keep = not (v == SB.V_NC and why in (SB.NC_IDENTITY, SB.NC_STREAM,
                                             SB.NC_SCALE))
        with self._lock:
            self.counts["tapped"] += 1
            self.counts["by_verdict"][key] = \
                self.counts["by_verdict"].get(key, 0) + 1
            if keep:
                self.counts["persistable"] += 1
                self._pending.append(row)

    def drain(self) -> list:
        with self._lock:
            rows = list(self._pending)
            self._pending.clear()
        return rows

    def telemetry(self) -> dict:
        with self._lock:
            return {"version": TAP_VERSION, "tapped": self.counts["tapped"],
                    "persistable": self.counts["persistable"],
                    "pending": len(self._pending),
                    "by_verdict": dict(self.counts["by_verdict"])}


_TAP: dict = {"tap": None}


def default_same_book_tap():
    """The process's tap: eligible only while the institutional stream runs
    here, for a registered held market that maps EXACTLY on the refdata this
    process holds. Built once; eligibility is re-checked per read."""
    if _TAP["tap"] is not None:
        return _TAP["tap"]

    def eligible(slug):
        from . import institutional_api_stream as IAS
        return IAS.running() and IAS._exact_here(slug)

    def record_for(slug):
        from . import institutional_api_stream as IAS
        with IAS._LOCK:
            return (IAS.REFDATA.get(slug) or {}).get("record")

    def current(slug, now=None):
        # the evidence certifies the held-mark use, so it reads as that use
        from . import institutional_stream as IS
        return IS.current_for_held_mark(slug, now=now)

    _TAP["tap"] = SameBookTap(eligible=eligible, current=current,
                              record_for=record_for)
    return _TAP["tap"]


async def persist_tap_samples(conn, *, tap=None, process_id=None) -> dict:
    """The tap's pending samples -> institutional_same_book_probe (one
    savepoint). Never raises; a failed write is counted, not retried."""
    t = tap if tap is not None else _TAP["tap"]
    rows = t.drain() if t is not None else []
    if not rows:
        return {"rows": 0, "written": 0}
    from . import institutional_same_book as SB
    try:
        async with conn.transaction():
            n = await SB.persist(conn, rows, process_id=process_id or (
                "%s:%s" % (TAP_VERSION, os.getpid())),
                service="sportsassets-api")
    except Exception as exc:                                   # noqa: BLE001
        return {"rows": len(rows), "written": 0, "error": type(exc).__name__}
    return {"rows": len(rows), "written": n}


OWNER = Owner()


def read(slug: str, **kw) -> dict:
    return OWNER.read(slug, **kw)


def set_held(slugs, *, now=None) -> int:
    """Register the held markets with the owner AND name them, in order, to
    the institutional focus universe (subscribed / probed first)."""
    try:
        from . import institutional_focus_universe as FU
        FU.note_held_first(slugs)
    except Exception:                                          # noqa: BLE001
        pass
    return OWNER.set_held(slugs, now=now)


def held_priority() -> list:
    return OWNER.held_priority()


def telemetry(*, now=None) -> dict:
    return OWNER.telemetry(now=now)


class PublicLane:
    """The held-mark refresh's handle on the public-gateway lane: this
    lane's hold, and one HELD read through the owner (off the loop)."""

    def __init__(self, owner=None):
        self._owner = owner

    @property
    def owner(self):
        return self._owner if self._owner is not None else OWNER

    def hold(self) -> dict:
        return self.owner.public.hold()

    async def read(self, slug: str, *, deadline: float) -> dict:
        """`deadline` is a time.monotonic() instant (the refresh's)."""
        import asyncio
        left = float(deadline) - time.monotonic()
        return await asyncio.to_thread(
            self.owner.read, slug, deadline_epoch_s=time.time() + left,
            auth=AUTH_PUBLIC)


def public_lane_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(PUBLIC_ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def default_public_lane():
    """The public-gateway lane for THIS process's held-mark refresh, unless
    switched off (PAPER_PUBLIC_GATEWAY_LANE=off) or the retail SDK is not
    importable here."""
    if not public_lane_enabled():
        return None
    try:
        import polymarket_us  # noqa: F401
    except Exception:                                          # noqa: BLE001
        return None
    return PublicLane()


def reset() -> None:
    """Tests only."""
    global OWNER
    OWNER = Owner()
