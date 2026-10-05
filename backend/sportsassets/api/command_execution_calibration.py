"""LIVE EXECUTION CALIBRATION: GET /api/command/execution-calibration (GET
only, COMMAND auth via agents_core.require_read). READ ONLY.

    ?since=<epoch>&until=<epoch>   the window of canonical decision intents
                                   (default: the last
                                   execution_calibration.WINDOW_DAYS days)

ANSWERS (the profitability envelope):
    data: {version, window,
           classes: {PAPER_SIMULATION | LIVE_SHADOW | ACTUAL:
                     {class, storage, live_use, is_proof_of_live_execution,
                      orders, independent_events, excluded,
                      metrics: {fill_rate, any_fill_rate, qty_fill_share,
                                slippage_vs_limit_pp,
                                slippage_vs_decision_best_pp,
                                adverse_selection_30s_pp,
                                adverse_selection_300s_pp, cancel_rate,
                                recovery_rate: {value, n, clusters, ci_low,
                                                ci_high, ci_method, status,
                                                why, unit, basis}}}},
           live_estimates: {metric: {status, fitted_on, live_use, value,
                                     class_ci_*, live_ci_*,
                                     transfer_penalty, not_used}},
           estimates_in_use: [{estimate, module, fitted_on, live_use, ...}],
           eddie_fit, rule, rule_sha, pooled_across_classes: false}

The three classes are read from their own tables and NEVER pooled
(execution_evidence): PAPER_SIMULATION from the PAPER adapter's paper order
and fills; LIVE_SHADOW from the SMALL LIVE SHADOW adapter's proposal walked
through the venue book observed at the decision with the paper simulator's
own pure marketable walk (bettor_paper_simulator.levels_for / walk) -- no
delay, no consumption, nothing sent; ACTUAL from small_live_order_events of a
LIVE-mode execution -- none exist while SMALL LIVE is SHADOW, so every
ACTUAL metric is UNAVAILABLE with execution_evidence.R_NO_ACTUAL, never 0.

THE SHADOW'S FILL FIGURES ARE TAUTOLOGICAL AT LIVE SCALE: the SHADOW order
was sized within this very book's depth at its limit and scaled 1:1,000, so
the walk fills it at the touch by construction. Its fill / slippage / cancel
/ recovery figures are shown with `live_eligible: false` and the reason,
never as live execution quality (execution_calibration).

BOUNDED AND SAID SO: each class reads at most MAX_INTENTS (MAX_EVENTS) rows,
the MOST RECENT first; when the window holds more, the class's `read` block
says `truncated: true` with the cap, the count in the window and the oldest
instant read, and the rows not read are counted under `excluded` -- the
newest evidence is never the part silently dropped.

Everything runs inside a READ ONLY transaction under a statement timeout.
This module writes nothing. Neither it nor anything it calls imports an
order-submission, venue, execution-path or funded module: not directly, and
not at run time -- tests/test_execution_calibration.py imports the route in
a fresh interpreter, runs its estimate registry and checks the loaded
modules (the micro-calibration lane's venue and ledger modules included:
their evidence class is restated in execution_calibration, never imported).
The paper modules it reads (the simulator's pure walk, Eddie's history) are
read models of paper records.
"""
from __future__ import annotations

import json
import math
import time

from fastapi import APIRouter, Depends, Query

from .. import bettor_paper_simulator as SIM
from .. import execution_calibration as EC
from .. import execution_evidence as EE
from .. import opportunity_tournament as OT
from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/execution-calibration"
STATEMENT_TIMEOUT_MS = 8000
CACHE_S = 15.0
_CACHE: dict = {}
MAX_INTENTS = EC.MAX_INTENTS
MAX_EVENTS = EC.MAX_EVENTS
HORIZONS = EC.HORIZONS
HORIZON_TOLERANCE_S = EC.HORIZON_TOLERANCE_S
PAPER_TERMINAL = EC.PAPER_TERMINAL
ACTUAL_TERMINAL = EC.ACTUAL_TERMINAL
WINDOW_DAYS = EC.WINDOW_DAYS


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


# ═════════════════════════════════════════════════════════════════════
# THE BOOK (pure)
# ═════════════════════════════════════════════════════════════════════

def book_md(row) -> dict | None:
    """A paper_book_observations row as market data, None if unreadable."""
    if not row or row.get("error") or row.get("bids") is None:
        return None
    return {"bids": _j(row.get("bids")) or [],
            "offers": _j(row.get("offers")) or []}


def best_acquisition(md, side) -> float | None:
    if not md or side not in ("LONG", "SHORT"):
        return None
    lv = SIM.levels_for(md, direction="BUY", holding_side=side)["levels"]
    return float(lv[0]["price"]) if lv else None


def side_mid(md, side) -> float | None:
    """Mid of the holding side: (best acquisition + best exit) / 2, cost
    space (Eddie's definition)."""
    if not md or side not in ("LONG", "SHORT"):
        return None
    a = SIM.levels_for(md, direction="BUY", holding_side=side)["levels"]
    x = SIM.levels_for(md, direction="SELL", holding_side=side)["levels"]
    if not a or not x:
        return None
    return (float(a[0]["price"]) + float(x[0]["price"])) / 2.0


def shadow_fill(md, *, holding_side: str, qty, limit, time_in_force) -> dict:
    """THE SHADOW PROPOSAL WALKED THROUGH THE OBSERVED BOOK, with the
    simulator's own marketable walk (no delay, no consumption ledger, no
    queue: it was never sent). Pure."""
    q, lim = _num(qty), _num(limit)
    if md is None:
        return {"ok": False, "why": "DECISION_BOOK_UNREADABLE"}
    if q is None or q <= 0 or lim is None:
        return {"ok": False, "why": "NO_LIVE_QUANTITY_OR_LIMIT"}
    lv = SIM.levels_for(md, direction="BUY", holding_side=holding_side)
    got = SIM.walk(lv["levels"], consumed={}, limit=lim, qty=q,
                   direction="BUY", allow_partial=str(time_in_force) == "IOC")
    takes = got.get("takes") or []
    filled = float(got.get("filled") or 0.0)
    vwap = (sum(float(t["price"]) * float(t["take"]) for t in takes)
            / filled) if filled > 0 else None
    return {"ok": True, "filled_qty": filled, "requested_qty": q,
            "vwap": vwap, "refusal": got.get("refusal"),
            "best": (float(lv["levels"][0]["price"]) if lv["levels"]
                     else None)}



# ═════════════════════════════════════════════════════════════════════
# THE READS (SELECT only)
# ═════════════════════════════════════════════════════════════════════

#: the most recent MAX_INTENTS of the window (newest first); the count of
#: the whole window says whether anything older was left unread
INTENT_SQL = """
    SELECT i.intent_id, i.decision_id, i.strategy, i.sleeve, i.us_market_slug,
           i.holding_side, i.contract, i.evidence, i.limit_price,
           i.target_qty, extract(epoch FROM i.created_at)::float8 AS decided_at,
           e.state, e.exclusion, e.requested, e.refs, e.mode
      FROM canonical_decision_intents i
      JOIN canonical_intent_executions e ON e.intent_id = i.intent_id
     WHERE e.adapter = $1 AND e.intent_kind = 'DECISION'
       AND i.created_at >= to_timestamp($2) AND i.created_at <= to_timestamp($3)
     ORDER BY i.created_at DESC, i.intent_id DESC LIMIT $4
"""
INTENT_COUNT_SQL = """
    SELECT count(*) FROM canonical_decision_intents i
      JOIN canonical_intent_executions e ON e.intent_id = i.intent_id
     WHERE e.adapter = $1 AND e.intent_kind = 'DECISION'
       AND i.created_at >= to_timestamp($2) AND i.created_at <= to_timestamp($3)
"""

PROBE_SQL = """
    SELECT x.k, extract(epoch FROM b.observed_at)::float8 AS at, b.bids,
           b.offers, b.error
      FROM unnest($1::text[], $2::text[], $3::float8[], $4::float8[])
           AS x(k, slug, t0, t1)
      JOIN LATERAL (SELECT observed_at, bids, offers, error
                      FROM paper_book_observations p
                     WHERE p.us_market_slug = x.slug
                       AND p.observed_at >= to_timestamp(x.t0)
                       AND p.observed_at <= to_timestamp(x.t1)
                       AND p.error IS NULL AND p.bids IS NOT NULL
                     ORDER BY p.observed_at LIMIT 1) b ON true
"""


async def _books(conn, ids) -> dict:
    ids = sorted({int(i) for i in ids if i is not None})
    if not ids:
        return {}
    return {r["obs_id"]: dict(r) for r in await conn.fetch(
        "SELECT obs_id, us_market_slug, bids, offers, error, "
        "       extract(epoch FROM observed_at)::float8 AS at "
        "  FROM paper_book_observations WHERE obs_id = ANY($1::bigint[])",
        ids)}


async def _adverse(conn, refs: list) -> dict:
    """refs: [(key, slug, side, t_ref, mid_ref)] -> {key: {h: value}} and
    the reason per missing horizon. One bounded read for every probe."""
    ks, slugs, t0s, t1s, meta = [], [], [], [], {}
    for key, slug, side, t, mid in refs:
        if t is None or mid is None:
            continue
        for h in HORIZONS:
            k = "%s|%d" % (key, int(h))
            ks.append(k)
            slugs.append(slug)
            t0s.append(float(t) + h)
            t1s.append(float(t) + h + HORIZON_TOLERANCE_S[h])
            meta[k] = (key, side, h, mid)
    out: dict = {key: {} for key, *_ in refs}
    if ks:
        for r in await conn.fetch(PROBE_SQL, ks, slugs, t0s, t1s):
            key, side, h, mid = meta[r["k"]]
            m1 = side_mid(book_md(dict(r)), side)
            if m1 is not None:
                out[key][h] = round(mid - m1, 9)
    return out


def _count(d: dict, k: str) -> None:
    d[k] = d.get(k, 0) + 1


def _cluster(intent: dict) -> str:
    return OT.opportunity_key(intent)["event_key"]


def read_block(rows: list, *, cap: int, in_window: int, at_key: str) -> dict:
    """What a bounded read covered: the cap, the rows in the window and
    read, whether older rows were left unread, and the oldest instant read.
    Pure."""
    ats = [r.get(at_key) for r in rows if r.get(at_key) is not None]
    return {"cap": cap, "rows_in_window": int(in_window),
            "rows_read": len(rows),
            "truncated": int(in_window) > len(rows),
            "kept": "MOST_RECENT_FIRST",
            "oldest_read_at": min(ats) if ats else None}


async def _intents(conn, adapter, *, since, until, excluded: dict) -> tuple:
    rows = [dict(r) for r in await conn.fetch(INTENT_SQL, adapter, since,
                                              until, MAX_INTENTS)]
    total = len(rows)
    if total >= MAX_INTENTS:
        total = int(await conn.fetchval(INTENT_COUNT_SQL, adapter, since,
                                        until) or 0)
    rd = read_block(rows, cap=MAX_INTENTS, in_window=total,
                    at_key="decided_at")
    if rd["truncated"]:
        excluded["NOT_READ_OLDER_THAN_THE_%d_MOST_RECENT" % MAX_INTENTS] = (
            total - len(rows))
    return rows, rd


async def paper_observations(conn, *, since, until) -> tuple:
    """(observations, excluded, read) of the PAPER_SIMULATION class."""
    excluded: dict = {}
    rows, rd = await _intents(conn, "PAPER", since=since, until=until,
                              excluded=excluded)
    oids = {}
    for r in rows:
        refs = _j(r["refs"]) or {}
        if r["state"] != "PAPER_SUBMITTED" or not refs.get("order_id"):
            _count(excluded, "%s:%s" % (r["state"], r.get("exclusion")))
            continue
        oids[refs["order_id"]] = r
    orders = {o["order_id"]: dict(o) for o in await conn.fetch(
        "SELECT order_id, state, qty, filled_qty, limit_price, order_type, "
        "       time_in_force FROM paper_orders WHERE order_id = ANY($1)",
        list(oids))} if oids else {}
    fills: dict = {}
    if oids:
        for f in await conn.fetch(
                "SELECT order_id, qty, price, book_obs_id, "
                "       extract(epoch FROM filled_at)::float8 AS at "
                "  FROM paper_fills WHERE order_id = ANY($1) "
                " ORDER BY filled_at, fill_id", list(oids)):
            fills.setdefault(f["order_id"], []).append(dict(f))
    books = await _books(conn, [(_j(r["evidence"]) or {}).get("book_obs_id")
                                for r in oids.values()]
                         + [fs[0]["book_obs_id"] for fs in fills.values()])
    obs, refs_for_adverse = [], []
    for oid, r in oids.items():
        o = orders.get(oid)
        if o is None:
            _count(excluded, "PAPER_ORDER_NOT_FOUND")
            continue
        if o["state"] not in PAPER_TERMINAL:
            _count(excluded, "ORDER_NOT_TERMINAL")
            continue
        side = r["holding_side"]
        ev = _j(r["evidence"]) or {}
        fs = fills.get(oid) or []
        fq = sum(float(f["qty"]) for f in fs)
        vwap = (sum(float(f["qty"]) * float(f["price"]) for f in fs) / fq
                if fq > 0 else None)
        dec_md = book_md(books.get(ev.get("book_obs_id")))
        ref_at = fs[0]["at"] if fs else None
        mid_ref = side_mid(book_md(books.get(fs[0]["book_obs_id"])), side) \
            if fs else None
        intent = dict(r, contract=_j(r["contract"]), evidence=ev)
        x = {"cls": EE.PAPER_SIMULATION, "intent_id": r["intent_id"],
             "opportunity_id": OT.opportunity_key(intent)["opportunity_id"],
             "cluster": _cluster(intent), "decided_at": r["decided_at"],
             "terminal": True, "requested_qty": float(o["qty"]),
             "filled_qty": fq, "full": fq >= float(o["qty"]) - 1e-9,
             "any": fq > 1e-9, "vwap": vwap,
             "limit": _num(o["limit_price"]),
             "best_at_decision": best_acquisition(dec_md, side),
             "cancelled_remainder": o["state"] in ("CANCELED", "EXPIRED"),
             "order_type": o["order_type"], "tif": o["time_in_force"]}
        obs.append(x)
        refs_for_adverse.append((r["intent_id"], r["us_market_slug"], side,
                                 ref_at, mid_ref))
    adv = await _adverse(conn, refs_for_adverse)
    for x in obs:
        x["adverse"] = adv.get(x["intent_id"]) or {}
    EC.mark_recoveries(obs)
    return obs, excluded, rd


async def shadow_observations(conn, *, since, until) -> tuple:
    """(observations, excluded, read) of the LIVE_SHADOW class."""
    excluded: dict = {}
    rows, rd = await _intents(conn, "SMALL_LIVE", since=since, until=until,
                              excluded=excluded)
    keep = []
    for r in rows:
        if r.get("mode") != "SHADOW":
            _count(excluded, "NOT_A_SHADOW_EXECUTION")
            continue
        if r["state"] != "SHADOW_PROPOSED":
            _count(excluded, "%s:%s" % (r["state"], r.get("exclusion")))
            continue
        req = _j(r["requested"]) or {}
        if str(req.get("order_type")) == "RESTING" or \
                str(req.get("time_in_force")) == "GTD":
            _count(excluded, "RESTING_SHADOW_FILL_NOT_MODELLED_QUEUE_UNKNOWN")
            continue
        keep.append(r)
    books = await _books(conn, [(_j(r["evidence"]) or {}).get("book_obs_id")
                                for r in keep])
    obs, refs_for_adverse = [], []
    for r in keep:
        req = _j(r["requested"]) or {}
        ev = _j(r["evidence"]) or {}
        side = r["holding_side"]
        md = book_md(books.get(ev.get("book_obs_id")))
        got = shadow_fill(md, holding_side=side, qty=req.get("qty"),
                          limit=req.get("limit_price"),
                          time_in_force=req.get("time_in_force"))
        if not got["ok"]:
            _count(excluded, got["why"])
            continue
        intent = dict(r, contract=_j(r["contract"]), evidence=ev)
        fq = got["filled_qty"]
        x = {"cls": EE.LIVE_SHADOW, "intent_id": r["intent_id"],
             "opportunity_id": OT.opportunity_key(intent)["opportunity_id"],
             "cluster": _cluster(intent), "decided_at": r["decided_at"],
             "terminal": True, "requested_qty": got["requested_qty"],
             "filled_qty": fq,
             "full": fq >= got["requested_qty"] - 1e-9, "any": fq > 1e-9,
             "vwap": got["vwap"], "limit": _num(req.get("limit_price")),
             "best_at_decision": got["best"],
             "cancelled_remainder": fq < got["requested_qty"] - 1e-9,
             "order_type": req.get("order_type"),
             "tif": req.get("time_in_force")}
        obs.append(x)
        refs_for_adverse.append((r["intent_id"], r["us_market_slug"], side,
                                 r["decided_at"] if fq > 0 else None,
                                 side_mid(md, side)))
    adv = await _adverse(conn, refs_for_adverse)
    for x in obs:
        x["adverse"] = adv.get(x["intent_id"]) or {}
    EC.mark_recoveries(obs)
    return obs, excluded, rd


async def actual_observations(conn, *, since, until) -> tuple:
    """(observations, excluded, integrity, read) from
    small_live_order_events (the most recent MAX_EVENTS of the window). An
    event on a SHADOW (or non-SMALL_LIVE) execution is an INTEGRITY
    VIOLATION: counted, never used."""
    rows = [dict(r) for r in await conn.fetch(
        """SELECT ev.event_id, ev.execution_id, ev.state, ev.cum_qty,
                  ev.avg_price, extract(epoch FROM ev.observed_at)::float8
                  AS at, e.adapter, e.mode, e.requested, e.intent_id,
                  i.us_market_slug, i.holding_side, i.contract, i.evidence,
                  i.strategy, i.sleeve,
                  extract(epoch FROM i.created_at)::float8 AS decided_at
             FROM small_live_order_events ev
             JOIN canonical_intent_executions e USING (execution_id)
             LEFT JOIN canonical_decision_intents i ON i.intent_id = e.intent_id
            WHERE ev.observed_at >= to_timestamp($1)
              AND ev.observed_at <= to_timestamp($2)
            ORDER BY ev.observed_at DESC, ev.event_id DESC LIMIT $3""",
        since, until, MAX_EVENTS)]
    rows.sort(key=lambda r: (r["execution_id"], r["at"], r["event_id"]))
    total = len(rows)
    if total >= MAX_EVENTS:
        total = int(await conn.fetchval(
            "SELECT count(*) FROM small_live_order_events WHERE observed_at "
            ">= to_timestamp($1) AND observed_at <= to_timestamp($2)",
            since, until) or 0)
    rd = read_block(rows, cap=MAX_EVENTS, in_window=total, at_key="at")
    integrity = sum(1 for r in rows if r["adapter"] != "SMALL_LIVE"
                    or r["mode"] == "SHADOW")
    live = [r for r in rows if r["adapter"] == "SMALL_LIVE"
            and r["mode"] != "SHADOW"
            and str(r.get("intent_id") or "").startswith("cdi_")
            and r.get("holding_side")]
    by: dict = {}
    for r in live:
        by.setdefault(r["execution_id"], []).append(r)
    excluded: dict = {}
    if rd["truncated"]:
        excluded["NOT_READ_OLDER_THAN_THE_%d_MOST_RECENT_EVENTS"
                 % MAX_EVENTS] = total - len(rows)
    obs, refs_for_adverse = [], []
    for eid, evs in by.items():
        last = evs[-1]
        if last["state"] not in ACTUAL_TERMINAL:
            _count(excluded, "VENUE_ORDER_NOT_TERMINAL")
            continue
        req = _j(last["requested"]) or {}
        q = _num(req.get("qty")) or 0.0
        cum = _num(last["cum_qty"]) or 0.0
        first_fill = next((e for e in evs if (_num(e["cum_qty"]) or 0) > 0),
                          None)
        intent = dict(last, contract=_j(last["contract"]),
                      evidence=_j(last["evidence"]) or {})
        x = {"cls": EE.ACTUAL, "intent_id": last["intent_id"],
             "opportunity_id": OT.opportunity_key(intent)["opportunity_id"],
             "cluster": _cluster(intent), "decided_at": last["decided_at"],
             "terminal": True, "requested_qty": q, "filled_qty": cum,
             "full": q > 0 and cum >= q - 1e-9, "any": cum > 1e-9,
             "vwap": _num(last["avg_price"]),
             "limit": _num(req.get("limit_price")),
             "best_at_decision": None,
             "cancelled_remainder": last["state"] == "CANCELLED"
             and cum < q - 1e-9,
             "order_type": req.get("order_type"),
             "tif": req.get("time_in_force")}
        obs.append(x)
        if first_fill is not None:
            mid_row = await conn.fetchrow(
                "SELECT bids, offers, error FROM paper_book_observations "
                " WHERE us_market_slug = $1 AND observed_at <= "
                "       to_timestamp($2) AND error IS NULL "
                " ORDER BY observed_at DESC LIMIT 1",
                last["us_market_slug"], first_fill["at"])
            refs_for_adverse.append((
                last["intent_id"], last["us_market_slug"],
                last["holding_side"], first_fill["at"],
                side_mid(book_md(dict(mid_row) if mid_row else None),
                         last["holding_side"])))
    adv = await _adverse(conn, refs_for_adverse)
    for x in obs:
        x["adverse"] = adv.get(x["intent_id"]) or {}
    EC.mark_recoveries(obs)
    return obs, excluded, integrity, rd


async def eddie_fit(conn, *, now: float) -> dict:
    """What Eddie's LIVE-path estimates are fitted on right now: his
    recorded history (PAPER_SIMULATION), per style, with the class and LIVE
    intervals."""
    from ..agents import eddie as E
    try:
        h = await E.history_stats(conn, now=now)
    except Exception as exc:                                  # noqa: BLE001
        return {"status": EE.UNAVAILABLE,
                "why": "EDDIE_HISTORY_UNREADABLE:%s" % type(exc).__name__}
    return {"status": "OK", "fitted_on": h.get("evidence_class"),
            "styles": {s: E.execution_evidence(h, s)
                       for s in (E.TAKER, E.MAKER)}}


async def report(conn, *, since: float | None, until: float | None,
                 now: float) -> dict:
    """THE THREE CLASSES SIDE BY SIDE over canonical decision intents in
    [since, until] (default: the last WINDOW_DAYS). SELECTs only; the caller
    holds the READ ONLY transaction."""
    until = float(until if until is not None else now)
    since = float(since if since is not None
                  else until - WINDOW_DAYS * 86400.0)
    p_obs, p_ex, p_rd = await paper_observations(conn, since=since,
                                                 until=until)
    s_obs, s_ex, s_rd = await shadow_observations(conn, since=since,
                                                  until=until)
    a_obs, a_ex, integrity, a_rd = await actual_observations(
        conn, since=since, until=until)
    classes = {
        EE.PAPER_SIMULATION: EC.summarise(EE.PAPER_SIMULATION, p_obs, p_ex),
        EE.LIVE_SHADOW: EC.summarise(EE.LIVE_SHADOW, s_obs, s_ex),
        EE.ACTUAL: EC.summarise(EE.ACTUAL, a_obs, a_ex,
                             unavailable_why=EE.R_NO_ACTUAL),
    }
    for cls, rd in ((EE.PAPER_SIMULATION, p_rd), (EE.LIVE_SHADOW, s_rd),
                    (EE.ACTUAL, a_rd)):
        classes[cls]["read"] = rd
    classes[EE.ACTUAL]["integrity_violations"] = integrity
    if integrity:
        classes[EE.ACTUAL]["integrity_why"] = (
            "VENUE_EVENTS_ON_A_SHADOW_OR_NON_LIVE_EXECUTION_COUNTED_NEVER_"
            "USED")
    return {"version": EC.VERSION, "window": {"since": since, "until": until,
                                           "days": round((until - since)
                                                         / 86400.0, 3)},
            "classes": classes, "live_estimates": EC.live_estimates(classes),
            "truncated": any(c["read"]["truncated"]
                             for c in classes.values()),
            "estimates_in_use": EC.estimates_in_use(),
            "eddie_fit": await eddie_fit(conn, now=now),
            "rule": EE.RULE, "rule_sha": EE.RULE_SHA,
            "pooled_across_classes": False, "computed_at": now}


async def _read(conn, *, since, until, now: float) -> dict:
    nested = conn.is_in_transaction()
    tr = conn.transaction(readonly=not nested)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        if not await conn.fetchval(
                "SELECT to_regclass('canonical_intent_executions') "
                "IS NOT NULL"):
            return {"status": "UNAVAILABLE",
                    "why": "MIGRATION_225_NOT_APPLIED", "data": None}
        data = await report(conn, since=since, until=until, now=now)
    finally:
        await tr.rollback()
    return {"status": "OK", "why": None, "data": data}


@router.get(PATH, dependencies=[Depends(require_read)])
async def execution_calibration(
        since: float | None = Query(default=None, ge=0),
        until: float | None = Query(default=None, ge=0)) -> dict:
    from ..profitability import common as C
    now = time.time()
    key = (None if since is None else round(float(since), 3),
           None if until is None else round(float(until), 3))
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            got = await _read(conn, since=since, until=until, now=now)
    except Exception as exc:                                    # noqa: BLE001
        return C.envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                     str(exc)[:160]),
                          data=None)
    out = C.envelope(got["status"], got.get("why"), computed_at=now,
                     data=got.get("data"), pooled_across_classes=False,
                     simulated_fills_are_live_proof=False)
    _CACHE[key] = (now, out)
    return out
