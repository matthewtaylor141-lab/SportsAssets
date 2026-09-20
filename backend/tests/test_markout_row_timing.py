"""A ROW EARNS ELIGIBILITY BY WHEN IT WAS OBSERVED, NOT BY ITS LABEL.

Owner directive 2026-09-20 §1:

    "Verify that performance eligibility for an individual 60S markout
    depends on the ACTUAL realized observation offset satisfying the
    frozen 60S timing/tolerance contract. A row labelled '60S' must not
    become performance evidence merely because TARGET_HORIZON = 60S...
    If actual timing falls outside the frozen tolerance:
    TIMING_STATUS = OUTSIDE_TOLERANCE, PERFORMANCE_ELIGIBLE = FALSE.
    No interpolation. No nearest observation outside tolerance. No zero
    P&L. No deletion."

    §2: "Apply the same general row-level timing principle to 300S. Do
    not assume a label proves its realized horizon. The recorded
    timestamps are authoritative."

TWO GATES, AND A ROW NEEDS BOTH:

  the HORIZON gate   -- can a 30s markout be resolved on a ~61s capture
                        grid at all? (shadow_markout_observability)
  the ROW gate       -- did THIS observation land inside the frozen
                        contract? (shadow_markout_timing)

The first can retire a whole horizon; the second retires one
measurement while its siblings stand. They are reported apart so a
reader can see which one refused a row.

THE TRAP WITH TEETH, pinned below in its own section. `record_markout`
stores `observed_at = observedAt or targetAt`, so a markout that
observed NOTHING lands in the table with observed_at EXACTLY equal to
its target -- a target error of zero, which on timestamps alone is the
most perfectly timed row in the ledger. It is not an observation at
all. Any rule that read only the clocks would promote every failed
markout to flawless evidence, which is the precise opposite of what
this directive asks for.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import shadow_experimental_markouts as mk
from sportsassets import shadow_markout_observability as ob
from sportsassets import shadow_markout_timing as tm
from sportsassets.api import command_experimental as ce

T0 = datetime(2026, 9, 20, 1, 0, 0, tzinfo=timezone.utc)
SHA = "b0fdb1ef9e8b51da"


def row(horizon, *, elapsed_s, tolerance_s=None, sha=SHA, decision_at=T0,
        target_s=None):
    """One markout row as the ledger would hold it."""
    horizon_s = tm.HORIZON_SECONDS[horizon]
    tol = mk.tolerance_s(horizon_s) if tolerance_s is None else tolerance_s
    target = decision_at + timedelta(
        seconds=horizon_s if target_s is None else target_s)
    return dict(horizon=horizon, decision_at=decision_at, target_at=target,
                observed_at=decision_at + timedelta(seconds=elapsed_s),
                tolerance_ms=tol * 1000.0, l2_book_sha=sha)


# ── the contract, per row ────────────────────────────────────────────


def test_a_sixty_second_row_observed_at_sixty_seconds_qualifies():
    v = tm.timing(**row("60S", elapsed_s=60))
    assert v["TIMING_STATUS"] == tm.WITHIN_TOLERANCE
    assert v["PERFORMANCE_ELIGIBLE"] is True
    assert v["REALIZED_OFFSET_S"] == 60.0
    assert v["TARGET_ERROR_S"] == 0.0
    assert v["TOLERANCE_S"] == 30.0


@pytest.mark.parametrize("elapsed", [30.0, 45.0, 89.9, 90.0])
def test_the_whole_admissible_window_qualifies(elapsed):
    """60S carries a 30s tolerance, so [30, 90] is the contract. The
    edges are IN -- the rule is `<=`, and nothing here narrows it."""
    v = tm.timing(**row("60S", elapsed_s=elapsed))
    assert v["TIMING_STATUS"] == tm.WITHIN_TOLERANCE, elapsed
    assert v["PERFORMANCE_ELIGIBLE"] is True


@pytest.mark.parametrize("elapsed", [29.9, 20.0, 90.1, 120.0, 300.0])
def test_a_row_labelled_sixty_that_landed_elsewhere_is_refused(elapsed):
    """THE DIRECTIVE'S CENTRAL CASE. The label says 60S; the clock says
    otherwise; the clock wins."""
    v = tm.timing(**row("60S", elapsed_s=elapsed))
    assert v["TIMING_STATUS"] == tm.OUTSIDE_TOLERANCE, elapsed
    assert v["PERFORMANCE_ELIGIBLE"] is False
    assert "not this horizon's markout" in v["why"]


def test_the_same_principle_governs_three_hundred_seconds():
    """§2. 300S is not exempt because it is comfortable."""
    assert tm.timing(**row("300S", elapsed_s=300))[
        "PERFORMANCE_ELIGIBLE"] is True
    assert tm.timing(**row("300S", elapsed_s=450))[
        "PERFORMANCE_ELIGIBLE"] is True          # tolerance is 150s
    outside = tm.timing(**row("300S", elapsed_s=451))
    assert outside["TIMING_STATUS"] == tm.OUTSIDE_TOLERANCE
    assert outside["PERFORMANCE_ELIGIBLE"] is False


def test_a_label_whose_target_does_not_reconstruct_is_refused():
    """"Do not assume a label proves its realized horizon." A row
    calling itself 60S whose stored target is 300s after the decision
    is a row whose label and clock disagree."""
    v = tm.timing(**row("60S", elapsed_s=300, target_s=300))
    assert v["TIMING_STATUS"] == tm.TARGET_DISAGREES_WITH_HORIZON
    assert v["PERFORMANCE_ELIGIBLE"] is False
    assert "the clock is authoritative" in v["why"]


def test_the_reconstruction_guard_is_not_a_measurement_tolerance():
    """One millisecond is a float-representation guard. It is far below
    any timing question this lane asks, and it is never widened to
    admit a row -- half a second of target drift is still refused."""
    assert tm.TARGET_RECONSTRUCTION_EPSILON_MS == 1.0
    assert tm.timing(**row("60S", elapsed_s=60, target_s=60.5))[
        "TIMING_STATUS"] == tm.TARGET_DISAGREES_WITH_HORIZON


# ── the trap: an unobserved row is not a perfect row ─────────────────


def test_a_row_with_no_book_is_not_a_zero_error_observation():
    """THE LOAD-BEARING TEST OF THIS FILE.

    `record_markout` writes observed_at = observedAt or targetAt, so a
    markout that found nothing is stored with observed_at EXACTLY at
    its target. Judged on timestamps alone that is a target error of
    0.0 -- the best-timed row in the table. It is not an observation.
    """
    v = tm.timing(**row("60S", elapsed_s=60, sha=None))
    assert v["TIMING_STATUS"] == tm.NO_OBSERVATION
    assert v["PERFORMANCE_ELIGIBLE"] is False
    # And the fields do not report a measurement that never happened.
    assert v["OBSERVATION_TIMESTAMP"] is None
    assert v["TARGET_ERROR_S"] is None
    assert v["REALIZED_OFFSET_S"] is None
    assert "never happened" in v["why"]


def test_the_observation_check_runs_before_the_timing_check():
    """Order matters: a missing book must be named as a missing book,
    not as a tolerance failure, or the panel would report the wrong
    cause for every unobserved horizon."""
    v = tm.timing(**row("60S", elapsed_s=600, sha=None))
    assert v["TIMING_STATUS"] == tm.NO_OBSERVATION     # not OUTSIDE


def test_an_unverifiable_row_is_refused_rather_than_assumed_good():
    """A row predating migration 078 has no target and no tolerance.
    It cannot be checked, so it is not evidence -- the gate fails
    closed, never open."""
    for missing in ({"target_at": None}, {"tolerance_ms": None}):
        v = tm.timing(**dict(row("60S", elapsed_s=60), **missing))
        assert v["TIMING_STATUS"] == tm.TIMING_NOT_RECORDED
        assert v["PERFORMANCE_ELIGIBLE"] is False


# ── the two gates stay separate and both apply ───────────────────────


def test_a_perfectly_timed_thirty_second_row_is_still_not_evidence():
    """The row gate passes; the horizon gate does not. 30S remains
    unresolvable on a ~61s capture grid however well one row landed."""
    v = tm.timing(**row("30S", elapsed_s=30))
    assert v["TIMING_STATUS"] == tm.WITHIN_TOLERANCE
    assert v["PERFORMANCE_ELIGIBLE"] is False
    assert v["HORIZON_OBSERVABILITY"] == ob.UNOBSERVABLE
    assert "unresolvable at the current capture frequency" in v["why"]


def test_the_panel_can_tell_which_gate_refused_a_row():
    """Both verdicts travel on the row, so "excluded" is never a bare
    fact a reader has to take on trust."""
    v = tm.timing(**row("60S", elapsed_s=200))
    assert v["TIMING_STATUS"] == tm.OUTSIDE_TOLERANCE
    assert v["HORIZON_OBSERVABILITY"] == ob.OBSERVABLE   # horizon is fine


def test_every_field_the_directive_named_is_present():
    for field in ("DECISION_TIMESTAMP", "TARGET_TIMESTAMP",
                  "OBSERVATION_TIMESTAMP", "REALIZED_OFFSET_S",
                  "TARGET_ERROR_S", "TOLERANCE_S", "TIMING_STATUS",
                  "PERFORMANCE_ELIGIBLE"):
        assert field in tm.timing(**row("60S", elapsed_s=60)), field


# ── the four prohibitions ────────────────────────────────────────────


def test_no_interpolation_no_substitution_no_zeroing_no_deletion():
    """"No interpolation. No nearest observation outside tolerance. No
    zero P&L. No deletion." Scanned in the code, not promised in a
    comment: the module writes nothing and invents no value."""
    import inspect
    import io
    import tokenize

    # THE CODE ONLY. The module's own prose promises it never
    # interpolates, so scanning the raw source would match the
    # sentence rather than the behaviour.
    kept = []
    for tok in tokenize.generate_tokens(
            io.StringIO(inspect.getsource(tm)).readline):
        if tok.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
            kept.append(tok.string)
    code = " ".join(kept).lower()

    for forbidden in ("insert", "update", "delete", "truncate",
                      "interpolate", "fillna", "coalesce"):
        assert forbidden not in code, forbidden


def test_a_refused_row_keeps_its_recorded_markout_value():
    """An ineligible row is excluded from performance, NOT zeroed and
    NOT emptied. Its number stays readable so the exclusion can be
    checked rather than believed."""
    r = {"markout_id": "xmk_1", "experimental_decision_id": "xdec_1",
         "experiment_id": "X1C_NULL_CONTROL", "market_id": "m1",
         "horizon": "60S", "status": "OBSERVED",
         "decision_timestamp": T0,
         "target_at": T0 + timedelta(seconds=60),
         "observed_at": T0 + timedelta(seconds=200),
         "tolerance_ms": 30000.0, "l2_book_sha": SHA,
         "l2_evidence_id": "l2_1",
         "executable_markout_usd": -347.55, "mid_markout_usd": -120.0,
         "realized_offset_s": 200.0, "target_error_s": 140.0,
         "reconstructed_horizon_s": 60.0}
    v = tm.row_verdict(r)
    assert v["PERFORMANCE_ELIGIBLE"] is False
    assert v["EXECUTABLE_MARKOUT_USD"] == -347.55     # preserved, not 0
    assert v["EXECUTABLE_MARKOUT_USD"] is not None


# ── the SQL gate is the same contract, and money uses it ─────────────


def test_the_money_statements_use_the_shared_gate():
    """One definition, two consumers. A hand-written second copy in a
    statement is how a gate drifts out of agreement with itself."""
    assert tm.TIMING_ELIGIBLE_SQL in ce._MARKED
    assert tm.TIMING_ELIGIBLE_SQL in ce._MARKED_WITH_HORIZONS


def test_the_money_statements_no_longer_trust_the_status_column():
    """THE REGRESSION GUARD. `status` is a conclusion written earlier;
    the directive makes the recorded timestamps authoritative, so the
    gate re-derives the verdict instead of reading the verdict."""
    for name in ("_MARKED", "_MARKED_WITH_HORIZONS"):
        sql = getattr(ce, name)
        assert "status = 'OBSERVED'" not in sql, (
            "%s admits rows on the strength of a stored status; the "
            "timestamps must be re-checked on every read" % name)


def test_the_sql_gate_checks_both_halves_of_the_contract():
    sql = tm.TIMING_ELIGIBLE_SQL
    assert "m.l2_book_sha IS NOT NULL" in sql          # something observed
    assert "d.decision_timestamp" in sql               # target reconstructs
    assert "m.observed_at - m.target_at" in sql        # inside tolerance
    assert "m.tolerance_ms" in sql
    # Every horizon the lane can write has an interval in the CASE, so
    # a new horizon cannot silently reconstruct to NULL and pass.
    for name, seconds in mk.HORIZONS:
        assert "WHEN '%s' THEN interval '%d seconds'" % (name, seconds) in sql


def test_the_full_gate_also_carries_the_horizon_restriction():
    gate = tm.performance_eligible_sql()
    horizons = gate.split("m.horizon IN (")[1].split(")")[0]
    assert horizons == "'300S', '60S'"
    assert "'30S'" not in horizons, (
        "the 30S horizon must not be admissible to a performance "
        "figure -- note it DOES still appear in the interval CASE "
        "below, and must, so that a 30S row's target reconstructs and "
        "the row is refused on its horizon rather than on a NULL")
    assert tm.TIMING_ELIGIBLE_SQL in gate


# ── the writer cross-check ───────────────────────────────────────────


def test_a_status_that_disagrees_with_the_clock_is_reported():
    """Not corrected at read time -- reported. A row stored OBSERVED
    whose timestamps fall outside tolerance means the WRITE path
    admitted something the contract forbids, and that is a finding
    about the writer rather than a number to quietly fix."""
    v = tm.timing(**row("60S", elapsed_s=200))
    v.update({"WRITTEN_STATUS": "OBSERVED", "MARKOUT_ID": "xmk_1",
              "EXECUTABLE_MARKOUT_USD": -347.55})
    out = tm.writer_disagreements([v])
    assert len(out) == 1
    assert out[0]["writtenStatus"] == "OBSERVED"
    assert out[0]["derivedTimingStatus"] == tm.OUTSIDE_TOLERANCE
    assert "outside the frozen tolerance" in out[0]["why"]


def test_agreement_produces_no_finding():
    v = tm.timing(**row("60S", elapsed_s=60))
    v.update({"WRITTEN_STATUS": "OBSERVED"})
    assert tm.writer_disagreements([v]) == []

    miss = tm.timing(**row("60S", elapsed_s=200))
    miss.update({"WRITTEN_STATUS": "NOT_IDENTIFIED"})
    assert tm.writer_disagreements([miss]) == []


def test_the_census_counts_rows_by_horizon_and_verdict():
    rows = []
    for elapsed, sha in ((60, SHA), (200, SHA), (60, None)):
        v = tm.timing(**row("60S", elapsed_s=elapsed, sha=sha))
        rows.append(v)
    c = tm.census(rows)
    assert c["60S|%s" % tm.WITHIN_TOLERANCE]["n"] == 1
    assert c["60S|%s" % tm.WITHIN_TOLERANCE]["performanceEligible"] == 1
    assert c["60S|%s" % tm.OUTSIDE_TOLERANCE]["performanceEligible"] == 0
    assert c["60S|%s" % tm.NO_OBSERVATION]["n"] == 1


# ── the panel reports it ─────────────────────────────────────────────


class TimingPool:
    ROWS = [
        {"markout_id": "xmk_ok", "experimental_decision_id": "xdec_1",
         "experiment_id": "X1C_NULL_CONTROL", "market_id": "m1",
         "horizon": "300S", "status": "OBSERVED", "decision_timestamp": T0,
         "target_at": T0 + timedelta(seconds=300),
         "observed_at": T0 + timedelta(seconds=296),
         "tolerance_ms": 150000.0, "l2_book_sha": SHA,
         "l2_evidence_id": "l2_1", "executable_markout_usd": -347.55,
         "mid_markout_usd": -120.0, "realized_offset_s": 296.0,
         "target_error_s": -4.0, "reconstructed_horizon_s": 300.0},
        {"markout_id": "xmk_late", "experimental_decision_id": "xdec_2",
         "experiment_id": "X1C_NULL_CONTROL", "market_id": "m1",
         "horizon": "60S", "status": "NOT_IDENTIFIED",
         "decision_timestamp": T0,
         "target_at": T0 + timedelta(seconds=60),
         "observed_at": T0 + timedelta(seconds=200),
         "tolerance_ms": 30000.0, "l2_book_sha": SHA,
         "l2_evidence_id": "l2_2", "executable_markout_usd": None,
         "mid_markout_usd": None, "realized_offset_s": 200.0,
         "target_error_s": 140.0, "reconstructed_horizon_s": 60.0},
    ]

    async def fetch(self, sql, *args):
        if "reconstructed_horizon_s" in sql:
            return list(self.ROWS)
        return []

    async def fetchrow(self, sql, *args):
        return None


@pytest.mark.asyncio
async def test_command_shows_each_row_with_its_timing_and_its_verdict():
    out = await ce.summary(TimingPool())
    t = out["markoutTiming"]
    assert t["noInterpolation"] is True
    assert t["noZeroPnlSubstitution"] is True
    assert t["noDeletion"] is True
    assert "The label is never sufficient" in t["rule"]

    by_id = {r["MARKOUT_ID"]: r for r in t["rows"]}
    ok = by_id["xmk_ok"]
    assert ok["TIMING_STATUS"] == tm.WITHIN_TOLERANCE
    assert ok["PERFORMANCE_ELIGIBLE"] is True
    assert ok["REALIZED_OFFSET_S"] == 296.0
    assert ok["TARGET_ERROR_S"] == -4.0
    assert ok["TOLERANCE_S"] == 150.0

    late = by_id["xmk_late"]
    assert late["TIMING_STATUS"] == tm.OUTSIDE_TOLERANCE
    assert late["PERFORMANCE_ELIGIBLE"] is False
    # Preserved, not deleted: the row is still on the panel.
    assert late["REALIZED_OFFSET_S"] == 200.0


@pytest.mark.asyncio
async def test_the_timestamps_are_rendered_not_dropped():
    out = await ce.summary(TimingPool())
    r = out["markoutTiming"]["rows"][0]
    for field in ("DECISION_TIMESTAMP", "TARGET_TIMESTAMP",
                  "OBSERVATION_TIMESTAMP"):
        assert isinstance(r[field], str) and r[field].startswith("2026-"), \
            field


@pytest.mark.asyncio
async def test_an_unreadable_timing_read_is_named_not_rendered_empty():
    class Broken(TimingPool):
        async def fetch(self, sql, *args):
            if "reconstructed_horizon_s" in sql:
                raise RuntimeError("column m.target_at does not exist")
            return []

    out = await ce.summary(Broken())
    assert "target_at does not exist" in out["markoutTiming"]["unavailable"]
