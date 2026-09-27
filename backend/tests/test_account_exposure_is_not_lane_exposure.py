"""ONE POSITION IN THIS LANE IS NOT ONE POSITION ON THE ACCOUNT.

THE CLAIM THIS FILE WITHDRAWS, IN MY OWN WORDS, from the owner decision package:

    "Cross-lane account exposure is not implemented. Until it is, the pilot
     procedure below holds ONE POSITION AT A TIME, which makes the gap
     unreachable in practice rather than merely unlikely."

It does not make the gap unreachable. The venue sees one account. Whether this
lane holds one position or ten says nothing about what a manual desk, the legacy
copier, or an unresolved submission from last week has already committed there.

AND THE MECHANISM I CITED IS LANE-LOCAL. The one-live-intent guarantee is a
UNIQUE INDEX on `bettor_funded_intents`. A row in `live_orders` does not violate
it. So the index bounds this lane's rows and not the account's risk, which is a
different statement from the one I made.

WHAT THE TESTS BELOW PIN.

  1  The claim is recorded as WITHDRAWN, with the reason, rather than deleted.
  2  Exposure fails CLOSED. A required path that cannot be read makes the total
     UNREADABLE -- never a partial sum that looks like a total.
  3  Working orders and unresolved submissions are counted, the second at FULL
     requested size, because an unknown treated as zero is how a timed-out
     submission becomes a double position.
  4  Isolation cannot be passed by asserting it.
  5  One table serving two paths is counted ONCE, so the fix does not introduce
     double counting in the other direction.
"""

from __future__ import annotations

import contextlib
import os

import asyncpg
import pytest

from sportsassets import bettor_account_exposure as AE

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


# ── 1 · the withdrawal is on the record ──────────────────────────────

def test_the_one_position_claim_is_recorded_as_withdrawn():
    got = AE.isolation_evidence()
    assert "withdrawn" in got["what_I_withdrew"]
    assert "unreachable in practice" in got["what_I_withdrew"]
    assert "fact about this lane" in (
        got["a_one_position_pilot_does_not_demonstrate_this"])
    assert set(got["the_two_routes"]) == {"ENFORCE", "ISOLATE"}


def test_the_lane_local_index_is_named_as_insufficient():
    """The specific mechanism I leaned on, and why it does not reach."""
    d = AE.describe()
    assert "UNIQUE INDEX" in d["lane_local_index_is_insufficient"]
    assert "does not violate it" in d["lane_local_index_is_insufficient"]
    reqs = {r["id"]: r for r in AE.ISOLATION_REQUIREMENTS}
    assert reqs["NO_OTHER_CREDENTIAL"][
        "a_lane_local_index_does_not_show_it"] is True


# ── 2 · every path that can commit the account is enumerated ─────────

def test_the_required_paths_include_the_legacy_and_manual_routes():
    """THE THREE I HAD NOT COUNTED. `live_orders` carries both the earlier live
    beta and, since migration 014, a manual sleeve -- so a human trade lands in
    the same table this lane never reads."""
    assert "THIS_LANE" in AE.REQUIRED_PATHS
    assert "LEGACY_COPIER" in AE.REQUIRED_PATHS
    assert "MANUAL_DESK" in AE.REQUIRED_PATHS
    assert "VENUE_HELD_POSITIONS" in AE.REQUIRED_PATHS
    # AND THE MODELLED LANES ARE EXCLUDED BY DECISION, NOT BY OMISSION.
    shadow = [p for p in AE.PATHS if p["path"] == "SHADOW_LANES"][0]
    assert shadow["required"] is False
    assert shadow["adds_real_exposure"] is False
    assert "CHECK that nothing was submitted" in shadow["why"]


def test_the_venue_is_the_authority_and_its_absence_is_not_a_zero():
    venue = [p for p in AE.PATHS if p["path"] == "VENUE_HELD_POSITIONS"][0]
    assert venue["required"] is True
    assert "THE AUTHORITY" in venue["why"]
    assert "credential" in venue["needs"]
    assert "Not requested here" in venue["needs"], (
        "this module must not ask for a credential in order to run")


def test_all_three_exposure_classes_are_counted_not_just_holdings():
    assert set(AE.CLASSES) == {AE.HELD, AE.WORKING, AE.UNRESOLVED}


# ── 3 · it fails closed ──────────────────────────────────────────────

@pg
@pytest.mark.asyncio
async def test_without_a_venue_read_the_total_is_UNREADABLE_not_partial():
    """THE FAILURE MODE THIS PREVENTS. A sum over the paths that happened to
    answer LOOKS like a total, and a gate that reads it passes on an account it
    never measured."""
    async with _conn() as conn:
        got = await AE.account_exposure(conn, account_id="acct_x")
        assert got["state"] == AE.TOTAL_UNREADABLE
        assert got["TOTAL_USD"] is None
        assert got["BLOCKER"] == AE.R_UNREADABLE
        assert "VENUE_HELD_POSITIONS" in got["unreadable_required_paths"]
        assert "would look like a total" in got["why"]
        # AND THE VENUE IS THE ONLY THING MISSING, so this is not a database
        # failure being reported as a credential one. A DB path that answered
        # or whose table is absent is settled either way; only READ_FAILED is
        # an unknown, and only the venue has one here.
        assert got["unreadable_required_paths"] == ["VENUE_HELD_POSITIONS"]
        assert got["paths"]["LEGACY_COPIER"]["read"] == AE.READ_OK


@pg
@pytest.mark.asyncio
async def test_with_every_path_read_the_total_is_a_number():
    async with _conn() as conn:
        got = await AE.account_exposure(
            conn, account_id="acct_x",
            venue_positions={"held_usd": 40.0, "working_usd": 10.0,
                             "unresolved_usd": 0.0, "read_at_epoch_s": 1.0})
        assert got["state"] == AE.TOTAL_MEASURED
        assert got["BLOCKER"] is None
        assert got["TOTAL_USD"] >= 50.0
        assert got["by_class"][AE.HELD] >= 40.0
        assert got["by_class"][AE.WORKING] >= 10.0


@pg
@pytest.mark.asyncio
async def test_a_venue_held_position_this_lane_cannot_see_raises_the_total():
    """THE WHOLE POINT, IN ONE COMPARISON. This lane holds nothing; the account
    holds $90. A gate reading only this lane would see room for a $25 order."""
    async with _conn() as conn:
        empty = await AE.account_exposure(
            conn, venue_positions={"held_usd": 0.0, "working_usd": 0.0,
                                   "unresolved_usd": 0.0})
        loaded = await AE.account_exposure(
            conn, venue_positions={"held_usd": 90.0, "working_usd": 0.0,
                                   "unresolved_usd": 0.0})
        assert empty["TOTAL_USD"] == 0.0
        assert loaded["TOTAL_USD"] == 90.0
        assert loaded["paths"]["THIS_LANE"][AE.HELD] == 0.0, (
            "this lane holds nothing and the account holds 90: that difference is "
            "exactly what the withdrawn claim assumed away")


@pg
@pytest.mark.asyncio
async def test_one_table_serving_two_paths_is_counted_once():
    """THE OPPOSITE ERROR, AND IT WOULD BE JUST AS WRONG. `live_orders` holds
    both the legacy copier and the manual sleeve and does not separate them, so
    reporting the same figures under both names would DOUBLE the account's
    measured exposure."""
    async with _conn() as conn:
        got = await AE.account_exposure(
            conn, venue_positions={"held_usd": 0.0, "working_usd": 0.0,
                                   "unresolved_usd": 0.0})
        manual = got["paths"]["MANUAL_DESK"]
        assert manual["counted_under"] == "LEGACY_COPIER"
        assert manual[AE.HELD] == 0.0
        assert "not a claim that the manual sleeve is empty" in (
            manual["why_zero_here"])


@pg
@pytest.mark.asyncio
async def test_an_unresolved_submission_counts_at_FULL_requested_size():
    """AN UNKNOWN OUTCOME IS NOT A ZERO. A submission that timed out may have
    reached the venue, so it reserves what it asked for until it resolves."""
    async with _conn() as conn:
        await conn.execute("DELETE FROM live_orders")
        await conn.execute(
            "INSERT INTO live_orders (asset, side, his_price, limit_price,"
            " requested_usd, requested_shares, status, filled_usd, filled_shares) "
            "VALUES ('a','BUY',0.5,0.55,100,200,'submitting',0,0)")
        got = await AE.account_exposure(
            conn, venue_positions={"held_usd": 0.0, "working_usd": 0.0,
                                   "unresolved_usd": 0.0})
        assert got["by_class"][AE.UNRESOLVED] == 100.0, (
            "the row filled nothing; counting its FILLED amount would score a "
            "possibly-live submission as zero exposure")
        assert got["TOTAL_USD"] == 100.0
        assert got["an_unresolved_submission_counts_at_full_size"] is True
        await conn.execute("DELETE FROM live_orders")


@pg
@pytest.mark.asyncio
async def test_an_errored_submission_is_also_unresolved_not_zero():
    """An error writing OUR row does not prove nothing reached the venue."""
    async with _conn() as conn:
        await conn.execute("DELETE FROM live_orders")
        await conn.execute(
            "INSERT INTO live_orders (asset, side, his_price, limit_price,"
            " requested_usd, requested_shares, status, filled_usd, filled_shares) "
            "VALUES ('a','BUY',0.5,0.55,75,150,'error',0,0)")
        got = await AE.account_exposure(
            conn, venue_positions={"held_usd": 0.0, "working_usd": 0.0,
                                   "unresolved_usd": 0.0})
        assert got["by_class"][AE.UNRESOLVED] == 75.0
        await conn.execute("DELETE FROM live_orders")


@pg
@pytest.mark.asyncio
async def test_a_settled_or_rejected_row_commits_nothing_further():
    """The counting must be conservative, not merely large: history is not
    exposure, or the 166,585 legacy rows would refuse every order forever."""
    async with _conn() as conn:
        await conn.execute("DELETE FROM live_orders")
        for st in ("settled", "rejected", "unfilled"):
            await conn.execute(
                "INSERT INTO live_orders (asset, side, his_price, limit_price,"
                " requested_usd, requested_shares, status, filled_usd,"
                " filled_shares) VALUES ('a','BUY',0.5,0.55,500,1000,$1,0,0)", st)
        got = await AE.account_exposure(
            conn, venue_positions={"held_usd": 0.0, "working_usd": 0.0,
                                   "unresolved_usd": 0.0})
        assert got["TOTAL_USD"] == 0.0, got["paths"]["LEGACY_COPIER"]
        await conn.execute("DELETE FROM live_orders")


# ── 4 · isolation cannot be asserted ─────────────────────────────────

def test_an_absent_table_is_zero_but_a_FAILED_read_is_not():
    """THE DISTINCTION, AND LUMPING THEM WOULD BE WRONG BOTH WAYS.

    A table that does not exist holds no rows, so that path's exposure is
    KNOWABLY zero -- a lane that was never migrated cannot have committed the
    account, and refusing on that would refuse forever. A table we could not
    read may hold anything, and treating THAT as zero is the whole failure mode
    this module exists to prevent.

    The venue path cannot hide behind this: without a credential it reports
    READ_FAILED, never TABLE_ABSENT, so no missing migration excuses the
    authority.
    """
    venue = [p for p in AE.PATHS if p["path"] == "VENUE_HELD_POSITIONS"][0]
    assert venue["source"] == "VENUE"
    assert venue["tables"] == (), (
        "the venue path has no table, so it can never report TABLE_ABSENT")


def test_isolation_is_NOT_DEMONSTRATED_by_default():
    got = AE.isolation_evidence()
    assert got["verdict"] == AE.ISOLATION_NOT_DEMONSTRATED
    assert got["BLOCKER"] == AE.R_NOT_ISOLATED
    assert set(got["missing"]) == {r["id"] for r in AE.ISOLATION_REQUIREMENTS}


def test_partial_isolation_evidence_still_refuses():
    """Three of five is not isolation, and the missing two are named."""
    got = AE.isolation_evidence({
        "NO_OTHER_CREDENTIAL": "venue key list, 1 key, read 2026-09-27",
        "NO_PRE_EXISTING_HOLDINGS": "venue positions empty at 16:00Z",
        "NO_OPEN_ORDERS": "venue open orders empty at 16:00Z",
    })
    assert got["verdict"] == AE.ISOLATION_NOT_DEMONSTRATED
    assert set(got["missing"]) == {"NO_MANUAL_ACCESS",
                                   "NO_UNRESOLVED_SUBMISSIONS"}


def test_every_isolation_requirement_says_how_it_would_be_SHOWN():
    """A requirement with no exhibit is a belief with a heading."""
    for r in AE.ISOLATION_REQUIREMENTS:
        assert r["how_it_would_be_shown"], r["id"]
        assert r["claim"], r["id"]
    # AND THE ORGANISATIONAL ONE IS MARKED AS THE OWNER'S TO STATE, so it is
    # not left looking like something code can settle.
    manual = [r for r in AE.ISOLATION_REQUIREMENTS
              if r["id"] == "NO_MANUAL_ACCESS"][0]
    assert "owner's to state" in manual["how_it_would_be_shown"]


def test_pre_existing_holdings_open_orders_and_unresolved_are_all_required():
    """The three the instruction names explicitly, each its own requirement."""
    ids = {r["id"] for r in AE.ISOLATION_REQUIREMENTS}
    assert {"NO_PRE_EXISTING_HOLDINGS", "NO_OPEN_ORDERS",
            "NO_UNRESOLVED_SUBMISSIONS"} <= ids
