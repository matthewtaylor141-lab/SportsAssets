"""Event-driven reactor: book pushes wake the loop, and a reactive pass
re-prices only what moved — with the same decision code and none of the
whole-cycle accounting that a partial view would corrupt."""

import time

import pytest

from edge.execution.engine import Policy
from edge.execution.risk import RiskManager
from edge.ledger.service import Ledger
from edge.shadow.reactor import Reactor
from edge.shadow.runner import run_cycle
from tests.test_run_cycle_e2e import StubFeed, StubVenue, _event

# Most tests here exercise loop and pricing MECHANICS, not trading policy.
# `blocked_categories` globally quarantines moneyline (measured -2.34c drift,
# retention 0.239 on our own fills), which would otherwise make every
# moneyline fixture untradeable and turn these into vacuous passes. The
# quarantine itself is pinned by its own tests in test_loop_health.py.
POLICY = Policy.load()
POLICY.leagues = {**POLICY.leagues, "blocked_categories": []}


def _rig(tmp_path):
    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    return ledger, RiskManager(ledger, {**POLICY.risk, "mode": "PAPER"})


# ── the queue itself ────────────────────────────────────────────────────

def test_take_returns_marked_slugs_and_clears():
    r = Reactor(debounce_s=0.0)
    r.mark("a")
    r.mark("b")
    assert r.take(timeout=1.0) == {"a", "b"}
    assert r.take(timeout=0.05) == set()   # drained; nothing new


def test_burst_on_one_slug_coalesces_into_one_pass():
    r = Reactor(debounce_s=0.05)
    for _ in range(20):
        r.mark("a")          # a single trade rewrites many levels
    assert r.take(timeout=1.0) == {"a"}
    assert r.stats()["ticks"] == 20
    assert r.stats()["reactions"] == 1


def test_take_times_out_without_activity_so_a_dead_stream_just_polls():
    r = Reactor(debounce_s=0.0)
    t0 = time.time()
    assert r.take(timeout=0.2) == set()
    assert 0.15 <= time.time() - t0 < 1.0


def test_overflow_is_deferred_not_dropped():
    """Back-pressure must never silently lose a book we haven't looked at."""
    r = Reactor(debounce_s=0.0, max_batch=2)
    for s in ("a", "b", "c", "d"):
        r.mark(s)
    first = r.take(timeout=1.0)
    assert len(first) == 2
    second = r.take(timeout=1.0)          # re-armed immediately
    assert len(second) == 2
    assert first | second == {"a", "b", "c", "d"}


def test_latency_is_measured_from_mark_to_take():
    r = Reactor(debounce_s=0.0)
    r.mark("a")
    time.sleep(0.05)
    r.take(timeout=1.0)
    assert r.stats()["avg_latency_ms"] >= 40
    r.reset_window()
    assert r.stats() == {"ticks": 0, "reactions": 0, "queued": 0, "deferred": 0,
                         "avg_latency_ms": 0.0, "max_latency_ms": 0.0}


# ── the streamer -> reactor wiring ──────────────────────────────────────

def test_stream_update_marks_the_slug():
    from edge.venues.pmus_stream import BookStreamer

    r = Reactor(debounce_s=0.0)
    s = BookStreamer("k", "s", autostart=False)
    s.add_listener(r.mark)
    s._on_market_data({"marketData": {"marketSlug": "m1", "offers": []}})
    assert r.take(timeout=1.0) == {"m1"}


def test_a_raising_listener_never_breaks_the_stream():
    from edge.venues.pmus_stream import BookStreamer

    s = BookStreamer("k", "s", autostart=False)
    s.add_listener(lambda slug: (_ for _ in ()).throw(RuntimeError("boom")))
    seen = []
    s.add_listener(seen.append)
    s._on_market_data({"marketData": {"marketSlug": "m1", "offers": []}})
    assert seen == ["m1"]                       # later listeners still run
    assert s.get("m1") is not None              # and the cache still updated


# ── reactive run_cycle ──────────────────────────────────────────────────

def test_reactive_pass_prices_only_the_moved_book(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    venue, feed = StubVenue(ask_price=0.47), StubFeed([_event()])

    # Only Chelsea's book moved: Arsenal is never priced this pass.
    funnel = run_cycle([venue], feed, POLICY, risk, ledger, ["soccer_epl"],
                       only_slugs={"T-CHE"})
    assert funnel["reactive"] == 1
    assert funnel["books_checked"] == 1


def test_a_reaction_trades_on_the_same_terms_as_a_sweep(tmp_path, monkeypatch):
    """The whole point: a mispricing found by push fills exactly as one found
    by poll — same filter, same caps, same ledger."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"], only_slugs={"T-ARS"})
    assert funnel["logged"] == 1
    assert ledger.summary()["fills"] == 1
    assert ledger.position("kalshi:T-ARS")["shares"] > 0


def test_reactive_pass_still_obeys_the_dead_zone(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.43)], StubFeed([_event(home_odds=1.90)]),
                       POLICY, risk, ledger, ["soccer_epl"], only_slugs={"T-ARS"})
    assert ledger.summary()["fills"] == 0
    assert funnel["rejects"].get("band", 0) >= 1


def test_reactive_pass_skips_whole_cycle_accounting(tmp_path, monkeypatch):
    """A partial view must not write match-rate stats, mark the book for the
    circuit breaker, or feed the watchdog — those describe a whole sweep."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"], only_slugs={"T-ARS"})
    assert "marked_delta" not in funnel and "halted" not in funnel
    assert ledger.match_rate_report(days=1) == []


def test_unknown_slug_reaction_is_a_cheap_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)

    class ExplodingVenue(StubVenue):
        def get_book(self, market_id, token):
            raise AssertionError("priced a book that did not move")

    funnel = run_cycle([ExplodingVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"],
                       only_slugs={"some-other-market"})
    assert funnel["books_checked"] == 0 and funnel["logged"] == 0


def test_fair_value_is_not_computed_for_untouched_events(tmp_path, monkeypatch):
    """The affordability guarantee: repricing must cost nothing for events
    whose books didn't move, or sub-second reaction isn't sustainable."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    calls = {"n": 0}
    import edge.fairvalue.devig as devig

    real = devig.fair_value
    monkeypatch.setattr(devig, "fair_value",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1),
                                         real(*a, **k))[1])
    run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]), POLICY, risk,
              ledger, ["soccer_epl"], only_slugs={"nothing-here"})
    assert calls["n"] == 0


def test_a_bad_odds_set_reports_fair_error_instead_of_vanishing(tmp_path, monkeypatch):
    """Deferring the de-vig must not lose its failure: the reject stays named."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    ev = _event()
    ev.h2h = {"Arsenal": 0.0, "Chelsea": 0.0}   # unusable prices
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([ev]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["rejects"].get("fair_error", 0) >= 1
    assert ledger.summary()["fills"] == 0


@pytest.mark.parametrize("reactive", [True, False])
def test_quarantine_ban_applies_on_both_paths(tmp_path, monkeypatch, reactive):
    """Sampling divergence is a sweep job; ENFORCING it is every pass's job."""
    import edge.shadow.runner as runner

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    monkeypatch.setattr(runner, "_quarantined",
                        lambda _l: {("kalshi", "epl", "moneyline")})
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"],
                       only_slugs={"T-ARS"} if reactive else None)
    assert funnel["rejects"].get("quarantined_slice", 0) >= 1
    assert ledger.summary()["fills"] == 0
