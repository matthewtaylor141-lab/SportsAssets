"""LAB-B: THE PRE-PUBLICATION CITATION-INTEGRITY VERIFIER (pure).

The owner's four failure examples (2026-10-04), each as a test:
  * "149.3 seconds stale" citing the OLDER 75.2-second review
      -> STALE_STATE_CITATION, re-cited to the current review
  * the account's cash from F143 cited as F139
      -> WRONG_FACT (cross-record substitution), re-cited to F143
  * a review called CURRENT whose cited fact is SUPERSEDED
      -> STALE_STATE_CITATION
  * a valid record of the WRONG position
      -> ENTITY_MISMATCH, re-cited to the named position's record
and the rules around them: the tolerance (written precision, measure words,
percent units), NO_CITATION, INSUFFICIENT_SUPPORT, repair only when EXACTLY
ONE fact supports the material, the gate's actions (a model reply falls back,
a records-only answer states the integrity failure), and that the records-only
composer's own answers verify.
"""

from decimal import Decimal

from sportsassets.lab import citation_integrity as CI
from sportsassets.lab import citation_integrity_store as CIS

G = "paperexpgrp:4f2a91c0"
G2 = "paperexpgrp:9b77e1d3"


def F(fid, text, *, source="paper_ledger", record_id="r", field="x",
      value=None):
    return {"fact_id": fid, "source": source, "record_id": record_id,
            "field": field, "value": value, "text": text}


def reviews():
    """Xavier's CURRENT and SUPERSEDED reviews of one paper position, in the
    exact text persona_facts.xavier_decision_text writes."""
    return [
        F("F11", "CURRENT Xavier management decision for paper position %s: "
                 "HOLD_ON_STALE_PROBABILITY; review rv-8812 at "
                 "2026-10-04T18:20:11+00:00; probability pinnacle, 149.300 s "
                 "old at the review (freshness limit 30 s), valuation 77120"
          % G, source="paper_xavier_reviews", record_id="rv-8812",
          field="current_management_decision",
          value="HOLD_ON_STALE_PROBABILITY"),
        F("F12", "SUPERSEDED Xavier review of paper position %s (history, NOT "
                 "the current decision; superseded by rv-8812): recorded HOLD; "
                 "review rv-8790 at 2026-10-04T18:18:57+00:00; probability "
                 "pinnacle, 75.200 s old at the review (freshness limit 30 s), "
                 "valuation 77101" % G, source="paper_xavier_reviews",
          record_id="rv-8790", field="superseded_review", value="HOLD"),
    ]


def account_facts():
    """A book's fact list in which F139 (the legacy desk account) and F143
    (the paper ledger's cash) are both real records."""
    filler = [F("F%d" % i, "Derek's entry decision drk-%d: REFUSE" % i,
                source="derek_entry_decisions", record_id="drk-%d" % i)
              for i in range(130, 139)]
    return filler + [
        F("F139", "LEGACY desk account desk-acct-07 (bettor_desk_account_"
                  "state; NOT the paper account) cash $97,958.35 as of "
                  "2026-10-03T22:00:00+00:00 (started $100,000, realised P&L "
                  "-$2,041.65, fees $0)", source="bettor_desk_account_state",
          record_id="desk-acct-07", field="legacy_desk_cash_usd",
          value=97958.35),
        F("F140", "paper account paper-main reserved $1,250 (part of cash, "
                  "not extra)", record_id="paper-main#seq412",
          field="paper_reserved_usd", value=1250.0),
        F("F141", "paper account available $498,750",
          record_id="paper-main#seq412", field="paper_available_usd",
          value=498750.0),
        F("F142", "paper realised P&L $2.91", record_id="paper-main#seq412",
          field="paper_realized_pnl_usd", value=2.91),
        F("F143", "paper account paper-main cash $500,000 (from the paper "
                  "ledger)", record_id="paper-main#seq412",
          field="paper_cash_usd", value=500000.0),
    ]


def one(text, facts, **kw):
    r = CI.verify(text, facts, **kw)
    sents = [s for s in r["sentences"] if s["verdict"] is not None]
    assert len(sents) == 1, [s["text"] for s in sents]
    return sents[0]


# ── the owner's four examples ───────────────────────────────────────

def test_149_3_seconds_citing_the_older_75_2_second_review_is_stale():
    facts = reviews()
    s = one("The probability was 149.3 seconds stale at Xavier's review "
            "[F12].", facts)
    assert s["verdict"] == CI.STALE_STATE_CITATION
    assert s["failing"][0]["token"] == "149.3"
    assert s["failing"][0]["alt"] == ["F11"]
    g = CI.gate("The probability was 149.3 seconds stale at Xavier's review "
                "[F12].", facts)
    assert g["action"] == CI.A_REPAIRED
    assert g["text"] == ("The probability was 149.3 seconds stale at "
                         "Xavier's review [F11].")
    assert g["after"]["passed"]
    assert g["sentences"][0]["repaired_ids"] == ["F11"]
    # citing the right review passes; so does its own figure on the old one
    assert one("Xavier's current review shows the probability 149.3 s old "
               "[F11].", facts)["verdict"] == CI.PASS
    assert one("The older review had it 75.2 seconds old [F12].",
               facts)["verdict"] == CI.PASS


def test_cash_from_f143_cited_as_f139_is_a_wrong_fact_and_is_recited():
    facts = account_facts()
    text = "The paper account holds $500,000 in cash [F139]."
    s = one(text, facts)
    assert s["verdict"] == CI.WRONG_FACT
    # the available balance holds the same number but is a DIFFERENT measure
    # of the same account: not a candidate (no coincidental support)
    assert s["failing"][0]["alt"] == ["F143"]
    g = CI.gate(text, facts)
    assert g["action"] == CI.A_REPAIRED
    assert g["text"] == "The paper account holds $500,000 in cash [F143]."


def test_a_review_called_current_whose_cited_fact_is_superseded():
    facts = reviews()
    s = one("Xavier's current decision on the position is HOLD [F12].", facts)
    assert s["verdict"] == CI.STALE_STATE_CITATION
    assert any(f["kind"] == CI.K_STATUS for f in s["failing"])
    # exactly one CURRENT-marked fact holds HOLD (as the leading part of its
    # code): repaired to it
    g = CI.gate("Xavier's current decision on the position is HOLD [F12].",
                facts)
    assert g["action"] == CI.A_REPAIRED and "[F11]" in g["text"]
    # naming the superseded review's own id as CURRENT cannot be repaired:
    # no current record holds rv-8790 -- the model reply is discarded
    g = CI.gate("The CURRENT review rv-8790 recommends HOLD [F12].", facts)
    assert g["before"]["sentences"][0]["verdict"] == CI.STALE_STATE_CITATION
    assert g["action"] == CI.A_FALLBACK and g["text"] is None
    assert g["reason"] == "CITATION_INTEGRITY:STALE_STATE_CITATION"


def test_a_valid_record_of_the_wrong_position_is_an_entity_mismatch():
    facts = reviews() + [
        F("F13", "CURRENT Xavier management decision for paper position %s: "
                 "EXIT; review rv-9001 at 2026-10-04T18:21:40+00:00; "
                 "probability pinnacle, 12.400 s old at the review (freshness "
                 "limit 30 s), valuation 77130" % G2,
          source="paper_xavier_reviews", record_id="rv-9001",
          field="current_management_decision", value="EXIT")]
    text = "Position %s is on EXIT [F11]." % G2
    s = one(text, facts)
    assert s["verdict"] == CI.ENTITY_MISMATCH
    g = CI.gate(text, facts)
    assert g["action"] == CI.A_REPAIRED
    assert g["text"] == "Position %s is on EXIT [F13]." % G2
    # a figure from the right kind of record but of the other position
    s = one("Position %s's probability was 149.3 s old [F13]." % G2, facts)
    assert s["verdict"] in (CI.ENTITY_MISMATCH, CI.WRONG_FACT)


# ── the other verdicts ──────────────────────────────────────────────

def test_no_citation_and_insufficient_support():
    facts = account_facts()
    s = one("The paper account holds $500,000 in cash.", facts)
    assert s["verdict"] == CI.NO_CITATION
    # one fact supports it: the citation is added deterministically
    g = CI.gate("The paper account holds $500,000 in cash.", facts)
    assert g["action"] == CI.A_REPAIRED
    assert g["text"] == "The paper account holds $500,000 in cash [F143]."
    # no single fact to cite (two facts hold $1,250? one does; 2026-10-03
    # is in several): the model reply is KEPT with the explicit statement
    g = CI.gate("The desk was last updated on 2026-10-03.",
                facts + [dict(facts[-1], fact_id="F144",
                              text="paper ledger as of 2026-10-03")])
    assert g["action"] == CI.A_STATED and g["verdict"] == CI.NO_CITATION
    assert g["text"].endswith("(Integrity check: it states a figure or "
                              "record without citing the evidence.)")
    s = one("Fees today were $3.17 [F142].", facts)
    assert s["verdict"] == CI.INSUFFICIENT_SUPPORT
    g = CI.gate("Fees today were $3.17 [F142].", facts)
    assert g["action"] == CI.A_FALLBACK


def test_ambiguity_is_never_resolved_by_guessing():
    facts = [F("F1", "hedge cost $800", field="hedge_cost_usd", value=800.0),
             F("F2", "standing order cost $800", field="cost_usd",
               value=800.0),
             F("F3", "unhedged, a Yankees win pays $1,000",
               field="unhedged_best_case_usd", value=1000.0)]
    g = CI.gate("We spent $800 on protection [F3].", facts)
    assert g["before"]["sentences"][0]["verdict"] == CI.WRONG_FACT
    assert g["action"] == CI.A_FALLBACK
    assert g["repairs"][0]["repaired"] is False
    assert g["repairs"][0]["why"].startswith("2_FACTS")


def test_tolerance_precision_measures_and_percent_units():
    facts = [F("F1", "blended probability 0.585", field="p_blended",
               value=0.585),
             F("F2", "desk cash $97,958.35", field="cash_usd",
               value=97958.35),
             F("F3", "edge 9 pp = 0.59 - 0.50", field="gross_edge_pp",
               value=9.0)]
    ok = lambda t: one(t, facts)["verdict"] == CI.PASS          # noqa: E731
    assert ok("The blend is 0.59 [F1].")          # rounded to 2 places
    assert ok("The blend is 0.585 [F1].")
    assert ok("About 58.5% [F1].")                # percent unit: x100
    assert ok("Desk cash $97,958 [F2].")          # its own rounding
    assert not ok("Desk cash $98k [F2].")         # no significant-figure
    assert not ok("The blend is 0.6 [F1].")       # one digit, 2.6% off
    assert ok("Edge 9 pp [F3].")
    assert not ok("Desk cash $97,000 [F2].")
    # a bare 0, 1 or 2 is a counting word; the question's own figures echo
    assert CI.verify("One of the two legs pays [F3].", facts)["material"] \
        == 0
    assert CI.verify("If we bought 500 contracts, the blend is 0.59 [F1].",
                     facts, question="what if we bought 500 contracts?")[
        "passed"]


def test_measure_words_read_the_same_way_on_both_sides():
    a = CI.measures_near("paper account reserved $0 (part of cash, not extra)",
                         23, 25)
    assert a == frozenset({"RESERVED"})
    t = "filled 2,000 contracts for $1,000 plus $10 fees"
    i = t.index("$1,000")
    assert CI.measures_near(t, i, i + 6) == frozenset()
    i = t.index("$10")
    assert CI.measures_near(t, i, i + 3) == frozenset({"FEES"})
    t = "149.300 s old at the review (freshness limit 30 s)"
    assert CI.measures_near(t, 0, 9) == frozenset({"AGE"})
    i = t.index("30 s")
    assert "LIMIT" in CI.measures_near(t, i, i + 4)
    t = ("cash deltas summing to $499,404.86 against cash of $499,404.86 "
         "and reserved deltas of $98.92")
    i = t.index("$499,404.86 and")
    assert CI.measures_near(t, i, i + 11) == frozenset({"CASH"})
    t = "Available of $499,305.94 equals cash minus reserved"
    i = t.index("$")
    assert CI.measures_near(t, i, i + 11) == frozenset({"AVAILABLE"})
    t = "the probability was 149.3 s old"
    i = t.index("149.3")
    assert CI.measures_near(t, i, i + 7) == frozenset({"AGE", "PROBABILITY"})
    assert not CI.measures_compatible(frozenset({"AGE"}),
                                      frozenset({"LIMIT", "PRICE"}))
    assert CI.measures_compatible(frozenset({"CASH"}), frozenset())


def test_a_citation_before_its_clause_and_several_groups_in_a_sentence():
    facts = account_facts()
    # each figure bound to the citation that closes its clause
    good = ("Right now the paper account holds $500,000 in cash [F143], "
            "$1,250 reserved [F140] and $498,750 available [F141].")
    assert CI.verify(good, facts)["passed"]
    # a citation written just before its clause is accepted
    assert CI.verify("The paper account [F143] holds $500,000 in cash, with "
                     "$1,250 reserved [F140].", facts)["passed"]
    # swapped citations: caught even though both facts are cited
    bad = ("The paper account holds $500,000 in cash [F141] and $498,750 "
           "available [F143].")
    r = CI.verify(bad, facts)
    assert r["sentences"][0]["verdict"] == CI.WRONG_FACT


def test_an_unknown_fact_id_never_supports_anything():
    facts = account_facts()
    s = one("Cash is $500,000 [F999].", facts)
    assert s["unknown_ids"] == ["F999"]
    assert s["verdict"] == CI.WRONG_FACT


def test_ids_timestamps_codes_and_attribution():
    facts = reviews() + [F("F20", "Derek's entry decision drk-44 on X at "
                                  "2026-10-04T18:00:00+00:00: REFUSE -- "
                                  "refused: BELOW_MIN_GROSS_EDGE",
                           source="derek_entry_decisions",
                           record_id="drk-44")]
    assert one("Derek's entry decision drk-44 refused it as "
               "BELOW_MIN_GROSS_EDGE [F20].", facts)["verdict"] == CI.PASS
    assert one("Derek refused it at 18:00 [F20].", facts)["verdict"] == \
        CI.PASS
    assert one("Derek refused it at 18:00:00.123456 [F20].", facts)[
        "verdict"] == CI.PASS                    # fractional seconds
    s = one("Derek refused it at 18:05 [F20].", facts)
    assert s["verdict"] == CI.INSUFFICIENT_SUPPORT
    s = one("Derek's decision was HOLD_ON_STALE_PROBABILITY [F11].", facts)
    assert s["verdict"] == CI.ENTITY_MISMATCH        # Xavier's record
    assert one("The refusal was MIN_GROSS_EDGE [F20].", facts)[
        "verdict"] == CI.PASS                    # a run of the code's parts
    s = one("The refusal was SETTLEMENT_NOT_SUPPORTED [F20].", facts)
    assert s["verdict"] == CI.INSUFFICIENT_SUPPORT


def test_an_interrupted_partial_leaves_its_unfinished_fragment_out():
    facts = account_facts()
    r = CI.verify("Cash is $500,000 [F143]. Reserved is $1,2", facts,
                  partial=True)
    assert r["passed"] and len(r["sentences"]) == 1
    g = CI.gate("Fees were $3.17 [F142]. And", facts, partial=True)
    assert g["action"] == CI.A_STATED
    assert g["text"].startswith("Fees were $3.17 [F142]. (Integrity check:")
    assert g["text"].endswith(" And")


def test_a_records_only_answer_states_the_failure_instead_of_falling_back():
    facts = account_facts()
    g = CI.gate("Fees were $3.17 [F142].", facts,
                composer=CI.COMPOSER_RECORDS)
    assert g["action"] == CI.A_STATED
    assert "(Integrity check: no record in the evidence holds" in g["text"]
    assert g["sentences"][0]["action"] == CI.A_STATED
    # the statement itself is never verified again as a claim
    again = CI.verify(g["text"], facts)
    assert again["counts"][CI.INSUFFICIENT_SUPPORT] == 1


def test_a_quoted_fact_with_sentences_of_its_own_stays_one_unit():
    fact = F("F4", "Challenge kch:0123456789abcdef01234567 (HIGH, freshness) "
                   "to XAVIER about review rv-8790: The HOLD stood on a 75.2 s "
                   "probability. State: OPEN.", source="karen_challenges",
             record_id="kch:0123456789abcdef01234567")
    text = "What I'm challenging: %s [F4]." % fact["text"]
    r = CI.verify(text, [fact])
    assert len(r["sentences"]) == 1 and r["passed"]


def test_retro_tally_counts_and_the_inferred_uncited_source():
    facts = account_facts()
    llm = CI.verify("Cash is $500,000 [F141]. Fees were $3.17 [F142]. "
                    "Available $498,750 [F141].", facts)
    rec = CI.verify("Cash is $500,000 [F143].", facts)
    t = CI.retro_tally([("XAVIER", "LLM", llm), ("XAVIER", "RECORDS_ONLY",
                                                 rec)])
    x = t["by_agent"]["XAVIER"]
    assert x["LLM"]["verdicts"][CI.WRONG_FACT] == 1
    assert x["LLM"][CI.INFERRED_UNCITED_SOURCE] == 1
    assert x["LLM"]["answers_with_wrong_support"] == 1
    assert x["ALL"]["answers"] == 2 and x["ALL"]["cited_material"] == 4
    assert x["RECORDS_ONLY"][CI.INFERRED_UNCITED_SOURCE] == 0


def test_wilson_and_the_scorecard_metrics_from_rows():
    assert CIS.wilson(0, 0) is None
    lo, hi = CIS.wilson(3, 100)
    assert 0.009 < lo < 0.011 and 0.084 < hi < 0.086
    win = CIS.window(1000.0, 100.0)
    empty = CIS.metrics_from_rows("XAVIER", [], [], win)
    assert [m["metric"] for m in empty] == list(CIS.CITATION_METRICS)
    assert all(m["status"] == CIS.UNAVAILABLE and m["value"] is None and
               m["reason"] == "NO_CHECKED_ANSWERS_IN_WINDOW" for m in empty)
    checks = [{"check_id": 1, "primary_text": True, "stage": CI.ST_MODEL,
               "action": CI.A_FALLBACK, "material_sentences": 4,
               "cited_material_sentences": 3,
               "verdict_counts": {"PASS": 1, "WRONG_FACT": 1,
                                  "STALE_STATE_CITATION": 1,
                                  "NO_CITATION": 1}},
              {"check_id": 2, "primary_text": False,
               "stage": CI.ST_RECORDS, "action": CI.A_VERIFIED,
               "material_sentences": 9, "cited_material_sentences": 9,
               "verdict_counts": {"PASS": 9}}]
    verdicts = [{"check_id": 1, "verdict": v, "action": CI.A_FALLBACK}
                for v in ("PASS", "WRONG_FACT", "STALE_STATE_CITATION",
                          "NO_CITATION")]
    m = {x["metric"]: x for x in CIS.metrics_from_rows("XAVIER", checks,
                                                       verdicts, win)}
    assert m["citation_coverage_pct"]["value"] == 75.0
    assert m["wrong_fact_rate"]["value"] == round(1 / 3, 6)
    assert m["stale_state_citation_rate"]["numerator"] == 1
    assert m["records_only_fallback_rate"]["value"] == 1.0
    assert m["correction_rate"]["value"] == 0.0
    assert m["unsupported_material_claim_rate"]["value"] == 0.0
    assert all(x["status"] == CIS.SMALL for x in m.values())
    assert m["wrong_fact_rate"]["detail"]["interval95"][1] > 0.33


def test_decimal_helpers():
    assert CI._sig_digits(Decimal("0.59")) == 2
    assert CI._sig_digits(Decimal("1")) == 1
    assert CI._sig_digits(Decimal("97958")) == 5


def test_a_comma_list_citation_is_stored_and_unstored_facts_are_unverifiable():
    from sportsassets.agents import persona_chat as PC
    facts = account_facts()
    got = PC.cited_facts("Cash $500,000 [F143, F140]; P&L [F142].", facts)
    assert {f["fact_id"] for f in got} == {"F143", "F140", "F142"}
    # the retrospective never judges a sentence whose cited fact the stored
    # record lacks (stored facts here: F142 only)
    stored = [f for f in facts if f["fact_id"] == "F142"]
    rep = CI.verify("Cash is $500,000 [F143, F142]. P&L $2.91 [F142].",
                    stored)
    t = CI.retro_tally([("DEREK", "LLM", rep)])["by_agent"]["DEREK"]["LLM"]
    assert t[CI.UNVERIFIABLE_NOT_STORED] == 1
    assert t["verdicts"][CI.PASS] == 1
    assert t[CI.INFERRED_UNCITED_SOURCE] == 0
    assert t["answers_with_wrong_support"] == 0


def test_a_quoted_records_own_citation_tokens_are_not_citations():
    """A stored lesson quoting an earlier answer's "[F72]" is the record's
    text: quoting it cites the lesson, never F72 (production research run
    37234402124)."""
    from sportsassets.agents import persona_chat as PC
    lesson = F("F5", "STORED OBSERVATION: an earlier answer said cash was "
                     "$500,000 [F143] and P&L $2.91 [F142]. HISTORICAL.",
               source="paper_agent_lessons", record_id="les-1",
               field="lesson")
    facts = account_facts() + [lesson]
    text = "My lessons: %s [F5]." % lesson["text"]
    assert CI.cited_fact_ids(text, facts) == ["F5"]
    assert [f["fact_id"] for f in PC.cited_facts(text, facts)] == ["F5"]
    r = CI.verify(text, facts)
    assert r["passed"] and r["sentences"][0]["cited_ids"] == ["F5"]
    assert r["sentences"][0]["text"] == text      # reported as written
    # outside a quotation the same token IS a citation
    assert CI.cited_fact_ids("Cash $500,000 [F143].", facts) == ["F143"]


def test_action_words_in_proposals_are_vocabulary_not_claims():
    """Production retrospective (FULL profile over the stored answers): a
    lone HOLD / REDUCE in a proposal, or a subject id in a "measurable
    outcome", is not a claim about a record; an assertion is."""
    facts = reviews()
    for t in ("That review should either reconfirm HOLD or select a "
              "different action.",
              "Repair: record the shortfall on every BELOW_MIN_GROSS_EDGE "
              "refusal.",
              "Record the threshold on every BELOW_MIN_GROSS_EDGE refusal.",
              "The measurable outcome is a stored review for %s." % G,
              "REDUCE halves the tail but still pays fees."):
        assert CI.verify(t, facts)["material"] == 0, t
    # assertions about a record still need their citation
    for t in ("My current management state is WAITING_FOR_FRESH_EVIDENCE.",
              "The running session is paper_session_20261001T014716Z.",
              "Derek's decision papercg:d7a0ff395098767c recorded ENTER."):
        assert CI.verify(t, facts)["counts"][CI.NO_CITATION] == 1, t
    # a cited proposal still has its figures checked
    s = one("The review should use a probability under 30 s old, not "
            "149.3 s [F12].", facts)
    assert s["verdict"] == CI.STALE_STATE_CITATION


def test_the_production_defect_classes_found_by_the_retrospective():
    """Shapes measured in production (research run 37234801958, sanitised):
    (1) a figure from one record cited to sibling records that hold only a
    state -- the quantity's own record is never cited; (2) a price cited to
    the probability's record; (3) a figure attributed to another agent's
    claim cited to the agent's own evidence."""
    facts = [F("F227", "paper paper_orders state = EXPIRED",
               source="paper_orders", record_id="paperord:aa11bb22cc33",
               field="state", value="EXPIRED"),
             F("F239", "paper paper_orders state = EXPIRED",
               source="paper_orders", record_id="paperord:dd44ee55ff66",
               field="state", value="EXPIRED"),
             F("F165", "paper paper_orders qty = 566.0",
               source="paper_orders", record_id="paperord:aa11bb22cc33",
               field="qty", value=566.0),
             F("F110", "paper paper_decisions p_pinnacle = 0.248708693850303",
               source="paper_decisions", record_id="paperdec:1",
               field="p_pinnacle", value=0.248708693850303),
             F("F114", "paper paper_decisions executable_price = 0.26",
               source="paper_decisions", record_id="paperdec:1",
               field="executable_price", value=0.26)]
    s = one("Three standing-protection sell orders for 566 units expired "
            "[F227][F239].", facts)
    assert s["verdict"] == CI.WRONG_FACT
    assert s["failing"][0]["alt"] == ["F165"]
    g = CI.gate("Three standing-protection sell orders for 566 units expired "
                "[F227][F239].", facts)
    # the one record that holds 566 is cited beside the state records
    assert g["action"] == CI.A_REPAIRED
    assert "[F227] [F239] [F165]" in g["text"]
    s = one("It paid 0.26 against a Pinnacle probability of "
            "0.248708693850303 [F110].", facts)
    assert s["verdict"] == CI.WRONG_FACT and s["failing"][0]["alt"] == \
        ["F114"]
