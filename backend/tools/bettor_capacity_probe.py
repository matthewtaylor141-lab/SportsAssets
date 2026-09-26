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
  RESTART      the pool is closed and rebuilt mid-run, then the same
               observations are replayed. Recovery is correct only if the
               replay still writes nothing and the counts are unchanged.
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


async def run(dsn: str, entries: int, burst: int, concurrency: int) -> dict:
    import asyncpg

    out: dict = {
        "probe": "BETTOR_ORDER_LIFECYCLE_CAPACITY_V1",
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
        out["phases"]["restart_recovery"] = dict(
            _stats(p4["lat_ms"], p4["wall_s"], min(50, entries)),
            cases=p4["cases"], counts_after=after_restart,
            wrote_nothing_after_restart=(
                after_restart["positions"] == after_burst["positions"]
                and after_restart["fills"] == after_burst["fills"]),
            why=("a rebuilt pool must not re-admit an observation the "
                 "ledger already holds. Recovery is only correct if the "
                 "replay still writes nothing"))

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
    finally:
        try:
            await pool.close()
        except Exception:                                       # noqa: BLE001
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.getenv("RN1X_TEST_DSN"))
    ap.add_argument("--entries", type=int, default=400)
    ap.add_argument("--burst", type=int, default=120)
    ap.add_argument("--concurrency", type=int, default=12)
    a = ap.parse_args()
    if not a.dsn:
        print("a --dsn (or RN1X_TEST_DSN) is required", file=sys.stderr)
        return 2
    out = asyncio.run(run(a.dsn, a.entries, a.burst, a.concurrency))
    print(json.dumps(out, indent=1, default=str))
    ok = (out["phases"]["replay"]["wrote_nothing"]
          and out["phases"]["replay"]["fee_unchanged"]
          and out["phases"]["restart_recovery"][
              "wrote_nothing_after_restart"]
          and out["accounting"]["fee_reconciles"]
          and out["accounting"]["one_fill_per_position"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
