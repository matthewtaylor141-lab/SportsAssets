"""THE TRANSPORT BOUNDARY, TESTED BY TRYING TO CROSS IT.

Owner 2026-09-20 §3: "Add a small, explicitly synthetic test that
deliberately reaches an otherwise eligible order intent and verifies
that the transport boundary prevents submission. Exercise each
supported submission route and report the interception evidence. Do
not disable the boundary to test it."

EVERYTHING IN THIS FILE IS SYNTHETIC AND SAYS SO.

The real-input rehearsal proves the chain REFUSES on real evidence: all
ten inputs end WOULD_NOT_SUBMIT because P_FILL, fair value and adverse
selection are unidentified. That is a test of the DECISION layer, and
it leaves one question open -- if a decision ever did come out
positive, would the transport boundary hold?

That question cannot be answered with real inputs without inventing the
missing estimates, which is forbidden and would also be worthless. So
it is answered with an intent constructed HERE, labelled SYNTHETIC,
carrying no claim about EV, fill probability or profitability. These
results must never be pooled with empirical evidence; the cohort field
and this docstring exist to keep that separation legible.

THE BOUNDARY IS NOT DISABLED TO TEST IT. Each route is called through
the armed context exactly as production code would call it, and the
test asserts the call RAISES and that nothing reached the venue's HTTP
layer. A test that stubbed out the boundary would prove only that a
stub does nothing.
"""

import pytest

from sportsassets import bettor_rehearsal as reh

SYNTHETIC = reh.COHORT_SYNTHETIC

# An intent that would be eligible IF the missing estimates existed.
# They do not, and this object does not pretend they do -- it carries
# no EV, no P_FILL and no profitability claim. It exists only to give
# the transport layer something to refuse.
SYNTHETIC_INTENT = {
    "COHORT": SYNTHETIC,
    "us_market_slug": "synthetic-market-that-does-not-exist",
    "limit_price": 0.50,
    "quantity": 1,
    "side": "BUY",
    "carriesNoEvidence": (
        "constructed in a test to exercise the boundary. It asserts "
        "nothing about EV, fill probability or profitability, and may "
        "not be cited as evidence of any of them"),
}


def _routes():
    """Every submission route the boundary claims to cover."""
    return list(reh.TRANSPORT_ENTRY_POINTS)


def test_the_boundary_covers_every_supported_submission_route():
    """If a route exists that the boundary does not arm, the boundary
    is decorative."""
    import importlib
    covered = {(m, a) for m, a in _routes()}
    found = set()
    for mod_name in ("sportsassets.pmus", "sportsassets.pmx"):
        mod = importlib.import_module(mod_name)
        for attr in dir(mod):
            if attr in ("submit_fok", "cancel_order", "place_order",
                        "create_order", "replace_order"):
                found.add((mod_name, attr))
    missing = found - covered
    assert not missing, "unarmed submission routes: %s" % sorted(missing)


@pytest.mark.parametrize("mod_name,attr", _routes())
def test_each_route_is_intercepted_and_raises(mod_name, attr):
    """SYNTHETIC. Calls the real route through the armed boundary and
    asserts it raises rather than returning something a caller could
    mistake for an accepted order."""
    import importlib
    with reh.TransportDisabled() as trip:
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, attr)
        with pytest.raises(reh.SubmissionAttempted) as excinfo:
            if attr == "submit_fok":
                fn(SYNTHETIC_INTENT["us_market_slug"],
                   SYNTHETIC_INTENT["limit_price"],
                   SYNTHETIC_INTENT["quantity"])
            else:
                fn("synthetic-order-id",
                   SYNTHETIC_INTENT["us_market_slug"])
        # INTERCEPTION EVIDENCE: the route that fired, by name.
        assert "%s.%s" % (mod_name, attr) in str(excinfo.value)
        assert trip.attempts == ["%s.%s" % (mod_name, attr)]


def test_interception_leaves_the_real_function_restored():
    """A boundary that leaked its stub would silently disable the venue
    calls for the rest of the process."""
    import importlib
    mod = importlib.import_module("sportsassets.pmus")
    before = mod.submit_fok
    with reh.TransportDisabled():
        assert mod.submit_fok is not before
    assert mod.submit_fok is before


def test_the_boundary_restores_even_when_the_body_raises():
    import importlib
    mod = importlib.import_module("sportsassets.pmus")
    before = mod.submit_fok
    with pytest.raises(ValueError):
        with reh.TransportDisabled():
            raise ValueError("body blew up")
    assert mod.submit_fok is before


def test_a_synthetic_interception_carries_no_economic_claim():
    """The separation the owner asked for, asserted rather than
    promised: nothing in this file may be read as EV, fill or
    profitability evidence."""
    assert SYNTHETIC_INTENT["COHORT"] == reh.COHORT_SYNTHETIC
    assert reh.COHORT_SYNTHETIC != reh.COHORT_REAL
    assert "may not be cited as evidence" in \
        SYNTHETIC_INTENT["carriesNoEvidence"]


def test_the_boundary_is_not_disabled_by_this_test_file():
    """Guards the test itself. If a future edit stubs the trip out, the
    file would pass while proving nothing."""
    import inspect
    import io
    import tokenize
    assert "raise SubmissionAttempted" in \
        inspect.getsource(reh.TransportDisabled)

    # CODE ONLY. Scanning raw text would match this test's own
    # forbidden-pattern literals -- the same self-match that broke two
    # earlier guards in this repo, once on a docstring disclaiming
    # credentials and once on the sentence "reads no book".
    src_here = inspect.getsource(
        inspect.getmodule(test_each_route_is_intercepted_and_raises))
    code = []
    for tok in tokenize.generate_tokens(io.StringIO(src_here).readline):
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            code.append(tok.string)
    code = " ".join(code)
    # This file must not rebind the boundary's own machinery.
    for forbidden in ("monkeypatch", "setattr"):
        assert forbidden not in code, forbidden
    assert "TransportDisabled" in code


def test_the_real_input_rehearsal_reached_no_route():
    """The empirical half, restated here so the two live side by side
    without being merged: ten real inputs, zero transport attempts."""
    import json
    import pathlib
    p = (pathlib.Path(__file__).resolve().parents[2] / "research"
         / "beta48" / "rehearsal" / "inputs_20260920T2026Z.jsonl")
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    out = reh.run(rows, cohort=reh.COHORT_REAL)
    assert out["COHORT"] == reh.COHORT_REAL
    assert out["EVALUATIONS"] >= 10
    assert out["SUBMISSION_REACHED_THE_WIRE"] is False
    assert out["TRANSPORT_ATTEMPTS"] == []
    assert out["WOULD_SUBMIT_COUNT"] == 0
    assert out["FABRICATION_CHECK"] == "CLEAN"
