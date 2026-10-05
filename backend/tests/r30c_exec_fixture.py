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
  the intent's components     the PRODUCTION component builders
                              (canonical_components.eddie_component /
                              derek_component), never hand-built shapes

PRODUCTION-SHAPED SIZING (R30C review): every ENTER here is sized WITHIN the
decision book's depth at its limit, as paper_benchmark.size_within_edge
sizes it -- a decision whose paper order fails does so because the book
MOVED before the simulator's fill, never because it was entered on a book
already beyond its limit (which the real sizer cannot produce).

`seed_history` writes, through the real writers, the recorded history the
production component path reads (Eddie's paper orders, fills and markout
books; settlements with their pre-map game starts; a capital snapshot), so
live_parity.canonical_decision can be driven end to end to a MEASURED V2.
"""
from __future__ import annotations

import json
import uuid

from sportsassets import allie_capital as AC
from sportsassets import bettor_paper_ledger as L
from sportsassets import canonical_components as CC
from sportsassets import canonical_intent as CI
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

#: (R30 tails integration) THE COMPLETED-GAME POLICY'S ACTIVE PARAMETER ROW,
#: as tests/test_live_parity.py states it. R30A made the SMALL LIVE adapter
#: refuse an intent with no policy block (LIVE_POLICY_ROW_MISSING_OR_
#: UNREADABLE, a VENUE_EXECUTION_DIFFERENCE); production's canonical_decision
#: always records one, so the fixture's intents carry it too.
PARAMS_V2 = {
    "policy_key": CG, "source": "ACTIVE_VERSION",
    "version_id": "paperparam:%s:V2" % CG, "version_no": 2,
    "values": {"min_gross_edge_pp": 0.5},
    "params_sha256": CI.params_row_sha({"min_gross_edge_pp": 0.5}),
    "version_source": "OWNER_DECISION",
    "approved_by": "OWNER (account holder): written paper-only authorization",
    "activation_id": "paperact:%s:V2:OWNER_DECISION" % CG,
    "activation_kind": "OWNER_DECISION", "fallback_reason": None}


async def _entry_adapters_approved(conn, intent, **kw):
    """THE SMALL LIVE SHADOW PROPOSAL FOR THE R30C READ MODELS. R30A's owner
    LIVE approvals: production approves no policy and no gate, so every
    SMALL LIVE record there is SHADOW_EXCLUDED with its governance reason
    and the LIVE_SHADOW class is empty (the read model counts it by reason).
    These proofs need a SHADOW_PROPOSED record to measure, so the approvals
    in force are THIS intent's policy and every gate -- for this one call,
    in this process only (live_parity.governance_in_force restored after).
    Nothing is written to live_approvals; SMALL LIVE stays SHADOW."""
    from sportsassets import live_approvals as LAP
    orig = LP.governance_in_force

    async def gin(_conn):
        return {"approved_policy_shas": frozenset(
                    {intent["policy"]["policy_sha"]}),
                "approved_gates": frozenset(LAP.GATES),
                "approvals_changed_at": None}
    LP.governance_in_force = gin
    try:
        return await LP.entry_adapters(conn, intent, **kw)
    finally:
        LP.governance_in_force = orig


def eddie_history(n: int = 40, *, fill_share: float = 0.9,
                  markout: float = 0.004, events: int | None = None) -> dict:
    """Eddie's recorded history through his own summariser
    (agents/eddie.summarise_history) from n synthetic terminal paper orders,
    fills and markouts of the taker style -- the shape history_stats
    returns, with the event key (fixture) of each order, two orders per
    event by default."""
    events = events or max(1, n // 2)
    filled = int(round(n * fill_share))
    orders = ([{"order_type": "MARKETABLE", "state": "FILLED",
                "submit_s": 1.0, "cluster": "fx-%d" % (i % events)}
               for i in range(filled)]
              + [{"order_type": "MARKETABLE", "state": "EXPIRED",
                  "submit_s": 1.0, "cluster": "fx-%d" % (i % events)}
                 for i in range(filled, n)])
    fills = [{"order_type": "MARKETABLE", "ttf_s": 2.0 + (i % 3)}
             for i in range(filled)]
    marks = [{"order_type": "MARKETABLE",
              "markout_pp": markout + (0.002 if i % 2 else -0.002),
              "cluster": "fx-%d" % (i % events)}
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
    transaction only).

    (R30 tails integration) MIGRATION 225'S PER-RELEASE SHAPE: one row per
    release whose recorded_at the database clock stamps
    (live_parity_cutover_stamp_trg), read through the view
    live_parity_effective_cutover. A backdated cutover is written the way
    tests/test_confidence_ladder writes its own: the stamp trigger disabled
    for THIS transaction only (the DDL rolls back with everything else), a
    named human and THIS build's decision-logic hash."""
    from sportsassets import decision_logic as DL
    iid = await conn.fetchval(
        "INSERT INTO live_parity_hook_installs (process, commit_sha, hooks)"
        " VALUES ('api', $1, $2) RETURNING install_id", SHA,
        list(LP.HOOK_NAMES))
    await conn.execute("ALTER TABLE live_parity_cutover DISABLE "
                       "TRIGGER live_parity_cutover_stamp_trg")
    await conn.execute(
        "INSERT INTO live_parity_cutover (recorded_at, release_sha, api_sha,"
        " workers_sha, migrations, decision_logic_hash, decision_logic_files,"
        " hook_install_id, small_live_mode, small_live_halted,"
        " capital_activated, evidence, recorded_by)"
        " VALUES (now() - interval '1 minute', $1, $1, $1,"
        " ARRAY['225','226'], $3, '{}'::jsonb, $2, 'SHADOW', false, false,"
        " '{}', 'release engineer')",
        SHA, iid, DL.decision_logic_hash()["hash"])
    await conn.execute("ALTER TABLE live_parity_cutover ENABLE "
                       "TRIGGER live_parity_cutover_stamp_trg")


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
        opportunity_score=v1c,
        derek=CC.derek_component(
            verdict="ENTER", policy_version=CG3,
            policy_decision={"selection_reason": "test fixture"},
            refusals=[], economics={"expected_net_profit_usd": 40.0},
            gross_edge_pp=round((p - limit) * 100, 6), probability=p),
        karen={"state": "NO_OPEN_CHALLENGE_ON_THIS_MARKET_OR_STRATEGY",
               "open_on_market": 0, "open_on_strategy": 0, "open_total": 0,
               "authority": "CHALLENGE_ONLY_ZERO_AUTHORITY"},
        allie=allie, eddie=CC.eddie_component(est),
        us_market_slug=slug,
        contract={"us_market_slug": slug, "event_key": event,
                  "sport_family": sport},
        holding_side="LONG", order_intent="ORDER_INTENT_BUY_LONG",
        order_type="MARKETABLE", time_in_force="IOC", limit_price=limit,
        wire_price=limit, target_qty=qty, sizing_basis={"rule": "test"},
        created_at=T,
        policy=CI.policy_block(strategy=CG, strategy_version=CG3,
                               params=PARAMS_V2),
        # (R30 tails integration) R30A derives the intent's expiry window
        # from its probability and book blocks; without them the SMALL LIVE
        # adapter refuses CANONICAL_INTENT_EXPIRY_UNAVAILABLE. Production's
        # canonical_decision records both; stated here as test_live_parity
        # states them, on THIS decision's recorded book.
        probability={"status": "MEASURED", "value": p,
                     "source": "pinnapi.com/raw-websocket",
                     "source_version": "PINNACLE_DEVIG_V1",
                     "observed_at": T - 5.0, "received_at": T - 4.5,
                     "age_at_decision_s": 5.0, "limit_s": 30.0},
        book={"status": "MEASURED", "obs_id": obs0, "observed_at": T - 1.0,
              "age_at_decision_s": 1.0, "max_age_s": 10.0})
    assert await LP.record_decision_intent(conn, intent)
    order = H.order(acct, key=tag, slug=slug, qty=qty, limit=limit, at=T,
                    group_id="paper_group_x_%s" % tag, fixture=event)
    order.update(LP.paper_entry_fields(intent))
    # the production entry key (agents/paper_benchmark: "<decision>:ENTRY"),
    # which the exceptional-settlement read joins on
    order["idempotency_key"] = "%s:ENTRY" % did
    got = await L.submit_order(conn, order, fee_fn=FEE, now=T)
    assert got["ok"], got
    par = await _entry_adapters_approved(conn, intent, paper_order=order,
                                         paper_result=got, now=T + 0.5)
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


async def seed_history(conn, acct, *, T: float, n: int = 26,
                       expire: int = 4, events: int = 13,
                       markout_drop: float = 0.02,
                       idle_capital_usd: float = 400_000.0) -> dict:
    """THE RECORDED HISTORY THE PRODUCTION COMPONENT PATH READS, through the
    real writers, all before T: n taker paper orders (each on its own
    market, two per fixture) submitted and simulated -- n - expire FILL on
    their book, `expire` find the book beyond their limit and expire --
    each fill followed by a book 60 s later whose mid fell `markout_drop`
    (Eddie's markout), the filled groups settled WON / LOST with a pre-map
    game start 2 h before settlement (the settlement lags), and a PAPER
    capital snapshot (idle capital)."""
    tag = uuid.uuid4().hex[:8]
    slugs = []
    for i in range(n):
        t0 = T - 3000.0 + i * 70.0
        slug = "test-hist-%s-%d" % (tag, i)
        fx = "fx-hist-%s-%d" % (tag, i % events)
        offers = [(0.50, 3000)] if i >= expire else [(0.60, 3000)]
        await H.observe(conn, slug, t0 - 1, bids=[(0.48, 3000)],
                        offers=offers)
        order = H.order(acct, key="h%s%d" % (tag, i), slug=slug, qty=100,
                        limit=0.52, at=t0, fixture=fx,
                        group_id="paper_group_h_%s_%d" % (tag, i))
        got = await L.submit_order(conn, order, fee_fn=FEE, now=t0)
        assert got["ok"], got
        await H.observe(conn, slug, t0 + 3, bids=[(0.48, 3000)],
                        offers=offers)
        r = await SIM.simulate_order(conn, got["order"]["order_id"],
                                     now=t0 + 4, fee_fn=FEE)
        if i >= expire:
            assert r["state"] == "FILLED", r
            await H.observe(conn, slug, t0 + 64,
                            bids=[(0.48 - markout_drop, 3000)],
                            offers=[(0.50 - markout_drop, 3000)])
            slugs.append((slug, order))
        else:
            assert r["state"] in ("EXPIRED", "CANCELED"), r
    for k, (slug, order) in enumerate(slugs):
        settled = T - 600.0
        await conn.execute(
            "INSERT INTO us_premap (identifier, market_slug, game_start) "
            "VALUES ($1, $2, to_timestamp($3))", "test-%s" % slug, slug,
            settled - 7200.0)
        got = await L.settle(
            conn, account_id=acct["account_id"], group_id=order["group_id"],
            slug=slug, holding_side="LONG", settlement_event_key="hist",
            outcome="WON" if k % 2 else "LOST",
            evidence={"source": "TEST_FIXTURE_SYNTHETIC"},
            evidence_source="TEST_FIXTURE", at=settled)
        assert got["ok"], got
    await conn.execute(
        "INSERT INTO pos_snapshots (snapshot_id, run_id, component, book, "
        " computed_at, payload, data_sha256, version) VALUES ($1, $1, "
        " 'CAPITAL', 'PAPER', to_timestamp($2), $3::jsonb, $4, 'TEST')",
        "snap_%s" % tag, T - 60.0,
        json.dumps({"idle_capital_usd": idle_capital_usd}), "0" * 64)
    return {"filled": len(slugs), "expired": expire, "events": events}


async def bare_intent(conn, *, T: float, slug: str, event: str,
                      p: float = 0.62, qty: float = 2000,
                      limit: float = 0.52, strategy: str = CG,
                      version: str = CG3, sport: str = "baseball") -> dict:
    """ONE recorded canonical decision intent (live_parity.build_decision_
    intent + record_decision_intent, production component builders) with
    no paper order and no tournament entry -- the row the database's
    tournament guard checks a raw INSERT against."""
    did = "papercg:%s" % uuid.uuid4().hex[:10]
    intent = LP.build_decision_intent(
        decision_id=did, strategy=strategy, strategy_version=version,
        evidence={"valuation_id": None, "book_obs_id": None,
                  "probability": p},
        opportunity_score={"status": "UNAVAILABLE",
                           "why": "TEST_BARE_INTENT"},
        derek=CC.derek_component(
            verdict="ENTER", policy_version=version, policy_decision={},
            refusals=[], economics={}, gross_edge_pp=10.0, probability=p),
        karen={"state": "NO_OPEN_CHALLENGE_ON_THIS_MARKET_OR_STRATEGY"},
        allie={"status": "UNAVAILABLE", "why": "TEST_BARE_INTENT"},
        eddie=CC.eddie_component(None, "TEST_BARE_INTENT"),
        us_market_slug=slug,
        contract={"us_market_slug": slug, "event_key": event,
                  "sport_family": sport},
        holding_side="LONG", order_intent="ORDER_INTENT_BUY_LONG",
        order_type="MARKETABLE", time_in_force="IOC", limit_price=limit,
        wire_price=limit, target_qty=qty, sizing_basis={"rule": "test"},
        created_at=T)
    assert await LP.record_decision_intent(conn, intent)
    return intent


async def settled_entry(conn, acct, intent: dict, *, T: float,
                        outcome: str, **settle_kw) -> dict:
    """The intent's paper ENTRY order under the production key
    ("<decision>:ENTRY"), filled by the simulator and settled through the
    ledger with `outcome`."""
    tag = uuid.uuid4().hex[:8]
    slug = intent["us_market_slug"]
    order = H.order(acct, key=tag, slug=slug, qty=100, limit=0.52, at=T,
                    group_id="paper_group_s_%s" % tag)
    order.update(LP.paper_entry_fields(intent))
    order.update(qty=100, idempotency_key="%s:ENTRY" % intent["decision_id"])
    await H.observe(conn, slug, T - 1, bids=[(0.48, 3000)],
                    offers=[(0.50, 3000)])
    got = await L.submit_order(conn, order, fee_fn=FEE, now=T)
    assert got["ok"], got
    await H.observe(conn, slug, T + 3, bids=[(0.48, 3000)],
                    offers=[(0.50, 3000)])
    r = await SIM.simulate_order(conn, got["order"]["order_id"], now=T + 4,
                                 fee_fn=FEE)
    assert r["state"] == "FILLED", r
    st = await L.settle(
        conn, account_id=acct["account_id"], group_id=order["group_id"],
        slug=slug, holding_side="LONG", settlement_event_key="exc-" + tag,
        outcome=outcome, evidence={"source": "TEST_FIXTURE_SYNTHETIC"},
        evidence_source="TEST_FIXTURE", at=T + 60, **settle_kw)
    assert st["ok"], st
    return st
