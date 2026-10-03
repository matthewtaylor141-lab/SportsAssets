"""REBUILD THE INSTITUTIONAL PAGE FIXTURES FROM THE REAL ROUTES.

Not a test (no test_ prefix): a tool that regenerates ok/, empty/ and
unavailable/ beside it. ALL DATA IS SYNTHETIC TEST DATA written into a
scratch database; nothing here touches a venue or a production database.

  # a fresh scratch database, migrated
  sudo -u postgres psql -c "CREATE DATABASE cc22 TEMPLATE <a migrated test db>"
  cd backend && DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/cc22 \
      python -m sportsassets.scripts.migrate
  sudo -u postgres psql -c "CREATE DATABASE cc22_empty TEMPLATE cc22"
  # seed cc22 the way the route tests seed (committed), then capture
  PYTHONPATH=. python tests/fixtures/institutional/capture.py seed \
      postgresql://postgres:postgres@127.0.0.1:5432/cc22
  PYTHONPATH=. python tests/fixtures/institutional/capture.py capture ok \
      postgresql://postgres:postgres@127.0.0.1:5432/cc22
  PYTHONPATH=. python tests/fixtures/institutional/capture.py capture empty \
      postgresql://postgres:postgres@127.0.0.1:5432/cc22_empty
  PYTHONPATH=. python tests/fixtures/institutional/capture.py capture \
      unavailable postgresql://postgres:postgres@127.0.0.1:5432/cc22_missing

Each route is called through FastAPI's TestClient with a throwaway admin
token, exactly as the page reads it (same path and query), and saved as
{"route", "http", "body"}.
"""
import asyncio
import json
import os
import pathlib
import sys
import time

import asyncpg

HERE = pathlib.Path(__file__).resolve().parent
ROUTES = {
 "intel": "/api/command/intel", "allocator": "/api/command/intel/allocator",
 "calibration": "/api/command/intel/calibration", "attribution": "/api/command/intel/attribution",
 "sizing": "/api/command/intel/sizing", "risk": "/api/command/intel/risk",
 "regime": "/api/command/intel/regime", "karen": "/api/command/karen",
 "karenChallenges": "/api/command/karen/challenges?limit=50",
 "xavierMgmt": "/api/command/xavier/management",
 "coverage": "/api/command/coverage?tz=America/New_York&days=7",
 "postmortems": "/api/command/postmortems?limit=25", "quality": "/api/command/quality",
 "p5": "/api/command/p5/evidence", "smallLive": "/api/command/small-live?limit=25",
 "agents": "/api/command/agents", "findings": "/api/command/agents/findings",
}


async def karen(conn, now):
    from sportsassets.agents import collaboration_loop as CL
    from sportsassets.agents import karen as K
    from sportsassets.agents import registry as R
    await R.ensure_identities(conn)
    T0 = now - 7200
    refs = []
    for i, who in enumerate(("DEREK", "XAVIER", "AUDREY", "DEREK")):
        ref = {"kind": "agent_decisions", "id": "adr:cc22-%d-%d" % (int(now), i)}
        await conn.execute("INSERT INTO agent_decisions (decision_ref, agent_id, kind, decided_at)"
                           " VALUES ($1,$2,'TEST',to_timestamp($3)) ON CONFLICT DO NOTHING",
                           ref["id"], who, T0)
        refs.append(ref)
    D1, D2, D3, D4 = refs
    f = await CL.open_finding(conn, proposer="DEREK", title="stale books after 21:00",
                              statement="entries refused for stale books cluster after 21:00",
                              evidence_refs=[D1], evidence_window_end=T0, at=T0 + 10)
    fid = f["finding_id"]
    assert (await CL.record_hypothesis(conn, fid, actor="DEREK", hypothesis="a later read helps",
                                       evidence_refs=[D2], at=T0 + 20))["ok"]
    got = await K.challenge_finding(conn, fid, claim="the hypothesis rests on a defective record",
                                    outcome="REFUTED", evidence_refs=[D3], at=T0 + 30, severity="HIGH")
    assert got["ok"], got
    c1 = got["challenge_id"]
    assert (await K.respond(conn, c1, agent="DEREK", stance="DISPUTE", response="the record was fine",
                            evidence_refs=[D1], at=T0 + 50))["ok"]
    assert (await K.resolve(conn, c1, resolver="AUDREY", outcome="REJECTED",
                            reason="record verified against the venue book", at=T0 + 60))["ok"]
    assert (await K.assess_false_block(conn, c1, assessor="AUDREY", false_block=True,
                                       evidence_refs=[D1], at=T0 + 70))["ok"]
    f2 = await CL.open_finding(conn, proposer="XAVIER", title="hold on stale probability",
                               statement="three holds rested on a probability older than the bound",
                               evidence_refs=[D2], evidence_window_end=T0, at=T0 + 15)
    got = await K.open_challenge(conn, target_agent="XAVIER", target_kind="agent_decisions",
                                 target_id=D2["id"], detector="HOLD_ON_STALE_PROBABILITY",
                                 claim="HOLD recommended on a probability 900 s old",
                                 severity="MEDIUM", evidence_refs=[D2], record_at=T0, at=T0 + 120)
    c2 = got["challenge_id"]
    assert (await K.respond(conn, c2, agent="XAVIER", stance="CONCEDE",
                            response="yes, the evidence was stale", at=T0 + 180))["ok"]
    assert (await K.resolve(conn, c2, resolver="XAVIER", outcome="UPHELD", reason="conceded",
                            at=T0 + 190))["ok"]
    assert (await K.link_improvement(conn, c2, actor="AUDREY", at=T0 + 200,
                                     finding_id=f2["finding_id"],
                                     impact={"note": "bounded paper experiment opened"}))["ok"]
    got = await K.open_challenge(conn, target_agent="AUDREY", target_kind="agent_decisions",
                                 target_id=D3["id"], detector="AUDIT_DISCREPANCY_LEFT_OPEN",
                                 claim="an audit discrepancy has been open for 2 h",
                                 severity="LOW", evidence_refs=[D3], record_at=T0, at=T0 + 300)
    got = await K.open_challenge(conn, target_agent="DEREK", target_kind="agent_decisions",
                                 target_id=D4["id"], detector="DECISION_WITHOUT_EVIDENCE",
                                 claim="decision cites no valuation record",
                                 severity="MEDIUM", evidence_refs=[D4], record_at=T0, at=T0 + 400)
    assert (await K.respond(conn, got["challenge_id"], agent="DEREK", stance="DISPUTE",
                            response="the valuation is linked by event key", evidence_refs=[D1],
                            at=T0 + 460))["ok"]

async def small_live(conn):
    from tests import test_small_live_view as SLV
    acct = await SLV._setup(conn)
    await SLV._complete_chain(conn, acct)
    # a refused row: an exclusion with its reason and no fill figures
    po = await SLV._paper_order(conn, acct, qty=1500)
    await SLV._mirror(conn, po, state="EXCLUDED", live_qty=0,
                      exclusion="ADMISSION_BOOK_CURRENCY_NOT_ESTABLISHED")

async def postmortems(conn):
    from tests import paper_harness as H
    from tests import paper_ops_seed as SEED
    from tests import test_position_postmortems as TPM
    from sportsassets import bettor_paper_ledger as L
    from sportsassets.agents import postmortems as PM
    T0 = H.T0
    acct = await H.new_account(conn, "pm", now=T0)
    seeded = await SEED.seed(conn, acct, t0=T0)
    strict = seeded["entries"]["strict"]
    slug = "seed-%s-mlb-early" % acct["account_id"][-10:]
    d1 = await SEED._decision(conn, acct, n=40, strategy=SEED.CG, verdict="ENTER", refusal=None,
                              at=T0 + 200, slug=slug, fail_at=None, edge=7.0, ev=5.0, p_pin=0.55)
    await TPM._entry_and_exit(conn, acct, slug=slug, key="early", decision_id=d1, at=T0 + 210)
    d2 = await SEED._decision(conn, acct, n=41, strategy=SEED.CG, verdict="ENTER", refusal=None,
                              at=T0 + 300, slug=slug, fail_at=None, edge=7.0, ev=5.0, p_pin=0.55)
    held = await SEED._entry(conn, acct, key="held", slug=slug, strategy=SEED.CG,
                             decision_id=d2, at=T0 + 310, qty=50.0, limit=0.5)
    await L.settle(conn, account_id=acct["account_id"], group_id=held["group_id"], slug=slug,
                   holding_side="LONG", settlement_event_key="venue-final:" + slug, outcome="WON",
                   evidence={"test": "synthetic"}, evidence_source="TEST_FIXTURE_SYNTHETIC",
                   at=T0 + 400, session_id=acct["session_id"])
    await conn.execute(
        "INSERT INTO execmirror_orders (mirror_id, role, us_market_slug, intent, order_type, tif,"
        " state, group_id, wire_price, live_qty, cum_qty) VALUES ('mir-pm-buy','ENTRY',$1,"
        " 'ORDER_INTENT_BUY_LONG','LIMIT','IOC','FILLED',$2,0.51,4,4), ('mir-pm-sell','EXIT',$1,"
        " 'ORDER_INTENT_SELL_LONG','LIMIT','IOC','FILLED',$2,0.70,4,4)", strict["slug"], strict["group_id"])
    await conn.execute(
        "INSERT INTO execmirror_fills (fill_key, mirror_id, venue_order_id, group_id, us_market_slug,"
        " intent, qty, price, fee_usd, observed_at, source) VALUES ('fk-pm-1','mir-pm-buy','v-1',$2,$1,"
        " 'ORDER_INTENT_BUY_LONG',4,0.51,0.02,now(),'TEST'), ('fk-pm-2','mir-pm-sell','v-2',$2,$1,"
        " 'ORDER_INTENT_SELL_LONG',4,0.70,0.02,now(),'TEST')", strict["slug"], strict["group_id"])
    await conn.execute(
        "INSERT INTO smalllive_handoffs (handoff_id, venue, group_id, us_market_slug, entry_mirror_id,"
        " opened_intent, live_held, live_bought, avg_entry_px, fees_usd, first_live_fill_at, state)"
        " VALUES ('livehand:test:pm','POLYMARKET',$1,$2,'mir-pm-buy','ORDER_INTENT_BUY_LONG',0,4,0.51,"
        " 0.04,now(),'CLOSED')", strict["group_id"], strict["slug"])
    res = await PM.run(conn, account_id=acct["account_id"], now=T0 + 500)
    print("postmortems", res.get("ran"), res.get("errors"))

async def intel(conn):
    from tests import test_intel_is_shadow_only as TI
    from sportsassets.intel import runner as RUN
    now = time.time()
    acct, exp, d, g = await TI._seed(conn, now)
    got = await RUN.run_cycle(conn, now=now, account_id=acct["account_id"], experiment_id=exp,
                              slug_prefix="intel-mkt-", include_actual=True)
    print("intel", got.get("ran"), got.get("components"))


async def seed_all(dsn):
    conn = await asyncpg.connect(dsn)
    try:
        now = time.time()
        for name, fn in (("karen", lambda: karen(conn, now)),
                         ("small_live", lambda: small_live(conn)),
                         ("postmortems", lambda: postmortems(conn)),
                         ("intel", lambda: intel(conn))):
            async with conn.transaction():
                await fn()
            print("seeded", name)
        # an account snapshot of the mirror account (as test_small_live_view)
        await conn.execute(
            "INSERT INTO execmirror_snapshots (account_fingerprint, balances,"
            " positions, open_orders, reconciliation) VALUES ("
            "'fp0123456789abcdef-small-live-test', "
            "'[{\"currency\":\"USD\",\"currentBalance\":100,"
            "\"buyingPower\":97.5}]', '[{\"slug\":\"a\"},"
            "{\"slug\":\"b\"}]', 1, '{\"reconciled\": true}')")
    finally:
        await conn.close()


def capture(name, dsn):
    os.environ["DATABASE_URL"] = dsn
    os.environ["ADMIN_TOKEN"] = "fixture-capture-token-not-a-secret"
    from starlette.testclient import TestClient
    from sportsassets import db as DB
    from sportsassets.api import app as A

    class _Acq:
        async def __aenter__(self):
            self.c = await asyncpg.connect(dsn)
            return self.c

        async def __aexit__(self, *a):
            await self.c.close()

    class _Pool:
        def acquire(self):
            return _Acq()

    async def _get():
        return _Pool()
    DB.get_pool = _get
    if hasattr(A, "get_pool"):
        A.get_pool = _get
    client = TestClient(A.app, raise_server_exceptions=False)
    out = HERE / name
    out.mkdir(exist_ok=True)
    for key, route in ROUTES.items():
        r = client.get(route, headers={"X-Admin-Token": os.environ["ADMIN_TOKEN"]})
        try:
            body = r.json()
        except ValueError:
            body = {"_text": r.text[:500]}
        (out / (key + ".json")).write_text(json.dumps(
            {"route": route, "http": r.status_code, "body": body},
            sort_keys=True, separators=(",", ":"), default=str) + "\n")
        print(name, key, r.status_code)


if __name__ == "__main__":
    if sys.argv[1] == "seed":
        os.environ["RN1X_TEST_DSN"] = sys.argv[2]
        asyncio.run(seed_all(sys.argv[2]))
    elif sys.argv[1] == "capture":
        capture(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("usage: capture.py seed DSN | capture SET DSN")
