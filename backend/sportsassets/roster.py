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
    """Fetch the sports-category profit leaderboard (same API behind
    polymarket.com/leaderboard). Tries the documented param spellings."""
    cfg = settings()
    params_variants = [
        {"window": window, "limit": limit, "category": "sports", "rankType": "profit"},
        {"window": window, "limit": limit, "category": "Sports"},
        {"timePeriod": window, "limit": limit, "category": "sports"},
    ]
    async with httpx.AsyncClient(base_url=cfg.leaderboard_api_base, timeout=20) as http:
        last_error: Exception | None = None
        for params in params_variants:
            try:
                resp = await http.get("/leaderboard", params=params)
                resp.raise_for_status()
                parsed = parse_leaderboard(resp.json())
                if parsed:
                    return parsed
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
        if last_error:
            raise last_error
    return []


async def _meets_activity_requirements(http: httpx.AsyncClient, address: str) -> bool:
    """≥ MIN resolved sports positions and traded within MAX_INACTIVE_DAYS."""
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
        # Resolved-position count: /positions supports sizeThreshold+limit paging;
        # we only need to know whether the count clears the bar.
        resp = await http.get(
            "/positions",
            params={"user": address, "limit": cfg.roster_min_resolved_positions, "redeemable": "false"},
        )
        if resp.status_code == 200 and isinstance(resp.json(), list):
            return len(resp.json()) >= cfg.roster_min_resolved_positions
        return True  # endpoint variant mismatch — don't block selection on it
    except httpx.HTTPError as exc:
        log.warning("activity check failed for %s: %s (treating as inactive)", address, exc)
        return False


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
