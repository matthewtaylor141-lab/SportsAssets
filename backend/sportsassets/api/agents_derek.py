"""DEREK'S WORKSPACE: GET /api/command/agents/derek (+ one decision's record).

READ-ONLY. Every section is {"status": OK|EMPTY|UNAVAILABLE, "why", "data",
"evidence"}; EMPTY carries its named reason and UNAVAILABLE the failed read's
exception type. An empty table is never styled as success and unknown is never
zero. Behind the same COMMAND read credential as every command read (resolved
at request time through `api.app`, so importing this module never imports the
app).
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..db import get_pool

router = APIRouter()

OK, EMPTY, UNAVAILABLE = "OK", "EMPTY", "UNAVAILABLE"
BASE = "/api/command/agents/derek"


async def require_read(request: Request) -> str:
    from . import app as A
    return A.require_command(bt_command=request.cookies.get("bt_command", ""),
                             x_desk_token=request.headers.get("x-desk-token",
                                                              ""),
                             x_admin_token=request.headers.get(
                                 "x-admin-token", ""))


async def require_write(request: Request) -> str:
    from . import app as A
    return A.require_command_control(
        x_admin_token=request.headers.get("x-admin-token", ""),
        x_desk_token=request.headers.get("x-desk-token", ""),
        bt_control=request.cookies.get("bt_control", ""),
        bt_command=request.cookies.get("bt_command", ""))


def _sec(status, data=None, why=None, evidence=None) -> dict:
    return {"status": status, "why": why, "data": data,
            "evidence": list(evidence or [])}


def _j(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


def _iso(t):
    return None if t is None else (t.isoformat() if hasattr(t, "isoformat")
                                   else t)


def _ep(t):
    return None if t is None else (t.timestamp() if hasattr(t, "timestamp")
                                   else float(t))


async def _regclass(conn, name) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    name))


def _decision_href(did) -> str:
    return "%s/decisions/%s" % (BASE, did)


def _decision_row(r) -> dict:
    d = dict(r)
    for k in ("checks", "latency", "evidence", "features"):
        d[k] = _j(d.get(k))
    for k in ("decided_at", "pinnacle_at", "model_at", "recorded_at"):
        d[k] = _iso(d.get(k))
    if d.get("qty") is not None:
        d["qty"] = float(d["qty"])
    d["gross_edge_percentage_points"] = (
        None if d.get("gross_edge_pp") is None
        else round(float(d["gross_edge_pp"]) * 100.0, 6))
    d["units"] = {"gross_edge_pp": "probability points as a fraction "
                                   "(0.05 = 5 pp) on a $0/$1 contract",
                  "expected_*_usd": "dollars for qty contracts",
                  "expected_net_roi": "net / (acquisition cost + fees)"}
    d["evidence_links"] = ((d.get("evidence") or {}).get("links")
                           or [{"kind": "derek_entry_decisions",
                                "id": d["decision_id"],
                                "href": _decision_href(d["decision_id"])}])
    return d


# ── SECTIONS ─────────────────────────────────────────────────────────────

async def status(conn) -> dict:
    try:
        from ..agents import registry as REG
        got = await REG.status_of(conn, "DEREK")
        if got:
            return _sec(OK, got, evidence=[{"kind": "agent_status",
                                            "id": "DEREK",
                                            "href": "/api/command/agents"}])
    except Exception:                                          # noqa: BLE001
        pass
    if not await _regclass(conn, "derek_entry_decisions"):
        return _sec(UNAVAILABLE, why="migration 153 is not applied here")
    r = await conn.fetchrow(
        "SELECT max(decided_at) AS last, count(*) AS n "
        "  FROM derek_entry_decisions")
    c = (await conn.fetchrow("SELECT max(at) AS last FROM "
                             "derek_coverage_census")
         if await _regclass(conn, "derek_coverage_census") else None)
    data = {"source": ("derived from Derek's own records: the agent registry "
                       "(migration 152) has no status row here"),
            "decisions_recorded": int(r["n"]),
            "last_decision_at": _iso(r["last"]),
            "last_census_at": _iso(c["last"]) if c else None}
    if not int(r["n"]) and not (c and c["last"]):
        return _sec(EMPTY, data, why=(
            "Derek has recorded nothing: after_cycle has not run against "
            "this database"))
    return _sec(OK, data)


async def versions(conn) -> dict:
    from ..agents import coverage as COV
    from ..agents import derek_policy as DP
    from .. import bettor_funded_model as FM
    pol = await DP.policy_params(conn)
    model = None
    try:
        got = await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT,
                                verify=False)
        m = got.get("model") or {}
        model = ({"state": "APPROVED", "model_id": m.get("model_id"),
                  "model_version": m.get("model_version"),
                  "approved_by": m.get("approved_by"),
                  "approved_at": _iso(m.get("approved_at")),
                  "display_only": "verification runs at decision time"}
                 if got.get("ok") else
                 {"state": "NONE", "refusal": got.get("refusal"),
                  "dependency": DP.DEP_ENGINEERING})
    except Exception as exc:                                   # noqa: BLE001
        model = {"state": "UNAVAILABLE", "why": type(exc).__name__}
    # WHAT THE MODEL IS, AND WHAT THE POLICY'S USE OF IT IS, wherever shown.
    model = dict(model, description=FM.ENTRY_PAYOUT_DESCRIPTION,
                 entry_policy_use=FM.ENTRY_POLICY_AGREEMENT_IS,
                 minimums=FM.qualification_minimums(FM.KEY_ENTRY_PAYOUT))
    try:
        from ..workers import ext_pinnacle_loop as L
        code = L._code_identity()
    except Exception as exc:                                   # noqa: BLE001
        code = {"why": type(exc).__name__}
    return _sec(OK, {"policy": {"key": DP.POLICY_KEY,
                                "version": pol.get("version"),
                                "source": pol.get("source"),
                                "params": pol.get("params"),
                                "param_units": DP.PARAM_UNITS,
                                "combination_policy": DP.COMBINATION_POLICY,
                                "edge_tolerance_pp": DP.EDGE_TOLERANCE_PP},
                     "internal_model": dict(model, model_key=FM.KEY_ENTRY_PAYOUT),
                     "probability_source": "PINNACLE_DEVIG_V1",
                     "coverage_census": COV.VERSION,
                     "code": code})


async def coverage(conn) -> dict:
    if not await _regclass(conn, "derek_coverage_census"):
        return _sec(UNAVAILABLE, why="migration 153 is not applied here")
    r = await conn.fetchrow(
        "SELECT census_id, at, categories, blocked_by_reason, sample "
        "  FROM derek_coverage_census ORDER BY at DESC LIMIT 1")
    if r is None:
        return _sec(EMPTY, why=("no coverage census has been recorded: "
                                "after_cycle has not run on this database"))
    data = {"census_id": r["census_id"], "at": _iso(r["at"]),
            "categories": _j(r["categories"]),
            "blocked_by_reason": _j(r["blocked_by_reason"]),
            "sample": _j(r["sample"])}
    return _sec(OK, data, evidence=[{"kind": "derek_coverage_census",
                                     "id": r["census_id"],
                                     "href": BASE + "#coverage"}])


async def subscription(conn) -> dict:
    from ..agents import derek_policy as DP
    raw = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1",
        "ext_pinnacle_last_cycle") if await _regclass(
            conn, "ingestion_state") else None
    hb = _j(raw) or {}
    dig = hb.get("market_subscription") if isinstance(hb, dict) else None
    try:
        from .. import bettor_stream_currency as SC
        missing = list(SC.MISSING_PRECONDITIONS)
    except Exception as exc:                                   # noqa: BLE001
        missing = ["UNREADABLE:%s" % type(exc).__name__]
    deps = []
    state = (dig or {}).get("subscription_state")
    if state == "DISABLED_BY_CONFIGURATION" or dig is None:
        deps.append({"what": "market-data subscription",
                     "state": state or "NOT_REPORTED",
                     "class": DP.DEP_ENGINEERING_CONFIGURATION,
                     "why": ("the subscription is enabled by configuration "
                             "(BETTOR_MARKET_SUBSCRIPTION)")})
    for m in missing:
        deps.append({"what": m, "class": DP.DEP_EVIDENCE,
                     "why": ("what the venue guarantees about timing is a "
                             "fact about its published protocol, not "
                             "something built here")})
    data = {"digest": dig, "p5_and_other_missing_preconditions": missing,
            "dependencies": deps}
    if dig is None:
        return _sec(EMPTY, data, why=(
            "no cycle heartbeat carries a subscription digest on this "
            "database: the scheduled loop has not reported one"))
    return _sec(OK, data)


async def decisions(conn, *, limit: int = 50) -> dict:
    if not await _regclass(conn, "derek_entry_decisions"):
        return _sec(UNAVAILABLE, why="migration 153 is not applied here")
    rows = [_decision_row(r) for r in await conn.fetch(
        "SELECT decision_id, valuation_id, fixture, us_market_slug, side, "
        "       decided_at, policy_version, pinnacle_p, pinnacle_at, "
        "       pinnacle_qualification, model_p, model_version, model_at, "
        "       model_qualification, executable_price, qty, gross_edge_pp, "
        "       expected_gross_profit_usd, fees_usd, expected_net_profit_usd,"
        "       expected_net_roi, verdict, refusal, latency, decided_by, "
        "       checks, evidence, NULL::jsonb AS features, recorded_at "
        "  FROM derek_entry_decisions ORDER BY decided_at DESC, decision_id "
        " LIMIT $1", int(limit))]
    for d in rows:
        d["checks"] = [{k: c.get(k) for k in ("check", "status", "blocks",
                                               "refusal", "dependency")}
                       for c in (d.get("checks") or [])]
        d.pop("evidence", None)
    if not rows:
        return _sec(EMPTY, [], why=(
            "no entry decision has been recorded: no candidate has been "
            "evaluated since Derek was installed"))
    return _sec(OK, rows, evidence=[
        {"kind": "derek_entry_decisions", "id": d["decision_id"],
         "href": _decision_href(d["decision_id"])} for d in rows])


async def opportunity_queue(conn) -> dict:
    if not await _regclass(conn, "derek_entry_decisions"):
        return _sec(UNAVAILABLE, why="migration 153 is not applied here")
    rows = [dict(r) for r in await conn.fetch(
        "SELECT decision_id, us_market_slug, side, verdict, refusal, "
        "       gross_edge_pp, expected_net_profit_usd, expected_net_roi, "
        "       pinnacle_p, model_p, executable_price, decided_at "
        "  FROM derek_entry_decisions "
        " WHERE decided_at > now() - interval '30 minutes' "
        "   AND (verdict = 'ENTER' OR gross_edge_pp IS NOT NULL) "
        " ORDER BY (verdict = 'ENTER') DESC, gross_edge_pp DESC NULLS LAST "
        " LIMIT 25")]
    for r in rows:
        r["decided_at"] = _iso(r["decided_at"])
    if not rows:
        return _sec(EMPTY, [], why=(
            "no candidate reached a priced edge in the last 30 minutes"))
    return _sec(OK, rows, evidence=[
        {"kind": "derek_entry_decisions", "id": r["decision_id"],
         "href": _decision_href(r["decision_id"])} for r in rows])


async def plans_fills(conn) -> dict:
    if not await _regclass(conn, "bettor_funded_intents"):
        return _sec(UNAVAILABLE, why="bettor_funded_intents is absent")
    if not await _regclass(conn, "derek_entry_decisions"):
        return _sec(UNAVAILABLE, why="migration 153 is not applied here")
    # BY ID, NEVER BY PROXIMITY. The intent names the Derek decision that
    # authorised it (decision_ref.derek_decision_id, written by the funded
    # connector from the entry gate's answer). This used to match on slug,
    # side and a five-minute window, which pairs two ENTER decisions on one
    # market with the same order and cannot tell which one sent it.
    rows = [dict(r) for r in await conn.fetch(
        "SELECT d.decision_id, i.intent_id, i.state, "
        "       i.quantity::float8 AS quantity, i.limit_price, i.created_at, "
        "       i.sent_at, i.venue_order_id, "
        "       coalesce((SELECT sum(f.qty) FROM bettor_funded_fills f "
        "                  WHERE f.intent_id = i.intent_id "
        "                    AND f.direction = 'ENTRY'), 0)::float8 "
        "           AS filled_qty, "
        "       coalesce((SELECT array_agg(f.fill_id ORDER BY f.at, f.fill_id)"
        "                   FROM bettor_funded_fills f "
        "                  WHERE f.intent_id = i.intent_id "
        "                    AND f.direction = 'ENTRY'), '{}'::text[]) "
        "           AS fill_ids "
        "  FROM derek_entry_decisions d "
        "  JOIN bettor_funded_intents i "
        "    ON i.kind = 'ENTRY' "
        "   AND i.decision_ref->>'derek_decision_id' = d.decision_id "
        " WHERE d.verdict = 'ENTER' ORDER BY i.created_at DESC LIMIT 25")]
    for r in rows:
        for k in ("created_at", "sent_at"):
            r[k] = _iso(r[k])
        for k in ("limit_price",):
            r[k] = None if r[k] is None else float(r[k])
        r["fill_ids"] = list(r["fill_ids"] or [])
    if not rows:
        sent = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE kind = 'ENTRY'")
        return _sec(EMPTY, [], why=(
            "no funded order has been sent" if not sent else
            "no funded entry order names a Derek ENTER decision "
            "(bettor_funded_intents.decision_ref.derek_decision_id)"))
    return _sec(OK, rows, evidence=[
        {"kind": "bettor_funded_intents", "id": r["intent_id"],
         "href": "/api/command/xavier/%s" % r["intent_id"]} for r in rows])


async def handoffs(conn) -> dict:
    if not await _regclass(conn, "agent_position_handoffs"):
        return _sec(UNAVAILABLE, why=(
            "agent_position_handoffs is absent: migration 152 (the agent "
            "core) is not applied here"))
    derek_id = ("(SELECT i.decision_ref->>'derek_decision_id' "
                "   FROM bettor_funded_intents i "
                "  WHERE i.intent_id = h.entry_intent_id)"
                if await _regclass(conn, "bettor_funded_intents")
                else "NULL::text")
    rows = [dict(r) for r in await conn.fetch(
        "SELECT h.*, %s AS derek_decision_id "
        "  FROM agent_position_handoffs h "
        " WHERE h.from_agent = 'DEREK' ORDER BY h.handoff_at DESC NULLS LAST "
        " LIMIT 25" % derek_id)]
    for r in rows:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif v is not None and type(v).__name__ == "Decimal":
                r[k] = float(v)
    if not rows:
        return _sec(EMPTY, [], why=(
            "no position has been handed to Xavier: no Derek entry has "
            "filled (funded submission is disabled)"))
    return _sec(OK, rows, evidence=[
        {"kind": "agent_position_handoffs", "id": r["entry_intent_id"],
         "href": "/api/command/xavier/%s" % r["entry_intent_id"]}
        for r in rows])


async def latency(conn) -> dict:
    if not await _regclass(conn, "derek_entry_decisions"):
        return _sec(UNAVAILABLE, why="migration 153 is not applied here")
    r = await conn.fetchrow(
        "SELECT count(*) AS n, "
        "  percentile_cont(0.5) WITHIN GROUP (ORDER BY "
        "     (latency->>'source_to_decision_s')::float8) AS s2d_median, "
        "  max((latency->>'source_to_decision_s')::float8) AS s2d_max, "
        "  count(*) FILTER (WHERE latency->>'decision_to_send_s' IS NOT NULL)"
        "     AS sent "
        "  FROM derek_entry_decisions "
        " WHERE decided_at > now() - interval '24 hours'")
    n = int(r["n"] or 0)
    data = {"window": "24h", "decisions": n,
            "source_to_decision_s": {"median": r["s2d_median"],
                                     "max": r["s2d_max"],
                                     "basis": ("the provider's own "
                                               "last_update to the decision")},
            "decision_to_send_s": (None if not r["sent"] else "see decisions"),
            "send_to_ack_s": None}
    if not n:
        return _sec(EMPTY, data, why="no decision in the last 24 hours, so "
                                     "no latency was measured")
    if not r["sent"]:
        data["decision_to_send"] = {"status": "EMPTY",
                                    "why": "no funded order has been sent"}
        data["send_to_ack"] = {"status": "EMPTY",
                               "why": "no funded order has been sent"}
    return _sec(OK, data)


async def performance(conn) -> dict:
    if not await _regclass(conn, "derek_entry_decisions"):
        return _sec(UNAVAILABLE, why="migration 153 is not applied here")
    rows = [dict(r) for r in await conn.fetch(
        "SELECT d.verdict, count(*) AS decisions, "
        "       count(DISTINCT d.fixture) AS fixtures, "
        "       count(*) FILTER (WHERE v.outcome_known) AS resolved, "
        "       count(DISTINCT d.fixture) FILTER (WHERE v.outcome_known) "
        "           AS resolved_fixtures, "
        "       avg(LEAST(d.pinnacle_p, coalesce(d.model_p, d.pinnacle_p))) "
        "           FILTER (WHERE v.outcome_known) AS mean_headline_p, "
        "       avg(v.outcome::float8) FILTER (WHERE v.outcome_known) "
        "           AS realised_frequency "
        "  FROM derek_entry_decisions d "
        "  LEFT JOIN external_valuations v ON v.id = d.valuation_id "
        " GROUP BY d.verdict ORDER BY d.verdict")]
    data = {"by_verdict": rows,
            "meaning": ("shadow agreement between the headline probability "
                        "and resolved outcomes, counted in fixtures. Not "
                        "money: realised P&L exists only for funded fills, "
                        "which are read from bettor_funded_economics"),
            "funded_realised": {"status": "EMPTY",
                                "why": "no funded order has been sent"}}
    if not any(int(r["resolved"] or 0) for r in rows):
        return _sec(EMPTY, data, why=(
            "no Derek decision has a resolved outcome yet: outcomes arrive "
            "as fixtures settle"))
    return _sec(OK, data)


async def collection(conn) -> dict:
    from ..agents import derek as D
    got = await D.collection(conn, now=time.time())
    po = got.get("pair_observations") or {}
    ent = got.get("entry_decisions") or {}
    if not po.get("available") and not ent.get("available"):
        return _sec(UNAVAILABLE, got, why=(
            "neither the observation tables nor Derek's decisions exist here"))
    observed = (po.get("observed") or {}) if isinstance(
        po.get("observed"), dict) else {}
    if not int(observed.get("fixtures") or 0) and \
            not int(ent.get("fixtures_decided") or 0):
        return _sec(EMPTY, got, why=(
            "no fixture has been observed or decided yet; every count below "
            "is a measured zero, and the dependencies say what produces more"))
    return _sec(OK, got)


async def model_qualification(conn) -> dict:
    """THE INTERNAL ENTRY MODEL'S QUALIFICATION PATH: the research
    observations by price cohort (never pooled), the exact minimums in
    fixtures, and each registered model's declared training population and
    recorded approval eligibility."""
    from .. import bettor_funded_model as FM
    data: dict[str, Any] = {
        "model_key": FM.KEY_ENTRY_PAYOUT,
        "description": FM.ENTRY_PAYOUT_DESCRIPTION,
        "entry_policy_use": FM.ENTRY_POLICY_AGREEMENT_IS,
        "minimums": FM.qualification_minimums(FM.KEY_ENTRY_PAYOUT),
        # THE SAME CONSTANTS, PER MODEL KEY, in fixtures
        "minimums_by_model_key": {k: FM.qualification_minimums(k) for k in (
            FM.KEY_MIDDLE, FM.KEY_HEDGE_GIVEN_PRIMARY, FM.KEY_ENTRY_PAYOUT)},
        "cohorts": [FM.COHORT_DISPLAYED, FM.COHORT_EXECUTABLE]}
    if not await _regclass(conn, "derek_research_observations"):
        return _sec(UNAVAILABLE, data, why="migration 170 is not applied here")
    from ..agents import derek_research as DR
    data["research_observations"] = await DR.summary(conn)
    models = []
    if await _regclass(conn, "bettor_funded_models"):
        for r in await conn.fetch(
                "SELECT model_id, model_version, state, approved_by, "
                "       approved_at, training_provenance, evaluation "
                "  FROM bettor_funded_models WHERE model_key = $1 "
                " ORDER BY created_at DESC LIMIT 10", FM.KEY_ENTRY_PAYOUT):
            prov = _j(r["training_provenance"]) or {}
            ev = _j(r["evaluation"]) or {}
            el = ev.get("approval_eligibility") or {}
            models.append({
                "model_id": r["model_id"], "state": r["state"],
                "model_version": r["model_version"],
                "approved_by": r["approved_by"],
                "approved_at": _iso(r["approved_at"]),
                "record_source": prov.get("source"),
                "training_population": prov.get("training_population"),
                "eligible": el.get("eligible"),
                "failed": el.get("failed"),
                "input_distribution_shift": el.get(
                    "input_distribution_shift")})
    data["models"] = models
    if not (data["research_observations"].get("by_cohort") or {}):
        return _sec(EMPTY, data, why=(
            "no research observation has been recorded: after_cycle has not "
            "run against this database since migration 170"))
    return _sec(OK, data)


SECTIONS = (("status", status), ("versions", versions),
            ("coverage", coverage), ("subscription", subscription),
            ("opportunity_queue", opportunity_queue),
            ("decisions", decisions), ("plans_fills", plans_fills),
            ("handoffs", handoffs), ("latency", latency),
            ("performance", performance), ("collection", collection),
            ("model_qualification", model_qualification))


async def workspace(conn) -> dict:
    from ..agents import derek_policy as DP
    out: dict[str, Any] = {
        "agent": {"agent_id": DP.AGENT_ID, "display_name": "Derek",
                  "mandate": ("discovery and initial entry on Polymarket US: "
                              "full-game moneylines in the entry lane's "
                              "configured sports, under %s"
                              % DP.POLICY_VERSION),
                  "sends_orders": False},
        "read_at": time.time(), "read_only": True, "sections": {}}
    for name, fn in SECTIONS:
        try:
            out["sections"][name] = await fn(conn)
        except Exception as exc:                               # noqa: BLE001
            out["sections"][name] = _sec(
                UNAVAILABLE, why="the read failed: %s" % type(exc).__name__)
    st = out["sections"].get("status") or {}
    if st.get("status") == OK and isinstance(st.get("data"), dict):
        out["agent"]["status"] = st["data"]
    return out


async def _pool():
    try:
        return await get_pool()
    except Exception as exc:                                   # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "NO_DATABASE_POOL", "detail": type(exc).__name__})


@router.get(BASE, dependencies=[Depends(require_read)])
async def derek_workspace(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        return await workspace(conn)


@router.get(BASE + "/decisions/{decision_id}",
            dependencies=[Depends(require_read)])
async def derek_decision(decision_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await _regclass(conn, "derek_entry_decisions"):
            raise HTTPException(status_code=404, detail={
                "reason": "DEREK_TABLES_ABSENT"})
        r = await conn.fetchrow(
            "SELECT * FROM derek_entry_decisions WHERE decision_id = $1",
            decision_id)
        if r is None:
            raise HTTPException(status_code=404, detail={
                "reason": "NO_SUCH_DECISION", "decision_id": decision_id})
        d = _decision_row(r)
        valuation = None
        if d.get("valuation_id") is not None and await _regclass(
                conn, "external_valuations"):
            v = await conn.fetchrow(
                "SELECT id, experiment_id, version, us_market_slug, "
                "       condition_id, event_key, payout_event, buy_intent, "
                "       probability, executable_price, cost_per_contract, "
                "       estimated_edge_per_contract, admissible, refusals, "
                "       decision, proposed_size, record_purpose, decided_at, "
                "       observed_at, outcome_known, outcome, outcome_at "
                "  FROM external_valuations WHERE id = $1",
                int(d["valuation_id"]))
            if v is not None:
                valuation = {k: (_iso(x) if hasattr(x, "isoformat") else x)
                             for k, x in dict(v).items()}
        link = None
        try:
            if await _regclass(conn, "agent_decisions"):
                lr = await conn.fetchrow(
                    "SELECT * FROM agent_decisions WHERE decision_ref = $1",
                    decision_id)
                link = None if lr is None else {
                    k: (_iso(x) if hasattr(x, "isoformat") else _j(x))
                    for k, x in dict(lr).items()}
        except Exception:                                      # noqa: BLE001
            link = None
        return {"decision": d, "valuation": valuation,
                "authoritative_record": ("external_valuations row %s"
                                         % d.get("valuation_id")),
                "registry_link": link,
                "evidence": d["evidence_links"], "read_only": True}
