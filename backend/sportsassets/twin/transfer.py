"""CROSS-SPORT TRANSFER LEARNING (RESEARCH). Pure except load().

NEVER ASSUME TRANSFER. A transfer test is PREREGISTERED before any target
data it will be judged on exists: it names the SOURCE SPORT, the TARGET
SPORT, the HYPOTHESIS (the source sport's metric value, measured on records
at or before the declaration and frozen), and the predeclared criteria (the
frozen TRANSFER_CRITERIA spec). It is then judged on FORWARD TARGET-SPORT
records only -- records whose instant is strictly after the declaration
(the database refuses an evaluation whose window starts earlier) -- and
classified:

  TRANSFERABLE    forward_n >= min_forward_n and the 95% CI of
                  (target - source) lies inside [-tolerance, +tolerance]
                  (an equivalence test)
  SPORT_SPECIFIC  forward_n >= min_forward_n and that CI lies entirely
                  beyond the tolerance band
  UNKNOWN         otherwise -- including every test whose forward sample is
                  still short (the default, and what a new deployment shows)

THE DIMENSIONS and their observations (one per independent unit):
  FAVORITE_UNDERDOG_CALIBRATION  resolved predictions (one per event and
                                 contract): mean(o-p | p<0.5) -
                                 mean(o-p | p>=0.5)
  PROBABILITY_BANDS              resolved predictions: Brier score
  SPREAD_BEHAVIOUR               one book per market-hour of a market
                                 BETTOR evaluated: quoted spread
  LIQUIDITY                      the same books: log10(1 + top-5
                                 displayed depth USD)
  LIVE_EXECUTION                 ACTUAL (real venue) entries: fill VWAP -
                                 planned price per contract
  TIME_TO_EVENT                  resolved predictions with a known event
                                 start: mean(o-p | <=6h) - mean(o-p | >6h)
  ADVERSE_SELECTION              ACTUAL entries: mid 1-15 min after the
                                 fill (cost space) - fill VWAP

Simulated paper fills are not execution evidence, so LIVE_EXECUTION and
ADVERSE_SELECTION are measured on ACTUAL fills only.
"""
from __future__ import annotations

import math

from . import common as C
from . import reads as R

VERSION = "TWIN_TRANSFER_V1"
Z = 1.96
SOURCE_DAYS = 60.0

DIMENSIONS = {
    "FAVORITE_UNDERDOG_CALIBRATION": {
        "metric": "mean(o-p | p<0.5) - mean(o-p | p>=0.5)",
        "min_source_n": 50, "min_forward_n": 100, "tolerance": 0.05},
    "PROBABILITY_BANDS": {
        "metric": "Brier score of resolved predictions",
        "min_source_n": 50, "min_forward_n": 100, "tolerance": 0.02},
    "SPREAD_BEHAVIOUR": {
        "metric": "mean quoted spread, one book per market-hour",
        "min_source_n": 50, "min_forward_n": 100, "tolerance": 0.01},
    "LIQUIDITY": {
        "metric": "mean log10(1 + top-5 displayed depth USD) per market-hour",
        "min_source_n": 50, "min_forward_n": 100, "tolerance": 0.25},
    "LIVE_EXECUTION": {
        "metric": "mean ACTUAL entry slippage per contract (VWAP - planned)",
        "min_source_n": 30, "min_forward_n": 30, "tolerance": 0.01},
    "TIME_TO_EVENT": {
        "metric": "mean(o-p | <=6h to event) - mean(o-p | >6h)",
        "min_source_n": 50, "min_forward_n": 100, "tolerance": 0.05,
        "split_hours": 6.0},
    "ADVERSE_SELECTION": {
        "metric": "mean ACTUAL 1-15 min markout per contract",
        "min_source_n": 30, "min_forward_n": 30, "tolerance": 0.01},
}
RULE = ("classify on FORWARD target-sport records only (instant >= "
        "declared_at); diff = target - source (source frozen at "
        "declaration); 95% CI of diff combines both standard errors; "
        "TRANSFERABLE iff forward_n >= min_forward_n and the CI lies within "
        "[-tolerance, +tolerance]; SPORT_SPECIFIC iff forward_n >= "
        "min_forward_n and the CI lies entirely beyond the band; otherwise "
        "UNKNOWN; transfer is never assumed")
CRITERIA_SPEC = {"version": 1, "rule": RULE, "z": Z,
                 "dimensions": DIMENSIONS,
                 "source_choice": ("the sport with the most observations "
                                   "meeting min_source_n at declaration"),
                 "label": C.LABEL}


def criteria_of(dimension: str) -> dict:
    return dict(DIMENSIONS[dimension], rule=RULE, z=Z, dimension=dimension)


# ═════════════════════════════════════════════════════════════════════
# OBSERVATIONS (pure)
# ═════════════════════════════════════════════════════════════════════

def observations(*, preds: list, books: list, actual_positions: list,
                 premap: dict, markouts: dict) -> dict:
    """{dimension: [obs]} with obs = {sport, at, x...}."""
    out = {d: [] for d in DIMENSIONS}
    for r in preds:
        p, o = C.num(r.get("probability")), r.get("outcome")
        if p is None or o not in (0, 1):
            continue
        s = str(r.get("sport_family") or "unknown").strip().lower()
        base = {"sport": s, "at": float(r["at"]), "p": p, "o": int(o),
                "id": int(r["id"])}
        out["FAVORITE_UNDERDOG_CALIBRATION"].append(base)
        out["PROBABILITY_BANDS"].append(base)
        gs = C.num((premap.get(r.get("us_market_slug")) or {}).get(
            "game_start"))
        if gs is not None and gs >= base["at"]:
            out["TIME_TO_EVENT"].append(dict(base, hours=(gs - base["at"])
                                             / 3600.0))
    for b in books:
        if b.get("spread") is not None:
            out["SPREAD_BEHAVIOUR"].append({"sport": b["sport"],
                                            "at": b["at"], "x": b["spread"],
                                            "id": "%s@%s" % (b["slug"],
                                                             b["at"])})
        if b.get("depth_usd") is not None and b.get("spread") is not None:
            out["LIQUIDITY"].append({"sport": b["sport"], "at": b["at"],
                                     "x": math.log10(1.0 + b["depth_usd"]),
                                     "id": "%s@%s" % (b["slug"], b["at"])})
    for pos in actual_positions:
        if pos.get("entry_at") is None or pos.get("v") is None:
            continue
        if pos.get("slippage_pc") is not None:
            out["LIVE_EXECUTION"].append({"sport": pos["sport"],
                                          "at": pos["entry_at"],
                                          "x": pos["slippage_pc"],
                                          "id": pos["group_id"]})
        mid = markouts.get((pos["slug"], float(pos["entry_at"])))
        if mid is not None:
            mk = C.cost_space(mid, pos["side"]) - pos["v"]
            out["ADVERSE_SELECTION"].append({"sport": pos["sport"],
                                             "at": pos["entry_at"], "x": mk,
                                             "id": pos["group_id"]})
    for d in out:
        out[d].sort(key=lambda r: (r["at"], str(r["id"])))
    return out


def measure(dimension: str, obs: list) -> dict:
    """{n, value, low, high, why} of a dimension's metric on `obs`."""
    n = len(obs)
    if not n:
        return {"n": 0, "value": None, "low": None, "high": None,
                "why": "NO_OBSERVATION"}
    if dimension == "FAVORITE_UNDERDOG_CALIBRATION":
        dog = [r["o"] - r["p"] for r in obs if r["p"] < 0.5]
        fav = [r["o"] - r["p"] for r in obs if r["p"] >= 0.5]
        d = C.two_mean_diff(dog, fav, Z)
        return {"n": n, "value": d["diff"], "low": d["low"],
                "high": d["high"], "why": d["why"]}
    if dimension == "TIME_TO_EVENT":
        split = DIMENSIONS[dimension]["split_hours"]
        near = [r["o"] - r["p"] for r in obs if r["hours"] <= split]
        far = [r["o"] - r["p"] for r in obs if r["hours"] > split]
        d = C.two_mean_diff(near, far, Z)
        return {"n": n, "value": d["diff"], "low": d["low"],
                "high": d["high"], "why": d["why"]}
    xs = ([(r["p"] - r["o"]) ** 2 for r in obs]
          if dimension == "PROBABILITY_BANDS" else [r["x"] for r in obs])
    m = sum(xs) / len(xs)
    ci = C.mean_ci(xs, Z)
    return {"n": n, "value": C.rnd(m), "low": (ci or {}).get("low"),
            "high": (ci or {}).get("high"),
            "why": None if ci else "FEWER_THAN_2_OBSERVATIONS"}


def classify(test: dict, forward: dict) -> dict:
    """Apply the test's predeclared criteria to its forward measurement."""
    cr = test["criteria"]
    n, tol = forward["n"], float(cr["tolerance"])
    out = {"forward_n": n, "target_value": forward["value"],
           "target_ci_low": forward["low"], "target_ci_high": forward["high"],
           "diff": None, "diff_ci_low": None, "diff_ci_high": None}
    if forward["value"] is not None:
        out["diff"] = C.rnd(forward["value"] - test["source_value"])
    if n < int(cr["min_forward_n"]):
        out.update(classification="UNKNOWN", reason=(
            "FORWARD_SAMPLE_SHORT: %d of %d forward target observations"
            % (n, int(cr["min_forward_n"]))))
        return out
    if None in (forward["low"], forward["high"], test.get("source_ci_low"),
                test.get("source_ci_high")):
        out.update(classification="UNKNOWN",
                   reason="NO_CONFIDENCE_INTERVAL: %s" % (forward["why"]))
        return out
    se_t = (forward["high"] - forward["low"]) / (2 * Z)
    se_s = (test["source_ci_high"] - test["source_ci_low"]) / (2 * Z)
    half = Z * math.sqrt(se_t ** 2 + se_s ** 2)
    lo, hi = out["diff"] - half, out["diff"] + half
    out["diff_ci_low"], out["diff_ci_high"] = C.rnd(lo), C.rnd(hi)
    if -tol <= lo and hi <= tol:
        out.update(classification="TRANSFERABLE", reason=(
            "diff CI [%.4f, %.4f] within +/-%.4f" % (lo, hi, tol)))
    elif lo > tol or hi < -tol:
        out.update(classification="SPORT_SPECIFIC", reason=(
            "diff CI [%.4f, %.4f] beyond +/-%.4f" % (lo, hi, tol)))
    else:
        out.update(classification="UNKNOWN", reason=(
            "INCONCLUSIVE: diff CI [%.4f, %.4f] overlaps the +/-%.4f band "
            "edge" % (lo, hi, tol)))
    return out


def by_sport(obs: list) -> dict:
    out: dict = {}
    for r in obs:
        out.setdefault(r["sport"], []).append(r)
    return out


def plan_registrations(obs: dict, *, existing: set, now: float,
                       spec_id: str) -> list:
    """New preregistrations: for each dimension, the source sport is the one
    with the most observations at or before now meeting min_source_n; one
    test per other observed sport not yet tested on that dimension."""
    out = []
    for dim, recs in obs.items():
        cr = criteria_of(dim)
        hist = [r for r in recs if r["at"] <= now
                and r["sport"] != "unknown"]
        groups = by_sport(hist)
        ranked = sorted(((len(v), s) for s, v in groups.items()
                         if len(v) >= cr["min_source_n"]), reverse=True)
        if not ranked:
            continue
        source = ranked[0][1]
        m = measure(dim, groups[source])
        if m["value"] is None:
            continue
        csha = C.sha(cr)
        for target in sorted(groups):
            if target == source or (dim, target) in existing:
                continue
            out.append({
                "test_id": "twintx:" + C.sha([dim, source, target,
                                              csha])[:24],
                "dimension": dim, "source_sport": source,
                "target_sport": target, "metric": cr["metric"],
                "hypothesis": (
                    "%s measured on %s (n=%d, value=%s, 95%% CI [%s, %s], "
                    "records at or before declaration) holds in %s within "
                    "+/-%s, judged only on %s records at or after the "
                    "declaration" % (cr["metric"], source, m["n"],
                                     m["value"], m["low"], m["high"],
                                     target, cr["tolerance"], target)),
                "criteria": cr, "criteria_sha256": csha,
                "criteria_spec_id": spec_id, "declared_at": now,
                "source_window_end": now, "source_n": m["n"],
                "source_value": m["value"], "source_ci_low": m["low"],
                "source_ci_high": m["high"]})
    return out


def evaluate(test: dict, obs: dict) -> dict:
    """The forward evaluation of one test on its TARGET sport's records at
    or after its declaration."""
    fwd = [r for r in obs.get(test["dimension"], [])
           if r["sport"] == test["target_sport"]
           and r["at"] > float(test["declared_at"])]
    m = measure(test["dimension"], fwd)
    out = classify(test, m)
    out["test_id"] = test["test_id"]
    out["forward_start"] = float(test["declared_at"])
    out["input_sha256"] = C.sha([test["test_id"], [
        (str(r["id"]), r["at"]) for r in fwd]])
    return out


# ═════════════════════════════════════════════════════════════════════
# THE READ
# ═════════════════════════════════════════════════════════════════════

async def existing_tests(conn) -> list:
    rows = await conn.fetch(
        "SELECT test_id, dimension, source_sport, target_sport, hypothesis,"
        "       metric, criteria, source_n, source_value, source_ci_low, "
        "       source_ci_high, "
        "       extract(epoch FROM declared_at)::float8 AS declared_at "
        "  FROM twin_transfer_tests ORDER BY declared_at, test_id")
    out = []
    for r in rows:
        d = dict(r)
        d["criteria"] = C.jload(d["criteria"])
        out.append(d)
    return out


async def load(conn, *, now: float, actual_positions: list,
               slug_sport: dict | None = None,
               since: float | None = None) -> dict:
    since = float(now) - SOURCE_DAYS * 86400.0 if since is None else since
    preds = await R.resolved_predictions(conn, since=since, until=now)
    books = await R.book_samples(conn, slug_sport or {}, since=since,
                                 until=now)
    prem = await R.premap(conn, [p.get("us_market_slug") for p in preds])
    mk = await R.markout_mids(conn, [(p["slug"], p["entry_at"])
                                     for p in actual_positions
                                     if p.get("entry_at") is not None])
    return observations(preds=preds, books=books,
                        actual_positions=actual_positions, premap=prem,
                        markouts=mk)
