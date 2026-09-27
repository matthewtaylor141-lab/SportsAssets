"""THE AUDIT'S ENGINEERING FINDINGS, EACH WITH THE DEFECT IT REPRODUCED.

One section per finding. Each begins by pinning the WRONG behaviour the audit
found -- reproduced here as the counterexample it was -- and then pins the
corrected behaviour, so a future reader can see what changed and why rather than
taking the fix on trust.

  A3   a readiness endpoint that read a registry flag while the four venue
       reads existed and nothing called them.
  A4   one typed limit schema: units, loss window, aggregation scope, and the
       rails an owner approval cannot reach.
  A9   a shadow open-book query with no event identity, so MAX_EVENT_EXPOSURE
       summed nothing and reported zero on every fixture.
"""

from __future__ import annotations

import time

import pytest

from sportsassets import bettor_account_onboarding as ON
from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets.workers import ext_pinnacle_loop as L


# ── A9 · THE SHADOW BOOK'S EVENT IDENTITY ───────────────────────────

def test_a9_the_open_book_query_now_selects_an_event_identity():
    """THE DEFECT, IN THE QUERY ITSELF.

    `exposure_from_rows` sums a row into MAX_EVENT_EXPOSURE only when
    `r["event_key"]` equals the candidate's. The query selected no such column,
    so the key was None on every row, the condition never held, and the rail
    measured ZERO however many positions the lane held on the fixture. Not an
    unenforced rail -- a rail reading a quantity nobody selected.
    """
    sql = L.OPEN_BOOK_SQL
    assert "event_slug" in sql and "AS event_key" in sql
    assert "us_premap" in sql, "the venue's own event namespace"
    assert "event_key_resolved" in sql, (
        "an unresolvable event must be distinguishable from a different event")
    # THE JOIN IS ON THE VENUE'S MARKET SLUG, which is the column that exists.
    assert "m.market_slug = p.venue_market_slug" in sql


def test_a9_the_defect_reproduced_and_then_repaired():
    """THE ARITHMETIC, BOTH WAYS.

    Two open positions of $40 on one fixture, and a proposed $10. Under the old
    rows -- no event key on any of them -- the event rail counts only the
    proposal, $10, and a $25 event cap looks satisfied. With the identity present
    it reads $90 and the cap is breached. The rail was not lenient; it was
    reading a quantity that was never selected.
    """
    old = [{"condition_id": "c1", "cost_usd": 40.0, "qty": 100.0,
            "opened_at": 1.0, "realized_net_usd": None},
           {"condition_id": "c2", "cost_usd": 40.0, "qty": 100.0,
            "opened_at": 1.0, "realized_net_usd": None}]
    got = EX.exposure_from_rows(old, condition_id="c3",
                               event_key="nba-bos-lal-2026-09-27",
                               proposed_cost_usd=10.0, proposed_qty=20.0,
                               now=2.0)
    assert got["observed"]["MAX_EVENT_EXPOSURE"] == 10.0, (
        "this is the defect: two open positions on the fixture, and the rail "
        "counts only the PROPOSED $10 because no existing row carried an event "
        "identity to be summed")

    new = [dict(r, event_key="nba-bos-lal-2026-09-27",
                event_key_resolved=True) for r in old]
    fixed = EX.exposure_from_rows(new, condition_id="c3",
                                 event_key="nba-bos-lal-2026-09-27",
                                 proposed_cost_usd=10.0, proposed_qty=20.0,
                                 now=2.0)
    assert fixed["observed"]["MAX_EVENT_EXPOSURE"] == 90.0, (
        "the proposed $10 plus the two $40 positions the rail could not see")
    # AND THAT IS THE DIFFERENCE BETWEEN A SATISFIED $25 CAP AND A BREACHED ONE.
    assert got["observed"]["MAX_EVENT_EXPOSURE"] <= 25.0
    assert fixed["observed"]["MAX_EVENT_EXPOSURE"] > 25.0


def test_a9_an_unresolvable_event_identity_refuses_rather_than_reads_zero():
    """AND THE HALF THAT MATTERS MOST. A row whose slug does not resolve must
    make the rail UNMEASURABLE, not smaller: reporting a partial sum and calling
    the rail satisfied is the same failure one step along."""
    ok = L.event_exposure_is_measurable(
        [{"realized_net_usd": None, "event_key_resolved": True,
          "venue_market_slug": "aec-a"}])
    assert ok["measurable"] is True

    bad = L.event_exposure_is_measurable(
        [{"realized_net_usd": None, "event_key_resolved": True,
          "venue_market_slug": "aec-a"},
         {"realized_net_usd": None, "event_key_resolved": False,
          "venue_market_slug": "aec-b"}])
    assert bad["measurable"] is False
    assert bad["refusal"] == L.R_EVENT_IDENTITY_UNRESOLVED
    assert bad["rows_without_an_event_identity"] == 1
    assert "smaller number" in bad["why"]

    # A SETTLED ROW OCCUPIES NO EXPOSURE, so it cannot block the rail.
    settled = L.event_exposure_is_measurable(
        [{"realized_net_usd": -3.0, "event_key_resolved": False,
          "venue_market_slug": "aec-old"}])
    assert settled["measurable"] is True

    # AN UNREAD BOOK IS NEITHER.
    unread = L.event_exposure_is_measurable(None)
    assert unread["measurable"] is None


def test_a9_the_candidate_uses_the_venues_event_namespace_not_the_providers():
    """THE TRAP A REPAIRED QUERY ALONE WOULD HAVE FALLEN INTO.

    The book's keys are now `us_premap.event_slug`. The candidate's key used to
    be the ODDS PROVIDER's `event_id` -- a different namespace -- so the two
    would never compare equal and the rail would still have read zero on a fully
    repaired query. Both sides now come from the same column.
    """
    import inspect

    src = inspect.getsource(L.cycle)
    assert 'event_key=ident.get("venue_event_key")' in src
    assert 'provider_event_id=quote["event_id"]' in src, (
        "the provider's id is still recorded -- it is just not the key")
    ident_src = inspect.getsource(L.resolve_venue_identity)
    assert "SELECT event_slug FROM us_premap" in ident_src


def test_a9_the_plan_refuses_when_the_event_rail_cannot_be_measured():
    import inspect

    src = inspect.getsource(L._entry_plan)
    assert "R_EVENT_IDENTITY_UNRESOLVED" in src
    assert "read as zero" in src


# ── A4 · ONE TYPED LIMIT SCHEMA ─────────────────────────────────────

def test_a4_every_declared_rail_has_a_unit_a_window_and_a_scope():
    assert EX.RAIL_TYPES_COVER_EVERY_DECLARED_RAIL is True
    for name in EX.PREDECLARED_LIMITS:
        t = EX.rail_type(name)
        assert t["unit"] in (EX.UNIT_USD, EX.UNIT_CONTRACTS,
                             EX.UNIT_USD_HOURS), name
        assert t["window"], name
        assert t["scope"], name
        assert t["measures"], name


def test_a4_residual_inventory_is_contracts_and_cannot_be_shown_as_dollars():
    """THE DEFECT THE AUDIT NAMED. The rail counts CONTRACTS -- the code says so
    (`MAX_RESIDUAL_INVENTORY_CONTRACTS`, and the exposure function sums `qty`) --
    and the activation proposal presented it as dollars. At the observed
    0.35-0.65 prices, "$50" read as fifty contracts is roughly $18-$33 of
    exposure. One typed declaration, one formatter."""
    t = EX.rail_type("MAX_RESIDUAL_INVENTORY")
    assert t["unit"] == EX.UNIT_CONTRACTS
    assert t.get("a_dollar_sign_here_is_a_bug") is True
    assert EX.format_rail("MAX_RESIDUAL_INVENTORY", 2000.0) == "2000 contracts"
    assert "$" not in EX.format_rail("MAX_RESIDUAL_INVENTORY", 2000.0)
    # AND NO OWNER FIELD REACHES IT, so an approval cannot tighten it and must
    # not appear to.
    assert t["owner_field"] is None
    assert "no owner field" in t["not_owner_approvable"]
    assert "MAX_RESIDUAL_INVENTORY" not in set(
        EX.APPROVED_LIMIT_TO_RAIL.values())


def test_a4_capital_hours_is_dollar_hours_not_a_duration():
    assert EX.rail_type("MAX_CAPITAL_HOURS")["unit"] == EX.UNIT_USD_HOURS
    assert EX.format_rail("MAX_CAPITAL_HOURS", 1200.0) == "1200 $·h"
    assert "ALREADY held" in EX.rail_type("MAX_CAPITAL_HOURS")["measures"]


def test_a4_the_loss_window_is_cumulative_and_says_the_name_is_wrong():
    t = EX.rail_type("MAX_DRAWDOWN")
    assert t["window"] == EX.WINDOW_CUMULATIVE
    assert t["owner_field"] == "daily_loss_stop_usd"
    assert "it says daily" in t["the_owner_field_name_is_inaccurate"]
    assert "no reset" in t["the_owner_field_name_is_inaccurate"]
    assert "timezone" in t["a_real_daily_window_would_need"]


def test_a4_the_side_by_side_readback_shows_frozen_approved_and_effective():
    got = EX.typed_limits({"capital_usd": 250, "per_order_usd": 25,
                           "max_exposure_usd": 100,
                           "daily_loss_stop_usd": 50,
                           "event_exposure_usd": 25})
    by = {r["rail"]: r for r in got["rows"]}
    assert set(by) == set(EX.PREDECLARED_LIMITS)
    # A TIGHTENED DOLLAR RAIL.
    ev = by["MAX_EVENT_EXPOSURE"]
    assert ev["frozen"] == 1000.0 and ev["effective"] == 25.0
    assert ev["tightened"] is True
    assert ev["effective_display"] == "$25"
    assert ev["owner_field"] == "event_exposure_usd"
    assert ev["owner_proposed"] == 25
    assert ev["scope"] == EX.SCOPE_ONE_EVENT
    # A RAIL NO APPROVAL REACHES, shown in its own unit and marked as such.
    ri = by["MAX_RESIDUAL_INVENTORY"]
    assert ri["owner_approvable"] is False
    assert ri["effective_display"].endswith("contracts")
    assert ri["tightened"] is False
    # AND THE DIGESTS TRAVEL WITH IT.
    assert got["frozen_digest"] and got["effective_digest"]
    assert got["frozen_digest"] != got["effective_digest"]
    assert got["loss_window"] == EX.WINDOW_CUMULATIVE


def test_a4_every_owner_field_maps_to_exactly_one_typed_rail():
    """No owner field may exist that no rail claims, and no two fields may
    claim one rail -- either way an approval would be ambiguous."""
    claimed = [t["owner_field"] for t in EX.RAIL_TYPES.values()
               if t.get("owner_field")]
    assert len(claimed) == len(set(claimed)), "two fields claim one rail"
    assert set(claimed) == set(EX.APPROVED_LIMIT_TO_RAIL)
    for field, rail in EX.APPROVED_LIMIT_TO_RAIL.items():
        assert EX.RAIL_TYPES[rail]["owner_field"] == field, (field, rail)


# ── A3 · A REAL RECONCILIATION, PERSISTED, WITH AN AGE ──────────────

def test_a3_the_readiness_route_no_longer_claims_to_rerun_reconciliation():
    import inspect

    from sportsassets.api import app as A

    doc = inspect.getdoc(A.admin_funded_activation_readiness) or ""
    flat = " ".join(doc.split())
    assert "does NOT contact the venue" in flat
    assert "does NOT rerun account reconciliation" in flat
    assert "reports that evidence's AGE" in flat


def test_a3_a_documented_operator_route_actually_performs_the_reads():
    from sportsassets.api import app as A

    paths = {getattr(r, "path", "") for r in A.app.routes}
    assert "/api/admin/funded-account-reconcile" in paths
    assert "/api/admin/funded-account-reconciliation" in paths
    # IT IS A POST, because it contacts the venue. A readiness GET that
    # silently reached a venue would be a write-shaped action wearing a GET.
    post = [r for r in A.app.routes
            if getattr(r, "path", "") == "/api/admin/funded-account-reconcile"]
    assert post and "POST" in getattr(post[0], "methods", set())


def test_a3_absent_evidence_is_not_passing_evidence():
    """THE REGISTRY FLAG IS A PAST CONCLUSION. Three separate failures, three
    names -- never recorded, recorded for another account, recorded too long
    ago -- because each needs a different action."""
    assert ON.R_NO_EVIDENCE and ON.R_EVIDENCE_STALE
    assert ON.R_EVIDENCE_OTHER_ACCOUNT
    assert ON.EVIDENCE_MAX_AGE_S == 2 * 3600.0


def test_a3_completeness_refuses_an_unanswered_or_partially_paged_read():
    """A positions read that stopped at page one can miss the very position
    that makes the account a discrepancy."""
    full = ON._completeness({"checks": [
        {"check": c, "verdict": ON.RECONCILED} for c in ON.CHECKS]})
    assert full["complete"] is True
    assert full["checks_missing"] == []

    missing = ON._completeness({"checks": [
        {"check": "balances", "verdict": ON.RECONCILED}]})
    assert missing["complete"] is False
    assert set(missing["checks_missing"]) == set(ON.CHECKS) - {"balances"}

    truncated = ON._completeness({"checks": [
        dict({"check": c, "verdict": ON.RECONCILED},
             **({"complete": False} if c == "positions" else {}))
        for c in ON.CHECKS]})
    assert truncated["complete"] is False
    assert truncated["reads_not_fully_paged"] == ["positions"]
    assert "not evidence" in truncated["why"]


def test_a3_the_readiness_account_check_requires_the_evidence_too():
    import inspect

    src = inspect.getsource(FA.readiness)
    assert "_reconciliation_evidence" in src
    assert "registry_flag_is_not_evidence" in src
    assert 'ev.get("passes")' in src


def test_a3_an_unreadable_evidence_read_does_not_pass():
    """A read that raised is not a clean account."""
    import asyncio

    class _Boom:
        async def fetchval(self, *a, **k):
            raise RuntimeError("no database")

    got = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        FA._reconciliation_evidence(_Boom(), "acct-x"))
    assert got["passes"] is False
    assert got["usable"] is False
    assert "not evidence that the account is clean" in got["why"]


# ── the three findings, against a real database ─────────────────────

DSN = None
try:
    import os

    DSN = os.environ.get("RN1X_TEST_DSN")
except Exception:                                              # noqa: BLE001
    DSN = None
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


@pg
@pytest.mark.asyncio
async def test_a3_evidence_is_persisted_read_back_and_expires():
    """THE WHOLE LOOP: record, read back with an age, and go stale.

    The adapter is absent here, so every venue read records UNREADABLE and the
    reconciliation does NOT pass -- which is the reconciliation working. What is
    proved is that the evidence is written, read back with its age and account
    identity, and refuses on each of its own named grounds.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           ON.RECONCILIATION_KEY)
        # NOTHING RECORDED IS NOT A PASS.
        none_ = await ON.reconciliation_evidence(conn, account_id="acct-a")
        assert none_["present"] is False
        assert none_["refusal"] == ON.R_NO_EVIDENCE

        rec = await ON.record_reconciliation(conn, account_id="acct-a",
                                            venue="PMUS_TEST_AUDIT",
                                            by="test")
        assert rec["account_id"] == "acct-a"
        assert rec["wrote_account_row"] is False
        assert rec["evidence_max_age_s"] == ON.EVIDENCE_MAX_AGE_S
        assert "chosen bound, not a derived one" in \
            rec["evidence_max_age_is_a_policy_allowance"]

        back = await ON.reconciliation_evidence(conn, account_id="acct-a")
        assert back["present"] is True
        assert back["age_s"] is not None and back["age_s"] < 60
        assert back["account_id"] == "acct-a"

        # A DIFFERENT ACCOUNT DOES NOT INHERIT IT.
        other = await ON.reconciliation_evidence(conn, account_id="acct-b")
        assert other["usable"] is False
        assert other["refusal"] == ON.R_EVIDENCE_OTHER_ACCOUNT
        assert "does not inherit" in other["why"]

        # AND IT EXPIRES.
        stale = await ON.reconciliation_evidence(
            conn, account_id="acct-a",
            now=time.time() + ON.EVIDENCE_MAX_AGE_S + 10.0)
        assert stale["usable"] is False
        assert stale["refusal"] == ON.R_EVIDENCE_STALE
        assert "no longer that instant" in stale["why"]
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           ON.RECONCILIATION_KEY)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a9_the_open_book_query_runs_and_carries_the_event_column():
    """THE QUERY ITSELF, AGAINST THE REAL SCHEMA. A join that does not parse is
    not a repair, and the column names here have been wrong before."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(L.OPEN_BOOK_SQL, "NO_SUCH_EXPERIMENT")
        assert rows == []
        # AND THE SHAPE IS RIGHT even on an empty result: ask the planner.
        prepared = await conn.prepare(L.OPEN_BOOK_SQL)
        names = [a.name for a in prepared.get_attributes()]
        assert "event_key" in names
        assert "event_key_resolved" in names
        assert "venue_market_slug" in names
    finally:
        await conn.close()
