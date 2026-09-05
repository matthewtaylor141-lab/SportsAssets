"""Worker: RETENTION for the two measurement tables that filled the disk.

THE INCIDENT (2026-09-04 23:15Z to 2026-09-05 ~14:30Z). sportsassets-db
(Render Postgres, 15 GB) filled. Render degraded the instance, its
internal hostname stopped resolving, and every service logged
`DB connect failed ([Errno -2] Name or service not known)` while
/healthz served {"ok": true, "db_ok": false}. The disk was raised to
25 GB through render.yaml (the dashboard refused a manual change), and
the dominant write source -- the paper trader, copy_probe.py -> ai_trades
plus a copy_probes row per probe, ~21,000 ai_trades rows/day across
seven whales including five funded at $0 (render.yaml's own count) --
is disabled by env until this exists. Neither table needs unbounded
history to do its job; this loop is the retention policy render.yaml
says is "the actual fix".

WHAT IT TOUCHES, AND NOTHING ELSE. Exactly two tables, pinned in TABLES
and re-checked against the frozen PINNED pair on every cycle:

    ai_trades    by placed_at   (index ai_trades_placed_idx, migration 006)
    copy_probes  by probe_at    (index copy_probes_probe_at_idx, migration 053;
                                 005's only index is led by whale_id and
                                 cannot serve an oldest-first scan)

A cycle whose plan names any other table, or whose window or clock
cannot be read, DELETES NOTHING and writes why to the state row. The
tests scan this module's source for table names, so a third table
cannot be added quietly.

THE WINDOWS ARE DERIVED FROM THE CONSUMERS, not invented. Every reader
of the two tables (grep 2026-09-05):

  ai_trades
    /api/ai-trader          app.py    days <= 90 on placed_at (summary) and
                                      on settled_at (daily) -- the longest
                                      BOUNDED consumer. The probe asks 30.
    /api/admin/whale-true-edge  app.py  placed_at >= since_day, default
                                      '2026-08-01': UNBOUNDED (a fixed date
                                      that drifts further back every day).
    /api/admin/true-edge-cashout app.py  same shape, same default: UNBOUNDED.
    settle_ai_trades        analytics/engine.py  status open/missed rows of
                                      any age, graded when their market
                                      resolves.
  copy_probes
    /api/copy-report        app.py    hours <= 720 (30 days) on probe_at.
                                      The probe asks 720.

So ai_trades keeps AI_TRADES_FLOOR_DAYS = 90 + 7 and copy_probes keeps
COPY_PROBES_FLOOR_DAYS = 30 + 7. The 7 is slack for the two ways a
boundary read can come up short: /api/ai-trader's daily leg windows on
settled_at, which trails placed_at by however long the game took to
resolve, and the probe is run by hand at odd hours, so a request at the
edge must not see a half-pruned day. The two admin endpoints read
unbounded history by default and WILL lose rows older than 97 days:
they are admin diagnostics over a paper account (the same measurement
the disk could not hold), 97 days is more than the whole life of the
copy program so far, and the alternative -- keeping everything -- is
the outage. An operator who needs longer passes since_day inside the
window; the state row carries oldest_kept so the truncation is visible.
Rows are pruned by timestamp REGARDLESS of status: an open paper trade
older than the window is a market that never resolved (or whose
resolution never arrived), and nothing served reads it.

The env may RAISE either window (RETENTION_AI_TRADES_DAYS,
RETENTION_COPY_PROBES_DAYS) but never lower it below the consumer floor:
a value below the floor, or one that does not parse, refuses the whole
cycle. A misconfigured env deletes nothing.

MECHANICS. Once an hour (RETENTION_EVERY_S), per table, in batches:

    DELETE FROM <t> WHERE id IN (SELECT id FROM <t> WHERE <ts> < $1
                                 ORDER BY <ts> LIMIT $2)

$1 is the cutoff computed HERE from one clock reading per cycle, never
now() inside the statement, so every batch of a cycle agrees on the
boundary -- and that clock is checked against the database's own
now() first, because the rows are stamped by Postgres and a container
booted with a wrong clock is the one input that turns a window into
"everything" (disagreement above CLOCK_SKEW_MAX_S refuses the cycle).
Each statement carries asyncpg's per-statement timeout, a short sleep
separates batches, and a per-cycle cap on rows
(RETENTION_MAX_ROWS_PER_CYCLE, split equally across the tables so the
first cannot starve the second) means one cycle cannot run for hours:
whatever the cap leaves is DISCLOSED (capped=true) and drains next hour.
The off-switch RETENTION=off is read from the environment on every
cycle, not at import, so it takes effect without a redeploy; so are the
batch, cap and cadence knobs, parse-or-refuse: a value that does not
parse or sits below 1 refuses that cycle, so a typo cannot kill the
whole workers process at import and a zero cannot become LIMIT 0
forever (the first build did exactly that; three reviewers reproduced
it on 2026-09-05).

Every DELETE runs in its own implicit transaction: short locks, and a
timeout that fires loses one batch, not the cycle. The index check reads
pg_indexes before touching a table, because the workers never run
migrations (migrate runs at API boot, best-effort, "serving anyway"): a
table whose oldest-first scan has no index is skipped with the reason
rather than hammered with an hourly sequential scan that only times out.

WHAT DELETE DOES NOT DO. A DELETE frees space for REUSE by later inserts
once autovacuum has been over the dead tuples; it does not hand disk
back to the OS (that needs VACUUM FULL, which takes an exclusive lock
and a second copy of the table, and is an operator decision, not a
loop's). The point of this loop is that the tables stop GROWING; the
25 GB stays the ceiling.

THE STATE ROW. ingestion_state['retention_last'] is written after every
cycle, refusals included: per-table deleted count, cutoff, oldest kept,
batches, the cycle's duration, and the refusal reason if there was one.
The same payload rides the 'retention' service heartbeat, because no
endpoint serves that state key and /api/health/services is what the
probe can read. A refusal that cannot even write its row is logged and
contained -- it never unwinds into the supervisor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

from ..db import get_pool, heartbeat

log = logging.getLogger(__name__)

STATE_KEY = "retention_last"
HEARTBEAT_SERVICE = "retention"

# The consumer-derived floors (derivation in the module docstring). An
# env override may only raise these.
AI_TRADES_FLOOR_DAYS = 90 + 7
COPY_PROBES_FLOOR_DAYS = 30 + 7

# THE PINNED PAIR: (table, timestamp column, floor in days, env override).
# TABLES is the plan; PINNED is what the plan is checked against on
# every cycle. Two separate objects on purpose -- a future edit that
# adds a table to one without the other refuses instead of deleting.
TABLES: tuple[tuple[str, str, int, str], ...] = (
    ("ai_trades", "placed_at", AI_TRADES_FLOOR_DAYS, "RETENTION_AI_TRADES_DAYS"),
    ("copy_probes", "probe_at", COPY_PROBES_FLOOR_DAYS, "RETENTION_COPY_PROBES_DAYS"),
)
PINNED: frozenset[tuple[str, str]] = frozenset({
    ("ai_trades", "placed_at"),
    ("copy_probes", "probe_at"),
})

BOOT_DELAY_S = 180.0                     # let the pool and the writers settle
# THE KNOBS' DEFAULTS. The live values come from read_knobs() on every
# cycle -- parse-or-refuse, the shape read_windows() already has -- and
# are never int()'d at import, where one malformed dashboard value
# would raise while workers/all.py imports this module and take EVERY
# loop down before supervise() exists (containment review 2026-09-05).
# A batch or cap below 1 is refused rather than used: LIMIT 0 deletes
# nothing and, unguarded, never drains -- the first build looped on it
# (476,794 empty DELETEs in 3 s with RETENTION_BATCH_ROWS=0, no state
# row, no heartbeat, and a kill switch that is read per cycle could not
# stop a cycle that never ended).
EVERY_S = 3600.0                         # RETENTION_EVERY_S
BATCH_ROWS = 5000                        # RETENTION_BATCH_ROWS
BATCH_SLEEP_S = 0.25
STATEMENT_TIMEOUT_S = 30.0
# Total rows per cycle across BOTH tables. At BATCH_ROWS a table's share
# is 20 batches; with every statement bounded by STATEMENT_TIMEOUT_S the
# worst cycle is minutes, not hours. The backlog drains hourly.
MAX_ROWS_PER_CYCLE = 200000              # RETENTION_MAX_ROWS_PER_CYCLE
# The database's clock, read once per cycle and compared with ours:
# ai_trades.placed_at is DEFAULT now() (006, the DATABASE's clock) and
# the cutoff is subtracted from THIS process's clock. Nothing else
# relates the two. Disagreement beyond this is a refusal, both clocks
# recorded, because a wrong worker clock is the one input on which a
# correct window deletes rows the endpoints still serve, up to the cap,
# hour after hour (money-safety review 2026-09-05).
CLOCK_SQL = "SELECT now()"
CLOCK_SKEW_MAX_S = 300.0

_sleep = asyncio.sleep


def _now() -> datetime:
    """The cycle's one clock reading. Module-level so a test can make
    the clock unreadable and watch the cycle refuse."""
    return datetime.now(tz=timezone.utc)


def enabled(env: dict | None = None) -> bool:
    """RETENTION=off is the kill switch; anything else (including unset)
    is on. Read at call time, never cached at import, so a flip in the
    service's env takes effect on the next cycle without a redeploy."""
    env = os.environ if env is None else env
    return str(env.get("RETENTION", "on")).strip().lower() not in ("off", "0", "false", "no")


class Refusal(Exception):
    """A cycle that must delete nothing, with the reason it will record."""


def read_windows(env: dict | None = None) -> dict[str, int]:
    """{table: keep_days} for the pinned plan, from the floors and the
    env overrides. Refuses (deleting nothing) when an override does not
    parse or sits BELOW the consumer floor: the floor is what the
    longest consumer can ask for, so a lower value is data loss the
    endpoints would serve as an empty window."""
    env = os.environ if env is None else env
    out: dict[str, int] = {}
    for table, _col, floor, env_key in TABLES:
        raw = env.get(env_key)
        if raw is None or str(raw).strip() == "":
            out[table] = int(floor)
            continue
        try:
            days = int(str(raw).strip())
        except ValueError:
            raise Refusal(f"window unreadable: {env_key}={raw!r} is not an integer")
        if days < floor:
            raise Refusal(f"window below consumer floor: {env_key}={days} < {floor} days")
        out[table] = days
    return out


_KNOBS: tuple[tuple[str, str, type], ...] = (
    ("RETENTION_EVERY_S", "every_s", float),
    ("RETENTION_BATCH_ROWS", "batch", int),
    ("RETENTION_MAX_ROWS_PER_CYCLE", "cap", int),
)


def read_knobs(env: dict | None = None) -> dict:
    """{every_s, batch, cap} from the module defaults and the env, read
    at call time. Refuses when a value does not parse or is below 1: a
    batch of 0 is LIMIT 0, which deletes nothing and never drains; a
    cap of 0 is a cycle that can do nothing; an unparseable value is a
    typo, not an order. An empty string is unset."""
    env = os.environ if env is None else env
    defaults = {"every_s": EVERY_S, "batch": BATCH_ROWS, "cap": MAX_ROWS_PER_CYCLE}
    out: dict = {}
    for env_key, name, typ in _KNOBS:
        raw = env.get(env_key)
        if raw is None or str(raw).strip() == "":
            val = defaults[name]
        else:
            try:
                val = typ(str(raw).strip())
            except ValueError:
                raise Refusal(f"knob unreadable: {env_key}={raw!r} is not a number")
        if not (val >= 1):                    # NaN fails this comparison too
            raise Refusal(f"knob below 1: {env_key}={val!r} is not a bound")
        out[name] = val
    return out


def check_plan(tables=None) -> None:
    """The plan must be EXACTLY the pinned pair -- same tables, same
    timestamp columns, nothing extra, nothing missing."""
    plan = TABLES if tables is None else tables
    named = {(t, c) for t, c, *_ in plan}
    if named != PINNED:
        raise Refusal(f"plan does not match the pinned pair: {sorted(named)} vs {sorted(PINNED)}")
    for t, c, *_ in plan:
        if not re.fullmatch(r"[a-z_]+", t) or not re.fullmatch(r"[a-z_]+", c):
            raise Refusal(f"plan names an identifier that is not a bare lowercase word: {t}.{c}")


def delete_sql(table: str, ts_col: str) -> str:
    """The one DELETE shape this loop ever runs. Identifiers are checked
    against the pinned pair before they reach here; the values are
    bound parameters."""
    if (table, ts_col) not in PINNED:
        raise Refusal(f"refusing to build a DELETE for {table}.{ts_col}")
    return (f"DELETE FROM {table} WHERE id IN "
            f"(SELECT id FROM {table} WHERE {ts_col} < $1 "
            f"ORDER BY {ts_col} LIMIT $2)")


def oldest_sql(table: str, ts_col: str) -> str:
    if (table, ts_col) not in PINNED:
        raise Refusal(f"refusing to build a read for {table}.{ts_col}")
    return f"SELECT {ts_col} FROM {table} ORDER BY {ts_col} ASC LIMIT 1"


def index_serves(indexdefs: list[str], ts_col: str) -> bool:
    """Does one of the table's indexes lead with ts_col alone? A btree
    on (ts_col) or (ts_col DESC) serves both the oldest-first scan and
    the oldest-kept read; one led by another column (005's
    (whale_id, probe_at DESC)) does not."""
    pat = re.compile(r"\(\s*" + re.escape(ts_col) + r"\s*(ASC|DESC)?\s*\)", re.I)
    return any(pat.search(d or "") for d in indexdefs)


def _deleted_count(status) -> int:
    """asyncpg's status tag ('DELETE 5000') -> 5000. Anything else is
    an unreadable result and raises, so a caller stops rather than
    loops on a number it does not have."""
    parts = str(status or "").split()
    if len(parts) != 2 or parts[0] != "DELETE":
        raise Refusal(f"unreadable DELETE status {status!r}")
    return int(parts[1])


async def prune_table(pool, table: str, ts_col: str, cutoff: datetime,
                      cap: int, batch: int | None = None) -> dict:
    """Delete rows of one pinned table older than cutoff, in bounded
    batches, up to cap rows. Returns the table's block of the state
    row. Never raises for a DB failure mid-way: the rows already gone
    are gone, the count is what it is, and the error is recorded.

    `batch` defaults to BATCH_ROWS at CALL time (a def-time default
    would have frozen the import-time value the knobs no longer have),
    and a batch or cap below 1 is refused before the first statement:
    LIMIT 0 is not a batch, it is the spin the first build had."""
    out: dict = {"deleted": 0, "batches": 0, "cutoff": cutoff.isoformat(timespec="seconds"),
                 "capped": False, "oldest_kept": None, "error": None}
    try:
        batch = BATCH_ROWS if batch is None else int(batch)
        if batch < 1 or int(cap) < 1:
            raise Refusal(f"batch {batch} / cap {cap}: a bound below 1 is not a bound")
        sql = delete_sql(table, ts_col)
        defs = await pool.fetch(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = $1 LIMIT 50", table, timeout=STATEMENT_TIMEOUT_S)
        if not index_serves([r["indexdef"] for r in defs], ts_col):
            out["error"] = (f"no index leads with {ts_col}: migration unapplied? "
                            "skipped rather than sequential-scan hourly")
            return out
        while out["deleted"] < cap:
            n = min(batch, cap - out["deleted"])
            if n < 1:                       # belt: never issue LIMIT 0
                raise Refusal(f"batch would be {n}")
            status = await pool.execute(sql, cutoff, n, timeout=STATEMENT_TIMEOUT_S)
            got = _deleted_count(status)
            out["batches"] += 1
            out["deleted"] += got
            if got < n:
                break                       # the table is drained to the cutoff
            await _sleep(BATCH_SLEEP_S)
        else:
            out["capped"] = True
    except Exception as exc:  # noqa: BLE001 -- recorded, never propagated
        out["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    try:
        oldest = await pool.fetchval(oldest_sql(table, ts_col), timeout=STATEMENT_TIMEOUT_S)
        out["oldest_kept"] = (oldest.isoformat(timespec="seconds")
                              if isinstance(oldest, datetime) else None)
    except Exception as exc:  # noqa: BLE001 -- a read that cannot be made is not a result
        out["oldest_kept_err"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


async def write_state(pool, payload: dict) -> None:
    """The state row and the heartbeat, both best-effort and both
    CONTAINED: a refusal must be able to record itself, and a database
    that cannot take the record must not turn the refusal into a crash
    of the supervised loop."""
    body = json.dumps(payload, default=str)
    try:
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            STATE_KEY, body, timeout=STATEMENT_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        log.exception("retention: state row write failed")
    # 'off' is the owner's switch, not a fault; 'refused' is a cycle
    # that could not establish its own preconditions and so did nothing.
    refused = payload.get("refused")
    status = "off" if refused == "RETENTION=off" else ("refused" if refused else "ok")
    try:
        await heartbeat(HEARTBEAT_SERVICE, status, payload)
    except Exception:  # noqa: BLE001
        log.exception("retention: heartbeat write failed")


async def run_once(pool, env: dict | None = None) -> dict:
    """One cycle. Deletes nothing unless the switch is on, the plan is
    the pinned pair, the windows are readable and above their floors,
    and the clock is readable. Always writes the state row."""
    t0 = time.monotonic()
    payload: dict = {"at": None, "db_clock": None, "clock_skew_s": None,
                     "enabled": enabled(env), "refused": None,
                     "capped": False, "deleted_total": 0, "tables": {},
                     "duration_s": 0.0}
    try:
        try:
            now = _now()
        except Exception as exc:  # noqa: BLE001 -- no clock, no cutoff, no delete
            raise Refusal(f"clock unreadable: {type(exc).__name__}: {str(exc)[:80]}")
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise Refusal(f"clock unreadable: {now!r} is not an aware datetime")
        payload["at"] = now.isoformat(timespec="seconds")
        if not payload["enabled"]:
            raise Refusal("RETENTION=off")
        check_plan()
        windows = read_windows(env)
        knobs = read_knobs(env)
        share = int(knobs["cap"]) // len(TABLES)
        if share < 1:
            raise Refusal(f"cap below the pair: RETENTION_MAX_ROWS_PER_CYCLE="
                          f"{knobs['cap']} leaves no rows per table")
        # The database's clock, once, before the first DELETE: the rows
        # are stamped by Postgres and the cutoff by this container, and
        # only this read relates the two (CLOCK_SKEW_MAX_S above).
        try:
            db_now = await pool.fetchval(CLOCK_SQL, timeout=STATEMENT_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 -- no DB clock, no cutoff, no delete
            raise Refusal(f"db clock unreadable: {type(exc).__name__}: {str(exc)[:80]}")
        if not isinstance(db_now, datetime) or db_now.tzinfo is None:
            raise Refusal(f"db clock unreadable: {db_now!r} is not an aware datetime")
        payload["db_clock"] = db_now.isoformat(timespec="seconds")
        skew = (db_now - now).total_seconds()
        payload["clock_skew_s"] = round(skew, 3)
        if abs(skew) > CLOCK_SKEW_MAX_S:
            raise Refusal(f"clock skew: worker {payload['at']} vs database "
                          f"{payload['db_clock']} differ by {skew:.0f}s "
                          f"(> {CLOCK_SKEW_MAX_S:.0f}s)")
        for table, col, _floor, _env_key in TABLES:
            keep = windows[table]
            block = await prune_table(pool, table, col, now - timedelta(days=keep),
                                      share, batch=knobs["batch"])
            block["keep_days"] = keep
            payload["tables"][table] = block
            payload["deleted_total"] += block["deleted"]
            payload["capped"] = payload["capped"] or block["capped"]
    except Refusal as r:
        payload["refused"] = str(r)
        log.warning("retention: cycle refused: %s", r)
    except Exception as exc:  # noqa: BLE001 -- unknown = refuse, recorded
        payload["refused"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        log.exception("retention: cycle failed")
    # A cycle that failed before it could read the clock still stamps
    # the row, from a second reading if one can be had, so the probe
    # can tell "refused at T" from "never ran".
    if payload["at"] is None:
        try:
            payload["at"] = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        except Exception:  # noqa: BLE001
            payload["at"] = None
    payload["duration_s"] = round(time.monotonic() - t0, 3)
    await write_state(pool, payload)
    return payload


async def main() -> None:
    await _sleep(BOOT_DELAY_S)
    pool = await get_pool()
    log.info("retention up: tables=%s every=%ss batch=%s cap=%s",
             [t for t, *_ in TABLES], EVERY_S, BATCH_ROWS, MAX_ROWS_PER_CYCLE)
    while True:
        try:
            result = await run_once(pool)
            log.info("retention: %s", {k: v for k, v in result.items() if k != "tables"})
        except Exception:  # noqa: BLE001 -- run_once contains its own failures; belt
            log.exception("retention cycle raised past its own guard")
        # The cadence is a knob too, read here so a dashboard change
        # takes effect without a redeploy; a knob the cycle refused is
        # unreadable, and the default paces the retry.
        try:
            every = read_knobs()["every_s"]
        except Refusal:
            every = EVERY_S
        await _sleep(every)


if __name__ == "__main__":
    asyncio.run(main())
