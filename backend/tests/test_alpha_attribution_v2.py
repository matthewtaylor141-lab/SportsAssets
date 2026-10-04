"""ALPHA ATTRIBUTION V2 (specs/ALPHA_ATTRIBUTION_V2.md): selection = WHAT,
allocation = HOW MUCH, execution = HOW WELL, management = HOW HANDLED.

  §1 THE EXACT IDENTITY. `decompose` over fractions.Fraction: the seven
     terms sum to the realized P&L with ==, not a tolerance, on many
     production-shaped cases (partial fills, sales, fees, exceptional and
     ordinary settlements, no alternative, an over- and an under-sized
     plan). The capital-efficiency identity (economic value + capital
     charge = realized) likewise.
  §2 PRODUCTION-SHAPED ROWS. attribute_v2 on float fills claims the FULL
     identity, its realized total equals V1's and reconciles to the cash
     legs within a cent; V1's numbers are carried unchanged.
  §3 NO DOUBLE COUNTING. Perturbing one decision input moves only its own
     terms (the alternatives -> opportunity set/selection; the plan ->
     allocation vs execution's fill shortfall, sum fixed; the fill price ->
     execution only; a sale -> management only; the settlement class ->
     exactly one of settlement / probability).
  §4 UNAVAILABLE WITH A REASON. Each term is null with its reason when its
     inputs are missing, and the identity falls back to the level the
     measured terms support (COMBINED, V1) or is not claimed -- never a
     manufactured zero. An entry that never filled and is terminal is a
     MEASURED zero with the missed edge in execution.
  §5 IDENTICAL RAILS. Every benchmark allocation is clamped by the same
     hard rails; the comparators never enter the identity.
  §6 THE SUMMARY keeps INVESTMENT apart from every other sleeve.
"""
from __future__ import annotations

from fractions import Fraction as F

from sportsassets.intel import attribution as A
from sportsassets.intel import attribution_v2 as V2

T = V2.IDENTITY_TERMS


# ── §1 the exact identity ────────────────────────────────────────────

CASES = [
    # q, q_plan, p, d, v, fees, mgmt, payoff, exceptional, k_ref, r_bench
    (F(100), F(100), F(62, 100), F(55, 100), F(56, 100), F(7, 10), F(0),
     F(1), False, F(1000), F(5, 100)),
    (F(60), F(100), F(62, 100), F(55, 100), F(553, 1000), F(3, 10),
     F(-13, 4), F(0), False, F(1000), F(-1, 100)),          # partial, sold
    (F(250), F(200), F(41, 100), F(37, 100), F(38, 100), F(2), F(17, 2),
     F(1), False, F(500), F(0)),                            # no alternative
    (F(80), F(80), F(7, 10), F(66, 100), F(66, 100), F(1, 2), F(0),
     F(66, 100), True, F(250), F(3, 100)),                  # void refund
    (F(0), F(150), F(55, 100), F(50, 100), F(50, 100), F(0), F(0),
     F(1), False, F(1000), F(2, 100)),                      # never filled
    (F(10), F(10), F(30, 100), F(35, 100), F(36, 100), F(1, 10), F(1, 5),
     F(0), False, F(2000), F(9, 100)),                      # negative edge
]


def test_the_v2_identity_is_exact_over_fractions():
    for q, qp, p, d, v, fees, m, pi, exc, k, rb in CASES:
        terms, realized = V2.decompose(
            q=q, q_plan=qp, p=p, d=d, v=v, fees=fees, management=m,
            payoff=pi, exceptional=exc, k_ref=k, r_bench=rb)
        assert set(terms) == set(T)
        assert all(isinstance(x, F) for x in terms.values()), terms
        assert sum(terms.values()) == realized          # EXACT, no epsilon
        # the layers: WHAT + HOW MUCH = q_plan (p - d); + HOW WELL = the
        # executable edge q (p - v) - f
        wahm = terms["opportunity_set_usd"] + terms["selection_usd"] + \
            terms["allocation_usd"]
        assert wahm == qp * (p - d)
        assert wahm + terms["execution_usd"] == q * (p - v) - fees
        # the realized total is V1's: q (pi - v) - f + management
        assert realized == q * (pi - v) - fees + m


def test_the_capital_efficiency_identity_is_exact():
    for realized, ch, h in ((F(37, 4), F(1250), F(1, 4000)),
                            (F(-5), F(80), F(3, 1000)), (F(0), F(0), F(1))):
        charge, ev = V2.economic_value(realized, ch, h)
        assert ev + charge == realized and charge == ch * h


# ── §2 production-shaped rows ────────────────────────────────────────

ENTRY = [{"qty": 60.0, "price": 0.55, "fee_usd": 0.42},
         {"qty": 40.0, "price": 0.57, "fee_usd": 0.31}]
SELL = [{"qty": 30.0, "price": 0.71, "fee_usd": 0.22}]
ALTS = {"status": "MEASURED", "returns": [0.04, 0.09, -0.01],
        "basis": "test tape"}
ALLOC = {"ALLIE": {"usd": 380.0, "basis": "allie_capital.allocate"},
         "EQUAL_ALLOCATION": {"usd": 1200.0, "basis": "usable / N"},
         "ROI_ONLY_RANKING": {"usd": 900.0, "basis": "greedy by ROI"},
         "CAPITAL_HOUR_RANKING": {"usd": 0.0, "basis": "tranche allocator"},
         "RESERVE_NO_ALLOCATION": {"usd": 0.0, "basis": "reserve"}}
RAILS = {"hard_rail_usd": 1000.0, "capacity_usd": 700.0,
         "idle_capital_usd": 450000.0}


def _row(**kw):
    base = dict(subject_id="g1", book="PAPER", sleeve="INVESTMENT", p=0.63,
                p_basis="P_PINNACLE", d=0.552, d_basis="PLAN_VWAP",
                entry_fills=ENTRY, sell_fills=SELL, hedge_legs=[],
                payoff=1.0, settlement_outcome="WON",
                payoff_basis="PAPER_SETTLEMENT", plan_qty=120.0,
                plan_qty_basis="ENTRY_ORDER_QTY", entry_terminal=True,
                alternatives=ALTS, allocations=ALLOC, rails=RAILS,
                capital={"position_capital_hours": 812.5,
                         "hurdle_ppch": 0.00021, "hurdle_basis": "tape q75"},
                actions=1)
    base.update(kw)
    return V2.attribute_v2(**base)


def test_a_production_shaped_row_claims_the_full_identity_and_reconciles():
    r = _row()
    idn = r["identity"]
    assert idn["claimed"] and idn["level"] == V2.FULL, idn
    assert abs(idn["check"]["residual_usd"]) <= V2.IDENTITY_EPS
    assert abs(sum(r[t] for t in T) - r["realized_pnl_usd"]) <= 1e-8
    v1 = A.attribute(subject_id="g1", book="PAPER", p=0.63, p_basis="x",
                     d=0.552, d_basis="x", entry_fills=ENTRY,
                     sell_fills=SELL, hedge_legs=[], payoff=1.0,
                     settlement_outcome="WON", payoff_basis="x")
    assert abs(r["realized_pnl_usd"] - v1["realized_pnl_usd"]) <= 1e-6
    assert r["reconciles_to_cash"] is True
    assert abs(r["realized_pnl_usd"] - r["cash_pnl_usd"]) <= 0.01
    # V1's terms ride along unchanged
    for k in ("model_edge_usd", "execution_edge_usd", "management_usd",
              "outcome_variance_usd"):
        assert r["v1"][k] == v1[k]
    # management is V1's, ordinary settlement routes the coin to probability
    assert r["management_usd"] == v1["management_usd"]
    assert r["settlement_usd"] == 0.0
    assert r["probability_usd"] == v1["outcome_variance_usd"]
    # capital efficiency: its own identity, outside the P&L identity
    ce = r["capital_efficiency"]
    assert ce["in_pnl_identity"] is False and ce["identity"]["holds"]
    assert abs(ce["realized_profit_per_capital_hour"]
               - r["realized_pnl_usd"] / 812.5) <= 1e-9
    assert r["production_confidence_eligible"] is True


def test_an_exceptional_settlement_is_settlement_not_probability():
    r = _row(payoff=0.552, settlement_outcome="VOID_REFUND", sell_fills=[])
    assert r["identity"]["claimed"] and r["identity"]["level"] == V2.FULL
    assert r["probability_usd"] == 0.0
    assert abs(r["settlement_usd"] - 100.0 * (0.552 - 0.63)) <= 1e-9


# ── §3 no double counting ────────────────────────────────────────────

def _moved(a, b):
    return {t for t in T if abs((a[t] or 0.0) - (b[t] or 0.0)) > 1e-12}


def test_each_decision_input_moves_only_its_own_terms():
    base = _row()
    # WHAT: other alternatives move only the opportunity set and selection
    alt = _row(alternatives={"status": "MEASURED", "returns": [0.2],
                             "basis": "t"})
    assert _moved(base, alt) == {"opportunity_set_usd", "selection_usd"}
    assert abs(alt["opportunity_set_usd"] + alt["selection_usd"]
               - base["opportunity_set_usd"] - base["selection_usd"]) < 1e-9
    # HOW MUCH: the plan moves allocation against the fill shortfall
    plan = _row(plan_qty=200.0)
    assert _moved(base, plan) == {"allocation_usd", "execution_usd"}
    assert abs(plan["allocation_usd"] + plan["execution_usd"]
               - base["allocation_usd"] - base["execution_usd"]) < 1e-9
    # HOW WELL: a worse fill price moves only execution (and the total)
    worse = _row(entry_fills=[dict(f, price=f["price"] + 0.01)
                              for f in ENTRY])
    assert _moved(base, worse) == {"execution_usd"}
    assert abs((worse["realized_pnl_usd"] - base["realized_pnl_usd"])
               - (worse["execution_usd"] - base["execution_usd"])) < 1e-9
    # HOW HANDLED: a different sale moves only management
    sold = _row(sell_fills=[{"qty": 30.0, "price": 0.80, "fee_usd": 0.22}])
    assert _moved(base, sold) == {"management_usd"}
    # the benchmark allocation shifts value among the ex-ante terms only
    ref = _row(allocations=dict(ALLOC, EQUAL_ALLOCATION={"usd": 300.0}))
    assert _moved(base, ref) <= {"opportunity_set_usd", "selection_usd",
                                 "allocation_usd"}
    assert ref["identity"]["claimed"]


# ── §4 unavailable with a reason ─────────────────────────────────────

def test_each_term_is_unavailable_with_a_reason_when_its_inputs_are_missing():
    no_p = _row(p=None)
    for t in ("opportunity_set_usd", "selection_usd", "allocation_usd",
              "execution_usd", "settlement_usd", "probability_usd"):
        assert no_p[t] is None and no_p["unmeasured"][t] == V2.R_NO_P, t
    assert not no_p["identity"]["claimed"] or \
        no_p["identity"]["level"] == V2.V1_LEVEL

    no_d = _row(d=None)
    assert no_d["selection_usd"] is None
    assert no_d["unmeasured"]["selection_usd"] == V2.R_NO_D

    no_plan = _row(plan_qty=None)
    assert no_plan["allocation_usd"] is None
    assert no_plan["unmeasured"]["allocation_usd"] == V2.R_NO_PLAN
    assert no_plan["execution_usd"] is None
    assert no_plan["identity"]["level"] == V2.V1_LEVEL   # V1 still holds

    tape = _row(alternatives={"status": "UNAVAILABLE",
                              "why": "LOL_TAPE_UNREADABLE_AT_THE_CLOCK"})
    assert tape["selection_usd"] is None
    assert tape["unmeasured"]["selection_usd"] == \
        "LOL_TAPE_UNREADABLE_AT_THE_CLOCK"
    assert tape["allocation_usd"] is not None           # needs no tape
    assert tape["identity"]["level"] == V2.COMBINED and \
        tape["identity"]["claimed"]

    no_ref = _row(allocations=dict(ALLOC, EQUAL_ALLOCATION={
        "usd": None, "why": "IDLE_CAPITAL_UNMEASURED_AT_THE_CLOCK"}))
    assert no_ref["allocation_usd"] is None
    assert no_ref["unmeasured"]["allocation_usd"].startswith(V2.R_NO_REF)
    assert "IDLE_CAPITAL_UNMEASURED" in no_ref["unmeasured"]["selection_usd"]
    assert no_ref["identity"]["level"] == V2.COMBINED

    open_ = _row(payoff=None, settlement_outcome=None)
    assert open_["realized_pnl_usd"] is None
    assert open_["unmeasured"]["settlement_usd"] == V2.R_NOT_SETTLED
    assert open_["identity"]["claimed"] is False
    assert open_["identity"]["not_claimed_because"] in (
        V2.R_NOT_SETTLED, "POSITION_NOT_SETTLED_HOLD_COUNTERFACTUAL_UNKNOWN")
    # ex-ante terms stay measured on an open position
    assert open_["selection_usd"] is not None

    pending = _row(entry_fills=[], entry_terminal=False)
    assert pending["fill_state"] == "ENTRY_UNFILLED_NOT_TERMINAL"
    assert pending["realized_pnl_usd"] is None
    assert pending["unmeasured"]["execution_usd"] == V2.R_NO_FILL_OPEN

    no_cap = _row(capital={})
    assert no_cap["capital_efficiency"]["capital_charge_usd"] is None
    assert no_cap["capital_efficiency"]["unmeasured"]


def test_a_terminal_unfilled_entry_is_a_measured_zero_with_the_missed_edge():
    r = _row(entry_fills=[], sell_fills=[], entry_terminal=True)
    assert r["fill_state"] == "ENTRY_TERMINAL_UNFILLED"
    assert r["realized_pnl_usd"] == 0.0 and r["identity"]["claimed"]
    assert abs(r["execution_usd"] + 120.0 * (0.63 - 0.552)) <= 1e-9
    assert r["management_usd"] == 0.0 and r["probability_usd"] == 0.0
    assert r["realized_basis"].startswith("MEASURED_ZERO")


def test_a_risk_refused_entry_puts_the_whole_ex_ante_edge_in_allocation():
    r = _row(entry_fills=[], sell_fills=[], plan_qty=0.0,
             plan_qty_basis="ORDER_REFUSED_BY_PAPER_RISK")
    assert r["fill_state"] == "NO_ENTRY_ATTEMPTED"
    assert r["execution_usd"] == 0.0 and r["realized_pnl_usd"] == 0.0
    assert abs(r["opportunity_set_usd"] + r["selection_usd"]
               + r["allocation_usd"]) <= 1e-9
    assert r["identity"]["claimed"] and r["identity"]["level"] == V2.FULL


def test_no_alternative_is_a_measured_no_trade_benchmark():
    r = _row(alternatives={"status": "MEASURED", "returns": [],
                           "basis": "t"})
    assert r["opportunity_set_usd"] == 0.0
    assert r["alternatives"]["basis"].startswith(
        "NO_QUALIFIED_ALTERNATIVE_AT_THE_CLOCK")
    assert abs(r["selection_usd"] - r["selection_vs_no_trade_usd"]) <= 1e-6


# ── §5 identical rails ───────────────────────────────────────────────

def test_every_benchmark_allocation_is_clamped_by_the_same_rails():
    r = _row()
    bk = r["benchmark_allocations"]
    for b in (V2.ALLIE, V2.EQUAL, V2.ROI):
        assert bk[b]["usd"] <= 700.0 + 1e-9, (b, bk[b])
        # every rail PROVIDED is applied, to every benchmark alike (V2.RAILS
        # also names the concentration / group rails, absent from this row)
        assert bk[b]["rails_applied"] == [r for r in V2.RAILS if r in RAILS]
    # CAPITAL_HOUR (0 in ALLOC) and RESERVE allocate nothing: zero under
    # any rails, no rail needed
    assert bk[V2.CAPHOUR]["usd"] == 0.0 and bk[V2.RESERVE]["usd"] == 0.0
    assert bk[V2.EQUAL]["usd"] == 700.0 and bk[V2.EQUAL]["raw_usd"] == 1200.0
    assert bk[V2.LEGACY]["within_rails"] is True       # 120 x 0.552 = 66.24
    m = r["allocation_alpha"]["matrix"]
    rc = (0.63 - 0.552) / 0.552
    assert abs(m[V2.ALLIE][V2.LEGACY]["usd"] - (380.0 - 66.24) * rc) < 1e-6
    assert abs(m[V2.LEGACY][V2.RESERVE]["usd"] - 66.24 * rc) < 1e-6
    assert r["allocation_alpha"]["summed_into_identity"] is False
    assert "SHADOW_PENDING_OWNER_APPROVAL" in \
        r["allocation_alpha"]["allie_authority"]


# ── §6 the summary ───────────────────────────────────────────────────

def test_the_summary_keeps_investment_apart_and_the_identity_holds():
    rows = [_row(), _row(subject_id="g2", plan_qty=150.0),
            _row(subject_id="g3", sleeve="TRAINING"),
            _row(subject_id="g4", payoff=None, settlement_outcome=None)]
    s = V2.summarize(rows)
    assert s["INVESTMENT"]["positions"] == 3
    assert s["NON_INVESTMENT_RESEARCH_ONLY"]["positions"] == 1
    assert s["INVESTMENT"]["identity_full"]["rows"] == 2
    assert s["INVESTMENT"]["identity_full"]["holds"]
    assert s["INVESTMENT"]["unavailable_reasons"].get(
        "settlement_usd:POSITION_NOT_SETTLED") == 1
    assert s["summed_across_books"] is False


# ── §5b one unit, the full rail set, the no-order state ──────────────

def test_equal_quantities_give_exactly_zero_allocation_alpha():
    """Red-team finding: legacy K was q_plan x d (fees excluded) while the
    benchmarks were capital required (fees included), so the same contracts
    showed a spurious -fees x r_c allocation alpha. With benchmarks given in
    CAPITAL units every K is converted to contracts x d."""
    d, q, fees = 0.554, 100.0, 0.7
    cpc = (q * d + fees) / q                  # capital per contract
    alloc = {b: {"usd": q * cpc, "basis": "same 100 contracts"}
             for b in (V2.ALLIE, V2.EQUAL, V2.ROI, V2.CAPHOUR)}
    r = _row(d=d, plan_qty=q, allocations=alloc,
             rails={"idle_capital_usd": 1e6}, allocation_unit=V2.CAPITAL,
             capital_per_contract=cpc,
             entry_fills=[{"qty": q, "price": d, "fee_usd": fees}])
    assert abs(r["allocation_usd"]) <= 1e-9
    for b in (V2.ALLIE, V2.EQUAL, V2.ROI, V2.CAPHOUR):
        assert abs(r["allocation_alpha"]["matrix"][V2.LEGACY][b]["usd"]) \
            <= 1e-9, b
        assert abs(r["benchmark_allocations"][b]["contracts"] - q) <= 1e-9
    assert r["identity"]["claimed"] and r["identity"]["level"] == V2.FULL
    # without the capital per contract a capital-unit benchmark is unknown
    r = _row(allocations=alloc, allocation_unit=V2.CAPITAL,
             capital_per_contract=None)
    assert r["allocation_usd"] is None
    assert V2.R_NO_CPC in r["unmeasured"]["allocation_usd"]


def test_an_unmeasured_hard_rail_makes_every_benchmark_unavailable():
    r = _row(rails=dict(RAILS, unmeasured={
        "market_headroom_usd": "OPEN_ORDERS_READ_TRUNCATED"}))
    for b in (V2.ALLIE, V2.EQUAL, V2.ROI):
        assert r["benchmark_allocations"][b]["usd"] is None
        assert r["benchmark_allocations"][b]["why"].startswith(
            "HARD_RAIL_UNMEASURED")
    assert r["selection_usd"] is None and r["allocation_usd"] is None
    assert r["identity"]["level"] == V2.COMBINED and r["identity"]["claimed"]


def test_an_enter_with_no_order_has_its_own_fill_state():
    r = _row(entry_fills=[], sell_fills=[], plan_qty=None,
             entry_terminal=None, fill_state_hint="NO_ORDER_RECORDED")
    assert r["fill_state"] == "NO_ORDER_RECORDED"
    assert r["unmeasured"]["execution_usd"] == V2.R_NO_ORDER
    assert r["realized_pnl_usd"] is None
