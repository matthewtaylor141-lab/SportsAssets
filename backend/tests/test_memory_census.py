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
from sportsassets.api import track_record as tr


def _fns():
    src = inspect.getsource(app_mod.api_memory_census)
    ns: dict = {}
    body = src[src.index("    import sys"):]
    # lift the helpers out of the endpoint for direct testing; `tr` is
    # the endpoint's own import, which _measure reads to tell the
    # ledgers form from a row list
    import textwrap
    helper_src = body[body.index("    def _deep"):body.index("    rss_mb")]
    exec("import sys\nfrom typing import Any\n"
         "from sportsassets.api import track_record as tr\n"
         + textwrap.dedent(helper_src), ns)
    return ns["_deep"], ns["_measure"]


def _fill(aid, slug, ts=1785628800.0):
    return {"id": aid, "type": "ACTIVITY_TYPE_TRADE",
            "trade": {"marketSlug": slug, "qty": 2, "price": {"value": 0.5},
                      "createTime": ts * 1000}}


def _ledgers(n_rows=3000, n_slugs=60):
    led = tr._ArchiveLedgers()
    for i in range(n_rows):
        slug = f"aec-mlb-{i % n_slugs}-2026-08-02"
        if i % 6 == 0:
            led.fold({"id": f"r{i}", "type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
                      "timestamp": 1785628800.0 + i,
                      "positionResolution": {
                          "marketSlug": slug,
                          "afterPosition": {"realized": {"value": 1.0},
                                            "marketMetadata": {"title": "T"}},
                          "beforePosition": {"cost": {"value": 1.0}}}})
        elif i % 9 == 0:
            sell = _fill(f"t{i}", slug, 1785628800.0 + i)
            sell["trade"]["side"] = "TRADE_SIDE_SELL"
            sell["trade"]["realizedPnl"] = {"value": 0.1}
            led.fold(tr._slim(sell))
        else:
            led.fold(tr._slim(_fill(f"t{i}", slug, 1785628800.0 + i)))
    led.fold({"id": "j", "type": "ACTIVITY_TYPE_TRADE", "timestamp": 1.0,
              "trade": {"marketSlug": None}})
    return led


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


class TestTheLedgersFormIsMeasured:
    """The archive is the folded ledgers, not a row list (design D,
    2026-09-03). A census that sized only lists reported rows=0 and
    est_mb=0 for both the archive and the grind under that form, so
    the 55-115 MB they hold landed in unaccounted_mb -- the instrument
    blind to its subject, which is the failure this file exists to
    stop."""

    def test_a_ledgers_archive_is_sized_not_reported_empty(self):
        _, _measure = _fns()
        led = _ledgers()
        out = _measure(led)
        assert out["rows"] == led.rows == 3001
        assert out["form"] == "ledgers_v4"
        assert out["slugs"] == led.slugs() == 60
        assert out["est_mb"] > 0 and out["bytes_per_row"] > 0
        assert out["bytes_per_row_naive"] >= out["bytes_per_row"]

    def test_every_holder_inside_the_ledgers_is_sized(self):
        _, _measure = _fns()
        led = _ledgers()
        parts = _measure(led)["parts"]
        assert set(parts) == {"entries", "sold", "resolutions", "ids",
                              "leftover"}
        assert parts["entries"]["n"] == len(led.entries)
        assert parts["sold"]["n"] == len(led.sold)
        assert parts["resolutions"]["n"] == len(led.resolutions)
        assert parts["ids"]["n"] == len(led.ids) == 3001
        assert parts["leftover"]["n"] == len(led.leftover) == 1
        for p in parts.values():
            assert p["bytes_per_item"] > 0 and p["est_mb"] >= 0
        # The id memory is the O(rows) holder; it must dominate here.
        assert parts["ids"]["est_mb"] >= parts["entries"]["est_mb"]

    def test_the_estimate_is_the_sum_of_the_parts(self):
        _, _measure = _fns()
        out = _measure(_ledgers())
        total = sum(p["bytes_per_item"] * p["n"] for p in out["parts"].values())
        assert abs(out["est_mb"] - total / 1048576) < 0.2
        assert abs(out["bytes_per_row"] - total / out["rows"]) < 2

    def test_an_empty_ledgers_archive_is_zero_not_an_error(self):
        _, _measure = _fns()
        out = _measure(tr._ArchiveLedgers())
        assert out["rows"] == 0 and out["est_mb"] == 0.0
        assert out["form"] == "ledgers_v4"
        assert all(p["n"] == 0 for p in out["parts"].values())

    def test_the_sample_is_bounded_for_ledgers_too(self):
        """Sizing the dicts must not copy them: the sample is strided
        through the dict, never a list of every item."""
        _, _measure = _fns()
        led = tr._ArchiveLedgers()
        for i in range(120000):
            led.ids[f"0x{i:060x}"] = float(i)
        led.rows = 120000
        out = _measure(led, sample=50)
        assert out["parts"]["ids"]["n"] == 120000
        assert out["parts"]["ids"]["bytes_per_item"] > 0
        src = inspect.getsource(app_mod.api_memory_census)
        assert "islice(d.items(), 0, None, step)" in src
        assert "[:sample]" in src

    def test_the_ci_line_fields_survive_the_form(self):
        """engine-diagnostic.yml prints .rows, .bytes_per_row,
        .bytes_per_row_naive and .est_mb for the archive and the
        grind; the ledgers form must answer all four."""
        _, _measure = _fns()
        for holder in (_ledgers(200, 5), [{"a": "x" * 50}] * 200):
            out = _measure(holder)
            for key in ("rows", "bytes_per_row", "bytes_per_row_naive",
                        "est_mb"):
                assert key in out, key

    def test_the_census_reads_the_grind_ledgers_not_a_row_list(self):
        src = inspect.getsource(app_mod.api_memory_census)
        assert '_hydrate_progress.get("ledgers")' in src
        assert '_hydrate_progress.get("rows")' not in src
        assert 'tr._archive_cache.get("data")' in src
        assert "isinstance(rows, tr._ArchiveLedgers)" in src


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
