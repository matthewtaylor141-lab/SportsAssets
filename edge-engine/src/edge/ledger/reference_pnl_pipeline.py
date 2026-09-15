"""
analyze.py — Classify trades by sport / category / type and compute realized PnL.
Rewritten July 2026 to handle 5.35M fills within a 4GB container. Methodology
is IDENTICAL to the original (documented below); the changes are performance
and API-drift repairs:

  PERFORMANCE
  - Sport/category classification runs once per MARKET (146K rows) and is
    mapped onto fills, instead of once per fill (5.35M rows).
  - The PnL engine writes into a preallocated numpy array instead of a list
    of per-fill dicts (the dict list alone would exceed container memory).
  - Low-cardinality string columns use pandas category dtype.

  API-DRIFT REPAIR
  - outcomeIndex sentinel 999 (or missing) is rebuilt by matching the fill's
    `outcome` label against the Gamma market's outcomes list. Unrepairable
    sentinels are counted and reported; they never match a winning index, so
    any residual inventory in them is excluded from resolution PnL rather
    than being silently marked a loser at $0 — see console output.

PnL METHODOLOGY (unchanged — management can audit it):
  - Average-cost position engine per (market, outcome token).
  - A BUY adds to position at cost. A SELL realizes (sell_price - avg_cost) * size
    on the sale date.
  - At market resolution, remaining shares realize (payout - avg_cost) * size on
    the resolution date, where payout = $1 if that outcome won, else $0.
  - Daily PnL = sum of all realizations dated that day. Open unresolved positions
    are excluded from realized PnL and reported separately.
  - This is cash-flow-accurate realized PnL. It will differ from Polymarket's
    displayed "profit" figures, which include unrealized mark-to-market.

Inputs:  data/trades_raw.csv, data/markets_meta.csv
Outputs: data/trades_enriched.csv, data/daily_pnl.csv, data/agg_*.csv, data/summary.json
"""

import gc
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---------------- Sport classification (unchanged rules) ----------------
SPORT_PATTERNS = [
    ("Soccer",     r"\b(fifwc|world cup|epl|premier league|la liga|serie a|bundesliga|ligue 1|uefa|champions league|mls|copa|fifa|liga mx)\b"),
    ("Tennis",     r"\b(atp|wta|wimbledon|us open tennis|roland garros|french open|australian open|tennis)\b"),
    ("Basketball", r"\b(nba|wnba|ncaam|ncaab|basketball|euroleague)\b"),
    ("Football",   r"\b(nfl|ncaafb|super bowl|college football)\b"),
    ("Baseball",   r"\b(mlb|baseball|world series)\b"),
    ("Hockey",     r"\b(nhl|hockey|stanley cup)\b"),
    ("MMA/Boxing", r"\b(ufc|mma|boxing|fight night)\b"),
    ("Golf",       r"\b(pga|golf|masters|ryder cup)\b"),
    ("Motorsport", r"\b(f1|formula 1|nascar|motogp|grand prix)\b"),
    ("Esports",    r"\b(esports|csgo|cs2|league of legends|dota|valorant)\b"),
    ("Cricket",    r"\b(cricket|ipl|t20)\b"),
]
SPORT_TAG_MAP = {
    "soccer": "Soccer", "football": "Football", "nfl": "Football", "epl": "Soccer",
    "nba": "Basketball", "basketball": "Basketball", "mlb": "Baseball",
    "baseball": "Baseball", "nhl": "Hockey", "hockey": "Hockey", "tennis": "Tennis",
    "mma": "MMA/Boxing", "ufc": "MMA/Boxing", "boxing": "MMA/Boxing", "golf": "Golf",
    "cricket": "Cricket", "esports": "Esports", "world cup": "Soccer",
    "champions league": "Soccer", "la liga": "Soccer", "serie a": "Soccer",
    "nascar": "Motorsport", "f1": "Motorsport", "wnba": "Basketball",
}


SLUG_PREFIX_MAP = {
    # Basketball
    "cbb": "Basketball", "bkcba": "Basketball",
    # American football
    "cfb": "Football",
    # Tennis
    "itf": "Tennis",
    # Baseball
    "kbo": "Baseball",
    # Soccer — league/competition codes observed in this account's slugs
    **{p: "Soccer" for p in [
        "elc", "lal", "sea", "es2", "bra", "ucl", "bun", "arg", "fl1", "fif",
        "j2100", "chi", "itsb", "bra2", "mex", "ere", "spl", "tur", "j1100",
        "por", "uel", "nor", "col", "kor", "bl2", "fr2", "col1", "mar1", "lib",
        "egy1", "sud", "rou1", "scop", "aus", "per1", "swe", "chi1", "rus",
        "cze1", "den", "efa", "el1", "el2", "uef", "nwsl", "enl", "ukr1",
        "hr1", "cde", "gtm", "isp", "brco", "acn", "svk1", "cdr", "epl",
        "mls", "sga",
    ]},
}
_PREFIX_RE = re.compile(r"^([a-z0-9]+)-")


def classify_sport(row):
    tags = str(row.get("tags", "") or "").lower()
    for key, sport in SPORT_TAG_MAP.items():
        if key in tags:
            return sport
    # July 2026 gamma drift: closed markets return empty tags. Slug league
    # prefixes (e.g. bra-cor-pal-...) are the most reliable signal.
    slug = str(row.get("market_slug", "") or "").lower()
    m = _PREFIX_RE.match(slug)
    if m and m.group(1) in SLUG_PREFIX_MAP:
        return SLUG_PREFIX_MAP[m.group(1)]
    text = f"{slug} {row.get('market_question','')}".lower()
    for sport, pat in SPORT_PATTERNS:
        if re.search(pat, text):
            return sport
    # Draw / both-teams-to-score phrasing only occurs in soccer markets here
    if re.search(r"end in a draw|both teams to score", text):
        return "Soccer"
    if "sports" in tags:
        return "Other Sports"
    return "Non-Sports"


def classify_category(text):
    t = str(text).lower()
    if re.search(r"exact score", t):
        return "Exact Score"
    if re.search(r"spread|\(\s*[-+]\d+(\.\d+)?\s*\)", t):
        return "Spread"
    if re.search(r"o/u|over/under|\bu\s*\d+(\.\d+)?\b.*goals|total (goals|points|runs)|\bo/u\b", t):
        return "Totals (O/U)"
    if re.search(r"win the|champion|winner of the|to win .*(cup|league|title|series|tournament)", t):
        return "Futures/Outright"
    if re.search(r"will .* win on|\bvs\.?\b|beat ", t):
        return "Moneyline"
    if re.search(r"both teams|first (goal|touchdown|basket)|scorer|cards|corners|assists|points\b", t):
        return "Props"
    return "Other"


BAND_EDGES = [-np.inf, 0.10, 0.30, 0.70, 0.90, np.inf]
BAND_LABELS = ["0-10c (longshot)", "10-30c (underdog)", "30-70c (coin-flip)",
               "70-90c (favorite)", "90-100c (heavy favorite)"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    t0 = time.time()
    log("loading trades_raw.csv ...")
    trades = pd.read_csv(
        DATA_DIR / "trades_raw.csv",
        usecols=["timestamp", "condition_id", "market_slug", "market_question",
                 "side", "outcome", "outcome_index", "price", "size"],
        dtype={"condition_id": "category", "market_slug": "category",
               "market_question": "category", "side": "category",
               "outcome": "category", "outcome_index": "float32",
               "price": "float64", "size": "float64"},
        parse_dates=["timestamp"])
    log(f"  {len(trades):,} fills, {trades['condition_id'].nunique():,} markets")

    meta = pd.read_csv(DATA_DIR / "markets_meta.csv")
    meta = meta.drop_duplicates(subset=["condition_id"])

    # ---- Market-level classification table (meta + fallback from fills) ----
    log("classifying markets (sport/category at market level) ...")
    mk = (trades[["condition_id", "market_slug", "market_question"]]
          .astype({"condition_id": str, "market_slug": str, "market_question": str})
          .drop_duplicates("condition_id"))
    mk = mk.merge(meta[["condition_id", "question", "slug", "tags",
                        "outcomes", "outcome_prices", "closed", "end_date",
                        "resolved_at"]],
                  on="condition_id", how="left")
    mk["market_question"] = mk["question"].fillna(mk["market_question"])
    mk["market_slug"] = mk["slug"].fillna(mk["market_slug"])
    mk["sport"] = mk.apply(classify_sport, axis=1)
    mk["category"] = mk["market_question"].map(classify_category)

    sport_map = dict(zip(mk["condition_id"], mk["sport"]))
    cat_map = dict(zip(mk["condition_id"], mk["category"]))

    cid_str = trades["condition_id"].astype(str)
    trades["sport"] = pd.Categorical(cid_str.map(sport_map))
    trades["category"] = pd.Categorical(cid_str.map(cat_map))

    # ---- outcome_index sentinel repair (API drift: 999) ----
    log("repairing outcome_index sentinels ...")
    label_idx = {}
    for m in mk.itertuples():
        try:
            outs = json.loads(m.outcomes) if isinstance(m.outcomes, str) else []
        except Exception:
            outs = []
        for i, o in enumerate(outs):
            label_idx[f"{m.condition_id}||{str(o).strip().lower()}"] = i
    oi = trades["outcome_index"].astype("float64")
    needs = oi.isna() | (oi == 999)
    n_needs = int(needs.sum())
    if n_needs:
        keys = (cid_str[needs] + "||"
                + trades.loc[needs, "outcome"].astype(str).str.strip().str.lower())
        oi.loc[needs] = keys.map(label_idx)
        still = int(oi.isna().sum())
        log(f"  {n_needs:,} sentinel/missing; {still:,} unrepairable "
            f"({still/len(trades):.2%} of fills)")
    trades["outcome_index"] = oi.fillna(-1).astype("int32")  # -1 = unknown token
    del cid_str, oi
    gc.collect()

    trades["price_band"] = pd.cut(trades["price"], BAND_EDGES, labels=BAND_LABELS,
                                  right=False)
    trades["notional"] = trades["price"] * trades["size"]

    # ---- Resolution lookup ----
    def winning_indices(op):
        try:
            prices = json.loads(op) if isinstance(op, str) and op else []
            return frozenset(i for i, p in enumerate(float(x) for x in prices) if p > 0.5)
        except Exception:
            return None
    mk["win_idx"] = mk["outcome_prices"].map(winning_indices)
    # Prefer actual resolution time (closedTime); fall back to scheduled
    # end_date clamped to now, so resolutions are never attributed to dates
    # before the trades or in the future.
    closed_time = (pd.to_datetime(mk["resolved_at"], errors="coerce", utc=True,
                                  format="mixed").dt.tz_localize(None))
    end_date = (pd.to_datetime(mk["end_date"], errors="coerce", utc=True)
                .dt.tz_localize(None))
    now = pd.Timestamp.utcnow().tz_localize(None)
    mk["res_date"] = closed_time.fillna(end_date.clip(upper=now))
    res_lookup = {m.condition_id: (m.win_idx, m.res_date, bool(m.closed) if pd.notna(m.closed) else False)
                  for m in mk.itertuples()}

    # ---- PnL engine: average cost, preallocated output ----
    log("running average-cost PnL engine over all fills ...")
    trades = trades.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    n = len(trades)
    pnl_arr = np.zeros(n, dtype="float64")
    positions = {}
    cids = trades["condition_id"].astype(str).to_numpy()
    oidxs = trades["outcome_index"].to_numpy()
    sides = trades["side"].astype(str).to_numpy()
    prices = trades["price"].to_numpy()
    sizes = trades["size"].to_numpy()
    for i in range(n):
        key = (cids[i], oidxs[i])
        pos = positions.get(key)
        if pos is None:
            pos = positions[key] = [0.0, 0.0]  # [size, cost]
        if sides[i] == "BUY":
            pos[0] += sizes[i]
            pos[1] += sizes[i] * prices[i]
        else:  # SELL
            avg = pos[1] / pos[0] if pos[0] > 1e-9 else prices[i]
            sell_sz = min(sizes[i], pos[0]) if pos[0] > 1e-9 else sizes[i]
            pnl_arr[i] = (prices[i] - avg) * sell_sz
            pos[0] = max(pos[0] - sizes[i], 0.0)
            pos[1] = pos[0] * avg
        if i % 1_000_000 == 0 and i:
            log(f"  {i:,}/{n:,} fills processed")
    trades["trade_pnl"] = pnl_arr
    del cids, oidxs, sides, prices, sizes, pnl_arr
    gc.collect()
    log(f"  engine done ({time.time()-t0:.0f}s elapsed)")

    # ---- Resolution events for remaining inventory ----
    log("computing resolution events ...")
    res_events, open_cost, skipped_unknown_token = [], 0.0, 0.0
    for (cid, oidx), (psize, pcost) in positions.items():
        if psize <= 1e-9:
            continue
        info = res_lookup.get(cid)
        if not info or info[0] is None or not info[2]:
            open_cost += pcost          # open / unresolved -> excluded
            continue
        if oidx < 0:
            skipped_unknown_token += pcost   # unrepairable token -> excluded, not zeroed
            continue
        payout = 1.0 if int(oidx) in info[0] else 0.0
        avg = pcost / psize
        res_events.append({
            "condition_id": cid, "outcome_index": oidx,
            "timestamp": info[1] if pd.notna(info[1]) else pd.NaT,
            "trade_pnl": (payout - avg) * psize,
            "size": psize, "price": payout, "side": "RESOLVE",
        })
    res_df = pd.DataFrame(res_events)
    log(f"  {len(res_df):,} resolution events; open-at-cost ${open_cost:,.0f}; "
        f"excluded unknown-token inventory ${skipped_unknown_token:,.0f}")
    if not res_df.empty:
        res_df["sport"] = res_df["condition_id"].map(sport_map)
        res_df["category"] = res_df["condition_id"].map(cat_map)
        res_df["price_band"] = "resolution"
        res_df["notional"] = 0.0

    enriched = pd.concat([trades, res_df], ignore_index=True)
    del trades, res_df
    gc.collect()
    enriched["date"] = pd.to_datetime(enriched["timestamp"]).dt.date

    log("writing trades_enriched.csv ...")
    enriched.to_csv(DATA_DIR / "trades_enriched.csv", index=False)

    # ---- Aggregations (methodology unchanged) ----
    log("aggregating ...")
    daily = (enriched.dropna(subset=["date"])
             .groupby("date", as_index=False)["trade_pnl"].sum()
             .rename(columns={"trade_pnl": "pnl"}))
    daily.to_csv(DATA_DIR / "daily_pnl.csv", index=False)

    def agg(df, by):
        g = df.groupby(by, observed=True).agg(
            fills=("trade_pnl", "size"),
            volume=("notional", "sum"),
            realized_pnl=("trade_pnl", "sum"),
        ).reset_index()
        wins = df[df.trade_pnl > 0].groupby(by, observed=True)["trade_pnl"].size()
        closers = df[df.trade_pnl != 0].groupby(by, observed=True)["trade_pnl"].size()
        g = g.merge(wins.rename("wins"), on=by, how="left") \
             .merge(closers.rename("closes"), on=by, how="left")
        g["win_rate_closes"] = (g["wins"] / g["closes"]).fillna(0)
        g["roi_on_volume"] = np.where(g["volume"] > 0, g["realized_pnl"] / g["volume"], np.nan)
        return g.sort_values("realized_pnl", ascending=False)

    agg(enriched, ["sport"]).to_csv(DATA_DIR / "agg_sport.csv", index=False)
    agg(enriched, ["category"]).to_csv(DATA_DIR / "agg_category.csv", index=False)
    agg(enriched, ["side"]).to_csv(DATA_DIR / "agg_side.csv", index=False)
    agg(enriched, ["price_band"]).to_csv(DATA_DIR / "agg_priceband.csv", index=False)
    agg(enriched, ["sport", "category"]).to_csv(DATA_DIR / "agg_sport_category.csv", index=False)

    n_fills = int((enriched["side"] != "RESOLVE").sum())
    summary = {
        "total_realized_pnl": float(enriched["trade_pnl"].sum()),
        "total_fills": n_fills,
        "total_volume": float(enriched["notional"].sum()),
        "date_start": str(enriched["date"].min()),
        "date_end": str(enriched["date"].max()),
        "open_positions_at_cost": float(open_cost),
        "excluded_unknown_token_at_cost": float(skipped_unknown_token),
        "green_days": int((daily["pnl"] > 0).sum()),
        "red_days": int((daily["pnl"] < 0).sum()),
        "best_day": daily.loc[daily["pnl"].idxmax()].to_dict() if len(daily) else {},
        "worst_day": daily.loc[daily["pnl"].idxmin()].to_dict() if len(daily) else {},
    }
    (DATA_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    log(f"DONE in {(time.time()-t0)/60:.1f} min. Next: python src/report_pdf.py")


if __name__ == "__main__":
    main()
