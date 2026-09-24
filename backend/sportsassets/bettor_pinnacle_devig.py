"""PINNACLE_DEVIG_V1 — an EXTERNAL BOOKMAKER valuation, and nothing more.

WHAT THIS IS. A sharp bookmaker's own price, de-vigged over the complete
outcome set, offered as a probability for one venue contract. It is not a
trained model, it has no fitted parameter, and it is not an internally
qualified settlement model. `bettor_fair_value` still reports
FV_BETTOR_INDEPENDENT = NOT_IDENTIFIED and that is untouched: the internal
challenger was measured WORSE than the venue price (delta log loss
-0.00926, NOT_DETECTED_AT_THIS_SAMPLE_SIZE) and nothing here revisits it.
This is a third, separately labelled source class.

WHY IT IS ALLOWED TO DRIVE A SHADOW DECISION AT ALL. The circularity guard
in `bettor_entry_gate` refuses a fair value derived from the venue we are
pricing against, because a benchmark cannot be evidence against itself.
A different market's price is not that: Pinnacle is not Polymarket. The
guard's purpose is preserved, which is exactly why this source must carry
its own kind string and never borrow FV_BETTOR_INDEPENDENT's.

────────────────────────────────────────────────────────────────────
WHAT THE PROVIDER ACTUALLY CARRIES, measured 2026-09-23T23:28:56-59Z by
`feed-coverage` (run 35933793563) against the real plan, not assumed:

    sport                  events   pinnacle      segments w/ pinnacle
    baseball_mlb              19    13  (68%)     h2h/spreads/totals_1st_5
    soccer_epl                20    20 (100%)     h2h_h1
    soccer_mexico_ligamx       9     9 (100%)     none
    basketball_nba            41     0            none
    icehockey_nhl             33     0            none

    props: NO sharp book on any sport (MLB 173 outcomes, EPL 56, NBA 18)
    plan: 82 sports; the whole probe cost 99 credits
          (x-requests-used 8,063,058 -> 8,063,157)

SO THE SUPPORTED SET IS DECIDED BY MEASUREMENT. Soccer h2h and MLB h2h
carry Pinnacle; basketball and hockey do not carry it at all, and props
carry no sharp book anywhere. A source named for Pinnacle must refuse the
sports Pinnacle does not quote rather than quietly substituting another
book, so `SUPPORTED` lists only what was observed.

────────────────────────────────────────────────────────────────────
WHAT IS REUSED, AND WHAT COULD NOT BE.

Reused as METHOD and SEMANTICS, cited rather than imported:

  edge/fairvalue/devig.py     multiplicative and power de-vig, and its
                              own note that the reference account's
                              calibration signature is consistent with
                              POWER on a Pinnacle-class feed
  edge/fairvalue/feed.py      ANCHOR_BOOKS (pinnacle weight 3.0),
                              SHARP_BOOKS, the 30 s `is_fresh` hard rule,
                              `enriched_at` vs `fetched_at` (the audit
                              found props trading on half-hour-old quotes
                              while fetched_at swore 25 s), per-OUTCOME
                              depth rather than per-event, and the rule
                              that a segment we have no quotes for is
                              REFUSED and never priced off the full game
  edge/venues/mapper.py       team-name normalisation for event identity

COULD NOT BE IMPORTED, and this is a fact about the image, not a choice:
`edge.fairvalue.devig` imports `scipy.optimize.brentq`. Neither scipy nor
numpy is in backend/pyproject.toml or the deployed API image, and `edge`
is not an installed package here (`import edge` -> ModuleNotFoundError).
So the arithmetic is re-implemented in the standard library, the power
root by bisection instead of brentq, and a test asserts the two agree to
1e-9 on the same odds. Claiming to reuse a module that cannot be imported
would have been the easy lie.
"""

from __future__ import annotations

VERSION = "PINNACLE_DEVIG_V1"
SOURCE_CLASS = "EXTERNAL_BOOKMAKER_VALUATION"
PROVIDER = "the-odds-api.com/v4"
BOOK = "pinnacle"

#: NOT a trained model, and every consumer is told so in these words.
LABEL = ("EXTERNAL BOOKMAKER VALUATION. A sharp book's own de-vigged price, "
         "not a trained proprietary model, not an internally qualified "
         "settlement model, and not validated against the venue price")

#: The de-vig methods, declared. `power` is the default because
#: edge/fairvalue/devig.py records that the reference account's calibration
#: signature (edge at 5-10c AND 30-50c AND 75-90c simultaneously) is
#: consistent with power de-vig on a Pinnacle-class feed -- and it also
#: says "validate in shadow", which is what this source is for.
METHOD_POWER = "power"
METHOD_MULTIPLICATIVE = "multiplicative"
METHODS = (METHOD_POWER, METHOD_MULTIPLICATIVE)
DEFAULT_METHOD = METHOD_POWER

#: (sport_key, market_key) -> the number of outcomes a COMPLETE set has.
#: A three-way soccer market de-vigged over two outcomes is not a de-vig,
#: it is a different and wrong number that still looks like a probability,
#: so the count is declared per market and enforced.
SUPPORTED: dict = {
    ("soccer", "h2h"): 3,      # home / draw / away
    ("baseball", "h2h"): 2,    # no draw in MLB
}

#: Sports Pinnacle was measured NOT to quote. Named so a refusal can say
#: "the book does not price this" rather than "no data".
PINNACLE_ABSENT = ("basketball", "icehockey")

#: How old a quote may be. The engine's own hard rule is 30 s and it is
#: adopted unchanged: "no order without a quote fresher than max_age_s".
MAX_QUOTE_AGE_S = 30.0

# ── refusals, one per way this can fail ─────────────────────────────
R_UNSUPPORTED_MARKET = "MARKET_NOT_IN_SUPPORTED_SET"
R_PINNACLE_ABSENT = "PINNACLE_DOES_NOT_QUOTE_THIS_SPORT"
R_BOOK_MISSING = "PINNACLE_NOT_IN_THIS_PAYLOAD"
R_INCOMPLETE_OUTCOMES = "OUTCOME_SET_INCOMPLETE"
R_STALE = "QUOTE_STALE"
R_NO_TIMESTAMP = "QUOTE_HAS_NO_TIMESTAMP"
R_BAD_ODDS = "ODDS_NOT_A_PRICE"
R_AMBIGUOUS_MAPPING = "MAPPING_AMBIGUOUS"
R_NO_MAPPING = "MAPPING_NOT_ESTABLISHED"
R_SELECTION_UNMATCHED = "SELECTION_NOT_IN_OUTCOME_SET"
R_LINE_MISMATCH = "LINE_DOES_NOT_MATCH"
R_PERIOD_MISMATCH = "PERIOD_DOES_NOT_MATCH"
R_SETTLEMENT_MISMATCH = "SETTLEMENT_RULE_DOES_NOT_MATCH"
R_UNKNOWN_METHOD = "DEVIG_METHOD_NOT_DECLARED"

def _epoch(value):
    """Seconds since the epoch, from a number or an ISO-8601 string.

    The provider states quote times as ISO-8601 with a trailing Z
    ("2026-09-24T00:00:00Z"); our own clocks are floats. Both reach this
    module, so both are accepted HERE rather than each caller
    reimplementing the parse and one of them getting it wrong. A value
    that is neither raises, and the caller turns that into
    QUOTE_HAS_NO_TIMESTAMP.
    """
    if isinstance(value, (int, float)):
        return float(value)
    from datetime import datetime, timezone

    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    try:
        return float(text)
    except ValueError:
        pass
    # `fromisoformat` in 3.11 handles the offset forms but not a bare Z.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        # NAMED, not assumed silently: a naive stamp from this provider is
        # UTC, and reading it as local time would shift every age by the
        # host's offset.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


REFUSALS = (R_UNSUPPORTED_MARKET, R_PINNACLE_ABSENT, R_BOOK_MISSING,
            R_INCOMPLETE_OUTCOMES, R_STALE, R_NO_TIMESTAMP, R_BAD_ODDS,
            R_AMBIGUOUS_MAPPING, R_NO_MAPPING, R_SELECTION_UNMATCHED,
            R_LINE_MISMATCH, R_PERIOD_MISMATCH, R_SETTLEMENT_MISMATCH,
            R_UNKNOWN_METHOD)


# ── the de-vig, in the standard library ─────────────────────────────

def implied(decimal_odds) -> list:
    """q_i = 1 / decimal_odds_i. The raw, vigged implied probabilities."""
    return [1.0 / float(o) for o in decimal_odds]


def devig_multiplicative(decimal_odds) -> list:
    q = implied(decimal_odds)
    s = sum(q)
    return [x / s for x in q]


def devig_power(decimal_odds, *, tol: float = 1e-12,
                max_iter: int = 200) -> list:
    """p_i = q_i^k with k solved so the set sums to one.

    BISECTION, NOT BRENTQ, because scipy is not in this image. sum(q_i^k)
    is strictly decreasing in k for q_i in (0, 1), so a sign change is
    found by widening the bracket exactly as the scipy version does, and
    the fallback on a degenerate set is multiplicative rather than a
    crash -- same behaviour, same reason.
    """
    q = implied(decimal_odds)
    if abs(sum(q) - 1.0) < 1e-9:
        return list(q)

    def f(k: float) -> float:
        return sum(x ** k for x in q) - 1.0

    lo, hi = 0.25, 4.0
    for _ in range(12):
        if f(lo) * f(hi) < 0:
            for _ in range(max_iter):
                mid = 0.5 * (lo + hi)
                fm = f(mid)
                if abs(fm) < tol or (hi - lo) < tol:
                    return [x ** mid for x in q]
                if f(lo) * fm < 0:
                    hi = mid
                else:
                    lo = mid
            return [x ** (0.5 * (lo + hi)) for x in q]
        lo, hi = lo / 2.0, hi * 2.0
        if lo < 1e-4 or hi > 512:
            break
    return devig_multiplicative(decimal_odds)


def devig(decimal_odds, method: str = DEFAULT_METHOD) -> list:
    if method == METHOD_POWER:
        return devig_power(decimal_odds)
    if method == METHOD_MULTIPLICATIVE:
        return devig_multiplicative(decimal_odds)
    raise ValueError("undeclared de-vig method %r" % (method,))


# ── mapping: one venue contract to one outcome in one complete set ───

def _norm(name) -> str:
    """Enough normalisation to compare names, and no fuzzy matching.

    DELIBERATELY NOT FUZZY. `edge/venues/mapper.py` does fuzzy matching
    with a score threshold because it must find SOME venue market for a
    feed event. Here the direction is reversed: we already have one
    contract and must decide whether this outcome is certainly the same
    selection. A near-match is an AMBIGUOUS mapping and gets refused, so
    a 0.9-confident string similarity is exactly the wrong instrument.
    """
    return " ".join(str(name or "").strip().lower().replace(".", "").split())


def map_selection(*, selection, outcomes) -> dict:
    """Which outcome IS this contract's selection? Exactly one, or refuse."""
    want = _norm(selection)
    if not want:
        return {"ok": False, "refusal": R_NO_MAPPING,
                "why": "the contract carries no selection to map"}
    hits = [n for n in outcomes if _norm(n) == want]
    if len(hits) == 1:
        return {"ok": True, "refusal": None, "outcome": hits[0],
                "match": "EXACT_AFTER_NORMALISATION"}
    if len(hits) > 1:
        return {"ok": False, "refusal": R_AMBIGUOUS_MAPPING,
                "why": ("%d outcomes normalise to %r; a selection that "
                        "matches more than one outcome is not a mapping"
                        % (len(hits), want))}
    return {"ok": False, "refusal": R_SELECTION_UNMATCHED,
            "why": ("%r is not among the priced outcomes %r. No fuzzy "
                    "fallback: a near-match here would price a different "
                    "bet that still looks clean"
                    % (selection, sorted(_norm(o) for o in outcomes)))}


def _contract_agrees(contract: dict, quote: dict) -> dict:
    """Event, line, period and settlement rule must all match explicitly.

    Each is compared only when BOTH sides declare it, and a declaration on
    one side with silence on the other is a refusal rather than an
    assumption -- that asymmetry is where a full-game price gets used for
    a first-half contract.
    """
    for field, code in (("event_key", R_NO_MAPPING),
                        ("period", R_PERIOD_MISMATCH),
                        ("line", R_LINE_MISMATCH),
                        ("settlement_rule", R_SETTLEMENT_MISMATCH)):
        a, b = contract.get(field), quote.get(field)
        if a is None and b is None:
            if field == "event_key":
                return {"ok": False, "refusal": R_NO_MAPPING,
                        "why": "neither side declares an event identity"}
            continue
        if a is None or b is None:
            return {"ok": False, "refusal": code,
                    "why": ("%s is declared on one side only (contract=%r, "
                            "quote=%r); silence is not agreement"
                            % (field, a, b))}
        if field == "line":
            try:
                if abs(float(a) - float(b)) > 1e-9:
                    return {"ok": False, "refusal": code,
                            "why": "line %r is not line %r" % (a, b)}
                continue
            except (TypeError, ValueError):
                return {"ok": False, "refusal": code,
                        "why": "line values are not numeric: %r / %r" % (a, b)}
        if _norm(a) != _norm(b):
            return {"ok": False, "refusal": code,
                    "why": "%s %r does not match %r" % (field, a, b)}
    return {"ok": True, "refusal": None}


# ── the valuation ───────────────────────────────────────────────────

def valuation(*, contract: dict, quote: dict, now: float,
              method: str = DEFAULT_METHOD,
              max_age_s: float = MAX_QUOTE_AGE_S) -> dict:
    """A probability for ONE venue contract, or every reason there is none.

    `contract` describes what we would buy: sport_family, market, selection,
    event_key, period, line, settlement_rule.
    `quote` is Pinnacle's own priced set for that market: book, observed_at,
    received_at, outcomes {name -> decimal odds}, plus the same identity
    fields to be matched against.

    Returns `probability` only when every requirement holds. The refusals
    are a LIST, not a first-failure, so the command centre can show every
    reason an opportunity did not exist.
    """
    sport = _norm(contract.get("sport_family"))
    market = _norm(contract.get("market"))
    out: dict = {
        "version": VERSION, "source_class": SOURCE_CLASS,
        "label": LABEL, "provider": PROVIDER, "book": BOOK,
        "devig_method": method, "sport_family": sport, "market": market,
        "refusals": [], "probability": None,
    }
    refusals = out["refusals"]

    if method not in METHODS:
        refusals.append(R_UNKNOWN_METHOD)
        out["why"] = "de-vig method %r is not one of %r" % (method, METHODS)
        return out

    expected = SUPPORTED.get((sport, market))
    if expected is None:
        refusals.append(R_UNSUPPORTED_MARKET)
        if sport in PINNACLE_ABSENT:
            # A DIFFERENT FACT with a different remedy: the market is not
            # unsupported because we have not got round to it, it is
            # unsupported because the book does not price it.
            refusals.append(R_PINNACLE_ABSENT)
        out["supported"] = sorted("%s/%s" % k for k in SUPPORTED)
        out["why"] = ("%s/%s is not in the measured supported set %s"
                      % (sport, market, out["supported"]))
        return out
    out["expected_outcomes"] = expected

    if _norm(quote.get("book")) != BOOK:
        refusals.append(R_BOOK_MISSING)
        out["why"] = ("this payload is from %r, not %s. A source named for "
                      "Pinnacle must not silently substitute another book"
                      % (quote.get("book"), BOOK))
        return out

    agree = _contract_agrees(contract, quote)
    if not agree["ok"]:
        refusals.append(agree["refusal"])
        out["why"] = agree["why"]
        return out

    outcomes = dict(quote.get("outcomes") or {})
    out["outcomes_priced"] = len(outcomes)
    if len(outcomes) != expected:
        refusals.append(R_INCOMPLETE_OUTCOMES)
        out["why"] = ("%d of %d outcomes priced. A de-vig normalises over "
                      "the COMPLETE set; over a subset it returns a number "
                      "that still looks like a probability and is not one"
                      % (len(outcomes), expected))
        return out

    bad = [n for n, o in outcomes.items()
           if not isinstance(o, (int, float)) or float(o) <= 1.0]
    if bad:
        refusals.append(R_BAD_ODDS)
        out["why"] = ("decimal odds must exceed 1.0; offending outcomes %r"
                      % sorted(bad))
        return out

    observed_at = quote.get("observed_at")
    if observed_at is None:
        refusals.append(R_NO_TIMESTAMP)
        out["why"] = ("the quote carries no observed_at, so it cannot be "
                      "aged. The engine's own rule is no order without a "
                      "quote fresher than %.0f s" % max_age_s)
        return out
    # A TIMESTAMP WE CANNOT READ IS A REFUSAL, NOT A CRASH. The provider
    # states `last_update` as an ISO-8601 string; float() on it raised
    # ValueError straight out of `valuation`, which a caller in a loop
    # would see as an exception rather than as the named refusal this
    # module exists to give. The parse is accepted here and the failure is
    # named, because a quote that cannot be aged must be refused the same
    # way a missing one is.
    try:
        observed_at = _epoch(observed_at)
    except (TypeError, ValueError):
        refusals.append(R_NO_TIMESTAMP)
        out["why"] = ("observed_at %r cannot be read as a time, so the "
                      "quote cannot be aged" % (observed_at,))
        return out
    try:
        received = (None if quote.get("received_at") is None
                    else _epoch(quote["received_at"]))
    except (TypeError, ValueError):
        received = None
    age = float(now) - float(observed_at)
    out["observed_at"] = float(observed_at)
    out["received_at"] = received
    out["age_s"] = age
    out["max_age_s"] = float(max_age_s)
    if age > float(max_age_s) or age < 0:
        refusals.append(R_STALE)
        out["why"] = (("the quote is %.1f s old against a %.0f s limit"
                       % (age, max_age_s)) if age >= 0 else
                      ("the quote is stamped %.1f s in the future; a clock "
                       "disagreement is not freshness" % (-age,)))
        return out

    m = map_selection(selection=contract.get("selection"),
                      outcomes=list(outcomes))
    if not m["ok"]:
        refusals.append(m["refusal"])
        out["why"] = m["why"]
        return out
    out["mapped_outcome"] = m["outcome"]
    out["mapping_match"] = m["match"]

    names = sorted(outcomes)
    probs = devig([outcomes[n] for n in names], method)
    out["raw_odds"] = {n: float(outcomes[n]) for n in names}
    out["devigged"] = dict(zip(names, probs))
    out["overround"] = sum(implied([outcomes[n] for n in names])) - 1.0
    out["probability"] = out["devigged"][m["outcome"]]
    out["why"] = ("%s de-vig over %d priced outcomes from %s, %.1f s old"
                  % (method, expected, BOOK, age))
    return out


def describe() -> dict:
    return {
        "version": VERSION,
        "source_class": SOURCE_CLASS,
        "label": LABEL,
        "is_a_trained_model": False,
        "is_an_internally_qualified_settlement_model": False,
        "provider": PROVIDER,
        "book": BOOK,
        "methods": list(METHODS),
        "default_method": DEFAULT_METHOD,
        "supported": sorted("%s/%s" % k for k in SUPPORTED),
        "complete_outcome_counts": {"%s/%s" % k: v
                                    for k, v in SUPPORTED.items()},
        "pinnacle_absent_sports": list(PINNACLE_ABSENT),
        "max_quote_age_s": MAX_QUOTE_AGE_S,
        "refusals": list(REFUSALS),
        "coverage_measured_at": "2026-09-23T23:28:56Z",
        "coverage_evidence": "feed-coverage run 35933793563",
    }
