"""Apply SQL migrations in order. Tracks applied versions in schema_migrations."""

import asyncio
import logging
import pathlib

from ..db import get_pool

log = logging.getLogger(__name__)
MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "migrations"


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            log.info("applying %s", path.name)
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1) "
                    "ON CONFLICT (version) DO NOTHING",
                    path.name,
                )
    log.info("migrations complete")


if __name__ == "__main__":
    asyncio.run(main())
