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
