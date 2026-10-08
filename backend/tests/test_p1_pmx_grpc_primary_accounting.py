"""PMX INSTITUTIONAL gRPC PRIMARY: OBSERVABLE, BOUNDED, ACCOUNTED BY SOURCE.

Production at 08828d04 (render-ops logs 2026-10-07 20:41-23:51Z, both
services): the only stream line ever logged was the boot record
"stream IDLE_NO_SYMBOLS_REQUESTED (started)" -- start_default's answer, by
construction IDLE for a stream that started -- and the Small Live launch view
read that same start record, so it showed IDLE forever. The completion
readback and the PM harness read PMX gRPC primary ONLY from the dedicated
market plane's snapshot (absent: MARKET_PLANE_SNAPSHOT_STALE_19660s), so the
streams that do run (sportsassets-api, sportsassets-workers) could never be
evidenced. VENUE_HEALTH read the workers' stream heartbeat and failed OPEN
when it was stale or absent (the heartbeat landed every ~450 s when idle).
The per-process symbol bound (200) never evicted, so after enough churn a
newly held market was silently never requested.

  S1  the live state is what readers see; transitions are logged
  S2  the digest carries acks, resident L2 books current under both bounds,
      their ages, and what the bound refused or evicted
  S3  the bound stays usable: held markets first, stale entries evicted
  S4  the mechanism label follows the live stream, never the boot constant
  S5  the workers' heartbeat is time-bounded inside the red team's max age
  S6  VENUE_HEALTH fails CLOSED on an absent / stale / unreadable stream
  S7  completion accounts PMX by source on the held-freshness denominator
  S8  the PM harness reads the deciding process when no plane snapshot
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import logging
from datetime import datetime, timezone

import pytest

from sportsassets import institutional_api_stream as IAS
from sportsassets import institutional_focus_universe as FU
from sportsassets import institutional_stream as IS
from sportsassets import pmx_institutional as pmx
from sportsassets.completion import read as CR
from sportsassets.pm_bind import acceptance as PA
from sportsassets.redteam import venue_health as VH
from sportsassets.workers import institutional_md as W

try:
    from tests.test_institutional_contract_map import AEC, SLUG
except ImportError:                                             # pragma: no cover
    from test_institutional_contract_map import AEC, SLUG  # type: ignore

ENV = {"INSTITUTIONAL_MD_STREAM": "on", "PMX_CLIENT_ID": "c",
       "PMX_PARTICIPANT_ID": "p", "PMX_KEY_ID": "k",
       "PMX_PRIVATE_KEY_B64": "z"}


class FakeTransport:
    subscription_mode = IS.MODE_EXPLICIT

    def __init__(self, books, token_fn):
        self.subs = []
        self.outbound = {"sent": 0, "subscribe": 0, "keepalive": 0,
                         "paced": 0}

    def start(self):
        pass

    def subscribe(self, syms):
        self.subs.append(list(syms))

    def stop(self):
        pass


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    IAS.reset()
    FU.note_held_first([])
    yield
    IS.reset()
    IAS.reset()
    FU.note_held_first([])


def _start():
    return IS.start_default(env=ENV, transport_factory=FakeTransport,
                            token_fn=lambda: "t",
                            available=lambda: (True, None))


def _book(books, sym, *, at, venue_age=0.4):
    books.set_instrument(sym, AEC)
    books.on_update({"symbol": sym, "bids": [(450, 1000)],
                     "offers": [(470, 500)],
                     "state": "INSTRUMENT_STATE_OPEN",
                     "transact_time": datetime.fromtimestamp(
                         at - venue_age, tz=timezone.utc)}, received_at=at)


# ── S1 ─────────────────────────────────────────────────────────────────

def test_s1_the_start_record_is_not_the_live_state_and_readers_use_live():
    out = _start()
    assert out["state"] == IS.S_IDLE                  # the boot record
    IS.want([SLUG])
    IS.BOOKS.on_connected("c1")
    d = IS.digest()
    assert d["state"] == IS.S_CONNECTED and d["connected"] is True
    assert d["start"]["state"] == IS.S_IDLE           # never updated
    from sportsassets import execmirror_view as V
    src = inspect.getsource(V.launch_state)
    assert '"state": d.get("state")' in src
    assert '"state": (d.get("start") or {}).get("state")' not in src


def test_s1_every_state_change_is_logged_once(caplog):
    b = IS.ResidentBooks(clock=lambda: 100.0)
    caplog.set_level(logging.INFO, logger=IS.log.name)
    b.set_state(IS.S_IDLE, "started")
    b.set_state(IS.S_IDLE, "no symbols requested")    # same state: silent
    b.want([SLUG])
    b.set_state(IS.S_CONNECTING, "attempt 1")
    b.on_connected("c1")
    b.on_disconnected("server closed")
    b.on_refused("PERMISSION_DENIED")
    lines = [r.getMessage() for r in caplog.records
             if "institutional_stream:" in r.getMessage()]
    assert [ln.split(" (")[0] for ln in lines] == [
        "institutional_stream: NOT_STARTED -> IDLE_NO_SYMBOLS_REQUESTED",
        "institutional_stream: IDLE_NO_SYMBOLS_REQUESTED -> CONNECTING",
        "institutional_stream: CONNECTING -> CONNECTED",
        "institutional_stream: CONNECTED -> RECONNECTING",
        "institutional_stream: RECONNECTING -> REFUSED_BY_VENUE"]
    assert all("symbols=" in ln for ln in lines[1:])


# ── S2 ─────────────────────────────────────────────────────────────────

def test_s2_digest_counts_acks_and_current_books_under_both_bounds():
    now = {"t": 1_000.0}
    b = IS.ResidentBooks(clock=lambda: now["t"])
    b.set_state(IS.S_IDLE, "started")
    b.want([SLUG, "aec-mlb-old-new-2026-10-03"])
    b.on_connected("c1")
    b.on_ack([SLUG, "aec-mlb-old-new-2026-10-03"])
    _book(b, SLUG, at=now["t"])
    now["t"] += 1.0
    d = b.digest()
    assert d["acked"] == 2 and d["acks_total"] == 1
    assert d["current_books"] == 1 and d["held_mark_current_books"] == 1
    assert d["book_age_s"]["n"] == 1 and d["book_age_s"]["max"] == 1.0
    assert d["venue_receipt_lag_s"]["p50"] == pytest.approx(0.4, abs=1e-3)
    # past the decision bound but inside the held-mark bound
    now["t"] += IS.MAX_SNAPSHOT_AGE_S + 5
    b.on_heartbeat()
    d = b.digest()
    assert d["current_books"] == 0 and d["held_mark_current_books"] == 1
    # a reconnect clears the acks of the old connection
    b.on_disconnected("x")
    b.on_connected("c2")
    assert b.digest()["acked"] == 0


# ── S3 ─────────────────────────────────────────────────────────────────

def test_s3_the_bound_counts_what_it_refuses_and_retain_frees_it():
    now = {"t": 0.0}
    b = IS.ResidentBooks(clock=lambda: now["t"])
    b.want(["aec-old-%03d" % i for i in range(IS.MAX_SYMBOLS)])
    now["t"] = IS.RETAIN_IDLE_S + 1.0
    assert b.want([SLUG]) == []                       # full: refused ...
    d = b.digest()
    assert d["dropped_at_cap"] == 1 and SLUG in d["dropped_recent"]
    keep = ["aec-old-000"]                            # ... never silently
    gone = b.retain(keep)
    assert len(gone) == IS.MAX_SYMBOLS - 1 and "aec-old-000" not in gone
    assert b.want([SLUG]) == [SLUG]
    assert b.digest()["evicted"] == IS.MAX_SYMBOLS - 1
    # an entry wanted recently is never evicted; `keep` protects, it does
    # not refresh -- the kept-but-unwanted entry goes once unprotected
    now["t"] += 10.0
    assert b.retain([]) == ["aec-old-000"]
    assert b.wanted() == [SLUG]


def test_s3_the_api_wants_held_markets_first_and_frees_stale_entries():
    _start()
    now = 10_000.0
    IS.BOOKS._clock = lambda: now
    old = ["aec-old-%03d" % i for i in range(IS.MAX_SYMBOLS)]
    with IS.BOOKS._lock:
        for s in old:
            IS.BOOKS._markets[s] = IS._new_market(now - IS.RETAIN_IDLE_S - 60)
    FU.note_held_first([SLUG])

    class Client:
        def read(self, name, symbol=""):
            rec = copy.deepcopy(AEC) if symbol == SLUG else None
            return {"status": 200, "ms": 3,
                    "body": {"instruments": [rec] if rec else []}}
    got = asyncio.run(IAS.refresh_once(client=Client(), symbols=[], now=now))
    assert got["subscribed"] == 1
    assert SLUG in IS.BOOKS.wanted()
    assert IS._TRANSPORT.subs and IS._TRANSPORT.subs[-1] == [SLUG]
    r = IAS.primary_report()
    assert r["requested"] == 1 and r["evicted"] == IS.MAX_SYMBOLS
    assert r["held_wanted"] == 1 and r["held_subscribed"] == 1
    assert r["subscription_mode"] == IS.MODE_EXPLICIT
    assert IAS.SUBSCRIPTION_MODE == IS.MODE_EXPLICIT


# ── S4 ─────────────────────────────────────────────────────────────────

def test_s4_the_mechanism_label_follows_the_live_stream():
    off = pmx.market_data_mechanisms(stream_enabled=False)
    assert off["mechanism"] == pmx.MARKET_DATA_MECHANISM
    assert off["stream"]["target"] == pmx.STREAM_TARGET == "NOT_IDENTIFIED"
    idle = pmx.market_data_mechanisms(stream_enabled=True, stream_digest={
        "state": IS.S_IDLE, "connected": False, "symbols": 0})
    assert idle["mechanism"] == pmx.MECH_STREAM_NOT_CURRENT
    assert idle["stream"]["target"] == IS.GRPC_TARGET
    live = pmx.market_data_mechanisms(stream_enabled=True, stream_digest={
        "state": IS.S_CONNECTED, "connected": True, "symbols": 3,
        "current_books": 0, "held_mark_current_books": 2})
    assert live["mechanism"] == pmx.MECH_STREAM_PRIMARY
    assert live["rest_sweep"] == "REST_POLL_MAINTAINED_IN_MEMORY"
    src = inspect.getsource(W.run)
    assert '"marketDataMechanism": pmx.MARKET_DATA_MECHANISM' not in src
    assert '"streamTarget": pmx.STREAM_TARGET' not in src


# ── S5 ─────────────────────────────────────────────────────────────────

def test_s5_the_worker_heartbeat_is_time_bounded():
    assert W.beat_due(None, 0.0)
    assert not W.beat_due(100.0, 100.0 + W.HEARTBEAT_EVERY_S - 0.1)
    assert W.beat_due(100.0, 100.0 + W.HEARTBEAT_EVERY_S)
    # worst case: one idle pass after the bound, still inside the max age
    assert W.HEARTBEAT_EVERY_S + W.BOOTSTRAP_BACKOFF_S < \
        VH.HEARTBEAT_MAX_AGE_S
    assert "beats % 15" not in inspect.getsource(W.run)


# ── S6 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("beat,reason", [
    (None, "STREAM_HEARTBEAT_ABSENT"),
    ({"at": 0.0, "detail": {"stream": {"state": "CONNECTED",
                                       "connected": True}}},
     "STREAM_HEARTBEAT_STALE"),
    ({"at": 990.0, "detail": {}}, "STREAM_STATE_UNREADABLE"),
    ({"at": 990.0, "detail": {"stream": {"state": "REFUSED_BY_VENUE"}}},
     "STREAM_REFUSED_BY_VENUE"),
    ({"at": 990.0, "detail": {"stream": {
        "state": "IDLE_NO_SYMBOLS_REQUESTED", "connected": False}}},
     "STREAM_NOT_CONNECTED:IDLE_NO_SYMBOLS_REQUESTED"),
])
def test_s6_venue_health_fails_closed_on_the_stream(beat, reason):
    held = {"markable": 11, "freshly_manageable": 11, "as_of": 1000.0}
    assert VH.stream_gap(beat, now=1000.0) == reason
    rep = VH.report([VH.polymarket_health(held, beat, now=1000.0)])
    assert rep["POLYMARKET_US"]["green"] is False
    assert "STREAM_GAPS_PRESENT" in rep["POLYMARKET_US"]["blockers"]


def test_s6_a_connected_stream_with_current_held_books_is_green():
    beat = {"at": 990.0, "detail": {"stream": {
        "state": "CONNECTED", "connected": True, "symbols": 4}}}
    held = {"markable": 11, "freshly_manageable": 11, "as_of": 1000.0}
    assert VH.stream_gap(beat, now=1000.0) is None
    rep = VH.report([VH.polymarket_health(held, beat, now=1000.0)])
    assert rep["POLYMARKET_US"]["green"] is True
    assert VH.MIN_RATE == 0.95 and VH.HEARTBEAT_MAX_AGE_S == 300.0


# ── S7 ─────────────────────────────────────────────────────────────────

def _run(*, state="CONNECTED", current=5, age=20.0, now=10_000.0):
    return {"run_id": "r1", "finished_at": now - age,
            "institutional_books": 9, "held_markets": 11,
            "sources": {"institutional_stream": 9, "rest": 2},
            "market_data": {
                "institutional_refusals": {
                    "INSTITUTIONAL_SAME_BOOK_NOT_PROVEN_FOR_THIS_SYMBOL": 2},
                "streams": {"institutional": {
                    "state": state, "connected": state == "CONNECTED",
                    "symbols": 40, "acked": 40, "current_books": current,
                    "held_mark_current_books": current,
                    "subscription_mode": "EXPLICIT_SYMBOL_LIST",
                    "target": IS.GRPC_TARGET,
                    "book_age_s": {"n": current, "p50": 1.2, "max": 4.0},
                    "api_stream": {"held_wanted": 11,
                                   "held_subscribed": 9}}}}}


FEEDS = {"held_marks_by_source": {"INSTITUTIONAL_STREAM": 9, "REST": 2},
         "fresh_marks_by_source": {"INSTITUTIONAL_STREAM": 9, "REST": 2},
         "oldest_held_mark_age_s": 41.0}


def test_s7_pmx_primary_is_grpc_only_with_evidence_on_the_held_denominator():
    b = CR.pmx_primary_block(_run(), FEEDS, markable=11, now=10_000.0)
    assert b["source"] == CR.PMX_GRPC and b["why"] == []
    assert (b["requested_symbols"], b["acked_symbols"],
            b["current_l2_books"]) == (40, 40, 5)
    assert b["held_fresh_from_stream"] == 9
    assert b["held_fresh_from_rest_fallback"] == 2
    assert b["held_markable"] == 11 and b["held_stream_rate"] == 0.8182
    assert b["fallback_reasons"] == {
        "INSTITUTIONAL_SAME_BOOK_NOT_PROVEN_FOR_THIS_SYMBOL": 2}


@pytest.mark.parametrize("run,feeds,why", [
    (None, FEEDS, "NO_HELD_MARK_REFRESH_RUN_RECORDED"),
    (_run(age=CR.PMX_RUN_MAX_AGE_S + 1), FEEDS, "HELD_MARK_RUN_OLDER_THAN_300S"),
    (_run(state="IDLE_NO_SYMBOLS_REQUESTED", current=0), FEEDS,
     "STREAM_NOT_CONNECTED:IDLE_NO_SYMBOLS_REQUESTED"),
    (_run(current=0), FEEDS, "NO_CURRENT_RESIDENT_L2_BOOK"),
    (_run(), {"held_marks_by_source": {"REST": 11},
              "fresh_marks_by_source": {"REST": 11}},
     "NO_HELD_MARK_FROM_THE_STREAM"),
])
def test_s7_anything_unproven_is_rest_with_the_reason(run, feeds, why):
    b = CR.pmx_primary_block(run, feeds, markable=11, now=10_000.0)
    assert b["source"] == CR.PMX_REST and why in b["why"]


# ── S8 ─────────────────────────────────────────────────────────────────

def _red(md, plane="DEDICATED_RUNNING"):
    # the snapshot is the DEDICATED plane's evidence only while the plane is
    # read as running (pm_bind.acceptance, PM review of RC4)
    return {"implementation_sha": "a" * 40,
            "completion": {"small_live": "SHADOW", "market_data": md,
                           "runtime": {"market_plane": {"state": plane}}}}


@pytest.mark.parametrize("plane", ["ABSENT", "STALE", None])
def test_s8_a_current_snapshot_without_a_running_dedicated_plane_is_withheld(
        plane):
    """A CURRENT snapshot while the dedicated plane is not read as running
    is not the dedicated plane's evidence: PMX primary acceptance is
    withheld (REST, fresh count left out, the reason named) exactly as for
    a stale or absent snapshot."""
    md = {"snapshot": "CURRENT", "fresh": 812, "subscription_mode": "ALL"}
    red = _red(md, plane)
    if plane is None:
        red["completion"].pop("runtime")
    r = PA.evaluate(red=red, scoreboard={}, release=None, now=10_000.0)
    assert r["evidence_input"]["pmx_primary_source"] == "REST"
    assert "pmx_grpc_fresh_count" not in r["evidence_input"]
    assert r["unproven"]["pmx_grpc_fresh_count"] == [
        PA.R_PMX_PLANE_NOT_DEDICATED]
    assert r["gates"]["pmx_grpc_primary"]["pass"] is False
    assert PA.R_PMX_PLANE_NOT_DEDICATED in \
        r["provenance"]["pmx_primary_source"]["source"]


@pytest.mark.parametrize("md", [
    {"snapshot": "MARKET_PLANE_SNAPSHOT_STALE_19660s", "fresh": 812,
     "subscription_mode": "ALL"},
    {"snapshot": "NO_MARKET_PLANE_SNAPSHOT"},
    {"snapshot": None, "fresh": 812, "subscription_mode": "ALL"},
    {}])
def test_s8_a_stale_or_absent_plane_snapshot_withholds_primary_acceptance(md):
    """PMX (defect 6): stale, absent, unlabelled or unread snapshot -> the
    pmx_grpc_primary gate never passes and the fresh count is never read,
    whatever the snapshot body claims."""
    r = PA.evaluate(red=_red(md), scoreboard={}, release=None, now=10_000.0)
    assert "pmx_grpc_fresh_count" not in r["evidence_input"]
    assert r["evidence_input"].get("pmx_primary_source") in (None, "REST")
    assert r["gates"]["pmx_grpc_primary"]["pass"] is False
    assert "pmx_grpc_primary" in r["economic_or_evidence_gaps"]


def test_s8_no_current_plane_snapshot_is_rest_and_the_process_only_reported():
    """The harness reads PMX from the dedicated plane's CURRENT snapshot
    only: a stale snapshot -- even one that says fresh > 0 -- is REST with
    the fresh count withheld, and the deciding process's own stream is
    REPORTED in provenance, never substituted into a harness input."""
    pp = CR.pmx_primary_block(_run(), FEEDS, markable=11, now=10_000.0)
    assert pp["source"] == "PMX_GRPC"           # the process stream is up
    e, prov = PA.collect(red=_red({"snapshot": "MARKET_PLANE_SNAPSHOT_STALE_"
                                               "19660s", "fresh": 812,
                                   "subscription_mode": "ALL",
                                   "pmx_primary": pp}),
                         scoreboard={}, release=None, now=10_000.0)
    assert e["pmx_primary_source"] == "REST"
    assert "pmx_grpc_fresh_count" not in e
    rep = prov["pmx_primary_source"]["deciding_process_report"]
    assert rep["source"] == "PMX_GRPC" and rep["held_fresh_from_stream"] == 9
    g = PA.evaluate(red=_red({"snapshot": "NO_MARKET_PLANE_SNAPSHOT",
                              "pmx_primary": pp}),
                    scoreboard={}, release=None, now=10_000.0)["gates"]
    assert g["pmx_grpc_primary"]["pass"] is False
    assert g["priority_freshness"]["pass"] is False
    # a CURRENT plane snapshot is the only road to PMX_GRPC
    e, _ = PA.collect(red=_red({"snapshot": "CURRENT", "fresh": 812,
                                "subscription_mode": "ALL",
                                "pmx_primary": pp}),
                      scoreboard={}, release=None, now=10_000.0)
    assert (e["pmx_primary_source"], e["pmx_grpc_fresh_count"]) == (
        "PMX_GRPC", 812)


def test_s8_unproven_primary_fails_the_gate_and_a_current_plane_wins():
    rest = CR.pmx_primary_block(_run(state="IDLE_NO_SYMBOLS_REQUESTED",
                                     current=0), FEEDS, markable=11,
                                now=10_000.0)
    g = PA.evaluate(red=_red({"snapshot": "NO_MARKET_PLANE_SNAPSHOT",
                              "pmx_primary": rest}),
                    scoreboard={}, release=None, now=10_000.0)["gates"]
    assert g["pmx_grpc_primary"]["pass"] is False
    pp = CR.pmx_primary_block(_run(), FEEDS, markable=11, now=10_000.0)
    e, prov = PA.collect(red=_red({"snapshot": "CURRENT", "fresh": 0,
                                   "subscription_mode": "SUBSCRIBE_ALL",
                                   "pmx_primary": pp}),
                         scoreboard={}, release=None, now=10_000.0)
    assert e["pmx_primary_source"] == "REST"         # the plane's own answer
    assert prov["pmx_primary_source"]["source"] == \
        "market plane snapshot subscription"


def test_the_persisted_market_data_carries_the_primary_evidence():
    from sportsassets import paper_market_data as PMD
    _start()
    IS.want([SLUG])
    IS.BOOKS.on_connected("c1")
    inst = PMD.stream_updates()["institutional"]
    for k in ("acked", "current_books", "held_mark_current_books",
              "book_age_s", "dropped_at_cap", "subscription_mode", "target"):
        assert k in inst, k
    from sportsassets.agents import paper_mark_refresh as PMR
    assert 'out["market_data"]["institutional_refusals"]' in \
        inspect.getsource(PMR.refresh)


# ── R: adversarial review of 884ebb7a ──────────────────────────────────

@pytest.mark.parametrize("stream,rest,src", [
    (1, 10, CR.PMX_REST), (5, 5, CR.PMX_REST), (6, 5, CR.PMX_GRPC)])
def test_r1_primary_means_the_majority_held_mark_source(stream, rest, src):
    feeds = {"held_marks_by_source": {"INSTITUTIONAL_STREAM": stream,
                                      "REST": rest},
             "fresh_marks_by_source": {"INSTITUTIONAL_STREAM": stream,
                                       "REST": rest}}
    b = CR.pmx_primary_block(_run(), feeds, markable=stream + rest,
                             now=10_000.0)
    assert b["source"] == src
    if src == CR.PMX_REST:
        assert "STREAM_NOT_THE_MAJORITY_HELD_MARK_SOURCE" in b["why"]
        g = PA.evaluate(red=_red({"snapshot": "NO_MARKET_PLANE_SNAPSHOT",
                                  "pmx_primary": b}),
                        scoreboard={}, release=None, now=10_000.0)["gates"]
        assert g["pmx_grpc_primary"]["pass"] is False


def test_r2_a_held_market_preempts_entries_this_pass_does_not_want():
    """retain() frees only entries idle RETAIN_IDLE_S; 200 entries named
    inside that window (focus churn, the decision path's own wants) must
    still not refuse a newly held market."""
    _start()
    now = 10_000.0
    IS.BOOKS._clock = lambda: now
    recent = ["aec-cand-%03d" % i for i in range(IS.MAX_SYMBOLS)]
    with IS.BOOKS._lock:
        for s in recent:
            IS.BOOKS._markets[s] = IS._new_market(now - 60.0)
    FU.note_held_first([SLUG])

    class Client:
        def read(self, name, symbol=""):
            rec = copy.deepcopy(AEC) if symbol == SLUG else None
            return {"status": 200, "ms": 3,
                    "body": {"instruments": [rec] if rec else []}}
    got = asyncio.run(IAS.refresh_once(client=Client(), symbols=[], now=now))
    assert got["subscribed"] == 1
    assert SLUG in IS.BOOKS.wanted()
    assert IS._TRANSPORT.subs and IS._TRANSPORT.subs[-1] == [SLUG]
    r = IAS.primary_report()
    assert r["held_subscribed"] == 1 and r["held_preempted"] == IS.MAX_SYMBOLS
    assert r["dropped_at_cap"] == 1          # the first try, counted


def test_r3_a_re_want_on_the_same_connection_keeps_its_ack():
    now = {"t": 0.0}
    b = IS.ResidentBooks(clock=lambda: now["t"])
    b.want(["A"])
    b.on_connected("c1")
    b.on_ack(added=["A"])
    now["t"] = IS.RETAIN_IDLE_S + 1.0
    assert b.retain([]) == ["A"]
    b.want(["A"])                 # still subscribed at the venue
    assert b.digest()["acked"] == 1
    now["t"] += IS.RETAIN_IDLE_S + 1.0
    b.retain([])
    b.on_connected("c2")          # a new connection forgets it
    b.want(["A"])
    assert b.digest()["acked"] == 0


def test_r3_the_venue_side_set_is_bounded_by_recycling_the_connection():
    """Nothing is unsubscribed, so evicted entries stay subscribed at the
    venue; past SUBSCRIBED_RECYCLE_AT the watchdog (the one place that ends
    a call) recycles the connection and nothing new is sent on it."""
    import time as _t

    class Call:
        cancelled = 0

        def cancel(self):
            Call.cancelled += 1
    books = IS.ResidentBooks()
    t = IS.GrpcBidiTransport(books, lambda: "t",
                             modules=IS.load_generated())
    t._subscribed = {"S%04d" % i for i in range(IS.SUBSCRIBED_RECYCLE_AT)}
    t.subscribe(["NEW"])
    assert t.recycles == 1 and t._recycle.is_set()
    assert t._q.empty()           # nothing sent: the next connection asks
    done = t._watchdog(Call())
    deadline = _t.monotonic() + 5.0
    while Call.cancelled == 0 and _t.monotonic() < deadline:
        _t.sleep(0.05)
    done.set()
    assert Call.cancelled == 1
    assert IS.SUBSCRIBED_RECYCLE_AT < 1000      # documented per stream
    assert IS.OUTBOUND_COMMANDS == ("subscribe", "keepalive")
    t2 = IS.GrpcBidiTransport(IS.ResidentBooks(), lambda: "t",
                              modules=IS.load_generated())
    t2.subscribe(["A", "B"])
    assert not t2._q.empty() and t2.recycles == 0


def test_r3_a_market_plane_shard_recycles_only_past_its_own_capacity():
    """A UMP explicit-mode shard (SizedBooks, max_symbols=1000) shares the
    transport: it must not recycle at the deciding stream's 400 on every
    connection (an endless reconnect loop in UMP_SUBSCRIBE_ALL=off), only
    past the documented 1,000 symbols per stream."""
    from sportsassets.market_plane.sharded_stream import SizedBooks
    books = SizedBooks.create(IS.ResidentBooks, 1000, lambda: 0.0)
    t = IS.GrpcBidiTransport(books, lambda: "t",
                             modules=IS.load_generated())
    assert t._recycle_at() == 1000
    t.subscribe(["S%04d" % i for i in range(1000)])
    assert t.recycles == 0 and not t._recycle.is_set()
    assert len(t._subscribed) == 1000
    t.subscribe(["ONE_MORE"])
    assert t.recycles == 1 and t._recycle.is_set()
    # the deciding stream's books keep the tighter bound
    t2 = IS.GrpcBidiTransport(IS.ResidentBooks(), lambda: "t",
                              modules=IS.load_generated())
    assert t2._recycle_at() == IS.SUBSCRIBED_RECYCLE_AT


@pytest.mark.parametrize("pp,reason", [
    (None, "API_STREAM_EVIDENCE_ABSENT"),
    ({"run_age_s": 400.0, "state": "CONNECTED", "connected": True},
     "API_STREAM_RUN_STALE"),
    ({"run_age_s": 20.0, "state": "GAVE_UP", "connected": False},
     "API_STREAM_GAVE_UP"),
    ({"run_age_s": 20.0, "state": "IDLE_NO_SYMBOLS_REQUESTED",
      "connected": False},
     "API_STREAM_NOT_CONNECTED:IDLE_NO_SYMBOLS_REQUESTED"),
])
def test_r4_venue_health_also_judges_the_deciding_stream(pp, reason):
    held = {"markable": 11, "freshly_manageable": 11, "as_of": 1000.0}
    beat = {"at": 990.0, "detail": {"stream": {
        "state": "CONNECTED", "connected": True}}}
    assert VH.api_stream_gap(pp, markable=11) == reason
    rep = VH.report([VH.polymarket_health(held, beat, now=1000.0,
                                          api_stream=pp,
                                          judge_api_stream=True)])
    assert rep["POLYMARKET_US"]["green"] is False
    ok = {"run_age_s": 20.0, "state": "CONNECTED", "connected": True}
    assert VH.api_stream_gap(ok, markable=11) is None
    assert VH.api_stream_gap(None, markable=0) is None   # nothing to mark
    rep = VH.report([VH.polymarket_health(held, beat, now=1000.0,
                                          api_stream=ok,
                                          judge_api_stream=True)])
    assert rep["POLYMARKET_US"]["green"] is True


def test_r4_the_red_team_evidence_names_both_stream_gaps():
    from sportsassets.redteam import readiness as RR
    src = inspect.getsource(RR)
    assert '"polymarket_stream_gap": vh.get(' in src
    assert '"polymarket_api_stream_gap": vh.get(' in src
    assert 'api_stream=md.get("pmx_primary")' in src


def test_r5_no_boot_line_labels_the_process_with_the_pre_stream_constants():
    from sportsassets.workers import shadow_experimental as SX
    src = inspect.getsource(SX.run)
    assert "streamTarget=pmx.STREAM_TARGET" not in src
    assert "marketDataMechanism=pmx.MARKET_DATA_MECHANISM" not in src
