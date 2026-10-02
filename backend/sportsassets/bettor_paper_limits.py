"""Owner's current capital policy for the main simulated account only.

The session's original config remains immutable. These effective settings are
separately versioned and recorded; they never create capital or venue authority.
"""
from copy import deepcopy

ACCOUNT_ID = "paper_acct_main"
VERSION = "PAPER_CAPITAL_1000_AVERAGE_TARGET_V1"
ENTRY_USD = 1000.0


def describe(account_id):
    if account_id != ACCOUNT_ID:
        return None
    return {"version": VERSION, "account_id": ACCOUNT_ID,
            "entry_target_usd_including_fees": ENTRY_USD,
            "entry_target_is": "AVERAGE_SIZING_OBJECTIVE_NOT_A_CEILING",
            "per_order_cap_usd": None, "positions_per_fixture": None,
            "sampling_inclusion_probability": 1.0,
            "aggregate_exposure_cap_usd": None, "realized_loss_stop_usd": None,
            "per_market_cap_usd": None, "per_fixture_cap_usd": None,
            "max_concurrent_groups": None, "hedge_reserve_fraction": 0.0,
            "funding_boundary": "AVAILABLE_SIMULATED_CASH",
            "scope": "NEW_PAPER_ORDERS_ONLY"}


def effective_caps(caps, account_id, role):
    result = dict(caps or {})
    if account_id == ACCOUNT_ID:
        result.update(per_order_cap_usd=None,
                      per_market_cap_usd=None, per_fixture_cap_usd=None,
                      max_concurrent_groups=None, hedge_reserve_fraction=0.0)
    return result


def effective_config(config, account_id):
    result = deepcopy(config)
    if account_id == ACCOUNT_ID:
        result["risk"] = effective_caps(result.get("risk"), account_id, "ENTRY")
        result.setdefault("entry", {})["target_order_usd"] = ENTRY_USD
        result["capital_policy"] = describe(account_id)
    return result


def uses_owner_policy(account_id):
    return account_id == ACCOUNT_ID
