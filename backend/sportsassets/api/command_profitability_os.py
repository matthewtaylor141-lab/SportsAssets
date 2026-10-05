"""THE PROFITABILITY OS READ: GET /api/command/profitability/os.

ONE READ-ONLY PAGE, ONE SECTION PER PROFITABILITY COMPONENT (the logic is
sportsassets/pos_os/*): champion_challenger, regime_detection,
execution_cost_learning, capital_hour_optimizer, capacity_frontier,
post_trade_attribution, counterfactual_twin, data_quality_sentinel,
experiment_governance, model_economic_drift, scenario_correlation,
execution_policy_league, expected_profit_clock, autonomy_health,
release_incident_twin. Each answers OK / EMPTY / UNAVAILABLE with why and
says whether the component EXISTS elsewhere in the line or is BUILT in
pos_os.

READ ONLY. COMMAND session auth (agents_core.require_read: 401 without a
session), GET only. The request runs inside ONE `BEGIN READ ONLY`
transaction with a bounded statement_timeout; each input is read in its own
savepoint, so a failed read leaves that section UNAVAILABLE (named) and the
others intact -- never zeros. A connection that cannot be had answers
UNAVAILABLE for the whole page with the reason.

  ?account_id=   the paper account (default the main paper account)
  ?window_days=  the lookback (1..90, default 30)

WHAT THIS MODULE CANNOT DO, BY CONSTRUCTION. It imports no order, venue,
execution, ledger or paper module (tests/test_pos_os_authority.py), issues
no write, and holds no approval, activation, sizing, cap or gate path. The
existing /api/command/profitability/* routes are unchanged.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query, Response

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/profitability/os"
STATEMENT_TIMEOUT_MS = 8000


async def _read_only(fn):
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            return await fn(conn)


@router.get(PATH, dependencies=[Depends(require_read)])
async def profitability_os(
        response: Response = None,
        account_id: str = Query(default="paper_acct_main", min_length=1,
                                max_length=120),
        window_days: float = Query(default=30.0, ge=1.0, le=90.0)) -> dict:
    from ..pos_os import assemble as A
    from ..pos_os import common as C
    from ..pos_os import reads as R
    from ..profitability import common as PC

    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    now = time.time()
    try:
        inputs = await _read_only(lambda conn: R.load_all(
            conn, account_id=account_id, now=now,
            window_days=float(window_days)))
    except Exception as exc:                                    # noqa: BLE001
        return PC.envelope(C.UNAVAILABLE, "%s:%s" % (
            type(exc).__name__, str(exc)[:160]), computed_at=now, data=None,
            sections={n: C.section(C.UNAVAILABLE, "%s:DATABASE" %
                                   C.R_INPUT_NOT_READ, audit=a,
                                   implemented_by=w)
                      for n, _, a, w in A.SECTIONS},
            version=A.VERSION)
    sections = A.build(inputs, now=now)
    summ = A.summary(sections)
    status = (C.OK if summ["by_status"].get(C.OK) else
              C.UNAVAILABLE if len(summ["by_status"].get(C.UNAVAILABLE, []))
              == len(sections) else C.EMPTY)
    return PC.envelope(
        status, None if status == C.OK else "NO_SECTION_COMPUTED",
        computed_at=now, data=summ, sections=sections, version=A.VERSION,
        scope={"account_id": account_id, "book": "PAPER",
               "window_days": float(window_days)},
        input_errors=inputs.get("_errors") or {},
        authority_note="observes and recommends only: no cap, size, "
                       "allowlist, EV, settlement, freshness or risk gate "
                       "reads this page",
        summed_across_books=False)
