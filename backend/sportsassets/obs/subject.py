"""Who the experiment is about -- run 83.2, owner decision 5.

FAILS CLOSED. With RN1_OBSERVABILITY_SUBJECT_WHALE_ID unset, malformed or
unknown, this module admits NOTHING and says why. That is not defensiveness for
its own sake: RUN83_ACTIVATION_FAILED_V1 had no subject filter at all, and
94.671% of its 12,535 events belonged to the other nineteen wallets on the
ingestion roster. An instrument that observes everyone when it is misconfigured
produces a cohort that looks full and answers a different question.

THE ADMISSION KEY IS THE IMMUTABLE ID, NEVER THE NAME. whales.username is
editable and the copy roster has already been re-cut once this month; a rename
must not be able to move the population. The username and wallet address ride
along as provenance, which is what a human reads in a log, and neither decides
anything.

NO I/O. Eligibility is evaluated synchronously on the ingestion hot path, from
a field already on the TradeEvent. There is no database round trip, no cache and
no await -- the whole point of owner decision 4's ordering is that the anchor is
stamped and eligibility decided before anything can block.
"""
from __future__ import annotations

import os


class AdmissionReason:
    """Why an event was or was not admitted. Every value is written to a row."""

    ADMITTED = "ADMITTED_SUBJECT_FIRST_RECEIPT"
    NOT_CONFIGURED = "OBSERVABILITY_SUBJECT_NOT_CONFIGURED"
    NOT_SUBJECT = "NOT_SUBJECT_WALLET"
    NOT_FIRST_RECEIPT = "NOT_FIRST_RECEIPT_CANONICAL_DEDUPE"
    NO_WHALE_ID = "EVENT_CARRIED_NO_WHALE_ID"


def configured_subject_whale_id() -> int | None:
    """The subject's immutable id, or None if it is not usably configured.

    NO DEFAULT. A default here would be a guess about whose money is being
    studied, and a wrong guess is silent: the rows would all look valid.
    """
    raw = os.environ.get("RN1_OBSERVABILITY_SUBJECT_WHALE_ID")
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        # Malformed is treated exactly like unset: not configured. Coercing
        # "2x" to 2 would admit a population nobody chose.
        return None
    return value if value > 0 else None


def subject_expected_wallet() -> str | None:
    """Optional boot-time cross-check: the wallet the subject id must resolve to.

    Set RN1_OBSERVABILITY_SUBJECT_WALLET to the expected address and the boot
    check (config.verify_subject_against_roster) refuses to collect if the
    configured id names a different wallet. Two independent facts have to agree
    before a cohort begins, so a re-numbered or re-pointed roster row cannot
    quietly change the subject between cohorts.
    """
    raw = os.environ.get("RN1_OBSERVABILITY_SUBJECT_WALLET")
    return raw.strip().lower() or None if raw else None


def admit(*, whale_id: int | None, was_insert: bool) -> tuple[bool, str]:
    """(admitted, reason) for one event. Pure, synchronous, never raises.

    The two conditions are the owner's primary population verbatim:
    RN1-confirmed AND canonical was_insert = true.

    was_insert IS TAKEN, NOT COMPUTED. It is the answer from the canonical
    `INSERT INTO trades ... ON CONFLICT (dedupe_key) DO UPDATE ... RETURNING
    (xmax = 0) AS was_insert` and nothing here second-guesses it. Owner decision
    4 rejected a per-lane first_seen flag precisely so that production and the
    research instrument share one dedupe authority; re-deriving it here would
    recreate the second authority under a different name.
    """
    subject = configured_subject_whale_id()
    if subject is None:
        return False, AdmissionReason.NOT_CONFIGURED
    if whale_id is None:
        return False, AdmissionReason.NO_WHALE_ID
    if int(whale_id) != subject:
        return False, AdmissionReason.NOT_SUBJECT
    if not was_insert:
        return False, AdmissionReason.NOT_FIRST_RECEIPT
    return True, AdmissionReason.ADMITTED
