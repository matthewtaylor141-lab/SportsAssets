"""Worker: THE RN1-SEEDED MANAGEMENT EXPERIMENT, ACTUALLY RUNNING.

The audit's row for this path read: "Batch runner; no API/worker caller,
no database persistence caller, no published trace route. Migration
existing in git is not production deployment." This loop is the worker
caller. `bettor_rn1x_store` is the persistence caller. `api/command_rn1x`
is the published route.

WHAT IT DOES, ONCE PER CYCLE.

    1. READ THE CONTROL ROW and fail closed. Absent, false, malformed or
       unreadable all mean STOPPED. Absence is not permission.
    2. VERIFY THE TABLES against the live catalog, not the migrations
       directory. Migration 100 sat committed at HEAD for hours while
       the production schema had none of its tables.
    3. TAKE ONE SEED CANDIDATE past the cursor -- one real cohort fill on
       a condition that has since RESOLVED.
    4. VERIFY INITIAL INVENTORY from the record, and refuse when it
       cannot be verified.
    5. REPLAY it through `bettor_rn1x_run.run`, which is the existing
       chain: entry classification, the frozen management policy, the
       existing `Managed` lifecycle over `bettor_desk.Order`/`Portfolio`.
    6. PERSIST the whole thing in one transaction, or record the refusal.

IT IS A HISTORICAL REPLAY AND SAYS SO IN EVERY ROW. Not a forward
shadow. The runner hard-codes `mode = HISTORICAL_REPLAY` and
`prospective = false`, and that is not a limitation of this loop -- it is
forced by the feed. The measured RN1 detection lag on the Santos
condition was 15.07 hours, POSTDATING that market's settlement by 11.22
hours. A forward lane seeded by this feed would be deciding on events
that had already resolved. The external dependency that would change
that is named in `BLOCKERS` and nowhere is it worked around.

NO CAPITAL, NO ORDERS, NO VENUE. Every order it writes is modelled and
the schema CHECKs `is_modelled`. There is no submit, cancel or funding
path in this module's import closure, and it never touches the desk's
tables or `acct_fc2d773a2afa4851`, which stays paused and
ACCOUNTING_UNCERTAIN as evidence.

Kill: the control row (prompt, no deploy) or RN1X_SHADOW=off (a deploy).
"""

from __future__ import annotations

import asyncio
import logging
import os

from .. import bettor_rn1x_policy as pol
from .. import bettor_rn1x_run as runner
from .. import bettor_rn1x_store as store
from ..db import get_pool, heartbeat

log = logging.getLogger(__name__)

SERVICE = "rn1x_shadow"
CONTROL_KEY = "rn1x_shadow"
CURSOR_KEY = "rn1x_source_cursor"

TICK_S = 20.0
IDLE_S = 120.0
# Bounded by construction. `trades` holds ~6.1M rows and a count(DISTINCT
# condition_id) over it has already blown a 240-second statement timeout
# in this project; nothing here scans the table.
SCAN_BATCH = 400          # trades rows examined per cycle
MAX_SEEDS_PER_CYCLE = 3   # conditions replayed per cycle
MAX_ROWS_PER_CONDITION = 4000

EXPERIMENT_ID = "RN1X_MGMT_PAIR091_STOP16_V1"
EXECUTION_BASIS = "PRINT_THROUGH_WITH_QUEUE_SHARE_V1"

# Named blockers, so the command centre shows a reason rather than an
# empty panel. Each is a genuine external dependency, not a switch.
BLOCKERS = {
    "SECOND_HALF_UNDEFINED": (
        "No timestamped event-progress feed is connected, so the "
        "second-half condition in MANAGEMENT_PAIR_091_STOP_16_V1 cannot "
        "be evaluated for any sport. The loss exit is therefore "
        "UNAVAILABLE on every position and the experiment currently "
        "tests the PAIRING half of the policy only. MISSING DEPENDENCY: "
        "a per-sport event clock with observed period/half boundaries, "
        "joined point-in-time to the decision instant."),
    "NO_CONTEMPORANEOUS_BOOK": (
        "No bid/ask/depth is retained for these historical instants. "
        "The tape prints in `trades` are executions by others, not a "
        "standing book, so `bid` and `bid_size` are not supplied and a "
        "trigger that needs an executable bid cannot fire. MISSING "
        "DEPENDENCY: an archived same-venue order book at the decision "
        "instants."),
    "FEED_POSTDATES_SETTLEMENT": (
        "RN1 fill detection lagged 15.07 hours on the measured seed and "
        "postdated that market's settlement by 11.22 hours, so no "
        "forward lane can be seeded from this feed. This experiment is "
        "a HISTORICAL REPLAY and is labelled so in every row."),
}


def _off(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).strip().lower() in (
        "off", "0", "false", "no")


# ── the control row, fail-closed four ways ──────────────────────────
async def _running(conn) -> tuple[bool, str]:
    """STOPPED unless the row says, in so many words, true.

    Absent, false, malformed, unreadable -- all STOPPED. This is the same
    shape `bettor_live_loop` uses, and for the same reason: the database
    row is the authoritative stop because it is the PROMPT one. A deploy
    is not needed to stop this loop and must not be.
    """
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


async def _cursor(conn) -> int:
    raw = await conn.fetchval(
        "SELECT value::text FROM ingestion_state WHERE key = $1", CURSOR_KEY)
    try:
        return int(str(raw).strip().strip('"'))
    except (TypeError, ValueError):
        return 0


async def _save_cursor(conn, value: int) -> None:
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        CURSOR_KEY, str(int(value)))


# ── seed candidates: real cohort fills on RESOLVED conditions ───────
#
# BUYS ONLY, because the runner refuses a SELL seed: a sale is not an
# entry and seeding a position from one would invent the inventory it
# sold. The join to `markets` is what makes the replay scorable -- an
# unresolved condition has no observed payout and step 8 would have
# nothing to read.
_CANDIDATES = """
    SELECT t.id, t.whale_id, t.condition_id
      FROM trades t
      JOIN markets m ON m.condition_id = t.condition_id
     WHERE t.id > $1
       AND t.condition_id IS NOT NULL
       AND t.outcome_index IN (0, 1)
       AND t.side = 'BUY'
       AND m.resolved
       AND m.resolved_prices IS NOT NULL
     ORDER BY t.id
     LIMIT $2
"""

# Every recorded fill on the condition, ours and others'. The others'
# are the EVIDENCE that licenses a modelled fill; without them a resting
# order could never fill and the experiment would be a hold-only test by
# omission rather than by policy.
_CONDITION_ROWS = """
    SELECT t.id, t.whale_id, t.outcome_index, t.side,
           t.size::float8 AS size, t.price::float8 AS price,
           extract(epoch FROM t.ts)::float8 AS ts,
           extract(epoch FROM t.detected_at)::float8 AS detected_at
      FROM trades t
     WHERE t.condition_id = $1
       AND t.outcome_index IN (0, 1)
     ORDER BY t.id
     LIMIT $2
"""


async def verify_initial_inventory(conn, *, whale_id, condition_id,
                                   trade_id) -> dict:
    """Was the account FLAT on this condition before the seed fill?

    THE AUDIT'S DEFECT 4 WAS THAT A TRUNCATED FIRST BUY WAS ENOUGH TO
    ASSIGN A POSITION, and its repair made the runner demand that the
    caller supply verified initial-inventory status -- while noting,
    correctly, that "the flag itself is not proof". So this function
    computes it rather than asserting it, from two facts in the record:

      prior_fills   the account's own earlier fills on this condition
      coverage_ok   our first recorded trade for this account is at or
                    before the condition's first recorded trade

    Both are needed. Zero prior fills alone proves nothing if our
    collection of this account began after the market opened -- the
    account could have bought before we were watching. The coverage
    comparison is what rules that out.

    IT IS STILL A BOUND, NOT A CERTIFICATE. It establishes flatness with
    respect to WHAT WE RECORDED. A gap inside our collection window
    would not show up here, and that limitation is returned with the
    verdict rather than swallowed.
    """
    prior = await conn.fetchval(
        "SELECT count(*) FROM trades WHERE whale_id = $1 "
        "AND condition_id = $2 AND id < $3",
        int(whale_id), condition_id, int(trade_id))
    acct_first = await conn.fetchval(
        "SELECT min(ts) FROM trades WHERE whale_id = $1", int(whale_id))
    cond_first = await conn.fetchval(
        "SELECT min(ts) FROM trades WHERE condition_id = $1", condition_id)
    coverage_ok = bool(acct_first and cond_first and acct_first <= cond_first)
    verified = int(prior or 0) == 0 and coverage_ok
    return {
        "verified": verified,
        "prior_fills_on_condition": int(prior or 0),
        "account_first_recorded_fill": acct_first,
        "condition_first_recorded_trade": cond_first,
        "coverage_precedes_condition": coverage_ok,
        "why_not": (None if verified else
                    ("the account has %d earlier recorded fill(s) on this "
                     "condition" % int(prior or 0)) if int(prior or 0)
                    else ("our record of this account begins after the "
                          "condition's first recorded trade, so a position "
                          "held before we were watching cannot be ruled "
                          "out")),
        "limitation": ("flatness is established with respect to RECORDED "
                       "fills. A gap inside the collection window would "
                       "not be visible here"),
    }


async def _replay_one(conn, *, trade_id, whale_id, condition_id) -> dict:
    rows = await conn.fetch(_CONDITION_ROWS, condition_id,
                            MAX_ROWS_PER_CONDITION)
    inv = await verify_initial_inventory(
        conn, whale_id=whale_id, condition_id=condition_id,
        trade_id=trade_id)
    mkt = await conn.fetchrow(
        "SELECT resolved_prices, extract(epoch FROM resolved_at)::float8 "
        "AS resolved_at FROM markets WHERE condition_id = $1", condition_id)

    payouts = None
    resolved_at = None
    if mkt is not None and mkt["resolved_prices"] is not None:
        import json as _json
        raw = mkt["resolved_prices"]
        prices = _json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(prices, list):
            payouts = {i: float(p) for i, p in enumerate(prices)}
        elif isinstance(prices, dict):
            payouts = {int(k): float(v) for k, v in prices.items()}
        resolved_at = mkt["resolved_at"]

    out = runner.run(
        rows=[dict(r) for r in rows],
        payouts=payouts, resolved_at=resolved_at,
        source_whale_id=whale_id, condition_id=condition_id,
        initial_inventory_verified=inv["verified"])
    out["initial_inventory_evidence"] = inv
    out["resolved_at"] = resolved_at
    out["blockers"] = sorted(BLOCKERS)
    return out


async def cycle(conn) -> dict:
    """One cycle. Never raises; returns what it did or why it did not."""
    running, why = await _running(conn)
    if not running:
        return {"ran": False, "state": "STOPPED", "why": why}

    rdy = await store.ready(conn)
    if not rdy["ok"]:
        return {"ran": False, "state": "BLOCKED", "why": rdy["blocker"],
                "missing_tables": rdy["missing"]}

    await store.declare_experiment(
        conn, experiment_id=EXPERIMENT_ID,
        code_version=runner.VERSION,
        seed_rule={
            "source": "cohort BUY fill in `trades` on a RESOLVED market",
            "venue": "POLYMARKET_GLOBAL_POLYGON",
            "event_class": "SINGLE_ACCOUNT_EXECUTIONS",
            "seed_price": "RN1's own fill price, ASSIGNED",
            "not_evidence_of": ("that we could have obtained this fill"),
        },
        policy_register=pol.describe(),
        execution_basis=EXECUTION_BASIS,
        notes=("HISTORICAL REPLAY. Management-defined and experimental; "
               "not learned and not proven profitable."))

    start = await _cursor(conn)
    cands = await conn.fetch(_CANDIDATES, start, SCAN_BATCH)
    if not cands:
        return {"ran": True, "state": "IDLE_NO_CANDIDATES",
                "cursor": start}

    seen_conditions: set[str] = set()
    results = []
    # THE CURSOR IS THE HIGHEST ROW WHOSE PROCESSING FINISHED, and
    # nothing beyond it. `safe_id` only ever takes the id of a row that
    # was written, refused for a stated reason, or deliberately skipped.
    # A row whose write FAILED never advances it, so the next cycle sees
    # that row again instead of the evidence being lost for good.
    #
    # This is the desk's recovery defect in another place: there, a
    # rollback left the cursor advanced and the loop skipped to a newer
    # feed head. A cursor that moves past a write that did not commit
    # loses records silently, which is the worst available outcome --
    # worse than reprocessing, because reprocessing is idempotent here
    # and the loss is not recoverable.
    safe_id = start
    stopped_at_error = None
    for r in cands:
        rid = int(r["id"])
        if len(results) >= MAX_SEEDS_PER_CYCLE:
            # Stop BEFORE consuming this row: it has not been examined.
            break
        if r["condition_id"] in seen_conditions:
            safe_id = rid
            continue
        seen_conditions.add(r["condition_id"])
        # Already replayed under this experiment and policy? The store's
        # key is derived, so this is a cheap check rather than a second
        # write that would be a no-op anyway.
        pid = store.position_id(EXPERIMENT_ID, pol.POLICY_ID, rid)
        if await conn.fetchval(
                "SELECT 1 FROM rn1x_positions WHERE position_id = $1", pid):
            safe_id = rid
            continue
        try:
            out = await _replay_one(
                conn, trade_id=rid, whale_id=int(r["whale_id"]),
                condition_id=r["condition_id"])
            wrote = await store.persist_run(
                conn, out, experiment_id=EXPERIMENT_ID,
                source_account=str(r["whale_id"]))
        except Exception as exc:                               # noqa: BLE001
            log.exception("rn1x replay failed for trade %s", rid)
            results.append({"trade_id": rid, "written": False,
                            "error": "%s: %s" % (type(exc).__name__, exc)})
            # STOP HERE. Continuing would process later rows and then
            # save a cursor above this one, which is how the failed row
            # would be skipped.
            stopped_at_error = rid
            break
        results.append({"trade_id": rid,
                        "condition_id": r["condition_id"], **wrote})
        safe_id = rid

    await _save_cursor(conn, safe_id)
    return {"ran": True, "state": "REPLAYED", "cursor": safe_id,
            "examined": len(cands), "results": results,
            "stopped_at_error": stopped_at_error,
            "written": sum(1 for x in results if x.get("written"))}


# ── ONE WRITER, AND A STANDBY THAT ACTUALLY RETRIES ─────────────────
#
# Its OWN lock key, not the desk's: these are two different books and a
# shared key would make one of them silently a standby of the other.
#
# The retry is the audit's defect 7, which I am not repeating here. The
# desk's standby did `if not acquired: while True: sleep` and never asked
# for the lock again, so a standby could never take over -- and that is
# the containment incident at 16:41 I had attributed to a deploy. This
# loop asks again every cycle, BEFORE any startup work.
LOCK_KEY = 7723901544120032


def enabled() -> bool:
    """The env pre-check. NOT the authority -- the control row is."""
    return not _off("RN1X_SHADOW", "off")


async def run(pool_factory=None) -> None:
    """Contend for the writer lock, then cycle. Hosted in the API.

    WHY THE API AND NOT THE WORKER SERVICE. sportsassets-api and
    sportsassets-workers both track the same auto-deploy branch, so one
    push to it restarts the observation collector mid-window and writes a
    PROCESS_REPLACED gap -- that has already happened once. The API can
    be released by commit id without any push, which is how the desk loop
    came to be hosted here too. This is a deployment constraint, recorded
    rather than worked around.
    """
    get = pool_factory or get_pool
    log.info("rn1x shadow: armed; contending for the writer lock. The "
             "CONTROL ROW decides whether it runs -- absence is not "
             "permission.")
    pool = await get()
    async with pool.acquire() as conn:
        # THE LOCK IS SESSION-SCOPED, so it must be held on ONE
        # connection for the loop's whole life -- not acquired and
        # returned to the pool each cycle, which would release it.
        while not await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", LOCK_KEY):
            log.info("rn1x shadow STANDBY: another process holds the "
                     "writer lock; retrying in %ss", IDLE_S)
            await heartbeat(SERVICE, "idle",
                            {"state": "STANDBY_NOT_THE_WRITER",
                             "why": ("another process holds the rn1x "
                                     "writer lock. This process writes "
                                     "nothing and retries")})
            await asyncio.sleep(IDLE_S)
        log.info("rn1x shadow: writer lock held")
        while True:
            delay = TICK_S
            try:
                res = await cycle(conn)
                idle = res.get("state") in ("STOPPED", "BLOCKED",
                                            "IDLE_NO_CANDIDATES")
                delay = IDLE_S if idle else TICK_S
                await heartbeat(SERVICE, "idle" if idle else "ok", res)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                           # noqa: BLE001
                log.exception("rn1x shadow cycle failed")
                delay = IDLE_S
                try:
                    await heartbeat(SERVICE, "error",
                                    {"error": "%s: %s"
                                     % (type(exc).__name__, exc)})
                except Exception:                              # noqa: BLE001
                    pass
            await asyncio.sleep(delay)


async def main() -> None:
    """Worker-service entry point, kept for parity. NOT registered.

    `workers/all.py` deliberately does not list this loop: registering it
    there would require a push to the shared auto-deploy branch, which
    restarts the collector. If the worker service is ever given its own
    branch, this is the entry point -- and the advisory lock above means
    running it in both places would still leave exactly one writer.
    """
    if not enabled():
        log.info("rn1x shadow: RN1X_SHADOW is not on; not starting")
        return
    await run()
