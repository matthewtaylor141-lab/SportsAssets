"""RC6.3c PMX-1: THE ASKED SYMBOLS' REFDATA COMES FROM THE PLANE, FIRST.

PRODUCTION (RC6.3b approved-judge packet, pm-acceptance ec8b892d,
completion.json market_data.pmx_primary.consumer_reads, PROCESS_SINCE_IMPORT):
14,224 consumer reads in the deciding process, 5 served by the PMX book;
PMX_IDENTITY_NOT_PROVEN_EXACT 4,752 -- the deciding process holding no
refdata for the symbol a consumer asked about -- merged across consumers
(fallback_reasons_top), so the collector's share could not be read. The
plane's refdata priority pull knew nothing of the ask (it read held /
EVALUATED_CANDIDATE / imminent registry rows; the full pull was TRUNCATED on
every attempt, refdata_pending 29,352 of 75,415), and a spare-capacity asked
symbol the plane had no record for was re-asked of the plane only after
RETRY_UNLISTED_S (300 s): the ask never became a record, the record never
became identity, the consumer read REST.

  §1  the share of asked symbols with refdata at ask time is published
      (institutional_api_stream.asked_refdata_report), carried by
      paper_pmx_books.telemetry and completion.consumer_reads_block
  §2  an asked symbol with a fresh persisted plane record maps WITHOUT A
      VENUE READ on the next hand-off beat (base: identity unproven until the
      300 s retry -> REST); the beat takes only a record inside the pass's
      own bound that names exactly the symbol; without a plane record the
      symbol stays unproven and its consumer reads REST exactly as today; a
      failing plane reader or pool installs nothing and never raises
  §3  the asked set is handed to the plane (ingestion_state, one bounded
      row, written when it changes) and the plane's refdata slot puts the
      asked symbols first, then EVALUATED_CANDIDATE, then held and imminent;
      an asked symbol outside the registry is counted, never read; the whole
      loop on real Postgres: ask -> hand-off -> priority read -> persisted
      record -> identity in the deciding process, no venue call by the API
  §4  the production identity-refused shapes replayed, base vs head named
  §5  the counters are split by consumer in telemetry and completion.json;
      every key the block carried before is unchanged
  §6  pins: the shared key and bounds, rule (a) not relaxed, the beat reaches
      no venue, the budgets unchanged, this file capital-critical

Synthetic records (the documented aec moneyline shape), in-memory streams,
fakes for every venue; real Postgres only for the plane's registry and the
hand-off row, in rolled-back transactions. No network, no order.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import pathlib
import time
import uuid

import pytest

from sportsassets import institutional_api_stream as IAS
from sportsassets import institutional_stream as IS
from sportsassets import paper_pmx_books as PCB
from sportsassets.completion import read as CR
from sportsassets.market_plane import refdata_universe as RU
from sportsassets.market_plane import registry as R
from sportsassets.workers import universal_market_plane as W

from tests import paper_harness as H
from tests.test_institutional_contract_map import AEC

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

BACKEND = pathlib.Path(__file__).resolve().parents[1]
LONG = "ORDER_INTENT_BUY_LONG"


def rec_for(slug: str) -> dict:
    """AEC's exact record re-keyed to another registered contract."""
    ev = slug[len("aec-"):]
    return json.loads(json.dumps(AEC).replace("mlb-sd-mil-2026-10-03", ev))


def slugs(n, tag="g"):
    return ["aec-mlb-sd-mil-2026-10-03-%s%02d" % (tag, i) for i in range(n)]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    PCB.reset()
    IS.reset()
    IAS.reset()
    yield
    PCB.reset()
    IS.reset()
    IAS.reset()


def live(monkeypatch):
    """THIS process runs the stream (nothing else injected)."""
    monkeypatch.setattr(IAS, "running", lambda: True)
    IS.BOOKS.set_state(IS.S_IDLE, "test")


class Plane:
    """The plane's persisted records as the API reads them."""

    def __init__(self):
        self.records: dict = {}
        self.asks: list = []

    async def __call__(self, symbols):
        self.asks.append(list(symbols))
        return {s: self.records[s] for s in symbols if s in self.records}


class Pool:
    """A pool whose connection records the hand-off writes."""

    def __init__(self, *, table=True, fail=False):
        self.writes: list = []
        self.table = table
        self.fail = fail

    async def __call__(self):
        return self

    def acquire(self):
        pool = self

        class Conn:
            async def fetchval(self_, sql, *a):
                if pool.fail:
                    raise RuntimeError("pool down")
                return pool.table

            async def execute(self_, sql, key, value):
                pool.writes.append((key, json.loads(value)))

        class A:
            async def __aenter__(self_):
                return Conn()

            async def __aexit__(self_, *a):
                return False
        return A()


def rest_reader():
    reads = []

    def boot(client, s):
        reads.append(s)
        return {"record": rec_for(s)}
    return reads, boot


# ═════════════════════════════════════════════════════════════════════
# §1 THE SHARE OF ASKED SYMBOLS WITH REFDATA AT ASK TIME
# ═════════════════════════════════════════════════════════════════════

def test_every_identity_question_is_counted_with_or_without_a_record(
        monkeypatch):
    live(monkeypatch)
    s = slugs(1, "q")[0]
    assert IAS.identity_mapper(s, LONG) is None            # asked, no record
    IAS.REFDATA[s] = {"record": rec_for(s), "at": time.time()}
    assert IAS.identity_mapper(s, LONG)["status"] == "EXACT"
    assert IAS.identity_mapper(s, "NO") is None            # not an ask: leg
    rep = IAS.asked_refdata_report()
    assert rep["asks"] == {"total": 2, "with_refdata": 1, "share": 0.5}
    assert rep["symbols"]["asked"] == 1 and rep["symbols"]["with_refdata"] == 1
    assert rep["symbols"]["share"] == 1.0
    assert rep["scope"] == "PROCESS_SINCE_IMPORT"
    assert IAS.primary_report()["asked_refdata"]["asks"]["total"] == 2
    assert IAS.describe()["asked_refdata"]["asks"]["total"] == 2
    # carried by the consumer telemetry the held-mark run persists, and by
    # the completion block that reads that run
    t = PCB.telemetry()
    assert t["asked_refdata"]["asks"] == rep["asks"]
    blk = CR.consumer_reads_block(dict(t, totals={"PMX_GRPC": 0, "REST": 1}))
    assert blk["asked_refdata"]["asks"] == rep["asks"]


def test_the_ask_names_its_consumer_and_the_report_splits_by_it(
        monkeypatch):
    live(monkeypatch)
    a, b = slugs(2, "c")
    PCB.consumer_read(a, consumer=PCB.C_COLLECTOR)
    PCB.consumer_read(b, consumer=PCB.C_PAPER_OWNER)
    IAS.REFDATA[b] = {"record": rec_for(b), "at": time.time()}
    by = IAS.asked_refdata_report()["symbols"]["by_consumer"]
    assert by[PCB.C_COLLECTOR] == {"asked": 1, "with_refdata": 0}
    assert by[PCB.C_PAPER_OWNER] == {"asked": 1, "with_refdata": 1}
    # the mapper's own ask (a missing record) never erases the consumer
    assert IAS.identity_mapper(a, LONG) is None
    assert IAS._ASKED_BY[a] == PCB.C_COLLECTOR


# ═════════════════════════════════════════════════════════════════════
# §2 THE PLANE'S RECORD, WITHOUT A VENUE READ
# ═════════════════════════════════════════════════════════════════════

def test_an_asked_symbol_with_a_fresh_plane_record_maps_without_a_venue_read(
        monkeypatch):
    """THE REGRESSION. A consumer asks about a symbol the plane has no record
    for yet; the plane persists one 15 s later. BASE: the pass asked the plane
    once, held the symbol back for RETRY_UNLISTED_S (300 s), and every
    consumer read meanwhile was PMX_IDENTITY_NOT_PROVEN_EXACT -> REST. HEAD:
    the hand-off beat takes the record at its next tick and the symbol maps
    EXACT with no venue read of any kind."""
    live(monkeypatch)
    focus = slugs(IAS.MAX_SYMBOLS, "f")       # the asked symbol is spare
    s = slugs(1, "a")[0]
    plane = Plane()
    reads, boot = rest_reader()
    t0 = time.time()
    PCB.consumer_read(s, consumer=PCB.C_COLLECTOR, now=t0)   # the ask
    assert IAS.asked_symbols(now=t0) == [s]
    asyncio.run(IAS.refresh_once(symbols=focus, held=[], bootstrap=boot,
                                 now=t0, plane_records=plane,
                                 max_bootstraps=500))
    assert s not in reads and s not in IAS.REFDATA      # no record anywhere
    got = PCB.consumer_read(s, consumer=PCB.C_COLLECTOR, now=t0 + 5)
    assert got["refusal"] == PCB.R_IDENTITY
    # the plane persists the record (its priority read answered the ask)
    plane.records[s] = {"record": rec_for(s), "at": t0 + 15}
    # the base's own path would not look again before the retry interval
    assert IAS.pending(t0 + 20) == []
    beat = asyncio.run(IAS.handoff_asked(None, now=t0 + 20,
                                         plane_records=plane))
    assert beat["installed"] == 1 and beat["without_refdata"] == 1
    assert plane.asks[-1] == [s]
    assert IAS.identity_mapper(s, LONG)["status"] == "EXACT"
    assert IAS.REFDATA[s]["source"] == "MARKET_PLANE_REGISTRY"
    assert IAS.REFDATA[s]["at"] == t0 + 15                   # its own age
    assert IAS._STATE["asked_from_plane"] == 1
    assert reads == focus                   # never a REST read for the ask
    # the next pass subscribes it; still no venue read
    asyncio.run(IAS.refresh_once(symbols=focus, held=[], bootstrap=boot,
                                 now=t0 + 21, plane_records=plane,
                                 max_bootstraps=500))
    assert s in IS.BOOKS.wanted() and reads == focus
    # rule (a) is now satisfied: the consumer's refusal moves on to the
    # stream's own (no book on this connection yet), never REST for identity
    got = PCB.consumer_read(s, consumer=PCB.C_COLLECTOR, now=t0 + 22)
    assert got["refusal"] != PCB.R_IDENTITY
    assert got["refusal"].startswith(PCB.R_STREAM)
    rep = IAS.asked_refdata_report(now=t0 + 22)
    assert rep["symbols"] == {"asked": 1, "with_refdata": 1, "from_plane": 1,
                              "share": 1.0, "by_consumer": {
                                  PCB.C_COLLECTOR: {"asked": 1,
                                                    "with_refdata": 1}}}
    assert rep["handoff"]["installed_total"] == 1


def test_the_beat_takes_only_a_fresh_record_naming_exactly_the_symbol(
        monkeypatch):
    live(monkeypatch)
    fresh, old, wrong, absent = slugs(4, "b")
    t0 = time.time()
    for s in (fresh, old, wrong, absent):
        IAS.request(s, now=t0, consumer=PCB.C_COLLECTOR)
    plane = Plane()
    plane.records = {
        fresh: {"record": rec_for(fresh), "at": t0 - 600},
        # older than the pass's own bound for a core symbol: not the beat's
        old: {"record": rec_for(old), "at": t0 - IAS.REFDATA_REFRESH_S - 1},
        # another instrument under this key: never
        wrong: {"record": rec_for(absent), "at": t0 - 60}}
    beat = asyncio.run(IAS.handoff_asked(None, now=t0, plane_records=plane))
    assert beat["installed"] == 1 and beat["without_refdata"] == 4
    assert set(IAS.REFDATA) == {fresh}
    assert IAS.identity_mapper(fresh, LONG)["status"] == "EXACT"
    for s in (old, wrong, absent):
        assert IAS.identity_mapper(s, LONG) is None
    # the entry keeps the record's own age: every later pass treats it
    # exactly as one the pass installed (due when the PLANE's record turns
    # REFDATA_REFRESH_S)
    assert IAS._due(fresh, t0) is False
    assert IAS._due(fresh, t0 - 600 + IAS.REFDATA_REFRESH_S + 1) is True
    # the stream was handed the instrument (set_instrument), no subscribe
    assert IS.BOOKS.current(fresh)["evidence"]["market"]["price_scale"] \
        == 1000
    assert fresh not in IS.BOOKS.wanted()


def test_without_a_plane_record_the_symbol_stays_unproven_and_rest_as_today(
        monkeypatch):
    live(monkeypatch)
    focus = slugs(IAS.MAX_SYMBOLS, "f")
    spare = slugs(1, "s")[0]
    plane = Plane()                      # the plane never holds a record
    reads, boot = rest_reader()
    t0 = time.time()
    PCB.consumer_read(spare, consumer=PCB.C_COLLECTOR, now=t0)
    for k in range(6):
        at = t0 + 10 * k
        asyncio.run(IAS.handoff_asked(None, now=at, plane_records=plane,
                                      force=True))
        asyncio.run(IAS.refresh_once(symbols=focus, held=[], bootstrap=boot,
                                     now=at, plane_records=plane,
                                     max_bootstraps=500))
        got = PCB.consumer_read(spare, consumer=PCB.C_COLLECTOR, now=at)
        assert got["refusal"] == PCB.R_IDENTITY        # fail closed
        PCB.note_rest(PCB.C_COLLECTOR, got["refusal"])
    assert spare not in reads                 # a spare symbol: never REST
    assert spare not in IAS.REFDATA and spare not in IS.BOOKS.wanted()
    assert IAS._STATE["asked_from_plane"] == 0
    t = PCB.telemetry()
    assert t["shares_by_consumer"][PCB.C_COLLECTOR][
        "identity_not_proven_share"] == 1.0
    # a core-set asked symbol (room in the focus bound) still takes its REST
    # bootstrap exactly as today when the plane has no record
    IAS.reset()
    live(monkeypatch)
    core = slugs(1, "k")[0]
    PCB.consumer_read(core, consumer=PCB.C_COLLECTOR, now=t0)
    asyncio.run(IAS.handoff_asked(None, now=t0, plane_records=plane,
                                  force=True))
    asyncio.run(IAS.refresh_once(symbols=focus[:4], held=[], bootstrap=boot,
                                 now=t0, plane_records=plane))
    assert core in reads and IAS.identity_mapper(core, LONG)["status"] \
        == "EXACT"
    assert "source" not in IAS.REFDATA[core]              # a REST record


def test_a_failing_plane_reader_or_pool_installs_nothing_and_never_raises(
        monkeypatch):
    live(monkeypatch)
    s = slugs(1, "e")[0]
    t0 = time.time()
    IAS.request(s, now=t0, consumer=PCB.C_COLLECTOR)

    async def broken(symbols):
        raise RuntimeError("plane down")
    beat = asyncio.run(IAS.handoff_asked(Pool(fail=True), now=t0,
                                         plane_records=broken))
    assert beat["installed"] == 0 and beat["written"] is False
    assert beat["error"] == "plane: RuntimeError"
    assert IAS.identity_mapper(s, LONG) is None
    rep = IAS.asked_refdata_report(now=t0)
    assert rep["handoff"]["error"] == "plane: RuntimeError"
    # a pool that fails on the write still lets the plane read happen
    plane = Plane()
    plane.records[s] = {"record": rec_for(s), "at": t0 - 1}
    beat = asyncio.run(IAS.handoff_asked(Pool(fail=True), now=t0 + 10,
                                         plane_records=plane))
    assert beat["written"] is False and beat["error"] == "write: RuntimeError"
    assert beat["installed"] == 1
    # the stream not running here: nothing is asked, nothing handed off
    IAS.reset()
    monkeypatch.setattr(IAS, "running", lambda: False)
    IAS.request(s, now=t0)
    assert asyncio.run(IAS.handoff_asked(Pool(), now=t0)) == {
        "skipped": "NOT_RUNNING"}


# ═════════════════════════════════════════════════════════════════════
# §3 THE HAND-OFF: QUEUED AT PRIORITY
# ═════════════════════════════════════════════════════════════════════

def test_the_hand_off_carries_the_asked_set_bounded_most_recent_first(
        monkeypatch):
    live(monkeypatch)
    a, b, c = slugs(3, "h")
    t0 = time.time()
    IAS.request(a, now=t0, consumer=PCB.C_COLLECTOR)
    IAS.request(b, now=t0 + 1, consumer=PCB.C_PAPER_OWNER)
    IAS.request(c, now=t0 + 2)
    IAS.REFDATA[b] = {"record": rec_for(b), "at": t0,
                      "source": "MARKET_PLANE_REGISTRY"}
    p = IAS._handoff_payload(t0 + 3)
    assert p["version"] == IAS.ASKED_HANDOFF_VERSION
    assert [r["symbol"] for r in p["symbols"]] == [c, b, a]
    assert p["symbols"][1] == {"symbol": b, "asked_at": pytest.approx(t0 + 1),
                               "consumer": PCB.C_PAPER_OWNER, "refdata": True,
                               "source": "MARKET_PLANE_REGISTRY"}
    assert p["symbols"][2]["consumer"] == PCB.C_COLLECTOR
    assert p["symbols"][2]["refdata"] is False
    assert p["asked"] == 3 and p["without_refdata"] == 2
    assert p["idle_s"] == IAS.REQUEST_IDLE_S
    # bounded by the asked set's own bound; an idle ask leaves
    for s in slugs(IAS.MAX_REQUESTED + 50, "m"):
        IAS.request(s, now=t0 + 10)
    p = IAS._handoff_payload(t0 + 11)
    assert p["asked"] == IAS.MAX_REQUESTED
    assert IAS._handoff_payload(t0 + 10 + IAS.REQUEST_IDLE_S + 1)["asked"] == 0


def test_the_hand_off_is_one_row_written_when_it_changes(monkeypatch):
    live(monkeypatch)
    a, b = slugs(2, "w")
    t0 = time.time()
    IAS.request(a, now=t0, consumer=PCB.C_COLLECTOR)
    pool = Pool()
    plane = Plane()
    beat = asyncio.run(IAS.handoff_asked(pool, now=t0, plane_records=plane))
    assert beat == {"written": True, "asked": 1, "without_refdata": 1,
                    "installed": 0, "error": None}
    assert len(pool.writes) == 1
    key, value = pool.writes[0]
    assert key == IAS.ASKED_HANDOFF_KEY == R.ASKED_HANDOFF_KEY
    assert [r["symbol"] for r in value["symbols"]] == [a]
    assert value["at"] == t0 and value["process"].startswith(IAS.SERVICE)
    # the beat is paced; an unchanged set is not rewritten (the plane's
    # records are still re-read for the symbols without refdata)
    assert asyncio.run(IAS.handoff_asked(pool, now=t0 + 5,
                                         plane_records=plane)) == {
        "skipped": "NOT_DUE"}
    beat = asyncio.run(IAS.handoff_asked(pool, now=t0 + 10,
                                         plane_records=plane))
    assert beat["written"] is False and len(pool.writes) == 1
    assert plane.asks == [[a], [a]]
    # ...but rewritten every ASKED_HANDOFF_REFRESH_S so the plane can bound
    # its age, and at once when the set or a refdata flag changes
    asyncio.run(IAS.handoff_asked(pool, now=t0 + IAS.ASKED_HANDOFF_REFRESH_S,
                                  plane_records=plane))
    assert len(pool.writes) == 2
    IAS.request(b, now=t0 + 65, consumer=PCB.C_COLLECTOR)
    asyncio.run(IAS.handoff_asked(pool, now=t0 + 70, plane_records=plane))
    assert len(pool.writes) == 3
    assert [r["symbol"] for r in pool.writes[-1][1]["symbols"]] == [b, a]
    plane.records[a] = {"record": rec_for(a), "at": t0 + 75}
    beat = asyncio.run(IAS.handoff_asked(pool, now=t0 + 80,
                                         plane_records=plane))
    assert beat["installed"] == 1 and len(pool.writes) == 3
    beat = asyncio.run(IAS.handoff_asked(pool, now=t0 + 90,
                                         plane_records=plane))
    assert beat["written"] is True and len(pool.writes) == 4
    assert {r["symbol"]: r["refdata"] for r in pool.writes[-1][1]["symbols"]} \
        == {a: True, b: False}
    assert plane.asks[-1] == [b]
    rep = IAS.asked_refdata_report(now=t0 + 90)["handoff"]
    assert rep["writes"] == 4 and rep["symbols"] == 2
    assert rep["without_refdata"] == 1 and rep["installed_total"] == 1
    # no table (a database before migration 001's key/value store): named
    pool2 = Pool(table=False)
    IAS.reset()
    live(monkeypatch)
    IAS.request(a, now=t0)
    beat = asyncio.run(IAS.handoff_asked(pool2, now=t0, plane_records=plane))
    assert beat["written"] is False
    assert beat["error"] == "write: INGESTION_STATE_ABSENT"


async def _registry_row(c, cid, *, prio, reason=None, start=None,
                        refdata=None, active=True):
    await c.execute(
        "INSERT INTO market_plane_registry (contract_id, venue, active, "
        " desired_subscription, updated_at, priority, required_reason, "
        " event_start, refdata, refdata_at) VALUES ($1, 'POLYMARKET_US', $2, "
        " true, now(), $3, $4, CASE WHEN $5::float8 IS NULL THEN NULL ELSE "
        " to_timestamp($5) END, $6::jsonb, CASE WHEN $6::jsonb IS NULL THEN "
        " NULL ELSE now() END) ON CONFLICT (contract_id) DO UPDATE SET "
        " active = EXCLUDED.active, priority = EXCLUDED.priority, "
        " required_reason = EXCLUDED.required_reason, event_start = "
        " EXCLUDED.event_start, refdata = EXCLUDED.refdata, refdata_at = "
        " EXCLUDED.refdata_at",
        cid, active, prio, reason, start,
        json.dumps(refdata) if refdata is not None else None)


class _Conn:
    """A pool over ONE connection inside the test's transaction."""

    def __init__(self, c):
        self.c = c

    async def __call__(self):
        return self

    def acquire(self):
        c = self.c

        class A:
            async def __aenter__(self_):
                return c

            async def __aexit__(self_, *a):
                return False
        return A()


@pg
def test_the_plane_reads_the_hand_off_and_puts_the_asked_symbols_first(
        monkeypatch):
    import asyncpg
    live(monkeypatch)                  # the API's stream runs (asks recorded)

    async def go():
        c = await asyncpg.connect(H.DSN)
        tr = c.transaction()
        await tr.start()
        try:
            now = time.time()
            tag = uuid.uuid4().hex[:6]
            asked_a, asked_b, outside, cand, held, immi, rest = (
                "aec-mlb-sd-mil-2026-10-11-pf%s-%s" % (k, tag)
                for k in ("a", "b", "o", "c", "h", "i", "r"))
            # the registry: an asked row at venue-activity priority, one
            # already holding refdata, a candidate, a held, an imminent, a
            # far-off row; `outside` is asked and not a registry member
            await _registry_row(c, asked_a, prio=80, start=now + 5 * 86400)
            await _registry_row(c, asked_b, prio=80, start=now + 5 * 86400,
                                refdata=rec_for(asked_b))
            await _registry_row(c, cand, prio=10, reason="EVALUATED_CANDIDATE",
                                start=now + 86400)
            await _registry_row(c, held, prio=0, reason="OPEN_PAPER_POSITION",
                                start=now + 86400)
            await _registry_row(c, immi, prio=50, reason="VENUE_ACTIVE",
                                start=now + 1800)
            await _registry_row(c, rest, prio=80, reason="VENUE_ACTIVE",
                                start=now + 5 * 86400)
            mine = [asked_a, asked_b, outside, cand, held, immi, rest]
            # no hand-off yet: ABSENT, nothing asked, the base's order but
            # with the candidate before the held row
            hand = await R.asked_handoff(c, now=now, max_age_s=1800.0)
            assert hand["state"] == "ABSENT" and hand["symbols"] == []
            split = await R.refdata_pending_split(
                c, now=now, unlisted_retry_s=21600.0, priority_max=10,
                imminent_s=7200.0, limit=1000, asked=hand["symbols"])
            pri = [s for s in split["priority"] if s in mine]
            assert pri == [cand, held, immi]
            assert [s for s in split["other"] if s in mine] == [asked_a, rest]
            # the deciding process hands off its asked set (the real writer)
            IAS.reset()
            IAS.request(asked_a, now=now - 30, consumer=PCB.C_COLLECTOR)
            IAS.request(asked_b, now=now - 20, consumer=PCB.C_PAPER_OWNER)
            IAS.request(outside, now=now - 10, consumer=PCB.C_COLLECTOR)
            IAS.REFDATA[asked_b] = {"record": rec_for(asked_b), "at": now}
            pool = _Conn(c)

            async def no_records(symbols):
                return {}
            beat = await IAS.handoff_asked(pool, now=now, force=True,
                                           plane_records=no_records)
            assert beat["written"] is True and beat["asked"] == 3
            hand = await R.asked_handoff(c, now=now + 12, max_age_s=1800.0)
            assert hand["state"] == "OK"
            assert hand["symbols"] == [outside, asked_b, asked_a]
            assert hand["without_refdata"] == 2
            assert hand["age_s"] == pytest.approx(12.0, abs=0.6)
            split = await R.refdata_pending_split(
                c, now=now + 12, unlisted_retry_s=21600.0, priority_max=10,
                imminent_s=7200.0, limit=1000, asked=hand["symbols"])
            # FIRST PLACE: the asked symbol (a registry member without
            # refdata), then EVALUATED_CANDIDATE, then held, then imminent;
            # the one already holding refdata is not pending; the one
            # outside the registry is counted, never read
            assert split["priority"][0] == asked_a
            assert [s for s in split["priority"] if s in mine] == [
                asked_a, cand, held, immi]
            assert split["asked_pending"] == 1
            assert split["asked_not_in_registry"] == 1
            assert [s for s in split["other"] if s in mine] == [rest]
            assert R.PRIORITY_ORDER == (
                "ASKED_BY_A_CONSUMER", "EVALUATED_CANDIDATE",
                "OPEN_PAPER_POSITION", "IMMINENT_BY_EVENT_START")
            # a cooling asked symbol (a failed read) is excluded before the
            # limit, exactly as every other
            split = await R.refdata_pending_split(
                c, now=now + 12, unlisted_retry_s=21600.0, priority_max=10,
                imminent_s=7200.0, limit=1000, asked=hand["symbols"],
                excluded=[asked_a])
            assert asked_a not in split["priority"]
            assert split["asked_pending"] == 0
            # a hand-off older than the bound is a stopped API's: not asks
            assert (await R.asked_handoff(c, now=now + 1801,
                                          max_age_s=1800.0))["state"] \
                == "STALE"
            # a symbol asked longer ago than idle_s leaves the asks
            hand = await R.asked_handoff(c, now=now + 12, max_age_s=1800.0,
                                         idle_s=35.0)
            assert hand["symbols"] == [outside, asked_b]
            assert hand["without_refdata"] == 1
        finally:
            await tr.rollback()
            await c.close()
    asyncio.run(go())


class _RefClient:
    """The venue's by-symbol refdata read: answers the symbols it lists."""

    def __init__(self, listed):
        self.listed = set(listed)
        self.bodies = []

    def read_instruments(self, body):
        self.bodies.append(dict(body))
        rows = [rec_for(s) for s in body.get("symbols", [])
                if s in self.listed]
        return {"status": 200, "body": {"instruments": rows}, "ms": 3}


@pg
def test_the_whole_loop_on_real_postgres_ask_handoff_priority_read_identity(
        monkeypatch):
    """ask -> hand-off row -> the plane's refdata slot reads the asked symbol
    FIRST and persists its record -> the deciding process takes it on its
    next beat -> identity EXACT. The API made no venue call."""
    import asyncpg
    live(monkeypatch)                  # the API's stream runs (asks recorded)

    async def go():
        c = await asyncpg.connect(H.DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            now = time.time()
            tag = uuid.uuid4().hex[:6]
            asked, outside, cand, held = (
                "aec-mlb-sd-mil-2026-10-11-lp%s-%s" % (k, tag)
                for k in ("a", "o", "c", "h"))
            await _registry_row(c, asked, prio=80, start=now + 5 * 86400)
            await _registry_row(c, cand, prio=10, reason="EVALUATED_CANDIDATE")
            await _registry_row(c, held, prio=0, reason="OPEN_PAPER_POSITION")
            pool = _Conn(c)
            IAS.reset()
            IAS.request(asked, now=now - 5, consumer=PCB.C_COLLECTOR)
            IAS.request(outside, now=now - 4, consumer=PCB.C_COLLECTOR)
            reads, boot = rest_reader()

            async def plane_recent(symbols):
                return await IAS.plane_refdata(
                    pool, symbols, max_age_s=IAS.REFDATA_REFRESH_S)
            # the beat: the hand-off written; the plane holds nothing yet
            beat = await IAS.handoff_asked(pool, now=now, force=True,
                                           plane_records=plane_recent)
            assert beat["written"] is True and beat["installed"] == 0
            assert IAS.identity_mapper(asked, LONG) is None
            # the plane's refdata slot: ONE priority read naming the asked
            # symbol, the candidate and the held row (the wire body is the
            # venue's sorted symbol list; the first-place ORDER, which the
            # 1,000-symbol batch is cut by, is the split's, proved above);
            # `outside` is not a registry member and is not read
            client = _RefClient([asked, cand, held])
            planner = RU.Planner(calls_per_minute=6)
            s1 = await W.refdata_step(pool, client, planner, {}, now=now + 1)
            assert s1["action"] == RU.A_PRIORITY
            assert client.bodies[0]["symbols"] == sorted([asked, cand, held])
            assert s1["stored"] == 3 and s1["unlisted"] == 0
            assert s1["asked"] == {"handoff": "OK", "age_s": pytest.approx(
                1.0, abs=0.6), "symbols": 2, "without_refdata_at_api": 2,
                "pending": 1, "not_in_registry": 1, "answered": 1}
            assert planner.totals["priority_symbols"] == 3
            assert planner.digest(now + 1)["totals"]["priority_calls"] == 1
            # the deciding process's next beat takes the persisted record:
            # identity EXACT, no venue call by the API at all
            beat = await IAS.handoff_asked(pool, now=now + 15, force=True,
                                           plane_records=plane_recent)
            assert beat["installed"] == 1
            assert IAS.identity_mapper(asked, LONG)["status"] == "EXACT"
            assert IAS.REFDATA[asked]["source"] == "MARKET_PLANE_REGISTRY"
            assert reads == [] and IAS._STATE["refdata_reads"] == 0
            # the slot has nothing asked left to read (the registry holds
            # the record); the hand-off's own refdata flag follows on the
            # next beat (the row is written before the beat's installs)
            s2 = await W.refdata_step(pool, client, planner, {}, now=now + 20)
            assert s2["asked"]["pending"] == 0
            assert s2["asked"]["not_in_registry"] == 1
            assert s2["asked"]["without_refdata_at_api"] == 2
            beat = await IAS.handoff_asked(pool, now=now + 30, force=True,
                                           plane_records=plane_recent)
            assert beat["written"] is True and beat["installed"] == 0
            hand = await R.asked_handoff(c, now=now + 31, max_age_s=1800.0)
            assert hand["without_refdata"] == 1          # `outside` only
            # the plane's second slot was the full pull's page, not another
            # by-symbol read: the asked symbol was read ONCE, by the plane
            assert s2["action"] == RU.A_PAGE
            assert [b for b in client.bodies if "symbols" in b] == \
                client.bodies[:1]
        finally:
            await tr.rollback()
            await c.close()
    asyncio.run(go())


# ═════════════════════════════════════════════════════════════════════
# §4 THE PRODUCTION IDENTITY-REFUSED SHAPES, REPLAYED
# ═════════════════════════════════════════════════════════════════════

SHAPES = [
    # (name, the plane's record at each beat, head proves by t0+20, base)
    ("A_record_persisted_after_the_ask", "LATE", True,
     "unproven until the 300 s plane retry"),
    ("B_no_record_ever", "NONE", False, "unproven; REST"),
    ("C_record_names_another_symbol", "WRONG", False, "unproven; REST"),
    ("D_record_older_than_the_pass_bound", "OLD", False,
     "the pass's own rule at its retry: a spare symbol takes it within 24 h"),
]


@pytest.mark.parametrize("name,shape,proves,base", SHAPES)
def test_the_identity_refused_shapes_replayed(monkeypatch, name, shape,
                                              proves, base):
    live(monkeypatch)
    focus = slugs(IAS.MAX_SYMBOLS, "f")
    s, other = slugs(2, "r")
    plane = Plane()
    reads, boot = rest_reader()
    t0 = time.time()
    seq = []

    def ask(at):
        got = PCB.consumer_read(s, consumer=PCB.C_COLLECTOR, now=at)
        if not got["ok"]:
            PCB.note_rest(PCB.C_COLLECTOR, got["refusal"])
        seq.append(got["refusal"])
    ask(t0)
    asyncio.run(IAS.refresh_once(symbols=focus, held=[], bootstrap=boot,
                                 now=t0, plane_records=plane,
                                 max_bootstraps=500))
    if shape == "LATE":
        plane.records[s] = {"record": rec_for(s), "at": t0 + 15}
    elif shape == "WRONG":
        plane.records[s] = {"record": rec_for(other), "at": t0 + 15}
    elif shape == "OLD":
        plane.records[s] = {"record": rec_for(s),
                            "at": t0 - IAS.REFDATA_REFRESH_S - 60}
    for at in (t0 + 10, t0 + 20):
        asyncio.run(IAS.handoff_asked(None, now=at, plane_records=plane))
        ask(at)
    assert seq[0] == PCB.R_IDENTITY and seq[1] == PCB.R_IDENTITY
    if proves:
        assert seq[2] != PCB.R_IDENTITY, (name, seq, base)
        assert IAS.identity_mapper(s, LONG)["status"] == "EXACT"
    else:
        assert seq[2] == PCB.R_IDENTITY, (name, seq, base)
        assert IAS.identity_mapper(s, LONG) is None
    assert s not in reads                 # a spare symbol: never REST
    # the counters name the shape per consumer
    t = PCB.telemetry()
    coll = t["fallback_reasons_by_consumer"][PCB.C_COLLECTOR]
    assert coll[PCB.R_IDENTITY] == (2 if proves else 3)
    if shape == "OLD":
        # the pass's own plane read (24 h for a spare symbol) still takes
        # the older record at its retry interval, exactly as on the base
        asyncio.run(IAS.refresh_once(symbols=focus, held=[], bootstrap=boot,
                                     now=t0 + IAS.RETRY_UNLISTED_S + 1,
                                     plane_records=plane, max_bootstraps=500))
        assert IAS.identity_mapper(s, LONG)["status"] == "EXACT"
        assert s not in reads


def test_the_stream_not_running_here_is_not_an_identity_refusal(monkeypatch):
    monkeypatch.setattr(IAS, "running", lambda: False)
    s = slugs(1, "n")[0]
    got = PCB.consumer_read(s, consumer=PCB.C_COLLECTOR)
    assert got["refusal"] == PCB.R_NOT_RUNNING
    assert IAS.asked_symbols() == []
    assert IAS._handoff_payload(time.time())["asked"] == 0


# ═════════════════════════════════════════════════════════════════════
# §5 THE COUNTERS, SPLIT BY CONSUMER
# ═════════════════════════════════════════════════════════════════════

def test_fallback_reasons_are_split_by_consumer_in_telemetry_and_completion():
    for _ in range(3):
        PCB.note_rest(PCB.C_COLLECTOR, PCB.R_IDENTITY)
    PCB.COUNTERS.note(PCB.C_COLLECTOR, PCB.SOURCE_PMX)
    for _ in range(2):
        PCB.note_rest(PCB.C_PAPER_OWNER, PCB.R_SAME_BOOK)
    t = PCB.telemetry()
    assert t["fallback_reasons_by_consumer"] == {
        PCB.C_COLLECTOR: {PCB.R_IDENTITY: 3},
        PCB.C_PAPER_OWNER: {PCB.R_SAME_BOOK: 2}}
    assert t["shares_by_consumer"] == {
        PCB.C_COLLECTOR: {"reads": 4, "pmx_share": 0.25,
                          "identity_not_proven": 3,
                          "identity_not_proven_share": 0.75},
        PCB.C_PAPER_OWNER: {"reads": 2, "pmx_share": 0.0,
                            "identity_not_proven": 0,
                            "identity_not_proven_share": 0.0}}
    blk = CR.consumer_reads_block(t)
    assert blk["status"] == "MEASURED"
    # what the block carried before, unchanged
    assert blk["by_consumer"][PCB.C_COLLECTOR] == {"PMX_GRPC": 1, "REST": 3}
    assert blk["totals"] == {"PMX_GRPC": 1, "REST": 5}
    assert blk["pmx_share"] == round(1 / 6, 4)
    assert blk["fallback_reasons_top"] == {PCB.R_IDENTITY: 3,
                                           PCB.R_SAME_BOOK: 2}
    # and now by consumer, with the readback criterion on the collector
    assert blk["fallback_reasons_by_consumer"] == \
        t["fallback_reasons_by_consumer"]
    assert blk["shares_by_consumer"] == t["shares_by_consumer"]
    assert blk["collector_identity_unproven"] == {
        "consumer": PCB.C_COLLECTOR, "refusal": PCB.R_IDENTITY,
        "share": 0.75, "reads": 4, "max": 0.10, "status": "OUTSIDE",
        "scope": "PROCESS_SINCE_IMPORT"}
    assert blk["asked_refdata"]["version"] == IAS.ASKED_HANDOFF_VERSION


def test_the_packet_shape_replayed_through_the_block_names_each_consumer():
    """The packet's consumer_reads carried one merged fallback_reasons_top;
    the same counts handed over per consumer read as the collector's own
    identity share, inside or outside the criterion."""
    def run(coll_identity, owner_identity):
        bs = {"scope": "PROCESS_SINCE_IMPORT", "enabled": True, "rule": "r",
              "totals": {"REST": 14219, "PMX_GRPC": 5}, "pmx_share": 0.0004,
              "by_consumer": {
                  "COLLECTOR_VENUE_QUOTE": {
                      "PMX_GRPC": 1, "REST": 3507,
                      "fallback_reasons": {
                          "PMX_IDENTITY_NOT_PROVEN_EXACT": coll_identity,
                          "PMX_SAME_BOOK_NOT_PROVEN_FOR_THIS_SYMBOL":
                              3507 - coll_identity}},
                  "PAPER_MARKET_DATA_OWNER": {
                      "PMX_GRPC": 4, "REST": 10712,
                      "fallback_reasons": {
                          "PMX_IDENTITY_NOT_PROVEN_EXACT": owner_identity,
                          "PMX_SAME_BOOK_NOT_PROVEN_FOR_THIS_SYMBOL":
                              10712 - owner_identity}}}}
        return CR.consumer_reads_block(bs)
    blk = run(1200, 3552)
    assert blk["fallback_reasons_top"]["PMX_IDENTITY_NOT_PROVEN_EXACT"] \
        == 4752                                        # the packet's merge
    assert blk["shares_by_consumer"]["COLLECTOR_VENUE_QUOTE"] == {
        "reads": 3508, "pmx_share": 0.0003, "identity_not_proven": 1200,
        "identity_not_proven_share": 0.3421}
    assert blk["collector_identity_unproven"]["status"] == "OUTSIDE"
    blk = run(300, 4452)
    assert blk["collector_identity_unproven"]["share"] == 0.0855
    assert blk["collector_identity_unproven"]["status"] == "INSIDE"
    # a run from a build before this one: the new evidence is UNREAD, named
    assert blk["asked_refdata"] == {
        "status": "UNREAD", "why": "NO_ASKED_REFDATA_TELEMETRY_ON_RUN"}
    assert CR.consumer_reads_block(None)["status"] == "UNREAD"


# ═════════════════════════════════════════════════════════════════════
# §6 PINS
# ═════════════════════════════════════════════════════════════════════

def test_the_shared_key_and_bounds_are_one_number_each():
    assert IAS.ASKED_HANDOFF_KEY == R.ASKED_HANDOFF_KEY == "pmx_asked_symbols"
    assert W.ASKED_HANDOFF_MAX_AGE_S == IAS.REQUEST_IDLE_S == 1800.0
    assert CR.PMX_IDENTITY_REFUSAL == PCB.R_IDENTITY
    assert CR.PMX_COLLECTOR_CONSUMER == PCB.C_COLLECTOR
    assert CR.PMX_IDENTITY_UNPROVEN_SHARE_MAX == 0.10
    assert IAS.ASKED_HANDOFF_EVERY_S == 10.0
    assert IAS.ASKED_HANDOFF_REFRESH_S == 60.0
    # the budgets and bounds of the stream task are unchanged
    assert (IAS.MAX_SYMBOLS, IAS.HELD_SYMBOL_BUDGET, IAS.BOOTSTRAPS_PER_PASS,
            IAS.REFDATA_REFRESH_S, IAS.RETRY_UNLISTED_S, IAS.READ_PACING_S,
            IAS.STREAM_MAX_SYMBOLS, IAS.MAX_REQUESTED,
            IAS.PLANE_REFDATA_MAX_AGE_S) == (
                32, 168, 24, 3600.0, 300.0, 0.15, 200, 200, 24 * 3600.0)
    # the plane's cap is unchanged
    assert (RU.CALLS_PER_MIN_DEFAULT, RU.CALLS_PER_MIN_MAX, RU.BATCH_MAX) == (
        5, 6, 1000)
    assert R.EVALUATED_CANDIDATE == "EVALUATED_CANDIDATE"


def test_rule_a_is_not_relaxed_a_plane_record_proves_nothing_by_itself(
        monkeypatch):
    live(monkeypatch)
    s = slugs(1, "p")[0]
    t0 = time.time()
    IAS.request(s, now=t0, consumer=PCB.C_COLLECTOR)
    # the plane's record for this symbol does not map exactly (payout)
    rec = copy.deepcopy(rec_for(s))
    rec["eventAttributes"]["payoutValue"] = "500"
    plane = Plane()
    plane.records[s] = {"record": rec, "at": t0 - 1}
    beat = asyncio.run(IAS.handoff_asked(None, now=t0, plane_records=plane))
    assert beat["installed"] == 1                 # the record is held...
    assert IAS.identity_mapper(s, LONG) is None   # ...identity is not
    got = PCB.consumer_read(s, consumer=PCB.C_COLLECTOR, now=t0)
    assert got["refusal"] == PCB.R_IDENTITY
    # the arbitration itself: no identity -> the identity refusal, first
    assert PCB.arbitrate(s, identity=None, current={"ok": True},
                         same_book=None, now=t0)["refusal"] == PCB.R_IDENTITY


def test_the_beat_reaches_no_venue_and_the_task_runs_a_pass_after_installing():
    for fn in (IAS.handoff_asked, IAS._take_plane_record,
               IAS._handoff_payload, IAS.asked_refdata_report):
        src = inspect.getsource(fn)
        assert "_bootstrap_one" not in src and "to_thread" not in src
        assert "bootstrap_instrument" not in src and "client" not in src
    run = inspect.getsource(IAS._run)
    assert "handoff_asked(get_pool, plane_records=_plane_recent)" in run
    assert run.index("handoff_asked(") < run.index('if beat.get("installed")')
    assert "max_age_s=REFDATA_REFRESH_S" in inspect.getsource(IAS._run)
    # the plane's slot reads the hand-off before it chooses its action
    step = inspect.getsource(W.refdata_step)
    assert step.index("R.asked_handoff(") < step.index("refdata_pending_split(")
    assert step.index("refdata_pending_split(") < step.index("next_action(")


def test_the_planner_counts_the_symbols_each_priority_read_names():
    p = RU.Planner(calls_per_minute=6)
    a = p.next_action(now=0.0, priority_pending=["x", "y", "z"])
    assert a["kind"] == RU.A_PRIORITY
    p.record(a, {"status": 200, "body": {"instruments": []}}, now=0.0)
    assert p.totals["priority_symbols"] == 3 and p.totals["priority_calls"] == 1
    a = p.next_action(now=10.0)
    p.record(a, {"status": 200, "body": {"instruments": [], "eof": True}},
             now=10.0)
    assert p.totals["priority_symbols"] == 3


def test_this_proof_is_capital_critical():
    listed = (BACKEND / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_rc63_pmx_refdata_priority.py" in listed.split()
