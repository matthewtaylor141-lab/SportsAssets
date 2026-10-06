"""XAVIER'S RECOMMENDATION FRESHNESS TRUTH (owner P0, 2026-10-04).

THE DEFECT. A paper position was entered, its PinnAPI probability went stale,
EXIT / REDUCE were (correctly) blocked because the valuation was stale -- and
Xavier still visibly recommended HOLD. HOLD survived BY DEFAULT, not because
fresh evidence proved it optimal: the paper selector ranked the only
candidate left (HOLD), `xavier_management.actual_alternatives` forced
`rec = HOLD` on stale evidence and `assessment()` rewrote any stale
discretionary recommendation to HOLD. Every read surface then printed the
stored word "HOLD" as the current recommendation, however old its evidence.

THE RULE (this module; pure, stdlib only, so every read layer can use it
without importing a paper, order or venue module):

  write time  a review on anything but FRESH_CURRENT_PROBABILITY records no
              management action: `recorded_recommendation()` returns
              WAITING_FOR_FRESH_EVIDENCE (a probability exists but is older
              than the limit) or MANAGEMENT_UNAVAILABLE_STALE_INPUT (no
              probability at all). Holding the position is a fact about the
              book, not a recommendation. The safety property is unchanged:
              stale input still blocks every discretionary sale
              (MEASURE_STALE_OR_ABSENT_NO_DISCRETIONARY_SALE) and the
              cost-recovery protection is still maintained.
  read time   `validity()` re-judges every stored recommendation at `now`:
                CURRENT  fresh at write, supporting probability still inside
                         its freshness limit, nothing below happened
                STALE    the supporting probability is now older than the
                         limit (source_at + limit < now), or the row was
                         recorded on non-fresh evidence (a historical HOLD)
                INVALID  the primary valuation changed, the PinnAPI
                         probability changed, the venue mark moved beyond
                         VENUE_MARK_INVALIDATION_USD, the game state changed
                         (the event started after the assessment) or a newer
                         Xavier assessment exists
                WAITING_FOR_FRESH_EVIDENCE / MANAGEMENT_UNAVAILABLE_STALE_INPUT
                         as recorded at write time
              Only CURRENT carries a `current_recommendation`; every other
              state shows the state itself, never the stored action word.
              History is never altered or deleted: the stored word stays in
              `recorded_recommendation`.

THE THRESHOLD IS THE EXISTING ONE. The freshness limit is the limit the
evidence itself recorded (`probability_limit_s` / `pinnacle_limit_s` = the
paper session's `entry.pinnacle_max_age_s`), else the thesis's
`probability_limit_s`, else `workers.ext_pinnacle_loop.PINNACLE_MAX_AGE_S`
(the odds source's 30 s rule). Nothing here defines or changes a freshness,
EV, risk or settlement threshold.

THE ALTERNATIVES. `complete_alternatives()` guarantees the six management
options -- HOLD, EXIT, REDUCE, SAME_VENUE_NETTING, DIRECT_HEDGE,
INDIRECT_HEDGE -- each appear once, each with a value or a NAMED missing-
evidence reason (NO_FRESH_PROBABILITY, NO_SAME_VENUE_OPPOSITE_BOOK,
NO_VERIFIED_HEDGE_IDENTITY, NO_CORRELATION_IDENTITY, ...). Nothing is ranked
around missing evidence silently. Evaluation and display only: no order,
no execution authority.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

VERSION = "XAVIER_FRESHNESS_TRUTH_V1"

E_FRESH = "FRESH_CURRENT_PROBABILITY"
E_STALE = "STALE_ENTRY_TIME_PROBABILITY"
E_NONE = "PROBABILITY_UNAVAILABLE"

#: WRITE-TIME NON-ACTIONS: what a review on non-fresh evidence records
#: instead of an action word (migration 206's CHECK admits them: they are
#: not EXIT / REDUCE / REALLOCATE).
REC_WAITING = "WAITING_FOR_FRESH_EVIDENCE"
REC_UNAVAILABLE = "MANAGEMENT_UNAVAILABLE_STALE_INPUT"
NON_ACTIONS = (REC_WAITING, REC_UNAVAILABLE)

S_CURRENT, S_STALE, S_INVALID = "CURRENT", "STALE", "INVALID"
S_WAITING, S_UNAVAILABLE = REC_WAITING, REC_UNAVAILABLE
S_NONE = "NO_RECOMMENDATION"
#: an OLDER review of a position that has a newer one: history, never the
#: current decision (one CURRENT decision per position; newest wins)
S_SUPERSEDED = "SUPERSEDED"
STATES = (S_CURRENT, S_STALE, S_INVALID, S_WAITING, S_UNAVAILABLE, S_NONE,
          S_SUPERSEDED)
#: THE POSITION'S CURRENT MANAGEMENT STATE (one per position, from its
#: NEWEST review): CURRENT with the action, or -- when that newest evidence
#: is stale, expired or invalidated -- WAITING_FOR_FRESH_EVIDENCE (never an
#: older review's HOLD), or MANAGEMENT_UNAVAILABLE_STALE_INPUT without any
#: probability.
MANAGEMENT_STATES = (S_CURRENT, S_WAITING, S_UNAVAILABLE, S_NONE)

# ── why a recommendation is not CURRENT (named, never silent) ──────────
I_EXPIRED = "SUPPORTING_PROBABILITY_EXCEEDED_FRESHNESS_LIMIT"
I_RECORDED_STALE = "RECORDED_ON_NON_FRESH_EVIDENCE"
I_NO_STAMP = "NO_PROBABILITY_SOURCE_TIMESTAMP_RECORDED"
I_NO_LIMIT = "NO_FRESHNESS_LIMIT_KNOWN"
I_VALUATION = "PRIMARY_VALUATION_CHANGED"
I_FEED = "PINNAPI_PROBABILITY_CHANGED"
I_MARK = "VENUE_MARK_MOVED_BEYOND_THRESHOLD"
I_GAME = "GAME_STATE_CHANGED"
I_SUPERSEDED = "NEWER_XAVIER_ASSESSMENT_EXISTS"
INVALIDATING = (I_VALUATION, I_FEED, I_MARK, I_GAME, I_SUPERSEDED)

#: A venue mark (the best executable exit of the held side) that moved by at
#: least one cent -- the venue tick -- since the assessment invalidates it:
#: the EXIT / REDUCE values were priced on the old book. The same move is a
#: MARKET_EVENT review trigger (paper_xavier._trigger: any best-exit move).
VENUE_MARK_INVALIDATION_USD = 0.01
#: A probability that differs from the supporting one by more than this is
#: a changed valuation (float noise only; any real change counts).
PROBABILITY_CHANGE_EPS = 1e-6

# ── the six management options ───────────────────────────────────────
O_HOLD, O_EXIT, O_REDUCE = "HOLD", "EXIT", "REDUCE"
O_NETTING, O_DIRECT, O_INDIRECT = ("SAME_VENUE_NETTING", "DIRECT_HEDGE",
                                   "INDIRECT_HEDGE")
OPTIONS = (O_HOLD, O_EXIT, O_REDUCE, O_NETTING, O_DIRECT, O_INDIRECT)
#: recorded action names -> the option they evaluate
ALIASES = {"NETTING": O_NETTING, "ACQUIRE_INDIRECT_HEDGE": O_INDIRECT,
           "VERIFIED_HEDGE": O_INDIRECT, "DIRECT_EXIT": O_EXIT}
#: options whose value is priced on the probability (q x p of what is kept)
P_PRICED = (O_HOLD, O_EXIT, O_REDUCE)
M_NO_FRESH_P = "NO_FRESH_PROBABILITY"
M_NO_P = "NO_PROBABILITY"
M_NO_OPPOSITE = "NO_SAME_VENUE_OPPOSITE_BOOK"
M_NO_HEDGE_ID = "NO_VERIFIED_HEDGE_IDENTITY"
M_NO_CORR_ID = "NO_CORRELATION_IDENTITY"
M_KALSHI = "KALSHI_NOT_CONNECTED"
M_NOT_EVALUATED = "NOT_EVALUATED_ON_THIS_PATH"
M_NO_REASON = "NOT_EVALUATED_NO_REASON_RECORDED"
DEFAULT_MISSING = {
    O_HOLD: M_NOT_EVALUATED, O_EXIT: M_NOT_EVALUATED,
    O_REDUCE: M_NOT_EVALUATED, O_NETTING: M_NO_OPPOSITE,
    O_DIRECT: M_NO_HEDGE_ID, O_INDIRECT: M_NO_CORR_ID}
DEFAULT_WHY = {
    O_NETTING: ("no opposite book on the same venue was evaluated for this "
                "position; netting is not valued"),
    O_DIRECT: ("a direct hedge needs a verified identity of the same event, "
               "settlement and the opposite side (bettor_venue_native_"
               "identity); none is established on this path, so it is not "
               "valued and never assumed"),
    O_INDIRECT: ("an indirect / correlated hedge needs an established event "
                 "+ settlement + side identity; none is established on this "
                 "path, so it is not valued and never assumed (titles that "
                 "look alike are never grouped)")}


#: THE LATEST VALUATION OF EACH PAPER GROUP'S OWN CONTRACT: the same
#: contract identity the review's measure reads (agents.paper_benchmark.
#: xavier_measure: slug, the held side's buy intent, the entry valuation's
#: payout event and complement flag), decided after $2 (epoch). Read only.
LATEST_VALUATION_SQL = """
    SELECT DISTINCT ON (o.group_id) o.group_id, v.id, v.probability,
           v.observed_at
      FROM paper_orders o
      JOIN paper_decisions d ON d.decision_id = o.decision_id
      JOIN external_valuations c ON c.id = d.valuation_id
      JOIN external_valuations v
        ON v.us_market_slug = o.us_market_slug
       AND v.buy_intent = CASE WHEN o.holding_side = 'LONG'
                               THEN 'ORDER_INTENT_BUY_LONG'
                               ELSE 'ORDER_INTENT_BUY_SHORT' END
       AND v.payout_event = c.payout_event
       AND v.payout_is_complement = c.payout_is_complement
       AND v.probability IS NOT NULL
       AND v.decided_at > to_timestamp($2)
     WHERE o.group_id = ANY($1::text[]) AND o.role = 'ENTRY'
     ORDER BY o.group_id, v.decided_at DESC, v.id DESC"""
CONTEXT_VALUATION_LOOKBACK_S = 3600.0


def _ep(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _j(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def default_limit_s() -> float | None:
    """THE EXISTING freshness limit of the odds source (the Pinnacle / PinnAPI
    30 s rule, workers.ext_pinnacle_loop.PINNACLE_MAX_AGE_S), read -- never
    defined -- here, and only when that module is already loaded in this
    process: this module imports nothing, so the read paths that use it
    (position rooms) keep their import graph free of the workers. None
    otherwise -- the reader then supplies the limit (the thesis's, the
    session config's) or the recommendation is STALE by
    NO_FRESHNESS_LIMIT_KNOWN, never assumed current."""
    import sys
    mod = sys.modules.get("sportsassets.workers.ext_pinnacle_loop")
    try:
        return None if mod is None else float(mod.PINNACLE_MAX_AGE_S)
    except (AttributeError, TypeError, ValueError):
        return None


# ═════════════════════════════════════════════════════════════════════
# WRITE TIME
# ═════════════════════════════════════════════════════════════════════

def recorded_recommendation(*, evidence_state, selected):
    """WHAT A REVIEW RECORDS AS ITS RECOMMENDATION. A fresh review records
    its selection; anything else records the named non-action -- never a
    HOLD that only survived because every alternative was blocked."""
    if evidence_state == E_FRESH:
        return selected
    return REC_UNAVAILABLE if evidence_state not in (E_STALE,) else REC_WAITING


def write_state(*, evidence_state, recommendation) -> str:
    """The recommendation state at the instant of writing."""
    if recommendation == REC_UNAVAILABLE:
        return S_UNAVAILABLE
    if recommendation == REC_WAITING or evidence_state != E_FRESH:
        return S_WAITING if evidence_state == E_STALE else S_UNAVAILABLE
    return S_CURRENT if recommendation is not None else S_NONE


def valuation_block(evidence: dict | None, *, assessed_at=None,
                    limit_s=None) -> dict:
    """THE VALUATION A RECOMMENDATION STANDS ON: source, source timestamp,
    observation (receipt) timestamp, age, freshness limit, expiry and the
    valuation id / hash -- from a `paper_xavier.probability_evidence` result
    or a review's `measure`. Pure. A missing stamp is null with its basis,
    never invented; a source stamp is derived from assessed_at - age only
    when that is all a historical row recorded (said in `source_at_basis`)."""
    e = evidence or {}
    src = _ep(e.get("probability_source_at"))
    basis = "RECORDED_PROBABILITY_SOURCE_AT" if src is not None else None
    if src is None:
        for k in ("pinnacle_at", "entry_pinnacle_at"):
            if _ep(e.get(k)) is not None:
                src, basis = _ep(e.get(k)), "RECORDED_%s" % k.upper()
                break
    rcv = _ep(e.get("probability_received_at"))
    if rcv is None:
        rcv = _ep(e.get("pinnacle_received_at")) or _ep(
            e.get("entry_pinnacle_received_at"))
    age = e.get("probability_age_s")
    if age is None:
        age = e.get("pinnacle_age_s")
    age = _ep(age)
    at = _ep(assessed_at)
    if src is None and age is not None and at is not None:
        src = round(at - age, 3)
        basis = "DERIVED_ASSESSED_AT_MINUS_RECORDED_AGE"
    lim = _ep(e.get("probability_limit_s")) or _ep(e.get("pinnacle_limit_s"))
    lbasis = "RECORDED_WITH_THE_EVIDENCE" if lim is not None else None
    if lim is None and _ep(limit_s) is not None:
        lim, lbasis = _ep(limit_s), "SUPPLIED_BY_THE_READER (thesis / config)"
    if lim is None:
        lim = default_limit_s()
        lbasis = ("workers.ext_pinnacle_loop.PINNACLE_MAX_AGE_S"
                  if lim is not None else None)
    p = e.get("probability")
    if p is None:
        p = e.get("p")
    feed = e.get("feed") if isinstance(e.get("feed"), dict) else {}
    ident = {"source": e.get("probability_source") or e.get("source"),
             "source_at": src, "probability": None if p is None else float(p),
             "valuation_id": e.get("valuation_id"),
             "feed_source_change_ms": feed.get("source_change_ms"),
             "feed_epoch": feed.get("epoch")}
    pp = e.get("p_pinnacle")
    return {"source": ident["source"],
            "evidence_state": e.get("evidence_state"),
            "probability": ident["probability"],
            # the PRIMARY valuation's own probability (de-vigged Pinnacle)
            # when the measure blends it with another model
            "pinnacle_probability": None if pp is None else float(pp),
            "source_at": src, "source_at_basis": basis or "NOT_RECORDED",
            "observed_at": rcv,
            "age_at_assessment_s": None if age is None else round(age, 3),
            "limit_s": lim, "limit_basis": lbasis or "NOT_KNOWN",
            "expires_at": (None if src is None or lim is None
                           else round(src + lim, 3)),
            "valuation_id": e.get("valuation_id"),
            # which table the id names: external_valuations (default) or
            # xavier_probability_snapshots (migration 303)
            "valuation_store": e.get("valuation_store")
            or ("external_valuations" if e.get("valuation_id") is not None
                else None),
            "valuation_version": VERSION,
            "valuation_hash": hashlib.sha256(json.dumps(
                ident, sort_keys=True, default=str).encode()).hexdigest()[:24]}


# ═════════════════════════════════════════════════════════════════════
# READ TIME
# ═════════════════════════════════════════════════════════════════════

def validity(*, recommendation, evidence_state, valuation: dict | None,
             now: float, assessed_at=None, newer_assessment_id=None,
             latest_valuation: dict | None = None, feed_change_at=None,
             mark_at_assessment=None, mark_now=None,
             event_start_at=None) -> dict:
    """THE STATE OF ONE STORED RECOMMENDATION AT `now`. Pure. Only CURRENT
    yields a `current_recommendation`; `display_recommendation` is the
    action word only when CURRENT and the state otherwise."""
    v = dict(valuation or {})
    now = float(now)
    at = _ep(assessed_at)
    src, lim, exp = _ep(v.get("source_at")), _ep(v.get("limit_s")), _ep(
        v.get("expires_at"))
    if exp is None and src is not None and lim is not None:
        exp = src + lim
    age_now = None if src is None else round(now - src, 3)
    reasons: list = []
    detail: dict[str, Any] = {}
    rec = recommendation
    if newer_assessment_id:
        # ONE CURRENT DECISION PER POSITION: a review with a newer one is
        # history (SUPERSEDED), whatever its own evidence said
        state = S_SUPERSEDED
        reasons.append(I_SUPERSEDED)
        detail["superseded_by"] = newer_assessment_id
    elif rec in NON_ACTIONS:
        state = rec
    elif evidence_state != E_FRESH:
        # a stored action word on non-fresh evidence (a historical HOLD):
        # shown as STALE, never as the current recommendation
        state = S_STALE if rec is not None else (
            S_WAITING if evidence_state == E_STALE else S_UNAVAILABLE)
        if rec is not None:
            reasons.append(I_RECORDED_STALE)
    elif rec is None:
        state = S_NONE
    else:
        lv = latest_valuation or {}
        lv_at = _ep(lv.get("observed_at"))
        lv_p = lv.get("probability")
        # compare like with like: the primary valuation's own probability
        # (a blended measure records it as pinnacle_probability; a blend
        # without it is not compared, rather than falsely invalidated)
        base_p = v.get("pinnacle_probability")
        if base_p is None and "BLEND" not in str(v.get("source") or ""):
            base_p = v.get("probability")
        if lv_at is not None and src is not None and lv_at > src + 1e-6 \
                and not (lv.get("id") == v.get("valuation_id")
                         and (v.get("valuation_store")
                              or "external_valuations")
                         == "external_valuations") \
                and lv_p is not None and base_p is not None \
                and abs(float(lv_p) - float(base_p)) \
                > PROBABILITY_CHANGE_EPS:
            reasons.append(I_VALUATION)
            detail["latest_valuation"] = {"id": lv.get("id"),
                                          "observed_at": lv_at,
                                          "probability": float(lv_p)}
        fc = _ep(feed_change_at)
        if fc is not None and src is not None and fc > src + 1e-3:
            reasons.append(I_FEED)
            detail["feed_change_at"] = fc
        m0, m1 = _ep(mark_at_assessment), _ep(mark_now)
        if m0 is not None and m1 is not None and \
                abs(m1 - m0) >= VENUE_MARK_INVALIDATION_USD - 1e-9:
            reasons.append(I_MARK)
            detail["mark"] = {"at_assessment": m0, "now": m1,
                              "threshold_usd": VENUE_MARK_INVALIDATION_USD}
        es = _ep(event_start_at)
        if es is not None and at is not None and at < es <= now:
            reasons.append(I_GAME)
            detail["event_started_at"] = es
        if src is None:
            reasons.append(I_NO_STAMP)
        elif lim is None:
            reasons.append(I_NO_LIMIT)
        elif exp is not None and now > exp:
            reasons.append(I_EXPIRED)
        if any(r in INVALIDATING for r in reasons):
            state = S_INVALID
        elif reasons:
            state = S_STALE
        else:
            state = S_CURRENT
    current = rec if state == S_CURRENT else None
    if state == S_SUPERSEDED:
        mstate = S_SUPERSEDED
    elif state == S_CURRENT:
        mstate = S_CURRENT
    elif evidence_state == E_NONE or state == S_UNAVAILABLE:
        mstate = S_UNAVAILABLE
    elif state == S_NONE:
        mstate = S_NONE
    else:                       # STALE / INVALID / WAITING
        mstate = S_WAITING
    return {"recommendation_state": state,
            "management_state": mstate,
            "superseded_by": detail.get("superseded_by"),
            "current_recommendation": current,
            "recorded_recommendation": rec,
            "display_recommendation": current if current else state,
            "is_current": state == S_CURRENT,
            "reasons": reasons, "detail": detail or None,
            "evidence_state": evidence_state,
            "valuation": dict(v, expires_at=None if exp is None
                              else round(exp, 3), age_now_s=age_now,
                              seconds_to_expiry=(None if exp is None
                                                 else round(exp - now, 3))),
            "evaluated_at": now, "version": VERSION,
            "rule": ("CURRENT only while the supporting probability is inside "
                     "its own freshness limit and no newer valuation, "
                     "PinnAPI change, venue-mark move (>= %.2f USD), game-"
                     "state change or newer assessment exists; a stored "
                     "recommendation is never shown as current otherwise"
                     % VENUE_MARK_INVALIDATION_USD)}


def of_assessment(a: dict, *, now: float, limit_s=None, **ctx) -> dict:
    """validity() of one xavier_management_assessments row (its `valuation`
    column when migration 222 wrote it; derived from probability_age_s and
    assessed_at for older rows, the derivation stated)."""
    val = _j(a.get("valuation"))
    if not isinstance(val, dict) or not val:
        val = valuation_block({
            "probability_source": a.get("probability_source"),
            "probability_age_s": a.get("probability_age_s"),
            "probability": a.get("probability"),
            "evidence_state": a.get("evidence_state")},
            assessed_at=a.get("assessed_at"), limit_s=limit_s)
    return validity(recommendation=a.get("recommendation"),
                    evidence_state=a.get("evidence_state"), valuation=val,
                    now=now, assessed_at=a.get("assessed_at"), **ctx)


def of_review(r: dict, *, now: float, limit_s=None, **ctx) -> dict:
    """validity() of one paper_xavier_reviews row, from its own `measure`."""
    m = _j(r.get("measure")) or {}
    if not isinstance(m, dict):
        m = {}
    sel = _j(r.get("selection")) or {}
    val = sel.get("valuation") if isinstance(sel, dict) else None
    if not isinstance(val, dict) or not val:
        val = valuation_block(m, assessed_at=r.get("reviewed_at"),
                              limit_s=limit_s)
    return validity(recommendation=r.get("recommendation"),
                    evidence_state=m.get("evidence_state") or (
                        E_NONE if m.get("p") is None else
                        E_STALE if m.get("stale") else None),
                    valuation=val, now=now, assessed_at=r.get("reviewed_at"),
                    mark_at_assessment=m.get("best_exit_at_review"), **ctx)


def decision(block: dict, *, review_id=None, reviewed_at=None) -> dict:
    """EVERY XAVIER DECISION, ONE SHAPE: review id and time, valuation id /
    version / hash and timestamp, age now, the freshness limit, the states
    and `superseded_by` (the newer review id) when it is history."""
    v = block.get("valuation") or {}
    return {"review_id": review_id,
            "review_timestamp": _ep(reviewed_at),
            "valuation_id": v.get("valuation_id"),
            "valuation_version": v.get("valuation_version"),
            "valuation_hash": v.get("valuation_hash"),
            "valuation_source": v.get("source"),
            "valuation_timestamp": v.get("source_at"),
            "valuation_observed_at": v.get("observed_at"),
            "age_seconds": v.get("age_now_s"),
            "age_at_review_seconds": v.get("age_at_assessment_s"),
            "freshness_limit": v.get("limit_s"),
            "expires_at": v.get("expires_at"),
            "recommendation_state": block["recommendation_state"],
            "management_state": block["management_state"],
            "current_recommendation": block["current_recommendation"],
            "recorded_recommendation": block["recorded_recommendation"],
            "is_current": block["is_current"],
            "superseded_by": block.get("superseded_by"),
            "reasons": block.get("reasons") or []}


def gated(row: dict, block: dict) -> dict:
    """A COPY OF A READ ROW WITH ITS RECOMMENDATION GATED: `recommendation`
    becomes the current action word only when CURRENT, the state otherwise;
    the stored word is kept in `recorded_recommendation`."""
    out = dict(row)
    out["recorded_recommendation"] = block["recorded_recommendation"]
    out["recommendation"] = block["display_recommendation"]
    out["recommendation_state"] = block["recommendation_state"]
    out["management_state"] = block["management_state"]
    out["superseded_by"] = block.get("superseded_by")
    out["current_recommendation"] = block["current_recommendation"]
    out["freshness"] = block
    out["decision"] = decision(
        block, review_id=row.get("review_id") or row.get("assessment_id"),
        reviewed_at=row.get("reviewed_at") or row.get("assessed_at"))
    return out


def _order_key(r: dict):
    t = _ep(r.get("reviewed_at") if r.get("reviewed_at") is not None
            else r.get("assessed_at"))
    return (t if t is not None else float("-inf"),
            str(r.get("review_id") or r.get("assessment_id") or ""))


def current_decisions(rows, *, now: float, of=None, group_key="group_id",
                      **kw) -> dict:
    """ONE CURRENT DECISION PER POSITION (pure). Rows (reviews or
    assessments, any order) are grouped by position; the NEWEST (time, then
    id) is the candidate -- its own state decides CURRENT or
    WAITING_FOR_FRESH_EVIDENCE / MANAGEMENT_UNAVAILABLE_STALE_INPUT (an
    older fresh HOLD never stands in for a stale newest review); every
    older one is SUPERSEDED with `superseded_by` = the newest's id.
    {group: {"current": gated row, "superseded": [gated rows]}}."""
    of = of or of_review
    by: dict = {}
    for r in rows or []:
        r = dict(r)
        by.setdefault(r.get(group_key), []).append(r)
    out = {}
    for g, rs in by.items():
        rs.sort(key=_order_key, reverse=True)
        head = rs[0]
        hid = head.get("review_id") or head.get("assessment_id")
        cur = gated(head, of(head, now=now, **kw))
        old = [gated(r, of(r, now=now, newer_assessment_id=hid, **kw))
               for r in rs[1:]]
        out[g] = {"current": cur, "superseded": old}
    return out


# ═════════════════════════════════════════════════════════════════════
# THE ALTERNATIVES: every option valued or named
# ═════════════════════════════════════════════════════════════════════

def complete_alternatives(alts, *, evidence_state) -> list:
    """EVERY MANAGEMENT OPTION, ONCE, WITH A VALUE OR A NAMED REASON. Pure
    and idempotent. Recorded entries are kept (their own action names and
    blockers); each gains `option` (the canonical option it evaluates) and
    `missing_evidence` (null only when it is valued on current evidence).
    A canonical option the record does not carry is appended unvalued with
    its named reason. Options priced on the probability carry
    NO_FRESH_PROBABILITY whenever the evidence is not fresh -- their value
    (if any) is entry-time, never current, and they are not rankable."""
    fresh = evidence_state == E_FRESH
    out, seen = [], set()
    for a in (_j(alts) or []):
        if not isinstance(a, dict):
            continue
        x = dict(a)
        act = x.get("action")
        opt = x.get("option") or ALIASES.get(act, act)
        x["option"] = opt
        if opt in P_PRICED and not fresh:
            x["missing_evidence"] = (M_NO_P if evidence_state == E_NONE
                                     else M_NO_FRESH_P)
            x["rankable"] = False
            x["value_is_current"] = False
        elif x.get("value_usd") is None:
            x["missing_evidence"] = (x.get("missing_evidence")
                                     or x.get("blocker") or M_NO_REASON)
        else:
            x.setdefault("missing_evidence", None)
        out.append(x)
        seen.add(opt)
    for opt in OPTIONS:
        if opt in seen:
            continue
        reason = DEFAULT_MISSING[opt]
        if opt in P_PRICED and not fresh:
            reason = M_NO_P if evidence_state == E_NONE else M_NO_FRESH_P
        out.append({"action": opt, "option": opt, "rankable": False,
                    "value_usd": None, "blocker": reason,
                    "missing_evidence": reason,
                    "why": DEFAULT_WHY.get(opt) or (
                        "this option was not evaluated on this path")})
    return out


def missing_report(alts) -> list:
    """[(option, value_usd, missing_evidence)] -- a test / audit helper:
    every entry must carry a value or a named reason."""
    return [(a.get("option") or a.get("action"), a.get("value_usd"),
             a.get("missing_evidence") or a.get("blocker"))
            for a in (_j(alts) or []) if isinstance(a, dict)]
