"""AUDREY'S RECONCILIATION STATUS, AS STORED AND AS READ (rc6.2 pmus-exec,
migration 366).

THE PROBLEM. Audrey wrote MATCHED / NOT_MIRRORED for a group from an account
snapshot 159 h old. A group she would call either while the newest snapshot
is missing or older than its admissibility bound
(execmirror.ACCOUNT_SNAPSHOT_MAX_AGE_S, 180 s) is a group she CANNOT DECIDE.
She must say so under a name of its own: not MATCHED, not NOT_MIRRORED, and
not DISCREPANCY (a discrepancy nobody found is not a finding).

THE REPRESENTATION. The status column of smalllive_reconciliations is
guarded by a CHECK (migration 198: MATCHED / DISCREPANCY / PENDING /
NOT_MIRRORED) and the table EXISTS in production. A migration that widened
that CHECK to admit a fifth word would change an existing table's
constraint, and the rollback gate (tools/upgrade_path_receipt.py,
compatibility()) cannot prove the previous release still works on a schema
whose existing tables gained or changed constraints. So the new word is not
a new status value:

    stored   status = 'PENDING'  and  stale_reason = <the named reason>
    read as  'STALE'

stale_reason is a nullable column added to smalllive_reconciliations
(migration 366: no default, no constraint, nothing on the existing table
that the previous release could be refused by). It is written in the SAME
statement as the status, so the two never disagree, and it is NULL for every
other status.

WHY PENDING AND NOT ANOTHER WORD (the previous release keeps working on the
new schema). The previous release neither knows nor writes stale_reason; its
Audrey names her columns in the INSERT, so the new column is invisible to
her, and it reads a PENDING row as "not final":

  * runtime_slo's RECONCILIATION_AGE wants MATCHED: PENDING is a breach
    (as STALE was);
  * agents/agent_work, karen_runner and redteam/controls act on
    DISCREPANCY only: PENDING raises no work item and no open discrepancy
    (DISCREPANCY would -- a false one), and it resolves none (only MATCHED
    does);
  * execmirror_view says "a live order in the group is still working: not
    yet final" -- not MATCHED, not NOT_MIRRORED, no decision shown.

A stored MATCHED / NOT_MIRRORED would be the very defect (a decision from
stale evidence); a stored DISCREPANCY a hidden-and-false one. PENDING is the
only existing word that means "no final answer yet", so it is the only one
that keeps the previous release correct. If the previous release rewrites
the row (it recomputes every group on its next pass) it leaves stale_reason
behind; effective() therefore reads STALE only from PENDING + a reason,
never from any other status, and the current release rewrites both columns
on its next pass.

THE READERS. Every reader that shows Audrey's status to a person or a gate
projects it through effective() / effective_sql() so that the current
release sees exactly what STALE was: execmirror_view (the meaning text),
runtime_slo (RECONCILED_STALE), redteam/controls (the quorum's evidence) and
position_rooms (the room's Audrey section). Readers that only act on
DISCREPANCY or MATCHED (agent_work, karen_runner, the quorum's open
discrepancy count) need no projection: a stale group is neither.

Pure: no imports, no database access.
"""
from __future__ import annotations

#: the word a reader sees for a group she cannot decide
STALE = "STALE"
#: the word the table stores for it (the one existing "not final" status)
STORED_AS = "PENDING"
#: what the stale_reason column holds (a refusal-taxonomy code)
R_SNAPSHOT_NOT_CURRENT = "AUDREY_ACCOUNT_SNAPSHOT_NOT_CURRENT"
#: the column migration 366 adds to smalllive_reconciliations
REASON_COLUMN = "stale_reason"
#: the statuses the table's own CHECK (migration 198) admits
STORED_STATUSES = ("MATCHED", "DISCREPANCY", "PENDING", "NOT_MIRRORED")


def stored(status: str, reason: str | None = None) -> tuple:
    """(status to store, stale_reason to store) for what Audrey decided.
    STALE is stored as PENDING + the reason (a reason is required: STALE
    without one would read as an ordinary PENDING); every other status is
    stored as itself with no reason. Never returns a status the table's
    CHECK would refuse."""
    if status == STALE:
        if not reason:
            raise ValueError("a STALE reconciliation names its reason")
        return STORED_AS, reason
    if status not in STORED_STATUSES:
        raise ValueError("not a reconciliation status: %r" % (status,))
    return status, None


def effective(status, reason=None):
    """The status as it is READ: STALE only for a PENDING row that carries a
    stale_reason; every other row reads as the status it stores."""
    if status == STORED_AS and reason:
        return STALE
    return status


def effective_row(row) -> dict:
    """A reconciliation row (a mapping holding status and, optionally,
    stale_reason) with its status replaced by the one to READ; the stored
    word is kept as stored_status. A row without a status (no
    reconciliation on record) is returned unchanged."""
    out = dict(row)
    if "status" in out:
        out["stored_status"] = out["status"]
        out["status"] = effective(out["status"], out.get(REASON_COLUMN))
    return out


def effective_sql(alias: str | None = None) -> str:
    """The SQL expression of effective() over a smalllive_reconciliations
    row (alias = the table's alias in the query, or None when unaliased);
    selected AS status in place of the stored column."""
    p = (alias + ".") if alias else ""
    return ("(CASE WHEN {p}status = '{pending}' AND {p}{col} IS NOT NULL "
            "THEN '{stale}' ELSE {p}status END)").format(
                p=p, pending=STORED_AS, col=REASON_COLUMN, stale=STALE)
