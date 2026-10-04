"""ROOT-CAUSE IMPROVEMENT CLUSTERS (owner R30 program section 20, migration
234 §3): GET /api/command/improvement-clusters (+ /{cluster_id}).
GET only, COMMAND auth (agents_core.require_read). READ ONLY.

ANSWERS:
    {status, why, version, computed_at, repeat_min, authority, disclosure,
     clusters: [{cluster_id, cluster_key, source, finding_class,
                 target_agent, title, owner, status, count, by_state,
                 first_seen_at, last_seen_at, member_basis,
                 sample_member_ids, affected {strategies, markets, ...},
                 linked_fix {commit_sha, ref, effective_at, linked_by} | null,
                 measured_effect {basis, before, after, difference / log rate
                                  ratio, ci95, status, why, verdict},
                 last_recorded_measurement, current_defect_rate,
                 folded {improve_items}, events [...]}]}

Every figure comes from agents.improvement_clusters.view over the recorded
rows (karen_challenges, paper_audrey_findings, the challenged records, the
cluster events); an unmeasurable figure is UNAVAILABLE with its reason, never
a zero. One READ ONLY transaction under a statement timeout. Nothing here
writes, links a fix, merges, deploys or approves; it imports no order, venue,
execution, funded or paper module.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/improvement-clusters"
STATEMENT_TIMEOUT_MS = 8000
CACHE_S = 15.0
_CACHE: dict = {}
DISCLOSURE = (
    "ROOT-CAUSE WORK ITEMS: repeated Karen / Audrey findings, one item per "
    "class. A linked fix is a reference a named person recorded; its effect "
    "is measured, never assumed. Karen's challenge counts are a throttled "
    "sample (three per detector per pass): a Karen rule's defect rate is "
    "measured on its own table. No authority: nothing here merges, deploys, "
    "approves or trades.")


def _clean(v):
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if v.__class__.__name__ == "Decimal":
        return float(v)
    return v


async def read(conn, *, now: float, cluster_id: str | None = None) -> dict:
    """The view inside a READ ONLY transaction (nested: a savepoint)."""
    from ..agents import improvement_clusters as IC
    nested = conn.is_in_transaction()
    tr = conn.transaction(readonly=not nested)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        got = await IC.view(conn, now=now, cluster_id=cluster_id)
    finally:
        await tr.rollback()
    return _clean(got)


def _envelope(got: dict, now: float) -> dict:
    from ..agents import improvement_clusters as IC
    return {"status": got.get("status"), "why": got.get("why"),
            "version": IC.VERSION, "computed_at": now,
            "repeat_min": IC.REPEAT_MIN, "authority": "NONE_RECORDS_ONLY",
            "disclosure": DISCLOSURE, "clusters": got.get("clusters") or []}


@router.get(PATH, dependencies=[Depends(require_read)])
async def improvement_clusters() -> dict:
    now = time.time()
    hit = _CACHE.get("all")
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            got = await read(conn, now=now)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE",
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "computed_at": now, "clusters": [],
                "disclosure": DISCLOSURE}
    out = _envelope(got, now)
    _CACHE["all"] = (now, out)
    return out


@router.get(PATH + "/{cluster_id}", dependencies=[Depends(require_read)])
async def improvement_cluster(cluster_id: str) -> dict:
    if not cluster_id.startswith("rcc:") or len(cluster_id) != 28:
        raise HTTPException(404, detail={"reason": "NOT_A_CLUSTER_ID"})
    now = time.time()
    pool = await _pool()
    async with pool.acquire() as conn:
        got = await read(conn, now=now, cluster_id=cluster_id)
    if not got.get("clusters"):
        raise HTTPException(404, detail={"reason": "NO_SUCH_CLUSTER"})
    return _envelope(got, now)
