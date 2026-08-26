"""Why the EXACT resolver missed, when it misses.

resolve_market_exact returned None silently and recorded nothing, so a
failure there was indistinguishable from never having been tried. Only
resolve_market -- the FUZZY fallback -- carried a last_diag. Every
unmapped diagnostic anyone has ever read therefore describes the fuzzy
attempt.

That matters because of where the volume is. Tonight's funnel:

    atp   4919 (24.6%)   wta  2425 (12.1%)   itf 2168 (10.9%)

Tennis is 48% of the recent unmapped funnel, it is moneyline-only, and
it goes through the exact path first. Four thousand nine hundred ATP
rows a week fail one step before the only instrument that was watching.

The abbreviation grammar itself is provably right -- checked against six
venue slugs from tonight's own receipts: harwen = Harry Wendelken,
stetra = Stefano Travaglia, danalt = Daniel Altmaier, colwon = Coleman
Wong, elmmoe = Elmer Moeller, ekaovc = Ekaterina Ovcharenko. So the
candidates are being built correctly and something downstream refuses
them, and there are at least four plausible somethings. Guessing which
is exactly what the owner asked to stop doing.

The codes distinguish them: all-404 means the venue does not list what
we generate (date, league code, or name grammar). `low` with a score
means the market is right and the floor or the outcome text is wrong.
`amb` means the side matcher cannot separate two players. `timeout`
means the 20s box is too small. Each points somewhere different.
"""

from __future__ import annotations

import inspect

from sportsassets import live_executor as le, pmus


class _Client:
    """A venue that answers exactly how each test needs it to."""

    def __init__(self, table):
        self.table = table
        self.markets = self

    def retrieve_by_slug(self, slug):
        v = self.table.get(slug)
        if v is None:
            raise RuntimeError("404 not found")
        return {"market": v}


def _run(table, cands, outcome, monkeypatch):
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))
    diag: list[str] = []
    got = pmus.resolve_market_exact(cands, outcome, diag)
    return got, diag


class TestEachRefusalIsNamed:
    def test_an_unlisted_candidate_is_404(self, monkeypatch):
        got, diag = _run({}, ["aec-atp-a-b-2026-08-26"], "X", monkeypatch)
        assert got is None
        assert diag == ["404"]

    def test_every_candidate_is_recorded_not_just_the_first(
            self, monkeypatch):
        got, diag = _run({}, ["a", "b", "c", "d"], "X", monkeypatch)
        assert got is None
        assert diag == ["404"] * 4, \
            "a per-candidate code is what makes 6x404 aggregate"

    def test_a_closed_market_is_distinguished_from_a_missing_one(
            self, monkeypatch):
        got, diag = _run({"s": {"slug": "s", "closed": True}}, ["s"],
                         "X", monkeypatch)
        assert got is None and diag == ["closed"]

    def test_a_low_score_carries_the_NUMBER(self, monkeypatch):
        """A floor problem and a wrong-market problem look identical
        without it."""
        got, diag = _run({"s": {"slug": "s", "outcome": "Totally Other"}},
                         ["s"], "Hanshin Tigers", monkeypatch)
        assert got is None
        assert diag and diag[0].startswith("low:")
        score = float(diag[0].split(":")[1])
        assert 0.0 <= score < pmus.MATCH_FLOOR

    def test_a_match_is_recorded_as_ok(self, monkeypatch):
        got, diag = _run({"s": {"slug": "s", "outcome": "Hanshin Tigers"}},
                         ["s"], "Hanshin Tigers", monkeypatch)
        assert got is not None
        assert diag == ["ok"]

    def test_an_ambiguous_side_pair_is_named_with_both_scores(
            self, monkeypatch):
        """'Ito' scores 1.0 against both 'Aoi Ito' and 'Mai Ito' through
        the containment rule -- the case that once let venue side
        ORDERING pick the player."""
        mkt = {"slug": "s", "outcome": "parent",
               "marketSides": [
                   {"identifier": "s-a", "description": "Aoi Ito"},
                   {"identifier": "s-b", "description": "Mai Ito"}]}
        got, diag = _run({"s": mkt}, ["s"], "Ito", monkeypatch)
        assert got is None, "ambiguity must still refuse"
        assert diag and diag[0].startswith("amb:")

    def test_a_clean_side_match_still_resolves(self, monkeypatch):
        mkt = {"slug": "s", "outcome": "parent",
               "marketSides": [
                   {"identifier": "s-a", "description": "Aoi Ito"},
                   {"identifier": "s-b", "description": "Coco Gauff"}]}
        got, diag = _run({"s": mkt}, ["s"], "Coco Gauff", monkeypatch)
        assert got is not None
        assert got["market_slug"] == "s-b"
        assert diag == ["ok"]


class TestTheDiagnosticCannotLie:
    def test_it_is_a_parameter_not_a_shared_attribute(self):
        """resolve_market's last_diag is a function attribute, and this
        runs under asyncio.to_thread with four copies in flight -- an
        attribute hands one row's reason to another row. A diagnostic
        that misattributes is worse than none."""
        sig = inspect.signature(pmus.resolve_market_exact)
        assert "diag_out" in sig.parameters
        assert sig.parameters["diag_out"].default is None
        src = inspect.getsource(pmus.resolve_market_exact)
        assert "resolve_market_exact.last_diag" not in src

    def test_two_concurrent_callers_get_their_own_reasons(
            self, monkeypatch):
        monkeypatch.setattr(pmus, "_get_client",
                            lambda: _Client({"hit": {"slug": "hit",
                                                     "outcome": "A"}}))
        d1: list[str] = []
        d2: list[str] = []
        pmus.resolve_market_exact(["miss", "miss2"], "A", d1)
        pmus.resolve_market_exact(["hit"], "A", d2)
        assert d1 == ["404", "404"]
        assert d2 == ["ok"]

    def test_omitting_it_is_still_valid(self):
        """Every pre-existing caller passes two arguments."""
        sig = inspect.signature(pmus.resolve_market_exact)
        assert len([p for p in sig.parameters.values()
                    if p.default is inspect.Parameter.empty]) == 2

    def test_it_is_bounded(self, monkeypatch):
        """A row's error column is 300 chars; an unbounded list would
        be truncated into nonsense."""
        got, diag = _run({}, [f"c{i}" for i in range(200)], "X",
                         monkeypatch)
        assert got is None
        assert len(diag) <= 24


class TestItReachesTheRejectedRow:
    def test_the_exact_summary_is_written_to_the_row(self):
        src = inspect.getsource(le.maybe_execute)
        block = src[src.index('diag = getattr(pmus.resolve_market'):]
        block = block[:block.index("log.info")]
        assert "_ex_diag" in block
        assert "unmapped: " in block

    def test_it_is_COUNTED_not_listed(self):
        """4,919 rows a week is a distribution question. '6x404'
        aggregates where six separate lines do not."""
        src = inspect.getsource(le.maybe_execute)
        block = src[src.index('diag = getattr(pmus.resolve_market'):]
        block = block[:block.index("log.info")]
        assert 'f"{v}x{k}"' in block

    def test_the_first_candidate_survives_verbatim(self):
        """So the generated grammar can be read back at a glance --
        'aec-atp-harwen-stetra-2026-08-24 6x404' says the abbreviation
        is fine and the DATE or the league code is not."""
        src = inspect.getsource(le.maybe_execute)
        block = src[src.index('diag = getattr(pmus.resolve_market'):]
        block = block[:block.index("log.info")]
        assert "_ex_cands[0]" in block

    def test_a_candidate_list_that_never_ran_is_distinguishable(self):
        """Empty diag with candidates present means the exact phase was
        skipped -- a different bug from every candidate 404ing."""
        src = inspect.getsource(le.maybe_execute)
        assert "not-run" in src

    def test_a_timeout_is_recorded_rather_than_silently_falling_through(
            self):
        """The exact phase is boxed at 20s and tennis triples the
        worst-case serial lookups, so 'the box is too small' is a live
        hypothesis that has never been observable."""
        src = inspect.getsource(le.maybe_execute)
        assert src.count('_ex_diag.append("timeout")') == 3, \
            "all three exact phases need it -- moneyline, derivative, " \
            "and the whale's own slug"

    def test_the_fuzzy_diagnostic_is_still_kept(self):
        """The exact reason is additional evidence, not a replacement."""
        src = inspect.getsource(le.maybe_execute)
        block = src[src.index('diag = getattr(pmus.resolve_market'):]
        block = block[:block.index("log.info")]
        assert "+ diag" in block


class TestTheGrammarItselfIsNotSuspect:
    """Six venue slugs from tonight's own overspend receipts. If the
    abbreviation rule were wrong these would not round-trip, and the
    investigation would start somewhere else entirely."""

    def test_the_abbreviation_matches_live_venue_slugs(self):
        cases = [("Harry Wendelken", "harwen"),
                 ("Stefano Travaglia", "stetra"),
                 ("Daniel Altmaier", "danalt"),
                 ("Francisco Comesana", "fracom"),
                 ("Coleman Wong", "colwon"),
                 ("Elmer Moeller", "elmmoe"),
                 ("Dominika Salkova", "domsal"),
                 ("Ekaterina Ovcharenko", "ekaovc")]
        for name, want in cases:
            assert le._abbrev_player(name) == want, name

    def test_a_single_token_name_refuses_rather_than_guessing(self):
        assert le._abbrev_player("Ito") is None

    def test_unicode_folds(self):
        assert le._abbrev_player("João Sousa") == "joasou"
