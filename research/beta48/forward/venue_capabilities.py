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

# CAPTURED, and it settles the question against the optimistic reading:
#   "QUOTE_STATUS_EXECUTED and quoteExecuted mean the paired exchange orders
#    were submitted and their order IDs were recorded. THEY DO NOT MEAN THE
#    ORDERS FILLED."
#   "Paired orders are submitted MAKER FIRST, THEN REQUESTER."
#   "If either restRemainder setting is true, unfilled quantity on that side
#    may remain on the book."
# A sequenced two-step submission with a resting remainder is not an atom.
RFQ_QUOTE_EXECUTED_EQUALS_FILL = False
COMBO_ELIMINATES_ORPHAN_RISK_AS_ASSUMPTION = False
COMBO_ATOMIC_FILL_GUARANTEE = "NO_EVIDENCE_NOT_ESTABLISHED"
COMBO_PAIRED_SUBMISSION_IS_SEQUENCED = True
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
# TWO ACCESS FAMILIES. NEVER COLLAPSED.
#
# The old single field `READ_ONLY_CREDENTIAL_ISOLATION = NOT_IDENTIFIED` was
# wrong by averaging: it hid a DOCUMENTED capability behind an undocumented
# one. They are different credentials, on different hosts, with different
# authorization servers, and only one of them has scopes.
RETAIL_API_KEY_READ_ONLY_SCOPE = "NOT_DOCUMENTED"
INSTITUTIONAL_AUTH0_READ_ONLY_SCOPE = "DOCUMENTED"

# Retail markets WebSocket: wss://api.polymarket.us/v1/ws/markets.
# Its TRADE payload is reported to carry maker.side / maker.intent /
# taker.side / taker.intent -- the aggressor fields the maker case needs.
# NOT captured by us: the retail API pages are absent from the venue's own
# llms.txt index and from all 339 pages we fetched, so this is RELAYED.
RETAIL_MARKET_WS_DATA_VALUE = "VERY_HIGH"
RETAIL_WS_TRADE_FIELDS_SOURCE = "RELAYED_NOT_CAPTURED"
RETAIL_READ_ONLY_KEY_SCOPE_DOCUMENTED = "NO"
SAFE_TO_USE_RETAIL_TRADING_CAPABLE_KEY = "NO"
SAFE_TO_CONNECT_WITH_EXISTING_TRADING_CAPABLE_KEY = "NO"
WEBSOCKET_DATA_VALUE = "HIGH"
WEBSOCKET_CONNECTED = False

# ---------------------------------------- institutional gRPC market data --
# CAPTURED. /trader-guide/authentication + /streaming-endpoints/*, 2026-09-16.
GRPC_HOST_PREPROD = "grpc-api.preprod.polymarketexchange.com:443"
GRPC_HOST_PROD = "grpc-api.prod.polymarketexchange.com:443"
GRPC_TLS_REQUIRED = True
GRPC_TOKEN_REFRESH_REQUIRED_EVERY = "3 minutes"
MARKET_DATA_STREAM_REQUIRES_PARTICIPANT_ID = "NO"
MARKET_DATA_STREAM_REQUIRES_KYC = "NO"
SYMBOL_LIMIT_PER_STREAM = 1000
MAX_CONCURRENT_STREAMS_PER_FIRM = 20
INGRESS_MSG_PER_SEC_PER_FIRM = 100

# UNRECONCILED IN THE DOCUMENTATION, so not resolved here: the request field
# says an EMPTY symbol list "subscribes to all instruments", while a separate
# warning caps each stream at 1000 symbols. Whether an empty list bypasses the
# cap or silently truncates is not stated.
EMPTY_SYMBOL_LIST_VS_1000_CAP = NOT_IDENTIFIED

# No market-data latency figure appears in any of the 339 captured pages. The
# only latency number documented is a 5-SECOND STOPGAP ON INBOUND ORDERS,
# which is an order-path control and must never be read as a data-feed SLA.
MARKET_DATA_LATENCY_SLA = NOT_IDENTIFIED
ORDER_LATENCY_STOPGAP = "5s (order path only, NOT a market-data figure)"

# read:l2marketdata is labelled "(premium)". No price, tier or commercial term
# for it appears anywhere in the captured documentation.
L2_SCOPE_COMMERCIAL_TERMS = NOT_IDENTIFIED
TIME_TO_OBTAIN_ACCESS = NOT_IDENTIFIED          # no SLA documented

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
# ------------------------------------------- the Market Maker Program -----
# The formal CFTC-filed programme EXISTS -- application-based, individual
# Market Maker Agreements, two-sided quoting, spread and depth obligations,
# performance-measured. Its TERMS are per-agreement and unknown to us.
#
# The consequence that matters for architecture: public incentives are NOT the
# ceiling, so the economics must be built so a negotiated agreement can be
# inserted later WITHOUT touching strategy code. Five reward channels, all
# separate, none of them a default for another.
NEGOTIATED_MM_PROGRAM_EXISTS = "VERIFIED"
NEGOTIATED_MARKET_MAKER_TERMS = NOT_IDENTIFIED
PUBLIC_INCENTIVES_ARE_ECONOMIC_CEILING = False

REWARD_CHANNELS = (
    "PUBLIC_MAKER_REBATE",
    "PUBLIC_LIQUIDITY_REWARD",
    "PUBLIC_FILL_REWARD",
    "PUBLIC_VOLUME_REWARD",
    "NEGOTIATED_MM_REWARD",
)

# ------------------------------------ exchange-side maker risk controls ----
# CAPTURED. These are the venue's OWN controls and are required for maker mode
# as an exchange-enforced layer. Our kill switches remain required
# independently: a control we do not operate is not a control we can trust to
# fire on our schedule.
EXCHANGE_POST_ONLY = "REQUIRED_FOR_MAKER_MODE"
SELF_MATCH_PREVENTION = "REQUIRED"
MASS_QUOTE_PROTECTION = "REQUIRED_WHERE_AVAILABLE"
OWN_KILL_SWITCHES_STILL_REQUIRED = True
MQP_SEMANTICS = ("cancels remaining eligible resting orders when executed "
                 "quantity X is reached within rolling interval Y, per account")
SMP_SEMANTICS = ("cancels the aggressing OR the passive order before a "
                 "self-match; FIX session-level or per-order tags 7928/8000")

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
