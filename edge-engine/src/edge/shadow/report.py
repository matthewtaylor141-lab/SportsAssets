"""Nightly report (build step 7): graded ledger results vs the reference
calibration curves, with divergence alerts.

Compares realized edge (cents per $1 staked) by band / league / venue against
data/calib_price.csv and data/calib_league.csv — the measured ground truth
from the 5.34M-fill reference dataset. Divergence beyond tolerance on a
meaningful sample raises an alert line; alerts are the input a human reads
before ever touching config/risk.yaml's mode line.

Attribution note: realized PnL lives per market (average-cost methodology);
each market's PnL is attributed to the band of its FIRST buy fill. Under the
one-position-per-event rule markets have a single entry fill, so this is
exact in beta; with multi-fill markets it is an approximation and is labeled
as such in the report.
"""

from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

DIVERGENCE_CENTS = 2.0        # |ours - reference| beyond this alerts
MIN_STAKED_FOR_ALERT = 500.0  # need a real sample before alerting


def _data_dir() -> Path:
    for c in (os.environ.get("EDGE_DATA_DIR"),
              Path(__file__).resolve().parents[3] / "data",
              Path.cwd() / "data"):
        if c and Path(c).is_dir():
            return Path(c)
    fallback = Path.cwd() / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _load_calib(name: str, key_field: str, value_field: str) -> dict[str, float]:
    """Reference CSVs ship with the repo/image; writable state may live on a
    separate mounted disk — search both."""
    candidates = [_data_dir() / name,
                  Path(__file__).resolve().parents[3] / "data" / name]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return {}
    out: dict[str, float] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                out[str(row[key_field]).strip()] = float(row[value_field])
            except (KeyError, TypeError, ValueError):
                continue
    return out


def _first_fill_meta(ledger) -> dict[str, dict]:
    """market_key -> {band, venue, league, staked} from each market's fills."""
    with ledger._conn() as conn:  # noqa: SLF001 — same package
        rows = conn.execute(
            """
            SELECT market_key, venue, league, decision, qty * price AS staked, ts
            FROM fills WHERE side = 'BUY' ORDER BY market_key, ts
            """
        ).fetchall()
    meta: dict[str, dict] = {}
    for r in rows:
        m = meta.setdefault(r["market_key"], {
            "venue": r["venue"], "league": r["league"], "staked": 0.0, "band": None})
        m["staked"] += float(r["staked"])
        if m["band"] is None and r["decision"]:
            try:
                m["band"] = (json.loads(r["decision"]) or {}).get("band")
            except ValueError:
                pass
    return meta


def _norm_bin(raw: str) -> str | None:
    """calib_price bin '[0.05, 0.1)' -> engine band key '0.05-0.10'."""
    import re

    m = re.match(r"\[([\d.]+),\s*([\d.]+)\)", raw or "")
    if not m:
        return None
    return f"{float(m.group(1)):.2f}-{float(m.group(2)):.2f}"


def nightly_report(ledger, policy) -> dict:
    """Aggregate, compare, alert, persist. Returns the report dict."""
    ref_band = {}
    for k, v in _load_calib("calib_price.csv", "bin", "edge_cents").items():
        nk = _norm_bin(k)
        if nk:
            ref_band[nk] = v
    ref_league = _load_calib("calib_league.csv", "prefix", "edge_cents")
    meta = _first_fill_meta(ledger)

    by = {"band": {}, "league": {}, "venue": {}}
    with ledger._conn() as conn:  # noqa: SLF001
        pos_rows = conn.execute(
            "SELECT market_key, realized_pnl, fees_paid, resolved FROM positions"
        ).fetchall()
    for p in pos_rows:
        m = meta.get(p["market_key"])
        if m is None or not p["resolved"]:
            continue
        net = float(p["realized_pnl"]) - float(p["fees_paid"])
        for dim, key in (("band", m["band"] or "?"), ("league", m["league"] or "?"),
                         ("venue", m["venue"])):
            row = by[dim].setdefault(key, {"staked": 0.0, "net_pnl": 0.0, "markets": 0})
            row["staked"] += m["staked"]
            row["net_pnl"] += net
            row["markets"] += 1
    for dim in by.values():
        for row in dim.values():
            row["edge_cents"] = round(100 * row["net_pnl"] / row["staked"], 2) \
                if row["staked"] > 0 else 0.0
            row["staked"] = round(row["staked"], 2)
            row["net_pnl"] = round(row["net_pnl"], 2)

    alerts: list[str] = []
    for dim, ref in (("band", ref_band), ("league", ref_league)):
        for key, row in by[dim].items():
            ref_val = ref.get(key)
            if ref_val is None or row["staked"] < MIN_STAKED_FOR_ALERT:
                continue
            div = row["edge_cents"] - ref_val
            if abs(div) > DIVERGENCE_CENTS:
                alerts.append(
                    f"{dim} {key}: ours {row['edge_cents']:+.1f}c vs reference "
                    f"{ref_val:+.1f}c ({div:+.1f}c, ${row['staked']:.0f} staked)")

    date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    report = {
        "date": date, "generated_ts": time.time(),
        "summary": ledger.summary(),
        "by_band": by["band"], "by_league": by["league"], "by_venue": by["venue"],
        "match_rate_24h": ledger.match_rate_report(days=1),
        "alerts": alerts,
        "attribution_note": "market PnL attributed to first-fill band; exact "
                            "under one-position-per-event",
        "shadow_gate": policy.risk.get("shadow_gate", {}),
    }
    out_dir = _data_dir() / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / f"nightly-{date}.json", "w") as f:
        json.dump(report, f, indent=2)
    return report
