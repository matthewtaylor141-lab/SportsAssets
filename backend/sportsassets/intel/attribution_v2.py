"""E2 · ALPHA ATTRIBUTION V2 (SHADOW). Extends intel/attribution.py (V1).

V1 (attribution.attribute) separates, per position, reconciled to cash:

  model edge q(p - d) + execution edge -(q(v - d)) - f + management
      + settlement + outcome variance = realized P&L

V2 (owner red-team acceptance addendum, specs/ALPHA_ATTRIBUTION_V2.md) asks
WHERE THE MODEL EDGE CAME FROM: was the right opportunity chosen (SELECTION),
was the right amount put on it (ALLOCATION), and what did the capital's
time cost (CAPITAL EFFICIENCY) -- WITHOUT DOUBLE COUNTING:

  selection   chooses WHAT          (which opportunity, vs the alternatives)
  allocation  chooses HOW MUCH      (the planned quantity, vs a benchmark
                                     allocation under the SAME hard rails)
  execution   determines HOW WELL   (price vs plan, fees, and the quantity
              it was acquired        that did not fill)
  management  determines HOW it was (Xavier's actions vs holding to
              handled after entry    settlement -- V1's term, unchanged)

THE IDENTITY (exact, per position; every symbol is a recorded value):

  q        filled entry quantity          q_plan  planned (ordered) quantity
  p        decision probability           d       decision's planned price
  v        entry fill VWAP                f       entry fees
  pi       payoff per contract            M       management (V1)
  r_c      = (p - d) / d                  the chosen opportunity's expected
                                          return per dollar at plan
  K_ref    the PRIMARY benchmark allocation (EQUAL_ALLOCATION under the
           same rails), USD
  r_bench  the mean expected return per dollar of the CONTEMPORANEOUS
           QUALIFIED ALTERNATIVES (the pre-allocation tape at the decision
           clock: opportunities an allocator could legally take under the
           same hard rules); the tape read and holding none -> 0, the
           alternative was no-trade; a tape that does not cover the clock,
           or an admissible alternative whose economics were never recorded,
           -> UNAVAILABLE (never a measured "none")

  opportunity_set  K_ref * r_bench                 the average alternative
  selection        K_ref * (r_c - r_bench)          WHAT
  allocation       (q_plan * d - K_ref) * r_c       HOW MUCH
  execution        -(q (v - d)) - f + (q - q_plan)(p - d)   HOW WELL
  management       M                                HOW HANDLED
  settlement       q (pi - p)  EXCEPTIONAL settlement, else 0
  probability      q (pi - p)  ORDINARY settlement (the coin plus the model's
                               probability error), else 0

  opportunity_set + selection + allocation = q_plan (p - d)
  + execution                              = q (p - v) - f
  + management + settlement + probability  = q (pi - v) - f + M = realized

The algebra is exact for ANY numeric type: `decompose` uses no float() and
tests/test_alpha_attribution_v2.py proves it with fractions.Fraction (==,
not a tolerance) and with production-shaped float rows (<= 1e-9, and the
realized total reconciles to the cash legs within a cent).

NO DOUBLE COUNTING, BY CONSTRUCTION. Each term reads a disjoint decision:
the alternatives move only opportunity_set/selection; the planned quantity
moves allocation against execution's fill shortfall (their sum is fixed by
the fill); fill prices and fees move only execution; sales and hedges move
only management; the settlement class routes q(pi - p) to exactly one of
settlement / probability. A test perturbs each input and checks that only
its own terms move.

ALLOCATION COMPARATORS (SHADOW, never summed into the identity). The legacy
sizing actually used and Allie's allocation (SHADOW_PENDING_OWNER_APPROVAL,
not authoritative) are each compared with LEGACY_SIZING, ALLIE,
EQUAL_ALLOCATION, ROI_ONLY_RANKING, CAPITAL_HOUR_RANKING and
RESERVE_NO_ALLOCATION: (K_policy - K_benchmark) * r_c, ex ante. Every
benchmark K is clamped by the SAME hard rails here (the per-order cap, the
usable idle capital after the hedge reserve, the per-market and per-fixture
concentration headroom, the concurrent-group slot, and the executable
capacity), so the comparison is under identical rails by construction; a
CONFIGURED rail whose input is unmeasured (`rails["unmeasured"]`) makes every
benchmark UNAVAILABLE rather than clamping some by fewer rails.

ONE UNIT (red-team finding: legacy K was q_plan x d while the benchmarks were
capital required = cost + fees, so equal quantities showed a spurious
-fees x r_c allocation alpha). Every K in the identity and the matrix is in
PLAN-COST units, contracts x d: a benchmark given in CAPITAL units
(`allocation_unit = CAPITAL_REQUIRED_USD`, the replay's) is clamped by the
rails in capital units, converted to contracts at the decision's capital per
contract (capital required / planned qty, fees included) and priced at d.
Equal quantities therefore give exactly zero allocation alpha.

CAPITAL EFFICIENCY (a second, separate identity -- not a P&L term).
realized profit per capital-hour; the capital charge = position
capital-hours x the hurdle per capital-hour of the pre-allocation qualified
tape (idle cash is NOT free: the hurdle is the option value of the next
qualified opportunity); economic value = realized - charge (exact). Beside
it: the marginal qualified opportunities actually available while the
capital was held, the entries refused because cash was held by prior
allocations, and the missed executable EV attributable to this position's
occupied capital. These price OTHER opportunities, so adding them to the
realized P&L would count one dollar twice; `in_pnl_identity` is False and
says why.

UNAVAILABLE IS A REASON, NEVER A ZERO. A term whose inputs are missing is
null with its reason in `unmeasured`; the identity is then claimed at the
level the measured terms support (FULL, COMBINED_WHAT_AND_HOW_MUCH, V1) or
not at all. A measured zero (no alternative existed; no management action;
an entry that never filled and is terminal) names its basis.

INVESTMENT ONLY for production confidence: every row carries its sleeve and
`production_confidence_eligible` is True only for INVESTMENT.

Pure: imports only the standard library and this package (pinned by
tests/test_intel_is_shadow_only.py).
"""
from __future__ import annotations

from . import attribution as A
from . import common as C

VERSION = "INTEL_ALPHA_ATTRIBUTION_V2"
INVESTMENT = "INVESTMENT"
IDENTITY_TERMS = ("opportunity_set_usd", "selection_usd", "allocation_usd",
                  "execution_usd", "management_usd", "settlement_usd",
                  "probability_usd")
COMBINED_TERMS = ("what_and_how_much_usd", "execution_usd", "management_usd",
                  "settlement_usd", "probability_usd")
V1_TERMS = ("model_edge_usd", "execution_edge_usd", "management_usd",
            "settlement_usd", "outcome_variance_usd")
FULL, COMBINED, V1_LEVEL = "FULL", "COMBINED_WHAT_AND_HOW_MUCH", "V1"

LEGACY, ALLIE = "LEGACY_SIZING", "ALLIE"
EQUAL, ROI, CAPHOUR, RESERVE = ("EQUAL_ALLOCATION", "ROI_ONLY_RANKING",
                                "CAPITAL_HOUR_RANKING",
                                "RESERVE_NO_ALLOCATION")
BENCHMARKS = (LEGACY, ALLIE, EQUAL, ROI, CAPHOUR, RESERVE)
POLICIES = (LEGACY, ALLIE)
PRIMARY_BENCHMARK = EQUAL
RAILS = ("hard_rail_usd", "capacity_usd", "idle_capital_usd",
         "market_headroom_usd", "fixture_headroom_usd", "group_slot_usd")
PLAN_COST, CAPITAL = "PLAN_COST_USD", "CAPITAL_REQUIRED_USD"
#: float identity tolerance (the algebra is exact; this is float rounding)
IDENTITY_EPS = 1e-9

R_NO_FILL_OPEN = "ENTRY_ORDER_NOT_TERMINAL_UNFILLED_AT_THE_HORIZON"
R_NO_ORDER = "ENTER_WITHOUT_A_RECORDED_ORDER_OR_REFUSAL"
R_NO_CPC = "CAPITAL_PER_CONTRACT_UNMEASURED"
R_NO_PLAN = "NO_PLANNED_QUANTITY_RECORDED"
R_NO_P = "DECISION_CARRIES_NO_PROBABILITY"
R_NO_D = "DECISION_CARRIES_NO_PLANNED_PRICE"
R_NOT_SETTLED = "POSITION_NOT_SETTLED"
R_HEDGE = "HEDGE_LEG_NOT_SETTLED"
R_NO_ALTS = "ALTERNATIVE_TAPE_UNAVAILABLE"
R_NO_REF = "PRIMARY_BENCHMARK_ALLOCATION_UNAVAILABLE"
NOT_IN_IDENTITY = (
    "capital efficiency prices the capital's TIME and OTHER opportunities "
    "(the hurdle, the marginal opportunities, the missed EV of occupied "
    "capital); the realized P&L does not contain them, so adding them to "
    "the P&L identity would count a dollar twice. Its own identity: "
    "economic_value + capital_charge = realized")


# ═════════════════════════════════════════════════════════════════════
# THE TERMS (one function each; `decompose` and `attribute_v2` share them)
# ═════════════════════════════════════════════════════════════════════

def r_chosen(p, d):
    return (p - d) / d


def t_opportunity_set(k_ref, r_bench):
    return k_ref * r_bench


def t_selection(k_ref, r_c, r_bench):
    return k_ref * (r_c - r_bench)


def t_allocation(q_plan, d, k_ref, r_c):
    return (q_plan * d - k_ref) * r_c


def t_execution(q, q_plan, p, d, v, fees):
    return -(q * (v - d)) - fees + (q - q_plan) * (p - d)


def t_settlement(q, payoff, p, exceptional):
    return q * (payoff - p) if exceptional else q * 0


def t_probability(q, payoff, p, exceptional):
    return q * 0 if exceptional else q * (payoff - p)


def t_realized(q, payoff, v, fees, management):
    return q * (payoff - v) - fees + management


def decompose(*, q, q_plan, p, d, v, fees, management, payoff, exceptional,
              k_ref, r_bench) -> tuple:
    """THE EXACT V2 IDENTITY over any numeric type (no float()). Returns
    (terms, realized); sum(terms.values()) == realized algebraically."""
    r_c = r_chosen(p, d)
    terms = {
        "opportunity_set_usd": t_opportunity_set(k_ref, r_bench),
        "selection_usd": t_selection(k_ref, r_c, r_bench),
        "allocation_usd": t_allocation(q_plan, d, k_ref, r_c),
        "execution_usd": t_execution(q, q_plan, p, d, v, fees),
        "management_usd": management,
        "settlement_usd": t_settlement(q, payoff, p, exceptional),
        "probability_usd": t_probability(q, payoff, p, exceptional),
    }
    return terms, t_realized(q, payoff, v, fees, management)


def economic_value(realized, capital_hours, hurdle_ppch) -> tuple:
    """(capital_charge, economic_value); economic_value + charge ==
    realized exactly."""
    charge = capital_hours * hurdle_ppch
    return charge, realized - charge


# ═════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════

def _sum_q(fills) -> float:
    return sum(C.num(f.get("qty")) or 0.0 for f in fills or [])


def clamp_to_rails(k, rails: dict):
    """(k clamped, rails applied, why). min(k, every configured rail); None
    for a rail means NOT CONFIGURED (no limit). A configured rail whose
    input is unmeasured (`rails["unmeasured"]`) makes the result None with
    its reason -- the same rails for every benchmark, or none at all."""
    if k is None:
        return None, [], None
    if float(k) <= 0:
        # nothing allocated stays nothing under ANY rails (min(0, r) = 0)
        return 0.0, [], None
    um = (rails or {}).get("unmeasured") or {}
    if um:
        name = sorted(um)[0]
        return None, [], "HARD_RAIL_UNMEASURED: %s: %s" % (name, um[name])
    out, used = float(k), []
    for name in RAILS:
        r = C.num((rails or {}).get(name))
        if r is not None:
            used.append(name)
            out = min(out, max(0.0, r))
    return max(0.0, out), used, None


def _alternatives(alts: dict | None):
    """(r_bench, basis, why). r_bench None when the tape is unreadable."""
    a = alts or {}
    if a.get("status") == "UNAVAILABLE" or a.get("returns") is None:
        return None, None, a.get("why") or R_NO_ALTS
    rs = [C.num(x) for x in a.get("returns") or []]
    rs = [x for x in rs if x is not None]
    if not rs:
        return 0.0, ("NO_QUALIFIED_ALTERNATIVE_AT_THE_CLOCK: the alternative "
                     "was no-trade (measured: the tape was read and held "
                     "none)"), None
    return (sum(rs) / len(rs),
            "MEAN_EXPECTED_RETURN_PER_DOLLAR_OF_%d_CONTEMPORANEOUS_QUALIFIED_"
            "ALTERNATIVES (%s)" % (len(rs), a.get("basis") or "tape"), None)


# ═════════════════════════════════════════════════════════════════════
# ONE POSITION
# ═════════════════════════════════════════════════════════════════════

def attribute_v2(*, subject_id: str, book: str, sleeve, p, p_basis, d,
                 d_basis, entry_fills: list, sell_fills: list,
                 hedge_legs: list, payoff, settlement_outcome, payoff_basis,
                 plan_qty, plan_qty_basis=None, entry_terminal=None,
                 alternatives: dict | None = None,
                 allocations: dict | None = None, rails: dict | None = None,
                 capital: dict | None = None, actions: int = 0,
                 extra: dict | None = None,
                 allocation_unit: str = PLAN_COST,
                 capital_per_contract=None, fill_state_hint=None) -> dict:
    """V1 (unchanged, under `v1`) + the V2 identity, the allocation
    comparators and the capital-efficiency block. Fills are V1's shape:
    {qty, price (cost space, excl. fee), fee_usd}. `allocation_unit` says
    whether `allocations` are in plan-cost USD (contracts x d) or capital
    USD (capital required, fees included -- then `capital_per_contract` is
    required to convert). `fill_state_hint="NO_ORDER_RECORDED"` names an
    ENTER with neither an order nor a refusal (no order exists to be
    "not terminal")."""
    v1 = A.attribute(subject_id=subject_id, book=book, p=p, p_basis=p_basis,
                     d=d, d_basis=d_basis, entry_fills=entry_fills,
                     sell_fills=sell_fills, hedge_legs=hedge_legs,
                     payoff=payoff, settlement_outcome=settlement_outcome,
                     payoff_basis=payoff_basis, actions=actions)
    out = C.Out(book=book, subject_id=subject_id, version=VERSION,
                label=C.LABEL, authority=C.AUTHORITY, sleeve=sleeve,
                production_confidence_eligible=(sleeve == INVESTMENT),
                v1_version=A.VERSION)
    out.update(extra or {})
    p, d, payoff = C.num(p), C.num(d), C.num(payoff)
    q = _sum_q(entry_fills)
    qp = C.num(plan_qty)
    out["entry_qty"] = C.rnd(q)
    out["plan_qty"] = C.rnd(qp)
    out["plan_qty_basis"] = plan_qty_basis
    exceptional = settlement_outcome in A.EXCEPTIONAL
    no_fill = q <= 0
    # the entry's acquisition (V1's raw arithmetic, unrounded)
    v = (sum((C.num(f["qty"]) or 0.0) * (C.num(f["price"]) or 0.0)
             for f in entry_fills) / q) if not no_fill else None
    fees = sum(C.num(f.get("fee_usd")) or 0.0 for f in entry_fills or [])
    # management (V1's definition)
    mgmt, mgmt_why = 0.0, None
    for s in sell_fills or []:
        if payoff is None:
            mgmt_why = "POSITION_NOT_SETTLED_HOLD_COUNTERFACTUAL_UNKNOWN"
            break
        sq = C.num(s["qty"]) or 0.0
        mgmt += sq * (C.num(s["price"]) or 0.0) - (
            C.num(s.get("fee_usd")) or 0.0) - sq * payoff
    for h in hedge_legs or []:
        hp = C.num(h.get("payoff_per_contract"))
        if hp is None:
            mgmt_why = mgmt_why or R_HEDGE
            continue
        mgmt += (C.num(h["qty"]) or 0.0) * hp - (C.num(h["cost_usd"]) or 0.0)

    # ── the ex-ante side: WHAT and HOW MUCH ───────────────────────────
    r_c = None if p is None or d is None or d <= 0 else r_chosen(p, d)
    out.put("chosen_expected_return_per_dollar", C.rnd(r_c, 9),
            R_NO_P if p is None else R_NO_D)
    r_bench, bench_basis, bench_why = _alternatives(alternatives)
    out["alternatives"] = {
        "n": len([x for x in ((alternatives or {}).get("returns") or [])
                  if C.num(x) is not None]),
        "benchmark_return_per_dollar": C.rnd(r_bench, 9),
        "basis": bench_basis, "why": bench_why,
        "tape_basis": (alternatives or {}).get("basis")}
    # the benchmark allocations, all under the same rails, in ONE unit
    allocs = allocations or {}
    cpc = C.num(capital_per_contract)
    if allocation_unit == CAPITAL:
        cpc_ok = cpc is not None and cpc > 0 and d is not None
    else:
        cpc_ok = d is not None and d > 0
    bench_k: dict = {}
    for b in BENCHMARKS:
        if b == LEGACY:
            continue
        a = allocs.get(b) or {}
        raw = C.num(a.get("usd"))
        k, used, rwhy = clamp_to_rails(raw, rails)
        contracts = cost = None
        why_b = None
        if k is None:
            why_b = rwhy or a.get("why") or "NOT_PROVIDED"
        elif not cpc_ok:
            why_b = R_NO_CPC if d is not None else R_NO_D
        elif allocation_unit == CAPITAL:
            contracts = k / cpc
            cost = contracts * d
        else:
            contracts, cost = k / d, k
        bench_k[b] = {"usd": C.rnd(cost, 6), "contracts": C.rnd(contracts, 6),
                      "capital_usd": (C.rnd(k, 6) if allocation_unit ==
                                      CAPITAL else None),
                      "raw_usd": C.rnd(raw, 6), "rails_applied": used,
                      "basis": a.get("basis"), "why": why_b}
    k_plan = None if qp is None or d is None else qp * d
    k_plan_cap = (None if qp is None else qp * cpc if allocation_unit ==
                  CAPITAL and cpc is not None else k_plan)
    legacy_rails = clamp_to_rails(k_plan_cap, rails)
    bench_k[LEGACY] = {
        "usd": C.rnd(k_plan, 6), "contracts": C.rnd(qp, 6),
        "capital_usd": (C.rnd(k_plan_cap, 6) if allocation_unit == CAPITAL
                        else None),
        "raw_usd": C.rnd(k_plan, 6),
        "rails_applied": [], "basis": plan_qty_basis or "PLANNED_QTY_x_PLAN"
        "_PRICE (the legacy sizing actually used)",
        "within_rails": (None if k_plan_cap is None or legacy_rails[0] is None
                         else legacy_rails[0] >= k_plan_cap - 1e-6),
        "why": None if k_plan is not None else (R_NO_PLAN if qp is None
                                                else R_NO_D)}
    out["allocation_unit"] = ("PLAN_COST_USD (contracts x d; benchmarks "
                              "given in %s)" % allocation_unit)
    out["benchmark_allocations"] = bench_k
    k_ref = C.num(bench_k[PRIMARY_BENCHMARK]["usd"])
    k_ref_why = (None if k_ref is not None else "%s: %s" % (
        R_NO_REF, bench_k[PRIMARY_BENCHMARK]["why"]))
    out["primary_benchmark"] = PRIMARY_BENCHMARK

    terms: dict = {}
    why: dict = {}

    def put(name, value, reason):
        terms[name] = value
        if value is None:
            why[name] = reason

    # opportunity set / selection
    if r_c is None:
        for n in ("opportunity_set_usd", "selection_usd", "allocation_usd"):
            put(n, None, R_NO_P if p is None else R_NO_D)
    else:
        if k_ref is None:
            put("opportunity_set_usd", None, k_ref_why)
            put("selection_usd", None, k_ref_why)
            put("allocation_usd", None, k_ref_why)
        else:
            if r_bench is None:
                put("opportunity_set_usd", None, bench_why)
                put("selection_usd", None, bench_why)
            else:
                put("opportunity_set_usd", t_opportunity_set(k_ref, r_bench),
                    None)
                put("selection_usd", t_selection(k_ref, r_c, r_bench), None)
            if qp is None:
                put("allocation_usd", None, R_NO_PLAN)
            else:
                put("allocation_usd", t_allocation(qp, d, k_ref, r_c), None)
    # selection beside the identity: the chosen opportunity vs no-trade
    out.put("selection_vs_no_trade_usd",
            None if r_c is None or k_ref is None else C.rnd(k_ref * r_c),
            (R_NO_P if p is None else R_NO_D) if r_c is None else k_ref_why)
    out.put("selection_vs_alternatives_return_spread",
            None if r_c is None or r_bench is None
            else C.rnd(r_c - r_bench, 9),
            (R_NO_P if p is None else R_NO_D) if r_c is None else bench_why)

    # ── the ex-post side: HOW WELL, HOW HANDLED, the coin ─────────────
    if no_fill:
        if fill_state_hint == "NO_ORDER_RECORDED":
            fill_state = "NO_ORDER_RECORDED"
        elif qp is not None and qp <= 0:
            fill_state = "NO_ENTRY_ATTEMPTED"
        elif entry_terminal is True:
            fill_state = "ENTRY_TERMINAL_UNFILLED"
        else:
            fill_state = "ENTRY_UNFILLED_NOT_TERMINAL"
    else:
        fill_state = "FILLED"
    out["fill_state"] = fill_state
    realized = None
    realized_why = None
    if fill_state in ("ENTRY_UNFILLED_NOT_TERMINAL", "NO_ORDER_RECORDED"):
        r_open = (R_NO_ORDER if fill_state == "NO_ORDER_RECORDED"
                  else R_NO_FILL_OPEN)
        for n in ("execution_usd", "management_usd", "settlement_usd",
                  "probability_usd"):
            put(n, None, r_open)
        realized_why = r_open
    elif no_fill:
        # nothing was acquired and nothing more can be: the position is a
        # MEASURED zero; execution carries the planned edge that never
        # filled (q = 0: execution = -q_plan (p - d))
        if qp is None or p is None or d is None:
            put("execution_usd", None, R_NO_PLAN if qp is None else
                (R_NO_P if p is None else R_NO_D))
        else:
            put("execution_usd", t_execution(0.0, qp, p, d, d, 0.0), None)
        put("management_usd", 0.0, None)
        put("settlement_usd", 0.0, None)
        put("probability_usd", 0.0, None)
        realized = 0.0
        out["realized_basis"] = ("MEASURED_ZERO: %s (no contract was held)"
                                 % fill_state)
    else:
        if qp is None or p is None or d is None:
            put("execution_usd", None, R_NO_PLAN if qp is None else
                (R_NO_P if p is None else R_NO_D))
        else:
            put("execution_usd", t_execution(q, qp, p, d, v, fees), None)
        put("management_usd", None if mgmt_why else mgmt, mgmt_why)
        if payoff is None:
            put("settlement_usd", None, R_NOT_SETTLED)
            put("probability_usd", None, R_NOT_SETTLED)
            realized_why = R_NOT_SETTLED
        elif p is None:
            put("settlement_usd", None, R_NO_P)
            put("probability_usd", None, R_NO_P)
        else:
            put("settlement_usd", t_settlement(q, payoff, p, exceptional),
                None)
            put("probability_usd", t_probability(q, payoff, p, exceptional),
                None)
        if payoff is not None and not mgmt_why:
            realized = t_realized(q, payoff, v, fees, mgmt)
        elif realized_why is None:
            realized_why = mgmt_why
        out["realized_basis"] = "q (pi - v) - f + management (V1)"
    out.put("realized_pnl_usd", C.rnd(realized, 9), realized_why)
    out["settlement_class"] = (None if payoff is None and not no_fill else
                               "EXCEPTIONAL" if exceptional else "ORDINARY")
    # the combined ex-ante term (WHAT + HOW MUCH), measurable without K_ref
    wahm = (None if qp is None or p is None or d is None
            else qp * (p - d))
    put("what_and_how_much_usd", wahm,
        R_NO_PLAN if qp is None else (R_NO_P if p is None else R_NO_D))

    for k, val in terms.items():
        out.put(k, C.rnd(val, 9), why.get(k))

    # ── THE IDENTITY, at the level the measured terms support ─────────
    def check(names):
        vals = [terms.get(n) for n in names]
        if realized is None or any(x is None for x in vals):
            return None
        s = sum(vals)
        return {"sum_usd": C.rnd(s, 9), "total_usd": C.rnd(realized, 9),
                "residual_usd": C.rnd(s - realized, 12),
                "holds": abs(s - realized) <= IDENTITY_EPS}
    full = check(IDENTITY_TERMS)
    comb = check(COMBINED_TERMS)
    if full is not None:
        level, chk, names = FULL, full, IDENTITY_TERMS
    elif comb is not None:
        level, chk, names = COMBINED, comb, COMBINED_TERMS
    elif v1.get("identity_claimed"):
        level, names = V1_LEVEL, V1_TERMS
        chk = {"sum_usd": C.rnd(sum(v1[n] for n in V1_TERMS), 9),
               "total_usd": v1.get("realized_pnl_usd"),
               "holds": bool(v1.get("reconciles"))}
    else:
        level, chk, names = None, None, ()
    missing = sorted({k: why[k] for k in IDENTITY_TERMS if k in why}.items())
    out["identity"] = {
        "claimed": bool(chk and chk["holds"]), "level": level,
        "terms": list(names), "check": chk,
        "not_claimed_because": (None if chk else (
            realized_why or (dict(missing) if missing else
                             "TERMS_UNAVAILABLE"))),
        "rule": "sum(terms) == realized P&L (exact algebra; float <= %g)"
                % IDENTITY_EPS}
    out["reconciles_to_cash"] = v1.get("reconciles")
    out["cash_pnl_usd"] = v1.get("cash_pnl_usd")

    # ── ALLOCATION ALPHA vs every benchmark (ex ante, not summed) ─────
    pol_k = {LEGACY: C.num(bench_k[LEGACY]["usd"]),
             ALLIE: C.num(bench_k[ALLIE]["usd"])}
    matrix: dict = {}
    for pol in POLICIES:
        row = {}
        for b in BENCHMARKS:
            if b == pol:
                continue
            kb = C.num(bench_k[b]["usd"])
            kp = pol_k[pol]
            if r_c is None or kb is None or kp is None:
                row[b] = {"usd": None, "why": (
                    (R_NO_P if p is None else R_NO_D) if r_c is None else
                    bench_k[pol]["why"] if kp is None else bench_k[b]["why"])}
            else:
                row[b] = {"usd": C.rnd((kp - kb) * r_c, 9), "why": None}
        matrix[pol] = row
    out["allocation_alpha"] = {
        "matrix": matrix, "unit": "USD expected at the decision probability",
        "basis": "(K_policy - K_benchmark) x r_c in plan-cost units "
                 "(contracts x d), each benchmark K clamped by the SAME hard "
                 "rails (%s)" % ", ".join(RAILS),
        "allie_authority": "SHADOW_PENDING_OWNER_APPROVAL (not "
                           "authoritative; nothing was sized by it)",
        "summed_into_identity": False}

    # ── CAPITAL EFFICIENCY (separate identity) ────────────────────────
    cap = capital or {}
    ch = C.num(cap.get("position_capital_hours"))
    hurdle = C.num(cap.get("hurdle_ppch"))
    ce = C.Out(in_pnl_identity=False, why_not_in_pnl_identity=NOT_IN_IDENTITY)
    ce.put("position_capital_hours", C.rnd(ch, 9),
           cap.get("capital_hours_why") or "CAPITAL_HOURS_NOT_PROVIDED")
    if realized is None or ch is None:
        ce.put("realized_profit_per_capital_hour", None,
               realized_why or cap.get("capital_hours_why")
               or "CAPITAL_HOURS_NOT_PROVIDED")
    elif ch <= 0:
        ce.put("realized_profit_per_capital_hour", None,
               "ZERO_CAPITAL_HOURS_NO_CAPITAL_WAS_HELD")
    else:
        ce.put("realized_profit_per_capital_hour", C.rnd(realized / ch, 12))
    ce.put("hurdle_per_capital_hour", C.rnd(hurdle, 12),
           cap.get("hurdle_why") or "HURDLE_NOT_PROVIDED")
    ce["hurdle_basis"] = cap.get("hurdle_basis")
    if realized is None or ch is None or hurdle is None:
        ce.put("capital_charge_usd", None,
               realized_why if realized is None else
               (cap.get("capital_hours_why") or "CAPITAL_HOURS_NOT_PROVIDED")
               if ch is None else
               (cap.get("hurdle_why") or "HURDLE_NOT_PROVIDED"))
        ce.put("economic_value_usd", None, ce["unmeasured"][
            "capital_charge_usd"])
        ce["identity"] = None
    else:
        charge, ev = economic_value(realized, ch, hurdle)
        ce.put("capital_charge_usd", C.rnd(charge, 9))
        ce.put("economic_value_usd", C.rnd(ev, 9))
        ce["identity"] = {"economic_value_plus_charge_usd": C.rnd(
            ev + charge, 9), "realized_usd": C.rnd(realized, 9),
            "holds": abs((ev + charge) - realized) <= IDENTITY_EPS}
        rp = ce.get("realized_profit_per_capital_hour")
        ce["excess_ppch_over_hurdle"] = (None if rp is None
                                         else C.rnd(rp - hurdle, 12))
    for k in ("marginal_opportunities_available",
              "cash_unavailable_by_prior_allocations",
              "missed_executable_ev_from_occupied_capital"):
        blk = cap.get(k)
        ce[k] = blk if blk is not None else {
            "status": "UNAVAILABLE", "why": "NOT_PROVIDED"}
    out["capital_efficiency"] = ce

    out["v1"] = {k: v1.get(k) for k in (
        "model_edge_usd", "execution_edge_usd", "slippage_usd", "fees_usd",
        "management_usd", "settlement_usd", "outcome_variance_usd",
        "realized_pnl_usd", "cash_pnl_usd", "reconciles", "identity_claimed",
        "fill_vwap", "settlement_class", "unmeasured")}
    return out


# ═════════════════════════════════════════════════════════════════════
# AGGREGATION (per independent event, per run; never across books)
# ═════════════════════════════════════════════════════════════════════

SUM_KEYS = IDENTITY_TERMS + ("what_and_how_much_usd", "realized_pnl_usd",
                             "selection_vs_no_trade_usd")


def summarize(rows: list) -> dict:
    """Totals over MEASURED values, the identity over the rows that claim
    it at FULL level (and at FULL-or-COMBINED level), counts of each
    unavailable reason; INVESTMENT apart from everything else."""
    def block(rs):
        s = {"positions": len(rs)}
        for k in SUM_KEYS:
            vals = [r[k] for r in rs if r.get(k) is not None]
            s[k] = C.rnd(sum(vals), 9) if vals else None
            s[k + "_measured_n"] = len(vals)
        full = [r for r in rs if (r.get("identity") or {}).get("level")
                == FULL and r["identity"]["claimed"]]
        s["identity_full"] = {
            "rows": len(full),
            "sum_of_terms_usd": C.rnd(sum(sum(r[t] for t in IDENTITY_TERMS)
                                          for r in full), 9),
            "realized_usd": C.rnd(sum(r["realized_pnl_usd"] for r in full),
                                  9)}
        # rows are stored at 9 decimals: at most ~4e-9 of rounding per row
        s["identity_full"]["holds"] = (
            abs(s["identity_full"]["sum_of_terms_usd"]
                - s["identity_full"]["realized_usd"])
            <= 1e-8 * max(1, len(full)))
        reasons: dict = {}
        for r in rs:
            for k, why in (r.get("unmeasured") or {}).items():
                if k in IDENTITY_TERMS:
                    key = "%s:%s" % (k, str(why).split(":")[0])
                    reasons[key] = reasons.get(key, 0) + 1
        s["unavailable_reasons"] = dict(sorted(reasons.items()))
        return s
    inv = [r for r in rows if r.get("sleeve") == INVESTMENT]
    other = [r for r in rows if r.get("sleeve") != INVESTMENT]
    return {"version": VERSION, "INVESTMENT": block(inv),
            "NON_INVESTMENT_RESEARCH_ONLY": block(other),
            "production_confidence": ("INVESTMENT rows only; the "
                                      "non-INVESTMENT block is research "
                                      "and is never pooled into it"),
            "summed_across_books": False}
