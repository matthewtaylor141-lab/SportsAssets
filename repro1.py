import asyncio, sys
sys.path.insert(0, "backend")
import asyncpg
DSN = "postgresql://postgres:postgres@127.0.0.1:5432/impl131"
from sportsassets import bettor_funded_reservations as RS

ACCT, FIX = "acct-repro", "fx-repro"

async def main():
    c = await asyncpg.connect(DSN)
    try:
        await c.execute("DELETE FROM bettor_funded_leg_reservations WHERE group_id LIKE 'g-repro%'")
        await c.execute("DELETE FROM bettor_funded_intents WHERE intent_id LIKE 'intent-%'")
        await c.execute("DELETE FROM bettor_funded_portfolio_groups WHERE group_id LIKE 'g-repro%'")
        await c.execute("INSERT INTO bettor_funded_portfolio_groups (group_id, account_id, venue, event_key, structure) VALUES ('g-repro',$1,'PMUS',$2,'INDIRECT_MIDDLE')", ACCT, FIX)
        for iid, slug in (("intent-A", "aec-a"), ("intent-B", "aec-b")):
            await c.execute(
                "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
                " venue_class, us_market_slug, event_key, order_intent, limit_price,"
                " quantity, collateral_usd, effective_digest, state, kind,"
                " residual_qty, portfolio_group_id, leg_role) VALUES "
                "($1,$2,'PMUS','US',$3,$4,'ORDER_INTENT_BUY_LONG',0.5,10,5.0,$5,"
                " 'INTENT_RECORDED','ENTRY',0,'g-repro',$6)",
                iid, ACCT, slug, FIX, iid + "-d",
                "PRIMARY" if iid == "intent-A" else "HEDGE")

        print("--- FINDING 1a: commit_to_intent replay with a DIFFERENT intent ---")
        await RS.hold(c, operation_id="op-1", group_id="g-repro",
                      leg_role="PRIMARY", us_market_slug="aec-a", quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        first = await RS.commit_to_intent(c, operation_id="op-1", intent_id="intent-A")
        print("  first commit ok:", first["ok"], "intent:", first["reservation"]["intent_id"])
        bad = await RS.commit_to_intent(c, operation_id="op-1", intent_id="intent-B")
        stored = await c.fetchval("SELECT intent_id FROM bettor_funded_leg_reservations WHERE operation_id='op-1'")
        print("  REPLAY  ok:", bad["ok"], "already:", bad.get("already"))
        print("  REPORTS exposure_is_now_counted_on:", bad.get("exposure_is_now_counted_on"))
        print("  STORED  intent_id                 :", stored)
        if bad["ok"] and bad.get("exposure_is_now_counted_on") != stored:
            print("  >>> BUG: reported a different intent from the one stored")

        print("--- FINDING 1b: hold() replay with DIFFERENT terms ---")
        again = await RS.hold(c, operation_id="op-1", group_id="g-repro",
                              leg_role="HEDGE", us_market_slug="aec-b",
                              quantity=999, limit_price=0.9, collateral_usd=900.0)
        r = dict(await c.fetchrow("SELECT leg_role, us_market_slug, quantity::float8 q, limit_price::float8 p, collateral_usd::float8 col FROM bettor_funded_leg_reservations WHERE operation_id='op-1'"))
        print("  REPLAY ok:", again["ok"], "already:", again.get("already"))
        print("  REQUESTED: HEDGE aec-b qty=999 px=0.9 col=900")
        print("  STORED   :", r)
        if again["ok"]:
            print("  >>> BUG: a conflicting reuse of the identity was accepted as a replay")
    finally:
        await c.close()
asyncio.run(main())

# CLEAN UP: leaving an open group holds the ONE open-group slot, which makes every
# 131 test skip. That is the bound working, and a repro script must not trip it.
async def _cleanup():
    c = await asyncpg.connect(DSN)
    try:
        await c.execute("DELETE FROM bettor_funded_leg_reservations WHERE group_id LIKE 'g-repro%'")
        await c.execute("DELETE FROM bettor_funded_intents WHERE intent_id LIKE 'intent-%'")
        await c.execute("DELETE FROM bettor_funded_portfolio_groups WHERE group_id LIKE 'g-repro%'")
    finally:
        await c.close()
asyncio.run(_cleanup())
print("cleaned up: the capacity slot is free again")
