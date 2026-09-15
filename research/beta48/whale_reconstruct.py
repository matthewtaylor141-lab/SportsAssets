#!/usr/bin/env python3
"""BETA48 — ONE canonical reconstruction, run identically for all four
reference accounts (directive instruction 6).

READ ONLY. Contacts nothing. Reads a fill file, writes evidence.

TWO SOURCES, ONE ACCOUNTING
  --source csv  research runner: edge-engine reference_pull.py output
                (trades_raw.csv + markets_meta.csv)
  --source u2   this container:  research/snapshots/u2_events_v1.jsonl.gz
                + settlement_v1.jsonl  (RN1 only)
Both normalise into the same internal fill row, so RN1's number from the
snapshot and RN1's number from the Actions pull are computed by the same
code and can be reconciled against each other.

THE MATCHED/RESIDUAL SPLIT IS NOT REIMPLEMENTED. It reuses
backend/sportsassets/analytics/merge_pnl.py::replay verbatim.

EVERY WHALE STATISTIC HERE IS A REFERENCE_ACCOUNT_* STATISTIC
(directive instruction 9). A completion rate measured on a whale's own
fills is what THAT ACCOUNT achieved with its own orders, latency and
queue position. It is never a BETTOR fill probability and this file
never emits one. The prefix is applied in the output keys so the
distinction survives being copied into a later document.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

DATA_API = "https://data-api.polymarket.com/activity?type=TRADE"

# Directive section 5, verbatim.
HORIZONS_S = (5, 10, 30, 60, 120, 300, 600, 1800, 3600)
CEILINGS = (0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99, 1.00)
PRICE_BANDS = ((0.0, 0.10), (0.10, 0.30), (0.30, 0.50),
               (0.50, 0.70), (0.70, 0.90), (0.90, 1.01))
SIZE_BUCKETS = ((0, 100), (100, 1000), (1000, 10000),
                (10000, 100000), (100000, float("inf")))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load("merge_pnl",
          ROOT / "backend" / "sportsassets" / "analytics" / "merge_pnl.py")


# ------------------------------------------------------------- loading --
def _epoch(ts) -> float | None:
    """Unix seconds from whatever the source wrote."""
    if ts in (None, ""):
        return None
    s = str(ts)
    try:
        return float(s) if s.replace(".", "", 1).isdigit() else \
            datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError):
        return None


def load_csv(path: Path, meta: Path | None) -> tuple[list, dict, Counter]:
    """reference_pull.py output. Dedupe is re-applied here on the SAME key
    the puller used, so a duplicate is counted rather than assumed away."""
    why = Counter()
    fills, seen = [], set()
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            why["ROWS"] += 1
            key = (r.get("tx_hash"), r.get("asset_id"), r.get("side"),
                   r.get("price"), r.get("size"), r.get("timestamp"))
            if key in seen:
                why["DUPLICATE_FILL_KEY"] += 1
                continue
            seen.add(key)
            cid = r.get("condition_id") or ""
            try:
                idx = int(float(r.get("outcome_index")))
            except (TypeError, ValueError):
                idx = -1
            t = _epoch(r.get("timestamp"))
            try:
                size, price = float(r["size"]), float(r["price"])
            except (TypeError, ValueError, KeyError):
                why["DROP_UNPARSEABLE_NUMBER"] += 1
                continue
            if not cid:
                why["DROP_NO_CONDITION"] += 1
                continue
            if idx not in (0, 1):
                why["DROP_LEG_NOT_IDENTIFIED"] += 1
                continue
            if size <= 0 or t is None:
                why["DROP_NON_POSITIVE_OR_UNTIMED"] += 1
                continue
            fills.append({
                "condition_id": cid, "outcome_index": idx, "size": size,
                "price": price, "side": (r.get("side") or "BUY").upper(),
                "t": t, "tx": r.get("tx_hash") or "",
                "slug": r.get("market_slug") or "",
                "question": r.get("market_question") or "",
            })
            why["KEPT"] += 1

    pay = {}
    if meta and meta.exists():
        with open(meta, newline="") as fh:
            for r in csv.DictReader(fh):
                cid = r.get("condition_id")
                raw = r.get("outcome_prices")
                if not cid or not raw:
                    continue
                try:
                    v = json.loads(raw)
                    v = [float(x) for x in v]
                except (ValueError, TypeError):
                    why["PAYOUT_NOT_PARSEABLE"] += 1
                    continue
                if len(v) == 2 and sorted(v) == [0.0, 1.0]:
                    pay[cid] = v
                    why["PAYOUT_ONE_HOT"] += 1
                else:
                    why["PAYOUT_NOT_ONE_HOT"] += 1
    fills.sort(key=lambda f: (f["t"], f["tx"]))
    return fills, pay, why


def load_u2() -> tuple[list, dict, Counter]:
    """The retained RN1 snapshot. Same normalisation, same sort."""
    why = Counter()
    fills = []
    src = ROOT / "research" / "snapshots" / "u2_events_v1.jsonl.gz"
    for line in gzip.open(src, "rt"):
        r = json.loads(line)
        why["ROWS"] += 1
        cid = r.get("condition_id_effective") or r.get("condition_id")
        try:
            idx = int(r.get("outcome_index"))
        except (TypeError, ValueError):
            idx = -1
        if not cid:
            why["DROP_NO_CONDITION"] += 1
            continue
        if idx not in (0, 1):
            why["DROP_LEG_NOT_IDENTIFIED"] += 1
            continue
        t = _epoch(r.get("ts"))
        try:
            size, price = float(r.get("size") or 0), float(r.get("price") or 0)
        except (TypeError, ValueError):
            why["DROP_UNPARSEABLE_NUMBER"] += 1
            continue
        if size <= 0 or t is None:
            why["DROP_NON_POSITIVE_OR_UNTIMED"] += 1
            continue
        fills.append({
            "condition_id": cid, "outcome_index": idx, "size": size,
            "price": price, "side": (r.get("side") or "BUY").upper(),
            "t": t, "tx": str(r.get("trade_id") or ""),
            "slug": r.get("market_slug") or "",
            "question": r.get("sport") or "",
        })
        why["KEPT"] += 1

    pay = {}
    with open(ROOT / "research" / "snapshots" / "settlement_v1.jsonl") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            cid = r.get("condition_id")
            if not cid or not r.get("resolved"):
                continue
            try:
                v = [float(x) for x in (r.get("payouts") or [])]
            except (TypeError, ValueError):
                continue
            if len(v) == 2 and sorted(v) == [0.0, 1.0]:
                pay[cid] = v
                why["PAYOUT_ONE_HOT"] += 1
            else:
                why["PAYOUT_NOT_ONE_HOT"] += 1
    fills.sort(key=lambda f: (f["t"], f["tx"]))
    return fills, pay, why


# -------------------------------------------------------------- census --
def census(wallet: str, address: str, source: str, fills: list,
           why: Counter, requested: str | None) -> dict:
    """Directive instruction 2, field for field. Coverage limitations are
    stated as facts about THIS pull, never as a completeness claim."""
    ts = [f["t"] for f in fills]
    txs = {f["tx"] for f in fills if f["tx"]}
    sides = Counter(f["side"] for f in fills)
    return {
        "REFERENCE_ACCOUNT": wallet,
        "WALLET_ADDRESS": address,
        "SOURCE_ENDPOINT": DATA_API if source == "csv"
        else "research/snapshots/u2_events_v1.jsonl.gz (retained snapshot)",
        "EXTRACTION_UTC": datetime.now(timezone.utc).isoformat(),
        "REQUESTED_RANGE_START": requested or "NOT_IDENTIFIED",
        "RETURNED_RANGE_START": datetime.fromtimestamp(
            min(ts), timezone.utc).isoformat() if ts else None,
        "RETURNED_RANGE_END": datetime.fromtimestamp(
            max(ts), timezone.utc).isoformat() if ts else None,
        "RETURNED_SPAN_DAYS": round((max(ts) - min(ts)) / 86400.0, 2)
        if ts else None,
        "ROWS_READ": why.get("ROWS", 0),
        "ROWS_KEPT": why.get("KEPT", 0),
        "UNIQUE_TRADE_IDS": len(txs),
        "DUPLICATE_FILL_KEYS": why.get("DUPLICATE_FILL_KEY", 0),
        "BUY_COUNT": sides.get("BUY", 0),
        "SELL_COUNT": sides.get("SELL", 0),
        "OTHER_SIDE_COUNT": sum(v for k, v in sides.items()
                                if k not in ("BUY", "SELL")),
        "DISTINCT_CONDITIONS": len({f["condition_id"] for f in fills}),
        "DISTINCT_MARKET_SLUGS": len({f["slug"] for f in fills if f["slug"]}),
        "DROP_CENSUS": {k: v for k, v in why.items()
                        if k.startswith(("DROP_", "PAYOUT_", "DUPLICATE"))},
        "COVERAGE_LIMITATIONS": [
            "A windowed-cursor pull is not a proof of lifetime completeness; "
            "it is what this request returned.",
            "Rows without a condition id or a resolvable leg are EXCLUDED "
            "and counted in DROP_CENSUS, never silently skipped.",
            "Settlement is only available for markets present in the meta "
            "pull; unresolved and non-one-hot payouts are excluded, not "
            "treated as losses.",
        ],
    }


# -------------------------------- the pair channel, split by open band --
def merge_pnl_by_open_band(fills: list) -> dict:
    """BETA48_DATA_GATE's NEXT_DECISIVE_EXPERIMENT: split the PAIR CHANNEL
    by a decision-time bucket, and reconcile it to the estimator exactly.

    WHY THIS EXISTS AND completion_grid DOES NOT ANSWER IT. The first
    attempt attributed pair economics from completion_grid, which walks
    only the FIRST complement of an OPENING leg. Measured against the
    retained RN1 snapshot that reproduced $58,394 of a $223,094
    realized_merge_pnl -- 26%. A leg built from many fills does most of
    its merging in events that grid never looks at, so a per-band table
    built from it would attribute a quarter of the money while reading
    like the whole of it.

    WHAT THIS DOES INSTEAD. It walks the fills with merge_pnl.step's own
    balance/cost accounting -- a BUY meeting an opposing balance closes
    m = min(size, bal[other]) shares and realises
    m * (1 - avg_cost_of_held_leg - price_paid_now) -- and additionally
    remembers the price at which each leg was OPENED from flat. Every
    merge is then booked to the band of the HELD leg's opening price.

    That price is a decision-time fact: it is known when the position is
    taken, before any of the pair economics are determined. It is the
    bucket the directive asked for.

    THE GUARANTEE. Summed over bands this equals realized_merge_pnl to
    the cent, because it is the same arithmetic over the same events in
    the same order -- only labelled. reconciles_to_estimator carries the
    residual so the claim is checkable in the sealed output rather than
    taken on faith. It reads nothing the estimator does not read, and it
    does not touch the estimator.
    """
    by_cond: dict[str, list] = defaultdict(list)
    for f in fills:
        by_cond[f["condition_id"]].append(f)

    bands: dict = defaultdict(lambda: {"merges": 0, "shares": 0.0,
                                       "stake": 0.0, "pnl": 0.0})
    total = 0.0
    DUST = 1e-9

    def band_of(p):
        for lo, hi in PRICE_BANDS:
            if lo <= p < hi:
                return "%.2f-%.2f" % (lo, hi)
        return "NOT_IDENTIFIED"

    for cid, rows in by_cond.items():
        bal, cost = [0.0, 0.0], [0.0, 0.0]
        opened_at: list = [None, None]      # price each leg was opened from flat
        for f in rows:
            idx = f["outcome_index"]
            other = 1 - idx
            if f["side"] == "SELL":
                q = min(f["size"], bal[idx])
                if q > DUST:
                    avg = cost[idx] / bal[idx] if bal[idx] > DUST else 0.0
                    bal[idx] -= q
                    cost[idx] -= q * avg
                    if bal[idx] <= DUST:
                        opened_at[idx] = None
                continue
            m = min(f["size"], bal[other])
            if m > DUST:
                avg_other = (cost[other] / bal[other]
                             if bal[other] > DUST else 0.0)
                pnl = m * (1.0 - avg_other - f["price"])
                key = band_of(opened_at[other]
                              if opened_at[other] is not None else avg_other)
                e = bands[key]
                e["merges"] += 1
                e["shares"] += m
                e["stake"] += m * avg_other
                e["pnl"] += pnl
                total += pnl
                bal[other] -= m
                cost[other] -= m * avg_other
                if bal[other] <= DUST:
                    opened_at[other] = None
            entry = f["size"] - m
            if entry > DUST:
                if bal[idx] <= DUST:
                    opened_at[idx] = f["price"]
                bal[idx] += entry
                cost[idx] += entry * f["price"]

    out = {}
    for k in sorted(bands):
        e = bands[k]
        out[k] = {
            "merges": e["merges"],
            "merged_shares": round(e["shares"], 2),
            "MATCHED_STAKE": round(e["stake"], 2),
            "MATCHED_PNL": round(e["pnl"], 2),
            "MATCHED_ROI": round(e["pnl"] / e["stake"], 6)
            if e["stake"] > 0 else None,
        }
    out["_TOTAL_MATCHED_PNL"] = round(total, 2)
    out["_BUCKET"] = ("the HELD leg's opening price -- a decision-time fact, "
                      "known before the pair economics are determined")
    return out


def pnl_by_open_band(fills: list, payouts: dict) -> dict:
    """ALL THREE CHANNELS by the same open band, reconciling to lot_p.

    WHY THIS IS NECESSARY AND merge_pnl_by_open_band IS NOT SUFFICIENT.
    The merge split answers "what did the PAIR channel earn in this
    band?" exactly. It cannot answer "was opening in this band
    profitable?", and those are different questions whenever legs opened
    in a band do not all get paired -- which is exactly the case here:
    28% to 81% of the cheapest opens NEVER pair, even by settlement.

    A leg opened at 5c and paired at basis 0.94 books a certain profit
    into the merge channel. The leg opened at 5c that never finds a
    complement is a naked longshot that usually settles at zero, and its
    loss lands in the SETTLED channel where the merge split cannot see
    it. Reading the merge split alone therefore has a built-in
    survivorship bias in favour of low bands, because low bands are
    precisely where pairing fails most often. The unanimous positive
    sign of the sub-0.30 bands could be entirely an artefact of that.

    So this books EVERY closed lot merge_pnl.replay books, to the band
    the leg was OPENED in:

      * MERGE   -- a BUY meeting an opposing balance (merge_pnl.py:318)
      * SELL    -- a SELL against a held balance (merge_pnl.py:305)
      * SETTLED -- a resolved-but-never-closed balance (merge_pnl.py:390)

    and the three sum, per band, to that band's TOTAL. Summed over
    bands, stake equals lot_s and pnl equals lot_p, so the reconciliation
    target is the estimator's own closed-lot totals rather than the
    merge subtotal. Ungraded balances are reported separately and are
    NOT treated as losses -- guessing an unresolved outcome would invent
    the number this exists to check.

    The standing rule that headline edge_roi may never stand in for pair
    economics applies here in the other direction too: a band's MERGE
    economics may never stand in for that band's TOTAL economics.
    """
    by_cond: dict = defaultdict(list)
    for f in fills:
        by_cond[f["condition_id"]].append(f)

    def blank():
        return {"merge_stake": 0.0, "merge_pnl": 0.0, "merges": 0,
                "sell_stake": 0.0, "sell_pnl": 0.0, "sells": 0,
                "settled_stake": 0.0, "settled_pnl": 0.0, "settled_lots": 0,
                "ungraded_stake": 0.0, "ungraded_shares": 0.0}

    bands: dict = defaultdict(blank)
    DUST = 1e-9

    def band_of(p):
        for lo, hi in PRICE_BANDS:
            if lo <= p < hi:
                return "%.2f-%.2f" % (lo, hi)
        return "NOT_IDENTIFIED"

    def key_for(opened, avg):
        return band_of(opened if opened is not None else avg)

    for cid, rows in by_cond.items():
        bal, cost = [0.0, 0.0], [0.0, 0.0]
        opened_at: list = [None, None]
        for f in rows:
            idx = f["outcome_index"]
            other = 1 - idx
            if f["side"] == "SELL":
                q = min(f["size"], bal[idx])
                if q > DUST:
                    avg = cost[idx] / bal[idx] if bal[idx] > DUST else 0.0
                    e = bands[key_for(opened_at[idx], avg)]
                    e["sells"] += 1
                    e["sell_stake"] += q * avg
                    e["sell_pnl"] += q * (f["price"] - avg)
                    bal[idx] -= q
                    cost[idx] -= q * avg
                    if bal[idx] <= DUST:
                        opened_at[idx] = None
                continue
            m = min(f["size"], bal[other])
            if m > DUST:
                avg_other = (cost[other] / bal[other]
                             if bal[other] > DUST else 0.0)
                e = bands[key_for(opened_at[other], avg_other)]
                e["merges"] += 1
                e["merge_stake"] += m * avg_other
                e["merge_pnl"] += m * (1.0 - avg_other - f["price"])
                bal[other] -= m
                cost[other] -= m * avg_other
                if bal[other] <= DUST:
                    opened_at[other] = None
            entry = f["size"] - m
            if entry > DUST:
                if bal[idx] <= DUST:
                    opened_at[idx] = f["price"]
                bal[idx] += entry
                cost[idx] += entry * f["price"]

        # Whatever is still held when the fills run out, booked exactly
        # as merge_pnl.finish books it.
        v = payouts.get(cid)
        for leg in (0, 1):
            q = bal[leg]
            if q <= DUST:
                continue
            avg = cost[leg] / q if q > DUST else 0.0
            e = bands[key_for(opened_at[leg], avg)]
            po = None
            if isinstance(v, (list, tuple)) and leg < len(v):
                try:
                    po = float(v[leg])
                except (TypeError, ValueError):
                    po = None
            if po is None:
                e["ungraded_stake"] += cost[leg]
                e["ungraded_shares"] += q
                continue
            e["settled_lots"] += 1
            e["settled_stake"] += cost[leg]
            e["settled_pnl"] += q * po - cost[leg]

    out, tot_s, tot_p = {}, 0.0, 0.0
    for k in sorted(bands):
        e = bands[k]
        stake = e["merge_stake"] + e["sell_stake"] + e["settled_stake"]
        pnl = e["merge_pnl"] + e["sell_pnl"] + e["settled_pnl"]
        tot_s += stake
        tot_p += pnl
        out[k] = {
            "merges": e["merges"],
            "MERGE_STAKE": round(e["merge_stake"], 2),
            "MERGE_PNL": round(e["merge_pnl"], 2),
            "MERGE_ROI": (round(e["merge_pnl"] / e["merge_stake"], 6)
                          if e["merge_stake"] > 0 else None),
            "sells": e["sells"],
            "SELL_STAKE": round(e["sell_stake"], 2),
            "SELL_PNL": round(e["sell_pnl"], 2),
            "settled_lots": e["settled_lots"],
            "SETTLED_STAKE": round(e["settled_stake"], 2),
            "SETTLED_PNL": round(e["settled_pnl"], 2),
            "SETTLED_ROI": (round(e["settled_pnl"] / e["settled_stake"], 6)
                            if e["settled_stake"] > 0 else None),
            "TOTAL_STAKE": round(stake, 2),
            "TOTAL_PNL": round(pnl, 2),
            "TOTAL_ROI": round(pnl / stake, 6) if stake > 0 else None,
            "UNGRADED_STAKE": round(e["ungraded_stake"], 2),
            "UNGRADED_SHARES": round(e["ungraded_shares"], 2),
        }
    out["_TOTAL_STAKE"] = round(tot_s, 2)
    out["_TOTAL_PNL"] = round(tot_p, 2)
    out["_BUCKET"] = ("the leg's OPENING price -- a decision-time fact. "
                      "Every closed lot merge_pnl.replay books is booked "
                      "here, so summing bands gives lot_s / lot_p, not the "
                      "merge subtotal.")
    return out


# --------------------------------------------- ceiling x horizon grid --
def completion_grid(fills: list) -> dict:
    """REFERENCE_ACCOUNT complement-completion, directive section 5.

    THE UNIT IS A FIRST-SIDE ACQUISITION. Walking each market
    chronologically, a BUY that adds shares to a leg while the OPPOSING
    leg is flat opens unpaired inventory. From that instant we look
    FORWARD for the first buy of the other leg and ask, at each horizon,
    whether the pair basis (first price + complement price) came in at or
    under each ceiling.

    LEAKAGE. The scan is forward-only from the decision instant and uses
    no settlement, no later price and no knowledge of whether the pair
    eventually completed. The horizon/ceiling answer for horizon H is a
    fact about [t, t+H] and nothing after it.

    WHAT IT IS NOT. This is what the REFERENCE ACCOUNT achieved with its
    own orders. It is not BETTOR_PASSIVE_FILL_PROBABILITY.
    """
    by_cond: dict[str, list] = defaultdict(list)
    for f in fills:
        by_cond[f["condition_id"]].append(f)

    # grid[horizon][ceiling] -> completed count ; opens -> denominator
    grid = {h: Counter() for h in HORIZONS_S}
    grid["settlement"] = Counter()
    opens = 0
    basis_sum = basis_n = 0.0
    never = 0
    by_band: dict = defaultdict(lambda: {"opens": 0, "done_1h": 0,
                                         "basis_sum": 0.0, "basis_n": 0})
    by_sport: dict = defaultdict(lambda: {"opens": 0, "done_1h": 0,
                                          "basis_sum": 0.0, "basis_n": 0})
    by_size: dict = defaultdict(lambda: {"opens": 0, "done_1h": 0,
                                         "basis_sum": 0.0, "basis_n": 0})
    by_week: dict = defaultdict(lambda: {"opens": 0, "done_1h": 0,
                                         "basis_sum": 0.0, "basis_n": 0})

    # PER-BAND ECONOMICS (BETA48_DATA_GATE's NEXT_DECISIVE_EXPERIMENT).
    #
    # The sealed grids carried opens / completion / mean basis per band and
    # NO P&L, so the 3/3 pair-channel sign split could not be attributed to
    # a decision-time bucket. These accumulate the missing economics.
    #
    # THE ATTRIBUTION, stated plainly because it is an approximation.
    # A completed pair returns exactly $1, so the pair bought at `basis`
    # earns (1 - basis) per share. We attribute
    #
    #     pair_pnl = matched_size * (1 - basis)
    #
    # to the FIRST LEG'S band, where matched_size is min(first, complement).
    # This is NOT merge_pnl's own number: merge_pnl closes against the
    # held leg's AVERAGE cost across every fill on that leg, whereas this
    # prices the single first fill that opened the position. The two agree
    # when a leg is opened and closed in one pair and diverge when a leg is
    # built from many fills. band_pnl_total is emitted alongside
    # realized_merge_pnl precisely so the size of that divergence is
    # visible rather than assumed, and the per-band split must be read as
    # an attribution of the pair economics, not as a restatement of the
    # estimator.
    band_pnl: dict = defaultdict(lambda: {"pairs": 0, "matched_shares": 0.0,
                                          "matched_stake": 0.0,
                                          "pair_pnl": 0.0, "done_5s": 0,
                                          "done_settle": 0, "bases": []})

    def band_of(p: float) -> str:
        for lo, hi in PRICE_BANDS:
            if lo <= p < hi:
                return "%.2f-%.2f" % (lo, hi)
        return "NOT_IDENTIFIED"

    def size_of(s: float) -> str:
        for lo, hi in SIZE_BUCKETS:
            if lo <= s < hi:
                return "%d-%s" % (lo, "inf" if hi == float("inf") else int(hi))
        return "NOT_IDENTIFIED"

    for cid, rows in by_cond.items():
        bal = [0.0, 0.0]
        for i, f in enumerate(rows):
            idx = f["outcome_index"]
            other = 1 - idx
            if f["side"] == "SELL":
                bal[idx] = max(0.0, bal[idx] - f["size"])
                continue
            m = min(f["size"], bal[other])
            entry = f["size"] - m
            bal[other] -= m
            opening = entry > 0 and bal[other] <= 1e-9 and bal[idx] <= 1e-9
            bal[idx] += entry
            if not opening:
                continue
            opens += 1
            p0, t0 = f["price"], f["t"]
            band, sport = band_of(p0), (f["question"] or "NOT_IDENTIFIED")[:24]
            sz, wk = size_of(f["size"]), datetime.fromtimestamp(
                t0, timezone.utc).strftime("%G-W%V")
            for d in (by_band[band], by_sport[sport], by_size[sz],
                      by_week[wk]):
                d["opens"] += 1
            # forward scan for the first complement
            comp = None
            for g in rows[i + 1:]:
                if g["outcome_index"] == other and g["side"] == "BUY":
                    comp = g
                    break
            if comp is None:
                never += 1
                continue
            dt = comp["t"] - t0
            basis = p0 + comp["price"]
            basis_sum += basis
            basis_n += 1
            for d, key in ((by_band[band], band), (by_sport[sport], sport),
                           (by_size[sz], sz), (by_week[wk], wk)):
                d["basis_sum"] += basis
                d["basis_n"] += 1
                if dt <= 3600:
                    d["done_1h"] += 1
            # The pair's own economics, attributed to the first leg's band.
            e = band_pnl[band]
            msize = min(f["size"], comp["size"])
            e["pairs"] += 1
            e["matched_shares"] += msize
            e["matched_stake"] += msize * p0
            e["pair_pnl"] += msize * (1.0 - basis)
            e["bases"].append(basis)
            if dt <= 5:
                e["done_5s"] += 1
            e["done_settle"] += 1      # completed at all == completed by settlement
            for h in HORIZONS_S:
                if dt <= h:
                    for c in CEILINGS:
                        if basis <= c + 1e-12:
                            grid[h][c] += 1
                    grid[h]["ANY_BASIS"] += 1
            for c in CEILINGS:
                if basis <= c + 1e-12:
                    grid["settlement"][c] += 1
            grid["settlement"]["ANY_BASIS"] += 1

    def pct(sorted_vals, q):
        """Nearest-rank percentile. No numpy on this path by design."""
        if not sorted_vals:
            return None
        i = min(len(sorted_vals) - 1,
                max(0, int(round(q * (len(sorted_vals) - 1)))))
        return sorted_vals[i]

    def rate(d, band_key=None):
        out = {
            "opens": d["opens"],
            "completed_within_1h": d["done_1h"],
            "completion_rate_1h": round(d["done_1h"] / d["opens"], 4)
            if d["opens"] else None,
            "mean_pair_basis": round(d["basis_sum"] / d["basis_n"], 5)
            if d["basis_n"] else None,
        }
        # The four keys above are unchanged and must stay so: the sealed
        # run 35028887477 grids are compared against these.
        if band_key is None or band_key not in band_pnl:
            return out
        e = band_pnl[band_key]
        b = sorted(e["bases"])
        out.update({
            "completed_within_5s": e["done_5s"],
            "completion_rate_5s": round(e["done_5s"] / d["opens"], 4)
            if d["opens"] else None,
            "completed_by_settlement": e["done_settle"],
            "completion_rate_settlement": round(e["done_settle"] / d["opens"], 4)
            if d["opens"] else None,
            "residual_rate": round(1.0 - e["done_settle"] / d["opens"], 4)
            if d["opens"] else None,
            "median_pair_basis": round(pct(b, 0.50), 5) if b else None,
            "p25_pair_basis": round(pct(b, 0.25), 5) if b else None,
            "p75_pair_basis": round(pct(b, 0.75), 5) if b else None,
            "first_complement_shares": round(e["matched_shares"], 2),
            "first_complement_stake": round(e["matched_stake"], 2),
            "first_complement_pnl": round(e["pair_pnl"], 2),
            "_NOT_THE_PAIR_CHANNEL": "first_complement_* covers ONLY the first "
                                     "complement of an OPENING leg and on RN1 "
                                     "reproduces just 26% of realized_merge_pnl. "
                                     "The pair channel's per-band economics are "
                                     "MERGE_PNL_BY_OPEN_BAND, which reconciles "
                                     "to the cent. Do not read these as MATCHED_PNL.",
        })
        return out

    return {
        "REFERENCE_ACCOUNT_FIRST_SIDE_ACQUISITIONS": opens,
        "REFERENCE_ACCOUNT_NEVER_COMPLETED": never,
        "REFERENCE_ACCOUNT_MEAN_PAIR_BASIS": round(basis_sum / basis_n, 5)
        if basis_n else None,
        "REFERENCE_ACCOUNT_COMPLETION_GRID": {
            str(h): {("ceiling_%.2f" % c): {
                "completed": grid[h][c],
                "rate": round(grid[h][c] / opens, 5) if opens else None}
                for c in CEILINGS}
            | {"any_basis_completed": grid[h]["ANY_BASIS"],
               "any_basis_rate": round(grid[h]["ANY_BASIS"] / opens, 5)
               if opens else None}
            for h in list(HORIZONS_S) + ["settlement"]},
        "BY_FIRST_LEG_PRICE_BAND": {k: rate(v, k) for k, v in
                                    sorted(by_band.items())},
        # First-complement-only totals. Kept because the completion rates
        # and basis percentiles above are computed over exactly this
        # subset, so a reader needs its size. NOT the pair channel: on the
        # retained RN1 snapshot this is $58,394 against a $223,094
        # realized_merge_pnl. MERGE_PNL_BY_OPEN_BAND is the pair channel.
        "FIRST_COMPLEMENT_PNL_TOTAL": round(
            sum(e["pair_pnl"] for e in band_pnl.values()), 2),
        "FIRST_COMPLEMENT_STAKE_TOTAL": round(
            sum(e["matched_stake"] for e in band_pnl.values()), 2),
        "BY_SPORT_OR_QUESTION": dict(sorted(
            ((k, rate(v)) for k, v in by_sport.items()),
            key=lambda kv: -kv[1]["opens"])[:25]),
        "BY_FILL_SIZE_BUCKET": {k: rate(v) for k, v in
                                sorted(by_size.items())},
        "BY_WEEK": {k: rate(v) for k, v in sorted(by_week.items())},
    }


# ------------------------------------------------------------- windows --
# merge_pnl computes a cluster-robust interval but does not gate on how
# many clusters produced it. proof.py sets MIN_PROOF_CLUSTERS = 30 for
# exactly this reason and merge_pnl does not import it.
#
# THE CANARY THAT FOUND THIS. The kch123 extraction returned a LAST_7D
# window of 5 closed lots in 5 clusters, and the estimator reported
# "PROFITABLE at 95% -- +47.06%, interval [+47.05%, +47.07%]": an
# interval 0.02pp wide on five observations. At that n the ratio
# estimator's variance collapses and the verdict is noise wearing a
# significance label. LAST_14D on the same account returned "LOSING at
# 95%" on 17 lots.
#
# So the verdict is SUPPRESSED below the threshold rather than quoted.
# The point estimate and the interval are still reported -- they are
# facts about the window -- but the word PROFITABLE is not available to
# a sample that cannot support it.
MIN_VERDICT_CLUSTERS = 30
INSUFFICIENT = "NOT_DEMONSTRATED_INSUFFICIENT_CLUSTERS"


def gate_verdict(r: dict) -> dict:
    n = r.get("edge_clusters") or 0
    if n < MIN_VERDICT_CLUSTERS:
        r["edge_verdict_raw"] = r.get("edge_verdict")
        r["edge_verdict"] = ("%s (%s clusters < %d; the estimator's own "
                             "verdict is retained as edge_verdict_raw and "
                             "must not be quoted)"
                             % (INSUFFICIENT, n, MIN_VERDICT_CLUSTERS))
    return r


def window(fills: list, days: int | None, as_of: float | None = None) -> list:
    """The last `days` before AS_OF -- a common wall-clock reference, not
    the account's own last fill.

    WHY THIS MATTERS AND WHY IT WAS WRONG. Anchoring to max(f["t"]) makes
    "LAST_7D" mean "the last seven days THIS ACCOUNT traded". kch123's
    pull ends 2026-06-29, so its "current regime" was late June while
    RN1's was mid-September. Windows anchored that way are not
    comparable across accounts and are not the current regime, which is
    the one thing the directive says controls deployment decisions.
    """
    if days is None or not fills:
        return fills
    ref = as_of if as_of is not None else max(f["t"] for f in fills)
    return [f for f in fills if f["t"] >= ref - days * 86400]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("csv", "u2"), required=True)
    ap.add_argument("--wallet", required=True)
    ap.add_argument("--address", default="NOT_IDENTIFIED")
    ap.add_argument("--path")
    ap.add_argument("--meta")
    ap.add_argument("--requested-start")
    # A COMMON wall-clock anchor for every current-regime window, so
    # "last 7 days" means the same seven days for every account. Default
    # is now. Never the account's own last fill.
    ap.add_argument("--as-of", default=None,
                    help="ISO8601 or unix seconds; default now(UTC)")
    ap.add_argument("--out", default=str(HERE / "evidence" / "whales"))
    a = ap.parse_args(argv)
    as_of = _epoch(a.as_of) if a.as_of else datetime.now(
        timezone.utc).timestamp()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.source == "csv":
        fills, pay, why = load_csv(Path(a.path),
                                   Path(a.meta) if a.meta else None)
    else:
        fills, pay, why = load_u2()

    cen = census(a.wallet, a.address, a.source, fills, why, a.requested_start)
    cen["AS_OF_UTC"] = datetime.fromtimestamp(as_of, timezone.utc).isoformat()
    cen["DAYS_SINCE_LAST_FILL_AT_AS_OF"] = round(
        (as_of - max(f["t"] for f in fills)) / 86400.0, 2) if fills else None
    print(json.dumps(cen, indent=1), flush=True)
    if not fills:
        (out / f"{a.wallet}_census.json").write_text(json.dumps(cen, indent=1))
        print("NO FILLS -- census sealed, nothing reconstructed", flush=True)
        return 2

    # Canonical matched/residual accounting, lifetime and current regime.
    drop = ("rows", "clus")
    replays = {}
    for label, days in (("LIFETIME", None), ("LAST_60D", 60),
                        ("LAST_30D", 30), ("LAST_14D", 14), ("LAST_7D", 7)):
        sub = window(fills, days, as_of)
        if not sub:
            replays[label] = {"_window_fills": 0,
                              "_window_days_requested": days,
                              "edge_verdict": "NO_FILLS_IN_WINDOW"}
            print("%-10s NO FILLS IN WINDOW" % label, flush=True)
            continue
        r = gate_verdict(M.replay(sub, payouts=pay))
        replays[label] = {k: v for k, v in r.items() if k not in drop}
        replays[label]["_window_fills"] = len(sub)
        replays[label]["_window_days_requested"] = days
        print("%-10s fills=%-8d merges=%-7s merge_pnl=%-14s verdict=%s"
              % (label, len(sub), r.get("n_merges"),
                 round(r.get("realized_merge_pnl") or 0, 2),
                 r.get("edge_verdict")), flush=True)

    grids = {label: completion_grid(window(fills, days, as_of))
             for label, days in (("LIFETIME", None), ("LAST_30D", 30),
                                 ("LAST_7D", 7))}

    # The pair channel split by the held leg's opening band, with the
    # residual against the estimator carried in the output so the
    # reconciliation is checkable from the sealed file alone.
    open_bands = {}
    for label, days in (("LIFETIME", None), ("LAST_30D", 30), ("LAST_7D", 7)):
        sub = window(fills, days, as_of)
        if not sub:
            open_bands[label] = "NO_FILLS_IN_WINDOW"
            continue
        b = merge_pnl_by_open_band(sub)
        est = round(replays[label].get("realized_merge_pnl") or 0.0, 2)
        b["_REALIZED_MERGE_PNL"] = est
        b["_RESIDUAL_VS_ESTIMATOR"] = round(b["_TOTAL_MATCHED_PNL"] - est, 2)
        b["_RECONCILES"] = abs(b["_TOTAL_MATCHED_PNL"] - est) < 0.01
        open_bands[label] = b
        print("%-10s band-split %s  residual $%s" % (
            label, "RECONCILES" if b["_RECONCILES"] else "DOES NOT RECONCILE",
            b["_RESIDUAL_VS_ESTIMATOR"]), flush=True)

    # ALL THREE CHANNELS by the same band. The merge split above answers
    # "what did the PAIR channel earn in this band?"; this answers "was
    # OPENING in this band profitable?", which is the question a BETTOR
    # rule would actually be built on. They differ wherever legs opened
    # in a band go unpaired -- and 28% to 81% of the cheapest opens never
    # pair, which is precisely where the merge-only view is most
    # flattering. Reconciled against the estimator's own closed-lot
    # totals (lot_s / lot_p), not against the merge subtotal.
    all_bands = {}
    for label, days in (("LIFETIME", None), ("LAST_30D", 30), ("LAST_7D", 7)):
        sub = window(fills, days, as_of)
        if not sub:
            all_bands[label] = "NO_FILLS_IN_WINDOW"
            continue
        b = pnl_by_open_band(sub, pay)
        rep = replays[label]
        ls, lp = rep.get("lot_s"), rep.get("lot_p")
        b["_ESTIMATOR_LOT_STAKE"] = ls
        b["_ESTIMATOR_LOT_PNL"] = lp
        b["_RESIDUAL_STAKE"] = (round(b["_TOTAL_STAKE"] - ls, 2)
                                if ls is not None else None)
        b["_RESIDUAL_PNL"] = (round(b["_TOTAL_PNL"] - lp, 2)
                              if lp is not None else None)
        b["_RECONCILES"] = (ls is not None and lp is not None
                            and abs(b["_TOTAL_STAKE"] - ls) < 0.01
                            and abs(b["_TOTAL_PNL"] - lp) < 0.01)
        all_bands[label] = b
        print("%-10s 3-channel %s  stake residual $%s  pnl residual $%s" % (
            label, "RECONCILES" if b["_RECONCILES"] else "DOES NOT RECONCILE",
            b["_RESIDUAL_STAKE"], b["_RESIDUAL_PNL"]), flush=True)

    payload = {"CENSUS": cen,
               "REFERENCE_ACCOUNT_REPLAY": replays,
               "REFERENCE_ACCOUNT_COMPLETION": grids,
               "MERGE_PNL_BY_OPEN_BAND": open_bands,
               "PNL_BY_OPEN_BAND_ALL_CHANNELS": all_bands}
    blob = json.dumps(payload, indent=1, default=str)
    (out / f"{a.wallet}_reconstruction.json").write_text(blob)
    (out / f"{a.wallet}_census.json").write_text(json.dumps(cen, indent=1))
    print("SHA256 %s  %s_reconstruction.json"
          % (hashlib.sha256(blob.encode()).hexdigest(), a.wallet), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
