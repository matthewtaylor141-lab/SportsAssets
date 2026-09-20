"""X1 IS EXPERIMENTAL MODEL EVIDENCE, NOT BETTOR EV ENGINE PERFORMANCE.

Owner directive 2026-09-20, after the first X1 cohort landed negative:

    "X1 IS AN EXPERIMENTAL BETTOR MODEL. X1 IS NOT YET THE
    DECISION-GRADE BETTOR EV ENGINE... because X1 currently does not
    produce P_BETTOR, P_FILL, CONSERVATIVE_ACTION_EV, and
    DECISION_GRADE = 0."

THE DEFECT THIS FILE PINS. `_headline` set `isBettorEvPerformance` to
True for anything that was not the control -- so the moment X1 opened
its first position, a −$646.66 experimental markout was flagged as
BETTOR EV engine performance on the panel management reads. The
attribution was wrong in the direction that matters: it credited an
engine that has never made a trade with an experiment's loss.

THE OTHER FIVE CORRECTIONS, each its own section below:

  §3  a markout is not P&L. It is where the position COULD have been
      exited on the observed book. Realized and settled are
      NOT_APPLICABLE -- not zero -- because nothing has exited.
  §4  capital comes from the position event series, never from entry
      notional. Four entries in one market overlap in time.
  §5  four entries in one market are ONE independent event.
  §6  a latency number requires one clock. These rows have two, so no
      number is reported at all.
  §7  EXACT_SAME_CONTRACT proves which instrument is held. It does not
      prove the settlement proposition has been read correctly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import shadow_experimental_capital as cap
from sportsassets import shadow_latency_integrity as lat
from sportsassets.api import command_experimental as ce

T0 = datetime(2026, 9, 20, 1, 56, 43, 514000, tzinfo=timezone.utc)
X1 = "X1_SHORT_HORIZON_DIRECTION"
X1C = "X1C_NULL_CONTROL"


def position(market, notional, *, at, qty=3000.0, experiment=X1):
    return {"position_id": "xpos_%s_%s" % (market[-4:], int(notional)),
            "experiment_id": experiment, "market_id": market,
            "side": "LONG", "opened_at": at, "entry_qty": qty,
            "entry_vwap": 0.306667, "entry_notional_usd": notional,
            "status": "OPEN"}


# THE FOUR REAL X1 ENTRIES, at their production instants.
FIRST_COHORT = [
    position("asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5", 920.0, at=T0),
    position("asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5", 920.0,
             at=T0 + timedelta(seconds=66.9)),
    position("asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5", 600.0,
             at=T0 + timedelta(seconds=132.4), qty=2000.0),
    position("asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5", 600.0,
             at=T0 + timedelta(seconds=197.2), qty=2000.0),
]


# ── the attribution itself ───────────────────────────────────────────


def test_x1_is_not_bettor_ev_performance():
    """THE REGRESSION GUARD. This was True and it should never have
    been: X1 emits a signed drift and nothing the EV engine emits."""
    row = _headline_row(X1)
    out = ce._headline(row)
    assert out["isBettorEvPerformance"] is False
    assert out["performanceClass"] == ce.CLASS_X1
    assert out["portfolio"] == "BETTOR X1 EXPERIMENTAL"
    assert out["decisionGrade"] is False


def test_the_control_is_not_bettor_ev_performance_either():
    out = ce._headline(_headline_row(X1C))
    assert out["isBettorEvPerformance"] is False
    assert out["performanceClass"] == ce.CLASS_CONTROL
    assert out["isNullControl"] is True


def test_only_the_decision_grade_class_can_ever_be_ev_performance():
    """Stated as a property of the mapping, so a third experiment added
    to this lane cannot quietly become EV performance."""
    assert ce.DECISION_GRADE_CLASS == ce.CLASS_EV
    assert ce.CLASS_EV not in ce.PERFORMANCE_CLASS.values(), (
        "no experiment in the experimental lane may map to the "
        "decision-grade class")


# ── §3: a markout is not P&L ─────────────────────────────────────────


def test_the_headline_names_the_markout_and_refuses_to_call_it_pnl():
    out = ce._headline(_headline_row(X1, pnl=-646.66))
    assert out["currentExecutableMarkoutUsd"] == -646.66
    assert out["currentExecutableMarkoutStatus"] == cap.UNREALIZED_MARKOUT
    # NOT ZERO -- not applicable. Nothing has exited or settled.
    assert out["realizedPnlUsd"] is None
    assert out["realizedPnlStatus"] == cap.NOT_APPLICABLE
    assert out["settledPnlUsd"] is None
    assert out["settledPnlStatus"] == cap.NOT_APPLICABLE
    assert out["netShadowPnlIsMarkoutNotRealized"] is True


def test_economics_separates_the_four_figures():
    out = cap.economics(entry_notional_usd=3040.0,
                        eligible_markouts={"300S": -646.66, "60S": -275.0})
    assert out["ENTRY_NOTIONAL_PLAYED_USD"] == 3040.0
    m = out["CURRENT_EXECUTABLE_MARKOUT"]
    assert m["300S"]["EXECUTABLE_MARKOUT_USD"] == -646.66
    assert m["300S"]["RETURN_ON_ENTRY_NOTIONAL"] == pytest.approx(-0.212717,
                                                                  abs=1e-6)
    assert m["300S"]["STATUS"] == cap.UNREALIZED_MARKOUT
    assert out["REALIZED_PNL_STATUS"] == cap.NOT_APPLICABLE
    assert out["SETTLED_PNL_STATUS"] == cap.NOT_APPLICABLE
    assert "not realized P&L and not settlement" in out["why"]


def test_a_realized_figure_is_reported_when_one_actually_exists():
    """The statuses are derived, not hardcoded: give it a realized
    number and it says REALIZED."""
    out = cap.economics(entry_notional_usd=100.0, eligible_markouts={},
                        realized_usd=-12.5, settled_usd=-13.0)
    assert out["REALIZED_PNL_USD"] == -12.5
    assert out["REALIZED_PNL_STATUS"] == cap.REALIZED
    assert out["SETTLED_PNL_STATUS"] == cap.SETTLED


# ── §4: capital from the event series ────────────────────────────────


def test_capital_is_additive_while_every_position_is_open():
    """Four overlapping entries tie up four sets of dollars. Nothing is
    inferred from entry notional -- the timeline is walked."""
    now = T0 + timedelta(hours=1)
    out = cap.capital(FIRST_COHORT, now=now)
    assert out["ENTRY_NOTIONAL_PLAYED_USD"] == 3040.0
    assert out["CURRENT_CAPITAL_DEPLOYED_USD"] == 3040.0
    assert out["PEAK_CAPITAL_DEPLOYED_USD"] == 3040.0
    assert out["OPEN_POSITIONS"] == 4
    assert out["CLOSED_POSITIONS"] == 0
    assert out["basis"] == "POSITION_EVENT_TIME_SERIES"


def test_capital_turns_of_one_means_nothing_was_recycled():
    """The number and its meaning, together. 1.0 here is not 'turned
    over once' -- it is 'never turned over', because no position has
    ever closed in this schema."""
    out = cap.capital(FIRST_COHORT, now=T0 + timedelta(hours=1))
    assert out["CAPITAL_TURNS"] == 1.0
    assert "no capital has been recycled" in out["why"]


def test_average_capital_is_time_weighted_not_the_mean_of_entries():
    """The entries arrive staggered, so the time-weighted average over
    the first hour is BELOW the peak -- which the mean of the four
    entry notionals could never show."""
    out = cap.capital(FIRST_COHORT, now=T0 + timedelta(hours=1))
    assert out["AVERAGE_CAPITAL_DEPLOYED_USD"] < out[
        "PEAK_CAPITAL_DEPLOYED_USD"]
    assert out["AVERAGE_CAPITAL_DEPLOYED_USD"] > 2900.0   # staggered by ~3min
    # CAPITAL_HOURS is the integral, so it is near (average x 1 hour).
    assert out["CAPITAL_HOURS"] == pytest.approx(
        out["AVERAGE_CAPITAL_DEPLOYED_USD"], rel=0.01)


def test_a_closed_position_stops_tying_up_capital_and_creates_a_turn():
    """The same function computes real recycling the moment an exit
    path exists -- it is not written for the all-open case."""
    a = dict(FIRST_COHORT[0])
    a["closed_at"] = T0 + timedelta(seconds=30)
    b = position("m2", 920.0, at=T0 + timedelta(seconds=60))
    out = cap.capital([a, b], now=T0 + timedelta(seconds=120))
    assert out["CURRENT_CAPITAL_DEPLOYED_USD"] == 920.0   # only b open
    assert out["PEAK_CAPITAL_DEPLOYED_USD"] == 920.0      # never both
    assert out["CAPITAL_TURNS"] == 2.0                    # 1840 / 920
    assert out["CLOSED_POSITIONS"] == 1


def test_capital_is_never_derived_from_entry_notional():
    import inspect
    src = inspect.getsource(cap.capital)
    assert "opened_at" in src and "_closed_at" in src
    assert "ENTRY_NOTIONAL_PLAYED_USD" in src          # reported...
    # ...but peak/current come from the walk, not from that sum.
    assert "deployed += delta" in src


# ── §5: concentration ────────────────────────────────────────────────


def test_four_entries_in_one_market_are_one_independent_event():
    out = cap.concentration(FIRST_COHORT)
    assert out["POSITIONS"] == 4
    assert out["UNIQUE_MARKETS_TRADED"] == 1
    assert out["UNIQUE_EVENTS_TRADED"] == 1
    assert out["INDEPENDENT_EVENTS"] == 1
    assert out["MAX_MARKET_CONCENTRATION_PCT"] == 100.0
    assert out["MAX_EVENT_CONCENTRATION_PCT"] == 100.0
    assert "not independent samples" in out["why"]


def test_positions_and_notional_are_reported_per_market():
    out = cap.concentration(FIRST_COHORT)
    row = out["PER_MARKET"][0]
    assert row["market"].endswith("1h-pos-13pt5")
    assert row["POSITIONS_PER_MARKET"] == 4
    assert row["NOTIONAL_PER_MARKET_USD"] == 3040.0
    assert row["SHARE_PCT"] == 100.0


def test_two_markets_in_one_contest_are_still_one_event():
    """Different lines on the same game resolve together. The event key
    is the venue symbol's own grammar, not a fuzzy match."""
    mixed = [
        position("asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5", 500.0, at=T0),
        position("asc-cfb-nill-arz-2026-09-19-1h-neg-6pt5", 500.0, at=T0),
    ]
    out = cap.concentration(mixed)
    assert out["UNIQUE_MARKETS_TRADED"] == 2
    assert out["UNIQUE_EVENTS_TRADED"] == 1
    assert out["INDEPENDENT_EVENTS"] == 1
    assert out["MAX_EVENT_CONCENTRATION_PCT"] == 100.0


def test_genuinely_separate_contests_count_separately():
    out = cap.concentration([
        position("asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5", 500.0, at=T0),
        position("asc-cfb-ohio-sala-2026-09-19-pos-24pt5", 500.0, at=T0),
    ])
    assert out["UNIQUE_EVENTS_TRADED"] == 2
    assert out["INDEPENDENT_EVENTS"] == 2
    assert out["MAX_EVENT_CONCENTRATION_PCT"] == 50.0


# ── §6: one clock, or no number ──────────────────────────────────────


def _row(**kw):
    base = {"venue_source_timestamp": "2026-09-20T01:56:21.302733768Z",
            "bettor_received_timestamp": T0 + timedelta(seconds=5.714),
            "features_sealed_timestamp": T0 + timedelta(seconds=2.692),
            "model_start_timestamp": T0 + timedelta(seconds=6.241),
            "model_end_timestamp": T0 + timedelta(seconds=6.241),
            "decision_timestamp": T0,
            "modeled_arrival_timestamp": T0 + timedelta(seconds=6.491),
            "modeled_execution_latency_ms": 6491.6}
    base.update(kw)
    return base


def test_the_production_row_is_a_clock_domain_conflict():
    """THE REAL FIRST-TRADE ROW. DECISION precedes FEATURES_SEALED,
    BETTOR_RECEIVED and MODEL_START/END. Two clocks."""
    out = lat.integrity(_row())
    assert out["LATENCY_STATUS"] == lat.CONFLICT
    assert out["latencyUsableEconomically"] is False
    assert out["violations"], "the ordering breach must be named"


def test_no_latency_number_is_manufactured():
    """"Do not manufacture a latency number." The 6,491.6 ms in the
    ledger is withheld, not adjusted, not absolute-valued."""
    out = lat.integrity(_row())
    assert out["MODELED_EXECUTION_LATENCY_MS"] is None
    assert out["MODELED_EXECUTION_LATENCY_STATUS"] == lat.CONFLICT


def test_the_one_modelled_component_survives_and_is_labelled_as_modelled():
    """MODELED_ARRIVAL - MODEL_END has both endpoints on one clock, so
    it is readable -- and it is named a configured constant rather than
    an observed latency."""
    out = lat.integrity(_row())
    assert out["MODELED_ARRIVAL_MINUS_MODEL_END_MS"] == 250.0
    assert "not an observed execution latency" in out["modeledComponentNote"]


def test_a_monotonic_row_reports_its_latency():
    """The rule is the ordering, not a blanket refusal: fix the clocks
    and the number comes back with no edit here."""
    good = _row(decision_timestamp=T0 + timedelta(seconds=6.241),
                features_sealed_timestamp=T0 + timedelta(seconds=2.0),
                bettor_received_timestamp=T0 + timedelta(seconds=1.0),
                model_start_timestamp=T0 + timedelta(seconds=3.0),
                model_end_timestamp=T0 + timedelta(seconds=6.0),
                modeled_arrival_timestamp=T0 + timedelta(seconds=6.491),
                modeled_execution_latency_ms=250.0)
    out = lat.integrity(good)
    assert out["LATENCY_STATUS"] == lat.OK
    assert out["latencyUsableEconomically"] is True
    assert out["MODELED_EXECUTION_LATENCY_MS"] == 250.0


def test_the_violating_pair_is_named_so_the_clocks_can_be_fixed():
    out = lat.integrity(_row())
    pairs = {(v["expectedEarlier"], v["expectedLater"])
             for v in out["violations"]}
    assert ("BETTOR_RECEIVED_TIME", "FEATURE_SEAL_TIME") in pairs or \
           ("MODEL_END_TIME", "DECISION_COMMIT_TIME") in pairs
    assert lat.census([out])["usableForEconomics"] == 0


def test_the_venue_string_is_not_coerced_into_a_clock():
    """SOURCE_EVENT_TIME is the venue's own text. Naming it as
    not-compared beats parsing it into a clock it may not share."""
    out = lat.integrity(_row())
    assert out["notCompared"] == ["SOURCE_EVENT_TIME"]


def test_nothing_repairs_a_stored_row():
    """"Do not silently repair old rows." The check is read-time."""
    import inspect
    src = inspect.getsource(lat).lower()
    for stmt in ("update ", "insert ", "delete "):
        assert stmt not in src, stmt


# ── §7: identity is not settlement ───────────────────────────────────


class SettlementPool:
    ROWS = [{"market_id": "asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5",
             "outcome_leg": "yes",
             "identity_status": "EXACT_SAME_CONTRACT",
             "conflict": "instrument_rules and instrument_rules_display "
                         "describe opposite propositions"}]

    async def fetch(self, sql, *args):
        if "settlement_prose_conflict" in sql:
            return [{"market_id": r["market_id"],
                     "outcome_leg": r["outcome_leg"],
                     "identity_status": r["identity_status"],
                     "settlement_prose_conflict": r["conflict"]}
                    for r in self.ROWS]
        return []


@pytest.mark.asyncio
async def test_an_exact_identity_does_not_certify_settlement_semantics():
    out = await cap.settlement_semantics(
        SettlementPool(), ["asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5"])
    assert out["SETTLEMENT_SEMANTICS_STATUS"] == cap.SETTLEMENT_CONFLICT
    assert out["settlementPnlUsable"] is False
    assert out["conflicts"][0]["identityStatus"] == "EXACT_SAME_CONTRACT"
    assert "does not prove the settlement proposition" in out[
        "identityIsNotSettlement"]


@pytest.mark.asyncio
async def test_markout_evidence_survives_a_settlement_conflict():
    """"Markout evidence can remain valid as price evidence." Only
    settlement P&L is withheld."""
    out = await cap.settlement_semantics(
        SettlementPool(), ["asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5"])
    assert "do not depend on the settlement rule" in out["why"]


class CleanPool(SettlementPool):
    async def fetch(self, sql, *args):
        return []


@pytest.mark.asyncio
async def test_a_market_with_no_conflict_reads_identified():
    out = await cap.settlement_semantics(CleanPool(), ["m1"])
    assert out["SETTLEMENT_SEMANTICS_STATUS"] == cap.SETTLEMENT_OK
    assert out["settlementPnlUsable"] is True


# ── the panel keeps three classes apart ──────────────────────────────


def _headline_row(experiment, *, pnl=-646.66):
    return {"experiment_id": experiment,
            "latency_regime": "DIRECT_INSTITUTIONAL_WORKER",
            "trades": 4, "decisions": 4, "entry_played": 3040.0,
            "unfilled": 960.0, "intended": 4000.0, "marked_n": 4,
            "unmarked_n": 0, "pnl_exec": pnl, "pnl_mid": -440.0,
            "wins": 0, "losses": 4, "pnl_today": pnl,
            "last_decision": T0}


def test_the_three_classes_are_named_and_distinct():
    assert ce.CLASS_EV != ce.CLASS_X1 != ce.CLASS_CONTROL
    assert set(ce.CLASS_LABEL) == {ce.CLASS_EV, ce.CLASS_X1,
                                   ce.CLASS_CONTROL}
    assert ce.CLASS_LABEL[ce.CLASS_X1] == "BETTOR X1 EXPERIMENTAL"
    assert ce.CLASS_LABEL[ce.CLASS_EV] == "BETTOR EV SHADOW"


def test_no_statement_totals_across_the_classes():
    """The panel cannot render a combined figure because the builder
    never computes one -- there is no all-experiments branch."""
    import inspect
    src = inspect.getsource(ce._management)
    assert "classes[klass]" in src
    assert "sum(" not in src.split("for experiment")[1].split("return")[0], (
        "the per-class loop must not accumulate a cross-class total")
