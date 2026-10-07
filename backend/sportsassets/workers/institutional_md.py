"""Worker: the PERSISTENT INSTITUTIONAL MARKET-DATA process.

Owner directive 2026-09-20 00:1xZ §5/§6: institutional market data →
a persistent BETTOR market-data process → an in-memory L2 book →
incremental features → the experimental model. "Do not put GitHub
Actions in this critical path."

WHAT THIS LOOP IS. It holds the production credential installed on this
service, bootstraps instrument metadata once per symbol, and then keeps
the current institutional book for a small focus set in memory. The
experimental loop reads that memory; it does not make a REST call to
decide.

THE MECHANISM IS NAMED HONESTLY. §6 says to use the venue's documented
persistent market-data mechanism WHERE VERIFIED. It is not verified:
the documentation spells the gRPC host four ways and none is confirmed
for production, and this lane has never opened one. So REST bootstraps
and REST maintains at a tight cadence, the mechanism is recorded as
REST_POLL_MAINTAINED_IN_MEMORY, and the stream target is recorded as
NOT_IDENTIFIED. Calling a poll a subscription would be a claim about
the venue that nobody here has checked.

FRESHNESS IS THE POINT OF A PERSISTENT PROCESS, and it is measured
rather than assumed: every book carries the venue's own transactTime,
this process's receive instant, and the lag between them. A book older
than the frozen limit is STALE, and the execution path refuses it.

NO ORDER PATH. `pmx_institutional` has an allow-list of exactly three
reads and no insert, cancel, replace, preview or funding path in its
source; a test asserts their absence. Even if the token carries
write:orders, ORDER_SUBMISSION_IMPLEMENTATION is NONE -- a scope the
venue granted is not a capability this process has.

Kill: INSTITUTIONAL_MD=off.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from .. import institutional_book as ib
from .. import institutional_contract_map as icm
from .. import institutional_focus_universe as fu
from .. import institutional_same_book as samebook
from .. import institutional_stream as istream
from .. import institutional_stream_evidence as sevid
from .. import market_data_identity as mdi
from .. import pmx_institutional as pmx
from .. import shadow_experimental_store as xstore
from .. import shadow_identity_resolver as resolver
from ..db import get_pool, heartbeat

log = logging.getLogger(__name__)

SERVICE = "institutional_md"

# THE MAINTENANCE CADENCE. Fast enough that a book is CURRENT under the
# 5s freshness limit for most of its life, slow enough to stay far
# below the ~3 req/s the gateway 429'd at: eight symbols per sweep at
# 2s is about 4 reads/s at the worst... so the sweep is paced.
SWEEP_S = 2.0
READ_PACING_S = 0.15
REFDATA_REFRESH_S = 3600
BOOTSTRAP_BACKOFF_S = 30.0
AUTH_BACKOFF_S = 60.0

# How many instruments this process maintains. The focus set is chosen
# by the experimental lane; this loop only keeps what that lane is
# already sampling, so the two never drift apart.
MAX_INSTRUMENTS = 8

# HOW OFTEN A BOOK IS WRITTEN DOWN as evidence. The in-memory book
# updates every sweep; persisting every sweep would write thousands of
# rows an hour for books nothing decided on. A book that a decision
# actually walked is persisted by the decision path itself; this is the
# background trail, and it is deliberately sparse.
EVIDENCE_EVERY_S = 60.0

# HOW OFTEN THE IDENTITY BINDING IS RE-RESOLVED. A binding is a fact
# about two venues' KEYS; those change when a market is relisted, not
# between ticks. The write is deduped by the binding's own sha, so a
# re-resolution that agrees costs one no-op insert.
IDENTITY_EVERY_S = 60.0

# THE STREAM'S DURABLE EVIDENCE (migration 210), only while the gRPC stream
# is enabled here: one row per wanted symbol (and one process row) per
# minute, and one same-book probe sample per mapped symbol per minute.
# Kill the probe alone with INSTITUTIONAL_SAME_BOOK_PROBE=off.
STREAM_EVIDENCE_EVERY_S = 60.0
SAME_BOOK_EVERY_S = 60.0

# THE PROBE'S RETAIL READS ARE PACED BY WHAT CAN BE COMPARED (production,
# release 730325f, 60 min: 536 of the probe's keyless retail reads answered
# RateLimitError -- one read per focus member per minute, whatever the venue
# hold or the stream said -- starving every retail read this process makes).
# A retail read is now spent only where a same-instant comparison is
# possible (institutional_same_book.NC_RETAIL_OLDER): never during the
# shared venue hold, never for a member whose stream book is not current,
# never while the stream's own book state is younger than the retail cache
# horizon (the cached retail representation is then an older state -- the
# 19-28 s offset measured), and at most SAME_BOOK_MAX_READS_PER_PASS per pass,
# rotating through the eligible members. A deferred member is counted by
# reason, never persisted as a sample and never a verdict.
SAME_BOOK_MAX_READS_PER_PASS = 10
SAME_BOOK_MIN_QUIET_S = 30.0
D_HOLD = "VENUE_HOLD_IN_FORCE"
D_BUDGET = "PASS_READ_BUDGET_SPENT"
D_STREAM = "STREAM_BOOK_NOT_CURRENT"
D_RECENT = "STREAM_STATE_YOUNGER_THAN_THE_RETAIL_CACHE_HORIZON"
_PROBE_CURSOR = {"i": 0}


class ReadDeferred(Exception):
    """The probe's retail read is not spent for this member this pass."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason

# THE FOCUS UNIVERSE (institutional_focus_universe), only while the stream is
# enabled here: what BETTOR holds and evaluates, in priority order, bounded by
# fu.MAX_MEMBERS (= the API stream's MAX_SYMBOLS, inside the stream's own
# MAX_SYMBOLS). It is what the STREAM subscribes (EXACT members only), what
# the evidence RECORDER covers and what the same-book PROBE samples. The REST
# sweep above stays on the experimental lane's own focus set (MAX_INSTRUMENTS)
# -- that lane reads those books -- and the focus set is the universe's last
# tier. Each member's refdata is read ONCE through the same allow-listed
# `instruments` read (bootstrap_instrument), at most
# UNIVERSE_BOOTSTRAPS_PER_SWEEP per sweep; an unlisted slug is re-asked no
# sooner than UNIVERSE_RETRY_UNLISTED_S. Recomputed every FOCUS_UNIVERSE_EVERY_S.
FOCUS_UNIVERSE_EVERY_S = 60.0
UNIVERSE_BOOTSTRAPS_PER_SWEEP = 2
UNIVERSE_RETRY_UNLISTED_S = 300.0
UNIVERSE_REFDATA_REFRESH_S = float(REFDATA_REFRESH_S)


def _off(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).strip().lower() in (
        "off", "0", "false", "no")


def _now():
    return datetime.now(tz=timezone.utc)


def bootstrap_instrument(client, symbol) -> dict:
    """Exact refdata, with transport failure distinct from proven unlisted."""
    from ..market_plane.refdata_progress import instrument_response
    parsed = instrument_response(symbol, client.read("instruments",symbol))
    rec = parsed["record"]
    ps,qs = pmx.scales_of(rec)
    return dict(parsed,priceScale=ps,qtyScale=qs,listed=rec is not None,
                priceable=bool(ps and qs))


def sweep_once(client, store, symbols) -> dict:
    """One paced pass over the focus set. Runs OFF the event loop."""
    from ..venue_pace import pace

    stats = {"read": 0, "stored": 0, "failed": 0, "notListed": 0,
             "venueMs": []}
    for symbol in symbols[:MAX_INSTRUMENTS]:
        inst = store.instrument(symbol)
        if inst is None:
            pace(READ_PACING_S)
            boot = bootstrap_instrument(client, symbol)
            store.put_instrument(symbol, boot["record"],
                                 price_scale=boot["priceScale"],
                                 qty_scale=boot["qtyScale"])
            if not boot["priceable"]:
                stats["notListed"] += 1
                continue
            inst = store.instrument(symbol)

        if not inst.get("priceable"):
            stats["notListed"] += 1
            continue

        pace(READ_PACING_S)
        row = client.read("book", symbol)
        stats["read"] += 1
        if row.get("status") != 200 or not isinstance(row.get("body"), dict):
            stats["failed"] += 1
            continue
        stored = store.put_book(symbol, row["body"],
                                received_at=_now(),
                                venue_request_ms=row.get("ms"),
                                request_id=row.get("requestId"))
        if stored is not None:
            stats["stored"] += 1
            if row.get("ms") is not None:
                stats["venueMs"].append(row["ms"])
    return stats


async def resolve_identities(pool, store, symbols) -> dict:
    """§2: bind the focus universe BEFORE any signal asks about it.

    THIS LOOP IS THE RIGHT PLACE and it is not a coincidence. It is the
    only process that holds the institutional credential, and it has
    already fetched each focus instrument's own refdata record to learn
    its scales -- so the identity binding costs no extra venue call at
    all. Resolving it in the experimental hot path instead would put a
    refdata round trip between a signal and its execution, which §7 of
    the earlier directive forbids and which is how the binding came to
    be computed from whatever a book fetch happened to carry.

    BOTH LEGS ARE RESOLVED, YES FIRST. §3: "Do not let unresolved
    BUY_NO baskets delay BUY_YES." The NO leg's honest verdict is
    recorded as its own row; it gates nothing on the YES side.
    """
    stats = {"resolved": 0, "written": 0, "yesEligible": 0,
             "noEligible": 0, "unresolved": 0}
    retail = await xstore.retail_rows(pool, symbols[:MAX_INSTRUMENTS])
    for symbol in symbols[:MAX_INSTRUMENTS]:
        inst = store.instrument(symbol) or {}
        record = inst.get("record")
        for leg in (resolver.LEG_YES, resolver.LEG_NO):
            row = resolver.resolve(
                symbol, leg, instrument_record=record,
                retail_row=retail.get((symbol, leg)))
            stats["resolved"] += 1
            if row["identity_status"] == resolver.ident.NOT_IDENTIFIED:
                stats["unresolved"] += 1
            if row["execution_eligible"]:
                stats["yesEligible" if leg == resolver.LEG_YES
                      else "noEligible"] += 1
            try:
                if await xstore.record_identity_binding(pool, row):
                    stats["written"] += 1
                    log.info("institutional_md: bound %s/%s -> %s %s (%s)",
                             symbol, leg, row["identity_status"],
                             row.get("institutional_instrument_id"),
                             "ELIGIBLE" if row["execution_eligible"]
                             else "; ".join(row.get("why") or [])[:120])
            except Exception:                                  # noqa: BLE001
                log.debug("institutional_md: binding write failed",
                          exc_info=True)
    return stats


async def persist_trail(pool, store, symbols) -> int:
    """The sparse background trail. Append-only, deduped by book sha.

    A book a DECISION walked is persisted by the decision path with its
    decision; this is the record that the process was reading the venue
    at all, and it is deliberately thin.
    """
    written = 0
    for symbol in symbols[:MAX_INSTRUMENTS]:
        row = store.current(symbol)
        if row.get("FRESHNESS_STATUS") == ib.ABSENT:
            continue
        try:
            if await xstore.record_direct_evidence(pool, row):
                written += 1
        except Exception:                                      # noqa: BLE001
            log.debug("institutional_md: trail write failed", exc_info=True)
    return written


def _identity(symbol, store, retail) -> dict:
    """The exact-identity answer for (retail slug = symbol, YES), compact.
    Pure over data this process already holds; no venue call."""
    inst = store.instrument(symbol) or {}
    m = icm.map_retail_to_institutional(
        symbol, "yes", inst.get("record"),
        retail_row=(retail or {}).get((symbol, "yes")))
    return {k: m.get(k) for k in ("version", "ok", "refusal",
                                  "institutional_symbol", "price_scale",
                                  "qty_scale")}


async def record_stream_evidence(pool, recorder, store, books=None) -> dict:
    """One minute of stream evidence -> institutional_stream_evidence."""
    books = books if books is not None else istream.BOOKS
    wanted = books.wanted()
    try:
        retail = await xstore.retail_rows(pool, wanted) if wanted else {}
    except Exception:                                          # noqa: BLE001
        retail = {}
    rows = recorder.rows(books, identity_for=lambda s: _identity(
        s, store, retail))
    return {"rows": len(rows), "written": await sevid.persist(pool, rows)}


def _venue_age(read, now):
    ts = samebook._epoch_of(samebook._snap_ts(read))
    return None if ts is None else float(now) - ts


def _default_gate():
    from .. import venue_request_gate as grt
    return grt.gate_state()


def evidence_order(symbols, *, focus=None, evidence=None) -> list:
    """PURE. The order the probe spends its bounded reads in: members whose
    same-book evidence is not SUPPORTED first (fewest comparable samples
    first, so the reads spread as the counts grow), then by focus tier (held
    paper and actual positions before candidates and discovery), then the
    universe's own order. SUPPORTED members come last -- they keep accruing
    only with what is left."""
    focus = focus or {}
    evidence = evidence or {}

    def key(item):
        i, s = item
        ev = evidence.get(s) or {}
        det = ev.get("detail") or {}
        rank = (focus.get(s) or {}).get("tier_rank") or 99
        return (1 if ev.get("status") == "SUPPORTED" else 0,
                int(det.get("comparable") or 0), int(rank), i)
    return [s for _i, s in sorted(enumerate(symbols or ()), key=key)]


async def probe_same_book(pool, store, symbols, *, process_id,
                          current=None, retail_read=None, focus=None,
                          limit=None, gate=None, clock=time.time,
                          max_reads=None, quiet_s=None,
                          evidence=None) -> dict:
    """One same-book sample per symbol (read-only) ->
    institutional_same_book_probe. The blocking reads run off the loop.
    `focus` ({slug: focus-universe member}) stamps each row's tier and why;
    with a focus universe the bound is its own (fu.MAX_MEMBERS)."""
    bound = limit if limit is not None else (
        fu.MAX_MEMBERS if focus is not None else MAX_INSTRUMENTS)
    syms = list(symbols or ())[:bound]
    focus = focus or {}
    try:
        retail = await xstore.retail_rows(pool, syms) if syms else {}
    except Exception:                                          # noqa: BLE001
        retail = {}
    # SAME-BOOK EVIDENCE CERTIFIES ONE USE: the held-mark book
    # (paper_market_data.institutional_held_book is its only consumer), so
    # the probe reads the stream exactly as that use does
    cur = current or istream.current_for_held_mark
    read = retail_read or samebook.retail_book_read
    gate_fn = gate or _default_gate
    budget = int(SAME_BOOK_MAX_READS_PER_PASS if max_reads is None
                 else max_reads)
    quiet = float(SAME_BOOK_MIN_QUIET_S if quiet_s is None else quiet_s)
    deferred: dict = {}
    spent = {"reads": 0}
    if evidence is not None:
        # evidence-first order (fewest comparable samples first)
        syms = evidence_order(syms, focus=focus, evidence=evidence)
    elif syms:
        # no evidence read: rotate so a bounded pass reaches every member
        k = _PROBE_CURSOR["i"] % len(syms)
        syms = syms[k:] + syms[:k]

    def defer(reason):
        deferred[reason] = deferred.get(reason, 0) + 1
        raise ReadDeferred(reason)

    def paced_read(slug):
        try:
            g = gate_fn() or {}
        except Exception:                                     # noqa: BLE001
            g = {}
        if g.get("blocking"):
            defer(D_HOLD)
        if spent["reads"] >= budget:
            defer(D_BUDGET)
        now = clock()
        s0 = cur(slug, now=now) or {}
        if not s0.get("ok"):
            defer(D_STREAM)
        age = _venue_age(s0, now)
        if age is not None and age < quiet:
            defer(D_RECENT)
        spent["reads"] += 1
        return read(slug)

    def run_all():
        out = []
        for i, s in enumerate(syms):
            if spent["reads"] >= budget and "stop_at" not in spent:
                spent["stop_at"] = i
            row = samebook.sample(
                s, record=(store.instrument(s) or {}).get("record"),
                retail_row=retail.get((s, "yes")), books_current=cur,
                retail_read=paced_read, focus=focus.get(s))
            why = str(row.get("verdict_reason") or "")
            if why.startswith("PROBE_RAISED:ReadDeferred"):
                continue
            out.append(row)
        return out

    rows = await asyncio.to_thread(run_all)
    if syms and evidence is None:
        # the next pass starts where this pass's read budget ran out
        _PROBE_CURSOR["i"] = (_PROBE_CURSOR["i"] + spent.get("stop_at", 0)) \
            % len(syms)
    by: dict = {}
    for r in rows:
        by[r["verdict"]] = by.get(r["verdict"], 0) + 1
    written = await samebook.persist(pool, rows, process_id=process_id,
                                     service=SERVICE)
    return {"samples": len(rows), "written": written, "by": by,
            "retail_reads": spent["reads"], "read_budget": budget,
            "deferred": deferred}


def loop_sleep_s(stats: dict) -> float:
    """The worker loop's pause: the bootstrap backoff only when there is
    neither a focus set nor focus-universe work (refdata just read, or
    EXACT members still unsubscribed by the stream)."""
    st = stats.get("status")
    if st not in ("no_focus_set", "sweep_failed"):
        return SWEEP_S
    boot = stats.get("universeBootstrap") or {}
    if boot.get("read") or stats.get("universeSubscribed") or \
            stats.get("universePending"):
        return SWEEP_S
    return BOOTSTRAP_BACKOFF_S


def bootstrap_universe(client, store, slugs, attempts, *, now=None,
                       limit=UNIVERSE_BOOTSTRAPS_PER_SWEEP) -> dict:
    """Refdata for focus-universe members not yet held: at most `limit` per
    call (paced), each through `bootstrap_instrument` (the allow-listed
    `instruments` read). `attempts` {slug: epoch} bounds re-asks: a held
    record is refreshed after UNIVERSE_REFDATA_REFRESH_S, an unlisted slug
    re-asked after UNIVERSE_RETRY_UNLISTED_S. Runs OFF the event loop."""
    from ..venue_pace import pace

    at = float(now if now is not None else time.time())
    out = {"read": 0, "listed": 0, "notListed": 0}
    for s in slugs or ():
        if out["read"] >= limit:
            break
        if not fu._SLUG.match(str(s or "")):
            continue
        inst = store.instrument(s)
        tried = attempts.get(s)
        if inst is not None and inst.get("record") is not None:
            if tried is None:
                attempts[s] = at      # already held (the sweep read it)
                continue
            if at - tried < UNIVERSE_REFDATA_REFRESH_S:
                continue
        elif tried is not None and at - tried < UNIVERSE_RETRY_UNLISTED_S:
            continue
        attempts[s] = at
        pace(READ_PACING_S)
        boot = bootstrap_instrument(client, s)
        out["read"] += 1
        if boot["record"] is None and inst is not None and \
                inst.get("record") is not None:
            continue                  # a failed refresh keeps the held record
        store.put_instrument(s, boot["record"], price_scale=boot["priceScale"],
                             qty_scale=boot["qtyScale"])
        out["listed" if boot["record"] is not None else "notListed"] += 1
    return out


async def compute_universe(pool, store, discovery, attempts) -> dict:
    """The focus universe (read-only) with every member's exact identity or
    UNAVAILABLE reason attached from the refdata this process holds."""
    u = await fu.compute(pool, discovery=list(discovery or ()))
    return await identify_universe(pool, store, u, attempts)


async def identify_universe(pool, store, universe, attempts) -> dict:
    slugs = [m["retail_slug"] for m in universe.get("members") or ()]
    try:
        retail = await xstore.retail_rows(pool, slugs) if slugs else {}
    except Exception:                                          # noqa: BLE001
        retail = {}
    return fu.attach(
        universe, record_for=lambda s: (store.instrument(s) or {}).get(
            "record"), retail=retail,
        attempted=lambda s: s in attempts or store.instrument(s) is not None)


def subscribe_universe(store, universe) -> list:
    """EXACT members only: their own refdata scales, then subscribe. An
    UNAVAILABLE member is never subscribed, compared or priced."""
    exact = fu.exact_symbols(universe)
    for s in exact:
        inst = store.instrument(s)
        if inst and inst.get("record") is not None:
            istream.set_instrument(s, inst["record"])
    if exact:
        istream.want(exact)
    return exact


async def run() -> None:
    if _off("INSTITUTIONAL_MD"):
        log.info("institutional_md: off by switch")
        return

    seen = pmx.presence()
    boot = {"service": SERVICE,
            "marketDataMechanism": pmx.MARKET_DATA_MECHANISM,
            "streamTarget": pmx.STREAM_TARGET,
            "evidenceEnvironment": pmx.EVIDENCE_ENVIRONMENT,
            "orderSubmissionImplementation":
                pmx.ORDER_SUBMISSION_IMPLEMENTATION,
            "freshnessLimitS": ib.FRESHNESS_LIMIT_S,
            "presence": seen,
            # WHICH IDENTITY THIS PROCESS WOULD READ MARKET DATA WITH: type,
            # presence, identifier fingerprints and distinctness from the
            # retail execution and funded keys. Never a value.
            "marketDataIdentity": mdi.inventory()}
    log.info("institutional_md: %s", boot)

    if seen["verdict"]:
        # PRESENCE ONLY. Which names are missing, never anything about
        # what the present ones contain.
        while True:
            await heartbeat(SERVICE, "credential_missing", boot)
            await asyncio.sleep(AUTH_BACKOFF_S)

    pool = await get_pool()
    client = pmx.Institutional()
    store = ib.STORE

    # §1/§3: prove the worker's own credential once, at boot, and write
    # the verdict where COMMAND and the logs can both read it.
    verdict = await asyncio.to_thread(pmx.verify, client, "")
    boot = dict(boot, auth=verdict)
    log.info("institutional_md: auth %s scopes=%s readL2=%s",
             verdict.get("AUTH_STATUS"), verdict.get("TOKEN_SCOPES"),
             verdict.get("READ_L2_PERMISSION"))
    await heartbeat(SERVICE, str(verdict.get("AUTH_STATUS")), boot)

    # THE STREAMING PATH, off unless INSTITUTIONAL_MD_STREAM=on, refused by
    # the identity guard for an execution key. grpcio/protobuf are locked and
    # the market-data stubs are vendored (sportsassets/vendor/pmx_proto);
    # TRANSPORT_UNAVAILABLE only if they fail to import. Never raises.
    # The evidence recorder is attached BEFORE the stream starts, so the
    # first connect is recorded too. Only when the stream is enabled here.
    recorder = (sevid.install(istream.BOOKS, service=SERVICE)
                if istream.enabled() else None)
    stream_start = istream.start_default()
    log.info("institutional_md: stream %s (%s)", stream_start.get("state"),
             stream_start.get("why"))

    last_evidence = 0.0
    # 0.0 rather than time.monotonic(): the FIRST pass through the loop
    # must bind, not wait a minute to start. Until a market is bound its
    # decisions are stamped NOT_IDENTIFIED, and a minute of those is a
    # minute of prospective signals that can never execute.
    last_identity = 0.0
    last_stream_evidence = time.monotonic()
    last_same_book = time.monotonic()
    last_universe = 0.0
    universe: dict = {}
    u_attempts: dict = {}
    beats = 0
    while True:
        started = time.monotonic()
        stats = {"service": SERVICE}
        try:
            symbols = [s["symbol"] for s in await xstore.focus_set(
                pool, size=MAX_INSTRUMENTS)]
        except Exception as exc:                               # noqa: BLE001
            symbols = []
            stats["focusError"] = "%s: %s" % (type(exc).__name__, exc)

        if not symbols:
            stats["status"] = "no_focus_set"
        else:
            # The stream holds the same focus set, priced by the same refdata.
            for sym in symbols[:MAX_INSTRUMENTS]:
                inst = store.instrument(sym)
                if inst and inst.get("record") is not None:
                    istream.set_instrument(sym, inst["record"])
            istream.want(symbols[:MAX_INSTRUMENTS])
            try:
                stats.update(await asyncio.to_thread(
                    sweep_once, client, store, symbols))
                stats["status"] = "ok"
            except Exception as exc:                           # noqa: BLE001
                stats["status"] = "sweep_failed"
                stats["sweepError"] = "%s: %s" % (type(exc).__name__, exc)
                log.warning("institutional_md: sweep failed", exc_info=True)

            if time.monotonic() - last_evidence >= EVIDENCE_EVERY_S:
                last_evidence = time.monotonic()
                try:
                    stats["trailRows"] = await persist_trail(
                        pool, store, symbols)
                except Exception:                              # noqa: BLE001
                    log.debug("institutional_md: trail failed", exc_info=True)

            # §2: THE BINDING IS RE-RESOLVED ON THE SAME SLOW CADENCE as
            # the evidence trail, not every sweep. A binding is a fact
            # about two venues' KEYS, which change on the timescale of a
            # relisting; re-deriving it thirty times a minute would be
            # thirty identical shas and thirty no-op writes. It re-runs
            # anyway (rather than once at boot) so a newly bootstrapped
            # focus market is bound within a minute of joining.
            if time.monotonic() - last_identity >= IDENTITY_EVERY_S:
                last_identity = time.monotonic()
                try:
                    stats["identity"] = await resolve_identities(
                        pool, store, symbols)
                except Exception as exc:                       # noqa: BLE001
                    stats["identityError"] = "%s: %s" % (
                        type(exc).__name__, exc)
                    log.warning("institutional_md: identity resolve failed",
                                exc_info=True)

        # THE FOCUS UNIVERSE (stream enabled only): recomputed each minute,
        # refdata bootstrapped a few per sweep, identities re-attached, EXACT
        # members subscribed. Never on the decision path; never an order.
        if recorder is not None:
            try:
                if time.monotonic() - last_universe >= FOCUS_UNIVERSE_EVERY_S:
                    last_universe = time.monotonic()
                    universe = await compute_universe(pool, store, symbols,
                                                      u_attempts)
                    stats["focusUniverse"] = fu.summary(universe)
                    stats["focusUniverseWritten"] = await fu.persist(
                        pool, universe, process_id=recorder.process_id,
                        service=SERVICE, wanted=istream.BOOKS.wanted())
                slugs = [m["retail_slug"]
                         for m in universe.get("members") or ()]
                boot = await asyncio.to_thread(
                    bootstrap_universe, client, store, slugs, u_attempts)
                if boot["read"]:
                    universe = await identify_universe(pool, store, universe,
                                                       u_attempts)
                stats["universeBootstrap"] = boot
                stats["universeSubscribed"] = len(
                    subscribe_universe(store, universe))
                # members whose refdata is not held yet (still to bootstrap)
                stats["universePending"] = sum(
                    1 for s_ in slugs if (store.instrument(s_) or {}).get(
                        "record") is None and s_ not in u_attempts)
            except Exception as exc:                           # noqa: BLE001
                stats["focusUniverseError"] = type(exc).__name__

        # THE STREAM'S EVIDENCE: recorded whether or not a focus set exists
        # (the process row says what the stream is doing); probed only for
        # symbols the stream holds. Never on the decision path.
        if recorder is not None and \
                time.monotonic() - last_stream_evidence >= \
                STREAM_EVIDENCE_EVERY_S:
            last_stream_evidence = time.monotonic()
            try:
                stats["streamEvidence"] = await record_stream_evidence(
                    pool, recorder, store)
            except Exception as exc:                           # noqa: BLE001
                stats["streamEvidenceError"] = type(exc).__name__
        # THE PROBE SAMPLES THE FOCUS UNIVERSE (every member, in priority
        # order; an UNAVAILABLE member is recorded NOT_COMPARABLE with its
        # identity refusal and reads nothing), or -- before the first
        # universe exists -- the focus set as before.
        probe_members = universe.get("members") or []
        probe_syms = ([m["retail_slug"] for m in probe_members]
                      if probe_members else symbols)
        if recorder is not None and probe_syms and \
                not _off("INSTITUTIONAL_SAME_BOOK_PROBE") and \
                time.monotonic() - last_same_book >= SAME_BOOK_EVERY_S:
            last_same_book = time.monotonic()
            focus = ({m["retail_slug"]: dict(
                m, universe_id=universe.get("universe_id"))
                for m in probe_members} if probe_members else None)
            try:
                try:
                    evidence = await samebook.same_book_by_symbol(
                        pool, probe_syms)
                except Exception:                              # noqa: BLE001
                    evidence = None
                stats["sameBook"] = await probe_same_book(
                    pool, store, probe_syms, process_id=recorder.process_id,
                    focus=focus, evidence=evidence)
            except Exception as exc:                           # noqa: BLE001
                stats["sameBookError"] = type(exc).__name__

        venue_ms = stats.pop("venueMs", []) or []
        if venue_ms:
            ordered = sorted(venue_ms)
            stats["venueMsP50"] = ordered[len(ordered) // 2]
            stats["venueMsMax"] = ordered[-1]
        stats.update(store.snapshot())
        stats["symbols"] = len(symbols)
        stats["sweepS"] = round(time.monotonic() - started, 3)
        stats["authStatus"] = verdict.get("AUTH_STATUS")
        stats["marketDataMechanism"] = pmx.MARKET_DATA_MECHANISM
        stats["orderSubmissionImplementation"] = \
            pmx.ORDER_SUBMISSION_IMPLEMENTATION
        stats["stream"] = istream.digest()

        # THE HEARTBEAT IS NOT ON THE HOT PATH, so it is throttled:
        # §7 keeps reporting out of the decision path, and a beat every
        # sweep would be thirty writes a minute saying the same thing.
        beats += 1
        if beats % 15 == 1:
            try:
                await heartbeat(SERVICE, str(stats.get("status") or "ok"),
                                stats)
            except Exception as exc:                           # noqa: BLE001
                log.error("institutional_md: heartbeat failed: %s: %s",
                          type(exc).__name__, exc)
        # THE RESTART BOOTSTRAP: an empty focus set (no sticky markets after
        # a restart) is no reason to back off while the focus universe still
        # has refdata to bootstrap or EXACT members to subscribe -- backing
        # off 30 s per two bootstraps left the stream IDLE_NO_SYMBOLS_REQUESTED
        # for minutes after every worker restart
        await asyncio.sleep(loop_sleep_s(stats))
