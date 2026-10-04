"""A PROBABILITY REQUEST DRIVES THE EXISTING HELD RE-EVALUATION PATH
(owner R30; agents.work_queue -> pinnapi_reactive.request_held_reevaluation
-> Scheduler.request_held).

The request queues the held event's CURRENT quote on the held queue exactly
as a price change would -- and only then: a held event, a live seed, a quote
the cache reads as usable now (fresh within its 30 s limit), never a version
already evaluated or already queued. Every refusal is named. Freshness stays
change-driven at the source: a request never makes a stale quote fresh.
"""
from __future__ import annotations

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_held as PH
from sportsassets import pinnapi_reactive as RX


def _quote(eid, *, at=1000.0, price=-150):
    return F.Quote(key=F.FULL_GAME_MONEYLINE_KEY, event_id=eid, sport_id=6,
                   stream="live", period=0, market_type="moneyline",
                   side=None, line=None, prices={"home": price, "away": 135},
                   epoch=1, source_change_ms=at * 1000, frame_ts_ms=at * 1000,
                   received_ms=at * 1000 + 20)


class _Cache:
    def __init__(self):
        self.quotes = {}
        self.reason = None

    def read(self, eid, key, evaluated_ms=None, max_age_s=30.0):
        q = self.quotes.get(eid)
        if q is None:
            return {"ok": False, "reason": "UNKNOWN_MARKET"}
        if self.reason:
            return {"ok": False, "reason": self.reason, "quote": q}
        return {"ok": True, "quote": q}


def _sched(now):
    w = PH.HeldWatch()
    w.set_targets({"held-slug": (7, None), "unseeded": (8, None),
                   "out": (None, "OUT_OF_FEED_SCOPE_SPORT")})

    async def ev(job):
        return {}

    async def audit(a):
        return None
    cache = _Cache()
    s = RX.Scheduler(cache, ev, audit, clock=lambda: now[0], held=w)
    s.seeds[7] = {"event": {}, "registered_at": now[0]}
    s.seeds[1] = {"event": {}, "registered_at": now[0]}
    return s, cache, w


def test_a_request_queues_a_fresh_unevaluated_held_quote_once():
    now = [1000.0]
    s, cache, _ = _sched(now)
    cache.quotes[7] = _quote(7, at=995.0)
    assert s.request_held(7) == "QUEUED"
    assert list(s.held_pending) == [7]
    assert s.held_pending[7]["requested"] is True
    assert s.counts["HELD_REQUESTED"] == 1
    assert s.request_held(7) == "ALREADY_QUEUED"
    eid, tick = s.next_job()                     # held first, as a change
    assert eid == 7 and tick["held"] is True
    # the same version again is never re-evaluated
    assert s.request_held(7) == "ALREADY_EVALUATED_THIS_VERSION"
    # a change of the price (a new version) can be requested again
    cache.quotes[7] = _quote(7, at=999.0, price=-160)
    assert s.request_held(7) == "QUEUED"


def test_every_refusal_is_named():
    now = [1000.0]
    s, cache, _ = _sched(now)
    assert s.request_held(1) == "NOT_A_HELD_EVENT"    # discovery only
    assert s.request_held(8) == "HELD_NO_DISCOVERY_SEED"
    assert s.request_held(7) == "UNKNOWN_MARKET"       # nothing cached
    cache.quotes[7] = _quote(7, at=900.0)
    cache.reason = "STALE"                             # older than 30 s
    assert s.request_held(7) == "STALE" and not s.held_pending
    cache.reason = None
    now[0] += RX.HELD_SEED_TTL_S + 1
    assert s.request_held(7) == "DISCOVERY_EXPIRED"
    s.closed = True
    assert s.request_held(7) == "SCHEDULER_CLOSED"


def test_the_module_request_resolves_the_slug_and_names_the_gaps(
        monkeypatch):
    now = [1000.0]
    s, cache, w = _sched(now)
    monkeypatch.setattr(PH, "WATCH", w)
    monkeypatch.setattr(RX, "ACTIVE", None)
    got = RX.request_held_reevaluation("held-slug")
    assert got == {"queued": False, "event_id": 7,
                   "reason": "REACTIVE_SCHEDULER_NOT_RUNNING"}
    assert RX.request_held_reevaluation("out")["reason"] == \
        "OUT_OF_FEED_SCOPE_SPORT"
    assert RX.request_held_reevaluation("never")["reason"] == \
        "NOT_A_HELD_TARGET_IN_THIS_PROCESS"
    monkeypatch.setattr(RX, "ACTIVE", s)
    cache.quotes[7] = _quote(7, at=995.0)
    assert RX.request_held_reevaluation("held-slug") == {
        "queued": True, "event_id": 7, "reason": "QUEUED"}
