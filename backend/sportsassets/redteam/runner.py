"""THE RED-TEAM RECEIPTS RUNNER (API process, beside Adriana's runner).

Every pass: evaluate the final interlock READ ONLY (redteam.readiness),
then APPEND its receipts (migration 315, append-only): the readiness
receipt, one control receipt per control (VENUE_HEALTH among them -- the
two-leg sentinel reads it), one profit-breaker receipt per mechanism and
one claim-exposure receipt per claim. A control / claim whose evidence is
unchanged inside RECEIPT_MIN_GAP_S is not re-appended. It writes nothing
else: no order, no authority, no PAPER row.

Kill switch: RED_TEAM_RUNNER_ENABLED=0 (default on).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from . import controls as C
from . import readiness as R

log = logging.getLogger(__name__)
SERVICE = "red_team_readiness"
INTERVAL_S = 300
FIRST_DELAY_S = 240
PASS_TIMEOUT_S = 240
RECEIPT_MIN_GAP_S = 3600.0


def enabled() -> bool:
    return os.getenv("RED_TEAM_RUNNER_ENABLED", "1").strip().lower() not in (
        "0", "false", "off", "no")


async def _latest_hash(conn, table: str, key_col: str, key: str,
                       time_col: str):
    r = await conn.fetchrow(
        "SELECT evidence_hash, extract(epoch FROM %s) at FROM %s WHERE "
        "%s = $1 ORDER BY %s DESC LIMIT 1" % (time_col, table, key_col,
                                             time_col), key)
    return (r["evidence_hash"], float(r["at"])) if r else (None, None)


async def write_receipts(conn, res: dict, *, now: float) -> dict:
    if not await C._has(conn, "red_team_readiness_receipts"):
        return {"status": "MIGRATION_315_NOT_APPLIED"}
    sha = res["implementation_sha"]
    n = {"readiness": 0, "controls": 0, "breakers": 0, "exposure": 0}
    body = {"status": res["status"], "checks": res["checks"],
            "blockers": res["blockers"]}
    h = C.evidence_hash(body)
    await conn.execute(
        "INSERT INTO red_team_readiness_receipts (receipt_id, computed_at, "
        " implementation_sha, status, blockers, checks, evidence_hash) "
        "VALUES ($1, to_timestamp($2), $3, $4, $5::jsonb, $6::jsonb, $7) "
        "ON CONFLICT (receipt_id) DO NOTHING",
        "rtr:%d:%s" % (int(now), h[:16]), now, sha, res["status"],
        json.dumps(res["blockers"]), json.dumps(res["checks"]), h)
    n["readiness"] = 1
    for name, c in res["controls"].items():
        ch = C.evidence_hash(_stable({k: c[k] for k in (
            "status", "blockers", "evidence")}))
        last, at = await _latest_hash(conn, "red_team_control_receipts",
                                      "control", name, "computed_at")
        # VENUE_HEALTH is the sentinel's live input: always appended
        if name != "VENUE_HEALTH" and last == ch and at and \
                now - at < RECEIPT_MIN_GAP_S:
            continue
        await conn.execute(
            "INSERT INTO red_team_control_receipts (receipt_id, computed_at,"
            " implementation_sha, control, status, blockers, evidence, "
            " evidence_hash) VALUES ($1, to_timestamp($2), $3, $4, $5, "
            " $6::jsonb, $7::jsonb, $8) ON CONFLICT (receipt_id) DO NOTHING",
            "rtc:%s:%d:%s" % (name, int(now), ch[:12]), now, sha, name,
            c["status"], json.dumps(c["blockers"]),
            json.dumps(c["evidence"], default=str), ch)
        n["controls"] += 1
    mechs = ((res["controls"].get("PROFIT_BREAKERS") or {}).get("evidence")
             or {}).get("mechanisms") or {}
    for m, v in mechs.items():
        mh = C.evidence_hash(v)
        last, at = await _latest_hash(conn,
                                      "red_team_profit_breaker_receipts",
                                      "mechanism", m, "computed_at")
        if last == mh and at and now - at < RECEIPT_MIN_GAP_S:
            continue
        await conn.execute(
            "INSERT INTO red_team_profit_breaker_receipts (receipt_id, "
            " computed_at, implementation_sha, mechanism, status, "
            " independent_events, mean_residual, confidence_bound, "
            " cumulative_residual, reason, evidence_hash, expected_pnl, "
            " realized_pnl) VALUES ($1, to_timestamp($2), $3, $4, $5, $6, "
            " $7::numeric, $8::numeric, $9::numeric, $10, $11, $12::numeric,"
            " $13::numeric) ON CONFLICT (receipt_id) DO NOTHING",
            "rtb:%s:%d" % (m, int(now)), now, sha, m, v["status"],
            int(v["independent_events"]), _num(v.get("mean_residual")),
            _num(v.get("confidence_bound_upper_90")),
            _num(v.get("cumulative_residual")), v.get("reason"), mh,
            _num(v.get("expected_pnl")), _num(v.get("realized_pnl")))
        n["breakers"] += 1
    for r in res.get("exposure_receipts") or []:
        last, at = await _latest_hash(conn,
                                      "red_team_claim_exposure_receipts",
                                      "claim_key", r["claim_key"],
                                      "observed_at")
        if last == r["evidence_hash"] and at and \
                now - at < RECEIPT_MIN_GAP_S:
            continue
        await conn.execute(
            "INSERT INTO red_team_claim_exposure_receipts (receipt_id, "
            " observed_at, implementation_sha, claim_key, event_key, "
            " payoff_fingerprint, alias_count, signed_notional, aliases, "
            " evidence_hash, event_notional, claim_limit_usd, "
            " event_limit_usd, eligible, blockers) VALUES ($1, "
            " to_timestamp($2), $3, $4, $5, $6, $7, $8::numeric, $9::jsonb, "
            " $10, $11::numeric, $12::numeric, $13::numeric, $14, $15::jsonb)"
            " ON CONFLICT (receipt_id) DO NOTHING",
            r["receipt_id"], now, sha, r["claim_key"], r["event_key"],
            r["payoff_fingerprint"], r["alias_count"],
            _num(r["signed_notional"]), json.dumps(r["aliases"]),
            r["evidence_hash"], _num(r["event_notional"]),
            _num(r["claim_limit_usd"]), _num(r["event_limit_usd"]),
            bool(r["eligible"]), json.dumps(r["blockers"]))
        n["exposure"] += 1
    return dict(n, status="OK")


VOLATILE = frozenset({"as_of", "computed_at"})


def _stable(x):
    """The evidence a receipt is deduplicated on: everything but the read's
    own clock (the receipt row keeps its computed_at)."""
    if isinstance(x, dict):
        return {k: _stable(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, (list, tuple)):
        return [_stable(v) for v in x]
    return x


def _num(v):
    if v is None:
        return None
    from decimal import Decimal
    try:
        return Decimal(str(v))
    except Exception:                                           # noqa: BLE001
        return None


async def pass_once(conn, *, now: float | None = None) -> dict:
    now = float(now if now is not None else time.time())
    async with conn.transaction(readonly=True):
        await conn.execute("SET LOCAL statement_timeout = 120000")
        res = await R.evaluate(conn, now=now)
    async with conn.transaction():
        wrote = await write_receipts(conn, res, now=now)
    out = {"status": res["status"], "blockers": res["blockers"],
           "wrote": wrote, "sha": res["implementation_sha"]}
    try:
        from .. import db as _db
        await _db.heartbeat(SERVICE, "ok", out, con=conn)
    except Exception:                                           # noqa: BLE001
        pass
    return out


async def run(get_pool, *, interval_s: float = INTERVAL_S,
              first_delay_s: float = FIRST_DELAY_S) -> None:
    if not enabled():
        log.info("red team: runner disabled (RED_TEAM_RUNNER_ENABLED)")
        return
    await asyncio.sleep(first_delay_s)
    while True:
        try:
            pool = await get_pool()
            async with asyncio.timeout(PASS_TIMEOUT_S):
                async with pool.acquire() as conn:
                    await pass_once(conn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            log.warning("red team: pass failed (%s)", type(exc).__name__)
            try:
                from .. import db as _db
                await _db.heartbeat(SERVICE, "error",
                                    {"error": type(exc).__name__})
            except Exception:                                   # noqa: BLE001
                pass
        await asyncio.sleep(interval_s)
