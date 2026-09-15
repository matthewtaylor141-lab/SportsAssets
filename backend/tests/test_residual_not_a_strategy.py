"""The derived remainder is a measurement error term, not a sleeve.

For thirteen days the owner's daily report published $5,001.11 of P&L
under the key `software` — a strategy class he ordered OFF on
2026-08-17 — with `settled=0` on every one of those days. The probe
printed it verbatim as `unattr=4970.76/0set` and nothing alarmed,
because a plug (`software = account - attributed`) cross-foots to the
account BY CONSTRUCTION no matter how wrong its inputs are. Its label
had already been corrected once, on 2026-08-22, after the same scare;
the correction reached CSV/PDF only, while the probe and every React
surface read the JSON KEY.

So these pins guard the key, the zeroed counts, the explicit marker,
and the honesty of the tie-out language — not the label.
"""

import inspect

from sportsassets.api import app as A


def _src():
    return inspect.getsource(A._category_breakdown)


class TestTheKeyIsNotAStrategyName:
    def test_the_derived_remainder_is_keyed_residual(self):
        src = _src()
        assert '_cat(day, "residual")' in src
        assert '_cat(day, "software")' not in src, \
            "the key is what the probe and the React surfaces read"

    def test_the_export_maps_carry_residual_not_software(self):
        assert "residual" in A._CAT_ORDER
        assert "software" not in A._CAT_ORDER
        assert "software" not in A._CAT_LABEL
        # The label must not name a strategy, and must say what it is.
        assert "not a strategy" in A._CAT_LABEL["residual"].lower()

    def test_every_export_category_has_a_label(self):
        # 2026-08-22 lesson: a category absent from the label map is
        # silently DROPPED from report.csv/pdf. Renaming the key must
        # not resurrect that failure.
        for cat in A._CAT_ORDER:
            assert cat in A._CAT_LABEL, f"{cat} would vanish from exports"


class TestTheCountsCannotLie:
    def test_the_residual_publishes_zero_counts_and_a_marker(self):
        src = _src()
        i = src.index('_cat(day, "residual")')
        block = src[i:i + 400]
        assert 'c["settled"] = 0' in block
        assert 'c["wins"] = 0' in block
        assert 'c["losses"] = 0' in block
        assert 'c["residual"] = True' in block, \
            "consumers need a positive marker to refuse to plot it"

    def test_the_dead_counts_loop_is_gone(self):
        # sw_counts could never count anything (copy_slugs marks nearly
        # every account row sleeve='copy' with no date bound), so it
        # dressed a plug in trade counts that were always zero.
        #
        # Pin the CODE, not the word: the comment above the deletion
        # names sw_counts on purpose, so that a reader who greps for it
        # finds why it went rather than nothing at all.
        src = _src()
        for construct in ("sw_counts: dict", "sw_counts.setdefault",
                          "sw_counts.get("):
            assert construct not in src, f"{construct} is still live code"

    def test_the_marker_survives_aggregation_into_totals(self):
        src = _src()
        assert 'if c.get("residual")' in src
        assert 't["residual"] = True' in src


class TestTheTieOutDoesNotOversellItself:
    def test_the_note_says_the_cross_foot_is_an_identity(self):
        src = _src()
        assert "cross_foots_by_construction" in src
        assert "BY CONSTRUCTION" in src
        assert "NOT an independent check" in src, \
            "a check that cannot fail must not read as evidence"

    def test_the_note_names_the_dominant_cause(self):
        # The residual is dominated by a dating mismatch: copies bucket
        # on the day our sweep DISCOVERED a resolution, the account on
        # the venue's own day. Saying so is what stops the next reader
        # from calling it trading P&L.
        src = _src()
        assert "DISCOVERED" in src
        assert "residual=true" in src


class TestTheProbeWouldHaveCaughtIt:
    def _wf(self):
        from pathlib import Path

        root = Path(A.__file__).resolve().parents[3]
        return (root / ".github/workflows/engine-diagnostic.yml").read_text()

    def test_the_probe_alarms_on_dollars_with_no_trades(self):
        y = self._wf()
        assert "BRK-PHANTOM" in y
        assert 'select(.key != "residual")' in y, \
            "the error term is exempt; every other category is not"

    def test_the_probe_reads_the_new_key(self):
        y = self._wf()
        assert "resid=\\($d.residual.pnl" in y
        assert "$d.software.pnl" not in y

    def test_the_probe_states_the_tieout_is_an_identity(self):
        assert "BRK-TIEOUT" in self._wf()
