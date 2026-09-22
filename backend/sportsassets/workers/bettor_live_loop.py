"""The complete operating loop, driven by stream updates.

    live observation -> normalization -> decision -> shadow execution
    -> inventory -> outcome -> reconciled accounting

DRIVEN BY UPDATES, NOT BY A CLOCK. The book callback marks a slug dirty
and the loop decides on dirty slugs. A timer would decide on books that
have not moved and miss books that moved twice between ticks.

THREE CLOCKS, ALL PERSISTED. Every record carries the venue's
`transactTime`, our receipt time, and the decision time -- measured per
observation, never once per pass.

REFUSALS ARE RECORDS. Every refusal and its reason is written beside
every eligible action. An engine that stores only its trades cannot be
evaluated, and this one refuses nearly everything.

EVERY FILL IS SIMULATED. A market trading at our quoted price does NOT
establish that our order filled -- we hold no order and queue position
is unobservable. Trades are counted as TOUCHES.

--------------------------------------------------------------------
WHAT THE SECOND ACTIVATION REVIEW FOUND, AND WHERE EACH FIX LIVES
--------------------------------------------------------------------

STORAGE THAT DELETES ITSELF. `/var/tmp/bettor` passes every fsync and
every atomic replace, and a REDEPLOY throws the whole container away.
Durability now lives in `bettor_live_store`: the default backend is
POSTGRES on the database the target service already holds, and a file
backend is REFUSED unless the path is declared to be a mounted disk.
Recovery is O(cursors), not O(journal) -- the journal is evidence, the
cursor table is position, and a month of evidence costs nothing to
restart against.

BLOCKING I/O ON THE SHARED EVENT LOOP. The previous version's docstring
claimed journal writes ran in a thread. They did not: `_record` fsynced
inline inside `decide_slug`, which `main()` calls directly. Records now
go to a bounded OUTBOX and are flushed in batches; the flush is the
only thing that touches storage, and a full outbox is COUNTED rather
than silently dropped.

A SETTLEMENT SCHEDULER THAT COULD NOT MAKE PROGRESS. Reading "the 25
oldest observed" means a permanently pending contract is permanently
the oldest. Scheduling state is now per contract and persisted:
never-attempted first, then due-by-backoff, terminal contracts retired
and never read again.

A SELECTION PATH NOBODY HAD RUN. The harness supplied 16 books that the
universe rule rejects, so it demonstrated the decision chain with
inputs handed to it, not discovery and selection. `main()` now accepts
an injected client and stream factory -- production passes neither --
so `scripts/bettor_startup_path.py` can drive paginated discovery ->
selection -> subscription -> decisions through this entry point.

Earlier review, fixes retained: durable-by-default rather than
memory-only; bounded rings; trades drained once rather than drained and
called back; `_dirty` behind a lock; `stream.stop()` in a finally;
discovery refreshed with expired subscriptions pruned; a failed
discovery refusing to start rather than running over an empty universe.

--------------------------------------------------------------------
WHAT THE FIRST LIVE RUN FOUND, 2026-09-21
--------------------------------------------------------------------

A UNIVERSE THAT WAS EMPTY BY CONSTRUCTION. `_discover` fed the venue's
LISTING straight into the selection rule. `MarketDetail` carries no
quote, no state and no traded-share counter, so all 3,000 listed
markets were excluded as ONE_SIDED_BOOK -- a reason about the market,
for a fact about the payload. Discovery now ENRICHES first, through
`bettor_universe_probe`, and the rule only ever sees a row that has a
real bid, ask, state and `sharesTraded` in it. COVERAGE is reported
beside the verdict so "we have not read it yet" can never again be
counted as "the book is one-sided".

A FIVE-SECOND RETRY LOOP. `workers/all.py:run_forever` restarts a
clean return after five seconds, forever, so refusing to start cost
about 73 rounds of six listing pages and one advisory-locked DDL
transaction each. `main()` now HOLDS before it returns, on an
escalating schedule, and a run that starts resets it.

A KILL SWITCH THAT A RESTART DID NOT DELIVER. `BETTOR_LIVE_LOOP=off`
was set and acknowledged, the service was demonstrably restarted
(`server_restarted` 22:43:40.131647Z), and the restarted process still
ran discovery. Stage 1 then established the other half: a NEW DEPLOY
does reload the environment -- 00:31:22.805Z, where the new process
read `off`, logged its refusal and returned. The precise finding is
therefore about the delivery mechanism, not the variable: a restart
API call does not reload the environment on this service; a deploy
does.

That is why the environment variable is a POOR OPERATIONAL CONTROL and
not a useless one. Stopping through it costs a deploy, which is minutes
and a new process; stopping through the database costs one UPDATE and
takes effect within `CONTROL_EVERY_S` in the process already running.

Stop: `bettor_live_observation` in `ingestion_state` (authoritative).
The `BETTOR_LIVE_LOOP=off` environment variable is kept as a cheap
pre-check that is only as prompt as the next deploy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from .. import bettor_decision_engine as de
from .. import bettor_live_control as ctl
from .. import bettor_live_store as store_mod
from .. import bettor_market_stream as ms
from .. import bettor_observation_adapter as oa
from .. import bettor_settlement_ingest as si
from .. import bettor_shadow_loop as sl
from .. import bettor_universe as uni
from .. import bettor_universe_probe as probe_mod

log = logging.getLogger(__name__)

LOOP_VERSION = "BETTOR_LIVE_LOOP_V3"
PROSPECTIVE = "PROSPECTIVE_SHADOW"
KILL_ENV = "BETTOR_LIVE_LOOP"

MAX_SOURCE_AGE_S = 10.0
MAX_RECEIPT_AGE_S = 5.0
POLL_S = 0.2

# In-memory rings, for REPORTING ONLY. The store holds the record.
RECORD_RING = 2000
TOUCH_RING = 5000

DISCOVERY_EVERY_S = 900.0

# HOW MANY MARKETS ARE WATCHED WHEN THE STRATEGY ADMITS NONE.
#
# Small and bounded on purpose. This is a transport experiment, not a
# collection programme: enough subscriptions to show that the socket
# connects, that frames arrive, that their timestamps and payloads
# parse, and that a stop closes them -- and few enough that an
# observation run cannot quietly become continuous collection.
#
# Zero disables the decoupling entirely and restores the old behaviour
# of watching only what the rule admits.
OBSERVE_MAX = 8
OBSERVE_MAX_ENV = "BETTOR_LIVE_OBSERVE_MAX"
# A CEILING ON THE OVERRIDE, so "watch a few for transport evidence"
# cannot be turned into a collection programme by one environment
# variable. The distinct-market reservation bounds what is ACQUIRED;
# this bounds what is SUBSCRIBED.
OBSERVE_MAX_CEIL = 40

# HOW LONG THE SHUTDOWN WAITS FOR THE SOCKET THREAD TO ACTUALLY CLOSE.
#
# The inner send loop checks `_stop` at most every 0.25 s and then
# awaits `ws.close()`, so this is generous by two orders of magnitude.
# It is bounded because a socket that will not close must not hold the
# supervisor's shutdown open -- the receipt then records
# `closed: false`, which is a finding, not a hang.
STREAM_CLOSE_WAIT_S = 20.0

SETTLEMENT_EVERY_S = 600.0
SETTLEMENT_BATCH = 25
LEDGER_EVERY_S = 60.0

# THE SUPERVISOR RESTARTS A CLEAN RETURN AFTER FIVE SECONDS, FOREVER.
# `workers/all.py:run_forever` is a `while True`, so on 2026-09-21 a
# `main()` that returned EMPTY_UNIVERSE immediately became a five-second
# cycle: about 73 rounds of six listing pages and one advisory-locked
# DDL transaction each, roughly 440 venue requests, for nothing.
#
# That loop is shared by twenty-five workers and is not this release's
# to change, so the bound lives HERE: a `main()` that cannot start
# waits before returning, and the wait escalates. The supervisor's five
# seconds are then added to a minute or more, not to zero.
#
# THE FIRST RUNG IS FIVE MINUTES, NOT ONE, BECAUSE A REFUSAL IS NOT
# FREE. An EMPTY_UNIVERSE return has already spent its discovery: six
# listing pages and up to 1,680 BBO reads, about 21 s of wall clock at
# eight in flight. Holding 60 s after that is ~1,686 reads per 81 s --
# 21 req/s, which is a hot loop wearing a backoff. Holding 300 s makes
# the same round 5.2 req/s and the next one 1.8, and it is still
# shorter than the 900 s a HEALTHY loop waits between refreshes.
#
# Reset to the first rung whenever a run actually starts, so a
# transient venue outage does not leave the loop on the long rung for
# the rest of the day.
# ── TWO HOLDS, FOR TWO DIFFERENT THINGS ──────────────────────────────
#
# A DELIBERATELY STOPPED WORKER IS NOT AN ERRING ONE. Both used to share
# one escalating schedule, and on 2026-09-22 that cost a probe: the loop
# had been idle for hours, so the rung had climbed to 3600 s, and when
# an allowance was armed at 07:38:21Z with a 1800 s deadline the next
# wake was not due until ~08:34 -- AFTER the deadline it was meant to
# run inside. The probe could only be started by restarting the service,
# which is not a control.
#
# So a refusal that never touched the venue POLLS instead. It costs one
# database read, it never escalates, and it is short enough that an
# operator's `obs-run` is picked up without a deploy or a restart.
IDLE_POLL_S = 30.0

# And a refusal that DID reach the venue still backs off, escalating, so
# a failing acquisition cannot hammer it. The 21 September incident is
# the reason this rung starts at five minutes rather than five seconds.
NOSTART_BACKOFF_S = (300.0, 900.0, 1800.0, 3600.0)
_nostart_rung = 0

# The reasons that are a STOP, not a failure. Every one of them is
# decided before a client is constructed, so each costs exactly one
# database read and no venue request.
_CONTROL_REASONS = frozenset({
    "KILL_SWITCH",
    ctl.W_STOPPED, ctl.W_ABSENT, ctl.W_MALFORMED, ctl.W_UNREADABLE,
    ctl.B_EXPIRED, ctl.B_EXHAUSTED, ctl.B_UNREADABLE,
})


def is_control_reason(why: str) -> bool:
    """One place decides which hold a reason gets."""
    return why in _CONTROL_REASONS


def _reset_backoff() -> None:
    global _nostart_rung
    _nostart_rung = 0


async def _backoff(why: str, *, sleep=None, at_least: float = 0.0) -> float:
    """Hold a non-starting `main()` before it returns to the supervisor.

    A CONTROL REASON POLLS; anything else BACKS OFF. A control reason
    also leaves the acquisition rung untouched in both directions: being
    switched off is not progress against a failing venue, and it is not
    a failure to be punished for either.

    `at_least` is a floor the caller can raise -- used to carry a
    venue's own `Retry-After` through, so a suspension is never
    shortened by our schedule being the smaller number.

    `sleep` is injectable so the tests can assert the SCHEDULE without
    spending it.
    """
    global _nostart_rung
    if is_control_reason(why):
        delay, kind = IDLE_POLL_S, "control poll"
    else:
        delay = NOSTART_BACKOFF_S[
            min(_nostart_rung, len(NOSTART_BACKOFF_S) - 1)]
        _nostart_rung = min(_nostart_rung + 1, len(NOSTART_BACKOFF_S) - 1)
        kind = "acquisition backoff"
    if at_least and at_least > delay:
        log.warning("bettor_live_loop: the venue asked for %.0fs; our "
                    "schedule would have returned in %.0fs, so the "
                    "venue's number wins", at_least, delay)
        delay, kind = at_least, "venue Retry-After"
    log.info("bettor_live_loop: not starting (%s); holding %.0fs (%s) "
             "before the supervisor's own restart delay",
             why, delay, kind)
    await (sleep or asyncio.sleep)(delay)
    return delay


def effective_config() -> dict:
    """What THIS PROCESS will actually use, read from its own
    environment at the moment of asking.

    Not what a service dashboard says, not what a deploy intended --
    what the interpreter holding this loop can see. The difference is
    the whole lesson of 2026-09-21.
    """
    pace = probe_mod.configured_pace()
    return {
        "loop": LOOP_VERSION,
        "probe": probe_mod.PROBE_VERSION,
        "max_rps": pace["max_rps"],
        "concurrency": pace["concurrency"],
        "above_demonstrated_envelope":
            pace["above_demonstrated_envelope"],
        "probe_batch": int(probe_mod._env_float(
            probe_mod.BATCH_ENV, probe_mod.PROBE_BATCH)),
        "rounds_at_start": probe_mod.PROBE_ROUNDS_AT_START,
        "frame_capture_n": _int_env(ms.FRAMES_ENV, ms.FRAMES_DEFAULT,
                                    ms.FRAMES_MAX),
        "max_contracts": os.environ.get("BETTOR_LIVE_MAX_CONTRACTS", "0"),
        "kill_env": os.environ.get(KILL_ENV, "(unset -> on)"),
        "control_every_s": ctl.CONTROL_EVERY_S,
        "suspend_above_s": probe_mod.PROBE_SUSPEND_ABOVE_S,
        "discovery_pages": os.environ.get(
            "BETTOR_LIVE_DISCOVERY_PAGES",
            str(probe_mod.LISTING_MAX_PAGES)),
        # THE LISTING'S OWN BOUNDS, counted apart from enrichment so a
        # listing retry storm cannot eat the enrichment allowance.
        "listing_max_retries": probe_mod.LISTING_MAX_RETRIES,
        "listing_timeout_s": probe_mod.LISTING_TIMEOUT_S,
        # ONE RESERVED UNIT PER OUTBOUND LISTING REQUEST. Stated here
        # because the previous build reserved one unit for up to six
        # requests, and an operator reading this line is entitled to
        # know which build they are looking at.
        "listing_reservation_unit": "ONE_PER_OUTBOUND_REQUEST",
        "listing_max_outbound_requests": (
            int(os.environ.get("BETTOR_LIVE_DISCOVERY_PAGES",
                               probe_mod.LISTING_MAX_PAGES))
            * (probe_mod.LISTING_MAX_RETRIES + 1)),
        "listing_paced": True,
        "listing_stop_after_consecutive_429":
            probe_mod.LISTING_MAX_CONSECUTIVE_429,
        # THE OBSERVATION SUBSET -- watched regardless of the frozen
        # rule's verdict, so the transport can be verified when the
        # strategy admits nothing. Not a trading permission.
        "observe_max": _int_env(OBSERVE_MAX_ENV, OBSERVE_MAX,
                                OBSERVE_MAX_CEIL),
        "observe_is_not_trading_admission": True,
        "sample_seed": probe_mod.sample_seed(),
        "sample_order_version": probe_mod.SAMPLE_ORDER_VERSION,
        "probe_max_retries": probe_mod.PROBE_MAX_RETRIES,
        "probe_timeout_s": probe_mod.PROBE_TIMEOUT_S,
        # THE LIFETIME BUDGET, reported here as the CODE'S DEFAULTS and
        # named as such. The authoritative numbers live in the
        # `bettor_live_probe_state` row and are whatever `obs-arm`
        # wrote; a refusal at the control returns before the budget is
        # ever read, so this line cannot claim to have read the row.
        # It says what this build would arm to, which is the thing a
        # reader wants from an idle process.
        "budget_key": ctl.BUDGET_KEY,
        "budget_max_distinct_default": ctl.PROBE_MAX_DISTINCT,
        "budget_max_bbo_attempts_default": ctl.PROBE_MAX_BBO_ATTEMPTS,
        "budget_max_listing_attempts_default":
            ctl.PROBE_MAX_LISTING_ATTEMPTS,
        "budget_deadline_s_default": ctl.PROBE_DEADLINE_S,
        "budget_authority": "the ingestion_state row, not this line",
        # THE PROPERTY THE CEILINGS ACTUALLY REST ON, stated in the
        # refusal log so it can be read off a stopped process rather
        # than taken on trust.
        "allowance_reserved_before_dispatch": True,
        "allowance_refund_paths": 0,
    }


def _int_env(name, default, ceiling):
    try:
        return max(0, min(int(os.environ.get(name, default) or 0), ceiling))
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return str(os.environ.get(KILL_ENV, "on")).strip().lower() != "off"


def _now():
    return datetime.now(timezone.utc)


class LiveLoop:
    """Stream in, decisions out, accounting reconciled. Never trades."""

    def __init__(self, stream, *, store=None, fees=None,
                 opening_cash: float = 0.0, max_contracts: float = 0.0,
                 leg_of=None, boot_id: str | None = None, admission=None):
        # ONE IDENTITY PER PROCESS BOOT, stamped on every record and on
        # the ledger. Without it "the worker restarted and kept its
        # records" cannot be checked: a stream epoch increments on a
        # RECONNECT, which is not a restart, and a row count that rose
        # says nothing about which process wrote the new rows.
        self.boot_id = boot_id or uuid.uuid4().hex[:16]
        self.stream = stream
        self.store = store or store_mod.MemoryStore()
        self.fees = fees or de.Fees.published_pmus(
            os.environ.get("BETTOR_FEE_DATE")
            or _now().strftime("%Y-%m-%d"))
        self.max_contracts = max_contracts
        self.leg_of = dict(leg_of or {})
        # WHY THIS MARKET IS BEING WATCHED, stamped on every record it
        # produces. A market watched for transport evidence that the
        # frozen rule refused must be distinguishable in the journal
        # from one the rule admitted, FOREVER and per record -- not by
        # cross-referencing a startup log line that a later restart
        # overwrites. Absent means the old behaviour: admitted.
        self.admission = dict(admission or {})

        self.shadow = sl.ShadowLoop(opening_cash=opening_cash,
                                    fees=self.fees,
                                    venue="polymarket-us",
                                    account_class="institutional")
        # _dirty CROSSES THREADS: the socket thread adds, the worker
        # takes.
        self._lock = threading.Lock()
        self._dirty: set = set()

        self.records: deque = deque(maxlen=RECORD_RING)
        self.touches: deque = deque(maxlen=TOUCH_RING)
        self.counters: dict = {}

        # THE OUTBOX. Records wait here for a batched flush instead of
        # fsyncing inline on the shared event loop. Bounded, and
        # overflow is counted by name.
        self._outbox: deque = deque()
        self._cursor_dirty: set = set()

        # ONE CURSOR PER MARKET: the dedup position AND the settlement
        # schedule. Bounded, persisted, and the only thing recovery
        # needs to read.
        self.cursors: dict = {}

        self.event_at: dict = {}

        self.started_at: str | None = None
        self.stopped_at: str | None = None
        self.records_written = 0
        self.persist_failures = 0
        # DURABILITY IS NOT A CONSTANT. "At most two seconds lost"
        # assumes every flush succeeds. Under a database outage the
        # uncommitted interval grows until the outbox overflows, and
        # these are the numbers that say so.
        self.flush_failures = 0
        self.records_dropped = 0
        self.first_drop_at: str | None = None
        self.last_drop_at: str | None = None
        self.last_commit_at: float | None = None
        self.last_flush_error: str | None = None
        self.recovered: dict | None = None
        self.store_started: dict | None = None
        self.discovery: dict | None = None
        # The last read of the database stop control, so `report()` can
        # say on whose authority the loop is still observing.
        self.control: dict | None = None
        self.settlement_runs: list = []
        self.outcomes: dict | None = None
        self.prunes: list = []

    # ── counters ─────────────────────────────────────────────────────

    def bump(self, key, n=1):
        self.counters[key] = self.counters.get(key, 0) + n

    def on_book(self, slug: str, rec: dict) -> None:
        """Stream-thread callback. Cheap, non-blocking, LOCKED."""
        with self._lock:
            self._dirty.add(slug)

    def on_trade(self, rec: dict) -> None:
        """A trade is a TOUCH at most, never a fill.

        THE ONLY PATH. `build()` does not install this as a stream
        callback: it was installed AND drained, so one trade counted
        twice.
        """
        self.bump("trades_seen")
        self.touches.append(rec)

    # ── cursors ──────────────────────────────────────────────────────

    def _cursor(self, slug: str) -> dict:
        c = self.cursors.get(slug)
        if c is None:
            c = store_mod.new_cursor(time.time())
            ev = self.event_at.get(slug)
            if ev is not None:
                # THE EVENT'S OWN TIMING, from the venue listing, so a
                # settlement check can be scheduled shortly after the
                # event rather than on whatever rung of a blind backoff
                # the contract happens to be on.
                c["event_at"] = ev
            self.cursors[slug] = c
            # A NEW CURSOR IS NEW DURABLE STATE, not a cached copy of
            # something the store already has, so it is dirty until it
            # is flushed and cannot be evicted before that.
            self._cursor_dirty.add(slug)
            self._evict_cursors()
        return c

    def _evict_cursors(self) -> None:
        """The in-memory map is a CACHE. The settlement queue is read
        from the STORE, so evicting here loses no future work -- but an
        UNFLUSHED cursor is an update, not a cache entry, so it is
        never evicted."""
        ev = store_mod.evict_to(self.cursors,
                                store_mod.CURSOR_MAX_IN_MEMORY,
                                dirty=self._cursor_dirty)
        if ev["evicted"]:
            self.bump("cursor_evicted", ev["evicted"])
            if ev["evicted_unresolved"]:
                # A CACHE MISS, NOT A LOSS: the row is still in the
                # store and comes back when it is due. Counted because
                # a persistently full cache means every settlement pass
                # pays a store read.
                self.bump("cursor_cache_miss_unresolved",
                          ev["evicted_unresolved"])
        if ev["kept_dirty"]:
            self.bump("cursor_kept_unflushed", ev["kept_dirty"])

    # ── the loop ─────────────────────────────────────────────────────

    def take_dirty(self) -> list:
        with self._lock:
            out = list(self._dirty)
            self._dirty.clear()
        return out

    def drain(self) -> list:
        out = []
        for slug in self.take_dirty():
            rec = self.decide_slug(slug)
            if rec is not None:
                out.append(rec)
        return out

    def decide_slug(self, slug: str) -> dict | None:
        decided_at = _now().isoformat()
        at = self.stream.book_at(slug, decided_at=decided_at,
                                 max_source_age_s=MAX_SOURCE_AGE_S,
                                 max_receipt_age_s=MAX_RECEIPT_AGE_S)
        self.bump("books_examined")
        base = {"loop": LOOP_VERSION, "kind": "DECISION",
                # FROM THE TRANSPORT. A replaying transport says
                # REPLAY_DECISION; only a live socket says
                # PROSPECTIVE_SHADOW. This was hard-coded, so a replay
                # would have been journalled as live observation.
                "evidence_class": getattr(self.stream, "evidence_class",
                                          PROSPECTIVE),
                "market_id": slug, "decided_at": decided_at,
                "source_ts": at.get("source_ts"),
                "received_at": at.get("received_at"),
                "source_age_s": at.get("source_age_s"),
                "receipt_age_s": at.get("receipt_age_s"),
                "venue_state": at.get("venue_state"),
                "stream_epoch": at.get("epoch"),
                # CARRIED ON EVERY RECORD so the journal is a time
                # series of the venue's traded-volume counter. The
                # research capture cannot be differenced -- 617 of its
                # 620 markets were observed exactly once in 24 hours --
                # and that is why no day's volume can be derived from
                # it. This loop observes the same market on every
                # update, so its journal can.
                "stats_shares_traded": at.get("stats_shares_traded"),
                "fee_schedule": self.fees.source,
                "fee_status": self.fees.status}

        # WHY THIS MARKET IS WATCHED, ON THE RECORD ITSELF.
        #
        # OBSERVATION_ONLY means the frozen rule REFUSED this market
        # and it is being watched anyway, for transport and price
        # evidence. `universe_rule_reason` carries that refusal
        # verbatim. Neither field relaxes anything: the rule is applied
        # unchanged, the refusal is durable, and an observation
        # subscription is not trading admission.
        adm = self.admission.get(slug)
        if adm:
            base.update(adm)
            self.bump("watched:%s" % adm.get("observation_basis"))
            if adm.get("universe_rule_reason"):
                self.bump("watched_refused:%s"
                          % adm["universe_rule_reason"])

        if not at["eligible"]:
            self.bump("ineligible")
            self.bump("ineligible:%s" % at["reason"])
            rec = dict(base, status="INELIGIBLE", reasons=[at["reason"]])
            self._record(rec)
            return rec

        src = at.get("source_ts")
        cur = self._cursor(slug)
        prev = cur.get("last_source_ts")
        if prev is not None and src is not None and src <= prev:
            self.bump("duplicate_observation_refused")
            return None
        cur["last_source_ts"] = src
        cur["last_decided_at"] = decided_at
        cur["last_seen_at"] = time.time()
        self._cursor_dirty.add(slug)

        leg = self.leg_of.get(slug)
        row = ms.to_observation(slug, at, outcome_leg=leg)
        norm = oa.normalize(row, venue="polymarket-us",
                            account_class="institutional",
                            fee_source=self.fees.source)
        if norm.status != oa.ACCEPTED:
            self.bump("rejected")
            for r in norm.reasons:
                self.bump("reject:%s" % r)
            rec = dict(base, status="REJECTED",
                       observation_id=row["observation_id"],
                       reasons=list(norm.reasons))
            self._record(rec)
            return rec

        step = self.shadow.step(oa.to_book(norm), evidence_class=sl.REPLAYED,
                                max_contracts=self.max_contracts)
        d = step.get("engine") or {}
        selected = step.get("decision") or d.get("selected")
        self.bump("decided")
        self.bump("action:%s" % selected)
        blockers = sorted({c["blocker"] for c in d.get("candidates", [])
                           if c.get("blocker")})
        for b in blockers:
            self.bump("blocker:%s" % b)
        if step.get("execution"):
            self.bump("executed")

        rec = dict(base, status="DECIDED",
                   observation_id=row["observation_id"],
                   outcome_leg=leg, selected=selected,
                   size_contracts=d.get("size_contracts", 0.0),
                   reason=d.get("reason"), blockers=blockers,
                   top_of_book={"bid": norm.bid, "ask": norm.ask,
                                "bid_size": norm.bid_size,
                                "ask_size": norm.ask_size,
                                "cumulative_ask_size":
                                    norm.cumulative_ask_size},
                   executed=bool(step.get("execution")),
                   cash_after=step.get("cash_after"))
        self._record(rec)
        return rec

    # ── durability ───────────────────────────────────────────────────

    def _record(self, rec: dict) -> None:
        """Ring for reporting, outbox for the store. NO I/O HERE.

        This used to open the journal and fsync it, on the event loop
        shared with eighteen sibling worker loops, once per decision.
        """
        rec.setdefault("boot_id", self.boot_id)
        rec.setdefault("record_key", store_mod.record_key(rec))
        rec.setdefault("enqueued_at", time.time())
        self.records.append(rec)
        if len(self._outbox) >= store_mod.OUTBOX_MAX:
            # COUNTED, NEVER SILENT. A full outbox means the store is
            # not keeping up, and a record dropped without a name is a
            # gap nobody can see. It also marks every measurement
            # window that overlaps it INCOMPLETE.
            self._outbox.popleft()
            self._note_drop(1)
        self._outbox.append(rec)

    def _note_drop(self, n: int) -> None:
        self.records_dropped += n
        self.bump("record_dropped_outbox_full", n)
        at = _now().isoformat()
        if self.first_drop_at is None:
            self.first_drop_at = at
        self.last_drop_at = at

    async def flush(self) -> dict:
        """Batch the outbox and the dirty cursors into the store."""
        if not self._outbox and not self._cursor_dirty:
            return {"records": 0, "cursors": 0, "failures": 0}
        recs = list(self._outbox)
        self._outbox.clear()
        dirty = {s: self.cursors[s] for s in self._cursor_dirty
                 if s in self.cursors}
        self._cursor_dirty.clear()
        out = await self.store.flush(recs, dirty)
        if out.get("failures"):
            self.persist_failures += int(out["failures"])
            self.flush_failures += 1
            self.last_flush_error = out.get("error")
            self.bump("persist_failed")
            if out.get("error"):
                self.bump("persist_failed:%s" % out["error"])
            # PUT THEM BACK, KEYS AND ALL. A failed flush must not lose
            # the batch. The records carry a CONTENT KEY, so if the
            # commit actually succeeded and only its acknowledgment was
            # lost, the retry is a no-op rather than a second copy.
            dropped = 0
            for r in reversed(recs):
                if len(self._outbox) < store_mod.OUTBOX_MAX:
                    self._outbox.appendleft(r)
                else:
                    dropped += 1
            if dropped:
                self._note_drop(dropped)
            self._cursor_dirty.update(dirty)
        else:
            self.records_written += int(out.get("records") or 0)
            self.last_commit_at = time.time()
        return out

    def durability_report(self) -> dict:
        """WHAT IS ACTUALLY GUARANTEED RIGHT NOW, not in the happy case.

        "At most one flush interval" holds only while flushes succeed.
        During a database outage the uncommitted interval grows until
        the outbox overflows, and then records are DROPPED. Every
        measurement window that overlaps a drop is INCOMPLETE and says
        so, because a rate computed over a window with an unrecorded
        gap is wrong in a direction nobody can see.
        """
        now = time.time()
        oldest = None
        if self._outbox:
            first = self._outbox[0].get("enqueued_at")
            if first is not None:
                oldest = round(now - first, 3)
        return {
            "boot_id": self.boot_id,
            "backend": getattr(self.store, "backend", None),
            "durable_across": list(getattr(self.store, "durable_across",
                                           ()) or ()),
            "store_started": self.store_started,
            "records_written": self.records_written,
            "uncommitted_records": len(self._outbox),
            "oldest_uncommitted_age_s": oldest,
            "uncommitted_cursors": len(self._cursor_dirty),
            "flush_failures": self.flush_failures,
            "persist_failures": self.persist_failures,
            "last_flush_error": self.last_flush_error,
            "seconds_since_last_commit": (
                None if self.last_commit_at is None
                else round(now - self.last_commit_at, 3)),
            "records_dropped": self.records_dropped,
            "windows_incomplete": self.records_dropped > 0,
            "incomplete_from": self.first_drop_at,
            "incomplete_to": self.last_drop_at,
            "guarantee": (
                "at most one flush interval (%.1f s) of decisions is "
                "uncommitted WHILE FLUSHES SUCCEED. A failed flush "
                "keeps its batch and the uncommitted interval grows; "
                "beyond %d records the oldest are DROPPED and every "
                "window overlapping a drop is marked incomplete"
                % (store_mod.FLUSH_EVERY_S, store_mod.OUTBOX_MAX)),
            "on_sigterm": (
                "MEASURED, not assumed: `workers/all.py` installs no "
                "SIGTERM handler, and Python's default disposition "
                "terminates the process WITHOUT running finally blocks "
                "(verified: exit 143, the finally never ran). A Render "
                "restart or redeploy arrives as SIGTERM, so the final "
                "flush does NOT run and up to one flush interval is "
                "lost. `oldest_uncommitted_age_s` in the last report "
                "before a restart is the size of that loss. The finally "
                "covers CANCELLATION -- a supervisor stopping the loop "
                "-- and the kill-switch exit, not SIGTERM"),
            "retry_is_idempotent": (
                "every record carries a content-derived key and the "
                "insert is ON CONFLICT DO NOTHING, so a commit whose "
                "acknowledgment was lost cannot be counted twice"),
            "recovered_at_start": self.recovered,
            "prunes": list(self.prunes),
            "bounds": {"records_ring": RECORD_RING,
                       "touches_ring": TOUCH_RING,
                       "outbox_max": store_mod.OUTBOX_MAX,
                       "cursors_cached": len(self.cursors),
                       "cursor_cache_max": store_mod.CURSOR_MAX_IN_MEMORY},
        }

    async def save_ledger(self) -> bool:
        ok = await self.store.save_ledger(self.shadow.snapshot())
        if not ok:
            self.persist_failures += 1
            self.bump("ledger_persist_failed")
        return ok

    async def recover(self) -> dict:
        """Rebuild position and ledger from the store. IN THE WORKER.

        READS THE CURSOR TABLE, NOT THE JOURNAL. Restart time is
        bounded by the number of markets, not by how long the worker
        has been recording.
        """
        t0 = time.time()
        loaded = await self.store.load()
        out = {"backend": loaded.get("backend"),
               "cursors_recovered": 0, "ledger_restored": False,
               "journal_rows": loaded.get("journal_rows"),
               "store_load_seconds": loaded.get("load_seconds")}
        if loaded.get("error"):
            out["load_error"] = loaded["error"]
            self.bump("recover_load_failed")
        for slug, c in (loaded.get("cursors") or {}).items():
            self.cursors[slug] = {k: c.get(k)
                                  for k in store_mod.CURSOR_FIELDS}
        store_mod.evict_to(self.cursors, store_mod.CURSOR_MAX_IN_MEMORY,
                           dirty=self._cursor_dirty)
        out["cursors_recovered"] = len(self.cursors)
        out["settlements_completed_at_recovery"] = sum(
            1 for c in self.cursors.values()
            if c.get("settle_status") in store_mod.TERMINAL_STATUSES)
        out["settlements_outstanding_at_recovery"] = sum(
            1 for c in self.cursors.values()
            if store_mod.has_outstanding_obligation(c))

        snap = loaded.get("ledger")
        if snap:
            try:
                self.shadow = sl.ShadowLoop.restore(
                    snap, fees=self.fees, venue="polymarket-us",
                    account_class="institutional")
                out["ledger_restored"] = True
                out["cash"] = self.shadow.ledger.cash
            except Exception as exc:  # noqa: BLE001 -- a corrupt ledger
                out["ledger_error"] = type(exc).__name__   # must not stop
                self.bump("ledger_restore_failed")         # the loop
        out["recover_seconds"] = round(time.time() - t0, 4)
        self.recovered = out
        return out

    async def prune(self) -> dict:
        out = await self.store.prune(
            max_rows=int(os.environ.get("BETTOR_LIVE_JOURNAL_MAX_ROWS",
                                        store_mod.JOURNAL_MAX_ROWS)),
            cursor_retention_days=int(
                os.environ.get("BETTOR_LIVE_CURSOR_RETENTION_DAYS",
                               store_mod.CURSOR_RETENTION_DAYS)))
        out["at"] = _now().isoformat()
        self.prunes.append(out)
        del self.prunes[:-10]
        return out

    # ── settlement ───────────────────────────────────────────────────

    async def settlement_due(self, *, limit=None, now=None) -> dict:
        """THE QUEUE LIVES IN THE STORE, NOT IN THE CACHE.

        Reading it from `self.cursors` would mean a market stopped
        being checked because the process was busy enough to evict it,
        which is not a reason. The store answers, and the rows it
        returns are merged back into the cache so the pass can update
        them.
        """
        t = now if now is not None else time.time()
        got = await self.store.due_for_settlement(
            now=t, limit=limit or SETTLEMENT_BATCH)
        if got.get("error"):
            self.bump("settlement_queue_read_failed")
        for slug, c in (got.get("cursors") or {}).items():
            if slug in self._cursor_dirty:
                # THE CACHE IS NEWER. A cursor with unflushed updates
                # must not be overwritten by the store's older copy, or
                # this pass's reschedule is undone by the next pass's
                # queue read and the contract is attempted forever at
                # the same attempt count.
                self.bump("settlement_cursor_kept_unflushed")
                continue
            merged = dict(store_mod.new_cursor(t))
            merged.update({k: v for k, v in c.items() if v is not None})
            existing = self.cursors.get(slug)
            if existing is not None:
                # The cache is newer for observation fields; the store
                # is authoritative for the schedule.
                merged["last_source_ts"] = existing.get("last_source_ts")
                merged["last_decided_at"] = existing.get("last_decided_at")
                merged["last_seen_at"] = existing.get("last_seen_at")
            self.cursors[slug] = merged
        self._evict_cursors()
        return got

    async def ingest_settlements(self, *, client=None, limit=None,
                                 now=None) -> dict:
        """Bounded settlement reads, SCHEDULED so they make progress.

        THREE RULES THIS ENFORCES:

        1. Only an AUTHORITATIVE resolution completes collection. A
           derived outcome is preserved separately and the contract
           stays queued for the venue's own answer.
        2. A PENDING answer is a SUCCESSFUL read. It never advances the
           failure count and can never retire a contract; a market may
           legitimately stay open far longer than any attempt count.
        3. A run of FAILED reads escalates to a visible status and
           keeps its place in the queue, at the back.
        """
        t = now if now is not None else time.time()
        q = await self.settlement_due(limit=limit, now=t)
        slugs = q.get("slugs") or []
        if not slugs:
            return {"counts": {}, "contracts_read": 0,
                    "outstanding": q.get("outstanding"),
                    "skipped": ("nothing due: %s contracts still owe an "
                                "authoritative outcome"
                                % q.get("outstanding"))}
        pairs = [("%s@%s" % (s, (self.cursors.get(s) or {}).get(
            "last_source_ts") or "NO_TS"), s) for s in slugs]
        out = await si.ingest(pairs, client=client,
                              writer=self._settlement_writer)

        retired = escalated = derived = 0
        for rec in out.get("detail") or []:
            slug = rec.get("slug")
            c = self.cursors.get(slug)
            if c is None:
                continue
            nxt = store_mod.settle_transition(
                rec.get("status"), c.get("settle_attempts") or 0, now=t,
                failures=c.get("settle_failures") or 0,
                event_at=c.get("event_at"),
                derived_at=c.get("settle_derived_at"),
                derived_outcome=c.get("settle_derived_outcome"),
                outcome=rec.get("outcome"))
            retired += 1 if nxt.pop("retired") else 0
            if nxt.get("settle_status") == store_mod.SETTLE_READ_ESCALATED:
                escalated += 1
            if nxt.get("settle_status") == store_mod.SETTLE_RESOLVED_DERIVED:
                derived += 1
            c.update(nxt)
            self._cursor_dirty.add(slug)

        out["at"] = _now().isoformat()
        out["contracts_read"] = len(pairs)
        out["completed_authoritatively"] = retired
        out["awaiting_authoritative"] = derived
        out["read_escalated"] = escalated
        out["outstanding_before_pass"] = q.get("outstanding")
        self.settlement_runs.append(
            {"at": out["at"], "counts": out["counts"],
             "reconciled": out["reconciled"],
             "contracts_read": out["contracts_read"],
             "completed_authoritatively": retired,
             "awaiting_authoritative": derived,
             "read_escalated": escalated,
             "unreadable_reasons": out["unreadable_reasons"]})
        del self.settlement_runs[:-20]
        for k, v in out["counts"].items():
            self.bump("settlement:%s" % k, v)
        self.bump("settlement_completed_authoritatively", retired)
        if escalated:
            self.bump("settlement_read_escalated", escalated)
        return out

    async def _settlement_writer(self, row: dict) -> None:
        """Settlements land in the journal beside the decisions.

        The production accounting writer (`bettor_state_store.
        record_settlement`) is deliberately NOT used: a decision-only
        worker must not write an accounting table, and this journal is
        the evidence it is allowed to keep.
        """
        self._record({"loop": LOOP_VERSION, "kind": "SETTLEMENT",
                      "at": _now().isoformat(), "settlement": row,
                      "market_id": row.get("market_id") or row.get("slug")})

    # ── reporting ────────────────────────────────────────────────────

    def freshness_distribution(self) -> dict:
        ages = sorted(r["source_age_s"] for r in self.records
                      if r.get("source_age_s") is not None)
        if not ages:
            return {"n": 0}

        def q(p):
            return round(ages[min(len(ages) - 1, int(p * len(ages)))], 4)

        return {"n": len(ages), "min": round(ages[0], 4), "p10": q(0.10),
                "median": q(0.50), "p90": q(0.90), "max": round(ages[-1], 4),
                "within_bound": sum(1 for a in ages if a <= MAX_SOURCE_AGE_S),
                "bound_s": MAX_SOURCE_AGE_S,
                "window": "the last %d records held in memory" % len(ages)}

    def settlement_schedule_report(self) -> dict:
        """Over the CACHE, and labelled as such -- the authoritative
        counts come from `store.outcome_report()`, which the loop logs
        beside this one."""
        by: dict = {}
        for c in self.cursors.values():
            k = c.get("settle_status") or "NEVER_ATTEMPTED"
            by[k] = by.get(k, 0) + 1
        return {
            "scope": "the in-memory cursor cache, not the whole store",
            "contracts_cached": len(self.cursors),
            "by_status": by,
            "completed_authoritatively": by.get(
                store_mod.SETTLE_RESOLVED, 0),
            "awaiting_authoritative": by.get(
                store_mod.SETTLE_RESOLVED_DERIVED, 0),
            "read_escalated": by.get(store_mod.SETTLE_READ_ESCALATED, 0),
            "outstanding": sum(1 for c in self.cursors.values()
                               if store_mod.has_outstanding_obligation(c)),
            "policy": store_mod.describe()["settlement_schedule"],
        }

    def report(self) -> dict:
        led = self.shadow.ledger
        rc = led.reconciles()
        return {
            "loop": LOOP_VERSION,
            "boot_id": self.boot_id,
            "started_at": self.started_at, "stopped_at": self.stopped_at,
            "runtime_s": _elapsed(self.started_at, self.stopped_at),
            "stream": self.stream.stats() if self.stream else None,
            # WHAT THE WIRE ACTUALLY SENT. Empty unless stage-1 frame
            # capture is on; NOT_ESTABLISHED until it has run, because
            # replaying HTTP bodies says nothing about the socket.
            "frames": (self.stream.frame_capture_report()
                       if hasattr(self.stream, "frame_capture_report")
                       else None),
            "discovery": self.discovery,
            # TWO INDEPENDENT OUTCOMES, REPORTED APART.
            #
            # `transport` is about the socket: did it connect, did
            # frames arrive, did they parse, did a stop close it. It is
            # answerable whether the strategy admits zero markets or
            # many, which is the whole point of the observation subset.
            #
            # `strategy` is the frozen rule's verdict, unchanged. A run
            # in which every watched market was refused is a COMPLETE
            # run with a NEGATIVE strategy result, not a failed one.
            "watching": {
                "watched": len(self.admission) or None,
                "by_basis": {k[8:]: v for k, v in self.counters.items()
                             if k.startswith("watched:")},
                "refusals_observed": {
                    k[16:]: v for k, v in self.counters.items()
                    if k.startswith("watched_refused:")},
                "rule": uni.UNIVERSE_VERSION,
                "rule_applied": "UNCHANGED",
                "observation_is_not_trading_admission": True,
            },
            "control": self.control,
            "counters": dict(self.counters),
            "decisions_in_ring": len(self.records),
            "durability": self.durability_report(),
            "by_action": {k[7:]: v for k, v in self.counters.items()
                          if k.startswith("action:")},
            "ineligible_by_reason": {k[11:]: v
                                     for k, v in self.counters.items()
                                     if k.startswith("ineligible:")},
            "rejected_by_reason": {k[7:]: v for k, v in self.counters.items()
                                   if k.startswith("reject:")},
            "blockers": {k[8:]: v for k, v in self.counters.items()
                         if k.startswith("blocker:")},
            "settlement": {"runs": list(self.settlement_runs),
                           "schedule": self.settlement_schedule_report(),
                           "outcomes": self.outcomes},
            "freshness": self.freshness_distribution(),
            "accounting": {
                "ledger": rc, "fees_paid": led.fees_paid,
                "realized_pnl": led.realized_pnl,
                "deployed": self.shadow.deployed,
                "combined_exposure": self.shadow.combined_exposure,
                "worst_case_loss": self.shadow.worst_case_loss,
                "open_positions": len([p for p in
                                       self.shadow.positions.values()
                                       if not p.flat]),
                "live_quotes": len([q for q in self.shadow.quotes.values()
                                    if q["state"] in sl.LIVE_QUOTE_STATES]),
            },
            "touches_not_fills": {
                "trades_observed": self.counters.get("trades_seen", 0),
                "in_ring": len(self.touches),
                "note": ("a trade at our quoted price is a TOUCH. We hold "
                         "no order, so it establishes nothing about a "
                         "fill of ours"),
            },
            "orders_submitted": 0,
        }


def _elapsed(a, b):
    if not a or not b:
        return None
    return round((datetime.fromisoformat(b)
                  - datetime.fromisoformat(a)).total_seconds(), 3)


def build(key_id: str, secret_key: str, *, slugs, leg_of=None, store=None,
          opening_cash: float = 0.0, stream_factory=None,
          boot_id: str | None = None, admission=None):
    """Wire a stream to a loop. Subscribes; does not start the socket.

    `on_trade` IS NOT INSTALLED as a stream callback. It used to be,
    while the caller ALSO drained trades into the same handler, so one
    trade was counted twice.

    `stream_factory` exists so the startup-path runner can drive this
    exact function with a replaying transport. Production passes None
    and gets `ms.MarketStream`.
    """
    loop = LiveLoop(None, store=store, leg_of=leg_of, admission=admission,
                    opening_cash=opening_cash, boot_id=boot_id)
    if store is not None and getattr(store, "boot_id", None) is None:
        store.boot_id = loop.boot_id
    factory = stream_factory or ms.MarketStream
    stream = factory(key_id, secret_key, on_book=loop.on_book)
    loop.stream = stream
    stream.subscribe(list(slugs)[:uni.MAX_MARKETS])
    return loop, stream


def describe() -> dict:
    return {
        "loop": LOOP_VERSION,
        "chain": ("live observation -> normalization -> decision -> "
                  "shadow execution -> inventory -> outcome -> "
                  "reconciled accounting"),
        "driven_by": "book updates, not a timer",
        "submits_orders": False,
        "writes_accounting": False,
        "writes_database": ("its own three tables only: %s"
                            % ", ".join(store_mod.OWN_TABLES)),
        "default_max_contracts": 0.0,
        "max_source_age_s": MAX_SOURCE_AGE_S,
        "max_receipt_age_s": MAX_RECEIPT_AGE_S,
        "durable": store_mod.describe(),
        "outcome_collection_completed_by": (
            "an AUTHORITATIVE settlement read, and nothing else. A "
            "derived outcome is preserved separately and the contract "
            "stays scheduled"),
        "bounded": {"records": RECORD_RING, "touches": TOUCH_RING,
                    "outbox": store_mod.OUTBOX_MAX,
                    "cursors": store_mod.CURSOR_MAX_IN_MEMORY,
                    "recovery": "O(cursors); the journal is never "
                                "replayed"},
        "trade_delivery": ("drained, never also a callback. Installing "
                           "both counted every trade twice"),
        "fills_are_simulated": (
            "a market trading at our quoted price does NOT establish "
            "that our order filled. Trades are counted as TOUCHES"),
    }


# ── the worker entry point ───────────────────────────────────────────

async def _list_candidates(client=None, *, reserve=None, sleep=None,
                           pacer=None) -> dict:
    """The venue's listing, PAGINATED, reduced to CANDIDATES.

    Identity only. The listing cannot answer a single question the
    selection rule asks, so nothing here judges a market -- see
    `bettor_universe_probe.candidates_from_listing`.

    ONE RESERVED UNIT AND ONE PACED SLOT PER PAGE.

    THE DEFECT THIS REPLACES, measured on probe 3c413436
    (2026-09-22). The reservation was taken ONCE, around a
    `while pages < max_pages` loop that issued up to SIX
    `markets.list` calls back to back. Two consequences, both
    recorded in the probe's own numbers:

      * The budget undercounted outbound listing requests by up to
        6x. `listing_attempts_reserved: 1` was written while the
        venue received six requests.
      * Those six requests went through NO PACER AT ALL, so the run
        opened with a six-request burst and was nevertheless reported
        as running at 0.25 req/s. The rate the venue actually saw at
        the start of that probe was never 0.25 req/s, and the 429
        analysis was built on a figure that was a setting rather than
        an observation.

    THE SDK ADDS NOTHING TO THE COUNT -- verified, not assumed; see
    `probe_mod.LISTING_PAGE_IS_ONE_REQUEST` for the three checks
    (no pagination, no retry transport, no redirect following). One
    `markets.list` call is one outbound HTTP GET, so one reservation
    per call is one reservation per request.
    """
    from .. import pmus

    max_pages = int(os.environ.get("BETTOR_LIVE_DISCOVERY_PAGES",
                                   str(probe_mod.LISTING_MAX_PAGES)))
    page_size = int(os.environ.get("BETTOR_LIVE_DISCOVERY_PAGE_SIZE", "500"))
    _sleep = sleep or asyncio.sleep
    pace = probe_mod.configured_pace()
    pacer = pacer if pacer is not None else probe_mod.Pacer(
        pace["max_rps"], sleep=_sleep, name="listing")

    def _one_page(offset: int):
        """EXACTLY ONE outbound request. No loop lives in here any more."""
        client_ = client or pmus._get_client()
        resp = client_.markets.list({"active": True, "closed": False,
                                     "limit": page_size, "offset": offset})
        return list((resp or {}).get("markets") or [])

    rows: list = []
    attempts = 0          # RESERVED UNITS SPENT == OUTBOUND REQUESTS
    pages = 0             # pages successfully read
    truncated = False
    consecutive_429 = 0
    rate_limited = 0
    acct: dict = {"rate_limit_events": []}
    suspended_for = 0.0
    fatal = None          # set only when page 1 never arrives
    stopped_why = None

    for page in range(max_pages):
        offset = page * page_size
        got = None
        last = None
        for attempt in range(probe_mod.LISTING_MAX_RETRIES + 1):
            # RESERVED BEFORE THE REQUEST, AND BEFORE THE PACER -- a
            # request that cannot be paid for never even waits its
            # turn. Retries cost their own unit; the units do not come
            # back.
            if reserve is not None:
                r = await reserve("listing", None)
                if not (r or {}).get("ok"):
                    why = (r or {}).get("why") or "RESERVATION_REFUSED"
                    log.error("bettor_live_loop: listing page %d attempt "
                              "%d not funded (%s); not issuing the "
                              "request", page + 1, attempt + 1, why)
                    stopped_why = "listing allowance refused: %s" % why
                    if page == 0 and not rows:
                        fatal = why
                    break
            attempts += 1
            throttled, asked = False, None
            await pacer.acquire()
            try:
                got = await asyncio.wait_for(
                    asyncio.to_thread(_one_page, offset),
                    timeout=probe_mod.LISTING_TIMEOUT_S)
                last = None
                break
            except asyncio.TimeoutError:
                last = "TimeoutError(%.0fs)" % probe_mod.LISTING_TIMEOUT_S
            except Exception as exc:  # noqa: BLE001 -- named, never swallowed
                last = type(exc).__name__
                throttled = probe_mod._status_of(exc) == 429
                asked = probe_mod._retry_after_s(exc) if throttled else None
            finally:
                # PAIRED WITH `acquire`, ALWAYS. An unreleased slot
                # makes `max_concurrent_inflight` a fiction, and that
                # figure is one of the few things this probe exists to
                # measure.
                pacer.release()
            if throttled:
                rate_limited += 1
                consecutive_429 += 1
                probe_mod._note_429(acct, pacer, "LISTING p%d" % (page + 1),
                                    asked, where="listing")
                if asked is not None and \
                        asked > probe_mod.PROBE_SUSPEND_ABOVE_S:
                    # THE SERVER ASKED FOR LONGER THAN THIS DISCOVERY
                    # SHOULD LIVE. Never shortened.
                    log.error("bettor_live_loop: listing HTTP 429 with "
                              "Retry-After %.0fs, above the %.0fs "
                              "threshold; ABANDONING the listing",
                              asked, probe_mod.PROBE_SUSPEND_ABOVE_S)
                    suspended_for = max(suspended_for, asked)
                    stopped_why = "listing suspended by Retry-After %.0fs" \
                                  % asked
                    break
                if consecutive_429 >= probe_mod.LISTING_MAX_CONSECUTIVE_429:
                    # STOP ON REPEATED THROTTLING, IMMEDIATELY -- not
                    # after spending the rest of this page's retries.
                    # A limiter that has refused this many times
                    # running is not asking to be asked again, and each
                    # further attempt is another reserved unit spent to
                    # collect the same answer.
                    log.error("bettor_live_loop: %d consecutive throttled "
                              "listing requests; STOPPING the listing",
                              consecutive_429)
                    break
                hold = asked
                if hold is None:
                    hold = probe_mod.LISTING_RETRY_BACKOFF_S[
                        min(attempt,
                            len(probe_mod.LISTING_RETRY_BACKOFF_S) - 1)]
                # HONOUR THE SERVER'S BACKOFF, VERBATIM, and slow every
                # later page with it rather than only this attempt -- a
                # 429 is about the process's rate, not about this page.
                pacer.penalise(hold)
                log.warning("bettor_live_loop: listing page %d HTTP 429; "
                            "holding %.0fs (Retry-After %s)", page + 1,
                            hold, "absent" if asked is None
                            else "%.0fs" % asked)
                await _sleep(hold)
                continue
            if attempt < probe_mod.LISTING_MAX_RETRIES:
                hold = probe_mod.LISTING_RETRY_BACKOFF_S[
                    min(attempt, len(probe_mod.LISTING_RETRY_BACKOFF_S) - 1)]
                log.warning("bettor_live_loop: listing page %d attempt %d "
                            "failed (%s); retrying in %.0fs",
                            page + 1, attempt + 1, last, hold)
                await _sleep(hold)

        if got is None:
            # THIS PAGE NEVER ARRIVED. Page 1 failing is a failed
            # listing; a later page failing is a SHORTER listing, and
            # the rows already paid for are kept rather than discarded.
            if consecutive_429 >= probe_mod.LISTING_MAX_CONSECUTIVE_429:
                # STOP ON REPEATED THROTTLING. A limiter that refuses
                # this many times running is not asking to be asked
                # again, and walking the remaining pages would spend
                # reserved units to collect identical refusals.
                stopped_why = stopped_why or (
                    "stopped after %d consecutive throttled listing "
                    "requests" % consecutive_429)
            if page == 0 and not rows:
                fatal = fatal or last or "LISTING_PAGE_1_FAILED"
                break
            stopped_why = stopped_why or ("listing stopped after page %d "
                                          "(%s)" % (pages, last))
            break
        consecutive_429 = 0
        rows.extend(got)
        pages += 1
        if len(got) < page_size:
            break
    else:
        truncated = True

    telemetry = pacer.telemetry()
    if fatal is not None:
        return {"ok": False, "error": fatal,
                "listing_attempts": attempts,
                "listing_pages_requested": attempts,
                "rate_limited": rate_limited,
                "rate_limit_events": acct["rate_limit_events"],
                "request_telemetry": telemetry,
                "suspended_for_s": suspended_for or None,
                "why": "market listing failed after %d reserved requests"
                       % attempts}

    cands = probe_mod.candidates_from_listing(rows)
    if truncated:
        log.warning("bettor_live_loop: listing hit the %d-page bound; "
                    "the candidate set is a PREFIX of the venue",
                    max_pages)
    if stopped_why:
        log.warning("bettor_live_loop: listing incomplete -- %s; %d rows "
                    "over %d pages are kept", stopped_why, len(rows), pages)
    return {"ok": True, "candidates": cands, "pages_read": pages,
            # ONE UNIT PER OUTBOUND REQUEST. `listing_attempts` and
            # `listing_pages_requested` are now the same number BY
            # CONSTRUCTION, and a divergence between them and the
            # budget row's `listing_attempts_reserved` is a defect.
            "listing_attempts": attempts,
            "listing_pages_requested": attempts,
            "rate_limited": rate_limited,
            "rate_limit_events": acct["rate_limit_events"],
            "request_telemetry": telemetry,
            "suspended_for_s": suspended_for or None,
            "listing_incomplete": stopped_why,
            "rows_listed": len(rows), "page_size": page_size,
            "listing_truncated_at_page_bound": truncated}


def observation_subset(rows, candidates, *, limit: int,
                       admitted=None) -> dict:
    """A small, bounded, DETERMINISTIC set of markets to WATCH --
    chosen without reference to whether the strategy would trade them.

    THE CIRCULAR DEPENDENCY THIS REMOVES. Probe 3c413436 subscribed
    only to markets the frozen rule ADMITTED. The rule admitted none,
    so nothing was subscribed, so no WebSocket frame was ever received,
    so the transport remained unverified -- and it would have remained
    unverified for exactly as long as the strategy kept saying no. The
    two questions are independent and are now answered independently.

    AN OBSERVATION SUBSCRIPTION IS NOT TRADING ADMISSION. The frozen
    rule is applied UNCHANGED to every one of these markets and its
    verdict is recorded on every observation (`observation_basis`,
    `universe_rule_reason` on each journalled record). A market watched
    here that the rule refused stays refused; watching it produces
    evidence about the transport and about prices, and produces no
    trading permission of any kind. The worker holds no order path at
    all -- it imports no order function -- and runs with
    MAX_CONTRACTS=0 besides.

    DETERMINISTIC, AND CHOSEN BEFORE ANY ECONOMIC RESULT IS SEEN. The
    order is the seeded candidate permutation fixed at listing time.
    Nothing here re-reads, re-searches or re-draws until something
    passes: it is one pass over what was already enriched, and the only
    non-economic preference applied is that a two-sided book is
    preferred to a one-sided one, because a book with both sides
    produces more transport evidence per frame.

    ADMITTED MARKETS ARE NOT COUNTED AGAINST THE LIMIT. If the rule
    admits markets they are watched as well; this subset exists so that
    the run has something to watch WHEN IT DOES NOT.
    """
    admitted = set(admitted or ())
    order = {c["slug"]: i for i, c in enumerate(candidates or [])}
    seen, pool = set(), []
    for row in rows or []:
        slug = row.get("slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        a = uni.assess(row)
        # VALID AND OPEN, and nothing else. A market the venue does not
        # call open, or whose book will not parse, cannot produce a
        # meaningful frame -- that is a transport fact, not an economic
        # one. Every economic exclusion (spread, price, volume,
        # one-sided) is left in the pool deliberately.
        if a["reason"] in (uni.R_NOT_OPEN, uni.R_UNPARSABLE):
            continue
        pool.append({
            "slug": slug,
            "two_sided": a["reason"] != uni.R_ONE_SIDED,
            "order_index": order.get(slug, 10 ** 9),
            "strategy_admitted": bool(a["included"]),
            "universe_rule_reason": a["reason"],
            "universe": a["universe"],
            "outcome_leg": a.get("outcome_leg"),
            "spread_ticks": a.get("spread_ticks"),
            "shares_traded": a.get("shares_traded"),
        })
    pool.sort(key=lambda p: (0 if p["two_sided"] else 1,
                             p["order_index"], p["slug"]))
    chosen, extra = [], []
    for p in pool:
        if p["slug"] in admitted:
            continue
        if len(extra) >= max(0, int(limit)):
            break
        extra.append(p)
    chosen = [p for p in pool if p["slug"] in admitted] + extra
    refused = [p for p in extra if not p["strategy_admitted"]]
    reasons: dict = {}
    for p in refused:
        r = p["universe_rule_reason"] or "UNKNOWN"
        reasons[r] = reasons.get(r, 0) + 1
    return {
        "limit": int(limit),
        "eligible_for_observation": len(pool),
        "observation_only_slugs": [p["slug"] for p in extra],
        "strategy_admitted_slugs": sorted(admitted),
        "slugs": [p["slug"] for p in chosen],
        "detail": chosen,
        "observed_despite_refusal": len(refused),
        "refusal_reasons_observed": reasons,
        "rule": uni.UNIVERSE_VERSION,
        "rule_applied": "UNCHANGED -- the refusal is recorded, not waived",
        "selection": ("seeded candidate order, two-sided first; fixed "
                      "before any economic result was examined"),
        "not_trading_admission": True,
    }


async def _discover(client=None, *, candidates=None, offset: int = 0,
                    rounds: int = probe_mod.PROBE_ROUNDS_AT_START,
                    batch: int | None = None, should_stop=None,
                    max_distinct: int | None = None, reserve=None,
                    sleep=None, observe_max: int | None = None) -> dict:
    """Listing -> ENRICHMENT -> the frozen selection rule.

    THE RULE RUNS ONLY AFTER ENRICHMENT. It used to run on listing
    rows, which carry no quote, no state and no traded-share counter,
    so all 3,000 of them were excluded as ONE_SIDED_BOOK and the
    universe was empty by construction. A missing field in a listing is
    not a one-sided market, and the rule never sees an unenriched row
    from here.

    A FAILED DISCOVERY IS NOT AN EMPTY UNIVERSE, and neither is an
    UNPROBED one: `coverage` says how much of the candidate set was
    actually read and is reported apart from `excluded_by_reason`,
    which is the rule's verdict on what we did read.
    """
    # THE VENUE CLIENT IS RESOLVED ONCE, HERE.
    #
    # `main()` is called by the supervisor WITH NO ARGUMENTS, so
    # `client` is None, and `probe._read_one_sync` reads
    # `getattr(client, "markets", None)` -- which on None yields
    # "client.markets.bbo is absent" for every market. `_list_candidates`
    # resolved its own client and so the LISTING worked, which is what
    # made this invisible: discovery would have spent the entire
    # allowance, 40 distinct slots and their attempts, on reads that
    # never left the process. Found by running the no-argument path
    # against a real database rather than an injected one.
    if client is None:
        from .. import pmus
        client = pmus._get_client()

    # ONE PACER FOR THE WHOLE DISCOVERY -- the listing and every
    # enrichment round. Before this the listing had no pacer and the
    # rounds each built their own, so "the process's request rate" was
    # not a quantity that existed anywhere.
    pace = probe_mod.configured_pace()
    pacer = probe_mod.Pacer(pace["max_rps"], sleep=sleep, name="discovery")

    ordering = None
    if candidates is None:
        listed = await _list_candidates(client=client, reserve=reserve,
                                        sleep=sleep, pacer=pacer)
        if not listed.get("ok"):
            return {"ok": False, "error": listed.get("error"),
                    "why": listed.get("why"),
                    # A LISTING SUSPENSION MUST REACH THE CALLER'S
                    # BACKOFF. The venue asked for a delay; a discovery
                    # failure that dropped it would let `main()` come
                    # back sooner than the server said, which is the
                    # one thing a limiter tells us not to do.
                    "suspended_for_s": listed.get("suspended_for_s"),
                    "request_telemetry": listed.get("request_telemetry"),
                    "rate_limit_events": listed.get("rate_limit_events")}
        candidates = listed["candidates"]
        meta = {k: listed.get(k) for k in
                ("pages_read", "rows_listed", "page_size",
                 "listing_attempts", "listing_pages_requested",
                 "listing_incomplete", "rate_limited",
                 "listing_truncated_at_page_bound")}
        meta["listing_rate_limit_events"] = listed.get("rate_limit_events")
        listing_suspended = float(listed.get("suspended_for_s") or 0.0)
        # SPREAD THE SAMPLE ACROSS THE LISTING, REPRODUCIBLY.
        #
        # `candidates_from_listing` sorts by slug and the enrichment
        # window walks that order from an offset, so probe 3c413436's
        # 40 markets were the 40 alphabetically first slugs and came
        # back as one market family. The permutation below is keyed by
        # a RECORDED SEED, fixed before any economic result was seen,
        # so the sample is reproducible from the seed and the listing.
        #
        # It improves coverage WITHIN the bounded listing and nothing
        # more: the listing is itself a page-bounded prefix of the
        # venue.
        ordering = probe_mod.order_candidates(candidates)
        candidates = ordering["ordered"]
        meta["sampling"] = {k: ordering[k] for k in
                            ("seed", "order_version", "population",
                             "population_families", "scope")}
    else:
        meta = {}
        listing_suspended = 0.0

    if not candidates:
        return dict(meta, ok=True, candidates=0, slugs=[], detail=[],
                    considered=0, eligible=0, selected=0, over_cap=0,
                    excluded_by_reason={}, coverage={},
                    next_offset=offset, rule=uni.describe()["rule"],
                    why="listing produced no candidates")

    # BY SLUG, so a round that wraps past the end of the candidate list
    # cannot enrich the same market twice. It did: four rounds of 240
    # over 400 candidates read 480 markets, `considered` came out
    # larger than the candidate set, and the duplicate rows reached
    # `select` as separate entries -- 46 "selected" collapsing to 34
    # subscriptions. The later read of a market wins, being the fresher
    # book.
    by_slug: dict = {}
    coverage, nxt, swept = {}, offset, 0
    stopped = False
    suspended_for = 0.0
    for _ in range(max(1, rounds)):
        # THE WINDOW HINT COUNTS RESERVATIONS, NOT SUCCESSES.
        #
        # It used to be `max_distinct - len(by_slug)`, and `by_slug`
        # holds only markets that PARSED. Thirty failed reads therefore
        # left thirty slots apparently unspent and bought another round
        # of fresh markets: 70 distinct markets against a ceiling of 40,
        # in one lifetime, with no crash involved (measured
        # 2026-09-22). Failures consume their request allowance, so the
        # figure to subtract is what was RESERVED.
        #
        # This is only a hint either way. The bound is the durable
        # reservation, taken one request at a time, and it holds even if
        # this arithmetic is wrong.
        paid = int((coverage or {}).get("distinct_markets", 0) or 0)
        left = None if max_distinct is None else max_distinct - paid
        if left is not None and left <= 0:
            break
        r = await probe_mod.probe(client, candidates, offset=nxt,
                                  batch=batch, should_stop=should_stop,
                                  remaining=len(candidates) - swept,
                                  max_distinct=left, reserve=reserve,
                                  pacer=pacer, sleep=sleep)
        for row in r["rows"]:
            by_slug[row["slug"]] = row
        coverage = probe_mod.merge_coverage(coverage, r["coverage"])
        nxt = r["next_offset"]
        swept += r["probed"]
        if r.get("suspended_for_s"):
            # THE VENUE ASKED FOR LONGER THAN A ROUND. Carried out so
            # the caller's backoff cannot come back sooner than the
            # server asked; retrying earlier than an explicit
            # Retry-After is the one thing a limiter tells us not to do.
            suspended_for = max(suspended_for, r["suspended_for_s"])
            stopped = True
            break
        if r.get("stopped"):
            # THE CONTROL SAID STOP MID-ACQUISITION. Whatever was read
            # is kept -- it cost the venue the same either way -- but no
            # further round is started.
            stopped = True
            break
        if swept >= len(candidates):
            # A FULL SWEEP IS THE END OF THE ROUND BUDGET. Going round
            # again would re-read markets already covered and inflate
            # every count derived from it.
            break
        # ELIGIBLE, NOT ENRICHED. The stopping condition is a FULL
        # UNIVERSE, and only the rule can say when we have one. Counting
        # enriched rows instead stopped after two rounds -- 480 rows,
        # about 30 of them eligible at the measured 6.35% admit rate --
        # and called a third of a universe done.
        if uni.select(list(by_slug.values()))["selected"] >= uni.MAX_MARKETS:
            break

    rows = list(by_slug.values())
    # `probed` counts READS ATTEMPTED and `distinct_enriched` counts
    # MARKETS COVERED; a wrapped round makes the first larger than the
    # second, and only the second says how much of the venue we have
    # actually looked at.
    coverage = dict(coverage, distinct_enriched=len(rows))
    sel = uni.select(rows)
    obs = observation_subset(
        rows, candidates,
        limit=(_int_env(OBSERVE_MAX_ENV, OBSERVE_MAX, OBSERVE_MAX_CEIL)
               if observe_max is None else observe_max),
        admitted=sel["slugs"])
    sel.update(meta)
    sel.update({"ok": True, "candidates": len(candidates),
                "enriched_rows": len(rows), "coverage": coverage,
                "next_offset": nxt, "swept": swept,
                "stopped_by_control": stopped,
                "budget_max_distinct": max_distinct,
                # THE LONGEST DELAY THE VENUE ASKED FOR, from either
                # stage. Taking only the enrichment figure would let a
                # listing suspension be retried sooner than the server
                # said.
                "suspended_for_s": (max(suspended_for, listing_suspended)
                                    or None),
                "pace": probe_mod.configured_pace(),
                "request_telemetry": pacer.telemetry(),
                "observation": obs,
                "enrichment": probe_mod.PROBE_VERSION})
    if ordering is not None:
        sel["sample_report"] = probe_mod.sample_report(
            ordering, [r["slug"] for r in rows])
    return sel


def _event_times(sel: dict) -> dict:
    """When each selected market's event is expected to conclude.

    Used to schedule the settlement check shortly after the event
    rather than on whatever rung of a blind backoff the contract
    happens to be on. Absent or unparsable means None, and the
    scheduler falls back to the bounded backoff -- a guessed event time
    would be worse than none.
    """
    out: dict = {}
    now = time.time()
    for d in sel.get("detail") or []:
        slug = d.get("slug")
        if not slug:
            continue
        raw = d.get("event_at") or d.get("time_to_event_s")
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        # `time_to_event_s` is a delta; an absolute epoch is not.
        out[slug] = now + v if abs(v) < 10 ** 8 else v
    return out


async def _final_flush(loop) -> None:
    """The last batch, even though this task is being cancelled.

    `workers/all.py` stops a loop by CANCELLING it. A `finally` that
    awaits inside an already-cancelled task has its first await
    re-raise CancelledError, so the batch the shutdown existed to save
    is exactly the batch that is lost. `uncancel()` clears the pending
    request for the duration of the flush; the CancelledError that
    brought us here still propagates out of the finally.
    """
    t = asyncio.current_task()
    if t is not None and t.cancelling():
        t.uncancel()
    try:
        await loop.flush()
        await loop.save_ledger()
    except asyncio.CancelledError:      # a second cancel while flushing
        loop.bump("final_flush_cancelled")


async def main(*, client=None, stream_factory=None, store=None,
               run_for_s: float | None = None, control_pool=None,
               sleep=None) -> dict | None:
    """The supervised loop `workers/all.py` runs.

    Every keyword is None in production: `workers/all.py` calls
    `main()`. They exist so the startup-path runner can drive discovery
    -> selection -> subscription -> decisions through THIS function
    rather than around it.

    Credentials come from the service's own environment -- the same
    `PMUS_KEY_ID` / `PMUS_SECRET_KEY` the workers already hold. Never
    copied, never logged.
    """
    from ..config import settings

    # A BOUNDED RUN DOES NOT SERVE A BACKOFF. `run_for_s` is set only by
    # the harnesses and the startup-path runner, which are asking what
    # `main()` DECIDES, not waiting out the supervisor on its behalf.
    # Production passes neither keyword and gets the real hold; the
    # schedule itself is asserted against an injected `sleep`.
    if sleep is None and run_for_s is not None:
        async def sleep(_delay):        # noqa: F811 -- deliberate shadow
            return

    if not enabled():
        log.info("bettor_live_loop: disabled by %s=off", KILL_ENV)
        await _backoff("KILL_SWITCH", sleep=sleep)
        return {"started": False, "why": "KILL_SWITCH"}

    # THE AUTHORITATIVE STOP, AND IT IS READ FIRST. Before a venue
    # request, before the schema, before credentials: a loop that is
    # stopped must cost nothing. The environment variable above is kept
    # as a cheap pre-check only -- it was set and acknowledged on
    # 2026-09-21 and the restarted process still did not read it, so it
    # is not what holds this loop off.
    control = await ctl.read_control(control_pool)
    if ctl.is_closed(control):
        # THE EFFECTIVE CONFIGURATION IS LOGGED EVEN WHEN STOPPED, and
        # that is deliberate. Setting an environment variable does not
        # prove the RUNNING PROCESS received it: on 2026-09-21
        # BETTOR_LIVE_LOOP=off was stored, acknowledged, and survived a
        # demonstrated restart without ever reaching the process that
        # was supposed to read it.
        #
        # So the process states its own budget, out of its own
        # `os.environ`, on every pass. Reading it costs nothing -- no
        # venue request, no database query, no socket -- so a stopped
        # loop can be used to VERIFY a configuration BEFORE anything is
        # enabled, which is the only order that catches the failure
        # above.
        log.info("bettor_live_loop: not observing (%s: %s); "
                 "effective config %s", control["why"],
                 control.get("detail"), json.dumps(effective_config()))
        await _backoff(control["why"], sleep=sleep)
        return {"started": False, "why": control["why"],
                "control": control, "config": effective_config()}

    # THE STOP, ASKED DURING ACQUISITION AND NOT ONLY BEFORE IT. At the
    # demonstrated pace a full startup round is minutes of wall clock,
    # so a stop that only took effect between rounds would wait out
    # every outstanding read. This closure is handed to the probe and
    # awaited between reads; it re-reads the database at most every
    # CONTROL_EVERY_S and otherwise answers from the last verdict, so
    # the cost is one query per interval however many reads happen.
    #
    # WORST-CASE STOP LATENCY DURING ACQUISITION:
    #   CONTROL_EVERY_S + PROBE_STOP_CHECK_EVERY / max_rps + timeout
    _ctl_cache = {"state": control, "at": time.time()}

    async def _stop_now() -> bool:
        now = time.time()
        if now - _ctl_cache["at"] >= ctl.CONTROL_EVERY_S:
            _ctl_cache["state"] = await ctl.read_control(control_pool)
            _ctl_cache["at"] = now
        return ctl.is_closed(_ctl_cache["state"])

    # THE LIFETIME BUDGET, read after the control and before anything
    # is acquired. It fails closed the same way: a budget we cannot
    # read, that has no row, that is exhausted or whose deadline has
    # passed, is not permission to acquire. Held in the database, not
    # in memory, because the supervisor restarts a returning loop
    # forever and an in-process cap would be re-granted on every cycle.
    budget = await ctl.read_budget(control_pool)
    if not budget.get("open"):
        log.info("bettor_live_loop: not observing (%s: %s); "
                 "effective config %s", budget["state"],
                 budget.get("detail"), json.dumps(effective_config()))
        if budget["state"] in (ctl.B_EXPIRED, ctl.B_EXHAUSTED):
            # AUTOMATIC SHUTDOWN, AND IT HAS TO OUTLIVE THE RESTART.
            # Returning is not enough: the supervisor starts us again
            # in five seconds. Writing the control to false stops the
            # next process too. Only ever false -- arming is a human
            # action.
            await ctl.disarm(control_pool, budget["state"])
        await _backoff(budget["state"], sleep=sleep)
        return {"started": False, "why": budget["state"],
                "budget": budget, "config": effective_config()}

    # ── THE RESERVER ─────────────────────────────────────────────────
    #
    # One closure, bound to THIS probe's identity, handed to everything
    # that can issue a request. Every listing attempt, every distinct
    # market and every BBO attempt goes through it BEFORE dispatch, and
    # it is the only thing standing between this process and the venue.
    #
    # The identity matters: if the row is re-armed while this process is
    # mid-round, the probe_id changes and every further reservation
    # answers RESERVATION_PROBE_MISMATCH, so a stale process cannot
    # spend the new probe's allowance.
    probe_id = budget.get("probe_id")
    reserved_tally = {"listing": 0, "distinct": 0, "bbo_attempt": 0,
                      "refused": 0}

    async def _reserve(kind: str, slug):
        # `control_pool` IS None IN PRODUCTION. The supervisor registers
        # this loop as `bettor_live_loop.main` and calls it with no
        # arguments; only tests and scripts inject a pool. `ctl.reserve`
        # resolves None through `_resolve` -> `get_pool()`, exactly as
        # `read_control` and `read_budget` already do, so None is passed
        # THROUGH rather than treated as "no pool".
        #
        # An earlier version short-circuited on None and refused every
        # reservation. It failed closed -- no venue request was made --
        # but the probe could not run at all: on 2026-09-22T07:39:05.938Z
        # the live worker read the control as `true`, read an open
        # allowance, and then refused its first listing reservation with
        # NO_CONTROL_POOL. Every local proof had injected a pool, so the
        # seam production actually uses was never exercised.
        r = await ctl.reserve(control_pool, kind, slug=slug,
                              probe_id=probe_id)
        ok = ctl.granted(r)
        if ok:
            reserved_tally[kind] = reserved_tally.get(kind, 0) + 1
        else:
            reserved_tally["refused"] += 1
            log.warning("bettor_live_loop: %s reservation refused for "
                        "%s (%s: %s)", kind, slug or "-", r.get("why"),
                        r.get("detail"))
        return {"ok": ok, "why": r.get("why"),
                "new": r.get("why") == ctl.V_GRANTED,
                "reserved": r.get("reserved"), "cap": r.get("cap")}

    cfg = settings()
    key_id = getattr(cfg, "pmus_key_id", None)
    secret = getattr(cfg, "pmus_secret_key", None)
    if not key_id or not secret:
        # NAMED, not degraded to a public client. A stream that cannot
        # authenticate produces no books, and "no books" must never
        # look like "a quiet market".
        log.error("bettor_live_loop: no PMUS credentials in this "
                  "environment; not starting")
        await _backoff("NO_CREDENTIALS", sleep=sleep)
        return {"started": False, "why": "NO_CREDENTIALS"}

    boot_id = uuid.uuid4().hex[:16]
    if store is None:
        chosen = store_mod.choose(boot_id=boot_id)
        if not chosen.get("ok"):
            # REFUSING IS THE POINT. A run whose evidence dies on the
            # next deploy is a run that produced nothing.
            log.error("bettor_live_loop: no durable store (%s); not "
                      "starting", chosen.get("why"))
            await _backoff("NO_DURABLE_STORE", sleep=sleep)
            return {"started": False, "why": "NO_DURABLE_STORE",
                    "detail": chosen.get("why")}
        store = chosen["store"]
    try:
        started = await store.start()
    except Exception as exc:  # noqa: BLE001
        log.error("bettor_live_loop: durable store would not start (%s); "
                  "not starting", type(exc).__name__)
        await _backoff("STORE_START_FAILED", sleep=sleep)
        return {"started": False, "why": "STORE_START_FAILED",
                "detail": type(exc).__name__}
    if not started.get("ok"):
        await _backoff("STORE_START_REFUSED", sleep=sleep)
        return {"started": False, "why": "STORE_START_REFUSED",
                "detail": started}

    first = await _discover(client=client, should_stop=_stop_now,
                            max_distinct=budget["remaining"],
                            reserve=_reserve, sleep=sleep)
    if not first.get("ok"):
        log.error("bettor_live_loop: %s (%s); not starting",
                  first.get("why"), first.get("error"))
        # NEVER SOONER THAN THE SERVER ASKED. A discovery that failed
        # because the venue suspended us carries its Retry-After here.
        await _backoff("DISCOVERY_FAILED", sleep=sleep,
                       at_least=first.get("suspended_for_s") or 0.0)
        return {"started": False, "why": "DISCOVERY_FAILED",
                "detail": first}
    # ── WHAT IS WATCHED vs WHAT THE STRATEGY ADMITS ─────────────────
    #
    # Two independent outcomes. `first["slugs"]` is the frozen rule's
    # verdict, unchanged. `watch` is the rule's verdict PLUS a small
    # bounded deterministic subset of valid, open markets chosen so the
    # transport can be verified even when the rule admits nothing --
    # which is exactly what happened on probe 3c413436.
    obs = first.get("observation") or {}
    watch = list(obs.get("slugs") or first.get("slugs") or [])
    admission = {d["slug"]: {
        "observation_basis": ("STRATEGY_ADMITTED" if d["strategy_admitted"]
                              else "OBSERVATION_ONLY"),
        "universe_rule_reason": d.get("universe_rule_reason"),
        "universe": d.get("universe"),
    } for d in (obs.get("detail") or [])}

    if not watch:
        # NOTHING VALID AND OPEN WAS FOUND -- a strictly weaker
        # statement than the old EMPTY_UNIVERSE, which fired whenever
        # the ECONOMIC rule said no. The rule is still not relaxed; the
        # difference is that its refusal no longer decides whether the
        # transport gets tested.
        #
        # COVERAGE IS REPORTED BESIDE THE VERDICT, because "of 3,000
        # candidates we read 960 and none qualified" and "we read none
        # of them" are different facts, and the first version of this
        # message could not tell them apart -- it counted 3,000
        # unenriched listing rows as 3,000 one-sided books.
        cov = first.get("coverage") or {}
        log.error("bettor_live_loop: nothing valid and open to watch; not "
                  "starting. COVERAGE candidates=%s probed=%s enriched=%s "
                  "%s | RULE considered=%s excluded=%s | OBSERVABLE %s",
                  cov.get("candidates"), cov.get("probed"),
                  cov.get("enriched"), json.dumps(cov.get("by_status", {})),
                  first.get("considered", 0),
                  json.dumps(first.get("excluded_by_reason", {})),
                  obs.get("eligible_for_observation"))
        await _backoff("EMPTY_UNIVERSE", sleep=sleep,
                       at_least=first.get("suspended_for_s") or 0.0)
        return {"started": False, "why": "NOTHING_OBSERVABLE",
                "considered": first.get("considered", 0),
                "excluded_by_reason": first.get("excluded_by_reason", {}),
                "coverage": cov, "observation": obs,
                "request_telemetry": first.get("request_telemetry"),
                "sample_report": first.get("sample_report"),
                "candidates": first.get("candidates"),
                "pages_read": first.get("pages_read")}

    # THE OUTCOME LEG COMES FROM THE LISTING. It used to be None for
    # every market, so every observation was REJECTED as
    # NO_OUTCOME_IDENTITY and the worker would have run for an hour
    # producing nothing but refusals about its own wiring. Found by
    # running the startup path end to end rather than by reading it.
    legs = {d["slug"]: d.get("outcome_leg")
            for d in (first.get("detail") or [])}
    # The observation subset carries its own legs -- those markets are
    # not in `detail`, which is the rule's chosen list.
    for d in (obs.get("detail") or []):
        legs.setdefault(d["slug"], d.get("outcome_leg"))
    if first.get("selected_without_outcome_leg"):
        log.warning("bettor_live_loop: %d of %d selected markets carry no "
                    "outcome leg; their observations will be refused as "
                    "NO_OUTCOME_IDENTITY",
                    first["selected_without_outcome_leg"],
                    len(first["slugs"]))
    loop, stream = build(
        key_id, secret, slugs=watch,
        leg_of={s: legs.get(s) for s in watch},
        store=store, stream_factory=stream_factory, boot_id=boot_id,
        admission=admission,
        opening_cash=float(os.environ.get("BETTOR_LIVE_OPENING_CASH", "0")))
    loop.max_contracts = float(
        os.environ.get("BETTOR_LIVE_MAX_CONTRACTS", "0"))
    loop.store_started = started
    loop.discovery = {k: v for k, v in first.items() if k != "detail"}
    loop.event_at.update(_event_times(first))

    rec = await loop.recover()
    loop.started_at = _now().isoformat()
    log.info("bettor_live_loop: boot %s, store %s, recovered %s",
             loop.boot_id, json.dumps(started, default=str),
             json.dumps(rec, default=str))
    # NOTHING IS CHARGED HERE, BECAUSE EVERYTHING WAS RESERVED FIRST.
    #
    # This is where the old `consume_budget` call stood, and standing
    # here was the defect: the requests had already been sent, the
    # EMPTY_UNIVERSE return above skipped the call entirely, and a crash
    # anywhere between the two left the row untouched and the whole
    # allowance available to the next process. The allowance is now
    # taken one unit at a time, before each request, by `_reserve`.
    log.info("bettor_live_loop: probe %s reserved %s",
             str(probe_id)[:8], json.dumps(reserved_tally))

    cov0 = first.get("coverage") or {}
    # TWO INDEPENDENT OUTCOMES, LOGGED APART. The first number is what
    # is WATCHED; the second is what the frozen rule ADMITTED. They are
    # different questions and a run is useful when either one answers.
    log.info("bettor_live_loop: WATCHING %d markets (%d strategy-admitted, "
             "%d observation-only); refusals observed %s; sampling %s",
             len(watch), len(first.get("slugs") or []),
             len(obs.get("observation_only_slugs") or []),
             json.dumps(obs.get("refusal_reasons_observed") or {}),
             json.dumps(first.get("sample_report") or {}, default=str))
    log.info("bettor_live_loop: request telemetry %s",
             json.dumps(first.get("request_telemetry") or {}, default=str))
    log.info("bettor_live_loop: %d markets subscribed; %d eligible of %d "
             "ENRICHED; covered %s of %s candidates in %s reads over %s "
             "pages (%s); fees %s (%s)",
             len(watch), first.get("eligible", 0),
             first.get("considered", 0), cov0.get("distinct_enriched"),
             cov0.get("candidates"), cov0.get("probed"),
             first.get("pages_read"), first.get("enrichment"),
             loop.fees.source, loop.fees.status)

    # A RUN THAT STARTED CLEARS THE BACKOFF. Otherwise one bad morning
    # leaves every later restart on the fifteen-minute rung.
    _reset_backoff()
    log.info("bettor_live_loop: effective config %s",
             json.dumps(effective_config(), default=str))

    stream.start()
    t_start = time.time()
    probe_offset = first.get("next_offset", 0)
    candidates = None
    last_discovery = last_settlement = last_save = last_flush = t_start
    last_control = t_start
    loop.control = control
    stop_reason = None
    last_prune = t_start
    cycle = 0
    receipt = None
    try:
        while enabled():
            loop.drain()
            for t in stream.drain_trades():
                loop.on_trade(t)

            now = time.time()
            if now - last_flush >= store_mod.FLUSH_EVERY_S:
                last_flush = now
                await loop.flush()

            # THE STOP IS RE-READ WHILE RUNNING, which is what makes it
            # a control rather than a startup option. Flipping one row
            # stops this loop within CONTROL_EVERY_S -- no deploy, no
            # restart, no environment change. An unreadable control
            # stops it too; see `bettor_live_control`.
            if now - last_control >= ctl.CONTROL_EVERY_S:
                last_control = now
                # THE DEADLINE IS CHECKED ON THE SAME TICK AS THE
                # CONTROL, and it DISARMS rather than merely returning:
                # the supervisor would otherwise start a fresh process
                # five seconds later and the probe would run on.
                b = await ctl.read_budget(control_pool)
                if not b.get("open"):
                    log.warning("bettor_live_loop: stopping on the "
                                "probe budget (%s: %s)", b["state"],
                                b.get("detail"))
                    await ctl.disarm(control_pool, b["state"])
                    stop_reason = b["state"]
                    break
                live = await ctl.read_control(control_pool)
                loop.control = live
                if ctl.is_closed(live):
                    log.warning("bettor_live_loop: stopping on the "
                                "control (%s: %s)", live["why"],
                                live.get("detail"))
                    stop_reason = live["why"]
                    break

            if now - last_discovery >= DISCOVERY_EVERY_S:
                last_discovery = now
                nxt = await _discover(client=client,
                                      candidates=candidates,
                                      offset=probe_offset, rounds=1,
                                      reserve=_reserve,
                                      should_stop=_stop_now)
                probe_offset = nxt.get("next_offset", probe_offset)
                # THE REFRESH WATCHES WHAT THE START WATCHED: admitted
                # markets plus the bounded observation subset. A
                # refresh that narrowed to the rule's verdict alone
                # would silently re-create the circular dependency
                # fifteen minutes into every run.
                nobs = nxt.get("observation") or {}
                nwatch = list(nobs.get("slugs") or nxt.get("slugs") or [])
                if nxt.get("ok") and nwatch:
                    loop.event_at.update(_event_times(nxt))
                    keep = set(nwatch)
                    dropped = stream.prune(keep)
                    added = stream.subscribe(nwatch)
                    for d in (nobs.get("detail") or []):
                        loop.admission[d["slug"]] = {
                            "observation_basis": (
                                "STRATEGY_ADMITTED" if d["strategy_admitted"]
                                else "OBSERVATION_ONLY"),
                            "universe_rule_reason":
                                d.get("universe_rule_reason"),
                            "universe": d.get("universe")}
                        loop.leg_of.setdefault(d["slug"],
                                               d.get("outcome_leg"))
                    loop.bump("discovery_refresh")
                    log.info("bettor_live_loop: discovery refresh "
                             "+%d -%d (now %d watched, %d admitted)",
                             added["queued"], dropped, len(keep),
                             len(nxt.get("slugs") or []))
                else:
                    # A refresh that fails leaves the EXISTING universe
                    # in place. Dropping it would turn a transient venue
                    # error into an empty engine.
                    loop.bump("discovery_refresh_failed")

            if now - last_settlement >= SETTLEMENT_EVERY_S:
                last_settlement = now
                try:
                    out = await loop.ingest_settlements(client=client)
                    loop.outcomes = await loop.store.outcome_report()
                    log.info("bettor_live_loop: settlement %s outcomes %s",
                             json.dumps(out.get("counts", {})),
                             json.dumps(loop.outcomes, default=str))
                except Exception as exc:  # noqa: BLE001
                    loop.bump("settlement_pass_failed")
                    log.warning("settlement pass failed: %s",
                                type(exc).__name__)

            if now - last_save >= LEDGER_EVERY_S:
                last_save = now
                await loop.save_ledger()

            if now - last_prune >= store_mod.PRUNE_EVERY_S:
                last_prune = now
                p = await loop.prune()
                if p.get("journal_rows_deleted") or p.get("cursors_deleted"):
                    log.info("bettor_live_loop: pruned %s",
                             json.dumps(p, default=str))

            cycle += 1
            if cycle % 150 == 0:
                rep = loop.report()
                d = rep["durability"]
                if d["windows_incomplete"]:
                    log.error("bettor_live_loop: %d records DROPPED; "
                              "measurement windows %s..%s are INCOMPLETE",
                              d["records_dropped"], d["incomplete_from"],
                              d["incomplete_to"])
                if (d["oldest_uncommitted_age_s"] or 0) > 60:
                    log.warning("bettor_live_loop: oldest uncommitted "
                                "record is %.1f s old, %d pending, %d "
                                "flush failures (%s)",
                                d["oldest_uncommitted_age_s"],
                                d["uncommitted_records"],
                                d["flush_failures"], d["last_flush_error"])
                log.info("bettor_live_loop: %s", json.dumps(
                    {"stream": rep["stream"], "frames": rep["frames"],
                     "by_action": rep["by_action"],
                     "ineligible": rep["ineligible_by_reason"],
                     "freshness": rep["freshness"],
                     "durability": d,
                     "settlement": rep["settlement"]["schedule"],
                     "outcomes": rep["settlement"]["outcomes"],
                     "orders_submitted": rep["orders_submitted"]},
                    default=str))
            if run_for_s is not None and time.time() - t_start >= run_for_s:
                break
            await asyncio.sleep(POLL_S)
    finally:
        # IN A FINALLY. `workers/all.py` shuts a loop down by
        # CANCELLING it, and a CancelledError raised at the await above
        # would otherwise leave the socket thread running forever and
        # the last batch of records unwritten.
        loop.stopped_at = _now().isoformat()
        # WAIT FOR THE SOCKET, AND MEASURE IT.
        #
        # `stop()` used to set a flag and return, so "the stream closed
        # within 90 seconds" could only ever be argued from the absence
        # of new rows -- which a quiet market produces just as well.
        # The thread is now joined with a bounded wait and the closure
        # is timed by the process that did it.
        shut = stream.stop(wait_s=STREAM_CLOSE_WAIT_S)
        await _final_flush(loop)
        rep = loop.report()
        receipt = {
            "probe_id": str(probe_id) if probe_id else None,
            "boot_id": loop.boot_id,
            "loop": LOOP_VERSION,
            "stopped_by": stop_reason,
            "started_at": loop.started_at,
            "stopped_at": loop.stopped_at,
            "runtime_s": rep.get("runtime_s"),
            # ACQUISITION: what was spent, and when the last outbound
            # request was started. `requests_started` stops rising the
            # moment acquisition ceases, so the pair is the cessation
            # evidence.
            "acquisition": {
                "reserved": dict(reserved_tally),
                "telemetry": (loop.discovery or {}).get(
                    "request_telemetry"),
            },
            # TRANSPORT: the positive shutdown facts.
            "transport": shut,
            "stream": rep.get("stream"),
            "frames": rep.get("frames"),
            "watching": rep.get("watching"),
            "records_written": loop.records_written,
            "durability": rep.get("durability"),
            "orders_submitted": 0,
        }
        await ctl.write_stop_receipt(control_pool, receipt)
        log.info("bettor_live_loop: stopped (%s); transport %s; "
                 "%d records written, %d persist failures",
                 stop_reason, json.dumps(shut, default=str),
                 loop.records_written, loop.persist_failures)
    return {"started": True, "stopped_by": stop_reason,
            "report": loop.report(), "stop_receipt": receipt}
