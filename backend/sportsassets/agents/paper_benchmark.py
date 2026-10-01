"""PINNACLE_ONLY_PAPER_BENCHMARK -- AN EXPERIMENTAL PAPER EXECUTION BENCHMARK.

PAPER ONLY, OWNER-AUTHORIZED. It decides on the existing fictional paper
account, inside the existing paper session, under the existing rails, and its
orders are rows in `paper_orders` filled by the paper simulator
(PAPER_SIM_V1). It is EXPERIMENTAL EXECUTION, NOT EVIDENCE OF QUALIFIED OR
PROVEN PROFITABILITY (`DISCLOSURE`, written on every record it produces).

WHAT IT DOES. For each entry-experiment valuation (`external_valuations` of
EXT_PINNACLE_DEVIG_V1_SHADOW, either purpose -- in production every row is
CALIBRATION_ONLY because of the P5 book-currency rule) it records ONE
decision labelled PINNACLE_ONLY_PAPER_BENCHMARK, beside -- never instead of
-- the original two-model decision on the same valuation (migration 182's
strategy key):

  probability   the STORED de-vigged Pinnacle probability of the event THIS
                contract pays on (`probability`, already oriented by the
                lane's single complement inversion), for the contract,
                payout event / complement and settlement terms of the row.
                Refused by name when the contract identity, the payout
                outcome match or the settlement terms are not established
                (the identity and settlement checks of the entry experiment,
                `derek_policy.evaluate` steps 1 and 3, unweakened), and
                PROBABILITY_EVIDENCE_STALE when the Pinnacle reading is
                older than the existing 30 s rule RE-AGED at this decision's
                own instant (`paper_derek._pinnacle`).
  price         NEVER the valuation's displayed quote. A CURRENT paper book
                observation read through the read-only market-data client at
                the decision (our receipt time, `paper_book_observations`):
                the consumed side's DISPLAYED depth only, on the adapter's
                cent grid, net of liquidity other paper orders consumed at
                that observation. PAPER_SIM_V1 has no depth-haircut
                parameter; displayed depth is the conservative maximum.
  edge          p_pinnacle - level price >= 0.05 (5.0 percentage points) AT
                EVERY LEVEL USED: the limit is the deepest level that still
                clears, so the simulator can never fill a level that does
                not (it walks within the limit on the first book observed
                at or after decision + the existing 2 s delay).
  EV            expected profit after fees > 0, with the fee function the
                simulator charges (`bettor_paper_ledger._fee`, the session's
                fee function or the deployed schedule) per walked level, and
                the measured void rate applied when there is one.
  size          the walked depth within the limit, capped by the session's
                target order size and per-order cap (reservation = limit x
                qty + max fees); every other rail -- hedge reserve, per
                market, per fixture, concurrent groups -- is
                `bettor_paper_ledger.submit_order`'s, unchanged.

REFUSALS ARE RECORDS TOO, with the exact shortfall (edge in pp against 5.0,
EV after fees, depth within the limit, Pinnacle and book ages): see
`research/pinnacle_benchmark_readback.sql`.

NEVER AN INTERNAL MODEL. p_internal is NULL, `internal_model` says
{available: false, reason}, p_blended is NULL. Pinnacle stays in
p_pinnacle / pinnacle.

P5 IS DISCLOSED, NOT RESOLVED. Every decision and order carries
book_currency = NOT_ESTABLISHED with its mechanism and basis: the venue read
does not establish that the displayed book is current; its age is bounded only
by our own receipt instant. Funded admission and the P5 rule are untouched.

THE SWITCH (default OFF). Both: the process environment PAPER_BENCHMARK in
(on, 1, true, yes) AND paper_control('PINNACLE_ONLY_PAPER_BENCHMARK').enabled
(migration 182 inserts it enabled: the environment flag is the operator
switch, the row the kill switch). It runs only inside the paper session
(PAPER_SESSION and its row on). With the flag unset nothing here runs: the
pass has no benchmark step and the per-valuation hook returns exactly as
before.

EVERY KEY IS STRATEGY-SPECIFIC. The decision id ('paperbench:' + sha of
session, valuation and STRATEGY) roots every other key: the group
('paperbenchgrp:'), the entry's idempotency key and order id, the fill keys
(order id + book observation + level), the position key (group), the handoff
key (group) and Xavier's management-order keys (group / position). The
two-model strategy keeps its own namespace ('paperdec:' / 'papergrp:'), and
migration 182's unique index is (session, valuation, strategy): neither
strategy can overwrite or suppress the other.

ONE CASH LEDGER. No bankroll of its own and no second funding: its orders
reserve on the account's one `paper_ledger` through
`bettor_paper_ledger.submit_order`, under the same account row lock as every
other paper order, so available cash is never committed twice across
strategies. Only this strategy is enabled for NEW paper entries; the
two-model strategy keeps recording its decisions with its entry switch
(paper_control 'PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2') off.

UNREACHABLE FROM REAL MONEY. This module imports only the paper ledger, the
paper simulator, the pure policy helpers (`derek_policy`) and paper Derek's
pure helpers; it never imports a funded, live or order-submitting module, and
no funded module imports a paper module
(tests/test_paper_records_cannot_reach_the_funded_path.py,
tests/test_pinnacle_only_paper_benchmark.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from typing import Any

from .. import bettor_paper_ledger as L
from .. import bettor_paper_simulator as SIM
from . import derek_policy as DP
from . import paper_derek as PD

STRATEGY = "PINNACLE_ONLY_PAPER_BENCHMARK"
#: THE ORIGINAL TWO-MODEL STRATEGY'S LABEL (migration 182's column default).
TWO_MODEL_STRATEGY = DP.POLICY_V2
VERSION = "PINNACLE_ONLY_PAPER_BENCHMARK_V1"
ENV_FLAG = "PAPER_BENCHMARK"
CONTROL_KEY = STRATEGY

DISCLOSURE = (
    "PINNACLE_ONLY_PAPER_BENCHMARK: EXPERIMENTAL PAPER EXECUTION on a "
    "fictional account, deciding on the de-vigged Pinnacle probability "
    "alone. It is NOT evidence of qualified or proven profitability: no "
    "internal model is used, the Pinnacle source's calibration and the venue "
    "book's currency (P5) are not established, and every fill is SIMULATED "
    "(PAPER_SIM_V1). No real money, no real venue order.")

#: THE OWNER'S EDGE: a probability difference on a $0/$1 contract.
MIN_EDGE = 0.05
MIN_EDGE_PP = 5.0
#: A paper book observation older than this at the decision is not current.
BOOK_MAX_AGE_S = 10.0

R_ENV_OFF = "PAPER_BENCHMARK_ENVIRONMENT_FLAG_IS_NOT_ON"
R_CONTROL_OFF = "THE_PINNACLE_ONLY_PAPER_BENCHMARK_CONTROL_ROW_IS_OFF"
R_CONTROL_ABSENT = "THE_PINNACLE_ONLY_PAPER_BENCHMARK_CONTROL_ROW_IS_ABSENT"

R_IDENTITY = DP.R_IDENTITY                    # FIXTURE_IDENTITY_NOT_ESTABLISHED
R_OUTCOME = "PAYOUT_OUTCOME_MATCH_NOT_ESTABLISHED"
R_SETTLEMENT = DP.R_SETTLEMENT                # SETTLEMENT_NOT_SUPPORTED
R_NOT_REAL = DP.R_NOT_REAL
R_NOT_PMUS = PD.R_NOT_PMUS
R_PROBABILITY_UNQUALIFIED = "PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE"
R_NO_PINNACLE = DP.R_NO_PINNACLE
R_STALE = DP.R_STALE                          # PROBABILITY_EVIDENCE_STALE
R_FRESHNESS_UNKNOWN = DP.R_FRESHNESS_UNKNOWN
R_NO_BOOK = PD.R_NO_BOOK
R_BOOK_NOT_CURRENT = "THE_PAPER_BOOK_OBSERVATION_IS_NOT_CURRENT"
R_EDGE = DP.R_BELOW                           # BELOW_MIN_GROSS_EDGE
R_NO_QTY = DP.R_NO_QTY
R_FEES = DP.R_FEES
R_NET = DP.R_NET                              # NET_EV_NOT_POSITIVE_AFTER_FEES
R_ORDER_REFUSED = PD.R_ORDER_REFUSED

BOOK_CURRENCY = {
    "verdict": "NOT_ESTABLISHED",
    "rule": "P5_BOOK_CURRENCY",
    "mechanism": ("PAPER_MARKET_DATA_CLIENT_READ: the read-only client's "
                  "book read, stamped with OUR receipt instant "
                  "(paper_book_observations.observed_at)"),
    "basis": ("the venue read does not establish that the displayed book is "
              "current; its age is bounded only by our own receipt instant. "
              "The price is never claimed proven current"),
    "funded_admission_and_p5_rule": "UNTOUCHED"}

STRATEGY_LABEL = {"strategy": STRATEGY, "disclosure": DISCLOSURE,
                  "book_currency": "NOT_ESTABLISHED"}

#: The entry experiment whose valuations are decided (the value of
#: bettor_external_shadow.EXPERIMENT_ID, stated here so this module imports
#: nothing beyond the paper and pure-policy modules; a test pins equality).
EXPERIMENT_ID = "EXT_PINNACLE_DEVIG_V1_SHADOW"

GAP_NOT_EVIDENCE = "NOT_EVIDENCE_OF_PROFITABILITY"
GAP_NO_MODEL = "NO_INTERNAL_MODEL_BY_DESIGN"

CANDIDATES_SQL = """
    SELECT v.* FROM external_valuations v
     WHERE v.experiment_id = $1
       AND v.decided_at > to_timestamp($2) AND v.decided_at <= to_timestamp($3)
       AND v.us_market_slug IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM paper_decisions d
                        WHERE d.session_id = $4 AND d.valuation_id = v.id
                          AND d.strategy = $6)
     ORDER BY v.decided_at DESC, v.id DESC
     LIMIT $5
"""


# ═════════════════════════════════════════════════════════════════════
# THE SWITCH
# ═════════════════════════════════════════════════════════════════════

def env_on() -> bool:
    return str(os.environ.get(ENV_FLAG, "")).strip().lower() in (
        "on", "1", "true", "yes")


async def enablement(conn) -> dict:
    """The environment flag AND the kill-switch row. Never raises."""
    out: dict[str, Any] = {"strategy": STRATEGY, "env_flag": ENV_FLAG,
                           "env_on": env_on(), "control_key": CONTROL_KEY}
    if not out["env_on"]:
        return dict(out, enabled=False, refusal=R_ENV_OFF)
    try:
        row = await conn.fetchrow(
            "SELECT enabled, why, updated_by FROM paper_control "
            " WHERE control_key = $1", CONTROL_KEY)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, enabled=False, refusal=R_CONTROL_ABSENT,
                    why="control row unreadable: %s" % type(exc).__name__)
    if row is None:
        return dict(out, enabled=False, refusal=R_CONTROL_ABSENT)
    out.update(control_on=bool(row["enabled"]), control_why=row["why"],
               control_updated_by=row["updated_by"])
    if not row["enabled"]:
        return dict(out, enabled=False, refusal=R_CONTROL_OFF)
    return dict(out, enabled=True, refusal=None)


def decision_id_for(session_id: str, valuation_id) -> str:
    return "paperbench:" + hashlib.sha256(
        ("%s:%s:%s" % (session_id, valuation_id, STRATEGY)).encode()
    ).hexdigest()[:24]


def group_id_for(decision_id: str) -> str:
    return "paperbenchgrp:" + decision_id.split(":", 1)[1]


# ═════════════════════════════════════════════════════════════════════
# THE CONTRACT / OUTCOME / SETTLEMENT-TERMS MATCH (pure)
# ═════════════════════════════════════════════════════════════════════

def _lane_codes(cand: dict) -> dict:
    """The lane's own refusals on the row, by the check that owns them."""
    out: dict = {"probability": [], "identity": [], "settlement": [],
                 "not_blocking": []}
    for code in cand.get("refusals") or []:
        stage = DP._lane_stage(code)
        if stage == "1_PROBABILITY":
            out["probability"].append(code)
        elif stage == "3_IDENTITY":
            out["identity"].append(code)
        elif stage == "4_SETTLEMENT_SCOPE" or \
                code == "VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED":
            out["settlement"].append(code)
        else:
            out["not_blocking"].append(code)
    return out


def contract_match(cand: dict, row: dict) -> dict:
    """THE MATCH BETWEEN THE STORED PROBABILITY AND THE CONTRACT TRADED.

    Identity and settlement are `derek_policy.evaluate`'s checks 1 and 3 on
    the same candidate (unweakened: settlement passes only when the
    comparison is COMPATIBLE, every rule established and no settlement-stage
    refusal is on the row). The payout outcome match: the probability is of
    the event the contract pays on -- the same event as the de-vig's when
    payout_is_complement is false, NOT(selection) when it is true."""
    lane = _lane_codes(cand)
    checks, refusals = [], []

    def put(name, ok, refusal, detail, **ev):
        checks.append(dict({"check": name, "passed": bool(ok),
                            "refusal": None if ok else refusal,
                            "detail": detail}, **ev))
        if not ok and refusal not in refusals:
            refusals.append(refusal)

    # 1 · IDENTITY (evaluate's C_IDENTITY)
    missing = [k for k in ("us_market_slug", "payout_event", "period",
                           "fixture") if not cand.get(k)]
    why_not = (list(lane["identity"]) + ["missing:%s" % k for k in missing]
               + ([] if cand.get("side") in (DP.LONG, DP.SHORT)
                  else ["side:%r" % cand.get("side")])
               + (["payout outcome not bound"]
                  if cand.get("payout_binding_ok") is False else []))
    put(DP.C_IDENTITY, not why_not, R_IDENTITY,
        ("%s %s on %s, period %s" % (cand.get("side"),
                                     cand.get("payout_event"),
                                     cand.get("us_market_slug"),
                                     cand.get("period"))
         if not why_not else "identity not established: %s" % why_not),
        lane_refusals=lane["identity"])
    # 2 · THE PAYOUT OUTCOME THE PROBABILITY DESCRIBES
    sel = str(row.get("contract_selection") or "")
    pay = str(row.get("payout_event") or "")
    pev = str(row.get("probability_event") or "")
    comp = bool(row.get("payout_is_complement"))
    if not pay:
        ok, why = False, "no payout event is recorded"
    elif comp:
        ok = pay == "NOT(%s)" % sel and bool(sel)
        why = ("complement: the probability is 1 - p(%s), the contract pays "
               "on %s" % (sel, pay)) if ok else (
            "payout_is_complement but the payout event %r is not NOT(%r)"
            % (pay, sel))
    else:
        ok = (not pev or pev == pay) and (not sel or sel == pay)
        why = ("the probability's event and the payout event are the same "
               "event (%s)" % pay) if ok else (
            "payout event %r differs from the probability's event %r / the "
            "selection %r without a complement" % (pay, pev, sel))
    put("payout_outcome_match", ok, R_OUTCOME, why,
        payout_event=pay or None, probability_event=pev or None,
        contract_selection=sel or None, payout_is_complement=comp,
        payout_event_basis=row.get("payout_event_basis"))
    # 3 · SETTLEMENT TERMS (evaluate's C_SETTLEMENT)
    st = dict(cand.get("settlement") or {})
    s_ok = (st.get("compatibility") == "COMPATIBLE"
            and st.get("overall_established") is True
            and not lane["settlement"])
    put(DP.C_SETTLEMENT, s_ok, R_SETTLEMENT,
        ("venue and book settlement terms compared COMPATIBLE and every "
         "rule established") if s_ok else (
            "settlement not established: compatibility=%s, "
            "overall_established=%s, lane=%s" % (
                st.get("compatibility"), st.get("overall_established"),
                lane["settlement"])),
        compatibility=st.get("compatibility"),
        overall_established=st.get("overall_established"),
        lane_refusals=lane["settlement"])
    # 4 · THE LANE QUALIFIED THE PROBABILITY ITSELF (de-vig, mapping, books)
    put("probability_qualified_by_the_lane", not lane["probability"],
        R_PROBABILITY_UNQUALIFIED,
        ("no probability-stage refusal on the row" if not lane["probability"]
         else "the lane refused the probability: %s" % lane["probability"]),
        lane_refusals=lane["probability"])
    # 5 · VENUE
    put("polymarket_us_contract", cand.get("venue") in (None, "PMUS"),
        R_NOT_PMUS, "venue %s" % cand.get("venue"))
    return {"established": not refusals, "refusals": refusals,
            "checks": checks,
            "lane_refusals_not_blocking": lane["not_blocking"],
            "not_blocking_because": (
                "freshness is re-aged here (Pinnacle) or disclosed (P5 venue "
                "currency); execution, sizing and risk are the paper "
                "session's own; CALIBRATION_ONLY is the row's purpose, not "
                "a contract fact")}


# ═════════════════════════════════════════════════════════════════════
# THE ECONOMICS ON THE OBSERVED BOOK (pure but for the fee function)
# ═════════════════════════════════════════════════════════════════════

def level_edges(levels: list, p: float) -> list:
    out = []
    for lv in levels:
        e = DP.gross_edge(p, lv["price"])
        out.append({"price": lv["price"], "wire": lv["wire"],
                    "qty": float(lv["qty"]),
                    "edge_pp": None if e is None else round(e * 100.0, 9),
                    "clears_5pp": DP.clears(e, MIN_EDGE)})
    return out


def size_within_edge(levels: list, *, p: float, consumed: dict,
                     target_usd: float, cap_usd: float,
                     fee_per_contract_max: float) -> dict:
    """The deepest level whose price still clears 5 pp sets the limit (the
    ladder is best first, so the clearing levels are a prefix); the quantity
    walks the displayed depth not already consumed, up to that limit, capped
    so that qty x limit + max fees stays within min(target, per-order cap).
    Whole contracts."""
    ok = []
    for lv in levels:
        if not DP.clears(DP.gross_edge(p, lv["price"]), MIN_EDGE):
            break
        ok.append(lv)
    if not ok:
        return {"qty": 0, "limit": None, "wire": None,
                "depth_within_limit": 0.0, "why": "NO_LEVEL_CLEARS_5PP"}
    limit = float(ok[-1]["price"])
    depth = sum(max(0.0, float(lv["qty"]) - float(consumed.get(
        SIM._wk(lv["wire"]), 0.0))) for lv in ok)
    budget = min(float(target_usd), float(cap_usd))
    per = limit + float(fee_per_contract_max)
    qty = math.floor(min(depth, budget / per if per > 0 else 0.0) + 1e-9)
    return {"qty": int(max(qty, 0)), "limit": limit, "wire": ok[-1]["wire"],
            "levels_used": len(ok), "depth_within_limit": round(depth, 6),
            "budget_usd": budget, "budget_per_contract_usd": round(per, 9)}


def economics(*, p: float, takes: list, fee_fn, at: float,
              void_states: dict) -> dict:
    """EV of the simulated acquisition: per walked level, gross q (p - px);
    fees by the simulator's fee function; the measured void rate scales the
    gross (a void returns the price; fees are not assumed refunded)."""
    qty = sum(float(t["take"]) for t in takes)
    cost = sum(float(t["take"]) * float(t["price"]) for t in takes)
    fees, fee_rows, fees_ok = 0.0, [], True
    try:
        for t in takes:
            fe = float(L._fee(fee_fn, t["take"], t["price"], at))
            fees += fe
            fee_rows.append({"price": t["price"], "qty": t["take"],
                             "fee_usd": fe})
    except Exception as exc:                                    # noqa: BLE001
        fees_ok = False
        fee_rows.append({"error": "%s: %s" % (type(exc).__name__,
                                              str(exc)[:160])})
    gross = sum(float(t["take"]) * (float(p) - float(t["price"]))
                for t in takes)
    scale = ((1.0 - float(void_states["void_rate"]))
             if void_states.get("applied") else 1.0)
    net = (round(gross * scale - fees, 9) if fees_ok else None)
    vwap = (cost / qty) if qty > 0 else None
    e_vwap = DP.gross_edge(p, vwap) if vwap is not None else None
    return {"qty": round(qty, 6), "acquisition_cost_usd": round(cost, 9),
            "vwap": None if vwap is None else round(vwap, 9),
            "edge_at_vwap_pp": (None if e_vwap is None
                                else round(e_vwap * 100.0, 9)),
            "worst_level_edge_pp": (None if not takes else round(
                DP.gross_edge(p, max(float(t["price"]) for t in takes))
                * 100.0, 9)),
            "fees_usd": round(fees, 9) if fees_ok else None,
            "fees_ok": fees_ok, "fee_basis": fee_rows,
            "fee_function": ("bettor_paper_ledger._fee: the fee function the "
                             "paper simulator charges per fill"),
            "expected_gross_profit_usd": round(gross, 9),
            "void_scale": scale,
            "expected_net_profit_usd": net,
            "net_ev_positive": (net is not None and net > 0)}


def _shortfall(*, pin: dict, best_edge_pp=None, ev=None, depth=None,
               qty=None, book_age=None) -> dict:
    return {"edge_pp": best_edge_pp, "edge_threshold_pp": MIN_EDGE_PP,
            "edge_shortfall_pp": (None if best_edge_pp is None else
                                  round(max(0.0, MIN_EDGE_PP - best_edge_pp),
                                        9)),
            "ev_after_fees_usd": ev, "ev_threshold_usd": 0.0,
            "depth_within_limit": depth, "qty": qty,
            "pinnacle_age_s": pin.get("age_s"),
            "pinnacle_limit_s": pin.get("limit_s"),
            "pinnacle_qualification": pin.get("qualification"),
            "book_age_s": book_age, "book_max_age_s": BOOK_MAX_AGE_S}


def _alternatives(md, *, side: str, p) -> dict:
    out = {"NO_TRADE": {"expected_net_usd": 0.0}}
    other = "SHORT" if side == "LONG" else "LONG"
    lv = SIM.levels_for(md, direction="BUY", holding_side=other)
    if lv["levels"] and p is not None:
        best = lv["levels"][0]
        e = DP.gross_edge(1.0 - float(p), best["price"])
        out["OPPOSITE_SIDE_SAME_MARKET"] = {
            "holding_side": other, "best_price": best["price"],
            "displayed_qty": best["qty"],
            "gross_edge_pp": None if e is None else round(e * 100.0, 9),
            "measure": "1 - p_pinnacle (the complement on the same measure)"}
    else:
        out["OPPOSITE_SIDE_SAME_MARKET"] = {"status": "NO_LEVELS"}
    return out


def qualification_gaps(*, cand: dict) -> list:
    st = dict(cand.get("settlement") or {})
    return [
        {"gap": GAP_NOT_EVIDENCE, "status": "DISCLOSED", "detail": DISCLOSURE},
        {"gap": GAP_NO_MODEL, "status": "NOT_USED",
         "detail": ("no internal model is used; p_internal is NULL and "
                    "Pinnacle is never substituted into it")},
        {"gap": PD.GAP_CALIBRATION, "status": "NOT_CLAIMED",
         "detail": ("the Pinnacle source's calibration is not a "
                    "qualification this benchmark claims or reads (the "
                    "collection worker that measures it is not imported "
                    "here); source version %s"
                    % (cand.get("pinnacle") or {}).get("source_version"))},
        {"gap": PD.GAP_P5, "status": "OPEN",
         "detail": ("book_currency NOT_ESTABLISHED (P5): %s; the valuation "
                    "row is %s and its displayed quote is never the "
                    "execution price" % (BOOK_CURRENCY["basis"],
                                         cand.get("record_purpose")))},
        {"gap": PD.GAP_EXECUTION, "status": "SIMULATED",
         "detail": ("PAPER_SIM_V1: %s; no depth-haircut parameter exists, "
                    "displayed depth only" % ", ".join(
                        SIM.ASSUMPTIONS["marketable"]))},
        {"gap": PD.GAP_SETTLEMENT,
         "status": ("COMPATIBLE" if st.get("compatibility") == "COMPATIBLE"
                    else "OPEN"),
         "detail": "settlement comparison %s" % st.get("compatibility")}]


# ═════════════════════════════════════════════════════════════════════
# THE CONTEXT
# ═════════════════════════════════════════════════════════════════════

CONTEXT_TTL_S = 300.0
_CONTEXT_CACHE: dict = {}


async def _context(conn, ctx: dict) -> dict:
    """Once per pass (or per CONTEXT_TTL_S for the per-valuation hook): the
    measured void rate."""
    if "benchmark" in ctx:
        return ctx["benchmark"]
    key = ctx.get("context_cache_key")
    now = float(ctx["now"])
    if key is not None:
        hit = _CONTEXT_CACHE.get(key)
        if hit is not None and 0.0 <= now - hit["at"] < CONTEXT_TTL_S:
            ctx["benchmark"] = hit["ctx"]
            return ctx["benchmark"]
    ctx["benchmark"] = {"void": await DP.void_measure(conn, through=now)}
    if key is not None:
        _CONTEXT_CACHE.clear()
        _CONTEXT_CACHE[key] = {"at": now, "ctx": ctx["benchmark"]}
    return ctx["benchmark"]


# ═════════════════════════════════════════════════════════════════════
# THE DECISION
# ═════════════════════════════════════════════════════════════════════

async def decide_one(conn, ctx: dict, row: dict) -> dict:
    """ONE BENCHMARK DECISION, persisted first; an ENTER then submits ONE
    paper order naming it (the simulator fills it after the delay)."""
    cfg = ctx["config"]
    ent = cfg["entry"]
    sim_cfg = cfg["simulator"]
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    fee_fn = ctx.get("fee_fn")
    bctx = await _context(conn, ctx)
    row = dict(row)
    cand = DP.candidate_from_row(row)
    side = PD.holding_side_of(cand.get("side"))
    did = decision_id_for(ctx["session_id"], cand["valuation_id"])
    cat = await DP.catalogue_row(conn, cand.get("us_market_slug"))
    fxr = await DP.fixture_row(conn, cand.get("condition_id"))
    label = dict(DP.instrument_label(cand, catalogue_row=cat,
                                     fixture_row=fxr), **STRATEGY_LABEL)
    refusals: list = []
    match = contract_match(cand, row)
    refusals.extend(match["refusals"])
    real = DP._realism(cand, cat)
    if real["status"] == DP.FAIL:
        refusals.append(R_NOT_REAL)
    # ── THE PROBABILITY: stored, oriented, re-aged at THIS instant ─────
    pin = PD._pinnacle(cand, at=at, max_age=float(ent["pinnacle_max_age_s"]))
    pin.update(decided_via=ctx.get("decided_via") or PD.DECIDED_VIA_PASS,
               decided_at=at, valuation_decided_at=cand.get("decided_at"),
               decision_lag_after_valuation_s=(
                   None if cand.get("decided_at") is None
                   else round(at - float(cand["decided_at"]), 3)),
               strategy=STRATEGY, probability_is=(
                   "the stored de-vigged Pinnacle probability of the event "
                   "this contract pays on (external_valuations.probability, "
                   "oriented once by the lane)"),
               contract_match=match, real_event=real,
               valuation_record_purpose=cand.get("record_purpose"),
               displayed_quote_used_as_price=False)
    if pin.get("refusal"):
        refusals.append(pin["refusal"])
    p = pin.get("p")
    obs, md, levels, edges = None, None, [], []
    sized: dict = {"qty": 0, "limit": None, "wire": None}
    econ: dict | None = None
    book_age = None
    if not refusals:
        # THE BOOK IS READ ONLY FOR A CANDIDATE THAT COULD STILL ENTER.
        if ctx["books_read"] >= int(cfg["cadence"]["max_book_reads_per_pass"]):
            return {"deferred": True, "why": "BOOK_READ_BUDGET"}
        got = await ctx["market_data"].read_book(cand["us_market_slug"])
        ctx["books_read"] += 1
        obs = await SIM.record_book(conn, slug=cand["us_market_slug"],
                                    read=got, source="PAPER_MARKET_DATA_"
                                    "CLIENT", read_basis=STRATEGY)
        md = obs.get("market_data")
        lv = SIM.levels_for(md, direction="BUY", holding_side=side)
        levels = lv["levels"]
        book_age = round(max(0.0, float(clock()) - float(obs["observed_at"])),
                         3)
        if obs.get("error") or not levels:
            refusals.append(R_NO_BOOK)
        elif book_age > BOOK_MAX_AGE_S:
            refusals.append(R_BOOK_NOT_CURRENT)
        else:
            edges = level_edges(levels, p)
            consumed = await SIM._consumed(conn, cand["us_market_slug"],
                                           lv["side"], obs["obs_id"])
            sized = size_within_edge(
                levels, p=p, consumed=consumed,
                target_usd=float(ent["target_order_usd"]),
                cap_usd=float(cfg["risk"]["per_order_cap_usd"]),
                fee_per_contract_max=float(L.max_fee_for(
                    1, 0.5, at=at, fee_fn=fee_fn)))
            if not edges[0]["clears_5pp"]:
                refusals.append(R_EDGE)
            elif sized["qty"] < 1:
                refusals.append(R_NO_QTY)
            else:
                walk = SIM.walk(levels, consumed=consumed,
                                limit=sized["limit"], qty=sized["qty"],
                                direction="BUY", allow_partial=True)
                states = DP.settlement_states(bctx["void"],
                                              void_refunds_price=True)
                econ = dict(economics(p=p, takes=walk["takes"],
                                      fee_fn=fee_fn, at=at,
                                      void_states=states),
                            settlement_states=states,
                            walk=[{"price": t["price"], "wire": t["wire"],
                                   "take": t["take"],
                                   "edge_pp": round(DP.gross_edge(
                                       p, t["price"]) * 100.0, 9)}
                                  for t in walk["takes"]])
                if not econ["fees_ok"]:
                    refusals.append(R_FEES)
                elif not econ["net_ev_positive"]:
                    refusals.append(R_NET)
    verdict = DP.ENTER if not refusals else DP.REFUSE
    best_edge = edges[0]["edge_pp"] if edges else None
    short = _shortfall(pin=pin, best_edge_pp=best_edge,
                       ev=(econ or {}).get("expected_net_profit_usd"),
                       depth=sized.get("depth_within_limit"),
                       qty=sized.get("qty"), book_age=book_age)
    economics_rec = {
        "strategy": STRATEGY, "version": VERSION, "disclosure": DISCLOSURE,
        "probability": p, "probability_basis": "PINNACLE_ONLY_DEVIGGED_STORED",
        "threshold_edge_pp": MIN_EDGE_PP,
        "edge_rule": "p_pinnacle - level price >= 0.05 at EVERY level used",
        "ev_rule": "expected net profit after the simulator's fees > 0",
        "levels": edges[:10], "best_level_edge_pp": best_edge,
        "limit_price": sized.get("limit"),
        "levels_used": sized.get("levels_used"),
        "depth_within_limit": sized.get("depth_within_limit"),
        "depth_basis": ("DISPLAYED depth only, net of paper liquidity "
                        "already consumed at this observation; PAPER_SIM_V1 "
                        "has no depth-haircut parameter"),
        "budget_usd": sized.get("budget_usd"),
        "proposed_qty": sized.get("qty"),
        "latency": {"decision_to_execution_delay_s": float(
            sim_cfg["decision_to_execution_delay_s"]),
            "fill_rule": sim_cfg.get("marketable")},
        "book_age_s": book_age, "book_max_age_s": BOOK_MAX_AGE_S,
        "book_currency": BOOK_CURRENCY,
        "acquisition": econ, "shortfall": short,
        "refusals": refusals}
    conditions = [
        {"condition": "contract_outcome_settlement_match",
         "passed": match["established"], "refusals": match["refusals"]},
        {"condition": "pinnacle_fresh_at_the_decision_instant",
         "passed": pin.get("qualified") is True,
         "value": pin.get("age_s"), "threshold": pin.get("limit_s"),
         "units": "seconds", "refusal": pin.get("refusal")},
        {"condition": "current_paper_book_with_depth",
         "passed": (None if obs is None else
                    bool(levels) and book_age is not None
                    and book_age <= BOOK_MAX_AGE_S),
         "value": book_age, "threshold": BOOK_MAX_AGE_S, "units": "seconds"},
        {"condition": "edge_at_least_5pp_at_every_level_used",
         "passed": None if not edges else bool(edges[0]["clears_5pp"]),
         "value": best_edge, "threshold": MIN_EDGE_PP,
         "units": "percentage points"},
        {"condition": "positive_ev_after_fees",
         "passed": None if econ is None else econ["net_ev_positive"],
         "value": (econ or {}).get("expected_net_profit_usd"),
         "threshold": 0.0, "units": "USD"}]
    policy_decision = {
        "strategy": STRATEGY, "policy_version": VERSION,
        "disclosure": DISCLOSURE, "p_internal": None, "p_blended": None,
        "p_pinnacle": p, "conditions": conditions,
        "gross_edge_pp": best_edge,
        "edge_at_vwap_pp": (econ or {}).get("edge_at_vwap_pp"),
        "net_expected_profit_usd": (econ or {}).get(
            "expected_net_profit_usd"),
        "fees_usd": (econ or {}).get("fees_usd"),
        "shortfall": short, "book_currency": BOOK_CURRENCY,
        "admitted": verdict == DP.ENTER,
        "refusal": refusals[0] if refusals else None,
        "refusals": refusals}
    gaps = qualification_gaps(cand=cand)
    optimistic = (SIM.optimistic_fill(md, direction="BUY", holding_side=side,
                                      qty=sized["qty"], limit=sized["limit"])
                  if verdict == DP.ENTER else None)
    internal_rec = {"available": False, "p": None, "strategy": STRATEGY,
                    "reason": ("PINNACLE_ONLY_PAPER_BENCHMARK uses no "
                               "internal model by design; Pinnacle is never "
                               "substituted into this field (it is in "
                               "p_pinnacle / pinnacle)")}
    book = None if obs is None else {
        "book_obs_id": obs["obs_id"], "observed_at": obs["observed_at"],
        "observed_at_is": "OUR_RECEIPT_INSTANT",
        "age_at_decision_s": book_age, "error": obs.get("error"),
        "side_consumed": SIM.side_consumed("BUY", side or "LONG"),
        "levels": levels[:10], "depth_levels": len(levels),
        "displayed_depth": round(sum(float(x["qty"]) for x in levels), 6),
        "book_currency": BOOK_CURRENCY,
        "basis": "OBSERVED_PAPER_BOOK_LEVELS_NOT_THE_VALUATION_QUOTE"}
    rec = {"decision_id": did, "verdict": verdict, "strategy": STRATEGY,
           "refusal": refusals[0] if refusals else None,
           "refusals": refusals, "shortfall": short}
    inserted = await conn.fetchval(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, intent, "
        " fixture, label, verdict, refusal, refusals, p_internal, "
        " internal_model, p_pinnacle, pinnacle, p_blended, book_obs_id, "
        " book, proposed_qty, limit_price, economics, qualification_gaps, "
        " policy_version, policy_decision, alternatives, optimistic, "
        " simulator_version, strategy) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,"
        " $10::jsonb,$11,$12,$13,NULL,$14::jsonb,$15,$16::jsonb,NULL,$17,"
        " $18::jsonb,$19,$20,$21::jsonb,$22::jsonb,$23,$24::jsonb,$25::jsonb,"
        " $26::jsonb,$27,$28) ON CONFLICT DO NOTHING RETURNING decision_id",
        did, ctx["session_id"], ctx["account_id"], L._ts(at),
        cand["valuation_id"], cand.get("us_market_slug"), side,
        cand.get("side"), cand.get("fixture"),
        json.dumps(label, default=str), verdict, rec["refusal"], refusals,
        json.dumps(internal_rec, default=str), p,
        json.dumps(pin, default=str),
        None if obs is None else obs["obs_id"],
        None if book is None else json.dumps(book, default=str),
        (None if not sized.get("qty") else L.D(sized["qty"])),
        (None if sized.get("limit") is None else L.D(sized["limit"])),
        json.dumps(economics_rec, default=str), json.dumps(gaps, default=str),
        VERSION, json.dumps(policy_decision, default=str),
        json.dumps(_alternatives(md, side=side or "LONG", p=p), default=str),
        None if optimistic is None else json.dumps(optimistic, default=str),
        cfg["simulator_version"], STRATEGY)
    if inserted is None:
        return dict(rec, duplicate=True)
    if verdict != DP.ENTER:
        return rec
    # ── ONLY NOW, THE PAPER ORDER ─────────────────────────────────────
    delay = float(sim_cfg["decision_to_execution_delay_s"])
    order = {"idempotency_key": "%s:ENTRY" % did,
             "account_id": ctx["account_id"],
             "session_id": ctx["session_id"],
             "group_id": group_id_for(did), "role": "ENTRY",
             "direction": "BUY", "holding_side": side,
             "intent": cand.get("side"),
             "us_market_slug": cand["us_market_slug"],
             "fixture": cand.get("fixture"), "label": label,
             "order_type": ent["order_type"],
             "time_in_force": ent["time_in_force"],
             "allow_partial": bool(ent["allow_partial"]),
             "qty": sized["qty"], "limit_price": sized["limit"],
             "wire_price": sized["wire"], "decision_id": did,
             "decided_at": at, "eligible_at": at + delay,
             "expires_at": at + float(sim_cfg["marketable_ttl_s"]),
             "simulator_version": cfg["simulator_version"],
             "strategy": STRATEGY}
    got = await L.submit_order(conn, order, caps=cfg["risk"],
                               fee_fn=fee_fn, now=at)
    rec["order"] = {k: got.get(k) for k in ("ok", "refusal", "duplicate")}
    if got.get("ok"):
        rec["order_id"] = got["order"]["order_id"]
        rec["eligible_at"] = at + delay
    else:
        rec["order_refusal"] = got.get("refusal")
        await PD._finding(conn, ctx, kind=R_ORDER_REFUSED, subject=did,
                          detail={"refusal": got.get("refusal"),
                                  "decision_id": did, "at": at,
                                  "strategy": STRATEGY,
                                  "disclosure": DISCLOSURE,
                                  **{k: v for k, v in got.items()
                                     if k not in ("ok",)}})
    return rec


# ═════════════════════════════════════════════════════════════════════
# THE PASS STEP AND THE PER-VALUATION HOOK
# ═════════════════════════════════════════════════════════════════════

async def step(conn, ctx: dict) -> dict:
    """THE BENCHMARK'S PAPER STEP (after Derek's, before the delayed fill
    step, which simulates its orders too)."""
    en = await enablement(conn)
    out: dict[str, Any] = {"strategy": STRATEGY, "enabled": en["enabled"],
                           "decisions_recorded": 0, "orders_submitted": 0,
                           "verdicts": {}, "refusals": {}, "deferred": 0}
    if not en["enabled"]:
        return dict(out, refusal=en["refusal"])
    cfg = ctx["config"]
    at = float(ctx["now"])
    rows = [dict(r) for r in await conn.fetch(
        CANDIDATES_SQL, EXPERIMENT_ID,
        at - float(cfg["entry"]["valuation_lookback_s"]), at + 1.0,
        ctx["session_id"], int(cfg["cadence"]["max_decisions_per_pass"]),
        STRATEGY)]
    out["candidates"] = len(rows)
    ctx.setdefault("pending_entries", [])
    for row in rows:
        if time.monotonic() > ctx["deadline"]:
            out["budget_exhausted"] = True
            break
        try:
            rec = await decide_one(conn, ctx, row)
        except Exception as exc:                                # noqa: BLE001
            k = "BENCHMARK_DECISION:%s" % type(exc).__name__
            out.setdefault("errors", {})[k] = str(exc)[:200]
            continue
        if rec.get("deferred"):
            out["deferred"] += 1
            continue
        if rec.get("duplicate"):
            out["already_recorded"] = out.get("already_recorded", 0) + 1
            continue
        out["decisions_recorded"] += 1
        out["verdicts"][rec["verdict"]] = out["verdicts"].get(
            rec["verdict"], 0) + 1
        if rec.get("refusal"):
            out["refusals"][rec["refusal"]] = out["refusals"].get(
                rec["refusal"], 0) + 1
        if rec.get("order_id"):
            out["orders_submitted"] += 1
            ctx["pending_entries"].append(rec)
    return out


async def decide_for_hook(conn, ctx: dict, row: dict, *,
                          timeout_s: float) -> dict:
    """The per-valuation hook's benchmark decision: guarded, bounded, never
    raises (CancelledError excepted)."""
    try:
        en = await enablement(conn)
        if not en["enabled"]:
            return {"decided": False, "why": en["refusal"]}
        ctx = dict(ctx, deadline=time.monotonic() + float(timeout_s))
        ctx.pop("benchmark", None)
        rec = await asyncio.wait_for(decide_one(conn, ctx, row), timeout_s)
        return dict({k: rec.get(k) for k in (
            "decision_id", "verdict", "refusal", "order_id", "duplicate",
            "deferred", "strategy")}, decided=not rec.get("deferred"))
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        return {"decided": False, "strategy": STRATEGY,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


# ═════════════════════════════════════════════════════════════════════
# XAVIER: THE SAME POLICY FOR THE LIFE OF THE POSITION
# ═════════════════════════════════════════════════════════════════════

async def group_strategy(conn, group_id: str) -> str:
    """The strategy of a paper group: its entry order's (migration 182). A
    group never switches policy."""
    s = await conn.fetchval(
        "SELECT strategy FROM paper_orders WHERE group_id=$1 "
        "   AND role='ENTRY' ORDER BY created_at LIMIT 1", group_id)
    return s or TWO_MODEL_STRATEGY


async def xavier_measure(conn, ctx: dict, *, pos: dict) -> dict:
    """P(the held side pays) for a BENCHMARK position, on the benchmark's
    own measure -- the de-vigged Pinnacle probability alone, for the same
    contract and payout outcome -- never the two-model blend:

      PINNACLE_ONLY_CURRENT     a reading for the same contract within the
                                lookback that is fresh under the 30 s rule
      PINNACLE_ONLY_LATEST      the latest such reading, older (stale, said)
      ENTRY_TIME_MEASURE        the entry decision's p_pinnacle (stale, said)
    """
    c = ctx.get("clock")
    at = float(c()) if c else float(ctx["now"])
    ent = ctx["config"]["entry"]
    lookback = float(ent["valuation_lookback_s"])
    max_age = float(ent["pinnacle_max_age_s"])
    intent = DP.LONG if pos["holding_side"] == "LONG" else DP.SHORT
    d = await conn.fetchrow(
        "SELECT d.decision_id, d.p_pinnacle, d.decided_at, d.valuation_id "
        "  FROM paper_decisions d JOIN paper_orders o "
        "    ON o.decision_id = d.decision_id "
        " WHERE o.group_id=$1 AND o.role='ENTRY' AND d.strategy=$2 LIMIT 1",
        pos["group_id"], STRATEGY)
    base = {"strategy": STRATEGY, "p_internal": None,
            "internal_model": {"available": False},
            "void_applied": False, "disclosure": DISCLOSURE}
    contract = None
    if d is not None and d["valuation_id"] is not None:
        contract = await conn.fetchrow(
            "SELECT payout_event, payout_is_complement FROM "
            " external_valuations WHERE id=$1", int(d["valuation_id"]))
    if contract is not None:
        v = await conn.fetchrow(
            "SELECT id, probability, observed_at FROM external_valuations "
            " WHERE us_market_slug=$1 AND buy_intent=$2 "
            "   AND probability IS NOT NULL AND payout_event=$3 "
            "   AND payout_is_complement=$4 "
            "   AND decided_at > to_timestamp($5) "
            " ORDER BY decided_at DESC LIMIT 1", pos["us_market_slug"],
            intent, contract["payout_event"],
            bool(contract["payout_is_complement"]), at - lookback)
        if v is not None:
            obs = L._epoch(v["observed_at"])
            age = None if obs is None else round(at - obs, 3)
            fresh = age is not None and age <= max_age
            return dict(base, p=float(v["probability"]),
                        source=("PINNACLE_ONLY_CURRENT" if fresh
                                else "PINNACLE_ONLY_LATEST"),
                        p_pinnacle=float(v["probability"]),
                        pinnacle_at=obs, pinnacle_age_s=age,
                        pinnacle_limit_s=max_age, valuation_id=v["id"],
                        stale=not fresh)
    if d is not None and d["p_pinnacle"] is not None:
        return dict(base, p=float(d["p_pinnacle"]),
                    source="ENTRY_TIME_MEASURE",
                    at=L._epoch(d["decided_at"]), stale=True,
                    why=("no Pinnacle reading for this contract within the "
                         "lookback; the entry decision's p_pinnacle is used "
                         "and labelled stale"))
    return dict(base, p=None, source=None, stale=True,
                why="NO_SETTLEMENT_MEASURE_FOR_THIS_POSITION")


# ═════════════════════════════════════════════════════════════════════
# AUDREY: THE BENCHMARK'S OWN SECTION AND FILL AUDIT
# ═════════════════════════════════════════════════════════════════════

async def has_records(conn, account_id: str) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM paper_decisions WHERE "
            " account_id=$1 AND strategy=$2)", account_id, STRATEGY))
    except Exception:                                           # noqa: BLE001
        return False


async def report_section(conn, *, account_id: str, t0, t1) -> dict:
    """THE BENCHMARK'S PERFORMANCE ROWS FOR THE REPORT DAY, apart from the
    two-model strategy's, each labelled with the strategy."""
    dec = await conn.fetch(
        "SELECT verdict, coalesce(refusal, 'ENTER') AS reason, count(*) AS n,"
        "       count(DISTINCT us_market_slug) AS markets "
        "  FROM paper_decisions WHERE account_id=$1 AND strategy=$2 "
        "   AND decided_at >= $3 AND decided_at < $4 GROUP BY 1, 2 "
        " ORDER BY 3 DESC", account_id, STRATEGY, t0, t1)
    q = await conn.fetchrow(
        "SELECT count(*) AS n, "
        "       sum((policy_decision->>'net_expected_profit_usd')::float8) "
        "         AS ev, avg((policy_decision->>'gross_edge_pp')::float8) "
        "         AS edge FROM paper_decisions WHERE account_id=$1 "
        "   AND strategy=$2 AND verdict='ENTER' AND decided_at >= $3 "
        "   AND decided_at < $4", account_id, STRATEGY, t0, t1)
    fills = await conn.fetchrow(
        "SELECT count(*) AS n, coalesce(sum(f.gross_usd), 0) AS gross, "
        "       coalesce(sum(f.fee_usd), 0) AS fees, "
        "       coalesce(sum(f.qty), 0) AS qty, "
        "       count(DISTINCT f.us_market_slug) AS markets "
        "  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id "
        " WHERE f.account_id=$1 AND o.strategy=$2 AND f.direction='BUY' "
        "   AND f.filled_at >= $3 AND f.filled_at < $4", account_id,
        STRATEGY, t0, t1)
    orders = await conn.fetch(
        "SELECT state, count(*) AS n FROM paper_orders WHERE account_id=$1 "
        "   AND strategy=$2 AND created_at >= $3 AND created_at < $4 "
        " GROUP BY 1", account_id, STRATEGY, t0, t1)
    handoffs = await conn.fetchval(
        "SELECT count(*) FROM paper_handoffs WHERE account_id=$1 "
        "   AND strategy=$2", account_id, STRATEGY)
    groups = {r["group_id"] for r in await conn.fetch(
        "SELECT DISTINCT group_id FROM paper_orders WHERE account_id=$1 "
        "   AND strategy=$2", account_id, STRATEGY)}
    pos = [p for p in await L.positions(conn, account_id,
                                        include_closed=True)
           if p["group_id"] in groups]
    return {
        "strategy": STRATEGY, "version": VERSION, "disclosure": DISCLOSURE,
        "decisions": [{"strategy": STRATEGY, "verdict": r["verdict"],
                       "reason": r["reason"], "decisions": int(r["n"]),
                       "markets": int(r["markets"])} for r in dec],
        "entries_at_decision_time": {
            "strategy": STRATEGY, "entries": int(q["n"] or 0),
            "expected_net_usd": q["ev"], "mean_best_level_edge_pp": q["edge"],
            "basis": "what was known at each decision; not scored on "
                     "outcomes"},
        "orders_by_state": {r["state"]: int(r["n"]) for r in orders},
        "fills": {"strategy": STRATEGY, "fills": int(fills["n"]),
                  "acquisition_usd": round(float(fills["gross"])
                                           + float(fills["fees"]), 6),
                  "fees_usd": float(fills["fees"]),
                  "qty": float(fills["qty"]),
                  "distinct_markets": int(fills["markets"])},
        "handoffs_to_xavier_total": int(handoffs or 0),
        "positions": {"strategy": STRATEGY, "count": len(pos),
                      "open": sum(1 for p in pos if p["open_qty"] > 1e-9),
                      "realized_pnl_usd": round(sum(
                          p["realized_pnl_usd"] for p in pos), 6),
                      "open_cost_basis_usd": round(sum(
                          p["cost_basis_usd"] for p in pos), 6)},
        "included_in_account_totals": True,
        "one_ledger": "paper_ledger (the benchmark shares the account's "
                      "single cash ledger; no separate funding)"}


async def audit_fills(conn, actx: dict, finding) -> list:
    """AUDREY'S CHECK OF EVERY BENCHMARK ENTRY THAT FILLED: each simulated
    fill has its one ledger FILL entry, and the group has been handed to
    Xavier. One INFO finding per group (WARNING when either is missing)."""
    out = []
    rows = await conn.fetch(
        "SELECT o.group_id, o.order_id, o.decision_id, o.filled_qty, "
        "       (SELECT count(*) FROM paper_fills f "
        "         WHERE f.order_id = o.order_id) AS fills, "
        "       (SELECT count(*) FROM paper_ledger l JOIN paper_fills f "
        "          ON f.fill_id = l.fill_id WHERE f.order_id = o.order_id "
        "         AND l.kind = 'FILL') AS ledger_fills, "
        "       (SELECT coalesce(-sum(l.cash_delta_usd), 0) FROM paper_ledger l"
        "         WHERE l.order_id = o.order_id AND l.kind = 'FILL') AS debit,"
        "       EXISTS (SELECT 1 FROM paper_handoffs h WHERE "
        "         h.group_id = o.group_id AND h.strategy = o.strategy) "
        "         AS handed "
        "  FROM paper_orders o WHERE o.account_id=$1 AND o.strategy=$2 "
        "   AND o.role='ENTRY' AND o.filled_qty > 0", actx["account_id"],
        STRATEGY)
    for r in rows:
        ok = int(r["fills"]) == int(r["ledger_fills"]) and bool(r["handed"])
        out.append(await finding(
            conn, actx, kind="PINNACLE_ONLY_PAPER_BENCHMARK_FILL_AUDITED",
            subject=r["group_id"], severity="INFO" if ok else "WARNING",
            detail={"strategy": STRATEGY, "disclosure": DISCLOSURE,
                    "order_id": r["order_id"],
                    "decision_id": r["decision_id"],
                    "filled_qty": float(r["filled_qty"]),
                    "simulated_fills": int(r["fills"]),
                    "ledger_fill_entries": int(r["ledger_fills"]),
                    "ledger_fill_debits_usd": float(r["debit"]),
                    "handed_to_xavier": bool(r["handed"]),
                    "passed": ok},
            scope="%s:%s" % (r["filled_qty"], r["handed"])))
    return out


def describe() -> dict:
    return {"strategy": STRATEGY, "version": VERSION, "env_flag": ENV_FLAG,
            "control_key": CONTROL_KEY, "min_edge_pp": MIN_EDGE_PP,
            "book_max_age_s": BOOK_MAX_AGE_S, "disclosure": DISCLOSURE,
            "book_currency": BOOK_CURRENCY}
