"""THE OPPORTUNITY SCORE V1 / V2 SHADOW TOURNAMENT:
GET /api/command/opportunity-score-tournament (GET only, COMMAND auth via
agents_core.require_read). READ ONLY.

    ?since=<epoch>   a later forward start (never before the R30 production
                     cutover recorded in live_parity_cutover; with no
                     cutover there is no forward window and the status is
                     NOT_ESTABLISHED)

ANSWERS (the profitability envelope), data = opportunity_tournament.compute:
    {status, why, sleeve: INVESTMENT, since, cutover, entries,
     counts: {opportunities, resolved, unresolved, excluded, reevaluations,
              unresolved_why, excluded_why},
     coverage: {v1_measured, v2_measured, v1_unavailable_why,
                v2_unavailable_why},
     common: {V1, V2: {n_opportunities, n_independent_events,
                       rank_correlation {value, ci_low, ci_high},
                       top_k_realized_net {value_mean_usd, k, ci_low,
                                           ci_high},
                       calibration {mean_predicted_usd, mean_realized_usd,
                                    slope_realized_on_predicted, buckets}}},
     own: {V1, V2: the same on each score's own resolved set},
     current_series, series: [{tournament_version, v1_version, v2_version,
                               v2_spec_sha, entries, first_decided_at,
                               current, evaluated}],
     series_excluded: {reason: count}, read: {cap, rows_in_window,
     rows_read, truncated, kept: MOST_RECENT_FIRST}, truncated,
     paired_v2_minus_v1: {rank_correlation_diff, top_k_mean_diff_usd},
     promotion_evidence: {status, checks, automatic_promotion: false,
                          authority_granted: NONE},
     promotion_rule, enter_rule_changed: false, v2_authority}

THE OUTCOME JOIN reads: opportunity_score_tournament (migration 300), each
intent's PAPER adapter record (canonical_intent_executions, migration 225)
-> its paper ENTRY order -> the paper ledger's positions
(bettor_paper_ledger.positions: realized P&L after fees, exactly as the
profitability validation reads it). INVESTMENT sleeve only; forward only;
ONE SPEC SERIES (opportunity_tournament.current_series) -- the per-series
counts and the first sightings of each opportunity / event come from
bounded aggregate reads over the whole forward window, so a bounded entry
read can never make a re-evaluation look like a first decision.

BOUNDED AND SAID SO: at most MAX_ENTRIES rows, the MOST RECENT first; when
the window holds more, `read.truncated` is true with the cap and the count,
and an opportunity whose first decision fell outside the read is excluded
by name (never scored from a later re-evaluation).

Everything runs inside a READ ONLY transaction under a statement timeout.
This module imports no order, venue, execution or funded module and writes
nothing. V2 has no authority here or anywhere.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from .. import opportunity_tournament as OT
from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/opportunity-score-tournament"
STATEMENT_TIMEOUT_MS = 8000
CACHE_S = 15.0
_CACHE: dict = {}

ENTRY_COLS = (
    "entry_id", "tournament_version", "intent_id", "intent_version",
    "decision_id", "opportunity_id", "opportunity_key_version", "event_key",
    "strategy", "sleeve", "us_market_slug", "holding_side", "v1_version",
    "v1_status",
    "v1_score", "v1_predicted_net_usd", "v1_why", "v2_version",
    "v2_spec_sha", "v2_status", "v2_score", "v2_predicted_net_lcb_usd",
    "v2_why")
MAX_ENTRIES = 20000


async def cutover_epoch(conn):
    """THE EFFECTIVE production cutover (epoch seconds), or None.

    (R30 tails integration) Written against the R30 singleton
    (`live_parity_cutover WHERE id = 1`). R30A made migration 225 one row
    per release, stamped by the database clock, with the forward window's
    start in the view live_parity_effective_cutover -- the same read
    command_validation.production_cutover_epoch and the confidence ladder
    make. A release that did not change decision logic does not restart the
    window; one that did restarts it."""
    if not await conn.fetchval(
            "SELECT to_regclass('live_parity_effective_cutover') IS NOT NULL"):
        return None
    return await conn.fetchval(
        "SELECT extract(epoch FROM cutover_at)::float8 FROM "
        " live_parity_effective_cutover")


async def series_summary(conn, *, since: float) -> dict:
    """{series: {entries, first, last}} over the WHOLE INVESTMENT forward
    window (one aggregate read)."""
    out = {}
    for r in await conn.fetch(
            "SELECT tournament_version, v1_version, v2_version, v2_spec_sha,"
            "       count(*) AS n, "
            "       extract(epoch FROM min(decided_at))::float8 AS first, "
            "       extract(epoch FROM max(decided_at))::float8 AS last "
            "  FROM opportunity_score_tournament "
            " WHERE sleeve = $1 AND decided_at >= to_timestamp($2) "
            " GROUP BY 1, 2, 3, 4", OT.INVESTMENT, float(since)):
        out[OT.series_of(dict(r))] = {"entries": int(r["n"]),
                                      "first": r["first"], "last": r["last"]}
    return out


async def first_seen(conn, *, since: float, opportunity_ids: list,
                     event_keys: list) -> dict:
    """The first forward sighting of each read opportunity (its first
    INVESTMENT row: instant, intent, series) and of each read event (its
    first INVESTMENT row, any series -- the population the series is
    evaluated on, exactly as opportunity_tournament.first_seen_of derives
    it). Two bounded reads keyed by the read rows."""
    opp, ev = {}, {}
    if opportunity_ids:
        for r in await conn.fetch(
                "SELECT DISTINCT ON (opportunity_id) opportunity_id, "
                "       intent_id, tournament_version, v1_version, "
                "       v2_version, v2_spec_sha, "
                "       extract(epoch FROM decided_at)::float8 AS decided_at "
                "  FROM opportunity_score_tournament "
                " WHERE sleeve = $1 AND decided_at >= to_timestamp($2) "
                "   AND opportunity_id = ANY($3::text[]) "
                " ORDER BY opportunity_id, decided_at, intent_id",
                OT.INVESTMENT, float(since), list(opportunity_ids)):
            opp[r["opportunity_id"]] = {"decided_at": r["decided_at"],
                                        "intent_id": r["intent_id"],
                                        "series": OT.series_of(dict(r))}
    if event_keys:
        for r in await conn.fetch(
                "SELECT event_key, "
                "       extract(epoch FROM min(decided_at))::float8 AS t "
                "  FROM opportunity_score_tournament "
                " WHERE sleeve = $1 AND decided_at >= to_timestamp($2) "
                "   AND event_key = ANY($3::text[]) GROUP BY 1",
                OT.INVESTMENT, float(since), list(event_keys)):
            ev[r["event_key"]] = r["t"]
    return {"opportunity": opp, "event": ev}


async def gather(conn, *, since: float) -> tuple:
    """(entries, outcomes, context) for opportunity_tournament.compute:
    the most recent MAX_ENTRIES INVESTMENT tournament rows decided at or
    after `since`, each intent's paper outcome
    (opportunity_tournament.intent_outcome) from its PAPER adapter record's
    order and the paper ledger, and the context compute() needs over the
    whole window (series_summary, first_seen, read). SELECTs only."""
    from .. import bettor_paper_ledger as L
    entries = [dict(r) for r in await conn.fetch(
        "SELECT %s, extract(epoch FROM decided_at)::float8 AS decided_at "
        "  FROM opportunity_score_tournament "
        " WHERE sleeve = $1 AND decided_at >= to_timestamp($2) "
        " ORDER BY decided_at DESC, intent_id DESC LIMIT $3"
        % ", ".join(ENTRY_COLS), OT.INVESTMENT, float(since), MAX_ENTRIES)]
    entries.reverse()
    ssum = await series_summary(conn, since=since)
    in_window = sum(v["entries"] for v in ssum.values())
    read = {"cap": MAX_ENTRIES, "rows_in_window": in_window,
            "rows_read": len(entries),
            "truncated": in_window > len(entries),
            "kept": "MOST_RECENT_FIRST",
            "oldest_read_at": entries[0]["decided_at"] if entries else None}
    fs = await first_seen(
        conn, since=since,
        opportunity_ids=sorted({e["opportunity_id"] for e in entries}),
        event_keys=sorted({e["event_key"] for e in entries
                           if e.get("event_key")}))
    ids = [e["intent_id"] for e in entries]
    orders = {}
    if ids:
        for r in await conn.fetch(
                """SELECT e.intent_id, o.order_id, o.account_id, o.group_id,
                          o.state, o.filled_qty
                     FROM canonical_intent_executions e
                     JOIN paper_orders o ON o.order_id = e.refs->>'order_id'
                    WHERE e.adapter = 'PAPER' AND e.intent_id = ANY($1)""",
                ids):
            orders[r["intent_id"]] = dict(r)
    by_group: dict = {}
    for acct in sorted({o["account_id"] for o in orders.values()}):
        for p in await L.positions(conn, acct, include_closed=True):
            by_group.setdefault(p["group_id"], []).append(p)
    outcomes = {iid: OT.intent_outcome(orders.get(iid), by_group.get(
        (orders.get(iid) or {}).get("group_id"), [])) for iid in ids}
    return entries, outcomes, {"series_summary": ssum, "first_seen": fs,
                               "read": read}


async def report(conn, *, since: float | None, now: float) -> dict:
    """The tournament since the production cutover (or a later `since`).
    SELECTs only; the caller holds the READ ONLY transaction."""
    cut = await cutover_epoch(conn)
    if cut is None:
        # no forward window: show what is recorded, conclude nothing
        out = OT.compute([], {}, since=None, cutover=None)
        out["entries_recorded"] = int(await conn.fetchval(
            "SELECT count(*) FROM opportunity_score_tournament") or 0)
        out["computed_at"] = now
        return out
    start = max(float(since), float(cut)) if since is not None else float(cut)
    entries, outcomes, ctx = await gather(conn, since=start)
    out = OT.compute(entries, outcomes, since=start, cutover=float(cut),
                     **ctx)
    out["computed_at"] = now
    return out


async def _read(conn, *, since, now: float) -> dict:
    import asyncio
    nested = conn.is_in_transaction()
    tr = conn.transaction(readonly=not nested)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        if not await conn.fetchval(
                "SELECT to_regclass('opportunity_score_tournament') "
                "IS NOT NULL"):
            return {"status": "UNAVAILABLE",
                    "why": "MIGRATION_300_NOT_APPLIED", "data": None}
        cut = await cutover_epoch(conn)
        if cut is None:
            data = await report(conn, since=since, now=now)
        else:
            start = (max(float(since), float(cut)) if since is not None
                     else float(cut))
            entries, outcomes, ctx = await gather(conn, since=start)
            data = await asyncio.to_thread(
                OT.compute, entries, outcomes, since=start,
                cutover=float(cut), **ctx)
            data["computed_at"] = now
    finally:
        await tr.rollback()
    return {"status": "OK" if data.get("status") == "OK" else
            data.get("status"), "why": data.get("why"), "data": data}


@router.get(PATH, dependencies=[Depends(require_read)])
async def opportunity_score_tournament(
        since: float | None = Query(default=None, ge=0)) -> dict:
    from ..profitability import common as C
    now = time.time()
    key = None if since is None else round(float(since), 3)
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            got = await _read(conn, since=None if since is None
                              else float(since), now=now)
    except Exception as exc:                                    # noqa: BLE001
        return C.envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                     str(exc)[:160]),
                          data=None, since=since)
    out = C.envelope(got["status"], got.get("why"), computed_at=now,
                     data=got.get("data"), since=since,
                     authority_of_v2="NONE", shadow_only=True)
    _CACHE[key] = (now, out)
    return out
