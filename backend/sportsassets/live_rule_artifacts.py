"""WHICH LIVE BOOK-CURRENCY RULES THE OWNER HAS APPROVED (migration 204).

The actual lane admits a book-currency verdict only under an APPROVED live
rule (`actual_admission.book_currency_admission`). The approved set is

    actual_admission.APPROVED_LIVE_BOOK_RULES          (code constant; EMPTY)
  U { rule_id of every live_rule_artifacts row that is
        status = 'APPROVED'
        AND carries an owner approval record (actor, approved_at, statement)
            whose actor is not an agent
        AND whose (version, sha256) is exactly what THIS code implements
            (live_book_currency.CODE_RULES) }

READ-ONLY and FAIL CLOSED: a missing table, a missing row, a hash that
differs from the code's, a malformed row or ANY error yields only the code
constant -- never a wider set. The read runs inside a savepoint when the
connection is in a transaction, so a failure never poisons the caller's
transaction. Nothing in the application writes `live_rule_artifacts`; the
owner records an approval as an operator action (research/
p5_live_stream_book_v1.md).
"""
from __future__ import annotations

import logging

from . import actual_admission as AA
from . import live_book_currency as LBC

log = logging.getLogger(__name__)

TABLE = "live_rule_artifacts"
STATUS_READY = "READY_FOR_OWNER_APPROVAL"
STATUS_APPROVED = "APPROVED"

#: Never an approver (the table CHECKs the same).
AGENT_ACTORS = ("DEREK", "XAVIER", "AUDREY", "CLAUDE", "SYSTEM")
AGENT_PREFIXES = ("AGENT", "CLAUDE", "MIGRATION")

_SQL = ("SELECT rule_id, version, sha256, status, owner_approval_actor, "
        "       owner_approved_at, owner_approval_statement, created_by "
        "  FROM live_rule_artifacts WHERE status = 'APPROVED'")


def _actor_ok(actor, created_by) -> bool:
    a = str(actor or "").strip().upper()
    if not a:
        return False
    if a in AGENT_ACTORS or any(a.startswith(p) for p in AGENT_PREFIXES):
        return False
    return a != str(created_by or "").strip().upper()


def admissible_rows(rows, code_rules=None) -> frozenset:
    """Pure: the rule ids among `rows` that are APPROVED with a valid owner
    record AND match the code's (version, sha256)."""
    code = LBC.CODE_RULES if code_rules is None else code_rules
    out = set()
    for r in rows or ():
        try:
            r = dict(r)
            if r.get("status") != STATUS_APPROVED:
                continue
            if not _actor_ok(r.get("owner_approval_actor"), r.get("created_by")):
                continue
            if r.get("owner_approved_at") is None:
                continue
            if not str(r.get("owner_approval_statement") or "").strip():
                continue
            want = (code.get(r.get("rule_id")) or {}).get(r.get("version"))
            if want is None or r.get("sha256") != want:
                continue
            out.add(str(r["rule_id"]))
        except Exception:                                     # noqa: BLE001
            continue
    return frozenset(out)


async def _read(conn):
    if await conn.fetchval("SELECT to_regclass($1)", TABLE) is None:
        return []
    return await conn.fetch(_SQL)


async def approved_live_book_rules(conn) -> frozenset:
    """THE APPROVED SET the actual lane passes to actual_admission.evaluate.
    NEVER RAISES; any failure -> the code constant alone (empty)."""
    base = frozenset(AA.APPROVED_LIVE_BOOK_RULES)
    try:
        in_tx = False
        try:
            in_tx = bool(conn.is_in_transaction())
        except Exception:                                     # noqa: BLE001
            in_tx = False
        if in_tx:
            async with conn.transaction():                    # a savepoint
                rows = await _read(conn)
        else:
            rows = await _read(conn)
        return base | admissible_rows(rows)
    except Exception as exc:                                  # noqa: BLE001
        log.debug("live_rule_artifacts read failed: %s", type(exc).__name__)
        return base


async def describe(conn) -> dict:
    """Read-only state of every code-declared live rule, for payloads."""
    approved = await approved_live_book_rules(conn)
    out = []
    for rid, versions in LBC.CODE_RULES.items():
        for ver, sha in versions.items():
            out.append({"rule_id": rid, "version": ver, "code_sha256": sha,
                        "admissible": rid in approved})
    return {"table": TABLE, "rules": out,
            "code_constant": sorted(AA.APPROVED_LIVE_BOOK_RULES),
            "approved_live_book_rules": sorted(approved)}
