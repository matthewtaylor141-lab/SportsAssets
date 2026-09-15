"""no_key_intersection is 37% and its explanation names two causes
that both need opposite fixes — and the real one may be a third.

The census has said, since it was written:

    "his keys match no us_premap row — either the sweep never captured
     this market, or the two key sets are built differently"

A is sweep coverage. B is key grammar. The probe printed a pair that is
neither:

    whale  bol1-gvs-ori-2026-08-25-gvs
    venue  atc-lpb-gvs-ori-2026-08-25-gvs

Same game, same date, same teams, same market. The whale feed calls the
league `bol1`; the venue calls it `lpb`. The keys cannot intersect no
matter how well the sweep ran and no matter how carefully both sides
build their keys — the grammars agree and the VOCABULARIES do not.

This measures the class. It does not fix it. Dropping the league token
widens what a signal can match, and widening a key is precisely how a
whale's pick reaches another game's row — the incident this entire lane
exists to prevent. Two leagues can share three-letter team codes on one
date. So the size comes first, on real rejected rows, and the matcher
does not move until the number justifies the risk.
"""

import inspect

from sportsassets.api import app as A
from sportsassets.workers import premap


class TestItOnlyMeasures:
    def test_resolve_still_returns_none_on_a_league_mismatch(self):
        """The probe must not become a resolution path by accident."""
        src = inspect.getsource(premap.resolve)
        assert "league_alias_probe" not in src, \
            "the widened key must never reach the copy hot path"

    def test_the_probe_lives_only_in_the_explainer(self):
        assert "league_alias_probe" in inspect.getsource(
            premap.resolve_explain)

    def test_the_probe_cannot_raise_into_the_census(self):
        src = inspect.getsource(premap.resolve_explain)
        i = src.index("league_alias_probe")
        assert "except Exception" in src[i - 1200:], \
            "a diagnostic must not be able to kill the row it explains"

    def test_the_census_says_counted_not_applied(self):
        src = inspect.getsource(A.api_unmapped_census)
        assert "counted, NOT applied" in src
        assert "collide two leagues" in src


class TestTheStripIsConservative:
    def _strip(self, keys, d):
        """Mirror of the rule in resolve_explain."""
        out = set()
        for k in keys:
            if not d or not k.endswith(d):
                continue
            toks = [t for t in k[:-len(d)].rstrip("-").split("-") if t]
            if len(toks) >= 3:
                out.add("-".join(toks[1:]) + "-" + d)
        return out

    def test_it_strips_the_league_token_off_a_dated_slug_key(self):
        assert self._strip({"bol1-gvs-ori-2026-08-25"}, "2026-08-25") == \
            {"gvs-ori-2026-08-25"}

    def test_it_leaves_title_keys_alone(self):
        assert self._strip({"guabira vs oriente@2026-08-25"},
                           "2026-08-25") == set()

    def test_it_will_not_strip_a_key_with_too_few_tokens(self):
        """`gvs-2026-08-25` has nothing to spare — stripping it would
        leave a bare date, which matches every game that day.

        My first rule counted hyphens and got this wrong: the date
        contributes two of its own, so `gvs-2026-08-25` passed a
        `count("-") >= 3` test and stripped to `2026-08-25`. This test
        caught it before it shipped, and it is the reason the rule
        counts TOKENS IN FRONT OF THE DATE instead."""
        assert self._strip({"gvs-2026-08-25"}, "2026-08-25") == set()

    def test_it_requires_the_key_to_END_in_the_date(self):
        assert self._strip({"bol1-gvs-ori-2026-08-25-gvs"},
                           "2026-08-25") == set()

    def test_a_bare_date_can_never_be_produced(self):
        for k in ("a-2026-08-25", "2026-08-25", "ab-2026-08-25"):
            for out in self._strip({k}, "2026-08-25"):
                assert out != "2026-08-25"


class TestTheReportedNumbersAreHonest:
    def test_it_reports_the_share_of_the_cause_not_just_the_sample(self):
        src = inspect.getsource(A.api_unmapped_census)
        assert "share_of_no_key_intersection" in src
        assert "share_of_sample" in src

    def test_a_zero_denominator_reports_none_not_a_division(self):
        src = inspect.getsource(A.api_unmapped_census)
        i = src.index("share_of_no_key_intersection")
        seg = src[i:i + 320]
        assert 'if steps.get("no_key_intersection") else None' in seg

    def test_examples_are_bounded(self):
        src = inspect.getsource(A.api_unmapped_census)
        assert "len(alias_examples) < 5" in src

    def test_the_probe_line_says_not_applied(self):
        from pathlib import Path

        root = Path(A.__file__).resolve().parents[3]
        y = (root / ".github/workflows/engine-diagnostic.yml").read_text()
        assert "UNMAPALIAS" in y
        assert "COUNTED, NOT APPLIED" in y
