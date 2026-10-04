"""THE IMPROVEMENT PIPELINE BOARD: READ-ONLY (migration 221).

    GET /api/command/improvements          every item: stage, owner,
                                           protected flag, preserved
                                           disagreements, next required actor
    GET /api/command/improvements/{id}     one item's full trail, with every
                                           cited record linked

Read-only, COMMAND session auth (agents_core.require_read -> 401 without a
session), GET only. Every read runs inside ONE `BEGIN READ ONLY` transaction
with a bounded `statement_timeout`; each section in its own savepoint, so a
missing table (migration 221 not yet deployed) is reported as ABSENT with its
reason, never as a manufactured empty pipeline.

WHAT THE BOARD SAYS IS DERIVED, NOT INVENTED. The stage is the item's latest
transition event; "next required actor" is improvement_stages.next_required
over the recorded events; the disagreement counts are the preserved
disagreement rows (OPEN is live; DECIDED is a dissent a third party overruled
-- never shown as agreement); the runner's freshness is its latest run row.

WHAT THIS MODULE CANNOT DO, BY CONSTRUCTION. It imports only the standard
library, FastAPI, the command read dependency and the PURE stage rules
(agents/improvement_stages.py -- no I/O); no order, venue, execution, ledger,
paper or funded module, and not the pipeline runner. Its SQL is SELECT only
(tests/test_command_improvements_authority.py).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..agents import improvement_stages as S
from .agents_core import _pool, require_read

router = APIRouter()

VERSION = "COMMAND_IMPROVEMENTS_V1"
STATEMENT_TIMEOUT_MS = 5000
RUNNER_INTERVAL_S = 600.0       # agents/improvement_pipeline.INTERVAL_S (read)
RUNNER_STALE_S = 3 * RUNNER_INTERVAL_S
ITEM_RE = re.compile(r"^impr:[0-9a-f]{24}$")
MAX_LIMIT = 500
STAGE_LABELS = {
    S.EVIDENCE: "Evidence", S.HYPOTHESIS: "Hypothesis",
    S.PEER_CHALLENGE: "Peer challenge", S.OWNER_RESPONSE: "Owner response",
    S.EXPERIMENT: "Experiment", S.INDEPENDENT_EVALUATION:
    "Independent evaluation", S.ELIGIBLE_CHANGE: "Eligible change",
    S.CONTROLLED_RELEASE: "Controlled release",
    S.FORWARD_RESULT: "Forward result", S.ROLLED_BACK: "Rolled back",
    S.CLOSED: "Closed"}
DISCLOSURE = (
    "SHADOW / RESEARCH. This board records the improvement path; it changes "
    "nothing. No agent approves its own model, policy, code, feature or "
    "economic claim. Candidate patches are references (branch / commit SHA / "
    "PR URL) -- the system never pushes, merges or deploys; a human and the "
    "engineering process do. A CONTROLLED RELEASE needs a recorded human "
    "approver and an exact-SHA gate receipt. Protected areas (risk, "
    "accounting, settlement, execution authorization, financial controls, "
    "credentials, live capital limits) need human review and two independent "
    "reviews. Disagreements are preserved verbatim; a third party's decision "
    "is not consensus.")


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _ep(v):
    return v.timestamp() if isinstance(v, datetime) else v


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if k in ("body", "source_ref", "evidence_refs", "experiment_refs",
                 "forward_result", "protected_basis", "positions",
                 "resolution_ref", "summary"):
            v = _j(v)
        out[k] = v
    return out


class _Reads:
    def __init__(self, conn):
        self.conn = conn
        self.sections: dict = {}

    async def run(self, name: str, table: str, fn, default=None):
        try:
            present = bool(await self.conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", table))
        except Exception:                                       # noqa: BLE001
            present = False
        if not present:
            self.sections[name] = {"status": "ABSENT",
                                   "why": "TABLE_NOT_DEPLOYED:%s (migration "
                                          "221)" % table}
            return default
        try:
            async with self.conn.transaction():
                got = await fn(self.conn)
        except Exception as exc:                                # noqa: BLE001
            self.sections[name] = {"status": "UNAVAILABLE",
                                   "why": type(exc).__name__}
            return default
        self.sections[name] = {"status": "OK"}
        return got


def _evidence_keys(item: dict, events: list) -> set:
    keys = {(r.get("kind"), str(r.get("id")))
            for r in item.get("evidence_refs") or []}
    for e in events:
        if e.get("stage") == S.EVIDENCE:
            keys |= {(r.get("kind"), str(r.get("id")))
                     for r in e.get("evidence_refs") or []}
    return keys


def _patch(events: list) -> dict | None:
    for e in reversed(events):
        if e.get("stage") == S.EXPERIMENT and (
                e.get("patch_commit_sha") or e.get("patch_branch")
                or e.get("patch_pr_url") or e.get("tests_ref")):
            return {"branch": e.get("patch_branch"),
                    "commit_sha": e.get("patch_commit_sha"),
                    "pr_url": e.get("patch_pr_url"),
                    "tests_ref": e.get("tests_ref"),
                    "recorded_by": e.get("actor"),
                    "event_id": e.get("event_id"),
                    "reference_only": True}
    return None


def _reviews(item: dict, events: list) -> dict:
    rev = S.latest_reviews(events)
    return {"passes": sum(1 for o in rev.values() if o == "PASS"),
            "fails": sum(1 for o in rev.values() if o == "FAIL"),
            "required": int(item.get("required_independent_reviews") or 1),
            "reviewers": rev}


def summarize(item: dict, events: list, disagreements: list, *,
              now: float) -> dict:
    """The board card for one item. Pure."""
    last = next((e for e in reversed(events) if e.get("is_transition")), None)
    upd = _ep(item.get("updated_at"))
    return {
        "item_id": item["item_id"], "title": item["title"],
        "source_kind": item["source_kind"], "source_ref": item["source_ref"],
        "stage": item.get("stage"), "stage_seq": item.get("stage_seq"),
        "owner_agent": item["owner_agent"],
        "created_at": _iso(item.get("created_at")),
        "updated_at": _iso(item.get("updated_at")),
        "age_s": None if upd is None else round(now - float(upd), 1),
        "evidence_count": len(_evidence_keys(item, events)),
        "events": len(events),
        "protected": {"areas": list(item.get("protected_areas") or []),
                      "is_protected": bool(item.get("protected_areas")),
                      "requires_human_review":
                          bool(item.get("requires_human_review")),
                      "required_independent_reviews":
                          int(item.get("required_independent_reviews") or 1),
                      "basis": item.get("protected_basis") or {}},
        "disagreements": S.disagreement_summary(disagreements),
        "reviews": _reviews(item, events),
        "next_required": S.next_required(item, events),
        "patch": _patch(events),
        "last_transition": None if last is None else {
            "event_id": last["event_id"], "stage": last["stage"],
            "from_stage": last.get("from_stage"), "at": _iso(last["at"]),
            "actor": last["actor"], "actor_class": last["actor_class"]},
        "href": "/improvements?item=%s" % item["item_id"],
    }


async def _runner(conn) -> dict:
    r = await conn.fetchrow("SELECT run_id, started_at, finished_at, status, "
                            " summary FROM improve_runs "
                            " ORDER BY started_at DESC LIMIT 1")
    if r is None:
        return {"status": "NO_RUN_RECORDED", "last_run_at": None,
                "age_s": None, "stale": True,
                "why": "the improvement runner has not recorded a pass on "
                       "this database"}
    age = time.time() - r["finished_at"].timestamp()
    return {"status": r["status"], "run_id": r["run_id"],
            "last_run_at": _iso(r["finished_at"]), "age_s": round(age, 1),
            "stale": age > RUNNER_STALE_S, "stale_after_s": RUNNER_STALE_S,
            "summary": _j(r["summary"])}


async def build_board(conn, *, stage: str | None = None,
                      owner: str | None = None, limit: int = 200) -> dict:
    now = time.time()
    rd = _Reads(conn)

    async def items(c):
        rows = await c.fetch(
            "SELECT * FROM improve_items WHERE ($1::text IS NULL OR stage=$1)"
            "   AND ($2::text IS NULL OR owner_agent=$2) "
            " ORDER BY updated_at DESC, item_id LIMIT $3", stage, owner, limit)
        return [_row(r) for r in rows]

    its = await rd.run("items", "improve_items", items, default=None)
    ids = [i["item_id"] for i in its or []]

    async def events(c):
        rows = await c.fetch("SELECT * FROM improve_events "
                             " WHERE item_id = ANY($1::text[]) "
                             " ORDER BY item_id, event_id", ids)
        return [_row(r) for r in rows]

    async def disagreements(c):
        rows = await c.fetch("SELECT * FROM improve_disagreements "
                             " WHERE item_id = ANY($1::text[]) "
                             " ORDER BY item_id, opened_at", ids)
        return [_row(r) for r in rows]

    ev = await rd.run("events", "improve_events", events, default=[]) or []
    ds = await rd.run("disagreements", "improve_disagreements",
                      disagreements, default=[]) or []
    runner = await rd.run("runner", "improve_runs", _runner, default=None)
    by_ev: dict = {}
    for e in ev:
        by_ev.setdefault(e["item_id"], []).append(e)
    by_ds: dict = {}
    for d in ds:
        by_ds.setdefault(d["item_id"], []).append(d)
    cards = [summarize(i, by_ev.get(i["item_id"], []),
                       by_ds.get(i["item_id"], []), now=now)
             for i in its or []]
    counts = {s: 0 for s in list(S.STAGES) + [S.ROLLED_BACK, S.CLOSED]}
    for c in cards:
        if c["stage"] in counts:
            counts[c["stage"]] += 1
    return {
        "version": VERSION, "read_only": True,
        "authority": "NONE_READ_ONLY",
        "as_of": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "available": its is not None,
        "unavailable_why": None if its is not None else
        rd.sections.get("items", {}).get("why"),
        "filters": {"stage": stage, "owner": owner, "limit": limit},
        "stages": [{"stage": s, "seq": S.SEQ[s], "label": STAGE_LABELS[s],
                    "count": counts[s], "terminal": s in S.TERMINAL}
                   for s in list(S.STAGES) + [S.ROLLED_BACK, S.CLOSED]],
        "counts": {
            "items": len(cards),
            "protected": sum(1 for c in cards if c["protected"]["is_protected"]),
            "open_disagreements": sum(c["disagreements"]["open"]
                                      for c in cards),
            "dissent_preserved": sum(c["disagreements"]["decided"]
                                     for c in cards),
            "awaiting_human": sum(1 for c in cards if c["next_required"][
                "actor_class"] == S.HUMAN)},
        "items": cards,
        "runner": runner,
        "sections": rd.sections,
        "disclosure": DISCLOSURE,
    }


def _linked(refs) -> list:
    return [S.link(r) for r in refs or [] if isinstance(r, dict)]


async def build_item(conn, item_id: str) -> dict | None:
    now = time.time()
    rd = _Reads(conn)

    async def item(c):
        r = await c.fetchrow("SELECT * FROM improve_items WHERE item_id=$1",
                             item_id)
        return None if r is None else _row(r)

    it = await rd.run("item", "improve_items", item, default=None)
    if it is None:
        if rd.sections.get("item", {}).get("status") != "OK":
            return {"available": False,
                    "unavailable_why": rd.sections["item"].get("why"),
                    "sections": rd.sections}
        return None

    async def events(c):
        rows = await c.fetch("SELECT * FROM improve_events WHERE item_id=$1 "
                             " ORDER BY event_id", item_id)
        return [_row(r) for r in rows]

    async def disagreements(c):
        rows = await c.fetch("SELECT * FROM improve_disagreements "
                             " WHERE item_id=$1 ORDER BY opened_at", item_id)
        return [_row(r) for r in rows]

    ev = await rd.run("events", "improve_events", events, default=[]) or []
    ds = await rd.run("disagreements", "improve_disagreements",
                      disagreements, default=[]) or []

    async def evidence(c):
        out = []
        for kind, rid in sorted(_evidence_keys(it, ev)):
            ref = {"kind": kind, "id": rid}
            ok = await c.fetchval("SELECT improve_ref_exists($1::jsonb)",
                                  json.dumps(ref))
            out.append(dict(S.link(ref), exists=bool(ok)))
        return out

    evid = await rd.run("evidence", "improve_items", evidence, default=[])
    trail = []
    for e in ev:
        trail.append({
            "event_id": e["event_id"], "stage": e["stage"],
            "stage_label": STAGE_LABELS.get(e["stage"], e["stage"]),
            "is_transition": e["is_transition"],
            "from_stage": e.get("from_stage"), "at": _iso(e["at"]),
            "actor": e["actor"], "actor_class": e["actor_class"],
            "stance": e.get("stance"), "outcome": e.get("outcome"),
            "body": e.get("body") or {},
            "source": S.link(e["source_ref"]) if e.get("source_ref") else None,
            "evidence": _linked(e.get("evidence_refs")),
            "experiments": _linked(e.get("experiment_refs")),
            "patch": None if not (e.get("patch_commit_sha")
                                  or e.get("patch_branch")
                                  or e.get("patch_pr_url")
                                  or e.get("tests_ref")) else {
                "branch": e.get("patch_branch"),
                "commit_sha": e.get("patch_commit_sha"),
                "pr_url": e.get("patch_pr_url"),
                "tests_ref": e.get("tests_ref"), "reference_only": True},
            "gate_receipt": None if not e.get("gate_receipt_sha") else {
                "ref": e.get("gate_receipt_ref"),
                "sha": e.get("gate_receipt_sha")},
            "release_ref": e.get("release_ref"),
            "monitoring": None if not e.get("monitoring_start") else {
                "start": _iso(e["monitoring_start"]),
                "end": _iso(e["monitoring_end"]),
                "forward_result": e.get("forward_result")},
            "rollback_ref": e.get("rollback_ref"),
            "recorded_by": e["recorded_by"],
        })
    reached = {}
    for e in ev:
        if e["is_transition"]:
            reached[e["stage"]] = _iso(e["at"])
    path = [{"stage": s, "label": STAGE_LABELS[s], "seq": S.SEQ[s],
             "reached": s in reached, "at": reached.get(s),
             "current": it.get("stage") == s,
             "events": sum(1 for e in ev if e["stage"] == s)}
            for s in S.STAGES]
    card = summarize(it, ev, ds, now=now)
    return {
        "version": VERSION, "read_only": True, "available": True,
        "authority": "NONE_READ_ONLY",
        "as_of": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "item": dict(card, problem_statement=it["problem_statement"],
                     statement_basis=it["statement_basis"],
                     created_by=it["created_by"],
                     terminal=it.get("stage") in S.TERMINAL,
                     closed=None if it.get("stage") not in S.TERMINAL else
                     next((t for t in reversed(trail)
                           if t["stage"] in S.TERMINAL), None)),
        "path": path,
        "trail": trail,
        "disagreements": [{
            "disagreement_id": d["disagreement_id"], "stage": d["stage"],
            "parties": d["parties"], "positions": [
                dict(p, source=S.link(p.get("source_ref") or {}))
                for p in d.get("positions") or []],
            "opened_at": _iso(d["opened_at"]), "state": d["state"],
            "open": d["state"] == "OPEN",
            "resolved_by": d.get("resolved_by"),
            "resolution": d.get("resolution"),
            "resolution_source": S.link(d["resolution_ref"])
            if d.get("resolution_ref") else None,
            "resolved_at": _iso(d.get("resolved_at")),
            "consensus": bool(d.get("consensus")),
            "raised_event_id": d.get("raised_event_id")} for d in ds],
        "evidence": evid,
        "sections": rd.sections,
        "disclosure": DISCLOSURE,
    }


async def _read_only(fn):
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            return await fn(conn)


@router.get("/api/command/improvements", dependencies=[Depends(require_read)])
async def improvements_index(response: Response,
                             stage: str | None = Query(None, max_length=40),
                             owner: str | None = Query(None, max_length=40),
                             limit: int = Query(200, ge=1, le=MAX_LIMIT)
                             ) -> dict:
    response.headers["Cache-Control"] = "no-store"
    st = str(stage).upper() if stage else None
    ow = str(owner).upper() if owner else None
    if st is not None and st not in S.SEQ:
        raise HTTPException(status_code=400, detail={
            "reason": "UNKNOWN_STAGE", "stages": list(S.SEQ)})
    if ow is not None and ow not in S.OWNER_AGENTS:
        raise HTTPException(status_code=400, detail={
            "reason": "UNKNOWN_OWNER", "owners": list(S.OWNER_AGENTS)})
    try:
        return await _read_only(lambda c: build_board(c, stage=st, owner=ow,
                                                      limit=limit))
    except HTTPException:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "IMPROVEMENTS_READ_FAILED",
            "detail": type(exc).__name__})


@router.get("/api/command/improvements/{item_id}",
            dependencies=[Depends(require_read)])
async def improvements_item(item_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    if not ITEM_RE.match(str(item_id)):
        raise HTTPException(status_code=404, detail={
            "reason": "NOT_AN_IMPROVEMENT_ID"})
    try:
        got = await _read_only(lambda c: build_item(c, item_id))
    except HTTPException:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "IMPROVEMENTS_READ_FAILED",
            "detail": type(exc).__name__})
    if got is None:
        raise HTTPException(status_code=404, detail={
            "reason": "NO_SUCH_IMPROVEMENT", "item_id": item_id})
    return got
