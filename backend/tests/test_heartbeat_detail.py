"""/api/health/services flattened every nested counter into a string.

The COPYQUEUE line of the engine diagnostic printed "unavailable" on
five consecutive probes. I read that as an instrument I had not
finished wiring. It was wired; the ENDPOINT was destroying its input.

    copy_sweep heartbeat  detail.copy_queue = {"n": .., "concurrency": ..}
    /api/health/services  detail.copy_queue = "{'n': .., 'concurrency': ..}"

A Python repr, single-quoted, truncated at 80 characters. jq indexing
it threw, the `||` fired, and the line reported a dead worker.

Copy-path queue latency is the number that says whether our OWN
semaphore is what ages a fresh signal into a stale-signal rejection.
It has been published correctly and read never.

The truncation is the worse half, and the reason this is a leak and
not a cosmetic bug: at 80 characters a slightly larger counter block
does not fail loudly, it loses its tail — and the reader sees a
plausible smaller number instead of an error. A dict of four 48-hour
retry counts is 73 characters.
"""

import pytest

from sportsassets.api.app import _sanitize_detail as san


class TestNestedCountersSurviveAsNumbers:
    def test_the_copy_queue_block_stays_an_object(self):
        d = {"candidates": 12,
             "copy_queue": {"n": 41, "concurrency": 4,
                            "avg_wait_s": 2.31, "max_wait_s": 18.44}}
        out = san(d)
        assert isinstance(out["copy_queue"], dict)
        assert out["copy_queue"]["max_wait_s"] == 18.44
        assert out["copy_queue"]["n"] == 41

    def test_numbers_are_numbers_not_strings_at_depth(self):
        out = san({"a": {"b": {"c": 7}}})
        assert out["a"]["b"]["c"] == 7
        assert isinstance(out["a"]["b"]["c"], int)

    def test_the_retry_block_is_not_truncated(self):
        """73 characters as a repr — one more status and the old path
        cut a count in half and published the fragment."""
        d = {"retryable_48h": {"unfilled": 4213, "error": 118,
                               "rejected": 26571, "stale_signal": 9042,
                               "expired": 771}}
        out = san(d)
        assert out["retryable_48h"]["expired"] == 771
        assert sum(out["retryable_48h"].values()) == 40715

    def test_a_list_of_numbers_survives(self):
        out = san({"window_h": [3.0, 14.0]})
        assert out["window_h"] == [3.0, 14.0]

    def test_booleans_are_not_coerced_to_ints(self):
        out = san({"halted": True, "n": 1})
        assert out["halted"] is True
        assert out["n"] == 1


class TestItIsStillASanitizer:
    """It is public and it must never carry a payload or a token. The
    fix keeps scalars at depth; it does not open the endpoint up."""

    def test_long_strings_are_capped(self):
        out = san({"note": "x" * 500})
        assert len(out["note"]) == 80

    def test_long_strings_are_capped_at_depth_too(self):
        out = san({"a": {"b": "y" * 500}})
        assert len(out["a"]["b"]) == 80

    def test_depth_is_bounded(self):
        out = san({"a": {"b": {"c": {"d": {"e": 1}}}}})
        leaf = out["a"]["b"]["c"]
        assert isinstance(leaf, str) and "depth" in leaf, \
            "past the bound it reports its SHAPE, never its contents"

    def test_a_deep_payload_is_refused_not_half_printed(self):
        secret = {"a": {"b": {"c": {"token": "PMUS_SECRET_VALUE"}}}}
        assert "PMUS_SECRET" not in repr(san(secret))

    def test_key_count_is_bounded_and_says_so(self):
        out = san({str(i): i for i in range(200)})
        assert len(out) == 41            # 40 keys + the marker
        assert out["_truncated_keys"] == 160

    def test_list_length_is_bounded_and_says_so(self):
        out = san({"xs": list(range(100))})
        assert len(out["xs"]) == 21
        assert out["xs"][-1] == "<+80 more>"

    def test_an_arbitrary_object_is_stringified_and_capped(self):
        class _Thing:
            def __repr__(self):
                return "T" * 500

        assert len(san({"o": _Thing()})["o"]) == 80

    def test_none_survives_as_none(self):
        assert san({"x": None})["x"] is None


class TestTheEndpointActuallyUsesIt:
    def test_health_services_calls_the_sanitizer(self):
        import inspect

        from sportsassets.api import app as A

        src = inspect.getsource(A.health_services)
        assert "_sanitize_detail(" in src
        assert "str(v)[:80]" not in src, \
            "the flattening one-liner must be gone, not merely bypassed"

    def test_the_error_key_is_still_split_out_separately(self):
        import inspect

        from sportsassets.api import app as A

        src = inspect.getsource(A.health_services)
        assert 'if k != "error"' in src
        assert '[:160]' in src


class TestTheProbeCanTellTheThreeCasesApart:
    """It reported a dead worker when the endpoint was the problem.
    That exact mistake — an instrument that cannot see its subject,
    reporting anyway — has cost more than any single bug today."""

    def _probe(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        return (root / ".github/workflows/engine-diagnostic.yml").read_text()

    def test_a_missing_heartbeat_row_says_so(self):
        assert "no copy_sweep heartbeat" in self._probe()

    def test_a_mangled_detail_blames_the_endpoint_not_the_worker(self):
        assert "the ENDPOINT is mangling it" in self._probe()

    def test_it_no_longer_claims_unavailable_on_a_type_error(self):
        assert "COPYQUEUE unavailable" not in self._probe()
