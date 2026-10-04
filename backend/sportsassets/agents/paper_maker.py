"""PINNACLE_COMPLETED_GAME_MAKER_PAPER -- THE MAKER-ENTRY PAPER POLICY.

OWNER-AUTHORIZED 2026-10-01, PAPER ONLY, its own strategy key and version
(PINNACLE_COMPLETED_GAME_MAKER_PAPER_V1). The investment policy's entry
requirements, reached by RESTING a bid instead of taking the ask:

  MATCH       the completed-game match (`paper_benchmark.completed_game_
              match`), a fresh Pinnacle reading (30 s rule, re-aged at the
              decision), a current observed book -- exactly as the taker
              policy.
  THRESHOLD   the completed-game policy's ACTIVE parameter version
              (`paper_benchmark.cg_parameters`): never below the owner's
              0.5 pp floor, fail-closed to it.
  PRICE       the HIGHEST whole-cent resting price L strictly below the
              current best ask (a resting bid that would cross is not a
              maker order; the venue's post-only flag rejects it) such that
                  p_pinnacle - L >= the threshold   AND
                  p_pinnacle - L - fee_per_contract(L) > 0.
              The highest such L maximises the chance of a fill while both
              conditions hold at the price actually paid.
  FEES        THE VENUE SCHEDULE IS VERIFIED, THE REBATE IS NOT ASSUMED.
              docs.polymarket.us/fees (as recorded in bettor_fee_schedule
              and confirmed against two independent public readings on
              2026-10-01) publishes taker theta 0.0695 and a maker REBATE
              theta -0.0125; the two readings disagree on the taker
              effective date (2026-09-17 vs 2026-09-25), both before today.
              PUBLISHED is not VERIFIED_APPLIED to this account (no settled
              statement shows a maker rebate credited), so every simulated
              maker fill is charged the TAKER fee and the rebate is recorded
              only as an unverified sensitivity.
  ORDER       post-only, good-till-date (the venue supports GOOD_TILL_DATE
              with goodTillTime and post-only limit orders, pmus.place),
              persisted at once with Derek's price, size, rationale, expiry
              and cancellation conditions. AN ORDER IS NOT A FILL: it
              reserves limit x qty + the maximum fee on the one ledger, under
              the account lock, so no other strategy can commit that cash.
  FILLS       PAPER_SIM_V1's resting rule, unchanged and conservative: a
              fill only when a book observed AFTER placement shows liquidity
              STRICTLY better than L (the price traded through our level),
              and only the part beyond the queue ahead at placement
              (displayed size at-or-better on our side plus earlier paper
              orders); partial fills allowed; a touch is never a fill; a
              missing update is never a fill.
  CANCEL      the order is cancelled (cancel-pending, confirmed on the next
              simulator step) when: a newer Pinnacle reading for the same
              contract no longer clears the threshold or the after-fee rule
              at L; no reading younger than MAX_UNVERIFIED_S exists; the
              policy's kill switch is off; or the good-till time passes.
              Settled markets release their open orders (Xavier's step).

ECONOMICS ARE CONDITIONAL on ordinary completion and ON BEING FILLED. A
resting bid fills preferentially when the price moves against it (adverse
selection); that effect is UNMEASURED and disclosed, never assumed away.
"""
from __future__ import annotations

import json
import math
from typing import Any

from .. import bettor_paper_ledger as L
from .. import bettor_paper_simulator as SIM
from . import derek_policy as DP
from . import paper_benchmark as PB
from . import paper_derek as PD

POL = PB.MAKER_POLICY
STRATEGY = PB.MAKER_STRATEGY
VERSION = PB.MAKER_VERSION
DISCLOSURE = PB.MAKER_DISCLOSURE

MAKER_TTL_S = 900.0                 # one collection cycle
MAX_UNVERIFIED_S = 1200.0           # no Pinnacle reading this young -> cancel
CENT = 0.01

FEE_BASIS = {
    "charged": "TAKER schedule (theta 0.0695 x C x p x (1-p)) on every "
               "simulated maker fill",
    "maker_rebate_published": "theta -0.0125 (a rebate), docs.polymarket.us"
                              "/fees as recorded in bettor_fee_schedule",
    "maker_rebate_status": "PUBLISHED_NOT_VERIFIED_APPLIED -- not assumed",
    "effective_date_disagreement": ("public readings name 2026-09-17 and "
                                    "2026-09-25 for the 0.0695 taker theta; "
                                    "both precede this decision"),
    "primary_document_access": ("docs.polymarket.us is not reachable from "
                                "the build environment; verified against "
                                "the recorded schedule and two independent "
                                "public readings")}

R_NO_PRICE = "NO_RESTING_PRICE_BELOW_THE_ASK_CLEARS_THE_THRESHOLD_AND_FEES"
R_NO_ASK = "NO_ASK_TO_REST_BELOW"
R_ALREADY_RESTING = "A_MAKER_ENTRY_ORDER_ALREADY_RESTS_ON_THIS_FIXTURE"
C_EDGE_GONE = "EDGE_NO_LONGER_CLEARS_AT_THE_RESTING_PRICE"
C_UNVERIFIED = "NO_RECENT_PINNACLE_READING_TO_VERIFY_THE_EDGE"
C_SWITCHED_OFF = "MAKER_POLICY_SWITCHED_OFF"

CANCEL_CONDITIONS = [
    {"condition": C_EDGE_GONE,
     "rule": ("a newer Pinnacle reading for the same contract and payout "
              "outcome gives p - L below the active threshold, or "
              "p - L - fee_per_contract(L) <= 0")},
    {"condition": C_UNVERIFIED,
     "rule": "no Pinnacle reading for the contract within %d s" %
             int(MAX_UNVERIFIED_S)},
    {"condition": C_SWITCHED_OFF,
     "rule": "the policy's kill-switch row is off"},
    {"condition": SIM.R_GTD_EXPIRED,
     "rule": "the good-till time (placement + %d s) passes" %
             int(MAKER_TTL_S)},
    {"condition": "MARKET_SETTLED",
     "rule": "the market settles (released by the settlement step)"}]


def resting_price(levels: list, *, p: float, min_edge: float,
                  fee_pc) -> dict:
    """Pure but for `fee_pc(price) -> fee per contract`. `levels` is the
    CONSUMED side in our cost space, best (cheapest) first."""
    if not levels:
        return {"limit": None, "refusal": R_NO_ASK}
    ask = float(levels[0]["price"])
    tried = 0
    # one cent below the ask, on the adapter's cent grid
    cents = int(round(ask * 100.0)) - 1
    while cents >= 1:
        px = cents / 100.0
        tried += 1
        e = DP.gross_edge(p, px)
        fpc = float(fee_pc(px))
        if DP.clears(e, min_edge) and e - fpc > DP.EDGE_TOLERANCE_PP:
            return {"limit": px, "best_ask": ask,
                    "improvement_vs_ask_usd": round(ask - px, 6),
                    "gross_edge_pp": round(e * 100.0, 9),
                    "fee_per_contract_usd": round(fpc, 9),
                    "net_edge_pp": round((e - fpc) * 100.0, 9),
                    "prices_tried": tried, "refusal": None}
        cents -= 1
    return {"limit": None, "best_ask": ask, "prices_tried": tried,
            "refusal": R_NO_PRICE}


async def decide_one(conn, ctx: dict, row: dict, pol=None) -> dict:
    """ONE MAKER-ENTRY DECISION, persisted first; an ENTER then places ONE
    resting paper order (no fill is implied)."""
    cfg = ctx["config"]
    ent = cfg["entry"]
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    fee_fn = ctx.get("fee_fn")
    for k in ("last_book_source", "last_book_age_s", "last_book_cooldown_s"):
        ctx.pop(k, None)
    row = dict(row)
    cand = DP.candidate_from_row(row)
    side = PD.holding_side_of(cand.get("side"))
    did = PB.decision_id_for(ctx["session_id"], cand["valuation_id"], POL)
    cat = await DP.catalogue_row(conn, cand.get("us_market_slug"))
    fxr = await DP.fixture_row(conn, cand.get("condition_id"))
    label = dict(DP.instrument_label(cand, catalogue_row=cat,
                                     fixture_row=fxr),
                 strategy=STRATEGY, disclosure=DISCLOSURE,
                 book_currency="NOT_ESTABLISHED", policy_version=VERSION,
                 economics_label=PB.ECONOMICS_LABEL)
    refusals: list = []
    params = await PB.cg_parameters(conn, ctx)
    min_edge_pp = max(float(params["values"]["min_gross_edge_pp"]),
                      PB.CG_MIN_EDGE_PP_V2)
    min_edge = min_edge_pp / 100.0
    match = PB.completed_game_match(cand, row, catalogue=cat)
    refusals.extend(match["refusals"])
    real = DP._realism(cand, cat)
    if real["status"] == DP.FAIL:
        refusals.append(PB.R_NOT_REAL)
    pin = PD._pinnacle(cand, at=at, max_age=float(ent["pinnacle_max_age_s"]))
    pin.update(decided_via=ctx.get("decided_via") or PD.DECIDED_VIA_PASS,
               decided_at=at, strategy=STRATEGY, contract_match=match,
               real_event=real, displayed_quote_used_as_price=False)
    if pin.get("refusal"):
        refusals.append(pin["refusal"])
    # R30A: an NFL line's P(win | no tie) is valued as the venue contract
    # (tie pays 0.50) at the worst cited tie rate, before any edge or EV.
    conv_refusal = PB.apply_venue_conversion(pin, match)
    if conv_refusal and conv_refusal not in refusals:
        refusals.append(conv_refusal)
    cross = await PB.cross_strategy_exposure(
        conn, account_id=ctx["account_id"], strategy=STRATEGY,
        slug=cand.get("us_market_slug"), fixture=cand.get("fixture"))
    pin["cross_strategy_exposure"] = cross
    if cross["held"]:
        refusals.append(PB.R_CROSS_STRATEGY)
    from .. import bettor_paper_limits as LIMITS
    if not refusals and not LIMITS.uses_owner_policy(ctx["account_id"]) and await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM paper_orders WHERE account_id=$1 "
            "   AND strategy=$2 AND role='ENTRY' AND state = ANY($3::text[]) "
            "   AND (us_market_slug=$4 OR ($5::text IS NOT NULL AND "
            "        fixture=$5)))", ctx["account_id"], STRATEGY,
            list(L.OPEN_STATES), cand.get("us_market_slug"),
            cand.get("fixture")):
        # ONE LIVE MAKER ENTRY PER FIXTURE: a newer valuation never stacks a
        # second resting bid; the standing one is re-checked every pass and
        # cancelled when its edge goes, after which a new one may be placed
        refusals.append(R_ALREADY_RESTING)
    p = pin.get("p")
    obs, md, levels, price = None, None, [], {}
    qty, econ, book_age, queue = 0, None, None, None
    if not refusals:
        bk = await PB.book_for(conn, ctx, cand["us_market_slug"],
                               basis=STRATEGY)
        if bk.get("deferred"):
            return {"deferred": True, "why": "BOOK_READ_BUDGET"}
        got, obs = bk["got"], bk["obs"]
        md = obs.get("market_data")
        lv = SIM.levels_for(md, direction="BUY", holding_side=side)
        levels = lv["levels"]
        book_age = round(max(0.0, float(clock()) - float(obs["observed_at"])),
                         3)
        ctx["last_book_source"] = PB.book_source(bk)
        ctx["last_book_age_s"] = book_age
        if PD.book_deadline_refusal(got):
            retry = PB.book_retry_plan(ctx, got, pin)
            ctx["last_book_cooldown_s"] = retry.get("cooldown_s")
            if retry["retry"]:
                return {"deferred": True, "why": "BOOK_RETRY",
                        "retry_after_s": retry["after_s"], "retry": retry}
            refusals.append(PD.R_BOOK_DEADLINE)
        elif obs.get("error") or not levels:
            refusals.append(PB.R_NO_BOOK)
        elif book_age > PB.BOOK_MAX_AGE_S:
            refusals.append(PB.R_BOOK_NOT_CURRENT)
        else:
            price = resting_price(
                levels, p=p, min_edge=min_edge,
                fee_pc=lambda px: PB.fee_per_contract(fee_fn, px, at))
            if price.get("refusal"):
                refusals.append(price["refusal"])
            else:
                lim = float(price["limit"])
                budget = float(ent["target_order_usd"])
                if cfg["risk"].get("per_order_cap_usd") is not None:
                    budget = min(budget, float(cfg["risk"]["per_order_cap_usd"]))
                per = lim + float(L.max_fee_for(1, lim, at=at,
                                                fee_fn=fee_fn))
                qty = int(max(0, math.floor(budget / per + 1e-9)))
                while qty >= 1 and float(L.reservation_for(
                        qty, lim, at=at, fee_fn=fee_fn)) > budget + 1e-9:
                    qty -= 1
                if qty < 1:
                    refusals.append(PB.R_NO_QTY)
                else:
                    fee = float(L._fee(fee_fn, qty, lim, at))
                    gross = qty * (float(p) - lim)
                    net = round(gross - fee, 9)
                    rebate = round(qty * 0.0125 * lim * (1 - lim), 6)
                    econ = {"qty": qty, "limit": lim,
                            "acquisition_cost_if_filled_usd": round(
                                qty * lim, 6),
                            "fees_if_filled_usd": round(fee, 9),
                            "fee_basis": FEE_BASIS,
                            "expected_gross_profit_if_filled_usd": round(
                                gross, 9),
                            "expected_net_profit_if_filled_usd": net,
                            "net_ev_positive_if_filled": net > 0,
                            "unverified_rebate_sensitivity_usd": rebate,
                            "conditional_on": ["ORDINARY_COMPLETION",
                                               "BEING_FILLED"],
                            "adverse_selection": "UNMEASURED (disclosed)",
                            "label": PB.ECONOMICS_LABEL}
                    if not net > 0:
                        refusals.append(PB.R_NET)
                    queue = await SIM.queue_ahead_at_placement(
                        conn, slug=cand["us_market_slug"], direction="BUY",
                        holding_side=side, limit=lim, market_data=md,
                        account_id=ctx["account_id"])
    PD.recheck_primary_reference(cand, pin, ctx, refusals)
    verdict = DP.ENTER if not refusals else DP.REFUSE
    best_gross = (None if not levels or p is None else round(
        DP.gross_edge(p, levels[0]["price"]) * 100.0, 9))
    rationale = (
        "rest a bid at %.2f (%.2f below the best ask %.2f): Pinnacle %.4f "
        "minus %.2f is %.3f pp gross, %.3f pp after the taker fee; if "
        "filled the modelled profit after fees is $%.2f on %d contracts" % (
            price["limit"], price["improvement_vs_ask_usd"],
            price["best_ask"], p, price["limit"], price["gross_edge_pp"],
            price["net_edge_pp"],
            float((econ or {}).get("expected_net_profit_if_filled_usd")
                  or 0.0), qty)
        if verdict == DP.ENTER else None)
    economics_rec = {
        "strategy": STRATEGY, "version": VERSION, "disclosure": DISCLOSURE,
        "probability": p, "probability_basis": "PINNACLE_ONLY_DEVIGGED_STORED",
        "threshold_edge_pp": min_edge_pp,
        "threshold_edge_probability": round(min_edge, 9),
        "parameters": params, "edge_rule": (
            "p - L >= threshold AND p - L - taker fee per contract(L) > 0 at "
            "the resting price L, L strictly below the best ask"),
        "resting_price": price, "best_level_edge_pp": best_gross,
        "limit_price": price.get("limit"), "proposed_qty": qty,
        "acquisition_if_filled": econ, "queue_at_placement": queue,
        "fee_basis": FEE_BASIS, "rationale": rationale,
        "expiry_s": MAKER_TTL_S, "cancel_conditions": CANCEL_CONDITIONS,
        "fill_rule": SIM.ASSUMPTIONS["resting"],
        "book_age_s": book_age, "book_max_age_s": PB.BOOK_MAX_AGE_S,
        "book_currency": PB.BOOK_CURRENCY,
        "exceptional_terms": match.get("exceptional_terms"),
        "label": PB.ECONOMICS_LABEL, "refusals": refusals,
        "fee_stop": None, "acquisition": None}
    conditions = [
        {"condition": "contract_outcome_settlement_match",
         "passed": match["established"], "refusals": match["refusals"]},
        {"condition": "pinnacle_fresh_at_the_decision_instant",
         "passed": pin.get("qualified") is True, "value": pin.get("age_s"),
         "threshold": pin.get("limit_s"), "units": "seconds",
         "refusal": pin.get("refusal")},
        {"condition": "current_paper_book_with_depth",
         "passed": (None if obs is None else bool(levels) and
                    book_age is not None and book_age <= PB.BOOK_MAX_AGE_S),
         "value": book_age, "threshold": PB.BOOK_MAX_AGE_S,
         "units": "seconds"},
        {"condition": "edge_at_least_min_gross_edge_pp_at_the_resting_price",
         "passed": None if not levels else price.get("limit") is not None,
         "value": price.get("gross_edge_pp"), "threshold": min_edge_pp,
         "units": "percentage points"},
        {"condition": "positive_ev_after_fees_if_filled",
         "passed": None if econ is None else econ[
             "net_ev_positive_if_filled"],
         "value": (econ or {}).get("expected_net_profit_if_filled_usd"),
         "threshold": 0.0, "units": "USD",
         "rule": "strictly greater than zero, taker fee charged"}]
    policy_decision = {
        "strategy": STRATEGY, "policy_version": VERSION,
        "threshold_edge_pp": min_edge_pp,
        "threshold_edge_probability": round(min_edge, 9),
        "disclosure": DISCLOSURE, "p_internal": None, "p_blended": None,
        "economics_label": PB.ECONOMICS_LABEL, "p_pinnacle": p,
        "conditions": conditions, "gross_edge_pp": price.get(
            "gross_edge_pp"), "best_ask_gross_edge_pp": best_gross,
        "net_expected_profit_usd": (econ or {}).get(
            "expected_net_profit_if_filled_usd"),
        "fees_usd": (econ or {}).get("fees_if_filled_usd"),
        "resting_price": price.get("limit"), "rationale": rationale,
        "expiry_s": MAKER_TTL_S, "cancel_conditions": CANCEL_CONDITIONS,
        "order_is_not_a_fill": True, "parameters": params,
        "book_currency": PB.BOOK_CURRENCY,
        "admitted": verdict == DP.ENTER,
        "refusal": refusals[0] if refusals else None, "refusals": refusals}
    internal_rec = {"available": False, "p": None, "strategy": STRATEGY,
                    "reason": "no internal model by design"}
    book = None if obs is None else {
        "book_obs_id": obs["obs_id"], "observed_at": obs["observed_at"],
        "observed_at_is": "OUR_RECEIPT_INSTANT",
        "age_at_decision_s": book_age, "error": obs.get("error"),
        "levels": levels[:10], "depth_levels": len(levels),
        "book_currency": PB.BOOK_CURRENCY,
        "basis": "OBSERVED_PAPER_BOOK_LEVELS_NOT_THE_VALUATION_QUOTE"}
    gaps = PB.qualification_gaps(cand=cand)
    alts = PB._alternatives(md, side=side or "LONG", p=p)
    provenance = PD.decision_provenance(
        strategy=STRATEGY, code_version=VERSION, policy_version=VERSION,
        row=row, session=ctx.get("session"), verdict=verdict,
        refusals=refusals, policy_decision=policy_decision,
        internal_model=internal_rec, pinnacle=pin, book=book,
        limit_price=price.get("limit"), qty=qty, alternatives=alts,
        optimistic=None, simulator_version=cfg["simulator_version"],
        decided_via=pin.get("decided_via"), at=at)
    rec = {"decision_id": did, "verdict": verdict, "strategy": STRATEGY,
           "refusal": refusals[0] if refusals else None,
           "refusals": refusals,
           "book_source": ctx.get("last_book_source"),
           "book_age_s": ctx.get("last_book_age_s"),
           "cooldown_s": ctx.get("last_book_cooldown_s")}
    inserted = await conn.fetchval(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, intent, "
        " fixture, label, verdict, refusal, refusals, p_internal, "
        " internal_model, p_pinnacle, pinnacle, p_blended, book_obs_id, "
        " book, proposed_qty, limit_price, economics, qualification_gaps, "
        " policy_version, policy_decision, alternatives, optimistic, "
        " simulator_version, strategy, provenance) VALUES ($1,$2,$3,$4,$5,"
        " $6,$7,$8,$9,$10::jsonb,$11,$12,$13,NULL,$14::jsonb,$15,$16::jsonb,"
        " NULL,$17,$18::jsonb,$19,$20,$21::jsonb,$22::jsonb,$23,$24::jsonb,"
        " $25::jsonb,NULL,$26,$27,$28::jsonb) ON CONFLICT DO NOTHING "
        " RETURNING decision_id",
        did, ctx["session_id"], ctx["account_id"], L._ts(at),
        cand["valuation_id"], cand.get("us_market_slug"), side,
        cand.get("side"), cand.get("fixture"),
        json.dumps(label, default=str), verdict, rec["refusal"], refusals,
        json.dumps(internal_rec, default=str), p,
        json.dumps(pin, default=str),
        None if obs is None else obs["obs_id"],
        None if book is None else json.dumps(book, default=str),
        None if not qty else L.D(qty),
        (None if price.get("limit") is None else L.D(price["limit"])),
        json.dumps(economics_rec, default=str), json.dumps(gaps, default=str),
        VERSION, json.dumps(policy_decision, default=str),
        json.dumps(alts, default=str), cfg["simulator_version"], STRATEGY,
        json.dumps(provenance, default=str))
    if inserted is None:
        return dict(rec, duplicate=True)
    if verdict != DP.ENTER:
        return rec
    # ── THE RESTING ORDER (persisted at once; not a fill) ─────────────
    lim = float(price["limit"])
    wire = lim if side == "LONG" else round(1.0 - lim, 6)
    order = {"idempotency_key": "%s:ENTRY" % did,
             "account_id": ctx["account_id"],
             "session_id": ctx["session_id"],
             "group_id": PB.group_id_for(did), "role": "ENTRY",
             "direction": "BUY", "holding_side": side,
             "intent": cand.get("side"),
             "us_market_slug": cand["us_market_slug"],
             "fixture": cand.get("fixture"),
             "label": dict(label, rationale=rationale,
                           cancel_conditions=CANCEL_CONDITIONS,
                           post_only=True, expiry_s=MAKER_TTL_S,
                           threshold_edge_pp=min_edge_pp,
                           p_pinnacle_at_placement=p),
             "order_type": "RESTING", "time_in_force": "GTD",
             "allow_partial": True, "qty": qty, "limit_price": lim,
             "wire_price": wire, "decision_id": did, "decided_at": at,
             "eligible_at": at, "expires_at": at + MAKER_TTL_S,
             "queue_ahead_qty": (queue or {}).get("queue_ahead_qty", 0.0),
             "queue_basis": dict(queue or {},
                                 placement_obs_id=obs["obs_id"]),
             "simulator_version": cfg["simulator_version"],
             "strategy": STRATEGY}
    got = await L.submit_order(conn, order, caps=cfg["risk"], fee_fn=fee_fn,
                               now=at, exclusive_fixture=True,
                               one_live_entry_per_fixture=True)
    rec["order"] = {k: got.get(k) for k in ("ok", "refusal", "duplicate")}
    if got.get("ok"):
        rec["order_id"] = got["order"]["order_id"]
        rec["resting"] = True
    else:
        rec["order_refusal"] = got.get("refusal")
        await PD._finding(conn, ctx, kind=PB.R_ORDER_REFUSED, subject=did,
                          detail={"refusal": got.get("refusal"),
                                  "decision_id": did, "at": at,
                                  "strategy": STRATEGY,
                                  "disclosure": DISCLOSURE,
                                  **{k: v for k, v in got.items()
                                     if k not in ("ok",)}})
    return rec


# ═════════════════════════════════════════════════════════════════════
# EVERY PASS: THE CANCELLATION CONDITIONS OF EACH STANDING ENTRY ORDER
# ═════════════════════════════════════════════════════════════════════

def check_resting(*, limit: float, p_new, reading_age_s, min_edge: float,
                  fee_pc, enabled: bool) -> dict:
    """Pure: which cancellation condition (if any) holds now."""
    if not enabled:
        return {"cancel": True, "condition": C_SWITCHED_OFF}
    if p_new is None or reading_age_s is None or \
            reading_age_s > MAX_UNVERIFIED_S:
        return {"cancel": True, "condition": C_UNVERIFIED,
                "reading_age_s": reading_age_s}
    e = DP.gross_edge(float(p_new), float(limit))
    fpc = float(fee_pc(limit))
    if not DP.clears(e, min_edge) or not e - fpc > DP.EDGE_TOLERANCE_PP:
        return {"cancel": True, "condition": C_EDGE_GONE,
                "gross_edge_pp": round(e * 100.0, 9),
                "net_edge_pp": round((e - fpc) * 100.0, 9)}
    return {"cancel": False, "gross_edge_pp": round(e * 100.0, 9),
            "net_edge_pp": round((e - fpc) * 100.0, 9),
            "reading_age_s": reading_age_s}


async def step_maintain(conn, ctx: dict) -> dict:
    """Re-check each open maker entry against its cancellation conditions
    on the newest Pinnacle reading; request a cancel when one holds (the
    simulator confirms it on its next step). Never places an order."""
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    fee_fn = ctx.get("fee_fn")
    out: dict[str, Any] = {"open": 0, "kept": 0, "cancel_requested": 0,
                           "by_condition": {}}
    en = await PB.enablement(conn, POL)
    params = await PB.cg_parameters(conn, ctx)
    min_edge = max(float(params["values"]["min_gross_edge_pp"]),
                   PB.CG_MIN_EDGE_PP_V2) / 100.0
    rows = await conn.fetch(
        "SELECT o.order_id, o.us_market_slug, o.holding_side, o.limit_price, "
        "       o.state, d.valuation_id FROM paper_orders o "
        "  LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id "
        " WHERE o.account_id=$1 AND o.strategy=$2 AND o.role='ENTRY' "
        "   AND o.state IN ('RESTING', 'PARTIALLY_FILLED')",
        ctx["account_id"], STRATEGY)
    for r in rows:
        out["open"] += 1
        contract = None
        if r["valuation_id"] is not None:
            contract = await conn.fetchrow(
                "SELECT buy_intent, payout_event, payout_is_complement, "
                "       sport_family, us_market_slug, raw_odds, "
                "       settlement_comparison->>'venue_rules_text' "
                "         AS venue_rules_text "
                "  FROM external_valuations WHERE id=$1",
                int(r["valuation_id"]))
        v = None
        if contract is not None:
            v = await conn.fetchrow(
                "SELECT probability, observed_at FROM external_valuations "
                " WHERE us_market_slug=$1 AND buy_intent=$2 "
                "   AND payout_event=$3 AND payout_is_complement=$4 "
                "   AND probability IS NOT NULL "
                " ORDER BY decided_at DESC LIMIT 1", r["us_market_slug"],
                contract["buy_intent"], contract["payout_event"],
                bool(contract["payout_is_complement"]))
        age = (None if v is None or v["observed_at"] is None
               else round(at - L._epoch(v["observed_at"]), 3))
        p_new = None if v is None else float(v["probability"])
        if p_new is not None and contract is not None:
            # R30A: the standing bid is re-checked on the SAME scale it was
            # placed on -- an NFL line's P(win | no tie) as the venue
            # contract's value (tie pays 0.50) at the worst cited tie rate.
            # A conversion that cannot be made leaves no probability, and
            # check_resting then cancels on the missing reading.
            from .. import bettor_nfl_settlement as NFL
            if (str(contract["sport_family"] or "") == "football"
                    and NFL.league_of_slug(contract["us_market_slug"])
                    == "nfl"):
                pin_like = {"p": p_new}
                why = PB.apply_venue_conversion(pin_like, {
                    "venue_conversion": {
                        "sport_family": "football", "league": "nfl",
                        "venue_rules_text": contract["venue_rules_text"],
                        "book_outcome_names": list(
                            (DP._j(contract["raw_odds"]) or {}).keys()),
                        "phase": None}})
                p_new = None if why else pin_like["p"]
        chk = check_resting(
            limit=float(r["limit_price"]),
            p_new=p_new,
            reading_age_s=age, min_edge=min_edge,
            fee_pc=lambda px: PB.fee_per_contract(fee_fn, px, at),
            enabled=bool(en.get("enabled")))
        if not chk["cancel"]:
            out["kept"] += 1
            continue
        got = await SIM.request_cancel(conn, r["order_id"], now=at,
                                       reason=chk["condition"])
        if got.get("ok"):
            out["cancel_requested"] += 1
            c = chk["condition"]
            out["by_condition"][c] = out["by_condition"].get(c, 0) + 1
    return out


async def step(conn, ctx: dict) -> dict:
    """THE MAKER-ENTRY POLICY'S PAPER STEP (its own kill-switch row)."""
    return await PB.step(conn, ctx, POL, decide=decide_one)


async def decide_for_hook(conn, ctx: dict, row: dict, *,
                          timeout_s: float) -> dict:
    return await PB.decide_for_hook(conn, ctx, row, timeout_s=timeout_s,
                                    pol=POL, decide=decide_one)


def describe() -> dict:
    return {"strategy": STRATEGY, "version": VERSION,
            "disclosure": DISCLOSURE, "ttl_s": MAKER_TTL_S,
            "max_unverified_s": MAX_UNVERIFIED_S, "fee_basis": FEE_BASIS,
            "cancel_conditions": CANCEL_CONDITIONS,
            "fill_rule": SIM.ASSUMPTIONS["resting"]}
