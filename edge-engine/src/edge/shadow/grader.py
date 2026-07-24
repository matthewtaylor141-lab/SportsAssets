"""Shadow grader — scores logged shadow fills at resolution.

Methodology identical to the Phase 1 study (apples-to-apples): each would-be
fill realizes (payout - price) * shares at settlement; Kalshi-modeled fees
subtracted where applicable. Output judges the capital gate in
config/risk.yaml. Capital decisions come from THIS output, nowhere else.

Resolutions come from Gamma with the measured drift rules (closed=true,
repeated condition_ids, explicit limit).

Run: python -m edge.shadow.grader
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import requests

from edge.execution.engine import Policy
from edge.shadow.runner import _shadow_log_path

GAMMA = "https://gamma-api.polymarket.com"
GRADE_OUT = _shadow_log_path().parent / "shadow_grade.json"


def load_fills() -> list[dict]:
    if not _shadow_log_path().exists():
        return []
    out = []
    with open(_shadow_log_path()) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def fetch_resolutions(condition_ids: list[str]) -> dict[str, dict[str, float]]:
    """condition_id -> {token_id: payout}. Only resolved markets returned."""
    sess = requests.Session()
    out: dict[str, dict[str, float]] = {}
    for i in range(0, len(condition_ids), 40):
        chunk = condition_ids[i : i + 40]
        params = [("condition_ids", c) for c in chunk] + [
            ("limit", str(len(chunk))), ("closed", "true")]
        resp = sess.get(f"{GAMMA}/markets", params=params, timeout=20)
        if resp.status_code != 200:
            continue
        for m in resp.json() or []:
            cid = m.get("conditionId")
            tokens = m.get("clobTokenIds") or "[]"
            prices = m.get("outcomePrices") or "[]"
            tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
            prices = json.loads(prices) if isinstance(prices, str) else prices
            try:
                payouts = [float(p) for p in prices]
            except (TypeError, ValueError):
                continue
            if not payouts or not all(p in (0.0, 1.0) for p in payouts):
                continue
            out[cid] = {str(t): payouts[j] for j, t in enumerate(tokens) if j < len(payouts)}
    return out


def grade() -> dict:
    fills = load_fills()
    if not fills:
        return {"error": "no shadow fills logged yet"}
    pm_ids = sorted({f["market_id"] for f in fills if f["venue"] == "polymarket"})
    resolutions = fetch_resolutions(pm_ids) if pm_ids else {}
    # Kalshi settlements: outcome_id is the market ticker; result yes/no.
    kalshi_tickers = sorted({f["outcome_id"] for f in fills if f["venue"] == "kalshi"})
    if kalshi_tickers:
        from edge.venues.kalshi import KalshiAdapter

        results = KalshiAdapter().fetch_results(kalshi_tickers)
        for f in fills:
            if f["venue"] == "kalshi" and f["outcome_id"] in results:
                resolutions.setdefault(f["market_id"], {})[f["outcome_id"]] = \
                    results[f["outcome_id"]]

    per_venue: dict[str, dict] = defaultdict(
        lambda: {"fills": 0, "settled": 0, "staked": 0.0, "pnl": 0.0,
                 "by_band": defaultdict(float), "by_league": defaultdict(float),
                 "by_alignment": defaultdict(lambda: {"staked": 0.0, "pnl": 0.0})})
    first_ts = min(f["ts"] for f in fills)
    for f in fills:
        v = per_venue[f["venue"]]
        v["fills"] += 1
        payouts = resolutions.get(f["market_id"])
        if payouts is None or f["outcome_id"] not in payouts:
            continue
        price = float(f["limit_price"])
        shares = float(f["size_usd"]) / price if price > 0 else 0.0
        pnl = (payouts[f["outcome_id"]] - price) * shares
        if f["venue"] == "kalshi":
            pnl -= 0.07 * price * (1 - price) * shares  # modeled taker fee
        v["settled"] += 1
        v["staked"] += float(f["size_usd"])
        v["pnl"] += pnl
        v["by_band"][f.get("band", "?")] += pnl
        v["by_league"][f.get("league", "?")] += pnl
        align = f.get("whale_alignment") or {}
        bucket = "whale_aligned" if align.get("same_side") else (
            "whale_opposed" if align.get("opposed") else "solo")
        v["by_alignment"][bucket]["staked"] += float(f["size_usd"])
        v["by_alignment"][bucket]["pnl"] += pnl

    gate = Policy.load().risk.get("shadow_gate", {})
    days = (time.time() - first_ts) / 86400
    report: dict = {"days": round(days, 1), "generated": time.strftime("%Y-%m-%d %H:%M UTC"),
                    "venues": {}}
    for venue, v in per_venue.items():
        roi = v["pnl"] / v["staked"] if v["staked"] > 0 else None
        report["venues"][venue] = {
            "fills": v["fills"], "settled": v["settled"],
            "staked": round(v["staked"], 2), "pnl": round(v["pnl"], 2),
            "roi": round(roi, 4) if roi is not None else None,
            "by_band": {k: round(x, 2) for k, x in sorted(v["by_band"].items())},
            "by_league": {k: round(x, 2) for k, x in
                          sorted(v["by_league"].items(), key=lambda kv: -kv[1])},
            "by_alignment": {
                k: {"staked": round(x["staked"], 2), "pnl": round(x["pnl"], 2),
                    "roi": round(x["pnl"] / x["staked"], 4) if x["staked"] else None}
                for k, x in v["by_alignment"].items()
            },
            "gate": {
                "min_days": days >= float(gate.get("min_days", 60)),
                "min_fills": v["fills"] >= int(gate.get("min_fills_per_venue", 5000)),
                "min_net_roi": roi is not None and roi >= float(gate.get("min_net_roi", 0.015)),
            },
        }
        report["venues"][venue]["gate"]["PASS"] = all(report["venues"][venue]["gate"].values())
    return report


if __name__ == "__main__":
    result = grade()
    GRADE_OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
