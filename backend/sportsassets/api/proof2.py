"""PROOF-2: the decomposed thesis meter (owner order 2026-08-28).

The end-to-end PROOF cohort needs ~15K settled copies to resolve,
because market-outcome variance dominates a per-copy P&L sample. But
the sleeve's edge DECOMPOSES into terms with very different variance:

    sleeve_edge  =  (mix-weighted whale edge, measured on THEIR books)
                  - capture drag   (our fill price vs the whale's own)
                  - fees

and the capture drag is OUTCOME-FREE: for a filled copy, the dollar
difference vs having filled at the whale's own price is exactly
shares x (ours - his) — deterministic the moment the order fills,
independent of how the market resolves. Its CI tightens with hundreds
of fills, not tens of thousands. The whale-edge term comes from the
analytics worker's merge-inclusive per-whale CIs over their FULL
books (tens of thousands of closed lots each), published hourly under
'whale_edge_benchmark'.

This module combines the components into a live estimate of the
sleeve's edge per entry dollar with a CI, then answers the owner's
question directly: at MEASURED dollar flow, what is P(annual profit
>= 100% of principal) across a principal grid — and what flow
multiple the thesis requires. Every number derives from measured
components; the meter moves when the evidence moves.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

# The clean-cohort start the PROOF endpoint uses — execution machinery
# before this date is a different regime and would contaminate drag.
DEFAULT_SINCE = "2026-08-25T14:00:00+00:00"

TARGET_ANNUAL = 1.0            # the owner's thesis: >= 100% / year
PRINCIPAL_GRID = [100_000, 250_000, 500_000, 1_000_000, 2_000_000]
FLOW_MULTIPLES = [1, 5, 10, 30, 50]


def _phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def kalshi_fee(shares: float, price: float) -> float:
    """Kalshi's published taker-fee formula."""
    return 0.07 * shares * price * (1.0 - price)


def capture_from_rows(rows: list[dict]) -> dict:
    """The outcome-free capture term from paired copy rows.

    Each usable row carries the whale's own entry price (his_price)
    and ours (fill_price) for the same fill: drag_i = shares x
    (ours - his). The weighted mean drag rate and its SE come from
    the entry-notional-weighted sample; fees are computed per venue
    (Kalshi's published formula; PM-US currently fee-free on these
    books).
    """
    usable = []
    for r in rows:
        his = r.get("his_price")
        ours = r.get("fill_price")
        sh = r.get("shares")
        if his is None or ours is None or not sh or sh <= 0:
            continue
        if his <= 0 or ours <= 0:
            continue
        usable.append((float(his), float(ours), float(sh),
                       str(r.get("venue") or ""),
                       str(r.get("whale") or "")))
    n = len(usable)
    if n == 0:
        return {"n": 0, "drag_rate": None, "drag_se": None,
                "fee_rate": None, "per_whale_entry": {}}
    tot_his = sum(h * s for h, o, s, v, w in usable)
    drag_usd = sum((o - h) * s for h, o, s, v, w in usable)
    fee_usd = sum(kalshi_fee(s, o) if v.startswith("kalshi") else 0.0
                  for h, o, s, v, w in usable)
    drag_rate = drag_usd / tot_his
    fee_rate = fee_usd / tot_his
    # weighted SE of the drag rate: w_i = his-notional share
    var = 0.0
    for h, o, s, v, w in usable:
        wgt = (h * s) / tot_his
        r_i = (o - h) / h
        var += (wgt ** 2) * ((r_i - drag_rate) ** 2)
    per_whale: dict[str, float] = {}
    for h, o, s, v, w in usable:
        per_whale[w] = per_whale.get(w, 0.0) + h * s
    return {
        "n": n,
        "entry_notional": round(tot_his, 2),
        "drag_usd": round(drag_usd, 2),
        "drag_rate": drag_rate,
        "drag_se": math.sqrt(var),
        "fee_usd": round(fee_usd, 2),
        "fee_rate": fee_rate,
        "per_whale_entry": per_whale,
    }


def combine(capture: dict, benchmark: dict) -> dict:
    """Mix-weighted whale edge minus drag minus fees, with a CI.

    The whale mix is OUR entry-notional mix over the cohort — the
    edge we are actually buying. A rostered whale without a published
    CI contributes edge 0 with se 0 (conservative: it dilutes the
    estimate rather than inflating it) and is named in the payload.
    """
    per_entry = capture.get("per_whale_entry") or {}
    total = sum(per_entry.values())
    pw = (benchmark or {}).get("per_whale") or {}
    if not total or capture.get("drag_rate") is None:
        return {"available": False,
                "reason": "no paired fills in the cohort yet"}
    mix_edge = 0.0
    mix_var = 0.0
    mix = {}
    unpublished = []
    for whale, ent in per_entry.items():
        w = ent / total
        g = None
        for k, v in pw.items():
            if k.lower() == whale.lower():
                g = v
                break
        edge = float((g or {}).get("edge_roi") or 0.0)
        ci = (g or {}).get("edge_ci95") or None
        if ci and isinstance(ci, (list, tuple)) and len(ci) == 2 \
                and ci[0] is not None and ci[1] is not None:
            se = (float(ci[1]) - float(ci[0])) / (2 * 1.96)
        else:
            edge, se = 0.0, 0.0
            unpublished.append(whale)
        mix[whale] = {"weight": round(w, 4), "edge": edge, "se": se}
        mix_edge += w * edge
        mix_var += (w * se) ** 2
    e_hat = mix_edge - capture["drag_rate"] - capture["fee_rate"]
    se = math.sqrt(mix_var + capture["drag_se"] ** 2)
    p_pos = _phi(e_hat / se) if se > 0 else (1.0 if e_hat > 0 else 0.0)
    return {
        "available": True,
        "whale_mix_edge": mix_edge,
        "whale_mix_se": math.sqrt(mix_var),
        "drag_rate": capture["drag_rate"],
        "drag_se": capture["drag_se"],
        "fee_rate": capture["fee_rate"],
        "sleeve_edge": e_hat,
        "sleeve_se": se,
        "p_edge_positive": p_pos,
        "mix": mix,
        "unpublished_whales": unpublished,
    }


def thesis(edge: dict, flow_per_day: float) -> dict:
    """P(annual profit >= 100% of principal) at MEASURED flow.

    Honesty note baked into the shape: dollar flow through the books
    is set by whale activity and book depth, NOT by our principal —
    so annual profit = flow x 365 x edge regardless of principal
    until flow itself is scaled (more proven whales, more sports,
    deeper markets). The grid therefore shows, for each principal,
    the probability at today's flow AND at flow multiples — the
    multiple IS the build-out requirement.
    """
    if not edge.get("available") or flow_per_day <= 0:
        return {"available": False}
    e, se = edge["sleeve_edge"], edge["sleeve_se"]
    out = []
    for principal in PRINCIPAL_GRID:
        row: dict[str, Any] = {"principal": principal}
        for m in FLOW_MULTIPLES:
            annual = flow_per_day * 365.0 * m
            mu = annual * e
            sd = annual * se
            need = TARGET_ANNUAL * principal
            p = _phi((mu - need) / sd) if sd > 0 else (
                1.0 if mu >= need else 0.0)
            row[f"p_100pct_at_{m}x_flow"] = round(p, 4)
        row["flow_x_for_p50"] = (
            round(TARGET_ANNUAL * principal / (flow_per_day * 365.0 * e), 1)
            if e > 0 else None)
        out.append(row)
    return {
        "available": True,
        "measured_flow_per_day": round(flow_per_day, 2),
        "target_annual_return": TARGET_ANNUAL,
        "grid": out,
    }


async def proof2_payload(pool: Any, since: str | None = None) -> dict:
    """Assemble the full PROOF-2 payload from live tables."""
    from .copies_record import COPY_WHALES
    from .copy_reports import LEDGER_SQL

    since_dt = datetime.fromisoformat(since or DEFAULT_SINCE)
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=timezone.utc)
    rows = [dict(r) for r in await pool.fetch(LEDGER_SQL,
                                              list(COPY_WHALES))]
    cohort = []
    for r in rows:
        at = r.get("placed_at")
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at >= since_dt:
            cohort.append(r)
    capture = capture_from_rows(cohort)
    raw = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1",
        "whale_edge_benchmark")
    benchmark = raw if isinstance(raw, dict) else (
        json.loads(raw) if raw else {})
    edge = combine(capture, benchmark)
    # measured flow: entry notional per day across the cohort span
    days = max(1.0, (datetime.now(timezone.utc) - since_dt).total_seconds()
               / 86_400.0)
    flow = (capture.get("entry_notional") or 0.0) / days
    return {
        "since": since_dt.isoformat(timespec="seconds"),
        "cohort_days": round(days, 2),
        "capture": {k: (round(v, 6) if isinstance(v, float) else v)
                    for k, v in capture.items()
                    if k != "per_whale_entry"},
        "edge": {k: (round(v, 6) if isinstance(v, float) else v)
                 for k, v in edge.items()},
        "thesis": thesis(edge, flow),
        "benchmark_measured_at": (benchmark or {}).get("measured_at"),
    }
