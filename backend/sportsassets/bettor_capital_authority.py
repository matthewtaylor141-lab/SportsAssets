"""PAPER CAPITAL AUTHORITY AND ZERO-CAPITAL SHADOW LEARNING (migration 305).

PAPER ONLY. Nothing here raises a cap, a size, a risk limit or a live
authority, and nothing here reaches a venue. Every gate below can only REFUSE
a paper ENTRY; none can admit one another gate refused, and none changes a
lifecycle state (promotion stays with a named person, bettor_strategy_
lifecycle.transition).

── A. PAPER CAPITAL AUTHORITY ─────────────────────────────────────────────

A new PAPER ENTRY (an ENTRY BUY at bettor_paper_ledger.submit_order, under the
account lock, for EVERY strategy -- Derek, the benchmarks, the maker, the
exploration strategy) needs ALL of:

  1. the lifecycle permits entry           (bettor_strategy_lifecycle.
                                            entry_gate, unchanged)
  2. management freshness passes           (the stale-management rate in the
                                            lifecycle gate and bettor_paper_
                                            freshness.allocation_refusal,
                                            unchanged)
  3. NO PREDECLARED STOPPING RULE FIRES NOW: the lifecycle's rules are
     re-evaluated on the strategy's positions AT THE ENTRY INSTANT
     (`stopping_rules_now`), not only read from the state the evaluator
     stored up to RUN_EVERY_S (600 s) ago. ANY firing rule refuses
     (R_RULE_FIRING_AT_ENTRY); an unreadable evaluation refuses
     (R_RULES_UNREADABLE).
  4. probability / identity / settlement evidence and a fresh executable
     book with depth: the decision's capital-eligibility evaluation
     (bettor_capital_eligibility.evaluate), carried on the order as
     `capital_evidence`. Absent => refused (R_EV_NOT_EVIDENCED).
  5. EXECUTABLE EV AFTER FEES AND EXECUTION COSTS > 0, from that same
     evaluation (depth walk, per-level fees, adverse-selection estimate).
     <= 0 => refused (CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE).
  6. FORWARD ECONOMICS SUPPORT CAPITAL (`forward_verdict`, below). UNKNOWN or
     NEGATIVE => refused by name and routed to shadow evaluation.

THE FORWARD-ECONOMICS RULE (FORWARD_RULE_VERSION), stated exactly:

  * FORWARD WINDOW: evidence first observed at or after FORWARD_SINCE = the
    later of the management epoch (bettor_paper_epoch.EPOCH_START) and the
    declaration of the stopping rules (bettor_strategy_lifecycle.
    RULES_DECLARED_AT). Pre-epoch history is history: it is not forward.
  * AN OBSERVATION is a SETTLED economic outcome of the strategy, of one of
    two kinds, both counted with every loss:
      - REALIZED PAPER: a CLOSED paper position of the strategy (settled or
        fully sold) first filled inside the window: its realized P&L.
      - SHADOW: a SHADOW_COUNTERFACTUAL of the strategy decided inside the
        window whose contract SETTLED and whose counterfactual FILLED: its
        counterfactual P&L. A NO_FILL or a VOID_REFUND is not an economic
        outcome and is not counted (reported beside).
    An open position, an unsettled shadow and a decision-time EV estimate are
    NOT observations: an estimate is a model's output, not evidence.
  * UNKNOWN   fewer than MIN_FORWARD_OBSERVATIONS (= the lifecycle's own
              MIN_CLOSED_FOR_RATE_RULES, 20) observations in the window.
  * NEGATIVE  at least that many, and the net P&L of all of them is <= 0; or
              the REALIZED PAPER observations alone number at least that many
              and net < 0 (a shadow gain never outweighs realized losses).
  * POSITIVE  otherwise. Only POSITIVE supports capital deployment.
  * An unreadable evaluation refuses (R_FORWARD_UNREADABLE). Fail-closed.

  Why UNKNOWN is honest: the window starts empty for every strategy, so the
  rule refuses capital until the strategy has SETTLED evidence of its own --
  and its shadow decisions are exactly what produce that evidence without
  spending any. Shadow P&L feeds this verdict as FORWARD EVIDENCE; it is
  never realized PAPER P&L, never summed into PAPER figures, and changes no
  lifecycle state.

── B. ZERO-CAPITAL SHADOW LEARNING ───────────────────────────────────────

A decision of a strategy WITHOUT capital authority (a lifecycle no-entry
state, a firing stopping rule, forward economics UNKNOWN / NEGATIVE) that
passed every other entry condition is recorded as a SHADOW_COUNTERFACTUAL
(paper_shadow_counterfactuals): the SAME decision instant, the book actually
observed at that instant, the simulator configuration's
decision_to_execution_delay_s, the depth walk, per-level fees and the
adverse-selection estimate of bettor_capital_eligibility. It is labelled
evidence_class = SHADOW_COUNTERFACTUAL, pnl_class = NOT_REALIZED_PNL; it is
never a paper order, never reserves cash, never exposure.

When the contract settles (authoritative evidence only, xavier_management.
settlement_outcome -- the existing read path), `settle_shadows` writes the
counterfactual outcome as its own append-only row:
  MARKETABLE  the FIRST READABLE book observed in [decided + delay, expires]
              (the simulator's own window rule) walked at the limit; with no
              such observation, the decision walk charged at the IOC
              worst-case bound (every contract at the limit) and labelled so.
  RESTING     the simulator's resting rule on the observations in the
              window (liquidity strictly through the limit, queue ahead first);
              with no observation, NO_FILL (a fill is never assumed).

── C. THE ENTRY-REFUSAL CENSUS ───────────────────────────────────────────

Every refused PAPER entry (decision stage and ledger stage) is appended to
paper_entry_refusal_census with strategy, contract, side, fixture, edge, edge
shortfall, expected fees, execution-cost estimate, executable EV and the
exact refusal; `blocker_census` ranks them by unique opportunity
(profitability.opportunity_funnel.opportunity_key).

── D. THE ACCEPTANCE READ ────────────────────────────────────────────────

`acceptance_view` (GET /api/command/paper/capital-authority). Actual
profitability stays DEFERRED_FORWARD_EVIDENCE. Unrealized PAPER P&L and
open exposure are NEVER NULL WHEN COMPUTABLE (`complete_marks`: the ledger
mark, else the known settlement outcome, else the last executable exit,
else a zero-exit floor, each labelled), and the $500,000 management epoch
is reconciled to the cent (`management_reconciliation`).

── E. THE PROFITABILITY BIND (migration 309) ─────────────────────────────

`ledger_entry_authority` and `authority` carry bettor_paper_profitability_bind:
the calibrated all-in executable EV, churn control and the capacity /
capital-hour / correlation size (refuse or shrink only) run before the
forward check; the ABSOLUTE-POSITIVE CHAMPION rule (forward CI lower bound
> 0) and the REGIME AUTHORITY run after it and, like forward UNKNOWN /
NEGATIVE, route an otherwise eligible decision to a shadow counterfactual
(NO_CAPITAL_AUTHORITY).
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import time
from typing import Any

from . import bettor_capital_eligibility as CE
from . import bettor_paper_profitability_bind as PBIND
from . import bettor_strategy_lifecycle as LC

VERSION = "PAPER_CAPITAL_AUTHORITY_V1"
FORWARD_RULE_VERSION = "FORWARD_ECONOMICS_RULE_V1"
EVIDENCE_CLASS = "SHADOW_COUNTERFACTUAL"
PNL_CLASS = "NOT_REALIZED_PNL"
PROFITABILITY = "DEFERRED_FORWARD_EVIDENCE"
AUTHORITY = "PAPER_ONLY_NO_CAPITAL_AUTHORITY"

#: the management epoch, stated here (bettor_paper_epoch.EPOCH_START,
#: 2026-10-05 00:00 America/New_York; pinned equal by a test) so this module
#: imports no read model on the order path
MANAGEMENT_EPOCH_START = 1_791_172_800.0
FORWARD_SINCE = max(MANAGEMENT_EPOCH_START, LC.RULES_DECLARED_AT)
MIN_FORWARD_OBSERVATIONS = LC.MIN_CLOSED_FOR_RATE_RULES

POSITIVE, UNKNOWN, NEGATIVE = "POSITIVE", "UNKNOWN", "NEGATIVE"

R_FORWARD_UNKNOWN = "CASH_WAIT_FORWARD_ECONOMICS_UNKNOWN"
R_FORWARD_NEGATIVE = "CASH_WAIT_FORWARD_ECONOMICS_NEGATIVE"
R_FORWARD_UNREADABLE = "FORWARD_ECONOMICS_UNREADABLE"
R_RULE_FIRING_AT_ENTRY = "STRATEGY_STOPPING_RULE_FIRING_AT_ENTRY"
R_RULES_UNREADABLE = "STRATEGY_STOPPING_RULES_UNREADABLE_AT_ENTRY"
R_EV_NOT_EVIDENCED = "CASH_WAIT_EXECUTABLE_EV_NOT_EVIDENCED"
R_EV_NOT_POSITIVE = CE.R_CE_CASH_WAIT_EV_NOT_POSITIVE

FORWARD_REFUSAL = {UNKNOWN: R_FORWARD_UNKNOWN, NEGATIVE: R_FORWARD_NEGATIVE}

#: refusals that mean "no capital authority": the decision is routed to a
#: shadow counterfactual when everything else passed
NO_CAPITAL_AUTHORITY = (tuple(LC.ENTRY_STATE_REFUSAL.values())
                        + (R_RULE_FIRING_AT_ENTRY, R_FORWARD_UNKNOWN,
                           R_FORWARD_NEGATIVE)
                        # the profitability bind's regime authority and
                        # absolute-positive champion rule (migration 309)
                        + PBIND.AUTHORITY_REFUSALS)

SRC_DECISION = "DECISION_CAPITAL_GATE"
SRC_LEDGER = "LEDGER_CAPITAL_AUTHORITY"
BASIS_EXEC_BOOK = "EXECUTION_BOOK_FIRST_READABLE_IN_THE_ORDER_WINDOW"
BASIS_LIMIT_BOUND = ("NO_EXECUTION_BOOK_OBSERVED_DECISION_WALK_CHARGED_AT_"
                     "THE_IOC_LIMIT_WORST_CASE_BOUND")
BASIS_RESTING = "RESTING_RULE_ON_THE_OBSERVATIONS_IN_THE_ORDER_WINDOW"
BASIS_RESTING_NONE = "RESTING_NO_EXECUTION_BOOK_OBSERVED_NO_FILL_ASSUMED"
SETTLE_PER_PASS = 200
RUN_EVERY_S = 600.0

T_SHADOW = "paper_shadow_counterfactuals"
T_OUTCOME = "paper_shadow_counterfactual_outcomes"
T_CENSUS = "paper_entry_refusal_census"

#: ledger refusals that are not an entry decision (malformed / not paper /
#: an idempotent duplicate): not instrumented
NOT_INSTRUMENTED = ("THE_ORDER_IS_MALFORMED", "NOT_A_PAPER_IDENTIFIER",
                    "THE_PAPER_ACCOUNT_DOES_NOT_EXIST",
                    "IDEMPOTENCY_KEY_BELONGS_TO_ANOTHER_ACCOUNT")


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _ts(epoch: float):
    return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc)


def _epoch(v):
    if v is None:
        return None
    return float(v.timestamp()) if hasattr(v, "timestamp") else float(v)


def _j(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


# ═════════════════════════════════════════════════════════════════════
# THE FORWARD-ECONOMICS RULE (pure)
# ═════════════════════════════════════════════════════════════════════

def forward_verdict(paper_pnls, shadow_pnls, *,
                    min_n: int = MIN_FORWARD_OBSERVATIONS) -> dict:
    """POSITIVE / UNKNOWN / NEGATIVE from the strategy's settled forward
    observations (module docstring, FORWARD_RULE_VERSION). Pure."""
    pp = [float(x) for x in (paper_pnls or []) if _num(x) is not None]
    sp = [float(x) for x in (shadow_pnls or []) if _num(x) is not None]
    n_p, n_s = len(pp), len(sp)
    net_p, net_s = sum(pp), sum(sp)
    n, net = n_p + n_s, net_p + net_s
    allp = pp + sp
    mean = (net / n) if n else None
    sd = (math.sqrt(sum((x - mean) ** 2 for x in allp) / (n - 1))
          if n >= 2 else None)
    half = (LC.CI_Z * sd / math.sqrt(n)) if sd is not None else None
    if n < min_n:
        verdict = UNKNOWN
        why = ("%d settled forward observation(s) (%d realized PAPER, %d "
               "SHADOW) < %d: forward economics are not measured" % (
                   n, n_p, n_s, min_n))
    elif net <= 0:
        verdict = NEGATIVE
        why = "net forward P&L %.6f over %d observations is <= 0" % (net, n)
    elif n_p >= min_n and net_p < 0:
        verdict = NEGATIVE
        why = ("realized PAPER forward P&L %.6f over %d closed positions is "
               "negative; shadow gains never outweigh realized losses"
               % (net_p, n_p))
    else:
        verdict = POSITIVE
        why = "net forward P&L %.6f > 0 over %d observations" % (net, n)
    return {"rule_version": FORWARD_RULE_VERSION, "verdict": verdict,
            "why": why, "min_observations": min_n,
            "forward_since": FORWARD_SINCE,
            "observations": n, "net_pnl_usd": round(net, 6) if n else None,
            "mean_pnl_usd": None if mean is None else round(mean, 6),
            "pnl_ci95_low": None if half is None else round(mean - half, 6),
            "pnl_ci95_high": None if half is None else round(mean + half, 6),
            "realized_paper": {"observations": n_p,
                               "net_pnl_usd": round(net_p, 6) if n_p else None,
                               "pnl_class": "REALIZED_PAPER_PNL"},
            "shadow": {"observations": n_s,
                       "net_pnl_usd": round(net_s, 6) if n_s else None,
                       "evidence_class": EVIDENCE_CLASS,
                       "pnl_class": PNL_CLASS},
            "refusal": FORWARD_REFUSAL.get(verdict)}


def forward_paper_pnls(positions: list, strategy: str, *,
                       since: float = FORWARD_SINCE,
                       default_strategy: str = "DEREK_ENTRY_POLICY_V2"
                       ) -> list:
    """Realized P&L of every CLOSED paper position of `strategy` first
    filled at or after `since` (losses included). Pure."""
    out = []
    for p in positions or []:
        if LC.strategy_of(p, default_strategy) != strategy:
            continue
        ff = _num(p.get("first_fill_at"))
        if ff is None or ff < since or LC.closed_at(p) is None:
            continue
        out.append(_num(p.get("realized_pnl_usd")) or 0.0)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE EXECUTABLE EVIDENCE (pure; reuses bettor_capital_eligibility)
# ═════════════════════════════════════════════════════════════════════

def evaluate_executable(*, p, levels, qty, limit, fee_fn, at, settlement,
                        identity, size_factor: float = 1.0,
                        adverse_selection_per_contract=None) -> dict:
    """bettor_capital_eligibility.evaluate with the paper ledger's fee
    schedule (`fee_fn` as the ledger takes it). Never raises."""
    from . import bettor_paper_ledger as L
    return CE.evaluate(
        p=p, levels=levels, qty=qty, limit=limit,
        fee_fn=lambda q, px: float(L._fee(fee_fn, q, px, at)),
        settlement=settlement, identity=identity,
        adverse_selection_per_contract=adverse_selection_per_contract,
        size_factor=size_factor)


def capital_evidence(ce: dict | None, *, p=None, limit=None,
                     threshold_edge_pp=None, basis: str = "",
                     levels=None, book_obs_id=None,
                     book_observed_at=None) -> dict | None:
    """THE EXECUTABLE-EV EVIDENCE an ENTRY order carries to the ledger
    (`order["capital_evidence"]`), normalized from a capital-eligibility
    result. Pure; None without a result."""
    if not isinstance(ce, dict):
        return None
    best = _num(ce.get("best_price"))
    pv = _num(p)
    gross = (round((pv - best) * 100.0, 9)
             if pv is not None and best is not None else None)
    thr = _num(threshold_edge_pp)
    return {
        "version": CE.VERSION, "basis": basis,
        "capital_eligible": ce.get("capital_eligible") is True,
        "refusals": list(ce.get("refusals") or []),
        "p": pv, "limit": _num(limit), "qty": ce.get("filled_qty") or ce.get(
            "qty"),
        "best_price": best, "vwap": _num(ce.get("vwap")),
        "gross_edge_pp": gross, "threshold_edge_pp": thr,
        "edge_shortfall_pp": (round(max(0.0, thr - gross), 9)
                              if thr is not None and gross is not None
                              else None),
        "fills": ce.get("fills") or [],
        "cost_usd": _num(ce.get("cost_usd")),
        "fees_usd": _num(ce.get("fees_usd")),
        "slippage_usd": _num(ce.get("slippage_vs_top_usd")),
        "adverse_selection_usd": _num(ce.get("adverse_selection_usd")),
        "adverse_selection_basis": ce.get("adverse_selection_basis"),
        "total_executable_ev_usd": _num(ce.get("total_executable_ev_usd")),
        "levels": list(levels or [])[:20],
        "book_obs_id": book_obs_id, "book_observed_at": book_observed_at,
        # the profitability bind's restatement (migration 309): the bound
        # figures above, the policy's own walk kept here so the ledger
        # re-derives the SAME bound size (never a second shrink)
        **({"pre_bind": ce["pre_bind"],
            "profitability_bind": ce.get("profitability_bind"),
            "bind_inputs": ce.get("bind_inputs")}
           if isinstance(ce.get("pre_bind"), dict) else {})}


def missing_ev_evidence_refusal(o: dict) -> dict | None:
    """THE LEDGER'S REQUIREMENT that an ENTRY BUY carry its executable-EV
    evidence. Production: refused when absent. (The test suite's conftest
    lifts only this absent-evidence check for proofs written before it,
    exactly as it seeds the other fail-closed gates; tests/test_capital_
    authority.py runs the production function.)"""
    if not isinstance(o.get("capital_evidence"), dict):
        return {"refusal": R_EV_NOT_EVIDENCED,
                "why": ("an ENTRY order must carry its capital-eligibility "
                        "evaluation (executable depth, identity, settlement, "
                        "fees, executable EV)")}
    return None


def ev_refusal(o: dict) -> dict | None:
    """Refuses an ENTRY whose carried evidence is not capital-eligible or
    whose executable EV is not > 0. Pure."""
    missing = missing_ev_evidence_refusal(o)
    if missing:
        return missing
    ev = o.get("capital_evidence")
    if not isinstance(ev, dict):
        return None
    x = _num(ev.get("total_executable_ev_usd"))
    if ev.get("capital_eligible") is not True:
        rs = [r for r in ev.get("refusals") or [] if r]
        return {"refusal": rs[0] if rs else R_EV_NOT_POSITIVE,
                "capital_evidence_refusals": rs,
                "total_executable_ev_usd": x}
    if x is None or x <= 0:
        return {"refusal": R_EV_NOT_POSITIVE, "total_executable_ev_usd": x}
    return None


# ═════════════════════════════════════════════════════════════════════
# THE DATABASE READS
# ═════════════════════════════════════════════════════════════════════

async def schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('paper_shadow_counterfactuals') IS NOT NULL "
            "   AND to_regclass('paper_shadow_counterfactual_outcomes') "
            "       IS NOT NULL "
            "   AND to_regclass('paper_entry_refusal_census') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


async def _positions(conn, account_id: str) -> list:
    from . import bettor_paper_ledger as L
    return await L.positions(conn, account_id, include_closed=True)


async def shadow_pnls(conn, account_id: str, strategy: str, *,
                      since: float = FORWARD_SINCE) -> list:
    """Counterfactual P&L of every SETTLED, FILLED shadow of the strategy
    decided at or after `since` (NO_FILL / VOID excluded)."""
    rows = await conn.fetch(
        "SELECT o.counterfactual_pnl_usd FROM paper_shadow_counterfactuals s "
        "  JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id) "
        " WHERE s.account_id = $1 AND s.strategy = $2 AND s.decided_at >= $3 "
        "   AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND') "
        "   AND o.filled_qty > 0", account_id, strategy, _ts(since))
    return [float(r["counterfactual_pnl_usd"]) for r in rows]


async def forward_economics(conn, account_id: str, strategy: str, *,
                            now: float, positions: list | None = None
                            ) -> dict:
    """The strategy's forward-economics verdict. Fail-closed: an unreadable
    evaluation is {ok: False} (callers refuse)."""
    from . import bettor_paper_ledger as L
    try:
        if not await schema(conn):
            return {"ok": False, "why": "MIGRATION_305_NOT_APPLIED"}
        pos = positions if positions is not None else await _positions(
            conn, account_id)
        pp = forward_paper_pnls(pos, strategy,
                                default_strategy=L.DEFAULT_STRATEGY)
        async with conn.transaction():
            sp = await shadow_pnls(conn, account_id, strategy)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(exc).__name__,
                                                str(exc)[:160])}
    return dict(forward_verdict(pp, sp), ok=True, as_of=float(now))


async def stopping_rules_now(conn, account_id: str, strategy: str, *,
                             now: float, positions: list | None = None
                             ) -> dict:
    """The lifecycle's PREDECLARED rules evaluated on the strategy's
    positions AT `now` (not the stored state). {ok, state, firing}."""
    from . import bettor_paper_ledger as L
    try:
        cur = await LC.current_state(conn, account_id, strategy)
        if not cur.get("ok"):
            return {"ok": False, "why": cur.get("why")}
        pos = positions if positions is not None else await _positions(
            conn, account_id)
        mine = [p for p in pos
                if LC.strategy_of(p, L.DEFAULT_STRATEGY) == strategy]
        m = LC.metrics(mine, now=now)
        fwd = LC.metrics(mine, now=now, window_days=3650.0,
                         since=LC.forward_since(cur.get("since")))
        fired = LC.rules_firing(m, current=cur["state"], forward=fwd)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(exc).__name__,
                                                str(exc)[:160])}
    return {"ok": True, "state": cur["state"], "firing": fired,
            "rolling": LC._brief(m)}


async def authority(conn, *, account_id: str, strategy: str, now: float,
                    positions: list | None = None,
                    context: dict | None = None) -> dict:
    """Rules firing NOW + forward economics, for one strategy, then (the
    profitability bind, migration 309) the ABSOLUTE-POSITIVE CHAMPION rule
    and -- with `context` (the entry's descriptor: sport / family /
    regime) -- the REGIME AUTHORITY. {refusal or None, rules, forward,
    bind_authority}. Fail-closed."""
    try:
        pos = positions if positions is not None else await _positions(
            conn, account_id)
    except Exception as exc:                                    # noqa: BLE001
        return {"refusal": R_RULES_UNREADABLE,
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    rules = await stopping_rules_now(conn, account_id, strategy, now=now,
                                     positions=pos)
    if not rules.get("ok"):
        return {"refusal": R_RULES_UNREADABLE, "rules": rules}
    if rules["firing"]:
        return {"refusal": R_RULE_FIRING_AT_ENTRY, "rules": rules,
                "state": rules.get("state")}
    fwd = await forward_economics(conn, account_id, strategy, now=now,
                                  positions=pos)
    if not fwd.get("ok"):
        return {"refusal": R_FORWARD_UNREADABLE, "rules": rules,
                "forward": fwd, "state": rules.get("state")}
    if fwd.get("refusal"):
        return {"refusal": fwd.get("refusal"), "rules": rules,
                "forward": fwd, "state": rules.get("state")}
    try:
        extra = await PBIND.authority_extra(
            conn, account_id=account_id, strategy=strategy, forward=fwd,
            context=context, now=now, positions=pos)
    except Exception as exc:                                    # noqa: BLE001
        extra = {"refusal": PBIND.R_REGIME_UNREADABLE,
                 "why": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    return {"refusal": extra.get("refusal"), "rules": rules, "forward": fwd,
            "state": rules.get("state"), "bind_authority": extra}


async def ledger_entry_authority(conn, o: dict, *, at: float,
                                 fee_fn=None) -> dict:
    """UNDER THE ACCOUNT LOCK (submit_order), for one ENTRY BUY that passed
    the lifecycle gate: executable-EV evidence > 0; THE PROFITABILITY BIND
    (bettor_paper_profitability_bind.entry_bind: calibrated probability, all-in
    executable EV with the learned execution costs and residual haircut,
    churn control, capacity / capital-hour / correlation sizing -- refuse or
    shrink only); no stopping rule firing now; forward economics POSITIVE;
    the absolute-positive champion rule; the regime authority. {refusal:
    None, qty, summary} or the refusal. Every evaluation is recorded
    (migration 309). Fail-closed."""
    chk = ev_refusal(o)
    if chk:
        return chk
    ev = o.get("capital_evidence")
    b = await PBIND.entry_bind(
        conn, account_id=o["account_id"], strategy=o["strategy"],
        evidence=ev, qty_in=_num(o.get("qty")),
        slug=o.get("us_market_slug"), side=o.get("holding_side"),
        fixture=o.get("fixture"), order_type=o.get("order_type"), at=at,
        fee_fn=fee_fn, qty_cap=_num(o.get("qty")),
        order_key=o.get("idempotency_key"))
    bsum = PBIND.summary(b)
    bev = PBIND.bound_evidence(ev, b)

    async def _record(refusal):
        await PBIND.record_evaluation(
            conn, account_id=o["account_id"], strategy=o["strategy"],
            stage="LEDGER", b=b, refusal=refusal,
            decision_id=o.get("decision_id"),
            order_key=o.get("idempotency_key"),
            slug=o.get("us_market_slug"), side=o.get("holding_side"),
            fixture=o.get("fixture"), at=at)

    if b.get("refusal"):
        await _record(b["refusal"])
        return {"refusal": b["refusal"], "profitability_bind": bsum,
                "why": b.get("why"), "churn": b.get("churn"),
                "capital_authority": {"version": VERSION,
                                      "profitability_bind": bsum}}
    got = await authority(conn, account_id=o["account_id"],
                          strategy=o["strategy"], now=at,
                          context=b.get("descriptor"))
    summary = {"version": VERSION,
               "forward_verdict": (got.get("forward") or {}).get("verdict"),
               "forward_observations": (got.get("forward") or {}).get(
                   "observations"),
               "rules_firing": [r["rule_id"] for r in (
                   got.get("rules") or {}).get("firing") or []],
               "champion": ((got.get("bind_authority") or {}).get(
                   "champion") or {}).get("champion"),
               "regime": (b.get("descriptor") or {}).get("regime"),
               "regime_verdict": (((got.get("bind_authority") or {}).get(
                   "regime") or {}).get("forward") or {}).get("verdict"),
               "profitability_bind": bsum,
               "total_executable_ev_usd": (
                   (bev or {}).get("total_executable_ev_usd")
                   if b.get("all_in") else (ev or {}).get(
                       "total_executable_ev_usd"))}
    await _record(got.get("refusal"))
    if got.get("refusal"):
        return dict(got, capital_authority=summary, bound_evidence=bev,
                    profitability_bind=bsum)
    return {"refusal": None, "capital_authority": summary,
            "qty": b.get("qty"), "profitability_bind": bsum}


# ═════════════════════════════════════════════════════════════════════
# THE WRITES (each under its own savepoint; never raise)
# ═════════════════════════════════════════════════════════════════════

def _sim_cfg(ctx: dict | None) -> dict:
    cfg = (ctx or {}).get("config") or {}
    sim = cfg.get("simulator")
    if not sim:
        from . import bettor_paper_session as S
        cfg = S.default_config()
        sim = cfg["simulator"]
    return {"delay_s": float(sim["decision_to_execution_delay_s"]),
            "ttl_s": float(sim.get("marketable_ttl_s") or 90.0),
            "simulator_version": cfg.get("simulator_version")}


async def record_shadow(conn, *, account_id: str, strategy: str,
                        decision_id, order_key, source: str,
                        capital_refusal: str, lifecycle_state,
                        slug, holding_side, fixture, payout_event,
                        decided_at: float, delay_s: float, expires_at: float,
                        simulator_version, evidence: dict,
                        order_type: str = "MARKETABLE",
                        extra: dict | None = None) -> dict:
    """ONE SHADOW_COUNTERFACTUAL, idempotent on strategy + decision / order
    key. Recorded only when the decision passed every other entry condition
    (its evidence capital-eligible, executable EV > 0). Never a paper order;
    never raises."""
    ev = evidence or {}
    x = _num(ev.get("total_executable_ev_usd"))
    qty = _num(ev.get("qty"))
    lim = _num(ev.get("limit"))
    p = _num(ev.get("p"))
    if ev.get("capital_eligible") is not True or x is None or x <= 0 \
            or not qty or qty <= 0 or lim is None or p is None \
            or not slug or holding_side not in ("LONG", "SHORT"):
        return {"recorded": False,
                "why": "NOT_OTHERWISE_ELIGIBLE_NO_SHADOW_COUNTERFACTUAL",
                "total_executable_ev_usd": x}
    key = "%s:%s" % (strategy, decision_id or order_key)
    try:
        if not await schema(conn):
            return {"recorded": False, "why": "MIGRATION_305_NOT_APPLIED"}
        elig = float(decided_at) + float(delay_s)
        async with conn.transaction():
            sid = await conn.fetchval(
                "INSERT INTO paper_shadow_counterfactuals (shadow_key, "
                " account_id, strategy, decision_id, order_key, source, "
                " capital_refusal, lifecycle_state, us_market_slug, "
                " holding_side, fixture, payout_event, order_type, "
                " decided_at, eligible_at, expires_at, "
                " decision_to_execution_delay_s, simulator_version, p, "
                " limit_price, qty, book_obs_id, book_observed_at, levels, "
                " fills, vwap, cost_usd, fees_usd, slippage_usd, "
                " adverse_selection_usd, total_executable_ev_usd, evidence) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,"
                " $16,$17,$18,$19,$20,$21,$22,$23,$24::jsonb,$25::jsonb,$26,"
                " $27,$28,$29,$30,$31,$32::jsonb) "
                "ON CONFLICT (shadow_key) DO NOTHING RETURNING shadow_id",
                key, account_id, strategy, decision_id, order_key, source,
                capital_refusal, lifecycle_state, slug, holding_side,
                fixture, payout_event, order_type, _ts(decided_at),
                _ts(elig), _ts(max(float(expires_at), elig)), float(delay_s),
                simulator_version, p, lim, qty, ev.get("book_obs_id"),
                (None if ev.get("book_observed_at") is None
                 else _ts(float(ev["book_observed_at"]))),
                json.dumps(ev.get("levels") or [], default=str),
                json.dumps(ev.get("fills") or [], default=str),
                _num(ev.get("vwap")), _num(ev.get("cost_usd")) or 0.0,
                _num(ev.get("fees_usd")) or 0.0, _num(ev.get("slippage_usd")),
                _num(ev.get("adverse_selection_usd")) or 0.0, x,
                json.dumps(dict(ev, **(extra or {}),
                                evidence_class=EVIDENCE_CLASS,
                                pnl_class=PNL_CLASS), default=str))
    except Exception as exc:                                    # noqa: BLE001
        return {"recorded": False, "why": "%s: %s" % (type(exc).__name__,
                                                      str(exc)[:160])}
    return {"recorded": sid is not None, "duplicate": sid is None,
            "shadow_id": sid, "shadow_key": key,
            "evidence_class": EVIDENCE_CLASS, "pnl_class": PNL_CLASS,
            "total_executable_ev_usd": x}


async def record_refusal(conn, *, account_id: str, strategy: str,
                         stage: str, refusal, refusals=None,
                         decision_id=None, order_key=None, slug=None,
                         holding_side=None, fixture=None, line=None,
                         scope=None, p=None, best_price=None,
                         threshold_edge_pp=None, evidence: dict | None = None,
                         expected_fees_usd=None, executable_ev_usd=None,
                         qty=None, limit_price=None, at: float,
                         detail: dict | None = None) -> int | None:
    """APPEND ONE REFUSED ENTRY to the census (migration 305). Edge = p -
    best price (pp); shortfall = max(0, threshold - edge). Fees, slippage,
    adverse selection and executable EV come from the capital-eligibility
    evidence when the decision got that far, else from the policy's own
    estimate. Never raises; None when absent or failed."""
    if not refusal:
        return None
    ev = evidence or {}
    pv = _num(p) if p is not None else _num(ev.get("p"))
    bp = _num(best_price) if best_price is not None else _num(
        ev.get("best_price"))
    gross = (round((pv - bp) * 100.0, 9)
             if pv is not None and bp is not None else None)
    thr = _num(threshold_edge_pp) if threshold_edge_pp is not None else _num(
        ev.get("threshold_edge_pp"))
    short = (round(max(0.0, thr - gross), 9)
             if thr is not None and gross is not None else None)
    fees = _num(ev.get("fees_usd"))
    if fees is None:
        fees = _num(expected_fees_usd)
    x = _num(ev.get("total_executable_ev_usd"))
    if x is None:
        x = _num(executable_ev_usd)
    try:
        if not await schema(conn):
            return None
        async with conn.transaction():
            return await conn.fetchval(
                "INSERT INTO paper_entry_refusal_census (account_id, "
                " strategy, stage, decision_id, order_key, refusal, refusals,"
                " us_market_slug, holding_side, fixture, line, scope, p, "
                " best_price, gross_edge_pp, threshold_edge_pp, "
                " edge_shortfall_pp, expected_fees_usd, slippage_usd, "
                " adverse_selection_usd, total_executable_ev_usd, qty, "
                " limit_price, detail, refused_at) VALUES ($1,$2,$3,$4,$5,$6,"
                " $7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,"
                " $22,$23,$24::jsonb,$25) RETURNING refusal_id",
                account_id, strategy, stage, decision_id, order_key,
                str(refusal), [str(r) for r in (refusals or [refusal]) if r],
                slug, holding_side, fixture,
                None if line is None else str(line),
                None if scope is None else str(scope), pv, bp, gross, thr,
                short, fees, _num(ev.get("slippage_usd")),
                _num(ev.get("adverse_selection_usd")), x,
                _num(qty) if qty is not None else _num(ev.get("qty")),
                (_num(limit_price) if limit_price is not None
                 else _num(ev.get("limit"))),
                json.dumps(detail or {}, default=str), _ts(at))
    except Exception:                                           # noqa: BLE001
        return None


async def after_ledger_refusal(conn, o: dict, got: dict, *, at: float
                               ) -> dict:
    """A REFUSED ENTRY BUY at the ledger: the census row, and -- when the
    refusal is a missing capital authority and the order's own evidence is
    capital-eligible -- its SHADOW_COUNTERFACTUAL. Never raises."""
    out: dict[str, Any] = {}
    refusal = got.get("refusal")
    if not refusal or refusal in NOT_INSTRUMENTED or got.get("duplicate"):
        return out
    strategy = o.get("strategy") or "DEREK_ENTRY_POLICY_V2"
    ev = o.get("capital_evidence") if isinstance(
        o.get("capital_evidence"), dict) else {}
    if isinstance(got.get("bound_evidence"), dict):
        # the order's evidence restated at the profitability bind's size
        ev = got["bound_evidence"]
    elif refusal in NO_CAPITAL_AUTHORITY and ev and \
            got.get("profitability_bind") is None:
        # a refusal before the bind ran (the lifecycle gate): the shadow is
        # evaluated under the same bind, and none is recorded when the
        # bind's economics refuse the decision itself
        b = await PBIND.entry_bind(
            conn, account_id=o["account_id"], strategy=strategy, evidence=ev,
            qty_in=_num(o.get("qty")), slug=o.get("us_market_slug"),
            side=o.get("holding_side"), fixture=o.get("fixture"),
            order_type=o.get("order_type"), at=at)
        out["profitability_bind"] = PBIND.summary(b)
        if b.get("refusal"):
            ev = dict(ev, capital_eligible=False,
                      refusals=[b["refusal"]])
        else:
            ev = PBIND.bound_evidence(ev, b)
    label = o.get("label") if isinstance(o.get("label"), dict) else {}
    out["census_id"] = await record_refusal(
        conn, account_id=o["account_id"], strategy=strategy, stage="LEDGER",
        refusal=refusal, decision_id=o.get("decision_id"),
        order_key=o.get("idempotency_key"), slug=o.get("us_market_slug"),
        holding_side=o.get("holding_side"), fixture=o.get("fixture"),
        line=label.get("line"), scope=label.get("scope"), evidence=ev,
        qty=o.get("qty"), limit_price=o.get("limit_price"), at=at,
        detail={k: v for k, v in got.items()
                if k in ("why", "rules", "forward", "lifecycle", "cap",
                         "capital_authority", "state", "profitability_bind",
                         "bind_authority", "churn")})
    if refusal in NO_CAPITAL_AUTHORITY:
        decided = _num(o.get("decided_at")) or at
        elig = _num(o.get("eligible_at")) or decided
        exp = _num(o.get("expires_at")) or elig
        out["shadow"] = await record_shadow(
            conn, account_id=o["account_id"], strategy=strategy,
            decision_id=o.get("decision_id"),
            order_key=o.get("idempotency_key"), source=SRC_LEDGER,
            capital_refusal=refusal,
            lifecycle_state=(got.get("lifecycle") or {}).get("state")
            or got.get("state"),
            slug=o.get("us_market_slug"),
            holding_side=o.get("holding_side"), fixture=o.get("fixture"),
            payout_event=label.get("payout_event"), decided_at=decided,
            delay_s=max(0.0, elig - decided), expires_at=exp,
            simulator_version=o.get("simulator_version"), evidence=ev,
            order_type=("RESTING" if o.get("order_type") == "RESTING"
                        else "MARKETABLE"),
            extra={"queue_ahead_qty": o.get("queue_ahead_qty")})
    return out


async def shadow_from_decision(conn, ctx: dict, *, strategy: str,
                               decision_id, cand: dict, side, ce: dict, p,
                               limit, levels, at: float, refusal: str,
                               lifecycle_state, threshold_edge_pp=None,
                               book: dict | None = None) -> dict:
    """The DECISION-stage shadow (paper_derek.capital_gate, shared by Derek
    and the benchmark policies): `ce` is the capital-eligibility evaluation
    of the same decision at its intended size. Never raises."""
    sim = _sim_cfg(ctx)
    ev = capital_evidence(ce, p=p, limit=limit,
                          threshold_edge_pp=threshold_edge_pp,
                          basis="DECISION_CAPITAL_GATE", levels=levels,
                          book_obs_id=(book or {}).get("book_obs_id"),
                          book_observed_at=(book or {}).get("observed_at"))
    return await record_shadow(
        conn, account_id=ctx["account_id"], strategy=strategy,
        decision_id=decision_id, order_key=None, source=SRC_DECISION,
        capital_refusal=refusal, lifecycle_state=lifecycle_state,
        slug=cand.get("us_market_slug"), holding_side=side,
        fixture=cand.get("fixture"), payout_event=cand.get("payout_event"),
        decided_at=at, delay_s=sim["delay_s"], expires_at=at + sim["ttl_s"],
        simulator_version=sim["simulator_version"], evidence=ev or {})


# ═════════════════════════════════════════════════════════════════════
# SETTLING THE SHADOWS (the existing settlement read path)
# ═════════════════════════════════════════════════════════════════════

def _fee_usd(q, px, at) -> float:
    from . import bettor_paper_ledger as L
    return float(L._fee(None, q, px, at))


async def execution_fill(conn, r: dict) -> dict:
    """The counterfactual fill of one shadow row, by the simulator's own
    window rules on the books actually observed after the delay."""
    from . import bettor_paper_simulator as SIM
    slug, side = r["us_market_slug"], r["holding_side"]
    lim, qty = float(r["limit_price"]), float(r["qty"])
    at = _epoch(r["decided_at"])
    if r.get("order_type") == "RESTING":
        rows = await conn.fetch(
            "SELECT * FROM paper_book_observations WHERE us_market_slug=$1 "
            "   AND observed_at >= $2 AND observed_at <= $3 "
            "   AND coalesce(error, '') = '' ORDER BY observed_at, obs_id "
            " LIMIT 500", slug, r["eligible_at"], r["expires_at"])
        if not rows:
            return {"basis": BASIS_RESTING_NONE, "filled": 0.0, "cost": 0.0,
                    "fees": 0.0, "obs_id": None}
        ev = _j(r.get("evidence")) or {}
        queue = _num(ev.get("queue_ahead_qty")) or 0.0
        filled, cost, fees, first = 0.0, 0.0, 0.0, None
        for row in rows:
            lv = SIM.levels_for(SIM._md(row), direction="BUY",
                                holding_side=side)
            got = SIM.resting_cross(lv["levels"], consumed={}, limit=lim,
                                    direction="BUY", queue_ahead=queue,
                                    remaining=qty - filled)
            queue = got["queue_ahead_after"]
            for t in got["takes"]:
                # a resting order fills at ITS OWN limit
                filled += float(t["take"])
                cost += float(t["take"]) * lim
                fees += _fee_usd(t["take"], lim, at)
                first = first or row["obs_id"]
            if filled >= qty - 1e-9:
                break
        return {"basis": BASIS_RESTING, "filled": round(filled, 6),
                "cost": cost, "fees": fees, "obs_id": first}
    w = await SIM.window_observations(conn, {
        "us_market_slug": slug, "eligible_at": r["eligible_at"],
        "expires_at": r["expires_at"]})
    row = w.get("readable")
    if row is None:
        return {"basis": BASIS_LIMIT_BOUND, "filled": qty,
                "cost": float(r["cost_usd"]) + float(
                    r["adverse_selection_usd"]),
                "fees": float(r["fees_usd"]), "obs_id": None}
    lv = SIM.levels_for(SIM._md(row), direction="BUY", holding_side=side)
    got = SIM.walk(lv["levels"], consumed={}, limit=lim, qty=qty,
                   direction="BUY", allow_partial=True)
    cost = sum(float(t["take"]) * float(t["price"]) for t in got["takes"])
    fees = sum(_fee_usd(t["take"], t["price"], at) for t in got["takes"])
    return {"basis": BASIS_EXEC_BOOK, "filled": float(got["filled"]),
            "cost": cost, "fees": fees, "obs_id": row["obs_id"]}


def counterfactual_pnl(*, outcome: str, payout_per_contract, filled: float,
                       cost: float, fees: float) -> float:
    """Pure. A VOID_REFUND refunds the cost (and the fees): 0."""
    if filled <= 0 or outcome == "VOID_REFUND":
        return 0.0
    return float(filled) * float(payout_per_contract or 0.0) - cost - fees


async def settle_shadows(conn, *, now: float, account_id: str | None = None,
                         limit: int = SETTLE_PER_PASS,
                         settlement_fn=None) -> dict:
    """Write the counterfactual OUTCOME of every unsettled shadow whose
    contract has authoritative settlement evidence. Append-only; idempotent
    (one outcome per shadow)."""
    out = {"examined": 0, "settled": 0, "pending": 0, "errors": 0}
    if not await schema(conn):
        return dict(out, refusal="MIGRATION_305_NOT_APPLIED")
    if settlement_fn is None:
        from .agents import xavier_management as XM
        settlement_fn = XM.settlement_outcome
    rows = await conn.fetch(
        "SELECT s.* FROM paper_shadow_counterfactuals s "
        "  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)"
        " WHERE o.shadow_id IS NULL AND ($1::text IS NULL OR s.account_id=$1)"
        "   AND s.decided_at <= $2 ORDER BY s.decided_at LIMIT $3",
        account_id, _ts(now), int(limit))
    for rr in rows:
        r = dict(rr)
        out["examined"] += 1
        try:
            st = await settlement_fn(conn, r["us_market_slug"],
                                     r["holding_side"])
            if not st or st.get("outcome") is None:
                out["pending"] += 1
                continue
            fill = await execution_fill(conn, r)
            outcome = st["outcome"] if fill["filled"] > 0 else "NO_FILL"
            pnl = counterfactual_pnl(
                outcome=outcome, payout_per_contract=st.get(
                    "payout_per_contract"), filled=fill["filled"],
                cost=fill["cost"], fees=fill["fees"])
            async with conn.transaction():
                got = await conn.fetchval(
                    "INSERT INTO paper_shadow_counterfactual_outcomes "
                    " (shadow_id, outcome, payout_per_contract, "
                    " execution_basis, execution_obs_id, filled_qty, "
                    " exec_cost_usd, exec_fees_usd, counterfactual_pnl_usd, "
                    " evidence, settled_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,"
                    " $9,$10::jsonb,$11) ON CONFLICT (shadow_id) DO NOTHING "
                    "RETURNING outcome_id",
                    r["shadow_id"], outcome,
                    _num(st.get("payout_per_contract")), fill["basis"],
                    fill["obs_id"], fill["filled"], round(fill["cost"], 6),
                    round(fill["fees"], 6), round(pnl, 6),
                    json.dumps({"settlement": st.get("evidence"),
                                "settlement_outcome": st.get("outcome"),
                                "evidence_class": EVIDENCE_CLASS,
                                "pnl_class": PNL_CLASS}, default=str),
                    _ts(now))
            if got is not None:
                out["settled"] += 1
        except Exception as exc:                                # noqa: BLE001
            out["errors"] += 1
            out.setdefault("why", "%s: %s" % (type(exc).__name__,
                                              str(exc)[:120]))
    return out


_LAST_RUN: dict = {}


async def step(conn, ctx: dict) -> dict:
    """THE PAPER PASS STEP: settle shadow counterfactuals, at most every
    RUN_EVERY_S. Records evidence only; never places, cancels or sizes an
    order."""
    acct = ctx.get("account_id")
    now = float(ctx["now"])
    last = _LAST_RUN.get(acct)
    if last is not None and 0 <= now - last < RUN_EVERY_S:
        return {"ran": False, "why": "RAN_WITHIN_RUN_EVERY_S"}
    _LAST_RUN[acct] = now
    got = await settle_shadows(conn, now=now, account_id=acct)
    return dict(got, ran=True)


# ═════════════════════════════════════════════════════════════════════
# THE READ MODELS
# ═════════════════════════════════════════════════════════════════════

async def shadow_summary(conn, account_id: str, *, since: float = 0.0
                         ) -> dict:
    """FORWARD SHADOW EVIDENCE BY STRATEGY -- labelled SHADOW_COUNTERFACTUAL
    / NOT_REALIZED_PNL, never summed with PAPER P&L."""
    rows = await conn.fetch(
        "SELECT s.strategy, s.capital_refusal, s.total_executable_ev_usd, "
        "       o.outcome, o.filled_qty, o.counterfactual_pnl_usd, "
        "       o.execution_basis "
        "  FROM paper_shadow_counterfactuals s "
        "  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)"
        " WHERE s.account_id = $1 AND s.decided_at >= $2",
        account_id, _ts(since))
    by: dict = {}
    for r in rows:
        b = by.setdefault(r["strategy"], {
            "recorded": 0, "settled": 0, "unsettled": 0, "filled": 0,
            "no_fill": 0, "void": 0, "wins": 0, "losses": 0, "pnls": [],
            "expected_ev_at_decision_usd": 0.0, "by_refusal": {},
            "by_execution_basis": {}})
        b["recorded"] += 1
        b["expected_ev_at_decision_usd"] += float(r["total_executable_ev_usd"])
        b["by_refusal"][r["capital_refusal"]] = b["by_refusal"].get(
            r["capital_refusal"], 0) + 1
        if r["outcome"] is None:
            b["unsettled"] += 1
            continue
        b["settled"] += 1
        b["by_execution_basis"][r["execution_basis"]] = b[
            "by_execution_basis"].get(r["execution_basis"], 0) + 1
        if r["outcome"] == "NO_FILL":
            b["no_fill"] += 1
        elif r["outcome"] == "VOID_REFUND":
            b["void"] += 1
        else:
            b["filled"] += 1
            x = float(r["counterfactual_pnl_usd"])
            b["pnls"].append(x)
            b["wins"] += x > 0
            b["losses"] += x < 0
    out = {}
    for s, b in by.items():
        pn = b.pop("pnls")
        n = len(pn)
        mean = sum(pn) / n if n else None
        sd = (math.sqrt(sum((x - mean) ** 2 for x in pn) / (n - 1))
              if n >= 2 else None)
        half = LC.CI_Z * sd / math.sqrt(n) if sd is not None else None
        out[s] = dict(b, evidence_class=EVIDENCE_CLASS, pnl_class=PNL_CLASS,
                      expected_ev_at_decision_usd=round(
                          b["expected_ev_at_decision_usd"], 6),
                      settled_filled_observations=n,
                      counterfactual_pnl_usd=round(sum(pn), 6) if n else None,
                      mean_counterfactual_pnl_usd=(None if mean is None
                                                   else round(mean, 6)),
                      pnl_ci95_low=(None if half is None
                                    else round(mean - half, 6)),
                      not_realized_paper_pnl=True)
    return out


def shadow_forward_metrics(row: dict | None) -> dict:
    """A strategy's shadow summary in the shape bettor_strategy_lifecycle.
    restore_evidence / promotion_evidence read -- ADVISORY forward evidence
    for the named-person governance, never an automatic transition."""
    r = row or {}
    return {"closed_positions": r.get("settled_filled_observations") or 0,
            "realized_pnl_usd": r.get("counterfactual_pnl_usd"),
            "pnl_ci95_low": r.get("pnl_ci95_low"),
            "dollars_per_capital_hour": None,
            "evidence_class": EVIDENCE_CLASS, "pnl_class": PNL_CLASS}


async def blocker_census(conn, account_id: str, *, since: float = 0.0,
                         top: int = 25) -> dict:
    """THE DISTINCT CONTRACT / SIDE BLOCKER CENSUS: refused entries keyed by
    unique opportunity (opportunity_funnel.opportunity_key) and contract /
    side, by binding refusal, ranked by executable EV at the decision."""
    got = await conn.fetch(
        "SELECT strategy, stage, refusal, us_market_slug, holding_side, "
        "       fixture, line, scope, gross_edge_pp, edge_shortfall_pp, "
        "       expected_fees_usd, slippage_usd, adverse_selection_usd, "
        "       total_executable_ev_usd, refused_at "
        "  FROM paper_entry_refusal_census WHERE account_id = $1 "
        "   AND refused_at >= $2", account_id, _ts(since))
    # THE TALLY RUNS IN A WORKER THREAD, OFF THE API EVENT LOOP (RC6).
    # Production 2026-10-09 (the loop watchdog's persisted ring, research-sql
    # rc6_api-responsive_loop_stalls.sql): an API loop stall of 2.5 s was the
    # red-team readiness pass -> completion read -> capital-readiness feeds
    # -> this census, keying every refused entry since the cutover on the
    # loop. The read stays on the loop; the tally is the same pure code, in
    # `_blocker_tally`, over the rows in their read order, on the API's CPU
    # lane (one worker thread for every such job: cpu_lane).
    from . import cpu_lane as _cpu
    return await _cpu.run(_blocker_tally, got, since=since, top=top)


def _blocker_tally(got, *, since: float, top: int) -> dict:
    """PURE (worker thread): the blocker census over the fetched refusal
    rows -- what `blocker_census` did inline on the event loop, verbatim."""
    from .profitability import opportunity_funnel as OF
    rows = [dict(r) for r in got]
    by_refusal: dict = {}
    by_contract: dict = {}
    unkeyed = 0
    for r in rows:
        key = OF.opportunity_key(r)
        if key is None:
            unkeyed += 1
        cs = (r["us_market_slug"], r["holding_side"])
        b = by_refusal.setdefault(r["refusal"], {
            "refusal": r["refusal"], "rows": 0, "contract_sides": set(),
            "opportunities": set(), "strategies": set(), "stages": set(),
            "best_executable_ev_usd": None})
        b["rows"] += 1
        b["strategies"].add(r["strategy"])
        b["stages"].add(r["stage"])
        if r["us_market_slug"] and r["holding_side"]:
            b["contract_sides"].add(cs)
        if key is not None:
            b["opportunities"].add(key)
        x = _num(r["total_executable_ev_usd"])
        if x is not None and (b["best_executable_ev_usd"] is None
                              or x > b["best_executable_ev_usd"]):
            b["best_executable_ev_usd"] = x
        if r["us_market_slug"] and r["holding_side"]:
            c = by_contract.setdefault(cs, {
                "us_market_slug": cs[0], "holding_side": cs[1],
                "fixture": r["fixture"], "refusals": {}, "strategies": set(),
                "best_executable_ev_usd": None, "best_gross_edge_pp": None,
                "min_edge_shortfall_pp": None, "rows": 0})
            c["rows"] += 1
            c["refusals"][r["refusal"]] = c["refusals"].get(
                r["refusal"], 0) + 1
            c["strategies"].add(r["strategy"])
            for k, col, better in (
                    ("best_executable_ev_usd", "total_executable_ev_usd",
                     max),
                    ("best_gross_edge_pp", "gross_edge_pp", max),
                    ("min_edge_shortfall_pp", "edge_shortfall_pp", min)):
                v = _num(r[col])
                if v is not None:
                    c[k] = v if c[k] is None else better(c[k], v)
    blockers = sorted((dict(b, contract_sides=len(b["contract_sides"]),
                            opportunities=len(b["opportunities"]),
                            strategies=sorted(b["strategies"]),
                            stages=sorted(b["stages"]))
                       for b in by_refusal.values()),
                      key=lambda b: (-b["contract_sides"], -b["rows"]))
    contracts = sorted((dict(c, strategies=sorted(c["strategies"]))
                        for c in by_contract.values()),
                       key=lambda c: (-(c["best_executable_ev_usd"]
                                        if c["best_executable_ev_usd"]
                                        is not None else -1e18),
                                      -c["rows"]))
    return {"since": since, "refused_entries": len(rows),
            "distinct_contract_sides": len(by_contract),
            "unkeyed_rows": unkeyed, "by_refusal": blockers,
            "top_contract_sides_by_executable_ev": contracts[:top],
            "ev_label": ("HYPOTHETICAL executable EV at the decision-time "
                         "book after fees and the adverse-selection "
                         "estimate; never a realized result")}


async def cutover_at(conn) -> dict:
    """The capital-authority cutover: when migration 305 was applied in this
    database (schema_migrations.applied_at)."""
    try:
        v = await conn.fetchval(
            "SELECT applied_at FROM schema_migrations "
            " WHERE version LIKE '305\\_%' ORDER BY applied_at LIMIT 1")
    except Exception:                                           # noqa: BLE001
        v = None
    return {"at": _epoch(v), "basis": (
        "schema_migrations.applied_at of migration 305" if v is not None
        else "UNAVAILABLE")}


async def entry_allowed(conn, *, account_id: str, strategy: str,
                        now: float, positions: list | None = None) -> dict:
    """WOULD A PAPER ENTRY OF THIS STRATEGY BE ALLOWED NOW (before the
    opportunity's own executable EV, which is per decision)? The lifecycle,
    management freshness, stopping rules now, forward economics. Read only."""
    from . import bettor_paper_freshness as PMF
    from . import bettor_stale_management as SM
    cur = await LC.current_state(conn, account_id, strategy)
    if not cur.get("ok"):
        return {"entry_allowed": False, "refusal": LC.R_LIFECYCLE_UNREADABLE}
    if cur["state"] in LC.ENTRY_STATE_REFUSAL:
        return {"entry_allowed": False,
                "refusal": LC.ENTRY_STATE_REFUSAL[cur["state"]],
                "state": cur["state"]}
    st = await SM.stale_management_rate(conn, account_id, strategy, now=now)
    if not st.get("ok"):
        return {"entry_allowed": False,
                "refusal": LC.R_STALE_MANAGEMENT_UNREADABLE}
    if (st.get("open_positions") or 0) >= LC.STALE_MANAGEMENT_MIN_OPEN and \
            (st.get("rate") or 0) > LC.STALE_MANAGEMENT_MAX_RATE:
        return {"entry_allowed": False,
                "refusal": LC.R_STALE_MANAGEMENT_EXCEEDED}
    fr = await PMF.allocation_refusal(conn, account_id=account_id,
                                      strategy=strategy, now=now)
    if fr:
        return {"entry_allowed": False, "refusal": fr["refusal"]}
    auth = await authority(conn, account_id=account_id, strategy=strategy,
                           now=now, positions=positions)
    return {"entry_allowed": auth.get("refusal") is None,
            "refusal": auth.get("refusal"), "state": cur["state"],
            "rules_firing": [r["rule_id"] for r in (
                auth.get("rules") or {}).get("firing") or []],
            "forward_economics": auth.get("forward"),
            "and_per_decision": ("executable EV after fees and execution "
                                 "costs > 0 on a fresh executable book")}


MB_LEDGER = "LEDGER_MARK_LATEST_BOOK"
MB_SETTLEMENT = "AUTHORITATIVE_SETTLEMENT_OUTCOME_PENDING_LEDGER"
MB_LAST_EXIT = "LAST_EXECUTABLE_EXIT_PRICE_OBSERVED"
MB_ZERO_FLOOR = "CONSERVATIVE_ZERO_EXIT_VALUE_FLOOR"
LAST_EXIT_SCAN = 50


async def complete_marks(conn, account_id: str, *, now: float,
                         positions: list | None = None) -> dict:
    """EVERY OPEN PAPER POSITION MARKED, NEVER NULL WHEN COMPUTABLE
    (acceptance read only -- the ledger's own `balances` and every risk
    control keep their marks). Per position, the first of:
      1 the ledger's mark (the latest error-free book's exit price);
      2 the AUTHORITATIVE settlement outcome already known for the contract
        but not yet booked (xavier_management.settlement_outcome: WON 1,
        LOST 0, the venue's published price; a VOID at the entry price);
      3 the newest error-free observation (of the last LAST_EXIT_SCAN) that
        published an executable exit price for the held side, with its age;
      4 the conservative floor: zero exit value (the whole basis at risk).
    Returns {marks: {slug: {side: mark}} for bettor_paper_ledger.balances,
    basis: {position_key: basis}, counts}."""
    from . import bettor_paper_freshness as PMF
    from . import bettor_paper_ledger as L
    from .agents import xavier_management as XM
    pos = positions if positions is not None else await L.positions(
        conn, account_id)
    open_pos = [p for p in pos if (_num(p.get("open_qty")) or 0) > 1e-9]
    marks = await L.latest_marks(conn, [p["us_market_slug"]
                                        for p in open_pos], now=now)
    basis, counts = {}, {MB_LEDGER: 0, MB_SETTLEMENT: 0, MB_LAST_EXIT: 0,
                         MB_ZERO_FLOOR: 0}
    for p in open_pos:
        slug, side = p["us_market_slug"], p["holding_side"]
        m = (marks.get(slug) or {}).get(side) or {}
        if m.get("price") is not None:
            basis[p["position_key"]] = MB_LEDGER
            counts[MB_LEDGER] += 1
            continue
        mark = None
        st = await XM.settlement_outcome(conn, slug, side)
        if st and st.get("outcome") is not None:
            per = _num(st.get("payout_per_contract"))
            if per is None and st.get("refund"):
                bq = _num(p.get("bought_qty")) or 0.0
                per = (((_num(p.get("acquisition_cost_usd")) or 0.0)
                        - (_num(p.get("buy_fees_usd")) or 0.0)) / bq
                       if bq > 0 else None)
            if per is not None:
                mark = {"status": "OK", "price": per,
                        "source": "settlement:%s" % st.get("outcome"),
                        "method": MB_SETTLEMENT, "stale": False}
                basis[p["position_key"]] = MB_SETTLEMENT
        if mark is None:
            for r in await conn.fetch(
                    "SELECT obs_id, observed_at, bids, offers "
                    "  FROM paper_book_observations WHERE us_market_slug=$1 "
                    "   AND error IS NULL ORDER BY observed_at DESC, "
                    "   obs_id DESC LIMIT $2", slug, LAST_EXIT_SCAN):
                bm = PMF.book_mark(dict(r), side)
                if bm.get("price") is not None:
                    at = _epoch(r["observed_at"])
                    mark = {"status": "STALE", "price": bm["price"],
                            "source": "paper_book_observations:%s"
                                      % r["obs_id"],
                            "observed_at": at, "age_s": round(now - at, 3),
                            "stale": True, "method": MB_LAST_EXIT}
                    basis[p["position_key"]] = MB_LAST_EXIT
                    break
        if mark is None:
            mark = {"status": "FLOOR", "price": 0.0, "stale": True,
                    "source": None, "method": MB_ZERO_FLOOR,
                    "why": m.get("why") or "NO_EXECUTABLE_EXIT_EVER_OBSERVED"}
            basis[p["position_key"]] = MB_ZERO_FLOOR
        counts[basis[p["position_key"]]] += 1
        marks.setdefault(slug, {})[side] = mark
    return {"marks": marks, "basis": basis, "counts": counts,
            "rule": ("ledger mark, else the known settlement outcome, else "
                     "the last executable exit observed, else zero exit "
                     "value: never null")}


async def management_reconciliation(conn, account_id: str, *, bal: dict,
                                    now: float) -> dict:
    """THE $500,000 MANAGEMENT EPOCH, reconciled to the cent
    (bettor_paper_epoch.read on the completely marked balances): opening
    equity 500,000.00 at 2026-10-05 00:00 America/New_York, realized,
    unrealized, equity, and the residual EQUITY - (OPENING + REALIZED +
    UNREALIZED), which must be 0.00. No management field is null."""
    from . import bettor_paper_epoch as EP
    async with conn.transaction():
        m = await EP.read(conn, account_id, bal=bal, now=now)
    if m.get("status") == "NOT_STARTED":
        return {"status": "NOT_STARTED", "epoch_id": EP.EPOCH_ID,
                "epoch_start": EP.EPOCH_START_LOCAL}
    gap = _num((m.get("identity") or {}).get("gap_usd"))
    out = {"epoch_id": EP.EPOCH_ID, "epoch_start": EP.EPOCH_START_LOCAL,
           "epoch_start_at": EP.EPOCH_START,
           "opening_equity_usd": float(EP.OPENING_EQUITY_USD),
           "realized_pnl_usd": m.get("realized_pnl_usd"),
           "unrealized_pnl_usd": m.get("unrealized_pnl_usd"),
           "total_pnl_usd": m.get("total_pnl_usd"),
           "equity_usd": m.get("equity_usd"),
           "cash_usd": m.get("cash_usd"),
           "reserved_usd": m.get("reserved_usd"),
           "open_exposure_basis_usd": (m.get("exposure") or {}).get(
               "basis_usd"),
           "residual_usd": None if gap is None else round(gap, 2),
           "identity": "EQUITY = OPENING_EQUITY + REALIZED + UNREALIZED",
           "ledger_reconciliation_gap_usd": (m.get(
               "ledger_reconciliation") or {}).get("gap_usd"),
           "carried_unverified_positions": m.get("carried_unverified"),
           "unmarked_open_positions": (m.get("exposure") or {}).get(
               "unmarked"),
           "management_status": m.get("status")}
    out["null_fields"] = sorted(k for k, v in out.items() if v is None)
    out["reconciles_to_the_cent"] = (
        gap is not None and abs(gap) < 0.005 and not out["null_fields"]
        and m.get("status") == "OK")
    out["status"] = ("OK" if out["reconciles_to_the_cent"]
                     else "DOES_NOT_RECONCILE")
    return out


async def acceptance_view(conn, *, account_id: str, now: float,
                          cutover: float | None = None) -> dict:
    """THE ACCEPTANCE READ. Raises on a failed read (the route turns that
    into UNAVAILABLE, never zeros)."""
    from . import bettor_paper_ledger as L
    if not await LC.schema(conn) or not await schema(conn):
        return {"status": "UNAVAILABLE",
                "why": "MIGRATION_290_OR_305_NOT_APPLIED"}
    co = ({"at": float(cutover), "basis": "REQUESTED"} if cutover is not None
          else await cutover_at(conn))
    since = co["at"] if co["at"] is not None else now
    allpos = await L.positions(conn, account_id, include_closed=True)
    # EVERY OPEN POSITION MARKED (never null when computable): the
    # acceptance read's balances use the completed marks
    cm = await complete_marks(conn, account_id, now=now, positions=allpos)
    bal = await L.balances(conn, account_id, now=now, marks=cm["marks"])
    management = await management_reconciliation(conn, account_id, bal=bal,
                                                 now=now)
    has_bind = await PBIND.schema(conn)
    models = await PBIND.latest_models(conn, account_id) if has_bind else {}
    names = set(LC.KNOWN_STRATEGIES) | {
        LC.strategy_of(p, L.DEFAULT_STRATEGY) for p in allpos}
    events = [dict(r) for r in await conn.fetch(
        "SELECT strategy, to_state, recorded_at FROM "
        " paper_strategy_lifecycle_events WHERE account_id = $1 "
        " ORDER BY event_id", account_id)]
    orders = [dict(r) for r in await conn.fetch(
        "SELECT o.order_id, o.strategy, o.decided_at, o.filled_qty, "
        "       o.state, e.detail FROM paper_orders o "
        "  LEFT JOIN paper_order_events e ON e.order_id = o.order_id "
        "   AND e.kind = 'SUBMITTED' "
        " WHERE o.account_id = $1 AND o.role = 'ENTRY' "
        "   AND o.direction = 'BUY' AND o.decided_at >= $2",
        account_id, _ts(since))]
    reserved = {r["strategy"]: float(r["res"]) for r in await conn.fetch(
        "SELECT strategy, coalesce(sum(reserved_remaining_usd), 0) AS res "
        "  FROM paper_orders WHERE account_id = $1 AND direction = 'BUY' "
        "   AND state = ANY($2::text[]) GROUP BY strategy",
        account_id, list(L.OPEN_STATES))}
    shadows = await shadow_summary(conn, account_id, since=since)
    shadows_all = await shadow_summary(conn, account_id,
                                       since=FORWARD_SINCE)
    unreal_by: dict = {}
    for v in bal.get("open_positions") or []:
        s = LC.strategy_of(v, L.DEFAULT_STRATEGY)
        u = unreal_by.setdefault(s, {"usd": 0.0, "unmarked": 0, "basis": {}})
        if v.get("unrealized_pnl_usd") is None:
            u["unmarked"] += 1
        else:
            u["usd"] += float(v["unrealized_pnl_usd"])
        b = cm["basis"].get(v["position_key"]) or MB_LEDGER
        u["basis"][b] = u["basis"].get(b, 0) + 1

    def state_at(s, t):
        st = LC.INITIAL_STATE
        for e in events:
            if e["strategy"] == s and _epoch(e["recorded_at"]) <= t:
                st = e["to_state"]
        return st

    rows: dict = {}
    totals = {"new_entries_since_cutover": 0,
              "entries_in_no_entry_states": 0,
              "entries_without_positive_forward_economics": 0}
    for s in sorted(names):
        pos = [p for p in allpos if LC.strategy_of(p, L.DEFAULT_STRATEGY)
               == s]
        cur = await LC.current_state(conn, account_id, s)
        allowed = await entry_allowed(conn, account_id=account_id,
                                      strategy=s, now=now, positions=allpos)
        mine = [o for o in orders if (o["strategy"] or L.DEFAULT_STRATEGY)
                == s]
        no_entry, unforwarded, ev_sum, ev_n = 0, 0, 0.0, 0
        for o in mine:
            t = _epoch(o["decided_at"])
            if state_at(s, t) in LC.NO_ENTRY_STATES:
                no_entry += 1
            ca = (_j(o["detail"]) or {}).get("capital_authority") or {}
            if ca.get("forward_verdict") != POSITIVE:
                unforwarded += 1
            x = _num(ca.get("total_executable_ev_usd"))
            if x is not None:
                ev_sum += x
                ev_n += 1
        closed_since = [p for p in pos if LC.closed_at(p) is not None
                        and (_num(p.get("first_fill_at")) or 0) >= since]
        realized = sum((_num(p.get("realized_pnl_usd")) or 0.0) for p in pos)
        open_cost = sum((_num(p.get("cost_basis_usd")) or 0.0) for p in pos
                        if (_num(p.get("open_qty")) or 0) > 1e-9)
        u = unreal_by.get(s) or {"usd": 0.0, "unmarked": 0, "basis": {}}
        fwd_all = forward_verdict(
            forward_paper_pnls(pos, s, default_strategy=L.DEFAULT_STRATEGY),
            await shadow_pnls(conn, account_id, s))
        bind_state = (await PBIND.strategy_state(
            conn, account_id=account_id, strategy=s, now=now, since=since,
            forward=fwd_all, positions=allpos, models=models)
            if has_bind else {"status": "MIGRATION_309_NOT_APPLIED"})
        sh_fwd = shadow_forward_metrics(shadows_all.get(s))
        rows[s] = {
            "strategy": s, "lifecycle_state": cur.get("state"),
            "paper_entry_allowed": allowed["entry_allowed"],
            "paper_entry_refusal": allowed.get("refusal"),
            "entry_check": allowed,
            "new_paper_entries_since_cutover": len(mine),
            "new_paper_entries_filled_since_cutover": sum(
                1 for o in mine if float(o["filled_qty"] or 0) > 0),
            "assert_entries_in_no_entry_states": no_entry,
            "assert_entries_without_positive_forward_economics": unforwarded,
            "expected_executable_ev_at_decision_usd": {
                "paper_entries": round(ev_sum, 6) if ev_n else None,
                "paper_entries_with_evidence": ev_n,
                "shadow_counterfactuals": (shadows.get(s) or {}).get(
                    "expected_ev_at_decision_usd"),
                "label": "HYPOTHETICAL, at the decision-time book"},
            "shadow_forward": shadows.get(s) or {
                "recorded": 0, "settled": 0,
                "evidence_class": EVIDENCE_CLASS, "pnl_class": PNL_CLASS},
            "forward_economics": fwd_all,
            "promotion_evidence_shadow_forward": {
                "restore": LC.restore_evidence(sh_fwd),
                "promotion": LC.promotion_evidence(sh_fwd),
                "use": ("ADVISORY forward evidence for a named person "
                        "(bettor_strategy_lifecycle.transition); never an "
                        "automatic promotion")},
            "realized_paper_pnl_usd": round(realized, 6) if pos else None,
            "unrealized_paper_pnl_usd": (round(u["usd"], 6)
                                         if not u["unmarked"] else None),
            "unrealized_paper_pnl_marked_only_usd": round(u["usd"], 6),
            "unrealized_mark_basis": u["basis"],
            "unmarked_open_positions": u["unmarked"],
            "open_exposure_usd": round(open_cost + reserved.get(s, 0.0), 6),
            "open_cost_basis_usd": round(open_cost, 6),
            "reserved_open_entry_usd": round(reserved.get(s, 0.0), 6),
            "profitability_bind": bind_state,
            "settled_paper_forward_since_cutover": {
                "closed_positions": len(closed_since),
                "realized_pnl_usd": (round(sum(
                    (_num(p.get("realized_pnl_usd")) or 0.0)
                    for p in closed_since), 6) if closed_since else None),
                "pnl_class": "REALIZED_PAPER_PNL"}}
        totals["new_entries_since_cutover"] += len(mine)
        totals["entries_in_no_entry_states"] += no_entry
        totals["entries_without_positive_forward_economics"] += unforwarded
    census = await blocker_census(conn, account_id, since=since)
    return {
        "status": "OK", "version": VERSION, "account_id": account_id,
        "as_of": now, "cutover": co,
        "forward_rule": {"version": FORWARD_RULE_VERSION,
                         "forward_since": FORWARD_SINCE,
                         "min_observations": MIN_FORWARD_OBSERVATIONS},
        "strategies": rows,
        "assertions": {
            "zero_entries_in_shadow_only_quarantined_retired": totals[
                "entries_in_no_entry_states"] == 0,
            "zero_entries_with_unknown_or_negative_forward_economics": totals[
                "entries_without_positive_forward_economics"] == 0,
            **totals},
        "paper": {"realized_pnl_usd": bal.get("realized_pnl_usd"),
                  "unrealized_pnl_usd": bal.get("unrealized_pnl_usd"),
                  "unrealized_mark_basis": cm["counts"],
                  "mark_rule": cm["rule"],
                  "open_exposure_usd": round(
                      sum((_num(v.get("cost_basis_usd")) or 0.0)
                          for v in bal.get("open_positions") or [])
                      + sum(reserved.values()), 6),
                  "open_position_value_usd": bal.get(
                      "open_position_value_usd"),
                  "equity_usd": bal.get("total_equity_usd"),
                  "pnl_class": "PAPER (LIVE MARKET DATA / SIMULATED "
                               "EXECUTION)",
                  "includes_shadow": False,
                  "management": management},
        "profitability_bind": {
            "version": PBIND.VERSION, "schema": has_bind,
            "models": {k: {"model_id": v.get("model_id"),
                           "fitted_at": v.get("fitted_at"),
                           "observations": v.get("observations")}
                       for k, v in models.items()},
            "calibration_cells": {
                k: {kk: c.get(kk) for kk in ("n", "status", "mean_p",
                                              "observed", "brier")}
                for k, c in ((models.get("CALIBRATION") or {}).get(
                    "cells") or {}).items()},
            "rules": {k: v for k, v in PBIND.describe().items()
                      if k not in ("at",)}},
        "blocker_census": census,
        "actual_profitability": PROFITABILITY,
        "authority": AUTHORITY}


def describe() -> dict:
    return {"version": VERSION, "forward_rule_version": FORWARD_RULE_VERSION,
            "forward_since": FORWARD_SINCE,
            "min_forward_observations": MIN_FORWARD_OBSERVATIONS,
            "refusals": [R_FORWARD_UNKNOWN, R_FORWARD_NEGATIVE,
                         R_FORWARD_UNREADABLE, R_RULE_FIRING_AT_ENTRY,
                         R_RULES_UNREADABLE, R_EV_NOT_EVIDENCED,
                         R_EV_NOT_POSITIVE],
            "evidence_class": EVIDENCE_CLASS, "pnl_class": PNL_CLASS,
            "paper_only": True, "at": time.time()}
