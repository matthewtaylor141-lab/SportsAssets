"""§3: the Command Centre reads PERSISTED DECISIONS, not just the book.

Owner directive, item 3: "Wire the Command Centre to persisted decisions
and show: selected action and execution eligibility; actual order/fill
status; residual holdings and unresolved executions; realized results,
qualified unrealized marks and provisional amounts; the exact reason an
action or candidate could not proceed."

THE GAP THESE TESTS PIN. `_funded_service` decided an action for every
held position on every cycle and the answer was returned to a caller that
dropped it -- and on three early-return paths (lane stopped, table
missing, credential absent) the heartbeat was not written at all, which
are exactly the states an operator most needs the tile in. So the panel
could show 6 held contracts and could not say whether the lane had chosen
to hold them, tried to sell them and been refused, or never looked.
"""

import json

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets.workers import ext_pinnacle_loop as L

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


# ═════════════════════════════════════════════════════════════════════
# 1 · THE TWO MODULES AGREE ON THE ROW
# ═════════════════════════════════════════════════════════════════════

def test_the_reader_and_the_writer_name_the_same_heartbeat_row():
    """The reader hard-codes the key so the API does not import a worker.

    That is a real tradeoff, and this is the test that keeps it honest:
    the constant is duplicated, so it must be checked, or a rename in the
    loop silently turns the panel into "nothing has run".
    """
    assert FB.SCHEDULED_CYCLE_KEY == L.HEARTBEAT_KEY


# ═════════════════════════════════════════════════════════════════════
# 2 · THE DIGEST, ON THE REAL SHAPE `manage` RETURNS
# ═════════════════════════════════════════════════════════════════════

def test_a_missing_servicing_result_digests_to_none_not_to_an_empty_one():
    assert L._servicing_digest(None) is None


def test_the_digest_never_raises_on_an_unexpected_shape():
    """A heartbeat whose whole job is to survive must not die in a projection."""
    got = L._servicing_digest({"selection": "not a list"})
    assert got["ok"] is None
    assert "digest_failed" in got
    assert "read the funded book" in got["why_this_matters"].lower()


def test_the_digest_carries_the_named_ineligibility_code():
    """EXECUTION ELIGIBILITY, which is the directive's own wording."""
    got = L._servicing_digest({"selection": [{
        "intent_id": "i1", "ok": True, "residual_qty": 10.0,
        "ranking": {
            "selected": "DIRECT_EXIT", "selected_qty": 4,
            "selected_locks_a_loss": True,
            "candidates": [
                {"action": "DIRECT_EXIT", "selection_eligible": True},
                {"action": "TAKE_COMPLEMENT", "selection_eligible": False,
                 "selection_ineligible_because": {
                     "code": "NO_EXECUTION_PATH_IN_THIS_CALLER"}}]},
        "not_rankable": [{"action": "MERGE", "blocker": "MERGE_NOT_APPLICABLE"}],
    }]})
    d = got["decisions"][0]
    assert d["selected"] == "DIRECT_EXIT"
    assert d["locks_a_loss"] is True
    assert d["ineligible"] == [
        {"action": "TAKE_COMPLEMENT",
         "code": "NO_EXECUTION_PATH_IN_THIS_CALLER"}]
    # NOT the same fact: ineligible means "no execution path", not
    # rankable means "could not be priced at all".
    assert d["not_rankable"] == [
        {"action": "MERGE", "blocker": "MERGE_NOT_APPLICABLE"}]
    assert got["positions_serviced"] == 1


def test_the_digest_is_bounded_and_says_when_it_truncated():
    """A heartbeat row that grows with the book eventually fails to write."""
    many = [{"intent_id": "i%d" % n, "ranking": {}}
            for n in range(L.SERVICING_DIGEST_LIMIT + 3)]
    got = L._servicing_digest({"selection": many})
    assert len(got["decisions"]) == L.SERVICING_DIGEST_LIMIT
    assert got["decisions_truncated_at"] == L.SERVICING_DIGEST_LIMIT


def test_an_untruncated_digest_does_not_claim_truncation():
    got = L._servicing_digest({"selection": [{"intent_id": "i1",
                                              "ranking": {}}]})
    assert got["decisions_truncated_at"] is None


def test_the_exit_projection_reads_the_keys_manage_actually_emits():
    """`manage` keys exits by the PARENT's `intent_id`.

    My first version read `parent_intent_id`, `limit_price` and
    `residual_qty` -- none of which `manage` emits -- and persisted three
    nulls. A digest that reads keys the producer does not emit is worse
    than no digest, because it looks populated.
    """
    got = L._servicing_digest({"selection": [], "exits": [{
        "intent_id": "parent-1", "exit_intent_id": "x-1",
        "selected": "REDUCE", "selected_qty": 3,
        "wire_limit_price": 0.55, "submitted": True, "venue_calls": 1,
        "position_after": {"residual_qty": 7.0}}]})
    x = got["exits"][0]
    assert x["parent_intent_id"] == "parent-1"
    assert x["wire_limit_price"] == 0.55
    assert x["position_after"]["residual_qty"] == 7.0


# ═════════════════════════════════════════════════════════════════════
# 3 · THE BLOCKER AN OPERATOR NOW READS IS NOT STUTTERED
# ═════════════════════════════════════════════════════════════════════

def test_the_settlement_blocker_is_not_double_prefixed():
    from sportsassets import bettor_funded_management as FM
    from sportsassets import bettor_mgmt_select as S

    out = S.rank_with_hold(
        qty=10, own_basis_per_contract=0.50,
        ev_hold={"probability": 0.55, "status": "IDENTIFIED"},
        bid=0.60, bid_size=10, complement_ask=None,
        complement_ask_size=None, fee_fn=FM.funded_fee_fn,
        venue="polymarket-us", us_market_slug="x", held_is_long=True)
    blockers = [r["blocker"] for r in out["not_rankable"]]
    assert "SETTLEMENT_SEMANTICS_NOT_SUPPLIED" in blockers
    assert not any(b.count("SETTLEMENT_SEMANTICS_") > 1 for b in blockers), \
        blockers


# ═════════════════════════════════════════════════════════════════════
# 4 · "NOTHING HAS RUN" IS NOT "NOTHING TO DO"
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_an_absent_heartbeat_reports_that_nothing_ran():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           FB.SCHEDULED_CYCLE_KEY)
        d = await FB.last_scheduled_decision(conn)
        assert d["available"] is False
        assert "nothing has run" in d["why"]
        # AND NOT an empty decision list, which reads as "all clear".
        assert "servicing" not in d
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_heartbeat_without_the_field_blames_the_serving_build():
    """An older build writes this row with no `funded_servicing`."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
            FB.SCHEDULED_CYCLE_KEY,
            json.dumps({"at": 1.0, "state": "RAN", "writer": "old-build"}))
        d = await FB.last_scheduled_decision(conn)
        assert d["available"] is False
        assert "SERVING BUILD" in d["why"]
        assert d["writer"] == "old-build"
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           FB.SCHEDULED_CYCLE_KEY)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_unreadable_json_is_reported_rather_than_raised():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        # A JSON scalar string: valid jsonb, and `.get` on it would raise.
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
            FB.SCHEDULED_CYCLE_KEY, json.dumps("not an object"))
        d = await FB.last_scheduled_decision(conn)
        assert d["available"] is False
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           FB.SCHEDULED_CYCLE_KEY)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · A STOPPED CYCLE STILL RECORDS WHAT IT DECIDED
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_stopped_cycle_persists_its_servicing_decision(monkeypatch):
    """The three early returns used to skip the heartbeat entirely.

    Servicing runs ABOVE the entry gates on purpose. A stopped entry loop
    is a reason to add nothing, not a reason to stop managing an open
    position -- so the decision it made must survive the cycle.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           FB.SCHEDULED_CYCLE_KEY)

        async def _stopped(c):
            return False, "the observation stop is engaged"

        monkeypatch.setattr(L, "_running", _stopped)
        monkeypatch.setattr(
            L, "_funded_service",
            lambda c, *, now: _fake_service())
        out = await L.cycle(conn)
        assert out["state"] == "STOPPED"
        d = await FB.last_scheduled_decision(conn)
        assert d["available"] is True, d
        assert d["cycle_state"] == "STOPPED"
        assert d["servicing"]["decisions"][0]["selected"] == "DIRECT_EXIT"
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           FB.SCHEDULED_CYCLE_KEY)
        await conn.close()


async def _fake_service():
    return {"ok": True, "selection": [{
        "intent_id": "i-stopped", "ok": True, "residual_qty": 5.0,
        "ranking": {"selected": "DIRECT_EXIT", "selected_qty": 5}}]}


# ═════════════════════════════════════════════════════════════════════
# 6 · THE SECTION CARRIES THE DECISION AND THE CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_section_carries_the_decision_beside_the_book():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        cc = await FB.command_center(conn)
        assert "last_scheduled_decision" in cc
        assert "why is this still held" in cc["decision_note"]
        # PER-CLASS, because the section can hold both kinds now.
        assert "funded_books" in cc and "demonstration_books" in cc
        assert cc["funded_book_count"] + cc["demonstration_book_count"] == \
            cc["book_count"]
        assert "PER BOOK" in cc["counts_note"]
        assert "nothing stops a caller" in cc[
            "classification_is_a_convention_not_a_permission"]
    finally:
        await conn.close()


def test_the_classification_is_not_claimed_as_enforcement():
    """Standing instruction: do not present a convention as a control.

    My first draft of this test ended both assertions in `or True`, which
    made it pass whatever the strings said. That is worse than no test.
    """
    demo = FB.classify_book("acct-DEMONSTRATION-1")
    assert demo["counts_toward_strategy_performance"] is False
    assert "establishes NOTHING about opportunity" in demo["why"]
    # A FUNDED book reads None, not False: False would claim a finding
    # this reader has not made.
    assert FB.classify_book("acct-pilot-1")[
        "counts_toward_strategy_performance"] is None
