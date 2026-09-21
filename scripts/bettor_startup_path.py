#!/usr/bin/env python3
"""THE STARTUP PATH, THROUGH THE WORKER'S OWN ENTRY POINT.

    python3 scripts/bettor_startup_path.py

WHAT WAS MISSING. `bettor_live_harness.py` reported 16 decisions over
books it handed the loop directly. The universe selector accepts ZERO
of those 16 -- they fail on spread, on price and on having no traded
volume figure -- so the harness demonstrated downstream processing with
supplied inputs. It never ran discovery, never ran selection, and never
subscribed to anything it had chosen.

AND WHAT THIS RUNNER ITSELF GOT WRONG. Its first version fixed that by
paging a listing built from 250 CAPTURED OBSERVATION ROWS -- our own
schema, carrying `yes_bid`, `yes_ask` and `stats_shares_traded`, all
spellings `bettor_universe.assess` happens to accept. So it passed,
and production did not: the venue's real `MarketDetail` listing has
none of those fields, and on 2026-09-21 the worker excluded all 3,000
listed markets as ONE_SIDED_BOOK, every five seconds, until it was
deregistered. A runner whose inputs are shaped like our database
proves nothing about a worker whose inputs come from the venue.

SO THE INPUTS ARE NOW THE ENDPOINTS' OWN SHAPES:

    markets.list   `MarketDetail` and nothing more -- slug, outcome,
                   active, closed, volume. NO quote, NO state, NO
                   sharesTraded.
    markets.bbo    `{"marketData": {...}}`, VERBATIM HTTP 200 bodies
                   from the capture archive. Wrapped, as the real
                   responses are; the SDK's flat `MarketBBO` is not
                   what the venue sends.
    the socket     the paired VERBATIM `/book` bodies, which already
                   ARE the websocket payload shape.

WHAT THIS RUNS. `bettor_live_loop.main()`, the same function
`workers/all.py` calls, with three seams:

    client          the two read endpoints above
    stream_factory  a transport that replays the REAL captured books
    control_pool    the database stop control, which fails closed

Everything between them is the worker: `_discover` pages the listing,
`bettor_universe_probe` enriches a bounded, deterministically rotated
batch, `bettor_universe.select` applies the FROZEN rule to rows that
finally contain what it asks for, `build` subscribes at the documented
100-market ceiling, the stream's own handler parses the payloads,
eligibility applies both clock bounds, and the engine decides.

THE RULE IS NOT RELAXED. If the universe comes out empty, `main()`
refuses to start and this script reports the exclusion histogram. That
is section 2, run deliberately against a listing that cannot produce a
universe.

THE CLOCK IS OURS, THE BOOK IS THE VENUE'S. Section 3 replays with the
captured `transactTime` untouched: every book is a day old and every
one is refused as STALE_SOURCE, which is the freshness gate working.
Section 4 rebases the transport clock so the decision path runs, and
every record it produces is stamped REPLAY_DECISION -- by the
TRANSPORT, not by this script. A file is not a feed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "backend")

from sportsassets import bettor_live_store as store_mod   # noqa: E402
from sportsassets import bettor_market_stream as ms       # noqa: E402
from sportsassets import bettor_universe as uni           # noqa: E402
from sportsassets.workers import bettor_live_loop as bl   # noqa: E402

PAIRS = "research/beta48/acceptance/bbo_book_real_400.json"
FAILS: list = []


class RunPool:
    """The database stop control, saying observation may run.

    The loop reads `ingestion_state` before it does anything else and
    FAILS CLOSED, so a runner that wants to reach discovery has to
    supply a control that says yes -- exactly as production will.
    """

    async def fetchval(self, _sql, *_a):
        return "true"


class StopPool:
    def __init__(self, value=None, raises=None):
        self.value, self.raises = value, raises

    async def fetchval(self, _sql, *_a):
        if self.raises is not None:
            raise self.raises
        return self.value


def rule(t=""):
    print("\n" + "=" * 76)
    if t:
        print(t)
        print("=" * 76)


def check(label, got, want):
    ok = got == want
    print("      %-48s %-16s expected %-16s %s"
          % (label, got, want, "OK" if ok else "*** MISMATCH ***"))
    if not ok:
        FAILS.append(label)


def atleast(label, got, floor):
    ok = got >= floor
    print("      %-48s %-16s expected >= %-13s %s"
          % (label, got, floor, "OK" if ok else "*** MISMATCH ***"))
    if not ok:
        FAILS.append(label)


# ── the venue's listing, paginated ───────────────────────────────────

class FakeMarkets:
    """The venue's two read endpoints, in the shapes it really answers.

    `list` ANSWERS `MarketDetail` AND NOTHING MORE. This is the whole
    point of the rewrite: the listing carries `id, slug, title,
    outcome, description, active, closed, liquidity, volume,
    eventSlug` and NO quote, NO state and NO traded-share counter. The
    previous version of this runner handed the worker CAPTURED
    OBSERVATION ROWS -- our own schema, carrying `yes_bid`, `yes_ask`
    and `stats_shares_traded` -- which the selection rule also accepts.
    It therefore proved the rule against a payload production never
    delivers, and on 2026-09-21 production excluded all 3,000 listed
    markets as ONE_SIDED_BOOK.

    `bbo` answers `{"marketData": {...}}` -- the WRAPPED form real
    responses use, not the flat `MarketBBO` the SDK declares -- and the
    object inside is a VERBATIM captured body.

    Pagination is real: the worker asks for a page, gets `limit` rows,
    and asks again until a short page ends it or its page bound is hit.
    """

    def __init__(self, bodies):
        self.bodies = {b["marketSlug"]: b for b in bodies}
        self.rows = [_listing_row(b) for b in bodies]
        self.calls: list = []
        self.bbo_calls: list = []

    def list(self, params):
        limit = int(params.get("limit") or 500)
        offset = int(params.get("offset") or 0)
        page = self.rows[offset:offset + limit]
        self.calls.append({"limit": limit, "offset": offset,
                           "returned": len(page)})
        return {"markets": page}

    def bbo(self, slug):
        self.bbo_calls.append(slug)
        body = self.bodies.get(slug)
        return {"marketData": body} if body is not None else None


def _listing_row(body):
    """A `MarketDetail`, built from what a listing CAN know.

    Deliberately lossy: the quote, the state and the counter that the
    rule needs are dropped, because the real listing does not have
    them. `volume` is present because `MarketDetail` has one -- and it
    is NOT `sharesTraded`, which is exactly why nothing is allowed to
    substitute it.
    """
    return {"id": abs(hash(body["marketSlug"])) % 10 ** 7,
            "slug": body["marketSlug"],
            "title": body["marketSlug"].replace("-", " "),
            "outcome": "over",
            "active": True, "closed": False, "archived": False,
            "liquidity": 1000.0, "volume": 5000.0,
            "eventSlug": body["marketSlug"].rsplit("-", 1)[0]}


class FakeClient:
    def __init__(self, bodies):
        self.markets = FakeMarkets(bodies)


# ── the transport, replaying real books ──────────────────────────────

class ReplayStream(ms.MarketStream):
    """The REAL stream object, fed from a file instead of a socket.

    Everything that matters is inherited: `_on_market_data` parses the
    payload, `book_at` applies both clock bounds and the epoch rule,
    `subscribe`/`prune`/`stats` are untouched. Only `start` and `stop`
    are replaced, because there is no socket in this container.
    """

    # NOT PROSPECTIVE_SHADOW. A file is not a feed, and the record says
    # so because the TRANSPORT says so.
    evidence_class = "REPLAY_DECISION"

    payloads: dict = {}
    rebase: bool = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._thread = None
        self._stop = threading.Event()
        self.replayed = 0

    def start(self):
        self.epoch += 1
        self.connected = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.connected = False

    def _run(self):
        for slug in list(self._subs):
            if self._stop.is_set():
                break
            msg = self.payloads.get(slug)
            if msg is None:
                continue
            if self.rebase:
                # THE TRANSPORT CLOCK, NOT THE BOOK. The prices, sizes,
                # ladders and state are the venue's, unmodified. Only
                # `transactTime` is moved, because a captured book is
                # older than the freshness bound by construction and
                # section 3 already showed what that does.
                msg = json.loads(json.dumps(msg))
                msg["marketData"]["transactTime"] = (
                    datetime.now(timezone.utc)
                    - timedelta(seconds=1)).isoformat()
            self._on_market_data(msg)
            self.replayed += 1


def as_payload(book_md):
    """A VERBATIM captured `/book` marketData -> the websocket shape.

    `_MarketDataPayload` is `marketSlug, bids, offers, state, stats,
    transactTime` -- `offers`, not `asks`; the payload has no `asks`
    key. A real book body already IS that shape, so nothing is
    constructed here: the ladders, their `px`/`qty`, the state and
    `stats.sharesTraded` are the venue's own, passed straight through.
    """
    return {"marketData": dict(book_md)}


# ── a run of main() ──────────────────────────────────────────────────

def run_main(pairs, *, page_size, pages, rebase, run_for_s=1.5,
             store=None, control_pool=None):
    client = FakeClient([p["bbo"] for p in pairs])
    ReplayStream.payloads = {p["bbo"]["marketSlug"]: as_payload(p["book"])
                             for p in pairs}
    ReplayStream.rebase = rebase
    os.environ["BETTOR_LIVE_DISCOVERY_PAGE_SIZE"] = str(page_size)
    os.environ["BETTOR_LIVE_DISCOVERY_PAGES"] = str(pages)
    made: dict = {"client": client}

    real_build = bl.build

    def capture(*a, **kw):
        loop, stream = real_build(*a, **kw)
        made["loop"], made["stream"] = loop, stream
        return loop, stream

    bl.build = capture
    try:
        bl._reset_backoff()
        out = asyncio.run(bl.main(client=client,
                                  stream_factory=ReplayStream,
                                  store=store or store_mod.FileStore(
                                      _tmpdir(), durable_across_redeploy=True),
                                  control_pool=control_pool or RunPool(),
                                  run_for_s=run_for_s))
    finally:
        bl.build = real_build
    made["out"] = out
    return made


def _tmpdir():
    import tempfile
    return tempfile.mkdtemp(prefix="bettor-startup-")


class Cfg:
    pmus_key_id = "startup-path-runner"
    pmus_secret_key = "startup-path-runner"


def main() -> int:
    import sportsassets.config as cfgmod
    cfgmod.settings = lambda: Cfg()     # noqa: E731 -- no real credential

    doc = json.load(open(PAIRS))
    rows = doc["pairs"]
    rule("BETTOR STARTUP PATH  |  listing -> ENRICHMENT -> selection -> "
         "subscription -> decisions")
    print("   ENTRY      bettor_live_loop.main(), the function "
          "workers/all.py calls")
    print("   LISTING    %d MarketDetail rows -- slug, outcome, active, "
          "closed, volume.\n              NO quote, NO state, NO "
          "sharesTraded. That is the real shape." % len(rows))
    print("   ENRICHED   markets.bbo(slug)['marketData'], %d VERBATIM "
          "HTTP 200 bodies\n              from %s"
          % (len(rows), doc["_source"]))
    print("   BOOKS      the paired VERBATIM /book bodies, replayed as "
          "the websocket\n              shape they already are")
    print("   CORPUS     %s" % json.dumps(doc["_corpus_totals"]))
    print("   SEAMS      an injected client, transport and control pool."
          "\n              Production passes none of them.")

    # ── 1 ────────────────────────────────────────────────────────────
    rule("1. PAGINATED DISCOVERY, ENRICHMENT, AND THE FROZEN RULE")
    made = run_main(rows, page_size=100, pages=6, rebase=True)
    out, client = made["out"], made["client"]
    disc = out["report"]["discovery"]
    print("   listing calls: %s" % json.dumps(client.markets.calls))
    print("   discovery    : %s" % json.dumps(
        {k: disc[k] for k in ("pages_read", "rows_listed", "considered",
                              "eligible", "selected", "page_size",
                              "listing_truncated_at_page_bound")}))
    print("   excluded     : %s" % json.dumps(disc["excluded_by_reason"]))
    check("the worker started", out["started"], True)
    check("pages actually requested", len(client.markets.calls), 5)
    check("pages the worker reports reading", disc["pages_read"], 5)
    check("rows listed across pages", disc["rows_listed"], 400)
    check("a short final page ended pagination",
          disc["listing_truncated_at_page_bound"], False)
    check("every candidate was ENRICHED before the rule saw it",
          disc["coverage"]["distinct_enriched"], 400)
    check("BBO reads issued", len(client.markets.bbo_calls), 480)
    check("markets considered", disc["considered"], 400)
    check("markets SELECTED by the frozen rule", disc["selected"], 34)
    check("every exclusion is counted",
          sum(disc["excluded_by_reason"].values()), 400 - 34)
    check("the rule is the frozen one", disc["universe"],
          uni.UNIVERSE_VERSION)

    # ── 2 ────────────────────────────────────────────────────────────
    rule("2. SUBSCRIPTION TO WHAT WAS CHOSEN, AND ONLY THAT")
    stream = made["stream"]
    rep = out["report"]["stream"]
    print("   stream       : %s" % json.dumps(
        {k: rep[k] for k in ("connected", "books", "epoch")
         if k in rep}))
    print("   subscriptions: %s" % json.dumps(rep.get("subscriptions")))
    check("markets subscribed", len(stream._subs), 34)
    check("subscribed set == selected set",
          sorted(stream._subs) == sorted(disc["slugs"]), True)
    subs = rep.get("subscriptions") or {}
    check("subscriptions confirmed by arriving data", subs.get("confirmed"),
          34)
    check("subscriptions that failed", subs.get("failed"), 0)
    print("   There is no positive acknowledgment in this protocol: "
          "MarketMessage\n   carries no 'subscribed' reply, so the "
          "arrival of data IS the\n   confirmation.")
    print("   The ceiling is %d per subscription, documented by the "
          "venue;\n   34 is what the rule chose, not what the cap "
          "allowed." % uni.MAX_MARKETS)

    # ── 3 ────────────────────────────────────────────────────────────
    rule("3. AN EMPTY UNIVERSE IS REPORTED, NEVER BYPASSED")
    # The BBO bodies are the venue's, with ONE counter overwritten: a
    # traded-share figure under the floor. Everything the rule needs is
    # still present and still real, so the universe empties for an
    # ECONOMIC reason and not because a field went missing.
    thin = [{"bbo": dict(p["bbo"], sharesTraded="1.0000"),
             "book": p["book"]} for p in rows]
    made2 = run_main(thin, page_size=100, pages=6, rebase=True)
    o2 = made2["out"]
    print("   result       : %s" % json.dumps(
        {k: o2.get(k) for k in ("started", "why", "considered",
                                "candidates", "pages_read")}))
    print("   coverage     : %s" % json.dumps(o2.get("coverage")))
    print("   excluded     : %s" % json.dumps(o2.get("excluded_by_reason")))
    check("it refuses to start", o2["started"], False)
    check("and names why", o2["why"], "EMPTY_UNIVERSE")
    check("every candidate was still ENRICHED",
          o2["coverage"]["distinct_enriched"], 400)
    check("volume is the binding reason",
          o2["excluded_by_reason"].get("TRADED_VOLUME_BELOW_MIN"),
          34 + 3)
    check("NOT ONE_SIDED_BOOK -- nothing was missing",
          o2["excluded_by_reason"].get("ONE_SIDED_BOOK"), 12)
    print("   MIN_SHARES_TRADED is still %.0f. Nothing was relaxed to "
          "find\n   something to watch -- and COVERAGE is reported "
          "beside the verdict, so\n   'we did not look' can never be "
          "read as 'the market is one-sided'."
          % uni.MIN_SHARES_TRADED)

    # ── 4 ────────────────────────────────────────────────────────────
    rule("4. THE CAPTURED CLOCK, UNTOUCHED -- the freshness gate")
    made3 = run_main(rows, page_size=250, pages=6, rebase=False)
    o3 = made3["out"]["report"]
    print("   ineligible   : %s" % json.dumps(o3["ineligible_by_reason"]))
    print("   by_action    : %s" % json.dumps(o3["by_action"]))
    atleast("books refused as stale at the venue clock",
            o3["ineligible_by_reason"].get(ms.STALE_SOURCE, 0), 1)
    check("decisions taken on a day-old book", o3["by_action"], {})
    print("   These are the SAME books section 5 decides on. The only\n"
          "   difference is the transport clock, and a %.0f s bound is\n"
          "   why a file cannot pretend to be a feed."
          % bl.MAX_SOURCE_AGE_S)

    # ── 5 ────────────────────────────────────────────────────────────
    rule("5. DECISIONS, WITH THE TRANSPORT CLOCK REBASED")
    o1 = out["report"]
    print("   books examined: %s" % o1["counters"].get("books_examined"))
    print("   by_action     : %s" % json.dumps(o1["by_action"]))
    print("   rejected      : %s" % json.dumps(o1["rejected_by_reason"]))
    print("   blockers      : %s" % json.dumps(o1["blockers"]))
    print("   freshness     : %s" % json.dumps(o1["freshness"]))
    atleast("books examined", o1["counters"].get("books_examined", 0), 18)
    check("markets decided on", len(o1["by_action"]), 1)
    check("every action taken is a refusal",
          set(o1["by_action"]) - {"NO_TRADE"}, set())
    check("nothing rejected for want of an outcome leg",
          o1["rejected_by_reason"].get("NO_OUTCOME_IDENTITY", 0), 0)
    check("orders submitted", o1["orders_submitted"], 0)
    check("durable records written",
          o1["durability"]["records_written"] > 0, True)
    check("persist failures", o1["durability"]["persist_failures"], 0)

    # ── 6 ────────────────────────────────────────────────────────────
    rule("6. EVERY RECORD SAYS WHAT KIND OF EVIDENCE IT IS")
    loop = made["loop"]
    classes = {r.get("evidence_class") for r in loop.records}
    print("   evidence classes in this run: %s" % sorted(classes))
    check("the transport, not the loop, sets the class",
          classes, {"REPLAY_DECISION"})
    check("and it is never PROSPECTIVE_SHADOW",
          "PROSPECTIVE_SHADOW" in classes, False)
    print("   `ms.MarketStream.evidence_class` is PROSPECTIVE_SHADOW and "
          "this\n   transport overrides it. The loop used to hard-code "
          "the live\n   value, so a replay would have been journalled "
          "as observation.")

    # ── summary ──────────────────────────────────────────────────────
    # ── 7 ────────────────────────────────────────────────────────────
    rule("7. THE STOP CONTROL, AND EVERY WAY IT SAYS NO")
    print("   BETTOR_LIVE_LOOP=off was set and acknowledged on "
          "2026-09-21, the\n   service was demonstrably restarted "
          "(server_restarted 22:43:40.131647Z),\n   and the restarted "
          "process still ran discovery. The stop now lives in\n   "
          "ingestion_state, where changing it needs no deploy.")
    for label, pool, why in (
            ("an explicit false", StopPool("false"), "STOPPED_BY_CONTROL"),
            ("an absent row", StopPool(None), "CONTROL_ROW_ABSENT"),
            ("an unparsable value", StopPool("maybe"), "CONTROL_MALFORMED"),
            ("a database that will not answer",
             StopPool(raises=ConnectionError("down")),
             "CONTROL_UNREADABLE")):
        made7 = run_main(rows, page_size=100, pages=6, rebase=True,
                         control_pool=pool)
        o7 = made7["out"]
        check("%-32s -> refuses" % label, o7["started"], False)
        check("%-32s -> names why" % label, o7["why"], why)
        check("%-32s -> no venue read at all" % label,
              len(made7["client"].markets.calls)
              + len(made7["client"].markets.bbo_calls), 0)
    print("   ONE way to run, FOUR ways to stop. A stopped loop costs "
          "nothing:\n   no listing page, no BBO read, no schema "
          "initialization.")

    rule("WHAT THIS RUN IS")
    print("   REAL       %d MarketDetail listing rows carrying NO quote, "
          "NO state\n              and NO sharesTraded -- the shape the "
          "venue really answers;\n              %d VERBATIM HTTP 200 "
          "/bbo bodies as the enrichment; their\n              paired "
          "VERBATIM /book bodies with real ladders, clocks and\n"
          "              counters; the frozen universe rule; the real "
          "stream\n              handler and eligibility; the real "
          "engine and its refusals."
          % (len(rows), len(rows)))
    print("   REAL CODE  bettor_live_loop.main(), start to finally.")
    print("   REPLACED   the socket and the listing HTTP call. Nothing "
          "between\n              them.")
    print("   UNPROVEN   that the socket connects, that the venue "
          "accepts a\n              subscription, and the venue's real "
          "update rate. Those\n              need the wire.")
    print("   ORDERS     0.")

    rule()
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("ALL INDEPENDENT ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
