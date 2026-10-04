"""LAB-F POLICY / MARKET DRIFT SENTINEL: THE ENGINE, PURE (no database).

  * the reference / comparison windows follow the stated rule exactly, from
    the recorded versions and their first decisions (a parameter change is a
    new regime); too young / inactive is UNAVAILABLE with the reason;
  * n is distinct UNITS, never decision rows; below MIN_UNITS -> UNAVAILABLE;
  * a known shift is MATERIAL_DRIFT, a moderate one WATCH, none NORMAL, after
    Benjamini-Hochberg across every test of the run;
  * LIVE_SHADOW / ACTUAL with no rows are UNAVAILABLE with the measured
    counts, never a zero;
  * drift lowers the confidence modifier (information only), creates
    Audrey / Scout work items (NOT enqueued) and one tournament task, and
    nothing else;
  * ANTI-LOOKAHEAD: the one point-in-time accessor drops a deliberately
    future-dated row, and adding such rows changes nothing in the report;
  * the production export renders the endpoint's SELECTs unchanged, passes
    the research workflow's read-only guard, and round-trips through the
    parser.
"""
from __future__ import annotations

import json
import math
import random
import re

import pytest

from sportsassets.lab import drift_reads as R
from sportsassets.lab import drift_sentinel as DS

HOUR = 3600.0
DAY = 86400.0
CLOCK = 1_800_000_000.0
CG = "PINNACLE_COMPLETED_GAME_PAPER"
DEREK = "DEREK_ENTRY_POLICY_V2"
PV = "PINNACLE_COMPLETED_GAME_PAPER_V3"
PARAM = "paperparam:PINNACLE_COMPLETED_GAME_PAPER:V2"


def _ver(strategy=CG, pv=PV, param=PARAM, first=CLOCK - 10 * DAY,
         last=CLOCK - 60, n=1000):
    return {"strategy": strategy, "policy_version": pv,
            "param_version_id": param, "first_at": first, "last_at": last,
            "recorded_at": last + 1, "n": n}


def _latest(strategy=CG, pv=PV, param=PARAM, t=CLOCK - 60):
    return {"strategy": strategy, "policy_version": pv,
            "param_version_id": param, "t": t, "recorded_at": t + 1,
            "decision_id": "paperdec:last"}


def _dec(i, *, t, gross=None, refusal="BELOW_MIN_GROSS_EDGE", contract=None,
         league="MLB", provider="pinnapi.com/raw-websocket", strategy=CG,
         pv=PV, param=PARAM, verdict="REFUSE", fixture=None, net=None,
         qty=None, latency=1.0, age=5.0, book=0.5, market="MONEYLINE"):
    c = contract if contract is not None else "mkt-%d" % i
    return {"decision_id": "paperdec:%d" % i, "strategy": strategy,
            "policy_version": pv, "param_version_id": param, "t": t,
            "recorded_at": t + 0.2, "verdict": verdict,
            "refusal": None if verdict == "ENTER" else refusal,
            "us_market_slug": c, "holding_side": "LONG",
            "fixture": fixture or ("event:%s" % c), "competition": league,
            "market_type": market, "sport_family": "baseball",
            "gross_edge_pp": None if gross is None else str(gross),
            "net_ev_usd": net, "acq_qty": qty,
            "pin_at": str(t - age), "pin_received_at": str(t - age + latency),
            "pin_age_s": str(age), "pin_provider": provider,
            "book_age_s": str(book)}


def _data(decisions, versions=None, latest=None, **kw):
    d = {"clock": CLOCK, "since": CLOCK - 14 * DAY,
         "versions": versions or [_ver()], "latest": latest or [_latest()],
         "decisions": decisions, "orders": [], "eddie": [], "econ": [],
         "settlements": [], "sources": {},
         "shadow": {"canonical_intent_executions": None,
                    "execmirror_orders": {"EXCLUDED": 8}},
         "actual": {"execmirror_fills": 0, "bettor_funded_fills": 0,
                    "kalshi_live_fills": 0}}
    d.update(kw)
    return d


def _windows(versions=None, latest=None, clock=CLOCK):
    return DS.derive_windows(versions or [_ver()], latest or [_latest()],
                             clock=clock)


def _shifted(n_units=60, shift=2.0, rows_per_unit=3, seed=1, **kw):
    """n_units contracts in each window, rows_per_unit rows each; the
    comparison window's gross edge shifted by `shift`."""
    rng = random.Random(seed)
    w = _windows()[CG]
    out, i = [], 0
    for win, delta in (("ref", 0.0), ("cmp", shift)):
        a, b = w[win]
        for u in range(n_units):
            g = rng.gauss(delta, 1.0)
            for _r in range(rows_per_unit):
                t = a + (b - a) * rng.random() * 0.999
                out.append(_dec(i, t=t, gross=g, contract="%s-%d" % (win, u),
                                **kw))
                i += 1
    return out


def _finding(rep, metric, *, league=DS.ALL, evidence=DS.OBSERVED,
             strategy=CG):
    got = [f for f in rep["findings"] if f["metric"] == metric
           and f["league"] == league and f["evidence_class"] == evidence
           and f["strategy"] == strategy]
    assert len(got) == 1, (metric, league, evidence, len(got))
    return got[0]


# ═════════════════════════════════════════════════════════════════════
# THE WINDOWS
# ═════════════════════════════════════════════════════════════════════

def test_windows_of_a_mature_version_are_seven_days_and_seventy_two_hours():
    first = CLOCK - 30 * DAY
    w = _windows([_ver(first=first)])[CG]
    assert w["active"] and w["status"] is None
    assert w["ref"] == [first, first + 7 * DAY]
    assert w["cmp"] == [CLOCK - 72 * HOUR, CLOCK]
    assert w["version_key"] == "%s|%s" % (PV, PARAM)
    assert w["reference_basis"].startswith(
        "FIRST_DECISIONS_OF_THE_ACTIVE_VERSION")


def test_a_young_version_splits_its_life_into_two_disjoint_halves():
    first = CLOCK - 40 * HOUR
    w = _windows([_ver(first=first)])[CG]
    assert w["ref"] == [first, first + 20 * HOUR]
    assert w["cmp"] == [first + 20 * HOUR, CLOCK]
    assert w["status"] is None


def test_a_version_younger_than_twelve_hours_is_unavailable():
    w = _windows([_ver(first=CLOCK - 10 * HOUR)])[CG]
    assert w["status"] == DS.UNAVAILABLE
    assert w["why"].startswith("VERSION_LIFE_10.0H_TOO_SHORT")


def test_a_strategy_without_a_recent_decision_is_inactive():
    w = _windows([_ver(last=CLOCK - 5 * DAY)],
                 [_latest(t=CLOCK - 5 * DAY)])[CG]
    assert w["active"] is False and w["status"] == DS.UNAVAILABLE
    assert w["why"] == "STRATEGY_INACTIVE_NO_DECISION_IN_LAST_72H"


def test_a_parameter_change_is_a_new_regime_with_its_own_first_decision():
    old = _ver(param="paperparam:X:V1", first=CLOCK - 20 * DAY,
               last=CLOCK - 9 * DAY)
    new = _ver(param=PARAM, first=CLOCK - 4 * DAY)
    w = _windows([old, new])[CG]
    assert w["version_key"].endswith(PARAM)
    assert w["ref"][0] == CLOCK - 4 * DAY          # not the older regime's
    # and rows recorded under the old parameter never enter its windows
    rows = [_dec(1, t=CLOCK - 3.9 * DAY, gross=1.0, param="paperparam:X:V1")]
    assert DS.decision_observations(rows, {CG: w}) == {}


# ═════════════════════════════════════════════════════════════════════
# UNITS, MINIMUM SAMPLE, CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════

def test_n_is_distinct_units_never_rows():
    rep = DS.compute(_data(_shifted(n_units=25, rows_per_unit=8)))
    f = _finding(rep, "gross_edge_pp")
    assert (f["n_ref"], f["n_cmp"]) == (25, 25)
    assert (f["rows_ref"], f["rows_cmp"]) == (200, 200)


def test_below_the_minimum_sample_is_unavailable_with_the_counts():
    rep = DS.compute(_data(_shifted(n_units=19, rows_per_unit=10)))
    f = _finding(rep, "gross_edge_pp")
    assert f["status"] == DS.UNAVAILABLE and f["p"] is None
    assert f["why"] == "INSUFFICIENT_SAMPLE_N_REF_19_N_CMP_19_MIN_20_UNITS"
    assert f["statistic"] is None


def test_a_large_shift_is_material_drift_and_no_shift_is_normal():
    rep = DS.compute(_data(_shifted(shift=2.0)))
    f = _finding(rep, "gross_edge_pp")
    assert f["status"] == DS.MATERIAL
    assert f["q"] < 0.05 and f["statistic"]["psi"] >= 0.25
    assert f["statistic"]["test"] == "KS_2SAMP"
    for s in f["statistic"]["quantile_shifts"]:
        assert s["lo"] <= s["shift"] <= s["hi"]
    assert f["statistic"]["ref_summary"]["n"] == 60
    rep0 = DS.compute(_data(_shifted(shift=0.0, seed=3)))
    assert _finding(rep0, "gross_edge_pp")["status"] == DS.NORMAL


def test_a_moderate_mix_shift_is_watch():
    # 400 contracts a window; refusal mix 50/50 -> 70/30: PSI 0.169, chi2
    # significant
    w = _windows()[CG]
    rows, i = [], 0
    for win, share_a in (("ref", 0.5), ("cmp", 0.7)):
        a, b = w[win]
        for u in range(400):
            code = "CODE_A" if u < share_a * 400 else "CODE_B"
            rows.append(_dec(i, t=a + (b - a) * (u + 0.5) / 400.0,
                             refusal=code, contract="%s-%d" % (win, u)))
            i += 1
    rep = DS.compute(_data(rows))
    f = _finding(rep, "refusal_mix")
    # (0.7 - 0.5) ln(0.7 / 0.5) + (0.3 - 0.5) ln(0.3 / 0.5)
    assert f["statistic"]["psi"] == pytest.approx(
        0.2 * math.log(1.4) + 0.2 * math.log(5 / 3), abs=1e-9)
    assert f["statistic"]["tvd"] == pytest.approx(0.2)
    assert f["status"] == DS.WATCH
    assert rep["strategies"][0]["status"] == DS.WATCH
    assert rep["strategies"][0]["confidence_modifier"]["value"] == 0.75
    assert rep["work_items"] == [] and rep["research_tasks"] == []


def test_benjamini_hochberg_runs_over_every_test_of_the_run():
    rep = DS.compute(_data(_shifted()))
    tested = [f for f in rep["findings"] if f["status"] != DS.UNAVAILABLE]
    assert rep["multiple_testing"]["tests"] == len(tested) > 5
    assert rep["multiple_testing"]["method"] == "BENJAMINI_HOCHBERG_FDR"
    from sportsassets.lab import drift_stats as S
    assert [f["q"] for f in tested] == S.bh_adjust([f["p"] for f in tested])
    for f in tested:
        assert f["p"] <= f["q"] <= 1.0


def test_segments_by_league_and_the_rollup():
    rows = _shifted(league="MLB") + [
        dict(r, competition="NFL", us_market_slug="nfl-" + r[
            "us_market_slug"]) for r in _shifted(seed=9)]
    rep = DS.compute(_data(rows))
    leagues = {f["league"] for f in rep["findings"]
               if f["metric"] == "gross_edge_pp"}
    assert leagues == {DS.ALL, "MLB", "NFL"}
    assert _finding(rep, "gross_edge_pp")["n_ref"] == 120
    assert _finding(rep, "gross_edge_pp", league="NFL")["n_ref"] == 60
    # league_mix exists only as a roll-up
    assert {f["league"] for f in rep["findings"]
            if f["metric"] == "league_mix"} == {DS.ALL}


def test_an_unattributed_league_names_its_sport_family():
    assert DS.league_of({"competition": None, "sport_family": "soccer"}) == \
        "UNATTRIBUTED:soccer"
    assert DS.league_of({}) == "UNATTRIBUTED:UNKNOWN"
    assert DS.league_of({"competition": "BRB"}) == "BRB"


# ═════════════════════════════════════════════════════════════════════
# EVIDENCE CLASSES AND UNAVAILABLE
# ═════════════════════════════════════════════════════════════════════

def test_live_shadow_and_actual_without_rows_are_unavailable_with_counts():
    rep = DS.compute(_data(_shifted()))
    ls = _finding(rep, "fill_fraction", evidence=DS.LIVE_SHADOW)
    assert ls["status"] == DS.UNAVAILABLE
    assert "NEVER_SUBMITTED" in ls["why"]
    assert "MIGRATION_225_NOT_APPLIED" in ls["why"]
    assert '{"EXCLUDED": 8}' in ls["why"]
    act = _finding(rep, "slippage_pp", evidence=DS.ACTUAL)
    assert act["status"] == DS.UNAVAILABLE
    assert '"execmirror_fills": 0' in act["why"]
    for f in rep["findings"]:
        if f["status"] == DS.UNAVAILABLE:
            assert f["why"] and f["p"] is None and f["statistic"] is None


def test_paper_simulation_fill_metrics_come_from_resolved_orders_only():
    w = _windows()[CG]
    orders = []
    for win in ("ref", "cmp"):
        a, b = w[win]
        for u in range(30):
            orders.append({
                "order_id": "o-%s-%d" % (win, u), "strategy": CG,
                "policy_version": PV, "param_version_id": PARAM,
                "t": a + (b - a) * (u + 0.5) / 30.0, "recorded_at": a + 1,
                "decision_recorded_at": a + 1,
                "terminal_at": a + (b - a) * (u + 0.5) / 30.0 + 60,
                "qty": 10.0, "filled_qty": 10.0 if u % 2 else 0.0,
                "competition": "MLB", "sport_family": "baseball"})
    orders.append(dict(orders[-1], order_id="o-open", terminal_at=None))
    rep = DS.compute(_data([], orders=orders))
    fp = _finding(rep, "fill_probability", evidence=DS.PAPER_SIM)
    assert (fp["n_ref"], fp["n_cmp"]) == (30, 30)
    assert fp["statistic"]["ref_shares"] == {"FILLED": 0.5,
                                             "NOT_FILLED": 0.5}
    assert fp["status"] == DS.NORMAL
    assert fp["unresolved_orders_excluded"] == 1
    ff = _finding(rep, "fill_fraction", evidence=DS.PAPER_SIM)
    assert ff["statistic"]["cmp_summary"]["p50"] == 0.5


def test_a_read_range_that_misses_the_reference_window_is_unavailable():
    rep = DS.compute(_data(_shifted(), since=CLOCK - 2 * DAY))
    f = _finding(rep, "fill_probability", evidence=DS.PAPER_SIM)
    assert f["status"] == DS.UNAVAILABLE
    assert f["why"].startswith("READ_RANGE_STARTS_")


def test_a_truncated_read_is_unavailable_never_a_partial_answer():
    rep = DS.compute(_data(_shifted(), sources={
        "decisions": {"relation": "paper_decisions", "present": True,
                      "truncated": True}}))
    f = _finding(rep, "gross_edge_pp")
    assert f["status"] == DS.UNAVAILABLE
    assert f["why"] == "READ_TRUNCATED_AT_LIMIT_paper_decisions"


# ═════════════════════════════════════════════════════════════════════
# WHAT DRIFT DOES -- AND DOES NOT DO
# ═════════════════════════════════════════════════════════════════════

def test_material_drift_lowers_confidence_and_creates_work_not_policy():
    rep = DS.compute(_data(_shifted()))
    s = rep["strategies"][0]
    assert s["status"] == DS.MATERIAL
    cm = s["confidence_modifier"]
    assert cm["value"] == 0.5 and cm["use"].startswith("INFORMATION_ONLY")
    assert {w["agent_id"] for w in rep["work_items"]} == {"AUDREY", "SCOUT"}
    for w in rep["work_items"]:
        assert w["production_effect"] == "NONE"
        assert w["integration"]["status"] == \
            "NOT_ENQUEUED_RETURNED_FOR_LATER_INTEGRATION"
        assert w["reason"] == "MATERIAL_DISTRIBUTION_DRIFT"
        assert re.fullmatch(r"labdrift:[0-9a-f]{24}", w["request_id"])
        assert w["kind"] in ("POLICY_REVALIDATION", "RESEARCH_QUESTION")
    (t,) = rep["research_tasks"]
    assert t["kind"] == "TOURNAMENT_REVALIDATION"
    assert t["status"] == "PROPOSED" and t["authority"] == DS.AUTHORITY
    assert "never FORWARD_VALIDATED" in t["design"]["evaluation"]
    assert rep["question_h"]["answer"] == "YES_MATERIAL_DRIFT_PRESENT"
    assert rep["question_h"]["material"] == [CG]
    # the same episode produces the same ids (dedupe when recorded)
    rep2 = DS.compute(_data(_shifted()))
    assert [w["request_id"] for w in rep2["work_items"]] == \
        [w["request_id"] for w in rep["work_items"]]
    # no key anywhere in the report is an order, size, threshold, venue or
    # policy field
    keys = set()

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                keys.add(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(rep)
    assert not keys & {"limit_price", "target_qty", "qty", "wire_price",
                       "venue_order_id", "min_gross_edge_pp", "threshold",
                       "allowlist", "order_intent", "size_usd"}


def test_normal_on_thin_coverage_gives_no_modifier():
    # only gross_edge_pp has rows: 1 of the decision metrics measured...
    rows = _shifted(shift=0.0, seed=4)
    for r in rows:
        r["pin_at"] = r["pin_received_at"] = r["pin_age_s"] = None
        r["book_age_s"] = None
    rep = DS.compute(_data(rows))
    s = rep["strategies"][0]
    if s["status"] == DS.NORMAL:
        assert s["rollup_decision_metric_coverage"] < 1.0
    rep_thin = DS.compute(_data([]))
    s = rep_thin["strategies"][0]
    assert s["status"] == DS.UNAVAILABLE
    assert s["confidence_modifier"]["value"] is None
    assert s["confidence_modifier"]["why"] == "DRIFT_NOT_MEASURABLE"
    assert rep_thin["question_h"]["answer"] == \
        "NOT_MEASURABLE_EVERY_ACTIVE_STRATEGY_UNAVAILABLE"


def test_the_coverage_rule_withholds_a_modifier_from_a_thin_normal():
    p = dict(DS.DEFAULTS)
    fs = [{"strategy": CG, "league": DS.ALL, "evidence_class": DS.OBSERVED,
           "status": DS.NORMAL, "metric": "gross_edge_pp"}] + [
        {"strategy": CG, "league": DS.ALL, "evidence_class": DS.OBSERVED,
         "status": DS.UNAVAILABLE, "metric": "m%d" % i} for i in range(5)]
    (s,) = DS._rollup(_windows(), fs, p)
    assert s["status"] == DS.NORMAL
    assert s["confidence_modifier"]["value"] is None
    assert s["confidence_modifier"]["why"].startswith(
        "NORMAL_ON_THIN_COVERAGE_17%")


# ═════════════════════════════════════════════════════════════════════
# ANTI-LOOKAHEAD: THE ONE POINT-IN-TIME ACCESSOR
# ═════════════════════════════════════════════════════════════════════

def test_the_accessor_drops_a_future_dated_row():
    rows = [{"recorded_at": CLOCK - 1, "t": CLOCK - 2},
            {"recorded_at": CLOCK + 1, "t": CLOCK - 2},       # recorded later
            {"recorded_at": CLOCK - 1, "t": CLOCK + 5},       # decided later
            {"recorded_at": None, "t": CLOCK - 2},            # unknown stamp
            {"recorded_at": CLOCK - 1, "t": CLOCK - 2,
             "decision_optional_recorded_at": CLOCK + 9}]     # joined later
    kept = R.point_in_time(rows, CLOCK, source="decisions")
    assert kept == [rows[0]]
    orders = [{"recorded_at": CLOCK - 5, "decision_recorded_at": CLOCK + 1,
               "t": CLOCK - 9}]
    assert R.point_in_time(orders, CLOCK, source="orders") == []


def test_future_rows_change_nothing_in_the_report():
    base = _shifted()
    clean = R.assemble({"versions": [_ver()], "latest": [_latest()],
                        "decisions": base}, clock=CLOCK,
                       since=CLOCK - 14 * DAY, present={"decisions"})
    # a NEW policy version decided before the clock but RECORDED after it:
    # if it leaked, it would become the active version and reset the windows
    leak_v = _ver(pv="PINNACLE_COMPLETED_GAME_PAPER_V9", first=CLOCK - 2 * DAY,
                  last=CLOCK - 30)
    leak_v["recorded_at"] = CLOCK + 3600
    leak_l = _latest(pv="PINNACLE_COMPLETED_GAME_PAPER_V9", t=CLOCK - 30)
    leak_l["recorded_at"] = CLOCK + 3600
    future = [dict(r, recorded_at=CLOCK + 10, gross_edge_pp="50") for r in
              base[:40]] + [dict(r, t=CLOCK + 100, recorded_at=CLOCK + 101,
                                 gross_edge_pp="-50") for r in base[40:80]]
    dirty = R.assemble({"versions": [_ver(), leak_v],
                        "latest": [_latest(), leak_l],
                        "decisions": base + future}, clock=CLOCK,
                       since=CLOCK - 14 * DAY, present={"decisions"})
    assert dirty["sources"]["decisions"]["dropped_by_point_in_time"] == 80
    a, b = DS.compute(clean), DS.compute(dirty)
    assert a["windows"] == b["windows"]
    assert a["findings"] == b["findings"]
    assert a["question_h"] == b["question_h"]


# ═════════════════════════════════════════════════════════════════════
# THE PRODUCTION EXPORT
# ═════════════════════════════════════════════════════════════════════

GUARD = re.compile(r"\b(insert|update|delete|drop|alter|truncate|grant|"
                   r"revoke|create|copy|vacuum|reindex|refresh|call|do|merge|"
                   r"lock|set\s+role)\b", re.I)


def test_the_export_is_the_endpoints_selects_and_passes_the_read_only_guard():
    sql = R.export_sql(clock=CLOCK, since=CLOCK - 14 * DAY,
                       ref_max_s=DS.DEFAULTS["ref_max_s"],
                       cmp_max_s=DS.DEFAULTS["cmp_max_s"],
                       present=set(R.RELATIONS) - {"canonical_executions"})
    stripped = "\n".join(re.sub(r"--.*$", "", ln) for ln in sql.splitlines()
                         if not ln.lstrip().startswith("\\echo"))
    assert not GUARD.search(stripped)
    for ln in sql.splitlines():
        if ln.lstrip().startswith("\\"):
            assert ln.lstrip().startswith("\\echo")
    for src, q, args in R.query_plan(
            clock=CLOCK, since=CLOCK - 14 * DAY,
            ref_max_s=DS.DEFAULTS["ref_max_s"],
            cmp_max_s=DS.DEFAULTS["cmp_max_s"],
            present=set(R.RELATIONS) - {"canonical_executions"}):
        assert R.render(q, args) in sql, src
    assert "canonical_intent_executions WHERE" not in sql.replace("\n", " ")
    split = R.export_sql(clock=CLOCK, since=CLOCK - DAY, ref_max_s=1.0,
                         cmp_max_s=1.0, present={"decisions"},
                         sources=["decisions"], between=(10.0, 20.0))
    assert "WHERE q.t >= 10.0 AND q.t < 20.0" in split
    assert "LABDRIFT|versions" not in split


def test_render_substitutes_every_placeholder_highest_first():
    assert R.render("a $1 b $10 c $2", tuple(range(1, 11))) == "a 1 b 10 c 2"
    with pytest.raises(ValueError):
        R.render("x $3", (1, 2))
    with pytest.raises(ValueError):
        R.render("x $1", (float("nan"),))


def test_the_export_parser_round_trips_positional_rows():
    log = "\n".join([
        "2026-10-04T20:30:29Z  LABDRIFT|meta|" + json.dumps(
            {"clock": CLOCK, "since": CLOCK - DAY, "ref_max_s": 1.0,
             "cmp_max_s": 2.0}),
        "noise line",
        " LABDRIFT|relations|{\"decisions\": true, \"eddie\": false}",
        " LABDRIFT|decisions#cols|[\"t\", \"recorded_at\", \"gross_edge_pp\"]",
        " LABDRIFT|decisions|[1.5, 2.5, \"0.25\"]",
        " LABDRIFT|decisions|[3.5, 4.5, null]",
    ])
    got = R.parse_export(log)
    assert got["meta"]["clock"] == CLOCK
    assert got["relations"] == {"decisions": True, "eddie": False}
    assert got["decisions"] == [
        {"t": 1.5, "recorded_at": 2.5, "gross_edge_pp": "0.25"},
        {"t": 3.5, "recorded_at": 4.5, "gross_edge_pp": None}]
    with pytest.raises(ValueError):
        R.parse_export(" LABDRIFT|orders|[1, 2]")


def test_report_from_log_refuses_a_mismatched_export():
    from sportsassets.lab import drift_runner as RUN
    log = " LABDRIFT|meta|" + json.dumps(
        {"clock": CLOCK, "since": CLOCK - DAY, "ref_max_s": 1.0,
         "cmp_max_s": 2.0})
    with pytest.raises(ValueError):
        RUN.report_from_log(log)
    with pytest.raises(ValueError):
        RUN.report_from_log("no export here")
