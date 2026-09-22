"""The incentive observation release, pinned where it could regress.

The tests are grouped by the gap each one closes, and the headline is
`test_a_quiet_book_stays_research_valid_long_past_the_trading_bound`:
that is the error the earlier spec made, and a change-driven feed makes
it easy to make again.

Run:  python -m pytest backend/tests/test_bettor_incentive_release.py
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import bettor_incentive_budget as bud
from sportsassets import bettor_incentive_feed as feed_mod
from sportsassets import bettor_incentive_journal as jrnl_mod
from sportsassets import bettor_incentive_manifest as man
from sportsassets import bettor_market_stream as ms
from sportsassets import bettor_live_control as ctl
from sportsassets.workers import bettor_incentive_observe as obs

# The repo's convention for tests that need a real server. Skipping is
# honest; a fake pool would prove nothing about a row lock.
PG_DSN = os.environ.get("BETTOR_TEST_PG_DSN")
needs_pg = pytest.mark.skipif(not PG_DSN,
                              reason="BETTOR_TEST_PG_DSN names no server")

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "sportsassets")

RELEASE_MODULES = ("bettor_incentive_state.py", "bettor_incentive_manifest.py",
                   "bettor_incentive_budget.py", "bettor_incentive_feed.py",
                   "bettor_incentive_journal.py",
                   "workers/bettor_incentive_observe.py")


def _code_only(name: str) -> str:
    """The module's CODE, with docstrings and comments removed.

    A grep over raw source cannot tell a call from a sentence about a
    call, and these modules explain at length what they deliberately do
    NOT do -- so a naive search finds `observation_subset` in the very
    paragraph promising never to use it. Parsing, dropping every
    docstring and unparsing leaves executable code and string literals
    that are actually evaluated, which is what the assertions are about.
    """
    tree = ast.parse(open(os.path.join(PKG, name), encoding="utf-8").read())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and \
                    isinstance(first.value, ast.Constant) and \
                    isinstance(first.value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


# ── fixtures ─────────────────────────────────────────────────────────

def _program(slug, *, program_id="p1", event="2026-12-27T04:59:00.000Z",
             pool=50.0, df=0.25, ts=500, state="INSTRUMENT_STATE_OPEN"):
    return {"market_slug": slug, "program_id": program_id,
            "created_at": "2026-09-21T00:00:00Z", "period": man.PERIOD_DAILY,
            "reward_pool": pool, "discount_factor": df, "target_size": ts,
            "event_start_time": event, "instrument_state": state,
            "start": "2026-09-23T04:00:00Z", "end": "2026-09-24T04:00:00Z"}


def _manifest(n=12, **kw):
    return {"manifest": man.MANIFEST_VERSION, "manifest_id": "abc123",
            "et_date": "2026-09-23", "response_digest": "sha256:deadbeef",
            "programs": [_program("m%02d" % i, **kw) for i in range(n)]}


# ═══ 1. THE FROZEN ALLOWLIST ═════════════════════════════════════════

def test_fewer_than_ten_markets_refuses_and_names_the_shortfall():
    out = man.freeze(_manifest(9))
    assert out["ok"] is False
    assert out["why"] == man.M_INSUFFICIENT
    assert out["markets"] == 9 and out["min_markets"] == 10
    assert "does NOT fall back" in out["detail"]


def test_the_refusal_carries_no_slugs_to_watch_instead():
    """INSUFFICIENT COVERAGE is a RESULT, not a prompt to find others."""
    out = man.freeze(_manifest(3))
    assert out["ok"] is False
    assert out.get("frozen") is not True


def test_no_module_in_this_release_can_reach_the_seeded_universe():
    """The fallback is absent STRUCTURALLY, not by discipline.

    `observation_subset` and `bettor_universe` are how the general loop
    chooses markets. If either ever appears in these modules, the
    allowlist has stopped being the only source of truth.
    """
    for name in RELEASE_MODULES:
        code = _code_only(name)
        for ident in ("observation_subset", "bettor_universe", "_discover"):
            # WORD BOUNDARIES, because a substring search is not a
            # reference check: `_discover` is inside
            # `terms_arrive_with_discovery`, which is a dictionary key
            # and not a call to the general loop's acquisition.
            assert not re.search(r"\b%s\b" % re.escape(ident), code), \
                "%s references %s" % (name, ident)


def test_the_watch_cap_is_a_total_and_truncation_is_named():
    out = man.freeze(_manifest(15), watch_max=12)
    assert out["ok"] is True
    assert out["markets"] == 12
    assert len(out["slugs"]) == 12
    assert out["dropped_by_watch_cap"] == ["m12", "m13", "m14"]


def test_truncation_follows_manifest_order_not_a_fresh_ranking():
    """The order was fixed at capture, before any book was seen."""
    out = man.freeze(_manifest(15), watch_max=11)
    assert out["slugs"] == ["m%02d" % i for i in range(11)]


def test_markets_programmes_and_events_are_three_counts():
    """Ten markets sharing one event are NOT ten independent units."""
    out = man.freeze(_manifest(12))
    assert out["markets"] == 12
    assert out["distinct_programs"] == 1
    assert out["distinct_events"] == 1
    assert "NOT independent statistical units" in out["independence_note"]


def test_distinct_events_moves_when_the_events_actually_differ():
    m = _manifest(0)
    m["programs"] = [_program("m%02d" % i, program_id="p%d" % i,
                              event="2026-12-%02dT00:00:00Z" % (i + 1))
                     for i in range(10)]
    out = man.freeze(m)
    assert (out["markets"], out["distinct_programs"],
            out["distinct_events"]) == (10, 10, 10)


def test_an_active_programme_on_a_closed_instrument_does_not_qualify():
    """`status: active` on the programme is not the instrument open."""
    m = _manifest(12, state="INSTRUMENT_STATE_CLOSED")
    out = man.freeze(m)
    assert out["ok"] is False
    assert all(r["why"] == "INSTRUMENT_NOT_OPEN" for r in out["rejected"])


def test_a_duplicate_market_is_counted_once_toward_a_market_gate():
    m = _manifest(10)
    m["programs"].append(_program("m00"))
    out = man.freeze(m)
    assert out["markets"] == 10
    assert any(r["why"] == "DUPLICATE_MARKET_IN_MANIFEST"
               for r in out["rejected"])


def test_an_absent_manifest_is_a_named_refusal_not_an_empty_universe():
    out = man.load("/nonexistent/manifest.json")
    assert out["ok"] is False and out["why"] == man.M_ABSENT


@pytest.mark.parametrize("field,value,why", [
    ("program_id", "p9", man.DRIFT_IDENTITY),
    ("created_at", "2026-09-22T00:00:00Z", man.DRIFT_IDENTITY),
    ("reward_pool", 75.0, man.DRIFT_TERMS),
    ("discount_factor", 0.5, man.DRIFT_TERMS),
    ("target_size", 5000, man.DRIFT_TERMS),
])
def test_drift_voids_on_identity_or_terms_and_records_both_sides(
        field, value, why):
    before = _program("m00")
    after = dict(before, **{field: value})
    d = man.drift(before, after)
    assert d["changed"] is True and d["why"] == why
    assert d["before"] != d["after"]        # both sets recorded


def test_a_programme_gone_at_recheck_is_its_own_verdict():
    assert man.drift(_program("m00"), None)["why"] == man.DRIFT_GONE


# ── the window ───────────────────────────────────────────────────────

def test_the_window_is_half_open_and_one_et_date():
    w = man.et_window("2026-09-23")
    assert w["start_iso"] == "2026-09-23T00:00:00-04:00"
    assert w["end_iso"] == "2026-09-24T00:00:00-04:00"
    assert "[start, end)" in w["half_open"]


def test_the_dst_dates_are_23_and_25_hours_not_24():
    """The bug this catches returned exactly 86,400 for both.

    Subtracting two aware datetimes that share a tzinfo OBJECT is done
    on the wall clock, so the DST hour vanishes silently. These are the
    two dates in the year where that matters.
    """
    assert man.et_window("2027-03-14")["scoring_instants"] == 82800
    assert man.et_window("2026-11-01")["scoring_instants"] == 90000
    assert man.et_window("2026-09-23")["scoring_instants"] == 86400


# ═══ 2. THE EIGHT-REQUEST BUDGET ═════════════════════════════════════
#
# THE CEILING IS A POSTGRES ROW, NOT AN OBJECT. The crash and
# overlapping-worker evidence needs a real server and lives in
# `research/beta48/incentive_durability_proof.py`, which runs every
# scenario against one. What is asserted here is what can be asserted
# without one: the arithmetic that makes the sub-caps a total, the
# refusals that happen before any reservation, and the per-run scope of
# the socket bounds.

def test_the_subcaps_sum_to_the_declared_total():
    """This is what makes per-kind enforcement TOTAL enforcement.

    `reserve()` checks one kind's cap per call and knows nothing about
    a total. That is safe only while 4 + 2 + 2 == 8: there is then no
    combination of grants reaching nine without some kind exceeding its
    own cap. Change one number and this fails, which is the point.
    """
    assert sum(bud.SUBCAPS.values()) == bud.TOTAL_CAP == 8
    assert ctl.INCENTIVE_TOTAL == bud.TOTAL_CAP
    assert (ctl.INCENTIVE_MAX_MANIFEST, ctl.INCENTIVE_MAX_RECHECK,
            ctl.INCENTIVE_MAX_RETRY) == (4, 2, 2)


def test_every_incentive_kind_has_a_counter_and_a_cap_in_the_row():
    for k, ctl_kind in bud.CTL_KIND.items():
        assert ctl_kind in ctl._COUNTER, k
        assert ctl_kind in ctl._CAP, k
        assert ctl_kind in ctl._DEFAULT_CAP, k


def test_the_ledger_holds_no_counter_of_its_own():
    """A count in the process is re-granted on every restart."""
    L = bud.DurableLedger(None, probe_id="p")
    assert not hasattr(L, "seed")
    assert not hasattr(L, "total")
    assert L.report()["authoritative_count"] == \
        "the allowance row, not this object"
    assert "reserve" in L.report()["enforcement"]


@pytest.mark.asyncio
async def test_listing_bbo_and_settlement_are_refused_before_any_reservation():
    """The general loop's acquisition spends ~1,686 requests.

    These are refused BY NAME and BEFORE the database is touched, so a
    code path that reached one fails loudly rather than quietly
    spending the venue's patience. `pool=None` proves no reservation
    was attempted: a reservation would have tried to resolve a pool.
    """
    L = bud.DurableLedger(None, probe_id="p")
    for kind in ("listing", "bbo_attempt", "distinct", "settlement"):
        r = await L.spend(kind)
        assert r["ok"] is False and r["verdict"] == bud.R_FORBIDDEN, kind


@pytest.mark.asyncio
async def test_an_unknown_kind_is_refused_rather_than_guessed():
    r = await bud.DurableLedger(None, probe_id="p").spend("whatever")
    assert r["ok"] is False and r["verdict"] == bud.R_UNKNOWN


def test_no_environment_variable_sets_the_request_cap():
    """It used to, and that was the wrong home for it."""
    assert not hasattr(bud, "TOTAL_ENV")
    src = _code_only("bettor_incentive_budget.py")
    assert "BETTOR_INCENTIVE_HTTP_CAP" not in src


def test_reconnects_and_resubscribes_are_bounded_separately_and_per_run():
    b = bud.ReconnectBounds(max_reconnects=2, max_resubscribes=5)
    assert b.check(2)["ok"] is True
    assert b.check(3)["why"] == bud.RC_RECONNECTS
    b2 = bud.ReconnectBounds(max_reconnects=100, max_resubscribes=2)
    b2.note_resubscribe(3)
    assert b2.check(0)["why"] == bud.RC_RESUBSCRIBES


def test_a_crash_loop_cannot_buy_a_fresh_reconnect_allowance():
    """Per boot would not be a bound: the supervisor restarts forever."""
    b = bud.ReconnectBounds(max_reconnects=20, max_resubscribes=40,
                            prior_reconnects=20, prior_resubscribes=0)
    v = b.check(1)
    assert v["ok"] is False and v["why"] == bud.RC_RECONNECTS
    assert v["scope"].startswith("PER RUN")
    assert v["prior_boots"]["reconnects"] == 20
    assert v["this_boot"]["reconnects"] == 1


# ── capture, with a ledger that records the ORDER of events ──────────

class _SpyLedger:
    """Grants up to the sub-caps and records reserve/dispatch order."""

    def __init__(self, caps=None):
        self.caps = dict(caps or bud.SUBCAPS)
        self.spent = {k: 0 for k in self.caps}
        self.events: list = []

    async def spend(self, kind, *, why=""):
        if kind in bud.FORBIDDEN:
            return {"ok": False, "verdict": bud.R_FORBIDDEN}
        if self.spent.get(kind, 0) >= self.caps.get(kind, 0):
            self.events.append(("refused", kind))
            return {"ok": False, "verdict": bud.R_SUBCAP, "detail": "cap"}
        self.spent[kind] += 1
        self.events.append(("reserved", kind))
        return {"ok": True, "verdict": bud.GRANTED, "durable": True}

    def report(self):
        return {"by_kind": dict(self.spent)}


@pytest.mark.asyncio
async def test_capture_reserves_before_it_dispatches():
    L = _SpyLedger()

    def get(_url, _params):
        L.events.append(("dispatched", None))
        return 200, {"programs": [], "nextPageToken": None}

    await man.capture(get, L, et_date="2026-09-23")
    # EVERY dispatch is preceded by a reservation, with none in flight.
    assert L.events[0][0] == "reserved"
    assert L.events[1][0] == "dispatched"


@pytest.mark.asyncio
async def test_capture_charges_every_page_and_charges_a_retry_to_retry():
    calls = {"n": 0}

    def get(_url, params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("first attempt fails")
        if calls["n"] == 2:
            return 200, {"programs": [], "nextPageToken": "t2"}
        return 200, {"programs": [], "nextPageToken": None}

    L = _SpyLedger()
    out = await man.capture(get, L, et_date="2026-09-23")
    assert out["pages_read"] == 2
    assert L.spent[bud.K_MANIFEST] == 2     # one per page
    assert L.spent[bud.K_RETRY] == 1        # the failure, charged apart
    assert sum(L.spent.values()) == 3


@pytest.mark.asyncio
async def test_capture_stops_at_a_refusal_rather_than_dispatching():
    sent = {"n": 0}

    def get(_url, _params):
        sent["n"] += 1
        return 200, {"programs": [], "nextPageToken": "more"}

    L = _SpyLedger()
    await man.capture(get, L, et_date="2026-09-23", max_pages=10)
    assert sent["n"] <= bud.SUBCAPS[bud.K_MANIFEST]
    assert sum(L.spent.values()) <= bud.TOTAL_CAP


@pytest.mark.asyncio
async def test_terms_arrive_with_discovery_so_no_per_market_call_is_budgeted():
    """The four-request arm phase only works because of this."""
    body = {"programs": [{
        "marketSlug": "m00", "eventStartTime": "2026-12-27T04:59:00.000Z",
        "instrumentState": "INSTRUMENT_STATE_OPEN",
        "timePeriods": [{"programId": "p1", "createdAt": "2026-09-21T00:00:00Z",
                         "period": man.PERIOD_DAILY, "rewardPool": 50,
                         "discountFactor": 0.25, "targetSize": 500,
                         "start": "s", "end": "e", "status": "active"}]}],
        "nextPageToken": None}
    L = _SpyLedger()
    out = await man.capture(lambda u, p: (200, body), L, et_date="2026-09-23")
    p = out["programs"][0]
    assert sum(L.spent.values()) == 1       # ONE request
    assert (p["reward_pool"], p["discount_factor"], p["target_size"]) \
        == (50.0, 0.25, 500.0)


@pytest.mark.asyncio
async def test_capture_is_unauthenticated_and_says_so():
    out = await man.capture(lambda u, p: (200, {"programs": []}),
                            _SpyLedger(), et_date="2026-09-23")
    assert out["authenticated"] is False
    assert "earnings" not in out["source"]


# ═══ 3. FEED HEALTH IS NOT BOOK AGE ══════════════════════════════════

def _stream_with_book(slug="m00", *, ts=None, state="MARKET_STATE_OPEN"):
    s = ms.MarketStream("k", "s")
    s.connected = True
    s.epoch = 1
    s._on_market_data({"marketData": {
        "marketSlug": slug, "state": state,
        "transactTime": (ts or datetime.now(timezone.utc).isoformat()),
        "bids": [{"px": "0.50", "qty": "400"}],
        "offers": [{"px": "0.52", "qty": "400"}], "stats": {}}})
    return s


def test_a_quiet_book_stays_research_valid_long_past_the_trading_bound():
    """THE HEADLINE. A change-driven book sends nothing when nothing
    changes, and it is CURRENT the whole time. The trading gate calls
    it stale after five seconds -- correctly, for trading -- and the
    research verdict must not inherit that.
    """
    now = time.time()
    quiet_since = now - 600.0               # ten minutes without a change
    s = _stream_with_book(ts=datetime.fromtimestamp(
        quiet_since, tz=timezone.utc).isoformat())
    # BOTH of the trading gate's clocks are genuinely old. Moving only
    # the sampling instant would leave `book_at` reading a fresh
    # receipt and the test would prove nothing.
    s._books["m00"]["received_at"] = quiet_since
    h = feed_mod.FeedHealth(s)
    h.on_book("m00", s._books["m00"])
    # ANOTHER market spoke a second ago, so the CONNECTION is alive
    # while THIS book has simply not changed.
    s.last_frame_at = now - 1.0
    r = h.sample("m00", now=now,
                 max_source_age_s=obs.MAX_SOURCE_AGE_S,
                 max_receipt_age_s=obs.MAX_RECEIPT_AGE_S)
    assert r["research_valid"] is True
    assert r["research_why"] == feed_mod.VALID
    assert r["book_age_s"] > 5.0            # reported, not used as a gate
    assert r["trading_eligible"] is False   # and the trading gate still says no
    assert r["trading_reason"] in (ms.STALE_SOURCE, ms.STALE_RECEIPT)


def test_the_trading_gate_is_called_not_reimplemented_and_not_relaxed():
    s = _stream_with_book()
    h = feed_mod.FeedHealth(s)
    h.on_book("m00", s._books["m00"])
    now = time.time()
    direct = s.book_at("m00", max_source_age_s=10.0, max_receipt_age_s=5.0)
    r = h.sample("m00", now=now, max_source_age_s=10.0, max_receipt_age_s=5.0)
    assert r["trading_eligible"] == bool(direct["eligible"])
    assert r["trading_reason"] == direct["reason"]


def test_a_disconnect_is_a_gap_and_not_a_stale_book():
    s = _stream_with_book()
    h = feed_mod.FeedHealth(s)
    h.on_book("m00", s._books["m00"])
    s.connected = False
    r = h.sample("m00", now=time.time(), max_source_age_s=10.0,
                 max_receipt_age_s=5.0)
    assert r["research_valid"] is False
    assert r["research_why"] == feed_mod.G_DISCONNECTED


def test_a_new_epoch_needs_a_fresh_initial_ladder_before_scoring():
    """A reconnect voids every cached book; the missed interval cannot
    be replayed, so the slug is unscorable until it speaks again."""
    s = _stream_with_book()
    h = feed_mod.FeedHealth(s)
    h.on_book("m00", s._books["m00"])
    s.epoch = 2                             # reconnected
    s.last_frame_at = time.time()
    r = h.sample("m00", now=time.time(), max_source_age_s=10.0,
                 max_receipt_age_s=5.0)
    assert r["research_why"] == feed_mod.G_NO_INITIAL
    # ...and it recovers the moment a full ladder arrives.
    s._on_market_data({"marketData": {
        "marketSlug": "m00", "state": "MARKET_STATE_OPEN",
        "transactTime": datetime.now(timezone.utc).isoformat(),
        "bids": [{"px": "0.50", "qty": "400"}], "offers": [], "stats": {}}})
    c = h.on_book("m00", s._books["m00"])
    assert c["ladder_class"] == feed_mod.INITIAL_LADDER
    assert h.sample("m00", now=time.time(), max_source_age_s=10.0,
                    max_receipt_age_s=5.0)["research_valid"] is True


def test_total_silence_without_a_heartbeat_is_undetermined_not_valid():
    """The honest third answer: with no heartbeat, a quiet universe and
    a dead socket are indistinguishable. It is neither promoted to
    valid nor blamed on the book."""
    s = _stream_with_book()
    h = feed_mod.FeedHealth(s, silence_max_s=60.0)
    h.on_book("m00", s._books["m00"])
    far = time.time() + 3600.0
    live = h.liveness(far)
    assert live["alive"] is False
    assert live["why"] == feed_mod.G_LIVENESS
    assert live["heartbeat_available"] is False
    r = h.sample("m00", now=far, max_source_age_s=10.0, max_receipt_age_s=5.0)
    assert r["research_why"] == feed_mod.G_LIVENESS


def test_a_heartbeat_resolves_silence_and_the_quiet_book_stays_valid():
    s = _stream_with_book()
    h = feed_mod.FeedHealth(s, silence_max_s=60.0)
    h.on_book("m00", s._books["m00"])
    far = time.time() + 3600.0
    s.heartbeats = 1
    s.last_heartbeat_at = far - 1.0
    r = h.sample("m00", now=far, max_source_age_s=10.0, max_receipt_age_s=5.0)
    assert r["research_valid"] is True
    assert r["feed"]["heartbeats_seen"] == 1


def test_the_stream_registers_a_heartbeat_listener_and_records_the_outcome():
    s = ms.MarketStream("k", "s")
    assert s.heartbeats == 0
    assert s.heartbeat_registered is None    # not yet connected
    seen = []
    s.set_heartbeat_listener(seen.append)
    s._on_heartbeat({"heartbeat": {}})
    assert s.heartbeats == 1 and len(seen) == 1
    assert ms.describe()["heartbeat"]["arrival_verified"] is False


def test_a_market_that_is_not_open_is_a_gap_with_its_own_reason():
    s = _stream_with_book(state="MARKET_STATE_HALTED")
    h = feed_mod.FeedHealth(s)
    h.on_book("m00", s._books["m00"])
    r = h.sample("m00", now=time.time(), max_source_age_s=10.0,
                 max_receipt_age_s=5.0)
    assert r["research_why"] == feed_mod.G_NOT_OPEN


def test_book_semantics_stay_labelled_as_assumed():
    assert "ASSUMED_FULL_REPLACEMENT" in \
        feed_mod.FeedHealth(_stream_with_book()).report()["book_semantics"]


# ═══ 4. WHAT IS PERSISTED, AND WHAT A GAP MEANS ══════════════════════
#
# The destination is Postgres and there is no other one, so the
# persistence tests need a server. `incentive_durability_proof.py`
# runs the same properties end to end against one.

def test_the_ephemeral_file_backend_is_gone_not_demoted():
    """/var/tmp passes every fsync and loses everything on a deploy."""
    assert not hasattr(jrnl_mod, "Journal")
    assert not hasattr(jrnl_mod, "directory_for")
    assert not hasattr(jrnl_mod, "DIR_ENV")
    assert "REMOVED" in jrnl_mod.describe()["ephemeral_file_backend"]
    assert jrnl_mod.describe()["destination"].startswith("postgres")


def test_the_reconstruction_key_is_the_pair_not_the_bare_epoch():
    """MarketStream.epoch restarts at 1 in every process."""
    d = jrnl_mod.describe()
    assert d["reconstruction_key"] == "(boot_id, epoch) -- never epoch alone"
    assert "NEVER carry a ladder across a boot boundary" in \
        jrnl_mod.RECONSTRUCTION_RULE


def _rec(boot, at, kind, epoch=None, slug=None, **payload):
    return {"boot_id": boot, "at": at, "kind": kind, "epoch": epoch,
            "slug": slug, "payload": payload}


def test_two_boots_with_the_same_epoch_number_are_two_segments():
    recs = [_rec("b1", 100.0, jrnl_mod.R_LADDER, 1, "m"),
            _rec("b1", 150.0, jrnl_mod.R_EPOCH, 1),
            _rec("b2", 200.0, jrnl_mod.R_GAP, None,
                 event=jrnl_mod.BOOT_GAP, why=jrnl_mod.PROCESS_REPLACED,
                 **{"from": 150.0, "to": 200.0}),
            _rec("b2", 201.0, jrnl_mod.R_LADDER, 1, "m")]
    segs = jrnl_mod.segments(recs)
    assert len(segs) == 2
    assert {s["boot_id"] for s in segs} == {"b1", "b2"}
    assert {s["epoch"] for s in segs} == {1}      # the SAME bare epoch


def test_an_instant_between_two_boots_is_unobserved():
    recs = [_rec("b1", 100.0, jrnl_mod.R_LADDER, 1, "m"),
            _rec("b1", 150.0, jrnl_mod.R_EPOCH, 1),
            _rec("b2", 200.0, jrnl_mod.R_GAP, None,
                 event=jrnl_mod.BOOT_GAP, why=jrnl_mod.PROCESS_REPLACED,
                 **{"from": 150.0, "to": 200.0}),
            _rec("b2", 201.0, jrnl_mod.R_LADDER, 1, "m")]
    v = jrnl_mod.covers(recs, 175.0)
    assert v["observed"] is False
    assert v["why"] == jrnl_mod.PROCESS_REPLACED


def test_a_quiet_stretch_inside_an_epoch_is_still_observed():
    """The same error as the sampling side, in the reconstruction.

    A segment that ended at its LAST LADDER would mark every quiet
    stretch before a disconnect as unobserved -- and quiet stretches
    are what a qualifying-uptime measurement is made of.
    """
    recs = [_rec("b1", 100.0, jrnl_mod.R_LADDER, 1, "m"),
            _rec("b1", 150.0, jrnl_mod.R_EPOCH, 1)]
    v = jrnl_mod.covers(recs, 140.0)          # 40s after the last ladder
    assert v["observed"] is True
    assert v["segment"]["boot_id"] == "b1"
    assert "NOT the last ladder" in v["segment"]["end_basis"]


def test_an_instant_before_any_ladder_is_unobserved():
    recs = [_rec("b1", 100.0, jrnl_mod.R_LADDER, 1, "m")]
    assert jrnl_mod.covers(recs, 50.0)["observed"] is False


@needs_pg
def test_the_journal_survives_process_replacement_and_records_the_gap():
    """Two boots of one run against a real server."""
    import asyncpg

    async def go():
        pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=3)
        run_id = "unit:%s" % os.getpid()
        try:
            j1 = jrnl_mod.PgJournal(pool, run_id=run_id, boot_id="b1")
            assert (await j1.open())["ok"]
            j1.run_open(et_date="2026-09-23")
            j1.ladder({"slug": "m", "epoch": 1,
                       "ladder_class": "INITIAL_LADDER", "ladder_seq": 1,
                       "received_at": time.time(), "source_ts": "t",
                       "venue_state": "MARKET_STATE_OPEN",
                       "replacement": "x"},
                      {"bids": [{"px": "0.5", "qty": "1"}], "offers": []})
            await j1.flush(force=True)
            await asyncio.sleep(0.4)

            j2 = jrnl_mod.PgJournal(pool, run_id=run_id, boot_id="b2")
            o2 = await j2.open()
            await j2.flush(force=True)
            recs = await jrnl_mod.read_run(pool, run_id)
            await pool.execute("DELETE FROM " + jrnl_mod.TABLE +
                               " WHERE run_id=$1", run_id)
            return o2, recs
        finally:
            await pool.close()

    o2, recs = asyncio.new_event_loop().run_until_complete(go())
    assert (o2.get("boot_gap") or {}).get("why") == jrnl_mod.PROCESS_REPLACED
    assert {r["boot_id"] for r in recs} == {"b1", "b2"}
    boot_gaps = [r for r in recs if r["kind"] == jrnl_mod.R_GAP
                 and r["payload"].get("event") == jrnl_mod.BOOT_GAP]
    assert len(boot_gaps) == 1
    assert boot_gaps[0]["payload"]["duration_s"] >= 0.3


def test_a_gap_is_never_extrapolated_into_a_full_day():
    v = feed_mod.coverage_verdict(instants_expected=86400,
                                  instants_valid=43200)
    assert v["observed_fraction"] == 0.5
    assert v["usable_as_research_unit"] is False
    assert "NOT scaled to the full window" in v["reward_scope"]
    assert "43200 OBSERVED" in v["reward_scope"]


def test_coverage_met_is_still_scoped_to_the_observed_portion():
    v = feed_mod.coverage_verdict(instants_expected=86400,
                                  instants_valid=85000)
    assert v["usable_as_research_unit"] is True
    assert "OBSERVED" in v["reward_scope"]


def test_research_coverage_is_not_a_trading_verdict():
    v = feed_mod.coverage_verdict(instants_expected=10, instants_valid=1)
    assert "independent of trading eligibility" in v["not_a_trading_verdict"]
    assert "trading_eligible" not in v
    assert "eligible" not in v


def test_a_boot_fraction_is_not_reported_as_the_dates_fraction():
    s = _stream_with_book()
    h = feed_mod.FeedHealth(s)
    h.on_book("m00", s._books["m00"])
    c = obs._coverage(["m00", "m01"], h, man.et_window("2026-09-23"),
                      time.time() - 60.0, time.time())
    assert "THIS BOOT ONLY" in c["scope"]
    assert c["markets_with_no_frame"] == ["m01"]


# ═══ 5. THE ENTRY POINT ══════════════════════════════════════════════

def test_the_mode_is_off_when_the_manifest_variable_is_unset(monkeypatch):
    """A deployment that changes nothing until it is configured."""
    from sportsassets.workers import bettor_live_loop as bll
    monkeypatch.delenv(bll._INCENTIVE_ENV, raising=False)
    assert bll._incentive_mode() is False
    monkeypatch.setenv(bll._INCENTIVE_ENV, "/x/manifest.json")
    assert bll._incentive_mode() is True
    monkeypatch.setenv(bll._INCENTIVE_ENV, "   ")
    assert bll._incentive_mode() is False


def test_the_observe_module_imports_no_order_path():
    """Asserted from the IMPORT LIST, not from a promise in a docstring."""
    src = open(os.path.join(PKG, "workers", "bettor_incentive_observe.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                mods.add(a.name)
            mods.add(n.module or "")
        elif isinstance(n, ast.Import):
            for a in n.names:
                mods.add(a.name)
    forbidden = {"pmus", "bettor_orders", "bettor_shadow_loop",
                 "bettor_decision_engine", "bettor_live_store",
                 "bettor_universe", "bettor_universe_probe"}
    assert not (mods & forbidden), sorted(mods & forbidden)
    assert obs.describe()["submits_orders"] is False


def test_nothing_in_the_release_writes_the_observation_control_true():
    """Arming stays a human action through `render-ops sql obs-run`."""
    for name in RELEASE_MODULES:
        code = _code_only(name)
        # No literal that could arm it, and no code path that names the
        # control row as a write target. The runner reaches the control
        # only through `read_control`, and `disarm` is never called.
        assert "'true'::jsonb" not in code, name
        assert "bettor_live_observation" not in code, name
        assert "disarm" not in code, name
    # The run row no longer holds the HTTP allowance at all.
    assert "http_spent" not in _code_only("bettor_incentive_state.py")
    state = _code_only("bettor_incentive_state.py")
    # READING the table is ordinary -- the budget module reads the
    # allowance row to report the authoritative count. WRITING it is
    # not: exactly one module inserts, exactly once, into its OWN key.
    assert state.count("INSERT INTO ingestion_state") == 1
    assert state.count("FROM ingestion_state") == 1
    assert "RUN_KEY" in state
    for other in set(RELEASE_MODULES) - {"bettor_incentive_state.py"}:
        code = _code_only(other)
        assert "INSERT INTO ingestion_state" not in code, other
        assert "UPDATE ingestion_state" not in code, other


def test_a_closed_control_starts_nothing_at_all(monkeypatch):
    """Read FIRST: before the manifest, before credentials, before a
    socket. A stopped run must cost nothing."""
    from sportsassets import bettor_live_control as ctl
    touched = {"manifest": False}

    async def _closed(_pool=None):
        return {"run": False, "why": ctl.W_STOPPED, "detail": "test"}

    def _load(*a, **k):
        touched["manifest"] = True
        return {"ok": False, "why": man.M_ABSENT}

    monkeypatch.setattr(obs.ctl, "read_control", _closed)
    monkeypatch.setattr(obs.man, "load", _load)
    out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        obs.run(control_pool=object()))
    assert out["started"] is False
    assert out["why"] == ctl.W_STOPPED
    assert touched["manifest"] is False


def test_the_kill_switch_still_answers_first(monkeypatch):
    monkeypatch.setenv(obs.KILL_ENV, "off")
    out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        obs.run())
    assert out == {"started": False, "why": "KILL_SWITCH",
                   "observe": obs.OBSERVE_VERSION}


def test_effective_config_is_readable_with_nothing_configured():
    """Logged even when stopped: setting a variable does not prove the
    running process received it."""
    c = obs.effective_config()
    assert c["control_key"] == "bettor_live_observation"
    assert c["max_contracts"].startswith("N/A")


# ═══ 6. THE THREE PRE-DEPLOYMENT PROPERTIES ══════════════════════════
#
# Full end-to-end evidence lives in
# `research/beta48/incentive_durability_proof.py`, which runs all
# sixteen against a real server. These are the three that must not be
# allowed to regress silently in CI.

_ARM = {"max_distinct": 0, "max_bbo_attempts": 0, "max_listing_attempts": 0,
        "distinct_reserved": 0, "bbo_attempts_reserved": 0,
        "listing_attempts_reserved": 0,
        "max_incentive_manifest": 4, "max_incentive_recheck": 2,
        "max_incentive_retry": 2, "incentive_manifest_reserved": 0,
        "incentive_recheck_reserved": 0, "incentive_retry_reserved": 0,
        "slugs": []}


async def _arm_row(pool, probe="t-probe", control=True, general=0):
    now = datetime.now(timezone.utc)
    row = dict(_ARM, probe_id=probe, started_at=now.isoformat(),
               deadline_at=(now + timedelta(hours=2)).isoformat(),
               max_distinct=general, max_bbo_attempts=general,
               max_listing_attempts=general)
    await pool.execute(
        "CREATE TABLE IF NOT EXISTS ingestion_state "
        "(key text PRIMARY KEY, value jsonb NOT NULL)")
    for k, v in ((ctl.BUDGET_KEY, row), (ctl.CONTROL_KEY, bool(control))):
        await pool.execute(
            "INSERT INTO ingestion_state (key,value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            k, json.dumps(v))


@needs_pg
def test_two_overlapping_workers_share_one_eight_request_ceiling():
    """The failure an in-process counter cannot prevent.

    Two ledgers on SEPARATE POOLS -- separate connections, as two
    processes would have -- each try the whole allowance concurrently.
    """
    import asyncpg

    async def go():
        admin = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
        a = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=3)
        b = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=3)
        try:
            await _arm_row(admin)
            kinds = ([bud.K_MANIFEST] * 4 + [bud.K_RECHECK] * 2
                     + [bud.K_RETRY] * 2)

            async def burst(pool):
                L = bud.DurableLedger(pool, probe_id="t-probe")
                return [await L.spend(k) for k in kinds]

            ga, gb = await asyncio.gather(burst(a), burst(b))
            total = (await bud.DurableLedger(
                admin, probe_id="t-probe").read())["total_reserved"]
            await admin.execute("DELETE FROM ingestion_state")
            return ga, gb, total
        finally:
            for p in (admin, a, b):
                await p.close()

    ga, gb, total = asyncio.new_event_loop().run_until_complete(go())
    assert len(ga + gb) == 16                      # 16 attempted
    assert sum(1 for r in ga + gb if r["ok"]) == 8  # 8 granted, in total
    assert total == 8                               # and the row says so


@needs_pg
def test_a_reservation_is_committed_before_the_caller_may_dispatch():
    import asyncpg

    async def go():
        pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=3)
        try:
            await _arm_row(pool)
            L = bud.DurableLedger(pool, probe_id="t-probe")
            r = await L.spend(bud.K_MANIFEST)
            # Read on a DIFFERENT ledger object: only a committed
            # increment is visible here, and this happens before any
            # dispatch would.
            seen = (await bud.DurableLedger(
                pool, probe_id="t-probe").read())["total_reserved"]
            await pool.execute("DELETE FROM ingestion_state")
            return r, seen
        finally:
            await pool.close()

    r, seen = asyncio.new_event_loop().run_until_complete(go())
    assert r["ok"] is True and r["durable"] is True
    assert seen == 1


@needs_pg
def test_removing_the_mode_cannot_start_general_discovery():
    """Configuration removal is not a stop -- so the arm makes it safe.

    `obs-arm-incentive` zeroes the general acquisition caps. If the
    manifest variable were removed while the control was still true,
    `main()` would return to the general loop, read BUDGET_EXHAUSTED
    before constructing a client, and issue nothing.
    """
    import asyncpg

    async def go():
        pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
        try:
            await _arm_row(pool, control=True, general=0)
            gen = await ctl.read_budget(pool)
            inc = await ctl.read_incentive_allowance(pool)
            listing = await ctl.reserve(pool, ctl.R_LISTING,
                                        probe_id="t-probe")
            bbo = await ctl.reserve(pool, ctl.R_ATTEMPT, probe_id="t-probe")
            ctrl = await ctl.read_control(pool)
            await pool.execute("DELETE FROM ingestion_state")
            return gen, inc, listing, bbo, ctrl
        finally:
            await pool.close()

    gen, inc, listing, bbo, ctrl = \
        asyncio.new_event_loop().run_until_complete(go())
    # The control is STILL TRUE -- removing configuration did not stop it.
    assert ctrl["run"] is True
    # ...but the general loop can acquire nothing.
    assert gen["state"] == ctl.B_EXHAUSTED and gen["open"] is False
    assert not ctl.granted(listing) and not ctl.granted(bbo)
    # ...while the incentive allowance reads OPEN, which is why the two
    # questions needed two functions.
    assert inc["state"] == ctl.B_OPEN and inc["open"] is True
    assert inc["general_caps_zeroed"] is True


@needs_pg
def test_a_stop_refuses_the_next_request_inside_the_same_transaction():
    import asyncpg

    async def go():
        pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
        try:
            await _arm_row(pool, control=False)
            L = bud.DurableLedger(pool, probe_id="t-probe")
            r = await L.spend(bud.K_MANIFEST)
            g = await ctl.reserve(pool, ctl.R_LISTING, probe_id="t-probe")
            await pool.execute("DELETE FROM ingestion_state")
            return r, g
        finally:
            await pool.close()

    r, g = asyncio.new_event_loop().run_until_complete(go())
    assert r["ok"] is False and r["verdict"] == ctl.V_STOPPED
    assert not ctl.granted(g) and g["why"] == ctl.V_STOPPED


def test_the_incentive_allowance_is_read_by_its_own_function():
    """`read_budget` gates on max_distinct, which this arm zeroes.

    Asking it whether the incentive run may proceed always answers no.
    The rehearsal found this; the direct-reserve proof did not, because
    it never went through the worker's gate.
    """
    src = _code_only("workers/bettor_incentive_observe.py")
    assert "read_incentive_allowance" in src
    assert "read_budget" not in src


def test_an_in_run_liveness_gap_is_visible_to_a_reconstruction():
    """A GAP_OPENED without `from`/`to` marks nothing.

    `covers()` reads those two fields. The closing record is the only
    one that knows both ends, so it carries them -- and it records why
    the gap EXISTED, not "ALIVE", which is merely how it ended.
    """
    recs = [_rec("b1", 100.0, jrnl_mod.R_LADDER, 1, "m"),
            _rec("b1", 110.0, jrnl_mod.R_GAP, None,
                 event="GAP_OPENED", why=feed_mod.G_DISCONNECTED),
            _rec("b1", 140.0, jrnl_mod.R_GAP, None,
                 event="GAP_CLOSED", why=feed_mod.G_DISCONNECTED,
                 duration_s=30.0, **{"from": 110.0, "to": 140.0}),
            _rec("b1", 141.0, jrnl_mod.R_LADDER, 1, "m")]
    v = jrnl_mod.covers(recs, 125.0)
    assert v["observed"] is False
    assert v["why"] == feed_mod.G_DISCONNECTED
    # ...and either side of it is still observed.
    assert jrnl_mod.covers(recs, 105.0)["observed"] is True
    assert jrnl_mod.covers(recs, 141.0)["observed"] is True


def test_the_worker_closes_an_open_gap_when_the_run_ends():
    src = _code_only("workers/bettor_incentive_observe.py")
    assert "closed_by" in src and "RUN_END" in src
