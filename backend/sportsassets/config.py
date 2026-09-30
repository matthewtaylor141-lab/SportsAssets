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
    # E10 (2026-09-08): the CEILING, and the env may only lower it --
    # ratelimit.data_api_rate() reads min(this, ratelimit.DATA_API_MAX_RPS
    # 6.0); the mirror's per-market read rides a priority lane on it.
    data_api_max_rps: float = 6.0  # combined ceiling across all Data-API callers
    # E10 (2026-09-08): 300 -> 900. api_positions, the table positions_sync
    # writes, is read by the API alone: the UI's whale profile
    # (api/queries.whale_profile) and events view (api/queries.events_view)
    # AND the edge engine's /api/signal alignment (api/app.api_signal,
    # read by edge-engine/src/edge/shadow/runner.py's shadow runner) -- a
    # 15-minute freshness moves that signal too; nothing under workers/,
    # analytics/ or ingestion/ reads it (the mirror's per-market read and
    # the exit worker's walk are their own reads), so nothing on the money
    # path does (E10 fold, the review's LOW-2: it was "the UI only"). At
    # 300 s its <= 48-page walk per whale was ~1.1 rps of the 6.0 rps
    # data-API budget the mirror's per-market read shares (hard2/E10_map.md
    # §1e: ~18% of the ceiling, the budget oversubscribed). At 900 s the
    # UI's book, and /api/signal's positioning, is at most 15 minutes old
    # (was 5) and the burst ~0.37 rps. The env may set it lower for a
    # fresher UI, never under positions_sync.POSITIONS_SYNC_MIN_S = 60 (the
    # floor sits where the value is read: positions_sync.sync_interval_s).
    positions_sync_interval_seconds: int = 900
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
    # ── NO PUBLISHED DEFAULT CREDENTIAL. FAIL CLOSED. ───────────────
    #
    # THE DEFECT THIS REPLACES, 2026-09-27. These two fields carried
    # defaults -- `admin_token = "change-me"` and `desk_password = "bt"` --
    # and both are published in this repository. A production readback then
    # showed `DESK_PASSWORD` was NOT set on the service, so the credential
    # actually in force for every protected Command Centre read WAS the
    # published default. Anyone who read this file could mint a desk token,
    # a wall token and a COMMAND cookie.
    #
    # THAT IS AN AUTHENTICATION DEFECT, NOT A CONFIGURATION TASK. I first
    # reported it as "an owner decision to make", which is wrong on its own
    # terms: the owner cannot decide their way out of a fallback the code
    # offers. The code must not offer it.
    #
    # `admin_token` was the worse of the two. It is compared directly by
    # `require_admin` AND `require_command`, so an unset environment would
    # have granted the ADMIN role -- the whole admin API -- to the string
    # "change-me". It is also the HMAC key every desk, wall and control
    # token is signed with, so an empty key would let a token forged with an
    # empty key verify.
    #
    # EMPTY MEANS NOT CONFIGURED, AND NOT CONFIGURED MEANS REFUSE. Every
    # consumer checks `if not expected` before comparing, the token
    # verifiers check `if not key`, and the minters raise rather than issue
    # a token nobody can trust. `credential_posture()` below reports which
    # are unset so the gap is visible instead of silent.
    admin_token: str = ""
    # Trading-desk unlock password (owner directive 2026-08-22): the desk
    # gets its own credential so the admin token never has to live in a
    # phone browser. Compared constant-time; a successful unlock mints a
    # short-lived HMAC token derived from admin_token (see api/app.py).
    #
    # NO DEFAULT. See the block above.
    desk_password: str = ""
    # OPERATOR CONTROL PASSWORD (2026-09-26). The desk's CONTROLS are
    # writes, and the first version made the browser carry the ADMIN
    # token to send them -- a service credential, with the whole admin
    # API behind it, typed into a page. This is a SECOND, NARROWER
    # credential: a successful sign-in mints a short-lived scoped token
    # that opens the control actions and NOTHING ELSE (see
    # api/app.py: mint_control_token / require_command_control).
    #
    # EMPTY MEANS THE CONTROL SIGN-IN IS NOT CONFIGURED, and the endpoint
    # refuses by name rather than falling back to a default -- a default
    # operator password would be worse than none.
    operator_password: str = ""
    # FUNDED RESOLUTION KEY (2026-09-29). The second factor for the one route
    # that can close a lost acknowledgement and release its exposure
    # (POST /api/admin/funded-investigations/{intent_id}/resolve). The admin
    # token alone is not enough there: it is also held by the verification
    # workflows, so it proves possession of a service credential, not an
    # operator's decision. This key is meant to be held by the owner only and
    # never placed in a workflow. EMPTY MEANS THE ROUTE REFUSES 503 by name.
    # It is deliberately NOT in CREDENTIAL_CONSEQUENCES: an unset key disables
    # owner-only actions, and must not report the service as misconfigured.
    # It IS in OWNER_CREDENTIAL_CONSEQUENCES below, which the posture reports
    # separately, so what refuses without it is still visible. Since D5b it is
    # also the second factor for SIGNING the funded owner authorization
    # (POST /api/admin/funded-owner-authorization and its /revoke).
    # Provisioning: research/RESOLUTION_CREDENTIAL_PROVISIONING.md.
    funded_resolution_key: str = ""
    # THE IDENTITY THAT KEY AUTHENTICATES. The route writes this -- not a name
    # typed into the request -- into the audit row, and refuses an attestation
    # signed by anyone else. Empty refuses 503, like an empty key.
    funded_resolution_operator: str = ""
    # Committed capital (owner directive 2026-08-22): dollars the owner
    # has committed to restore to the Polymarket account (an owner draw
    # outstanding). Displayed ONLY as part of the clearly-labeled
    # "trading capital (incl. committed capital)" composite — never
    # blended into a figure labeled cash/balance. Zero this env var when
    # the money is restored and the display collapses to plain cash.
    committed_capital_pm_usd: float = 0.0
    # Shared secret the edge-engine uses to record its shadow fills here.
    engine_ingest_token: str = ""
    # ── SESSION EPOCH: REVOKE EVERY SESSION WITHOUT ROTATING THE KEY ──
    #
    # WHY IT EXISTS. Every desk, wall and command token is an HMAC keyed by
    # `admin_token`, so the only way to revoke them all was to rotate that key.
    # That works, and it has a cost: the SAME token is the credential the
    # authorized verification workflows present, held as a GitHub secret. Rotating
    # it on the service without simultaneously updating that secret breaks the
    # API-only readback route -- the mechanism used to prove the fix landed.
    #
    # THE PUBLISHED-DEFAULT DEFECT MADE THIS CONCRETE. Sessions minted against the
    # default password stay valid for up to 12 h (desk) or 7 DAYS (wall), and
    # removing the default stops NEW sessions without touching existing ones. They
    # have to be revoked, and revoking them must not require breaking the
    # verification route in the same move.
    #
    # So the epoch goes INSIDE THE SIGNED MATERIAL. Bumping it invalidates every
    # outstanding token at once. IT IS NOT A SECRET -- it is a counter, so it can
    # be set through the ordinary env route without the value needing to be
    # private, and it is safe to print.
    session_epoch: str = "1"

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
    # A SEPARATE ordinary Polymarket US API key used for the API process's
    # market-data subscription (env PMUS_MD_KEY_ID / PMUS_MD_SECRET_KEY),
    # apart from the key the protected worker streams with: separate
    # revocation, and possibly separate per-key limits. It does not isolate
    # limits applied per account, participant, endpoint or IP, and the key
    # is not read-only merely because this path only reads. Never logged.
    pmus_md_key_id: str = ""
    pmus_md_secret_key: str = ""
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


# ── CREDENTIAL POSTURE, REPORTED RATHER THAN ASSUMED ─────────────────
#
# WHY THIS EXISTS. The published-default defect was invisible: the service ran,
# every protected route answered 200, and nothing anywhere said which credential
# was actually in force. The only reason it surfaced is that a readback happened
# to print `DESK_PASSWORD present: NO` and someone read the line.
#
# So the posture is now a value the service can be ASKED for. A credential that
# is not configured is reported as `NOT_CONFIGURED`, and the routes it guards are
# reported as refusing -- which is what they now do.
#
# NO VALUE, LENGTH OR FINGERPRINT OF ANY SECRET IS RETURNED. A length is a
# meaningful hint about a password and a fingerprint of a short secret is
# brute-forceable, so this returns booleans and route consequences only.

#: Credential -> what refuses when it is absent.
CREDENTIAL_CONSEQUENCES = {
    "admin_token": (
        "every /api/admin route refuses 401; the `admin` role in "
        "require_command is unreachable; NO desk, wall or control token can be "
        "minted or verified, because it is the HMAC signing key"),
    "desk_password": (
        "/api/desk/unlock, /api/wall/unlock and /api/command/session all refuse, "
        "so no COMMAND cookie can be obtained"),
    "operator_password": (
        "/api/command/session/control refuses, so no control token can be "
        "minted and the desk's control actions are unreachable"),
}


#: THE OWNER-HELD CREDENTIALS -> what refuses when they are absent (D5b).
#:
#: KEPT APART FROM CREDENTIAL_CONSEQUENCES ON PURPOSE. That map drives
#: `all_configured`, which /healthz publishes and the verification workflow
#: expects to read `true`: every name in it guards a route the service needs to
#: operate. These two guard OWNER-ONLY acts -- signing or revoking the funded
#: owner authorization, and resolving a lost acknowledgement -- and are unset
#: until the owner provisions them, which is the correct state for a service
#: with capital off, not a misconfiguration. Folding them into the first map
#: would turn every healthz readback red until then. They are reported beside
#: it, by name and consequence, with no value.
OWNER_CREDENTIAL_CONSEQUENCES = {
    "funded_resolution_key": (
        "FUNDED_RESOLUTION_KEY: POST /api/admin/funded-owner-authorization, "
        "POST /api/admin/funded-owner-authorization/revoke and POST "
        "/api/admin/funded-investigations/{intent_id}/resolve all refuse 503 "
        "FUNDED_RESOLUTION_KEY_NOT_CONFIGURED, so the owner cannot sign or "
        "revoke the funded authorization and no lost acknowledgement can be "
        "resolved"),
    "funded_resolution_operator": (
        "FUNDED_RESOLUTION_OPERATOR: the same three routes refuse 503, because "
        "a key with no identity bound to it authenticates nobody; it is the "
        "name written into every owner-authorization and resolution audit row"),
}


def credential_posture() -> dict:
    """Which authentication credentials are configured, and what refuses if not.

    Returns no secret material -- not the value, not its length, not a hash.
    """
    s = settings()
    out = {}
    for name, consequence in CREDENTIAL_CONSEQUENCES.items():
        configured = bool((getattr(s, name, "") or "").strip())
        out[name] = {
            "configured": configured,
            "state": "CONFIGURED" if configured else "NOT_CONFIGURED",
            "if_absent": consequence,
        }
    missing = sorted(k for k, v in out.items() if not v["configured"])
    owner = {}
    for name, consequence in OWNER_CREDENTIAL_CONSEQUENCES.items():
        configured = bool((getattr(s, name, "") or "").strip())
        owner[name] = {
            "configured": configured,
            "state": "CONFIGURED" if configured else "NOT_CONFIGURED",
            "if_absent": consequence,
        }
    return {
        "credentials": out,
        "not_configured": missing,
        "all_configured": not missing,
        # REPORTED, NOT COUNTED: owner-only credentials do not make the
        # service misconfigured when unset (see OWNER_CREDENTIAL_CONSEQUENCES).
        "owner_credentials": owner,
        "owner_not_configured": sorted(
            k for k, v in owner.items() if not v["configured"]),
        "there_is_no_default_for_any_of_these": (
            "admin_token and desk_password previously defaulted to values "
            "published in this repository. Both now default to empty, and every "
            "consumer refuses on empty rather than falling back"),
        "and_no_secret_material_is_returned_here": (
            "not the value, not its length, not a fingerprint -- a length is a "
            "hint about a password and a short secret's hash is brute-forceable"),
    }
