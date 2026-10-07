"""P&L ATTRIBUTION on BETTOR's settled paper positions.

Each settled position (positions.build_positions) is mapped onto the package's
AttributionInput, per contract:
  market_prior_p   the side-held mid of the book recorded at submission
  model_p          the decision's recorded probability (p_pinnacle)
  calibrated_p     the same recorded probability: production applies no
                   calibrator to it, so none is invented here
  outcome          the paper settlement payout per contract (0 / 1, or the
                   recorded payout for a void / push)
  expected fill    the side-held ask at submission; actual = fill VWAP
  expected cost    BETTOR's published taker fee at that ask; actual = fees paid
  management       expected 0 (decisions are priced to hold to settlement);
                   realized = what the closing sells earned against holding:
                   sold share x (sell VWAP - outcome) - sell fees per contract
  settlement       expected 0; realized = cash ledger minus the binary model,
                   so the decomposition reconciles to the recorded cash P&L

Expected - realized = outcome variance + execution residual + management
residual + settlement residual, checked by require_reconciled for every row,
and every row's realized P&L is checked against the cash ledger. Positions
sold out before an outcome is recorded have no outcome to attribute against
and are reported, not imputed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict

from common import fee_per_contract, iso, window
from bettor_profit_stack.pnl_attribution import (AttributionInput, aggregate, attribute,
                                                 require_reconciled)

FIELDS = ("expected_profit", "realized_profit", "residual", "forecast_residual",
          "execution_residual", "management_residual", "settlement_residual",
          "outcome_variance", "reconciliation_error")


def to_input(p: dict) -> tuple[AttributionInput | None, str | None]:
    if p["status"] != "SETTLED":
        return None, "POSITION_OPEN"
    if p.get("payout") is None:
        return None, "SOLD_OUT_BEFORE_ANY_RECORDED_OUTCOME"
    if p.get("p") is None:
        return None, "NO_RECORDED_DECISION_PROBABILITY"
    if p.get("touch_ask") is None:
        return None, "NO_BOOK_RECORDED_AT_SUBMISSION"
    fee, why = fee_per_contract(p["touch_ask"], "TAKER", iso(p.get("entered_at")))
    if fee is None:
        return None, "FEE_SCHEDULE_REFUSED:%s" % why
    q = p["qty"]
    y = float(p["payout"])
    sold_share = p["sold_qty"] / q
    mgmt = (sold_share * (p["sell_vwap"] - y) if p["sold_qty"] > 0 else 0.0) - p["sell_fees"] / q
    model_per_contract = y - p["entry_px"] - p["entry_fee"] / q + mgmt
    settle_adj = p["realized_per_contract"] - model_per_contract
    return AttributionInput(
        decision_id=p["decision_id"] or p["group_id"], strategy=p["strategy"], sport=p["sport"],
        family=p["family"], regime=p["regime"], venue=p["venue"], qty=q,
        market_prior_p=p["touch_mid"], model_p=float(p["p"]), calibrated_p=float(p["p"]),
        outcome=y, expected_fill_price=p["touch_ask"], actual_fill_price=p["entry_px"],
        expected_execution_cost=fee, actual_execution_cost=p["entry_fee"] / q,
        expected_management_value=0.0, realized_management_value=mgmt,
        expected_settlement_adjustment=0.0, realized_settlement_adjustment=settle_adj), None


def run(positions: list[dict]) -> dict:
    atts, skipped, ledger_err = [], Counter(), 0.0
    events_of = {}
    for p in positions:
        inp, why = to_input(p)
        if inp is None:
            skipped[why] += 1
            continue
        a = attribute(inp)
        require_reconciled(a, tolerance=1e-6)
        ledger_err = max(ledger_err, abs(a.realized_profit - p["realized_pnl"]))
        atts.append(a)
        events_of[a.decision_id] = p["event"]
    cells = defaultdict(list)
    for a in atts:
        d = a.dimensions
        cells["|".join((d["strategy"], d["sport"], d["family"], d["regime"], d["venue"]))].append(a)
    cell_rows = []
    for k, vs in sorted(cells.items()):
        row = {"cell": k, "positions": len(vs),
               "unique_events": len({events_of[v.decision_id] for v in vs})}
        for f in FIELDS:
            row[f] = sum(getattr(v, f) for v in vs)
        cell_rows.append(row)
    total = {f: sum(getattr(a, f) for a in atts) for f in FIELDS}
    return {
        "module": "PNL_ATTRIBUTION",
        "decision_rows": {"positions_input": len(positions), "attributed": len(atts),
                          "not_attributed": dict(skipped)},
        "unique_independent_events": len(set(events_of.values())),
        "window": window(p.get("settled_at") for p in positions if p["status"] == "SETTLED"),
        "identity": "expected - realized = outcome_variance + execution + management + settlement",
        "max_row_reconciliation_error": max((abs(a.reconciliation_error) for a in atts), default=0.0),
        "max_row_cash_ledger_difference_usd": ledger_err,
        "total": total,
        "by_strategy_sport_family_regime_venue": cell_rows,
        "by_strategy": aggregate(atts, "strategy"),
        "by_family": aggregate(atts, "family"),
        "by_regime": aggregate(atts, "regime"),
        "calibrated_p_note": "RECORDED_P_UNCALIBRATED: production applies no calibrator to p_pinnacle",
    }
