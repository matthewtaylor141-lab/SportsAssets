"""The curve that decides the fill rule: mean ask change t seconds after his fill.

Sampled by workers/price_path.py into the price_path table, one row per
(attempted copy, offset). This module turns those samples into the
number that matters: for each offset t, the mean of (ask_t - ask_0) with
a 95% interval, in cents.

WHY t=0 IS THE BASELINE AND NOT HIS PRICE. ask_0 is the post-impact ask
at the moment we could first act -- the price an IOC would pay. The
question is what happens AFTER that: does the market keep moving in his
direction (buy now), revert (rest a bid), or sit still (no limit rule
has edge). His own price is one impact below ask_0 and is the rest
lane's target; the curve says whether the market ever comes back to it.

THE INTERVAL CLUSTERS BY GAME (round three, 2026-09-01). Each row
contributes one difference per offset, but rows are not independent:
the whale fires sibling legs of one match within seconds, and their asks
at 30-600 s share the game's news and the same follower crowd. The iid
interval was too narrow in exactly the direction that lets an offset
"leave zero" early -- and the reading fires on the FIRST offset that
does. Same algebra as proof.roi_with_ci: residuals summed within a game
before squaring. A row without a game key is its own cluster.

A row missing an offset (venue unreadable at that moment) is dropped
from that offset only, never zero-filled -- a pile of zeros is a flat
curve by fiat.
"""
from __future__ import annotations

import math
from typing import Any

from .proof import Z95
from .proof import MIN_PROOF_CLUSTERS

OFFSETS_S: tuple[int, ...] = (0, 30, 60, 120, 281, 600)


def _clustered(diffs: list[float], keys: list[str]) -> tuple[float, int, float | None]:
    """(se, clusters, deff) for the mean of `diffs`, clustered by key."""
    n = len(diffs)
    mean = sum(diffs) / n
    e = [d - mean for d in diffs]
    ss = sum(x * x for x in e)
    se_iid = math.sqrt(ss / (n - 1)) / math.sqrt(n)
    clus: dict[str, float] = {}
    for i, (x, k) in enumerate(zip(e, keys)):
        kk = k or f"\x00{i}"           # unkeyed rows stay singletons
        clus[kk] = clus.get(kk, 0.0) + x
    g = len(clus)
    if g < 2:
        return se_iid, g, None
    se = math.sqrt(max(0.0, g / (g - 1) * sum(v * v for v in clus.values()))) / n
    return se, g, (round(se / se_iid, 4) if se_iid > 0 else None)


def path_curve(samples: list[dict]) -> dict:
    """samples: [{row_id, t_s, ask, event_key?}] -> per-offset mean delta with CI.

    Returns {"n_rows": ..., "by_t": {t: {n, clusters, deff, mean_cents,
    ci95_cents, verdict}}, "reading": ...}.
    """
    by_row: dict[Any, dict[int, float]] = {}
    key_of: dict[Any, str] = {}
    for s in samples:
        try:
            rid, t, ask = s["row_id"], int(s["t_s"]), s.get("ask")
        except (KeyError, TypeError, ValueError):
            continue
        if ask is None:
            continue
        try:
            a = float(ask)
        except (TypeError, ValueError):
            continue
        if not (0.0 < a < 1.0):
            continue
        by_row.setdefault(rid, {})[t] = a
        k = s.get("event_key")
        if k:
            key_of[rid] = str(k)
    out: dict[str, Any] = {"n_rows": len(by_row), "by_t": {}}
    for t in OFFSETS_S:
        if t == 0:
            n0 = sum(1 for r in by_row.values() if 0 in r)
            out["by_t"][t] = {"n": n0, "mean_cents": 0.0,
                              "ci95_cents": [0.0, 0.0] if n0 else None,
                              "verdict": "baseline"}
            continue
        pairs = [((r[t] - r[0]) * 100.0, key_of.get(rid, ""))
                 for rid, r in by_row.items() if 0 in r and t in r]
        n = len(pairs)
        if n == 0:
            out["by_t"][t] = {"n": 0, "mean_cents": None,
                              "ci95_cents": None, "verdict": "NO SAMPLE"}
            continue
        diffs = [d for d, _ in pairs]
        mean = sum(diffs) / n
        if n < 2:
            out["by_t"][t] = {"n": n, "mean_cents": round(mean, 3),
                              "ci95_cents": None,
                              "verdict": "INSUFFICIENT — one row"}
            continue
        se, g, deff = _clustered(diffs, [k for _, k in pairs])
        if g < 2:
            out["by_t"][t] = {"n": n, "clusters": g, "deff": None,
                              "mean_cents": round(mean, 3), "ci95_cents": None,
                              "verdict": "INSUFFICIENT — one game"}
            continue
        lo, hi = mean - Z95 * se, mean + Z95 * se
        if lo > 0:
            v = "RISES at 95% — his information is still propagating"
        elif hi < 0:
            v = "REVERTS at 95% — the impact is coming back to him"
        else:
            v = "NOT DEMONSTRATED — contains zero"
        # THE SAME FLOOR AS EVERY OTHER VERDICT (2026-09-02, first live
        # read): 19 rows on a handful of games printed "RISES at 95%"
        # while the proof, impact and screen verdicts all hold at 30
        # games. A curve on fewer games leans; it does not decide.
        if g < MIN_PROOF_CLUSTERS and v.startswith(("RISES", "REVERTS")):
            v = (f"PROVISIONAL (games<{MIN_PROOF_CLUSTERS}) — leans "
                 f"{v.split(' at 95%')[0]}")
        out["by_t"][t] = {"n": n, "clusters": g, "deff": deff,
                          "mean_cents": round(mean, 3),
                          "ci95_cents": [round(lo, 3), round(hi, 3)],
                          "verdict": v}
    # THE READING. Name the first offset whose (clustered) interval
    # leaves zero, and which way. The fill rule follows from the
    # direction: rises -> take the ask now (IOC), reverts -> rest at his
    # price, neither -> the lever is not the order type. A provisional
    # lean is reported as a lean, never as the rule.
    for t in OFFSETS_S[1:]:
        b = out["by_t"][t]
        if b.get("verdict", "").startswith("RISES"):
            out["reading"] = (f"RISES by t={t}s: buy the post-impact ask "
                              f"immediately; a resting bid below it will "
                              f"be adversely selected")
            return out
        if b.get("verdict", "").startswith("REVERTS"):
            out["reading"] = (f"REVERTS by t={t}s: a bid resting at his "
                              f"price captures the reversion; an IOC at "
                              f"the ask pays impact that comes back")
            return out
    for t in OFFSETS_S[1:]:
        b = out["by_t"][t]
        if b.get("verdict", "").startswith("PROVISIONAL"):
            out["reading"] = (f"PROVISIONAL: the curve leans "
                              f"{b['verdict'].split('leans ')[-1]} by t={t}s "
                              f"on {b.get('clusters')} games — under the "
                              f"{MIN_PROOF_CLUSTERS}-game floor, not a rule")
            return out
    out["reading"] = ("no offset leaves zero yet — the order type is not "
                      "the lever on this sample; see n per offset")
    return out


async def cohort_path(pool: Any, since: str, whale: str | None = None) -> dict:
    """Curve over attempted copies placed since the cutoff, keyed by game."""
    import datetime as _dt

    ts = _dt.datetime.fromisoformat(str(since))
    args: list[Any] = [ts]
    q = """
        SELECT pp.row_id, pp.t_s, pp.ask,
               COALESCE(NULLIF(m.event_slug, ''),
                        NULLIF(lo.us_market_slug, '')) AS event_key
          FROM price_path pp
          JOIN live_orders lo ON lo.id = pp.row_id
          LEFT JOIN market_tokens mt ON mt.token_id = lo.asset
          LEFT JOIN markets m ON m.condition_id = mt.condition_id
         WHERE lo.placed_at >= $1
           AND COALESCE(lo.whale_username, '') NOT IN ('manual', 'underdog')
    """
    if whale:
        args.append(whale.lower())
        q += "           AND lower(COALESCE(lo.whale_username,'')) = $2\n"
    rows = [dict(r) for r in await pool.fetch(q, *args)]
    out = path_curve(rows)
    out["since"] = since
    out["whale"] = whale.lower() if whale else "(all copied whales)"
    return out
