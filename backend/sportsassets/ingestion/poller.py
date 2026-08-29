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
                known_newest = await pool.fetchval(
                    "SELECT extract(epoch from max(ts)) FROM trades "
                    "WHERE whale_id = $1 AND source = 'poll' "
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

    async def run(self, history: bool = True) -> None:
        """history=False: LIVE detection only — no deep-history backfill.

        The backfill pages a whale's full lifetime trades (millions for the
        reference account) and belongs on a worker with room to breathe.
        Run inside the API service's memory limit it OOM-cycled the whole
        API every ~10 minutes (observed 2026-08-02 23:30Z, minutes after
        the ingestion fallback first deployed with it enabled)."""
        log.info("Path B poller starting (interval=%ss, fast lane=%ss)",
                 self._interval, self._priority_interval)
        if history:
            asyncio.get_running_loop().create_task(self._history_loop())
        if self._priority_interval > 0:
            asyncio.get_running_loop().create_task(self._priority_loop())
        while True:
            whales = await self.tracked_whales()
            if not whales:
                await heartbeat("poller", "idle", {"reason": "empty roster"})
                await asyncio.sleep(self._interval)
                continue
            stagger = self._interval / len(whales)
            for whale in whales:
                try:
                    new = await self.poll_wallet(whale)
                    self._consecutive_failures = 0
                    await heartbeat("poller", "ok",
                                    {"last_wallet": whale["address"],
                                     "new": new,
                                     "detect_lag_s": self.last_lag_s})
                except Exception as exc:  # noqa: BLE001 — one bad wallet/payload
                    # must never kill live detection for the others
                    self._consecutive_failures += 1
                    log.warning("poll failed for %s: %s", whale["address"], exc)
                    await heartbeat(
                        "poller", "error", {"failures": self._consecutive_failures, "error": str(exc)}
                    )
                    if self._consecutive_failures == self._fail_threshold and self.on_alert:
                        await self.on_alert(
                            f"⚠️ Poll cycle failed {self._consecutive_failures}× — Path B degraded"
                        )
                await asyncio.sleep(stagger)
