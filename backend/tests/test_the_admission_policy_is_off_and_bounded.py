"""THE POLICY ADMISSION IS OFF, AND IT CANNOT PRETEND TO BE A MEASUREMENT.

WHY THIS MODULE EXISTS AT ALL. Under the most favourable observation the venue can
produce -- `Age: 0`, an in-bound 304, a heartbeat one second old, a full-book
message one second old -- `bettor_venue_currency.evaluate` returns
`BOOK_CURRENCY_NOT_ESTABLISHED` and `admits()` is `False`. Zero candidates can be
admitted, because the venue publishes no market-data timing guarantee.

So there are two ways past it: the venue documents its timing, or the OWNER
accepts an explicit assumption in its place. The second is `bettor_admission_
policy`, and these tests exist to make sure it is the second thing and not a
weakened gate wearing its clothes.

THE FOUR PROPERTIES THAT MAKE IT LEGITIMATE RATHER THAN A LOOPHOLE:

  IT IS OFF                     `POLICY_ADMISSION_ENABLED` is False, and a signed
                                record alone starts nothing.
  IT DOES NOT TOUCH THE GATE    `bettor_venue_currency` still says
                                NOT_ESTABLISHED and still refuses.
  IT NEVER CLAIMS MEASUREMENT   the basis token carries NOT_A_MEASUREMENT.
  THE SIGNATURE IS REAL         the assumption text must be echoed verbatim; a
                                signature against a summary is refused.
"""

from __future__ import annotations

import time

import pytest

from sportsassets import bettor_admission_policy as AP
from sportsassets import bettor_stream_currency as SC
from sportsassets import bettor_venue_currency as VC


#: THE AUTHENTICATED OWNER. A bare `accepted_by` string is no longer accepted --
#: echoing a sentence verbatim is not authentication, and neither is naming
#: yourself, so the record is built only from a principal the SERVER
#: authenticated. `ACCOUNT` and `INSTRUMENTS` are the other scopes the
#: acceptance is bound to.
PRINCIPAL = AP.authenticated_principal(
    subject="owner@example", authenticated_by="COMMAND_SESSION",
    authenticated_at=1_000_000_000.0)
ACCOUNT = "acct_under_test"
INSTRUMENTS = ("aec-x", "aec-y")


def _accepted(**kw):
    base = dict(principal=PRINCIPAL, echoed_assumption=AP.THE_ASSUMPTION,
                venue="PMUS", account_id=ACCOUNT, instruments=INSTRUMENTS,
                policy_version=AP.POLICY_VERSION)
    base.update(kw)
    return AP.acceptance_record(**base)


def _all_other_requirements():
    """EVERY REQUIREMENT THE EXCEPTION CANNOT WAIVE, satisfied. Supplied so the
    tests below isolate the POLICY bounds; a test that wants to see the
    waives-nothing-else refusal drops one on purpose."""
    return {k: True for k in AP.REQUIREMENTS_IT_CANNOT_WAIVE}


def _admit_kw(**kw):
    base = dict(venue="PMUS", presenting_principal=PRINCIPAL,
                account_id=ACCOUNT, instrument=INSTRUMENTS[0],
                policy_version=AP.POLICY_VERSION, intent=AP.INTENT_NEW,
                other_requirements=_all_other_requirements())
    base.update(kw)
    return base


def _live_sub(now, *, msg_age=1.0, silence=1.0):
    return {"last_update_at": now - msg_age, "alive_at": now - silence,
            "silence_s": silence}


# ── 1 · the blocking fact this module answers ────────────────────────

def test_the_best_possible_observation_still_does_not_admit():
    """THE FACT THAT MAKES THIS MODULE THE ONLY ROUTE. Everything favourable at
    once, and the answer is still no."""
    import email.utils
    now = time.time()
    SC.connection_opened(now=now - 10)
    SC.heartbeat(now=now - 1)
    SC.message_received("aec-x", now=now - 1, payload_slug="aec-x")
    sub = SC.evidence_for("aec-x", now=now)["subscription"]
    v = VC.evaluate(
        now=now,
        observation={"headers": {
            "date": email.utils.formatdate(now, usegmt=True), "age": "0",
            "last-modified": email.utils.formatdate(now, usegmt=True),
            "cache-control": "public, max-age=30"}},
        subscription=sub,
        revalidation={"status": 304, "date_epoch_s": now},
        venue_ts=now, our_receipt_at=now)
    assert v["verdict"] == VC.NOT_ESTABLISHED
    assert VC.admits(v) is False
    assert v["mechanism"] == VC.NO_MECHANISM
    assert SC.MISSING_PRECONDITIONS == (SC.P5_DOCUMENTED_TIMING,)


# ── 2 · it is off ────────────────────────────────────────────────────

def test_it_is_disabled_in_the_shipped_build():
    assert AP.POLICY_ADMISSION_ENABLED is False
    a = AP.available()
    assert a["enabled_in_code"] is False
    assert a["refusal"] == AP.R_NOT_ENABLED
    assert "code change that goes through the gate" in a["why"]


def test_a_signed_record_alone_starts_nothing():
    """TWO THINGS MUST BOTH BE TRUE. Same shape as
    REAL_ORDER_SUBMISSION_ENABLED, for the same reason: a record is not a
    release."""
    rec = _accepted()
    assert rec["ok"] is True
    now = time.time()
    got = AP.admits(**_admit_kw(record=rec, subscription=_live_sub(now),
                               proposed_cost_usd=10.0, now=now))
    assert got["ok"] is False
    assert got["refusal"] == AP.R_NOT_ENABLED
    assert "two_things_must_both_be_true" in AP.describe()


def test_it_does_not_touch_the_freshness_gate():
    """A weakened gate would move the bound or add a token. Neither happened."""
    assert VC.ESTABLISHING_MECHANISMS == (VC.M1_LIVE_SUBSCRIPTION,)
    assert AP.BASIS_OWNER_POLICY not in VC.ESTABLISHING_MECHANISMS
    assert AP.BASIS_OWNER_POLICY not in VC.PARTIAL_MECHANISMS
    assert VC.MAX_BOOK_STATE_AGE_S == 30.0, "the measured bound is untouched"
    d = AP.describe()
    assert "still returns" in d["what_this_is_not"]
    assert "NOT_ESTABLISHED" in d["what_this_is_not"]


def test_the_basis_token_can_never_read_as_a_measurement():
    """It goes into logs and records. `NOT_A_MEASUREMENT` is in the token itself
    so no reader has to know the convention."""
    assert "NOT_A_MEASUREMENT" in AP.BASIS_OWNER_POLICY
    assert "ESTABLISHED" not in AP.BASIS_OWNER_POLICY
    now = time.time()
    got = AP.admits(**_admit_kw(record=_accepted(),
                               subscription=_live_sub(now),
                               proposed_cost_usd=10.0, now=now))
    assert got["basis"] == AP.BASIS_OWNER_POLICY
    assert got["this_is_not_a_measurement"] is True
    assert got["the_currency_verdict_is_still"] == "BOOK_CURRENCY_NOT_ESTABLISHED"


# ── 3 · the signature has to be real ─────────────────────────────────

def test_a_signature_against_a_summary_is_REFUSED():
    """The value of the record is that whoever signed it read what they were
    accepting. A paraphrase is not that."""
    for bad in ("I accept the freshness risk.",
                AP.THE_ASSUMPTION[:120],
                AP.THE_ASSUMPTION.replace("POLICY DECISION", "measurement"),
                "", None):
        got = _accepted(echoed_assumption=bad)
        assert got["ok"] is False, bad
        assert got["refusal"] == AP.R_TEXT_MISMATCH


def test_the_verbatim_text_is_accepted_and_names_what_is_given_up():
    got = _accepted()
    assert got["ok"] is True
    a = got["assumption"]
    # THE CONCLUSION AT ITS REAL WIDTH: our predicate rejects this path. NOT
    # "the venue has no route" and NOT "nothing can be engineered".
    assert "under the current evidence requirements" in a
    assert "does not qualify" in a
    assert "transactTime field is unresolved" in a
    assert "not as a measurement" in a
    # AND THE UNBOUNDED UPSTREAM AGE, which is the whole of the risk and which
    # the five-second window does not touch.
    assert "ENTIRELY UNBOUNDED" in a
    assert "limits only the delay WE add" in a
    assert "ten-minute-old book received one second ago is still ten minutes" in a
    # AND THE UNDETECTABILITY, which is the part that is easy to leave out.
    assert "indistinguishable from a fresh one" in a
    assert "detected only afterwards" in a


def test_ECHOING_A_SENTENCE_VERBATIM_IS_NOT_AUTHENTICATION():
    """THE CORRECTION THIS TEST REPLACES.

    It used to assert that an acceptance must NAME who accepted it, and that was
    the whole of the identity check. But a name is a string the caller chose, and
    so is a verbatim echo of THE_ASSUMPTION -- anything able to call this
    function can supply both, so between them they authenticated nobody.

    The record is now built only from a principal the SERVER authenticated, and
    `accepted_by` is DERIVED from it rather than accepted alongside it.
    """
    # A bare name, with or without the verbatim text, is refused as an
    # AUTHENTICATION failure -- not as a missing field.
    assert _accepted(principal=None, accepted_by="owner@example")["refusal"] == (
        AP.R_PRINCIPAL_UNVERIFIED)
    assert _accepted(principal=None)["refusal"] == AP.R_NO_PRINCIPAL

    # A principal missing any of its three parts is not a principal.
    for bad in ({"subject": "o"},
                {"subject": "o", "authenticated_by": "CMD"},
                {"subject": "", "authenticated_by": "CMD",
                 "authenticated_at": 1.0},
                {"subject": "o", "authenticated_by": "",
                 "authenticated_at": 1.0},
                {"subject": "o", "authenticated_by": "CMD",
                 "authenticated_at": None},
                "owner@example", 42, ()):
        assert _accepted(principal=bad)["refusal"] == AP.R_NO_PRINCIPAL, bad

    # AND `accepted_by` IS DERIVED, so it cannot disagree with the principal.
    rec = _accepted(accepted_by="somebody-else-entirely")
    assert rec["ok"] is True
    assert rec["accepted_by"] == PRINCIPAL["subject"]
    assert rec["principal"]["authenticated_by"] == "COMMAND_SESSION"


@pytest.mark.parametrize("kw,expected", [
    ({"account_id": None}, AP.R_NO_ACCOUNT),
    ({"account_id": "   "}, AP.R_NO_ACCOUNT),
    ({"instruments": ()}, AP.R_NO_INSTRUMENTS),
    ({"instruments": ("", "  ")}, AP.R_NO_INSTRUMENTS),
    ({"policy_version": None}, AP.R_POLICY_VERSION),
    ({"policy_version": "ADMISSION_POLICY_V1"}, AP.R_POLICY_VERSION),
])
def test_the_record_must_be_BOUND_to_every_scope(kw, expected):
    """An acceptance with no account, no instrument set or a different policy
    version is not a bounded exception, and each gap refuses by its own name."""
    assert _accepted(**kw)["refusal"] == expected


def test_a_record_carries_every_binding_and_says_what_it_cannot_waive():
    rec = _accepted()
    assert rec["account_id"] == ACCOUNT
    assert rec["instruments"] == tuple(sorted(INSTRUMENTS))
    assert rec["policy_version"] == AP.POLICY_VERSION
    assert rec["revoked"] is False and rec["revoked_at"] is None
    assert rec["expires_at"] > rec["accepted_at"]
    bound = " ".join(rec["what_it_is_bound_to"])
    for scope in ("principal", "account", "venue", "instruments",
                  "policy version", "expiry", "revocation"):
        assert scope in bound, scope
    assert rec["and_what_it_still_cannot_waive"] == list(
        AP.REQUIREMENTS_IT_CANNOT_WAIVE)


# ── 3b · the bindings are enforced at admission, not just stored ─────

@pytest.mark.parametrize("kw,expected", [
    ({"presenting_principal": None}, AP.R_PRINCIPAL_UNVERIFIED),
    ({"presenting_principal": AP.authenticated_principal(
        subject="someone-else", authenticated_by="COMMAND_SESSION",
        authenticated_at=1.0)}, AP.R_PRINCIPAL_MISMATCH),
    ({"presenting_principal": AP.authenticated_principal(
        subject="owner@example", authenticated_by="A_DIFFERENT_MECHANISM",
        authenticated_at=1.0)}, AP.R_PRINCIPAL_MISMATCH),
    ({"account_id": "acct_someone_else"}, AP.R_ACCOUNT_MISMATCH),
    ({"instrument": "not-in-the-set"}, AP.R_INSTRUMENT_NOT_COVERED),
    ({"policy_version": "ADMISSION_POLICY_V1"}, AP.R_POLICY_VERSION),
    ({"intent": "SOMETHING_ELSE"}, AP.R_UNKNOWN_INTENT),
    ({"intent": None}, AP.R_UNKNOWN_INTENT),
])
def test_each_binding_refuses_at_admission_by_its_own_name(monkeypatch, kw,
                                                          expected):
    """A report must never say only 'refused'. Each scope that failed says so."""
    monkeypatch.setattr(AP, "POLICY_ADMISSION_ENABLED", True)
    now = time.time()
    got = AP.admits(**_admit_kw(record=_accepted(now=now),
                                subscription=_live_sub(now),
                                proposed_cost_usd=10.0, now=now, **kw))
    assert got["ok"] is False
    assert got["refusal"] == expected, got


def test_the_exception_CANNOT_WAIVE_ANYTHING_ELSE(monkeypatch):
    """PROVED BY ENFORCEMENT, not by a sentence. Every requirement outside this
    exception's scope must be explicitly True, so UNKNOWN IS A REFUSAL and the
    exception can never be the sole authority for an order."""
    monkeypatch.setattr(AP, "POLICY_ADMISSION_ENABLED", True)
    now = time.time()

    def _go(reqs):
        return AP.admits(**_admit_kw(record=_accepted(now=now),
                                     subscription=_live_sub(now),
                                     proposed_cost_usd=10.0, now=now,
                                     other_requirements=reqs))

    # Nothing supplied at all.
    for empty in (None, {}):
        got = _go(empty)
        assert got["refusal"] == AP.R_OTHER_REQUIREMENT_NOT_MET
        assert set(got["requirements_not_met"]) == set(
            AP.REQUIREMENTS_IT_CANNOT_WAIVE)

    # EACH ONE ALONE IS ENOUGH TO BLOCK, and it is named.
    for missing in AP.REQUIREMENTS_IT_CANNOT_WAIVE:
        reqs = _all_other_requirements()
        for value in (False, None, "yes", 1):
            reqs[missing] = value
            got = _go(reqs)
            assert got["refusal"] == AP.R_OTHER_REQUIREMENT_NOT_MET, (
                missing, value)
            assert got["requirements_not_met"] == [missing], (missing, value)
        del reqs[missing]
        assert _go(reqs)["requirements_not_met"] == [missing]

    # The named list covers settlement, calibration, identity, accounting,
    # exposure and execution -- each one the user asked to see kept separate.
    named = " ".join(AP.REQUIREMENTS_IT_CANNOT_WAIVE)
    for topic in ("SETTLEMENT", "CALIBRATION", "IDENTITY", "ACCOUNTING",
                  "EXPOSURE", "EXECUTION", "RISK_RAILS", "AUTHORIZATION"):
        assert topic in named, topic


@pytest.mark.parametrize("break_it", ["expire", "revoke"])
def test_expiry_and_revocation_STOP_NEW_EXPOSURE_AND_PRESERVE_SERVICING(
        monkeypatch, break_it):
    """THE ASYMMETRY, AND WHY IT MATTERS. A lapsed or revoked assumption must
    stop us OPENING anything new. It must NOT stop us cancelling, exiting or
    settling what is already held -- freezing servicing would convert an expiry
    into trapped capital, which is worse than the risk the expiry ends."""
    monkeypatch.setattr(AP, "POLICY_ADMISSION_ENABLED", True)
    now = time.time()
    rec = _accepted(now=now)
    if break_it == "expire":
        rec = dict(rec, expires_at=now - 1.0)
        expected, flag = AP.R_EXPIRED, "expired_but_servicing_is_permitted"
    else:
        rec = AP.revoke(rec, by=PRINCIPAL, now=now)["record"]
        expected, flag = AP.R_REVOKED, "revoked_but_servicing_is_permitted"

    new = AP.admits(**_admit_kw(record=rec, subscription=_live_sub(now),
                                proposed_cost_usd=10.0, now=now,
                                intent=AP.INTENT_NEW))
    assert new["ok"] is False
    assert new["refusal"] == expected

    svc = AP.admits(**_admit_kw(record=rec, subscription=_live_sub(now),
                                proposed_cost_usd=10.0, now=now,
                                intent=AP.INTENT_SERVICING))
    assert svc["ok"] is True, svc
    assert svc[flag] is True


def test_a_revocation_also_needs_an_authenticated_principal():
    assert AP.revoke(_accepted(), by=None)["refusal"] == AP.R_NO_PRINCIPAL
    assert AP.revoke(_accepted(), by="someone")["refusal"] == AP.R_NO_PRINCIPAL
    ok = AP.revoke(_accepted(), by=PRINCIPAL, now=5.0)
    assert ok["ok"] is True
    assert ok["record"]["revoked_at"] == 5.0
    assert ok["record"]["revoked_by"]["subject"] == PRINCIPAL["subject"]
    assert "trapped capital" in ok["record"]["and_servicing_is_unaffected"]


def test_SERVICING_does_not_waive_the_other_requirements_either(monkeypatch):
    """Servicing survives expiry. It does not survive a missing settlement or
    accounting requirement -- the asymmetry is about the ASSUMPTION's lifetime,
    not a second, looser gate."""
    monkeypatch.setattr(AP, "POLICY_ADMISSION_ENABLED", True)
    now = time.time()
    rec = dict(_accepted(now=now), expires_at=now - 1.0)
    reqs = _all_other_requirements()
    reqs["EXECUTION_ACCOUNTING"] = False
    got = AP.admits(**_admit_kw(record=rec, subscription=_live_sub(now),
                                proposed_cost_usd=10.0, now=now,
                                intent=AP.INTENT_SERVICING,
                                other_requirements=reqs))
    assert got["refusal"] == AP.R_OTHER_REQUIREMENT_NOT_MET
    assert got["requirements_not_met"] == ["EXECUTION_ACCOUNTING"]


def test_an_absent_record_is_a_refusal_not_a_default(monkeypatch):
    monkeypatch.setattr(AP, "POLICY_ADMISSION_ENABLED", True)
    now = time.time()
    for rec in (None, {}, {"accepted_by": ""}):
        got = AP.admits(**_admit_kw(record=rec,
                                   subscription=_live_sub(now),
                                   proposed_cost_usd=10.0, now=now))
        assert got["refusal"] == AP.R_NO_RECORD
        assert "not a default" in got["why"]


# ── 4 · the bounds, all enforced, none widenable ─────────────────────

@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(AP, "POLICY_ADMISSION_ENABLED", True)


def test_the_correct_case_ADMITS_under_the_policy(on):
    """CORRECT CASES MUST PASS. If this failed the module would be refusing
    everything and proving nothing."""
    now = time.time()
    got = AP.admits(**_admit_kw(record=_accepted(),
                               subscription=_live_sub(now),
                               proposed_cost_usd=20.0, now=now))
    assert got["ok"] is True, got
    assert got["refusal"] is None
    assert got["accepted_by"] == "owner@example"
    assert "POLICY ASSUMPTION, not under a measurement" in got["why"]


def test_the_owner_may_tighten_and_never_widen():
    """Same rule the approved limits already use, so accepting with a LARGER
    number than proposed changes nothing."""
    loose = _accepted(bounds={"max_single_order_usd": 10_000.0,
                              "max_our_processing_delay_s": 300.0})
    assert loose["bounds"]["max_single_order_usd"] == (
        AP.PROPOSED_BOUNDS["max_single_order_usd"])
    assert loose["bounds"]["max_our_processing_delay_s"] == (
        AP.PROPOSED_BOUNDS["max_our_processing_delay_s"])
    assert loose["tightened_by_the_owner"] == {}
    tight = _accepted(bounds={"max_single_order_usd": 5.0})
    assert tight["bounds"]["max_single_order_usd"] == 5.0
    assert tight["tightened_by_the_owner"] == {"max_single_order_usd": 5.0}


def test_the_window_BOUNDS_OUR_DELAY_AND_NOT_THE_BOOKS_AGE():
    """THE CORRECTION. I proposed tightening this window from 30 s to 5 s and
    described it as reducing 'exposure to being wrong'. It does not. It measures
    the gap between OUR RECEIPT of a message and OUR DECISION on it, so it bounds
    our own contribution to the delay and NOTHING about the age of the snapshot
    inside the message. The bound is kept -- a processing delay is worth bounding
    on its own account -- and it is named for what it actually bounds."""
    assert AP.PROPOSED_BOUNDS["max_our_processing_delay_s"] == 5.0
    # The old name is GONE, so no caller can read it as a book age again.
    assert "max_message_age_s" not in AP.PROPOSED_BOUNDS
    assert "delay WE add after receiving" in AP.PROPOSED_BOUNDS["what_this_bounds"]
    assert "NOTHING about the age of the book" in (
        AP.PROPOSED_BOUNDS["what_this_bounds"])
    # AND THE UPSTREAM AGE IS SAID TO BE UNBOUNDED, not merely left unmentioned.
    nb = AP.PROPOSED_BOUNDS["what_this_does_NOT_bound"]
    assert "ENTIRELY UNBOUNDED" in nb
    assert "tightening this number from 30 to 5 does not change that" in nb
    # The 30 s venue-currency bound is NOT the thing this is tighter than. It
    # applies to a different quantity, so the comparison is not drawn.
    assert VC.MAX_BOOK_STATE_AGE_S == 30.0


def test_the_refusal_is_named_for_OUR_delay_with_the_old_name_kept_as_an_alias():
    """Renaming a refusal silently would break readers of the old constant. The
    new name is the truthful one; the old one remains as an alias to the SAME
    string so nothing is now called a message-age refusal by mistake."""
    assert AP.R_OUR_DELAY_TOO_LONG == AP.R_MESSAGE_TOO_OLD
    assert "DELAY" in AP.R_OUR_DELAY_TOO_LONG


@pytest.mark.parametrize("kw,expected", [
    ({"subscription": None}, AP.R_NO_SUBSCRIPTION),
    ({"subscription": {"last_update_at": None, "alive_at": None}},
     AP.R_SILENT),
    ({"silence": 60.0}, AP.R_SILENT),
    ({"msg_age": 30.0}, AP.R_MESSAGE_TOO_OLD),
    ({"proposed_cost_usd": 40.0}, AP.R_OVER_ORDER_CAP),
    ({"pilot_total_usd": 95.0}, AP.R_OVER_PILOT_CAP),
    ({"venue": "SOMEWHERE_ELSE"}, AP.R_VENUE),
])
def test_each_bound_refuses_by_its_own_name(on, kw, expected):
    now = time.time()
    sub_kw = {k: kw.pop(k) for k in ("msg_age", "silence") if k in kw}
    sub = kw.pop("subscription", _live_sub(now, **sub_kw))
    call = _admit_kw(record=_accepted(), subscription=sub,
                     proposed_cost_usd=10.0, now=now)
    call.update(kw)
    got = AP.admits(**call)
    assert got["ok"] is False
    assert got["refusal"] == expected, got


def test_the_acceptance_expires_and_must_be_re_signed(on):
    """An assumption accepted on one day's evidence is not accepted
    indefinitely."""
    now = time.time()
    rec = _accepted(now=now - 25 * 3600)
    got = AP.admits(**_admit_kw(record=rec, subscription=_live_sub(now),
                               proposed_cost_usd=10.0, now=now))
    assert got["refusal"] == AP.R_EXPIRED
    assert "lapsed and must be re-signed" in got["why"]


def test_a_revoked_acceptance_refuses_distinctly_from_an_expired_one(on):
    """They need different responses: re-sign, or stop."""
    now = time.time()
    rec = dict(_accepted(), revoked=True)
    got = AP.admits(**_admit_kw(record=rec, subscription=_live_sub(now),
                               proposed_cost_usd=10.0, now=now))
    assert got["refusal"] == AP.R_REVOKED


def test_changing_the_assumption_text_invalidates_old_signatures(on):
    """If the risk being accepted changes, the old signature does not cover it."""
    now = time.time()
    rec = dict(_accepted(), assumption="something I signed last week")
    got = AP.admits(**_admit_kw(record=rec, subscription=_live_sub(now),
                               proposed_cost_usd=10.0, now=now))
    assert got["refusal"] == AP.R_TEXT_MISMATCH
    assert "needs a new signature" in got["why"]


# ── 5 · what it does not excuse ──────────────────────────────────────

def test_it_covers_TIMING_ONLY_and_says_what_it_does_not_excuse():
    """A policy that quietly excused a missing book or the wrong instrument
    would be a very different thing from one that accepts a timing risk."""
    excl = AP.describe()["what_the_policy_does_NOT_excuse"]
    joined = " ".join(excl)
    assert "missing book (P1)" in joined
    assert "wrong instrument (P4)" in joined
    assert "carried across a disconnect (P3)" in joined
    assert "unreadable account-wide exposure" in joined
    assert "unreconciled account" in joined
    assert "code constant on real order submission" in joined


def test_a_missing_subscription_is_refused_even_with_a_valid_signature(on):
    """The clearest case of the above: the owner accepted a TIMING risk, not a
    licence to trade without a book."""
    now = time.time()
    got = AP.admits(**_admit_kw(record=_accepted(), subscription=None,
                               proposed_cost_usd=10.0, now=now))
    assert got["refusal"] == AP.R_NO_SUBSCRIPTION
    assert "TIMING only" in got["why"]
    assert "does not excuse a missing book" in got["why"]


def test_how_the_risk_is_detected_is_stated_because_it_is_not_obvious():
    """A stale book is indistinguishable from a fresh one at decision time, so
    watching cannot find this. Only reconciling fills against expected prices
    can -- which is why that comparison is a release condition."""
    d = AP.describe()["how_the_risk_is_detected"]
    assert "not by watching" in d
    assert "indistinguishable" in d
    assert "comparing fills against the prices" in d
    assert "release condition" in d
