"""THE PROBE MUST FAIL LOUDLY, NOT AS A VENUE REFUSAL.

THE FAILURE THIS EXISTS FOR. `venue_quote` was corrected to take the
VENUE's own slug (`us_slug`) instead of the global `condition_id`. The
command API's `_venue_read_probe` was not updated with it, so it raised

    TypeError: venue_quote() got an unexpected keyword argument
               'condition_id'

on the FIRST candidate row. The outer `except Exception` swallowed it into
`probe["error"]` -- a field the production read did not print -- AFTER
`attempted` had already been incremented, and the loop never ran again. So
run 26 reported

    attempted 1   ok 0   results []
    verdict: no open market ... returned a usable book

which every reader, including me, took as "the venue refused the read". It
meant "our own probe crashed before reaching the venue". Two of the
directive's questions were answered with that sentence.

Three things are pinned here:

    1 · the probe's call to `venue_quote` BINDS against `venue_quote`'s
        real signature -- a signature change on either side fails here
        rather than in production;
    2 · a raise is attributed to the ROW, so `results` is never empty
        while `attempted` is positive;
    3 · the candidates are venue-native, because the venue accepts no
        other identifier.
"""
import inspect

import pytest

from sportsassets.api import app as API
from sportsassets.workers import ext_pinnacle_loop as EXT


def _code(fn) -> str:
    """Source with comments and the docstring removed.

    The docstring above deliberately quotes the broken call, so a naive
    substring search over the raw source would match the description of
    the bug and never the bug.
    """
    src = inspect.getsource(fn)
    for line in (fn.__doc__ or "").splitlines():
        src = src.replace(line, "")
    return "\n".join(l.split("#", 1)[0] for l in src.splitlines())


def test_the_probe_no_longer_passes_a_global_identifier():
    code = _code(API._venue_read_probe)
    assert "condition_id=" not in code, (
        "the venue endpoint does not accept the global catalogue id")
    assert 'us_slug=row["market_slug"]' in code


def test_the_probes_call_binds_against_the_real_signature():
    """THE CHECK THAT WAS MISSING. A keyword the callee does not accept is
    a TypeError at runtime and nothing at all at import time."""
    sig = inspect.signature(EXT.venue_quote)
    # exactly what the probe passes now: the slug AND the intent, because
    # the intent is what names the side on this venue.
    sig.bind(None, us_slug="aec-mlb-chc-mia-2026-09-24-cubs",
             intent="ORDER_INTENT_BUY_LONG", now=1.0)
    # the global-id shape must still be rejected
    with pytest.raises(TypeError):
        sig.bind(None, condition_id="0xabc", intent="X", now=1.0)
    # AND SO MUST THE PARAMETER THAT USED TO DO NOTHING. `outcome_index`
    # looked like it selected a side and was ignored, which is how a
    # one-sided reader hid for three runs. It must not be accepted again.
    with pytest.raises(TypeError):
        sig.bind(None, us_slug="aec-x", outcome_index=0, now=1.0)
    # the side is not optional either
    with pytest.raises(TypeError):
        sig.bind(None, us_slug="aec-x", now=1.0)


def test_a_raise_is_attributed_to_the_row_not_the_probe():
    code = _code(API._venue_read_probe)
    # the per-row try/except exists and names itself
    assert "PROBE_RAISED" in code
    assert "exception=type(exc).__name__" in code
    # and the top-level handler reports its detail rather than a bare name
    assert "error_detail" in code


def test_candidates_come_from_the_venues_own_catalogue():
    sql = API._PROBE_VENUE_SQL
    assert "us_premap" in sql, (
        "the venue accepts only its own slug, so candidates must come "
        "from its own catalogue")
    assert "aec-" in sql, "the winner family is the one this prices"
    assert "-draw" in sql, "the draw sibling is a different contract"
    # the loop's recency bound, not a different one
    assert "make_interval" in sql


def test_the_probe_states_what_it_does_not_test():
    """Passing here means the BOOK READ works, not that the identity
    crossing does. Conflating those is what produced three wrong reads."""
    code = _code(API._venue_read_probe)
    assert "what_this_does_not_test" in code
    assert "resolve_venue_identity" in inspect.getsource(API._venue_read_probe)


def test_an_empty_candidate_set_is_named_not_reported_as_a_refusal():
    code = _code(API._venue_read_probe)
    assert "NO_FRESH_VENUE_CONTRACT_IN_PREMAP" in code, (
        "an empty catalogue is a freshness fact, not a venue refusal")
