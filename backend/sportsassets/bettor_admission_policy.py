"""A DISABLED POLICY EXCEPTION. Not a freshness repair, and not the only route.

WHAT THE EVIDENCE ACTUALLY SHOWS, STATED AT ITS REAL WIDTH.

    UNDER THE CURRENT EVIDENCE REQUIREMENTS, THIS MARKET-DATA PATH DOES NOT
    QUALIFY.

That is what the test demonstrates: the admission predicate as it stands rejects
the observations this path supplies. It is a fact about our predicate applied to
this path.

WHAT IT DOES NOT SHOW, AND I CLAIMED BOTH:

  * that nothing can be engineered. It does not. Other data paths, other
    endpoints, a different subscription type, a vendor feed with its own
    contract, or a predicate built on a quantity we have not yet identified are
    all untested rather than excluded.
  * that new venue documentation is the only possible future route. It is not.
    Documentation is the route I can NAME; naming one route is not enumerating
    them.

I wrote "nothing I can build unblocks today" and "the venue publishes no timing
guarantee, so a credential does not unblock today". The first half of each was a
statement about my current predicate presented as a statement about the world.

SO THIS MODULE IS A POLICY EXCEPTION, KEPT SEPARATE AND DISABLED. It is not
listed as a mechanism, not a completed repair, and not the last word on market
data. `market_data_design` holds the assessment; this holds one bounded exception
the owner may or may not want, and the freshness work continues either way.

AND THE LOCAL WINDOW IS NOT A BOUND ON UPSTREAM AGE -- WHICH I ALSO GOT WRONG.

I proposed tightening the window from 30 s to 5 s and described that as reducing
"exposure to being wrong". It does not do that. `max_message_age_s` measures the
gap between OUR RECEIPT of a message and OUR DECISION on it. Shortening it
reduces the delay we add after receipt and bounds nothing at all about the age of
the snapshot inside that message.

    IF THE VENUE HANDS US A TEN-MINUTE-OLD BOOK, RECEIVING IT ONE SECOND AGO AND
    DECIDING WITHIN FIVE SECONDS LEAVES IT TEN MINUTES OLD.

That is the receipt-instant error I withdrew earlier in this repository, and I
reintroduced it inside the very bounds meant to contain the risk. The window is
kept because a processing delay is worth bounding on its own account, and it is
now labelled for what it bounds. THE UPSTREAM AGE REMAINS ENTIRELY UNBOUNDED
under this exception, and that is the whole of the risk being accepted.

WHAT THE OWNER WOULD BE ACCEPTING, THEREFORE. Not "a book at most five seconds
old". A book of UNKNOWN AGE, with our own contribution to the delay bounded at
five seconds. Those are very different propositions and the second is the true
one.

HOW THE RISK IS DETECTED. Not by watching -- a stale book is indistinguishable
from a fresh one at decision time. Only by comparing fills against the prices we
expected, afterwards, which is why that comparison is a release condition.
"""

from __future__ import annotations

import time

#: THE BASIS TOKEN. Deliberately not in `bettor_venue_currency`'s establishing
#: set, deliberately not containing the word ESTABLISHED, and deliberately ugly
#: so it cannot be mistaken for a measurement in a log.
BASIS_OWNER_POLICY = "OWNER_ACCEPTED_POLICY_ASSUMPTION_NOT_A_MEASUREMENT"

#: THE CODE CONSTANT. False in the shipped build.
#:
#: Two independent things must both be true for a policy admission to happen: an
#: owner record must exist AND this must be True. Turning it on is a code change
#: that goes through the gate and the release process, so a signed record alone
#: cannot start a trade -- which is the same shape as
#: `REAL_ORDER_SUBMISSION_ENABLED` and for the same reason.
POLICY_ADMISSION_ENABLED = False

#: What the owner would be accepting, verbatim, for the approval record. This
#: text is what `accept()` requires to be echoed back, so a signature cannot be
#: obtained against a summary.
THE_ASSUMPTION = (
    "I accept that under the current evidence requirements this market-data path "
    "does not qualify, that the meaning of the venue's transactTime field is "
    "unresolved, and that THE AGE OF THE ORDER BOOK INSIDE ANY MESSAGE WE "
    "RECEIVE IS ENTIRELY UNBOUNDED. I understand that the five-second bound in "
    "this policy limits only the delay WE add between receiving a message and "
    "acting on it, and bounds nothing whatever about how old the book was when "
    "the venue sent it -- a ten-minute-old book received one second ago is still "
    "ten minutes old. I accept that a full-replacement order-book message "
    "received on a live, non-dropped subscription may be treated as tradeable, "
    "as MY POLICY DECISION and not as a measurement. I accept that a stale book "
    "is indistinguishable from a fresh one at decision time, so this risk is "
    "detected only afterwards by comparing fills against expected prices."
)

#: THE POLICY'S OWN VERSION. Bound into every acceptance, so a change to the
#: terms invalidates signatures taken against the old ones rather than silently
#: inheriting them.
POLICY_VERSION = "ADMISSION_POLICY_V2_UPSTREAM_AGE_UNBOUNDED"

#: THE BOUNDS THE ASSUMPTION IS OFFERED UNDER. Every one of these is enforced
#: server-side and none of them is the caller's to widen.
PROPOSED_BOUNDS = {
    "venue": "PMUS",
    "max_subscription_silence_s": 15.0,
    # RENAMED, BECAUSE THE OLD NAME LIED ABOUT WHAT IT MEASURES.
    #
    # It was `max_message_age_s`, which reads as "the book is at most this old".
    # It is not. It is the gap between OUR RECEIPT of a message and OUR DECISION
    # on it -- our own processing delay, and nothing else.
    "max_our_processing_delay_s": 5.0,
    "what_this_bounds": (
        "the delay WE add after receiving a message. It bounds our contribution "
        "and NOTHING about the age of the book inside the message"),
    "what_this_does_NOT_bound": (
        "the upstream age, which is ENTIRELY UNBOUNDED under this exception. A "
        "ten-minute-old book received one second ago is still ten minutes old, "
        "and tightening this number from 30 to 5 does not change that by one "
        "second"),
    "and_I_described_it_wrongly": (
        "I called tightening it a reduction in 'exposure to being wrong'. That "
        "was the receipt-instant error this repository already withdrew once, "
        "reintroduced inside the bounds meant to contain the risk"),
    "max_single_order_usd": 25.0,
    "max_total_pilot_usd": 100.0,
    "max_concurrent_positions": 1,
    "expires_after_s": 24 * 3600,
    "why_it_expires": (
        "an assumption accepted on one day's evidence is not accepted "
        "indefinitely. It lapses and has to be re-signed"),
}

R_NOT_ENABLED = "POLICY_ADMISSION_IS_DISABLED_IN_CODE"
R_NO_RECORD = "NO_OWNER_ACCEPTANCE_RECORD_EXISTS"
R_TEXT_MISMATCH = "THE_ACCEPTANCE_DOES_NOT_ECHO_THE_ASSUMPTION"
R_EXPIRED = "THE_OWNER_ACCEPTANCE_HAS_EXPIRED"
R_REVOKED = "THE_OWNER_ACCEPTANCE_WAS_REVOKED"
R_VENUE = "THE_ACCEPTANCE_IS_FOR_A_DIFFERENT_VENUE"
R_NO_SUBSCRIPTION = "NO_LIVE_SUBSCRIPTION_FOR_THIS_MARKET"
R_SILENT = "THE_SUBSCRIPTION_HAS_BEEN_SILENT_TOO_LONG"
R_OUR_DELAY_TOO_LONG = "OUR_OWN_PROCESSING_DELAY_SINCE_RECEIPT_IS_TOO_LONG"
#: Kept so a stored refusal string still resolves. The old name said "the
#: message is older than the bound", which described the book. It never did.
R_MESSAGE_TOO_OLD = R_OUR_DELAY_TOO_LONG
R_OVER_ORDER_CAP = "THE_ORDER_EXCEEDS_THE_POLICY_SINGLE_ORDER_CAP"
R_OVER_PILOT_CAP = "THE_PILOT_TOTAL_EXCEEDS_THE_POLICY_CAP"

ACCEPTANCE_KEY = "bettor_admission_policy_acceptance"


def available() -> dict:
    """Is a policy admission possible at all in this build?"""
    return {
        "enabled_in_code": POLICY_ADMISSION_ENABLED,
        "refusal": None if POLICY_ADMISSION_ENABLED else R_NOT_ENABLED,
        "why": (None if POLICY_ADMISSION_ENABLED else
                "POLICY_ADMISSION_ENABLED is False in the shipped build. An "
                "owner acceptance record alone does not start a trade; turning "
                "this on is a code change that goes through the gate"),
        "and_the_currency_verdict_is_unaffected": (
            "bettor_venue_currency still returns NOT_ESTABLISHED and admits() "
            "still returns False. This is an override recorded alongside that "
            "verdict, never a change to it"),
    }


def acceptance_record(*, accepted_by: str, echoed_assumption: str,
                      venue: str, bounds: dict | None = None,
                      now: float | None = None) -> dict:
    """BUILD THE OWNER'S ACCEPTANCE. Refuses anything short of a real signature.

    `echoed_assumption` must match `THE_ASSUMPTION` exactly. A signature obtained
    against a paraphrase is not a signature against this risk, and the whole
    value of the record is that the person who signed it read what they were
    accepting.
    """
    at = float(now if now is not None else time.time())
    if (echoed_assumption or "").strip() != THE_ASSUMPTION:
        return {"ok": False, "refusal": R_TEXT_MISMATCH,
                "why": ("the acceptance must echo THE_ASSUMPTION verbatim. A "
                        "signature against a summary is not a signature "
                        "against this risk"),
                "expected_chars": len(THE_ASSUMPTION)}
    if not str(accepted_by or "").strip():
        return {"ok": False, "refusal": R_NO_RECORD,
                "why": "an acceptance must name who accepted it"}
    b = dict(PROPOSED_BOUNDS)
    # THE OWNER MAY TIGHTEN AND NEVER WIDEN. Element-wise minimum on every
    # numeric bound, same rule the approved limits already use, so accepting
    # with a larger number than proposed changes nothing.
    tightened = {}
    for k, v in (bounds or {}).items():
        if k in b and isinstance(b[k], (int, float)) and isinstance(
                v, (int, float)):
            if float(v) < float(b[k]):
                b[k] = float(v)
                tightened[k] = float(v)
    return {
        "ok": True, "refusal": None,
        "accepted_by": str(accepted_by).strip(),
        "accepted_at": at,
        "venue": str(venue or "").upper(),
        "assumption": THE_ASSUMPTION,
        "bounds": b,
        "tightened_by_the_owner": tightened,
        "expires_at": at + float(b["expires_after_s"]),
        "basis": BASIS_OWNER_POLICY,
        "revoked": False,
        "this_is_not_a_measurement": True,
    }


def admits(*, record, subscription, venue, proposed_cost_usd,
           pilot_total_usd=0.0, now=None) -> dict:
    """MAY THIS ORDER GO, UNDER THE OWNER'S POLICY? Every refusal named.

    `subscription` is `bettor_stream_currency.evidence_for(slug)["subscription"]`
    -- which is `None` unless P1-P4 all hold, so the message really is a
    full-replacement snapshot for the right instrument on a connection that has
    not dropped. The policy assumption covers TIMING ONLY; it does not excuse a
    missing book, the wrong instrument, or a book carried across a disconnect.
    """
    at = float(now if now is not None else time.time())
    out = {"basis": BASIS_OWNER_POLICY, "ok": False, "refusal": None,
           "this_is_not_a_measurement": True,
           "the_currency_verdict_is_still": "BOOK_CURRENCY_NOT_ESTABLISHED"}

    if not POLICY_ADMISSION_ENABLED:
        return dict(out, refusal=R_NOT_ENABLED,
                    why=available()["why"])
    rec = record if isinstance(record, dict) else None
    if not rec or not rec.get("accepted_by"):
        return dict(out, refusal=R_NO_RECORD,
                    why=("no owner acceptance exists. The absence of a record "
                         "is a refusal, not a default"))
    if rec.get("revoked") or rec.get("revoked_at"):
        return dict(out, refusal=R_REVOKED, why="the acceptance was revoked")
    if (rec.get("assumption") or "") != THE_ASSUMPTION:
        return dict(out, refusal=R_TEXT_MISMATCH,
                    why=("the stored acceptance does not echo the current "
                         "assumption text. If the assumption changed, it needs "
                         "a new signature"))
    exp = rec.get("expires_at")
    if exp is None or at >= float(exp):
        return dict(out, refusal=R_EXPIRED,
                    why=("the acceptance has lapsed and must be re-signed. An "
                         "assumption accepted on one day's evidence is not "
                         "accepted indefinitely"))
    if str(rec.get("venue") or "").upper() != str(venue or "").upper():
        return dict(out, refusal=R_VENUE,
                    why="accepted for %r, asked for %r"
                        % (rec.get("venue"), venue))

    b = dict(rec.get("bounds") or {})
    if not isinstance(subscription, dict):
        return dict(out, refusal=R_NO_SUBSCRIPTION,
                    why=("no live full-replacement subscription for this "
                         "market. The policy covers TIMING only -- it does not "
                         "excuse a missing book, the wrong instrument, or a "
                         "book carried across a disconnect"))
    silence = subscription.get("silence_s")
    if silence is None:
        alive = subscription.get("alive_at")
        silence = None if alive is None else at - float(alive)
    if silence is None or float(silence) > float(b["max_subscription_silence_s"]):
        return dict(out, refusal=R_SILENT, silence_s=silence,
                    why=("the subscription has not proven itself alive within "
                         "%.0f s" % float(b["max_subscription_silence_s"])))
    last = subscription.get("last_update_at")
    age = None if last is None else at - float(last)
    # NAMED FOR WHAT IT IS: our own delay since receipt, not the book's age.
    out["our_processing_delay_s"] = None if age is None else round(age, 3)
    out["upstream_book_age_s"] = None
    out["upstream_book_age_is"] = "UNBOUNDED_UNDER_THIS_EXCEPTION"
    if age is None or age > float(b["max_our_processing_delay_s"]):
        return dict(out, refusal=R_OUR_DELAY_TOO_LONG,
                    why=("we received the last full-book message %s, against a "
                         "%.0f s bound on OUR OWN processing delay. This says "
                         "nothing about how old the book was when it arrived"
                         % ("never" if age is None else "%.1f s ago" % age,
                            float(b["max_our_processing_delay_s"]))))
    cost = float(proposed_cost_usd or 0.0)
    if cost > float(b["max_single_order_usd"]) + 1e-9:
        return dict(out, refusal=R_OVER_ORDER_CAP,
                    why="%.2f against a %.2f single-order policy cap"
                        % (cost, float(b["max_single_order_usd"])))
    if float(pilot_total_usd or 0.0) + cost > float(
            b["max_total_pilot_usd"]) + 1e-9:
        return dict(out, refusal=R_OVER_PILOT_CAP,
                    why=("the pilot has committed %.2f and this order proposes "
                         "%.2f against a %.2f total policy cap"
                         % (float(pilot_total_usd or 0.0), cost,
                            float(b["max_total_pilot_usd"]))))
    return dict(out, ok=True, refusal=None,
                accepted_by=rec["accepted_by"],
                accepted_at=rec["accepted_at"],
                expires_at=rec["expires_at"],
                why=("admitted under the owner's POLICY ASSUMPTION, not under a "
                     "measurement. %s accepted the stated risk at %s and it "
                     "lapses at %s"
                     % (rec["accepted_by"], rec["accepted_at"],
                        rec["expires_at"])))


def describe() -> dict:
    return {
        "what_this_is": (
            "the owner's explicit acceptance of a named risk, in place of a "
            "guarantee the venue does not publish"),
        "what_this_is_not": (
            "a measurement, a mechanism, or a change to the freshness gate. "
            "bettor_venue_currency is untouched: it still returns "
            "NOT_ESTABLISHED and admits() still returns False"),
        "basis_token": BASIS_OWNER_POLICY,
        "enabled_in_code": POLICY_ADMISSION_ENABLED,
        "two_things_must_both_be_true": (
            "an owner acceptance record must exist AND "
            "POLICY_ADMISSION_ENABLED must be True. A signed record alone "
            "starts nothing"),
        "the_assumption": THE_ASSUMPTION,
        "proposed_bounds": dict(PROPOSED_BOUNDS),
        "owner_may_tighten_never_widen": True,
        "it_expires": "%d hours" % (PROPOSED_BOUNDS["expires_after_s"] // 3600),
        "what_the_policy_does_NOT_excuse": [
            "a missing book (P1)",
            "the wrong instrument (P4)",
            "a book carried across a disconnect (P3)",
            "an unreadable account-wide exposure total",
            "an unreconciled account",
            "the code constant on real order submission",
        ],
        "refusals": [R_NOT_ENABLED, R_NO_RECORD, R_TEXT_MISMATCH, R_EXPIRED,
                     R_REVOKED, R_VENUE, R_NO_SUBSCRIPTION, R_SILENT,
                     R_MESSAGE_TOO_OLD, R_OVER_ORDER_CAP, R_OVER_PILOT_CAP],
        "how_the_risk_is_detected": (
            "not by watching -- a stale book is indistinguishable from a fresh "
            "one at decision time. Only by comparing fills against the prices "
            "we expected, which is why that comparison is a release condition"),
    }
