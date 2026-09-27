"""DOES THE EXPOSURE READER REACH THE SUBMISSION PATH? Driven through it.

A NEW READER IS NOT CLOSURE. I built `bettor_account_exposure`, reported the
cross-lane gap addressed, and nothing consulted it. `authorize_submission` --
which IS the submission path -- did not take an exposure argument at all, so the
measurement existed and the gate could not see it.

So these tests do not call the reader. They call
`bettor_entry_execution.authorize_submission`, which is what a submission has to
pass, and they assert what it does with the evidence.

BOTH DIRECTIONS ARE TESTED, because "refuses in more cases" is not an acceptance
criterion:

    CORRECT CASES MUST PASS    an in-bound, in-date, right-account measurement
                               reaches `account_exposure_consumed: True` and the
                               only thing left refusing is the code constant.
    INCORRECT CASES MUST REFUSE  absent, unreadable, undated, stale, wrong-account
                               and over-cap each refuse BY THEIR OWN NAME, so a
                               caller can tell which one it was.

AND THE COUNTEREXAMPLES ARE REAL, not hypothetical: another lane's inventory
measured through the actual reader against a migrated database, and a write that
lands BETWEEN the read and the reservation.
"""

from __future__ import annotations

import contextlib
import os
import time

import asyncpg
import pytest

from sportsassets import bettor_account_exposure as AE
from sportsassets import bettor_entry_execution as EX

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct_enforce_1"
LIMITS = {"capital_usd": 100.0, "market_exposure_usd": 50.0,
          "daily_loss_stop_usd": 25.0, "position_hours_usd": 200.0,
          "event_exposure_usd": 50.0}


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


def _auth():
    return {"account_id": ACCT, "venue": "PMUS",
            "effective_digest": EX.effective_limits(LIMITS)["effective_digest"],
            "expires_at": time.time() + 3600.0}


def _gate(**kw):
    return EX.authorize_submission(account_id=ACCT, venue="PMUS",
                                   authorization=_auth(),
                                   approved_limits=LIMITS, **kw)


def _measured(total, *, account_id=ACCT, at=None):
    return {"account_id": account_id, "state": AE.TOTAL_MEASURED,
            "TOTAL_USD": float(total), "by_class": {},
            "measured_at_epoch_s": time.time() if at is None else at}


# ── 1 · the gate takes the argument at all ───────────────────────────

def test_the_submission_gate_accepts_and_requires_exposure_evidence():
    """THE DEFECT THIS CLOSES. `authorize_submission` had no exposure parameter,
    so the reader could not have been consulted however correct it was."""
    import inspect
    sig = inspect.signature(EX.authorize_submission)
    assert "account_exposure" in sig.parameters
    assert "proposed_cost_usd" in sig.parameters
    # AND OMITTING IT REFUSES. A default of "no evidence means fine" would have
    # been the same defect with a parameter added.
    got = _gate()
    assert got["ok"] is False
    assert got["refusal"] == EX.R_ACCOUNT_EXPOSURE_UNKNOWN
    assert got["account_exposure_consumed"] is False


def test_the_correct_case_PASSES_the_exposure_check():
    """CORRECT CASES MUST PASS. $40 committed, $25 proposed, $100 cap: the
    exposure check is satisfied and the only remaining refusal is the code
    constant. If this ever failed, the gate would be refusing everything and
    proving nothing."""
    got = _gate(account_exposure=_measured(40.0), proposed_cost_usd=25.0)
    assert got["account_exposure_consumed"] is True
    assert got["refusal"] == EX.R_SUBMISSION_DISABLED, (
        "the exposure check must be the thing that PASSED here")
    assert got["account_wide"]["would_be_usd"] == 65.0
    assert got["account_wide"]["cap_usd"] == 100.0


def test_exactly_at_the_cap_is_allowed_and_a_cent_over_is_not():
    """The boundary, both sides. An off-by-one here either blocks a legitimate
    order or admits one over the owner's cap."""
    at_cap = _gate(account_exposure=_measured(75.0), proposed_cost_usd=25.0)
    assert at_cap["refusal"] == EX.R_SUBMISSION_DISABLED
    over = _gate(account_exposure=_measured(75.01), proposed_cost_usd=25.0)
    assert over["refusal"] == EX.R_ACCOUNT_EXPOSURE_EXCEEDED


# ── 2 · each wrong case refuses BY ITS OWN NAME ──────────────────────

@pytest.mark.parametrize("evidence,expected", [
    (None, EX.R_ACCOUNT_EXPOSURE_UNKNOWN),
    ({"account_id": ACCT, "state": AE.TOTAL_UNREADABLE, "TOTAL_USD": None,
      "unreadable_required_paths": ["VENUE_HELD_POSITIONS"],
      "measured_at_epoch_s": 0},
     EX.R_ACCOUNT_EXPOSURE_UNREADABLE),
    ({"account_id": "acct_someone_else", "state": AE.TOTAL_MEASURED,
      "TOTAL_USD": 0.0, "measured_at_epoch_s": 0},
     EX.R_ACCOUNT_EXPOSURE_OTHER_ACCOUNT),
    ({"account_id": ACCT, "state": AE.TOTAL_MEASURED, "TOTAL_USD": 0.0},
     EX.R_ACCOUNT_EXPOSURE_STALE),
])
def test_each_failure_mode_has_its_own_refusal(evidence, expected):
    """COLLAPSING THESE WOULD HIDE WHICH ONE FAILED, and they need different
    responses: supply the evidence, fix the credential, use the right account,
    re-measure."""
    kw = {} if evidence is None else {"account_exposure": evidence}
    got = _gate(**kw)
    assert got["ok"] is False
    assert got["refusal"] == expected
    assert got["account_exposure_consumed"] is False


def test_an_hour_old_measurement_is_a_measurement_about_an_hour_ago():
    got = _gate(account_exposure=_measured(0.0, at=time.time() - 3600))
    assert got["refusal"] == EX.R_ACCOUNT_EXPOSURE_STALE
    assert got["exposure_evidence_age_s"] > 3000


def test_a_negative_age_is_refused_rather_than_trusted():
    """A measurement stamped in the future means the clocks disagree. Trusting
    it would make a stale reading look fresh by exactly the skew."""
    got = _gate(account_exposure=_measured(0.0, at=time.time() + 600))
    assert got["refusal"] == EX.R_ACCOUNT_EXPOSURE_STALE
    assert "clocks disagree" in got["why"]


def test_an_unreadable_total_is_not_read_as_zero():
    """THE WHOLE POINT OF FAILING CLOSED. An unmeasured account is not an empty
    one, and the venue read is the path normally missing."""
    got = _gate(account_exposure={
        "account_id": ACCT, "state": AE.TOTAL_UNREADABLE, "TOTAL_USD": None,
        "unreadable_required_paths": ["VENUE_HELD_POSITIONS"],
        "measured_at_epoch_s": time.time()}, proposed_cost_usd=1.0)
    assert got["refusal"] == EX.R_ACCOUNT_EXPOSURE_UNREADABLE
    assert got["unreadable_paths"] == ["VENUE_HELD_POSITIONS"]


def test_the_exposure_check_runs_BEFORE_the_code_constant():
    """ORDERING MATTERS. If `R_SUBMISSION_DISABLED` came first it would mask a
    cap breach, and the day submission is enabled the mask would lift with the
    breach still there."""
    over = _gate(account_exposure=_measured(200.0), proposed_cost_usd=25.0)
    assert over["refusal"] == EX.R_ACCOUNT_EXPOSURE_EXCEEDED, (
        "a cap breach must not be reported as 'submission is disabled'")


def test_an_unauthorized_caller_is_told_that_first():
    """And the ordering the other way: someone with no authorization should not
    be sent to fetch exposure evidence they have no use for."""
    got = EX.authorize_submission(account_id=ACCT, venue="PMUS",
                                  authorization=None, approved_limits=LIMITS)
    assert got["refusal"] == EX.R_NO_AUTHORIZATION


# ── 3 · the counterexamples, through the real reader ─────────────────

@pg
@pytest.mark.asyncio
async def test_another_lanes_inventory_refuses_this_lanes_order():
    """COUNTEREXAMPLE ONE. This lane holds nothing. Another path holds $95 on the
    same account. A gate reading only this lane's rows sees a clear $100 of
    headroom and admits a $25 order; the account-wide read refuses it.

    The other path here is `live_orders`, which carries both the legacy copier
    and -- since migration 014 -- the manual sleeve. Neither is constrained by
    this lane's one-live-intent unique index.
    """
    async with _conn() as conn:
        await conn.execute("DELETE FROM live_orders")
        await conn.execute(
            "INSERT INTO live_orders (asset, side, his_price, limit_price,"
            " requested_usd, requested_shares, status, filled_usd,"
            " filled_shares) "
            "VALUES ('other-lane','BUY',0.5,0.55,95,190,'filled',95,190)")
        ex = await AE.account_exposure(
            conn, account_id=ACCT,
            venue_positions={"held_usd": 0.0, "working_usd": 0.0,
                             "unresolved_usd": 0.0})
        assert ex["state"] == AE.TOTAL_MEASURED
        assert ex["paths"]["THIS_LANE"][AE.HELD] == 0.0, (
            "this lane holds nothing, which is why a lane-local check passes")
        assert ex["TOTAL_USD"] == 95.0

        got = _gate(account_exposure=ex, proposed_cost_usd=25.0)
        assert got["refusal"] == EX.R_ACCOUNT_EXPOSURE_EXCEEDED
        assert got["account_wide"]["already_committed_usd"] == 95.0
        await conn.execute("DELETE FROM live_orders")


@pg
@pytest.mark.asyncio
async def test_a_write_between_the_read_and_the_reservation_is_not_covered():
    """COUNTEREXAMPLE TWO, AND IT IS A LIMITATION RATHER THAN A PASS.

    The exposure read happens before the reservation. Another writer can land in
    between, and this test does exactly that: measure, then insert, then show the
    gate still authorising against the OLD number.

    THAT IS NOT A BUG IN THE GATE -- it is what a measurement is. The test exists
    so the limitation is demonstrated rather than described, and so the two cases
    are kept apart:

      ANOTHER LANE IN THIS DATABASE   controllable, by taking the read and the
          insert in ONE transaction holding the account row. The gate cannot do
          that itself -- it is synchronous and holds no connection -- so the
          requirement falls on the caller and is asserted below.
      ANOTHER CREDENTIAL AT THE VENUE  NOT controllable from our side at all.
          No lock of ours is visible to it. Isolation is the only sound answer.
    """
    async with _conn() as conn:
        await conn.execute("DELETE FROM live_orders")
        ex = await AE.account_exposure(
            conn, account_id=ACCT,
            venue_positions={"held_usd": 0.0, "working_usd": 0.0,
                             "unresolved_usd": 0.0})
        assert ex["TOTAL_USD"] == 0.0

        # ANOTHER WRITER, AFTER THE READ.
        await conn.execute(
            "INSERT INTO live_orders (asset, side, his_price, limit_price,"
            " requested_usd, requested_shares, status, filled_usd,"
            " filled_shares) "
            "VALUES ('race','BUY',0.5,0.55,95,190,'filled',95,190)")

        # THE GATE STILL SEES THE OLD NUMBER, and authorises on it.
        stale_pass = _gate(account_exposure=ex, proposed_cost_usd=25.0)
        assert stale_pass["account_exposure_consumed"] is True, (
            "this is the limitation being demonstrated: a measurement is about "
            "the past, and nothing in the gate can change that")

        # RE-MEASURING SEES IT, which is what makes the one-transaction
        # requirement the actual control rather than advice.
        fresh = await AE.account_exposure(
            conn, account_id=ACCT,
            venue_positions={"held_usd": 0.0, "working_usd": 0.0,
                             "unresolved_usd": 0.0})
        assert fresh["TOTAL_USD"] == 95.0
        assert _gate(account_exposure=fresh,
                     proposed_cost_usd=25.0)["refusal"] == (
            EX.R_ACCOUNT_EXPOSURE_EXCEEDED)
        await conn.execute("DELETE FROM live_orders")


def test_the_two_concurrency_cases_are_stated_not_conflated():
    """One is controllable and one is not, and a design that treats them alike
    either over-promises on the second or under-uses the first."""
    lim = EX.CONCURRENT_WRITER_LIMITATION
    lane = lim["another_lane_in_this_database"]
    venue = lim["another_credential_at_the_venue"]
    assert lane["controllable"] is True
    assert "ONE transaction" in lane["how"]
    assert "the CALLER" in lane["who_must_do_it"]
    assert venue["controllable"] is False
    assert "no shared enforcement boundary" in venue["why"].lower() or \
        "no shared enforcement boundary" in venue["why"]
    assert "ISOLATION" in venue["the_only_sound_answer"]
    assert "NOT_DEMONSTRATED" in venue["the_only_sound_answer"]
    assert "measurement of the past" in venue["what_must_not_be_done"]


def test_the_evidence_age_limit_is_labelled_a_policy_assumption():
    assert EX.MAX_EXPOSURE_EVIDENCE_AGE_S == 60.0
    src = __import__("inspect").getsource(EX)
    i = src.index("MAX_EXPOSURE_EVIDENCE_AGE_S")
    window = src[max(0, i - 600):i]
    assert "POLICY ASSUMPTION" in window.upper()
    assert "chosen, not derived" in window


# ── 4 · the isolation route, since enforcement has a hole ────────────

def test_isolation_is_the_named_answer_for_the_uncontrollable_case():
    """Where a shared enforcement boundary cannot exist, the instruction is to
    say so and implement isolation. The requirement is stated and the verdict is
    NOT_DEMONSTRATED -- not quietly assumed."""
    got = AE.isolation_evidence()
    assert got["verdict"] == AE.ISOLATION_NOT_DEMONSTRATED
    ids = {r["id"] for r in AE.ISOLATION_REQUIREMENTS}
    assert "NO_OTHER_CREDENTIAL" in ids
    assert "NO_MANUAL_ACCESS" in ids
