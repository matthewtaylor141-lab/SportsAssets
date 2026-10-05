"""THE HELD-MARK REFRESH: EVERY HELD MARKET RE-READ INSIDE THE MARK SLA.

THE ROOT CAUSE IT REPAIRS (production 2026-10-05: "158 stale marks > 300s,
12 unmarked, 71/241 fresh"). The paper pass reads books in ONE step
(`paper_runtime.step_books`) under `cadence.max_book_reads_per_pass` = 12, of
which the books step may use HALF (6), and held positions are read LAST --
after due entries, Xavier's priority slugs and every resting order (each
standing protection order is a resting order). With ~60 s between scheduled
passes that is at most ~6 markets refreshed a minute against a book of
~150+ held markets and a 300 s SLA: most marks could never be refreshed in
time, whatever the venue said. A read that failed was recorded but invisible
to the marks, and a read the pass never reached left no record at all.

THE REPAIR. A separate, explicitly budgeted refresh run (its own pool
connection, never the paper pass's lock or read cap, so Derek's half of the
pass is unchanged):

  1. every open paper position's market is a candidate; a market whose
     newest successful read carries a TERMINAL venue state is not re-read
     (EXTERNAL_UNAVAILABLE, the observation is the evidence) and is
     recorded SKIPPED_EXTERNAL_TERMINAL;
  2. a market is DUE when its newest successful read is older than
     REFRESH_AFTER_S (180 s: inside the 300 s SLA with one run of margin)
     or it was never read;
  3. due markets are served STALEST FIRST (never read first), the larger
     exposure first on a tie -- so no market starves;
  4. a book THIS PROCESS already read within HARVEST_MAX_AGE_S (the
     collection cycle's own reads) is recorded with its ORIGINAL receipt
     instant -- no venue request, nothing made fresher than it is;
  5. otherwise one paced, gated venue read (the same read the pass makes,
     `paper_derek.read_book_within_deadline` through the read-only
     PaperMarketDataClient), recorded as an observation (an error included);
  6. the run is BOUNDED: at most MAX_READS_PER_RUN venue reads, at most
     RUN_BUDGET_S seconds, at most MAX_VENUE_READS_PER_HOUR venue reads per
     rolling hour in this process; every due market it could not reach is
     recorded SKIPPED_* with the bound that stopped it;
  7. the run and EVERY due market's outcome are recorded in
     paper_mark_refresh_runs (migration 270).

THE EXTERNAL API BUDGET, MADE EXPLICIT. Every venue read goes through the
process-wide venue pace (venue_pace.MIN_GAP_S, normal lane: the money paths'
priority claims go first) and the venue request gate (429 cooldowns honoured,
refused by name rather than waited past the run's deadline). On top of that
this refresh may spend at most MAX_VENUE_READS_PER_HOUR (3,600: one a second
on average, about a third of the pace's 1 / 0.35 s ceiling) and at most
MAX_READS_PER_RUN per run, one run at a time, at most one run per
MIN_RUN_INTERVAL_S. A held book of N markets needs about N x 3600 / 210
reads an hour in steady state (each market re-read every ~180-240 s), so the
budget covers ~200 held markets; beyond that the shortfall is RECORDED
(SKIPPED_HOURLY_BUDGET -> FEED_GAP), never hidden and never relabelled.

PAPER ONLY. It reads books and writes paper_book_observations and its own run
record; it places, cancels and changes no order, limit or threshold.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .. import bettor_paper_freshness as PMF
from .. import bettor_paper_guard as G
from .. import bettor_paper_ledger as L
from .. import bettor_paper_session as S

log = logging.getLogger(__name__)

VERSION = "PAPER_HELD_MARK_REFRESH_V1"

#: a held market is due once its newest successful read is older than this
#: (the SLA is bettor_paper_ledger.MARK_STALE_AFTER_S = 300 s; 180 leaves
#: more than one run interval of margin)
REFRESH_AFTER_S = 180.0
#: the explicit external budget (see the module docstring)
MAX_READS_PER_RUN = 90
RUN_BUDGET_S = 45.0
MAX_VENUE_READS_PER_HOUR = 3600
MIN_RUN_INTERVAL_S = 30.0
#: one read never holds the run longer than this
PER_READ_TIMEOUT_S = 8.0
#: a book this process read at most this long ago answers without a venue
#: request (recorded with its original receipt instant)
HARVEST_MAX_AGE_S = 60.0
READ_BASIS = "HELD_MARK_REFRESH"
HARVEST_BASIS = "HELD_MARK_REFRESH_SHARED_READ"
SOURCE = "PAPER_MARKET_DATA_CLIENT"

O_READ_OK = "READ_OK"
O_READ_FAILED = "READ_FAILED"
O_HARVESTED = "HARVESTED_SHARED_READ"
O_SKIP_TERMINAL = "SKIPPED_EXTERNAL_TERMINAL"
O_SKIP_RUN_CAP = "SKIPPED_RUN_READ_CAP"
O_SKIP_HOURLY = "SKIPPED_HOURLY_BUDGET"
O_SKIP_TIME = "SKIPPED_RUN_TIME_BUDGET"

_HOURLY: dict = {"reads": []}


def budget() -> dict:
    return {"refresh_after_s": REFRESH_AFTER_S,
            "sla_s": PMF.SLA_S,
            "max_reads_per_run": MAX_READS_PER_RUN,
            "run_budget_s": RUN_BUDGET_S,
            "max_venue_reads_per_hour": MAX_VENUE_READS_PER_HOUR,
            "min_run_interval_s": MIN_RUN_INTERVAL_S,
            "per_read_timeout_s": PER_READ_TIMEOUT_S,
            "harvest_max_age_s": HARVEST_MAX_AGE_S,
            "pace": "venue_pace (process-wide gap, normal lane) + "
                    "venue_request_gate (429 cooldowns)"}


def hourly_used(now: float) -> int:
    _HOURLY["reads"] = [t for t in _HOURLY["reads"] if now - t < 3600.0]
    return len(_HOURLY["reads"])


def _default_recent(slug: str, *, max_age_s: float):
    """A book this process read within `max_age_s` (the collection cycle's
    own successful reads), or None. Never raises; never a venue request."""
    try:
        from ..workers import ext_pinnacle_loop as LOOP
        return LOOP.recent_book(slug, max_age_s=max_age_s)
    except Exception:                                           # noqa: BLE001
        return None


def plan(held: list, ev: dict, *, now: float) -> dict:
    """WHICH HELD MARKETS ARE DUE, IN WHAT ORDER (pure).

    `held`: [{us_market_slug, exposure_usd}] one per market; `ev`:
    bettor_paper_freshness.market_evidence. Returns {due[...], not_due[...],
    terminal{slug: evidence}}."""
    due, not_due, terminal = [], [], {}
    for h in held:
        slug = h["us_market_slug"]
        ok = (ev.get(slug) or {}).get("last_ok")
        st = str((ok or {}).get("market_state") or "").upper()
        if ok and st in PMF.TERMINAL_MARKET_STATES:
            terminal[slug] = {"obs_id": ok["obs_id"], "market_state": st}
            continue
        age = None if not ok else now - float(ok["at"])
        if age is not None and age <= REFRESH_AFTER_S:
            not_due.append(slug)
            continue
        due.append((0 if age is None else 1, -(age or 0.0),
                    -float(h.get("exposure_usd") or 0.0), slug,
                    None if not ok else float(ok["at"])))
    due.sort()
    return {"due": [(d[3], d[4]) for d in due], "not_due": not_due,
            "terminal": terminal}


async def refresh(conn, *, account_id: str = L.ACCOUNT_ID, market_data=None,
                  now: float | None = None, clock=None,
                  run_budget_s: float = RUN_BUDGET_S,
                  max_reads: int = MAX_READS_PER_RUN,
                  hourly_budget: int = MAX_VENUE_READS_PER_HOUR,
                  recent=None, trigger: str = "SCHEDULED") -> dict:
    """ONE BOUNDED HELD-MARK REFRESH RUN for `account_id`. Records every due
    market's outcome. Never raises (CancelledError excepted)."""
    from . import paper_derek as PD
    from .. import bettor_paper_simulator as SIM
    clock = clock or time.time
    at = float(now if now is not None else clock())
    t0 = time.monotonic()
    deadline = t0 + float(run_budget_s)
    md = market_data if market_data is not None else G.PaperMarketDataClient()
    recent = recent if recent is not None else _default_recent
    out: dict[str, Any] = {"version": VERSION, "account_id": account_id,
                           "trigger": trigger, "at": at, "held_markets": 0,
                           "due": 0, "not_due": 0, "harvested": 0,
                           "read_attempted": 0, "read_ok": 0,
                           "read_failed": 0, "skipped_budget": 0,
                           "skipped_terminal": 0, "outcomes": {},
                           "budget": budget()}
    run_id = None
    has_log = await PMF._has(conn, "paper_mark_refresh_runs")
    try:
        if has_log:
            run_id = await conn.fetchval(
                "INSERT INTO paper_mark_refresh_runs (account_id, trigger, "
                " started_at, budget) VALUES ($1,$2,to_timestamp($3),"
                " $4::jsonb) RETURNING run_id", account_id, trigger, at,
                json.dumps(out["budget"]))
        out["run_id"] = run_id
        pos = await L.positions(conn, account_id)
        expo: dict = {}
        for p in pos:
            expo[p["us_market_slug"]] = expo.get(p["us_market_slug"], 0.0) \
                + float(p.get("cost_basis_usd") or 0.0)
        held = [{"us_market_slug": s, "exposure_usd": e}
                for s, e in expo.items()]
        ev = await PMF.market_evidence(conn, list(expo),
                                       account_id=account_id)
        pl = plan(held, ev, now=at)
        out.update(held_markets=len(held), due=len(pl["due"]),
                   not_due=len(pl["not_due"]),
                   skipped_terminal=len(pl["terminal"]))
        for slug, evd in pl["terminal"].items():
            out["outcomes"][slug] = {"outcome": O_SKIP_TERMINAL,
                                     "why": evd["market_state"],
                                     "obs_id": evd["obs_id"]}
        for slug, ok_at in pl["due"]:
            # 4. THE HARVEST: a newer book this process already holds
            shared = None
            try:
                shared = recent(slug, max_age_s=HARVEST_MAX_AGE_S)
            except Exception:                                   # noqa: BLE001
                shared = None
            if shared is not None and isinstance(
                    shared.get("marketData"), dict) and (
                    ok_at is None
                    or float(shared.get("observed_at") or 0) > ok_at):
                rec = await SIM.record_book(
                    conn, slug=slug, read=dict(shared, shared_read=True),
                    source=SOURCE, read_basis=HARVEST_BASIS)
                out["harvested"] += 1
                out["outcomes"][slug] = {"outcome": O_HARVESTED,
                                         "obs_id": rec.get("obs_id")}
                continue
            # 6. THE BOUNDS, checked before each venue read
            stop = None
            if out["read_attempted"] >= int(max_reads):
                stop = O_SKIP_RUN_CAP
            elif hourly_used(float(clock())) >= int(hourly_budget):
                stop = O_SKIP_HOURLY
            elif deadline - time.monotonic() <= PD.BOOK_READ_RESERVE_S:
                stop = O_SKIP_TIME
            if stop is not None:
                out["skipped_budget"] += 1
                out["outcomes"][slug] = {"outcome": stop, "why": stop}
                continue
            # 5. ONE PACED, GATED VENUE READ
            rctx = {"market_data": md,
                    "deadline": min(deadline, time.monotonic()
                                    + PER_READ_TIMEOUT_S
                                    + PD.BOOK_READ_RESERVE_S)}
            _HOURLY["reads"].append(float(clock()))
            out["read_attempted"] += 1
            got = await PD.read_book_within_deadline(rctx, slug)
            rec = await SIM.record_book(conn, slug=slug, read=got,
                                        source=SOURCE, read_basis=READ_BASIS)
            if rec.get("error"):
                out["read_failed"] += 1
                out["outcomes"][slug] = {"outcome": O_READ_FAILED,
                                         "why": str(rec["error"])[:160],
                                         "obs_id": rec.get("obs_id")}
            else:
                out["read_ok"] += 1
                out["outcomes"][slug] = {"outcome": O_READ_OK,
                                         "obs_id": rec.get("obs_id")}
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    out["elapsed_s"] = round(time.monotonic() - t0, 3)
    out["venue_reads_last_hour"] = hourly_used(float(clock()))
    if run_id is not None:
        try:
            await conn.execute(
                "UPDATE paper_mark_refresh_runs SET finished_at = "
                " to_timestamp($2), held_markets=$3, due=$4, not_due=$5, "
                " harvested=$6, read_attempted=$7, read_ok=$8, "
                " read_failed=$9, skipped_budget=$10, skipped_terminal=$11,"
                " outcomes=$12::jsonb, error=$13 WHERE run_id = $1",
                run_id, at + out["elapsed_s"], out["held_markets"],
                out["due"], out["not_due"], out["harvested"],
                out["read_attempted"], out["read_ok"], out["read_failed"],
                out["skipped_budget"], out["skipped_terminal"],
                json.dumps(out["outcomes"], default=str), out.get("error"))
        except Exception as exc:                                # noqa: BLE001
            out["record_error"] = type(exc).__name__
    return out


# ═════════════════════════════════════════════════════════════════════
# THE SCHEDULE: ONE BACKGROUND RUN AT A TIME, ON ITS OWN CONNECTION
# ═════════════════════════════════════════════════════════════════════

ACQUIRE_TIMEOUT_S = 10.0
HARD_TIMEOUT_S = RUN_BUDGET_S + 30.0
_TASK: dict = {"task": None, "last_started": 0.0, "runs": 0,
               "coalesced": 0, "last": None}


async def run_once(get_pool, *, trigger: str,
                   account_id: str = L.ACCOUNT_ID) -> dict:
    """ONE RUN, only while the paper session is enabled (env flag AND the
    control row). Never raises."""
    try:
        pool = await get_pool()
        async with pool.acquire(timeout=ACQUIRE_TIMEOUT_S) as conn:
            en = await S.enablement(conn)
            if not en.get("enabled"):
                res = {"ran": False, "refusal": en.get("refusal")}
            else:
                res = await asyncio.wait_for(
                    refresh(conn, account_id=account_id, trigger=trigger),
                    HARD_TIMEOUT_S)
                res["ran"] = True
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        res = {"ran": False, "error": "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:200])}
    _TASK["last"] = {k: res.get(k) for k in (
        "ran", "refusal", "error", "run_id", "held_markets", "due",
        "read_attempted", "read_ok", "read_failed", "skipped_budget",
        "harvested", "elapsed_s")}
    return res


def schedule(get_pool, *, trigger: str, now: float | None = None) -> dict:
    """START ONE BACKGROUND RUN unless one is running or the last one started
    less than MIN_RUN_INTERVAL_S ago. Returns at once; never raises."""
    if not S.env_on():
        return {"scheduled": False, "why": S.R_ENV_OFF}
    t = _TASK.get("task")
    if t is not None and not t.done():
        _TASK["coalesced"] += 1
        return {"scheduled": False, "why": "A_REFRESH_RUN_IS_RUNNING"}
    at = time.time() if now is None else float(now)
    if at - _TASK["last_started"] < MIN_RUN_INTERVAL_S:
        return {"scheduled": False, "why": "MIN_RUN_INTERVAL",
                "next_after_s": round(MIN_RUN_INTERVAL_S
                                      - (at - _TASK["last_started"]), 3)}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"scheduled": False, "why": "NO_RUNNING_LOOP"}
    _TASK["last_started"] = at
    _TASK["runs"] += 1
    _TASK["task"] = loop.create_task(run_once(get_pool, trigger=trigger))
    return {"scheduled": True, "trigger": trigger, "runs": _TASK["runs"]}


def status() -> dict:
    t = _TASK.get("task")
    return {"version": VERSION, "running": t is not None and not t.done(),
            "runs": _TASK["runs"], "coalesced": _TASK["coalesced"],
            "last": _TASK["last"], "budget": budget(),
            "venue_reads_last_hour": hourly_used(time.time())}
