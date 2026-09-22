"""Build the disposable database with the REPOSITORY'S OWN migration runner.

Runs sportsassets.scripts.migrate.main() -- the real one, resolving its own
pool through the production path. Some migrations ALTER tables that no
migration CREATEs; those tables are created at runtime by worker ensure-table
functions. When the runner stalls on one, this driver calls THAT WORKER'S OWN
DDL FUNCTION and resumes. Nothing here hand-writes a schema.
"""
import asyncio
import sys
import traceback

import asyncpg

from sportsassets.db import get_pool
from sportsassets.scripts import migrate

# missing relation -> the repository function that creates it in production
PROVIDERS = {}


def _load_providers():
    from sportsassets.workers import premap
    PROVIDERS["us_premap"] = ("sportsassets.workers.premap._ensure_table",
                              premap._ensure_table)


async def main() -> int:
    _load_providers()
    supplied = []
    for attempt in range(1, 12):
        try:
            await migrate.main()
            print(f"MIGRATE_COMPLETE after {attempt} pass(es)")
            break
        except asyncpg.exceptions.UndefinedTableError as exc:
            rel = str(exc).split('"')[1] if '"' in str(exc) else ""
            prov = PROVIDERS.get(rel)
            if prov is None:
                print(f"UNRESOLVED missing relation: {rel!r}")
                traceback.print_exc()
                return 2
            name, fn = prov
            pool = await get_pool()
            await fn(pool)
            supplied.append((rel, name))
            print(f"SUPPLIED {rel} via {name}; resuming the runner")
    else:
        print("gave up after 11 passes")
        return 3

    pool = await get_pool()
    applied = [r["version"] for r in await pool.fetch(
        "SELECT version FROM schema_migrations ORDER BY version")]
    print(f"APPLIED_COUNT={len(applied)}")
    print(f"APPLIED_FIRST={applied[0]}")
    print(f"APPLIED_LAST={applied[-1]}")
    cols = await pool.fetch(
        "SELECT ordinal_position, column_name, data_type, is_nullable, "
        "coalesce(column_default,'-') AS dflt FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='ingestion_state' "
        "ORDER BY ordinal_position")
    print("INGESTION_STATE_COLUMNS:")
    for c in cols:
        print(f"  {c['ordinal_position']} | {c['column_name']} | "
              f"{c['data_type']} | nullable={c['is_nullable']} | {c['dflt']}")
    print("RUNTIME_SUPPLIED_TABLES:", supplied or "none")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
