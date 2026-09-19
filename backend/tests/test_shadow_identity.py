"""THE IDENTITY GATE, against the record production actually returned.

Owner directive 2026-09-19 22:0xZ §2/§4. The institutional fixture is
the VERBATIM instrument record from production run 35472190984
(2026-09-19 22:03:10Z, evidenceClass OBSERVED_PRODUCTION).

THE FAILURE THIS PREVENTS. An experimental trade's P&L is reconstructed
against the institutional book. If that instrument is not the same
economic contract the decision was made about, every dollar is measured
against the wrong market -- and nothing looks wrong, because the slug
matched. Slug equality is exactly the evidence the directive says is
insufficient, so the gate must refuse a slug-only match.
"""

from __future__ import annotations

import pytest

from sportsassets import shadow_identity as ident

# ── the verbatim production instrument ───────────────────────────────

INSTRUMENT = {
    "symbol": "astatc-mls-sje-laf-2026-09-19-sh-ftts-laf",
    "productId": "astatc-mls-sje-laf-2026-09-19-sh-ftts",
    "priceScale": "100",
    "fractionalQtyScale": "100",
    "state": "INSTRUMENT_STATE_OPEN",
    "expirationDate": "2026-09-19",
    "metadata": {
        "event_id": "mls-sje-laf-2026-09-19",
        "outcome_strike": "laf",
        "market_sport_type": "soccer_game_second_half_first_team_to_score",
        "event_start_time": "2026-09-19 23:30:00+00",
        "long_participant_name": "Los Angeles FC",
        "instrument_rules": (
            "This market will settle to the team that scores the first "
            "goal in the second half of regulation in the San Jose "
            "Earthquakes vs Los Angeles FC MLS match ..."),
    },
    "eventAttributes": {
        "eventId": "astatc-mls-sje-laf-2026-09-19-sh-ftts",
        "payoutValue": "100",
        "eventOutcome": "EVENT_OUTCOME_MUTUALLY_EXCLUSIVE",
        "question": ("Will Los Angeles FC be the first to score a goal in "
                     "the second half on 2026-09-19 7:30PM ET?"),
    },
}

# our collector's row for the same slug
RETAIL_YES = {
    "market_slug": "astatc-mls-sje-laf-2026-09-19-sh-ftts-laf",
    "outcome_leg": "yes",
    "identifier": "0xRETAILTOKEN",
    "event_slug": "mls-sje-laf-2026-09-19",
}


def inst():
    return ident.institutional_identity(INSTRUMENT)


# ── §1: the units come from the venue, and they are these ────────────


def test_the_scales_are_read_from_the_venues_own_record():
    """The $73.75 walk assumed 100. The venue says 100 -- but the
    assumption was only right by luck, and this is what replaces it."""
    i = inst()
    assert i["priceScale"] == "100"
    assert i["qtyScale"] == "100"
    assert i["payoutValue"] == "100"


def test_the_venue_native_identity_fields_are_all_present():
    i = inst()
    for field in ("symbol", "productId", "eventId", "eventMetadataId",
                  "outcomeStrike", "eventOutcome", "marketSportType",
                  "settlementRule", "eventStartTime"):
        assert i[field], field


# ── §2: the structural finding ───────────────────────────────────────


def test_a_mutually_exclusive_outcome_is_not_a_binary_leg():
    """THE FINDING. The institutional instrument `-laf` IS the "LAF
    scores first in the second half" outcome, one of a MUTUALLY
    EXCLUSIVE set. Our collector records the same slug with an
    outcome_leg of `yes` -- it models a YES/NO binary. Those can
    coincide, but nothing either venue returned proves they do."""
    out = ident.classify(inst(), ident.retail_identity(RETAIL_YES))
    assert out["verdict"] == ident.AMBIGUOUS
    assert out["executionEligible"] is False
    assert any("MUTUALLY EXCLUSIVE" in w for w in out["why"])


def test_the_same_reading_holds_for_the_no_leg():
    row = dict(RETAIL_YES, outcome_leg="no")
    out = ident.classify(inst(), ident.retail_identity(row))
    assert out["verdict"] == ident.AMBIGUOUS


def test_a_retail_leg_that_names_the_outcome_binds_exactly():
    """The shape that WOULD pass: the retail leg names the
    institutional outcome rather than a yes/no side."""
    row = dict(RETAIL_YES, outcome_leg="laf")
    out = ident.classify(inst(), ident.retail_identity(row))
    assert out["verdict"] == ident.EXACT_SAME_CONTRACT
    assert out["executionEligible"] is True
    ident.assert_execution_eligible(out)


def test_a_different_slug_is_a_different_contract():
    row = dict(RETAIL_YES, outcome_leg="laf",
               market_slug="astatc-mls-sje-laf-2026-09-19-sh-ftts-sje")
    out = ident.classify(inst(), ident.retail_identity(row))
    assert out["verdict"] == ident.DIFFERENT_CONTRACT


def test_slug_equality_alone_never_reaches_exact():
    """"Slug equality alone is insufficient." A row whose slug matches
    but which names no leg must not bind."""
    row = {"market_slug": INSTRUMENT["symbol"]}
    out = ident.classify(inst(), ident.retail_identity(row))
    assert out["verdict"] != ident.EXACT_SAME_CONTRACT


def test_a_missing_scale_blocks_the_binding():
    """§1: "If either price or quantity scale remains unidentified ...
    No dollar P&L." """
    for drop in ("priceScale", "fractionalQtyScale"):
        record = {k: v for k, v in INSTRUMENT.items() if k != drop}
        out = ident.classify(
            ident.institutional_identity(record),
            ident.retail_identity(dict(RETAIL_YES, outcome_leg="laf")))
        assert out["executionEligible"] is False


# ── §4: only an exact binding may feed execution ─────────────────────


def test_the_gate_fails_closed_on_every_non_exact_verdict():
    """A binding that fails open is not a binding."""
    for verdict in (ident.AMBIGUOUS, ident.DIFFERENT_CONTRACT,
                    ident.NOT_IDENTIFIED):
        with pytest.raises(ident.IdentityRefusal):
            ident.assert_execution_eligible(
                {"verdict": verdict, "executionEligible": False})
    with pytest.raises(ident.IdentityRefusal):
        ident.assert_execution_eligible(None)


def test_an_absent_side_is_not_identified_rather_than_same():
    assert ident.classify({}, {})["verdict"] == ident.NOT_IDENTIFIED
    assert ident.classify(inst(), {})["verdict"] == ident.NOT_IDENTIFIED


# ── the binding sha travels and is sensitive ─────────────────────────


def test_the_binding_sha_changes_when_either_identity_changes():
    """It travels on every experimental decision, so a re-derived
    binding must be visibly different rather than silently adopted."""
    base = ident.classify(inst(), ident.retail_identity(RETAIL_YES))
    other = ident.classify(
        inst(), ident.retail_identity(dict(RETAIL_YES, outcome_leg="laf")))
    assert base["identityBindingSha"] != other["identityBindingSha"]
    assert len(base["identityBindingSha"]) == 16


def test_the_binding_sha_is_stable_for_the_same_pair():
    a = ident.classify(inst(), ident.retail_identity(RETAIL_YES))
    b = ident.classify(inst(), ident.retail_identity(RETAIL_YES))
    assert a["identityBindingSha"] == b["identityBindingSha"]


# ── THE RESOLVED SET (research run 35472636412, 2026-09-19 22:12:22Z) ─
#
# The retail board lists THREE identifiers under productId
# astatc-mls-sje-laf-2026-09-19-sh-ftts, each with a yes and a no side,
# and each with its own question:
#
#   ...-sh-ftts-laf   "Will Los Angeles FC be the first to score a goal
#                      in the second half on 2026-09-19 7:30PM ET?"
#   ...-sh-ftts-sje   "Will San Jose Earthquakes be the first ..."
#   ...-sh-ftts-none  "Will None be the first ..."
#
# So BOTH venues model the same three-outcome mutually exclusive set.
# Retail gives each outcome its own YES/NO binary; institutional gives
# each outcome its own instrument. That is what settles §2: the
# structures are not different, they are two encodings of one set.

RETAIL_SIBLINGS = (
    "astatc-mls-sje-laf-2026-09-19-sh-ftts-laf",
    "astatc-mls-sje-laf-2026-09-19-sh-ftts-sje",
    "astatc-mls-sje-laf-2026-09-19-sh-ftts-none",
)


def test_the_retail_board_enumerates_the_same_three_outcomes():
    """The candidate set came from the OTHER venue's own board, not
    from us guessing LAF / SJE / NEITHER."""
    assert len(RETAIL_SIBLINGS) == 3
    assert INSTRUMENT["symbol"] in RETAIL_SIBLINGS
    # every sibling sits under the institutional productId
    for s in RETAIL_SIBLINGS:
        assert s.startswith(INSTRUMENT["productId"] + "-")


def test_the_yes_side_binds_one_to_one():
    """RETAIL `-laf` YES settles to 1 exactly when LAF scores first in
    the second half -- which is what INSTITUTIONAL `-laf` settles to.
    One outcome, one instrument."""
    row = dict(RETAIL_YES, outcome_leg="laf")
    out = ident.classify(inst(), ident.retail_identity(row))
    assert out["verdict"] == ident.EXACT_ONE_TO_ONE
    assert out["executionEligible"] is True


def test_the_no_side_is_a_two_leg_complement_basket():
    """RETAIL `-laf` NO settles to 1 when LAF does NOT score first --
    which is SJE *or* NONE. Two instruments, not one. Pricing it off a
    single sibling would call the missing leg edge."""
    primary = {"symbol": RETAIL_SIBLINGS[0], "siblingSetComplete": True}
    siblings = [{"symbol": s} for s in RETAIL_SIBLINGS[1:]]
    basket = ident.complement_basket(primary, siblings, retail_leg="no")
    assert basket["verdict"] == ident.EXACT_ONE_TO_COMPLEMENT_BASKET
    assert set(basket["complementInstrumentIds"]) == {
        "astatc-mls-sje-laf-2026-09-19-sh-ftts-sje",
        "astatc-mls-sje-laf-2026-09-19-sh-ftts-none"}
    assert basket["settlementEquivalenceRule"]


def test_the_no_side_is_not_executable_until_its_basket_is_walkable():
    """§7: "Do not pretend one sibling represents NO." An exact basket
    whose books cannot be walked still leaves that side
    NOT_IDENTIFIED -- and it must not block the proven YES side."""
    primary = {"symbol": RETAIL_SIBLINGS[0], "siblingSetComplete": True}
    siblings = [{"symbol": s} for s in RETAIL_SIBLINGS[1:]]
    basket = ident.complement_basket(primary, siblings, retail_leg="no")
    with pytest.raises(ident.IdentityRefusal):
        ident.assert_execution_eligible(basket)
    ident.assert_execution_eligible(basket, basket_walkable=True)


def test_both_venues_word_the_proposition_identically():
    """SUPPORTING evidence, not the proof. The structural match --
    same productId, same event, same enumerated outcome set -- is what
    carries the binding; the wording agreeing is corroboration."""
    retail_question = ("Will Los Angeles FC be the first to score a goal "
                       "in the second half on 2026-09-19 7:30PM ET?")
    assert inst()["question"] == retail_question


# ── the YES leg, one to one with its institutional outcome ───────────


def test_the_yes_leg_of_a_slug_that_names_an_outcome_is_that_outcome():
    """The retail board lists -laf, -sje and -none SEPARATELY, each
    with a yes and a no leg (run 35472636412). So the retail market is
    a binary over ONE outcome of the same mutually exclusive set, and
    its yes leg is that outcome."""
    binding = ident.yes_leg_binding(
        inst(), ident.retail_identity(
            {"market_slug": RETAIL_SIBLINGS[0], "side_norm": "yes",
             "identifier": "0xabc"}))
    assert binding["verdict"] == ident.EXACT_ONE_TO_ONE
    assert binding["executionEligible"] is True
    assert binding["identityBindingSha"]


def test_the_no_leg_gets_no_shortcut_from_the_yes_rule():
    """It falls through to the general gate, which refuses it. The NO
    side is the complement BASKET and is established separately."""
    binding = ident.yes_leg_binding(
        inst(), ident.retail_identity(
            {"market_slug": RETAIL_SIBLINGS[0], "side_norm": "no"}))
    assert binding["verdict"] != ident.EXACT_ONE_TO_ONE
    assert binding["executionEligible"] is False


def test_a_slug_whose_terminal_token_is_not_the_outcome_is_refused():
    """SLUG EQUALITY ALONE IS INSUFFICIENT (§2). The rule needs the
    slug's own terminal token to BE the institutional outcome; a slug
    that merely matches the symbol does not reach the verdict."""
    other = dict(inst())
    other["outcomeStrike"] = "sje"
    binding = ident.yes_leg_binding(
        other, ident.retail_identity(
            {"market_slug": RETAIL_SIBLINGS[0], "side_norm": "yes"}))
    assert binding["verdict"] != ident.EXACT_ONE_TO_ONE


def test_an_exact_contract_we_cannot_price_is_not_executable():
    """A binding without the venue's scales would have to assume one,
    and an assumed scale misprices every row that does not use it."""
    unpriced = dict(inst())
    unpriced["priceScale"] = None
    binding = ident.yes_leg_binding(
        unpriced, ident.retail_identity(
            {"market_slug": RETAIL_SIBLINGS[0], "side_norm": "yes"}))
    assert binding["verdict"] == ident.AMBIGUOUS
    assert binding["executionEligible"] is False
