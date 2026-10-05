"""READ-ONLY, BOUNDED LOADERS OF THE PROFITABILITY OS VIEW.

Every statement here is a SELECT (pinned by tests/test_pos_os_authority.py).
Every read is bounded by a window and a LIMIT and scoped to ONE paper
account (position keys and paper rows carry it). `load_all` runs each
loader in its own SAVEPOINT, so one failed read leaves the others usable;
a failed loader's input is None and its error is named in
inputs["_errors"] -- the section that needs it is UNAVAILABLE, never zero.

The caller owns the transaction: the API opens it READ ONLY with a bounded
statement_timeout.
"""
from __future__ import annotations

import json

from . import common as C

LIMIT_POSITIONS = 5000
LIMIT_FILLS = 3000
LIMIT_ORDERS = 10000
LIMIT_DECISIONS = 20000
LIMIT_ROWS = 2000
#: the recorded book used as the mid AT a fill: the fill's own book_obs_id,
#: else the latest observation of the market at or before the fill within
#: this many seconds
FILL_BOOK_MAX_S = 30.0
#: markout horizons (s) and the tolerance after each in which the FIRST
#: recorded observation is taken as the mark
HORIZONS = (60.0, 300.0, 1800.0)


def _tol(h):
    return max(30.0, 0.5 * h)


def _d(r):
    out = {}
    for k, v in dict(r).items():
        if v is not None and type(v).__name__ == "Decimal":
            v = float(v)
        out[k] = v
    return out


async def positions(conn, *, account_id, now, window_days):
    """PAPER positions of the account (pos_economics_latest) whose last
    event is inside the window or that are still open, and their
    COUNTERFACTUAL rows (separate book, never summed)."""
    pre = "paperpos:%s:" % account_id
    rows = await conn.fetch(
        "SELECT book, position_key, basis_position_key, counterfactual_kind,"
        "       group_id, us_market_slug, holding_side, strategy, state, "
        "       detail->'scope'->>'sleeve' AS sleeve, "
        "       detail->'scope'->>'policy_version' AS policy_version, "
        "       bought_qty, capital_committed_usd, open_cost_basis_usd, "
        "       capital_hours, time_committed_h, net_profit_usd, "
        "       expected_net_profit_usd, expected_capital_hours, "
        "       realized_profit_per_capital_hour, "
        "       expected_profit_per_capital_hour, predicted_edge_per_dollar,"
        "       realized_edge_per_dollar, probability, "
        "       extract(epoch FROM opened_at)::float8 AS opened_at, "
        "       extract(epoch FROM first_fill_at)::float8 AS first_fill_at, "
        "       extract(epoch FROM last_event_at)::float8 AS last_event_at, "
        "       extract(epoch FROM released_at)::float8 AS released_at, "
        "       extract(epoch FROM event_start_at)::float8 AS event_start_at,"
        "       extract(epoch FROM expected_release_at)::float8 "
        "       AS expected_release_at "
        "  FROM pos_economics_latest "
        " WHERE ((book = 'PAPER' AND left(position_key, length($1)) = $1) "
        "     OR (book = 'COUNTERFACTUAL' "
        "         AND left(basis_position_key, length($1)) = $1)) "
        "   AND (state = 'OPEN' OR last_event_at >= to_timestamp($2)) "
        " ORDER BY last_event_at DESC NULLS LAST LIMIT $3",
        pre, now - window_days * C.DAY, LIMIT_POSITIONS)
    return [_d(r) for r in rows]


async def fills(conn, *, account_id, now, window_days):
    """The account's fills in the window with their order's execution
    policy, the recorded book AT the fill and the first recorded book at
    each markout horizon."""
    marks = []
    params = [account_id, now - window_days * C.DAY, now, LIMIT_FILLS,
              FILL_BOOK_MAX_S]
    for i, h in enumerate(HORIZONS):
        a = len(params) + 1
        params += [h, h + _tol(h)]
        marks.append(
            " LEFT JOIN LATERAL (SELECT bids, offers, observed_at "
            "   FROM paper_book_observations b WHERE b.us_market_slug = "
            "   f.us_market_slug AND b.observed_at BETWEEN f.filled_at + "
            "   make_interval(secs => $%d) AND f.filled_at + "
            "   make_interval(secs => $%d) ORDER BY b.observed_at LIMIT 1) "
            "   m%d ON true" % (a, a + 1, i))
    sel = ", ".join(
        "m%d.bids AS m%d_bids, m%d.offers AS m%d_offers, "
        "extract(epoch FROM m%d.observed_at)::float8 AS m%d_at"
        % (i, i, i, i, i, i) for i in range(len(HORIZONS)))
    rows = await conn.fetch(
        "SELECT f.fill_id, f.order_id, f.group_id, f.role, f.direction, "
        "       f.holding_side, f.us_market_slug, f.fixture, f.label, "
        "       f.qty, f.price, f.fee_usd, f.gross_usd, f.strategy, "
        "       f.book_obs_id, "
        "       extract(epoch FROM f.filled_at)::float8 AS filled_at, "
        "       o.order_type, o.time_in_force, o.qty AS order_qty, "
        "       o.filled_qty AS order_filled_qty, "
        "       b0.bids AS b0_bids, b0.offers AS b0_offers, " + sel +
        "  FROM paper_fills f JOIN paper_orders o USING (order_id) "
        "  LEFT JOIN LATERAL (SELECT bids, offers FROM "
        "       paper_book_observations b WHERE (f.book_obs_id IS NOT NULL "
        "       AND b.obs_id = f.book_obs_id) OR (f.book_obs_id IS NULL AND "
        "       b.us_market_slug = f.us_market_slug AND b.observed_at <= "
        "       f.filled_at AND b.observed_at >= f.filled_at - "
        "       make_interval(secs => $5)) ORDER BY b.observed_at DESC "
        "       LIMIT 1) b0 ON true " + "".join(marks) +
        " WHERE f.account_id = $1 AND f.filled_at >= to_timestamp($2) "
        "   AND f.filled_at <= to_timestamp($3) "
        " ORDER BY f.filled_at DESC LIMIT $4", *params)
    return [_d(r) for r in rows]


async def orders(conn, *, account_id, now, window_days):
    rows = await conn.fetch(
        "SELECT order_id, strategy, role, direction, order_type, "
        "       time_in_force, qty, filled_qty, state, "
        "       extract(epoch FROM created_at)::float8 AS created_at, "
        "       extract(epoch FROM expires_at)::float8 AS expires_at, "
        "       extract(epoch FROM terminal_at)::float8 AS terminal_at "
        "  FROM paper_orders WHERE account_id = $1 "
        "   AND created_at >= to_timestamp($2) "
        " ORDER BY created_at DESC LIMIT $3",
        account_id, now - window_days * C.DAY, LIMIT_ORDERS)
    return [_d(r) for r in rows]


async def decisions(conn, *, account_id, now, window_days):
    rows = await conn.fetch(
        "SELECT decision_id, strategy, verdict, refusal, refusals, "
        "       p_blended, p_pinnacle, p_internal, policy_version, label, "
        "       us_market_slug, holding_side, book_obs_id, "
        "       (economics->'acquisition'->>'expected_net_profit_usd') "
        "       AS acq_ev, "
        "       (policy_decision->>'net_expected_profit_usd') AS pd_ev, "
        "       extract(epoch FROM decided_at)::float8 AS decided_at "
        "  FROM paper_decisions WHERE account_id = $1 "
        "   AND decided_at >= to_timestamp($2) "
        " ORDER BY decided_at DESC LIMIT $3",
        account_id, now - window_days * C.DAY, LIMIT_DECISIONS)
    out = []
    for r in rows:
        d = _d(r)
        d["refusals"] = list(d.get("refusals") or [])
        d["label"] = C.jload(d.get("label"))
        ev = C.num(d.pop("acq_ev", None))
        pd = C.num(d.pop("pd_ev", None))
        d["recorded_ev_usd"] = ev if ev is not None else pd
        out.append(d)
    return out


async def settlements(conn, *, account_id, now, window_days):
    rows = await conn.fetch(
        "SELECT settlement_id, position_key, version, supersedes, outcome, "
        "       payout_per_contract, qty, "
        "       extract(epoch FROM settled_at)::float8 AS settled_at "
        "  FROM paper_settlements WHERE account_id = $1 "
        "   AND settled_at >= to_timestamp($2) "
        " ORDER BY settled_at DESC LIMIT $3",
        account_id, now - window_days * C.DAY, LIMIT_POSITIONS)
    return [_d(r) for r in rows]


async def capacity(conn, *, account_id, now, window_days):
    rows = await conn.fetch(
        "SELECT c.candidate_id, c.strategy, c.status, c.why, c.edge_at_size,"
        "       c.executable_capacity_usd, c.executable_opportunity_dollars, "
        "       c.capacity_ceiling_usd, c.capacity_ceiling_depth_bound, "
        "       extract(epoch FROM c.decided_at)::float8 AS decided_at "
        "  FROM pos_capacity_latest c JOIN paper_decisions d "
        "    ON d.decision_id = c.candidate_id AND d.account_id = $1 "
        " WHERE c.decided_at >= to_timestamp($2) "
        " ORDER BY c.decided_at DESC LIMIT $3",
        account_id, now - window_days * C.DAY, LIMIT_POSITIONS)
    out = []
    for r in rows:
        d = _d(r)
        d["edge_at_size"] = C.jload(d.get("edge_at_size"))
        out.append(d)
    return out


async def books(conn, *, account_id, now, window_days):
    """A bounded sample of the recorded books of the account's markets."""
    rows = await conn.fetch(
        "SELECT b.obs_id, b.us_market_slug, b.bids, b.offers, b.error, "
        "       b.market_state, "
        "       extract(epoch FROM b.observed_at)::float8 AS observed_at "
        "  FROM paper_book_observations b "
        " WHERE b.us_market_slug IN (SELECT DISTINCT us_market_slug "
        "        FROM paper_decisions WHERE account_id = $1 "
        "         AND decided_at >= to_timestamp($2) LIMIT 500) "
        "   AND b.observed_at >= to_timestamp($2) "
        " ORDER BY b.observed_at DESC LIMIT $3",
        account_id, now - window_days * C.DAY, LIMIT_FILLS)
    return [_d(r) for r in rows]


async def integrity(conn, *, account_id, now, window_days):
    """Counts the data-quality sentinel judges (one row)."""
    lo = now - window_days * C.DAY
    r = await conn.fetchrow(
        "SELECT "
        " (SELECT count(*) FROM paper_fills WHERE account_id=$1 "
        "   AND filled_at >= to_timestamp($2)) AS fills, "
        " (SELECT count(*) FROM paper_fills WHERE account_id=$1 "
        "   AND filled_at >= to_timestamp($2) AND book_obs_id IS NULL) "
        "   AS fills_without_book, "
        " (SELECT count(*) FROM paper_fills WHERE account_id=$1 "
        "   AND filled_at >= to_timestamp($2) AND (price <= 0 OR price >= 1"
        "   OR qty <= 0)) AS fills_out_of_range, "
        " (SELECT count(*) FROM paper_fills WHERE account_id=$1 "
        "   AND filled_at >= to_timestamp($2) AND (fee_usd IS NULL OR "
        "   fee_usd < 0)) AS fills_bad_fee, "
        " (SELECT count(*) FROM paper_fills WHERE account_id=$1 "
        "   AND filled_at >= to_timestamp($2) AND book_observed_at IS NOT "
        "   NULL AND filled_at - book_observed_at > interval '60 seconds') "
        "   AS fills_book_older_than_60s, "
        " (SELECT count(*) FROM paper_decisions WHERE account_id=$1 "
        "   AND decided_at >= to_timestamp($2)) AS decisions, "
        " (SELECT count(*) FROM paper_decisions WHERE account_id=$1 "
        "   AND decided_at >= to_timestamp($2) AND verdict='ENTER' AND "
        "   coalesce(p_blended, p_pinnacle, p_internal) IS NULL) "
        "   AS enters_without_probability, "
        " (SELECT count(*) FROM paper_decisions WHERE account_id=$1 "
        "   AND decided_at >= to_timestamp($2) AND strategy IS NULL) "
        "   AS decisions_without_strategy, "
        " (SELECT count(*) FROM paper_orders WHERE account_id=$1 "
        "   AND created_at >= to_timestamp($2)) AS orders, "
        " (SELECT count(*) FROM paper_orders WHERE account_id=$1 "
        "   AND created_at >= to_timestamp($2) AND terminal_at IS NULL "
        "   AND expires_at < to_timestamp($3) - interval '1 hour') "
        "   AS orders_past_expiry_not_terminal, "
        " (SELECT count(*) FROM paper_orders WHERE account_id=$1 "
        "   AND created_at >= to_timestamp($2) AND filled_qty > qty) "
        "   AS orders_overfilled",
        account_id, lo, now)
    return _d(r)


async def regime(conn, *, account_id, now, window_days):
    r = await conn.fetchrow(
        "SELECT run_id, recommendation, reasons, signals, applied, "
        "       extract(epoch FROM computed_at)::float8 AS computed_at "
        "  FROM intel_regime_states ORDER BY computed_at DESC LIMIT 1")
    return [] if r is None else [_d(r)]


async def attribution(conn, *, account_id, now, window_days):
    rows = await conn.fetch(
        "SELECT DISTINCT ON (a.book, a.subject_id) a.book, a.subject_id, "
        "       a.strategy, a.model_edge_usd, a.execution_edge_usd, "
        "       a.slippage_usd, a.fees_usd, a.management_usd, "
        "       a.settlement_usd, a.outcome_variance_usd, a.realized_pnl_usd,"
        "       a.reconciles, "
        "       extract(epoch FROM a.computed_at)::float8 AS computed_at "
        "  FROM intel_attribution a JOIN paper_decisions d "
        "    ON d.decision_id = a.decision_id AND d.account_id = $1 "
        " WHERE a.book = 'PAPER' AND a.computed_at >= to_timestamp($2) "
        " ORDER BY a.book, a.subject_id, a.computed_at DESC LIMIT $3",
        account_id, now - window_days * C.DAY, LIMIT_POSITIONS)
    return [_d(r) for r in rows]


async def twin(conn, *, account_id, now, window_days):
    rows = await conn.fetch(
        "SELECT result_id, run_id, scenario_id, basis_book, status, "
        "       unavailable_reason, comparison, counts, research_only, "
        "       summed_across_books, "
        "       extract(epoch FROM computed_at)::float8 AS computed_at "
        "  FROM twin_scenario_results WHERE run_id = (SELECT run_id FROM "
        "       twin_scenario_results ORDER BY computed_at DESC LIMIT 1) "
        " ORDER BY scenario_id LIMIT 50")
    return [_d(r) for r in rows]


async def model_tournament(conn, *, account_id, now, window_days):
    r = await conn.fetchrow(
        "SELECT run_id, payload, "
        "       extract(epoch FROM computed_at)::float8 AS computed_at "
        "  FROM poslearn_snapshots WHERE component = 'MODEL_TOURNAMENT' "
        " ORDER BY computed_at DESC LIMIT 1")
    if r is None:
        return []
    d = _d(r)
    d["payload"] = C.jload(d["payload"])
    return [d]


async def experiments(conn, *, account_id, now, window_days):
    rows = await conn.fetch(
        "SELECT experiment_id, hypothesis, primary_metric, stopping_rule, "
        "       min_sample, status, status_reason, result, "
        "       extract(epoch FROM registered_at)::float8 AS registered_at, "
        "       extract(epoch FROM start_at)::float8 AS start_at, "
        "       extract(epoch FROM stop_at)::float8 AS stop_at, "
        "       extract(epoch FROM status_changed_at)::float8 "
        "       AS status_changed_at "
        "  FROM poslearn_experiments ORDER BY registered_at DESC LIMIT $1",
        LIMIT_ROWS)
    out = []
    for r in rows:
        d = _d(r)
        for k in ("primary_metric", "stopping_rule", "result"):
            d[k] = C.jload(d.get(k))
        out.append(d)
    return out


async def improvements(conn, *, account_id, now, window_days):
    """Candidates, trials and releases of the improvement pipeline (one
    list of rows tagged by kind)."""
    out = []
    for r in await conn.fetch(
            "SELECT candidate_id, hypothesis, success_metrics, harm_metrics,"
            "       state, "
            "       extract(epoch FROM created_at)::float8 AS created_at, "
            "       extract(epoch FROM evaluated_at)::float8 AS evaluated_at,"
            "       extract(epoch FROM approved_at)::float8 AS approved_at "
            "  FROM improvement_candidates ORDER BY created_at DESC "
            " LIMIT $1", LIMIT_ROWS):
        d = _d(r)
        d["kind"] = "CANDIDATE"
        d["success_metrics"] = C.jload(d.get("success_metrics"))
        out.append(d)
    for r in await conn.fetch(
            "SELECT trial_id, candidate_id, verdict, evaluation_boundary, "
            "       extract(epoch FROM evaluation_boundary)::float8 "
            "       AS evaluation_boundary_at, "
            "       extract(epoch FROM created_at)::float8 AS created_at "
            "  FROM improvement_trials ORDER BY created_at DESC LIMIT $1",
            LIMIT_ROWS):
        d = _d(r)
        d.pop("evaluation_boundary", None)
        d["kind"] = "TRIAL"
        out.append(d)
    for r in await conn.fetch(
            "SELECT release_id, candidate_id, policy_key, from_version, "
            "       to_version, state, "
            "       extract(epoch FROM released_at)::float8 AS released_at, "
            "       extract(epoch FROM rolled_back_at)::float8 "
            "       AS rolled_back_at "
            "  FROM improvement_releases ORDER BY released_at DESC LIMIT $1",
            LIMIT_ROWS):
        d = _d(r)
        d["kind"] = "RELEASE"
        out.append(d)
    return out


async def activations(conn, *, account_id, now, window_days):
    rows = await conn.fetch(
        "SELECT activation_id, policy_key, kind, version_id, "
        "       previous_version_id, proposal_id, evaluation_id, actor, "
        "       extract(epoch FROM at)::float8 AS at "
        "  FROM paper_policy_parameter_activations "
        " ORDER BY at DESC LIMIT $1", LIMIT_ROWS)
    return [_d(r) for r in rows]


async def incidents(conn, *, account_id, now, window_days):
    rows = await conn.fetch(
        "SELECT finding_id, kind, severity, subject, "
        "       extract(epoch FROM found_at)::float8 AS at "
        "  FROM paper_audrey_findings WHERE account_id = $1 "
        "   AND severity = 'CRITICAL' AND found_at >= to_timestamp($2) "
        " ORDER BY found_at DESC LIMIT $3",
        account_id, now - window_days * C.DAY, LIMIT_ROWS)
    return [_d(r) for r in rows]


async def eddie_outcomes(conn, *, account_id, now, window_days):
    rows = await conn.fetch(
        "SELECT e.outcome_id, e.realized_execution_loss_pp, "
        "       e.predicted_execution_loss_pp, e.naive_execution_loss_pp, "
        "       e.realized_adverse_selection_pp, "
        "       extract(epoch FROM e.measured_at)::float8 AS measured_at "
        "  FROM eddie_execution_outcomes e JOIN paper_decisions d "
        "    ON d.decision_id = e.decision_id AND d.account_id = $1 "
        " WHERE e.measured_at >= to_timestamp($2) "
        " ORDER BY e.measured_at DESC LIMIT $3",
        account_id, now - window_days * C.DAY, LIMIT_ROWS)
    return [_d(r) for r in rows]


async def loops(conn, *, account_id, now, window_days):
    """The runtime loop verdicts (loop_health's own SELECT-only reader)."""
    from .. import loop_health as LH
    got = await LH.read(conn, now=now)
    return [got]


LOADERS = {
    "positions": positions, "fills": fills, "orders": orders,
    "decisions": decisions, "settlements": settlements,
    "capacity": capacity, "books": books, "integrity": integrity,
    "regime": regime, "attribution": attribution, "twin": twin,
    "model_tournament": model_tournament, "experiments": experiments,
    "improvements": improvements, "activations": activations,
    "incidents": incidents, "eddie_outcomes": eddie_outcomes,
    "loops": loops,
}


async def load_all(conn, *, account_id, now, window_days, only=None) -> dict:
    """Every loader, each in its own savepoint. A failure leaves that
    input None and names the error in out["_errors"]."""
    out: dict = {"_errors": {}}
    for name, fn in LOADERS.items():
        if only is not None and name not in only:
            continue
        try:
            async with conn.transaction():
                got = await fn(conn, account_id=account_id, now=now,
                               window_days=window_days)
            out[name] = got
        except Exception as exc:                                # noqa: BLE001
            out[name] = None
            out["_errors"][name] = "%s:%s" % (type(exc).__name__,
                                              json.dumps(str(exc)[:120]))
    return out
