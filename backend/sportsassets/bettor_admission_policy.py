"""THE ONE DECISION THAT COULD ADMIT A TRADE TODAY — and it is the owner's.

THE BLOCKING FACT, FIRST. Under the most favourable observation the venue can
produce — an origin read with `Age: 0`, an in-bound 304, a live subscription with
a heartbeat one second old and a full-book message one second old, and a venue
timestamp equal to now — `bettor_venue_currency.evaluate` returns
`BOOK_CURRENCY_NOT_ESTABLISHED` and `admits()` is `False`.

    SO ZERO CANDIDATES CAN BE ADMITTED TODAY ON THE EVIDENCE PATH. Not because a
    market is stale, and not because our code is unfinished: because the venue
    publishes no timing guarantee for market data, and an admitting rule may use
    only what the venue documents and what we can observe.

There are exactly two ways past that, and only one of them exists today:

  1  THE VENUE DOCUMENTS ITS TIMING. Not available. Zero matching sentences on
     the published WebSocket page. We cannot make this happen.

  2  THE OWNER ACCEPTS AN EXPLICIT POLICY ASSUMPTION IN ITS PLACE. That is this
     module, and it is a DIFFERENT KIND OF THING from the mechanisms in
     `bettor_venue_currency`. It does not measure anything. It records that a
     named person, at a named time, accepted a named risk for a bounded scope.

WHY THIS IS NOT A WEAKENED EVIDENCE GATE, AND THE DISTINCTION IS THE WHOLE POINT.

A weakened gate would move `MAX_BOOK_STATE_AGE_S`, or add a token to
`ESTABLISHING_MECHANISMS`, or quietly let a fast response stand in for a
market-data age. None of that happens here. `evaluate` still returns
`NOT_ESTABLISHED`; `admits()` still returns `False`; the currency module is
untouched. What this adds is a SEPARATE, VISIBLE, SIGNED OVERRIDE that sits
alongside the verdict and never replaces it:

    verdict:  BOOK_CURRENCY_NOT_ESTABLISHED     <- unchanged, always reported
    basis:    OWNER_ACCEPTED_POLICY_ASSUMPTION  <- never "ESTABLISHED"
    accepted_by / accepted_at / expires_at      <- who, when, until when

Every record of every decision taken under it carries that basis. Nothing in this
system will ever say a book's currency was measured when it was assumed.

AND IT IS OFF. `POLICY_ADMISSION_AVAILABLE` is False until an owner record exists
AND the code constant below is turned on, which is a code change. Writing the
record alone changes nothing. This module exists so that when the owner signs,
the work is already done and reviewable -- not so that it takes effect quietly.

WHAT THE OWNER IS ACCEPTING, IN PLAIN TERMS. That a full-replacement order-book
message, received on a connection proven alive within the silence bound and not
carried across a disconnect, is CURRENT ENOUGH to trade a bounded amount on --
even though the venue states no guarantee to that effect, and even though the
observed `transactTime` values on the markets we could read were old and their
meaning is unresolved.

THE CONCRETE RISK THAT ACCEPTS. We buy at a displayed price that has already
moved. The loss on any one order is bounded by the order's size; the loss across
the pilot is bounded by the approved cap; and the failure is INVISIBLE in the
sense that matters -- a stale book looks exactly like a fresh one, so this cannot
be detected by watching. It is detected only by reconciling fills against the
prices we expected, which is why the pilot's fill-versus-expectation comparison
is a release condition and not a nice-to-have.
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
    "I accept that the venue publishes no market-data timing guarantee, that "
    "the meaning of its transactTime field is unresolved, and that no mechanism "
    "in this system can establish how current an order book is. I accept that a "
    "full-replacement order-book message received on a live, non-dropped "
    "subscription may be treated as sufficiently current to trade on, as MY "
    "POLICY DECISION and not as a measurement. I accept that a stale book is "
    "indistinguishable from a fresh one at decision time, so the risk is "
    "detected only afterwards by comparing fills against expected prices."
)

#: THE BOUNDS THE ASSUMPTION IS OFFERED UNDER. Every one of these is enforced
#: server-side and none of them is the caller's to widen.
PROPOSED_BOUNDS = {
    "venue": "PMUS",
    "max_subscription_silence_s": 15.0,
    "max_message_age_s": 5.0,
    "why_5_not_30": (
        "the 30 s bound was chosen for a MEASURED book-state age. Under an "
        "assumption rather than a measurement the exposure to being wrong is "
        "the whole risk, so the window is tightened, not kept"),
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
R_MESSAGE_TOO_OLD = "THE_LAST_FULL_BOOK_MESSAGE_IS_OLDER_THAN_THE_BOUND"
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
    out["message_age_s"] = None if age is None else round(age, 3)
    if age is None or age > float(b["max_message_age_s"]):
        return dict(out, refusal=R_MESSAGE_TOO_OLD,
                    why=("the last full-book message is %s against a %.0f s "
                         "policy bound"
                         % ("absent" if age is None else "%.1f s old" % age,
                            float(b["max_message_age_s"]))))
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
