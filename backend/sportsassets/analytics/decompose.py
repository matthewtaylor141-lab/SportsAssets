"""His edge, decomposed into SELECTION and TIMING.

Owner question 2026-09-01 (evening): can the whales' engine be reverse
engineered from their history? The honest precondition is a number
nobody had measured. A whale's edge at his own fill price is the sum of
two things: WHAT he picked (selection -- would this side have paid at a
fair price?) and WHEN he acted (timing -- he bought before the price
moved). History can teach the first; only speed can reproduce the
second. So score every one of his buys at four prices:

  at_fill    his fill price            his edge on these buys. NOT the
                                       edge gate's number: the gate is his
                                       whole book, merges and sells
                                       included; this is BUY-only, this
                                       window, held to resolution
  at_5m      the market 5 min later    most of the timing removed
  at_10m     the market 10 min later
  at_60m     the market an hour later
  pre_game   the market just before    ALL timing removed: pure selection.
             the game starts           Pre-game buys only.

Each leg is a ratio-estimator ROI with the same cluster-robust interval
proof.roi_with_ci gives our own copies (clustered by his GAME, because
three legs of one match are one result). The reading compares the
pre-game lower bound to zero:

  pre_game lower bound > 0 on 30+ games   SELECTION SURVIVES: there is a
                                          learnable WHAT under the WHEN
  pre_game interval contains zero while   TIMING: what he is doing is
  at_fill is proven                       acting before the price moves;
                                          history cannot teach that
  otherwise                               NOT DEMONSTRATED; see n

Pure functions over rows the endpoint reads from trades x trade_marks x
markets. Payout is the venue's resolution for the outcome he bought.
"""
from __future__ import annotations

import json
from typing import Any

from .proof import MIN_PROOF_CLUSTERS, roi_with_ci

# The whales the owner named. The endpoint accepts any whale; the probe
# reads these four.
WHALES: tuple[str, ...] = ("rn1", "homerunhazard", "swisstony", "kch123")

LEGS: tuple[tuple[str, str | None], ...] = (
    ("at_fill", None), ("at_5m", "p_5m"), ("at_10m", "p_10m"),
    ("at_60m", "p_60m"), ("pre_game", "p_pre"))


def payout_of(resolved_prices: Any, outcome_index: Any) -> float | None:
    """The venue's payout for the outcome he bought: 1.0, 0.0, or a
    split; None if unresolvable."""
    v = resolved_prices
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    if not isinstance(v, (list, tuple)):
        return None
    try:
        i = int(outcome_index)
        p = float(v[i])
    except (TypeError, ValueError, IndexError):
        return None
    return p if 0.0 <= p <= 1.0 else None


def _leg(rows: list[dict], price_key: str | None) -> dict:
    stakes = []
    for r in rows:
        p = r.get("price") if price_key is None else r.get(price_key)
        payout = r.get("payout")
        try:
            size = float(r.get("size") or 0)
            p = float(p) if p is not None else None
        except (TypeError, ValueError):
            continue
        if p is None or payout is None or size <= 0 or not (0.0 < p < 1.0):
            continue
        stakes.append({"stake": size * p, "pnl": size * (float(payout) - p),
                       "event_key": r.get("event_key")})
    out = roi_with_ci(stakes)
    ci = out.get("ci95")
    g = int(out.get("clusters") or 0)
    if not ci:
        out["verdict"] = "INSUFFICIENT — no interval"
    elif g < MIN_PROOF_CLUSTERS:
        out["verdict"] = f"PROVISIONAL (games<{MIN_PROOF_CLUSTERS})"
    elif ci[0] > 0:
        out["verdict"] = "POSITIVE at 95%"
    elif ci[1] < 0:
        out["verdict"] = "NEGATIVE at 95%"
    else:
        out["verdict"] = "NOT DEMONSTRATED — contains zero"
    return out


def score(rows: list[dict]) -> dict:
    """rows: [{size, price, payout, p_5m, p_10m, p_60m, p_pre, event_key}]
    -> {legs: {name: roi_with_ci + verdict}, timing_share, reading}."""
    legs = {name: _leg(rows, key) for name, key in LEGS}
    fill, pre = legs["at_fill"], legs["pre_game"]
    out: dict[str, Any] = {"legs": legs, "n_rows": len(rows)}
    if fill.get("roi") and fill["roi"] > 0 and pre.get("roi") is not None:
        out["timing_share"] = round(1.0 - pre["roi"] / fill["roi"], 4)
    else:
        out["timing_share"] = None
    pci, fci = pre.get("ci95"), fill.get("ci95")
    pg, fg = int(pre.get("clusters") or 0), int(fill.get("clusters") or 0)
    if pci and pg >= MIN_PROOF_CLUSTERS and pci[0] > 0:
        out["reading"] = (
            f"SELECTION SURVIVES: at the pre-game price his picks return "
            f"{pre['roi']:+.2%} [{pci[0]:+.2%}, {pci[1]:+.2%}] on {pg} "
            f"games. There is a learnable WHAT under the WHEN: build the "
            f"selection model, validate it walk-forward, measure at $50.")
    elif pci and pg >= MIN_PROOF_CLUSTERS and pci[1] < 0:
        out["reading"] = (
            f"ANTI-SELECTION: at the pre-game price his picks LOSE "
            f"{pre['roi']:+.2%} [{pci[0]:+.2%}, {pci[1]:+.2%}] — whatever he "
            f"earns is entirely timing.")
    elif (pci and pg >= MIN_PROOF_CLUSTERS and fci and fg >= MIN_PROOF_CLUSTERS
            and fci[0] > 0 and pci[0] <= 0):
        out["reading"] = (
            f"TIMING: his fills earn {fill['roi']:+.2%} [{fci[0]:+.2%}, "
            f"{fci[1]:+.2%}] but at the pre-game price the same picks read "
            f"{pre['roi']:+.2%} [{pci[0]:+.2%}, {pci[1]:+.2%}] — the edge is "
            f"in WHEN he acts, not WHAT he picks. History cannot teach "
            f"that; only a faster price can.")
    else:
        out["reading"] = (
            f"NOT DEMONSTRATED: pre-game leg has {pre.get('n', 0)} buys on "
            f"{pg} games (needs {MIN_PROOF_CLUSTERS}+ games and an interval "
            f"that leaves zero); at_fill has {fill.get('n', 0)} on {fg}.")
    return out


async def cohort_decompose(pool: Any, whale: str, days: int = 30) -> dict:
    """Score one whale's resolved BUYs over the window."""
    w = whale.lower()
    rows = await pool.fetch(
        """
        SELECT t.id, t.size::float8 AS size, t.price::float8 AS price,
               t.outcome_index, m.resolved_prices,
               tm.p_5m, tm.p_10m, tm.p_60m, tm.p_pre,
               COALESCE(NULLIF(m.event_slug, ''), NULLIF(t.event_slug, ''),
                        t.condition_id) AS event_key
          FROM trades t
          JOIN whales wh ON wh.id = t.whale_id
          JOIN trade_marks tm ON tm.trade_id = t.id
          JOIN markets m ON m.condition_id = t.condition_id
         WHERE lower(wh.username) = $1
           AND t.side = 'BUY'
           AND t.ts >= now() - make_interval(days => $2)
           AND COALESCE(m.resolved, false) = true
           AND m.resolved_prices IS NOT NULL
           AND t.outcome_index IS NOT NULL
        """, w, int(days))
    scored = []
    unresolvable = 0
    for r in rows:
        d = dict(r)
        d["payout"] = payout_of(d.pop("resolved_prices"), d.get("outcome_index"))
        if d["payout"] is None:
            unresolvable += 1
            continue
        scored.append(d)
    out = score(scored)
    cov = await pool.fetchrow(
        """
        SELECT count(*)::int AS buys,
               count(tm.trade_id)::int AS marked,
               count(tm.trade_id) FILTER (WHERE tm.p_pre IS NOT NULL)::int AS pre_game_marked,
               count(*) FILTER (WHERE COALESCE(m.resolved, false))::int AS resolved
          FROM trades t
          JOIN whales wh ON wh.id = t.whale_id
          LEFT JOIN trade_marks tm ON tm.trade_id = t.id
          LEFT JOIN markets m ON m.condition_id = t.condition_id
         WHERE lower(wh.username) = $1 AND t.side = 'BUY'
           AND t.ts >= now() - make_interval(days => $2)
        """, w, int(days))
    out["whale"] = w
    out["days"] = int(days)
    out["coverage"] = dict(cov) if cov else {}
    out["coverage"]["unresolvable_payout"] = unresolvable
    out["min_proof_clusters"] = MIN_PROOF_CLUSTERS
    return out
