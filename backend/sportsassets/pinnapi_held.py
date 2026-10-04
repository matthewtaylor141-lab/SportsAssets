"""HELD POSITIONS ARE THE PINNAPI PATH'S PRIORITY TARGETS (no socket, no
new owner, no new provider).

WHY XAVIER'S PROBABILITIES WERE 100-170 s OLD. Freshness is CHANGE-driven
(pinnapi_feed.FeedCache.read: the quote age is evaluated_ms -
source_change_ms, and only a delta that changes the price sets
source_change_ms; a snapshot or an unchanged re-send never restamps it --
and must not), while Xavier's reviews were CLOCK-driven (the paper backstop
every 60 s, the mirror's cadence every 60 s). Between price changes the
only stored readings for a held contract (the stored valuation rows) were written
by the 900 s collector cycle or by the reactive scheduler, which ignored a
held event's change unless the event was a live DISCOVERY seed of Derek's
current selection, queued it FIFO behind discovery (evicting the oldest)
and evaluated one at a time under a 12 s deadline -- so the row it wrote was
often born already older than 30 s. Nothing ever asked for a review when a
held market moved, so reviews landed long after the last change and read
the latest stored row (paper_benchmark.xavier_measure): 106 s, 173 s.

WHAT THIS DOES.
  targets    every OPEN paper and actual position's contract, resolved to
             its provider event in THIS process's feed cache with the
             census's own identity rules (pinnapi_feed_runtime.
             held_event_id: same event, full-game moneyline). Refreshed
             every HELD_REFRESH_S by the feed runtime's own task.
  priority   pinnapi_reactive.Scheduler serves held events FIRST (a held
             queue ahead of discovery, never evicted by discovery, held
             seeds pinned while held), on the SAME single worker and
             deadline: the existing budget and pacing, held events win it.
  triggers   a held market's price CHANGE on the feed is recorded per held
             slug (`changed_at`) and announced to listeners: the paper side
             runs a Xavier-only review of the holding groups at once
             (paper_runtime.schedule_held_review), the mirror reviews the
             actual position on its next 2 s tick (MARKET_EVENT). Each review
             then reads the in-process cache on demand (<= 1 s), fresh only
             when the source change is within 30 s and the identity matches.
  scope      a held event whose sport the feed does not subscribe is named
             (OUT_OF_FEED_SCOPE_SPORT) -- never silently fresh, never a
             resubscription here (the scope row is the owner's choice).

Nothing here restamps a price, invents a probability or places an order.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter

log = logging.getLogger(__name__)

HELD_REFRESH_S = 10.0
HELD_REFRESH_TIMEOUT_S = 8.0
MAX_HELD = 400

#: open paper positions (handed to Xavier, net long, not settled) and open
#: actual positions: the contracts Xavier holds now
HELD_SLUGS_SQL = """
    SELECT f.us_market_slug AS slug, 'PAPER' AS kind
      FROM paper_fills f JOIN paper_handoffs h ON h.group_id = f.group_id
     WHERE NOT EXISTS (SELECT 1 FROM paper_settlements s
                        WHERE s.group_id = f.group_id
                          AND s.us_market_slug = f.us_market_slug)
     GROUP BY f.group_id, f.us_market_slug
    HAVING sum(CASE WHEN f.direction = 'BUY' THEN f.qty ELSE -f.qty END) > 0
    UNION
    SELECT us_market_slug AS slug, 'ACTUAL' AS kind
      FROM smalllive_handoffs WHERE state = 'OPEN'
     LIMIT %d""" % MAX_HELD


class HeldWatch:
    """Held provider events and the last price change of each held slug.
    `changed` is the feed cache's synchronous change notification: bounded,
    never I/O (listeners must only schedule work)."""

    def __init__(self, *, clock=time.time):
        self.clock = clock
        self.targets: dict = {}        # feed event id -> set of held slugs
        self.slug_event: dict = {}     # held slug -> feed event id
        self.unmatched: dict = {}      # held slug -> named reason
        self.changes: dict = {}        # held slug -> provider change (s)
        self.listeners: list = []
        self.counts = Counter()
        self.refreshed_at = None

    # ── targets ──────────────────────────────────────────────────────
    def set_targets(self, resolved: dict) -> None:
        """`resolved`: slug -> (event_id or None, reason)."""
        targets, slug_event, unmatched = {}, {}, {}
        for slug, (eid, why) in resolved.items():
            if eid is None:
                unmatched[slug] = why
                continue
            targets.setdefault(eid, set()).add(slug)
            slug_event[slug] = eid
        self.targets, self.slug_event, self.unmatched = (targets, slug_event,
                                                         unmatched)
        # forget changes of slugs no longer held
        self.changes = {s: t for s, t in self.changes.items()
                        if s in slug_event}
        self.refreshed_at = self.clock()

    def is_held(self, event_id) -> bool:
        return event_id in self.targets

    def held_events(self) -> set:
        return set(self.targets)

    # ── changes ──────────────────────────────────────────────────────
    def changed(self, quote) -> None:
        from . import pinnapi_feed as F
        if quote.key != F.FULL_GAME_MONEYLINE_KEY:
            return
        # a live-phase child's change is its FIXTURE's change (R30A RC3):
        # the targets are fixture ids (held_event_id, census identity)
        fid = getattr(quote, "fixture_id", None) or quote.event_id
        slugs = self.targets.get(fid)
        change = getattr(quote, "change_ms", quote.source_change_ms)
        if not slugs or change is None:
            return
        at = float(change) / 1000.0
        for s in slugs:
            if at > self.changes.get(s, 0.0):
                self.changes[s] = at
        self.counts["HELD_CHANGES"] += 1
        for fn in list(self.listeners):
            try:
                fn(fid, sorted(slugs))
            except Exception:                                   # noqa: BLE001
                self.counts["HELD_LISTENER_ERRORS"] += 1

    def changed_at(self, slug) -> float | None:
        """The provider time of the held slug's last observed price change
        (None when none was observed while held)."""
        return self.changes.get(slug)

    def status(self) -> dict:
        return {"held_events": len(self.targets),
                "held_slugs": len(self.slug_event),
                "unmatched": dict(Counter(self.unmatched.values())),
                "unmatched_slugs": sorted(self.unmatched)[:20],
                "counts": dict(self.counts),
                "refreshed_at": self.refreshed_at,
                "refresh_s": HELD_REFRESH_S}


#: THE ONE WATCH of this process (the feed runtime installs it on the
#: owner's cache; the reactive scheduler and the mirror read it).
WATCH = HeldWatch()


def changed_at(slug) -> float | None:
    return WATCH.changed_at(slug)


async def held_slugs(conn) -> list:
    return sorted({r["slug"] for r in await conn.fetch(HELD_SLUGS_SQL)
                   if r["slug"]})


async def refresh(conn, *, watch: HeldWatch | None = None) -> dict:
    """Resolve every held slug to its feed event (bounded). Never raises."""
    from . import pinnapi_feed_runtime as FR
    w = watch or WATCH
    try:
        async with asyncio.timeout(HELD_REFRESH_TIMEOUT_S):
            slugs = await held_slugs(conn)
            resolved = {}
            for s in slugs:
                resolved[s] = await FR.held_event_id(conn, s)
    except Exception as exc:                                    # noqa: BLE001
        w.counts["REFRESH_FAILED"] += 1
        return {"ok": False, "why": type(exc).__name__}
    w.set_targets(resolved)
    return {"ok": True, **w.status()}


async def refresh_loop(pool, *, watch: HeldWatch | None = None,
                       every_s: float = HELD_REFRESH_S) -> None:
    while True:
        try:
            async with pool.acquire() as c:
                await refresh(c, watch=watch)
        except asyncio.CancelledError:
            raise
        except Exception:                                       # noqa: BLE001
            log.warning("held targets refresh failed", exc_info=True)
        await asyncio.sleep(every_s)


def paper_review_listener(event_id, slugs) -> None:
    """A held market moved: Xavier reviews the paper groups holding it now
    (paper_runtime.schedule_held_review: debounced, background, the paper
    pass's own locks). Scheduling only -- never I/O in the notification."""
    from .agents import paper_runtime as PR
    PR.schedule_held_review(slugs)


def add_listener(fn, *, watch: HeldWatch | None = None) -> None:
    w = watch or WATCH
    if fn not in w.listeners:
        w.listeners.append(fn)


def install(cache, *, watch: HeldWatch | None = None) -> None:
    """Chain the held watch into the cache's change notification, ahead of
    whatever was there (the reactive scheduler)."""
    w = watch or WATCH
    prev = getattr(cache, "on_change", None)
    if getattr(prev, "_held_chain", False):
        return

    def chained(quote):
        w.changed(quote)
        if prev is not None:
            prev(quote)
    chained._held_chain = True
    cache.on_change = chained
    cache._held_watch_installed = True


def reinstall_if_installed(cache, *, watch: HeldWatch | None = None) -> None:
    """Keep the watch first after someone else replaced the notification --
    only on a cache the feed runtime installed it on."""
    if getattr(cache, "_held_watch_installed", False):
        install(cache, watch=watch)
