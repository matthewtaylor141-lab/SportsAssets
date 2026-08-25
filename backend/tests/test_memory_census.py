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
