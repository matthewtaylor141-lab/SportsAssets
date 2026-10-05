"""THE AGENTS' OPERATIONAL WORK STATES (owner R30, 2026-10-04).

    "Xavier is NOT idle when he owns open positions. ... No fake 'working'
     state. State must reflect actual recorded work or blockers."

THE SIX STATES (one per agent, derived only from recorded rows):

  WORKING                     a run is recorded in progress, or the agent
                              recorded an output inside ACTIVE_WINDOW_S
                              (producers: Derek, Archer, Scout, the Chief
                              Allocator)
  REVIEWING                   the same evidence for a reviewing role
                              (Xavier, Audrey, Karen); for Xavier also: every
                              open position's ONE current review is CURRENT
                              (fresh and inside its own freshness window)
  WAITING_FOR_FRESH_EVIDENCE  recorded open work that cannot proceed until
                              newer evidence arrives (Xavier: a position whose
                              current review is not CURRENT; Scout: features
                              under forward test awaiting samples; any agent:
                              its own runner recorded WAITING_FOR_EVIDENCE /
                              WAITING_FOR_PROVIDER on a fresh heartbeat)
  BLOCKED_ON_MARKET_DATA      the market data the agent's open work requires
                              is failing or stale in the recorded reads: the
                              PinnAPI feed telemetry (ingestion_state
                              'pinnapi_feed_last': stale, or not OWNER_SYNCED)
                              and the venue book reads (paper_book_
                              observations: the recent reads mostly errors;
                              for one of Xavier's positions, its latest book
                              absent, unreadable or older than
                              MARKET_DATA_MAX_AGE_S). Only agents whose work
                              reads market data can be blocked on it: Xavier,
                              Derek, Archer. Karen, Audrey, Scout and the Chief
                              Allocator read no live market data, so this
                              state is never theirs (tested).
  HANDOFF_PENDING             work handed to the agent, recorded, and not yet
                              picked up (the predicates per agent below)
  IDLE_NO_OPEN_WORK           none of the above. INVALID for Xavier while he
                              owns any open / unresolved position: enforced
                              in `derive` (a guard that can only move away
                              from IDLE) and by `invariant_holds` (tests).

PRECEDENCE (the first that holds): a run in progress (WORKING / REVIEWING)
-> BLOCKED_ON_MARKET_DATA -> HANDOFF_PENDING -> WAITING_FOR_FRESH_EVIDENCE
-> a recorded output inside the window (WORKING / REVIEWING) ->
IDLE_NO_OPEN_WORK. Xavier's own order, position facts first: run in progress
-> unreviewed positions (HANDOFF_PENDING) -> positions blocked on market data
-> positions waiting for fresh evidence -> other hand-offs -> every position
CURRENT (REVIEWING) -> IDLE only with no open position at all.

THE HAND-OFF PREDICATES, PER AGENT (each a recorded row, named in `basis`):
  every agent     agent_tasks assigned to it with status OPEN (not yet
                  IN_PROGRESS)
  DEREK, XAVIER,  karen_challenges against it still OPEN (not yet answered)
  AUDREY, CHIEF_ALLOCATOR
  AUDREY / XAVIER karen_challenges RESPONDED by a target they evaluate
                  (EVALUATOR_FOR, = agents.karen.EVALUATOR_FOR) and not yet
                  resolved
  XAVIER          an open position (net filled quantity, not settled; ACTUAL:
                  smalllive_handoffs OPEN) with NO review recorded
  ARCHER           paper ENTER decisions inside his lookback (ARCHER_LOOKBACK_S
                  = agents.archer_runner.LOOKBACK_S) with no execution estimate
  CHIEF_ALLOCATOR paper ENTER decisions recorded after her latest allocation
                  run started (that run could not have ranked them)

Xavier's per-position facts read the ONE current review per position from
`xavier_current_review` (migration 226; the newest review per group when the
migration is not applied) and judge it at read time with
`xavier_freshness.of_review` / `of_assessment`.

WHAT THIS MODULE CANNOT DO. It imports only the standard library and
`xavier_freshness` (pure). Its SQL is SELECT only (tests/test_agent_work_
state_authority.py). It holds no write, order, approval or capital path.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from . import xavier_freshness as XF

VERSION = "AGENT_WORK_STATE_V1"

WORKING = "WORKING"
REVIEWING = "REVIEWING"
WAITING = "WAITING_FOR_FRESH_EVIDENCE"
BLOCKED = "BLOCKED_ON_MARKET_DATA"
HANDOFF = "HANDOFF_PENDING"
IDLE = "IDLE_NO_OPEN_WORK"
WORK_STATES = (WORKING, REVIEWING, WAITING, BLOCKED, HANDOFF, IDLE)

AGENTS = ("DEREK", "KAREN", "SCOUT", "ARCHER", "CHIEF_ALLOCATOR", "AUDREY",
          "XAVIER", "ADRIANA")
BUSY = {"XAVIER": REVIEWING, "AUDREY": REVIEWING, "KAREN": REVIEWING}
#: the agents whose open work reads live market data, and which data
MARKET_SOURCES = {"XAVIER": ("feed", "venue"), "DEREK": ("feed", "venue"),
                  "ARCHER": ("venue",), "ADRIANA": ("venue",)}
#: = agents.karen.EVALUATOR_FOR (read, not imported; a test pins equality)
EVALUATOR_FOR = {"DEREK": "AUDREY", "XAVIER": "AUDREY",
                 "CHIEF_ALLOCATOR": "AUDREY", "AUDREY": "XAVIER"}
CHALLENGE_TARGETS = tuple(EVALUATOR_FOR)

ACTIVE_WINDOW_S = 300.0          # = api.command_floor.ACTIVE_WINDOW_S
RUN_WINDOW_FLOOR_S = 600.0       # = api.command_floor.RUN_WINDOW_FLOOR_S
STALE_FLOOR_S = 900.0            # = api.command_floor.STALE_FLOOR_S
#: a held position's venue book older than this is stale for its management
#: (the same 300 s bound the position rooms apply to game state and
#: bettor_fixture_store to a fixture row; a display bound, no trading rule)
MARKET_DATA_MAX_AGE_S = 300.0
#: the venue reads judged together: the newest VENUE_RECENT_READS reads
#: inside VENUE_WINDOW_S; failing when at least VENUE_MIN_READS were made and
#: at least VENUE_FAIL_RATIO of them failed
VENUE_RECENT_READS = 50
VENUE_WINDOW_S = 600.0
VENUE_MIN_READS = 3
VENUE_FAIL_RATIO = 0.5
#: the PinnAPI feed's telemetry: = pinnapi_feed_runtime.HEARTBEAT_S (read,
#: not imported); stale after 3 beats, as heartbeat_view judges it
FEED_HEARTBEAT_KEY = "pinnapi_feed_last"
FEED_HEARTBEAT_S = 30.0
FEED_OK_STATES = ("OWNER_SYNCED",)
WAITING_STATUSES = ("WAITING_FOR_EVIDENCE", "WAITING_FOR_PROVIDER")
ARCHER_LOOKBACK_S = 2 * 86400.0   # = agents.archer_runner.LOOKBACK_S
MAX_POSITIONS = 500
SAMPLE_IDS = 5

# position classes (Xavier)
P_UNREVIEWED, P_CURRENT, P_BLOCKED, P_WAITING = (
    "UNREVIEWED", "CURRENT", "BLOCKED_ON_MARKET_DATA",
    "WAITING_FOR_FRESH_EVIDENCE")


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
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def run_in_progress(status: dict | None, now: float,
                    window_s: float = RUN_WINDOW_FLOOR_S) -> bool:
    """A run started after the last finish, recently, while EVALUATING
    (the floor's own rule). Pure."""
    s = status or {}
    started = _ep(s.get("last_run_started_at"))
    finished = _ep(s.get("last_run_finished_at"))
    if started is None or s.get("state") != "EVALUATING":
        return False
    if finished is not None and finished >= started:
        return False
    return 0 <= now - started <= max(window_s, RUN_WINDOW_FLOOR_S)


def market_status(market: dict | None, now: float) -> dict:
    """THE RECORDED MARKET-DATA HEALTH. Pure. {"feed": {...}, "venue":
    {...}}, each with `blocked` (bool) and `why`. Nothing recorded is NOT a
    blocker (no claim without a row): `blocked` False, `recorded` False."""
    m = market or {}
    f = m.get("feed")
    if not isinstance(f, dict) or not f.get("recorded"):
        feed = {"recorded": False, "blocked": False,
                "why": "FEED_TELEMETRY_NOT_RECORDED"}
    else:
        beat = _ep(f.get("beat_at"))
        age = None if beat is None else round(now - beat, 1)
        state = f.get("state")
        if age is None or not 0 <= age <= 3 * FEED_HEARTBEAT_S:
            why = "FEED_TELEMETRY_STALE"
        elif state not in FEED_OK_STATES:
            why = "FEED_%s" % (state or "STATE_UNKNOWN")
        else:
            why = None
        feed = {"recorded": True, "state": state, "age_s": age,
                "blocked": why is not None, "why": why}
    v = m.get("venue")
    if not isinstance(v, dict) or not v.get("recorded"):
        venue = {"recorded": False, "blocked": False,
                 "why": "NO_VENUE_BOOK_READ_RECORDED"}
    else:
        reads = [r for r in v.get("reads") or []
                 if _ep(r.get("at")) is not None
                 and 0 <= now - _ep(r["at"]) <= VENUE_WINDOW_S]
        errs = sum(1 for r in reads if r.get("error"))
        ok_at = max([_ep(r["at"]) for r in reads if not r.get("error")]
                    or [None], key=lambda x: x or 0)
        failing = (len(reads) >= VENUE_MIN_READS
                   and errs >= VENUE_FAIL_RATIO * len(reads))
        venue = {"recorded": True, "recent_reads": len(reads),
                 "recent_errors": errs, "newest_ok_at": ok_at,
                 "blocked": failing,
                 "why": "VENUE_BOOK_READS_FAILING" if failing else None}
    return {"feed": feed, "venue": venue}


def position_class(p: dict, *, market: dict, now: float) -> tuple:
    """ONE OPEN POSITION'S STATE for Xavier, from its current review, its
    latest venue book and the feed. Pure. (class, why)."""
    cr = p.get("current_review")
    if not cr:
        return P_UNREVIEWED, "NO_REVIEW_RECORDED"
    if cr.get("management_state") == XF.S_CURRENT:
        return P_CURRENT, "CURRENT_REVIEW_INSIDE_ITS_FRESHNESS_WINDOW"
    if market["feed"].get("blocked"):
        return P_BLOCKED, market["feed"]["why"]
    b = p.get("book")
    if p.get("position_kind", "PAPER") == "PAPER":
        if not b:
            return P_BLOCKED, "NO_VENUE_BOOK_OBSERVED"
        if b.get("error"):
            return P_BLOCKED, "LATEST_VENUE_BOOK_UNREADABLE"
        at = _ep(b.get("observed_at"))
        if at is None or now - at > MARKET_DATA_MAX_AGE_S:
            return P_BLOCKED, "VENUE_BOOK_OLDER_THAN_%dS" % int(
                MARKET_DATA_MAX_AGE_S)
    return P_WAITING, "CURRENT_REVIEW_IS_%s" % (
        cr.get("recommendation_state") or cr.get("management_state")
        or "NOT_CURRENT")


def _out(state, detail, basis, since=None, **kw) -> dict:
    return dict({"state": state, "detail": detail, "basis": basis,
                 "since": since, "version": VERSION}, **kw)


def _handoffs(facts: dict) -> list:
    return [h for h in facts.get("handoffs") or []
            if int(h.get("count") or 0) > 0]


def _handoff_out(hs: list, *, prefix: str = "") -> dict:
    total = sum(int(h["count"]) for h in hs)
    oldest = min([_ep(h.get("oldest_at")) for h in hs
                  if _ep(h.get("oldest_at")) is not None] or [None],
                 key=lambda x: x or 0)
    return _out(HANDOFF, prefix + "%d handed over, not yet picked up (%s)" % (
        total, ", ".join("%s %d" % (h["kind"], h["count"]) for h in hs)),
        [dict(h) for h in hs], since=oldest)


def _status_waiting(facts: dict, now: float) -> dict | None:
    st = facts.get("status") or {}
    hb = _ep(st.get("heartbeat_at"))
    stale_s = float(facts.get("stale_s") or STALE_FLOOR_S)
    if st.get("state") in WAITING_STATUSES and hb is not None and \
            0 <= now - hb <= stale_s:
        return {"kind": "AGENT_STATUS_%s" % st["state"],
                "table": "agent_status", "id": facts.get("agent"),
                "activity": st.get("activity"), "at": hb}
    return None


def _recent_output(facts: dict, now: float) -> dict | None:
    outs = sorted([o for o in facts.get("outputs") or []
                   if _ep(o.get("at")) is not None
                   and 0 <= now - _ep(o["at"]) <= ACTIVE_WINDOW_S],
                  key=lambda o: -_ep(o["at"]))
    return outs[0] if outs else None


def _run(facts: dict, now: float) -> dict | None:
    r = facts.get("run")
    if isinstance(r, dict):
        return r if r.get("in_progress") else None
    st = facts.get("status") or {}
    if run_in_progress(st, now, float(facts.get("stale_s")
                                      or STALE_FLOOR_S)):
        return {"in_progress": True, "table": "agent_status",
                "id": facts.get("agent"),
                "started_at": _ep(st.get("last_run_started_at")),
                "activity": st.get("activity")}
    return None


# ═════════════════════════════════════════════════════════════════════
# THE DERIVATION
# ═════════════════════════════════════════════════════════════════════

def invariant_holds(agent: str, facts: dict, state) -> bool:
    """XAVIER WITH ANY OPEN / UNRESOLVED POSITION IS NEVER IDLE. Pure."""
    if agent != "XAVIER" or state != IDLE:
        return True
    n = facts.get("open_positions")
    return not ((n is None or int(n) > 0) or (facts.get("positions") or []))


def derive(agent: str, facts: dict, *, now: float | None = None) -> dict:
    """THE WORK STATE OF ONE AGENT from its recorded facts. Pure.

    Returns {"state" (one of WORK_STATES, or None when the facts it needs
    could not be read), "detail", "basis" [rows it rests on], "since",
    "counts" (Xavier: positions per class), "version"}."""
    now = float(now if now is not None else time.time())
    facts = dict(facts or {}, agent=agent)
    out = _xavier(facts, now) if agent == "XAVIER" else _generic(
        agent, facts, now)
    if not invariant_holds(agent, facts, out["state"]):
        # unreachable by construction (an open position is unreviewed,
        # blocked, waiting or current); kept so no fact shape can ever
        # yield IDLE for an owner of open positions
        out = _out(WAITING, "Owns open positions; no review state could be "
                   "classified (guard)", out["basis"],
                   counts=out.get("counts"), guard="XAVIER_NEVER_IDLE")
    return out


def _generic(agent: str, facts: dict, now: float) -> dict:
    busy = BUSY.get(agent, WORKING)
    run = _run(facts, now)
    if run:
        return _out(busy, str(run.get("activity") or "Run in progress"),
                    [run], since=_ep(run.get("started_at")))
    hs = _handoffs(facts)
    srcs = MARKET_SOURCES.get(agent)
    # Derek's and Adriana's standing work always reads the books (Derek's
    # the feed too); Archer's only when decisions are waiting for his estimate
    market_work = agent in ("DEREK", "ADRIANA") or (agent == "ARCHER" and any(
        h["kind"] == "ENTER_DECISION_WITHOUT_ESTIMATE" for h in hs))
    if srcs and market_work:
        ms = market_status(facts.get("market"), now)
        bad = [dict(ms[s], source=s) for s in srcs if ms[s].get("blocked")]
        if bad:
            return _out(BLOCKED, "Market data failing: " + ", ".join(
                b["why"] for b in bad), [dict(b, table=(
                    "ingestion_state:%s" % FEED_HEARTBEAT_KEY
                    if b["source"] == "feed" else "paper_book_observations"))
                    for b in bad])
    if hs:
        return _handoff_out(hs)
    waits = [w for w in facts.get("waiting") or []
             if int(w.get("count") or 0) > 0]
    sw = _status_waiting(facts, now)
    if waits or sw:
        parts = ["%s %d" % (w["kind"], w["count"]) for w in waits]
        if sw:
            parts.append(str(sw.get("activity") or sw["kind"]))
        return _out(WAITING, "Waiting for fresh evidence: " + "; ".join(
            parts), [dict(w) for w in waits] + ([sw] if sw else []),
            since=_ep((sw or {}).get("at")))
    o = _recent_output(facts, now)
    if o:
        return _out(busy, "Recorded %s" % (o.get("label") or o.get("table")),
                    [dict(o)], since=_ep(o["at"]))
    return _out(IDLE, "No open work recorded", [{
        "table": "agent_status", "id": agent, "why": "NO_RUN_IN_PROGRESS_"
        "NO_HANDOFF_NO_WAITING_NO_RECENT_OUTPUT"}])


def _xavier(facts: dict, now: float) -> dict:
    n_open = facts.get("open_positions")
    positions = list(facts.get("positions") or [])
    if n_open is None:
        return _out(None, "Open positions could not be read; never taken as "
                    "zero", [{"table": "paper_fills", "why":
                              facts.get("positions_why") or "UNREADABLE"}])
    ms = market_status(facts.get("market"), now)
    classes: dict = {P_UNREVIEWED: [], P_CURRENT: [], P_BLOCKED: [],
                     P_WAITING: []}
    for p in positions:
        c, why = position_class(p, market=ms, now=now)
        classes[c].append(dict(p, why=why))
    counts = {"open_positions": int(n_open),
              "classified": len(positions),
              **{k: len(v) for k, v in classes.items()}}
    reqs = {}
    not_enqueued = 0
    for p in classes[P_WAITING] + classes[P_BLOCKED]:
        op = (p.get("requests") or {})
        for k in op:
            reqs[k] = reqs.get(k, 0) + 1
        not_enqueued += 0 if op else 1
    counts["open_requests_by_kind"] = reqs
    counts["non_current_without_open_request"] = not_enqueued

    def ids(lst):
        return [{"position_kind": p.get("position_kind", "PAPER"),
                 "group_id": p.get("group_id"),
                 "review_id": (p.get("current_review") or {}).get(
                     "review_id"), "why": p.get("why")}
                for p in lst[:SAMPLE_IDS]]

    run = _run(facts, now)
    if run:
        return _out(REVIEWING, "%s · %d open positions" % (
            run.get("activity") or "Review run in progress", n_open),
            [run], since=_ep(run.get("started_at")), counts=counts)
    if classes[P_UNREVIEWED]:
        lst = classes[P_UNREVIEWED]
        oldest = min([_ep(p.get("first_fill_at")) for p in lst
                      if _ep(p.get("first_fill_at")) is not None]
                     or [None], key=lambda x: x or 0)
        return _out(HANDOFF, "%d of %d open positions handed over with no "
                    "review yet" % (len(lst), n_open),
                    [{"table": "paper_fills / smalllive_handoffs",
                      "kind": "OPEN_POSITION_WITHOUT_REVIEW",
                      "count": len(lst), "positions": ids(lst)}],
                    since=oldest, counts=counts)
    if classes[P_BLOCKED]:
        lst = classes[P_BLOCKED]
        whys = sorted({p["why"] for p in lst})
        return _out(BLOCKED, "%d of %d open positions blocked on market data "
                    "(%s)" % (len(lst), n_open, ", ".join(whys)),
                    [{"table": "paper_book_observations / ingestion_state:%s"
                      % FEED_HEARTBEAT_KEY, "kind": "POSITION_MARKET_DATA",
                      "count": len(lst), "positions": ids(lst),
                      "feed": ms["feed"]}], counts=counts)
    if classes[P_WAITING]:
        lst = classes[P_WAITING]
        since = min([_ep((p.get("current_review") or {}).get("reviewed_at"))
                     for p in lst] or [None], key=lambda x: x or 0)
        return _out(WAITING, "%d of %d open positions waiting for fresh "
                    "evidence; %d acquisition requests open%s" % (
                        len(lst), n_open, sum(reqs.values()),
                        (", %d positions with none open" % not_enqueued)
                        if not_enqueued else ""),
                    [{"table": "xavier_current_review",
                      "kind": "CURRENT_REVIEW_NOT_CURRENT",
                      "count": len(lst), "positions": ids(lst)},
                     {"table": "agent_work_open", "kind": "OPEN_REQUESTS",
                      "by_kind": reqs}], since=since, counts=counts)
    hs = _handoffs(facts)
    if hs:
        return dict(_handoff_out(hs), counts=counts)
    if classes[P_CURRENT]:
        lst = classes[P_CURRENT]
        return _out(REVIEWING, "Managing %d open positions on CURRENT "
                    "reviews" % n_open,
                    [{"table": "xavier_current_review",
                      "kind": "CURRENT_REVIEW_INSIDE_FRESHNESS_WINDOW",
                      "count": len(lst), "positions": ids(lst)}],
                    since=max([_ep((p.get("current_review") or {}).get(
                        "reviewed_at")) or 0 for p in lst]), counts=counts)
    if int(n_open) > 0:
        return _out(WAITING, "%d open positions beyond the %d classified; "
                    "none is idle" % (n_open, len(positions)),
                    [{"table": "paper_fills", "kind": "OPEN_POSITIONS",
                      "count": int(n_open)}], counts=counts)
    sw = _status_waiting(facts, now)
    if sw:
        return _out(WAITING, "Waiting for fresh evidence: %s" % (
            sw.get("activity") or sw["kind"]), [sw], since=sw["at"],
            counts=counts)
    return _out(IDLE, "No open position, no hand-off recorded",
                [{"table": "paper_fills / smalllive_handoffs",
                  "kind": "OPEN_POSITIONS", "count": 0}], counts=counts)


# ═════════════════════════════════════════════════════════════════════
# THE RECORDED FACTS (SELECT only; each section in its own savepoint)
# ═════════════════════════════════════════════════════════════════════

class _Sections:
    def __init__(self, conn):
        self.conn = conn
        self.status: dict = {}
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
                self.status[name] = {"status": "ABSENT",
                                     "why": "TABLE_NOT_DEPLOYED:%s" % t}
                return default
        try:
            async with self.conn.transaction():
                got = await fn(self.conn)
        except Exception as exc:                                # noqa: BLE001
            self.status[name] = {"status": "UNAVAILABLE",
                                 "why": type(exc).__name__}
            return default
        self.status[name] = {"status": "OK" if got else "EMPTY",
                             "why": None}
        return got


def _stale_s(cadence) -> float:
    c = _j(cadence)
    vals = [float(v) for k, v in (c or {}).items()
            if str(k).endswith("_s") and _ep(v) and float(v) > 0] \
        if isinstance(c, dict) else []
    return max(STALE_FLOOR_S, 3.0 * max(vals)) if vals else STALE_FLOOR_S


async def _read_status(s: _Sections) -> dict:
    async def fn(conn):
        return {r["agent_id"]: {
            "state": r["state"], "activity": r["activity"],
            "heartbeat_at": _ep(r["last_heartbeat_at"]),
            "last_run_started_at": _ep(r["last_run_started_at"]),
            "last_run_finished_at": _ep(r["last_run_finished_at"]),
            "stale_s": _stale_s(r["cadence"])} for r in await conn.fetch(
                "SELECT agent_id, state, activity, last_heartbeat_at, "
                "       last_run_started_at, last_run_finished_at, cadence "
                "  FROM agent_status")}
    return await s.run("work.agent_status", ("agent_status",), fn,
                       default={}) or {}


async def _read_market(s: _Sections) -> dict:
    async def feed(conn):
        v = _j(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1",
            FEED_HEARTBEAT_KEY))
        if not isinstance(v, dict):
            return {"recorded": False}
        return {"recorded": True, "state": v.get("state"),
                "beat_at": _ep(v.get("beat_at"))}

    async def venue(conn):
        rows = await conn.fetch(
            "SELECT obs_id, observed_at, error IS NOT NULL AS error "
            "  FROM paper_book_observations ORDER BY obs_id DESC LIMIT $1",
            VENUE_RECENT_READS)
        if not rows:
            return {"recorded": False}
        return {"recorded": True,
                "reads": [{"obs_id": r["obs_id"],
                           "at": _ep(r["observed_at"]),
                           "error": bool(r["error"])} for r in rows]}
    return {"feed": await s.run("work.feed", ("ingestion_state",), feed)
            or {"recorded": False},
            "venue": await s.run("work.venue_books",
                                 ("paper_book_observations",), venue)
            or {"recorded": False}}


async def _read_positions(s: _Sections, now: float) -> dict:
    """Xavier's open positions with their ONE current review, latest book
    and open fresh-evidence requests. {"open": int|None, "positions": [..],
    "why"}."""
    out: dict = {"open": None, "positions": [], "why": None}

    async def paper(conn):
        rows = await conn.fetch(
            "SELECT f.group_id, f.us_market_slug, min(f.filled_at) AS ffa "
            "  FROM paper_fills f WHERE NOT EXISTS ("
            "       SELECT 1 FROM paper_settlements x "
            "        WHERE x.group_id = f.group_id "
            "          AND x.us_market_slug = f.us_market_slug) "
            " GROUP BY f.group_id, f.us_market_slug "
            "HAVING sum(CASE WHEN f.direction = 'BUY' THEN f.qty "
            "                ELSE -f.qty END) > 1e-9 "
            " ORDER BY min(f.filled_at), f.group_id LIMIT $1",
            MAX_POSITIONS + 1)
        return [{"position_kind": "PAPER", "group_id": r["group_id"],
                 "slug": r["us_market_slug"],
                 "first_fill_at": _ep(r["ffa"])} for r in rows]

    async def actual(conn):
        return [{"position_kind": "ACTUAL", "group_id": r["group_id"],
                 "slug": r["us_market_slug"],
                 "first_fill_at": _ep(r["first_live_fill_at"])}
                for r in await conn.fetch(
                    "SELECT group_id, us_market_slug, first_live_fill_at "
                    "  FROM smalllive_handoffs WHERE state = 'OPEN' "
                    " ORDER BY first_live_fill_at LIMIT $1", MAX_POSITIONS)]

    pp = await s.run("work.xavier_paper_positions",
                     ("paper_fills", "paper_settlements"), paper)
    if pp is None and s.status.get("work.xavier_paper_positions", {}).get(
            "status") != "EMPTY":
        out["why"] = s.status.get("work.xavier_paper_positions", {}).get(
            "why")
        return out
    pp = pp or []
    n_paper = len(pp)
    if n_paper > MAX_POSITIONS:
        n_paper = await s.run("work.xavier_paper_count",
                              ("paper_fills", "paper_settlements"),
                              lambda c: c.fetchval(
                                  "SELECT count(*) FROM (SELECT 1 FROM "
                                  " paper_fills f WHERE NOT EXISTS (SELECT 1"
                                  " FROM paper_settlements x WHERE "
                                  " x.group_id = f.group_id AND "
                                  " x.us_market_slug = f.us_market_slug) "
                                  " GROUP BY f.group_id, f.us_market_slug "
                                  "HAVING sum(CASE WHEN f.direction='BUY' "
                                  " THEN f.qty ELSE -f.qty END) > 1e-9) q"))
        pp = pp[:MAX_POSITIONS]
    ap = await s.run("work.xavier_actual_positions", ("smalllive_handoffs",),
                     actual) or []
    if n_paper is None:
        out["why"] = "PAPER_POSITION_COUNT_UNREADABLE"
        return out
    positions = pp + ap
    out["open"] = int(n_paper) + len(ap)
    if not positions:
        return out
    by = {(p["position_kind"], p["group_id"]): p for p in positions}
    paper_groups = sorted({p["group_id"] for p in pp})
    actual_groups = sorted({p["group_id"] for p in ap})

    async def reviews(conn):
        got = {}
        if paper_groups:
            if await s.exists("xavier_current_review"):
                sql = ("SELECT 'PAPER' AS pk, r.review_id, r.group_id, "
                       "       r.reviewed_at, r.recommendation, r.measure, "
                       "       r.selection FROM xavier_current_review c "
                       "  JOIN paper_xavier_reviews r "
                       "    ON r.review_id = c.paper_review_id "
                       " WHERE c.position_kind = 'PAPER' "
                       "   AND c.group_id = ANY($1::text[])")
            else:
                sql = ("SELECT DISTINCT ON (group_id) 'PAPER' AS pk, "
                       "       review_id, group_id, reviewed_at, "
                       "       recommendation, measure, selection "
                       "  FROM paper_xavier_reviews "
                       " WHERE group_id = ANY($1::text[]) "
                       " ORDER BY group_id, reviewed_at DESC, "
                       "          review_id DESC")
            for r in await conn.fetch(sql, paper_groups):
                d = dict(r, reviewed_at=_ep(r["reviewed_at"]))
                blk = XF.of_review(d, now=now)
                got[("PAPER", r["group_id"])] = {
                    "table": "paper_xavier_reviews",
                    "review_id": r["review_id"],
                    "reviewed_at": d["reviewed_at"],
                    "recorded_recommendation": r["recommendation"],
                    "recommendation_state": blk["recommendation_state"],
                    "management_state": blk["management_state"]}
        if actual_groups and await s.exists("xavier_management_assessments"):
            if await s.exists("xavier_current_review"):
                sql = ("SELECT a.* FROM xavier_current_review c "
                       "  JOIN xavier_management_assessments a "
                       "    ON a.assessment_id = c.assessment_id "
                       " WHERE c.position_kind = 'ACTUAL' "
                       "   AND c.group_id = ANY($1::text[])")
            else:
                sql = ("SELECT DISTINCT ON (group_id) * "
                       "  FROM xavier_management_assessments "
                       " WHERE position_kind = 'ACTUAL' "
                       "   AND group_id = ANY($1::text[]) "
                       " ORDER BY group_id, assessed_at DESC, "
                       "          assessment_id DESC")
            for r in await conn.fetch(sql, actual_groups):
                d = dict(r, assessed_at=_ep(r["assessed_at"]))
                blk = XF.of_assessment(d, now=now)
                got[("ACTUAL", r["group_id"])] = {
                    "table": "xavier_management_assessments",
                    "review_id": r["assessment_id"],
                    "reviewed_at": d["assessed_at"],
                    "recorded_recommendation": r["recommendation"],
                    "recommendation_state": blk["recommendation_state"],
                    "management_state": blk["management_state"]}
        return got

    async def books(conn):
        return {r["us_market_slug"]: {
            "obs_id": r["obs_id"], "observed_at": _ep(r["observed_at"]),
            "error": r["error"]} for r in await conn.fetch(
                "SELECT DISTINCT ON (us_market_slug) us_market_slug, obs_id,"
                "       observed_at, error FROM paper_book_observations "
                " WHERE us_market_slug = ANY($1::text[]) "
                " ORDER BY us_market_slug, observed_at DESC, obs_id DESC",
                sorted({p["slug"] for p in positions if p.get("slug")}))}

    async def requests(conn):
        got: dict = {}
        for r in await conn.fetch(
                "SELECT o.position_kind, o.group_id, o.kind, o.request_id, "
                "       o.opened_at, r.expires_at, EXISTS (SELECT 1 FROM "
                "       agent_work_request_events e WHERE e.request_id = "
                "       o.request_id AND e.state = 'DISPATCHED') AS sent "
                "  FROM agent_work_open o JOIN agent_work_requests r "
                "    ON r.request_id = o.request_id "
                " WHERE o.agent_id = 'XAVIER'"):
            got.setdefault((r["position_kind"], r["group_id"]), {})[
                r["kind"]] = {"request_id": r["request_id"],
                              "opened_at": _ep(r["opened_at"]),
                              "expires_at": _ep(r["expires_at"]),
                              "dispatched": bool(r["sent"])}
        return got

    rv = await s.run("work.xavier_current_reviews", ("paper_xavier_reviews",),
                     reviews, default={}) or {}
    bk = await s.run("work.xavier_position_books",
                     ("paper_book_observations",), books, default={}) or {}
    rq = await s.run("work.xavier_open_requests",
                     ("agent_work_open", "agent_work_requests",
                      "agent_work_request_events"), requests,
                     default={}) or {}
    for key, p in by.items():
        p["current_review"] = rv.get(key)
        p["book"] = bk.get(p.get("slug"))
        p["requests"] = rq.get(key) or {}
    out["positions"] = positions
    return out


async def _read_handoffs(s: _Sections, now: float) -> dict:
    """{agent: [handoff rows]} from agent_tasks, karen_challenges, paper
    ENTER decisions (Archer, the Chief Allocator)."""
    out: dict = {a: [] for a in AGENTS}

    async def tasks(conn):
        return [dict(r) for r in await conn.fetch(
            "SELECT assignee, count(*) AS n, min(created_at) AS oldest, "
            "       (array_agg(task_id ORDER BY created_at))[1:5] AS ids "
            "  FROM agent_tasks WHERE status = 'OPEN' GROUP BY assignee")]

    async def challenges(conn):
        return [dict(r) for r in await conn.fetch(
            "SELECT target_agent, state, count(*) AS n, "
            "       min(coalesce(responded_at, challenged_at)) AS oldest, "
            "       (array_agg(challenge_id ORDER BY challenged_at))[1:5] "
            "       AS ids FROM karen_challenges "
            " WHERE state IN ('OPEN', 'RESPONDED') "
            " GROUP BY target_agent, state")]

    async def archer(conn):
        return dict(await conn.fetchrow(
            "SELECT count(*) AS n, min(d.decided_at) AS oldest, "
            "       (array_agg(d.decision_id ORDER BY d.decided_at))[1:5] "
            "       AS ids FROM paper_decisions d "
            " WHERE d.verdict = 'ENTER' "
            "   AND d.decided_at BETWEEN to_timestamp($1) "
            "                        AND to_timestamp($2) "
            "   AND NOT EXISTS (SELECT 1 FROM eddie_execution_estimates e "
            "                    WHERE e.decision_id = d.decision_id)",
            now - ARCHER_LOOKBACK_S, now))

    async def allocator(conn):
        last = await conn.fetchrow(
            "SELECT run_id, started_at FROM intel_runs "
            " WHERE component = 'ALLOCATOR' ORDER BY started_at DESC "
            " LIMIT 1")
        if last is None:
            return None
        r = await conn.fetchrow(
            "SELECT count(*) AS n, min(decided_at) AS oldest, "
            "       (array_agg(decision_id ORDER BY decided_at))[1:5] AS ids"
            "  FROM paper_decisions WHERE verdict = 'ENTER' "
            "   AND decided_at > $1 AND decided_at <= to_timestamp($2)",
            last["started_at"], now)
        return dict(r, run_id=last["run_id"])

    for r in await s.run("work.agent_tasks", ("agent_tasks",), tasks,
                         default=[]) or []:
        if r["assignee"] in out:
            out[r["assignee"]].append({
                "kind": "AGENT_TASK_OPEN", "table": "agent_tasks",
                "count": int(r["n"]), "oldest_at": _ep(r["oldest"]),
                "ids": list(r["ids"] or [])})
    for r in await s.run("work.karen_challenges", ("karen_challenges",),
                         challenges, default=[]) or []:
        tgt = r["target_agent"]
        if r["state"] == "OPEN" and tgt in out:
            out[tgt].append({
                "kind": "KAREN_CHALLENGE_TO_ANSWER",
                "table": "karen_challenges", "count": int(r["n"]),
                "oldest_at": _ep(r["oldest"]), "ids": list(r["ids"] or [])})
        elif r["state"] == "RESPONDED" and tgt in EVALUATOR_FOR:
            out[EVALUATOR_FOR[tgt]].append({
                "kind": "CHALLENGE_ANSWER_TO_EVALUATE:%s" % tgt,
                "table": "karen_challenges", "count": int(r["n"]),
                "oldest_at": _ep(r["oldest"]), "ids": list(r["ids"] or [])})
    e = await s.run("work.archer_pending", ("paper_decisions",
                                           "eddie_execution_estimates"),
                    archer)
    if e and int(e["n"] or 0):
        out["ARCHER"].append({
            "kind": "ENTER_DECISION_WITHOUT_ESTIMATE",
            "table": "paper_decisions", "count": int(e["n"]),
            "oldest_at": _ep(e["oldest"]), "ids": list(e["ids"] or [])})
    al = await s.run("work.allocator_pending", ("intel_runs",
                                                "paper_decisions"),
                     allocator)
    if al and int(al["n"] or 0):
        out["CHIEF_ALLOCATOR"].append({
            "kind": "ENTER_DECISION_AFTER_LAST_ALLOCATION_RUN",
            "table": "paper_decisions", "count": int(al["n"]),
            "oldest_at": _ep(al["oldest"]), "ids": list(al["ids"] or []),
            "last_run_id": al["run_id"]})
    return out


async def _read_outputs(s: _Sections) -> dict:
    out: dict = {a: [] for a in AGENTS}
    for agent, table, name, sql in (
            ("DEREK", "paper_decisions", "decision",
             "SELECT decision_id AS id, decided_at AS at FROM paper_decisions"
             " ORDER BY decided_at DESC LIMIT 1"),
            ("KAREN", "karen_challenges", "challenge",
             "SELECT challenge_id AS id, challenged_at AS at "
             "  FROM karen_challenges ORDER BY challenged_at DESC LIMIT 1"),
            ("ARCHER", "eddie_execution_estimates", "execution estimate",
             "SELECT estimate_id AS id, estimated_at AS at "
             "  FROM eddie_execution_estimates ORDER BY estimated_at DESC "
             " LIMIT 1"),
            ("SCOUT", "scout_features", "feature",
             "SELECT feature_id AS id, greatest(proposed_at, "
             "       coalesce(state_set_at, proposed_at)) AS at "
             "  FROM scout_features ORDER BY 2 DESC LIMIT 1"),
            ("AUDREY", "paper_audrey_findings", "audit finding",
             "SELECT finding_id AS id, found_at AS at "
             "  FROM paper_audrey_findings ORDER BY found_at DESC LIMIT 1"),
            ("AUDREY", "audrey_audit_reports", "audit report",
             "SELECT report_id AS id, computed_at AS at "
             "  FROM audrey_audit_reports ORDER BY computed_at DESC LIMIT 1"),
            ("ADRIANA", "adriana_arb_scans", "arbitrage census",
             "SELECT scan_id AS id, finished_at AS at FROM adriana_arb_scans"
             " ORDER BY finished_at DESC LIMIT 1"),
            ("CHIEF_ALLOCATOR", "intel_runs", "allocation run",
             "SELECT run_id AS id, finished_at AS at FROM intel_runs "
             " WHERE component = 'ALLOCATOR' AND status = 'OK' "
             "   AND finished_at IS NOT NULL "
             " ORDER BY finished_at DESC LIMIT 1")):
        async def fn(conn, sql=sql):
            return await conn.fetchrow(sql)
        r = await s.run("work.output.%s.%s" % (agent, table), (table,), fn)
        if r is not None and r["at"] is not None:
            out[agent].append({"table": table, "id": str(r["id"]),
                               "at": _ep(r["at"]), "label": "%s %s" % (
                                   name, r["id"])})
    return out


async def _read_allocator_run(s: _Sections, now: float) -> dict | None:
    async def fn(conn):
        return await conn.fetchrow(
            "SELECT run_id, component, started_at FROM intel_runs "
            " WHERE component IN ('ALLOCATOR', 'CYCLE') "
            "   AND finished_at IS NULL AND started_at >= to_timestamp($1) "
            "   AND started_at <= to_timestamp($2) "
            " ORDER BY started_at DESC LIMIT 1",
            now - RUN_WINDOW_FLOOR_S, now)
    r = await s.run("work.allocator_run", ("intel_runs",), fn)
    if r is None:
        return {"in_progress": False}
    return {"in_progress": True, "table": "intel_runs", "id": r["run_id"],
            "started_at": _ep(r["started_at"]),
            "activity": "%s run %s in progress" % (
                str(r["component"]).title(), r["run_id"])}


async def _read_scout_waiting(s: _Sections) -> list:
    async def fn(conn):
        return await conn.fetchrow(
            "SELECT count(*) AS n, (array_agg(feature_id ORDER BY "
            "       proposed_at))[1:5] AS ids FROM scout_features "
            " WHERE state = 'UNDER_TEST'")
    r = await s.run("work.scout_under_test", ("scout_features",), fn)
    if not r or not int(r["n"] or 0):
        return []
    return [{"kind": "FEATURE_UNDER_TEST_AWAITING_SAMPLES",
             "table": "scout_features", "count": int(r["n"]),
             "ids": list(r["ids"] or [])}]


async def read_facts(conn, *, now: float | None = None) -> dict:
    """EVERY AGENT'S RECORDED WORK FACTS. Read only; a missing table or a
    failed read is named in `sections`, never raised and never a zero."""
    now = float(now if now is not None else time.time())
    s = _Sections(conn)
    status = await _read_status(s)
    market = await _read_market(s)
    pos = await _read_positions(s, now)
    hand = await _read_handoffs(s, now)
    outs = await _read_outputs(s)
    alloc_run = await _read_allocator_run(s, now)
    scout_wait = await _read_scout_waiting(s)
    facts = {}
    for a in AGENTS:
        st = status.get(a)
        f = {"status": st, "stale_s": (st or {}).get("stale_s"),
             "market": market, "handoffs": hand.get(a) or [],
             "outputs": outs.get(a) or [], "waiting": []}
        if a == "XAVIER":
            f.update(open_positions=pos["open"], positions=pos["positions"],
                     positions_why=pos["why"])
        elif a == "CHIEF_ALLOCATOR":
            f["run"] = alloc_run
        elif a == "SCOUT":
            f["waiting"] = scout_wait
        facts[a] = f
    return {"now": now, "facts": facts, "sections": s.status}


async def read_work_states(conn, *, now: float | None = None) -> dict:
    """{"states": {agent: derive(...)}, "sections": {...}}."""
    got = await read_facts(conn, now=now)
    return {"now": got["now"], "sections": got["sections"],
            "states": {a: derive(a, f, now=got["now"])
                       for a, f in got["facts"].items()}}
