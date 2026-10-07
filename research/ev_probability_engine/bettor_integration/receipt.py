"""BETTOR x EV PROBABILITY ENGINE V1 -- the real-data receipt (research only).

Order of operations, which is the protection against tuning on the holdout:
  1. split events chronologically: the last HOLDOUT_FRAC of events (package
     sacred_holdout_split) is the UNTOUCHED HOLDOUT;
  2. development events only: event-clustered chronological walk-forward
     (package chronological_event_splits); every fold trains ONLY on events
     whose outcome was settled before the fold's first test decision;
  3. model selection on pooled walk-forward out-of-sample predictions ->
     a FROZEN SPEC, hashed and recorded before anything touches the holdout;
  4. the frozen spec is refit on development events settled before the
     holdout begins and evaluated ONCE on the holdout.
The capital verdict needs the frozen model to beat BOTH market priors
(venue no-vig and Pinnacle no-vig) on the holdout with event-clustered
intervals, acceptable calibration, a positive event-clustered lower bound on
net executable EV after every cost and haircut, and forward shadow evidence.
Anything less is CASH (or SHADOW where only forward evidence is missing).
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(HERE))

from bettor_ev_probability_engine.event_validation import (chronological_event_splits,   # noqa: E402
                                                           sacred_holdout_split)
from bettor_ev_probability_engine.metrics import (expected_calibration_error,             # noqa: E402
                                                  model_delta_metrics, probability_metrics,
                                                  reliability_table)
from bettor_ev_probability_engine.residual_model import shrink_to_market                  # noqa: E402
from models import (EPS, HierarchicalCalibrator, MarketResidualOffset, event_weights,     # noqa: E402
                    fit_shrink_weight)

VERSION = "BETTOR_EV_PROBABILITY_RECEIPT_V1"
AUTHORITY = "RESEARCH_PAPER_SHADOW_ONLY_NO_ORDER_NO_CAPITAL_AUTHORITY"
Z = 1.645
HOLDOUT_FRAC = 0.20
N_SPLITS = 5
MIN_CAL_EVENTS = 40
ISO_MIN_EVENTS = 150
MIN_SEGMENT_EVENTS = 30
MAX_ECE = 0.06
N_BOOT = 2000
FEEDBACK_WINDOW_EVENTS = 20
MARK_TOL_S = 300

PRIORS = ("MARKET_PRIOR_VENUE", "SHARP_PRIOR_PINNACLE")
CANDIDATES = PRIORS + ("BETTOR_RAW", "CALIBRATED_BETA", "CALIBRATED_ISOTONIC",
                       "CALIBRATED_BETA_SHRUNK", "MARKET_RESIDUAL")


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


# ── metrics ───────────────────────────────────────────────────────────────
def _ll(y, p):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def weighted_metrics(y, p, w) -> dict:
    y, p, w = map(lambda a: np.asarray(a, dtype=float), (y, p, w))
    W = w.sum()
    ece = 0.0
    edges = np.linspace(0, 1, 11)
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.any():
            ece += w[m].sum() / W * abs(np.average(y[m], weights=w[m]) - np.average(p[m], weights=w[m]))
    return {"events": float(W), "brier": float((w * (p - y) ** 2).sum() / W),
            "log_loss": float((w * _ll(y, p)).sum() / W), "ece_10": float(ece)}


def clustered_delta(y, p_base, p_cand, events, seed=11) -> dict:
    """Event-weighted mean of (candidate - baseline) loss with an event
    bootstrap 90% interval. Negative = candidate better."""
    y = np.asarray(y, dtype=float)
    ev = np.asarray(events).astype(str)
    w = event_weights(ev)
    out = {}
    for name, f in (("log_loss", _ll), ("brier", lambda yy, pp: (pp - yy) ** 2)):
        d = w * (f(y, np.asarray(p_cand)) - f(y, np.asarray(p_base)))
        uniq, inv = np.unique(ev, return_inverse=True)
        per = np.bincount(inv, weights=d)
        k = len(uniq)
        rng = np.random.default_rng(seed)
        boots = per[rng.integers(0, k, size=(N_BOOT, k))].mean(1) if k > 1 else np.array([per.mean()])
        out[name] = {"delta": float(per.mean()), "lo90": float(np.quantile(boots, 0.05)),
                     "hi90": float(np.quantile(boots, 0.95)), "events": int(k)}
    out["candidate_beats_baseline_ci"] = out["log_loss"]["hi90"] < 0 and out["brier"]["hi90"] < 0
    return out


def cluster_mean(values, events, seed=13) -> dict:
    v = np.asarray(values, dtype=float)
    ev = np.asarray(events).astype(str)
    if len(v) == 0:
        return {"n_rows": 0, "events": 0, "mean": None, "lo90": None, "hi90": None}
    uniq, inv = np.unique(ev, return_inverse=True)
    sums, cnt = np.bincount(inv, weights=v), np.bincount(inv)
    k = len(uniq)
    rng = np.random.default_rng(seed)
    if k < 2:
        return {"n_rows": int(len(v)), "events": k, "mean": float(v.mean()), "lo90": None, "hi90": None}
    idx = rng.integers(0, k, size=(N_BOOT, k))
    boots = sums[idx].sum(1) / cnt[idx].sum(1)
    return {"n_rows": int(len(v)), "events": int(k), "mean": float(v.mean()),
            "lo90": float(np.quantile(boots, 0.05)), "hi90": float(np.quantile(boots, 0.95))}


# ── fitting ───────────────────────────────────────────────────────────────
def arr(rows, k):
    return np.array([r[k] for r in rows], dtype=float)


def fit_models(train: list[dict]) -> dict:
    y, pm, pr = arr(train, "outcome"), arr(train, "p_venue"), arr(train, "p_raw")
    ev = [r["event_id"] for r in train]
    w = event_weights(ev)
    beta = HierarchicalCalibrator("beta", MIN_CAL_EVENTS, ISO_MIN_EVENTS).fit(train)
    iso = HierarchicalCalibrator("isotonic", MIN_CAL_EVENTS, ISO_MIN_EVENTS).fit(train)
    res = MarketResidualOffset(Z)
    if len(set(ev)) < MIN_CAL_EVENTS:
        # not enough settled events to learn anything: everything stays the
        # market prior (calibrators fall back to it, alpha = 0, w = 0)
        wshr, res.fitted = 0.0, False
    else:
        # shrink weight learned on the training rows' OWN calibrated values is
        # in-sample; it is a single scalar on the logit scale and is reported
        pb_train, _ = beta.predict(train)
        wshr = fit_shrink_weight(pb_train, pm, y, w)
        res.fit(pr, pm, y, ev)
        res.fitted = True
    return {"beta": beta, "iso": iso, "shrink_w": wshr, "res": res,
            "train_events": len(set(ev)), "train_rows": len(train)}


def predict_models(m: dict, rows: list[dict]) -> tuple[dict, dict]:
    pm, pr, ps = arr(rows, "p_venue"), arr(rows, "p_raw"), arr(rows, "p_sharp")
    pb, lev_b = m["beta"].predict(rows)
    pi, _ = m["iso"].predict(rows)
    out = {"MARKET_PRIOR_VENUE": pm, "SHARP_PRIOR_PINNACLE": np.clip(ps, EPS, 1 - EPS),
           "BETTOR_RAW": np.clip(pr, EPS, 1 - EPS), "CALIBRATED_BETA": pb, "CALIBRATED_ISOTONIC": pi,
           "CALIBRATED_BETA_SHRUNK": np.clip(shrink_to_market(pb, pm, m["shrink_w"]), EPS, 1 - EPS),
           "MARKET_RESIDUAL": m["res"].predict(pr, pm)}
    return out, {"calibration_level": lev_b}


def settled_before(rows, t):
    return [r for r in rows if r.get("settled_at") is not None and float(r["settled_at"]) < t]


# ── positions (settled paper record) for the loss waterfall / feedback ──────
def build_positions(orders: list[dict], outcome_by_slug: dict) -> list[dict]:
    groups = defaultdict(list)
    for r in orders:
        groups[r["group_id"]].append(r)
    out = []
    for gid, rs in groups.items():
        ent = [r for r in rs if r["role"] == "ENTRY" and (r.get("fill_qty") or 0) > 0]
        if len(ent) != 1:
            continue
        e = ent[0]
        side = e.get("side")
        q, px, fee = float(e["fill_qty"]), float(e["vwap"]), float(e.get("fees") or 0.0)
        sells = [r for r in rs if r.get("direction") == "SELL" and (r.get("fill_qty") or 0) > 0]
        sold = sum(float(r["fill_qty"]) for r in sells)
        # the venue mid just after each sale (first book <= MARK_TOL_S after
        # the fill) splits a sale into its spread cost and outcome luck
        sale_parts = []
        for r in sells:
            mid = None
            for m in r.get("marks") or []:
                if (m.get("bid") is not None and m.get("ask") is not None and m.get("actual_lag") is not None
                        and float(m["actual_lag"]) <= MARK_TOL_S):
                    b, a = float(m["bid"]), float(m["ask"])
                    if side == "SHORT":
                        b, a = 1 - a, 1 - b
                    mid = 0.5 * (b + a)
                    break
            sale_parts.append((float(r["fill_qty"]), float(r["vwap"]), mid))
        proceeds = sum(float(r["fill_qty"]) * float(r["vwap"]) for r in sells)
        sfee = sum(float(r.get("fees") or 0.0) for r in sells)
        held = max(0.0, q - sold)
        pay = e.get("settle_payout")
        if held > 1e-6 and pay is None:
            continue                               # still open: not settled
        cash = -q * px - fee + proceeds - sfee + (held * float(pay) if held > 1e-6 else 0.0)
        yl = outcome_by_slug.get(e["slug"])
        y = None if yl is None else (yl if side != "SHORT" else 1.0 - yl)
        if y is None and pay is not None:
            y = float(pay)
        sb, sa = e.get("sub_bid"), e.get("sub_ask")
        if sb is None or sa is None:
            continue
        bid, ask = (1 - float(sa), 1 - float(sb)) if side == "SHORT" else (float(sb), float(sa))
        out.append({"group_id": gid, "decision_id": e.get("decision_id"), "strategy": e.get("strategy"),
                    "slug": e["slug"], "side": side, "qty": q, "entry_px": px, "entry_fee": fee,
                    "sold": sold, "sell_vwap": proceeds / sold if sold else None, "sell_fees": sfee,
                    "sale_parts": sale_parts,
                    "p": e.get("p_pin"), "p_venue": 0.5 * (bid + ask), "touch_ask": ask, "y": y,
                    "realized": cash, "entered_at": e.get("first_fill"), "settled_at": e.get("settled"),
                    "start": e.get("start"), "league": e.get("league"), "sports_type": e.get("sports_type"),
                    "event": e.get("event")})
    return out


def position_rows(positions, rows_by_decision, fee_fn, dataset_mod) -> list[dict]:
    """Per position: expected, realized and the exact split of the error."""
    out = []
    for p in positions:
        if p["y"] is None or p["p"] is None:
            continue
        q, y, pr, pv = p["qty"], p["y"], float(p["p"]), p["p_venue"]
        fee_s, _ = fee_fn(p["touch_ask"], float(p["entered_at"] or 0))
        if fee_s is None:
            continue
        mgmt = ((p["sold"] / q) * (p["sell_vwap"] - y) if p["sold"] else 0.0) - p["sell_fees"] / q
        m_exec = sum(n * (vw - md) for n, vw, md in p["sale_parts"] if md is not None) - p["sell_fees"]
        m_luck = sum(n * (md - y) for n, vw, md in p["sale_parts"] if md is not None)
        m_unat = sum(n * (vw - y) for n, vw, md in p["sale_parts"] if md is None)
        expected = (pr - p["touch_ask"] - fee_s) * q
        model = (y - p["entry_px"] - p["entry_fee"] / q + mgmt) * q
        d = rows_by_decision.get(p["decision_id"]) or {}
        ev, _ = dataset_mod.event_identity({"event": p["event"], "slug": p["slug"]})
        tts = ((float(p["start"]) - float(p["entered_at"])) / 60.0) if (p["start"] and p["entered_at"]) else None
        out.append({
            "decision_id": p["decision_id"], "event_id": ev, "market_id": p["slug"], "strategy": p["strategy"],
            "qty": q, "expected_usd": expected, "realized_usd": p["realized"],
            "error_usd": p["realized"] - expected,
            "outcome_vs_market_usd": (y - pv) * q,                 # luck against the market's own price
            "bettor_disagreement_usd": (pv - pr) * q,              # cost of BETTOR's departure from the market
            "execution_usd": ((p["touch_ask"] - p["entry_px"]) + (fee_s - p["entry_fee"] / q)) * q,
            "management_usd": mgmt * q,
            "management_spread_cost_usd": m_exec, "management_outcome_luck_usd": m_luck,
            "management_unattributed_usd": m_unat,
            "settlement_usd": p["realized"] - model,
            "sport": d.get("sport") or (p["league"] or (ev or "unknown").split("-")[0]).lower(),
            "league": d.get("league") or (p["league"] or "UNKNOWN").lower(),
            "family": d.get("family") or dataset_mod.family_class(p["slug"], None),
            "family_class": dataset_mod.family_class(p["slug"], None),
            "favorite": "FAVORITE" if pv >= 0.5 else "LONGSHOT",
            "price_band": dataset_mod.band(p["touch_ask"], (0.2, 0.4, 0.6, 0.8),
                                           ("00-20", "20-40", "40-60", "60-80", "80-100")),
            "prob_band": dataset_mod.band(pr, (0.2, 0.4, 0.6, 0.8), ("00-20", "20-40", "40-60", "60-80", "80-100")),
            "regime": d.get("regime") or ("REGIME_UNKNOWN" if tts is None else ("PREGAME" if tts > 0 else "IN_PLAY")),
            "tts_band": d.get("tts_band") or dataset_mod.band(tts, (0, 60, 360, 1440),
                                                             ("IN_PLAY_OR_PAST_START", "0-1h", "1-6h", "6-24h", "24h+")),
            "freshness_band": d.get("freshness_band") or "UNKNOWN_NOT_IN_EXTRACT",
            "model_id": d.get("model_id") or (p["strategy"] or "UNKNOWN"),
            "settled_at": p["settled_at"], "entered_at": p["entered_at"],
        })
    return out


WATERFALL_DIMS = ("sport", "league", "family", "family_class", "favorite", "price_band", "prob_band",
                  "regime", "tts_band", "freshness_band", "model_id", "strategy")
PARTS = ("expected_usd", "realized_usd", "error_usd", "outcome_vs_market_usd", "bettor_disagreement_usd",
         "execution_usd", "management_usd", "management_spread_cost_usd", "management_outcome_luck_usd",
         "management_unattributed_usd", "settlement_usd")


def waterfall(prs: list[dict]) -> dict:
    tot = {k: float(sum(p[k] for p in prs)) for k in PARTS}
    tot["identity_check_usd"] = tot["error_usd"] - (tot["outcome_vs_market_usd"] + tot["bettor_disagreement_usd"]
                                                    + tot["execution_usd"] + tot["management_usd"]
                                                    + tot["settlement_usd"])
    tot["management_split_check_usd"] = tot["management_usd"] - (tot["management_spread_cost_usd"]
                                                                 + tot["management_outcome_luck_usd"]
                                                                 + tot["management_unattributed_usd"])
    by = {}
    for dim in WATERFALL_DIMS:
        g = defaultdict(list)
        for p in prs:
            g[p[dim]].append(p)
        rows = []
        for k, ps in g.items():
            r = {dim: k, "positions": len(ps), "unique_events": len({p["event_id"] for p in ps})}
            for f in PARTS:
                r[f] = float(sum(p[f] for p in ps))
            r["share_of_total_error"] = r["error_usd"] / tot["error_usd"] if tot["error_usd"] else None
            rows.append(r)
        by[dim] = sorted(rows, key=lambda r: r["error_usd"])
    return {"positions": len(prs), "unique_events": len({p["event_id"] for p in prs}),
            "unique_settled_markets": len({p["market_id"] for p in prs}), "total": tot,
            "identity": "error = outcome_vs_market + bettor_disagreement + execution + management + settlement; "
                        "management = spread_cost + outcome_luck + unattributed (sales with no venue book within %d s)" % MARK_TOL_S,
            "by_dimension": by}


def _feedback_action(fs: list[dict]) -> dict:
    """Shrink / disable rule on settled positions in settlement order: the
    last FEEDBACK_WINDOW_EVENTS events' realized-minus-expected per contract
    (event-clustered 90% interval) entirely below zero -> SHRINK_ALPHA_TO_ZERO
    (fall back to the market prior); below zero over the whole history too
    -> DISABLE. Too few events -> UNMEASURED (stays SHADOW_ONLY)."""
    evs = []
    for f in fs:
        if f["event_id"] not in evs:
            evs.append(f["event_id"])
    if len(evs) < FEEDBACK_WINDOW_EVENTS:
        return {"events": len(evs), "action": "UNMEASURED_KEEP_SHADOW_ONLY"}
    last = set(evs[-FEEDBACK_WINDOW_EVENTS:])
    win = [f for f in fs if f["event_id"] in last]
    cm = cluster_mean([(f["realized_usd"] - f["expected_usd"]) / f["qty"] for f in win], [f["event_id"] for f in win])
    full = cluster_mean([(f["realized_usd"] - f["expected_usd"]) / f["qty"] for f in fs], [f["event_id"] for f in fs])
    act = ("DISABLE" if (cm["hi90"] is not None and cm["hi90"] < 0 and full["hi90"] is not None and full["hi90"] < 0)
           else "SHRINK_ALPHA_TO_ZERO" if (cm["hi90"] is not None and cm["hi90"] < 0)
           else "MONITOR")
    return {"events": len(evs), "last_window_events": FEEDBACK_WINDOW_EVENTS,
            "realized_minus_expected_per_contract_last_window": cm,
            "realized_minus_expected_per_contract_all": full, "action": act}


# ── the receipt ───────────────────────────────────────────────────────────
def segkey(r):
    return "|".join((r["sport"], r["family"], r["regime"]))


def run(rows: list[dict], positions_src: list[dict], *, dataset_mod, fee_fn, provenance: dict) -> dict:
    rows = [r for r in rows if r.get("p_sharp") is not None]
    rows.sort(key=lambda r: (r["at"], r["event_id"], r["decision_id"]))
    df = pd.DataFrame({"event_id": [r["event_id"] for r in rows], "event_time": [r["at"] for r in rows]})
    dev_idx, hold_idx = sacred_holdout_split(df, holdout_frac=HOLDOUT_FRAC)
    dev = [rows[i] for i in dev_idx]
    hold = [rows[i] for i in hold_idx]
    dev_ev, hold_ev = {r["event_id"] for r in dev}, {r["event_id"] for r in hold}
    assert not dev_ev & hold_ev, "an event is in both development and holdout"
    hold_start = min(r["at"] for r in hold)

    # ── walk-forward on development events only ──
    ddf = pd.DataFrame({"event_id": [r["event_id"] for r in dev], "event_time": [r["at"] for r in dev]})
    n_dev_ev = len(dev_ev)
    min_train = max(40, int(0.4 * n_dev_ev))
    test_events = max(10, math.ceil((n_dev_ev - min_train) / N_SPLITS))
    folds = chronological_event_splits(ddf, min_train_events=min_train, test_events=test_events, n_splits=N_SPLITS)
    oos, fold_meta = [], []
    for i, f in enumerate(folds):
        te = [dev[j] for j in f.test_idx]
        t0 = min(r["at"] for r in te)
        tr_all = [dev[j] for j in f.train_idx]
        tr = settled_before(tr_all, t0)
        assert not {r["event_id"] for r in tr} & {r["event_id"] for r in te}
        m = fit_models(tr)
        preds, meta = predict_models(m, te)
        for k, r in enumerate(te):
            oos.append({**r, "fold": i, **{"p_" + c: float(preds[c][k]) for c in CANDIDATES},
                        "cal_level": meta["calibration_level"][k][0]})
        fold_meta.append({"fold": i, "train_events_settled_before_test": m["train_events"],
                          "train_events_excluded_unsettled": len({r["event_id"] for r in tr_all}) - m["train_events"],
                          "test_events": len({r["event_id"] for r in te}), "test_rows": len(te),
                          "shrink_weight": m["shrink_w"],
                          "residual": m["res"].summary() if m["res"].fitted else "UNFITTED_INSUFFICIENT_SETTLED_EVENTS_ALPHA_0",
                          "calibrators_fitted": len(m["beta"].cals)})

    dev_settled = settled_before(dev, hold_start)
    if not oos:
        why = "NO_WALK_FORWARD_FOLDS"
        return {"version": VERSION, "authority": AUTHORITY, "provenance": provenance, "status": "INSUFFICIENT_EVIDENCE",
                "counts": {"decision_rows": len(rows), "unique_canonical_events": len({r["event_id"] for r in rows}),
                           "unique_settled_markets": len({r["market_id"] for r in rows}),
                           "holdout_events": len(hold_ev), "train_dev_events": n_dev_ev},
                "4_walk_forward": {"folds": fold_meta, "selection": None},
                "5_untouched_holdout": {"selected_model": None, "status": "NOT_EVALUATED"},
                "11_expected_vs_realized_feedback": {"rows": 0},
                "12_capital_verdict": {"verdict": "CASH", "reasons": [why], "live_capital": "NOT_ACTIVATED"},
                "_feedback_rows": []}

    def model_table(rs):
        y, ev = arr(rs, "outcome"), [r["event_id"] for r in rs]
        w = event_weights(ev)
        out = {}
        for c in CANDIDATES:
            p = arr(rs, "p_" + c)
            out[c] = {"row_level_package_metrics": probability_metrics(y, p),
                      "event_weighted": weighted_metrics(y, p, w),
                      "vs_MARKET_PRIOR_VENUE": clustered_delta(y, arr(rs, "p_MARKET_PRIOR_VENUE"), p, ev),
                      "vs_SHARP_PRIOR_PINNACLE": clustered_delta(y, arr(rs, "p_SHARP_PRIOR_PINNACLE"), p, ev)}
        return out

    wf_table = model_table(oos)
    # ── freeze the selection BEFORE the holdout is touched ──
    sel = min(CANDIDATES, key=lambda c: wf_table[c]["event_weighted"]["log_loss"])
    frozen = {"selected_model": sel, "selected_on": "pooled walk-forward OOS event-weighted log loss",
              "hyper": {"MIN_CAL_EVENTS": MIN_CAL_EVENTS, "ISO_MIN_EVENTS": ISO_MIN_EVENTS, "Z": Z,
                        "HOLDOUT_FRAC": HOLDOUT_FRAC, "N_SPLITS": N_SPLITS},
              "dev_events": n_dev_ev, "dev_last_decision": max(r["at"] for r in dev)}
    frozen["frozen_spec_sha256"] = sha(frozen)

    # ── untouched holdout: one refit on dev events settled before it ──
    mfin = fit_models(dev_settled)
    hp, hmeta = predict_models(mfin, hold)
    hrows = [{**r, **{"p_" + c: float(hp[c][k]) for c in CANDIDATES},
              "cal_level": hmeta["calibration_level"][k][0], "cal_level_events": hmeta["calibration_level"][k][1]}
             for k, r in enumerate(hold)]
    hold_table = model_table(hrows)

    # ── BETTOR's internal model, on the rows where it recorded one ──
    def internal_block(rs):
        sub = [r for r in rs if r.get("p_int") is not None]
        if len({r["event_id"] for r in sub}) < MIN_SEGMENT_EVENTS:
            return {"rows": len(sub), "events": len({r["event_id"] for r in sub}),
                    "status": "UNMEASURED_INSUFFICIENT_EVENTS"}
        y, ev = arr(sub, "outcome"), [r["event_id"] for r in sub]
        w = event_weights(ev)
        out = {"rows": len(sub), "events": len(set(ev)), "strategies": dict(Counter(r["strategy"] for r in sub))}
        for name, key in (("MARKET_PRIOR_VENUE", "p_venue"), ("SHARP_PRIOR_PINNACLE", "p_sharp"),
                          ("BETTOR_INTERNAL_MODEL", "p_int"), ("BETTOR_BLENDED", "p_blend")):
            p = np.clip(arr(sub, key), EPS, 1 - EPS)
            out[name] = {"event_weighted": weighted_metrics(y, p, w),
                         "vs_MARKET_PRIOR_VENUE": clustered_delta(y, arr(sub, "p_venue"), p, ev),
                         "vs_SHARP_PRIOR_PINNACLE": clustered_delta(y, arr(sub, "p_sharp"), p, ev)}
        return out

    # ── calibration table by sport x family x regime (walk-forward OOS) ──
    cal_tab = []
    segs = defaultdict(list)
    for r in oos:
        segs[segkey(r)].append(r)
    for k, rs in sorted(segs.items()):
        evs = {r["event_id"] for r in rs}
        row = {"segment": k, "decision_rows": len(rs), "unique_events": len(evs),
               "unique_settled_markets": len({r["market_id"] for r in rs}),
               "calibration_levels_used": dict(Counter(r["cal_level"] for r in rs))}
        if len(evs) < MIN_SEGMENT_EVENTS:
            row["status"] = "UNMEASURED_INSUFFICIENT_EVENTS"
        else:
            y, w = arr(rs, "outcome"), event_weights([r["event_id"] for r in rs])
            for c in ("MARKET_PRIOR_VENUE", "SHARP_PRIOR_PINNACLE", "BETTOR_RAW", "CALIBRATED_BETA",
                      "MARKET_RESIDUAL", sel):
                row[c] = weighted_metrics(y, arr(rs, "p_" + c), w)
            row["raw_vs_venue"] = clustered_delta(y, arr(rs, "p_MARKET_PRIOR_VENUE"), arr(rs, "p_BETTOR_RAW"),
                                                  [r["event_id"] for r in rs])
            row["status"] = "MEASURED"
        cal_tab.append(row)

    # ── the same table at every fallback level, walk-forward OOS and holdout ──
    def hier_table(rs):
        out = []
        for keys in (("sport", "family", "regime"), ("sport", "family"), ("sport",), ("family",), ()):
            g = defaultdict(list)
            for r in rs:
                g[tuple(r[k] for k in keys)].append(r)
            for kk, gr in sorted(g.items()):
                evs = {r["event_id"] for r in gr}
                row = {"level": "/".join(keys) or "GLOBAL", "segment": "|".join(kk) or "ALL",
                       "decision_rows": len(gr), "unique_events": len(evs),
                       "unique_settled_markets": len({r["market_id"] for r in gr})}
                if len(evs) < MIN_SEGMENT_EVENTS:
                    row["status"] = "UNMEASURED_INSUFFICIENT_EVENTS"
                else:
                    y, ev = arr(gr, "outcome"), [r["event_id"] for r in gr]
                    w = event_weights(ev)
                    row["status"] = "MEASURED"
                    for c in ("MARKET_PRIOR_VENUE", "SHARP_PRIOR_PINNACLE", "BETTOR_RAW", sel):
                        row[c] = weighted_metrics(y, arr(gr, "p_" + c), w)
                    row["raw_vs_venue"] = clustered_delta(y, arr(gr, "p_MARKET_PRIOR_VENUE"), arr(gr, "p_BETTOR_RAW"), ev)
                    row["sharp_vs_venue"] = clustered_delta(y, arr(gr, "p_MARKET_PRIOR_VENUE"),
                                                            arr(gr, "p_SHARP_PRIOR_PINNACLE"), ev)
                out.append(row)
        return out

    # ── reliability / win rate by bucket, residual vs market, CLV ──
    def reliab(rs, c):
        t = reliability_table(arr(rs, "outcome"), arr(rs, "p_" + c), bins=10)
        return json.loads(t.to_json(orient="records"))

    def residual_block(rs):
        ev = [r["event_id"] for r in rs]
        out = {"bettor_minus_venue_p": cluster_mean([r["p_raw"] - r["p_venue"] for r in rs], ev),
               "outcome_minus_venue_p": cluster_mean([r["outcome"] - r["p_venue"] for r in rs], ev),
               "outcome_minus_bettor_p": cluster_mean([r["outcome"] - r["p_raw"] for r in rs], ev)}
        clv_rows = [r for r in rs if r.get("p_close") is not None]
        out["clv"] = {
            "defensible_rows": len(clv_rows),
            "close_minus_entry_ask": cluster_mean([r["p_close"] - r["ask"] for r in clv_rows],
                                                  [r["event_id"] for r in clv_rows]),
            "market_moved_toward_bettor": cluster_mean(
                [(r["p_close"] - r["p_venue"]) * np.sign(r["p_raw"] - r["p_venue"]) for r in clv_rows],
                [r["event_id"] for r in clv_rows]),
            "basis": "venue mid of the last book observed at/before the premap game start"}
        return out

    # ── execution / management costs measured BEFORE the holdout ──
    outcome_by_slug = {}
    for r in rows:
        outcome_by_slug[r["market_id"]] = r["outcome"] if r["side"] != "SHORT" else 1.0 - r["outcome"]
    positions = build_positions(positions_src, outcome_by_slug)
    pre = [p for p in positions if p["entered_at"] and float(p["entered_at"]) < hold_start]
    pre_ev = [dataset_mod.event_identity({"event": p["event"], "slug": p["slug"]})[0] for p in pre]
    slip = cluster_mean([p["entry_px"] - p["touch_ask"] for p in pre], pre_ev)
    # management cost per contract entered = the SPREAD cost of the protection
    # / exit sales (sale VWAP vs the venue mid just after it, plus sell fees);
    # the outcome luck of selling early is not a cost of the policy. Positions
    # with an unattributable sale are left out (counted).
    mg, mg_ev, mg_skip = [], [], 0
    for p, e in zip(pre, pre_ev):
        if any(md is None for _, _, md in p["sale_parts"]):
            mg_skip += 1; continue
        mg.append((sum(n * (vw - md) for n, vw, md in p["sale_parts"]) - p["sell_fees"]) / p["qty"])
        mg_ev.append(e)
    mgmt = dict(cluster_mean(mg, mg_ev), positions_with_unattributed_sale_excluded=mg_skip)

    # ── EV binding on the holdout with the frozen model ──
    seg_ece = {}
    for k, rs in segs.items():
        if len({r["event_id"] for r in rs}) >= MIN_SEGMENT_EVENTS:
            seg_ece[k] = weighted_metrics(arr(rs, "outcome"), arr(rs, "p_" + sel),
                                          event_weights([r["event_id"] for r in rs]))["ece_10"]
    pooled_ece = wf_table[sel]["event_weighted"]["ece_10"]
    slip_ub = max(0.0, (slip["hi90"] if slip["hi90"] is not None else float("inf")))
    mgmt_cost = max(0.0, -(mgmt["lo90"] if mgmt["lo90"] is not None else -float("inf")))
    ev_rows = []
    for r in hrows:
        p = r["p_" + sel]
        n_lv = r["cal_level_events"] or 0
        if sel in PRIORS:
            prob_hc = 0.0       # the market's own price carries no BETTOR estimation error
        else:
            prob_hc = Z * math.sqrt(p * (1 - p) / n_lv) if n_lv else float("inf")
        cal_hc = seg_ece.get(segkey(r), pooled_ece)
        if r["fee"] is None:
            ev_rows.append({**r, "net_ev": None, "net_ev_lb": None, "gate": "REFUSE_FEE_UNMEASURED"}); continue
        if slip["mean"] is None or not math.isfinite(slip_ub):
            ev_rows.append({**r, "net_ev": None, "net_ev_lb": None, "gate": "REFUSE_SLIPPAGE_UNMEASURED"}); continue
        if not math.isfinite(mgmt_cost):
            ev_rows.append({**r, "net_ev": None, "net_ev_lb": None, "gate": "REFUSE_MANAGEMENT_COST_UNMEASURED"}); continue
        net = p - r["ask"] - r["fee"] - slip["mean"] - mgmt_cost
        lb = p - prob_hc - cal_hc - r["ask"] - r["fee"] - slip_ub - mgmt_cost
        ev_rows.append({**r, "net_ev": net, "net_ev_lb": lb,
                        "realized_net": r["outcome"] - r["ask"] - r["fee"],
                        "gate": "ADMIT_SHADOW" if lb > 0 else "REFUSE_CASH_WAIT"})
    by_strat = {}
    for s in sorted({r["strategy"] for r in ev_rows}):
        rs = [r for r in ev_rows if r["strategy"] == s and r["net_ev"] is not None]
        adm = [r for r in rs if r["gate"] == "ADMIT_SHADOW"]
        ent = [r for r in rs if r["verdict"] == "ENTER"]
        by_strat[s] = {
            "holdout_rows": len(rs), "holdout_events": len({r["event_id"] for r in rs}),
            "gate": dict(Counter(r["gate"] for r in rs)),
            "net_ev_lower_bound_all_rows": cluster_mean([r["net_ev_lb"] for r in rs], [r["event_id"] for r in rs]),
            "admitted": {"rows": len(adm), "events": len({r["event_id"] for r in adm}),
                         "expected_net_ev_lb": cluster_mean([r["net_ev_lb"] for r in adm], [r["event_id"] for r in adm]),
                         "realized_net_ev": cluster_mean([r["realized_net"] for r in adm], [r["event_id"] for r in adm])},
            "what_production_actually_entered": {
                "rows": len(ent), "events": len({r["event_id"] for r in ent}),
                "expected_net_ev_frozen_model": cluster_mean([r["net_ev"] for r in ent], [r["event_id"] for r in ent]),
                "realized_net_ev": cluster_mean([r["realized_net"] for r in ent], [r["event_id"] for r in ent])}}
    adm_all = [r for r in ev_rows if r["gate"] == "ADMIT_SHADOW"]
    ev_block = {
        "formula": "net = p_frozen - executable_ask - fee - slippage - adverse_selection - management + rebate",
        "haircuts": {"probability": "z*sqrt(p(1-p)/events at the calibration level used); 0 for a prior",
                     "calibration": "walk-forward ECE of the frozen model in that sport x family x regime (pooled if unmeasured)",
                     "execution": "slippage upper 90% bound (event-clustered) from paper fills entered before the holdout",
                     "correlation_capacity": "every aggregate is an event-clustered bootstrap bound; one game = one draw"},
        "components": {"slippage_per_contract": slip, "slippage_used_in_lb": slip_ub,
                       "management_cost_per_contract": mgmt, "management_cost_used": mgmt_cost,
                       "adverse_selection": "HOLD_TO_SETTLEMENT: carried by the outcome; sub-minute adverse selection UNMEASURED",
                       "rebate": "0.0 (taker path; maker path not admissible without measured maker fill/AS -- see Profitability Stack)",
                       "fees": "BETTOR published schedule, taker, at the executable ask"},
        "gate_counts": dict(Counter(r["gate"] for r in ev_rows)),
        "admitted_event_clustered_lb": cluster_mean([r["net_ev_lb"] for r in adm_all], [r["event_id"] for r in adm_all]),
        "admitted_realized": cluster_mean([r["realized_net"] for r in adm_all], [r["event_id"] for r in adm_all]),
        "by_strategy": by_strat}

    # ── loss waterfall + residual feedback on settled paper positions ──
    rows_by_dec = {r["decision_id"]: r for r in rows}
    prs = position_rows(positions, rows_by_dec, fee_fn, dataset_mod)
    wf = waterfall(prs)
    feedback = []
    for p in sorted(prs, key=lambda x: float(x["settled_at"] or x["entered_at"] or 0)):
        d = rows_by_dec.get(p["decision_id"])
        cal_res = None
        if d is not None:
            pc, _ = predict_models(mfin, [d])
            cal_res = (float(pc[sel][0]) - d["p_raw"]) * p["qty"]
        feedback.append({**{k: p[k] for k in ("decision_id", "event_id", "market_id", "strategy", "sport",
                                              "family", "regime", "qty", "expected_usd", "realized_usd",
                                              "settled_at")},
                         "segment": "|".join((p["sport"], p["family"], p["regime"])),
                         "forecast_residual_usd": -p["bettor_disagreement_usd"],
                         "calibration_residual_usd": cal_res,
                         "calibration_residual_basis": ("IN_SAMPLE_FOR_DEV_POSITIONS" if d and d["at"] < hold_start
                                                        else "OUT_OF_SAMPLE"),
                         "execution_residual_usd": p["execution_usd"], "management_residual_usd": p["management_usd"],
                         "settlement_residual_usd": p["settlement_usd"],
                         "outcome_variance_usd": p["outcome_vs_market_usd"] + p["bettor_disagreement_usd"]})
    def feedback_actions(key):
        acts = {}
        by = defaultdict(list)
        for f in feedback:
            by[f[key]].append(f)
        for k, fs in sorted(by.items()):
            acts[k] = _feedback_action(fs)
        return acts

    seg_actions = feedback_actions("segment")
    actions_by_level = {"sport|family|regime": seg_actions, "strategy": feedback_actions("strategy"),
                        "family": feedback_actions("family")}

    # ── kill list ──
    kill = []
    for row in cal_tab:
        k = row["segment"]
        reasons = []
        if row["status"] != "MEASURED":
            reasons.append("INSUFFICIENT_INDEPENDENT_EVENTS")
        else:
            rs = segs[k]
            y, ev = arr(rs, "outcome"), [r["event_id"] for r in rs]
            best_b = min(("BETTOR_RAW", "CALIBRATED_BETA", "MARKET_RESIDUAL", "CALIBRATED_BETA_SHRUNK"),
                         key=lambda c: row.get(c, weighted_metrics(y, arr(rs, "p_" + c), event_weights(ev)))["log_loss"])
            d = clustered_delta(y, arr(rs, "p_MARKET_PRIOR_VENUE"), arr(rs, "p_" + best_b), ev)
            if not d["candidate_beats_baseline_ci"]:
                reasons.append("DOES_NOT_BEAT_MARKET_PRIOR")
            if row["BETTOR_RAW"]["ece_10"] > MAX_ECE:
                reasons.append("RAW_MATERIALLY_MISCALIBRATED")
            per_fold = []
            for fo in sorted({r["fold"] for r in rs}):
                fr = [r for r in rs if r["fold"] == fo]
                if len({r["event_id"] for r in fr}) >= 5:
                    yy, ww = arr(fr, "outcome"), event_weights([r["event_id"] for r in fr])
                    per_fold.append(weighted_metrics(yy, arr(fr, "p_BETTOR_RAW"), ww)["log_loss"]
                                    - weighted_metrics(yy, arr(fr, "p_MARKET_PRIOR_VENUE"), ww)["log_loss"])
            if len(per_fold) >= 2 and min(per_fold) < 0 < max(per_fold):
                reasons.append("UNSTABLE_RESIDUAL_ACROSS_FOLDS")
        hk = [r for r in ev_rows if segkey(r) == k and r["net_ev_lb"] is not None]
        if hk:
            lbm = cluster_mean([r["net_ev_lb"] for r in hk], [r["event_id"] for r in hk])
            if lbm["mean"] is not None and lbm["mean"] <= 0:
                reasons.append("NEGATIVE_CONSERVATIVE_EXECUTABLE_EV")
        fa = seg_actions.get(k, {}).get("action")
        if fa in ("DISABLE", "SHRINK_ALPHA_TO_ZERO"):
            reasons.append("FEEDBACK_" + fa)
        if reasons:
            cls = ("CASH" if any(x in reasons for x in ("DOES_NOT_BEAT_MARKET_PRIOR",
                                                        "NEGATIVE_CONSERVATIVE_EXECUTABLE_EV",
                                                        "FEEDBACK_DISABLE")) else "SHADOW_ONLY")
            kill.append({"segment": k, "classification": cls, "reasons": reasons,
                         "unique_events": row["unique_events"], "decision_rows": row["decision_rows"],
                         "new_paper_capital": "NOT_ALLOWED"})

    for lvl in ("strategy", "family"):
        for k, a in actions_by_level[lvl].items():
            if a["action"] in ("DISABLE", "SHRINK_ALPHA_TO_ZERO"):
                kill.append({"segment": "%s=%s" % (lvl, k), "classification": "CASH",
                             "reasons": ["FEEDBACK_" + a["action"] + "_REALIZED_BELOW_EXPECTED"],
                             "unique_events": a["events"], "new_paper_capital": "NOT_ALLOWED"})

    # ── governor ──
    hs = hold_table[sel]
    beats = (sel not in PRIORS and hs["vs_MARKET_PRIOR_VENUE"]["candidate_beats_baseline_ci"]
             and hs["vs_SHARP_PRIOR_PINNACLE"]["candidate_beats_baseline_ci"])
    reasons = []
    if sel == "MARKET_PRIOR_VENUE":
        reasons.append("SELECTED_MODEL_IS_THE_VENUE_PRICE: no probability beat the market BETTOR trades against")
    elif sel == "SHARP_PRIOR_PINNACLE":
        reasons.append("SELECTED_MODEL_IS_THE_PINNACLE_PRIOR: BETTOR's own calibration / residual / internal "
                       "model added nothing beyond Pinnacle out of sample")
    if not beats:
        reasons.append("FROZEN_MODEL_DOES_NOT_BEAT_BOTH_MARKET_PRIORS_ON_HOLDOUT_WITH_EVENT_CLUSTERED_CI")
    if hs["event_weighted"]["ece_10"] > MAX_ECE:
        reasons.append("HOLDOUT_CALIBRATION_ERROR_ABOVE_%.2f" % MAX_ECE)
    lb_all = ev_block["admitted_event_clustered_lb"]
    if not adm_all or lb_all["lo90"] is None or lb_all["lo90"] <= 0:
        reasons.append("NO_POSITIVE_EVENT_CLUSTERED_LOWER_BOUND_NET_EXECUTABLE_EV")
    real = ev_block["admitted_realized"]
    if adm_all and (real["lo90"] is None or real["lo90"] <= 0):
        reasons.append("ADMITTED_HOLDOUT_REALIZED_NET_EV_LOWER_BOUND_NOT_POSITIVE")
    if len(hold_ev) < 100:
        reasons.append("FEWER_THAN_100_HOLDOUT_EVENTS")
    reasons.append("NO_FORWARD_SHADOW_EVIDENCE_FOR_THE_FROZEN_MODEL_YET")
    hard = [r for r in reasons if not r.startswith("NO_FORWARD")]
    verdict = "CASH" if hard else "SHADOW"

    return {
        "version": VERSION, "authority": AUTHORITY, "provenance": provenance,
        "counts": {
            "decision_rows": len(rows), "unique_canonical_events": len({r["event_id"] for r in rows}),
            "unique_settled_markets": len({r["market_id"] for r in rows}),
            "train_dev_rows": len(dev), "train_dev_events": n_dev_ev,
            "walk_forward_oos_rows": len(oos), "walk_forward_oos_events": len({r["event_id"] for r in oos}),
            "holdout_rows": len(hold), "holdout_events": len(hold_ev),
            "holdout_settled_markets": len({r["market_id"] for r in hold}),
            "final_fit_events_settled_before_holdout": mfin["train_events"],
            "decisions_by_strategy": dict(Counter(r["strategy"] for r in rows)),
            "event_identity_sources": dict(Counter(r["event_src"] for r in rows)),
            "raw_probability_sources": dict(Counter(r["raw_src"] for r in rows)),
        },
        "window": {"from": min(r["at"] for r in rows), "to": max(r["at"] for r in rows),
                   "holdout_from": hold_start},
        "3_market_prior_benchmark_walk_forward": wf_table,
        "3b_internal_model_subset": {"walk_forward_oos": internal_block(oos), "holdout": internal_block(hrows)},
        "4_walk_forward": {"folds": fold_meta, "selection": frozen},
        "5_untouched_holdout": {"frozen_spec_sha256": frozen["frozen_spec_sha256"], "selected_model": sel,
                                "final_fit": {"settled_events": mfin["train_events"], "shrink_weight": mfin["shrink_w"],
                                              "calibrators_fitted": len(mfin["beta"].cals),
                                              "residual": mfin["res"].summary() if mfin["res"].fitted
                                              else "UNFITTED_INSUFFICIENT_SETTLED_EVENTS_ALPHA_0"},
                                "models": hold_table,
                                "calibration_levels_used": dict(Counter(r["cal_level"] for r in hrows))},
        "6_calibration_table_sport_family_regime": cal_tab,
        "6b_calibration_hierarchy": {"walk_forward_oos": hier_table(oos), "holdout": hier_table(hrows),
                                     "min_events_to_measure": MIN_SEGMENT_EVENTS},
        "reliability": {"walk_forward": {c: reliab(oos, c) for c in ("MARKET_PRIOR_VENUE", "BETTOR_RAW", sel)},
                        "holdout": {c: reliab(hrows, c) for c in ("MARKET_PRIOR_VENUE", "BETTOR_RAW", sel)}},
        "residual_and_clv": {"walk_forward": residual_block(oos), "holdout": residual_block(hrows)},
        "7_loss_contribution_waterfall": wf,
        "8_kill_list": kill,
        "9_raw_vs_calibrated_vs_residual": {
            split: {c: t[c]["event_weighted"] | {"row_level": t[c]["row_level_package_metrics"]}
                    for c in ("MARKET_PRIOR_VENUE", "SHARP_PRIOR_PINNACLE", "BETTOR_RAW", "CALIBRATED_BETA",
                              "CALIBRATED_ISOTONIC", "CALIBRATED_BETA_SHRUNK", "MARKET_RESIDUAL")}
            for split, t in (("walk_forward", wf_table), ("holdout", hold_table))},
        "10_conservative_executable_ev": ev_block,
        "11_expected_vs_realized_feedback": {"rows": len(feedback), "segment_actions": seg_actions,
                                             "actions_by_level": actions_by_level,
                                             "file": "feedback.jsonl.gz"},
        "12_capital_verdict": {"verdict": verdict, "reasons": reasons,
                               "rule": "CHALLENGER requires every hard check plus forward shadow evidence; "
                                       "SHADOW when only forward evidence is missing; otherwise CASH",
                               "live_capital": "NOT_ACTIVATED"},
        "_feedback_rows": feedback,
    }
