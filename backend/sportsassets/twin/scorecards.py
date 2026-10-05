"""AGENT FINANCIAL SCORECARDS: ONE PER AGENT, ECONOMIC CONTRIBUTION ONLY.

Never volume of analysis: no count of reviews, findings, messages or
reports is a score (the database refuses such metric names). Every metric
carries value, numerator, denominator, sample, CI and status (common.metric);
unmeasured is UNAVAILABLE / UNPROVEN with a reason, never 0; a sample below
its minimum is INSUFFICIENT_SAMPLE (shown, nothing concluded).

BOOKS. PAPER and ACTUAL metrics are separate rows; a metric derived from a
twin world is COUNTERFACTUAL and its basis book is named in the metric
(`..._paper_basis` / `..._actual_basis`). Nothing is summed across books.

  DEREK      predicted vs realized edge per contract (and their paired gap),
             calibration (Brier) of the decision probability, accepted-
             opportunity P&L, refusal quality, false-positive EV, false
             negatives (refused decisions whose outcome later paid)
  XAVIER     incremental P&L vs HOLD, drawdown reduction (frozen value-add),
             capital-hours released, fees created, good-exit rate, value of
             REALLOCATE (UNAVAILABLE: REALLOCATE is shadow only)
  ARCHER      slippage saved, spread captured, fill improvement, adverse
             selection avoided, execution alpha, capital-hours saved
             (through pos_iface_eddie_execution; absent -> UNAVAILABLE)
  SCOUT      features proposed / validated, incremental Brier / log-loss,
             incremental realized edge, false discoveries, feature decay
             (through pos_iface_scout_feature_effects; absent -> UNAVAILABLE)
  KAREN      valid-defect precision, loss avoided / profit sacrificed (the
             KAREN_BLOCK_ACCEPTED world), false-block rate, recall
             (UNAVAILABLE: no independent defect census)
  ALLOCATOR  shadow allocator vs equal-weight and vs independent sizing,
             drawdown, utilization, concentration (correlation) reduction,
             profit per capital-hour -- all COUNTERFACTUAL
  AUDREY     risk disagreements detected and reconciliation accuracy (her
             independent risk recompute), accounting accuracy (postmortem
             residual), false alarms (her findings Karen upheld challenges
             against), errors before financial impact (UNAVAILABLE)
"""
from __future__ import annotations

import math

from . import common as C
from . import reads as R

VERSION = "TWIN_SCORECARDS_V1"
MIN_N = 30
RULES = {"version": 1, "min_sample": MIN_N, "ci": "95% normal mean / Wilson",
         "economic_contribution_only": True,
         "never_summed_across_books": True, "label": C.LABEL}


def M(agent, name, **kw):
    return C.metric(agent, name, **kw)


def _mean_metric(agent, name, xs, *, book, basis, unit, why_empty,
                 min_sample=MIN_N):
    xs = [x for x in xs if x is not None]
    if not xs:
        return M(agent, name, book=book, basis=basis, unit=unit,
                 reason=why_empty, sample=0)
    return M(agent, name, book=book, basis=basis, unit=unit,
             value=sum(xs) / len(xs), numerator=sum(xs),
             denominator=len(xs), sample=len(xs), ci=C.mean_ci(xs),
             min_sample=min_sample)


def _rate_metric(agent, name, k, n, *, book, basis, why_empty,
                 min_sample=MIN_N):
    if not n:
        return M(agent, name, book=book, basis=basis, unit="rate",
                 reason=why_empty, sample=0)
    return M(agent, name, book=book, basis=basis, unit="rate",
             value=k / n, numerator=k, denominator=n, sample=n,
             ci=C.wilson(k, n), min_sample=min_sample)


# ═════════════════════════════════════════════════════════════════════
# DEREK
# ═════════════════════════════════════════════════════════════════════

def derek(streams: dict) -> list:
    out = []
    for book, st in sorted(streams.items()):
        pos = [p for p in st.positions if p["q"] > 0]
        done = [p for p in pos if p["realized_pnl_usd"] is not None]
        pred = [p["model_edge_pc"] for p in done]
        real = [p["realized_pnl_usd"] / p["q"] for p in done]
        gap = [r - p for r, p in zip(real, pred, strict=True) if p is not None]
        none = "NO_SETTLED_POSITION_IN_WINDOW"
        out.append(_mean_metric("DEREK", "predicted_edge_per_contract", pred,
                                book=book, basis="decision p - planned price"
                                " on settled positions", unit="usd/contract",
                                why_empty=none))
        out.append(_mean_metric("DEREK", "realized_edge_per_contract", real,
                                book=book, basis="realized net P&L / qty on "
                                "settled positions", unit="usd/contract",
                                why_empty=none))
        out.append(_mean_metric("DEREK", "realized_minus_predicted_edge", gap,
                                book=book, basis="paired per position",
                                unit="usd/contract", why_empty=none))
        cal = [(p["p"] - p["payoff"]) ** 2 for p in done
               if p["p"] is not None and p["payoff"] in (0.0, 1.0)]
        out.append(_mean_metric("DEREK", "calibration_brier", cal, book=book,
                                basis="decision probability vs ordinary "
                                "settlement (WON/LOST)", unit="brier",
                                why_empty="NO_ORDINARY_SETTLEMENT"))
        out.append(_mean_metric("DEREK", "accepted_opportunity_pnl",
                                [p["realized_pnl_usd"] for p in done],
                                book=book, basis="realized net P&L per "
                                "accepted, settled position (numerator = "
                                "total)", unit="usd/position",
                                why_empty=none))
        fps = [p for p in done if p["realized_pnl_usd"] < 0
               and p["p"] is not None and p["d"] is not None]
        out.append(_mean_metric(
            "DEREK", "false_positive_ev",
            [p["q"] * (p["p"] - p["d"]) for p in fps], book=book,
            basis="predicted EV (q x (p - planned price)) claimed on accepted "
            "positions that lost (numerator = total)", unit="usd/position",
            why_empty="NO_LOSING_SETTLED_POSITION_WITH_A_PREDICTION"))
        if book != "PAPER":
            for name in ("refusal_quality", "false_negative_rate"):
                out.append(M("DEREK", name, book=book, basis="refusals",
                             reason="NO_ACTUAL_ANALOG_REFUSALS_ARE_PAPER_"
                             "DECISIONS"))
            continue
        hyp = []
        for o in st.opps:
            if o["recorded_entered"] or o["recorded_enter_verdict"]:
                continue
            orc = st.oracle.get(o["subject_id"]) or {}
            if o.get("d") is None or orc.get("payoff") is None:
                continue
            hyp.append(orc["payoff"] - o["d"] - (o.get("fee_pc") or 0.0))
        good = sum(1 for h in hyp if h <= 0)
        why = "NO_REFUSED_DECISION_WITH_A_PLANNED_PRICE_AND_KNOWN_OUTCOME"
        out.append(_rate_metric(
            "DEREK", "refusal_quality", good, len(hyp), book=book,
            basis="refusals whose outcome would have lost or broken even at "
            "the planned price and fee", why_empty=why))
        out.append(_rate_metric(
            "DEREK", "false_negative_rate", len(hyp) - good, len(hyp),
            book=book, basis="refusals whose outcome would have paid net of "
            "planned price and fee", why_empty=why))
    return out


# ═════════════════════════════════════════════════════════════════════
# XAVIER
# ═════════════════════════════════════════════════════════════════════

def xavier(streams: dict, value_add: list) -> list:
    out = []
    for book, st in sorted(streams.items()):
        pos = [p for p in st.positions if p["q"] > 0]
        inc = [p["management_usd"] for p in pos
               if p["management_usd"] is not None]
        out.append(_mean_metric(
            "XAVIER", "incremental_pnl_vs_hold", inc, book=book,
            basis="management value per position (actions vs holding the "
            "entry inventory to settlement; a held position is a measured "
            "0); numerator = total", unit="usd/position",
            why_empty="NO_POSITION_WITH_A_KNOWN_HOLD_COUNTERFACTUAL"))
        va = [r for r in value_add if r["position_kind"] == book]
        dd = [C.num(((r["incremental"] or {}).get(
            "ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT") or {}).get(
            "max_drawdown_usd")) for r in va]
        out.append(_mean_metric(
            "XAVIER", "drawdown_reduction", dd, book=book,
            basis="xavier_value_add FINAL: Xavier's worst mark minus HOLD's "
            "(positive = shallower drawdown)", unit="usd/position",
            why_empty="NO_FINAL_VALUE_ADD_RECORD"))
        fees = [C.num(((r["incremental"] or {}).get(
            "ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT") or {}).get("fees_usd"))
            for r in va]
        if any(f is not None for f in fees):
            out.append(_mean_metric(
                "XAVIER", "fees_created", fees, book=book,
                basis="xavier_value_add FINAL: Xavier's fees minus HOLD's",
                unit="usd/position", why_empty="NO_FINAL_VALUE_ADD_RECORD"))
        else:
            managed = [p for p in pos if p["sell_fills"]]
            out.append(_mean_metric(
                "XAVIER", "fees_created",
                [sum(s["fee_usd"] for s in p["sell_fills"]) for p in managed],
                book=book, basis="sale fees on managed positions",
                unit="usd/position", why_empty="NO_MANAGED_POSITION"))
        rel = []
        for p in pos:
            if not p["sell_fills"] or p["close_at"] is None:
                continue
            sold = sum(s["qty"] or 0.0 for s in p["sell_fills"])
            if sold < p["q"] - 1e-9 or p["payoff_at"] is None:
                continue
            if p["payoff_at"] > p["close_at"] and p["cost_usd"]:
                rel.append(p["cost_usd"] * (p["payoff_at"] - p["close_at"])
                           / 3600.0)
        out.append(M("XAVIER", "capital_hours_released", book=book,
                     basis="entry cost x hours between a full exit and the "
                     "contract's settlement", unit="usd-hours",
                     value=sum(rel) if rel else None,
                     numerator=sum(rel) if rel else None,
                     denominator=len(rel) if rel else None,
                     sample=len(rel), reason=None if rel else
                     "NO_FULL_EXIT_BEFORE_A_KNOWN_SETTLEMENT",
                     min_sample=MIN_N))
        managed = [p for p in pos if p["sell_fills"]
                   and p["management_usd"] is not None]
        good = sum(1 for p in managed if p["management_usd"] > 0)
        out.append(_rate_metric(
            "XAVIER", "good_exit_rate", good, len(managed), book=book,
            basis="managed positions whose actions beat holding (bad exits = "
            "denominator - numerator)",
            why_empty="NO_MANAGED_POSITION_WITH_A_KNOWN_HOLD_COUNTERFACTUAL"))
        out.append(M("XAVIER", "reallocate_value", book=book,
                     basis="REALLOCATE recommendations",
                     reason="REALLOCATE_IS_SHADOW_ONLY_NO_REALLOCATION_WAS_"
                     "EXECUTED_SO_ITS_VALUE_IS_UNMEASURED"))
    return out


# ═════════════════════════════════════════════════════════════════════
# ARCHER / SCOUT (interfaces)
# ═════════════════════════════════════════════════════════════════════

ARCHER_METRICS = ("slippage_saved_per_contract", "spread_captured",
                 "fill_improvement", "adverse_selection_avoided",
                 "execution_alpha", "capital_hours_saved")
SCOUT_METRICS = ("features_proposed", "features_validated",
                 "incremental_brier", "incremental_log_loss",
                 "incremental_realized_edge", "false_discoveries",
                 "feature_decay")


def archer(rows, why) -> list:
    b = "COUNTERFACTUAL"
    if rows is None:
        return [M("ARCHER", m, book=b, basis="pos_iface_eddie_execution",
                  reason=why) for m in ARCHER_METRICS]
    g = lambda r, k: C.num(r.get(k))
    diff = lambda a, c: [g(r, a) - g(r, c) for r in rows
                         if g(r, a) is not None and g(r, c) is not None]
    spread = [(g(r, "baseline_vwap") - g(r, "eddie_vwap")) / g(r, "spread_pc")
              for r in rows if None not in (g(r, "baseline_vwap"),
                                            g(r, "eddie_vwap"),
                                            g(r, "spread_pc"))
              and g(r, "spread_pc") > 0]
    alpha = [g(r, "qty") * (g(r, "baseline_vwap") - g(r, "eddie_vwap"))
             + g(r, "baseline_fee_usd") - g(r, "eddie_fee_usd") for r in rows
             if None not in (g(r, "qty"), g(r, "baseline_vwap"),
                             g(r, "eddie_vwap"), g(r, "baseline_fee_usd"),
                             g(r, "eddie_fee_usd"))]
    ch = [g(r, "capital_hours_saved") for r in rows
          if g(r, "capital_hours_saved") is not None]
    basis = "pos_iface_eddie_execution vs the baseline execution"
    return [
        _mean_metric("ARCHER", "slippage_saved_per_contract",
                     diff("baseline_vwap", "eddie_vwap"), book=b, basis=basis,
                     unit="usd/contract", why_empty="NO_COMPARABLE_ROW"),
        _mean_metric("ARCHER", "spread_captured", spread, book=b, basis=basis,
                     unit="share of spread", why_empty="NO_SPREAD_COLUMN_"
                     "OR_ROW"),
        _mean_metric("ARCHER", "fill_improvement",
                     diff("eddie_fill_ratio", "baseline_fill_ratio"), book=b,
                     basis=basis, unit="fill ratio",
                     why_empty="NO_FILL_RATIO_COLUMN_OR_ROW"),
        _mean_metric("ARCHER", "adverse_selection_avoided",
                     diff("eddie_markout_pc", "baseline_markout_pc"), book=b,
                     basis=basis, unit="usd/contract",
                     why_empty="NO_MARKOUT_COLUMN_OR_ROW"),
        _mean_metric("ARCHER", "execution_alpha", alpha, book=b, basis=basis,
                     unit="usd/decision", why_empty="NO_COMPARABLE_ROW"),
        M("ARCHER", "capital_hours_saved", book=b, basis=basis,
          unit="usd-hours", value=sum(ch) if ch else None,
          numerator=sum(ch) if ch else None, denominator=len(ch) or None,
          sample=len(ch), min_sample=MIN_N,
          reason=None if ch else "NO_CAPITAL_HOURS_COLUMN_OR_ROW")]


def _outcome_of(row, streams):
    o = C.num(row.get("outcome"))
    if o in (0.0, 1.0):
        return o
    for st in streams.values():
        p = st.pos_by_subject.get(row.get("decision_id"))
        if p and p["payoff"] in (0.0, 1.0):
            return p["payoff"]
        orc = st.oracle.get(row.get("decision_id")) or {}
        if orc.get("payoff") in (0.0, 1.0):
            return orc["payoff"]
    return None


def _ll(p, o):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(o * math.log(p) + (1 - o) * math.log(1 - p))


def scout(rows, why, streams, results) -> list:
    b = "COUNTERFACTUAL"
    if rows is None:
        return [M("SCOUT", m, book=b, basis="pos_iface_scout_feature_"
                  "effects", reason=why) for m in SCOUT_METRICS]
    basis = "pos_iface_scout_feature_effects"
    feats = sorted({r["feature_id"] for r in rows})
    val = sorted({r["feature_id"] for r in rows
                  if str(r.get("status")).upper() == "VALIDATED"})
    scored = []
    for r in rows:
        o = _outcome_of(r, streams)
        pw, po = C.num(r.get("p_with")), C.num(r.get("p_without"))
        if o is None or pw is None or po is None:
            continue
        scored.append((r, (pw - o) ** 2 - (po - o) ** 2,
                       _ll(pw, o) - _ll(po, o)))
    out = [M("SCOUT", "features_proposed", book=b, basis=basis,
             unit="features", value=len(feats), numerator=len(feats),
             denominator=None, sample=len(rows)),
           M("SCOUT", "features_validated", book=b, basis=basis,
             unit="features", value=len(val), numerator=len(val),
             denominator=len(feats) or None, sample=len(feats)),
           _mean_metric("SCOUT", "incremental_brier",
                        [s[1] for s in scored], book=b, basis=basis +
                        " (negative = the feature improves calibration)",
                        unit="brier", why_empty="NO_ROW_WITH_A_KNOWN_OUTCOME"),
           _mean_metric("SCOUT", "incremental_log_loss",
                        [s[2] for s in scored], book=b, basis=basis,
                        unit="log loss",
                        why_empty="NO_ROW_WITH_A_KNOWN_OUTCOME")]
    inc = (results.get("SCOUT_FEATURE_INCLUDED") or {}).get("PAPER")
    exc = (results.get("SCOUT_FEATURE_EXCLUDED") or {}).get("PAPER")
    ti = ((inc or {}).get("world") or {}).get("total_pnl_usd")
    te = ((exc or {}).get("world") or {}).get("total_pnl_usd")
    out.append(M("SCOUT", "incremental_realized_edge", book=b,
                 basis="twin SCOUT_FEATURE_INCLUDED minus EXCLUDED, PAPER "
                 "basis (difference of two counterfactual worlds)",
                 unit="usd", value=None if None in (ti, te) else ti - te,
                 numerator=ti, denominator=te,
                 sample=((inc or {}).get("world") or {}).get("scored"),
                 reason="TWIN_SCOUT_WORLDS_UNSCORED"))
    fd, decay = [], []
    for f in val:
        mine = sorted([s for s in scored if s[0]["feature_id"] == f],
                      key=lambda s: s[0]["at"])
        if len(mine) >= 4:
            h = len(mine) // 2
            a = sum(s[1] for s in mine[:h]) / h
            z = sum(s[1] for s in mine[h:]) / (len(mine) - h)
            decay.append(z - a)
            fd.append(z >= 0)
    out.append(_rate_metric("SCOUT", "false_discoveries", sum(fd), len(fd),
                            book=b, basis="validated features whose later-"
                            "half incremental Brier is not an improvement",
                            why_empty="NO_VALIDATED_FEATURE_WITH_4_SCORED_"
                            "ROWS", min_sample=10))
    out.append(_mean_metric("SCOUT", "feature_decay", decay, book=b,
                            basis="later-half minus earlier-half incremental "
                            "Brier per validated feature (positive = decay)",
                            unit="brier", why_empty="NO_VALIDATED_FEATURE_"
                            "WITH_4_SCORED_ROWS", min_sample=10))
    return out


# ═════════════════════════════════════════════════════════════════════
# KAREN, ALLOCATOR, AUDREY
# ═════════════════════════════════════════════════════════════════════

def karen(kc: dict, results: dict, traces: dict) -> list:
    out = []
    if kc is None:
        why = "MIGRATION_207_NOT_APPLIED"
        return [M("KAREN", m, book="PAPER", basis="karen_challenges",
                  reason=why) for m in ("valid_defect_precision",
                                        "false_block_rate", "recall")]
    out.append(_rate_metric(
        "KAREN", "valid_defect_precision", kc["upheld"],
        kc["upheld"] + kc["rejected"], book="PAPER",
        basis="resolved challenges UPHELD / (UPHELD + REJECTED), resolved by "
        "a third party", why_empty="NO_RESOLVED_CHALLENGE"))
    out.append(_rate_metric(
        "KAREN", "false_block_rate", kc["false_blocks"], kc["blocks_assessed"],
        book="PAPER", basis="blocking challenges later assessed false by "
        "another agent", why_empty="NO_ASSESSED_BLOCKING_CHALLENGE"))
    out.append(M("KAREN", "recall", book="PAPER", basis="defects",
                 reason="NO_INDEPENDENT_DEFECT_CENSUS_TO_COUNT_MISSED_DEFECTS"))
    for basis in C.BOOKS:
        rows = [t for t in traces.get(("KAREN_BLOCK_ACCEPTED", basis), [])
                if t["world_action"].startswith("BLOCK_ACCEPTED")]
        pairs = [(t["pnl_usd"], t["baseline_pnl_usd"]) for t in rows
                 if t["pnl_usd"] is not None
                 and t["baseline_pnl_usd"] is not None]
        avoided = [max(0.0, a - b) for a, b in pairs]
        sacr = [max(0.0, b - a) for a, b in pairs]
        sfx = "_%s_basis" % basis.lower()
        for name, xs in (("loss_avoided" + sfx, avoided),
                         ("profit_sacrificed" + sfx, sacr)):
            out.append(M("KAREN", name, book="COUNTERFACTUAL",
                         basis="twin KAREN_BLOCK_ACCEPTED vs the recorded "
                         "(ignored) world, per blocked position",
                         unit="usd", value=sum(xs) if xs else None,
                         numerator=sum(xs) if xs else None,
                         denominator=len(xs) or None, sample=len(xs),
                         reason=None if xs else
                         "NO_BLOCKED_POSITION_SCORED_IN_BOTH_WORLDS",
                         min_sample=MIN_N))
    return out


def allocator(results: dict, traces: dict) -> list:
    out = []
    for basis in C.BOOKS:
        sfx = "_%s_basis" % basis.lower()
        b = "COUNTERFACTUAL"
        res = {k: (results.get(k) or {}).get(basis) for k in (
            "ALLOCATOR_INTEL_SHADOW", "ALLOCATOR_EQUAL_WEIGHT",
            "ALLOCATOR_INDEPENDENT_SIZING")}
        intel = (res["ALLOCATOR_INTEL_SHADOW"] or {}).get("world") or {}
        if not intel.get("scored"):
            why = ("NO_POSITION_WITH_A_SHADOW_ALLOCATION_RECORDED_BEFORE_"
                   "ITS_DECISION" if res["ALLOCATOR_INTEL_SHADOW"] else
                   "TWIN_ALLOCATOR_WORLD_NOT_RUN")
            for m in ("pnl_vs_equal_weight", "pnl_vs_independent_sizing",
                      "max_drawdown", "utilization",
                      "concentration_reduction", "profit_per_capital_hour"):
                out.append(M("ALLOCATOR", m + sfx, book=b,
                             basis="twin ALLOCATOR worlds", reason=why))
            continue
        it = {t["subject_id"]: t for t in traces.get(
            ("ALLOCATOR_INTEL_SHADOW", basis), [])}
        for key, name in (("ALLOCATOR_EQUAL_WEIGHT", "pnl_vs_equal_weight"),
                          ("ALLOCATOR_INDEPENDENT_SIZING",
                           "pnl_vs_independent_sizing")):
            other = {t["subject_id"]: t for t in traces.get((key, basis), [])}
            d = [it[s]["pnl_usd"] - other[s]["pnl_usd"] for s in sorted(it)
                 if s in other and it[s]["pnl_usd"] is not None
                 and other[s]["pnl_usd"] is not None]
            out.append(_mean_metric("ALLOCATOR", name + sfx, d, book=b,
                                    basis="paired per position: shadow "
                                    "allocator world minus %s world" % key,
                                    unit="usd/position",
                                    why_empty="NO_POSITION_SCORED_IN_BOTH"))
        eq = (res["ALLOCATOR_EQUAL_WEIGHT"] or {}).get("world") or {}
        n = intel.get("scored")
        out.append(M("ALLOCATOR", "max_drawdown" + sfx, book=b,
                     basis="shadow allocator world, cumulative P&L by close",
                     unit="usd", value=intel.get("max_drawdown_usd"),
                     sample=n, reason="NO_SCORED_POSITION"))
        out.append(M("ALLOCATOR", "utilization" + sfx, book=b,
                     basis="capital-hours / (sleeve x window hours)",
                     unit="share", value=intel.get("utilization"),
                     numerator=intel.get("capital_hours"), sample=n,
                     reason=(intel.get("unmeasured") or {}).get(
                         "utilization", "NOT_MEASURED")))
        a, c = intel.get("max_fixture_capital_share"), eq.get(
            "max_fixture_capital_share")
        out.append(M("ALLOCATOR", "concentration_reduction" + sfx, book=b,
                     basis="equal-weight max single-fixture capital share "
                     "minus the shadow allocator's (positive = less "
                     "correlated exposure)", unit="share",
                     value=None if None in (a, c) else c - a, numerator=c,
                     denominator=a, sample=n,
                     reason="NO_CAPITAL_DEPLOYED_IN_ONE_OF_THE_WORLDS"))
        out.append(M("ALLOCATOR", "profit_per_capital_hour" + sfx, book=b,
                     basis="shadow allocator world P&L / capital-hours",
                     unit="usd per usd-hour",
                     value=intel.get("pnl_per_capital_hour"),
                     numerator=intel.get("total_pnl_usd"),
                     denominator=intel.get("capital_hours"), sample=n,
                     reason=(intel.get("unmeasured") or {}).get(
                         "pnl_per_capital_hour", "NOT_MEASURED")))
    return out


def audrey(ad: dict) -> list:
    out = []
    for book in C.BOOKS:
        rc = (ad.get("risk_checks") or {}).get(book)
        if rc is None:
            out.append(M("AUDREY", "risk_disagreements_detected", book=book,
                         basis="intel_audrey_risk_checks",
                         reason="MIGRATION_208_NOT_APPLIED_OR_NO_CHECK"))
            out.append(M("AUDREY", "reconciliation_accuracy", book=book,
                         basis="intel_audrey_risk_checks",
                         reason="MIGRATION_208_NOT_APPLIED_OR_NO_CHECK"))
        else:
            n, agree = rc["n"], rc["agree"]
            out.append(M("AUDREY", "risk_disagreements_detected", book=book,
                         basis="independent recompute disagreeing with the "
                         "primary risk figure", unit="checks",
                         value=n - agree if n else None,
                         numerator=n - agree if n else None,
                         denominator=n or None, sample=n,
                         ci=C.wilson(n - agree, n) if n else None,
                         reason=None if n else "NO_RISK_CHECK_IN_WINDOW"))
            out.append(_rate_metric(
                "AUDREY", "reconciliation_accuracy", agree, n, book=book,
                basis="independent recompute agreeing within tolerance",
                why_empty="NO_RISK_CHECK_IN_WINDOW"))
        pm = (ad.get("postmortems") or {}).get(book)
        out.append(_rate_metric(
            "AUDREY", "accounting_accuracy", (pm or {}).get("exact", 0),
            (pm or {}).get("n", 0), book=book,
            basis="postmortems whose components explain realized P&L to the "
            "cent (|unexplained| <= $0.01)",
            why_empty="NO_POSTMORTEM_IN_WINDOW"))
    fa = ad.get("false_alarms")
    out.append(_rate_metric(
        "AUDREY", "false_alarm_rate", (fa or {}).get("upheld", 0),
        (fa or {}).get("resolved", 0), book="PAPER",
        basis="Audrey records against which a Karen challenge was UPHELD by "
        "a third party", why_empty="NO_RESOLVED_CHALLENGE_AGAINST_AUDREY"))
    out.append(M("AUDREY", "errors_detected_before_financial_impact",
                 book="PAPER", basis="findings",
                 reason="NO_RECORDED_LINK_FROM_A_FINDING_TO_A_FINANCIAL_"
                 "IMPACT"))
    return out


# ═════════════════════════════════════════════════════════════════════
# THE READ
# ═════════════════════════════════════════════════════════════════════

async def load(conn, *, now: float, since: float) -> dict:
    out = {"value_add": [], "karen": None, "audrey": {}}
    if await R.regclass(conn, "xavier_value_add"):
        out["value_add"] = [
            {"position_kind": r["position_kind"],
             "incremental": C.jload(r["incremental"])}
            for r in await conn.fetch(
                "SELECT DISTINCT ON (thesis_id) position_kind, incremental "
                "  FROM xavier_value_add WHERE status = 'FINAL' "
                "   AND computed_at >= to_timestamp($1) "
                " ORDER BY thesis_id, computed_at DESC LIMIT %d"
                % R.MAX_ROWS, float(since))]
    if await R.regclass(conn, "karen_challenges"):
        r = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE outcome = 'UPHELD') AS upheld, "
            "       count(*) FILTER (WHERE outcome = 'REJECTED') AS rejected,"
            "       count(*) FILTER (WHERE blocked AND false_block) AS fb, "
            "       count(*) FILTER (WHERE blocked AND false_block IS NOT "
            "                        NULL) AS fba, "
            "       count(*) FILTER (WHERE target_agent = 'AUDREY' AND "
            "                        outcome = 'UPHELD') AS a_up, "
            "       count(*) FILTER (WHERE target_agent = 'AUDREY' AND "
            "                        outcome IN ('UPHELD','REJECTED')) AS a_res"
            "  FROM karen_challenges WHERE challenged_at >= to_timestamp($1)",
            float(since))
        out["karen"] = {"upheld": r["upheld"], "rejected": r["rejected"],
                        "false_blocks": r["fb"], "blocks_assessed": r["fba"]}
        out["audrey"]["false_alarms"] = {"upheld": r["a_up"],
                                         "resolved": r["a_res"]}
    if await R.regclass(conn, "intel_audrey_risk_checks"):
        out["audrey"]["risk_checks"] = {
            r["book"]: {"n": r["n"], "agree": r["agree"]}
            for r in await conn.fetch(
                "SELECT book, count(*) AS n, count(*) FILTER (WHERE agrees) "
                "       AS agree FROM intel_audrey_risk_checks "
                " WHERE computed_at >= to_timestamp($1) GROUP BY book",
                float(since))}
    if await R.regclass(conn, "position_postmortems"):
        out["audrey"]["postmortems"] = {
            r["book"]: {"n": r["n"], "exact": r["exact"]}
            for r in await conn.fetch(
                "SELECT book, count(*) AS n, count(*) FILTER (WHERE "
                "       abs(unexplained_usd) <= 0.01) AS exact "
                "  FROM position_postmortems "
                " WHERE computed_at >= to_timestamp($1) GROUP BY book",
                float(since))}
    return out


def compute(*, streams: dict, results: dict, traces: dict, db: dict,
            archer_rows, archer_why, scout_rows, scout_why) -> list:
    rows = (derek(streams) + xavier(streams, db["value_add"])
            + archer(archer_rows, archer_why)
            + scout(scout_rows, scout_why, streams, results)
            + karen(db["karen"], results, traces)
            + allocator(results, traces) + audrey(db["audrey"]))
    return rows
