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

    def band_probation(self, price: float, category: str = "moneyline") -> bool:
        """Is this a PROBATION band — donor-dead, re-opened at a
        deliberately elevated bar to generate our own evidence? The
        elevated bar is the FLOOR there: no tier may discount it."""
        src = (self.bands if category == "moneyline"
               else (self.bands.get("categories") or {}).get(category) or {})
        for key, c in (src.get("tradeable") or {}).items():
            lo, hi = (float(x) for x in key.split("-"))
            if lo <= price < hi:
                return bool(c.get("probation"))
        return False

    def category_blocked(self, league_code: str | None, category: str) -> bool:
        """League x category blocks, plus GLOBAL category quarantines.

        The global list is the same instrument as the league blocklist,
        applied to bet type instead of competition: a category our own fills
        have measured negative stops trading everywhere, not just where it
        was noticed. Reversible by config — it is a quarantine pending
        evidence, not a verdict.
        """
        if category in (self.leagues.get("blocked_categories") or []):
            return True
        blocks = self.leagues.get("category_blocks") or {}
        return category in (blocks.get(league_code or "") or [])

    def venue_league_blocked(self, venue: str | None,
                             code: str | None) -> bool:
        """Per-VENUE league quarantine (owner order 2026-08-17, weekly
        report R1): the bands were calibrated on Polymarket fills and the
        venue transfer is unproven per venue — the Aug 10-16 Kalshi
        tennis book lost heavily while Polymarket tennis was positive
        the same week. Env-driven and reversible:
        EDGE_VENUE_LEAGUE_BLOCKS="kalshi:atp+wta+itf+ch;venue2:..."
        (set empty to clear). Copies are NOT gated here — this stops
        only engine-initiated entries."""
        if not venue or not code:
            return False
        import os
        raw = os.environ.get(
            "EDGE_VENUE_LEAGUE_BLOCKS",
            "kalshi:atp+wta+itf+ch+atpch+wtach+challenger")
        cached = getattr(self, "_venue_league_blocks", None)
        if cached is None or cached[0] != raw:
            table: dict[str, set] = {}
            for part in raw.split(";"):
                if ":" not in part:
                    continue
                v, leagues = part.split(":", 1)
                table[v.strip().lower()] = {
                    x.strip().lower() for x in leagues.split("+") if x.strip()}
            cached = (raw, table)
            self._venue_league_blocks = cached
        return code.lower() in cached[1].get(venue.lower(), set())

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

    def league_measured(self, code: str | None) -> bool:
        """True only for leagues the 5.35M-fill calibration actually covers
        (the allowlist). Unknown-but-allowed leagues trade on the strength
        of the de-vig arithmetic, not on evidence — the probation tier
        sizes them accordingly until their own settlements speak."""
        if not code:
            return False
        for group in (self.leagues.get("allowlist") or {}).values():
            if code in group:
                return True
        return False


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
    raw_edge: float = 0.0        # apparent edge before shrinkage
    keep: float = 1.0            # fraction of the apparent edge believed real


def strategy_filter(
    policy: Policy, league_code: str | None, price: float, fair: float,
    venue_fee: float = 0.0, category: str = "moneyline",
    consensus_books: int | None = None, drift_penalty: float = 0.0,
    keep: float | None = None, venue: str | None = None,
) -> StrategyVerdict:
    """The entry rule alone: fair - price - fee >= threshold(band, category),
    plus dead zones, league allow/block, and league x category blocks.
    Sizing is RiskManager.approve()'s job.

    Two corrections for the fact that our fair value is an ESTIMATE, and only
    ever one of them applies:

    `keep` is the measured fraction of an apparent edge that survives (see
    LedgerService.reversion). We buy where fair - price is largest, which
    preferentially selects the moments our own estimate is most wrong — the
    winner's curse — so the apparent edge is shrunk toward the market price
    by exactly the measured amount. This is proportional: it costs a 10c
    claim ten times what it costs a 1c claim, because that is where the
    error actually lives.

    `drift_penalty` is the blunt fallback for when reversion is not yet
    measurable: a flat surcharge on the bar (see drift_penalties). It taxes
    every trade equally, which is wrong in detail but safe.

    Passing both would charge for the same error twice, so shrinkage wins
    when it is available and the surcharge is ignored."""
    band = policy.band_of(price)
    raw_edge = fair - price - venue_fee
    if keep is not None:
        keep = min(1.0, max(0.0, float(keep)))
        edge, drift_penalty = raw_edge * keep, 0.0
    else:
        keep, edge = 1.0, raw_edge
    if policy.league_allowed(league_code) == "block":
        return StrategyVerdict(False, f"league {league_code} blocked", band, None, edge, raw_edge=raw_edge, keep=keep)
    if policy.venue_league_blocked(venue, league_code):
        return StrategyVerdict(
            False, f"league {league_code} blocked on {venue}",
            band, None, edge, raw_edge=raw_edge, keep=keep)
    if policy.category_blocked(league_code, category):
        return StrategyVerdict(
            False, f"category {category} blocked for {league_code}", band, None, edge, raw_edge=raw_edge, keep=keep)
    base_threshold = policy.band_threshold(price, category)
    if base_threshold is None:
        return StrategyVerdict(
            False, f"band {band} dead/unproven ({category})", band, None, edge, raw_edge=raw_edge, keep=keep)
    drift_penalty = max(0.0, float(drift_penalty or 0.0))
    threshold = base_threshold + drift_penalty
    if edge < threshold:
        # Exploration: below the band bar but still a positive, non-trivial
        # edge backed by enough books to not be a single quote's noise. These
        # trade on a separate, capped budget so the threshold becomes a
        # measurement rather than an assumption.
        exp = policy.risk.get("exploration") or {}
        if exp.get("enabled") and not policy.band_probation(price, category):
            # (Probation bands are exploration-EXEMPT: their elevated bar
            # exists to demand unusually clear mispricing in a zone the
            # donor measured DEAD — discounting it would fund exactly the
            # trades the calibration disproved.)
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
                max_ratio = float(policy.bands.get(
                    "max_believable_edge_ratio", 0.5))
                # Both implausibility guards bind here too — the exploration
                # budget studies small edges, not suspicious ones.
                if edge <= max_edge + 1e-9 and (
                        price <= 0 or edge <= price * max_ratio + 1e-9):
                    return StrategyVerdict(
                        True, "exploration", band, threshold, edge,
                        tier="exploration", drift_penalty=drift_penalty,
                        raw_edge=raw_edge, keep=keep)
            if edge >= min_edge and not enough_books:
                return StrategyVerdict(
                    False, f"thin_consensus {consensus_books} books for "
                           f"{edge:.3f} edge", band, threshold, edge,
                           drift_penalty=drift_penalty, raw_edge=raw_edge,
                           keep=keep)
        return StrategyVerdict(
            False, f"edge {edge:.3f} < threshold {threshold:.3f}", band, threshold,
            edge, drift_penalty=drift_penalty, raw_edge=raw_edge, keep=keep)
    # Implausibility guard: measured real edges are 1-4c. A "20c edge" is a
    # mapping/model error wearing a costume — log it, never trade it.
    max_edge = float(policy.bands.get("max_believable_edge", 0.08))
    if edge > max_edge + 1e-9:
        return StrategyVerdict(
            False, f"implausible edge {edge:.3f} > {max_edge:.2f} (mapping/model suspect)",
            band, threshold, edge, drift_penalty=drift_penalty,
            raw_edge=raw_edge, keep=keep)
    # RELATIVE implausibility: the absolute cap does not scale. An 8c edge
    # at 60c is a 13% disagreement — arguable; the same 8c at 10c claims
    # the venue misprices the outcome by 80% of its value, which in
    # practice is our mapping error, a one-sided quote, or a resolution
    # mismatch far more often than it is free money. The 0.5 default
    # admits the measured favorite-longshot signature (reference fills at
    # 5-10c averaged +2.7c, a 0.36 ratio) and de facto closes sub-nickel
    # prices, where the 3c bar cannot fit under half the price — a
    # 2c contract claiming a 3c edge is claiming fair value 2.5x the
    # market, and nothing we sell ourselves about longshot bias makes
    # that a measurement.
    max_ratio = float(policy.bands.get("max_believable_edge_ratio", 0.5))
    if price > 0 and edge > price * max_ratio + 1e-9:
        return StrategyVerdict(
            False, f"implausible edge {edge:.3f} > {max_ratio:.0%} of price "
                   f"{price:.2f} (mapping/model suspect)",
            band, threshold, edge, drift_penalty=drift_penalty,
            raw_edge=raw_edge, keep=keep)
    return StrategyVerdict(True, "ok", band, threshold, edge,
                           drift_penalty=drift_penalty, raw_edge=raw_edge,
                           keep=keep)


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
