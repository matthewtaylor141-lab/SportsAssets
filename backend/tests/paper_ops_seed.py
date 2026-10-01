"""A SYNTHETIC PAPER SESSION FOR THE OPERATIONAL-VIEW PROOFS (no tests here).

ALL DATA IS SYNTHETIC TEST DATA written into a scratch test database: a
fresh paper account (tests) or, for the local screenshot run only, the test
database's own paper account. It writes through the real ledger, simulator
and settlement functions where they exist (orders, fills, reservations,
settlements) and inserts the agents' records (decisions, handoffs, reviews,
findings, a daily report, the runtime heartbeat) in the shapes their writers
use. Nothing here touches a production database.

What it seeds, so every part of the operational view has something real to
read:
  * Derek: DEREK_ENTRY_POLICY_V2 research decisions (REFUSE, including
    STRATEGY_ENTRIES_DISABLED), PINNACLE_ONLY_PAPER_BENCHMARK decisions (one
    ENTER) and PINNACLE_COMPLETED_GAME_PAPER decisions (two ENTER), every one
    with its recorded policy_decision.conditions;
  * the benchmark entries' paper orders and simulated fills on the one
    ledger, their handoffs to Xavier, a standing protection order, Xavier's
    reviews, a WON settlement and a SETTLED_AT_VENUE_PRICE settlement, and
    one position left pending;
  * Audrey's daily report and findings; the paper runtime's heartbeat.
"""
from __future__ import annotations

import json

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets import bettor_paper_simulator as SIM

from tests import paper_harness as H

V2 = "DEREK_ENTRY_POLICY_V2"
STRICT = "PINNACLE_ONLY_PAPER_BENCHMARK"
CG = "PINNACLE_COMPLETED_GAME_PAPER"
VERSIONS = {V2: V2, STRICT: "PINNACLE_ONLY_PAPER_BENCHMARK_V1",
            CG: "PINNACLE_COMPLETED_GAME_PAPER_V1"}
BENCH_CONDITIONS = ("contract_outcome_settlement_match",
                    "pinnacle_fresh_at_the_decision_instant",
                    "current_paper_book_with_depth",
                    "edge_at_least_5pp_at_every_level_used",
                    "positive_ev_after_fees")
V2_CONDITIONS = ("internal_model_qualified", "pinnacle_qualified",
                 "gross_edge_at_least_threshold", "net_ev_positive")


def _conds(names, fail_at):
    """Conditions in order; the one at `fail_at` fails, later ones are not
    evaluated (passed None). fail_at None = every condition passed."""
    out = []
    for i, n in enumerate(names):
        if fail_at is None or i < fail_at:
            out.append({"condition": n, "passed": True})
        elif i == fail_at:
            out.append({"condition": n, "passed": False,
                        "refusal": "SYNTHETIC_" + n.upper()})
        else:
            out.append({"condition": n, "passed": None})
    return out


#: SYNTHETIC GAMES (test data): the instrument label a decision records, in
#: derek_policy.instrument_label's keys, so the pages can name the market.
GAMES = [
    {"participant": "New York Yankees", "home_team": "Boston Red Sox",
     "away_team": "New York Yankees", "market_type": "MONEYLINE",
     "competition": "MLB", "period": "FULL_GAME", "event_date": "2026-10-02"},
    {"participant": "Los Angeles Dodgers", "home_team": "Los Angeles Dodgers",
     "away_team": "San Diego Padres", "market_type": "MONEYLINE",
     "competition": "MLB", "period": "FULL_GAME", "event_date": "2026-10-02"},
    {"participant": "Seattle Mariners", "home_team": "Houston Astros",
     "away_team": "Seattle Mariners", "market_type": "MONEYLINE",
     "competition": "MLB", "period": "FULL_GAME", "event_date": "2026-10-03"},
]


def _economics(strategy, *, ev, vwap=0.5, qty=400.0):
    """The decision's economics.acquisition, in paper_benchmark's keys; the
    completed-game policy carries its exceptional-settlement scenarios with
    UNMEASURED probabilities (paper_benchmark.exceptional_scenarios)."""
    if strategy == V2:
        return None
    acq = {"qty": qty, "vwap": vwap, "acquisition_cost_usd": qty * vwap,
           "fees_usd": 0.4, "expected_net_profit_usd": ev,
           "net_ev_positive": ev > 0}
    if strategy == CG:
        acq.update(label="CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED",
                   conditional_on="ORDINARY_COMPLETION")
        acq["exceptional_settlement"] = {
            "label": "CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED",
            "included_in_conditional_ev": False,
            "scenarios": {
                "NOT_PLAYED": {
                    "venue_payout": "PAY_LAST_FAIR_MARKET_PRICE",
                    "probability": "UNMEASURED",
                    "payoff_per_contract_range": [-vwap, 1.0 - vwap],
                    "payoff_basis": ("the venue settles at the contract's "
                                     "last fair market price S")},
                "SUSPENDED_BEYOND_THE_WINDOW": {
                    "venue_payout": "PAY_LAST_FAIR_MARKET_PRICE",
                    "probability": "UNMEASURED",
                    "payoff_per_contract_range": [-vwap, 1.0 - vwap],
                    "payoff_basis": ("the venue settles at the contract's "
                                     "last fair market price S")}}}
    return {"acquisition": acq}


async def _decision(conn, acct, *, n, strategy, verdict, refusal, at, slug,
                    fail_at, edge, ev, p_pin=0.58, p_int=None, game=None,
                    short_pp=None):
    did = "paperseed:%s:%s:%d" % (acct["account_id"][-10:], strategy[:6], n)
    names = V2_CONDITIONS if strategy == V2 else BENCH_CONDITIONS
    pd = {"strategy": strategy, "policy_version": VERSIONS[strategy],
          "conditions": _conds(names, fail_at), "gross_edge_pp": edge,
          "net_expected_profit_usd": ev, "fees_usd": 0.4,
          "p_pinnacle": p_pin, "admitted": verdict == "ENTER",
          "refusal": refusal, "refusals": [refusal] if refusal else [],
          "shortfall": {"edge_pp": edge, "ev_after_fees_usd": ev,
                        "edge_threshold_pp": 5.0,
                        "edge_shortfall_pp": (short_pp if short_pp is not None
                                              else max(0.0, round(5.0 - edge, 6))),
                        "depth_within_limit": 400, "pinnacle_age_s": 12.0,
                        "pinnacle_limit_s": 120.0, "book_age_s": 2.0},
          "economics_label": ("CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED"
                              if strategy == CG else None),
          "rationale": ("%s %s: synthetic research decision" % (
              verdict, refusal or "") if strategy == V2 else None)}
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, intent, "
        " fixture, label, verdict, refusal, refusals, p_internal, "
        " internal_model, p_pinnacle, pinnacle, p_blended, proposed_qty, "
        " limit_price, qualification_gaps, policy_version, policy_decision, "
        " simulator_version, strategy, economics) VALUES ($1,$2,$3,$4,NULL,$5,"
        " 'LONG','ORDER_INTENT_BUY_LONG','fx-seed',$6::jsonb,$7,$8,$9,$10,"
        " '{}'::jsonb,$11,'{}'::jsonb,$12,$13,$14,'[]'::jsonb,$15,$16::jsonb,"
        " $17,$18,$19::jsonb)",
        did, acct["session_id"], acct["account_id"], L._ts(at), slug,
        json.dumps(GAMES[n % len(GAMES)] if game is None else game),
        verdict, refusal, [refusal] if refusal else [], p_int, p_pin,
        None if p_int is None else round((p_int + p_pin) / 2, 6),
        L.D(400) if verdict == "ENTER" else None,
        L.D("0.5") if verdict == "ENTER" else None, VERSIONS[strategy],
        json.dumps(pd), SIM.VERSION, strategy,
        None if _economics(strategy, ev=ev) is None
        else json.dumps(_economics(strategy, ev=ev)))
    return did


async def _entry(conn, acct, *, key, slug, strategy, decision_id, at,
                 qty=400.0, limit=0.5):
    o = H.order(acct, key=key, slug=slug, qty=qty, limit=limit, at=at,
                fixture="fx-seed")
    o["strategy"] = strategy
    o["decision_id"] = decision_id
    lb = await conn.fetchval("SELECT label FROM paper_decisions "
                             " WHERE decision_id=$1", decision_id)
    if lb:
        o["label"] = json.loads(lb) if isinstance(lb, str) else lb
    got = await L.submit_order(conn, o, fee_fn=H.flat_fee(0.001), now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3.0, offers=[(limit, qty)],
                    bids=[(round(limit - 0.03, 2), qty)])
    sim = await SIM.simulate_order(conn, got["order"]["order_id"],
                                   now=at + 4.0, fee_fn=H.flat_fee(0.001))
    fill = await conn.fetchrow(
        "SELECT fill_id, filled_at, qty FROM paper_fills WHERE order_id=$1 "
        " ORDER BY filled_at LIMIT 1", got["order"]["order_id"])
    hid = "paperhand:%s" % got["order"]["order_id"].split(":", 1)[1]
    await conn.execute(
        "INSERT INTO paper_handoffs (handoff_id, session_id, account_id, "
        " group_id, decision_id, entry_order_id, first_fill_id, first_fill_at,"
        " confirmed_qty, outstanding_qty, strategy) VALUES ($1,$2,$3,$4,$5,"
        " $6,$7,$8,$9,0,$10)", hid, acct["session_id"], acct["account_id"],
        o["group_id"], decision_id, got["order"]["order_id"],
        fill["fill_id"], fill["filled_at"], fill["qty"], strategy)
    return {"order": got["order"], "group_id": o["group_id"], "sim": sim,
            "slug": slug}


async def _review(conn, acct, *, n, group_id, strategy, at, rec, trigger):
    await conn.execute(
        "INSERT INTO paper_xavier_reviews (review_id, session_id, account_id,"
        " group_id, reviewed_at, trigger, recommendation, refusal, "
        " alternatives, exposure, strategy, action) VALUES ($1,$2,$3,$4,$5,"
        " $6,$7,NULL,'[]'::jsonb,'{}'::jsonb,$8,$9::jsonb)",
        "paperrev:%s:%d" % (acct["account_id"][-10:], n), acct["session_id"],
        acct["account_id"], group_id, L._ts(at), trigger, rec, strategy,
        json.dumps({"action": rec, "simulated": True}))


async def seed(conn, acct: dict, *, t0: float) -> dict:
    """Seed `acct` ({account_id, session_id}) with the synthetic session.
    Returns what the proofs assert against."""
    # ONE MARKET NAMESPACE PER ACCOUNT: book observations and consumed
    # liquidity are per market, so two seeded accounts never share a book
    tail = acct["account_id"][-10:]

    def sl(x):
        return "seed-%s-mlb-%s" % (tail, x)
    out = {"decisions": {V2: [], STRICT: [], CG: []}}
    n = 0
    # DEREK'S ORIGINAL RESEARCH DECISIONS: recorded, all refused
    for i, (refusal, fail_at) in enumerate([
            ("INTERNAL_MODEL_NOT_QUALIFIED", 0),
            ("INTERNAL_MODEL_NOT_QUALIFIED", 0),
            ("BELOW_MIN_EDGE", 2),
            ("STRATEGY_ENTRIES_DISABLED", None)]):
        n += 1
        out["decisions"][V2].append(await _decision(
            conn, acct, n=n, strategy=V2, verdict="REFUSE", refusal=refusal,
            at=t0 + i, slug=sl("v2-%d" % i), fail_at=fail_at,
            edge=3.1 + i, ev=-1.0 + i, p_int=0.55))
    # THE STRICT BENCHMARK: two refusals, one entry
    for i, (verdict, refusal, fail_at) in enumerate([
            ("REFUSE", "SETTLEMENT_NOT_SUPPORTED", 0),
            ("REFUSE", "EDGE_BELOW_5PP", 3),
            ("ENTER", None, None)]):
        n += 1
        out["decisions"][STRICT].append(await _decision(
            conn, acct, n=n, strategy=STRICT, verdict=verdict,
            refusal=refusal, at=t0 + 10 + i, slug=sl("strict-%d" % i),
            fail_at=fail_at, edge=6.2 if verdict == "ENTER" else 2.0,
            ev=14.5 if verdict == "ENTER" else -3.0))
    # THE COMPLETED-GAME POLICY: one refusal, two entries
    for i, (verdict, refusal, fail_at) in enumerate([
            ("REFUSE", "PINNACLE_NOT_FRESH", 1),
            ("ENTER", None, None), ("ENTER", None, None)]):
        n += 1
        out["decisions"][CG].append(await _decision(
            conn, acct, n=n, strategy=CG, verdict=verdict, refusal=refusal,
            at=t0 + 20 + i, slug=sl("cg-%d" % i), fail_at=fail_at,
            edge=7.4 if verdict == "ENTER" else 4.0,
            ev=18.0 if verdict == "ENTER" else -2.0))
    # FIVE COMPLETED-GAME NEAR MISSES (edge within 1 pp of the 5 pp
    # threshold): the learning record's rule for a Derek proposal
    for i in range(5):
        n += 1
        out["decisions"][CG].append(await _decision(
            conn, acct, n=n, strategy=CG, verdict="REFUSE",
            refusal="BELOW_MIN_GROSS_EDGE", at=t0 + 25 + i,
            slug=sl("cg-near-%d" % i), fail_at=3, edge=4.5, ev=-0.2,
            short_pp=0.5))
    # ENTRIES ON THE ONE LEDGER, AND THEIR HANDOFFS TO XAVIER
    strict = await _entry(conn, acct, key="seed-strict", strategy=STRICT,
                          slug=sl("strict-2"),
                          decision_id=out["decisions"][STRICT][2], at=t0 + 30)
    cg1 = await _entry(conn, acct, key="seed-cg-1", strategy=CG,
                       slug=sl("cg-1"),
                       decision_id=out["decisions"][CG][1], at=t0 + 40)
    cg2 = await _entry(conn, acct, key="seed-cg-2", strategy=CG,
                       slug=sl("cg-2"),
                       decision_id=out["decisions"][CG][2], at=t0 + 50)
    out["entries"] = {"strict": strict, "cg1": cg1, "cg2": cg2}
    # A STANDING PROTECTION ORDER (resting sell of held inventory)
    prot = H.order(acct, key="seed-prot", slug=sl("cg-2"),
                   direction="SELL", qty=200.0, limit=0.62,
                   role="STANDING_PROTECTION", group_id=cg2["group_id"],
                   order_type="RESTING", tif="GTD", at=t0 + 60, ttl=86400.0)
    prot["strategy"] = CG
    got = await L.submit_order(conn, prot, fee_fn=H.flat_fee(0.001),
                               now=t0 + 60)
    assert got["ok"], got
    out["protection_order"] = got["order"]["order_id"]
    # XAVIER'S REVIEWS
    for i, (g, s, rec, trig) in enumerate([
            (strict["group_id"], STRICT, "HOLD", "FIRST_FILL"),
            (cg1["group_id"], CG, "HOLD", "FIRST_FILL"),
            (cg2["group_id"], CG, "PLACE_STANDING_PROTECTION", "FIRST_FILL"),
            (cg2["group_id"], CG, "HOLD", "SCHEDULED_BACKSTOP")]):
        await _review(conn, acct, n=i + 1, group_id=g, strategy=s,
                      at=t0 + 70 + i, rec=rec, trigger=trig)
    # SETTLEMENTS: the strict entry WON; one completed-game entry settled at
    # the venue's published price; the other stays pending
    await L.settle(conn, account_id=acct["account_id"],
                   group_id=strict["group_id"], slug=strict["slug"],
                   holding_side="LONG", settlement_event_key="venue-final:"
                   + strict["slug"], outcome="WON",
                   evidence={"test": "synthetic"},
                   evidence_source="TEST_FIXTURE_SYNTHETIC", at=t0 + 90,
                   session_id=acct["session_id"])
    await L.settle(conn, account_id=acct["account_id"],
                   group_id=cg1["group_id"], slug=cg1["slug"],
                   holding_side="LONG", settlement_event_key="venue-final:"
                   + cg1["slug"], outcome="SETTLED_AT_VENUE_PRICE",
                   evidence={"test": "synthetic", "price": 0.47},
                   evidence_source="TEST_FIXTURE_SYNTHETIC", at=t0 + 95,
                   session_id=acct["session_id"], price_per_contract=0.47)
    # AUDREY: a finding and a daily report
    for i, (sev, kind) in enumerate([
            ("INFO", "PINNACLE_COMPLETED_GAME_PAPER_FILL_AUDITED"),
            ("WARNING", "CONFLICTING_SETTLEMENT_EVIDENCE")]):
        await conn.execute(
            "INSERT INTO paper_audrey_findings (finding_id, session_id, "
            " account_id, found_at, kind, severity, subject, detail) VALUES "
            " ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)",
            "paperfind:seed:%s:%d" % (acct["account_id"][-10:], i),
            acct["session_id"], acct["account_id"], L._ts(t0 + 100 + i),
            kind, sev, cg2["group_id"], json.dumps({"synthetic": True}))
    b = await L.balances(conn, acct["account_id"], now=t0 + 110)
    rep = {"equity": {"cash_usd": b["cash_usd"],
                      "reserved_usd": b["reserved_usd"]},
           "pnl": {"realized_total_usd": b["realized_pnl_usd"]},
           "reconciliation": {"checks": [{"check": "one_ledger",
                                          "passed": True}],
                              "reconciles": True},
           "pinnacle_completed_game_paper": {"entries": 2}}
    import datetime as _dt
    await conn.execute(
        "INSERT INTO paper_audrey_reports (report_id, session_id, account_id,"
        " report_day, reporting_tz, version, generated_at, final, reconciles,"
        " report, digest) VALUES ($1,$2,$3,$4,'America/New_York',1,$5,FALSE,"
        " TRUE,$6::jsonb,'seeddigest')",
        "paperrep:seed:%s" % acct["account_id"][-10:], acct["session_id"],
        acct["account_id"],
        _dt.datetime.fromtimestamp(t0, _dt.timezone.utc).date(),
        L._ts(t0 + 110), json.dumps(rep))
    # THE RUNTIME'S HEARTBEAT (a pass that ran; the settle step left one
    # position waiting)
    # AUDREY'S EVENT AUDITS and THE LEARNING RECORD, through the learning
    # module's own steps (migration 185), when this build has them
    try:
        from sportsassets.agents import paper_learning as PLRN
        if await PLRN.has_schema(conn):
            ctx = {"account_id": acct["account_id"],
                   "session_id": acct["session_id"], "now": t0 + 115}
            out["event_audits"] = await PLRN.step_audit_events(conn, ctx)
            out["learning"] = await PLRN.learn(
                conn, account_id=acct["account_id"],
                session_id=acct["session_id"], now=t0 + 116)
    except ImportError:
        pass
    await S.record_pass(conn, acct["session_id"], now=t0 + 120, result={
        "at": t0 + 120, "ran": True, "errors": {}, "elapsed_s": 1.2,
        "steps": {"settle": {"settled": 0, "corrected": 0, "conflicts": 0,
                             "waiting": 1, "settled_at_venue_price": 1}}})
    out["balances"] = await L.balances(conn, acct["account_id"],
                                       now=t0 + 120)
    return out
