"""THE FALSE-GREEN COUNTEREXAMPLES, PINNED BEFORE THE CODE CHANGED.

An independent review reproduced four verdicts at pushed SHA 57da30e that
reported `ok=True` on payloads establishing nothing, and a fifth turned up in
the first live run. Every one has the same shape: the verdict's NAME claimed
accounting the code never performed.

  1 · an EMPTY decision object            -> NOTHING_TO_EVALUATE, ok=True
  2 · 100 considered, 0 evaluated, 1 refusal
                                          -> ..._ACCOUNTED_FOR, ok=True
  3 · 100 considered, 0 evaluated, one BLANK ledger row
                                          -> ..._ACCOUNTED_FOR, ok=True
  4 · evaluated = -1                      -> CANDIDATES_WERE_EVALUATED, ok=True
  5 · 82 considered, 33 refused (live)    -> ..._ACCOUNTED_FOR, ok=True

The rule they break: the presence of A refusal or A ledger row does not
establish that every candidate is accounted for. One refusal out of a hundred
candidates is one accounted candidate and ninety-nine unknown ones.

── AND THE POPULATIONS ARE NOT INTERCHANGEABLE ─────────────────────
`markets_considered` counts venue markets the cycle examined. The refusal
counters fire per CANDIDATE, most of them before anything is scored -- the
route's own `why_two_refusal_sources` says so. So a difference between the two
totals is NOT automatically a defect and NOT automatically fine: it is
UNESTABLISHED, and that is the verdict it must get. Summing overlapping
refusal reasons, or comparing a catalogue count with a provider-candidate
count, would manufacture a reconciliation rather than perform one.
"""

from __future__ import annotations

import pytest

from sportsassets import cycle_readback as CR


def _statuses(**cycle):
    """A payload shaped like `/api/command/rn1x/statuses` ACTUALLY is.

    That route loads the heartbeat row WHOLE into
    `statuses.external_valuation.last_cycle`, so the fixture is the raw row --
    not a desk projection. Using the route that already works is why this
    diagnostic needs no new deployment to function.
    """
    return {"scope": "RN1X",
            "statuses": {"external_valuation": {"last_cycle": dict(cycle)}}}


# ═════════════════════════════════════════════════════════════════════
# 1 · AN EMPTY DECISION OBJECT ESTABLISHES NOTHING
# ═════════════════════════════════════════════════════════════════════

def test_an_empty_cycle_object_is_not_nothing_to_evaluate():
    """`{}` is an absent measurement, not a measured absence.

    It reported NOTHING_TO_EVALUATE with ok=True -- the verdict reserved for
    "the provider supplied no fixtures", which is a positive claim about the
    world. An empty object supports no claim at all.
    """
    v = CR.verdict(_statuses())
    assert v["ok"] is False, v
    assert v["verdict"] != CR.V_NOTHING_TO_EVALUATE
    assert v["verdict"] in CR.FAILING


def test_a_cycle_with_no_instant_is_not_a_measured_empty_funnel():
    """Zero counters with no `at` is an unwritten row, not a quiet cycle."""
    v = CR.verdict(_statuses(markets_considered=0, evaluated=0, refusals={}))
    assert v["ok"] is False, v
    assert v["verdict"] in CR.FAILING


# ═════════════════════════════════════════════════════════════════════
# 2 · ONE REFUSAL DOES NOT ACCOUNT FOR A HUNDRED CANDIDATES
# ═════════════════════════════════════════════════════════════════════

def test_one_refusal_out_of_a_hundred_is_not_complete_accounting():
    """CODEX'S CASE 2. The name said ACCOUNTED_FOR; 99 were unexplained."""
    v = CR.verdict(_statuses(at=1_790_000_000.0, state="LIVE",
                             markets_considered=100, evaluated=0,
                             refusals={"NO_VENUE_CONTRACT_FOR_EVENT": 1}))
    assert v["verdict"] != CR.V_ALL_REFUSED_ACCOUNTED, (
        "one refusal was read as accounting for a hundred candidates")
    assert v["reconciled"] is False
    # NO `unexplained` NUMBER IS PUBLISHED. It would be a catalogue count minus
    # a candidate count, which is the incomparable subtraction this file's
    # last test exists to forbid.
    assert v.get("unexplained") is None
    assert v["distinct_candidates"] == 0


def test_a_blank_ledger_row_accounts_for_nothing():
    """CODEX'S CASE 3. A row with no candidate and no reason is not evidence.

    The ledger's whole purpose is to name WHICH candidate hit WHICH blocker.
    A row carrying neither identifies nothing, and counting it as coverage is
    counting the container instead of the contents.
    """
    v = CR.verdict(_statuses(at=1_790_000_000.0, state="LIVE",
                             markets_considered=100, evaluated=0,
                             refusals={},
                             mapped_candidate_ledger=[{}]))
    assert v["verdict"] != CR.V_ALL_REFUSED_ACCOUNTED
    assert v["ok"] is False
    assert v["ledger_rows_usable"] == 0
    assert v["ledger_rows_blank"] == 1


def test_a_ledger_row_must_identify_a_candidate_and_a_reason():
    """A usable row names both. One without a reason is not an outcome."""
    v = CR.verdict(_statuses(
        at=1_790_000_000.0, state="LIVE", markets_considered=2, evaluated=0,
        refusals={},
        mapped_candidate_ledger=[{"slug": "aec-mlb-chc-sd-2026-09-30"},
                                 {"slug": "x", "refusal": "NO_DEPTH"}]))
    assert v["ledger_rows_usable"] == 1
    assert v["ledger_rows_blank"] == 1


# ═════════════════════════════════════════════════════════════════════
# 3 · A NEGATIVE COUNT IS NOT A COUNT
# ═════════════════════════════════════════════════════════════════════

def test_a_negative_evaluated_count_is_rejected_not_believed():
    """CODEX'S CASE 4. `evaluated=-1` reported candidates were evaluated.

    Any non-negative integer is a count; -1 is a corrupt field, and treating
    it as truthy is the same class of bug as NaN passing `float()`.
    """
    v = CR.verdict(_statuses(at=1_790_000_000.0, state="LIVE",
                             markets_considered=10, evaluated=-1))
    assert v["verdict"] != CR.V_EVALUATED, "a negative count was believed"
    assert v["ok"] is False
    # The reason travels with the name, so match the name as a prefix.
    assert any(f.startswith("evaluated") for f in (v.get("invalid_fields")
                                                   or [])), v


@pytest.mark.parametrize("bad", [-1, -100, "seven", 1.5, True, [], {}])
def test_only_a_nonnegative_integer_is_a_counter(bad):
    """Every non-counter is named as invalid rather than coerced.

    `True` is included deliberately: in Python `isinstance(True, int)` holds
    and `int(True)` is 1, so a boolean would silently become a count of one.
    """
    v = CR.verdict(_statuses(at=1_790_000_000.0, state="LIVE",
                             markets_considered=5, evaluated=bad))
    assert v["ok"] is False, (bad, v["verdict"])
    # The reason travels with the name, so match the name as a prefix.
    assert any(f.startswith("evaluated")
               for f in (v.get("invalid_fields") or [])), (bad, v)


def test_a_negative_refusal_count_is_rejected_too():
    v = CR.verdict(_statuses(at=1_790_000_000.0, state="LIVE",
                             markets_considered=5, evaluated=0,
                             refusals={"X": -3}))
    assert v["ok"] is False
    assert any("refusals.X" in f for f in (v.get("invalid_fields") or [])), v


# ═════════════════════════════════════════════════════════════════════
# 4 · THE LIVE CASE: 82 CONSIDERED, 33 REFUSED
# ═════════════════════════════════════════════════════════════════════

def test_the_live_82_vs_33_cycle_is_unestablished_not_accounted_for():
    """THE FIFTH CASE, FROM THE FIRST SUCCESSFUL LIVE RUN.

    Deployed c3d0cfc reported `markets_considered: 82` with 33 refusals and
    `evaluated` absent. My verdict said EVERY_CANDIDATE_REFUSED_AND_ACCOUNTED
    _FOR. 82 - 33 = 49 candidates with no stated outcome.

    AND IT IS NOT A DEFECT EITHER. `markets_considered` counts venue markets
    examined while the refusal counters fire per candidate, so the two may be
    different populations -- which is exactly why the honest verdict is
    UNESTABLISHED rather than either a clean account or a broken one.
    """
    v = CR.verdict(_statuses(
        at=1_790_613_300.4, state="LIVE",
        cycle_label="ZERO_EVALUATED__INPUT_PATH_BLOCKED",
        writer={"build": "c3d0cfc303fa92e182786d40b4b27a4d05322b32"},
        markets_considered=82,
        refusals={"NO_PINNACLE_ON_EVENT": 9, "VENUE_MAPPING_AMBIGUOUS": 1,
                  "NO_VENUE_CONTRACT_FOR_EVENT": 21,
                  "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED": 1,
                  "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE": 1},
        mapped_candidate_ledger=[{"slug": "aec-mlb-chc-sd-2026-09-30",
                                  "refusal": "VENUE_BOOK_READ_RETURNED_ERROR"}]))
    assert v["verdict"] == CR.V_REFUSALS_NAMED_NOT_RECONCILED, v["verdict"]
    assert v["reconciled"] is False
    assert v["refusal_total"] == 33
    assert v["markets_considered"] == 82
    # `evaluated` ABSENT is the reason it cannot be closed, and it is named.
    assert v["evaluated"] is None
    assert v["why_not_reconciled"]


def test_a_cycle_that_does_reconcile_is_accounted_for():
    """The good case: every counted refusal names a DISTINCT candidate.

    Five refusals, five ledger rows, five distinct identities, none evaluated.
    Coverage is then established over the candidates the cycle named -- which
    is a different and weaker claim than "every venue market was accounted
    for", and deliberately so.
    """
    v = CR.verdict(_statuses(
        at=1_790_000_000.0, state="LIVE", markets_considered=5, evaluated=0,
        refusals={"A": 3, "B": 2},
        mapped_candidate_ledger=[{"slug": "c%d" % i, "refusal": "A"}
                                 for i in range(3)]
                                + [{"slug": "c%d" % i, "refusal": "B"}
                                   for i in range(3, 5)]))
    assert v["verdict"] == CR.V_ALL_REFUSED_ACCOUNTED
    assert v["reconciled"] is True
    assert v["distinct_candidates"] == 5
    assert v["ok"] is True


def test_a_reconciling_cycle_with_evaluations_passes():
    v = CR.verdict(_statuses(
        at=1_790_000_000.0, state="LIVE", markets_considered=5, evaluated=2,
        refusals={"A": 3},
        mapped_candidate_ledger=[{"slug": "c%d" % i, "refusal": "A"}
                                 for i in range(3)]))
    assert v["verdict"] == CR.V_EVALUATED
    assert v["reconciled"] is True
    assert v["ok"] is True


def test_more_refusals_than_ledger_rows_is_not_silently_accepted():
    """Counted refusals that name no candidate leave coverage unestablished.

    Five refusals with three named outcomes means two belong to candidates
    this cycle never identified -- and `markets_considered` cannot supply
    them, because it counts a different population.
    """
    v = CR.verdict(_statuses(
        at=1_790_000_000.0, state="LIVE", markets_considered=3, evaluated=0,
        refusals={"A": 3, "B": 2},
        mapped_candidate_ledger=[{"slug": "c%d" % i, "refusal": "A"}
                                 for i in range(3)]))
    assert v["reconciled"] is False
    assert v["verdict"] == CR.V_REFUSALS_NAMED_NOT_RECONCILED
    assert "belong to candidates this cycle did not identify" \
        in v["why_not_reconciled"]


# ═════════════════════════════════════════════════════════════════════
# 5 · THE ROUTE IT READS
# ═════════════════════════════════════════════════════════════════════

def test_the_reader_uses_the_statuses_route_that_already_works():
    """No new deployment required for basic EV diagnosis.

    `/api/command/rn1x/statuses` already returns the persisted heartbeat WHOLE
    at `statuses.external_valuation.last_cycle`, and an independent run
    confirmed that route answering against the deployed build. Making the
    diagnostic depend on a desk projection that only exists on an undeployed
    branch would have made it useless exactly when it is needed.
    """
    assert CR.STATUSES_PATH == "/api/command/rn1x/statuses"
    assert CR.P_CYCLE == ("statuses", "external_valuation", "last_cycle")


def test_an_older_build_missing_a_field_is_not_missing_heartbeat_data():
    """Two different absences, kept apart.

    An older build writes the row WITHOUT `venue_sdk`: the heartbeat is
    present and that field is not. That must not read as "no heartbeat", and
    it must not read as "the SDK is unpinned" either.
    """
    v = CR.verdict(_statuses(
        at=1_790_000_000.0, state="LIVE", markets_considered=2, evaluated=0,
        refusals={"A": 2},
        mapped_candidate_ledger=[{"slug": "c1", "refusal": "A"},
                                 {"slug": "c2", "refusal": "A"}]))
    assert v["verdict"] != CR.V_NO_HEARTBEAT
    assert v["cycle_row_present"] is True
    assert v["venue_sdk_is_absent"] is True
    assert v["ok"] is True


def test_an_unreadable_cycle_row_is_not_an_empty_one():
    """The route reports `{"unreadable": "..."}` when its own read failed."""
    v = CR.verdict(_statuses(unreadable="UndefinedTableError"))
    assert v["ok"] is False
    assert v["verdict"] in CR.FAILING


# ═════════════════════════════════════════════════════════════════════
# 6 · ARITHMETIC EQUALITY IS NOT COVERAGE
# ═════════════════════════════════════════════════════════════════════
#
# Reproduced on be9fde8: markets_considered=2, evaluated=0,
# refusals={A:1, B:1}, and a ledger holding the SAME candidate twice -- once
# under each reason. The totals met (2 = 0 + 2) and the verdict read
# EVERY_CANDIDATE_REFUSED_AND_ACCOUNTED_FOR, reconciled=True.
#
# One candidate was accounted for. The second was a duplicate of the first.
# Two numbers agreeing is not a partition of a population, and the agreement
# was produced by counting one candidate twice.

def test_the_same_candidate_twice_does_not_account_for_two():
    """The sum matched and the coverage did not exist."""
    v = CR.verdict(_statuses(
        at=1_790_000_000.0, state="LIVE", markets_considered=2, evaluated=0,
        refusals={"A": 1, "B": 1},
        mapped_candidate_ledger=[
            {"slug": "aec-mlb-chc-sd-2026-09-30", "refusal": "A"},
            {"slug": "aec-mlb-chc-sd-2026-09-30", "refusal": "B"}]))
    assert v["verdict"] != CR.V_ALL_REFUSED_ACCOUNTED, (
        "the same candidate counted twice was read as complete coverage")
    assert v["reconciled"] is False
    assert v["distinct_candidates"] == 1
    assert v["ledger_rows_usable"] == 2
    assert v["duplicate_candidate_identities"] == 1


def test_distinct_candidates_with_one_reason_each_can_reconcile():
    """The good case needs DISTINCT identities, and this one has them."""
    v = CR.verdict(_statuses(
        at=1_790_000_000.0, state="LIVE", markets_considered=2, evaluated=0,
        refusals={"A": 1, "B": 1},
        mapped_candidate_ledger=[{"slug": "one", "refusal": "A"},
                                 {"slug": "two", "refusal": "B"}]))
    assert v["distinct_candidates"] == 2
    assert v["duplicate_candidate_identities"] == 0
    assert v["reconciled"] is True
    assert v["verdict"] == CR.V_ALL_REFUSED_ACCOUNTED


def test_a_venue_catalogue_count_is_never_the_candidate_population():
    """MY OWN PUBLISHED ERROR, pinned so it cannot be repeated.

    I reported "49 unexplained candidates" from `82 considered - 0 evaluated -
    33 refused`. `markets_considered` counts VENUE MARKETS the cycle examined;
    the refusal counters fire per PROVIDER CANDIDATE. Subtracting one from the
    other produces a number with no referent, and I published it in the same
    breath as saying the populations may not be comparable.

    The reconciliation is now performed over the CANDIDATE population only,
    and `markets_considered` is carried as catalogue context that is explicitly
    not that population.
    """
    v = CR.verdict(_statuses(
        at=1_790_613_300.4, state="LIVE", markets_considered=82, evaluated=0,
        refusals={"NO_PINNACLE_ON_EVENT": 9, "VENUE_MAPPING_AMBIGUOUS": 1,
                  "NO_VENUE_CONTRACT_FOR_EVENT": 21,
                  "VENUE_BOOK_CURRENCY_NOT_ESTABLISHED": 1,
                  "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE": 1},
        mapped_candidate_ledger=[{"slug": "aec-mlb-chc-sd-2026-09-30",
                                  "refusal": "VENUE_BOOK_READ_RETURNED_ERROR"}]))
    # NO SUBTRACTION ACROSS THE TWO POPULATIONS.
    assert v.get("unexplained") is None, (
        "a difference between a catalogue count and a candidate count was "
        "published as a candidate shortfall")
    assert v["markets_considered"] == 82
    assert v["markets_considered_is_not_the_candidate_population"] is True
    assert v["verdict"] == CR.V_REFUSALS_NAMED_NOT_RECONCILED
    assert "not the candidate population" in v["why_not_reconciled"]


def test_coverage_needs_a_ledger_not_just_counters():
    """Refusal totals alone cannot establish which candidates they covered.

    A refusal histogram with no ledger says how many refusals happened, not
    how many DISTINCT candidates they account for -- two reasons can fire on
    one candidate. Without identities, coverage is unestablished.
    """
    v = CR.verdict(_statuses(at=1_790_000_000.0, state="LIVE",
                             markets_considered=5, evaluated=0,
                             refusals={"A": 3, "B": 2},
                             mapped_candidate_ledger=[]))
    assert v["reconciled"] is False
    assert v["verdict"] == CR.V_REFUSALS_NAMED_NOT_RECONCILED
    assert v["distinct_candidates"] == 0
