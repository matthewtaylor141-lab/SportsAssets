"""THE IDENTITY BINDING, RESOLVED AHEAD OF THE SIGNAL.

Owner directive 2026-09-20 (the identity blocker). 13 prospective BUY
decisions carried IDENTITY_BINDING_STATUS = NOT_IDENTIFIED and so
EXECUTION_STATUS = BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE, and
POSITIONS stayed 0. "Do not wait until X1 says BUY and then begin
resolving identity."

WHAT WAS ACTUALLY WRONG, found in the code rather than guessed at.
`instrument_records` read the institutional instrument record out of
`bettor_l2_evidence.instrument_record` -- a column only the GitHub
bridge ever wrote. The direct worker holds the credential now and
fetches refdata itself, but it was not writing that record anywhere the
binding could see, so the lookup returned nothing for every focus
market and `bind_yes` short-circuited to NOT_IDENTIFIED. The message it
produced ("no institutional instrument record has been observed for
this symbol") was TRUE, which is why nothing looked broken: every
component reported healthy and the eligible population was empty.

THE CHAIN THIS MODULE RESOLVES, once per market, before any signal:

    RETAIL MARKET / CONTRACT
      -> VENUE-NATIVE RETAIL IDENTITY      (us_premap: slug, identifier)
      -> INSTITUTIONAL INSTRUMENT          (refdata, read directly)
      -> INSTITUTIONAL NATIVE IDENTITY     (symbol, outcome_strike, ...)
      -> SETTLEMENT EQUIVALENCE            (stated, not assumed)
      -> SCALE                             (the instrument's own)
      -> L2                                (the in-memory book)

DETERMINISTIC VENUE-NATIVE FIELDS ONLY. No fuzzy title matching, no
approximate team-name matching, no price matching, and no instrument
chosen because its current price looks similar. This module computes no
price and reads none; it could not match on one if it wanted to.

ELIGIBILITY IS INDEPENDENT OF MODEL OUTCOME. Nothing here reads a
signal, a direction, a P&L or an experiment's output. "Do not choose
only markets whose historical X1 signals were profitable."

THE HONEST DEFAULT IS STILL REFUSAL. This module resolves EARLIER, not
LOOSER: every verdict comes from `shadow_identity`, whose gate is
unchanged, and a market whose identity cannot be established lands
AMBIGUOUS or NOT_IDENTIFIED exactly as before -- now with the reason
recorded per market instead of a blanket "no record observed".
"""

from __future__ import annotations

from . import shadow_identity as ident

EVIDENCE_ENVIRONMENT = "DIRECT_INSTITUTIONAL_WORKER"

# §3: the YES side first. "Do not let unresolved BUY_NO baskets delay
# BUY_YES." A NO leg is resolved too -- and recorded honestly as the
# pending complement it is -- but it never gates the YES side.
LEG_YES = "yes"
LEG_NO = "no"


def _int_or_none(value):
    """A scale as an INTEGER, or nothing. Never a default.

    A scale this module cannot read is absent, not 100. Assuming one
    misprices every instrument that does not use it, and the
    institutional board runs 100 on some sports and 1000 on others.
    """
    try:
        out = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return out if out > 0 else None


def settlement_equivalence(binding: dict) -> str | None:
    """WHY these two keys name one economic contract, in words.

    Returned only for a verdict that actually established it. A
    sentence attached to an AMBIGUOUS binding would read as though
    something had been proven.
    """
    verdict = (binding or {}).get("verdict")
    if verdict != ident.EXACT_ONE_TO_ONE:
        return None
    inst = (binding or {}).get("institutional") or {}
    outcome = inst.get("outcomeStrike")
    symbol = inst.get("symbol")
    return (
        "the retail market %r is a binary over the single outcome %r, and "
        "the institutional instrument %r IS that outcome; buying the "
        "retail YES leg settles to 1 exactly when %r settles to 1. "
        "Established from both venues' own keys -- the slug's terminal "
        "token is the institutional outcome_strike -- and from no title, "
        "team name or price." % (symbol, outcome, symbol, outcome))


def resolve(market_id, outcome_leg, *, instrument_record, retail_row) -> dict:
    """One market's binding, as the row §2 asks to be precomputed.

    Returns a row whatever happens. A market the institutional venue
    does not list is a real answer about coverage and is recorded as
    one; raising here would lose the finding and leave the market
    looking simply unexamined.
    """
    leg = str(outcome_leg or LEG_YES).strip().lower()
    row = {
        "market_id": str(market_id),
        "outcome_leg": leg,
        "binding_version": ident.BINDING_VERSION,
        "evidence_environment": EVIDENCE_ENVIRONMENT,
        "retail_native_id": None,
        "event_slug": None,
        "institutional_instrument_id": None,
        "institutional_event_id": None,
        "outcome_strike": None,
        "event_outcome": None,
        "price_scale": None,
        "quantity_scale": None,
        "payout_value": None,
        "settlement_equivalence": None,
        "agree": [],
    }

    if not instrument_record:
        row.update({
            "identity_status": ident.NOT_IDENTIFIED,
            "execution_eligible": False,
            "why": ["the institutional venue returned no instrument for "
                    "this symbol, so there is nothing to bind to"],
        })
        row["identity_binding_sha"] = ident.binding_sha(
            {}, {"marketSlug": row["market_id"], "outcomeLeg": leg},
            ident.NOT_IDENTIFIED)
        return row

    if not retail_row:
        row.update({
            "identity_status": ident.NOT_IDENTIFIED,
            "execution_eligible": False,
            "why": ["no retail row is known for this market and leg, so "
                    "the retail side of the pair has no venue-native "
                    "identity to bind"],
        })
        inst = ident.institutional_identity(instrument_record)
        row.update(_institutional_columns(inst))
        row["identity_binding_sha"] = ident.binding_sha(
            inst, {"marketSlug": row["market_id"], "outcomeLeg": leg},
            ident.NOT_IDENTIFIED)
        return row

    inst = ident.institutional_identity(instrument_record)
    rt = ident.retail_identity(retail_row)
    # The leg the CALLER asked about wins over whatever the stored row
    # happens to carry: this function resolves a named (market, leg).
    rt = dict(rt, outcomeLeg=leg)

    # §3: the YES path has its own established reading; everything else
    # goes through the general gate, which refuses by default.
    binding = (ident.yes_leg_binding(inst, rt) if leg in ("yes", "long")
               else ident.classify(inst, rt))

    row.update(_institutional_columns(binding.get("institutional") or inst))
    row.update({
        "retail_native_id": rt.get("identifier"),
        "event_slug": rt.get("eventSlug"),
        "identity_status": binding["verdict"],
        "execution_eligible": bool(binding.get("executionEligible")),
        "identity_binding_sha": binding.get("identityBindingSha"),
        "settlement_equivalence": settlement_equivalence(binding),
        "agree": list(binding.get("agree") or []),
        "why": list(binding.get("why") or []),
    })

    # THE PRICEABILITY GATE, restated here because the database enforces
    # it too. An exact contract whose scales we cannot read is not an
    # executable one, and the row must not claim it is.
    if row["execution_eligible"] and (row["price_scale"] is None
                                      or row["quantity_scale"] is None):
        row["execution_eligible"] = False
        row["why"] = list(row["why"]) + [
            "the binding is exact but the instrument's scales are not "
            "readable, so its book cannot be converted to money"]
    return row


def _institutional_columns(inst: dict) -> dict:
    inst = inst or {}
    return {
        "institutional_instrument_id": inst.get("symbol"),
        "institutional_event_id": (inst.get("eventId")
                                   or inst.get("eventMetadataId")),
        "outcome_strike": inst.get("outcomeStrike"),
        "event_outcome": inst.get("eventOutcome"),
        "price_scale": _int_or_none(inst.get("priceScale")),
        "quantity_scale": _int_or_none(inst.get("qtyScale")),
        "payout_value": inst.get("payoutValue"),
    }


def binding_for_hot_path(row: dict) -> dict:
    """A stored binding, in the shape the execution engine expects.

    The engine was written against the live `bind_yes` result and must
    keep receiving exactly that shape, so the precomputed row is
    translated rather than the engine being taught a second one.
    """
    row = row or {}
    return {
        "bindingVersion": row.get("binding_version"),
        "verdict": row.get("identity_status", ident.NOT_IDENTIFIED),
        "executionEligible": bool(row.get("execution_eligible")),
        "identityBindingSha": row.get("identity_binding_sha"),
        "institutional": {
            "symbol": row.get("institutional_instrument_id"),
            "outcomeStrike": row.get("outcome_strike"),
            "eventOutcome": row.get("event_outcome"),
            "priceScale": row.get("price_scale"),
            "qtyScale": row.get("quantity_scale"),
            "payoutValue": row.get("payout_value"),
        },
        "retailLeg": row.get("outcome_leg"),
        "settlementEquivalence": row.get("settlement_equivalence"),
        "agree": row.get("agree") or [],
        "why": row.get("why") or [],
        # A one-to-one YES binding walks ONE instrument, so there is no
        # basket question to answer. The complement basket stays
        # separately gated by `assert_execution_eligible`.
        "basketWalkable": False,
        "preBound": True,
    }


def census(rows) -> dict:
    """§6's report, counted from the rows themselves."""
    rows = list(rows or [])
    status = {}
    for r in rows:
        status[r.get("identity_status")] = status.get(
            r.get("identity_status"), 0) + 1
    yes = [r for r in rows if r.get("outcome_leg") in ("yes", "long")]
    no = [r for r in rows if r.get("outcome_leg") in ("no", "short")]
    return {
        "FOCUS_MARKETS": len({r.get("market_id") for r in rows}),
        "IDENTITY_RESOLVED": len(
            [1 for r in rows
             if r.get("identity_status") not in (ident.NOT_IDENTIFIED, None)]),
        "YES_EXECUTION_ELIGIBLE": len(
            [1 for r in yes if r.get("execution_eligible")]),
        "NO_EXECUTION_ELIGIBLE": len(
            [1 for r in no if r.get("execution_eligible")]),
        "AMBIGUOUS": status.get(ident.AMBIGUOUS, 0),
        "UNRESOLVED": status.get(ident.NOT_IDENTIFIED, 0),
        "byStatus": status,
    }
