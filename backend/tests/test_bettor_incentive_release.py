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
from sportsassets.workers import bettor_incentive_observe as obs

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

def test_eight_is_the_total_across_every_kind():
    L = bud.RequestLedger()
    got = [L.spend(k)["ok"] for k in
           [bud.K_MANIFEST] * 4 + [bud.K_RECHECK] * 2 + [bud.K_RETRY] * 2]
    assert got == [True] * 8
    assert L.used() == 8 and L.remaining() == 0
    assert L.spend(bud.K_RETRY)["verdict"] == bud.R_TOTAL


def test_each_subcap_binds_before_the_total_does():
    L = bud.RequestLedger()
    for _ in range(4):
        assert L.spend(bud.K_MANIFEST)["ok"]
    r = L.spend(bud.K_MANIFEST)
    assert r["ok"] is False and r["verdict"] == bud.R_SUBCAP
    assert L.used() == 4                    # the total still has room


def test_listing_bbo_and_settlement_are_refused_by_name():
    """The general loop's acquisition spends ~1,686 requests.

    Not calling those paths is not enough -- they are refused at the
    reservation, so a code path that reached one fails loudly.
    """
    L = bud.RequestLedger()
    for kind in ("listing", "bbo_attempt", "distinct", "settlement"):
        r = L.spend(kind)
        assert r["ok"] is False and r["verdict"] == bud.R_FORBIDDEN, kind
    assert L.used() == 0


def test_the_environment_can_lower_the_cap_and_never_raise_it(monkeypatch):
    monkeypatch.setenv(bud.TOTAL_ENV, "99")
    assert bud.RequestLedger().total == 8
    monkeypatch.setenv(bud.TOTAL_ENV, "3")
    assert bud.RequestLedger().total == 3


def test_a_restart_resumes_the_spend_rather_than_regranting_it():
    """The supervisor restarts a returning loop forever."""
    first = bud.RequestLedger()
    for _ in range(3):
        first.spend(bud.K_MANIFEST)
    second = bud.RequestLedger()
    second.seed(first.spent)
    assert second.used() == 3 and second.remaining() == 5


def test_an_unreadable_prior_spend_seeds_the_ledger_full_not_empty():
    L = bud.RequestLedger()
    L.seed({"manifest": "?", "recheck": None, "retry": "x"})
    assert L.remaining() == 0               # fails closed


def test_reconnects_and_resubscribes_are_bounded_separately():
    b = bud.ReconnectBounds(max_reconnects=2, max_resubscribes=5)
    assert b.check(2)["ok"] is True
    assert b.check(3)["why"] == bud.RC_RECONNECTS
    b2 = bud.ReconnectBounds(max_reconnects=100, max_resubscribes=2)
    b2.note_resubscribe(3)
    assert b2.check(0)["why"] == bud.RC_RESUBSCRIBES


def test_capture_charges_every_page_and_charges_a_retry_to_retry():
    calls = {"n": 0}

    def get(_url, params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("first attempt fails")
        if calls["n"] == 2:
            return 200, {"programs": [], "nextPageToken": "t2"}
        return 200, {"programs": [], "nextPageToken": None}

    L = bud.RequestLedger()
    out = man.capture(get, L, et_date="2026-09-23")
    assert out["pages_read"] == 2
    assert L.spent[bud.K_MANIFEST] == 2     # one per page
    assert L.spent[bud.K_RETRY] == 1        # the failure, charged apart
    assert L.used() == 3


def test_capture_stops_when_the_budget_refuses_rather_than_dispatching():
    sent = {"n": 0}

    def get(_url, _params):
        sent["n"] += 1
        return 200, {"programs": [], "nextPageToken": "more"}

    L = bud.RequestLedger()
    man.capture(get, L, et_date="2026-09-23", max_pages=10)
    assert sent["n"] <= 4                   # the manifest sub-cap
    assert L.used() <= bud.TOTAL_CAP


def test_terms_arrive_with_discovery_so_no_per_market_call_is_budgeted():
    """The four-request arm phase only works because of this."""
    body = {"programs": [{
        "marketSlug": "m00", "eventStartTime": "2026-12-27T04:59:00.000Z",
        "instrumentState": "INSTRUMENT_STATE_OPEN",
        "timePeriods": [{"programId": "p1", "createdAt": "2026-09-21T00:00:00Z",
                         "period": man.PERIOD_DAILY, "rewardPool": 50,
                         "discountFactor": 0.25, "targetSize": 500,
                         "start": "s", "end": "e", "status": "active"}]}],
        "nextPageToken": None}
    L = bud.RequestLedger()
    out = man.capture(lambda u, p: (200, body), L, et_date="2026-09-23")
    p = out["programs"][0]
    assert L.used() == 1                    # ONE request
    assert (p["reward_pool"], p["discount_factor"], p["target_size"]) \
        == (50.0, 0.25, 500.0)


def test_capture_is_unauthenticated_and_says_so():
    out = man.capture(lambda u, p: (200, {"programs": []}), bud.RequestLedger(),
                      et_date="2026-09-23")
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

def test_the_first_frame_of_an_epoch_is_an_initial_ladder(tmp_path):
    s = ms.MarketStream("k", "s")
    s.connected, s.epoch = True, 7
    h = feed_mod.FeedHealth(s)
    j = jrnl_mod.Journal(str(tmp_path), run_id="r1", disk_declared=True)
    assert j.open()["ok"]
    for i in range(3):
        s._on_market_data({"marketData": {
            "marketSlug": "m00", "state": "MARKET_STATE_OPEN",
            "transactTime": "2026-09-23T12:00:0%d.123456Z" % i,
            "bids": [{"px": "0.50", "qty": "400"}],
            "offers": [{"px": "0.52", "qty": "300"}], "stats": {}}})
        c = h.on_book("m00", s._books["m00"])
        j.ladder(c, s._books["m00"]["book"])
    j.close()
    rows = [json.loads(x) for x in open(j.path, encoding="utf-8")]
    ladders = [r for r in rows if r["kind"] == jrnl_mod.R_LADDER]
    assert [r["ladder_class"] for r in ladders] == [
        feed_mod.INITIAL_LADDER, feed_mod.UPDATE, feed_mod.UPDATE]
    assert [r["ladder_seq"] for r in ladders] == [1, 2, 3]
    # FULL LADDER, BOTH SIDES, AND THE VENUE CLOCK AT SOURCE PRECISION.
    assert ladders[0]["bids"] and ladders[0]["offers"]
    assert ladders[0]["source_ts"].endswith(".123456Z")
    assert all(r["epoch"] == 7 for r in ladders)


def test_epochs_gaps_and_programme_versions_are_all_recorded(tmp_path):
    j = jrnl_mod.Journal(str(tmp_path), run_id="r2", disk_declared=True)
    j.open()
    j.run_open(et_date="2026-09-23")
    j.program_version(phase="ARM", programs={"m00": _program("m00")})
    j.epoch(epoch=1, event="EPOCH_OPENED")
    j.gap(event="GAP_OPENED", why=feed_mod.G_DISCONNECTED)
    j.gap(event="GAP_CLOSED", why="ALIVE", duration_s=12.5)
    j.program_version(phase="RECHECK", programs={})
    j.run_close(end_why=obs.END_WINDOW)
    j.close()
    kinds = [json.loads(x)["kind"] for x in open(j.path, encoding="utf-8")]
    for k in (jrnl_mod.R_RUN_OPEN, jrnl_mod.R_PROGRAM, jrnl_mod.R_EPOCH,
              jrnl_mod.R_GAP, jrnl_mod.R_RUN_CLOSE):
        assert k in kinds, k
    assert kinds.count(jrnl_mod.R_PROGRAM) == 2   # ARM and RECHECK


def test_the_reconstruction_rule_travels_with_the_data(tmp_path):
    j = jrnl_mod.Journal(str(tmp_path), run_id="r3", disk_declared=True)
    j.open()
    j.run_open()
    j.close()
    first = json.loads(open(j.path, encoding="utf-8").readline())
    assert "same EPOCH" in first["reconstruction_rule"]
    assert "Never carry a ladder across an epoch boundary" in \
        first["reconstruction_rule"]


def test_an_ephemeral_journal_directory_is_declared_not_assumed(tmp_path):
    j = jrnl_mod.Journal(str(tmp_path), run_id="r4", disk_declared=False)
    assert j.open()["durable_across_redeploy"] is False


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
    state = _code_only("bettor_incentive_state.py")
    # The one module here that touches `ingestion_state` issues exactly
    # two statements, and both are keyed by its OWN row.
    assert state.count("INSERT INTO ingestion_state") == 1
    assert state.count("FROM ingestion_state") == 1
    assert "RUN_KEY" in state
    for other in set(RELEASE_MODULES) - {"bettor_incentive_state.py"}:
        assert "ingestion_state" not in _code_only(other), other


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
