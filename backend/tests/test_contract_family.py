"""CONTRACT FAMILIES, AND THE MUTATIONS THAT MUST NOT PASS.

Owner production check 2026-09-20. All 16 production bindings came
back AMBIGUOUS with "retail leg 'yes' does not name outcome '1.5'".
The venue declares every one of them EVENT_OUTCOME_DIRECTIONAL while
the MLS first-to-score set it was being judged against declares
MUTUALLY_EXCLUSIVE.

THE FIXTURES ARE PRODUCTION ROWS, not inventions. SPREAD is
asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5 exactly as the venue returned
it in run 35479721534, including the two settlement sentences that
contradict each other. MLS is the three-outcome set the stricter rule
was built for.

WHAT THE MUTATIONS ARE FOR. "Mutation-test the distinction so the MLS
three-outcome case cannot accidentally pass through the binary rule."
Each mutation below changes ONE venue field and asserts the binding
refuses -- so a future edit that loosens any single condition fails
here rather than in production with money attached.
"""

from __future__ import annotations

import copy

import pytest

from sportsassets import shadow_contract_family as cf
from sportsassets import shadow_identity as ident
from sportsassets import shadow_identity_resolver as resolver

# ── the production spread, verbatim ──────────────────────────────────

SPREAD_SLUG = "asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5"

SPREAD = {
    "symbol": SPREAD_SLUG,
    "productId": "asc-cfb-nill-arz-2026-09-19-1h",
    "priceScale": "100",
    "fractionalQtyScale": "100",
    "state": "OPEN",
    "eventAttributes": {
        "eventId": "asc-cfb-nill-arz-2026-09-19-1h",
        "question": "Will the Northern Illinois cover 13.5 vs the Arizona "
                    "in Northern Illinois vs. Arizona?",
        "strikeUnit": "decimal",
        "payoutValue": "100",
        "strikeValue": "13.5",
        "eventOutcome": "EVENT_OUTCOME_DIRECTIONAL",
        "evaluationType": ">",
        "eventDisplayName": "1st Half Spread: Northern Illinois vs. Arizona",
        "calculationMethod": "CALCULATION_METHOD_VALUE",
    },
    "metadata": {
        "event_id": "cfb-nill-arz-2026-09-19",
        "prop_type": "team",
        "product_id": "asc-cfb-nill-arz-2026-09-19-1h",
        "event_product_id": "asc-cfb-nill-arz-2026-09-19-1h",
        "market_title": "Northern Illinois +13.5 (first half)",
        "outcome_type": "spreads",
        "event_category": "SPR",
        "outcome_strike": "13.5",
        "market_sport_type": "football_team_first_half_spread",
        "cftc_instrument_id": SPREAD_SLUG,
        "instrument_product": "asc",
        "long_participant_id": "cfb-nill",
        "short_participant_id": "cfb-arz",
        "long_participant_name": "Northern Illinois",
        "short_participant_name": "Arizona",
        # THE TWO SENTENCES THAT CONTRADICT EACH OTHER, verbatim.
        "instrument_rules":
            "This market will settle to Yes if Northern Illinois, after "
            "applying a +13.5 point spread, outscores Arizona in the first "
            "half of the Northern Illinois vs Arizona College Football "
            "game scheduled for Sep 19, 2026.",
        "instrument_rules_display":
            "This market will settle to Yes if Arizona outscores Northern "
            "Illinois by more than 13.5 points in the first half of the "
            "Arizona vs Northern Illinois College Football game scheduled "
            "for Sep 19, 2026.",
    },
}

SPREAD_RETAIL = {"market_slug": SPREAD_SLUG, "identifier": "0xSPREADYES",
                 "event_slug": "cfb-nill-arz-2026-09-19", "side_norm": "yes",
                 "kind": "side", "line": 13.5}

# The NEGATIVE-strike production row, where the retail line is the
# magnitude and the slug carries the sign.
NEG_SLUG = "asc-cfb-byu-colst-2026-09-19-1h-neg-8pt5"
NEG = copy.deepcopy(SPREAD)
NEG["symbol"] = NEG_SLUG
NEG["eventAttributes"].update({
    "eventId": "asc-cfb-byu-colst-2026-09-19-1h", "strikeValue": "8.5"})
NEG["metadata"].update({
    "event_id": "cfb-byu-colst-2026-09-19", "outcome_strike": "-8.5",
    "cftc_instrument_id": NEG_SLUG,
    "long_participant_id": "cfb-byu", "short_participant_id": "cfb-colst",
    "long_participant_name": "BYU", "short_participant_name": "Colorado State",
    "instrument_rules_display":
        "This market will settle to Yes if BYU, after applying a -8.5 "
        "point spread, outscores Colorado State in the first half.",
})
NEG_RETAIL = {"market_slug": NEG_SLUG, "identifier": "0xNEGYES",
              "event_slug": "cfb-byu-colst-2026-09-19", "side_norm": "yes",
              "kind": "side", "line": 8.5}

# ── the MLS three-outcome set, the case that must never pass ─────────

MLS_SLUG = "astatc-mls-sje-laf-2026-09-19-sh-ftts-laf"
MLS = {
    "symbol": MLS_SLUG,
    "priceScale": "100",
    "fractionalQtyScale": "1",
    "eventAttributes": {
        "eventId": "astatc-mls-sje-laf-2026-09-19-sh-ftts",
        "eventOutcome": "MUTUALLY_EXCLUSIVE",
        "payoutValue": "1.00",
        "question": "Which team scores first?",
    },
    "metadata": {
        "event_id": "mls-sje-laf-2026-09-19",
        "outcome_strike": "laf",
        "cftc_instrument_id": MLS_SLUG,
        "market_sport_type": "soccer_first_team_to_score",
        "instrument_rules":
            "This market will settle to Yes if LAFC scores the first goal.",
    },
}
MLS_RETAIL = {"market_slug": MLS_SLUG, "identifier": "0xMLSYES",
              "event_slug": "mls-sje-laf-2026-09-19", "side_norm": "yes",
              "kind": "atc", "line": None}


def resolve_yes(record, retail):
    return resolver.resolve(retail["market_slug"], "yes",
                            instrument_record=record, retail_row=retail)


# ── the venue names the family, and we read it ───────────────────────


def test_the_venue_declares_the_family_and_both_are_recognised():
    assert cf.family_of(SPREAD)["family"] == cf.BINARY_PROPOSITION
    assert cf.family_of(MLS)["family"] == cf.MULTI_OUTCOME_SET


def test_an_unrecognised_family_is_refused_not_assumed_binary():
    """A family we have not seen is the dangerous case: guessing it is
    binary is exactly how a multi-outcome set gets priced as one."""
    odd = copy.deepcopy(SPREAD)
    odd["eventAttributes"]["eventOutcome"] = "EVENT_OUTCOME_SOMETHING_NEW"
    fam = cf.family_of(odd)
    assert fam["family"] == cf.FAMILY_UNKNOWN
    assert "refused rather than assumed binary" in fam["why"]

    row = resolve_yes(odd, SPREAD_RETAIL)
    assert row["execution_eligible"] is False
    assert row["contract_family"] == cf.FAMILY_UNKNOWN


# ── the production blocker, now resolved ─────────────────────────────


def test_the_production_spread_now_binds_one_to_one():
    row = resolve_yes(SPREAD, SPREAD_RETAIL)
    assert row["identity_status"] == ident.EXACT_ONE_TO_ONE
    assert row["execution_eligible"] is True
    assert row["contract_family"] == cf.BINARY_PROPOSITION
    assert row["event_outcome"] == "EVENT_OUTCOME_DIRECTIONAL"
    assert row["price_scale"] == 100 and row["quantity_scale"] == 100
    assert "registered contract" in row["settlement_equivalence"]


def test_the_negative_strike_row_binds_too():
    """The retail line is the MAGNITUDE and the slug carries the sign;
    the institutional outcome_strike carries both. A naive numeric
    compare called these disagreements -- they are not."""
    row = resolve_yes(NEG, NEG_RETAIL)
    assert row["identity_status"] == ident.EXACT_ONE_TO_ONE
    assert row["execution_eligible"] is True


def test_the_old_refusal_message_is_gone_for_directional_contracts():
    row = resolve_yes(SPREAD, SPREAD_RETAIL)
    assert not any("does not name outcome" in w for w in row["why"])


# ── THE MUTATIONS. Each breaks ONE venue field. ──────────────────────


def mutate(record, path, value):
    """Change one field, leaving everything else exactly as it was."""
    out = copy.deepcopy(record)
    node = out
    for key in path[:-1]:
        node = node[key]
    if value is None:
        node.pop(path[-1], None)
    else:
        node[path[-1]] = value
    return out


MUTATIONS = [
    # The registered contract id is the identity claim. Break it and
    # there is no registered contract binding the two venues.
    (("metadata", "cftc_instrument_id"), None, "cftc_instrument_id absent"),
    (("metadata", "cftc_instrument_id"), "asc-cfb-other-game-2026-09-19-pos-13pt5",
     "cftc_instrument_id names a different contract"),
    # The key must COMPOSE from separately published parts.
    (("eventAttributes", "eventId"), "asc-cfb-nill-arz-2026-09-19-2h",
     "the eventId is a different period"),
    (("eventAttributes", "strikeValue"), "14.5",
     "the strike is a different line"),
    # Both venues must name the SAME SIGNED strike.
    (("metadata", "outcome_strike"), "-13.5",
     "the institutional strike is the opposite side"),
    (("metadata", "outcome_strike"), "7.5",
     "the institutional strike is another line"),
    # The participants must belong to this event.
    (("metadata", "long_participant_id"), "cfb-alabama",
     "the long participant is not in this fixture"),
    (("metadata", "short_participant_id"), None,
     "the short participant is unnamed"),
    # It must settle as a binary Yes.
    (("metadata", "instrument_rules"), "Settlement to be determined.",
     "no binary Yes condition is stated"),
    (("metadata", "instrument_rules"), None, "no settlement rule at all"),
    # It must be priceable.
    (("priceScale",), None, "priceScale absent"),
    (("fractionalQtyScale",), None, "fractionalQtyScale absent"),
    (("eventAttributes", "payoutValue"), None, "payoutValue absent"),
]


@pytest.mark.parametrize("path,value,label", MUTATIONS,
                         ids=[m[2] for m in MUTATIONS])
def test_one_broken_venue_field_refuses_the_binding(path, value, label):
    row = resolve_yes(mutate(SPREAD, path, value), SPREAD_RETAIL)
    assert row["execution_eligible"] is False, label
    assert row["identity_status"] != ident.EXACT_ONE_TO_ONE, label
    assert row["why"], "a refusal with no reason is not a finding"


def test_a_retail_line_from_another_market_refuses():
    row = resolve_yes(SPREAD, dict(SPREAD_RETAIL, line=10.5))
    assert row["execution_eligible"] is False
    assert any("different lines" in w for w in row["why"])


def test_a_retail_slug_from_another_market_refuses():
    other = dict(SPREAD_RETAIL,
                 market_slug="asc-cfb-nill-arz-2026-09-19-1h-neg-13pt5")
    row = resolver.resolve(other["market_slug"], "yes",
                           instrument_record=SPREAD, retail_row=other)
    assert row["execution_eligible"] is False


# ── THE MLS CASE, which must never pass through the binary rule ──────


def test_the_mls_three_outcome_set_does_not_reach_the_binary_rule():
    proof = cf.binary_identity(MLS, MLS_RETAIL)
    assert proof["proven"] is False
    assert proof["family"] == cf.MULTI_OUTCOME_SET
    assert "binary-proposition rule does not apply" in " ".join(proof["why"])


def test_the_mls_yes_leg_keeps_its_own_stricter_verdict():
    """The old rule is not weakened for the family it was built for:
    on {LAF, SJE, NEITHER} the retail leg must name ONE outcome."""
    row = resolve_yes(MLS, MLS_RETAIL)
    assert row["contract_family"] == cf.MULTI_OUTCOME_SET
    assert row["identity_status"] in (ident.EXACT_ONE_TO_ONE,
                                      ident.AMBIGUOUS)
    # Whatever it decides, it decided it through the MULTI-OUTCOME path.
    assert "registered contract" not in (
        row.get("settlement_equivalence") or "")


def test_an_mls_instrument_relabelled_directional_still_cannot_compose():
    """THE MUTATION THE DIRECTIVE ASKED FOR. Even if the venue's family
    marker were flipped -- by a feed change, a bug or a bad assumption
    -- a three-outcome instrument still fails the binary proof, because
    its key does not compose from a direction and a numeric strike."""
    faked = mutate(MLS, ("eventAttributes", "eventOutcome"),
                   "EVENT_OUTCOME_DIRECTIONAL")
    proof = cf.binary_identity(faked, MLS_RETAIL)
    assert proof["proven"] is False
    row = resolve_yes(faked, MLS_RETAIL)
    assert row["execution_eligible"] is False, (
        "a mutually exclusive outcome must not become executable merely "
        "because something called it directional")


# ── §4: the NO leg stays honest ──────────────────────────────────────


def test_the_no_leg_is_pending_and_names_the_instrument_to_confirm():
    row = resolver.resolve(SPREAD_SLUG, "no", instrument_record=SPREAD,
                           retail_row=dict(SPREAD_RETAIL, side_norm="no"))
    assert row["execution_eligible"] is False
    assert row["identity_status"] == ident.STRUCTURAL_COMPLEMENT_PENDING
    assert row["complement_instrument_id"] == \
        "asc-cfb-nill-arz-2026-09-19-1h-neg-13pt5"
    assert any("has not been asked to confirm" in w for w in row["why"])


def test_the_no_leg_never_becomes_no_trade():
    """§4: "Do not convert it into NO_TRADE." The verdict is a blocked
    identity, which is a different fact from a model declining."""
    row = resolver.resolve(SPREAD_SLUG, "no", instrument_record=SPREAD,
                           retail_row=dict(SPREAD_RETAIL, side_norm="no"))
    assert "NO_TRADE" not in row["identity_status"]


def test_the_yes_side_is_unaffected_by_the_pending_no_side():
    """§3: "Do not let unresolved BUY_NO baskets delay BUY_YES."""
    yes = resolve_yes(SPREAD, SPREAD_RETAIL)
    no = resolver.resolve(SPREAD_SLUG, "no", instrument_record=SPREAD,
                          retail_row=dict(SPREAD_RETAIL, side_norm="no"))
    assert yes["execution_eligible"] is True
    assert no["execution_eligible"] is False
    assert yes["identity_binding_sha"] != no["identity_binding_sha"]


# ── the venue's contradictory prose, detected and not acted on ───────


def test_the_two_settlement_sentences_are_detected_as_contradictory():
    conflict = cf.settlement_prose_conflict(cf.proposition(SPREAD))
    assert conflict, (
        "instrument_rules says Northern Illinois +13.5 and "
        "instrument_rules_display says Arizona -13.5; with a .5 line "
        "those are exact complements and one is wrong")
    assert "registered contract id" in conflict


def test_the_conflict_is_recorded_on_the_row_but_does_not_block_it():
    """Identity here is the registered contract id, so a disagreement
    between two prose fields cannot flip a side. It is written down
    because the day something else reads that field, this row already
    says it was wrong."""
    row = resolve_yes(SPREAD, SPREAD_RETAIL)
    assert row["settlement_prose_conflict"]
    assert row["execution_eligible"] is True


def test_a_consistent_pair_of_sentences_raises_no_conflict():
    consistent = mutate(
        SPREAD, ("metadata", "instrument_rules_display"),
        "This market will settle to Yes if Northern Illinois, after "
        "applying a +13.5 point spread, outscores Arizona in the first "
        "half.")
    assert cf.settlement_prose_conflict(
        cf.proposition(consistent)) is None


# ── no prices, no titles, no fuzzy matching ──────────────────────────


def code_only(module) -> str:
    import inspect
    import io
    import tokenize
    kept = []
    for tok in tokenize.generate_tokens(
            io.StringIO(inspect.getsource(module)).readline):
        if tok.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
            kept.append(tok.string)
    return " ".join(kept).lower()


def test_the_family_module_reads_no_price_and_no_title():
    src = code_only(cf).split()
    for token in ("bid", "offer", "midpoint", "px", "vwap", "mid",
                  "market_title", "question", "difflib", "fuzz",
                  "levenshtein"):
        assert token not in src, token


def test_the_strike_token_spelling_round_trips_the_venue_key():
    assert cf.strike_token("13.5") == "13pt5"
    assert cf.strike_token("-8.5") == "8pt5"
    assert cf.strike_token("2") == "2"
    assert cf.strike_token("nonsense") is None
