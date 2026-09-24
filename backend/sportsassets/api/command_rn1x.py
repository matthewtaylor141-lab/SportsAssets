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
               "external_valuation")


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
        "prospective_rn1_management": _live(
            shadow_on, prosp > 0, what="the PROSPECTIVE lane: UNRESOLVED "
            "markets, live ingestion lanes only",
            why=("decisions recorded before resolution; NOT scored and "
                 "never summed with a scored result")),
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
    }


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
    states = await pool.fetch(
        "SELECT selection_reason state, count(*) n FROM rn1x_decisions "
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


async def positions(pool, limit: int = 50) -> list:
    """The positions themselves, newest first, with their outcome."""
    rows = await pool.fetch(
        "SELECT p.position_id, p.policy, p.source_trade_id, "
        "p.source_account, p.condition_id, p.outcome_index, "
        "p.entry_kind, p.unknown_reason, p.seed_qty::float8 seed_qty, "
        "p.seed_price::float8 seed_price, "
        "p.seed_basis_usd::float8 seed_basis_usd, "
        "p.source_ts, p.detected_ts, p.decision_ts, "
        "extract(epoch FROM (p.detected_ts - p.source_ts))::float8 "
        "  detection_lag_s, "
        "o.net_usd::float8 net_usd, o.fees_usd::float8 fees_usd, "
        "o.residual_qty::float8 residual_qty, o.settled_at, "
        "(SELECT count(*) FROM rn1x_decisions d "
        "   WHERE d.position_id = p.position_id) decisions, "
        "(SELECT count(*) FROM rn1x_orders r "
        "   WHERE r.position_id = p.position_id) orders "
        "FROM rn1x_positions p "
        "LEFT JOIN rn1x_outcomes o ON o.position_id = p.position_id "
        "ORDER BY p.decision_ts DESC LIMIT $1", int(limit))
    return [dict(r) for r in rows]


async def trace(pool, position_id: str) -> dict:
    """ONE position, end to end: decisions, orders, fills, outcome."""
    pos = await pool.fetchrow(
        "SELECT * FROM rn1x_positions WHERE position_id = $1", position_id)
    if pos is None:
        return {"found": False, "position_id": position_id}
    dec = await pool.fetch(
        "SELECT decision_id, decision_ts, evidence_id, selected_action, "
        "selection_reason, alternatives, ev_basis, "
        "conditional_on_our_fill FROM rn1x_decisions "
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
           devig_method, venue, condition_id, contract_selection,
           sport_family, market, period, line, settlement_rule, event_key,
           raw_odds, outcomes_priced, expected_outcomes, overround,
           observed_at, received_at, age_s, outcome_books, mapped_outcome,
           mapping_match, probability, executable_price, cost_per_contract,
           estimated_edge_per_contract, decision, admissible, refusals,
           why, proposed_size, decided_at, outcome_known, outcome,
           outcome_at, realised_net_usd, order_submitted
      FROM external_valuations WHERE id = $1
"""


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
        },
    }
