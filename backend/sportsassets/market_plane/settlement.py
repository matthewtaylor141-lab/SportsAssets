"""THE MARKET PLANE'S SETTLEMENT STATE, PER ACTIVE CONTRACT, FROM EVIDENCE ONLY.

MARKET DATA / RESEARCH ONLY. Decision-time `bettor_venue_settlement.attest`
(with `bettor_settlement_terms.compare_prose`'s condition -> payout
comparison) REMAINS THE CONTROLLING COMPATIBILITY DECISION FOR TRADING. This
module only says, for coverage, which contracts that decision -- or the same
comparison run on the contract's captured CURRENT rules text -- has proven,
and names precisely why the rest are not proven. Nothing here admits,
sizes, prices or orders anything.

Exactly one of five states:

  SETTLEMENT_PROVEN_COMPATIBLE
      basis DECISION_ATTEST: the latest decision valuation's settlement
      comparison is COMPATIBLE (and, where both are recorded, the rules
      fingerprint it was decided on is the one captured now); or
      basis RULES_TERMS_COMPARISON (a contract never valued):
      compare_prose(sport_family, "h2h", venue_prose=<captured rules text>,
      extra_book_terms=bettor_venue_settlement._legacy_book_terms(fam)) --
      the exact call attest makes -- returns COMPATIBLE with the bookmaker's
      terms HELD, and no tie clause the terms comparison does not cover.
  SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED
      basis DECISION_PRICED_SETTLEMENT_DIFFERENCE: the latest paper decision
      on the contract recorded bettor_settlement_difference_policy.
      eligibility as eligible AND the policy's own price (marker, id, CURRENT
      version, p) -- the priced branch of bettor_capital_eligibility.
      settlement_resolved, restated (no capital module is imported).
      A never-valued contract has no book probability to price and no
      completed-game match, so SDP.eligibility is run on its comparison and
      refuses by its own name: a price is never invented here.
  SETTLEMENT_RULE_EVIDENCE_CONFLICT
      the captured rule evidence is CONFLICT (an explicit structured rule the
      parsed text contradicts) or the venue's own text gives one condition
      two payouts. Fails closed, over any decision verdict.
  EXTERNAL_SETTLEMENT_DATA_UNAVAILABLE
      ONLY when the venue's own listing was read and carries no rules field.
  MAPPED_BUT_SETTLEMENT_NOT_PROVEN
      everything else, with a precise why (VENUE_RULES_NOT_CAPTURED,
      VENUE_RULES_SILENT_ON:<conditions>, BOOKMAKER_TERMS_NOT_HELD:<...>,
      INCOMPATIBLE_NOT_PRICEABLE:<conditions>:<policy refusal>,
      RULES_CHANGED_SINCE_DECISION_ATTEST, ...). Silence is never agreement.
"""
from __future__ import annotations

COMPATIBLE = "SETTLEMENT_PROVEN_COMPATIBLE"
DIFFERENT_BUT_PRICED = "SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED"
NOT_PROVEN = "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"
CONFLICT = "SETTLEMENT_RULE_EVIDENCE_CONFLICT"
EXTERNAL = "EXTERNAL_SETTLEMENT_DATA_UNAVAILABLE"
STATES = (COMPATIBLE, DIFFERENT_BUT_PRICED, NOT_PROVEN, CONFLICT, EXTERNAL)
PROVEN_STATES = (COMPATIBLE, DIFFERENT_BUT_PRICED)

BASIS_DECISION_ATTEST = "DECISION_ATTEST"
BASIS_DECISION_PRICED = "DECISION_PRICED_SETTLEMENT_DIFFERENCE"
BASIS_RULES_TERMS = "RULES_TERMS_COMPARISON"
BASIS_RULE_EVIDENCE = "RULE_EVIDENCE"
BASIS_NONE = "NO_EVIDENCE"

R_NO_COMPARISON = "NO_SETTLEMENT_COMPARISON_RECORDED"
R_VENUE_RULES_NOT_CAPTURED = "VENUE_RULES_NOT_CAPTURED"
R_VENUE_PUBLISHES_NO_RULES = "VENUE_PUBLISHES_NO_RULES_TEXT_FOR_THIS_CONTRACT"
R_VENUE_RULES_SILENT_ON = "VENUE_RULES_SILENT_ON"
R_BOOKMAKER_TERMS_NOT_HELD = "BOOKMAKER_TERMS_NOT_HELD"
R_INCOMPATIBLE_NOT_PRICEABLE = "INCOMPATIBLE_NOT_PRICEABLE"
R_RULES_CHANGED_SINCE_DECISION = "RULES_CHANGED_SINCE_DECISION_ATTEST"
R_TIE_RULE_NOT_COMPARED = "TIE_RULE_NOT_COVERED_BY_THE_TERMS_COMPARISON"
R_KALSHI_NOT_MAPPED = "KALSHI_CONTRACT_NOT_MAPPED_TO_A_BETTOR_FAMILY"
R_RULE_EVIDENCE_CONFLICT = "RULE_EVIDENCE_CONFLICT"
R_VENUE_SELF_CONTRADICTORY = "VENUE_RULES_SELF_CONTRADICTORY"

AUTHORITY_NOTE = ("coverage evidence only: decision-time bettor_venue_"
                  "settlement.attest remains the authority for trading")

#: the families whose money line has captured bookmaker terms
#: (bettor_settlement_terms.BOOK_TERMS); every other family / market is
#: BOOKMAKER_TERMS_NOT_HELD for a never-valued contract
H2H_FAMILIES = ("baseball", "basketball", "football", "hockey", "soccer")
#: compare_prose results by (rules fingerprint, family, league): a contract's
#: text is compared once per process, not once per coverage pass
_TERMS_CACHE: dict = {}
_TERMS_CACHE_MAX = 250_000


def _short(xs, n=8) -> str:
    xs = [str(x) for x in (xs or [])]
    return ",".join(xs[:n]) + (",+%d" % (len(xs) - n) if len(xs) > n else "")


def _rules_evidence(rules: dict | None) -> dict:
    r = dict(rules or {})
    ev = r.get("evidence") or {}
    if isinstance(ev, str):
        import json
        try:
            ev = json.loads(ev)
        except ValueError:
            ev = {}
    return {"rules_sha256": r.get("rules_sha256"),
            "rules_field": r.get("rules_field"),
            "rules_published": r.get("rules_published"),
            "parse_status": r.get("parse_status"),
            "parser_version": r.get("parser_version"),
            "source": r.get("source"),
            "matched": list(ev.get("matched") or []),
            "verification_sources": list(ev.get("verification_sources")
                                         or []),
            "special_conditions": list(ev.get("special_conditions") or []),
            "parsed_settlement": dict(ev.get("settlement") or {}),
            "conflicts": dict(ev.get("conflicts") or {})}


def h2h_family(contract: dict) -> str | None:
    """The bookmaker-terms family of a full-event WINNER contract, else None
    (a line, period or prop market has no captured money-line terms)."""
    sp = str(contract.get("sport") or "").lower()
    if sp in H2H_FAMILIES and str(contract.get("family") or "") == "WINNER" \
            and str(contract.get("period") or "") == "FULL_EVENT":
        return sp
    return None


def terms_comparison(*, sport_family, league, rules_text, rules_sha256):
    """compare_prose exactly as attest calls it, cached by fingerprint."""
    key = (rules_sha256, sport_family, league)
    if rules_sha256 and key in _TERMS_CACHE:
        return _TERMS_CACHE[key]
    from .. import bettor_settlement_terms as ST
    from .. import bettor_venue_settlement as V
    cmp_ = ST.compare_prose(sport_family=sport_family, market="h2h",
                            venue_prose=rules_text or "",
                            extra_book_terms=V._legacy_book_terms(
                                sport_family))
    slim = {"verdict": cmp_.get("verdict"),
            "book_terms_held": bool(cmp_.get("book_terms_held")),
            "per_condition": {c: {k: r.get(k) for k in (
                "verdict", "book_payout", "venue_payout")}
                for c, r in (cmp_.get("per_condition") or {}).items()},
            "mismatched_conditions": list(cmp_.get("mismatched_conditions")
                                          or []),
            "unstated_conditions": list(cmp_.get("unstated_conditions")
                                        or []),
            "venue_self_contradictory": list(
                cmp_.get("venue_self_contradictory") or []),
            "book_side_absent_refusals": list(
                cmp_.get("book_side_absent_refusals") or []),
            "refusal": cmp_.get("refusal")}
    if len(_TERMS_CACHE) > _TERMS_CACHE_MAX:
        _TERMS_CACHE.clear()
    if rules_sha256:
        _TERMS_CACHE[key] = slim
    return slim


def priced_resolved(priced: dict | None) -> bool:
    """The recorded paper decision's priced settlement difference, checked
    by the capital gate's own predicate (marker, policy id, CURRENT version,
    a priced p) and the recorded eligibility verdict."""
    p = dict(priced or {})
    elig = p.get("eligibility") or {}
    pol = p.get("policy") or {}
    if elig.get("eligible") is not True or pol.get("p") is None:
        return False
    # the priced branch of bettor_capital_eligibility.settlement_resolved,
    # restated so the market plane imports no capital module (pinned equal
    # to it by tests/test_settlement_rule_registry_integration.py)
    from .. import bettor_settlement_difference_policy as SDP
    return (pol.get("policy_id") == SDP.POLICY_ID
            and pol.get("version") == SDP.VERSION
            and pol.get("p") is not None)


def _out(state, why, basis, evidence):
    return {"state": state, "why": why, "basis": basis,
            "proven": state in PROVEN_STATES,
            "evidence": dict(evidence, basis=basis, note=AUTHORITY_NOTE)}


def state_for(contract: dict, *, valuation: dict | None = None,
              rules: dict | None = None, priced: dict | None = None,
              rules_looked_up: bool = False) -> dict:
    """PURE (apart from the cached terms comparison). The settlement state
    of one registry contract. `valuation`: the latest decision valuation
    (settlement_verdict, refusals, decision_rules_fingerprint); `rules`: its
    market_plane_rules row (with rules_text when a terms comparison is
    needed); `priced`: {"eligibility": ..., "policy": ...} from the latest
    paper decision; `rules_looked_up`: whether the rules table was read (an
    absent row then means VENUE_RULES_NOT_CAPTURED)."""
    c = dict(contract or {})
    ev = _rules_evidence(rules)
    v = dict(valuation or {})
    verdict = str(v.get("settlement_verdict") or "")
    refusals = [str(x) for x in (v.get("refusals") or [])]
    settle_codes = [x for x in refusals if x.startswith("SETTLEMENT")]
    ev["decision"] = ({"settlement_verdict": verdict or None,
                       "settlement_refusals": settle_codes[:6],
                       "decision_rules_fingerprint":
                           v.get("decision_rules_fingerprint")}
                      if v else None)

    # 1. CONFLICT fails closed, over anything a decision recorded
    if (rules or {}).get("parse_status") == "CONFLICT":
        return _out(CONFLICT, "%s:%s" % (R_RULE_EVIDENCE_CONFLICT,
                                         _short(sorted(ev["conflicts"]))),
                    BASIS_RULE_EVIDENCE, ev)

    cur_sha = (rules or {}).get("rules_sha256")
    dec_sha = v.get("decision_rules_fingerprint")
    changed = bool(cur_sha and dec_sha and cur_sha != dec_sha)

    # 2. A DECISION ON THE RECORD IS AUTHORITATIVE
    if v and (verdict or settle_codes):
        if verdict == "COMPATIBLE":
            if changed:
                return _out(NOT_PROVEN, R_RULES_CHANGED_SINCE_DECISION,
                            BASIS_DECISION_ATTEST, ev)
            return _out(COMPATIBLE, None, BASIS_DECISION_ATTEST, ev)
        if priced is not None and priced_resolved(priced):
            ev["priced"] = {k: (priced.get("policy") or {}).get(k) for k in (
                "policy_id", "version", "p", "q_hi")}
            if changed:
                return _out(NOT_PROVEN, R_RULES_CHANGED_SINCE_DECISION,
                            BASIS_DECISION_PRICED, ev)
            return _out(DIFFERENT_BUT_PRICED, None, BASIS_DECISION_PRICED, ev)
        why = (("SETTLEMENT_REFUSED:%s" % settle_codes[0]) if settle_codes
               else "SETTLEMENT_VERDICT:%s" % verdict)
        return _out(NOT_PROVEN, why, BASIS_DECISION_ATTEST, ev)

    # 3. NEVER ATTESTED: the captured current rules text
    if rules is None:
        return _out(NOT_PROVEN, R_VENUE_RULES_NOT_CAPTURED if rules_looked_up
                    else R_NO_COMPARISON, BASIS_NONE, ev)
    if not rules.get("rules_published"):
        return _out(EXTERNAL, R_VENUE_PUBLISHES_NO_RULES, BASIS_RULE_EVIDENCE,
                    ev)
    if str(rules.get("venue") or c.get("venue") or "") == "KALSHI":
        return _out(NOT_PROVEN, R_KALSHI_NOT_MAPPED, BASIS_RULE_EVIDENCE, ev)
    fam = h2h_family(c)
    if fam is None:
        return _out(NOT_PROVEN, "%s:%s/%s/%s" % (
            R_BOOKMAKER_TERMS_NOT_HELD, c.get("sport") or "UNKNOWN_SPORT",
            c.get("family") or "UNKNOWN_FAMILY",
            c.get("period") or "UNKNOWN_PERIOD"), BASIS_RULES_TERMS, ev)
    if rules.get("rules_text") is None:
        # the row is held but its text was not loaded for this pass
        return _out(NOT_PROVEN, R_VENUE_RULES_NOT_CAPTURED, BASIS_NONE, ev)
    cmp_ = terms_comparison(sport_family=fam, league=c.get("competition"),
                            rules_text=rules.get("rules_text"),
                            rules_sha256=cur_sha)
    ev["terms"] = cmp_
    if cmp_.get("venue_self_contradictory"):
        return _out(CONFLICT, "%s:%s" % (
            R_VENUE_SELF_CONTRADICTORY,
            _short(cmp_["venue_self_contradictory"])), BASIS_RULES_TERMS, ev)
    if cmp_.get("refusal"):
        return _out(NOT_PROVEN, "%s:%s" % (R_BOOKMAKER_TERMS_NOT_HELD,
                                           cmp_["refusal"]),
                    BASIS_RULES_TERMS, ev)
    if not cmp_.get("book_terms_held"):
        return _out(NOT_PROVEN, "%s:%s" % (
            R_BOOKMAKER_TERMS_NOT_HELD,
            _short(cmp_.get("book_side_absent_refusals")) or "NO_CAPTURE"),
            BASIS_RULES_TERMS, ev)
    vd = cmp_.get("verdict")
    if vd == "COMPATIBLE":
        from .. import bettor_venue_settlement as V
        ties = [s for s in ev["special_conditions"]
                if s in ("TIE_SCALAR_0_50", "TIE_MANUAL_REVIEW")]
        if ties and V.BOOK_OUTCOMES.get(fam) != 3:
            return _out(NOT_PROVEN, "%s:%s" % (R_TIE_RULE_NOT_COMPARED,
                                               _short(ties)),
                        BASIS_RULES_TERMS, ev)
        return _out(COMPATIBLE, None, BASIS_RULES_TERMS, ev)
    if vd == "INCOMPATIBLE":
        from .. import bettor_settlement_difference_policy as SDP
        el = SDP.eligibility(sport_family=fam, market="h2h",
                             league=c.get("competition"),
                             settlement={"per_condition":
                                         cmp_.get("per_condition") or {}},
                             lane_codes=(), precise_codes=(), match=None)
        ev["difference_policy"] = {k: el.get(k) for k in (
            "eligible", "refusal", "refusals", "why")}
        return _out(NOT_PROVEN, "%s:%s:%s" % (
            R_INCOMPATIBLE_NOT_PRICEABLE,
            _short(cmp_.get("mismatched_conditions")),
            el.get("refusal") or "ELIGIBLE_BUT_NO_BOOK_PROBABILITY_TO_PRICE"),
            BASIS_RULES_TERMS, ev)
    per = cmp_.get("per_condition") or {}
    venue_silent = [k for k, r in per.items() if r.get("verdict") in (
        "VENUE_STATES_NO_RULE_FOR_THIS_CONDITION",
        "NEITHER_SIDE_STATES_A_RULE_FOR_THIS_CONDITION")]
    if venue_silent:
        return _out(NOT_PROVEN, "%s:%s" % (R_VENUE_RULES_SILENT_ON,
                                           _short(venue_silent)),
                    BASIS_RULES_TERMS, ev)
    book_silent = [k for k, r in per.items() if r.get("verdict") ==
                   "BOOK_STATES_NO_RULE_FOR_THIS_CONDITION"]
    return _out(NOT_PROVEN, "%s:SILENT_ON:%s" % (R_BOOKMAKER_TERMS_NOT_HELD,
                                                 _short(book_silent)),
                BASIS_RULES_TERMS, ev)
