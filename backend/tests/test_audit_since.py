"""The display window and the audit floor are different numbers.

The 2026-08-24 owner order re-baselined the FRONT END ("start fresh
today"), which moved DEFAULT_SINCE to that day's epoch. Two
reconciliations anchored on track_record(None) and so inherited the new
window — while their other side, live_orders, counts rows by the day
they SETTLED, with no floor at all. The two sides stopped covering the
same population and the difference surfaced as a phantom residual:
anchor=1 row vs copies=471 rows, reported as $867 of unattributed P&L
and tripping RECON-ALARM on every probe.

An alarm that fires on its own windowing is worse than no alarm — it
trains the reader to ignore the one control watching record-vs-venue
divergence. These pin the separation.
"""

import inspect

from sportsassets.api import app as app_mod
from sportsassets.api.track_record import (
    AUDIT_SINCE, DEFAULT_SINCE, RECORD_EPOCH,
)


def test_the_audit_floor_predates_the_display_epoch():
    assert AUDIT_SINCE < RECORD_EPOCH, (
        "the audit must reach back further than the display window, or "
        "it cannot see the rows the display is hiding")
    assert AUDIT_SINCE == "2026-08-01"


def test_the_display_epoch_is_still_the_rebaselined_day():
    """The owner's re-baseline stands — this fix must not quietly undo
    it by widening the DISPLAY window."""
    assert RECORD_EPOCH == "2026-08-24"
    assert DEFAULT_SINCE == RECORD_EPOCH


def _code(src: str) -> str:
    """Source with comments and docstrings stripped.

    A guard that greps raw source can be satisfied — or broken — by a
    COMMENT that happens to contain the phrase. That already bit this
    codebase once today (a comment shadowed a safety guard and made its
    test pass while pointing at prose). These tests are about what the
    code DOES, so they read tokens, not paragraphs."""
    import io
    import tokenize

    out = []
    prev_type = tokenize.INDENT
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError):
        # Method sources come dedented-with-context in some Python
        # builds; fall back to a line filter rather than skipping.
        return "\n".join(ln for ln in src.splitlines()
                          if not ln.lstrip().startswith("#"))
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            continue
        if (tok.type == tokenize.STRING
                and prev_type in (tokenize.INDENT, tokenize.NEWLINE,
                                  tokenize.NL, tokenize.DEDENT)):
            continue  # a bare string statement is a docstring
        out.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev_type = tok.type
    return " ".join(out)


def _src(fn):
    import textwrap

    return _code(textwrap.dedent(inspect.getsource(fn)))


def test_the_day_detail_audit_does_not_anchor_on_the_display_window():
    src = _src(app_mod.api_breakdown_day_detail)
    assert "track_record ( AUDIT_SINCE )" in src
    assert "track_record ( None )" not in src


def test_the_category_breakdown_does_not_anchor_on_the_display_window():
    src = _src(app_mod._category_breakdown)
    assert "track_record ( _audit_since )" in src
    assert "track_record ( None )" not in src


def test_no_reconciliation_in_app_still_reads_the_display_default():
    """Belt and braces across the module: a future reconciliation that
    reaches for track_record(None) gets caught here rather than in a
    probe alarm three days later."""
    # The served endpoint passes the caller's `since` through, which is
    # correct — it is a DISPLAY. Bare None calls are the audit hazard.
    src = _code(inspect.getsource(app_mod))
    assert "track_record ( None )" not in src
