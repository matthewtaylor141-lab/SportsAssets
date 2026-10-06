"""THE PRODUCTION CANARY'S CHECKS, SHARED BY THE SCRIPT AND THE API.

scripts/bettor_canary.py needs a DSN for the production database, and none is
held outside Render. Its restart, checkpoint and no-order logic lives here so
`GET /api/command/canary` (api/command_canary.py) answers the same questions
from inside the API process, through one READ ONLY transaction, and the
script and the endpoint cannot drift apart.

WHAT IS ANSWERED, EACH WITH ITS SOURCE, NEVER WITH A GUESS:

  boots        per-service process identity: the bettor live journal's
               boot_id history (first/last row, row counts), runtime_loop_
               health's (process, commit_sha, host, pid) identities, the
               workers_boot marker (workers/all.py), service_heartbeats.
  checkpoint   the latest durable checkpoint -- bettor_live_ledger (written
               by the live loop's store) -- and the current snapshot in the
               script's checkpoint shape, plus the scalars a caller can hand
               back after a restart (before_max_id / before_rows /
               before_cursors / before_boots) for a checkpoint-compared
               restart verdict without a DSN (`restart_verdict`).
  cursors      did any per-market cursor REGRESS across the last restart:
               every market the journal decided on in the last two boots has
               a cursor, and no cursor is behind the journal's own record of
               the newest source timestamp / receipt it wrote for that market.
  retention    ingestion_state.retention_last (workers/retention.py): fresh,
               not refused, and no pinned table holds a row older than its
               window plus one cycle.
  no_order     structural evidence that no live order exists since the
               latest cutover: SMALL LIVE mode SHADOW (or halted = STOPPED),
               the other live controls disabled or stopped, zero venue
               orders in every live-order table since the cutover, zero
               executed or sized records in our own journal, and the
               workers' venue-write lock as the boot marker read it.

EVERY VERDICT IS PASS, FAIL OR NOT_ESTABLISHED. An unreadable or absent
source is NOT_ESTABLISHED with its reason -- never a pass. This module issues
SELECTs only and imports no order, venue, execution, ledger or paper module
(tests/test_ops_canary.py walks its imports and its SQL).
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "NOT_ESTABLISHED"
VERSION = "OPS_CANARY_V1"

# THE LANE AND THE RESOLVED STATUS, as bettor_live_store names them (a test
# pins the equality, so this module need not import the store).
LANE = "polymarket-us/institutional/decision-only"
SETTLE_RESOLVED = "RESOLVED"

# the script's thresholds (scripts/bettor_canary.py re-exports these)
MAX_SOURCE_AGE_S = 10.0
MAX_RECEIPT_AGE_S = 5.0
SETTLEMENT_EVERY_S = 600.0
MIN_FRESH_DECISIONS = 10
RSS_GROWTH_PCT = 15.0
CHECKPOINT_KEYS = 200

BOOT_HISTORY = 12
# a cursor's last_seen_at is stamped when the market is SEEN; the journal row
# is written after the decision, so the receipt comparison allows this slack
CURSOR_SEEN_SLACK_S = 120.0
# retention_last is written hourly (workers/retention.EVERY_S = 3600)
RETENTION_EVERY_S = 3600.0
RETENTION_STALE_AFTER_S = 3 * RETENTION_EVERY_S
# the pinned pair and its consumer floors (workers/retention.TABLES; pinned
# by a test so the two cannot drift)
RETENTION_TABLES = (("ai_trades", "placed_at", 97),
                    ("copy_probes", "probe_at", 37))
HEARTBEAT_STALE_S = 900.0

_ISO = r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"


def state_of(ok):
    return PASS if ok is True else (FAIL if ok is False else UNKNOWN)


def _iso(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(v)


def _unavailable(why, **extra):
    return {"state": UNKNOWN, "status": "UNAVAILABLE", "why": why} | extra


async def _section(conn, fn):
    """Each source in its own savepoint: an absent table is reported with
    its reason and never aborts the rest of the read."""
    try:
        async with conn.transaction():
            return await fn(conn)
    except Exception as exc:                                    # noqa: BLE001
        return _unavailable("%s: %s" % (type(exc).__name__, str(exc)[:160]))


async def _has(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


# ── shared with scripts/bettor_canary.py ─────────────────────────────

async def snapshot(con, lane: str = LANE) -> dict:
    """What a restart has to preserve (the script's checkpoint shape, plus
    the journal's max id so a caller without the keys can compare)."""
    rows = await con.fetch(
        "SELECT record_key FROM bettor_live_journal WHERE lane = $1 "
        " ORDER BY id DESC LIMIT $2", lane, CHECKPOINT_KEYS)
    boots = await con.fetch(
        "SELECT DISTINCT boot_id FROM bettor_live_journal WHERE lane = $1 "
        "   AND boot_id IS NOT NULL", lane)
    cur = await con.fetchrow(
        "SELECT count(*) AS n, "
        "       count(*) FILTER (WHERE settle_status IS DISTINCT FROM $2) "
        "         AS outstanding FROM bettor_live_cursor WHERE lane = $1",
        lane, SETTLE_RESOLVED)
    led = await con.fetchrow(
        "SELECT boot_id, saved_at FROM bettor_live_ledger WHERE lane = $1",
        lane)
    j = await con.fetchrow(
        "SELECT count(*) AS n, max(id) AS max_id FROM bettor_live_journal "
        " WHERE lane = $1", lane)
    keys = [r["record_key"] for r in rows]
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "journal_rows": j["n"],
        "journal_max_id": j["max_id"],
        "record_keys": keys,
        "record_keys_sha256": hashlib.sha256(
            "\n".join(keys).encode("utf-8")).hexdigest(),
        "boot_ids": sorted(b["boot_id"] for b in boots),
        "cursors": cur["n"], "outstanding": cur["outstanding"],
        "ledger_boot_id": led["boot_id"] if led else None,
        "ledger_saved_at": str(led["saved_at"]) if led else None,
    }


def restart_verdict(before: dict, after: dict, *, still_present: int,
                    expected_present: int) -> tuple[str, str, list]:
    """THE RESTART RULE, ONE COPY: a NEW boot_id since the checkpoint, every
    checkpointed record still present, and neither the journal row count nor
    the cursor count going backwards. Returns (state, why, new_boots)."""
    new_boots = sorted(set(after.get("boot_ids") or ())
                       - set(before.get("boot_ids") or ()))
    if not new_boots:
        return (FAIL, "NO NEW PROCESS BOOT: the worker did not restart, so "
                "this window cannot establish recovery", new_boots)
    lost = expected_present - still_present
    rows_back = (after.get("journal_rows") or 0) < (before.get("journal_rows")
                                                    or 0)
    cur_back = (after.get("cursors") or 0) < (before.get("cursors") or 0)
    ok = lost == 0 and not rows_back and not cur_back
    why = ("required: a NEW boot_id, every checkpointed record still present, "
           "and neither the row count nor the cursor count going backwards"
           + ("" if ok else "; LOST %d checkpointed record(s)%s%s" % (
               max(lost, 0), ", journal rows went BACKWARDS" if rows_back
               else "", ", cursors went BACKWARDS" if cur_back else "")))
    return state_of(ok), why, new_boots


async def journal_no_order(con, lane: str = LANE) -> dict:
    """Our OWN journal: records claiming an execution, records with a
    non-zero size (over ALL time). Not a venue-account audit."""
    ex = await con.fetchval(
        "SELECT count(*) FROM bettor_live_journal "
        " WHERE lane = $1 AND (record->>'executed') = 'true'", lane)
    sized = await con.fetchval(
        "SELECT count(*) FROM bettor_live_journal WHERE lane = $1 "
        "   AND COALESCE((record->>'size_contracts')::float, 0) <> 0", lane)
    return {"executed": ex, "sized": sized,
            "state": state_of(ex == 0 and sized == 0)}


# ── boots ────────────────────────────────────────────────────────────

async def journal_boots(conn, lane: str = LANE,
                        limit: int = BOOT_HISTORY) -> dict:
    async def fn(c):
        rows = await c.fetch(
            "SELECT boot_id, count(*) AS rows, min(id) AS first_id, "
            "       max(id) AS last_id, min(written_at) AS first_at, "
            "       max(written_at) AS last_at "
            "  FROM bettor_live_journal "
            " WHERE lane = $1 AND boot_id IS NOT NULL "
            " GROUP BY boot_id ORDER BY min(id) DESC LIMIT $2", lane, limit)
        items = [{"boot_id": r["boot_id"], "rows": r["rows"],
                  "first_id": r["first_id"], "last_id": r["last_id"],
                  "first_at": _iso(r["first_at"]),
                  "last_at": _iso(r["last_at"])} for r in rows]
        return {"status": "OK", "lane": lane, "newest_first": items,
                "source": "bettor_live_journal.boot_id (one per worker "
                          "process boot of the live loop)"}
    return await _section(conn, fn)


async def loop_identities(conn) -> dict:
    async def fn(c):
        rows = await c.fetch(
            "SELECT process, commit_sha, host, pid, count(*) AS loops, "
            "       min(last_start_at) AS first_start, "
            "       max(updated_at) AS last_update "
            "  FROM runtime_loop_health "
            " GROUP BY process, commit_sha, host, pid "
            " ORDER BY max(updated_at) DESC LIMIT 40")
        by = {}
        for r in rows:
            by.setdefault(r["process"], []).append({
                "commit_sha": r["commit_sha"], "host": r["host"],
                "pid": r["pid"], "loops": r["loops"],
                "first_start": _iso(r["first_start"]),
                "last_update": _iso(r["last_update"])})
        return {"status": "OK", "by_process": by,
                "source": "runtime_loop_health (one current row per loop "
                          "and process; identity = commit, host, pid)"}
    return await _section(conn, fn)


async def workers_boot(conn) -> dict:
    async def fn(c):
        v = await c.fetchval(
            "SELECT value FROM ingestion_state WHERE key = 'workers_boot'")
        if v is None:
            return _unavailable("no workers_boot row")
        v = json.loads(v) if isinstance(v, str) else (v or {})
        return {"status": "OK", "commit_sha": v.get("commit_sha"),
                "commit": v.get("commit"), "at": v.get("at"),
                "venue_writes": v.get("venue_writes"),
                "lock_reason": v.get("lock_reason"),
                "source": "ingestion_state.workers_boot (workers/all.py)"}
    return await _section(conn, fn)


async def heartbeats(conn) -> dict:
    async def fn(c):
        rows = await c.fetch(
            "SELECT service, status, beat_at, "
            "       extract(epoch FROM now() - beat_at) AS age_s, "
            "       detail->>'boot_id' AS boot_id, "
            "       COALESCE(detail->>'commit_sha', detail->>'commit') "
            "         AS commit FROM service_heartbeats ORDER BY service")
        items = [{"service": r["service"], "status": r["status"],
                  "beat_at": _iso(r["beat_at"]),
                  "age_s": round(float(r["age_s"]), 1)
                  if r["age_s"] is not None else None,
                  "stale": r["age_s"] is None
                  or float(r["age_s"]) > HEARTBEAT_STALE_S,
                  "boot_id": r["boot_id"], "commit": r["commit"]}
                 for r in rows]
        return {"status": "OK", "items": items,
                "stale": [i["service"] for i in items if i["stale"]],
                "stale_after_s": HEARTBEAT_STALE_S,
                "source": "service_heartbeats (db.heartbeat)"}
    return await _section(conn, fn)


# ── checkpoint ───────────────────────────────────────────────────────

async def checkpoint(conn, boots: dict, lane: str = LANE) -> dict:
    async def fn(c):
        led = await c.fetchrow(
            "SELECT boot_id, saved_at, loop_version, schema_version, "
            "       length(snapshot) AS bytes FROM bettor_live_ledger "
            " WHERE lane = $1", lane)
        snap = await snapshot(c, lane)
        newest = ((boots or {}).get("newest_first") or [None])[0]
        out = {"status": "OK", "lane": lane,
               "ledger": None if led is None else {
                   "boot_id": led["boot_id"], "saved_at": _iso(led["saved_at"]),
                   "loop_version": led["loop_version"],
                   "schema_version": led["schema_version"],
                   "bytes": led["bytes"]},
               "snapshot": {k: v for k, v in snap.items()
                            if k != "record_keys"},
               "compare_after_restart": {
                   "before_max_id": snap["journal_max_id"],
                   "before_rows": snap["journal_rows"],
                   "before_cursors": snap["cursors"],
                   "before_boots": ",".join(snap["boot_ids"][-BOOT_HISTORY:])},
               "source": "bettor_live_ledger (the live loop's durable "
                         "checkpoint) + bettor_live_journal / _cursor"}
        if led is None:
            out.update(state=UNKNOWN, why="no ledger row: the live loop has "
                       "never checkpointed against this database")
        elif newest is None:
            out.update(state=UNKNOWN, why="no journal boot to compare with")
        else:
            cur_boot = led["boot_id"] == newest["boot_id"]
            out.update(state=state_of(cur_boot),
                       written_by_current_boot=cur_boot,
                       why=None if cur_boot else
                       "the newest checkpoint was written by boot %s, not by "
                       "the newest boot %s: the running process has not "
                       "checkpointed" % (led["boot_id"], newest["boot_id"]))
        return out
    return await _section(conn, fn)


async def restart_against(conn, before: dict, lane: str = LANE) -> dict:
    """`restart_verdict` against caller-supplied scalars from an earlier
    canary read (no record keys, no DSN): rows with id <= before_max_id are
    the checkpointed records, and every one must still be there."""
    async def fn(c):
        after = await snapshot(c, lane)
        still = await c.fetchval(
            "SELECT count(*) FROM bettor_live_journal "
            " WHERE lane = $1 AND id <= $2", lane, int(before["max_id"]))
        st, why, new = restart_verdict(
            {"boot_ids": before.get("boots") or [],
             "journal_rows": before.get("rows"),
             "cursors": before.get("cursors")},
            after, still_present=still,
            expected_present=int(before.get("rows") or 0))
        return {"status": "OK", "state": st, "why": why, "new_boots": new,
                "checkpointed_rows_still_present": still,
                "checkpointed_rows": before.get("rows"),
                "journal_rows_now": after["journal_rows"],
                "cursors_now": after["cursors"]}
    return await _section(conn, fn)


# ── cursors ──────────────────────────────────────────────────────────

CURSOR_SQL = (
    "WITH j AS ("
    "  SELECT market_id, "
    "         max(CASE WHEN source_ts ~ $3 THEN source_ts::timestamptz END) "
    "           AS j_source, "
    "         max(written_at) AS j_written "
    "    FROM bettor_live_journal "
    "   WHERE lane = $1 AND kind = 'DECISION' AND market_id IS NOT NULL "
    "     AND boot_id = ANY($2::text[]) "
    "   GROUP BY market_id) "
    "SELECT count(*) AS markets, "
    "  count(*) FILTER (WHERE c.market_id IS NULL) AS missing, "
    "  count(*) FILTER (WHERE c.last_source_ts ~ $3 AND j.j_source IS NOT NULL "
    "    AND c.last_source_ts::timestamptz < j.j_source) AS behind_source, "
    "  count(*) FILTER (WHERE c.last_seen_at IS NOT NULL "
    "    AND c.last_seen_at < extract(epoch FROM j.j_written) - $4) "
    "    AS behind_seen "
    "  FROM j LEFT JOIN bettor_live_cursor c "
    "    ON c.lane = $1 AND c.market_id = j.market_id")


async def cursors(conn, boots: dict, lane: str = LANE) -> dict:
    items = (boots or {}).get("newest_first") or []
    if len(items) < 2:
        return _unavailable("fewer than two boots in the journal: there is no "
                            "restart to compare across", boots_seen=len(items))
    cur, prev = items[0], items[1]

    async def fn(c):
        r = await c.fetchrow(CURSOR_SQL, lane,
                             [cur["boot_id"], prev["boot_id"]], _ISO,
                             CURSOR_SEEN_SLACK_S)
        regress = (r["missing"] or 0) + (r["behind_source"] or 0) \
            + (r["behind_seen"] or 0)
        overlap = (prev["last_id"] or 0) > (cur["first_id"] or 0)
        out = {"status": "OK",
               "restart": {"from_boot": prev["boot_id"],
                           "to_boot": cur["boot_id"],
                           "previous_last_at": prev["last_at"],
                           "current_first_at": cur["first_at"],
                           "writers_overlapped": overlap},
               "markets_compared": r["markets"],
               "missing_cursor": r["missing"],
               "behind_journal_source_ts": r["behind_source"],
               "behind_journal_receipt": r["behind_seen"],
               "seen_slack_s": CURSOR_SEEN_SLACK_S,
               "rule": "every market the journal decided on in the last two "
                       "boots has a cursor, and no cursor is behind the "
                       "newest source timestamp / receipt the journal wrote "
                       "for it",
               "source": "bettor_live_cursor against bettor_live_journal"}
        if not r["markets"]:
            out.update(state=UNKNOWN, why="no DECISION rows in the last two "
                       "boots to compare cursors against")
        else:
            out.update(state=state_of(regress == 0),
                       why=None if regress == 0 else
                       "%d cursor(s) regressed across the restart" % regress)
        return out
    return await _section(conn, fn)


# ── retention ────────────────────────────────────────────────────────

async def retention(conn) -> dict:
    async def fn(c):
        v = await c.fetchval(
            "SELECT value FROM ingestion_state WHERE key = 'retention_last'")
        if v is None:
            return _unavailable("no retention_last row: the retention loop "
                                "has never completed a cycle here")
        v = json.loads(v) if isinstance(v, str) else (v or {})
        db_now = await c.fetchval("SELECT now()")
        at = v.get("at")
        age = None
        try:
            age = (db_now - datetime.fromisoformat(at)).total_seconds()
        except (TypeError, ValueError):
            pass
        over = {}
        for table, col, floor in RETENTION_TABLES:
            keep = ((v.get("tables") or {}).get(table) or {}).get("keep_days")
            keep = float(keep) if keep else float(floor)
            if not await _has(c, table):
                over[table] = {"status": "ABSENT"}
                continue
            # the pinned names are constants of this module, never input
            n = await c.fetchval(
                "SELECT count(*) FROM (SELECT 1 FROM " + table + " WHERE "
                + col + " < now() - make_interval(secs => $1) LIMIT 1000) t",
                keep * 86400.0 + 2 * RETENTION_EVERY_S)
            over[table] = {"keep_days": keep, "rows_past_window": n}
        refused = v.get("refused")
        off = refused == "RETENTION=off"
        past = sum(int(x.get("rows_past_window") or 0) for x in over.values())
        fresh = age is not None and age <= RETENTION_STALE_AFTER_S
        if off:
            state, why = UNKNOWN, "retention is switched off (RETENTION=off)"
        elif not fresh:
            state, why = FAIL, ("the last cycle is %s old (> %ss)"
                                % ("?" if age is None else "%.0fs" % age,
                                   int(RETENTION_STALE_AFTER_S)))
        elif refused:
            state, why = FAIL, "the last cycle refused: %s" % refused
        else:
            state, why = state_of(past == 0), (
                None if past == 0 else
                "%d row(s) older than the window plus two cycles" % past)
        return {"status": "OK", "state": state, "why": why,
                "last_cycle_at": at,
                "age_s": None if age is None else round(age, 1),
                "deleted_total": v.get("deleted_total"),
                "capped": v.get("capped"), "refused": refused,
                "tables": over,
                "source": "ingestion_state.retention_last "
                          "(workers/retention.py) + the pinned tables"}
    return await _section(conn, fn)


# ── no order ─────────────────────────────────────────────────────────

# (name, table, timestamp column, predicate that marks a VENUE order)
LIVE_ORDER_SOURCES = (
    ("small_live_order_events", "small_live_order_events", "observed_at",
     "true"),
    ("canonical_small_live_sent", "canonical_intent_executions", "created_at",
     "adapter = 'SMALL_LIVE' AND (mode <> 'SHADOW' OR refs ? 'venue_order_id')"),
    ("live_orders", "live_orders", "placed_at", "true"),
    ("funded_venue_orders", "bettor_funded_intents", "created_at",
     "venue_order_id IS NOT NULL"),
    ("execmirror_venue_orders", "execmirror_orders",
     "COALESCE(submit_started_at, accepted_at)",
     "venue_order_id IS NOT NULL OR submit_started_at IS NOT NULL"),
    ("kalshi_venue_orders", "kalshi_live_intents",
     "COALESCE(submit_started_at, accepted_at, created_at)",
     "venue_order_id IS NOT NULL OR submit_started_at IS NOT NULL"),
)


async def no_order(conn, wboot: dict, lane: str = LANE) -> dict:
    out = {"source": "small_live_control, live_parity_cutover, the live "
                     "controls and every live-order table (SELECT only)"}

    async def control(c):
        r = await c.fetchrow(
            "SELECT mode, halted, halted_at, halt_reason "
            "  FROM small_live_control WHERE id = 1")
        if r is None:
            return _unavailable("no small_live_control row")
        eff = "STOPPED" if r["halted"] else r["mode"]
        return {"status": "OK", "mode": r["mode"], "halted": r["halted"],
                "effective": eff, "halt_reason": r["halt_reason"],
                "state": state_of(eff in ("SHADOW", "STOPPED"))}
    out["small_live"] = await _section(conn, control)

    async def cutover(c):
        # THE EFFECTIVE CUTOVER (migration 225's view: the latest release
        # whose decision logic changed) is the forward window every live-
        # parity reader uses; it is never later than the latest deployment's
        # row, so "since the cutover" is the wider, stricter window.
        r = await c.fetchrow(
            "SELECT cutover_id, release_sha, recorded_at, small_live_mode, "
            "       capital_activated FROM live_parity_effective_cutover")
        latest = await c.fetchrow(
            "SELECT cutover_id, release_sha, recorded_at "
            "  FROM live_parity_cutover "
            " ORDER BY recorded_at DESC, cutover_id DESC LIMIT 1")
        if r is None:
            return _unavailable("no cutover recorded")
        return {"status": "OK", "cutover_id": r["cutover_id"],
                "release_sha": r["release_sha"],
                "recorded_at": r["recorded_at"],
                "small_live_mode": r["small_live_mode"],
                "capital_activated": r["capital_activated"],
                "latest_deployment": None if latest is None else {
                    "cutover_id": latest["cutover_id"],
                    "release_sha": latest["release_sha"],
                    "recorded_at": _iso(latest["recorded_at"])}}
    cut = await _section(conn, cutover)
    since = cut.get("recorded_at")
    out["cutover"] = {k: (_iso(v) if k == "recorded_at" else v)
                      for k, v in cut.items()}

    async def controls(c):
        res = {}
        for name, table in (("execmirror", "execmirror_control"),
                            ("kalshi_smalllive", "kalshi_smalllive_control")):
            if not await _has(c, table):
                res[name] = {"status": "ABSENT"}
                continue
            r = await c.fetchrow("SELECT enabled, stopped FROM " + table
                                 + " WHERE id = 1")
            res[name] = {"status": "OK", "enabled": r["enabled"],
                         "stopped": r["stopped"],
                         "inert": (not r["enabled"]) or bool(r["stopped"])} \
                if r else {"status": "NO_ROW"}
        return {"status": "OK", "items": res,
                "state": state_of(all(v.get("inert", True)
                                      for v in res.values()))}
    out["controls"] = await _section(conn, controls)

    orders = {}
    for name, table, ts, pred in LIVE_ORDER_SOURCES:
        async def one(c, table=table, ts=ts, pred=pred):
            if not await _has(c, table):
                return {"status": "ABSENT"}
            # every identifier is a constant of LIVE_ORDER_SOURCES
            r = await c.fetchrow(
                "SELECT count(*) FILTER (WHERE " + pred + ") AS all_time, "
                "       count(*) FILTER (WHERE (" + pred + ") AND " + ts
                + " >= $1) AS since_cutover, max(" + ts + ") FILTER (WHERE "
                + pred + ") AS newest FROM " + table, since)
            return {"status": "OK", "all_time": r["all_time"],
                    "since_cutover": r["since_cutover"]
                    if since is not None else None,
                    "newest": _iso(r["newest"])}
        orders[name] = await _section(conn, one)
    out["live_orders"] = orders

    async def journal(c):
        if not await _has(c, "bettor_live_journal"):
            return {"status": "ABSENT", "state": UNKNOWN}
        return {"status": "OK"} | await journal_no_order(c, lane) | {
            "scope": "OUR OWN JOURNAL: not a venue-account audit"}
    out["journal"] = await _section(conn, journal)
    out["venue_writes"] = (wboot or {}).get("venue_writes")

    reasons, unknown = [], []
    sl = out["small_live"]
    if sl.get("state") == FAIL:
        reasons.append("SMALL LIVE mode is %s" % sl.get("effective"))
    elif sl.get("state") != PASS:
        unknown.append("small_live_control unreadable")
    if out["controls"].get("state") == FAIL:
        reasons.append("a live control is enabled and not stopped")
    if since is None:
        unknown.append("no cutover recorded, so 'since cutover' is undefined")
    for name, o in orders.items():
        if o.get("status") == "OK" and (o.get("since_cutover") or 0) > 0:
            reasons.append("%s: %s venue order(s) since the cutover"
                           % (name, o["since_cutover"]))
        elif o.get("status") not in ("OK", "ABSENT"):
            unknown.append("%s unreadable" % name)
    if out["journal"].get("state") == FAIL:
        reasons.append("our journal records an execution or a size")
    if out["venue_writes"] != "LOCKED":
        unknown.append("workers venue-write lock reads %r"
                       % out["venue_writes"])
    out["state"] = FAIL if reasons else (UNKNOWN if unknown else PASS)
    out["fail_reasons"], out["not_established"] = reasons, unknown
    return out


# ── the whole read ───────────────────────────────────────────────────

async def build(conn, *, lane: str = LANE, before: dict | None = None,
                api: dict | None = None) -> dict:
    jb = await journal_boots(conn, lane)
    wb = await workers_boot(conn)
    boots = {"journal": jb, "loops": await loop_identities(conn),
             "workers_boot": wb, "heartbeats": await heartbeats(conn),
             "api": api}
    ck = await checkpoint(conn, jb, lane)
    cu = await cursors(conn, jb, lane)
    rt = await retention(conn)
    no = await no_order(conn, wb, lane)
    checks = {"checkpoint_by_current_boot": ck.get("state", UNKNOWN),
              "cursors_never_regressed": cu.get("state", UNKNOWN),
              "retention": rt.get("state", UNKNOWN),
              "no_order": no.get("state", UNKNOWN)}
    out = {"version": VERSION, "read_only": True,
           "generated_at": _iso(datetime.now(timezone.utc)),
           "generated_at_epoch": time.time(),
           "boots": boots, "checkpoint": ck, "cursors": cu,
           "retention": rt, "no_order": no}
    if before is not None:
        out["restart"] = await restart_against(conn, before, lane)
        checks["restart_retained_records"] = out["restart"].get("state",
                                                                UNKNOWN)
    vals = list(checks.values())
    out["checks"] = checks
    out["verdict"] = (FAIL if FAIL in vals else
                      UNKNOWN if UNKNOWN in vals else PASS)
    out["scope"] = ("the observation instrument and the no-order structure; "
                    "not a venue-account audit and not a profitability result")
    return out
