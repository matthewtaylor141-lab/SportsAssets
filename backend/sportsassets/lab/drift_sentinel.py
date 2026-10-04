"""LAB-F POLICY / MARKET DRIFT SENTINEL (SHADOW; pure, no I/O).

THE QUESTION (owner PM question H). A policy validated in one market regime
should lose confidence when the operating environment changes: are any
ACTIVE strategies operating under material distribution drift relative to
the period on which their active policy version first ran?

THE REFERENCE WINDOW, EXACTLY. No policy version at this base carries a
recorded validation window (migration 186's parameter-version record holds
a parameter set, its source and -- for an EVALUATED_PROPOSAL only -- an
evaluation id; the production rows are SHIPPED_DEFAULT and OWNER_DECISION
with evaluation_id NULL, research-sql run 37231355643; live_parity_cutover
records a production cutover, not a policy validation; this module reads
none of them). So the reference is the period on which the active version
FIRST RAN, derived from the recorded decisions themselves:

  version key   (paper_decisions.strategy, .policy_version, and the
                parameter version the decision records in
                policy_decision->parameters->>version_id) -- a parameter
                change (e.g. the completed-game threshold) is a new regime
  active        the version key of the strategy's most recent decision at
                the clock (ties by decision_id), provided that decision is
                inside the comparison horizon (CMP_MAX_S); otherwise the
                strategy is INACTIVE and not tested
  F             the first decided_at of the active version key
  life          clock - F
  reference     [F, F + min(REF_MAX_S, life / 2))
  comparison    [max(clock - CMP_MAX_S, reference end), clock]
  too young     either window shorter than MIN_WINDOW_S -> UNAVAILABLE

Windows never overlap, and every row is read through the reader's one
point-in-time accessor (record stamp <= clock) before it gets here.

THE UNIT OF OBSERVATION. Decisions re-evaluate the same contract many
times (one row per valuation change), so rows are NOT independent. Each
decision-level metric is reduced to ONE observation per unit per window
before any test -- numeric: the unit's median; categorical: the unit's rows
split its unit weight equally across their categories -- so n is the number
of distinct units (contracts, orders, positions, fixtures), never the number
of rows. Rows are reported beside n.

THE METHODS (sportsassets.lab.drift_stats; every one unit-tested on known
values):
  numeric       two-sample Kolmogorov-Smirnov (exact lattice-path p-value up
                to 250,000 cells, else asymptotic with Stephens' correction);
                PSI on the reference window's own quintiles (deciles from 100
                units); p10 / p50 / p90 shifts with seeded percentile-
                bootstrap 95% intervals; p25 / p50 / p75 of each window
  categorical   chi-square test of homogeneity (cells pooled to expected >=
                5); PSI over the categories; total-variation distance
  minimum       MIN_UNITS (20) units in EACH window, else UNAVAILABLE: the
                exact KS test is valid at any n but below ~20 it can detect
                only near-total separation, and quintile PSI bins would hold
                fewer than 4 reference units
  multiplicity  Benjamini-Hochberg (FDR 5%) across EVERY metric x segment
                test of one run (league rows and roll-up rows together)
  status        MATERIAL_DRIFT  q < ALPHA and PSI >= PSI_MATERIAL (0.25)
                WATCH           q < ALPHA and PSI_WATCH (0.10) <= PSI < 0.25
                NORMAL          otherwise (a significant but negligible shift,
                                or a large shift the test cannot distinguish
                                from noise -- the PSI null expectation is
                                reported beside every PSI)
                UNAVAILABLE     no recorded source, version too young, or a
                                window below MIN_UNITS -- with the reason

THE EVIDENCE CLASSES. OBSERVED_AT_DECISION: real feed / book observations
the PAPER decision path recorded (edges are computed from observed book
levels and the simulator's fee schedule; nothing is filled). PAPER_SIMULATION:
simulated fills, Eddie's outcomes of simulated fills, paper capital release
and paper settlements. LIVE_SHADOW: SMALL LIVE SHADOW records -- never
submitted, so they carry no fill, slippage or markout by construction.
ACTUAL: real venue fills. A class with no recorded rows is UNAVAILABLE with
the measured counts, never a zero.

DRIFT CHANGES NO POLICY. It (1) exposes a confidence modifier the confidence
ladder may READ AS INFORMATION ONLY -- never a gate, size, threshold or
allowlist; (2) returns revalidation work items for Audrey and Scout shaped as
agent_work_requests rows (NOT enqueued: at base 96fd349 the queue's CHECKs
admit only Xavier's four fresh-evidence kinds); (3) returns a tournament /
research task record. Nothing here is read by a decision path.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json

from . import drift_stats as S

VERSION = "LAB_DRIFT_SENTINEL_V1"
AUTHORITY = "SHADOW_RESEARCH_ONLY"
HOUR = 3600.0
DAY = 86400.0

NORMAL, WATCH, MATERIAL, UNAVAILABLE = (
    "NORMAL", "WATCH", "MATERIAL_DRIFT", "UNAVAILABLE")
STATUSES = (NORMAL, WATCH, MATERIAL, UNAVAILABLE)
RANK = {NORMAL: 0, WATCH: 1, MATERIAL: 2}

OBSERVED = "OBSERVED_AT_DECISION"
PAPER_SIM = "PAPER_SIMULATION"
LIVE_SHADOW = "LIVE_SHADOW"
ACTUAL = "ACTUAL"
EVIDENCE_CLASSES = (OBSERVED, PAPER_SIM, LIVE_SHADOW, ACTUAL)

NUMERIC, CATEGORICAL = "NUMERIC", "CATEGORICAL"
ALL = "ALL"
REF, CMP = "REFERENCE", "COMPARISON"

DEFAULTS = {
    "ref_max_s": 7 * DAY,
    "cmp_max_s": 72 * HOUR,
    "min_window_s": 6 * HOUR,
    "min_units": 20,
    "alpha": 0.05,
    "psi_watch": 0.10,
    "psi_material": 0.25,
    "quantiles": (0.1, 0.5, 0.9),
    "boot_b": S.BOOT_B,
    "min_coverage_for_normal": 0.5,
}

#: the confidence ladder may read this (information only)
CONFIDENCE_MODIFIER = {NORMAL: 1.0, WATCH: 0.75, MATERIAL: 0.5,
                       UNAVAILABLE: None}

_FILL_EVIDENCE = (PAPER_SIM, LIVE_SHADOW, ACTUAL)
METRICS = {
    "gross_edge_pp": {
        "kind": NUMERIC, "evidence": (OBSERVED,), "unit": "CONTRACT",
        "by_league": True,
        "source": ("paper_decisions.policy_decision->>'gross_edge_pp' (the "
                   "best observed level's p_pinnacle - price, pp; recorded "
                   "for every decision that reached the book)")},
    "executable_edge_pp": {
        "kind": NUMERIC, "evidence": (OBSERVED,), "unit": "CONTRACT",
        "by_league": True,
        "source": ("100 x paper_decisions.economics->'acquisition'->>"
                   "'expected_net_profit_usd' / ->>'qty': net edge per "
                   "contract after the simulator's fees at the VWAP walked "
                   "over OBSERVED book levels; recorded only when a candidate "
                   "cleared the gross threshold (population conditional on "
                   "that)")},
    "source_latency_s": {
        "kind": NUMERIC, "evidence": (OBSERVED,), "unit": "CONTRACT",
        "by_league": True,
        "source": ("paper_decisions.pinnacle->>'received_at' - ->>'at': "
                   "the reference probability's source stamp to our receipt "
                   "(for PinnAPI the stamp is the source CHANGE time, so an "
                   "unchanged price adds its time since the last change)")},
    "probability_age_s": {
        "kind": NUMERIC, "evidence": (OBSERVED,), "unit": "CONTRACT",
        "by_league": True,
        "source": "paper_decisions.pinnacle->>'age_s' (age at the decision)"},
    "book_age_s": {
        "kind": NUMERIC, "evidence": (OBSERVED,), "unit": "CONTRACT",
        "by_league": True,
        "source": "paper_decisions.book->>'age_at_decision_s' (book freshness)"},
    "refusal_mix": {
        "kind": CATEGORICAL, "evidence": (OBSERVED,), "unit": "CONTRACT",
        "by_league": True,
        "source": "paper_decisions.refusal (first refusal; '(ENTER)' if none)"},
    "probability_source_mix": {
        "kind": CATEGORICAL, "evidence": (OBSERVED,), "unit": "CONTRACT",
        "by_league": True,
        "source": "paper_decisions.pinnacle->>'provider'"},
    "market_type_mix": {
        "kind": CATEGORICAL, "evidence": (OBSERVED,), "unit": "CONTRACT",
        "by_league": True,
        "source": "paper_decisions.label->>'market_type' (event mix)"},
    "league_mix": {
        "kind": CATEGORICAL, "evidence": (OBSERVED,), "unit": "CONTRACT",
        "by_league": False,
        "source": ("paper_decisions.label->>'competition', else "
                   "UNATTRIBUTED:<external_valuations.sport_family>")},
    "entry_league_concentration": {
        "kind": CATEGORICAL, "evidence": (OBSERVED,), "unit": "CONTRACT",
        "by_league": False,
        "source": ("the league mix of ENTER decisions only "
                   "(paper_decisions.verdict = 'ENTER'): where entries "
                   "concentrate")},
    "event_cluster_size": {
        "kind": NUMERIC, "evidence": (OBSERVED,), "unit": "FIXTURE",
        "by_league": True,
        "source": ("per fixture (paper_decisions.fixture), the number of "
                   "distinct contracts ENTERED in the window: same-event "
                   "(correlated) exposure")},
    "fill_probability": {
        "kind": CATEGORICAL, "evidence": _FILL_EVIDENCE, "unit": "ORDER",
        "by_league": True,
        "source": ("paper_orders role ENTRY resolved by the clock "
                   "(terminal_at): FILLED when append-only paper_fills sum "
                   "> 0")},
    "fill_fraction": {
        "kind": NUMERIC, "evidence": _FILL_EVIDENCE, "unit": "ORDER",
        "by_league": True,
        "source": ("sum(paper_fills.qty recorded <= clock) / "
                   "paper_orders.qty, ENTRY orders resolved by the clock")},
    "slippage_pp": {
        "kind": NUMERIC, "evidence": _FILL_EVIDENCE, "unit": "DECISION",
        "by_league": True,
        "source": "eddie_execution_outcomes.realized_slippage_pp"},
    "markout_pp": {
        "kind": NUMERIC, "evidence": _FILL_EVIDENCE, "unit": "DECISION",
        "by_league": True,
        "source": ("eddie_execution_outcomes.realized_adverse_selection_pp "
                   "(adverse selection / markout)")},
    "capital_release_h": {
        "kind": NUMERIC, "evidence": (PAPER_SIM, LIVE_SHADOW, ACTUAL),
        "unit": "POSITION", "by_league": True,
        "source": ("pos_position_economics.time_committed_h of CLOSED "
                   "positions (newest revision computed <= clock), windowed "
                   "by released_at")},
    "settlement_exception": {
        "kind": CATEGORICAL, "evidence": (PAPER_SIM,), "unit": "POSITION",
        "by_league": True,
        "source": ("paper_settlements.outcome (newest version recorded <= "
                   "clock): ORDINARY (WON / LOST) vs VOID_REFUND / "
                   "SETTLED_AT_VENUE_PRICE")},
}
DECISION_METRICS = tuple(m for m, d in METRICS.items()
                         if d["evidence"] == (OBSERVED,))


# ═════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════

def _num(v):
    xs = S._finite([v])
    return xs[0] if xs else None


def iso(t) -> str | None:
    if t is None:
        return None
    return _dt.datetime.fromtimestamp(float(t), _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def version_key(policy_version, param_version_id) -> str:
    pv = str(policy_version) if policy_version is not None else "UNKNOWN"
    return pv if not param_version_id else "%s|%s" % (pv, param_version_id)


def league_of(row: dict) -> str:
    comp = row.get("competition")
    if comp:
        return str(comp)
    return "UNATTRIBUTED:%s" % (row.get("sport_family") or "UNKNOWN")


def _sid(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()
                          ).hexdigest()[:24]


def _params(params: dict | None) -> dict:
    p = dict(DEFAULTS)
    p.update(params or {})
    return p


# ═════════════════════════════════════════════════════════════════════
# THE WINDOWS
# ═════════════════════════════════════════════════════════════════════

def derive_windows(versions: list, latest: list, *, clock: float,
                   params: dict | None = None) -> dict:
    """{strategy: window record}. `versions`: [{strategy, policy_version,
    param_version_id, first_at, last_at, n}]; `latest`: [{strategy,
    policy_version, param_version_id, t, decision_id}] (each strategy's
    most recent decision)."""
    p = _params(params)
    c = float(clock)
    first = {}
    for v in versions:
        key = (v["strategy"], version_key(v["policy_version"],
                                          v.get("param_version_id")))
        f = _num(v.get("first_at"))
        if f is not None:
            first[key] = (min(first[key][0], f), v) if key in first \
                else (f, v)
    out = {}
    for lt in sorted(latest, key=lambda r: str(r["strategy"])):
        s = lt["strategy"]
        vk = version_key(lt["policy_version"], lt.get("param_version_id"))
        rec = {"strategy": s, "policy_version": lt["policy_version"],
               "param_version_id": lt.get("param_version_id"),
               "version_key": vk, "latest_decision_at": _num(lt.get("t")),
               "latest_decision_id": lt.get("decision_id"),
               "reference_basis": (
                   "FIRST_DECISIONS_OF_THE_ACTIVE_VERSION: no validation "
                   "window is recorded for any policy version at this base"),
               "status": None, "why": None, "ref": None, "cmp": None}
        f = first.get((s, vk))
        last = _num(lt.get("t"))
        if f is None or last is None:
            rec.update(status=UNAVAILABLE, active=False,
                       why="ACTIVE_VERSION_FIRST_DECISION_NOT_RECORDED")
            out[s] = rec
            continue
        f0 = f[0]
        life = c - f0
        rec.update(first_decision_at=f0, life_h=round(life / HOUR, 3),
                   decisions_in_version=int(f[1].get("n") or 0))
        if last < c - p["cmp_max_s"]:
            rec.update(status=UNAVAILABLE, active=False,
                       why="STRATEGY_INACTIVE_NO_DECISION_IN_LAST_%dH"
                       % round(p["cmp_max_s"] / HOUR))
            out[s] = rec
            continue
        rec["active"] = True
        ref_span = min(p["ref_max_s"], life / 2.0)
        ref = (f0, f0 + ref_span)
        cmp_ = (max(c - p["cmp_max_s"], ref[1]), c)
        rec["ref"], rec["cmp"] = list(ref), list(cmp_)
        rec["ref_iso"] = [iso(ref[0]), iso(ref[1])]
        rec["cmp_iso"] = [iso(cmp_[0]), iso(cmp_[1])]
        if (ref[1] - ref[0] < p["min_window_s"]
                or cmp_[1] - cmp_[0] < p["min_window_s"]):
            rec.update(status=UNAVAILABLE,
                       why="VERSION_LIFE_%.1fH_TOO_SHORT_FOR_TWO_DISJOINT_"
                       "%dH_WINDOWS" % (life / HOUR,
                                        round(p["min_window_s"] / HOUR)))
        out[s] = rec
    return out


def window_of(t, w: dict):
    if t is None or not w.get("ref") or not w.get("cmp"):
        return None
    t = float(t)
    if w["ref"][0] <= t < w["ref"][1]:
        return REF
    if w["cmp"][0] <= t <= w["cmp"][1]:
        return CMP
    return None


# ═════════════════════════════════════════════════════════════════════
# THE OBSERVATIONS
# ═════════════════════════════════════════════════════════════════════

def _obs(metric, evidence, row_league, unit, value, win):
    return {"metric": metric, "evidence": evidence, "league": row_league,
            "unit": unit, "value": value, "window": win}


def _active_window(row: dict, windows: dict, *, strategy_key="strategy"):
    s = row.get(strategy_key)
    w = windows.get(s)
    if not w or not w.get("active") or w.get("status") == UNAVAILABLE:
        return None, None
    vk = version_key(row.get("policy_version"), row.get("param_version_id"))
    if vk != w["version_key"]:
        return None, None
    return s, window_of(row.get("t"), w)


def decision_observations(rows: list, windows: dict) -> dict:
    """{strategy: [obs]} from paper_decisions rows."""
    out: dict = {}
    clusters: dict = {}
    for r in rows:
        s, win = _active_window(r, windows)
        if win is None:
            continue
        lg = league_of(r)
        unit = "%s:%s" % (r.get("us_market_slug"), r.get("holding_side"))
        o = out.setdefault(s, [])
        g = _num(r.get("gross_edge_pp"))
        if g is not None:
            o.append(_obs("gross_edge_pp", OBSERVED, lg, unit, g, win))
        net, qty = _num(r.get("net_ev_usd")), _num(r.get("acq_qty"))
        if net is not None and qty is not None and qty > 0:
            o.append(_obs("executable_edge_pp", OBSERVED, lg, unit,
                          100.0 * net / qty, win))
        at, rec = _num(r.get("pin_at")), _num(r.get("pin_received_at"))
        if at is not None and rec is not None:
            o.append(_obs("source_latency_s", OBSERVED, lg, unit, rec - at,
                          win))
        age = _num(r.get("pin_age_s"))
        if age is not None:
            o.append(_obs("probability_age_s", OBSERVED, lg, unit, age, win))
        ba = _num(r.get("book_age_s"))
        if ba is not None:
            o.append(_obs("book_age_s", OBSERVED, lg, unit, ba, win))
        enter = r.get("verdict") == "ENTER"
        ref = r.get("refusal") or ("(ENTER)" if enter
                                   else "(NO_REFUSAL_CODE)")
        o.append(_obs("refusal_mix", OBSERVED, lg, unit, str(ref), win))
        o.append(_obs("probability_source_mix", OBSERVED, lg, unit,
                      str(r.get("pin_provider") or "NONE_RECORDED"), win))
        o.append(_obs("market_type_mix", OBSERVED, lg, unit,
                      str(r.get("market_type") or "NOT_RECORDED"), win))
        o.append(_obs("league_mix", OBSERVED, ALL, unit, lg, win))
        if enter:
            o.append(_obs("entry_league_concentration", OBSERVED, ALL, unit,
                          lg, win))
            fx = r.get("fixture") or ("unit:" + unit)
            clusters.setdefault((s, win, fx), {"league": lg, "units": set()})
            clusters[(s, win, fx)]["units"].add(unit)
    for (s, win, fx), c in clusters.items():
        out.setdefault(s, []).append(_obs(
            "event_cluster_size", OBSERVED, c["league"], fx,
            float(len(c["units"])), win))
    return out


def order_observations(rows: list, windows: dict, *, clock: float) -> tuple:
    """({strategy: [obs]}, {strategy: unresolved count})."""
    out: dict = {}
    unresolved: dict = {}
    for r in rows:
        s, win = _active_window(r, windows)
        if win is None:
            continue
        term = _num(r.get("terminal_at"))
        if term is None or term > float(clock):
            unresolved[s] = unresolved.get(s, 0) + 1
            continue
        qty, filled = _num(r.get("qty")), _num(r.get("filled_qty")) or 0.0
        lg = league_of(r)
        unit = str(r.get("order_id"))
        o = out.setdefault(s, [])
        o.append(_obs("fill_probability", PAPER_SIM, lg, unit,
                      "FILLED" if filled > 0 else "NOT_FILLED", win))
        if qty is not None and qty > 0:
            o.append(_obs("fill_fraction", PAPER_SIM, lg, unit,
                          min(1.0, filled / qty), win))
    return out, unresolved


def eddie_observations(rows: list, windows: dict) -> tuple:
    out: dict = {}
    unmeasured: dict = {}
    for r in rows:
        s, win = _active_window(r, windows)
        if win is None:
            continue
        ev = ACTUAL if r.get("source") == "ACTUAL" else PAPER_SIM
        lg = league_of(r)
        unit = str(r.get("decision_id"))
        o = out.setdefault(s, [])
        sl = _num(r.get("realized_slippage_pp"))
        if sl is not None:
            o.append(_obs("slippage_pp", ev, lg, unit, sl, win))
        mk = _num(r.get("realized_adverse_selection_pp"))
        if mk is not None:
            o.append(_obs("markout_pp", ev, lg, unit, mk, win))
        else:
            k = (s, ev)
            unmeasured[k] = unmeasured.get(k, 0) + 1
    return out, unmeasured


def _group_attribution(r: dict) -> dict:
    """A position's strategy / version through its sleeve classification and
    decision (both LEFT JOINs): None when it cannot be attributed."""
    if r.get("policy_version") is None:
        return {}
    return {"strategy": r.get("decision_strategy") or r.get("strategy"),
            "policy_version": r.get("policy_version"),
            "param_version_id": r.get("param_version_id"),
            "t": r.get("t")}


def position_observations(econ: list, settlements: list,
                          windows: dict) -> tuple:
    out: dict = {}
    unattributed = {"econ": 0, "settlements": 0}
    for r in econ:
        a = _group_attribution(r)
        if not a:
            unattributed["econ"] += 1
            continue
        s, win = _active_window(a, windows)
        if win is None:
            continue
        h = _num(r.get("time_committed_h"))
        if h is None:
            continue
        ev = ACTUAL if r.get("book") == "ACTUAL" else PAPER_SIM
        out.setdefault(s, []).append(_obs(
            "capital_release_h", ev, league_of(r), str(r.get("position_key")),
            h, win))
    for r in settlements:
        a = _group_attribution(r)
        if not a:
            unattributed["settlements"] += 1
            continue
        s, win = _active_window(a, windows)
        if win is None:
            continue
        oc = str(r.get("outcome"))
        cat = "ORDINARY" if oc in ("WON", "LOST") else oc
        out.setdefault(s, []).append(_obs(
            "settlement_exception", PAPER_SIM, league_of(r),
            str(r.get("position_key")), cat, win))
    return out, unattributed


# ═════════════════════════════════════════════════════════════════════
# ONE TEST
# ═════════════════════════════════════════════════════════════════════

def reduce_numeric(obs: list) -> list:
    by: dict = {}
    for o in obs:
        by.setdefault(o["unit"], []).append(o["value"])
    return [S.median(v) for _u, v in sorted(by.items())]


def reduce_categorical(obs: list) -> dict:
    by: dict = {}
    for o in obs:
        by.setdefault(o["unit"], []).append(o["value"])
    counts: dict = {}
    for _u, cats in by.items():
        w = 1.0 / len(cats)
        for c in cats:
            counts[c] = counts.get(c, 0.0) + w
    return counts


def _summary(xs: list) -> dict:
    s = sorted(xs)
    return {"n": len(s), "p25": S.quantile(s, 0.25),
            "p50": S.quantile(s, 0.5), "p75": S.quantile(s, 0.75)}


def measure_segment(kind: str, ref_obs: list, cmp_obs: list, *, params: dict,
                 seed_parts: tuple) -> dict:
    """The statistic of one (strategy, version, league, metric, evidence)
    segment, before multiplicity control. status None means tested."""
    p = params
    if kind == NUMERIC:
        r, c = reduce_numeric(ref_obs), reduce_numeric(cmp_obs)
        n_ref, n_cmp = len(r), len(c)
    else:
        rc, cc = reduce_categorical(ref_obs), reduce_categorical(cmp_obs)
        n_ref = len({o["unit"] for o in ref_obs})
        n_cmp = len({o["unit"] for o in cmp_obs})
    base = {"n_ref": n_ref, "n_cmp": n_cmp, "rows_ref": len(ref_obs),
            "rows_cmp": len(cmp_obs)}
    if n_ref < p["min_units"] or n_cmp < p["min_units"]:
        return dict(base, status=UNAVAILABLE, p=None, statistic=None,
                    why="INSUFFICIENT_SAMPLE_N_REF_%d_N_CMP_%d_MIN_%d_UNITS"
                    % (n_ref, n_cmp, p["min_units"]))
    if kind == NUMERIC:
        ks = S.ks_2samp(r, c)
        psi = S.psi_numeric(r, c)
        shifts = []
        for q in p["quantiles"]:
            shifts.append(S.bootstrap_quantile_shift(
                r, c, q, seed=S.seed_for(*seed_parts, q), b=p["boot_b"]))
        stat = {"test": "KS_2SAMP", "ks_d": ks["d"], "ks_method": ks["method"],
                "psi": psi["psi"], "psi_bins": psi["bins"],
                "psi_null_expectation": psi["psi_null_expectation"],
                "psi_edges": psi["edges"], "quantile_shifts": shifts,
                "ref_summary": _summary(r), "cmp_summary": _summary(c)}
        pval = ks["p"]
    else:
        chi = S.chi_square_homogeneity(rc, cc)
        psi = S.psi_categorical(rc, cc)
        nr, nc = sum(rc.values()), sum(cc.values())
        cats = sorted(set(rc) | set(cc), key=str)
        stat = {"test": "CHI_SQUARE_HOMOGENEITY", "chi2": chi["chi2"],
                "df": chi["df"], "chi_note": chi["note"],
                "cells_after_pooling": [list(x) for x in chi["cells"]],
                "psi": psi["psi"], "psi_bins": psi["bins"],
                "psi_null_expectation": psi["psi_null_expectation"],
                "tvd": S.tvd(rc, cc),
                "ref_shares": {k: rc.get(k, 0.0) / nr for k in cats},
                "cmp_shares": {k: cc.get(k, 0.0) / nc for k in cats}}
        pval = chi["p"]
    return dict(base, status=None, p=pval, statistic=stat, why=None)


def classify(q: float, psi: float, params: dict) -> tuple:
    p = params
    if q < p["alpha"] and psi >= p["psi_material"]:
        return MATERIAL, ("Q_%.4g_BELOW_%.2f_AND_PSI_%.3f_AT_LEAST_%.2f"
                          % (q, p["alpha"], psi, p["psi_material"]))
    if q < p["alpha"] and psi >= p["psi_watch"]:
        return WATCH, ("Q_%.4g_BELOW_%.2f_AND_PSI_%.3f_IN_[%.2f,%.2f)"
                       % (q, p["alpha"], psi, p["psi_watch"],
                          p["psi_material"]))
    if q < p["alpha"]:
        return NORMAL, ("SIGNIFICANT_BUT_NEGLIGIBLE_PSI_%.3f_BELOW_%.2f"
                        % (psi, p["psi_watch"]))
    return NORMAL, ("NOT_SIGNIFICANT_AFTER_BH_Q_%.4g_PSI_%.3f" % (q, psi))


# ═════════════════════════════════════════════════════════════════════
# THE RUN
# ═════════════════════════════════════════════════════════════════════

def _unavailable_evidence_reason(metric: str, ev: str, data: dict) -> str:
    if ev == LIVE_SHADOW:
        sh = data.get("shadow") or {}
        canon = sh.get("canonical_intent_executions")
        em = sh.get("execmirror_orders")
        part = ("canonical_intent_executions SHADOW records in range: %s"
                % (json.dumps(canon, sort_keys=True) if canon is not None
                   else "TABLE_ABSENT_MIGRATION_225_NOT_APPLIED"))
        part2 = ("execmirror_orders in range by state: %s"
                 % (json.dumps(em, sort_keys=True) if em is not None
                    else "TABLE_ABSENT"))
        return ("LIVE_SHADOW_RECORDS_ARE_NEVER_SUBMITTED_SO_CARRY_NO_%s "
                "(SMALL LIVE is SHADOW); %s; %s"
                % (metric.upper(), part, part2))
    if ev == ACTUAL:
        act = data.get("actual") or {}
        return ("NO_ACTUAL_ROWS_ATTRIBUTABLE_TO_THIS_STRATEGY_VERSION; ACTUAL "
                "fill rows in range: %s" % json.dumps(act, sort_keys=True))
    return "NO_ROWS_RECORDED_FOR_THIS_METRIC_IN_EITHER_WINDOW"


_RANGED = ("fill_probability", "fill_fraction", "slippage_pp", "markout_pp",
           "capital_release_h", "settlement_exception")


def _source_reason(metric: str, data: dict, w: dict | None = None
                   ) -> str | None:
    src = data.get("sources") or {}
    since = _num(data.get("since"))
    if (metric in _RANGED and w and w.get("ref") and since is not None
            and since > w["ref"][0]):
        return ("READ_RANGE_STARTS_%s_AFTER_THE_REFERENCE_WINDOW_START_%s"
                % (iso(since), iso(w["ref"][0])))
    need = {"gross_edge_pp": ("decisions",), "fill_probability": ("orders",
                                                                  "fills"),
            "fill_fraction": ("orders", "fills"),
            "slippage_pp": ("eddie",), "markout_pp": ("eddie",),
            "capital_release_h": ("econ", "sleeves"),
            "settlement_exception": ("settlements", "sleeves")}.get(
        metric, ("decisions",))
    for n in need:
        s = src.get(n) or {}
        if s.get("present") is False:
            return "SOURCE_UNAVAILABLE_%s_%s" % (s.get("relation"), s.get("why"))
        if s.get("truncated"):
            return "READ_TRUNCATED_AT_LIMIT_%s" % s.get("relation", n)
    return None


def compute(data: dict, *, params: dict | None = None) -> dict:
    """THE SENTINEL. `data` is drift_reads.assemble()'s output (or the same
    shape built by a test). Returns the report; changes nothing."""
    p = _params(params)
    clock = float(data["clock"])
    windows = derive_windows(data.get("versions") or [],
                             data.get("latest") or [], clock=clock, params=p)
    dec = decision_observations(data.get("decisions") or [], windows)
    ords, unresolved = order_observations(data.get("orders") or [], windows,
                                          clock=clock)
    edd, adverse_unmeasured = eddie_observations(data.get("eddie") or [],
                                                 windows)
    pos, unattributed = position_observations(data.get("econ") or [],
                                              data.get("settlements") or [],
                                              windows)
    findings = []
    for s in sorted(windows):
        w = windows[s]
        obs = (dec.get(s, []) + ords.get(s, []) + edd.get(s, [])
               + pos.get(s, []))
        for metric, md in METRICS.items():
            for ev in md["evidence"]:
                mobs = [o for o in obs if o["metric"] == metric
                        and o["evidence"] == ev]
                leagues = [ALL]
                if md["by_league"]:
                    leagues += sorted({o["league"] for o in mobs} - {ALL})
                for lg in leagues:
                    seg = mobs if lg == ALL else [o for o in mobs
                                                  if o["league"] == lg]
                    f = {"strategy": s, "policy_version": w["policy_version"],
                         "param_version_id": w["param_version_id"],
                         "version_key": w["version_key"], "league": lg,
                         "metric": metric, "kind": md["kind"],
                         "evidence_class": ev, "source": md["source"],
                         "unit_basis": md["unit"],
                         "windows": {"reference": w.get("ref_iso"),
                                     "comparison": w.get("cmp_iso")},
                         "q": None}
                    why = None
                    if w.get("status") == UNAVAILABLE:
                        why = w["why"]
                    else:
                        why = _source_reason(metric, data, w)
                    if why is None and not seg:
                        why = _unavailable_evidence_reason(metric, ev, data)
                        if metric == "markout_pp" and ev in (PAPER_SIM,
                                                             ACTUAL):
                            n_un = adverse_unmeasured.get((s, ev), 0)
                            if n_un:
                                why += ("; %d outcomes record "
                                        "realized_adverse_selection as "
                                        "UNMEASURED" % n_un)
                    if why is not None:
                        f.update(status=UNAVAILABLE, why=why, p=None,
                                 statistic=None, n_ref=0, n_cmp=0,
                                 rows_ref=0, rows_cmp=0)
                        findings.append(f)
                        continue
                    r_obs = [o for o in seg if o["window"] == REF]
                    c_obs = [o for o in seg if o["window"] == CMP]
                    t = measure_segment(md["kind"], r_obs, c_obs, params=p,
                                     seed_parts=(s, w["version_key"], lg,
                                                 metric, ev))
                    f.update(t)
                    if metric in ("fill_probability", "fill_fraction") \
                            and unresolved.get(s):
                        f["unresolved_orders_excluded"] = unresolved[s]
                    findings.append(f)
    tested = [f for f in findings if f["status"] is None]
    qs = S.bh_adjust([f["p"] for f in tested])
    for f, q in zip(tested, qs):
        f["q"] = q
        f["status"], f["why"] = classify(q, f["statistic"]["psi"], p)
    strategies = _rollup(windows, findings, p)
    work, tasks = _actions(strategies, findings, clock=clock)
    return {
        "version": VERSION, "stats_version": S.VERSION,
        "authority": AUTHORITY, "production_effect": "NONE",
        "as_of": clock, "as_of_iso": iso(clock),
        "params": {k: (list(v) if isinstance(v, tuple) else v)
                   for k, v in p.items()},
        "methods": METHODS, "reference_rule": REFERENCE_RULE,
        "multiple_testing": {"method": "BENJAMINI_HOCHBERG_FDR",
                             "alpha": p["alpha"], "tests": len(tested),
                             "family": "every metric x segment test of this "
                                       "run (league and roll-up rows)"},
        "windows": windows, "strategies": strategies,
        "question_h": _question_h(strategies),
        "findings": findings, "work_items": work, "research_tasks": tasks,
        "context": {"unresolved_orders_excluded": unresolved,
                    "adverse_selection_unmeasured": {
                        "%s|%s" % k: v
                        for k, v in adverse_unmeasured.items()},
                    "positions_unattributed": unattributed,
                    "shadow": data.get("shadow"), "actual": data.get("actual"),
                    "sources": data.get("sources")},
        "disclosure": DISCLOSURE,
    }


def _rollup(windows: dict, findings: list, p: dict) -> list:
    out = []
    for s in sorted(windows):
        w = windows[s]
        fs = [f for f in findings if f["strategy"] == s]
        measured = [f for f in fs if f["status"] in RANK]
        roll = [f for f in fs if f["league"] == ALL]
        roll_obs = [f for f in roll if f["evidence_class"] == OBSERVED]
        cov = (len([f for f in roll_obs if f["status"] in RANK])
               / float(len(roll_obs)) if roll_obs else 0.0)
        status = (max((f["status"] for f in measured), key=RANK.get)
                  if measured else UNAVAILABLE)
        mod = CONFIDENCE_MODIFIER[status]
        mod_why = {NORMAL: "NO_DRIFT_ON_MEASURED_METRICS",
                   WATCH: "WATCH_DRIFT_ON_AT_LEAST_ONE_METRIC",
                   MATERIAL: "MATERIAL_DRIFT_ON_AT_LEAST_ONE_METRIC",
                   UNAVAILABLE: "DRIFT_NOT_MEASURABLE"}[status]
        if status == NORMAL and cov < p["min_coverage_for_normal"]:
            mod, mod_why = None, ("NORMAL_ON_THIN_COVERAGE_%.0f%%_OF_DECISION"
                                  "_METRICS_MEASURED_IS_NOT_EVIDENCE_OF_"
                                  "STABILITY" % (100 * cov))
        out.append({
            "strategy": s, "policy_version": w["policy_version"],
            "param_version_id": w["param_version_id"],
            "version_key": w["version_key"], "active": bool(w.get("active")),
            "window_status": w.get("status") or "OK",
            "window_why": w.get("why"),
            "status": status,
            "material_metrics": sorted({"%s/%s/%s" % (
                f["metric"], f["evidence_class"], f["league"])
                for f in fs if f["status"] == MATERIAL}),
            "watch_metrics": sorted({"%s/%s/%s" % (
                f["metric"], f["evidence_class"], f["league"])
                for f in fs if f["status"] == WATCH}),
            "rows_measured": len(measured), "rows_total": len(fs),
            "rollup_decision_metric_coverage": round(cov, 4),
            "confidence_modifier": {
                "value": mod, "why": mod_why, "status": status,
                "scale": {k: v for k, v in CONFIDENCE_MODIFIER.items()},
                "use": ("INFORMATION_ONLY for the confidence ladder: not a "
                        "gate, size, threshold, allowlist or policy input; "
                        "no decision path reads it")}})
    return out


def _question_h(strategies: list) -> dict:
    active = [s for s in strategies if s["active"]]
    material = [s["strategy"] for s in active if s["status"] == MATERIAL]
    watch = [s["strategy"] for s in active if s["status"] == WATCH]
    unav = [s["strategy"] for s in active if s["status"] == UNAVAILABLE]
    normal = [s["strategy"] for s in active if s["status"] == NORMAL]
    if material:
        ans = "YES_MATERIAL_DRIFT_PRESENT"
    elif not active:
        ans = "NOT_MEASURABLE_NO_ACTIVE_STRATEGY"
    elif len(unav) == len(active):
        ans = "NOT_MEASURABLE_EVERY_ACTIVE_STRATEGY_UNAVAILABLE"
    elif unav:
        ans = "NO_MATERIAL_DRIFT_DETECTED_ON_MEASURED_STRATEGIES_SOME_UNAVAILABLE"
    else:
        ans = "NO_MATERIAL_DRIFT_DETECTED"
    return {"question": ("Are any active strategies operating under material "
                         "distribution drift?"),
            "answer": ans, "material": material, "watch": watch,
            "normal": normal, "unavailable": unav,
            "inactive": [s["strategy"] for s in strategies
                         if not s["active"]],
            "per_strategy": [{k: s[k] for k in (
                "strategy", "version_key", "status", "window_status",
                "window_why", "material_metrics", "watch_metrics",
                "rows_measured", "rows_total")} for s in strategies]}


QUEUE_INTEGRATION = {
    "status": "NOT_ENQUEUED_RETURNED_FOR_LATER_INTEGRATION",
    "why": ("agent_work_requests at base 96fd349 (migration 226) admits only "
            "Xavier's fresh-evidence kinds PROBABILITY / VENUE_BOOK / "
            "GAME_STATE / MANAGEMENT_REASSESSMENT with reasons "
            "WAITING_FOR_FRESH_EVIDENCE / MANAGEMENT_UNAVAILABLE_STALE_INPUT "
            "on a PAPER / ACTUAL position; a policy revalidation item is "
            "refused by those CHECKs. The seven-agent queue is in flight "
            "(R30B agents, migration 234). At integration this record is "
            "enqueued through that queue's API with the subject kind / key "
            "and kind named here; nothing here writes the queue."),
}


def _actions(strategies: list, findings: list, *, clock: float) -> tuple:
    work, tasks = [], []
    for s in strategies:
        if s["status"] != MATERIAL or not s["active"]:
            continue
        mats = [f for f in findings if f["strategy"] == s["strategy"]
                and f["status"] == MATERIAL]
        metrics = sorted({f["metric"] for f in mats})
        ref = next((f["windows"]["reference"] for f in mats), None)
        episode = _sid(s["strategy"], s["version_key"], json.dumps(ref),
                       ",".join(metrics))
        evidence = ["%s/%s/%s q=%.4g psi=%.3f" % (
            f["metric"], f["evidence_class"], f["league"], f["q"],
            f["statistic"]["psi"]) for f in mats][:20]
        detail = {"strategy": s["strategy"], "version_key": s["version_key"],
                  "drifting_metrics": metrics, "reference_window": ref,
                  "comparison_window": next(
                      (f["windows"]["comparison"] for f in mats), None),
                  "episode": episode, "authority": AUTHORITY}
        for agent, kind, other, ask in (
                ("AUDREY", "POLICY_REVALIDATION", "SCOUT",
                 "Revalidate %s under the comparison-window distribution: "
                 "do the policy's admission evidence and economics still "
                 "hold?" % s["version_key"]),
                ("SCOUT", "RESEARCH_QUESTION", "AUDREY",
                 "Which market / feed change explains the drift of %s on %s?"
                 % (", ".join(metrics), s["version_key"]))):
            work.append({
                "request_id": "labdrift:%s" % _sid(episode, agent),
                "agent_id": agent, "kind": kind,
                "position_kind": "POLICY",
                "group_id": "%s|%s" % (s["strategy"], s["version_key"]),
                "reason": "MATERIAL_DISTRIBUTION_DRIFT",
                "enqueued_at": iso(clock), "due_at": iso(clock + DAY),
                "expires_at": iso(clock + 7 * DAY),
                "evidence_needed": evidence or metrics,
                "collaborator": other, "ask": ask, "detail": detail,
                "production_effect": "NONE",
                "integration": QUEUE_INTEGRATION})
        tasks.append({
            "task_id": "labdrifttask:%s" % episode,
            "kind": "TOURNAMENT_REVALIDATION",
            "strategy": s["strategy"], "version_key": s["version_key"],
            "trigger": "MATERIAL_DISTRIBUTION_DRIFT",
            "drifting_metrics": metrics,
            "hypothesis": ("the active policy's economics hold under the "
                           "comparison-window distribution"),
            "design": {
                "arms": ["ACTIVE_POLICY_AS_IS",
                         ("ACTIVE_POLICY_RE-EVALUATED_ON_THE_COMPARISON_"
                          "WINDOW_DISTRIBUTION (research arm, shadow only)")],
                "evaluation": ("FORWARD SHADOW from the task's creation; a "
                               "retrospective replay may inform it but is "
                               "never FORWARD_VALIDATED"),
                "primary_metric": ("INVESTMENT-sleeve forward realized net "
                                   "per capital-hour "
                                   "(profitability/validation.py)"),
                "promotion": ("NONE: any policy change is a separate owner "
                              "approval")},
            "status": "PROPOSED", "created_at": iso(clock),
            "authority": AUTHORITY, "production_effect": "NONE"})
    return work, tasks


METHODS = {
    "numeric": ("two-sample KS (exact lattice-path p-value up to 250,000 "
                "cells, else asymptotic Kolmogorov with Stephens' "
                "correction); PSI on reference quintiles (deciles from 100 "
                "units), empty bins floored at 1e-4; p10/p50/p90 shifts with "
                "seeded percentile-bootstrap 95% intervals (B=400, at most "
                "2,000 values per window)"),
    "categorical": ("chi-square test of homogeneity, cells pooled to expected "
                    ">= 5; PSI over categories; total-variation distance"),
    "unit_reduction": ("one observation per unit per window (numeric: the "
                       "unit's median; categorical: the unit's rows share its "
                       "unit weight); n = distinct units"),
    "minimum_sample": "20 units in each window, else UNAVAILABLE",
    "multiplicity": "Benjamini-Hochberg FDR 5% across every test of the run",
    "status": ("MATERIAL_DRIFT: q < 0.05 and PSI >= 0.25; WATCH: q < 0.05 and "
               "0.10 <= PSI < 0.25; NORMAL otherwise; UNAVAILABLE with a "
               "reason"),
}
REFERENCE_RULE = (
    "active version = the (policy_version, recorded parameter version) of "
    "the strategy's most recent decision at the clock; F = its first "
    "decided_at; reference = [F, F + min(7 d, (clock - F) / 2)); comparison "
    "= [max(clock - 72 h, reference end), clock]; either window < 6 h -> "
    "UNAVAILABLE. No validation window is recorded for any policy version "
    "at this base, so the reference is the version's first-run period.")
DISCLOSURE = (
    "SHADOW RESEARCH ONLY. Drift reduces displayed confidence and creates "
    "revalidation work; it never changes a policy, threshold, size, limit, "
    "allowlist or order. Retrospective drift on recorded data is not "
    "forward validation.")
