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

    LIVE_BOOK_EVIDENCE(ctx, cand, obs=..., now=...) -> dict | None   (sync)

The live book-currency evidence (P5_LIVE_STREAM_BOOK_V1) for the decision's
contract, installed beside DECISION_HOOK by `execution_intent.start` as
`live_book_evidence.for_decision`. The decision modules record what it
returns in the intent's admission facts and decide nothing from it.
"""

DECISION_HOOK = None
LIVE_BOOK_EVIDENCE = None
