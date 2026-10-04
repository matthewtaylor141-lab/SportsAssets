"""Synthetic rows for the lost opportunity proofs (tests/test_lost_opportunity_*).

ALL DATA HERE IS SYNTHETIC TEST DATA written into a scratch test database
inside a transaction the test rolls back, under a fresh paper account.
Decisions are shaped like Derek's own records (agents/paper_derek.py):
refusal codes, the policy decision's figures, the Pinnacle reading's age and
limit, the observed book, the internal model record.
"""
from __future__ import annotations

import json
import time

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
    from tests import pos_fixture as P
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H
    import pos_fixture as P

HOUR = 3600.0
DAY = 86400.0
DEREK = "DEREK_ENTRY_POLICY_V2"


def pd_fig(net, *, edge=0.09, thr=0.05, qty=10.0, price=0.50, fees=0.10,
           min_net=0.0, switch=None) -> dict:
    """A policy decision's own figures (decide_entry's RECORD_FIELDS)."""
    out = {"policy_name": DEREK, "net_expected_profit_usd": net,
           "gross_edge_fraction": edge, "threshold_gross_edge_fraction": thr,
           "edge_tolerance_fraction": 1e-9, "min_net_ev_usd": min_net,
           "executable_price": price, "qty": qty, "fees_usd": fees,
           "acquisition_cost_usd": round(price * qty, 6)}
    if switch is not None:
        out["entries_switch"] = {"enabled": switch, "why": "TEST_SWITCH"}
    return out


def book_rec(levels=((0.50, 100),)) -> dict:
    return {"book_obs_id": None, "observed_at": time.time(), "error": None,
            "levels": [{"price": p, "qty": q} for p, q in levels],
            "depth_levels": len(levels)}


async def account(conn, *, now):
    return await H.new_account(conn, "lol", now=now - 40 * DAY)


async def decision(conn, acct, *, at, refusals, slug=None, side="LONG",
                   p_internal=0.60, p_pinnacle=0.58, pd=None, econ=None,
                   pinnacle=None, book=None, proposed_qty=None,
                   label=None, strategy=DEREK, fixture="fx-test",
                   valuation_id=None) -> dict:
    did = "paperdec:" + F.uid()
    slug = slug or F.uid("lol-mkt-")
    p_blended = (None if p_internal is None or p_pinnacle is None
                 else (p_internal + p_pinnacle) / 2.0)
    label = label if label is not None else {
        "competition": "MLB", "event_key": "e-" + slug, "unknown": {}}
    pin = pinnacle if pinnacle is not None else {
        "p": p_pinnacle, "qualified": True, "qualification": "FRESH",
        "at": at - 10, "age_s": 10.0, "limit_s": 60.0}
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, fixture, "
        " label, verdict, refusal, refusals, p_internal, internal_model, "
        " p_pinnacle, pinnacle, p_blended, book, proposed_qty, economics, "
        " qualification_gaps, policy_version, policy_decision, "
        " simulator_version, strategy) VALUES ($1,$2,$3,to_timestamp($4),"
        " $5,$6,$7,$8,$9::jsonb,'REFUSE',$10,$11,$12,$13::jsonb,$14,"
        " $15::jsonb,$16,$17::jsonb,$18,$19::jsonb,'[]'::jsonb,$20,"
        " $21::jsonb,$22,$23)",
        did, acct["session_id"], acct["account_id"], float(at), valuation_id,
        slug, side, fixture, json.dumps(label), refusals[0], list(refusals),
        p_internal, json.dumps({"p": p_internal, "model_id": "m-test"}),
        p_pinnacle, json.dumps(pin), p_blended,
        None if book is None else json.dumps(book), proposed_qty,
        None if econ is None else json.dumps(econ), DEREK,
        None if pd is None else json.dumps(pd), F.SIM_VERSION, strategy)
    return {"decision_id": did, "slug": slug, "side": side, "at": at}


async def settle(conn, *, slug, side="LONG", outcome="WON", at, now):
    """The market's settlement, recorded on ANOTHER paper position (the
    refused decision has none)."""
    other = await H.new_account(conn, "lolset", now=now - 40 * DAY)
    ppc = {"WON": 1.0, "LOST": 0.0, "VOID_REFUND": 0.5}[outcome]
    return await P.settle(conn, other, group_id="paper_group_" + F.uid(),
                          slug=slug, side=side, qty=1, outcome=outcome,
                          ppc=ppc, at=at)
