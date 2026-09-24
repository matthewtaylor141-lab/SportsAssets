"""Apply SQL migrations in order. Tracks applied versions in schema_migrations.

AN APPLIED MIGRATION IS IMMUTABLE, AND THE RUNNER NOW SAYS SO OUT LOUD.

A version is recorded BY FILENAME and skipped forever after. So appending
statements to a file that has already run means they never execute -- in
production. Locally, `psql -f migrations/<file>.sql` replays the WHOLE
file and everything looks applied, which is how this went unnoticed until
an endpoint returned HTTP 500 reading columns that existed on one machine
only.

Each applied migration's content hash is therefore recorded, and a file
whose hash no longer matches is reported at boot. It is a WARNING, not a
raise: a failed boot takes the API down instead of reporting a problem,
and the same reasoning is written into migration 114's header. The fix for
a mismatch is always a NEW migration carrying the difference, never an
edit to the old one.
"""

import asyncio
import hashlib
import logging
import pathlib

from ..db import get_pool

log = logging.getLogger(__name__)
MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "migrations"


def content_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    pool = await get_pool()
    drifted = []
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        # Added separately so an existing table gains it without a rewrite.
        await conn.execute(
            "ALTER TABLE schema_migrations "
            "ADD COLUMN IF NOT EXISTS content_sha TEXT")
        applied = {r["version"]: r["content_sha"] for r in await conn.fetch(
            "SELECT version, content_sha FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            text = path.read_text()
            sha = content_sha(text)
            if path.name in applied:
                seen = applied[path.name]
                if seen is None:
                    # Applied before hashes were recorded. Adopt the
                    # current content as the baseline rather than reporting
                    # every pre-existing file as drifted.
                    await conn.execute(
                        "UPDATE schema_migrations SET content_sha = $2 "
                        "WHERE version = $1", path.name, sha)
                elif seen != sha:
                    drifted.append(path.name)
                    log.warning(
                        "%s WAS ALREADY APPLIED AND ITS CONTENT HAS SINCE "
                        "CHANGED. The change has NOT run and never will: "
                        "this runner skips a version it has seen. Move the "
                        "difference into a NEW migration.", path.name)
                continue
            log.info("applying %s", path.name)
            async with conn.transaction():
                await conn.execute(text)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, content_sha) "
                    "VALUES ($1, $2) ON CONFLICT (version) DO UPDATE SET "
                    "content_sha = EXCLUDED.content_sha",
                    path.name, sha)
    if drifted:
        log.warning("migrations complete, with %d CHANGED-AFTER-APPLY "
                    "file(s) whose changes did not run: %s",
                    len(drifted), ", ".join(drifted))
    else:
        log.info("migrations complete")


if __name__ == "__main__":
    asyncio.run(main())
