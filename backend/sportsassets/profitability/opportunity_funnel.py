"""THE UNIQUE OPPORTUNITY FUNNEL (owner audit 2026-10-04, R30A). Pure; no I/O.

THE DEFECT. Blockers were ranked by DECISION ROWS. Every paper policy
re-evaluates a live market on every valuation (each drained WebSocket change,
each periodic re-read): one market refused for the same reason two hundred
times in an afternoon counted as two hundred "opportunities lost" to that
blocker, while a blocker that cost twenty different markets once each ranked
below it. The ranking measured how often a market was looked at, not how many
opportunities a blocker stood in front of.

THE UNIQUE OPPORTUNITY. One key per opportunity:

    (event = fixture, market = us_market_slug, side = holding_side,
     line = label.line, scope = label.period)

Every decision row with that key is one EVALUATION of the opportunity; the
rows after the first are RE-EVALUATIONS, counted separately and never as new
opportunities. A row without a market or side is not an opportunity and is
counted apart (UNKEYED) with its reason.

PER BLOCKER (the decision's first refusal; the binding blocker of an
opportunity is the first refusal of its LATEST evaluation, unless any
evaluation ENTERED):
  unique_opportunities           opportunities whose binding blocker it is
  unique_opportunities_ever      opportunities it refused at least once
  evaluations / re_evaluations   its rows, and its rows beyond the first per
                                 opportunity it refused
  near_misses                    never-entered opportunities it binds whose
                                 best evaluation offered a POSITIVE executable
                                 net EV
  missed_executable_ev_usd       the sum, over the never-entered opportunities
                                 it binds, of each one's BEST executable net
                                 EV (one per opportunity, never summed across
                                 its re-evaluations)
Blockers are ranked by unique_opportunities; the row count is shown beside it
as re-evaluations (and the old row ranking is reported for comparison).

EXECUTABLE EV -- AT THE STRATEGY'S OWN FRESHNESS, NEVER OLDER. One
evaluation's executable net EV is profitability.capacity.assess (levels whose
p - price - fee > 0, after fees, CONDITIONAL ON FILLING at the displayed
book) on a book that meets the decision strategy's executable freshness
(capacity.executable_fresh: the decision's own recorded book at its recorded
age, else the latest observation at or before the decision no older than the
strategy's entry bound), with a probability that was FRESH at the decision
(the Pinnacle reading qualified under its own 30 s rule). Anything missing is
UNAVAILABLE with its reason -- a stale probability, no book at executable
freshness, no fee schedule -- never a zero. It is HYPOTHETICAL and decision-
time only: a hindsight result is not a missed opportunity.

RESEARCH / SHADOW: this module computes over rows a caller read. It has no
venue, order, sizing, threshold, gate or capital authority.
"""
from __future__ import annotations

from ..intel import attribution as AT
from . import capacity as CP
from . import common as C

VERSION = "OPPORTUNITY_FUNNEL_V1"
KEY_FIELDS = ("fixture", "us_market_slug", "holding_side", "line", "scope")
ENTER = "ENTER"
ENTERED = "ENTERED"
MEASURED, UNAVAILABLE = C.MEASURED, C.UNAVAILABLE
STAGES = ("EVALUATED", "PROBABILITY_FRESH", "EXECUTABLE_FRESH_BOOK",
          "POSITIVE_EXECUTABLE_EV", "ENTERED")
EV_LABEL = ("HYPOTHETICAL: executable net EV after fees at the decision-time "
            "book, conditional on filling at the displayed depth; never a "
            "realized result, never summed with PAPER or ACTUAL")
R_UNKEYED = "NO_MARKET_OR_SIDE_ON_THE_DECISION"
R_NO_PROBABILITY = "NO_PROBABILITY_ON_THE_DECISION"
R_PROBABILITY_NOT_FRESH = "PROBABILITY_NOT_FRESH_AT_THE_DECISION"
R_NOT_EVALUATED = "EV_NOT_EVALUATED: beyond the per-opportunity bound"
R_NO_REFUSAL_CODE = "REFUSED_WITHOUT_A_RECORDED_REFUSAL_CODE"


def _s(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def opportunity_key(d: dict):
    """The unique-opportunity key of one decision row, or None (UNKEYED)."""
    slug, side = _s(d.get("us_market_slug")), _s(d.get("holding_side"))
    if slug is None or side is None:
        return None
    return (_s(d.get("fixture")), slug, side.upper(), _s(d.get("line")),
            _s(d.get("scope")))


def key_fields(key) -> dict:
    return dict(zip(KEY_FIELDS, key)) if key else {}


def blocker_of(d: dict):
    """The decision's binding refusal: its first recorded refusal (ENTER for
    an admitted decision)."""
    if str(d.get("verdict") or "").upper() == ENTER:
        return ENTERED
    rs = d.get("refusals") or []
    return _s(d.get("refusal")) or (_s(rs[0]) if rs else None) or \
        R_NO_REFUSAL_CODE


def probability_fresh(d: dict) -> dict:
    """{fresh, p, basis, why}: the decision's probability, and whether its
    Pinnacle reading was qualified under its own freshness rule AT THE
    DECISION (the recorded `pinnacle` record: qualified, age_s, limit_s)."""
    p, basis = AT.decision_probability(d)
    pin = C.jload(d.get("pinnacle")) or {}
    if not isinstance(pin, dict):
        pin = {}
    age, lim = C.num(pin.get("age_s")), C.num(pin.get("limit_s"))
    out = {"p": p, "basis": basis, "age_s": age, "limit_s": lim}
    if p is None or not 0.0 < p < 1.0:
        return dict(out, fresh=False, why=R_NO_PROBABILITY)
    if pin.get("qualified") is not True:
        return dict(out, fresh=False, why="%s: %s" % (
            R_PROBABILITY_NOT_FRESH, pin.get("refusal") or "NOT_QUALIFIED"))
    if age is not None and lim is not None and age > lim:
        return dict(out, fresh=False, why="%s: age %.3fs > %.3fs" % (
            R_PROBABILITY_NOT_FRESH, age, lim))
    return dict(out, fresh=True, why=None)


def evaluate(d: dict, *, book: dict | None, fee_fn) -> dict:
    """The executable net EV of ONE evaluation. `d` is a decision row
    (decision_id, strategy, decided_at, us_market_slug, holding_side,
    probabilities, pinnacle, book_obs_id, decision_book_age_s); `book` is
    {obs_id, observed_at, bids, offers} -- the decision's own observation or
    the latest at or before it -- or None."""
    pf = probability_fresh(d)
    base = {"probability": pf["p"], "probability_basis": pf["basis"],
            "probability_fresh": pf["fresh"],
            "probability_age_s": pf["age_s"],
            "probability_limit_s": pf["limit_s"]}
    if not pf["fresh"]:
        return dict(base, status=UNAVAILABLE, why=pf["why"],
                    executable_ev_usd=None, positive=None,
                    book_freshness=None)
    dec = C.num(d.get("decided_at"))
    obs_at = C.num((book or {}).get("observed_at"))
    fr = CP.executable_fresh(
        strategy=d.get("strategy"), book_obs_id=(book or {}).get("obs_id"),
        decision_book_obs_id=d.get("book_obs_id"),
        book_age_s=(None if dec is None or obs_at is None else dec - obs_at),
        recorded_age_s=d.get("decision_book_age_s"))
    if not fr["fresh"]:
        return dict(base, status=UNAVAILABLE, why=fr["why"],
                    executable_ev_usd=None, positive=None, book_freshness=fr)
    a = CP.assess({"candidate_id": d.get("decision_id"),
                   "decided_at": dec,
                   "us_market_slug": d.get("us_market_slug"),
                   "holding_side": d.get("holding_side"),
                   "strategy": d.get("strategy"),
                   "probability": pf["p"]}, book, fee_fn=fee_fn)
    if a.get("status") != MEASURED:
        return dict(base, status=UNAVAILABLE, why=a.get("why"),
                    executable_ev_usd=None, positive=None, book_freshness=fr)
    ev = C.num(a.get("executable_opportunity_dollars"))
    return dict(base, status=MEASURED, why=None, executable_ev_usd=ev,
                positive=bool(ev is not None and ev > 0),
                executable_capacity_usd=C.num(a.get(
                    "executable_capacity_usd")),
                best_price=C.num(a.get("best_price")),
                book_obs_id=(book or {}).get("obs_id"), book_freshness=fr)


# ═════════════════════════════════════════════════════════════════════
# THE FUNNEL OVER ONE SCOPE
# ═════════════════════════════════════════════════════════════════════

def _opportunities(rows: list) -> tuple:
    """({key: opportunity}, unkeyed rows). `rows` carry the decision fields
    and `ev` (evaluate()'s result, or None when not evaluated)."""
    ops: dict = {}
    unkeyed = []
    for r in sorted(rows, key=lambda r: (C.num(r.get("decided_at")) or 0.0,
                                         str(r.get("decision_id") or ""))):
        k = opportunity_key(r)
        if k is None:
            unkeyed.append(r)
            continue
        o = ops.setdefault(k, {
            "key": k, "evaluations": 0, "first_at": None, "last_at": None,
            "entered": False, "blockers_seen": {}, "latest_blocker": None,
            "strategies": set(), "policy_versions": set(),
            "prob_fresh": False, "exec_fresh": False, "best_ev": None,
            "ev_measured": 0, "ev_considered": 0, "ev_whys": {}})
        o["evaluations"] += 1
        t = C.num(r.get("decided_at"))
        o["first_at"] = t if o["first_at"] is None else o["first_at"]
        o["last_at"] = t
        b = blocker_of(r)
        if b == ENTERED:
            o["entered"] = True
        else:
            o["blockers_seen"][b] = o["blockers_seen"].get(b, 0) + 1
        o["latest_blocker"] = b
        if r.get("strategy"):
            o["strategies"].add(str(r["strategy"]))
        if r.get("policy_version"):
            o["policy_versions"].add(str(r["policy_version"]))
        ev = r.get("ev")
        if ev is None:
            continue
        o["ev_considered"] += 1
        if ev.get("probability_fresh"):
            o["prob_fresh"] = True
        if (ev.get("book_freshness") or {}).get("fresh"):
            o["exec_fresh"] = True
        if ev.get("status") == MEASURED:
            o["ev_measured"] += 1
            x = ev.get("executable_ev_usd")
            if x is not None and (o["best_ev"] is None or x > o["best_ev"]):
                o["best_ev"] = x
        else:
            w = str(ev.get("why") or "UNKNOWN").split(":", 1)[0]
            o["ev_whys"][w] = o["ev_whys"].get(w, 0) + 1
    for o in ops.values():
        o["binding_blocker"] = ENTERED if o["entered"] else o["latest_blocker"]
        o["near_miss"] = bool(not o["entered"] and o["best_ev"] is not None
                              and o["best_ev"] > 0)
    return ops, unkeyed


def compute(rows: list, *, book: str = "PAPER", sleeve, strategy,
            top_n: int = 20) -> dict:
    """The unique-opportunity funnel of ONE scope (`strategy` None = every
    strategy of the sleeve)."""
    ops, unkeyed = _opportunities(rows)
    by: dict = {}
    for o in ops.values():
        for b, n in o["blockers_seen"].items():
            e = by.setdefault(b, {
                "blocker": b, "unique_opportunities": 0,
                "unique_opportunities_ever": 0, "evaluations": 0,
                "near_misses": 0, "missed_executable_ev_usd": 0.0,
                "missed_ev_measured": 0, "missed_ev_unavailable": 0,
                "missed_ev_unavailable_why": {}})
            e["unique_opportunities_ever"] += 1
            e["evaluations"] += n
        b = o["binding_blocker"]
        if b == ENTERED:
            continue
        e = by[b]
        e["unique_opportunities"] += 1
        if o["near_miss"]:
            e["near_misses"] += 1
        if o["best_ev"] is not None:
            e["missed_ev_measured"] += 1
            e["missed_executable_ev_usd"] += o["best_ev"]
        else:
            e["missed_ev_unavailable"] += 1
            for w, n in (o["ev_whys"] or {"NO_EVALUATION_WITH_A_FRESH_"
                                          "PROBABILITY_AND_BOOK": 1}).items():
                e["missed_ev_unavailable_why"][w] = \
                    e["missed_ev_unavailable_why"].get(w, 0) + n
    blockers = []
    for e in by.values():
        e["re_evaluations"] = e["evaluations"] - e["unique_opportunities_ever"]
        e["evaluations_per_opportunity"] = C.rnd(
            e["evaluations"] / e["unique_opportunities_ever"], 6)
        e["missed_executable_ev_usd"] = (
            C.rnd(e["missed_executable_ev_usd"], 6)
            if e["missed_ev_measured"] else None)
        e["missed_executable_ev_why"] = (
            None if e["missed_ev_measured"] else
            "UNAVAILABLE: no opportunity it binds had a fresh probability "
            "and an executable-fresh book" if e["unique_opportunities"] else
            "NOT_BINDING: every opportunity it refused was later entered or "
            "bound by another blocker")
        blockers.append(e)
    blockers.sort(key=lambda e: (-e["unique_opportunities"],
                                 -(e["missed_executable_ev_usd"] or 0.0),
                                 e["blocker"]))
    by_rows = [e["blocker"] for e in sorted(
        blockers, key=lambda e: (-e["evaluations"], e["blocker"]))]
    n_ops = len(ops)
    evals = sum(o["evaluations"] for o in ops.values())
    never = [o for o in ops.values() if not o["entered"]]
    measured_never = [o for o in never if o["best_ev"] is not None]
    stages = {
        "EVALUATED": n_ops,
        "PROBABILITY_FRESH": sum(1 for o in ops.values() if o["prob_fresh"]),
        "EXECUTABLE_FRESH_BOOK": sum(1 for o in ops.values()
                                     if o["exec_fresh"]),
        "POSITIVE_EXECUTABLE_EV": sum(
            1 for o in ops.values()
            if o["best_ev"] is not None and o["best_ev"] > 0),
        "ENTERED": sum(1 for o in ops.values() if o["entered"])}
    top = sorted(ops.values(), key=lambda o: (-o["evaluations"],
                                              str(o["key"])))[:top_n]
    pvs = sorted({v for o in ops.values() for v in o["policy_versions"]})
    return {
        "version": VERSION, "book": book, "sleeve": sleeve,
        "strategy": strategy or C.ALL_STRATEGIES,
        "policy_versions": pvs,
        "policy_version": pvs[0] if len(pvs) == 1 else None,
        "policy_version_why": (None if len(pvs) == 1 else
                               "NO_DECISION_IN_SCOPE" if not pvs else
                               "MORE_THAN_ONE_POLICY_VERSION_IN_SCOPE"),
        "confidence_scope": C.confidence_scope(sleeve),
        "key": list(KEY_FIELDS),
        "totals": {
            "unique_opportunities": n_ops,
            "evaluations": evals,
            "re_evaluations": evals - n_ops,
            "entered_unique": stages["ENTERED"],
            "refused_unique": len(never),
            "near_misses": sum(1 for o in never if o["near_miss"]),
            "missed_executable_ev_usd": (
                C.rnd(sum(o["best_ev"] for o in measured_never), 6)
                if measured_never else None),
            "missed_executable_ev_measured": len(measured_never),
            "missed_executable_ev_unavailable": len(never)
            - len(measured_never),
            "missed_executable_ev_label": EV_LABEL,
            "unkeyed_evaluations": len(unkeyed),
            "unkeyed_why": R_UNKEYED if unkeyed else None},
        "stages": [{"stage": s, "unique_opportunities": stages[s]}
                   for s in STAGES],
        "blockers": blockers,
        "ranking_basis": ("unique opportunities bound (ties: missed "
                          "executable EV); rows are re-evaluations"),
        "row_ranking_for_comparison": by_rows,
        "most_reevaluated": [dict(
            key_fields(o["key"]), evaluations=o["evaluations"],
            re_evaluations=o["evaluations"] - 1,
            binding_blocker=o["binding_blocker"],
            blockers_seen=o["blockers_seen"], entered=o["entered"],
            near_miss=o["near_miss"],
            best_executable_ev_usd=C.rnd(o["best_ev"], 6),
            ev_evaluations_considered=o["ev_considered"],
            ev_evaluations_measured=o["ev_measured"],
            ev_unavailable_why=o["ev_whys"] or None,
            strategies=sorted(o["strategies"]),
            first_at=o["first_at"], last_at=o["last_at"]) for o in top],
        "label": C.LABEL, "authority": C.AUTHORITY}
