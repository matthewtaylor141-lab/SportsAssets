#!/usr/bin/env python3
"""MICRO_LIVE_V1 -- THE EXECUTION HARNESS THAT CANNOT SUBMIT.

WHAT THIS IS. The full path from a candidate opportunity to the exact order
instruction that WOULD be sent: EV evaluation, admission, order construction,
risk gates, and a dry-run receipt. Every step a live order would take, run to
completion, with the last step structurally absent.

WHAT MAKES IT UNABLE TO SUBMIT, and it is not a flag. There is no venue client
in this module, no host constant, no HTTP call, no credential read, no import
of any module that can place an order. `WOULD_SUBMIT` is a terminal state whose
successor is not implemented: `submit()` does not exist. A reviewer looking for
the submit path finds nothing to disable, because there is nothing there.

    MICRO_LIVE_MODE       = DRY_RUN_NO_SUBMIT
    LIVE_ORDER_SUBMISSION = DISABLED
    LIVE_EXECUTION_ENABLED = False

THE LIVE STATES ARE DEFINED AND UNREACHABLE. SUBMITTED, ACKNOWLEDGED,
PARTIAL_FILL and the rest exist as names so the reconciliation and telemetry
schemas can be written against them now. `transition()` refuses to enter any of
them while the mode is DRY_RUN_NO_SUBMIT, and the refusal is a raised error,
not a logged warning.

FAIL CLOSED, ALWAYS. Every gate here answers "may this proceed?" with a default
of NO. An unreadable input, an unknown state, a missing limit, an exception --
each ends in BLOCKED. There is no path in this file where absence of evidence
becomes permission.

This module contacts nothing.
"""
import hashlib
import json
from datetime import datetime, timezone

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_SET = "NOT_SET"

MICRO_LIVE_MODE = "DRY_RUN_NO_SUBMIT"
LIVE_ORDER_SUBMISSION = "DISABLED"
LIVE_EXECUTION_ENABLED = False
ORDERS_PLACED = 0
CAPITAL_DEPLOYED = 0
CREDENTIALS = "NONE"
MIRROR_LIVE = False
THIS_MODULE_CONTACTS_NOTHING = True
NO_SUBMIT_FUNCTION_EXISTS = True
WHY_NO_SUBMIT_FUNCTION = (
    "a submit path guarded by a flag is a submit path; this module has no "
    "venue client, no host, no credential read and no submit function, so "
    "there is nothing for a mistake to switch on")

# ---------------------------------------------------------------------------
# THE LIFECYCLE
# ---------------------------------------------------------------------------

DRY_RUN_STATES = (
    "CANDIDATE",
    "EV_EVALUATED",
    "ADMISSION_PASS",
    "ADMISSION_FAIL",
    "ORDER_PROPOSED",
    "RISK_APPROVED",
    "RISK_REJECTED",
    "DRY_RUN_READY",
    "WOULD_SUBMIT",
    "WOULD_NOT_SUBMIT",
)

LIVE_STATES = (
    "SUBMITTED",
    "ACKNOWLEDGED",
    "PARTIAL_FILL",
    "FULL_FILL",
    "CANCEL_REQUESTED",
    "CANCELLED",
    "REJECTED",
    "PASSIVE_CLOSE",
    "AGGRESSIVE_CLOSE",
    "SETTLED",
)

TERMINAL_DRY_RUN_STATES = ("ADMISSION_FAIL", "RISK_REJECTED", "WOULD_SUBMIT",
                           "WOULD_NOT_SUBMIT")

ALLOWED = {
    "CANDIDATE": ("EV_EVALUATED",),
    "EV_EVALUATED": ("ADMISSION_PASS", "ADMISSION_FAIL"),
    "ADMISSION_PASS": ("ORDER_PROPOSED",),
    "ADMISSION_FAIL": (),
    "ORDER_PROPOSED": ("RISK_APPROVED", "RISK_REJECTED"),
    "RISK_APPROVED": ("DRY_RUN_READY",),
    "RISK_REJECTED": (),
    "DRY_RUN_READY": ("WOULD_SUBMIT", "WOULD_NOT_SUBMIT"),
    # WOULD_SUBMIT's successor is SUBMITTED, and it is not reachable here.
    "WOULD_SUBMIT": (),
    "WOULD_NOT_SUBMIT": (),
}


class MicroLiveError(Exception):
    """Base. Every refusal in this module raises rather than returns."""


class SubmissionBlocked(MicroLiveError):
    """A live state was requested while submission is disabled."""


class IllegalTransition(MicroLiveError):
    """A transition the lifecycle does not define."""


class KillSwitchActive(MicroLiveError):
    """The master switch, or one of the fail-closed conditions."""


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Lifecycle:
    """One economic decision's state, with every transition timestamped.

    The history is the audit record: what state, when, why, and under which
    code and configuration revision. A transition with no reason is refused --
    an unexplained state change is exactly what a reconciliation cannot later
    account for.
    """

    def __init__(self, decision_id, clock=utcnow):
        self.decision_id = decision_id
        self.state = "CANDIDATE"
        self._clock = clock
        self.history = [{"STATE": "CANDIDATE", "AT": clock(),
                         "WHY": "candidate observed", "FROM": None}]

    def transition(self, to, why, mode=None):
        mode = MICRO_LIVE_MODE if mode is None else mode
        if not why:
            raise IllegalTransition("a transition needs a reason")
        if to in LIVE_STATES:
            if mode == "DRY_RUN_NO_SUBMIT":
                raise SubmissionBlocked(
                    "%s is a LIVE state; MICRO_LIVE_MODE = %s and "
                    "LIVE_ORDER_SUBMISSION = %s" % (to, mode,
                                                    LIVE_ORDER_SUBMISSION))
            raise SubmissionBlocked(
                "%s requires a separate explicit authorization that this "
                "build does not implement" % to)
        if to not in DRY_RUN_STATES:
            raise IllegalTransition("unknown state %r" % (to,))
        if to not in ALLOWED.get(self.state, ()):
            raise IllegalTransition("%s -> %s is not a defined transition"
                                    % (self.state, to))
        self.history.append({"STATE": to, "AT": self._clock(), "WHY": why,
                             "FROM": self.state})
        self.state = to
        return self.state

    def audit(self):
        return {
            "DECISION_ID": self.decision_id,
            "STATE": self.state,
            "IS_TERMINAL": self.state in TERMINAL_DRY_RUN_STATES,
            "TRANSITIONS": list(self.history),
            "TRANSITION_COUNT": len(self.history),
            "EVERY_TRANSITION_TIMESTAMPED": all(h.get("AT")
                                                for h in self.history),
            "EVERY_TRANSITION_HAS_A_REASON": all(
                h.get("WHY") for h in self.history),
            "MICRO_LIVE_MODE": MICRO_LIVE_MODE,
            "LIVE_ORDER_SUBMISSION": LIVE_ORDER_SUBMISSION,
            "LIVE_STATES_DEFINED_BUT_UNREACHABLE": list(LIVE_STATES),
        }


# ---------------------------------------------------------------------------
# IDENTIFIERS AND IDEMPOTENCY
# ---------------------------------------------------------------------------

ID_KINDS = ("DECISION_ID", "POSITION_ID", "ORDER_INTENT_ID", "CLIENT_ORDER_ID")
WHY_IDEMPOTENCY = (
    "a retry is a network event, not a second decision; one economic decision "
    "that produces two orders is an overfill nobody chose")


def decision_id(event_id, market_id, side, decided_at, nonce=""):
    """Deterministic in the decision, not in the attempt.

    Two retries of the SAME decision produce the SAME id, which is the whole
    point: the registry can then recognise the repeat. A genuinely new decision
    differs in at least one of these fields.
    """
    raw = "|".join(str(x) for x in (event_id, market_id, side, decided_at,
                                    nonce))
    return "D-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def order_intent_id(decision, limit_price, quantity, order_type):
    raw = "|".join(str(x) for x in (decision, limit_price, quantity,
                                    order_type))
    return "I-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def client_order_id(intent):
    """The venue-facing id. Derived from the intent, so a resend of the same
    intent carries the same client id and the venue can reject the duplicate
    even if our own registry were bypassed."""
    return "C-" + hashlib.sha256(str(intent).encode()).hexdigest()[:20]


def position_id(event_id, market_id, side):
    raw = "|".join(str(x) for x in (event_id, market_id, side))
    return "P-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


class IntentRegistry:
    """Idempotency. A repeated intent returns the existing record.

    THE FAILURE THIS PREVENTS is the one the mirror has already lived through:
    a response lost in flight, a retry issued, and two orders resting where one
    decision was made. The registry answers "have I already committed to this?"
    before anything is constructed, and its answer for a known intent is the
    original record -- never a new one, and never silence.
    """

    def __init__(self):
        self._by_intent = {}
        self._by_client = {}

    def register(self, intent_id, record):
        if intent_id in self._by_intent:
            return {"IDEMPOTENT_HIT": True,
                    "CREATED_NEW": False,
                    "ORDER_INTENT_ID": intent_id,
                    "RECORD": self._by_intent[intent_id],
                    "WHY": WHY_IDEMPOTENCY}
        cid = client_order_id(intent_id)
        self._by_intent[intent_id] = dict(record, ORDER_INTENT_ID=intent_id,
                                          CLIENT_ORDER_ID=cid)
        self._by_client[cid] = intent_id
        return {"IDEMPOTENT_HIT": False,
                "CREATED_NEW": True,
                "ORDER_INTENT_ID": intent_id,
                "CLIENT_ORDER_ID": cid,
                "RECORD": self._by_intent[intent_id]}

    def get(self, intent_id):
        return self._by_intent.get(intent_id)

    def known_client_order_id(self, cid):
        return cid in self._by_client

    def __len__(self):
        return len(self._by_intent)


# ---------------------------------------------------------------------------
# THE KILL SWITCH AND THE FAIL-CLOSED CONDITIONS
# ---------------------------------------------------------------------------

FAIL_CLOSED_CONDITIONS = (
    "STALE_BOOK",
    "MISSING_PRICE",
    "MISSING_EVENT_IDENTITY",
    "MISSING_MARKET_IDENTITY",
    "UNKNOWN_EV_CRITICAL_INPUT",
    "CODE_SHA_MISMATCH",
    "CONFIG_SHA_MISMATCH",
    "RISK_LIMIT_MISSING",
    "RISK_LIMIT_EXCEEDED",
    "DUPLICATE_ORDER",
    "UNEXPECTED_EXISTING_INVENTORY",
    "VENUE_RESPONSE_NOT_UNDERSTOOD",
    "POSITION_STATE_INCONSISTENT",
    "TELEMETRY_FAILURE",
    "KILL_SWITCH_ACTIVE",
)

FAIL_CLOSED = True
NEVER_FAIL_OPEN = (
    "an unreadable input, an unknown state and an exception all mean the same "
    "thing here: we do not know, and not knowing is never permission")


def kill_switch(conditions=None, live_execution_enabled=LIVE_EXECUTION_ENABLED,
                unknown_conditions=None):
    """May execution proceed? The answer defaults to NO.

    `conditions` maps a FAIL_CLOSED_CONDITIONS name -> truthy if it is TRIPPED.
    A condition that is absent from the map is NOT assumed safe: it is reported
    in UNEVALUATED_CONDITIONS, and any unevaluated condition blocks. A gate
    that only stops what it was asked about is a gate with a hole in it.
    """
    conditions = dict(conditions or {})
    unknown = list(unknown_conditions or ())
    tripped = [c for c in FAIL_CLOSED_CONDITIONS if conditions.get(c)]
    unevaluated = [c for c in FAIL_CLOSED_CONDITIONS if c not in conditions]
    unrecognised = [c for c in conditions if c not in FAIL_CLOSED_CONDITIONS]
    blocked = bool(tripped or unevaluated or unrecognised or unknown
                   or not live_execution_enabled)
    return {
        "LIVE_EXECUTION_ENABLED": bool(live_execution_enabled),
        "EXECUTION_PERMITTED": "NO" if blocked else "YES",
        "BLOCKED": blocked,
        "TRIPPED_CONDITIONS": tripped,
        "UNEVALUATED_CONDITIONS": unevaluated,
        "UNRECOGNISED_CONDITIONS": unrecognised,
        "UNKNOWN_CONDITIONS_REPORTED_BY_CALLER": unknown,
        "AN_UNEVALUATED_CONDITION_BLOCKS": True,
        "FAIL_CLOSED": FAIL_CLOSED,
        "NEVER_FAIL_OPEN": NEVER_FAIL_OPEN,
        "MICRO_LIVE_MODE": MICRO_LIVE_MODE,
        "LIVE_ORDER_SUBMISSION": LIVE_ORDER_SUBMISSION,
        "EVALUATED_AT": utcnow(),
    }


def all_conditions_clear():
    """The map a caller must build to even be considered. Provided so the
    'everything false' case is written once and cannot drift."""
    return {c: False for c in FAIL_CLOSED_CONDITIONS}


def status():
    return {
        "MICRO_LIVE_MODE": MICRO_LIVE_MODE,
        "LIVE_ORDER_SUBMISSION": LIVE_ORDER_SUBMISSION,
        "LIVE_EXECUTION_ENABLED": LIVE_EXECUTION_ENABLED,
        "ORDERS_PLACED": ORDERS_PLACED,
        "CAPITAL_DEPLOYED": CAPITAL_DEPLOYED,
        "CREDENTIALS": CREDENTIALS,
        "mirror_live": MIRROR_LIVE,
        "NO_SUBMIT_FUNCTION_EXISTS": NO_SUBMIT_FUNCTION_EXISTS,
        "WHY_NO_SUBMIT_FUNCTION": WHY_NO_SUBMIT_FUNCTION,
        "THIS_MODULE_CONTACTS_NOTHING": THIS_MODULE_CONTACTS_NOTHING,
        "DRY_RUN_STATES": list(DRY_RUN_STATES),
        "LIVE_STATES_DEFINED_BUT_UNREACHABLE": list(LIVE_STATES),
        "FAIL_CLOSED_CONDITIONS": list(FAIL_CLOSED_CONDITIONS),
    }


def render(s=None):
    s = s or status()
    return "\n".join("%-42s = %s" % (k, v if not isinstance(v, list)
                                     else json.dumps(v))
                     for k, v in s.items() if not isinstance(v, list))
