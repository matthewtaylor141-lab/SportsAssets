"""XAVIER OBTAINS A FRESH PROBABILITY FOR EACH HELD POSITION.

Production reviews stood on probabilities 106 s and 173 s old against the
30 s limit: freshness is CHANGE-driven (a quote is fresh for 30 s after the
provider changed it) while reviews were CLOCK-driven, held changes reached
only a discovery-gated FIFO scheduler, and the reader took the latest stored
row. Now:

  * held events are the PinnAPI path's priority targets: resolved from the
    open positions, served FIRST by the one reactive worker (never evicted by
    discovery, seed pinned while held) -- same worker, same deadline;
  * a held market's price change triggers Xavier's review at once (paper:
    a Xavier-only held review; actual: the next mirror tick, MARKET_EVENT),
    and the review's on-demand read of the in-process cache (<= 1 s) is used
    only when fresh (source change <= 30 s, same event / market / period /
    side identity);
  * a stale-only world stays STALE / UNAVAILABLE: no EXIT, REDUCE or
    REALLOCATE.

SYNTHETIC data; a real FeedCache behind a fake owner; no network, no order.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import execmirror as M
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as R
from sportsassets import pinnapi_held as PH
from sportsassets import pinnapi_reactive as RX
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import xavier_management as XM
from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_xavier_review_probability_freshness as XF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ML = F.FULL_GAME_MONEYLINE_KEY
EID = 9001
HOME, AWAY = "New York Yankees", "Tampa Bay Rays"
PRICES = [{"designation": "home", "price": -150},
          {"designation": "away", "price": 135}]
MOVED = [{"designation": "home", "price": -160},
         {"designation": "away", "price": 145}]


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _mk(prices):
    return {"key": ML, "type": "moneyline", "period": 0, "status": "open",
            "prices": prices}


def _feed(*, start, changed_at, sport=6):
    """A synced cache: the held event's moneyline last CHANGED at
    `changed_at` (a snapshot alone never stamps a change)."""
    c = F.FeedCache()
    ep = c.new_connection([("live", sport)])
    ev = {"id": EID, "startTime": _iso(start),
          "participants": [{"name": HOME, "alignment": "home"},
                           {"name": AWAY, "alignment": "away"}],
          "markets": [_mk(PRICES)]}
    c.apply({"type": "snapshot", "stream": "live", "sport_id": sport,
             "ts": (changed_at - 60) * 1000, "events": [ev]}, epoch=ep,
            received_ms=(changed_at - 60) * 1000)
    c.apply({"type": "live", "sport_id": sport, "op": "upd",
             "ts": changed_at * 1000,
             "rec": {"id": EID, "markets": [_mk(MOVED)]}}, epoch=ep,
            received_ms=changed_at * 1000 + 40)
    return c, ep


def _move(cache, ep, *, at, prices, sport=6):
    cache.apply({"type": "live", "sport_id": sport, "op": "upd",
                 "ts": at * 1000, "rec": {"id": EID,
                                          "markets": [_mk(prices)]}},
                epoch=ep, received_ms=at * 1000 + 30)


def _quote(eid, *, at=1000.0, key=ML, price=-150):
    return F.Quote(key=key, event_id=eid, sport_id=6, stream="live",
                   period=0, market_type="moneyline", side=None, line=None,
                   prices={"home": price, "away": 135}, epoch=1,
                   source_change_ms=at * 1000, frame_ts_ms=at * 1000,
                   received_ms=at * 1000 + 20)


# ═════════════════════════════════════════════════════════════════════
# THE HELD WATCH AND THE PRIORITY (pure)
# ═════════════════════════════════════════════════════════════════════

def test_the_watch_records_a_held_markets_change_and_announces_it():
    w = PH.HeldWatch(clock=lambda: 5.0)
    seen = []
    w.listeners.append(lambda eid, slugs: seen.append((eid, slugs)))
    w.set_targets({"slug-a": (11, None), "slug-b": (11, None),
                   "slug-c": (None, "OUT_OF_FEED_SCOPE_SPORT")})
    assert w.is_held(11) and not w.is_held(12)
    w.changed(_quote(12, at=100.0))                 # not held: nothing
    w.changed(_quote(11, at=100.0, key="s;0;s;1.5"))  # not the moneyline
    assert seen == [] and w.changed_at("slug-a") is None
    w.changed(_quote(11, at=100.0))
    assert seen == [(11, ["slug-a", "slug-b"])]
    assert w.changed_at("slug-a") == 100.0 == w.changed_at("slug-b")
    assert w.status()["unmatched"] == {"OUT_OF_FEED_SCOPE_SPORT": 1}
    # a slug no longer held forgets its change
    w.set_targets({"slug-a": (11, None)})
    assert w.changed_at("slug-b") is None


class _Cache:
    def __init__(self):
        self.ok = True

    def read(self, eid, key, evaluated_ms=None, max_age_s=30.0):
        return {"ok": self.ok}


def _sched(held, clock):
    async def ev(job):
        return {}

    async def audit(a):
        return None
    s = RX.Scheduler(_Cache(), ev, audit, clock=clock, queue_cap=3,
                     seed_cap=4, seed_ttl=60, held=held)
    for eid in range(1, 8):
        s.seeds[eid] = {"event": {}, "registered_at": clock()}
    return s


def test_held_events_win_the_reactive_budget_over_discovery():
    now = [1000.0]
    w = PH.HeldWatch()
    w.set_targets({"held-slug": (7, None)})
    s = _sched(w, lambda: now[0])
    for eid in (1, 2, 3):                           # discovery fills the queue
        s.changed(_quote(eid, at=now[0]))
    s.changed(_quote(7, at=now[0]))                 # then the held change
    for eid in (4, 5, 6):                           # more discovery churn
        s.changed(_quote(eid, at=now[0]))
    # the held change was never evicted and is served FIRST
    assert s.next_job()[0] == 7
    assert s.counts["HELD_QUEUED"] == 1
    assert s.counts["QUEUE_EVICTED"] == 3          # discovery keeps its cap
    assert [s.next_job()[0] for _ in range(3)] == [4, 5, 6]
    assert s.next_job() is None


def test_a_held_seed_is_pinned_while_held():
    now = [1000.0]
    w = PH.HeldWatch()
    w.set_targets({"held-slug": (7, None)})
    s = _sched(w, lambda: now[0])
    now[0] += 600                                   # past the discovery ttl
    s.changed(_quote(1, at=now[0]))
    assert s.counts["DISCOVERY_EXPIRED"] == 1
    s.changed(_quote(7, at=now[0]))
    assert list(s.held_pending) == [7]
    # eviction beyond the seed cap never takes the held seed
    s.seeds = OrderedDict([(7, {"event": {}, "registered_at": now[0]})] + [
        (k, {"event": {}, "registered_at": now[0]}) for k in (1, 2, 3, 4, 5)])
    s._evict_seeds()
    assert 7 in s.seeds and 1 not in s.seeds and len(s.seeds) == s.seed_cap
    # a held change without any discovery seed is counted, never invented
    w.set_targets({"held-slug": (7, None), "other": (99, None)})
    s.changed(_quote(99, at=now[0]))
    assert s.counts["HELD_NO_DISCOVERY_SEED"] == 1


def test_the_actual_review_is_triggered_by_a_held_change_with_a_pacing_gap():
    t = M.live_review_trigger
    assert t(last_reviewed_at=100.0, last_held=3, held=3, now=105.0,
             feed_change_at=103.0) is None          # inside the 10 s gap
    assert t(last_reviewed_at=100.0, last_held=3, held=3,
             now=100.0 + M.LIVE_MARKET_MIN_GAP_S,
             feed_change_at=103.0) == M.LIVE_T_MARKET
    assert t(last_reviewed_at=100.0, last_held=3, held=3, now=130.0,
             feed_change_at=99.0) is None           # changed before review


async def test_the_on_demand_read_is_bounded_to_one_second(monkeypatch):
    async def slow(*a, **k):
        await asyncio.sleep(5)
    monkeypatch.setattr(R, "held_moneyline", slow)
    t0 = time.monotonic()
    got = await PX._held_feed(None, pos={"us_market_slug": "s"},
                              payout_event="X", payout_is_complement=False,
                              at=0.0, max_age_s=30.0)
    assert time.monotonic() - t0 < R.HELD_ON_DEMAND_BUDGET_S + 0.5
    assert got == {"ok": False, "reason": R.R_ON_DEMAND_TIMEOUT}
    assert R.HELD_ON_DEMAND_BUDGET_S == 1.0


# ═════════════════════════════════════════════════════════════════════
# END TO END: FILL -> HANDOFF -> FIRST REVIEW -> HELD CHANGE -> FRESH
# ═════════════════════════════════════════════════════════════════════

async def _premap(conn, slug, *, start):
    ev = "ev-%s" % slug
    for i, (team, mslug) in enumerate(((AWAY, slug), (HOME, slug + "-h"))):
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, event_title, "
            " market_slug, kind, team_name, team_id, sports_type, "
            " game_start) VALUES ($1,$2,$3,$4,'moneyline',$5,$6,"
            " 'baseball_team_full_game_winner', to_timestamp($7))",
            "id-%s-%d" % (slug, i), ev, "%s vs %s" % (AWAY, HOME), mslug,
            team, 1000 + i, start)


async def _purge(conn, slugs):
    await XF._purge(conn, slugs)
    await conn.execute("DELETE FROM us_premap WHERE event_slug = ANY($1)",
                       ["ev-%s" % s for s in slugs])


async def _position(conn, tag, now, *, bid_now=0.40):
    """A completed-game paper position on an AWAY moneyline, filled at
    now - 56 and handed to Xavier at now - 55; the entry reading is an hour
    old (outside the lookback)."""
    a = await H.new_account(conn, tag, now=now - 5000)
    slug = "%sxfr-%s" % (PL.SYN, uuid.uuid4().hex[:10])
    g = "paper_g_%s_xfr" % a["account_id"][-10:]
    vid = await XF._reading(conn, slug, decided_at=now - 3600, pin_age_s=5.0,
                            p=0.45, payout_event=AWAY)
    did = await XF._decision(conn, a, slug=slug, vid=vid, p=0.45,
                             at=now - 3600)
    o = H.order(a, key="e", qty=100, limit=0.40, slug=slug, at=now - 60,
                group_id=g)
    o.update(decision_id=did, strategy=PB.CG_STRATEGY)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=now - 60)
    assert got["ok"], got
    await H.observe(conn, slug, now - 57, offers=[(0.40, 100)],
                    bids=[(0.38, 100)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=now - 56,
                             fee_fn=H.zero_fee)
    await PX.step_handoff(conn, XF._ctx(a, now - 55))
    await H.observe(conn, slug, now - 54, offers=[(bid_now + 0.02, 100)],
                    bids=[(bid_now, 100)])
    return a, g, slug


def _own(monkeypatch, cache):
    monkeypatch.setitem(R._STATE, "owner", SimpleNamespace(
        cache=cache, sport_ids=[6]))


async def _assessments(conn, g, kind="PAPER"):
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM xavier_management_assessments WHERE group_id=$1 "
        "   AND position_kind=$2 ORDER BY assessed_at", g, kind)]


@pg
async def test_end_to_end_the_re_review_gets_a_fresh_probability_from_the_held_refresh(
        monkeypatch):
    conn = await H.connect()
    slugs = []
    try:
        now = time.time()
        start = now + 3600
        a, g, slug = await _position(conn, "xfre2e", now)
        slugs.append(slug)
        await _premap(conn, slug, start=start)
        # the feed carries the held event; its moneyline last changed 120 s
        # ago -- the production situation: nothing fresh to read
        cache, ep = _feed(start=start, changed_at=now - 120)
        _own(monkeypatch, cache)
        watch = PH.HeldWatch()
        monkeypatch.setattr(PH, "WATCH", watch)
        PH.install(cache)
        announced = []
        PH.add_listener(lambda eid, ss: announced.append((eid, ss)))
        # FIRST REVIEW (handoff pass): stale, held, nothing discretionary
        out = await PX.step(conn, dict(XF._ctx(a, now - 50), deadline=0.0))
        assert out["by_trigger"] == {PX.T_FIRST: 1}
        (first,) = await _assessments(conn, g)
        assert first["evidence_state"] == XM.E_STALE
        assert first["recommendation"] == PX.A_HOLD
        m1 = _j((await conn.fetchrow(
            "SELECT measure FROM paper_xavier_reviews WHERE group_id=$1",
            g))["measure"])
        assert m1["feed_refusal"] == F.R_STALE      # the read was attempted
        # THE PRIORITY TARGETS: the open position resolves to the feed event
        got = await PH.refresh(conn, watch=watch)
        assert got["ok"] is True
        assert watch.slug_event[slug] == EID and watch.is_held(EID)
        # the held market MOVES on the feed: the watch notes and announces it
        _move(cache, ep, at=now, prices=PRICES)
        assert watch.changed_at(slug) == pytest.approx(now)
        assert (EID, [slug]) in announced
        # RE-REVIEW at once (the Xavier-only held review the change
        # schedules), 2 s after the change: FRESH from the on-demand read
        monkeypatch.setattr(S, "env_on", lambda: True)

        async def enabled(conn_):
            return {"enabled": True}
        monkeypatch.setattr(S, "enablement", enabled)
        res = await PR.held_review(conn, slugs=[slug], now=now + 2,
                                   account_id=a["account_id"])
        assert res["ran"] is True and res["groups"] == [g], res
        assert res["xavier"]["by_trigger"] == {PX.T_MARKET: 1}
        rows = await _assessments(conn, g)
        assert len(rows) == 2
        re = rows[1]
        assert re["trigger"] == PX.T_MARKET
        assert re["evidence_state"] == XM.E_FRESH
        assert re["probability_source"] == PB.SOURCE_FEED_CURRENT
        assert re["probability_age_s"] == pytest.approx(2.0, abs=0.1)
        assert re["review_latency_s"] == pytest.approx(2.0, abs=0.1)
        assert re["discretionary_permitted"] is True
        m2 = _j((await conn.fetchrow(
            "SELECT measure FROM paper_xavier_reviews WHERE group_id=$1 "
            " ORDER BY reviewed_at DESC LIMIT 1", g))["measure"])
        assert m2["feed"]["feed_event_id"] == EID
        assert m2["feed"]["market_key"] == ML
        assert m2["feed"]["designation"] == "away"
        assert m2["feed"]["quote_age_s"] <= 30.0
        assert m2["current_hold_value_usd"] is not None
        # nothing more is due until the next change or the cadence
        res = await PR.held_review(conn, slugs=[slug], now=now + 4,
                                   account_id=a["account_id"])
        assert res["xavier"]["reviews"] == 0
    finally:
        monkeypatch.setitem(R._STATE, "owner", None)
        await _purge(conn, slugs)
        await conn.close()


@pg
async def test_a_stale_only_world_is_stale_or_unavailable_and_never_discretionary(
        monkeypatch):
    conn = await H.connect()
    slugs = []
    try:
        now = time.time()
        start = now + 3600
        # a 0.80 bid: selling would out-value holding at the stale 0.45
        a, g, slug = await _position(conn, "xfstl", now, bid_now=0.80)
        slugs.append(slug)
        await _premap(conn, slug, start=start)
        cache, ep = _feed(start=start, changed_at=now - 120)
        _own(monkeypatch, cache)
        watch = PH.HeldWatch()
        monkeypatch.setattr(PH, "WATCH", watch)
        PH.install(cache)
        await PX.step(conn, dict(XF._ctx(a, now - 50), deadline=0.0))
        await PH.refresh(conn, watch=watch)
        # a change is announced, but the review runs 45 s later: the quote
        # is no longer fresh -> STALE, held, no discretionary action
        _move(cache, ep, at=now, prices=PRICES)
        monkeypatch.setattr(S, "env_on", lambda: True)

        async def enabled(conn_):
            return {"enabled": True}
        monkeypatch.setattr(S, "enablement", enabled)
        await PR.held_review(conn, slugs=[slug], now=now + 45,
                             account_id=a["account_id"])
        # and with no feed owner at all
        monkeypatch.setitem(R._STATE, "owner", None)
        await PX.review_group(conn, XF._ctx(a, now + 120), g,
                              trigger=PX.T_BACKSTOP)
        rows = await _assessments(conn, g)
        assert len(rows) == 3
        for r in rows:
            assert r["evidence_state"] in (XM.E_STALE, XM.E_NONE), r
            assert r["recommendation"] not in ("EXIT", "REDUCE",
                                               "REALLOCATE")
            assert r["discretionary_permitted"] is False
            assert _j(r["reallocate"])["recommended"] is False
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role "
            " IN ('EXIT','REDUCE')", g) == 0
        m = _j((await conn.fetchrow(
            "SELECT measure FROM paper_xavier_reviews WHERE group_id=$1 "
            " ORDER BY reviewed_at DESC LIMIT 1", g))["measure"])
        assert m["feed_refusal"] == F.R_NO_AUTHORITY
        assert m["current_hold_value_usd"] is None
    finally:
        monkeypatch.setitem(R._STATE, "owner", None)
        await _purge(conn, slugs)
        await conn.close()


@pg
async def test_a_held_change_re_reviews_the_actual_position_on_the_next_tick(
        monkeypatch):
    from tests import test_execmirror as TE
    conn = await H.connect()
    po = None
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        mirror._probability_reader = PX.live_position_evidence
        mirror._management_assessor = XM.actual_review_hook
        watch = PH.HeldWatch()
        monkeypatch.setattr(PH, "WATCH", watch)
        po = await TE._paper_order(conn, acct, qty=2702)
        await TE._paper_fill(conn, acct, po, qty=2702)
        venue.behaviour = [{"fill": 3}]
        t0 = time.time()
        await mirror.tick(conn)                       # FIRST
        watch.set_targets({po["slug"]: (EID, None)})
        watch.changed(_quote(EID, at=t0 + 3))
        mirror._now = lambda: t0 + 5                  # inside the pacing gap
        await mirror.tick(conn)
        mirror._now = lambda: t0 + M.LIVE_MARKET_MIN_GAP_S + 1
        await mirror.tick(conn)
        revs = await conn.fetch(
            "SELECT r.detail FROM smalllive_reviews r JOIN smalllive_handoffs h"
            " USING (handoff_id) WHERE h.group_id=$1 ORDER BY r.reviewed_at",
            po["group_id"])
        assert [_j(r["detail"])["review_trigger"] for r in revs] == [
            M.LIVE_T_FIRST, M.LIVE_T_MARKET]
        d = _j(revs[1]["detail"])
        assert d["review_latency_s"] == pytest.approx(
            M.LIVE_MARKET_MIN_GAP_S + 1 - 3, abs=0.5)
        assert len(venue.placed) == 1 and venue.cancelled == []
    finally:
        if po is not None:
            await XF._purge(conn, [po["slug"]])
        await conn.close()
