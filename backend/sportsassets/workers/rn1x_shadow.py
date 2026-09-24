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

TWO LANES, AND I HAD ONLY BUILT ONE WHILE BLAMING THE FEED FOR IT.

I reported that no forward lane could be seeded from this feed, citing a
15.07 h detection lag on one seed that postdated settlement. That was a
wrong diagnosis of my own code. The candidate query required
`m.resolved`, so the experiment could only ever see settled markets --
HISTORICAL BY MY OWN FILTER, not by any property of the feed. The cursor
also defaults to 0, so it walks `trades` from the first row ever
recorded: at id ~300 of ~6.1M it is replaying March.

And the lag figure could not carry that weight. `trades.source`
separates `chain`/`poll` (live, `detected_at` stamped at insert) from
`backfill` (inserted months later) and `s1` (a synthetic emitter). On a
backfill row `detected_at - ts` is the age of the backfill. `lane_census`
now measures latency over LIVE lanes only, every cycle, into the
heartbeat.

  HISTORICAL   resolved markets; scored against the observed payout; its
               cursor walks from the start and is never reset here.
  PROSPECTIVE  UNRESOLVED markets, LIVE lanes only; cursor seeded at the
               feed head on a genuine first start. Decisions are recorded
               while the outcome is unknown, and no payout is read.

The two are keyed to different experiment ids and different cursors, so
their rows and their totals are never mixed.

NO CAPITAL, NO ORDERS, NO VENUE. Every order it writes is modelled and
the schema CHECKs `is_modelled`. There is no submit, cancel or funding
path in this module's import closure, and it never touches the desk's
tables or `acct_fc2d773a2afa4851`, which stays paused and
ACCOUNTING_UNCERTAIN as evidence.

Kill: the control row (prompt, no deploy) or RN1X_SHADOW=off (a deploy).
"""

from __future__ import annotations

import asyncio
import decimal
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

# ── TWO LANES, SEPARATELY KEYED AND SEPARATELY PERSISTED ────────────
#
# I PREVIOUSLY REPORTED THAT NO FORWARD LANE WAS POSSIBLE FROM THIS FEED.
# That was wrong, and the mistake was mine rather than the feed's. The
# candidate query below required `m.resolved`, so the experiment could only
# ever see settled markets -- historical BY MY OWN FILTER. On top of that
# the cursor defaults to 0, so it walks `trades` from the very first row:
# at id ~300 of ~6.1M it is replaying March, not consuming new activity.
# The 15.07 h "detection lag" I generalised into a feed blocker came from
# ONE seed, and `trades.source` shows why that figure cannot bear the
# weight I put on it: for a `backfill` row, `detected_at - ts` is when we
# BACKFILLED it, not when anything was detected.
#
# So there are two lanes and they never share a total:
#   HISTORICAL   resolved markets, any lane, cursor walks from the start.
#                Scored against the observed payout. Its cursor and its
#                rows are PRESERVED -- nothing here resets or discards it.
#   PROSPECTIVE  UNRESOLVED markets, LIVE lanes only, cursor seeded at the
#                feed head on first start so it consumes what arrives
#                after it begins. No payout exists yet, so it is not
#                scored; it records decisions at the time they were taken.
HISTORICAL_EXPERIMENT_ID = "RN1X_MGMT_PAIR091_STOP16_V1"
PROSPECTIVE_EXPERIMENT_ID = "RN1X_MGMT_PAIR091_STOP16_V1_PROSPECTIVE"
# Kept under the old name so existing rows, tests and readbacks that speak
# of "the experiment id" keep meaning the historical one.
EXPERIMENT_ID = HISTORICAL_EXPERIMENT_ID

HISTORICAL_CURSOR_KEY = "rn1x_source_cursor"      # unchanged, preserved
PROSPECTIVE_CURSOR_KEY = "rn1x_prospective_cursor"

# THE LIVE LANES, BY NAME. Migration 033 is explicit that the shadow
# instrument's evidence queries filter `source IN ('chain','poll')` so the
# S1 emitter's own rows can never be read as venue coverage. My candidate
# query had NO source filter at all, so 's1' rows were eligible seeds --
# a rule the project had written down and I had not honoured.
LIVE_SOURCES = ("chain", "poll")
SEEDABLE_SOURCES = ("chain", "poll", "backfill")   # never 's1'

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
    # WITHDRAWN AS A BLOCKER. It was never one. Kept under its own name
    # rather than deleted, because a claim I published and then retracted
    # should stay visible as a retraction rather than disappear.
    "FEED_POSTDATES_SETTLEMENT_WITHDRAWN": (
        "WITHDRAWN 2026-09-23. I reported that RN1 detection postdated "
        "settlement by 11.22 hours and concluded no forward lane was "
        "possible. The 15.07 h figure came from ONE seed and could not "
        "bear that weight: `trades.source` shows `backfill` rows, whose "
        "`detected_at - ts` is the age of the backfill and not a "
        "detection latency. The experiment was historical because my own "
        "candidate query required `m.resolved`, not because of the feed. "
        "A PROSPECTIVE lane on unresolved markets and live lanes only now "
        "exists, and `lane_census` measures live latency every cycle so "
        "this is answered from persisted state rather than one sample."),
}

# Blockers that apply ONLY to the prospective lane, because a decision
# taken before resolution genuinely cannot be scored yet.
PROSPECTIVE_NOTES = {
    "NOT_YET_SCORED": (
        "A prospective decision has no observed payout, because the "
        "market has not resolved. These rows record what was decided and "
        "when; the historical lane scores the same conditions once they "
        "settle. A prospective total is never added to a scored one."),
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


async def _cursor(conn, key: str = HISTORICAL_CURSOR_KEY,
                  *, seed_at_head: bool = False) -> int:
    """Resume exactly, or -- for the prospective lane only -- start at NOW.

    THE HISTORICAL CURSOR IS NEVER RESET. It defaults to 0 so the
    historical lane keeps walking `trades` from the beginning, which is
    what makes its evidence a replay of the record. Nothing in this module
    writes it backwards.

    THE PROSPECTIVE CURSOR IS DIFFERENT AND MUST BE. A prospective lane
    seeded at 0 would grind through five months of history while every row
    it published claimed to be new activity -- the same defect the desk
    loop documents at `_load_cursor`. On a genuine FIRST start it takes
    max(id), so it consumes only what arrives after it begins. A restart
    resumes from the stored value exactly, so a redeploy loses nothing.
    """
    raw = await conn.fetchval(
        "SELECT value::text FROM ingestion_state WHERE key = $1", key)
    try:
        return int(str(raw).strip().strip('"'))
    except (TypeError, ValueError):
        pass
    if not seed_at_head:
        return 0
    head = int(await conn.fetchval(
        "SELECT coalesce(max(id), 0) FROM trades") or 0)
    log.info("rn1x prospective FIRST START: seeding cursor at head %s; "
             "history before it is not replayed as new activity", head)
    return head


async def _save_cursor(conn, value: int,
                       key: str = HISTORICAL_CURSOR_KEY) -> None:
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        key, str(int(value)))


# ── seed candidates: real cohort fills on RESOLVED conditions ───────
#
# BUYS ONLY, because the runner refuses a SELL seed: a sale is not an
# entry and seeding a position from one would invent the inventory it
# sold. The join to `markets` is what makes the replay scorable -- an
# unresolved condition has no observed payout and step 8 would have
# nothing to read.
_CANDIDATES_HISTORICAL = """
    SELECT t.id, t.whale_id, t.condition_id
      FROM trades t
      JOIN markets m ON m.condition_id = t.condition_id
     WHERE t.id > $1
       AND t.condition_id IS NOT NULL
       AND t.outcome_index IN (0, 1)
       AND t.side = 'BUY'
       AND t.source = ANY($3::text[])
       AND m.resolved
       AND m.resolved_prices IS NOT NULL
     ORDER BY t.id
     LIMIT $2
"""

# THE PROSPECTIVE LANE. Live lanes only, and markets that have NOT
# resolved -- so a decision here is taken while the outcome is genuinely
# unknown. No payout is read because none exists; these rows are scored
# later, by the historical lane, once the market settles.
_CANDIDATES_PROSPECTIVE = """
    SELECT t.id, t.whale_id, t.condition_id
      FROM trades t
      JOIN markets m ON m.condition_id = t.condition_id
     WHERE t.id > $1
       AND t.condition_id IS NOT NULL
       AND t.outcome_index IN (0, 1)
       AND t.side = 'BUY'
       AND t.source = ANY($3::text[])
       AND NOT m.resolved
     ORDER BY t.id
     LIMIT $2
"""

# ── THE PROVENANCE CENSUS ───────────────────────────────────────────
#
# Rides the heartbeat every cycle, so "is this historical or prospective"
# is answered by persisted operational state rather than by reading the
# source of a query. It is deliberately ONE bounded statement on the
# `trades_detected_live_idx` window plus one cheap join on the PK.
_LANE_CENSUS = """
    SELECT count(*) FILTER (WHERE t.source = ANY($1::text[])) AS live_24h,
           count(*) FILTER (WHERE t.source = 'backfill') AS backfill_24h,
           count(*) FILTER (WHERE t.source = 's1') AS s1_24h,
           count(*) FILTER (WHERE t.source = ANY($1::text[])
                            AND t.side = 'BUY' AND NOT m.resolved)
               AS live_unresolved_buys_24h,
           round(max(extract(epoch FROM (t.detected_at - t.ts)))
                 FILTER (WHERE t.source = ANY($1::text[]))::numeric, 1)
               AS live_max_latency_s,
           round(avg(extract(epoch FROM (t.detected_at - t.ts)))
                 FILTER (WHERE t.source = ANY($1::text[]))::numeric, 1)
               AS live_avg_latency_s
      FROM trades t
      JOIN markets m ON m.condition_id = t.condition_id
     WHERE t.detected_at > now() - interval '24 hours'
"""

_SEEDED_BY_SOURCE = """
    SELECT t.source, count(*) AS seeded
      FROM rn1x_positions p
      JOIN trades t ON t.id = p.source_trade_id
     GROUP BY 1 ORDER BY 2 DESC
"""


async def lane_census(conn) -> dict:
    """What the feed actually delivered in the last 24 hours, by lane.

    THE FIGURE THAT MATTERS FOR THE BLOCKER I WRONGLY DECLARED is
    `live_unresolved_buys_24h`: live-lane BUY fills on markets that have
    not resolved. If it is non-zero then a prospective lane has evidence
    to work with, and "the feed postdates settlement" was a statement
    about backfill rows and one seed, not about the feed.

    `live_max_latency_s` is computed over LIVE lanes only, because
    `detected_at - ts` on a backfill row is the age of the backfill.
    """
    row = await conn.fetchrow(_LANE_CENSUS, list(LIVE_SOURCES))
    seeded = await conn.fetch(_SEEDED_BY_SOURCE)
    # `round(...)::numeric` comes back as Decimal, which json.dumps refuses.
    # A figure that cannot be read stays None rather than becoming 0.
    out = {k: (float(v) if isinstance(v, decimal.Decimal) else v)
           for k, v in (dict(row) if row else {}).items()}
    out["seeded_by_source"] = {r["source"]: int(r["seeded"]) for r in seeded}
    out["live_sources"] = list(LIVE_SOURCES)
    out["latency_note"] = (
        "live lanes only. For a backfill row detected_at - ts is the age "
        "of the backfill, not a detection latency")
    return out


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


async def _replay_one(conn, *, trade_id, whale_id, condition_id,
                      prospective: bool = False) -> dict:
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
    # A PROSPECTIVE RUN READS NO PAYOUT, FULL STOP. Not "there happens not
    # to be one" -- the lane refuses it. A market that resolves between
    # the candidate query and this read would otherwise hand a prospective
    # decision its own outcome, which is look-ahead arriving by a race.
    if (not prospective) and mkt is not None and mkt["resolved_prices"] is not None:
        import json as _json
        raw = mkt["resolved_prices"]
        prices = _json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(prices, list):
            payouts = {i: float(p) for i, p in enumerate(prices)}
        elif isinstance(prices, dict):
            payouts = {int(k): float(v) for k, v in prices.items()}
        resolved_at = mkt["resolved_at"]

    # THE CLOCK IS READ HERE, in the runtime caller, and only the
    # prospective lane may use it. The historical lane is a counterfactual
    # replay of a market that has already settled -- stamping it with
    # today's wall clock would be a worse lie than the one being fixed --
    # so it keeps the availability basis and is labelled a replay.
    #
    # This is the line that decides whether the word "prospective" on
    # these rows means anything. Before it, the lane inherited
    # decision_ts = detected_ts from the runner's default and every row
    # was backdated to the instant its evidence arrived.
    import time as _time

    out = runner.run(
        rows=[dict(r) for r in rows],
        payouts=payouts, resolved_at=resolved_at,
        source_whale_id=whale_id, condition_id=condition_id,
        initial_inventory_verified=inv["verified"],
        now=(_time.time() if prospective else None),
        decision_basis=(runner.BASIS_RUNTIME if prospective
                        else runner.BASIS_REPLAY))
    out["initial_inventory_evidence"] = inv
    out["resolved_at"] = resolved_at
    out["blockers"] = sorted(BLOCKERS)
    out["lane"] = "PROSPECTIVE" if prospective else "HISTORICAL"
    if prospective:
        out["prospective_notes"] = dict(PROSPECTIVE_NOTES)
        out["scored"] = False
    else:
        out["scored"] = payouts is not None
    return out


async def cycle(conn, *, lane: str = "HISTORICAL") -> dict:
    """One cycle on ONE lane. Never raises.

    `lane` selects the candidate query, the experiment id, the cursor and
    whether a payout may be read. The two lanes share this code path
    deliberately -- a separate prospective implementation would be a
    second experiment that could drift from the frozen policy -- but they
    share no identifier, no cursor and no total.
    """
    if lane not in ("HISTORICAL", "PROSPECTIVE"):
        raise ValueError("unknown lane %r" % (lane,))
    prospective = lane == "PROSPECTIVE"
    running, why = await _running(conn)
    if not running:
        return {"ran": False, "state": "STOPPED", "why": why}

    rdy = await store.ready(conn)
    if not rdy["ok"]:
        return {"ran": False, "state": "BLOCKED", "why": rdy["blocker"],
                "missing_tables": rdy["missing"]}

    experiment_id = (PROSPECTIVE_EXPERIMENT_ID if prospective
                     else HISTORICAL_EXPERIMENT_ID)
    cursor_key = (PROSPECTIVE_CURSOR_KEY if prospective
                  else HISTORICAL_CURSOR_KEY)
    query = (_CANDIDATES_PROSPECTIVE if prospective
             else _CANDIDATES_HISTORICAL)
    sources = list(LIVE_SOURCES if prospective else SEEDABLE_SOURCES)

    await store.declare_experiment(
        conn, experiment_id=experiment_id,
        code_version=runner.VERSION,
        seed_rule={
            "lane": lane,
            "source": ("cohort BUY fill in `trades` on an UNRESOLVED "
                       "market, live lanes only"
                       if prospective else
                       "cohort BUY fill in `trades` on a RESOLVED market"),
            "trades_source_filter": sources,
            "excludes": ("'s1' -- migration 033: the shadow instrument "
                         "must never read the emitter's own rows"),
            "venue": "POLYMARKET_GLOBAL_POLYGON",
            "event_class": "SINGLE_ACCOUNT_EXECUTIONS",
            "seed_price": "RN1's own fill price, ASSIGNED",
            "cursor_start": ("feed head on first start"
                             if prospective else "0 (walks the record)"),
            "not_evidence_of": ("that we could have obtained this fill"),
        },
        policy_register=pol.describe(),
        execution_basis=EXECUTION_BASIS,
        notes=(("PROSPECTIVE SHADOW. Decisions recorded before the market "
                "resolved; NOT scored, and never summed with a scored "
                "result." if prospective else
                "HISTORICAL REPLAY, scored against the observed payout.")
               + " Management-defined and experimental; not learned and "
                 "not proven profitable."))

    start = await _cursor(conn, cursor_key, seed_at_head=prospective)
    cands = await conn.fetch(query, start, SCAN_BATCH, sources)
    census = await lane_census(conn)
    if not cands:
        # PERSIST THE POSITION EVEN WHEN NOTHING WAS FOUND. Returning here
        # without saving is what made the prospective lane incapable of
        # ever producing a position: the seed lived only in this return
        # value, so the next cycle found no stored row, re-seeded at the
        # NEW head, and every row that had arrived in between was skipped
        # permanently. The only candidate it could ever catch was one
        # inserted between `max(id)` and the query microseconds later.
        #
        # Production stated it plainly and I read past it four times:
        # `rn1x_prospective_cursor` was ABSENT from `ingestion_state` while
        # the lane reported IDLE_NO_CANDIDATES every cycle, against 9,444
        # eligible live BUYs a day.
        #
        # This is safe for the historical lane and changes nothing there:
        # `start` is the value already stored (or 0 when absent, which is
        # what absence already means), so it never moves the cursor
        # backwards and never past unexamined evidence.
        await _save_cursor(conn, start, cursor_key)
        return {"ran": True, "state": "IDLE_NO_CANDIDATES", "lane": lane,
                "cursor": start, "cursor_persisted": True,
                "lane_census": census}

    seen_conditions: set[str] = set()
    results = []
    # EVERY CANDIDATE ACCOUNTED FOR. The old pair of numbers was "examined
    # 400, refusals 3", and both were true and neither was the whole story:
    # `examined` counted the SCAN, not the processing, and three branches
    # below skip a row with `continue` and no counter at all. A reader
    # comparing 400 to 3 could only conclude that 397 rows vanished.
    #
    # MAX_SEEDS_PER_CYCLE is 3, so three processed rows is the CAP being
    # reached, not a sample of the 400. These counters say so.
    flow = {"fetched": len(cands), "processed": 0,
            "duplicate_condition_in_batch": 0, "already_replayed": 0,
            "deferred_cap": 0, "refused": 0, "failed": 0, "written": 0,
            "not_reached_after_error": 0}
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
    for i, r in enumerate(cands):
        rid = int(r["id"])
        if len(results) >= MAX_SEEDS_PER_CYCLE:
            # Stop BEFORE consuming this row: it has not been examined.
            # Everything from here to the end of the batch is DEFERRED to
            # a later cycle, not refused and not lost -- the cursor stays
            # below it.
            flow["deferred_cap"] = len(cands) - i
            break
        if r["condition_id"] in seen_conditions:
            # One condition, one seed per cycle: a second fill on the same
            # market is the same candidate, not a new one.
            flow["duplicate_condition_in_batch"] += 1
            safe_id = rid
            continue
        seen_conditions.add(r["condition_id"])
        # Already replayed under this experiment and policy? The store's
        # key is derived, so this is a cheap check rather than a second
        # write that would be a no-op anyway.
        pid = store.position_id(experiment_id, pol.POLICY_ID, rid)
        if await conn.fetchval(
                "SELECT 1 FROM rn1x_positions WHERE position_id = $1", pid):
            # Idempotence, not a refusal: this trade already has its
            # position under this experiment and policy.
            flow["already_replayed"] += 1
            safe_id = rid
            continue
        try:
            out = await _replay_one(
                conn, trade_id=rid, whale_id=int(r["whale_id"]),
                condition_id=r["condition_id"], prospective=prospective)
            wrote = await store.persist_run(
                conn, out, experiment_id=experiment_id,
                source_account=str(r["whale_id"]))
        except Exception as exc:                               # noqa: BLE001
            log.exception("rn1x replay failed for trade %s", rid)
            results.append({"trade_id": rid, "written": False,
                            "error": "%s: %s" % (type(exc).__name__, exc)})
            # STOP HERE. Continuing would process later rows and then
            # save a cursor above this one, which is how the failed row
            # would be skipped.
            stopped_at_error = rid
            flow["failed"] += 1
            flow["processed"] += 1
            flow["not_reached_after_error"] = len(cands) - i - 1
            break
        results.append({"trade_id": rid,
                        "condition_id": r["condition_id"], **wrote})
        flow["processed"] += 1
        if wrote.get("written"):
            flow["written"] += 1
        else:
            flow["refused"] += 1
        safe_id = rid

    await _save_cursor(conn, safe_id, cursor_key)
    # THE REFUSAL DISTRIBUTION, not just the count of writes. "400
    # examined, 0 written" is the same line whether every candidate was
    # refused for a stated reason or the first one raised and the lane
    # never got past it -- and those need opposite responses. Tallied by
    # the reason each result actually carries.
    tally: dict = {}
    for x in results:
        if x.get("written"):
            key = "WRITTEN"
        elif x.get("error"):
            key = "ERROR:" + str(x["error"]).split(":")[0]
        else:
            key = str(x.get("refused_at") or "REFUSED_UNSPECIFIED")
            if x.get("unknown_reason"):
                key += "/" + str(x["unknown_reason"])
        tally[key] = tally.get(key, 0) + 1
    # THE IDENTITY, CHECKED. Every fetched row is in exactly one bucket.
    # If this ever fails the census is lying, and a boolean in the payload
    # is how a reader finds that out without re-deriving it by hand.
    flow["accounted"] = (
        flow["fetched"] == (flow["processed"]
                            + flow["duplicate_condition_in_batch"]
                            + flow["already_replayed"]
                            + flow["deferred_cap"]
                            + flow["not_reached_after_error"]))
    flow["cap_per_cycle"] = MAX_SEEDS_PER_CYCLE
    flow["scan_batch"] = SCAN_BATCH
    flow["reading"] = (
        "fetched = rows the scan returned; processed = rows that reached a "
        "replay, capped at cap_per_cycle; the rest are duplicates of a "
        "condition already seeded this batch, positions already written, "
        "or rows deferred to a later cycle. `examined` is kept as an alias "
        "of `fetched` for the older readers and is the number that was "
        "misread as 'processed'")
    return {"ran": True, "state": "REPLAYED", "lane": lane,
            "cursor": safe_id, "examined": len(cands), "flow": flow,
            "results": results,
            "stopped_at_error": stopped_at_error,
            "refusals": tally,
            "cursor_moved": safe_id != start,
            "lane_census": census,
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
                # BOTH LANES, EVERY CYCLE, in this order. Prospective runs
                # first: it consumes what has just arrived, and making it
                # wait behind a multi-month backfill would be the same
                # mistake as seeding its cursor at zero. Each lane keeps
                # its own result so the heartbeat shows two states, never
                # one blended one.
                res_p = await cycle(conn, lane="PROSPECTIVE")
                res_h = await cycle(conn, lane="HISTORICAL")
                # A COMPACT SUMMARY THAT SURVIVES TRUNCATION. The
                # operational readback prints left(detail, 1400) and the
                # historical lane's `results` array fills that on its own,
                # so the prospective lane -- the one whose state is in
                # question -- was invisible in every production read I
                # took. jsonb orders keys by length then bytewise, so a
                # 5-character key lands before "state" and well inside the
                # window. This adds no facts; it makes the ones already
                # there readable without widening the query.
                def _sum(r):
                    return {"state": r.get("state"),
                            # EVERY CANDIDATE ACCOUNTED FOR, in the summary
                            # the command centre reads. Without it the tile
                            # showed "examined 400, refusals 3" and a
                            # reader had to guess at the other 397.
                            "flow": r.get("flow") or {},
                            "cursor": r.get("cursor"),
                            "examined": r.get("examined", 0),
                            "written": r.get("written", 0),
                            "moved": r.get("cursor_moved"),
                            "stopped": r.get("stopped_at_error"),
                            "refusals": r.get("refusals") or {}}
                res = {"lanes": {"P": _sum(res_p), "H": _sum(res_h)},
                       "prospective": res_p, "historical": res_h,
                       "lane_census": (res_h.get("lane_census")
                                       or res_p.get("lane_census"))}
                states = (res_p.get("state"), res_h.get("state"))
                if "STOPPED" in states:
                    res["state"] = "STOPPED"
                elif "BLOCKED" in states:
                    res["state"] = "BLOCKED"
                elif "REPLAYED" in states:
                    res["state"] = "REPLAYED"
                else:
                    res["state"] = "IDLE_NO_CANDIDATES"
                idle = res["state"] in ("STOPPED", "BLOCKED",
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
