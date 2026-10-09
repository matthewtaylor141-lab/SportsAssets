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
             its provider event in THIS process's feed cache with the held
             reads' own identity rules (pinnapi_feed_runtime.held_event_id
             -> held_fixture: same event; a full-game moneyline or, RC6, a
             line contract whose family is proven; the entry-proven fixture
             where exact names find nothing). Refreshed every
             HELD_REFRESH_S by the feed runtime's own task.
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

from . import bettor_paper_ledger as L

log = logging.getLogger(__name__)

HELD_REFRESH_S = 10.0
HELD_REFRESH_TIMEOUT_S = 8.0
MAX_HELD = 400

#: open paper positions (handed to Xavier; CANONICALLY open: bought - sold -
#: the latest settlement qty > the ledger epsilon, per group / market /
#: holding side -- bettor_paper_ledger.CANONICAL_OPEN_POSITIONS_SQL) and open
#: actual positions: the contracts Xavier holds now
HELD_SLUGS_SQL = """
    SELECT DISTINCT c.us_market_slug AS slug, 'PAPER' AS kind
      FROM (%s) c JOIN paper_handoffs h ON h.group_id = c.group_id
    UNION
    SELECT us_market_slug AS slug, 'ACTUAL' AS kind
      FROM smalllive_handoffs WHERE state = 'OPEN'
     LIMIT %d""" % (L.CANONICAL_OPEN_POSITIONS_SQL, MAX_HELD)


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
        # held slug -> the latest PROVIDER-STAMPED confirmation of its
        # unchanged price (s); the held read admits it within the same 30 s
        # (pinnapi_feed.read_held), so it is a review trigger too
        self.confirms: dict = {}
        self._notified: dict = {}      # held slug -> last confirmation notice
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
        self.confirms = {s: t for s, t in self.confirms.items()
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
        if not slugs:
            return
        change = getattr(quote, "change_ms", quote.source_change_ms)
        moved = False
        if change is not None:
            at = float(change) / 1000.0
            for s in slugs:
                if at > self.changes.get(s, 0.0):
                    self.changes[s] = at
                    moved = True
            self.counts["HELD_CHANGES"] += 1
        conf = None
        if getattr(quote, "confirmed_ms", None) is not None and \
                getattr(quote, "confirmed_clock", None) == F.CLOCK_PROVIDER \
                and getattr(quote, "confirmed_by", None) in \
                F.PROVIDER_CONFIRMATIONS:
            conf = float(quote.confirmed_ms) / 1000.0
            for s in slugs:
                if conf > self.confirms.get(s, 0.0):
                    self.confirms[s] = conf
        if conf is not None and not moved:
            # a confirmation notifies at most once per CONFIRM_NOTICE_S per
            # slug: Xavier reviews right after a provider frame, bounded
            due = [s for s in slugs if conf - self._notified.get(s, 0.0)
                   >= CONFIRM_NOTICE_S]
            if not due:
                return
            for s in due:
                self._notified[s] = conf
            self.counts["HELD_CONFIRMATIONS_NOTIFIED"] += 1
        elif change is None:
            return
        for fn in list(self.listeners):
            try:
                fn(fid, sorted(slugs))
            except Exception:                                   # noqa: BLE001
                self.counts["HELD_LISTENER_ERRORS"] += 1

    def changed_at(self, slug) -> float | None:
        """The provider time of the held slug's last observed price change
        (None when none was observed while held)."""
        return self.changes.get(slug)

    def fresh_at(self, slug) -> float | None:
        """The provider time from which the held slug's price is current:
        its last observed change or its latest provider-stamped confirmation
        of the unchanged price, whichever is later (the review trigger)."""
        xs = [x for x in (self.changes.get(slug), self.confirms.get(slug))
              if x is not None]
        return max(xs) if xs else None

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


def fresh_at(slug) -> float | None:
    return WATCH.fresh_at(slug)


#: at most one confirmation-driven review notice per held slug per this many
#: seconds (a provider list re-sent every ~60 s gives one fresh review each)
CONFIRM_NOTICE_S = 20.0


async def held_slugs(conn) -> list:
    return sorted({r["slug"] for r in await conn.fetch(HELD_SLUGS_SQL)
                   if r["slug"]})


#: THE ENTRY-PROVEN PROVIDER FIXTURE OF EACH HELD PAPER CONTRACT (RC6): the
#: event key of the valuation the entry was decided on, when it names a
#: PinnAPI fixture (pinnapi_feed_runtime.ENTRY_FIXTURE_PREFIX), the newest
#: entry per contract. The held READ already resolves a fixture this way
#: where exact names find nothing (closeout: venue "Vila Nova" vs provider
#: "Vila Nova FC"); the watch now resolves the same fixture, so its changes
#: trigger the review (pinnapi_feed_runtime.held_fixture). Bounded like
#: HELD_SLUGS_SQL; read only.
HELD_ENTRY_FIXTURES_SQL = """
    SELECT DISTINCT ON (c.us_market_slug) c.us_market_slug AS slug,
           v.event_key AS entry_event_key
      FROM (%s) c
      JOIN paper_orders o ON o.group_id = c.group_id AND o.role = 'ENTRY'
                         AND o.us_market_slug = c.us_market_slug
      JOIN paper_decisions d ON d.decision_id = o.decision_id
      JOIN external_valuations v ON v.id = d.valuation_id
     WHERE v.event_key LIKE 'pinnapi:%%'
     ORDER BY c.us_market_slug, o.created_at DESC
     LIMIT %d""" % (L.CANONICAL_OPEN_POSITIONS_SQL, MAX_HELD)


async def held_entry_fixtures(conn, w: "HeldWatch | None" = None) -> dict:
    """{held slug: entry valuation event key 'pinnapi:<fixture id>'}. Never
    raises: an unreadable answer is counted and resolves nothing by it
    (exact names still apply, as before)."""
    try:
        rows = await conn.fetch(HELD_ENTRY_FIXTURES_SQL)
        return {r["slug"]: r["entry_event_key"] for r in rows
                if r.get("slug") and str(r.get("entry_event_key") or "")
                .startswith("pinnapi:")}
    except asyncio.CancelledError:
        raise
    except Exception:                                           # noqa: BLE001
        if w is not None:
            w.counts["ENTRY_FIXTURES_UNREAD"] += 1
        return {}


async def refresh(conn, *, watch: HeldWatch | None = None) -> dict:
    """Resolve every held slug to its feed event (bounded). Never raises."""
    from . import pinnapi_feed_runtime as FR
    w = watch or WATCH
    try:
        async with asyncio.timeout(HELD_REFRESH_TIMEOUT_S):
            slugs = await held_slugs(conn)
            resolved = {}
            # ONE feed event view per pass, not one per held slug (R30A: the
            # per-slug rebuild held the event loop 2.0 s -- see
            # pinnapi_feed_runtime.held_event_id). A view a few hundred ms
            # old matches the same events; the next pass re-reads.
            view = None
            o = FR._STATE.get("owner")
            entry = {}
            matched = {}
            if slugs and o is not None and o.cache.authority.synced:
                from . import pinnapi_census as C
                from . import xavier_held_fixture as XHF
                view = C.feed_event_view(o.cache)
                entry = await held_entry_fixtures(conn, w)
                # THE SAME FIXTURE THE HELD READ IS HANDED (RC6 xavier-
                # records, xavier_held_fixture): where the entry named no
                # PinnAPI fixture, the one the PinnAPI matcher recorded for
                # the same contract -- so a held position the read now
                # prices is a watch target too, and its changes trigger the
                # review. Bounded; an unreadable answer resolves nothing.
                got = await XHF.matched_fixtures(
                    conn, [s for s in slugs if s not in entry])
                if None in got:
                    w.counts["MATCHED_FIXTURES_UNREAD"] += 1
                matched = {s: XHF.PREFIX + str(r["feed_event_id"])
                           for s, r in got.items() if s is not None}
            for s in slugs:
                # the entry-proven fixture where the entry named one, else
                # the fixture the matcher recorded for the same contract
                k = entry.get(s) or matched.get(s)
                resolved[s] = await FR.held_event_id(
                    conn, s, view=view,
                    **({"entry_event_key": k} if k else {}))
    except Exception as exc:                                    # noqa: BLE001
        w.counts["REFRESH_FAILED"] += 1
        return {"ok": False, "why": type(exc).__name__}
    w.set_targets(resolved)
    return {"ok": True, **w.status()}


async def refresh_loop(pool, *, watch: HeldWatch | None = None,
                       every_s: float = HELD_REFRESH_S) -> None:
    from . import loop_health as LH
    while True:
        try:
            async with pool.acquire() as c:
                await refresh(c, watch=watch)
                # health at most every 30 s (loop_health record_every_s)
                await LH.record(c, "pinnapi_held.refresh", process="api",
                                phase=LH.SUCCESS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            log.warning("held targets refresh failed", exc_info=True)
            await LH.record(pool, "pinnapi_held.refresh", process="api",
                            phase=LH.ERROR, error=exc)
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
        # the next consumer (the reactive entry scheduler) sees CHANGES
        # only, exactly as before; confirmations are the held watch's
        if prev is not None and getattr(quote, "change_ms", None) is not None:
            prev(quote)
    chained._held_chain = True
    cache.on_change = chained
    cache._held_watch_installed = True


def reinstall_if_installed(cache, *, watch: HeldWatch | None = None) -> None:
    """Keep the watch first after someone else replaced the notification --
    only on a cache the feed runtime installed it on."""
    if getattr(cache, "_held_watch_installed", False):
        install(cache, watch=watch)
