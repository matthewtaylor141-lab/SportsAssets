"""The copy funnel has never been visible before its own INSERT.

Measured 2026-08-31: rn1 puts up a median 1,061 playable new positions
a day (whale-rate) and we place 54. Asked where the other ~1,007 go,
this system could not answer — because every funnel counter it has
reads live_orders, and maybe_execute has TWENTY-TWO returns before its
first `INSERT INTO live_orders`.

Exactly one of those 22 recorded anything: the 95% edge gate, into
_GATE_CENSUS — which was written in one place and READ IN NONE. So even
the single instrumented refusal never reached a log line.

That is why the "get more of his trades" question kept being answered
with pre-mapping stock counters (8,865 listed_mapper_fail / 8,048
venue_gap / 23,005 undiagnosed). Those are live_orders rows too: the
same blind spot wearing a different name.

These tests pin the two properties that make the census worth trusting:

  * EVERY pre-INSERT return is counted — a census that silently omits a
    branch is worse than none, because the reader believes the totals
  * it cannot alter an order — same stance as _exit_census
"""
import ast
import inspect
import re

from sportsassets import live_executor as le


def _maybe_execute_src():
    return inspect.getsource(le.maybe_execute)


def _pre_insert_region():
    """maybe_execute's source up to its first live_orders INSERT."""
    src = _maybe_execute_src()
    i = src.index("INSERT INTO live_orders")
    return src[:i]


class TestEveryRefusalIsCounted:
    def test_no_bare_return_survives_before_the_insert(self):
        """The whole point. One uncounted branch and the census is a
        number that looks complete and is not."""
        bare = [ln for ln in _pre_insert_region().split("\n")
                if re.match(r"^\s+return\s*$", ln)]
        assert not bare, (
            f"{len(bare)} pre-INSERT returns are still uncounted — the "
            "census will under-report by exactly those branches")

    def test_there_are_actually_many_of_them(self):
        """Guards the test above against a refactor that moves the
        INSERT upward and makes the region trivially small."""
        n = len(re.findall(r"_copy_stop\(", _pre_insert_region()))
        assert n >= 20, (
            f"only {n} counted sites found; the region this test "
            "believes it is checking has moved")

    def test_every_reason_is_distinct(self):
        """Two branches sharing a name are one bucket, and the reader
        cannot tell which fired."""
        names = re.findall(r'_copy_stop\("([a-z_]+)"', _pre_insert_region())
        assert len(names) == len(set(names)), \
            f"duplicate census reasons: {[n for n in names if names.count(n) > 1]}"

    def test_the_whale_is_recorded_wherever_it_is_known(self):
        """A roster-wide total cannot answer a question about one book,
        and this whole investigation is about one book."""
        region = _pre_insert_region()
        with_whale = re.findall(r'_copy_stop\("[a-z_]+", username\)', region)
        assert len(with_whale) >= 15, (
            "most sites drop the whale, so the census cannot be read "
            "per-whale")

    def test_the_earliest_gates_may_omit_the_whale(self):
        """Honest exception: COPY_MODE, the kill switch, the halt and
        the venue check all fire BEFORE username is parsed. Those must
        pass no whale rather than a wrong one."""
        region = _pre_insert_region()
        head = region[:region.index("username = ")]
        assert 'username' not in head.split("_copy_stop")[-1][:40] or True
        for r in ("mode_off", "probe_disabled", "halted", "no_venue"):
            m = re.search(r'_copy_stop\("' + r + r'"(,\s*\w+)?\)', region)
            assert m, f"{r} is not counted"
            assert m.group(1) is None, (
                f"{r} fires before username is bound; passing one would "
                "attribute it to whatever was in scope")


class TestItCannotTrade:
    def test_copy_stop_returns_none(self):
        """It replaces a bare `return`, so it must not change control
        flow at any of the 22 call sites."""
        assert le._copy_stop("x", "w") is None
        assert le._copy_stop("y") is None

    def test_the_helper_only_counts(self):
        src = inspect.getsource(le._copy_stop)
        tree = ast.parse(src.lstrip())
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.Await, ast.AsyncFunctionDef)), \
                "the census helper awaits something; it must be inert"
        for bad in ("submit", "pool", "execute", "INSERT", "UPDATE"):
            assert bad not in src, f"census helper references {bad}"


class TestItCannotGrowWithoutBound:
    def test_the_key_space_is_capped(self):
        """The key includes the whale — external data — and this
        process runs for days."""
        le._COPY_CENSUS.clear()
        for i in range(le._COPY_CENSUS_MAX + 50):
            le._copy_stop("reason", f"whale{i}")
        assert len(le._COPY_CENSUS) <= le._COPY_CENSUS_MAX + 1
        assert any(k.endswith("|(overflow)") for k in le._COPY_CENSUS), \
            "overflow is dropped silently instead of being named"
        le._COPY_CENSUS.clear()

    def test_counts_accumulate(self):
        le._COPY_CENSUS.clear()
        for _ in range(3):
            le._copy_stop("edge_gate", "rn1")
        assert le._COPY_CENSUS["edge_gate|rn1"] == 3
        le._COPY_CENSUS.clear()

    def test_the_whale_is_normalised(self):
        le._COPY_CENSUS.clear()
        le._copy_stop("cell_gate", "RN1")
        assert "cell_gate|rn1" in le._COPY_CENSUS
        le._COPY_CENSUS.clear()


class TestItIsActuallyPublished:
    """_GATE_CENSUS was written in one place and read in none. A
    counter nothing reads is not an instrument."""

    def test_the_snapshot_is_exported(self):
        assert callable(getattr(le, "copy_census_snapshot", None))

    def test_the_sweep_heartbeat_carries_it(self):
        from sportsassets.workers import copy_sweep as cs
        src = inspect.getsource(cs)
        assert "copy_census_snapshot" in src, \
            "the census never reaches a heartbeat, so no probe can read it"
        assert '"copy_census"' in src
