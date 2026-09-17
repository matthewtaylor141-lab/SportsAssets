"""Section 22. Calibration and orthogonality, strictly nested.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

THE DEFECT THIS EXISTS TO PREVENT
---------------------------------
A calibrator is fitted with outcomes. If it is fitted on the same observations
the conditional-market test is then scored on, those outcomes have entered the
challenger's probabilities before the test asks whether the challenger knows
anything the market does not. The test can then report incremental information
that is really the calibrator remembering the answer.

The failure is quiet. A calibrated probability looks like a forecast; nothing
about the number says which outcomes shaped it. So the protection has to be
structural, not a matter of remembering to be careful.

THE ORDER, AND IT IS NOT NEGOTIABLE
-----------------------------------
    TRAIN MODEL            on window W0
      -> FIT CALIBRATOR    on window W1, strictly after W0, strictly before W2
      -> FIT/SELECT STACK  on window W2, strictly before W3
      -> TEST              on window W3, later unseen events

Every probability consumed by the conditional-market test is generated without
using that test's outcomes. Each window is disjoint from every later one, and
`leak_check` proves it rather than asserting it.

METHOD SELECTION IS PART OF FITTING
-----------------------------------
Choosing Platt over isotonic BY LOOKING AT THE TEST is the same leak as
fitting on the test, laundered through a comparison. The choice is made inside
W1 and then frozen. `SELECTED_ON` records where, so a later reader can check.

WHERE W1 COMES FROM
-------------------
The natural temptation is to carve the calibration window out of the
evaluation events, which are scarce. That is allowed but expensive: it shrinks
the test window, and the test window is already the binding constraint.

The alternative used here is a calibration window drawn from EXTERNAL match
history held out of the model fit -- matches the model never trained on, whose
contracts are priced the same way, and none of which is an evaluation event.
That keeps every evaluation event available for W2 and W3. It carries one
assumption, recorded rather than buried: the calibration population is
external-league fixtures while the test population is venue contracts, so a
calibrator fitted on the first is being transported to the second.
POPULATION_TRANSPORT_ASSUMED is set whenever that path is taken.
"""

import math
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

WINDOW_ORDER = ("TRAIN", "CALIBRATION", "DEVELOPMENT", "TEST")

CALIBRATOR_MAY_NOT_SEE = ("DEVELOPMENT_OUTCOMES", "TEST_OUTCOMES")
STACK_MAY_NOT_SEE = ("TEST_OUTCOMES",)

METHOD_SELECTION_IS_PART_OF_FITTING = True

WHY_NESTING = (
    "a calibrator is fitted with outcomes; if it sees the test outcomes, the "
    "orthogonality experiment can report the calibrator's memory as the "
    "challenger's information")

CALIBRATION_METHODS = ("IDENTITY", "PLATT", "BETA", "TEMPERATURE", "ISOTONIC")

ISOTONIC_MIN_EVENTS = 200


def _clip(p, eps=1e-6):
    return min(max(float(p), eps), 1.0 - eps)


def _logit(p):
    p = _clip(p)
    return math.log(p / (1.0 - p))


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _log_loss_pairs(pairs):
    if not pairs:
        return None
    s = 0.0
    for p, y in pairs:
        p = _clip(p)
        s -= math.log(p) if y else math.log(1.0 - p)
    return s / len(pairs)


# ---------------------------------------------------------------------------
# Windows, and the proof that they are nested.
# ---------------------------------------------------------------------------

def leak_check(windows):
    """Every window must be disjoint from every later one, by EVENT.

    Rows are not the unit. Two contracts on the same fixture share a
    scoreline, so putting one in the calibration window and the other in the
    test window leaks the outcome just as surely as reusing the row.
    """
    keys = {name: set(evs) for name, evs in windows.items()}
    violations = []
    order = [w for w in WINDOW_ORDER if w in keys]
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            shared = keys[a] & keys[b]
            if shared:
                violations.append({
                    "EARLIER": a, "LATER": b,
                    "SHARED_EVENTS": len(shared),
                    "EXAMPLE": sorted(shared)[:3],
                })
    return {
        "WINDOWS": {k: len(v) for k, v in keys.items()},
        "VIOLATIONS": violations,
        "NESTING_STATUS": "CLEAN" if not violations else "LEAKED",
        "UNIT_OF_DISJOINTNESS": "EVENT_NOT_ROW",
    }


def chronological_windows(event_keys, fractions=(0.0, 0.0, 0.6, 0.4),
                          date_of=None):
    """Split evaluation events into windows in date order.

    `fractions` is (TRAIN, CALIBRATION, DEVELOPMENT, TEST). Zeros are allowed:
    a zero TRAIN/CALIBRATION means those windows are supplied from elsewhere
    (external history), which is the preferred path.
    """
    if abs(sum(fractions) - 1.0) > 1e-9:
        raise ValueError("window fractions must sum to 1")
    if date_of is None:
        def date_of(ev):
            parts = str(ev).split("-")
            for i in range(len(parts) - 2):
                if (len(parts[i]) == 4 and parts[i].isdigit()
                        and len(parts[i + 1]) == 2 and len(parts[i + 2]) == 2):
                    return "-".join(parts[i:i + 3])
            return str(ev)
    evs = sorted(set(event_keys), key=lambda e: (date_of(e), e))
    n = len(evs)
    out, start = {}, 0
    for name, f in zip(WINDOW_ORDER, fractions):
        take = int(round(f * n))
        out[name] = evs[start:start + take]
        start += take
    # any rounding remainder joins the test window, never an earlier one
    if start < n:
        out["TEST"] = list(out["TEST"]) + evs[start:]
    return out


# ---------------------------------------------------------------------------
# W1: fit AND select the calibrator, using only the calibration window.
# ---------------------------------------------------------------------------

def _fit_platt(pairs):
    import numpy as np
    X = np.asarray([[1.0, _logit(p)] for p, _ in pairs], float)
    y = np.asarray([float(v) for _, v in pairs], float)
    b = np.zeros(2)
    for _ in range(60):
        z = X @ b
        mu = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        w = np.clip(mu * (1 - mu), 1e-9, None)
        g = X.T @ (y - mu) - 1e-4 * b
        H = X.T @ (X * w[:, None]) + 1e-4 * np.eye(2)
        try:
            step = np.linalg.solve(H, g)
        except Exception:
            return None
        b = b + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return {"METHOD": "PLATT", "A": float(b[0]), "B": float(b[1])}


def _fit_temperature(pairs):
    best, arg = None, None
    for i in range(1, 601):
        t = i / 100.0
        ll = _log_loss_pairs([(_sigmoid(_logit(p) / t), y) for p, y in pairs])
        if ll is not None and (best is None or ll < best):
            best, arg = ll, t
    return {"METHOD": "TEMPERATURE", "T": arg} if arg else None


def _fit_beta(pairs):
    import numpy as np
    X = np.asarray([[1.0, math.log(_clip(p)), -math.log(1 - _clip(p))]
                    for p, _ in pairs], float)
    y = np.asarray([float(v) for _, v in pairs], float)
    b = np.zeros(3)
    for _ in range(60):
        z = X @ b
        mu = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        w = np.clip(mu * (1 - mu), 1e-9, None)
        g = X.T @ (y - mu) - 1e-4 * b
        H = X.T @ (X * w[:, None]) + 1e-4 * np.eye(3)
        try:
            step = np.linalg.solve(H, g)
        except Exception:
            return None
        b = b + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return {"METHOD": "BETA", "A": float(b[0]), "B": float(b[1]),
            "C": float(b[2])}


def _fit_isotonic(pairs, n_events):
    if n_events is not None and n_events < ISOTONIC_MIN_EVENTS:
        return None
    pts = sorted((_clip(p), float(y)) for p, y in pairs)
    blocks = [[p, v, 1.0] for p, v in pts]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][1] > blocks[i + 1][1]:
            p0, v0, w0 = blocks[i]
            p1, v1, w1 = blocks[i + 1]
            blocks[i:i + 2] = [[p1, (v0 * w0 + v1 * w1) / (w0 + w1), w0 + w1]]
            i = max(i - 1, 0)
        else:
            i += 1
    return {"METHOD": "ISOTONIC",
            "KNOTS": [(b[0], b[1]) for b in blocks]}


def apply_calibrator(cal, p):
    if cal is None or cal.get("METHOD") == "IDENTITY":
        return _clip(p)
    m = cal["METHOD"]
    if m == "PLATT":
        return _sigmoid(cal["A"] + cal["B"] * _logit(p))
    if m == "TEMPERATURE":
        return _sigmoid(_logit(p) / cal["T"])
    if m == "BETA":
        return _sigmoid(cal["A"] + cal["B"] * math.log(_clip(p))
                        - cal["C"] * math.log(1 - _clip(p)))
    if m == "ISOTONIC":
        # Each knot is (upper edge of a PAVA block, block value). A query
        # belongs to the FIRST block whose upper edge is at or above it --
        # taking the last block at or BELOW it reads one block too low and
        # returns the neighbouring block's value at every boundary.
        knots = cal["KNOTS"]
        p = _clip(p)
        lo, hi = 0, len(knots) - 1
        if p <= knots[0][0]:
            return _clip(knots[0][1])
        if p >= knots[hi][0]:
            return _clip(knots[hi][1])
        while lo < hi:
            mid = (lo + hi) // 2
            if knots[mid][0] >= p:
                hi = mid
            else:
                lo = mid + 1
        return _clip(knots[lo][1])
    return _clip(p)


def _fit_one(method, pairs, n_events):
    if method == "IDENTITY":
        return {"METHOD": "IDENTITY"}
    if method == "PLATT":
        return _fit_platt(pairs)
    if method == "BETA":
        return _fit_beta(pairs)
    if method == "TEMPERATURE":
        return _fit_temperature(pairs)
    if method == "ISOTONIC":
        return _fit_isotonic(pairs, n_events)
    return None


def fit_and_select_calibrator(cal_pairs, n_cal_events=None,
                              methods=CALIBRATION_METHODS, folds=5):
    """Fit every candidate on W1 and pick the winner INSIDE W1, by k-fold CV.

    Two rules are in tension and both matter.

    The first is the one this module exists for: the winner may not be chosen
    by looking at a later window. Scoring candidates on the development or
    test events would borrow the answer.

    The second is that a plain in-sample choice inside W1 is not neutral
    either -- it systematically hands the prize to the most flexible
    candidate. Isotonic regression can fit a calibration window almost
    perfectly and generalise worse than doing nothing at all, so an in-sample
    comparison would select it every time.

    Cross-validating INSIDE W1 satisfies both: flexibility is charged for, and
    no outcome outside W1 is touched. The winner is then refitted on the whole
    of W1, because the fold models were only ever a device for choosing.
    """
    if not cal_pairs:
        return {"STATUS": "NO_CALIBRATION_DATA",
                "SELECTED": {"METHOD": "IDENTITY"}}
    n = len(cal_pairs)
    folds = max(2, min(folds, n // 20)) if n >= 40 else 0

    cv_scores, in_sample = {}, {}
    for m in methods:
        full = _fit_one(m, cal_pairs, n_cal_events)
        if full is None:
            cv_scores[m] = in_sample[m] = NOT_IDENTIFIED
            continue
        in_sample[m] = _log_loss_pairs(
            [(apply_calibrator(full, p), y) for p, y in cal_pairs])
        if not folds:
            cv_scores[m] = in_sample[m]
            continue
        held = []
        ok = True
        for k in range(folds):
            tr = [pr for i, pr in enumerate(cal_pairs) if i % folds != k]
            te = [pr for i, pr in enumerate(cal_pairs) if i % folds == k]
            sub = _fit_one(m, tr, (n_cal_events or n) * (folds - 1) // folds)
            if sub is None or not te:
                ok = False
                break
            held += [(apply_calibrator(sub, p), y) for p, y in te]
        cv_scores[m] = _log_loss_pairs(held) if ok and held else NOT_IDENTIFIED

    fitted = {m: _fit_one(m, cal_pairs, n_cal_events) for m in methods}
    usable = {m: s for m, s in cv_scores.items()
              if isinstance(s, float) and fitted.get(m) is not None}
    if not usable:
        return {"STATUS": "ALL_METHODS_FAILED",
                "SELECTED": {"METHOD": "IDENTITY"}, "CV_SCORES": cv_scores}
    best = min(usable, key=usable.get)
    return {
        "STATUS": "SELECTED",
        "SELECTED": fitted[best],
        "SELECTED_METHOD": best,
        "SELECTED_ON": "CALIBRATION_WINDOW_W1_KFOLD_CV",
        "CV_FOLDS": folds,
        "CV_SCORES": cv_scores,
        "IN_WINDOW_SCORES": in_sample,
        "IN_SAMPLE_WOULD_HAVE_PICKED": (
            min((m for m, s in in_sample.items()
                 if isinstance(s, float) and fitted.get(m) is not None),
                key=lambda m: in_sample[m])),
        "CALIBRATION_PAIRS": len(cal_pairs),
        "CALIBRATION_EVENTS": n_cal_events,
        "METHOD_SELECTION_IS_PART_OF_FITTING":
            METHOD_SELECTION_IS_PART_OF_FITTING,
        "CALIBRATION_DOES_NOT_CREATE_NEW_INFORMATION": True,
    }


# ---------------------------------------------------------------------------
# W2: the stack. W3: the test.
# ---------------------------------------------------------------------------

def _fit_logistic(X, y, l2=1e-3, iters=60):
    import numpy as np
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, k = X.shape
    if n < 8 * k:
        return None
    b = np.zeros(k)
    for _ in range(iters):
        z = np.clip(X @ b, -30, 30)
        mu = 1.0 / (1.0 + np.exp(-z))
        w = np.clip(mu * (1 - mu), 1e-9, None)
        g = X.T @ (y - mu) - l2 * b
        H = X.T @ (X * w[:, None]) + l2 * np.eye(k)
        try:
            step = np.linalg.solve(H, g)
        except Exception:
            return None
        b = b + step
        if np.max(np.abs(step)) < 1e-10:
            break
    return [float(v) for v in b]


def fit_stack(dev_rows, market="P_MARKET", challenger=None, ykey="Y"):
    """The market-only and market-plus-challenger blends, fitted on W2."""
    usable = [r for r in dev_rows
              if r.get(market) is not None and r.get(challenger) is not None
              and r.get(ykey) in (0, 1)]
    if len(usable) < 24:
        return {"STATUS": "TOO_FEW_DEVELOPMENT_ROWS", "ROWS": len(usable)}
    y = [r[ykey] for r in usable]
    ba = _fit_logistic([[1.0, _logit(r[market])] for r in usable], y)
    bab = _fit_logistic([[1.0, _logit(r[market]), _logit(r[challenger])]
                         for r in usable], y)
    if ba is None or bab is None:
        return {"STATUS": "FIT_FAILED"}
    return {"STATUS": "FITTED", "MARKET_ONLY": ba, "MARKET_PLUS": bab,
            "DEVELOPMENT_ROWS": len(usable),
            "FITTED_ON": "DEVELOPMENT_WINDOW_W2"}


def apply_stack(stack, row, market, challenger):
    ba, bab = stack["MARKET_ONLY"], stack["MARKET_PLUS"]
    lm = _logit(row[market])
    lc = _logit(row[challenger])
    return (_sigmoid(ba[0] + ba[1] * lm),
            _sigmoid(bab[0] + bab[1] * lm + bab[2] * lc))


def run_nested(rows, challenger, market="P_MARKET", ykey="Y",
               event_key="EVENT_KEY", windows=None, calibration_pairs=None,
               n_calibration_events=None, population_transport=False,
               draws=400):
    """The whole §22 protocol, end to end, with the leak check attached.

    `windows` maps window name -> event keys. `calibration_pairs` are (p, y)
    from W1; when they come from external history rather than evaluation
    events, pass population_transport=True so the assumption is recorded.
    """
    import ev_core_power as POW

    if windows is None:
        windows = chronological_windows({r[event_key] for r in rows})
    lc = leak_check(windows)
    if lc["NESTING_STATUS"] != "CLEAN":
        return {"STATUS": "REFUSED_NESTING_LEAKED", "LEAK_CHECK": lc}

    cal = fit_and_select_calibrator(calibration_pairs or [],
                                    n_calibration_events)
    calibrator = cal.get("SELECTED")

    dev_evs = set(windows.get("DEVELOPMENT", ()))
    test_evs = set(windows.get("TEST", ()))
    ckey = "_P_CHALLENGER_CALIBRATED"
    for r in rows:
        if r.get(challenger) is not None:
            r[ckey] = apply_calibrator(calibrator, r[challenger])

    dev = [r for r in rows if r.get(event_key) in dev_evs]
    test = [r for r in rows if r.get(event_key) in test_evs]
    stack = fit_stack(dev, market, ckey, ykey)
    if stack.get("STATUS") != "FITTED":
        return {"STATUS": "REFUSED_STACK_NOT_FITTED", "STACK": stack,
                "LEAK_CHECK": lc, "CALIBRATION": cal}

    scored = []
    for r in test:
        if (r.get(market) is None or r.get(ckey) is None
                or r.get(ykey) not in (0, 1)):
            continue
        pm, pb = apply_stack(stack, r, market, ckey)
        scored.append({event_key: r[event_key], ykey: r[ykey],
                       "_P_MKT_ONLY": pm, "_P_BLEND": pb})
    if len({s[event_key] for s in scored}) < 8:
        return {"STATUS": "REFUSED_TOO_FEW_TEST_EVENTS",
                "TEST_EVENTS": len({s[event_key] for s in scored}),
                "LEAK_CHECK": lc, "CALIBRATION": cal, "STACK": stack}

    diffs, meta = POW.paired_event_differences(
        scored, "_P_BLEND", "_P_MKT_ONLY", ykey=ykey, event_key=event_key)
    summ = POW.paired_summary(diffs, draws=draws)
    lo, hi = summ["CI95_EVENT_BOOTSTRAP"]
    adds = hi < 0  # negative D_EVENT means the blend scored better

    return {
        "STATUS": "MEASURED",
        "CHALLENGER": challenger,
        "PROTOCOL": "TRAIN -> CALIBRATE(W1) -> STACK(W2) -> TEST(W3)",
        "LEAK_CHECK": lc,
        "CALIBRATION": {k: v for k, v in cal.items() if k != "SELECTED"},
        "CALIBRATOR": calibrator,
        "POPULATION_TRANSPORT_ASSUMED": bool(population_transport),
        "POPULATION_TRANSPORT_NOTE": (
            "the calibrator was fitted on external-league fixtures and applied "
            "to venue contracts; that transport is an assumption, not a "
            "measurement" if population_transport else None),
        "STACK": stack,
        "TEST_EVENTS": summ["N_EVENTS"],
        "TEST_ROWS": meta["ROWS_PAIRED"],
        "MEAN_D_EVENT": summ["MEAN_D_EVENT"],
        "SD_D_EVENT": summ["SD_D_EVENT"],
        "CI95_EVENT_BOOTSTRAP": (lo, hi),
        "DELTA_LOG_LOSS": -summ["MEAN_D_EVENT"],
        "INCREMENTAL_SIGNAL_STATUS": (
            "DETECTED" if adds else "NOT_DETECTED_AT_THIS_SAMPLE_SIZE"),
        "EVERY_PROBABILITY_IN_THE_TEST_WAS_GENERATED_WITHOUT_TEST_OUTCOMES":
            True,
        "NOT_DETECTED_IS_NOT_ABSENT": (
            "an interval spanning zero says the effect was not detected in "
            "this sample, not that it is zero"),
    }


def describe():
    return {
        "WINDOW_ORDER": WINDOW_ORDER,
        "CALIBRATOR_MAY_NOT_SEE": CALIBRATOR_MAY_NOT_SEE,
        "STACK_MAY_NOT_SEE": STACK_MAY_NOT_SEE,
        "METHOD_SELECTION_IS_PART_OF_FITTING":
            METHOD_SELECTION_IS_PART_OF_FITTING,
        "WHY_NESTING": WHY_NESTING,
        "UNIT_OF_DISJOINTNESS": "EVENT_NOT_ROW",
        "CALIBRATION_METHODS": CALIBRATION_METHODS,
    }
