"""PROBABILITY, EXECUTABLE EV AND DIGITAL-TWIN EVIDENCE FROM PRODUCTION RECORDS
(completion readiness, read-only).

  probability   every settled market's FIRST ENTER decision per strategy x
                side in the window: BETTOR's probability as the decision
                used it (economics.probability, else p_blended, else the
                Pinnacle probability -- the source kept), against the market
                prior (the mid of the venue book the decision recorded,
                oriented to the held side), scored by log loss on the settled
                outcome; independence is the EVENT (premap event_slug), and
                the improvement's lower bound is clustered by event. No
                parameter is fitted here, so there is nothing to tune and no
                holdout to touch. probability_authority.choose_probability
                decides the authority from this aggregate.
  executable_ev every recent ENTER decision priced at its own recorded book:
                p_used is what the probability authority permits (the market
                prior unless BETTOR_RESIDUAL_ALLOWED), the executable price
                the oriented offer, the fee from BETTOR's published schedule
                (a refusal excludes the decision, never a zero fee).
                ev_authority.evaluate_all_in_ev per decision; the portfolio
                verdict is ELIGIBLE only when the event-clustered lower bound
                of the per-decision lower bounds is positive. Otherwise CASH.
  twin          the repaired IOC fill replay (completion.fill_replay) over marketable PAPER
                orders that reached a terminal state AFTER the diagnosis
                window (fresh: the semantics were derived before these
                orders existed), against their recorded PAPER outcomes.
                Each order carries the books of its [eligible, expires]
                window AND the last book observed at or before eligible_at:
                the repaired replay never reads the latter (it skips every
                book before eligibility); the OPTIMISTIC diagnosis needs it,
                because the package twin crossed on exactly that book.
                Without it (before RC6) every optimistic diagnosis saw no
                pre-activation book and named every PAPER fill
                CANCELLED_BEFORE_FIRST_ELIGIBLE_BOOK (production RC5: 7 of 7).

  labels        a position's settlement is its LATEST version
                (paper_settlements is versioned; a correction appends v2).
                Reading every version kept a superseded WON / LOST as a
                label after a correction to VOID / a venue price, and
                dropped the market when v1 and v2 disagreed.

Pure builders over plain rows, plus the bounded SELECTs that produce them.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from . import ev_authority as EVA
from . import probability_authority as PA
from . import fill_replay as TW

#: the Profitability Stack V1 twin receipt's window end (run 37643890985):
#: orders after it are FRESH for the repaired twin's agreement
TWIN_DIAGNOSIS_WINDOW_END = datetime(2026, 10, 7, 14, 56, 26,
                                     tzinfo=timezone.utc).timestamp()
TWIN_MIN_FRESH_ORDERS = 100
PROB_MIN_EVENTS = 100
Z = 1.645

BOOK_TOP = """
  (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
  (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask"""

#: (production 088af82: 43 s -- a nested loop over paper_decisions and one
#: sequential us_premap probe per row) the recent decisions first, labels
#: only for their slugs, and ONE hashed us_premap read for the event slugs
PROB_SQL = """
WITH d0 AS (
  SELECT DISTINCT ON (d.strategy, d.us_market_slug, d.holding_side)
         d.decision_id, extract(epoch FROM d.decided_at) at, d.strategy,
         d.us_market_slug slug, d.holding_side side, d.p_blended p_blend,
         d.p_pinnacle p_pin, (d.economics->>'probability')::float8 p_used,
         d.book_obs_id
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
     AND d.decided_at > now() - make_interval(days => $1)
   ORDER BY d.strategy, d.us_market_slug, d.holding_side, d.decided_at),
slugs AS (SELECT DISTINCT slug FROM d0),
ps AS (
  SELECT DISTINCT ON (position_key, settlement_event_key)
         us_market_slug, holding_side, outcome, payout_per_contract,
         settled_at
    FROM paper_settlements
   WHERE us_market_slug IN (SELECT slug FROM slugs)
   ORDER BY position_key, settlement_event_key, version DESC),
lab AS (
  SELECT us_market_slug slug,
         CASE WHEN holding_side='LONG' THEN payout_per_contract
              ELSE 1 - payout_per_contract END::float8 y, settled_at at
    FROM ps WHERE outcome IN ('WON','LOST')
  UNION ALL
  SELECT us_market_slug, CASE WHEN buy_intent ILIKE '%SHORT%' THEN 1 - outcome
                              ELSE outcome END::float8, outcome_at
    FROM external_valuations WHERE outcome_known AND outcome IN (0,1)
     AND us_market_slug IN (SELECT slug FROM slugs)),
lab2 AS (SELECT slug, min(y) y_long, min(at) settled_at
           FROM lab GROUP BY slug HAVING min(y) = max(y)),
pm AS (SELECT DISTINCT ON (market_slug) market_slug, event_slug
         FROM us_premap WHERE market_slug IN (SELECT slug FROM lab2)
        ORDER BY market_slug)
SELECT d.*, l.y_long, pm.event_slug,""" + BOOK_TOP + """
  FROM d0 d JOIN lab2 l ON l.slug = d.slug
  LEFT JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
                                     AND o.error IS NULL
  LEFT JOIN pm ON pm.market_slug = d.slug
 LIMIT $2"""

#: THE DECLARED WINDOW IS PRICED WHOLE (RC6.2 p-evcontrols). The bound was
#: 2,000 decisions: production completion.json (pm-acceptance 37888018192)
#: priced 1,992 + 8 dropped = 2,000 exactly, against about 4,300 ENTER
#: decisions in its 7-day window (research-sql 37943749921 §6), so
#: `window_days: 7` described the newest ~2.4 days -- one strategy. The bound
#: is now a safety stop well above the window's volume (production's busiest
#: day, 2026-10-05: 1,251 ENTER decisions), the pricing runs on the CPU lane,
#: and a read the bound cuts is CASH by name (EV_READ_TRUNCATED), never a
#: verdict over a subset.
EV_MAX_DECISIONS = 20000
EV_READ_TRUNCATED = "READ_TRUNCATED_WINDOW_NOT_PRICED_WHOLE"

#: one hashed us_premap read for the event slugs (was a sequential probe
#: per decision row)
EV_SQL = """
WITH d0 AS (
  SELECT d.decision_id, d.decided_at, d.strategy, d.us_market_slug,
         d.holding_side, d.book_obs_id
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
     AND d.decided_at > now() - make_interval(days => $1)
   ORDER BY d.decided_at DESC, d.decision_id DESC
   LIMIT $2),
pm AS (SELECT DISTINCT ON (market_slug) market_slug, event_slug
         FROM us_premap
        WHERE market_slug IN (SELECT us_market_slug FROM d0)
        ORDER BY market_slug)
SELECT d.decision_id, extract(epoch FROM d.decided_at) at, d.strategy,
       d.us_market_slug slug, d.holding_side side, pm.event_slug,""" + BOOK_TOP + """
  FROM d0 d
  LEFT JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
                                     AND o.error IS NULL
  LEFT JOIN pm ON pm.market_slug = d.us_market_slug
 ORDER BY d.decided_at DESC, d.decision_id DESC"""

TWIN_SQL = """
SELECT json_build_object(
  'order_id', o.order_id, 'role', o.role, 'tif', o.time_in_force,
  'direction', o.direction, 'side', o.holding_side, 'slug', o.us_market_slug,
  'state', o.state, 'qty', o.qty, 'limit', o.limit_price,
  'created', extract(epoch FROM o.created_at),
  'eligible', extract(epoch FROM o.eligible_at),
  'expires', extract(epoch FROM o.expires_at),
  'terminal', extract(epoch FROM o.terminal_at),
  'fills', (SELECT json_agg(json_build_object('at', extract(epoch FROM pf.filled_at),
                   'qty', pf.qty, 'price', pf.price) ORDER BY pf.filled_at)
              FROM paper_fills pf WHERE pf.order_id = o.order_id),
  'books', (SELECT json_agg(json_build_object('at', extract(epoch FROM b.observed_at),
                   'bids', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.bids)='array'
                                       THEN b.bids ELSE '[]' END) l),
                   'asks', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.offers)='array'
                                       THEN b.offers ELSE '[]' END) l))
                   ORDER BY b.observed_at)
              FROM paper_book_observations b
             WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
               AND ((b.observed_at >= o.eligible_at
                     AND b.observed_at <= o.expires_at)
                    OR b.obs_id = (
                      SELECT p.obs_id FROM paper_book_observations p
                       WHERE p.error IS NULL
                         AND p.us_market_slug = o.us_market_slug
                         AND p.observed_at <= o.eligible_at
                       ORDER BY p.observed_at DESC LIMIT 1))))::text AS j
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp($1)
 ORDER BY o.eligible_at
 LIMIT $2"""


def orient(bid, ask, side):
    if bid is None or ask is None:
        return None
    bid, ask = float(bid), float(ask)
    if str(side).upper() == "SHORT":
        bid, ask = 1.0 - ask, 1.0 - bid
    if not (0.0 < bid < ask < 1.0):
        return None
    return bid, ask


def _event(r) -> str:
    return str(r.get("event_slug") or r.get("slug") or "")


def _ll(p, y) -> float:
    p = min(1 - 1e-9, max(1e-9, float(p)))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def cluster_mean_bounds(values, clusters, z=Z) -> dict:
    """Mean of `values` with an event-clustered standard error (cluster
    sums, n/(n-1) correction). None bounds with fewer than 2 clusters."""
    by: dict = {}
    for v, c in zip(values, clusters):
        by.setdefault(c, []).append(float(v))
    n = sum(len(v) for v in by.values())
    if n == 0:
        return {"n": 0, "clusters": 0, "mean": None, "lb": None, "ub": None,
                "se": None}
    mean = sum(sum(v) for v in by.values()) / n
    g = len(by)
    if g < 2:
        return {"n": n, "clusters": g, "mean": mean, "lb": None, "ub": None,
                "se": None}
    ss = sum((sum(v) - len(v) * mean) ** 2 for v in by.values())
    se = math.sqrt(ss * g / (g - 1)) / n
    return {"n": n, "clusters": g, "mean": mean, "lb": mean - z * se,
            "ub": mean + z * se, "se": se}


def ece(ps, ys, bins=10):
    if not ps:
        return None
    tot, err = len(ps), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(ps)
               if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if idx:
            err += len(idx) / tot * abs(
                sum(ps[i] for i in idx) / len(idx)
                - sum(ys[i] for i in idx) / len(idx))
    return err


def probability_block(rows: list) -> dict:
    """Aggregate OOS evidence -> ForecastEvidence -> choose_probability."""
    kept, dropped = [], {}

    def drop(w):
        dropped[w] = dropped.get(w, 0) + 1
    for r in rows:
        b = orient(r.get("bid"), r.get("ask"), r.get("side"))
        if b is None:
            drop("NO_TWO_SIDED_VENUE_BOOK")
            continue
        if r.get("y_long") is None:
            drop("NO_SETTLED_OUTCOME")
            continue
        if r.get("p_used") is not None:
            p, src = float(r["p_used"]), "ECONOMICS_PROBABILITY"
        elif r.get("p_blend") is not None:
            p, src = float(r["p_blend"]), "P_BLENDED"
        elif r.get("p_pin") is not None:
            p, src = float(r["p_pin"]), "P_PINNACLE_FALLBACK"
        else:
            drop("NO_DECISION_PROBABILITY")
            continue
        y = float(r["y_long"])
        if str(r.get("side")).upper() == "SHORT":
            y = 1.0 - y
        pm = 0.5 * (b[0] + b[1])
        kept.append({"p": p, "pm": pm, "y": y, "event": _event(r),
                     "src": src})
    events = sorted({k["event"] for k in kept})
    # delta = BETTOR loss - MARKET loss (negative is BETTOR better)
    deltas = [_ll(k["p"], k["y"]) - _ll(k["pm"], k["y"]) for k in kept]
    b = cluster_mean_bounds(deltas, [k["event"] for k in kept])
    improvement_lb = None if b["ub"] is None else -b["ub"]
    cal = ece([k["p"] for k in kept], [k["y"] for k in kept])
    ev = PA.ForecastEvidence(
        market_prior=(sum(k["pm"] for k in kept) / len(kept)) if kept
        else None,
        bettor_raw=(sum(k["p"] for k in kept) / len(kept)) if kept else None,
        bettor_calibrated=None,
        independent_events=len(events),
        calibration_error=cal,
        oos_logloss_delta_vs_market=b["mean"],
        oos_logloss_delta_ci_low=b["lb"],
        residual_alpha=None, residual_signal=None,
        residual_improvement_lb=None,
        evidence_complete=bool(kept))
    dec = PA.choose_probability(ev, minimum_events=PROB_MIN_EVENTS)
    srcs: dict = {}
    for k in kept:
        srcs[k["src"]] = srcs.get(k["src"], 0) + 1
    return {"authority": dec.authority, "reason": dec.reason,
            "evaluated_on": "AGGREGATE_OUT_OF_SAMPLE_EVIDENCE",
            "decisions_scored": len(kept), "dropped": dropped,
            "independent_events": len(events),
            "probability_sources": srcs,
            "bettor_logloss_minus_market": b["mean"],
            "delta_ci90": [b["lb"], b["ub"]],
            "improvement_lower_bound": improvement_lb,
            "bettor_calibration_error_ece10": cal,
            "residual_model": "NONE_IN_PRODUCTION",
            "edge_claimed": False,
            "sample_note": ("the FIRST settled ENTER per strategy x market x "
                            "side in the window: decisions BETTOR selected, "
                            "not a frozen forward holdout -- evidence, never "
                            "authority; the readiness gate takes no "
                            "probability edge unless the authority is "
                            "BETTOR_RESIDUAL_ALLOWED"),
            "minimum_events": PROB_MIN_EVENTS,
            "fitted_parameters": 0,
            "holdout_touched": False}


def ev_block(rows: list, *, authority: str, fee_fn) -> dict:
    """Per-decision all-in executable EV under the probability authority;
    the portfolio verdict from the event-clustered lower bound."""
    out, dropped = [], {}

    def drop(w):
        dropped[w] = dropped.get(w, 0) + 1
    for r in rows:
        b = orient(r.get("bid"), r.get("ask"), r.get("side"))
        if b is None:
            drop("NO_TWO_SIDED_VENUE_BOOK")
            continue
        if authority == "ABSTAIN":
            drop("PROBABILITY_AUTHORITY_ABSTAINS")
            continue
        if authority != "MARKET_PRIOR_ONLY":
            # a residual authority needs its own measured uncertainty; none
            # is measured in production, so nothing is priced on it
            drop("RESIDUAL_UNCERTAINTY_UNMEASURED")
            continue
        p_used = 0.5 * (b[0] + b[1])
        fee = fee_fn(b[1], float(r.get("at") or 0))
        if fee is None:
            drop("FEE_UNMEASURED")
            continue
        d = EVA.evaluate_all_in_ev(EVA.ExecutableEconomics(
            p_used=p_used, executable_price=b[1], fee_per_contract=fee))
        out.append({"event": _event(r), "net": d.net_ev_per_contract,
                    "lb": d.lower_bound_ev_per_contract,
                    "verdict": d.verdict})
    bnd = cluster_mean_bounds([x["lb"] for x in out],
                              [x["event"] for x in out])
    eligible = sum(1 for x in out if x["verdict"] != "CASH")
    portfolio_ok = bnd["lb"] is not None and bnd["lb"] > 0
    return {"verdict": ("ELIGIBLE_FOR_EXISTING_GATED_PATH" if portfolio_ok
                        else "CASH"),
            "reason": ("POSITIVE_ALL_IN_EXECUTABLE_EV_LOWER_BOUND"
                       if portfolio_ok else
                       "LOWER_BOUND_NET_EXECUTABLE_EV_NOT_POSITIVE"
                       if out else "NO_PRICEABLE_DECISION"),
            "probability_authority": authority,
            "decisions_priced": len(out), "dropped": dropped,
            "decisions_individually_eligible": eligible,
            "mean_net_ev_per_contract": (sum(x["net"] for x in out) / len(out))
            if out else None,
            "portfolio_lower_bound_ev_per_contract": bnd["lb"],
            "events": bnd["clusters"],
            "incentives": "NOT_COUNTED (unmeasured; never assumed)",
            "authority_granted": False}


def ev_population(out: dict, *, read: int, limit: int,
                  truncated: bool) -> dict:
    """`ev_block`'s verdict with WHAT IT COVERS of the declared window: the
    decisions read, and whether the bound left any out. A cut read can
    never pass: its verdict is CASH by name (EV_READ_TRUNCATED), the subset's
    own verdict kept beside it as evidence only."""
    out = dict(out, population={
        "decisions_read": int(read), "limit": int(limit),
        "truncated": bool(truncated),
        "population_in_window": None if truncated else int(read),
        "population_at_least": int(read) + (1 if truncated else 0),
        "covers_the_declared_window": not truncated})
    if truncated:
        out.update(subset_verdict=out.get("verdict"),
                   subset_reason=out.get("reason"), verdict="CASH",
                   reason=EV_READ_TRUNCATED)
    return out


#: the twin read the OLDEST `limit` fresh orders (TWIN_SQL orders by
#: eligible_at): past the bound, every newer order -- the ones that say
#: whether the twin still agrees -- would be left out of a certification
#: (RC6.2). A cut population certifies nothing, by this name.
TWIN_READ_TRUNCATED = "READ_TRUNCATED_NEWER_ORDERS_NOT_REPLAYED"


def twin_block(orders: list, *, truncated: bool = False,
               limit: int | None = None) -> dict:
    fresh = [o for o in orders
             if float(o.get("eligible") or 0) > TWIN_DIAGNOSIS_WINDOW_END]
    rep = TW.agreement(fresh)
    rate = rep.get("fill_agreement_rate")
    enough = rep["replayed"] >= TWIN_MIN_FRESH_ORDERS
    certified = bool(enough and rate is not None and rate >= TW.TARGET
                     and not truncated)
    return dict(rep, fresh_since=TWIN_DIAGNOSIS_WINDOW_END,
                min_fresh_orders=TWIN_MIN_FRESH_ORDERS,
                status=(TWIN_READ_TRUNCATED if truncated else
                        "CERTIFIED" if certified else
                        "ACCUMULATING" if not enough else "BELOW_TARGET"),
                certified=certified,
                read={"orders_read": len(orders), "limit": limit,
                      "truncated": bool(truncated),
                      "order": "eligible_at ascending (oldest first)"},
                diagnosis_receipt={
                    "source": "Profitability Stack V1 twin run 37643890985",
                    "optimistic_fill_agreement": 0.700508,
                    "optimistic_mismatches": {
                        "TWIN_FILL_PAPER_NOFILL (PAPER EXPIRED/IOC)": 200,
                        "TWIN_NOFILL_PAPER_FILL (twin CANCELLED/IOC)": 36},
                    "repaired_in_sample_agreement": 0.984772,
                    "repaired_in_sample_note": (
                        "same 788 orders the class was diagnosed on: "
                        "in-sample, never the certification")})


async def read_probability(conn, *, days: int = 30, limit: int = 5000) -> dict:
    rows = [dict(r) for r in await conn.fetch(PROB_SQL, int(days), int(limit))]
    out = probability_block(rows)
    out["window_days"] = days
    return out


async def read_ev(conn, *, authority: str, days: int = 7,
                  limit: int = EV_MAX_DECISIONS) -> dict:
    from .. import calibration_fees as CF
    from .. import cpu_lane as _cpu

    def fee_fn(price, at):
        iso = datetime.fromtimestamp(float(at), timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        got = CF.expected_fee(float(price), 100, role=CF.ROLE_TAKER,
                              at=iso) or {}
        if got.get("BLOCKER") or got.get("FEE") is None:
            return None
        return float(got["FEE"]) / 100.0
    # one past the bound is read, so a cut window is known, not guessed
    rows = [dict(r) for r in await conn.fetch(EV_SQL, int(days),
                                              int(limit) + 1)]
    truncated = len(rows) > int(limit)
    rows = rows[:int(limit)]
    # the pricing is pure: on the API's CPU lane (one worker thread for
    # every such job: cpu_lane), never on the event loop, at the window's
    # whole size
    out = await _cpu.run(ev_block, rows, authority=authority, fee_fn=fee_fn)
    out = ev_population(out, read=len(rows), limit=int(limit),
                        truncated=truncated)
    out["window_days"] = days
    return out


async def read_twin(conn, *, limit: int = 800) -> dict:
    import json
    rows = await conn.fetch(TWIN_SQL, float(TWIN_DIAGNOSIS_WINDOW_END),
                            int(limit) + 1)
    truncated = len(rows) > int(limit)
    orders = [json.loads(r["j"]) for r in rows[:int(limit)]]
    return twin_block(orders, truncated=truncated, limit=int(limit))
