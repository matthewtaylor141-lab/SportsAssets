"""What each refusal gate costs or saves: the refused trades, scored at
HIS price to resolution, per reason.

Owner order 2026-09-02: "limit the rejections in a safe and effective
way that preserves the profitability of the whale himself ... make
decisions based on real numbers." The fast lane refused 24,225 of
25,287 detections in a week before any order. A gate preserves his
edge only if the trades it refuses are worse than the ones it lets
through; that is measurable from rows we already hold.

Every 'rejected' live_orders row names its gate in its error text and
points at his trade (size, price, outcome) and the market's resolution.
Each refused trade is scored at his fill price held to resolution --
the same basis as the true-edge counterfactual, labelled as such: it
assumes a fill at his price, which the impact buckets say costs about a
cent. Per gate: ratio-estimator ROI with the cluster-robust interval
proof.roi_with_ci gives our copies, clustered by his game, thirty-game
floor. The TAKEN trades (filled copies) are scored on the same basis
beside them, so a gate is judged against what we actually let through.

  refused lower bound > 0 on 30+ games   REFUSED TRADES EARN: the gate is
                                         discarding proven edge -- a lever
  refused upper bound < 0 on 30+ games   REFUSED TRADES LOSE: the gate is
                                         preserving his edge -- keep it
  otherwise                              NOT DEMONSTRATED / PROVISIONAL

Refusals that happen before a row exists (cell gates, dust floor,
exits) are counted in the copy census but leave no trade to score
here; they are named in the payload as unscored.
"""
from __future__ import annotations

from typing import Any

from .decompose import payout_of
from .proof import MIN_PROOF_CLUSTERS, roi_with_ci

# error-text prefix -> gate label. Order matters: first match wins.
GATES: tuple[tuple[str, str], ...] = (
    ("unmapped", "unmapped"),
    ("no verified Polymarket US market", "unmapped"),
    ("one position per game", "one_per_game"),
    ("never-add", "never_add"),
    ("no-stack", "no_stack"),
    ("not verified-profitable", "not_verified_profitable"),
    ("stale-signal", "stale_signal"),
    ("first-fill gate", "first_fill_gate"),
    ("short-branch-refused", "short_branch"),
    ("side-echo", "side_echo_trip"),
    ("quarantined", "mapping_quarantine"),
    ("hold:", "hold_pending_certification"),
    ("kalshi copied", "kalshi_took_it"),
    ("no side intent", "no_side_intent"),
    ("side-price-mismatch", "side_price_mismatch"),
    ("clob-leg-closed", "clob_leg_closed"),
)
UNSCORED_CENSUS_REASONS = (
    "was_an_exit_pending", "below_min_clip", "under_one_share",
    "already_taken", "cell_gate_soccer_price_floor",
    "cell_gate_cell_not_allowed", "cell_gate_outside_entry_band",
    "cell_gate_market_type_blocked", "not_buy_or_off_roster", "whale_cut")


def gate_of(error: Any) -> str:
    e = str(error or "").strip().lower()
    for prefix, label in GATES:
        if e.startswith(prefix.lower()):
            return label
    return "other"


def _score(rows: list[dict]) -> dict:
    stakes = []
    for r in rows:
        try:
            size = float(r.get("size") or 0)
            p = float(r.get("price")) if r.get("price") is not None else None
        except (TypeError, ValueError):
            continue
        payout = r.get("payout")
        if p is None or payout is None or size <= 0 or not (0.0 < p < 1.0):
            continue
        stakes.append({"stake": size * p, "pnl": size * (float(payout) - p),
                       "event_key": r.get("event_key")})
    out = roi_with_ci(stakes)
    ci, g = out.get("ci95"), int(out.get("clusters") or 0)
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


def score(refused: list[dict], taken: list[dict]) -> dict:
    """refused/taken: [{gate|None, size, price, payout, event_key}] ->
    {gates: {label: score + n_refused + lever}, taken: score, reading}."""
    by: dict[str, list[dict]] = {}
    for r in refused:
        by.setdefault(str(r.get("gate") or gate_of(r.get("error"))), []).append(r)
    gates: dict[str, dict] = {}
    total = sum(len(v) for v in by.values())
    for label, rows in sorted(by.items(), key=lambda kv: -len(kv[1])):
        s = _score(rows)
        s["refused"] = len(rows)
        s["share_of_refusals"] = round(len(rows) / total, 4) if total else None
        ci, g = s.get("ci95"), int(s.get("clusters") or 0)
        if ci and g >= MIN_PROOF_CLUSTERS and ci[0] > 0:
            s["lever"] = "REFUSED TRADES EARN — the gate discards proven edge; lift it at the measuring clip"
        elif ci and g >= MIN_PROOF_CLUSTERS and ci[1] < 0:
            s["lever"] = "REFUSED TRADES LOSE — the gate preserves his edge; keep it"
        elif ci and g >= MIN_PROOF_CLUSTERS:
            s["lever"] = "FLAT — lifting neither adds nor removes proven edge; volume only"
        else:
            s["lever"] = "NOT DEMONSTRATED — too few games to judge"
        gates[label] = s
    tk = _score(taken)
    tk["taken"] = len(taken)
    out: dict[str, Any] = {"gates": gates, "taken_at_his_price": tk,
                           "n_refused_scored": total,
                           "min_proof_clusters": MIN_PROOF_CLUSTERS,
                           "unscored_census_reasons": list(UNSCORED_CENSUS_REASONS),
                           "basis": "each trade scored at HIS fill price held to "
                                    "resolution (fill at his price assumed; the "
                                    "impact buckets put that at about a cent)"}
    earn = [k for k, v in gates.items() if v["lever"].startswith("REFUSED TRADES EARN")]
    lose = [k for k, v in gates.items() if v["lever"].startswith("REFUSED TRADES LOSE")]
    out["reading"] = (f"levers (refused trades earn at 95%): {', '.join(earn) or 'none yet'}; "
                      f"keep (refused trades lose at 95%): {', '.join(lose) or 'none yet'}; "
                      f"taken at his price: {tk.get('verdict')}")
    return out


async def cohort_gate_edge(pool: Any, days: int = 30, whale: str | None = None) -> dict:
    args: list[Any] = [int(days)]
    q = """
        SELECT lo.status, lo.error, lower(COALESCE(lo.whale_username, '')) AS whale,
               COALESCE(t.source, 'unknown') AS lane,
               t.size::float8 AS size, t.price::float8 AS price,
               t.outcome_index, m.resolved_prices,
               COALESCE(NULLIF(m.event_slug, ''), NULLIF(t.event_slug, ''),
                        t.condition_id) AS event_key
          FROM live_orders lo
          JOIN trades t ON t.id = lo.trade_id
          JOIN markets m ON m.condition_id = t.condition_id
         WHERE lo.placed_at >= now() - make_interval(days => $1)
           AND lo.side = 'BUY'
           -- 'merged': an add leg that FILLED and was booked onto its
           -- standing row (migration 045) -- a taken trade, scored at
           -- his price like any other fill, never a refusal
           AND lo.status IN ('rejected', 'filled', 'settled', 'cashed_out', 'exiting', 'merged')
           AND COALESCE(lo.whale_username, '') NOT IN ('manual', 'underdog')
           AND COALESCE(m.resolved, false) = true
           AND m.resolved_prices IS NOT NULL
           AND t.outcome_index IS NOT NULL
    """
    if whale:
        args.append(whale.lower())
        q += "           AND lower(COALESCE(lo.whale_username, '')) = $2\n"
    rows = await pool.fetch(q, *args)
    refused, taken, unresolvable = [], [], 0
    for r in rows:
        d = dict(r)
        d["payout"] = payout_of(d.pop("resolved_prices"), d.get("outcome_index"))
        if d["payout"] is None:
            unresolvable += 1
            continue
        if d["status"] == "rejected":
            d["gate"] = gate_of(d.get("error"))
            refused.append(d)
        else:
            taken.append(d)
    out = score(refused, taken)
    out["days"] = int(days)
    out["whale"] = whale.lower() if whale else None
    out["unresolvable_payout"] = unresolvable
    return out


__all__ = ["GATES", "gate_of", "score", "cohort_gate_edge"]
