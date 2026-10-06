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


class Owner:
    """The cache / coalescing / priority-queue owner. One per process
    (`OWNER`); tests construct their own with fakes."""

    def __init__(self, *, transport=None, recent=None, gate=None,
                 clock=time.time, max_in_flight: int = MAX_IN_FLIGHT,
                 cache_max_age_s: float = CACHE_MAX_AGE_S):
        self._transport = transport
        self._recent = recent
        self._gate = gate
        self._clock = clock
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
            "discovery_deferred_during_hold", "coalesce_deadline_refusals")}
        self._by_lane = {ln: {"reads": 0, "dispatches": 0,
                              "queue_wait_s": 0.0} for ln in LANES}

    # ── the held registry ────────────────────────────────────────────
    def set_held(self, slugs, *, now=None) -> int:
        """The held-mark refresh names every held market (in its own
        priority order: due and not stream-covered first)."""
        at = float(now if now is not None else self._clock())
        order = [str(s) for s in slugs or () if s]
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
             lane_name=None) -> dict:
        """ONE paper book read (blocking; run off the event loop). Never
        raises. Returns the transport's {marketData, error, observed_at, ...}
        or a cache / coalesced copy (original receipt instant) or a named
        refusal."""
        slug = str(slug or "")
        ln = self.lane_for(slug, lane_name)
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
                fl = self._flights.get(slug)
                if fl is None:
                    fl = {"event": threading.Event(), "result": None,
                          "started_at": self._clock()}
                    self._flights[slug] = fl
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
            out = self._dispatch(slug, ln, deadline_epoch_s)
            return out
        finally:
            with self._cv:
                fl["result"] = out
                self._flights.pop(slug, None)
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
            try:
                out = tr(slug, deadline_epoch_s=deadline_epoch_s)
            except TypeError:
                out = tr(slug)
            except Exception as exc:                           # noqa: BLE001
                out = {"marketData": None, "error": type(exc).__name__}
            out = dict(out or {})
            out.setdefault("observed_at", self._clock())
            self._note_response(out)
            if out.get("error"):
                self._note("read_errors")
            out["owner_lane"] = ln
            out["served_by"] = "REST"
            return out
        finally:
            self._release()

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
        d = IS.BOOKS.digest()
        run = d.get("state") in IS.RUNNING
        out["institutional"] = {"running": run, "state": d.get("state"),
                                "updates": d.get("messages") if run else None,
                                "symbols": d.get("symbols"),
                                "by_refusal": d.get("by_refusal")}
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

SAME_BOOK_SYMBOL_SQL = """
    SELECT symbol,
           CASE WHEN verdict <> 'NOT_COMPARABLE' AND NOT %s
                THEN 'NOT_COMPARABLE' ELSE verdict END       AS verdict,
           count(*)                                          AS n,
           count(*) FILTER (WHERE NOT stream_changed_in_window) AS n_stable
      FROM institutional_same_book_probe
     WHERE probed_at > now() - make_interval(secs => $1)
       AND symbol = ANY($2::text[])
     GROUP BY 1, 2
"""


async def same_book_by_symbol(conn, symbols) -> dict:
    """{symbol: {"status", "detail"}} under p5_runtime.same_book_status, for
    THESE symbols only (exact-identity samples, the P5 window). Never
    raises; an absent table or a failed read is {} (nothing proven)."""
    from . import p5_runtime as P5R
    syms = sorted({str(s) for s in symbols or () if s})
    if not syms:
        return {}
    try:
        if not await conn.fetchval(
                "SELECT to_regclass('institutional_same_book_probe') "
                "IS NOT NULL"):
            return {}
        rows = await conn.fetch(SAME_BOOK_SYMBOL_SQL % P5R.EXACT_SAMPLE_SQL,
                                float(P5R.SAME_BOOK_WINDOW_S), syms)
    except Exception:                                          # noqa: BLE001
        return {}
    by: dict = {}
    for r in rows:
        by.setdefault(r["symbol"], {})[r["verdict"]] = (int(r["n"]),
                                                        int(r["n_stable"]))
    out = {}
    for s, counts in by.items():
        st, det = P5R.same_book_status(counts)
        out[s] = {"status": st, "detail": det}
    return out


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
        return {"symbols_with_evidence": len(self.same_book),
                "supported": sum(1 for v in self.same_book.values()
                                 if v.get("status") == "SUPPORTED")}

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
        got = institutional_held_book(slug, identity=ident,
                                      same_book=self.same_book.get(slug),
                                      current=cur, now=now, sla_s=sla_s)
        if not got["ok"]:
            self.refusals[got["refusal"]] = \
                self.refusals.get(got["refusal"], 0) + 1
            return None
        return got["read"]


def default_institutional():
    """The institutional source when the PMX stream AND the API process's
    refdata / identity task run here; None otherwise (fail closed)."""
    try:
        from . import institutional_api_stream as IAS
        from . import institutional_stream as IS
        if not IAS.running():
            return None
        return InstitutionalBooks(identity_fn=IAS.identity_mapper,
                                  current_fn=IS.current)
    except Exception:                                          # noqa: BLE001
        return None


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


def reset() -> None:
    """Tests only."""
    global OWNER
    OWNER = Owner()
