"""CAPITAL-CRITICAL: XAVIER'S MANAGEMENT PACKET CARRIES A FRESH PROBABILITY
WHEN ONE EXISTS -- AND ONLY THEN.

Production 2026-10-06 ~21:20Z (release 55eb7c82): 0 of 107 current Xavier
reviews had a complete packet; NO_FRESH_PROBABILITY on every one. The code-
controlled causes found and repaired here:

  RC1  paper_benchmark.xavier_measure read the feed quote's change instant as
       `prov["source_change_ms"] / 1000.0`. A change whose frame carried no
       provider stamp is dated by our labelled observation of it
       (pinnapi_feed CHANGE_CLOCK_LOCAL: source_change_ms None, change_ms the
       observation) -- so the FRESH read raised TypeError, the review never
       wrote, and paper_xavier.step aborted every review queued after it
       (a held market that just moved is queued FIRST, FEED_CHANGE_PRIORITY).
       Now the measure is dated by `change_ms`, the instant the feed's own
       30 s rule (and the held read's de-vig) measured.
  RC2  one group's raising review no longer starves the rest of the step.
  RC3  a held review refused because a paper pass holds the lock (R_BUSY) was
       DROPPED (its slugs cleared before the run): the market's fresh window
       passed unreviewed. It is now retried inside the 30 s window.

UNCHANGED, and proven so: the 30 s probability rule (an older or age-unknown
reading is still NO_FRESH_PROBABILITY -- a quiet line with no observed change
is never made fresh by a confirmation), the 300 s mark SLA, and the strict
management entry rail (an incomplete packet refuses a NEW entry; management
sales stay available). Synthetic data in a scratch test database; no network,
no venue, no order authority.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_ledger as L
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as FR
from sportsassets import xavier_freshness as XFT
from sportsassets import xavier_packet as XPK
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_runtime as PRT
from sportsassets.agents import paper_xavier as PX
from sportsassets.workers import ext_pinnacle_loop as LOOP

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_xavier_review_probability_freshness as XRF

#: the strict management entry rail runs its production functions here
MANAGEMENT_RAIL_ENFORCED = True

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XRF.AT
CG = XRF.CG
ML = {"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline", "period": 0,
      "status": "open",
      "prices": [{"designation": "home", "price": -120},
                 {"designation": "away", "price": 105}]}


# ═════════════════════════════════════════════════════════════════════
# THE FEED READ, AS THE REAL CACHE PRODUCES IT
# ═════════════════════════════════════════════════════════════════════

def _cache_read(*, change_at: float, evaluated_at: float, stamped: bool,
                change: bool = True) -> dict:
    """A REAL pinnapi_feed.FeedCache read of a full-game moneyline: the
    subscribe snapshot at change_at - 60 s, then (if `change`) a live frame
    that changes the price, received at change_at -- with the provider
    stamp `ts` = change_at when `stamped`, without one otherwise -- read at
    evaluated_at under the 30 s rule."""
    c = F.FeedCache()
    ep = c.new_connection([("live", 4)])
    c.apply({"type": "snapshot", "stream": "live", "sport_id": 4,
             "ts": (change_at - 60) * 1000.0,
             "events": [{"id": 5, "markets": [ML]}]},
            epoch=ep, received_ms=(change_at - 60) * 1000.0 + 10)
    if change:
        moved = {**ML, "prices": [{"designation": "home", "price": -140},
                                  {"designation": "away", "price": 120}]}
        frame = {"type": "live", "sport_id": 4, "op": "upd",
                 "rec": {"id": 5, "markets": [moved]}}
        if stamped:
            frame["ts"] = change_at * 1000.0
        c.apply(frame, epoch=ep, received_ms=change_at * 1000.0 + 40)
    else:
        # the same price re-sent: a confirmation, never a change
        c.apply({"type": "live", "sport_id": 4, "op": "upd",
                 "ts": (evaluated_at - 1) * 1000.0,
                 "rec": {"id": 5, "markets": [ML]}},
                epoch=ep, received_ms=(evaluated_at - 1) * 1000.0)
    return c.read(5, F.FULL_GAME_MONEYLINE_KEY,
                  evaluated_ms=evaluated_at * 1000.0,
                  max_age_s=float(LOOP.PINNACLE_MAX_AGE_S))


def _held_answer(got: dict, *, p: float = 0.71) -> dict:
    """What pinnapi_feed_runtime.held_quote returns for that read."""
    where = {"sport_id": 4, "feed_event_id": 5,
             "market_key": F.FULL_GAME_MONEYLINE_KEY, "designation": "home"}
    if not got.get("ok"):
        return dict(where, ok=False, reason=got.get("reason"),
                    provenance=got.get("provenance"))
    return dict(where, ok=True, p=p, p_selection=p, payout_event="HOME",
                payout_is_complement=False,
                provenance=dict(got["provenance"], stream="live"),
                devig={"version": "TEST"})


def _install_feed(monkeypatch, answer: dict):
    seen = []

    async def held_moneyline(conn, **kw):
        seen.append(kw)
        return answer
    monkeypatch.setattr(FR, "held_moneyline", held_moneyline)
    return seen


def test_the_pins():
    assert LOOP.PINNACLE_MAX_AGE_S == 30.0            # the probability rule
    assert L.MARK_STALE_AFTER_S == 300.0              # the mark SLA
    assert PRT.HELD_REVIEW_RETRY_WINDOW_S == float(LOOP.PINNACLE_MAX_AGE_S)
    assert XPK.CURRENT_BOOK_CLASSES == PMF.FRESHLY_MANAGEABLE
    assert XPK.P_PROBABILITY == "NO_FRESH_PROBABILITY"


def test_an_unstamped_change_reads_fresh_with_no_source_stamp():
    """The precondition of RC1, on the real cache: an ok read whose
    source_change_ms is None and whose change_ms is our observation."""
    got = _cache_read(change_at=AT - 4, evaluated_at=AT, stamped=False)
    assert got["ok"] is True
    prov = got["provenance"]
    assert prov["source_change_ms"] is None
    assert prov["change_clock"] == F.CHANGE_CLOCK_LOCAL
    assert prov["change_ms"] == pytest.approx((AT - 4) * 1000.0 + 40)
    assert prov["quote_age_s"] == pytest.approx(3.96)


@pg
async def test_xavier_measure_dates_an_unstamped_fresh_feed_change_by_change_ms(
        monkeypatch):
    """RC1 at the measure: before the repair this raised TypeError."""
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "xpfmeas", entry_age_s=3600)
        slugs.append(slug)
        _install_feed(monkeypatch, _held_answer(_cache_read(
            change_at=AT - 4, evaluated_at=AT, stamped=False)))
        pos = next(p for p in await L.positions(conn, a["account_id"])
                   if p["group_id"] == g)
        m = await PB.xavier_measure(conn, XRF._ctx(a, AT), pos=pos,
                                    strategy=CG, feed=PX._held_feed)
        assert m["source"] == PB.SOURCE_FEED_CURRENT and m["stale"] is False
        assert m["pinnacle_at"] == pytest.approx(AT - 4 + 0.04)
        assert m["feed"]["change_clock"] == F.CHANGE_CLOCK_LOCAL
        assert m["feed"]["source_change_ms"] is None
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
@pytest.mark.parametrize("stamped", [False, True])
async def test_a_fresh_feed_read_of_the_held_contract_completes_the_packet(
        monkeypatch, stamped):
    """A review of a held market with a fresh (<= 30 s) feed read of THAT
    contract carries the probability, persists it (valuation id) and -- the
    book current with exit depth and the protection valid -- is COMPLETE."""
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "xpf%s" % int(stamped),
                                     entry_age_s=3600)
        slugs.append(slug)
        seen = _install_feed(monkeypatch, _held_answer(_cache_read(
            change_at=AT - 4, evaluated_at=AT, stamped=stamped)))
        rv, m, alts, sales = await XRF._review(conn, a, g)
        # the read was for THIS contract (the entry valuation's identity)
        assert seen and seen[0]["us_market_slug"] == slug
        assert seen[0]["payout_event"] == "HOME"
        assert seen[0]["payout_is_complement"] is False
        assert m["evidence_state"] == PX.E_FRESH, m
        assert m["source"] == PB.SOURCE_FEED_CURRENT
        assert m["probability_age_s"] is not None
        assert 0 <= m["probability_age_s"] <= 30.0
        assert m["valuation_store"] == PX.VALUATION_STORE_SNAPSHOT
        assert m["valuation_id"] is not None
        pk = H.j(rv["selection"])["management_packet"]
        assert pk["gate"]["complete"] is True, pk["gate"]
        assert pk["probability"]["present"] is True
        assert pk["book"]["mark_class"] in XPK.CURRENT_BOOK_CLASSES
        assert m["management_packet"]["missing"] == []
        assert rv["refusal"] != XPK.R_XAVIER_PACKET_INCOMPLETE
        assert rv["recommendation"] not in XFT.NON_ACTIONS
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_fresh_stored_valuation_of_the_exact_contract_completes_the_packet():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "xpfval", entry_age_s=3600)
        slugs.append(slug)
        # decided 3 s ago on a quote 5 s older: 8 s old at the review
        vid = await XRF._reading(conn, slug, decided_at=AT - 3,
                                 pin_age_s=5.0, p=0.71)
        rv, m, alts, sales = await XRF._review(conn, a, g)
        assert m["evidence_state"] == PX.E_FRESH
        assert m["source"] == "PINNACLE_ONLY_CURRENT"
        assert m["valuation_id"] == vid
        assert m["valuation_store"] == PX.VALUATION_STORE_EXTERNAL
        assert H.j(rv["selection"])["management_packet"]["gate"][
            "complete"] is True
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
@pytest.mark.parametrize("case", ["old_feed", "quiet_line_confirmed",
                                  "stored_31s", "other_side_fresh"])
async def test_an_old_or_foreign_probability_is_still_no_fresh_probability(
        monkeypatch, case):
    """The 30 s rule is not widened: a feed change 45 s old, a quiet line
    whose price was only re-confirmed (no observed change: age unknown), a
    stored reading 31 s old, and a fresh reading of the OTHER payout side
    are all NO_FRESH_PROBABILITY -- WAITING_FOR_FRESH_EVIDENCE, no sale."""
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "xpfold%s" % case[:4],
                                     entry_age_s=3600)
        slugs.append(slug)
        if case == "old_feed":
            got = _cache_read(change_at=AT - 45, evaluated_at=AT,
                              stamped=True)
            assert got["reason"] == F.R_STALE
            _install_feed(monkeypatch, _held_answer(got))
        elif case == "quiet_line_confirmed":
            got = _cache_read(change_at=AT - 4, evaluated_at=AT,
                              stamped=True, change=False)
            assert got["reason"] == F.R_NO_CHANGE_TIME
            # a confirmation 1 s ago is carried, never a decision input
            assert got["provenance"]["age_since_confirmation_s"] == \
                pytest.approx(1.0)
            assert got["provenance"][
                "confirmation_is_a_decision_input"] is False
            _install_feed(monkeypatch, _held_answer(got))
        elif case == "stored_31s":
            await XRF._reading(conn, slug, decided_at=AT - 1,
                               pin_age_s=30.0, p=0.71)
        else:
            await XRF._reading(conn, slug, decided_at=AT - 3,
                               pin_age_s=5.0, p=0.29, complement=True)
        rv, m, alts, sales = await XRF._review(conn, a, g)
        assert m["evidence_state"] == PX.E_STALE, m
        missing = H.j(rv["selection"])["management_packet"]["gate"]["missing"]
        assert XPK.P_PROBABILITY in missing
        assert rv["recommendation"] == XFT.REC_WAITING
        assert rv["refusal"] == XPK.R_XAVIER_PACKET_INCOMPLETE
        assert sales == 0
        if case in ("old_feed", "quiet_line_confirmed"):
            assert m["feed_refusal"] == got["reason"]
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_one_raising_group_review_never_starves_the_rest(monkeypatch):
    # SETUP ONLY: a second entry on the same account (this proof is about the
    # step's isolation, not the entry rail, which has its own proof below)
    async def _not_judged(conn_, account_id, strategy, *, now):
        return {"refusal": None, "strategy": strategy}
    monkeypatch.setattr(PMF, "strategy_management_integrity", _not_judged)
    conn = await H.connect()
    slugs = []
    try:
        a, g1, s1 = await XRF._held(conn, "xpfstarve", entry_age_s=3600)
        slugs.append(s1)
        # a second held group on the same account
        s2 = "%sxf-%s" % (PL.SYN, uuid.uuid4().hex[:10])
        slugs.append(s2)
        g2 = g1 + "_2"
        vid = await XRF._reading(conn, s2, decided_at=AT - 3600,
                                 pin_age_s=5.0, p=0.62)
        did = await XRF._decision(conn, a, slug=s2, vid=vid, p=0.62,
                                  at=AT - 3600)
        o = H.order(a, key="e2", qty=XRF.QTY, limit=0.40, slug=s2,
                    at=AT - 60, group_id=g2)
        o.update(decision_id=did, strategy=CG)
        assert (await L.submit_order(conn, o, fee_fn=H.zero_fee,
                                     now=AT - 60))["ok"]
        await H.observe(conn, s2, AT - 57, offers=[(0.40, XRF.QTY)])
        from sportsassets import bettor_paper_simulator as SIM
        oid = await conn.fetchval("SELECT order_id FROM paper_orders WHERE "
                                  " group_id=$1", g2)
        await SIM.simulate_order(conn, oid, now=AT - 56, fee_fn=H.zero_fee)
        await PX.step_handoff(conn, XRF._ctx(a, AT - 55))
        real = PX.review_group
        first = sorted([g1, g2])[0]

        async def review_group(conn_, ctx_, g, **kw):
            if g == first:
                raise TypeError("unsupported operand type(s)")
            return await real(conn_, ctx_, g, **kw)
        monkeypatch.setattr(PX, "review_group", review_group)
        out = await PX.step(conn, XRF._ctx(a, AT))
        assert [e["group_id"] for e in out["review_errors"]] == [first]
        assert "TypeError" in out["review_errors"][0]["error"]
        other = g2 if first == g1 else g1
        assert await conn.fetchval("SELECT count(*) FROM paper_xavier_reviews"
                                   " WHERE group_id=$1", other) >= 1
        assert out["reviews"] >= 1
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# RC3: A BUSY HELD REVIEW IS RETRIED INSIDE THE FRESH WINDOW
# ═════════════════════════════════════════════════════════════════════

@pytest.fixture
def held_state():
    saved = {k: (set(v) if isinstance(v, set) else
                 dict(v) if isinstance(v, dict) else v)
             for k, v in PRT._HELD.items()}
    PRT._HELD.update(task=None, pending=set(), queued_at={},
                     busy_retries=0, busy_dropped=0)
    yield PRT._HELD
    PRT._HELD.clear()
    PRT._HELD.update(saved)


def test_requeue_busy_keeps_slugs_inside_the_window_and_drops_the_rest(
        held_state):
    held_state["queued_at"].update({"a": 100.0, "b": 60.0})
    got = PRT.requeue_busy({"a", "b"}, now=125.0)
    assert got == {"requeued": ["a"], "dropped": ["b"]}
    assert held_state["pending"] == {"a"}
    assert "b" not in held_state["queued_at"]
    assert held_state["busy_dropped"] == 1


async def test_a_held_review_refused_busy_is_retried_not_dropped(
        monkeypatch, held_state):
    calls = []
    clock = {"t": 1000.0}

    async def held_review(conn, *, slugs, **kw):
        calls.append(sorted(slugs))
        if len(calls) == 1:
            return {"ran": False, "refusal": PRT.R_BUSY,
                    "why": "PROCESS_LOCK_HELD"}
        return {"ran": True}

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
    monkeypatch.setattr(PRT, "HELD_REVIEW_MIN_GAP_S", 0.0)
    got = PRT.schedule_held_review(["slug-x"], get_pool=get_pool,
                                   clock=lambda: clock["t"])
    assert got["scheduled"] is True
    await asyncio.wait_for(held_state["task"], 5)
    assert calls == [["slug-x"], ["slug-x"]]
    assert held_state["busy_retries"] == 1
    assert held_state["queued_at"] == {}


async def test_a_busy_held_review_past_the_window_is_dropped_and_counted(
        monkeypatch, held_state):
    calls = []
    clock = {"t": 1000.0}

    async def held_review(conn, *, slugs, **kw):
        calls.append(sorted(slugs))
        clock["t"] += 31.0             # the pass held the lock past 30 s
        return {"ran": False, "refusal": PRT.R_BUSY}

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
    monkeypatch.setattr(PRT, "HELD_REVIEW_MIN_GAP_S", 0.0)
    PRT.schedule_held_review(["slug-y"], get_pool=get_pool,
                             clock=lambda: clock["t"])
    await asyncio.wait_for(held_state["task"], 5)
    assert calls == [["slug-y"]]            # bounded: no endless retry
    assert held_state["busy_dropped"] == 1 and not held_state["pending"]


# ═════════════════════════════════════════════════════════════════════
# THE STRICT RAIL IS UNCHANGED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_strict_rail_admits_on_complete_packets_and_refuses_otherwise(
        monkeypatch):
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "xpfrail", entry_age_s=3600)
        slugs.append(slug)
        # 0.95 beats the 0.80 bid: HOLD, the protection kept
        _install_feed(monkeypatch, _held_answer(_cache_read(
            change_at=AT - 4, evaluated_at=AT, stamped=False), p=0.95))
        rv, m, _, _ = await XRF._review(conn, a, g)
        assert rv["recommendation"] == "HOLD", rv["recommendation"]
        iv = await PMF.strategy_management_integrity(
            conn, a["account_id"], CG, now=AT + 1)
        assert iv["refusal"] is None, iv
        # 40 s later the same quote is stale: the newest review is
        # incomplete, and a NEW entry of the strategy is refused
        _install_feed(monkeypatch, _held_answer(_cache_read(
            change_at=AT - 4, evaluated_at=AT + 40, stamped=False),
            p=0.95))
        await PX.review_group(conn, XRF._ctx(a, AT + 40), g,
                              trigger=PX.T_EXPIRY)
        iv = await PMF.strategy_management_integrity(
            conn, a["account_id"], CG, now=AT + 41)
        assert iv["refusal"] == PMF.R_PACKETS_BLOCK_ALLOCATION
        o = H.order(a, key="new", qty=10, limit=0.50, at=AT + 41,
                    slug="%s:new" % a["account_id"], fixture="fx-new")
        o["strategy"] = CG
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=AT + 41)
        assert got["ok"] is False
        assert got["refusal"] == PMF.R_PACKETS_BLOCK_ALLOCATION
        # a management SALE of the held position is never blocked by it
        from sportsassets import bettor_paper_simulator as SIM
        for r in await conn.fetch(
                "SELECT order_id FROM paper_orders WHERE group_id=$1 AND "
                " role='STANDING_PROTECTION' AND state = ANY($2::text[])",
                g, list(L.OPEN_STATES)):
            await SIM.request_cancel(conn, r["order_id"], now=AT + 41.5,
                                     reason="TEST")
            await SIM.simulate_order(conn, r["order_id"], now=AT + 41.8,
                                     fee_fn=H.zero_fee)
        sale = H.order(a, key="sell", direction="SELL", role="EXIT", qty=5,
                       limit=0.30, slug=slug, at=AT + 42, group_id=g)
        sale["strategy"] = CG
        assert (await L.submit_order(conn, sale, now=AT + 42))["ok"]
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()
