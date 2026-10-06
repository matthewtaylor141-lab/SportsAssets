"""CAPITAL-CRITICAL: P0 CLOSEOUT -- XAVIER'S MANAGEMENT PACKET, PROTECTION
CONTINUITY AND CANONICAL PAPER POSITION RECONCILIATION.

Pure rules first, then the real review / simulator / ledger on synthetic data
in a scratch test database (no network, no real order):

  * a FRESH probability with a null persisted valuation_id is packet-
    INCOMPLETE; with an id it is present;
  * protection is present ONLY for PROTECTED_RESTING -- UNPROTECTED,
    CANCEL_PENDING, expired, cancelled, pending simulation, more than one live
    order, quantity mismatch and unknown are all INCOMPLETE;
  * on stale / incomplete evidence HOLD, EXIT and REDUCE are all moved to
    not_rankable BEFORE the selector -- XP.run ranks none of them;
  * GTD expiry -> terminal confirmation -> replacement on the next pass, and
    never two live-or-potentially-live protections;
  * a fully sold position (whole or fractional) reconciles to zero at once
    and leaves every canonical reader;
  * a still-displayed crossing level never fills the same resting order
    twice (economic duplicate);
  * no new PAPER ENTRY while protection continuity or packet completeness
    has failed.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_reconciliation as REC
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import open_position_canon as CANON
from sportsassets import xavier_packet as XPK
from sportsassets.agents import paper_xavier as PX
from sportsassets.agents import xavier_management as XM

from tests import paper_harness as H
from tests import test_xavier_review_probability_freshness as XRF

#: the strict management entry rail runs its production functions here
MANAGEMENT_RAIL_ENFORCED = True

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XRF.AT
QTY = XRF.QTY
NOW = 1_790_000_000.0


# ═════════════════════════════════════════════════════════════════════
# PURE
# ═════════════════════════════════════════════════════════════════════

def _packet(**over):
    kw = dict(residual={"open_qty": 10, "ledger_open_qty": 10,
                        "reconciled": True},
              evidence_state=XPK.E_FRESH, probability_source="PINNACLE",
              valuation_id=7,
              mark={"obs_id": 3, "age_s": 5, "bid": 0.4, "ask": 0.42,
                    "exit_depth_at_mark": 50},
              mark_class="FRESH",
              settlement={"fingerprint": "settle:x"},
              protection=PMF.protection_state(
                  [{"order_id": "o", "state": "RESTING", "qty": 10,
                    "filled_qty": 0, "limit_price": 0.5,
                    "expires_at": NOW + 3600}], 10, now=NOW))
    kw.update(over)
    return XPK.build(**kw)


def test_fresh_state_with_null_valuation_id_is_packet_incomplete():
    assert XPK.gate(_packet())["complete"] is True
    p = _packet(valuation_id=None)
    g = XPK.gate(p)
    assert g["complete"] is False and g["missing"] == [XPK.P_PROBABILITY]
    assert p["probability"]["absent_because"] == \
        "FRESH_BUT_NO_PERSISTED_VALUATION_ID"
    for a in ("HOLD", "EXIT", "REDUCE"):
        assert not XPK.permits(a, p)


def _st(state, qty=10, filled=0, expires=NOW + 3600):
    return {"order_id": "o-%s" % state, "state": state, "qty": qty,
            "filled_qty": filled, "limit_price": 0.5, "expires_at": expires}


@pytest.mark.parametrize("standing,open_qty,want", [
    ([], 10, "UNPROTECTED_NO_STANDING_ORDER"),
    ([_st("CANCEL_PENDING")], 10, "PROTECTION_CANCEL_PENDING"),
    ([_st("RESTING", expires=NOW - 1)], 10, "PROTECTION_EXPIRED"),
    ([_st("EXPIRED")], 10, "PROTECTION_EXPIRED"),
    ([_st("CANCELED")], 10, "PROTECTION_CANCELLED"),
    ([_st("PENDING_SIMULATION")], 10, "PROTECTION_PENDING_SIMULATION"),
    ([_st("RESTING"), _st("PARTIALLY_FILLED")], 10,
     "PROTECTION_MULTIPLE_LIVE_ORDERS"),
    ([_st("RESTING", qty=8)], 10, "PROTECTION_QTY_DIFFERS_FROM_OPEN_QTY"),
    ([_st("RESTING")], 10.4, "PROTECTION_QTY_DIFFERS_FROM_OPEN_QTY"),
    ([_st("SOMETHING_NEW")], 10, "PROTECTION_STATE_UNKNOWN"),
])
def test_every_non_valid_protection_state_is_packet_incomplete(
        standing, open_qty, want):
    pr = PMF.protection_state(standing, open_qty, now=NOW)
    assert pr["state"] == want
    assert XPK.protection_present(pr) is False
    g = XPK.gate(_packet(protection=pr))
    assert g["complete"] is False and g["missing"] == [XPK.P_PROTECTION]


def test_only_an_active_quantity_matched_resting_order_is_protection():
    for st in ("RESTING", "PARTIALLY_FILLED"):
        pr = PMF.protection_state([_st(st, qty=12, filled=2)], 10, now=NOW)
        assert pr["state"] == "PROTECTED_RESTING"
        assert pr["active"] is True and pr["qty_matched"] is True
        assert XPK.protection_present(pr) is True
    # a dict that merely SAYS the state, without the evidence, is not enough
    assert not XPK.protection_present({"state": "PROTECTED_RESTING",
                                       "known": True})
    assert not XPK.protection_present(None)


def test_the_pins():
    assert L.MARK_STALE_AFTER_S == 300.0          # the executable mark SLA
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    assert LOOP.PINNACLE_MAX_AGE_S == 30.0        # the probability rule
    assert PX.XPK_P_PROTECTION == XPK.P_PROTECTION
    assert PMF.FIRST_MANAGEMENT_GRACE_S == XM.PAPER_FIRST_REVIEW_BOUND_S
    assert XPK.VALID_PROTECTION_STATES == (PMF.PS_PROTECTED,)
    assert L.OPEN_QTY_EPS == CANON.OPEN_QTY_EPS == 1e-9
    from sportsassets import order_state_truth as OST
    assert OST.OPEN_QTY_EPS == CANON.OPEN_QTY_EPS


def test_a_fully_sold_protected_position_reads_closed_not_its_history():
    """8 bought, 8 sold by a filled protective sale: position_qty (held + sold
    by protection) is still 8 by its documented basis, but the position is
    CLOSED and nothing is unprotected -- never displayed as an open 8."""
    from sportsassets import order_state_truth as OST
    s = OST.protection_summary(held_qty=0.0, orders=[{
        "role": "STANDING_PROTECTION", "direction": "SELL", "qty": 8,
        "filled_qty": 8, "raw_state": "FILLED", "source": "PAPER",
        "order_ref": "o", "limit": 0.83}])
    assert s["position_state"] == "CLOSED"
    assert s["held_qty_now"] == 0.0 and s["unprotected_qty"] == 0.0
    assert OST.protection_summary(held_qty=0.39, orders=[])[
        "position_state"] == "OPEN"
    assert L.CANONICAL_OPEN_POSITIONS_SQL is CANON.CANONICAL_OPEN_POSITIONS_SQL
    from sportsassets import refusal_taxonomy_table as T
    for code in (PMF.R_PACKETS_BLOCK_ALLOCATION,
                 PMF.R_PROTECTION_BLOCKS_ALLOCATION,
                 PMF.R_INTEGRITY_UNREADABLE, PX.B_NO_FRESH_EXIT_WALK):
        assert code in T.TABLE, code


def test_a_still_displayed_crossing_level_is_not_new_liquidity():
    lv = [{"price": 0.52, "wire": 0.52, "qty": 105.81}]
    seen = SIM.update_seen(lv, limit=0.45, direction="SELL", seen={})
    assert seen == {"0.520000": 105.81}
    # the next book shows the SAME level: nothing is available to us
    c = SIM.carry_seen(lv, consumed={}, seen=seen)
    got = SIM.resting_cross(lv, consumed=c, limit=0.45, direction="SELL",
                            queue_ahead=0.0, remaining=500.0)
    assert got["filled"] == 0.0
    # the level grows: only the increment is new
    lv2 = [{"price": 0.52, "wire": 0.52, "qty": 130.0}]
    c = SIM.carry_seen(lv2, consumed={}, seen=seen)
    got = SIM.resting_cross(lv2, consumed=c, limit=0.45, direction="SELL",
                            queue_ahead=0.0, remaining=500.0)
    assert got["filled"] == pytest.approx(130.0 - 105.81)
    # the level vanishes, then reappears: the memory forgot it (new)
    gone = SIM.update_seen([], limit=0.45, direction="SELL", seen=seen)
    assert gone == {}


def _pos(k, group, last_fill_at):
    return {"position_key": k, "group_id": group, "last_fill_at": last_fill_at}


def test_the_integrity_verdict():
    """STRICT (P1): ANY applicable position with an incomplete packet or a
    protection other than PROTECTED_RESTING refuses growth; only a market
    EXTERNAL_UNAVAILABLE at the venue is not applicable."""
    pos = [_pos("a", "g1", NOW - 3600), _pos("b", "g2", NOW - 3600)]
    good = {"state": "PROTECTED_RESTING"}
    v = PMF.integrity_verdict(positions=pos, packets={"g1": True, "g2": True},
                              protections={"a": good, "b": good}, now=NOW)
    assert v["refusal"] is None and v["rule"] == \
        "STRICT_ANY_APPLICABLE_POSITION"
    # ONE of ten unprotected is enough
    many = [_pos(str(i), "g%d" % i, NOW - 3600) for i in range(10)]
    ok_p = {"g%d" % i: True for i in range(10)}
    prot = {str(i): good for i in range(10)}
    prot["3"] = {"state": "UNPROTECTED_NO_STANDING_ORDER"}
    v = PMF.integrity_verdict(positions=many, packets=ok_p,
                              protections=prot, now=NOW)
    assert v["refusal"] == PMF.R_PROTECTION_BLOCKS_ALLOCATION
    for bad in ("PROTECTION_CANCEL_PENDING", "PROTECTION_EXPIRED",
                "PROTECTION_QTY_DIFFERS_FROM_OPEN_QTY",
                "PROTECTION_MULTIPLE_LIVE_ORDERS"):
        prot["3"] = {"state": bad}
        assert PMF.integrity_verdict(positions=many, packets=ok_p,
                                     protections=prot, now=NOW)[
            "refusal"] == PMF.R_PROTECTION_BLOCKS_ALLOCATION, bad
    # ONE incomplete packet is enough
    prot["3"] = good
    v = PMF.integrity_verdict(positions=many, packets=dict(ok_p, g7=False),
                              protections=prot, now=NOW)
    assert v["refusal"] == PMF.R_PACKETS_BLOCK_ALLOCATION
    # an unreviewed new fill blocks too (no grace) and is named
    fresh = [_pos("n", "gn", NOW - 10)]
    v = PMF.integrity_verdict(positions=fresh, packets={}, protections={},
                              reviewed_at={}, now=NOW)
    assert v["refusal"] is not None
    assert v["awaiting_first_management"] == ["n"]
    # a terminal / halted market at the venue is not applicable
    v = PMF.integrity_verdict(
        positions=[_pos("x", "gx", NOW - 3600)], packets={},
        protections={"x": {"state": "UNPROTECTED_NO_STANDING_ORDER"}},
        now=NOW, classes={"x": PMF.EXTERNAL_UNAVAILABLE})
    assert v["refusal"] is None
    assert v["excluded_external_unavailable"] == ["x"]


# ═════════════════════════════════════════════════════════════════════
# THE REAL REVIEW / SIMULATOR / LEDGER (Postgres)
# ═════════════════════════════════════════════════════════════════════

async def _live_standing(conn, g):
    return await conn.fetch(
        "SELECT order_id, state FROM paper_orders WHERE group_id=$1 AND "
        " role='STANDING_PROTECTION' AND state = ANY($2::text[])", g,
        list(L.OPEN_STATES))


@pg
async def test_a_stale_packet_ranks_no_hold_exit_or_reduce():
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "p0stale", entry_age_s=3600)
        slugs.append(slug)
        rv, m, alts, sales = await XRF._review(conn, a, g)
        assert m["evidence_state"] == PX.E_STALE
        assert not [c for c in alts["candidates"]
                    if c["action"] in ("HOLD", "EXIT", "REDUCE")]
        nr = {c["action"]: c["blocker"] for c in alts["not_rankable"]
              if c["action"] in ("HOLD", "EXIT", "REDUCE")}
        assert nr == {"HOLD": PX.B_STALE_MEASURE, "EXIT": PX.B_STALE_MEASURE,
                      "REDUCE": PX.B_STALE_MEASURE}
        assert H.j(rv["selection"])["mechanical_selection"] is None
        assert rv["recommendation"] not in XPK.MANAGEMENT_ACTIONS
        assert sales == 0
        # the protection stays exactly one and valid
        assert len(await _live_standing(conn, g)) == 1
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_fresh_probability_without_a_persisted_valuation_never_ranks(
        monkeypatch):
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "p0noval", entry_age_s=3600)
        slugs.append(slug)

        async def measure(conn_, ctx_, *, pos, levels_buy):
            return {"p": 0.71, "source": "PINNACLE_ONLY_CURRENT",
                    "stale": False, "pinnacle_at": AT - 3,
                    "pinnacle_limit_s": 30.0}

        async def no_snapshot(conn_, **kw):
            return None                  # the snapshot write failed
        monkeypatch.setattr(PX, "_measure", measure)
        monkeypatch.setattr(PX, "persist_probability_snapshot", no_snapshot)
        rv, m, alts, sales = await XRF._review(conn, a, g)
        assert m["evidence_state"] == PX.E_FRESH
        assert m.get("valuation_id") is None
        assert m["management_packet"]["missing"] == [XPK.P_PROBABILITY]
        assert not [c for c in alts["candidates"]
                    if c["action"] in ("HOLD", "EXIT", "REDUCE")]
        assert rv["recommendation"] == "MANAGEMENT_UNAVAILABLE_STALE_INPUT"
        assert sales == 0
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_a_fresh_feed_reading_is_persisted_and_carries_its_id(
        monkeypatch):
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "p0snap", entry_age_s=3600)
        slugs.append(slug)

        async def measure(conn_, ctx_, *, pos, levels_buy):
            return {"p": 0.95, "source": "PINNAPI_FEED_CURRENT",
                    "stale": False, "pinnacle_at": AT - 3,
                    "pinnacle_limit_s": 30.0,
                    "feed": {"payout_event": "HOME",
                             "payout_is_complement": False}}
        monkeypatch.setattr(PX, "_measure", measure)
        rv, m, alts, sales = await XRF._review(conn, a, g)
        assert m["valuation_store"] == PX.VALUATION_STORE_SNAPSHOT
        row = await conn.fetchrow("SELECT * FROM xavier_probability_"
                                  "snapshots WHERE snapshot_id=$1",
                                  int(m["valuation_id"]))
        assert row["group_id"] == g and row["probability"] == 0.95
        assert m["management_packet"]["complete"] is True
        assert rv["recommendation"] == "HOLD"         # 0.95 beats the 0.80 bid
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_gtd_expiry_terminal_confirmation_then_replacement():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "p0gtd")
        slug = "p0-gtd-%s" % a["account_id"][-10:]
        g = "paper_g_%s_gtd" % a["account_id"][-10:]
        t0 = H.T0 + 100
        await _filled_position(conn, a, qty=40, slug=slug, g=g, at=t0)
        await PX.step_handoff(conn, XRF._ctx(a, t0 + 5))
        (oid,) = await H.protect(conn, XRF._ctx(a, t0 + 6), g, at=t0 + 6)
        exp = float(await conn.fetchval(
            "SELECT extract(epoch FROM expires_at) FROM paper_orders "
            " WHERE order_id=$1", oid))
        t = exp + 1.0
        # a current (non-crossing) book so the review's walk is readable
        await H.observe(conn, slug, t - 1, bids=[(0.30, 40)],
                        offers=[(0.60, 40)])
        pos = next(p for p in await L.positions(conn, a["account_id"])
                   if p["group_id"] == g)
        # 1 · past expiry, NOT yet terminal: protection EXPIRED (packet-
        # incomplete) and nothing is placed beside it
        pr = PMF.protection_state(
            [dict(r) for r in await conn.fetch(
                "SELECT * FROM paper_orders WHERE order_id=$1", oid)],
            pos["open_qty"], now=t)
        assert pr["state"] == "PROTECTION_EXPIRED"
        assert not XPK.protection_present(pr)
        await PX.review_group(conn, XRF._ctx(a, t), g, trigger=PX.T_BACKSTOP)
        rv = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                 " group_id=$1 ORDER BY reviewed_at DESC "
                                 " LIMIT 1", g)
        assert H.j(rv["action"])["taken"] == "WAIT_FOR_TERMINAL_EXPIRY"
        assert XPK.P_PROTECTION in H.j(rv["measure"])["management_packet"][
            "missing"]
        assert len(await _live_standing(conn, g)) == 1
        # 2 · terminal confirmation (the simulator's step)
        await SIM.simulate_order(conn, oid, now=t + 1, fee_fn=H.zero_fee)
        assert await conn.fetchval("SELECT state FROM paper_orders WHERE "
                                   " order_id=$1", oid) == "EXPIRED"
        # 3 · replacement on the next feasible pass, exactly one live
        await PX.review_group(conn, XRF._ctx(a, t + 2), g,
                              trigger=PX.T_ORDER)
        live = await _live_standing(conn, g)
        assert len(live) == 1 and live[0]["order_id"] != oid
        rv = await conn.fetchrow("SELECT * FROM paper_xavier_reviews WHERE "
                                 " group_id=$1 ORDER BY reviewed_at DESC "
                                 " LIMIT 1", g)
        assert H.j(rv["action"])["taken"] == "PLACE_STANDING"
    finally:
        await conn.close()


@pg
async def test_never_two_live_or_potentially_live_protections():
    from sportsassets import bettor_xavier_standing_orders as SPO
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "p0two", entry_age_s=3600)
        slugs.append(slug)
        (st,) = await _live_standing(conn, g)
        await SIM.request_cancel(conn, st["order_id"], now=AT - 10,
                                 reason="TEST")
        pos = next(p for p in await L.positions(conn, a["account_id"])
                   if p["group_id"] == g)
        standing = [dict(r) for r in await conn.fetch(
            "SELECT * FROM paper_orders WHERE order_id=$1", st["order_id"])]
        prot = PX.protective_price(qty=pos["open_qty"],
                                   cost_basis=pos["cost_basis_usd"],
                                   fee_fn=H.zero_fee, at=AT - 9)
        got = await PX._maintain_standing(
            conn, XRF._ctx(a, AT - 9), pos=pos, standing=standing, prot=prot,
            md=None, at=AT - 9, SPO=SPO)
        assert got["taken"] == "WAIT_FOR_TERMINAL"
        assert len(await _live_standing(conn, g)) == 1
        # the database refuses a racing second one outright
        got = await PX._maintain_standing(
            conn, XRF._ctx(a, AT - 8), pos=pos, standing=[], prot=prot,
            md=None, at=AT - 8, SPO=SPO)
        assert got["taken"] in ("PLACEMENT_REFUSED", "NONE"), got
        assert len(await _live_standing(conn, g)) == 1
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


async def _filled_position(conn, a, *, qty, slug, g, at):
    o = H.order(a, key="e%s" % qty, qty=qty, limit=0.40, slug=slug, at=at,
                group_id=g)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3, offers=[(0.40, qty)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=at + 4,
                             fee_fn=H.zero_fee)


@pg
@pytest.mark.parametrize("qty", [8, 1454.12, 416.39])
async def test_a_fully_sold_position_closes_and_leaves_every_reader(qty):
    from sportsassets import pinnapi_held as PH
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "p0sold")
        slug = "p0-sold-%s" % a["account_id"][-10:]
        g = "paper_g_%s_p0" % a["account_id"][-10:]
        t = H.T0 + 100
        await _filled_position(conn, a, qty=qty, slug=slug, g=g, at=t)
        await PX.step_handoff(conn, XRF._ctx(a, t + 5))
        # the protection covers the WHOLE held qty, fraction included
        (oid,) = await H.protect(conn, XRF._ctx(a, t + 6), g, at=t + 6)
        assert float(await conn.fetchval("SELECT qty FROM paper_orders WHERE"
                                         " order_id=$1", oid)) == \
            pytest.approx(qty)
        # a bid strictly better than the protective price fills it entirely
        await H.observe(conn, slug, t + 10, bids=[(0.95, 10_000)])
        await SIM.simulate_order(conn, oid, now=t + 11, fee_fn=H.zero_fee)
        assert await conn.fetchval("SELECT state FROM paper_orders WHERE "
                                   " order_id=$1", oid) == "FILLED"
        allp = [p for p in await L.positions(conn, a["account_id"],
                                             include_closed=True)
                if p["group_id"] == g]
        assert len(allp) == 1 and allp[0]["open_qty"] == 0.0
        # CLOSED: out of the ledger's open set, the canonical SQL, the held
        # watch, the freshness denominator and Xavier's queue
        assert not [p for p in await L.positions(conn, a["account_id"])
                    if p["group_id"] == g]
        assert not await conn.fetch(
            "SELECT 1 FROM (" + CANON.CANONICAL_OPEN_POSITIONS_SQL + ") c "
            " WHERE c.group_id=$1", g)
        assert slug not in {r["slug"] for r in await conn.fetch(
            PH.HELD_SLUGS_SQL)}
        rows = await PMF.position_rows(conn, a["account_id"], now=t + 12)
        assert not [r for r in rows if r.get("group_id") == g
                    or g in str(r.get("position_key"))]
        out = await PX.step(conn, XRF._ctx(a, t + 12))
        assert g not in str(out)
        # the receipt agrees: closed, no phantom, no live protection on it
        rc = await REC.receipt(conn, a["account_id"], now=t + 13)
        assert rc["status"] == "OK"
        assert rc["counts"]["true_open"] == 0
        assert rc["counts"]["closed"] == 1
        assert rc["counts"]["live_protection_on_closed_positions"] == 0
        assert all(v == 0 for v in rc["counts"][
            "phantom_opens_by_legacy_reader"].values())
    finally:
        await conn.close()


@pg
async def test_a_still_displayed_crossing_level_fills_a_resting_order_once():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "p0dup")
        slug = "p0-dup-%s" % a["account_id"][-10:]
        g = "paper_g_%s_dup" % a["account_id"][-10:]
        t = H.T0 + 100
        await _filled_position(conn, a, qty=300, slug=slug, g=g, at=t)
        await PX.step_handoff(conn, XRF._ctx(a, t + 5))
        (oid,) = await H.protect(conn, XRF._ctx(a, t + 6), g, at=t + 6)
        # the SAME crossing bid observed three times before the next step
        for k in range(3):
            await H.observe(conn, slug, t + 10 + k, bids=[(0.95, 105.81)])
        await SIM.simulate_order(conn, oid, now=t + 20, fee_fn=H.zero_fee)
        fills = await conn.fetch("SELECT qty FROM paper_fills WHERE "
                                 " order_id=$1", oid)
        assert [float(f["qty"]) for f in fills] == [pytest.approx(105.81)]
        # a new observation of the same level on a later step: still once
        await H.observe(conn, slug, t + 30, bids=[(0.95, 105.81)])
        await SIM.simulate_order(conn, oid, now=t + 31, fee_fn=H.zero_fee)
        assert await conn.fetchval("SELECT count(*) FROM paper_fills WHERE "
                                   " order_id=$1", oid) == 1
        # genuinely new size (the level grew) fills only the increment
        await H.observe(conn, slug, t + 40, bids=[(0.95, 150.0)])
        await SIM.simulate_order(conn, oid, now=t + 41, fee_fn=H.zero_fee)
        assert float(await conn.fetchval(
            "SELECT sum(qty) FROM paper_fills WHERE order_id=$1", oid)) == \
            pytest.approx(150.0)
    finally:
        await conn.close()


@pg
async def test_no_new_paper_entry_while_protection_continuity_has_failed():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "p0rail")
        slug = "p0-rail-%s" % a["account_id"][-10:]
        g = "paper_g_%s_rl" % a["account_id"][-10:]
        t = H.T0 + 100
        await _filled_position(conn, a, qty=50, slug=slug, g=g, at=t)
        later = t + PMF.FIRST_MANAGEMENT_GRACE_S + 60
        # a fresh book: the stale-mark rail alone would permit growth
        await H.observe(conn, slug, later - 5, bids=[(0.39, 50)],
                        offers=[(0.41, 50)])
        o = H.order(a, key="n1", qty=10, limit=0.50, at=later,
                    slug="%s:n1" % a["account_id"], fixture="fx-n1")
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=later)
        assert got["ok"] is False
        assert got["refusal"] == PMF.R_PROTECTION_BLOCKS_ALLOCATION
        assert got["protection_not_valid"][0]["state"] == \
            "UNPROTECTED_NO_STANDING_ORDER"
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE idempotency_key=$1",
            o["idempotency_key"]) == 0
        # protected, but its latest review's packet was incomplete (stale)
        await PX.step_handoff(conn, XRF._ctx(a, later))
        await H.protect(conn, XRF._ctx(a, later + 1), g, at=later + 1)
        await PX.review_group(conn, XRF._ctx(a, later + 2), g,
                              trigger=PX.T_BACKSTOP)
        got = await L.submit_order(conn, dict(o, idempotency_key=o[
            "idempotency_key"] + ":2"), fee_fn=H.zero_fee, now=later + 3)
        assert got["ok"] is False
        assert got["refusal"] == PMF.R_PACKETS_BLOCK_ALLOCATION
        # a management SALE is never blocked by the rail
        sale = H.order(a, key="sell", direction="SELL", role="EXIT", qty=5,
                       limit=0.30, slug=slug, at=later + 3, group_id=g)
        await conn.execute("UPDATE paper_orders SET state='CANCEL_PENDING' "
                           " WHERE group_id=$1 AND role='STANDING_"
                           "PROTECTION' AND state='RESTING'", g)
        await SIM.simulate_order(conn, (await _live_standing(conn, g))[0][
            "order_id"], now=later + 3, fee_fn=H.zero_fee)
        assert (await L.submit_order(conn, sale, now=later + 4))["ok"]
    finally:
        await conn.close()


@pg
async def test_the_receipt_names_sub_contract_remainders_and_duplicates():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "p0rcpt")
        slug = "p0-rcpt-%s" % a["account_id"][-10:]
        g = "paper_g_%s_rc" % a["account_id"][-10:]
        t = H.T0 + 100
        await _filled_position(conn, a, qty=416.39, slug=slug, g=g, at=t)
        # the OLD whole-contract protection sold 416 and left 0.39 behind
        sale = H.order(a, key="s416", direction="SELL", role="EXIT",
                       qty=416, limit=0.30, slug=slug, at=t + 10, group_id=g)
        got = await L.submit_order(conn, sale, now=t + 10)
        assert got["ok"], got
        await H.observe(conn, slug, t + 13, bids=[(0.35, 1000)])
        await SIM.simulate_order(conn, got["order"]["order_id"], now=t + 14,
                                 fee_fn=H.zero_fee)
        rc = await REC.receipt(conn, a["account_id"], now=t + 20)
        assert rc["status"] == "OK"
        assert rc["counts"]["true_open"] == 1
        assert rc["counts"]["sub_contract_remainders_open"] == 1
        (rem,) = rc["sections"]["sub_contract_remainders"]
        assert rem["open_qty"] == pytest.approx(0.39)
        assert rem["group_id"] == g
        assert rc["counts"]["phantom_opens_in_canonical_readers"] == 0
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# THE PM'S ACCEPTANCE MATRIX FOR THE STRICT RAIL (Astra, lane A): 10 positions
# ═════════════════════════════════════════════════════════════════════

def _ten(*, stale=0, packet_incomplete=0, protection_bad=0):
    good = {"state": "PROTECTED_RESTING"}
    pos = [_pos("p%d" % i, "g%d" % i, NOW - 3600) for i in range(10)]
    packets = {"g%d" % i: i >= packet_incomplete for i in range(10)}
    prot = {"p%d" % i: (good if i >= protection_bad else
                        {"state": "UNPROTECTED_NO_STANDING_ORDER"})
            for i in range(10)}
    return pos, packets, prot


def _mark_rows(stale):
    import tests.test_paper_mark_freshness_classifier as C
    fresh = dict(C.cls(last_ok=C.obs(1, 5)), strategy="A")
    old = dict(C.cls(last_ok=C.obs(1, 900)), strategy="A")
    return [old] * stale + [fresh] * (10 - stale)


def test_ten_positions_nine_healthy_one_stale_refuses_entry():
    s = PMF.summarize(_mark_rows(1))
    assert s["by_strategy"]["A"]["allocation_blocked"] is True


def test_ten_positions_nine_healthy_one_packet_incomplete_refuses_entry():
    pos, packets, prot = _ten(packet_incomplete=1)
    v = PMF.integrity_verdict(positions=pos, packets=packets,
                              protections=prot, now=NOW)
    assert v["refusal"] == PMF.R_PACKETS_BLOCK_ALLOCATION


def test_ten_positions_nine_healthy_one_protection_incomplete_refuses():
    pos, packets, prot = _ten(protection_bad=1)
    v = PMF.integrity_verdict(positions=pos, packets=packets,
                              protections=prot, now=NOW)
    assert v["refusal"] == PMF.R_PROTECTION_BLOCKS_ALLOCATION


def test_ten_of_ten_healthy_this_rail_alone_does_not_refuse():
    assert PMF.summarize(_mark_rows(0))["by_strategy"]["A"][
        "allocation_blocked"] is False
    pos, packets, prot = _ten()
    assert PMF.integrity_verdict(positions=pos, packets=packets,
                                 protections=prot, now=NOW)["refusal"] is None
