"""XAVIER'S REVIEWS STATE THE FRESHNESS OF THE PROBABILITY THEY STAND ON.

Found in production by the agents: some HOLD reviews of the live-capable
investment policy (PINNACLE_COMPLETED_GAME_PAPER) were made on the stale
ENTRY-TIME Pinnacle probability without saying so. Every review now:

  * attempts a FRESH current probability for the held contract (a valuation
    for the same contract and payout outcome whose own source stamp is
    within ext_pinnacle_loop.PINNACLE_MAX_AGE_S, else the PinnAPI feed);
  * records `evidence_state` -- FRESH_CURRENT_PROBABILITY,
    STALE_ENTRY_TIME_PROBABILITY or PROBABILITY_UNAVAILABLE -- with the
    source, its source and receipt stamps, age and the limit used;
  * on anything but fresh, states `probability_limitation`, never shows the
    stale hold value as the current expected value, ranks no discretionary
    sale and never liquidates because the probability is missing;
  * unavailable is null, never 0.

Paper reviews (paper_xavier_reviews.measure) and the actual small-live
positions (smalllive_reviews.detail) both. SYNTHETIC data in a scratch test
database; the venue is a fake (no network, no real order).
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import xavier_freshness as XFT
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_xavier as PX
from sportsassets.workers import ext_pinnacle_loop as LOOP

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
CG = PB.CG_STRATEGY
AT = H.T0 + 50_000.0
QTY = 100


# ═════════════════════════════════════════════════════════════════════
# THE EVIDENCE STATE (pure)
# ═════════════════════════════════════════════════════════════════════

def test_the_limit_is_the_collectors_pinnacle_freshness_rule():
    from sportsassets import bettor_paper_session as S
    assert S.default_config()["entry"]["pinnacle_max_age_s"] == \
        float(LOOP.PINNACLE_MAX_AGE_S)


def test_the_three_states_and_no_placeholder_zero():
    fresh = PX.probability_evidence(
        {"p": 0.7, "source": "PINNACLE_ONLY_CURRENT", "stale": False,
         "pinnacle_at": AT - 8, "pinnacle_received_at": AT - 7,
         "pinnacle_age_s": 8.0, "pinnacle_limit_s": 30.0},
        at=AT, limit_s=30.0, qty=QTY)
    assert fresh["evidence_state"] == PX.E_FRESH == "FRESH_CURRENT_PROBABILITY"
    assert fresh["probability_limitation"] is None
    assert fresh["current_hold_value_usd"] == pytest.approx(70.0)
    assert fresh["entry_time_hold_value_usd"] is None

    stale = PX.probability_evidence(
        {"p": 0.62, "source": "ENTRY_TIME_MEASURE", "stale": True,
         "entry_pinnacle_at": AT - 3605, "entry_pinnacle_received_at":
         AT - 3604}, at=AT, limit_s=30.0, qty=QTY)
    assert stale["evidence_state"] == "STALE_ENTRY_TIME_PROBABILITY"
    assert stale["probability_age_s"] == pytest.approx(3605.0)
    assert stale["probability_source_at"] == AT - 3605
    assert stale["probability_received_at"] == AT - 3604
    assert "NOT a current expected value" in stale["probability_limitation"]
    assert stale["current_hold_value_usd"] is None
    assert stale["entry_time_hold_value_usd"] == pytest.approx(62.0)

    none = PX.probability_evidence({"p": None, "source": None, "stale": True},
                                   at=AT, limit_s=30.0, qty=QTY)
    assert none["evidence_state"] == "PROBABILITY_UNAVAILABLE"
    for k in ("probability", "probability_source_at", "probability_age_s",
              "probability_received_at", "current_hold_value_usd",
              "entry_time_hold_value_usd"):
        assert none[k] is None, k            # null, never a placeholder 0
    assert "never liquidates" in none["probability_limitation"]


@pytest.mark.parametrize("age", [-0.5, 30.5, 600.0])
def test_a_current_claim_outside_the_limit_is_not_fresh(age):
    """A measure that calls itself current is still not fresh when its own
    source stamp is in the future or older than the limit."""
    ev = PX.probability_evidence(
        {"p": 0.5, "source": "CURRENT_BLEND", "stale": False,
         "pinnacle_at": AT - age}, at=AT, limit_s=30.0, qty=QTY)
    assert ev["evidence_state"] == PX.E_STALE
    assert ev["current_hold_value_usd"] is None


# ═════════════════════════════════════════════════════════════════════
# PAPER REVIEWS THROUGH THE REAL REVIEW (Postgres)
# ═════════════════════════════════════════════════════════════════════

def _ctx(a, now):
    return {"account_id": a["account_id"], "session_id": a["session_id"],
            "config": a["config"], "now": now, "clock": lambda: now,
            "session": {"session_id": a["session_id"], "config": a["config"],
                        "reporting_tz": "America/New_York"},
            "fee_fn": H.zero_fee, "deadline": 1e18}


async def _decision(conn, acct, *, slug, vid, p, at, side="LONG"):
    did = "papercg:%s" % uuid.uuid4().hex[:24]
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, intent, "
        " fixture, label, verdict, refusal, refusals, p_internal, "
        " internal_model, p_pinnacle, pinnacle, p_blended, proposed_qty, "
        " limit_price, qualification_gaps, policy_version, policy_decision, "
        " simulator_version, strategy, economics) VALUES ($1,$2,$3,"
        " to_timestamp($4),$5,$6,$7,$8,'fx-1','{}'::jsonb,'ENTER',NULL,'{}',"
        " NULL,'{}'::jsonb,$9,'{}'::jsonb,NULL,$10,0.40,'[]'::jsonb,$11,"
        " '{}'::jsonb,'TEST',$12,'{}'::jsonb)",
        did, acct["session_id"], acct["account_id"], float(at), vid, slug,
        side, PL.LONG if side == "LONG" else "ORDER_INTENT_BUY_SHORT",
        float(p), QTY, PB.CG_VERSION, CG)
    return did


async def _reading(conn, slug, *, decided_at, pin_age_s, p,
                   payout_event="HOME", complement=False):
    v = await PL.valuation(conn, slug=slug, decided_at=decided_at,
                           p_pin=p, pin_age_s=pin_age_s)
    # a complement row names the event its probability is of
    await conn.execute("UPDATE external_valuations SET payout_event=$2, "
                       " payout_is_complement=$3, probability_event="
                       " CASE WHEN $3 THEN 'AWAY' END WHERE id=$1",
                       v["valuation_id"], payout_event, complement)
    return v["valuation_id"]


async def _held(conn, tag, *, entry_age_s, p_entry=0.62):
    """A held completed-game paper position handed to Xavier: the entry
    decision's valuation is `entry_age_s` old at the review instant AT, and
    the review's book bids 0.80 (a sale would out-value holding at 0.62)."""
    a = await H.new_account(conn, tag, now=AT - 5000)
    slug = "%sxf-%s" % (PL.SYN, uuid.uuid4().hex[:10])
    g = "paper_g_%s_xf" % a["account_id"][-10:]
    vid = await _reading(conn, slug, decided_at=AT - entry_age_s,
                         pin_age_s=5.0, p=p_entry)
    did = await _decision(conn, a, slug=slug, vid=vid, p=p_entry,
                          at=AT - entry_age_s)
    o = H.order(a, key="e", qty=QTY, limit=0.40, slug=slug, at=AT - 60,
                group_id=g)
    o.update(decision_id=did, strategy=CG)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=AT - 60)
    assert got["ok"], got
    await H.observe(conn, slug, AT - 57, offers=[(0.40, QTY)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=AT - 56,
                             fee_fn=H.zero_fee)
    await PX.step_handoff(conn, _ctx(a, AT - 55))
    await _protect(conn, a, g, at=AT - 54)
    await H.observe(conn, slug, AT - 2, offers=[(0.82, QTY)],
                    bids=[(0.80, QTY)])
    return a, g, slug


async def _protect(conn, a, g, *, at):
    """A valid standing protection before the review (paper_harness.protect)."""
    return (await H.protect(conn, _ctx(a, at), g, at=at))[0]


async def _review(conn, a, g):
    await PX.review_group(conn, _ctx(a, AT), g, trigger=PX.T_BACKSTOP)
    rv = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                             " group_id=$1 ORDER BY reviewed_at DESC LIMIT 1",
                             g)
    sales = await conn.fetchval(
        "SELECT count(*) FROM paper_orders WHERE group_id=$1 AND role "
        " IN ('EXIT', 'REDUCE')", g)
    return rv, H.j(rv["measure"]), H.j(rv["alternatives"]), sales


def _hold(alts):
    return next(c for c in alts["candidates"] if c["action"] == PX.A_HOLD)


async def _purge(conn, slugs):
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM external_valuations "
                           " WHERE us_market_slug = ANY($1::text[])", slugs)


@pg
async def test_a_fresh_valuation_for_the_same_contract_is_used_and_recorded():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held(conn, "xffresh", entry_age_s=3600)
        slugs.append(slug)
        await _reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0, p=0.71)
        rv, m, alts, _ = await _review(conn, a, g)
        assert m["evidence_state"] == PX.E_FRESH
        assert m["source"] == "PINNACLE_ONLY_CURRENT" and m["stale"] is False
        assert m["p"] == pytest.approx(0.71)
        assert m["probability"] == pytest.approx(0.71)
        assert m["probability_source"] == "PINNACLE_ONLY_CURRENT"
        assert m["probability_source_at"] == pytest.approx(AT - 8)
        assert m["probability_received_at"] == pytest.approx(AT - 7)
        assert m["probability_age_s"] == pytest.approx(8.0)
        assert m["probability_limit_s"] == float(LOOP.PINNACLE_MAX_AGE_S)
        assert m["probability_limitation"] is None
        assert m["current_hold_value_usd"] == pytest.approx(QTY * 0.71)
        assert m["entry_time_hold_value_usd"] is None
        hold = _hold(alts)
        assert hold["ev_basis"] == PX.E_FRESH and hold["ev_is_current"]
        assert hold["value_usd"] == pytest.approx(QTY * 0.71)
        assert hold["expected_net_usd"] is not None
        assert PX.E_STALE not in H.j(rv["exceptional"])
    finally:
        await _purge(conn, slugs)
        await conn.close()


@pg
@pytest.mark.parametrize("case", ["entry_only", "latest_too_old"])
async def test_without_fresh_evidence_the_review_says_so_and_never_sells(case):
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held(conn, "xfstale%s" % case[:4],
                                 entry_age_s=3600)
        slugs.append(slug)
        if case == "latest_too_old":
            # a later reading for the same contract, 160 s old at AT
            await _reading(conn, slug, decided_at=AT - 100, pin_age_s=60.0,
                           p=0.66)
        rv, m, alts, sales = await _review(conn, a, g)
        assert m["evidence_state"] == PX.E_STALE
        assert m["stale"] is True
        if case == "entry_only":
            assert m["source"] == "ENTRY_TIME_MEASURE"
            assert m["probability"] == pytest.approx(0.62)
            assert m["probability_source_at"] == pytest.approx(AT - 3605)
            assert m["probability_received_at"] == pytest.approx(AT - 3604)
            assert m["probability_age_s"] == pytest.approx(3605.0)
        else:
            assert m["source"] == "PINNACLE_ONLY_LATEST"
            assert m["probability"] == pytest.approx(0.66)
            assert m["probability_age_s"] == pytest.approx(160.0)
        assert m["probability_limit_s"] == float(LOOP.PINNACLE_MAX_AGE_S)
        lim = m["probability_limitation"]
        assert lim and "NOT a current expected value" in lim
        assert "FEED_OWNERSHIP_NOT_HELD" in lim     # the feed was attempted
        # the stale value is labelled entry-time, never current
        assert m["current_hold_value_usd"] is None
        assert m["entry_time_hold_value_usd"] == pytest.approx(
            QTY * m["probability"])
        # HOLD, EXIT AND REDUCE ALL LEFT THE RANKABLE SET BEFORE THE
        # SELECTOR (P0): the stale HOLD is not a candidate at all
        assert not [c for c in alts["candidates"]
                    if c["action"] in (PX.A_HOLD, PX.A_EXIT, PX.A_REDUCE)]
        hold = next(x for x in alts["not_rankable"]
                    if x["action"] == PX.A_HOLD)
        assert hold["blocker"] == PX.B_STALE_MEASURE
        assert hold["ev_basis"] == PX.E_STALE and hold["ev_is_current"] is False
        assert hold["expected_net_usd"] is None
        assert hold["entry_time_expected_net_usd"] is not None
        assert PX.E_STALE in H.j(rv["exceptional"])
        # no discretionary sale even though the 0.80 bid out-values holding
        # at the stale probability -- and NO default HOLD either (owner P0):
        # the review waits for fresh evidence; the selector ranked nothing
        assert rv["recommendation"] == XFT.REC_WAITING
        assert H.j(rv["selection"])["mechanical_selection"] is None
        assert sales == 0
        blocked = {x["action"] for x in alts["not_rankable"]
                   if x.get("blocker") == PX.B_STALE_MEASURE}
        assert {"HOLD", "EXIT", "REDUCE"} <= blocked
        # protection unaffected
        assert H.j(rv["action"])["taken"] in ("PLACE_STANDING",
                                              "KEEP_STANDING")
    finally:
        await _purge(conn, slugs)
        await conn.close()


@pg
@pytest.mark.parametrize("case", ["future_stamp", "other_outcome",
                                  "complement", "other_contract"])
async def test_a_future_stamped_or_mis_mapped_row_is_never_fresh(case):
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held(conn, "xfmap%s" % case[:5],
                                 entry_age_s=3600)
        slugs.append(slug)
        if case == "future_stamp":
            # stamped 5 s AFTER the review instant: a clock disagreement
            await _reading(conn, slug, decided_at=AT - 2, pin_age_s=-7.0,
                           p=0.90)
        elif case == "other_outcome":
            await _reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0,
                           p=0.90, payout_event="AWAY")
        elif case == "complement":
            await _reading(conn, slug, decided_at=AT - 3, pin_age_s=5.0,
                           p=0.90, complement=True)
        else:
            other = "%sxf-%s" % (PL.SYN, uuid.uuid4().hex[:10])
            slugs.append(other)
            await _reading(conn, other, decided_at=AT - 3, pin_age_s=5.0,
                           p=0.90)
        rv, m, alts, sales = await _review(conn, a, g)
        assert m["evidence_state"] == PX.E_STALE, m
        assert m["current_hold_value_usd"] is None
        assert m["probability_limitation"]
        if case == "future_stamp":
            assert m["probability_age_s"] < 0
        else:
            # the mis-mapped reading is not used at all
            assert m["source"] == "ENTRY_TIME_MEASURE"
            assert m["probability"] == pytest.approx(0.62)
        assert rv["recommendation"] == XFT.REC_WAITING and sales == 0
    finally:
        await _purge(conn, slugs)
        await conn.close()


@pg
async def test_an_unavailable_probability_is_null_and_liquidates_nothing(
        monkeypatch):
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await _held(conn, "xfnone", entry_age_s=3600)
        slugs.append(slug)

        async def measure(conn_, ctx_, *, pos, levels_buy):
            return {"p": None, "source": None, "stale": True,
                    "why": PX.R_NO_MEASURE}
        monkeypatch.setattr(PX, "_measure", measure)
        rv, m, alts, sales = await _review(conn, a, g)
        assert m["evidence_state"] == PX.E_NONE
        for k in ("probability", "probability_age_s", "probability_source_at",
                  "current_hold_value_usd", "entry_time_hold_value_usd"):
            assert m[k] is None, k
        assert m["probability_limitation"]
        assert rv["recommendation"] == XFT.REC_UNAVAILABLE and sales == 0
        assert H.j(rv["action"])["taken"] in ("PLACE_STANDING",
                                              "KEEP_STANDING")
        assert PX.E_NONE in H.j(rv["exceptional"])
    finally:
        await _purge(conn, slugs)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE ACTUAL POSITION (execmirror.xavier_live_reviews, smalllive_reviews)
# ═════════════════════════════════════════════════════════════════════

async def _live(conn, monkeypatch, *, fresh: bool, wired: bool = True):
    from tests import test_execmirror as TE
    acct, venue, mirror = await TE._setup(conn, monkeypatch)
    if wired:
        # what api/app.py hands the lane (execmirror imports no paper module)
        mirror._probability_reader = PX.live_position_evidence
    # rc6.3 pmus-sizing: a whole 3,000 (3 contracts at 1:1000); 2,702 is
    # now ROUNDED DOWN to 2 contracts, never enlarged to 3
    po = await TE._paper_order(conn, acct, qty=3000)
    now = time.time()
    vid = await _reading(conn, po["slug"], decided_at=now - 3600,
                         pin_age_s=5.0, p=0.62)
    did = await _decision(conn, acct, slug=po["slug"], vid=vid, p=0.62,
                          at=now - 3600)
    await conn.execute("UPDATE paper_orders SET decision_id=$2 "
                       " WHERE order_id=$1", po["order_id"], did)
    if fresh:
        await _reading(conn, po["slug"], decided_at=now - 1, pin_age_s=5.0,
                       p=0.71)
    await TE._paper_fill(conn, acct, po, qty=3000)
    venue.behaviour = [{"fill": 3}]
    await mirror.tick(conn)
    h = await conn.fetchrow("SELECT * FROM smalllive_handoffs WHERE "
                            " group_id = $1", po["group_id"])
    rv = await conn.fetchrow("SELECT * FROM smalllive_reviews WHERE "
                             " handoff_id = $1", h["handoff_id"])
    return po, venue, rv, H.j(rv["detail"])


@pg
async def test_the_actual_position_records_fresh_probability_evidence(
        monkeypatch):
    conn = await H.connect()
    po = None
    try:
        po, venue, rv, d = await _live(conn, monkeypatch, fresh=True)
        ev = d["probability_evidence"]
        assert d["evidence_state"] == ev["evidence_state"] == PX.E_FRESH
        assert ev["probability"] == pytest.approx(0.71)
        assert ev["probability_source"] == "PINNACLE_ONLY_CURRENT"
        assert 0 <= ev["probability_age_s"] <= LOOP.PINNACLE_MAX_AGE_S
        assert ev["probability_received_at"] is not None
        assert ev["probability_limit_s"] == float(LOOP.PINNACLE_MAX_AGE_S)
        assert ev["current_hold_value_usd"] == pytest.approx(3 * 0.71)
        assert ev["probability_limitation"] is None
        assert rv["action"] == "HOLD_FOLLOWS_PAPER_DECISION"
    finally:
        if po is not None:
            await _purge(conn, [po["slug"]])
        await conn.close()


@pg
async def test_the_actual_position_states_stale_evidence_and_is_not_sold(
        monkeypatch):
    from sportsassets import execmirror_view as V
    conn = await H.connect()
    po = None
    try:
        po, venue, rv, d = await _live(conn, monkeypatch, fresh=False)
        ev = d["probability_evidence"]
        assert d["evidence_state"] == PX.E_STALE
        # only the entry reading exists (outside the lookback): entry-time
        assert ev["probability_source"] == "ENTRY_TIME_MEASURE"
        assert ev["probability"] == pytest.approx(0.62)
        assert ev["probability_age_s"] > LOOP.PINNACLE_MAX_AGE_S
        assert "NOT a current expected value" in ev["probability_limitation"]
        assert ev["current_hold_value_usd"] is None
        assert ev["entry_time_hold_value_usd"] == pytest.approx(3 * 0.62)
        # the action still follows the paper decision: nothing is sold or
        # closed because the probability is stale
        assert rv["action"] == "HOLD_FOLLOWS_PAPER_DECISION"
        assert venue.closed == [] and len(venue.placed) == 1
        # management sees the freshness of the actual position's evidence
        row = {"group_id": po["group_id"]}
        mg = await V._management(conn, [row])
        xa = V._management_section(row, mg)["xavier_actual"]
        assert xa["latest_probability_evidence_state"] == PX.E_STALE
        assert "NOT a current expected value" in \
            xa["latest_probability_limitation"]
    finally:
        if po is not None:
            await _purge(conn, [po["slug"]])
        await conn.close()


@pg
async def test_an_unwired_reader_records_unavailable_and_sells_nothing(
        monkeypatch):
    conn = await H.connect()
    po = None
    try:
        po, venue, rv, d = await _live(conn, monkeypatch, fresh=True,
                                       wired=False)
        ev = d["probability_evidence"]
        assert d["evidence_state"] == ev["evidence_state"] == PX.E_NONE
        assert ev["why"] == "NO_PROBABILITY_READER_IN_THIS_PROCESS"
        for k in ("probability", "probability_age_s",
                  "current_hold_value_usd", "entry_time_hold_value_usd"):
            assert ev[k] is None, k
        assert rv["action"] == "HOLD_FOLLOWS_PAPER_DECISION"
        assert venue.closed == [] and len(venue.placed) == 1
    finally:
        if po is not None:
            await _purge(conn, [po["slug"]])
        await conn.close()


def test_the_lane_is_handed_the_reader_and_imports_no_paper_module():
    import inspect
    from pathlib import Path
    from sportsassets import execmirror as M
    assert "paper" not in "".join(
        ln for ln in inspect.getsource(M).splitlines()
        if ln.lstrip().startswith(("import ", "from ")))
    app = (Path(M.__file__).parent / "api" / "app.py").read_text()
    assert "probability_reader=_PX.live_position_evidence" in \
        app.replace("\n", "").replace(" ", "")
    assert M.UNAVAILABLE_PROBABILITY["evidence_state"] == PX.E_NONE
    assert set(M.UNAVAILABLE_PROBABILITY) == set(PX.probability_evidence(
        {"p": None}, at=AT, limit_s=30.0))
    assert M.MAX_INTENT_AGE_S == float(LOOP.PINNACLE_MAX_AGE_S)


def test_the_live_review_records_without_a_database_failure_path():
    """A failed read is PROBABILITY_UNAVAILABLE (null), never an exception
    that would stop the review loop, and never a placeholder 0."""
    import asyncio

    class _Broken:
        async def fetchval(self, *a, **k):
            raise RuntimeError("down")

        async def fetchrow(self, *a, **k):
            raise RuntimeError("down")

    ev = asyncio.run(PX.live_position_evidence(
        _Broken(), {"group_id": "g", "us_market_slug": "s",
                    "opened_intent": "ORDER_INTENT_BUY_LONG"},
        at=AT, qty=3))
    assert ev["evidence_state"] == PX.E_NONE
    assert ev["probability"] is None and ev["current_hold_value_usd"] is None
    assert ev["why"] == "PROBABILITY_READ_FAILED"
    assert ev["error"] == "RuntimeError"
    json.dumps(ev)
