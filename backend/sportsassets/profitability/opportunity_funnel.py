"""THE UNIQUE OPPORTUNITY FUNNEL (owner audit 2026-10-04, R30A). Pure; no I/O.

THE DEFECT. Blockers were ranked by DECISION ROWS. Every paper policy
re-evaluates a live market on every valuation (each drained WebSocket change,
each periodic re-read): one market refused for the same reason two hundred
times in an afternoon counted as two hundred "opportunities lost" to that
blocker, while a blocker that cost twenty different markets once each ranked
below it. The ranking measured how often a market was looked at, not how many
opportunities a blocker stood in front of. (Production, research-sql run
37229376890: three strategies recorded 530 decisions each on 24 contracts in
6 h -- 22 rows per contract on average, up to 91.)

THE UNIQUE OPPORTUNITY. One key per opportunity:

    (event = fixture, market = us_market_slug, side = holding_side,
     line = label.line, scope = label.period)

Every decision row with that key is one EVALUATION of the opportunity; the
rows after the first are RE-EVALUATIONS, counted separately and never as new
opportunities. A row without a market or side is not an opportunity and is
counted apart (UNKEYED) with its reason.

ATTRIBUTION IS PER (OPPORTUNITY, STRATEGY) (R30A review). Two INVESTMENT
strategies (the completed-game policy and Derek) evaluate the SAME markets
(production: 530 decisions each on the same 24 contracts). Under one key the
binding blocker was whichever strategy happened to evaluate LAST, and an
ENTER by one hid every refusal by the other. Now each strategy's evaluations
of an opportunity are one UNIT: the unit's binding blocker is the first
refusal of THAT strategy's latest evaluation (none once that strategy
entered). An opportunity is bound by every blocker that binds one of its
units -- counted ONCE per blocker however many strategies it binds -- and
`entered_by_another_strategy` says when another strategy entered it.

PER BLOCKER (`unique_opportunities` ranks it):
  unique_opportunities           opportunities it binds for >= 1 strategy
  strategy_opportunities         (opportunity, strategy) units it binds
  bound_by_strategy              those units per strategy
  unique_opportunities_ever      opportunities it refused at least once
  evaluations / re_evaluations   its rows, and its rows beyond the first per
                                 opportunity it refused
  near_misses                    opportunities with a unit it binds whose
                                 best PRICED evaluation offered a POSITIVE
                                 executable net EV
  missed_executable_ev_usd       the sum over the opportunities it binds of
                                 the best executable net EV among the units
                                 it binds (one per opportunity: never summed
                                 across re-evaluations or strategies)
  evaluations_not_priced /       evaluations with a fresh probability left
  missed_ev_partial              unpriced by the request's pricing bound, and
                                 the opportunities whose best EV is therefore
                                 the best of a PARTIAL set
  book, sleeve, strategies,      the scope of the row itself
  policy_versions
The row count is shown beside it as re-evaluations (and the old row ranking
is reported for comparison).

THE PROBABILITY STAGES. A decision's probability is classified ONCE, from
the recorded `pinnacle` reading and the decision's own refusals:
  NO_PROBABILITY       none recorded (or outside (0, 1));
  NOT_QUALIFIED        the reading was not qualified for a reason that is
                       NOT its age (absent, provenance refused, or the lane
                       refused it: NO_QUALIFIED_PINNACLE_PROBABILITY /
                       PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE);
  NOT_FRESH            qualified, but older than its own limit (the 30 s
                       rule), or its freshness could not be established;
  FRESH                qualified and within its limit.
The stages PROBABILITY_QUALIFIED and PROBABILITY_FRESH are counted from the
decision rows THEMSELVES, never from whether an evaluation was priced.

EXECUTABLE EV -- AT THE STRATEGY'S OWN FRESHNESS, NEVER OLDER. One
evaluation's executable net EV is profitability.capacity.assess (levels whose
p - price - fee > 0, after fees, CONDITIONAL ON FILLING at the displayed
book) on a book that meets the decision strategy's executable freshness
(capacity.executable_fresh: the decision's own recorded book at its recorded
age, else the latest observation at or before the decision no older than the
strategy's entry bound), with a FRESH probability. Anything missing is
UNAVAILABLE with its reason -- a stale or unqualified probability, no book
at executable freshness, no fee schedule, or NOT_EVALUATED (beyond the
request's pricing bound) -- never a zero. It is HYPOTHETICAL and decision-
time only: a hindsight result is not a missed opportunity.

RESEARCH / SHADOW: this module computes over rows a caller read. It has no
venue, order, sizing, threshold, gate or capital authority.
"""
from __future__ import annotations

from ..intel import attribution as AT
from . import capacity as CP
from . import common as C

#: V2 (R30A review): per-(opportunity, strategy) attribution; explicit
#: NOT_EVALUATED; probability qualification split from freshness
VERSION = "OPPORTUNITY_FUNNEL_V2"
KEY_FIELDS = ("fixture", "us_market_slug", "holding_side", "line", "scope")
ENTER = "ENTER"
ENTERED = "ENTERED"
MEASURED, UNAVAILABLE = C.MEASURED, C.UNAVAILABLE
STAGES = ("EVALUATED", "PROBABILITY_QUALIFIED", "PROBABILITY_FRESH",
          "EXECUTABLE_FRESH_BOOK", "POSITIVE_EXECUTABLE_EV", "ENTERED")
#: the stages known only for PRICED evaluations
PRICED_STAGES = ("EXECUTABLE_FRESH_BOOK", "POSITIVE_EXECUTABLE_EV")
EV_LABEL = ("HYPOTHETICAL: executable net EV after fees at the decision-time "
            "book, conditional on filling at the displayed depth; the best of "
            "the PRICED evaluations (missed_ev_partial counts opportunities "
            "with unpriced fresh-probability evaluations); never a realized "
            "result, never summed with PAPER or ACTUAL")
R_UNKEYED = "NO_MARKET_OR_SIDE_ON_THE_DECISION"
R_NO_PROBABILITY = "NO_PROBABILITY_ON_THE_DECISION"
R_PROBABILITY_NOT_QUALIFIED = "PROBABILITY_NOT_QUALIFIED_AT_THE_DECISION"
R_PROBABILITY_NOT_FRESH = "PROBABILITY_NOT_FRESH_AT_THE_DECISION"
R_PROBABILITY_FRESHNESS_UNKNOWN = \
    "PROBABILITY_FRESHNESS_UNKNOWN_AT_THE_DECISION"
R_NOT_EVALUATED = "EV_NOT_EVALUATED: beyond the request's pricing bound"
R_NO_REFUSAL_CODE = "REFUSED_WITHOUT_A_RECORDED_REFUSAL_CODE"
#: the probability classes, in the order a decision is judged
P_NONE, P_UNQUALIFIED, P_STALE, P_FRESH = (
    "NO_PROBABILITY", "NOT_QUALIFIED", "NOT_FRESH", "FRESH")
#: refusals that say the LANE did not qualify the probability (copied, not
#: imported -- this module reaches no agent; tests/test_opportunity_funnel.py
#: pins them to derek_policy.R_NO_PINNACLE and
#: paper_benchmark.R_PROBABILITY_UNQUALIFIED)
LANE_UNQUALIFIED_REFUSALS = ("NO_QUALIFIED_PINNACLE_PROBABILITY",
                             "PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE")
#: the reading's own staleness / unknown-freshness refusals (derek_policy
#: R_STALE / R_FRESHNESS_UNKNOWN, pinned the same way)
STALE_REFUSAL = "PROBABILITY_EVIDENCE_STALE"
FRESHNESS_UNKNOWN_REFUSAL = "PROBABILITY_EVIDENCE_FRESHNESS_UNKNOWN"


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
    """{cls, qualified, fresh, p, basis, age_s, limit_s, why}: the
    decision's probability and its class AT THE DECISION, from the recorded
    `pinnacle` reading (qualified, qualification, refusal, age_s, limit_s)
    and the decision's own refusals. Qualification failures (absent,
    provenance refused, the lane refused it) are NOT freshness failures;
    only an age beyond the reading's own limit (or an unestablishable
    freshness) is."""
    p, basis = AT.decision_probability(d)
    pin = C.jload(d.get("pinnacle")) or {}
    if not isinstance(pin, dict):
        pin = {}
    age, lim = C.num(pin.get("age_s")), C.num(pin.get("limit_s"))
    out = {"p": p, "basis": basis, "age_s": age, "limit_s": lim}
    refusals = {str(x) for x in (d.get("refusals") or []) if x}
    if d.get("refusal"):
        refusals.add(str(d["refusal"]))
    pref = _s(pin.get("refusal"))
    qual = _s(pin.get("qualification"))

    def res(cls, why):
        return dict(out, cls=cls, qualified=cls in (P_STALE, P_FRESH),
                    fresh=cls == P_FRESH, why=why)
    if p is None or not 0.0 < p < 1.0:
        return res(P_NONE, R_NO_PROBABILITY)
    lane = sorted(refusals & set(LANE_UNQUALIFIED_REFUSALS))
    if lane:
        return res(P_UNQUALIFIED, "%s: %s" % (R_PROBABILITY_NOT_QUALIFIED,
                                              lane[0]))
    if age is not None and lim is not None and age > lim:
        return res(P_STALE, "%s: age %.3fs > %.3fs" % (
            R_PROBABILITY_NOT_FRESH, age, lim))
    if pref == STALE_REFUSAL or qual == "STALE":
        return res(P_STALE, "%s: %s" % (R_PROBABILITY_NOT_FRESH,
                                        pref or qual))
    if pref == FRESHNESS_UNKNOWN_REFUSAL or qual in ("UNKNOWN",
                                                     "CLOCKS_DISAGREE"):
        return res(P_STALE, "%s: %s" % (R_PROBABILITY_FRESHNESS_UNKNOWN,
                                        qual or pref))
    if pin.get("qualified") is not True:
        return res(P_UNQUALIFIED, "%s: %s" % (
            R_PROBABILITY_NOT_QUALIFIED, pref or qual or "NOT_QUALIFIED"))
    return res(P_FRESH, None)


def not_evaluated(d: dict) -> dict:
    """The explicit marker of a FRESH-probability evaluation the request's
    pricing bound left unpriced: UNAVAILABLE / NOT_EVALUATED, never a
    missing probability, never a zero."""
    pf = probability_fresh(d)
    return {"status": UNAVAILABLE, "why": R_NOT_EVALUATED,
            "not_evaluated": True, "probability": pf["p"],
            "probability_basis": pf["basis"],
            "probability_fresh": pf["fresh"],
            "probability_age_s": pf["age_s"],
            "probability_limit_s": pf["limit_s"],
            "executable_ev_usd": None, "positive": None,
            "book_freshness": None}


def evaluate(d: dict, *, book: dict | None, fee_fn) -> dict:
    """The executable net EV of ONE evaluation. `d` is a decision row
    (decision_id, strategy, decided_at, us_market_slug, holding_side,
    probabilities, pinnacle, book_obs_id, decision_book_age_s); `book` is
    {obs_id, observed_at, bids, offers} -- the decision's own observation or
    the latest at or before it -- or None."""
    pf = probability_fresh(d)
    base = {"probability": pf["p"], "probability_basis": pf["basis"],
            "probability_fresh": pf["fresh"],
            "probability_class": pf["cls"],
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

def _why_code(w) -> str:
    return str(w or "UNKNOWN").split(":", 1)[0]


def _units(rows: list) -> tuple:
    """({(key, strategy): unit}, unkeyed rows). `rows` carry the decision
    fields and `ev` (evaluate()'s result, the NOT_EVALUATED marker, or None
    when the caller priced nothing)."""
    units: dict = {}
    unkeyed = []
    for r in sorted(rows, key=lambda r: (C.num(r.get("decided_at")) or 0.0,
                                         str(r.get("decision_id") or ""))):
        k = opportunity_key(r)
        if k is None:
            unkeyed.append(r)
            continue
        s = _s(r.get("strategy")) or "UNRECORDED_STRATEGY"
        u = units.setdefault((k, s), {
            "key": k, "strategy": s, "evaluations": 0, "first_at": None,
            "last_at": None, "entered": False, "blockers_seen": {},
            "latest_blocker": None, "policy_versions": set(),
            "prob_qualified": False, "prob_fresh": False,
            "exec_fresh": False, "best_ev": None, "ev_measured": 0,
            "ev_considered": 0, "ev_not_evaluated": 0, "ev_whys": {}})
        u["evaluations"] += 1
        t = C.num(r.get("decided_at"))
        u["first_at"] = t if u["first_at"] is None else u["first_at"]
        u["last_at"] = t
        b = blocker_of(r)
        if b == ENTERED:
            u["entered"] = True
        else:
            u["blockers_seen"][b] = u["blockers_seen"].get(b, 0) + 1
        u["latest_blocker"] = b
        if r.get("policy_version"):
            u["policy_versions"].add(str(r["policy_version"]))
        # the probability stages come from the decision row itself, never
        # from whether the request priced the evaluation
        pf = probability_fresh(r)
        u["prob_qualified"] = u["prob_qualified"] or pf["qualified"]
        u["prob_fresh"] = u["prob_fresh"] or pf["fresh"]
        ev = r.get("ev")
        if ev is None or ev.get("not_evaluated"):
            if pf["fresh"]:
                u["ev_not_evaluated"] += 1
            else:
                w = _why_code(pf["why"])
                u["ev_whys"][w] = u["ev_whys"].get(w, 0) + 1
            continue
        u["ev_considered"] += 1
        if (ev.get("book_freshness") or {}).get("fresh"):
            u["exec_fresh"] = True
        if ev.get("status") == MEASURED:
            u["ev_measured"] += 1
            x = ev.get("executable_ev_usd")
            if x is not None and (u["best_ev"] is None or x > u["best_ev"]):
                u["best_ev"] = x
        else:
            w = _why_code(ev.get("why"))
            u["ev_whys"][w] = u["ev_whys"].get(w, 0) + 1
    for u in units.values():
        u["binding_blocker"] = ENTERED if u["entered"] else u["latest_blocker"]
        u["near_miss"] = bool(not u["entered"] and u["best_ev"] is not None
                              and u["best_ev"] > 0)
        u["ev_partial"] = u["ev_not_evaluated"] > 0
    return units, unkeyed


def _opportunities(rows: list) -> tuple:
    """({key: opportunity}, unkeyed rows): each opportunity holds its
    per-strategy units."""
    units, unkeyed = _units(rows)
    ops: dict = {}
    for (k, s), u in units.items():
        o = ops.setdefault(k, {"key": k, "units": {}})
        o["units"][s] = u
    for o in ops.values():
        us = list(o["units"].values())
        o["evaluations"] = sum(u["evaluations"] for u in us)
        o["first_at"] = min((u["first_at"] for u in us
                             if u["first_at"] is not None), default=None)
        o["last_at"] = max((u["last_at"] for u in us
                            if u["last_at"] is not None), default=None)
        o["entered_by"] = sorted(u["strategy"] for u in us if u["entered"])
        o["entered"] = bool(o["entered_by"])
        o["bound_by"] = {u["strategy"]: u["binding_blocker"] for u in us
                         if not u["entered"]}
        o["blockers_seen"] = {}
        for u in us:
            for b, n in u["blockers_seen"].items():
                o["blockers_seen"][b] = o["blockers_seen"].get(b, 0) + n
        o["prob_qualified"] = any(u["prob_qualified"] for u in us)
        o["prob_fresh"] = any(u["prob_fresh"] for u in us)
        o["exec_fresh"] = any(u["exec_fresh"] for u in us)
        bests = [u["best_ev"] for u in us if u["best_ev"] is not None]
        o["best_ev"] = max(bests) if bests else None
        never = [u for u in us if not u["entered"]]
        nb = [u["best_ev"] for u in never if u["best_ev"] is not None]
        o["best_ev_unentered"] = max(nb) if nb else None
        o["near_miss"] = bool(not o["entered"] and o["best_ev"] is not None
                              and o["best_ev"] > 0)
        o["ev_not_evaluated"] = sum(u["ev_not_evaluated"] for u in us)
        o["ev_measured"] = sum(u["ev_measured"] for u in us)
        o["ev_considered"] = sum(u["ev_considered"] for u in us)
        o["ev_whys"] = {}
        for u in us:
            for w, n in u["ev_whys"].items():
                o["ev_whys"][w] = o["ev_whys"].get(w, 0) + n
        o["policy_versions"] = sorted({v for u in us
                                       for v in u["policy_versions"]})
    return ops, unkeyed


def _missing_ev_reasons(units: list) -> dict:
    out: dict = {}
    for u in units:
        whys = dict(u["ev_whys"])
        if u["ev_not_evaluated"]:
            whys[_why_code(R_NOT_EVALUATED)] = u["ev_not_evaluated"]
        for w, n in (whys or {"NO_EVALUATION_RECORDED": 1}).items():
            out[w] = out.get(w, 0) + n
    return out


def compute(rows: list, *, book: str = "PAPER", sleeve, strategy,
            top_n: int = 20) -> dict:
    """The unique-opportunity funnel of ONE scope (`strategy` None = every
    strategy of the sleeve, attributed per (opportunity, strategy))."""
    ops, unkeyed = _opportunities(rows)
    scope = C.confidence_scope(sleeve)
    by: dict = {}

    def entry(b):
        return by.setdefault(b, {
            "blocker": b, "book": book, "sleeve": sleeve,
            "confidence_scope": scope,
            "unique_opportunities": 0, "strategy_opportunities": 0,
            "bound_by_strategy": {}, "entered_by_another_strategy": 0,
            "unique_opportunities_ever": 0, "evaluations": 0,
            "near_misses": 0, "missed_executable_ev_usd": 0.0,
            "missed_ev_measured": 0, "missed_ev_unavailable": 0,
            "missed_ev_unavailable_why": {}, "missed_ev_partial": 0,
            "evaluations_not_priced": 0, "_strategies": set(),
            "_pvs": set()})
    for o in ops.values():
        for b, n in o["blockers_seen"].items():
            e = entry(b)
            e["unique_opportunities_ever"] += 1
            e["evaluations"] += n
        bound: dict = {}
        for s, b in o["bound_by"].items():
            bound.setdefault(b, []).append(o["units"][s])
        for b, us in bound.items():
            e = entry(b)
            e["unique_opportunities"] += 1
            e["strategy_opportunities"] += len(us)
            for u in us:
                e["bound_by_strategy"][u["strategy"]] = \
                    e["bound_by_strategy"].get(u["strategy"], 0) + 1
                e["_strategies"].add(u["strategy"])
                e["_pvs"].update(u["policy_versions"])
                e["evaluations_not_priced"] += u["ev_not_evaluated"]
            if o["entered"]:
                e["entered_by_another_strategy"] += 1
            if any(u["near_miss"] for u in us):
                e["near_misses"] += 1
            bests = [u["best_ev"] for u in us if u["best_ev"] is not None]
            if bests:
                e["missed_ev_measured"] += 1
                e["missed_executable_ev_usd"] += max(bests)
                if any(u["ev_partial"] for u in us):
                    e["missed_ev_partial"] += 1
            else:
                e["missed_ev_unavailable"] += 1
                for w, n in _missing_ev_reasons(us).items():
                    e["missed_ev_unavailable_why"][w] = \
                        e["missed_ev_unavailable_why"].get(w, 0) + n
    blockers = []
    for e in by.values():
        e["strategies"] = sorted(e.pop("_strategies"))
        e["policy_versions"] = sorted(e.pop("_pvs"))
        e["re_evaluations"] = e["evaluations"] - e["unique_opportunities_ever"]
        e["evaluations_per_opportunity"] = C.rnd(
            e["evaluations"] / e["unique_opportunities_ever"], 6) \
            if e["unique_opportunities_ever"] else None
        e["missed_executable_ev_usd"] = (
            C.rnd(e["missed_executable_ev_usd"], 6)
            if e["missed_ev_measured"] else None)
        e["missed_executable_ev_why"] = (
            ("PARTIAL: %d of the opportunities it binds had fresh-"
             "probability evaluations left unpriced by the bound; their EV "
             "is the best of the priced ones" % e["missed_ev_partial"])
            if e["missed_ev_measured"] and e["missed_ev_partial"] else
            None if e["missed_ev_measured"] else
            ("UNAVAILABLE: no opportunity it binds has a priced evaluation "
             "(see missed_ev_unavailable_why)") if e["unique_opportunities"]
            else "NOT_BINDING: every opportunity it refused was later "
                 "entered by that strategy or bound by another blocker")
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
    unpriced = [o for o in ops.values()
                if o["prob_fresh"] and o["ev_considered"] == 0]
    stages = {
        "EVALUATED": n_ops,
        "PROBABILITY_QUALIFIED": sum(1 for o in ops.values()
                                     if o["prob_qualified"]),
        "PROBABILITY_FRESH": sum(1 for o in ops.values() if o["prob_fresh"]),
        "EXECUTABLE_FRESH_BOOK": sum(1 for o in ops.values()
                                     if o["exec_fresh"]),
        "POSITIVE_EXECUTABLE_EV": sum(
            1 for o in ops.values()
            if o["best_ev"] is not None and o["best_ev"] > 0),
        "ENTERED": sum(1 for o in ops.values() if o["entered"])}
    n_units = sum(len(o["units"]) for o in ops.values())
    top = sorted(ops.values(), key=lambda o: (-o["evaluations"],
                                              str(o["key"])))[:top_n]
    pvs = sorted({v for o in ops.values() for v in o["policy_versions"]})
    return {
        "version": VERSION, "book": book, "sleeve": sleeve,
        "strategy": strategy or C.ALL_STRATEGIES,
        "strategies": sorted({s for o in ops.values() for s in o["units"]}),
        "policy_versions": pvs,
        "policy_version": pvs[0] if len(pvs) == 1 else None,
        "policy_version_why": (None if len(pvs) == 1 else
                               "NO_DECISION_IN_SCOPE" if not pvs else
                               "MORE_THAN_ONE_POLICY_VERSION_IN_SCOPE"),
        "confidence_scope": scope,
        "key": list(KEY_FIELDS),
        "attribution": ("per (opportunity, strategy): a unit's binding "
                        "blocker is that strategy's latest refusal unless "
                        "that strategy entered; an opportunity counts once "
                        "per blocker that binds any of its units"),
        "totals": {
            "unique_opportunities": n_ops,
            "strategy_opportunities": n_units,
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
            "missed_executable_ev_partial": sum(
                1 for o in measured_never if o["ev_not_evaluated"]),
            "missed_executable_ev_label": EV_LABEL,
            "evaluations_not_priced": sum(o["ev_not_evaluated"]
                                          for o in ops.values()),
            "unkeyed_evaluations": len(unkeyed),
            "unkeyed_why": R_UNKEYED if unkeyed else None},
        "stages": [dict({"stage": s, "unique_opportunities": stages[s]},
                        **({"not_priced_unknown": len(unpriced),
                            "not_priced_why": R_NOT_EVALUATED}
                           if s in PRICED_STAGES else {}))
                   for s in STAGES],
        "blockers": blockers,
        "ranking_basis": ("unique opportunities bound (ties: missed "
                          "executable EV); rows are re-evaluations"),
        "row_ranking_for_comparison": by_rows,
        "most_reevaluated": [dict(
            key_fields(o["key"]), evaluations=o["evaluations"],
            re_evaluations=o["evaluations"] - 1,
            binding_blockers=o["bound_by"], entered_by=o["entered_by"],
            blockers_seen=o["blockers_seen"], entered=o["entered"],
            near_miss=o["near_miss"],
            best_executable_ev_usd=C.rnd(o["best_ev"], 6),
            ev_evaluations_considered=o["ev_considered"],
            ev_evaluations_measured=o["ev_measured"],
            ev_evaluations_not_priced=o["ev_not_evaluated"],
            ev_complete=o["ev_not_evaluated"] == 0,
            ev_unavailable_why=o["ev_whys"] or None,
            strategies=sorted(o["units"]), policy_versions=o["policy_versions"],
            first_at=o["first_at"], last_at=o["last_at"]) for o in top],
        "label": C.LABEL, "authority": C.AUTHORITY}


def blocker_ranking(rows: list, *, sleeve, strategy=None,
                    top: int = 10) -> list:
    """THE ONE RANKING every other report reuses (Slack, Audrey's audit):
    [{blocker, unique_opportunities, strategy_opportunities, evaluations,
    re_evaluations, bound_by_strategy}] over decision rows WITHOUT pricing
    (no EV is claimed). Pure."""
    f = compute([dict(r, ev=None) for r in rows], sleeve=sleeve,
                strategy=strategy, top_n=0)
    return [{k: e[k] for k in (
        "blocker", "unique_opportunities", "strategy_opportunities",
        "unique_opportunities_ever", "evaluations", "re_evaluations",
        "bound_by_strategy")} for e in f["blockers"]
        if e["unique_opportunities"] or e["unique_opportunities_ever"]][:top]
