#!/usr/bin/env python3
"""Venue capabilities BETTOR may one day use. Nothing here is activated.

This module holds LABELS, not behaviour. It exists so that a capability's
status is a value a test can assert rather than a sentence in a report that
drifts. Nothing in it opens a connection, holds a credential, or knows how to.

THE RULE THAT GOVERNS EVERY ENTRY: a cost that is documented is not the same
as a benefit that is documented, and neither is the same as a behaviour we
have observed. Where the venue plainly charges for something, that is recorded
as verified. Where the thing it buys is unmeasured, that is recorded as
NOT_IDENTIFIED -- NOT as zero. Recording an unmeasured benefit as zero is the
same error as recording an unmeasured cost as zero, just pointed the other way.
"""
from __future__ import annotations

NOT_IDENTIFIED = "NOT_IDENTIFIED"


# ------------------------------------------------------ combos and RFQ -----
#
# The COST is captured from the venue's own fee page and dated. The BENEFIT --
# whether paying it buys materially lower legging/orphan risk -- is the open
# question, and is not prejudged in either direction.
UPCOMING_COMBO_TAKER_COST_PREMIUM = "VERIFIED_FROM_PRIMARY_DOCS"
COMBO_ORPHAN_RISK_REDUCTION = NOT_IDENTIFIED
COMBO_EXECUTION_ATOMICITY = NOT_IDENTIFIED
COMBO_PARTIAL_FILL_BEHAVIOR = NOT_IDENTIFIED
COMBO_NET_VALUE_VS_SINGLE_LEG_EXECUTION = NOT_IDENTIFIED
COMBO_REST_REMAINDER_BEHAVIOR = NOT_IDENTIFIED
COMBO_PAIR_SUBMISSION_SEMANTICS = NOT_IDENTIFIED
COMBO_LAST_LOOK_RISK = NOT_IDENTIFIED
COMBO_EXECUTION_ATTRIBUTION_SOURCE = "DROP_COPY_RELAYED_NOT_CAPTURED"
COMBO_REQUIRED_AUTH_SCOPE = NOT_IDENTIFIED
COMBO_RETAIL_OR_INSTITUTIONAL_ACCESS = NOT_IDENTIFIED

# MEASURED on the observed board prefix, segment 35042094434: comboEnabled is
# present on 20,000/20,000 rows and TRUE on 5,113 (25.6%).
COMBO_ENABLED_SHARE_OF_OBSERVED_PREFIX = "5113/20000 = 25.6%"


# ------------------------------------------------- the markets WebSocket ---
#
# Documented to carry full order book, lite BBO, trades, maker side, maker
# intent, taker side and taker intent -- which is four of the five terms the
# maker case currently cannot measure at all. The value is not in doubt.
#
# What IS in doubt is whether it can be reached without holding a credential
# that can also trade. The current capability boundary is not a policy, it is
# a PROPERTY: fwd_collect.py has no code path that could construct an auth
# header, imports no `os`, and is proved so by AST scan as a gate before any
# venue contact. A trading-capable key dissolves that property, and a promise
# not to use it is not the same guarantee at all.
WEBSOCKET_DATA_VALUE = "HIGH"
RETAIL_READ_ONLY_KEY_SCOPE_DOCUMENTED = "NO"
READ_ONLY_CREDENTIAL_ISOLATION = NOT_IDENTIFIED
SAFE_TO_CONNECT_WITH_EXISTING_TRADING_CAPABLE_KEY = "NO"
WEBSOCKET_CONNECTED = False

# The three routes that could change the answer. All are research questions;
# none has been attempted, and none may be attempted without separate approval.
WEBSOCKET_ISOLATION_ROUTES = {
    "A_SCOPE_LIMITED_KEYS_IN_DEVELOPER_PORTAL": NOT_IDENTIFIED,
    "B_INSTITUTIONAL_OAUTH_READ_ONLY_SCOPE": NOT_IDENTIFIED,
    # C is ours, not the venue's: a fixed-function signing broker plus network
    # path restriction could make the CAPABILITY non-trading even if the
    # underlying credential is broader. That is a containment argument, and it
    # is weaker than a scope the venue enforces -- it must be argued on its own
    # merits, not assumed equivalent.
    "C_FIXED_FUNCTION_BROKER_PLUS_PATH_RESTRICTION": NOT_IDENTIFIED,
}

# What the socket would answer, listed so the cost of NOT having it is legible.
WEBSOCKET_WOULD_MEASURE = (
    "TRADE_AGGRESSOR", "QUEUE_DEPLETION_PROXY", "TOUCH_TO_TRADE",
    "POST_TRADE_MARKOUT", "ADVERSE_SELECTION", "PASSIVE_FILL_INFERENCE",
)


# ------------------------------------------------- collateral and margin ---
#
# This belongs in the CAPITAL layer, never the alpha layer. It reduces a margin
# REQUIREMENT; it does not reduce economic risk, and it does not create edge.
# As a multiplier it scales whatever sign the trading economics already have --
# which means it can improve a profitable engine and will faithfully amplify a
# losing one.
COLLATERAL_RETURN_CLASSIFICATION = "CAPITAL_EFFICIENCY_MULTIPLIER"
COLLATERAL_RETURN_IS_ALPHA_SOURCE = False

MARGIN_WITHOUT_OFFSET = NOT_IDENTIFIED
MARGIN_WITH_OFFSET = NOT_IDENTIFIED
BUYING_POWER_FREED = NOT_IDENTIFIED
CAPITAL_REUSE_ALLOWED = NOT_IDENTIFIED
CLOSE_POSITION_COLLATERAL_REQUIREMENT = NOT_IDENTIFIED
NET_PNL_PER_WORKING_CAPITAL_DOLLAR_PER_HOUR = NOT_IDENTIFIED

# The restrictions, kept as named constraints rather than prose, because each
# one is a way the freed capital can turn into an obligation.
CAPITAL_REUSE_RESTRICTIONS = {
    # Freed power may be spent in OTHER events only.
    "NOT_IN_SAME_EVENT_THAT_GENERATED_IT": "DOCUMENTED_RELAYED",
    # Closing the offsetting leg may require restoring the freed collateral.
    "CLOSING_OFFSET_MAY_REQUIRE_RESTORING_COLLATERAL": "DOCUMENTED_RELAYED",
    # And the close can be REFUSED if that capital is already deployed.
    "CLOSE_MAY_BE_REJECTED_IF_FREED_CAPITAL_DEPLOYED": "DOCUMENTED_RELAYED",
}

# THE HAZARD THIS CREATES, named so the allocator is built against it.
#
# Deploying freed buying power elsewhere can make an offsetting leg
# UNCLOSEABLE at the moment we most want to close it. That converts a margin
# optimisation into a liquidity obligation, and it does so exactly when the
# book is under stress -- the same shape as every forced-exit failure this
# programme has already recorded. Any allocator that spends freed collateral
# must therefore reserve the capacity to unwind the position that freed it.
FREED_CAPITAL_MAY_CREATE_CLOSE_OBLIGATION = True
ALLOCATOR_MUST_RESERVE_UNWIND_CAPACITY = True
