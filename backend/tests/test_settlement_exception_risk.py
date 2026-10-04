"""R30C · THE MEASURED SETTLEMENT-EXCEPTION RISK (program section 15).

  * the intervals are the exact Clopper-Pearson / Wilson bounds;
  * a venue settlement record is classified by what the OUTCOME JOIN wrote
    (a binary payout, a declared void, a price strictly between 0 and 1) --
    the rows here are written by the REAL join
    (workers/ext_pinnacle_loop.join_outcomes) from production-shaped venue
    answers, and by the REAL paper ledger (`bettor_paper_ledger.settle`);
  * unknown is never zero: an unmeasured cell carries a conservative prior,
    a structural zero names the rule of the game that makes it one, and an
    external base rate enters only with its citation, count and denominator;
  * the expected exception cost is carried on the canonical decision's
    evidence and gates nothing: the ENTER, the order and its size are the
    same whatever the cost says.

The DB tests write SYNTHETIC rows into the test database only.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi import Response

from sportsassets import settlement_exception_risk as SER
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: THE VENUE'S OWN TEXT for an MLB money line (the 2026-10-01 NL Wild Card
#: Game 3, as tests/paper_live_fixture records it): a postponement or
#: suspension settles at the last fair market price.
RECORDED_MLB_PROSE = (
    "This market will settle to the winner of the Philadelphia Phillies vs "
    "Atlanta Braves MLB NL Wild Card Game 3 scheduled for 2026-10-01 at "
    "2:00PM ET. Extra innings are included if played. If the game is "
    "delayed, postponed, or suspended and not rescheduled to a date within "
    "two weeks of the originally scheduled date, the market will settle to "
    "the last fair market price. Outcome sourced from MLB.")


# ═════════════════════════════════════════════════════════════════════
# 1 · INTERVALS
# ═════════════════════════════════════════════════════════════════════

def test_the_bounds_are_the_exact_ones():
    lo, hi = SER.clopper_pearson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(1 - 0.025 ** (1 / 10), abs=1e-9)
    lo, hi = SER.clopper_pearson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.025 ** (1 / 10), abs=1e-9)
    lo, hi = SER.clopper_pearson(5, 10)
    assert lo == pytest.approx(0.187086, abs=1e-5)
    assert hi == pytest.approx(0.812914, abs=1e-5)
    w = SER.wilson(1, 65)
    assert w[1] == pytest.approx(0.082133, abs=1e-5)
    iv = SER.interval(1, 65)
    # the conservative bound is the larger of the two
    assert iv["upper_95"] == pytest.approx(max(w[1], SER.clopper_pearson(
        1, 65)[1]))
    assert SER.interval(0, 0)["rate"] is None
    # the prior's floor: zero events in MIN_FIXTURES fixtures
    assert SER.PRIOR_FLOOR_UPPER == pytest.approx(
        1 - 0.025 ** (1 / SER.MIN_FIXTURES), abs=1e-9)


def test_the_constants_are_the_writers_own():
    from sportsassets import bettor_external_shadow as ext
    from sportsassets import bettor_pair_observations as PO
    from sportsassets.agents import paper_xavier as PX
    from sportsassets.workers import ext_pinnacle_loop as X
    assert SER.EXPERIMENT_ID == ext.EXPERIMENT_ID
    assert set(SER.LABEL_BASES) == set(PX.LABEL_BASES) == {
        X.B_SETTLEMENT_PRICE, X.B_REPORTED_OUTCOME}
    assert SER.VOID_BASIS == PX.VOID_BASIS == X.B_CONFIRMED_VOID
    assert SER.MIN_FIXTURES == PO.MIN_VOID_RATE_FIXTURES


# ═════════════════════════════════════════════════════════════════════
# 2 · CLASSIFICATION AND APPLICABILITY
# ═════════════════════════════════════════════════════════════════════

def test_a_market_is_classified_by_what_the_join_wrote():
    c = SER.classify_market
    assert c(bases=["VENUE_SETTLEMENT_PRICE"])["class"] == SER.C_ORDINARY
    assert c(bases=["CONFIRMED_VOID"])["class"] == SER.E_VOID
    # the production record: aec-mlb-bal-nyy-2026-09-27 settled at 0.485
    assert c(numeric_reads=["0.485"])["class"] == SER.E_PRICE
    # a 0.5 is a tie only where the interval can end level
    assert c(numeric_reads=["0.5"], tie_reachable=True)["class"] == SER.E_TIE
    assert c(numeric_reads=["0.5"], tie_reachable=False)["class"] == \
        SER.E_PRICE
    assert c(numeric_reads=["0.5"], tie_reachable=None)["class"] == \
        SER.E_PRICE
    # 0 and 1 are not exceptional reads; a read the join did not establish
    # (named winner, pending) is not terminal
    assert c(numeric_reads=["1", "0"])["class"] is None
    assert c(numeric_reads=["Yes"])["class"] is None
    # disagreeing reads are excluded by name, never resolved by preference
    got = c(bases=["VENUE_SETTLEMENT_PRICE", "CONFIRMED_VOID"])
    assert got["class"] == SER.C_CONFLICT
    # the paper ledger's settlements are the same evidence, held subset
    assert c(paper_outcomes=["VOID_REFUND"])["class"] == SER.E_VOID
    assert c(paper_prices=["0.30"])["class"] == SER.E_PRICE
    assert c(paper_outcomes=["WON"])["class"] == SER.C_ORDINARY


def test_a_tie_applies_only_where_the_rules_of_the_game_allow_it():
    t = SER.tie_applicability
    assert t("football", "nfl", "h2h")["status"] == "APPLICABLE"
    # college overtime is played to a result; baseball includes extra innings
    assert t("football", "cfb", "h2h")["status"] == SER.S_STRUCTURAL
    assert t("baseball", "mlb", "h2h")["status"] == SER.S_STRUCTURAL
    # the draw is a priced outcome on both sides
    assert t("soccer", "unl", "h2h")["status"] == SER.S_NOT_APPLICABLE
    # undeclared is UNKNOWN, never a structural zero
    assert t("hockey", "nhl", "h2h")["status"] == "UNKNOWN"
    assert t("baseball", "mlb", "spreads")["status"] == "UNKNOWN"


def test_the_baseball_structural_zero_is_league_scoped():
    """KBO and NPB regular-season games end level at the league's
    extra-inning cap, and the venue lists both on the MLB winner type: a
    structural zero inherited from the family default would be a zero no
    rule supports. MLB alone is declared unable to end level; any other
    baseball league is UNKNOWN (the prior)."""
    t = SER.tie_applicability
    assert t("baseball", "mlb", "h2h")["status"] == SER.S_STRUCTURAL
    for lg in ("kbo", "npb"):
        got = t("baseball", lg, "h2h")
        assert got["status"] == "APPLICABLE", (lg, got)
        assert got["status"] != SER.S_STRUCTURAL
    assert t("baseball", "cpbl", "h2h")["status"] == "UNKNOWN"
    # and through the table: a KBO cell with no settled record is never zero
    tb = SER.build_table([_mkt("aec-kbo-lg-ss-2026-10-01", "baseball", "fx-k",
                               bases=["VENUE_SETTLEMENT_PRICE"])])
    kbo = next(c for c in tb["cells"] if c["cell"] == "baseball/kbo/h2h")
    assert kbo["events"][SER.E_TIE]["status"] == SER.S_PRIOR
    assert kbo["events"][SER.E_TIE]["upper_95"] >= SER.PRIOR_FLOOR_UPPER


# ═════════════════════════════════════════════════════════════════════
# 3 · THE TABLE: MEASURED, POOLED, CITED, PRIOR -- NEVER A ZERO FOR UNKNOWN
# ═════════════════════════════════════════════════════════════════════

def _mkt(slug, fam, fixture, **kw):
    return dict({"slug": slug, "sport_family": fam, "market": "h2h",
                 "fixture": fixture, "bases": [], "numeric_reads": [],
                 "paper_outcomes": [], "paper_prices": []}, **kw)


def _production_shape():
    """The production census of 2026-10-04 (research-sql run 37226249974):
    65 settled MLB fixtures, one settled at 0.485; 15 + 7 + 6 soccer; 6 cfb."""
    rows = []
    for i in range(64):
        rows.append(_mkt("aec-mlb-t%02d-u%02d-2026-09-27" % (i, i), "baseball",
                         "fx-mlb-%d" % i, bases=["VENUE_SETTLEMENT_PRICE"]))
    rows.append(_mkt("aec-mlb-bal-nyy-2026-09-27", "baseball", "fx-bal-nyy",
                     numeric_reads=["0.485"]))
    for lg, n in (("unl", 15), ("brb", 7), ("uwcl", 6)):
        for i in range(n):
            rows.append(_mkt("atc-%s-a%d-b%d-2026-10-01" % (lg, i, i),
                             "soccer", "fx-%s-%d" % (lg, i),
                             bases=["VENUE_REPORTED_OUTCOME"]))
    for i in range(6):
        rows.append(_mkt("aec-cfb-c%d-d%d-2026-10-03" % (i, i), "football",
                         "fx-cfb-%d" % i, bases=["VENUE_SETTLEMENT_PRICE"]))
    return rows


def test_the_table_on_the_production_census():
    t = SER.build_table(_production_shape(), as_of=1.0)
    cells = {c["cell"]: c for c in t["cells"]}
    mlb = cells["baseball/mlb/h2h"]
    assert mlb["settled_fixtures"] == 65
    price = mlb["events"][SER.E_PRICE]
    assert price["status"] == SER.S_MEASURED
    assert (price["k"], price["n"]) == (1, 65)
    assert price["rate"] == pytest.approx(1 / 65)
    assert price["upper_95"] == pytest.approx(SER.interval(1, 65)["upper_95"])
    # zero voids in 65 is a measurement with an upper bound, not a zero
    void = mlb["events"][SER.E_VOID]
    assert void["status"] == SER.S_MEASURED and void["k"] == 0
    assert void["upper_95"] > 0.05
    # a baseball tie is impossible by the rules of the game, and says so
    tie = mlb["events"][SER.E_TIE]
    assert tie["status"] == SER.S_STRUCTURAL
    # a structural zero names the rule of the game it rests on
    assert "LEAGUE_TIE_REACHABLE[(baseball, mlb)]" in tie["why"], tie["why"]
    assert "structural zero, not an unmeasured one" in tie["why"]
    assert (tie["rate"], tie["upper_95"]) == (0.0, 0.0)
    # soccer: 15 fixtures in unl, 28 in the family -- below 40 everywhere,
    # no cited rate: the conservative prior, never zero
    unl = cells["soccer/unl/h2h"]
    for e in (SER.E_PRICE, SER.E_VOID):
        est = unl["events"][e]
        assert est["status"] == SER.S_PRIOR, est
        assert est["rate"] is None
        assert est["upper_95"] >= SER.PRIOR_FLOOR_UPPER
        assert est["internal_observation"]["n"] == 15
    # the prior is never cheaper than an evidenced cell for the same event
    assert t["prior_upper_95"][SER.E_PRICE] >= price["upper_95"]
    assert t["prior_upper_95"][SER.E_VOID] >= void["upper_95"]
    # the draw is priced: not applicable, with the reason
    assert unl["events"][SER.E_TIE]["status"] == SER.S_NOT_APPLICABLE
    cfb = cells["football/cfb/h2h"]
    assert cfb["events"][SER.E_TIE]["status"] == SER.S_STRUCTURAL
    assert cfb["events"][SER.E_PRICE]["status"] == SER.S_PRIOR
    assert t["table_sha256"] and t["authority"] == SER.AUTHORITY


def test_postponement_and_suspension_are_one_measured_event_counted_once():
    t = SER.build_table(_production_shape())
    assert t["program_states"][SER.P_POSTPONEMENT] == SER.E_PRICE
    assert t["program_states"][SER.P_ABANDONMENT_SUSPENSION] == SER.E_PRICE
    mlb = next(c for c in t["cells"] if c["cell"] == "baseball/mlb/h2h")
    assert "JOINTLY" in mlb["events"][SER.E_PRICE]["joint"]
    assert set(mlb["events"]) == set(SER.EVENTS)


def test_a_fixture_counts_once_and_a_conflict_is_excluded_by_name():
    rows = []
    for i in range(40):
        # TWO markets on each fixture (per-side contracts): one fixture
        rows.append(_mkt("atc-epl-h%d-a%d-2026-10-01-h" % (i, i), "soccer",
                         "fx-%d" % i, bases=["VENUE_REPORTED_OUTCOME"]))
        rows.append(_mkt("atc-epl-h%d-a%d-2026-10-01-a" % (i, i), "soccer",
                         "fx-%d" % i, bases=["VENUE_REPORTED_OUTCOME"]))
    # a postponed fixture settles BOTH of its markets at a price: ONE event
    rows.append(_mkt("atc-epl-px-py-2026-10-02-h", "soccer", "fx-p",
                     numeric_reads=["0.41"]))
    rows.append(_mkt("atc-epl-px-py-2026-10-02-a", "soccer", "fx-p",
                     numeric_reads=["0.33"]))
    rows.append(_mkt("atc-epl-cx-cy-2026-10-02-h", "soccer", "fx-c",
                     bases=["VENUE_REPORTED_OUTCOME", "CONFIRMED_VOID"]))
    t = SER.build_table(rows)
    c = next(x for x in t["cells"] if x["cell"] == "soccer/epl/h2h")
    assert c["settled_fixtures"] == 41
    assert c["conflicting_fixtures"] == 1
    assert c["events"][SER.E_PRICE]["k"] == 1
    assert c["events"][SER.E_PRICE]["n"] == 41
    assert t["excluded"][SER.C_CONFLICT] == 1


def test_a_small_cell_falls_back_and_keeps_its_own_evidence():
    rows = []
    for i in range(45):
        rows.append(_mkt("aec-mlb-x%d-y%d-2026-09-20" % (i, i), "baseball",
                         "fx-a%d" % i, bases=["VENUE_SETTLEMENT_PRICE"]))
    # a NEW league token of the family with 3 fixtures, two of them voided
    for i in range(3):
        rows.append(_mkt("aec-kbo-x%d-y%d-2026-09-20" % (i, i), "baseball",
                         "fx-k%d" % i, bases=(["CONFIRMED_VOID"] if i < 2
                                              else ["VENUE_SETTLEMENT_PRICE"])))
    t = SER.build_table(rows)
    kbo = next(x for x in t["cells"] if x["cell"] == "baseball/kbo/h2h")
    v = kbo["events"][SER.E_VOID]
    # the family pool (48 fixtures) is used ...
    assert v["status"] == SER.S_POOLED_FAMILY
    # ... but 2 of 3 observed exceeds the pool's bound, so the cell's own
    # exact bound is carried: small-sample evidence is never discarded
    assert v["raised_by_internal_observation"] is True
    assert v["upper_95"] == pytest.approx(SER.interval(2, 3)["upper_95"])


def test_a_pool_never_counts_cells_where_the_event_cannot_happen():
    """College football cannot end level. 2,000 settled cfb fixtures must
    not dilute the NFL tie rate: the family pool counts ties only over cells
    where a tie can happen, so the cited NFL base rate stands."""
    rows = [_mkt("aec-cfb-c%d-d%d-2026-10-03" % (i, i), "football",
                 "fx-cfb-%d" % i, bases=["VENUE_SETTLEMENT_PRICE"])
            for i in range(2000)]
    rows += [_mkt("aec-nfl-n%d-m%d-2026-10-04" % (i, i), "football",
                  "fx-nfl-%d" % i, bases=["VENUE_SETTLEMENT_PRICE"])
             for i in range(10)]
    t = SER.build_table(rows)
    nfl = next(c for c in t["cells"] if c["cell"] == "football/nfl/h2h")
    tie = nfl["events"][SER.E_TIE]
    assert tie["status"] == SER.S_EXTERNAL, tie
    assert (tie["k"], tie["n"]) == (4, 1360)
    assert tie["upper_95"] == pytest.approx(SER.interval(4, 1360)["upper_95"])
    # the family pool's tie denominator is the NFL's 10 fixtures only
    fam = t["pooled"]["football/*/*"]
    assert fam["settled_fixtures"] == 2010
    assert fam["events"][SER.E_TIE]["n"] == 10
    # price / void CAN happen in cfb: there the 2,010 fixtures are pooled
    assert fam["events"][SER.E_PRICE]["n"] == 2010
    assert nfl["events"][SER.E_PRICE]["status"] == SER.S_POOLED_FAMILY
    # the decision carries the cited rate, not a diluted pool
    got = SER.decision_cost(t, sport_family="football", league="nfl",
                            market="h2h", holding_side="LONG", p=0.7,
                            price=0.65, qty=100)
    assert got["events"][SER.E_TIE]["rate_status"] == SER.S_EXTERNAL
    assert got["events"][SER.E_TIE]["rate_upper_95"] == pytest.approx(
        SER.interval(4, 1360)["upper_95"])
    # a decision on a cfb market built at decision time is still structural
    cfb = SER.decision_cost(t, sport_family="football", league="cfb",
                            market="h2h", holding_side="LONG", p=0.7,
                            price=0.65, qty=100)
    assert cfb["events"][SER.E_TIE]["rate_status"] == SER.S_STRUCTURAL


def test_the_prior_is_never_below_a_raised_small_sample_bound():
    """The module's invariant: an unmeasured cell is never cheaper than an
    upper bound the table holds for the same event -- including a small
    cell's bound raised to its own observation (the raise used to run AFTER
    the prior was computed)."""
    rows = [_mkt("aec-mlb-x%d-y%d-2026-09-20" % (i, i), "baseball",
                 "fx-a%d" % i, bases=["VENUE_SETTLEMENT_PRICE"])
            for i in range(45)]
    rows += [_mkt("aec-kbo-x%d-y%d-2026-09-20" % (i, i), "baseball",
                  "fx-k%d" % i, bases=(["CONFIRMED_VOID"] if i < 2
                                       else ["VENUE_SETTLEMENT_PRICE"]))
             for i in range(3)]
    rows += [_mkt("atc-unl-a%d-b%d-2026-10-01" % (i, i), "soccer",
                  "fx-u%d" % i, bases=["VENUE_REPORTED_OUTCOME"])
             for i in range(5)]
    t = SER.build_table(rows)
    cells = {c["cell"]: c for c in t["cells"]}
    kv = cells["baseball/kbo/h2h"]["events"][SER.E_VOID]
    assert kv["raised_by_internal_observation"] is True
    uv = cells["soccer/unl/h2h"]["events"][SER.E_VOID]
    assert uv["status"] == SER.S_PRIOR
    assert uv["upper_95"] >= kv["upper_95"], (uv["upper_95"], kv["upper_95"])
    assert t["prior_upper_95"][SER.E_VOID] >= kv["upper_95"]
    for c in t["cells"]:
        for e in SER.EVENTS:
            est = c["events"][e]
            if est["status"] in SER.EVIDENCED:
                assert t["prior_upper_95"][e] >= est["upper_95"], (c["cell"],
                                                                   e)


def test_an_external_rate_enters_only_cited_with_its_count_and_denominator():
    ok = SER.EXTERNAL_BASE_RATES[0]
    assert SER.admit_external(ok)["ok"]
    for c in ok["citations"]:
        for f in SER.CITATION_FIELDS:
            assert str(c[f]).strip(), f
        assert len(c["page_sha256"]) == 64
    assert (ok["count"], ok["denominator"]) == (4, 1360)
    bare = dict(ok, citations=())
    assert SER.R_EXT_NO_CITATION in SER.admit_external(bare)["refusals"]
    no_quote = dict(ok, citations=(dict(ok["citations"][0], quote=""),))
    assert SER.R_EXT_NO_CITATION in SER.admit_external(no_quote)["refusals"]
    rate_only = dict(ok, count=None, denominator=None, rate=0.003)
    assert SER.R_EXT_COUNTS in SER.admit_external(rate_only)["refusals"]
    assert SER.R_EXT_COUNTS in SER.admit_external(
        dict(ok, count=5, denominator=4))["refusals"]
    # an NFL cell with no settled fixture uses it; the cfb cell cannot tie
    t = SER.build_table([_mkt("aec-nfl-a-b-2026-10-04", "football", "fx-n",
                              bases=["VENUE_SETTLEMENT_PRICE"])])
    nfl = next(x for x in t["cells"] if x["cell"] == "football/nfl/h2h")
    tie = nfl["events"][SER.E_TIE]
    assert tie["status"] == SER.S_EXTERNAL
    assert (tie["k"], tie["n"]) == (4, 1360)
    assert tie["upper_95"] == pytest.approx(SER.interval(4, 1360)["upper_95"])
    assert tie["citations"][0]["reader"].startswith("fetch-docs run")


# ═════════════════════════════════════════════════════════════════════
# 4 · THE COST OF ONE DECISION
# ═════════════════════════════════════════════════════════════════════

def test_the_cost_is_probability_times_payout_difference_at_the_bound():
    t = SER.build_table(_production_shape())
    got = SER.decision_cost(t, sport_family="baseball", league="mlb",
                            market="h2h", holding_side="LONG", p=0.60,
                            price=0.50, qty=1000,
                            venue_rules_text=RECORDED_MLB_PROSE,
                            conditional_net_usd=95.0)
    assert got["status"] == SER.D_MEASURED, got
    assert got["gates_the_decision"] is False
    ev = got["events"]
    price = ev[SER.E_PRICE]
    # the venue settles a postponement at the last fair market price: a
    # variable payout in [0, 1] -- worst case 0, so the difference is p
    assert price["payout_per_contract_range"] == [0.0, 1.0]
    up = SER.interval(1, 65)["upper_95"]
    assert price["cost_per_contract_conservative"] == pytest.approx(up * 0.60)
    # no measured void and an unstated cancellation payout: the bound
    void = ev[SER.E_VOID]
    assert void["payout_per_contract_range"] == [0.0, 1.0]
    assert void["cost_per_contract_conservative"] == pytest.approx(
        SER.interval(0, 65)["upper_95"] * 0.60)
    assert ev[SER.E_TIE]["cost_per_contract_conservative"] == 0.0
    total = (price["cost_per_contract_conservative"]
             + void["cost_per_contract_conservative"])
    assert got["expected_exception_cost_usd_conservative"] == pytest.approx(
        total * 1000)
    assert got["conditional_net_less_conservative_exception_cost_usd"] == \
        pytest.approx(95.0 - total * 1000)
    # the last fair market price is not a value, so there is no point
    assert got["expected_exception_cost_usd_point"] is None
    assert got["point_unavailable_because"]


def test_a_stated_payout_prices_the_state_exactly():
    # SYNTHETIC venue sentences, in the constructions the clause reader
    # supports, so the payout comes from the text and not from a default
    text = ("A tie resolves 50-50. If the game is cancelled, the market "
            "resolves 50-50.")
    t = SER.build_table([_mkt("aec-nfl-a-b-2026-10-04", "football", "fx-n",
                              bases=["VENUE_SETTLEMENT_PRICE"])])
    fav = SER.decision_cost(t, sport_family="football", league="nfl",
                            market="h2h", holding_side="LONG", p=0.70,
                            price=0.65, qty=100, venue_rules_text=text)
    tie = fav["events"][SER.E_TIE]
    assert tie["payout_per_contract_range"] == [0.5, 0.5]
    up = SER.interval(4, 1360)["upper_95"]
    # a favourite loses (p - 0.5) on a tie, at the rate's UPPER bound
    assert tie["cost_per_contract_conservative"] == pytest.approx(up * 0.20)
    assert tie["cost_per_contract_point"] == pytest.approx(4 / 1360 * 0.20)
    assert fav["events"][SER.E_VOID]["payout_per_contract_range"] == [0.5, 0.5]
    # an underdog GAINS on a tie: the conservative end is the LOWER bound
    dog = SER.decision_cost(t, sport_family="football", league="nfl",
                            market="h2h", holding_side="LONG", p=0.30,
                            price=0.25, qty=100, venue_rules_text=text)
    lo = SER.interval(4, 1360)["lower_95"]
    assert dog["events"][SER.E_TIE]["cost_per_contract_conservative"] == \
        pytest.approx(lo * (0.30 - 0.5))
    # the NFL's price / void rates are unmeasured: PRIOR_BOUNDED, named
    assert fav["status"] == SER.D_PRIOR
    assert SER.E_PRICE in fav["why"] and SER.E_VOID in fav["why"]


def test_unknown_is_never_zero_even_with_no_record_at_all():
    empty = SER.build_table([])
    got = SER.decision_cost(empty, sport_family="hockey", league="nhl",
                            market="h2h", holding_side="SHORT", p=0.55,
                            price=0.48, qty=10)
    assert got["status"] == SER.D_PRIOR
    assert got["cell_built_at_decision"] is True
    for e in SER.EVENTS:
        assert got["events"][e]["rate_status"] == SER.S_PRIOR
        assert got["events"][e]["rate_upper_95"] >= SER.PRIOR_FLOOR_UPPER
    assert got["expected_exception_cost_usd_conservative"] > 0


@pytest.mark.parametrize("kw,why", [
    ({"p": None}, SER.R_NO_PROBABILITY), ({"price": None}, SER.R_NO_PRICE),
    ({"price": 1.0}, SER.R_NO_PRICE), ({"qty": 0}, SER.R_NO_QTY),
    ({"sport_family": None}, SER.R_NO_FAMILY)])
def test_a_missing_input_is_unavailable_with_its_reason(kw, why):
    base = dict(sport_family="baseball", league="mlb", market="h2h",
                holding_side="LONG", p=0.6, price=0.5, qty=10)
    base.update(kw)
    got = SER.decision_cost(SER.build_table(_production_shape()), **base)
    assert got["status"] == SER.D_UNAVAILABLE and got["why"] == why
    assert SER.decision_cost(None, **dict(base, **{"p": 0.6})).get(
        "why") in (SER.R_NO_TABLE, why)
    unread = {"ok": False, "refusal": SER.R_TABLE_UNREADABLE}
    assert SER.decision_cost(unread, sport_family="baseball", league="mlb",
                             market="h2h", holding_side="LONG", p=0.6,
                             price=0.5, qty=10)["why"] == SER.R_NO_TABLE


# ═════════════════════════════════════════════════════════════════════
# 5 · ON A REAL DATABASE, WRITTEN BY THE REAL WRITERS
# ═════════════════════════════════════════════════════════════════════

async def _valuation(conn, *, slug, family, event_key, decided_at):
    """One entry-experiment valuation, as the collector inserts it (the
    tests/paper_live_fixture.valuation shape, with its sport and fixture)."""
    from sportsassets import bettor_external_shadow as ext
    from tests import paper_live_fixture as PL
    return await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, condition_id, "
        " us_market_slug, contract_selection, sport_family, market, period, "
        " raw_odds, outcomes_priced, expected_outcomes, observed_at, "
        " received_at, probability, decision, admissible, refusals, why, "
        " payout_event, payout_is_complement, buy_intent, ladder_side, "
        " record_purpose, decided_at, event_key, settlement_comparison, "
        " calibration_only_evidence) "
        "VALUES ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION',"
        " 'pinnapi.com/raw-websocket','pinnacle','power','PMUS',$2,$2,'HOME',"
        " $3,'h2h','FULL_GAME','{}'::jsonb,2,2,to_timestamp($4 - 5),"
        " to_timestamp($4 - 4),0.55,'NO_TRADE',false,"
        " ARRAY['VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'],'synthetic',"
        " 'HOME',false,'ORDER_INTENT_BUY_LONG','ASK','CALIBRATION_ONLY',"
        " to_timestamp($4),$5,$6::jsonb,$7::jsonb) RETURNING id",
        ext.EXPERIMENT_ID, slug, family, float(decided_at), event_key,
        json.dumps(PL.settlement_comparison("INCOMPATIBLE"), default=str),
        json.dumps({"usable_for_orders": False,
                    "venue_read_refusal": "VENUE_BOOK_CURRENCY_NOT_"
                                          "ESTABLISHED"}))


async def _purge(conn, prefix):
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM external_valuations "
                           " WHERE us_market_slug LIKE $1", prefix + "%")


@pg
async def test_the_real_join_and_the_real_ledger_feed_the_table(monkeypatch):
    from sportsassets import bettor_live_read as lr
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_simulator as SIM
    from sportsassets.workers import ext_pinnacle_loop as X
    conn = await H.connect()
    tag = uuid.uuid4().hex[:6]
    lg = "tq%s" % tag
    prefix = "aec-%s-" % lg
    old = time.time() - 3 * 3600
    answers = {}
    try:
        # 44 fixtures: 41 paid one side, one settled at 0.485 (the production
        # bal-nyy shape), one DECLARED void, one the venue has not settled
        for i in range(44):
            slug = "%sh%02d-a%02d-2026-09-27" % (prefix, i, i)
            await _valuation(conn, slug=slug, family="baseball",
                             event_key="ev-%s-%d" % (tag, i), decided_at=old)
            if i == 41:
                answers[slug] = {"status": lr.RESOLVED,
                                 "settlement_price": 0.485,
                                 "settlement_price_raw": "0.485"}
            elif i == 42:
                answers[slug] = {"status": lr.RESOLVED,
                                 "settlement_price": 0.5,
                                 "settlement_price_raw": "0.5", "void": True}
            elif i == 43:
                answers[slug] = {"status": lr.PENDING}
            else:
                answers[slug] = {"status": lr.RESOLVED,
                                 "settlement_price": float(i % 2),
                                 "settlement_price_raw": str(i % 2)}
        monkeypatch.setattr(X, "_read_resolution_blocking",
                            lambda s: answers.get(s, {"status": lr.PENDING}))
        got = await X.join_outcomes(conn, limit=500)
        assert got["ran"], got
        assert got["void"] >= 1 and got["neither_side_paid"] >= 1, got
        # the join wrote the production shapes
        row = await conn.fetchrow(
            "SELECT outcome_basis, settlement_read FROM external_valuations "
            " WHERE us_market_slug=$1", "%sh41-a41-2026-09-27" % prefix)
        assert row["outcome_basis"] is None and row["settlement_read"] == \
            "0.485"
        # A HELD paper position on one more fixture, settled VOID_REFUND by
        # the paper ledger itself
        pslug = "%sph-pa-2026-09-27" % prefix
        await _valuation(conn, slug=pslug, family="baseball",
                         event_key="ev-%s-p" % tag, decided_at=old)
        a = await H.new_account(conn, "exc")
        fee = H.zero_fee
        o = H.order(a, key="e", slug=pslug, qty=100, limit=0.50,
                    group_id="paper_g_exc_%s" % tag, at=H.T0)
        go = await L.submit_order(conn, o, fee_fn=fee, now=H.T0)
        assert go["ok"], go
        await H.observe(conn, pslug, H.T0 + 3, offers=[(0.50, 500)])
        r = await SIM.simulate_order(conn, go["order"]["order_id"],
                                     now=H.T0 + 4, fee_fn=fee)
        assert r["state"] == "FILLED", r
        s = await L.settle(conn, account_id=a["account_id"],
                           group_id="paper_g_exc_%s" % tag, slug=pslug,
                           holding_side="LONG",
                           settlement_event_key="venue-final:%s" % pslug,
                           outcome="VOID_REFUND",
                           evidence={"rule": "TEST venue-declared void"},
                           evidence_source="TEST", at=H.T0 + 10,
                           session_id=a["session_id"])
        assert s["ok"], s

        t = await SER.measure(conn)
        assert t["ok"], t
        cell = next(c for c in t["cells"] if c["cell"] ==
                    "baseball/%s/h2h" % lg)
        # 41 binary + 1 price + 1 declared void + 1 paper void; the pending
        # fixture is not terminal
        assert cell["settled_fixtures"] == 44, cell
        price = cell["events"][SER.E_PRICE]
        void = cell["events"][SER.E_VOID]
        assert price["status"] == SER.S_MEASURED
        assert (price["k"], price["n"]) == (1, 44)
        assert (void["k"], void["n"]) == (2, 44)
        assert void["upper_95"] == pytest.approx(
            SER.interval(2, 44)["upper_95"])
        # the synthetic league token is not a declared baseball league, so
        # whether its games can end level is UNKNOWN: it is counted (0 ties
        # of 44) with the applicability named -- never a structural zero
        # inherited from the family (review, R30C: KBO / NPB can end level)
        tie = cell["events"][SER.E_TIE]
        assert tie["status"] == SER.S_MEASURED and tie["k"] == 0, tie
        assert tie["applicability_unknown"]
        # the recorded venue-vs-book comparison of the same rows: the venue's
        # last-fair-market-price rule against the book's stake return
        div = cell["rule_divergence"]
        assert div["status"] == "MEASURED"
        assert div["markets_compared"] == 45
        assert div["markets_with_an_exceptional_mismatch"] >= 1
        assert t["source"]["paper_settled_positions"] >= 1
    finally:
        await _purge(conn, prefix)
        await conn.close()


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pg
async def test_the_endpoint_is_read_only_and_reports_the_table(monkeypatch):
    from fastapi import HTTPException
    from sportsassets.api import command_risk_evidence as API
    conn = await H.connect()
    try:
        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(API, "_pool", pool)
        got = await API.settlement_exception_risk(Response(), limit=5)
        assert got["table"]["ok"] is True
        assert got["gates_the_decision"] is False
        assert got["authority"] == SER.AUTHORITY
        assert isinstance(got["recent_decisions"], list)

        async def writer(c, **kw):
            await c.execute("INSERT INTO us_premap (identifier) VALUES "
                            "('must-not-be-written')")
        with pytest.raises(HTTPException) as e:
            await API._read_only(writer, reason="X")
        assert e.value.status_code == 503
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 6 · ON THE CANONICAL DECISION, THROUGH THE REAL PAPER PASS -- NOT A GATE
# ═════════════════════════════════════════════════════════════════════

async def _cg_pass(conn, monkeypatch, *, tag):
    from sportsassets import live_parity as LP
    from sportsassets.agents import paper_benchmark as PB
    from sportsassets.agents import paper_derek as PD
    from sportsassets.agents import paper_runtime as PRT
    from tests import paper_live_fixture as PL
    now = time.time() + 5.0
    ctl = await LP.control(conn)
    if ctl.get("halted"):
        await LP.clear_halt(conn, actor="test harness (human operator)",
                            reason="isolate this test")
    if await conn.fetchval("SELECT count(*) FROM execmirror_control") == 0:
        await conn.execute("INSERT INTO execmirror_control DEFAULT VALUES")
    acct = await PL.new_account(conn, "exc%s" % tag, now=now)
    t = PL.Transport(now)
    v = await PL.valuation(conn, decided_at=now - 10, p_pin=0.62,
                           compatibility="INCOMPATIBLE")
    t.set(v["slug"], offers=[(0.50, 2000)], bids=[(0.48, 2000)])
    client = PL.client(t)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()

    async def nosleep(_):
        return None
    t.t = max(t.t, now)
    p1 = await PRT.paper_pass(conn, now=now, account_id=acct["account_id"],
                              market_data=client, config=acct["config"],
                              force=True, fee_fn=H.flat_fee(0.01),
                              sleep=nosleep)
    assert p1["ran"] and not p1["errors"], p1["errors"]
    d = await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE session_id=$1 AND valuation_id=$2"
        " AND strategy=$3", acct["session_id"], v["valuation_id"],
        PB.CG_STRATEGY)
    it = await conn.fetchrow(
        "SELECT * FROM canonical_decision_intents WHERE decision_id=$1",
        d["decision_id"])
    o = await conn.fetchrow(
        "SELECT * FROM paper_orders WHERE decision_id=$1 AND role='ENTRY'",
        d["decision_id"])
    return d, it, o


@pytest.fixture
def cg_on(monkeypatch, new_strategies_off):
    from sportsassets import canonical_components as CC
    from sportsassets import live_parity as LP
    from sportsassets.agents import paper_benchmark as PB
    from tests import paper_live_fixture as PL
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    CC.reset_cache()
    LP.install()
    yield
    LP.uninstall()
    CC.reset_cache()


@pg
async def test_the_cost_rides_on_the_intent_and_gates_nothing(
        monkeypatch, cg_on):
    from sportsassets import live_parity as LP
    from tests import paper_live_fixture as PL
    conn = await H.connect()
    try:
        await PL.purge_everything(conn)
        d, it, o = await _cg_pass(conn, monkeypatch, tag="a")
        assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
        assert it is not None and LP.verify_intent(dict(it))
        ev = H.j(it["evidence"])
        c = ev["settlement_exception_risk"]
        # the table is readable here, so the component is computed (an
        # UNAVAILABLE would carry its reason; it is not expected on this DB)
        assert c["status"] in (SER.D_MEASURED, SER.D_PRIOR), c
        assert c["gates_the_decision"] is False
        assert c["authority"] == SER.AUTHORITY
        # stored in the intent's canonical decimal form (the sha's normal
        # form), so read back as numbers
        assert float(c["expected_exception_cost_usd_conservative"]) > 0
        assert float(c["qty"]) == pytest.approx(float(it["target_qty"]))
        # the decision's own venue text: the recorded MLB prose settles a
        # postponement at the last fair market price
        from sportsassets import bettor_settlement_terms as ST
        assert c["events"][SER.E_PRICE]["venue_rule"] == \
            ST.PAY_LAST_FAIR_MARKET_PRICE
        # it is not one of the parity ledger's evidence ids
        assert "settlement_exception_risk" not in LP.evidence_ids(dict(it))
        qty_measured = float(o["qty"])
        assert qty_measured == float(it["target_qty"])
        await PL.purge_everything(conn)

        # THE SAME DECISION WITH AN ENORMOUS EXCEPTION COST: the verdict, the
        # order and its size do not move -- the cost is evidence, not a gate
        real = SER.decision_cost

        def huge(*a, **kw):
            got = real(*a, **kw)
            return dict(got, status=SER.D_MEASURED,
                        expected_exception_cost_usd_conservative=1e9,
                        conditional_net_less_conservative_exception_cost_usd=(
                            -1e9))
        monkeypatch.setattr(SER, "decision_cost", huge)
        from sportsassets import canonical_components as CC
        CC.reset_cache()
        d2, it2, o2 = await _cg_pass(conn, monkeypatch, tag="b")
        assert d2["verdict"] == "ENTER"
        assert float(H.j(it2["evidence"])["settlement_exception_risk"][
            "expected_exception_cost_usd_conservative"]) == 1e9
        assert float(o2["qty"]) == qty_measured
        assert float(o2["limit_price"]) == float(o["limit_price"])
        assert o2["holding_side"] == o["holding_side"]
    finally:
        await PL.purge_everything(conn)
        await conn.close()


def test_nan_never_reaches_the_intent():
    t = SER.build_table(_production_shape())
    got = SER.decision_cost(t, sport_family="baseball", league="mlb",
                            market="h2h", holding_side="LONG",
                            p=float("nan"), price=0.5, qty=1)
    assert got["status"] == SER.D_UNAVAILABLE
    ok = SER.decision_cost(t, sport_family="baseball", league="mlb",
                           market="h2h", holding_side="SHORT", p=0.4,
                           price=0.55, qty=3)

    def walk(v):
        if isinstance(v, float):
            assert math.isfinite(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(ok)
