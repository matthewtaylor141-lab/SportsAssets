"""Decision engine: threshold check, band/league filters, sizing, caps.

Pure functions over config — the shadow runner and (eventually) live
execution call the SAME code path, so shadow evidence transfers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


@dataclass
class Policy:
    bands: dict
    leagues: dict
    risk: dict

    @classmethod
    def load(cls) -> "Policy":
        return cls(load_yaml("bands.yaml"), load_yaml("leagues.yaml"), load_yaml("risk.yaml"))

    def band_of(self, price: float) -> str:
        lo = int(price * 20) * 5 / 100  # 5-cent bands
        return f"{lo:.2f}-{lo + 0.05:.2f}"

    def band_threshold(self, price: float) -> float | None:
        """Min edge for this entry price; None = band not tradeable."""
        band = self.band_of(price)
        for dz in self.bands.get("dead_zones", []):
            lo, hi = (float(x) for x in dz.split("-"))
            if lo <= price < hi:
                return None
        for key, cfg in self.bands.get("tradeable", {}).items():
            lo, hi = (float(x) for x in key.split("-"))
            if lo <= price < hi:
                return float(cfg["min_edge_threshold"])
        return None  # unlisted band — not proven, don't trade

    def league_allowed(self, code: str | None) -> str:
        """'allow' | 'block' | 'shadow_only' (unknown)."""
        if not code:
            return self.leagues.get("unknown_league_policy", "shadow_only")
        if code in self.leagues.get("blocklist", []):
            return "block"
        for group in (self.leagues.get("allowlist") or {}).values():
            if code in group:
                return "allow"
        return self.leagues.get("unknown_league_policy", "shadow_only")


@dataclass
class Decision:
    trade: bool
    reason: str
    size_usd: float = 0.0
    band: str = ""


@dataclass
class ExposureBook:
    """Running per-market/day exposure for cap enforcement."""

    per_market: dict[str, float] = field(default_factory=dict)
    day_total: float = 0.0

    def add(self, market_id: str, usd: float) -> None:
        self.per_market[market_id] = self.per_market.get(market_id, 0.0) + usd
        self.day_total += usd


def decide(
    policy: Policy,
    exposure: ExposureBook,
    market_id: str,
    league_code: str | None,
    price: float,
    fair: float,
    venue_fee: float = 0.0,
) -> Decision:
    """The entry rule: fair - price - fee >= threshold(band), filters, caps."""
    band = policy.band_of(price)
    league_status = policy.league_allowed(league_code)
    if league_status == "block":
        return Decision(False, f"league {league_code} blocked", band=band)
    threshold = policy.band_threshold(price)
    if threshold is None:
        return Decision(False, f"band {band} dead/unproven", band=band)
    edge = fair - price - venue_fee
    if edge < threshold:
        return Decision(False, f"edge {edge:.3f} < threshold {threshold:.3f}", band=band)

    risk = policy.risk
    size = float(risk.get("per_fill_usd_default", 5000))
    market_room = float(risk.get("per_market_exposure_usd", 25000)) - exposure.per_market.get(
        market_id, 0.0
    )
    day_room = float(risk.get("per_day_deployment_usd", 250000)) - exposure.day_total
    size = min(size, market_room, day_room)
    if size <= 0:
        return Decision(False, "exposure caps reached", band=band)
    reason = "shadow_only league" if league_status == "shadow_only" else "ok"
    return Decision(True, reason, size_usd=round(size, 2), band=band)
