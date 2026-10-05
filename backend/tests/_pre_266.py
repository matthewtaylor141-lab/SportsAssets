"""Re-running a migration older than 266 presumes a database before 266 (the
EDDIE -> ARCHER rename): every row naming ARCHER that a committed test, the
runner or the migration's own seed left in an agent-keyed table is removed
INSIDE the caller's rolled-back test transaction, so the older migration's
narrower agent CHECK can be re-asserted. With `alias=True` the historical
alias's rows (EDDIE) go too -- for a test that presumes a database before
217, when neither name existed. Nothing is committed; the alias guard's
frozen rows are only ever removed this way, inside a transaction that rolls
back."""
from __future__ import annotations

import pathlib

MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP_266 = (MIG / "266_archer_execution_agent_rename.sql").read_text()
DOWN_266 = (MIG / "rollback"
            / "266_archer_execution_agent_rename.down.sql").read_text()

#: the columns that name an agent by id (any case)
AGENT_COLUMNS = ("agent_id", "agent", "assignee", "proposer", "owner_agent",
                 "from_agent", "to_agent", "actor", "created_by")
#: deleted LAST: the rows the others reference
IDENTITY_TABLES = ("agent_status", "agent_identities")


async def _remove(conn, names) -> None:
    assert conn.is_in_transaction(), "only inside a rolled-back test tx"
    cols = await conn.fetch(
        "SELECT c.table_name, c.column_name "
        "  FROM information_schema.columns c "
        "  JOIN information_schema.tables t ON t.table_name = c.table_name "
        "   AND t.table_schema = c.table_schema "
        " WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' "
        "   AND c.column_name = ANY($1::text[]) "
        " ORDER BY c.table_name = ANY($2::text[]), "
        "          array_position($2::text[], c.table_name::text), "
        "          c.table_name, c.column_name",
        list(AGENT_COLUMNS), list(IDENTITY_TABLES))
    for r in cols:
        t, c = r["table_name"], r["column_name"]
        where = "upper(%s::text) = ANY($1::text[])" % c
        if not await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM %s WHERE %s)" % (t, where),
                list(names)):
            continue
        await conn.execute("ALTER TABLE %s DISABLE TRIGGER USER" % t)
        await conn.execute("DELETE FROM %s WHERE %s" % (t, where),
                           list(names))
        await conn.execute("ALTER TABLE %s ENABLE TRIGGER USER" % t)


async def remove_archer_rows(conn, *, alias: bool = False) -> None:
    await _remove(conn, ["ARCHER", "EDDIE"] if alias else ["ARCHER"])


async def as_written_before_266(conn, sql: str, *args, table: str) -> None:
    """Write one row AS A PRE-266 DATABASE HOLDS IT -- under the historical
    alias EDDIE -- past the alias guard, inside the caller's rolled-back
    test transaction (the guard refuses any new alias row otherwise). The
    alias's identity row is put in place first when a test before this one
    removed it (agent_runs and others reference it)."""
    assert conn.is_in_transaction(), "only inside a rolled-back test tx"
    await conn.execute("ALTER TABLE agent_identities DISABLE TRIGGER "
                       "agent_historical_alias_trg")
    await conn.execute(
        "INSERT INTO agent_identities (agent_id, display_name, mandate) "
        "VALUES ('EDDIE', 'Eddie', 'historical alias of ARCHER') "
        "ON CONFLICT (agent_id) DO NOTHING")
    await conn.execute("ALTER TABLE agent_identities ENABLE TRIGGER "
                       "agent_historical_alias_trg")
    await conn.execute("ALTER TABLE %s DISABLE TRIGGER "
                       "agent_historical_alias_trg" % table)
    await conn.execute(sql, *args)
    await conn.execute("ALTER TABLE %s ENABLE TRIGGER "
                       "agent_historical_alias_trg" % table)
