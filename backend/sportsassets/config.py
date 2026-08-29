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
    # The 2026 exchange (Polymarket migrated; the two above stopped
    # emitting fills — diagnosed on-chain 2026-08-10). Old addresses stay
    # subscribed: harmless if silent, covering any straggler flow.
    pm_exchange_v2_address: str = "0xE2222D279d744050d28E00520010520000310f59"
    # Second 2026 exchange instance: the crypto/non-sports books fill
    # here — same OrderFilled-v2 event (topic 0xd543adfd...), different
    # emitter. Diagnosed 2026-08-22 from the crypto copy whales' real
    # fill receipts (KCR-CHAIN probe): every one of their fills emitted
    # from this address while the listener watched only the three above,
    # so chain decoded ZERO of their trades and detection fell to the
    # Data-API poll (3.5-8.5 min publication lag vs the leg's 90s bar).
    pm_exchange_crypto_address: str = "0xE111180000d2663c0091E4f400237545B87b996B"

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
    # Fast-lane cycle for the pinned COPY whales (owner latency push
    # 2026-08-20): each pinned wallet re-polled every ~this many seconds
    # on top of the roster rotation. 0 disables the lane.
    poll_priority_seconds: float = 2.5
    # 4.0 -> 6.0 with the fast lane (its ~2 rps rides on top of the
    # roster pass); polite_get's 429 backoff still owns the true ceiling.
    data_api_max_rps: float = 6.0  # combined ceiling across all Data-API callers
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
    # ntfy push alerts — free, no account: recipient subscribes to the secret
    # topic in the ntfy app; we POST each fresh detection to it (~1-2s).
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""            # long random topic name = the only secret
    ntfy_watch_addresses: str = ""  # comma-separated wallets; empty = all whales
    # SMS alerts (Twilio). Texts fire on fresh detections only (never backfill),
    # scoped to sms_watch_addresses when set, burst-collapsed like push.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""   # your Twilio number, E.164 (+1...)
    sms_to_numbers: str = ""       # comma-separated recipients, E.164
    sms_watch_addresses: str = ""  # comma-separated wallets; empty = all whales
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
    # Trading-desk unlock password (owner directive 2026-08-22): the desk
    # gets its own credential so the admin token never has to live in a
    # phone browser. Compared constant-time; a successful unlock mints a
    # short-lived HMAC token derived from admin_token (see api/app.py).
    desk_password: str = "bt"
    # Committed capital (owner directive 2026-08-22): dollars the owner
    # has committed to restore to the Polymarket account (an owner draw
    # outstanding). Displayed ONLY as part of the clearly-labeled
    # "trading capital (incl. committed capital)" composite — never
    # blended into a figure labeled cash/balance. Zero this env var when
    # the money is restored and the display collapses to plain cash.
    committed_capital_pm_usd: float = 0.0
    # Shared secret the edge-engine uses to record its shadow fills here.
    engine_ingest_token: str = ""

    # Copy-trade feasibility probes: on each fresh whale BUY, snapshot the
    # residual order book and compute achievable prices. assumed_edge is the
    # whale's measured profit-per-dollar (swisstony ≈ 2.3%).
    copy_probe_enabled: bool = True
    copy_probe_assumed_edge: float = 0.023

    # AI TRADER paper account: copy the source whale's BUYs at ratio * his
    # notional, filled from the live residual book at our reaction time.
    ai_trader_enabled: bool = True
    # Comma-separated usernames to copy. swisstony's edge is MEASURED
    # (decay study, 5.35M-fill calibration); RN1 added 2026-08-03 on owner
    # instruction — unmeasured, so both the paper account and the live
    # penny sleeve grade copies per-username and RN1 earns its keep or is
    # removed on its own record, never blended into swisstony's.
    # Four-account sport-weighted portfolio (owner directive 2026-08-06):
    # per-whale sport assignments live in copy_sports.py; this list is WHO
    # gets copied at all. kch123 + HomeRunHazard added from fill-level
    # forensic reconstructions (kch123 7.1% blended; HRH directional +3.51%).
    # 0x2c33…0563 promoted from vetting 2026-08-10 (owner approval; 1,712
    # probes, +0.76%/$1k residual ROI at our real latency). Keyed by the
    # roster's auto-generated username — pinned in migration 019 so the
    # weekly refresh cannot rename it out from under this list. NOTE: an
    # AI_TRADER_SOURCE env on Render overrides this default; if one is
    # set, the owner must add the same entry there.
    ai_trader_source: str = ("swisstony,RN1,kch123,HomeRunHazard,"
                             "0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563"
                             "-1759935795465")
    ai_trader_ratio: float = 0.10
    # VETTING whales (owner directive 2026-08-06): candidates under
    # evaluation for the live copy sleeve. Vetting whales are probed and
    # PAPER-copied (per-username graded) but NEVER live-copied — only
    # promotion into ai_trader_source arms real money. Comma-separated
    # usernames, matched against the tracked roster.
    ai_trader_vetting: str = ""

    # Dossier promotions (owner order 2026-08-21: "get the 2 recommended
    # traders added immediately and starting to be fired immediately").
    # The env list stays the primary arming switch; this field adds the
    # code-promoted probation whales so a promotion ships as one deploy
    # instead of code + a manual env edit that can be forgotten (which
    # is exactly what left them detecting but never firing for 2h on
    # promotion day). Env AI_TRADER_SOURCE_EXTRA="" disarms them.
    ai_trader_source_extra: str = "ferrarichampions2026,0x076daa87"

    def source_whales(self) -> set[str]:
        return ({s.strip().lower() for s in self.ai_trader_source.split(",")
                 if s.strip()}
                | {s.strip().lower()
                   for s in self.ai_trader_source_extra.split(",")
                   if s.strip()})

    def vetting_whales(self) -> set[str]:
        return {s.strip().lower() for s in self.ai_trader_vetting.split(",")
                if s.strip()}

    # ── LIVE trading beta (REAL MONEY — disabled by default) ────────────
    # Requires an eligible, funded Polymarket account and a dedicated wallet
    # holding ONLY the beta bankroll. FOK limit orders only, buy-only,
    # triple-capped. Kill switch: POST /api/admin/live/pause.
    live_trading_enabled: bool = False
    # Polymarket US (regulated app) — Ed25519 API keys from polymarket.us/developer.
    # When set, live orders route to the US venue. This is the supported path
    # for US-based accounts.
    pmus_key_id: str = ""
    pmus_secret_key: str = ""
    # Global CLOB (non-US accounts only) — unused when PMUS keys are set.
    pm_private_key: str = ""       # dedicated wallet key (export from PM settings)
    pm_funder: str = ""            # your Polymarket profile (proxy) address
    pm_signature_type: int = 1     # 1/2 = web-account proxy, 0 = raw EOA
    live_copy_ratio: float = 0.001  # $1 per $1k the source whale trades
    # These three govern only the dormant "full" COPY_MODE — the live
    # penny_trial path takes its authority from the trial knobs and the
    # per-whale clip map in live_executor. Defaults raised 2026-08-29
    # (owner order: "I don't want to limit the flow"): the old 25/250/
    # 500 were beta training wheels, and the $500 lifetime silently
    # bound once already (2026-08-05, hours of no-op copies). A future
    # mode flip must inherit the owner's sizing, not the beta's.
    live_max_per_fill_usd: float = 250.0
    live_max_daily_usd: float = 11000.0
    live_max_total_usd: float = 1e12   # effectively no lifetime cap
    live_max_slippage_cents: float = 1.0
    # "*" = accept any origin (fine while testing; set to your site URL(s),
    # comma-separated, to lock down for production).
    cors_origins: str = "*"


@lru_cache
def settings() -> Settings:
    return Settings()
