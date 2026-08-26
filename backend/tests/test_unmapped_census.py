"""The bucket that decides the fill rate could not be attributed at all.

listed_mapper_fail is 26,569 rejected rows — markets that ARE listed on
our venue and that we still could not name. 1,155 blocked whale entries
a day. At the repo's own 13.6% entry-to-fill conversion, fixing a
quarter of it adds ~39 fills/day against a 19.2/day baseline. Nothing
else on the board is that size.

And the endpoint written to diagnose it cannot see one row of it.
api_mapgap filters `us_market_slug IS NOT NULL`, and that column is
written only AFTER a mapping succeeds — so it measures the population
that WORKED while reporting on the population that failed.

resolve() has SIX ways to return None and they need completely
different fixes. "Coverage is fine" was an assumption resting on all
six being invisible.
"""

import asyncio
import inspect

from sportsassets.api import app as app_mod
from sportsassets.workers import premap


class _Pool:
    def __init__(self, rows=None, raise_it=False):
        self._rows = rows if rows is not None else []
        self._raise = raise_it

    async def fetch(self, _sql, *_a):
        if self._raise:
            raise RuntimeError("db down")
        return self._rows


def _explain(**kw):
    return asyncio.run(premap.resolve_explain(
        kw.pop("pool", _Pool()), kw.pop("title", "Arsenal vs Chelsea"),
        kw.pop("event", "epl-ars-che"), kw.pop("outcome", "Arsenal"),
        kw.pop("slug", "atc-epl-ars-che-2026-08-25-ars")))


class TestEverySilentFailureNowHasAName:
    def test_a_signal_with_no_slug_is_now_caught_EARLIER(self):
        """no_keys_built used to catch this. The dateless guard now
        precedes it, which is the more specific and more useful name:
        the reason a slugless signal builds nothing is that it cannot
        say which game it is on, and bare title keys match that pairing
        on every date."""
        r = _explain(title=None, event=None, slug=None)
        assert r["step"] == "no_date_on_his_signal"

    def test_a_DATED_signal_always_builds_at_least_the_slug_key(self):
        """Which is why no_keys_built is now unreachable rather than
        merely rare — stated here so a reader does not go looking for
        it in a census that can never report it."""
        from sportsassets.workers.premap import event_keys_for

        assert event_keys_for(None, "lpa-tig-cac-2026-08-24-tig")

    def test_no_key_intersection(self):
        """Keys were built and matched nothing — either the sweep never
        captured the market, or the two key sets are built differently.
        Those are different fixes, which is the whole point."""
        r = _explain(pool=_Pool([]))
        assert r["step"] == "no_key_intersection"
        assert r["keys"] > 0

    def test_a_failed_query_is_named_not_silently_a_miss(self):
        r = _explain(pool=_Pool(raise_it=True))
        assert r["step"] == "premap_query_failed"

    def test_no_side_match(self):
        r = _explain(outcome="Tottenham", pool=_Pool([
            {"identifier": "atc-epl-ars-che-2026-08-25-ars",
             "side_norm": "arsenal", "kind": "moneyline", "line": None,
             "question": "q", "event_title": "e", "intent": "i"}]))
        assert r["step"] in ("no_side_match", "type_prefix_filter_emptied")

    def test_side_has_no_intent(self):
        """The side matched and the venue named no long/short. Ordering
        it would hand side selection back to the venue — the 2026-08-24
        incident — so the refusal is right and must stay; it just needs
        COUNTING separately from a coverage miss."""
        r = _explain(pool=_Pool([
            {"identifier": "atc-epl-ars-che-2026-08-25-ars",
             "side_norm": "arsenal", "kind": "moneyline", "line": None,
             "question": "q", "event_title": "e", "intent": None}]))
        assert r["step"] in ("side_has_no_intent",
                             "type_prefix_filter_emptied")

    def test_every_step_name_is_distinct_and_actionable(self):
        """Six causes, six fixes. A census that collapsed them would be
        the same blindness with a nicer name."""
        src = inspect.getsource(premap.resolve_explain)
        for step in ("no_keys_built", "no_key_intersection",
                     "unknown_market_type", "type_prefix_filter_emptied",
                     "no_side_match", "side_has_no_intent", "resolves"):
            assert f'"{step}"' in src, step


class TestItCannotAffectAnOrder:
    """It walks the money path's decision tree. It must be impossible
    for it to place, price or refuse anything."""

    def test_it_is_separate_from_resolve(self):
        assert premap.resolve_explain is not premap.resolve

    def test_resolve_itself_grew_no_diagnostic_branch(self):
        src = inspect.getsource(premap.resolve)
        assert "resolve_explain" not in src

    def test_the_explainer_only_reads(self):
        src = inspect.getsource(premap.resolve_explain)
        for forbidden in ("INSERT", "UPDATE", "DELETE", "submit_fok",
                          "execute_copy", "pool.execute"):
            assert forbidden not in src, forbidden


class TestTheCensusMeasuresTheRightPopulation:
    def test_it_selects_rows_that_never_mapped(self):
        """The whole bug in api_mapgap, inverted."""
        src = inspect.getsource(app_mod.api_unmapped_census)
        assert "lo.us_market_slug IS NULL" in src
        assert "lo.status = 'rejected'" in src

    def test_it_records_why_api_mapgap_could_not(self):
        src = inspect.getsource(app_mod.api_unmapped_census)
        assert "us_market_slug IS NOT NULL" in src
        assert "the wrong population" in src

    def test_one_bad_row_cannot_kill_the_census(self):
        src = inspect.getsource(app_mod.api_unmapped_census)
        assert "explain_raised" in src

    def test_it_is_bounded(self):
        src = inspect.getsource(app_mod.api_unmapped_census)
        assert "min(int(sample), 1500)" in src

    def test_an_empty_window_does_not_claim_a_largest_cause(self):
        src = inspect.getsource(app_mod.api_unmapped_census)
        assert "NO UNMAPPED ROWS in window" in src

    def test_it_is_admin_only(self):
        route = [r for r in app_mod.app.routes
                 if getattr(r, "path", "") == "/api/admin/unmapped-census"]
        assert route and route[0].dependencies


class TestTheCensusAsksProductionsQuestion:
    """It passed `t.event_slug` into resolve_explain's EVENT_TITLE
    parameter.

    Those are different data — "mlb-nyy-bos-2026-08-25" against
    "New York Yankees vs. Boston Red Sox" — and event_keys_for builds
    title-derived keys out of whatever it is handed. So the census
    built a DIFFERENT KEY SET than production and then attributed
    production's failures to it.

    Failure mode (c), a probe reading a different argument list than
    the thing it measures, sitting inside the instrument that every
    coverage decision today was prioritised from. The published cause
    ranking — no_key_intersection 35.5%, resolves 25.8%, no_side_match
    23.3% — was measured on inputs the copy path never sees.
    """

    def _src(self):
        import inspect

        from sportsassets.api import app as A

        return inspect.getsource(A.api_unmapped_census)

    def test_it_passes_an_event_TITLE(self):
        src = self._src()
        assert 'r["event_title"]' in src
        assert 'r["event_slug"]' not in src

    def test_it_sources_the_title_the_way_production_does(self):
        """_market_context reads market_tokens joined to markets,
        preferring the enriched payload. The census must reach the same
        columns or it is measuring a different population."""
        src = self._src()
        assert "m.event_title" in src
        assert "market_tokens mt" in src
        assert "markets m" in src

    def test_it_left_joins_so_a_missing_market_row_is_not_dropped(self):
        """An INNER join would silently exclude exactly the unenriched
        rows this census exists to explain."""
        src = self._src()
        assert "LEFT JOIN market_tokens" in src
        assert "LEFT JOIN markets" in src

    def test_it_coalesces_the_same_way_production_prefers(self):
        src = self._src()
        for col in ("t.market_title, m.title", "t.outcome, mt.outcome",
                    "t.market_slug, m.slug"):
            assert f"COALESCE({col})" in src, col

    def test_the_two_argument_lists_agree(self):
        """The real invariant: whatever production hands resolve(), the
        census hands resolve_explain() in the same order."""
        import inspect
        import re

        from sportsassets.api import app as A
        from sportsassets import live_executor as le

        def _args(src, call):
            """The argument text of `call`, paren-balanced.

            A naive [^)]* stops inside ctx.get("market_title") and
            compares one argument against four — which would have let
            this test pass on the broken code."""
            i = src.index(call) + len(call)
            depth, out = 1, []
            while depth:
                c = src[i]
                depth += (c == "(") - (c == ")")
                if depth:
                    out.append(c)
                i += 1
            return "".join(out)

        def _fields(t):
            return re.findall(r'"([a-z_]+)"', t)

        prod = _fields(_args(inspect.getsource(le.maybe_execute),
                             "_premap.resolve("))
        cens = _fields(_args(inspect.getsource(A.api_unmapped_census),
                             "resolve_explain("))
        assert prod and cens
        assert prod == cens, (prod, cens)
