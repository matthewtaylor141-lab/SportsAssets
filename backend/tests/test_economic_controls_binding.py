"""CAPITAL-CRITICAL: BINDING PROFITABILITY CONTROLS ON EVERY ACTIVE PAPER ENTRY
PATH (rc6 econ-binding; bettor_paper_profitability_bind control 25).

"If CASH has the highest admissible expected value, CASH must win." Every
paper ENTRY -- Derek, the two benchmark policies, the maker and exploration --
reaches ONE writer, bettor_paper_ledger.submit_order, and under its account
lock the capital authority and the profitability bind. These proofs run the
PRODUCTION functions (ECONOMIC_CONTROLS_ENFORCED, PROFITABILITY_BIND_ENFORCED,
CAPITAL_AUTHORITY_ENFORCED below; the suite's seeded passthroughs in
tests/conftest.py do not apply here):

  §1 THE PATH MAP: every paper order writer outside the ledger is a known
     strategy; every ENTRY reaches the capital authority, the bind and its
     control inputs, and the record requirement, in that order.
  §2 THE CONTROL INPUTS: each missing / unproven input refuses by its OWN
     reason code (identity, probability, settlement, the probability's and
     the book's instants, the four learned models fitted within the
     readiness gate's own bound) -- CASH, never an order -- and a complete
     entry still ENTERS with every control's provenance recorded.
  §3 CASH WINS: when the best admissible all-in EV is <= 0, when every
     candidate is refused (the explicit CASH decision), and when the
     evaluation or its counterfactual variants could not be recorded.
  §4 THE CHAMPION: a strategy whose forward absolute return is not positive
     (or whose CI lower bound is not above zero) is never given capital,
     whatever it beats.
  §5 THE DECISION STAGE: the benchmarks' and Derek's capital gate carries
     the probability's instant (refused without it); the maker and
     exploration carry their inputs to the ledger's bind.
  §6 THE REFERENCE POLICY: each rule of the independent acceptance package's
     economic_policy.decide / champion maps to an engine control with a
     classified reason code (no parallel module).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from sportsassets import bettor_capital_authority as CA
from sportsassets import bettor_capital_eligibility as CE
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_profitability_bind as B
from sportsassets import bettor_settlement_difference_policy as SDP
from sportsassets import bettor_strategy_lifecycle as LC

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                           # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

ECONOMIC_CONTROLS_ENFORCED = True
PROFITABILITY_BIND_ENFORCED = True
CAPITAL_AUTHORITY_ENFORCED = True

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
NOW = 1_791_500_000.0
HOUR = 3600.0
CG = "PINNACLE_COMPLETED_GAME_PAPER"
DEREK = "DEREK_ENTRY_POLICY_V2"
FEE = H.flat_fee(0.01)
ML = "baseball_team_full_game_winner"
COMPATIBLE = {"compatibility": "COMPATIBLE"}


def fee(q, px):
    return 0.01 * q


# ── helpers ──────────────────────────────────────────────────────────

def _cal(n=300, p=0.60, rate=0.60, regime=B.PRE_1H_24H):
    ones = int(round(n * rate))
    return B.fit_calibration([
        {"sport": "baseball", "family": B.MONEYLINE, "regime": regime,
         "p": p, "y": 1.0 if i < ones else 0.0} for i in range(n)])


EMPTY_FITS = {"CALIBRATION": lambda: _cal(),
              "EXECUTION": lambda: B.fit_execution([], []),
              "RESIDUAL": lambda: B.fit_residuals([]),
              "MANAGEMENT": lambda: B.fit_management([])}


async def fit_models(conn, acct, *, at=NOW - 60, kinds=B.MODEL_KINDS):
    """Records each kind as the paper pass's fit step does (fit_all)."""
    for k in kinds:
        assert await B.record_model(conn, account_id=acct, kind=k,
                                    payload=EMPTY_FITS[k](), at=at)


async def _tx():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    return conn, tr


async def _done(conn, tr):
    await tr.rollback()
    await conn.close()


async def premap(conn, slug, *, game_start, sports_type=ML):
    await conn.execute(
        "INSERT INTO us_premap (identifier, market_slug, event_slug, "
        " side_norm, game_start, sports_type, team_name) VALUES ($1,$2,$3,"
        " 'HOME',to_timestamp($4),$5,'Test Home')",
        "ecb-" + F.uid(), slug, "mlb-test-" + slug[-6:], float(game_start),
        sports_type)


def _settled(outcome="WON", payout=1.0):
    async def fn(conn, slug, side):
        return {"outcome": outcome, "payout_per_contract": payout,
                "evidence": {"test": True}}
    return fn


async def seed_forward(conn, a, strategy, *, outcomes=None):
    """MIN_FORWARD_OBSERVATIONS settled, filled shadows of `strategy` in the
    PRE_1H_24H regime (each a counterfactual at 0.40 on p 0.60)."""
    n = CA.MIN_FORWARD_OBSERVATIONS
    outcomes = outcomes or ["WON"] * n
    for i in range(n):
        slug = "%s:fwd:%s:%d" % (a["account_id"], strategy[-6:], i)
        dec = NOW - 900 + i
        await premap(conn, slug, game_start=dec + 3 * HOUR)
        ce = CA.evaluate_executable(
            p=0.6, levels=[{"price": 0.40, "qty": 100}], qty=100, limit=0.40,
            fee_fn=FEE, at=dec, settlement=COMPATIBLE,
            identity={"us_market_slug": slug, "payout_event": "HOME",
                      "fixture": "fx-" + slug, "holding_side": "LONG"})
        ev = CA.capital_evidence(ce, p=0.6, limit=0.40, basis="TEST",
                                 levels=[{"price": 0.40, "qty": 100}])
        got = await CA.record_shadow(
            conn, account_id=a["account_id"], strategy=strategy,
            decision_id="dec:" + slug, order_key=None,
            source=CA.SRC_DECISION, capital_refusal=CA.R_FORWARD_UNKNOWN,
            lifecycle_state=LC.ACTIVE_CHALLENGER, slug=slug,
            holding_side="LONG", fixture="fx-" + slug, payout_event="HOME",
            decided_at=dec, delay_s=2.0, expires_at=dec + 90,
            simulator_version="test", evidence=ev)
        assert got["recorded"], got
        out = outcomes[i]
        await CA.settle_shadows(
            conn, now=NOW - 100, account_id=a["account_id"], limit=1,
            settlement_fn=_settled(out, 1.0 if out == "WON" else 0.0))


async def ready(conn, tag, *, strategy=CG, kinds=B.MODEL_KINDS,
                models_at=NOW - 60, outcomes=None):
    a = await H.new_account(conn, tag, now=NOW - 3600)
    await fit_models(conn, a["account_id"], at=models_at, kinds=kinds)
    await seed_forward(conn, a, strategy, outcomes=outcomes)
    return a


def inputs(**over):
    """The decision's control inputs as a policy carries them."""
    base = {"evaluated_at": NOW, "p_observed_at": NOW - 5.0,
            "book_observed_at": NOW - 2.0, "settlement": dict(COMPATIBLE)}
    base.update(over)
    return base


def evidence(*, slug, p=0.60, qty=100, limit=0.40, levels=None,
             carried=None, settlement=COMPATIBLE):
    levels = levels or [{"price": limit, "qty": qty}]
    ce = CA.evaluate_executable(
        p=p, levels=levels, qty=qty, limit=limit, fee_fn=FEE, at=NOW,
        settlement=settlement,
        identity={"us_market_slug": slug, "payout_event": "HOME",
                  "fixture": "fx-" + slug, "holding_side": "LONG"})
    ev = CA.capital_evidence(ce, p=p, limit=limit, threshold_edge_pp=0.5,
                             basis="TEST", levels=levels)
    if carried is not None:
        ev = dict(ev, bind_inputs=carried)
    return ev


def order(a, *, key, strategy=CG, qty=100, limit=0.40, p=0.60, levels=None,
          carried="DEFAULT", fixture="DEFAULT", slug=None):
    slug = slug or "%s:%s" % (a["account_id"], key)
    o = H.order(a, key=key, qty=qty, limit=limit, at=NOW, slug=slug,
                group_id="paper_g_%s_%s" % (a["account_id"][-6:], key),
                fixture=("fx-" + slug) if fixture == "DEFAULT" else fixture)
    o["strategy"] = strategy
    o["capital_evidence"] = evidence(
        slug=slug, p=p, qty=qty, limit=limit, levels=levels,
        carried=inputs() if carried == "DEFAULT" else carried)
    return o


async def submit(conn, o):
    await premap(conn, o["us_market_slug"], game_start=NOW + 3 * HOUR)
    return await L.submit_order(conn, o, fee_fn=FEE, now=NOW)


async def counts(conn, acct):
    return {k: await conn.fetchval(sql, acct) for k, sql in {
        "orders": "SELECT count(*) FROM paper_orders WHERE account_id=$1",
        "shadows": "SELECT count(*) FROM paper_shadow_counterfactuals "
                   " WHERE account_id=$1",
        "census": "SELECT count(*) FROM paper_entry_refusal_census "
                  " WHERE account_id=$1",
        "evals": "SELECT count(*) FROM paper_profitability_evaluations "
                 " WHERE account_id=$1"}.items()}


async def last_eval(conn, acct):
    return await conn.fetchrow(
        "SELECT * FROM paper_profitability_evaluations WHERE account_id=$1 "
        " ORDER BY eval_id DESC LIMIT 1", acct)


def _calls(tree, dotted: str) -> list:
    mod, name = dotted.split(".")
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == name
            and isinstance(n.func.value, ast.Name) and n.func.value.id == mod]


def _func(tree, name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name)


# ── §1 the path map ──────────────────────────────────────────────────

#: EVERY PAPER ORDER WRITER: the modules that call the ledger's one order
#: writer, and whether they can open an ENTRY (Xavier sells only)
ORDER_PRODUCERS = {"agents/paper_derek.py": "ENTRY",
                   "agents/paper_benchmark.py": "ENTRY",
                   "agents/paper_maker.py": "ENTRY",
                   "agents/paper_explore.py": "ENTRY",
                   "agents/paper_xavier.py": "SELL_ONLY"}


def test_every_paper_order_writer_is_a_known_strategy():
    found = {}
    for f in PKG.rglob("*.py"):
        rel = f.relative_to(PKG).as_posix()
        src = f.read_text()
        if "INSERT INTO paper_orders" in src:
            assert rel == "bettor_paper_ledger.py", rel
        if "submit_order(" in src and rel != "bettor_paper_ledger.py":
            found[rel] = len(_calls(ast.parse(src), "L.submit_order"))
    assert set(found) == set(ORDER_PRODUCERS), found
    # Xavier's orders are sales (management), never an ENTRY BUY
    px = (PKG / "agents" / "paper_xavier.py").read_text()
    for call in _calls(ast.parse(px), "L.submit_order"):
        assert ast.unparse(call.args[1]) == "o"
    assert '"direction": "SELL"' in px and '"role": "ENTRY"' not in px


def test_every_entry_reaches_the_authority_the_bind_and_the_record():
    led = ast.parse((PKG / "bettor_paper_ledger.py").read_text())
    sub = ast.unparse(_func(led, "_submit_order"))
    i_auth = sub.index("CA.ledger_entry_authority(conn, o, at=at")
    i_rec = sub.index("PBIND.entry_record_refusal(conn, o, at=at)")
    i_ins = sub.index("INSERT INTO paper_orders")
    assert i_auth < i_rec < i_ins
    ca = ast.unparse(_func(ast.parse(
        (PKG / "bettor_capital_authority.py").read_text()),
        "ledger_entry_authority"))
    assert "PBIND.entry_bind(" in ca and "await authority(" in ca
    eb = ast.unparse(_func(ast.parse(
        (PKG / "bettor_paper_profitability_bind.py").read_text()),
        "entry_bind"))
    assert eb.index("control_inputs(") < eb.index("bind_economics(")
    assert "control_provenance(" in eb


# ── §2 the control inputs (pure) ─────────────────────────────────────

def _models(at=NOW, age=60.0, missing=()):
    return {k: {"model_id": i + 1, "fitted_at": at - age}
            for i, k in enumerate(B.MODEL_KINDS) if k not in missing}


def _ci(**over):
    kw = dict(evidence={"p": 0.6}, inputs=inputs(), models=_models(),
              slug="s1", side="LONG", fixture="fx-1", at=NOW)
    kw.update(over)
    return B.control_inputs(**kw)


def test_complete_control_inputs_pass_every_control():
    got = _ci()
    assert got["refusal"] is None
    assert set(got["controls"]) == set(B.INPUT_CONTROLS)
    assert {c["status"] for c in got["controls"].values()} == {B.PASS}


@pytest.mark.parametrize("over,code", [
    ({"fixture": None}, B.R_IDENTITY_INCOMPLETE),
    ({"slug": ""}, B.R_IDENTITY_INCOMPLETE),
    ({"side": "YES"}, B.R_IDENTITY_INCOMPLETE),
    ({"evidence": {"p": 1.2}}, B.R_PROBABILITY_INVALID),
    ({"evidence": {"p": float("nan")}}, B.R_PROBABILITY_INVALID),
    ({"evidence": {"p": True}}, B.R_PROBABILITY_INVALID),
    ({"evidence": {}}, B.R_PROBABILITY_INVALID),
    ({"inputs": inputs(settlement=None)}, B.R_SETTLEMENT_NOT_RESOLVED),
    ({"inputs": inputs(settlement={"compatibility": "UNKNOWN"})},
     B.R_SETTLEMENT_NOT_RESOLVED),
    ({"inputs": inputs(settlement={"compatibility": "INCOMPATIBLE"})},
     B.R_SETTLEMENT_NOT_RESOLVED),
    # the priced policy is resolved only by its own marker, id, CURRENT
    # version and priced p (bettor_capital_eligibility.settlement_resolved)
    ({"inputs": inputs(settlement={
        "compatibility": SDP.SETTLEMENT_PRICED, "policy_id": SDP.POLICY_ID,
        "version": "AN_OLD_VERSION", "p": 0.55})},
     B.R_SETTLEMENT_NOT_RESOLVED),
    ({"inputs": inputs(p_observed_at=None)}, B.R_PROBABILITY_TIME_MISSING),
    ({"inputs": inputs(book_observed_at=None)}, B.R_BOOK_TIME_MISSING),
    ({"models": _models(missing=("CALIBRATION",))},
     B.R_CALIBRATION_NOT_CURRENT),
    ({"models": _models(missing=("EXECUTION",))},
     B.R_EXECUTION_MODEL_NOT_CURRENT),
    ({"models": _models(missing=("RESIDUAL",))},
     B.R_RESIDUAL_FEEDBACK_NOT_CURRENT),
    ({"models": _models(missing=("MANAGEMENT",))},
     B.R_MANAGEMENT_MODEL_NOT_CURRENT),
    ({"models": _models(age=B.MODEL_MAX_AGE_S + 1)},
     B.R_CALIBRATION_NOT_CURRENT),
])
def test_each_missing_or_unproven_input_refuses_by_its_own_code(over, code):
    got = _ci(**over)
    assert got["refusal"] == code
    refused = [n for n, c in got["controls"].items()
               if c["status"] == B.REFUSED]
    assert got["controls"][refused[0]]["code"] == code


def test_a_stale_model_of_each_kind_is_named():
    for kind, code in B.MODEL_REFUSAL.items():
        ms = _models()
        ms[kind] = dict(ms[kind], fitted_at=NOW - B.MODEL_MAX_AGE_S - 1)
        got = _ci(models=ms)
        assert got["refusal"] == code, kind
        name = next(n for n, k in B.MODEL_CONTROL.items() if k == kind)
        assert got["controls"][name]["age_s"] > B.MODEL_MAX_AGE_S
    # exactly at the bound is current
    ms = _models(age=B.MODEL_MAX_AGE_S)
    assert _ci(models=ms)["refusal"] is None


def test_the_priced_settlement_difference_policy_resolves_and_caps_p():
    priced = {"compatibility": SDP.SETTLEMENT_PRICED,
              "policy_id": SDP.POLICY_ID, "version": SDP.VERSION, "p": 0.52,
              "q_hi": 0.03}
    assert _ci(inputs=inputs(settlement=priced))["refusal"] is None
    st = B.settlement_terms({"settlement": priced})
    assert st["applies"] and st["p_cap"] == 0.52
    assert st["cost_per_contract"] == 0.03


def test_the_model_bound_is_the_readiness_gates_own_bound():
    """NOT A NEW NUMBER: the currency bound is the one the readiness gate
    profitability_bind_active already holds these models to."""
    from sportsassets.capital_readiness import feeds as FD
    assert B.MODEL_MAX_AGE_S == FD.BIND_MODEL_MAX_AGE_S
    assert B.MODEL_MAX_AGE_S > B.FIT_EVERY_S
    assert set(B.MODEL_KINDS) == {"CALIBRATION", "EXECUTION", "RESIDUAL",
                                  "MANAGEMENT"}


def test_control_provenance_names_pass_refused_and_not_reached():
    refused = {"refusal": B.R_RESIDUAL_FEEDBACK_NOT_CURRENT,
               "control_inputs": _ci(models=_models(
                   missing=("RESIDUAL",)))}
    pv = B.control_provenance(refused)
    assert pv["residual_feedback"] == {
        "status": B.REFUSED, "code": B.R_RESIDUAL_FEEDBACK_NOT_CURRENT}
    assert pv["identity"]["status"] == B.PASS
    for name, _, _ in B.ECONOMIC_CONTROLS:
        assert pv[name]["status"] == B.NOT_REACHED, name
    econ = {"refusal": B.R_CAPACITY_NONE, "control_inputs": _ci(),
            "terms": {}, "freshness": {}, "all_in_at_policy_size": {},
            "capacity": {}}
    pv = B.control_provenance(econ)
    assert pv["calibrated_all_in_ev"]["status"] == B.PASS
    assert pv["capacity"] == {"status": B.REFUSED,
                              "code": B.R_CAPACITY_NONE}
    assert pv["capital_hour"]["status"] == B.NOT_REACHED


def test_carry_inputs_never_overwrites_the_decisions_own():
    ev = {"p": 0.6, "bind_inputs": {"p_observed_at": NOW - 9}}
    got = B.carry_inputs(ev, evaluated_at=NOW, p_observed_at=NOW - 1,
                         book_observed_at=NOW - 2, settlement=COMPATIBLE)
    assert got["bind_inputs"]["p_observed_at"] == NOW - 9
    assert got["bind_inputs"]["book_observed_at"] == NOW - 2
    assert got["bind_inputs"]["settlement"]["compatibility"] == "COMPATIBLE"
    assert ev["bind_inputs"] == {"p_observed_at": NOW - 9}    # not mutated
    assert B.carry_inputs(None, evaluated_at=NOW, p_observed_at=None,
                          book_observed_at=None, settlement=None) is None


# ── §2 the control inputs, at the ledger (every strategy) ────────────

@pg
async def test_a_complete_entry_enters_with_its_control_provenance():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "ecok")
        acct = a["account_id"]
        o = order(a, key="ok1")
        got = await submit(conn, o)
        assert got["ok"], got
        assert float(got["order"]["qty"]) == 50          # bound, as before
        ev = H.j(await conn.fetchval(
            "SELECT detail FROM paper_order_events WHERE order_id=$1 "
            "   AND kind='SUBMITTED'", got["order"]["order_id"]))
        pb = ev["capital_authority"]["profitability_bind"]
        assert pb["controls"] and set(pb["controls"].values()) == {B.PASS}
        assert set(pb["controls"]) == set(B.INPUT_CONTROLS) | {
            c[0] for c in B.ECONOMIC_CONTROLS}
        r = await conn.fetchrow(
            "SELECT * FROM paper_profitability_evaluations WHERE "
            " account_id=$1 AND order_key=$2", acct, o["idempotency_key"])
        assert r["verdict"] == "ENTER" and r["stage"] == "LEDGER"
        d = H.j(r["detail"])
        assert d["controls"]["residual_feedback"]["status"] == B.PASS
        # the counterfactual variants were recorded WITH the evaluation
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_counterfactual_variants WHERE "
            " eval_id=$1", r["eval_id"]) == len(B.VARIANTS)
    finally:
        await _done(conn, tr)


@pytest.mark.parametrize("case,code", [
    ("no_p_time", B.R_PROBABILITY_TIME_MISSING),
    ("no_book_time", B.R_BOOK_TIME_MISSING),
    # nothing carried: the first input control in order (the settlement
    # verdict) names it; the probability / book instants are refused beside
    ("no_carried_inputs", B.R_SETTLEMENT_NOT_RESOLVED),
    ("settlement_unresolved", B.R_SETTLEMENT_NOT_RESOLVED),
    ("no_fixture", B.R_IDENTITY_INCOMPLETE),
    ("no_residual_model", B.R_RESIDUAL_FEEDBACK_NOT_CURRENT),
    ("stale_models", B.R_CALIBRATION_NOT_CURRENT),
    ("no_execution_model", B.R_EXECUTION_MODEL_NOT_CURRENT),
    ("no_management_model", B.R_MANAGEMENT_MODEL_NOT_CURRENT),
])
@pg
async def test_a_missing_input_is_cash_at_the_ledger_by_its_own_code(
        case, code):
    conn, tr = await _tx()
    try:
        kinds = {"no_residual_model": ("CALIBRATION", "EXECUTION",
                                       "MANAGEMENT"),
                 "no_execution_model": ("CALIBRATION", "RESIDUAL",
                                        "MANAGEMENT"),
                 "no_management_model": ("CALIBRATION", "EXECUTION",
                                         "RESIDUAL")}.get(case,
                                                          B.MODEL_KINDS)
        a = await ready(conn, "ecmi", kinds=kinds,
                        models_at=(NOW - B.MODEL_MAX_AGE_S - 60
                                   if case == "stale_models" else NOW - 60))
        acct = a["account_id"]
        carried = {"no_p_time": inputs(p_observed_at=None),
                   "no_book_time": inputs(book_observed_at=None),
                   "no_carried_inputs": None,
                   "settlement_unresolved": inputs(
                       settlement={"compatibility": "UNKNOWN"})}.get(
                           case, "DEFAULT")
        o = order(a, key="m1", carried=carried,
                  fixture=None if case == "no_fixture" else "DEFAULT")
        if case == "no_book_time":
            o["capital_evidence"]["book_observed_at"] = None
        before = await counts(conn, acct)
        got = await submit(conn, o)
        assert got["ok"] is False and got["under_lock"] is True
        assert got["refusal"] == code, got
        after = await counts(conn, acct)
        assert after["orders"] == before["orders"]            # CASH
        assert after["shadows"] == before["shadows"]          # not a shadow
        assert after["census"] == before["census"] + 1        # named
        r = await last_eval(conn, acct)
        assert r["verdict"] == "CASH" and r["refusal"] == code
        assert r["stage"] == "LEDGER" and float(r["qty_out"]) == 0
        ctl = H.j(r["detail"])["controls"]
        assert [n for n, c in ctl.items() if c["status"] == B.REFUSED] \
            and all(c["status"] == B.NOT_REACHED for n, c in ctl.items()
                    if n not in B.INPUT_CONTROLS)
    finally:
        await _done(conn, tr)


@pg
async def test_an_out_of_range_probability_never_reaches_the_ev():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "ecpr")
        o = order(a, key="p1")
        # a forged evidence: eligible, positive EV, p outside [0, 1]
        o["capital_evidence"] = dict(o["capital_evidence"], p=1.4)
        got = await submit(conn, o)
        assert got["refusal"] == B.R_PROBABILITY_INVALID
        assert (await counts(conn, a["account_id"]))["orders"] == 0
    finally:
        await _done(conn, tr)


# ── §3 CASH wins ─────────────────────────────────────────────────────

def test_cash_wins_when_the_best_admissible_all_in_ev_is_not_positive():
    desc = {"sport": "baseball", "family": B.MONEYLINE,
            "regime": B.PRE_1H_24H, "expected_hold_hours": 6.25}
    # raw edge 2c over a 1c fee: the all-in costs (management, freshness)
    # take the rest -- the bind refuses, CASH (0) is the better action
    ev = {"p": 0.42, "fills": [[0.40, 100.0]], "best_price": 0.40,
          "limit": 0.40, "adverse_selection_usd": 0.0,
          "levels": [{"price": 0.40, "qty": 1000}]}
    got = B.bind_economics(evidence=ev, qty_in=100, fee_fn=fee, desc=desc,
                           calibration=_cal(), execution=None,
                           residuals=None, strategy=CG,
                           order_type="MARKETABLE", n_correlated=0,
                           inputs=inputs(), at=NOW)
    assert got["refusal"] == B.R_ALL_IN_EV_NOT_POSITIVE
    assert got["qty"] == 0
    assert got["all_in_at_policy_size"]["ev_given_fill_usd"] <= 0


@pg
async def test_cash_wins_at_the_ledger_when_the_all_in_ev_is_not_positive():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "ecev")
        o = order(a, key="e1", p=0.42, levels=[{"price": 0.40,
                                                 "qty": 1000}])
        # the capital evaluation's EV is positive (2c - 1c fee) ...
        assert o["capital_evidence"]["total_executable_ev_usd"] > 0
        got = await submit(conn, o)
        # ... the all-in EV is not: CASH, by name
        assert got["refusal"] == B.R_ALL_IN_EV_NOT_POSITIVE
        assert (await counts(conn, a["account_id"]))["orders"] == 0
        ctl = H.j((await last_eval(conn, a["account_id"]))["detail"])[
            "controls"]
        assert ctl["calibrated_all_in_ev"]["status"] == B.REFUSED
        assert all(ctl[n]["status"] == B.PASS for n in B.INPUT_CONTROLS)
    finally:
        await _done(conn, tr)


@pg
async def test_cash_is_the_decision_when_every_candidate_is_refused():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "eccs", kinds=("CALIBRATION", "EXECUTION",
                                             "MANAGEMENT"))
        acct = a["account_id"]
        refused = []
        for i, carried in enumerate((inputs(p_observed_at=None),
                                     inputs(settlement=None), "DEFAULT")):
            o = order(a, key="c%d" % i, carried=carried)
            got = await submit(conn, o)
            assert got["ok"] is False
            refused.append(got["refusal"])
            # the strategy's decision records the refusal (as every policy
            # does before its order)
            await conn.execute(
                "INSERT INTO paper_decisions (decision_id, session_id, "
                " account_id, decided_at, us_market_slug, holding_side, "
                " fixture, label, verdict, refusal, internal_model, "
                " pinnacle, qualification_gaps, policy_version, "
                " simulator_version, strategy) VALUES ($1,$2,$3,$4,$5,"
                " 'LONG',$6,'{}'::jsonb,'REFUSE',$7,'{}'::jsonb,'{}'::jsonb,"
                " '[]'::jsonb,'TEST','PAPER_SIM_V1',$8)",
                "paperdec:" + F.uid(), a["session_id"], acct, L._ts(NOW),
                o["us_market_slug"], o["fixture"], got["refusal"], CG)
        assert refused == [B.R_PROBABILITY_TIME_MISSING,
                           B.R_SETTLEMENT_NOT_RESOLVED,
                           B.R_RESIDUAL_FEEDBACK_NOT_CURRENT]
        assert (await counts(conn, acct))["orders"] == 0
        got = await B.cash_step(conn, {"account_id": acct, "now": NOW,
                                       "clock": lambda: NOW + 5})
        assert got["recorded"] == 1 and got["strategies_entered"] == 0
        row = await conn.fetchrow(
            "SELECT * FROM paper_cash_decisions WHERE account_id=$1", acct)
        assert row["strategy"] == CG and row["decisions_evaluated"] == 3
        assert set(H.j(row["binding_refusals"])) == set(refused)
    finally:
        await _done(conn, tr)


@pg
async def test_no_entry_when_its_evaluation_could_not_be_recorded(
        monkeypatch):
    conn, tr = await _tx()
    try:
        a = await ready(conn, "ecrc")
        acct = a["account_id"]

        async def _not_written(conn, **kw):
            return None
        monkeypatch.setattr(B, "record_evaluation", _not_written)
        got = await submit(conn, order(a, key="r1"))
        assert got["ok"] is False
        assert got["refusal"] == B.R_EVALUATION_NOT_RECORDED
        assert got["capital_authority"]["forward_verdict"] == CA.POSITIVE
        c = await counts(conn, acct)
        assert c["orders"] == 0 and c["census"] == 1
    finally:
        await _done(conn, tr)


@pg
async def test_a_failed_variant_write_unrecords_the_evaluation_and_refuses(
        monkeypatch):
    conn, tr = await _tx()
    try:
        a = await ready(conn, "ecrv")
        acct = a["account_id"]

        async def _fails(conn, **kw):
            raise RuntimeError("variant insert failed")
        monkeypatch.setattr(B, "_insert_variants", _fails)
        o = order(a, key="v1")
        got = await submit(conn, o)
        assert got["refusal"] == B.R_EVALUATION_NOT_RECORDED
        # recorded together or not at all: no ENTER evaluation without its
        # counterfactuals
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_profitability_evaluations WHERE "
            " account_id=$1 AND verdict='ENTER'", acct) == 0
        assert (await counts(conn, acct))["orders"] == 0
    finally:
        await _done(conn, tr)


# ── §4 the champion ──────────────────────────────────────────────────

def test_a_champion_is_never_a_non_positive_forward_return():
    n = CA.MIN_FORWARD_OBSERVATIONS
    cases = {
        "all_losing_but_least_bad": [-0.10] * n,
        "flat": [0.0] * n,
        "net_positive_ci_low_negative": [5.0] * 11 + [-4.0] * (n - 11),
        "too_few_observations": [1.0] * (n - 1)}
    for name, pnls in cases.items():
        ch = B.champion_verdict(CA.forward_verdict(pnls, []))
        assert ch["champion"] is False, name
        assert ch["refusal"] == B.R_NOT_ABSOLUTE_CHAMPION, name
    # realized PAPER losses are never outweighed by shadow gains
    fv = CA.forward_verdict([-1.0] * n, [5.0] * n)
    assert fv["verdict"] == CA.NEGATIVE
    assert B.champion_verdict(fv)["champion"] is False
    # the only champion: net > 0 AND the CI lower bound > 0
    ok = CA.forward_verdict([1.0, 1.2] * (n // 2), [])
    assert ok["pnl_ci95_low"] > 0
    assert B.champion_verdict(ok)["champion"] is True


@pg
async def test_a_losing_strategy_gets_no_capital_and_only_a_shadow():
    conn, tr = await _tx()
    try:
        n = CA.MIN_FORWARD_OBSERVATIONS
        a = await ready(conn, "eclo", outcomes=["LOST"] * n)
        acct = a["account_id"]
        got = await submit(conn, order(a, key="l1"))
        assert got["refusal"] == CA.R_FORWARD_NEGATIVE
        c = await counts(conn, acct)
        assert c["orders"] == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_shadow_counterfactuals WHERE "
            " account_id=$1 AND capital_refusal=$2", acct,
            CA.R_FORWARD_NEGATIVE) == 1
    finally:
        await _done(conn, tr)


# ── §5 the decision stage and the carried inputs ─────────────────────

def _cand(slug):
    return {"us_market_slug": slug, "payout_event": "HOME",
            "fixture": "fx-" + slug, "settlement": dict(COMPATIBLE)}


@pg
async def test_the_decision_gate_needs_the_probabilitys_instant():
    from sportsassets.agents import paper_derek as PD
    conn, tr = await _tx()
    try:
        a = await ready(conn, "ecdg", strategy=DEREK)
        ctx = {"account_id": a["account_id"], "config": a["config"]}
        slug = "%s:dg" % a["account_id"]
        await premap(conn, slug, game_start=NOW + 3 * HOUR)
        levels = [{"price": 0.40, "qty": 120}]
        kw = dict(strategy=DEREK, p=0.60, levels=levels,
                  sized={"qty": 100, "limit": 0.40}, cand=_cand(slug),
                  side="LONG", at=NOW, fee_fn=FEE, threshold_edge_pp=0.5,
                  book={"book_obs_id": 1, "observed_at": NOW - 2})
        # what the benchmark policies passed before: no probability instant
        ce = await PD.capital_gate(conn, ctx, decision_id="paperdec:dg0",
                                   **kw)
        assert ce["refusals"] == [B.R_PROBABILITY_TIME_MISSING]
        assert "shadow" not in ce
        ce = await PD.capital_gate(conn, ctx, decision_id="paperdec:dg1",
                                   p_observed_at=NOW - 5, **kw)
        assert ce["capital_eligible"] is True, ce
        assert ce["qty"] == 60
        assert set(ce["profitability_bind"]["controls"].values()) == {
            B.PASS}
        # the ledger re-derives the same size from the carried inputs
        ev = CA.capital_evidence(ce, p=0.60, limit=0.40,
                                 threshold_edge_pp=0.5, basis="TEST",
                                 levels=levels)
        assert ev["bind_inputs"]["p_observed_at"] == NOW - 5
        o = H.order(a, key="dg1", qty=ce["qty"], limit=0.40, at=NOW,
                    slug=slug, fixture="fx-" + slug)
        o.update(strategy=DEREK, capital_evidence=ev,
                 decision_id="paperdec:dg1")
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["ok"], got
        assert float(got["order"]["qty"]) == 60
    finally:
        await _done(conn, tr)


def test_every_policy_carries_its_control_inputs():
    """The benchmarks pass the probability's instant to the shared capital
    gate (Derek already did); the maker and exploration, which have no
    decision-stage bind, carry their inputs on the evidence to the ledger's
    bind."""
    der = (PKG / "agents" / "paper_derek.py").read_text()
    assert "p_observed_at=pin.get(\"at\"))" in der
    bm = ast.parse((PKG / "agents" / "paper_benchmark.py").read_text())
    gates = _calls(bm, "PD.capital_gate")
    assert gates and all(
        any(k.arg == "p_observed_at" and ast.unparse(k.value)
            == "pin.get('at')" for k in c.keywords) for c in gates)
    for f in ("paper_maker.py", "paper_explore.py"):
        tree = ast.parse((PKG / "agents" / f).read_text())
        carry = _calls(tree, "PBIND.carry_inputs")
        assert len(carry) == 1, f
        kws = {k.arg: ast.unparse(k.value) for k in carry[0].keywords}
        assert kws["p_observed_at"] == "pin.get('at')", f
        assert "observed_at" in kws["book_observed_at"], f
        assert "settlement" in kws and "evaluated_at" in kws, f


@pg
async def test_a_ledger_only_policy_binds_on_its_carried_inputs():
    """The maker / exploration path: evidence straight to the ledger. With
    its inputs carried it enters; the same evidence without them is CASH."""
    conn, tr = await _tx()
    try:
        a = await ready(conn, "eclo2")
        slug = "%s:lo" % a["account_id"]
        bare = evidence(slug=slug)
        o = H.order(a, key="lo0", qty=100, limit=0.40, at=NOW, slug=slug,
                    fixture="fx-" + slug)
        o.update(strategy=CG, capital_evidence=bare)
        got = await submit(conn, o)
        assert got["refusal"] == B.R_SETTLEMENT_NOT_RESOLVED
        ctl = H.j((await last_eval(conn, a["account_id"]))["detail"])[
            "controls"]
        assert ctl["settlement"]["status"] == B.REFUSED
        assert ctl["freshness_inputs"] == {
            "status": B.REFUSED, "code": B.R_PROBABILITY_TIME_MISSING}
        o = H.order(a, key="lo1", qty=100, limit=0.40, at=NOW, slug=slug,
                    fixture="fx-" + slug)
        o.update(strategy=CG, capital_evidence=B.carry_inputs(
            bare, evaluated_at=NOW, p_observed_at=NOW - 5,
            book_observed_at=NOW - 2, settlement=COMPATIBLE))
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["ok"], got
    finally:
        await _done(conn, tr)


# ── §6 the reference policy, rule by rule ────────────────────────────

#: economic_policy.decide / champion (the independent acceptance package's
#: standalone spec) -> the ENGINE's control that enforces the rule on the
#: active path, by its own reason code(s)
SPEC_RULES = {
    "UNTRUSTED_DECIMAL_INPUT / NONFINITE_DECIMAL": (
        CE.R_CE_NO_PROBABILITY, B.R_PROBABILITY_INVALID),
    "INVALID_CONTRACT_IDENTITY": (CE.R_CE_IDENTITY_UNRESOLVED,
                                  B.R_IDENTITY_INCOMPLETE),
    "INVALID_PRICE_OR_PROBABILITY": (CE.R_CE_NO_PROBABILITY,
                                     CE.R_CE_NO_LIMIT,
                                     B.R_PROBABILITY_INVALID),
    "UNVERIFIED_DEPTH_OR_SIZE": (CE.R_CE_NO_EXECUTABLE_DEPTH,
                                 CE.R_CE_SIZE_BELOW_ONE_CONTRACT),
    "SETTLEMENT_DIFFERENCE_NOT_PRICED": (CE.R_CE_SETTLEMENT_UNRESOLVED,
                                         B.R_SETTLEMENT_NOT_RESOLVED),
    "STALE_ODDS": (B.R_FRESHNESS_BEYOND_BOUND,
                   B.R_PROBABILITY_TIME_MISSING, B.R_BOOK_TIME_MISSING),
    "MAPPING_UNVERIFIED": (CE.R_CE_IDENTITY_UNRESOLVED,
                           B.R_IDENTITY_INCOMPLETE),
    "CALIBRATION_UNVERIFIED": (B.R_CALIBRATED_EV_NOT_POSITIVE,
                               B.R_CALIBRATION_NOT_CURRENT),
    "REGIME_NOT_AUTHORIZED": (B.R_REGIME_UNKNOWN, B.R_REGIME_NOT_POSITIVE),
    "COUNTERFACTUAL_MISSING": (B.R_EVALUATION_NOT_RECORDED,),
    "RESIDUAL_FEEDBACK_STALE": (B.R_RESIDUAL_FEEDBACK_NOT_CURRENT,),
    "VENUE_LIQUIDITY_UNVERIFIED": (CE.R_CE_NO_EXECUTABLE_DEPTH,
                                   B.R_CAPACITY_NONE),
    "CHAMPION_NOT_INDEPENDENTLY_ELIGIBLE": (B.R_NOT_ABSOLUTE_CHAMPION,
                                            CA.R_FORWARD_UNKNOWN,
                                            CA.R_FORWARD_NEGATIVE),
    "CAPITAL_HOUR_CASH_FALLBACK": (B.R_CAPITAL_HOUR_BELOW_FLOOR,
                                   B.R_NO_HOLD_ESTIMATE),
    "NON_POSITIVE_EXECUTABLE_EV": (CE.R_CE_CASH_WAIT_EV_NOT_POSITIVE,
                                   B.R_ALL_IN_EV_NOT_POSITIVE),
    "PORTFOLIO_CAPACITY_EXHAUSTED": (L.R_PER_ORDER, L.R_PER_MARKET,
                                     L.R_PER_FIXTURE,
                                     B.R_CORRELATED_EXPOSURE,
                                     B.R_SCENARIO_CONCENTRATION),
    "INSUFFICIENT_EXECUTABLE_CAPACITY": (B.R_SIZE_BELOW_ONE,
                                         B.R_CAPACITY_NONE),
    "NO_ABSOLUTE_POSITIVE_FORWARD_CHAMPION": (B.R_NOT_ABSOLUTE_CHAMPION,),
}


def test_every_reference_rule_has_an_engine_control_with_a_classified_code():
    from sportsassets import refusal_taxonomy as RT
    for rule, codes in SPEC_RULES.items():
        assert codes, rule
        for c in codes:
            assert isinstance(c, str) and c, (rule, c)
            row = RT.lookup(c)
            assert row is not None, (rule, c)
    # the reference charges latency and churn as costs: the engine charges
    # the IOC worst-case / learned markout, the freshness decay and the
    # expected exit (churn) cost on every contract -- only ever costs
    ex = B.all_in(fills=[[0.40, 10.0]], p_used=0.6, fee_fn=fee,
                  adverse_per_contract=-1.0, haircut_per_contract=-1.0,
                  fill_probability=1.0,
                  extra_per_contract={"freshness": -1.0, "management": -1.0})
    assert ex["ev_given_fill_usd"] == pytest.approx(10 * (0.6 - 0.40 - 0.01))


def test_the_new_codes_are_economic_bind_refusals_never_shadows():
    from sportsassets import refusal_taxonomy_table as TT
    new = set(B.CONTROL_INPUT_REFUSALS) | {B.R_EVALUATION_NOT_RECORDED}
    assert new <= set(B.REFUSALS) <= set(B.ALL_REFUSALS)
    assert not new & set(CA.NO_CAPITAL_AUTHORITY)
    for c in new:
        assert TT.TABLE[c][0] == "SOFTWARE", c


def test_nothing_here_raises_a_cap_a_size_or_an_authority():
    src = (PKG / "bettor_paper_profitability_bind.py").read_text()
    for word in ("per_order_cap_usd", "per_market_cap_usd", "transition(",
                 "LC.record(", "INSERT INTO paper_orders",
                 "INSERT INTO paper_ledger", "UPDATE ", "DELETE FROM"):
        assert word not in src, word
    # the thresholds the controls sit beside are unchanged
    assert (B.FRESHNESS_BOUND_S, B.MIN_EV_PER_CAPITAL_HOUR,
            B.MAX_DEPTH_FRACTION, B.MAX_ENTRIES_PER_HOUR,
            B.MIN_REGIME_OBSERVATIONS, CA.MIN_FORWARD_OBSERVATIONS) == (
        30.0, 0.0001, 0.5, 12, 20, 20)


def test_this_proof_is_registered_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_economic_controls_binding.py" in listed.splitlines()
