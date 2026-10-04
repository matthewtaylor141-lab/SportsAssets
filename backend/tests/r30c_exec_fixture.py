"""R30C fixture: production-shaped canonical decisions through the REAL
writers. ALL DATA SYNTHETIC, written into a scratch test database inside the
caller's transaction (rolled back by the caller); nothing touches the live
paper account and nothing reaches a venue.

  the canonical intent        live_parity.build_decision_intent +
                              record_decision_intent (migration 225)
  the PAPER adapter's order   bettor_paper_ledger.submit_order with the
                              order fields READ FROM THE INTENT
                              (live_parity.paper_entry_fields), filled by the
                              real simulator (bettor_paper_simulator.
                              simulate_order) on recorded books
  the SMALL LIVE adapter      live_parity.entry_adapters (SHADOW proposal +
                              the parity row)
  the settlement              bettor_paper_ledger.settle
  the tournament entry        opportunity_tournament.record_entry with V1
                              from lost_opportunity.score and V2 from
                              opportunity_score_v2 over Eddie's real
                              estimate (agents/eddie.estimate)
"""
from __future__ import annotations

import json
import uuid

from sportsassets import allie_capital as AC
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import live_parity as LP
from sportsassets import opportunity_score_v2 as V2
from sportsassets import opportunity_tournament as OT
from sportsassets.agents import eddie as E
from sportsassets.lost_opportunity import score as SC

from tests import paper_harness as H

CG = "PINNACLE_COMPLETED_GAME_PAPER"
CG3 = "PINNACLE_COMPLETED_GAME_PAPER_V3"
SHA = "b" * 40
FEE = H.flat_fee(0.005)


def eddie_history(n: int = 40, *, fill_share: float = 0.9,
                  markout: float = 0.004) -> dict:
    """Eddie's recorded history through his own summariser
    (agents/eddie.summarise_history) from n synthetic terminal paper orders,
    fills and markouts of the taker style -- the shape history_stats
    returns."""
    filled = int(round(n * fill_share))
    orders = ([{"order_type": "MARKETABLE", "state": "FILLED",
                "submit_s": 1.0}] * filled
              + [{"order_type": "MARKETABLE", "state": "EXPIRED",
                  "submit_s": 1.0}] * (n - filled))
    fills = [{"order_type": "MARKETABLE", "ttf_s": 2.0 + (i % 3)}
             for i in range(filled)]
    marks = [{"order_type": "MARKETABLE",
              "markout_pp": markout + (0.002 if i % 2 else -0.002)}
             for i in range(n)]
    return E.summarise_history(orders, fills, marks)


async def prepare(conn) -> None:
    """The live-side state the SMALL LIVE adapter reads (inside the caller's
    transaction): SMALL LIVE not halted, the execution mirror's control row,
    a fresh retail balance snapshot."""
    await conn.execute(
        "UPDATE small_live_control SET halted = false, cleared_by = "
        "'test harness (human operator)' WHERE id = 1 AND halted")
    if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
        await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
    await conn.execute(
        "INSERT INTO execmirror_snapshots (at, balances, positions, "
        " open_orders) VALUES (now(), $1::jsonb, '[]', 0)",
        json.dumps([{"currency": "USD", "buyingPower": 500}]))


async def record_cutover(conn) -> None:
    """A recorded R30 production cutover one minute ago (caller's
    transaction only: the singleton is append-only)."""
    iid = await conn.fetchval(
        "INSERT INTO live_parity_hook_installs (process, commit_sha, hooks)"
        " VALUES ('api', $1, $2) RETURNING install_id", SHA,
        list(LP.HOOK_NAMES))
    await conn.execute(
        "INSERT INTO live_parity_cutover (cutover_at, release_sha, api_sha,"
        " workers_sha, migrations, hook_install_id, small_live_mode,"
        " small_live_halted, capital_activated, evidence, recorded_by)"
        " VALUES (now() - interval '1 minute', $1, $1, $1,"
        " ARRAY['225','226'], $2, 'SHADOW', false, false, '{}', 'test')",
        SHA, iid)


async def decision(conn, acct, *, T: float, slug: str, event: str,
                   offers, bids, qty: float = 2000, limit: float = 0.52,
                   p: float = 0.62, sport: str = "baseball") -> dict:
    """ONE canonical ENTER decision at T on the book observed at T-1, its
    PAPER order (fields from the intent) and both adapters' records."""
    tag = uuid.uuid4().hex[:10]
    obs0 = await H.observe(conn, slug, T - 1, bids=bids, offers=offers)
    book_row = await conn.fetchrow(
        "SELECT obs_id, extract(epoch FROM observed_at)::float8 AS "
        " observed_at, bids, offers, tick, market_state FROM "
        " paper_book_observations WHERE obs_id = $1", obs0)
    did = "papercg:%s" % tag
    cand = {"decision_id": did, "decided_at": T, "us_market_slug": slug,
            "holding_side": "LONG", "proposed_qty": qty,
            "limit_price": limit, "p_pinnacle": p,
            "economics": {"acquisition": {"fees_usd": qty * 0.005}}}
    est = E.estimate(cand, dict(book_row), eddie_history(), now=T)
    allie = AC.allocate(
        eddie_ev_usd=est.get("expected_executable_ev_usd"),
        modelled_net_usd=40.0, capital_required_usd=qty * limit,
        event_start_at=T + 3600, decided_at=T, median_lag_s=3600.0,
        lag_n=40, eddie_max_qty=est.get("max_executable_qty"),
        limit_price=limit, displayed_depth_qty=qty, fixture_open_groups=0,
        fixture_open_usd=0.0, book_open_usd=0.0, idle_capital_usd=400_000.0,
        recent_adjusted_ppch=[], paper_rail_usd=5000.0, live_rail_usd=25.0,
        live_scale=1000.0, order_cost_usd=qty * limit)
    v1 = SC.score(
        dict(cand, status="MEASURED", strategy=CG, verdict="ENTER",
             executable_opportunity_dollars=40.0,
             executable_capacity_usd=qty * limit, event_start_at=T + 3600),
        fill_probability=est.get("expected_fill_probability"),
        fill_basis="EDDIE_ESTIMATE_AT_DECISION", fill_source="EDDIE",
        idle_capital_usd=400_000.0,
        lag_samples=[(T - 100 - i, 3600.0) for i in range(10)])
    v1c = {"status": v1.get("status"), "version": SC.VERSION,
           "opportunity_score": v1.get("opportunity_score"),
           "score_basis": v1.get("score_basis"), "why": v1.get("why"),
           "expected_net_executable_ev_usd": v1.get(
               "expected_net_executable_ev_usd"),
           "fill_probability": v1.get("fill_probability"),
           "capacity_factor": v1.get("capacity_factor"),
           "capital_hours": v1.get("capital_hours")}
    v2 = V2.score(V2.inputs_from(est, allie, V2.exceptional_from_counts(
        scope_counts={sport: {"k": 0, "n": 60}}, scope_key=sport,
        pooled={"k": 1, "n": 200})))
    intent = LP.build_decision_intent(
        decision_id=did, strategy=CG, strategy_version=CG3,
        evidence={"valuation_id": None, "book_obs_id": obs0,
                  "probability": p},
        opportunity_score=v1c, derek={"verdict": "ENTER",
                                      "status": "MEASURED"},
        karen={"state": "NO_OPEN_CHALLENGE_ON_THIS_MARKET_OR_STRATEGY"},
        allie=allie, eddie={"status": "MEASURED",
                            "execution_evidence": est["execution_evidence"]},
        us_market_slug=slug,
        contract={"us_market_slug": slug, "event_key": event,
                  "sport_family": sport},
        holding_side="LONG", order_intent="ORDER_INTENT_BUY_LONG",
        order_type="MARKETABLE", time_in_force="IOC", limit_price=limit,
        wire_price=limit, target_qty=qty, sizing_basis={"rule": "test"},
        created_at=T)
    assert await LP.record_decision_intent(conn, intent)
    order = H.order(acct, key=tag, slug=slug, qty=qty, limit=limit, at=T,
                    group_id="paper_group_x_%s" % tag, fixture=event)
    order.update(LP.paper_entry_fields(intent))
    got = await L.submit_order(conn, order, fee_fn=FEE, now=T)
    assert got["ok"], got
    par = await LP.entry_adapters(conn, intent, paper_order=order,
                                  paper_result=got)
    assert par.get("parity") in (LP.MATCHED, LP.SCALE), par
    recorded = await OT.record_entry(conn, intent=intent, v1=v1c, v2=v2)
    return {"intent": intent, "order": order,
            "order_id": got["order"]["order_id"], "obs0": obs0, "est": est,
            "v1": v1c, "v2": v2, "tournament_recorded": recorded,
            "group_id": order["group_id"]}


async def fill(conn, d: dict, *, at: float, offers, bids) -> dict:
    """The simulator fills the PAPER order on a book observed at at-1."""
    await H.observe(conn, d["order"]["us_market_slug"], at - 1, bids=bids,
                    offers=offers)
    return await SIM.simulate_order(conn, d["order_id"], now=at, fee_fn=FEE)


async def settle(conn, acct, d: dict, *, outcome: str, at: float) -> dict:
    return await L.settle(
        conn, account_id=acct["account_id"], group_id=d["group_id"],
        slug=d["order"]["us_market_slug"], holding_side="LONG",
        settlement_event_key="test-settle", outcome=outcome,
        evidence={"source": "TEST_FIXTURE_SYNTHETIC"},
        evidence_source="TEST_FIXTURE", at=at)
