from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Sequence

GREEN="GREEN"
RED="RED"
UNKNOWN="UNKNOWN"
CASH="CASH"
PAPER_SHADOW_ONLY="PAPER_SHADOW_ONLY"
CAPITAL_CANDIDATE="CAPITAL_CANDIDATE"

@dataclass(frozen=True)
class VenueHealth:
    venue: str
    current_books: int
    required_books: int
    stale_books: int
    gaps: int
    rate_limited: bool
    as_of: float
    source: str

@dataclass(frozen=True)
class PositionTruth:
    source: str
    claim_key: str
    qty: Decimal
    value: Decimal | None
    as_of: float

@dataclass(frozen=True)
class ClaimExposure:
    claim_key: str
    event_key: str
    payoff_fingerprint: str
    venue: str
    instrument_id: str
    qty: Decimal
    signed_notional: Decimal
    alias_group: str

@dataclass(frozen=True)
class StrategyEvidence:
    strategy: str
    independent_events: int
    expected_pnl: Decimal
    realized_pnl: Decimal
    lower_bound_pnl: Decimal | None
    capacity: Decimal | None
    capital_hours: Decimal | None
    lifecycle: str

@dataclass(frozen=True)
class ReleaseEvidence:
    tested_sha: str
    release_sha: str
    deployed_sha: str
    accepted_base_sha: str
    is_descendant_of_base: bool
    backend_tests_green: bool
    capital_critical_green: bool
    commit_guard_green: bool
    engine_diagnostic_green: bool
    migration_fingerprint_match: bool

@dataclass(frozen=True)
class TwinEvidence:
    compared: int
    matched: int
    optimistic_false_fills: int
    lookahead_violations: int
    mismatch_reasons: Mapping[str,int] = field(default_factory=dict)

@dataclass(frozen=True)
class ReadinessInput:
    release_ok: bool
    runtime_ok: bool
    market_data_ok: bool
    held_freshness_ok: bool
    priority_freshness_ok: bool
    settlement_ok: bool
    twin_ok: bool
    reconciliation_ok: bool
    positive_edge_ok: bool
    capacity_ok: bool
    claim_exposure_ok: bool
    profit_breakers_ok: bool
    live_authority_still_shadow: bool
    historical_paper_immutable: bool
