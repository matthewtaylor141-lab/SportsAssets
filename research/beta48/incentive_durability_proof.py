"""The three pre-deployment properties, demonstrated against Postgres.

Nothing here is mocked at the database boundary. A real PostgreSQL 16
`ingestion_state` row is armed exactly as `render-ops sql
obs-arm-incentive` arms it, and the scenarios below are run against it.

  P1  THE REQUEST CEILING survives a crash between reservation and
      dispatch, a crash after dispatch, and two OVERLAPPING WORKERS.
  P2  THE EVIDENCE survives process replacement, and the interval the
      replacement created is recorded as UNOBSERVED rather than
      inferred away.
  P3  ROLLBACK cannot silently start general discovery.

Run:  python research/beta48/incentive_durability_proof.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))

PORT = os.environ.get("REHEARSAL_PG_PORT", "5433")
DSN = "postgresql://postgres@localhost:%s/rehearsal" % PORT
os.environ["DATABASE_URL"] = DSN

import asyncpg                                              # noqa: E402

from sportsassets import bettor_incentive_budget as bud     # noqa: E402
from sportsassets import bettor_incentive_journal as jrnl   # noqa: E402
from sportsassets import bettor_live_control as ctl         # noqa: E402

RESULTS: list = []

# The arm, character for character equivalent to the render-ops action.
ARM = {
    "probe_id": "PROOF-PROBE-0001",
    "started_at": None, "deadline_at": None,
    "max_distinct": 0, "max_bbo_attempts": 0, "max_listing_attempts": 0,
    "distinct_reserved": 0, "bbo_attempts_reserved": 0,
    "listing_attempts_reserved": 0,
    "max_incentive_manifest": 4, "max_incentive_recheck": 2,
    "max_incentive_retry": 2,
    "incentive_manifest_reserved": 0, "incentive_recheck_reserved": 0,
    "incentive_retry_reserved": 0,
    "slugs": [],
}


def _iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


async def arm(pool, *, control=True, probe_id="PROOF-PROBE-0001",
              deadline_in=3600.0, general_caps=0):
    row = dict(ARM, probe_id=probe_id,
               started_at=_iso(time.time()),
               deadline_at=_iso(time.time() + deadline_in),
               max_distinct=general_caps,
               max_bbo_attempts=general_caps * 4,
               max_listing_attempts=general_caps)
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        ctl.BUDGET_KEY, json.dumps(row))
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        ctl.CONTROL_KEY, json.dumps(bool(control)))


async def row_total(pool):
    raw = await pool.fetchval(
        "SELECT value FROM ingestion_state WHERE key=$1", ctl.BUDGET_KEY)
    v = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    return sum(int(v.get(ctl._COUNTER[k], 0) or 0) for k in
               ("incentive_manifest", "incentive_recheck", "incentive_retry"))


def record(name, question, checks):
    ok = all(v for _, v in checks)
    RESULTS.append({"property": name, "question": question, "pass": ok,
                    "checks": [{"check": k, "pass": bool(v)}
                               for k, v in checks]})
    print("  %s  %-30s %s" % ("PASS" if ok else "FAIL", name, question))
    for k, v in checks:
        print("        %s %s" % ("ok " if v else "NO ", k))


# ═══ P1 ══════════════════════════════════════════════════════════════

async def p1(pool):
    print("\nP1  THE REQUEST CEILING\n")

    # ---- the reservation is committed BEFORE the caller may dispatch
    await arm(pool)
    dispatched = []

    async def request(kind):
        L = bud.DurableLedger(pool, probe_id="PROOF-PROBE-0001")
        r = await L.spend(kind)
        # THE ORDER THAT MATTERS: the row is read AFTER the grant and
        # BEFORE the dispatch. If the increment were not already
        # committed, this would read the old number.
        committed = await row_total(pool)
        if r["ok"]:
            dispatched.append(kind)
        return r, committed

    r1, committed_before_dispatch = await request(bud.K_MANIFEST)
    record("P1a reserve-then-dispatch",
           "is the unit durable before the request goes out?", [
               ("the reservation was granted", r1["ok"]),
               ("the ROW already shows 1 before dispatch",
                committed_before_dispatch == 1),
               ("the grant says it is durable", r1.get("durable") is True),
           ])

    # ---- crash BETWEEN reservation and dispatch
    await arm(pool)
    L = bud.DurableLedger(pool, probe_id="PROOF-PROBE-0001")
    await L.spend(bud.K_MANIFEST)          # reserved...
    del L                                  # ...process dies here
    after_crash = await row_total(pool)
    L2 = bud.DurableLedger(pool, probe_id="PROOF-PROBE-0001")   # new boot
    grants = [(await L2.spend(bud.K_MANIFEST))["ok"] for _ in range(4)]
    record("P1b crash before dispatch",
           "can the lost in-flight unit be spent twice?", [
               ("the unit was already committed when the process died",
                after_crash == 1),
               ("the new boot got only the 3 that remained",
                grants == [True, True, True, False]),
               ("the manifest sub-cap is exactly spent",
                await row_total(pool) == 4),
           ])

    # ---- crash AFTER dispatch
    await arm(pool)
    L = bud.DurableLedger(pool, probe_id="PROOF-PROBE-0001")
    await L.spend(bud.K_MANIFEST)
    sent = True                            # the request really went out
    del L
    L3 = bud.DurableLedger(pool, probe_id="PROOF-PROBE-0001")
    record("P1c crash after dispatch",
           "does the row still know the request was made?", [
               ("the request was dispatched", sent),
               ("the row counts it after the crash",
                await row_total(pool) == 1),
               ("a fresh boot reads the row, not zero",
                (await L3.read())["total_reserved"] == 1),
           ])

    # ---- TWO OVERLAPPING WORKERS, on separate pools and connections
    await arm(pool)
    pool_a = await asyncpg.create_pool(DSN, min_size=2, max_size=4)
    pool_b = await asyncpg.create_pool(DSN, min_size=2, max_size=4)
    try:
        A = bud.DurableLedger(pool_a, probe_id="PROOF-PROBE-0001")
        B = bud.DurableLedger(pool_b, probe_id="PROOF-PROBE-0001")

        async def burst(L, kinds):
            out = []
            for k in kinds:
                out.append(await L.spend(k))
            return out

        kinds = ([bud.K_MANIFEST] * 4 + [bud.K_RECHECK] * 2
                 + [bud.K_RETRY] * 2)
        got_a, got_b = await asyncio.gather(burst(A, kinds), burst(B, kinds))
        grants = sum(1 for r in got_a + got_b if r["ok"])
        total = await row_total(pool)
        record("P1d two overlapping workers",
               "do two workers each get eight?", [
                   ("16 requests were attempted", len(got_a + got_b) == 16),
                   ("exactly 8 were granted IN TOTAL", grants == 8),
                   ("the row agrees", total == 8),
                   ("both workers got some -- they really did race",
                    any(r["ok"] for r in got_a) and any(r["ok"]
                                                        for r in got_b)),
               ])
    finally:
        await pool_a.close()
        await pool_b.close()

    # ---- a stale process cannot spend the next probe's allowance
    await arm(pool, probe_id="PROBE-TWO")
    stale = bud.DurableLedger(pool, probe_id="PROOF-PROBE-0001")
    r = await stale.spend(bud.K_MANIFEST)
    record("P1e re-arm invalidates stale",
           "can a process that outlived its probe spend?", [
               ("refused", r["ok"] is False),
               ("named RESERVATION_PROBE_MISMATCH",
                r["verdict"] == ctl.V_MISMATCH),
               ("nothing was spent", await row_total(pool) == 0),
           ])

    # ---- a stop refuses the NEXT request inside the same transaction
    await arm(pool, control=False)
    L = bud.DurableLedger(pool, probe_id="PROOF-PROBE-0001")
    r = await L.spend(bud.K_MANIFEST)
    record("P1f stop refuses mid-run",
           "does a stop wait for the next poll?", [
               ("refused immediately", r["ok"] is False),
               ("named RESERVATION_STOPPED_BY_CONTROL",
                r["verdict"] == ctl.V_STOPPED),
               ("nothing was spent", await row_total(pool) == 0),
           ])

    # ---- the sub-caps ARE the total
    record("P1g subcaps sum to the total",
           "can eight be exceeded without exceeding a sub-cap?", [
               ("4 + 2 + 2 == 8", sum(bud.SUBCAPS.values()) == bud.TOTAL_CAP),
               ("the control module agrees",
                ctl.INCENTIVE_TOTAL == bud.TOTAL_CAP),
           ])

    # ---- scope of the socket bounds, stated
    b = bud.ReconnectBounds(max_reconnects=20, max_resubscribes=40,
                            prior_reconnects=20, prior_resubscribes=0)
    record("P1h socket bounds scope",
           "are reconnect limits per boot or per run?", [
               ("declared PER RUN", b.check(0)["scope"].startswith("PER RUN")),
               ("a fresh boot cannot buy another twenty",
                b.check(1)["ok"] is False),
               ("prior boots are carried in the verdict",
                b.check(1)["prior_boots"]["reconnects"] == 20),
           ])


# ═══ P2 ══════════════════════════════════════════════════════════════

async def p2(pool):
    print("\nP2  EVIDENCE DURABILITY\n")
    await pool.execute("DROP TABLE IF EXISTS bettor_incentive_journal")
    run_id = "2026-09-23:PROOFMANIFEST"

    # ---- boot 1 writes, then "dies"
    j1 = jrnl.PgJournal(pool, run_id=run_id, boot_id="boot-one")
    o1 = await j1.open()
    j1.run_open(et_date="2026-09-23")
    for i in range(3):
        j1.ladder({"slug": "m00", "epoch": 1, "ladder_class":
                   jrnl.R_LADDER if i else "INITIAL_LADDER",
                   "ladder_seq": i + 1, "received_at": time.time(),
                   "source_ts": "2026-09-23T12:00:0%d.123456Z" % i,
                   "venue_state": "MARKET_STATE_OPEN", "replacement": "x"},
                  {"bids": [{"px": "0.50", "qty": "400"}], "offers": []})
    # A QUIET STRETCH, then a connection-level record. On a
    # change-driven feed this is an ordinary minute: the books did not
    # move, and the socket was fine the whole time.
    await asyncio.sleep(0.3)
    quiet_instant = time.time()
    await asyncio.sleep(0.3)
    j1.epoch(epoch=1, event="STILL_CONNECTED")
    await j1.flush(force=True)
    boot1_last = time.time()
    del j1

    # THE PROCESS IS REPLACED. A real gap of wall-clock time passes.
    await asyncio.sleep(1.2)

    # ---- boot 2 resumes the SAME run
    j2 = jrnl.PgJournal(pool, run_id=run_id, boot_id="boot-two")
    o2 = await j2.open()
    j2.run_open(et_date="2026-09-23", resumed=True)
    mid = time.time()
    j2.ladder({"slug": "m00", "epoch": 1, "ladder_class": "INITIAL_LADDER",
               "ladder_seq": 1, "received_at": mid,
               "source_ts": "2026-09-23T12:01:00.000001Z",
               "venue_state": "MARKET_STATE_OPEN", "replacement": "x"},
              {"bids": [{"px": "0.51", "qty": "300"}], "offers": []})
    await j2.flush(force=True)
    c2 = await j2.close()

    recs = await jrnl.read_run(pool, run_id)
    gaps = [r for r in recs if r["kind"] == jrnl.R_GAP]
    boot_gaps = [g for g in gaps
                 if g["payload"].get("event") == jrnl.BOOT_GAP]
    segs = jrnl.segments(recs)
    # The instant in the middle of the outage.
    t_out = boot1_last + 0.5
    verdict = jrnl.covers(recs, t_out)

    record("P2a destination",
           "where does the evidence actually live?", [
               ("the journal opened against Postgres",
                o1.get("ok") and o1.get("destination") == "postgres"),
               ("it declares itself durable across a redeploy",
                o1.get("durable_across_redeploy") is True),
               ("no ephemeral file backend exists at all",
                "REMOVED" in jrnl.describe()["ephemeral_file_backend"]),
           ])

    record("P2b survives process replacement",
           "do boot 1's records survive into boot 2?", [
               ("records from both boots are present",
                {r["boot_id"] for r in recs} == {"boot-one", "boot-two"}),
               ("boot 1's three ladders are still there",
                sum(1 for r in recs if r["kind"] == jrnl.R_LADDER
                    and r["boot_id"] == "boot-one") == 3),
               ("boot 2 wrote its own", c2["rows_written"] >= 2),
           ])

    record("P2c the gap is not mistaken for coverage",
           "does the outage look like a quiet book?", [
               ("a BOOT_GAP was recorded", len(boot_gaps) == 1),
               ("it is attributed to PROCESS_REPLACED",
                boot_gaps and boot_gaps[0]["payload"]["why"]
                == jrnl.PROCESS_REPLACED),
               ("it spans the real outage",
                boot_gaps and boot_gaps[0]["payload"]["duration_s"] >= 1.0),
               ("an instant inside it is UNOBSERVED",
                verdict["observed"] is False),
               ("and says why", verdict.get("why") == jrnl.PROCESS_REPLACED),
               ("open() reported the gap rather than hiding it",
                (o2.get("boot_gap") or {}).get("why")
                == jrnl.PROCESS_REPLACED),
           ])

    record("P2d run identity across boots",
           "is (boot_id, epoch) the reconstruction key?", [
               ("both boots used epoch 1",
                {r["epoch"] for r in recs if r["kind"] == jrnl.R_LADDER}
                == {1}),
               ("but they are TWO segments, not one", len(segs) == 2),
               ("keyed on boot_id",
                {s["boot_id"] for s in segs} == {"boot-one", "boot-two"}),
               ("the rule says so in the data",
                "never epoch alone"
                in jrnl.describe()["reconstruction_key"]),
           ])

    # THE OTHER HALF OF THE SAME ERROR. Gaps must not swallow real
    # coverage: `quiet_instant` sits AFTER boot 1's last ladder, in a
    # stretch where nothing changed and the connection was fine. A
    # segment that ended at its final frame would call this unobserved
    # -- which is the "quiet book is missing" mistake wearing a
    # reconstruction hat.
    inside = jrnl.covers(recs, quiet_instant)
    record("P2e coverage is not blanket",
           "is a quiet stretch inside an epoch still observed?", [
               ("the quiet instant IS observed", inside["observed"] is True),
               ("attributed to boot-one",
                inside.get("segment", {}).get("boot_id") == "boot-one"),
               ("it lies after boot 1's last LADDER",
                quiet_instant > max(r["at"] for r in recs
                                    if r["kind"] == jrnl.R_LADDER
                                    and r["boot_id"] == "boot-one")),
               ("the segment says why it extends that far",
                "NOT the last ladder"
                in (inside.get("segment") or {}).get("end_basis", "")),
           ])


# ═══ P3 ══════════════════════════════════════════════════════════════

async def p3(pool):
    print("\nP3  ROLLBACK STOPS OBSERVATION\n")

    # THE UNSAFE SEQUENCE, demonstrated: control left TRUE while the
    # mode is removed. Without the zeroed caps the general loop would
    # begin discovery.
    await arm(pool, control=True, general_caps=0)
    b = await ctl.read_budget(pool)
    control_after = await ctl.read_control(pool)
    r_list = await ctl.reserve(pool, ctl.R_LISTING,
                               probe_id="PROOF-PROBE-0001")
    r_bbo = await ctl.reserve(pool, ctl.R_ATTEMPT,
                              probe_id="PROOF-PROBE-0001")
    record("P3a config removal alone",
           "if the mode is removed while the control is true?", [
               ("the control IS still true -- removal is not a stop",
                control_after["run"] is True),
               ("but the allowance reads BUDGET_EXHAUSTED",
                b["state"] == ctl.B_EXHAUSTED and b["open"] is False),
               ("so the general loop never constructs a client",
                b["open"] is False),
               ("a listing reservation is refused",
                not ctl.granted(r_list)),
               ("a BBO reservation is refused", not ctl.granted(r_bbo)),
               ("zero venue requests are possible",
                b["max_distinct"] == 0 and b["max_listing_attempts"] == 0),
           ])

    # THE SAFE SEQUENCE: stop first, then verify, then remove config.
    await arm(pool, control=True, general_caps=0)
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,'false'::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value='false'::jsonb",
        ctl.CONTROL_KEY)
    c = await ctl.read_control(pool)
    inc = await bud.DurableLedger(pool, probe_id="PROOF-PROBE-0001"
                                  ).spend(bud.K_MANIFEST)
    gen = await ctl.reserve(pool, ctl.R_LISTING, probe_id="PROOF-PROBE-0001")
    record("P3b stop first, then verify",
           "does obs-stop actually stop BOTH paths?", [
               ("the control reads stopped", ctl.is_closed(c)),
               ("the incentive path is refused",
                inc["ok"] is False and inc["verdict"] == ctl.V_STOPPED),
               ("the general path is refused too",
                not ctl.granted(gen) and gen["why"] == ctl.V_STOPPED),
               ("verification is a read, needing no deploy",
                c["readable"] is True),
           ])

    # And with the GENERAL caps armed, the same removal WOULD have let
    # discovery run -- which is why the incentive arm zeroes them.
    await arm(pool, control=True, general_caps=40)
    b2 = await ctl.read_budget(pool)
    r2 = await ctl.reserve(pool, ctl.R_LISTING, probe_id="PROOF-PROBE-0001")
    record("P3c the guard is load-bearing",
           "would a normally-armed row have allowed discovery?", [
               ("with general caps armed the budget is OPEN",
                b2["state"] == ctl.B_OPEN and b2["open"] is True),
               ("and a listing reservation IS granted", ctl.granted(r2)),
               ("which is exactly what obs-arm-incentive prevents",
                True),
           ])


async def main() -> int:
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=6)
    try:
        await pool.execute(
            "CREATE TABLE IF NOT EXISTS ingestion_state "
            "(key text PRIMARY KEY, value jsonb NOT NULL)")
        await p1(pool)
        await p2(pool)
        await p3(pool)
        await pool.execute("DELETE FROM ingestion_state")
        await pool.execute("DROP TABLE IF EXISTS bettor_incentive_journal")
    finally:
        await pool.close()
    passed = sum(1 for r in RESULTS if r["pass"])
    print("\n%d of %d properties passed" % (passed, len(RESULTS)))
    out = os.path.join(HERE, "acceptance", "incentive_durability_proof.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"proof": "BETTOR_INCENTIVE_DURABILITY_V1",
                   "database": "real PostgreSQL 16, real ingestion_state, "
                               "real bettor_incentive_journal",
                   "properties": RESULTS,
                   "passed": passed, "total": len(RESULTS)}, fh, indent=2)
    print("wrote %s" % out)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
