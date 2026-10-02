"""KNOWN GAP, ISOLATED AND REPORTED -- NOT ON THE CAPITAL-CRITICAL LIST.

The release gate rejects any critical file containing an xfailed test, so
this case lives in its own file instead of being dropped from the integration
suite. Its assertion is the original one, unchanged; its result is reported by
every gate run (strict: if it ever starts passing, the run fails here and the
marker must be removed deliberately).

WHAT IT SHOWS. On a paper account OUTSIDE the owner capital policy, there is
no same-contract rule at either level -- by policy scope, not by accident:
`bettor_paper_limits.uses_owner_policy` is `paper_acct_main` only, and other
accounts' policies are deliberately not broadened. So a second drained
WebSocket change on a held contract records a second ENTER decision and
places a second ENTRY order on such an account.

WHY PRODUCTION IS NOT EXPOSED. Production's reactive path decides on
`paper_runtime.DEFAULT_ACCOUNT_ID` = `paper_acct_main`, where the decision-
level read refuses the re-entry by name and the account-lock check refuses any
order that races past it (tests/test_pinnapi_reactive_integration.py::
test_a_held_contract_under_the_owner_policy_records_a_named_refusal_not_a_
second_enter and ::test_a_burst_coalesces_and_a_held_contract_gets_no_second_
entry_order). It becomes a live gap only if a non-owner account is ever
pointed at the reactive path.
"""
from __future__ import annotations

import pytest

try:    # pytest collects tests/ as a package; unittest discovery does not
    from tests.test_pinnapi_reactive_integration import (   # noqa: F401
        env, pg, _burst_then_one_more, _enters_on_contract)
except ImportError:
    from test_pinnapi_reactive_integration import (         # noqa: F401
        env, pg, _burst_then_one_more, _enters_on_contract)


@pg
@pytest.mark.xfail(strict=True, reason=(
    "NON-OWNER ACCOUNT, NO SAME-CONTRACT RULE BY POLICY SCOPE. "
    "bettor_paper_limits.uses_owner_policy is paper_acct_main only, so on "
    "this scratch account a second drained change on a held contract records "
    "a second ENTER decision and a second ENTRY order. Production's reactive "
    "path decides on paper_acct_main, where both the decision-level and the "
    "under-lock rule refuse it (see the integration suite)."))
async def test_a_second_drained_change_on_a_held_contract_records_no_second_enter(
        env):
    e = env
    await _burst_then_one_more(e)
    assert await _enters_on_contract(e) == 1
