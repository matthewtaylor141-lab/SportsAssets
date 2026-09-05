"""Path B — Data API polling (fallback + reconciliation source of truth).

Polls /trades?user={wallet} per tracked wallet on a staggered schedule:
with N wallets and interval I, one wallet is polled every I/N seconds
(5 wallets @ 5s → 1 req/s aggregate). Records arrive pre-enriched, so a
Path-B-first detection still renders fully in the feed, flagged source=poll.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..config import settings
from ..db import get_pool, heartbeat
from .dedupe import key_fields_valid
from .pipeline import TradeEvent, ingest_trade_result

log = logging.getLogger(__name__)

# A most-recent page whose newest row sits this far behind a fill this
# poller itself venue-stamped is a frozen index, not a quiet wallet
# (fleet round 37). Generous vs feed jitter; a real regression is
# hours, not minutes.
STALE_INDEX_S = 1800

# How long run()'s finally waits for its cancelled side loops to unwind
# before abandoning them with an error line (2026-09-05). Cancellation
# lands at the child's next await, so this is not "finish the work",
# only "let the CancelledError propagate"; both loops catch Exception,
# never BaseException, so it should land at once. The bound exists
# because an open-ended wait here would be the one place a child that
# swallowed its cancel could hang run() forever -- and a run() that
# never returns is one supervise() never restarts, which is Path B
# dead in silence.
CANCEL_WAIT_S = 10.0

# The 'Path B degraded' alert's ceiling (2026-09-05, the containment
# re-review of the same day's fix). _alert_degraded guards the call so
# a channel that RAISES is a log line and not the end of run() -- but
# a channel that HANGS was still the end of run()'s pacing, with
# nothing to log: an await with no ceiling is held for as long as the
# carrier takes, and the alert fires at exactly the moment (a dead
# database, a dead venue) the same bad night is likeliest to have
# taken the carrier too. Fifteen seconds sits ABOVE telegram._send's
# own httpx timeout of 10 s on purpose: a channel with its own ceiling
# and its own error text (a 502, its own read timeout) gets to put
# that text on the log line, and this ceiling only ever names a bare
# TimeoutError for a channel that has no ceiling of its own.
# asyncio.TimeoutError is an Exception, so the guard's existing except
# contains it like any other failed delivery.
ALERT_TIMEOUT_S = 15.0


def parse_data_api_trade(raw: dict[str, Any], whale_id: int, username: str | None) -> TradeEvent:
    """Map a data-api trade payload onto our TradeEvent."""
    # stored values are NORMALIZED exactly as the dedupe key and the
    # validity gate normalize them (fleet round 15: side='buy ' passed
    # the gate on .upper().strip() but the INSERT bound the raw value
    # and the side CHECK constraint killed the whole batch)
    return TradeEvent(
        whale_id=whale_id,
        whale_username=username,
        tx_hash=str(raw.get("transactionHash") or raw.get("txHash") or "").strip(),
        asset=str(raw.get("asset") or raw.get("tokenId") or "").strip(),
        side=str(raw.get("side", "")).upper().strip(),
        size=float(raw.get("size", 0)),
        price=float(raw.get("price", 0)),
        ts_epoch=int(raw.get("timestamp", 0)),
        source="poll",
        condition_id=raw.get("conditionId"),
        outcome=raw.get("outcome"),
        outcome_index=raw.get("outcomeIndex"),
        market_title=raw.get("title"),
        market_slug=raw.get("slug"),
        event_slug=raw.get("eventSlug"),
        sport="unclassified",  # classified below from persisted metadata when available
    )


async def _sport_for_condition(condition_id: str | None) -> str | None:
    if not condition_id:
        return None
    pool = await get_pool()
    return await pool.fetchval("SELECT sport FROM markets WHERE condition_id=$1", condition_id)


def priority_whales(whales: list[dict]) -> list[dict]:
    """The pinned COPY whales out of the tracked roster — the wallets the
    executor actually trades on, and therefore the only ones whose
    detection latency is worth paying extra request budget for."""
    from ..api.copies_record import COPY_WHALES, CRYPTO_WHALES

    return [w for w in whales
            if (w.get("username") or "").lower()
            in (COPY_WHALES | CRYPTO_WHALES)]


# Polls that came back with a FULL page, per whale. See poll_wallet:
# /trades takes limit=100 and no cursor, so a full page means the
# venue may hold older trades this poll could not see. Counting a
# suspicion, not a loss.
_PAGE_FULL: dict[str, int] = {}


def page_full_counts() -> dict:
    """Per-whale count of full-page polls, for the heartbeat."""
    return dict(_PAGE_FULL)


class Poller:
    def __init__(self) -> None:
        cfg = settings()
        self._http = httpx.AsyncClient(base_url=cfg.data_api_base, timeout=10)
        self._interval = cfg.poll_interval_seconds
        self._priority_interval = cfg.poll_priority_seconds
        self._fail_threshold = cfg.poll_failure_alert_threshold
        self._consecutive_failures = 0
        self.on_alert = None  # callable(str) set by the worker (Telegram admin alert)
        # Detection-lag telemetry (owner latency push 2026-08-20): venue
        # trade timestamp -> our ingest, for the LAST new trade this
        # process detected. The number that says whether copy latency is
        # ours to fix or the venue's publication lag.
        self.last_lag_s: float | None = None
        # The side loops run() spawns, kept so run() can cancel them on
        # its way out (2026-09-05; see run()). Empty until run() starts.
        self._subtasks: list[asyncio.Task] = []

    async def tracked_whales(self) -> list[dict]:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT id, address, username FROM whales WHERE active AND NOT banned ORDER BY id"
        )
        return [dict(r) for r in rows]

    async def poll_wallet(self, whale: dict) -> int:
        """One poll cycle for one wallet. Returns count of NEW trades ingested."""
        from ..ratelimit import polite_get

        resp = await polite_get(
            self._http,
            "/trades",
            params={"user": whale["address"], "limit": 100, "takerOnly": "false"},
        )
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, list):
            # round 23 (major): an HTTP-200 body that is valid JSON
            # but NOT a list (an error dict, a pagination envelope, a
            # bare JSON string) used to iterate anyway — every element
            # died in the per-row containment below and the cycle
            # returned 0 as a SUCCESSFUL empty poll, RESETTING the
            # failure counter and heartbeating 'ok': the configured
            # 'Path B degraded' alert was structurally unreachable
            # while the carrier was dead, and the ops monitor reads
            # the same heartbeat row. The reconciler has classified
            # this exact body as a venue error shape since round 19;
            # the poller now fails the cycle so run()'s failure
            # accounting counts it and the alert can fire.
            raise ValueError(
                "venue served a non-list /trades body: "
                + type(page).__name__)
        events = []
        bad = 0
        for raw in page:
            # same validity the reconciler enforces (fleet rounds
            # 12-14): every dedupe-key field gates ingest, checked on
            # the NORMALIZED, FINITE values the key itself derives —
            # and contained per row, because a hostile field can make
            # the parse itself raise (round 14: one Infinity row was
            # aborting the wallet's ENTIRE poll cycle at the key-list
            # build, killing the poll carrier every healthy fill and
            # every S1 abstention leans on, below the alert threshold)
            try:
                ev = parse_data_api_trade(raw, whale["id"], whale["username"])
                if not key_fields_valid(ev):
                    bad += 1
                    continue
            except Exception:  # noqa: BLE001 — one junk row costs one row
                bad += 1
                continue
            events.append(ev)
        # A FULL PAGE MEANS WE MAY HAVE MISSED THE ONES BEHIND IT
        # (2026-08-31). /trades is fetched with limit=100 and NO cursor:
        # one page, newest first, never followed. So a whale who makes
        # more than 100 trades between two polls of his wallet loses the
        # overflow permanently — the next poll starts from the newest
        # again and the older ones have fallen off the end.
        #
        # Sized before writing this, so it is not an alarm dressed as a
        # finding: at poll_interval_seconds=5.0 the page holds 5s of
        # flow, and overflow needs 20 trades/second. rn1 — the busiest
        # book on the roster at a median 11,514 trades/day — averages
        # 0.13/s. Headroom is roughly 150x and this has probably never
        # fired.
        #
        # It is counted anyway because the failure is SILENT and
        # unrecoverable: nothing anywhere would show a gap, the trades
        # simply never exist, and every edge interval we publish would
        # then rest on a denominator missing its busiest minutes. A
        # full page is not proof of loss — exactly 100 could be exactly
        # 100 — so this counts a SUSPICION, and the name says so.
        if len(page) >= 100:
            _PAGE_FULL[whale.get("username") or "?"] = (
                _PAGE_FULL.get(whale.get("username") or "?", 0) + 1)
            log.warning(
                "POLLER page full (%d) for %s — the venue may hold "
                "older trades this poll did not see; limit=100 has no "
                "cursor", len(page), whale.get("username"))
        if page and bad == len(page):
            # the round-23 shape one level down: a LIST page whose
            # every element is unusable (nulls, junk dicts) is the
            # same dead carrier as a non-list body — 0 as a
            # "successful" poll would reset the failure counter and
            # keep the Path B alert unreachable. One healthy row is a
            # normal page; zero healthy rows out of a served page is
            # venue degradation and fails the cycle.
            raise ValueError(
                f"venue served {bad} rows, none usable")
        if not events:
            # round 36 (major): a 200-[] page slipped past both
            # round-23 guards (non-list; all-junk needs a truthy
            # page) and returned 0 as a SUCCESSFUL poll — failure
            # counter reset, heartbeat 'ok', the Path-B-degraded
            # alert structurally unreachable while the primary
            # carrier (and the source of every venue_seen_at stamp
            # S1's corroboration leans on) was dead. The request is
            # not cursor-windowed — it asks for the wallet's most
            # recent 100 trades — so a whale with ANY known fill can
            # never legitimately serve an empty page: that is the
            # round-25 cold-index shape the backfill already refuses.
            # Known fills + empty page fails the cycle, visibly.
            if not page:
                pool = await get_pool()
                has_any = await pool.fetchval(
                    "SELECT 1 FROM trades WHERE whale_id = $1 LIMIT 1",
                    whale["id"])
                if has_any:
                    raise ValueError(
                        "venue served an empty /trades page for a "
                        "whale with known fills — a cold venue "
                        "index, not an empty history")
            return 0
        # BATCH PRE-DEDUPE (audit 2026-08-21): nearly every returned row
        # is already ingested, and the old path paid a sport SELECT plus
        # an INSERT-conflict round trip PER ROW — hundreds of wasted
        # queries/sec across the fast lane, on the same Postgres the
        # executor prices against. One ANY() probe drops the known rows;
        # the INSERT ... ON CONFLICT stays as the authoritative gate for
        # anything that races in between.
        pool = await get_pool()
        # dedupe_key is a @property — calling it was 'str' object is not
        # callable on EVERY wallet with events (Path B fully down,
        # 2026-08-21 evening; masked because chain carried the sports
        # whales and the hourly reconciler back-filled the rest).
        keys = [ev.dedupe_key for ev in events]
        try:
            # an UNSTAMPED s1 row is deliberately NOT "known": the poll
            # duplicate must flow through ingest once so the conflict
            # branch stamps venue_seen_at — the venue-anchored
            # corroboration the shadow requires of every S1 emission
            # (fleet round 2). Once stamped, the key is known again.
            seen = {r["dedupe_key"] for r in await pool.fetch(
                "SELECT dedupe_key FROM trades "
                "WHERE dedupe_key = ANY($1::text[]) "
                "AND NOT (source = 's1' AND venue_seen_at IS NULL)",
                keys)}
        except Exception:  # noqa: BLE001 — pre-filter is an optimization
            seen = set()
        new = 0
        attempted = 0
        ingest_bad = 0
        for ev, key in zip(events, keys):
            if key in seen:
                continue
            attempted += 1
            # per-row containment around the INGEST too (fleet round
            # 15: a gate-passing row can still fail inside ingest —
            # DB constraint, column overflow, datetime range — and an
            # uncontained raise here killed the wallet's whole batch
            # every cycle, the exact round-14 class one call later).
            # One row that cannot land costs one row, never the batch.
            try:
                sport = await _sport_for_condition(ev.condition_id)
                if sport:
                    ev.sport = sport
                # Same contract change as the reconciler: ingest_trade
                # returns the id for duplicates too since the ON
                # CONFLICT DO UPDATE, so `is not None` over-counted
                # every re-polled fill as new and inflated this
                # heartbeat's `new` on every pass.
                _tid, was_new = await ingest_trade_result(ev)
            except Exception:  # noqa: BLE001
                log.warning("poll: row failed to ingest, skipping: %s",
                            key[:16])
                ingest_bad += 1
                continue
            if was_new:
                new += 1
                if ev.ts_epoch:
                    import time as _t

                    self.last_lag_s = round(_t.time() - ev.ts_epoch, 1)
        if attempted and ingest_bad == attempted:
            # round 24 (minor): the round-23 arithmetic one layer
            # down — a cycle whose EVERY attempted row failed inside
            # ingest (constraint drift, DB refusals: the 2026-08-21
            # side-CHECK incident is the documented precedent) still
            # returned success, reset the failure counter and
            # heartbeated ok while zero rows and zero venue_seen_at
            # stamps could land. Total ingest loss fails the cycle;
            # a mixed outcome stays per-row (round 15).
            raise ValueError(
                f"all {attempted} attempted rows failed inside ingest")
        if not attempted and events:
            # round 37 (major): the round-36 cold-index probe fires
            # only on an EMPTY page — a frozen index serving stale,
            # already-ingested rows while omitting fresh fills was
            # still a silent successful poll (attempted=0), the same
            # dead carrier behind the same green heartbeat, and two
            # reconciler walks of the same frozen feed even counted
            # as clean coverage. The check is the index's own math: a
            # fill THIS poller venue-stamped can only leave the
            # most-recent page by being pushed down by NEWER fills,
            # so the wallet's newest known poll-stamped fill sitting
            # beyond a lag margin ABOVE the served page's newest row
            # is a provable index regression, never an empty hour.
            page_newest = max((ev.ts_epoch or 0 for ev in events),
                              default=0)
            try:
                # ::float8 (fleet round 39, major): extract(epoch)
                # returns NUMERIC on real PG, asyncpg decodes Decimal,
                # and the isinstance((int,float)) guard below was
                # structurally False — the round-37 alarm was dead on
                # arrival, provable only against real Postgres (the
                # unit fake returned a Python float). Every other
                # epoch query in the tree casts; this one now does.
                known_newest = await pool.fetchval(
                    "SELECT extract(epoch from max(ts))::float8 "
                    "FROM trades WHERE whale_id = $1 "
                    "AND source = 'poll' "
                    "AND venue_seen_at IS NOT NULL", whale["id"])
            except Exception:  # noqa: BLE001 — probe is best-effort;
                known_newest = None       # next cycle re-checks
            if (isinstance(known_newest, (int, float)) and page_newest
                    and known_newest - page_newest > STALE_INDEX_S):
                raise ValueError(
                    "venue index regressed: newest served row is "
                    f"{round(known_newest - page_newest)}s older than "
                    "a fill this poller already venue-stamped — a "
                    "frozen index, not a quiet wallet")
        return new

    async def _priority_loop(self) -> None:
        """Fast lane (owner latency push 2026-08-20): the pinned copy
        whales are re-polled on their own short cycle, on top of the
        full-roster rotation. Every second of detection lag is ~1.5c/90s
        of copy edge decaying, so the wallets we actually trade get
        polled every ~poll_priority_seconds instead of waiting out a
        full roster pass. Duplicates lose the ingest dedupe and cost
        nothing; the shared Data-API throttle still bounds total rps."""
        while True:
            try:
                whales = priority_whales(await self.tracked_whales())
                if not whales:
                    await asyncio.sleep(10)
                    continue

                # CONCURRENT pass, time-boxed cycle (audit 2026-08-21):
                # the sequential loop added the stagger ON TOP of each
                # poll's duration, so 9 priority wallets ran a real
                # cycle of ~8-11s against the configured 2.5s. Polls now
                # fire together — the shared Data-API throttle still
                # serializes the HTTP starts and bounds total rps — and
                # the sleep is whatever remains of the interval, not a
                # fixed add-on.
                async def _one(whale: dict) -> None:
                    try:
                        await self.poll_wallet(whale)
                    except Exception as exc:  # noqa: BLE001 — one bad
                        # wallet must never stall the fast lane; the
                        # main loop's failure accounting owns alerting.
                        log.warning("fast-lane poll failed for %s: %s",
                                    whale["address"], exc)

                import time as _t
                t0 = _t.monotonic()
                await asyncio.gather(*(_one(w) for w in whales))
                elapsed = _t.monotonic() - t0
                await asyncio.sleep(
                    max(0.25, self._priority_interval - elapsed))
            except Exception:  # noqa: BLE001 — roster fetch etc.
                log.exception("fast-lane pass failed; retrying")
                await asyncio.sleep(5)

    async def _history_loop(self) -> None:
        """One-time deep history import per whale — background, never blocks
        live polling; checks for newly added whales every minute."""
        from .history import backfill_pending  # late import to avoid cycle

        while True:
            try:
                scanned = await backfill_pending()
                if scanned:
                    log.info("deep history backfill scanned %s trades", scanned)
            except Exception:  # noqa: BLE001
                log.exception("history backfill pass failed; will retry")
            await asyncio.sleep(60)

    async def _beat(self, status: str, detail: dict | None = None) -> None:
        """heartbeat() for run(): written when it can be, logged when it
        cannot, never a raise. The beat goes through the same pool the
        poll just lost, so with the database unreadable it raises too
        (get_pool's own backoff, then RuntimeError; or db.heartbeat's
        own ceiling, TimeoutError, against a database that answers and
        then stalls) -- and until 2026-09-05 that raise left the except
        branch in run() and killed the loop. This makes the beat itself
        non-fatal, nothing more: whether a dead database REACHES the
        'Path B degraded' alert is run()'s failure accounting, which
        counts the roster stage toward the same threshold as the wallet
        stage (see _alert_degraded)."""
        try:
            await heartbeat("poller", status, detail)
        except Exception as exc:  # noqa: BLE001 -- the beat is telemetry
            # the type is named because a TimeoutError's str() is empty
            log.warning("poller heartbeat %r not written: %s: %s",
                        status, type(exc).__name__, exc)

    async def _alert_degraded(self) -> None:
        """The 'Path B degraded' admin alert: once per failure streak,
        exactly at the threshold, from EITHER failing stage, and never
        a raise out of run().

        Until 2026-09-05 only the per-wallet branch counted toward the
        threshold, so a database that was fully gone -- the roster read
        failing every pace, no wallet ever polled -- produced paced
        'error' beats and no Telegram line at all, while the docstring
        promised one. The roster branch now increments the same
        counter (a roster failure IS a failed poll cycle) and calls
        this; a wallet success resets it, as before. A successful
        roster read alone does NOT reset it: with a one- or two-wallet
        roster and threshold 3, a reset at every pass boundary would
        make a dead venue's streak structurally unable to reach 3 --
        the exact 'alert unreachable' class of rounds 23, 24 and 36.
        The call itself is guarded because the alert channel (Telegram)
        is one more carrier that can be down on the same bad night, and
        a raise here would end the loop the alert is about -- and
        bounded (ALERT_TIMEOUT_S), because a carrier that hangs instead
        of failing would end its pacing just as surely, with nothing
        logged."""
        if self._consecutive_failures != self._fail_threshold or not self.on_alert:
            return
        try:
            # bounded (see ALERT_TIMEOUT_S): a channel that hangs must
            # end in a TimeoutError the except below can log, never
            # hold run() at the threshold
            await asyncio.wait_for(
                self.on_alert(
                    f"⚠️ Poll cycle failed {self._consecutive_failures}× — Path B degraded"
                ),
                ALERT_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 -- the alert is telemetry too
            log.warning("poller degraded-alert not delivered: %s: %s",
                        type(exc).__name__, exc)

    @staticmethod
    def _side_loop_died(task: asyncio.Task) -> None:
        """Done-callback on every side loop run() spawns (2026-09-05).
        Both loops catch Exception and go on forever, so a side loop
        that ENDS on its own is a bug in the loop's own containment
        (its except path raising, a BaseException that is not a
        cancel) -- and the first cut of run()'s finally retrieved that
        exception and discarded it, which silenced the one signal that
        the fast lane or the backfill had been dead for the rest of the
        run. This logs it at the moment it happens, while run() is
        still alive; the finally logs what it finds on the way out."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("poller side loop %s died on its own: %r",
                      task.get_name(), exc)

    async def run(self, history: bool = True) -> None:
        """history=False: LIVE detection only — no deep-history backfill.

        The backfill pages a whale's full lifetime trades (millions for the
        reference account) and belongs on a worker with room to breathe.
        Run inside the API service's memory limit it OOM-cycled the whole
        API every ~10 minutes (observed 2026-08-02 23:30Z, minutes after
        the ingestion fallback first deployed with it enabled).

        run() OWNS its side loops and its HTTP client and CONSUMES this
        Poller: when it returns or raises, for any reason, the history
        and fast-lane tasks are cancelled and awaited and the client is
        closed. Build a new Poller for a new run (workers/poller.py and
        the API's ingestion fallback both do)."""
        log.info("Path B poller starting (interval=%ss, fast lane=%ss)",
                 self._interval, self._priority_interval)
        # WHY THE HANDLES ARE KEPT AND THE ROSTER READ IS INSIDE THE TRY
        # (2026-09-05, the workers dying during the full-disk outage of
        # 2026-09-04/05). The two side loops used to be spawned with a
        # bare create_task and their handles dropped, and the roster
        # read sat outside any try. With the database unreadable the
        # roster read raised out of run(); workers/all.py's supervise()
        # restarted it 5 s later through workers/poller.py, which builds
        # a NEW Poller -- while the two orphaned loops of the old one
        # lived on forever (they catch Exception and loop), holding the
        # old Poller and its never-closed httpx client. Every ~110 s of
        # dead database: +1 Poller, +1 AsyncClient, +2 immortal tasks,
        # for as long as the outage lasted. Now the roster read is a
        # logged, paced retry inside the loop, and the finally below
        # cancels and awaits whatever this run() spawned and closes the
        # client, so a run() that does end takes everything it owns
        # with it.
        tasks: list[asyncio.Task] = []
        self._subtasks = tasks
        try:
            if history:
                t = asyncio.create_task(self._history_loop(), name="poller.history")
                t.add_done_callback(self._side_loop_died)
                tasks.append(t)
            if self._priority_interval > 0:
                t = asyncio.create_task(self._priority_loop(), name="poller.priority")
                t.add_done_callback(self._side_loop_died)
                tasks.append(t)
            while True:
                try:
                    whales = await self.tracked_whales()
                except Exception as exc:  # noqa: BLE001 -- the roster read
                    # IS the database; unreadable is a paced retry, never
                    # a raise that restarts run() and orphans the loops.
                    # It counts as a failed cycle toward the degraded
                    # alert (see _alert_degraded): a database that is
                    # fully gone must reach Telegram, not only the
                    # heartbeat row it cannot write.
                    self._consecutive_failures += 1
                    log.warning("poll: roster unreadable (%s); retry in %ss",
                                exc, self._interval)
                    await self._beat("error", {"stage": "roster",
                                               "failures": self._consecutive_failures,
                                               "error": str(exc)})
                    await self._alert_degraded()
                    await asyncio.sleep(self._interval)
                    continue
                if not whales:
                    await self._beat("idle", {"reason": "empty roster"})
                    await asyncio.sleep(self._interval)
                    continue
                stagger = self._interval / len(whales)
                for whale in whales:
                    try:
                        new = await self.poll_wallet(whale)
                        self._consecutive_failures = 0
                        await self._beat("ok",
                                         {"last_wallet": whale["address"],
                                          "new": new,
                                          "detect_lag_s": self.last_lag_s})
                    except Exception as exc:  # noqa: BLE001 — one bad wallet/payload
                        # must never kill live detection for the others
                        self._consecutive_failures += 1
                        log.warning("poll failed for %s: %s", whale["address"], exc)
                        await self._beat(
                            "error", {"failures": self._consecutive_failures, "error": str(exc)}
                        )
                        await self._alert_degraded()
                    await asyncio.sleep(stagger)
        finally:
            for t in tasks:
                t.cancel()
            try:
                if tasks:
                    # asyncio.wait, not gather: a child that crashed on
                    # its own keeps its exception on the task instead of
                    # raising it here, and the timeout bounds the wait
                    # (see CANCEL_WAIT_S). Either way the client below
                    # is closed.
                    done, pending = await asyncio.wait(tasks, timeout=CANCEL_WAIT_S)
                    for t in done:
                        if t.cancelled():
                            continue
                        # retrieved either way (no GC-time warning), and
                        # a loop that ended on its own is said so, not
                        # swallowed: see _side_loop_died for the moment
                        # it happened; this is what run() found leaving.
                        exc = t.exception()
                        if exc is not None:
                            log.error("poller side loop %s died on its own: %r "
                                      "(found dead when run() left)",
                                      t.get_name(), exc)
                    for t in pending:
                        log.error("poller side loop %s ignored cancel for "
                                  "%ss; abandoned", t.get_name(), CANCEL_WAIT_S)
            finally:
                http = self._http
                if http is not None:
                    await http.aclose()
