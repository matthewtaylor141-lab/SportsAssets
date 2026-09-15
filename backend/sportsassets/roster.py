"""Roster Service — selects and refreshes the tracked whale list.

Selection rule (spec §3): from the sports-category leaderboard, rank by
all-time realized sports profit, require ≥ MIN resolved sports positions and
activity within the last MAX_INACTIVE_DAYS, take top N. Pinned wallets are
always tracked; banned wallets never. On removal, history is KEPT and the
whale is marked inactive; admins are notified. Refresh is weekly — never a
silent mid-week swap.

Leaderboard endpoint shape varies; `parse_leaderboard` accepts the known
variants defensively and the base URL is config. Wallet addresses are always
resolved from the live API — never hardcoded from articles (spec seed note).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import settings
from .db import get_pool
from .notifications import telegram

log = logging.getLogger(__name__)

# ── track-only qualification cohort ─────────────────────────────────
# Whale-qualification dossier round 1 (owner 95%-confidence program,
# 2026-08-28): 52 censused candidates → 3 ranking lenses → skeptic
# verification against the raw census rows → this shortlist. These
# wallets are tracked for WHALEEDGE evidence ONLY (poller/reconciler
# ingest their books; the analytics worker measures their per-whale
# edge CIs). They are NEVER copied: copying is gated by COPY_WHALES /
# CRYPTO_WHALES in api/copies_record.py, and promotion into those
# sets is the OWNER'S decision alone — tests pin the disjointness.
# They join the roster target WITHOUT consuming roster_size slots and
# survive refreshes without being pinned. (0xf705fa04, the dossier's
# crypto candidate, is absent here because it is already rostered in
# the crypto leg.)
TRACK_ONLY: dict[str, str] = {
    "0xcd30f4698c6f5f3829893e68e183a8e5ea18f316": "SPCEXBUYER",
    "0xf201a19b43471261a3c1ba9247335d55270e527e": "0xF201A19b",
    "0xf23c5bc7b547867eb6532920144562718aa49f81": "DoNotTailMe",
    "0xe30e74595517de48f1fb19f4553dd3d9f1e96b87": "0xE30E7459",
    "0x16bb9951a36fce71e2ef57890b786145e0ba8492": "SDTrading",
    "0x8546a601f7c7cc3dae7141f20b0e09e42bbf35b8": "OOOwhyOOO",
    "0xdc41c39b95453c943174f369926018f6963bdd7e": "nigiri99",
    "0xd235973291b2b75ff4070e9c0b01728c520b0f29": "zxgngl",
    "0x65c18d740c8246af3353741c0b32e4b6da552988": "casualbet2020",
    "0x4bff30af91642dc7d2b19a8664378fe55c45fc26": "Sassy-Bucket",
    "0x6e2c0e9474af7d720e5aba2a5b0bb6f723b7787c": "0x99a093burst",
}


def parse_leaderboard(raw: Any) -> list[dict]:
    """Normalize leaderboard payload → [{address, username, profit, rank}]."""
    if isinstance(raw, dict):
        raw = raw.get("leaderboard") or raw.get("data") or raw.get("traders") or []
    out = []
    for i, entry in enumerate(raw or []):
        if not isinstance(entry, dict):
            continue
        address = (
            entry.get("proxyWallet")
            or entry.get("walletAddress")
            or entry.get("address")
            or entry.get("wallet")
        )
        if not address:
            continue
        profit = entry.get("amount") or entry.get("profit") or entry.get("pnl") or 0
        out.append(
            {
                "address": str(address).lower(),
                "username": entry.get("name") or entry.get("userName") or entry.get("pseudonym"),
                "profit": float(profit),
                "rank": int(entry.get("rank", i + 1)),
            }
        )
    return out


async def fetch_sports_leaderboard(window: str = "all", limit: int = 50) -> list[dict]:
    """Fetch the profit leaderboard (same API behind polymarket.com/leaderboard).

    The exact path/params have changed over time, so try known variants in
    order and use the first that returns parseable entries. Sports-category
    filtering is applied when the API supports it; otherwise we fall back to
    the overall profit board (activity requirements still filter candidates)
    and log the degradation.
    """
    cfg = settings()
    candidates: list[tuple[str, dict]] = [
        # Category-aware spellings (newer API surface):
        ("/leaderboard", {"window": window, "limit": limit, "category": "sports", "rankType": "profit"}),
        ("/leaderboard", {"window": window, "limit": limit, "category": "Sports"}),
        # Classic lb-api paths (long-lived; power the original leaderboard):
        ("/profit", {"window": window, "limit": limit, "category": "sports"}),
        ("/profit", {"window": window, "limit": limit}),
    ]
    failures: list[str] = []
    async with httpx.AsyncClient(base_url=cfg.leaderboard_api_base, timeout=20) as http:
        for path, params in candidates:
            try:
                resp = await http.get(path, params=params)
                resp.raise_for_status()
                parsed = parse_leaderboard(resp.json())
                if parsed:
                    if "category" not in params:
                        log.warning(
                            "leaderboard: category filter unavailable — using overall "
                            "profit board via %s (roster may include non-sports whales)",
                            path,
                        )
                    log.info("leaderboard resolved via %s %s (%s entries)", path, params, len(parsed))
                    return parsed
                failures.append(f"{path}{params} -> empty/unparseable")
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[:120] if exc.response is not None else ""
                failures.append(f"{path} -> HTTP {exc.response.status_code} {body}")
            except (httpx.HTTPError, ValueError) as exc:
                failures.append(f"{path} -> {exc}")
    raise RuntimeError("all leaderboard endpoints failed: " + " | ".join(failures))


async def _meets_activity_requirements(http: httpx.AsyncClient, address: str) -> bool:
    """Recency is decisive: traded within MAX_INACTIVE_DAYS (drops dormant
    one-hit wallets). The min-resolved-positions bar is enforced when the
    positions endpoint cooperates, and advisory (pass + log) when its shape
    doesn't match — a candidate must never be dropped by OUR endpoint guess."""
    cfg = settings()
    try:
        resp = await http.get(
            "/trades", params={"user": address, "limit": 1, "takerOnly": "false"}
        )
        resp.raise_for_status()
        trades = resp.json()
        if not trades:
            return False
        last_ts = datetime.fromtimestamp(int(trades[0]["timestamp"]), tz=timezone.utc)
        if datetime.now(tz=timezone.utc) - last_ts > timedelta(days=cfg.roster_max_inactive_days):
            return False
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("recency check failed for %s: %s (treating as inactive)", address, exc)
        return False

    # Advisory depth check: does a position exist at offset MIN-1?
    try:
        resp = await http.get(
            "/positions",
            params={"user": address, "limit": 1,
                    "offset": cfg.roster_min_resolved_positions - 1},
        )
        if resp.status_code == 200 and isinstance(resp.json(), list):
            if not resp.json():
                log.info("%s below %s-position bar", address, cfg.roster_min_resolved_positions)
                return False
    except httpx.HTTPError as exc:
        log.info("positions depth check unavailable for %s (%s) — passing on recency", address, exc)
    return True


async def select_roster() -> list[dict]:
    """Apply the selection rule; returns the candidate list (top N qualified)."""
    cfg = settings()
    board = await fetch_sports_leaderboard("all", limit=50)
    qualified: list[dict] = []
    async with httpx.AsyncClient(base_url=cfg.data_api_base, timeout=20) as http:
        for cand in board:
            if len(qualified) >= cfg.roster_size:
                break
            if await _meets_activity_requirements(http, cand["address"]):
                qualified.append(cand)
    return qualified


async def apply_roster(candidates: list[dict]) -> dict:
    """Reconcile candidates + pins/bans into the whales table. Never deletes history."""
    pool = await get_pool()
    cfg = settings()
    rows = await pool.fetch("SELECT * FROM whales")
    existing = {r["address"]: dict(r) for r in rows}
    banned = {a for a, w in existing.items() if w["banned"]}
    pinned = [w for w in existing.values() if w["pinned"] and not w["banned"]]

    target: list[dict] = [{"address": p["address"], "username": p["username"],
                           "profit": p["sports_profit_alltime"], "rank": p["source_rank"]}
                          for p in pinned]
    for cand in candidates:
        if len(target) >= cfg.roster_size:
            break
        if cand["address"] in banned:
            continue
        if any(t["address"] == cand["address"] for t in target):
            continue
        target.append(cand)

    # track-only cohort: beyond the roster_size cap by design (they
    # are evidence collection, not roster picks), banned still wins,
    # and membership in target_addrs is what protects them from the
    # retire loop below
    for addr, uname in TRACK_ONLY.items():
        if addr in banned:
            continue
        if any(t["address"] == addr for t in target):
            continue
        target.append({"address": addr, "username": uname,
                       "profit": None, "rank": None})

    added, removed = [], []
    target_addrs = {t["address"] for t in target}
    async with pool.acquire() as conn:
        for t in target:
            w = existing.get(t["address"])
            if w is None or not w["active"]:
                await conn.execute(
                    """
                    INSERT INTO whales (address, username, active, source_rank, sports_profit_alltime)
                    VALUES ($1,$2,TRUE,$3,$4)
                    ON CONFLICT (address) DO UPDATE SET
                        active=TRUE, removed_at=NULL, username=COALESCE($2, whales.username),
                        source_rank=$3, sports_profit_alltime=COALESCE($4, whales.sports_profit_alltime)
                    """,
                    t["address"], t["username"], t.get("rank"), t.get("profit"),
                )
                added.append(t["address"])
            else:
                await conn.execute(
                    "UPDATE whales SET username=COALESCE($2, username), source_rank=$3, "
                    "sports_profit_alltime=COALESCE($4, sports_profit_alltime) WHERE address=$1",
                    t["address"], t["username"], t.get("rank"), t.get("profit"),
                )
        for addr, w in existing.items():
            if w["active"] and not w["pinned"] and addr not in target_addrs:
                # History retained; whale marked inactive (spec §3).
                await conn.execute(
                    "UPDATE whales SET active=FALSE, removed_at=now() WHERE address=$1", addr
                )
                removed.append(addr)
        await conn.execute(
            "INSERT INTO roster_events (kind, detail) VALUES ('refreshed', $1::jsonb)",
            json.dumps({"added": added, "removed": removed, "roster": sorted(target_addrs)}),
        )

    if added or removed:
        await telegram.alert_admins(
            "📋 Roster refresh: "
            + (f"added {', '.join(added)}. " if added else "")
            + (f"retired {', '.join(removed)} (history kept)." if removed else "")
        )
    return {"added": added, "removed": removed, "size": len(target_addrs)}


async def refresh_roster() -> dict:
    candidates = await select_roster()
    if not candidates:
        log.error("leaderboard returned no qualified candidates — roster unchanged")
        await telegram.alert_admins("⚠️ Roster refresh aborted: leaderboard empty/unreachable")
        return {"added": [], "removed": [], "error": "no candidates"}
    return await apply_roster(candidates)
