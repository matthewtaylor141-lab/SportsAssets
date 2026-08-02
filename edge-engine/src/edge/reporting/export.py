"""Position-level export — the artifact that makes a claim checkable.

A summary asks to be trusted. A row per position, with what we paid, what we
thought it was worth, what the bar was and how it resolved, can be
recomputed by someone who does not trust the summary. Everything in the
methodology document should be derivable from this file.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from edge.reporting.figures import compute_figures

COLUMNS = [
    "entry_ts_utc", "settle_ts_utc", "venue", "league", "category", "band",
    "tier", "market_key", "entries", "entry_price", "shares", "stake",
    "fair_at_entry", "fair_at_plus_60s", "edge_claimed", "threshold_required",
    "resolved", "payout", "pnl",
]


def _iso(ts) -> str:
    if not ts:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def write_positions_csv(ledger, path: Path, *, days: int = 7,
                        since: float | None = None,
                        live_only: bool = True) -> int:
    """Write one row per position. Returns the row count.

    Asserts the window invariant in code rather than trusting the query: a
    row that predates the window start is a bug in the filter, and finding
    that by eye in a CSV is not a control.
    """
    rows = ledger.positions_export(days=days, since=since, live_only=live_only)
    start = float(since) if since is not None else None
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            if start is not None:
                assert r["entry_ts"] >= start, (
                    f"row predates window start: {r['market_key']} "
                    f"{r['entry_ts']} < {start}")
            w.writerow({
                "entry_ts_utc": _iso(r["entry_ts"]),
                "settle_ts_utc": _iso(r["settle_ts"]),
                "venue": r["venue"], "league": r["league"],
                "category": r["category"], "band": r["band"], "tier": r["tier"],
                "market_key": r["market_key"], "entries": r["entries"],
                "entry_price": r["entry_price"], "shares": r["shares"],
                "stake": r["stake"],
                "fair_at_entry": r["fair_at_entry"],
                "fair_at_plus_60s": r["fair_at_plus_60s"],
                "edge_claimed": r["edge_claimed"],
                "threshold_required": r["threshold_required"],
                "resolved": int(bool(r["resolved"])),
                "payout": r["payout"], "pnl": r["pnl"],
            })
    return len(rows)


def write_bundle(ledger, policy, out_dir: Path, *, days: int = 7,
                 since: float | None = None, live_only: bool = True) -> dict:
    """figures.json + positions.csv + METHODOLOGY.md, from one window."""
    from edge.reporting.methodology import render

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    figures = compute_figures(ledger, policy, days=days, since=since,
                              live_only=live_only)
    (out_dir / "figures.json").write_text(json.dumps(figures, indent=2) + "\n")
    n = write_positions_csv(ledger, out_dir / "positions.csv", days=days,
                            since=since, live_only=live_only)
    doc = render(ledger, policy, days=days, since=since, live_only=live_only,
                 figures=figures)
    (out_dir / "METHODOLOGY.md").write_text(doc)
    return {"positions": n, "out_dir": str(out_dir),
            "settled": figures["headline"]["n_settled"]["value"]}
