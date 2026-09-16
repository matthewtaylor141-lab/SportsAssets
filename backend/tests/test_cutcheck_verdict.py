"""A second basis that copies the first is not a second opinion.

/api/admin/true-edge-cashout re-grades whale P&L at their own exits
instead of at resolution. Where a whale has NO recorded sells it falls
back to the settlement number row by row — correctly, since there is
nothing else to use. But that makes cf_cashout identical to
cf_settlement, and the first version of the verdict then printed:

    rn1: settlement $-4285.24 -> cashout $-4285.24 (delta 0.0)
         exited 0/47397 — negative on both bases

"negative on both bases" reads as two independent confirmations of a
cut. It is one number printed twice. Every copied whale came back that
way — swisstony with 0 exits out of 142,890 detections.

That is the failure mode this whole night kept producing: an
instrument that cannot see its subject, reporting a result anyway.
Silence would have been investigated; false corroboration would have
been quoted back as proof the cuts were sound.

So: no exits, no verdict.
"""

import inspect

from sportsassets.api import app as app_mod


def _verdict(exited, settlement, cashout):
    """The classifier as written in api_true_edge_cashout."""
    if not exited:
        return ("NO EXIT DATA — cashout basis unavailable; this is the "
                "settlement number repeated, NOT a second opinion")
    if settlement <= 0 < cashout:
        return ("CUT MAY BE WRONG — negative at settlement, positive on "
                "his own exits")
    if cashout <= 0:
        return "negative on both bases"
    return "positive on both bases"


class TestNoExitsMeansNoVerdict:
    """The six real rows from 2026-08-25, all with exited=0."""

    ROWS = [
        ("homerunhazard", 0, 21123.78, 21123.78),
        ("swisstony", 0, 17969.64, 17969.64),
        ("0x076daa87", 0, 12166.16, 12166.16),
        ("rn1", 0, -4285.24, -4285.24),
        ("ferrarichampions2026", 0, -14252.08, -14252.08),
        ("0x2c33", 0, -59651.25, -59651.25),
    ]

    def test_none_of_them_claims_two_bases(self):
        for whale, exited, st, co in self.ROWS:
            v = _verdict(exited, st, co)
            assert "both bases" not in v, (
                f"{whale} has no exits — the verdict must not imply a "
                f"second basis")
            assert "NO EXIT DATA" in v

    def test_the_cut_whales_are_not_declared_confirmed(self):
        """The three cuts must NOT read as validated by a basis that
        does not exist. This is the specific misreading that would have
        closed the question."""
        for whale, exited, st, co in self.ROWS:
            if st < 0:
                assert "negative on both bases" != _verdict(exited, st, co)


class TestARealSecondBasisStillGrades:
    def test_a_cut_that_flips_positive_is_flagged(self):
        v = _verdict(120, -4285.24, 3100.0)
        assert v.startswith("CUT MAY BE WRONG")

    def test_negative_on_both_needs_real_exits(self):
        assert _verdict(120, -4285.24, -900.0) == "negative on both bases"

    def test_positive_on_both_needs_real_exits(self):
        assert _verdict(120, 500.0, 900.0) == "positive on both bases"

    def test_one_exit_is_enough_to_have_a_basis(self):
        """The gate is existence, not sufficiency — a thin basis is
        still a basis, and the counts are printed beside it."""
        assert "NO EXIT DATA" not in _verdict(1, -10.0, -10.0)


def test_the_endpoint_carries_the_guard():
    src = inspect.getsource(app_mod.api_true_edge_cashout)
    assert "NO EXIT DATA" in src
    assert 'if not (d.get("exited") or 0):' in src
