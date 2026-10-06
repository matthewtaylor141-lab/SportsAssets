"""Read-only evidence collectors for the Capital Readiness Lab (RESEARCH /
SHADOW_NO_AUTHORITY).

Every function here READS existing BETTOR evidence and returns plain dicts.
None writes, and none can place, cancel, size, approve or route anything:
persistence is the observer's job, through runner.py only. Readers of other
BETTOR layers are imported lazily inside the collector that needs them, and
the observer runs every collector inside one transaction it rolls back, so
a collector that tried to write would fail closed instead of writing.

THE FOUR FEEDS (sources, all already persisted by the paper runtime):

  1 DECISION SHADOW COURT  paper_profitability_evaluations (migration 309):
    the bind's per-entry all-in EV, fill probability, hold, learned adverse
    selection and residual haircut AS RECORDED AT THE DECISION, plus the
    newest profitability model fitted AT OR BEFORE the decision (never a
    later one) for the epistemic inputs. CASH is always an alternative.
    Outcomes: paper fills held to settlement (ENTER), the 305 shadow
    counterfactual outcome (refused trades), else the decision-time terms
    at the settled payout.
  2 AGENT ECONOMICS       DEREK discovery (settled ENTRY fills vs the market
    price the bind recorded at the decision), XAVIER management (his
    EXIT / REDUCE / STANDING_PROTECTION sale proceeds vs holding the sold
    quantity to settlement), CAPITAL_AUTHORITY prevented loss (305 shadow
    counterfactual outcomes of refused entries). ARCHER, AUDREY, KAREN,
    ALLIE, ADRIANA and SCOUT have no measurable dollar source in the
    persisted paper evidence and are reported UNAVAILABLE, never invented.
  3 SCALE TWIN INPUTS     pos_capacity (migration 216, MEASURED rows only)
    and the 309 bind's capacity frontier recorded on each evaluation, with
    the entered evaluations' expected all-in EV as the base economics.
  4 READINESS GATES       the existing in-process readers named per gate.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics
from typing import Any, Awaitable, Callable

from . import common as C
from . import epistemic as E
from . import readiness as R

VERSION = "CAPITAL_READINESS_FEEDS_V1"

DEREK_STRATEGY_PREFIX = "DEREK"
XAVIER_ROLES = ("EXIT", "REDUCE", "STANDING_PROTECTION")
REFUSAL_GATE_AGENT = "CAPITAL_AUTHORITY"
COURT_PREFIX = "ppe:"

COURT_WINDOW_S = 7 * 86400.0
AGENT_WINDOW_S = 30 * 86400.0
SCALE_WINDOW_S = 86400.0
COURT_BATCH = 500
SCORE_BATCH = 500
AGENT_BATCH = 2000
FRESHNESS_TARGET = 0.95
MIRROR_HEARTBEAT_MAX_AGE_S = 900.0
BIND_MODEL_MAX_AGE_S = 3600.0
FIRST_LOSS_WINDOW_S = 3600.0

UNAVAILABLE_AGENTS = {
    "ARCHER": ("NO_RECORDED_EXPECTED_FILL_COST_PER_ORDER: paper fills are "
               "the simulator's; evaluations record the best price, not an "
               "expected walked cost, so expected-vs-realized execution "
               "cost per order is not measurable"),
    "AUDREY": ("NO_AUDREY_ATTRIBUTED_REFUSAL_WITH_A_COUNTERFACTUAL: the 305 "
               "shadow counterfactuals record capital-authority refusals "
               "(lifecycle / stopping rule / forward economics), not "
               "Audrey's; they are credited to CAPITAL_AUTHORITY"),
    "KAREN": ("NO_DOLLAR_VALUED_BLOCK: karen_challenges record false-block "
              "booleans, never a counterfactual P&L"),
    "ALLIE": "NO_ALLOCATION_COUNTERFACTUAL_PERSISTED",
    "ADRIANA": "NO_REALIZED_ARBITRAGE_OUTCOME_PERSISTED",
    "SCOUT": "NO_FEATURE_LIFT_ATTRIBUTION_PERSISTED",
}


# ── small helpers ────────────────────────────────────────────────────

def _num(v):
    return C.num(v)


def _epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    return _num(v)


def _ts(epoch):
    return dt.datetime.fromtimestamp(float(epoch), dt.timezone.utc)


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


async def _has(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


async def _guard(conn, fn):
    """Run one reader in its own savepoint: (value, None) or (None, why)."""
    try:
        async with conn.transaction():
            return await fn(), None
    except Exception as exc:                                    # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:160])


def position_key(account_id, group_id, slug, side) -> str:
    # the paper ledger's own key format (bettor_paper_ledger.position_key)
    return "paperpos:%s:%s:%s:%s" % (account_id, group_id, slug, side)


# ═════════════════════════════════════════════════════════════════════
# SETTLED PAYOUTS (authoritative settlement evidence only)
# ═════════════════════════════════════════════════════════════════════

async def settled_payouts(conn, account_id: str, keys) -> dict:
    """{(slug, side): {y, basis, settled_at}} for contracts with a settled
    payout per contract: the newest paper_settlements version of the
    account (a VOID refund is not an outcome), else a WON / LOST outcome of
    the venue-joined valuation rows (bettor_paper_profitability_bind.
    outcomes). Unsettled contracts are absent."""
    keys = {(s, h) for s, h in keys if s and h}
    if not keys:
        return {}
    slugs = sorted({s for s, _ in keys})
    out: dict = {}
    for r in await conn.fetch(
            "SELECT DISTINCT ON (position_key) position_key, us_market_slug,"
            "       holding_side, outcome, payout_per_contract, settled_at "
            "  FROM paper_settlements WHERE account_id = $1 "
            "   AND us_market_slug = ANY($2::text[]) "
            " ORDER BY position_key, version DESC", account_id, slugs):
        k = (r["us_market_slug"], r["holding_side"])
        if k not in keys or r["outcome"] == "VOID_REFUND":
            continue
        y = _num(r["payout_per_contract"])
        if y is None:
            continue
        out.setdefault(k, {"y": y, "basis": "PAPER_SETTLEMENT",
                           "outcome": r["outcome"],
                           "settled_at": _epoch(r["settled_at"])})
    missing = [s for s in slugs
               if any((s, h) in keys and (s, h) not in out
                      for h in ("LONG", "SHORT"))]
    if missing and await _has(conn, "external_valuations"):
        from .. import bettor_paper_profitability_bind as PB
        got, _why = await _guard(conn, lambda: PB.outcomes(conn, missing))
        for k, y in (got or {}).items():
            if k in keys and k not in out:
                out[k] = {"y": float(y), "basis": "VALUATION_OUTCOME",
                          "outcome": "WON" if y >= 1 else "LOST",
                          "settled_at": None}
    return out


# ═════════════════════════════════════════════════════════════════════
# 1 · THE DECISION SHADOW COURT
# ═════════════════════════════════════════════════════════════════════

def calibration_slope(cell: dict | None):
    """Weighted least-squares slope of observed on predicted across the
    cell's probability bins (weights = bin n). None with fewer than two
    distinct bins."""
    bins = list(((cell or {}).get("bins") or {}).values())
    pts = [(float(b["mean_p"]), float(b["observed"]), float(b["n"]))
           for b in bins if _num(b.get("mean_p")) is not None
           and _num(b.get("observed")) is not None and (_num(b.get("n"))
                                                        or 0) > 0]
    w = sum(n for _, _, n in pts)
    if len(pts) < 2 or w <= 0:
        return None
    mx = sum(x * n for x, _, n in pts) / w
    my = sum(y * n for _, y, n in pts) / w
    sxx = sum(n * (x - mx) ** 2 for x, _, n in pts)
    if sxx <= 1e-12:
        return None
    return sum(n * (x - mx) * (y - my) for x, y, n in pts) / sxx


def _cell_key(sport, family, regime) -> str:
    # bettor_paper_profitability_bind.cell_key, without importing it here
    return "%s|%s|%s" % (sport or "UNKNOWN", family or "UNKNOWN",
                         regime or "UNKNOWN")


def evaluation_epistemic(e: dict, cal: dict | None, exe: dict | None):
    """The epistemic confidence of one evaluation from the models fitted at
    or before it (fail-closed: missing evidence => factor 0)."""
    cells = (cal or {}).get("cells") or {}
    key = _cell_key(e.get("sport"), e.get("market_family"), e.get("regime"))
    cell = cells.get(key) or {}
    rows = ((exe or {}).get("by_strategy_style") or {}).values()
    exe_n = sum(int(r.get("fills") or 0) for r in rows
                if str(r.get("strategy")) == str(e.get("strategy")))
    regime = e.get("regime")
    return E.confidence(
        calibration_n=int(cell.get("n") or 0), execution_n=exe_n,
        brier=_num(cell.get("brier")),
        calibration_slope=calibration_slope(cell),
        regime_novelty=0.0 if regime in (None, "UNKNOWN") else 1.0,
        ood_score=1.0, data_quality=1.0)


def entry_terms(e: dict) -> dict:
    """The ENTER alternative's economics exactly as recorded at the
    decision (no later value is read)."""
    d = _j(e.get("detail")) or {}
    bind = d.get("bind") or {}
    cap = d.get("capacity") or {}
    p = _num(e.get("p_used"))
    ev = _num(e.get("all_in_ev_usd"))
    pf = _num(e.get("fill_probability"))
    if e.get("verdict") == "ENTER":
        q = _num(e.get("qty_out"))
    else:
        q = _num(bind.get("qty")) or None
        q = q if q and q > 0 else _num(e.get("qty_in"))
    a = max(0.0, _num(e.get("learned_adverse_per_contract")) or 0.0)
    h = max(0.0, _num(e.get("residual_haircut_per_contract")) or 0.0)
    mkt = _num(e.get("market_price"))
    capital = None
    if None not in (q, p, ev) and q > 0:
        # cost + fees = q p_used - EV - (adverse + haircut) q; the learned
        # adverse is <= the charged one, so this is an upper bound on the
        # capital (conservative: it can only lower EV per capital-hour)
        capital = q * p - ev - (a + h) * q
        if capital <= 0 and mkt is not None:
            capital = q * mkt
        if capital is not None and capital <= 0:
            capital = None
    per = None if capital is None or not q else capital / q
    cap_qty = _num(cap.get("qty"))
    return {
        "qty": q, "p_used": p, "market_price": mkt,
        "ev_given_fill_usd": ev, "fill_probability": pf,
        "expected_net_usd": None if ev is None or pf is None else ev * pf,
        "capital_usd": capital,
        "expected_hold_hours": _num(e.get("expected_hold_hours")),
        "uncertainty_sigma_usd": (None if q is None or p is None else
                                  q * math.sqrt(max(0.0, p * (1.0 - p)))),
        "capacity_qty": cap_qty,
        "capacity_usd": (None if cap_qty is None or per is None
                         else cap_qty * per)}


def court_alternatives(e: dict, cal: dict | None, exe: dict | None):
    """(chosen, alternatives) for one evaluation; CASH always present."""
    t = entry_terms(e)
    epi = evaluation_epistemic(e, cal, exe)
    frozen = {"eval_id": e.get("eval_id"), "stage": e.get("stage"),
              "strategy": e.get("strategy"), "verdict": e.get("verdict"),
              "refusal": e.get("refusal"),
              "evaluated_at": _epoch(e.get("evaluated_at")),
              "us_market_slug": e.get("us_market_slug"),
              "holding_side": e.get("holding_side"),
              "calibration_model_id": (cal or {}).get("model_id"),
              "execution_model_id": (exe or {}).get("model_id"),
              "basis": "VALUES_RECORDED_AT_THE_DECISION_ONLY"}
    enter = {"name": "ENTER",
             "expected_net_usd": t["expected_net_usd"],
             "capital_usd": t["capital_usd"],
             "expected_hold_hours": t["expected_hold_hours"],
             "uncertainty_sigma_usd": t["uncertainty_sigma_usd"],
             "capacity_usd": t["capacity_usd"],
             "epistemic": {"confidence_factor": epi["confidence_factor"],
                           "status": epi["status"],
                           "components": epi["components"]},
             "terms": t, "frozen_at_decision": frozen}
    cash = {"name": "CASH", "frozen_at_decision": frozen}
    chosen = "ENTER" if e.get("verdict") == "ENTER" else "CASH"
    return chosen, [enter, cash]


EVAL_COLUMNS = (
    "e.eval_id, e.account_id, e.strategy, e.stage, e.decision_id, "
    "e.order_key, e.us_market_slug, e.holding_side, e.fixture, e.sport, "
    "e.market_family, e.regime, e.p_raw, e.p_used, e.market_price, "
    "e.qty_in, e.qty_out, e.all_in_ev_usd, e.ev_per_capital_hour, "
    "e.expected_hold_hours, e.residual_haircut_per_contract, "
    "e.learned_adverse_per_contract, e.fill_probability, e.verdict, "
    "e.refusal, e.detail, e.evaluated_at")


class _Models:
    """The newest model of a kind fitted AT OR BEFORE an instant (cached
    by model id; payloads fetched once)."""

    def __init__(self, conn, account_id):
        self.conn, self.acct, self.payloads = conn, account_id, {}

    async def at(self, kind: str, at) -> dict | None:
        mid = await self.conn.fetchval(
            "SELECT model_id FROM paper_profitability_models "
            " WHERE account_id = $1 AND kind = $2 AND fitted_at <= $3 "
            " ORDER BY fitted_at DESC, model_id DESC LIMIT 1",
            self.acct, kind, at)
        if mid is None:
            return None
        if mid not in self.payloads:
            p = await self.conn.fetchval(
                "SELECT payload FROM paper_profitability_models "
                " WHERE model_id = $1", mid)
            self.payloads[mid] = dict(_j(p) or {}, model_id=mid)
        return self.payloads[mid]


async def court_judgements(conn, account_id: str, *, now: float,
                           window_s: float = COURT_WINDOW_S,
                           limit: int = COURT_BATCH) -> dict:
    """Courts for evaluations not yet judged (one per evaluation)."""
    from . import shadow_court as SC
    if not (await _has(conn, "paper_profitability_evaluations")
            and await _has(conn, "paper_profitability_models")):
        return {"status": "UNAVAILABLE", "why": "MIGRATION_309_NOT_APPLIED",
                "courts": []}
    rows = [dict(r) for r in await conn.fetch(
        "SELECT " + EVAL_COLUMNS + " FROM paper_profitability_evaluations e"
        " WHERE e.account_id = $1 AND e.evaluated_at >= $2 "
        "   AND NOT EXISTS (SELECT 1 FROM capital_readiness_shadow_court c "
        "                    WHERE c.decision_id = $3 || e.eval_id::text) "
        " ORDER BY e.eval_id LIMIT $4", account_id, _ts(now - window_s),
        COURT_PREFIX, int(limit))]
    models = _Models(conn, account_id)
    courts = []
    for e in rows:
        cal = await models.at("CALIBRATION", e["evaluated_at"])
        exe = await models.at("EXECUTION", e["evaluated_at"])
        chosen, alts = court_alternatives(e, cal, exe)
        court = SC.judge(decision_id=COURT_PREFIX + str(e["eval_id"]),
                         chosen=chosen, alternatives=alts)
        courts.append({"court": court,
                       "decided_at": _epoch(e["evaluated_at"])})
    return {"status": "OK", "why": None, "courts": courts,
            "candidates": len(rows)}


async def _entry_fills(conn, account_id, e: dict):
    """(qty, cost incl. fees) of the ENTRY BUY fills of the order this
    evaluation bound, or None when no such order exists."""
    key, did = e.get("order_key"), e.get("decision_id")
    r = None
    if key:
        r = await conn.fetchrow(
            "SELECT count(DISTINCT o.order_id) AS n, "
            "       coalesce(sum(f.qty), 0) AS q, "
            "       coalesce(sum(f.gross_usd + f.fee_usd), 0) AS cost "
            "  FROM paper_orders o LEFT JOIN paper_fills f "
            "    ON f.order_id = o.order_id "
            " WHERE o.account_id = $1 AND o.idempotency_key = $2 "
            "   AND o.role = 'ENTRY' AND o.direction = 'BUY'",
            account_id, key)
    if (r is None or not r["n"]) and did:
        r = await conn.fetchrow(
            "SELECT count(DISTINCT o.order_id) AS n, "
            "       coalesce(sum(f.qty), 0) AS q, "
            "       coalesce(sum(f.gross_usd + f.fee_usd), 0) AS cost "
            "  FROM paper_orders o LEFT JOIN paper_fills f "
            "    ON f.order_id = o.order_id "
            " WHERE o.account_id = $1 AND o.decision_id = $2 "
            "   AND o.strategy = $3 AND o.role = 'ENTRY' "
            "   AND o.direction = 'BUY'", account_id, did, e.get("strategy"))
    if r is None or not r["n"]:
        return None
    return float(r["q"]), float(r["cost"])


async def _counterfactual(conn, account_id, e: dict):
    if not await _has(conn, "paper_shadow_counterfactual_outcomes"):
        return None
    key, did = e.get("order_key"), e.get("decision_id")
    if not key and not did:
        return None
    return await conn.fetchrow(
        "SELECT s.shadow_key, o.outcome, o.filled_qty, "
        "       o.counterfactual_pnl_usd, o.settled_at "
        "  FROM paper_shadow_counterfactuals s "
        "  JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id) "
        " WHERE s.account_id = $1 AND s.strategy = $2 "
        "   AND ((s.order_key IS NOT NULL AND s.order_key = $3) "
        "        OR (s.decision_id IS NOT NULL AND s.decision_id = $4)) "
        " ORDER BY s.shadow_id LIMIT 1", account_id, e.get("strategy"),
        key, did)


async def realized_for(conn, account_id: str, e: dict, payouts: dict):
    """{"ENTER": usd, "CASH": 0.0, basis} for one evaluation, or None while
    the decision has not settled."""
    if e.get("verdict") != "ENTER":
        cf = await _counterfactual(conn, account_id, e)
        if cf is not None:
            if cf["outcome"] == "VOID_REFUND":
                return None
            pnl = (0.0 if cf["outcome"] == "NO_FILL"
                   else float(cf["counterfactual_pnl_usd"]))
            return {"ENTER": pnl, "CASH": 0.0,
                    "basis": "SHADOW_COUNTERFACTUAL_OUTCOME_305",
                    "settled_at": _epoch(cf["settled_at"])}
    y = payouts.get((e.get("us_market_slug"), e.get("holding_side")))
    if y is None:
        return None
    if e.get("verdict") == "ENTER":
        got = await _entry_fills(conn, account_id, e)
        if got is not None:
            q, cost = got
            return {"ENTER": q * y["y"] - cost, "CASH": 0.0,
                    "basis": "PAPER_ENTRY_FILLS_HELD_TO_SETTLEMENT",
                    "settled_at": y.get("settled_at")}
    t = entry_terms(e)
    if t["qty"] is None or t["capital_usd"] is None:
        return None
    return {"ENTER": t["qty"] * y["y"] - t["capital_usd"], "CASH": 0.0,
            "basis": "DECISION_TIME_TERMS_AT_THE_SETTLED_PAYOUT",
            "settled_at": y.get("settled_at")}


async def court_outcomes(conn, account_id: str, *,
                         limit: int = SCORE_BATCH) -> dict:
    """Scores for judged, not yet scored courts whose decision settled.
    Each becomes a NEW court row carrying the result (never an update)."""
    from . import shadow_court as SC
    rows = [dict(r) for r in await conn.fetch(
        "SELECT c.decision_id, c.chosen, c.shadow_winner, c.disagreement, "
        "       c.alternatives, c.decided_at "
        "  FROM capital_readiness_shadow_court c "
        " WHERE c.result IS NULL AND c.decision_id LIKE $1 "
        "   AND NOT EXISTS (SELECT 1 FROM capital_readiness_shadow_court s "
        "                    WHERE s.decision_id = c.decision_id "
        "                      AND s.result IS NOT NULL) "
        " ORDER BY c.court_id LIMIT $2", COURT_PREFIX + "%", int(limit))]
    ids = []
    for r in rows:
        try:
            ids.append(int(str(r["decision_id"])[len(COURT_PREFIX):]))
        except ValueError:
            continue
    evals = {}
    if ids:
        evals = {r["eval_id"]: dict(r) for r in await conn.fetch(
            "SELECT " + EVAL_COLUMNS + " FROM paper_profitability_evaluations"
            " e WHERE e.account_id = $1 AND e.eval_id = ANY($2::bigint[])",
            account_id, ids)}
    payouts = await settled_payouts(conn, account_id, {
        (e["us_market_slug"], e["holding_side"]) for e in evals.values()})
    scored = []
    for r in rows:
        try:
            e = evals.get(int(str(r["decision_id"])[len(COURT_PREFIX):]))
        except ValueError:
            e = None
        if e is None:
            continue
        real = await realized_for(conn, account_id, e, payouts)
        if real is None:
            continue
        court = {"decision_id": r["decision_id"], "chosen": r["chosen"],
                 "shadow_winner": r["shadow_winner"],
                 "disagreement": r["disagreement"],
                 "alternatives": _j(r["alternatives"]) or []}
        res = SC.score_outcome(court, {"ENTER": real["ENTER"],
                                       "CASH": real["CASH"]})
        res.update(basis=real["basis"], settled_at=real.get("settled_at"),
                   realized_by_alternative={"ENTER": C.rnd(real["ENTER"]),
                                            "CASH": 0.0})
        scored.append({"court": court, "result": res,
                       "decided_at": _epoch(r["decided_at"])})
    return {"status": "OK", "why": None, "scored": scored,
            "candidates": len(rows)}


# ═════════════════════════════════════════════════════════════════════
# 2 · AGENT ECONOMICS (the component each agent controls, never raw P&L)
# ═════════════════════════════════════════════════════════════════════

DEREK_SQL = """
SELECT f.group_id, f.us_market_slug, f.holding_side, f.order_id,
       o.idempotency_key, o.decided_at, max(f.strategy) AS strategy,
       sum(f.qty) AS q, sum(f.gross_usd) AS gross, min(f.filled_at) AS first_fill
  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
 WHERE f.account_id = $1 AND f.role = 'ENTRY' AND f.direction = 'BUY'
   AND f.strategy LIKE $2 AND f.filled_at >= $3
 GROUP BY 1, 2, 3, 4, 5, 6
 ORDER BY min(f.filled_at) LIMIT $4
"""

XAVIER_SQL = """
SELECT f.order_id, f.group_id, f.us_market_slug, f.holding_side,
       max(f.role) AS role, max(f.strategy) AS strategy, sum(f.qty) AS q,
       sum(f.gross_usd) AS gross, sum(f.fee_usd) AS fees,
       max(f.filled_at) AS last_fill
  FROM paper_fills f
 WHERE f.account_id = $1 AND f.direction = 'SELL'
   AND f.role = ANY($2::text[]) AND f.filled_at >= $3
 GROUP BY 1, 2, 3, 4 ORDER BY max(f.filled_at) LIMIT $4
"""

PREVENTED_SQL = """
SELECT s.shadow_key, s.strategy, s.capital_refusal, s.source, s.decided_at,
       o.outcome, o.filled_qty, o.counterfactual_pnl_usd, o.settled_at
  FROM paper_shadow_counterfactuals s
  JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE s.account_id = $1 AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND')
   AND o.filled_qty > 0 AND o.settled_at >= $2
 ORDER BY o.settled_at LIMIT $3
"""


def derek_alpha(legs: list, y: float) -> float:
    """Discovery alpha of one settled position: sum over its entry orders
    of qty x (settled payout per contract - market price at the decision).
    Fees and management are not Derek's."""
    return sum(float(l["q"]) * (float(y) - float(l["m"])) for l in legs)


def xavier_alpha(*, qty, gross, fees, y) -> float:
    """Management alpha of one sale: net proceeds minus the settlement value
    of the sold quantity had it been held (positive = the sale added
    value)."""
    return (float(gross) - float(fees)) - float(qty) * float(y)


def prevented_loss(counterfactual_pnl) -> float:
    """A refused trade's prevented loss: -(its counterfactual P&L)."""
    return -float(counterfactual_pnl)


async def agent_observations(conn, account_id: str, *, now: float,
                             window_s: float = AGENT_WINDOW_S,
                             limit: int = AGENT_BATCH) -> dict:
    """Measurable agent-economics observations (dicts shaped for
    runner.record_agent_observation) plus the UNAVAILABLE agents."""
    since = _ts(now - window_s)
    obs: list = []
    status: dict = {}
    # DEREK -- discovery alpha per settled position
    legs = [dict(r) for r in await conn.fetch(
        DEREK_SQL, account_id, DEREK_STRATEGY_PREFIX + "%", since,
        int(limit))]
    mk = {}
    keys = sorted({l["idempotency_key"] for l in legs if l["idempotency_key"]})
    if keys and await _has(conn, "paper_profitability_evaluations"):
        mk = {r["order_key"]: _num(r["market_price"]) for r in await conn.fetch(
            "SELECT DISTINCT ON (order_key) order_key, market_price "
            "  FROM paper_profitability_evaluations "
            " WHERE account_id = $1 AND order_key = ANY($2::text[]) "
            "   AND verdict = 'ENTER' AND market_price IS NOT NULL "
            " ORDER BY order_key, eval_id DESC", account_id, keys)}
    sells = [dict(r) for r in await conn.fetch(
        XAVIER_SQL, account_id, list(XAVIER_ROLES), since, int(limit))]
    payouts = await settled_payouts(conn, account_id, {
        (r["us_market_slug"], r["holding_side"]) for r in legs + sells})
    pos: dict = {}
    for l in legs:
        q = float(l["q"])
        if q <= 0:
            continue
        m = mk.get(l["idempotency_key"])
        basis = "BIND_MARKET_PRICE_AT_DECISION"
        if m is None:
            m, basis = float(l["gross"]) / q, "ENTRY_FILL_VWAP_FALLBACK"
        k = (l["group_id"], l["us_market_slug"], l["holding_side"])
        pos.setdefault(k, []).append({
            "order_id": l["order_id"], "q": q, "m": m, "basis": basis,
            "strategy": l["strategy"],
            "signal_age_s": (None if l["decided_at"] is None else max(
                0.0, _epoch(l["first_fill"]) - _epoch(l["decided_at"])))})
    for (g, slug, side), ls in pos.items():
        y = payouts.get((slug, side))
        if y is None:
            continue
        alpha = derek_alpha(ls, y["y"])
        capital = sum(l["q"] * l["m"] for l in ls)
        ages = [l["signal_age_s"] for l in ls if l["signal_age_s"] is not None]
        obs.append({
            "agent": "DEREK", "role_metric": "discovery_alpha_usd",
            "subject_id": position_key(account_id, g, slug, side),
            "economic_alpha_usd": round(alpha, 6),
            "observed_at": y.get("settled_at") or now,
            "detail": {"payout_per_contract": y["y"],
                       "payout_basis": y["basis"],
                       "entry_qty": sum(l["q"] for l in ls),
                       "capital_at_market_usd": round(capital, 6),
                       "signal_age_s": (round(min(ages), 3) if ages
                                        else None),
                       "legs": ls,
                       "rule": "sum qty x (settled payout - market price at "
                               "the decision); fees and management excluded"}})
    status["DEREK"] = {"status": "MEASURED", "source": "paper_fills ENTRY "
                       "BUY (DEREK*) + paper_profitability_evaluations."
                       "market_price + settled payout"}
    # XAVIER -- management alpha per EXIT / REDUCE / protection sale
    for r in sells:
        y = payouts.get((r["us_market_slug"], r["holding_side"]))
        if y is None:
            continue
        alpha = xavier_alpha(qty=r["q"], gross=r["gross"], fees=r["fees"],
                             y=y["y"])
        obs.append({
            "agent": "XAVIER", "role_metric": "management_alpha_usd",
            "subject_id": r["order_id"],
            "economic_alpha_usd": round(alpha, 6),
            "observed_at": y.get("settled_at") or now,
            "detail": {"role": r["role"], "strategy": r["strategy"],
                       "sold_qty": float(r["q"]),
                       "net_proceeds_usd": round(float(r["gross"])
                                                 - float(r["fees"]), 6),
                       "hold_counterfactual_usd": round(
                           float(r["q"]) * y["y"], 6),
                       "payout_basis": y["basis"],
                       "position_key": position_key(
                           account_id, r["group_id"], r["us_market_slug"],
                           r["holding_side"]),
                       "rule": "net sale proceeds - sold qty x settled "
                               "payout (HOLD decisions contribute nothing)"}})
    status["XAVIER"] = {"status": "MEASURED", "source": "paper_fills SELL "
                        "(EXIT/REDUCE/STANDING_PROTECTION) + settled payout"}
    # CAPITAL AUTHORITY -- prevented loss of refused entries
    if await _has(conn, "paper_shadow_counterfactual_outcomes"):
        for r in await conn.fetch(PREVENTED_SQL, account_id, since,
                                  int(limit)):
            obs.append({
                "agent": REFUSAL_GATE_AGENT, "role_metric": "prevented_loss_usd",
                "subject_id": r["shadow_key"],
                "economic_alpha_usd": round(prevented_loss(
                    r["counterfactual_pnl_usd"]), 6),
                "observed_at": _epoch(r["settled_at"]) or now,
                "detail": {"strategy": r["strategy"],
                           "refusal": r["capital_refusal"],
                           "source": r["source"], "outcome": r["outcome"],
                           "filled_qty": float(r["filled_qty"]),
                           "counterfactual_pnl_usd": float(
                               r["counterfactual_pnl_usd"]),
                           "rule": "-(settled shadow counterfactual P&L)"}})
        status[REFUSAL_GATE_AGENT] = {
            "status": "MEASURED", "source": "paper_shadow_counterfactuals + "
            "paper_shadow_counterfactual_outcomes (305)"}
    else:
        status[REFUSAL_GATE_AGENT] = {"status": "UNAVAILABLE",
                                      "why": "MIGRATION_305_NOT_APPLIED"}
    for a, why in UNAVAILABLE_AGENTS.items():
        status[a] = {"status": "UNAVAILABLE", "why": why}
    return {"status": "OK", "why": None, "observations": obs,
            "agents": status}


async def stored_agent_rows(conn, *, limit: int = 20000) -> list:
    if not await _has(conn, "capital_readiness_agent_economics"):
        return []
    return [dict(r) for r in await conn.fetch(
        "SELECT agent, role_metric, subject_id, economic_alpha_usd, detail, "
        "       observed_at FROM capital_readiness_agent_economics "
        " ORDER BY observed_at DESC, observation_id DESC LIMIT $1",
        int(limit))]


def championship_input(rows: list) -> list:
    """Stored observations in agent_championship.evaluate's shape: the
    value under the agent's own role metric."""
    out = []
    for r in rows:
        v = _num(r.get("economic_alpha_usd"))
        if v is None:
            continue
        out.append({"agent": r.get("agent"), "metric": r.get("role_metric"),
                    r.get("role_metric"): v})
    return out


def alpha_decay_input(rows: list) -> list:
    """DEREK observations as (signal age, realized alpha per dollar)."""
    out = []
    for r in rows:
        if str(r.get("agent")) != "DEREK":
            continue
        d = _j(r.get("detail")) or {}
        cap = _num(d.get("capital_at_market_usd"))
        a = _num(r.get("economic_alpha_usd"))
        age = _num(d.get("signal_age_s"))
        if cap and cap > 0 and a is not None and age is not None:
            out.append({"signal_age_s": age,
                        "realized_alpha_per_dollar": a / cap})
    return out


# ═════════════════════════════════════════════════════════════════════
# 3 · SCALE TWIN INPUTS (measured capacity only)
# ═════════════════════════════════════════════════════════════════════

async def scale_inputs(conn, account_id: str, *, now: float,
                       window_s: float = SCALE_WINDOW_S) -> dict:
    """The scale twin's inputs over the window, or UNAVAILABLE with the
    reason. Capacity is the SMALLER of the measured capacities available
    (pos_capacity MEASURED rows; the bind's capacity frontier); with none
    measured nothing is evaluated."""
    since = _ts(now - window_s)
    caps: dict = {}
    decay = []
    if await _has(conn, "pos_capacity"):
        rows = await conn.fetch(
            "SELECT DISTINCT ON (candidate_id) candidate_id, "
            "       executable_capacity_usd, edge_decay_per_1000_usd "
            "  FROM pos_capacity WHERE status = 'MEASURED' "
            "   AND computed_at >= $1 AND executable_capacity_usd IS NOT NULL"
            " ORDER BY candidate_id, computed_at DESC", since)
        if rows:
            caps["pos_capacity_usd"] = sum(
                float(r["executable_capacity_usd"]) for r in rows)
            caps["pos_capacity_candidates"] = len(rows)
            decay = [float(r["edge_decay_per_1000_usd"]) for r in rows
                     if r["edge_decay_per_1000_usd"] is not None]
    if not await _has(conn, "paper_profitability_evaluations"):
        return {"status": "UNAVAILABLE", "why": "MIGRATION_309_NOT_APPLIED"}
    evs = [dict(r) for r in await conn.fetch(
        "SELECT " + EVAL_COLUMNS + " FROM paper_profitability_evaluations e"
        " WHERE e.account_id = $1 AND e.evaluated_at >= $2 "
        " ORDER BY e.eval_id", account_id, since)]
    frontier, seen = 0.0, set()
    for e in sorted(evs, key=lambda r: -int(r["eval_id"])):
        k = (e["strategy"], e["us_market_slug"], e["holding_side"])
        if k in seen:
            continue
        seen.add(k)
        t = entry_terms(e)
        if t["capacity_usd"] is not None and t["capacity_usd"] > 0:
            frontier += t["capacity_usd"]
    if frontier > 0:
        caps["bind_capacity_frontier_usd"] = frontier
    measured = [v for k, v in caps.items() if k.endswith("_usd") and v > 0]
    if not measured:
        return {"status": "UNAVAILABLE",
                "why": "NO_MEASURED_EXECUTABLE_CAPACITY_IN_WINDOW",
                "window_s": window_s}
    base_cap = base_ev = var = 0.0
    n = 0
    for e in evs:
        if e["verdict"] != "ENTER":
            continue
        t = entry_terms(e)
        if None in (t["capital_usd"], t["expected_net_usd"],
                    t["uncertainty_sigma_usd"]):
            continue
        n += 1
        base_cap += t["capital_usd"]
        base_ev += t["expected_net_usd"]
        var += t["uncertainty_sigma_usd"] ** 2
    if n == 0 or base_cap <= 0:
        return {"status": "UNAVAILABLE",
                "why": "NO_ENTERED_EVALUATION_WITH_COMPLETE_TERMS_IN_WINDOW",
                "capacities": caps, "window_s": window_s}
    args = {"base_capital_usd": round(base_cap, 6),
            "base_expected_ev_usd": round(base_ev, 6),
            "capacity_usd": round(min(measured), 6),
            "edge_decay_per_1000_usd": (round(max(0.0, statistics.median(
                decay)), 9) if decay else 0.0),
            "fixed_cost_usd": 0.0, "variable_cost_bps": 0.0,
            "uncertainty_sigma_usd": round(math.sqrt(var), 6),
            "correlation_penalty_bps": 0.0}
    inputs = {"args": args, "capacities": caps, "entered_evaluations": n,
              "window_s": window_s, "account_id": account_id,
              "edge_decay_samples": len(decay),
              "basis": {"capacity": "min(measured capacities)",
                        "base": "entered 309 evaluations: expected all-in EV"
                                " (EV x fill probability) on decision-time "
                                "capital",
                        "sigma": "sqrt(sum qty^2 p(1-p)) settlement variance",
                        "costs": "all-in EV is already net of fees, adverse "
                                 "selection and residual haircut; the bind "
                                 "already applied the correlation factor"}}
    inputs["inputs_sha"] = C.sha(inputs)
    return {"status": "OK", "why": None, "args": args, "inputs": inputs,
            "scope_key": "ACCOUNT:%s:%ds" % (account_id, int(window_s))}


# ═════════════════════════════════════════════════════════════════════
# 4 · READINESS GATES (existing in-process readers, fail closed)
# ═════════════════════════════════════════════════════════════════════

def _gate(value, reason=None, **evidence) -> dict:
    return {"value": value is True, "reason": None if value is True else
            (reason or "GATE_NOT_SATISFIED"), "evidence": evidence}


async def gate_ci_exact_sha_green(conn, ctx) -> dict:
    """A committed, hash-verified release receipt for EXACTLY the running
    SHA, GATED, with every recorded GitHub CI run concluded success."""
    from ..api import command_release as REL
    sha = (ctx.get("source_sha") or "").strip().lower()
    if not sha or not REL._HEX.match(sha) or len(sha) != 40:
        return _gate(False, "NO_EXACT_SHA_CI_ATTESTATION_IN_PRODUCTION",
                     why="running SHA unknown or not a full 40-hex commit "
                         "(RENDER_GIT_COMMIT)", source_sha=sha or None)
    rec = REL.read_receipts()
    match = [r for r in rec.get("items") or () if r.get("sha") == sha]
    ok = [r for r in match if r.get("state") == "GATED"
          and r.get("hash_verified") and r.get("github_ci")
          and all(str((c or {}).get("conclusion")) == "success"
                  for c in r["github_ci"])]
    return _gate(bool(ok), "NO_EXACT_SHA_CI_ATTESTATION_IN_PRODUCTION",
                 source_sha=sha, receipts_for_sha=[
                     {k: r.get(k) for k in ("file", "state", "hash_verified")}
                     for r in match],
                 source="sportsassets/release_receipts (api/command_release."
                        "read_receipts)")


async def gate_small_live_shadow(conn, ctx) -> dict:
    if not await _has(conn, "execmirror_control"):
        return _gate(False, "EXECMIRROR_CONTROL_ABSENT")
    r = await conn.fetchrow(
        "SELECT enabled, stopped, revision, updated_at FROM "
        " execmirror_control WHERE id = 1")
    if r is None:
        return _gate(False, "EXECMIRROR_CONTROL_ROW_MISSING")
    actual_live = bool(r["enabled"]) and not bool(r["stopped"])
    return _gate(not actual_live, "SMALL_LIVE_ACTUAL_LANE_ACTIVE",
                 enabled=bool(r["enabled"]), stopped=bool(r["stopped"]),
                 revision=r["revision"],
                 source="execmirror_control (GET /api/command/small-live)")


async def gate_freshness_gte_95(conn, ctx) -> dict:
    from .. import bettor_paper_freshness as FR
    got = await FR.read(conn, ctx["account_id"], now=ctx["now"],
                        rows_limit=0)
    rate = _num(got.get("fresh_rate"))
    if got.get("status") != "OK":
        return _gate(False, "FRESHNESS_UNREADABLE", why=got.get("why"))
    if rate is None:
        return _gate(False, "FRESH_RATE_NULL_NOTHING_MARKABLE",
                     open_positions=got.get("open_positions"))
    return _gate(rate >= FRESHNESS_TARGET, "FRESH_RATE_BELOW_0_95",
                 fresh_rate=rate, markable=got.get("markable"),
                 source="bettor_paper_freshness.read")


async def _mirror_heartbeat(conn, ctx):
    if not await _has(conn, "service_heartbeats"):
        return None, "SERVICE_HEARTBEATS_ABSENT"
    r = await conn.fetchrow(
        "SELECT status, detail, beat_at FROM service_heartbeats "
        " WHERE service = 'mirror_shadow'")
    if r is None:
        return None, "NO_MIRROR_SHADOW_HEARTBEAT"
    age = ctx["now"] - _epoch(r["beat_at"])
    if age > MIRROR_HEARTBEAT_MAX_AGE_S:
        return None, "MIRROR_SHADOW_HEARTBEAT_STALE_%ds" % int(age)
    return {"status": r["status"], "detail": _j(r["detail"]) or {},
            "age_s": round(age, 1)}, None


async def gate_mirror_positions_readable(conn, ctx) -> dict:
    hb, why = await _mirror_heartbeat(conn, ctx)
    if hb is None:
        return _gate(False, why)
    bad = bool(hb["detail"].get("positions_unreadable"))
    return _gate(not bad, "MIRROR_POSITIONS_UNREADABLE",
                 status=hb["status"], age_s=hb["age_s"],
                 abandoned=hb["detail"].get("abandoned"),
                 source="service_heartbeats mirror_shadow "
                        "(workers/mirror_shadow.tick_once)")


async def gate_mirror_exit_unsuppressed(conn, ctx) -> dict:
    hb, why = await _mirror_heartbeat(conn, ctx)
    if hb is None:
        return _gate(False, why)
    leg = hb["detail"].get("exit_leg")
    bad = (not isinstance(leg, dict) or "error" in leg
           or leg.get("state") in ("suppressed", "unread", "truncated"))
    return _gate(not bad, "MIRROR_EXIT_LEG_SUPPRESSED_OR_UNREAD",
                 exit_leg_state=(leg or {}).get("state")
                 if isinstance(leg, dict) else None,
                 exit_leg_error=(leg or {}).get("error")
                 if isinstance(leg, dict) else None,
                 abandoned=hb["detail"].get("abandoned"),
                 source="service_heartbeats mirror_shadow exit_leg")


async def gate_software_reds_zero(conn, ctx) -> dict:
    from .. import coverage_first_loss as FL
    got = await FL.read(conn, since=ctx["now"] - FIRST_LOSS_WINDOW_S,
                        until=ctx["now"] + 1.0)
    if got.get("status") != "OK":
        return _gate(False, "FIRST_LOSS_CENSUS_UNAVAILABLE",
                     why=got.get("why"))
    by = (got.get("totals") or {}).get("by_class") or {}
    sw = by.get("SOFTWARE")
    return _gate(sw == 0, "SOFTWARE_RED_FIRST_LOSSES_IN_LAST_HOUR",
                 software=sw, by_class=by, window_s=FIRST_LOSS_WINDOW_S,
                 source="coverage_first_loss.read (1 h)")


async def _acceptance(conn, ctx):
    if "acceptance" not in ctx:
        from ..api import command_capital_authority as CCA
        try:
            ctx["acceptance"] = await CCA.read(
                conn, account_id=ctx["account_id"], now=ctx["now"])
        except Exception as exc:                                # noqa: BLE001
            ctx["acceptance"] = {"status": "UNAVAILABLE", "why": "%s: %s" % (
                type(exc).__name__, str(exc)[:160]), "data": None}
    return ctx["acceptance"]


async def gate_management_epoch_reconciled(conn, ctx) -> dict:
    acc = await _acceptance(conn, ctx)
    if acc.get("status") != "OK":
        return _gate(False, "CAPITAL_AUTHORITY_READ_UNAVAILABLE",
                     why=acc.get("why"))
    m = ((acc.get("data") or {}).get("paper") or {}).get("management") or {}
    return _gate(m.get("reconciles_to_the_cent") is True,
                 "MANAGEMENT_EPOCH_DOES_NOT_RECONCILE",
                 status=m.get("status"), residual_usd=m.get("residual_usd"),
                 null_fields=m.get("null_fields"),
                 source="bettor_capital_authority.management_reconciliation")


async def gate_xavier_complete(conn, ctx) -> dict:
    from .. import bettor_paper_freshness as FR
    from .. import bettor_paper_ledger as L
    pos = await L.positions(conn, ctx["account_id"])
    strategies = sorted({p.get("strategy") or L.DEFAULT_STRATEGY
                         for p in pos})
    incomplete, per = [], {}
    for s in strategies:
        iv = await FR.strategy_management_integrity(
            conn, ctx["account_id"], s, now=ctx["now"])
        per[s] = {"open_positions": iv.get("open_positions"),
                  "packet_incomplete_rate": iv.get("packet_incomplete_rate")}
        incomplete += list(iv.get("packet_incomplete") or [])
    return _gate(not incomplete, "XAVIER_PACKETS_INCOMPLETE",
                 open_positions=len(pos), strategies=per,
                 packet_incomplete=incomplete[:20],
                 source="bettor_paper_freshness.strategy_management_integrity")


async def gate_production_canary_clean(conn, ctx) -> dict:
    from .. import ops_canary as OC
    got = await OC.build(conn, api=None)
    return _gate(got.get("verdict") == OC.PASS, "CANARY_NOT_PASS",
                 verdict=got.get("verdict"), checks=got.get("checks"),
                 source="ops_canary.build (GET /api/command/canary)")


async def gate_profitability_bind_active(conn, ctx) -> dict:
    from .. import bettor_paper_profitability_bind as PB
    if not await PB.schema(conn):
        return _gate(False, "MIGRATION_309_NOT_APPLIED")
    r = await conn.fetchrow(
        "SELECT max(fitted_at) AS last_fit, count(*) AS n FROM "
        " paper_profitability_models WHERE account_id = $1",
        ctx["account_id"])
    last = _epoch(r["last_fit"]) if r else None
    age = None if last is None else ctx["now"] - last
    return _gate(age is not None and age <= BIND_MODEL_MAX_AGE_S,
                 "NO_PROFITABILITY_MODEL_FITTED_WITHIN_1H",
                 last_fit_age_s=None if age is None else round(age, 1),
                 models=int(r["n"]) if r else 0,
                 source="paper_profitability_models (309 bind step)")


async def gate_forward_economics_positive(conn, ctx) -> dict:
    acc = await _acceptance(conn, ctx)
    if acc.get("status") != "OK":
        return _gate(False, "CAPITAL_AUTHORITY_READ_UNAVAILABLE",
                     why=acc.get("why"))
    rows = ((acc.get("data") or {}).get("strategies") or {})
    verdicts = {s: (r.get("forward_economics") or {}).get("verdict")
                for s, r in rows.items()}
    pos = sorted(s for s, v in verdicts.items() if v == "POSITIVE")
    return _gate(bool(pos), "NO_STRATEGY_WITH_POSITIVE_FORWARD_ECONOMICS",
                 verdicts=verdicts, positive=pos,
                 source="api/command_capital_authority.read "
                        "(bettor_capital_authority.forward_verdict)")


GATE_READERS: dict[str, Callable[[Any, dict], Awaitable[dict]]] = {
    "ci_exact_sha_green": gate_ci_exact_sha_green,
    "small_live_shadow": gate_small_live_shadow,
    "freshness_gte_95": gate_freshness_gte_95,
    "mirror_positions_readable": gate_mirror_positions_readable,
    "mirror_exit_unsuppressed": gate_mirror_exit_unsuppressed,
    "software_reds_zero": gate_software_reds_zero,
    "management_epoch_reconciled": gate_management_epoch_reconciled,
    "xavier_complete": gate_xavier_complete,
    "production_canary_clean": gate_production_canary_clean,
    "profitability_bind_active": gate_profitability_bind_active,
    "forward_economics_positive": gate_forward_economics_positive,
}
assert set(GATE_READERS) == set(R.HARD_GATES)


async def gates(conn, *, account_id: str, now: float, source_sha=None,
                readers: dict | None = None) -> dict:
    """{gate: {value, reason, evidence}} for every hard gate. A reader that
    raises or is missing is False (fail closed) with the reason."""
    rd = dict(GATE_READERS)
    rd.update(readers or {})
    ctx = {"account_id": account_id, "now": float(now),
           "source_sha": source_sha}
    out = {}
    for g in R.HARD_GATES:
        fn = rd.get(g)
        if fn is None:
            out[g] = _gate(False, "NO_READER")
            continue
        got, why = await _guard(conn, lambda fn=fn: fn(conn, ctx))
        if got is None or not isinstance(got, dict):
            out[g] = _gate(False, "UNREADABLE", why=why)
        else:
            out[g] = dict(got, value=got.get("value") is True)
    return out


def source_sha_from_env():
    return (os.environ.get("RENDER_GIT_COMMIT") or "").strip() or None
