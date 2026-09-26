"""ORDER-LIFECYCLE CAPACITY, MEASURED THROUGH THE REAL WRITERS.

WHAT THIS MEASURES, AND WHAT IT DOES NOT. It measures how many entry
decisions the deployed lifecycle can turn into orders, fills, inventory
and accounting per unit time, against a real migrated database, through
`bettor_entry_inventory.plan_entry` and `persist_entry` -- the same two
functions the scheduled lane calls. It uses no stub and no fake
connection.

IT IS NOT A STATEMENT ABOUT OPPORTUNITY. Processing capacity and the
number of qualifying opportunities are different quantities and are
reported separately and never combined. A lane that can process 900
entries a day and admits none today has both facts true at once, and
quoting the first as if it were the second is the specific misreport this
file refuses to make. The workload below is SYNTHETIC by construction:
every plan is fabricated to exercise the writer, none came from a venue,
and none is evidence that such a trade existed.

WHAT IT EXERCISES, each reported on its own:

  THROUGHPUT   sustained entries per second, and the daily rate that
               implies, over a steady workload.
  BURST        the same writers under a burst of concurrent writes, which
               is where a duplicate or a lock-order defect shows up.
  DUPLICATES   the SAME observation replayed. `classify_write` must
               answer EXACT_REPLAY_OF_A_RECORDED_OBSERVATION and write
               nothing: no second position, no second order, no second
               fill, no second fee.
  CONNECTION   the pool is closed and rebuilt mid-run, then the same
  RECOVERY     observations are replayed. THIS IS CONNECTION RECOVERY AND
               NOT A PROCESS RESTART -- the interpreter, its module state
               and its caches all survive, so it exercises the database
               handle and nothing else. An actual process restart is a
               separate phase (`--restart-child`), which re-execs a child
               interpreter so nothing in memory carries over.
  ACCOUNTING   after every phase, basis and fees are summed from the
               ledger and compared against the writes that were accepted.
               A double-counted basis, a duplicated fee or a fill on an
               already-exited quantity is a failure, not a rounding note.

Run: python3 -m tools.bettor_capacity_probe --dsn ... --entries 400
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sportsassets import bettor_entry_inventory as inv          # noqa: E402
from sportsassets import bettor_external_shadow as ext          # noqa: E402

#: The fee schedule the lane uses, per contract, taker side.
FEE_PER_CONTRACT = 0.016


def _fee(qty, price, maker=False):
    return FEE_PER_CONTRACT * float(qty)


def _record(i: int) -> dict:
    """One synthetic entry decision. SYNTHETIC, and labelled so."""
    cid = "0x%064x" % (0xC0DE0000 + i)
    slug = "aec-cap-%05d-2026-09-26" % i
    return {
        "admissible": True,
        "experiment_id": ext.EXPERIMENT_ID,
        "observed_at": 1_000_000.0 + i,
        "received_at": 1_000_000.0 + i,
        "payout_event": "Capacity Probe Side A",
        "contract": {"condition_id": cid, "us_market_slug": slug,
                     "buy_intent": "ORDER_INTENT_BUY_LONG",
                     "event_key": "cap-%05d" % i},
        "execution_plan": {"execution": {
            "size": 100.0, "vwap": 0.62, "submitted_limit": 0.70,
            "intended_notional_usd": 1000.0,
            "unfilled_notional_usd": 938.0,
            "levels_taken": [{"price": 0.62, "qty": 100.0, "cost": 62.0}]}},
    }


async def _counts(pool) -> dict:
    async def one(sql):
        try:
            return await pool.fetchval(sql)
        except Exception as exc:                                # noqa: BLE001
            return "ERR:" + type(exc).__name__
    return {
        "positions": await one(
            "SELECT count(*) FROM rn1x_positions WHERE policy = '%s'"
            % inv.POLICY),
        "orders": await one(
            "SELECT count(*) FROM rn1x_orders o JOIN rn1x_positions p "
            "ON p.position_id = o.position_id WHERE p.policy = '%s'"
            % inv.POLICY),
        "fills": await one(
            "SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o "
            "ON o.order_id = f.order_id JOIN rn1x_positions p "
            "ON p.position_id = o.position_id WHERE p.policy = '%s'"
            % inv.POLICY),
        "fee_total": await one(
            "SELECT coalesce(sum(f.fee_usd), 0) FROM rn1x_fills f "
            "JOIN rn1x_orders o ON o.order_id = f.order_id "
            "JOIN rn1x_positions p ON p.position_id = o.position_id "
            "WHERE p.policy = '%s'" % inv.POLICY),
        "filled_qty_total": await one(
            "SELECT coalesce(sum(f.qty), 0) FROM rn1x_fills f "
            "JOIN rn1x_orders o ON o.order_id = f.order_id "
            "JOIN rn1x_positions p ON p.position_id = o.position_id "
            "WHERE p.policy = '%s'" % inv.POLICY),
    }


async def _write(pool, rec, now):
    plan = inv.plan_entry(rec, now=now, outcome_index=0, fee_fn=_fee)
    if not plan.get("ok"):
        return {"written": False, "case": "PLAN_REFUSED",
                "why": plan.get("why")}
    async with pool.acquire() as conn:
        return await inv.persist_entry(conn, plan)


async def _phase_serial(pool, records, now0):
    lat = []
    cases = {}
    t0 = time.perf_counter()
    for k, rec in enumerate(records):
        s = time.perf_counter()
        out = await _write(pool, rec, now0 + k)
        lat.append((time.perf_counter() - s) * 1000.0)
        c = out.get("case") or ("WRITTEN" if out.get("written") else "?")
        cases[c] = cases.get(c, 0) + 1
    return {"wall_s": round(time.perf_counter() - t0, 3),
            "cases": cases, "lat_ms": lat}


async def _phase_burst(pool, records, now0, concurrency):
    sem = asyncio.Semaphore(concurrency)
    lat = []
    cases = {}

    async def one(k, rec):
        async with sem:
            s = time.perf_counter()
            out = await _write(pool, rec, now0 + k)
            lat.append((time.perf_counter() - s) * 1000.0)
            c = out.get("case") or ("WRITTEN" if out.get("written") else "?")
            cases[c] = cases.get(c, 0) + 1

    t0 = time.perf_counter()
    await asyncio.gather(*(one(k, r) for k, r in enumerate(records)))
    return {"wall_s": round(time.perf_counter() - t0, 3),
            "cases": cases, "lat_ms": lat, "concurrency": concurrency}


def _stats(lat, wall, n):
    lat = sorted(lat)
    def pct(p):
        if not lat:
            return None
        return round(lat[min(len(lat) - 1, int(p * len(lat)))], 2)
    return {
        "n": n,
        "wall_s": wall,
        "entries_per_s": round(n / wall, 2) if wall > 0 else None,
        "implied_per_day": int(n / wall * 86400) if wall > 0 else None,
        "latency_ms": {"p50": pct(0.50), "p90": pct(0.90), "p99": pct(0.99),
                       "max": round(lat[-1], 2) if lat else None,
                       "mean": round(statistics.fmean(lat), 2) if lat else None},
    }


async def run(dsn: str, entries: int, burst: int, concurrency: int,
              lifecycles: int = 0, restart: int = 0) -> dict:
    import asyncpg

    out: dict = {
        "probe": "BETTOR_ORDER_LIFECYCLE_CAPACITY_V2",
        "scope": ("A WRITER MICROBENCHMARK plus, when --lifecycles is\n                  given, a bounded batch of COMPLETE lifecycles. The two\n                  are reported separately because they measure different\n                  things"),
        "workload_is_synthetic": True,
        "workload_note": (
            "every plan is fabricated to exercise the writer. None came "
            "from a venue and none is evidence that such a trade existed"),
        "processing_capacity_is_not_opportunity": (
            "entries per day here is what the lifecycle can PROCESS. It is "
            "not a count of qualifying opportunities and must never be "
            "quoted as one"),
        "policy": inv.POLICY,
        "fee_per_contract": FEE_PER_CONTRACT,
        "implied_per_day_is_an_extrapolation": (
            "entries_per_s is measured; implied_per_day multiplies it by "
            "86400 and is NOT a claim that the lane would process that "
            "many. The real constraints are the 900 s cycle, the shared "
            "venue read budget and the provider's credit limit -- none of "
            "which this probe touches. Read it as: the WRITER is not the "
            "bottleneck at hundreds of trades a day, by three orders of "
            "magnitude"),
        "phases": {},
    }
    pool = await asyncpg.create_pool(dsn, min_size=2, max_size=concurrency + 2)
    try:
        async with pool.acquire() as conn:
            await inv.ensure_experiment(conn, ext.EXPERIMENT_ID)
        out["baseline"] = await _counts(pool)

        # ── 1 · SUSTAINED THROUGHPUT ────────────────────────────────
        recs = [_record(i) for i in range(entries)]
        p = await _phase_serial(pool, recs, 2_000_000.0)
        out["phases"]["sustained"] = dict(
            _stats(p["lat_ms"], p["wall_s"], entries), cases=p["cases"])
        after_sustained = await _counts(pool)
        out["phases"]["sustained"]["counts_after"] = after_sustained

        # ── 2 · DUPLICATE PREVENTION, the same observations again ───
        p2 = await _phase_serial(pool, recs, 9_000_000.0)
        after_replay = await _counts(pool)
        out["phases"]["replay"] = dict(
            _stats(p2["lat_ms"], p2["wall_s"], entries), cases=p2["cases"],
            counts_after=after_replay,
            wrote_nothing=(after_replay["positions"]
                           == after_sustained["positions"]
                           and after_replay["fills"]
                           == after_sustained["fills"]
                           and after_replay["orders"]
                           == after_sustained["orders"]),
            fee_unchanged=(str(after_replay["fee_total"])
                           == str(after_sustained["fee_total"])))

        # ── 3 · BURST, concurrent writes of NEW observations ────────
        brecs = [_record(100_000 + i) for i in range(burst)]
        p3 = await _phase_burst(pool, brecs, 3_000_000.0, concurrency)
        after_burst = await _counts(pool)
        out["phases"]["burst"] = dict(
            _stats(p3["lat_ms"], p3["wall_s"], burst), cases=p3["cases"],
            concurrency=concurrency, counts_after=after_burst,
            positions_added=(after_burst["positions"]
                             - after_replay["positions"]),
            no_duplicate_position=(after_burst["positions"]
                                   - after_replay["positions"]) <= burst)

        # ── 4 · RESTART RECOVERY: drop the pool, rebuild, replay ────
        await pool.close()
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        p4 = await _phase_serial(pool, recs[:min(50, entries)], 4_000_000.0)
        after_restart = await _counts(pool)
        out["phases"]["connection_recovery"] = dict(
            _stats(p4["lat_ms"], p4["wall_s"], min(50, entries)),
            cases=p4["cases"], counts_after=after_restart,
            wrote_nothing_after_reconnect=(
                after_restart["positions"] == after_burst["positions"]
                and after_restart["fills"] == after_burst["fills"]),
            what_this_is=("CONNECTION RECOVERY. The pool is closed and "
                          "rebuilt; the interpreter, its imports and its "
                          "caches all survive. It is NOT a process restart "
                          "and must not be reported as one"),
            why=("a rebuilt pool must not re-admit an observation the "
                 "ledger already holds"))

        # ── 5 · ACCOUNTING RECONCILIATION ──────────────────────────
        final = await _counts(pool)
        pos = int(final["positions"]) - int(out["baseline"]["positions"])
        fills = int(final["fills"]) - int(out["baseline"]["fills"])
        qty = float(final["filled_qty_total"]) - float(
            out["baseline"]["filled_qty_total"])
        fees = float(final["fee_total"]) - float(out["baseline"]["fee_total"])
        expect_fee = round(FEE_PER_CONTRACT * qty, 6)
        out["accounting"] = {
            "positions_created": pos,
            "fills_recorded": fills,
            "one_fill_per_position": fills == pos,
            "filled_qty_total": round(qty, 6),
            "fee_total": round(fees, 6),
            "fee_expected_from_qty": expect_fee,
            "fee_reconciles": abs(fees - expect_fee) < 0.01,
            "orders_equal_positions": (int(final["orders"])
                                       - int(out["baseline"]["orders"])) == pos,
            "why": ("every accepted entry writes exactly one position, one "
                    "order and one fill, and the fee total must equal the "
                    "schedule times the filled quantity. A mismatch is a "
                    "double count, not a rounding note"),
        }
        out["final_counts"] = final

        # ── 6 · COMPLETE LIFECYCLES, and the duplicate delivery of them ─
        if lifecycles:
            async with pool.acquire() as conn:
                await inv.ensure_experiment(conn, LIFECYCLE_EXPERIMENT)
            out["phases"]["complete_lifecycles"] = await phase_lifecycles(
                pool, lifecycles)
            out["phases"]["duplicate_lifecycles"] = \
                await phase_duplicate_lifecycles(
                    pool, min(lifecycles, 5))

        # ── 7 · AN ACTUAL PROCESS RESTART ──────────────────────────────
        if restart:
            async with pool.acquire() as conn:
                await inv.ensure_experiment(conn, LIFECYCLE_EXPERIMENT)
            lo = 500_000
            out["phases"]["actual_process_restart"] = \
                await phase_process_restart(
                    pool, dsn, lo, lo + restart,
                    kill_after=max(1, restart // 3))
    finally:
        try:
            await pool.close()
        except Exception:                                       # noqa: BLE001
            pass
    return out



# ── COMPLETE LIFECYCLES ─────────────────────────────────────────────
#
# WHY THIS PHASE EXISTS. Everything above measures the ENTRY WRITER: one
# plan in, one position, one order and one fill out. That is a writer
# microbenchmark and it was reported as one, but it is not what the lane
# does for a living. A complete lifecycle is entry, recurring management
# cycles, a marketable reduction, a completing exit and reconciled
# accounting -- five to eight writes, several reads and a decision each
# time -- and its cost per position is an order of magnitude larger.
#
# IT RUNS THE SAME DEPLOYED FUNCTIONS. `bettor_demonstration.run_full`
# drives `plan_entry`/`persist_entry`, `manage_open_position`,
# `persist_run` and `settle_open_positions` -- the scheduled lane's own
# components -- with CHOSEN inputs, on this harness's own book. The inputs
# are synthetic; the code under measurement is not.
LIFECYCLE_EXPERIMENT = "CAPACITY_PROBE_LIFECYCLE_V1"


def _lc_identity(i: int) -> tuple:
    """A distinct contract per lifecycle. Synthetic, and unmistakably so."""
    return ("0x%064x" % (0xCAFE0000 + i),
            "aec-lifecycle-%05d-2026-09-26" % i,
            "Capacity Lifecycle %05d Side A" % i)


async def _one_lifecycle(pool, i: int) -> dict:
    """One complete lifecycle. Returns what happened, including a failure."""
    from sportsassets import bettor_demonstration as D

    cid, slug, pays = _lc_identity(i)
    t0 = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            got = await D.run_full(conn, experiment=LIFECYCLE_EXPERIMENT,
                                   cid=cid, slug=slug, pays_on=pays)
    except Exception as exc:                                    # noqa: BLE001
        return {"i": i, "ms": (time.perf_counter() - t0) * 1000.0,
                "completed": False, "error": "%s: %s" % (type(exc).__name__,
                                                         exc)}
    rec = (got.get("reconciliation") or {})
    rows = rec.get("positions") or []
    r0 = rows[0] if rows else {}
    return {
        "i": i, "ms": (time.perf_counter() - t0) * 1000.0,
        "cycles_run": got.get("cycles_run"),
        "flat": bool(got.get("position_is_flat")),
        "reconciles": bool(rec.get("reconciles")),
        # COMPLETED MEANS THE POSITION WENT FLAT AND THE LEDGER ADDS UP.
        # A lifecycle that merely ran without raising is not completed.
        "completed": bool(got.get("position_is_flat")
                          and rec.get("reconciles")),
        "bought": r0.get("bought_qty"), "sold": r0.get("sold_qty"),
        "held": r0.get("held_qty"), "fees_usd": r0.get("fees_usd"),
        "realised_net_usd": r0.get("realised_net_of_fees_usd"),
        "decisions": r0.get("decisions"), "orders": r0.get("orders"),
        "fills": r0.get("fills"),
        "identity_holds": r0.get("quantity_identity_holds"),
    }


async def _lc_counts(pool) -> dict:
    """The lifecycle book's own totals, by the experiment that owns it."""
    async def one(sql):
        try:
            return await pool.fetchval(sql, LIFECYCLE_EXPERIMENT)
        except Exception as exc:                                # noqa: BLE001
            return "ERR:" + type(exc).__name__
    return {
        "positions": await one(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = $1"),
        "orders": await one(
            "SELECT count(*) FROM rn1x_orders o JOIN rn1x_positions p "
            "ON p.position_id = o.position_id WHERE p.experiment_id = $1"),
        "fills": await one(
            "SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o "
            "ON o.order_id = f.order_id JOIN rn1x_positions p "
            "ON p.position_id = o.position_id WHERE p.experiment_id = $1"),
        "decisions": await one(
            "SELECT count(*) FROM rn1x_decisions d JOIN rn1x_positions p "
            "ON p.position_id = d.position_id WHERE p.experiment_id = $1"),
        "fee_total": await one(
            "SELECT coalesce(sum(f.fee_usd), 0) FROM rn1x_fills f "
            "JOIN rn1x_orders o ON o.order_id = f.order_id "
            "JOIN rn1x_positions p ON p.position_id = o.position_id "
            "WHERE p.experiment_id = $1"),
        "filled_qty_total": await one(
            "SELECT coalesce(sum(f.qty), 0) FROM rn1x_fills f "
            "JOIN rn1x_orders o ON o.order_id = f.order_id "
            "JOIN rn1x_positions p ON p.position_id = o.position_id "
            "WHERE p.experiment_id = $1"),
    }


async def phase_lifecycles(pool, n: int) -> dict:
    """A bounded batch of complete lifecycles, measured end to end."""
    before = await _lc_counts(pool)
    results = []
    t0 = time.perf_counter()
    for i in range(n):
        results.append(await _one_lifecycle(pool, i))
    wall = time.perf_counter() - t0
    after = await _lc_counts(pool)
    done = [r for r in results if r.get("completed")]
    failed = [r for r in results if not r.get("completed")]
    qty = float(after["filled_qty_total"]) - float(
        before["filled_qty_total"])
    fees = float(after["fee_total"]) - float(before["fee_total"])
    expect = round(FEE_PER_CONTRACT * qty, 6)
    return dict(
        _stats([r["ms"] for r in results], round(wall, 3), n),
        phase="complete_lifecycles",
        completed=len(done), attempted=n,
        completion_rate=(round(len(done) / n, 4) if n else None),
        failures=[{k: r.get(k) for k in ("i", "error", "flat", "reconciles",
                                         "cycles_run")}
                  for r in failed][:20],
        failure_count=len(failed),
        # EVERY LIFECYCLE'S OWN ARITHMETIC, and then the book's.
        all_identities_hold=all(r.get("identity_holds") is True
                                for r in done),
        median_cycles=(statistics.median([r["cycles_run"] for r in done])
                       if done else None),
        median_writes_per_lifecycle=(
            statistics.median([(r.get("orders") or 0) + (r.get("fills") or 0)
                               + (r.get("decisions") or 0) for r in done])
            if done else None),
        counts_before=before, counts_after=after,
        accounting={
            "filled_qty_added": round(qty, 6),
            "fee_added": round(fees, 6),
            "fee_expected_from_qty": expect,
            "fee_reconciles": abs(fees - expect) < 0.01,
            "buys_equal_sells": all(
                abs(float(r.get("bought") or 0)
                    - float(r.get("sold") or 0)) < 1e-9 for r in done),
            "discrepancies": [
                {"i": r["i"], "bought": r.get("bought"),
                 "sold": r.get("sold"), "held": r.get("held")}
                for r in done
                if abs(float(r.get("held") or 0)) > 1e-9],
        },
        lifecycles_per_s=(round(n / wall, 3) if wall > 0 else None),
        what_this_measures=(
            "a COMPLETE lifecycle: entry, recurring management, a "
            "reduction, a completing exit and reconciled accounting, "
            "through the deployed functions. Its inputs are chosen; its "
            "code is production's"),
        what_it_does_not_measure=(
            "opportunity. It says nothing about how many contracts a venue "
            "would show, a provider would price or the gates would admit"),
        examples=results[:3],
    )


async def phase_duplicate_lifecycles(pool, n: int) -> dict:
    """THE WHOLE LIFECYCLE, DELIVERED TWICE. Nothing may be written.

    Not just the entry: the management decisions carry derived ids too, so
    re-running the lifecycle must add no decision, no order and no fill,
    and must not move a single fee.
    """
    before = await _lc_counts(pool)
    t0 = time.perf_counter()
    results = [await _one_lifecycle(pool, i) for i in range(n)]
    wall = time.perf_counter() - t0
    after = await _lc_counts(pool)
    same = {k: (str(before[k]) == str(after[k])) for k in before}
    return dict(
        _stats([r["ms"] for r in results], round(wall, 3), n),
        phase="duplicate_delivery_of_complete_lifecycles",
        redelivered=n,
        counts_before=before, counts_after=after,
        unchanged=same,
        wrote_nothing=all(same.values()),
        why=("a re-delivered observation is a duplicate at every stage of "
             "the lifecycle, not only at entry. One extra fill here would "
             "be a fabricated contract and a fabricated fee"))


# ── AN ACTUAL PROCESS RESTART ───────────────────────────────────────
#
# WHAT MAKES THIS DIFFERENT FROM `connection_recovery`. There, the pool is
# closed and rebuilt inside a living interpreter: the imports, the module
# globals and every cache survive. Here a CHILD INTERPRETER is spawned,
# killed with SIGKILL part-way through its batch, and a SECOND child is
# spawned to finish the same batch. Nothing in memory carries over, the
# first child gets no chance to clean up, and the only state the second
# child can rely on is what reached the database.
RESTART_MARKER = "LIFECYCLE_DONE "


async def _child_lifecycles(dsn: str, lo: int, hi: int) -> int:
    """The child's own job: run lifecycles lo..hi, announcing each."""
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        for i in range(lo, hi):
            got = await _one_lifecycle(pool, i)
            print(RESTART_MARKER + json.dumps(
                {"i": i, "completed": got.get("completed")}), flush=True)
    finally:
        await pool.close()
    return 0


async def phase_process_restart(pool, dsn: str, lo: int, hi: int,
                                kill_after: int) -> dict:
    """Kill a child mid-batch, then finish the batch in a NEW process."""
    import subprocess

    out = {"phase": "actual_process_restart", "range": [lo, hi],
           "kill_after_completions": kill_after}
    cmd = [sys.executable, "-m", "tools.bettor_capacity_probe",
           "--dsn", dsn, "--child-lifecycles", "%d:%d" % (lo, hi)]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    first = subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    seen = []
    t0 = time.perf_counter()
    try:
        while len(seen) < kill_after and time.perf_counter() - t0 < 180:
            line = first.stdout.readline()
            if not line:
                break
            if line.startswith(RESTART_MARKER):
                seen.append(json.loads(line[len(RESTART_MARKER):]))
    finally:
        first.kill()                      # SIGKILL: no cleanup, no flush
        try:
            first.wait(timeout=20)
        except Exception:                                       # noqa: BLE001
            pass
    out["first_child"] = {"pid": first.pid, "completed_before_kill":
                          len(seen), "returncode": first.returncode,
                          "killed_with": "SIGKILL"}
    mid = await _lc_counts(pool)
    out["counts_after_kill"] = mid
    second = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                            timeout=600)
    done2 = [json.loads(ln[len(RESTART_MARKER):])
             for ln in (second.stdout or "").splitlines()
             if ln.startswith(RESTART_MARKER)]
    out["second_child"] = {"returncode": second.returncode,
                           "lifecycles_reported": len(done2),
                           "all_completed": all(d.get("completed")
                                                for d in done2) and
                           bool(done2),
                           "stderr_tail": (second.stderr or "")[-400:]}
    after = await _lc_counts(pool)
    out["counts_after_restart"] = after
    # ONE POSITION PER CONTRACT IN THE RANGE, AND NOT ONE MORE. A restart
    # that re-admitted an entry would show up here as a duplicate row.
    n = 0
    dupes = []
    flat = []
    for i in range(lo, hi):
        cid, _slug, _p = _lc_identity(i)
        rows = await pool.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = $1 "
            "AND condition_id = $2", LIFECYCLE_EXPERIMENT, cid)
        n += int(rows or 0)
        if int(rows or 0) > 1:
            dupes.append({"i": i, "positions": int(rows)})
        held = await pool.fetchval(
            "SELECT coalesce(sum(CASE WHEN upper(o.side) = 'BUY' THEN f.qty "
            "ELSE -f.qty END), 0)::float8 FROM rn1x_fills f "
            "JOIN rn1x_orders o ON o.order_id = f.order_id "
            "JOIN rn1x_positions p ON p.position_id = o.position_id "
            "WHERE p.experiment_id = $1 AND p.condition_id = $2",
            LIFECYCLE_EXPERIMENT, cid)
        flat.append(abs(float(held or 0.0)) < 1e-9)
    qty = float(after["filled_qty_total"]) - float(mid["filled_qty_total"])
    fees = float(after["fee_total"]) - float(mid["fee_total"])
    out.update(
        positions_in_range=n, expected_positions=hi - lo,
        duplicate_positions=dupes,
        no_duplicate_position=(n == hi - lo and not dupes),
        all_positions_flat=all(flat),
        flat_count=sum(1 for f in flat if f),
        fee_added_after_restart=round(fees, 6),
        fee_expected_after_restart=round(FEE_PER_CONTRACT * qty, 6),
        fee_reconciles=abs(fees - FEE_PER_CONTRACT * qty) < 0.01,
        what_this_is=(
            "AN ACTUAL PROCESS RESTART. A child interpreter was SIGKILLed "
            "part-way through its batch and a second child finished the "
            "same batch. No module state, cache or connection survived, "
            "and the only thing the second process could rely on is what "
            "reached the database"),
        what_it_proves=(
            "the ledger is the state. A killed writer leaves no duplicate "
            "and no half-written position that a new process would "
            "re-admit"),
        what_it_does_not_prove=(
            "that the hosted scheduler recovers on Render's own restart "
            "path, which has its own lock and its own cadence"))
    out["ok"] = bool(out["no_duplicate_position"]
                     and out["all_positions_flat"]
                     and out["fee_reconciles"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.getenv("RN1X_TEST_DSN"))
    ap.add_argument("--entries", type=int, default=400)
    ap.add_argument("--burst", type=int, default=120)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--lifecycles", type=int, default=0,
                    help="run this many COMPLETE lifecycles as well")
    ap.add_argument("--restart", type=int, default=0,
                    help="lifecycles for the actual process-restart phase")
    ap.add_argument("--child-lifecycles", default="",
                    help="INTERNAL: lo:hi, the child process's own batch")
    a = ap.parse_args()
    if a.child_lifecycles:
        # THE CHILD PROCESS. It runs lifecycles and announces each one; the
        # parent kills it mid-batch. It prints no report of its own.
        lo, hi = (int(x) for x in a.child_lifecycles.split(":"))
        return asyncio.run(_child_lifecycles(a.dsn, lo, hi))
    if not a.dsn:
        print("a --dsn (or RN1X_TEST_DSN) is required", file=sys.stderr)
        return 2
    out = asyncio.run(run(a.dsn, a.entries, a.burst, a.concurrency,
                          lifecycles=a.lifecycles, restart=a.restart))
    print(json.dumps(out, indent=1, default=str))
    lc = out["phases"].get("complete_lifecycles") or {}
    dup = out["phases"].get("duplicate_lifecycles") or {}
    rst = out["phases"].get("actual_process_restart") or {}
    ok = ((lc.get("completion_rate") in (None, 1.0))
          and (dup.get("wrote_nothing") in (None, True))
          and (rst.get("ok") in (None, True))
          and out["phases"]["replay"]["wrote_nothing"]
          and out["phases"]["replay"]["fee_unchanged"]
          and out["phases"]["connection_recovery"][
              "wrote_nothing_after_reconnect"]
          and out["accounting"]["fee_reconciles"]
          and out["accounting"]["one_fill_per_position"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
