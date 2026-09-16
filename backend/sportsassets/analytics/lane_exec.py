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
# A send more than this long after his trade is a sweep reclaim of an
# old row, not the lane's own time to venue.
MAX_SEND_LAG_S = 3600.0


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
        # a 'merged' row is an add leg that FILLED and was booked onto
        # its standing row (migration 045): its own money columns are
        # zeroed there, so it is counted by status, not by filled_usd
        filled = sum(1 for r, s in zip(rs, st)
                     if s == "merged"
                     or (s in FILLED and (_num(r.get("filled_usd")) or 0) > 0))
        counts = {
            "attempts": len(rs), "filled": filled,
            "unfilled": st.count("unfilled"), "rejected": st.count("rejected"),
            "error": st.count("error"), "open": sum(1 for s in st if s in OPEN),
            # add legs merged onto a standing row, broken out so the
            # probe shows the adds lever firing (a subset of `filled`)
            "merged": st.count("merged"),
        }
        counts["fill_rate"] = round(filled / len(rs), 4) if rs else None
        reaction = [_num(r.get("reaction_s")) for r in rs]
        det = [_num(r.get("det_lag")) for r in rs]
        send, rtt = [], []
        for r in rs:
            ts, t_send, t_reply = _num(r.get("his_ts")), _num(r.get("t_send")), _num(r.get("t_reply"))
            # A sweep reclaim re-sends a row hours after his trade under
            # the row's ORIGINAL reaction stamp; that is not the lane's
            # time to venue and is left out of the send percentiles.
            if ts is not None and t_send is not None and 0 <= t_send - ts <= MAX_SEND_LAG_S:
                send.append(t_send - ts)
            if t_send is not None and t_reply is not None:
                rtt.append(t_reply - t_send)
        # WHY THE LANE REFUSES (first live read, 2026-09-02: 24,225 of
        # 25,287 chain-lane detections in a week ended 'rejected' before
        # any order). The row's error text names the gate; the top
        # reasons are the lane's real fill-rate story.
        reasons: dict[str, int] = {}
        for r, s_ in zip(rs, st):
            if s_ == "rejected":
                k = str(r.get("err") or "").strip()[:48] or "(no reason)"
                reasons[k] = reasons.get(k, 0) + 1
        top_reasons = sorted(reasons.items(), key=lambda kv: -kv[1])[:10]
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
        out["lanes"][lane] = {**counts, "latency": lat, "settled": roi,
                              "rejected_reasons": [{"reason": k, "n": n}
                                                   for k, n in top_reasons]}
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
               left(lo.error, 48) AS err,
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
    out["adds"] = await adds_census(pool, days, whale)
    return out


async def adds_census(pool: Any, days: int = 7, whale: str | None = None) -> dict:
    """The never-add -> adds lever, counted (2026-09-02): legs merged
    onto standing rows, legs named for the reaper, legs that stood
    alone, the dollars the merged legs spent, and the reasons his other
    re-buys were still refused. Best-effort: an unreadable census is an
    empty one, never an error on the probe."""
    args: list[Any] = [int(days)]
    wfilt = ""
    if whale:
        args.append(whale.lower())
        wfilt = "AND lower(COALESCE(whale_username, '')) = $2"
    try:
        row = await pool.fetchrow(
            f"""
            SELECT count(*) FILTER (WHERE status = 'merged')::int AS merged,
                   count(*) FILTER (WHERE status = 'error'
                                    AND error LIKE 'ORPHAN FILL RECORDED%add leg of row%')::int AS named,
                   count(*) FILTER (WHERE error LIKE 'add-unmerged%')::int AS standalone,
                   COALESCE(sum((raw->'add_leg'->>'usd')::float8)
                            FILTER (WHERE status = 'merged'), 0)::float8 AS legs_usd,
                   count(*) FILTER (WHERE error LIKE 'never-add:%(add refused%')::int AS refused
              FROM live_orders
             WHERE placed_at >= now() - make_interval(days => $1) {wfilt}
            """, *args)
        why = await pool.fetch(
            f"""
            SELECT substring(error from '\\(add refused: [^)]*\\)') AS why, count(*)::int AS n
              FROM live_orders
             WHERE placed_at >= now() - make_interval(days => $1) {wfilt}
               AND error LIKE 'never-add:%(add refused%'
             GROUP BY 1 ORDER BY 2 DESC LIMIT 12
            """, *args)
    except Exception:  # noqa: BLE001 — a census that cannot be read is empty
        return {"merged": 0, "named": 0, "standalone": 0, "legs_usd": 0.0,
                "refused": 0, "refusals": {}, "unavailable": True}
    return {"merged": int(row["merged"] or 0) if row else 0,
            "named": int(row["named"] or 0) if row else 0,
            "standalone": int(row["standalone"] or 0) if row else 0,
            "legs_usd": round(float(row["legs_usd"] or 0.0), 2) if row else 0.0,
            "refused": int(row["refused"] or 0) if row else 0,
            "refusals": {str(r["why"] or "?"): int(r["n"] or 0) for r in why}}


__all__ = ["summarize", "cohort_lane_exec", "adds_census"]
