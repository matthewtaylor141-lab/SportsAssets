"""THE PAYOUT-STATE DISTRIBUTION INDIRECT PAIRING NEEDS. PURE: NO DB, NO NETWORK.

THE GAP THIS CLOSES. `ACQUIRE_INDIRECT_HEDGE` is valued by
`bettor_funded_decision.indirect_candidate`, which needs a probability on EVERY
row of the structure's payoff table. The registry model predicts one number,
P(both legs win), and `bettor_funded_model.region_probabilities` refuses to
spread `1 - p_middle` without an outside split that no admissible source
supplies. So in production the indirect candidate was always `not_rankable`.

WHAT EVERY ACTION'S VALUE ACTUALLY DEPENDS ON. A region enters any action's
value only through its per-leg payout pair -- (primary cents a_r, hedge cents
b_r). HOLD, DIRECT_EXIT and REDUCE read a_r through the primary marginal;
ACQUIRE reads a_r and b_r. Two regions with the same pair are therefore
indistinguishable to every decision this lane makes, and splitting mass between
them is EV-neutral. So:

  1 `payout_classes`   merges ONLY payout-identical rows into one class per
                       distinct pair, and names each leg's semantic outcome in
                       it (WIN / LOSE / VOID / PARTIAL, cents kept);
  2 `merged_structure` and `merged_position_value` re-state the table with ONE
                       row per class, so a class probability prices it exactly
                       -- no within-class split is needed, and none is invented;
  3 `distribution`     prices each class as
                           (1 - void) x P(primary outcome) x P(class | outcome)
                       where P(primary outcome) is the SAME probability HOLD is
                       valued on, P(class | outcome) is 1 when the table admits
                       one hedge payout for that outcome (structural, not
                       learned) and a learned P(hedge wins | outcome) when it
                       admits exactly two -- {0, 100} -- and the void mass is a
                       measured rate. Anything else is refused by name.

WHAT IT NEVER DOES. No outside split, no uniform fill, no folding a push into a
loss. A push, a tie, a refund or any payout between 0 and 100 on the hedge side
among several hedge payouts is refused (R_HEDGE_OUTCOME_NOT_BINARY): pushes are
preserved by refusing, not by approximating. A primary partial outcome (a
moneyline tie cell) needs its own stated probability or is refused. An integer
total line -- whose push the partition does not isolate -- is refused rather
than priced on a table that misgrades it.
"""

from __future__ import annotations

import math
from fractions import Fraction

from . import bettor_funded_decision as FD
from . import bettor_indirect_structures as IS

VERSION = "PAYOUT_STATES_V1"

#: Each leg's semantic outcome in a class.
WIN = "WIN"
LOSE = "LOSE"
VOID = "VOID"
PARTIAL = "PARTIAL"
#: The outcomes the external primary probability is stated over, given the
#: fixture was played. VOID is priced separately, by the measured rate.
PRIMARY_OUTCOMES = (WIN, LOSE, PARTIAL)

MERGE_RULE = (
    "ROWS ARE MERGED ONLY WHEN THEIR PER-LEG PAYOUTS ARE IDENTICAL -- the same "
    "(primary cents, hedge cents) pair -- AND THEY AGREE ON WHETHER THE FIXTURE "
    "WAS VOID. Every action's value depends on a region only through that pair, "
    "so merging identical pairs is EV-neutral and removes the need for any "
    "within-class split. A VOID row is never merged with a played row even at "
    "equal cents, because the two are priced from different sources (a measured "
    "void rate against the primary probability)")

POSTPONED_BASIS = (
    "POSTPONEMENT IS NOT A TERMINAL PAYOUT: the venue keeps the market open, so "
    "the eventual payout is one of the terminal classes. The distribution is "
    "over terminal outcomes and the postponed state carries zero terminal mass "
    "-- exactly how `indirect_candidate` already admits an unresolved state (only "
    "at probability 0)")

STRUCTURAL_BASIS = ("the payoff table admits one hedge payout given this "
                    "primary outcome")
LEARNED_BASIS = ("the payoff table admits exactly two hedge payouts, {0, 100}, "
                 "given this primary outcome; P(hedge wins | outcome) is the "
                 "approved conditional model's prediction")

IDENTIFIED_STRUCTURAL = "EXTERNAL_PRIMARY×STRUCTURAL"
IDENTIFIED_LEARNED = "EXTERNAL_PRIMARY×LEARNED"
IDENTIFIED_VOID = "MEASURED_VOID_RATE"

#: ── REFUSALS ────────────────────────────────────────────────────────
R_STRUCTURE_IS_UNESTABLISHABLE = FD.R_STRUCTURE_IS_UNESTABLISHABLE
R_NO_TABLE = "THE_STRUCTURE_HAS_NO_PAYOFF_TABLE"
R_ROW_UNDETERMINED = "A_TERMINAL_PAYOFF_ROW_HAS_NO_DETERMINED_PAYOUT"
R_NOT_TWO_LEGS = "A_PAYOFF_ROW_DOES_NOT_CARRY_EXACTLY_TWO_LEG_PAYOUTS"
R_CLASS_ROWS_DISAGREE = "ROWS_WITH_ONE_PAYOUT_PAIR_DISAGREE_ON_THE_JOINT_PAYOUT"
R_INTEGER_TOTAL_PUSH_NOT_ISOLATED = IS.R_INTEGER_TOTAL_PUSH_NOT_ISOLATED
R_TOTAL_LINES_NOT_RECORDED = (
    "A_TOTAL_STRUCTURE_THAT_DOES_NOT_RECORD_ITS_LINES_CANNOT_BE_SHOWN_FREE_OF_AN_"
    "UNISOLATED_PUSH")
R_PRIMARY_PROBABILITY_INVALID = "THE_PRIMARY_PROBABILITY_IS_NOT_A_PROBABILITY"
R_PRIMARY_PARTIAL_OUTCOME_NOT_PRICED = (
    "THE_PRIMARY_LEGS_PARTIAL_OUTCOME_HAS_NO_STATED_PROBABILITY")
R_PRIMARY_OUTCOME_NOT_IN_TABLE = (
    "THE_PRIMARY_PROBABILITY_GIVES_MASS_TO_AN_OUTCOME_THE_TABLE_DOES_NOT_ADMIT")
R_VOID_RATE_NOT_MEASURED = "THE_VOID_RATE_IS_NOT_MEASURED"
R_VOID_CLASS_NOT_UNIQUE = "THE_TABLE_CARRIES_MORE_THAN_ONE_VOID_PAYOUT"
R_CONDITIONAL_NOT_ESTIMATED = (
    "THE_HEDGE_GIVEN_PRIMARY_OUTCOME_CONDITIONAL_IS_NOT_ESTIMATED")
R_CONDITIONAL_INVALID = "THE_HEDGE_GIVEN_PRIMARY_CONDITIONAL_IS_NOT_A_PROBABILITY"
R_HEDGE_OUTCOME_NOT_BINARY = (
    "THE_HEDGE_OUTCOME_GIVEN_THE_PRIMARY_OUTCOME_IS_NOT_BINARY")
R_DISTRIBUTION_DOES_NOT_SUM = "THE_CLASS_PROBABILITIES_DO_NOT_SUM_TO_ONE"
R_POSITION_TABLE_DOES_NOT_MATCH = (
    "THE_POSITION_TABLE_DOES_NOT_MATCH_THE_STRUCTURES_PAYOUT_CLASSES")

_SUM_TOL = 1e-9


def _as_dict(structure) -> dict:
    if structure is None:
        return {}
    return structure if isinstance(structure, dict) else structure.to_dict()


def _outcome(cents: int, *, void: bool) -> str:
    if void:
        return VOID
    if cents == IS.CENTS:
        return WIN
    if cents == 0:
        return LOSE
    return PARTIAL


def _label(a: int, b: int, *, void: bool) -> str:
    return "%s[%d,%d]" % ("VOID" if void else "PAYOUT", a, b)


def _is_total_table(table) -> bool:
    return any(str((r or {}).get("region", "")).startswith("total ")
               for r in table)


def _prob(v) -> float | None:
    """A finite probability in [0, 1], or None. Booleans are not numbers."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(f) or f < 0.0 or f > 1.0:
        return None
    return f


# ═════════════════════════════════════════════════════════════════════
# 1 · PAYOUT CLASSES
# ═════════════════════════════════════════════════════════════════════

def payout_classes(structure) -> dict:
    """GROUP EVERY TERMINAL ROW BY ITS EXACT (PRIMARY, HEDGE) PAYOUT PAIR.

    Index 0 of `per_leg_cents` is the held (primary) leg and index 1 the
    hedge: `bettor_funded_pair_cycle.discover` classifies
    `classify(held_leg, candidate_leg)` and `bettor_pair_observations` records
    the held leg as the primary, so the order is the one every caller builds.

    Never raises. Returns `{ok, refusal, why, classes, unresolved, by_region,
    merge_rule}`.
    """
    d = _as_dict(structure)
    out: dict = {"version": VERSION, "merge_rule": MERGE_RULE,
                 "classes": [], "unresolved": [], "by_region": {}}
    table = [dict(r) for r in (d.get("table") or ())]
    if (str(d.get("taxonomy")) == IS.UNESTABLISHABLE
            or d.get("missing_facts") or d.get("undetermined_regions")):
        return dict(out, ok=False, refusal=R_STRUCTURE_IS_UNESTABLISHABLE,
                    missing_facts=list(d.get("missing_facts") or ()),
                    undetermined_regions=list(
                        d.get("undetermined_regions") or ()),
                    why=("the classifier did not establish this structure, so "
                         "its rows cannot be priced"))
    if not table:
        return dict(out, ok=False, refusal=R_NO_TABLE,
                    why="there is no payoff table to group")
    # ── THE INTEGER-TOTAL DEFECT, REFUSED BY NAME ────────────────────
    grading = list(d.get("leg_grading") or ())
    bad = [g.get("condition_id") for g in grading
           if g.get("variable") == IS.VAR_TOTAL and g.get("line") is not None
           and Fraction(str(g["line"])).denominator == 1]
    if bad:
        return dict(out, ok=False, refusal=R_INTEGER_TOTAL_PUSH_NOT_ISOLATED,
                    legs=bad,
                    why=("an integer total line can push and the total "
                         "partition breaks only at floor(line), so the push is "
                         "folded into a neighbouring cell. Pricing that table "
                         "would grade a push as a win or a loss"))
    if not grading and _is_total_table(table):
        return dict(out, ok=False, refusal=R_TOTAL_LINES_NOT_RECORDED,
                    why=("the table is over the TOTAL and the structure does "
                         "not record its legs' lines; 'total in [0, 20]' is "
                         "the same label for OVER 20.5 and OVER 20, and only "
                         "the second has a push the partition misgrades"))
    classes: dict = {}
    order: list = []
    for r in table:
        region = str(r.get("region") or "")
        if r.get("state") == IS.STATE_POSTPONED:
            out["unresolved"].append({
                "region": region, "state": IS.STATE_POSTPONED,
                "probability": 0.0, "basis": POSTPONED_BASIS})
            continue
        per_leg = list(r.get("per_leg_cents") or ())
        if len(per_leg) != 2:
            return dict(out, ok=False, refusal=R_NOT_TWO_LEGS, region=region,
                        why=("a pairing class is a (primary, hedge) pair; this "
                             "row carries %d leg payouts" % len(per_leg)))
        if (not r.get("determined") or r.get("joint_cents") is None
                or any(p is None for p in per_leg)):
            return dict(out, ok=False, refusal=R_ROW_UNDETERMINED,
                        region=region,
                        why=("a terminal row with no determined payout cannot "
                             "be placed in a class, and leaving it out would "
                             "treat an outcome that can happen as impossible"))
        a, b = int(per_leg[0]), int(per_leg[1])
        void = r.get("state") == IS.STATE_VOID
        key = (a, b, void)
        if key not in classes:
            classes[key] = {
                "label": _label(a, b, void=void),
                "per_leg_cents": [a, b],
                "joint_cents": r.get("joint_cents"),
                "void": void,
                "primary": {"outcome": _outcome(a, void=void), "cents": a},
                "hedge": {"outcome": _outcome(b, void=void), "cents": b},
                "merged_regions": [], "states": []}
            order.append(key)
        c = classes[key]
        if c["joint_cents"] != r.get("joint_cents"):
            return dict(out, ok=False, refusal=R_CLASS_ROWS_DISAGREE,
                        region=region,
                        why=("two rows with the same per-leg payouts carry "
                             "different joint payouts (%r and %r), so the "
                             "table's quantities are not one per leg"
                             % (c["joint_cents"], r.get("joint_cents"))))
        c["merged_regions"].append(region)
        if r.get("state") not in c["states"]:
            c["states"].append(r.get("state"))
        out["by_region"][region] = c["label"]
    out["classes"] = [classes[k] for k in order]
    if not out["classes"]:
        return dict(out, ok=False, refusal=R_NO_TABLE,
                    why="the table has no terminal row")
    return dict(out, ok=True, refusal=None,
                rows=len(table), terminal_rows=sum(
                    len(c["merged_regions"]) for c in out["classes"]),
                why=("%d terminal row(s) merged into %d payout class(es); %d "
                     "unresolved state(s) carried at zero terminal mass"
                     % (sum(len(c["merged_regions"]) for c in out["classes"]),
                        len(out["classes"]), len(out["unresolved"]))))


# ═════════════════════════════════════════════════════════════════════
# 2 · THE TABLE RE-STATED ONE ROW PER CLASS
# ═════════════════════════════════════════════════════════════════════

def merged_structure(structure, classes: dict) -> dict:
    """A COPY OF THE STRUCTURE WHOSE TABLE HAS ONE ROW PER PAYOUT CLASS.

    Postponed rows are kept as they are. `both_win_regions` and
    `both_lose_regions` are recomputed over the class rows by the classifier's
    own rule; the minimum and maximum payout cannot change (they are over the
    same set of joint payouts) and are left as the classifier stated them.
    """
    d = dict(_as_dict(structure))
    rows = []
    for c in classes.get("classes") or ():
        rows.append({"region": c["label"],
                     "state": (c["states"][0] if len(c["states"]) == 1
                               else "MERGED"),
                     "states": list(c["states"]),
                     "per_leg_cents": list(c["per_leg_cents"]),
                     "joint_cents": c["joint_cents"],
                     "determined": True,
                     "merged_regions": list(c["merged_regions"])})
    for u in classes.get("unresolved") or ():
        orig = next((dict(r) for r in (d.get("table") or ())
                     if r.get("region") == u["region"]), None)
        rows.append(orig or {"region": u["region"], "state": u["state"],
                             "per_leg_cents": [None, None],
                             "joint_cents": None, "determined": False})
    d["table"] = tuple(rows)
    det = [r for r in rows if r.get("determined")]
    d["both_win_regions"] = tuple(r["region"] for r in det
                                  if r["joint_cents"] >= 2 * IS.CENTS)
    d["both_lose_regions"] = tuple(r["region"] for r in det
                                   if r["joint_cents"] == 0)
    d["merge_rule"] = MERGE_RULE
    d["merged_from_rows"] = sum(len(c["merged_regions"])
                                for c in classes.get("classes") or ())
    return d


def merged_position_value(position_value: dict, classes: dict) -> dict:
    """`position_worst_case`'s result with its regions merged by class.

    The position table is built at the REAL quantities, so its joint payouts
    differ from the structure's unit table -- but its per-leg cents are the
    same per-unit payout functions over the same partition, so each of its
    regions belongs to exactly one class. That is CHECKED, not assumed: a
    region the classes do not name, or one whose per-leg cents differ from its
    class's, refuses. The floor fields are carried unchanged; merging rows with
    identical payouts cannot change a minimum.
    """
    pv = dict(position_value or {})
    out = {"ok": False}
    if not pv.get("ok"):
        return dict(out, refusal=pv.get("refusal") or
                    R_POSITION_TABLE_DOES_NOT_MATCH,
                    why="the position itself was not valued")
    by_label = {c["label"]: c for c in classes.get("classes") or ()}
    by_region = dict(classes.get("by_region") or {})
    merged: dict = {}
    order = []
    for r in pv.get("regions") or ():
        label = by_region.get(r.get("region"))
        if label is None:
            return dict(out, refusal=R_POSITION_TABLE_DOES_NOT_MATCH,
                        region=r.get("region"),
                        why=("the position values region %r, which no payout "
                             "class names -- the two tables were not built on "
                             "one partition" % r.get("region")))
        c = by_label[label]
        if [int(x) for x in (r.get("per_leg_cents") or ())] != \
                list(c["per_leg_cents"]):
            return dict(out, refusal=R_POSITION_TABLE_DOES_NOT_MATCH,
                        region=r.get("region"),
                        why=("region %r pays %r per unit in the position and "
                             "%r in its class" % (r.get("region"),
                                                  r.get("per_leg_cents"),
                                                  c["per_leg_cents"])))
        if label not in merged:
            merged[label] = {"region": label, "state": r.get("state"),
                             "payout_usd": r.get("payout_usd"),
                             "net_usd": r.get("net_usd"),
                             "per_leg_cents": list(c["per_leg_cents"]),
                             "merged_regions": []}
            order.append(label)
        m = merged[label]
        if (m["payout_usd"] != r.get("payout_usd")
                or m["net_usd"] != r.get("net_usd")):
            return dict(out, refusal=R_CLASS_ROWS_DISAGREE,
                        region=r.get("region"),
                        why="two position rows of one class pay differently")
        m["merged_regions"].append(r.get("region"))
    missing = [lbl for lbl in by_label if lbl not in merged]
    if missing:
        return dict(out, refusal=R_POSITION_TABLE_DOES_NOT_MATCH,
                    classes_not_in_the_position=missing,
                    why=("the structure admits payout class(es) %s that the "
                         "position table does not value" % missing))
    return dict(pv, regions=[merged[k] for k in order],
                regions_before_merge=len(pv.get("regions") or ()),
                binding_class=by_region.get(pv.get("binding_region")),
                merge_rule=MERGE_RULE)


# ═════════════════════════════════════════════════════════════════════
# 3 · THE DISTRIBUTION OVER CLASSES
# ═════════════════════════════════════════════════════════════════════

def distribution(classes: dict, *, primary: dict | None,
                 conditional: dict | None, void: dict | None) -> dict:
    """P(class) = (1 - void) x P(primary outcome) x P(class | primary outcome).

    `primary`      {"p_win": P(primary WINS | not void), "source", "basis",
                   optionally "p_partial"} -- the probability HOLD is valued
                   on. `p_partial` is required iff a played class pays the
                   primary between 0 and 100 (a tie cell).
    `conditional`  {outcome: P(hedge WINS | outcome)} for each primary outcome
                   whose classes are exactly two with hedge payouts {0, 100}.
    `void`         {"rate", "n_fixtures", "upper_95", "source"}, required iff
                   the table carries a VOID class.

    Pure; never raises.
    """
    out: dict = {"version": VERSION, "ok": False}
    if not (classes or {}).get("ok"):
        return dict(out, refusal=(classes or {}).get("refusal") or R_NO_TABLE,
                    why=(classes or {}).get("why"))
    cls = list(classes.get("classes") or ())
    played = [c for c in cls if not c["void"]]
    voids = [c for c in cls if c["void"]]
    primary = dict(primary or {})
    conditional = dict(conditional or {})

    # ── THE PRIMARY MARGINAL: THE ONE HOLD IS VALUED ON ──────────────
    p_win = _prob(primary.get("p_win"))
    if p_win is None:
        return dict(out, refusal=R_PRIMARY_PROBABILITY_INVALID,
                    stated=primary.get("p_win"),
                    why="P(primary wins | not void) must be a finite number "
                        "in [0, 1]")
    partial_cents = sorted({c["primary"]["cents"] for c in played
                            if c["primary"]["outcome"] == PARTIAL})
    p_partial = 0.0
    if partial_cents:
        if len(partial_cents) > 1:
            return dict(out, refusal=R_PRIMARY_PARTIAL_OUTCOME_NOT_PRICED,
                        partial_primary_payouts=partial_cents,
                        why=("the table pays the primary leg %s cents in "
                             "different played outcomes; one stated partial "
                             "probability cannot price them apart"
                             % partial_cents))
        p_partial = _prob(primary.get("p_partial"))
        if p_partial is None:
            return dict(out, refusal=R_PRIMARY_PARTIAL_OUTCOME_NOT_PRICED,
                        partial_primary_payouts=partial_cents,
                        why=("a played outcome pays the primary leg %d cents "
                             "(e.g. a tie cell). Its probability is not "
                             "stated, and it is neither a win nor a loss"
                             % partial_cents[0]))
    elif primary.get("p_partial") not in (None, 0, 0.0):
        return dict(out, refusal=R_PRIMARY_OUTCOME_NOT_IN_TABLE,
                    outcome=PARTIAL,
                    why=("a partial primary outcome was given probability "
                         "%r and the table admits none" % primary.get(
                             "p_partial")))
    p_lose = 1.0 - p_win - p_partial
    if p_lose < -_SUM_TOL:
        return dict(out, refusal=R_PRIMARY_PROBABILITY_INVALID,
                    why=("P(win) %r + P(partial) %r exceeds one"
                         % (p_win, p_partial)))
    p_lose = max(0.0, p_lose)
    p_outcome = {WIN: p_win, LOSE: p_lose, PARTIAL: p_partial}
    by_outcome = {o: [c for c in played if c["primary"]["outcome"] == o]
                  for o in PRIMARY_OUTCOMES}
    for o in PRIMARY_OUTCOMES:
        if p_outcome[o] > 0.0 and not by_outcome[o]:
            return dict(out, refusal=R_PRIMARY_OUTCOME_NOT_IN_TABLE,
                        outcome=o, p=p_outcome[o],
                        why=("the primary probability puts %r on the primary "
                             "leg's %s outcome and no played row of the table "
                             "has it -- the probability and the table describe "
                             "different contracts" % (p_outcome[o], o)))

    # ── THE VOID MASS: MEASURED, NEVER ASSUMED ───────────────────────
    v = 0.0
    void_basis: dict = {"used": False}
    if len(voids) > 1:
        return dict(out, refusal=R_VOID_CLASS_NOT_UNIQUE,
                    classes=[c["label"] for c in voids],
                    why="one void rate cannot price two void payouts")
    if voids:
        vr = dict(void or {})
        rate = _prob(vr.get("rate"))
        upper = _prob(vr.get("upper_95"))
        try:
            n_fx = int(vr.get("n_fixtures") or 0)
        except (TypeError, ValueError):
            n_fx = 0
        # A MEASURED RATE STATES ITS RATE, ITS FIXTURE COUNT, ITS UPPER BOUND
        # AND ITS SOURCE. A bare number is an assumption with a decimal point.
        if (not vr or rate is None or upper is None or upper < rate
                or n_fx < 1 or not vr.get("source")):
            return dict(out, refusal=R_VOID_RATE_NOT_MEASURED,
                        supplied=({k: vr.get(k) for k in (
                            "rate", "n_fixtures", "upper_95", "source")}
                                  if vr else None),
                        why=("the table pays a cancelled fixture (%s) and no "
                             "measured void rate -- rate, fixtures counted, "
                             "upper bound and source -- was supplied; its mass "
                             "is not assumed" % voids[0]["label"]))
        v = rate
        void_basis = {"used": True, "rate": rate,
                      "n_fixtures": vr.get("n_fixtures"),
                      "n_void_fixtures": vr.get("n_void_fixtures"),
                      "upper_95": vr.get("upper_95"),
                      "source": vr.get("source"), "basis": vr.get("basis")}
    elif void:
        void_basis = {
            "used": False, "rate_supplied": (void or {}).get("rate"),
            "why": ("the payoff table admits no VOID outcome (cancellation was "
                    "declared impossible for this fixture), so every class "
                    "probability is conditional on the fixture being played; "
                    "the measured rate is recorded and not applied")}

    # ── THE HEDGE, GIVEN EACH PRIMARY OUTCOME ────────────────────────
    probs: dict = {}
    identified: dict = {}
    cond_basis: dict = {}
    for o in PRIMARY_OUTCOMES:
        group = by_outcome[o]
        if not group:
            continue
        if len(group) == 1:
            c = group[0]
            probs[c["label"]] = (1.0 - v) * p_outcome[o] * 1.0
            identified[c["label"]] = IDENTIFIED_STRUCTURAL
            cond_basis[o] = {"kind": "STRUCTURAL", "basis": STRUCTURAL_BASIS,
                             "classes": {c["label"]: 1.0}}
            continue
        hedge_cents = sorted(c["hedge"]["cents"] for c in group)
        if len(group) == 2 and hedge_cents == [0, IS.CENTS]:
            q_raw = conditional.get(o)
            if q_raw is None:
                return dict(out, refusal=R_CONDITIONAL_NOT_ESTIMATED,
                            outcome=o,
                            classes=[c["label"] for c in group],
                            why=("given the primary leg's %s outcome the table "
                                 "admits two hedge payouts {0, 100}; "
                                 "P(hedge wins | %s) is not estimated" % (o, o)))
            q = _prob(q_raw)
            if q is None:
                return dict(out, refusal=R_CONDITIONAL_INVALID, outcome=o,
                            stated=q_raw)
            win_c = next(c for c in group if c["hedge"]["cents"] == IS.CENTS)
            lose_c = next(c for c in group if c["hedge"]["cents"] == 0)
            probs[win_c["label"]] = (1.0 - v) * p_outcome[o] * q
            probs[lose_c["label"]] = (1.0 - v) * p_outcome[o] * (1.0 - q)
            identified[win_c["label"]] = IDENTIFIED_LEARNED
            identified[lose_c["label"]] = IDENTIFIED_LEARNED
            cond_basis[o] = {"kind": "LEARNED", "basis": LEARNED_BASIS,
                             "p_hedge_wins": q,
                             "classes": {win_c["label"]: q,
                                         lose_c["label"]: 1.0 - q}}
            continue
        return dict(out, refusal=R_HEDGE_OUTCOME_NOT_BINARY, outcome=o,
                    hedge_payouts=hedge_cents,
                    classes=[c["label"] for c in group],
                    why=("given the primary leg's %s outcome the table admits "
                         "hedge payouts %s. Only a single payout (structural) "
                         "or exactly {0, 100} (one learned probability) is "
                         "priced; a push or partial payout among several is "
                         "refused, never folded into a loss" % (o, hedge_cents)))
    for c in voids:
        probs[c["label"]] = v
        identified[c["label"]] = IDENTIFIED_VOID

    # ── VALIDATED, NOT ROUNDED INTO SHAPE ────────────────────────────
    bad = {k: p for k, p in probs.items()
           if not math.isfinite(p) or p < -_SUM_TOL or p > 1.0 + _SUM_TOL}
    total = sum(probs.values())
    if bad or abs(total - 1.0) > _SUM_TOL:
        return dict(out, refusal=R_DISTRIBUTION_DOES_NOT_SUM,
                    sums_to=total, out_of_range=bad)
    probs = {k: min(1.0, max(0.0, p)) for k, p in probs.items()}
    implied = sum(probs[c["label"]] for c in played
                  if c["primary"]["outcome"] == WIN)
    unresolved = {u["region"]: 0.0 for u in classes.get("unresolved") or ()}
    return dict(
        out, ok=True, refusal=None,
        probabilities=probs,
        unresolved_probabilities=unresolved,
        classes=[{k: c[k] for k in ("label", "per_leg_cents", "void",
                                    "primary", "hedge", "merged_regions")}
                 for c in cls],
        basis={
            "primary": {"p_win": p_win, "p_partial": p_partial,
                        "p_lose": p_lose,
                        "is": "P(primary outcome | the fixture is not void)",
                        "source": primary.get("source"),
                        "basis": primary.get("basis")},
            "conditional": cond_basis,
            "void": void_basis,
            "postponed": POSTPONED_BASIS},
        identified=identified,
        indistinguishable={c["label"]: list(c["merged_regions"]) for c in cls},
        implied_primary_marginal=implied,
        implied_primary_marginal_is=(
            "(1 - void rate) x P(primary wins | not void): the unconditional "
            "probability the primary leg pays in full under this distribution. "
            "HOLD is valued on P(primary wins) itself, so the two differ by "
            "exactly the void mass the table admits"),
        sums_to=total,
        no_outside_split=("no outside split and no uniform fill: every class "
                          "is priced from the primary marginal, the table's "
                          "own structure, a learned conditional or a measured "
                          "void rate"))
