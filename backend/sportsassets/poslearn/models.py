"""1 · THE MODEL TOURNAMENT: the champion, the challengers, how each is
trained BEFORE registration, and how each forecasts a forward opportunity.
Pure; no I/O.

  M0_PINNAPI_RAW                CHAMPION. The current approved PinnAPI policy:
                                the raw de-vigged PinnAPI probability with the
                                approved entry rule (gross edge >= the active
                                paper_policy_parameter_heads threshold, net
                                edge after fees > 0). It stays champion until a
                                challenger shows a PREDECLARED, statistically
                                credible improvement AND a human approves.
  M1_PINNAPI_CALIBRATED         logistic recalibration of p, fitted on the
                                training window by intel/calibration.
                                fit_overlay (reused, not re-derived), frozen
                                into the registration.
  M2_PINNAPI_PLUS_MICROSTRUCTURE ridge logistic on logit(p) + the venue
                                microstructure as of t (cross-market gap,
                                spread, liquidity, PinnAPI volatility).
                                Abstains (named) without a book as of t.
  M3_PINNAPI_PLUS_SCOUT         AWAITING_FEATURES: Scout features do not exist
                                at this SHA. Registered as a placeholder that
                                forecasts nothing; once Scout features are
                                recorded it is registered as a NEW version
                                with its features and parameters declared.
  M4_CROSS_MARKET_CONSENSUS     AWAITING_SOURCE: every recorded probability is
                                Pinnacle (pinnapi raw websocket, or the legacy
                                the-odds-api Pinnacle book); the
                                valuation_corroboration table (195) has no
                                writer at this SHA and stores no probability;
                                no Kalshi price is recorded for the same
                                events. A consensus needs an INDEPENDENT second
                                market price, so none is fitted.
  M5_<SPORT>_SPECIFIC           NFL, MLB, NCAAF, NBA, NHL, SOCCER, TENNIS: the
                                M1 recalibration fitted on THAT sport's
                                training rows only (no sport leakage); it
                                abstains, by name, on every other sport, so
                                its coverage is reported honestly.

EVERY CHALLENGER RECEIVES THE SAME FUTURE EVENTS: the runner offers every
captured opportunity to every ACTIVE_FORWARD registration and records a
forecast row for each -- ABSTAIN is an explicit row with its reason, never a
silent omission.
"""
from __future__ import annotations

from ..intel import calibration as CAL
from . import common as C
from . import logistic as LR

VERSION = "POSLEARN_MODELS_V1"
FAMILY = "MODEL_TOURNAMENT_V1"
CHAMPION = "M0_PINNAPI_RAW"
SPORTS = ("NFL", "MLB", "NCAAF", "NBA", "NHL", "SOCCER", "TENNIS")
M2_FEATURES = ("logit_p", "cross_market_gap", "spread", "log_liquidity",
               "pinnapi_vol_1h")
TRAINING_DAYS = 180.0
MIN_SAMPLE = 300
MIN_SAMPLE_SPORT = 200
MIN_COVERAGE = 0.5
ALPHA = 0.05
DEFAULT_THRESHOLD_PP = 0.5
THRESHOLD_BASIS_DEFAULT = (
    "PAPER_POLICY_PARAMETER_HEADS_UNREADABLE_SHIPPED_DEFAULT_0.5PP")

#: (subject, kind of model, placeholder status or None)
SPECS = (
    (CHAMPION, "RAW", None),
    ("M1_PINNAPI_CALIBRATED", "CALIBRATED", None),
    ("M2_PINNAPI_PLUS_MICROSTRUCTURE", "MICROSTRUCTURE", None),
    ("M3_PINNAPI_PLUS_SCOUT", "SCOUT", "AWAITING_FEATURES"),
    ("M4_CROSS_MARKET_CONSENSUS", "CONSENSUS", "AWAITING_SOURCE"),
) + tuple(("M5_%s_SPECIFIC" % s, "SPORT:" + s, None) for s in SPORTS)

#: THE FAMILY SIZE, FIXED BY THE DECLARED PLAN (every challenger above),
#: not by how many happen to be trainable -- multiple-testing defence.
FAMILY_SIZE = len(SPECS) - 1

PLACEHOLDER_WHY = {
    "AWAITING_FEATURES": (
        "Scout features do not exist at this SHA. This model consumes them "
        "once they are recorded; it is re-registered as a new version with "
        "its features, parameters and training window declared before any "
        "forward evaluation."),
    "AWAITING_SOURCE": (
        "No independent second market source is recorded: every probability "
        "is Pinnacle (pinnapi.com raw websocket or the legacy "
        "the-odds-api.com Pinnacle book); valuation_corroboration (migration "
        "195) has no writer at this SHA and stores no probability; no Kalshi "
        "price is recorded for the same events. A cross-market consensus is "
        "not fitted from one source."),
}


def sport_key(sport, league) -> str:
    s = str(sport or "").lower()
    lg = str(league or "").lower()
    unknown = lg in ("", "unknown")
    if lg == "nfl" or "nfl" in s:
        return "NFL"
    if lg in ("ncaaf", "cfb", "ncaa football") or "ncaaf" in s:
        return "NCAAF"
    if lg == "mlb" or (s.startswith("baseball") and unknown):
        return "MLB"
    if lg == "nba" or "basketball_nba" in s:
        return "NBA"
    if lg == "nhl" or "icehockey_nhl" in s or (
            s in ("hockey", "icehockey") and unknown):
        return "NHL"
    if s.startswith("soccer"):
        return "SOCCER"
    if s.startswith("tennis"):
        return "TENNIS"
    return "OTHER"


def decision(p, price, fee, threshold_pp):
    """The approved entry rule applied to a probability.
    (action, predicted_net_edge, abstain_reason)."""
    if p is None:
        return "ABSTAIN", None, "NO_PROBABILITY"
    if price is None:
        return "ABSTAIN", None, "NO_PRICE_AS_OF_T"
    if fee is None:
        return "ABSTAIN", None, "FEE_NOT_RECORDED_NET_EDGE_UNMEASURED"
    gross = p - price
    net = gross - fee
    ok = gross >= float(threshold_pp) / 100.0 - 1e-12 and net > 0
    return ("ENTER" if ok else "PASS"), net, None


# ═════════════════════════════════════════════════════════════════════
# TRAINING (before registration, on outcomes known by registration only)
# ═════════════════════════════════════════════════════════════════════

def recalibrate(pairs):
    """The M1 / M5 recalibration: intel/calibration.fit_overlay (reused),
    ACCEPTED ONLY IF IT CONVERGED -- finite, bounded, and no worse in
    likelihood than leaving p alone. Plain Newton from (a=0, b=1) diverges
    on extreme probabilities (observed: a=5000, b=587461 on p in {0.03,
    0.97}); then the damped IRLS of poslearn/logistic is used on logit(p),
    and the parameters say which method produced them and why."""
    if len(pairs) < CAL.MIN_FIT_N:
        return None
    ov = CAL.fit_overlay(pairs)
    ok = False
    if ov is not None:
        a, b = C.num(ov.get("a")), C.num(ov.get("b"))
        if a is not None and b is not None and abs(a) <= 20 and 0 < b <= 20:
            fitted = LR.nll([(CAL.apply_overlay(ov, p), o) for p, o in pairs])
            ok = fitted <= LR.nll(pairs) + 1e-9
    if ok:
        return {"method": "INTEL_FIT_OVERLAY", "a": ov["a"], "b": ov["b"]}
    lr = LR.fit([{"logit_p": C.logit(p)} for p, _ in pairs],
                [int(o) for _, o in pairs], ["logit_p"], ridge=1e-3)
    if lr is None:
        return None
    return {"method": "DAMPED_IRLS_FALLBACK", "lr": lr,
            "why": "intel fit_overlay did not converge on this training set "
                   "(non-finite, out of bounds, or worse than identity): %s"
                   % ({k: ov.get(k) for k in ("a", "b")} if ov else None)}


def apply_recalibration(params, p):
    if params.get("method") == "DAMPED_IRLS_FALLBACK":
        return LR.predict(params["lr"], {"logit_p": C.logit(p)})["p"]
    return CAL.apply_overlay(params, p)


def train(subject, kind, rows, *, now) -> dict:
    """rows: resolved training records {p, o, features, sport_key,
    decided_at, outcome_at}, ALL with outcome_at <= now. Returns
    {"trainable": bool, "why", "params", "features", "window", "n"}."""
    rows = [r for r in rows if r.get("outcome_at") is not None
            and r["outcome_at"] <= now and r.get("p") is not None
            and r.get("o") in (0, 1)]
    if kind == "RAW":
        return {"trainable": True, "params": {}, "features": ["p_reference"],
                "window": None, "n": 0}
    if kind in ("SCOUT", "CONSENSUS"):
        return {"trainable": False, "why": PLACEHOLDER_WHY[
            "AWAITING_FEATURES" if kind == "SCOUT" else "AWAITING_SOURCE"]}
    if kind.startswith("SPORT:"):
        want = kind.split(":", 1)[1]
        rows = [r for r in rows if r.get("sport_key") == want]
    if kind == "MICROSTRUCTURE":
        rows = [r for r in rows
                if r["features"].get("cross_market_gap") is not None]
        params = LR.fit([r["features"] for r in rows],
                        [int(r["o"]) for r in rows], M2_FEATURES)
        feats = list(M2_FEATURES)
    else:
        params = recalibrate([(float(r["p"]), int(r["o"])) for r in rows])
        feats = ["p_reference"]
    if params is None:
        need = (2 * (len(M2_FEATURES) + 1) if kind == "MICROSTRUCTURE"
                else CAL.MIN_FIT_N)
        return {"trainable": False,
                "why": "INSUFFICIENT_TRAINING_DATA_N_%d_NEEDS_%d" % (
                    len(rows), need)}
    starts = [r["decided_at"] for r in rows if r.get("decided_at")]
    ends = [r["outcome_at"] for r in rows]
    return {"trainable": True, "params": params, "features": feats,
            "n": len(rows),
            "window": {"start": min(starts) if starts else min(ends) - 1.0,
                       "end": max(ends)}}


def document(subject, kind, trained, *, version, threshold_pp,
             threshold_basis, placeholder=None) -> dict:
    """The registration document: everything that will judge this model,
    declared before its first forward forecast."""
    champion = subject == CHAMPION
    z = C.adjusted_z(ALPHA, FAMILY_SIZE)
    n_min = MIN_SAMPLE_SPORT if kind.startswith("SPORT:") else MIN_SAMPLE
    win = trained.get("window") if trained else None
    doc = {
        "subject_id": subject, "version": version, "kind": "MODEL",
        "family": FAMILY, "role": "CHAMPION" if champion else "CHALLENGER",
        "model_kind": kind, "code_version": VERSION,
        "training_window": ({"start": win["start"], "end": win["end"],
                             "n": trained.get("n"),
                             "rule": "resolved independent PinnAPI "
                                     "valuations whose outcome was known "
                                     "at or before registration"}
                            if win else "NOT_TRAINED"),
        "features": (trained or {}).get("features") or [],
        "parameters": (trained or {}).get("params") or {},
        "decision_rule": {
            "rule": "ENTER iff gross edge (p - price) >= threshold and net "
                    "edge after the recorded fee > 0; ABSTAIN (named) when "
                    "p, price or fee is missing",
            "threshold_pp": threshold_pp, "threshold_basis": threshold_basis},
        "validation_method": (
            "PROSPECTIVE FORWARD: forecasts recorded before outcomes on "
            "opportunities strictly after registration; scored only on "
            "outcomes after registration; paired against the champion on the "
            "same opportunities"),
        "minimum_sample": 1 if champion else n_min,
        "hypothesis_family": FAMILY,
        "family_size": FAMILY_SIZE,
        "metrics": ["brier", "log_loss", "calibration_reliability",
                    "net_predicted_edge", "realized_edge", "coverage",
                    "sample_size", "confidence_intervals"],
    }
    if champion:
        doc["promotion_threshold"] = {
            "role": "CHAMPION: stays champion until a challenger meets its "
                    "predeclared criteria, passes Karen and Audrey, and a "
                    "human approves"}
        doc["failure_threshold"] = {"role": "CHAMPION: not failed by the "
                                            "tournament; replaced only by "
                                            "an approved challenger"}
    else:
        doc["promotion_threshold"] = {
            "test": "PAIRED_FIXED_SAMPLE_VS_CHAMPION",
            "sample": "the FIRST minimum_sample opportunities by "
                      "opportunity_at on which this model and the champion "
                      "both forecast a probability and the outcome RESOLVED; "
                      "decided once, never re-tested on a larger sample",
            "requires_all": [
                "paired mean log-loss improvement (champion - challenger) "
                "lower bound at z_adjusted > 0",
                "paired mean Brier improvement >= 0",
                "coverage >= %.2f of the opportunities offered" % MIN_COVERAGE],
            "alpha": ALPHA, "family_size": FAMILY_SIZE,
            "adjustment": "BONFERRONI", "z_adjusted": round(z, 6)}
        doc["failure_threshold"] = {
            "rule": "paired mean log-loss improvement upper bound at "
                    "z_adjusted < 0 on the same fixed sample -> "
                    "FAILED_FORWARD",
            "z_adjusted": round(z, 6)}
    if placeholder:
        doc["placeholder"] = {"status": placeholder,
                              "why": PLACEHOLDER_WHY[placeholder]}
    return doc


# ═════════════════════════════════════════════════════════════════════
# FORECAST
# ═════════════════════════════════════════════════════════════════════

def forecast(doc: dict, opp: dict) -> dict:
    """One forward forecast for one opportunity under a frozen document."""
    kind = doc["model_kind"]
    f = opp.get("features") or {}
    p_raw = C.num(opp.get("p_reference"))
    params = doc.get("parameters") or {}
    out = {"probability": None, "output": {"model_kind": kind}}
    p, why = None, None
    if p_raw is None:
        why = "NO_PROBABILITY"
    elif kind == "RAW":
        p = p_raw
    elif kind == "CALIBRATED":
        p = apply_recalibration(params, p_raw)
    elif kind.startswith("SPORT:"):
        want = kind.split(":", 1)[1]
        got = sport_key(opp.get("sport"), opp.get("league"))
        if got != want:
            why = "OUT_OF_SCOPE_SPORT_%s" % got
        else:
            p = apply_recalibration(params, p_raw)
    elif kind == "MICROSTRUCTURE":
        if f.get("cross_market_gap") is None:
            why = "NO_VENUE_BOOK_AS_OF_T"
        else:
            got = LR.predict(params, f)
            p = got["p"]
            out["output"]["p_ci"] = [C.rnd(got["p_lo"]), C.rnd(got["p_hi"])]
    else:
        why = "MODEL_NOT_FORECASTING_%s" % kind
    if p is None:
        out.update(action="ABSTAIN", abstain_reason=why,
                   predicted_net_edge=None)
        return out
    rule = doc["decision_rule"]
    action, net, why2 = decision(p, C.num(opp.get("price")),
                                 C.num(opp.get("fee")), rule["threshold_pp"])
    out["probability"] = C.rnd(p, 10)
    out["predicted_net_edge"] = None if net is None else C.rnd(net, 10)
    # a probability is scored even when the entry rule cannot be applied
    out["action"] = "SCORE" if action == "ABSTAIN" else action
    out["output"]["decision_unmeasured"] = why2
    out["abstain_reason"] = None
    return out
