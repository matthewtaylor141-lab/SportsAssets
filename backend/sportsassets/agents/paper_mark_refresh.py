"""THE HELD-MARK REFRESH: EVERY HELD MARKET RE-READ INSIDE THE MARK SLA.

THE ROOT CAUSE IT REPAIRS (production 2026-10-05: "158 stale marks > 300s,
12 unmarked, 71/241 fresh"). The paper pass reads books in ONE step
(`paper_runtime.step_books`) under `cadence.max_book_reads_per_pass` = 12, of
which the books step may use HALF (6), and held positions are read LAST --
after due entries, Xavier's priority slugs and every resting order (each
standing protection order is a resting order). With ~60 s between scheduled
passes that is at most ~6 markets refreshed a minute against a book of
~150+ held markets and a 300 s SLA: most marks could never be refreshed in
time, whatever the venue said. A read that failed was recorded but invisible
to the marks, and a read the pass never reached left no record at all.

THE REPAIR. A separate, explicitly budgeted refresh run (its own pool
connection, never the paper pass's lock or read cap, so Derek's half of the
pass is unchanged):

  1. every open paper position's market is a candidate; a market whose
     newest successful read carries a TERMINAL venue state is not re-read
     (EXTERNAL_UNAVAILABLE, the observation is the evidence) and is
     recorded SKIPPED_EXTERNAL_TERMINAL;
  2. a market is DUE when its newest successful read is older than
     REFRESH_AFTER_S (180 s: inside the 300 s SLA with one run of margin)
     or it was never read;
  3. due markets are served STALEST FIRST (never read first), the larger
     exposure first on a tie -- so no market starves;
  4. a book THIS PROCESS already read within HARVEST_MAX_AGE_S (the
     collection cycle's own reads) is recorded with its ORIGINAL receipt
     instant -- no venue request, nothing made fresher than it is;
  5. otherwise one paced, gated venue read (the same read the pass makes,
     `paper_derek.read_book_within_deadline` through the read-only
     PaperMarketDataClient), recorded as an observation (an error included);
  6. the run is BOUNDED: at most MAX_READS_PER_RUN venue reads, at most
     RUN_BUDGET_S seconds, at most MAX_VENUE_READS_PER_HOUR venue reads per
     rolling hour in this process; every due market it could not reach is
     recorded SKIPPED_* with the bound that stopped it;
  7. the run and EVERY due market's outcome are recorded in
     paper_mark_refresh_runs (migration 270).

THE EXTERNAL API BUDGET, MADE EXPLICIT. Every venue read goes through the
process-wide venue pace (venue_pace.MIN_GAP_S, normal lane: the money paths'
priority claims go first) and the venue request gate (429 cooldowns honoured,
refused by name rather than waited past the run's deadline). On top of that
this refresh may spend at most MAX_VENUE_READS_PER_HOUR (3,600: one a second
on average, about a third of the pace's 1 / 0.35 s ceiling) and at most
MAX_READS_PER_RUN per run, one run at a time, at most one run per
MIN_RUN_INTERVAL_S. A held book of N markets needs about N x 3600 / 210
reads an hour in steady state (each market re-read every ~180-240 s), so the
budget covers ~200 held markets; beyond that the shortfall is RECORDED
(SKIPPED_HOURLY_BUDGET -> FEED_GAP), never hidden and never relabelled.

THE VENUE COOLDOWN, MEASURED IN PRODUCTION (2026-10-05, research-sql run
37384642567). The authenticated book endpoint answers 429 with a
`Retry-After` of 7-10 s; the workers process had 158 of 463 dispatches
rate-limited and the request gate refused 1,405 more. Every read of runs 1-21
asked for an 8 s deadline, so whenever a hold was in force the gate refused it
(VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE) and the run burned its whole
read cap in under a second: 0-1 reads OK out of 90, every run. The repair,
which waits for the venue instead of asking it for more:

  * THE COOLDOWN IS WAITED OUT INSIDE THE RUN'S OWN BUDGET. This run has no
    decision deadline; its bound is RUN_BUDGET_S. A hold no longer than
    MAX_COOLDOWN_WAIT_S that leaves a full read inside the run is slept
    (asynchronously, on this run's own task) and the read then dispatches
    after the venue's window. A longer hold stops the REST lane for this run:
    every remaining due market is recorded SKIPPED_VENUE_COOLDOWN with the
    seconds left and the reason -- FEED_GAP, never fresh, and no doomed
    request or refusal row is written for it.
  * THE PUSHED BOOK IS USED WHEN THE PROCESS HOLDS ONE. When the retail
    market-data subscription (bettor_market_subscription) is running in this
    process, every held market is asked for (`want`) and a due market whose
    stream book passes the stream's own eligibility at the SLA bounds
    (`book_at`: current connection, open, venue clock AND our receipt both
    <= SLA_S) is recorded with ITS receipt instant (HELD_MARK_STREAM) -- no
    venue request, nothing made fresher than it is. A quiet market whose last
    pushed book is older than the SLA is NOT inferred current; it falls to
    the REST lane like any other.

THE SOURCE ORDER (P0 market-data freshness, 2026-10-06). Freshness was 0.56:
the REST endpoint 429s below what ~100 held markets need inside 300 s, and
every paper reader polled it independently. Each due market now consults,
in order, and takes the FIRST that answers inside the SLA:

  1. INSTITUTIONAL -- the PMX gRPC resident book (institutional_stream
     .current), ONLY where this symbol's exact retail->institutional identity
     AND this symbol's own same-book evidence are proven
     (paper_market_data.institutional_held_book; never aggregate agreement),
     recorded HELD_MARK_INSTITUTIONAL_STREAM with ITS receipt instant;
  2. RETAIL STREAM -- the market-data subscription's pushed book at the SLA
     bounds (HELD_MARK_STREAM), as before;
  3. HARVEST -- a book this process already read (HELD_MARK_REFRESH_SHARED_
     READ), as before;
  4. PACED REST -- through the process's one paper market-data owner
     (paper_market_data) in its HELD lane: coalesced with any concurrent read
     of the slug, ahead of every discovery read, under the shared venue hold.
So the REST budget is spent only on held markets no stream covers. Every
held market is registered with the owner (held reads outrank discovery
wherever they are made) and named, due-first, to the institutional focus
universe (held symbols subscribed and same-book probed first). The run's
per-source counts and the owner's telemetry are recorded on the run
(migration 306).

PAPER ONLY. It reads books and writes paper_book_observations and its own run
record; it places, cancels and changes no order, limit or threshold.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .. import bettor_paper_freshness as PMF
from .. import bettor_paper_guard as G
from .. import bettor_paper_ledger as L
from .. import bettor_paper_session as S
from .. import paper_market_data as PMD

log = logging.getLogger(__name__)

VERSION = "PAPER_HELD_MARK_REFRESH_V1"

#: a held market is due once its newest successful read is older than this
#: (the SLA is bettor_paper_ledger.MARK_STALE_AFTER_S = 300 s; 180 leaves
#: more than one run interval of margin)
REFRESH_AFTER_S = 180.0
#: the explicit external budget (see the module docstring)
MAX_READS_PER_RUN = 90
RUN_BUDGET_S = 45.0
MAX_VENUE_READS_PER_HOUR = 3600
MIN_RUN_INTERVAL_S = 30.0
#: one read never holds the run longer than this
PER_READ_TIMEOUT_S = 8.0
#: a book this process read at most this long ago answers without a venue
#: request (recorded with its original receipt instant)
HARVEST_MAX_AGE_S = 60.0
#: the longest venue hold this run sleeps out before its next read (the
#: measured Retry-After is 7-10 s); a longer hold stops the REST lane
MAX_COOLDOWN_WAIT_S = 15.0
READ_BASIS = "HELD_MARK_REFRESH"
HARVEST_BASIS = "HELD_MARK_REFRESH_SHARED_READ"
SOURCE = "PAPER_MARKET_DATA_CLIENT"
STREAM_BASIS = "HELD_MARK_STREAM"
STREAM_SOURCE = "PAPER_MARKET_STREAM"

O_READ_OK = "READ_OK"
O_READ_FAILED = "READ_FAILED"
O_HARVESTED = "HARVESTED_SHARED_READ"
O_SKIP_TERMINAL = "SKIPPED_EXTERNAL_TERMINAL"
O_SKIP_RUN_CAP = "SKIPPED_RUN_READ_CAP"
O_SKIP_HOURLY = "SKIPPED_HOURLY_BUDGET"
O_SKIP_TIME = "SKIPPED_RUN_TIME_BUDGET"
O_SKIP_COOLDOWN = "SKIPPED_VENUE_COOLDOWN"
O_STREAM = "STREAM_BOOK"
INSTITUTIONAL_BASIS = PMD.INSTITUTIONAL_BASIS
INSTITUTIONAL_SOURCE = PMD.INSTITUTIONAL_SOURCE
O_INSTITUTIONAL = "INSTITUTIONAL_STREAM_BOOK"
PUBLIC_BASIS = PMD.PUBLIC_BASIS
PUBLIC_SOURCE = PMD.PUBLIC_SOURCE
#: the consultation order (pinned by a test); the last step is two REST
#: lanes in parallel -- the authenticated key and the keyless public gateway
SOURCE_ORDER = ("INSTITUTIONAL_STREAM", "RETAIL_STREAM", "HARVEST",
                "PACED_REST")
REST_LANES = ("AUTHENTICATED", "PUBLIC_GATEWAY")

_HOURLY: dict = {"reads": []}


def budget() -> dict:
    return {"refresh_after_s": REFRESH_AFTER_S,
            "sla_s": PMF.SLA_S,
            "max_reads_per_run": MAX_READS_PER_RUN,
            "run_budget_s": RUN_BUDGET_S,
            "max_venue_reads_per_hour": MAX_VENUE_READS_PER_HOUR,
            "min_run_interval_s": MIN_RUN_INTERVAL_S,
            "per_read_timeout_s": PER_READ_TIMEOUT_S,
            "harvest_max_age_s": HARVEST_MAX_AGE_S,
            "max_cooldown_wait_s": MAX_COOLDOWN_WAIT_S,
            "source_order": list(SOURCE_ORDER),
            "institutional": "institutional_stream.current, per symbol: exact "
                             "identity + this symbol's same-book SUPPORTED + "
                             "venue clock and receipt <= SLA_S",
            "rest_owner": "paper_market_data (cache, coalescing, HELD lane)",
            "stream":"bettor_market_subscription (when running here): "
                      "book_at at the SLA bounds, its own receipt instant",
            "pace": "venue_pace (process-wide gap, normal lane) + "
                    "venue_request_gate (429 cooldowns)"}


def hourly_used(now: float) -> int:
    _HOURLY["reads"] = [t for t in _HOURLY["reads"] if now - t < 3600.0]
    return len(_HOURLY["reads"])


def _default_recent(slug: str, *, max_age_s: float):
    """A book this process read within `max_age_s` (the collection cycle's
    own successful reads), or None. Never raises; never a venue request."""
    try:
        from ..workers import ext_pinnacle_loop as LOOP
        return LOOP.recent_book(slug, max_age_s=max_age_s)
    except Exception:                                           # noqa: BLE001
        return None


def _default_gate() -> dict:
    """The process's venue not-before hold (venue_request_gate.gate_state):
    {blocking, seconds_left, reason}. Never raises; unknown is not blocking
    (the gate itself still refuses at dispatch)."""
    try:
        from .. import venue_request_gate as GRT
        return GRT.gate_state()
    except Exception:                                           # noqa: BLE001
        return {"blocking": False, "seconds_left": 0.0, "reason": None}


class _SubscriptionBooks:
    """The retail market-data subscription's books, at the SLA bounds."""

    def __init__(self, sub, msub):
        self._sub = sub
        self._msub = msub

    def want(self, slugs) -> dict:
        return self._msub.want(list(slugs))

    def book(self, slug: str, *, now: float):
        st = getattr(self._sub, "stream", None)
        if st is None:
            return None
        got = st.book_at(slug, max_source_age_s=PMF.SLA_S,
                         max_receipt_age_s=PMF.SLA_S)
        if not got or not got.get("eligible") or not got.get("book"):
            return None
        bk = got["book"]
        return {"marketData": {"bids": bk.get("bids") or [],
                               "offers": bk.get("offers") or [],
                               "state": got.get("venue_state"),
                               "transactTime": got.get("source_ts")},
                "observed_at": float(now) - float(got["receipt_age_s"]),
                "stream_receipt_age_s": got["receipt_age_s"],
                "stream_source_age_s": got.get("source_age_s")}


def _default_stream():
    """The retail market-data subscription running in THIS process, or None
    (BETTOR_MARKET_SUBSCRIPTION off, no market-data key, not started)."""
    try:
        from .. import bettor_market_subscription as MSUB
        sub = MSUB.active()
        if sub is None or getattr(sub, "stream", None) is None:
            return None
        return _SubscriptionBooks(sub, MSUB)
    except Exception:                                           # noqa: BLE001
        return None


def plan(held: list, ev: dict, *, now: float) -> dict:
    """WHICH HELD MARKETS ARE DUE, IN WHAT ORDER (pure).

    `held`: [{us_market_slug, exposure_usd}] one per market; `ev`:
    bettor_paper_freshness.market_evidence. Returns {due[...], not_due[...],
    terminal{slug: evidence}}."""
    due, not_due, terminal = [], [], {}
    for h in held:
        slug = h["us_market_slug"]
        ok = (ev.get(slug) or {}).get("last_ok")
        st = str((ok or {}).get("market_state") or "").upper()
        if ok and st in PMF.TERMINAL_MARKET_STATES:
            terminal[slug] = {"obs_id": ok["obs_id"], "market_state": st}
            continue
        age = None if not ok else now - float(ok["at"])
        if age is not None and age <= REFRESH_AFTER_S:
            not_due.append(slug)
            continue
        due.append((0 if age is None else 1, -(age or 0.0),
                    -float(h.get("exposure_usd") or 0.0), slug,
                    None if not ok else float(ok["at"])))
    due.sort()
    return {"due": [(d[3], d[4]) for d in due], "not_due": not_due,
            "terminal": terminal}


async def refresh(conn, *, account_id: str = L.ACCOUNT_ID, market_data=None,
                  now: float | None = None, clock=None,
                  run_budget_s: float = RUN_BUDGET_S,
                  max_reads: int = MAX_READS_PER_RUN,
                  hourly_budget: int = MAX_VENUE_READS_PER_HOUR,
                  recent=None, trigger: str = "SCHEDULED", gate=None,
                  sleep=None, stream=None, institutional=None,
                  public=None) -> dict:
    """ONE BOUNDED HELD-MARK REFRESH RUN for `account_id`. Records every due
    market's outcome. Never raises (CancelledError excepted).

    `institutional`: a paper_market_data.InstitutionalBooks-like source
    ({load(conn, slugs), book(slug, now=, sla_s=)}); by default the one the
    process runs (None when the PMX stream is not running here)."""
    from . import paper_derek as PD
    from .. import bettor_paper_simulator as SIM
    clock = clock or time.time
    at = float(now if now is not None else clock())
    t0 = time.monotonic()
    deadline = t0 + float(run_budget_s)
    # THE PUBLIC-GATEWAY LANE runs only on the process's own read path: a
    # caller that hands in its own market-data client (a test, a stand-in)
    # gets exactly that client and no second lane unless it names one.
    if public is None and market_data is None:
        public = PMD.default_public_lane()
    md = market_data if market_data is not None else G.PaperMarketDataClient()
    recent = recent if recent is not None else _default_recent
    gate = gate if gate is not None else _default_gate
    sleep = sleep if sleep is not None else asyncio.sleep
    books = stream if stream is not None else _default_stream()
    inst = institutional if institutional is not None else \
        PMD.default_institutional()
    out: dict[str, Any] = {"version": VERSION, "account_id": account_id,
                           "trigger": trigger, "at": at, "held_markets": 0,
                           "due": 0, "not_due": 0, "harvested": 0,
                           "read_attempted": 0, "read_ok": 0,
                           "read_failed": 0, "skipped_budget": 0,
                           "skipped_terminal": 0, "skipped_cooldown": 0,
                           "stream_books": 0, "institutional_books": 0,
                           "cooldown_waited_s": 0.0,
                           "stream": "RUNNING" if books else "NOT_RUNNING",
                           "institutional": ("RUNNING" if inst
                                             else "NOT_RUNNING"),
                           "sources": {"institutional_stream": 0,
                                       "retail_stream": 0, "harvest": 0,
                                       "rest": 0, "public_gateway": 0},
                           "lane_reads": {},
                           "public_lane": ("RUNNING" if public is not None
                                           else "NOT_RUNNING"),
                           "outcomes": {},
                           "budget": budget()}
    run_id = None
    has_log = await PMF._has(conn, "paper_mark_refresh_runs")
    try:
        if has_log:
            run_id = await conn.fetchval(
                "INSERT INTO paper_mark_refresh_runs (account_id, trigger, "
                " started_at, budget) VALUES ($1,$2,to_timestamp($3),"
                " $4::jsonb) RETURNING run_id", account_id, trigger, at,
                json.dumps(out["budget"]))
        out["run_id"] = run_id
        pos = await L.positions(conn, account_id)
        expo: dict = {}
        for p in pos:
            expo[p["us_market_slug"]] = expo.get(p["us_market_slug"], 0.0) \
                + float(p.get("cost_basis_usd") or 0.0)
        held = [{"us_market_slug": s, "exposure_usd": e}
                for s, e in expo.items()]
        ev = await PMF.market_evidence(conn, list(expo),
                                       account_id=account_id)
        pl = plan(held, ev, now=at)
        out.update(held_markets=len(held), due=len(pl["due"]),
                   not_due=len(pl["not_due"]),
                   skipped_terminal=len(pl["terminal"]))
        # EVERY HELD MARKET IS REGISTERED WITH THE ONE PAPER OWNER (its reads
        # outrank discovery wherever they are made) and named, due first, to
        # the institutional focus universe.
        try:
            PMD.set_held([s for s, _ in pl["due"]] + list(pl["not_due"]),
                         now=at)
        except Exception:                                       # noqa: BLE001
            pass
        if books is not None:
            try:
                out["stream_want"] = books.want(list(expo))
            except Exception as exc:                            # noqa: BLE001
                out["stream_want"] = {"error": type(exc).__name__}
        if inst is not None:
            try:
                out["institutional_evidence"] = await inst.load(
                    conn, [s for s, _ in pl["due"]])
            except Exception as exc:                            # noqa: BLE001
                out["institutional_evidence"] = {"error": type(exc).__name__}
        rest_due: list = []
        for slug, evd in pl["terminal"].items():
            out["outcomes"][slug] = {"outcome": O_SKIP_TERMINAL,
                                     "why": evd["market_state"],
                                     "obs_id": evd["obs_id"]}
        for slug, ok_at in pl["due"]:
            # 1. THE INSTITUTIONAL BOOK, only where THIS symbol's identity
            #    and same-book evidence are proven, at the SLA bounds, with
            #    ITS receipt instant
            ibk = None
            if inst is not None:
                try:
                    ibk = inst.book(slug, now=float(clock()), sla_s=PMF.SLA_S)
                except Exception:                               # noqa: BLE001
                    ibk = None
            if ibk is not None and (
                    ok_at is None
                    or float(ibk.get("observed_at") or 0) > ok_at):
                rec = await SIM.record_book(
                    conn, slug=slug, read=ibk, source=INSTITUTIONAL_SOURCE,
                    read_basis=INSTITUTIONAL_BASIS)
                out["harvested"] += 1
                out["institutional_books"] += 1
                out["sources"]["institutional_stream"] += 1
                out["outcomes"][slug] = {
                    "outcome": O_INSTITUTIONAL, "obs_id": rec.get("obs_id"),
                    "receipt_age_s": ibk.get("institutional_receipt_age_s"),
                    "source_age_s": ibk.get("institutional_source_age_s")}
                continue
            # 2. THE PUSHED RETAIL BOOK, at the SLA bounds, with its receipt
            pushed = None
            if books is not None:
                try:
                    pushed = books.book(slug, now=float(clock()))
                except Exception:                               # noqa: BLE001
                    pushed = None
            if pushed is not None and (
                    ok_at is None
                    or float(pushed.get("observed_at") or 0) > ok_at):
                rec = await SIM.record_book(
                    conn, slug=slug, read=pushed, source=STREAM_SOURCE,
                    read_basis=STREAM_BASIS)
                out["harvested"] += 1
                out["stream_books"] += 1
                out["sources"]["retail_stream"] += 1
                out["outcomes"][slug] = {
                    "outcome": O_STREAM, "obs_id": rec.get("obs_id"),
                    "receipt_age_s": pushed.get("stream_receipt_age_s")}
                continue
            # 3. THE HARVEST: a newer book this process already holds
            shared = None
            try:
                shared = recent(slug, max_age_s=HARVEST_MAX_AGE_S)
            except Exception:                                   # noqa: BLE001
                shared = None
            if shared is not None and isinstance(
                    shared.get("marketData"), dict) and (
                    ok_at is None
                    or float(shared.get("observed_at") or 0) > ok_at):
                rec = await SIM.record_book(
                    conn, slug=slug, read=dict(shared, shared_read=True),
                    source=SOURCE, read_basis=HARVEST_BASIS)
                out["harvested"] += 1
                out["sources"]["harvest"] += 1
                out["outcomes"][slug] = {"outcome": O_HARVESTED,
                                         "obs_id": rec.get("obs_id")}
                continue
            # 4. NO STREAM / HARVEST: a REST read (below, in the lanes)
            rest_due.append((slug, ok_at))
        # ── 4. THE REST LANES ──────────────────────────────────────────
        # AUTHENTICATED (the paced, gated key through the one owner) and,
        # when it runs here, PUBLIC_GATEWAY (keyless, its own pace and
        # Retry-After hold). Each lane's worker pulls the next due market
        # from ONE shared queue, so the held markets split across the lanes
        # by availability: a lane on a long hold stops, and the other keeps
        # serving -- neither lane's 429 starves the other.
        queue = list(rest_due)
        rlock = asyncio.Lock()
        lanes = [("AUTHENTICATED", None)]
        if public is not None:
            lanes.append(("PUBLIC_GATEWAY", public))
        stopped: dict = {}

        async def _record(slug, got, *, source, basis, feed):
            async with rlock:
                rec = await SIM.record_book(conn, slug=slug, read=got,
                                            source=source, read_basis=basis)
            if rec.get("error"):
                out["read_failed"] += 1
                out["outcomes"][slug] = {"outcome": O_READ_FAILED,
                                         "why": str(rec["error"])[:160],
                                         "obs_id": rec.get("obs_id"),
                                         "lane": feed}
            else:
                out["read_ok"] += 1
                out["sources"]["rest" if feed == "AUTHENTICATED"
                               else "public_gateway"] += 1
                out["outcomes"][slug] = {"outcome": O_READ_OK,
                                         "obs_id": rec.get("obs_id"),
                                         "lane": feed}

        def _bound():
            if out["read_attempted"] >= int(max_reads):
                return O_SKIP_RUN_CAP
            if hourly_used(float(clock())) >= int(hourly_budget):
                return O_SKIP_HOURLY
            if deadline - time.monotonic() <= PD.BOOK_READ_RESERVE_S:
                return O_SKIP_TIME
            return None

        async def _worker(name, pub):
            while queue:
                if _bound() is not None:
                    return
                # THIS LANE'S HOLD: waited out inside the run's budget, or
                # this lane stops (the other lane, if any, carries on)
                g = (gate() if pub is None else pub.hold()) or {}
                hold = float(g.get("seconds_left") or 0.0) \
                    if g.get("blocking") else 0.0
                if hold > 0.0:
                    room = (deadline - time.monotonic()
                            - PD.BOOK_READ_RESERVE_S - PER_READ_TIMEOUT_S)
                    if hold > MAX_COOLDOWN_WAIT_S or hold > room:
                        stopped[name] = "%.1fs_left:%s" % (
                            hold, g.get("reason") or "VENUE_HOLD")
                        return
                    await sleep(hold + 0.05)
                    out["cooldown_waited_s"] = round(
                        out["cooldown_waited_s"] + hold + 0.05, 3)
                    continue
                if not queue:
                    return
                slug, _ok_at = queue.pop(0)
                rctx = {"market_data": md,
                        "deadline": min(deadline, time.monotonic()
                                        + PER_READ_TIMEOUT_S
                                        + PD.BOOK_READ_RESERVE_S)}
                _HOURLY["reads"].append(float(clock()))
                out["read_attempted"] += 1
                out["lane_reads"][name] = out["lane_reads"].get(name, 0) + 1
                if pub is None:
                    with PMD.lane(PMD.LANE_HELD):
                        got = await PD.read_book_within_deadline(rctx, slug)
                    await _record(slug, got, source=SOURCE,
                                  basis=READ_BASIS, feed=name)
                else:
                    got = await pub.read(slug, deadline=rctx["deadline"])
                    await _record(slug, got, source=PUBLIC_SOURCE,
                                  basis=PUBLIC_BASIS, feed=name)

        await asyncio.gather(*[_worker(n, p) for n, p in lanes])
        # every due market no lane reached is recorded with the bound or
        # the hold that stopped it -- FEED_GAP, never fresh
        for slug, _ok_at in queue:
            stop, why = _bound(), None
            if stop is None:
                stop = O_SKIP_COOLDOWN
                why = "; ".join("%s:%s" % kv for kv in sorted(
                    stopped.items())) or "VENUE_HOLD"
                if len(stopped) == 1 and "AUTHENTICATED" in stopped:
                    why = stopped["AUTHENTICATED"]
            out["skipped_budget"] += 1
            if stop == O_SKIP_COOLDOWN:
                out["skipped_cooldown"] += 1
            out["outcomes"][slug] = {"outcome": stop, "why": why or stop}
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    out["elapsed_s"] = round(time.monotonic() - t0, 3)
    out["venue_reads_last_hour"] = hourly_used(float(clock()))
    if inst is not None:
        out["institutional_refusals"] = dict(
            getattr(inst, "refusals", {}) or {})
    # THE HELD READS' SAME-BOOK SAMPLES (paper_market_data.SameBookTap):
    # persisted here, per symbol, so the next run's per-symbol evidence can
    # admit the institutional book. Nothing pending (no stream here) -> no
    # write at all.
    try:
        tapped = await PMD.persist_tap_samples(conn)
        if tapped.get("rows"):
            out["same_book_tap"] = tapped
    except Exception as exc:                                    # noqa: BLE001
        out["same_book_tap"] = {"error": type(exc).__name__}
    try:
        out["market_data"] = PMD.telemetry()
    except Exception as exc:                                    # noqa: BLE001
        out["market_data"] = {"error": type(exc).__name__}
    if run_id is not None:
        try:
            sql = ("UPDATE paper_mark_refresh_runs SET finished_at = "
                   " to_timestamp($2), held_markets=$3, due=$4, not_due=$5, "
                   " harvested=$6, read_attempted=$7, read_ok=$8, "
                   " read_failed=$9, skipped_budget=$10, skipped_terminal=$11,"
                   " outcomes=$12::jsonb, error=$13")
            args = [run_id, at + out["elapsed_s"], out["held_markets"],
                    out["due"], out["not_due"], out["harvested"],
                    out["read_attempted"], out["read_ok"], out["read_failed"],
                    out["skipped_budget"], out["skipped_terminal"],
                    json.dumps(out["outcomes"], default=str),
                    out.get("error")]
            if await _has_306(conn):
                # MIGRATION 306: the fields the run already computed and the
                # record dropped, plus the per-source counts and telemetry
                sql += (", skipped_cooldown=$14, stream_books=$15, "
                        " institutional_books=$16, cooldown_waited_s=$17, "
                        " sources=$18::jsonb, market_data=$19::jsonb")
                args += [out["skipped_cooldown"], out["stream_books"],
                         out["institutional_books"],
                         float(out["cooldown_waited_s"]),
                         json.dumps(out["sources"]),
                         json.dumps(out["market_data"], default=str)]
            await conn.execute(sql + " WHERE run_id = $1", *args)
        except Exception as exc:                                # noqa: BLE001
            out["record_error"] = type(exc).__name__
    return out


#: Columns migration 306 adds to paper_mark_refresh_runs.
COLUMNS_306 = ("skipped_cooldown", "stream_books", "institutional_books",
               "cooldown_waited_s", "sources", "market_data")


async def _has_306(conn) -> bool:
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE "
            " table_name = 'paper_mark_refresh_runs' AND column_name = "
            " ANY($1::text[])", list(COLUMNS_306))
        return int(n or 0) == len(COLUMNS_306)
    except Exception:                                           # noqa: BLE001
        return False


# ═════════════════════════════════════════════════════════════════════
# THE SCHEDULE: ONE BACKGROUND RUN AT A TIME, ON ITS OWN CONNECTION
# ═════════════════════════════════════════════════════════════════════

ACQUIRE_TIMEOUT_S = 10.0
HARD_TIMEOUT_S = RUN_BUDGET_S + 30.0
_TASK: dict = {"task": None, "last_started": 0.0, "runs": 0,
               "coalesced": 0, "last": None}


async def run_once(get_pool, *, trigger: str,
                   account_id: str = L.ACCOUNT_ID) -> dict:
    """ONE RUN, only while the paper session is enabled (env flag AND the
    control row). Never raises."""
    try:
        pool = await get_pool()
        async with pool.acquire(timeout=ACQUIRE_TIMEOUT_S) as conn:
            en = await S.enablement(conn)
            if not en.get("enabled"):
                res = {"ran": False, "refusal": en.get("refusal")}
            else:
                res = await asyncio.wait_for(
                    refresh(conn, account_id=account_id, trigger=trigger),
                    HARD_TIMEOUT_S)
                res["ran"] = True
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        res = {"ran": False, "error": "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:200])}
    _TASK["last"] = {k: res.get(k) for k in (
        "ran", "refusal", "error", "run_id", "held_markets", "due",
        "read_attempted", "read_ok", "read_failed", "skipped_budget",
        "harvested", "skipped_cooldown", "stream_books", "cooldown_waited_s",
        "stream", "elapsed_s", "institutional_books", "institutional",
        "sources", "institutional_refusals", "lane_reads", "public_lane")}
    return res


def schedule(get_pool, *, trigger: str, now: float | None = None) -> dict:
    """START ONE BACKGROUND RUN unless one is running or the last one started
    less than MIN_RUN_INTERVAL_S ago. Returns at once; never raises."""
    if not S.env_on():
        return {"scheduled": False, "why": S.R_ENV_OFF}
    t = _TASK.get("task")
    if t is not None and not t.done():
        _TASK["coalesced"] += 1
        return {"scheduled": False, "why": "A_REFRESH_RUN_IS_RUNNING"}
    at = time.time() if now is None else float(now)
    if at - _TASK["last_started"] < MIN_RUN_INTERVAL_S:
        return {"scheduled": False, "why": "MIN_RUN_INTERVAL",
                "next_after_s": round(MIN_RUN_INTERVAL_S
                                      - (at - _TASK["last_started"]), 3)}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"scheduled": False, "why": "NO_RUNNING_LOOP"}
    _TASK["last_started"] = at
    _TASK["runs"] += 1
    _TASK["task"] = loop.create_task(run_once(get_pool, trigger=trigger))
    return {"scheduled": True, "trigger": trigger, "runs": _TASK["runs"]}


def status() -> dict:
    t = _TASK.get("task")
    return {"version": VERSION, "running": t is not None and not t.done(),
            "runs": _TASK["runs"], "coalesced": _TASK["coalesced"],
            "last": _TASK["last"], "budget": budget(),
            "venue_reads_last_hour": hourly_used(time.time())}
