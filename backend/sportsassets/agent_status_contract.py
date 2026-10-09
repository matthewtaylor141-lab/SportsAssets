"""EVERY AGENT'S STATUS CONTRACT (owner Mission 4, 2026-10-09).

    "Every agent exposes its latest action, status, input freshness, next
     cycle, refusals and failures; DEGRADED, never green."

ONE READ-ONLY ANSWER, PER SUBJECT, FROM THE ROWS THE SUBJECT ALREADY WRITES
(GET /api/command/agent-status). A SUBJECT is

  AGENT              the eight desks: Derek, Xavier, Audrey, Karen, Archer
                     (historical alias EDDIE: its rows are shown under
                     ARCHER, named, never as a ninth agent), Scout, Adriana
                     and the Chief Allocator;
  RUNTIME_LOOP       every recurring loop the API lifespan or the workers
                     service runs (loop_health.INVENTORY, the same inventory
                     GET /api/command/loop-health reads, with its R30A
                     verdict carried as is), and the venue-write loops the
                     workers register and deliberately never start (cand21);
  DEDICATED_SERVICE  the market-plane service's own beats (market_plane,
                     universal_market_plane, kalshi_ws_market_data), which
                     run in their own Render service and are therefore in no
                     loop inventory.

EVERY SUBJECT CARRIES (the frontend schema; all instants are epoch seconds,
every age is measured against `now`, the instant the read was made):

  latest_action   {what, at, age_s, source} -- the newest thing the subject
                  RECORDED doing (a durable output, a finished run, the
                  cycle end its own heartbeat recorded, a loop's newest
                  pass), or null
  status          GREEN | DEGRADED | FAILED | UNKNOWN | DISABLED, with
                  `status_reasons` naming every rule that applied
  input_freshness [{input, source, age_s, bound_s, fresh, why}] -- the
                  subject's own liveness record first, then each recorded
                  input its work depends on (the hosting loop, the PinnAPI
                  feed telemetry, the venue book reads, Xavier's held
                  positions' current reviews, Adriana's census evidence)
  next_cycle_at   when the next cycle is due under the cadence the subject
                  RECORDS (agent_status.cadence, the servicing heartbeat's
                  own cadence digest, the entry cycle's start-to-start rule,
                  a loop's inventoried period), or null with
                  `next_cycle_why`
  refusals        {status, window_s, total, by_reason, by_class, software,
                  unclassified_total, source} -- MEASURED from the subject's
                  refusal records in the window, every code classified by
                  the ONE taxonomy (refusal_taxonomy); NOT_APPLICABLE (with
                  the structural reason) for a subject that records none;
                  UNAVAILABLE when the records could not be read
  failures        {status, window_s, count, count_is_lower_bound, last
                  {at, what, source}, lifetime_errors, by_source} --
                  MEASURED from failed runs, failed passes, self-reported
                  FAILED states and failed attempts in the window
  missing_fields  every contract field that could not be established

THE STATUS RULES (pure; `finalize`), in order:

  DISABLED  a loop not armed in this deployment (named), or a venue-write
            loop registered and never started by design. Never GREEN.
  UNKNOWN   no heartbeat at all, the heartbeat unreadable, an agent that has
            NEVER run, a loop whose health is UNAVAILABLE or that has not
            succeeded yet since it started, an event-driven loop with no
            event in the window. NEVER GREEN BY DEFAULT.
  FAILED    the subject is DOWN or ERRORING: its heartbeat is older than its
            stale bound, it reported FAILED itself, its newest run / pass is
            a failure, or the R30A loop verdict is UNHEALTHY.
  DEGRADED  it is alive, and ANY of: an input not fresh; a failure recorded
            in the window; a SOFTWARE or UNCLASSIFIED refusal in the window
            (an ECONOMIC refusal is the system working -- CASH is an
            acceptable decision -- and never degrades); the subject's own
            word that it is waiting, blocked, recovering or degraded; ANY
            contract field missing.
  GREEN     only what is left: alive, every input fresh, no failure and no
            software / unclassified refusal in the window, every field
            present. `finalize` asserts GREEN carries no reason and no
            missing field.

BOUNDS ARE THE CODE BASE'S OWN, NEVER NEW SLAs: a liveness record is stale
after loop_health.HEALTH_FACTOR (3) x the cadence the writer declares, with
agent_work_state.STALE_FLOOR_S (900 s) as the floor for an agent (the floor's
and the work state's rule); the PinnAPI feed after 3 beats
(agent_work_state.FEED_HEARTBEAT_S); a venue book read after
agent_work_state.MARKET_DATA_MAX_AGE_S (Adriana: her own BOOK_WINDOW_S).
The failure / refusal window is a query parameter (default 3600 s, the
software-reds gate's own window); it judges recency, never freshness.

WHAT THIS MODULE CANNOT DO. It imports no order, venue, execution, ledger,
paper or funded module (tests walk its imports), issues SELECTs only (its
SQL is scanned for write keywords) inside the caller's READ ONLY transaction,
each section in its own savepoint, every read bounded by an index-backed
window or a LIMIT. A passing status grants nothing: it is a readback, never
an authority, a gate or a trading permission.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from . import agent_work_state as AWS
from . import loop_health as LH
from . import refusal_taxonomy as RT

VERSION = "AGENT_STATUS_CONTRACT_V1"
AUTHORITY = "READ_ONLY_NO_ORDER_NO_CAPITAL_AUTHORITY"

GREEN, DEGRADED, FAILED, UNKNOWN, DISABLED = (
    "GREEN", "DEGRADED", "FAILED", "UNKNOWN", "DISABLED")
STATUSES = (GREEN, DEGRADED, FAILED, UNKNOWN, DISABLED)
#: the base verdict of the liveness rules before the DEGRADED checks
OK = "OK"

AGENT, LOOP, SERVICE = "AGENT", "RUNTIME_LOOP", "DEDICATED_SERVICE"
MEASURED, NOT_APPLICABLE, UNAVAILABLE = (
    "MEASURED", "NOT_APPLICABLE", "UNAVAILABLE")

DEFAULT_WINDOW_S = 3600.0
MIN_WINDOW_S = 300.0
MAX_WINDOW_S = 86400.0
STATEMENT_TIMEOUT_MS = 5000
HEALTH_FACTOR = LH.HEALTH_FACTOR
STALE_FLOOR_S = AWS.STALE_FLOOR_S
#: a run older than this with no finish is not in progress (the floor's and
#: the work state's RUN_WINDOW_FLOOR_S): counted as a HUNG run, a failure
HUNG_RUN_S = AWS.RUN_WINDOW_FLOOR_S
FEED_BOUND_S = HEALTH_FACTOR * AWS.FEED_HEARTBEAT_S
VENUE_BOUND_S = AWS.MARKET_DATA_MAX_AGE_S
#: refusal / failure groups read per source, and reasons kept per subject
MAX_GROUPS = 2000
MAX_REASONS = 40
MAX_TEXT = 200

#: copied, not imported (this module imports no worker or agent module);
#: tests/test_agent_status_contract.py pins each to its source
EXT_CYCLE_S = 900.0                 # = workers.ext_pinnacle_loop.CYCLE_S
EXT_IDLE_POLL_S = 60.0              # = workers.ext_pinnacle_loop.IDLE_POLL_S
ADRIANA_BOOK_WINDOW_S = 900.0       # = agents.adriana.BOOK_WINDOW_S
ARCHER_NOT_EXECUTING = ("WAIT", "SKIP_EXECUTION")   # = archer.WAIT, SKIP
EVAL_FAILED_OUTCOMES = ("TIMEOUT", "ERROR")  # paper_evaluation_attempts
RUN_FAILED_STATUSES = ("FAILED", "TIMEOUT")  # intel / pos / twin / improve
#: the shadow lanes' decision ledger (shadow_decisions.lane, whose CHECK
#: admits exactly these two): = shadow_lanes.BETTOR_EV_SHADOW and
#: shadow_lanes.RN1_SHADOW; a refusal is a NO_TRADE decision (= shadow.
#: NO_TRADE) named by its first blocker. The experimental lane records its
#: decisions elsewhere (bettor_experimental_decisions) with an action and a
#: free-text why, no refusal code: its refusals are NOT_APPLICABLE, named.
SHADOW_LANES = {"shadow_bettor": "BETTOR_EV_SHADOW",
                "shadow_rn1": "RN1_SHADOW"}
NO_REFUSAL_CODE_LOOPS = {
    "shadow_experimental": "EXPERIMENTAL_LANE_RECORDS_ACTIONS_WITH_A_FREE_"
                           "TEXT_WHY_NO_REFUSAL_CODE"}
NO_TRADE = "NO_TRADE"
#: a writer's own word that a pass RAN but is impaired (kalshi_market_data
#: and the plane write 'degraded', the workers memory beat 'high', the
#: reconciler 'drift' when its walk found missed trades): never GREEN
DEGRADED_BEATS = frozenset({"degraded", "high", "drift"})
#: agent_status states that are the agent's own word that it is not working
#: normally (registry.STATES)
SELF_DEGRADED_STATES = ("WAITING_FOR_EVIDENCE", "WAITING_FOR_PROVIDER",
                        "BLOCKED", "RECOVERING")
SELF_FAILED_STATE = "FAILED"
NOT_YET_RUN = "NOT_YET_RUN"          # = registry.ensure_identities' activity
#: = agents.registry.HISTORICAL_ALIASES (pinned): an alias's rows are shown
#: under the agent it now names, never as another agent
HISTORICAL_ALIASES = {"EDDIE": "ARCHER"}
#: a next cycle that is structurally unknowable (not a missing field)
STRUCTURAL_NO_NEXT_CYCLE = ("EVENT_DRIVEN",)

# ═════════════════════════════════════════════════════════════════════
# THE SUBJECTS
# ═════════════════════════════════════════════════════════════════════

#: refusal sources (the agent's own refusal records)
RF_PAPER_DECISIONS = "PAPER_DECISIONS"
RF_XAVIER_REVIEWS = "XAVIER_REVIEWS"
RF_ARCHER_ESTIMATES = "ARCHER_ESTIMATES"
RF_ADRIANA_REFUSALS = "ADRIANA_REFUSALS"

AGENT_SPECS = (
    {"id": "DEREK", "name": "Derek", "role": "Entry decisions (PAPER)",
     "host_loop": ("ext_pinnacle.entry_cycle", "api"),
     "market": ("feed", "venue"), "venue_bound_s": VENUE_BOUND_S,
     "cycle": "EXT_ENTRY_CYCLE", "refusals": RF_PAPER_DECISIONS,
     "status_failures": True, "eval_failures": True},
    {"id": "XAVIER", "name": "Xavier", "role": "Position management",
     "host_loop": ("ext_pinnacle.servicing", "api"),
     "market": ("feed", "venue"), "venue_bound_s": VENUE_BOUND_S,
     "positions": True, "refusals": RF_XAVIER_REVIEWS,
     "status_failures": True},
    {"id": "AUDREY", "name": "Audrey", "role": "Audit and reporting",
     "host_loop": ("ext_pinnacle.servicing", "api"), "cycle": "SLOW_HALF",
     "refusals": None, "status_failures": True,
     "no_refusals_why": "AUDIT_ROLE_RECORDS_FINDINGS_NOT_REFUSALS"},
    {"id": "KAREN", "name": "Karen", "role": "Red team (challenge only)",
     "host_loop": ("agents.karen_runner", "api"), "refusals": None,
     "no_refusals_why": "CHALLENGE_ONLY_AGENT_RECORDS_NO_REFUSALS"},
    {"id": "ARCHER", "name": "Archer", "role": "Execution (SHADOW)",
     "host_loop": ("agents.archer_runner", "api"), "market": ("venue",),
     "venue_bound_s": VENUE_BOUND_S, "refusals": RF_ARCHER_ESTIMATES,
     "aliases": ("EDDIE",)},
    {"id": "SCOUT", "name": "Scout", "role": "Research (SHADOW)",
     "host_loop": ("agents.scout_runner", "api"), "refusals": None,
     "no_refusals_why": "RESEARCH_AGENT_RECORDS_NO_REFUSALS_FEATURES_ARE_"
                        "JUDGED_BY_THE_EVALUATOR"},
    {"id": "ADRIANA", "name": "Adriana", "role": "Arbitrage census (SHADOW)",
     "host_loop": ("agents.adriana_runner", "api"), "market": ("venue",),
     "venue_bound_s": ADRIANA_BOOK_WINDOW_S, "census": True,
     "refusals": RF_ADRIANA_REFUSALS},
    {"id": "CHIEF_ALLOCATOR", "name": "Chief Allocator",
     "role": "Shadow allocation", "host_loop": ("intel.runner", "api"),
     "heartbeat": "INTEL_ALLOCATOR", "cycle": "INTEL_ALLOCATOR",
     "refusals": None,
     "no_refusals_why": "ALLOCATOR_RANKS_AND_WEIGHTS_RECORDS_NO_REFUSALS"},
)
AGENT_IDS = tuple(a["id"] for a in AGENT_SPECS)
#: whose refusals a hosting loop's records are (not counted twice)
HOSTED_BY = {}
for _a in AGENT_SPECS:
    HOSTED_BY.setdefault(_a["host_loop"], []).append(_a["id"])

#: the dedicated market-plane service's beats (service_heartbeats). Health
#: cadence: market_plane = universal_market_plane.PLANE_BEAT_EVERY_S (60);
#: universal_market_plane beats every pass (INTERVAL_S 2 s + its work) and
#: is judged on 60 s, the cadence its runtime_loop_health row recorded when
#: the shared workers ran it; kalshi_ws_market_data =
#: kalshi_ws_market_data.HEARTBEAT_EVERY_S (15). 'degraded' is a pass that
#: RAN impaired (DEGRADED, never a success to call GREEN); 'blocked' and
#: 'error' are failures.
SERVICE_SPECS = (
    {"name": "market_plane", "service": "market_plane", "cadence_s": 60.0,
     "success": ("ok",), "role": "the dedicated plane's own liveness beat"},
    {"name": "universal_market_plane", "service": "universal_market_plane",
     "cadence_s": 60.0, "success": ("ok", "degraded"),
     "role": "the universal market plane's pass (books, freshness, radar)"},
    {"name": "kalshi_ws_market_data", "service": "kalshi_ws_market_data",
     "cadence_s": 15.0, "success": ("ok", "degraded"),
     "role": "Kalshi WebSocket market data (orderless)"},
)


def _ep(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d.timestamp()
    except ValueError:
        return None


def _j(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _age(now: float, at) -> float | None:
    a = _ep(at)
    return None if a is None else round(now - a, 1)


def _text(v, n: int = MAX_TEXT) -> str | None:
    return None if v is None else str(v)[:n]


def bound_window(window_s) -> float:
    """The failure / refusal window, clamped to [MIN, MAX]. Pure."""
    try:
        w = float(window_s)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_S
    if w != w:                                          # NaN
        return DEFAULT_WINDOW_S
    return min(MAX_WINDOW_S, max(MIN_WINDOW_S, w))


# ═════════════════════════════════════════════════════════════════════
# PURE: FIELDS, MISSING FIELDS, THE STATUS
# ═════════════════════════════════════════════════════════════════════

def inp(name: str, source: str, *, age_s=None, bound_s=None, fresh=None,
        why=None, bound_rule=None) -> dict:
    """One input_freshness entry. `fresh` is never inferred from a missing
    age or bound: either missing -> fresh False."""
    if fresh is None and age_s is not None and bound_s is not None:
        fresh = 0 <= float(age_s) <= float(bound_s)
    out = {"input": name, "source": source, "age_s": age_s,
           "bound_s": bound_s, "fresh": bool(fresh) if fresh is not None
           else False, "why": why}
    if bound_rule:
        out["bound_rule"] = bound_rule
    return out


def action(what, at, source, now: float) -> dict | None:
    a = _ep(at)
    if a is None:
        return None
    return {"what": _text(what) or "RECORDED", "at": a,
            "age_s": round(now - a, 1), "source": source}


def newest(*actions) -> dict | None:
    got = [a for a in actions if a and a.get("at") is not None]
    return max(got, key=lambda a: a["at"]) if got else None


def refusals_na(why: str, window_s: float) -> dict:
    return {"status": NOT_APPLICABLE, "window_s": window_s, "total": 0,
            "by_reason": {}, "by_class": {}, "software": 0,
            "unclassified_total": 0, "source": None, "why": why}


def refusals_unavailable(why: str, window_s: float, source=None) -> dict:
    return {"status": UNAVAILABLE, "window_s": window_s, "total": None,
            "by_reason": None, "by_class": None, "software": None,
            "unclassified_total": None, "source": source, "why": why}


def refusal_summary(counts: dict, *, window_s: float, source: str,
                    software=None, unclassified=None, extra=None) -> dict:
    """{code: n} -> the refusals field. Every code classified by the ONE
    taxonomy; an unknown code is UNCLASSIFIED by name, never economic.
    `software` / `unclassified` override the code-level counts with a
    decision-level census (a decision with ANY software code is a software
    refusal: refusal_taxonomy.decision_class). Pure."""
    counts = {str(k): int(v or 0) for k, v in (counts or {}).items()
              if int(v or 0) > 0}
    s = RT.summarize(counts)
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    keep = top[:MAX_REASONS]
    sw = s["by_class"].get(RT.SOFTWARE, 0) if software is None else software
    un = (s["by_class"].get(RT.UNCLASSIFIED, 0) if unclassified is None
          else unclassified)
    out = {"status": MEASURED, "window_s": window_s,
           "total": sum(counts.values()), "by_reason": dict(keep),
           "by_reason_truncated": len(top) - len(keep),
           "by_class": s["by_class"], "software": int(sw),
           "unclassified_total": int(un),
           "unclassified": sorted(s["unclassified"])[:MAX_REASONS],
           "source": source, "why": None}
    if extra:
        out.update(extra)
    return out


def failures_field(window_s: float, sources: list, *, status=MEASURED,
                   why=None) -> dict:
    """sources: [{"source", "count", "lower_bound", "last_at", "last_what",
    "lifetime"}] -> the failures field. Pure."""
    if status != MEASURED:
        return {"status": status, "window_s": window_s, "count": None,
                "count_is_lower_bound": None, "last": None,
                "lifetime_errors": None, "by_source": {}, "why": why}
    count = sum(int(s.get("count") or 0) for s in sources)
    lasts = [s for s in sources if s.get("last_at") is not None
             and int(s.get("count") or 0) > 0]
    last = max(lasts, key=lambda s: s["last_at"]) if lasts else None
    life = [s.get("lifetime") for s in sources
            if s.get("lifetime") is not None]
    return {"status": MEASURED, "window_s": window_s, "count": count,
            "count_is_lower_bound": any(s.get("lower_bound")
                                        and int(s.get("count") or 0) > 0
                                        for s in sources),
            "last": None if last is None else {
                "at": last["last_at"], "what": _text(last.get("last_what")),
                "source": last["source"]},
            "lifetime_errors": sum(life) if life else None,
            "by_source": {s["source"]: int(s.get("count") or 0)
                          for s in sources},
            "why": why}


def missing_fields(c: dict) -> list:
    """Every contract field that could not be established. Pure."""
    out = []
    la = c.get("latest_action")
    if not la or la.get("at") is None or not la.get("what"):
        out.append("latest_action")
    ins = c.get("input_freshness")
    if not ins:
        out.append("input_freshness")
    for i in ins or []:
        if i.get("age_s") is None:
            out.append("input_freshness.%s.age_s" % i.get("input"))
        if i.get("bound_s") is None and not i.get("bound_rule"):
            out.append("input_freshness.%s.bound_s" % i.get("input"))
    if c.get("next_cycle_at") is None and c.get("next_cycle_why") not in \
            STRUCTURAL_NO_NEXT_CYCLE:
        out.append("next_cycle_at")
    r = c.get("refusals") or {}
    if r.get("status") not in (MEASURED, NOT_APPLICABLE):
        out.append("refusals")
    f = c.get("failures") or {}
    if f.get("status") != MEASURED or f.get("count") is None:
        out.append("failures")
    return out


def finalize(c: dict, *, base: str, hard: list, soft: list) -> dict:
    """THE STATUS. `base` is DISABLED / UNKNOWN / FAILED from the liveness
    rules, or OK; `hard` names why the base is not OK; `soft` the subject-
    specific degrading observations (its own word that it waits, a
    self-reported degraded beat). Pure. GREEN only when the base is OK and
    nothing at all degrades it."""
    missing = missing_fields(c)
    degr = list(soft)
    for i in c.get("input_freshness") or []:
        if not i.get("fresh"):
            degr.append("INPUT_NOT_FRESH:%s" % i.get("input"))
    f = c.get("failures") or {}
    if f.get("count"):
        degr.append("FAILURES_IN_WINDOW:%d%s" % (
            f["count"], "+" if f.get("count_is_lower_bound") else ""))
    r = c.get("refusals") or {}
    if r.get("software"):
        degr.append("SOFTWARE_REFUSALS_IN_WINDOW:%d" % r["software"])
    if r.get("unclassified_total"):
        degr.append("UNCLASSIFIED_REFUSALS_IN_WINDOW:%d"
                    % r["unclassified_total"])
    degr += ["FIELD_MISSING:%s" % m for m in missing]
    if base == OK:
        status = DEGRADED if degr else GREEN
    else:
        status = base
    # a DISABLED subject's absent records are its configuration, not news:
    # its reasons are why it is disabled (the missing fields stay listed)
    reasons = list(hard) + ([] if base == DISABLED else degr)
    c.update(status=status, status_reasons=reasons, missing_fields=missing)
    # THE INVARIANT, ENFORCED WHERE THE STATUS IS MADE
    if status == GREEN and (reasons or missing):
        raise AssertionError("GREEN with reasons or missing fields")
    return c


def skeleton(sid: str, kind: str, name: str, *, role=None, process=None,
             capital_critical=False, sources=(), window_s: float) -> dict:
    return {"id": sid, "kind": kind, "name": name, "role": role,
            "process": process, "capital_critical": bool(capital_critical),
            "heartbeat_sources": list(sources), "status": None,
            "status_reasons": [], "latest_action": None,
            "input_freshness": [], "next_cycle_at": None,
            "next_cycle_why": None, "next_cycle": None,
            "refusals": refusals_unavailable("NOT_READ", window_s),
            "failures": failures_field(window_s, [], status=UNAVAILABLE,
                                       why="NOT_READ"),
            "missing_fields": []}


def set_next(c: dict, at, *, basis: str | None, why: str | None,
             now: float) -> None:
    a = _ep(at)
    c["next_cycle_at"] = a
    c["next_cycle_why"] = None if a is not None else (why or
                                                      "NO_RECORDED_CADENCE")
    c["next_cycle"] = {"at": a, "in_s": None if a is None
                       else round(a - now, 1), "basis": basis,
                       "why": c["next_cycle_why"]}


# ═════════════════════════════════════════════════════════════════════
# PURE: ONE AGENT
# ═════════════════════════════════════════════════════════════════════

def agent_interval(cadence) -> tuple:
    """(interval_s, key) from an agent_status.cadence record: the declared
    *_interval_s (target_interval_s / review_interval_s ...). Pure."""
    c = _j(cadence)
    if not isinstance(c, dict):
        return None, None
    for k in sorted(c):
        if str(k).endswith("_interval_s"):
            v = _ep(c[k])
            if v and v > 0:
                return float(v), k
    return None, None


def loop_input(name: str, process: str, verdict: dict | None) -> dict:
    """A loop's R30A verdict as an input of the subject it hosts."""
    label = "loop %s (%s)" % (name, process)
    if not verdict:
        return inp(label, "loop_health", why="LOOP_NOT_IN_THE_READ")
    st = verdict.get("status")
    why = None if st == LH.HEALTHY else "%s:%s" % (st, verdict.get("why"))
    return inp(label, verdict.get("success_source") or "loop_health",
               age_s=verdict.get("lag_s"),
               bound_s=verdict.get("unhealthy_after_s"),
               fresh=(st == LH.HEALTHY), why=why)


def market_inputs(spec: dict, market: dict, now: float,
                  sections: dict) -> list:
    """The feed telemetry and the venue book reads, judged by the work
    state's own rules (agent_work_state.market_status). Pure."""
    out = []
    ms = AWS.market_status(market, now)
    if "feed" in (spec.get("market") or ()):
        f = ms["feed"]
        out.append(inp(
            "PinnAPI feed telemetry", "ingestion_state:%s"
            % AWS.FEED_HEARTBEAT_KEY, age_s=f.get("age_s"),
            bound_s=FEED_BOUND_S,
            fresh=bool(f.get("recorded")) and not f.get("blocked"),
            why=f.get("why") or (sections.get("work.feed") or {}).get(
                "why")))
    if "venue" in (spec.get("market") or ()):
        v = (market or {}).get("venue") or {}
        reads = [r for r in v.get("reads") or []
                 if _ep(r.get("at")) is not None]
        ok_at = max([_ep(r["at"]) for r in reads if not r.get("error")]
                    or [None], key=lambda x: x or 0)
        age = None if ok_at is None else round(now - ok_at, 1)
        bound = float(spec.get("venue_bound_s") or VENUE_BOUND_S)
        failing = ms["venue"].get("blocked")
        why = ms["venue"].get("why") or (
            None if v.get("recorded") else "NO_VENUE_BOOK_READ_RECORDED")
        if why is None and (age is None or age > bound):
            why = "NEWEST_GOOD_BOOK_READ_OLDER_THAN_%dS" % int(bound)
        out.append(inp(
            "venue book reads", "paper_book_observations", age_s=age,
            bound_s=bound, fresh=(age is not None and 0 <= age <= bound
                                  and not failing), why=why))
    return out


def positions_input(pos: dict, market: dict, now: float) -> dict | None:
    """Xavier's held positions: fresh only when every open position's ONE
    current review is CURRENT (xavier_freshness, judged at read time by
    agent_work_state.position_class). None when nothing is held. Pure."""
    if pos is None or pos.get("open") is None:
        return inp("held positions' current reviews", "xavier_current_review",
                   why=(pos or {}).get("why") or "POSITIONS_UNREADABLE",
                   bound_rule="PER_REVIEW_FRESHNESS_WINDOW")
    if not pos.get("open"):
        return None
    ms = AWS.market_status(market, now)
    classes: dict = {}
    ages = []
    for p in pos.get("positions") or []:
        k, _why = AWS.position_class(p, market=ms, now=now)
        classes[k] = classes.get(k, 0) + 1
        cr = p.get("current_review") or {}
        if cr.get("reviewed_at") is not None:
            ages.append(now - float(cr["reviewed_at"]))
    n = int(pos["open"])
    current = classes.get(AWS.P_CURRENT, 0)
    shown = len(pos.get("positions") or [])
    return inp("held positions' current reviews", "xavier_current_review",
               age_s=round(max(ages), 1) if ages else None, bound_s=None,
               fresh=(current == n and shown == n),
               bound_rule="PER_REVIEW_FRESHNESS_WINDOW",
               why=None if current == n else "%d_OF_%d_CURRENT:%s" % (
                   current, n, ",".join("%s=%d" % kv
                                        for kv in sorted(classes.items()))))


def agent_contract(spec: dict, f: dict, *, now: float,
                   window_s: float) -> dict:
    """ONE AGENT'S CONTRACT from its gathered facts. Pure.

    f: {"status": agent_status row dict | None, "status_read": bool,
        "verdicts": {(loop, process): loop_health verdict},
        "market", "positions", "outputs": [..], "runs": {..} | None,
        "last_run": {..} | None, "refusals": field, "eval": {..} | None,
        "loop_errors": {(loop, process): {...}}, "allocator": {..},
        "ext_cycle": {..}, "servicing": {..}, "census": {..},
        "sections": {..}, "aliases": [..]}"""
    aid = spec["id"]
    hb_sources = (["intel_runs:ALLOCATOR"] if spec.get("heartbeat")
                  == "INTEL_ALLOCATOR" else ["agent_status:%s" % aid])
    c = skeleton(aid, AGENT, spec["name"], role=spec.get("role"),
                 process=spec["host_loop"][1], sources=hb_sources,
                 window_s=window_s)
    c["host_loop"] = {"name": spec["host_loop"][0],
                      "process": spec["host_loop"][1]}
    hard: list = []
    soft: list = []
    st = f.get("status") or {}
    verdict = (f.get("verdicts") or {}).get(spec["host_loop"])

    # ── liveness: the heartbeat and its bound ──────────────────────────
    if spec.get("heartbeat") == "INTEL_ALLOCATOR":
        al = f.get("allocator") or {}
        hb_at = _ep(al.get("newest_started_at"))
        cad = (LH.BY_NAME.get(spec["host_loop"]) or {}).get("cadence_s")
        bound = max(STALE_FLOOR_S, HEALTH_FACTOR * cad) if cad else None
        read_ok = al.get("read") is True
        interval = cad
    else:
        hb_at = _ep(st.get("last_heartbeat_at"))
        interval, _key = agent_interval(st.get("cadence"))
        host_cad = (LH.BY_NAME.get(spec["host_loop"]) or {}).get("cadence_s")
        declared = interval or (
            EXT_CYCLE_S if spec.get("cycle") == "EXT_ENTRY_CYCLE" else
            _ep(((f.get("servicing") or {}).get("cadence") or {}).get(
                "learning_interval_s"))
            if spec.get("cycle") == "SLOW_HALF" else host_cad)
        bound = (max(STALE_FLOOR_S, HEALTH_FACTOR * declared) if declared
                 else STALE_FLOOR_S)
        read_ok = f.get("status_read") is True
    hb_age = None if hb_at is None else round(now - hb_at, 1)
    c["input_freshness"].append(inp(
        "heartbeat", hb_sources[0], age_s=hb_age, bound_s=bound,
        why=None if hb_age is not None else (
            "HEARTBEAT_UNREADABLE" if not read_ok
            else "NO_HEARTBEAT_RECORDED")))

    # ── the hosting loop and the recorded inputs ───────────────────────
    hl = spec["host_loop"]
    c["input_freshness"].append(loop_input(hl[0], hl[1], verdict))
    c["input_freshness"] += market_inputs(spec, f.get("market"), now,
                                          f.get("sections") or {})
    if spec.get("positions"):
        pi = positions_input(f.get("positions"), f.get("market"), now)
        if pi is not None:
            c["input_freshness"].append(pi)
    if spec.get("cycle") == "SLOW_HALF":
        sv = (f.get("servicing") or {}).get("cadence") or {}
        li = _ep(sv.get("learning_interval_s"))
        sh = _ep(sv.get("slow_half_at"))
        c["input_freshness"].append(inp(
            "servicing slow half (Audrey's pass)",
            "ingestion_state:ext_pinnacle_last_servicing.servicing_cadence",
            age_s=_age(now, sh) if sh else None,
            bound_s=HEALTH_FACTOR * li if li else None,
            why=None if sh and li else "SLOW_HALF_CADENCE_NOT_RECORDED"))
    if spec.get("census"):
        cs = f.get("census") or {}
        scan_at = _ep(cs.get("finished_at"))
        books = cs.get("books_fresh")
        evidence = cs.get("status") not in (None, "NO_EVIDENCE") and (
            books is None or int(books) > 0)
        c["input_freshness"].append(inp(
            "census evidence (recorded supported-family books)",
            "adriana_arb_scans", age_s=_age(now, scan_at),
            bound_s=ADRIANA_BOOK_WINDOW_S,
            fresh=(scan_at is not None
                   and now - scan_at <= ADRIANA_BOOK_WINDOW_S and evidence),
            why=None if evidence else "%s:%s" % (
                cs.get("status") or "NO_SCAN_RECORDED",
                _text(cs.get("why"), 120))))

    # ── the base verdict ───────────────────────────────────────────────
    state = st.get("state")
    activity = st.get("activity")
    if spec.get("heartbeat") == "INTEL_ALLOCATOR":
        al = f.get("allocator") or {}
        if not read_ok:
            base, hard = UNKNOWN, ["HEARTBEAT_UNREADABLE"]
        elif hb_at is None:
            base, hard = UNKNOWN, ["NO_ALLOCATOR_RUN_RECORDED"]
        elif hb_age > bound:
            base, hard = FAILED, ["HEARTBEAT_STALE:%ds>%ds" % (
                int(hb_age), int(bound))]
        elif al.get("newest_status") in RUN_FAILED_STATUSES:
            base, hard = FAILED, ["LATEST_RUN_FAILED:%s" % al.get(
                "newest_status")]
        else:
            base = OK
    elif not read_ok:
        base, hard = UNKNOWN, ["HEARTBEAT_UNREADABLE"]
    elif not st:
        base, hard = UNKNOWN, ["NO_HEARTBEAT_RECORDED"]
    elif activity == NOT_YET_RUN and not int(st.get("runs") or 0) \
            and st.get("last_run_started_at") is None:
        base, hard = UNKNOWN, ["NEVER_RAN"]
    elif hb_at is None:
        base, hard = UNKNOWN, ["NO_HEARTBEAT_RECORDED"]
    elif hb_age > bound:
        base, hard = FAILED, ["HEARTBEAT_STALE:%ds>%ds" % (int(hb_age),
                                                           int(bound))]
    elif state == SELF_FAILED_STATE:
        base, hard = FAILED, ["SELF_REPORTED_FAILED:%s" % _text(activity,
                                                                120)]
    else:
        base = OK
        if state in SELF_DEGRADED_STATES:
            soft.append("SELF_REPORTED_%s:%s" % (state, _text(activity, 120)))

    # ── latest action ──────────────────────────────────────────────────
    cands = []
    for o in f.get("outputs") or []:
        cands.append(action(o.get("label"), o.get("at"),
                            "%s:%s" % (o.get("table"), o.get("id")), now))
    lr = f.get("last_run") or {}
    if lr.get("finished_at") is not None:
        cands.append(action(lr.get("outcome") or "RUN_FINISHED",
                            lr.get("finished_at"),
                            "agent_runs:%s" % lr.get("run_id"), now))
    if st.get("last_run_finished_at") is not None:
        cands.append(action("%s: %s" % (state, activity),
                            st.get("last_run_finished_at"),
                            "agent_status.last_run_finished_at", now))
    for extra in f.get("extra_actions") or []:
        cands.append(action(extra.get("what"), extra.get("at"),
                            extra.get("source"), now))
    c["latest_action"] = newest(*cands)

    # ── next cycle ─────────────────────────────────────────────────────
    if spec.get("heartbeat") == "INTEL_ALLOCATOR":
        al = f.get("allocator") or {}
        st_at = _ep(al.get("newest_started_at"))
        set_next(c, None if st_at is None or not interval
                 else st_at + interval,
                 basis="newest ALLOCATOR intel_runs start + intel.runner "
                       "cadence %ss (loop_health inventory)" % interval,
                 why="NO_ALLOCATOR_RUN_RECORDED", now=now)
    elif interval:
        base_at = _ep(st.get("last_run_started_at")) or hb_at
        set_next(c, None if base_at is None else base_at + interval,
                 basis="agent_status.cadence.%s after the last run start"
                       % _key, why="NO_RUN_START_RECORDED", now=now)
    elif spec.get("cycle") == "EXT_ENTRY_CYCLE":
        ec = f.get("ext_cycle") or {}
        at = _ep(ec.get("at"))
        ran = ec.get("ran")
        if ran is None:
            ran = ec.get("state") == "LIVE"
        el = _ep(ec.get("elapsed_s")) or 0.0
        nxt = None if at is None else (
            at + max(EXT_IDLE_POLL_S, EXT_CYCLE_S - max(0.0, el)) if ran
            else at + EXT_IDLE_POLL_S)
        set_next(c, nxt, basis="ext_pinnacle_last_cycle: start to start at "
                 "CYCLE_S %gs after a cycle that ran, IDLE_POLL_S %gs after "
                 "one that did not (workers.ext_pinnacle_loop."
                 "next_cycle_delay)" % (EXT_CYCLE_S, EXT_IDLE_POLL_S),
                 why="ENTRY_CYCLE_HEARTBEAT_NOT_RECORDED", now=now)
    elif spec.get("cycle") == "SLOW_HALF":
        sv = (f.get("servicing") or {}).get("cadence") or {}
        sh, li = _ep(sv.get("slow_half_at")), _ep(sv.get("learning_interval_s"))
        set_next(c, None if not (sh and li) else sh + li,
                 basis="servicing_cadence.slow_half_at + learning_interval_s "
                       "(the servicing heartbeat's own digest)",
                 why="SLOW_HALF_CADENCE_NOT_RECORDED", now=now)
    else:
        set_next(c, None, basis=None, why="NO_RECORDED_CADENCE", now=now)

    # ── refusals ───────────────────────────────────────────────────────
    if spec.get("refusals") is None:
        c["refusals"] = refusals_na(spec["no_refusals_why"], window_s)
    else:
        c["refusals"] = f.get("refusals") or refusals_unavailable(
            "NOT_READ", window_s)

    # ── failures ───────────────────────────────────────────────────────
    srcs = []
    unreadable = []
    runs = f.get("runs")
    if spec.get("heartbeat") != "INTEL_ALLOCATOR":
        if runs is None:
            unreadable.append("agent_runs")
        else:
            # lifetime: the agent's OWN error counter (agent_status.errors,
            # never windowed), beside the windowed count
            srcs.append({"source": "agent_runs (failed or hung runs)",
                         "count": int(runs.get("failed") or 0)
                         + int(runs.get("hung") or 0),
                         "last_at": _ep(runs.get("last_failed_at")),
                         "last_what": runs.get("last_failed_what"),
                         "lifetime": None if st.get("errors") is None
                         else int(st["errors"])})
    if spec.get("status_failures") and state == SELF_FAILED_STATE and \
            hb_at is not None and now - hb_at <= window_s:
        srcs.append({"source": "agent_status (self-reported FAILED)",
                     "count": 1, "lower_bound": True, "last_at": hb_at,
                     "last_what": "%s %s" % (activity, _text(
                         st.get("last_error"), 160) or ""),
                     "lifetime": None})
    if spec.get("eval_failures"):
        ev = f.get("eval")
        if ev is None:
            unreadable.append("paper_evaluation_attempts")
        else:
            srcs.append({"source": "paper_evaluation_attempts "
                                   "(TIMEOUT / ERROR)",
                         "count": int(ev.get("count") or 0),
                         "last_at": _ep(ev.get("last_at")),
                         "last_what": ev.get("last_what")})
    if spec.get("heartbeat") == "INTEL_ALLOCATOR":
        al = f.get("allocator") or {}
        if al.get("read") is not True:
            unreadable.append("intel_runs")
        else:
            srcs.append({"source": "intel_runs ALLOCATOR (FAILED / TIMEOUT)",
                         "count": int(al.get("failed") or 0),
                         "last_at": _ep(al.get("last_failed_at")),
                         "last_what": al.get("last_failed_what")})
    le = (f.get("loop_errors") or {}).get(spec["host_loop"])
    if verdict:
        err_at = _ep(verdict.get("last_error_at"))
        recent = err_at is not None and now - err_at <= window_s
        srcs.append({"source": "host loop %s (newest error)" % hl[0],
                     "count": 1 if recent else 0, "lower_bound": True,
                     "last_at": err_at,
                     "last_what": verdict.get("last_error")})
        c["host_loop"]["lifetime_errors"] = (le or {}).get("errors")
    if unreadable:
        c["failures"] = failures_field(window_s, srcs, status=UNAVAILABLE,
                                       why="UNREADABLE:%s" % ",".join(
                                           unreadable))
    else:
        c["failures"] = failures_field(window_s, srcs)

    if f.get("aliases"):
        c["historical_aliases"] = f["aliases"]
    c["self_reported"] = {"state": state, "activity": _text(activity),
                          "last_error": _text(st.get("last_error"), 160),
                          "runs": st.get("runs"), "errors": st.get("errors")}
    return finalize(c, base=base, hard=hard, soft=soft)


# ═════════════════════════════════════════════════════════════════════
# PURE: ONE LOOP / ONE DEDICATED SERVICE
# ═════════════════════════════════════════════════════════════════════

def loop_contract(spec: dict, v: dict | None, f: dict, *, now: float,
                  window_s: float) -> dict:
    """ONE RECURRING LOOP'S CONTRACT from its R30A verdict (loop_health.
    classify, carried as is) and the windowed facts. Pure.

    f: {"lifetime_errors": int | None, "run_failures": {..} | None,
        "reactive": {..} | None, "refusals": field | None,
        "parent": verdict | None}"""
    c, base, hard, soft = _loop_parts(
        spec, v, f, now=now, window_s=window_s,
        sid="loop:%s@%s" % (spec["name"], spec["process"]), kind=LOOP)
    return finalize(c, base=base, hard=hard, soft=soft)


def _loop_parts(spec: dict, v: dict | None, f: dict, *, now: float,
                window_s: float, sid: str, kind: str) -> tuple:
    """(contract, base, hard, soft) of one loop or dedicated service, before
    `finalize`. Pure."""
    name, proc = spec["name"], spec["process"]
    srcs = [("%s:%s" % (s[0], s[1])) if len(s) > 1 else s[0]
            for s in spec.get("sources") or ()]
    c = skeleton(sid, kind, name, role=spec.get("note"), process=proc,
                 capital_critical=spec.get("capital_critical"),
                 sources=srcs, window_s=window_s)
    hard: list = []
    soft: list = []
    v = v or {}
    st = v.get("status")
    c["loop_health"] = {"status": st, "why": v.get("why"),
                        "beat_status": v.get("beat_status")}
    cad = spec.get("cadence_s")
    period = spec.get("period_s") or cad

    # ── input: the loop's own success record (and a declared parent) ──
    c["input_freshness"].append(inp(
        "own success record", v.get("success_source") or (
            srcs[0] if srcs else "NO_PERSISTED_HEALTH_SOURCE"),
        age_s=v.get("lag_s"), bound_s=v.get("unhealthy_after_s"),
        fresh=(st == LH.HEALTHY and v.get("lag_s") is not None),
        why=None if st == LH.HEALTHY else "%s:%s" % (st, v.get("why"))))
    if f.get("parent") is not None or spec.get("lease", {}).get("parent"):
        par = spec.get("lease", {}).get("parent")
        c["input_freshness"].append(loop_input(par, proc, f.get("parent")))

    # ── base ──────────────────────────────────────────────────────────
    if st == LH.DISABLED:
        base, hard = DISABLED, ["DISABLED:%s" % v.get("why")]
    elif st in (LH.UNAVAILABLE, None):
        base, hard = UNKNOWN, ["LOOP_HEALTH_UNAVAILABLE:%s" % v.get(
            "why", "NOT_IN_THE_READ")]
    elif st == LH.STARTING:
        base, hard = UNKNOWN, ["NO_SUCCESS_YET_SINCE_START"]
    elif st == LH.UNHEALTHY:
        base, hard = FAILED, ["LOOP_UNHEALTHY:%s" % v.get("why")]
    else:
        base = OK
        succ, err = _ep(v.get("last_success_at")), _ep(v.get("last_error_at"))
        if st == LH.EVENT_DRIVEN:
            ra = f.get("reactive") or {}
            if not ra.get("completed_in_window") and not ra.get(
                    "failed_in_window"):
                base, hard = UNKNOWN, ["NO_EVENT_IN_WINDOW"]
            elif ra.get("orphaned_started"):
                soft.append("ORPHANED_STARTED_ATTEMPTS:%d"
                            % ra["orphaned_started"])
        elif err is not None and (succ is None or err > succ):
            base, hard = FAILED, ["LATEST_PASS_FAILED:%s" % _text(
                v.get("last_error"), 120)]
        bs = v.get("beat_status")
        if base == OK and isinstance(bs, str) and bs in DEGRADED_BEATS:
            soft.append("SELF_REPORTED_DEGRADED:%s" % bs)
        if base == OK and st == LH.DEGRADED:
            # (RC6.2 D6g) the newest success beat recorded phase errors
            soft.append("LOOP_DEGRADED:%s" % v.get("why"))

    # ── latest action: the loop's newest recorded pass ────────────────
    succ_at, err_at = v.get("last_success_at"), v.get("last_error_at")
    ra = f.get("reactive") or {}
    c["latest_action"] = newest(
        action("PASS_SUCCEEDED%s" % (
            (" (%s)" % v["beat_status"]) if v.get("beat_status")
            and st != LH.EVENT_DRIVEN else ""), succ_at,
            v.get("success_source") or "loop_health", now),
        action("PASS_FAILED: %s" % _text(v.get("last_error"), 160), err_at,
               "loop_health", now),
        action("LOOP_STARTED", v.get("last_start_at"),
               v.get("start_source") or "loop_health", now)
        if v.get("start_source") == "runtime_loop_health" else None)

    # ── next cycle ─────────────────────────────────────────────────────
    if st == LH.DISABLED:
        set_next(c, None, basis=None, why="DISABLED", now=now)
    elif st == LH.EVENT_DRIVEN or (cad is None and spec["name"]
                                   == "pinnapi_reactive"):
        set_next(c, None, basis=None, why="EVENT_DRIVEN", now=now)
    elif not period:
        set_next(c, None, basis=None, why="NO_DECLARED_CADENCE", now=now)
    else:
        anchor = [a for a in (_ep(succ_at), _ep(err_at),
                              _ep(v.get("last_start_at"))) if a is not None]
        set_next(c, (max(anchor) + float(period)) if anchor else None,
                 basis="newest recorded pass + period %gs (loop_health "
                       "inventory)" % float(period),
                 why="NO_PASS_RECORDED", now=now)

    # ── refusals ──────────────────────────────────────────────────────
    hosted = HOSTED_BY.get((name, proc))
    if f.get("refusals") is not None:
        c["refusals"] = f["refusals"]
    elif hosted:
        c["refusals"] = refusals_na("RECORDED_UNDER_AGENT:%s"
                                    % ",".join(hosted), window_s)
    elif name in NO_REFUSAL_CODE_LOOPS:
        c["refusals"] = refusals_na(NO_REFUSAL_CODE_LOOPS[name], window_s)
    else:
        c["refusals"] = refusals_na("NO_REFUSAL_RECORD_FOR_THIS_LOOP",
                                    window_s)

    # ── failures ──────────────────────────────────────────────────────
    srcs_f = []
    e_at = _ep(err_at)
    srcs_f.append({"source": "loop_health (newest error)",
                   "count": 1 if e_at is not None and now - e_at <= window_s
                   else 0, "lower_bound": True, "last_at": e_at,
                   "last_what": v.get("last_error"),
                   "lifetime": f.get("lifetime_errors")})
    unreadable = []
    if spec.get("run_table"):
        rf = f.get("run_failures")
        if rf is None:
            unreadable.append(spec["run_table"])
        else:
            srcs_f.append({"source": "%s (FAILED / TIMEOUT runs)"
                                     % spec["run_table"],
                           "count": int(rf.get("failed") or 0),
                           "last_at": _ep(rf.get("last_failed_at")),
                           "last_what": rf.get("last_failed_what")})
    if spec["name"] == "pinnapi_reactive":
        if f.get("reactive") is None:
            unreadable.append("pinnapi_reactive_attempts")
        else:
            srcs_f.append({"source": "pinnapi_reactive_attempts "
                                     "(TIMEOUT / ERROR)",
                           "count": int(ra.get("failed_in_window") or 0),
                           "last_at": _ep(ra.get("last_failed_at")),
                           "last_what": "TIMEOUT_OR_ERROR_ATTEMPT"})
    c["failures"] = (failures_field(window_s, srcs_f, status=UNAVAILABLE,
                                    why="UNREADABLE:%s" % ",".join(
                                        unreadable))
                     if unreadable else failures_field(window_s, srcs_f))
    c["lease"] = {k: spec.get("lease", {}).get(k)
                  for k in ("kind", "parent") if spec.get("lease", {}).get(k)}
    return c, base, hard, soft


def not_started_contract(name: str, *, now: float, window_s: float) -> dict:
    """A venue-write loop the workers register and never start (cand21)."""
    c = skeleton("loop:%s@workers" % name, LOOP, name,
                 role="venue-write loop registered and deliberately NOT "
                      "started (cand21)", process="workers",
                 window_s=window_s)
    c["loop_health"] = {"status": LH.DISABLED,
                        "why": "NOT_STARTED_BY_DESIGN"}
    c["input_freshness"].append(inp("own success record",
                                    "NOT_STARTED_BY_DESIGN",
                                    why="NOT_STARTED_BY_DESIGN"))
    set_next(c, None, basis=None, why="DISABLED", now=now)
    c["refusals"] = refusals_na("NOT_STARTED_BY_DESIGN", window_s)
    c["failures"] = failures_field(window_s, [])
    return finalize(c, base=DISABLED, hard=["DISABLED:NOT_STARTED_BY_DESIGN"],
                    soft=[])


def service_spec(s: dict) -> dict:
    """A dedicated service as a loop_health spec (for loop_health.classify,
    the same verdict rules every loop is judged by)."""
    return {"name": s["name"], "process": "market-plane",
            "cadence_s": s["cadence_s"], "capital_critical": False,
            "lease": {"kind": "OWN_SERVICE",
                      "why": "its own Render service; one instance"},
            "sources": (("service_heartbeats", s["service"],
                         frozenset(s["success"])),),
            "armed": ("always",), "record_every_s": None,
            "period_s": s["cadence_s"], "note": s["role"]}


def service_facts(s: dict, row: dict | None, read: bool) -> dict:
    """loop_health.classify facts from one service_heartbeats row (the
    reader's own service_heartbeats rule: a status outside the writer's
    success vocabulary is a failed pass, never a success)."""
    facts = {"success_at": [], "sources_read": [], "sources_missing": [],
             "process_started_at": None}
    if not read:
        return facts
    label = "service_heartbeats:" + s["service"]
    facts["sources_read"].append(label)
    if not row:
        facts["sources_missing"].append(label + ":NO_ROW")
        return facts
    at, status = _ep(row.get("beat_at")), row.get("status")
    facts["beat_status"] = status
    if status in s["success"]:
        facts["success_at"].append((at, label))
    else:
        facts["error_at"], facts["error"] = at, "NON_SUCCESS_BEAT:%s" % status
    return facts


def service_contract(s: dict, row: dict | None, read: bool, *, now: float,
                     window_s: float) -> dict:
    """ONE DEDICATED SERVICE'S CONTRACT: its service_heartbeats beat judged
    by loop_health.classify (3 x its cadence), its own 'degraded' never
    GREEN, a 'blocked' / 'error' beat a failed pass. Pure."""
    spec = service_spec(s)
    v = LH.classify(spec, service_facts(s, row, read), now=now,
                    is_armed=True)
    if not read:
        v.update(status=LH.UNAVAILABLE, why="SERVICE_HEARTBEATS_UNREADABLE")
    elif not row:
        v.update(status=LH.UNAVAILABLE, why="NO_HEARTBEAT_RECORDED")
    c, base, hard, soft = _loop_parts(
        spec, v, {"lifetime_errors": None, "refusals": refusals_na(
            "MARKET_DATA_ONLY_RECORDS_NO_REFUSALS", window_s)},
        now=now, window_s=window_s, sid="service:%s" % s["name"],
        kind=SERVICE)
    return finalize(c, base=base, hard=hard, soft=soft)


# ═════════════════════════════════════════════════════════════════════
# THE READER (SELECT only; each section in its own savepoint)
# ═════════════════════════════════════════════════════════════════════

AGENT_STATUS_SQL = (
    "SELECT agent_id, state, activity, last_heartbeat_at, "
    "       last_run_started_at, last_run_finished_at, runs, errors, "
    "       left(last_error, 300) AS last_error, cadence "
    "  FROM agent_status")

#: one row per agent: runs in the window, failed (outcome FAILED or a phase /
#: detector error recorded in the run's summary), hung (unfinished after
#: HUNG_RUN_S), the newest failure. agent_runs_agent_idx (agent_id,
#: started_at DESC) bounds it to the window.
AGENT_RUNS_SQL = (
    "SELECT agent_id, count(*) AS runs, "
    "       count(*) FILTER (WHERE failed) AS failed, "
    "       count(*) FILTER (WHERE finished_at IS NULL "
    "                          AND started_at < to_timestamp($2)) AS hung, "
    "       max(coalesce(finished_at, started_at)) FILTER (WHERE failed "
    "           OR (finished_at IS NULL AND started_at < to_timestamp($2))) "
    "         AS last_failed_at, "
    "       (array_agg(what ORDER BY coalesce(finished_at, started_at) DESC) "
    "          FILTER (WHERE failed OR (finished_at IS NULL "
    "                  AND started_at < to_timestamp($2))))[1] "
    "         AS last_failed_what "
    "  FROM (SELECT agent_id, started_at, finished_at, "
    "               (outcome = 'FAILED' "
    "                OR (jsonb_typeof(summary -> 'phase_errors') = 'object' "
    "                    AND summary -> 'phase_errors' <> '{}'::jsonb) "
    "                OR (jsonb_typeof(summary -> 'detector_errors') = "
    "                    'object' AND summary -> 'detector_errors' "
    "                    <> '{}'::jsonb)) AS failed, "
    "               left(coalesce(outcome, 'UNFINISHED') || ' ' || "
    "                    coalesce(nullif(summary -> 'phase_errors', "
    "                                    '{}'::jsonb)::text, "
    "                             nullif(summary -> 'detector_errors', "
    "                                    '{}'::jsonb)::text, ''), 200) "
    "                 AS what "
    "          FROM agent_runs WHERE started_at >= to_timestamp($1)) r "
    " GROUP BY agent_id")

#: each agent's newest FINISHED run, one index probe per agent
LAST_RUN_SQL = (
    "SELECT a.id AS agent_id, r.run_id, r.finished_at, r.outcome "
    "  FROM unnest($1::text[]) AS a(id) "
    "  CROSS JOIN LATERAL (SELECT run_id, finished_at, outcome "
    "                        FROM agent_runs WHERE agent_id = a.id "
    "                         AND finished_at IS NOT NULL "
    "                       ORDER BY started_at DESC LIMIT 1) r")

DEREK_DECISIONS_SQL = (
    "SELECT verdict, refusal, refusals, count(*) AS n "
    "  FROM paper_decisions WHERE decided_at >= to_timestamp($1) "
    " GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT %d" % MAX_GROUPS)

DEREK_EVAL_SQL = (
    "SELECT count(*) AS count, max(at) AS last_at, "
    "       (array_agg(outcome || coalesce(': ' || left(refusal, 120), '') "
    "                  ORDER BY at DESC))[1] AS last_what "
    "  FROM paper_evaluation_attempts "
    " WHERE at >= to_timestamp($1) AND outcome = ANY($2::text[])")

XAVIER_REVIEWS_SQL = (
    "SELECT refusal, count(*) AS n FROM paper_xavier_reviews "
    " WHERE reviewed_at >= to_timestamp($1) "
    " GROUP BY 1 ORDER BY 2 DESC LIMIT %d" % MAX_GROUPS)

XAVIER_NEWEST_SQL = (
    "(SELECT 'paper_xavier_reviews' AS t, review_id AS id, "
    "        reviewed_at AS at, recommendation AS what "
    "   FROM paper_xavier_reviews ORDER BY reviewed_at DESC LIMIT 1) "
    "UNION ALL "
    "(SELECT 'xavier_management_assessments', assessment_id, assessed_at, "
    "        recommendation FROM xavier_management_assessments "
    "  ORDER BY assessed_at DESC LIMIT 1)")

ARCHER_ESTIMATES_SQL = (
    "SELECT recommendation, split_part(coalesce(recommendation_reason, ''), "
    "       ':', 1) AS code, count(*) AS n "
    "  FROM eddie_execution_estimates "
    " WHERE estimated_at >= to_timestamp($1) "
    " GROUP BY 1, 2 ORDER BY 3 DESC LIMIT %d" % MAX_GROUPS)

ADRIANA_REFUSALS_SQL = (
    "SELECT primary_code, count(*) AS n FROM adriana_arb_refusals "
    " WHERE decided_at >= to_timestamp($1) "
    " GROUP BY 1 ORDER BY 2 DESC LIMIT %d" % MAX_GROUPS)

ADRIANA_SCAN_SQL = (
    "SELECT scan_id, finished_at, status, left(why, 200) AS why, "
    "       markets_read, books_fresh FROM adriana_arb_scans "
    " WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1")

ALLOCATOR_SQL = (
    "SELECT (SELECT started_at FROM intel_runs WHERE component = 'ALLOCATOR'"
    "         ORDER BY started_at DESC LIMIT 1) AS newest_started_at, "
    "       (SELECT status FROM intel_runs WHERE component = 'ALLOCATOR' "
    "         ORDER BY started_at DESC LIMIT 1) AS newest_status, "
    "       count(*) FILTER (WHERE status = ANY($2::text[])) AS failed, "
    "       max(coalesce(finished_at, started_at)) FILTER (WHERE status = "
    "           ANY($2::text[])) AS last_failed_at, "
    "       (array_agg(status || coalesce(': ' || left(error, 120), '') "
    "                  ORDER BY started_at DESC) FILTER (WHERE status = "
    "                  ANY($2::text[])))[1] AS last_failed_what "
    "  FROM intel_runs WHERE component = 'ALLOCATOR' "
    "   AND started_at >= to_timestamp($1)")

RUN_TABLE_SQL = (
    "SELECT count(*) FILTER (WHERE status = ANY($2::text[])) AS failed, "
    "       max(coalesce(finished_at, started_at)) FILTER (WHERE status = "
    "           ANY($2::text[])) AS last_failed_at, "
    "       (array_agg(status ORDER BY started_at DESC) FILTER (WHERE "
    "           status = ANY($2::text[])))[1] AS last_failed_what "
    "  FROM %s WHERE started_at >= to_timestamp($1)")

REACTIVE_SQL = (
    "SELECT count(*) FILTER (WHERE state = 'COMPLETED') AS completed, "
    "       count(*) FILTER (WHERE state IN ('TIMEOUT', 'ERROR')) AS failed, "
    "       max(updated_at) FILTER (WHERE state IN ('TIMEOUT', 'ERROR')) "
    "         AS last_failed_at, "
    "       count(*) FILTER (WHERE state = 'STARTED' AND updated_at < "
    "           to_timestamp($2)) AS orphaned "
    "  FROM pinnapi_reactive_attempts WHERE created_at >= to_timestamp($1)")

#: a blocker is {"code", "why"} (shadow_bettor.blockers_for) or a bare code
SHADOW_SQL = (
    "SELECT lane, CASE WHEN jsonb_typeof(blockers) <> 'array' THEN NULL "
    "                  WHEN jsonb_typeof(blockers -> 0) = 'object' "
    "                  THEN blockers -> 0 ->> 'code' "
    "                  ELSE blockers ->> 0 END AS code, count(*) AS n "
    "  FROM shadow_decisions "
    " WHERE lane = ANY($2::text[]) AND decision_ts >= to_timestamp($1) "
    "   AND proposed_action = $3 "
    " GROUP BY 1, 2 ORDER BY 3 DESC LIMIT %d" % MAX_GROUPS)

HEARTBEATS_SQL = "SELECT service, status, beat_at FROM service_heartbeats"

LOOP_COUNTERS_SQL = (
    "SELECT loop_name, process, errors FROM runtime_loop_health")

CYCLE_KEYS_SQL = (
    "SELECT key, value ->> 'at' AS at, value ->> 'state' AS state, "
    "       value ->> 'ran' AS ran, value ->> 'elapsed_s' AS elapsed_s, "
    "       value -> 'servicing_cadence' AS servicing_cadence "
    "  FROM ingestion_state WHERE key = ANY($1::text[]) "
    "   AND jsonb_typeof(value) = 'object'")

#: the run-table loops (loop_health sources ("run_table", t))
RUN_TABLES = tuple(sorted({s[1] for spec in LH.INVENTORY
                           for s in spec["sources"] if s[0] == "run_table"}))


class _Reads:
    """Sections, each in its own savepoint of the caller's READ ONLY
    transaction: an absent table or a failed read is named, never raised,
    never a zero."""

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

    async def run(self, name: str, tables: tuple, fn):
        """(ok, value). ok False when a table is absent or the read failed."""
        for t in tables:
            if not await self.exists(t):
                self.sections[name] = {"status": "ABSENT",
                                       "why": "TABLE_NOT_DEPLOYED:%s" % t}
                return False, None
        try:
            async with self.conn.transaction():
                got = await fn(self.conn)
        except Exception as exc:                                # noqa: BLE001
            self.sections[name] = {"status": "UNAVAILABLE",
                                   "why": type(exc).__name__}
            return False, None
        self.sections[name] = {"status": "OK" if got else "EMPTY",
                               "why": None}
        return True, got


def _decision_counts(rows: list) -> tuple:
    """paper_decisions groups -> ({primary code: n}, decisions_by_class,
    enters, refused). Pure."""
    counts: dict = {}
    by_class: dict = {}
    enters = refused = 0
    for r in rows or []:
        n = int(r["n"] or 0)
        verdict = str(r["verdict"] or "").upper()
        if verdict == RT.ENTER:
            enters += n
            continue
        refused += n
        codes = [x for x in (r["refusals"] or []) if x] or (
            [r["refusal"]] if r["refusal"] else [])
        k = RT.decision_class(verdict, codes)
        by_class[k] = by_class.get(k, 0) + n
        code = RT.normalize(r["refusal"] or (codes[0] if codes else None)) \
            or "NO_CODE_RECORDED"
        counts[code] = counts.get(code, 0) + n
    return counts, by_class, enters, refused


async def read(conn, *, now: float | None = None,
               window_s: float = DEFAULT_WINDOW_S, env=None) -> dict:
    """EVERY SUBJECT'S CONTRACT. SELECT only; the caller holds the READ ONLY
    transaction and its statement timeout."""
    now = float(time.time() if now is None else now)
    window_s = bound_window(window_s)
    since = now - window_s
    rd = _Reads(conn)

    # the R30A loop verdicts (each source in its own savepoint)
    try:
        async with conn.transaction():
            lh = await LH.read(conn, now=now, env=env)
        rd.sections["loop_health"] = {"status": "OK", "why": None}
    except Exception as exc:                                    # noqa: BLE001
        lh = {"loops": [], "sources_missing": []}
        rd.sections["loop_health"] = {"status": "UNAVAILABLE",
                                      "why": type(exc).__name__}
    verdicts = {(lp["name"], lp["process"]): lp for lp in lh.get("loops")
                or []}

    # the work state's market, positions, outputs and allocator reads
    try:
        async with conn.transaction():
            wi = await AWS.read_inputs(conn, now=now)
    except Exception as exc:                                    # noqa: BLE001
        wi = {"market": {}, "positions": {"open": None, "why": type(
            exc).__name__}, "outputs": {}, "allocator_run": None,
            "sections": {"work.inputs": {"status": "UNAVAILABLE",
                                         "why": type(exc).__name__}}}
    for k, v in (wi.get("sections") or {}).items():
        rd.sections[k] = v

    ok_st, st_rows = await rd.run("agent_status", ("agent_status",),
                                  lambda c: c.fetch(AGENT_STATUS_SQL))
    statuses = {r["agent_id"]: dict(r) for r in (st_rows or [])}
    ok_runs, run_rows = await rd.run(
        "agent_runs.window", ("agent_runs",),
        lambda c: c.fetch(AGENT_RUNS_SQL, since, now - HUNG_RUN_S))
    runs = {r["agent_id"]: dict(r) for r in (run_rows or [])}
    ok_lr, lr_rows = await rd.run(
        "agent_runs.newest", ("agent_runs",),
        lambda c: c.fetch(LAST_RUN_SQL, list(AGENT_IDS)))
    last_runs = {r["agent_id"]: dict(r) for r in (lr_rows or [])}
    ok_hb, hb_rows = await rd.run("service_heartbeats",
                                  ("service_heartbeats",),
                                  lambda c: c.fetch(HEARTBEATS_SQL))
    beats = {r["service"]: dict(r) for r in (hb_rows or [])}
    ok_lc, lc_rows = await rd.run("runtime_loop_health.counters",
                                  ("runtime_loop_health",),
                                  lambda c: c.fetch(LOOP_COUNTERS_SQL))
    counters = {(r["loop_name"], r["process"]): {"errors": r["errors"]}
                for r in (lc_rows or [])}
    ok_ck, ck_rows = await rd.run(
        "ingestion_state.cycles", ("ingestion_state",),
        lambda c: c.fetch(CYCLE_KEYS_SQL, ["ext_pinnacle_last_cycle",
                                           "ext_pinnacle_last_servicing"]))
    cyc = {r["key"]: dict(r) for r in (ck_rows or [])}

    # ── refusals per agent ────────────────────────────────────────────
    refusals: dict = {}
    ok, rows = await rd.run("refusals.DEREK", ("paper_decisions",),
                            lambda c: c.fetch(DEREK_DECISIONS_SQL, since))
    if ok:
        counts, by_class, enters, refused = _decision_counts(rows)
        refusals["DEREK"] = refusal_summary(
            counts, window_s=window_s, source="paper_decisions (REFUSE, "
            "primary code; class per decision by refusal_taxonomy."
            "decision_class)",
            software=by_class.get(RT.REJECTED_SOFTWARE, 0),
            unclassified=by_class.get(RT.REJECTED_UNCLASSIFIED, 0),
            extra={"decisions_by_class": by_class, "enter": enters,
                   "refuse": refused})
    else:
        refusals["DEREK"] = refusals_unavailable(
            (rd.sections.get("refusals.DEREK") or {}).get("why"), window_s,
            "paper_decisions")
    ok, rows = await rd.run("refusals.XAVIER", ("paper_xavier_reviews",),
                            lambda c: c.fetch(XAVIER_REVIEWS_SQL, since))
    if ok:
        reviews = sum(int(r["n"]) for r in rows or [])
        counts = {RT.normalize(r["refusal"]) or str(r["refusal"]):
                  int(r["n"]) for r in rows or [] if r["refusal"]}
        refusals["XAVIER"] = refusal_summary(
            counts, window_s=window_s,
            source="paper_xavier_reviews.refusal",
            extra={"reviews": reviews})
    else:
        refusals["XAVIER"] = refusals_unavailable(
            (rd.sections.get("refusals.XAVIER") or {}).get("why"), window_s,
            "paper_xavier_reviews")
    ok, rows = await rd.run("refusals.ARCHER", ("eddie_execution_estimates",),
                            lambda c: c.fetch(ARCHER_ESTIMATES_SQL, since))
    if ok:
        counts: dict = {}
        by_rec: dict = {}
        for r in rows or []:
            by_rec[r["recommendation"]] = by_rec.get(
                r["recommendation"], 0) + int(r["n"])
            if r["recommendation"] in ARCHER_NOT_EXECUTING:
                code = RT.normalize(r["code"]) or "NO_CODE_RECORDED"
                counts[code] = counts.get(code, 0) + int(r["n"])
        refusals["ARCHER"] = refusal_summary(
            counts, window_s=window_s,
            source="eddie_execution_estimates (WAIT / SKIP_EXECUTION, "
                   "reason code)", extra={"by_recommendation": by_rec})
    else:
        refusals["ARCHER"] = refusals_unavailable(
            (rd.sections.get("refusals.ARCHER") or {}).get("why"), window_s,
            "eddie_execution_estimates")
    ok, rows = await rd.run("refusals.ADRIANA", ("adriana_arb_refusals",),
                            lambda c: c.fetch(ADRIANA_REFUSALS_SQL, since))
    if ok:
        refusals["ADRIANA"] = refusal_summary(
            {r["primary_code"]: int(r["n"]) for r in rows or []},
            window_s=window_s, source="adriana_arb_refusals.primary_code")
    else:
        refusals["ADRIANA"] = refusals_unavailable(
            (rd.sections.get("refusals.ADRIANA") or {}).get("why"), window_s,
            "adriana_arb_refusals")

    ok_ev, ev_row = await rd.run(
        "failures.DEREK.evaluation_attempts", ("paper_evaluation_attempts",),
        lambda c: c.fetchrow(DEREK_EVAL_SQL, since,
                             list(EVAL_FAILED_OUTCOMES)))
    ok_xn, xn_rows = await rd.run(
        "actions.XAVIER", ("paper_xavier_reviews",
                           "xavier_management_assessments"),
        lambda c: c.fetch(XAVIER_NEWEST_SQL))
    ok_sc, scan = await rd.run("census.ADRIANA", ("adriana_arb_scans",),
                               lambda c: c.fetchrow(ADRIANA_SCAN_SQL))
    ok_al, al = await rd.run(
        "heartbeat.CHIEF_ALLOCATOR", ("intel_runs",),
        lambda c: c.fetchrow(ALLOCATOR_SQL, since,
                             list(RUN_FAILED_STATUSES)))

    # ── the loops' windowed facts ─────────────────────────────────────
    run_failures: dict = {}
    for t in RUN_TABLES:
        ok, row = await rd.run(
            "failures.%s" % t, (t,),
            lambda c, t=t: c.fetchrow(RUN_TABLE_SQL % t, since,
                                      list(RUN_FAILED_STATUSES)))
        run_failures[t] = dict(row) if ok and row is not None else None
    ok_ra, ra_row = await rd.run(
        "failures.pinnapi_reactive_attempts", ("pinnapi_reactive_attempts",),
        lambda c: c.fetchrow(REACTIVE_SQL, since, now - 60.0))
    ok_sd, sd_rows = await rd.run(
        "refusals.shadow_lanes", ("shadow_decisions",),
        lambda c: c.fetch(SHADOW_SQL, since, list(SHADOW_LANES.values()),
                          NO_TRADE))

    # ── ASSEMBLE ──────────────────────────────────────────────────────
    subjects = []
    outputs = wi.get("outputs") or {}
    for spec in AGENT_SPECS:
        aid = spec["id"]
        extra_actions = []
        if aid == "XAVIER" and ok_xn:
            for r in xn_rows or []:
                extra_actions.append({"what": "%s %s" % (
                    "review" if r["t"] == "paper_xavier_reviews"
                    else "assessment", r["what"]), "at": r["at"],
                    "source": "%s:%s" % (r["t"], r["id"])})
        if aid == "CHIEF_ALLOCATOR" and (wi.get("allocator_run") or {}).get(
                "in_progress"):
            ar = wi["allocator_run"]
            extra_actions.append({"what": ar.get("activity"),
                                  "at": ar.get("started_at"),
                                  "source": "intel_runs:%s" % ar.get("id")})
        aliases = []
        for alias in spec.get("aliases") or ():
            row = statuses.get(alias)
            beat = beats.get("agent_%s" % alias.lower())
            if row or beat:
                aliases.append({
                    "alias": alias, "canonical": aid,
                    "note": "historical alias (migration 266): its rows are "
                            "append-only history under the old name, never "
                            "a live agent",
                    "agent_status_heartbeat_age_s": _age(
                        now, (row or {}).get("last_heartbeat_at")),
                    "service_heartbeat_age_s": _age(
                        now, (beat or {}).get("beat_at"))})
        al_facts = None
        if spec.get("heartbeat") == "INTEL_ALLOCATOR":
            al_facts = dict(al or {}, read=bool(ok_al))
        f = {"status": statuses.get(aid), "status_read": ok_st,
             "verdicts": verdicts, "market": wi.get("market"),
             "positions": wi.get("positions"),
             "outputs": outputs.get(aid) or [],
             "runs": (runs.get(aid) or {"failed": 0, "hung": 0})
             if ok_runs else None,
             "last_run": last_runs.get(aid) if ok_lr else None,
             "refusals": refusals.get(aid),
             "eval": dict(ev_row) if ok_ev and ev_row is not None
             else ({"count": 0} if ok_ev else None),
             "loop_errors": counters, "allocator": al_facts,
             "ext_cycle": _cycle(cyc.get("ext_pinnacle_last_cycle")),
             "servicing": {"cadence": _j((cyc.get(
                 "ext_pinnacle_last_servicing") or {}).get(
                     "servicing_cadence")) or {}},
             "census": dict(scan) if ok_sc and scan is not None else None,
             "sections": rd.sections, "extra_actions": extra_actions,
             "aliases": aliases}
        subjects.append(agent_contract(spec, f, now=now, window_s=window_s))

    shadow: dict = {}
    for r in sd_rows or []:
        code = RT.normalize(r["code"]) or "NO_BLOCKER_RECORDED"
        lane = shadow.setdefault(r["lane"], {})
        lane[code] = lane.get(code, 0) + int(r["n"])
    for spec in LH.INVENTORY:
        key = (spec["name"], spec["process"])
        rt = next((s[1] for s in spec["sources"] if s[0] == "run_table"),
                  None)
        lane = SHADOW_LANES.get(spec["name"]) if spec["process"] == \
            "workers" else None
        f = {"lifetime_errors": (counters.get(key) or {}).get("errors")
             if ok_lc else None,
             "run_failures": run_failures.get(rt) if rt else None,
             "reactive": None, "refusals": None,
             "parent": verdicts.get((spec["lease"].get("parent"),
                                     spec["process"]))
             if spec["lease"].get("parent") else None}
        if spec["name"] == "pinnapi_reactive" and ok_ra:
            ra = dict(ra_row or {})
            f["reactive"] = {
                "completed_in_window": int(ra.get("completed") or 0),
                "failed_in_window": int(ra.get("failed") or 0),
                "last_failed_at": ra.get("last_failed_at"),
                "orphaned_started": int(ra.get("orphaned") or 0)}
        if lane is not None:
            f["refusals"] = (refusal_summary(
                shadow.get(lane) or {}, window_s=window_s,
                source="shadow_decisions lane %s (NO_TRADE, first blocker)"
                       % lane) if ok_sd else refusals_unavailable(
                (rd.sections.get("refusals.shadow_lanes") or {}).get("why"),
                window_s, "shadow_decisions"))
        sp = dict(spec, run_table=rt)
        subjects.append(loop_contract(sp, verdicts.get(key), f, now=now,
                                      window_s=window_s))
    for name in LH.WORKERS_NOT_STARTED:
        subjects.append(not_started_contract(name, now=now,
                                             window_s=window_s))
    for s in SERVICE_SPECS:
        subjects.append(service_contract(s, beats.get(s["service"]),
                                         bool(ok_hb), now=now,
                                         window_s=window_s))

    summary: dict = {"subjects": len(subjects), "by_status": {},
                     "by_kind": {}, "green": 0, "missing_fields": 0}
    for c in subjects:
        summary["by_status"][c["status"]] = summary["by_status"].get(
            c["status"], 0) + 1
        k = summary["by_kind"].setdefault(c["kind"], {})
        k[c["status"]] = k.get(c["status"], 0) + 1
        summary["green"] += c["status"] == GREEN
        summary["missing_fields"] += len(c["missing_fields"])
    summary["agents_not_green"] = [c["id"] for c in subjects
                                   if c["kind"] == AGENT
                                   and c["status"] != GREEN]
    return {"version": VERSION, "authority": AUTHORITY, "now": now,
            "window_s": window_s, "statuses": list(STATUSES),
            "rules": RULES, "subjects": subjects, "summary": summary,
            "sections": rd.sections,
            "loop_health_sources_missing": lh.get("sources_missing") or [],
            "historical_aliases": dict(HISTORICAL_ALIASES)}


def _cycle(row: dict | None) -> dict:
    r = row or {}
    ran = r.get("ran")
    if isinstance(ran, str):
        ran = {"true": True, "false": False}.get(ran.strip().lower())
    return {"at": r.get("at"), "state": r.get("state"), "ran": ran,
            "elapsed_s": r.get("elapsed_s")}


RULES = {
    GREEN: "alive (heartbeat inside its bound, newest pass a success), every "
           "input fresh, no failure and no SOFTWARE / UNCLASSIFIED refusal "
           "in the window, every contract field present",
    DEGRADED: "alive, and an input not fresh, a failure in the window, a "
              "SOFTWARE or UNCLASSIFIED refusal in the window, the subject's "
              "own word that it waits / is blocked / degraded, or a missing "
              "field",
    FAILED: "down or erroring: heartbeat older than its bound, self-reported "
            "FAILED, newest run / pass a failure, or the R30A loop verdict "
            "UNHEALTHY",
    UNKNOWN: "no heartbeat, unreadable, never ran, no success yet since "
             "start, health unavailable, or no event in the window -- never "
             "green by default",
    DISABLED: "not armed in this deployment, or registered and not started "
              "by design (cand21) -- never green",
}
