"""I · EVIDENCE-QUALITY SIZING (SHADOW). What size the evidence would
support, persisted BESIDE the paper size and the actual size. NEVER APPLIED:
migration 208 stores intel_sizing.applied = false only, and nothing reads a
shadow size to place, scale or cap an order.

THE ARITHMETIC (per ENTER decision):

  p_cons     = p - u_cal               u_cal: calibration uncertainty, the
                                       Wilson 95% half-width of p's
                                       probability band (intel/calibration),
                                       plus any measured over-statement
                                       max(0, mean_p - observed)
  cost_cons  = c + fee_pc + u_exec     c: the decision's planned acquisition
                                       VWAP; u_exec: the standard deviation
                                       of measured paper slippage
  edge_cons  = p_cons - cost_cons      <= 0 -> shadow size 0 (a computed
                                       zero: no edge survives the evidence)
  kelly      = edge_cons / (1 - cost_cons)      (binary contract)
  base_usd   = SLEEVE x KELLY_FRACTION x kelly

  then multiplied by, each in [0, 1] with its reason:
    settlement confidence  1 - (void upper bound) when measured
    correlation            1 / (1 + open positions on the same game)
    sample size            sqrt(min(1, n_band / FULL_EVIDENCE_N)), floor 0.1
    drawdown               1 - current drawdown / DRAWDOWN_HALT_PCT
    regime                 NORMAL 1, REDUCE 0.5, NO_TRADE 0
  and capped by liquidity (LIQUIDITY_SHARE of the displayed depth within the
  decision's limit, at cost) and by PER_POSITION_CAP of the sleeve.

AN UNMEASURED INPUT is never read as zero risk: it takes the declared
conservative default (UNMEASURED_*) and is listed in `unmeasured` with its
reason, so a shadow size that rests on a default says so.
"""
from __future__ import annotations

import math

from . import calibration as CAL
from . import common as C

VERSION = "INTEL_SIZING_V1"
KELLY_FRACTION = 0.25
PER_POSITION_CAP = 0.10            # of the sleeve
LIQUIDITY_SHARE = 0.25             # of displayed depth within the limit
FULL_EVIDENCE_N = 300
DRAWDOWN_HALT_PCT = 20.0
UNMEASURED_CAL_UNCERTAINTY = 0.10
UNMEASURED_EXEC_UNCERTAINTY = 0.02
UNMEASURED_SETTLEMENT_FACTOR = 0.8
UNMEASURED_DRAWDOWN_FACTOR = 0.75
REGIME_FACTOR = {"NORMAL": 1.0, "REDUCE": 0.5, "NO_TRADE": 0.0}
UNMEASURED_REGIME_FACTOR = 0.5


def exec_uncertainty(attributions: list):
    xs = [a["slippage_pc"] for a in attributions or []
          if a.get("book") == "PAPER" and a.get("slippage_pc") is not None]
    return C.stdev(xs), len(xs)


def shadow_size(*, p, cost, fee_pc, depth_qty, cal_unc: dict,
                exec_sd, exec_n, void_upper, same_game_open,
                drawdown_pct, regime, sleeve=C.SLEEVE_NOTIONAL_USD) -> dict:
    """The pure sizing function. Returns the shadow USD / qty and every
    factor with its reason."""
    out = C.Out(version=VERSION, sleeve_usd=sleeve, applied=False)
    f = {}
    if p is None or cost is None:
        out.put("shadow_usd", None, "DECISION_LACKS_PROBABILITY_OR_COST")
        out.put("shadow_qty", None, "DECISION_LACKS_PROBABILITY_OR_COST")
        out["factors"] = f
        out["binding_constraint"] = None
        return out
    # ── calibration uncertainty
    hw = cal_unc.get("half_width") if cal_unc else None
    mis = cal_unc.get("miscalibration") if cal_unc else None
    if hw is None:
        u_cal = UNMEASURED_CAL_UNCERTAINTY
        out["unmeasured"]["calibration_uncertainty"] = (
            (cal_unc or {}).get("unmeasured", {}).get("half_width")
            or "NO_CALIBRATION_EVIDENCE") + "_DEFAULT_%.2f" % u_cal
    else:
        u_cal = hw + max(0.0, mis or 0.0)
    f["calibration_uncertainty"] = C.rnd(u_cal)
    # ── execution uncertainty
    if exec_sd is None:
        u_exec = UNMEASURED_EXEC_UNCERTAINTY
        out["unmeasured"]["execution_uncertainty"] = (
            "FEWER_THAN_TWO_MEASURED_SLIPPAGES_DEFAULT_%.2f" % u_exec)
    else:
        u_exec = exec_sd
    f["execution_uncertainty"] = C.rnd(u_exec)
    f["execution_sample_n"] = exec_n
    fee = fee_pc or 0.0
    p_cons = p - u_cal
    cost_cons = min(0.999, cost + fee + u_exec)
    edge = p_cons - cost_cons
    f.update({"p": C.rnd(p), "p_conservative": C.rnd(p_cons),
              "cost": C.rnd(cost), "fee_pc": C.rnd(fee),
              "cost_conservative": C.rnd(cost_cons),
              "net_edge_conservative": C.rnd(edge),
              "net_ev_per_dollar_conservative": C.rnd(edge / cost_cons)})
    # ── multipliers
    if void_upper is None:
        m_set = UNMEASURED_SETTLEMENT_FACTOR
        out["unmeasured"]["settlement_confidence"] = (
            "VOID_RATE_NOT_MEASURED_DEFAULT_%.2f" % m_set)
    else:
        m_set = C.clamp(1.0 - void_upper)
    m_corr = 1.0 / (1.0 + max(0, int(same_game_open or 0)))
    n_band = int((cal_unc or {}).get("n") or 0)
    m_n = max(0.1, math.sqrt(min(1.0, n_band / FULL_EVIDENCE_N)))
    if drawdown_pct is None:
        m_dd = UNMEASURED_DRAWDOWN_FACTOR
        out["unmeasured"]["drawdown"] = (
            "DRAWDOWN_NOT_MEASURED_DEFAULT_%.2f" % m_dd)
    else:
        m_dd = C.clamp(1.0 - drawdown_pct / DRAWDOWN_HALT_PCT)
    if regime in REGIME_FACTOR:
        m_reg = REGIME_FACTOR[regime]
    else:
        m_reg = UNMEASURED_REGIME_FACTOR
        out["unmeasured"]["regime"] = (
            "NO_REGIME_RECOMMENDATION_DEFAULT_%.2f" % m_reg)
    f.update({"settlement_factor": C.rnd(m_set),
              "correlation_factor": C.rnd(m_corr),
              "same_game_open_positions": int(same_game_open or 0),
              "sample_size_factor": C.rnd(m_n), "sample_size_n": n_band,
              "drawdown_factor": C.rnd(m_dd), "regime_factor": C.rnd(m_reg),
              "regime": regime})
    if depth_qty is None:
        out["unmeasured"]["liquidity_cap"] = "NO_DISPLAYED_DEPTH_RECORDED"
    if edge <= 0:
        out.put("shadow_usd", 0.0)
        out.put("shadow_qty", 0.0)
        out["binding_constraint"] = "NO_EDGE_AFTER_EVIDENCE_UNCERTAINTY"
        out["factors"] = f
        return out
    kelly = edge / (1.0 - cost_cons)
    base = sleeve * KELLY_FRACTION * kelly
    mult = m_set * m_corr * m_n * m_dd * m_reg
    sized = base * mult
    caps = {"KELLY_X_EVIDENCE": sized,
            "PER_POSITION_CAP": sleeve * PER_POSITION_CAP}
    if depth_qty is not None:
        caps["LIQUIDITY"] = LIQUIDITY_SHARE * depth_qty * cost
    bind = min(caps, key=lambda k: caps[k])
    usd = max(0.0, caps[bind])
    f.update({"kelly_fraction_of_full": C.rnd(kelly),
              "kelly_scale": KELLY_FRACTION, "base_usd": C.rnd(base),
              "evidence_multiplier": C.rnd(mult),
              "caps_usd": {k: C.rnd(v) for k, v in caps.items()}})
    out.put("shadow_usd", C.rnd(usd, 4))
    out.put("shadow_qty", C.rnd(usd / cost, 4))
    out["binding_constraint"] = bind
    out["factors"] = f
    return out


def size_decision(dec: dict, *, cal_report, exec_sd, exec_n, risk_paper,
                  regime, actual: dict | None) -> dict:
    """One ENTER decision -> its sizing row (shadow beside paper/actual)."""
    from . import attribution as A

    econ = C.jload(dec.get("economics")) or {}
    acq = econ.get("acquisition") if isinstance(econ, dict) else {}
    acq = acq if isinstance(acq, dict) else {}
    p, _ = A.decision_probability(dec)
    cost, _ = A.decision_price(dec)
    qty = C.num(dec.get("proposed_qty"))
    fees = C.num(acq.get("fees_usd"))
    fee_pc = (fees / qty) if (fees is not None and qty) else None
    depth = C.num(econ.get("depth_within_limit")) if isinstance(
        econ, dict) else None
    states = acq.get("settlement_states") or {}
    void_upper = C.num(states.get("void_upper_95")) if isinstance(
        states, dict) else None
    label = C.jload(dec.get("label")) or {}
    game = str((label.get("event_key") if isinstance(label, dict) else None)
               or dec.get("fixture") or dec.get("us_market_slug"))
    same_game = 0
    dd = None
    if risk_paper:
        for g in (risk_paper.get("exposure") or {}).get("game") or []:
            if g["key"] == game:
                same_game = g["positions"]
        dd = (risk_paper.get("equity") or {}).get("current_drawdown_pct")
    s = shadow_size(p=p, cost=cost, fee_pc=fee_pc, depth_qty=depth,
                    cal_unc=CAL.uncertainty_for(cal_report, p=p),
                    exec_sd=exec_sd, exec_n=exec_n, void_upper=void_upper,
                    same_game_open=same_game, drawdown_pct=dd, regime=regime)
    if risk_paper is None:
        s["unmeasured"]["correlation"] = "NO_RISK_REPORT_THIS_CYCLE"
    paper_usd = (qty * cost) if (qty is not None and cost is not None) \
        else None
    row = C.Out(decision_id=dec["decision_id"], strategy=dec.get("strategy"),
                us_market_slug=dec.get("us_market_slug"), label=C.LABEL)
    row.put("paper_qty", qty, "DECISION_HAS_NO_PROPOSED_QTY")
    row.put("paper_usd", C.rnd(paper_usd), "DECISION_HAS_NO_PROPOSED_QTY")
    aq = C.num((actual or {}).get("live_qty"))
    row.put("actual_qty", aq, (actual or {}).get("why")
            or "NO_EXECUTION_INTENT_FOR_THIS_DECISION")
    row.put("actual_usd", C.rnd(aq * cost) if (aq is not None
                                               and cost is not None) else
            None, (actual or {}).get("why")
            or "NO_EXECUTION_INTENT_FOR_THIS_DECISION")
    row["shadow_usd"] = s["shadow_usd"]
    row["shadow_qty"] = s["shadow_qty"]
    row["binding_constraint"] = s["binding_constraint"]
    row["factors"] = s["factors"]
    row["unmeasured"].update(s["unmeasured"])
    row["applied"] = False
    return row


DECISIONS_SQL = """
    SELECT decision_id, decided_at, us_market_slug, holding_side, fixture,
           label, p_pinnacle, p_blended, p_internal, limit_price,
           proposed_qty, economics, strategy
      FROM paper_decisions
     WHERE verdict = 'ENTER' AND decided_at >= to_timestamp($1)
       AND ($2::text IS NULL OR account_id = $2)
     ORDER BY decided_at DESC LIMIT $3
"""


async def load_and_size(conn, *, now, cal_report, attributions, risk_paper,
                        regime, days=2.0, account_id=C.PAPER_ACCOUNT,
                        limit=500) -> list:
    decs = [dict(r) for r in await conn.fetch(
        DECISIONS_SQL, float(now) - days * 86400.0, account_id, int(limit))]
    ids = [d["decision_id"] for d in decs]
    actual = {}
    if ids:
        for r in await conn.fetch(
                "SELECT DISTINCT ON (decision_id) decision_id, "
                "       coalesce(live_qty_exact, live_qty) AS live_qty, "
                "       live_eligible, actual_state, actual_refusal "
                "  FROM execution_intents WHERE decision_id = ANY($1::text[])"
                " ORDER BY decision_id, created_at DESC", ids):
            actual[r["decision_id"]] = {
                "live_qty": r["live_qty"] if r["live_eligible"] else None,
                "why": None if r["live_eligible"] else (
                    "NOT_LIVE_ELIGIBLE:%s" % (r["actual_refusal"]
                                              or r["actual_state"]))}
    sd, n = exec_uncertainty(attributions)
    return [size_decision(d, cal_report=cal_report, exec_sd=sd, exec_n=n,
                          risk_paper=risk_paper, regime=regime,
                          actual=actual.get(d["decision_id"]))
            for d in decs]
