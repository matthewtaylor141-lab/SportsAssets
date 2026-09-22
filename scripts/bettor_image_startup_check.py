"""THE DEPLOYMENT IMAGE, THE REAL ENTRY POINT, A MIGRATION-BUILT DATABASE.

This runs INSIDE the image built from backend/Dockerfile, from WORKDIR /app,
so `import sportsassets` resolves to /app/sportsassets -- the same copy
`sh start.sh` and `python -m sportsassets.workers.all` resolve.

WHAT IS REAL HERE
  * the image                  -- backend/Dockerfile, unmodified
  * the database               -- built by sportsassets.scripts.migrate,
                                  all 91 migrations, verified column-for-
                                  column against production
  * the entry point            -- bettor_live_loop.main() WITH NO ARGUMENTS,
                                  exactly as workers/all.py invokes it
  * settings, get_pool, every reservation and every control read
  * the BBO bodies             -- VERBATIM marketData from real HTTP 200
                                  responses (bbo_book_real_400.json)

WHAT IS REPLACED -- the external venue transport, and only that
  * pmus._get_client   -> a client serving the recorded REST bodies
  * ms.MarketStream._run -> the WebSocket thread, replaced by a replay of
                          recorded frames through the class's OWN
                          `_on_market_data`. `book_at`, the freshness
                          gates, the epoch and the capture path are the
                          production class, inherited untouched.

The LISTING leg has no verbatim recording: the capture corpus holds
/v1/markets/{slug}/bbo and /book only, never the collection endpoint. Its
envelope is therefore CONSTRUCTED in markets.list shape, from real slugs
taken out of the recorded corpus plus the fields the SDK's MarketDetail
declares. Said plainly so it is not mistaken for a recording.

No credential is needed or used: the transport never leaves the process.
"""
import asyncio
import json
import os
import sys
import time
import uuid

# Mounted into the container, never copied into the image: the recorded
# corpus is test scaffolding and has no business in a deployment artifact.
# backend/Dockerfile copies no scripts/ and no research/beta48/acceptance/,
# so neither this file nor the corpus ships. See the README beside the
# evidence for the exact `docker run -v` invocation.
RECORDED = os.environ.get("BETTOR_RECORDED_CORPUS",
                          "/recorded/bbo_book_real_400.json")

sys.path.insert(0, "/app")

import sportsassets.pmus as pmus                       # noqa: E402
from sportsassets import bettor_market_stream as ms    # noqa: E402
from sportsassets import bettor_live_control as ctl    # noqa: E402
from sportsassets.db import get_pool                   # noqa: E402
from sportsassets.workers import bettor_live_loop as bl  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print("   [%s] %-58s got %r" % ("PASS" if ok else "FAIL", label, got))
    if not ok:
        FAILS.append((label, got, want))
    return ok


def rule(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


# ── the recorded venue ───────────────────────────────────────────────
class RecordedMarkets:
    """Serves recorded BBO bodies and counts every call."""

    def __init__(self, n_markets=40):
        raw = json.load(open(RECORDED))
        pairs = raw["pairs"]
        usable = []
        for p in pairs:
            b = p.get("bbo") or {}
            bk = p.get("book") or {}
            bid, ask = b.get("bestBid"), b.get("bestAsk")
            if not bid or not ask:
                continue
            if not bk.get("bids") or not bk.get("offers"):
                continue
            try:
                lo, hi = float(bid["value"]), float(ask["value"])
            except (TypeError, ValueError, KeyError):
                continue
            # a real economic spread, not a one-tick book
            if hi - lo >= 0.03 and b.get("state") == "MARKET_STATE_OPEN":
                usable.append((b, bk))
        self.bodies = {}
        self.frames = {}
        self.order = []
        for i, (b, bk) in enumerate(usable[:n_markets]):
            slug = b.get("marketSlug") or "rec-%03d" % i
            if slug in self.bodies:
                continue
            self.bodies[slug] = b
            # the /book half of the SAME recorded pair, in exactly the
            # marketData shape _on_market_data parses
            self.frames[slug] = dict(bk, marketSlug=slug)
            self.order.append(slug)
        self.corpus_total = raw["_corpus_totals"]["bbo_http_200"]
        self.usable_total = len(usable)
        self.list_calls = 0
        self.bbo_calls = []

    def list(self, params):
        self.list_calls += 1
        if params.get("offset", 0) > 0:
            return {"markets": []}
        return {"markets": [
            # markets.list envelope, MarketDetail fields, REAL slugs.
            {"id": "id-%s" % s, "slug": s, "title": "Recorded %s" % s,
             "outcome": "yes", "description": "", "active": True,
             "closed": False, "liquidity": 1000.0, "volume": 5000.0,
             "eventSlug": "ev-%s" % s, "team": "T"}
            for s in self.order]}

    def bbo(self, slug):
        self.bbo_calls.append(slug)
        body = self.bodies.get(slug)
        if body is None:
            raise RuntimeError("no recorded body for %s" % slug)
        return {"marketData": body}          # the wrapper the venue sends

    def book(self, slug):
        return self.bbo(slug)

    def settlement(self, slug):
        return {"marketData": {}}

    @property
    def distinct(self):
        return len(set(self.bbo_calls))


class RecordedClient:
    def __init__(self, markets):
        self.markets = markets


class ReplayStream(ms.MarketStream):
    """THE REAL MarketStream with ONLY ITS SOCKET REPLACED.

    Everything that decides anything -- `_on_market_data`, `book_at`,
    `subscribe`, `prune`, `_capture_frame`, the epoch, the freshness
    gates -- is the production class, inherited untouched. The single
    override is `_run`, the thread that would open a WebSocket; it
    pushes recorded frames through the same `_on_market_data` the
    socket would call.

    `evidence_class` is REPLAY_DECISION, not PROSPECTIVE_SHADOW. The
    production class documents exactly this: "a replaying transport
    reading a file produces REPLAY_DECISION". Journalling replayed
    frames as live observation is the mistake that rule exists to stop.
    """

    evidence_class = "REPLAY_DECISION"
    made = []
    frames_by_slug = {}
    restamp = True

    def __init__(self, key_id, secret_key, on_book=None, on_trade=None,
                 autostart=False):
        super().__init__(key_id, secret_key, on_book=on_book,
                         on_trade=on_trade, autostart=False)
        self.replayed = 0
        ReplayStream.made.append(self)

    def _run(self):
        """Replaces the WebSocket thread. Same callback, no network."""
        self.epoch += 1
        self.connected = True
        self.connected_since = ms._now_iso()
        self.first_connected_at = self.connected_since
        while not self._stop:
            with self._lock:
                want = list(self._subs)
            for slug in want:
                md = ReplayStream.frames_by_slug.get(slug)
                if md is None:
                    continue
                md = dict(md)
                if ReplayStream.restamp:
                    # ONLY transactTime moves, to the replay clock.
                    # Every price, quantity, ladder level, state and
                    # counter below is exactly as the venue sent it.
                    md["transactTime"] = (
                        __import__("datetime").datetime.now(
                            __import__("datetime").timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%S.%f000Z"))
                self._on_market_data({"marketData": md})
                self.replayed += 1
            for _ in range(20):          # 2 s, checked at 100 ms
                if self._stop:
                    break
                time.sleep(0.1)
        self.connected = False

    @property
    def open(self):
        return self.connected


# ── the operator statements, verbatim from render-ops ────────────────
async def arm(pool, *, minutes=30):
    pid = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, "
        "jsonb_build_object('probe_id', $2::text,"
        " 'started_at', to_char(now() AT TIME ZONE 'UTC',"
        "   'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'),"
        " 'deadline_at', to_char((now() + ($3::text || ' minutes')"
        "   ::interval) AT TIME ZONE 'UTC',"
        "   'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'),"
        " 'max_distinct', 40, 'max_bbo_attempts', 160,"
        " 'max_listing_attempts', 18, 'distinct_reserved', 0,"
        " 'bbo_attempts_reserved', 0, 'listing_attempts_reserved', 0,"
        " 'slugs', '[]'::jsonb)) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        ctl.BUDGET_KEY, pid, str(minutes))
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, 'true'::jsonb)"
        " ON CONFLICT (key) DO UPDATE SET value = 'true'::jsonb",
        ctl.CONTROL_KEY)
    return pid


async def obs_stop(pool):
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, 'false'::jsonb)"
        " ON CONFLICT (key) DO UPDATE SET value = 'false'::jsonb",
        ctl.CONTROL_KEY)


async def budget(pool):
    v = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", ctl.BUDGET_KEY)
    return json.loads(v) if isinstance(v, str) else v


async def control(pool):
    v = await pool.fetchval(
        "SELECT value::text FROM ingestion_state WHERE key=$1",
        ctl.CONTROL_KEY)
    return v


async def journal_count(pool):
    return await pool.fetchval("SELECT count(*) FROM bettor_live_journal")


async def supervisor_run(timeout_s):
    """main() exactly as workers/all.py invokes it: NO ARGUMENTS."""
    task = asyncio.create_task(bl.main())
    try:
        return await asyncio.wait_for(task, timeout=timeout_s)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        return {"started": None, "why": "TEST_TIMEOUT"}


async def main():
    pool = await get_pool()

    print("image check: python %s" % sys.version.split()[0])
    print("package    : %s" % __import__("sportsassets").__file__)
    print("entry point: bettor_live_loop.main() with NO ARGUMENTS")
    print("database   : %s" % (await pool.fetchval("SELECT current_database()")))

    # keep the pace out of it: 0.25 req/s is asserted elsewhere and
    # spending 160 s of it here would prove nothing this check is about.
    os.environ["BETTOR_PROBE_MAX_RPS"] = "100000"
    os.environ["BETTOR_PROBE_CONCURRENCY"] = "4"
    ctl.CONTROL_EVERY_S = 1.0

    m = RecordedMarkets()
    ReplayStream.frames_by_slug = m.frames
    ms.MarketStream = ReplayStream
    bl.ms.MarketStream = ReplayStream

    pmus._get_client = lambda: RecordedClient(m)
    print("recorded   : %d verbatim BBO bodies in the corpus, %d usable, "
          "%d served" % (m.corpus_total, m.usable_total, len(m.order)))
    print("patched    : pmus._get_client and ms.MarketStream. NOTHING "
          "about the pool, settings or reservation path.")

    # ── S1 ───────────────────────────────────────────────────────────
    rule("S1. listing reservation -> BBO reservation -> selection -> "
         "persisted decision")
    ReplayStream.made.clear()
    before = await journal_count(pool)
    pid = await arm(pool, minutes=30)
    bl._reset_backoff()
    out = await supervisor_run(45)
    b = await budget(pool)
    after = await journal_count(pool)

    print("   result : started=%s why=%s" % (out.get("started"),
                                             out.get("why")))
    print("   venue  : %d listing request(s), %d BBO request(s) over %d "
          "distinct market(s)" % (m.list_calls, len(m.bbo_calls), m.distinct))
    print("   row    : listing=%s distinct=%s attempts=%s probe_id=%s"
          % (b["listing_attempts_reserved"], b["distinct_reserved"],
             b["bbo_attempts_reserved"], b["probe_id"][:8]))
    print("   journal: %d -> %d rows" % (before, after))
    sel = sorted({sg for f in ReplayStream.made for sg in f._subs})
    print("   selected: %d market(s) subscribed on the stream" % len(sel))
    print("   replayed: %d recorded frame(s) pushed through "
          "_on_market_data" % sum(f.replayed for f in ReplayStream.made))

    check("a listing attempt was RESERVED before the request",
          b["listing_attempts_reserved"] >= 1, True)
    check("and the listing request actually went out",
          m.list_calls >= 1, True)
    check("the listing reservation covers every listing request",
          b["listing_attempts_reserved"] >= m.list_calls, True)
    check("a distinct-market slot was RESERVED per market read",
          b["distinct_reserved"] == m.distinct, True)
    check("a BBO attempt was RESERVED per BBO request",
          b["bbo_attempts_reserved"] == len(m.bbo_calls), True)
    check("no ceiling was exceeded: distinct <= 40",
          b["distinct_reserved"] <= 40, True)
    check("no ceiling was exceeded: attempts <= 160",
          b["bbo_attempts_reserved"] <= 160, True)
    check("no ceiling was exceeded: listing <= 18",
          b["listing_attempts_reserved"] <= 18, True)
    check("the row carries the armed identity",
          b["probe_id"] == pid, True)
    check("the recorded bodies produced a non-empty selection",
          len(sel) > 0, True)
    check("a stream was opened and subscribed",
          any(f.epoch >= 1 and f._subs for f in ReplayStream.made), True)
    check("recorded frames reached the production _on_market_data",
          sum(f.updates for f in ReplayStream.made) > 0, True)
    check("a DECISION WAS PERSISTED to bettor_live_journal", after > before,
          True)
    for _ in range(60):
        if all(not f.connected for f in ReplayStream.made):
            break
        await asyncio.sleep(0.1)
    check("and every stream the run opened was closed",
          all(not f.connected for f in ReplayStream.made), True)

    rows = await pool.fetch(
        "SELECT status, record->>'evidence_class' AS ec, "
        "count(*) AS n FROM bettor_live_journal GROUP BY 1,2 "
        "ORDER BY n DESC")
    print("   journal rows by (status, evidence_class):")
    for r in rows:
        print("     %-12s %-20s %d" % (r["status"], r["ec"], r["n"]))
    classes = {r["ec"] for r in rows}
    check("every persisted decision is labelled REPLAY_DECISION, never "
          "PROSPECTIVE_SHADOW", classes, {"REPLAY_DECISION"})
    check("at least one decision passed the freshness gates",
          any(r["status"] not in ("INELIGIBLE",) for r in rows), True)
    check("MAX_CONTRACTS=0 held: no order was submitted",
          out.get("orders_submitted") in (None, 0), True)

    # ── S2 ───────────────────────────────────────────────────────────
    rule("S2. obs-stop -- the control refuses, no venue request is made")
    list_before, bbo_before = m.list_calls, len(m.bbo_calls)
    b_before = await budget(pool)
    await obs_stop(pool)
    bl._reset_backoff()
    out2 = await supervisor_run(60)
    b_after = await budget(pool)
    print("   result : started=%s why=%s" % (out2.get("started"),
                                             out2.get("why")))
    print("   venue  : listing %d -> %d, BBO %d -> %d"
          % (list_before, m.list_calls, bbo_before, len(m.bbo_calls)))
    check("the stopped loop did not start", out2.get("started"), False)
    check("it issued NO listing request", m.list_calls, list_before)
    check("it issued NO BBO request", len(m.bbo_calls), bbo_before)
    check("and it reserved NOTHING",
          (b_after["listing_attempts_reserved"],
           b_after["distinct_reserved"], b_after["bbo_attempts_reserved"]),
          (b_before["listing_attempts_reserved"],
           b_before["distinct_reserved"], b_before["bbo_attempts_reserved"]))
    check("the refusal is a CONTROL reason, so it polls rather than "
          "escalating", bl.is_control_reason(out2.get("why")), True)

    # ── S3 ───────────────────────────────────────────────────────────
    rule("S3. deadline -- an expired allowance refuses, and no request "
         "is made")
    await arm(pool, minutes=30)
    # move the deadline into the past, the way 1,800 s of wall clock would
    await pool.execute(
        "UPDATE ingestion_state SET value = jsonb_set(value, "
        "'{deadline_at}', to_jsonb(to_char((now() - interval '1 second')"
        " AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'))) "
        "WHERE key=$1", ctl.BUDGET_KEY)
    list_before, bbo_before = m.list_calls, len(m.bbo_calls)
    bl._reset_backoff()
    t0 = time.monotonic()
    out3 = await supervisor_run(60)
    dt = time.monotonic() - t0
    b3 = await budget(pool)
    ctlv = await control(pool)
    print("   result : started=%s why=%s in %.2fs"
          % (out3.get("started"), out3.get("why"), dt))
    print("   venue  : listing %d -> %d, BBO %d -> %d"
          % (list_before, m.list_calls, bbo_before, len(m.bbo_calls)))
    print("   control after the expiry: %s" % ctlv)
    check("the expired probe did not start", out3.get("started"), False)
    check("it issued NO listing request past the deadline",
          m.list_calls, list_before)
    check("it issued NO BBO request past the deadline",
          len(m.bbo_calls), bbo_before)
    check("counters did not move", (b3["distinct_reserved"],
                                    b3["bbo_attempts_reserved"]), (0, 0))

    # the database keeps refusing, independently of anything local
    res = await ctl.reserve(pool, ctl.R_LISTING, probe_id=b3["probe_id"])
    print("   direct reservation after the deadline: %s" % (res,))
    check("the DATABASE itself refuses a post-deadline reservation",
          ctl.granted(res), False)
    # THE REFUSAL ABOVE CAME FROM THE CONTROL, because the deadline's own
    # shutdown had just written it false -- correct, but it means the
    # deadline gate has not yet been shown refusing on its own. Put the
    # control back to true, leave the deadline in the past, and ask again.
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, 'true'::jsonb)"
        " ON CONFLICT (key) DO UPDATE SET value = 'true'::jsonb",
        ctl.CONTROL_KEY)
    res2 = await ctl.reserve(pool, ctl.R_LISTING, probe_id=b3["probe_id"])
    print("   with the control back ON and the deadline still past: %s"
          % res2["why"])
    check("the DEADLINE gate refuses on its own, control notwithstanding",
          (ctl.granted(res2), res2["why"]),
          (False, "RESERVATION_DEADLINE_PASSED"))
    await obs_stop(pool)

    rule("RESULT")
    if FAILS:
        print("FAILED %d check(s):" % len(FAILS))
        for lbl, got, want in FAILS:
            print("  - %s: got %r want %r" % (lbl, got, want))
        return 1
    print("every check passed, inside the deployment image, against a "
          "database built by the repository's own migration runner.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
