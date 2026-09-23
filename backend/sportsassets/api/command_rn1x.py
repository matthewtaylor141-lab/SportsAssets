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
    }


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
    }
