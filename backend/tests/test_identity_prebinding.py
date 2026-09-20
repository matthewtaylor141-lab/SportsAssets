"""THE IDENTITY BINDING, RESOLVED BEFORE THE SIGNAL.

Owner directive 2026-09-20 (the identity blocker). 13 prospective BUY
decisions carried IDENTITY_BINDING_STATUS = NOT_IDENTIFIED, so
EXECUTION_STATUS = BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE and
POSITIONS = 0.

THE DEFECT THESE TESTS PIN. `instrument_records` read the institutional
record out of `bettor_l2_evidence.instrument_record` -- a column only
the GitHub bridge ever wrote -- so on the direct path the lookup
returned nothing for every market and `bind_yes` short-circuited. The
first test below reproduces that exact shape, and the rest prove the
pre-bound path resolves it without loosening a single verdict.

WHAT IS DELIBERATELY NOT TESTED AS PASSING: nothing here makes an
AMBIGUOUS market eligible. The gate in `shadow_identity` is unchanged;
these tests run it EARLIER, not looser.
"""

from __future__ import annotations

import pytest

from sportsassets import shadow_identity as ident
from sportsassets import shadow_identity_resolver as resolver
from sportsassets.workers import shadow_experimental as worker

def code_only(module) -> str:
    """The module's CODE, with every comment, docstring and string gone.

    WHY THE SCANS BELOW NEED THIS. A module that explains in prose why
    it never matches on a price contains the word "price" in that
    explanation. Scanning the raw source would then fail on the
    sentence promising the behaviour, which is the opposite of a
    useful check: the claim being tested is about what the code READS,
    not about what the comments SAY.
    """
    import inspect
    import io
    import tokenize

    kept = []
    for tok in tokenize.generate_tokens(
            io.StringIO(inspect.getsource(module)).readline):
        if tok.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER):
            kept.append(tok.string)
    return " ".join(kept).lower()


# A production-shaped pair. The retail slug and the institutional
# symbol are the same venue key, and the slug's TERMINAL TOKEN is the
# institutional outcome_strike -- which is what makes the retail market
# a binary over that one outcome rather than over the event.
SYMBOL = "astatc-nba-lal-bos-2026-09-21-sh-ftts-laf"

INSTRUMENT = {
    "symbol": SYMBOL,
    "productId": "PRD-991",
    "priceScale": "100",
    "fractionalQtyScale": "1",
    "expirationDate": "2026-09-22T03:00:00Z",
    "eventAttributes": {"eventId": "EVT-77", "eventOutcome": "MUTUALLY_EXCLUSIVE",
                        "payoutValue": "1.00",
                        "question": "First team to ten points?"},
    "metadata": {"outcome_strike": "laf", "event_id": "EVT-77",
                 "market_sport_type": "NBA",
                 "event_start_time": "2026-09-21T23:00:00Z"},
}

RETAIL_YES = {"market_slug": SYMBOL, "identifier": "0xTOKENYES",
              "event_slug": "nba-lal-bos-2026-09-21", "side_norm": "yes",
              "kind": "atc", "line": None}
RETAIL_NO = dict(RETAIL_YES, identifier="0xTOKENNO", side_norm="no")


# ── the defect, reproduced ───────────────────────────────────────────


def test_the_old_path_refuses_when_the_bridge_wrote_no_record():
    """THE PRODUCTION SHAPE. The venue had answered refdata for this
    symbol; the bridge simply had not written it into evidence, so the
    lookup handed `bind_yes` None and it refused. The message was true
    and told nobody that refdata was sitting in the worker's memory."""
    binding = worker.bind_yes(None, RETAIL_YES)
    assert binding["verdict"] == ident.NOT_IDENTIFIED
    assert not binding["executionEligible"]
    assert "no institutional instrument record" in " ".join(binding["why"])


def test_the_same_market_resolves_once_the_record_is_actually_present():
    row = resolver.resolve(SYMBOL, "yes", instrument_record=INSTRUMENT,
                           retail_row=RETAIL_YES)
    assert row["identity_status"] == ident.EXACT_ONE_TO_ONE
    assert row["execution_eligible"] is True
    assert row["institutional_instrument_id"] == SYMBOL
    assert row["price_scale"] == 100 and row["quantity_scale"] == 1
    assert row["payout_value"] == "1.00"
    assert row["retail_native_id"] == "0xTOKENYES"
    assert row["identity_binding_sha"]


def test_the_hot_path_prefers_the_prebound_row_over_the_bridge_lookup():
    """§2: "The experimental hot path should already know whether the
    market is execution eligible." With the pre-bound row present the
    absent bridge record no longer decides anything."""
    row = resolver.resolve(SYMBOL, "yes", instrument_record=INSTRUMENT,
                           retail_row=RETAIL_YES)
    binding = worker.binding_for(
        SYMBOL, "yes", bound={(SYMBOL, "yes"): row},
        instruments={},          # the bridge wrote nothing, as in production
        retail={})
    assert binding["verdict"] == ident.EXACT_ONE_TO_ONE
    assert binding["executionEligible"] is True
    assert binding["preBound"] is True
    assert binding["identityBindingSha"] == row["identity_binding_sha"]


def test_without_a_prebound_row_the_hot_path_still_refuses():
    """The fallback is a fallback, not a second chance at eligibility."""
    binding = worker.binding_for(SYMBOL, "yes", bound={}, instruments={},
                                 retail={})
    assert binding["verdict"] == ident.NOT_IDENTIFIED
    assert not binding["executionEligible"]


# ── the gate is EARLIER, not LOOSER ──────────────────────────────────


def test_settlement_equivalence_is_stated_only_when_established():
    """A sentence on an AMBIGUOUS binding would read like a proof."""
    good = resolver.resolve(SYMBOL, "yes", instrument_record=INSTRUMENT,
                            retail_row=RETAIL_YES)
    assert "settles to 1 exactly when" in good["settlement_equivalence"]

    # A slug whose terminal token is NOT the institutional outcome: the
    # two venues are not keying on the same thing.
    other = dict(RETAIL_YES, market_slug=SYMBOL.replace("-laf", "-sje"))
    row = resolver.resolve(other["market_slug"], "yes",
                           instrument_record=INSTRUMENT, retail_row=other)
    assert row["identity_status"] != ident.EXACT_ONE_TO_ONE
    assert row["settlement_equivalence"] is None
    assert row["execution_eligible"] is False


def test_an_exact_binding_without_scales_is_not_eligible():
    """An exact contract we cannot price is not an executable one, and
    the row must not claim it is -- the database CHECK agrees."""
    no_scale = dict(INSTRUMENT)
    no_scale.pop("priceScale")
    row = resolver.resolve(SYMBOL, "yes", instrument_record=no_scale,
                           retail_row=RETAIL_YES)
    assert row["execution_eligible"] is False
    assert row["price_scale"] is None


def test_a_scale_is_never_defaulted_to_one_hundred():
    assert resolver._int_or_none(None) is None
    assert resolver._int_or_none("0") is None
    assert resolver._int_or_none("nonsense") is None
    assert resolver._int_or_none("1000") == 1000


def test_no_price_reaches_the_resolver_at_all():
    """§1: "No price matching. No choosing an instrument because its
    current price looks similar." Checked against the CODE, not against
    a promise: this module cannot match on a price it never reads."""
    src = code_only(resolver)
    for token in ("bid", "offer", "midpoint", "px", "book_sha", "vwap"):
        assert token not in src.split(), token


def test_no_fuzzy_matching_reaches_the_resolver_either():
    """No fuzzy title matching and no approximate team-name matching --
    checked by the absence of anything that could do it."""
    src = code_only(resolver)
    for token in ("difflib", "fuzz", "levenshtein", "similarity",
                  "startswith", "endswith", "in_"):
        assert token not in src.split(), token


# ── §3: YES first, and NO stays honest ───────────────────────────────


def test_the_no_leg_is_recorded_honestly_and_gates_nothing()  :
    """§4: "Do not convert it into NO_TRADE. Do not manufacture a NO
    book as 1-YES." The NO leg gets its own row with its own refusal,
    and the YES row beside it is still eligible."""
    yes = resolver.resolve(SYMBOL, "yes", instrument_record=INSTRUMENT,
                           retail_row=RETAIL_YES)
    no = resolver.resolve(SYMBOL, "no", instrument_record=INSTRUMENT,
                          retail_row=RETAIL_NO)

    assert yes["execution_eligible"] is True
    assert no["execution_eligible"] is False
    assert no["identity_status"] in (ident.AMBIGUOUS,
                                     ident.DIFFERENT_CONTRACT,
                                     ident.NOT_IDENTIFIED)
    assert no["why"], "a refusal with no reason is not a finding"
    # The two legs are separate rows with separate shas, so one can
    # never be read as the other.
    assert yes["identity_binding_sha"] != no["identity_binding_sha"]


def test_a_market_the_venue_does_not_list_is_a_named_answer():
    row = resolver.resolve("never-listed", "yes", instrument_record=None,
                           retail_row=RETAIL_YES)
    assert row["identity_status"] == ident.NOT_IDENTIFIED
    assert row["execution_eligible"] is False
    assert "returned no instrument" in " ".join(row["why"])
    assert row["identity_binding_sha"], "even a refusal is identifiable"


def test_a_market_with_no_retail_row_says_which_side_is_missing():
    row = resolver.resolve(SYMBOL, "yes", instrument_record=INSTRUMENT,
                           retail_row=None)
    assert row["identity_status"] == ident.NOT_IDENTIFIED
    assert "no retail row is known" in " ".join(row["why"])
    # The institutional side IS recorded, because it was observed.
    assert row["institutional_instrument_id"] == SYMBOL
    assert row["price_scale"] == 100


# ── the sha is a claim, and it moves when the claim moves ────────────


def test_the_sha_changes_when_the_binding_changes():
    a = resolver.resolve(SYMBOL, "yes", instrument_record=INSTRUMENT,
                         retail_row=RETAIL_YES)
    rescaled = dict(INSTRUMENT, priceScale="1000")
    b = resolver.resolve(SYMBOL, "yes", instrument_record=rescaled,
                         retail_row=RETAIL_YES)
    assert b["price_scale"] == 1000
    assert a["identity_binding_sha"] != b["identity_binding_sha"], (
        "a binding that prices the book differently is a different "
        "binding, and a trade must be readable against the one it used")


def test_the_same_inputs_resolve_to_the_same_sha():
    a = resolver.resolve(SYMBOL, "yes", instrument_record=INSTRUMENT,
                         retail_row=RETAIL_YES)
    b = resolver.resolve(SYMBOL, "yes", instrument_record=INSTRUMENT,
                         retail_row=RETAIL_YES)
    assert a["identity_binding_sha"] == b["identity_binding_sha"]


# ── §6's census ──────────────────────────────────────────────────────


def test_the_census_counts_what_section_six_asks_for():
    rows = [
        resolver.resolve(SYMBOL, "yes", instrument_record=INSTRUMENT,
                         retail_row=RETAIL_YES),
        resolver.resolve(SYMBOL, "no", instrument_record=INSTRUMENT,
                         retail_row=RETAIL_NO),
        resolver.resolve("never-listed", "yes", instrument_record=None,
                         retail_row=RETAIL_YES),
    ]
    out = resolver.census(rows)
    assert out["FOCUS_MARKETS"] == 2
    assert out["YES_EXECUTION_ELIGIBLE"] == 1
    assert out["NO_EXECUTION_ELIGIBLE"] == 0
    assert out["UNRESOLVED"] >= 1
    assert set(out) >= {"FOCUS_MARKETS", "IDENTITY_RESOLVED",
                        "YES_EXECUTION_ELIGIBLE", "NO_EXECUTION_ELIGIBLE",
                        "AMBIGUOUS", "UNRESOLVED"}


def test_eligibility_cannot_depend_on_a_model_outcome():
    """§6: "Identity eligibility must be independent of model outcome."

    The resolver has no name for a signal, a P&L or an experiment
    anywhere in its code, so eligibility could not be conditioned on
    one even by mistake."""
    src = code_only(resolver).split()
    for token in ("signal", "pnl", "profit", "experiment", "x1", "action",
                  "edge", "roi"):
        assert token not in src, token


# ── the schema and the module agree on the vocabulary ────────────────


def test_every_verdict_the_resolver_can_reach_is_in_the_check():
    from pathlib import Path
    import re

    sql = (Path(__file__).resolve().parents[1] / "migrations"
           / "082_bettor_identity_bindings.sql").read_text()
    block = sql.split("bettor_identity_status_known")[1].split(")")[0]
    allowed = set(re.findall(r"'([A-Z_]+)'", block))
    reachable = set(ident.VERDICTS) | {ident.STRUCTURAL_COMPLEMENT_PENDING}
    assert reachable == allowed, reachable ^ allowed
