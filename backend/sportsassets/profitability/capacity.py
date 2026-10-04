"""THE CAPACITY MODEL (RESEARCH). Pure; no I/O.

How much of an opportunity can actually be bought, at what edge, and how
much is headline only. Per CANDIDATE (a recorded paper decision), from the
venue book RECORDED for it (paper_book_observations: the decision's own
book_obs_id, else the latest readable observation of the market at or
before the decision, within MAX_BOOK_AGE_S):

  take ladder         LONG buys the offers; SHORT buys the complement
                      against the bids at (1 - bid); cost space, ascending
  fee per contract    the PUBLISHED Polymarket US taker schedule for the
                      decision date (bettor_fee_schedule: theta x p x (1-p),
                      unrounded; published, not verified-as-applied)
  marginal net edge   p - price - fee, per contract, level by level

OUTPUTS (USD):

  THEORETICAL_OPPORTUNITY_DOLLARS   max(0, p - best price) x every visible
        contract: the HEADLINE -- top price for all depth, no impact, no
        fees, certain fill. What the north star says NOT to optimize.
  EXECUTABLE_OPPORTUNITY_DOLLARS    the expected net profit of buying every
        level whose marginal net edge is positive (the profit-maximizing
        size), after impact and fees, CONDITIONAL ON FILLING at the
        displayed book. (alias: executable_opportunity_usd)
  EXECUTABLE_CAPACITY_USD           the capital that size deploys (cost incl.
        fees); MAX_EXECUTABLE_CONTRACTS its contracts.
  CAPACITY_CEILING_USD              the break-even size: keep buying past the
        profit maximum until cumulative expected net profit returns to 0.
        Bounded by the visible book; `capacity_ceiling_depth_bound` says the
        book ran out first (the ceiling is then a lower bound).
  EXPECTED_EDGE_AT_SIZE             for each USD size on SIZE_GRID_USD: the
        contracts that size buys, VWAP, price impact over the best price,
        expected net profit and edge per dollar. A size beyond the visible
        book is EXCEEDS_VISIBLE_DEPTH with no edge (nothing is invented past
        the book).
  EDGE_DECAY_PER_1000_USD           (edge per dollar at the smallest measured
        size - at the largest) / (size difference / $1,000).
  EXPECTED_PRICE_IMPACT             VWAP of the executable size - best price.
  EXIT_LIQUIDITY_USD                what selling the executable contracts back
        into the recorded opposite side returns now; unabsorbed contracts
        reported (valued at 0).

Book-level, not per candidate (they are empirical rates over many orders,
see reads.capacity_rates): fill probability, deployment time, time to exit.
EXPECTED executable opportunity = executable x fill probability, only when
the fill probability is measured.

FAIL-CLOSED. No probability, no recorded book, a book too far from the
decision, an empty or unparsable take side, or no fee schedule for the date
-> status UNAVAILABLE with the reason and NO capacity number (the database
refuses one too). Never a fabricated depth.
"""
from __future__ import annotations

from ..intel import common as IC
from . import common as C

VERSION = "POS_CAPACITY_V1"
MAX_BOOK_AGE_S = 300.0
SIZE_GRID_USD = (10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0,
                 5000.0, 10000.0)
EPS = 1e-12

R_NO_P = "NO_PROBABILITY_ON_THE_CANDIDATE"
R_NO_BOOK = "NO_RECORDED_BOOK_OBSERVATION"
R_STALE = "BOOK_OBSERVATION_MORE_THAN_%ds_FROM_THE_DECISION" % int(
    MAX_BOOK_AGE_S)
R_EMPTY = "TAKE_SIDE_EMPTY_OR_UNPARSABLE"
R_NO_FEE = "NO_FEE_SCHEDULE_FOR_THE_DECISION_DATE"
R_NO_SIDE = "CANDIDATE_HOLDING_SIDE_UNKNOWN"


def take_ladder(view: dict, side: str) -> list:
    if str(side).upper() == "SHORT":
        lad = [(1.0 - p, q) for p, q in view.get("bids") or []]
    else:
        lad = list(view.get("offers") or [])
    return sorted(lad, key=lambda x: x[0])


def _unavailable(out, why):
    out["status"] = C.UNAVAILABLE
    out["why"] = why
    for k in ("theoretical_opportunity_dollars",
              "executable_opportunity_dollars", "executable_capacity_usd",
              "capacity_ceiling_usd", "visible_depth_usd"):
        out.put(k, None, why)
    out["executable_opportunity_usd"] = None
    out["edge_at_size"] = None
    return out


def assess(cand: dict, book: dict | None, *, fee_fn) -> dict:
    """Capacity of ONE candidate. `book` is {obs_id, observed_at, bids,
    offers} or None; `fee_fn(price) -> fee per contract` or None."""
    out = C.Out(candidate_id=cand["candidate_id"],
                decided_at=cand.get("decided_at"),
                us_market_slug=cand.get("us_market_slug"),
                holding_side=cand.get("holding_side"),
                strategy=cand.get("strategy"), version=VERSION,
                label=C.LABEL, status=C.MEASURED, why=None)
    p = C.num(cand.get("probability"))
    out["probability"] = p
    out["book_obs_id"] = (book or {}).get("obs_id")
    out["book_observed_at"] = (book or {}).get("observed_at")
    side = str(cand.get("holding_side") or "").upper()
    if p is None or not 0.0 < p < 1.0:
        return _unavailable(out, R_NO_P)
    if side not in ("LONG", "SHORT"):
        return _unavailable(out, R_NO_SIDE)
    if not book or C.num(book.get("observed_at")) is None:
        return _unavailable(out, R_NO_BOOK)
    dec = C.num(cand.get("decided_at"))
    age = None if dec is None else dec - float(book["observed_at"])
    out["book_age_s"] = C.rnd(age, 3)
    if age is None or abs(age) > MAX_BOOK_AGE_S:
        return _unavailable(out, R_STALE)
    view = IC.book_view(book.get("bids"), book.get("offers"))
    lad = take_ladder(view, side)
    if not lad:
        return _unavailable(out, R_EMPTY)
    if fee_fn is None:
        return _unavailable(out, R_NO_FEE)
    try:
        fees = [float(fee_fn(px)) for px, _ in lad]
    except Exception:                                          # noqa: BLE001
        return _unavailable(out, R_NO_FEE)
    best = lad[0][0]
    out["best_price"] = C.rnd(best)
    vq = sum(q for _, q in lad)
    out["visible_depth_contracts"] = C.rnd(vq)
    out.put("visible_depth_usd", C.rnd(sum(px * q for px, q in lad)))
    out.put("theoretical_opportunity_dollars", C.rnd(max(0.0, p - best) * vq))
    # ── the profit-maximizing walk, then on to break-even ──────────────
    eq = ecost = eprofit = egross = 0.0
    for (px, q), f in zip(lad, fees):
        if p - px - f <= 0:
            break
        eq += q
        ecost += q * (px + f)
        egross += q * px
        eprofit += q * (p - px - f)
    out["max_executable_contracts"] = C.rnd(eq)
    out.put("executable_capacity_usd", C.rnd(ecost))
    out.put("executable_opportunity_dollars", C.rnd(eprofit))
    out["executable_opportunity_usd"] = out["executable_opportunity_dollars"]
    if eq > EPS:
        out.put("expected_price_impact", C.rnd(egross / eq - best))
    else:
        out.put("expected_price_impact", None, "NO_LEVEL_WITH_POSITIVE_NET_EDGE")
    cum = ccost = 0.0
    bound = True
    for (px, q), f in zip(lad, fees):
        e = p - px - f
        if e >= 0 or cum + q * e >= 0:
            cum += q * e
            ccost += q * (px + f)
            continue
        extra = cum / (-e)
        ccost += extra * (px + f)
        cum = 0.0
        bound = False
        break
    out.put("capacity_ceiling_usd", C.rnd(ccost))
    out["capacity_ceiling_depth_bound"] = bound
    # ── expected edge at size ─────────────────────────────────────────
    grid = []
    for size in SIZE_GRID_USD:
        left, q_, gross, prof = size, 0.0, 0.0, 0.0
        for (px, q), f in zip(lad, fees):
            unit = px + f
            if unit <= EPS:
                continue
            take = min(q, left / unit)
            q_ += take
            gross += take * px
            prof += take * (p - px - f)
            left -= take * unit
            if left <= 1e-9:
                break
        if left > 1e-6:
            grid.append({"size_usd": size, "status": "EXCEEDS_VISIBLE_DEPTH",
                         "contracts": None, "vwap": None,
                         "price_impact": None, "expected_net_profit_usd": None,
                         "edge_per_dollar": None})
            continue
        vwap = gross / q_ if q_ > EPS else None
        grid.append({"size_usd": size, "status": C.MEASURED,
                     "contracts": C.rnd(q_), "vwap": C.rnd(vwap),
                     "price_impact": C.rnd(None if vwap is None
                                           else vwap - best),
                     "expected_net_profit_usd": C.rnd(prof),
                     "edge_per_dollar": C.rnd(prof / size, 9)})
    out["edge_at_size"] = grid
    meas = [g for g in grid if g["status"] == C.MEASURED]
    if len(meas) >= 2:
        a, b = meas[0], meas[-1]
        out.put("edge_decay_per_1000_usd", C.rnd(
            (a["edge_per_dollar"] - b["edge_per_dollar"])
            / ((b["size_usd"] - a["size_usd"]) / 1000.0), 9))
    else:
        out.put("edge_decay_per_1000_usd", None,
                "FEWER_THAN_TWO_GRID_SIZES_INSIDE_THE_VISIBLE_BOOK")
    # ── exit ──────────────────────────────────────────────────────────
    w = IC.exit_walk(view, side, eq)
    out.put("exit_liquidity_usd", C.rnd(w["value_usd"]))
    out["exit_unabsorbed_contracts"] = C.rnd(w["unabsorbed_qty"])
    out["detail"] = {
        "fee_basis": cand.get("fee_basis"),
        "levels": len(lad),
        "spread": C.rnd(view.get("spread")),
        "theoretical_basis": "max(0, p - best) x visible contracts; no "
                             "impact, no fees, certain fill",
        "executable_basis": "levels with p - price - fee > 0, conditional on "
                            "filling at the displayed book"}
    return out


def aggregate(rows: list, *, rates: dict) -> dict:
    """Aggregate capacity over the latest candidate per (market, side).
    `rates` carries the book-level fill probability, deployment time and
    time to exit (each value-or-None with a reason)."""
    out = C.Out(label=C.LABEL, authority=C.AUTHORITY, version=VERSION)
    out["candidates"] = len(rows)
    latest: dict = {}
    for r in sorted(rows, key=lambda r: r.get("decided_at") or 0):
        latest[(r.get("us_market_slug"), r.get("holding_side"))] = r
    meas = [r for r in latest.values() if r["status"] == C.MEASURED]
    out["markets"] = len(latest)
    out["measured_markets"] = len(meas)
    why: dict = {}
    for r in latest.values():
        if r["status"] != C.MEASURED:
            why[r["why"]] = why.get(r["why"], 0) + 1
    out["unavailable_by_reason"] = why
    if not meas:
        reason = "NO_CANDIDATE_WITH_DEPTH_EVIDENCE"
        for k in ("THEORETICAL_OPPORTUNITY_DOLLARS",
                  "EXECUTABLE_OPPORTUNITY_DOLLARS", "EXECUTABLE_CAPACITY_USD",
                  "CAPACITY_CEILING_USD",
                  "EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS",
                  "daily_executable_opportunity_dollars"):
            out.put(k, None, reason)
    else:
        for k, src in (
                ("THEORETICAL_OPPORTUNITY_DOLLARS",
                 "theoretical_opportunity_dollars"),
                ("EXECUTABLE_OPPORTUNITY_DOLLARS",
                 "executable_opportunity_dollars"),
                ("EXECUTABLE_CAPACITY_USD", "executable_capacity_usd"),
                ("CAPACITY_CEILING_USD", "capacity_ceiling_usd")):
            out.put(k, C.rnd(sum(r[src] for r in meas)))
        th = out["THEORETICAL_OPPORTUNITY_DOLLARS"]
        out["executable_share_of_theoretical"] = (
            C.rnd(out["EXECUTABLE_OPPORTUNITY_DOLLARS"] / th, 6)
            if th and th > 0 else None)
        fp = (rates.get("fill_probability") or {}).get("value")
        out.put("EXPECTED_EXECUTABLE_OPPORTUNITY_DOLLARS",
                C.rnd(out["EXECUTABLE_OPPORTUNITY_DOLLARS"] * fp)
                if fp is not None else None,
                (rates.get("fill_probability") or {}).get("why")
                or "FILL_PROBABILITY_NOT_MEASURED")
        days: dict = {}
        seen = set()
        for r in sorted(rows, key=lambda r: -(r.get("decided_at") or 0)):
            if r["status"] != C.MEASURED or r.get("decided_at") is None:
                continue
            d = int(r["decided_at"] // 86400)
            k = (d, r.get("us_market_slug"), r.get("holding_side"))
            if k in seen:
                continue
            seen.add(k)
            days[d] = days.get(d, 0.0) + r["executable_opportunity_dollars"]
        out["days_observed"] = len(days)
        out.put("daily_executable_opportunity_dollars",
                C.rnd(sum(days.values()) / len(days)) if days else None,
                "NO_DATED_MEASURED_CANDIDATE")
    out["rates"] = rates
    out["aliases"] = {"THEORETICAL_OPPORTUNITY_USD":
                      "THEORETICAL_OPPORTUNITY_DOLLARS",
                      "EXECUTABLE_CAPACITY_USD (capital deployed)":
                      "EXECUTABLE_CAPACITY_USD",
                      "EXECUTABLE_OPPORTUNITY_USD":
                      "EXECUTABLE_OPPORTUNITY_DOLLARS"}
    out["basis"] = ("latest candidate per (market, side); sums over MEASURED "
                    "candidates only; UNAVAILABLE candidates counted by "
                    "reason, never as zero")
    return out


def rate(numer, denom, n, *, min_n, basis, why):
    """A measured rate or None with its reason."""
    if n < min_n or not denom:
        return {"value": None, "n": n, "basis": basis,
                "why": why if n < min_n else "ZERO_DENOMINATOR"}
    return {"value": C.rnd(numer / denom, 6), "n": n, "basis": basis,
            "why": None}
