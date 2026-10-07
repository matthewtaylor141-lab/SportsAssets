"""BETTOR adapters of the Profitability Stack: synthetic rows shaped exactly
like the research-SQL extracts. No database, network, order or capital access."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import numpy as np
import pytest

AD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AD))
import attribution_adapter as ATT                 # noqa: E402
import common as C                                # noqa: E402
import digital_twin_adapter as TW                 # noqa: E402
import execution_truth_adapter as EX              # noqa: E402
import governor_adapter as GOV                    # noqa: E402
import run_receipts as RR                         # noqa: E402
import structural_arb_adapter as ARB              # noqa: E402
from positions import build_positions             # noqa: E402

T0 = 1_791_000_000.0


# ── common ────────────────────────────────────────────────────────────────
def test_event_identity_prefers_premap_then_slug():
    assert C.event_identity({"event": "brb-rec-ber-2026-10-06", "slug": "x"}) == (
        "brb-rec-ber-2026-10-06", "PREMAP_EVENT_SLUG")
    assert C.event_identity({"slug": "aec-mlb-sd-mil-2026-10-03"}) == (
        "mlb-sd-mil-2026-10-03", "SLUG_DERIVED")
    assert C.event_identity({"slug": "atc-brb-rec-ber-2026-10-06-rec"})[0] == "brb-rec-ber-2026-10-06"
    assert C.event_identity({"slug": "asc-nhl-phi-tb-2026-10-05-pos-1pt5"})[0] == "nhl-phi-tb-2026-10-05"
    assert C.event_identity({"slug": "garbage"}) == (None, "NO_EVENT_IDENTITY")


def test_short_is_the_complement_view_and_crossed_books_are_refused():
    assert C.orient(0.335, 0.34, "SHORT") == pytest.approx((0.66, 0.665))
    assert C.orient(0.5, 0.5, "LONG") is None
    assert C.orient(None, 0.4, "LONG") is None


def test_log_parser_strips_bom_and_timestamp_prefixes():
    row = {"order_id": "a", "x": 1}
    log = "\n".join(["header", " " + json.dumps(row),
                     "﻿2026-10-07T15:26:25.6713909Z  " + json.dumps(dict(row, order_id="b")),
                     "(2 rows)"])
    got = C.parse_extract_log(log, "order_id")
    assert [r["order_id"] for r in got] == ["a", "b"]
    assert RR.declared_rows(log) == 2


def test_cluster_ci_counts_events_not_rows():
    ci = C.cluster_mean_ci([1, 1, 1, -1, -1, -1], ["a", "a", "a", "b", "b", "b"])
    assert ci["events"] == 2 and ci["n_rows"] == 6 and ci["mean"] == 0


def test_fee_refusal_is_unmeasured_never_zero():
    fee, why = C.fee_per_contract(0.5, "TAKER", "2020-01-01T00:00:00Z")
    assert fee is None and why


# ── execution truth ───────────────────────────────────────────────────────
def _decisions(n_events=60, per_event=5, edge=0.0, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for e in range(n_events):
        mid = float(rng.uniform(0.3, 0.7))
        y = float(rng.random() < mid)
        for k in range(per_event):
            at = T0 + e * 3600 + k * 60
            out.append({"id": "d%d_%d" % (e, k), "at": at, "strategy": "S",
                        "slug": "aec-mlb-t%02d-u%02d-2026-10-0%d" % (e, e, 1 + e % 9), "side": "LONG",
                        "p_pin": min(0.97, max(0.03, mid + edge * (y - mid) + rng.normal(0, 0.02))),
                        "y_long": y, "league": "mlb", "sports_type": "moneyline",
                        "start": at + 7200, "bid": round(mid - 0.01, 3), "ask": round(mid + 0.01, 3),
                        "close_bid": round(mid - 0.01, 3), "close_ask": round(mid + 0.01, 3),
                        "n15_min_ask": round(mid + 0.01, 3), "n15_max_bid": round(mid - 0.01, 3),
                        "n15_obs": 3})
    return out


def test_split_never_puts_one_event_in_both_halves():
    fr = EX.frame(_decisions())
    cal, ev = EX.split_by_event(fr)
    assert {d["event"] for d in cal}.isdisjoint({d["event"] for d in ev})
    assert len(cal) + len(ev) == len(fr)


def test_no_alpha_refuses_every_path():
    out = EX.run([], _decisions(edge=0.0))
    assert set(out["evaluation_actions"]) == {"REFUSE"}
    assert out["unique_independent_events"]["evaluation_half"] < out["decision_rows"]["evaluation_half"]


def test_a_path_with_an_unmeasured_cost_is_not_admissible():
    fr = EX.frame(_decisions())
    cal, ev = EX.split_by_event(fr)
    est = EX.calibrate(cal, {"mean": 0.0, "se": None})       # taker slippage unmeasured
    r = EX.decide(ev[0], est)
    assert "TAKE" not in r.get("candidates", {})
    assert "TAKER_SLIPPAGE_UNMEASURED" in r["components_unmeasured"]


def test_short_lag_adverse_selection_is_unmeasured_at_minute_cadence():
    o = {"order_id": "o1", "group_id": "g", "role": "ENTRY", "type": "MARKETABLE", "direction": "BUY",
         "side": "LONG", "slug": "aec-mlb-a-b-2026-10-01", "strategy": "S", "state": "FILLED",
         "limit": 0.5, "fill_qty": 10, "vwap": 0.5, "fees": 0.1, "first_fill": T0, "p_pin": 0.55,
         "sub_bid": 0.49, "sub_ask": 0.5, "decided": T0 - 2, "created": T0 - 2, "eligible": T0 - 1,
         "marks": [{"lag": L, "bid": 0.48, "ask": 0.49, "actual_lag": 61.0} for L in (0.1, 0.5, 1, 5, 60)]}
    t = EX.segment_table(EX.order_segments([o]))
    row = next(iter(t.values()))
    for lag in ("0.1s", "0.5s", "1s", "5s"):
        assert row["adverse_selection_after_fill"][lag]["status"] == "UNMEASURED"
    assert row["adverse_selection_after_fill"]["60s"]["n"] == 1


# ── structural arb ────────────────────────────────────────────────────────
def _leg(cid, line, bid, ask, state="MAPPED_BUT_SETTLEMENT_NOT_PROVEN"):
    return {"contract_id": cid, "venue": "POLYMARKET_US", "family": "POINTS",
            "meaning": {"event_id": "nfl-a-b-2026-10-08", "operator": "TOTAL", "line": line,
                        "metric": "POINTS", "period": "FULL_EVENT", "subject_type": "TEAM",
                        "subject_id": None, "competition": "a", "raw_market_type": "tt"},
            "sides": {"LONG": {"side_norm": "over"}, "independent_short_book": False},
            "rules_sha": "r", "settlement_state": state, "bid": bid, "ask": ask,
            "bid_q": 100, "ask_q": 100}


def test_same_book_complement_never_locks():
    r = ARB.complement_check(_leg("c", 28.5, 0.50, 0.52), "2026-10-07T00:00:00Z")
    assert r["executable"] is False and r["total_cost"] > 1.0


def test_implication_violation_is_found_but_not_eligible_without_proof():
    legs = [_leg("weak", 27.5, 0.40, 0.42), _leg("strong", 28.5, 0.60, 0.62)]
    v = ARB.implication_checks(legs, "2026-10-07T00:00:00Z")
    assert len(v) == 1 and v[0]["candidate"] is True
    assert v[0]["eligible"] is False and v[0]["ineligible_reason"] == "SETTLEMENT_IDENTITY_NOT_PROVEN"
    proven = [_leg("weak", 27.5, 0.40, 0.42, ARB.PROVEN), _leg("strong", 28.5, 0.60, 0.62, ARB.PROVEN)]
    assert ARB.implication_checks(proven, "2026-10-07T00:00:00Z")[0]["eligible"] is True


def test_proven_different_is_never_treated_as_proven():
    assert ARB.leg_proven({"settlement_state": "SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED"}) is False


def test_arb_receipt_without_proof_reports_no_eligible_arb():
    rows = [{"event_id": "nfl-a-b-2026-10-08", "bucket": T0,
             "legs": [_leg("weak", 27.5, 0.40, 0.42), _leg("strong", 28.5, 0.60, 0.62)]}]
    out = ARB.run(rows, {"KALSHI": 5, "POLYMARKET_US": 9})
    assert out["eligible_locked_baskets"] == 0
    assert out["result"] == "NO_ELIGIBLE_STRUCTURAL_ARB"
    assert out["cross_venue"]["status"] == "UNMEASURED"


# ── digital twin ──────────────────────────────────────────────────────────
def _twin_order(**kw):
    o = {"order_id": "o1", "group_id": "g", "role": "ENTRY", "type": "MARKETABLE", "tif": "IOC",
         "direction": "BUY", "side": "LONG", "slug": "aec-mlb-a-b-2026-10-01", "strategy": "S",
         "state": "FILLED", "qty": 10, "limit": 0.52, "decided": T0, "created": T0,
         "eligible": T0 + 2, "expires": T0 + 90, "fills": [{"at": T0 + 2, "qty": 10, "price": 0.51}],
         "books": [{"at": T0 - 30, "bids": [{"px": 0.49, "qty": 50}], "asks": [{"px": 0.51, "qty": 50}]}]}
    o.update(kw)
    return o


def test_twin_ioc_fills_at_the_touch_after_submit_latency():
    r = TW.replay_order(_twin_order(), cancel_latency_s=1.0)
    assert r["twin_qty"] == 10 and r["twin_vwap"] == pytest.approx(0.51)
    assert r["twin_first_fill"] == pytest.approx(T0 + 2)
    assert r["no_lookahead_violations"] == 0


def test_twin_never_lets_a_decision_see_a_later_book():
    # the only marketable book arrives AFTER the IOC was cancelled
    o = _twin_order(books=[{"at": T0 - 30, "bids": [{"px": 0.55, "qty": 5}], "asks": [{"px": 0.60, "qty": 5}]},
                           {"at": T0 + 30, "bids": [{"px": 0.49, "qty": 5}], "asks": [{"px": 0.50, "qty": 5}]}])
    r = TW.replay_order(o, cancel_latency_s=1.0)
    assert r["twin_qty"] == 0 and r["books_before_decision"] == 1


def test_twin_gtd_order_rests_until_a_crossing_book_then_cancels_at_expiry():
    o = _twin_order(tif="GTD", books=[
        {"at": T0 - 30, "bids": [{"px": 0.49, "qty": 5}], "asks": [{"px": 0.60, "qty": 5}]},
        {"at": T0 + 40, "bids": [{"px": 0.49, "qty": 5}], "asks": [{"px": 0.52, "qty": 5}]}])
    r = TW.replay_order(o, cancel_latency_s=1.0)
    assert r["twin_qty"] == 10 and r["twin_first_fill"] == pytest.approx(T0 + 40)
    assert r["touch_smaller_than_order"] is True


def test_short_side_twin_uses_the_complement_book():
    bid, ask = TW.oriented_top({"bids": [{"px": 0.33, "qty": 7}], "asks": [{"px": 0.335, "qty": 9}]}, "SHORT")
    assert bid == pytest.approx((0.665, 9)) and ask == pytest.approx((0.67, 7))


# ── positions / governor / attribution ───────────────────────────────────
def _orders_for_position(gid, slug, qty=100, px=0.5, payout=1.0, sell=None, strategy="S", p=0.55):
    rows = [{"order_id": gid + "e", "group_id": gid, "role": "ENTRY", "type": "MARKETABLE",
             "direction": "BUY", "side": "LONG", "slug": slug, "strategy": strategy, "state": "FILLED",
             "fill_qty": qty, "vwap": px, "fees": 1.0, "limit": px, "p_pin": p, "decision_id": gid,
             "sub_bid": px - 0.01, "sub_ask": px, "first_fill": T0, "decided": T0 - 1,
             "settle_payout": payout, "settle_outcome": "WON" if payout else "LOST", "settled": T0 + 9000}]
    if sell:
        q, spx = sell
        rows.append({"order_id": gid + "s", "group_id": gid, "role": "STANDING_PROTECTION",
                     "type": "LIMIT", "direction": "SELL", "side": "LONG", "slug": slug, "strategy": strategy,
                     "state": "FILLED", "fill_qty": q, "vwap": spx, "fees": 0.5})
    return rows


def test_position_pnl_is_cash_including_partial_sells_and_settlement():
    ps = build_positions(_orders_for_position("g1", "aec-mlb-a-b-2026-10-01", sell=(40, 0.6), payout=1.0))
    p = ps[0]
    assert p["status"] == "SETTLED" and p["held_at_settlement"] == 60
    assert p["realized_pnl"] == pytest.approx(-50 - 1 + 24 - 0.5 + 60)


def test_open_position_is_excluded_not_guessed():
    ps = build_positions(_orders_for_position("g2", "aec-mlb-a-b-2026-10-01", payout=None))
    assert ps[0]["status"] == "OPEN"


def test_attribution_reconciles_to_identity_and_to_cash_ledger():
    orders = (_orders_for_position("g1", "aec-mlb-a-b-2026-10-01", sell=(40, 0.6), payout=1.0)
              + _orders_for_position("g2", "aec-mlb-c-d-2026-10-01", payout=0.0)
              + _orders_for_position("g3", "aec-mlb-e-f-2026-10-01", sell=(100, 0.45), payout=None))
    out = ATT.run(build_positions(orders))
    assert out["decision_rows"]["attributed"] == 2
    assert out["decision_rows"]["not_attributed"] == {"SOLD_OUT_BEFORE_ANY_RECORDED_OUTCOME": 1}
    assert out["max_row_reconciliation_error"] < 1e-9
    assert out["max_row_cash_ledger_difference_usd"] < 1e-9
    assert out["by_strategy_sport_family_regime_venue"][0]["cell"].count("|") == 4


def test_governor_unit_is_the_event_not_the_decision_row():
    orders = []
    for k in range(10):          # ten positions on ONE game
        orders += _orders_for_position("g%d" % k, "aec-mlb-a-b-2026-10-01-x%d" % k)
    out = GOV.run(build_positions(orders), decision_rows_by_strategy={"S": 10},
                  execution_ok_by_strategy={}, settlement_evidence={})
    s = out["strategies"]["S"]
    assert s["settled_positions"] == 10 and s["unique_independent_events"] == 1
    assert s["capital_status"] == "SHADOW_ONLY"
    assert s["capital_reason"] == "INSUFFICIENT_INDEPENDENT_FORWARD_EVENTS"
    assert out["capital_status"] == "CASH"


def test_governor_never_grants_capital_without_every_proof_even_when_winning():
    orders = []
    for k in range(150):         # 150 distinct winning games
        orders += _orders_for_position("g%d" % k, "aec-mlb-t%03d-u-2026-10-01" % k, payout=1.0, p=0.99)
    out = GOV.run(build_positions(orders), decision_rows_by_strategy={"S": 150},
                  execution_ok_by_strategy={"S": {"ok": False, "reason": "X"}},
                  settlement_evidence={"all_traded_contracts_proven_compatible": False})
    s = out["strategies"]["S"]
    assert s["evidence_state"]["status"] == "POSITIVE_LOWER_BOUND"
    assert s["capital_status"] == "SHADOW_ONLY" and s["capital_reason"] == "SETTLEMENT_IDENTITY_NOT_PROVEN"
    assert "ACTIVE_CHAMPION" not in json.dumps(out)
    assert out["allocation_fraction"] == {"CASH": 1.0} and out["capital_status"] == "CASH"


# ── authority ─────────────────────────────────────────────────────────────
def test_adapters_have_no_order_db_network_or_capital_path():
    for p in AD.glob("*.py"):
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
        src = p.read_text()
        for bad in ("submit_order", "request_cancel", "bettor_funded", "live_executor", "place_order"):
            assert bad not in src, (p.name, bad)
