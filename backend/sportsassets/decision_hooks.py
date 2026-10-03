"""THE ONE SEAM BETWEEN A QUALIFIED PAPER-LANE DECISION AND EXECUTION.

ONE DECISION -> PAPER + ACTUAL (owner correction 2026-10-03). The paper
decision modules (`agents/paper_benchmark`, `agents/paper_explore`) must never
import an execution, venue or funded module; the process that executes
installs a callable here (`execution_intent.start`) and the decision modules
call it with the qualified decision's payload. This module imports NOTHING
and does no I/O: it only holds the installed callable.

    DECISION_HOOK(conn, payload) -> dict   (awaitable)

Absent (a process that does not execute, or a test), a decision proceeds
paper-only and records that no execution hook ran in its process.
"""

DECISION_HOOK = None
