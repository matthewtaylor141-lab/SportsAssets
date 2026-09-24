"""PINNACLE_DEVIG_V1 acceptance. CONTROLLED TESTS, labelled as such.

Every quote below is supplied by the test. None of it is an observed
opportunity, and no test here licenses a claim that the source has found
one. What they establish is that the connected path behaves correctly when
it IS fed: the de-vig arithmetic, the complete-outcome-set requirement,
freshness, the four identity matches, the comparison after costs, and that
an external source never becomes a qualified model.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_entry_gate as gate
from sportsassets import bettor_external_shadow as X
from sportsassets import bettor_fair_value as fv
from sportsassets import bettor_pinnacle_devig as D

NOW = 1_790_000_000.0

#: A real-shaped three-way soccer set: home / draw / away, overround ~4%.
EPL = {"Arsenal": 1.90, "Draw": 3.60, "Chelsea": 4.20}


def _quote(**kw):
    q = {"book": "pinnacle", "observed_at": NOW - 5.0,
         "received_at": NOW - 4.0, "outcomes": dict(EPL),
         "event_key": "epl-ars-che-20260924", "period": "FULL_GAME",
         "line": None, "settlement_rule": "REGULATION_90"}
    q.update(kw)
    return q


def _contract(**kw):
    c = {"venue": "PMUS", "condition_id": "0xabc", "selection": "Arsenal",
         "sport_family": "soccer", "market": "h2h",
         "event_key": "epl-ars-che-20260924", "period": "FULL_GAME",
         "line": None, "settlement_rule": "REGULATION_90"}
    c.update(kw)
    return c


def _fee(*, qty, price):
    """The real shape: theta * C * p * (1-p), taker."""
    return 0.0695 * float(qty) * float(price) * (1.0 - float(price))


def _ms(ask=0.50, **kw):
    ms = {"readable": True, "ask": ask, "depth": 500, "leg": "YES"}
    ms.update(kw)
    return ms


def _ok_inputs(**kw):
    d = {"market_state": _ms(), "execution_estimate": {"p_fill": 0.25},
         "size": 100.0, "risk": {"permitted": True}, "fee_fn": _fee,
         "now": NOW, "outcome_books": 4, "armed": True}
    d.update(kw)
    return d


# ── the arithmetic ──────────────────────────────────────────────────

def test_the_devig_sums_to_one_and_removes_the_overround():
    for method in D.METHODS:
        p = D.devig(list(EPL.values()), method)
        assert abs(sum(p) - 1.0) < 1e-9, (method, p)
        # every de-vigged probability is BELOW its vigged implied value,
        # which is what removing an overround means
        for q, pi in zip(D.implied(list(EPL.values())), p):
            assert pi < q, (method, pi, q)


def test_power_and_multiplicative_DISAGREE_and_that_is_the_point():
    """Two declared methods that returned the same numbers would make
    `devig_method` a decorative column."""
    a = D.devig(list(EPL.values()), D.METHOD_POWER)
    b = D.devig(list(EPL.values()), D.METHOD_MULTIPLICATIVE)
    assert max(abs(x - y) for x, y in zip(a, b)) > 1e-4, (a, b)
    # THE DIRECTION IS THE SUBSTANTIVE CLAIM, and I asserted it backwards
    # first. Measured on this set: power puts the favourite HIGHER
    # (0.50501 -> 0.51241) and the longshot LOWER (0.22846 -> 0.22426).
    # That is the favourite-longshot correction devig.py means by
    # "multiplicative ... biased at longshots" and power's "better tail
    # behavior": multiplicative over-prices the tail and power pulls it
    # back. In ABSOLUTE terms the favourite moves more, which is what my
    # first assertion got wrong; RELATIVE to its own size the longshot
    # moves more, and that is the tail behaviour being described.
    fav_p, fav_m = a[0], b[0]
    dog_p, dog_m = a[2], b[2]
    assert fav_p > fav_m, (fav_p, fav_m)
    assert dog_p < dog_m, (dog_p, dog_m)
    assert abs(dog_p - dog_m) / dog_m > abs(fav_p - fav_m) / fav_m


def test_the_bisection_agrees_with_the_scipy_original_it_replaces():
    """`edge/fairvalue/devig.py` solves the same root with brentq. scipy is
    not in this image, so the root is bisected instead -- and the two must
    agree, or 'reuse the method' is an empty claim.

    The reference is re-implemented here from the ORIGINAL's definition
    (sum(q^k) == 1), not imported, precisely because it cannot be imported.
    """
    odds = list(EPL.values())
    q = D.implied(odds)
    got = D.devig_power(odds)
    k = None
    # recover the exponent our bisection effectively used
    for cand in (got[0] / q[0],):
        assert cand > 0
    import math
    k = math.log(got[0]) / math.log(q[0])
    assert abs(sum(x ** k for x in q) - 1.0) < 1e-9, k
    # and it is a genuine power transform of q, not a rescale
    for qi, pi in zip(q, got):
        assert abs(pi - qi ** k) < 1e-12


def test_an_already_fair_set_is_returned_unchanged():
    fair = [2.0, 2.0]
    p = D.devig_power(fair)
    assert abs(p[0] - 0.5) < 1e-12 and abs(p[1] - 0.5) < 1e-12


# ── the complete outcome set ────────────────────────────────────────

def test_a_three_way_market_priced_on_two_outcomes_is_REFUSED():
    """The failure this requirement exists for: dropping the draw yields a
    number that still looks exactly like a probability."""
    two = {"Arsenal": 1.90, "Chelsea": 4.20}
    out = D.valuation(contract=_contract(), quote=_quote(outcomes=two),
                      now=NOW)
    assert out["probability"] is None
    assert D.R_INCOMPLETE_OUTCOMES in out["refusals"], out
    assert out["outcomes_priced"] == 2 and out["expected_outcomes"] == 3


def test_the_complete_set_prices_and_the_draw_is_carried():
    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
    assert out["probability"] is not None, out
    assert set(out["devigged"]) == set(EPL)
    assert out["devigged"]["Draw"] > 0.2
    assert out["overround"] > 0
    # the mapped selection's probability is the one returned
    assert out["probability"] == out["devigged"]["Arsenal"]


def test_mlb_is_two_outcomes_and_soccer_three():
    assert D.SUPPORTED[("baseball", "h2h")] == 2
    assert D.SUPPORTED[("soccer", "h2h")] == 3
    out = D.valuation(
        contract=_contract(sport_family="baseball", selection="Yankees"),
        quote=_quote(outcomes={"Yankees": 1.80, "Red Sox": 2.10}), now=NOW)
    assert out["probability"] is not None, out


# ── what the provider does not carry ────────────────────────────────

@pytest.mark.parametrize("sport", ["basketball", "icehockey"])
def test_a_sport_pinnacle_does_not_quote_says_SO(sport):
    """Measured 2026-09-23T23:28:58Z: NBA 41 events and NHL 33 events with
    zero Pinnacle. 'Unsupported' and 'the book does not price it' are
    different facts with different remedies."""
    out = D.valuation(contract=_contract(sport_family=sport),
                      quote=_quote(), now=NOW)
    assert D.R_UNSUPPORTED_MARKET in out["refusals"]
    assert D.R_PINNACLE_ABSENT in out["refusals"], out


def test_another_book_is_not_silently_substituted():
    out = D.valuation(contract=_contract(),
                      quote=_quote(book="lowvig"), now=NOW)
    assert out["probability"] is None
    assert D.R_BOOK_MISSING in out["refusals"], out


# ── freshness ───────────────────────────────────────────────────────

def test_a_stale_quote_does_not_price():
    out = D.valuation(contract=_contract(),
                      quote=_quote(observed_at=NOW - 45.0), now=NOW)
    assert D.R_STALE in out["refusals"], out
    assert out["age_s"] == pytest.approx(45.0)
    assert D.MAX_QUOTE_AGE_S == 30.0


def test_a_quote_with_no_timestamp_cannot_be_aged():
    out = D.valuation(contract=_contract(),
                      quote=_quote(observed_at=None), now=NOW)
    assert D.R_NO_TIMESTAMP in out["refusals"], out


def test_both_clocks_are_carried_so_receipt_cannot_masquerade_as_the_quote():
    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
    assert out["observed_at"] == NOW - 5.0
    assert out["received_at"] == NOW - 4.0
    assert out["observed_at"] < out["received_at"]


# ── the four identity matches ───────────────────────────────────────

@pytest.mark.parametrize("field,value,code", [
    ("period", "H1", D.R_PERIOD_MISMATCH),
    ("settlement_rule", "INCLUDING_EXTRA_TIME", D.R_SETTLEMENT_MISMATCH),
    ("event_key", "some-other-match", D.R_NO_MAPPING),
])
def test_a_mismatch_on_any_identity_field_refuses(field, value, code):
    out = D.valuation(contract=_contract(**{field: value}),
                      quote=_quote(), now=NOW)
    assert out["probability"] is None, out
    assert code in out["refusals"], out


def test_a_line_that_differs_refuses_and_an_equal_line_does_not():
    bad = D.valuation(contract=_contract(line=2.5), quote=_quote(line=3.5),
                      now=NOW)
    assert D.R_LINE_MISMATCH in bad["refusals"], bad
    good = D.valuation(contract=_contract(line=2.5), quote=_quote(line=2.5),
                       now=NOW)
    assert D.R_LINE_MISMATCH not in good["refusals"], good


def test_a_field_declared_on_one_side_only_is_NOT_agreement():
    """The asymmetry that lets a full-game price serve a first-half
    contract: one side says FULL_GAME, the other says nothing."""
    out = D.valuation(contract=_contract(period="FULL_GAME"),
                      quote=_quote(period=None), now=NOW)
    assert D.R_PERIOD_MISMATCH in out["refusals"], out


def test_an_unmatched_selection_is_refused_with_no_fuzzy_fallback():
    out = D.valuation(contract=_contract(selection="Arsenal FC"),
                      quote=_quote(), now=NOW)
    assert D.R_SELECTION_UNMATCHED in out["refusals"], out


def test_an_ambiguous_mapping_is_refused_rather_than_picked():
    out = D.valuation(
        contract=_contract(selection="Arsenal"),
        quote=_quote(outcomes={"Arsenal": 1.90, "ARSENAL ": 1.91,
                              "Draw": 3.60}), now=NOW)
    assert D.R_AMBIGUOUS_MAPPING in out["refusals"], out


def test_an_undeclared_devig_method_is_refused():
    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW,
                      method="shrinkage")
    assert D.R_UNKNOWN_METHOD in out["refusals"], out


# ── the comparison, through the real gate ───────────────────────────

def test_an_admissible_BUY_through_the_REAL_gate():
    """CONTROLLED. The ask is set by the test at a price the external
    probability beats by more than the cost of crossing."""
    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
    p = out["probability"]
    ask = round(p - 0.08, 2)
    rec = X.evaluate(contract=_contract(), quote=_quote(),
                     **_ok_inputs(market_state=_ms(ask=ask)))
    assert rec["admissible"] is True, rec["refusals"]
    assert rec["decision"] == "BUY"
    assert rec["order_submitted"] is False
    assert rec["estimated_edge_per_contract"] > X.MIN_NET_EDGE_PER_CONTRACT
    # the edge is probability - ask - cost, and the cost is NOT zero
    assert rec["cost_per_contract"] > 0
    assert rec["estimated_edge_per_contract"] == pytest.approx(
        p - ask - rec["cost_per_contract"])


def test_a_correct_REFUSAL_differing_only_in_the_ask():
    """The mirror of the case above, so admission cannot pass for an
    incidental reason: identical inputs except a price with no edge."""
    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
    ask = round(out["probability"] + 0.02, 2)
    rec = X.evaluate(contract=_contract(), quote=_quote(),
                     **_ok_inputs(market_state=_ms(ask=ask)))
    assert rec["admissible"] is False
    assert rec["decision"] == "NO_TRADE"
    assert gate.R_NO_POSITIVE_EDGE in rec["refusals"], rec["refusals"]
    assert rec["estimated_edge_per_contract"] < 0


def test_the_cost_is_what_turns_a_thin_edge_into_a_refusal():
    """A gross-positive, net-negative case: exactly the band the fee eats."""
    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
    p = out["probability"]
    ask = p - 0.005                        # 0.5c gross edge
    rec = X.evaluate(contract=_contract(), quote=_quote(),
                     **_ok_inputs(market_state=_ms(ask=ask)))
    assert p - ask > 0, "gross edge must be positive for this to mean anything"
    assert rec["estimated_edge_per_contract"] < 0, rec
    assert rec["admissible"] is False


# ── the checks that must NOT be weakened ────────────────────────────

@pytest.mark.parametrize("kw,code", [
    ({"execution_estimate": {}}, gate.R_NO_EXECUTION_ESTIMATE),
    ({"size": 0}, gate.R_NO_SIZING),
    ({"risk": {"permitted": False}}, gate.R_RISK_BLOCKED),
    ({"market_state": {"readable": False, "ask": 0.4, "depth": 10}},
     gate.R_BOOK_UNREADABLE),
    ({"market_state": {"readable": True, "ask": 0.4, "depth": 0}},
     gate.R_NO_DEPTH),
])
def test_execution_and_risk_checks_still_bind(kw, code):
    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
    base = _ok_inputs(market_state=_ms(ask=round(out["probability"] - 0.08, 2)))
    base.update(kw)
    rec = X.evaluate(contract=_contract(), quote=_quote(), **base)
    assert rec["admissible"] is False, rec
    assert code in rec["refusals"], rec["refusals"]


def test_a_single_book_on_the_outcome_is_refused():
    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
    rec = X.evaluate(
        contract=_contract(), quote=_quote(),
        **_ok_inputs(market_state=_ms(ask=round(out["probability"] - 0.08, 2)),
                     outcome_books=1))
    assert rec["admissible"] is False
    assert X.R_THIN_OUTCOME in rec["refusals"], rec["refusals"]


def test_an_unarmed_experiment_records_why_and_stops():
    rec = X.evaluate(contract=_contract(), quote=_quote(),
                     **_ok_inputs(armed=False))
    assert rec["decision"] == "NO_TRADE"
    assert rec["refusals"][0] == X.R_CONTROL_OFF
    assert "gate" not in rec, "an unarmed run must not reach the gate"


def test_no_resting_price_is_used_anywhere():
    """The comparison is against the ASK, crossing. A bid-based comparison
    would invent a queue position we never held."""
    src = open(X.__file__).read()
    assert 'get("ask")' in src
    assert '"bid"' not in src, "a resting/bid price has appeared"
    assert X.describe()["assumes_resting_fills"] is False


# ── the label, which is the whole point ─────────────────────────────

def test_the_external_source_NEVER_becomes_a_qualified_model():
    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
    rec = X.evaluate(contract=_contract(), quote=_quote(),
                     **_ok_inputs(market_state=_ms(
                         ask=round(out["probability"] - 0.08, 2))))
    assert rec["admissible"] is True, rec["refusals"]
    d = rec["gate"]["detail"] if "detail" in rec["gate"] else rec["gate"]
    assert d["qualified_model"] is False, d
    assert d["belief_provenance"] == "EXTERNAL_BOOKMAKER_VALUATION"
    assert d["external_valuation"][
        "is_a_qualified_settlement_model"] is False
    assert D.describe()["is_a_trained_model"] is False


def test_the_internal_fair_value_verdict_is_untouched():
    """This experiment must not read as a promotion of the internal
    challenger that was measured WORSE than the venue price."""
    assert fv.fair_value()[fv.FV_BETTOR_INDEPENDENT] == fv.NOT_IDENTIFIED
    assert fv.directional_permitted()["permitted"] is False
    assert fv.CHAMPION == "B0_VENUE_PRICE"


def test_supplying_an_external_source_without_enabling_it_is_refused():
    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
    got = gate.admit(
        fair_value={"value": out["probability"], "kind": D.SOURCE_CLASS},
        execution_estimate={"p_fill": 0.25}, size=100.0,
        risk={"permitted": True},
        market_state=_ms(ask=round(out["probability"] - 0.08, 2)),
        fee_fn=_fee, external_source=out, external_enabled=False)
    assert got["admissible"] is False
    assert gate.R_EXTERNAL_NOT_ENABLED in got["refusals"], got


def test_the_gate_is_UNCHANGED_when_no_external_source_is_supplied():
    """The additive parameter must not alter the existing production path.
    With no external source the model requirement refuses exactly as it
    did before."""
    got = gate.admit(
        fair_value={"value": 0.6, "kind": "SOMETHING_ELSE"},
        execution_estimate={"p_fill": 0.25}, size=100.0,
        risk={"permitted": True}, market_state=_ms(ask=0.50), fee_fn=_fee)
    assert got["admissible"] is False
    assert gate.R_NO_QUALIFIED_MODEL in got["refusals"], got
    assert got["detail"]["qualified_model"] is False
    assert got["detail"]["belief_provenance"] == "NONE"


def test_rn1_seeded_management_is_not_touched():
    d = X.describe()
    assert d["separate_from_rn1_seeded_management"] is True
    assert d["touches_frozen_exit_policy"] is False
    assert d["submits_orders"] is False
    src = open(X.__file__).read()
    for forbidden in ("bettor_rn1x_policy", "pair_limit", "loss_trigger"):
        assert forbidden not in src, forbidden


def test_the_credential_absence_is_named_not_hidden():
    got = X.credential_present(env={})
    assert got["present"] is False
    assert got["refusal"] == X.R_NO_CREDENTIAL
    assert "EDGE_ODDS_API_KEY" == got["variable"]
    present = X.credential_present(env={"EDGE_ODDS_API_KEY": "x" * 32})
    assert present["present"] is True and present["refusal"] is None


# ── persistence, against the real schema ────────────────────────────

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


@pg
@pytest.mark.asyncio
async def test_a_refusal_is_persisted_and_the_census_counts_it():
    """The refusals are the deliverable when nothing clears, so they must
    survive in the table and be readable as a distribution."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            open("migrations/103_external_valuations.sql").read())
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE condition_id = '0xabc'")

        out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
        good = X.evaluate(contract=_contract(), quote=_quote(),
                          **_ok_inputs(market_state=_ms(
                              ask=round(out["probability"] - 0.08, 2))))
        rid = await X.persist(conn, good)
        assert rid is not None

        stale = X.evaluate(contract=_contract(),
                           quote=_quote(observed_at=NOW - 900),
                           **_ok_inputs())
        await X.persist(conn, stale)

        row = await conn.fetchrow(
            "SELECT decision, admissible, probability, executable_price, "
            "cost_per_contract, estimated_edge_per_contract, raw_odds, "
            "mapped_outcome, mapping_match, devig_method, order_submitted, "
            "outcome_known, observed_at, received_at "
            "FROM external_valuations WHERE id = $1", rid)
        assert row["decision"] == "BUY" and row["admissible"] is True
        assert row["order_submitted"] is False
        assert row["outcome_known"] is False, "recorded before the outcome"
        assert row["mapped_outcome"] == "Arsenal"
        assert row["mapping_match"] == "EXACT_AFTER_NORMALISATION"
        assert row["devig_method"] == "power"
        assert row["observed_at"] < row["received_at"]
        import json as _json
        odds = row["raw_odds"]
        odds = _json.loads(odds) if isinstance(odds, str) else odds
        assert set(odds) == set(EPL), odds

        cen = await X.census(conn, hours=24)
        assert cen["summary"]["evaluated"] >= 2
        assert cen["refusals"].get(D.R_STALE, 0) >= 1, cen["refusals"]

        # THE OUTCOME ARRIVES LATER, BY UPDATE -- and "later" is relative to
        # `decided_at`, which the table stamps with now(). Passing NOW +
        # 7200 (a fixture constant two days in the past) made the
        # outcome_is_later CHECK fire, which is the constraint doing its
        # job on my inconsistent clock rather than a schema fault.
        import time as _time
        await conn.execute(X.JOIN_OUTCOME, rid, 1, _time.time() + 7200.0, 12.5)
        after = await conn.fetchrow(
            "SELECT outcome_known, outcome, realised_net_usd "
            "FROM external_valuations WHERE id = $1", rid)
        assert after["outcome_known"] is True and after["outcome"] == 1
    finally:
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE condition_id = '0xabc'")
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_table_refuses_a_submitted_order_and_an_early_outcome():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            open("migrations/103_external_valuations.sql").read())
        base = ("INSERT INTO external_valuations (experiment_id, version, "
                "source_class, provider, book, devig_method, venue, "
                "contract_selection, sport_family, market, raw_odds, "
                "outcomes_priced, expected_outcomes, decision, admissible")
        vals = ("VALUES ('E','V','EXTERNAL_BOOKMAKER_VALUATION','p','pinnacle',"
                "'power','PMUS','Arsenal','soccer','h2h','{}'::jsonb,3,3,"
                "'NO_TRADE',FALSE")
        # funded trading disabled, at the schema
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(base + ", order_submitted) " + vals + ", TRUE)")
        # an outcome present at insert
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                base + ", outcome_known, outcome, outcome_at) "
                + vals + ", TRUE, 1, now())")
        # a BUY that carries none of the numbers it was admitted on
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                base.replace("decision, admissible", "decision, admissible")
                + ") " + vals.replace("'NO_TRADE',FALSE", "'BUY',TRUE") + ")")
        # a source class that is not external
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                base + ") " + vals.replace(
                    "'EXTERNAL_BOOKMAKER_VALUATION'", "'TRAINED_MODEL'")
                + ")")
    finally:
        await conn.close()


# ── the PRODUCTION shadow decision path ─────────────────────────────

def test_the_external_source_reaches_a_BUY_through_shadow_bettor_decide():
    """THE PRODUCTION PATH, not a parallel one. CONTROLLED inputs.

    `shadow_bettor.decide` forwards `opportunity["entryInputs"]` straight
    into `bettor_entry_gate.admit`, so the two additive parameters are
    carried by the real worker path with no separate plumbing. Nothing is
    monkeypatched and `decide` is called exactly as the worker calls it.
    """
    from sportsassets import shadow_bettor as SB
    from sportsassets import shadow as sh

    out = D.valuation(contract=_contract(), quote=_quote(), now=NOW)
    ask = round(out["probability"] - 0.08, 2)
    book = {"readable": True, "ask": ask, "depth": 500.0, "leg": "YES",
            "bestBid": ask - 0.01, "bestAsk": ask,
            "microstructure": {"depth": 500.0, "spreadRelative": 0.02}}
    opp = {
        "bettorOpportunityId": "opp-ext-1", "symbol": "EPL-ARS-YES",
        "outcomeLeg": "YES", "eventId": "ev-ext", "marketId": "mk-ext",
        "sport": "soccer", "league": "epl", "evidenceSource": "PMUS_BBO",
        "observedAt": NOW, "featureLineage": {},
        "microstructure": {"depth": 500.0, "spreadRelative": 0.02},
        "entryInputs": {
            "external_source": out,
            "external_enabled": True,
            "execution_estimate": {"p_fill": 0.25},
            "size": 100.0,
            "risk": {"permitted": True},
            "fee_fn": _fee,
            "fair_value": {"value": out["probability"],
                           "kind": D.SOURCE_CLASS},
        },
    }
    rec = SB.decide(opp, book, decision_ts=NOW)
    assert rec["proposedAction"] == gate.ENTRY_ACTION, rec
    assert rec["entryGate"]["admissible"] is True, rec["entryGate"]
    # AND IT IS STILL A SHADOW DECISION.
    assert rec["orderSubmitted"] is False
    assert rec["shadowOnly"] is True
    # the belief is labelled external at the decision record's own level
    det = rec["entryGate"]["detail"]
    assert det["qualified_model"] is False
    assert det["belief_provenance"] == "EXTERNAL_BOOKMAKER_VALUATION"
    # the production default is untouched: no entryInputs, no BUY
    plain = SB.decide({k: v for k, v in opp.items() if k != "entryInputs"},
                      book, decision_ts=NOW)
    assert plain["proposedAction"] == sh.NO_TRADE, plain
