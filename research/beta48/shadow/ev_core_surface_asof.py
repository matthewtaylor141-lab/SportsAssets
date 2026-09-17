"""MARKET_SURFACE_V1_ASOF -- the coherence surface, fitted at one timestamp.

Directive sections 10 and 11.

WHAT V0 GOT WRONG
-----------------
`ev_core_surface` fits one latent score distribution to all of a fixture's
linked contract prices at once. The arithmetic is right and the defect is in the
inputs: they span hours, and some of them postdate goals. A surface fitted to a
price quoted at kick-off and a price quoted at the 70th minute is not a forecast
of anything -- it partly describes a score already known. That is why
MARKET_SURFACE_V0_STATUS is COHERENCE_PROTOTYPE_ONLY_NONCONTEMPORANEOUS and why
V0 may not be used for settlement prediction, residual alpha or convergence.

WHAT V1 ADDS
------------
One common decision timestamp T per surface. Every input is

  * the LATEST observation of its contract at or before T,
  * no older than MAX_QUOTE_AGE_S,
  * carrying its own quote age, which is reported per input and summarised
    per horizon as median, p90 and max.

Nothing observed after T can reach the fit. Pregame and live are never mixed.

THE ANCHOR PROBLEM, WHICH IS REAL
---------------------------------
"T minus 24 hours" needs something to be 24 hours before. The obvious anchor is
kick-off, and the retained corpus does not contain kick-off. It contains
RESOLVED_AT -- when the market settled, which for soccer is roughly two hours
AFTER kick-off.

So the anchors are named, and their consequences are enforced:

  ANCHOR_RESOLVED_AT         always available. T-24H and T-6H are comfortably
                             pregame. T-1H, T-15M and T-5M are NOT: one hour
                             before settlement is the second half. Those
                             horizons are refused on this anchor, because a
                             price quoted after a goal is not a pregame
                             opinion and LIVE_SURFACE_STATUS is NOT_IDENTIFIED
                             for want of game state.

  ANCHOR_PUBLIC_KICKOFF      available for fixtures bound to the public source,
                             which publishes a date and a local clock time with
                             NO TIMEZONE. That is worth up to a few hours of
                             error, so the short horizons are refused on this
                             anchor too -- KICKOFF_TIMEZONE_STATUS is
                             NOT_IDENTIFIED and a 5-minute horizon cannot
                             survive a 1-hour anchor uncertainty.

Reporting a T-5M surface from either anchor would be inventing precision. The
horizons are therefore produced where the anchor supports them and refused by
name where it does not. Recovering the short horizons needs kick-off to the
minute in a stated zone -- which is the paid-data request already on the table.

SECTION 11: THE CIRCULARITY GUARD
---------------------------------
A surface fitted to a contract's own price and then compared to that same price
cannot be informative about it -- the residual is partly the fit reproducing its
own input. Two hold-outs, and NO surface residual may be called informative
until both have been run:

  LEAVE_ONE_CONTRACT_OUT  refit without the contract being judged, then price
                          it. Any remaining residual is out-of-sample.
  LEAVE_ONE_FAMILY_OUT    refit without that contract's WHOLE family. Totals
                          are near-duplicates of each other, so dropping one
                          totals line while keeping five others barely changes
                          the fit; the honest hold-out drops the family.

LEAVE_ONE_FAMILY_OUT is the binding one, and a residual that survives only
leave-one-contract-out has not survived.
"""

from __future__ import annotations

import datetime
import math
import re
from collections import defaultdict

import ev_core_event_model as EM
import ev_core_surface as SURF

NOT_IDENTIFIED = "NOT_IDENTIFIED"

OBJECT_NAME = "MARKET_SURFACE_V1_ASOF"
MARKET_SURFACE_V1_ASOF_STATUS = "BUILT"
IS_MARKET_DERIVED = True
IS_INDEPENDENT_ALPHA = False

# ---------------------------------------------------------------------------
# Horizons and anchors
# ---------------------------------------------------------------------------

HORIZONS = (("T-24H", 24 * 3600), ("T-6H", 6 * 3600), ("T-1H", 3600),
            ("T-15M", 15 * 60), ("T-5M", 5 * 60))

MAX_QUOTE_AGE_S = 6 * 3600

ANCHOR_RESOLVED_AT = "RESOLVED_AT"
ANCHOR_PUBLIC_KICKOFF = "PUBLIC_KICKOFF"
# An anchor that needs no external clock at all: a quantile of the event's OWN
# observation times. It carries no horizon label -- it is not "T minus an hour"
# -- but it IS a real moment at which those prices stood together, which is
# exactly what the hold-outs in section 11 need. Use it to measure coherence;
# never use it to claim a horizon.
ANCHOR_OBSERVATION_QUANTILE = "OBSERVATION_QUANTILE"
ANCHORS = (ANCHOR_RESOLVED_AT, ANCHOR_PUBLIC_KICKOFF,
           ANCHOR_OBSERVATION_QUANTILE)

ANCHOR_OBSERVATION_QUANTILE_CARRIES_NO_HORIZON = (
    "This anchor says WHEN relative to the event's own quote stream, not "
    "relative to kick-off. A surface fitted at it is contemporaneous, which is "
    "what makes the leave-one-out residuals meaningful, but it cannot be "
    "reported as a T-minus-anything figure.")

# Anchor uncertainty, in seconds, stated rather than assumed away.
ANCHOR_UNCERTAINTY_S = {
    # zero by construction: it is one of the observation timestamps
    ANCHOR_OBSERVATION_QUANTILE: 0,
    # settlement follows the final whistle by an unknown but bounded delay, and
    # a soccer match runs about two hours from kick-off
    ANCHOR_RESOLVED_AT: 3 * 3600,
    # the public source writes a local clock time with no zone
    ANCHOR_PUBLIC_KICKOFF: 1 * 3600,
}

KICKOFF_TIMEZONE_STATUS = NOT_IDENTIFIED
LIVE_SURFACE_STATUS = NOT_IDENTIFIED
LIVE_SURFACE_WHY = SURF.LIVE_SURFACE_WHY

# A horizon is only honest if it is longer than the anchor's own uncertainty;
# otherwise "24 hours before" and "22 hours before" are the same measurement
# and a 5-minute horizon is noise wearing a label.
HORIZON_REFUSED_ANCHOR_TOO_UNCERTAIN = "HORIZON_SHORTER_THAN_ANCHOR_UNCERTAINTY"
HORIZON_REFUSED_WOULD_BE_LIVE = "HORIZON_FALLS_AFTER_KICKOFF_LIVE_NOT_PREGAME"
HORIZON_REFUSED_NO_ANCHOR = "NO_ANCHOR_TIMESTAMP_FOR_THIS_EVENT"


def horizon_admissible(horizon_s, anchor):
    """May this horizon be measured against this anchor? With a reason."""
    if anchor not in ANCHOR_UNCERTAINTY_S:
        return False, "UNKNOWN_ANCHOR"
    if horizon_s <= ANCHOR_UNCERTAINTY_S[anchor]:
        return False, HORIZON_REFUSED_ANCHOR_TOO_UNCERTAIN
    return True, "ADMISSIBLE"


# ---------------------------------------------------------------------------
# As-of snapshot construction
# ---------------------------------------------------------------------------


def _parse(ts):
    if not ts:
        return None
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d


def snapshot(event_rows, t, max_quote_age_s=MAX_QUOTE_AGE_S):
    """The latest observation of each contract at or before `t`.

    Returns (rows, report). A contract's key is (MARKET_SLUG, OUTCOME): the two
    sides of one market are separate quotes and must not overwrite each other.
    """
    T = _parse(t)
    if T is None:
        return [], {"STATUS": "NO_T"}
    latest, counted = {}, defaultdict(int)
    for r in event_rows or ():
        ts = _parse(r.get("AS_OF"))
        if ts is None:
            counted["NO_TIMESTAMP"] += 1
            continue
        if ts > T:
            counted["AFTER_T"] += 1
            continue
        age = (T - ts).total_seconds()
        if age > max_quote_age_s:
            counted["TOO_STALE"] += 1
            continue
        key = (r.get("MARKET_SLUG"), r.get("OUTCOME"))
        prev = latest.get(key)
        if prev is None or ts > prev[0]:
            latest[key] = (ts, dict(r, QUOTE_AGE_S=age))
    rows = [v[1] for v in latest.values()]
    ages = sorted(r["QUOTE_AGE_S"] for r in rows)
    return rows, {
        "T": t,
        "CONTRACTS": len(rows),
        "OBSERVATIONS_SEEN": len(event_rows or ()),
        "DROPPED": dict(counted),
        "MAX_QUOTE_AGE_S": max_quote_age_s,
        "QUOTE_AGE_MEDIAN_S": (ages[len(ages) // 2] if ages
                               else NOT_IDENTIFIED),
        "QUOTE_AGE_P90_S": (ages[min(len(ages) - 1, int(0.9 * len(ages)))]
                            if ages else NOT_IDENTIFIED),
        "QUOTE_AGE_MAX_S": (ages[-1] if ages else NOT_IDENTIFIED),
        "NOTHING_AFTER_T_REACHED_THE_FIT": counted["AFTER_T"] >= 0,
    }


def surface_asof(event_rows, t, home_code=None, away_code=None,
                 max_quote_age_s=MAX_QUOTE_AGE_S, max_goals=8):
    """Fit one coherent surface using only what was quoted at or before `t`."""
    rows, snap = snapshot(event_rows, t, max_quote_age_s)
    out = SURF.surface(rows, home_code, away_code, max_goals)
    out["OBJECT"] = OBJECT_NAME
    out["AS_OF"] = t
    out["SNAPSHOT"] = snap
    out["MARKET_SURFACE_V1_ASOF_STATUS"] = MARKET_SURFACE_V1_ASOF_STATUS
    out["IS_INDEPENDENT_ALPHA"] = IS_INDEPENDENT_ALPHA
    out["NOT_ALPHA"] = SURF.NOT_ALPHA
    return out


def observation_quantile_t(event_rows, q=0.75):
    """The q-th quantile of this event's own observation timestamps.

    q=0.75 by default: late enough that most of the event's contracts have
    already been quoted at least once, early enough to leave the tail out.
    """
    ts = sorted(t for t in (_parse(r.get("AS_OF")) for r in event_rows or ())
                if t is not None)
    if not ts:
        return None
    return ts[min(len(ts) - 1, int(q * len(ts)))].isoformat().replace(
        "+00:00", "Z")


def anchor_for(event_rows, anchor, kickoff_by_event=None, event_key=None):
    """The anchor timestamp for one event, or None with a reason."""
    if anchor == ANCHOR_OBSERVATION_QUANTILE:
        t = observation_quantile_t(event_rows)
        return (t, "OK") if t else (None, HORIZON_REFUSED_NO_ANCHOR)
    if anchor == ANCHOR_RESOLVED_AT:
        for r in event_rows or ():
            if r.get("RESOLVED_AT"):
                return r["RESOLVED_AT"], "OK"
        return None, HORIZON_REFUSED_NO_ANCHOR
    if anchor == ANCHOR_PUBLIC_KICKOFF:
        k = (kickoff_by_event or {}).get(event_key)
        if k:
            return k, "OK"
        return None, HORIZON_REFUSED_NO_ANCHOR
    return None, "UNKNOWN_ANCHOR"


def surfaces_by_horizon(event_rows, anchor=ANCHOR_RESOLVED_AT,
                        kickoff_by_event=None, event_key=None,
                        home_code=None, away_code=None,
                        horizons=HORIZONS, max_quote_age_s=MAX_QUOTE_AGE_S):
    """One surface per admissible horizon; the rest refused by name."""
    a, why = anchor_for(event_rows, anchor, kickoff_by_event, event_key)
    out = {"ANCHOR": anchor, "ANCHOR_TS": a,
           "ANCHOR_UNCERTAINTY_S": ANCHOR_UNCERTAINTY_S.get(anchor),
           "KICKOFF_TIMEZONE_STATUS": KICKOFF_TIMEZONE_STATUS,
           "LIVE_SURFACE_STATUS": LIVE_SURFACE_STATUS,
           "HORIZONS": {}}
    A = _parse(a)
    for name, secs in horizons:
        ok, reason = horizon_admissible(secs, anchor)
        if A is None:
            out["HORIZONS"][name] = {"STATUS": "REFUSED", "REASON": why}
            continue
        if not ok:
            out["HORIZONS"][name] = {
                "STATUS": "REFUSED", "REASON": reason,
                "HORIZON_S": secs,
                "ANCHOR_UNCERTAINTY_S": ANCHOR_UNCERTAINTY_S.get(anchor),
                "LIVE_SURFACE_WHY": (LIVE_SURFACE_WHY
                                     if secs <= 2 * 3600 else None)}
            continue
        t = (A - datetime.timedelta(seconds=secs)).isoformat().replace(
            "+00:00", "Z")
        out["HORIZONS"][name] = surface_asof(
            event_rows, t, home_code, away_code, max_quote_age_s)
    return out


# ---------------------------------------------------------------------------
# Section 11: the circularity guard
# ---------------------------------------------------------------------------

FAMILY_RE = (
    ("FIRST_HALF_TOTAL", re.compile(r"-first-half-total-\d+pt5$")),
    ("HALFTIME_RESULT", re.compile(r"-halftime-result-(home|away|draw)$")),
    ("TEAM_TOTAL", re.compile(r"-team-total-(home|away)-\d+pt5$")),
    ("TOTAL", re.compile(r"-total-\d+pt5$")),
    ("EXACT_SCORE", re.compile(r"-exact-score-\d+-\d+$")),
    ("SPREAD", re.compile(r"-spread-(home|away)-\d+pt5$")),
    ("BTTS", re.compile(r"-btts$")),
    ("DRAW", re.compile(r"-draw$")),
)

LOCO = "LEAVE_ONE_CONTRACT_OUT"
LOFO = "LEAVE_ONE_FAMILY_OUT"
HOLDOUTS = (LOCO, LOFO)

NO_RESIDUAL_IS_INFORMATIVE_UNTIL = (
    "A residual computed from a surface that was fitted to the contract's own "
    "price is partly the fit reproducing its input. Neither "
    "LEAVE_ONE_CONTRACT_OUT nor LEAVE_ONE_FAMILY_OUT has any meaning as a "
    "claim until both are run, and LEAVE_ONE_FAMILY_OUT is the binding one: "
    "the six lines of a totals ladder are near-duplicates, so dropping one of "
    "them and keeping five leaves the fit essentially unchanged and the "
    "hold-out essentially fake."
)


def family_of(slug):
    """The contract family, segment-qualified patterns tested FIRST.

    '-first-half-total-2pt5' also ends in '-total-2pt5' and
    '-halftime-result-draw' also ends in '-draw'. Testing the plain patterns
    first silently files a half-time contract as a full-game one -- the same
    ordering defect that produced 29 false contradictions in the outcome
    reconstructor, caught the same way.
    """
    s = slug or ""
    for name, rx in FAMILY_RE:
        if rx.search(s):
            return name
    return "OTHER"


def _price_of(grid, slug, outcome):
    """Re-price one contract off a grid, or None if the family is not priceable."""
    side = (outcome or "").strip().lower()
    fam = family_of(slug)
    if fam == "TOTAL":
        m = re.search(r"-total-(\d+)pt5$", slug)
        p = EM.p_total_over(grid, int(m.group(1)) + 0.5)
        if p == NOT_IDENTIFIED:
            return None
        return p if side == "over" else (1.0 - p if side == "under" else None)
    if fam == "EXACT_SCORE":
        m = re.search(r"-exact-score-(\d+)-(\d+)$", slug)
        p = EM.p_exact(grid, int(m.group(1)), int(m.group(2)))
        return p if side == "yes" else (1.0 - p if side == "no" else None)
    if fam == "DRAW":
        p = EM.p_draw(grid)
        return p if side == "yes" else (1.0 - p if side == "no" else None)
    if fam == "BTTS":
        p = EM.p_btts(grid)
        return p if side == "yes" else (1.0 - p if side == "no" else None)
    return None


def holdout_residuals(event_rows, t, home_code=None, away_code=None,
                      max_quote_age_s=MAX_QUOTE_AGE_S, max_goals=8):
    """In-fit, leave-one-contract-out and leave-one-family-out residuals.

    One entry per priceable contract in the as-of snapshot. The comparison that
    matters is how much of the in-fit residual survives each hold-out.
    """
    rows, snap = snapshot(event_rows, t, max_quote_age_s)
    if len(rows) < SURF.MIN_CONTRACTS + 1:
        return {"STATUS": "INSUFFICIENT_CONTRACTS", "CONTRACTS": len(rows),
                "SNAPSHOT": snap}

    def grid_from(subset):
        obs = SURF.observations(subset, home_code, away_code)
        got = SURF.fit(obs, max_goals)
        if got is None:
            return None
        _e, lh, la, rho = got
        return EM.score_grid(lh, la, EM.FAMILY_DIXON_COLES, rho=rho,
                             max_goals=max_goals)

    full = grid_from(rows)
    if full is None:
        return {"STATUS": "FIT_FAILED", "SNAPSHOT": snap}

    out = []
    for i, r in enumerate(rows):
        slug, outcome = r.get("MARKET_SLUG"), r.get("OUTCOME")
        raw = r.get("P_VENUE_TRADE")
        if raw is None:
            continue
        p_in = _price_of(full, slug, outcome)
        if p_in is None:
            continue
        fam = family_of(slug)

        loco_rows = [x for j, x in enumerate(rows) if j != i]
        g_loco = grid_from(loco_rows) if len(loco_rows) >= SURF.MIN_CONTRACTS \
            else None
        p_loco = _price_of(g_loco, slug, outcome) if g_loco else None

        lofo_rows = [x for x in rows if family_of(x.get("MARKET_SLUG")) != fam]
        g_lofo = grid_from(lofo_rows) if len(lofo_rows) >= SURF.MIN_CONTRACTS \
            else None
        p_lofo = _price_of(g_lofo, slug, outcome) if g_lofo else None

        out.append({
            "MARKET_SLUG": slug, "OUTCOME": outcome, "FAMILY": fam,
            "QUOTE_AGE_S": r.get("QUOTE_AGE_S"),
            "P_VENUE_TRADE": float(raw),
            "P_SURFACE_IN_FIT": p_in,
            "RESIDUAL_IN_FIT": p_in - float(raw),
            "P_SURFACE_LOCO": p_loco,
            "RESIDUAL_LOCO": (p_loco - float(raw)) if p_loco is not None
                             else NOT_IDENTIFIED,
            "P_SURFACE_LOFO": p_lofo,
            "RESIDUAL_LOFO": (p_lofo - float(raw)) if p_lofo is not None
                             else NOT_IDENTIFIED,
            "LOFO_CONTRACTS_REMAINING": len(lofo_rows),
            "SETTLED_YES": r.get("SETTLED_YES"),
        })

    def _mean_abs(key):
        vals = [abs(x[key]) for x in out
                if isinstance(x.get(key), float)]
        return (sum(vals) / len(vals)) if vals else NOT_IDENTIFIED

    return {
        "STATUS": "MEASURED",
        "T": t,
        "CONTRACTS": len(out),
        "SNAPSHOT": snap,
        "RESIDUALS": out,
        "MEAN_ABS_RESIDUAL_IN_FIT": _mean_abs("RESIDUAL_IN_FIT"),
        "MEAN_ABS_RESIDUAL_LOCO": _mean_abs("RESIDUAL_LOCO"),
        "MEAN_ABS_RESIDUAL_LOFO": _mean_abs("RESIDUAL_LOFO"),
        "HOLDOUTS": list(HOLDOUTS),
        "BINDING_HOLDOUT": LOFO,
        "NO_RESIDUAL_IS_INFORMATIVE_UNTIL": NO_RESIDUAL_IS_INFORMATIVE_UNTIL,
        "IS_INDEPENDENT_ALPHA": IS_INDEPENDENT_ALPHA,
        "NOT_ALPHA": SURF.NOT_ALPHA,
    }


def residual_predicts_settlement(residual_reports, key="RESIDUAL_LOFO"):
    """Does the held-out residual point at the outcome? Descriptive, per family.

    A positive mean residual on contracts that settled YES, and negative on
    those that settled NO, would mean the surface leans the right way. This
    reports that difference and nothing stronger: it is not a trading result
    and it does not net a spread or a fee.
    """
    by = defaultdict(lambda: {"YES": [], "NO": []})
    for rep in residual_reports or ():
        if rep.get("STATUS") != "MEASURED":
            continue
        for r in rep["RESIDUALS"]:
            v, y = r.get(key), r.get("SETTLED_YES")
            if not isinstance(v, float) or y not in (0, 1):
                continue
            by[r["FAMILY"]]["YES" if y else "NO"].append(v)
            by["ALL"]["YES" if y else "NO"].append(v)
    out = {}
    for fam, d in sorted(by.items()):
        ys, ns = d["YES"], d["NO"]
        if len(ys) < 5 or len(ns) < 5:
            out[fam] = {"STATUS": "TOO_FEW", "YES_N": len(ys), "NO_N": len(ns)}
            continue
        my, mn = sum(ys) / len(ys), sum(ns) / len(ns)
        vy = sum((x - my) ** 2 for x in ys) / len(ys)
        vn = sum((x - mn) ** 2 for x in ns) / len(ns)
        pooled = math.sqrt((vy + vn) / 2)
        out[fam] = {
            "YES_MEAN_RESIDUAL": my, "NO_MEAN_RESIDUAL": mn,
            "DIFFERENCE": my - mn,
            "SMD": ((my - mn) / pooled) if pooled > 0 else 0.0,
            "YES_N": len(ys), "NO_N": len(ns),
            "SIGN_IS_RIGHT_WAY_ROUND": my > mn,
        }
    return {
        "RESIDUAL_KEY": key,
        "BY_FAMILY": out,
        "THIS_IS_NOT_A_TRADING_RESULT": (
            "a residual that leans the right way still has to clear the spread "
            "and the fee and be fillable; none of that is measured here"),
        "IS_INDEPENDENT_ALPHA": IS_INDEPENDENT_ALPHA,
    }


# ---------------------------------------------------------------------------
# WHAT THE CORPUS ACTUALLY SUPPORTS -- measured, not assumed
# ---------------------------------------------------------------------------
#
# The machinery above is correct and the retained corpus barely feeds it. The
# reason is in ev_core_data's own warning: OBSERVATION_SAMPLING is
# WHALE_TRADE_TRIGGERED, so a contract is only priced when RN1 happened to
# trade it. That produces a sample concentrated where he traded, which is the
# last two hours before kick-off.
#
# Measured over 41,413 soccer contract observations across 1,092 events:

CORPUS_DENSITY_MEASURED_AT = "2026-09-17"
CORPUS_SOCCER_OBSERVATIONS = 41413
CORPUS_SOCCER_EVENTS = 1092

# Against the settlement anchor, how many events have MIN_CONTRACTS linked
# quotes alive at the horizon, at any quote-age limit from 2h to 72h:
EVENTS_WITH_ENOUGH_CONTRACTS = {
    "T-24H": 0,        # of 1,092. Not few. None.
    "T-6H": 6,         # 0.5%
}

# Against the public kick-off anchor, on the 201 events bound to the public
# source, where each observation actually sits relative to kick-off (hours):
OBSERVATION_MINUS_KICKOFF_HOURS = {
    "p1": -2.01, "p5": -1.84, "p10": -1.67, "p25": -1.15,
    "p50": -0.49, "p75": -0.10, "p90": +0.58, "p99": +0.88,
}
SHARE_PREGAME_PCT = 79.9
SHARE_IN_PLAY_PCT = 20.1
SHARE_MORE_THAN_6H_BEFORE_KICKOFF_PCT = 0.3
SETTLEMENT_MINUS_KICKOFF_HOURS_MEDIAN = 1.88

CORPUS_SUPPORTS_PREGAME_HORIZONS = False
WHY_THE_CORPUS_DOES_NOT_SUPPORT_THEM = (
    "Four fifths of the observations are pregame, which is better than V0's "
    "description suggested, but they are packed into the last two hours: the "
    "median observation is 29 minutes before kick-off and only 0.3% are more "
    "than six hours out. So T-24H has literally no events with four linked "
    "quotes and T-6H has six of 1,092. The horizons the directive names are "
    "not thin here, they are empty, and no amount of re-fitting changes that.\n\n"
    "The horizons this corpus could support are inside the last two hours -- "
    "T-2H, T-90M, T-60M, T-30M. Measuring them needs kick-off to the minute in "
    "a stated timezone, which the free public source does not publish: it "
    "writes a local clock time with no zone, worth about an hour of error, and "
    "an hour of anchor error makes a thirty-minute horizon meaningless.\n\n"
    "Two ways forward, and they are independent. Buy kick-off times to the "
    "minute, which is already in the paid-data request. Or collect prices on a "
    "clock of our own rather than on RN1's trading, which is what the "
    "substantive public-book capture does."
)
