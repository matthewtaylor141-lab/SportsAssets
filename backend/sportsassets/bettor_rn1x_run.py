"""ONE REAL RN1-SEEDED POSITION, MANAGED END TO END.

THE CHAIN, each step naming the module that does the work. Nothing here
implements a book, an exit engine or an accounting model:

    1 SOURCE      a real `trades` row              OBSERVED_INPUT
    2 CLASSIFY    bettor_entry_kind                OBSERVED_INPUT
    3 SEED        bettor_mgmt_lifecycle.Managed    ASSIGNED
    4 WHETHER     bettor_mgmt_select.exposure_trigger   DECLARED_RULE
    5 HOW         bettor_mgmt_select.rank_priced_actions DECLARED_RULE
    6 ORDER       bettor_desk.Order via Managed.place
    7 FILL        Managed.on_print                 EXECUTION_ASSUMPTION
    8 SETTLE      Managed.settle_at_observed_payout  OBSERVED, SCORING ONLY
    9 COMPARE     hold-to-settlement and RN1's own path

EVERY ACTION NAMES ITS SOURCE. `source_class` is attached to each figure
in the trace, drawn from `bettor_rn1x_policy.SOURCE_CLASS`, so no number
in a published trace is unattributed.

WHAT THE POLICY MAY SEE AT STEP 4 AND 5. Only evidence at or before the
instant being decided. Not the settlement payout -- it enters at step 8
alone. Not RN1's later actions; those are the benchmark, reconstructed
separately at step 9 and never fed to our decision.

HISTORICAL REPLAY AND FORWARD SHADOW ARE LABELLED SEPARATELY. `mode` is
HISTORICAL_REPLAY when the condition has already resolved and the run is
being scored, FORWARD_SHADOW when it has not. A scored historical result
and a live one are never summed.
"""

from __future__ import annotations

from . import bettor_entry_kind as ek
from . import bettor_mgmt_lifecycle as lc
from . import bettor_rn1x_policy as pol

VERSION = "BETTOR_RN1X_RUN_V1"

HISTORICAL = "HISTORICAL_REPLAY"
FORWARD = "FORWARD_SHADOW"


def _fee_fn():
    """The production schedule, taker side for our own executions."""
    from . import bettor_fee_schedule as FEES
    from decimal import Decimal

    def fee(qty, price, maker=False):
        return float(FEES.LATEST.fill_fee(Decimal(str(round(float(qty), 6))),
                                          Decimal(str(round(float(price), 6))),
                                          maker=bool(maker)))
    return fee


def run(*, rows, payouts=None, resolved_at=None, source_whale_id,
        queue_share=0.25, condition_id=None, fee_fn=None,
        fee_basis="TRANSFERRED_PMUS_LATEST_SCENARIO",
        initial_inventory_verified=False, policy_params=None):
    """`rows` are the condition's fills, ours and others', in any order.

    Each row: id, whale_id, outcome_index, side, size, price, ts,
    detected_at. `payouts` maps outcome_index -> observed payout and is
    used at step 8 ONLY.
    """
    fee = fee_fn or _fee_fn()
    qs = float(queue_share)
    if not 0 <= qs <= 1:
        raise ValueError("queue_share must be between zero and one")
    # A retrospective replay of an unresolved market is not prospective.
    mode = HISTORICAL
    unique = {}
    for row in rows:
        key = int(row["id"])
        if key in unique and unique[key] != row:
            raise ValueError("conflicting records for trade %s" % key)
        unique[key] = row
    rows = sorted(unique.values(), key=lambda r: (float(r["detected_at"]), int(r["id"])))
    mine = [r for r in rows if int(r["whale_id"]) == int(source_whale_id)]

    out = {"version": VERSION, "mode": mode,
           "policy": pol.describe(), "steps": {},
           "condition_id": condition_id, "queue_share": qs,
           "fee_basis": fee_basis, "prospective": False,
           # None is the FROZEN champion. Anything else is a challenger
           # arm and is labelled as one on the run, not only per decision.
           "policy_params": dict(policy_params) if policy_params else None,
           "arm": "CHAMPION" if not policy_params else "CHALLENGER"}

    # ── 1 SOURCE ────────────────────────────────────────────────────
    if not mine:
        out["failed_step"] = "SOURCE"
        out["steps"]["SOURCE"] = {"ok": False,
                                  "why": "no fill by the source account"}
        return out
    seed_row = mine[0]
    out["steps"]["SOURCE"] = {
        "ok": True, "source_class": "OBSERVED_INPUT",
        "trade_id": seed_row["id"], "whale_id": seed_row["whale_id"],
        "outcome_index": seed_row["outcome_index"],
        "side": seed_row["side"], "size": float(seed_row["size"]),
        "price": float(seed_row["price"]),
        "source_ts": float(seed_row["ts"]),
        "detected_ts": float(seed_row["detected_at"])}

    # ── 2 CLASSIFY, over the account's OWN prior fills only ─────────
    hist = [ek.Fill(source_ts=float(r["ts"]),
                    detected_ts=float(r["detected_at"]),
                    outcome_index=r["outcome_index"], side=r["side"],
                    size=float(r["size"]), price=float(r["price"]),
                    trade_id=int(r["id"]))
            for r in mine]
    if not initial_inventory_verified:
        out["failed_step"] = "CLASSIFY"
        out["steps"]["CLASSIFY"] = {
            "ok": False, "kind": ek.UNKNOWN,
            "why": "initial flat inventory/history completeness not verified"}
        return out
    # Classify only the available seed; future or late-received history
    # cannot retroactively establish inventory at this decision.
    cls = ek.classify_condition(hist[:1])
    first = cls[0]
    out["steps"]["CLASSIFY"] = {"ok": True,
                                "source_class": "OBSERVED_INPUT",
                                **first.to_dict(),
                                "census": ek.census(cls)}
    if first.kind == ek.UNKNOWN:
        out["failed_step"] = "SEED"
        out["steps"]["SEED"] = {
            "ok": False, "unknown_reason": first.unknown_reason,
            "why": ("prior inventory cannot be established, so no "
                    "position is assigned. Seeding here would invent the "
                    "position the classifier just said we cannot see")}
        return out
    if seed_row["side"] != "BUY":
        out["failed_step"] = "SEED"
        out["steps"]["SEED"] = {"ok": False,
                                "why": "the source event is a SELL"}
        return out

    # ── 3 SEED ──────────────────────────────────────────────────────
    decision_ts = float(seed_row["detected_at"])
    m = lc.Managed(condition_id=condition_id or "c",
                   outcome_index=int(seed_row["outcome_index"]),
                   seed_qty=float(seed_row["size"]),
                   seed_price=float(seed_row["price"]),
                   at=decision_ts, fee_fn=fee, queue_share=qs,
                   expiry_s=900.0, account="rn1x-%s" % seed_row["id"])
    out["steps"]["SEED"] = {
        "ok": True, "source_class": "ASSIGNED",
        "entry_kind": first.kind, "qty": m.seed_qty,
        "price": m.seed_price, "basis_usd": m.seed_basis,
        "decision_ts": decision_ts,
        "label": ("ASSIGNED ENTRY at RN1's own fill price. NOT evidence "
                  "that we could have obtained this fill")}

    # ── 4-7, walking forward. Only rows after the decision instant. ──
    events = []
    m.manage_policy(at=decision_ts, decision_id="seed:%s" % seed_row["id"],
                    policy_params=policy_params)
    for r in rows:
        at = float(r["detected_at"])
        if at <= decision_ts:
            continue
        if resolved_at and at > float(resolved_at):
            continue
        oi = int(r["outcome_index"])
        px = float(r["price"])
        eid = "trade:%s" % r["id"]
        m.expire(at)
        # Only an order predating the source print can consume it. Late
        # receipt does not create a new execution opportunity.
        fills = m.on_print(at=float(r["ts"]), outcome_index=oi, price=px,
                           size=float(r["size"]), evidence_id=eid)
        m.acknowledge_cancels(at)
        # Tape prints are not bids, asks, depth or event progress.
        d = m.manage_policy(at=at, decision_id="rn1x-%s" % r["id"],
                            policy_params=policy_params)
        if d.get("acted") or fills:
            events.append({"at": at, "evidence_id": eid,
                           "by_source_account":
                               int(r["whale_id"]) == int(source_whale_id),
                           "decision": d, "fills": fills})
    out["steps"]["MANAGE"] = {
        "ok": True, "decisions": len(m.decisions),
        "acted": sum(1 for d in m.decisions if d.get("acted")),
        "events": events,
        "source_class": {"trigger": pol.SOURCE_CLASS["loss_trigger_fraction"],
                         "method": pol.SOURCE_CLASS["pair_target_cost"],
                         "fills": pol.SOURCE_CLASS["our_fill"]}}

    # ── 8 SETTLE, the only place a payout is read ────────────────────
    st_before = m.state()
    settled = None
    if payouts:
        if resolved_at is None or float(resolved_at) < decision_ts:
            raise ValueError("settlement must have an observed time after the seed")
        payouts = {int(k): float(v) for k, v in payouts.items()}
        settled = m.settle_at_observed_payout(
            {int(k): float(v) for k, v in payouts.items()},
            float(resolved_at or decision_ts))
    final = m.state()
    out["steps"]["SETTLE"] = {
        "ok": True, "source_class": pol.SOURCE_CLASS["settlement_payout"],
        "settled": settled,
        "note": ("the payout is read HERE and nowhere earlier. Steps 4 "
                 "and 5 never saw it"),
        "before_settlement": {
            "matched_qty": st_before["matched_qty"],
            "residual_qty": st_before["residual_qty"],
            "inventory_cost_usd":
                st_before["portfolio"]["inventory_cost_usd"]}}

    # ── 9 COMPARE, from the IDENTICAL assigned inventory ─────────────
    hold_net = None
    if payouts:
        p = float(payouts.get(int(seed_row["outcome_index"]), 0.0)
                  if isinstance(payouts, dict) else 0.0)
        hold_net = p * m.seed_qty - m.seed_basis
    rn1_after = [r for r in mine if float(r["ts"]) > decision_ts]
    out["steps"]["COMPARE"] = {
        "ok": True,
        "ours_net_usd": final["portfolio"]["realized_pnl_usd"],
        "hold_to_settlement_net_usd": hold_net,
        "rn1_subsequent_actions": len(rn1_after),
        "rn1_benchmark": (
            pol.BENCHMARKS["RN1_MANAGEMENT"] if rn1_after else
            "NOT RECONSTRUCTIBLE: the source account took no further "
            "action on this condition inside the observed window, so "
            "there is no management path of theirs to compare against"),
        "identical_starting_inventory": True}

    out["final_state"] = final
    # EVERY DECISION, NOT JUST THE ONES THAT ACTED. `steps.MANAGE.events`
    # carries only the instants where an order moved or a fill landed,
    # which is the activity-looking subset; a persisted record built from
    # it alone would show the experiment doing things and hide the state
    # it is in most of the time, which for this policy is
    # HOLD_NO_FEASIBLE_PAIR. The store writes this list.
    out["all_decisions"] = list(m.decisions)
    out["accounting"] = {
        "realized_pnl_usd": final["portfolio"]["realized_pnl_usd"],
        "fees_usd": final["portfolio"]["fees_usd"],
        "open_inventory_at_cost_usd":
            final["portfolio"]["inventory_cost_usd"],
        "open_inventory_mark": "NOT_IDENTIFIED",
        "matched_qty": final["matched_qty"],
        "residual_qty": final["residual_qty"],
        "inventory_discrepancy_qty": final["inventory_discrepancy_qty"],
        "invariant": final["invariant"],
        "reconciles": bool(final["invariant"].get("ok")),
    }
    out["does_not_establish"] = list(pol.DOES_NOT_ESTABLISH)
    return out
