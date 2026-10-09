"""CAPITAL-CRITICAL: A HELD MARKET THAT MOVES WHILE A PAPER PASS RUNS IS
REVIEWED BY THAT PASS, NOT AFTER IT (RC6.2 p-xavier SW-2).

Production (research-sql 37945613145 / 37945859108, 2026-10-09): the paper
SERVICING pass held the paper lock 27.7 s and 36.4 s with nothing held, its
budget exhausted; 557 of 6,255 PAPER MARKET_EVENT assessments in 72 h were
made 30 s or more after their PinnAPI change. A change notified while a pass
ran was refused busy by the held review, retried every 5 s, and dropped once
30 s had passed -- the pass's own Xavier step had built its due list before
the change. Repaired and proven here:

  * the pass services the pending held slugs BETWEEN ITS STEPS, under the
    locks it already holds, through the same Xavier step a held review runs:
    a change notified in one step is reviewed (MARKET_EVENT) before the next;
  * the 30 s rule is unchanged: a review in the pass of a provider change
    older than 30 s still reads STALE (WAITING_FOR_FRESH_EVIDENCE), however
    recently WE were notified; a notification past its retry window is
    dropped and counted, never reviewed as if current;
  * a slug no group of the pass's account holds goes back to the scheduler;
  * the scheduler's counters and its newest outcomes reach the persisted
    PinnAPI feed heartbeat (they were in memory only);
  * every pass records each step's wall time, and the pass heartbeat ring
    keeps the NEWEST beats in order (it kept the wrong end: 19 beats of
    2026-10-01 beside only the latest one).

SYNTHETIC data in a scratch test database; the feed read is the real
FeedCache's answer; no network, no venue, no order authority.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from sportsassets import bettor_paper_session as S
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as FR
from sportsassets import pinnapi_held as PH
from sportsassets import xavier_freshness as XFT
from sportsassets import xavier_packet as XPK
from sportsassets.agents import paper_runtime as PRT
from sportsassets.agents import paper_xavier as PX
from sportsassets.workers import ext_pinnacle_loop as LOOP

from tests import paper_harness as H
from tests import test_xavier_packet_fresh_inputs as XFI
from tests import test_xavier_review_probability_freshness as XRF

#: the strict management rail runs its production functions here
MANAGEMENT_RAIL_ENFORCED = True

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XRF.AT


@pytest.fixture
def held_state(monkeypatch):
    saved = {k: (set(v) if isinstance(v, set) else
                 dict(v) if isinstance(v, dict) else
                 list(v) if isinstance(v, list) else v)
             for k, v in PRT._HELD.items()}
    PRT._HELD.update(task=None, pending=set(), queued_at={},
                     busy_retries=0, busy_dropped=0, serviced_in_pass=0,
                     in_pass_slugs=0, in_pass_dropped=0, latency=[])
    PRT._HELD.pop("clock", None)
    monkeypatch.setattr(PRT, "HELD_REVIEW_MIN_GAP_S", 0.0)
    yield PRT._HELD
    PRT._HELD.clear()
    PRT._HELD.update(saved)


class _NoPool:
    """The background scheduler's pool: the pass takes the slugs first, so
    the scheduler must never need it here (a call is recorded)."""

    def __init__(self):
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        raise AssertionError("the background held review ran")


async def _settle_scheduler(state):
    t = state.get("task")
    if t is not None:
        await asyncio.wait_for(t, 5)


def test_the_pins():
    # the retry window IS the probability rule; the in-pass work is bounded
    assert PRT.HELD_REVIEW_RETRY_WINDOW_S == float(LOOP.PINNACLE_MAX_AGE_S)
    assert LOOP.PINNACLE_MAX_AGE_S == 30.0
    assert 0 < PRT.HELD_IN_PASS_MAX_S <= 30.0
    assert PRT.HELD_REVIEW_BUDGET_S == 10.0


async def _held_with_review(conn, tag, *, last_review_at):
    """A held, protected paper position whose newest Xavier review was at
    `last_review_at` (a backstop review)."""
    a, g, slug = await XRF._held(conn, tag, entry_age_s=3600)
    await PX.review_group(conn, XRF._ctx(a, last_review_at), g,
                          trigger=PX.T_BACKSTOP)
    return a, g, slug


async def _pass(conn, a, steps):
    return await PRT.paper_pass(conn, now=AT, account_id=a["account_id"],
                                config=a["config"], force=True,
                                fee_fn=H.zero_fee, steps=steps)


async def _market_reviews(conn, g):
    return [dict(r) for r in await conn.fetch(
        "SELECT reviewed_at, measure, selection, recommendation, refusal "
        "  FROM paper_xavier_reviews WHERE group_id=$1 AND trigger=$2 "
        " ORDER BY reviewed_at", g, PX.T_MARKET)]


@pg
async def test_a_held_change_notified_during_a_pass_is_reviewed_before_its_next_step(
        monkeypatch, held_state):
    """THE REGRESSION. On b3f1b0cd nothing reviews the change while the pass
    holds the lock: the probe step sees no MARKET_EVENT review."""
    conn = await H.connect()
    slugs = []
    pool = _NoPool()
    try:
        a, g, slug = await _held_with_review(conn, "pxin1",
                                             last_review_at=AT - 10)
        slugs.append(slug)
        # the provider moved the held market at AT - 4 (its own stamp); the
        # held read at AT is fresh (4 s, inside the unchanged 30 s rule)
        XFI._install_feed(monkeypatch, XFI._held_answer(XFI._cache_read(
            change_at=AT - 4, evaluated_at=AT, stamped=True)))
        change = {}
        monkeypatch.setattr(PH, "fresh_at", lambda s: change.get(s))
        seen = {}

        async def notify(conn_, ctx):
            # the feed's change notification (pinnapi_held.
            # paper_review_listener) arrives WHILE this pass holds the lock
            change[slug] = AT - 4
            return PRT.schedule_held_review([slug], get_pool=pool,
                                            clock=lambda: AT - 3.9)

        async def probe(conn_, ctx):
            seen["reviews"] = await _market_reviews(conn_, g)
            return {}

        out = await _pass(conn, a, [("notify", notify), ("probe", probe)])
        assert out["ran"] and not out["errors"], out["errors"]
        # reviewed INSIDE the pass, before its next step
        assert len(seen["reviews"]) == 1, seen
        m = json.loads(seen["reviews"][0]["measure"])
        assert m["evidence_state"] == PX.E_FRESH, m
        assert 0 <= m["probability_age_s"] <= 30.0
        rec = out["held_in_pass"]
        assert rec["slugs"] == 1 and rec["groups"] == 1
        assert rec["reviews"] >= 1 and rec["dropped"] == 0
        assert rec["elapsed_s"] <= PRT.HELD_IN_PASS_MAX_S
        st = PRT.held_review_status()
        assert st["serviced_in_pass"] == 1 and st["in_pass_slugs"] == 1
        assert st["recent_outcomes"]["by_via"][PRT.V_IN_PASS] == 1
        assert st["pending"] == 0
        await _settle_scheduler(held_state)
        assert pool.calls == 0          # never reviewed twice
        assert held_state["queued_at"] == {}
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_provider_change_older_than_30s_reviewed_in_the_pass_still_reads_stale(
        monkeypatch, held_state):
    """THE 30 s RULE IS UNCHANGED: WE were notified 1 s ago, but the
    provider's own stamp is 35 s old -- the review is STALE, waits for
    fresh evidence, and ranks no sale. Our notification clock never makes a
    probability fresh."""
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held_with_review(conn, "pxin2",
                                             last_review_at=AT - 40)
        slugs.append(slug)
        got = XFI._cache_read(change_at=AT - 35, evaluated_at=AT,
                              stamped=True)
        assert got["reason"] == F.R_STALE
        XFI._install_feed(monkeypatch, XFI._held_answer(got))
        change = {}
        monkeypatch.setattr(PH, "fresh_at", lambda s: change.get(s))
        seen = {}

        async def notify(conn_, ctx):
            change[slug] = AT - 35
            return PRT.schedule_held_review([slug], get_pool=_NoPool(),
                                            clock=lambda: AT - 1)

        async def probe(conn_, ctx):
            seen["reviews"] = await _market_reviews(conn_, g)
            return {}

        out = await _pass(conn, a, [("notify", notify), ("probe", probe)])
        assert out["ran"] and not out["errors"], out["errors"]
        assert len(seen["reviews"]) == 1
        rv = seen["reviews"][0]
        m = json.loads(rv["measure"])
        assert m["evidence_state"] == PX.E_STALE, m
        assert m["feed_refusal"] == F.R_STALE
        missing = json.loads(rv["selection"])["management_packet"]["gate"][
            "missing"]
        assert XPK.P_PROBABILITY in missing
        assert rv["recommendation"] == XFT.REC_WAITING
        sales = await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role "
            " IN ('EXIT', 'REDUCE')", g)
        assert sales == 0
        await _settle_scheduler(held_state)
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_notification_past_its_window_is_dropped_and_counted_not_reviewed(
        monkeypatch, held_state):
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held_with_review(conn, "pxin3",
                                             last_review_at=AT - 60)
        slugs.append(slug)
        change = {}
        monkeypatch.setattr(PH, "fresh_at", lambda s: change.get(s))
        clock = {"t": AT - 31}
        seen = {}

        async def notify(conn_, ctx):
            change[slug] = AT - 31
            got = PRT.schedule_held_review([slug], get_pool=_NoPool(),
                                           clock=lambda: clock["t"])
            clock["t"] = AT           # 31 s later: past the 30 s window
            return got

        async def probe(conn_, ctx):
            seen["reviews"] = await _market_reviews(conn_, g)
            return {}

        out = await _pass(conn, a, [("notify", notify), ("probe", probe)])
        assert out["ran"] and not out["errors"], out["errors"]
        assert seen["reviews"] == []
        assert out["held_in_pass"]["dropped"] == 1
        assert out["held_in_pass"]["reviews"] == 0
        st = PRT.held_review_status()
        assert st["in_pass_dropped"] == 1
        ring = held_state["latency"]
        assert ring[-1]["via"] == PRT.V_DROPPED and ring[-1]["slug"] == slug
        assert ring[-1]["latency_s"] == pytest.approx(31.0)
        assert slug not in held_state["queued_at"]
        await _settle_scheduler(held_state)
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_slug_this_account_does_not_hold_goes_back_to_the_scheduler(
        monkeypatch, held_state):
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "pxin4", now=AT - 100)
        seen = {}

        async def notify(conn_, ctx):
            PRT.schedule_held_review(["syn-pxin-not-held"],
                                     get_pool=_NoPool(),
                                     clock=lambda: AT)
            return {}

        async def probe(conn_, ctx):
            seen["pending"] = set(held_state["pending"])
            return {}

        out = await _pass(conn, a, [("notify", notify), ("probe", probe)])
        assert out["ran"] and not out["errors"], out["errors"]
        # returned to the scheduler after the first checkpoint, untouched
        assert seen["pending"] == {"syn-pxin-not-held"}
        rec = out["held_in_pass"]
        assert rec["left_for_scheduler"] >= 1 and rec["reviews"] == 0
        assert PRT.held_review_status()["serviced_in_pass"] == 0
        await _settle_scheduler(held_state)
    finally:
        await conn.close()


@pg
async def test_a_pass_with_nothing_pending_does_no_held_work(held_state):
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "pxin5", now=AT - 100)

        async def one(conn_, ctx):
            return {"ok": True}
        out = await _pass(conn, a, [("one", one)])
        assert out["ran"] and "held_in_pass" not in out
    finally:
        await conn.close()


async def test_a_failing_in_pass_review_hands_the_slugs_back(
        monkeypatch, held_state):
    """Nothing is assumed reviewed: a review that raises inside the pass
    leaves the slugs with the scheduler (still inside their window)."""
    class _Conn:
        async def fetch(self, sql, *args):
            return [{"group_id": "g1", "us_market_slug": "s1"}]

    async def boom(conn, ctx, *, only_groups=None):
        raise TypeError("review failed")
    monkeypatch.setattr(PX, "step", boom)
    held_state["pending"].add("s1")
    held_state["queued_at"]["s1"] = 100.0
    held_state["clock"] = lambda: 105.0
    ctx = {"account_id": "acct", "session": {}, "session_id": "sess",
           "config": {}, "clock": lambda: 105.0}
    got = await PRT.service_held_in_pass(_Conn(), ctx)
    assert "TypeError" in got["error"]
    assert held_state["pending"] == {"s1"}
    assert held_state["queued_at"]["s1"] == 100.0
    assert PRT.held_review_status()["serviced_in_pass"] == 0


async def test_the_in_pass_work_is_capped_per_pass(monkeypatch, held_state):
    calls = []

    async def service(conn, ctx):
        calls.append(1)
        return {"slugs": 1, "groups": 1, "reviews": 1}
    monkeypatch.setattr(PRT, "service_held_in_pass", service)
    held_state["pending"].add("s1")
    out = {"held_in_pass": {"checkpoints": 3, "slugs": 3, "groups": 3,
                            "reviews": 3, "dropped": 0,
                            "left_for_scheduler": 0,
                            "elapsed_s": PRT.HELD_IN_PASS_MAX_S,
                            "capped": False, "errors": []}}
    await PRT._held_checkpoint(None, {}, out)
    assert calls == [] and out["held_in_pass"]["capped"] is True


# ═════════════════════════════════════════════════════════════════════
# THE SCHEDULER'S RECORD REACHES THE PERSISTED HEARTBEAT
# ═════════════════════════════════════════════════════════════════════

def test_a_dropped_change_is_on_the_outcome_ring_and_counted(held_state):
    held_state["queued_at"].update({"a": 100.0, "b": 60.0})
    got = PRT.requeue_busy({"a", "b"}, now=125.0)
    assert got == {"requeued": ["a"], "dropped": ["b"]}
    st = PRT.held_review_status()
    assert st["busy_dropped"] == 1 and st["busy_retries"] == 1
    assert st["recent_outcomes"]["by_via"][PRT.V_DROPPED] == 1
    assert st["recent"][-1] == {"slug": "b", "via": PRT.V_DROPPED,
                                "queued_at": 60.0, "at": 125.0,
                                "latency_s": 65.0}


async def test_a_background_held_review_records_its_latency(
        monkeypatch, held_state):
    clock = {"t": 1000.0}

    async def held_review(conn, *, slugs, **kw):
        return {"ran": True, "groups": ["g1"]}

    class _Pool:
        def acquire(self, timeout=None):
            class _C:
                async def __aenter__(self_):
                    return object()

                async def __aexit__(self_, *a):
                    return False
            return _C()

    async def get_pool():
        return _Pool()
    monkeypatch.setattr(PRT, "held_review", held_review)
    PRT.schedule_held_review(["slug-l"], get_pool=get_pool,
                             clock=lambda: clock["t"])
    clock["t"] = 1002.5
    await asyncio.wait_for(held_state["task"], 5)
    ring = held_state["latency"]
    assert ring[-1]["via"] == PRT.V_HELD_REVIEW
    assert ring[-1]["latency_s"] == pytest.approx(2.5)
    st = PRT.held_review_status()
    assert st["recent_outcomes"]["reviewed_latency_max_s"] == \
        pytest.approx(2.5)
    assert st["recent_outcomes"]["reviewed_after_window"] == 0


def test_the_feed_heartbeat_carries_the_held_review_scheduler(
        monkeypatch, held_state):
    class _Owner:
        def status(self):
            return {"state": "OWNER_SYNCED"}
    monkeypatch.setitem(FR._STATE, "owner", _Owner())
    held_state["busy_dropped"] = 7
    held_state["serviced_in_pass"] = 3
    d = FR.digest()
    h = d["held_review_scheduler"]
    assert h["busy_dropped"] == 7 and h["serviced_in_pass"] == 3
    assert h["retry_window_s"] == 30.0
    # persisted as the writer persists it: valid JSON inside the cap
    body = FR._capped(dict(d, beat_at=1.0))
    assert json.loads(body)["held_review_scheduler"]["busy_dropped"] == 7


# ═════════════════════════════════════════════════════════════════════
# THE PASS RECORD: EACH STEP'S TIME, AND A RING THAT KEEPS THE NEWEST
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_each_steps_wall_time_is_on_the_pass_and_its_heartbeat(
        held_state):
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "pxin6", now=AT - 100)

        async def slow(conn_, ctx):
            await asyncio.sleep(0.05)
            return {}

        async def fast(conn_, ctx):
            return {}
        out = await _pass(conn, a, [("slow", slow), ("fast", fast)])
        assert set(out["step_elapsed_s"]) == {"slow", "fast"}
        assert out["step_elapsed_s"]["slow"] >= 0.05
        h = await S.health(conn, a["session_id"])
        assert set(h["last_pass"]["step_elapsed_s"]) == {"slow", "fast"}
        beat = h["recent_heartbeats"][-1]
        assert beat["slowest_step"] == "slow"
        assert beat["slowest_step_s"] >= 0.05
    finally:
        await conn.close()


@pg
async def test_the_pass_heartbeat_ring_keeps_the_newest_beats_in_order():
    """THE REGRESSION. On b3f1b0cd the ring dropped the previous newest beat
    on every pass and kept the oldest ones forever."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "pxring", now=AT - 1000)
        n = S.RECENT_HEARTBEATS_KEPT + 7
        for i in range(n):
            await S.record_pass(conn, a["session_id"],
                                result={"elapsed_s": 0.1}, now=AT + i)
        h = await S.health(conn, a["session_id"])
        ats = [b["at"] for b in h["recent_heartbeats"]]
        assert ats == [AT + i for i in range(n - S.RECENT_HEARTBEATS_KEPT,
                                             n)]
    finally:
        await conn.close()


@pg
async def test_the_scrambled_production_ring_heals_on_the_next_pass():
    """Production's ring as it stood (19 old beats and the newest one, in
    no order): the next pass keeps the newest RECENT_HEARTBEATS_KEPT by
    their own instant, oldest first, the new beat last."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "pxheal", now=AT - 1000)
        old = [{"at": AT - 700000 + 60 * i, "ok": True} for i in range(19)]
        ring = [{"at": AT - 30, "ok": True}] + old[::-1]
        await conn.execute(
            "UPDATE paper_session_health SET recent_heartbeats=$2::jsonb "
            " WHERE session_id=$1", a["session_id"], json.dumps(ring))
        await S.record_pass(conn, a["session_id"], result={}, now=AT)
        h = await S.health(conn, a["session_id"])
        ats = [b["at"] for b in h["recent_heartbeats"]]
        assert len(ats) == S.RECENT_HEARTBEATS_KEPT
        assert ats == sorted(ats) and ats[-1] == AT and ats[-2] == AT - 30
        assert AT - 700000 not in ats          # the oldest beat left first
    finally:
        await conn.close()
