"""THE TRADING FLOOR: ONE READ-ONLY AGGREGATE OF WHAT EVERY AGENT IS DOING.

    GET /api/command/floor            the seven desks + collaboration edges
    GET /api/command/floor/{agent}    one agent's workspace detail

Read-only, COMMAND session auth (agents_core.require_read -> 401 without a
session), GET only. Every read runs inside ONE `BEGIN READ ONLY` transaction
with a bounded `statement_timeout`, each section in its own savepoint, so a
missing table (a migration not yet deployed) or a failed read is reported as
ABSENT / UNAVAILABLE with its reason and never aborts the other sections.

WHAT A STATE MEANS. Every state is DERIVED from real rows with their
timestamps -- nothing here is invented for animation:

  NOT_DEPLOYED  the agent's tables / identity do not exist on this database
                (Archer and Scout until migration 217 ships)
  STALE         no heartbeat, or the latest heartbeat is older than the
                agent's stale bound (3 x its recorded cadence, floor 15 min)
  WORKING_ON    a run is in progress inside the heartbeat window
                (agent_status: EVALUATING with a run started after the last
                finish), a Slack request is being worked, or the agent
                recorded an output inside ACTIVE_WINDOW_S
  REVIEWING     the same evidence for a reviewing role (Xavier reviewing a
                position, Audrey evaluating/auditing)
  CHALLENGING   Karen with a run in progress or a challenge raised inside
                ACTIVE_WINDOW_S
  WAITING       agent_status says WAITING_FOR_EVIDENCE / WAITING_FOR_PROVIDER
                / BLOCKED / FAILED / RECOVERING (the activity says on what)
  IDLE          heartbeat fresh, nothing in progress or recent

THE WORK STATE (owner R30, `work_state` / `work_detail` / `work_basis` /
`work_since` / `work_counts` beside the desk state above, on both routes):
one of WORKING, REVIEWING, WAITING_FOR_FRESH_EVIDENCE, BLOCKED_ON_MARKET_DATA,
HANDOFF_PENDING, IDLE_NO_OPEN_WORK, derived by `agent_work_state` from the
agent's recorded open work, current reviews, runs in progress, open fresh-
evidence requests, market-data reads and hand-offs not yet picked up (null,
with its reason, only when those facts cannot be read). Xavier is never
IDLE_NO_OPEN_WORK while he owns an open position. The desk `state` keeps its
seven-state vocabulary for the existing page.

COLLABORATION EDGES are real rows inside the window (default one hour):
Karen's challenges (raised / answered / resolved), collaboration-loop stages
recorded by one agent on another's finding (migration 203), Derek -> Xavier
paper hand-offs, the candidate-review workflow's consecutive steps and
Archer's estimates of Derek's decisions (migration 217, read only when the
tables exist) and the agents' durable hand-off / memory hand-off messages
(agent_conversation_messages, migration 224). Each edge carries its
evidence ids.

WHAT THIS MODULE CANNOT DO, BY CONSTRUCTION. It imports no order, venue,
execution, ledger or funded module (tests/test_command_floor_authority.py
walks its imports), issues only SELECTs inside a READ ONLY transaction, and
holds no write, approval, activation or capital path. Paper and actual
counts are separate keys, never summed.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response

from ..agent_work_state import WORK_STATES as _WORK_STATES
from ..agent_work_state import read_work_states as _read_work_states
from ..xavier_freshness import of_assessment as _xf_of_assessment
from ..xavier_freshness import of_review as _xf_of_review
from .agents_core import _pool, require_read

router = APIRouter()


def _labels(blk: dict) -> dict:
    """XAVIER'S RECOMMENDATION AS THE FLOOR MAY SHOW IT (owner P0): the
    action only while CURRENT; otherwise the state, with the stored word
    named as recorded (xavier_freshness.validity at read time)."""
    st, rec = blk["recommendation_state"], blk["recorded_recommendation"]
    if blk["is_current"]:
        label = rec
    else:
        label = st + (" (recorded %s, not current)" % rec
                      if rec and rec != st else "")
    v = blk.get("valuation") or {}
    return {"recommendation": blk["current_recommendation"] or st,
            "recommendation_state": st,
            "management_state": blk.get("management_state"),
            "superseded_by": blk.get("superseded_by"),
            "recorded_recommendation": rec,
            "recommendation_label": label,
            "valuation_id": v.get("valuation_id"),
            "valuation_timestamp": v.get("source_at"),
            "age_seconds": v.get("age_now_s"),
            "freshness_limit": v.get("limit_s"),
            "valuation_expires_at": v.get("expires_at")}


def _gate_assessment(row: dict, now: float, *,
                     superseded_by: str | None = None) -> dict:
    v = row.get("valuation")
    if isinstance(v, str):
        try:
            row["valuation"] = json.loads(v)
        except ValueError:
            row["valuation"] = None
    blk = _xf_of_assessment(
        dict(row, assessed_at=_ep(row.get("assessed_at"))), now=now,
        newer_assessment_id=superseded_by)
    return dict(row, **_labels(blk))

VERSION = "COMMAND_FLOOR_V1"
STATEMENT_TIMEOUT_MS = 5000
WINDOW_S = 3600.0                 # collaboration edges: the last hour
ACTIVE_WINDOW_S = 300.0           # a recorded output this recent = working
STALE_FLOOR_S = 900.0             # never call an agent stale sooner
STALE_FACTOR = 3.0                # x the agent's recorded cadence
RUN_WINDOW_FLOOR_S = 600.0        # a run older than this is not "in progress"
DETAIL_WINDOW_S = 86400.0         # a workspace's timeline: the last day
ALLOCATOR_CYCLE_S = 600.0         # intel/runner.CYCLE_S (read, not imported)

STATES = ("WORKING_ON", "REVIEWING", "CHALLENGING", "WAITING", "IDLE",
          "STALE", "NOT_DEPLOYED")
WAITING_STATUSES = ("WAITING_FOR_EVIDENCE", "WAITING_FOR_PROVIDER", "BLOCKED",
                    "FAILED", "RECOVERING")
BUSY_STATE = {"KAREN": "CHALLENGING", "AUDREY": "REVIEWING",
              "XAVIER": "REVIEWING"}

# ── THE EIGHT DESKS: THE CANDIDATE-REVIEW ORDER, THEN ADRIANA ───────────
# Derek -> Karen -> Scout -> Archer -> Allocator -> Audrey -> Xavier is the
# order of pos_candidate_review_steps (migration 217); the floor seats them
# along the arc in that order so the review flows across the room.
SEATS = (
    {"agent": "DEREK", "slug": "derek", "display_name": "Derek",
     "title": "Chief Investment Officer · alpha & entry decisions",
     "authority_level": "ENTRY_REQUEST_THROUGH_GATED_PATH",
     "workspace": "/derek", "kind": "REGISTRY",
     "may": ["Read the catalogue, prices and valuations",
             "Record each entry verdict with its evidence",
             "Request an entry only through the one gated entry path "
             "(owner entry policy + every existing rail must agree)"],
     "may_not": ["Manage or exit a position after a fill (Xavier owns it)",
                 "Write audits, directives or policy candidates",
                 "Change limits, thresholds, approvals or submission switches",
                 "Submit or cancel an order outside the gated path"]},
    {"agent": "KAREN", "slug": "karen", "display_name": "Karen",
     "title": "Red team · evidence challenges",
     "authority_level": "NONE_ZERO_AUTHORITY",
     "workspace": "/karen", "kind": "REGISTRY",
     "may": ["Read decisions, intents, reviews, reconciliations, audits",
             "Raise a challenge that cites at least one existing record",
             "Record the PEER_CHALLENGE stage of another agent's finding"],
     "may_not": ["Place, cancel or request any order",
                 "Resolve her own challenge",
                 "Approve, activate or promote anything",
                 "Touch capital, risk limits or credentials"]},
    {"agent": "SCOUT", "slug": "scout", "display_name": "Scout",
     "title": "Market intelligence · research shadow",
     "authority_level": "RESEARCH_SHADOW_ONLY",
     "workspace": "/scout", "kind": "POS_AGENT",
     "deploy_table": "scout_features",
     "may": ["Register compliant, licensed sources and features",
             "Freeze a feature tournament spec against the PinnAPI baseline"],
     "may_not": ["Adopt or promote a feature into a model",
                 "Judge his own tournament (the evaluator decides)",
                 "Any order, capital or venue action"]},
    {"agent": "ARCHER", "slug": "archer", "display_name": "Archer",
     "title": "Head of Execution · shadow only",
     "authority_level": "SHADOW_ONLY",
     "workspace": "/archer", "kind": "POS_AGENT",
     "deploy_table": "eddie_execution_estimates",
     "may": ["Estimate executable edge, fill probability and slippage for "
             "Derek's decisions (SHADOW)",
             "Measure realized execution against the estimate"],
     "may_not": ["Place, cancel or route any order",
                 "Change a size, limit or threshold",
                 "Allocate or reserve capital"]},
    {"agent": "CHIEF_ALLOCATOR", "slug": "allocator",
     "display_name": "Chief Allocator",
     "title": "Capital allocation · shadow sleeve",
     "authority_level": "SHADOW_WEIGHTS_ONLY",
     "workspace": "/allocator", "kind": "INTEL",
     "deploy_table": "intel_runs",
     "may": ["Rank qualified candidates and open positions for the "
             "$1,000.00 notional SHADOW sleeve",
             "Record shadow weights, binding constraints and opportunity "
             "cost per dollar"],
     "may_not": ["Size, place or cancel any order (no order reads its weights)",
                 "Change caps, limits or thresholds",
                 "Commit or reserve real capital"]},
    {"agent": "AUDREY", "slug": "audrey", "display_name": "Audrey",
     "title": "Risk & audit · management reporting",
     "authority_level": "AUDIT_NO_ORDER_PATH",
     "workspace": "/audrey", "kind": "REGISTRY",
     "may": ["Read every record (read only)",
             "Audit Derek and Xavier against the authoritative records",
             "Evaluate challenges independently; record directives and tasks",
             "Propose policy CANDIDATES for owner approval"],
     "may_not": ["Hold any order tool",
                 "Approve or activate a policy, limit or model",
                 "Deploy code or change submission switches"]},
    {"agent": "XAVIER", "slug": "xavier", "display_name": "Xavier",
     "title": "Portfolio manager · position management",
     "authority_level": "MANAGEMENT_DISPATCH_THROUGH_CLAIM_PATH",
     "workspace": "/xavier", "kind": "REGISTRY",
     "may": ["Manage every position with confirmed filled quantity",
             "Persist each management decision before any action",
             "Dispatch only through the existing claim path"],
     "may_not": ["Open a new entry",
                 "Write entry decisions, audits or directives",
                 "Change limits, approvals or submission switches"]},
    # (265) the eighth desk: not a step of the candidate review, so it sits
    # after it
    {"agent": "ADRIANA", "slug": "adriana", "display_name": "Adriana",
     "title": "Head of Arbitrage · shadow only",
     "authority_level": "SHADOW_ONLY",
     "workspace": "/adriana", "kind": "POS_AGENT",
     "deploy_table": "adriana_arb_scans",
     "may": ["Read recorded venue books, the catalogue and settlement terms",
             "Record arbitrage opportunities and refusals with their "
             "evidence (SHADOW)",
             "Hand an opportunity to Archer for an execution review and ask "
             "Karen to challenge it"],
     "may_not": ["Place, cancel or route any order on any venue",
                 "Hold or read any venue credential",
                 "Allocate, reserve or approve capital",
                 "Change a size, limit, threshold, fee or freshness rule"]},
)
SEAT_BY_AGENT = {s["agent"]: s for s in SEATS}
SEAT_BY_SLUG = {s["slug"]: s for s in SEATS}
#: (266) a historical seat address -> the seat it names now; pinned equal to
#: registry.HISTORICAL_ALIASES by a test (this module imports no registry)
SEAT_ALIASES = {"eddie": "archer"}


# ═════════════════════════════════════════════════════════════════════
# PURE HELPERS
# ═════════════════════════════════════════════════════════════════════

def _ep(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _f(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def stale_after_s(cadence) -> float:
    """3 x the agent's recorded cadence (any *_interval_s key), never less
    than STALE_FLOOR_S. Pure."""
    c = _j(cadence) if cadence is not None else None
    vals = []
    if isinstance(c, dict):
        for k, v in c.items():
            if str(k).endswith("_s") and _f(v) and _f(v) > 0:
                vals.append(_f(v))
    return max(STALE_FLOOR_S, STALE_FACTOR * max(vals)) if vals else \
        STALE_FLOOR_S


def run_in_progress(status: dict | None, now: float,
                    window_s: float) -> bool:
    """A run started after the last finish, recently, while EVALUATING."""
    s = status or {}
    started = _ep(s.get("last_run_started_at"))
    finished = _ep(s.get("last_run_finished_at"))
    if started is None or s.get("state") != "EVALUATING":
        return False
    if finished is not None and finished >= started:
        return False
    return 0 <= now - started <= max(window_s, RUN_WINDOW_FLOOR_S)


def derive_state(agent: str, *, now: float, deployed: bool,
                 deploy_why: str | None, heartbeat_at, stale_s: float,
                 status: dict | None, signals: list) -> dict:
    """THE ONE STATE OF ONE DESK, from real rows only. Pure.

    `signals`: recent real activities, each {"at", "hint" (WORKING_ON /
    REVIEWING / CHALLENGING), "label", "ref", "basis"}. Returns {"state",
    "detail", "since", "basis", "activity_basis", "heartbeat_age_s"}."""
    hb = _ep(heartbeat_at)
    age = None if hb is None else round(max(0.0, now - hb), 1)
    out = {"heartbeat_age_s": age, "stale_after_s": stale_s,
           "activity_basis": None}
    if not deployed:
        return dict(out, state="NOT_DEPLOYED",
                    detail="Not yet deployed (%s)" % (
                        deploy_why or "NO_TABLES"),
                    since=None, basis=[{"kind": "deployment",
                                        "why": deploy_why}])
    if hb is None:
        return dict(out, state="STALE", detail="No heartbeat recorded",
                    since=None, basis=[{"kind": "heartbeat",
                                        "why": "NO_HEARTBEAT_RECORDED"}])
    if age > stale_s:
        return dict(out, state="STALE",
                    detail="Last heartbeat %ds ago (stale after %ds)"
                    % (int(age), int(stale_s)), since=hb,
                    basis=[{"kind": "heartbeat", "at": hb}])
    st = status or {}
    if run_in_progress(st, now, stale_s):
        return dict(out, state=BUSY_STATE.get(agent, "WORKING_ON"),
                    detail=str(st.get("activity") or "Run in progress"),
                    since=_ep(st.get("last_run_started_at")),
                    activity_basis="RUN_IN_PROGRESS",
                    basis=[{"kind": "agent_status", "id": agent,
                            "at": _ep(st.get("last_run_started_at"))}])
    recent = sorted([s for s in signals or []
                     if _ep(s.get("at")) is not None
                     and 0 <= now - _ep(s["at"]) <= ACTIVE_WINDOW_S],
                    key=lambda s: -_ep(s["at"]))
    if recent:
        s = recent[0]
        hint = s.get("hint") if s.get("hint") in STATES else "WORKING_ON"
        return dict(out, state=hint, detail=s.get("label") or hint,
                    since=_ep(s["at"]),
                    activity_basis=s.get("basis") or "RECENT_OUTPUT",
                    basis=[s.get("ref") or {"kind": "signal"}])
    if st.get("state") in WAITING_STATUSES:
        why = st.get("activity") or st.get("state")
        if st.get("state") == "FAILED" and st.get("last_error"):
            why = "%s (%s)" % (why, str(st.get("last_error"))[:160])
        return dict(out, state="WAITING", detail=str(why), since=hb,
                    basis=[{"kind": "agent_status", "id": agent,
                            "state": st.get("state"), "at": hb}])
    return dict(out, state="IDLE",
                detail=str(st.get("activity") or "Heartbeat fresh; "
                           "nothing in progress"), since=hb,
                basis=[{"kind": "heartbeat", "at": hb}])


def merge_edges(raw: list, *, max_evidence: int = 5) -> list:
    """Collapse raw edge rows to one per (from, to, kind): count, latest
    instant, up to `max_evidence` evidence refs. Pure; newest first."""
    acc: dict = {}
    for e in raw:
        if not e.get("from") or not e.get("to") or e["from"] == e["to"]:
            continue
        k = (e["from"], e["to"], e["kind"])
        a = acc.setdefault(k, {"from": e["from"], "to": e["to"],
                               "kind": e["kind"], "count": 0, "at": None,
                               "first_at": None, "evidence": [],
                               "summary": None})
        a["count"] += 1
        at = _ep(e.get("at"))
        if at is not None and (a["at"] is None or at > a["at"]):
            a["at"] = at
            a["summary"] = e.get("summary")
        if at is not None and (a["first_at"] is None or at < a["first_at"]):
            a["first_at"] = at
        if e.get("evidence") and len(a["evidence"]) < max_evidence:
            a["evidence"].append(e["evidence"])
    return sorted(acc.values(), key=lambda a: -(a["at"] or 0))


def _seat_agent(v) -> str | None:
    """A recorded actor name -> a seat id, or None (a person, a system)."""
    s = str(v or "").strip().upper()
    if s in SEAT_BY_AGENT:
        return s
    if s in ("ALLOCATOR", "CHIEF ALLOCATOR"):
        return "CHIEF_ALLOCATOR"
    return None


# ═════════════════════════════════════════════════════════════════════
# GUARDED READS
# ═════════════════════════════════════════════════════════════════════

class _Reads:
    """Sections run each in a savepoint of the one READ ONLY transaction."""

    def __init__(self, conn):
        self.conn = conn
        self.sections: dict = {}
        self._exists: dict = {}

    async def exists(self, table: str) -> bool:
        if table not in self._exists:
            try:
                self._exists[table] = bool(await self.conn.fetchval(
                    "SELECT to_regclass($1) IS NOT NULL", table))
            except Exception:                                   # noqa: BLE001
                self._exists[table] = False
        return self._exists[table]

    async def run(self, name: str, tables: tuple, fn, default=None):
        for t in tables:
            if not await self.exists(t):
                self.sections[name] = {"status": "ABSENT",
                                       "why": "TABLE_NOT_DEPLOYED:%s" % t}
                return default
        try:
            async with self.conn.transaction():
                got = await fn(self.conn)
        except Exception as exc:                                # noqa: BLE001
            self.sections[name] = {"status": "UNAVAILABLE",
                                   "why": type(exc).__name__}
            return default
        self.sections[name] = {"status": "OK" if got else "EMPTY",
                               "why": None}
        return got


def _ref(kind, rid, href=None, at=None) -> dict:
    out = {"kind": kind, "id": None if rid is None else str(rid)}
    if href:
        out["href"] = href
    if at is not None:
        out["at"] = _ep(at)
    return out


async def _status_rows(rd: _Reads) -> dict:
    async def fn(conn):
        rows = await conn.fetch(
            "SELECT i.agent_id, i.display_name, i.mandate, i.policy_version,"
            "       i.model_version, i.tool_permissions, s.state, s.activity,"
            "       s.waiting_on, s.last_heartbeat_at, s.last_run_started_at,"
            "       s.last_run_finished_at, s.last_run_elapsed_s, s.runs, "
            "       s.errors, s.last_error, s.cadence "
            "  FROM agent_identities i LEFT JOIN agent_status s "
            " USING (agent_id)")
        return {r["agent_id"]: {k: (_ep(v) if isinstance(v, datetime) else
                                    _j(v) if k in ("tool_permissions",
                                                   "waiting_on", "cadence")
                                    else (float(v) if hasattr(v, "as_tuple")
                                          else v))
                                for k, v in dict(r).items()}
                for r in rows}
    return await rd.run("agent_status", ("agent_identities", "agent_status"),
                        fn, default={}) or {}


async def _derek(rd: _Reads, now: float) -> dict:
    async def fn(conn):
        agg = await conn.fetchrow(
            "SELECT count(*) AS n, count(*) FILTER (WHERE verdict='ENTER') "
            "       AS enter, max(decided_at) AS last "
            "  FROM paper_decisions WHERE decided_at >= to_timestamp($1)",
            now - DETAIL_WINDOW_S)
        last = await conn.fetchrow(
            "SELECT decision_id, decided_at, verdict, refusal, "
            "       us_market_slug, holding_side, fixture "
            "  FROM paper_decisions ORDER BY decided_at DESC LIMIT 1")
        return {"n24": int(agg["n"]), "enter24": int(agg["enter"]),
                "last": None if last is None else dict(last)}
    return await rd.run("paper_decisions", ("paper_decisions",), fn) or {}


async def _xavier(rd: _Reads, now: float) -> dict:
    async def paper(conn):
        r = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE open_qty > 1e-9 AND NOT settled) "
            "       AS open, count(*) AS handed "
            "  FROM (SELECT h.group_id, "
            "         coalesce((SELECT sum(qty) FILTER (WHERE direction='BUY')"
            "                   - coalesce(sum(qty) FILTER ("
            "                       WHERE direction='SELL'), 0) "
            "                     FROM paper_fills f "
            "                    WHERE f.group_id = h.group_id), 0) AS open_qty,"
            "         EXISTS (SELECT 1 FROM paper_settlements s "
            "                  WHERE s.group_id = h.group_id) AS settled "
            "    FROM paper_handoffs h) q")
        return {"open": int(r["open"]), "handed": int(r["handed"])}

    async def actual(conn):
        # VENUE BY VENUE, never one count across Polymarket US and Kalshi;
        # each venue's connection from ITS OWN control row only.
        by = {r["venue"]: int(r["n"]) for r in await conn.fetch(
            "SELECT venue, count(*) AS n FROM smalllive_handoffs "
            " WHERE state='OPEN' GROUP BY venue")}
        conn_of = {}
        for venue, table, col in (
                ("POLYMARKET", "execmirror_control", "account_fingerprint"),
                ("KALSHI", "kalshi_smalllive_control", "key_fingerprint")):
            if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                   table):
                conn_of[venue] = bool(await conn.fetchval(
                    "SELECT %s IS NOT NULL FROM %s WHERE id = 1"
                    % (col, table)))
            else:
                conn_of[venue] = False
        return {"by_venue": {v: by.get(v, 0) for v in ("POLYMARKET",
                                                        "KALSHI")},
                "connected": conn_of,
                "unknown_venue_open": sum(n for v, n in by.items()
                                          if v not in ("POLYMARKET",
                                                       "KALSHI"))}

    async def assessments(conn):
        agg = await conn.fetchrow(
            "SELECT count(*) AS n, count(DISTINCT group_id) AS groups "
            "  FROM xavier_management_assessments "
            " WHERE assessed_at >= to_timestamp($1)", now - DETAIL_WINDOW_S)
        last = await conn.fetchrow(
            "SELECT assessment_id, position_kind, group_id, assessed_at, "
            "       trigger, evidence_state, thesis_state, recommendation, "
            "       probability, probability_source, probability_age_s, "
            "       to_jsonb(x) -> 'valuation' AS valuation "
            "  FROM xavier_management_assessments x "
            " ORDER BY assessed_at DESC LIMIT 1")
        return {"n24": int(agg["n"]), "groups24": int(agg["groups"]),
                "last": None if last is None else _gate_assessment(
                    dict(last), now)}

    async def reviews(conn):
        last = await conn.fetchrow(
            "SELECT review_id, group_id, reviewed_at, trigger, recommendation,"
            "       refusal, measure, selection FROM paper_xavier_reviews "
            " ORDER BY reviewed_at DESC LIMIT 1")
        if last is None:
            return None
        d = dict(last)
        blk = _xf_of_review(dict(d, reviewed_at=_ep(d.get("reviewed_at"))),
                           now=now)
        d.pop("measure", None)
        d.pop("selection", None)
        return dict(d, **_labels(blk))

    return {
        "paper": await rd.run("xavier_paper_positions",
                              ("paper_handoffs", "paper_fills",
                               "paper_settlements"), paper),
        "actual": await rd.run("xavier_actual_positions",
                               ("smalllive_handoffs",), actual),
        "assessments": await rd.run("xavier_management_assessments",
                                    ("xavier_management_assessments",),
                                    assessments) or {},
        "review": await rd.run("paper_xavier_reviews",
                               ("paper_xavier_reviews",), reviews)}


async def _audrey(rd: _Reads, now: float) -> dict:
    async def findings(conn):
        agg = await conn.fetchrow(
            "SELECT count(*) AS n, "
            "       count(*) FILTER (WHERE severity='CRITICAL') AS critical, "
            "       count(*) FILTER (WHERE severity='WARNING') AS warning "
            "  FROM paper_audrey_findings WHERE found_at >= to_timestamp($1)",
            now - DETAIL_WINDOW_S)
        last = await conn.fetchrow(
            "SELECT finding_id, found_at, kind, severity, subject "
            "  FROM paper_audrey_findings ORDER BY found_at DESC LIMIT 1")
        return {"n24": int(agg["n"]), "critical24": int(agg["critical"]),
                "warning24": int(agg["warning"]),
                "last": None if last is None else dict(last)}

    async def alerts(conn):
        agg = await conn.fetchrow(
            "SELECT count(*) AS n, max(detected_at) AS last "
            "  FROM coverage_collapse_alerts "
            " WHERE detected_at >= to_timestamp($1)", now - DETAIL_WINDOW_S)
        return {"n24": int(agg["n"]), "last_at": _ep(agg["last"])}

    async def reports(conn):
        last = await conn.fetchrow(
            "SELECT report_id, version, audit_day, computed_at "
            "  FROM audrey_audit_reports ORDER BY computed_at DESC LIMIT 1")
        return None if last is None else dict(last)

    return {
        "findings": await rd.run("paper_audrey_findings",
                                 ("paper_audrey_findings",), findings) or {},
        "alerts": await rd.run("coverage_collapse_alerts",
                               ("coverage_collapse_alerts",), alerts),
        "report": await rd.run("audrey_audit_reports",
                               ("audrey_audit_reports",), reports)}


async def _challenges(rd: _Reads, now: float) -> dict:
    async def fn(conn):
        rows = await conn.fetch(
            "SELECT target_agent, count(*) FILTER (WHERE state='OPEN') AS open,"
            "       count(*) FILTER (WHERE state='RESPONDED') AS responded, "
            "       count(*) FILTER (WHERE challenged_at >= to_timestamp($1)) "
            "       AS raised24 "
            "  FROM karen_challenges GROUP BY target_agent",
            now - DETAIL_WINDOW_S)
        last = await conn.fetchrow(
            "SELECT challenge_id, target_agent, target_kind, target_id, "
            "       severity, claim, state, challenged_at "
            "  FROM karen_challenges ORDER BY challenged_at DESC LIMIT 1")
        return {"by_target": {r["target_agent"]: {
                    "open": int(r["open"]), "responded": int(r["responded"]),
                    "raised24": int(r["raised24"])} for r in rows},
                "last": None if last is None else dict(last)}
    return await rd.run("karen_challenges", ("karen_challenges",), fn) or {}


async def _allocator(rd: _Reads, now: float) -> dict:
    async def fn(conn):
        last = await conn.fetchrow(
            "SELECT run_id, status, started_at, finished_at, error, "
            "       duration_ms, summary, version FROM intel_runs "
            " WHERE component='ALLOCATOR' ORDER BY started_at DESC LIMIT 1")
        cycle = await conn.fetchrow(
            "SELECT max(started_at) AS started, max(finished_at) AS finished "
            "  FROM intel_runs WHERE component='CYCLE'")
        out = {"last": None if last is None else dict(last),
               "cycle_started_at": _ep(cycle["started"]) if cycle else None,
               "cycle_finished_at": _ep(cycle["finished"]) if cycle else None}
        if last is not None and await rd.exists("intel_allocations"):
            f = await conn.fetchrow(
                "SELECT count(*) AS n, count(*) FILTER (WHERE shadow_usd > 0)"
                "       AS funded, coalesce(sum(shadow_usd), 0) AS usd "
                "  FROM intel_allocations WHERE run_id=$1", last["run_id"])
            out["allocations"] = {"ranked": int(f["n"]),
                                  "funded": int(f["funded"]),
                                  "shadow_usd": round(float(f["usd"]), 2)}
        return out if (last is not None or out["cycle_started_at"]) else None
    return await rd.run("intel_runs", ("intel_runs",), fn) or {}


async def _archer(rd: _Reads, now: float) -> dict:
    async def fn(conn):
        agg = await conn.fetchrow(
            "SELECT count(*) AS n FROM eddie_execution_estimates "
            " WHERE estimated_at >= to_timestamp($1)", now - DETAIL_WINDOW_S)
        last = await conn.fetchrow(
            "SELECT estimate_id, decision_id, estimated_at, recommendation, "
            "       expected_net_executable_edge_pp, us_market_slug "
            "  FROM eddie_execution_estimates "
            " ORDER BY estimated_at DESC LIMIT 1")
        return {"n24": int(agg["n"]),
                "last": None if last is None else dict(last)}
    return await rd.run("eddie_execution_estimates",
                        ("eddie_execution_estimates",), fn) or {}


async def _scout(rd: _Reads, now: float) -> dict:
    async def fn(conn):
        agg = await conn.fetchrow(
            "SELECT count(*) AS n, max(proposed_at) AS last_at "
            "  FROM scout_features")
        last = await conn.fetchrow(
            "SELECT feature_id, feature, state, proposed_at "
            "  FROM scout_features ORDER BY proposed_at DESC LIMIT 1")
        return {"features": int(agg["n"]),
                "last": None if last is None else dict(last)}
    return await rd.run("scout_features", ("scout_features",), fn) or {}


async def _adriana(rd: _Reads, now: float) -> dict:
    """Her latest census pass, a day of passes and the newest proven
    opportunity -- as recorded (265)."""
    async def fn(conn):
        agg = await conn.fetchrow(
            "SELECT count(*) AS n, coalesce(sum(opportunities), 0) AS opp, "
            "       coalesce(sum(refusals_total), 0) AS ref "
            "  FROM adriana_arb_scans WHERE finished_at >= to_timestamp($1)",
            now - DETAIL_WINDOW_S)
        last = await conn.fetchrow(
            "SELECT scan_id, started_at, finished_at, status, why, venues, "
            "       markets_read, books_fresh, structures_considered, "
            "       opportunities, refusals_total, refusals_recorded, "
            "       by_code, by_kind FROM adriana_arb_scans "
            " ORDER BY finished_at DESC LIMIT 1")
        opp = await conn.fetchrow(
            "SELECT opportunity_id, scan_id, structure_kind, event_key, "
            "       venues, max_qty, net_profit_usd, edge_per_set_usd, "
            "       decided_at FROM adriana_arb_opportunities "
            " ORDER BY decided_at DESC LIMIT 1")
        return {"scans24": int(agg["n"]), "opportunities24": int(agg["opp"]),
                "refusals24": int(agg["ref"]),
                "last": None if last is None else dict(last),
                "last_opportunity": None if opp is None else dict(opp)}
    return await rd.run("adriana_arb_scans",
                        ("adriana_arb_scans", "adriana_arb_opportunities"),
                        fn) or {}


async def _feed(rd: _Reads, now: float) -> list:
    """The opportunity feed: Derek's latest PAPER decisions, as recorded."""
    async def fn(conn):
        return [{"kind": "paper_decisions", "id": r["decision_id"],
                 "at": _ep(r["decided_at"]), "verdict": r["verdict"],
                 "refusal": r["refusal"], "market": r["us_market_slug"],
                 "side": r["holding_side"], "fixture": r["fixture"],
                 "p_blended": _f(r["p_blended"]),
                 "limit_price": _f(r["limit_price"]), "book": "PAPER"}
                for r in await conn.fetch(
                    "SELECT decision_id, decided_at, verdict, refusal, "
                    "       us_market_slug, holding_side, fixture, p_blended,"
                    "       limit_price FROM paper_decisions "
                    " ORDER BY decided_at DESC LIMIT 12")]
    return await rd.run("feed.paper_decisions", ("paper_decisions",), fn,
                        default=[]) or []


async def _opportunities(rd: _Reads, alloc: dict) -> list:
    """The Chief Allocator's latest SHADOW ranking (top 6), as recorded."""
    run = (alloc or {}).get("last") or {}
    if not run.get("run_id"):
        rd.sections["opportunities.intel_allocations"] = {
            "status": "EMPTY", "why": "NO_ALLOCATOR_RUN"}
        return []

    async def fn(conn):
        return [{"kind": "intel_allocations", "id": r["candidate_id"],
                 "run_id": run["run_id"], "at": _ep(r["computed_at"]),
                 "rank": r["rank"], "candidate_kind": r["candidate_kind"],
                 "market": r["us_market_slug"],
                 "decision_id": r["decision_id"], "group_id": r["group_id"],
                 "score": _f(r["score"]),
                 "net_ev_per_dollar": _f(r["net_ev_per_dollar"]),
                 "shadow_usd": _f(r["shadow_usd"]),
                 "binding_constraint": r["binding_constraint"],
                 "label": "SHADOW"}
                for r in await conn.fetch(
                    "SELECT candidate_id, computed_at, rank, candidate_kind, "
                    "       us_market_slug, decision_id, group_id, score, "
                    "       net_ev_per_dollar, shadow_usd, binding_constraint"
                    "  FROM intel_allocations WHERE run_id=$1 "
                    " ORDER BY rank LIMIT 6", run["run_id"])]
    return await rd.run("opportunities.intel_allocations",
                        ("intel_allocations",), fn, default=[]) or []


async def _slack(rd: _Reads, now: float) -> dict:
    """Live Slack questions being worked (state + time only, no text)."""
    async def fn(conn):
        rows = await conn.fetch(
            "SELECT delivery_id, agent, state, updated_at "
            "  FROM agent_slack_delivery "
            " WHERE state IN ('WORKING','SENDING') "
            "   AND updated_at >= to_timestamp($1)", now - ACTIVE_WINDOW_S)
        return {str(r["agent"]).upper(): dict(r) for r in rows}
    return await rd.run("agent_slack_delivery", ("agent_slack_delivery",),
                        fn) or {}


async def _edges_raw(rd: _Reads, since: float) -> list:
    raw: list = []

    async def karen(conn):
        out = []
        for r in await conn.fetch(
                "SELECT challenge_id, target_agent, severity, claim, state, "
                "       challenged_at, responded_at, responded_by, "
                "       response_stance, resolved_at, resolved_by, outcome "
                "  FROM karen_challenges "
                " WHERE challenged_at >= to_timestamp($1) "
                "    OR responded_at >= to_timestamp($1) "
                "    OR resolved_at >= to_timestamp($1)", since):
            ev = _ref("karen_challenges", r["challenge_id"],
                      "/api/command/karen/challenges/%s" % r["challenge_id"])
            if _ep(r["challenged_at"]) and _ep(r["challenged_at"]) >= since:
                out.append({"from": "KAREN", "to": r["target_agent"],
                            "kind": "CHALLENGE_RAISED",
                            "at": r["challenged_at"], "evidence": ev,
                            "summary": "%s challenge: %s" % (
                                r["severity"], str(r["claim"])[:140])})
            if _ep(r["responded_at"]) and _ep(r["responded_at"]) >= since:
                out.append({"from": _seat_agent(r["responded_by"]),
                            "to": "KAREN", "kind": "CHALLENGE_ANSWERED",
                            "at": r["responded_at"], "evidence": ev,
                            "summary": "%s her challenge" % (
                                str(r["response_stance"] or "").title()
                                or "Answered")})
            if _ep(r["resolved_at"]) and _ep(r["resolved_at"]) >= since:
                out.append({"from": _seat_agent(r["resolved_by"]),
                            "to": r["target_agent"],
                            "kind": "CHALLENGE_RESOLVED",
                            "at": r["resolved_at"], "evidence": ev,
                            "summary": "Resolved %s" % (r["outcome"]
                                                        or r["state"])})
        return out

    async def loop(conn):
        return [{"from": _seat_agent(r["actor"]),
                 "to": _seat_agent(r["proposer"]),
                 "kind": "LOOP_%s" % r["stage"], "at": r["at"],
                 "evidence": _ref("agent_finding_stages", r["stage_id"],
                                  None, r["at"]),
                 "summary": "%s on finding %s" % (
                     str(r["stage"]).replace("_", " ").lower(),
                     r["finding_id"])}
                for r in await conn.fetch(
                    "SELECT s.stage_id, s.finding_id, s.stage, s.actor, s.at,"
                    "       f.proposer FROM agent_finding_stages s "
                    "  JOIN agent_findings f USING (finding_id) "
                    " WHERE s.at >= to_timestamp($1) "
                    "   AND upper(s.actor) <> upper(f.proposer)", since)]

    async def handoffs(conn):
        return [{"from": "DEREK", "to": "XAVIER", "kind": "HANDOFF",
                 "at": r["created_at"],
                 "evidence": _ref("paper_handoffs", r["handoff_id"]),
                 "summary": "Handed group %s to Xavier" % r["group_id"]}
                for r in await conn.fetch(
                    "SELECT handoff_id, group_id, created_at "
                    "  FROM paper_handoffs "
                    " WHERE created_at >= to_timestamp($1)", since)]

    async def pos_steps(conn):
        rows = await conn.fetch(
            "SELECT review_id, seq, step, agent, status, at "
            "  FROM pos_candidate_review_steps "
            " WHERE review_id IN (SELECT review_id FROM "
            "        pos_candidate_review_steps WHERE at >= to_timestamp($1))"
            " ORDER BY review_id, seq", since)
        out, prev = [], None
        for r in rows:
            if (prev is not None and prev["review_id"] == r["review_id"]
                    and _ep(r["at"]) and _ep(r["at"]) >= since):
                out.append({"from": _seat_agent(prev["agent"]),
                            "to": _seat_agent(r["agent"]),
                            "kind": "CANDIDATE_REVIEW",
                            "at": r["at"],
                            "evidence": _ref("pos_candidate_review_steps",
                                             "%s#%s" % (r["review_id"],
                                                        r["seq"])),
                            "summary": "%s (%s)" % (
                                str(r["step"]).replace("_", " ").title(),
                                r["status"])})
            prev = r
        return out

    async def archer(conn):
        return [{"from": "DEREK", "to": "ARCHER", "kind": "EXECUTION_ESTIMATE",
                 "at": r["estimated_at"],
                 "evidence": _ref("eddie_execution_estimates",
                                  r["estimate_id"]),
                 "summary": "Estimated decision %s: %s" % (
                     r["decision_id"], r["recommendation"])}
                for r in await conn.fetch(
                    "SELECT estimate_id, decision_id, recommendation, "
                    "       estimated_at FROM eddie_execution_estimates "
                    " WHERE estimated_at >= to_timestamp($1)", since)]

    async def messages(conn):
        # (224) durable agent-to-agent hand-offs and memory hand-offs
        return [{"from": _seat_agent(r["from_agent"]),
                 "to": _seat_agent(r["to_agent"]),
                 "kind": r["message_kind"], "at": r["created_at"],
                 "evidence": _ref("agent_conversation_messages",
                                  r["message_id"], None, r["created_at"]),
                 "summary": str(r["summary"])[:160]}
                for r in await conn.fetch(
                    "SELECT message_id, from_agent, to_agent, message_kind, "
                    "       summary, created_at "
                    "  FROM agent_conversation_messages "
                    " WHERE created_at >= to_timestamp($1)", since)]

    for name, tables, fn in (
            ("edges.agent_conversation_messages",
             ("agent_conversation_messages",), messages),
            ("edges.karen_challenges", ("karen_challenges",), karen),
            ("edges.collaboration_loop", ("agent_finding_stages",
                                          "agent_findings"), loop),
            ("edges.paper_handoffs", ("paper_handoffs",), handoffs),
            ("edges.candidate_review", ("pos_candidate_review_steps",),
             pos_steps),
            ("edges.archer_estimates", ("eddie_execution_estimates",), archer)):
        raw.extend(await rd.run(name, tables, fn, default=[]) or [])
    return raw


# ═════════════════════════════════════════════════════════════════════
# THE AGGREGATE
# ═════════════════════════════════════════════════════════════════════

def _money(v) -> str | None:
    return None if v is None else "${:,.2f}".format(float(v))


def _venue_open(ac: dict | None, venue: str):
    """One ACTUAL venue's open managed positions, or None (with the reason
    at the call site) when that venue is not connected and holds none."""
    if not ac:
        return None
    n = (ac.get("by_venue") or {}).get(venue, 0)
    if not (ac.get("connected") or {}).get(venue) and not n:
        return None
    return n


def _m(label, value, source, as_of=None, why=None) -> dict:
    """A monitor metric: a real value, or None with the reason."""
    return {"label": label, "value": value, "source": source,
            "as_of": _ep(as_of),
            "why": why if value is None else None}


async def build_floor(conn, *, now: float | None = None,
                      window_s: float = WINDOW_S) -> dict:
    """Read every desk and the edges. Never raises on a missing section."""
    now = float(now if now is not None else time.time())
    rd = _Reads(conn)
    status = await _status_rows(rd)
    derek = await _derek(rd, now)
    xav = await _xavier(rd, now)
    aud = await _audrey(rd, now)
    ch = await _challenges(rd, now)
    alloc = await _allocator(rd, now)
    archer_deployed = (await rd.exists("eddie_execution_estimates")
                      and "ARCHER" in status)
    scout_deployed = await rd.exists("scout_features") and "SCOUT" in status
    adriana_deployed = (await rd.exists("adriana_arb_scans")
                        and "ADRIANA" in status)
    archer = await _archer(rd, now) if archer_deployed else {}
    scout = await _scout(rd, now) if scout_deployed else {}
    adriana = await _adriana(rd, now) if adriana_deployed else {}
    slack = await _slack(rd, now)
    feed = await _feed(rd, now)
    opportunities = await _opportunities(rd, alloc)
    edges = merge_edges(await _edges_raw(rd, now - window_s))
    by_target = ch.get("by_target") or {}
    # THE WORK STATE OF EVERY DESK (owner R30): recorded work and blockers
    work = await rd.run("work_states", (), lambda c: _read_work_states(
        c, now=now), default={}) or {}
    rd.sections.update(work.get("sections") or {})
    work_states = work.get("states") or {}

    agents = []
    for seat in SEATS:
        a = seat["agent"]
        st = status.get(a) or {}
        signals: list = []
        monitor: list = []
        focus = last_output = None
        deployed, deploy_why = True, None
        hb_at, stale_s = st.get("last_heartbeat_at"), stale_after_s(
            st.get("cadence"))
        if seat["kind"] == "POS_AGENT":
            ok_table = await rd.exists(seat["deploy_table"])
            deployed = bool(ok_table and a in status)
            deploy_why = (None if deployed else
                          ("MIGRATION_265_NOT_APPLIED" if a == "ADRIANA"
                           else "MIGRATION_217_NOT_APPLIED")
                          if not ok_table else "IDENTITY_NOT_REGISTERED")
        elif seat["kind"] == "INTEL":
            deployed = await rd.exists("intel_runs")
            deploy_why = None if deployed else "MIGRATION_208_NOT_APPLIED"
            lr = alloc.get("last") or {}
            hb_at = max([x for x in (alloc.get("cycle_finished_at"),
                                     alloc.get("cycle_started_at"),
                                     _ep(lr.get("started_at")),
                                     _ep(lr.get("finished_at"))) if x]
                        or [None]) if alloc else None
            stale_s = max(STALE_FLOOR_S, STALE_FACTOR * ALLOCATOR_CYCLE_S)
            st = {}
            if lr and lr.get("finished_at") is None and lr.get("started_at") \
                    and now - _ep(lr["started_at"]) <= RUN_WINDOW_FLOOR_S:
                st = {"state": "EVALUATING",
                      "last_run_started_at": lr["started_at"],
                      "activity": "Allocation run %s in progress"
                      % lr["run_id"]}
            elif lr and lr.get("status") in ("FAILED", "TIMEOUT"):
                st = {"state": "FAILED", "activity": "Last allocation run "
                      "%s" % lr["status"], "last_error": lr.get("error")}
        elif a not in status:
            deploy_why = "IDENTITY_NOT_REGISTERED"

        if a in slack:
            s = slack[a]
            signals.append({"at": s["updated_at"], "hint": "WORKING_ON",
                            "basis": "SLACK_REQUEST",
                            "label": "Answering a management question "
                                     "in Slack",
                            "ref": _ref("agent_slack_delivery",
                                        s["delivery_id"], None,
                                        s["updated_at"])})
        if a == "DEREK":
            last = derek.get("last")
            if last:
                lab = "%s %s%s" % (last["verdict"],
                                   last.get("us_market_slug") or "",
                                   (" · " + last["refusal"]) if
                                   last.get("refusal") else "")
                focus = last_output = dict(_ref(
                    "paper_decisions", last["decision_id"], None,
                    last["decided_at"]), summary=lab)
                signals.append({"at": last["decided_at"], "hint":
                                "WORKING_ON", "label": "Recorded decision: "
                                + lab, "ref": focus})
            monitor = [
                _m("Decisions (24h)", derek.get("n24"), "paper_decisions",
                   (last or {}).get("decided_at"),
                   rd.sections.get("paper_decisions", {}).get("why")),
                _m("ENTER verdicts (24h)", derek.get("enter24"),
                   "paper_decisions", (last or {}).get("decided_at"),
                   rd.sections.get("paper_decisions", {}).get("why"))]
        elif a == "XAVIER":
            asmt = (xav.get("assessments") or {}).get("last")
            rev = xav.get("review")
            cand = []
            if asmt:
                cand.append((asmt["assessed_at"], dict(_ref(
                    "xavier_management_assessments", asmt["assessment_id"],
                    None, asmt["assessed_at"]), group_id=asmt["group_id"],
                    position_kind=asmt["position_kind"],
                    summary="%s · %s · %s" % (
                        asmt.get("recommendation_label")
                        or "NO RECOMMENDATION",
                        asmt["position_kind"], asmt["group_id"]))))
            if rev:
                cand.append((rev["reviewed_at"], dict(_ref(
                    "paper_xavier_reviews", rev["review_id"], None,
                    rev["reviewed_at"]), group_id=rev["group_id"],
                    position_kind="PAPER",
                    summary="%s · PAPER · %s" % (
                        rev.get("recommendation_label")
                        or rev.get("refusal") or "REVIEWED",
                        rev["group_id"]))))
            if cand:
                at, last_output = max(cand, key=lambda c: _ep(c[0]) or 0)
                focus = last_output
                signals.append({"at": at, "hint": "REVIEWING",
                                "label": "Reviewed position: "
                                + last_output["summary"], "ref": focus})
            p, ac = xav.get("paper"), xav.get("actual")
            monitor = [
                _m("Managed positions · PAPER", (p or {}).get("open"),
                   "paper_handoffs+paper_fills", now,
                   rd.sections.get("xavier_paper_positions", {}).get("why")),
                # ACTUAL, venue by venue: never one figure across venues,
                # never one venue inferred from the other
                *[_m("Managed positions · ACTUAL · " + lbl,
                     _venue_open(ac, venue), "smalllive_handoffs (venue=%s)"
                     % venue, now,
                     rd.sections.get("xavier_actual_positions", {}).get("why")
                     or "%s — NOT_CONNECTED" % lbl)
                  for venue, lbl in (("POLYMARKET", "POLYMARKET US"),
                                     ("KALSHI", "KALSHI"))],
                _m("Latest recommendation",
                   (last_output or {}).get("summary"),
                   (last_output or {}).get("kind") or
                   "xavier_management_assessments",
                   (last_output or {}).get("at"), "NO_REVIEW_RECORDED")]
        elif a == "AUDREY":
            f = aud.get("findings") or {}
            lf = f.get("last")
            cand = []
            if lf:
                cand.append((lf["found_at"], dict(_ref(
                    "paper_audrey_findings", lf["finding_id"], None,
                    lf["found_at"]), summary="%s %s%s" % (
                        lf["severity"], lf["kind"],
                        (" · " + lf["subject"]) if lf.get("subject") else ""))))
            rep = aud.get("report")
            if rep:
                cand.append((rep["computed_at"], dict(_ref(
                    "audrey_audit_reports", rep["report_id"],
                    "/api/command/agents/audrey/reports/%s"
                    % rep["report_id"], rep["computed_at"]),
                    summary="Audit report %s v%s" % (rep["audit_day"],
                                                    rep["version"]))))
            for e in edges:
                if e["from"] == "AUDREY" and e["kind"] == "CHALLENGE_RESOLVED":
                    cand.append((e["at"], dict(e["evidence"][0] if
                                               e["evidence"] else {},
                                               at=e["at"],
                                               summary=e["summary"])))
            if cand:
                at, last_output = max(cand, key=lambda c: _ep(c[0]) or 0)
                focus = last_output
                signals.append({"at": at, "hint": "REVIEWING",
                                "label": "Audited: " + last_output["summary"],
                                "ref": focus})
            al = aud.get("alerts") or {}
            monitor = [
                _m("Findings (24h)", f.get("n24"), "paper_audrey_findings",
                   (lf or {}).get("found_at"),
                   rd.sections.get("paper_audrey_findings", {}).get("why")),
                _m("Critical findings (24h)", f.get("critical24"),
                   "paper_audrey_findings", (lf or {}).get("found_at"),
                   rd.sections.get("paper_audrey_findings", {}).get("why")),
                _m("Coverage alerts (24h)", al.get("n24"),
                   "coverage_collapse_alerts", al.get("last_at"),
                   rd.sections.get("coverage_collapse_alerts", {}).get("why"))]
        elif a == "KAREN":
            lc = ch.get("last")
            if lc:
                focus = last_output = dict(_ref(
                    "karen_challenges", lc["challenge_id"],
                    "/api/command/karen/challenges/%s" % lc["challenge_id"],
                    lc["challenged_at"]), target=lc["target_agent"],
                    summary="%s → %s: %s" % (lc["severity"],
                                             lc["target_agent"],
                                             str(lc["claim"])[:140]))
                signals.append({"at": lc["challenged_at"],
                                "hint": "CHALLENGING",
                                "label": "Challenged %s: %s" % (
                                    lc["target_agent"].title(),
                                    str(lc["claim"])[:120]), "ref": focus})
            sec_why = rd.sections.get("karen_challenges", {}).get("why")
            have = rd.sections.get("karen_challenges", {}).get("status") in (
                "OK", "EMPTY")
            monitor = [
                _m("Open challenges", sum(v["open"] + v["responded"] for v
                                          in by_target.values())
                   if have else None, "karen_challenges",
                   (lc or {}).get("challenged_at"), sec_why),
                _m("Raised (24h)", sum(v["raised24"] for v in
                                       by_target.values()) if have else None,
                   "karen_challenges", (lc or {}).get("challenged_at"),
                   sec_why)]
        elif a == "CHIEF_ALLOCATOR":
            lr = alloc.get("last")
            al = alloc.get("allocations") or {}
            if lr:
                summ = _j(lr.get("summary")) or {}
                last_output = focus = dict(_ref(
                    "intel_runs", lr["run_id"], "/api/command/intel/allocator",
                    lr.get("finished_at") or lr["started_at"]),
                    summary="Run %s · %s candidates · %s SHADOW allocated" % (
                        lr["status"], summ.get("candidates", "?"),
                        _money(summ.get("allocated_usd")) or "UNAVAILABLE"))
                if lr.get("status") == "OK":
                    signals.append({"at": lr.get("finished_at")
                                    or lr["started_at"], "hint": "WORKING_ON",
                                    "label": "Ranked the shadow sleeve: "
                                    + last_output["summary"], "ref": focus})
            monitor = [
                _m("Candidates ranked", al.get("ranked"), "intel_allocations",
                   (lr or {}).get("started_at"), "NO_ALLOCATOR_RUN"),
                _m("Funded (shadow)", al.get("funded"), "intel_allocations",
                   (lr or {}).get("started_at"), "NO_ALLOCATOR_RUN"),
                _m("Shadow sleeve allocated", _money(al.get("shadow_usd"))
                   if al else None, "intel_allocations",
                   (lr or {}).get("started_at"), "NO_ALLOCATOR_RUN")]
        elif a == "ARCHER" and deployed:
            le = archer.get("last")
            if le:
                focus = last_output = dict(_ref(
                    "eddie_execution_estimates", le["estimate_id"],
                    "/api/command/archer/estimates/%s" % le["estimate_id"],
                    le["estimated_at"]), decision_id=le["decision_id"],
                    summary="%s · decision %s" % (le["recommendation"],
                                                  le["decision_id"]))
                signals.append({"at": le["estimated_at"], "hint":
                                "WORKING_ON", "label": "Estimated execution: "
                                + last_output["summary"], "ref": focus})
            monitor = [_m("Estimates (24h)", archer.get("n24"),
                          "eddie_execution_estimates",
                          (le or {}).get("estimated_at"),
                          rd.sections.get("eddie_execution_estimates",
                                          {}).get("why"))]
        elif a == "SCOUT" and deployed:
            ls = scout.get("last")
            if ls:
                focus = last_output = dict(_ref(
                    "scout_features", ls["feature_id"], None,
                    ls["proposed_at"]), summary="%s (%s)" % (ls["feature"],
                                                           ls["state"]))
                signals.append({"at": ls["proposed_at"], "hint": "WORKING_ON",
                                "label": "Registered feature: "
                                + last_output["summary"], "ref": focus})
            monitor = [_m("Features registered", scout.get("features"),
                          "scout_features", (ls or {}).get("proposed_at"),
                          rd.sections.get("scout_features", {}).get("why"))]
        elif a == "ADRIANA" and deployed:
            sc, lo = adriana.get("last"), adriana.get("last_opportunity")
            sec_why = rd.sections.get("adriana_arb_scans", {}).get("why")
            if lo:
                focus = last_output = dict(_ref(
                    "adriana_arb_opportunities", lo["opportunity_id"],
                    "/api/command/adriana/opportunities/%s"
                    % lo["opportunity_id"], lo["decided_at"]),
                    summary="%s · %s · %s · %d sets · $%.2f worst-case "
                            "net (SHADOW)" % (
                                lo["structure_kind"], lo["event_key"],
                                "+".join(lo["venues"] or []),
                                lo["max_qty"],
                                float(lo["net_profit_usd"])))
            if sc:
                summ = ("Census %s · %d structures · %d proven · %d refused"
                        % (sc["status"], sc["structures_considered"],
                           sc["opportunities"], sc["refusals_total"]))
                scan_ref = dict(_ref("adriana_arb_scans", sc["scan_id"],
                                     "/api/command/adriana",
                                     sc["finished_at"]), summary=summ)
                if last_output is None or (_ep(sc["finished_at"]) or 0) > \
                        (_ep(last_output.get("at")) or 0):
                    focus = last_output = scan_ref
                signals.append({"at": sc["finished_at"], "hint":
                                "WORKING_ON", "label": summ, "ref": scan_ref})
            monitor = [
                _m("Census passes (24h)", adriana.get("scans24"),
                   "adriana_arb_scans", (sc or {}).get("finished_at"),
                   sec_why),
                _m("Proven after costs (24h)",
                   adriana.get("opportunities24"),
                   "adriana_arb_opportunities", (sc or {}).get("finished_at"),
                   sec_why),
                _m("Refused (last pass)", (sc or {}).get("refusals_total"),
                   "adriana_arb_scans", (sc or {}).get("finished_at"),
                   sec_why or "NO_CENSUS_PASS"),
                _m("Fresh books (last pass)", (sc or {}).get("books_fresh"),
                   "adriana_arb_scans", (sc or {}).get("finished_at"),
                   sec_why or "NO_CENSUS_PASS")]

        # A challenge raised against this agent recently is a REVIEWING
        # signal only once it answers (CHALLENGE_ANSWERED edge from it).
        for e in edges:
            actor = e["to"] if e["kind"] == "CANDIDATE_REVIEW" else e["from"]
            if actor == a and (e["kind"] in ("CHALLENGE_ANSWERED",
                                             "CANDIDATE_REVIEW")
                               or e["kind"].startswith("LOOP_")):
                signals.append({"at": e["at"], "hint": "CHALLENGING" if
                                a == "KAREN" else "REVIEWING",
                                "label": e["summary"],
                                "ref": e["evidence"][0] if e["evidence"]
                                else None})

        state = derive_state(a, now=now, deployed=deployed,
                             deploy_why=deploy_why, heartbeat_at=hb_at,
                             stale_s=stale_s, status=st, signals=signals)
        wk = work_states.get(a) if deployed else None
        tgt = by_target.get(a) or {}
        agents.append({
            "agent": a, "slug": seat["slug"],
            "display_name": seat["display_name"], "title": seat["title"],
            "role": seat["title"],
            "authority": {"level": seat["authority_level"],
                          "may": seat["may"], "may_not": seat["may_not"],
                          "tool_permissions": st.get("tool_permissions")
                          if seat["kind"] != "INTEL" else None},
            "workspace": seat["workspace"],
            "deployed": deployed, "deploy_why": deploy_why,
            "heartbeat": {"at": _ep(hb_at),
                          "age_s": state["heartbeat_age_s"],
                          "stale_after_s": stale_s,
                          "source": ("intel_runs (shadow intelligence cycle)"
                                     if seat["kind"] == "INTEL" else
                                     "agent_status.last_heartbeat_at")},
            "status_row": None if seat["kind"] == "INTEL" else {
                k: st.get(k) for k in (
                    "state", "activity", "waiting_on", "last_run_started_at",
                    "last_run_finished_at", "runs", "errors", "last_error")},
            "state": state["state"], "state_detail": state["detail"],
            "state_since": state["since"], "state_basis": state["basis"],
            "activity_basis": state["activity_basis"],
            "focus": focus, "last_output": last_output,
            "monitor": monitor,
            "work_state": (wk or {}).get("state"),
            "work_detail": (wk or {}).get("detail") if wk else (
                "Not yet deployed (%s)" % deploy_why if not deployed else
                "Work facts unavailable (%s)" % (
                    rd.sections.get("work_states", {}).get("why")
                    or "NOT_READ")),
            "work_basis": (wk or {}).get("basis") or [],
            "work_since": (wk or {}).get("since"),
            "work_counts": (wk or {}).get("counts"),
            "challenges": {
                "open_against": (tgt.get("open", 0) + tgt.get("responded", 0))
                if a in ("DEREK", "XAVIER", "AUDREY", "CHIEF_ALLOCATOR")
                and rd.sections.get("karen_challenges", {}).get(
                    "status") in ("OK", "EMPTY") else None,
                "raised_open": sum(v["open"] + v["responded"]
                                   for v in by_target.values())
                if a == "KAREN" and rd.sections.get(
                    "karen_challenges", {}).get("status") in ("OK", "EMPTY")
                else None},
        })
    counts = {s: sum(1 for x in agents if x["state"] == s) for s in STATES}
    work_counts = {s: sum(1 for x in agents if x["work_state"] == s)
                   for s in _WORK_STATES}
    return {"version": VERSION, "read_at": now, "read_only": True,
            "window_s": window_s, "active_window_s": ACTIVE_WINDOW_S,
            "states": list(STATES), "counts": counts,
            "work_states": list(_WORK_STATES),
            "work_counts": work_counts,
            "agents": agents, "edges": edges,
            "feed": feed, "opportunities": opportunities,
            "sections": rd.sections,
            "disclosure": ("Every state is derived from recorded rows; "
                           "PAPER and ACTUAL are separate and never summed; "
                           "no figure here is an order or an approval.")}


async def build_agent_detail(conn, slug: str, *, now: float | None = None
                             ) -> dict | None:
    """One workspace: the floor entry plus its queue, outputs, challenges
    given / received and a day of collaboration edges."""
    seat = SEAT_BY_SLUG.get(str(slug or "").lower())
    if seat is None:
        return None
    now = float(now if now is not None else time.time())
    floor = await build_floor(conn, now=now, window_s=DETAIL_WINDOW_S)
    me = next(x for x in floor["agents"] if x["agent"] == seat["agent"])
    a = seat["agent"]
    rd = _Reads(conn)

    async def queue(c):
        out = []
        if await rd.exists("agent_tasks"):
            for r in await c.fetch(
                    "SELECT task_id, kind, title, status, created_by, "
                    "       updated_at FROM agent_tasks WHERE assignee=$1 "
                    "   AND status NOT IN ('REJECTED','RELEASED','ROLLED_BACK',"
                    "       'CLOSED_NO_CHANGE','CANCELLED','APPROVED') "
                    " ORDER BY updated_at DESC LIMIT 25", a):
                out.append({"kind": "agent_tasks", "id": r["task_id"],
                            "title": r["title"], "status": r["status"],
                            "from": r["created_by"],
                            "at": _ep(r["updated_at"]),
                            "href": "/api/command/agents/tasks/%s"
                            % r["task_id"]})
        if await rd.exists("karen_challenges") and a in (
                "DEREK", "XAVIER", "AUDREY", "CHIEF_ALLOCATOR"):
            for r in await c.fetch(
                    "SELECT challenge_id, severity, claim, state, "
                    "       challenged_at FROM karen_challenges "
                    " WHERE target_agent=$1 AND state='OPEN' "
                    " ORDER BY challenged_at DESC LIMIT 25", a):
                out.append({"kind": "karen_challenges",
                            "id": r["challenge_id"],
                            "title": "Answer Karen: %s" % str(r["claim"])[:160],
                            "status": "%s · %s" % (r["state"], r["severity"]),
                            "from": "KAREN", "at": _ep(r["challenged_at"]),
                            "href": "/api/command/karen/challenges/%s"
                            % r["challenge_id"]})
        if a == "XAVIER" and await rd.exists("agent_work_open") and \
                await rd.exists("agent_work_requests"):
            # (226) the fresh-evidence acquisitions his stale reviews
            # enqueued and that are still open
            for r in await c.fetch(
                    "SELECT r.request_id, r.kind, r.group_id, r.reason, "
                    "       r.enqueued_at, r.expires_at "
                    "  FROM agent_work_open o JOIN agent_work_requests r "
                    "    ON r.request_id = o.request_id "
                    " WHERE o.agent_id = 'XAVIER' "
                    " ORDER BY r.enqueued_at DESC LIMIT 25"):
                out.append({"kind": "agent_work_requests",
                            "id": r["request_id"],
                            "title": "Acquire %s for %s" % (r["kind"],
                                                           r["group_id"]),
                            "status": "OPEN · %s" % r["reason"],
                            "from": "XAVIER", "at": _ep(r["enqueued_at"]),
                            "expires_at": _ep(r["expires_at"])})
        if a == "AUDREY" and await rd.exists("karen_challenges"):
            for r in await c.fetch(
                    "SELECT challenge_id, target_agent, claim, responded_at "
                    "  FROM karen_challenges WHERE state='RESPONDED' "
                    "   AND target_agent IN ('DEREK','XAVIER',"
                    "       'CHIEF_ALLOCATOR') "
                    " ORDER BY responded_at DESC LIMIT 25"):
                out.append({"kind": "karen_challenges",
                            "id": r["challenge_id"],
                            "title": "Evaluate %s's answer: %s" % (
                                r["target_agent"].title(),
                                str(r["claim"])[:140]),
                            "status": "AWAITING_INDEPENDENT_EVALUATION",
                            "from": r["target_agent"],
                            "at": _ep(r["responded_at"]),
                            "href": "/api/command/karen/challenges/%s"
                            % r["challenge_id"]})
        return sorted(out, key=lambda x: -(x["at"] or 0))

    async def outputs(c):
        out = []
        if a == "DEREK" and await rd.exists("paper_decisions"):
            for r in await c.fetch(
                    "SELECT decision_id, decided_at, verdict, refusal, "
                    "       us_market_slug, holding_side, p_blended, "
                    "       limit_price FROM paper_decisions "
                    " ORDER BY decided_at DESC LIMIT 20"):
                out.append({"kind": "paper_decisions", "id": r["decision_id"],
                            "at": _ep(r["decided_at"]),
                            "verdict": r["verdict"],
                            "summary": "%s %s %s%s" % (
                                r["verdict"], r["us_market_slug"] or "",
                                r["holding_side"] or "",
                                (" · " + r["refusal"]) if r["refusal"]
                                else "")})
        elif a == "XAVIER" and await rd.exists(
                "xavier_management_assessments"):
            newest: dict = {}
            for r in await c.fetch(
                    "SELECT assessment_id, position_kind, group_id, "
                    "       assessed_at, trigger, evidence_state, "
                    "       thesis_state, recommendation, probability, "
                    "       probability_source, probability_age_s, "
                    "       to_jsonb(x) -> 'valuation' AS valuation "
                    "  FROM xavier_management_assessments x "
                    " ORDER BY assessed_at DESC, assessment_id DESC "
                    " LIMIT 20"):
                # newest first: an older row of a position already listed
                # is SUPERSEDED by it, never a current recommendation
                key = (r["position_kind"], r["group_id"])
                g = _gate_assessment(dict(r), time.time(),
                                     superseded_by=newest.get(key))
                newest.setdefault(key, r["assessment_id"])
                out.append({"kind": "xavier_management_assessments",
                            "id": r["assessment_id"],
                            "at": _ep(r["assessed_at"]),
                            "verdict": g["recommendation"],
                            "recommendation_state": g[
                                "recommendation_state"],
                            "recorded_recommendation": g[
                                "recorded_recommendation"],
                            "management_state": g["management_state"],
                            "superseded_by": g["superseded_by"],
                            "valuation_id": g["valuation_id"],
                            "valuation_timestamp": g["valuation_timestamp"],
                            "age_seconds": g["age_seconds"],
                            "freshness_limit": g["freshness_limit"],
                            "summary": "%s · %s %s · %s · %s" % (
                                g["recommendation_label"],
                                r["position_kind"], r["group_id"],
                                r["evidence_state"], r["thesis_state"])})
        elif a == "AUDREY" and await rd.exists("paper_audrey_findings"):
            for r in await c.fetch(
                    "SELECT finding_id, found_at, kind, severity, subject "
                    "  FROM paper_audrey_findings "
                    " ORDER BY found_at DESC LIMIT 20"):
                out.append({"kind": "paper_audrey_findings",
                            "id": r["finding_id"], "at": _ep(r["found_at"]),
                            "verdict": r["severity"],
                            "summary": "%s %s%s" % (
                                r["severity"], r["kind"],
                                (" · " + r["subject"]) if r["subject"]
                                else "")})
        elif a == "KAREN" and await rd.exists("karen_challenges"):
            for r in await c.fetch(
                    "SELECT challenge_id, target_agent, severity, claim, "
                    "       state, challenged_at FROM karen_challenges "
                    " ORDER BY challenged_at DESC LIMIT 20"):
                out.append({"kind": "karen_challenges",
                            "id": r["challenge_id"],
                            "at": _ep(r["challenged_at"]),
                            "verdict": r["state"],
                            "summary": "%s → %s: %s" % (
                                r["severity"], r["target_agent"],
                                str(r["claim"])[:160]),
                            "href": "/api/command/karen/challenges/%s"
                            % r["challenge_id"]})
        elif a == "ADRIANA" and await rd.exists("adriana_arb_scans"):
            for r in await c.fetch(
                    "SELECT opportunity_id, structure_kind, event_key, "
                    "       venues, max_qty, net_profit_usd, decided_at "
                    "  FROM adriana_arb_opportunities "
                    " ORDER BY decided_at DESC LIMIT 10"):
                out.append({"kind": "adriana_arb_opportunities",
                            "id": r["opportunity_id"],
                            "at": _ep(r["decided_at"]),
                            "verdict": "GUARANTEED_AFTER_COSTS",
                            "summary": "%s · %s · %s · %d sets · $%.2f "
                                       "worst-case net (SHADOW)" % (
                                           r["structure_kind"],
                                           r["event_key"],
                                           "+".join(r["venues"] or []),
                                           r["max_qty"],
                                           float(r["net_profit_usd"])),
                            "href": "/api/command/adriana/opportunities/%s"
                            % r["opportunity_id"]})
            for r in await c.fetch(
                    "SELECT refusal_id, structure_kind, event_key, venues, "
                    "       primary_code, decided_at "
                    "  FROM adriana_arb_refusals "
                    " ORDER BY decided_at DESC LIMIT 10"):
                out.append({"kind": "adriana_arb_refusals",
                            "id": r["refusal_id"],
                            "at": _ep(r["decided_at"]),
                            "verdict": "REFUSED",
                            "summary": "REFUSED %s · %s · %s · %s" % (
                                r["primary_code"], r["structure_kind"],
                                r["event_key"] or "NO_EVENT_IDENTITY",
                                "+".join(r["venues"] or []))})
            out.sort(key=lambda x: -(x["at"] or 0))
        elif a == "CHIEF_ALLOCATOR" and await rd.exists("intel_runs"):
            for r in await c.fetch(
                    "SELECT run_id, status, started_at, finished_at, "
                    "       summary FROM intel_runs "
                    " WHERE component='ALLOCATOR' "
                    " ORDER BY started_at DESC LIMIT 20"):
                s = _j(r["summary"]) or {}
                out.append({"kind": "intel_runs", "id": r["run_id"],
                            "at": _ep(r["finished_at"] or r["started_at"]),
                            "verdict": r["status"],
                            "summary": "%s · %s candidates · %s SHADOW "
                                       "allocated" % (
                                           r["status"],
                                           s.get("candidates", "?"),
                                           _money(s.get("allocated_usd"))
                                           or "UNAVAILABLE"),
                            "href": "/api/command/intel/allocator"})
        return out

    async def challenges(c):
        if not await rd.exists("karen_challenges"):
            return None
        cols = ("SELECT challenge_id, target_agent, target_kind, target_id, "
                "       severity, claim, state, challenged_at, "
                "       response_stance, responded_at, outcome, resolved_by, "
                "       resolved_at FROM karen_challenges ")

        def row(r):
            d = {k: (_ep(v) if isinstance(v, datetime) else v)
                 for k, v in dict(r).items()}
            d["href"] = "/api/command/karen/challenges/%s" % r["challenge_id"]
            return d
        given = ([row(r) for r in await c.fetch(
            cols + "ORDER BY challenged_at DESC LIMIT 20")]
            if a == "KAREN" else [])
        received = ([row(r) for r in await c.fetch(
            cols + "WHERE target_agent=$1 ORDER BY challenged_at DESC "
            "LIMIT 20", a)] if a != "KAREN" else [])
        evaluated = [row(r) for r in await c.fetch(
            cols + "WHERE upper(resolved_by)=$1 ORDER BY resolved_at DESC "
            "LIMIT 20", a)]
        return {"given": given, "received": received, "evaluated": evaluated}

    q = await rd.run("queue", (), queue, default=[]) or []
    o = await rd.run("outputs", (), outputs, default=[]) or []
    c = await rd.run("challenges", (), challenges)
    timeline = [e for e in floor["edges"] if a in (e["from"], e["to"])]
    return {"version": VERSION, "read_at": now, "read_only": True,
            "agent": me, "queue": q, "outputs": o, "challenges": c,
            "timeline": timeline, "timeline_window_s": DETAIL_WINDOW_S,
            "peers": [{"agent": x["agent"], "slug": x["slug"],
                       "display_name": x["display_name"],
                       "state": x["state"], "work_state": x["work_state"],
                       "workspace": x["workspace"]}
                      for x in floor["agents"]],
            "sections": dict(floor["sections"], **{
                "detail." + k: v for k, v in rd.sections.items()}),
            "disclosure": floor["disclosure"]}


async def _read_only(fn):
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = %d"
                               % STATEMENT_TIMEOUT_MS)
            return await fn(conn)


@router.get("/api/command/floor", dependencies=[Depends(require_read)])
async def floor_index(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _read_only(lambda conn: build_floor(conn))
    except HTTPException:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "FLOOR_READ_FAILED", "detail": type(exc).__name__})


@router.get("/api/command/floor/{agent}",
            dependencies=[Depends(require_read)])
async def floor_agent(agent: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    # (266) a historical alias (eddie) reads the seat it now names (archer)
    # and says so -- the same payload plus alias_of / historical_alias
    canon = SEAT_ALIASES.get(str(agent).lower())
    if canon:
        got = await floor_agent(canon, response)
        return dict(got or {}, alias_of=canon,
                    historical_alias=str(agent).upper())
    if str(agent).lower() not in SEAT_BY_SLUG:
        raise HTTPException(status_code=404, detail={
            "reason": "NOT_A_FLOOR_AGENT", "agents": sorted(SEAT_BY_SLUG)})
    try:
        got = await _read_only(lambda conn: build_agent_detail(conn, agent))
    except HTTPException:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "reason": "FLOOR_READ_FAILED", "detail": type(exc).__name__})
    return got
