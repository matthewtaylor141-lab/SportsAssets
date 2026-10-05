"""OWNER LIVE APPROVALS: VERSIONED, HASH-MATCHED, FAIL CLOSED (R30A sections
23 and 24; audit P0 #8 and #9). Migration 225 `live_approvals`.

WHAT IS APPROVED, AND BY WHAT HASH.

  LIVE_GATE       a live gate's ENFORCED CONFIGURATION. Two gates stay on the
                  live path and are never bypassed:
                    P5_LIVE_STREAM_BOOK_V1        book currentness
                                                  (live_book_currency +
                                                  actual_admission.
                                                  book_currency_admission)
                    SETTLEMENT_COMPATIBILITY_V1   settlement compatibility
                                                  (actual_admission.
                                                  settlement_admission)
                  `gate_config(gate)` is the configuration the gate code
                  actually enforces -- its version, the rule document's hash
                  and every threshold / state set it reads. `config_sha256`
                  is the sha256 of its canonical JSON. An approval names that
                  sha; when any enforced constant changes, the code's sha
                  changes, the stored approval no longer matches and the gate
                  admits nothing until the owner approves the NEW config.
  POLICY_VERSION  the policy a LIVE order would act under, by its
                  canonical_intent.policy_block(...).policy_sha (strategy
                  version + parameter version + values). A paper-only
                  authorization of the parameter version is NOT a LIVE
                  approval.

THE BOOK GATE'S APPROVAL HAS TWO PARTS, both required: the rule document
approved in live_rule_artifacts (migration 204, matched to the code's
document sha -- unchanged) AND the gate configuration approved here (matched
to the code's config sha). live_rule_artifacts.approved_live_book_rules
intersects them.

READ-ONLY AND FAIL CLOSED: a missing table, a missing row, a REVOKE as the
latest decision, a hash that differs from the code's, or ANY error yields
nothing approved. NOTHING IN THE APPLICATION WRITES `live_approvals`; the
owner records an approval as an operator action, and this module creates
none. The reads run in a savepoint when the connection is in a transaction,
so a failure never poisons the caller's transaction.
"""
from __future__ import annotations

import hashlib
import json
import logging

from . import actual_admission as AA
from . import live_book_currency as LBC

log = logging.getLogger(__name__)

TABLE = "live_approvals"
KIND_GATE = "LIVE_GATE"
KIND_POLICY = "POLICY_VERSION"

GATE_BOOK = LBC.RULE_ID
GATE_SETTLEMENT = AA.SETTLEMENT_GATE_ID
GATES = (GATE_BOOK, GATE_SETTLEMENT)
R_GATE_APPROVAL = "LIVE_GATE_APPROVAL_ABSENT_OR_STALE"


def _canon(v):
    if isinstance(v, (set, frozenset, tuple, list)):
        return sorted(("null" if x is None else str(x)) for x in v)
    return v


def gate_config(gate: str) -> dict:
    """THE CONFIGURATION THE GATE CODE ENFORCES (pure). Every value here is
    read from the module that applies it, never restated, so editing an
    enforced constant changes this config and its sha."""
    if gate == GATE_BOOK:
        return {
            "gate": GATE_BOOK, "version": LBC.VERSION,
            "rule_document_sha256": LBC.SHA256,
            "max_receipt_age_s": LBC.MAX_RECEIPT_AGE_S,
            "max_venue_receipt_skew_s": LBC.MAX_VENUE_RECEIPT_SKEW_S,
            "max_verdict_age_at_submit_s": LBC.MAX_VERDICT_AGE_AT_SUBMIT_S,
            "receipt_future_tolerance_s": LBC.RECEIPT_FUTURE_TOLERANCE_S,
            "tradable_instrument_states": _canon(
                LBC.TRADABLE_INSTRUMENT_STATES),
            "verdict_precedence": list(LBC.PRECEDENCE),
            "subscription_running": LBC.SUBSCRIPTION_RUNNING,
            "admission_version": AA.VERSION,
            "admission_tradable_market_states": _canon(
                AA.TRADABLE_MARKET_STATES),
            "admission_negative_values": _canon(AA.NEGATIVE),
            "admission_rule": ("verdict ESTABLISHED under an approved live "
                               "rule, subscription RUNNING or absent, no gap "
                               "since the snapshot")}
    if gate == GATE_SETTLEMENT:
        return {
            "gate": GATE_SETTLEMENT, "version": AA.SETTLEMENT_GATE_VERSION,
            "admission_version": AA.VERSION,
            "rule": dict(AA.SETTLEMENT_GATE_RULE),
            "admission_negative_values": _canon(AA.NEGATIVE)}
    raise KeyError("unknown live gate %r" % gate)


def canonical_json(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def config_sha256(gate: str) -> str:
    return hashlib.sha256(canonical_json(gate_config(gate)).encode()
                          ).hexdigest()


def gate_version(gate: str) -> str:
    return str(gate_config(gate)["version"])


def code_gates() -> dict:
    """{gate: {version, config_sha256}} as THIS build enforces them."""
    return {g: {"version": gate_version(g), "config_sha256": config_sha256(g)}
            for g in GATES}


def _row_ok(r: dict) -> bool:
    from .canonical_intent import is_named_human
    return (str(r.get("decision")) == "APPROVE"
            and is_named_human(r.get("approved_by"))
            and bool(str(r.get("statement") or "").strip()))


def admissible_gates(rows, code=None) -> frozenset:
    """Pure: the gates whose CURRENT decision (rows are the latest per
    subject) is an APPROVE by a named human for exactly the code's version
    and config sha."""
    code = code_gates() if code is None else code
    out = set()
    for r in rows or ():
        try:
            r = dict(r)
            if r.get("subject_kind") != KIND_GATE or not _row_ok(r):
                continue
            want = code.get(r.get("subject_id"))
            if want is None or str(r.get("subject_version")) != want["version"]:
                continue
            if r.get("config_sha256") != want["config_sha256"]:
                continue
            out.add(str(r["subject_id"]))
        except Exception:                                     # noqa: BLE001
            continue
    return frozenset(out)


def admissible_policy_shas(rows) -> frozenset:
    """Pure: the policy shas whose current decision is an APPROVE by a named
    human. The sha IS the match: a policy whose version or values changed has
    another sha and no approval."""
    out = set()
    for r in rows or ():
        try:
            r = dict(r)
            if r.get("subject_kind") != KIND_POLICY or not _row_ok(r):
                continue
            out.add(str(r["config_sha256"]))
        except Exception:                                     # noqa: BLE001
            continue
    return frozenset(out)


async def _current_rows(conn) -> list:
    async def read():
        if await conn.fetchval("SELECT to_regclass($1)", TABLE) is None:
            return []
        return [dict(r) for r in await conn.fetch(
            "SELECT * FROM live_approvals_current")]
    in_tx = False
    try:
        in_tx = bool(conn.is_in_transaction())
    except Exception:                                         # noqa: BLE001
        in_tx = False
    if in_tx:
        async with conn.transaction():                        # a savepoint
            return await read()
    return await read()


async def approved_gates(conn) -> frozenset:
    """The live gates whose configuration approval matches THIS build.
    NEVER RAISES; any failure -> nothing approved."""
    try:
        return admissible_gates(await _current_rows(conn))
    except Exception as exc:                                  # noqa: BLE001
        log.debug("live_approvals read failed: %s", type(exc).__name__)
        return frozenset()


async def approved_settlement_gates(conn) -> frozenset:
    """What actual_admission.evaluate takes as approved_settlement_gates:
    the code constant (AA.APPROVED_SETTLEMENT_GATES, EMPTY) UNION the
    settlement gate when its owner approval matches this build's config --
    the same shape as live_rule_artifacts.approved_live_book_rules."""
    return frozenset(AA.APPROVED_SETTLEMENT_GATES) | frozenset(
        g for g in await approved_gates(conn) if g == GATE_SETTLEMENT)


async def approved_policy_shas(conn) -> frozenset:
    """The policy shas LIVE may act under. NEVER RAISES; failure -> none."""
    try:
        return admissible_policy_shas(await _current_rows(conn))
    except Exception as exc:                                  # noqa: BLE001
        log.debug("live_approvals read failed: %s", type(exc).__name__)
        return frozenset()


async def describe(conn) -> dict:
    """Read-only state for payloads: each gate's code config sha and whether
    a matching approval is in force; how many policy shas are approved."""
    gates = await approved_gates(conn)
    pol = await approved_policy_shas(conn)
    return {"table": TABLE,
            "gates": [{"gate": g, "version": gate_version(g),
                       "code_config_sha256": config_sha256(g),
                       "approved": g in gates} for g in GATES],
            "approved_policy_shas": sorted(pol),
            "writes": "NONE: the owner records approvals as an operator action"}
