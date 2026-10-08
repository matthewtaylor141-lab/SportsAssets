"""One bounded DB-only enrichment of the existing authenticated Trader read.

Only game, score_feed and the snapshot's content digest change. Quotes,
probabilities, orders, positions, fees, P&L, packets and settlement do not.
"""
from __future__ import annotations
import asyncio
import copy
import os

from .core import display, unavailable, digest, AUTHORITY
from .storage import LATEST_SQL, object_value


def enabled() -> bool:
    return os.getenv("BETTOR_DISPLAY_SCORES", "off").lower() in ("on", "true", "1")


def apply_rows(snapshot: dict, rows: list[dict], *, now: float, error: str | None = None,
               health: dict | None = None) -> dict:
    result = copy.deepcopy(snapshot)
    lookup = {(r["venue"], r["event_id"]): r for r in rows}
    counts = {"current": 0, "stale": 0, "unavailable": 0, "provider_timestamp_known": 0}
    requested = set()
    for p in result.get("positions") or []:
        venue, event_id = p.get("venue"), p.get("event_id")
        requested.add((venue, event_id))
        row = lookup.get((venue, event_id))
        if not venue or not event_id:
            game = unavailable(venue, event_id, "CANONICAL_VENUE_EVENT_MISSING")
        elif error:
            # A failed DB read may not be interpreted as an empty successful score feed.
            game = unavailable(venue, event_id, error)
        else:
            game = display(object_value(row.get("payload")) if row else None,
                           venue=venue, event_id=event_id, now=now,
                           issue=row.get("issue") if row else None)
        p["game"] = game
        counts[game["status"].lower()] += 1
        counts["provider_timestamp_known"] += game.get("source_at") is not None
    result["score_feed"] = {
        "schema": "bettor.live_game_feed.v1", "authority": AUTHORITY,
        "status": "UNAVAILABLE" if error else "OK" if rows else "NO_EVIDENCE",
        "why": error or (None if rows else "NO_SCORE_PROVIDER_EVIDENCE_RECORDED"),
        "returned_position_count": len(result.get("positions") or []),
        "unique_fixture_count": len(requested), "counts": counts,
        "rates_scope": "RETURNED_POSITIONS_ONLY", "collector": health or None,
        "display_only": True, "no_settlement_authority": True,
    }
    # Digest changes when score evidence changes; original digest is not falsely reused.
    original_id = snapshot.get("snapshot_id")
    result["snapshot_id"] = digest({"native_snapshot_id": original_id,
                                    "games": [p["game"] for p in result.get("positions") or []]})[:24]
    return result


async def enrich_snapshot(conn, snapshot: dict, *, now: float) -> dict:
    if not enabled():
        return snapshot
    keys = sorted({(p.get("venue"), p.get("event_id")) for p in snapshot.get("positions") or []
                   if p.get("venue") in ("POLYMARKET_US", "KALSHI") and p.get("event_id")})
    if not keys:
        return apply_rows(snapshot, [], now=now)
    rows, health, error = [], None, None
    try:
        async with asyncio.timeout(1.0):
            # Nested transaction is a SAVEPOINT in the existing repeatable-read API transaction.
            async with conn.transaction():
                old_timeout = await conn.fetchval("SHOW statement_timeout")
                await conn.execute("SET LOCAL statement_timeout = 750")
                rows = await conn.fetch(LATEST_SQL, [k[0] for k in keys], [k[1] for k in keys])
                hr = await conn.fetchrow("SELECT heartbeat_at,payload FROM trader_display_score_health WHERE worker='trader_live_scores'")
                if hr:
                    health = {"heartbeat_at": hr["heartbeat_at"].timestamp(),
                              "readback": object_value(hr["payload"])}
                await conn.execute("SELECT set_config('statement_timeout',$1,true)", old_timeout)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = "SCORE_EVIDENCE_READ_FAILED:" + type(exc).__name__
    return apply_rows(snapshot, list(rows), now=now, error=error, health=health)
