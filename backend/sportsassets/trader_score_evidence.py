"""Append-only display-score adapter, not an installed provider or trading gate.

Caller supplies a licensed provider payload and an independently certified
exact fixture binding. A repeatable event transaction serializes score writes.
No browser, order, settlement, model, network or credential path exists here.
"""
from __future__ import annotations
import hashlib
import json
from .trader_mode import normalize_game, epoch, object_value, num


async def record(conn, payload: dict, *, mapping: dict, now: float) -> dict:
    required = ("event_id", "provider_event_id", "home_id", "away_id", "sport", "league", "source")
    if mapping.get("verified") is not True or not mapping.get("evidence_id"):
        return {"recorded": False, "why": "CANONICAL_SCORE_MAPPING_NOT_VERIFIED"}
    if any(not mapping.get(k) or payload.get(k) != mapping[k] for k in required):
        return {"recorded": False, "why": "SCORE_MAPPING_DIMENSION_MISMATCH"}
    if mapping["home_id"] == mapping["away_id"]:
        return {"recorded": False, "why": "SCORE_PARTICIPANTS_NOT_DISTINCT"}
    raw = dict(payload, identity_verified=True, mapping_evidence_id=mapping["evidence_id"])
    normalized = normalize_game(raw, expected_event_id=mapping["event_id"], now=now)
    if normalized["status"] != "CURRENT":
        return {"recorded": False, "why": normalized["why"]}
    raw["source_at"] = epoch(raw["source_at"])
    try:
        body = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        return {"recorded": False, "why": "SCORE_PAYLOAD_NOT_FINITE_JSON"}
    if len(body.encode()) > 65536:
        return {"recorded": False, "why": "SCORE_PAYLOAD_TOO_LARGE"}
    key = "trader-game:" + hashlib.sha256(body.encode()).hexdigest()
    cid = "game:" + mapping["event_id"]
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", cid)
        previous = await conn.fetchrow(
            "SELECT event_key,payload FROM market_plane_events "
            "WHERE kind='TRADER_GAME_STATE' AND contract_id=$1 "
            "ORDER BY at DESC,event_key DESC LIMIT 1", cid)
        if previous:
            prev = object_value(previous["payload"])
            pat = epoch(prev.get("source_at"))
            if pat is None or raw["source_at"] < pat:
                return {"recorded": False, "why": "SCORE_SOURCE_TIME_REGRESSION"}
            if raw["source_at"] == pat and previous["event_key"] != key:
                seq, last_seq = num(raw.get("source_sequence")), num(prev.get("source_sequence"))
                if seq is None or last_seq is None or seq <= last_seq:
                    return {"recorded": False, "why": "SCORE_SAME_TIME_CONFLICT_WITHOUT_ADVANCING_SEQUENCE"}
        row = await conn.fetchval(
            "INSERT INTO market_plane_events(event_key,contract_id,kind,payload,at,label,authority) "
            "VALUES($1,$2,'TRADER_GAME_STATE',$3::jsonb,to_timestamp($4),'RESEARCH',"
            "'MARKET_DATA_ONLY_NO_ORDER_AUTHORITY') ON CONFLICT(event_key) DO NOTHING RETURNING event_key",
            key, cid, body, now)
    return {"recorded": row is not None, "duplicate": row is None,
            "event_key": key, "authority": "DISPLAY_ONLY"}
