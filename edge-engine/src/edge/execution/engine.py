"""Decision engine: threshold check, band/league filters, sizing, caps.

Pure functions over config — the shadow runner and (eventually) live
execution call the SAME code path, so shadow evidence transfers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def _config_dir() -> Path:
    """Locate config/: env override, repo layout, then cwd — the package may
    run from the source tree or as an installed distribution."""
    candidates = [
        os.environ.get("EDGE_CONFIG_DIR"),
        Path(__file__).resolve().parents[3] / "config",
        Path.cwd() / "config",
    ]
    for c in candidates:
        if c and Path(c).is_dir():
            return Path(c)
    raise FileNotFoundError(
        "edge-engine config directory not found; set EDGE_CONFIG_DIR"
    )


def load_yaml(name: str) -> dict:
    with open(_config_dir() / name) as f:
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

    def band_threshold(self, price: float, category: str = "moneyline") -> float | None:
        """Min edge for this entry price in this trade category; None = not
        tradeable. Moneyline uses the measured swisstony bands; spread/total
        use their own windows (config `categories`) — the 40-45c dead zone
        is a moneyline phenomenon and does NOT apply to derivatives."""
        if category != "moneyline":
            cfg = (self.bands.get("categories") or {}).get(category)
            if cfg is None:
                return None  # unknown category — never tradeable
            src = cfg
        else:
            src = self.bands
        for dz in src.get("dead_zones", []) or []:
            lo, hi = (float(x) for x in dz.split("-"))
            if lo <= price < hi:
                return None
        for key, c in (src.get("tradeable") or {}).items():
            lo, hi = (float(x) for x in key.split("-"))
            if lo <= price < hi:
                return float(c["min_edge_threshold"])
        return None  # unlisted band — not proven, don't trade

    def category_blocked(self, league_code: str | None, category: str) -> bool:
        """League x category blocks (e.g. NFL moneyline: specialist-measured
        negative even while NFL spreads are strongly positive)."""
        blocks = self.leagues.get("category_blocks") or {}
        return category in (blocks.get(league_code or "") or [])

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
class StrategyVerdict:
    """Pure strategy filter output (sizing/caps live in execution.risk)."""

    ok: bool
    reason: str
    band: str
    threshold: float | None
    edge: float
    tier: str = "core"       # 'core' = above the band bar; 'exploration' =
                             # below it, traded on a capped budget to MEASURE
                             # whether small edges pay.
    drift_penalty: float = 0.0   # measured adverse drift folded into the bar


def strategy_filter(
    policy: Policy, league_code: str | None, price: float, fair: float,
    venue_fee: float = 0.0, category: str = "moneyline",
    consensus_books: int | None = None, drift_penalty: float = 0.0,
) -> StrategyVerdict:
    """The entry rule alone: fair - price - fee >= threshold(band, category),
    plus dead zones, league allow/block, and league x category blocks.
    Sizing is RiskManager.approve()'s job.

    `drift_penalty` is the measured adverse move in fair value after entry
    (see LedgerService.drift_penalties). It is added to the band threshold
    rather than subtracted from the edge, so the reported edge stays the raw
    estimate and the bar is visibly the thing that moved."""
    band = policy.band_of(price)
    edge = fair - price - venue_fee
    if policy.league_allowed(league_code) == "block":
        return StrategyVerdict(False, f"league {league_code} blocked", band, None, edge)
    if policy.category_blocked(league_code, category):
        return StrategyVerdict(
            False, f"category {category} blocked for {league_code}", band, None, edge)
    base_threshold = policy.band_threshold(price, category)
    if base_threshold is None:
        return StrategyVerdict(
            False, f"band {band} dead/unproven ({category})", band, None, edge)
    drift_penalty = max(0.0, float(drift_penalty or 0.0))
    threshold = base_threshold + drift_penalty
    if edge < threshold:
        # Exploration: below the band bar but still a positive, non-trivial
        # edge backed by enough books to not be a single quote's noise. These
        # trade on a separate, capped budget so the threshold becomes a
        # measurement rather than an assumption.
        exp = policy.risk.get("exploration") or {}
        if exp.get("enabled"):
            # Exploration exists to test whether SMALL edges pay. An edge
            # smaller than the measured adverse drift is not an open question
            # — it is a measured loser — so the floor moves with the drift
            # too, or the study budget just funds the thing we disproved.
            min_edge = float(exp.get("min_edge", 0.008)) + drift_penalty
            min_books = int(exp.get("min_consensus_books", 3))
            # Fail CLOSED. An unknown consensus depth is not permission — if
            # we cannot say how many books back this number, we are not
            # trading a fraction of a cent on it.
            enough_books = (consensus_books is not None
                            and consensus_books >= min_books)
            if edge >= min_edge and enough_books:
                max_edge = float(policy.bands.get("max_believable_edge", 0.08))
                if edge <= max_edge + 1e-9:
                    return StrategyVerdict(
                        True, "exploration", band, threshold, edge,
                        tier="exploration", drift_penalty=drift_penalty)
            if edge >= min_edge and not enough_books:
                return StrategyVerdict(
                    False, f"thin_consensus {consensus_books} books for "
                           f"{edge:.3f} edge", band, threshold, edge,
                           drift_penalty=drift_penalty)
        return StrategyVerdict(
            False, f"edge {edge:.3f} < threshold {threshold:.3f}", band, threshold,
            edge, drift_penalty=drift_penalty)
    # Implausibility guard: measured real edges are 1-4c. A "20c edge" is a
    # mapping/model error wearing a costume — log it, never trade it.
    max_edge = float(policy.bands.get("max_believable_edge", 0.08))
    if edge > max_edge + 1e-9:
        return StrategyVerdict(
            False, f"implausible edge {edge:.3f} > {max_edge:.2f} (mapping/model suspect)",
            band, threshold, edge, drift_penalty=drift_penalty)
    return StrategyVerdict(True, "ok", band, threshold, edge,
                           drift_penalty=drift_penalty)


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
