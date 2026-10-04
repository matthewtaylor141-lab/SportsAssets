"""POINT-IN-TIME READS FOR THE RANKER AND THE ALLOCATOR (control plane,
stream C). SELECT only; the caller holds the transaction.

Every read is AS OF a clock and filters each row by its OWN recorded
timestamp (a fill by filled_at AND recorded_at, a settlement by settled_at
AND recorded_at -- its latest version visible at the clock --, a ledger entry
by committed_at, an intent by created_at AND recorded_at, a ranking row by
as_of, and for PRODUCTION_SHADOW also recorded_at). A row recorded after the
clock is invisible; a mutable control row changed after the clock is
UNAVAILABLE for that clock rather than read as if it had always been so.
(Stream E's pit.py is the control plane's one point-in-time accessor; these
filters are the same rule and route through it after integration.)

  positions_at      position truth (red-team item 9): open_qty = bought -
                    sold - settled, cost basis = average cost incl. fees x
                    open_qty -- partial exits, reductions and protection fills
                    netted, exactly as bettor_paper_ledger.positions derives
                    them (pinned equal by a test), but as of the clock
  reservations_at   open BUY reservations: the ledger's reserved_delta per
                    order summed at the clock
  cash_at           cash / reserved / available summed from the ledger
  drawdown_at       peak-to-trough of realized closed-position P&L in release
                    order, over starting cash (REALIZED ONLY: marked-equity
                    drawdown is red-team item 16, not built here)
  session_rules_at  the frozen config of the paper session active at the
                    clock (risk caps, the probability freshness rule, the
                    cadence bound)
  approved_rails    THE APPROVED RAILS AS READ: the session's frozen risk
                    caps through bettor_paper_limits.effective_caps (the
                    owner's current capital policy for the account), its
                    funding boundary and same-contract rule, and the SMALL
                    LIVE per-order rail at its scale (execmirror_control,
                    small_live_control mode). Nothing here sets a limit.
  qualified_tape    the pre-allocation qualified-opportunity tape (red-team
                    item 7): the latest capital efficiency of every distinct
                    opportunity that cleared every gate in a rank run before
                    the clock -- admitted or not, funded or not
  intents_between   canonical decision intents (migration 225) created in
                    (since, clock], with the decision's instrument label

This module imports nothing from the order, venue, execution, funded or live
modules; bettor_paper_limits is the owner's pure capital-policy module (no
I/O, imports only `copy`).
"""
from __future__ import annotations

import json
import math

from .. import bettor_paper_limits as LIMITS
from . import allocator as AL
from . import ranking as RK

TAPE_LOOKBACK_S = 7 * 86400.0     # allie's hurdle window (7 days)
TAPE_LIMIT = 500
INTENT_LIMIT = 2000
MIN_VOL_SAMPLE = 10               # = allie_capital.MIN_HURDLE_SAMPLE

R_NO_SESSION = "NO_PAPER_SESSION_ACTIVE_AT_THE_CLOCK"
R_NO_LEDGER = "NO_PAPER_LEDGER_ENTRY_AT_OR_BEFORE_THE_CLOCK"
R_LIVE_CHANGED = "EXECMIRROR_CONTROL_CHANGED_AFTER_THE_CLOCK"
R_NO_LIVE = "EXECMIRROR_CONTROL_ABSENT"

num = RK.num


def _f(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    return num(v)


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v if v is not None else {}


async def _exists(conn, rel: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    rel))


async def has_schema(conn) -> bool:
    return await _exists(conn, "cp_opportunity_rankings")


# ─────────────────────────── the ledger, as of the clock ────────────────

POSITIONS_AT_SQL = """
    WITH f AS (
        SELECT group_id, us_market_slug, holding_side,
               max(fixture) AS fixture, max(strategy) AS strategy,
               (array_agg(label ORDER BY filled_at))[1] AS label,
               sum(qty) FILTER (WHERE direction='BUY') AS bought,
               sum(gross_usd) FILTER (WHERE direction='BUY') AS buy_gross,
               sum(fee_usd) FILTER (WHERE direction='BUY') AS buy_fees,
               sum(qty) FILTER (WHERE direction='SELL') AS sold,
               sum(gross_usd) FILTER (WHERE direction='SELL') AS sale_gross,
               sum(fee_usd) FILTER (WHERE direction='SELL') AS sale_fees,
               min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
          FROM paper_fills
         WHERE account_id = $1 AND filled_at <= to_timestamp($2)
           AND recorded_at <= to_timestamp($2)
         GROUP BY group_id, us_market_slug, holding_side),
    s AS (
        SELECT DISTINCT ON (position_key) position_key, qty, payout_usd,
               settled_at
          FROM paper_settlements
         WHERE account_id = $1 AND settled_at <= to_timestamp($2)
           AND recorded_at <= to_timestamp($2)
         ORDER BY position_key, version DESC)
    SELECT f.*, s.qty AS settled_qty, s.payout_usd, s.settled_at
      FROM f LEFT JOIN s ON s.position_key =
           'paperpos:' || $1 || ':' || f.group_id || ':' || f.us_market_slug
           || ':' || f.holding_side
     ORDER BY f.group_id, f.us_market_slug, f.holding_side
"""

SPORT_AT_SQL = """
    SELECT DISTINCT ON (o.group_id) o.group_id,
           c.contract->>'sport_family' AS sport
      FROM paper_orders o
      JOIN canonical_decision_intents c ON c.decision_id = o.decision_id
     WHERE o.account_id = $1 AND o.role = 'ENTRY'
       AND c.created_at <= to_timestamp($2)
       AND c.recorded_at <= to_timestamp($2)
     ORDER BY o.group_id, o.created_at
"""


def position_from_row(r) -> dict:
    """The ledger's own derivation (bettor_paper_ledger._position_from),
    restated over the as-of rows."""
    bought = float(r["bought"] or 0)
    buy_cost = float(r["buy_gross"] or 0) + float(r["buy_fees"] or 0)
    sold = float(r["sold"] or 0)
    proceeds = float(r["sale_gross"] or 0) - float(r["sale_fees"] or 0)
    settled = float(r["settled_qty"] or 0)
    payout = float(r["payout_usd"] or 0)
    avg = buy_cost / bought if bought > 0 else 0.0
    open_qty = bought - sold - settled
    realized = (proceeds - avg * sold) + (payout - avg * settled
                                          if settled > 0 else 0.0)
    ends = [t for t in (_f(r["last_fill_at"]), _f(r["settled_at"]))
            if t is not None]
    return {"group_id": r["group_id"], "us_market_slug": r["us_market_slug"],
            "holding_side": r["holding_side"], "fixture": r["fixture"],
            "strategy": r["strategy"], "label": _j(r["label"]),
            "bought_qty": bought, "sold_qty": sold, "settled_qty": settled,
            "open_qty": open_qty, "acquisition_cost_usd": buy_cost,
            "cost_basis_usd": avg * open_qty, "realized_pnl_usd": realized,
            "first_fill_at": _f(r["first_fill_at"]),
            "released_at": max(ends) if ends and open_qty <= 1e-9 else None}


async def positions_at(conn, account_id: str, clock: float, *,
                       include_closed: bool = False) -> list:
    rows = [position_from_row(r) for r in await conn.fetch(
        POSITIONS_AT_SQL, account_id, float(clock))]
    if rows and await _exists(conn, "canonical_decision_intents"):
        sport = {r["group_id"]: r["sport"] for r in await conn.fetch(
            SPORT_AT_SQL, account_id, float(clock))}
        for p in rows:
            p["sport"] = sport.get(p["group_id"])
    return rows if include_closed else [p for p in rows
                                        if p["open_qty"] > 1e-9]


async def reservations_at(conn, account_id: str, clock: float) -> list:
    rows = await conn.fetch(
        """SELECT o.order_id, o.group_id, o.us_market_slug, o.holding_side,
                  o.fixture, o.label, o.strategy,
                  sum(l.reserved_delta_usd) AS reserved
             FROM paper_ledger l
             JOIN paper_orders o ON o.order_id = l.order_id
            WHERE l.account_id = $1 AND l.committed_at <= to_timestamp($2)
              AND o.direction = 'BUY' AND o.created_at <= to_timestamp($2)
            GROUP BY o.order_id, o.group_id, o.us_market_slug,
                     o.holding_side, o.fixture, o.label, o.strategy
           HAVING sum(l.reserved_delta_usd) > 0
            ORDER BY o.order_id""", account_id, float(clock))
    return [{"order_id": r["order_id"], "group_id": r["group_id"],
             "us_market_slug": r["us_market_slug"],
             "holding_side": r["holding_side"], "fixture": r["fixture"],
             "label": _j(r["label"]), "strategy": r["strategy"],
             "reserved_usd": float(r["reserved"])} for r in rows]


async def cash_at(conn, account_id: str, clock: float) -> dict | None:
    r = await conn.fetchrow(
        """SELECT coalesce(sum(cash_delta_usd), 0) AS cash,
                  coalesce(sum(reserved_delta_usd), 0) AS reserved,
                  count(*) AS n
             FROM paper_ledger
            WHERE account_id = $1 AND committed_at <= to_timestamp($2)""",
        account_id, float(clock))
    if not r or not r["n"]:
        return None
    cash, res = float(r["cash"]), float(r["reserved"])
    return {"cash_usd": cash, "reserved_usd": res,
            "available_usd": cash - res, "entries": int(r["n"])}


async def drawdown_at(conn, account_id: str, clock: float, *,
                      positions: list | None = None) -> tuple:
    """(drawdown fraction, basis, per-strategy realized per-$ volatility)."""
    from ..profitability import common as PC
    pos = positions if positions is not None else await positions_at(
        conn, account_id, clock, include_closed=True)
    start = await conn.fetchval(
        "SELECT starting_cash_usd FROM paper_accounts WHERE account_id = $1",
        account_id)
    closed = sorted((p for p in pos if p.get("released_at") is not None
                     and p["released_at"] <= clock),
                    key=lambda p: (p["released_at"], p["group_id"]))
    vol: dict = {}
    by: dict = {}
    for p in closed:
        if (p.get("acquisition_cost_usd") or 0) > 0:
            by.setdefault(str(p.get("strategy")), []).append(
                p["realized_pnl_usd"] / p["acquisition_cost_usd"])
    for s, xs in by.items():
        if len(xs) >= MIN_VOL_SAMPLE:
            m = sum(xs) / len(xs)
            vol[s] = math.sqrt(sum((x - m) ** 2 for x in xs)
                               / (len(xs) - 1))
    if not start or float(start) <= 0:
        return None, "NO_STARTING_CASH", vol
    dd = PC.max_drawdown([p["realized_pnl_usd"] for p in closed])
    return (dd / float(start),
            "REALIZED_CLOSED_POSITIONS_ONLY over %d releases (marked-equity "
            "drawdown is red-team item 16)" % len(closed), vol)


async def portfolio_at(conn, account_id: str, clock: float) -> dict | None:
    """THE PORTFOLIO AS OF THE CLOCK from position / ledger truth, or None
    when the ledger has no entry at the clock."""
    cash = await cash_at(conn, account_id, clock)
    if cash is None:
        return None
    allp = await positions_at(conn, account_id, clock, include_closed=True)
    res = await reservations_at(conn, account_id, clock)
    dd, dd_basis, vol = await drawdown_at(conn, account_id, clock,
                                          positions=allp)
    return AL.portfolio_from_ledger(
        positions=[p for p in allp if p["open_qty"] > 1e-9],
        reservations=res, cash=cash, clock=clock, account_id=account_id,
        drawdown_fraction=dd, drawdown_basis=dd_basis,
        realized_volatility=vol)


# ─────────────────────────── the frozen session and the rails ───────────

async def session_rules_at(conn, account_id: str, clock: float) -> dict | None:
    r = await conn.fetchrow(
        """SELECT session_id, config, config_sha,
                  extract(epoch FROM started_at)::float8 AS started_at
             FROM paper_sessions
            WHERE account_id = $1 AND started_at <= to_timestamp($2)
              AND (stopped_at IS NULL OR stopped_at > to_timestamp($2))
            ORDER BY started_at DESC LIMIT 1""", account_id, float(clock))
    if r is None:
        return None
    cfg = _j(r["config"])
    return {"session_id": r["session_id"], "config_sha": r["config_sha"],
            "started_at": r["started_at"], "config": cfg,
            "probability_max_age_s": num((cfg.get("entry") or {}).get(
                "pinnacle_max_age_s")),
            "max_decisions_per_pass": num((cfg.get("cadence") or {}).get(
                "max_decisions_per_pass"))}


async def approved_rails(conn, account_id: str, clock: float, *,
                         rules: dict | None = None) -> dict:
    """THE APPROVED RAILS AS READ (never invented). status READ, or
    UNAVAILABLE with the reason (the allocator then funds nothing)."""
    rules = rules if rules is not None else await session_rules_at(
        conn, account_id, clock)
    if rules is None:
        return {"status": "UNAVAILABLE", "why": R_NO_SESSION,
                "rails_sha": None}
    risk = dict((rules["config"] or {}).get("risk") or {})
    eff = LIMITS.effective_caps(risk, account_id, "ENTRY")
    policy = LIMITS.describe(account_id) or {}
    paper = {
        "per_order_cap_usd": num(eff.get("per_order_cap_usd")),
        "per_market_cap_usd": num(eff.get("per_market_cap_usd")),
        "per_fixture_cap_usd": num(eff.get("per_fixture_cap_usd")),
        "aggregate_exposure_cap_usd": num(
            policy.get("aggregate_exposure_cap_usd")
            if "aggregate_exposure_cap_usd" in policy
            else eff.get("aggregate_exposure_cap_usd")),
        "max_concurrent_groups": num(eff.get("max_concurrent_groups")),
        "hedge_reserve_fraction": num(eff.get("hedge_reserve_fraction")),
        "same_strategy_same_contract": policy.get(
            "same_strategy_same_contract"),
        "funding_boundary": policy.get("funding_boundary")
        or "AVAILABLE_SIMULATED_CASH"}
    psrc = ("paper_sessions[%s].config.risk (frozen, config_sha %s) through "
            "bettor_paper_limits.effective_caps%s" % (
                rules["session_id"], rules["config_sha"],
                " (owner capital policy %s)" % policy.get("version")
                if policy else ""))
    live: dict = {"status": "UNAVAILABLE", "why": R_NO_LIVE}
    if await _exists(conn, "execmirror_control"):
        em = await conn.fetchrow(
            """SELECT scale, max_order_usd, revision,
                      extract(epoch FROM updated_at)::float8 AS updated_at
                 FROM execmirror_control WHERE id = 1""")
        mode = None
        if await _exists(conn, "small_live_control"):
            mode = await conn.fetchval(
                "SELECT mode FROM small_live_control LIMIT 1")
        if em is None:
            live = {"status": "UNAVAILABLE", "why": R_NO_LIVE}
        elif em["updated_at"] is not None and em["updated_at"] > clock:
            live = {"status": "UNAVAILABLE", "why": R_LIVE_CHANGED}
        else:
            live = {"status": "READ", "scale": float(em["scale"]),
                    "max_order_usd": float(em["max_order_usd"]),
                    "revision": em["revision"], "mode": mode or "SHADOW",
                    "source": "execmirror_control (revision %s); "
                              "small_live_control.mode %s"
                              % (em["revision"], mode)}
    body = {"account_id": account_id, "paper": paper, "live": live,
            "session_config_sha": rules["config_sha"],
            "policy_version": policy.get("version")}
    return {"status": "READ", "why": None, "account_id": account_id,
            "as_of": float(clock), "paper": paper, "live": live,
            "sources": {"paper": psrc,
                        "AVAILABLE_CASH": "paper_ledger summed at the clock",
                        "SAME_STRATEGY_SAME_CONTRACT":
                            "bettor_paper_limits.describe(%s)" % account_id},
            "session_id": rules["session_id"],
            "rails_sha": RK.sha_of(body),
            "label": "APPROVED_RAILS_AS_READ"}


# ─────────────────────────── the tape and the candidates ────────────────

async def qualified_tape(conn, clock: float, *, source: str,
                         lookback_s: float = TAPE_LOOKBACK_S,
                         limit: int = TAPE_LIMIT) -> list:
    if not await has_schema(conn):
        return []
    rows = await conn.fetch(
        """SELECT ce FROM (
               SELECT DISTINCT ON (opportunity_id) opportunity_id,
                      capital_efficiency_component::float8 AS ce, as_of
                 FROM cp_opportunity_rankings
                WHERE source = $1 AND gates_passed
                  AND as_of < to_timestamp($2)
                  AND as_of >= to_timestamp($2 - $3)
                  AND ($1 <> 'PRODUCTION_SHADOW'
                       OR recorded_at <= to_timestamp($2))
                ORDER BY opportunity_id, as_of DESC, ranking_id) t
            ORDER BY as_of DESC, opportunity_id LIMIT $4""",
        source, float(clock), float(lookback_s), int(limit))
    return [r["ce"] for r in rows if r["ce"] is not None]


async def intents_between(conn, since: float, clock: float, *,
                          limit: int = INTENT_LIMIT) -> list:
    if not await _exists(conn, "canonical_decision_intents"):
        return []
    rows = await conn.fetch(
        """SELECT c.intent_id, c.intent_version, c.decision_id, c.strategy,
                  c.strategy_version, c.sleeve, c.evidence,
                  c.opportunity_score, c.derek, c.karen, c.allie, c.eddie,
                  c.venue, c.us_market_slug, c.contract, c.holding_side,
                  c.limit_price, c.wire_price, c.target_qty, c.sizing_basis,
                  extract(epoch FROM c.created_at)::float8 AS created_at,
                  extract(epoch FROM c.recorded_at)::float8 AS recorded_at,
                  c.content_sha, d.label
             FROM canonical_decision_intents c
             LEFT JOIN paper_decisions d
               ON d.decision_id = c.decision_id
              AND d.recorded_at <= to_timestamp($2)
            WHERE c.created_at > to_timestamp($1)
              AND c.created_at <= to_timestamp($2)
              AND c.recorded_at <= to_timestamp($2)
            ORDER BY c.created_at, c.intent_id LIMIT $3""",
        float(since), float(clock), int(limit))
    out = []
    for r in rows:
        d = dict(r)
        for k in ("evidence", "opportunity_score", "derek", "karen", "allie",
                  "eddie", "contract", "sizing_basis", "label"):
            d[k] = _j(d[k])
        for k in ("limit_price", "wire_price", "target_qty"):
            d[k] = num(d[k])
        out.append(d)
    return out


# ─────────────────────────── the reads the API answers ──────────────────

async def latest_run(conn, *, source: str | None = None) -> dict | None:
    r = await conn.fetchrow(
        """SELECT rank_run_id, extract(epoch FROM max(as_of))::float8 AS as_of
             FROM cp_opportunity_rankings
            WHERE ($1::text IS NULL OR source = $1)
            GROUP BY rank_run_id
            ORDER BY max(as_of) DESC, rank_run_id DESC LIMIT 1""", source)
    return None if r is None else dict(r)


async def run_rankings(conn, run_id: str, *, limit: int) -> list:
    rows = await conn.fetch(
        """SELECT ranking_id, rank_run_id,
                  extract(epoch FROM as_of)::float8 AS as_of, opportunity_id,
                  snapshot_id, meta_decision_id, strategy, strategy_class,
                  agent, rank, tier, raw_ev::float8, net_ev::float8,
                  confidence_component::float8,
                  execution_component::float8, liquidity_component::float8,
                  capital_efficiency_component::float8,
                  risk_component::float8, correlation_component::float8,
                  final_priority::float8, components, gates_passed,
                  gate_failures, admitted, admission_basis,
                  starvation_credit, admission, ranker_version, config_sha,
                  code_sha, model_versions, source, authority,
                  extract(epoch FROM recorded_at)::float8 AS recorded_at
             FROM cp_opportunity_rankings
            WHERE rank_run_id = $1 ORDER BY rank LIMIT $2""",
        run_id, int(limit))
    out = []
    for r in rows:
        d = dict(r)
        for k in ("components", "admission", "model_versions"):
            d[k] = _j(d[k])
        d["gate_failures"] = list(d["gate_failures"] or [])
        out.append(d)
    return out


async def run_allocations(conn, run_id: str, *, limit: int) -> list:
    rows = await conn.fetch(
        """SELECT allocation_id, ranking_id, rank_run_id,
                  extract(epoch FROM as_of)::float8 AS as_of, opportunity_id,
                  snapshot_id, meta_decision_id, strategy, rank,
                  requested_size::float8, approved_size::float8,
                  approved_qty::float8, size_limiting_factor,
                  size_limiting_kind, size_limiting_why, caps,
                  portfolio_exposure_after, kelly_fraction_used::float8,
                  uncertainty_haircuts, capital_efficiency,
                  expected_log_utility_gain::float8, allie, allie_basis,
                  live_lane, lane, allocator_version, config_sha, rails_sha,
                  code_sha, model_versions, source, authority,
                  extract(epoch FROM recorded_at)::float8 AS recorded_at
             FROM cp_capital_allocations
            WHERE rank_run_id = $1 ORDER BY rank LIMIT $2""",
        run_id, int(limit))
    out = []
    for r in rows:
        d = dict(r)
        for k in ("caps", "portfolio_exposure_after", "uncertainty_haircuts",
                  "capital_efficiency", "allie", "live_lane",
                  "model_versions"):
            d[k] = _j(d[k])
        out.append(d)
    return out
