"""DEREK_ENTRY_POLICY_V2 -- THE BLENDED AVERAGE, PROVEN AS PURE ARITHMETIC.

The owner's rule:

    blended_probability = (internal_probability + pinnacle_fair_probability) / 2
    gross_edge          = blended_probability - executable acquisition price
    enter only if gross_edge >= 0.05 AND expected net profit after fees > 0

Everything here goes through `derek_policy.decide_entry` -- the one function
the live gate, the scheduled pass, the stored record and the workspace all
use -- or through `derek_policy.evaluate`, which takes its combination and
net-EV checks from it. V1 (conservative agreement) is retained and exercised
as a replay. The last section proves the RETROSPECTIVE replay tool on
synthetic records. No database.
"""

from __future__ import annotations

import json

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_model as FM
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import derek_policy_replay as RP

AT = 1790000000.0


def _q(internal=0.60, pinnacle=0.60, *, price=0.50, qty=100.0,
       internal_ok=True, pinnacle_ok=True, pin_refusal=None,
       int_refusal=None, fee_fn=None, params=None, policy=DP.POLICY_V2,
       void=None):
    econ = DP.economics(p_pinnacle=pinnacle,
                        p_model=internal if internal_ok else None,
                        fills=[(price, qty)], fee_fn=fee_fn, at=AT,
                        params=params, policy=policy)
    return DP.decide_entry(
        policy,
        internal={"p": internal if internal_ok else None,
                  "qualified": internal_ok,
                  "refusal": None if internal_ok else (int_refusal
                                                       or DP.R_NO_MODEL),
                  "model_id": "m-1", "model_version": "m-1@v1", "at": AT},
        pinnacle={"p": pinnacle, "qualified": pinnacle_ok,
                  "qualification": "FRESH" if pinnacle_ok else "STALE",
                  "refusal": None if pinnacle_ok else (pin_refusal
                                                       or DP.R_STALE),
                  "at": AT - 3},
        econ=econ, params=params, void=void, void_refunds_price=True)


def _no_fee(q, px):
    return (0.0, "TEST_ZERO_FEE")


def _status(pd):
    return {c["condition"]: c["status"] for c in pd["conditions"]}


# ═════════════════════════════════════════════════════════════════════════
# THE POLICY'S IDENTITY
# ═════════════════════════════════════════════════════════════════════════

def test_v2_is_the_active_policy_and_v1_is_retained():
    assert DP.ACTIVE_POLICY == DP.POLICY_VERSION == "DEREK_ENTRY_POLICY_V2"
    assert DP.COMBINATION_POLICY == DP.COMBINATION_V2 == \
        "BLENDED_AVERAGE_MIN_GROSS_EDGE"
    assert DP.POLICY_RULES[DP.POLICY_V1] == "CONSERVATIVE_AGREEMENT"
    assert DP.COMBINATION_CHECK[DP.POLICY_V2] == DP.C_BLENDED == \
        "blended_average_min_gross_edge"
    assert DP.DEFAULT_PARAMS["min_gross_edge_pp"] == 0.05
    d = DP.describe()
    assert d["active_policy"] == DP.POLICY_V2
    assert d["retained_policies"] == [DP.POLICY_V1]
    # WHAT THE AVERAGE IS, stated plainly
    use = DP.policy_use_is()
    assert use == FM.ENTRY_POLICY_AGREEMENT_IS
    assert "trained on market prices" in use
    assert "not independent confirmation" in use
    assert DP.policy_use_is(DP.POLICY_V1) == FM.ENTRY_POLICY_V1_AGREEMENT_IS
    assert "each to clear 5 pp" in DP.policy_use_is(DP.POLICY_V1)
    with pytest.raises(ValueError):
        DP.decide_entry("DEREK_ENTRY_POLICY_V9", internal={}, pinnacle={},
                        econ={})


def test_the_stable_record_fields():
    assert DP.RECORD_FIELDS == (
        "policy_name", "policy_version", "p_internal",
        "internal_model_version", "internal_at", "p_pinnacle",
        "pinnacle_at", "p_blended", "gross_edge_pp", "fees_usd",
        "net_expected_profit_usd", "expected_return_pct", "conditions",
        "rationale", "instrument")
    pd = _q()
    for k in DP.RECORD_FIELDS:
        assert k in pd, k
    json.dumps(pd)                          # storable as it stands


# ═════════════════════════════════════════════════════════════════════════
# THE OWNER'S WORKED EXAMPLES
# ═════════════════════════════════════════════════════════════════════════

def test_owner_example_price_50_internal_60_pinnacle_60():
    # gross, before fees (a zero-fee schedule isolates the gross figures)
    g = _q(0.60, 0.60, qty=1.0, fee_fn=_no_fee)
    assert g["p_blended"] == 0.60
    assert g["gross_edge_pp"] == pytest.approx(10.0, abs=1e-9)
    assert g["expected_gross_profit_per_contract_usd"] == pytest.approx(0.10)
    assert g["expected_gross_return_pct"] == pytest.approx(20.0)
    # with the deployed fees the net is lower, and positive, so it enters
    for qty in (1.0, 100.0):
        pd = _q(0.60, 0.60, qty=qty)
        fee, _ = FB.fee_for(qty, 0.50, at=AT)
        assert pd["fees_usd"] == pytest.approx(fee) and fee > 0
        assert pd["net_expected_profit_usd"] == pytest.approx(0.10 * qty - fee)
        assert 0 < pd["net_expected_profit_usd"] < 0.10 * qty
        assert pd["expected_return_pct"] == pytest.approx(
            100.0 * (0.10 * qty - fee) / (0.50 * qty + fee))
        assert pd["expected_return_pct"] < 20.0
        assert pd["admitted"] is True and pd["refusal"] is None
        assert set(_status(pd).values()) == {DP.PASS}


def test_owner_example_1000_dollars_on_the_yankees_at_50c():
    """$1,000 at $0.50 = 2,000 contracts before fees; internal 0.60 and
    Pinnacle 0.58 blend to 0.59; the gross edge is exactly 9 pp; the
    expected profit before fees is $180, an 18% return on the purchase
    cost; fees reduce the net figure."""
    qty = 1000.0 / 0.50
    assert qty == 2000.0
    g = _q(0.60, 0.58, qty=qty, fee_fn=_no_fee)
    # probability
    assert g["p_blended"] == 0.59
    # edge in percentage points (and the fraction beside it)
    assert g["gross_edge_pp"] == 9.0
    assert g["gross_edge_fraction"] == 0.09
    # profit in dollars, before fees
    assert g["acquisition_cost_usd"] == pytest.approx(1000.0)
    assert g["expected_gross_profit_usd"] == pytest.approx(180.0)
    # return in percent, on the purchase cost, before fees
    assert g["expected_gross_return_pct"] == pytest.approx(18.0)
    # FEES REDUCE THE NET FIGURE
    pd = _q(0.60, 0.58, qty=qty)
    fee, _ = FB.fee_for(qty, 0.50, at=AT)
    assert pd["fees_usd"] == pytest.approx(fee) and fee > 0
    assert pd["expected_gross_profit_usd"] == pytest.approx(180.0)
    assert pd["net_expected_profit_usd"] == pytest.approx(180.0 - fee)
    assert pd["net_expected_profit_usd"] < pd["expected_gross_profit_usd"]
    assert pd["expected_return_pct"] == pytest.approx(
        100.0 * (180.0 - fee) / (1000.0 + fee))
    assert pd["expected_return_pct"] < 18.0
    assert pd["admitted"] is True
    # the rationale is built from the same stored fields
    for piece in ("p_blended 0.5900", "p_internal 0.6000", "p_pinnacle 0.5800",
                  "gross_edge_pp 9.00", "fees_usd %.2f" % fee,
                  "net_expected_profit_usd %.2f" % (180.0 - fee)):
        assert piece in pd["rationale"], (piece, pd["rationale"])


# ═════════════════════════════════════════════════════════════════════════
# DISAGREEMENT: V1 REFUSED, V2 ADMITS -- AND THE REVERSE, BY FEES
# ═════════════════════════════════════════════════════════════════════════

def test_a_disagreement_v1_refused_v2_admits():
    # internal 0.62, Pinnacle 0.53 at $0.50: Pinnacle's own edge is 3 pp
    v1 = _q(0.62, 0.53, policy=DP.POLICY_V1)
    assert v1["admitted"] is False and v1["refusal"] == DP.R_BELOW
    v2 = _q(0.62, 0.53)
    assert v2["p_blended"] == 0.575
    assert v2["gross_edge_pp"] == pytest.approx(7.5, abs=1e-9)
    assert v2["admitted"] is True, v2["refusals"]
    # the other direction of disagreement: Pinnacle clears, the model not
    v1 = _q(0.52, 0.60, policy=DP.POLICY_V1)
    assert v1["refusal"] == DP.R_DISAGREE
    v2 = _q(0.52, 0.60)
    assert v2["p_blended"] == 0.56 and v2["admitted"] is True


def test_fees_that_leave_no_net_ev_refuse_by_name():
    # a 5 pp blended edge at $0.50 (qualifies on gross) with a fee larger
    # than the gross profit: refused NET_EV_NOT_POSITIVE_AFTER_FEES
    def heavy(q, px):
        return (0.06 * q, "TEST_HEAVY_FEE")
    pd = _q(0.60, 0.50, fee_fn=heavy)
    assert pd["gross_edge_pp"] == pytest.approx(5.0, abs=1e-9)
    st = _status(pd)
    assert st[DP.COND_BLENDED_EDGE] == DP.PASS
    assert st[DP.COND_NET_POSITIVE] == DP.FAIL
    assert pd["refusal"] == DP.R_NET and pd["admitted"] is False
    assert pd["net_expected_profit_usd"] <= 0
    # the deployed schedule on a 1 pp blended edge under a 1 pp override
    pd = _q(0.52, 0.50, params={"min_gross_edge_pp": 0.01})
    assert _status(pd)[DP.COND_BLENDED_EDGE] == DP.PASS
    assert pd["refusal"] == DP.R_NET
    # exactly zero net is not positive
    def exact(q, px):
        return (0.05 * q, "TEST_EXACT_FEE")
    pd = _q(0.60, 0.50, fee_fn=exact)
    assert pd["net_expected_profit_usd"] == pytest.approx(0.0, abs=1e-9)
    assert pd["refusal"] == DP.R_NET
    # and the separately named net threshold
    pd = _q(0.60, 0.60, params={"min_net_ev_usd": 1e9})
    assert pd["refusal"] == DP.R_BELOW_NET


def test_the_boundary_at_exactly_5pp():
    # internal 0.60, Pinnacle 0.50 -> blended 0.55 -> 5 pp: >= passes
    pd = _q(0.60, 0.50, fee_fn=_no_fee)
    assert pd["p_blended"] == 0.55
    assert pd["gross_edge_fraction"] == 0.05
    assert _status(pd)[DP.COND_BLENDED_EDGE] == DP.PASS
    assert pd["admitted"] is True
    # binary representation: 0.61 + 0.49 is 1.1 only in decimal
    pd = _q(0.61, 0.49, fee_fn=_no_fee)
    assert _status(pd)[DP.COND_BLENDED_EDGE] == DP.PASS
    # 4.99 pp refuses by name
    pd = _q(0.5998, 0.50, fee_fn=_no_fee)
    assert pd["gross_edge_pp"] == pytest.approx(4.99, abs=1e-9)
    assert pd["refusal"] == DP.R_BELOW
    assert _status(pd)[DP.COND_BLENDED_EDGE] == DP.FAIL


# ═════════════════════════════════════════════════════════════════════════
# MISSING OR UNQUALIFIED INPUTS: REFUSED BY NAME, NEVER AVERAGED
# ═════════════════════════════════════════════════════════════════════════

def test_missing_or_unqualified_inputs_refuse_by_name():
    # no approved internal model (would clear on Pinnacle alone)
    pd = _q(0.90, 0.90, internal_ok=False)
    assert pd["refusal"] == DP.R_NO_MODEL and pd["p_blended"] is None
    assert pd["gross_edge_pp"] is None
    assert pd["net_expected_profit_usd"] is None
    assert _status(pd)[DP.COND_BLENDED_EDGE] == DP.NOT_EVALUATED
    # a model that cannot score this candidate
    pd = _q(0.90, 0.90, internal_ok=False,
            int_refusal=DP.R_MODEL_CANNOT_SCORE)
    assert pd["refusal"] == DP.R_MODEL_CANNOT_SCORE
    # a stale Pinnacle probability is not averaged
    pd = _q(0.90, 0.90, pinnacle_ok=False)
    assert pd["refusal"] == DP.R_STALE and pd["p_blended"] is None
    # an unmeasured clock
    pd = _q(0.90, 0.90, pinnacle_ok=False,
            pin_refusal=DP.R_FRESHNESS_UNKNOWN)
    assert pd["refusal"] == DP.R_FRESHNESS_UNKNOWN
    assert pd["conditions"][0]["status"] == DP.UNKNOWN
    # no Pinnacle probability at all
    pd = _q(0.90, None)
    assert pd["refusal"] == DP.R_NO_PINNACLE and pd["p_blended"] is None
    assert DP.blend(None, 0.6) is None and DP.blend(0.6, None) is None
    # nothing is admitted without both
    for pd in (_q(0.9, 0.9, internal_ok=False), _q(0.9, 0.9,
                                                   pinnacle_ok=False)):
        assert pd["admitted"] is False
        assert "p_blended n/a" in pd["rationale"]


def test_no_priced_quantity_refuses_no_sized_quantity():
    econ = DP.economics(p_pinnacle=0.6, p_model=0.6, fills=[], at=AT)
    pd = DP.decide_entry(DP.POLICY_V2,
                         internal={"p": 0.6, "qualified": True},
                         pinnacle={"p": 0.6, "qualified": True}, econ=econ)
    assert pd["refusal"] == DP.R_NO_QTY and pd["admitted"] is False


# ═════════════════════════════════════════════════════════════════════════
# THE SETTLEMENT STATES (the payout-state convention, void refunds price)
# ═════════════════════════════════════════════════════════════════════════

def test_a_measured_void_rate_scales_the_expected_gross():
    void = {"ok": True, "rate": 0.02, "upper_95": 0.05, "n_fixtures": 400,
            "source": "bettor_pair_observations"}
    pd = _q(0.60, 0.58, qty=2000.0, void=void)
    fee, _ = FB.fee_for(2000.0, 0.50, at=AT)
    st = pd["settlement_states"]
    assert st["applied"] is True and st["void_status"] == DP.VOID_MEASURED
    # P(WIN)=(1-v)p, P(VOID)=v refunds the price: gross = (1-v) x 180
    assert pd["expected_gross_profit_usd"] == pytest.approx(0.98 * 180.0)
    assert pd["net_expected_profit_usd"] == pytest.approx(0.98 * 180.0 - fee)
    assert pd["net_expected_profit_at_void_upper_95_usd"] == pytest.approx(
        0.95 * 180.0 - fee)
    # the owner's gross edge is the probability difference, unscaled
    assert pd["gross_edge_pp"] == 9.0
    # unmeasured: conditional on the fixture being played, and labelled
    pd = _q(0.60, 0.58, qty=2000.0,
            void={"ok": False, "refusal": "TOO_FEW_SETTLED_FIXTURES"})
    assert pd["settlement_states"]["void_status"] == DP.VOID_UNMEASURED
    assert pd["expected_gross_profit_usd"] == pytest.approx(180.0)
    assert "CONDITIONAL" in pd["settlement_states"]["basis"]


# ═════════════════════════════════════════════════════════════════════════
# EXECUTION AND DISPLAY USE THE SAME OUTPUT
# ═════════════════════════════════════════════════════════════════════════

def _cand(p_pin=0.58, price=0.50, qty=2000.0):
    """A candidate whose every non-combination check passes on its face."""
    return {
        "source": "ROW", "valuation_id": 7, "record_purpose": "ENTRY_DECISION",
        "experiment_id": None, "venue": "PMUS", "condition_id": "c-1",
        "us_market_slug": "aec-mlb-x", "event_key": "e-1",
        "fixture": "condition:c-1", "side": DP.LONG, "payout_event": "HOME",
        "payout_is_complement": False, "period": "FULL_GAME",
        "period_basis": "TEST",
        "pinnacle": {"p": p_pin, "observed_at": AT - 3,
                     "received_at": AT - 2, "overround": 0.02,
                     "method": "power", "source_version": "PINNACLE_DEVIG_V1"},
        "executable_price": price, "executable_price_basis": "TEST",
        "lane_fee_per_contract": 0.0174,
        "est": {"ok": True, "size": qty,
                "levels_taken": [{"price": price, "qty": qty}]},
        "risk": {"permitted": True},
        "freshness": {"fresh": True, "why": "fresh",
                      "pinnacle_qualification": "FRESH",
                      "venue_qualification": "FRESH"},
        "book_currency": {"verdict": "BOOK_CURRENCY_ESTABLISHED"},
        "settlement": {"compatibility": "COMPATIBLE",
                       "overall_established": True, "unmet": [],
                       "fixture_read": True, "game_pk": 1},
        "payout_binding_ok": True, "admissible": True, "refusals": [],
        "plan_input": {}, "decided_at": AT}


def _model(p=0.60):
    return {"ok": True, "p": p, "model_id": "m-1", "model_version": "m-1@v1",
            "approved_by": "owner", "features": {"acquisition_price": 0.5},
            "feature_sha": "x"}


def test_evaluate_takes_its_combination_and_net_checks_from_decide_entry():
    cand, model = _cand(), _model()
    dec = DP.evaluate(cand, model=model, authority=[])
    pd = dec["policy_decision"]
    checks = {c["check"]: c for c in dec["checks"]}
    assert dec["policy_name"] == DP.POLICY_V2
    assert dec["policy_version"] == DP.POLICY_V2 == pd["policy_version"]
    assert checks[DP.C_BLENDED]["source"] == \
        "agents.derek_policy.decide_entry"
    assert checks[DP.C_BLENDED]["evidence"]["p_blended"] == pd["p_blended"]
    assert checks[DP.C_NET_EV]["evidence"]["expected_net_profit_usd"] == \
        pd["net_expected_profit_usd"]
    assert DP.C_AGREEMENT not in checks
    assert pd["rationale"].startswith("%s under %s" % (dec["verdict"],
                                                       DP.POLICY_V2))
    # the stored columns are copies of the same output
    vals = DP.row_values("d-1", cand, dec, latency={}, decided_by="AFTER_CYCLE",
                         evidence={})
    cols = dict(zip(("decision_id", "valuation_id", "fixture",
                     "us_market_slug", "side", "decided_at",
                     "policy_version", "pinnacle_p", "pinnacle_at",
                     "pinnacle_qualification", "model_p", "model_version",
                     "model_at", "model_qualification", "executable_price",
                     "qty", "gross_edge_pp", "expected_gross_profit_usd",
                     "fees_usd", "expected_net_profit_usd",
                     "expected_net_roi"), vals))
    assert cols["gross_edge_pp"] == pd["gross_edge_fraction"]
    assert cols["expected_net_profit_usd"] == pd["net_expected_profit_usd"]
    assert cols["expected_net_roi"] == pd["expected_return_on_capital"]
    assert cols["fees_usd"] == pd["fees_usd"]
    # V1 replay of the same candidate records V1's check and no blend
    v1 = DP.evaluate(cand, model=model, authority=[], policy=DP.POLICY_V1)
    names = {c["check"] for c in v1["checks"]}
    assert DP.C_AGREEMENT in names and DP.C_BLENDED not in names
    assert v1["policy_decision"]["p_blended"] is None


def test_the_record_carries_the_instrument_label_inputs():
    cand = dict(_cand(), selection="New York Yankees", market="h2h",
                line=None, period="FULL_GAME")
    cand["settlement"] = dict(cand["settlement"], home_team="New York Yankees",
                              away_team="Boston Red Sox",
                              official_date="2026-10-01")
    dec = DP.evaluate(cand, model=_model(), authority=[],
                      catalogue_row={"event_slug": "mlb-bos-nyy-2026-10-01",
                                     "event_title": "Boston Red Sox vs. New "
                                                    "York Yankees"})
    ins = dec["policy_decision"]["instrument"]
    assert set(DP.INSTRUMENT_FIELDS) <= set(ins)
    assert ins["participant"] == "New York Yankees"
    assert (ins["home_team"], ins["away_team"]) == ("New York Yankees",
                                                    "Boston Red Sox")
    assert ins["market_type"] == "MONEYLINE" and ins["side"] == DP.LONG
    assert ins["period"] == "FULL_GAME" and ins["competition"] == "MLB"
    assert ins["event_date"] == "2026-10-01"
    # unknown is None WITH ITS REASON, never guessed
    assert ins["line"] is None
    assert ins["unknown"]["line"] == "a moneyline has no line"
    # a valuation that read no fixture metadata leaves teams and date null
    bare = dict(cand, settlement=dict(cand["settlement"], fixture_read=None,
                                      home_team=None, away_team=None,
                                      official_date=None))
    ins = DP.instrument_label(bare, catalogue_row=None,
                              fixture_row={"home_team": "X",
                                           "official_date": "2026-01-01"})
    assert ins["home_team"] is None and ins["event_date"] is None
    assert ins["competition"] is None
    assert "no fixture metadata" in ins["unknown"]["event_date"]
    assert "competition" in ins["unknown"]


def test_a_record_names_the_policy_that_judged_it():
    assert DP.policy_of_record({"evidence": {"policy_decision": {
        "policy_name": DP.POLICY_V2}}}) == DP.POLICY_V2
    # written before V2: no policy decision, the V1 combination
    assert DP.policy_of_record({"evidence": json.dumps({
        "combination_policy": "CONSERVATIVE_AGREEMENT"}),
        "policy_version": "code-default-abc"}) == DP.POLICY_V1
    assert DP.policy_of_record({"evidence": None,
                                "policy_version": DP.POLICY_V1}) == \
        DP.POLICY_V1


# ═════════════════════════════════════════════════════════════════════════
# THE RETROSPECTIVE REPLAY TOOL, ON SYNTHETIC RECORDS
# ═════════════════════════════════════════════════════════════════════════

def _record(did, *, p_pin, p_int, outcome=None, fixture=None,
            depth_refused=False, price=0.50, qty=100.0):
    """A stored decision as the table holds it: recorded inputs, the walk,
    the fees and every named check with its status."""
    fee, _ = FB.fee_for(qty, price, at=AT)
    checks = [{"check": n, "status": DP.PASS, "blocks": DP.BLOCKS_POLICY,
               "refusal": None} for n in (
        DP.C_PURPOSE, DP.C_IDENTITY, DP.C_REAL, DP.C_SETTLEMENT,
        DP.C_PROBABILITY, DP.C_TICK, DP.C_FEES, DP.C_CAPACITY, DP.C_LANE)]
    checks.append({"check": DP.C_DEPTH,
                   "status": DP.FAIL if depth_refused else DP.PASS,
                   "blocks": DP.BLOCKS_POLICY,
                   "refusal": DP.R_NO_DEPTH if depth_refused else None})
    checks.append({"check": DP.C_MODEL,
                   "status": DP.PASS if p_int is not None else DP.FAIL,
                   "blocks": DP.BLOCKS_POLICY,
                   "refusal": None if p_int is not None else DP.R_NO_MODEL})
    checks.append({"check": DP.C_SUBMISSION, "status": DP.FAIL,
                   "blocks": DP.BLOCKS_EXECUTION,
                   "refusal": "FUNDED_SUBMISSION_DISABLED"})
    return {"decision_id": did, "valuation_id": int(did.split("-")[1]),
            "fixture": fixture or did, "policy_version": DP.POLICY_V1,
            "pinnacle_p": p_pin, "pinnacle_at": AT - 3,
            "pinnacle_qualification": "FRESH", "model_p": p_int,
            "model_version": "m@v1" if p_int is not None else None,
            "model_at": AT, "executable_price": price, "qty": qty,
            "fees_usd": fee, "checks": json.dumps(checks),
            "evidence": json.dumps({
                "combination_policy": "CONSERVATIVE_AGREEMENT",
                "params": dict(DP.DEFAULT_PARAMS),
                "economics": {"fee_basis": [{"price": price, "qty": qty,
                                             "fee_usd": fee}],
                              "fees_usd": fee}}),
            "outcome_known": outcome is not None, "outcome": outcome,
            "outcome_basis": ("VENUE_SETTLEMENT_PRICE" if outcome is not None
                              else None)}


def test_the_replay_tool_on_synthetic_records():
    fee, _ = FB.fee_for(100.0, 0.50, at=AT)
    recs = [
        # both clear: admitted by both; resolved as a win
        _record("r-1", p_pin=0.60, p_int=0.62, outcome=1, fixture="fx-a"),
        # disagreement: V1 refuses (Pinnacle 3 pp), V2 admits on 0.575;
        # resolved as a loss
        _record("r-2", p_pin=0.53, p_int=0.62, outcome=0, fixture="fx-b"),
        # Pinnacle clears, model does not: V1 disagree, V2 blended 0.56;
        # unresolved
        _record("r-3", p_pin=0.60, p_int=0.52, fixture="fx-c"),
        # no approved model: both refuse NO_APPROVED_INTERNAL_MODEL
        _record("r-4", p_pin=0.70, p_int=None, outcome=1, fixture="fx-d"),
        # the production case: book currency not established -- neither
        # combination rule matters
        _record("r-5", p_pin=0.70, p_int=0.70, depth_refused=True,
                outcome=1, fixture="fx-e"),
        # below 5 pp for both
        _record("r-6", p_pin=0.52, p_int=0.53, outcome=1, fixture="fx-f"),
    ]
    out = RP.replay_records(recs)
    print(json.dumps(out, indent=2, default=str))
    assert out["evidence"] == "RETROSPECTIVE"
    assert "RETROSPECTIVE" in out["note"]
    assert "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED" in out["note"]
    assert "recorded inputs only" in out["note"]
    v1, v2 = out["policies"][DP.POLICY_V1], out["policies"][DP.POLICY_V2]
    for p in (v1, v2):
        assert p["candidates_considered"] == 6
        assert p["refused_by_reason"].get(DP.R_NO_MODEL) == 1
        assert p["refused_by_reason"].get(DP.R_NO_DEPTH) == 1
    assert v2["active"] is True and v1["active"] is False
    assert v1["admitted"] == 1
    assert v1["refused_by_reason"][DP.R_BELOW] == 2        # r-2, r-6
    assert v1["refused_by_reason"][DP.R_DISAGREE] == 1     # r-3
    assert v2["admitted"] == 3                             # r-1, r-2, r-3
    assert v2["refused_by_reason"][DP.R_BELOW] == 1        # r-6
    # RESOLVED: V1 took only the r-1 win; V2 took the win and the r-2 loss
    win, loss = 100.0 * (1 - 0.50) - fee, 100.0 * (0 - 0.50) - fee
    assert v1["resolved"]["admitted_and_resolved"] == 1
    assert v1["resolved"]["net_result_usd"] == pytest.approx(win)
    assert v2["resolved"]["admitted_and_resolved"] == 2
    assert v2["resolved"]["resolved_fixtures"] == 2
    assert v2["resolved"]["net_result_usd"] == pytest.approx(win + loss)
    assert v2["resolved"]["capital_deployed_usd"] == pytest.approx(
        2 * (50.0 + fee))
    assert out["verdict_changed"] == 2
    assert {c["decision_id"] for c in out["verdict_changed_sample"]} == \
        {"r-2", "r-3"}


def test_the_replay_of_a_production_like_history_admits_nothing():
    """Every recorded decision refused by book currency: both policies admit
    nothing and the comparison says so."""
    recs = [_record("p-%d" % i, p_pin=0.5 + i / 100.0, p_int=0.62,
                    depth_refused=True, outcome=i % 2) for i in range(1, 9)]
    out = RP.replay_records(recs)
    for p in out["policies"].values():
        assert p["admitted"] == 0
        assert p["refused_by_reason"] == {DP.R_NO_DEPTH: 8}
        assert p["resolved"]["admitted_and_resolved"] == 0
    assert out["verdict_changed"] == 0
