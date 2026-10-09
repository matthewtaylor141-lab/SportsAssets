"""D · THE CALIBRATION ENGINE (SHADOW). Every PinnAPI-derived prediction,
joined to what happened, scored, segmented, with its uncertainty.

EXTENDS `bettor_source_calibration`, does not duplicate it. The outcome
classification (RESOLVED / VOID / UNRESOLVED / UNVERIFIED, with the venue
provenance requirement), the "first priced row per fixture" independence
rule, the reliability table and the Murphy decomposition are imported from
there; the Brier, log loss, Wilson and bootstrap helpers were added there.
That module's verdict (the MODEL_TRUST_DRIFT gate row) is untouched: this
engine writes only to its own intel_* tables and gates nothing.

TWO PREDICTION SOURCES, NEVER POOLED (one fixture appears in both):

  DECISION_P_PINNACLE    paper_decisions.p_pinnacle -- the probability a
                         paper decision acted on. Outcome: the decision's
                         valuation outcome when its venue provenance is
                         verified, else the paper settlement of the
                         decision's ENTRY position (WON 1 / LOST 0; VOID and
                         SETTLED_AT_VENUE_PRICE are not binary outcomes and
                         are counted, not scored).
  VALUATION_PROBABILITY  external_valuations.probability, classified by
                         bettor_source_calibration.classify.

INDEPENDENT UNIT: one prediction per fixture per source -- the first priced,
gradeable one (bettor_source_calibration.unique_fixtures' rule), so the
quotes of one coin flip are not counted as many coins.

SEGMENTS: ALL, sport, league, market, probability band, live state
(PREGAME / LIVE / UNKNOWN against the venue's game start), time-to-start
state, liquidity band. Each with n, Brier (+ bootstrap 95% CI), log loss
(+ bootstrap 95% CI), mean probability, observed frequency (+ Wilson 95%
CI) and the binned reliability table. n = 0 -> every score is null with a
reason (the migration's CHECK refuses a 0).

THE FROZEN OUT-OF-SAMPLE OVERLAY PROTOCOL (PROTOCOL below, in code). An
overlay MAY be fitted in shadow. Production probabilities are NEVER
modified: no code path reads an overlay to price anything, and the register
can only store production_applied = false. An overlay's status is
NOT_VALIDATED unless a FORWARD out-of-sample test passes on predictions made
strictly AFTER the overlay was frozen.
"""
from __future__ import annotations

import math

from .. import bettor_source_calibration as SC
from . import common as C

VERSION = "INTEL_CALIBRATION_V1"
SOURCES = ("DECISION_P_PINNACLE", "VALUATION_PROBABILITY")
SEGMENT_KINDS = ("ALL", "sport", "league", "market", "probability_band",
                 "live_state", "time_to_start", "liquidity_band")
PROB_BANDS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))
LIQUIDITY_BANDS = ((0.0, 100.0, "LT_100"), (100.0, 1000.0, "100_1K"),
                   (1000.0, 10000.0, "1K_10K"),
                   (10000.0, float("inf"), "GE_10K"))
MIN_RELIABLE_N = 30
RELIABILITY_BINS = 10
DRIFT_RECENT_DAYS = 14.0
BOOTSTRAP_REPS = 400
MIN_BOOTSTRAP_REPS = 100
BOOTSTRAP_BUDGET = 60000           # resampled events per interval, at most
MAX_SEGMENT_VALUES = 40            # per kind; the rest are pooled as OTHER

EXCEPTIONAL = "EXCEPTIONAL_SETTLEMENT"
PAPER_SETTLEMENT_BASIS = "PAPER_SETTLEMENT"

# ── THE FROZEN FORWARD OUT-OF-SAMPLE PROTOCOL ─────────────────────────
OVERLAY_METHOD = "LOGISTIC_RECALIBRATION_ON_LOGIT_V1"
MIN_FIT_N = 100
MIN_OOS_N = 200
PROTOCOL = {
    "version": "FROZEN_FORWARD_OOS_V1",
    "method": OVERLAY_METHOD,
    "form": "p' = 1 / (1 + exp(-(a + b * logit(p))))",
    "fit": ("Newton-Raphson on the log likelihood (ridge 1e-3 on a and b-1),"
            " over every RESOLVED independent prediction of ONE source made "
            "at or before fit_window_end"),
    "min_fit_n": MIN_FIT_N,
    "freeze": ("params, their SHA-256, fit_n, fit_window_end and this "
               "protocol are stored at frozen_at and are IMMUTABLE (migration "
               "208 trigger). One NOT_VALIDATED overlay per source at a time; "
               "no refit while one is pending"),
    "out_of_sample_set": ("ONLY predictions made strictly AFTER frozen_at -- "
                          "data that did not exist when the overlay was fit"),
    "min_oos_n": MIN_OOS_N,
    "pass_requires_all": [
        "oos_n >= min_oos_n independent fixtures",
        "paired mean log-loss improvement (raw - overlay) > 0 with its 95% "
        "normal CI lower bound > 0",
        "paired mean Brier improvement (raw - overlay) >= 0",
    ],
    "statuses": {
        "NOT_VALIDATED": "fitted; the forward OOS sample is not yet large "
                         "enough, or nothing has been tested -- the default",
        "VALIDATED_SHADOW_ONLY": "passed the frozen forward OOS test. STILL "
                                 "SHADOW: production is not modified",
        "FAILED_OOS": "the forward OOS test was decided and failed",
    },
    "decided_once": "a VALIDATED_SHADOW_ONLY or FAILED_OOS verdict is final",
    "production_probabilities_modified": False,
}


# ═════════════════════════════════════════════════════════════════════
# NORMALISATION: rows -> prediction records
# ═════════════════════════════════════════════════════════════════════

def prob_band(p):
    if p is None:
        return "UNKNOWN"
    for lo, hi in PROB_BANDS:
        if lo <= p < hi or (hi == 1.0 and p == 1.0):
            return "%.1f-%.1f" % (lo, hi)
    return "UNKNOWN"


def liquidity_band(usd):
    if usd is None:
        return "UNKNOWN"
    for lo, hi, name in LIQUIDITY_BANDS:
        if lo <= usd < hi:
            return name
    return "UNKNOWN"


def time_state(predicted_at, game_start):
    """(live_state, time_to_start) against the venue's own game start."""
    if predicted_at is None or game_start is None:
        return "UNKNOWN", "UNKNOWN"
    dt = float(game_start) - float(predicted_at)
    if dt <= 0:
        return "LIVE", "LIVE_IN_PLAY"
    h = dt / 3600.0
    if h < 1:
        return "PREGAME", "PRE_LT_1H"
    if h < 6:
        return "PREGAME", "PRE_1H_6H"
    if h < 24:
        return "PREGAME", "PRE_6H_24H"
    return "PREGAME", "PRE_GE_24H"


def _record(*, source, rid, unit, p, outcome, cls, basis, predicted_at,
            sport, league, market, game_start, liquidity_usd):
    live, tts = time_state(predicted_at, game_start)
    return {"source": source, "id": rid, "unit": unit or "",
            "p": C.num(p), "outcome": outcome, "class": cls, "basis": basis,
            "predicted_at": predicted_at,
            "sport": sport or "UNKNOWN", "league": league or "UNKNOWN",
            "market": market or "UNKNOWN",
            "probability_band": prob_band(C.num(p)),
            "live_state": live, "time_to_start": tts,
            "liquidity_usd": liquidity_usd,
            "liquidity_band": liquidity_band(liquidity_usd)}


def normalize_valuation(row: dict, pm: dict | None = None) -> dict:
    pm = pm or {}
    cls = SC.classify(row)
    o = int(row["outcome"]) if cls == SC.RESOLVED else None
    est = C.jload(row.get("execution_estimate")) or {}
    liq = C.num(est.get("depth_usd")) if isinstance(est, dict) else None
    if liq is None and isinstance(est, dict):
        dq = C.num(est.get("depth_within_limit"))
        px = C.num(row.get("executable_price"))
        liq = dq * px if (dq is not None and px is not None) else None
    rec = _record(
        source="VALUATION_PROBABILITY", rid=row.get("id"),
        unit=row.get("event_key"), p=row.get("probability"), outcome=o,
        cls=cls, basis=row.get("outcome_basis"),
        predicted_at=C.num(row.get("observed_at_epoch"))
        or C.num(row.get("decided_at_epoch")),
        sport=row.get("sport_family") or pm.get("sports_type"),
        league=pm.get("team_league"), market=row.get("market"),
        game_start=C.num(pm.get("game_start")), liquidity_usd=liq)
    rec["gradeable"] = SC._gradeable(row)
    return rec


def decision_outcome(dec: dict, val: dict | None, settlement: dict | None):
    """(outcome, class, basis) for one paper decision's probability."""
    if val:
        cls = SC.classify(val)
        if cls == SC.RESOLVED:
            return int(val["outcome"]), SC.RESOLVED, val.get("outcome_basis")
        if cls == SC.VOID:
            return None, SC.VOID, val.get("outcome_basis")
    if settlement:
        out = str(settlement.get("outcome") or "")
        basis = "%s:%s" % (PAPER_SETTLEMENT_BASIS,
                           settlement.get("evidence_source") or "UNKNOWN")
        if out == "WON":
            return 1, SC.RESOLVED, basis
        if out == "LOST":
            return 0, SC.RESOLVED, basis
        if out == "VOID_REFUND":
            return None, SC.VOID, basis
        if out:
            return None, EXCEPTIONAL, basis
    if val and SC.classify(val) == SC.UNVERIFIED:
        return None, SC.UNVERIFIED, val.get("outcome_basis")
    return None, SC.UNRESOLVED, None


def normalize_decision(dec: dict, val: dict | None = None,
                       settlement: dict | None = None,
                       pm: dict | None = None) -> dict:
    pm = pm or {}
    label = C.jload(dec.get("label")) or {}
    econ = C.jload(dec.get("economics")) or {}
    o, cls, basis = decision_outcome(dec, val, settlement)
    depth = C.num(econ.get("depth_within_limit")) if isinstance(
        econ, dict) else None
    px = C.num(dec.get("limit_price"))
    liq = depth * px if (depth is not None and px is not None) else None
    rec = _record(
        source="DECISION_P_PINNACLE", rid=dec.get("decision_id"),
        unit=(label.get("event_key") or dec.get("fixture")
              or dec.get("us_market_slug")),
        p=dec.get("p_pinnacle"), outcome=o, cls=cls, basis=basis,
        predicted_at=C.epoch(dec.get("decided_at")),
        sport=(val or {}).get("sport_family") or pm.get("sports_type"),
        league=pm.get("team_league") or label.get("competition"),
        market=label.get("market_type") or (val or {}).get("market"),
        game_start=C.num(pm.get("game_start")), liquidity_usd=liq)
    rec["gradeable"] = rec["p"] is not None
    return rec


def independent(records) -> list:
    """One record per (source, fixture): the first GRADEABLE one by
    prediction time (ties on id), else the first one -- the rule of
    bettor_source_calibration.unique_fixtures, applied per source."""
    best: dict = {}
    usable = {(r["source"], r["unit"]) for r in records
              if r.get("gradeable") and r["p"] is not None}
    for r in records:
        key = (r["source"], r["unit"])
        if not r["unit"]:
            continue
        if key in usable and not (r.get("gradeable") and r["p"] is not None):
            continue
        mine = (float(r["predicted_at"] or 0.0), str(r["id"]))
        cur = best.get(key)
        if cur is None or mine < cur[0]:
            best[key] = (mine, r)
    return [v[1] for v in best.values()]


def pairs_of(records) -> list:
    return [(float(r["p"]), int(r["outcome"])) for r in records
            if r["class"] == SC.RESOLVED and r["p"] is not None
            and r["outcome"] in (0, 1)]


# ═════════════════════════════════════════════════════════════════════
# SCORING
# ═════════════════════════════════════════════════════════════════════

def _normal_ci(losses):
    m, sd = SC._mean_sd(losses)
    se = sd / math.sqrt(len(losses))
    return (m - SC.CONFIDENCE_Z * se, m + SC.CONFIDENCE_Z * se)


def score(pairs, *, reps=BOOTSTRAP_REPS) -> dict:
    """Brier, log loss, observed frequency, each with its 95% interval."""
    out = C.Out(n=len(pairs))
    if not pairs:
        why = "NO_RESOLVED_PREDICTIONS_IN_SEGMENT"
        for k in ("brier", "brier_ci_low", "brier_ci_high", "log_loss",
                  "log_loss_ci_low", "log_loss_ci_high", "mean_probability",
                  "observed_frequency", "frequency_ci_low",
                  "frequency_ci_high", "reliability"):
            out.put(k, None, why)
        out["small_sample"] = True
        return out
    out.put("brier", C.rnd(SC.brier(pairs)))
    out.put("log_loss", C.rnd(SC.log_loss(pairs)))
    n = len(pairs)
    if n * MIN_BOOTSTRAP_REPS <= BOOTSTRAP_BUDGET:
        # percentile bootstrap, repetitions bounded so one cycle's cost is
        # bounded however large the segment
        reps = max(MIN_BOOTSTRAP_REPS, min(reps, BOOTSTRAP_BUDGET // n))
        blo, bhi = SC.bootstrap_ci(pairs, SC.brier, reps=reps)
        llo, lhi = SC.bootstrap_ci(pairs, SC.log_loss, reps=reps)
        out["ci_method"] = "PERCENTILE_BOOTSTRAP_%d_REPS_SEED_FIXED" % reps
    else:
        # a mean of per-event losses over a large n: the normal interval on
        # the per-event losses (CLT), stated as such
        blo, bhi = _normal_ci([(p - o) ** 2 for p, o in pairs])
        llo, lhi = _normal_ci([SC.log_loss([(p, o)]) for p, o in pairs])
        out["ci_method"] = "NORMAL_ON_PER_EVENT_LOSSES_N_%d" % n
    why_ci = "ONE_OBSERVATION_HAS_NO_SAMPLING_DISTRIBUTION"
    out.put("brier_ci_low", C.rnd(blo), why_ci)
    out.put("brier_ci_high", C.rnd(bhi), why_ci)
    out.put("log_loss_ci_low", C.rnd(llo), why_ci)
    out.put("log_loss_ci_high", C.rnd(lhi), why_ci)
    k = sum(o for _, o in pairs)
    out.put("mean_probability", C.rnd(sum(p for p, _ in pairs) / n))
    out.put("observed_frequency", C.rnd(k / n))
    flo, fhi = SC.wilson_interval(k, n)
    out.put("frequency_ci_low", C.rnd(flo))
    out.put("frequency_ci_high", C.rnd(fhi))
    out.put("reliability", SC.reliability(pairs, bins=RELIABILITY_BINS))
    out["decomposition"] = SC.decompose(pairs)
    out["small_sample"] = n < MIN_RELIABLE_N
    out["log_loss_eps"] = SC.LOG_LOSS_EPS
    return out


def class_counts(records) -> dict:
    out: dict = {}
    for r in records:
        out[r["class"]] = out.get(r["class"], 0) + 1
    return out


def segments(records) -> list:
    """[{source, segment_kind, segment_value, ...score}] for every segment
    that has at least one independent record (resolved or not)."""
    out = []
    for src in SOURCES:
        mine = [r for r in records if r["source"] == src]
        for kind in SEGMENT_KINDS:
            groups: dict = {}
            for r in mine:
                v = "ALL" if kind == "ALL" else str(r.get(kind) or "UNKNOWN")
                groups.setdefault(v, []).append(r)
            if kind == "ALL" and not groups:
                groups["ALL"] = []
            if len(groups) > MAX_SEGMENT_VALUES:
                # bounded: the largest values keep their own segment, the
                # long tail is pooled and named as such
                keep = sorted(groups, key=lambda k: (-len(groups[k]), k))[
                    :MAX_SEGMENT_VALUES - 1]
                pooled = [r for k, g in groups.items() if k not in keep
                          for r in g]
                groups = {k: groups[k] for k in keep}
                groups["OTHER_POOLED"] = pooled
            for v in sorted(groups):
                got = groups[v]
                s = score(pairs_of(got))
                s.update({"source": src, "segment_kind": kind,
                          "segment_value": v, "records": len(got),
                          "classes": class_counts(got)})
                out.append(s)
    return out


def drift(records, *, now, recent_days=DRIFT_RECENT_DAYS) -> dict:
    """Recent vs earlier Brier per source (regime detection's calibration
    drift input). Null with reason when either side is empty."""
    cut = float(now) - recent_days * 86400.0
    out = {}
    for src in SOURCES:
        mine = [r for r in records if r["source"] == src]
        rec = pairs_of([r for r in mine if (r["predicted_at"] or 0) >= cut])
        old = pairs_of([r for r in mine if (r["predicted_at"] or 0) < cut])
        d = C.Out(recent_n=len(rec), baseline_n=len(old),
                  recent_days=recent_days)
        d.put("recent_brier", C.rnd(SC.brier(rec)),
              "NO_RESOLVED_PREDICTIONS_IN_THE_RECENT_WINDOW")
        d.put("baseline_brier", C.rnd(SC.brier(old)),
              "NO_RESOLVED_PREDICTIONS_BEFORE_THE_RECENT_WINDOW")
        if d["recent_brier"] is None or d["baseline_brier"] is None:
            d.put("brier_change", None, "ONE_SIDE_OF_THE_COMPARISON_IS_EMPTY")
        else:
            d.put("brier_change", C.rnd(d["recent_brier"]
                                        - d["baseline_brier"]))
        out[src] = d
    return out


def uncertainty_for(report: dict | None, *, p, sport=None) -> dict:
    """The calibration uncertainty sizing and allocation charge against p:
    the Wilson 95% half-width of the observed frequency in p's probability
    band (decision source first, valuation source as fallback), and the
    band's measured miscalibration (mean_p - observed). Null with reason
    when the band has no resolved prediction."""
    out = C.Out(p=p)
    if not report or not report.get("segments"):
        out.put("half_width", None, "NO_CALIBRATION_REPORT")
        out.put("miscalibration", None, "NO_CALIBRATION_REPORT")
        out["n"] = 0
        return out
    band = prob_band(C.num(p))
    for src in SOURCES:
        for s in report["segments"]:
            if (s["source"] == src and s["segment_kind"] == "probability_band"
                    and s["segment_value"] == band and s.get("n")):
                lo, hi = s.get("frequency_ci_low"), s.get("frequency_ci_high")
                out["n"] = s["n"]
                out["source"] = src
                out["band"] = band
                out.put("half_width", None if lo is None or hi is None
                        else C.rnd((hi - lo) / 2.0), "NO_INTERVAL")
                mp, ob = s.get("mean_probability"), s.get("observed_frequency")
                out.put("miscalibration", None if mp is None or ob is None
                        else C.rnd(mp - ob), "NO_FREQUENCY")
                return out
    out["n"] = 0
    out["band"] = band
    out.put("half_width", None,
            "NO_RESOLVED_PREDICTION_IN_PROBABILITY_BAND_%s" % band)
    out.put("miscalibration", None,
            "NO_RESOLVED_PREDICTION_IN_PROBABILITY_BAND_%s" % band)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE OVERLAY: FIT, APPLY, FROZEN FORWARD OOS TEST
# ═════════════════════════════════════════════════════════════════════

def _logit(p):
    q = min(max(float(p), 1e-6), 1.0 - 1e-6)
    return math.log(q / (1.0 - q))


def apply_overlay(params: dict, p: float) -> float:
    z = float(params["a"]) + float(params["b"]) * _logit(p)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def fit_overlay(pairs, *, iters=50, ridge=1e-3) -> dict | None:
    """Logistic recalibration a + b*logit(p) by Newton-Raphson. None below
    MIN_FIT_N -- an overlay fitted on too little is not fitted at all."""
    if len(pairs) < MIN_FIT_N:
        return None
    a, b = 0.0, 1.0
    xs = [(_logit(p), o) for p, o in pairs]
    for _ in range(iters):
        ga = gb = 0.0
        haa = hab = hbb = 0.0
        for x, o in xs:
            z = a + b * x
            q = 1.0 / (1.0 + math.exp(-z)) if z >= 0 else (
                math.exp(z) / (1.0 + math.exp(z)))
            r = q - o
            w = q * (1.0 - q)
            ga += r
            gb += r * x
            haa += w
            hab += w * x
            hbb += w * x * x
        ga += ridge * a
        gb += ridge * (b - 1.0)
        haa += ridge
        hbb += ridge
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a -= da
        b -= db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return {"a": round(a, 10), "b": round(b, 10)}


def oos_test(params: dict, pairs) -> dict:
    """The frozen forward test of one overlay on predictions made after its
    freeze. Status NOT_VALIDATED until MIN_OOS_N; then decided once."""
    n = len(pairs)
    out = C.Out(oos_n=n, min_oos_n=MIN_OOS_N, protocol=PROTOCOL["version"])
    if n == 0:
        out["status"] = "NOT_VALIDATED"
        out.put("log_loss_improvement", None,
                "NO_RESOLVED_PREDICTION_AFTER_THE_FREEZE")
        out.put("brier_improvement", None,
                "NO_RESOLVED_PREDICTION_AFTER_THE_FREEZE")
        return out
    ll, bs = [], []
    for p, o in pairs:
        q = apply_overlay(params, p)
        ll.append(SC.log_loss([(p, o)]) - SC.log_loss([(q, o)]))
        bs.append((p - o) ** 2 - (q - o) ** 2)
    m, sd = SC._mean_sd(ll)
    se = sd / math.sqrt(n) if n else 0.0
    lo = m - SC.CONFIDENCE_Z * se
    mb = sum(bs) / n
    out.put("log_loss_improvement", C.rnd(m))
    out["log_loss_improvement_ci_low"] = C.rnd(lo)
    out["log_loss_improvement_ci_high"] = C.rnd(m + SC.CONFIDENCE_Z * se)
    out.put("brier_improvement", C.rnd(mb))
    passed = bool(m > 0 and lo > 0 and mb >= 0)
    if n < MIN_OOS_N:
        out["status"] = "NOT_VALIDATED"
        out["why"] = "FORWARD_OOS_SAMPLE_%d_BELOW_%d" % (n, MIN_OOS_N)
    else:
        out["status"] = "VALIDATED_SHADOW_ONLY" if passed else "FAILED_OOS"
        out["why"] = ("PASSED_ALL_CONDITIONS" if passed
                      else "FAILED_AT_LEAST_ONE_CONDITION")
    out["production_probabilities_modified"] = False
    return out


def overlay_plan(records, overlays: list, *, now) -> list:
    """What the overlay register should do this cycle -- pure.

    Returns actions: {"op": "FIT", "source", "params", "fit_n",
    "fit_window_end"} or {"op": "EVALUATE", "overlay_id", "result"}."""
    actions = []
    for src in SOURCES:
        mine = [r for r in records if r["source"] == src]
        pending = [o for o in overlays if o["source"] == src
                   and o["status"] == "NOT_VALIDATED"]
        if pending:
            ov = sorted(pending, key=lambda o: o["frozen_at"])[-1]
            frozen = float(ov["frozen_at"])
            fwd = pairs_of([r for r in mine
                            if (r["predicted_at"] or 0.0) > frozen])
            params = C.jload(ov["params"])
            actions.append({"op": "EVALUATE", "overlay_id": ov["overlay_id"],
                            "source": src, "result": oos_test(params, fwd)})
            continue
        if any(o["source"] == src for o in overlays):
            # a decided overlay exists; refits are a later, explicit choice
            decided = [o for o in overlays if o["source"] == src]
            last = sorted(decided, key=lambda o: o["frozen_at"])[-1]
            if float(now) - float(last["frozen_at"]) < 30 * 86400.0:
                continue
        fit_set = [r for r in mine if (r["predicted_at"] or 0.0) <= now]
        pairs = pairs_of(fit_set)
        params = fit_overlay(pairs)
        if params is None:
            continue
        actions.append({
            "op": "FIT", "source": src, "params": params,
            "fit_n": len(pairs),
            "fit_window_end": max(r["predicted_at"] or 0.0 for r in fit_set
                                  if r["class"] == SC.RESOLVED)})
    return actions


def report(records, *, now, overlays=None) -> dict:
    """The full calibration payload (records already independent)."""
    segs = segments(records)
    return C.envelope(
        version=VERSION, computed_at=now,
        segments=segs,
        overall={s["source"]: s for s in segs if s["segment_kind"] == "ALL"},
        classes={src: class_counts([r for r in records
                                    if r["source"] == src])
                 for src in SOURCES},
        drift=drift(records, now=now),
        protocol=PROTOCOL, overlays=overlays or [],
        independent_unit="ONE_PREDICTION_PER_FIXTURE_PER_SOURCE_FIRST_PRICED",
        production_probabilities_modified=False)


# ═════════════════════════════════════════════════════════════════════
# THE READ
# ═════════════════════════════════════════════════════════════════════

VALUATIONS_SQL = """
    SELECT id, version, devig_method, sport_family, market, event_key,
           payout_event, probability, outcome_known, outcome, outcome_basis,
           buy_intent, us_market_slug, execution_estimate, executable_price,
           extract(epoch FROM observed_at)::float8 AS observed_at_epoch,
           extract(epoch FROM decided_at)::float8  AS decided_at_epoch
      FROM external_valuations
     WHERE decided_at >= to_timestamp($1)
       AND ($2::text IS NULL OR experiment_id = $2)
     ORDER BY decided_at DESC
     LIMIT $3
"""

DECISIONS_SQL = """
    SELECT decision_id, decided_at, valuation_id, us_market_slug,
           holding_side, fixture, label, verdict, p_pinnacle, limit_price,
           economics, strategy
      FROM paper_decisions
     WHERE p_pinnacle IS NOT NULL
       AND decided_at >= to_timestamp($1)
       AND ($2::text IS NULL OR account_id = $2)
     ORDER BY decided_at DESC
     LIMIT $3
"""


async def load_records(conn, *, now, days=180.0, account_id=C.PAPER_ACCOUNT,
                       experiment_id=None, limit=None) -> list:
    from . import reads as R

    limit = int(limit or R.MAX_ROWS)
    since = float(now) - float(days) * 86400.0
    vals = [dict(r) for r in await conn.fetch(VALUATIONS_SQL, since,
                                              experiment_id, limit)]
    decs = [dict(r) for r in await conn.fetch(DECISIONS_SQL, since,
                                              account_id, limit)]
    pm = await R.premap(conn, [v.get("us_market_slug") for v in vals]
                        + [d.get("us_market_slug") for d in decs])
    joined = await R.valuations_by_id(conn, [d.get("valuation_id")
                                             for d in decs])
    groups = await R.entry_groups(conn, [d["decision_id"] for d in decs
                                         if d.get("verdict") == "ENTER"])
    setts = await R.latest_settlements(conn, group_ids=groups.values())
    # THE NORMALISATION RUNS IN A WORKER THREAD (RC6). Up to 2 x MAX_ROWS
    # rows (20,000 valuations + 20,000 decisions) are normalised here, and on
    # the event loop that was the longest hold of the whole intel cycle:
    # 0.36-0.42 s at the bound on a quiet local core (LOCAL BENCHMARK ONLY,
    # synthetic rows at the LIMITs), several times that on the API's one
    # shared production CPU -- where 19 of the 24 intel cycles of
    # 2026-10-08 completed 0-12 s after a >= 2 s loop stall record
    # (render-ops logs, 'loop stall' / 'intel shadow cycle'), and Render
    # restarted the API twice for unanswered /healthz. Pure over what was
    # read above, so the loop keeps serving while it runs -- through
    # common.offload (in the API process, its CPU lane: cpu_lane).
    return await C.offload(build_records, vals, decs, pm, joined, groups,
                           setts)


def build_records(vals, decs, pm, joined, groups, setts) -> list:
    """`load_records`' reads -> calibration records. PURE (no connection, no
    clock): it runs in a worker thread, and it is exactly the loop that ran
    inline before."""
    sidx = {(s["group_id"], s["us_market_slug"], str(s["holding_side"])): s
            for s in setts.values()}
    recs = [normalize_valuation(v, pm.get(v.get("us_market_slug")))
            for v in vals]
    for d in decs:
        g = groups.get(d["decision_id"])
        st = sidx.get((g, d.get("us_market_slug"),
                       str(d.get("holding_side")))) if g else None
        val = joined.get(int(d["valuation_id"])) if d.get(
            "valuation_id") is not None else None
        recs.append(normalize_decision(d, val, st,
                                       pm.get(d.get("us_market_slug"))))
    return recs
