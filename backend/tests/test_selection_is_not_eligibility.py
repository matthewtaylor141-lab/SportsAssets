"""ACCOUNT SELECTION IS NOT ACCOUNT ELIGIBILITY.

THE CORRECTION THESE TESTS HOLD. An account can be NAMED for assessment, and
naming it changes nothing about it: it does not clear a paused status, it does not
produce reconciliation evidence, and confirming its id is not confirming that
capital may be committed. I had treated a named id as progress towards
eligibility, and it is not progress at all -- it is a different question with a
different answer.

So the route reports `selected_for_assessment` and `eligible` as SEPARATE fields,
and these tests check the separation holds in every combination: named and
ineligible, unnamed, absent from the registry, present but paused, present and
active but unreconciled.

AND THE ONE THING THE OTHER READS NEVER SHOWED. Who else can write this account's
exposure. The lanes that share `live_orders` have no import edge between them, so
no import analysis finds them; `capital_path.account_writers` finds them by write
statement, and a one-position rule in THIS lane bounds this lane only.
"""

from __future__ import annotations

import pytest

from sportsassets import capital_path as CP


# ── 1 · THE WRITERS, WHICH ARE THE ISOLATION QUESTION ────────────────

def test_the_real_exposure_table_has_SEVERAL_writers_and_they_are_named():
    """`live_orders` carries the legacy copier's real orders AND the manual desk's
    sleeve. If several modules write it, a uniqueness constraint in the autonomous
    lane constrains none of them, and the count is the finding."""
    aw = CP.account_writers()
    by_table = aw["writers_by_table"]
    assert "live_orders" in by_table
    writers = by_table["live_orders"]
    assert len(writers) >= 2, (
        "if this collapsed to one writer the isolation argument would change "
        "completely, so it is asserted rather than assumed")
    # The ones that matter are named, so a reader does not have to trust a count.
    for expected in ("live_executor.py", "workers/mirror_live.py"):
        assert expected in writers, (expected, writers)
    assert aw["writer_count"] >= len(writers)


def test_the_autonomous_lanes_own_tables_are_reported_separately():
    """THIS LANE'S tables are not the account. They are one path of five, and
    conflating them is how a lane-local total became an account total."""
    by_table = CP.account_writers()["writers_by_table"]
    assert "bettor_funded_intents" in by_table
    assert "bettor_funded_fills" in by_table
    assert "bettor_funded_book.py" in by_table["bettor_funded_fills"]


def test_the_enumeration_says_it_is_a_LOWER_BOUND_and_names_the_human():
    """A static scan cannot see a person at a database prompt or in the venue's
    own app, and on a funded account that unseen writer is the whole risk."""
    aw = CP.account_writers()
    blind = " ".join(aw["this_is_a_LOWER_BOUND"])
    assert "human at a database prompt" in blind
    assert "web interface or app" in blind
    assert "outside this repository" in blind
    assert "Counting writers is not controlling them" in (
        aw["and_what_follows_from_that"])


def test_it_finds_writes_and_NOT_reads(tmp_path):
    """A SELECT is not a writer. If reads counted, the enumeration would name
    every module that looks at the table and mean nothing."""
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "reader.py").write_text(
        "SQL = 'SELECT * FROM live_orders WHERE id = $1'\n")
    (d / "writer.py").write_text(
        "SQL = 'UPDATE live_orders SET state = $1'\n")
    (d / "inserter.py").write_text(
        "SQL = 'INSERT INTO live_orders (id) VALUES ($1)'\n")
    (d / "deleter.py").write_text(
        "SQL = 'DELETE FROM live_orders WHERE id = $1'\n")
    got = CP.writers_of(["live_orders"], root=str(d))
    assert set(got) == {"writer.py", "inserter.py", "deleter.py"}
    assert got["writer.py"]["live_orders"] == ["UPDATE"]
    assert got["inserter.py"]["live_orders"] == ["INSERT"]
    assert got["deleter.py"]["live_orders"] == ["DELETE"]


def test_a_different_table_is_not_a_hit(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "other.py").write_text(
        "SQL = 'INSERT INTO live_orders_archive (id) VALUES ($1)'\n")
    assert CP.writers_of(["live_orders"], root=str(d)) == {}


# ── 2 · THE ROUTE, THROUGH THE REAL HANDLER ──────────────────────────
#
# Called as a function with a stub connection rather than over HTTP, because the
# question under test is the VERDICT LOGIC -- does a paused row, or a missing
# reconciliation, produce `eligible: False` with a named reason -- and a live
# database would answer a different question.

class _StubConn:
    """Returns whatever the test seeds. `account_exposure` is stubbed out by the
    test that needs it; here the read raising is itself a case worth covering."""

    def __init__(self, registry=(), state=None):
        self._registry = [dict(r) for r in registry]
        self._state = state

    async def fetch(self, sql, *args):
        return [dict(r) for r in self._registry]

    async def fetchrow(self, sql, *args):
        return None

    async def fetchval(self, sql, *args):
        return self._state


def _row(account_id="acct_under_test", status="ACTIVE", paused=False):
    return {"account_id": account_id, "desk_id": "d", "status": status,
            "paused": paused, "paused_reason": None,
            "accounting_status": "UNRESOLVED"}


@pytest.mark.parametrize("registry,recon_ok,expected_eligible,expect_reason", [
    # NAMED BUT ABSENT FROM THE REGISTRY.
    ((), False, False, "no registry row"),
    # PRESENT AND PAUSED -- the case that actually applies today.
    ((_row(paused=True),), True, False, "PAUSED"),
    # PRESENT, NOT ACTIVE.
    ((_row(status="RETIRED"),), True, False, "status="),
    # PRESENT AND ACTIVE BUT UNRECONCILED. An absent reconciliation is not a
    # passing one, and this is the case most likely to be read as a pass.
    ((_row(),), False, False, "reconciliation:"),
    # THE CORRECT CASE: active, unpaused, reconciled. It MUST come out eligible,
    # or the route would be refusing everything and proving nothing.
    ((_row(),), True, True, None),
])
def test_eligibility_is_earned_and_each_failure_is_NAMED(
        monkeypatch, registry, recon_ok, expected_eligible, expect_reason):
    import asyncio

    from sportsassets import bettor_account_exposure as AE
    from sportsassets import bettor_account_onboarding as ON
    from sportsassets.api import app as A

    async def _recon(conn, *, account_id=None, **kw):
        return ({"ok": True, "refusal": None, "age_s": 12.0} if recon_ok
                else {"ok": False, "refusal": ON.R_NO_EVIDENCE})

    async def _expo(conn, *, account_id=None, venue_positions=None, now=None):
        return {"total": None, "refusal": "TOTAL_UNREADABLE"}

    monkeypatch.setattr(ON, "reconciliation_evidence", _recon)
    monkeypatch.setattr(AE, "account_exposure", _expo)

    conn = _StubConn(registry=registry)

    class _Pool:
        def acquire(self):
            class _Ctx:
                async def __aenter__(_s):
                    return conn

                async def __aexit__(_s, *a):
                    return False
            return _Ctx()

    async def _pool():
        return _Pool()

    monkeypatch.setattr("sportsassets.db.get_pool", _pool)

    class _Resp:
        headers: dict = {}

    got = asyncio.run(A.admin_funded_account_eligibility(
        _Resp(), account_id="acct_under_test"))

    # SELECTION AND ELIGIBILITY ARE SEPARATE FIELDS, always.
    assert got["selected_for_assessment"] is True
    assert got["eligible"] is expected_eligible, got
    if expect_reason is None:
        assert got["why_not"] is None
    else:
        assert any(expect_reason in r for r in got["why_not"]), got["why_not"]

    # AND THE SEPARATION IS STATED, not left for the reader to infer.
    assert "does not clear its paused or unreconciled state" in (
        got["and_selection_is_not_eligibility"])
    # THE WRITERS ARE ALWAYS PRESENT, eligible or not.
    assert got["every_writer_that_can_reach_the_account"]["writer_count"] > 0
    assert "counting them is not controlling them" in (
        got["and_a_one_position_rule_does_not_isolate_this_account"])
    # AN UNREADABLE EXPOSURE TOTAL IS NEVER REPORTED AS ZERO.
    assert got["account_wide_exposure"]["total"] is None
    assert got["this_route_is_read_only"] == {"method": "GET", "writes": 0,
                                              "venue_calls": 0}
