"""SMALL LIVE · PAPER vs ACTUAL, as management reads it (read-only view).

Pinned against Postgres (RN1X_TEST_DSN) with synthetic records written
straight into the real tables -- no venue, no network:

  * one row links paper decision -> paper order -> simulated fill -> mirror
    order -> venue fill, with the decision's economics, the SIMULATED and
    ACTUAL sections kept apart, the difference, and the Xavier / Audrey
    records (an ACTUAL-position Xavier record is "unavailable" until one
    exists);
  * an exclusion row carries its reason and no fill figures (null, not 0);
  * the view / status filters, with the status read from the side the view
    names;
  * empty data is null with a reason, never zero -- including the existing
    management view's P&L block;
  * KALSHI is listed as NOT_CONNECTED (credentials not configured);
  * the route carries the COMMAND read guard and refuses an anonymous caller.

All paper data is synthetic and written to fresh test accounts in the test
database; `paper_acct_main` is never read or written.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import os
import uuid

import pytest

from sportsassets import execmirror_view as V
from sportsassets.api import app as A

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
FP = "fp0123456789abcdef-small-live-test"
NOW = dt.datetime.now(dt.timezone.utc)


# ─────────────────────────── fixtures (synthetic) ───────────────────────────

async def _conn():
    import asyncpg
    return await asyncpg.connect(DSN)


async def _setup(conn, *, enabled=True, cutover=True):
    from tests.paper_harness import new_account
    acct = await new_account(conn, "slv")
    if await conn.fetchval("SELECT to_regclass('smalllive_reviews') IS NOT NULL"):
        await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                           "smalllive_reconciliations")
    await conn.execute("TRUNCATE execmirror_fills, execmirror_events, "
                       "execmirror_snapshots, execmirror_orders")
    await conn.execute(
        """UPDATE execmirror_control SET enabled = $1, stopped = false,
             stop_done_at = NULL, flatten_on_stop = false,
             cutover_at = CASE WHEN $2 THEN now() - interval '1 hour' END,
             account_fingerprint = $3, baseline = '{}'::jsonb,
             max_order_usd = 25, scale = 1000 WHERE id = 1""",
        enabled, cutover, FP)
    return acct


async def _decision(conn, acct, *, p=0.62, limit=0.55, qty=2702, vwap=0.548,
                    net_ev=171.4, gross=7.0, provider="pinnapi.com/raw-websocket"):
    did = "paper_d_%s" % uuid.uuid4().hex[:12]
    pd = {"strategy": "PINNACLE_COMPLETED_GAME_PAPER",
          "policy_version": "PINNACLE_COMPLETED_GAME_PAPER_V2",
          "gross_edge_pp": gross, "edge_at_vwap_pp": 7.2,
          "net_expected_profit_usd": net_ev, "fees_usd": 12.5, "p_pinnacle": p}
    eco = {"acquisition": {"vwap": vwap, "expected_net_profit_usd": net_ev,
                           "fees_usd": 12.5, "edge_at_vwap_pp": 7.2}}
    await conn.execute(
        """INSERT INTO paper_decisions (decision_id, session_id, account_id,
             decided_at, valuation_id, us_market_slug, holding_side, intent,
             label, verdict, internal_model, p_pinnacle, pinnacle, proposed_qty,
             limit_price, economics, qualification_gaps, policy_version,
             policy_decision, simulator_version, strategy)
           VALUES ($1,$2,$3,now(),$4,'mlb-slv','LONG','ORDER_INTENT_BUY_LONG',
                   '{"team":"Synthetic Nine"}'::jsonb,'ENTER','{}'::jsonb,$5,$6::jsonb,
                   $7,$8,$9::jsonb,'[]'::jsonb,'PINNACLE_COMPLETED_GAME_PAPER_V2',
                   $10::jsonb,'TEST','PINNACLE_COMPLETED_GAME_PAPER')""",
        did, acct["session_id"], acct["account_id"],
        int(uuid.uuid4().int % 10 ** 12), p,
        json.dumps({"provider": provider, "p": p}), qty, limit,
        json.dumps(eco), json.dumps(pd))
    return did


async def _paper_order(conn, acct, *, decision_id=None, qty=2702, wire=0.55,
                       state="FILLED", filled_qty=None, group=None,
                       strategy="PINNACLE_COMPLETED_GAME_PAPER",
                       intent="ORDER_INTENT_BUY_LONG"):
    oid = "paper_%s" % uuid.uuid4().hex[:12]
    group = group or "paper_group_%s" % uuid.uuid4().hex[:8]
    slug = "mlb-slv-%s" % group[-4:]
    fq = qty if (filled_qty is None and state == "FILLED") else (filled_qty or 0)
    await conn.execute(
        """INSERT INTO paper_orders (order_id, idempotency_key, account_id,
             session_id, group_id, role, direction, holding_side, intent,
             us_market_slug, order_type, time_in_force, allow_partial, qty,
             limit_price, wire_price, filled_qty, state, decision_id, decided_at,
             eligible_at, expires_at, simulator_version, strategy, label)
           VALUES ($1,$1,$2,$3,$4,'ENTRY','BUY','LONG',$5,$6,'MARKETABLE','IOC',
                   true,$7,$8,$8,$9,$10,$11,now(),now(),now() + interval '30 min',
                   'TEST',$12,'{"policy_version":"PINNACLE_COMPLETED_GAME_PAPER_V2"}'::jsonb)""",
        oid, acct["account_id"], acct["session_id"], group, intent, slug, qty,
        wire, fq, state, decision_id, strategy)
    return {"order_id": oid, "group_id": group, "slug": slug, "qty": qty, "wire": wire}


async def _paper_fill(conn, acct, po, *, qty, price=0.55, fee=1.0):
    fid = "paper_f_%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        """INSERT INTO paper_fills (fill_id, idempotency_key, order_id, account_id,
             session_id, group_id, role, direction, holding_side, us_market_slug,
             qty, price, wire_price, fee_usd, gross_usd, filled_at, basis,
             simulator_version)
           VALUES ($1,$1,$2,$3,$4,$5,'ENTRY','BUY','LONG',$6,$7,$8,$8,$9,$10,
                   now() - interval '2 seconds','DEPTH_WALK_WITHIN_LIMIT','TEST')""",
        fid, po["order_id"], acct["account_id"], acct["session_id"], po["group_id"],
        po["slug"], qty, price, fee, qty * price)
    return fid


async def _mirror(conn, po, *, state="FILLED", live_qty=3, exclusion=None,
                  venue_order_id=None, cum=0, avg=None, fees=0, latency=41,
                  detail=None, polled=True, created_offset_s=0):
    mid = "em:" + po["order_id"]
    det = dict(detail or {})
    if venue_order_id:
        det.setdefault("params", {"price": {"value": "%.4f" % po["wire"],
                                            "currency": "USD"},
                                  "quantity": live_qty})
        det.setdefault("decision_to_accept_ms", 812)
    await conn.execute(
        """INSERT INTO execmirror_orders (mirror_id, paper_order_id, group_id, role,
             strategy, us_market_slug, intent, order_type, tif, wire_price,
             paper_qty, scaled_qty, live_qty, rounding_delta, state, exclusion,
             venue_order_id, venue_state, paper_decided_at, submit_started_at,
             accepted_at, latency_ms, cum_qty, avg_px, fees_usd, last_polled_at,
             detail, created_at)
           VALUES ($1,$2,$3,'ENTRY','PINNACLE_COMPLETED_GAME_PAPER',$4,
                   'ORDER_INTENT_BUY_LONG','MARKETABLE','IOC',$5,$6,$7,$8,$9,$10,$11,
                   $12,$13,now(),$14,$15,$16,$17,$18,$19,$20,$21::jsonb,
                   now() + make_interval(secs => $22))""",
        mid, po["order_id"], po["group_id"], po["slug"], po["wire"], po["qty"],
        po["qty"] / 1000, live_qty, live_qty - po["qty"] / 1000, state, exclusion,
        venue_order_id,
        ("ORDER_STATE_FILLED" if venue_order_id and polled and state == "FILLED"
         else ("ORDER_STATE_CANCELED" if venue_order_id and polled else None)),
        NOW if venue_order_id else None, NOW if venue_order_id else None,
        latency if venue_order_id else None, cum, avg, fees,
        NOW if (venue_order_id and polled) else None, json.dumps(det),
        float(created_offset_s))
    return mid


async def _venue_fill(conn, mid, po, *, vid, qty, price, fee):
    await conn.execute(
        """INSERT INTO execmirror_fills (fill_key, mirror_id, venue_order_id,
             group_id, us_market_slug, intent, qty, price, fee_usd)
           VALUES ($1,$2,$3,$4,$5,'ORDER_INTENT_BUY_LONG',$6,$7,$8)""",
        "%s:%s" % (vid, qty), mid, vid, po["group_id"], po["slug"], qty, price, fee)


async def _xavier_and_audrey(conn, acct, po, fid, did):
    hid = "paper_h_%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        """INSERT INTO paper_handoffs (handoff_id, session_id, account_id, group_id,
             decision_id, entry_order_id, first_fill_id, first_fill_at,
             confirmed_qty, outstanding_qty)
           VALUES ($1,$2,$3,$4,$5,$6,$7,now(),$8,0)""",
        hid, acct["session_id"], acct["account_id"], po["group_id"], did,
        po["order_id"], fid, po["qty"])
    for i, rec in enumerate(("HOLD", "PASSIVE_EXIT")):
        await conn.execute(
            """INSERT INTO paper_xavier_reviews (review_id, session_id, account_id,
                 group_id, reviewed_at, trigger, recommendation, alternatives, exposure)
               VALUES ($1,$2,$3,$4,now() + make_interval(secs => $5),$6,$7,
                       '[]'::jsonb,'{}'::jsonb)""",
            "paper_xr_%s" % uuid.uuid4().hex[:10], acct["session_id"],
            acct["account_id"], po["group_id"], float(i),
            "FIRST_FILL" if i == 0 else "FILL_EVENT", rec)
    fnd = "paper_af_%s" % uuid.uuid4().hex[:10]
    await conn.execute(
        """INSERT INTO paper_audrey_findings (finding_id, session_id, account_id,
             found_at, kind, severity, subject, detail)
           VALUES ($1,$2,$3,now(),'FILL_PRICE_CHECK','INFO',$4,'{}'::jsonb)""",
        fnd, acct["session_id"], acct["account_id"], did)
    return hid, fnd


def _row(v, mid):
    return next(r for r in v["rows"] if r["mirror_id"] == mid)


# ─────────────────────────── the linked row ───────────────────────────

@pg
@pytest.mark.asyncio
async def test_a_row_links_decision_paper_fill_mirror_order_and_venue_fills():
    conn = await _conn()
    try:
        acct = await _setup(conn)
        did = await _decision(conn, acct)
        po = await _paper_order(conn, acct, decision_id=did, qty=2702)
        fid = await _paper_fill(conn, acct, po, qty=2702, price=0.55, fee=27.02)
        mid = await _mirror(conn, po, state="FILLED", live_qty=3, venue_order_id="v-77",
                            cum=3, avg=0.56, fees=0.06)
        await _venue_fill(conn, mid, po, vid="v-77", qty=3, price=0.56, fee=0.06)
        hid, fnd = await _xavier_and_audrey(conn, acct, po, fid, did)

        v = await V.small_live(conn)
        r = _row(v, mid)
        d, p, a, x, m = (r["decision"], r["paper"], r["actual"], r["difference"],
                         r["management"])
        # decision, from the paper decision record
        assert d["present"] and d["decision_id"] == did and d["verdict"] == "ENTER"
        assert d["strategy"] == "PINNACLE_COMPLETED_GAME_PAPER"
        assert d["policy_version"] == "PINNACLE_COMPLETED_GAME_PAPER_V2"
        assert d["p_pinnacle"] == pytest.approx(0.62)
        assert d["executable_price"] == pytest.approx(0.548)
        assert d["executable_price_source"] == "economics.acquisition.vwap"
        assert d["limit_price"] == pytest.approx(0.55)
        assert d["net_ev_usd"] == pytest.approx(171.4)
        assert d["net_ev_per_contract_usd"] == pytest.approx(171.4 / 2702, abs=1e-6)
        assert d["gross_edge_pp"] == pytest.approx(7.0)
        assert d["provider"] == "pinnapi.com/raw-websocket"
        assert d["valuation_id"] is not None
        # paper: SIMULATED
        assert p["label"] == "SIMULATED" and p["order_id"] == po["order_id"]
        assert p["qty"] == 2702 and p["wire_price"] == pytest.approx(0.55)
        assert p["state"] == "FILLED" and p["status"] == "filled"
        assert p["simulated_filled_qty"] == 2702 and p["simulated_fill_count"] == 1
        assert p["avg_simulated_fill_price"] == pytest.approx(0.55)
        assert p["simulated_fees_usd"] == pytest.approx(27.02)
        # the group is still open on paper: no realized P&L, said so (not 0)
        assert p["group_realized_pnl_usd"] is None and p["group_pnl_why_unavailable"]
        # actual: ACTUAL, venue figures only
        assert a["label"] == "ACTUAL" and a["venue"] == "POLYMARKET"
        assert a["scaled_qty"] == pytest.approx(2.702) and a["intended_qty"] == 3
        assert a["rounding_delta"] == pytest.approx(0.298)
        assert a["venue_order_id"] == "v-77" and a["submitted_price"] == pytest.approx(0.55)
        assert a["filled_qty"] == 3 and a["avg_fill_price"] == pytest.approx(0.56)
        assert a["fees_usd"] == pytest.approx(0.06) and a["venue_fill_count"] == 1
        assert a["intended_notional_usd"] == pytest.approx(1.65)
        assert a["filled_notional_usd"] == pytest.approx(1.68)
        assert a["submit_latency_ms"] == 41 and a["decision_to_accept_ms"] == 812
        assert a["acknowledged_at"] is not None and a["status"] == "filled"
        assert a["fill_source"] == "VENUE_ORDER_RECORD" and not a["excluded"]
        # difference
        assert x["submitted_minus_paper_wire_price"] == pytest.approx(0.0)
        assert x["live_minus_paper_fill_wire_price"] == pytest.approx(0.01)
        assert x["price_adverse_per_contract"] == pytest.approx(0.01)
        assert x["slippage_vs_submitted_adverse_per_contract"] == pytest.approx(0.01)
        assert x["expected_live_qty_from_paper_fill"] == pytest.approx(2.702)
        assert x["live_minus_expected_qty"] == pytest.approx(0.298)
        assert x["live_fees_minus_scaled_paper_fees_usd"] == pytest.approx(0.06 - 0.02702)
        assert x["fee_per_contract_live_minus_paper_usd"] == pytest.approx(0.02 - 0.01)
        assert x["rounded_qty"] == pytest.approx(0.298) and x["excluded_scaled_qty"] is None
        assert isinstance(x["paper_first_fill_to_live_ack_ms"], int)
        # management
        assert m["xavier_paper"]["handoff_id"] == hid
        assert m["xavier_paper"]["latest_recommendation"] == "PASSIVE_EXIT"
        assert m["xavier_actual"]["present"] is False
        assert m["xavier_actual"]["handoff_id"] is None
        assert m["xavier_actual"]["why_unavailable"].startswith("unavailable")
        assert [f["finding_id"] for f in m["audrey_findings"]] == [fnd]
        # summary
        assert v["empty_state"]["live_order_placed"] is True
        assert v["counts"]["live_orders_placed"] == 1
        assert v["control"]["state"] == "ENABLED"
        assert v["control"]["account_fingerprint_prefix"] == FP[:8]
        assert FP not in json.dumps(v, default=str)
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_actual_position_xavier_record_is_shown_when_one_exists():
    conn = await _conn()
    try:
        acct = await _setup(conn)
        po = await _paper_order(conn, acct, qty=3000)
        mid = await _mirror(conn, po, live_qty=3, venue_order_id="v-x1", cum=3,
                            avg=0.55, fees=0.03)
        if not await conn.fetchval("SELECT to_regclass('smalllive_handoffs') IS NOT NULL"
                                   " AND to_regclass('smalllive_reviews') IS NOT NULL"):
            # Without the small-live management schema (migration 197) the
            # view still answers, and says the actual record is unavailable.
            xa = _row(await V.small_live(conn), mid)["management"]["xavier_actual"]
            assert xa["present"] is False and xa["why_unavailable"].startswith("unavailable")
            return
        await conn.execute(
            """INSERT INTO smalllive_handoffs (handoff_id, venue, group_id,
                 us_market_slug, entry_mirror_id, live_held, live_bought,
                 avg_entry_px, first_live_fill_at)
               VALUES ('livehand:test', 'POLYMARKET', $1, $2, $3, 3, 3, 0.55, now())""",
            po["group_id"], po["slug"], mid)
        await conn.execute(
            """INSERT INTO smalllive_reviews (review_id, handoff_id, live_held,
                 committed_exit_qty, resting_protection_qty, filled_protection_qty,
                 quote, mark_value_usd, unrealized_usd, action)
               VALUES ('liverev:test', 'livehand:test', 3, 0, 0, 0, '{}'::jsonb,
                       1.71, 0.06, 'HOLD')""")
        xa = _row(await V.small_live(conn), mid)["management"]["xavier_actual"]
        assert xa["present"] and xa["handoff_id"] == "livehand:test"
        assert xa["latest_action"] == "HOLD" and xa["unrealized_usd"] == pytest.approx(0.06)
    finally:
        await conn.close()


# ─────────────────────────── exclusions ───────────────────────────

@pg
@pytest.mark.asyncio
async def test_an_exclusion_row_shows_the_reason_and_no_fill_figures():
    conn = await _conn()
    try:
        acct = await _setup(conn)
        po1 = await _paper_order(conn, acct, qty=2000, strategy="PINNACLE_EXPLORATION_PAPER")
        m1 = await _mirror(conn, po1, state="EXCLUDED", live_qty=0,
                           exclusion="STRATEGY_NOT_LIVE_ELIGIBLE",
                           detail={"live_eligibility": {"class": "EXPLORATION_RESEARCH_COST_PAPER_ONLY"}})
        po2 = await _paper_order(conn, acct, qty=60000, wire=0.60)
        m2 = await _mirror(conn, po2, state="EXCLUDED", live_qty=60,
                           exclusion="ABOVE_ORDER_CAP",
                           detail={"cost_usd": "36.00", "cap_usd": "25"})
        v = await V.small_live(conn)
        r1, r2 = _row(v, m1), _row(v, m2)
        for r in (r1, r2):
            a = r["actual"]
            assert a["excluded"] and a["status"] == "refused" and not a["submitted"]
            # nothing was sent: every venue figure is null, never 0
            for k in ("filled_qty", "avg_fill_price", "fees_usd", "venue_order_id",
                      "submitted_price", "acknowledged_at", "submit_latency_ms",
                      "filled_notional_usd", "intended_notional_usd", "venue_fill_count"):
                assert a[k] is None, (r["mirror_id"], k)
            assert a["why_no_fill_figures"].startswith("not sent: excluded")
        assert r1["actual"]["exclusion"] == "STRATEGY_NOT_LIVE_ELIGIBLE"
        assert r1["actual"]["exclusion_detail"]["live_eligibility"]["class"] == \
            "EXPLORATION_RESEARCH_COST_PAPER_ONLY"
        assert r1["strategy"]["kind"] == "TRAINING"
        assert r2["actual"]["exclusion"] == "ABOVE_ORDER_CAP"
        assert r2["actual"]["would_have_cost_usd"] == pytest.approx(36.0)
        assert r2["difference"]["excluded_scaled_qty"] == pytest.approx(60.0)
        # no decision record: null, with the reason, and the paper order's
        # own recorded decision instant shown with its source
        assert r2["decision"]["present"] is False and r2["decision"]["net_ev_usd"] is None
        assert r2["decision"]["decided_at_source"] == "paper_orders.decided_at"
        assert v["counts"]["excluded_by_reason"] == {"STRATEGY_NOT_LIVE_ELIGIBLE": 1,
                                                     "ABOVE_ORDER_CAP": 1}
        es = v["empty_state"]
        assert es["live_order_placed"] is False
        assert es["headline"] == "No live order has been placed yet."
        assert any("ABOVE_ORDER_CAP × 1" in s for s in es["reasons"])
        # the legacy view no longer shows the column default 0 for unsent rows
        lv = next(o for o in (await V.view(conn))["orders"] if o["mirror_id"] == m2)
        assert lv["live"]["filled_qty"] is None and lv["live"]["fees_usd"] is None
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_only_paper_only_strategies_since_cutover_is_said_plainly():
    conn = await _conn()
    try:
        acct = await _setup(conn)
        po = await _paper_order(conn, acct, strategy="PINNACLE_EXPLORATION_PAPER")
        await _mirror(conn, po, state="EXCLUDED", live_qty=0,
                      exclusion="STRATEGY_NOT_LIVE_ELIGIBLE")
        es = (await V.small_live(conn))["empty_state"]
        assert any(s.startswith("No qualifying investment-policy paper order since the cutover")
                   for s in es["reasons"]), es
    finally:
        await conn.close()


# ─────────────────────────── filters ───────────────────────────

@pg
@pytest.mark.asyncio
async def test_the_view_and_status_filters():
    conn = await _conn()
    try:
        acct = await _setup(conn)
        # filled live (paper filled), closed live (IOC, nothing traded; paper
        # still resting), open live, refused live (paper filled)
        pf = await _paper_order(conn, acct, qty=3000)
        m_f = await _mirror(conn, pf, venue_order_id="v-f", cum=3, avg=0.55, fees=0.03,
                            created_offset_s=1)
        pc = await _paper_order(conn, acct, qty=3000, state="RESTING")
        m_c = await _mirror(conn, pc, state="CANCELLED", venue_order_id="v-c",
                            created_offset_s=2)
        po = await _paper_order(conn, acct, qty=3000, state="PENDING_SIMULATION")
        m_o = await _mirror(conn, po, state="OPEN", venue_order_id="v-o", polled=False,
                            created_offset_s=3)
        pr = await _paper_order(conn, acct, qty=300)
        m_r = await _mirror(conn, pr, state="EXCLUDED", live_qty=0,
                            exclusion="BELOW_VENUE_MINIMUM", created_offset_s=4)

        async def ids(**k):
            return [r["mirror_id"] for r in (await V.small_live(conn, **k))["rows"]]

        assert await ids() == [m_r, m_o, m_c, m_f]           # newest first
        assert await ids(status="filled") == [m_f]
        assert await ids(status="closed") == [m_c]
        assert await ids(status="open") == [m_o]
        assert await ids(status="refused") == [m_r]
        assert await ids(view="polymarket", status="refused") == [m_r]
        # view=paper reads the PAPER side: two paper orders filled, one
        # resting, one pending
        assert set(await ids(view="paper", status="filled")) == {m_f, m_r}
        assert set(await ids(view="paper", status="open")) == {m_c, m_o}
        assert await ids(limit=2) == [m_r, m_o]
        # an accepted order whose venue record has not been read: unknown fill
        o = _row(await V.small_live(conn), m_o)["actual"]
        assert o["filled_qty"] is None and o["fees_usd"] is None
        assert o["why_no_fill_figures"].startswith("sent;")
        # a read venue record that traded nothing is a real 0
        c = _row(await V.small_live(conn), m_c)["actual"]
        assert c["filled_qty"] == 0 and c["status"] == "closed"
        k = await V.small_live(conn, view="kalshi")
        assert k["rows"] == [] and k["kalshi"]["status"] == "NOT_CONNECTED"
        assert k["filters"]["status_applies_to"] is None
        cnt = (await V.small_live(conn))["counts"]
        assert cnt["actual_status"] == {"open": 1, "filled": 1, "closed": 1, "refused": 1}
        assert cnt["paper_status"] == {"open": 2, "filled": 2, "closed": 0, "refused": 0}
        assert cnt["view"] == {"paper": 4, "polymarket": 4, "kalshi": None}
        with pytest.raises(ValueError):
            await V.small_live(conn, view="betfair")
        with pytest.raises(ValueError):
            await V.small_live(conn, status="won")
    finally:
        await conn.close()


# ─────────────────────────── null, not zero ───────────────────────────

@pg
@pytest.mark.asyncio
async def test_empty_data_is_null_with_a_reason_never_zero():
    conn = await _conn()
    try:
        await _setup(conn, enabled=False, cutover=False)
        v = await V.small_live(conn)
        assert v["rows"] == []
        acc = v["account"]
        assert acc["status"] == "UNAVAILABLE" and acc["why"]
        for k in ("at", "balances", "positions_count", "open_orders_count", "reconciled"):
            assert acc[k] is None, k
        assert v["control"]["state"] == "DISABLED"
        es = v["empty_state"]
        assert es["live_order_placed"] is False
        assert any("disabled" in s for s in es["reasons"])
        assert any("No cutover" in s for s in es["reasons"])
        # THE EXISTING MANAGEMENT VIEW: no records -> P&L null, not 0.0
        pnl = (await V.view(conn))["pnl"]
        assert pnl["status"] == "UNAVAILABLE" and pnl["why"]
        for k in ("paper_total", "expected_live_total", "live_total", "difference",
                  "tracking_ratio"):
            assert pnl[k] is None, k
        assert pnl["markets"] == []
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_account_snapshot_is_summarised_without_the_full_fingerprint():
    conn = await _conn()
    try:
        await _setup(conn)
        await conn.execute(
            """INSERT INTO execmirror_snapshots (account_fingerprint, balances,
                 positions, open_orders, reconciliation)
               VALUES ($1, '[{"currency":"USD","currentBalance":100,"buyingPower":97.5}]',
                       '[{"slug":"a"},{"slug":"b"}]', 1, '{"reconciled": true}')""", FP)
        v = await V.small_live(conn)
        acc = v["account"]
        assert acc["status"] == "AVAILABLE" and acc["positions_count"] == 2
        assert acc["open_orders_count"] == 1 and acc["buying_power_usd"] == 97.5
        assert acc["reconciled"] is True
        assert FP not in json.dumps(v, default=str)
    finally:
        await conn.close()


# ─────────────────────────── venues ───────────────────────────

def test_kalshi_is_listed_as_not_connected_with_nothing_filled_in():
    vs = V.venues({"enabled": True, "stopped": False, "scale": 1000,
                   "account_fingerprint": FP}, None)
    assert [v["venue"] for v in vs] == ["POLYMARKET", "KALSHI"]
    k = vs[1]
    assert k["status"] == "NOT_CONNECTED" and k["why"] == "credentials not configured"
    assert k["connected"] is False
    assert k["account_fingerprint_prefix"] is None and k["last_account_snapshot_at"] is None
    assert vs[0]["status"] == "MIRROR_ENABLED"
    assert V.venues({"enabled": True, "stopped": True}, None)[0]["status"] == "MIRROR_STOPPED"
    assert V.venues({}, None)[0]["status"] == "UNAVAILABLE"


def test_the_small_live_builder_holds_no_mutating_statement():
    src = "".join(inspect.getsource(f) for f in (
        V.small_live, V._management, V._counts, V._decision_section,
        V._paper_section, V._actual_section, V._difference))
    for word in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE", "ALTER "):
        assert word not in src.upper(), word


# ─────────────────────────── the route and its guard ───────────────────────────

class _Cfg:
    """Known credentials for the ASGI app under test; not real secrets."""
    admin_token = "admin-token-for-the-small-live-tests"
    desk_password = "desk-password-for-the-small-live-tests"


@pytest.fixture()
def client(monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    return TestClient(A.app, raise_server_exceptions=False)


def test_the_route_carries_the_command_read_guard():
    found = [r for r in A.app.routes if getattr(r, "path", "") == "/api/command/small-live"]
    assert len(found) == 1
    names = {getattr(d.call, "__name__", "") for d in found[0].dependant.dependencies}
    assert "require_command" in names
    assert found[0].methods <= {"GET", "HEAD"}


def test_an_anonymous_caller_gets_401(client):
    assert client.get("/api/command/small-live").status_code == 401
    assert client.get("/api/command/small-live?view=kalshi").status_code == 401


@pg
def test_the_route_answers_an_authorised_reader_and_refuses_bad_filters(client, monkeypatch):
    import asyncpg

    class _Acq:
        async def __aenter__(self):
            self.c = await asyncpg.connect(DSN)
            return self.c

        async def __aexit__(self, *a):
            await self.c.close()

    class _Pool:
        def acquire(self):
            return _Acq()

    async def _get():
        return _Pool()

    monkeypatch.setattr(A, "get_pool", _get)
    auth = {"X-Admin-Token": _Cfg.admin_token}
    r = client.get("/api/command/small-live?view=kalshi", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Small Live · Paper vs Actual"
    assert body["rows"] == [] and body["kalshi"]["status"] == "NOT_CONNECTED"
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/api/command/small-live?view=betfair", headers=auth).status_code == 422
    assert client.get("/api/command/small-live?status=won", headers=auth).status_code == 422
    assert client.get("/api/command/small-live?status=refused&limit=5",
                      headers=auth).status_code == 200


# ─────────────────────────── the page ───────────────────────────

def test_the_page_reads_the_route_and_labels_both_sides():
    import pathlib
    bundle = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "public" / "command"
    html = (bundle / "live.html").read_text()
    js = (bundle / "live.js").read_text()
    assert "<title>Small Live · Paper vs Actual" in html
    for ref in ('src="core.js"', 'src="unlock.js"', 'src="live.js"', 'href="desk.css"'):
        assert ref in html, ref
    assert html.index('src="core.js"') < html.index('src="live.js"')
    assert "'/api/command/small-live'" in js and "C.endpoint(PATH)" in js
    assert "credentials: 'same-origin'" in js and "no-store" in js
    for s in ("PAPER — SIMULATED", "ACTUAL — ", "'SIMULATED'", "'ACTUAL'",
              "unavailable", "No live order has been placed yet.",
              "Kalshi is not connected"):
        assert s in js, s
    for never in ("localStorage", "sessionStorage", "document.cookie", "http://", "https://"):
        assert never not in js, never
    # reachable from the existing navigation
    assert 'href="live.html"' in (bundle / "desk.html").read_text()
    assert 'href="live.html"' in (bundle / "index.html").read_text()
    assert 'href="live.html"' in (bundle / "center.js").read_text()
