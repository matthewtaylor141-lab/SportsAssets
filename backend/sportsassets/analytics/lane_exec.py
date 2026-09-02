"""Execution by DETECTION lane: where the seconds go, and what fills.

The chain lane sees rn1 in about a second; the poll lane in minutes.
What nobody had measured was what happens AFTER detection on each
lane: how long until the order actually leaves for the venue, how often
the IOC fills, and what the fills return. placed_at is stamped at the
INSERT (before mapping and three venue round trips) and reaction_s at
the copy semaphore, so neither is the time the order left. The
executor now stamps t_send/t_reply into the row's raw at the IOC call;
this module reads them beside trades.source.

Per lane, over fresh copies (reaction_s known) in the window:
  attempts, filled, unfilled, rejected, error, open
  fill_rate                         filled / attempts
  reaction_s p50/p90                his trade -> our attempt
  send_s p50/p90                    his trade -> the order left (t_send)
  venue_rtt_s p50/p90               t_send -> t_reply
  settled: roi_with_ci over settled/cashed-out rows, clustered by game

Pure functions over rows; the endpoint supplies the rows.
"""
from __future__ import annotations

from typing import Any

from .proof import MIN_PROOF_CLUSTERS, roi_with_ci

FILLED = ("filled", "settled", "cashed_out", "exiting")
OPEN = ("submitting",)


def _pct(vals: list[float], q: float) -> float | None:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    if len(v) == 1:
        return round(v[0], 3)
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 3)


def _num(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def summarize(rows: list[dict]) -> dict:
    """rows: [{lane, status, filled_usd, reaction_s, det_lag, his_ts,
    t_send, t_reply, stake, pnl, event_key, settled(bool)}]."""
    lanes: dict[str, list[dict]] = {}
    for r in rows:
        lanes.setdefault(str(r.get("lane") or "unknown"), []).append(r)
    out: dict[str, Any] = {"lanes": {}, "n_rows": len(rows),
                           "min_proof_clusters": MIN_PROOF_CLUSTERS}
    for lane, rs in sorted(lanes.items()):
        st = [str(r.get("status") or "") for r in rs]
        filled = sum(1 for r, s in zip(rs, st)
                     if s in FILLED and (_num(r.get("filled_usd")) or 0) > 0)
        counts = {
            "attempts": len(rs), "filled": filled,
            "unfilled": st.count("unfilled"), "rejected": st.count("rejected"),
            "error": st.count("error"), "open": sum(1 for s in st if s in OPEN),
        }
        counts["fill_rate"] = round(filled / len(rs), 4) if rs else None
        reaction = [_num(r.get("reaction_s")) for r in rs]
        det = [_num(r.get("det_lag")) for r in rs]
        send, rtt = [], []
        for r in rs:
            ts, t_send, t_reply = _num(r.get("his_ts")), _num(r.get("t_send")), _num(r.get("t_reply"))
            if ts is not None and t_send is not None:
                send.append(t_send - ts)
            if t_send is not None and t_reply is not None:
                rtt.append(t_reply - t_send)
        lat = {
            "reaction_s": {"p50": _pct(reaction, 0.5), "p90": _pct(reaction, 0.9),
                           "n": sum(1 for x in reaction if x is not None)},
            "detect_s": {"p50": _pct(det, 0.5), "p90": _pct(det, 0.9),
                         "n": sum(1 for x in det if x is not None)},
            "send_s": {"p50": _pct(send, 0.5), "p90": _pct(send, 0.9), "n": len(send)},
            "venue_rtt_s": {"p50": _pct(rtt, 0.5), "p90": _pct(rtt, 0.9), "n": len(rtt)},
        }
        settled = [{"stake": _num(r.get("stake")) or 0.0, "pnl": _num(r.get("pnl")) or 0.0,
                    "event_key": r.get("event_key")}
                   for r in rs if r.get("settled") and _num(r.get("pnl")) is not None
                   and (_num(r.get("stake")) or 0) > 0]
        roi = roi_with_ci(settled)
        ci, g = roi.get("ci95"), int(roi.get("clusters") or 0)
        if not ci:
            roi["verdict"] = "INSUFFICIENT — no interval"
        elif g < MIN_PROOF_CLUSTERS:
            roi["verdict"] = f"PROVISIONAL (games<{MIN_PROOF_CLUSTERS})"
        elif ci[0] > 0:
            roi["verdict"] = "POSITIVE at 95%"
        elif ci[1] < 0:
            roi["verdict"] = "NEGATIVE at 95%"
        else:
            roi["verdict"] = "NOT DEMONSTRATED — contains zero"
        out["lanes"][lane] = {**counts, "latency": lat, "settled": roi}
    # THE READING: the fast lane's fill rate and where its seconds go.
    fast = out["lanes"].get("chain")
    if fast and fast["attempts"]:
        s, r = fast["latency"]["send_s"], fast["latency"]["reaction_s"]
        out["reading"] = (
            f"chain lane: {fast['attempts']} attempts, fill rate "
            f"{(fast['fill_rate'] or 0):.0%}; his trade -> our attempt p50 "
            f"{r['p50']}s, -> order sent p50 {s['p50']}s (p90 {s['p90']}s) "
            f"on {s['n']} stamped rows; settled {fast['settled'].get('verdict')}")
    else:
        out["reading"] = "no chain-lane copies in the window"
    return out


async def cohort_lane_exec(pool: Any, days: int = 7, whale: str | None = None) -> dict:
    args: list[Any] = [int(days)]
    q = """
        SELECT COALESCE(t.source, 'unknown') AS lane, lo.status,
               lo.filled_usd::float8 AS filled_usd,
               lo.reaction_s::float8 AS reaction_s,
               extract(epoch FROM (t.detected_at - t.ts))::float8 AS det_lag,
               extract(epoch FROM t.ts)::float8 AS his_ts,
               (lo.raw->>'t_send')::float8 AS t_send,
               (lo.raw->>'t_reply')::float8 AS t_reply,
               COALESCE(lo.filled_usd, lo.requested_usd)::float8 AS stake,
               lo.pnl::float8 AS pnl,
               (lo.status IN ('settled', 'cashed_out')) AS settled,
               COALESCE(NULLIF(m.event_slug, ''), NULLIF(lo.us_market_slug, '')) AS event_key
          FROM live_orders lo
          JOIN trades t ON t.id = lo.trade_id
          LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
          LEFT JOIN markets m ON m.condition_id = mt.condition_id
         WHERE lo.placed_at >= now() - make_interval(days => $1)
           AND lo.reaction_s IS NOT NULL
           AND lo.side = 'BUY'
           AND COALESCE(lo.whale_username, '') NOT IN ('manual', 'underdog')
    """
    if whale:
        args.append(whale.lower())
        q += "           AND lower(COALESCE(lo.whale_username, '')) = $2\n"
    rows = [dict(r) for r in await pool.fetch(q, *args)]
    out = summarize(rows)
    out["days"] = int(days)
    out["whale"] = whale.lower() if whale else None
    return out


__all__ = ["summarize", "cohort_lane_exec"]
