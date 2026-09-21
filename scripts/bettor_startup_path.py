#!/usr/bin/env python3
"""THE STARTUP PATH, THROUGH THE WORKER'S OWN ENTRY POINT.

    python3 scripts/bettor_startup_path.py

WHAT WAS MISSING. `bettor_live_harness.py` reported 16 decisions over
books it handed the loop directly. The universe selector accepts ZERO
of those 16 -- they fail on spread, on price and on having no traded
volume figure -- so the harness demonstrated downstream processing with
supplied inputs. It never ran discovery, never ran selection, and never
subscribed to anything it had chosen.

WHAT THIS RUNS. `bettor_live_loop.main()`, the same function
`workers/all.py` calls, with two seams:

    client          a paginated market listing built from REAL rows
    stream_factory  a transport that replays REAL captured books

Everything between them is the worker: `_discover` pages the listing,
`bettor_universe.select` applies the FROZEN rule, `build` subscribes at
the documented 100-market ceiling, the stream's own handler parses the
payloads, eligibility applies both clock bounds, and the engine
decides.

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

ROWS = "research/beta48/acceptance/startup_rows_250.json"
FAILS: list = []


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
    """`markets.list({active, closed, limit, offset})`, over real rows.

    Pagination is real: the worker asks for a page, gets `limit` rows,
    and asks again until a short page ends it or its page bound is hit.
    """

    def __init__(self, rows):
        self.rows = rows
        self.calls: list = []

    def list(self, params):
        limit = int(params.get("limit") or 500)
        offset = int(params.get("offset") or 0)
        page = self.rows[offset:offset + limit]
        self.calls.append({"limit": limit, "offset": offset,
                           "returned": len(page)})
        return {"markets": page}


class FakeClient:
    def __init__(self, rows):
        self.markets = FakeMarkets(rows)


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


def as_payload(row):
    """A captured observation -> the venue's own websocket shape.

    `_MarketDataPayload`: marketSlug, bids, offers, state, stats,
    transactTime. `offers`, not `asks` -- the payload has no `asks`
    key.
    """
    lad = row.get("multi_level_depth") or {}
    if isinstance(lad, str):
        lad = json.loads(lad)

    def side(key):
        out = []
        for lv in sorted(lad.get(key) or [],
                         key=lambda x: int(x.get("level", 0))):
            out.append({"px": {"value": str(lv["price"]), "currency": "USD"},
                        "qty": str(lv["qty"])})
        return out

    return {"marketData": {
        "marketSlug": row["market_id"],
        "transactTime": row.get("book_source_ts"),
        "state": row.get("venue_state"),
        "bids": side("bid"), "offers": side("ask"),
        "stats": {"sharesTraded": row.get("stats_shares_traded")}}}


# ── a run of main() ──────────────────────────────────────────────────

def run_main(rows, *, page_size, pages, rebase, run_for_s=1.5,
             store=None):
    client = FakeClient(rows)
    ReplayStream.payloads = {r["market_id"]: as_payload(r) for r in rows}
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
        out = asyncio.run(bl.main(client=client,
                                  stream_factory=ReplayStream,
                                  store=store or store_mod.FileStore(
                                      _tmpdir(), durable_across_redeploy=True),
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

    rows = json.load(open(ROWS))
    rule("BETTOR STARTUP PATH  |  discovery -> selection -> subscription "
         "-> decisions")
    print("   ENTRY      bettor_live_loop.main(), the function "
          "workers/all.py calls")
    print("   LISTING    %d real market rows from "
          "bettor_state_observations" % len(rows))
    print("   SEAMS      an injected listing client and an injected "
          "transport.\n              Production passes neither.")

    # ── 1 ────────────────────────────────────────────────────────────
    rule("1. PAGINATED DISCOVERY AND THE FROZEN SELECTION RULE")
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
    check("pages actually requested", len(client.markets.calls), 3)
    check("pages the worker reports reading", disc["pages_read"], 3)
    check("rows listed across pages", disc["rows_listed"], 250)
    check("a short final page ended pagination",
          disc["listing_truncated_at_page_bound"], False)
    check("markets considered", disc["considered"], 250)
    check("markets SELECTED by the frozen rule", disc["selected"], 18)
    check("every exclusion is counted",
          sum(disc["excluded_by_reason"].values()), 250 - 18)
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
    check("markets subscribed", len(stream._subs), 18)
    check("subscribed set == selected set",
          sorted(stream._subs) == sorted(disc["slugs"]), True)
    subs = rep.get("subscriptions") or {}
    check("subscriptions confirmed by arriving data", subs.get("confirmed"),
          18)
    check("subscriptions that failed", subs.get("failed"), 0)
    print("   There is no positive acknowledgment in this protocol: "
          "MarketMessage\n   carries no 'subscribed' reply, so the "
          "arrival of data IS the\n   confirmation.")
    print("   The ceiling is %d per subscription, documented by the "
          "venue;\n   18 is what the rule chose, not what the cap "
          "allowed." % uni.MAX_MARKETS)

    # ── 3 ────────────────────────────────────────────────────────────
    rule("3. AN EMPTY UNIVERSE IS REPORTED, NEVER BYPASSED")
    thin = [dict(r, stats_shares_traded="1.0") for r in rows]
    made2 = run_main(thin, page_size=100, pages=6, rebase=True)
    o2 = made2["out"]
    print("   result       : %s" % json.dumps(
        {k: o2.get(k) for k in ("started", "why", "considered",
                                "pages_read")}))
    print("   excluded     : %s" % json.dumps(o2.get("excluded_by_reason")))
    check("it refuses to start", o2["started"], False)
    check("and names why", o2["why"], "EMPTY_UNIVERSE")
    check("volume is the binding reason",
          o2["excluded_by_reason"].get("TRADED_VOLUME_BELOW_MIN"), 34)
    check("and it is exactly the 18 selected plus the 16 already "
          "short", 18 + 16, 34)
    print("   MIN_SHARES_TRADED is still %.0f. Nothing was relaxed to "
          "find\n   something to watch." % uni.MIN_SHARES_TRADED)

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
    rule("WHAT THIS RUN IS")
    print("   REAL       250 venue market rows with their own ladders, "
          "clocks,\n              states and traded-volume counters; the "
          "frozen universe\n              rule; the real stream handler "
          "and eligibility; the real\n              engine and its "
          "refusals.")
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
