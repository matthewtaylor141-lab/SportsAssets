"""WHERE EACH REFUSED CANDIDATE ACTUALLY STOPPED, AND WHAT THAT SUPPORTS.

THE CLAIM THIS CORRECTS, AND IT WAS MINE. I reported that every production
candidate lacked profitable depth. That does not follow from the evidence I
had, which was that the walk and VWAP fields were empty. A candidate refused
at PROBABILITY, FRESHNESS, IDENTITY or SETTLEMENT SCOPE never reached
execution estimation at all -- so it has no walk BECAUSE IT WAS NEVER
PRICED, not because the book was thin. The absence of a measurement is not a
measurement of absence, and collapsing the two turned a stage-2 clock
failure into a statement about market liquidity.

`stage_report` is pure, so the attribution can be held to specific inputs
here rather than inferred from a production summary. The distinction that
matters is between the two negative-edge classes:

  * negative_edge_WITH_a_walk    -- a book WAS walked and the sized
                                    quantity's volume-weighted cost plus
                                    per-level fees exceeded the
                                    probability. This is the only class
                                    about which "no profitable depth" is
                                    sayable at all.
  * negative_edge_WITHOUT_a_walk -- no estimate existed, so the gate
                                    compared the probability against the
                                    observed BEST ASK. A real refusal, and
                                    it establishes nothing about depth
                                    further down, because none was read.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_external_shadow as ext

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


def _row(refusals=(), *, admissible=False, estimate_ok=False,
         levels_taken=0, edge=None, vwap=None):
    return {"refusals": list(refusals), "admissible": admissible,
            "estimate_ok": estimate_ok, "levels_taken": levels_taken,
            "edge": edge, "vwap": vwap, "executable_price": None,
            "probability": None}


# ── the earliest stage wins, because that is where it stopped ────────

def test_the_earliest_failing_stage_is_the_one_reported():
    """A row carrying several refusals stopped at the FIRST of them. Taking
    the last, or the most interesting, attributes the refusal to a gate the
    candidate never reached."""
    assert ext.first_stage(["NO_ACTION_HAS_POSITIVE_NET_EDGE",
                            "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED"]) \
        == "4_SETTLEMENT_SCOPE"
    # AND ORDER OF PRESENTATION MUST NOT CHANGE THE ANSWER.
    assert ext.first_stage(["VOID_ABANDONMENT_RULE_NOT_ESTABLISHED",
                            "NO_ACTION_HAS_POSITIVE_NET_EDGE"]) \
        == "4_SETTLEMENT_SCOPE"


def test_an_unrecognised_refusal_is_named_not_silently_dropped():
    """A refusal this table does not know about must appear as its own
    bucket. Dropping it would make the stage counts silently under-add and
    the total look like fewer candidates than were examined."""
    assert ext.first_stage(["A_REFUSAL_ADDED_LATER"]) \
        == ext.STAGE_UNCLASSIFIED
    got = ext.stage_report([_row(["A_REFUSAL_ADDED_LATER"])])
    assert got["candidates"] == 1
    assert got["by_first_stage"] == {ext.STAGE_UNCLASSIFIED: 1}
    assert sum(got["by_first_stage"].values()) == got["candidates"]


def test_a_candidate_with_no_refusal_is_in_no_stage_bucket():
    assert ext.first_stage([]) is None
    got = ext.stage_report([_row([], admissible=True)])
    assert got["candidates"] == 1 and got["admissible"] == 1
    assert got["by_first_stage"] == {}


# ── the distinction the overstated claim collapsed ───────────────────

def test_a_negative_edge_without_a_walk_is_counted_apart():
    """THE WHOLE POINT. Both rows below were refused on economics with a
    negative edge. Only one of them had a book walked, and only that one
    says anything about depth."""
    got = ext.stage_report([
        # WALKED: three levels taken, cost above the probability.
        _row(["NO_ACTION_HAS_POSITIVE_NET_EDGE"], estimate_ok=True,
             levels_taken=3, edge=-0.02, vwap=0.64),
        # NOT WALKED: the gate saw the top of the book and nothing else.
        _row(["NO_ACTION_HAS_POSITIVE_NET_EDGE"], edge=-0.05),
    ])
    assert got["by_first_stage"] == {"8_ECONOMICS": 2}
    assert got["negative_edge_with_a_walk"] == 1
    assert got["negative_edge_without_a_walk"] == 1
    assert got["walk_took_levels"] == 1
    assert got["reached_execution_estimate"] == 1
    # AND THE REPORT SAYS WHAT EACH CLASS SUPPORTS, in the payload itself,
    # so a reader cannot recombine them without contradicting it.
    s = got["what_this_supports"]
    assert "only class" in s["negative_edge_with_a_walk"]
    assert "does NOT establish" in s["negative_edge_without_a_walk"]
    assert "not a thin book" in s["refused_before_5_EXECUTION_ESTIMATE"]


def test_a_candidate_refused_early_is_not_a_depth_finding():
    """Four candidates that stopped before execution estimation. None of
    them contributes to either negative-edge count, because none of them
    was ever compared against a price at all."""
    # THE REAL REFUSAL STRINGS, one per early stage. An invented name
    # would land in 9_UNCLASSIFIED and the test would pass while telling
    # us nothing -- which is what a first draft of this file did.
    got = ext.stage_report([
        _row(["INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED"]),
        _row(["QUOTE_STALE"]),
        _row(["NO_PREMAP_CONTRACT_FOR_THIS_FIXTURE"]),
        _row(["VOID_ABANDONMENT_RULE_NOT_ESTABLISHED"]),
    ])
    assert got["candidates"] == 4
    assert got["reached_execution_estimate"] == 0
    assert got["walk_took_levels"] == 0
    assert got["negative_edge_with_a_walk"] == 0
    assert got["negative_edge_without_a_walk"] == 0
    # EACH IN ITS OWN BUCKET, in the declared order.
    assert list(got["by_first_stage"]) == sorted(
        got["by_first_stage"], key=ext.STAGE_ORDER.index)
    assert set(got["by_first_stage"]) <= set(ext.STAGE_ORDER)
    assert len(got["by_first_stage"]) == 4


def test_an_empty_window_reports_zero_candidates_not_a_conclusion():
    got = ext.stage_report([])
    assert got["candidates"] == 0
    assert got["by_first_stage"] == {}
    assert got["negative_edge_with_a_walk"] == 0
    assert got["stage_order"] == list(ext.STAGE_ORDER)


def test_every_declared_refusal_is_a_string_production_actually_emits():
    """THE FAILURE MODE THIS CATCHES. A refusal name invented here rather
    than taken from the engine buckets every real candidate into
    9_UNCLASSIFIED, and the report then looks computed while attributing
    nothing. Checked against the source of the modules that raise them."""
    import pathlib

    src = ""
    for f in ("sportsassets/bettor_external_shadow.py",
              "sportsassets/bettor_entry_gate.py",
              "sportsassets/bettor_entry_execution.py",
              "sportsassets/bettor_entry_sizing.py",
              "sportsassets/bettor_risk_engine.py",
              "sportsassets/bettor_settlement_terms.py",
              "sportsassets/bettor_pinnacle_devig.py",
              "sportsassets/workers/ext_pinnacle_loop.py",
              "sportsassets/bettor_freshness.py"):
        p = pathlib.Path(f)
        if p.exists():
            src += p.read_text()
    assert src, "the production modules were not found from the test cwd"
    for stage, refusals in ext.STAGES:
        for r in refusals:
            assert r in src, (stage, r, "not emitted by any module")


def test_every_declared_refusal_maps_to_exactly_one_stage():
    """A refusal in two stages would make the attribution order-dependent
    on the table's own layout, which is not a property anyone could audit."""
    seen = {}
    for stage, refusals in ext.STAGES:
        for r in refusals:
            assert r not in seen, (r, seen[r], stage)
            seen[r] = stage
    assert ext.STAGE_OF == seen


# ── and the production read it feeds must actually run ───────────────

@pg
@pytest.mark.asyncio
async def test_the_stage_census_statement_runs_against_the_real_schema():
    """`census` reports `computed: False` when this read fails, which is
    honest and is also invisible until someone looks. The statement is
    exercised here so a syntax or column error is a test failure rather
    than a permanently empty section of a production report."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(ext.STAGE_CENSUS, ext.EXPERIMENT_ID, "6")
        got = ext.stage_report([dict(r) for r in rows])
        assert got["candidates"] == len(rows)
        # AND `census` ITSELF MUST NOT REPORT A FAILED ATTRIBUTION.
        out = await ext.census(conn, hours=6)
        assert out["stages"].get("computed") is not False, out["stages"]
        assert "by_first_stage" in out["stages"]
    finally:
        await conn.close()
