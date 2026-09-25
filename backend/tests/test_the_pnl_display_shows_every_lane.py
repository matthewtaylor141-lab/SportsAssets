"""THE COMMAND CENTRE'S P&L, AND THE LANE IT WAS OMITTING.

TWO DEFECTS THIS CLOSES, BOTH FOUND BY READING PRODUCTION RATHER THAN THE
CODE.

1 · THE AUTONOMOUS ENTRY LANE WAS NOT IN THE P&L AT ALL. `_pnl_status`
    iterated exactly two experiment ids -- the historical and prospective
    management lanes -- so a position created by
    `EXT_PINNACLE_DEVIG_V1_SHADOW` would have carried a cost basis, owed
    fees and settled without ever appearing in the displayed book. Nothing
    had exposed it because that lane has produced NO position: every one of
    its 464 candidates in 24 hours is refused upstream. A display that
    silently omits a lane is one that understates the book the moment the
    lane starts working, which is precisely when someone would be relying
    on it.

2 · UNREALISED WAS THE STRING "NOT_IDENTIFIED". The stated reason -- "a
    midpoint is where nobody transacted" -- is right, and is kept. The
    conclusion was too strong: the manager already prices DIRECT_EXIT as
    selling the held leg INTO THE OBSERVED BID, capped at that bid's own
    depth and net of the production fee schedule. That is transactable and
    it is the number the exit decision is already made on, so marking at
    anything else would make the display disagree with the manager about
    what a position is worth. Open inventory is still reported at cost too,
    because a mark and a basis are different facts.

WHAT IS PINNED HERE. That all three lanes are read; that the entry lane's
own experiment id is the one read for it, not a copy; that the marking
refuses by name instead of reporting zeros; that the autonomous/seeded
split is carried through so an acceptance position is never counted as
evidence the engine entered something; and that an unreadable read is not
an unrealised P&L of zero.
"""

from __future__ import annotations

import inspect

from sportsassets import bettor_external_shadow as EXT
from sportsassets import bettor_shadow_marks as MK
from sportsassets.api import command_rn1x as CR
from sportsassets.workers import rn1x_shadow as W

SRC = inspect.getsource(CR._pnl_status)


# ── every lane, including the one that was missing ───────────────────

def test_all_three_lanes_are_read():
    assert "W.HISTORICAL_EXPERIMENT_ID" in SRC
    assert "W.PROSPECTIVE_EXPERIMENT_ID" in SRC
    assert "EXT.EXPERIMENT_ID" in SRC
    assert '"autonomous_entry"' in SRC


def test_the_entry_lanes_id_is_read_from_its_own_module():
    """A copied string would drift the day the experiment is renamed, and
    the display would then quietly read an experiment nobody writes."""
    assert EXT.EXPERIMENT_ID == "EXT_PINNACLE_DEVIG_V1_SHADOW"
    assert "EXT_PINNACLE_DEVIG_V1_SHADOW" not in SRC, \
        "the id must come from EXT.EXPERIMENT_ID, not a literal"


def test_the_omission_is_recorded_not_just_fixed():
    assert "WAS NOT IN THE P&L AT ALL" in SRC or \
        "MISSING FROM THIS DISPLAY" in SRC.upper()
    assert "understate" in SRC


# ── the unrealised basis ─────────────────────────────────────────────

def test_unrealised_is_identified_on_a_named_basis():
    assert "MK.MARK_BASIS" in SRC
    assert MK.MARK_BASIS == "EXECUTABLE_EXIT_NET_OF_FEES_ON_OBSERVED_DEPTH"


def test_the_midpoint_reasoning_is_kept_not_deleted():
    """It was the right reason. It just did not support reporting no
    unrealised figure at all."""
    assert "MK.WHY_NOT_A_MIDPOINT" in SRC
    assert "nobody transacted" in MK.WHY_NOT_A_MIDPOINT
    assert "MK.WHY_NOT_THE_HOLD_VALUE" in SRC


def test_open_inventory_is_still_reported_at_cost():
    """A mark and a basis are different facts, so the mark does not
    replace the cost figure."""
    assert "open_inventory_at_cost_usd" in CR.OPEN_POSITIONS_SQL
    assert "at cost as well" in SRC or "still" in SRC


# ── the reads themselves ─────────────────────────────────────────────

def test_the_latest_decision_per_position_is_what_marks_it():
    """An older cycle's bid is not a mark, so DISTINCT ON takes the newest
    decision per position."""
    q = CR.OPEN_WITH_LATEST_DECISION_SQL
    assert "DISTINCT ON (p.position_id)" in q
    assert "d.decision_ts DESC" in q
    # AND IT IS THE OPEN BOOK: a settled position has an outcome row.
    assert "o.position_id IS NULL" in q


def test_the_position_quantity_and_basis_come_from_the_position_row():
    q = CR.OPEN_WITH_LATEST_DECISION_SQL
    assert "p.seed_qty" in q and "p.seed_basis_usd" in q
    assert "p.provenance" in q, "the autonomous/seeded split needs it"


def test_an_unreadable_open_book_is_not_a_zero_mark():
    assert "not an unrealised P&L of zero" in SRC or \
        "not an unrealised" in SRC
    assert '"readable": False' in SRC


def test_the_provenance_split_is_carried_into_the_display():
    assert "by_provenance" in SRC
    assert "PROVENANCE_NOT_DECLARED" in SRC


def test_jsonb_arriving_as_text_is_decoded_rather_than_guessed_at():
    """asyncpg hands JSONB back as str or dict depending on codecs. Both
    are handled, and anything else is passed through so the MARKER names it
    rather than this helper inventing an answer."""
    assert CR._as_json('{"ranked": []}') == {"ranked": []}
    assert CR._as_json({"ranked": []}) == {"ranked": []}
    assert CR._as_json("not json") is None
    assert CR._as_json(None) is None


# ── the whole thing, against the real tables ─────────────────────────

def test_the_status_runs_and_states_its_basis(pnl_pool):
    """Executed, not asserted from source: the SQL has to parse and the
    roll-up has to survive whatever the real rows contain."""
    got = pnl_pool
    assert got["unrealised_basis"] == MK.MARK_BASIS
    assert set(got["lanes"]) == {"historical", "prospective",
                                 "autonomous_entry"}
    for lane, d in got["lanes"].items():
        u = d.get("unrealised")
        assert isinstance(u, dict), lane
        if u.get("readable"):
            # A TOTAL IS EITHER COMPLETE OR EXPLICITLY PARTIAL. There is no
            # third state, and a number with no status is what this
            # prevents.
            assert u["total_pnl_status"] in ("COMPLETE", "PARTIAL"), lane
            if u["total_pnl_status"] == "PARTIAL":
                assert u["total_pnl_usd"] is None, lane
                assert u.get("why_total_is_partial"), lane
            assert u["mark_basis"] == MK.MARK_BASIS, lane
            assert u["modelled"] is True, lane


def test_the_autonomous_lane_reports_no_position_and_says_so(pnl_pool):
    """THE MEASURED STATE. The entry lane has created nothing, so its book
    is empty -- and an empty book must read as a measurement of zero
    positions, not as an absent lane."""
    d = pnl_pool["lanes"]["autonomous_entry"]
    assert d["unrealised"]["readable"] is True
    assert d["unrealised"]["open_positions"] == 0
    assert d["unrealised"]["unrealised_usd"] is None
    assert d["unrealised"]["total_pnl_status"] == "PARTIAL"


# ── the fixture that actually runs it ────────────────────────────────

import os                                                    # noqa: E402

import pytest                                                # noqa: E402

DSN = os.environ.get("RN1X_TEST_DSN")


@pytest.fixture(scope="module")
def pnl_pool():
    """Run `_pnl_status` against a real database, or skip.

    Skipped rather than mocked: a mock pool would prove the Python and
    nothing about whether the SQL parses, which is the half that broke
    twice already in this session (policy_version, decided_at).
    """
    if not DSN:
        pytest.skip("RN1X_TEST_DSN is not set")
    import asyncio

    import asyncpg

    async def go():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
        try:
            return await CR._pnl_status(pool)
        finally:
            await pool.close()

    return asyncio.run(go())
