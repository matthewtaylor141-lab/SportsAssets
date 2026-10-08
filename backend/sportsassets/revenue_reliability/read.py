"""Revenue Reliability V1 -- the live read: run evidence.QUERIES inside a
READ ONLY transaction with a statement timeout and hand the rows to the pure
evidence.build. Absent tables are reported as such (never zero)."""
from __future__ import annotations

import time

from . import evidence as E

STATEMENT_TIMEOUT_MS = 15000
REQUIRED_TABLES = ("paper_strategy_lifecycle_current_v", "xavier_entry_theses", "xavier_value_add",
                   "paper_profitability_models", "paper_profitability_evaluations", "karen_challenges",
                   "paper_counterfactual_variants", "paper_counterfactual_variant_outcomes", "intel_allocations",
                   "paper_fills", "paper_orders", "paper_settlements", "improvement_candidates",
                   "market_plane_registry", "us_premap")

PARAMS = {"lifecycle": ("acct",), "positions": ("days",), "models": ("acct",), "segments": ("acct", "days"),
          "karen": ("days",), "variants": ("days",), "allocations": ("days",), "reconciliation": ("days",),
          "improvements": ()}


async def bankroll(conn, account_id: str) -> tuple[float | None, str]:
    """The paper account's AVAILABLE cash, derived by the ledger itself."""
    from .. import bettor_paper_ledger as L
    try:
        cs = await L.cash_state(conn, account_id)
    except Exception as exc:                                        # noqa: BLE001
        return None, "UNAVAILABLE: %s" % type(exc).__name__
    if not cs.get("entries"):
        return None, "UNAVAILABLE: EMPTY_PAPER_LEDGER"
    return float(cs["available"]), ("paper_ledger available cash (cash - reserved), %d entries, "
                                    "running balance %s" % (cs["entries"], "agrees" if cs["running_balance_agrees"]
                                                            else "DISAGREES"))


async def read(conn, *, account_id: str, now: float | None = None, days: int = E.LOOKBACK_DAYS) -> dict:
    now = float(now if now is not None else time.time())
    tr = conn.transaction() if conn.is_in_transaction() else conn.transaction(readonly=True)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d" % STATEMENT_TIMEOUT_MS)
        missing = [t for t in REQUIRED_TABLES
                   if not await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t)]
        if missing:
            return {"status": "UNAVAILABLE", "why": "TABLES_ABSENT", "missing": missing, "computed_at": now}
        rows = {}
        for k, q in E.QUERIES.items():
            args = [account_id if p == "acct" else int(days) for p in PARAMS[k]]
            rows[k] = [dict(r) for r in await conn.fetch(q, *args)]
        bank, basis = await bankroll(conn, account_id)
    finally:
        await tr.rollback()
    out = E.build(rows, bankroll_usd=bank, bankroll_basis=basis, now=now, account_id=account_id)
    return {"status": "OK", "why": None, "computed_at": now, "data": out}
