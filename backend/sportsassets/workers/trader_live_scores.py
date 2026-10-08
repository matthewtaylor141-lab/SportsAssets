"""Standalone display collector. Run on a dedicated read-data/write-display service.

Does not join workers/all.py, change a kill switch, activate a strategy or import
an execution adapter. Disabled by default. Uses one DB leadership lease to avoid
multiplying upstream requests across service replicas.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import signal
import time

from ..live_game_state.collector import Collector
from ..live_game_state.core import Aliases
from ..live_game_state.integration import enabled
from ..live_game_state.providers import ScoreClient, HttpTransport
from ..live_game_state.storage import PostgresStore

log = logging.getLogger(__name__)


async def main():
    if not enabled():
        log.info("trader_live_scores DISABLED; no provider calls")
        return
    dsn = os.getenv("TRADER_SCORE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("TRADER_SCORE_DATABASE_URL_OR_DATABASE_URL_REQUIRED")
    import asyncpg
    # Two connections: one holds the lease, one handles bounded display storage.
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, command_timeout=3)
    client = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        async with pool.acquire() as lease:
            leader = await lease.fetchval("SELECT pg_try_advisory_lock(hashtextextended('BETTOR_DISPLAY_SCORES_LEADER_V1',0))")
            if not leader:
                log.info("trader_live_scores STANDBY; no provider calls")
            while not leader and not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=30)
                except TimeoutError:
                    leader = await lease.fetchval("SELECT pg_try_advisory_lock(hashtextextended('BETTOR_DISPLAY_SCORES_LEADER_V1',0))")
            if stop.is_set():
                return
            client = ScoreClient(HttpTransport(), odds_key=os.getenv("ODDS_API_KEY"),
                                 odds_enabled=os.getenv("BETTOR_ODDS_SCORE_FALLBACK", "off").lower() in ("on", "1", "true"))
            store = PostgresStore(pool)
            collector = Collector(client, store,
                                  enrich_summaries=os.getenv("BETTOR_SCORE_SUMMARIES", "off").lower() in ("on", "1", "true"))
            aliases_path = os.getenv("BETTOR_SCORE_ALIASES_FILE")
            if aliases_path:
                from pathlib import Path
                p = Path(aliases_path)
                if p.stat().st_size > 131072:
                    raise RuntimeError("TEAM_ALIAS_FILE_TOO_LARGE")
                collector.aliases = Aliases(json.loads(p.read_text()))
            focus = {x.strip() for x in os.getenv("BETTOR_SCORE_FOCUS_EVENT_IDS", "").split(",") if x.strip()}
            fixtures, census, last_discovery = [], {}, 0.0
            while not stop.is_set():
                if lease.is_closed():
                    raise RuntimeError("SCORE_LEADERSHIP_CONNECTION_LOST")
                # Confirm lease connection health BEFORE issuing more upstream requests.
                await lease.fetchval("SELECT 1")
                now = time.time()
                try:
                    if now-last_discovery >= 30:
                        fixtures, census = await store.fixtures()
                        last_discovery = now
                    report = await collector.cycle(fixtures, focused_ids=focus, census=census)
                    log.info("trader_live_scores %s", json.dumps(report, separators=(",", ":")))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Don't include exception text: it might contain a DSN or key.
                    log.error("trader_live_scores cycle_failed type=%s", type(exc).__name__)
                    await store.heartbeat({"status": "ERROR", "error_type": type(exc).__name__,
                                           "authority": "DISPLAY_ONLY"}, now=time.time())
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except TimeoutError:
                    pass
    finally:
        if client:
            await client.close()
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # httpx normally logs full request URLs (including apiKey). Suppress that logger.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(main())
