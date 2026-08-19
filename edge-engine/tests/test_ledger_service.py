"""Build step 1 gate: replay 1,000 fills + resolutions through the SQLite
ledger service and tie out to independently hand-computed PnL to the cent.

The expected numbers are derived with exact Fraction arithmetic implementing
the written methodology directly (re-average on buy, realize on sell at sale
price vs avg, realize remainder at payout) — a second, independent
implementation, not a call into the engine. The fixture is deterministic and
covers: mixed buy prices, partial sells, sells at exactly avg cost (zero
realization), oversell clamping, zero-remainder resolutions, winning and
losing settlements, Kalshi taker fees, and full-journal idempotent replay.
"""

from fractions import Fraction as F

import pytest

from edge.ledger.service import Ledger

N_MARKETS = 250  # 4 fills each -> exactly 1,000 fills


def cents(x: F) -> float:
    """Round an exact Fraction to cents (the tie-out precision)."""
    return float(round(x, 2))


def market_fixture(i: int) -> dict:
    """Deterministic per-market script: BUY, BUY, SELL, SELL, then resolution."""
    q1 = 10 + (i % 37)
    q2 = 5 + (i % 23)
    p1 = F(5 + (i * 7) % 90, 100)
    p2 = F(5 + (i * 11) % 90, 100)
    ps1 = F(10 + (i * 13) % 80, 100)
    ps2 = F(10 + (i * 17) % 80, 100)
    total = q1 + q2
    s1 = total // 4                      # partial sell (0 when total < 4 never happens)
    s2 = (total - s1) + 5 if i % 10 == 9 else total // 5   # oversell branch clamps
    payout = 1 if i % 3 == 0 else 0
    fee_rate = F(7, 100) if i % 5 == 0 else F(0)           # "kalshi" taker markets
    return {"q1": q1, "q2": q2, "p1": p1, "p2": p2, "ps1": ps1, "ps2": ps2,
            "s1": s1, "s2": s2, "payout": payout, "fee_rate": fee_rate,
            "venue": "kalshi" if i % 5 == 0 else "polymarket-us",
            "league": ["epl", "nba", "lal", "wta", "fl1"][i % 5]}


def expected_market(m: dict) -> dict:
    """Independent exact-arithmetic implementation of the methodology."""
    q1, q2 = F(m["q1"]), F(m["q2"])
    avg = (q1 * m["p1"] + q2 * m["p2"]) / (q1 + q2)
    inventory = q1 + q2

    sold1 = min(F(m["s1"]), inventory)
    r1 = sold1 * (m["ps1"] - avg)
    inventory -= sold1

    sold2 = min(F(m["s2"]), inventory)
    r2 = sold2 * (m["ps2"] - avg)
    inventory -= sold2

    res = inventory * (F(m["payout"]) - avg)

    fee = m["fee_rate"] * (q1 * m["p1"] * (1 - m["p1"]) + q2 * m["p2"] * (1 - m["p2"]))
    staked = q1 * m["p1"] + q2 * m["p2"]
    return {"gross": r1 + r2 + res, "fee": fee, "staked": staked,
            "remainder": inventory}


def replay(ledger: Ledger) -> None:
    base_ts = 1_750_000_000
    for i in range(N_MARKETS):
        m = market_fixture(i)
        key = f"mkt-{i}"
        ts = base_ts + i * 3600
        fee1 = float(m["fee_rate"] * m["q1"] * m["p1"] * (1 - m["p1"]))
        fee2 = float(m["fee_rate"] * m["q2"] * m["p2"] * (1 - m["p2"]))
        ledger.record_fill(f"{key}-b1", m["venue"], key, "BUY", m["q1"],
                           float(m["p1"]), ts=ts, fee=fee1, league=m["league"])
        ledger.record_fill(f"{key}-b2", m["venue"], key, "BUY", m["q2"],
                           float(m["p2"]), ts=ts + 60, fee=fee2, league=m["league"])
        ledger.record_fill(f"{key}-s1", m["venue"], key, "SELL", m["s1"],
                           float(m["ps1"]), ts=ts + 120, league=m["league"])
        ledger.record_fill(f"{key}-s2", m["venue"], key, "SELL", m["s2"],
                           float(m["ps2"]), ts=ts + 180, league=m["league"])
        ledger.record_resolution(key, float(m["payout"]), ts=ts + 86_400)


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(db_path=str(tmp_path / "ledger.sqlite3"))
    replay(led)
    return led


def test_thousand_fill_replay_ties_out_to_the_cent(ledger):
    total_expected = F(0)
    for i in range(N_MARKETS):
        m = market_fixture(i)
        exp = expected_market(m)
        total_expected += exp["gross"]
        pos = ledger.position(f"mkt-{i}")
        assert pos is not None and pos["resolved"] == 1
        assert pos["shares"] == pytest.approx(0.0, abs=1e-9)
        # per-market tie-out to the cent against exact arithmetic
        assert pos["realized_pnl"] == pytest.approx(cents(exp["gross"]), abs=0.005), \
            f"market {i} gross mismatch"
        assert pos["fees_paid"] == pytest.approx(cents(exp["fee"]), abs=0.005)
    s = ledger.summary()
    assert s["fills"] == 2 * N_MARKETS                # BUY fills
    assert s["gross_realized"] == pytest.approx(cents(total_expected), abs=0.01)


def test_fixture_is_exactly_one_thousand_fills(ledger):
    import sqlite3

    with sqlite3.connect(ledger.db_path) as conn:
        n = conn.execute("SELECT count(*) FROM fills").fetchone()[0]
    assert n == 1000


def test_summary_staked_and_net(ledger):
    exp_staked = sum((expected_market(market_fixture(i))["staked"]
                      for i in range(N_MARKETS)), F(0))
    exp_fees = sum((expected_market(market_fixture(i))["fee"]
                    for i in range(N_MARKETS)), F(0))
    s = ledger.summary()
    assert s["staked"] == pytest.approx(cents(exp_staked), abs=0.01)
    assert s["fees"] == pytest.approx(cents(exp_fees), abs=0.01)
    assert s["net_realized"] == pytest.approx(s["gross_realized"] - s["fees"], abs=1e-6)
    assert s["open_markets"] == 0 and s["resolved_markets"] == N_MARKETS


def test_full_journal_replay_is_idempotent(ledger):
    before = ledger.summary()
    replay(ledger)  # replay the entire 1,000-fill journal + resolutions again
    after = ledger.summary()
    assert after == before


def test_oversell_markets_clamp_and_settle_with_zero_remainder():
    # i % 10 == 9 markets oversell on the second sale: clamp, then the
    # resolution realizes exactly 0 on 0 remaining shares.
    m = market_fixture(9)
    exp = expected_market(m)
    assert exp["remainder"] == 0
    assert m["s1"] + m["s2"] > m["q1"] + m["q2"]


def test_daily_journal_sums_to_total_gross(ledger):
    days = ledger.daily_pnl()
    total = sum(d["pnl"] for d in days)
    assert total == pytest.approx(ledger.summary()["gross_realized"], abs=0.01)
    # 2 sells + 1 resolution per market, minus sells that clamped to nothing
    assert sum(d["events"] for d in days) >= 2 * N_MARKETS


def test_post_resolution_fills_are_ignored(tmp_path):
    led = Ledger(db_path=str(tmp_path / "x.sqlite3"))
    led.record_fill("f1", "kalshi", "m", "BUY", 100, 0.60, ts=1)
    led.record_resolution("m", 1.0, ts=2)
    r = led.record_fill("f2", "kalshi", "m", "BUY", 50, 0.10, ts=3)
    assert r["realized"] == 0.0
    pos = led.position("m")
    assert pos["realized_pnl"] == pytest.approx(100 * 0.40)
    assert pos["resolved"] == 1
    # second resolution is a no-op too
    assert led.record_resolution("m", 0.0, ts=4)["applied"] is False


def test_decision_record_round_trip(tmp_path):
    led = Ledger(db_path=str(tmp_path / "x.sqlite3"))
    decision = {"fair": 0.55, "best_ask": 0.51, "threshold": 0.02,
                "book": {"asks": [[0.51, 900]]}, "caps": {"per_fill": 10.0}}
    led.record_fill("f1", "kalshi", "m", "BUY", 10, 0.51, ts=1,
                    mode="PAPER", decision=decision)
    rec = led.decision_record("f1")
    assert rec["decision"] == decision
    assert rec["mode"] == "PAPER"


def test_sell_at_exact_avg_cost_is_a_zero_pnl_event(tmp_path):
    led = Ledger(db_path=str(tmp_path / "x.sqlite3"))
    led.record_fill("b", "kalshi", "m", "BUY", 100, 0.50, ts=1)
    r = led.record_fill("s", "kalshi", "m", "SELL", 40, 0.50, ts=2)
    assert r["realized"] == 0.0
    days = led.daily_pnl()
    assert sum(d["events"] for d in days) == 1  # journaled despite $0


def test_entry_cohort_splits_on_fill_time_not_settlement_time(tmp_path):
    """The clean-cohort verdict must exclude a position ENTERED before
    the cutoff even when it settles after it — settlement-windowed
    scorecards blend defect-era entries into the post-fix record."""
    led = Ledger(str(tmp_path / "l.db"))
    CUT = 1_000_000.0
    # Dirty: entered before the cutoff, settles after.
    led.record_fill("f-dirty", "kalshi", "kx:DIRTY", "BUY", 100, 0.40,
                    ts=CUT - 500, mode="LIVE_BETA", category="kalshi_fsc")
    led.record_resolution("kx:DIRTY", 1.0, ts=CUT + 5_000)
    # Clean: entered and settled after the cutoff.
    led.record_fill("f-clean", "kalshi", "kx:CLEAN", "BUY", 100, 0.50,
                    ts=CUT + 100, mode="LIVE_BETA", category="kalshi_fsc")
    led.record_resolution("kx:CLEAN", 0.0, ts=CUT + 6_000)
    # Different category, after cutoff: not this sleeve's evidence.
    led.record_fill("f-other", "kalshi", "kx:OTHER", "BUY", 10, 0.50,
                    ts=CUT + 100, mode="LIVE_BETA", category="copy")
    led.record_resolution("kx:OTHER", 1.0, ts=CUT + 6_000)

    out = led.category_entry_cohort("kalshi_fsc", CUT)
    assert out["settled"] == 1
    assert out["wins"] == 0 and out["losses"] == 1
    assert out["staked"] == 50.0
    assert out["realized"] == -50.0
    assert out["roi"] == -1.0
