"""ROOT-CAUSE IMPROVEMENT CLUSTERS (owner R30 program section 20, migration
234 §3): GET /api/command/improvement-clusters (+ /{cluster_id}).
GET only, COMMAND auth (agents_core.require_read). READ ONLY.

THE HUMAN STEPS (R30B review: with no application path, "linked fix" and
"measured effect after the fix" could never be filled in production):

    POST /api/admin/improvement-clusters/{cluster_id}/link-fix
         {actor, commit_sha (40 hex), effective_at (epoch s), ref?,
          actor_class? HUMAN | ENGINEERING}
    POST /api/admin/improvement-clusters/{cluster_id}/assign-owner
         {actor, owner}
    POST /api/admin/improvement-clusters/{cluster_id}/close   {actor, note}
    POST /api/admin/improvement-clusters/{cluster_id}/reopen  {actor, note}

Admin token (the clear-halt pattern of api/command_live_parity.py). The
operator NAMES the person, who is recorded as both actor and recorded_by;
the database refuses an agent or system name, a malformed SHA, an owner
assignment that would change the status, anything after CLOSED but
REOPENED, and any human step inside the cluster runner's session. These
write only improvement_cluster_events (through agents.improvement_clusters'
own functions): a fix is a TEXT REFERENCE -- nothing here merges, deploys,
runs, approves or trades.

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
a zero. One READ ONLY transaction under a statement timeout. The GET routes
write nothing; nothing here merges, deploys or approves; it imports no order,
venue, execution, funded or paper module.
"""
from __future__ import annotations

import time

import re

from fastapi import APIRouter, Depends, HTTPException, Request

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


# ═════════════════════════════════════════════════════════════════════
# THE HUMAN STEPS (admin token; a named person; the database decides)
# ═════════════════════════════════════════════════════════════════════

ADMIN_PATH = "/api/admin/improvement-clusters"
ACTIONS = ("link-fix", "assign-owner", "close", "reopen")
_SHA = re.compile(r"^[0-9a-f]{40}$")


async def _require_admin(request: Request) -> None:
    from . import app as A
    A.require_admin(x_admin_token=request.headers.get("x-admin-token", ""))


def _body(b: dict | None, action: str) -> dict:
    """The validated arguments of one human step (400 with the reason)."""
    b = b or {}
    actor = str(b.get("actor") or "").strip()
    if not actor:
        raise HTTPException(400, detail={"reason": "ACTOR_REQUIRED"})
    out = {"actor": actor}
    if action == "link-fix":
        sha = str(b.get("commit_sha") or "").strip().lower()
        if not _SHA.match(sha):
            raise HTTPException(400, detail={
                "reason": "COMMIT_SHA_MUST_BE_40_HEX"})
        try:
            eff = float(b.get("effective_at"))
        except (TypeError, ValueError):
            raise HTTPException(400, detail={
                "reason": "EFFECTIVE_AT_EPOCH_SECONDS_REQUIRED"}) from None
        cls = str(b.get("actor_class") or "ENGINEERING").upper()
        if cls not in ("HUMAN", "ENGINEERING"):
            raise HTTPException(400, detail={"reason": "BAD_ACTOR_CLASS"})
        out.update(commit_sha=sha, effective_at=eff, actor_class=cls,
                   ref=(str(b["ref"]).strip()[:300] if b.get("ref")
                        else None))
    elif action == "assign-owner":
        owner = str(b.get("owner") or "").strip().upper()
        if not owner:
            raise HTTPException(400, detail={"reason": "OWNER_REQUIRED"})
        out["owner"] = owner
    else:
        note = str(b.get("note") or "").strip()
        if not note:
            raise HTTPException(400, detail={"reason": "NOTE_REQUIRED"})
        out["note"] = note[:2000]
    return out


async def apply(conn, cluster_id: str, action: str, args: dict) -> dict:
    """ONE HUMAN STEP through agents.improvement_clusters' own functions."""
    from ..agents import improvement_clusters as IC
    if action == "link-fix":
        return await IC.link_fix(conn, cluster_id, actor=args["actor"],
                                 commit_sha=args["commit_sha"],
                                 effective_at=args["effective_at"],
                                 ref=args.get("ref"),
                                 actor_class=args["actor_class"])
    if action == "assign-owner":
        return await IC.assign_owner(conn, cluster_id, actor=args["actor"],
                                     owner=args["owner"])
    if action == "close":
        return await IC.close(conn, cluster_id, actor=args["actor"],
                              note=args["note"])
    return await IC.reopen(conn, cluster_id, actor=args["actor"],
                           note=args["note"])


@router.post(ADMIN_PATH + "/{cluster_id}/{action}",
             dependencies=[Depends(_require_admin)])
async def cluster_human_step(cluster_id: str, action: str,
                             body: dict | None = None) -> dict:
    """A NAMED PERSON links a fix, assigns an owner, closes or reopens a
    root-cause cluster. 409 with the database's refusal when refused."""
    if action not in ACTIONS:
        raise HTTPException(404, detail={"reason": "NO_SUCH_ACTION"})
    if not cluster_id.startswith("rcc:") or len(cluster_id) != 28:
        raise HTTPException(404, detail={"reason": "NOT_A_CLUSTER_ID"})
    args = _body(body, action)
    pool = await _pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM improvement_clusters "
            " WHERE cluster_id = $1)", cluster_id)
        if not exists:
            raise HTTPException(404, detail={"reason": "NO_SUCH_CLUSTER"})
        got = await apply(conn, cluster_id, action, args)
    if not got.get("ok"):
        raise HTTPException(409, detail={"reason": "REFUSED_BY_THE_RECORD",
                                         "refusal": got.get("refusal"),
                                         "detail": got.get("why")})
    _CACHE.clear()
    return {"ok": True, "event_id": got.get("event_id"),
            "cluster_id": cluster_id, "action": action,
            "actor": args["actor"], "authority": "NONE_RECORDS_ONLY"}
