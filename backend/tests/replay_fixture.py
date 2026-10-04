"""Synthetic rows for the historical-replay proofs (tests/test_r30_replay.py).

ALL DATA HERE IS SYNTHETIC TEST DATA written into a scratch test database
inside a transaction the test rolls back. Unlike the other fixtures, every
helper sets the row's RECORDED stamp explicitly (recorded_at / created_at /
decided_at / updated_at), because the replay's whole contract is about WHEN a
row became durable. The paper ledger stamps itself with clock_timestamp()
(migration 171's trigger), so a scenario's base clock B is placed after the
account's INITIAL_FUNDING row and the run is given a `now` after B.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from sportsassets import canonical_intent as CI


def _harness():
    """tests/paper_harness, imported only when an account is built through
    the real session code (it loads the session's whole default-config
    import graph; the runtime-footprint test builds its account raw)."""
    try:
        from tests import paper_harness as H
    except ImportError:                                         # pragma: no cover
        import paper_harness as H
    return H

SIM = "PAPER_SIM_V1"
INV = "PINNACLE_COMPLETED_GAME_PAPER"          # INVESTMENT
TRN = "PINNACLE_EXPLORATION_PAPER"             # TRAINING
LONG = "ORDER_INTENT_BUY_LONG"


def uid(tag="x") -> str:
    return "%s%s" % (tag, uuid.uuid4().hex[:10])


def ts(e):
    import datetime as dt
    return dt.datetime.fromtimestamp(float(e), dt.timezone.utc)


#: the session caps the raw account carries (bettor_paper_session's code
#: defaults, restated so the raw path imports no session module)
RAW_CONFIG = {"risk": {"per_order_cap_usd": 5000.0,
                       "per_market_cap_usd": 10000.0,
                       "per_fixture_cap_usd": 15000.0,
                       "hedge_reserve_fraction": 0.20,
                       "max_concurrent_groups": 150},
              "entry": {"order_type": "MARKETABLE", "time_in_force": "IOC"},
              "simulator_version": SIM}


async def account(conn, tag="rpl", *, raw=False):
    """A fresh paper account. Returns (acct, B): B is a base clock after the
    account's INITIAL_FUNDING ledger row (which the ledger stamps itself).
    raw=True funds it through the ledger's own initializer and writes its
    session row directly (no session-module import graph)."""
    w = time.time()
    if not raw:
        acct = await _harness().new_account(conn, tag, now=w - 7200.0)
        return acct, w + 100.0
    from sportsassets import bettor_paper_ledger as L
    aid = "paper_test_%s_%s" % (tag, uuid.uuid4().hex[:10])
    got = await L.ensure_account(conn, account_id=aid,
                                 account_key=aid.upper())
    assert got.get("ok"), got
    sid = "paper_session_%s_raw" % hashlib.sha256(aid.encode()).hexdigest()[:10]
    cfg = dict(RAW_CONFIG, account_id=aid)
    await conn.execute(
        "INSERT INTO paper_sessions (session_id, account_id, started_at, "
        " config, config_sha, simulator_version, reporting_tz) VALUES "
        " ($1,$2,$3,$4::jsonb,$5,$6,'UTC')", sid, aid, ts(w - 7200.0),
        json.dumps(cfg), hashlib.sha256(json.dumps(
            cfg, sort_keys=True).encode()).hexdigest(), SIM)
    return {"account_id": aid, "session_id": sid, "config": cfg}, w + 100.0


async def premap(conn, slug, *, game_start, updated_at):
    await conn.execute(
        "INSERT INTO us_premap (identifier, event_slug, market_slug, "
        " game_start, updated_at, team_league) VALUES ($1,$2,$3,$4,$5,'MLB')",
        uid("pm-"), "ev-" + slug, slug, ts(game_start), ts(updated_at))


async def valuation(conn, *, slug, p, recorded_at, observed_at, received_at):
    return await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, condition_id, "
        " us_market_slug, contract_selection, sport_family, market, period, "
        " raw_odds, outcomes_priced, expected_outcomes, observed_at, "
        " received_at, age_s, probability, decision, admissible, refusals, "
        " why, payout_event, payout_is_complement, buy_intent, "
        " record_purpose, decided_at, event_key, mapped_outcome) VALUES "
        " ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION','test',"
        " 'pinnacle','power','PMUS',$2,$2,'HOME','baseball','h2h',"
        " 'FULL_GAME','{}'::jsonb,2,2,$3,$4,$5,$6,'NO_TRADE',false,"
        " '{}'::text[],'synthetic','HOME',false,$7,'ENTRY_DECISION',$8,$9,"
        " 'HOME') RETURNING id",
        uid("rpl-exp-"), slug, ts(observed_at), ts(received_at),
        float(received_at - observed_at), float(p), LONG, ts(recorded_at),
        "e-" + slug)


def md(*, bids=(), offers=()) -> dict:
    """paper_harness.md's venue-shaped levels (restated: no harness import)."""
    return {"bids": [{"px": {"value": "%.2f" % p}, "qty": "%s" % q}
                     for p, q in bids],
            "offers": [{"px": {"value": "%.2f" % p}, "qty": "%s" % q}
                       for p, q in offers]}


async def book(conn, slug, *, observed_at, recorded_at, bids=(), offers=()):
    md_ = md(bids=bids, offers=offers)
    return await conn.fetchval(
        "INSERT INTO paper_book_observations (us_market_slug, observed_at, "
        " source, bids, offers, read_basis, recorded_at) VALUES ($1,$2,"
        " 'TEST_FIXTURE_SYNTHETIC_BOOK',$3::jsonb,$4::jsonb,'TEST',$5) "
        "RETURNING obs_id", slug, ts(observed_at), json.dumps(md_["bids"]),
        json.dumps(md_["offers"]), ts(recorded_at))


def economics(*, qty, vwap, fees, net, depth=None, edge_pp=None):
    return {"acquisition": {"qty": qty, "acquisition_cost_usd": qty * vwap,
                            "vwap": vwap, "fees_usd": fees,
                            "expected_net_profit_usd": net},
            "depth_within_limit": depth, "best_level_edge_pp": edge_pp}


async def decision(conn, acct, *, slug, at, recorded_at, verdict="ENTER",
                   strategy=INV, p=0.62, limit=0.56, qty=100.0, econ=None,
                   valuation_id=None, book_obs_id=None, refusal=None,
                   refusals=(), pinnacle=None, fixture=None, side="LONG"):
    did = "paperdec:" + uid()
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, intent, "
        " fixture, label, verdict, refusal, refusals, p_pinnacle, "
        " internal_model, pinnacle, book_obs_id, proposed_qty, limit_price, "
        " economics, qualification_gaps, policy_version, simulator_version, "
        " strategy, recorded_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,"
        " '{}'::jsonb,$10,$11,$12::text[],$13,'{}'::jsonb,$14::jsonb,$15,"
        " $16,$17,$18::jsonb,'[]'::jsonb,$19,$20,$21,$22)",
        did, acct["session_id"], acct["account_id"], ts(at), valuation_id,
        slug, side, LONG if side == "LONG" else "ORDER_INTENT_BUY_SHORT",
        fixture or ("fx-" + slug), verdict,
        None if verdict == "ENTER" else (refusal or "TEST_REFUSAL"),
        list(refusals or ([refusal] if refusal else [])), p,
        json.dumps(pinnacle or {}), book_obs_id, qty, limit,
        json.dumps(econ or {}), strategy + "_TEST_V1", SIM, strategy,
        ts(recorded_at))
    return did


async def order(conn, acct, *, decision_id, group_id, slug, qty, limit, at,
                strategy=INV, role="ENTRY", direction="BUY", side="LONG",
                filled_qty=None, state="FILLED", terminal_at=None,
                fixture=None, updated_at=None, queue_ahead_qty=None):
    """An order row. `updated_at` is its last durable write (the terminal
    UPDATE in production); by default the insert's now(), i.e. before B."""
    oid = "paperord:" + uid()
    intent = ("ORDER_INTENT_%s_%s" % (direction, side))
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, fixture, label, order_type, time_in_force, "
        " allow_partial, qty, limit_price, wire_price, reserved_usd, "
        " filled_qty, state, decision_id, decided_at, eligible_at, "
        " expires_at, simulator_version, strategy, terminal_at, created_at, "
        " updated_at, queue_ahead_qty) "
        " VALUES ($1,$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'{}'::jsonb,'MARKETABLE',"
        " 'IOC',true,$11,$12,$12,$13,$14,$15,$16,$17,$17,$18,$19,$20,$21,$17,"
        " coalesce($22, now()),$23)",
        oid, acct["account_id"], acct["session_id"], group_id, role,
        direction, side, intent, slug, fixture or ("fx-" + slug), qty, limit,
        round(qty * limit, 6) if direction == "BUY" else 0.0,
        qty if filled_qty is None else filled_qty,
        state, decision_id, ts(at), ts(at + 90), SIM, strategy,
        None if terminal_at is None else ts(terminal_at),
        None if updated_at is None else ts(updated_at), queue_ahead_qty)
    return oid


async def order_event(conn, *, order_id, kind, at, recorded_at, detail=None):
    await conn.execute(
        "INSERT INTO paper_order_events (order_id, kind, event_source, "
        " simulator_version, at, detail, recorded_at) VALUES ($1,$2,"
        " 'SIMULATOR',$3,$4,$5::jsonb,$6)", order_id, kind, SIM, ts(at),
        json.dumps(detail or {}), ts(recorded_at))


async def karen_event(conn, *, challenge_id, kind, at, recorded_at):
    await conn.execute(
        "INSERT INTO karen_challenge_events (challenge_id, at, kind, actor, "
        " detail, recorded_at) VALUES ($1,$2,$3,'DEREK','{}'::jsonb,$4)",
        challenge_id, ts(at), kind, ts(recorded_at))


async def fill(conn, acct, *, order_id, group_id, slug, qty, price, fee, at,
               recorded_at, role="ENTRY", direction="BUY", side="LONG",
               strategy=INV, fixture=None):
    fid = "paperfill:" + uid()
    await conn.execute(
        "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
        " account_id, session_id, group_id, role, direction, holding_side, "
        " us_market_slug, fixture, label, qty, price, wire_price, fee_usd, "
        " gross_usd, filled_at, basis, simulator_version, strategy, "
        " recorded_at) VALUES ($1,$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'{}'::jsonb,"
        " $11,$12,$12,$13,$14,$15,'DEPTH_WALK_WITHIN_LIMIT',$16,$17,$18)",
        fid, order_id, acct["account_id"], acct["session_id"], group_id,
        role, direction, side, slug, fixture or ("fx-" + slug), qty, price,
        fee, round(qty * price, 6), ts(at), SIM, strategy, ts(recorded_at))
    return fid


async def settle(conn, acct, *, group_id, slug, qty, outcome, payout, at,
                 recorded_at, side="LONG"):
    sid = "papersettle:" + uid()
    pk = "paperpos:%s:%s:%s:%s" % (acct["account_id"], group_id, slug, side)
    await conn.execute(
        "INSERT INTO paper_settlements (settlement_id, account_id, "
        " position_key, settlement_event_key, version, group_id, "
        " us_market_slug, holding_side, qty, outcome, payout_per_contract, "
        " payout_usd, evidence, evidence_source, settled_at, recorded_at) "
        " VALUES ($1,$2,$3,'test',1,$4,$5,$6,$7,$8,$9,$10,'{}'::jsonb,"
        " 'TEST_EVIDENCE',$11,$12)",
        sid, acct["account_id"], pk, group_id, slug, side, qty, outcome,
        payout, round(qty * payout, 6), ts(at), ts(recorded_at))
    return sid


async def review(conn, acct, *, group_id, at, recorded_at, selection,
                 measure, alternatives, standing, exposure, action,
                 strategy=INV):
    rid = "paperrev:" + uid()
    await conn.execute(
        "INSERT INTO paper_xavier_reviews (review_id, session_id, "
        " account_id, group_id, reviewed_at, trigger, recommendation, "
        " alternatives, selection, exposure, standing, measure, action, "
        " strategy, recorded_at) VALUES ($1,$2,$3,$4,$5,'SCHEDULED_BACKSTOP',"
        " NULL,$6::jsonb,$7::jsonb,$8::jsonb,$9::jsonb,$10::jsonb,"
        " $11::jsonb,$12,$13)",
        rid, acct["session_id"], acct["account_id"], group_id, ts(at),
        json.dumps(alternatives), json.dumps(selection), json.dumps(exposure),
        json.dumps(standing), json.dumps(measure), json.dumps(action),
        strategy, ts(recorded_at))
    return rid


async def finding(conn, acct, *, kind, subject, detail, at):
    await conn.execute(
        "INSERT INTO paper_audrey_findings (finding_id, session_id, "
        " account_id, found_at, kind, severity, subject, detail, "
        " recorded_at) VALUES ($1,$2,$3,$4,$5,'INFO',$6,$7::jsonb,$4)",
        "paperfind:" + uid(), acct["session_id"], acct["account_id"], ts(at),
        kind, subject, json.dumps(detail))


async def karen(conn, *, target_id, challenged_at, created_at):
    cid = "kc_" + uid()
    await conn.execute(
        "INSERT INTO karen_challenges (challenge_id, target_agent, "
        " target_kind, target_id, detector, claim, severity, evidence_refs, "
        " record_at, challenged_at, created_at) VALUES ($1,'DEREK',"
        " 'paper_decisions',$2,$3,'synthetic replay challenge','LOW',"
        " $4::jsonb,$5,$5,$6)", cid, target_id, uid("det-"),
        json.dumps([{"kind": "paper_decisions", "id": target_id}]),
        ts(challenged_at), ts(created_at))
    return cid


async def recorded_intent(conn, *, decision_id, slug, qty, limit, at,
                          recorded_at, strategy=INV):
    it = CI.build_decision_intent(
        decision_id=decision_id, strategy=strategy,
        strategy_version=strategy + "_TEST_V1", evidence={},
        opportunity_score={"status": "UNAVAILABLE", "why": "TEST"},
        derek={"verdict": "ENTER"}, karen={"state": "UNAVAILABLE"},
        allie={"status": "UNAVAILABLE", "why": "TEST"},
        eddie={"status": "UNAVAILABLE", "why": "TEST"}, us_market_slug=slug,
        contract={}, holding_side="LONG", order_intent=LONG,
        order_type="MARKETABLE", time_in_force="IOC", limit_price=limit,
        wire_price=limit, target_qty=qty, sizing_basis={}, created_at=at)
    await conn.execute(
        "INSERT INTO canonical_decision_intents (intent_id, intent_version, "
        " decision_id, strategy, strategy_version, sleeve, evidence, "
        " opportunity_score, derek, karen, allie, eddie, venue, "
        " us_market_slug, contract, holding_side, order_intent, order_type, "
        " time_in_force, limit_price, wire_price, target_qty, sizing_basis, "
        " created_at, content_sha, recorded_at) VALUES ($1,$2,$3,$4,$5,$6,"
        " '{}','{\"status\":\"UNAVAILABLE\"}','{\"verdict\":\"ENTER\"}',"
        " '{\"state\":\"UNAVAILABLE\"}','{\"status\":\"UNAVAILABLE\"}',"
        " '{\"status\":\"UNAVAILABLE\"}','POLYMARKET',$7,'{}','LONG',$8,"
        " 'MARKETABLE','IOC',$9,$9,$10,'{}',$11,$12,$13)",
        it["intent_id"], it["intent_version"], decision_id, strategy,
        it["strategy_version"], it["sleeve"], slug, LONG, limit, qty, ts(at),
        it["content_sha"], ts(recorded_at))
    return it


# ═════════════════════════════════════════════════════════════════════
# THE END-TO-END SCENARIO
# ═════════════════════════════════════════════════════════════════════

async def scenario(conn, *, raw=False) -> dict:
    """One INVESTMENT position managed by Xavier and settled, a qualified
    refusal on another market, a TRAINING entry refused for cash on the same
    fixture, an INVESTMENT entry whose record cannot build a canonical
    intent, deliberately FUTURE-DATED rows the replay must not see, and the
    settlement-lag history Allie needs."""
    acct, B = await account(conn, raw=raw)
    m1, m2, m4 = uid("rpl-m1-"), uid("rpl-m2-"), uid("rpl-m4-")
    fx = "fx-" + m1
    # settlement-lag history: 6 markets settled 3 h after their start, a day
    # before B, with pre-map rows written before B
    for k in range(6):
        s = uid("rpl-hist-")
        start = B - 86400.0 + k * 600
        await premap(conn, s, game_start=start, updated_at=B - 90000.0)
        await settle(conn, acct, group_id=uid("g-hist-"), slug=s, qty=1.0,
                     outcome="WON", payout=1.0, at=start + 10800.0,
                     recorded_at=start + 10860.0)
    await premap(conn, m1, game_start=B + 3600.0, updated_at=B - 100.0)
    # m2's pre-map row was REWRITTEN after the clock: invisible
    await premap(conn, m2, game_start=B + 7200.0, updated_at=B + 50.0)
    v1 = await valuation(conn, slug=m1, p=0.62, recorded_at=B - 20.0,
                         observed_at=B - 25.0, received_at=B - 22.0)
    b1 = await book(conn, m1, observed_at=B - 5.0, recorded_at=B - 5.0,
                    bids=((0.53, 300),), offers=((0.55, 60), (0.56, 140)))
    # FUTURE-DATED: claims to be observed before the decision but was
    # recorded after the decision clock -- must be invisible to it
    b_future = await book(conn, m1, observed_at=B - 1.0, recorded_at=B + 0.5,
                          bids=((0.52, 7),), offers=((0.57, 7),))
    # an earlier decision on m1 that Karen challenged (OPEN at the clock)
    d0 = await decision(conn, acct, slug=m1, at=B - 1000.0,
                        recorded_at=B - 999.0, verdict="REFUSE",
                        refusal="BELOW_MIN_GROSS_EDGE",
                        econ=economics(qty=10, vwap=0.6, fees=0.1, net=-1.0))
    k_open = await karen(conn, target_id=d0, challenged_at=B - 50.0,
                         created_at=B - 50.0)
    k_future = await karen(conn, target_id=d0, challenged_at=B - 40.0,
                           created_at=B + 1.0)           # recorded later
    # the qualified alternative: a refusal on m2 shortly before
    d2 = await decision(
        conn, acct, slug=m2, at=B - 100.0, recorded_at=B - 99.9,
        verdict="REFUSE", refusal="PROBABILITY_EVIDENCE_STALE",
        p=0.58, limit=0.50, qty=50.0,
        econ=economics(qty=50, vwap=0.50, fees=0.4, net=3.6),
        pinnacle={"age_s": 45.0, "limit_s": 30.0})
    # THE INVESTMENT DECISION (no book_obs_id: the replay must pick the
    # latest book recorded by the clock -- b1, not the future-dated one)
    d1 = await decision(conn, acct, slug=m1, at=B, recorded_at=B + 0.2,
                        valuation_id=v1, book_obs_id=None,
                        econ=economics(qty=100, vwap=0.554, fees=0.7, net=6.0,
                                       depth=200, edge_pp=7.0))
    g1 = "paper_group_" + uid()
    o1 = await order(conn, acct, decision_id=d1, group_id=g1, slug=m1,
                     qty=100.0, limit=0.56, at=B + 0.2, terminal_at=B + 3.0)
    await recorded_intent(conn, decision_id=d1, slug=m1, qty=100.0,
                          limit=0.56, at=B, recorded_at=B + 0.2)
    await fill(conn, acct, order_id=o1, group_id=g1, slug=m1, qty=60.0,
               price=0.55, fee=0.42, at=B + 2.0, recorded_at=B + 2.1)
    await fill(conn, acct, order_id=o1, group_id=g1, slug=m1, qty=40.0,
               price=0.56, fee=0.28, at=B + 2.5, recorded_at=B + 2.6)
    # Xavier: REDUCE 40 on fresh evidence (taken), then protection on stale
    await review(
        conn, acct, group_id=g1, at=B + 600.0, recorded_at=B + 600.1,
        selection={"selected": "REDUCE", "mechanical_selection": "REDUCE"},
        measure={"evidence_state": "FRESH_CURRENT_PROBABILITY", "p": 0.66,
                 "stale": False},
        alternatives={"candidates": [
            {"action": "REDUCE", "qty": 40.0, "fee_usd": 0.2,
             "walk": {"worst_price": 0.70, "worst_wire": 0.70}},
            {"action": "HOLD", "qty": 100.0}], "not_rankable": []},
        standing={"live_orders": [], "protective_price": {
            "ok": True, "price": 0.57}},
        exposure={"open_qty": 100.0}, action={"taken": "SUBMIT_REDUCE"})
    o2 = await order(conn, acct, decision_id=None, group_id=g1, slug=m1,
                     qty=40.0, limit=0.70, at=B + 600.2, role="REDUCE",
                     direction="SELL", terminal_at=B + 602.0)
    await fill(conn, acct, order_id=o2, group_id=g1, slug=m1, qty=40.0,
               price=0.70, fee=0.2, at=B + 601.0, recorded_at=B + 601.1,
               role="REDUCE", direction="SELL")
    # production's writer shape: mechanical_selection = the selection
    # (paper_xavier.py: mechanical_selection=chosen, chosen = selected)
    await review(
        conn, acct, group_id=g1, at=B + 1200.0, recorded_at=B + 1200.1,
        selection={"selected": "HOLD", "mechanical_selection": "HOLD"},
        measure={"evidence_state": "STALE_ENTRY_TIME_PROBABILITY",
                 "p": 0.66, "stale": True},
        alternatives={"candidates": [{"action": "HOLD", "qty": 60.0}],
                      "not_rankable": []},
        standing={"live_orders": [], "protective_price": {
            "ok": True, "price": 0.57}},
        exposure={"open_qty": 60.0}, action={"taken": "PLACE_STANDING"})
    await book(conn, m1, observed_at=B + 900.0, recorded_at=B + 900.0,
               bids=((0.40, 300),), offers=((0.42, 300),))   # a marked dip
    await settle(conn, acct, group_id=g1, slug=m1, qty=60.0, outcome="WON",
                 payout=1.0, at=B + 3600.0 + 10800.0,
                 recorded_at=B + 3600.0 + 10860.0)
    # m2 settles LOST (the refusal's HYPOTHETICAL)
    await settle(conn, acct, group_id=uid("g-m2-"), slug=m2, qty=1.0,
                 outcome="LOST", payout=0.0, at=B + 7200.0 + 10800.0,
                 recorded_at=B + 7200.0 + 10860.0)
    # a TRAINING entry on the same fixture refused by the ledger for cash
    d3 = await decision(conn, acct, slug=uid("rpl-m3-"), at=B + 300.0,
                        recorded_at=B + 300.1, strategy=TRN, fixture=fx,
                        econ=economics(qty=20, vwap=0.40, fees=0.1, net=1.5))
    await finding(conn, acct, kind="PAPER_RISK_REFUSED_THE_ORDER",
                  subject=d3, detail={"refusal":
                                      "INSUFFICIENT_AVAILABLE_PAPER_CASH"},
                  at=B + 300.2)
    # an INVESTMENT entry whose record carries no sized quantity: no
    # canonical intent can be built, yet the historical order went out
    d4 = await decision(conn, acct, slug=m4, at=B + 400.0,
                        recorded_at=B + 400.1, qty=None,
                        econ=economics(qty=10, vwap=0.45, fees=0.05, net=0.9))
    g4 = "paper_group_" + uid()
    o4 = await order(conn, acct, decision_id=d4, group_id=g4, slug=m4,
                     qty=10.0, limit=0.45, at=B + 400.1, terminal_at=B + 403)
    await fill(conn, acct, order_id=o4, group_id=g4, slug=m4, qty=10.0,
               price=0.45, fee=0.05, at=B + 402.0, recorded_at=B + 402.1)
    await settle(conn, acct, group_id=g4, slug=m4, qty=10.0, outcome="LOST",
                 payout=0.0, at=B + 20000.0, recorded_at=B + 20060.0)
    return {"acct": acct, "B": B, "m1": m1, "m2": m2, "m4": m4, "d0": d0,
            "d1": d1, "d2": d2, "d3": d3, "d4": d4, "g1": g1, "g4": g4,
            "b1": b1, "b_future": b_future, "v1": v1, "k_open": k_open,
            "k_future": k_future, "end": B + 30000.0, "now": B + 30001.0}
