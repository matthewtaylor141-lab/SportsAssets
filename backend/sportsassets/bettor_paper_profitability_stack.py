"""THE PROFITABILITY STACK AROUND THE BIND (migration 311): AUTOMATIC
QUARANTINE, THE COUNTERFACTUAL VARIANT LEDGER'S SETTLEMENT, SUBSYSTEM PROFIT
ATTRIBUTION, THE PROBABILISTIC P&L FORECAST AND THE FORWARD PAPER / SHADOW
SCOREBOARD.

PAPER / SHADOW ONLY. Nothing here raises a cap, a size, a risk limit or a
live authority, and nothing here reaches a venue. The one write with an
effect on entries is a QUARANTINE: an automatic lifecycle TIGHTENING
(bettor_strategy_lifecycle.record with the AUTOMATIC_RULE_EVALUATOR actor,
which the lifecycle itself refuses unless the move raises the rank), after
which the lifecycle entry gate -- at the decision (paper_derek.capital_gate)
and under the account lock (the paper ledger entry path) -- refuses
every new PAPER entry of that strategy (STRATEGY_LIFECYCLE_QUARANTINED_NO_
PAPER_ENTRY; the decision is still recorded as a shadow counterfactual).
Leaving QUARANTINED stays with a named person (bettor_strategy_lifecycle.
transition): there is NO automatic promotion anywhere here. Historical
PAPER rows are read, never rewritten.

  21 QUARANTINE (`quarantine_triggers`, `evaluate_quarantine`, run by the
     paper pass step `quarantine_step` after the fits): a strategy whose
       CALIBRATION deteriorated -- >= QUAR_MIN_CALIBRATION_OBS settled
         distinct contract-sides it evaluated, and the Brier score of the
         probability it used exceeds the market price's by
         QUAR_BRIER_MARGIN, or its ECE exceeds QUAR_MAX_ECE;
       RESIDUALS deteriorated -- >= QUAR_MIN_RESIDUAL_OBS realized-minus-
         expected observations with a mean residual per contract <=
         -QUAR_RESIDUAL_PER_CONTRACT;
       EXECUTION deteriorated -- >= QUAR_MIN_EXECUTION_FILLS fills with a
         post-fill markout >= QUAR_MARKOUT_PER_CONTRACT against it, or >=
         QUAR_MIN_EXECUTION_ORDERS terminal orders with a fill rate below
         QUAR_MIN_FILL_RATE
     is moved to QUARANTINED with the evidence recorded.
  22/23 COUNTERFACTUAL VARIANTS: the bind records each evaluation's
     variants (bettor_paper_profitability_bind.record_variants); the step
     `counterfactual_step` settles them on authoritative outcomes
     (NOT_REALIZED_PNL, never summed with PAPER P&L).
  24 ATTRIBUTION: every evaluation's expected EV is decomposed by the bind
     (probability edge, calibration, settlement difference, spread,
     slippage, fees, adverse selection, residual haircut, freshness,
     management); `attribution_totals` sums the entered ones and sets the
     realized P&L beside them (the outcome residual = realized - expected).
  26 FORECAST (`pnl_forecast`): per-position Bernoulli settlement
     simulation of the open book plus the forward expected entries (fill x
     settlement), deterministic seed; p5 / p50 / p95.
  SCOREBOARD (`scoreboard`, pure; `scoreboard_read`; GET /api/command/
     paper/profitability-scoreboard): the forward sample since the bind
     cutover (migration 309's applied instant), expected after-cost EV,
     realized P&L since the cutover (historical losses shown separately,
     unchanged), EV per capital-hour, Brier / ECE, residual, max drawdown,
     turnover, capital deployed, the forecast, and a verdict that is
     DEFERRED_FORWARD_EVIDENCE below the required sample (stated).
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import random
import time
from typing import Any

from . import bettor_paper_profitability_bind as PBIND

VERSION = "PAPER_PROFITABILITY_STACK_V1"
AUTHORITY = "PAPER_ONLY_NO_CAPITAL_AUTHORITY"

# ── 21 quarantine (predeclared) ──────────────────────────────────────
QUAR_MIN_CALIBRATION_OBS = 50
QUAR_BRIER_MARGIN = 0.02
QUAR_MAX_ECE = 0.15
QUAR_MIN_RESIDUAL_OBS = 30
QUAR_RESIDUAL_PER_CONTRACT = 0.05
QUAR_MIN_EXECUTION_FILLS = 20
QUAR_MARKOUT_PER_CONTRACT = 0.05
QUAR_MIN_EXECUTION_ORDERS = 30
QUAR_MIN_FILL_RATE = 0.05
QUARANTINE_EVERY_S = 600.0
RULE_CALIBRATION_QUARANTINE = "PROFITABILITY_CALIBRATION_DETERIORATION_QUARANTINE"
RULE_RESIDUAL_QUARANTINE = "PROFITABILITY_RESIDUAL_DETERIORATION_QUARANTINE"
RULE_EXECUTION_QUARANTINE = "PROFITABILITY_EXECUTION_DETERIORATION_QUARANTINE"
QUARANTINE_RULES = (RULE_CALIBRATION_QUARANTINE, RULE_RESIDUAL_QUARANTINE,
                    RULE_EXECUTION_QUARANTINE)

# ── 26 forecast ──────────────────────────────────────────────────────
FORECAST_SIMS = 4000
FORECAST_SEED = 20261006

# ── scoreboard ───────────────────────────────────────────────────────
ECE_BINS = 10
MIN_FORWARD_SAMPLE = 100
CI_Z = 1.96
DEFERRED = "DEFERRED_FORWARD_EVIDENCE"
FORWARD_POSITIVE = "FORWARD_POSITIVE"
FORWARD_NEGATIVE = "FORWARD_NEGATIVE"
FORWARD_INCONCLUSIVE = "FORWARD_INCONCLUSIVE"
#: settle-able variants (EARLY_EXIT has no observable exit price)
SETTLED_VARIANTS = ("AS_BOUND", "POLICY_SIZE", "HALF_SIZE", "MAKER",
                    "HOLD_TO_SETTLEMENT")


def _num(v):
    return PBIND._num(v)


def _r(v, n=9):
    return None if v is None else round(float(v), n)


def _ts(epoch: float):
    return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc)


def _epoch(v):
    if v is None:
        return None
    return float(v.timestamp()) if hasattr(v, "timestamp") else float(v)


# ═════════════════════════════════════════════════════════════════════
# CALIBRATION METRICS, DRAWDOWN (pure)
# ═════════════════════════════════════════════════════════════════════

def calibration_metrics(rows: list, *, key: str = "p") -> dict:
    """rows: [{p, y, market?}]. Brier score and expected calibration error
    (ECE_BINS equal-width bins) of `key`, and the market's Brier beside."""
    xs = [(float(r[key]), float(r["y"])) for r in rows or []
          if _num(r.get(key)) is not None and _num(r.get("y")) is not None]
    n = len(xs)
    if not n:
        return {"n": 0, "brier": None, "ece": None, "market_brier": None}
    brier = sum((p - y) ** 2 for p, y in xs) / n
    bins: dict = {}
    for p, y in xs:
        b = min(ECE_BINS - 1, int(p * ECE_BINS))
        g = bins.setdefault(b, [0, 0.0, 0.0])
        g[0] += 1
        g[1] += p
        g[2] += y
    ece = sum(g[0] / n * abs(g[1] / g[0] - g[2] / g[0])
              for g in bins.values())
    mk = [(float(r["market"]), float(r["y"])) for r in rows or []
          if _num(r.get("market")) is not None
          and _num(r.get("y")) is not None and _num(r.get(key)) is not None]
    mb = (sum((p - y) ** 2 for p, y in mk) / len(mk)) if mk else None
    return {"n": n, "brier": _r(brier), "ece": _r(ece),
            "market_brier": _r(mb), "market_n": len(mk)}


def max_drawdown(pnls: list) -> float:
    peak = cum = mdd = 0.0
    for x in pnls or []:
        cum += float(x)
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return round(mdd, 6)


# ═════════════════════════════════════════════════════════════════════
# 21 · AUTOMATIC STRATEGY QUARANTINE
# ═════════════════════════════════════════════════════════════════════

def quarantine_triggers(strategy: str, *, calibration_rows: list,
                        residual_model: dict | None,
                        execution_model: dict | None) -> list:
    """Every predeclared deterioration trigger the strategy's evidence
    fires, each with the numbers it fired on. Pure."""
    out = []
    cm = calibration_metrics(calibration_rows, key="p")
    if cm["n"] >= QUAR_MIN_CALIBRATION_OBS:
        worse = (cm["market_brier"] is not None and cm["brier"]
                 - cm["market_brier"] > QUAR_BRIER_MARGIN)
        if worse or (cm["ece"] or 0.0) > QUAR_MAX_ECE:
            out.append({"rule_id": RULE_CALIBRATION_QUARANTINE,
                        "evidence": dict(cm, brier_margin=QUAR_BRIER_MARGIN,
                                         max_ece=QUAR_MAX_ECE)})
    c = ((residual_model or {}).get("cells") or {}).get("%s|*|*" % strategy)
    if c and int(c.get("n") or 0) >= QUAR_MIN_RESIDUAL_OBS and \
            (_num(c.get("residual_per_contract")) or 0.0) <= \
            -QUAR_RESIDUAL_PER_CONTRACT:
        out.append({"rule_id": RULE_RESIDUAL_QUARANTINE,
                    "evidence": dict(c, threshold_per_contract=(
                        -QUAR_RESIDUAL_PER_CONTRACT),
                        min_observations=QUAR_MIN_RESIDUAL_OBS)})
    for k, row in sorted(((execution_model or {}).get(
            "by_strategy_style") or {}).items()):
        if row.get("strategy") != strategy:
            continue
        mk = _num(row.get("markout_per_contract_raw"))
        fr = _num(row.get("fill_rate_raw"))
        if int(row.get("fills") or 0) >= QUAR_MIN_EXECUTION_FILLS and \
                mk is not None and mk >= QUAR_MARKOUT_PER_CONTRACT:
            out.append({"rule_id": RULE_EXECUTION_QUARANTINE,
                        "evidence": dict(row, trigger="MARKOUT",
                                         threshold=QUAR_MARKOUT_PER_CONTRACT)})
            break
        if int(row.get("terminal_orders") or 0) >= \
                QUAR_MIN_EXECUTION_ORDERS and fr is not None and \
                fr < QUAR_MIN_FILL_RATE:
            out.append({"rule_id": RULE_EXECUTION_QUARANTINE,
                        "evidence": dict(row, trigger="FILL_RATE",
                                         threshold=QUAR_MIN_FILL_RATE)})
            break
    return out


CAL_ROWS_SQL = """
SELECT DISTINCT ON (us_market_slug, holding_side)
       us_market_slug, holding_side, p_used, market_price
  FROM paper_profitability_evaluations
 WHERE account_id = ANY($1::text[]) AND strategy = $2 AND p_used IS NOT NULL
   AND us_market_slug IS NOT NULL AND holding_side IS NOT NULL
   AND evaluated_at >= $3
 ORDER BY us_market_slug, holding_side, evaluated_at DESC, eval_id DESC
"""


async def calibration_rows(conn, account_id: str, strategy: str, *,
                           since: float) -> list:
    """The strategy's settled, distinct contract-sides it evaluated: the
    probability it used, the market price and the outcome."""
    from .simulated_account_context import risk_history_accounts
    accounts = await risk_history_accounts(conn, account_id)
    rows = [dict(r) for r in await conn.fetch(CAL_ROWS_SQL, accounts,
                                              strategy, _ts(since))]
    ys = await PBIND.outcomes(conn, [r["us_market_slug"] for r in rows])
    out = []
    for r in rows:
        y = ys.get((r["us_market_slug"], r["holding_side"]))
        if y is None:
            continue
        out.append({"p": _num(r["p_used"]), "market": _num(r["market_price"]),
                    "y": y})
    return out


async def evaluate_quarantine(conn, *, account_id: str, now: float,
                              strategies=None) -> dict:
    """Apply the predeclared deterioration triggers to every strategy with
    evaluations on the account and RECORD the QUARANTINE each firing
    strategy requires (an automatic tightening only)."""
    from . import bettor_strategy_lifecycle as LC
    out: dict[str, Any] = {"account_id": account_id, "at": now,
                           "quarantined": [], "strategies": {}}
    if not await PBIND.schema(conn) or not await LC.schema(conn):
        return dict(out, why="MIGRATION_309_OR_290_NOT_APPLIED")
    models = await PBIND.latest_models(conn, account_id)
    from .simulated_account_context import risk_history_accounts
    accounts = await risk_history_accounts(conn, account_id)
    names = set(strategies or ()) | {r["strategy"] for r in await conn.fetch(
        "SELECT DISTINCT strategy FROM paper_profitability_evaluations "
        " WHERE account_id = ANY($1::text[])", accounts)}
    since = now - PBIND.FIT_WINDOW_DAYS * 86400.0
    for s in sorted(names):
        cur = await LC.current_state(conn, account_id, s)
        if not cur.get("ok"):
            out["strategies"][s] = {"why": cur.get("why")}
            continue
        fired = quarantine_triggers(
            s, calibration_rows=await calibration_rows(
                conn, account_id, s, since=since),
            residual_model=models.get("RESIDUAL"),
            execution_model=models.get("EXECUTION"))
        out["strategies"][s] = {"state": cur["state"],
                                "fired": [f["rule_id"] for f in fired]}
        if not fired or not LC.is_tightening(cur["state"], LC.QUARANTINED):
            continue
        eid = await LC.record(
            conn, account_id=account_id, strategy=s,
            from_state=cur["state"], to_state=LC.QUARANTINED,
            rule_id=fired[0]["rule_id"], actor=LC.AUTOMATIC_ACTOR,
            evidence={"profitability_stack": VERSION, "triggers": fired},
            why="predeclared deterioration trigger %s fired (%s)" % (
                fired[0]["rule_id"], json.dumps(fired[0]["evidence"],
                                                default=str)[:400]),
            at=now)
        out["quarantined"].append({"strategy": s, "event_id": eid,
                                   "from_state": cur["state"],
                                   "rule_id": fired[0]["rule_id"]})
        out["strategies"][s]["state"] = LC.QUARANTINED
    return out


_LAST_QUARANTINE: dict = {}


async def quarantine_step(conn, ctx: dict) -> dict:
    """THE PAPER PASS STEP (after the fits, before the entries): at most
    every QUARANTINE_EVERY_S. Records quarantines only; never an order."""
    acct = ctx.get("account_id")
    now = float(ctx["now"])
    last = _LAST_QUARANTINE.get(acct)
    if last is not None and 0 <= now - last < QUARANTINE_EVERY_S:
        return {"ran": False, "why": "RAN_WITHIN_QUARANTINE_EVERY_S"}
    _LAST_QUARANTINE[acct] = now
    try:
        got = await evaluate_quarantine(conn, account_id=acct, now=now)
    except Exception as exc:                                    # noqa: BLE001
        return {"ran": True, "error": "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:160])}
    return {"ran": True, "quarantined": len(got.get("quarantined") or [])}


# ═════════════════════════════════════════════════════════════════════
# 22 / 23 · THE COUNTERFACTUAL VARIANTS' SETTLEMENT
# ═════════════════════════════════════════════════════════════════════

def variant_pnl(v: dict, y: float) -> float:
    """The counterfactual P&L of a variant at settlement payout `y`: the
    fill-probability-weighted (qty x y - cost - fees). NOT_REALIZED_PNL."""
    q = float(v.get("qty") or 0)
    pf = _num(v.get("fill_probability"))
    pf = 1.0 if pf is None else pf
    return pf * (q * float(y) - float(v.get("cost_usd") or 0)
                 - float(v.get("fees_usd") or 0))


async def settle_variants(conn, *, account_id: str, now: float,
                          limit: int = 500) -> dict:
    if not await conn.fetchval(
            "SELECT to_regclass('paper_counterfactual_variant_outcomes') "
            "IS NOT NULL"):
        return {"settled": 0, "why": "MIGRATION_311_NOT_APPLIED"}
    rows = [dict(r) for r in await conn.fetch(
        "SELECT v.* FROM paper_counterfactual_variants v "
        " WHERE v.account_id = $1 AND v.variant = ANY($2::text[]) "
        "   AND NOT EXISTS (SELECT 1 FROM paper_counterfactual_variant_"
        "outcomes o WHERE o.variant_id = v.variant_id) "
        " ORDER BY v.variant_id LIMIT $3", account_id,
        list(SETTLED_VARIANTS), int(limit))]
    ys = await PBIND.outcomes(conn, [r["us_market_slug"] for r in rows])
    n = 0
    for r in rows:
        y = ys.get((r["us_market_slug"], r["holding_side"]))
        if y is None:
            continue
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO paper_counterfactual_variant_outcomes "
                    " (variant_id, outcome, payout_per_contract, "
                    " counterfactual_pnl_usd, settled_at) VALUES ($1,$2,$3,"
                    " $4,$5) ON CONFLICT (variant_id) DO NOTHING",
                    r["variant_id"], "WON" if y >= 0.5 else "LOST", y,
                    round(variant_pnl(r, y), 6), _ts(now))
            n += 1
        except Exception:                                       # noqa: BLE001
            continue
    return {"settled": n, "pending": len(rows) - n}


async def counterfactual_step(conn, ctx: dict) -> dict:
    try:
        from .simulated_account_context import risk_history_accounts
        accounts = await risk_history_accounts(conn, ctx["account_id"])
        out = {"settled": 0, "pending": 0}
        for account_id in accounts:
            got = await settle_variants(conn, account_id=account_id,
                                        now=float(ctx["now"]))
            for key in ("settled", "pending"):
                out[key] += got.get(key, 0)
            if got.get("why"):
                out.setdefault("why", got["why"])
        return out
    except Exception as exc:                                    # noqa: BLE001
        return {"settled": 0, "why": "%s: %s" % (type(exc).__name__,
                                                 str(exc)[:160])}


# ═════════════════════════════════════════════════════════════════════
# 26 · THE PROBABILISTIC P&L FORECAST (pure)
# ═════════════════════════════════════════════════════════════════════

def _pct(xs: list, q: float) -> float:
    if not xs:
        return 0.0
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def pnl_forecast(open_positions: list, forward: list | None = None, *,
                 n_sims: int = FORECAST_SIMS, seed: int = FORECAST_SEED
                 ) -> dict:
    """open_positions: [{qty, cost_basis_usd, p}] -- each settles at 1 per
    contract with probability p (a Bernoulli draw per position, per
    simulation), P&L = qty x y - cost basis. forward: [{qty, cost_usd,
    fees_usd, p, fill_probability}] -- each fills with its fill probability
    and then settles the same way. Deterministic for a seed. Pure."""
    rng = random.Random(seed)
    ops = [(float(o["qty"]), float(o.get("cost_basis_usd") or 0.0),
            min(1.0, max(0.0, float(o["p"]))))
           for o in open_positions or []
           if _num(o.get("qty")) and _num(o.get("p")) is not None]
    fws = [(float(f["qty"]), float(f.get("cost_usd") or 0.0)
            + float(f.get("fees_usd") or 0.0),
            min(1.0, max(0.0, float(f["p"]))),
            min(1.0, max(0.0, float(f.get("fill_probability")
                                    if f.get("fill_probability") is not None
                                    else 1.0))))
           for f in forward or []
           if _num(f.get("qty")) and _num(f.get("p")) is not None]
    sims = []
    for _ in range(max(1, int(n_sims))):
        tot = 0.0
        for q, c, p in ops:
            tot += (q if rng.random() < p else 0.0) - c
        for q, c, p, pf in fws:
            if rng.random() < pf:
                tot += (q if rng.random() < p else 0.0) - c
        sims.append(tot)
    sims.sort()
    mean = sum(sims) / len(sims)
    exp = sum(q * p - c for q, c, p in ops) + sum(
        pf * (q * p - c) for q, c, p, pf in fws)
    return {"method": "PER_POSITION_BERNOULLI_SETTLEMENT_SIMULATION",
            "seed": seed, "simulations": len(sims),
            "open_positions": len(ops), "forward_entries": len(fws),
            "p5_usd": _r(_pct(sims, 0.05), 6),
            "p50_usd": _r(_pct(sims, 0.50), 6),
            "p95_usd": _r(_pct(sims, 0.95), 6),
            "mean_usd": _r(mean, 6), "expected_usd": _r(exp, 6),
            "pnl_class": "FORECAST_NOT_REALIZED_PNL"}


# ═════════════════════════════════════════════════════════════════════
# 24 · ATTRIBUTION TOTALS (pure)
# ═════════════════════════════════════════════════════════════════════

def attribution_totals(rows: list, *, realized_pnl_usd=None,
                       expected_on_closed_usd=None) -> dict:
    """rows: the `attribution` of each entered evaluation (scaled by the
    caller to the filled quantity). The sum per term, the expected total,
    and -- with the realized P&L of the closed entries -- the outcome
    residual (realized - their expected)."""
    tot: dict = {}
    for a in rows or []:
        for k, v in ((a or {}).get("terms") or {}).items():
            if _num(v) is not None:
                tot[k] = tot.get(k, 0.0) + float(v)
    exp = sum(tot.values())
    rz = _num(realized_pnl_usd)
    ec = _num(expected_on_closed_usd)
    return {"terms_usd": {k: _r(v, 6) for k, v in sorted(tot.items())},
            "expected_total_usd": _r(exp, 6), "entries": len(rows or []),
            "realized_closed_pnl_usd": _r(rz, 6),
            "expected_on_closed_usd": _r(ec, 6),
            "outcome_residual_usd": (None if rz is None or ec is None
                                     else _r(rz - ec, 6))}


# ═════════════════════════════════════════════════════════════════════
# THE FORWARD PAPER / SHADOW SCOREBOARD
# ═════════════════════════════════════════════════════════════════════

def required_sample(pnls: list) -> int:
    """The forward sample a mean per-trade P&L needs for its 95% lower
    bound to clear zero at the observed mean / sd: (z sd / mean)^2, never
    below MIN_FORWARD_SAMPLE (a non-positive mean: the floor)."""
    xs = [float(x) for x in pnls or []]
    n = len(xs)
    if n < 2:
        return MIN_FORWARD_SAMPLE
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    if m <= 0 or sd <= 0:
        return MIN_FORWARD_SAMPLE
    return max(MIN_FORWARD_SAMPLE, int(math.ceil((CI_Z * sd / m) ** 2)))


def scoreboard(d: dict) -> dict:
    """THE FORWARD SCOREBOARD, pure over the gathered `d` (scoreboard_read
    states every field's source). The verdict never promotes anything."""
    pnls = [float(x) for x in d.get("closed_pnls_since_cutover") or []]
    n_ind = int(d.get("independent_trades") or 0)
    need = required_sample(pnls)
    n = len(pnls)
    mean = sum(pnls) / n if n else None
    sd = (math.sqrt(sum((x - mean) ** 2 for x in pnls) / (n - 1))
          if n >= 2 else None)
    half = CI_Z * sd / math.sqrt(n) if sd is not None else None
    lo = None if half is None else mean - half
    hi = None if half is None else mean + half
    settled_ind = int(d.get("independent_settled_trades") or n)
    if settled_ind < need:
        verdict = DEFERRED
        why = ("%d independent settled forward trade(s) since the cutover "
               "< the required %d: profitability is not measured" % (
                   settled_ind, need))
    elif lo is not None and lo > 0 and sum(pnls) > 0:
        verdict, why = FORWARD_POSITIVE, "95% CI lower bound > 0"
    elif hi is not None and hi < 0:
        verdict, why = FORWARD_NEGATIVE, "95% CI upper bound < 0"
    else:
        verdict, why = FORWARD_INCONCLUSIVE, "the 95% CI spans zero"
    exp = d.get("expected_ev_usd")
    rz = sum(pnls) if pnls else 0.0
    return {
        "version": VERSION, "label": "PAPER", "authority": AUTHORITY,
        "cutover": d.get("cutover"), "cutover_basis": d.get("cutover_basis"),
        "sample": {"independent_opportunities": int(
                       d.get("independent_opportunities") or 0),
                   "independent_events": int(d.get("independent_events")
                                             or 0),
                   "independent_trades": n_ind,
                   "independent_settled_trades": settled_ind,
                   "basis": ("distinct contract-sides / fixtures evaluated "
                             "and entered since the cutover")},
        "expected_after_cost_ev_usd": _r(exp, 6),
        "realized_pnl_since_cutover_usd": _r(rz, 6),
        "historical_realized_pnl_usd": _r(d.get("historical_realized_pnl_usd"),
                                          6),
        "historical_is": "BEFORE_THE_CUTOVER_SHOWN_SEPARATELY_UNCHANGED",
        "ev_per_capital_hour": _r(d.get("ev_per_capital_hour")),
        "calibration": d.get("calibration") or {"n": 0},
        "expected_vs_realized_residual_usd": _r(d.get("residual_usd"), 6),
        "attribution": d.get("attribution"),
        "max_drawdown_usd": max_drawdown(pnls),
        "turnover": d.get("turnover") or {},
        "capital_deployed_usd": _r(d.get("capital_deployed_usd"), 6),
        "forecast": d.get("forecast") or pnl_forecast([], []),
        "counterfactuals": d.get("counterfactuals") or {},
        "shadow": d.get("shadow") or {},
        "mean_pnl_per_trade_usd": _r(mean, 6),
        "pnl_ci95": [_r(lo, 6), _r(hi, 6)],
        "verdict": verdict, "why": why,
        "required_sample_size": need,
        "promotes_nothing": True}


async def bind_cutover(conn) -> tuple:
    """Migration 309's applied instant (schema_migrations), else the first
    profitability evaluation, else the capital authority's forward start."""
    try:
        v = await conn.fetchval(
            "SELECT applied_at FROM schema_migrations WHERE version = $1",
            "309_paper_profitability_bind.sql")
        if v is not None:
            return _epoch(v), "schema_migrations.applied_at(309)"
    except Exception:                                           # noqa: BLE001
        pass
    from . import bettor_capital_authority as CA
    return CA.FORWARD_SINCE, "bettor_capital_authority.FORWARD_SINCE"


async def scoreboard_read(conn, *, account_id: str, now: float,
                          cutover: float | None = None) -> dict:
    """Gather the scoreboard's inputs for the account since the cutover and
    return `scoreboard(...)`. Raises on a failed read (the route answers
    UNAVAILABLE)."""
    from . import bettor_paper_ledger as L
    from . import bettor_strategy_lifecycle as LC
    basis = "?cutover="
    if cutover is None:
        cutover, basis = await bind_cutover(conn)
    c = float(cutover)
    ev = [dict(r) for r in await conn.fetch(
        "SELECT eval_id, strategy, stage, verdict, us_market_slug, "
        "       holding_side, fixture, order_key, qty_out, all_in_ev_usd, "
        "       fill_probability, ev_per_capital_hour, p_used, market_price, "
        "       detail, "
        "       extract(epoch FROM evaluated_at)::float8 AS at "
        "  FROM paper_profitability_evaluations WHERE account_id = $1 "
        "   AND evaluated_at >= $2 ORDER BY eval_id", account_id, _ts(c))]
    opp = {(r["us_market_slug"], r["holding_side"]) for r in ev
           if r["us_market_slug"]}
    events = {r["fixture"] for r in ev if r["fixture"]}
    entered = [r for r in ev if r["stage"] == "LEDGER"
               and r["verdict"] == "ENTER"]
    by_key = {r["order_key"]: r for r in entered if r["order_key"]}
    trades = {(r["us_market_slug"], r["holding_side"]) for r in entered}
    exp = sum((_num(r["all_in_ev_usd"]) or 0.0)
              * (_num(r["fill_probability"]) or 1.0) for r in entered)
    evch = [_num(r["ev_per_capital_hour"]) for r in entered
            if _num(r["ev_per_capital_hour"]) is not None]
    pos = await L.positions(conn, account_id, include_closed=True)
    closed = [p for p in pos if LC.closed_at(p) is not None]
    fwd = sorted([p for p in closed if (_num(p.get("first_fill_at")) or 0)
                  >= c], key=lambda p: LC.closed_at(p))
    hist = [p for p in closed if (_num(p.get("first_fill_at")) or 0) < c]
    # expected EV on each forward closed position (its ledger evaluation)
    eo = {(r["group_id"], r["us_market_slug"], r["holding_side"]):
          (r["idempotency_key"], float(r["filled_qty"]))
          for r in await conn.fetch(
              "SELECT group_id, us_market_slug, holding_side, "
              "       idempotency_key, filled_qty FROM paper_orders "
              " WHERE account_id=$1 AND role='ENTRY' AND direction='BUY' "
              "   AND filled_qty > 0", account_id)}
    resid, exp_closed, attrs = 0.0, 0.0, []
    for p in fwd:
        k = (p["group_id"], p["us_market_slug"], p["holding_side"])
        o = eo.get(k)
        e = by_key.get(o[0]) if o else None
        if not e or not _num(e["qty_out"]):
            continue
        scale = o[1] / float(e["qty_out"])
        x = (_num(e["all_in_ev_usd"]) or 0.0) * scale
        exp_closed += x
        resid += (_num(p.get("realized_pnl_usd")) or 0.0) - x
    for r in entered:
        a = (PBIND._j(r["detail"]) or {}).get("attribution")
        if a:
            attrs.append(a)
    cal_rows = []
    ys = await PBIND.outcomes(conn, [k[0] for k in opp])
    last: dict = {}
    for r in ev:
        if r["us_market_slug"] and _num(r["p_used"]) is not None:
            last[(r["us_market_slug"], r["holding_side"])] = r
    for k, r in last.items():
        y = ys.get(k)
        if y is not None:
            cal_rows.append({"p": _num(r["p_used"]),
                             "market": _num(r["market_price"]), "y": y})
    settled_trades = {k for k in trades if ys.get(k) is not None} | {
        (p["us_market_slug"], p["holding_side"]) for p in fwd}
    fills = await conn.fetchrow(
        "SELECT count(DISTINCT order_id) AS n, "
        "       coalesce(sum(qty * price + fee_usd), 0) AS usd "
        "  FROM paper_fills WHERE account_id=$1 AND direction='BUY' "
        "   AND filled_at >= $2", account_id, _ts(c))
    hours = max(1e-9, (float(now) - c) / 3600.0)
    # forecast: open positions at the probability their entry used (else
    # the cost per contract -- no edge assumed) + live entry orders
    open_pos = [p for p in pos if LC.closed_at(p) is None]
    p_by = {k: _num(r["p_used"]) for k, r in last.items()}
    ops = []
    for p in open_pos:
        q = _num(p.get("open_qty")) or 0.0
        cb = _num(p.get("cost_basis_usd")) or 0.0
        pp = p_by.get((p["us_market_slug"], p["holding_side"]))
        ops.append({"qty": q, "cost_basis_usd": cb,
                    "p": pp if pp is not None else (cb / q if q else 0.0)})
    live = [dict(r) for r in await conn.fetch(
        "SELECT idempotency_key, qty, filled_qty, limit_price FROM "
        " paper_orders WHERE account_id=$1 AND role='ENTRY' "
        "   AND direction='BUY' AND state = ANY($2::text[])", account_id,
        list(L.OPEN_STATES))]
    fw = []
    for o in live:
        e = by_key.get(o["idempotency_key"])
        left = float(o["qty"]) - float(o["filled_qty"])
        if e is None or left <= 0 or _num(e["p_used"]) is None:
            continue
        fw.append({"qty": left, "cost_usd": left * float(o["limit_price"]),
                   "p": _num(e["p_used"]),
                   "fill_probability": _num(e["fill_probability"])})
    cf = {}
    try:
        for r in await conn.fetch(
                "SELECT v.variant, v.verdict, count(*) AS n, "
                "       coalesce(sum(v.expected_ev_usd), 0) AS exp, "
                "       count(o.outcome_id) AS settled, "
                "       coalesce(sum(o.counterfactual_pnl_usd), 0) AS pnl "
                "  FROM paper_counterfactual_variants v LEFT JOIN "
                "       paper_counterfactual_variant_outcomes o USING "
                "       (variant_id) WHERE v.account_id=$1 "
                "   AND v.decided_at >= $2 GROUP BY 1, 2", account_id,
                _ts(c)):
            cf["%s|%s" % (r["variant"], r["verdict"])] = {
                "n": int(r["n"]), "expected_ev_usd": _r(r["exp"], 6),
                "settled": int(r["settled"]),
                "counterfactual_pnl_usd": _r(r["pnl"], 6),
                "pnl_class": "NOT_REALIZED_PNL"}
    except Exception:                                           # noqa: BLE001
        cf = {"why": "MIGRATION_311_NOT_APPLIED"}
    sh = await conn.fetchrow(
        "SELECT count(*) AS n, coalesce(sum(o.counterfactual_pnl_usd), 0) "
        "       AS pnl FROM paper_shadow_counterfactuals s JOIN "
        "       paper_shadow_counterfactual_outcomes o USING (shadow_id) "
        " WHERE s.account_id=$1 AND s.decided_at >= $2 "
        "   AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND') "
        "   AND o.filled_qty > 0", account_id, _ts(c)) \
        if await conn.fetchval(
            "SELECT to_regclass('paper_shadow_counterfactuals') IS NOT NULL"
        ) else None
    d = {"cutover": c, "cutover_basis": basis,
         "independent_opportunities": len(opp),
         "independent_events": len(events),
         "independent_trades": len(trades),
         "independent_settled_trades": len(settled_trades),
         "expected_ev_usd": exp,
         "closed_pnls_since_cutover": [_num(p.get("realized_pnl_usd")) or 0.0
                                       for p in fwd],
         "historical_realized_pnl_usd": sum(
             _num(p.get("realized_pnl_usd")) or 0.0 for p in hist),
         "ev_per_capital_hour": (sum(evch) / len(evch)) if evch else None,
         "calibration": calibration_metrics(cal_rows),
         "residual_usd": resid,
         "attribution": attribution_totals(
             attrs, realized_pnl_usd=sum(_num(p.get("realized_pnl_usd"))
                                         or 0.0 for p in fwd),
             expected_on_closed_usd=exp_closed),
         "turnover": {"entry_orders_filled": int(fills["n"] or 0),
                      "bought_usd": _r(fills["usd"], 6),
                      "bought_usd_per_hour": _r(float(fills["usd"] or 0)
                                                / hours, 6),
                      "hours_since_cutover": _r(hours, 3)},
         "capital_deployed_usd": float(fills["usd"] or 0),
         "forecast": pnl_forecast(ops, fw),
         "counterfactuals": cf,
         "shadow": ({"settled_filled": int(sh["n"]),
                     "counterfactual_pnl_usd": _r(sh["pnl"], 6),
                     "pnl_class": "NOT_REALIZED_PNL"} if sh else {})}
    return dict(scoreboard(d), computed_at=float(now))


def describe() -> dict:
    return {"version": VERSION, "authority": AUTHORITY,
            "quarantine": {
                "rules": list(QUARANTINE_RULES),
                "min_calibration_obs": QUAR_MIN_CALIBRATION_OBS,
                "brier_margin": QUAR_BRIER_MARGIN, "max_ece": QUAR_MAX_ECE,
                "min_residual_obs": QUAR_MIN_RESIDUAL_OBS,
                "residual_per_contract": -QUAR_RESIDUAL_PER_CONTRACT,
                "min_execution_fills": QUAR_MIN_EXECUTION_FILLS,
                "markout_per_contract": QUAR_MARKOUT_PER_CONTRACT,
                "min_execution_orders": QUAR_MIN_EXECUTION_ORDERS,
                "min_fill_rate": QUAR_MIN_FILL_RATE,
                "automatic_promotion": False},
            "forecast": {"simulations": FORECAST_SIMS, "seed": FORECAST_SEED},
            "scoreboard": {"min_forward_sample": MIN_FORWARD_SAMPLE,
                           "deferred_verdict": DEFERRED},
            "paper_only": True, "at": time.time()}
