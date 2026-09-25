"""COORDINATION USES THE EXISTING CENSUS, not a second one.

The shortcut it replaces walked `?status=in_progress` and `?status=queued`
and decided membership by a `beta48-` name prefix. Both halves were
wrong in the same direction -- towards reading an occupied domain as an
idle one -- and the collector they would have missed is the one that was
actually executing when this was written: run85-phase2-capture, a member
of pmus-public-read-global with no beta48 prefix.
"""

from __future__ import annotations

import pytest

from sportsassets import calibration_domain as cd

from .conftest import repo_path

#: The reservation workflow, anchored to the repository root.
_WF = repo_path(".github", "workflows", "calibration-evidence.yml")

REPO = "matthewtaylor141-lab/SportsAssets"


def pages(rows_by_workflow):
    """A fetch() over per-workflow run histories, paged like the API."""
    def fetch(url):
        wf = url.split("/workflows/")[1].split("/runs")[0]
        rows = list(rows_by_workflow.get(wf, []))
        return {"workflow_runs": rows, "total_count": len(rows)}
    return fetch


def run(**over):
    r = {"id": 1, "name": "beta48-substantive-capture", "status": "completed"}
    r.update(over)
    return r


class TestTheInventoryIsDiscoveredNotPrefixed:
    def test_it_comes_from_the_workflow_files(self):
        got = cd.inventory()
        assert "run85-phase2-capture" in got
        assert "beta48-substantive-capture" in got
        assert len(got) >= 19

    def test_the_prefix_shortcut_would_have_missed_real_members(self):
        """This is the defect, stated as a number: members that do not
        start with beta48- and are venue-touching all the same."""
        got = cd.inventory()
        missed = [w for w in got if not w.startswith("beta48-")]
        assert "run85-phase2-capture" in missed
        assert len(missed) >= 10, missed

    def test_an_empty_inventory_is_unknown_never_clear(self, monkeypatch):
        monkeypatch.setattr(cd, "inventory", lambda root=None: [])
        got = cd.domain_state(fetch=pages({}), repo=REPO)
        assert got["STATE"] == cd.UNKNOWN
        assert got["REASON"] == "EMPTY_INVENTORY"


class TestTheFourVerifications:
    """The checks management named, each against the real machinery."""

    def test_an_executing_run85_blocks(self):
        got = cd.domain_state(
            fetch=pages({"run85-phase2-capture": [
                run(id=35366308627, name="run85-phase2-capture",
                    status="in_progress")]}),
            repo=REPO)
        assert got["STATE"] == cd.ACTIVE
        assert got["running"] == [{"workflow": "run85-phase2-capture",
                                   "id": 35366308627}]

    @pytest.mark.parametrize("status", ["queued", "pending", "requested",
                                        "waiting"])
    def test_a_concurrency_held_pending_member_is_not_invisible(self, status):
        """`?status=queued` is one filter over four waiting states. The
        walk takes the whole history and lets the census classify it, so
        a member held in any of them is seen."""
        got = cd.domain_state(
            fetch=pages({"beta48-forward-capture": [
                run(id=7, name="beta48-forward-capture", status=status)]}),
            repo=REPO)
        assert got["STATE"] == cd.ACTIVE, status
        assert got["waiting"][0]["workflow"] == "beta48-forward-capture"

    def test_an_incomplete_census_blocks(self):
        def short(url):
            # a full final page: there may be another, so the walk is
            # unresolved and the census cannot be certified
            wf = url.split("/workflows/")[1].split("/runs")[0]
            if "substantive" in wf:
                return {"workflow_runs": [run(id=i) for i in range(100)],
                        "total_count": 500}
            return {"workflow_runs": [], "total_count": 0}
        got = cd.domain_state(fetch=short, repo=REPO)
        assert got["STATE"] == cd.UNKNOWN
        assert got["REASON"] == "CENSUS_INCOMPLETE"

    def test_an_unattributable_occupying_run_blocks(self):
        got = cd.domain_state(
            fetch=pages({"beta48-substantive-capture": [
                {"id": 9, "status": "in_progress"}]}),      # no name
            repo=REPO)
        assert got["STATE"] != cd.CLEAR

    def test_a_failed_read_is_unknown_not_clear(self):
        def boom(_url):
            raise RuntimeError("github 500")
        got = cd.domain_state(fetch=boom, repo=REPO)
        assert got["STATE"] == cd.UNKNOWN

    def test_no_token_is_unknown_not_clear(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        got = cd.domain_state()
        assert got["STATE"] == cd.UNKNOWN
        assert got["REASON"] == "NO_REPO_OR_TOKEN"

    def test_a_quiet_domain_is_clear(self):
        got = cd.domain_state(fetch=pages({}), repo=REPO)
        assert got["STATE"] == cd.CLEAR
        assert got["running"] == [] and got["waiting"] == []

    def test_a_terminal_run_does_not_block(self):
        got = cd.domain_state(
            fetch=pages({"run85-phase2-capture": [
                run(name="run85-phase2-capture", status="completed")]}),
            repo=REPO)
        assert got["STATE"] == cd.CLEAR


SELF = {cd.RESERVATION_ENV: "pmus-public-read-global",
        cd.RUN_ID_ENV: "999", cd.WORKFLOW_ENV: "calibration-evidence"}


def me(status="in_progress"):
    return run(id=999, name="calibration-evidence", status=status)


class TestAnEnvVarIsNotOwnership:
    def test_the_marker_is_only_a_marker(self):
        got = cd.reservation_state(SELF)
        assert got["MARKER_PRESENT"] is True
        assert "not evidence that the job holds the slot" in got["note"]
        assert "RESERVATION_HELD" not in got

    def test_a_missing_marker_blocks(self):
        got = cd.coordination(fetch=pages({}), repo=REPO, env={})
        assert got["blockers"] == [cd.B_NO_RESERVATION]
        assert got["MAY_READ_THE_VENUE"] is False

    def test_the_marker_alone_does_not_prove_ownership(self):
        """A clear census and the env var, but our own run is nowhere in
        the census -- so we cannot show we hold the slot."""
        got = cd.coordination(fetch=pages({}), repo=REPO, env=SELF)
        assert got["ownership"]["OWNED"] is False
        assert got["blockers"] == [cd.B_OWNERSHIP]

    def test_ownership_needs_our_run_EXECUTING(self):
        got = cd.coordination(
            fetch=pages({"calibration-evidence": [me("queued")]}),
            repo=REPO, env=SELF)
        assert got["ownership"]["OWNED"] is False
        assert cd.B_OWNERSHIP in got["blockers"]

    def test_our_run_executing_in_the_group_is_ownership(self):
        got = cd.coordination(
            fetch=pages({"calibration-evidence": [me()]}),
            repo=REPO, env=SELF)
        assert got["ownership"]["OWNED"] is True, got["ownership"]
        assert got["blockers"] == []
        assert got["MAY_READ_THE_VENUE"] is True

    def test_a_workflow_outside_the_group_cannot_own_the_slot(self):
        env = dict(SELF, **{cd.WORKFLOW_ENV: "some-other-workflow"})
        got = cd.coordination(
            fetch=pages({"calibration-evidence": [me()]}),
            repo=REPO, env=env)
        assert got["ownership"]["OWNED"] is False


class TestWaitingFollowersAreReportedNotTreatedAsCompetition:
    def test_a_waiter_does_not_block_a_run_that_owns_the_slot(self):
        """The post-acquisition rule: by the time we read, we hold the
        slot, so a waiter cannot be displaced by us and issues no venue
        request while it waits."""
        got = cd.coordination(
            fetch=pages({"calibration-evidence": [me()],
                         "beta48-forward-capture": [
                             run(id=7, name="beta48-forward-capture",
                                 status="queued")]}),
            repo=REPO, env=SELF)
        assert got["ownership"]["OWNED"] is True, got["ownership"]
        assert got["blockers"] == []
        assert got["domain"]["waiting"][0]["workflow"] == \
            "beta48-forward-capture"

    def test_another_EXECUTING_collector_still_blocks(self):
        got = cd.coordination(
            fetch=pages({"calibration-evidence": [me()],
                         "run85-phase2-capture": [
                             run(id=8, name="run85-phase2-capture",
                                 status="in_progress")]}),
            repo=REPO, env=SELF)
        assert cd.B_DOMAIN in got["blockers"]

    def test_an_unreadable_domain_blocks_whatever_we_hold(self):
        def boom(_u):
            raise RuntimeError("github 500")
        got = cd.coordination(fetch=boom, repo=REPO, env=SELF)
        assert cd.B_DOMAIN_UNREADABLE in got["blockers"]


class TestTheInventoryIncludesTheGatherItself:
    def test_the_scanner_alone_does_not_find_calibration_evidence(self):
        """Checked, not assumed. It reaches the venue through the SDK in
        this package, not a literal gateway URL in a shadow module."""
        assert "calibration-evidence" not in cd.scanner_only_inventory()

    def test_the_verified_inventory_includes_it(self):
        assert "calibration-evidence" in cd.inventory()
        assert "calibration-evidence" in cd.EXPLICIT_MEMBERS

    def test_the_reason_is_recorded(self):
        assert "scanner" in cd.EXPLICIT_MEMBERS["calibration-evidence"]


class TestNoSecondCensusWasWritten:
    def test_it_delegates_to_the_repository_s_own_modules(self):
        rc, vd = cd.machinery()
        assert hasattr(rc, "workflow_walk") and hasattr(rc, "census")
        assert hasattr(vd, "isolation")
        assert vd.GLOBAL_DOMAIN == "pmus-public-read-global"

    def test_the_verdict_is_venue_domain_s_own(self):
        """`clear` is read off DIRECT_RESEARCH_COLLECTOR_ISOLATION rather
        than re-derived here, so the two can never disagree."""
        import inspect
        src = inspect.getsource(cd.domain_state)
        assert 'DIRECT_RESEARCH_COLLECTOR_ISOLATION") == "ESTABLISHED"' in src

    def test_the_walk_applies_no_status_filter(self):
        import inspect
        src = inspect.getsource(cd._walk_workflow)
        assert "status=" not in src
        assert "per_page" in src and "page=" in src

    def test_unavailable_machinery_is_unknown_not_a_fallback(self,
                                                             monkeypatch):
        def missing(root=None):
            raise cd.MachineryUnavailable("moved")
        monkeypatch.setattr(cd, "machinery", missing)
        got = cd.domain_state(fetch=pages({}), repo=REPO)
        assert got["STATE"] == cd.UNKNOWN
        assert cd.B_NO_MACHINERY in got["REASON"]


class TestTheEvidenceWorkflowIsTheReservation:
    def test_it_exists_and_joins_the_venue_group(self):
        import pathlib
        # ANCHORED, NOT RELATIVE. `.github/` sits at the repository root
        # while this suite runs from `backend/`, so a bare relative path
        # here reported this file as missing -- which I then relayed to the
        # owner as a real funded-path blocker. See conftest._deterministic_cwd.
        p = _WF
        assert p.exists()
        text = p.read_text()
        assert "group: pmus-public-read-global" in text
        assert "cancel-in-progress: false" in text

    def test_it_exports_the_reservation_the_code_checks_for(self):
        import pathlib
        text = _WF.read_text()
        assert "%s: pmus-public-read-global" % cd.RESERVATION_ENV in text

    def test_it_declares_the_outcome_side_and_the_exit_policy(self):
        import pathlib
        text = _WF.read_text()
        assert "--outcome-side" in text
        assert "--max-exit-orders" in text

    def test_it_never_submits(self):
        """Checked on the STEPS, not the file text: the comment names the
        absent flag on purpose, and a grep cannot tell an explanation
        from an instruction."""
        import pathlib

        import yaml
        doc = yaml.safe_load(_WF.read_text())
        steps = [s for j in doc["jobs"].values() for s in j.get("steps", ())]
        commands = "\n".join(str(s.get("run") or "") for s in steps)
        assert commands.strip()
        assert "--submit" not in commands
        assert "calibration_execute" not in commands
        assert "calibration_evidence" in commands
