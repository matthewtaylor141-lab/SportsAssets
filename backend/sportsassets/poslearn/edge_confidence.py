"""2 · THE EDGE-CONFIDENCE META-MODEL (SHADOW). When is BETTOR's estimated
edge trustworthy? Pure; no I/O.

TARGET. Whether the predicted net edge became a realized net edge. For a
$0/$1 contract bought at all-in cost c* = price + fee, the realized net edge
per contract is (outcome - c*); it is positive exactly when the contract
pays. So the meta-model is a ridge logistic regression (poslearn/logistic,
pure Python: numpy is not in the image) of the realized positive net outcome
on the opportunity's features (features.EC_FEATURES: the PinnAPI probability,
gross/net edge, microstructure, time-to-event, live state, volatility,
change frequency, price movement, quote age, cross-market gap, calibration
history, regime, historical edge reliability, and EDDIE's execution
uncertainty through its interface). Its Laplace posterior gives every
forward row:

  q                      the meta-estimated probability of a positive net
                         outcome, with its 95% interval;
  expected_net_edge      q - c*, with its 95% interval;
  EDGE_CONFIDENCE        P(expected net edge > 0) = Phi((eta - logit c*) /
                         se_eta): how confident the evidence is that the
                         edge is REAL, not just that this trade wins;
  edge_error_predicted   expected_net_edge - BETTOR's predicted net edge (p -
                         c*): the predicted-vs-realized edge error the
                         meta-model expects.

TRAINED BEFORE REGISTRATION, on resolved independent opportunities whose
outcome was known by registration, features computed as of each row's own
decision instant; FROZEN in the hashed registration; evaluated FORWARD only.

NOT A SIZING INPUT. The future sizing formula

    size = EV x EDGE_CONFIDENCE x LIQUIDITY x CALIBRATION x RISK_BUDGET

is DOCUMENTED here and NOT ACTIVE: nothing reads EDGE_CONFIDENCE to size,
admit or refuse anything, the forecast row CHECKs applied = false, and
activating it needs forward validation plus an owner-approved release. It
must never be used for live sizing.
"""
from __future__ import annotations

from . import common as C
from . import features as FT
from . import logistic as LR

VERSION = "POSLEARN_EDGE_CONFIDENCE_V1"
SUBJECT = "EDGE_CONFIDENCE"
MIN_TRAIN = 200
MIN_PRESENCE = 0.10
MIN_SAMPLE = 200
RIDGE = 1.0
BUCKETS = ((0.0, 0.5, "LT_0.5"), (0.5, 0.7, "0.5_0.7"),
           (0.7, 0.9, "0.7_0.9"), (0.9, 1.000001, "GE_0.9"))
FUTURE_SIZING_FORMULA = {
    "formula": "EV x EDGE_CONFIDENCE x LIQUIDITY x CALIBRATION x RISK_BUDGET",
    "status": "NOT_ACTIVE",
    "why": ("documented only; requires forward validation of "
            "EDGE_CONFIDENCE, an Audrey evaluation, a Karen challenge and an "
            "owner-approved release before any reader may use it. It must "
            "never be used for live sizing from this layer."),
    "live_sizing_effect": "NONE"}


def cost_star(price, fee):
    if price is None or fee is None:
        return None
    c = float(price) + float(fee)
    return c if 0.0 < c < 1.0 else None


def train(rows, *, now) -> dict:
    """rows: resolved records {o, features, outcome_at, price, fee}."""
    rows = [r for r in rows if r.get("outcome_at") is not None
            and r["outcome_at"] <= now and r.get("o") in (0, 1)
            and cost_star(r.get("price"), r.get("fee")) is not None
            and r["features"].get("logit_p") is not None]
    if len(rows) < MIN_TRAIN:
        return {"trainable": False,
                "why": "INSUFFICIENT_TRAINING_DATA_N_%d_NEEDS_%d" % (
                    len(rows), MIN_TRAIN)}
    names = [k for k in FT.EC_FEATURES
             if sum(1 for r in rows if r["features"].get(k) is not None)
             >= MIN_PRESENCE * len(rows)]
    dropped = {k: "PRESENT_IN_FEWER_THAN_%d%%_OF_TRAINING_ROWS" % (
        MIN_PRESENCE * 100) for k in FT.EC_FEATURES if k not in names}
    params = LR.fit([r["features"] for r in rows],
                    [int(r["o"]) for r in rows], names, ridge=RIDGE)
    if params is None:
        return {"trainable": False,
                "why": "FIT_FAILED_ONE_CLASS_OR_SINGULAR_N_%d" % len(rows)}
    return {"trainable": True, "params": params, "features": names,
            "dropped": dropped, "n": len(rows),
            "window": {"start": min(r.get("decided_at") or r["outcome_at"]
                                    for r in rows) - 1.0,
                       "end": max(r["outcome_at"] for r in rows)}}


def document(trained, *, version) -> dict:
    return {
        "subject_id": SUBJECT, "version": version, "kind": "META_MODEL",
        "family": "META_MODELS_V1", "role": "META",
        "code_version": VERSION,
        "target": "realized net outcome > 0 (outcome - price - fee > 0)",
        "training_window": {"start": trained["window"]["start"],
                            "end": trained["window"]["end"],
                            "n": trained["n"]},
        "features": trained["features"],
        "features_unavailable": dict(
            trained.get("dropped") or {},
            settlement_confidence=FT.CATALOG[19][3],
            karen_challenge_state=FT.CATALOG[20][3],
            scout_feature_state=FT.CATALOG[21][3]),
        "parameters": trained["params"],
        "validation_method": (
            "PROSPECTIVE FORWARD: recorded before outcomes; evaluated on "
            "opportunities after registration -- Brier / log loss of q "
            "paired against the raw PinnAPI probability, and realized vs "
            "predicted net edge by EDGE_CONFIDENCE bucket"),
        "minimum_sample": MIN_SAMPLE,
        "promotion_threshold": {
            "status": "NOT_PROMOTABLE_TO_SIZING_FROM_THIS_LAYER",
            "future_sizing_formula": FUTURE_SIZING_FORMULA},
        "failure_threshold": {
            "rule": "forward paired log loss of q worse than the raw "
                    "probability with its 95% upper bound < 0 at "
                    "minimum_sample -> reported FAILED_FORWARD"},
        "hypothesis_family": "META_MODELS_V1", "family_size": 2,
    }


def forecast(doc: dict, opp: dict) -> dict:
    f = opp.get("features") or {}
    params = doc["parameters"]
    out = C.Out(action="SCORE")
    c = cost_star(opp.get("price"), opp.get("fee"))
    if f.get("logit_p") is None:
        out.update(action="ABSTAIN", abstain_reason="NO_PROBABILITY")
        return out
    pred = LR.predict(params, f)
    q = pred["p"]
    out["probability"] = C.rnd(q, 10)
    out["output"] = {"q": C.rnd(q, 8), "q_ci": [C.rnd(pred["p_lo"], 8),
                                               C.rnd(pred["p_hi"], 8)],
                     "se_eta": C.rnd(pred["se_eta"], 8),
                     "features_missing_at_mean": pred["missing"],
                     "future_sizing_formula": "NOT_ACTIVE"}
    if c is None:
        why = "NO_ALL_IN_COST_PRICE_OR_FEE_UNRECORDED"
        for k in ("edge_confidence", "expected_net_edge",
                  "expected_net_edge_ci_low", "expected_net_edge_ci_high"):
            out.put(k, None, why)
        return out
    se = pred["se_eta"]
    gap = pred["eta"] - C.logit(c)
    ec = (1.0 if gap > 0 else 0.0) if se <= 1e-12 else C.norm_cdf(gap / se)
    out.put("edge_confidence", C.rnd(ec, 8))
    out.put("expected_net_edge", C.rnd(q - c, 8))
    out.put("expected_net_edge_ci_low", C.rnd(pred["p_lo"] - c, 8))
    out.put("expected_net_edge_ci_high", C.rnd(pred["p_hi"] - c, 8))
    p = C.num(opp.get("p_reference"))
    out["predicted_net_edge"] = None if p is None else C.rnd(p - c, 10)
    out["output"]["edge_error_predicted"] = (
        None if p is None else C.rnd((q - c) - (p - c), 8))
    return out


def bucket(ec):
    for lo, hi, name in BUCKETS:
        if ec is not None and lo <= ec < hi:
            return name
    return "UNMEASURED"


def evaluate(rows: list) -> dict:
    """rows: forward EC forecasts joined to RESOLVED outcomes:
    {edge_confidence, probability (q), p_reference, price, fee, o}."""
    from .. import bettor_source_calibration as SC

    out = C.Out(n=len(rows), min_sample=MIN_SAMPLE)
    out["small_sample"] = len(rows) < MIN_SAMPLE
    pairs_q = [(r["probability"], r["o"]) for r in rows
               if r.get("probability") is not None]
    pairs_p = [(r["p_reference"], r["o"]) for r in rows
               if r.get("probability") is not None
               and r.get("p_reference") is not None]
    why = "NO_RESOLVED_FORWARD_EDGE_CONFIDENCE_FORECAST"
    out.put("brier_q", C.rnd(SC.brier(pairs_q)), why)
    out.put("log_loss_q", C.rnd(SC.log_loss(pairs_q)), why)
    out.put("brier_raw_p_same_rows", C.rnd(SC.brier(pairs_p)), why)
    diffs = [SC.log_loss([(r["p_reference"], r["o"])])
             - SC.log_loss([(r["probability"], r["o"])]) for r in rows
             if r.get("probability") is not None
             and r.get("p_reference") is not None]
    m, lo, hi, n = C.mean_ci(diffs, 1.959964)
    out.put("paired_log_loss_improvement_vs_raw", C.rnd(m), why)
    out.put("paired_log_loss_improvement_ci",
            None if lo is None else [C.rnd(lo), C.rnd(hi)],
            "FEWER_THAN_TWO_PAIRED_ROWS")
    out["reliability_q"] = SC.reliability(pairs_q, bins=10) if pairs_q \
        else None
    buckets = []
    for _, _, name in BUCKETS:
        mine = [r for r in rows if bucket(r.get("edge_confidence")) == name]
        real = [r["o"] - cost_star(r["price"], r["fee"]) for r in mine
                if cost_star(r.get("price"), r.get("fee")) is not None]
        pred = [r["p_reference"] - cost_star(r["price"], r["fee"])
                for r in mine if r.get("p_reference") is not None
                and cost_star(r.get("price"), r.get("fee")) is not None]
        rm, rlo, rhi, rn = C.mean_ci(real, 1.959964)
        pm, _, _, _ = C.mean_ci(pred, 1.959964)
        b = C.Out(bucket=name, n=rn)
        b.put("mean_predicted_net_edge", C.rnd(pm), "EMPTY_BUCKET")
        b.put("mean_realized_net_edge", C.rnd(rm), "EMPTY_BUCKET")
        b.put("realized_ci", None if rlo is None else [C.rnd(rlo),
                                                       C.rnd(rhi)],
              "FEWER_THAN_TWO_ROWS")
        b.put("predicted_vs_realized_error",
              None if rm is None or pm is None else C.rnd(rm - pm),
              "EMPTY_BUCKET")
        b.put("share_realized_positive",
              C.rnd(sum(1 for x in real if x > 0) / len(real))
              if real else None, "EMPTY_BUCKET")
        buckets.append(b)
    out["by_edge_confidence_bucket"] = buckets
    return out
