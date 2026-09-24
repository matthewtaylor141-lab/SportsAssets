"""THE ACCEPTANCE GATE, in ONE place so two callers cannot drift.

MOVED HERE, NOT REWRITTEN. Every rule below is the text that ran in
`tools/desk_experiments.py`, unchanged: the same conditions, the same
constants, the same wording in the rejection reasons. The verdicts it
produced before this move stand exactly as it produced them.

WHY IT MOVED. `backend/tools/` is not copied into the API image (see
backend/Dockerfile: sportsassets, migrations, start.sh and the pinned
research artifacts, and nothing else). The runtime learning loop needs
this gate, and the two ways to give it one are to import it or to write
a second copy. A second copy is a competing research stack that would
drift from this one on the first edit, so the gate lives in the package
and the offline tool imports it from here.

V1 (`gate`) IS KEPT ALONGSIDE V2, deliberately. Cycle 1's verdicts were
produced by V1 and rescoring a finished cycle under a gate written after
seeing its results is exactly the tuning this loop exists to prevent.
V1 is therefore preserved as a historical record, not as an alternative
a caller may pick for a fresh comparison.
"""

from __future__ import annotations

# The declared execution-scenario grid. P_FILL is NOT_IDENTIFIED, so a
# candidate must win under EVERY one of these or it has found a
# liquidity assumption rather than an edge.
SCENARIOS = (0.10, 0.25, 0.50)

MIN_DECIDED = 50                        # eligibility floor: no inactivity wins
MIN_TURNOVER_FRAC = 0.50                # V2: a candidate may not win by shrinking


def gate_v2(base, cand, n_decided) -> dict:
    """GATE V2, declared in cycle 2's commit BEFORE cycle 2 was run, and
    earned by four things cycle 1 measured about GATE V1 itself.

    V1 ranked on the ZERO-MARKED LOWER TERMINAL BOUND. Cycle 1 showed
    that statistic cannot do the job:

      (a) NO RESOLVING POWER. lower = realized - unresolved_cost, and on
          this corpus the bound spans ~$34k on $100k of cash while the
          realized figures it is meant to separate are +-$1k. The
          instrument is 9-34x wider than the effect.
      (b) IT IS A DEGENERATE OBJECTIVE. Minimising it means holding no
          inventory, so its optimum is "trade nothing". MIN_DECIDED=50
          was supposed to stop that and did not bind -- every candidate
          decided 300-1800 orders. Both of V1's TRAIN accepts, C1 and
          C4, were DOSE REDUCTIONS: narrower band, smaller clip. Less
          of a losing strategy is not an edge.
      (c) IT INVERTED THE SIGN. V1 called baseline VALIDATION "worse" at
          queue_share 0.50 (lower -4435 vs -3315) while its realized
          P&L ROSE (+974 vs +41). Marking open inventory at zero made
          more trading look worse when more trading had earned more.

    So V2 ranks on the COST-MARKED terminal -- which is exactly
    `realized`, since cash + inventory_cost - realized == starting_cash
    -- and keeps the bounds as a REQUIRED DISCLOSURE rather than the
    selector. Then it closes the three holes that opens:

      G1 ACTIVITY AND SIZE. An order-count floor alone lets a candidate
         win by shrinking. Turnover must also stay within 50% of the
         baseline's, so a dose reduction has to beat the baseline at
         comparable size or not at all.
      G3 INVENTORY IS NOT A HIDING PLACE. Cost-marked P&L can be
         flattered by parking capital in unresolved legs, so
         unresolved_cost may not exceed the baseline's by >10%.
      G4 THE MARGIN MUST EXCEED THE UNCERTAINTY. If the improvement in
         realized is smaller than the change in bound WIDTH, the result
         is NOT_RESOLVED, not accepted.
      G7 SIGN STABILITY ACROSS PARTITIONS. Cycle 1's sharpest finding
         was that d(realized)/d(queue_share) is NEGATIVE on TRAIN and
         POSITIVE on VALIDATION for the same policy. A candidate whose
         response to the one execution assumption we cannot identify
         flips sign between partitions has not shown an edge, and the
         caller supplies both partitions so this can be tested.

    V2 IS NOT APPLIED RETROACTIVELY TO CYCLE 1. Cycle 1's verdicts stand
    as V1 produced them. Rescoring a finished cycle under a gate written
    after seeing its results is the tuning this loop exists to prevent.
    """
    reasons, ok = [], True
    if n_decided < MIN_DECIDED:
        ok = False
        reasons.append("INELIGIBLE: %d decided orders < %d floor -- "
                       "inactivity is not performance"
                       % (n_decided, MIN_DECIDED))
    for b, c in zip(base, cand):
        if c["turnover_usd"] < b["turnover_usd"] * MIN_TURNOVER_FRAC:
            ok = False
            reasons.append(
                "DOSE REDUCTION, NOT AN EDGE: turnover %.0f is %.0f%% of "
                "the baseline's %.0f. Trading less of a losing strategy "
                "loses less; that is arithmetic, not improvement."
                % (c["turnover_usd"],
                   100.0 * c["turnover_usd"] / max(b["turnover_usd"], 1e-9),
                   b["turnover_usd"]))
            break
    wins = [c["realized_pnl_usd"] > b["realized_pnl_usd"]
            for b, c in zip(base, cand)]
    if not all(wins):
        ok = False
        reasons.append("NOT ROBUST: improves cost-marked P&L in %d of %d "
                       "scenarios; a candidate that needs a particular "
                       "fill assumption has found a liquidity assumption, "
                       "not an edge" % (sum(wins), len(wins)))
    for b, c in zip(base, cand):
        if c["unresolved_cost_usd"] > b["unresolved_cost_usd"] * 1.10 + 1.0:
            ok = False
            reasons.append("CAPITAL PARKED, NOT EARNED: unresolved cost "
                           "%.0f vs %.0f. Cost-marked P&L can be flattered "
                           "by holding rather than closing."
                           % (c["unresolved_cost_usd"],
                              b["unresolved_cost_usd"]))
            break
    for b, c in zip(base, cand):
        margin = c["realized_pnl_usd"] - b["realized_pnl_usd"]
        dwidth = abs((c["terminal_upper_usd"] - c["terminal_lower_usd"])
                     - (b["terminal_upper_usd"] - b["terminal_lower_usd"]))
        if margin <= dwidth:
            ok = False
            reasons.append("NOT_RESOLVED: the %.0f improvement is smaller "
                           "than the %.0f change in the terminal-value "
                           "bound width. The instrument cannot see an "
                           "effect this size." % (margin, dwidth))
            break
    for b, c in zip(base, cand):
        if c["max_drawdown_usd"] > b["max_drawdown_usd"] * 1.10 + 1.0:
            ok = False
            reasons.append("WORSE DRAWDOWN: %.2f vs %.2f"
                           % (c["max_drawdown_usd"], b["max_drawdown_usd"]))
            break
    for b, c in zip(base, cand):
        if c["peak_committed_usd"] > b["peak_committed_usd"] * 1.10 + 1.0:
            ok = False
            reasons.append("MORE CAPITAL AT RISK: %.2f vs %.2f"
                           % (c["peak_committed_usd"],
                              b["peak_committed_usd"]))
            break
    if not all(c["invariant_ok"] for c in cand):
        ok = False
        reasons.append("LEDGER DOES NOT RECONCILE")
    return {"accepted": ok,
            "reasons": reasons or ["passed every declared V2 condition"]}


def slope_sign(rows) -> int:
    """sign of d(realized)/d(queue_share) across the scenario grid."""
    d = rows[-1]["realized_pnl_usd"] - rows[0]["realized_pnl_usd"]
    return 0 if abs(d) < 1e-9 else (1 if d > 0 else -1)


def sign_stability(train_rows, valid_rows) -> dict:
    """G7. The same policy must respond to queue_share the same way in
    both partitions, or its response is partition noise.

    This is the one criterion cycle 1 could not have had, because it
    tests a property cycle 1 is what discovered.
    """
    st, sv = slope_sign(train_rows), slope_sign(valid_rows)
    return {
        "train_slope_sign": st, "validation_slope_sign": sv,
        "stable": st == sv,
        "detail": ("d(realized)/d(queue_share) is %s on TRAIN and %s on "
                   "VALIDATION" % ({1: "POSITIVE", -1: "NEGATIVE",
                                    0: "FLAT"}[st],
                                   {1: "POSITIVE", -1: "NEGATIVE",
                                    0: "FLAT"}[sv])),
    }


def gate(base, cand, n_decided) -> dict:
    """THE ACCEPTANCE GATE, declared before any candidate was run.

    Four conditions, ALL required:
      1. eligible      at least MIN_DECIDED decided orders, so a policy
                       that traded nothing cannot pass
      2. robust        beats the baseline's LOWER terminal bound under
                       EVERY declared scenario, not on average
      3. no worse risk drawdown and peak committed capital not worse
                       than the baseline by more than 10%
      4. reconciles    the ledger identity holds in every scenario
    """
    reasons, ok = [], True
    if n_decided < MIN_DECIDED:
        ok = False
        reasons.append("INELIGIBLE: %d decided orders < %d floor -- "
                       "inactivity is not performance"
                       % (n_decided, MIN_DECIDED))
    wins = [c["terminal_lower_vs_start"] > b["terminal_lower_vs_start"]
            for b, c in zip(base, cand)]
    if not all(wins):
        ok = False
        reasons.append("NOT ROBUST: improves the lower terminal bound in "
                       "%d of %d scenarios; a candidate that needs a "
                       "particular fill assumption has found a liquidity "
                       "assumption, not an edge" % (sum(wins), len(wins)))
    for b, c in zip(base, cand):
        if c["max_drawdown_usd"] > b["max_drawdown_usd"] * 1.10 + 1.0:
            ok = False
            reasons.append("WORSE DRAWDOWN: %.2f vs %.2f"
                           % (c["max_drawdown_usd"], b["max_drawdown_usd"]))
            break
    for b, c in zip(base, cand):
        if c["peak_committed_usd"] > b["peak_committed_usd"] * 1.10 + 1.0:
            ok = False
            reasons.append("MORE CAPITAL AT RISK: %.2f vs %.2f"
                           % (c["peak_committed_usd"],
                              b["peak_committed_usd"]))
            break
    if not all(c["invariant_ok"] for c in cand):
        ok = False
        reasons.append("LEDGER DOES NOT RECONCILE")
    return {"accepted": ok,
            "reasons": reasons or ["passed every declared condition"]}
