"""BETTOR integration of the EV Probability Engine (research / shadow only).
Synthetic rows shaped exactly like research/ev_extract_v1.sql output and the
oriented frame; no database, network, order or capital access."""
from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path

import numpy as np
import pytest

BI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BI))
import dataset as D                                   # noqa: E402
import models as M                                    # noqa: E402
import receipt as R                                   # noqa: E402
import run as RUN                                     # noqa: E402

T0 = 1_790_900_000.0


def _frame(n_events=260, rows_per_event=3, info=0.0, seed=5):
    """Oriented frame rows. info=0: BETTOR raw = venue mid + noise (no edge);
    info>0: raw carries real information about the outcome."""
    rng = np.random.default_rng(seed)
    out = []
    for e in range(n_events):
        sport = ("mlb", "nfl")[e % 2]
        truth = float(rng.uniform(0.2, 0.8))
        y = float(rng.random() < truth)
        venue = float(np.clip(truth + rng.normal(0, 0.06), 0.05, 0.95))
        at = T0 + e * 3600
        for k in range(rows_per_event):
            raw = float(np.clip(venue + info * (truth - venue) + rng.normal(0, 0.03), 0.02, 0.98))
            out.append({"decision_id": "d%d_%d" % (e, k), "at": at + k * 900, "event_id": "%s-ev%03d" % (sport, e),
                        "event_src": "PREMAP_EVENT_SLUG", "market_id": "aec-%s-ev%03d" % (sport, e),
                        "side": "LONG", "strategy": "S", "verdict": "ENTER" if k == 0 else "REFUSE",
                        "n_bucket": 1, "sport": sport, "league": sport, "family": "MONEYLINE",
                        "family_class": "MONEYLINE", "regime": "PREGAME", "venue": "POLYMARKET_US",
                        "settlement_identity": "x", "registry_settlement": "NOT_IN_REGISTRY",
                        "p_venue": venue, "p_sharp": raw, "p_raw": raw, "raw_src": "ECONOMICS_PROBABILITY",
                        "p_int": None, "p_close": venue, "bid": venue - 0.01, "ask": venue + 0.01, "spread": 0.02,
                        "top_qty": 100.0, "fee": 0.01, "fee_basis": "test", "outcome": y,
                        "settled_at": at + 4 * 3600, "start": at + 3 * 3600, "time_to_start_min": 180.0,
                        "source_age_s": 10.0, "prob_age_s": 5.0, "book_age_s": 3.0, "v_age_s": 10.0,
                        "model_id": "S|x", "favorite": "FAVORITE" if venue >= .5 else "LONGSHOT",
                        "price_band": "40-60", "prob_band": "40-60", "tts_band": "1-6h",
                        "freshness_band": "0-30s", "label_src": "PAPER_SETTLEMENT"})
    return out


def _run(rows, orders=()):
    return R.run(copy.deepcopy(rows), list(orders), dataset_mod=D, fee_fn=lambda p, t: (0.01, "test"),
                 provenance={"test": True})


# ── dataset ───────────────────────────────────────────────────────────────
def test_log_parser_handles_jsonb_text_bom_and_timestamps():
    row = {"at": 1.0, "id": "x"}
    log = "\n".join(["hdr", " " + json.dumps(row), "﻿2026-10-07T16:00:00.1Z  " + json.dumps(row), "(2 rows)"])
    assert len(D.parse_log(log)) == 2 and D.declared_rows(log) == 2


def test_short_rows_are_the_complement_and_drops_are_counted():
    base = {"id": "a", "at": T0, "strategy": "S", "slug": "aec-mlb-a-b-2026-10-01", "side": "SHORT",
            "bid": 0.40, "ask": 0.42, "y_long": 1.0, "p_pin": 0.6, "p_used": 0.6, "start": T0 + 60}
    rows, dropped = D.frame_rows([base, dict(base, id="b", bid=None), dict(base, id="c", y_long=None)])
    r = rows[0]
    assert r["p_venue"] == pytest.approx(0.59) and r["ask"] == pytest.approx(0.60) and r["outcome"] == 0.0
    assert r["event_id"] == "mlb-a-b-2026-10-01" and r["event_src"] == "SLUG_DERIVED"
    assert dropped == {"NO_TWO_SIDED_VENUE_BOOK": 1, "NO_SETTLED_OUTCOME": 1}


def test_family_class_never_claims_alternate():
    assert D.family_class("tsc-nfl-tb-dal-2026-10-08-tt-dal-28pt5", None) == "TEAM_TOTAL"
    assert D.family_class("asc-nhl-a-b-2026-10-05-pos-1pt5", None) == "SPREAD"


# ── models ────────────────────────────────────────────────────────────────
def test_many_rows_from_few_events_do_not_unlock_a_subgroup_calibrator():
    rows = _frame(n_events=20, rows_per_event=30)          # 600 rows, 20 events
    cal = M.HierarchicalCalibrator("beta", min_events=40).fit(rows)
    p, lev = cal.predict(rows[:5])
    assert all(l[0] == "MARKET_PRIOR_FALLBACK" for l in lev)
    assert np.allclose(p, [r["p_venue"] for r in rows[:5]])


def test_residual_alpha_collapses_without_information_and_survives_with_it():
    for info, expect_zero in ((0.0, True), (0.9, False)):
        rows = _frame(n_events=1500, rows_per_event=2, info=info, seed=8)
        m = M.MarketResidualOffset().fit([r["p_raw"] for r in rows], [r["p_venue"] for r in rows],
                                         [r["outcome"] for r in rows], [r["event_id"] for r in rows])
        assert (m.alpha == 0.0) is expect_zero, (info, m.summary())


# ── pipeline ──────────────────────────────────────────────────────────────
def test_no_edge_is_cash_and_holdout_is_disjoint():
    rec = _run(_frame())
    c = rec["counts"]
    assert c["holdout_events"] > 0 and c["decision_rows"] > c["unique_canonical_events"]
    assert rec["12_capital_verdict"]["verdict"] == "CASH"
    assert rec["12_capital_verdict"]["live_capital"] == "NOT_ACTIVATED"
    assert "ACTIVE" not in json.dumps(rec["12_capital_verdict"])


def test_frozen_selection_does_not_depend_on_holdout_outcomes():
    rows = _frame()
    a = _run(rows)
    flipped = copy.deepcopy(rows)
    hold_from = a["window"]["holdout_from"]
    for r in flipped:
        if r["at"] >= hold_from:
            r["outcome"] = 1.0 - r["outcome"]
    b = _run(flipped)
    assert a["4_walk_forward"]["selection"] == b["4_walk_forward"]["selection"]
    assert a["5_untouched_holdout"]["models"] != b["5_untouched_holdout"]["models"]


def test_walk_forward_trains_only_on_events_settled_before_the_fold():
    rows = _frame()
    for r in rows:
        r["settled_at"] = r["at"] + 30 * 24 * 3600        # nothing settles inside the window
    rec = _run(rows)
    folds = rec["4_walk_forward"]["folds"]
    assert folds and all(f["train_events_settled_before_test"] == 0 for f in folds)
    assert all(f["residual"] == "UNFITTED_INSUFFICIENT_SETTLED_EVENTS_ALPHA_0" for f in folds)
    # with nothing learnable, every calibrated / residual model IS the market prior
    t = rec["3_market_prior_benchmark_walk_forward"]
    for c in ("CALIBRATED_BETA", "CALIBRATED_ISOTONIC", "CALIBRATED_BETA_SHRUNK", "MARKET_RESIDUAL"):
        assert t[c]["event_weighted"] == pytest.approx(t["MARKET_PRIOR_VENUE"]["event_weighted"], rel=1e-9)
    assert rec["12_capital_verdict"]["verdict"] == "CASH"


def test_best_model_that_is_still_a_prior_or_negative_is_never_capital():
    rec = _run(_frame(info=0.0))
    v = rec["12_capital_verdict"]
    assert v["verdict"] == "CASH"
    assert any("NO_POSITIVE_EVENT_CLUSTERED_LOWER_BOUND" in x or "DOES_NOT_BEAT" in x for x in v["reasons"])


def test_unsupported_segments_are_unmeasured_and_on_the_kill_list():
    rows = _frame()
    for r in rows[:9]:
        r["sport"] = "rare"
    rec = _run(rows)
    tab = {t["segment"]: t for t in rec["6_calibration_table_sport_family_regime"]}
    rare = [k for k in tab if k.startswith("rare")]
    for k in rare:
        assert tab[k]["status"] == "UNMEASURED_INSUFFICIENT_EVENTS"
        assert any(x["segment"] == k and "INSUFFICIENT_INDEPENDENT_EVENTS" in x["reasons"] for x in rec["8_kill_list"])


def test_waterfall_reconciles_exactly():
    rows = _frame()
    orders = []
    for i, r in enumerate(rows[:60:3]):
        orders.append({"order_id": "o%d" % i, "group_id": "g%d" % i, "role": "ENTRY", "direction": "BUY",
                       "side": "LONG", "slug": r["market_id"], "strategy": "S", "fill_qty": 100,
                       "vwap": r["ask"], "fees": 1.0, "p_pin": r["p_raw"], "decision_id": r["decision_id"],
                       "sub_bid": r["bid"], "sub_ask": r["ask"], "first_fill": r["at"],
                       "settle_payout": r["outcome"], "settled": r["settled_at"], "start": r["start"],
                       "league": r["sport"], "sports_type": "moneyline", "event": r["event_id"]})
        orders.append({"order_id": "s%d" % i, "group_id": "g%d" % i, "role": "STANDING_PROTECTION",
                       "direction": "SELL", "side": "LONG", "slug": r["market_id"], "fill_qty": 30,
                       "vwap": 0.5, "fees": 0.2})
    wf = _run(rows, orders)["7_loss_contribution_waterfall"]
    assert wf["positions"] == 20
    assert abs(wf["total"]["identity_check_usd"]) < 1e-6
    for dim, rs in wf["by_dimension"].items():
        assert abs(sum(r["error_usd"] for r in rs) - wf["total"]["error_usd"]) < 1e-6


def test_feedback_disables_a_segment_that_keeps_underperforming():
    mk = lambda e, gap: {"event_id": "e%d" % e, "qty": 10.0, "expected_usd": 1.0, "realized_usd": 1.0 + gap}
    bad = [mk(e, -5.0 + 0.1 * (e % 3)) for e in range(30)]
    ok = [mk(e, (-1) ** e * 2.0) for e in range(30)]
    few = [mk(e, -5.0) for e in range(5)]
    assert R._feedback_action(bad)["action"] == "DISABLE"
    assert R._feedback_action(ok)["action"] == "MONITOR"
    assert R._feedback_action(few)["action"] == "UNMEASURED_KEEP_SHADOW_ONLY"


def test_runner_refuses_to_overwrite_a_receipt(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    with pytest.raises(SystemExit):
        RUN.main([str(d), "--extract", "x", "--extract-run", "1", "--orders", "x",
                  "--orders-source", "x", "--generated-at", "x"])


def test_integration_has_no_order_db_network_or_capital_path():
    for p in BI.glob("*.py"):
        tree = ast.parse(p.read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module)
        for bad in ("asyncpg", "psycopg", "psycopg2", "requests", "httpx", "urllib", "socket",
                    "aiohttp", "subprocess"):
            assert not any(m.split(".")[0] == bad for m in mods), (p.name, bad)
        for bad in ("submit_order", "request_cancel", "bettor_funded", "live_executor", "place_order"):
            assert bad not in p.read_text(), (p.name, bad)
