"""Stop guessing at the OOM and measure it.

Three memory fixes shipped tonight, each on reasoning that it was
obviously the cost:

    type filter on the hydrate   317,681 -> 300,182 rows   (5.5%)
    streaming snapshot packer    RSS 595 -> 1,684.6 MB on ONE process
                                 across a completed grind, no restart

Both removed real waste. Neither was the thing. The fourth guess is
not worth shipping — a number is.

These tests pin the properties that make the census trustworthy: it
must deep-size (a shallow getsizeof on a dict of strings reports ~360
bytes and hides the strings, which would rank every holder as
negligible), it must not allocate a copy of the archive to measure the
archive, and it must publish the UNACCOUNTED gap rather than implying
the caches add up to RSS.
"""

import inspect

from sportsassets.api import app as app_mod


def _fns():
    src = inspect.getsource(app_mod.api_memory_census)
    ns: dict = {}
    body = src[src.index("    import sys"):]
    # lift the two helpers out of the endpoint for direct testing
    import textwrap
    helper_src = body[body.index("    def _deep"):body.index("    rss_mb")]
    exec("import sys\nfrom typing import Any\n"
         + textwrap.dedent(helper_src), ns)
    return ns["_deep"], ns["_measure"]


class TestItSeesWhatShallowSizingHides:
    def test_a_dict_of_strings_weighs_more_than_its_shell(self):
        import sys

        _deep, _ = _fns()
        row = {"slug": "aec-atp-harwen-stetra-2026-08-24",
               "type": "ACTIVITY_TYPE_TRADE",
               "title": "Harry Wendelken vs Stefano Travaglia"}
        assert _deep(row) > sys.getsizeof(row) * 1.5, (
            "shallow sizing hides the strings — that is the whole "
            "reason the caches looked negligible")

    def test_a_realistic_slim_row_is_hundreds_of_bytes(self):
        _deep, _ = _fns()
        row = {"id": "0x" + "a" * 60, "type": "ACTIVITY_TYPE_TRADE",
               "slug": "aec-mlb-pit-sd-2026-08-24", "side": "BUY",
               "outcome": "San Diego Padres", "price": 0.55,
               "size": 1136.0, "usd": 249.92,
               "ts": "2026-08-25T05:33:00Z"}
        assert 400 < _deep(row) < 6000


class TestTheEstimateScales:
    def test_it_reports_rows_and_a_per_row_cost(self):
        _, _measure = _fns()
        rows = [{"a": "x" * 50, "b": i} for i in range(5000)]
        out = _measure(rows)
        assert out["rows"] == 5000
        assert out["bytes_per_row"] > 100
        assert out["est_mb"] > 0

    def test_empty_and_missing_are_zero_not_an_error(self):
        _, _measure = _fns()
        for empty in (None, [], {}, "not a list"):
            out = _measure(empty)
            assert out["rows"] == 0 and out["est_mb"] == 0.0

    def test_measuring_does_not_copy_the_whole_archive(self):
        """A census that allocates a second archive to size the first
        would push a struggling process over the line it is there to
        diagnose. The sample is bounded regardless of input size."""
        _, _measure = _fns()
        big = [{"a": "y" * 40, "i": i} for i in range(200000)]
        out = _measure(big, sample=200)
        assert out["rows"] == 200000
        assert out["bytes_per_row"] > 0
        src = inspect.getsource(app_mod.api_memory_census)
        assert "sample: int = 200" in src
        assert "rows[::step][:sample]" in src


class TestTheGapIsPublished:
    """The failure mode this whole night kept producing is an
    instrument that cannot see its subject reporting anyway. If the
    retained structures do not explain RSS, the census must SAY the
    remainder is unexplained rather than presenting a total that looks
    like an answer."""

    def test_unaccounted_is_returned(self):
        src = inspect.getsource(app_mod.api_memory_census)
        assert '"unaccounted_mb"' in src
        assert '"accounted_mb"' in src

    def test_it_states_that_the_estimate_is_sampled(self):
        src = inspect.getsource(app_mod.api_memory_census)
        assert '"note"' in src
        assert "not an exact walk" in src

    def test_it_measures_all_three_known_holders(self):
        src = inspect.getsource(app_mod.api_memory_census)
        for holder in ("_archive_cache", "_hydrate_progress", "_raw_cache"):
            assert holder in src


class TestItIsAdminOnly:
    def test_the_route_requires_the_admin_token(self):
        route = [r for r in app_mod.app.routes
                 if getattr(r, "path", "") == "/api/admin/memory-census"]
        assert route, "route not registered"
        assert route[0].dependencies, "census must not be public"


class TestSharedStringsAreChargedOnce:
    """The correction to this census's own first result.

    _slim interns type tags, market slugs and sides, so one string
    object is shared across tens of thousands of rows. The first
    version charged every row the full size of those shared objects
    and reported 1,838 B/row and 526 MB for the archive — a number my
    own instrument invented, and one I had already quoted as fact.

    "How much would freeing this row give back" is the marginal cost,
    not the isolated one. Both are now reported, because the naive
    figure still answers a different real question (what one row costs
    if nothing else exists).
    """

    def test_interned_rows_cost_far_less_marginally_than_naively(self):
        import sys

        _, _measure = _fns()
        tag = sys.intern("ACTIVITY_TYPE_TRADE")
        slug = sys.intern("aec-mlb-pit-sd-2026-08-24")
        rows = [{"type": tag, "slug": slug, "id": f"0x{i:060x}"}
                for i in range(4000)]
        out = _measure(rows)
        assert out["bytes_per_row_naive"] > out["bytes_per_row"], (
            "shared strings must not be charged to every row")

    def test_unshared_rows_measure_about_the_same_either_way(self):
        """The correction must not deflate genuinely distinct data —
        that would swap one wrong number for another."""
        _, _measure = _fns()
        rows = [{"a": f"unique-value-{i}-{'z' * 30}"} for i in range(2000)]
        out = _measure(rows)
        naive, marg = out["bytes_per_row_naive"], out["bytes_per_row"]
        assert abs(naive - marg) < naive * 0.25

    def test_the_same_object_twice_is_counted_once(self):
        _deep, _ = _fns()
        inner = {"x": "y" * 100}
        seen: set = set()
        first = _deep({"a": inner}, seen)
        second = _deep({"b": inner}, seen)
        assert second < first, (
            "the second row shares `inner` and must not pay for it again")

    def test_est_mb_scales_the_marginal_cost(self):
        _, _measure = _fns()
        rows = [{"a": f"v{i}"} for i in range(10000)]
        out = _measure(rows)
        expected = out["bytes_per_row"] * out["rows"] / 1048576
        assert abs(out["est_mb"] - expected) < 0.5
