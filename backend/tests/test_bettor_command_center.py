"""THE COMMAND CENTRE'S REFUSALS, EXERCISED.

Every fixture in this file is SYNTHETIC and is labelled so: the helpers
are named `fake_*` and every record they build carries
`"synthetic": True` in its payload. None of it is venue data, none of
it is a run result, and nothing here is evidence of anything except
that the read model behaves.

The states the brief requires are each a test: stopped, armed with no
frames, active collection, a quiet healthy book, a disconnected feed, a
restart, an exhausted allowance, partial coverage, failed tests and
unavailable data.

Run:  python -m pytest backend/tests/test_bettor_command_center.py -q
"""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from sportsassets import bettor_command_center as CC
from sportsassets.api import command_center as IO

T0 = dt.datetime.fromisoformat(CC.WINDOW_START).timestamp()
T_END = dt.datetime.fromisoformat(CC.WINDOW_END).timestamp()
SLUGS = ["m%02d" % i for i in range(12)]


# ── synthetic fixtures. NOT VENUE DATA. ──────────────────────────────

def fake_ladder(at, slug, *, levels=4, boot="bootA", epoch=1,
                ladder_class=None):
    p = {"synthetic": True, "levels": levels,
         "bids": [1] * levels, "offers": []}
    if ladder_class is not None:
        p["ladder_class"] = ladder_class
    return {"boot_id": boot, "at": at, "kind": "LADDER", "epoch": epoch,
            "slug": slug, "payload": p}


def fake_gap(at, event, *, why="LINK_DOWN", frm=None, to=None, dur=None,
             boot="bootA", detail=None):
    p = {"synthetic": True, "event": event, "why": why}
    if frm is not None:
        p["from"] = frm
    if to is not None:
        p["to"] = to
    if dur is not None:
        p["duration_s"] = dur
    if detail is not None:
        p["detail"] = detail
    return {"boot_id": boot, "at": at, "kind": "GAP", "epoch": None,
            "slug": None, "payload": p}


def fake_epoch(at, event, *, epoch=1, boot="bootA", **kw):
    return {"boot_id": boot, "at": at, "kind": "EPOCH", "epoch": epoch,
            "slug": None,
            "payload": dict({"synthetic": True, "event": event,
                             "epoch": epoch}, **kw)}


def fake_probe(**kw):
    base = {"synthetic": True, "armed": True, "probe_id": "probe-synthetic",
            "max_distinct": 0,
            "max_incentive_manifest": 4, "incentive_manifest_reserved": 0,
            "max_incentive_recheck": 2, "incentive_recheck_reserved": 0,
            "max_incentive_retry": 2, "incentive_retry_reserved": 0,
            "max_socket_connect": 20, "socket_connect_reserved": 0,
            "max_socket_subscribe": 40, "socket_subscribe_reserved": 0,
            "deadline_at": CC.WINDOW_END}
    base.update(kw)
    return base


_DEFAULT = object()


def build(records=(), *, control=True, probe=_DEFAULT, run_row=None,
          allowlist=None, suites=(), artifacts=None, deployed=None,
          now=None):
    return CC.build(records=list(records), control=control,
                    probe=fake_probe() if probe is _DEFAULT else probe,
                    run_row=run_row or {"run_id": "2026-09-23:synthetic",
                                        "manifest_id": "synthetic",
                                        "et_date": "2026-09-23"},
                    allowlist=allowlist if allowlist is not None else SLUGS,
                    suites=list(suites), artifacts=artifacts or {},
                    deployed=deployed or {"sha": "synthetic",
                                          "source": "test"},
                    now=now if now is not None else T0 + 3600)


# ═══ THE TWO NAMED REFUSALS ══════════════════════════════════════════

class TestATrueFlagIsNotCollection:

    def test_control_true_with_no_frames_is_its_own_state(self):
        """ARMED_NO_FRAMES exists so this gap cannot hide."""
        out = build([], control=True)
        lc = out["views"]["live_operation"]["lifecycle"]
        assert lc["state"] == CC.L_ARMED_NO_FRAMES
        assert lc["state"] != CC.L_COLLECTING

    def test_a_connected_socket_with_no_frames_is_still_no_frames(self):
        """An OPEN EPOCH is not persistence. The run has connected,
        subscribed and written an epoch record -- and nothing else."""
        recs = [fake_epoch(T0, "RUN_STARTED", subscribed=12),
                fake_epoch(T0 + 1, "EPOCH_OPENED", connect_attempts=1,
                           subscribe_messages=12, reconnects=1)]
        out = build(recs, control=True)
        live = out["views"]["live_operation"]
        assert live["lifecycle"]["state"] == CC.L_ARMED_NO_FRAMES
        # The health block may say connected; the lifecycle still does not
        # say collecting. The two answers are about different questions.
        assert live["health"]["verdict"] != CC.H_NOT_STARTED
        assert live["frames"]["first_persisted"]["status"] == CC.UNKNOWN
        assert live["frames"]["first_persisted"]["value"] is None

    def test_management_does_not_call_it_healthy(self):
        out = build([], control=True)
        mg = out["views"]["management"]
        assert mg["system_health"]["state"]["value"] == CC.L_ARMED_NO_FRAMES
        assert any("NOT PRODUCING FRAMES" in b["blocker"]
                   for b in mg["blockers"])


class TestAQuietBookIsNotABrokenFeed:

    def test_long_silence_with_no_open_gap_is_quiet_not_broken(self):
        """Six hours of silence on a change-driven feed is a market
        nobody traded, and the verdict says so in those words."""
        recs = [fake_epoch(T0, "RUN_STARTED"),
                fake_ladder(T0 + 10, SLUGS[0])]
        h = CC.connection_health(recs, now=T0 + 6 * 3600, control=True)
        assert h["verdict"] == CC.H_LIVE_QUIET
        assert h["verdict"] != CC.H_DISCONNECTED
        assert "not a fault" in h["silence_s"]["note"].lower() or \
               "QUIET" in h["why"]

    def test_quiet_does_not_move_the_lifecycle_off_collecting(self):
        recs = [fake_epoch(T0, "RUN_STARTED"),
                fake_ladder(T0 + 10, SLUGS[0])]
        out = build(recs, control=True, now=T0 + 6 * 3600)
        assert out["views"]["live_operation"]["lifecycle"]["state"] \
            == CC.L_COLLECTING

    def test_an_open_gap_IS_a_broken_feed(self):
        """The worker's own liveness detector is the evidence, not
        recency."""
        recs = [fake_epoch(T0, "RUN_STARTED"),
                fake_ladder(T0 + 10, SLUGS[0]),
                fake_gap(T0 + 20, "GAP_OPENED", why="NO_FRAMES_60S")]
        h = CC.connection_health(recs, now=T0 + 100, control=True)
        assert h["verdict"] == CC.H_DISCONNECTED
        out = build(recs, control=True, now=T0 + 100)
        assert out["views"]["live_operation"]["lifecycle"]["state"] \
            == CC.L_INTERRUPTED

    def test_a_gap_that_closed_is_not_still_open(self):
        """THE REGRESSION THIS PINS. GAP_OPENED carries no `to`, so a
        naive 'to is None' test finds every gap the run ever recovered
        from and pins the page at INTERRUPTED for the rest of the day."""
        recs = [fake_epoch(T0, "RUN_STARTED"),
                fake_ladder(T0 + 10, SLUGS[0]),
                fake_gap(T0 + 20, "GAP_OPENED", why="NO_FRAMES_60S"),
                fake_gap(T0 + 80, "GAP_CLOSED", why="NO_FRAMES_60S",
                         frm=T0 + 20, to=T0 + 80, dur=60.0),
                fake_ladder(T0 + 90, SLUGS[1])]
        gv = CC.gap_view(recs)
        assert gv["n_open"] == 0
        assert gv["n_closed"] == 1
        assert gv["unobserved_s"] == 60.0
        out = build(recs, control=True, now=T0 + 120)
        assert out["views"]["live_operation"]["lifecycle"]["state"] \
            == CC.L_COLLECTING


# ═══ THE REQUIRED STATES ═════════════════════════════════════════════

class TestLifecycleStates:

    def test_stopped_control_false_with_frames(self):
        recs = [fake_ladder(T0 + 10, SLUGS[0])]
        out = build(recs, control=False)
        assert out["views"]["live_operation"]["lifecycle"]["state"] \
            == CC.L_STOPPED

    def test_scheduled_nothing_armed_nothing_running(self):
        out = build([], control=False, probe=None)
        assert out["views"]["live_operation"]["lifecycle"]["state"] \
            == CC.L_SCHEDULED

    def test_armed_but_not_started(self):
        out = build([], control=False, probe=fake_probe(armed=True))
        assert out["views"]["live_operation"]["lifecycle"]["state"] \
            == CC.L_ARMED

    def test_active_collection(self):
        recs = [fake_epoch(T0, "RUN_STARTED")] + [
            fake_ladder(T0 + i, SLUGS[i % 12]) for i in range(1, 60)]
        out = build(recs, control=True, now=T0 + 61)
        live = out["views"]["live_operation"]
        assert live["lifecycle"]["state"] == CC.L_COLLECTING
        assert live["health"]["verdict"] == CC.H_LIVE_ACTIVE
        assert live["frames"]["total"]["value"] == 59

    def test_completed_run_close_plus_frames(self):
        recs = [fake_ladder(T0 + 10, SLUGS[0]),
                {"boot_id": "bootA", "at": T_END, "kind": "RUN_CLOSE",
                 "epoch": None, "slug": None,
                 "payload": {"synthetic": True, "why": "WINDOW_END"}}]
        out = build(recs, control=False, now=T_END + 1)
        assert out["views"]["live_operation"]["lifecycle"]["state"] \
            == CC.L_COMPLETED

    def test_a_close_followed_by_frames_is_not_completed(self):
        """THE REGRESSION THIS PINS, found by reconciling against the
        real run. A replaced boot writes RUN_CLOSE on its way out; the
        next boot reopens and keeps collecting. Reading any RUN_CLOSE as
        terminal reported a LIVE run as COMPLETED -- which says "nothing
        more is owed" about a run that still has to be stopped at its
        fixed end."""
        recs = [fake_ladder(T0 + 10, SLUGS[0], boot="bootA"),
                {"boot_id": "bootA", "at": T0 + 30, "kind": "RUN_CLOSE",
                 "epoch": None, "slug": None,
                 "payload": {"synthetic": True,
                             "why": "STOPPED_BY_CONTROL"}},
                fake_ladder(T0 + 200, SLUGS[1], boot="bootB"),
                fake_ladder(T0 + 300, SLUGS[2], boot="bootB")]
        out = build(recs, control=True, now=T0 + 320)
        lc = out["views"]["live_operation"]["lifecycle"]
        assert lc["state"] == CC.L_COLLECTING
        assert lc["state"] != CC.L_COMPLETED

    def test_a_close_at_the_fixed_end_with_nothing_after_it_IS_completed(
            self):
        """The ONE shape that earns COMPLETED: the latest boot closed,
        it closed at or after the fixed end, the close was not an
        interruption, and nothing followed it."""
        recs = [fake_ladder(T0 + 10, SLUGS[0]),
                {"boot_id": "bootA", "at": T_END, "kind": "RUN_CLOSE",
                 "epoch": None, "slug": None,
                 "payload": {"synthetic": True, "why": "WINDOW_END"}}]
        out = build(recs, control=False, now=T_END + 100)
        assert out["views"]["live_operation"]["lifecycle"]["state"] \
            == CC.L_COMPLETED

    # ── THE TWO SHAPES THE BRIEF NAMES EXPLICITLY ────────────────────

    def test_restart_before_first_frame_is_not_completed(self):
        """A CLOSE FOLLOWED BY NO FRAME DOES NOT PROVE COMPLETION.

        bootA collected and was replaced. bootB has opened, subscribed
        and persisted NOTHING. The last record in the whole journal is
        bootA's close -- which is exactly the shape of a finished run,
        and is not one. The run is waiting to resume and still owes
        collection up to its fixed end."""
        recs = [
            fake_ladder(T0 + 10, SLUGS[0], boot="bootA"),
            fake_ladder(T0 + 20, SLUGS[1], boot="bootA"),
            {"boot_id": "bootA", "at": T0 + 30, "kind": "RUN_CLOSE",
             "epoch": None, "slug": None,
             "payload": {"synthetic": True, "why": "STOPPED_BY_CONTROL",
                         "frames": 2}},
            # bootB opens AFTER the close and produces no ladder.
            fake_gap(T0 + 300, "BOOT_GAP", why="PROCESS_REPLACED",
                     frm=T0 + 30, to=T0 + 300, dur=270.0, boot="bootB"),
            fake_epoch(T0 + 301, "RUN_STARTED", boot="bootB", subscribed=12),
        ]
        lb = CC.latest_boot(recs)
        assert lb["boot_id"] == "bootB"
        assert lb["frames"] == 0
        assert lb["closed"] is None

        out = build(recs, control=True, now=T0 + 400)
        lc = out["views"]["live_operation"]["lifecycle"]
        assert lc["state"] == CC.L_ARMED_NO_FRAMES
        assert lc["state"] != CC.L_COMPLETED
        assert lc["state"] != CC.L_COLLECTING
        assert "bootB" in lc["why"]

    def test_a_stopped_partial_run_is_partial_not_complete(self):
        """A run stopped BEFORE the fixed end is a PARTIAL run. The
        close is clean, the control is down, nothing followed -- and it
        is still not COMPLETED, because the window had hours left."""
        recs = [fake_ladder(T0 + 10, SLUGS[0], boot="bootA"),
                fake_ladder(T0 + 600, SLUGS[1], boot="bootA"),
                {"boot_id": "bootA", "at": T0 + 900, "kind": "RUN_CLOSE",
                 "epoch": None, "slug": None,
                 "payload": {"synthetic": True, "why": "STOPPED_BY_CONTROL",
                             "frames": 2}}]
        out = build(recs, control=False, now=T0 + 1200)
        lc = out["views"]["live_operation"]["lifecycle"]
        assert lc["state"] == CC.L_STOPPED
        assert lc["state"] != CC.L_COMPLETED
        assert "PARTIAL" in lc["why"]

    def test_an_interrupted_boot_is_not_a_completed_run(self):
        """The close reason decides. A boot that ended because it was
        replaced did not finish the job, whatever the clock says."""
        recs = [fake_ladder(T0 + 10, SLUGS[0], boot="bootA"),
                {"boot_id": "bootA", "at": T_END + 5, "kind": "RUN_CLOSE",
                 "epoch": None, "slug": None,
                 "payload": {"synthetic": True, "why": "PROCESS_REPLACED"}}]
        out = build(recs, control=False, now=T_END + 60)
        lc = out["views"]["live_operation"]["lifecycle"]
        assert lc["state"] == CC.L_INTERRUPTED
        assert lc["state"] != CC.L_COMPLETED

    def test_failed_run_error(self):
        recs = [fake_ladder(T0 + 10, SLUGS[0]),
                fake_gap(T0 + 20, "RUN_ERROR", why="RUN_ERROR",
                         detail="ConnectionResetError")]
        out = build(recs, control=True, now=T0 + 30)
        lc = out["views"]["live_operation"]["lifecycle"]
        assert lc["state"] == CC.L_FAILED
        assert "ConnectionResetError" in lc["why"]

    def test_failure_is_terminal_and_read_before_the_control(self):
        """A dead run with the flag still true is FAILED, not COLLECTING."""
        recs = [fake_ladder(T0 + 10, SLUGS[i]) for i in range(12)] + [
            fake_gap(T0 + 20, "RUN_ERROR", why="RUN_ERROR", detail="boom")]
        out = build(recs, control=True, now=T0 + 30)
        assert out["views"]["live_operation"]["lifecycle"]["state"] \
            == CC.L_FAILED

    def test_past_the_fixed_end_with_the_control_still_true(self):
        recs = [fake_ladder(T0 + 10, SLUGS[0])]
        out = build(recs, control=True, now=T_END + 60)
        lc = out["views"]["live_operation"]["lifecycle"]
        assert lc["state"] == CC.L_STOPPED
        assert "stop has not been applied" in lc["why"]


class TestRestart:

    def test_two_boots_are_visible_and_the_boot_gap_is_counted(self):
        recs = [
            fake_epoch(T0, "RUN_STARTED", boot="bootA"),
            fake_ladder(T0 + 10, SLUGS[0], boot="bootA"),
            # The gap open() writes before anything else on a resume.
            fake_gap(T0 + 300, "BOOT_GAP", why="PROCESS_REPLACED",
                     frm=T0 + 10, to=T0 + 300, dur=290.0, boot="bootB"),
            fake_epoch(T0 + 301, "RUN_STARTED", boot="bootB"),
            fake_ladder(T0 + 310, SLUGS[1], boot="bootB"),
        ]
        out = build(recs, control=True, now=T0 + 320)
        live = out["views"]["live_operation"]
        assert live["identity"]["boot_count"]["value"] == 2
        assert set(live["identity"]["boots"]["value"]) == {"bootA", "bootB"}
        assert live["gaps"]["unobserved_s"] == 290.0
        assert live["gaps"]["n_open"] == 0
        assert live["lifecycle"]["state"] == CC.L_COLLECTING

    def test_epoch_one_of_two_boots_are_two_segments_not_one(self):
        """The bare epoch number restarts at 1 in each process. If the
        two were pooled the outage would read as a quiet book."""
        recs = [
            fake_ladder(T0 + 10, SLUGS[0], boot="bootA", epoch=1),
            fake_ladder(T0 + 20, SLUGS[0], boot="bootA", epoch=1),
            fake_ladder(T0 + 310, SLUGS[1], boot="bootB", epoch=1),
        ]
        out = build(recs, control=True, now=T0 + 320)
        segs = out["views"]["live_operation"]["segments"]
        assert segs["known"] is True
        assert segs["n"] == 2


class TestAllowance:

    def test_usage_is_shown_against_its_limit(self):
        out = build([], probe=fake_probe(socket_connect_reserved=3))
        al = out["views"]["live_operation"]["allowance"]
        assert al["socket_connect"]["value"] == {
            "used": 3, "limit": 20, "remaining": 17, "exhausted": False}

    def test_exhausted_is_named(self):
        out = build([], probe=fake_probe(socket_connect_reserved=20,
                                         socket_subscribe_reserved=40))
        al = out["views"]["live_operation"]["allowance"]
        assert al["socket_connect"]["value"]["exhausted"] is True
        assert al["socket_connect"]["value"]["remaining"] == 0
        assert al["socket_subscribe"]["value"]["exhausted"] is True

    def test_http_sums_the_three_incentive_sub_caps(self):
        out = build([], probe=fake_probe(incentive_manifest_reserved=4,
                                         incentive_recheck_reserved=1,
                                         incentive_retry_reserved=0))
        al = out["views"]["live_operation"]["allowance"]
        assert al["http"]["value"] == {"used": 5, "limit": 8,
                                       "remaining": 3, "exhausted": False}

    def test_a_missing_counter_is_unknown_not_zero(self):
        p = fake_probe()
        p.pop("socket_connect_reserved")
        out = build([], probe=p)
        al = out["views"]["live_operation"]["allowance"]
        assert al["socket_connect"]["status"] == CC.UNKNOWN
        assert al["socket_connect"]["value"] is None

    def test_a_non_incentive_probe_row_is_flagged(self):
        """max_distinct != 0 means this is the general discovery arm's
        row, not the incentive arm's."""
        out = build([], probe=fake_probe(max_distinct=40))
        al = out["views"]["live_operation"]["allowance"]
        assert al["general_loop_zeroed"]["value"] is False

    def test_nothing_armed_says_so(self):
        out = build([], probe=None)
        assert out["views"]["live_operation"]["allowance"]["known"] is False


class TestCoverage:

    def test_partial_coverage_is_partial(self):
        recs = [fake_ladder(T0 + 10, s) for s in SLUGS[:5]]
        out = build(recs, control=True, now=T0 + 60)
        cov = out["views"]["live_operation"]["coverage"]
        assert cov["allowlisted"] == 12
        assert cov["receiving"] == 5
        assert cov["with_depth"] == 5
        assert sum(1 for m in cov["markets"] if not m["receiving"]) == 7

    def test_a_frame_without_depth_is_counted_apart(self):
        recs = [fake_ladder(T0 + 10, SLUGS[0], levels=0),
                fake_ladder(T0 + 11, SLUGS[1], levels=3)]
        out = build(recs, control=True, now=T0 + 60)
        cov = out["views"]["live_operation"]["coverage"]
        assert cov["receiving"] == 2
        assert cov["with_depth"] == 1

    def test_twelve_markets_are_not_twelve_observations(self):
        out = build([], control=True)
        cov = out["views"]["live_operation"]["coverage"]
        assert "not independent" in cov["independence_note"].lower()


class TestWatchedIsNotObserved:
    """A SUBSCRIBED MARKET WITH NO BOOK IS UNOBSERVED, NOT QUIET.

    "Nothing has changed" presupposes a book to change FROM. Before a
    market's first full snapshot in the current connection epoch there
    is none, so that stretch is time nobody observed -- and calling it
    quiet is how a market that never arrived gets counted as covered.
    """

    def _iv(self, out):
        return out["views"]["live_operation"]["coverage_time"]

    def test_a_market_with_no_snapshot_is_unobserved_not_quiet(self):
        # Three markets arrive; the other nine are subscribed and send
        # nothing at all. The segment runs T0+10 .. T0+600.
        recs = [fake_ladder(T0 + 10, SLUGS[i]) for i in range(3)]
        recs.append(fake_ladder(T0 + 600, SLUGS[0]))
        out = build(recs, control=True, now=T0 + 700)
        iv = self._iv(out)
        by = {m["slug"]: m for m in iv["per_market"]}

        arrived, never = by[SLUGS[0]], by[SLUGS[11]]
        assert arrived["observed_s"] == 590.0
        assert arrived["never_observed"] is False

        assert never["observed_s"] == 0.0
        assert never["never_observed"] is True
        assert never["epochs_with_snapshot"] == 0
        # The whole segment is time that market was watched, not observed.
        assert never["awaiting_first_snapshot_s"] == 590.0

    def test_one_market_without_a_book_makes_the_instant_unusable(self):
        """The reward is scored across the board, so the run figure is
        the time EVERY allowlisted market held a valid book -- which is
        zero while one of them has never sent a snapshot."""
        recs = [fake_ladder(T0 + 10, SLUGS[i]) for i in range(11)]
        recs.append(fake_ladder(T0 + 600, SLUGS[0]))
        out = build(recs, control=True, now=T0 + 700)
        iv = self._iv(out)
        assert iv["achieved"]["value"]["observed_s"] == 0.0
        # The socket was up the whole time, and that is reported apart.
        assert iv["feed_up_s"] == 590.0
        assert iv["observed_best_market_s"] == 590.0

    def test_coverage_begins_at_the_first_snapshot_not_the_segment(self):
        recs = [fake_ladder(T0 + 10, s) for s in SLUGS[:11]] + [
            fake_ladder(T0 + 310, SLUGS[11]),            # late arrival
            fake_ladder(T0 + 610, SLUGS[0])]
        out = build(recs, control=True, now=T0 + 700)
        by = {m["slug"]: m for m in self._iv(out)["per_market"]}
        assert by[SLUGS[0]]["observed_s"] == 600.0
        assert by[SLUGS[11]]["observed_s"] == 300.0
        assert by[SLUGS[11]]["awaiting_first_snapshot_s"] == 300.0
        # Every market valid only from the latest arrival onwards.
        assert self._iv(out)["achieved"]["value"]["observed_s"] == 300.0

    def test_an_update_alone_does_not_establish_a_book(self):
        """A delta against a snapshot this process never saw is not a
        book. Only an INITIAL_LADDER starts the clock."""
        recs = [fake_ladder(T0 + 10, s, ladder_class="INITIAL_LADDER")
                for s in SLUGS[:11]] + [
            fake_ladder(T0 + 20, SLUGS[11], ladder_class="UPDATE"),
            fake_ladder(T0 + 600, SLUGS[0], ladder_class="UPDATE")]
        out = build(recs, control=True, now=T0 + 700)
        by = {m["slug"]: m for m in self._iv(out)["per_market"]}
        stray = by[SLUGS[11]]
        assert stray["frames"] == 1           # it IS receiving
        assert stray["observed_s"] == 0.0     # and it is NOT observed
        assert stray["watched_but_silent"] is False
        assert stray["never_observed"] is True

    def test_a_reconnect_voids_the_book_until_it_is_rebuilt(self):
        """`MarketStream.epoch` counts connections and the journal
        forbids carrying a ladder across one. So the second epoch's
        clock starts at that epoch's OWN snapshot, and a market that
        did not re-send one is unobserved for the whole of it."""
        recs = []
        for s in SLUGS:                                  # epoch 1: all 12
            recs.append(fake_ladder(T0 + 10, s, epoch=1,
                                    ladder_class="INITIAL_LADDER"))
        recs.append(fake_ladder(T0 + 300, SLUGS[0], epoch=1,
                                ladder_class="UPDATE"))
        # A reconnect. Epoch 2 re-snapshots eleven markets, not the 12th.
        for s in SLUGS[:11]:
            recs.append(fake_ladder(T0 + 400, s, epoch=2,
                                    ladder_class="INITIAL_LADDER"))
        recs.append(fake_ladder(T0 + 900, SLUGS[0], epoch=2,
                                ladder_class="UPDATE"))
        out = build(recs, control=True, now=T0 + 1000)
        iv = self._iv(out)
        by = {m["slug"]: m for m in iv["per_market"]}

        assert iv["segments"] == [
            {"start": CC.iso(T0 + 10), "end": CC.iso(T0 + 300),
             "seconds": 290.0},
            {"start": CC.iso(T0 + 400), "end": CC.iso(T0 + 900),
             "seconds": 500.0}]

        rebuilt = by[SLUGS[0]]
        assert rebuilt["epochs_with_snapshot"] == 2
        assert rebuilt["observed_s"] == 790.0

        stale = by[SLUGS[11]]
        assert stale["epochs_with_snapshot"] == 1
        # Epoch 1 only. The 500s of epoch 2 are NOT carried over.
        assert stale["observed_s"] == 290.0
        assert stale["awaiting_first_snapshot_s"] == 500.0

        # And the run figure stops at the reconnect for the same reason.
        assert iv["achieved"]["value"]["observed_s"] == 290.0

    def test_the_page_says_watched_is_not_observed_in_words(self):
        out = build([fake_ladder(T0 + 10, SLUGS[0])], control=True,
                    now=T0 + 60)
        note = self._iv(out)["watched_is_not_observed"]
        assert "UNOBSERVED" in note
        assert "reconnect" in note.lower()


class TestEarlyVersusWindow:

    def test_early_frames_are_excluded_from_the_window(self):
        recs = [fake_ladder(T0 - 1800, SLUGS[0]),     # early, operational
                fake_ladder(T0 - 900, SLUGS[1]),      # early, operational
                fake_ladder(T0 + 10, SLUGS[2])]       # in the window
        out = build(recs, control=True, now=T0 + 60)
        per = out["views"]["live_operation"]["periods"]
        assert per["early"]["frames"] == 2
        assert per["measurement"]["frames"] == 1
        assert per["early"]["counts_toward_measurement"] is False

    def test_window_coverage_counts_only_window_frames(self):
        recs = [fake_ladder(T0 - 1800, s) for s in SLUGS]   # all 12, EARLY
        recs.append(fake_ladder(T0 + 10, SLUGS[0]))          # one in-window
        out = build(recs, control=True, now=T0 + 60)
        live = out["views"]["live_operation"]
        assert live["coverage"]["with_depth"] == 1
        assert live["early_coverage"]["with_depth"] == 12

    def test_the_boundary_is_half_open(self):
        recs = [fake_ladder(T0, SLUGS[0]), fake_ladder(T_END, SLUGS[1])]
        per = CC.split_periods(recs)
        assert per["measurement"]["frames"] == 1     # T0 is in
        assert per["after_end"]["frames"] == 1       # T_END is out


# ═══ VIEW 2: TEST EVIDENCE ═══════════════════════════════════════════

class TestSuiteEvidence:

    def test_an_incomplete_run_reports_no_counts(self):
        row = CC.suite_row(name="s", kind=CC.K_UNIT, sha="abc",
                           environment="ci", at=None, complete=False,
                           passed=200, failed=0, skipped=0)
        assert row["passed"]["status"] == CC.UNKNOWN
        assert row["passed"]["value"] is None
        assert "prefix" in row["why_no_counts"]

    def test_a_complete_run_carries_its_sha_and_environment(self):
        row = CC.suite_row(name="s", kind=CC.K_UNIT, sha="d630d3d",
                           environment="ci py3.12", at="2026-09-21T18:58Z",
                           complete=True, passed=264, failed=0, skipped=0)
        assert row["tested_sha"] == "d630d3d"
        assert row["environment"] == "ci py3.12"
        assert row["passed"]["value"] == 264
        assert row["passed"]["as_of"] == "2026-09-21T18:58Z"

    def test_failed_tests_are_shown_with_their_ids(self):
        row = CC.suite_row(name="gate", kind=CC.K_SIM, sha="x",
                           environment="ci", at="t", complete=True,
                           passed=13, failed=13, skipped=0,
                           failure_ids=["b::2", "a::1"])
        assert row["failed"]["value"] == 13
        assert row["failure_ids"] == ["a::1", "b::2"]

    def test_superseded_evidence_names_its_replacement(self):
        row = CC.suite_row(name="repair", kind=CC.K_UNIT, sha="195ee01",
                           environment="ci", at="t", complete=True,
                           passed=217, failed=0, skipped=0,
                           superseded_by="release suite @ d630d3d")
        assert row["superseded_by"] == "release suite @ d630d3d"

    def test_the_four_kinds_stay_distinct(self):
        assert len({CC.K_UNIT, CC.K_SIM, CC.K_REPLAY, CC.K_VENUE}) == 4


class TestBaselineComparison:

    def test_both_shas_are_shown(self):
        cand = CC.suite_row(name="c", kind=CC.K_UNIT, sha="1aa2ce9",
                            environment="ci", at="t", complete=True,
                            passed=10, failed=1, skipped=0,
                            failure_ids=["x::1"])
        base = CC.suite_row(name="b", kind=CC.K_UNIT, sha="e8f616a",
                            environment="ci", at="t", complete=True,
                            passed=11, failed=0, skipped=0, failure_ids=[])
        cand["selection"] = base["selection"] = "tests/"
        cmp = CC.baseline_compare(candidate=cand, baseline=base)
        assert cmp["candidate_sha"] == "1aa2ce9"
        assert cmp["baseline_sha"] == "e8f616a"
        assert cmp["new_on_candidate"] == ["x::1"]
        assert cmp["unattributed"] == []

    def test_an_incomplete_baseline_makes_everything_unattributed(self):
        cand = CC.suite_row(name="c", kind=CC.K_UNIT, sha="a",
                            environment="ci", at="t", complete=True,
                            passed=1, failed=1, skipped=0,
                            failure_ids=["x::1"])
        base = CC.suite_row(name="b", kind=CC.K_UNIT, sha="b",
                            environment="ci", at=None, complete=False)
        cmp = CC.baseline_compare(candidate=cand, baseline=base)
        assert cmp["comparable"] is False
        assert cmp["unattributed"] == ["x::1"]

    def test_a_different_selection_makes_the_difference_unattributed(self):
        cand = CC.suite_row(name="c", kind=CC.K_UNIT, sha="a",
                            environment="ci", at="t", complete=True,
                            passed=1, failed=1, skipped=0,
                            failure_ids=["x::1"])
        base = CC.suite_row(name="b", kind=CC.K_UNIT, sha="b",
                            environment="ci", at="t", complete=True,
                            passed=1, failed=0, skipped=0, failure_ids=[])
        cand["selection"] = "tests/"
        base["selection"] = "tests/test_one.py"
        cmp = CC.baseline_compare(candidate=cand, baseline=base)
        assert cmp["same_selection"] is False
        assert cmp["unattributed"] == ["x::1"]
        assert "UNATTRIBUTED" in cmp["note"]


class TestJUnitParsing:

    def test_a_real_artifact_parses_with_its_counts(self, tmp_path):
        p = tmp_path / "s.xml"
        p.write_text(
            '<?xml version="1.0"?><testsuites><testsuite name="pytest" '
            'errors="0" failures="1" skipped="2" tests="10" '
            'timestamp="2026-09-21T18:58:24+00:00">'
            '<testcase classname="t.A" name="ok" time="0.1"/>'
            '<testcase classname="t.A" name="bad" time="0.1">'
            '<failure message="assert 1 == 2">tb</failure></testcase>'
            '</testsuite></testsuites>')
        row = IO.parse_junit(str(p), name="s", kind=CC.K_UNIT, sha="abc",
                             environment="ci")
        assert row["complete"] is True
        assert row["total"] == 10
        assert row["failed"]["value"] == 1
        assert row["skipped"]["value"] == 2
        assert row["passed"]["value"] == 7
        assert row["failure_ids"] == ["t.A::bad"]
        assert row["failures"][0]["message"] == "assert 1 == 2"

    def test_a_missing_artifact_is_an_incomplete_run(self, tmp_path):
        row = IO.parse_junit(str(tmp_path / "nope.xml"), name="s",
                             kind=CC.K_UNIT, sha="abc", environment="ci")
        assert row["complete"] is False
        assert row["passed"]["status"] == CC.UNKNOWN

    def test_a_truncated_artifact_is_an_incomplete_run(self, tmp_path):
        p = tmp_path / "t.xml"
        p.write_text('<?xml version="1.0"?><testsuites><testsuite tests="9"')
        row = IO.parse_junit(str(p), name="s", kind=CC.K_UNIT, sha="abc",
                             environment="ci")
        assert row["complete"] is False
        assert row["failed"]["value"] is None

    def test_the_gate_run_against_defective_code_shows_its_failures(self):
        """13 failures that are the POINT. The row must not hide them and
        must not present them as a broken release."""
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        path = os.path.join(root, "research/beta48/acceptance",
                            "gate_tests_against_defective_code.xml")
        if not os.path.exists(path):
            pytest.skip("evidence artifact not present in this tree")
        row = IO.parse_junit(path, name="gate", kind=CC.K_SIM,
                             sha="195ee01+defects",
                             environment="deliberately defective code")
        assert row["complete"] is True
        assert row["failed"]["value"] == 13
        assert len(row["failure_ids"]) == 13
        assert row["kind"] == CC.K_SIM


# ═══ VIEW 3: ECONOMICS ═══════════════════════════════════════════════

class TestEconomics:

    def test_the_four_buckets_exist_and_do_not_sum(self):
        econ = CC.economics({})
        assert set(econ["buckets"]) == {CC.M_REALIZED, CC.M_REPLAY,
                                        CC.M_HYPOTHETICAL, CC.M_CONFIRMED}
        assert "total" not in econ
        assert not any(k.startswith("sum") for k in econ)

    def test_realized_pnl_is_not_applicable_not_zero(self):
        econ = CC.economics({})
        v = econ["buckets"][CC.M_REALIZED]["value"]
        assert v["status"] == CC.NOT_APPLICABLE
        assert v["value"] is None
        assert v["value"] != 0

    def test_venue_confirmed_reward_is_unknown_not_zero(self):
        econ = CC.economics({})
        v = econ["buckets"][CC.M_CONFIRMED]["value"]
        assert v["status"] == CC.UNKNOWN
        assert v["value"] is None

    def test_a_hypothetical_reward_never_claims_to_be_earned(self):
        econ = CC.economics({"opportunity": {"rows": [{"share": 0.07}],
                                             "terms_source": "manifest"}})
        b = econ["buckets"][CC.M_HYPOTHETICAL]
        assert "no order was placed" in b["execution_assumptions"]
        assert b["kind"] != CC.M_CONFIRMED

    def test_a_missing_denominator_is_unknown(self):
        b = CC.economic_bucket(CC.M_REPLAY, value=1.0, source="x")
        assert b["denominator"]["status"] == CC.UNKNOWN
        assert b["denominator"]["value"] is None

    def test_replay_carries_its_policy_dataset_and_event_count(self):
        ev = {"cut": "2026-09-17T00:00:00+00:00", "selected": "R7",
              "qualifies": False,
              "provenance": {"independent_holdout": False,
                             "status_of_every_figure_here":
                                 "DEVELOPMENT DIAGNOSTIC"},
              "eval": [{"qfrac": 0.25, "net_usd": -74.0, "events": 5,
                        "capital_hours": 3696.0, "taker_fees_usd": -24.98,
                        "rebates_usd": 6.33, "per_capital_hour": -0.02}]}
        econ = CC.economics({"evaluation": ev})
        b = econ["buckets"][CC.M_REPLAY]
        assert b["policy_version"] == "R7"
        assert b["dataset"] == "2026-09-17T00:00:00+00:00"
        assert b["events"]["value"] == 5
        assert b["provenance"]["independent_holdout"] is False
        assert "EVENT clusters" in b["events"]["note"]

    def test_a_missing_evaluation_artifact_is_unknown_not_zero(self):
        econ = CC.economics({})
        v = econ["buckets"][CC.M_REPLAY]["value"]
        assert v["status"] == CC.UNKNOWN
        assert v["value"] is None


# ═══ VIEW 4: CAPABILITIES ════════════════════════════════════════════

class TestCapabilities:

    def test_every_promised_capability_is_present(self):
        names = " ".join(c["capability"] for c in CC.capabilities())
        for want in ("ENTRY", "QUOTE", "SIZING", "INVENTORY", "COMPLETION",
                     "LOSS-TAKING", "HOLDING", "SETTLEMENT", "CAPITAL REUSE"):
            assert want in names

    def test_nothing_claims_live_execution_validation(self):
        assert not [c for c in CC.capabilities()
                    if c["evidence"] == CC.E_LIVE_EXECUTED]

    def test_p_fill_is_absent_and_says_observation_cannot_supply_it(self):
        row = [c for c in CC.capabilities()
               if c["capability"].startswith("FILL PROBABILITY")][0]
        assert row["evidence"] == CC.E_ABSENT
        assert "CANNOT supply" in row["venue_difference"]

    def test_every_row_names_its_strongest_evidence(self):
        for c in CC.capabilities():
            assert c["strongest_evidence"]

    def test_venue_differences_are_recorded_where_they_exist(self):
        rows = {c["capability"]: c for c in CC.capabilities()}
        assert "4.8x" in rows["COMPLETION / PAIRING"]["venue_difference"]
        assert "MERGE" in rows["CAPITAL REUSE / NETTING"]["venue_difference"]


# ═══ VIEW 5: MANAGEMENT ══════════════════════════════════════════════

class TestManagement:

    def test_health_and_profitability_are_separate_blocks(self):
        mg = build([], control=True)["views"]["management"]
        assert "system_health" in mg and "economic_qualification" in mg
        assert "money" in mg["system_health"]["caveat"].lower()
        assert "OBSERVATION DOES NOT VALIDATE TRADING" in \
            mg["economic_qualification"]["caveat"]

    def test_p_fill_is_always_a_blocker(self):
        mg = build([], control=True)["views"]["management"]
        assert any("P_FILL" in b["blocker"] for b in mg["blockers"])

    def test_every_metric_carries_a_source_and_an_as_of(self):
        mg = build([], control=True)["views"]["management"]
        for block in ("system_health", "economic_qualification"):
            for key, val in mg[block].items():
                if isinstance(val, dict) and "value" in val:
                    assert val["source"], "%s.%s has no source" % (block, key)

    def test_the_next_action_is_stated_for_each_state(self):
        for state in (CC.L_COLLECTING, CC.L_ARMED, CC.L_ARMED_NO_FRAMES,
                      CC.L_COMPLETED, CC.L_FAILED, CC.L_SCHEDULED):
            act = CC._next_action(state, T0)
            assert act["action"] and act["why"]

    def test_no_complete_suite_says_so_rather_than_showing_nothing(self):
        mg = build([], control=True, suites=[])["views"]["management"]
        assert "why" in mg["latest_verified_evidence"]

    def test_the_latest_complete_suite_is_the_one_shown(self):
        old = CC.suite_row(name="old", kind=CC.K_UNIT, sha="aaa",
                           environment="ci", at="2026-09-01T00:00:00+00:00",
                           complete=True, passed=1, failed=0, skipped=0)
        new = CC.suite_row(name="new", kind=CC.K_UNIT, sha="bbb",
                           environment="ci", at="2026-09-21T00:00:00+00:00",
                           complete=True, passed=2, failed=0, skipped=0)
        incomplete = CC.suite_row(name="newest-but-dead", kind=CC.K_UNIT,
                                  sha="ccc", environment="ci",
                                  at="2026-09-22T00:00:00+00:00",
                                  complete=False)
        mg = build([], suites=[old, new, incomplete])["views"]["management"]
        assert mg["latest_verified_evidence"]["suite"] == "new"


# ═══ THE CELL CONTRACT ═══════════════════════════════════════════════

class TestCellContract:

    def test_a_cell_cannot_be_built_without_a_source(self):
        with pytest.raises(ValueError):
            CC.cell(1, "")
        with pytest.raises(ValueError):
            CC.cell(1, None)

    def test_unknown_is_never_zero(self):
        u = CC.unknown("src", "not measured")
        assert u["value"] is None
        assert u["status"] == CC.UNKNOWN
        assert u["value"] != 0

    def test_every_cell_in_a_built_payload_has_a_source(self):
        out = build([fake_ladder(T0 + 1, SLUGS[0])], control=True)
        missing = []

        def walk(node, path):
            if isinstance(node, dict):
                if "value" in node and "status" in node and "source" in node:
                    if not node["source"]:
                        missing.append(path)
                    return
                for k, v in node.items():
                    walk(v, "%s.%s" % (path, k))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, "%s[%d]" % (path, i))

        walk(out, "")
        assert missing == []

    def test_the_payload_is_json_serialisable(self):
        out = build([fake_ladder(T0 + 1, SLUGS[0])], control=True)
        assert json.loads(json.dumps(out))["version"] == CC.VERSION


# ═══ UNAVAILABLE DATA ════════════════════════════════════════════════

class TestUnavailableData:

    def test_an_unreadable_control_refuses_rather_than_reporting_stopped(
            self, monkeypatch):
        """A control we cannot read is not a control that says stop."""
        import asyncio
        from sportsassets import bettor_live_control as CTL

        async def fake_read(pool=None):
            return {"control": "v", "key": CTL.CONTROL_KEY, "run": False,
                    "why": CTL.W_UNREADABLE, "detail": "read failed: OSError",
                    "read_at": T0, "readable": False}

        monkeypatch.setattr(CTL, "read_control", fake_read)
        with pytest.raises(IO.RetrievalIncomplete) as exc:
            asyncio.run(IO.read_control(object()))
        assert exc.value.reason == CTL.W_UNREADABLE

    def test_a_malformed_probe_row_refuses(self, monkeypatch):
        import asyncio

        class P:
            async def fetchval(self, *a):
                return "{not json"

        with pytest.raises(IO.RetrievalIncomplete) as exc:
            asyncio.run(IO.read_probe(P()))
        assert exc.value.reason == "PROBE_ROW_MALFORMED"

    def test_an_unreadable_journal_refuses(self):
        import asyncio

        class P:
            async def fetch(self, *a):
                raise OSError("no connection")

        with pytest.raises(IO.RetrievalIncomplete) as exc:
            asyncio.run(IO.read_journal(P(), "run"))
        assert exc.value.reason == "JOURNAL_UNREADABLE"

    def test_absent_artifacts_are_named_not_faked(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BETTOR_EVIDENCE_ROOT", str(tmp_path))
        arts = IO.load_artifacts()
        assert arts["present"] == {"evaluation": False, "opportunity": False,
                                   "manifest": False}
        assert "production image" in arts["why_absent"]

    def test_an_unstamped_deployment_is_unknown_not_a_working_tree_guess(
            self, monkeypatch):
        for var in ("RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_COMMIT",
                    "BETTOR_DEPLOYED_SHA"):
            monkeypatch.delenv(var, raising=False)
        import asyncio
        d = asyncio.run(IO.deployed_identity(None))
        assert d["sha"] is None
        assert "UNKNOWN rather than guessed" in d["why"]
        out = build([], deployed=d)
        ident = out["views"]["live_operation"]["identity"]["deployed_sha"]
        assert ident["status"] == CC.UNKNOWN


# ═══ READ-ONLY, AND NO SECRETS ═══════════════════════════════════════

class TestReadOnly:

    MUTATORS = ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE ",
                "ALTER ", "CREATE ", "GRANT ")

    @staticmethod
    def code_only(path):
        """Source with comments and string literals removed.

        A prose scan is not a code scan. These modules describe their own
        discipline in words -- "writes no row", "the reader is
        SELECT-only" -- and a grep over the raw file would trip on the
        sentence that promises the very thing being checked.
        """
        import io
        import tokenize

        out = []
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                out.append(tok.string)
        return " ".join(out)

    def test_the_io_module_holds_no_mutating_statement(self):
        code = self.code_only(IO.__file__).upper()
        for verb in self.MUTATORS:
            assert verb not in code, "mutating verb %r in the reader" % verb

    def test_the_io_modules_sql_is_select_only(self):
        """The SQL lives in string literals, so it is checked separately
        -- every statement in this module must begin with SELECT."""
        import ast

        tree = ast.parse(open(IO.__file__, encoding="utf-8").read())
        sql = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and any(v in n.value.upper() for v in
                       ("SELECT ", "INSERT ", "UPDATE ", "DELETE "))]
        assert sql, "expected to find the reader's SQL literals"
        for frag in sql:
            u = frag.upper().strip()
            assert u.startswith("SELECT") or " FROM " in u, frag
            for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP "):
                assert verb not in u, "%r in SQL: %s" % (verb, frag)

    def test_the_read_model_touches_no_database_at_all(self):
        code = self.code_only(CC.__file__)
        for token in ("SELECT", "get_pool", "asyncpg", "aiohttp", "requests",
                      "fetchval", "fetch", "execute", "open"):
            assert token not in code.split(), \
                "%r in the pure read model" % token

    def test_no_credential_shaped_key_reaches_the_payload(self):
        out = build([fake_ladder(T0 + 1, SLUGS[0])], control=True,
                    probe=fake_probe(api_key="SHOULD-NOT-APPEAR",
                                     private_key="SHOULD-NOT-APPEAR"))
        blob = json.dumps(out)
        assert "SHOULD-NOT-APPEAR" not in blob

    def test_the_probe_row_is_projected_not_dumped(self):
        """The allowance view must read named counters, never echo the
        whole row -- an echoed row carries whatever else is in it."""
        out = build([], probe=fake_probe(secret_token="LEAK-ME"))
        assert "LEAK-ME" not in json.dumps(
            out["views"]["live_operation"]["allowance"])
