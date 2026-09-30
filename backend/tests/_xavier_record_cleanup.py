"""Remove ONE test account's Xavier records from a shared test database.

`bettor_xavier_decisions` is append-only (migration 148's trigger refuses
UPDATE and DELETE), so a proof that runs real management under its own
non-demonstration account leaves decisions behind. The improvement replay's
AUTO scope reads every decision in its window and treats ANY
non-demonstration decision as production evidence -- correctly, in
production -- so a leftover test decision silently flipped a later proof's
rehearsal evaluation to PRODUCTION scope (and a REJECTED verdict).

Exactly the given account's rows go, with the trigger bypassed for this
transaction only; every other account's records are untouched.
"""
from __future__ import annotations


async def purge_xavier_records(conn, account_id: str) -> None:
    if not await conn.fetchval(
            "SELECT to_regclass('bettor_xavier_decisions') IS NOT NULL"):
        return
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for t in ("bettor_xavier_execution_events",
                  "bettor_xavier_standing_orders"):
            if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t):
                has = await conn.fetchval(
                    "SELECT count(*) FROM information_schema.columns "
                    " WHERE table_name=$1 AND column_name='account_id'", t)
                if has:
                    await conn.execute(
                        "DELETE FROM %s WHERE account_id=$1" % t, account_id)
        await conn.execute(
            "DELETE FROM bettor_xavier_decisions WHERE account_id=$1",
            account_id)
