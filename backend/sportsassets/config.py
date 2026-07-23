"""Central configuration. Every tunable is an env var; see .env.example."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Infrastructure
    database_url: str = "postgresql://sportsassets:sportsassets@localhost:5432/sportsassets"
    redis_url: str = "redis://localhost:6379/0"

    # Polygon RPC
    polygon_ws_url: str = ""
    polygon_http_url: str = ""
    ctf_exchange_address: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    neg_risk_ctf_exchange_address: str = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

    # Public APIs
    data_api_base: str = "https://data-api.polymarket.com"
    gamma_api_base: str = "https://gamma-api.polymarket.com"
    leaderboard_api_base: str = "https://lb-api.polymarket.com"
    clob_api_base: str = "https://clob.polymarket.com"

    # Roster
    roster_size: int = 5
    roster_min_resolved_positions: int = 200
    roster_max_inactive_days: int = 14
    roster_refresh_interval_hours: int = 168  # weekly

    # Ingestion
    poll_interval_seconds: float = 5.0
    data_api_max_rps: float = 4.0  # combined ceiling across all Data-API callers
    positions_sync_interval_seconds: int = 300
    history_max_trades: int = 500_000  # deep-backfill cap per wallet
    history_start_date: str = "2025-07-01"  # earliest fill date to import
    poll_failure_alert_threshold: int = 3
    ws_down_alert_seconds: int = 30
    reconcile_interval_seconds: int = 3600
    metadata_refresh_seconds: int = 60

    # Notifications
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_claims_email: str = "mailto:admin@example.com"
    telegram_bot_token: str = ""
    telegram_channel_id: str = ""
    telegram_admin_chat_id: str = ""
    telegram_channel_invite_url: str = ""
    burst_collapse_threshold: int = 5
    burst_collapse_window_seconds: int = 60

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    admin_token: str = "change-me"
    # Shared secret the edge-engine uses to record its shadow fills here.
    engine_ingest_token: str = ""

    # Copy-trade feasibility probes: on each fresh whale BUY, snapshot the
    # residual order book and compute achievable prices. assumed_edge is the
    # whale's measured profit-per-dollar (swisstony ≈ 2.3%).
    copy_probe_enabled: bool = True
    copy_probe_assumed_edge: float = 0.023
    # "*" = accept any origin (fine while testing; set to your site URL(s),
    # comma-separated, to lock down for production).
    cors_origins: str = "*"


@lru_cache
def settings() -> Settings:
    return Settings()
