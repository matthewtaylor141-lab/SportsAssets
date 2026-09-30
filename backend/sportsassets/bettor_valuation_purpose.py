"""WHAT A VALUATION RECORD IS FOR, AND WHY ONE KIND CAN NEVER TRADE.

THE SITUATION THIS EXISTS FOR. The external-valuation lane writes one
`external_valuations` row per candidate it evaluates. Since d66e89e
(2026-09-27) `venue_quote` refuses every book whose currency no published
mechanism establishes -- which is every book today, because the venue has
not documented its market-data timing (bettor_stream_currency P5). The row
was only ever written after an ok venue read, so no row was written at all,
and the odds-source calibration cohort (Pinnacle's de-vigged probability
against the venue's own settlement) stopped growing on 2026-09-27.

Calibration measures the ODDS SOURCE. It needs the probability, the fixture
and the venue contract whose settlement will label it; it does not need a
tradable venue price. So the lane now also writes the valuation when the
venue read refused for currency -- as a record whose PURPOSE is calibration
and nothing else.

"NOT ADMISSIBLE" IS NOT ENOUGH, and the owner said so (D4 addendum A). An
`admissible` flag is one boolean a later edit could flip. So the purpose is
its own column (migration 144) with its own constraints, and every consumer
that could turn a valuation into inventory, an order, a reservation or an
exposure REFUSES a calibration-only record BY NAME, whatever `admissible`
says:

    ENTRY_DECISION    the lane's ordinary record of an entry decision --
                      admitted or refused. The column default, so every row
                      written before migration 144 is one.
    CALIBRATION_ONLY  the venue read refused (book currency not established,
                      or contradicted); the valuation is recorded so the
                      calibration cohort can grow. It is never admissible,
                      carries no executable price, no size and no plan, and
                      the venue price it was compared against is a DISPLAYED
                      price flagged unusable for orders.

This module is pure constants and one predicate, imported by the writer and
by every consumer, so the name and the rule cannot drift between them.
"""

from __future__ import annotations

ENTRY_DECISION = "ENTRY_DECISION"
CALIBRATION_ONLY = "CALIBRATION_ONLY"
PURPOSES = (ENTRY_DECISION, CALIBRATION_ONLY)

#: The refusal every entry/funded consumer returns for a calibration-only
#: record. One name everywhere, so a census can count it.
R_CALIBRATION_ONLY = "CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE"

#: A purpose that is neither of the two. Refused rather than defaulted: a
#: typo must not become an entry decision.
R_UNKNOWN_PURPOSE = "VALUATION_RECORD_PURPOSE_NOT_RECOGNISED"

#: What the venue price on a calibration-only record is. Read the same way
#: `ext_pinnacle_loop.observation_quote` labels its displayed prices.
DISPLAYED_NOT_AN_ORDER_PRICE = (
    "A DISPLAYED PRICE, RECORDED BESIDE A CALIBRATION VALUATION. NOT AN ORDER "
    "PRICE: the book's currency was not established, so nothing may be sized, "
    "reserved or sent against it")

WHY_CALIBRATION_ONLY_CANNOT_TRADE = (
    "a calibration-only record exists so the odds source can be scored "
    "against the venue's settlement. Its venue read was REFUSED (book currency "
    "not established), so it has no executable price, no size, no execution "
    "estimate and no risk verdict, and the database forbids it from being "
    "admissible (migration 144). Every consumer that could create inventory, "
    "an order, a reservation or an exposure refuses it by name, whatever its "
    "admissible flag says")


def purpose_of(rec) -> str:
    """The record's purpose, fail-closed.

    A record CARRYING calibration-only evidence is calibration-only even if
    its purpose key was lost -- the evidence is only ever attached by the
    calibration path. A record with no purpose at all predates this module
    (or is a test's hand-built entry record) and is an ENTRY_DECISION, which
    is also the column default. Anything else is returned verbatim so the
    caller refuses it as unrecognised.
    """
    rec = rec if isinstance(rec, dict) else {}
    if rec.get("calibration_only_evidence") is not None:
        return CALIBRATION_ONLY
    got = rec.get("record_purpose")
    if got is None:
        return ENTRY_DECISION
    return str(got)


def refuse_unless_entry(rec=None, *, purpose=None) -> dict | None:
    """None when the record may be considered as an entry, else the refusal.

    `purpose` may be passed directly by a consumer that holds only the
    purpose (e.g. `bind_payout_outcome`, which is handed identifiers rather
    than the record). Never raises.
    """
    p = str(purpose) if purpose is not None else purpose_of(rec)
    if p == ENTRY_DECISION:
        return None
    if p == CALIBRATION_ONLY:
        return {"ok": False, "refusal": R_CALIBRATION_ONLY,
                "record_purpose": p, "why": WHY_CALIBRATION_ONLY_CANNOT_TRADE}
    return {"ok": False, "refusal": R_UNKNOWN_PURPOSE, "record_purpose": p,
            "why": ("the record names purpose %r, which is neither %s nor %s. "
                    "An unrecognised purpose is refused, never read as an "
                    "entry decision" % (p, ENTRY_DECISION, CALIBRATION_ONLY))}
