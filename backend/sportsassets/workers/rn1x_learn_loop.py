"""Worker: THE CONTINUOUS EVALUATION CYCLE.

The audit listed what a continuous improvement loop needs and found none
of it connected: "prospective input/prediction logging, mature-label /
completeness joining, scheduled candidate training, artifact
registration, evaluation on new data, promotion/rejection receipts, and a
runtime model loader with rollback."

This loop supplies the middle of that list for the RN1X management
policy, and is explicit about the two ends it does NOT supply:

  SUPPLIED   scheduled evaluation on accumulated data; artifact
             registration in the EXISTING `bettor_learn_model` register;
             a rejection or eligibility receipt for every challenger,
             every cycle, with the gate's own reasons attached.
  NOT SUPPLIED, DELIBERATELY: a runtime model loader with rollback. There
             is nothing for it to load. The champion is MANAGEMENT-DEFINED
             and frozen, and a loader that could swap it would be this
             process rewriting management policy. A cleared challenger is
             recorded ELIGIBLE and a human decides.

THE MOST LIKELY OUTCOME IS A REJECTION, AND THAT IS THE POINT. The gate's
eligibility floor is 50 decided orders; this experiment will sit below it
for a long time, and the receipt will say INELIGIBLE rather than the
panel going quiet. A learning loop whose only visible output is silence
until it agrees with itself is not evidence of learning.

IT RE-EVALUATES FROM THE EVIDENCE, NOT FROM STORED METRICS. Each cycle
re-reads the source fills for the settled positions and re-runs every
arm. Caching an arm's score and comparing cached numbers across code
versions is how a comparison silently becomes a comparison of two
different implementations.

Kill: the `rn1x_learn` control row, or RN1X_LEARN=off.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time

from .. import bettor_rn1x_learn as learn
from .. import bettor_rn1x_store as store
from ..db import get_pool, heartbeat
from . import rn1x_shadow as shadow

log = logging.getLogger(__name__)

SERVICE = "rn1x_learn"
CONTROL_KEY = "rn1x_learn"
LOCK_KEY = 7723901544120033

# Evaluation is not a tick-rate activity: it re-runs every arm over every
# settled position, and running it every twenty seconds would burn the
# database for a number that cannot have moved.
CYCLE_S = 900.0
IDLE_S = 300.0
MAX_POSITIONS = 200


def enabled() -> bool:
    return os.getenv("RN1X_LEARN", "off").strip().lower() not in (
        "off", "0", "false", "no")


async def _running(conn) -> tuple[bool, str]:
    """Fail-closed, the same four ways as the experiment's own control."""
    try:
        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            CONTROL_KEY)
    except Exception as exc:                                   # noqa: BLE001
        return False, "CONTROL_UNREADABLE_%s" % type(exc).__name__
    if raw is None:
        return False, "CONTROL_ROW_ABSENT"
    if raw.strip().lower() != "true":
        return False, "CONTROL_ROW_NOT_TRUE"
    return True, "RUNNING"


async def _registry_ready(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = "
        "'public' AND table_name = 'bettor_learn_model'"))


_SETTLED = """
    SELECT p.position_id, p.source_trade_id, p.source_account,
           p.condition_id
      FROM rn1x_positions p
      JOIN rn1x_outcomes o ON o.position_id = p.position_id
     WHERE o.settled_at IS NOT NULL
     ORDER BY p.decision_ts
     LIMIT $1
"""


async def _seeds(conn, limit: int) -> list:
    """Rebuild each settled position's evidence set from `trades`.

    The SAME evidence and the SAME assigned inventory go to every arm.
    Nothing here re-selects positions per arm.
    """
    out = []
    for row in await conn.fetch(_SETTLED, int(limit)):
        rows = await conn.fetch(shadow._CONDITION_ROWS, row["condition_id"],
                                shadow.MAX_ROWS_PER_CONDITION)
        inv = await shadow.verify_initial_inventory(
            conn, whale_id=int(row["source_account"]),
            condition_id=row["condition_id"],
            trade_id=int(row["source_trade_id"]))
        mkt = await conn.fetchrow(
            "SELECT resolved_prices, extract(epoch FROM resolved_at)::float8"
            " AS ra FROM markets WHERE condition_id = $1",
            row["condition_id"])
        if mkt is None or mkt["resolved_prices"] is None:
            continue
        raw = mkt["resolved_prices"]
        prices = json.loads(raw) if isinstance(raw, str) else raw
        payouts = ({i: float(p) for i, p in enumerate(prices)}
                   if isinstance(prices, list)
                   else {int(k): float(v) for k, v in prices.items()})
        out.append({"rows": [dict(r) for r in rows], "payouts": payouts,
                    "resolved_at": mkt["ra"],
                    "source_whale_id": int(row["source_account"]),
                    "condition_id": row["condition_id"],
                    "initial_inventory_verified": inv["verified"]})
    return out


def _dataset_sha(seeds: list) -> str:
    """Which evidence produced this verdict, as one digest.

    A receipt that does not name its dataset cannot be checked later, and
    two receipts over different data would look comparable.
    """
    h = hashlib.sha256()
    for s in seeds:
        for r in s["rows"]:
            h.update(("%s:%s:%s:%s:%s\n" % (
                r["id"], r["outcome_index"], r["side"], r["size"],
                r["price"])).encode())
    return h.hexdigest()


async def _next_version(conn, challenger: str) -> int:
    """One version per (challenger, evaluation), monotone.

    Versions are per model_key in the existing register, so a challenger
    re-evaluated on more data gets a NEW version rather than overwriting
    its earlier verdict. The history of what was rejected, and on how much
    data, is the record that makes a later acceptance meaningful.
    """
    v = await conn.fetchval(
        "SELECT max(version) FROM bettor_learn_model WHERE model_key = $1",
        learn.MODEL_KEY)
    return int(v or 0) + 1


async def _already_recorded(conn, challenger: str, sha: str,
                            status: str) -> int | None:
    """Has this exact verdict on this exact dataset already been written?

    THE DEFECT THIS FIXES WAS MINE AND IT WAS WRITING JUNK EVERY CYCLE.
    The loop re-evaluates every CYCLE_S and `_next_version` always returns
    `max + 1`, so the `ON CONFLICT (model_key, version)` clause below could
    never fire and each cycle appended four MORE rows -- about 384 a day --
    to `bettor_learn_model`. That register is the EXISTING shared one that
    `render-ops sql learn-inventory` reads, so this was steadily filling a
    production table other things depend on with duplicates, and making
    `version` useless as the comparison ledger it is meant to be.

    A new version is warranted when the DATASET changed or the VERDICT
    changed. Re-evaluating identical data and reaching the same conclusion
    is not a new result and gets no row; the heartbeat still records that
    the cycle ran, so "it is working and nothing moved" stays visible
    without polluting the register.
    """
    return await conn.fetchval(
        "SELECT version FROM bettor_learn_model "
        "WHERE model_key = $1 AND params->>'challenger' = $2 "
        "AND dataset_sha = $3 AND status = $4 "
        "ORDER BY version DESC LIMIT 1",
        learn.MODEL_KEY, challenger, sha, status)


async def cycle(conn, *, code_sha="unknown") -> dict:
    running, why = await _running(conn)
    if not running:
        return {"ran": False, "state": "STOPPED", "why": why}
    rdy = await store.ready(conn)
    if not rdy["ok"]:
        return {"ran": False, "state": "BLOCKED", "why": rdy["blocker"],
                "missing_tables": rdy["missing"]}
    if not await _registry_ready(conn):
        return {"ran": False, "state": "BLOCKED",
                "why": "LEARN_REGISTRY_ABSENT",
                "detail": ("bettor_learn_model does not exist on this "
                           "database. The register is the EXISTING one "
                           "(render-ops sql learn-schema creates it); this "
                           "loop does not create a competing register of "
                           "its own")}

    seeds = await _seeds(conn, MAX_POSITIONS)
    if not seeds:
        return {"ran": True, "state": "NO_SETTLED_POSITIONS_YET",
                "why": ("the experiment has no settled positions to "
                        "evaluate. That is a state, not a result: there is "
                        "nothing here to say a challenger did worse than"),
                "challengers": learn.challenger_names()}

    ev = learn.evaluate(seeds)
    sha = _dataset_sha(seeds)
    cutoff = time.time()
    receipts = []
    for name in learn.challenger_names():
        rec = learn.recommend(ev, name)
        prior = await _already_recorded(conn, name, sha, rec["verdict"])
        if prior is not None:
            # SAME DATA, SAME VERDICT. Not a new result, so not a new row.
            receipts.append({"challenger": name, "version": int(prior),
                             "verdict": rec["verdict"], "written": False,
                             "why": ("unchanged from version %d on the "
                                     "same dataset" % int(prior))})
            continue
        version = await _next_version(conn, name)
        row = learn.registry_row(
            name, ev, rec, dataset_sha=sha, code_sha=code_sha,
            train_cutoff=cutoff, version=version)
        # ONE RECEIPT PER CHALLENGER PER CYCLE, written to the EXISTING
        # register. A rejection is written exactly like an acceptance.
        await conn.execute(
            "INSERT INTO bettor_learn_model (model_key, version, kind, "
            "target, horizon_s, kernel, dataset_sha, train_cutoff, "
            "calib_cutoff, code_sha, params, evaluation, status, "
            "trained_at, note) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,"
            "$11::jsonb,$12::jsonb,$13,$14,$15) "
            "ON CONFLICT (model_key, version) DO NOTHING",
            row["model_key"], row["version"], row["kind"], row["target"],
            row["horizon_s"], row["kernel"], row["dataset_sha"],
            row["train_cutoff"], row["calib_cutoff"], row["code_sha"],
            json.dumps(row["params"]), json.dumps(row["evaluation"],
                                                  default=str),
            row["status"], cutoff, row["note"])
        receipts.append({"challenger": name, "version": version,
                         "verdict": rec["verdict"], "written": True,
                         "why": rec["why"][:400]})

    retained = all(r["verdict"] == learn.RETAIN for r in receipts)
    return {"ran": True, "state": "EVALUATED",
            "positions": len(seeds), "decided": ev["decided"],
            "dataset_sha": sha[:12], "receipts": receipts,
            "new_rows": sum(1 for r in receipts if r.get("written")),
            "champion_retained": retained,
            "note": ("ELIGIBLE never changes the active policy. The "
                     "champion is management-defined and frozen; this loop "
                     "has no promotion path.")}


async def run(pool_factory=None) -> None:
    get = pool_factory or get_pool
    code_sha = (os.environ.get("RENDER_GIT_COMMIT") or "unknown")[:12]
    log.info("rn1x learn: armed; contending for its own lock")
    pool = await get()
    async with pool.acquire() as conn:
        while not await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", LOCK_KEY):
            log.info("rn1x learn STANDBY: lock held elsewhere; retrying")
            await heartbeat(SERVICE, "idle",
                            {"state": "STANDBY_NOT_THE_EVALUATOR"})
            await asyncio.sleep(IDLE_S)
        log.info("rn1x learn: evaluator lock held")
        while True:
            delay = CYCLE_S
            try:
                res = await cycle(conn, code_sha=code_sha)
                idle = res.get("state") in ("STOPPED", "BLOCKED",
                                            "NO_SETTLED_POSITIONS_YET")
                delay = IDLE_S if idle else CYCLE_S
                await heartbeat(SERVICE, "idle" if idle else "ok", res)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                           # noqa: BLE001
                log.exception("rn1x learn cycle failed")
                delay = IDLE_S
                try:
                    await heartbeat(SERVICE, "error",
                                    {"error": "%s: %s"
                                     % (type(exc).__name__, exc)})
                except Exception:                              # noqa: BLE001
                    pass
            await asyncio.sleep(delay)


async def main() -> None:
    """Not registered in `workers/all.py`; see rn1x_shadow.main."""
    if not enabled():
        log.info("rn1x learn: RN1X_LEARN is not on; not starting")
        return
    await run()
