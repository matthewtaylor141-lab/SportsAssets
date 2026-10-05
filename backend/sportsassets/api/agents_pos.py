"""ARCHER'S AND SCOUT'S WORKSPACES (migration 217): READ-ONLY.

  GET /api/command/archer                      profile, status, desk, current
                                              estimates, outcomes, scorecard,
                                              candidate reviews (+ the
                                              workspace contract `sections`)
  GET /api/command/agents/archer               the same (route parity)
  GET /api/command/archer/estimates            filter by recommendation
  GET /api/command/archer/estimates/{id}       one estimate + its outcomes
  GET /api/command/eddie[...]                 (266) the historical alias of
                                              each Archer route above: the
                                              same payload plus
                                              alias_of: "archer"
  GET /api/command/scout                      profile, status, desk, sources,
                                              features, observations,
                                              tournaments, scorecard
  GET /api/command/agents/scout               the same
  GET /api/command/pos/reviews                the candidate-review workflow

All reads need the COMMAND read credential. There is NO write route here:
neither agent can be asked to act through this API, and no route touches an
order, a limit, an approval or a policy. Every section follows the shared
workspace contract: OK / EMPTY (with the reason) / UNAVAILABLE (the failed
read's exception type).
"""
from __future__ import annotations

import contextlib
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .agents_core import _pool, _read_section, require_read

router = APIRouter()

#: every read here is bounded and cannot write
STATEMENT_TIMEOUT = "8s"


@contextlib.asynccontextmanager
async def _ro(pool):
    """A pooled connection that is READ ONLY (default_transaction_read_only)
    with a bounded statement_timeout for the duration of the request, both
    reset before it returns to the pool. A write through it is refused by
    the database."""
    async with pool.acquire() as conn:
        await conn.execute("SET default_transaction_read_only = on")
        await conn.execute("SET statement_timeout = '%s'" % STATEMENT_TIMEOUT)
        try:
            yield conn
        finally:
            try:
                await conn.execute("RESET statement_timeout")
                await conn.execute("RESET default_transaction_read_only")
            except Exception:                                   # noqa: BLE001
                pass


def _ok(data, evidence=None) -> dict:
    return {"status": "OK", "why": None, "data": data,
            "evidence": list(evidence or [])[:200]}


async def _status_section(conn, agent: str) -> tuple:
    from ..agents import registry as R
    try:
        st = await R.status_of(conn, agent)
    except Exception as exc:                                    # noqa: BLE001
        st = {"agent_id": agent, "state": None, "status": "UNAVAILABLE",
              "why": type(exc).__name__}
    if st is None:
        st = {"agent_id": agent, "state": None,
              "why": "NOT_REGISTERED_THE_RUNNER_HAS_NOT_STARTED"}
    sec = {"status": "OK" if st.get("state") else "EMPTY",
           "why": st.get("why"), "data": st, "evidence": []}
    return st, sec


async def _metric_section(fn) -> tuple:
    try:
        met = await fn
        return met, _ok(met)
    except Exception as exc:                                    # noqa: BLE001
        return None, {"status": "UNAVAILABLE", "why": type(exc).__name__,
                      "data": None, "evidence": []}


def _authority_section(agent: str) -> dict:
    from ..agents import pos_authority as PA
    from ..agents import registry as R
    perms = R.IDENTITIES[agent]["tool_permissions"]
    return _ok({"authority": PA.AUTHORITY_STATUS[agent],
                "allowed": perms["allowed"], "denied": perms["denied"],
                "forbidden_actions": list(PA.FORBIDDEN_ACTIONS),
                "enforced_by": PA.profile(agent)["enforced_by"]})


async def _archer_workspace() -> dict:
    from ..agents import collaboration_loop as CL
    from ..agents import archer as E
    from ..agents import archer_runner as ER
    from ..agents import pos_authority as PA
    from ..agents import pos_workflow as W
    from ..agents import registry as R

    pool = await _pool()
    async with _ro(pool) as conn:
        if not await E.schema(conn):
            raise HTTPException(status_code=503, detail={
                "reason": E.R_NO_SCHEMA})
        st, status_sec = await _status_section(conn, R.ARCHER)
        current = await _read_section(
            E.estimates(conn, limit=50),
            empty_why="ARCHER_HAS_ESTIMATED_NO_CANDIDATE",
            evidence_of=lambda r: list(r.get("evidence") or []))
        outs = await _read_section(
            E.outcomes(conn, limit=50),
            empty_why="NO_ESTIMATED_CANDIDATE_HAS_FILLED_YET",
            evidence_of=lambda r: list(r.get("evidence_refs") or []))
        reviews = await _read_section(
            W.reviews(conn, limit=10),
            empty_why="NO_CANDIDATE_REVIEW_RECORDED",
            evidence_of=lambda r: list(r.get("evidence") or []))
        met, metrics = await _metric_section(E.metrics(conn))
        # RECOMMENDATIONS ARE NOT ORDERS: Archer's EXECUTE_NOW /
        # SKIP_EXECUTION counts beside BETTOR-originated orders submitted,
        # venue-acknowledged and filled (bettor_originated_status).
        funnel_sec = await _funnel_section(conn)
        try:
            desk = await E.desk(conn)
            desk_sec = _ok(desk)
        except Exception as exc:                                # noqa: BLE001
            desk = None
            desk_sec = {"status": "UNAVAILABLE", "why": type(exc).__name__,
                        "data": None, "evidence": []}
    ident = R.IDENTITIES[R.ARCHER]
    profile = dict(PA.profile(R.ARCHER),
                   recommendations=list(E.RECOMMENDATIONS),
                   hard_rule=("never recommend executing (EXECUTE_NOW / "
                              "REST_LIMIT / SPLIT) when the expected "
                              "executable EV is <= 0 or unmeasured"),
                   dimensions=list(E.DIMENSIONS),
                   peer_routing=list(CL.PEER_ROUTING["ARCHER"]),
                   persona={"manner": "fast, precise, controlled, "
                                      "institutional",
                            "grounding": "his estimate and outcome records "
                                         "only"},
                   runner={"enabled": ER.enabled(),
                           "interval_s": ER.INTERVAL_S,
                           "max_estimates_per_pass":
                               ER.MAX_ESTIMATES_PER_PASS,
                           "kill_switch": "ARCHER_RUNNER_ENABLED",
                           "kill_switch_historical_alias":
                               "EDDIE_RUNNER_ENABLED"})
    return {
        "agent": dict(st, role=ident["role"]), "profile": profile,
        "desk": desk, "metrics": met,
        "sections": {
            "status": status_sec,
            "authority": _authority_section(R.ARCHER),
            "desk": desk_sec,
            "current_estimates": current,
            "predicted_vs_realized": outs,
            "scorecard": metrics,
            "candidate_reviews": reviews,
            "execution_funnel": funnel_sec,
            "runner": _ok(profile["runner"]),
        },
        "execution_funnel": funnel_sec.get("data"),
        "production_effect": "NONE", "read_at": time.time(),
        "read_only": True}


async def _funnel_section(conn) -> dict:
    """Archer's recommendations apart from orders, and the SMALL LIVE --
    BETTOR ORIGINATED status beside the LEGACY MIRROR label."""
    from .. import bettor_originated_status as BOS
    try:
        got = await BOS.read_isolated(conn)
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "UNAVAILABLE", "why": type(exc).__name__,
                "data": None, "evidence": []}
    f = dict(got["archer_funnel"])
    f["small_live"] = {k: got["small_live"].get(k) for k in (
        "title", "status", "why", "chain_required", "venues")}
    f["legacy_mirror_label"] = (got["legacy_mirror"] or {}).get("label")
    return {"status": "OK" if f.get("status") == "OK" else "EMPTY",
            "why": f.get("why"), "data": f, "evidence": []}


async def _scout_workspace() -> dict:
    from ..agents import collaboration_loop as CL
    from ..agents import pos_authority as PA
    from ..agents import registry as R
    from ..agents import scout as S
    from ..agents import scout_runner as SR

    pool = await _pool()
    async with _ro(pool) as conn:
        if not await S.schema(conn):
            raise HTTPException(status_code=503, detail={
                "reason": S.R_NO_SCHEMA})
        st, status_sec = await _status_section(conn, R.SCOUT)
        srcs = await _read_section(
            S.sources(conn), empty_why="NO_SOURCE_DECLARED_YET",
            evidence_of=lambda r: [{"kind": "scout_sources",
                                    "id": r["source_id"], "href": None}])
        feats = await _read_section(
            S.features(conn), empty_why="NO_FEATURE_REGISTERED",
            evidence_of=lambda r: list(r.get("evidence") or []))
        obs = await _read_section(
            S.observations(conn, limit=50),
            empty_why="NO_COMPLIANT_OBSERVATION_RECORDED",
            evidence_of=lambda r: list(r.get("evidence") or []))
        tours = await _read_section(
            S.tournaments(conn), empty_why="NO_FORWARD_TEST_FROZEN",
            evidence_of=lambda r: [{"kind": "scout_feature_tournaments",
                                    "id": r["tournament_id"], "href": None}])
        met, metrics = await _metric_section(S.metrics(conn))
        try:
            desk = await S.desk(conn)
            desk_sec = _ok(desk)
        except Exception as exc:                                # noqa: BLE001
            desk = None
            desk_sec = {"status": "UNAVAILABLE", "why": type(exc).__name__,
                        "data": None, "evidence": []}
    ident = R.IDENTITIES[R.SCOUT]
    profile = dict(PA.profile(R.SCOUT),
                   compliance_checks=S.COMPLIANCE_CHECKS,
                   evaluator=S.EVALUATOR,
                   tournament_spec=S.TOURNAMENT_SPEC,
                   peer_routing=list(CL.PEER_ROUTING["SCOUT"]),
                   persona={"manner": "research-minded, "
                                      "validation-obsessed",
                            "grounding": "his source, feature, observation "
                                         "and tournament records only"},
                   runner={"enabled": SR.enabled(),
                           "interval_s": SR.INTERVAL_S,
                           "kill_switch": "SCOUT_RUNNER_ENABLED"})
    return {
        "agent": dict(st, role=ident["role"]), "profile": profile,
        "desk": desk, "metrics": met,
        "sections": {
            "status": status_sec,
            "authority": _authority_section(R.SCOUT),
            "desk": desk_sec,
            "sources": srcs,
            "features": feats,
            "tournaments": tours,
            "observations": obs,
            "scorecard": metrics,
            "runner": _ok(profile["runner"]),
        },
        "production_effect": "NONE", "read_at": time.time(),
        "read_only": True}


@router.get("/api/command/archer", dependencies=[Depends(require_read)])
async def archer_workspace(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _archer_workspace()


@router.get("/api/command/agents/archer", dependencies=[Depends(require_read)])
async def archer_workspace_agents(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _archer_workspace()


@router.get("/api/command/archer/estimates",
            dependencies=[Depends(require_read)])
async def archer_estimates(response: Response,
                          recommendation: str | None = Query(default=None),
                          limit: int = Query(default=50, ge=1, le=500)
                          ) -> dict:
    from ..agents import archer as E
    response.headers["Cache-Control"] = "no-store"
    if recommendation is not None and recommendation not in \
            E.RECOMMENDATIONS:
        raise HTTPException(status_code=400, detail={
            "reason": "THAT_IS_NOT_A_RECOMMENDATION",
            "recommendation": recommendation})
    pool = await _pool()
    async with _ro(pool) as conn:
        sec = await _read_section(
            E.estimates(conn, limit=limit, recommendation=recommendation),
            empty_why="NO_ESTIMATE_MATCHES",
            evidence_of=lambda r: list(r.get("evidence") or []))
    return {"estimates": sec, "production_effect": "NONE",
            "authority": E.AUTHORITY, "read_at": time.time(),
            "read_only": True}


@router.get("/api/command/archer/estimates/{estimate_id}",
            dependencies=[Depends(require_read)])
async def archer_estimate(estimate_id: str, response: Response) -> dict:
    from ..agents import archer as E
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with _ro(pool) as conn:
        try:
            got = await E.estimate_record(conn, estimate_id)
        except Exception as exc:                                # noqa: BLE001
            raise HTTPException(status_code=503, detail={
                "reason": "ARCHER_READ_FAILED", "detail": type(exc).__name__})
    if got is None:
        raise HTTPException(status_code=404, detail={
            "reason": "NO_SUCH_ESTIMATE", "estimate_id": estimate_id})
    return dict(got, read_at=time.time(), read_only=True)


# ── THE HISTORICAL ALIAS (migration 266): /api/command/eddie[...] ────────
# The execution agent was renamed EDDIE -> ARCHER. Each old route still
# answers -- with exactly the Archer payload plus `alias_of: "archer"`, so a
# bookmark or an old client keeps working and is told the canonical name.
ALIAS_OF = "archer"


def _aliased(payload: dict) -> dict:
    return dict(payload, alias_of=ALIAS_OF, historical_alias="EDDIE")


@router.get("/api/command/eddie", dependencies=[Depends(require_read)])
async def eddie_workspace_alias(response: Response) -> dict:
    return _aliased(await archer_workspace(response))


@router.get("/api/command/agents/eddie", dependencies=[Depends(require_read)])
async def eddie_workspace_agents_alias(response: Response) -> dict:
    return _aliased(await archer_workspace_agents(response))


@router.get("/api/command/eddie/estimates",
            dependencies=[Depends(require_read)])
async def eddie_estimates_alias(response: Response,
                                recommendation: str | None = Query(
                                    default=None),
                                limit: int = Query(default=50, ge=1, le=500)
                                ) -> dict:
    return _aliased(await archer_estimates(response,
                                           recommendation=recommendation,
                                           limit=limit))


@router.get("/api/command/eddie/estimates/{estimate_id}",
            dependencies=[Depends(require_read)])
async def eddie_estimate_alias(estimate_id: str, response: Response) -> dict:
    return _aliased(await archer_estimate(estimate_id, response))


@router.get("/api/command/scout", dependencies=[Depends(require_read)])
async def scout_workspace(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _scout_workspace()


@router.get("/api/command/agents/scout", dependencies=[Depends(require_read)])
async def scout_workspace_agents(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await _scout_workspace()


@router.get("/api/command/pos/reviews", dependencies=[Depends(require_read)])
async def pos_reviews(response: Response,
                      limit: int = Query(default=20, ge=1, le=200)) -> dict:
    from ..agents import pos_workflow as W
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with _ro(pool) as conn:
        sec = await _read_section(
            W.reviews(conn, limit=limit),
            empty_why="NO_CANDIDATE_REVIEW_RECORDED",
            evidence_of=lambda r: list(r.get("evidence") or []))
    return {"reviews": sec, "steps": [s for _, s, _ in W.STEPS],
            "production_effect": "NONE", "read_at": time.time(),
            "read_only": True}
