"""Re-running a migration older than 265 presumes a database before 265
(Adriana): every ADRIANA row a committed test or the migration's own seed
left in a table keyed by `agent_id` is removed INSIDE the caller's rolled-back
test transaction, so the older migration's narrower agent CHECK can be
re-asserted. Nothing is committed."""
from __future__ import annotations


async def remove_adriana_rows(conn) -> None:
    assert conn.is_in_transaction(), "only inside a rolled-back test tx"
    tables = await conn.fetch(
        "SELECT c.table_name FROM information_schema.columns c "
        "  JOIN information_schema.tables t ON t.table_name = c.table_name "
        "   AND t.table_schema = c.table_schema "
        " WHERE c.table_schema = 'public' AND c.column_name = 'agent_id' "
        "   AND t.table_type = 'BASE TABLE'")
    # agent_status references agent_identities: remove it first
    tables = sorted(tables, key=lambda r: (
        r["table_name"] == "agent_identities",
        r["table_name"] != "agent_status"))
    for r in tables:
        t = r["table_name"]
        if not await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM %s WHERE agent_id::text = "
                "'ADRIANA')" % t):
            continue
        await conn.execute("ALTER TABLE %s DISABLE TRIGGER USER" % t)
        await conn.execute("DELETE FROM %s WHERE agent_id::text = 'ADRIANA'"
                           % t)
        await conn.execute("ALTER TABLE %s ENABLE TRIGGER USER" % t)
