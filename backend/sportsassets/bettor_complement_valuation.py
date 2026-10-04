"""THE OTHER SIDE OF THE SAME VENUE CONTRACT, VALUED FROM THE SAME DE-VIG.

THE DEFECT (P0 incident, inc-edge, measured 2026-10-04 on the recorded
production rows -- research/incident_edge_inputs*.sql, runs 37233569900,
37233755356, 37234196320). The entry lane prices exactly ONE outcome per
provider event: the provider's HOME team (`ext_pinnacle_loop` passes
`selection = quote["home"]`; `bettor_venue_native_identity` documents
"priced_outcome: the provider's HOME team"). 5,050 valuations in 7 days,
the selection was the away side on none of them. So every paper strategy
could only ever BUY THE HOME SIDE of a contract:

  * MLB money lines (`aec-` contracts): the home team is the venue's SHORT
    side; the away team -- the LONG side of the SAME contract, the SAME book
    -- was never evaluated.
  * soccer per-side contracts (`atc-...-<home>`): YES on the home team was
    evaluated; NO on the same contract (home does not win in 90 minutes:
    draw or away) was never evaluated.

Every input of the home-side gross edge recomputes exactly (power de-vig
2797/2797 to 6e-13, American odds 3627/3627, consumed side 2876/2876, fees
and net EV 46/46), so the home side's BELOW_MIN_GROSS_EDGE refusals are
genuine economics. But on 17 completed-game V3 decisions (10 markets) in the
same window the OTHER side of the same contract, on the same observed book,
cleared the 0.5 pp gross threshold AND was positive net of the venue fee at
its best level -- legitimate candidates the pipeline never looked at, while
their home-side twin was recorded BELOW_MIN_GROSS_EDGE.

THE EQUIVALENCE, AND WHY IT IS EXACT. A venue contract is binary: its two
sides settle to complementary payouts (LONG is paid the YES settlement, SHORT
1 minus it -- `ext_pinnacle_loop.join_outcomes` and `paper_xavier.
venue_price_settlement` already settle every side that way). So the other
side of the contract that pays on the selection pays on NOT(selection), in
every state, including the exceptional ones (a last-fair-price settlement
pays the two sides S and 1 - S). And the de-vig normalises over the COMPLETE
outcome set (`bettor_pinnacle_devig.SUPPORTED`: 2 outcomes for baseball, 3
for soccer, refused otherwise), so P(NOT selection) = 1 - P(selection)
exactly: on a three-way book that is P(draw) + P(away), never P(away) alone
(`bettor_external_shadow.evaluate`'s own complement note).

NOTHING NEW IS INVENTED. The complement record is the SAME
`bettor_external_shadow.evaluate` call on the SAME contract identity, quote
and decision instant, with `payout_is_complement=True` (the lane's single
inversion, already in the vocabulary: `paper_benchmark.contract_match`
checks `payout_event == NOT(selection)`, `pinnapi_feed_runtime.held_quote`
values a held complement as 1 - p, `paper_benchmark.xavier_measure` and
`paper_maker` match valuations on payout_event and the complement flag) and
the OTHER intent, whose ladder is the other side of the one book already
read. No threshold, fee, freshness rule or settlement check changes; the
paper strategies decide the complement row exactly as they decide any row.

NEVER A FUNDED CANDIDATE. A complement is recorded only beside a
CALIBRATION_ONLY home record (every production valuation today: the venue
does not document its market-data timing, P5), and it is sealed
CALIBRATION_ONLY itself -- no executable price, no size, never admissible,
refused by name by every inventory / funded / reservation consumer. Beside
an ENTRY_DECISION home record nothing is written (counted by name): the
funded lane's candidate set is not this module's to widen.

Pure: no I/O, no database, no venue. The collector does the reads and the
write; this module only states what the complement IS.
"""
from __future__ import annotations

VERSION = "COMPLEMENT_SIDE_VALUATION_V1"
LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"

#: The payout-event basis written on the complement row (the home row's is
#: RESOLVER_MATCHED_A_SIDE_FOR_THE_REQUESTED_OUTCOME).
BASIS = ("COMPLEMENT_SIDE_OF_THE_SAME_BINARY_VENUE_CONTRACT_PAYS_ON_NOT_THE_"
         "SELECTION")
#: The de-vig sports whose complete outcome set makes 1 - p exact (a copy of
#: the keys of bettor_pinnacle_devig.SUPPORTED, pinned equal by a test so
#: this module imports nothing).
COMPLETE_SET_OUTCOMES = {("soccer", "h2h"): 3, ("baseball", "h2h"): 2}

# ── why no complement is written (counted by name, never silent) ──────
N_HOME_NOT_CALIBRATION_ONLY = "HOME_RECORD_IS_NOT_CALIBRATION_ONLY"
N_NO_PROBABILITY = "HOME_RECORD_HAS_NO_PROBABILITY"
N_ALREADY_COMPLEMENT = "HOME_RECORD_IS_ALREADY_A_COMPLEMENT"
N_INTENT = "HOME_INTENT_IS_NOT_A_BINARY_SIDE"
N_OUTCOME_SET = "OUTCOME_SET_NOT_COMPLETE_FOR_AN_EXACT_COMPLEMENT"
N_NO_SELECTION = "HOME_RECORD_HAS_NO_SELECTION"
N_OTHER_SIDE_EMPTY = "OTHER_SIDE_OF_THE_BOOK_DISPLAYS_NO_LEVEL"
NOT_WRITTEN = (N_HOME_NOT_CALIBRATION_ONLY, N_NO_PROBABILITY,
               N_ALREADY_COMPLEMENT, N_INTENT, N_OUTCOME_SET, N_NO_SELECTION,
               N_OTHER_SIDE_EMPTY)


def other_intent(intent) -> str | None:
    """The other side of a binary venue contract, or None."""
    s = str(intent or "")
    if s == LONG:
        return SHORT
    if s == SHORT:
        return LONG
    return None


def applies(rec: dict, contract: dict, other_displayed) -> tuple:
    """(True, None) when the complement of this HOME record is exact and
    may be written; (False, named reason) otherwise. Pure."""
    rec = rec if isinstance(rec, dict) else {}
    contract = contract if isinstance(contract, dict) else {}
    if rec.get("record_purpose") != "CALIBRATION_ONLY":
        return False, N_HOME_NOT_CALIBRATION_ONLY
    if rec.get("payout_is_complement"):
        return False, N_ALREADY_COMPLEMENT
    if rec.get("probability") is None:
        return False, N_NO_PROBABILITY
    if other_intent(contract.get("buy_intent")) is None:
        return False, N_INTENT
    if not str(contract.get("selection") or ""):
        return False, N_NO_SELECTION
    val = rec.get("valuation") or {}
    key = (str(contract.get("sport_family") or "").lower(),
           str(contract.get("market") or "").lower())
    want = COMPLETE_SET_OUTCOMES.get(key)
    if (want is None or int(val.get("expected_outcomes") or 0) != want
            or int(val.get("outcomes_priced") or 0) != want):
        return False, N_OUTCOME_SET
    od = other_displayed if isinstance(other_displayed, dict) else {}
    if not od.get("ok") or od.get("acquisition_price") is None:
        return False, N_OTHER_SIDE_EMPTY
    return True, None


def complement_contract(contract: dict) -> dict:
    """The SAME venue contract identity, bought on its other side. The
    selection and the probability's event stay the priced outcome (the
    de-vig prices it); the payout event becomes NOT(selection) inside
    `evaluate` (`payout_is_complement=True`), inverted exactly once."""
    c = dict(contract or {})
    c["buy_intent"] = other_intent(c.get("buy_intent"))
    side = str(c.get("ladder_side") or "")
    c["ladder_side"] = {"ASK": "BID", "BID": "ASK"}.get(side)
    c["payout_event"] = "NOT(%s)" % c.get("selection")
    c["payout_event_basis"] = BASIS
    c["probability_event"] = c.get("selection")
    # THE VENUE SIDE MATCHED IS NOT RE-READ: the resolver matched the
    # priced outcome's side; the other side is that contract's complement
    # by the venue's binary settlement, not by a second catalogue match.
    c["matched_side_norm"] = None
    c["complement_of"] = {"buy_intent": contract.get("buy_intent"),
                          "payout_event": contract.get("payout_event"),
                          "matched_side_norm": contract.get(
                              "matched_side_norm"),
                          "basis": BASIS, "version": VERSION}
    return c


def complement_evidence(home_evidence: dict, other_displayed: dict, *,
                        decision_instant: float, decision_lag_s) -> dict:
    """The calibration-only evidence of the complement record: the SAME
    refused venue read (its refusal, why and currency verdict), with the
    DISPLAYED price of the side the complement consumes -- flagged unusable
    for orders exactly as the home record's is."""
    ev = dict(home_evidence or {})
    shown = dict(other_displayed or {})
    shown["usable_for_orders"] = False
    return {"venue_read_refusal": ev.get("venue_read_refusal"),
            "venue_read_why": ev.get("venue_read_why"),
            "book_currency": ev.get("book_currency"),
            "displayed_quote": shown,
            "decision_instant_epoch_s": decision_instant,
            "decision_lag_s": decision_lag_s,
            "complement": {"version": VERSION, "basis": BASIS,
                           "same_read_as_the_home_record": True}}


def describe() -> dict:
    return {"version": VERSION, "basis": BASIS,
            "complete_set_outcomes": {"%s/%s" % k: v for k, v in
                                      COMPLETE_SET_OUTCOMES.items()},
            "written_only_beside": "CALIBRATION_ONLY home records",
            "record_purpose": "CALIBRATION_ONLY (never admissible)",
            "not_written_reasons": list(NOT_WRITTEN),
            "probability": "1 - p(selection), complete-set de-vig, once"}
