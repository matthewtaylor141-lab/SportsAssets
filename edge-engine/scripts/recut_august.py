#!/usr/bin/env python3
"""Emit the August re-baseline artifacts.

Every measured figure in PHILOSOPHY.md is cumulative over the engine's full
history. This script recuts them against a window that starts 2026-08-01.

It currently emits `null` for every figure, and that is the honest output, not
a stub. Three independent blockers stop the computation, all documented with
line references in `out/RECUT_NOTES.md`:

  1. `edge_drift` is keyed by market_key, not fill_uid — fair-value-at-+60s is
     not persisted per fill, so drift/retention/surcharge/shrinkage cannot be
     computed at position level at all.
  2. FIXED. Every report in ledger/service.py took a ROLLING `days: int` with
     no absolute `since`, so "since 2026-08-01" was inexpressible and days=7
     asked on 2026-08-02 dragged five and a half pre-window days into a figure
     labelled in-window. All eight now accept `since` (see
     ledger.window_start). The figures remain null only because the store is
     still on the worker (blocker 3) — the arithmetic is no longer the
     obstacle.
  3. The store lives on the edge-shadow worker's mounted disk and is not
     reachable from a checkout. Only worker-computed aggregates surface, inside
     the /api/engine/status heartbeat.

When those are fixed, the `NULL_REASON` entries below are replaced by calls
into the EXISTING report functions — extended with a `since` parameter, never
reimplemented. A second implementation of these metrics that disagreed with the
engine's own would be worse than no recut.

Run: python scripts/recut_august.py
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "out"

# ── the window ──────────────────────────────────────────────────────────
# Ledger timestamps are zone-free Unix epoch seconds (`ts REAL`); Postgres
# uses timestamptz. Nothing is mixed, so the boundary is unambiguous.
WINDOW_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
# Last complete data instant: the engine heartbeat read by the
# engine-diagnostic workflow, run 30756459863.
WINDOW_END = datetime(2026, 8, 2, 16, 22, 7, tzinfo=timezone.utc)

# Why a given figure is null. Distinguishing these matters: "we measured zero"
# and "we could not measure" are the same number to a naive reader and must
# never be the same number to the engine.
NO_WINDOW = ("store_unreachable_pending_deploy: the `since` parameter now "
             "exists on all eight report functions (commit adding "
             "ledger.window_start), so this figure is computable — but only "
             "where the data lives, on the edge-shadow worker. It needs a "
             "deploy plus an export path before it can be read from here")
UNREACHABLE = ("store_unreachable: lives in SQLite on the edge-shadow Render "
               "worker at /var/edge-data/edge_ledger.sqlite3; no copy exists "
               "in a checkout")
NOT_PER_FILL = ("not_persisted_per_fill: edge_drift is keyed by market_key "
                "(service.py:148), so there is one drift observation per "
                "market regardless of how many times we entered it")
LOW_N = "n_below_threshold"
UNTRACED = "source_untraced: see RECUT_NOTES.md section 2, UNTRACED"


def fig(unit, source_file, source_function, null_reason, n=None, note=None):
    """A figure. `value` is null unless it is genuinely computed.

    Integrity rule 2: every figure carries its own n. Where n itself is
    unobtainable it is null and the reason says so — an absent n is not an
    n of zero.
    """
    d = {
        "value": None,
        "n": n,
        "unit": unit,
        "source_file": source_file,
        "source_function": source_function,
        "computed_at": WINDOW_END.isoformat().replace("+00:00", "Z"),
        "null_reason": null_reason,
    }
    if note:
        d["note"] = note
    return d


LEDGER = "src/edge/ledger/service.py"
CATEGORIES = ["moneyline", "draw", "spread", "total", "prop", "segment",
              "overall"]

figures: dict = {
    "_window": {
        "start_utc": WINDOW_START.isoformat().replace("+00:00", "Z"),
        "end_utc": WINDOW_END.isoformat().replace("+00:00", "Z"),
        "hours": round((WINDOW_END - WINDOW_START).total_seconds() / 3600, 2),
        "basis_note": (
            "Entry-basis and settlement-basis populations were both requested. "
            "Both are null: entry-basis is blocked by the rolling-window "
            "limitation, settlement-basis additionally by the missing "
            "resolution timestamp (RECUT_NOTES section 4.3). The >5% "
            "divergence flag could not be evaluated."),
        "source_probe": (
            "https://github.com/matthewtaylor141-lab/SportsAssets/actions/"
            "runs/30756459863"),
    },

    # ── headline record ─────────────────────────────────────────────────
    # n=3 in-window settlements (platform cohort) or 0 (engine ledger). Every
    # statistic below is null on sample size alone, before any other blocker.
    "headline": {
        "n_settled": fig("count", LEDGER, "performance", LOW_N + (
            ": the two stores disagree — platform cohort reports 3 settled, "
            "engine ledger reports 0 over the same period. Neither supports a "
            "statistic, and the disagreement is itself unresolved"), n=3),
        "wins": fig("count", LEDGER, "performance", LOW_N, n=3),
        "losses": fig("count", LEDGER, "performance", LOW_N, n=3),
        "staked": fig("usd", LEDGER, "performance", NO_WINDOW),
        "net": fig("usd", LEDGER, "performance", NO_WINDOW, note=(
            "Step 6 asked for two independent computations. Neither is "
            "possible: there are no in-window position rows to sum, and no "
            "ledger balance series is exposed. Three separate accounting "
            "paths disagree about today's stake — engine ledger $583.08, "
            "platform cohort $311.71, risk day-counter $390.22")),
        "return_pct": fig("fraction", LEDGER, "performance", LOW_N),
        "sd_per_trade": fig("fraction", None, None, LOW_N + " (n=3)"),
        "se": fig("fraction", None, None, LOW_N + " (n=3)"),
        "sigma_from_zero": fig("sigma", None, None, LOW_N + (
            " (n=3). No arrangement of 3 settlements is distinguishable from "
            "zero. The cumulative figure is 0.23 sigma; in-window there is no "
            "evidence of anything in either direction")),
        "daily_series": fig("rows", LEDGER, "performance", (
            "resolution activities carry no timestamp in any field the "
            "settlement parser reads, so every settlement buckets as "
            "'undated' — confirmed live: 'undated 3 settled W1/L2'")),
    },

    # ── drift / retention / surcharge, per category ─────────────────────
    "drift_cents": {c: fig("cents", LEDGER, "drift_report", NOT_PER_FILL)
                    for c in CATEGORIES},
    "drift_surcharge_cents": {
        c: fig("cents", LEDGER, "drift_penalties", NOT_PER_FILL, note=(
            "A thin category does NOT recut to 0.00c: drift_penalties() "
            "already makes under-DRIFT_MIN_N categories inherit the overall "
            "surcharge rather than trade free. Zero is reached only when "
            "nothing at all is measured, which is the documented pre-existing "
            "behaviour (the bands govern alone). Correcting an earlier note "
            "of mine that called this a defect — it is not"))
        for c in CATEGORIES},
    "retention": {
        c: fig("fraction", LEDGER, "drift_report", NOT_PER_FILL, note=(
            "excluded-denominator count (|fair_entry - price| < 0.005) is "
            "also unobtainable"))
        for c in CATEGORIES},

    # ── free samples and reversion ──────────────────────────────────────
    "free_samples": {
        "n_observations": fig("count", LEDGER, "price_drift_report", NO_WINDOW),
        "mean_drift_cents": fig("cents", LEDGER, "price_drift_report",
                                NO_WINDOW),
    },
    "reversion_keep": {
        "slope": fig("fraction", LEDGER, "reversion", NO_WINDOW),
        "keep": fig("fraction", LEDGER, "reversion", NO_WINDOW, note=(
            "REVERSION_MIN_N = 200. Determines whether shrinkage applies "
            "INSTEAD of the surcharge; the two must never both apply")),
        "r_squared": fig("fraction", LEDGER, "reversion", (
            "not_computed: reversion() returns slope and n but does not "
            "compute R^2. Adding it is a change to the existing function, not "
            "a second implementation")),
    },

    # ── Gate 3 quarantine cohorts ───────────────────────────────────────
    "gate3_quarantine_cohorts": {
        c: {"n": fig("count", LEDGER, "drift_report", NOT_PER_FILL),
            "drift_cents": fig("cents", LEDGER, "drift_report", NOT_PER_FILL),
            "retention": fig("fraction", LEDGER, "drift_report", NOT_PER_FILL)}
        for c in ["moneyline", "draw", "free_pool"]},

    # ── refusal telemetry ───────────────────────────────────────────────
    "refusals": fig("rows", "src/edge/shadow/runner.py", "run_cycle", (
        "not_persisted: refusal counters are per-CYCLE funnel counters, "
        "discarded at the end of each cycle. Only a single ~22s cycle is ever "
        "visible. There is no windowed store to aggregate")),

    # ── figures the document asserts that I could not trace at all ──────
    "untraced_document_claims": {
        "philosophy_11_record_313_settled": fig(
            "usd", None, None, UNTRACED, note=(
                "PHILOSOPHY.md section 11 asserts 313 settled, 85W/227L, "
                "-$5.02 on $227.57. No reachable store reproduces it. Should "
                "not appear in the document until it can be")),
        "philosophy_11_settlements_per_day": fig(
            "count_per_day", None, None, UNTRACED, note=(
                "'~150/day vs ~10/day' has no source in any report")),
        "philosophy_4_daily_loss_halt": fig(
            "usd", "config/risk.yaml", None, UNTRACED, note=(
                "documented as '$15, or 15% of the day, or 4 sigma'; engine "
                "reports halt_at=61.03, which is neither $15 nor 15% of the "
                "$232.78 day cap ($34.92)")),
    },

    # ── stopping rule ───────────────────────────────────────────────────
    "stopping_rule": {
        "settlements_in_window": fig("count", LEDGER, "performance", LOW_N,
                                     n=3),
        "target": {"value": 500, "unit": "settlements",
                   "source_file": "PHILOSOPHY.md", "source_function": None,
                   "n": None,
                   "computed_at": WINDOW_END.isoformat().replace("+00:00",
                                                                "Z")},
        "pct_complete": {"value": 0.6, "unit": "percent", "n": 3,
                         "source_file": LEDGER,
                         "source_function": "performance",
                         "computed_at": WINDOW_END.isoformat().replace(
                             "+00:00", "Z"),
                         "note": "3 of 500, on the platform cohort"},
        "projected_completion_date": fig("date", None, None, (
            "refuse_to_extrapolate: 3 settlements over 40 hours is not a "
            "rate, and the population is contaminated by the repeated-entry "
            "defect. The engine is also PAPER as of commit ae436c6, so the "
            "live settlement rate is currently zero and the clock is stopped")),
    },
}


def write_json() -> None:
    (OUT / "figures_2026-08.json").write_text(
        json.dumps(figures, indent=2) + "\n")


TRADE_COLUMNS = [
    "entry_ts_utc", "settle_ts_utc", "league", "category", "market_id",
    "event_id", "outcome", "entry_price", "executable_price", "fair_at_entry",
    "fair_at_plus_60s", "stake", "resolution", "payout", "pnl",
    "threshold_required", "edge_claimed", "ladder_rung",
]

REFUSAL_COLUMNS = ["reason_code", "gate", "count", "share_of_candidates"]


def write_csvs() -> None:
    """Header-only, with the reason recorded in the file itself.

    Deliberately not populated with partial rows. Six of the eighteen trade
    columns have no source anywhere (category, fair_at_plus_60s per position,
    threshold_required, ladder_rung, executable_price distinct from
    limit_price, event_id), and filling the rest would produce a file that
    looks like an export and is not one.
    """
    rows: list[list] = []          # no in-window rows are obtainable

    # Step 6: assert the invariant in code, not by eye. Vacuous while rows is
    # empty — but it is the check that must survive once rows exist, so it
    # lives here rather than in a comment.
    start = WINDOW_START.isoformat().replace("+00:00", "Z")
    for r in rows:
        assert r[0] >= start, f"row predates window start: {r[0]}"
        if r[1]:
            assert r[1] >= start, f"settlement predates window start: {r[1]}"

    with (OUT / "trades_2026-08.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(TRADE_COLUMNS)
        w.writerows(rows)
        fh.write("# EMPTY BY DESIGN, not by failure. No in-window position "
                 "rows are obtainable: the ledger lives on the edge-shadow "
                 "worker disk, and 6 of these 18 columns (category, "
                 "fair_at_plus_60s per position, threshold_required, "
                 "ladder_rung, executable_price, event_id) have no source in "
                 "any store. See out/RECUT_NOTES.md sections 0 and 1.\n")

    with (OUT / "refusals_2026-08.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(REFUSAL_COLUMNS)
        fh.write("# EMPTY BY DESIGN. Refusal counts are per-cycle funnel "
                 "counters and are never persisted, so no window can be "
                 "aggregated. A single ~22s cycle is visible at a time. See "
                 "out/RECUT_NOTES.md section 3.\n")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    write_json()
    write_csvs()
    print(f"window {WINDOW_START.isoformat()} -> {WINDOW_END.isoformat()}")
    print(f"wrote {OUT}/figures_2026-08.json")
    print(f"wrote {OUT}/trades_2026-08.csv    (header only)")
    print(f"wrote {OUT}/refusals_2026-08.csv  (header only)")
    print("every figure is null; see out/RECUT_NOTES.md for why")
