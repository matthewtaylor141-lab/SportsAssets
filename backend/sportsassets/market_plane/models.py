from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

PRICEABLE = "PRICEABLE"
MAPPED_NO_FAIR_VALUE = "MAPPED_BUT_NO_FAIR_VALUE_SOURCE"
MAPPED_SETTLEMENT_UNKNOWN = "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"
EXTERNAL_UNAVAILABLE = "EXTERNAL_DATA_UNAVAILABLE"
CODE_CONTROLLED_GAP = "CODE_CONTROLLED_GAP"
TERMINAL_STATES = (
    PRICEABLE,
    MAPPED_NO_FAIR_VALUE,
    MAPPED_SETTLEMENT_UNKNOWN,
    EXTERNAL_UNAVAILABLE,
    CODE_CONTROLLED_GAP,
)

SOURCE_PMX = "PMX_GRPC"
SOURCE_RETAIL = "RETAIL_PUSH"
SOURCE_REST = "REST_RECOVERY"
SOURCE_PRIORITY = (SOURCE_PMX, SOURCE_RETAIL, SOURCE_REST)


@dataclass(frozen=True)
class ContractMeaning:
    venue: str
    venue_contract_id: str
    sport: str | None = None
    competition: str | None = None
    event_id: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    period: str | None = None
    metric: str | None = None
    operator: str | None = None
    line: float | None = None
    side: str | None = None
    settlement_schema: str | None = None
    raw_market_type: str | None = None
    ontology_version: str = "CONTRACT_ONTOLOGY_V1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BookState:
    source: str
    venue: str
    contract_id: str
    bids: tuple
    offers: tuple
    venue_at: float | None
    received_at: float | None
    normalized_at: float | None
    source_certified: bool
    source_detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["bids"] = list(self.bids)
        out["offers"] = list(self.offers)
        return out
