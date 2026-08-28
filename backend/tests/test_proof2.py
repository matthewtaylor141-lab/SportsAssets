"""PROOF-2 thesis meter: the decomposed estimator's math, pinned.

The capture term must be outcome-free and exact per fill; the whale
mix must weight by OUR entry notional; unpublished whales must dilute
(edge 0) rather than inflate; and the thesis grid must scale profit
with FLOW, never with principal — flow is the venue's constraint, not
our bankroll's.
"""

from __future__ import annotations

import math

from sportsassets.api.proof2 import (
    _phi, capture_from_rows, combine, kalshi_fee, thesis,
)


def _row(his, ours, shares, venue="polymarket-us", whale="rn1"):
    return {"his_price": his, "fill_price": ours, "shares": shares,
            "venue": venue, "whale": whale}


def test_capture_is_exact_and_outcome_free():
    rows = [
        _row(0.50, 0.52, 100),          # paid 2c over on 100 shares
        _row(0.40, 0.40, 50),           # matched him exactly
    ]
    c = capture_from_rows(rows)
    assert c["n"] == 2
    # drag dollars: 100 x 0.02 + 0 = $2.00 — deterministic, no outcome
    assert abs(c["drag_usd"] - 2.0) < 1e-9
    # entry notional at HIS prices: 50 + 20 = 70
    assert abs(c["entry_notional"] - 70.0) < 1e-9
    assert abs(c["drag_rate"] - 2.0 / 70.0) < 1e-12
    assert c["fee_usd"] == 0.0, "PM legs carry no venue fee"


def test_kalshi_legs_carry_the_published_fee():
    rows = [_row(0.50, 0.50, 100, venue="kalshi")]
    c = capture_from_rows(rows)
    assert abs(c["fee_usd"] - kalshi_fee(100, 0.50)) < 1e-9
    assert abs(c["fee_usd"] - 0.07 * 100 * 0.25) < 1e-9


def test_unusable_rows_are_excluded():
    rows = [_row(None, 0.5, 10), _row(0.5, None, 10),
            _row(0.5, 0.5, 0), _row(0, 0.5, 10), _row(0.5, 0.5, 10)]
    assert capture_from_rows(rows)["n"] == 1


def test_combine_weights_by_our_entry_mix_and_dilutes_unpublished():
    rows = [_row(0.50, 0.50, 180, whale="rn1"),      # $90 entry
            _row(0.50, 0.50, 20, whale="mystery")]   # $10 entry
    cap = capture_from_rows(rows)
    bench = {"per_whale": {"rn1": {
        "edge_roi": 0.02, "edge_ci95": [0.01, 0.03]}}}
    e = combine(cap, bench)
    assert e["available"] is True
    # mystery has no published CI: contributes 0 edge — dilution
    assert abs(e["whale_mix_edge"] - 0.9 * 0.02) < 1e-9
    assert e["unpublished_whales"] == ["mystery"]
    # zero drag, zero fees here: sleeve edge = mix edge
    assert abs(e["sleeve_edge"] - e["whale_mix_edge"]) < 1e-12
    assert 0.5 < e["p_edge_positive"] < 1.0


def test_drag_subtracts_from_the_whale_edge():
    rows = [_row(0.50, 0.52, 100, whale="rn1")]      # 4% drag rate
    cap = capture_from_rows(rows)
    bench = {"per_whale": {"rn1": {
        "edge_roi": 0.02, "edge_ci95": [0.019, 0.021]}}}
    e = combine(cap, bench)
    # 2% whale edge minus 4% drag: the sleeve edge is NEGATIVE and
    # the meter must say so — this is the honest direction
    assert e["sleeve_edge"] < 0
    assert e["p_edge_positive"] < 0.5


def test_thesis_scales_with_flow_not_principal():
    edge = {"available": True, "sleeve_edge": 0.03, "sleeve_se": 0.005}
    t = thesis(edge, flow_per_day=13_000.0)
    assert t["available"] is True
    grid = {g["principal"]: g for g in t["grid"]}
    # at 1x today's flow, annual mu = 13k x 365 x 3% = ~$142k:
    # virtually certain to clear 100% of $100k...
    assert grid[100_000]["p_100pct_at_1x_flow"] > 0.95
    # ...and virtually impossible to clear 100% of $2M
    assert grid[2_000_000]["p_100pct_at_1x_flow"] < 0.05
    # the build-out requirement is named: ~14x flow for $2M @ p50
    need = grid[2_000_000]["flow_x_for_p50"]
    assert 13 <= need <= 15
    # more flow monotonically raises the probability
    row = grid[2_000_000]
    assert row["p_100pct_at_50x_flow"] > row["p_100pct_at_10x_flow"] \
        >= row["p_100pct_at_1x_flow"]


def test_phi_sanity():
    assert abs(_phi(0.0) - 0.5) < 1e-12
    assert _phi(1.96) > 0.974 and _phi(-1.96) < 0.026
    assert abs(_phi(1.6449) - 0.95) < 0.001


def test_payload_end_to_end_with_fake_pool():
    import asyncio
    import datetime as dt
    import json as _json

    at = dt.datetime(2026, 8, 27, 12, 0, tzinfo=dt.timezone.utc)

    class _Pool:
        async def fetch(self, sql, *a, timeout=None):
            return [
                {"his_price": 0.50, "fill_price": 0.51, "shares": 100.0,
                 "venue": "polymarket-us", "whale": "rn1",
                 "placed_at": at},
                {"his_price": 0.40, "fill_price": 0.40, "shares": 50.0,
                 "venue": "kalshi", "whale": "rn1", "placed_at": at},
                # pre-cohort row: excluded
                {"his_price": 0.30, "fill_price": 0.90, "shares": 999.0,
                 "venue": "polymarket-us", "whale": "rn1",
                 "placed_at": at - dt.timedelta(days=30)},
            ]

        async def fetchval(self, sql, *a, timeout=None):
            return _json.dumps({"per_whale": {"rn1": {
                "edge_roi": 0.03, "edge_ci95": [0.02, 0.04]}},
                "measured_at": "2026-08-28T00:00:00+00:00"})

    from sportsassets.api.proof2 import proof2_payload
    out = asyncio.run(proof2_payload(_Pool()))
    assert out["capture"]["n"] == 2, "pre-cohort rows are excluded"
    assert out["edge"]["available"] is True
    assert out["thesis"]["available"] is True
    assert out["benchmark_measured_at"] == "2026-08-28T00:00:00+00:00"
    # the $2M row exists and names its flow requirement
    row = [g for g in out["thesis"]["grid"]
           if g["principal"] == 2_000_000][0]
    assert "p_100pct_at_1x_flow" in row


def test_sell_rows_mirror_the_drag_sign():
    """First-print finding (2026-08-28): mirror-exit SELL rows carry
    the OPPOSITE drag sign — exiting below his price is the cost.
    The unsigned version scored a good exit as drag."""
    good_exit = dict(_row(0.50, 0.55, 100), side="SELL")   # sold 5c ABOVE
    bad_exit = dict(_row(0.50, 0.45, 100), side="SELL")    # sold 5c below
    c = capture_from_rows([good_exit])
    assert c["drag_usd"] < 0, "beating his exit price is negative drag"
    c = capture_from_rows([bad_exit])
    assert abs(c["drag_usd"] - 5.0) < 1e-9, \
        "exiting below him costs (his - ours) x shares"


def test_worst_rows_name_the_drag_carriers():
    rows = [_row(0.50, 0.51, 10) for _ in range(10)]
    rows.append(dict(_row(0.10, 0.70, 500), slug="the-culprit"))
    c = capture_from_rows(rows)
    assert len(c["worst_rows"]) <= 8
    top = c["worst_rows"][0]
    assert top["slug"] == "the-culprit"
    assert abs(top["drag_usd"] - 300.0) < 1e-6
    assert top["side"] == "BUY" and top["his"] == 0.1


def test_short_copies_are_denominated_by_cost_per_share(monkeypatch):
    """First-print finding #2 (the PRICEFID short bug's family): on a
    BUY_SHORT copy the venue's fill_price names the LONG leg — his
    0.10 underdog entry recorded as ~0.90 fabricated ~80c/share of
    drag on our BEST fills. capture must denominate through
    cost_per_share, the one owner of the long/short conversion."""
    from sportsassets import live_executor as le

    monkeypatch.setattr(le, "short_model_confirmed", lambda: True)
    row = dict(_row(0.10, 0.87, 100),
               intent="MARKET_ORDER_INTENT_BUY_SHORT")
    c = capture_from_rows([row])
    # our true cost is 1 - 0.87 = 0.13: drag = (0.13 - 0.10) x 100
    assert abs(c["drag_usd"] - 3.0) < 1e-9, \
        "3c of real slippage, not 77c of fabricated drag"
    assert abs(c["worst_rows"][0]["ours"] - 0.13) < 1e-9
    # a long row is untouched by the conversion
    c2 = capture_from_rows([_row(0.50, 0.52, 100)])
    assert abs(c2["drag_usd"] - 2.0) < 1e-9


def test_ledger_sql_carries_the_one_intent_expression():
    """The intent LEDGER_SQL exposes must stay the same expression
    ORDER_INTENT_SQL owns — normalized-whitespace containment, so a
    change to either side breaks this pin and forces a re-sync."""
    from sportsassets.api.copy_reports import LEDGER_SQL
    from sportsassets.live_executor import ORDER_INTENT_SQL

    norm = " ".join(ORDER_INTENT_SQL.split())
    assert norm in " ".join(LEDGER_SQL.split()), \
        "one expression owns the intent read"
