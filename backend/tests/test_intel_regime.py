"""CAPITAL-CRITICAL (SHADOW): REGIME DETECTION RECOMMENDS, WITH REASONS,
AND HAS NO AUTHORITY.

  §1 each signal: OK within baseline, DEGRADED / SEVERE past its declared
     threshold, UNMEASURED (with a reason) when a window is too thin
  §2 the recommendation: SEVERE -> NO_TRADE, DEGRADED -> REDUCE, measured
     and clean -> NORMAL, nothing measurable -> REDUCE; never applied
  §3 the read over synthetic rows runs end to end, and the regime table
     refuses an applied row or a recommendation outside the three
"""
from __future__ import annotations

import time

import asyncpg
import pytest

from sportsassets.intel import regime as RG

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
NOW = 2_000_000_000.0
RECENT = NOW - 3600            # inside the recent window
BASE = NOW - 2 * 86400         # inside the baseline window


def _obs(at, bid, offer, qty=100):
    return {"at": at, "bids": [{"px": {"value": "%.2f" % bid},
                                "qty": str(qty)}],
            "offers": [{"px": {"value": "%.2f" % offer}, "qty": str(qty)}]}


def _books(recent_spread, recent_qty=100, n=30):
    obs = [_obs(BASE + i, 0.50, 0.52) for i in range(n)]
    obs += [_obs(RECENT + i, 0.50, 0.50 + recent_spread, recent_qty)
            for i in range(n)]
    return {s["signal"]: s for s in RG.book_signals(obs, now=NOW)}


# ── §1 signals ───────────────────────────────────────────────────────

def test_spread_and_liquidity_thresholds():
    ok = _books(0.02)
    assert ok["spread"]["status"] == "OK" and ok["spread"]["ratio"] == 1.0
    assert _books(0.03)["spread"]["status"] == "DEGRADED"     # 1.5x
    assert _books(0.06)["spread"]["status"] == "SEVERE"       # 3x
    thin = _books(0.02, recent_qty=20)
    assert thin["liquidity"]["status"] == "SEVERE"           # 0.2x depth
    assert thin["liquidity"]["reasons"][0].startswith("LIQUIDITY_DEPTH")
    assert _books(0.02, recent_qty=40)["liquidity"]["status"] == "DEGRADED"


def test_a_thin_window_is_unmeasured_not_ok():
    s = _books(0.02, n=5)["spread"]
    assert s["status"] == "UNMEASURED"
    assert s["unmeasured"]["status"].startswith("TOO_FEW_OBSERVATIONS")


def _vals(base_step, recent_step, n=40, refusal_recent=None):
    out = []
    for i in range(n):
        out.append({"key": "k", "at": BASE + i * 60,
                    "p": 0.5 + (base_step if i % 2 else 0), "age": 5.0,
                    "refusals": ["VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"]})
        out.append({"key": "k2", "at": RECENT + i * 60,
                    "p": 0.5 + (recent_step if i % 2 else 0), "age": 5.0,
                    "refusals": [refusal_recent or
                                 "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED"]})
    return out


def test_pinnapi_volatility():
    assert RG.volatility_signal(_vals(0.01, 0.01), now=NOW)["status"] == "OK"
    assert RG.volatility_signal(_vals(0.01, 0.025),
                                now=NOW)["status"] == "DEGRADED"
    assert RG.volatility_signal(_vals(0.01, 0.04),
                                now=NOW)["status"] == "SEVERE"
    assert RG.volatility_signal([], now=NOW)["status"] == "UNMEASURED"


def test_feed_latency_and_gaps_from_the_heartbeat():
    hb = {"beat_at": NOW - 10,
          "cache": {"provider_stamp_to_receipt_ms": {"n": 10, "p95": 800}}}
    lat, gap = RG.feed_signals(hb, [], now=NOW)
    assert lat["status"] == "OK"
    assert lat["provider_to_receipt_p95_ms"] == 800
    assert gap["status"] == "OK" and gap["heartbeat_age_s"] == 10
    hb2 = {"beat_at": NOW - 900,
           "cache": {"provider_stamp_to_receipt_ms": {"p95": 20000}}}
    lat, gap = RG.feed_signals(hb2, [], now=NOW)
    assert lat["status"] == "SEVERE" and gap["status"] == "SEVERE"
    lat, gap = RG.feed_signals(None, [], now=NOW)
    assert lat["status"] == gap["status"] == "UNMEASURED"
    assert gap["heartbeat_age_s"] is None
    assert gap["unmeasured"]["heartbeat_age_s"] == "NO_FEED_HEARTBEAT_ROW"


def test_a_silence_in_valuations_is_a_gap():
    base = [{"at": BASE + i * 60, "age": 5.0} for i in range(100)]
    recent = [{"at": NOW - RG.RECENT_S + 60, "age": 5.0}]
    _, gap = RG.feed_signals(None, base + recent, now=NOW)
    assert gap["status"] == "SEVERE"
    assert gap["silence_ratio"] > 10


def test_mapping_refusal_mix_shift():
    assert RG.mapping_signal(_vals(0, 0), now=NOW)["status"] == "OK"
    s = RG.mapping_signal(_vals(0, 0, refusal_recent="CONTRACT_MAPPING_"
                                                     "AMBIGUOUS"), now=NOW)
    assert s["status"] == "SEVERE"
    assert s["mapping_share_recent"] == 1.0
    assert s["total_variation_distance"] == pytest.approx(1.0)


def test_calibration_drift_reads_the_engine():
    rep = {"drift": {"DECISION_P_PINNACLE": {
        "brier_change": 0.07, "recent_n": 40, "baseline_n": 80,
        "recent_brier": 0.27, "baseline_brier": 0.20}}}
    assert RG.calibration_signal(rep)["status"] == "SEVERE"
    rep["drift"]["DECISION_P_PINNACLE"]["recent_n"] = 10
    assert RG.calibration_signal(rep)["status"] == "UNMEASURED"
    assert RG.calibration_signal(None)["status"] == "UNMEASURED"


def test_execution_fill_rate_and_slippage():
    orders = ([{"at": BASE + i, "filled": True} for i in range(30)]
              + [{"at": RECENT + i, "filled": i % 3 == 0}
                 for i in range(30)])
    s = RG.execution_signal(orders, [], now=NOW)
    assert s["status"] == "SEVERE"          # 1.0 -> 0.33
    assert s["fill_rate_recent"] == pytest.approx(10 / 30, abs=1e-6)
    assert s["slippage_rise_pc"] is None
    attrs = ([{"book": "PAPER", "decided_at": BASE + i, "slippage_pc": 0.0}
              for i in range(25)]
             + [{"book": "PAPER", "decided_at": RECENT + i,
                 "slippage_pc": 0.015} for i in range(25)])
    s = RG.execution_signal([], attrs, now=NOW)
    assert s["status"] == "DEGRADED"
    assert s["slippage_rise_pc"] == pytest.approx(0.015)


# ── §2 the recommendation ────────────────────────────────────────────

def _s(status, name="x"):
    return {"signal": name, "status": status,
            "reasons": [] if status in ("OK", "UNMEASURED") else [status]}


def test_the_recommendation_ladder_and_no_authority():
    assert RG.recommend([_s("OK"), _s("SEVERE")])["recommendation"] == (
        "NO_TRADE")
    assert RG.recommend([_s("OK"), _s("DEGRADED")])["recommendation"] == (
        "REDUCE")
    r = RG.recommend([_s("OK"), _s("UNMEASURED")])
    assert r["recommendation"] == "NORMAL"
    none = RG.recommend([_s("UNMEASURED")])
    assert none["recommendation"] == "REDUCE"
    assert "NO_REGIME_SIGNAL_MEASURABLE" in none["reasons"][0]
    for x in (r, none):
        assert x["applied"] is False
        assert x["autonomous_limit_changes"] is False
        assert x["authority"] == "SHADOW_NO_AUTHORITY"


# ── §3 the read ──────────────────────────────────────────────────────

@pg
async def test_the_read_detects_and_the_table_refuses_authority():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        prefix = F.uid("intel-rg-")
        for i in range(25):
            await F.book(conn, prefix + "a", now - 2 * 86400 + i,
                         bids=((0.50, 100),), offers=((0.52, 100),))
            await F.book(conn, prefix + "a", now - 600 + i,
                         bids=((0.45, 100),), offers=((0.55, 100),))
        rep = await RG.load_and_detect(
            conn, now=now, account_id="paper_test_none",
            experiment_id=F.uid("NOEXP_"), slug_prefix=prefix)
        sig = {s["signal"]: s for s in rep["signals"]}
        assert sig["spread"]["status"] == "SEVERE"      # 0.02 -> 0.10
        assert rep["recommendation"] == "NO_TRADE"
        assert rep["applied"] is False and rep["label"] == "SHADOW"
        assert sig["mapping"]["status"] == "UNMEASURED"
        for sql in ("INSERT INTO intel_regime_states (run_id, computed_at, "
                    " recommendation, reasons, signals, applied) VALUES "
                    " ('r1', now(), 'NORMAL', '[]', '[]', true)",
                    "INSERT INTO intel_regime_states (run_id, computed_at, "
                    " recommendation, reasons, signals) VALUES "
                    " ('r2', now(), 'HALVE_LIMITS', '[]', '[]')"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(sql)
            await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()
