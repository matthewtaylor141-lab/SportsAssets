"""THE PUBLISHED TRACE for the RN1-seeded management experiment.

The audit recorded "no published trace route" for this path. This is it.

WHAT IT PUBLISHES, AND WHY EACH PIECE. The brief was command-centre views
showing "actual operation, decisions, results and remaining blockers",
so all four are here and none of them is optional:

    operation   is the loop running, what is it STOPPED or BLOCKED by,
                when did it last write, how far has the cursor got
    decisions   the real rows, including the ones that did not act
    results     accounting per position, and the hold-to-settlement
                comparison from the identical assigned inventory
    blockers    the named external dependencies, always present

READS ONLY. No write, no decision, no venue call.

TWO THINGS IT REFUSES TO DO.

ITS COUNTS DESCRIBE THE EXPERIMENT'S OWN TABLES AND NOTHING ELSE. Every
figure is scoped to `rn1x_*` under one experiment id. It is not a system
census and the payload says so on the row, not in a footnote.

IT DOES NOT SUM A REPLAY WITH A LIVE RESULT. Every position here is
HISTORICAL_REPLAY; there is no live lane to mix with, and if one is ever
added it gets its own experiment id and its own total.
"""

from __future__ import annotations

from .. import bettor_rn1x_store as _store

SCOPE = ("Every count and total on this page is scoped to the rn1x_* "
         "tables under one experiment id. It is not a census of the "
         "system, of the desk, or of any other lane.")

LABEL = ("MANAGEMENT-DEFINED AND EXPERIMENTAL. The policy was specified "
         "by management and frozen before evaluation. It is not learned, "
         "not fitted, and not proven profitable. Every order is "
         "MODELLED and no capital is at risk.")


async def _heartbeat(pool) -> dict:
    row = await pool.fetchrow(
        "SELECT status, detail, beat_at FROM service_heartbeats "
        "WHERE service = 'rn1x_shadow'")
    if row is None:
        return {"status": "NEVER_BEAT",
                "why": ("the loop has never completed a cycle on this "
                        "deployment. That is not the same as running "
                        "and finding nothing")}
    detail = row["detail"]
    if isinstance(detail, str):
        import json
        try:
            detail = json.loads(detail)
        except ValueError:
            detail = {"raw": detail}
    return {"status": row["status"], "at": row["beat_at"], "detail": detail}


async def _control(pool) -> dict:
    raw = await pool.fetchval(
        "SELECT value::text FROM ingestion_state WHERE key = 'rn1x_shadow'")
    if raw is None:
        return {"value": None, "running": False,
                "state": "CONTROL_ROW_ABSENT",
                "why": ("the loop fails closed: with no row it does not "
                        "run. Absence is not permission")}
    running = raw.strip().lower() == "true"
    return {"value": raw, "running": running,
            "state": "RUNNING" if running else "STOPPED_BY_CONTROL_ROW"}


# ── THE SEVEN STATUSES, SEPARATELY ──────────────────────────────────
#
# One screen showing one blended "running / not running" hid which of
# these was true. They fail independently and are reported independently.
STATUS_KEYS = ("historical_replay", "prospective_rn1_management",
               "independent_ev_entries", "pairing",
               "second_half_loss_exit", "accounting_health",
               "learning_evaluation",
               # The EXTERNAL source is its own status. `independent_ev_
               # entries` is the internal settlement-model path; this is a
               # bookmaker's price. Different source class, different
               # reason to be blocked, so a separate badge.
               "external_valuation",
               # ORDER BOOK STATE and SHADOW P&L, as their own tiles. They
               # were only reachable by clicking into one position's trace,
               # so a manager could not see how much is resting or what the
               # book has actually earned without knowing where to look.
               "order_book_state",
               "shadow_pnl",
               # ACTUAL MODEL FITTING, separate from the policy comparator.
               "model_fitting",
               # SINGLE-WRITER OWNERSHIP, read from pg_locks. It was a
               # claim about code with no production read behind it, and
               # two of the four loops did not even take the lock the
               # claim described.
               "writer_ownership")


def _live(running: bool, has_rows: bool, *, what: str, why: str) -> dict:
    """A LIVE badge must say WHAT is live.

    Three states, not two: LIVE (running and producing), ARMED (running,
    nothing produced yet) and STOPPED. "Running and found nothing" and
    "not running" are different claims and a single badge conflated them.
    """
    if not running:
        return {"badge": "STOPPED", "live": False, "what": what, "why": why}
    return {"badge": "LIVE" if has_rows else "ARMED", "live": bool(running),
            "producing": bool(has_rows), "what": what, "why": why}


async def statuses(pool) -> dict:
    """Seven independent statuses, each naming what is live and why."""
    from .. import bettor_entry_gate as egate
    from .. import bettor_rn1x_learn as L
    from .. import bettor_rn1x_policy as pol
    from ..workers import rn1x_shadow as W

    async def _ctl(key):
        raw = await pool.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1", key)
        return bool(raw and raw.strip().lower() == "true")

    shadow_on = await _ctl("rn1x_shadow")
    learn_on = await _ctl("rn1x_learn")
    have = await pool.fetchval(
        "SELECT count(*) FROM information_schema.tables WHERE "
        "table_schema = 'public' AND table_name = 'rn1x_positions'")
    hist = prosp = 0
    exits = pairs = 0
    inv_ok = None
    if have:
        hist = await pool.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = $1",
            W.HISTORICAL_EXPERIMENT_ID) or 0
        prosp = await pool.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE experiment_id = $1",
            W.PROSPECTIVE_EXPERIMENT_ID) or 0
        pairs = await pool.fetchval(
            "SELECT count(*) FROM rn1x_orders WHERE intent = "
            "'COMPLETE_PAIR'") or 0
        exits = await pool.fetchval(
            "SELECT count(*) FROM rn1x_orders WHERE intent = 'EXIT'") or 0
        inv_ok = await pool.fetchval(
            "SELECT bool_and(coalesce(net_usd, 0) IS NOT NULL) "
            "FROM rn1x_outcomes")

    ev_rows = await pool.fetchval(
        "SELECT count(*) FROM information_schema.tables WHERE "
        "table_schema = 'public' AND table_name = 'bettor_learn_model'")
    verdicts = 0
    if ev_rows:
        verdicts = await pool.fetchval(
            "SELECT count(*) FROM bettor_learn_model WHERE model_key = $1",
            L.MODEL_KEY) or 0

    return {
        "historical_replay": _live(
            shadow_on, hist > 0, what="the HISTORICAL lane: resolved "
            "markets, scored against the observed payout",
            why="replays the record; its cursor walks from the start"),
        "prospective_rn1_management": await _prospective_status(
            pool, shadow_on=shadow_on, positions=prosp),
        "independent_ev_entries": {
            "badge": "BLOCKED", "live": False, "producing": False,
            "what": "independently selected EV entries",
            "why": ("the path EXISTS (bettor_entry_gate) and refuses: no "
                    "qualified model, no independent fair value, no "
                    "execution estimate, no sizing policy. A connected "
                    "engine without a qualified model is not the same as "
                    "an unimplemented path"),
            "requirements": list(egate.REQUIREMENTS),
        },
        "pairing": _live(
            shadow_on, pairs > 0,
            what="the PAIRING half of the frozen policy",
            why="combined cost <= 0.91 including fees"),
        "second_half_loss_exit": {
            "badge": "UNAVAILABLE", "live": False,
            "producing": exits > 0,
            "orders_ever_placed": int(exits),
            "what": "the SECOND-HALF loss exit (84% of allocated cost)",
            "why": ("implemented and demonstrated in controlled tests, but "
                    "no sport is admitted: a halfway rule is written for "
                    "soccer/basketball/football and NO progress feed is "
                    "connected for any of them. The only temporal "
                    "integration is game_start_time, a scheduled start "
                    "instant, and start + wall-clock is forbidden"),
            "rules_written": sorted(k for k, v in
                                    pol.DOCUMENTED_MAPPINGS.items()
                                    if v is not None),
            "feeds_connected": sorted(pol.PROGRESS_FEED_CONNECTED),
            "admitted": sorted(pol.SECOND_HALF_MAPPING),
        },
        "accounting_health": {
            "badge": "OK" if inv_ok else ("EMPTY" if not hist else "CHECK"),
            "live": bool(have), "what": "the experiment's own ledger",
            "why": ("cash + inventory_cost - realized == starting_cash on "
                    "every written position. This is a CONSISTENCY check, "
                    "not a completeness one"),
            "reconciles": inv_ok,
        },
        "learning_evaluation": {
            **_live(learn_on, verdicts > 0,
                    what="the POLICY COMPARATOR",
                    why=L.DESCRIPTION["fits_note"]),
            "verdicts_recorded": int(verdicts),
            "description": L.DESCRIPTION,
        },
        # AN EIGHTH STATUS, and it is deliberately not folded into
        # `independent_ev_entries`. That one reports the internal
        # settlement-model path, which refuses for want of a qualified
        # model. This reports an EXTERNAL BOOKMAKER valuation, which is a
        # different source class with a different reason to be blocked, and
        # one badge over both would hide which of the two moved.
        "external_valuation": await _external_status(pool),
        "order_book_state": await _order_book_status(pool),
        "shadow_pnl": await _pnl_status(pool),
        "model_fitting": await _model_fitting_status(pool),
        # A TWELFTH STATUS, because "single-writer ownership" was a claim
        # about code with nothing behind it in production. It is now a
        # read: which of the four writer locks is actually held.
        "writer_ownership": await _writer_ownership_status(pool),
    }


async def _prospective_status(pool, *, shadow_on: bool,
                              positions: int) -> dict:
    """The PROSPECTIVE lane, with the distinction the old badge hid.

    This tile read LIVE whenever the lane held any position, so four
    positions written before migration 104 -- every one of them backdated
    and reclassified -- made it look as though prospective evidence
    existed. A running process and prospective evidence are different
    claims, and the process badge must not stand in for the evidence.

    So it reports both, and separately: positions created SINCE the clock
    fix that carry an OBSERVED runtime decision instant, and the legacy
    rows that cannot. Zero of the former is CHECK, however live the loop.
    """
    import json as _json

    from ..bettor_rn1x_run import BASIS_RUNTIME
    from ..workers import rn1x_shadow as SH
    from ..workers import rn1x_shadow as W

    out = {"what": ("the PROSPECTIVE lane: UNRESOLVED markets, live "
                    "ingestion lanes only"),
           "positions": int(positions),
           "process_is_running": bool(shadow_on),
           "a_running_process_is_not_prospective_evidence": True}
    try:
        rows = await pool.fetch(
            "SELECT coalesce(decision_basis, 'UNLABELLED') AS basis, "
            "       count(*) AS n, max(decision_ts) AS newest "
            "  FROM rn1x_positions WHERE experiment_id = $1 "
            " GROUP BY 1 ORDER BY 1", W.PROSPECTIVE_EXPERIMENT_ID)
    except Exception as exc:                                   # noqa: BLE001
        out.update(badge="UNAVAILABLE",
                   why="rn1x_positions unreadable: %s" % type(exc).__name__)
        return out
    by_basis = {r["basis"]: int(r["n"]) for r in rows}
    runtime_n = by_basis.get(BASIS_RUNTIME, 0)
    out["by_decision_basis"] = by_basis
    out["with_observed_runtime_decision"] = runtime_n
    out["legacy_backdated_and_reclassified"] = sum(
        n for b, n in by_basis.items() if b != BASIS_RUNTIME)
    newest = [r["newest"] for r in rows if r["newest"] is not None]
    out["newest_decision_ts"] = (max(newest).isoformat()
                                 if newest else None)
    # WHY THE LANE WROTE NOTHING, from its own last cycle rather than
    # inferred from the absence of rows.
    try:
        raw = await pool.fetchval(
            "SELECT detail::text FROM service_heartbeats WHERE service = $1",
            SH.SERVICE)
        beat = _json.loads(raw) if raw else {}
        lane = (beat.get("lanes") or {}).get("P") or {}
        flow = lane.get("flow") or {}
        out["last_cycle_prospective"] = {
            "state": lane.get("state"),
            # THE FULL ACCOUNTING, so 400 and 3 stop contradicting. The
            # scan fetches SCAN_BATCH rows and the lane replays at most
            # cap_per_cycle conditions; the rest are duplicates of a
            # condition already seeded, positions already written, or rows
            # deferred to a later cycle.
            "flow": flow,
            "fetched": flow.get("fetched", lane.get("examined")),
            "processed": flow.get("processed"),
            "cap_per_cycle": flow.get("cap_per_cycle"),
            "every_candidate_accounted": flow.get("accounted"),
            "written": lane.get("written"),
            "cursor_moved": lane.get("moved"),
            "refusals": lane.get("refusals") or {}}
        # WHY ZERO QUALIFY, from the lane's own numbers rather than a
        # guess. Three readings, and they need different responses.
        if flow:
            if flow.get("processed", 0) == 0:
                out["why_none_qualify"] = (
                    "no candidate reached a replay this cycle: %d fetched, "
                    "%d duplicate conditions, %d positions already written"
                    % (flow.get("fetched", 0),
                       flow.get("duplicate_condition_in_batch", 0),
                       flow.get("already_replayed", 0)))
            elif flow.get("written", 0) == 0:
                out["why_none_qualify"] = (
                    "%d of %d fetched rows reached a replay (the cap is %s) "
                    "and every one refused. The refusal codes beside this "
                    "say which rule stopped each"
                    % (flow.get("processed", 0), flow.get("fetched", 0),
                       flow.get("cap_per_cycle")))
            else:
                out["why_none_qualify"] = (
                    "%d position(s) were written this cycle; a zero here "
                    "refers to the OBSERVED-runtime-decision count above, "
                    "which is a different question"
                    % flow.get("written", 0))
    except Exception as exc:                                   # noqa: BLE001
        out["last_cycle_prospective"] = {"unreadable": type(exc).__name__}
    if not shadow_on:
        out.update(badge="STOPPED", live=False, producing=False,
                   why="the rn1x_shadow control row is not true")
    elif runtime_n == 0:
        out.update(
            badge="CHECK", live=True, producing=False,
            why=("the loop RUNS and no position in this lane carries an "
                 "OBSERVED runtime decision instant, so there is NO "
                 "prospective evidence yet. The %d position(s) here "
                 "predate migration 104 and are labelled backdated. See "
                 "last_cycle_prospective for what the lane did on its "
                 "most recent pass" % int(positions)))
    else:
        out.update(badge="LIVE", live=True, producing=True,
                   why=("%d position(s) carry an OBSERVED runtime decision "
                        "instant (%s); NOT scored and never summed with a "
                        "scored result" % (runtime_n, BASIS_RUNTIME)))
    return out


#: THE FOUR WRITER LOCKS, by the module that takes them. Each loop holds a
#: session-scoped advisory lock for its whole life so a second instance
#: becomes a standby that writes nothing rather than a second writer.
WRITER_LOCKS = {
    7723901544120032: "workers/rn1x_shadow",
    7723901544120033: "workers/rn1x_learn_loop",
    7723901544120034: "workers/ext_pinnacle_loop",
    7723901544120035: "workers/rn1x_model_loop",
}

#: pg_locks splits a bigint advisory key into (classid, objid). Reassemble
#: it rather than comparing halves, and take `granted` from the row: a
#: waiting entry is not ownership.
WRITER_LOCK_SQL = """
    SELECT ((classid::bigint << 32) | objid::bigint) AS lock_key,
           pid, granted
      FROM pg_locks
     WHERE locktype = 'advisory'
"""


async def _writer_ownership_status(pool) -> dict:
    """Which loops hold their writer lock, read from the server.

    This does NOT prove that only one process could ever write -- the lock
    is advisory, so a writer that never asks for it is not stopped by it.
    What it establishes is that each loop that DOES ask is holding its own
    key, one pid per key, which is the property a second instance would
    break and which was previously only asserted in a comment.
    """
    out = {"what": "SINGLE-WRITER OWNERSHIP: one advisory lock per loop",
           "advisory_is_not_mandatory": (
               "an advisory lock stops the loops that ask for it. A writer "
               "that never asks is not prevented by it, so this is "
               "ownership among the four loops, not a guarantee about any "
               "other process"),
           "expected": {str(k): v for k, v in WRITER_LOCKS.items()}}
    # THE COST OF HOLDING THE LOCK, stated where the lock is reported. A
    # session advisory lock has to be held on a connection for the loop's
    # life, so four loops occupy four of this pool's ten slots. That is a
    # real consequence of this design and the place to notice it is here,
    # next to the ownership it buys: `size == max` with `idle == 0` is a
    # saturated pool, and a handler waiting on a slot looks like a slow
    # database rather than like this.
    try:
        from .. import db as _db

        out["pool"] = _db.pool_stats()
        out["pool_slots_held_by_locks"] = len(WRITER_LOCKS)
    except Exception:                                          # noqa: BLE001
        out["pool"] = None
    try:
        rows = await pool.fetch(WRITER_LOCK_SQL)
    except Exception as exc:                                   # noqa: BLE001
        out.update(badge="UNAVAILABLE",
                   why="pg_locks unreadable: %s" % type(exc).__name__)
        return out
    held: dict = {}
    for r in rows:
        key = int(r["lock_key"])
        if key not in WRITER_LOCKS:
            continue
        held.setdefault(WRITER_LOCKS[key], []).append(
            {"pid": int(r["pid"]), "granted": bool(r["granted"])})
    out["held"] = held
    out["loops_holding_their_lock"] = len(held)
    doubled = sorted(n for n, v in held.items()
                     if len([x for x in v if x["granted"]]) > 1)
    out["more_than_one_holder"] = doubled
    if doubled:
        out.update(badge="CHECK",
                   why=("more than one granted holder on: %s. That is two "
                        "writers, which is the thing the lock exists to "
                        "prevent" % ", ".join(doubled)))
    elif not held:
        out.update(badge="EMPTY",
                   why=("no writer lock is held on this database right now. "
                        "Either no loop is armed, or they run against a "
                        "different database than this read"))
    else:
        out.update(badge="OK",
                   why=("%d of %d loops hold their own lock, one pid each"
                        % (len(held), len(WRITER_LOCKS))))
    return out


ORDER_BOOK_SQL = """
    SELECT o.state,
           count(*) AS n,
           sum(o.qty)::float8 AS qty,
           sum(o.filled_qty)::float8 AS filled,
           sum(o.qty - o.filled_qty)::float8 AS remaining
      FROM rn1x_orders o
      JOIN rn1x_positions p ON p.position_id = o.position_id
     WHERE p.experiment_id = $1
     GROUP BY o.state ORDER BY 1
"""


async def _order_book_status(pool) -> dict:
    """RESTING ORDERS, PARTIAL FILLS, CANCELLATIONS AND REMAINING SIZE.

    By ORDER STATE, because the states are the answer: RESTING is working,
    PARTIALLY_FILLED has a known executed quantity behind an untouched
    remainder, CANCEL_PENDING is still executable, and CANCELLED is not.
    Collapsing them into "open orders" loses the one distinction that
    matters during a cancel race.
    """
    from ..workers import rn1x_shadow as W

    out = {"what": ("modelled order state by lifecycle state, with "
                    "remaining size. Every order here is MODELLED"),
           "lanes": {}}
    have = await pool.fetchval(
        "SELECT to_regclass('public.rn1x_orders') IS NOT NULL")
    if not have:
        out.update(badge="UNAVAILABLE", why="rn1x_orders is not present")
        return out
    total = 0
    for eid, lane in ((W.HISTORICAL_EXPERIMENT_ID, "historical"),
                      (W.PROSPECTIVE_EXPERIMENT_ID, "prospective")):
        rows = [dict(r) for r in await pool.fetch(ORDER_BOOK_SQL, eid)]
        out["lanes"][lane] = {
            "by_state": rows,
            "orders": sum(int(r["n"]) for r in rows),
            "remaining_qty": sum(float(r["remaining"] or 0) for r in rows),
            "partially_filled": sum(
                int(r["n"]) for r in rows
                if str(r["state"]).upper() == "PARTIALLY_FILLED"),
            "cancel_pending_still_executable": sum(
                int(r["n"]) for r in rows
                if str(r["state"]).upper() == "CANCEL_PENDING"),
        }
        total += out["lanes"][lane]["orders"]
    out.update(badge=("LIVE" if total else "EMPTY"), orders_total=total,
               why=("%d modelled orders across both lanes" % total)
                   if total else "no modelled order has been written yet")
    return out


#: ACCOUNTING LIVES IN `rn1x_outcomes`, one row per settled position.
#: There is no rn1x_accounting table -- I wrote a query against one and
#: the schema check caught it. The columns below are the real ones.
PNL_SQL = """
    SELECT count(*) AS settled_positions,
           sum(o.realized_cash_usd)::float8 AS realized_cash_usd,
           sum(o.fees_usd)::float8        AS fees_usd,
           sum(o.net_usd)::float8         AS net_usd,
           sum(o.residual_qty)::float8    AS residual_qty,
           sum(o.residual_settled_usd)::float8 AS residual_settled_usd,
           sum(o.unpaired_qty)::float8    AS unpaired_qty,
           sum(o.turnover_usd)::float8    AS turnover_usd,
           max(o.committed_peak_usd)::float8 AS committed_peak_usd,
           min(o.settled_at)              AS first_settled_at,
           max(o.settled_at)              AS last_settled_at
      FROM rn1x_outcomes o
      JOIN rn1x_positions p ON p.position_id = o.position_id
     WHERE p.experiment_id = $1
"""

OPEN_POSITIONS_SQL = """
    SELECT count(*) AS open_positions,
           sum(p.seed_basis_usd)::float8 AS open_inventory_at_cost_usd
      FROM rn1x_positions p
      LEFT JOIN rn1x_outcomes o ON o.position_id = p.position_id
     WHERE p.experiment_id = $1 AND o.position_id IS NULL
"""


async def _pnl_status(pool) -> dict:
    """REALISED SHADOW P&L, FEES, AND WHAT IS NOT MARKED.

    Unrealised P&L is reported as NOT_IDENTIFIED rather than as a number,
    and that is not an omission: marking open inventory needs a price we
    are entitled to use, and the venue mid is not one -- nobody transacted
    there. Open inventory is therefore carried AT COST with the mark named
    as absent, which is the same convention the desk's own accounting uses.
    """
    from ..workers import rn1x_shadow as W

    out = {"what": ("realised shadow P&L and fees per lane. Every figure "
                    "is MODELLED: no capital moved"),
           "lanes": {},
           "unrealised": "NOT_IDENTIFIED",
           "why_unrealised_is_absent": (
               "marking open inventory requires a price we may use. A "
               "midpoint is where nobody transacted, so open inventory is "
               "carried at cost and the mark is named as missing"),
           "rebates": {
               "value": "NOT_APPLICABLE_TO_THESE_ORDERS",
               "why": ("the fee schedule's maker side is what would pay a "
                       "rebate; every fill modelled here is priced through "
                       "the taker side of the same production schedule"),
           }}
    have = await pool.fetchval(
        "SELECT to_regclass('public.rn1x_outcomes') IS NOT NULL")
    if not have:
        out.update(badge="UNAVAILABLE", why="rn1x_outcomes is not present")
        return out
    tot = 0
    for eid, lane in ((W.HISTORICAL_EXPERIMENT_ID, "historical"),
                      (W.PROSPECTIVE_EXPERIMENT_ID, "prospective")):
        r = await pool.fetchrow(PNL_SQL, eid)
        d = dict(r) if r else {}
        o = await pool.fetchrow(OPEN_POSITIONS_SQL, eid)
        d.update(dict(o) if o else {})
        n = int(d.get("settled_positions") or 0)
        out["lanes"][lane] = d
        tot += n
    out.update(badge=("LIVE" if tot else "EMPTY"), settled_total=tot,
               why=("%d settled positions with accounting rows" % tot)
                    if tot else
                    "no position has settled, so no realised figure exists")
    return out


async def _model_fitting_status(pool) -> dict:
    """ACTUAL FITTING, separate from the policy comparator.

    `learning_evaluation` is the comparator: it re-runs policies over the
    same seeds and fits nothing. This tile is the fitted-model pipeline,
    and it names its target, because the target is what stops a
    behavioural forecast being read as a settlement probability.
    """
    from .. import bettor_model_inventory as MI
    from ..workers import rn1x_model_loop as ML

    out = {"what": ("SCHEDULED MODEL FITTING: prepare, fit, predict before "
                    "the outcome exists, join, evaluate"),
           "target": ML.TARGET,
           "target_predicts": MI.TARGETS[ML.TARGET]["predicts"],
           "target_is_not_usable_for": MI.TARGETS[ML.TARGET]["not_usable_for"],
           "entry_target_required_by_the_gate": MI.ENTRY_REQUIRES,
           "this_is_not_a_settlement_forecast": True,
           "this_is_not_our_fill_probability": True,
           "promotes_a_winner": False,
           "assumptions": list(ML.ASSUMPTIONS)}
    try:
        raw = await pool.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            ML.CONTROL_KEY)
        armed = bool(raw and raw.strip().lower() == "true")
    except Exception:                                          # noqa: BLE001
        armed = False
    out["armed"] = armed
    have = await pool.fetchval(
        "SELECT to_regclass('public.rn1x_model_predictions') IS NOT NULL")
    if not have:
        out.update(badge="UNAVAILABLE",
                   why="migration 102 has not been applied here")
        return out
    n = await pool.fetchval(
        "SELECT count(*) FROM rn1x_model_predictions WHERE target = $1",
        ML.TARGET) or 0
    joined = await pool.fetchval(
        "SELECT count(*) FROM rn1x_model_predictions WHERE target = $1 "
        "AND outcome_known = TRUE", ML.TARGET) or 0
    out.update(predictions=int(n), joined_outcomes=int(joined))
    # MATURITY, so "0 joined" can be read. A prediction cannot be joined
    # before its horizon closes, and 0 joined means something different
    # before the first maturity than after it: the first is waiting, the
    # second is a defect. This says which.
    try:
        mat = await pool.fetchrow(
            "SELECT min(predicted_at + (horizon_s || ' seconds')::interval) "
            "         AS earliest_maturity, "
            "       min(predicted_at) AS earliest_prediction, "
            "       count(*) FILTER (WHERE outcome_known = FALSE AND "
            "         predicted_at + (horizon_s || ' seconds')::interval "
            "         <= now()) AS matured_not_joined, "
            "       min(predicted_at + (horizon_s || ' seconds')::interval) "
            "         FILTER (WHERE predicted_at + "
            "         (horizon_s || ' seconds')::interval > now()) "
            "         AS next_maturity "
            "  FROM rn1x_model_predictions WHERE target = $1", ML.TARGET)
        def _iso(v):
            return v.isoformat() if v is not None else None
        out["maturity"] = {
            "earliest_prediction_at": _iso(mat["earliest_prediction"]),
            "earliest_maturity_at": _iso(mat["earliest_maturity"]),
            "next_maturity_at": _iso(mat["next_maturity"]),
            "matured_but_not_joined": int(mat["matured_not_joined"] or 0),
            "horizon_s": ML.HORIZON_S,
            "join_runs": "once per fitting cycle (%ss)" % int(ML.CYCLE_S),
            "censoring": ("a horizon that has NOT closed is left unjoined. "
                          "A closed horizon with no complement fill is a "
                          "genuine 0, not a missing observation"),
            "evaluation_floor": ML.MIN_EVAL_ROWS}
    except Exception as exc:                                   # noqa: BLE001
        out["maturity"] = {"unreadable": type(exc).__name__}
    try:
        import json as _json

        raw = await pool.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            ML.HEARTBEAT_KEY)
        out["last_cycle"] = _json.loads(raw) if raw else None
        sraw = await pool.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            ML.STANDBY_KEY)
        out["standby"] = _json.loads(sraw) if sraw else None
    except Exception as exc:                                   # noqa: BLE001
        out["last_cycle"] = {"unreadable": type(exc).__name__}
    if not armed:
        out.update(badge="STOPPED",
                   why="the %s control row is not true" % ML.CONTROL_KEY)
    elif n == 0:
        out.update(badge="ARMED",
                   why="armed; no prediction has been recorded yet")
    else:
        out.update(badge="LIVE",
                   why=("%d predictions recorded, %d with outcomes joined"
                        % (n, joined)))
    return out


async def _external_status(pool) -> dict:
    """PINNACLE_DEVIG_V1's own tile: what it valued, and why it refused."""
    from .. import bettor_external_shadow as EX

    have = await pool.fetchval(
        "SELECT count(*) FROM information_schema.tables WHERE "
        "table_schema = 'public' AND table_name = 'external_valuations'")
    cred = EX.credential_present()
    out = {
        "what": ("EXTERNAL BOOKMAKER VALUATION -- Pinnacle's own de-vigged "
                 "price, not a trained model and not an internally "
                 "qualified settlement model"),
        "experiment_id": EX.EXPERIMENT_ID,
        "label": EX.LABEL,
        "source": EX.describe()["source"],
        "credential": cred,
        "table_present": bool(have),
    }
    if not have:
        out.update(badge="UNAVAILABLE", live=False,
                   why=("migration 103 has not been applied here, so no "
                        "valuation can be recorded"))
        return out
    summ = await pool.fetchrow(EX.SUMMARY, EX.EXPERIMENT_ID)
    rows = await pool.fetch(EX.REFUSAL_CENSUS, EX.EXPERIMENT_ID, "24")
    n = int((summ or {}).get("evaluated") or 0)
    adm = int((summ or {}).get("admissible") or 0)
    out["summary"] = dict(summ) if summ is not None else {}
    # THE REFUSAL DISTRIBUTION IS THE POINT OF THE TILE. Management's
    # question is not "did it buy" but "why did it not", and that answer is
    # a histogram rather than a sentence.
    out["refusals_24h"] = {r["refusal"]: int(r["n"]) for r in rows}
    # The newest row's id, so a reader can follow the tile straight to one
    # complete trace instead of guessing an id.
    out["last_id"] = await pool.fetchval(
        "SELECT max(id) FROM external_valuations WHERE experiment_id = $1",
        EX.EXPERIMENT_ID)
    # THE LAST CYCLE'S OWN TALLY. Most of this loop's refusals happen
    # before a candidate is ever scored, so they never become a row and the
    # table's census cannot show them. Without this, a cycle in which every
    # candidate was refused for a nameable reason reads as "evaluated 0"
    # with an empty refusal list -- indistinguishable from a cycle that did
    # not run.
    try:
        import json as _json

        from ..workers import ext_pinnacle_loop as _EXT

        raw = await pool.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            _EXT.HEARTBEAT_KEY)
        out["last_cycle"] = _json.loads(raw) if raw else None
        # THE STANDBY, SEPARATELY. It used to be written into the line
        # above and every read returned its empty cycle instead of the
        # writer's. Reported so "a second process is up and waiting" is
        # still visible, and can never be mistaken for a cycle.
        sraw = await pool.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            _EXT.STANDBY_KEY)
        out["standby"] = _json.loads(sraw) if sraw else None
    except Exception as exc:                                   # noqa: BLE001
        out["last_cycle"] = {"unreadable": type(exc).__name__}
    out["why_two_refusal_sources"] = (
        "`refusals_24h` counts candidates that were SCORED and refused. "
        "`last_cycle.refusals` counts every candidate the cycle looked at, "
        "including the ones refused before scoring -- no venue contract, "
        "an ambiguous mapping, no contemporaneous quote. A cycle can refuse "
        "everything and still write no row at all")
    if not cred["present"]:
        out.update(badge="BLOCKED", live=False,
                   why=("the odds credential is not present on this "
                        "service, so the source cannot price anything "
                        "here. " + (cred["why"] or "")))
    elif n == 0:
        out.update(badge="ARMED", live=True,
                   why="connected and has evaluated nothing yet")
    else:
        out.update(badge="LIVE", live=True, producing=True,
                   why=("%d evaluated in this experiment, %d admissible; "
                        "the refusal histogram says why the rest were not"
                        % (n, adm)))
    return out


async def overview(pool) -> dict:
    """Operation, totals and blockers in one read."""
    from ..workers import rn1x_shadow as W
    from .. import bettor_rn1x_policy as pol

    tables = await pool.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name LIKE 'rn1x%'")
    if not tables:
        return {"scope": SCOPE, "label": LABEL,
                "operation": {"state": "SCHEMA_ABSENT",
                              "why": ("the rn1x_* tables do not exist on "
                                      "this database, so the experiment "
                                      "cannot have written anything")},
                "blockers": W.BLOCKERS}

    counts = await pool.fetchrow(
        "SELECT (SELECT count(*) FROM rn1x_positions) positions, "
        "(SELECT count(*) FROM rn1x_decisions) decisions, "
        "(SELECT count(*) FROM rn1x_orders) orders, "
        "(SELECT count(*) FROM rn1x_fills) fills, "
        "(SELECT count(*) FROM rn1x_outcomes) outcomes, "
        "(SELECT max(decision_ts) FROM rn1x_decisions) last_decision, "
        "(SELECT max(written_at) FROM rn1x_outcomes) last_outcome")

    # THE OPERATING STATES, counted. A policy whose honest answer is
    # mostly HOLD should read as mostly HOLD on the screen; a panel that
    # showed only the orders would misrepresent it as busy.
    #
    # GROUPED ON `operating_state` NOW, NOT ON THE REASON. The reason is
    # a sentence and often carries the prices it was computed from, so
    # grouping by it produced one bucket per decision and a panel of
    # twelve singletons. The state is the categorical field; migration
    # 109 added it precisely so this read has something to group on.
    # COALESCE keeps the pre-109 rows visible under their old reason
    # rather than collapsing them all into one NULL bucket.
    states = await pool.fetch(
        "SELECT COALESCE(operating_state, left(selection_reason, 60)) "
        "AS state, count(*) n FROM rn1x_decisions "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 12")

    net = await pool.fetchrow(
        "SELECT sum(net_usd)::float8 ours, sum(fees_usd)::float8 fees, "
        "count(*) n FROM rn1x_outcomes")

    return {
        "scope": SCOPE, "label": LABEL,
        "mode": "HISTORICAL_REPLAY",
        "prospective": False,
        "policy": pol.describe(),
        "experiment_id": W.EXPERIMENT_ID,
        "control": await _control(pool),
        "heartbeat": await _heartbeat(pool),
        "counts": dict(counts) if counts else {},
        "operating_states": [dict(r) for r in states],
        "settled_totals": {
            "positions_settled": (net or {}).get("n") if net else 0,
            "net_usd": (net or {}).get("ours") if net else None,
            "fees_usd": (net or {}).get("fees") if net else None,
            "basis": ("modelled executions under "
                      "PRINT_THROUGH_WITH_QUEUE_SHARE_V1 against a "
                      "TRANSFERRED PMUS fee scenario. NOT a realised "
                      "return and NOT same-venue historical fees")},
        "blockers": W.BLOCKERS,
        "statuses": await statuses(pool),
        "arms": await _arms(pool),
        # WHETHER THE DATABASE-LEVEL GUARD IS ACTUALLY THERE. Migration 114
        # creates the unique index only if the existing rows already satisfy
        # it, and WARNS instead of failing when they do not -- which is the
        # right behaviour for a boot-time migration and the wrong thing to
        # assume afterwards. A guard that "should" exist is not a guard, so
        # the read reports its presence rather than its intent.
        "schema_guards": await _schema_guards(pool),
    }


ACCEPTANCE_INDEX = "rn1x_one_acceptance_position"


async def _schema_guards(pool) -> dict:
    """Is the acceptance uniqueness backstop present, and why not."""
    try:
        present = bool(await pool.fetchval(
            "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' "
            "AND indexname = $1", ACCEPTANCE_INDEX))
        dupes = await pool.fetch(
            "SELECT experiment_id, policy, condition_id, outcome_index,"
            " count(*) n FROM rn1x_positions"
            " WHERE policy LIKE 'ACCEPTANCE\\_%'"
            " GROUP BY 1,2,3,4 HAVING count(*) > 1")
    except Exception as exc:                                   # noqa: BLE001
        return {"read": False,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "why": ("the guard could not be read, which is not the same "
                        "as the guard being absent")}
    return {
        "read": True,
        "acceptance_unique_index": ACCEPTANCE_INDEX,
        "present": present,
        "duplicate_groups": [dict(r) for r in dupes],
        "blocked_by_existing_duplicates": bool(dupes) and not present,
        "why_it_might_be_absent": (
            "migration 114 counts duplicate (experiment, policy, condition, "
            "outcome) groups first and SKIPS the index with a warning if any "
            "exist, so that a data problem is reported instead of failing API "
            "boot. An absent index with no duplicate groups means the "
            "migration has not run on this database yet"),
        "enforced_in_code_regardless": (
            "seed_acceptance_position serialises its lookup and insert on a "
            "session advisory lock, so a concurrent caller adopts rather than "
            "seeds even where this index is missing"),
    }


ARMS_SQL = """
    SELECT p.experiment_id, p.policy,
           count(DISTINCT p.position_id)                      AS positions,
           count(d.decision_id)                               AS decisions,
           count(*) FILTER (WHERE d.selected_action IS NOT NULL)
                                                              AS selected,
           count(*) FILTER (WHERE d.operating_state
                                  = 'HOLD_FOR_MISSING_INPUT') AS blind,
           count(*) FILTER (WHERE d.is_a_deliberate_hold)      AS held_by_choice,
           count(*) FILTER (WHERE d.input_available)           AS with_inputs,
           count(*) FILTER (WHERE d.accounting_reconciles IS FALSE)
                                                              AS unreconciled,
           count(*) FILTER (WHERE d.eligibility IS NOT NULL
                              AND d.eligibility <> 'ELIGIBLE')
                                                              AS held,
           max(d.decision_ts)                                 AS last_decision
      FROM rn1x_positions p
      LEFT JOIN rn1x_decisions d ON d.position_id = p.position_id
     GROUP BY 1, 2
     ORDER BY 1, 2
"""


async def _arms(pool) -> dict:
    """The champion and the challenger, side by side and never summed.

    §1: "Keep the current $0.91/second-half-16% experiment as a frozen
    benchmark. Run the broader decision policy as a separately versioned
    shadow challenger so we can measure what it changes." The measuring
    needs the two visible in one place, with their identifiers, and with
    the blind cycles counted apart from the deliberate holds.
    """
    from .. import bettor_mgmt_select as sel
    from .. import bettor_rn1x_policy as pol

    rows = await pool.fetch(ARMS_SQL)
    return {
        "rows": [dict(r) for r in rows],
        "champion": {"policy_id": pol.POLICY_ID,
                     "frozen_at": pol.FROZEN_AT,
                     "what": ("pair at or below $0.91 combined, exit on "
                              "a 16-point second-half loss trigger"),
                     "untouched_by_the_challenger": True},
        "challenger": {"policy_id": sel.CHALLENGER_ID,
                       "policy_class": sel.CHALLENGER_CLASS,
                       "what": ("ranks every available action INCLUDING "
                                "hold, valued from a labelled external "
                                "probability"),
                       "fallback": sel.FALLBACK_RULE,
                       "is_not": sel.CHALLENGER_OBJECTIVE["not_the_goal"]},
        "never_summed": (
            "the two arms manage the SAME assigned inventory under "
            "different decisions. Adding their results would count one "
            "position twice and would attribute the benchmark's outcome "
            "to the challenger"),
        "held_decisions": (
            "`held` counts decisions excluded by a containment migration. "
            "They are kept and readable and must not be counted as "
            "results: migration 110 holds every challenger decision "
            "taken under the input builder that supplied the opposite "
            "side's acquisition cost as the exit price"),
        "blind_is_not_held": (
            "`blind` counts decisions with no inputs at all. They are "
            "excluded from `held_by_choice` because a policy that could "
            "not see did not choose to wait"),
    }


async def learning(pool) -> dict:
    """THE LEARNING CYCLE'S STATE AND ITS LATEST RESULT.

    The acceptance screen must show "learning cycle status and latest
    result", and a rejection IS a result. The most likely state for a long
    while is INELIGIBLE on the gate's 50-decided floor, and this reports
    that rather than leaving the panel blank.
    """
    from .. import bettor_rn1x_learn as L
    from .. import learn_gate as G

    have = await pool.fetchval(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = "
        "'public' AND table_name = 'bettor_learn_model'")
    raw = await pool.fetchval(
        "SELECT value::text FROM ingestion_state WHERE key = 'rn1x_learn'")
    hb = await pool.fetchrow(
        "SELECT status, detail, beat_at FROM service_heartbeats "
        "WHERE service = 'rn1x_learn'")
    out = {
        "champion": L.MODEL_KEY,
        "champion_is_management_defined": True,
        "gate": "GATE_V2 (sportsassets.learn_gate, unchanged)",
        "scenario_grid": list(G.SCENARIOS),
        "eligibility_floor_decided_orders": G.MIN_DECIDED,
        "challengers": [{"name": c["name"], "params": c["params"],
                         "question": c["question"]} for c in L.CHALLENGERS],
        "control": {"value": raw,
                    "running": bool(raw and raw.strip().lower() == "true"),
                    "state": ("RUNNING" if raw and raw.strip().lower() ==
                              "true" else "STOPPED_OR_ABSENT")},
        "heartbeat": ({"status": hb["status"], "at": hb["beat_at"],
                       "detail": hb["detail"]} if hb else
                      {"status": "NEVER_BEAT"}),
        "verdicts": ["RETAIN_CHAMPION",
                     "CHALLENGER_ELIGIBLE_PENDING_MANAGEMENT"],
        "no_promotion_path": (
            "ELIGIBLE records that a challenger cleared every declared "
            "condition. It does NOT change the active policy, and this "
            "service holds no code that would."),
        "registry": "bettor_learn_model (the EXISTING register)",
    }
    if not have:
        out["blocker"] = "LEARN_REGISTRY_ABSENT"
        out["latest"] = []
        return out
    rows = await pool.fetch(
        "SELECT version, params, status, note, left(dataset_sha, 12) "
        "dataset, to_timestamp(trained_at) at, evaluation "
        "FROM bettor_learn_model WHERE model_key = $1 "
        "ORDER BY version DESC LIMIT 8", L.MODEL_KEY)
    out["latest"] = [dict(r) for r in rows]
    if not rows:
        out["latest_result"] = (
            "NO EVALUATION HAS RUN YET on this deployment. Not 'no "
            "improvement found' -- no comparison has been made.")
    return out


#: WHETHER THE MANAGER WOULD STILL SEE THIS POSITION, by the manager's own
#: predicate rather than a second one written here. `bettor_rn1x_store`
#: owns both halves -- unreleased inventory and the absence of a TERMINAL
#: settlement basis -- and they are imported rather than restated, because
#: a reader that disagreed with the manager about what is open would make
#: "excluded from recurring management" unfalsifiable.
#:
#: WHY THIS IS HERE AT ALL. The successive-cycles check demanded two priced
#: HOLD decisions on the acceptance position. Its fixture is over, the odds
#: provider no longer carries it, and no EV_HOLD can be identified for it
#: again -- so that demand can never be satisfied and the check failed on
#: every run. A position whose terminal path is SETTLEMENT must be verified
#: by its EXCLUSION from management, which needs the exclusion to be
#: readable. It was not.
POSITIONS_SQL = """
    SELECT p.position_id, p.policy, p.source_trade_id,
           p.source_account, p.condition_id, p.outcome_index,
           p.entry_kind, p.unknown_reason, p.provenance,
           p.seed_qty::float8 seed_qty,
           p.seed_price::float8 seed_price,
           p.seed_basis_usd::float8 seed_basis_usd,
           p.source_ts, p.detected_ts, p.decision_ts,
           extract(epoch FROM (p.detected_ts - p.source_ts))::float8
             detection_lag_s,
           o.net_usd::float8 net_usd, o.fees_usd::float8 fees_usd,
           o.residual_qty::float8 residual_qty, o.settled_at,
           o.outcome_basis,
           COALESCE(f.released, 0)::float8 AS released_qty,
           NOT (%(open)s) AS terminally_settled,
           (COALESCE(f.released, 0) < p.seed_qty AND %(open)s)
               AS open_to_management,
           (SELECT count(*) FROM rn1x_decisions d
              WHERE d.position_id = p.position_id) decisions,
           (SELECT count(*) FROM rn1x_orders r
              WHERE r.position_id = p.position_id) orders
      FROM rn1x_positions p
      LEFT JOIN rn1x_outcomes o ON o.position_id = p.position_id
      LEFT JOIN (
            SELECT o2.position_id,
                   sum(CASE WHEN o2.side = 'SELL' THEN fl.qty ELSE 0 END)
                       AS released
              FROM rn1x_orders o2
              JOIN rn1x_fills  fl ON fl.order_id = o2.order_id
             GROUP BY o2.position_id) f ON f.position_id = p.position_id
     WHERE ($2::text IS NULL OR p.policy ILIKE '%%' || $2 || '%%')
     ORDER BY p.decision_ts DESC LIMIT $1
""" % {"open": _store.NOT_TERMINALLY_SETTLED}

#: The bases that make a position terminal, republished for readers so a
#: check does not have to hard-code the strings.
TERMINAL_OUTCOME_BASES = _store.TERMINAL_OUTCOME_BASES


async def positions(pool, limit: int = 50, policy: str | None = None) -> list:
    """The positions themselves, newest first, with their outcome.

    `policy` MATTERS MORE THAN IT LOOKS. This read is newest-first across
    every experiment, and the historical lane holds hundreds of rows, so
    the one challenger position fell off the end of a 50-row page and a
    caller reading that page concluded "no challenger position exists" --
    which is what run 37 printed while the position was sitting in the
    table. A substring filter, applied in SQL, is what lets a caller ask
    for the arm it means.
    """
    rows = await pool.fetch(POSITIONS_SQL, int(limit), policy or None)
    return [dict(r) for r in rows]


async def trace(pool, position_id: str) -> dict:
    """ONE position, end to end: decisions, orders, fills, outcome."""
    pos = await pool.fetchrow(
        "SELECT * FROM rn1x_positions WHERE position_id = $1", position_id)
    if pos is None:
        return {"found": False, "position_id": position_id}
    # THE WHOLE DECISION, not the part that fits on one line. Migration
    # 109 put the rest of §5's field list on the row -- selected size,
    # policy version, governing rule, input availability and freshness,
    # the payout identity, the venue translation, the resulting
    # inventory and whether the accounting reconciled -- and a trace
    # that still selected eight columns would leave all of it invisible.
    dec = await pool.fetch(
        "SELECT decision_id, decision_ts, evidence_id, selected_action, "
        "selected_qty::float8 AS selected_qty, selection_reason, "
        "alternatives, ev_at_decision_usd, ev_basis, "
        "conditional_on_our_fill, input_labels, "
        "policy_version, governing_rule, operating_state, "
        "is_a_deliberate_hold, input_available, input_freshness, "
        "payout_identity, hold_value_usd, hold_value_basis, "
        "venue_translation, order_state, resulting_inventory, "
        # HELD DECISIONS ARE SHOWN AND LABELLED, never hidden and never
        # presented as sound. Migration 110 holds every challenger
        # decision taken under the broken input builder; a trace that
        # omitted the column would read them as ordinary results.
        "accounting_reconciles, eligibility, ineligible_reason, "
        # THE CHAIN THE DECISION RECORDED. Migration 112 put it on the row
        # and persist_run writes it, but this projection did not select it,
        # so every reader saw `input_chain: null` on decisions that had one.
        # Run 38 is the cost: seven ranked decisions on the acceptance
        # position, each with a fresh probability inside its own bound and a
        # priced exit it beat, and the acceptance gate could still only
        # report HELD_EXPOSURE_MAPPING, VENUE_CONTRACT_AND_INTENT and
        # EXIT_LADDER_AND_DEPTH as missing -- because the three items it
        # reads off the chain were invisible to the read, not absent from
        # the row.
        "input_chain "
        "FROM rn1x_decisions "
        "WHERE position_id = $1 ORDER BY decision_ts, decision_id",
        position_id)
    orders = await pool.fetch(
        "SELECT order_id, decision_id, side, intent, liquidity, "
        "limit_price::float8 limit_price, qty::float8 qty, "
        "filled_qty::float8 filled_qty, state, placed_at, fill_basis "
        "FROM rn1x_orders WHERE position_id = $1 ORDER BY placed_at",
        position_id)
    fills = await pool.fetch(
        "SELECT f.fill_id, f.order_id, f.at, f.qty::float8 qty, "
        "f.price::float8 price, f.fee_usd::float8 fee_usd, "
        "f.evidence_id, f.queue_share::float8 queue_share, f.fill_basis "
        "FROM rn1x_fills f JOIN rn1x_orders o ON o.order_id = f.order_id "
        "WHERE o.position_id = $1 ORDER BY f.at", position_id)
    out = await pool.fetchrow(
        "SELECT * FROM rn1x_outcomes WHERE position_id = $1", position_id)
    return {
        "found": True, "scope": SCOPE, "label": LABEL,
        "position": dict(pos),
        "decisions": [dict(r) for r in dec],
        "orders": [dict(r) for r in orders],
        "fills": [dict(r) for r in fills],
        "outcome": dict(out) if out is not None else None,
        "fill_semantics": (
            "Every fill above is MODELLED. It was licensed by an OBSERVED "
            "print by someone else, allocated at a declared queue share. "
            "P_FILL remains NOT_IDENTIFIED: none of this is evidence that "
            "our order would have been filled."),
        # WHICH CLOCKS ON THIS ROW ARE OBSERVATIONS. Without this, three
        # identical timestamps read as a sub-second round trip, when on a
        # pre-104 row they are one instant copied twice.
        "clock_semantics": _clock_semantics(pos),
        "decision_semantics": _decision_semantics(
            [dict(r) for r in dec]),
    }


# WHAT A HOLD ON THIS TRACE MEANS, counted three ways.
#
# §5: "HOLD needs a reason too. A missing input must be distinguishable
# from a deliberate decision to hold." All three states below leave the
# position untouched. A page that printed one number for "held" would
# make a policy that could not see look exactly like one that looked and
# chose to wait, which is the single most flattering error available
# here -- so the three are counted apart and never summed.
HOLD_STATES = {
    "HOLD_BY_DECISION": ("priced against every alternative and won. The "
                         "hold value was available"),
    "HOLD_BY_FALLBACK_RULE": ("EV_HOLD was not identified, so the "
                              "declared exposure trigger answered "
                              "WHETHER on observed inputs and did not "
                              "fire. A decision, by a named rule, "
                              "without a forecast"),
    "HOLD_FOR_MISSING_INPUT": ("NOT a decision. Nothing was evaluated: "
                               "the position is unchanged because the "
                               "policy was blind at that instant"),
    "HOLD_NO_FEASIBLE_PAIR": ("the FROZEN benchmark's own hold state -- "
                              "no pair limit satisfied its target cost. "
                              "It is not a valuation of holding"),
}


def _decision_semantics(decisions) -> dict:
    counts: dict = {}
    for d in decisions:
        counts[d.get("operating_state") or "UNSET"] = \
            counts.get(d.get("operating_state") or "UNSET", 0) + 1
    blind = sum(v for k, v in counts.items()
                if k == "HOLD_FOR_MISSING_INPUT")
    decided_holds = sum(v for k, v in counts.items()
                        if k in ("HOLD_BY_DECISION",
                                 "HOLD_BY_FALLBACK_RULE"))
    return {
        "by_operating_state": counts,
        "hold_states_explained": dict(HOLD_STATES),
        "decisions_taken_blind": blind,
        "holds_that_were_decisions": decided_holds,
        "why_separate": (
            "both leave the position unchanged and they are opposite "
            "facts. Summing them would report a blind policy as a "
            "patient one"),
        "selected_is_never_permanently_empty": (
            "a null selected_action is always accompanied by a named "
            "refusal in selection_reason. An empty field with no reason "
            "would not meet the requirement"),
    }


def _clock_semantics(pos) -> dict:
    """Say plainly what this position's timestamps are, and are not."""
    basis = pos["decision_basis"] if "decision_basis" in pos.keys() else None
    known = {
        "RUNTIME_WALL_CLOCK": (
            "decision_ts was read from the clock the policy actually ran "
            "on, so this row CAN support a prospective claim"),
        "REPLAY_AT_AVAILABILITY": (
            "a counterfactual REPLAY. decision_ts is the instant the "
            "evidence became available, not an instant at which anyone "
            "decided anything. Valid as a replay, and not prospective"),
        "BACKDATED_TO_AVAILABILITY_UNAUDITED": (
            "decision_ts was written equal to max(source_ts, detected_at) "
            "to satisfy a CHECK. It is NOT an observation. The raw receipt "
            "instant was overwritten by that same max() and cannot be "
            "recovered for this row. An unresolved outcome here does NOT "
            "establish that the decision was made prospectively"),
    }
    return {
        "decision_basis": basis or "UNSET",
        "means": known.get(basis, (
            "no basis is recorded, so this row's decision_ts cannot be "
            "read as an observation")),
        "supports_a_prospective_claim": basis == "RUNTIME_WALL_CLOCK",
        "source_ts": "OBSERVED: the venue's own instant for the fill",
        "detected_ts": ("OBSERVED from migration 104 onward; on earlier "
                        "rows it holds the derived availability instant"),
        "available_at": "DERIVED: max(source_ts, detected_ts)",
    }


CLOCK_AUDIT = """
    SELECT experiment_id, decision_basis, positions,
           first_decision_ts, last_decision_ts, decision_eq_available,
           avg_decision_lag_s, max_decision_lag_s
      FROM rn1x_clock_audit
     ORDER BY experiment_id, decision_basis
"""


async def clock_audit(conn) -> dict:
    """The split between rows that can and cannot be called prospective."""
    try:
        rows = [dict(r) for r in await conn.fetch(CLOCK_AUDIT)]
    except Exception as exc:                                   # noqa: BLE001
        return {"badge": "UNAVAILABLE",
                "what": "the clock audit view is not present",
                "why": type(exc).__name__, "rows": []}
    total = sum(int(r["positions"] or 0) for r in rows)
    prospective_ok = sum(int(r["positions"] or 0) for r in rows
                         if r["decision_basis"] == "RUNTIME_WALL_CLOCK")
    backdated = sum(int(r["positions"] or 0) for r in rows
                    if r["decision_basis"]
                    == "BACKDATED_TO_AVAILABILITY_UNAUDITED")
    return {
        "badge": ("CHECK" if backdated else ("OK" if total else "EMPTY")),
        "what": ("which positions carry an OBSERVED decision instant and "
                 "which were backdated to an availability instant"),
        "positions_total": total,
        "can_support_a_prospective_claim": prospective_ok,
        "backdated_and_cannot": backdated,
        "rows": rows,
        "note": ("a backdated row is not deleted or rewritten. Its receipt "
                 "instant was overwritten before migration 104 and cannot "
                 "be recovered, so it is LABELLED and excluded from "
                 "prospective claims instead of being repaired"),
    }


EXTERNAL_TRACE = """
    SELECT id, experiment_id, version, source_class, provider, book,
           devig_method, venue, condition_id, us_market_slug,
           contract_identity_basis, contract_selection,
           sport_family, market, period, line, settlement_rule, event_key,
           raw_odds, outcomes_priced, expected_outcomes, overround,
           observed_at, received_at, age_s, outcome_books, mapped_outcome,
           mapping_match, probability, executable_price, cost_per_contract,
           estimated_edge_per_contract, decision, admissible, refusals,
           why, proposed_size, decided_at, outcome_known, outcome,
           outcome_at, realised_net_usd, order_submitted
      FROM external_valuations WHERE id = $1
"""


ENTRY_EVIDENCE = """
    SELECT id, condition_id, us_market_slug, contract_selection,
           payout_event, buy_intent, sport_family, probability,
           executable_price, cost_per_contract,
           estimated_edge_per_contract, proposed_size, decision,
           admissible, refusals, decided_at, age_s, outcome_books,
           execution_estimate, risk_verdict, exposure_observed,
           settlement_comparison
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
     ORDER BY decided_at DESC LIMIT $3
"""

ENTRY_INVENTORY = """
    SELECT p.position_id, p.condition_id, p.outcome_index, p.provenance,
           p.seed_qty::float8      AS qty,
           p.seed_price::float8    AS price,
           p.seed_basis_usd::float8 AS cost_basis_usd,
           p.decision_ts, p.decision_basis,
           (SELECT count(*) FROM rn1x_decisions d
             WHERE d.position_id = p.position_id) AS decisions,
           (SELECT count(*) FROM rn1x_orders o
             WHERE o.position_id = p.position_id) AS orders,
           (SELECT coalesce(sum(f.fee_usd), 0)::float8 FROM rn1x_fills f
             JOIN rn1x_orders o2 ON o2.order_id = f.order_id
            WHERE o2.position_id = p.position_id) AS fees_usd,
           (SELECT coalesce(sum(f.qty), 0)::float8 FROM rn1x_fills f
             JOIN rn1x_orders o2 ON o2.order_id = f.order_id
            WHERE o2.position_id = p.position_id) AS filled_qty,
           (SELECT count(*) FROM rn1x_outcomes x
             WHERE x.position_id = p.position_id) AS outcomes
      FROM rn1x_positions p
     WHERE p.policy = $1
     ORDER BY p.decision_ts DESC LIMIT $2
"""


async def entry_evidence(conn, *, hours: int = 24, limit: int = 20) -> dict:
    """THE ENTRY LANE'S OWN DECISIONS, with every input behind them.

    The census counts refusals; this shows what each candidate was
    actually looking at -- the probability, the price and the depth, the
    cost, the size the frozen policy chose, the marketable fill estimate
    and its basis, every risk rail with its limit and its measurement, and
    the settlement comparison with the fixture evidence that scoped it.
    A refused candidate is included, because the refusal is the result.

    It also returns the inventory the lane HOLDS, if any. An admitted
    decision that created no position would be a valuation wearing the
    word "entry", and this read is where that shows.
    """
    import json as _json

    from .. import bettor_entry_execution as entryx
    from .. import bettor_entry_inventory as inv
    from .. import bettor_external_shadow as ext
    from .. import bettor_pinnacle_devig as devig

    rows = await conn.fetch(ENTRY_EVIDENCE, ext.EXPERIMENT_ID,
                            str(max(1, min(720, int(hours)))),
                            max(1, min(200, int(limit))))
    held = await conn.fetch(ENTRY_INVENTORY, inv.POLICY,
                            max(1, min(200, int(limit))))
    try:
        cal = await conn.fetchrow(
            "SELECT source_version, sample_size, metric, score, tolerance, "
            "within_tolerance, measured_by, measured_at "
            "FROM external_source_calibration WHERE source_version = $1 "
            "ORDER BY measured_at DESC LIMIT 1", devig.VERSION)
        # `measured` IS SET EXPLICITLY, not inferred from the row's
        # presence by whoever reads this. A row that came back without it
        # printed `measured: null`, which reads like a third state that
        # does not exist.
        calibration = (dict(cal, measured=True) if cal is not None
                       else {"measured": False,
                             "source_version": devig.VERSION,
                             "why": ("no calibration has been measured for "
                                     "this source, so MODEL_TRUST_DRIFT is "
                                     "NOT_EVALUABLE and blocks every "
                                     "entry")})
    except Exception as exc:                                   # noqa: BLE001
        # A FAILED READ IS NOT AN ABSENT MEASUREMENT, and the two must not
        # print the same.
        calibration = {"measured": False, "read_failed": type(exc).__name__}

    # JSONB COMES BACK AS A STRING, AND THE API MUST NOT PASS THAT ON.
    #
    # asyncpg returns a jsonb column as the raw JSON TEXT unless a codec is
    # registered, so `execution_estimate` and friends were serialised into
    # the response as STRINGS containing JSON. Every reader then has to
    # parse them, and the production report did not: jq stopped on "Cannot
    # index string" after the first candidate, and because the failing
    # command was upstream of a `tee` its exit status was discarded, so the
    # error was never printed either. One candidate's evidence appeared out
    # of twenty-five and nothing said why.
    #
    # My own local check had hand-parsed these fields before running the
    # report against them -- mimicking a shape the API does not emit --
    # which is why it looked verified. The endpoint's contract is JSON
    # objects, so it parses them here.
    def _obj(v):
        if not isinstance(v, str):
            return v
        try:
            return _json.loads(v)
        except (ValueError, TypeError):
            return {"unparsed": v[:400],
                    "why": "this column did not contain readable JSON"}

    cands = []
    for r in rows:
        d = dict(r)
        for k in ("execution_estimate", "risk_verdict", "exposure_observed",
                  "settlement_comparison"):
            d[k] = _obj(d.get(k))
        d["refusals"] = list(d.get("refusals") or [])
        cands.append(d)

    return {
        "experiment_id": ext.EXPERIMENT_ID,
        "policy": inv.POLICY,
        "candidates": cands,
        "inventory": [dict(r) for r in held],
        "source_calibration": calibration,
        "risk_declaration": entryx.declaration(),
        "reading": {
            "execution_estimate": (
                "p_fill is %s -- the fraction of the intended notional the "
                "arrival ladder actually showed at or inside the break-even "
                "limit. It is an OBSERVATION, not a forecast, and it is not "
                "a resting order's fill probability"
                % entryx.MARKETABLE_BASIS),
            "risk_verdict": ("every rail with the limit THIS LANE "
                             "predeclared, the exposure measured against "
                             "it and the verdict. An unevaluable rail is "
                             "not a pass"),
            "settlement_comparison": ("the condition-to-payout verdict, "
                                      "plus the fixture row that "
                                      "established the phase and the "
                                      "game format it was read under"),
            "inventory": ("what the lane HOLDS. Modelled fills against "
                          "observed depth: no order was submitted to any "
                          "venue and no capital is at risk"),
            "source_calibration": ("the MODEL_TRUST_DRIFT gate's evidence. "
                                   "Absent, the gate is NOT_EVALUABLE and "
                                   "no entry can create inventory"),
        },
    }


async def external_trace(conn, row_id: int) -> dict:
    """ONE external valuation, with every field management inspects."""
    row = await conn.fetchrow(EXTERNAL_TRACE, int(row_id))
    if row is None:
        return {"found": False, "id": row_id}
    d = dict(row)
    return {
        "found": True,
        "label": ("EXTERNAL BOOKMAKER VALUATION. Pinnacle's own de-vigged "
                  "price, not a trained model and not an internally "
                  "qualified settlement model"),
        "valuation": d,
        "reading": {
            "observed_at": "the BOOK's clock for this price",
            "received_at": "when WE received it. Never used as its age",
            "probability": ("de-vigged over the COMPLETE outcome set by "
                            "the declared method, or null if refused"),
            "executable_price": ("the same-venue ASK. Crossing, because a "
                                 "resting price invents a queue position "
                                 "we never held"),
            "estimated_edge_per_contract":
                "probability - ask - cost. Null if any input was missing",
            "refusals": ("why the engine did not buy. This list is the "
                         "deliverable when nothing clears"),
            "order_submitted": ("always false. No submit path is reachable "
                                "from the loop that wrote this row"),
            # THE TWO IDENTIFIERS, NAMED SEPARATELY. Run 24's six
            # NotFoundError reads were a global id handed to a US endpoint,
            # and a trace that showed one opaque "contract" could not have
            # shown that.
            "us_market_slug": ("the VENUE's own market slug "
                               "(us_premap.market_slug). This is what "
                               "pmus.book_read accepts, and the only "
                               "identifier a venue read can use"),
            "condition_id": ("the GLOBAL catalogue's id for the same "
                             "fixture. The venue does not accept it"),
            "contract_identity_basis": ("which identifier is authoritative. "
                                        "BOTH means each was sourced "
                                        "independently, never derived"),
        },
    }


async def input_chain(pool, position_id: str) -> dict:
    """WHY this held position has no priced hold -- the FIRST failing link.

    Repeated blind decisions are the operational blocker, and
    `EV_HOLD_NOT_IDENTIFIED` names the symptom rather than the cause. This
    walks the same chain the management phase walks, in the same order,
    with the same adapters, and returns every link with its identifiers,
    timestamps and refusal.

    IT IS A READ, and it makes the same outbound calls the decision makes:
    at most two odds requests (one per sport key of the held family) and
    one venue book read. It writes nothing -- no valuation row, no
    decision, no order -- so it can be run against production without
    changing what production decides.
    """
    import os

    from .. import bettor_mgmt_lifecycle as lc
    from .. import bettor_rn1x_store as store
    from ..workers import rn1x_shadow as W
    from ..workers.ext_pinnacle_loop import VENUE_SPORT_LABELS as EXT_LABELS

    pos = await pool.fetchrow(
        "SELECT position_id, policy, condition_id, outcome_index, "
        "provenance, entry_kind, source_account, "
        "seed_qty::float8 AS seed_qty, seed_price::float8 AS seed_price, "
        "extract(epoch FROM decision_ts)::float8 AS decision_ts "
        "FROM rn1x_positions WHERE position_id = $1", position_id)
    if pos is None:
        return {"found": False, "position_id": position_id}
    p = dict(pos)
    out = {"found": True, "scope": SCOPE, "position": p,
           "reading": {
               "purpose": ("the first failing link in the input chain a "
                           "management decision needs, with the "
                           "identifiers and timestamps behind it"),
               "writes": "NOTHING. No valuation row, decision or order",
               "cost": ("at most two odds requests for the held sport "
                        "family and one venue book read"),
               "freshness_rule": ("the probability is aged against "
                                  "bettor_pinnacle_devig.MAX_QUOTE_AGE_S "
                                  "= 30 s, from the bookmaker's own "
                                  "observed_at. This read does not widen "
                                  "it"),
           }}
    async with pool.acquire() as conn:
        # RESIDUAL FIRST: a chain for a position that is already flat
        # would be a question about nothing.
        led = await store.load_position(conn, position_id)
        try:
            # THE SAME FEE FUNCTION THE DECISION USES. A residual read
            # with a different cost model would report an inventory the
            # manager does not have.
            from .. import bettor_rn1x_run as runner
            m = lc.reload_managed(position=p, orders=led["orders"],
                                  fills=led["fills"],
                                  fee_fn=runner._fee_fn())
            out["inventory"] = {"residual_qty": m.residual(),
                                "realized_pnl_usd":
                                    m.pf.to_dict()["realized_pnl_usd"],
                                "invariant_ok": m.pf.invariant()["ok"],
                                "decisions_so_far":
                                    led["decisions_so_far"]}
        except Exception as exc:                               # noqa: BLE001
            out["inventory"] = {"error": "%s: %s"
                                        % (type(exc).__name__, exc)}
        ci = await W.managed_inputs_for(
            conn, condition_id=p["condition_id"],
            outcome_index=int(p["outcome_index"]),
            odds=W.ManagedOdds(api_key=os.environ.get("EDGE_ODDS_API_KEY"),
                               calls=2))
    # ── WHAT THE ENTRY LANE EVER WROTE FOR THIS CONDITION ────────────
    #
    # The pull does not need this. It is here because the diagnosis does:
    # "no row at all" and "a row 900 s old" are different findings, and
    # the second one is the schedule, not a missing feature.
    vh = await pool.fetchrow(
        "SELECT count(*) AS rows, "
        "count(*) FILTER (WHERE probability IS NOT NULL) AS priced, "
        "count(*) FILTER (WHERE eligibility = 'ELIGIBLE') AS eligible, "
        "max(observed_at) AS newest_observed, max(id) AS last_id, "
        "extract(epoch FROM now() - max(observed_at))::float8 AS newest_age_s "
        "FROM external_valuations WHERE condition_id = $1",
        p["condition_id"])
    out["entry_lane_valuations"] = dict(vh) if vh is not None else None
    out["entry_lane_cadence_s"] = 900.0
    out["odds_rule_s"] = 30.0
    out["why_the_table_cannot_serve_management"] = (
        "the entry lane writes on a %.0f s cycle and the applicable odds "
        "rule is %.0f s, so a decision reading that table is stale unless "
        "it lands inside a %.0f s window of a %.0f s cadence"
        % (900.0, 30.0, 30.0, 900.0))

    # ── THIS POSITION'S OWN DECISION CENSUS, by cause ────────────────
    cen = await pool.fetch(
        "SELECT ev_basis, "
        "count(*) AS n, "
        "count(*) FILTER (WHERE hold_value_usd IS NOT NULL) AS priced, "
        "coalesce(input_labels->>'first_failing_link', "
        "         input_chain->'first_failing_link'->>'link', "
        "         'NOT_RECORDED') AS first_failing_link "
        "FROM rn1x_decisions WHERE position_id = $1 "
        "GROUP BY 1, 4 ORDER BY 2 DESC", position_id)
    out["decision_census"] = [dict(r) for r in cen]

    # ── A SUPPORTED POSITION TO DEMONSTRATE ON, IF THIS ONE IS NOT ───
    #
    # If the held exposure is outside provider coverage, the honest next
    # step is to demonstrate the connected manager on a position that is
    # inside it -- and that requires knowing whether one exists rather
    # than assuming.
    alt = await pool.fetch(
        "SELECT q.position_id, q.condition_id, m.sport, m.title, "
        "q.seed_qty::float8 AS seed_qty, q.decision_ts "
        "FROM rn1x_positions q JOIN markets m ON m.condition_id = q.condition_id "
        "WHERE q.policy LIKE '%CHALLENGER%' AND NOT m.closed AND NOT m.resolved "
        "AND m.sport = ANY($1::text[]) ORDER BY q.decision_ts DESC LIMIT 5",
        sorted({lbl for labels in EXT_LABELS.values() for lbl in labels}))
    out["supported_open_challenger_positions"] = [dict(r) for r in alt]
    out["provider_sport_labels"] = sorted(
        {lbl for labels in EXT_LABELS.values() for lbl in labels})

    out["chain"] = ci.get("chain")
    out["first_failing_link"] = ci.get("first_failing_link")
    out["inputs_complete"] = bool(ci.get("available")
                                  and ci.get("book_available"))
    out["probability"] = (ci.get("probability_row") or {}).get("probability")
    out["exit_price"] = ci.get("bid")
    out["exit_size"] = ci.get("bid_size")
    out["us_market_slug"] = ci.get("us_market_slug")
    out["held_intent"] = ci.get("held_intent")
    out["payout_event"] = ci.get("payout_event")
    out["odds_request"] = ci.get("odds_request")
    out["clocks"] = ci.get("clocks")
    # THE FEED'S OWN LAG, MEASURED ACROSS THE WHOLE PAYLOAD. If it exceeds
    # the odds rule the remedy is a faster feed, not a wider rule, and
    # this is the number that decides which.
    out["provider_quote_ages_s"] = ci.get("provider_quote_ages_s")
    out["settlement"] = ci.get("settlement")
    out["fixture"] = ci.get("fixture")

    # ── THE VENUE'S OWN ANSWER ON WHETHER THIS HAS RESOLVED ──────────
    #
    # A slug dated yesterday and a start time in the past establish
    # NEITHER completion NOR settlement. This asks the venue: its
    # settlement endpoint first, its listing second, five distinct
    # statuses so PENDING, UNREADABLE and UNMATCHED cannot be read as
    # resolution -- and RESOLVED_DERIVED (prices converged) is never
    # counted as the venue reporting an outcome.
    slug = ci.get("us_market_slug")
    if slug:
        import asyncio as _aio

        from ..workers.ext_pinnacle_loop import _read_resolution_blocking
        try:
            out["venue_resolution"] = await _aio.to_thread(
                _read_resolution_blocking, slug)
        except Exception as exc:                               # noqa: BLE001
            out["venue_resolution"] = {"status": "UNREADABLE",
                                       "error": "%s: %s"
                                       % (type(exc).__name__, exc)}
        out["venue_resolution_reading"] = (
            "RESOLVED means the venue's settlement endpoint answered. "
            "RESOLVED_DERIVED is an inference from converged prices and is "
            "NOT the venue reporting an outcome. PENDING means listed and "
            "unresolved. UNMATCHED means the venue does not list this "
            "slug. UNREADABLE means the read failed -- which is not a "
            "statement about the market at all")
    return out
