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

    LIVE_BOOK_STREAM(ctx, cand, now=...) -> dict   (sync)

The decision's ONE identity answer and ONE resident institutional stream
read (`live_book_evidence.observe`), installed beside it. When it carries a
current `observation`, the decision modules price the ACTUAL lane's
admission facts from it (P5 C12); otherwise nothing changes.


    CANONICAL_DECISION(conn, **inputs) -> dict | None              (awaitable)
    CANONICAL_ENTRY_ADAPTERS(conn, intent, *, paper_order, paper_result)
    CANONICAL_MANAGEMENT_RECORD(conn, intent) -> bool              (awaitable)
    CANONICAL_MANAGEMENT_ADAPTERS(conn, intent, *, taken, open_qty)

R30 LIVE PARITY (`live_parity.install`, called by `execution_intent.start`):
build + record the ONE canonical decision intent of a qualified ENTER (the
paper order is then built FROM it), record a canonical management intent,
and record what BOTH execution adapters did with each (the SMALL LIVE adapter
is SHADOW) plus their parity. Absent, the paper decision and management
proceed exactly as before and no live proposal exists.
"""

DECISION_HOOK = None
LIVE_BOOK_EVIDENCE = None
LIVE_BOOK_STREAM = None
CANONICAL_DECISION = None
CANONICAL_ENTRY_ADAPTERS = None
CANONICAL_MANAGEMENT_RECORD = None
CANONICAL_MANAGEMENT_ADAPTERS = None
