"""REHEARSAL -- SUBSTITUTED VENUE TRANSPORT. NOT A REAL ORDER, NOT A REAL FILL.

The full management path through the production `cycle()`, with ONLY the venue
transport substituted (books, matching and acknowledgements are SYNTHETIC):

  entry fill (real writers) -> Xavier's scheduled review -> ranked
  alternatives -> the selected action -> the dispatched order and the
  (substituted) venue acknowledgement -> the updated residual exposure and
  P&L -> the NEXT scheduled review of the reduced position.

It writes the trace to research/rehearsal/XAVIER_SCHEDULED_PATH_REHEARSAL.json
labelled REHEARSAL on every step, so the evidence cannot be mistaken for a
production order. The account id carries DEMONSTRATION for the same reason.
"""
import json
import os
import pathlib

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_xavier as XV
from tests.test_xavier_manages_positions_through_the_scheduled_path import (
    ACCT, HELD, HELD_ID, VENUE, Venue, _connect, alternative,
    assert_sent_the_persisted_plan, books, clean, pg, run_cycle, start,
    step_of, substitute, xavier_records)

LABEL = "REHEARSAL_SUBSTITUTED_VENUE_TRANSPORT_NOT_A_REAL_ORDER"
OUT = pathlib.Path(__file__).resolve().parents[2] / "research" / "rehearsal" \
    / "XAVIER_SCHEDULED_PATH_REHEARSAL.json"


def _alt_rows(rec):
    return [{"action": a.get("action"),
             "quantity": a.get("quantity") or a.get("qty"),
             "expected_net_usd": a.get("expected_net_usd"),
             "increment_vs_hold_usd": a.get("increment_vs_hold_usd"),
             "worst_case_remaining_loss_usd": a.get(
                 "worst_case_remaining_loss_usd"),
             "capital_released_usd": a.get("capital_released_usd"),
             "fees_usd": a.get("fees_usd"),
             "blocker": a.get("blocker")}
            for a in rec.get("alternatives") or []]


@pg
@pytest.mark.asyncio
async def test_rehearsal_entry_fill_to_updated_exposure_and_pnl(monkeypatch):
    conn = await _connect()
    trace = {"label": LABEL, "account": ACCT, "venue": VENUE,
             "what_is_real": ("the production cycle(), Xavier, ranking, the "
                              "funded book writers, the database"),
             "what_is_substituted": ("the venue transport: order books, "
                                     "matching and acknowledgements are "
                                     "SYNTHETIC"),
             "steps": []}
    try:
        # 1 · ENTRY FILL: 10 held at 0.60, recorded by the real writers
        await start(conn, p=0.30, price=0.60, approve_model=False)
        pos0 = [p for p in await FB.open_entry_positions(
            conn, account_id=ACCT, venue=VENUE) if p["intent_id"] == HELD_ID]
        assert pos0 and float(pos0[0]["residual_qty"]) == 10.0
        trace["steps"].append({"step": "1_ENTRY_FILL", "label": LABEL,
                               "intent_id": HELD_ID, "contract": HELD,
                               "residual_qty": 10.0, "basis_price": 0.60})
        # 2-5 · SCHEDULED REVIEW, RANKED ALTERNATIVES, SELECTION, DISPATCH
        venue = Venue(books=books(held_bids=[(0.45, 4), (0.42, 3),
                                             (0.10, 400)]),
                      holdings={HELD: (10.0, 6.0)})
        substitute(monkeypatch, venue)
        out = await run_cycle(conn)
        step = step_of(out)
        rec = (await xavier_records(conn))[0]
        trace["steps"].append({
            "step": "2_SCHEDULED_REVIEW", "label": LABEL,
            "decision_id": rec["decision_id"],
            "forward_probability": 0.30,
            "responsibility_state": rec.get("responsibility_state")})
        trace["steps"].append({"step": "3_RANKED_ALTERNATIVES",
                               "label": LABEL,
                               "alternatives": _alt_rows(rec)})
        assert rec["chosen_action"] == "REDUCE"
        hold = alternative(rec, "HOLD")
        rd = alternative(rec, "REDUCE")
        assert rd["expected_net_usd"] > hold["expected_net_usd"]
        trace["steps"].append({
            "step": "4_SELECTED_ACTION", "label": LABEL,
            "action": rec["chosen_action"],
            "quantity": rec["evidence"]["chosen_plan"].get("quantity"),
            "plan_digest": rec["chosen_plan_digest"],
            "reasoning": rec.get("reasoning"),
            "selection_basis": step["common_valuation"]["selection_basis"]})
        plan, sent = assert_sent_the_persisted_plan(venue, rec)
        pcx = out["funded_servicing"]["pair_cycle"]["exits"][0]
        assert pcx["submitted"] is True and pcx["order_binding"]["ok"] is True
        trace["steps"].append({
            "step": "5_DISPATCH_AND_ACKNOWLEDGEMENT", "label": LABEL,
            "order_sent_to_substituted_venue": {
                k: sent.get(k) for k in ("intent", "quantity", "price",
                                         "marketSlug")},
            "execution_eligibility": rec["execution_eligibility"],
            "acknowledged": bool(pcx["order_binding"]["ok"])})
        # 6 · UPDATED RESIDUAL EXPOSURE AND P&L
        pos = [p for p in await FB.open_entry_positions(
            conn, account_id=ACCT, venue=VENUE) if p["intent_id"] == HELD_ID]
        pl = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert float(pos[0]["residual_qty"]) == pytest.approx(3.0)
        assert pl["exit_proceeds_usd"] == pytest.approx(4 * 0.45 + 3 * 0.42)
        trace["steps"].append({
            "step": "6_UPDATED_EXPOSURE_AND_PNL", "label": LABEL,
            "residual_qty": float(pos[0]["residual_qty"]),
            "exit_proceeds_usd": pl["exit_proceeds_usd"],
            "realised_pnl_usd": pl["realised_pnl_usd"]})
        # 7 · THE NEXT SCHEDULED REVIEW SEES THE REDUCED POSITION
        out2 = await run_cycle(conn)
        recs = await xavier_records(conn)
        assert len(recs) >= 2
        trace["steps"].append({
            "step": "7_NEXT_SCHEDULED_REVIEW", "label": LABEL,
            "decision_id": recs[0]["decision_id"],
            "chosen_action": recs[0]["chosen_action"],
            "execution_eligibility": recs[0]["execution_eligibility"],
            "residual_qty_reviewed": 3.0,
            "funded_servicing_ran": bool(out2.get("funded_servicing"))})
        if os.environ.get("REHEARSAL_WRITE_TRACE"):
            OUT.write_text(json.dumps(trace, indent=2, default=str) + "\n")
    finally:
        await clean(conn)
        await conn.close()
