"""Nineteen of the exit path's twenty refusals were silent.

"mirror_exit has never placed an order" has stood as an open item all
day, and it is not a finding — it is the absence of one. classify_exit
and mirror_exit refuse in twenty distinct ways, and in production
"the whale never exited", "we never copied his entry", "the venue says
we hold nothing", "another task claimed it" and "this token is not
binary" all arrived as the same event: no log line at all.

That is the same position the mapper was in before resolve_explain, and
the same failure mode as a probe reading a column production does not
write. You cannot prove a path fires while every way it declines is
invisible.

The single most important distinction the census draws:

    mx_no_position_of_ours   we never copied his entry — NOTHING TO
                             SELL. At a 0.55% fill rate this should
                             dominate, and it is a coverage number, not
                             an exit defect.
    anything after
    mx_reached_position_lookup
                             we hold something and still did not sell.
                             THAT is an exit defect.

Reading the first as the second is how a working path gets rewritten.
"""

import inspect
import re

import pytest

from sportsassets import live_executor as le


@pytest.fixture(autouse=True)
def _clean_census():
    le._EXIT_CENSUS.clear()
    le._EXIT_RING.clear()
    yield
    le._EXIT_CENSUS.clear()
    le._EXIT_RING.clear()


class TestItCannotChangeAnOrder:
    """A diagnostic on the money path must be provably incapable of
    affecting a decision, and a reader must see that at a glance."""

    def test_the_recorder_always_returns_none(self):
        assert le._exit_stop("anything") is None
        assert le._exit_stop("anything", whale="x", n=1) is None

    def test_no_call_site_reads_its_value(self):
        """Every call is `return _exit_stop(...)` or a bare statement —
        never an assignment, a condition, or a comparison."""
        for fn in (le.classify_exit, le.mirror_exit):
            for line in inspect.getsource(fn).splitlines():
                t = line.strip()
                if "_exit_stop(" not in t:
                    continue
                assert t.startswith("return _exit_stop(") \
                    or t.startswith("_exit_stop("), \
                    f"census value used in a decision: {t!r}"

    def test_it_never_raises_on_an_unserialisable_context(self):
        class _Bad:
            def __repr__(self):
                return "z" * 5000

        assert le._exit_stop("r", obj=_Bad()) is None
        assert len(le._EXIT_RING[-1]["obj"]) == 64


class TestTheRingIsBounded:
    def test_it_cannot_grow_without_limit(self):
        for i in range(500):
            le._exit_stop("r", i=i)
        assert len(le._EXIT_RING) == le._EXIT_RING_MAX

    def test_it_keeps_the_NEWEST_entries(self):
        for i in range(500):
            le._exit_stop("r", i=i)
        assert le._EXIT_RING[-1]["i"] == 499

    def test_counts_are_not_capped_by_the_ring(self):
        for i in range(500):
            le._exit_stop("r", i=i)
        assert le._EXIT_CENSUS["r"] == 500

    def test_a_reason_with_no_context_costs_no_ring_slot(self):
        le._exit_stop("bare")
        assert le._EXIT_CENSUS["bare"] == 1
        assert le._EXIT_RING == []


class TestItSurvivesTheHeartbeatSanitizer:
    """The census has to travel from the worker process to a reader and
    the only channel is the heartbeat, which /api/health/services
    sanitizes to a bounded depth of 3."""

    def test_a_list_of_dicts_would_have_been_destroyed(self):
        from sportsassets.api.app import _sanitize_detail as san

        raw = {"exit_census_raw": {"recent": [{"reason": "x"}]}}
        assert "depth" in str(san(raw)["exit_census_raw"]["recent"][0]), \
            "this is WHY the ring is flattened to strings at the source"

    def test_the_flattened_lines_survive_intact(self):
        from sportsassets.api.app import _sanitize_detail as san

        le._exit_stop("mx_SOLD", whale="rn1", shares=214)
        lines = le.exit_census_lines()
        out = san({"exit_recent": lines})
        assert out["exit_recent"] == lines
        assert "mx_SOLD" in out["exit_recent"][0]

    def test_the_counts_survive_as_numbers(self):
        from sportsassets.api.app import _sanitize_detail as san

        le._exit_stop("mx_no_position_of_ours")
        out = san({"exit_census": le.exit_census()["counts"]})
        assert out["exit_census"]["mx_no_position_of_ours"] == 1

    def test_lines_are_capped_at_the_sanitizers_string_limit(self):
        le._exit_stop("r", a="q" * 300, b="w" * 300)
        assert all(len(x) <= 80 for x in le.exit_census_lines())


class TestEveryRefusalIsNamedDistinctly:
    def test_classify_exit_has_no_silent_return(self):
        src = inspect.getsource(le.classify_exit)
        bare = [l for l in src.splitlines()
                if l.strip() in ("return None", "return")]
        assert bare == [], f"silent refusals remain: {bare}"

    def test_mirror_exit_has_no_silent_return(self):
        src = inspect.getsource(le.mirror_exit)
        bare = [l for l in src.splitlines()
                if l.strip() in ("return None", "return")]
        assert bare == [], f"silent refusals remain: {bare}"

    def test_the_reasons_are_unique(self):
        import re

        src = (inspect.getsource(le.classify_exit)
               + inspect.getsource(le.mirror_exit))
        names = re.findall(r'_exit_stop\(\s*"([a-zA-Z_]+)"', src)
        assert len(names) == len(set(names)), \
            f"two paths share a reason: {names}"
        assert len(names) >= 18

    def test_the_two_functions_use_distinct_prefixes(self):
        import re

        cls = re.findall(r'_exit_stop\(\s*"([a-zA-Z_]+)"',
                         inspect.getsource(le.classify_exit))
        mx = re.findall(r'_exit_stop\(\s*"([a-zA-Z_]+)"',
                        inspect.getsource(le.mirror_exit))
        assert all(n.startswith("cls_") for n in cls)
        assert all(n.startswith("mx_") for n in mx)

    def test_coverage_and_defect_are_never_the_same_bucket(self):
        """'We never copied his entry' and 'we hold it and could not
        sell' must never share a counter. Collapsing them is how a
        working exit path gets blamed for a fill-rate problem."""
        src = inspect.getsource(le.mirror_exit)
        assert "mx_no_position_of_ours" in src
        assert "mx_venue_holds_nothing" in src
        assert "mx_reached_position_lookup" in src


class TestTheEndpointReadsTheRightProcess:
    """The counters live in the process that runs the copy path — the
    WORKER process, where poller, copy_sweep and whale_exits all run
    under workers/all.py. The API is separate. An endpoint returning
    its own in-process census would answer zero forever and read as
    'the exit path never ran' — the exact false negative this exists
    to prevent."""

    def test_it_does_not_call_the_module_global(self):
        from sportsassets.api import app as A

        src = inspect.getsource(A.admin_exit_census)
        assert "service_heartbeats" in src
        # the module global would be reached as le.exit_census() /
        # live_executor.exit_census() — the endpoint's own NAME
        # contains the substring, so match the call, not the word
        assert "live_executor" not in src
        assert ".exit_census()" not in src, \
            "that would read the API process's own empty counters"

    def test_it_names_its_source_in_its_own_output(self):
        from sportsassets.api import app as A

        src = inspect.getsource(A.admin_exit_census)
        assert '"source"' in src and "worker process" in src

    def test_a_missing_heartbeat_is_reported_as_a_liveness_problem(self):
        from sportsassets.api import app as A

        src = inspect.getsource(A.admin_exit_census)
        assert "never completed a cycle" in src
        assert "not an exit-path finding" in src

    def test_the_verdict_separates_fill_rate_from_exit_defect(self):
        from sportsassets.api import app as A

        src = inspect.getsource(A.admin_exit_census)
        assert "FILL RATE constraint" in src
        assert "post_position_refusals" in src

    def test_the_sweep_heartbeat_actually_carries_it(self):
        from sportsassets.workers import copy_sweep as cs

        src = inspect.getsource(cs.sweep_once)
        assert '"exit_census"' in src and '"exit_recent"' in src


class TestTheProbeCanReadIt:
    def _probe(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        return (root / ".github/workflows/engine-diagnostic.yml").read_text()

    def test_the_line_exists(self):
        assert "EXITCENSUS" in self._probe()

    def test_an_empty_census_says_what_empty_means(self):
        assert "has not run once since this worker booted" in self._probe()

    def test_it_prints_the_recent_ring_too(self):
        assert "EXITRECENT" in self._probe()


class TestAbsentIsNotEmpty:
    """A heartbeat with NO exit_census field was substituted with {}
    and then reported available:true beside the verdict "no exit signal
    has reached mirror_exit at all".

    That is a confident claim about the exit path drawn from a
    heartbeat that never measured it — the exact false negative this
    endpoint exists to prevent, reproduced inside the endpoint. The
    most likely cause is also the most misleading one: the worker
    running an older commit than the API."""

    def _src(self):
        from sportsassets.api import app as A

        return inspect.getsource(A.admin_exit_census)

    def test_a_missing_field_reports_unavailable(self):
        src = self._src()
        assert '"exit_census" not in detail' in src
        i = src.index('"exit_census" not in detail')
        assert '"available": False' in src[i:i + 500]

    def test_it_names_the_likely_cause(self):
        assert "older commit than the API" in self._src()

    def test_it_refuses_to_draw_a_conclusion(self):
        assert "says NOTHING about whether exits are firing" in self._src()

    def test_an_EMPTY_census_is_still_distinguishable(self):
        """Present-but-empty means the worker published and nothing has
        happened yet — a different fact from absent, and the probe
        already says so."""
        from pathlib import Path

        from sportsassets.api import app as A

        root = Path(A.__file__).resolve().parents[3]
        y = (root / ".github/workflows/engine-diagnostic.yml").read_text()
        assert "has not run once since this worker booted" in y


class TestTheVerdictCanPointAtEveryPostLookupRefusal:
    """The defect list omitted three reasons, so the verdict could send
    a reader to post_position_refusals and show them an empty object
    while the sleeve sat halted. A diagnostic that names a bucket must
    be able to put things in it."""

    def _src(self):
        from sportsassets.api import app as A

        return inspect.getsource(A.admin_exit_census)

    def test_the_halt_is_in_the_defect_list(self):
        assert "mx_overspend_halt" in self._src()

    def test_every_post_lookup_reason_is_covered(self):
        from sportsassets import live_executor as le

        src = inspect.getsource(le.mirror_exit)
        after = src[src.index("mx_reached_position_lookup"):]
        reasons = set(re.findall(r'_exit_stop\(\s*"(mx_[a-z_]+)"', after))
        reasons.discard("mx_SOLD")
        listed = set(re.findall(r'"(mx_[a-z_]+)"', self._src()))
        missing = reasons - listed
        assert not missing, f"verdict cannot name: {sorted(missing)}"

    def test_the_reached_counter_is_stamped_AFTER_the_halt_gate(self):
        """It was counted before overspend_halt, so a sleeve stopped by
        a tripped breaker still reported every exit as reaching the
        lookup — a halted system looking like a working one with
        nothing to sell."""
        from sportsassets import live_executor as le

        src = inspect.getsource(le.mirror_exit)
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        assert code.index("mx_overspend_halt") < \
            code.index("mx_reached_position_lookup")
