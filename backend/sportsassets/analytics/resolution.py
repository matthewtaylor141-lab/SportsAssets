"""Resolution tracking.

Primary signal: Gamma `closed` + collapsed outcome prices (0/1), polled for
every market a tracked whale has touched that isn't resolved yet. The
metadata refresher keeps active markets fresh; this sweep specifically chases
markets that left the active set (i.e. just closed/resolved).

On new resolutions the analytics cycle re-runs, realizing remaining shares
at $1/$0 and refreshing rollups — this is the "recompute on resolution
events" path from the spec.

THE SWEEP STARVED (E13, 2026-09-08). `unresolved_traded_condition_ids`
selected `DISTINCT t.condition_id ... LIMIT 500` with NO ORDER BY over
49,213 unresolved traded conditions (13,114 of them RN1's): Postgres
handed back the same 500 every cycle, the analytics beat read
`newly_resolved: 4` per five-minute cycle, and the gamma rows of markets
played 09-04 .. 09-07 still read closed=f resolved=f with `updated_at`
at the event day (the last time the active-set refresher saw them; the
refresher keeps ACTIVE markets only and this sweep is what chases the
rest). A mirror book ends on that row reading closed (step M) or on the
venue settling its standing row, so 40+ flat books stood 'live' for two
days and book 43's short of 1,042 stood frozen behind a row nobody
re-asked. THE ORDER NOW, de-duplicated, per call:

  (1) THE DESK'S MARKETS FIRST -- every unresolved condition a
      mirror_books row with state <> 'closed' or a live_orders row with
      status IN ('submitting', 'filled', 'exiting') references, ALL of
      them, never cut by the limit (books first, then rows);
  (2) THEN NEWEST FIRST -- unresolved traded conditions by their newest
      trade `ts` DESC (the newest ended markets resolve first), read
      over the last NEWEST_WINDOW_S of trades on trades_ts_idx so the
      aggregate never scans the whole ledger;
  (3) THEN A ROTATING WINDOW over the remainder, ordered by condition_id
      from a cursor kept in ingestion_state (`resolution_sweep_cursor`:
      the last condition_id swept), advanced on every call, wrapped to
      the start when the end is reached -- so every unresolved
      condition is eventually re-asked. An absent, malformed or junk
      cursor starts at the beginning; a cursor that could not be
      written is retried next call (the rows are asked either way).

The total per call is bounded by `limit` past the desk's own set (the
desk's set is the open books plus the live rows: dozens). COST: the
gamma batch fetch chunks 40 condition_ids per request (two requests a
chunk at most: closed=true first, then the remainder), so a 500-row
call is at most 26 requests; the CLOB fallback reads the same function
at clob_batch=300 -- the same priority order, its own advance of the
cursor -- and is one request per condition, as before.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

from .. import gamma
from ..db import get_pool

log = logging.getLogger(__name__)

# the ingestion_state key the rotation's cursor lives under: {"cursor":
# <the last condition_id swept>, "at": <iso instant>}
SWEEP_CURSOR_KEY = "resolution_sweep_cursor"
# the newest-first window (2): the trades of the last 14 days, on
# trades_ts_idx -- a match plays out inside it; older markets are the
# rotation's (3). A constant, never a knob
NEWEST_WINDOW_S = 14 * 86400

_UNRESOLVED = "(m.condition_id IS NULL OR NOT m.resolved)"

# (1) the desk's markets: books first (rank 0), then the live rows (rank 1)
SQL_DESK = f"""
SELECT c.condition_id
  FROM (SELECT condition_id, min(rank) AS rank
          FROM (SELECT condition_id, 0 AS rank FROM mirror_books WHERE state <> 'closed'
                UNION ALL
                SELECT condition_id, 1 AS rank FROM live_orders
                 WHERE status IN ('submitting', 'filled', 'exiting')) d
         WHERE condition_id IS NOT NULL
         GROUP BY condition_id) c
  LEFT JOIN markets m ON m.condition_id = c.condition_id
 WHERE {_UNRESOLVED}
 ORDER BY c.rank, c.condition_id /* resolution-desk */
"""

# (2) newest trade first, inside the window
SQL_NEWEST = f"""
SELECT t.condition_id
  FROM trades t
  LEFT JOIN markets m ON m.condition_id = t.condition_id
 WHERE t.condition_id IS NOT NULL AND t.ts > $2::timestamptz AND {_UNRESOLVED}
 GROUP BY t.condition_id
 ORDER BY max(t.ts) DESC, t.condition_id
 LIMIT $1 /* resolution-newest */
"""

# (3) the rotation: by condition_id past the cursor, the ones already
# chosen this call left out
SQL_ROTATE = f"""
SELECT DISTINCT t.condition_id
  FROM trades t
  LEFT JOIN markets m ON m.condition_id = t.condition_id
 WHERE t.condition_id IS NOT NULL AND t.condition_id > $2 AND {_UNRESOLVED}
   AND NOT (t.condition_id = ANY($3::text[]))
 ORDER BY t.condition_id
 LIMIT $1 /* resolution-rotate */
"""

SQL_CURSOR_READ = "SELECT value FROM ingestion_state WHERE key = $1 /* resolution-cursor */"
SQL_CURSOR_WRITE = """
INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb)
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value /* resolution-cursor */
"""


def _cursor_of(raw: Any) -> str:
    """The stored cursor's condition_id, or '' (the start) for an absent,
    malformed or junk value: not JSON, not an object, no string `cursor`."""
    if raw is None:
        return ""
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            return ""
    if not isinstance(raw, dict):
        return ""
    cur = raw.get("cursor")
    return cur if isinstance(cur, str) else ""


async def _read_cursor(pool) -> str:
    try:
        return _cursor_of(await pool.fetchval(SQL_CURSOR_READ, SWEEP_CURSOR_KEY))
    except Exception as exc:  # noqa: BLE001 — an unreadable cursor is the start
        log.warning("resolution sweep cursor unreadable (%s); rotating from the start", exc)
        return ""


async def _write_cursor(pool, cursor: str) -> None:
    value = {"cursor": cursor, "at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")}
    try:
        await pool.execute(SQL_CURSOR_WRITE, SWEEP_CURSOR_KEY, json.dumps(value))
    except Exception as exc:  # noqa: BLE001 — retried next call; the rows were asked either way
        log.warning("resolution sweep cursor write failed (%s)", exc)


async def unresolved_traded_condition_ids(limit: int = 500) -> list[str]:
    """The unresolved conditions to re-ask this call, in the module's
    order: the desk's (all), then newest first, then the rotation --
    de-duplicated, the desk's set never cut, the rest up to `limit`."""
    pool = await get_pool()
    limit = max(0, int(limit))
    out: list[str] = []
    seen: set[str] = set()

    def _take(rows, room: int | None = None) -> int:
        """Append the rows' new condition_ids, at most `room` of them
        (None: all -- the desk's stage). Returns how many were taken."""
        n = 0
        for r in rows:
            if room is not None and n >= room:
                break
            cid = r["condition_id"]
            if isinstance(cid, str) and cid and cid not in seen:
                seen.add(cid)
                out.append(cid)
                n += 1
        return n

    # (1) the desk's, never cut
    try:
        _take(await pool.fetch(SQL_DESK))
    except Exception as exc:  # noqa: BLE001 — the desk's set unreadable: the rest is still asked
        log.warning("resolution sweep: the desk's conditions unreadable (%s)", exc)
    room = max(0, limit - len(out))
    if room == 0:
        return out
    # (2) newest first, inside the window (the desk's may be among the
    # newest: asked for with room to spare, taken up to the room)
    since = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(seconds=NEWEST_WINDOW_S)
    _take(await pool.fetch(SQL_NEWEST, room + len(out), since), room)
    room = max(0, limit - len(out))
    if room == 0:
        return out
    # (3) the rotation from the cursor, wrapped once at the end
    cursor = await _read_cursor(pool)
    last = cursor
    rows = await pool.fetch(SQL_ROTATE, room, cursor, out)
    got = _take(rows, room)
    if rows:
        last = str(rows[-1]["condition_id"])
    if got < room:
        # the end was reached: wrap to the start for what is left
        rows = await pool.fetch(SQL_ROTATE, room - got, "", out)
        _take(rows, room - got)
        last = str(rows[-1]["condition_id"]) if rows else ""
    if last != cursor:
        await _write_cursor(pool, last)
    return out


async def sweep_resolutions(client: gamma.GammaClient, clob_batch: int = 300) -> int:
    """Fetch metadata for unresolved traded markets; return count newly resolved.

    Two independent sources: Gamma (batch) first, then the CLOB API per-market
    — settlement must not hinge on one endpoint. Both read
    unresolved_traded_condition_ids, so both ask in its order: the desk's
    markets, then the newest, then the rotation. The desk's and the newest
    are asked by both passes; the rotation is NOT (E13 review F2): every
    call advances the cursor, so the CLOB pass's rotation window is the
    NEXT one, never the one gamma just asked, and a rotation-only market
    gamma asked and could not serve waits for a later cycle's CLOB window
    to land on it. Accepted: the windows drift across wraps (the desk's
    set and the newest window change size), so it does land; the desk's
    markets -- the only ones a book's close waits on -- are asked twice
    every cycle; and asking gamma's window again would halve the sweep's
    reach per cycle for a per-market fallback that costs one request each.
    """
    import httpx

    from ..clob import fetch_clob_market
    from ..config import settings

    condition_ids = await unresolved_traded_condition_ids()
    if not condition_ids:
        return 0
    pool = await get_pool()
    before = await pool.fetchval("SELECT count(*) FROM markets WHERE resolved")

    try:
        raws = await client.fetch_by_condition_ids(condition_ids)
        for raw in raws:
            meta = gamma.parse_market(raw)
            if meta:
                await gamma.upsert_market(meta)
    except Exception as exc:  # noqa: BLE001 — CLOB fallback still runs
        log.warning("gamma resolution batch failed: %s", exc)

    remaining = await unresolved_traded_condition_ids(limit=clob_batch)
    if remaining:
        async with httpx.AsyncClient(base_url=settings().clob_api_base, timeout=10) as http:
            for cid in remaining:
                meta = await fetch_clob_market(http, cid)
                if meta:
                    await gamma.upsert_market(meta)

    after = await pool.fetchval("SELECT count(*) FROM markets WHERE resolved")
    newly = after - before
    if newly:
        log.info("%s market(s) newly resolved", newly)
    return newly
