#!/usr/bin/env python3
"""WHAT PUBLIC OBSERVATION CAN MEASURE TODAY, AND WHAT IT CANNOT.

    python3 scripts/bettor_market_measures_run.py

THE CLAIM BEING TESTED. "Public book and trade observations can already
measure market activity, hypothetical quote markouts and opportunity
persistence. Those are not fill-conditioned results, but they can
eliminate weak policies now."

That is correct in principle, and this script runs those measures. It
also reports the reason every one of them comes back NOT_IDENTIFIED on
the data we currently hold, which is not a property of the measures:

    every one of them needs the SAME MARKET OBSERVED MORE THAN ONCE.

Measured 2026-09-21 against the production replica
(`research/bettor_counter_truth.sql`, run 35646334184):

    620   markets carrying a numeric traded-volume counter
    623   observations of them, over a 24.24-hour window
    617   of those markets observed EXACTLY ONCE
      3   consecutive observation pairs in the whole table
    0.9s  their summed separation

So the capture is a COVERAGE SAMPLER, not a time series. It answers
"what did the venue look like across many markets at one moment" and
cannot answer "what did this market do next". No differencing, no
markout and no persistence run is possible on it, and the $396,361/day
figure derived from it by taking max() of a counter and multiplying by
max(mid) is therefore withdrawn -- section 4.

`bettor_live_loop` is the instrument that closes this. It decides on
every book update for every subscribed market and journals bid, ask,
the traded-volume counter and three clocks, so its journal IS the
series these measures need. Section 5 runs the measures over a
journal the worker itself produced, to show the pipeline works the
moment the data exists.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "backend")

from sportsassets import bettor_live_store as store_mod         # noqa: E402
from sportsassets import bettor_market_measures as mm           # noqa: E402
from sportsassets import bettor_market_stream as ms             # noqa: E402
from sportsassets.workers import bettor_live_loop as bl         # noqa: E402

SERIES = "research/beta48/acceptance/observation_series_120.json"
STARTUP = "research/beta48/acceptance/startup_rows_250.json"
FAILS: list = []


def rule(t=""):
    print("\n" + "=" * 76)
    if t:
        print(t)
        print("=" * 76)


def check(label, got, want):
    ok = got == want
    print("      %-48s %-18s expected %-18s %s"
          % (label, got, want, "OK" if ok else "*** MISMATCH ***"))
    if not ok:
        FAILS.append(label)


def main() -> int:
    rule("BETTOR MARKET MEASURES  |  no order of ours is required")
    print("   MODULE     %s" % mm.MEASURES_VERSION)
    d = mm.describe()
    print("   CAN        %s" % "; ".join(d["measurable_without_our_orders"]))
    print("   CANNOT     %s"
          % "; ".join(d["not_measurable_without_our_orders"]))

    # ── 1 ────────────────────────────────────────────────────────────
    rule("1. THE CAPTURE, MEASURED FOR WHETHER IT CAN ANSWER ANYTHING")
    rows = json.load(open(SERIES))
    got = mm.measure_all(rows)
    cov = got["coverage"]
    print("   markets                 : %s" % cov["markets"])
    print("   observations            : %s" % cov["observations"])
    print("   markets seen once only  : %s" % cov["markets_seen_once_only"])
    print("   consecutive pairs       : %s" % cov["consecutive_pairs"])
    print("   observation gaps (s)    : %s" % json.dumps(cov["gap_seconds"]))
    print("   sufficient for a number : %s"
          % cov["sufficient_for_a_number"])
    check("this export is the venue's 120 busiest markets",
          cov["markets"], 120)
    check("and it holds one observation each, bar one",
          cov["markets_seen_once_only"], 119)
    check("so there is almost nothing to difference",
          cov["consecutive_pairs"], 1)
    check("which is below the threshold for a number",
          cov["sufficient_for_a_number"], False)
    print("   A measure over one observation per market is not a small")
    print("   sample. There is no interval, so there is no quantity.")

    # ── 2 ────────────────────────────────────────────────────────────
    rule("2. EACH MEASURE, AND THE REASON IT CANNOT RUN")
    for name in ("activity", "markout", "persistence"):
        m = got[name]
        print("   %-12s %s" % (name.upper(), m["evidence_class"]))
        why = m.get("why") or (
            m.get("horizons", {}).get("60s", {}).get("why"))
        if why:
            print("                %s" % why)
    check("activity", got["activity"]["evidence_class"], mm.NOT_IDENTIFIED)
    check("markout", got["markout"]["evidence_class"], mm.NOT_IDENTIFIED)
    check("persistence", got["persistence"]["evidence_class"],
          mm.NOT_IDENTIFIED)
    print("   NOT_IDENTIFIED with a reason, not zero. A zero here would")
    print("   read as 'the markets do not trade' when what it means is")
    print("   'we did not look twice'.")

    # ── 3 ────────────────────────────────────────────────────────────
    rule("3. THE TWO THAT GENUINELY NEED OUR ORDERS, SEPARATED")
    for q in got["still_needs_our_orders"]:
        print("      %s" % q)
    print("   These stay out of reach until a BETTOR order rests, and")
    print("   none of them is a reason to delay the three above.")
    check("adverse selection is not claimed as measured",
          got["markout"]["is_adverse_selection"], False)
    check("nor is persistence called a quote lifetime",
          got["persistence"]["is_quote_lifetime"], False)

    # ── 4 ────────────────────────────────────────────────────────────
    rule("4. THE DAILY CAPACITY FIGURE IS WITHDRAWN")
    print("   The old derivation, from research/bettor_traded_volume.sql:")
    print("      max(stats_shares_traded) per market  x  max(mid)")
    print("      summed over 409 markets  ->  $400,660")
    print("   Three independent reasons it is not a day's volume:")
    print("      1  the counter is never differenced, so the level")
    print("         measures the market's life, not any window;")
    print("      2  617 of 620 markets were observed ONCE, so it")
    print("         cannot be differenced even in principle here;")
    print("      3  shares were priced at max(mid) rather than at the")
    print("         price prevailing when they traded, so the dollar")
    print("         figure has no consistent unit basis.")
    print("   It is not an upper bound either: a lifetime counter can")
    print("   exceed a day, and a max() over mids can sit either side")
    print("   of the traded price.")
    print("   REPLACEMENT: none is asserted. A daily figure will come")
    print("   from counter differences over an established interval --")
    print("   which is what the worker's journal produces.")

    # ── 5 ────────────────────────────────────────────────────────────
    rule("5. THE SAME MEASURES OVER A JOURNAL THE WORKER PRODUCED")
    journal = _run_worker_and_journal()
    print("   journal records     : %s" % len(journal))
    out = mm.measure_all(journal)
    cov2 = out["coverage"]
    print("   markets             : %s" % cov2["markets"])
    print("   consecutive pairs   : %s" % cov2["consecutive_pairs"])
    print("   activity            : %s" % json.dumps(
        {k: out["activity"].get(k)
         for k in ("evidence_class", "pairs", "shares", "notional_usd",
                   "covered_seconds", "counter_went_backwards")}))
    h = out["markout"]["horizons"]["10s"]
    print("   markout 10s         : %s" % json.dumps(h))
    print("   persistence         : %s" % json.dumps(
        {k: out["persistence"].get(k)
         for k in ("evidence_class", "runs", "runs_censored",
                   "qualifying_observations")}))
    check("the worker's journal HAS intervals to difference",
          cov2["consecutive_pairs"] > 0, True)
    check("so ACTIVITY becomes measurable",
          out["activity"]["evidence_class"], mm.PUBLIC_COUNTER_DELTA)
    check("and no counter went backwards",
          out["activity"]["counter_went_backwards"], 0)
    print("   TWO OF THE THREE STILL SAY NOT_IDENTIFIED, and the reasons")
    print("   are properties of THIS REPLAY, not of the measures:")
    print("      MARKOUT      the rounds are %.2f s apart, so no pair"
          % RepeatStream.spacing_s)
    print("                   sits near the %s s horizons the protocol"
          % ", ".join("%g" % h for h in mm.MARKOUT_HORIZONS_S))
    print("                   brackets. A run long enough to straddle")
    print("                   them answers it; this one is 2 seconds.")
    print("      PERSISTENCE  all %d runs are CENSORED: nothing in this"
          % out["persistence"]["runs"])
    print("                   replay ever stops qualifying, so no run")
    print("                   ends and an uncensored duration does not")
    print("                   exist. A market leaving the universe is")
    print("                   what ends a run, and that needs real time.")
    print("   The point of the section is narrower than 'the measures")
    print("   work': it is that the journal IS a series and the capture")
    print("   is NOT, which is the whole difference between a measure")
    print("   that can run and one that cannot.")
    print("   The venue's real update rate is not claimed here.")

    rule("WHAT THIS RUN IS")
    print("   REAL       the venue's own book, ladder, state and traded-")
    print("              volume counter, for 120 and 250 real markets;")
    print("              the frozen universe rule; the real worker.")
    print("   MEASURED   that the capture cannot answer these questions,")
    print("              and exactly why.")
    print("   NOT SHOWN  any number for activity, markout or persistence")
    print("              on live venue data. That needs the socket.")
    print("   ORDERS     0.")

    rule()
    if FAILS:
        print("FAILED: %s" % ", ".join(FAILS))
        return 1
    print("ALL INDEPENDENT ASSERTIONS PASSED")
    return 0


# ── section 5's worker run ───────────────────────────────────────────

class RepeatStream(ms.MarketStream):
    """The real stream, replaying each market REPEATEDLY, over time.

    The worker decides on the LATEST book per slug each time it drains,
    so a transport that pushes every round at once produces one
    decision per market and no series at all. This one pushes a round,
    waits longer than the loop's poll interval, and pushes again --
    which is the shape a live feed has and the capture does not.

    The venue's real update rate is NOT known and is not claimed here;
    only that consecutive observations of one market exist.
    """

    evidence_class = "REPLAY_DECISION"
    payloads: dict = {}
    rounds: int = 6
    spacing_s: float = 0.35

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        self.epoch += 1
        self.connected = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, *, wait_s=0.0):
        self._stop.set()
        self.connected = False
        return {"closed": True, "thread_alive": False,
                "close_latency_s": 0.0, "waited_s": wait_s}

    def _run(self):
        for k in range(self.rounds):
            if self._stop.is_set():
                return
            at = (datetime.now(timezone.utc)
                  - timedelta(seconds=1)).isoformat()
            for slug in list(self._subs):
                msg = self.payloads.get(slug)
                if msg is None:
                    continue
                msg = json.loads(json.dumps(msg))
                msg["marketData"]["transactTime"] = at
                stats = msg["marketData"].setdefault("stats", {})
                try:
                    # The counter advances, because a market that trades
                    # is the case these measures exist for.
                    stats["sharesTraded"] = "%.4f" % (
                        float(stats.get("sharesTraded") or 0) + 25.0 * k)
                except (TypeError, ValueError):
                    pass
                self._on_market_data(msg)
            self._stop.wait(self.spacing_s)


def _run_worker_and_journal():
    """Run `main()` and read back what it wrote.

    NOT a simulation of a journal: the journal IS the worker's output,
    read from disk after the worker has stopped.
    """
    import sportsassets.config as cfgmod
    rows = json.load(open(STARTUP))

    class Cfg:
        pmus_key_id = "measures-runner"
        pmus_secret_key = "measures-runner"

    cfgmod.settings = lambda: Cfg()        # noqa: E731

    def as_payload(row):
        lad = row.get("multi_level_depth") or {}
        if isinstance(lad, str):
            lad = json.loads(lad)

        def side(key):
            return [{"px": {"value": str(lv["price"]), "currency": "USD"},
                     "qty": str(lv["qty"])}
                    for lv in sorted(lad.get(key) or [],
                                     key=lambda x: int(x.get("level", 0)))]

        return {"marketData": {
            "marketSlug": row["market_id"],
            "transactTime": row.get("book_source_ts"),
            "state": row.get("venue_state"),
            "bids": side("bid"), "offers": side("ask"),
            "stats": {"sharesTraded": row.get("stats_shares_traded")}}}

    RepeatStream.payloads = {r["market_id"]: as_payload(r) for r in rows}
    os.environ["BETTOR_LIVE_DISCOVERY_PAGE_SIZE"] = "250"
    d = tempfile.mkdtemp(prefix="bettor-measures-")
    store = store_mod.FileStore(d, durable_across_redeploy=True)
    # THE STOP CONTROL IS READ BEFORE ANYTHING ELSE AND FAILS CLOSED,
    # so a runner that wants the worker to start has to supply one that
    # says run -- exactly as production will, out of `ingestion_state`.
    class _RunControl:
        """Control and allowance, both open. The loop fails closed on
        either, so a runner has to supply both -- and the allowance is
        now RESERVED before each request, so this also has to offer the
        transaction surface `ctl.reserve` takes."""

        PROBE_ID = "77777777-6666-5555-4444-333333333333"

        def __init__(self):
            self.row = {
                "probe_id": self.PROBE_ID,
                "max_distinct": 10_000,
                "max_bbo_attempts": 100_000,
                "max_listing_attempts": 10_000,
                "distinct_reserved": 0,
                "bbo_attempts_reserved": 0,
                "listing_attempts_reserved": 0,
                "slugs": []}

        async def fetchval(self, _sql, *a):
            from sportsassets import bettor_live_control as _c
            if a and a[0] == _c.BUDGET_KEY:
                from datetime import datetime, timedelta, timezone
                now = datetime.now(timezone.utc)
                out = dict(self.row)
                out["started_at"] = now.isoformat()
                out["deadline_at"] = (now + timedelta(
                    hours=1)).isoformat()
                return json.dumps(out)
            return "true"

        async def execute(self, sql, *a):
            from sportsassets import bettor_live_control as _c
            if a and a[0] == _c.BUDGET_KEY and "jsonb_set" in sql:
                self.row[a[1]] = a[2]
                if len(a) > 3:
                    self.row.setdefault("slugs", []).append(a[3])
            return "OK"

        def acquire(self):
            pool = self

            class _Held:
                async def __aenter__(self):
                    return pool

                async def __aexit__(self, *a):
                    return False

            return _Held()

        def transaction(self):
            class _Txn:
                async def __aenter__(self):
                    return None

                async def __aexit__(self, *a):
                    return False

            return _Txn()

    out = asyncio.run(bl.main(client=_Client(rows),
                              stream_factory=RepeatStream, store=store,
                              control_pool=_RunControl(),
                              run_for_s=RepeatStream.rounds
                              * RepeatStream.spacing_s + 0.6))
    if not out.get("started"):
        raise SystemExit("the worker refused to start: %s"
                         % json.dumps(out, default=str))
    path = os.path.join(d, "decisions.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("status") == "DECIDED":
            out.append({"slug": r["market_id"], "at": r["source_ts"],
                        "bid": (r.get("top_of_book") or {}).get("bid"),
                        "ask": (r.get("top_of_book") or {}).get("ask"),
                        "vol": r.get("stats_shares_traded"),
                        "state": r.get("venue_state")})
    return out


class _Markets:
    """The venue's two read endpoints, in the shapes it really answers.

    `list` returns `MarketDetail` -- slug, outcome, active, closed --
    and NOT the quote, state or traded-share counter the selection rule
    needs. Those come from `bbo`, wrapped in `marketData` as the real
    responses are. The runner used to hand its capture rows straight to
    `list`, which is the defect that made the first live run select
    nothing.
    """

    def __init__(self, rows):
        self.rows = rows
        self.by_slug = {r["market_id"]: r for r in rows}

    def list(self, params):
        off = int(params.get("offset") or 0)
        lim = int(params.get("limit") or 500)
        page = self.rows[off:off + lim]
        return {"markets": [{"slug": r["market_id"],
                             "outcome": r.get("outcome_leg"),
                             "active": True, "closed": False}
                            for r in page]}

    def bbo(self, slug):
        r = self.by_slug.get(slug)
        if r is None:
            return None
        return {"marketData": {
            "marketSlug": slug,
            "bestBid": {"value": str(r.get("yes_bid")), "currency": "USD"},
            "bestAsk": {"value": str(r.get("yes_ask")), "currency": "USD"},
            "sharesTraded": str(r.get("stats_shares_traded")),
            "state": r.get("venue_state"),
            "bidDepth": 1, "askDepth": 1}}


class _Client:
    def __init__(self, rows):
        self.markets = _Markets(rows)


if __name__ == "__main__":
    raise SystemExit(main())
