"""CONTINUOUS IMPROVEMENT FOR THE RN1X MANAGEMENT POLICY.

The brief: "records outcomes, evaluates challengers and retains the
existing policy unless improvement is demonstrated." Those are three
separate obligations and this module keeps them separate.

  RECORDS OUTCOMES     `bettor_rn1x_store` already does this. Outcomes
                       land in their own table and never update a
                       decision row, so a settlement cannot rewrite the
                       expectation that preceded it.
  EVALUATES CHALLENGERS here. Each challenger is re-run over the SAME
                       assigned inventory and the SAME observed evidence
                       as the champion -- that is what makes the
                       comparison about management rather than about
                       which seeds each one happened to get.
  RETAINS THE EXISTING here, and by construction: `recommend()` can
  POLICY               return RETAIN_CHAMPION or
                       CHALLENGER_ELIGIBLE_PENDING_MANAGEMENT, and there
                       is no third value. It has no code path that
                       changes the active policy.

WHY IT CANNOT PROMOTE ANYTHING BY ITSELF. The champion is
MANAGEMENT-DEFINED and was frozen by management before evaluation. A loop
that could swap it out would be an agent rewriting management policy,
which is outside every authorization in force. So a challenger that
clears the gate is recorded as ELIGIBLE with its evidence attached, and a
human decides. "Eligible" is not "promoted" and the status string says so.

THE GATE IS NOT MINE. `learn_gate.gate_v2` is the gate that was declared
before cycle 2 ran, moved into the package unchanged so this loop and
`tools/desk_experiments.py` share one implementation. Its rules are what
stop the three ways a challenger can look better without being better:

  a DOSE REDUCTION -- trading less of a losing policy loses less
  CAPITAL PARKED   -- cost-marked P&L flattered by not closing
  A FILL ASSUMPTION -- winning at one queue_share and losing at another

P_FILL IS STILL NOT_IDENTIFIED. Every comparison runs the whole declared
`SCENARIOS` grid and a challenger must win under all of them. That is the
gate's own rule and this module does not relax it.

NO TUNING AGAINST THE EVALUATION PERIOD. The challenger set is DECLARED
below, in code, as a fixed list. It is not searched, not optimised, and
not extended in response to a result. A new challenger is a commit.
"""

from __future__ import annotations

from . import bettor_rn1x_run as runner
from . import learn_gate as gate_mod

VERSION = "BETTOR_RN1X_LEARN_V1"

MODEL_KEY = "rn1x_mgmt_policy"
TARGET = "MANAGEMENT_PAIR_NET_USD_PER_ASSIGNED_POSITION"
KIND = "POLICY"
KERNEL = "REPLAY_THROUGH_BETTOR_MGMT_LIFECYCLE"

RETAIN = "RETAIN_CHAMPION"
ELIGIBLE = "CHALLENGER_ELIGIBLE_PENDING_MANAGEMENT"

# ── THE DECLARED CHALLENGER SET ──────────────────────────────────────
#
# Fixed, in code, and each one questions ONE number so a verdict is
# attributable. A grid search over both at once would find the best pair
# on the evaluation data, which is the tuning this module exists to
# avoid.
#
# HOLD_TO_SETTLEMENT is included as the null: it is what the position
# does with no management at all, and a management policy that cannot
# beat doing nothing has not earned its complexity.
CHALLENGERS = (
    {"name": "PAIR_090", "params": {"target_cost": 0.90},
     "question": ("does a tighter combined-cost ceiling earn more per "
                  "assigned position, or does it simply refuse more "
                  "completions?")},
    {"name": "PAIR_092", "params": {"target_cost": 0.92},
     "question": ("does a looser ceiling complete enough additional "
                  "pairs to pay for the thinner margin on each?")},
    {"name": "STOP_80", "params": {"trigger_fraction": 0.80},
     "question": ("does a later stop keep more of the recoveries than it "
                  "loses to the deeper drawdowns? UNTESTABLE while the "
                  "loss exit is unavailable -- see the blocker.")},
    {"name": "HOLD_TO_SETTLEMENT", "params": {"manage": False},
     "question": ("the null. Assigned inventory, no management at all, "
                  "settled at the observed payout.")},
)


def challenger_names() -> list:
    return [c["name"] for c in CHALLENGERS]


def _metrics(out: dict, queue_share: float) -> dict:
    """One scenario row, in the shape `learn_gate` consumes.

    THE FIELD NAMES ARE THE GATE'S, not new ones. A gate reading
    `realized_pnl_usd` and a producer writing `net_usd` is how a shared
    gate silently stops gating.
    """
    acct = out.get("accounting") or {}
    final = out.get("final_state") or {}
    unresolved = float(acct.get("open_inventory_at_cost_usd") or 0.0)
    realized = float(acct.get("realized_pnl_usd") or 0.0)
    # TERMINAL VALUE BOUNDS. An unresolved leg pays somewhere in [0, 1]
    # per share, so the book's terminal value is bounded -- and the LOWER
    # bound is what a candidate must survive. Reported, never averaged
    # into the ranking statistic.
    open_qty = float(acct.get("residual_qty") or 0.0) + float(
        acct.get("matched_qty") or 0.0)
    turnover = 0.0
    fills = 0
    # PEAK COMMITTED CAPITAL, from actual outlay. The seed basis is
    # committed the moment inventory is assigned; every BUY fill adds its
    # notional and its fee. A figure taken from `inventory_cost_usd`
    # instead would read ZERO after settlement -- reporting that a
    # position which held $60 of inventory for six hours committed
    # nothing, which is the flattering direction.
    seed_basis = float(
        (out.get("steps") or {}).get("SEED", {}).get("basis_usd") or 0.0)
    buy_outlay = 0.0
    orders = final.get("all_orders") or []
    for o in orders:
        for f in (o.get("fills") or []):
            turnover += abs(float(f["qty"]) * float(f["price"]))
            fills += 1
            if o.get("side") == "BUY":
                buy_outlay += (float(f["qty"]) * float(f["price"])
                               + float(f.get("fee_usd") or 0.0))
    return {
        "queue_share": queue_share,
        "realized_pnl_usd": realized,
        "unresolved_cost_usd": unresolved,
        "terminal_lower_usd": realized - unresolved,
        "terminal_upper_usd": realized - unresolved + open_qty,
        "terminal_lower_vs_start": realized - unresolved,
        # NO EQUITY PATH IS RETAINED for a single assigned position, so
        # drawdown is reported as the worst realised excursion we can
        # actually compute -- not modelled from a mark we do not have.
        "max_drawdown_usd": max(0.0, -realized),
        "peak_committed_usd": seed_basis + buy_outlay,
        "turnover_usd": turnover,
        "orders": len(orders),
        "fills": fills,
        "invariant_ok": bool(acct.get("reconciles")),
    }


def _sum_rows(rows: list) -> dict:
    """Aggregate one scenario across positions. Sums, never averages of
    ratios -- an average of per-position returns weights a $1 position
    like a $1,000 one."""
    if not rows:
        return {}
    keys = ("realized_pnl_usd", "unresolved_cost_usd", "terminal_lower_usd",
            "terminal_upper_usd", "terminal_lower_vs_start",
            "max_drawdown_usd", "peak_committed_usd", "turnover_usd",
            "orders", "fills")
    out = {k: sum(float(r[k]) for r in rows) for k in keys}
    out["queue_share"] = rows[0]["queue_share"]
    out["invariant_ok"] = all(r["invariant_ok"] for r in rows)
    out["positions"] = len(rows)
    return out


def evaluate(seeds, *, scenarios=None, fee_fn=None) -> dict:
    """Run the champion and every challenger over the SAME seeds.

    `seeds` is a list of dicts, each carrying the arguments one call to
    `bettor_rn1x_run.run` needs: rows, payouts, resolved_at,
    source_whale_id, condition_id, initial_inventory_verified.

    ONE EVIDENCE SET, ONE ASSIGNED INVENTORY, MANY POLICIES. Nothing here
    re-selects which positions a policy gets; that is the whole design.
    """
    scen = tuple(scenarios or gate_mod.SCENARIOS)
    arms = [{"name": "CHAMPION", "params": {}}] + [dict(c) for c in
                                                   CHALLENGERS]
    results = {}
    for arm in arms:
        per_scenario = []
        for qs in scen:
            rows = []
            for seed in seeds:
                out = runner.run(queue_share=qs, fee_fn=fee_fn,
                                 policy_params=arm["params"] or None,
                                 **seed)
                if not (out.get("steps", {}).get("SEED", {}) or {}).get("ok"):
                    # A refused seed is refused for EVERY arm identically
                    # (the refusal is about our inventory record, not the
                    # policy), so dropping it keeps the arms comparable.
                    continue
                rows.append(_metrics(out, qs))
            per_scenario.append(_sum_rows(rows))
        results[arm["name"]] = [r for r in per_scenario if r]
    return {"version": VERSION, "scenarios": list(scen), "arms": results,
            # The eligibility floor is a COUNT, so it is an int. A float
            # here would read "6.0 decided orders" on the receipt.
            "decided": int(sum(r.get("orders", 0)
                               for r in results.get("CHAMPION", [])))}


def recommend(evaluation: dict, challenger: str) -> dict:
    """RETAIN or ELIGIBLE. There is no third value and no promote path.

    The gate is `learn_gate.gate_v2`, unchanged. Its verdict is recorded
    verbatim, including when it rejects -- a rejection is the result the
    loop is most likely to produce and the most important one to keep.
    """
    arms = evaluation.get("arms") or {}
    base = arms.get("CHAMPION") or []
    cand = arms.get(challenger) or []
    if not base or not cand or len(base) != len(cand):
        return {"verdict": RETAIN, "challenger": challenger,
                "gate": None,
                "why": ("the comparison is not evaluable: the champion "
                        "produced %d scenario rows and the challenger %d. "
                        "An unevaluable comparison RETAINS -- it is not "
                        "an improvement" % (len(base), len(cand)))}
    verdict = gate_mod.gate_v2(base, cand, evaluation.get("decided", 0))
    return {
        "challenger": challenger,
        "gate": verdict,
        "verdict": ELIGIBLE if verdict["accepted"] else RETAIN,
        "gate_version": "GATE_V2",
        "why": ("; ".join(verdict["reasons"])),
        "not_a_promotion": (
            "ELIGIBLE means the challenger cleared every declared "
            "condition. It does NOT change the active policy. The "
            "champion is MANAGEMENT-DEFINED and frozen; swapping it is a "
            "management decision, and this process has no code path that "
            "performs one."),
    }


def registry_row(challenger: str, evaluation: dict, rec: dict, *,
                 dataset_sha: str, code_sha: str, train_cutoff: float,
                 version: int) -> dict:
    """The row for the EXISTING `bettor_learn_model` register.

    Not a new register. The brief was to use the existing kernel, scripts
    and registry, and `bettor_learn_model` already holds model_key,
    version, params, evaluation, status and superseded_by -- which is
    exactly a champion/challenger ledger.
    """
    params = next((c["params"] for c in CHALLENGERS
                   if c["name"] == challenger), {})
    return {
        "model_key": MODEL_KEY, "version": int(version),
        "kind": KIND, "target": TARGET, "horizon_s": 0.0, "kernel": KERNEL,
        "dataset_sha": dataset_sha, "code_sha": code_sha,
        "train_cutoff": float(train_cutoff),
        "calib_cutoff": float(train_cutoff),
        "params": {"challenger": challenger, **params},
        "evaluation": {"arms": evaluation.get("arms"),
                       "scenarios": evaluation.get("scenarios"),
                       "decided": evaluation.get("decided"),
                       "recommendation": rec},
        "status": rec["verdict"],
        "note": ("challenger evaluated against the frozen management "
                 "champion over identical assigned inventory. %s"
                 % rec["not_a_promotion"]),
    }
