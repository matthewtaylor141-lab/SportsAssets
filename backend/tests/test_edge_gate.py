"""The 95% gate: only a demonstrated edge gets funded.

Owner requirement 2026-08-30. Until this gate existed the bar was a
report — five of the six graded books were funded with real money while
their intervals contained zero.

The two properties that matter and are pinned hardest:
  1. It FAILS CLOSED on every path, including ones that look like
     infrastructure noise rather than evidence. A gate that fails open
     is worse than none, because it reads as safety.
  2. It NEVER touches exits. A refused whale must still be sellable —
     at this venue an exit arrives labelled BUY, so a guard placed
     above classify_exit would silently strand every position we hold
     in a whale the moment his edge stopped clearing the bar.
"""

import inspect

from sportsassets import edge_gate as eg

PROVEN = {"edge_ci95": [0.0028, 0.046], "edge_roi": 0.0244,
          "edge_clusters": 41000, "truncated": False}
CROSSES = {"edge_ci95": [-0.0004, 0.0517], "edge_roi": 0.0257,
           "truncated": False}
LOSING = {"edge_ci95": [-0.09, -0.01], "edge_roi": -0.05, "truncated": False}

FRESH = {"stat_age_s": 600.0, "read_age_s": 10.0}


class TestOnlyADemonstratedEdgeIsFunded:
    def test_a_lower_bound_above_zero_funds(self):
        ok, why = eg.decide({"rn1": PROVEN}, "rn1", **FRESH)
        assert ok and why == "edge-proven-at-95"

    def test_an_interval_touching_zero_does_not(self):
        # homerunhazard on 2026-08-30: -0.04% lower bound. He was funded
        # that morning on a +0.09% bound that counted three legs of one
        # game as three results.
        ok, why = eg.decide({"hrh": CROSSES}, "hrh", **FRESH)
        assert not ok and why == "edge-not-demonstrated"

    def test_the_biggest_headline_number_loses_to_its_interval(self):
        # 0x076daa87: +6.98% on dollar deployed, interval -7.46% to
        # +21.42%. A point-estimate rule funds him first.
        big = {"edge_ci95": [-0.0746, 0.2142], "edge_roi": 0.0698}
        ok, why = eg.decide({"w": big}, "w", **FRESH)
        assert not ok and why == "edge-not-demonstrated"

    def test_a_proven_loser_is_named_as_one(self):
        ok, why = eg.decide({"w": LOSING}, "w", **FRESH)
        assert not ok and why == "edge-losing-at-95"

    def test_it_reads_the_interval_not_a_verdict_string(self):
        # analytics publishes no verdict prose; depending on one would
        # be a parsing dependency on a field that does not exist.
        src = inspect.getsource(eg.decide)
        assert "edge_ci95" in src
        for word in ("PROFITABLE", "NOT DEMONSTRATED", "edge_verdict"):
            assert word not in src


class TestItFailsClosed:
    def test_unreadable_state_refuses(self):
        ok, why = eg.decide(None, "rn1", stat_age_s=1.0, read_age_s=1.0,
                            read_err="OSError")
        assert not ok and why.startswith("edge-stat-unread")

    def test_a_stale_read_refuses_even_with_a_fresh_payload(self):
        ok, why = eg.decide({"rn1": PROVEN}, "rn1", stat_age_s=1.0,
                            read_age_s=eg.GATE_CACHE_MAX_AGE_S + 1)
        assert not ok and why == "edge-stat-read-stale"

    def test_a_stale_statistic_refuses(self):
        ok, why = eg.decide({"rn1": PROVEN}, "rn1",
                            stat_age_s=eg.GATE_STAT_MAX_AGE_S + 1,
                            read_age_s=1.0)
        assert not ok and why == "edge-stat-stale"

    def test_an_undated_statistic_refuses(self):
        ok, why = eg.decide({"rn1": PROVEN}, "rn1", stat_age_s=None,
                            read_age_s=1.0)
        assert not ok and why == "edge-stat-undated"

    def test_an_unknown_whale_refuses(self):
        ok, why = eg.decide({"rn1": PROVEN}, "somebody-else", **FRESH)
        assert not ok and why == "edge-missing-whale"

    def test_a_missing_or_broken_interval_refuses(self):
        for g, expect in (({}, "edge-no-interval"),
                          ({"edge_ci95": None}, "edge-no-interval"),
                          ({"edge_ci95": [0.1]}, "edge-no-interval"),
                          ({"edge_ci95": ["x", "y"]}, "edge-bad-interval")):
            ok, why = eg.decide({"w": g}, "w", **FRESH)
            assert not ok and why == expect, g

    def test_a_truncated_replay_refuses_however_good_it_looks(self):
        # merge_pnl caps at 600,000 fills per whale, so a flagged book's
        # statistic is the EARLIEST trades, not a sample of them.
        t = dict(PROVEN, truncated=True)
        ok, why = eg.decide({"w": t}, "w", **FRESH)
        assert not ok and why == "edge-truncated-replay"

    def test_a_clean_row_in_a_truncated_PAYLOAD_refuses(self):
        # THE RUN-1403 CASE, and the reason this rule exists.
        #
        # That publish flagged three whales and left three unflagged —
        # and the unflagged rows were wrong too: the API's live replay
        # of the same books, minutes apart, put 0x076daa87 at
        # [-7.51,+21.09] where the payload claimed [+2.49,+19.99].
        # Row-by-row reading would have funded him — the poll-bound
        # whale whose filled cohort runs -28% ROI — off that row.
        #
        # So a neighbour's truncation condemns a clean row. Whatever
        # cut the replay short did not confine itself to the books it
        # labelled.
        payload = {"clean": PROVEN, "cut": dict(PROVEN, truncated=True)}
        ok, why = eg.decide(payload, "clean", **FRESH)
        assert not ok and why == "edge-publish-truncated"

    def test_the_flagged_whale_keeps_its_own_reason(self):
        # Both rules are live and the diagnosis stays specific: the
        # whale that was actually cut is named as cut, not blamed on a
        # neighbour. Reversing these two checks would make
        # edge-truncated-replay unreachable dead code.
        payload = {"clean": PROVEN, "cut": dict(PROVEN, truncated=True)}
        ok, why = eg.decide(payload, "cut", **FRESH)
        assert not ok and why == "edge-truncated-replay"

    def test_a_clean_payload_still_funds(self):
        # The tightening must not be a blanket refusal — it self-heals
        # on the next clean publish, and this is what "clean" means.
        payload = {"a": PROVEN, "b": dict(CROSSES), "c": dict(LOSING)}
        ok, why = eg.decide(payload, "a", **FRESH)
        assert ok and why == "edge-proven-at-95"

    def test_a_junk_row_beside_a_good_one_is_not_read_as_truncated(self):
        # A row that is not a dict, or carries no flag, is not evidence
        # OF truncation either — `truncated` absent means unflagged,
        # and a non-dict row is unreadable, not cut.
        payload = {"a": PROVEN, "junk": "not-a-dict", "bare": {}}
        ok, why = eg.decide(payload, "a", **FRESH)
        assert ok and why == "edge-proven-at-95"

    def test_empty_state_refuses(self):
        ok, why = eg.decide({}, "rn1", **FRESH)
        assert not ok and why == "edge-missing-whale"


class TestTheCacheCannotFundOnADeadDatabase:
    def test_read_at_is_stamped_only_after_a_successful_read(self):
        # Stamping at the top of refresh() makes now - read_at
        # permanently ~0, so the hard expiry never fires and a dead DB
        # funds whales forever on a verdict nobody can still read.
        # Read the FILE, not the attribute: conftest's autouse fixture
        # replaces edge_gate.refresh with a no-op so the rest of the
        # suite can reach the money path, and inspecting the attribute
        # would inspect the stub.
        whole = open(eg.__file__).read()
        src = whole[whole.index("async def refresh("):]
        src = src[:src.index("\ndef ")]
        body = src[src.index("try:"):]
        assert '_cache["read_at"] = now' in body, \
            "the stamp must be inside the success path"
        head = src[:src.index("try:")]
        assert '_cache["read_at"] = now' not in head

    def test_the_two_clocks_are_different(self):
        # Payload age and read age are separate questions: a payload can
        # be hours old and honest, but an unreadable row is not evidence.
        assert eg.GATE_CACHE_MAX_AGE_S < eg.GATE_STAT_MAX_AGE_S


class TestNoEnvironmentVariableOpensIt:
    def test_the_module_reads_no_env_at_all(self):
        src = inspect.getsource(eg)
        assert "environ" not in src and "getenv" not in src, (
            "a stale env silently overrode a roster order for two days "
            "in August; widening this gate is a reviewed code change")


class TestItSitsBelowTheExitPath:
    def _src(self):
        from sportsassets import live_executor as le

        return inspect.getsource(le.maybe_execute)

    def test_the_gate_is_after_mirror_exit_and_before_sizing(self):
        src = self._src()
        gate = src.index("edge_gate.verdict(")
        # classify_exit / mirror_exit decide an EXIT above this point.
        assert src.index("classify_exit") < gate
        assert src.index("mirror_exit") < gate
        # and every line that spends money is below it.
        assert gate < src.index("volume_normalized_clip")
        assert gate < src.index("INSERT INTO live_orders")

    def test_it_does_not_touch_the_exitable_set(self):
        import ast

        from sportsassets import live_executor as le

        # Check the CODE, not the prose — the module docstring discusses
        # exitable_whales at length precisely because not touching it is
        # the point.
        tree = ast.parse(inspect.getsource(eg))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "exitable_whales" not in called
        # And the exit path's own gate does not consult this one, so a
        # whale the bar refuses can always still be sold.
        assert "edge_gate" not in inspect.getsource(le.exitable_whales)
