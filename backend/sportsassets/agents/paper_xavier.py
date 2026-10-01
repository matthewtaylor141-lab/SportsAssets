"""XAVIER ON THE PAPER BOOK: HANDOFF FROM THE FIRST FILL, REVIEWS, STANDING
PROTECTION, AND SETTLEMENT FROM AUTHORITATIVE EVIDENCE.

HANDOFF. A paper group passes to Xavier from its FIRST partial simulated fill
(`paper_handoffs`, one owner per group, unique): confirmed quantity is the
filled quantity, the rest of the entry is outstanding. Replays cannot create
a second owner.

REVIEWS. Each held group is reviewed immediately on its first fill, then on a
new fill (FILL_EVENT), on a market event (a newly observed book whose best
exit moved) and on the scheduled backstop (`cadence.xavier_backstop_s`). A
review compares HOLD, EXIT, REDUCE, NETTING and the INDIRECT HEDGE on the
SAME settlement measure through the existing comparison
(`agents.xavier_policy.run` -> `bettor_funded_decision.decide`, unchanged):
both legs of the group, the fees of every action, unmatched inventory,
exceptional states (an unreadable or empty book, a stale measure) and
incomplete searches are all on the record.

    measure     P(the held side pays) = (research model at the current best
                price + the latest Pinnacle reading) / 2, when a Pinnacle
                reading for the same contract is on a valuation within the
                lookback; otherwise the entry decision's blended probability,
                labelled ENTRY_TIME_MEASURE (stale, stated). Void is not
                applied (conditional on the fixture being played).
    HOLD        value = q x p (the payout expected from here)
    EXIT        value = walked proceeds of a sale of q into the observed bids
                (after fees) + the unsold remainder held at q' x p
    REDUCE      the same for half the position
    NETTING     on this venue one signed net position means buying the
                complement IS the sale: shown, not rankable, never counted
                twice
    INDIRECT    a hedge on a sibling contract of the fixture: the paper book
                does not run the hedge search, so it is NOT_RANKABLE with
                the search recorded as INCOMPLETE -- never assumed absent

STANDING PROTECTION. When the review holds, Xavier keeps ONE resting
protective sale per group at the protective price: the lowest cent at which
selling the whole open quantity recovers its cost basis, the sale fees and
the policy buffer (a positive floor on the matched quantity). The one
live-or-potentially-live order per group rule is the funded rule
(`bettor_xavier_standing_orders.second_live_hedge_permitted`) and a partial
unique index in migration 171. A position that changes (partial fills of the
entry, a partial fill of the protection) is reconciled: cancel -> the
simulator confirms the terminal state -> the replacement is placed on a later
pass. A floor is NEVER realized P&L until a sale or a settlement books it.

SETTLEMENT. An open position settles only on authoritative evidence: the
venue's own settlement joined onto `external_valuations` (outcome_basis
VENUE_SETTLEMENT_PRICE / VENUE_REPORTED_OUTCOME for a 0/1, CONFIRMED_VOID for
a refund), unanimous across the rows for the contract. A later disagreement
with what was settled books a CORRECTION; conflicting evidence settles
nothing and is recorded as a finding.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from decimal import Decimal
from typing import Any

from .. import bettor_paper_ledger as L
from .. import bettor_paper_simulator as SIM
from . import derek_policy as DP

VERSION = "PAPER_XAVIER_V1"
T_FIRST = "FIRST_FILL"
T_FILL = "FILL_EVENT"
T_MARKET = "MARKET_EVENT"
T_BACKSTOP = "SCHEDULED_BACKSTOP"

A_HOLD, A_EXIT, A_REDUCE = "HOLD", "EXIT", "REDUCE"
A_NETTING, A_INDIRECT = "NETTING", "ACQUIRE_INDIRECT_HEDGE"
B_NETTING_IS_EXIT = "IDENTICAL_TO_EXIT_ON_A_ONE_NET_POSITION_VENUE"
B_INDIRECT_NOT_SEARCHED = "INDIRECT_HEDGE_SEARCH_NOT_RUN_ON_THE_PAPER_BOOK"
B_NO_BIDS = "NO_EXECUTABLE_EXIT_DEPTH_IN_THE_OBSERVED_BOOK"
R_NO_MEASURE = "NO_SETTLEMENT_MEASURE_FOR_THIS_POSITION"
PROTECTION_BUFFER_USD_PER_CONTRACT = 0.01
LABEL_BASES = ("VENUE_SETTLEMENT_PRICE", "VENUE_REPORTED_OUTCOME")
VOID_BASIS = "CONFIRMED_VOID"


def _h(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()
                          ).hexdigest()[:24]


async def _strategy(conn, group_id: str) -> str:
    """The group's strategy (its entry order's, migration 182), carried onto
    every management order: a position never switches policy."""
    from . import paper_benchmark as PB
    return await PB.group_strategy(conn, group_id)


def _clock(ctx) -> float:
    c = ctx.get("clock")
    return float(c()) if c else float(ctx["now"])


# ═════════════════════════════════════════════════════════════════════
# HANDOFF
# ═════════════════════════════════════════════════════════════════════

async def step_handoff(conn, ctx: dict) -> dict:
    """ONE OWNER PER GROUP FROM ITS FIRST SIMULATED FILL; later fills update
    the same row."""
    acct = ctx["account_id"]
    rows = await conn.fetch(
        "SELECT o.order_id, o.group_id, o.decision_id, o.qty, o.filled_qty, "
        "       o.state, o.strategy, (SELECT f.fill_id FROM paper_fills f "
        "                  WHERE f.order_id = o.order_id "
        "                  ORDER BY f.filled_at, f.fill_id LIMIT 1) AS ff, "
        "       (SELECT min(f.filled_at) FROM paper_fills f "
        "                  WHERE f.order_id = o.order_id) AS ffa "
        "  FROM paper_orders o WHERE o.account_id = $1 AND o.role = 'ENTRY' "
        "   AND o.filled_qty > 0", acct)
    created, updated = [], 0
    for r in rows:
        outstanding = (Decimal(0) if r["state"] in L.TERMINAL_STATES
                       else r["qty"] - r["filled_qty"])
        hid = "paperhand:" + _h(r["group_id"])
        got = await conn.fetchval(
            "INSERT INTO paper_handoffs (handoff_id, session_id, account_id, "
            " group_id, decision_id, entry_order_id, first_fill_id, "
            " first_fill_at, confirmed_qty, outstanding_qty, strategy) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
            "ON CONFLICT (group_id) DO UPDATE SET "
            " confirmed_qty = EXCLUDED.confirmed_qty, "
            " outstanding_qty = EXCLUDED.outstanding_qty, updated_at = now() "
            " WHERE paper_handoffs.confirmed_qty <> EXCLUDED.confirmed_qty "
            "    OR paper_handoffs.outstanding_qty <> EXCLUDED.outstanding_qty"
            " RETURNING (xmax = 0) AS inserted",
            hid, ctx["session_id"], acct, r["group_id"], r["decision_id"],
            r["order_id"], r["ff"], r["ffa"], r["filled_qty"], outstanding,
            # THE ENTRY'S STRATEGY (migration 182): the group keeps it.
            r["strategy"])
        if got is True:
            created.append(r["group_id"])
        elif got is False:
            updated += 1
    ctx["new_handoffs"] = created
    return {"handoffs_created": len(created), "handoffs_updated": updated}


# ═════════════════════════════════════════════════════════════════════
# THE MEASURE AND THE ALTERNATIVES (pure given inputs)
# ═════════════════════════════════════════════════════════════════════

def _exit_walk(levels: list, qty: float, fee_fn, at) -> dict:
    left, proceeds, fees, takes = float(qty), 0.0, 0.0, []
    for lv in levels:
        if left <= 1e-9:
            break
        t = min(float(lv["qty"]), left)
        g = t * float(lv["price"])
        fe = float(L._fee(fee_fn, t, lv["price"], at))
        proceeds += g
        fees += fe
        takes.append({"price": lv["price"], "wire": lv["wire"], "qty": t})
        left -= t
    sold = float(qty) - left
    return {"sold": round(sold, 6), "proceeds_usd": round(proceeds, 6),
            "fees_usd": round(fees, 6), "unsold": round(left, 6),
            "takes": takes,
            "worst_price": takes[-1]["price"] if takes else None,
            "worst_wire": takes[-1]["wire"] if takes else None}


def alternatives(*, pos: dict, levels: list, p: float | None, fee_fn,
                 at: float) -> dict:
    """HOLD / EXIT / REDUCE / NETTING / INDIRECT on ONE measure, in the unit
    `bettor_funded_decision.decide` ranks: expected dollars from here over
    the whole position (the cost basis is sunk and common to all)."""
    q = float(pos["open_qty"])
    basis = float(pos["cost_basis_usd"])
    cands, blocked = [], []
    if p is None:
        blocked.append({"action": A_HOLD, "blocker": R_NO_MEASURE,
                        "value_usd": None})
    else:
        cands.append({"action": A_HOLD, "candidate_id": "HOLD", "qty": q,
                      "value_usd": round(q * p, 6),
                      "expected_net_usd": round(q * p - basis, 6),
                      "worst_case_net_usd": round(-basis, 6),
                      "downside_usd": round(-basis, 6),
                      "incremental_capital_usd": 0.0,
                      "fees_usd": 0.0,
                      "evidence_quality": "MEASURE_%s" % (
                          "PRESENT" if p is not None else "ABSENT")})
    for action, sell in ((A_EXIT, q), (A_REDUCE, math.floor(q / 2))):
        if sell < 1:
            blocked.append({"action": action, "blocker": "NOTHING_TO_SELL",
                            "value_usd": None})
            continue
        w = _exit_walk(levels, sell, fee_fn, at)
        if w["sold"] <= 0:
            blocked.append({"action": action, "blocker": B_NO_BIDS,
                            "value_usd": None})
            continue
        kept = q - w["sold"]
        cash = w["proceeds_usd"] - w["fees_usd"]
        value = None if (p is None and kept > 1e-9) else round(
            cash + kept * (p or 0.0), 6)
        c = {"action": action, "candidate_id": action, "qty": w["sold"],
             "value_usd": value,
             "expected_net_usd": (None if value is None
                                  else round(value - basis, 6)),
             "worst_case_net_usd": round(cash - basis, 6),
             "downside_usd": round(cash - basis, 6),
             "incremental_capital_usd": 0.0, "fees_usd": w["fees_usd"],
             "walk": w, "unmatched_after_qty": round(kept, 6),
             "evidence_quality": "OBSERVED_BOOK_DEPTH"}
        (cands if value is not None else blocked).append(
            c if value is not None else dict(c, blocker=R_NO_MEASURE))
    blocked.append({"action": A_NETTING, "blocker": B_NETTING_IS_EXIT,
                    "value_usd": None,
                    "why": ("buying the complement on the same market is the "
                            "same order as the sale on a one-signed-net "
                            "venue; valued once, as EXIT")})
    blocked.append({"action": A_INDIRECT, "blocker": B_INDIRECT_NOT_SEARCHED,
                    "value_usd": None, "rankable": False,
                    "why": ("no sibling-contract hedge search runs on the "
                            "paper book; the comparison is INCOMPLETE on "
                            "this alternative, never assumed empty")})
    return {"candidates": cands, "not_rankable": blocked,
            "version": "PAPER_HOLD_RANKING_V1",
            "incomplete_search": {"complete": False,
                                  "missing": [A_INDIRECT],
                                  "why": B_INDIRECT_NOT_SEARCHED}}


def protective_price(*, qty: float, cost_basis: float, fee_fn, at,
                     buffer_per_contract: float =
                     PROTECTION_BUFFER_USD_PER_CONTRACT) -> dict:
    """THE LOWEST CENT at which selling `qty` recovers the cost basis, the
    sale fees and the buffer: a positive floor on the matched quantity if
    it fills. Pure but for the fee function."""
    need = float(cost_basis) + float(buffer_per_contract) * float(qty)
    for cents in range(1, 100):
        px = cents / 100.0
        fee = float(L._fee(fee_fn, qty, px, at))
        if qty * px - fee >= need - 1e-9:
            return {"ok": True, "price": px, "fee_usd": fee,
                    "floor_usd": round(qty * px - fee - float(cost_basis), 6),
                    "floor_is": ("NOT REALIZED: a floor on the matched "
                                 "quantity only if the protection fills")}
    return {"ok": False, "refusal": "NO_PROTECTIVE_PRICE_BELOW_ONE_DOLLAR"}


# ═════════════════════════════════════════════════════════════════════
# THE REVIEW
# ═════════════════════════════════════════════════════════════════════

async def _latest_book(conn, slug: str):
    return await conn.fetchrow(
        "SELECT * FROM paper_book_observations WHERE us_market_slug=$1 "
        " ORDER BY observed_at DESC LIMIT 1", slug)


async def _measure(conn, ctx, *, pos: dict, levels_buy: list) -> dict:
    """P(the held side pays), on the paper session's measure -- the measure
    of the group's OWN strategy: a PINNACLE_ONLY_PAPER_BENCHMARK group is
    measured on the de-vigged Pinnacle probability alone
    (`paper_benchmark.xavier_measure`), never the two-model blend; a position
    never switches policy."""
    from . import paper_benchmark as PB
    strat = await PB.group_strategy(conn, pos["group_id"])
    if strat in PB.BENCHMARK_STRATEGIES:
        return await PB.xavier_measure(conn, ctx, pos=pos, strategy=strat)
    at = _clock(ctx)
    lookback = float(ctx["config"]["entry"]["valuation_lookback_s"])
    intent = DP.LONG if pos["holding_side"] == "LONG" else DP.SHORT
    v = await conn.fetchrow(
        "SELECT probability, observed_at, payout_is_complement, version "
        "  FROM external_valuations WHERE us_market_slug=$1 "
        "   AND buy_intent=$2 AND probability IS NOT NULL "
        "   AND decided_at > to_timestamp($3) "
        " ORDER BY decided_at DESC LIMIT 1", pos["us_market_slug"], intent,
        at - lookback)
    from . import paper_derek as PD
    model = (ctx.get("derek") or {}).get("model")
    if model is None:
        model = await PD.research_model(conn, at=at, verify=False)
    if v is not None and model.get("ok") and levels_buy:
        sc = PD.score(model, price=levels_buy[0]["price"],
                      payout_is_complement=bool(v["payout_is_complement"]))
        if sc.get("ok"):
            return {"p": DP.blend(sc["p"], float(v["probability"])),
                    "source": "CURRENT_BLEND",
                    "p_internal": sc["p"], "model_id": model.get("model_id"),
                    "model_label": PD.MODEL_LABEL,
                    "p_pinnacle": float(v["probability"]),
                    "pinnacle_at": L._epoch(v["observed_at"]),
                    "stale": False, "void_applied": False}
    d = await conn.fetchrow(
        "SELECT d.p_blended, d.decided_at FROM paper_decisions d "
        "  JOIN paper_orders o ON o.decision_id = d.decision_id "
        " WHERE o.group_id=$1 AND o.us_market_slug=$2 "
        "   AND d.p_blended IS NOT NULL LIMIT 1", pos["group_id"],
        pos["us_market_slug"])
    if d is not None:
        return {"p": float(d["p_blended"]), "source": "ENTRY_TIME_MEASURE",
                "at": L._epoch(d["decided_at"]), "stale": True,
                "void_applied": False,
                "why": ("no current Pinnacle reading for this contract "
                        "within the lookback; the entry decision's blended "
                        "probability is used and labelled stale")}
    return {"p": None, "source": None, "stale": True, "why": R_NO_MEASURE}


def _trigger(*, group: str, new_handoffs: list, last: dict | None,
             last_fill_at, book_at, best_exit, at: float,
             backstop_s: float) -> str | None:
    if group in new_handoffs or last is None:
        return T_FIRST
    if last_fill_at is not None and last_fill_at > last["reviewed_at"]:
        return T_FILL
    prev_exit = ((last.get("measure") or {}).get("best_exit_at_review"))
    if book_at is not None and book_at > last["reviewed_at"] and \
            best_exit is not None and prev_exit is not None and \
            abs(float(best_exit) - float(prev_exit)) > 1e-9:
        return T_MARKET
    if at - last["reviewed_at"] >= backstop_s:
        return T_BACKSTOP
    return None


async def review_group(conn, ctx: dict, group_id: str, *,
                       trigger: str) -> dict:
    from . import xavier_policy as XP
    from .. import bettor_xavier_standing_orders as SPO
    at = _clock(ctx)
    fee_fn = ctx.get("fee_fn")
    acct = ctx["account_id"]
    allpos = [p for p in await L.positions(conn, acct)
              if p["group_id"] == group_id]
    reviews = []
    for pos in allpos:
        obs = await _latest_book(conn, pos["us_market_slug"])
        md = None if obs is None or obs["error"] else {
            "bids": L._j(obs["bids"]) or [], "offers": L._j(obs["offers"])
            or []}
        exit_lv = SIM.levels_for(md, direction="SELL",
                                 holding_side=pos["holding_side"])["levels"]
        buy_lv = SIM.levels_for(md, direction="BUY",
                                holding_side=pos["holding_side"])["levels"]
        measure = await _measure(conn, ctx, pos=pos, levels_buy=buy_lv)
        measure["best_exit_at_review"] = (exit_lv[0]["price"] if exit_lv
                                          else None)
        measure["book_obs_id"] = None if obs is None else obs["obs_id"]
        alts = alternatives(pos=pos, levels=exit_lv, p=measure.get("p"),
                            fee_fn=fee_fn, at=at)
        policy = await XP.load(conn)
        sel = XP.run(policy, hold_ranking=alts, limits=None)
        exceptional = []
        if obs is None:
            exceptional.append("NO_BOOK_OBSERVED_FOR_THIS_MARKET")
        elif obs["error"]:
            exceptional.append("LATEST_BOOK_UNREADABLE")
        if not exit_lv:
            exceptional.append(B_NO_BIDS)
        if measure.get("stale"):
            exceptional.append("MEASURE_%s" % (measure.get("source")
                                               or "ABSENT"))
        # ── STANDING PROTECTION AND THE ACTION ──────────────────────
        standing = await conn.fetch(
            "SELECT * FROM paper_orders WHERE group_id=$1 "
            "   AND us_market_slug=$2 AND holding_side=$3 "
            "   AND role='STANDING_PROTECTION' AND state = ANY($4::text[])",
            group_id, pos["us_market_slug"], pos["holding_side"],
            list(L.OPEN_STATES))
        action: dict[str, Any] = {"taken": "NONE"}
        chosen = sel.get("selected")
        prot = protective_price(qty=pos["open_qty"],
                                cost_basis=pos["cost_basis_usd"],
                                fee_fn=fee_fn, at=at)
        if chosen in (A_EXIT, A_REDUCE):
            if standing:
                # THE EXIT WAITS FOR THE PROTECTION TO BE TERMINAL: its
                # inventory is committed to the resting sale.
                for s in standing:
                    if s["state"] != "CANCEL_PENDING":
                        await SIM.request_cancel(
                            conn, s["order_id"], now=at,
                            reason="EXIT_WAITS_FOR_STANDING_TERMINAL")
                action = {"taken": "CANCEL_STANDING_BEFORE_EXIT",
                          "orders": [s["order_id"] for s in standing]}
            else:
                cand = sel.get("selected_candidate") or {}
                w = cand.get("walk") or {}
                action = await _submit_sale(
                    conn, ctx, pos=pos, role=chosen, qty=cand.get("qty"),
                    limit=w.get("worst_price"), wire=w.get("worst_wire"),
                    review_key="%s:%s:%s" % (group_id, pos["position_key"],
                                             at))
        elif chosen == A_HOLD and prot.get("ok"):
            action = await _maintain_standing(
                conn, ctx, pos=pos, standing=[dict(s) for s in standing],
                prot=prot, md=md, at=at, SPO=SPO)
        confirmed = await conn.fetchval(
            "SELECT coalesce(sum(qty), 0) FROM paper_fills WHERE group_id=$1"
            "   AND role='STANDING_PROTECTION'", group_id)
        live = [dict(s) for s in standing]
        protected_qty = sum(float(s["qty"]) - float(s["filled_qty"])
                            for s in live)
        exposure = {"open_qty": pos["open_qty"],
                    "cost_basis_usd": pos["cost_basis_usd"],
                    "max_loss_usd": pos["cost_basis_usd"],
                    "resting_protection_qty": protected_qty,
                    "unmatched_inventory_qty": round(
                        pos["open_qty"] - protected_qty, 6),
                    "remaining_exposure_usd": pos["cost_basis_usd"],
                    "floors_are_not_realized_pnl": True}
        rid = "paperrev:" + _h(group_id, pos["position_key"], at, trigger)
        await conn.execute(
            "INSERT INTO paper_xavier_reviews (review_id, session_id, "
            " account_id, group_id, reviewed_at, trigger, recommendation, "
            " refusal, alternatives, selection, exposure, standing, "
            " confirmed_protection, incomplete_search, exceptional, measure,"
            " action, strategy) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,"
            " $10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,"
            " $15::jsonb,$16::jsonb,$17::jsonb,$18) ON CONFLICT DO NOTHING",
            rid, ctx["session_id"], acct, group_id, L._ts(at), trigger,
            chosen, sel.get("refusal"), json.dumps(alts, default=str),
            json.dumps({k: sel.get(k) for k in (
                "selected", "refusal", "selection_reason",
                "margin_over_runner_up", "decision_policy", "tie_break",
                "hold_is_priced", "limits_applied")}, default=str),
            json.dumps(exposure, default=str),
            json.dumps({"live_orders": [L.order_view(s) for s in standing],
                        "protective_price": prot,
                        "invariant": SPO.WHY_ONE_LIVE_ORDER},
                       default=str),
            json.dumps({"filled_protection_qty": float(confirmed),
                        "basis": "simulated fills of the standing sale"},
                       default=str),
            json.dumps(alts["incomplete_search"], default=str),
            json.dumps(exceptional), json.dumps(measure, default=str),
            json.dumps(action, default=str), pos.get("strategy")
            or L.DEFAULT_STRATEGY)
        reviews.append({"review_id": rid, "position": pos["position_key"],
                        "recommendation": chosen, "trigger": trigger,
                        "action": action.get("taken")})
    return {"group_id": group_id, "reviews": reviews}


async def _submit_sale(conn, ctx, *, pos, role, qty, limit, wire,
                       review_key) -> dict:
    if not qty or limit is None:
        return {"taken": "NONE", "why": "NO_WALKABLE_SALE"}
    sim = ctx["config"]["simulator"]
    at = _clock(ctx)
    o = {"idempotency_key": "paper:%s:%s" % (role, _h(review_key)),
         "account_id": ctx["account_id"], "session_id": ctx["session_id"],
         "group_id": pos["group_id"], "role": role, "direction": "SELL",
         "holding_side": pos["holding_side"],
         "intent": SIM.intent_of("SELL", pos["holding_side"]),
         "us_market_slug": pos["us_market_slug"], "fixture": pos["fixture"],
         "label": pos.get("label") or {}, "order_type": "MARKETABLE",
         "time_in_force": "IOC", "allow_partial": True, "qty": qty,
         "limit_price": limit, "wire_price": wire, "decision_id": None,
         "decided_at": at,
         "eligible_at": at + float(sim["decision_to_execution_delay_s"]),
         "expires_at": at + float(sim["marketable_ttl_s"]),
         "simulator_version": ctx["config"]["simulator_version"]}
    o["strategy"] = await _strategy(conn, pos["group_id"])
    got = await L.submit_order(conn, o, caps=ctx["config"]["risk"],
                               fee_fn=ctx.get("fee_fn"), now=at)
    return {"taken": "SUBMIT_%s" % role, "ok": got.get("ok"),
            "refusal": got.get("refusal"),
            "order_id": (got.get("order") or {}).get("order_id")}


async def _maintain_standing(conn, ctx, *, pos, standing, prot, md, at,
                             SPO) -> dict:
    """ONE live-or-potentially-live protective sale per group position."""
    want_qty = float(pos["open_qty"])
    live = [s for s in standing if s["state"] != "CANCEL_PENDING"]
    pending = [s for s in standing if s["state"] == "CANCEL_PENDING"]
    if live:
        s = live[0]
        remaining = float(s["qty"]) - float(s["filled_qty"])
        if abs(remaining - want_qty) < 1e-9 and \
                abs(float(s["limit_price"]) - prot["price"]) < 1e-9:
            return {"taken": "KEEP_STANDING", "order_id": s["order_id"]}
        await SIM.request_cancel(conn, s["order_id"], now=at,
                                 reason="POSITION_OR_PROTECTIVE_PRICE_CHANGED")
        return {"taken": "CANCEL_FOR_REPLACEMENT", "order_id": s["order_id"],
                "why": ("cancel -> confirmed terminal -> replacement on a "
                        "later pass; no second live order meanwhile")}
    gate = SPO.second_live_hedge_permitted(
        live_or_potentially_live=len(standing))
    if not gate["permitted"]:
        return {"taken": "WAIT_FOR_TERMINAL", "gate": gate,
                "orders": [s["order_id"] for s in pending]}
    held = await L.held_uncommitted(conn, ctx["account_id"],
                                    group_id=pos["group_id"],
                                    slug=pos["us_market_slug"],
                                    holding_side=pos["holding_side"])
    qty = math.floor(float(held))
    if qty < 1:
        return {"taken": "NONE", "why": "NO_UNCOMMITTED_INVENTORY"}
    q = await SIM.queue_ahead_at_placement(
        conn, slug=pos["us_market_slug"], direction="SELL",
        holding_side=pos["holding_side"], limit=prot["price"],
        market_data=md, account_id=ctx["account_id"])
    wire = (prot["price"] if pos["holding_side"] == "LONG"
            else round(1.0 - prot["price"], 6))
    last_obs = await conn.fetchval(
        "SELECT max(obs_id) FROM paper_book_observations "
        " WHERE us_market_slug=$1", pos["us_market_slug"])
    sim = ctx["config"]["simulator"]
    o = {"idempotency_key": "paper:STANDING:%s" % _h(
            pos["position_key"], qty, prot["price"], at),
         "account_id": ctx["account_id"], "session_id": ctx["session_id"],
         "group_id": pos["group_id"], "role": "STANDING_PROTECTION",
         "direction": "SELL", "holding_side": pos["holding_side"],
         "intent": SIM.intent_of("SELL", pos["holding_side"]),
         "us_market_slug": pos["us_market_slug"], "fixture": pos["fixture"],
         "label": pos.get("label") or {}, "order_type": "RESTING",
         "time_in_force": "GTD", "allow_partial": True, "qty": qty,
         "limit_price": prot["price"], "wire_price": wire,
         "decision_id": None, "decided_at": at, "eligible_at": at,
         "expires_at": at + float(sim["resting_gtd_s"]),
         "queue_ahead_qty": q["queue_ahead_qty"],
         "queue_basis": dict(q, placement_obs_id=last_obs or 0),
         "simulator_version": ctx["config"]["simulator_version"]}
    o["strategy"] = await _strategy(conn, pos["group_id"])
    try:
        got = await L.submit_order(conn, o, now=at)
    except Exception as exc:                                    # noqa: BLE001
        # the partial unique index: a racing second placement is refused
        return {"taken": "PLACEMENT_REFUSED", "why": type(exc).__name__,
                "gate": "ONE_LIVE_STANDING_ORDER_PER_GROUP_INDEX"}
    return {"taken": "PLACE_STANDING", "ok": got.get("ok"),
            "refusal": got.get("refusal"),
            "order_id": (got.get("order") or {}).get("order_id"),
            "protective_price": prot["price"],
            "floor_if_filled_usd": prot.get("floor_usd"),
            "floor_is": prot.get("floor_is")}


async def step(conn, ctx: dict) -> dict:
    """REVIEW EVERY HELD GROUP THAT IS DUE (first fill, fill event, market
    event or the scheduled backstop), within the pass budget."""
    acct = ctx["account_id"]
    at = _clock(ctx)
    backstop = float(ctx["config"]["cadence"].get("xavier_backstop_s", 60.0))
    groups = sorted({p["group_id"] for p in await L.positions(conn, acct)})
    handed = {r["group_id"] for r in await conn.fetch(
        "SELECT group_id FROM paper_handoffs WHERE account_id=$1", acct)}
    out = {"reviews": 0, "groups_held": len(groups), "by_trigger": {},
           "not_handed_off": sorted(set(groups) - handed)}
    for g in groups:
        if g not in handed:
            continue
        if time.monotonic() > ctx["deadline"]:
            out["budget_exhausted"] = True
            break
        last = await conn.fetchrow(
            "SELECT reviewed_at, measure FROM paper_xavier_reviews "
            " WHERE group_id=$1 ORDER BY reviewed_at DESC LIMIT 1", g)
        lastd = None if last is None else {
            "reviewed_at": L._epoch(last["reviewed_at"]),
            "measure": L._j(last["measure"]) or {}}
        lf = L._epoch(await conn.fetchval(
            "SELECT max(filled_at) FROM paper_fills WHERE group_id=$1", g))
        slug = await conn.fetchval(
            "SELECT us_market_slug FROM paper_orders WHERE group_id=$1 "
            "   AND role='ENTRY' LIMIT 1", g)
        book = None if slug is None else await _latest_book(conn, slug)
        best_exit = None
        if book is not None and not book["error"]:
            side = await conn.fetchval(
                "SELECT holding_side FROM paper_orders WHERE group_id=$1 "
                "   AND role='ENTRY' LIMIT 1", g)
            lv = SIM.levels_for({"bids": L._j(book["bids"]) or [],
                                 "offers": L._j(book["offers"]) or []},
                                direction="SELL", holding_side=side)
            best_exit = lv["levels"][0]["price"] if lv["levels"] else None
        trig = _trigger(group=g, new_handoffs=ctx.get("new_handoffs") or [],
                        last=lastd, last_fill_at=lf,
                        book_at=(None if book is None
                                 else L._epoch(book["observed_at"])),
                        best_exit=best_exit, at=at, backstop_s=backstop)
        if trig is None:
            continue
        got = await review_group(conn, ctx, g, trigger=trig)
        out["reviews"] += len(got["reviews"])
        out["by_trigger"][trig] = out["by_trigger"].get(trig, 0) + 1
    return out


# ═════════════════════════════════════════════════════════════════════
# SETTLEMENT FROM AUTHORITATIVE EVIDENCE
# ═════════════════════════════════════════════════════════════════════

#: THE VENUE'S OWN NON-BINARY SETTLEMENT, as the outcome join recorded it
#: (`settlement_read`, left unjoined: neither side was paid in full).
VENUE_PRICE_SQL = """
    SELECT id, buy_intent, settlement_read, settlement_read_at,
           settlement_comparison->>'venue_rules_text' AS rules
      FROM external_valuations
     WHERE us_market_slug = $1 AND outcome_basis IS NULL
       AND settlement_read IS NOT NULL AND settlement_read_at IS NOT NULL
     ORDER BY id
"""


def venue_price_settlement(rows: list, *, holding_side: str) -> dict:
    """THE VENUE'S PUBLISHED PRICE FOR A CONTRACT IT SETTLED AT A PRICE.
    Pure. Established only when (1) the venue's recorded settlement for the
    contract is a price strictly between 0 and 1, (2) every read agrees,
    and (3) the contract's OWN rules text states a price settlement (the
    last fair market price). The long side is paid that price per contract,
    the short side its complement. Otherwise: no payout, the position stays
    open and pending -- a refund is never assumed."""
    from .. import bettor_settlement_terms as ST
    prices, ev, stated = set(), [], False
    for r in rows:
        try:
            sp = float(str(r.get("settlement_read")).strip())
        except (TypeError, ValueError):
            continue
        if not (0.0 < sp < 1.0):
            continue
        prices.add(round(sp, 9))
        ev.append({"valuation_id": r.get("id"),
                   "settlement_read": r.get("settlement_read"),
                   "settlement_read_at": L._epoch(r.get("settlement_read_at"))})
        terms = ST.read_terms(r.get("rules") or "").get("terms") or {}
        if ST.PAY_LAST_FAIR_MARKET_PRICE in terms.values():
            stated = True
    if not ev:
        return {"price": None, "why": "NO_VENUE_PRICE_SETTLEMENT_RECORDED"}
    if len(prices) > 1:
        return {"price": None, "why": "CONFLICTING_VENUE_SETTLEMENT_PRICES",
                "evidence": ev}
    if not stated:
        return {"price": None, "evidence": ev,
                "why": ("VENUE_SETTLED_AT_A_PRICE_BUT_THE_CONTRACT_TEXT_HELD_"
                        "STATES_NO_PRICE_SETTLEMENT")}
    long_px = prices.pop()
    per = long_px if holding_side == "LONG" else round(1.0 - long_px, 9)
    return {"price": per, "venue_long_price": long_px, "evidence": ev,
            "rule": "the contract's stated last-fair-market-price settlement"}


def outcome_for(rows: list, *, holding_side: str) -> dict:
    """Our side's settlement from the venue-joined valuation rows. Pure."""
    ours = DP.LONG if holding_side == "LONG" else DP.SHORT
    seen = set()
    ev = []
    for r in rows:
        basis = r.get("outcome_basis")
        if basis == VOID_BASIS:
            seen.add("VOID_REFUND")
        elif basis in LABEL_BASES and r.get("outcome_known") and \
                r.get("outcome") in (0, 1):
            won = (int(r["outcome"]) == 1) == (r.get("buy_intent") == ours)
            seen.add("WON" if won else "LOST")
        else:
            continue
        ev.append({"valuation_id": r.get("id"), "outcome": r.get("outcome"),
                   "outcome_basis": basis, "buy_intent": r.get("buy_intent"),
                   "outcome_at": L._epoch(r.get("outcome_at"))})
    if not ev:
        return {"outcome": None, "why": "NO_AUTHORITATIVE_SETTLEMENT_YET"}
    if len(seen) > 1:
        return {"outcome": None, "why": "CONFLICTING_SETTLEMENT_EVIDENCE",
                "evidence": ev}
    return {"outcome": seen.pop(), "evidence": ev}


async def step_settle(conn, ctx: dict) -> dict:
    acct = ctx["account_id"]
    at = _clock(ctx)
    out = {"settled": 0, "corrected": 0, "conflicts": 0, "waiting": 0}
    try:
        has = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE "
            " table_name='external_valuations' AND column_name='outcome_basis'")
    except Exception:                                           # noqa: BLE001
        has = 0
    if not has:
        return dict(out, refusal="OUTCOME_EVIDENCE_COLUMNS_ABSENT")
    allpos = await L.positions(conn, acct, include_closed=True)
    for p in allpos:
        rows = [dict(r) for r in await conn.fetch(
            "SELECT id, buy_intent, outcome, outcome_known, outcome_basis, "
            "       outcome_at FROM external_valuations "
            " WHERE us_market_slug=$1 AND outcome_basis IS NOT NULL "
            " ORDER BY id", p["us_market_slug"])]
        got = outcome_for(rows, holding_side=p["holding_side"])
        key = "venue-final:%s" % p["us_market_slug"]
        vp = None
        if got["outcome"] is None and got.get("why") == \
                "NO_AUTHORITATIVE_SETTLEMENT_YET" and p["open_qty"] > 1e-9 \
                and p.get("settlement") is None:
            # THE COMPLETED-GAME POLICY'S EXCEPTIONAL SETTLEMENT: paid at
            # the venue's own published price, never an assumed refund.
            from . import paper_benchmark as PB
            if await PB.group_strategy(conn, p["group_id"]) == \
                    PB.CG_STRATEGY:
                vrows = [dict(r) for r in await conn.fetch(
                    VENUE_PRICE_SQL, p["us_market_slug"])]
                vp = venue_price_settlement(vrows,
                                            holding_side=p["holding_side"])
                if vp.get("price") is not None:
                    r = await L.settle(
                        conn, account_id=acct, group_id=p["group_id"],
                        slug=p["us_market_slug"],
                        holding_side=p["holding_side"],
                        settlement_event_key=key,
                        outcome="SETTLED_AT_VENUE_PRICE",
                        evidence=dict(vp, policy=PB.CG_VERSION),
                        evidence_source="external_valuations."
                                        "settlement_read",
                        at=at, session_id=ctx["session_id"],
                        price_per_contract=vp["price"])
                    out["settled"] += 1 if r.get("ok") and not r.get(
                        "duplicate") else 0
                    out.setdefault("settled_at_venue_price", 0)
                    out["settled_at_venue_price"] += 1
                    continue
                out.setdefault("pending_reasons", {})
                out["pending_reasons"][vp.get("why")] = \
                    out["pending_reasons"].get(vp.get("why"), 0) + 1
        if got["outcome"] is None:
            if got["why"] == "CONFLICTING_SETTLEMENT_EVIDENCE":
                out["conflicts"] += 1
                from .paper_derek import _finding
                await _finding(conn, ctx, kind="CONFLICTING_SETTLEMENT_"
                               "EVIDENCE", subject=p["position_key"],
                               detail=got, severity="WARNING")
            elif p["open_qty"] > 1e-9:
                out["waiting"] += 1
            continue
        evidence = {"rows": got["evidence"], "rule": "unanimous venue-joined "
                    "outcome rows for this contract"}
        if p["open_qty"] > 1e-9 and p.get("settlement") is None:
            r = await L.settle(conn, account_id=acct, group_id=p["group_id"],
                               slug=p["us_market_slug"],
                               holding_side=p["holding_side"],
                               settlement_event_key=key,
                               outcome=got["outcome"], evidence=evidence,
                               evidence_source="external_valuations."
                               "outcome_basis", at=at,
                               session_id=ctx["session_id"])
            out["settled"] += 1 if r.get("ok") and not r.get(
                "duplicate") else 0
        elif p.get("settlement") and p["settlement"]["outcome"] != \
                got["outcome"]:
            r = await L.correct_settlement(
                conn, account_id=acct, group_id=p["group_id"],
                slug=p["us_market_slug"], holding_side=p["holding_side"],
                settlement_event_key=key, outcome=got["outcome"],
                evidence=evidence, evidence_source="external_valuations."
                "outcome_basis", at=at, session_id=ctx["session_id"])
            out["corrected"] += 1 if r.get("ok") else 0
    # Open orders on a settled market can never fill: release them.
    for o in await conn.fetch(
            "SELECT o.order_id FROM paper_orders o WHERE o.account_id=$1 "
            "   AND o.state = ANY($2::text[]) AND EXISTS (SELECT 1 FROM "
            "   paper_settlements s WHERE s.account_id=o.account_id "
            "   AND s.us_market_slug=o.us_market_slug)", acct,
            list(L.OPEN_STATES)):
        await L.release_remainder(conn, order_id=o["order_id"],
                                  reason="MARKET_SETTLED", at=at,
                                  state="CANCELED")
    return out
