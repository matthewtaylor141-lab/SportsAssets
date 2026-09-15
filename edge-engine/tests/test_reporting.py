"""The methodology document must be a function of the ledger, not of prose.

The previous document was hand-maintained and drifted: three different
sample counts for one cohort, a surcharge 0.25c stale, a mode line that
outlived the config change. These tests pin the properties that stop that
happening again — numbers come from the data, absent numbers say so, and the
document cannot claim a result the sample does not support.
"""

import json
import time

import pytest

from edge.execution.engine import Policy
from edge.ledger.service import Ledger
from edge.reporting.export import write_bundle, write_positions_csv
from edge.reporting.figures import (MIN_SETTLED_FOR_RETURN, compute_figures,
                                    fig, verdict)
from edge.reporting.methodology import num, render

POLICY = Policy.load()


def _settled(led, n, *, pnl, stake=1.0, ts=None, category="prop", wins=None):
    """n settled markets, each staking `stake`.

    `wins` gives a realistic mix; without it every market resolves the same
    way, which is a legitimate input but has ZERO dispersion — and a sample
    with no dispersion has no standard error and no sigma. Tests that want a
    distance-from-zero figure have to supply variation.
    """
    now = ts or time.time()
    for i in range(n):
        mk = f"polymarket-us:m{i}-{now}"
        led.record_fill(fill_uid=f"f{i}-{now}", venue="polymarket-us",
                        market_key=mk, side="BUY", qty=stake / 0.5, price=0.5,
                        ts=now, mode="LIVE_BETA", league="epl",
                        category=category,
                        decision={"band": "0.45-0.50", "tier": "core",
                                  "edge": 0.03, "threshold": 0.02})
        won = (i < wins) if wins is not None else (pnl > 0)
        led.record_resolution(mk, payout=1.0 if won else 0.0, ts=now)


# ── figures ─────────────────────────────────────────────────────────────

def test_a_figure_nulls_itself_rather_than_report_a_thin_sample():
    assert fig(0.5, n=3, min_n=30)["value"] is None
    assert "n_below_threshold" in fig(0.5, n=3, min_n=30)["null_reason"]
    assert fig(0.5, n=99, min_n=30)["value"] == 0.5


def test_every_figure_carries_its_own_sample_count(tmp_path):
    """Integrity rule: a figure without an n is not a figure."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    f = compute_figures(led, POLICY, days=7)
    for key, item in f["headline"].items():
        assert "n" in item, f"{key} has no sample count"
        assert "source" in item and item["source"], f"{key} has no source"


def test_a_null_figure_always_says_why(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    f = compute_figures(led, POLICY, days=7)

    def walk(node):
        if isinstance(node, dict):
            if "value" in node and "unit" in node:
                if node["value"] is None:
                    assert node.get("null_reason"), f"silent null: {node}"
                return
            for v in node.values():
                walk(v)

    walk(f)


def test_the_return_is_not_quoted_on_a_thin_sample(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    _settled(led, 3, pnl=1.0)
    f = compute_figures(led, POLICY, days=7)
    assert f["headline"]["n_settled"]["value"] == 3
    assert f["headline"]["return_pct"]["value"] is None
    assert "NO EVIDENCE EITHER WAY" in verdict(f)


def test_a_real_sample_does_get_quoted(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    n = MIN_SETTLED_FOR_RETURN + 10
    _settled(led, n, pnl=1.0, wins=n // 2)
    f = compute_figures(led, POLICY, days=7)
    assert f["headline"]["return_pct"]["value"] is not None
    assert f["headline"]["sigma_from_zero"]["value"] is not None
    assert f["headline"]["sd_per_trade"]["value"] > 0


def test_a_sample_with_no_dispersion_has_no_sigma(tmp_path):
    """Every trade returning identically is not evidence of certainty — it
    is a sample with no standard error, and a sigma computed from it would
    be infinite. It nulls instead."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    _settled(led, MIN_SETTLED_FOR_RETURN + 5, pnl=1.0)      # all winners
    f = compute_figures(led, POLICY, days=7)
    assert f["headline"]["return_pct"]["value"] is not None
    assert f["headline"]["sigma_from_zero"]["value"] is None
    assert f["headline"]["sigma_from_zero"]["null_reason"]


def test_the_verdict_is_symmetric_between_winning_and_losing(tmp_path):
    """The rule that refuses to call a losing stretch a disproof must refuse
    to call a winning one an edge. A one-sided threshold is how a system
    talks itself into staying live."""
    win = Ledger(db_path=str(tmp_path / "w.sqlite3"))
    lose = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    _settled(win, 5, pnl=1.0)
    _settled(lose, 5, pnl=-1.0)
    assert verdict(compute_figures(win, POLICY)).split(".")[0] == \
           verdict(compute_figures(lose, POLICY)).split(".")[0]


def test_an_absolute_window_excludes_everything_before_it(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    _settled(led, 5, pnl=1.0, ts=now - 10 * 86_400)     # well before
    _settled(led, 4, pnl=1.0, ts=now - 3600)            # inside
    cut = now - 86_400
    assert compute_figures(led, POLICY, since=cut)["headline"][
        "n_settled"]["value"] == 4
    # ...and the rolling window, asked for a week, sees only the recent ones
    # too — the point is that `since` is not merely another way to say days.
    assert compute_figures(led, POLICY, days=7)["headline"][
        "n_settled"]["value"] == 4
    assert compute_figures(led, POLICY, days=30)["headline"][
        "n_settled"]["value"] == 9


def test_daily_series_buckets_on_the_engines_own_settlement_clock(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    _settled(led, 2, pnl=1.0, ts=now)
    _settled(led, 3, pnl=-1.0, ts=now - 2 * 86_400)
    days = led.performance_daily(days=30)
    assert len(days) == 2
    assert days[0]["day"] > days[1]["day"]            # newest first
    assert {d["settled"] for d in days} == {2, 3}


# ── the document ────────────────────────────────────────────────────────

def test_an_unmeasured_number_reads_as_unmeasured_not_as_zero():
    """The single most dangerous rendering bug available to this document."""
    assert "not measured" in num({"value": None, "n": 4})
    assert "0" not in num({"value": None, "n": None})


def test_the_document_renders_from_an_empty_ledger(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    doc = render(led, POLICY, days=7)
    assert "# How the edge is made" in doc
    assert "not measured" in doc
    assert "NO EVIDENCE EITHER WAY" in doc


def test_the_document_quotes_the_config_it_is_generated_against(tmp_path):
    """A settings figure in the prose must come from config, or it will
    outlive the config change that invalidated it — which is exactly how the
    old document ended up advertising a mode the engine had left."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    doc = render(led, POLICY, days=7)
    assert f"mode:                     {POLICY.risk['mode']}" in doc
    for league in POLICY.leagues.get("blocklist") or []:
        assert league in doc
    assert str(POLICY.leagues.get("unknown_league_policy")) in doc


def test_the_document_reports_the_measured_record(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    n = MIN_SETTLED_FOR_RETURN + 5
    _settled(led, n, pnl=1.0, wins=n // 2)
    doc = render(led, POLICY, days=7)
    assert f"{n}" in doc
    assert "σ" in doc


# ── the export ──────────────────────────────────────────────────────────

def test_the_position_export_carries_one_row_per_position(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    _settled(led, 4, pnl=1.0)
    n = write_positions_csv(led, tmp_path / "p.csv", days=7)
    assert n == 4
    lines = (tmp_path / "p.csv").read_text().strip().splitlines()
    assert len(lines) == 5                          # header + 4
    assert lines[0].startswith("entry_ts_utc,settle_ts_utc")


def test_the_export_refuses_a_row_outside_its_own_window(tmp_path):
    """The invariant is asserted in code, not checked by eye in a CSV."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    _settled(led, 2, pnl=1.0, ts=now - 10 * 86_400)
    _settled(led, 2, pnl=1.0, ts=now)
    n = write_positions_csv(led, tmp_path / "p.csv", since=now - 86_400)
    assert n == 2


def test_the_bundle_writes_all_three_artifacts(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    _settled(led, 6, pnl=1.0)
    res = write_bundle(led, POLICY, tmp_path / "out", days=7)
    assert res["positions"] == 6 and res["settled"] == 6
    for name in ("figures.json", "positions.csv", "METHODOLOGY.md"):
        assert (tmp_path / "out" / name).exists(), name
    figures = json.loads((tmp_path / "out" / "figures.json").read_text())
    assert figures["headline"]["n_settled"]["value"] == 6


def test_the_document_and_the_json_cannot_disagree(tmp_path):
    """Both render from ONE compute_figures() call. If they ever diverge it
    is because someone added a second source of the same number."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    n = MIN_SETTLED_FOR_RETURN + 1
    _settled(led, n, pnl=1.0, wins=n // 2)
    write_bundle(led, POLICY, tmp_path / "out", days=7)
    figures = json.loads((tmp_path / "out" / "figures.json").read_text())
    doc = (tmp_path / "out" / "METHODOLOGY.md").read_text()
    assert f"{figures['headline']['n_settled']['value']:,}" in doc
    roi = figures["headline"]["return_pct"]["value"]
    assert f"{roi:+.2%}" in doc


@pytest.mark.parametrize("cmd", ["figures", "methodology"])
def test_the_cli_commands_run(tmp_path, monkeypatch, capsys, cmd):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EDGE_LEDGER_DB", str(tmp_path / "l.sqlite3"))
    from edge.cli import _cmd_reporting

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    _cmd_reporting(cmd, led, POLICY, ["--days", "3"])
    assert capsys.readouterr().out.strip()


# ── entry margin: profit per turnover, measured at entry ────────────────

def _margin_fill(led, uid, *, price, edge, category, mode="LIVE_BETA"):
    """A fill carrying its book snapshot: mid sits half a spread below the
    ask we paid, so paid_over_mid is (ask - mid)."""
    led.record_fill(
        fill_uid=uid, venue="polymarket-us", market_key=f"polymarket-us:{uid}",
        side="BUY", qty=1.0 / price, price=price, ts=time.time(), mode=mode,
        category=category,
        decision={"edge": edge, "book_asks": [(price, 500)],
                  "book_bids": [(price - 0.02, 500)]})


def test_entry_margin_is_gross_only_until_retention_is_measured(tmp_path):
    """An unmeasured retention must never silently become 1.0 — the gross
    figure is reported alone and the net one says why it is absent."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    for i in range(5):
        _margin_fill(led, f"f{i}", price=0.30, edge=0.03, category="prop")
    m = led.entry_margin(days=7)
    assert m["prop"]["n_fills"] == 5
    # edge 3c minus paid-over-mid 1c, over a 30c price: 6.7% gross.
    assert m["prop"]["gross_margin"] == pytest.approx(0.0667, abs=2e-3)
    assert m["prop"]["net_margin"] is None
    assert m["prop"]["retention"] is None


def test_entry_margin_discounts_the_claim_by_measured_retention(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    for i in range(led.DRIFT_MIN_N + 3):
        uid = f"f{i}"
        _margin_fill(led, uid, price=0.30, edge=0.03, category="prop")
        # Fair at entry 0.33; a minute later 0.32 — a third of the claimed
        # edge gone. Retention ~0.667 measured on every fill's market.
        led.record_entry_fair(f"polymarket-us:{uid}", 0.30, 0.33,
                              category="prop")
        led.record_drift_later(f"polymarket-us:{uid}", 0.32)
    m = led.entry_margin(days=7)
    assert m["prop"]["retention"] == pytest.approx(0.667, abs=0.01)
    # (3c x 0.667 - 1c) / 30c = 3.3%: still paying, but half the gross —
    # exactly the discount adverse selection should charge.
    assert m["prop"]["net_margin"] == pytest.approx(0.033, abs=3e-3)
    assert m["prop"]["net_margin"] < m["prop"]["gross_margin"]


def test_entry_margin_reaches_the_figures_object(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    for i in range(4):
        _margin_fill(led, f"f{i}", price=0.30, edge=0.03, category="prop")
    f = compute_figures(led, POLICY, days=7)
    em = f["entry_margin"]["prop"]
    assert em["gross_margin"]["value"] is not None
    assert em["net_margin"]["value"] is None
    assert "retention_unmeasured" in em["net_margin"]["null_reason"]


# ── promotion readiness: a bigger ticket must be EARNED ─────────────────

def test_a_category_is_not_ready_on_a_hot_streak(tmp_path):
    """35 settled at a good ROI is a week, not a record. Readiness needs
    sigma AND sample size AND a positive record, and even then it only
    reports — acting on it is a reviewed config edit."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    _settled(led, 35, pnl=1.0, wins=25)
    f = compute_figures(led, POLICY, days=7)
    ready = f["promotion_readiness"]["prop"]
    assert ready["ready"] is False
    assert ready["settled"] == 35


def test_a_category_with_a_real_record_reads_ready(tmp_path):
    from edge.reporting.figures import PROMOTION_MIN_N

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    n = PROMOTION_MIN_N + 50
    # 60% winners at even prices: strongly positive, well-powered.
    _settled(led, n, pnl=1.0, wins=int(n * 0.6))
    f = compute_figures(led, POLICY, days=7)
    ready = f["promotion_readiness"]["prop"]
    assert ready["sigma"] is not None and ready["sigma"] >= 2.0
    assert ready["ready"] is True


def test_readiness_never_reads_ready_on_a_negative_record(tmp_path):
    from edge.reporting.figures import PROMOTION_MIN_N

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    n = PROMOTION_MIN_N + 50
    _settled(led, n, pnl=-1.0, wins=int(n * 0.4))
    f = compute_figures(led, POLICY, days=7)
    ready = f["promotion_readiness"]["prop"]
    # A LOSING record can be many sigma from zero — that is evidence to
    # close the category, never to promote it.
    assert ready["ready"] is False
