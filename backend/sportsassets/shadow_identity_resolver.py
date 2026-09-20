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

from . import shadow_contract_family as cf
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


def settlement_equivalence(binding: dict, family=None) -> str | None:
    """WHY these two keys name one economic contract, in words.

    Returned only for a verdict that actually established it. A
    sentence attached to an AMBIGUOUS binding would read as though
    something had been proven.

    THE SENTENCE IS PER FAMILY, because the two families establish
    equivalence differently and one sentence covering both would be
    true of neither.
    """
    verdict = (binding or {}).get("verdict")
    if verdict != ident.EXACT_ONE_TO_ONE:
        return None
    inst = (binding or {}).get("institutional") or {}
    outcome = inst.get("outcomeStrike")
    symbol = inst.get("symbol")

    if family == cf.BINARY_PROPOSITION:
        return (
            "the registered contract %r is a single binary proposition: "
            "the venue's own settlement rule settles it to Yes on one "
            "stated condition at strike %s, and the retail YES leg of the "
            "same registered id buys exactly that. Established from the "
            "cftc_instrument_id both venues quote, from the venue's own "
            "eventId + direction + strike composing to this key, and from "
            "both venues naming the same signed strike -- not from a "
            "title, a team name or a price."
            % (symbol, outcome))

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
        # ── the contract-family columns, present on EVERY row ────────
        # A row that simply omits them would be indistinguishable from
        # one resolved before this rule existed.
        "contract_family": cf.FAMILY_UNKNOWN,
        "settlement_rule": None,
        "settlement_prose_conflict": None,
        "strike_value": None,
        "evaluation_type": None,
        "long_participant_id": None,
        "short_participant_id": None,
        "complement_instrument_id": None,
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

    # ── WHICH FAMILY, AS THE VENUE DECLARES IT ───────────────────────
    #
    # The venue's own eventAttributes.eventOutcome says whether this is
    # a DIRECTIONAL binary proposition or a MUTUALLY_EXCLUSIVE set, and
    # the two need different proofs. Applying the multi-outcome rule to
    # a binary is what refused all 16 production bindings with "retail
    # leg 'yes' does not name outcome '1.5'": on a spread there is no
    # outcome NAME to match, because the contract is one proposition.
    fam = cf.family_of(instrument_record)
    row["contract_family"] = fam["family"]
    row["event_outcome"] = fam["eventOutcome"]

    if fam["family"] == cf.BINARY_PROPOSITION:
        binding = _binary_binding(instrument_record, retail_row, inst, rt,
                                  leg, row)
    else:
        # MULTI_OUTCOME_SET and anything unrecognised keep the stricter
        # rule exactly as it was. A family we have not seen is refused,
        # never assumed binary.
        binding = (ident.yes_leg_binding(inst, rt) if leg in ("yes", "long")
                   else ident.classify(inst, rt))

    row.update(_institutional_columns(binding.get("institutional") or inst))
    row.update({
        "retail_native_id": rt.get("identifier"),
        "event_slug": rt.get("eventSlug"),
        "identity_status": binding["verdict"],
        "execution_eligible": bool(binding.get("executionEligible")),
        "identity_binding_sha": binding.get("identityBindingSha"),
        "settlement_equivalence": settlement_equivalence(
            binding, fam["family"]),
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


def _binary_binding(instrument_record, retail_row, inst, rt, leg, row) -> dict:
    """§3: the YES leg of a directional contract, or an honest refusal.

    YES BINDS ONE-TO-ONE when the proof holds. `shadow_contract_family`
    establishes it from the registered contract id, the composition of
    the venue's own eventId + direction + strike, the signed strike
    agreeing across both venues, the participants belonging to this
    event, and a settlement rule that settles to Yes. Not from a title,
    not from a price, and not from slug equality on its own.

    NO STAYS HONEST. §4: "Do not convert it into NO_TRADE. Do not
    manufacture a NO book as 1-YES." The retail NO leg corresponds to
    the OPPOSITE directional instrument, which is a different contract
    the institutional venue may or may not list. Until that instrument
    is confirmed and priceable the NO side is recorded as structurally
    identified and pending -- with the candidate symbol named, so the
    next reader knows exactly what to go and confirm.
    """
    proof = cf.binary_identity(instrument_record, retail_row)
    prop = proof["proposition"]
    row["settlement_prose_conflict"] = proof.get("settlementProseConflict")
    row["settlement_rule"] = prop.get("settlementRule")
    row["strike_value"] = prop.get("strikeValue")
    row["evaluation_type"] = prop.get("evaluationType")
    row["long_participant_id"] = prop.get("longParticipantId")
    row["short_participant_id"] = prop.get("shortParticipantId")

    if leg not in ("yes", "long"):
        candidate = cf.complement_symbol(prop, rt.get("marketSlug"))
        row["complement_instrument_id"] = candidate
        verdict = (ident.STRUCTURAL_COMPLEMENT_PENDING if proof["proven"]
                   else ident.AMBIGUOUS)
        why = (["the retail NO leg of this binary proposition is the "
                "OPPOSITE directional instrument %r, which the "
                "institutional venue has not been asked to confirm; one "
                "side of a spread is not the complement of the other "
                "until the venue lists it and its book can be walked"
                % candidate]
               if proof["proven"] else list(proof["why"]))
        return {"bindingVersion": ident.BINDING_VERSION,
                "verdict": verdict, "executionEligible": False,
                "agree": list(proof["agree"]), "why": why,
                "institutional": inst, "retail": rt,
                "identityBindingSha": ident.binding_sha(
                    inst, rt, verdict)}

    verdict = (ident.EXACT_ONE_TO_ONE if proof["proven"]
               else ident.AMBIGUOUS)
    return {"bindingVersion": ident.BINDING_VERSION,
            "verdict": verdict,
            "executionEligible": verdict in ident.EXECUTION_ELIGIBLE,
            "agree": list(proof["agree"]), "why": list(proof["why"]),
            "institutional": inst, "retail": rt,
            "identityBindingSha": ident.binding_sha(inst, rt, verdict)}


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
