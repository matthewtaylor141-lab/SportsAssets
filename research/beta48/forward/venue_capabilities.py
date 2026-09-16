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

# ---------------------------------------------------------------------------
# WHAT LEVEL_4 MBO DOES AND DOES NOT PROVE
# ---------------------------------------------------------------------------
#
# FIX market data is Market-by-Order: every order individually represented,
# unique OrderID (37), MDEntryID (278), a per-order timestamp documented as
# used for time priority within a price level, incremental New/Change/Delete
# (279), Trade entries (269=2) with TradeID (1003) and AggressorSide (2446).
# That is dramatically stronger than aggregated L2.
#
# It is still not the same thing as knowing whether OUR order would have
# filled, and the distinction has a name on each side so the two can never be
# written into one field:
#
#   COUNTERFACTUAL_PASSIVE_FILL      what a simulator concludes about a quote
#                                    that was never submitted
#   ACTUAL_BETTOR_PASSIVE_FILL       what the venue did with an order BETTOR
#                                    actually placed
#
# A perfect MBO replay plus documented matching semantics gives the first at
# high fidelity. It cannot give the second, because the order was not there:
# it was not in the queue, it did not displace anyone, and no counterparty
# reacted to it. Nor does any feed reveal how long the venue would have taken
# to accept it.
LEVEL_4_MBO_WITHOUT_BETTOR_ORDER = {
    "TOUCH": "YES",
    "SPREAD": "YES",
    "DEPTH": "YES",
    "DEPTH_DEPLETION": "YES",
    "QUEUE_AHEAD_AT_HYPOTHETICAL_ENTRY": "YES",
    "TRADE_AGGRESSOR": "YES",
    "COUNTERFACTUAL_PASSIVE_FILL": "YES",
    # Conditional, and the condition is named rather than assumed: it holds
    # only while the venue's documented matching and time-priority semantics
    # are the ones actually in force.
    "TRUE_QUEUE_POSITION_FOR_HYPOTHETICAL_ORDER":
        "YES_CONDITIONAL_ON_DOCUMENTED_MATCHING_AND_TIME_PRIORITY_SEMANTICS",
    "ACTUAL_BETTOR_PASSIVE_FILL": "NO",
    "ACTUAL_BETTOR_ORDER_ACCEPTANCE_LATENCY": "NO",
    "ACTUAL_BETTOR_FILL_PROBABILITY": "NO",
}

FIX_MBO_GIVES = "HIGH_FIDELITY_COUNTERFACTUAL_FILL_SIMULATION"
FIX_MBO_DOES_NOT_GIVE = "DIRECTLY_OBSERVED_BETTOR_FILL_PROBABILITY"

# The three NOs above close only when BETTOR submits real passive orders. That
# is a LATER GATE, recorded so no amount of data quality is mistaken for it.
# Recording the requirement is not authorisation to meet it.
MICRO_LIVE_REQUIRED_FOR_FINAL_EXECUTION_VALIDATION = True
MICRO_LIVE_AUTHORIZED = False

# ---------------------------------------------------------------------------
# gRPC `unaggregated` -- a high-value UNRESOLVED feature, not a fourth level
# ---------------------------------------------------------------------------
#
# The gRPC docs offer unaggregated=True, described as "receive raw order
# book". The documented BookEntry schema, however, carries only `px` and
# `qty`, and describes qty as the AGGREGATE quantity at that price level. Two
# statements that cannot both be fully true of the same message, and the docs
# do not reconcile them.
#
# Reading "raw order book" as market-by-order would hand us OrderID and time
# priority on the strength of one adjective. It stays unresolved.
GRPC_UNAGGREGATED_EXISTS = "YES"
GRPC_UNAGGREGATED_EXACT_SEMANTICS = NOT_IDENTIFIED
GRPC_UNAGGREGATED_HAS_ORDER_ID = "NO_DOCUMENTED_FIELD"
GRPC_UNAGGREGATED_HAS_ORDER_TIMESTAMP = "NO_DOCUMENTED_FIELD"
GRPC_UNAGGREGATED_GIVES_TRUE_TIME_PRIORITY = "NOT_ESTABLISHED"
GRPC_UNAGGREGATED_IS_LEVEL_4 = False

# THE FIRST SAFE STREAMING EXPERIMENT, specified now so it is not designed in
# the excitement of finally having a credential. Same symbol, same window,
# aggregated=False against unaggregated=True, compared on:
GRPC_UNAGGREGATED_EXPERIMENT = (
    "NUMBER_OF_ENTRIES_PER_PRICE",
    "SUM_QTY_BY_PRICE",
    "ORDERING_STABILITY",
    "ENTRY_CHURN",
    "UPDATE_FREQUENCY",
    "TRANSACT_TIME",
    "RECONCILIATION_TO_AGGREGATED_BOOK",
)
GRPC_UNAGGREGATED_QUESTION = (
    "DOES_UNAGGREGATED_GRPC_IMPROVE_QUEUE_DEPLETION_SIMULATION?")
GRPC_UNAGGREGATED_ANSWER = NOT_IDENTIFIED

# Order identity and time priority are INFERRED FROM NOTHING. They are read
# off documented fields plus observed runtime behaviour, or they stay unset.
GRPC_ORDER_IDENTITY_MAY_BE_INFERRED = False

# ---------------------------------------------------------------------------
# A DOCUMENTED CAPABILITY IS NOT A GRANT
# ---------------------------------------------------------------------------
#
# The scope list is confirmed and enforcement is strict: order submission
# requires write:orders, market-data streaming requires read:marketdata, and a
# token without a scope is refused by the venue rather than by us. All of that
# is a fact about the venue. None of it is a fact about BETTOR.
DOCUMENTED_SCOPES = (
    "read:marketdata", "read:l2marketdata", "read:instruments",
    "read:orders", "write:orders", "read:reports", "read:positions",
    "read:dropcopy",
)
SCOPE_ENFORCEMENT = "STRICT_SERVER_SIDE"
ORDER_SUBMISSION_REQUIRES = "write:orders"
MARKET_DATA_STREAMING_REQUIRES = "read:marketdata"

VENUE_ENFORCED_READ_ONLY_CAPABILITY = "DOCUMENTED"
CAPABILITY_DOCUMENTED = "YES"
BETTOR_GRANTED_SCOPE = NOT_IDENTIFIED
BETTOR_CREDENTIAL_INSTALLED = "NO"
BETTOR_CONNECTION_AUTHORIZED = "NO"

# ---------------------------------------------------------------------------
# THE PUBLIC EXECUTION TAPE
# ---------------------------------------------------------------------------
#
# CAPTURED, not relayed: docs.polymarket.us/faqs/execution-tape.md, response
# sha256 5ff446a6abd4fbca..., in the 340-page capture on branch
# beta48-capability/docs-35047389408.
PUBLIC_TIME_SALES_AVAILABLE = "YES"
PUBLIC_TIME_SALES_AUTH_REQUIRED = "NO"
TAPE_EXECUTION_TIMESTAMP = "YES"
TAPE_EXECUTION_PRICE = "YES"
TAPE_EXECUTION_QUANTITY = "YES"
TAPE_SYMBOL = "YES"
TAPE_TRADE_SIDE = "NO"
TAPE_AGGRESSOR = "NO"
TAPE_PARTICIPANT_IDENTITY = "NO"

# WHAT THE CAPTURED PAGE ADDS THAT THE RELAY DID NOT. It is not an endpoint.
# It is a DAILY CSV FILE, `YYYYMMDD-time-and-sales.csv`, offered from a THIRD
# host we had never contacted. Each of these changes the design.
TAPE_DELIVERY = "DAILY_CSV_FILE_DOWNLOAD"
TAPE_IS_AN_API = False
TAPE_IS_REALTIME = False
TAPE_HOST_IS_A_THIRD_HOST = "www.polymarketexchange.com"
TAPE_FILENAME_CONVENTION = "YYYYMMDD-time-and-sales.csv"
TAPE_DOWNLOAD_URL_DOCUMENTED = "NO"        # only a landing page is linked
TAPE_URL_MAY_BE_CONSTRUCTED_FROM_CONVENTION = False

# The venue's reporting day is "as of 5:00 PM Eastern Time each business day"
# (captured: learn/trading/access-and-limits/trading-hours.md). A file named
# 20260113 is therefore NOT the UTC day 2026-01-13. This is the C-6 off-by-a-
# day error in a new place, and it would misalign the whole tape by up to
# seven hours in a way that looks like ordinary noise.
TAPE_BUSINESS_DATE_CUTOVER_ET = "17:00 America/New_York"
TAPE_BUSINESS_DATE_IS_A_UTC_DAY = False

# The three questions that decide whether any of this is usable, all open
# until a real file is read. The third is the single point of failure: if the
# tape's Symbol does not join to the board's market slug, the pipeline yields
# nothing at all.
TAPE_TIMESTAMP_PRECISION = NOT_IDENTIFIED
TAPE_PUBLICATION_LATENCY = NOT_IDENTIFIED
TAPE_SYMBOL_JOINS_TO_MARKET_SLUG = NOT_IDENTIFIED
TAPE_JOIN_IS_THE_SINGLE_POINT_OF_FAILURE = True

# A second free artifact found in the same capture, 21 columns including open
# interest, settlement price and the day's low/high bid and offer.
PUBLIC_DAILY_MARKET_REPORT_AVAILABLE = "YES"
DAILY_MARKET_REPORT_DELIVERY = "DAILY_CSV_FILE_DOWNLOAD"

# The tape moves counterfactual terms only. These are unchanged by it.
ACTUAL_BETTOR_FILL = NOT_IDENTIFIED
ACTUAL_BETTOR_FILL_PROBABILITY_FROM_TAPE = NOT_IDENTIFIED
TRADE_AGGRESSOR_FROM_TAPE = NOT_IDENTIFIED
ACTUAL_BETTOR_QUEUE_POSITION_FROM_TAPE = NOT_IDENTIFIED

# ---------------------------------------------------------------------------
# MATCHING PRIORITY, AND ITS DOCUMENTED EXCEPTION
# ---------------------------------------------------------------------------
CURRENT_MATCHING_PRIORITY = "PRICE_TIME"
BETTER_PRICE_PRIORITY = "YES"
SAME_PRICE_TIME_PRIORITY = "YES"
# The Rulebook permits a different algorithm for a particular Contract after
# advance notice, so price-time is the current rule and not a law of nature.
MATCHING_ALGORITHM_EXCEPTION_POSSIBLE = "YES"
CONTRACT_SPECIFIC_ALGORITHM_OVERRIDE_POSSIBLE = "YES"
CHECK_PRODUCT_SPECIFIC_MATCHING_NOTICE = "REQUIRED"
PRODUCT_NOTICE_CHECK_REQUIRED_BEFORE_LIVE = "YES"

# ---------------------------------------------------------------------------
# THE TARGET SIZE DOCUMENTATION CONFLICT
# ---------------------------------------------------------------------------
#
# Two official pages describe the same parameter incompatibly. The generic
# incentives page reads it as a MAXIMUM per side that counts toward scoring;
# the dedicated Liquidity Incentive Program page gives an algorithm in which it
# is a MINIMUM AGGREGATE threshold that defines the qualifying price range.
#
# We implement the DETAILED page, because it is the one that states the
# procedure. We do NOT rewrite either page to match the other, and we do not
# retire the conflict on our own authority: it is resolved by the venue or by
# runtime evidence, and until then it is a live risk to every depth-panel
# figure, since the two readings disagree about which orders score at all.
TARGET_SIZE_GENERIC_PAGE = "MAXIMUM_DESCRIPTION"
TARGET_SIZE_DETAILED_LIQUIDITY_PAGE = "MINIMUM_AGGREGATE_THRESHOLD"
DOC_CONFLICT_TARGET_SIZE = "YES"
TARGET_SIZE_IMPLEMENTED_FROM = "DETAILED_LIQUIDITY_PROGRAM_PAGE"
DOC_CONFLICT_TARGET_SIZE_RESOLVED_BY = NOT_IDENTIFIED

# The liquidity programme's mechanics, from the detailed page.
LIQUIDITY_SIDES_NORMALIZED_INDEPENDENTLY = True
LIQUIDITY_SCORED_EVERY_SECOND = True
LIQUIDITY_TARGET_SIZE_USES_RAW_SIZE = True
LIQUIDITY_DISCOUNT_AFFECTS_SCORE_NOT_THRESHOLD = True
LIQUIDITY_BEYOND_RANGE_SCORES_ZERO = True
LIQUIDITY_INDIVIDUAL_SIZE_CAP_INSIDE_RANGE = "NONE"
LIQUIDITY_REWARD_IS_PROPORTIONAL_TO_SCORE = True
LIQUIDITY_PARAMETERS_CHANGE_BETWEEN_PERIODS = True

# THE QUESTION THIS REFRAMES. Not "does BETTOR have a resting quote?" but "is
# it inside the qualifying range, and what fraction of TOTAL score does it
# contribute?" The second needs every participant's qualifying score through
# time, which no public endpoint publishes.
ACTUAL_REWARD_SHARE = NOT_IDENTIFIED

# ---------------------------------------------------------------------------
# MAKER-ONLY EXECUTION CONTROL (architecture only; nothing is submitted)
# ---------------------------------------------------------------------------
MAKER_MODE_POST_ONLY_CONTROL = "REQUIRED"
POST_ONLY_FIELD = "participateDontInitiate"
TAKER_EXECUTION_REQUIRES_EXPLICIT_ENGINE_SELECTION = True
SELF_MATCH_PREVENTION_REQUIRED_IN_PRODUCTION = True

# ---------------------------------------------------------------------------
# MASS QUOTE PROTECTION IS A BACKSTOP, NOT A FILL CAP
# ---------------------------------------------------------------------------
#
# Executions accumulate over a rolling interval; when the threshold is reached
# the remaining resting orders in the bucket are cancelled -- but the execution
# that BREACHED it still completes, so one aggressive sweep can fill MORE than
# the threshold before any cancellation happens. Treating MQP as a maximum
# position change is therefore an inventory cap that does not cap inventory.
MQP_IS_HARD_MAX_FILL_LIMIT = False
MQP_TRIGGERING_EXECUTION_COMPLETES = True
MQP_REMAINING_QUOTES_CANCEL_AFTER_TRIGGER = True
MQP_IS_SUFFICIENT_AS_SOLE_INVENTORY_CAP = False

BETTOR_OWN_REQUIRED_CONTROLS = (
    "POSITION_LIMITS", "EVENT_LIMITS", "CORRELATION_LIMITS", "LOSS_LIMITS",
    "STALE_DATA_KILL", "QUOTE_AGE_LIMIT", "INVENTORY_KILL",
)

# ---------------------------------------------------------------------------
# PUBLIC SPORTS REFERENCE DATA -- SPEC ONLY, NOT BUILT
# ---------------------------------------------------------------------------
#
# A separate read-only coverage probe, specified so it is not designed later
# under pressure. It measures JOIN RATES and builds no model, and it does not
# touch Engine B. Every rate is NOT_IDENTIFIED because runtime capture, not a
# schema, decides which fields actually come back.
SPORTS_COVERAGE_PROBE_STATUS = "SPECIFIED_NOT_BUILT"
SPORTS_COVERAGE_PROBE_MEASURES = (
    "SPORT_JOIN_RATE", "LEAGUE_JOIN_RATE", "EVENT_JOIN_RATE",
    "TEAM_JOIN_RATE", "GAME_START_JOIN_RATE", "MARKET_TYPE_JOIN_RATE",
    "LINE_JOIN_RATE", "PROVIDER_ID_JOIN_RATE",
    # Only if the captured event endpoints actually return them. Presence in a
    # schema is not presence in a response.
    "LIVE_STATE_JOIN_RATE", "SCORE_JOIN_RATE", "ELAPSED_JOIN_RATE",
    "PERIOD_JOIN_RATE",
)
SPORTS_LIVE_STATE_FIELDS_ASSUMED_PRESENT = False
