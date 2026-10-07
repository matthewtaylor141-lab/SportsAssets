"""BETTOR integration of the Alpha Proof Lab (research / shadow only).

Synthetic rows shaped exactly like research/apl_extract_v1.sql output; no
database, network, order or capital access."""
from __future__ import annotations

import ast
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "bettor_integration"))
import receipt as R                                              # noqa: E402

T0 = 1_790_000_000.0


def _rows(n=1500, edge=0.0, seed=3, markets=120):
    """`edge` > 0 gives the recorded probability real information beyond the
    venue mid; 0 makes it pure noise around the mid (no alpha)."""
    rng = np.random.default_rng(seed)
    out = []
    truth = {}
    for i in range(n):
        m = i % markets
        slug = "aec-mlb-t%02d-u%02d-2026-10-%02d" % (m, m + 1, 1 + m % 28)
        mid_true = truth.setdefault(slug, float(rng.uniform(0.25, 0.75)))
        y_long = truth.setdefault(slug + ":y", float(rng.random() < mid_true))
        noise = rng.normal(0, 0.03)
        mid = min(0.95, max(0.05, mid_true + rng.normal(0, 0.02)))
        bid, ask = round(mid - 0.01, 3), round(mid + 0.01, 3)
        p = min(0.97, max(0.03, mid + edge * (y_long - mid) + noise))
        at = T0 + i * 600
        out.append({"id": "d%05d" % i, "at": at, "strategy": "S",
                    "slug": slug, "side": "LONG", "verdict": "REFUSE",
                    "p_pin": p, "p_int": None, "p_blend": None,
                    "y_long": y_long, "label_src": "PAPER_SETTLEMENT",
                    "sports_type": "moneyline", "league": "mlb",
                    "start": at + 3 * 3600, "bid": bid, "ask": ask,
                    "bid_q": 100.0, "ask_q": 100.0, "book_at": at,
                    "close_bid": bid, "close_ask": ask,
                    "n15_min_ask": ask - 0.02, "n15_max_bid": bid, "n15_obs": 3})
    return out


def _build(rows):
    return R.build_receipt(rows, code_sha="c" * 64, data_sha=R.dataset_sha(rows),
                           generated_at=T0 + 10**7, source={"test": True})


def test_short_side_is_the_complement_view():
    r = _rows(1)[0]
    r.update(side="SHORT", bid=0.40, ask=0.44, y_long=1.0, close_bid=0.5,
             close_ask=0.52)
    o = R.orient(r)
    assert o["y"] == 0.0
    assert o["bid"] == pytest.approx(0.56) and o["ask"] == pytest.approx(0.60)
    assert o["close_mid"] == pytest.approx(0.49)


def test_a_crossed_or_missing_book_is_not_usable():
    r = _rows(1)[0]
    r.update(bid=0.5, ask=0.5)
    assert R.orient(r) is None
    r.update(bid=None)
    assert R.orient(r) is None


def test_fees_come_from_bettors_published_schedule_and_rebate_is_negative():
    taker, basis = R.fee_per_contract(0.5, "TAKER", "2026-10-01")
    maker, _ = R.fee_per_contract(0.5, "MAKER", "2026-10-01")
    assert taker > 0 and "calibration_fees.expected_fee" in basis
    assert maker <= 0


def test_a_fee_the_schedule_refuses_is_unmeasured_never_zero():
    fee, why = R.fee_per_contract(0.5, "TAKER", "2020-01-01")
    assert fee is None and why


def test_no_alpha_means_cash_and_least_negative_is_not_promoted():
    rec = _build(_rows(1500, edge=0.0))
    assert rec["verdict"] == R.STATUS_CASH
    assert all(not v["eligible"] for v in rec["strategies"].values())
    statuses = {v["status"] for v in rec["strategies"].values()}
    assert "ACTIVE_CHAMPION" not in statuses | {rec["verdict"]}


def test_even_real_alpha_is_not_eligible_without_forward_and_management_cost():
    rec = _build(_rows(1500, edge=0.6))
    for v in rec["strategies"].values():
        assert "MANAGEMENT_COST_UNMEASURED" in v["reasons"]
        assert "NO_FORWARD_SHADOW_EVIDENCE" in v["reasons"]
        assert v["eligible"] is False
    assert rec["verdict"] == R.STATUS_CASH


def test_holdout_is_chronologically_last_and_never_in_walk_forward():
    rec = _build(_rows(1500))
    sp = rec["split"]
    assert sp["development_rows"] + sp["holdout_rows"] == rec["rows_usable"]
    assert sp["holdout_rows"] == rec["rows_usable"] - int(rec["rows_usable"] * 0.8)
    assert rec["walk_forward"]["oos_rows"] <= sp["development_rows"]


def test_all_twelve_outputs_and_a_content_addressed_model_card():
    rec = _build(_rows(1500, edge=0.3))
    for k in ("1_market_prior_vs_residual", "2_calibration_raw_vs_calibrated",
              "3_executable_ev_components", "4_maker_taker_wait", "5_clv",
              "6_pbo_multiple_testing", "7_deflated_sharpe",
              "8_capacity_frontier", "9_correlation_aware_sizing",
              "10_expected_vs_realized", "11_model_card",
              "12_holdout_and_forward_shadow"):
        assert k in rec, k
    mc = rec["11_model_card"]
    assert len(mc["model_card_sha256"]) == 64
    assert mc["card"]["capital_status"] == rec["verdict"]
    assert rec["12_holdout_and_forward_shadow"]["forward_shadow"]["status"] == \
        "ACCUMULATING"


def test_cluster_bound_counts_markets_not_rows():
    ci = R.cluster_mean_ci([1.0, 1.0, -1.0, -1.0], ["a", "a", "b", "b"])
    assert ci["markets"] == 2 and ci["n"] == 4 and ci["mean"] == 0.0


def test_small_segments_are_unmeasured_not_guessed():
    rec = _build(_rows(1500))
    by = rec["2_calibration_raw_vs_calibrated"]["by_sport_family_regime"]
    for v in by.values():
        if v.get("status"):
            assert v["status"] == "UNMEASURED_FALLS_BACK_TO_POOLED"


def test_the_integration_has_no_order_db_network_or_capital_path():
    for p in (LAB / "bettor_integration").glob("*.py"):
        tree = ast.parse(p.read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module)
        for bad in ("asyncpg", "psycopg", "requests", "httpx", "urllib",
                    "socket", "aiohttp"):
            assert not any(m.split(".")[0] == bad for m in mods), (p, bad)
        src = p.read_text()
        for bad in ("submit_order", "request_cancel", "bettor_funded",
                    "live_executor", "place_order"):
            assert bad not in src, (p, bad)


def test_parse_extract_log_reads_only_json_rows():
    rows = _rows(2)
    log = "\n".join(["== header ==", " id | x"] +
                    [" " + json.dumps(r) for r in rows] + ["(2 rows)"])
    assert R.parse_extract_log(log) == rows
