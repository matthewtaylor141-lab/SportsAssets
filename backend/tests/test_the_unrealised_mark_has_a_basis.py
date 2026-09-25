"""UNREALISED SHADOW P&L, ON A BASIS WE MAY USE -- AND ITS REFUSALS.

WHAT THIS CLOSES. `_pnl_status` reported `unrealised: NOT_IDENTIFIED` on
the grounds that "marking open inventory requires a price we may use. A
midpoint is where nobody transacted". The reasoning is right and is kept;
the conclusion was too strong. The manager already computes a price we may
use, every cycle, for exactly the held quantity: DIRECT_EXIT prices selling
the leg INTO THE OBSERVED BID, capped at that bid's own depth, net of the
production fee schedule. That is transactable, and it is the number the
exit decision is already made on.

A CORRECTION OF MINE, AND IT IS WHY THESE TESTS LOOK LIKE THIS. I wrote
this module expecting `rn1x_decisions.alternatives` to be a JSON ARRAY of
candidates. Asked of production on 2026-09-25, the three newest decision
rows answered

    alt_type = object    alt_len = 0    actions = NULL

-- a MAPPING, and an empty one. Empty is correct for those particular rows
(their fixtures have finished, so no bid exists and nothing was rankable),
but the shape is not what I assumed, and a reader expecting a list would
have called a live exit absent. So both shapes are accepted and an empty
ranking is named THE_DECISION_RANKED_NOTHING rather than reported as a
malformed row. The tests below use the measured shape, not the one I
expected.

FOUR COLLAPSES THIS REFUSES, each of which flatters the number:
  * an unmarked position is not worth zero -- it is excluded and named;
  * a depth-capped mark covers only the slice the book would take, and the
    remainder is priced from the HOLD model, so it is reported under its
    own label and never added in;
  * a total is only a total when EVERY open position is marked;
  * a mark older than the management cadence is stale, not current.
"""

from __future__ import annotations

from sportsassets import bettor_shadow_marks as M

NOW = 1790350000.0
FRESH = NOW - 60.0


def _exit(**over):
    """A DIRECT_EXIT candidate in the ranker's own field vocabulary."""
    row = {"action": "DIRECT_EXIT", "qty": 10.0,
           "value_usd": 1.40, "value_per_contract": 0.14,
           "slice_value_usd": 1.40, "retained_value_usd": None,
           "execution_secured": False, "fees_usd": 0.05,
           "depth_limited": False, "cash_now_usd": 5.45,
           "cash_at_settlement_usd": 0.0}
    row.update(over)
    return row


def _hold(value=2.0):
    return {"action": "HOLD", "value_usd": value}


# ── the basis itself ─────────────────────────────────────────────────

def test_the_basis_is_named_and_is_not_a_midpoint():
    assert M.MARK_BASIS == "EXECUTABLE_EXIT_NET_OF_FEES_ON_OBSERVED_DEPTH"
    assert "nobody transacted" in M.WHY_NOT_A_MIDPOINT
    # AND THE HOLD VALUE IS REFUSED AS A MARK, separately: it is the right
    # input for choosing, and nothing can be transacted at it.
    assert "probability model" in M.WHY_NOT_THE_HOLD_VALUE


def test_a_position_is_marked_at_the_executable_exit():
    got = M.mark_one(position_id="p1", alternatives=[_hold(), _exit()],
                     decided_at=FRESH, now=NOW, position_qty=10.0)
    assert got["marked"] is True, got
    assert got["unrealised_usd"] == 1.40
    assert got["basis"] == M.MARK_BASIS
    assert got["marked_qty"] == 10.0
    assert got["fees_usd"] == 0.05
    assert got["blocker"] is None


def test_the_hold_value_is_never_used_as_the_mark():
    """A ranking with a HOLD but no executable exit is UNMARKED, even
    though HOLD carries a number. Using it would mark inventory at a price
    nobody is showing."""
    got = M.mark_one(position_id="p1", alternatives=[_hold(99.0)],
                     decided_at=FRESH, now=NOW, position_qty=10.0)
    assert got["marked"] is False
    assert got["unrealised_usd"] is None
    assert got["blocker"] == M.R_NO_EXIT_CANDIDATE


# ── the shape, as production actually stores it ──────────────────────

def test_the_mapping_shape_production_uses_is_read():
    """`persist_run` writes `{"ranked": [...]}`; the column reads back as a
    JSON object. A reader expecting a list would find nothing."""
    got = M.mark_one(position_id="p1",
                     alternatives={"ranked": [_hold(), _exit()],
                                   "refused": []},
                     decided_at=FRESH, now=NOW, position_qty=10.0)
    assert got["marked"] is True, got
    assert got["unrealised_usd"] == 1.40


def test_an_action_keyed_mapping_is_also_read():
    got = M.mark_one(position_id="p1",
                     alternatives={"DIRECT_EXIT": _exit(), "HOLD": _hold()},
                     decided_at=FRESH, now=NOW, position_qty=10.0)
    assert got["marked"] is True, got


def test_the_empty_object_production_returned_is_named_not_malformed():
    """THE MEASURED CASE. alt_type=object, alt_len=0 on the three newest
    rows. Nothing was priced -- not even a refused exit -- and that is what
    a finished fixture looks like, so it gets its own name."""
    got = M.mark_one(position_id="p1", alternatives={},
                     decided_at=FRESH, now=NOW, position_qty=10.0)
    assert got["marked"] is False
    assert got["blocker"] == M.R_NO_RANKED
    assert "finished fixture" in got["why"]
    assert got["blocker"] != M.R_MALFORMED, \
        "an empty ranking is not a malformed row"


# ── the refusals, each by name ───────────────────────────────────────

def test_the_rankers_own_blocker_name_is_passed_through():
    """NO_BID and NO_EXECUTABLE_DEPTH are different facts about the book,
    and renaming them here would make the dashboard unreconcilable against
    the engine."""
    for name in ("NO_BID", "NO_EXECUTABLE_DEPTH",
                 "FEE_SCHEDULE_NOT_ESTABLISHED"):
        got = M.mark_one(
            position_id="p1",
            alternatives=[_hold(),
                          {"action": "DIRECT_EXIT", "blocker": name}],
            decided_at=FRESH, now=NOW, position_qty=10.0)
        assert got["marked"] is False
        assert got["blocker"] == name, got


def test_a_stale_mark_is_stale_rather_than_missing():
    """A price from an hour ago is not one anybody is showing. It is also
    not the same problem as no price at all: one needs a cycle to run, the
    other needs a bid."""
    got = M.mark_one(position_id="p1", alternatives=[_exit()],
                     decided_at=NOW - 3600.0, now=NOW, position_qty=10.0)
    assert got["marked"] is False
    assert got["blocker"] == M.R_STALE
    assert got["mark_age_s"] == 3600.0
    assert "not a mark" in got["why"]


def test_a_candidate_with_no_number_is_malformed_not_zero():
    got = M.mark_one(position_id="p1",
                     alternatives=[_exit(slice_value_usd=None)],
                     decided_at=FRESH, now=NOW, position_qty=10.0)
    assert got["marked"] is False
    assert got["blocker"] == M.R_MALFORMED
    assert got["unrealised_usd"] is None


def test_a_nan_is_not_a_number():
    got = M.mark_one(position_id="p1",
                     alternatives=[_exit(slice_value_usd=float("nan"))],
                     decided_at=FRESH, now=NOW, position_qty=10.0)
    assert got["marked"] is False


# ── depth-capped marks keep the remainder apart ──────────────────────

def test_a_depth_capped_mark_covers_only_the_sellable_slice():
    """The bid takes 4 of 10. The mark is the slice; the other 6 are
    UNMARKED RESIDUAL and their HOLD-derived value carries its own basis
    label so it can never be added into the executable figure."""
    got = M.mark_one(
        position_id="p1",
        alternatives=[_exit(qty=4.0, depth_limited=True,
                            slice_value_usd=0.56,
                            retained_value_usd=0.90)],
        decided_at=FRESH, now=NOW, position_qty=10.0)
    assert got["marked"] is True, got
    assert got["unrealised_usd"] == 0.56
    assert got["marked_qty"] == 4.0
    assert got["depth_limited"] is True
    assert got["unmarked_residual_qty"] == 6.0
    assert got["retained_value_usd"] == 0.90
    assert got["retained_value_basis"] == \
        "MODEL_DERIVED_HOLD_VALUE_NOT_A_TRANSACTABLE_MARK"
    # THE EXECUTABLE FIGURE DOES NOT INCLUDE THE RETAINED VALUE.
    assert got["unrealised_usd"] != 0.56 + 0.90


def test_the_position_quantity_comes_from_the_position():
    """Not from the candidate: the candidate carries the SELLABLE size.
    Without the position's own qty the residual is unknown, and unknown is
    reported rather than inferred."""
    got = M.mark_one(
        position_id="p1",
        alternatives=[_exit(qty=4.0, depth_limited=True,
                            slice_value_usd=0.56)],
        decided_at=FRESH, now=NOW, position_qty=None)
    assert got["marked"] is True
    assert got["unmarked_residual_qty"] is None


# ── the roll-up, and the total that refuses to be a total ────────────

def test_a_total_is_only_a_total_when_everything_is_marked():
    marks = [M.mark_one(position_id="a", alternatives=[_exit()],
                        decided_at=FRESH, now=NOW, position_qty=10.0),
             M.mark_one(position_id="b", alternatives={},
                        decided_at=FRESH, now=NOW, position_qty=5.0)]
    got = M.roll_up(marks, realised_usd=12.0, open_positions=2)
    assert got["marked_positions"] == 1
    assert got["unmarked_positions"] == 1
    assert got["unrealised_usd"] == 1.40
    assert got["unrealised_covers"] == "1 of 2 open positions"
    assert got["total_pnl_usd"] is None
    assert got["total_pnl_status"] == "PARTIAL"
    assert M.R_NO_RANKED in got["unmarked_by_blocker"]
    assert "is not the book's P&L" in got["why_total_is_partial"]


def test_a_complete_total_states_how_it_was_built_and_reconciles():
    marks = [M.mark_one(position_id="a", alternatives=[_exit()],
                        decided_at=FRESH, now=NOW, position_qty=10.0)]
    got = M.roll_up(marks, realised_usd=12.0, open_positions=1)
    assert got["total_pnl_status"] == "COMPLETE"
    assert got["total_pnl_usd"] == 13.40
    assert got["reconciles"] is True
    assert "realised" in got["how_total_is_built"]
    assert M.MARK_BASIS in got["how_total_is_built"]


def test_an_open_position_with_no_decision_row_still_counts_against_us():
    """THE DENOMINATOR IS THE OPEN BOOK. A position with no decision
    produces no mark row at all, and a coverage figure computed over rows
    would report 100% while missing it entirely."""
    marks = [M.mark_one(position_id="a", alternatives=[_exit()],
                        decided_at=FRESH, now=NOW, position_qty=10.0)]
    got = M.roll_up(marks, realised_usd=12.0, open_positions=3)
    assert got["open_positions"] == 3
    assert got["marked_positions"] == 1
    assert got["unmarked_positions"] == 2
    assert got["unmarked_by_blocker"][M.R_NO_DECISION] == 2
    assert got["total_pnl_status"] == "PARTIAL"


def test_no_open_position_marked_is_not_a_zero_unrealised():
    got = M.roll_up([M.mark_one(position_id="a", alternatives={},
                                decided_at=FRESH, now=NOW)],
                    realised_usd=12.0, open_positions=1)
    assert got["unrealised_usd"] is None
    assert got["total_pnl_usd"] is None
    assert "no open position could be marked" in got["why_total_is_partial"]


def test_an_unreadable_realised_figure_blocks_the_total():
    marks = [M.mark_one(position_id="a", alternatives=[_exit()],
                        decided_at=FRESH, now=NOW, position_qty=10.0)]
    got = M.roll_up(marks, realised_usd=None, open_positions=1)
    assert got["total_pnl_status"] == "PARTIAL"
    assert "no realised figure" in got["why_total_is_partial"]


def test_everything_is_labelled_modelled():
    got = M.roll_up([], realised_usd=0.0, open_positions=0)
    assert got["modelled"] is True
    assert got["no_capital_moved"] is True


def test_describe_states_what_it_refuses_to_claim():
    d = M.describe()
    assert d["mark_basis"] == M.MARK_BASIS
    assert d["everything_here_is_modelled"] is True
    joined = " ".join(d["refuses_to_collapse"])
    assert "not worth zero" in joined
    assert "only a total when every open position is marked" in joined
    for name in (M.R_NO_RANKED, M.R_STALE, "NO_BID"):
        assert name in d["blockers"]
