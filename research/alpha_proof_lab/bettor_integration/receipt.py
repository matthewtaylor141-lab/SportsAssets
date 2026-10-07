"""BETTOR x ALPHA PROOF LAB V1 -- THE REAL-DATA WALK-FORWARD RECEIPT.

RESEARCH / SHADOW ONLY. This module reads a dataset extracted SELECT-only
from BETTOR's immutable records (research/apl_extract_v1.sql: paper_decisions,
the venue book each decision recorded, the market's closing book, the next
15 minutes of book path, and settled outcomes from paper_settlements /
external_valuations) and produces the twelve outputs the owner required
before any PAPER integration. It has NO order, cancel, credential, funded,
risk-limit or capital authority and writes nothing but its receipt file.

Methodology is the package's (bettor_alpha_lab): market-prior residual
logistic, beta / isotonic calibration, taker / maker executable EV,
fractional Kelly with correlation / capacity haircuts, chronological
walk-forward, CLV, CSCV PBO, deflated Sharpe, signed model card. This layer
only maps BETTOR's records onto it, and is explicit where evidence is absent:
a missing cost is UNMEASURED (never zero), an undersized segment is
UNMEASURED, and no strategy is promoted by being least negative -- no
positive eligible strategy means CASH.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
LAB = HERE.parent
sys.path.insert(0, str(LAB))

from bettor_alpha_lab.allocation import allocate_binary          # noqa: E402
from bettor_alpha_lab.calibration import (                       # noqa: E402
    BetaCalibrator, IsotonicCalibrator, probability_metrics)
from bettor_alpha_lab.execution import (                         # noqa: E402
    CostBreakdown, binary_maker_ev, binary_taker_ev)
from bettor_alpha_lab.market import clamp01, logit               # noqa: E402
from bettor_alpha_lab.model_card import ModelCard, sha256_bytes  # noqa: E402
from bettor_alpha_lab.models import MarketResidualLogistic       # noqa: E402
from bettor_alpha_lab.validation import (                        # noqa: E402
    chronological_walkforward, deflated_sharpe_ratio,
    probability_of_backtest_overfitting, sharpe_ratio)

VERSION = "BETTOR_ALPHA_PROOF_RECEIPT_V1"
AUTHORITY = "RESEARCH_SHADOW_ONLY_NO_ORDER_NO_CAPITAL_AUTHORITY"
#: the chronologically last share of the data, never used to fit, calibrate
#: or select anything: reported once, separately
HOLDOUT_SHARE = 0.20
MIN_SEGMENT_N = 200           # the package's min_segment_calibration_n
MIN_SEGMENT_MARKETS = 20      # rows inside one market are not independent
MAKER_WINDOW_S = 900          # the extract's next-15-minute book path
FEE_QTY = 100                 # fee per contract is quoted at this size
MAX_ECE, MAX_PBO, MIN_DSR = 0.05, 0.50, 0.95

STATUS_CASH = "CASH"
STATUS_SHADOW = "SHADOW_ONLY"
STATUS_RESEARCH = "RESEARCH_ONLY"


# ── fees: BETTOR's own published-schedule implementation (pure stdlib) ─────
def _fees_module():
    root = LAB.parents[1] / "backend"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from sportsassets import calibration_fees as CF      # pure: decimal only
    return CF


def fee_per_contract(price: float, role: str, at_iso: str | None):
    """(fee per contract at FEE_QTY, provenance) from calibration_fees.
    Positive = charge (taker), negative = rebate (maker). None when the
    schedule refuses (UNMEASURED, never zero)."""
    CF = _fees_module()
    r = CF.ROLE_TAKER if role == "TAKER" else CF.ROLE_MAKER
    got = CF.expected_fee(float(price), FEE_QTY, role=r, at=at_iso) or {}
    if got.get("BLOCKER") or got.get("FEE") is None:
        return None, got.get("BLOCKER") or "NO_FEE_STATED"
    return float(got["FEE"]) / FEE_QTY, "calibration_fees.expected_fee(%s, qty=%d, %s)" % (
        role, FEE_QTY, got.get("schedule"))


# ── dataset ─────────────────────────────────────────────────────────────
def parse_extract_log(text: str) -> list[dict]:
    """The research-sql log -> rows (one JSON object per line)."""
    rows = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith('{"id"'):
            rows.append(json.loads(s))
    return rows


def dataset_sha(rows: list[dict]) -> str:
    return sha256_bytes("\n".join(json.dumps(r, sort_keys=True)
                                  for r in rows).encode())


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def family_of(r: dict) -> str:
    st = (r.get("sports_type") or "").lower()
    slug = r.get("slug") or ""
    if "total" in st or slug.startswith("tsc-") or "-total-" in slug:
        return "TOTAL"
    if "spread" in st or "handicap" in st or slug.startswith("asc-"):
        return "SPREAD"
    if st or slug.startswith(("aec-", "atc-")):
        return "MONEYLINE" if not slug.startswith("atc-") else "MONEYLINE_3WAY"
    return "UNKNOWN"


def sport_of(r: dict) -> str:
    lg = (r.get("league") or "").lower()
    slug = r.get("slug") or ""
    parts = slug.split("-")
    code = parts[1] if len(parts) > 1 else ""
    for k, v in (("nfl", "FOOTBALL"), ("cfb", "FOOTBALL"), ("ncaaf", "FOOTBALL"),
                 ("mlb", "BASEBALL"), ("npb", "BASEBALL"), ("kbo", "BASEBALL"),
                 ("nhl", "HOCKEY"), ("nba", "BASKETBALL"), ("wnba", "BASKETBALL")):
        if code == k or lg == k:
            return v
    return "SOCCER" if slug.startswith("atc-") else (code.upper() or "UNKNOWN")


def regime_of(r: dict) -> str:
    at, start = _f(r.get("at")), _f(r.get("start"))
    if start is None or at is None:
        return "START_UNKNOWN"
    h = (start - at) / 3600.0
    if h < 0:
        return "IN_PLAY"
    return "PREGAME_LT_6H" if h < 6 else "PREGAME_GE_6H"


def orient(r: dict) -> dict | None:
    """The bought side's view: y, bid/ask/mid, closing mid, maker fill."""
    bid, ask = _f(r.get("bid")), _f(r.get("ask"))
    y_long = _f(r.get("y_long"))
    if bid is None or ask is None or y_long is None or not (0 < bid < ask < 1):
        return None
    short = r.get("side") == "SHORT"
    o = {"y": (1.0 - y_long) if short else y_long,
         "bid": (1 - ask) if short else bid, "ask": (1 - bid) if short else ask,
         "top_qty": _f(r.get("bid_q") if short else r.get("ask_q"))}
    o["mid"] = (o["bid"] + o["ask"]) / 2.0
    o["spread"] = o["ask"] - o["bid"]
    cb, ca = _f(r.get("close_bid")), _f(r.get("close_ask"))
    o["close_mid"] = None
    if cb is not None and ca is not None and 0 < cb < ca < 1:
        o["close_mid"] = 1 - (cb + ca) / 2.0 if short else (cb + ca) / 2.0
    # a resting buy at the side's bid filled within MAKER_WINDOW_S if the side's
    # ask came down to it (LONG: yes-ask <= bid; SHORT: yes-bid >= yes-ask_0)
    mn, mx, n = _f(r.get("n15_min_ask")), _f(r.get("n15_max_bid")), r.get("n15_obs") or 0
    if not n:
        o["maker_filled"] = None
    elif short:
        o["maker_filled"] = mx is not None and mx >= ask
    else:
        o["maker_filled"] = mn is not None and mn <= bid
    return o


def build_frame(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        o = orient(r)
        if o is None:
            continue
        p_pin = _f(r.get("p_pin"))
        if p_pin is None or not 0 < p_pin < 1:
            continue
        p_model = _f(r.get("p_blend")) or _f(r.get("p_int")) or p_pin
        at = _f(r.get("at"))
        start = _f(r.get("start"))
        out.append(dict(
            o, id=r["id"], at=at, slug=r["slug"], side=r.get("side"),
            strategy=r.get("strategy"), verdict=r.get("verdict"),
            p_pin=p_pin, p_model=clamp01(p_model, 1e-6),
            p_int=_f(r.get("p_int")),
            sport=sport_of(r), family=family_of(r), regime=regime_of(r),
            hours_to_start=None if start is None or at is None
            else (start - at) / 3600.0,
            event=(r["slug"].rsplit("-", 1)[0] if r.get("slug") else None),
            at_iso=None if at is None else _dt.datetime.fromtimestamp(
                at, _dt.timezone.utc).date().isoformat()))
    out.sort(key=lambda d: (d["at"], d["id"]))
    return out


# ── statistics helpers ─────────────────────────────────────────────────
def cluster_mean_ci(values, clusters, z=1.96):
    """Mean with a cluster-robust (by market) standard error."""
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return {"n": 0, "markets": 0, "mean": None, "se": None, "lower": None}
    by = defaultdict(list)
    for x, c in zip(v, clusters):
        by[c].append(x)
    g = len(by)
    mu = float(v.mean())
    if g < 2:
        return {"n": len(v), "markets": g, "mean": mu, "se": None, "lower": None}
    resid = np.array([np.sum(np.asarray(xs) - mu) for xs in by.values()])
    se = float(math.sqrt(g / (g - 1) * np.sum(resid ** 2)) / len(v))
    return {"n": int(len(v)), "markets": g, "mean": round(mu, 6),
            "se": round(se, 6), "lower": round(mu - z * se, 6)}


def segment_key(d):
    return "%s|%s|%s" % (d["sport"], d["family"], d["regime"])


# ── the receipt ────────────────────────────────────────────────────────
def build_receipt(rows: list[dict], *, code_sha: str, data_sha: str,
                  generated_at: float, source: dict) -> dict:
    df = build_frame(rows)
    n_all = len(df)
    rec = {"version": VERSION, "authority": AUTHORITY,
           "generated_at": generated_at, "source": source,
           "rows_extracted": len(rows), "rows_usable": n_all,
           "markets_usable": len({d["slug"] for d in df}),
           "small_live": "SHADOW", "historical_paper_mutation": "NONE"}
    if n_all < 50:
        rec.update(verdict=STATUS_CASH, why="INSUFFICIENT_LABELLED_DATA")
        return rec
    cut = int(n_all * (1 - HOLDOUT_SHARE))
    dev, hold = df[:cut], df[cut:]
    rec["split"] = {"development_rows": len(dev), "holdout_rows": len(hold),
                    "holdout_from": hold[0]["at"] if hold else None,
                    "holdout_rule": "chronologically last %d%%, never fitted, "
                    "calibrated or selected on" % int(HOLDOUT_SHARE * 100)}

    # ── walk-forward over the development set ──────────────────────────
    train_min = max(100, len(dev) // 5)
    test_size = max(50, len(dev) // 10)
    folds = chronological_walkforward(len(dev), train_min, test_size)
    oos = []
    for k, fold in enumerate(folds):
        tr = [dev[i] for i in fold.train_idx]
        te = [dev[i] for i in fold.test_idx]
        oos.extend(_score_fold(tr, te, k))
    rec["walk_forward"] = {"folds": len(folds), "train_min": train_min,
                           "test_size": test_size, "oos_rows": len(oos)}
    hold_scored = _score_fold(dev, hold, "HOLDOUT") if hold else []

    rec["1_market_prior_vs_residual"] = _prior_vs_residual(oos, hold_scored)
    rec["2_calibration_raw_vs_calibrated"] = _calibration(oos, hold_scored)
    rules = _rules()
    trades = {name: [t for t in (rule(d) for d in oos) if t]
              for name, rule in rules.items()}
    rec["3_executable_ev_components"] = _ev_components(oos)
    rec["4_maker_taker_wait"] = _maker_taker_wait(oos)
    rec["5_clv"] = _clv(oos)
    pbo, dsr = _pbo_dsr(oos, trades)
    rec["6_pbo_multiple_testing"] = pbo
    rec["7_deflated_sharpe"] = dsr
    rec["8_capacity_frontier"] = _capacity(trades)
    rec["9_correlation_aware_sizing"] = _sizing(trades)
    rec["10_expected_vs_realized"] = _residuals(trades)
    hold_trades = {name: [t for t in (rule(d) for d in hold_scored) if t]
                   for name, rule in rules.items()}
    rec["12_holdout_and_forward_shadow"] = {
        "holdout": {name: cluster_mean_ci([t["realized"] for t in ts],
                                          [t["slug"] for t in ts])
                    for name, ts in hold_trades.items()},
        "forward_shadow": {
            "status": "ACCUMULATING", "settled_decisions_after_receipt": 0,
            "registered_from": generated_at,
            "rule": "decisions after `registered_from` are scored by the next "
                    "receipt against this receipt's frozen rules and model "
                    "card; nothing before it may be re-used as forward"}}
    rec["by_segment"] = _segments(oos, trades)
    rec["strategies"] = _verdicts(rec, trades, hold_trades)
    eligible = [s for s, v in rec["strategies"].items() if v["eligible"]]
    rec["verdict"] = STATUS_SHADOW if eligible else STATUS_CASH
    rec["verdict_rule"] = (
        "no strategy is ACTIVE_CHAMPION without positive settled FORWARD net "
        "$/capital-hour (none exists yet); a strategy is eligible only with a "
        "positive cluster-robust lower bound on walk-forward AND holdout net "
        "EV, ECE <= %.2f, PBO <= %.2f, DSR >= %.2f and no UNMEASURED cost. "
        "No eligible positive strategy => CASH. Least negative is never "
        "promoted. Fixed turnover is a ceiling, never a requirement."
        % (MAX_ECE, MAX_PBO, MIN_DSR))
    rec["11_model_card"] = _model_card(rec, code_sha=code_sha,
                                       data_sha=data_sha)
    return rec


def _score_fold(train, test, fold):
    """Fit residual model + calibrators on `train` only; score `test`."""
    if len(train) < 50 or not test:
        return []
    y_tr = np.array([d["y"] for d in train])
    if len(set(y_tr)) < 2:
        return []

    def feats(ds):
        return np.array([[logit(d["p_pin"]) - logit(d["mid"]),
                          (logit(d["p_model"]) - logit(d["mid"])),
                          min(48.0, max(-6.0, d["hours_to_start"] or 0.0)) / 48.0]
                         for d in ds])
    m_tr = np.array([d["mid"] for d in train])
    res = MarketResidualLogistic().fit(m_tr, feats(train), y_tr)
    p_res_tr = res.predict_proba(m_tr, feats(train))
    raw_tr = np.array([d["p_model"] for d in train])
    beta_raw = BetaCalibrator().fit(raw_tr, y_tr)
    iso_raw = IsotonicCalibrator().fit(raw_tr, y_tr)
    beta_res = BetaCalibrator().fit(p_res_tr, y_tr)
    m_te = np.array([d["mid"] for d in test])
    p_res = res.predict_proba(m_te, feats(test))
    raw_te = np.array([d["p_model"] for d in test])
    p_beta = beta_raw.predict(raw_te)
    p_iso = iso_raw.predict(raw_te)
    p_res_cal = beta_res.predict(p_res)
    out = []
    for j, d in enumerate(test):
        out.append(dict(d, fold=fold, p_market=d["mid"], p_raw=d["p_model"],
                        p_beta=float(p_beta[j]), p_iso=float(p_iso[j]),
                        p_residual=float(p_res[j]),
                        p_residual_cal=float(p_res_cal[j])))
    return out


PROB_STREAMS = ("p_market", "p_pin", "p_raw", "p_beta", "p_iso",
                "p_residual", "p_residual_cal")


def _metrics(ds, col):
    ds = [d for d in ds if d.get(col) is not None]
    if len(ds) < 30 or len({d["y"] for d in ds}) < 2:
        return {"n": len(ds), "status": "UNMEASURED_TOO_FEW_ROWS"}
    m = probability_metrics([d["y"] for d in ds], [d[col] for d in ds])
    m["markets"] = len({d["slug"] for d in ds})
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in m.items()}


def _prior_vs_residual(oos, hold):
    return {"walk_forward": {c: _metrics(oos, c) for c in PROB_STREAMS},
            "holdout": {c: _metrics(hold, c) for c in PROB_STREAMS},
            "definitions": {
                "p_market": "venue mid at the decision (the market prior)",
                "p_pin": "de-vigged Pinnacle recorded on the decision",
                "p_raw": "the strategy's own recorded probability (blend / "
                         "internal / Pinnacle)",
                "p_residual": "MarketResidualLogistic: logit(mid) + "
                              "regularised residual on (pin - mid, model - mid, "
                              "time to start), fitted on prior folds only",
                "p_residual_cal": "p_residual, beta-calibrated on prior folds"}}


def _calibration(oos, hold):
    segs = defaultdict(list)
    for d in oos:
        segs[segment_key(d)].append(d)
    by = {}
    for k, ds in sorted(segs.items()):
        markets = len({d["slug"] for d in ds})
        if len(ds) < MIN_SEGMENT_N or markets < MIN_SEGMENT_MARKETS:
            by[k] = {"n": len(ds), "markets": markets,
                     "status": "UNMEASURED_FALLS_BACK_TO_POOLED"}
            continue
        by[k] = {c: _metrics(ds, c) for c in ("p_raw", "p_beta", "p_iso")}
    return {"pooled_walk_forward": {c: _metrics(oos, c)
                                    for c in ("p_raw", "p_beta", "p_iso",
                                              "p_residual", "p_residual_cal")},
            "pooled_holdout": {c: _metrics(hold, c)
                               for c in ("p_raw", "p_beta", "p_iso",
                                         "p_residual_cal")},
            "by_sport_family_regime": by,
            "segment_rule": "a segment is measured with >= %d rows AND >= %d "
                            "markets; otherwise UNMEASURED and pooled"
                            % (MIN_SEGMENT_N, MIN_SEGMENT_MARKETS)}


def _taker(d, p):
    fee, prov = fee_per_contract(d["ask"], "TAKER", d["at_iso"])
    if fee is None:
        return None
    ev = binary_taker_ev(p, d["ask"], CostBreakdown(fees=fee))
    return {"fee": fee, "fee_basis": prov, "ev": ev.net_ev_per_contract,
            "gross": ev.gross_edge,
            "realized": d["y"] - d["ask"] - fee}


def _rules():
    """The candidate strategies evaluated (every one counts as a trial for
    the multiple-testing adjustments). Each buys the side at the ask when its
    probability's net EV after the scheduled taker fee is positive."""
    out = {}
    for col in ("p_pin", "p_raw", "p_beta", "p_residual_cal"):
        for thr in (0.0, 0.02):
            def rule(d, col=col, thr=thr):
                p = d.get(col)
                if p is None:
                    return None
                t = _taker(d, p)
                if t is None or t["ev"] <= thr:
                    return None
                return dict(t, slug=d["slug"], event=d["event"], at=d["at"],
                            p=p, y=d["y"], ask=d["ask"], segment=segment_key(d),
                            top_qty=d["top_qty"], fold=d["fold"])
            out["TAKER_%s_EV_GT_%s" % (col.upper(), thr)] = rule
    return out


def _ev_components(oos):
    comps = defaultdict(list)
    for d in oos:
        t = _taker(d, d["p_residual_cal"])
        if t is None:
            comps["fee_unmeasured"].append(1)
            continue
        comps["gross_edge_vs_ask"].append(t["gross"])
        comps["fees"].append(t["fee"])
        comps["half_spread"].append(d["spread"] / 2.0)
        comps["net_ev"].append(t["ev"])
    return {
        "probability": "p_residual_cal (walk-forward, prior folds only)",
        "per_contract_means": {k: round(float(np.mean(v)), 6)
                               for k, v in comps.items() if v and k != "fee_unmeasured"},
        "fee_unmeasured_rows": len(comps["fee_unmeasured"]),
        "slippage": "UNMEASURED beyond the top level (the extract carries "
                    "top-of-book quantity only); capacity is capped at the top "
                    "level instead",
        "adverse_selection_taker": "not applicable to an immediate fill; "
                                   "the maker path measures it (section 4)",
        "management_cost": "UNMEASURED in this receipt (migration 311's learned "
                           "management cost is not joined yet) -- therefore no "
                           "strategy can be eligible on this receipt",
        "correlation_charge": "applied in sizing (section 9), not in EV",
        "rebate": "maker only, and only when filled (section 4)"}


def _maker_taker_wait(oos):
    filled = [d for d in oos if d["maker_filled"] is True]
    seen = [d for d in oos if d["maker_filled"] is not None]
    if len(seen) < 30:
        return {"status": "UNMEASURED_TOO_FEW_BOOK_PATHS", "n": len(seen)}
    pf = len(filled) / len(seen)
    y_all = float(np.mean([d["y"] for d in seen]))
    y_f = float(np.mean([d["y"] for d in filled])) if filled else None
    p_all = float(np.mean([d["p_residual_cal"] for d in seen]))
    p_f = float(np.mean([d["p_residual_cal"] for d in filled])) if filled else None
    adverse = None if not filled else (p_f - y_f) - (p_all - y_all)
    maker = []
    for d in seen:
        reb, prov = fee_per_contract(d["bid"], "MAKER", d["at_iso"])
        if reb is None:
            continue
        costs = CostBreakdown(fees=0.0, adverse_selection=max(0.0, adverse or 0.0),
                              rebate=-reb if reb < 0 else 0.0)
        ev = binary_maker_ev(d["p_residual_cal"], d["bid"], pf, costs)
        maker.append({"exp": ev.expected_profit_per_posted_contract,
                      "realized": (d["y"] - d["bid"] - reb) if d["maker_filled"] else 0.0,
                      "slug": d["slug"]})
    taker = [(t, d["slug"]) for t, d in
             ((_taker(d, d["p_residual_cal"]), d) for d in seen) if t]
    return {
        "n_book_paths": len(seen), "window_s": MAKER_WINDOW_S,
        "fill_probability_at_bid": round(pf, 4),
        "conditional_adverse_selection": None if adverse is None else round(adverse, 6),
        "adverse_selection_definition": "(mean p - mean y | filled) - (mean p - "
                                        "mean y | all): how much worse the "
                                        "model is exactly when a resting bid fills",
        "maker_expected_per_posted": cluster_mean_ci([m["exp"] for m in maker],
                                                     [m["slug"] for m in maker]),
        "maker_realized_per_posted": cluster_mean_ci([m["realized"] for m in maker],
                                                     [m["slug"] for m in maker]),
        "taker_expected_all": cluster_mean_ci([t["ev"] for t, _ in taker],
                                              [s_ for _, s_ in taker]),
        "taker_realized_all": cluster_mean_ci([t["realized"] for t, _ in taker],
                                              [s_ for _, s_ in taker]),
        "wait": {"expected": 0.0, "realized": 0.0,
                 "note": "CASH_WAIT is the zero line every path is judged against"},
        "fill_model": "a resting buy at the bought side's bid is FILLED when the "
                      "side's best ask reached it within the window (book "
                      "snapshots, not trades: an approximation, labelled)"}


def _clv(oos):
    vals = [(d["close_mid"] - d["ask"], d["slug"]) for d in oos
            if d.get("close_mid") is not None and d["regime"] != "IN_PLAY"]
    if len(vals) < 30:
        return {"status": "UNMEASURED_TOO_FEW_CLOSING_BOOKS", "n": len(vals)}
    v = np.array([x for x, _ in vals])
    return {"definition": "closing venue mid (last book before the start) - "
                          "the ask paid, bought side, pregame decisions",
            "all_decisions": cluster_mean_ci(v, [s for _, s in vals]),
            "quantiles": {q: round(float(np.quantile(v, q)), 4)
                          for q in (0.05, 0.25, 0.5, 0.75, 0.95)},
            "share_positive": round(float((v > 0).mean()), 4)}


def _pbo_dsr(oos, trades):
    names = sorted(trades)
    blocks = 8
    if not oos:
        return ({"status": "UNMEASURED"}, {"status": "UNMEASURED"})
    t0, t1 = oos[0]["at"], oos[-1]["at"]
    edges = np.linspace(t0, t1 + 1e-6, blocks + 1)
    mat = np.zeros((blocks, len(names)))
    for j, n in enumerate(names):
        for t in trades[n]:
            b = min(blocks - 1, int(np.searchsorted(edges, t["at"], "right") - 1))
            mat[b, j] += t["realized"]
    try:
        pbo = probability_of_backtest_overfitting(mat)
    except ValueError as exc:
        pbo = None
        why = str(exc)
    out_pbo = {"trials": len(names), "time_blocks": blocks,
               "pbo": None if pbo is None else round(pbo, 4),
               "threshold": MAX_PBO,
               "status": "MEASURED" if pbo is not None else "UNMEASURED:" + why,
               "per_block_net_by_trial": {n: [round(x, 4) for x in mat[:, j]]
                                          for j, n in enumerate(names)}}
    dsr = {}
    for n in names:
        r = [t["realized"] for t in trades[n]]
        if len(r) < 3:
            dsr[n] = {"n": len(r), "status": "UNMEASURED"}
            continue
        sr = sharpe_ratio(r)
        a = np.asarray(r)
        sk = float(((a - a.mean()) ** 3).mean() / (a.std() ** 3 + 1e-12))
        ku = float(((a - a.mean()) ** 4).mean() / (a.std() ** 4 + 1e-12)) - 3
        dsr[n] = {"n": len(r), "sharpe_per_trade": round(sr, 4),
                  "dsr_probability": round(deflated_sharpe_ratio(
                      sr, len(r), len(names), sk, ku), 4)}
    return out_pbo, {"trials": len(names), "threshold": MIN_DSR, "by_trial": dsr,
                     "note": "per-trade Sharpe on correlated trades (several "
                             "per market) overstates n; read with the "
                             "cluster-robust bounds"}


def _capacity(trades):
    caps = (10, 50, 100, 500, 1000, 5000)
    out = {}
    for n, ts in trades.items():
        row = {}
        for c in caps:
            exp = sum(min(c, t["top_qty"] or 0.0) * t["ev"] for t in ts)
            real = sum(min(c, t["top_qty"] or 0.0) * t["realized"] for t in ts)
            row[str(c)] = {"expected_usd": round(exp, 2),
                           "realized_usd": round(real, 2)}
        out[n] = row
    return {"by_trial_contracts_cap": out,
            "basis": "top-of-book quantity only (deeper levels are not in this "
                     "extract): a ceiling, never a turnover target"}


def _sizing(trades):
    out = {}
    for n, ts in trades.items():
        by_event = defaultdict(int)
        for t in ts:
            by_event[(t["event"], int(t["at"] // 3600))] += 1
        stakes = []
        for t in ts:
            k = by_event[(t["event"], int(t["at"] // 3600))]
            a = allocate_binary(100_000.0, t["p"], t["ask"],
                                standard_error=0.03,
                                correlation_haircut=1.0 / math.sqrt(k),
                                capacity_haircut=1.0, max_fraction=0.02,
                                all_in_cost_per_contract=t["fee"])
            stakes.append((a.stake_usd, a.reason))
        alloc = [s for s, r in stakes if s > 0]
        out[n] = {"candidates": len(ts), "allocated": len(alloc),
                  "cash_wait": len(ts) - len(alloc),
                  "mean_stake_usd_on_100k": round(float(np.mean(alloc)), 2) if alloc else 0.0}
    return {"by_trial": out,
            "rule": "fractional Kelly 0.25 on a conservative probability (p - "
                    "1 x 0.03), haircut 1/sqrt(k) for k same-event same-hour "
                    "candidates, capped at 2%% of an illustrative $100k; zero "
                    "conservative edge => CASH_WAIT. Research only: no limit "
                    "or sizing in BETTOR is changed."}


def _residuals(trades):
    out = {}
    for n, ts in trades.items():
        if not ts:
            out[n] = {"n": 0}
            continue
        exp = np.array([t["ev"] for t in ts])
        real = np.array([t["realized"] for t in ts])
        cal = np.array([t["y"] - t["p"] for t in ts])
        out[n] = {"n": len(ts), "markets": len({t["slug"] for t in ts}),
                  "expected_sum": round(float(exp.sum()), 4),
                  "realized_sum": round(float(real.sum()), 4),
                  "residual_sum": round(float((real - exp).sum()), 4),
                  "attribution": {
                      "probability_error_sum": round(float(cal.sum()), 4),
                      "note": "realized - expected = (y - p) per contract: "
                              "the whole residual is probability error, fees "
                              "and fill price being the same on both sides"},
                  "realized_cluster_ci": cluster_mean_ci(real, [t["slug"] for t in ts])}
    return out


def _segments(oos, trades):
    segs = defaultdict(list)
    for d in oos:
        segs[segment_key(d)].append(d)
    out = {}
    best = "TAKER_P_RESIDUAL_CAL_EV_GT_0.0"
    for k, ds in sorted(segs.items()):
        ts = [t for t in trades.get(best, []) if t["segment"] == k]
        out[k] = {"rows": len(ds), "markets": len({d["slug"] for d in ds}),
                  "brier_market": _metrics(ds, "p_market").get("brier"),
                  "brier_residual_cal": _metrics(ds, "p_residual_cal").get("brier"),
                  "trades_%s" % best: cluster_mean_ci([t["realized"] for t in ts],
                                                      [t["slug"] for t in ts])}
    return out


def _verdicts(rec, trades, hold_trades):
    pbo = rec["6_pbo_multiple_testing"].get("pbo")
    dsr = rec["7_deflated_sharpe"].get("by_trial", {})
    ece = rec["2_calibration_raw_vs_calibrated"]["pooled_walk_forward"]
    out = {}
    for n, ts in trades.items():
        wf = cluster_mean_ci([t["realized"] for t in ts], [t["slug"] for t in ts])
        ho = cluster_mean_ci([t["realized"] for t in hold_trades.get(n, [])],
                             [t["slug"] for t in hold_trades.get(n, [])])
        col = n.split("TAKER_")[1].split("_EV_GT")[0].lower()
        e = (ece.get(col) or {}).get("ece_10")
        reasons = ["MANAGEMENT_COST_UNMEASURED", "NO_FORWARD_SHADOW_EVIDENCE"]
        if not ts:
            reasons.append("NO_TRADES")
        if wf["lower"] is None or wf["lower"] <= 0:
            reasons.append("WALK_FORWARD_LOWER_BOUND_NOT_POSITIVE")
        if ho["lower"] is None or ho["lower"] <= 0:
            reasons.append("HOLDOUT_LOWER_BOUND_NOT_POSITIVE")
        if e is None or e > MAX_ECE:
            reasons.append("CALIBRATION_ERROR_UNMEASURED_OR_TOO_HIGH")
        if pbo is None or pbo > MAX_PBO:
            reasons.append("PBO_UNMEASURED_OR_TOO_HIGH")
        d = (dsr.get(n) or {}).get("dsr_probability")
        if d is None or d < MIN_DSR:
            reasons.append("DEFLATED_SHARPE_UNMEASURED_OR_BELOW_THRESHOLD")
        out[n] = {"walk_forward_realized": wf, "holdout_realized": ho,
                  "eligible": not reasons,
                  "status": STATUS_SHADOW if not reasons else STATUS_RESEARCH,
                  "reasons": reasons}
    return out


def _model_card(rec, *, code_sha, data_sha):
    card = ModelCard(
        model_name="BETTOR_MARKET_RESIDUAL_BETA_V1",
        version=VERSION, code_sha=code_sha, data_sha=data_sha,
        train_end=str(rec["split"]["holdout_from"]),
        feature_schema_sha=sha256_bytes(b"pin-mid|model-mid|hours_to_start/48"),
        hyperparameters={"residual_C": 0.1, "beta_C": 100.0,
                         "holdout_share": HOLDOUT_SHARE,
                         "walk_forward": rec["walk_forward"]},
        calibration={k: rec["2_calibration_raw_vs_calibrated"]["pooled_walk_forward"].get(k)
                     for k in ("p_raw", "p_beta", "p_residual_cal")},
        validation={"verdict": rec["verdict"],
                    "pbo": rec["6_pbo_multiple_testing"].get("pbo")},
        limitations=[
            "labels exist only for markets with a settled paper position or a "
            "0/1 valuation outcome: a selected sample, not the universe",
            "rows within one market are correlated; bounds are cluster-robust "
            "by market",
            "book snapshots, not trades; maker fills are approximated",
            "management cost and deep-book slippage are UNMEASURED",
            "no forward SHADOW sample yet"],
        capital_status=rec["verdict"])
    fp = card.fingerprint()
    return {"card": json.loads(card.canonical_json()), "model_card_sha256": fp,
            "signature": "sha256(canonical model card) -- content-addressed; "
                         "no secret key is used or stored"}


def code_sha(paths) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(str(p.relative_to(LAB)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()
