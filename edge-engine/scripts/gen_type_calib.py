#!/usr/bin/env python3
"""gen_type_calib.py — lifetime edge by MARKET TYPE and prop subtype.

Owner question 2026-08-17 night: "examine the lifetime performance of
additional markets that the whales are operating in (i.e. Player props)
... which is most profitable in player props and what the profit
percentage per dollar spend is".

Reads a wallet's raw pull (trades_raw.csv + markets_meta.csv), scores
every resolved BUY fill as edge_if_held = payout - price (the Phase-1
methodology, same as gen_calib.py), and aggregates by market category
(moneyline / spread / totals / props / exact score / futures) and, for
props, by the specific prop (points, rebounds, home runs, strikeouts,
touchdowns, goal scorer, ...). "Profit percentage per dollar spend" =
pnl_if_held / stake (roi column).

Outputs calib_types.csv and prints one TYPESUM line for the blob
channel. Usage: PULL_DATA_DIR=... PULL_WALLET=... python gen_type_calib.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pandas as pd

DATA_DIR = Path(os.environ.get("PULL_DATA_DIR", "data"))

# Category from question/slug text — extends the audited classifier in
# reference_pnl_pipeline.classify_category with slug word forms.
_CATS = [
    ("Exact Score", r"exact score|-exact-score-"),
    ("BTTS", r"both teams to score|-btts|-ftts"),
    ("Spread", r"spread|\(\s*[-+]\d+(\.\d+)?\s*\)"),
    ("Totals (O/U)", r"o/u|over/under|total (goals|points|runs)|-total-|"
                     r"-o\d+pt5|-u\d+pt5"),
    ("Futures/Outright", r"win the|champion|winner of the|"
                         r"to win .*(cup|league|title|series|tournament)"),
]
_PROP_HINT = re.compile(
    r"both teams|first (goal|touchdown|basket)|scorer|cards|corners|"
    r"assists|rebounds|points\b|home run|strikeout|record a hit|\brbi\b|"
    r"total bases|touchdown|passing yards|rushing yards|receiving yards|"
    r"three-pointers|3-pointers|aces|double fault|saves|shots on|"
    r"-player-|goals? scored by")
_ML = re.compile(r"will .* win on|\bvs\.?\b|beat ")

_SUBTYPES = [
    ("home-run", r"home run|-hr-"),
    ("strikeouts", r"strikeout"),
    ("hits (batter)", r"record a hit|\d+\+ hits"),
    ("rbi", r"\brbi\b|runs batted"),
    ("total-bases", r"total bases"),
    ("points (basketball)", r"(score|\d\+)\s*points|-player-points|points\b"),
    ("rebounds", r"rebound"),
    ("assists", r"assist"),
    ("threes", r"three-pointers|3-pointers|threes"),
    ("touchdown", r"touchdown"),
    ("passing-yards", r"passing yards"),
    ("rushing-yards", r"rushing yards"),
    ("receiving-yards", r"receiving yards"),
    ("goal-scorer", r"(anytime|first) goal|goals? scored by|scorer"),
    ("first-scorer", r"first (basket|touchdown)"),
    ("cards", r"cards"),
    ("corners", r"corners"),
    ("aces", r"aces|double fault"),
    ("saves", r"saves"),
    ("shots", r"shots on"),
]


def classify(text: str) -> tuple[str, str | None]:
    t = (text or "").lower()
    for cat, pat in _CATS:
        if re.search(pat, t):
            return cat, None
    if _PROP_HINT.search(t):
        for sub, pat in _SUBTYPES:
            if re.search(pat, t):
                return "Props", sub
        return "Props", "other-prop"
    if _ML.search(t):
        return "Moneyline", None
    return "Other", None


def winning_indices(op):
    try:
        prices = json.loads(op) if isinstance(op, str) and op else []
        return frozenset(i for i, p in
                         enumerate(float(x) for x in prices) if p > 0.5)
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    t = pd.read_csv(DATA_DIR / "trades_raw.csv",
                    usecols=["condition_id", "market_slug",
                             "market_question", "side", "outcome",
                             "outcome_index", "price", "size"],
                    dtype={"condition_id": "string",
                           "market_slug": "string",
                           "market_question": "string",
                           "side": "string", "outcome": "string"})
    t = t[t["side"] == "BUY"].copy()
    for c in ("price", "size"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t.dropna(subset=["price", "size", "condition_id"])

    mk = pd.read_csv(DATA_DIR / "markets_meta.csv",
                     usecols=["condition_id", "question", "closed",
                              "outcomes", "outcome_prices"],
                     dtype={"condition_id": "string",
                            "question": "string"})
    mk["win_idx"] = mk["outcome_prices"].map(winning_indices)
    res = mk[mk["closed"].astype("boolean").fillna(False)
             & mk["win_idx"].notna()]
    win_map = dict(zip(res["condition_id"], res["win_idx"]))
    q_map = dict(zip(mk["condition_id"], mk["question"]))

    label_map: dict = {}
    for r in mk.itertuples():
        try:
            for i, lbl in enumerate(json.loads(r.outcomes)
                                    if isinstance(r.outcomes, str) else []):
                label_map[(r.condition_id,
                           str(lbl).strip().lower())] = i
        except Exception:  # noqa: BLE001
            continue
    oi = pd.to_numeric(t["outcome_index"], errors="coerce")
    bad = oi.isna() | (oi >= 999) | (oi < 0)
    if bad.any():
        keys = list(zip(t.loc[bad, "condition_id"],
                        t.loc[bad, "outcome"].str.strip().str.lower()))
        oi.loc[bad] = [label_map.get(k) for k in keys]
    t["outcome_index"] = oi
    t = t.dropna(subset=["outcome_index"])
    t["outcome_index"] = t["outcome_index"].astype(int)
    t["win_idx"] = t["condition_id"].map(win_map)
    t = t.dropna(subset=["win_idx"])
    t["payout"] = [1.0 if int(i) in w else 0.0
                   for i, w in zip(t["outcome_index"], t["win_idx"])]
    t["notional"] = t["price"] * t["size"]
    t["pnl"] = t["size"] * (t["payout"] - t["price"])

    # Classify once per MARKET, map onto fills (the 5.35M-fill lesson).
    per_mkt = {}
    for cid in t["condition_id"].unique():
        slug_rows = t.loc[t["condition_id"] == cid, "market_slug"]
        text = f"{q_map.get(cid, '')} {slug_rows.iloc[0] if len(slug_rows) else ''}"
        per_mkt[cid] = classify(text)
    t["category"] = t["condition_id"].map(lambda c: per_mkt[c][0])
    t["subtype"] = t["condition_id"].map(lambda c: per_mkt[c][1])

    def agg(g):
        stake = g["notional"].sum()
        pnl = g["pnl"].sum()
        sz = g["size"].sum()
        return pd.Series({
            "n": len(g), "stake": round(stake, 2),
            "pnl": round(pnl, 2),
            "roi": round(pnl / stake, 6) if stake else None,
            "win_rate": round(float((g["size"] * g["payout"]).sum() / sz),
                              4) if sz else None})

    by_cat = t.groupby("category", sort=False).apply(
        agg, include_groups=False).reset_index()
    props = t[t["category"] == "Props"]
    by_sub = (props.groupby("subtype", sort=False).apply(
        agg, include_groups=False).reset_index()
        if len(props) else pd.DataFrame())

    out = pd.concat([by_cat.assign(subtype=None)[
        ["category", "subtype", "n", "stake", "pnl", "roi", "win_rate"]],
        by_sub.assign(category="Props")[
        ["category", "subtype", "n", "stake", "pnl", "roi", "win_rate"]]
        if len(by_sub) else pd.DataFrame()], ignore_index=True)
    out.to_csv(DATA_DIR / "calib_types.csv", index=False)

    summary = {
        "wallet": os.environ.get("PULL_WALLET", "?"),
        "resolved_buy_fills": int(len(t)),
        "by_category": {r.category: {"n": int(r.n),
                                     "stake": float(r.stake),
                                     "pnl": float(r.pnl),
                                     "roi": r.roi}
                        for r in by_cat.itertuples()},
        "prop_subtypes": {r.subtype: {"n": int(r.n),
                                      "stake": float(r.stake),
                                      "pnl": float(r.pnl), "roi": r.roi}
                          for r in by_sub.itertuples()}
        if len(by_sub) else {},
    }
    print("TYPESUM " + json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
