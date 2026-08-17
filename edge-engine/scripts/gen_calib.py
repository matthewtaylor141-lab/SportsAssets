#!/usr/bin/env python3
"""gen_calib.py — the Phase-1 calibration aggregator, wallet-agnostic.

Turns a wallet's raw fill dump (reference_pull.py output: trades_raw.csv
+ markets_meta.csv) into the study's calibration tables:

  calib_price.csv   5-cent entry-band edge (bin,n,stake,avg_price,
                    win_rate,edge_cents,roi_if_held,pnl_implied)
  calib_league.csv  slug-prefix edge (prefix,n,stake,avg_price,
                    win_rate,edge_cents,roi,implied_pnl)
  calib_size.csv    notional-bucket edge (szb,n,stake,pnl,roi)
  calib_summary.json  headline numbers for the blob channel

Methodology is the study's, verbatim (the SQL twin lives in
backend/sportsassets/analytics/calibration.py): for every BUY fill on a
RESOLVED market with a known outcome index, edge_if_held = payout −
price, share-weighted within the group. Sells, open markets, and
unrepairable outcome indices are excluded — never zeroed.

Usage: PULL_DATA_DIR=/path/to/data python scripts/gen_calib.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(os.environ.get("PULL_DATA_DIR", "data"))

SIZE_EDGES = [0, 10, 50, 250, 1000, 10_000, 50_000, float("inf")]
SIZE_LABELS = ["(0, 10]", "(10, 50]", "(50, 250]", "(250, 1000]",
               "(1000, 10000]", "(10000, 50000]", "(50000, inf)"]


def winning_indices(op) -> frozenset | None:
    try:
        prices = json.loads(op) if isinstance(op, str) and op else []
        return frozenset(i for i, p in
                         enumerate(float(x) for x in prices) if p > 0.5)
    except Exception:  # noqa: BLE001 — unparseable = unresolved
        return None


def load() -> pd.DataFrame:
    t = pd.read_csv(DATA_DIR / "trades_raw.csv",
                    usecols=["condition_id", "market_slug", "side",
                             "outcome", "outcome_index", "price", "size"],
                    dtype={"condition_id": "string",
                           "market_slug": "string", "side": "string",
                           "outcome": "string"})
    t = t[t["side"] == "BUY"].copy()
    t["price"] = pd.to_numeric(t["price"], errors="coerce")
    t["size"] = pd.to_numeric(t["size"], errors="coerce")
    t = t.dropna(subset=["price", "size", "condition_id"])

    mk = pd.read_csv(DATA_DIR / "markets_meta.csv",
                     usecols=["condition_id", "closed", "outcomes",
                              "outcome_prices"],
                     dtype={"condition_id": "string"})
    mk["win_idx"] = mk["outcome_prices"].map(winning_indices)
    mk = mk[mk["closed"].astype("boolean").fillna(False)
            & mk["win_idx"].notna()]
    win_map = dict(zip(mk["condition_id"], mk["win_idx"]))

    # outcomeIndex sentinel repair (999 / missing), same rule as the
    # pnl pipeline: match the fill's outcome label to the market list.
    label_map: dict = {}
    for r in mk.merge(
            pd.DataFrame({"condition_id": mk["condition_id"]}),
            on="condition_id").itertuples():
        pass  # (labels resolved below from the outcomes column directly)
    outs = pd.read_csv(DATA_DIR / "markets_meta.csv",
                       usecols=["condition_id", "outcomes"],
                       dtype={"condition_id": "string"})
    for r in outs.itertuples():
        try:
            labels = json.loads(r.outcomes) if isinstance(r.outcomes, str) \
                else []
            for i, lbl in enumerate(labels):
                label_map[(r.condition_id, str(lbl).strip().lower())] = i
        except Exception:  # noqa: BLE001
            continue

    oi = pd.to_numeric(t["outcome_index"], errors="coerce")
    bad = oi.isna() | (oi >= 999) | (oi < 0)
    if bad.any():
        keys = list(zip(t.loc[bad, "condition_id"],
                        t.loc[bad, "outcome"].str.strip().str.lower()))
        oi.loc[bad] = [label_map.get(k, np.nan) for k in keys]
    t["outcome_index"] = oi
    t = t.dropna(subset=["outcome_index"])
    t["outcome_index"] = t["outcome_index"].astype(int)

    t["win_idx"] = t["condition_id"].map(win_map)
    t = t.dropna(subset=["win_idx"])          # resolved markets only
    t["payout"] = [1.0 if int(i) in w else 0.0
                   for i, w in zip(t["outcome_index"], t["win_idx"])]
    t["notional"] = t["price"] * t["size"]
    t["pnl_if_held"] = t["size"] * (t["payout"] - t["price"])
    t["prefix"] = t["market_slug"].fillna("").str.split("-").str[0]
    return t


def _agg(g: pd.DataFrame) -> pd.Series:
    shares = g["size"].sum()
    stake = g["notional"].sum()
    pnl = g["pnl_if_held"].sum()
    return pd.Series({
        "n": len(g),
        "stake": round(stake, 2),
        "avg_price": round(stake / shares, 4) if shares else None,
        "win_rate": round(float((g["size"] * g["payout"]).sum() / shares),
                          6) if shares else None,
        "edge_cents": round(100 * pnl / shares, 4) if shares else None,
        "roi_if_held": round(pnl / stake, 6) if stake else None,
        "pnl_implied": round(pnl, 2),
    })


def main() -> None:
    t = load()
    if t.empty:
        print("NO RESOLVED BUY FILLS — nothing to calibrate", flush=True)
        sys.exit(1)

    band = np.floor(t["price"] * 20) / 20.0
    t["bin"] = band.map(lambda lo: f"[{lo:.2f}, {lo + 0.05:.2f})")
    px = t.groupby("bin", sort=True).apply(_agg, include_groups=False)
    px.index.name = "bin"
    px.reset_index().to_csv(DATA_DIR / "calib_price.csv", index=False)

    lg = (t[t["prefix"] != ""].groupby("prefix", sort=False)
          .apply(_agg, include_groups=False))
    lg = lg[lg["n"] >= 50].sort_values("pnl_implied", ascending=False)
    lg = lg.rename(columns={"roi_if_held": "roi",
                            "pnl_implied": "implied_pnl"})
    lg.index.name = "prefix"
    lg.reset_index().to_csv(DATA_DIR / "calib_league.csv", index=False)

    t["szb"] = pd.cut(t["notional"], SIZE_EDGES, labels=SIZE_LABELS,
                      right=True)
    sz = t.groupby("szb", sort=False, observed=True).apply(
        _agg, include_groups=False)
    sz.index.name = "szb"
    sz.reset_index().to_csv(DATA_DIR / "calib_size.csv", index=False)

    total_pnl = t["pnl_if_held"].sum()
    total_stake = t["notional"].sum()
    summary = {
        "wallet": os.environ.get("PULL_WALLET", "?"),
        "buy_fills_resolved": int(len(t)),
        "stake": round(float(total_stake), 2),
        "pnl_if_held": round(float(total_pnl), 2),
        "roi_if_held": round(float(total_pnl / total_stake), 6)
        if total_stake else None,
        "n_markets": int(t["condition_id"].nunique()),
        "span": [None, None],
    }
    (DATA_DIR / "calib_summary.json").write_text(
        json.dumps(summary, indent=1))
    print("CALIB DONE:", json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
