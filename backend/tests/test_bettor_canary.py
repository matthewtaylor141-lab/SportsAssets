"""The canary's own acceptance: it must not pass on an unknown.

THE DEFECT THIS FILE PINS. `return 1 if bad else 0` exited 0 when a
required check was NOT_ESTABLISHED, so a canary came back green over a
scheduler that had never run and a restart that had never happened.

Three more, each fixed and each pinned below:

  * the live-book check computed freshness and then passed on market
    count alone, so a feed of nothing but STALE books passed a check
    named "live";
  * settlement passed on `attempted > 0 or due`, so a queue with
    items in it counted as a scheduler that ran;
  * restart recovery passed on existing rows plus a ledger, neither of
    which establishes that a process restarted.

These run against a REAL PostgreSQL when `BETTOR_TEST_PG_DSN` names
one, and are skipped otherwise.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import bettor_live_store as st

PG_DSN = os.environ.get("BETTOR_TEST_PG_DSN")
needs_pg = pytest.mark.skipif(not PG_DSN,
                              reason="BETTOR_TEST_PG_DSN names no server")

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANARY = os.path.normpath(os.path.join(_HERE, "..", "..", "scripts",
                                        "bettor_canary.py"))


def canary():
    """Import the script fresh, so RESULTS never leaks between runs."""
    spec = importlib.util.spec_from_file_location("bettor_canary", _CANARY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bettor_canary"] = mod
    spec.loader.exec_module(mod)
    return mod


def run(mod, **kw):
    kw.setdefault("dsn", PG_DSN)
    kw.setdefault("minutes", 60.0)
    kw.setdefault("checkpoint", None)
    kw.setdefault("checkpoint_write", None)
    kw.setdefault("evidence", None)
    kw.setdefault("boot_ids", [])
    return asyncio.run(mod.run(**kw))


def states(mod):
    return {r["check"]: r["state"] for r in mod.RESULTS}


# ── seeding ──────────────────────────────────────────────────────────

def decision(boot, i, *, source_age=1.0, receipt_age=0.5,
             evidence_class="PROSPECTIVE_SHADOW"):
    at = (datetime.now(timezone.utc) - timedelta(seconds=i)).isoformat()
    rec = {"loop": "TEST", "kind": "DECISION", "boot_id": boot,
           "market_id": "m%02d" % (i % 4), "source_ts": at,
           "decided_at": at, "received_at": at, "status": "DECIDED",
           "selected": "NO_TRADE", "evidence_class": evidence_class,
           "source_age_s": source_age, "receipt_age_s": receipt_age,
           "size_contracts": 0.0, "executed": False}
    rec["record_key"] = st.record_key(rec)
    return rec


async def seed(*, boot, n=30, source_age=1.0, receipt_age=0.5,
               evidence_class="PROSPECTIVE_SHADOW", cursors=None,
               wipe=False):
    s = st.PgStore(dsn=PG_DSN, boot_id=boot)
    await s.start()
    if wipe:
        pool = await s._get_pool()
        async with pool.acquire() as con:
            for t in st.OWN_TABLES:
                await con.execute("DELETE FROM %s WHERE lane = $1" % t,
                                  st.LANE)
    recs = [decision(boot, i, source_age=source_age,
                     receipt_age=receipt_age,
                     evidence_class=evidence_class) for i in range(n)]
    await s.flush(recs, cursors or {})
    await s.save_ledger('{"cash": 0.0, "opening_cash": 0.0, '
                        '"movements": [], "positions": {}, "quotes": {}, '
                        '"settled": [], "version": "X"}')
    await s.close()
    return recs


def cur(**kw):
    c = st.new_cursor(time.time())
    c.update(kw)
    return c


EVIDENCE = {"rss_mb_p95": 980, "rss_mb_baseline_p95": 940,
            "restarts_since_deploy": 1, "oom_since_deploy": 0,
            "loops_heartbeating": 19, "loops_expected": 19,
            "collected_at": "2026-09-21T21:00:00Z",
            "source": "render-ops run 1234"}


# ── 1. an unknown is not a pass ──────────────────────────────────────

@needs_pg
class TestAnUnknownIsNotAPass:

    def test_a_bare_run_is_INCOMPLETE_not_success(self):
        asyncio.run(seed(boot="bootA", wipe=True))
        m = canary()
        assert run(m) == m.EXIT_INCOMPLETE
        assert m.EXIT_INCOMPLETE == 3

    def test_the_exit_codes_are_distinct(self):
        m = canary()
        assert {m.EXIT_OK, m.EXIT_FAILED, m.EXIT_INCOMPLETE} == {0, 1, 3}

    def test_a_failure_outranks_an_unknown(self, tmp_path):
        """A FAIL must not be reported as merely incomplete."""
        asyncio.run(seed(boot="bootA", evidence_class="REPLAY_DECISION",
                         wipe=True))
        m = canary()
        assert run(m) == m.EXIT_FAILED

    def test_everything_supplied_gives_exit_zero(self, tmp_path):
        """The whole procedure: seed, checkpoint, restart under a NEW
        boot id, supply operational evidence."""
        due = {"d%02d" % i: cur(settle_next_at=time.time() - 3600)
               for i in range(4)}
        asyncio.run(seed(boot="bootA", n=40, cursors=due, wipe=True))

        cp = str(tmp_path / "cp.json")
        m = canary()
        assert run(m, checkpoint_write=cp) == m.EXIT_OK
        assert json.load(open(cp))["boot_ids"] == ["bootA"]

        # The restart: a different process, and the scheduler ran.
        attempted = {k: cur(settle_status=st.SETTLE_PENDING,
                            settle_attempts=1, settle_last_at=time.time(),
                            settle_next_at=time.time() + 600)
                     for k in due}
        asyncio.run(seed(boot="bootB", n=40, cursors=attempted))

        ev = str(tmp_path / "ev.json")
        json.dump(EVIDENCE, open(ev, "w"))
        m2 = canary()
        code = run(m2, checkpoint=cp, evidence=json.load(open(ev)))
        assert states(m2) == {
            "a live socket delivered FRESH books": m2.PASS,
            "decisions and refusals are in Postgres": m2.PASS,
            "outcome checks ran when work was due": m2.PASS,
            "no execution recorded in our own journal": m2.PASS,
            "no order call is reachable from the worker (STRUCTURAL)":
                m2.UNKNOWN,
            "existing workers unaffected": m2.PASS,
            "a restart happened and records were retained": m2.PASS,
        }
        assert code == m2.EXIT_OK


# ── 2. freshness is required, not merely computed ────────────────────

@needs_pg
class TestFreshBooksAreRequired:

    def test_stale_books_fail_a_check_named_live(self):
        """THE DEFECT: within_bound was computed and then ignored."""
        asyncio.run(seed(boot="bootA", source_age=90.0, receipt_age=60.0,
                         wipe=True))
        m = canary()
        run(m)
        assert states(m)["a live socket delivered FRESH books"] == m.FAIL

    def test_a_stale_receipt_clock_fails_even_with_a_fresh_venue_clock(self):
        """Both clocks. A book the venue stamped a second ago that
        reached us a minute later is not a fresh book."""
        asyncio.run(seed(boot="bootA", source_age=1.0, receipt_age=45.0,
                         wipe=True))
        m = canary()
        run(m)
        assert states(m)["a live socket delivered FRESH books"] == m.FAIL

    def test_a_replayed_transport_fails_by_construction(self):
        asyncio.run(seed(boot="bootA", evidence_class="REPLAY_DECISION",
                         wipe=True))
        m = canary()
        run(m)
        assert states(m)["a live socket delivered FRESH books"] == m.FAIL

    def test_too_few_fresh_decisions_is_not_a_pass(self):
        asyncio.run(seed(boot="bootA", n=3, wipe=True))
        m = canary()
        run(m)
        assert m.MIN_FRESH_DECISIONS > 3
        assert states(m)["a live socket delivered FRESH books"] == m.FAIL

    def test_fresh_books_from_a_live_transport_pass(self):
        asyncio.run(seed(boot="bootA", n=30, wipe=True))
        m = canary()
        run(m)
        assert states(m)["a live socket delivered FRESH books"] == m.PASS


# ── 3. settlement must have RUN ──────────────────────────────────────

@needs_pg
class TestSettlementMustHaveRun:

    def test_work_waiting_is_not_work_done(self):
        """THE DEFECT: `attempted > 0 or bool(due)` passed a scheduler
        that had never executed."""
        due = {"d%02d" % i: cur(settle_next_at=time.time() - 3600)
               for i in range(5)}
        asyncio.run(seed(boot="bootA", cursors=due, wipe=True))
        m = canary()
        run(m)
        assert states(m)["outcome checks ran when work was due"] == m.FAIL

    def test_an_attempt_in_the_window_passes(self):
        done = {"d%02d" % i: cur(settle_status=st.SETTLE_PENDING,
                                 settle_attempts=1,
                                 settle_last_at=time.time(),
                                 settle_next_at=time.time() - 60)
                for i in range(5)}
        asyncio.run(seed(boot="bootA", cursors=done, wipe=True))
        m = canary()
        run(m)
        assert states(m)["outcome checks ran when work was due"] == m.PASS

    def test_an_attempt_before_the_window_does_not_count(self):
        old = {"d%02d" % i: cur(settle_status=st.SETTLE_PENDING,
                                settle_attempts=1,
                                settle_last_at=time.time() - 86400,
                                settle_next_at=time.time() - 60)
               for i in range(5)}
        asyncio.run(seed(boot="bootA", cursors=old, wipe=True))
        m = canary()
        run(m, minutes=5.0)
        assert states(m)["outcome checks ran when work was due"] == m.FAIL

    def test_nothing_due_is_reported_explicitly_not_passed(self):
        """A window in which nothing was owed cannot establish that the
        scheduler works, and must not claim to."""
        none_due = {"d%02d" % i: cur(settle_status=st.SETTLE_PENDING,
                                     settle_attempts=1,
                                     settle_last_at=time.time() - 86400,
                                     settle_next_at=time.time() + 86400)
                    for i in range(5)}
        asyncio.run(seed(boot="bootA", cursors=none_due, wipe=True))
        m = canary()
        run(m, minutes=5.0)
        got = [r for r in m.RESULTS
               if r["check"] == "outcome checks ran when work was due"][0]
        assert got["state"] == m.UNKNOWN
        assert "NOTHING WAS DUE" in got["detail"]


# ── 4. a restart must have happened ──────────────────────────────────

@needs_pg
class TestARestartMustHaveHappened:

    def test_rows_and_a_ledger_alone_do_not_establish_a_restart(self):
        """THE DEFECT, exactly: the old check passed on these two."""
        asyncio.run(seed(boot="bootA", wipe=True))
        m = canary()
        run(m)
        assert states(m)[
            "a restart happened and records were retained"] == m.UNKNOWN

    def test_the_same_boot_after_a_checkpoint_is_a_FAILURE(self, tmp_path):
        """More collection by the SAME process is not a restart."""
        asyncio.run(seed(boot="bootA", wipe=True))
        cp = str(tmp_path / "cp.json")
        m = canary()
        run(m, checkpoint_write=cp)
        asyncio.run(seed(boot="bootA", n=10))       # same boot, more rows
        m2 = canary()
        run(m2, checkpoint=cp)
        assert states(m2)[
            "a restart happened and records were retained"] == m2.FAIL

    def test_a_new_boot_that_kept_its_records_passes(self, tmp_path):
        first = asyncio.run(seed(boot="bootA", wipe=True))
        cp = str(tmp_path / "cp.json")
        m = canary()
        run(m, checkpoint_write=cp)
        before = json.load(open(cp))
        assert before["boot_ids"] == ["bootA"]
        assert set(before["record_keys"]) <= {r["record_key"] for r in first}

        asyncio.run(seed(boot="bootB", n=10))
        m2 = canary()
        run(m2, checkpoint=cp)
        assert states(m2)[
            "a restart happened and records were retained"] == m2.PASS

    def test_a_restart_that_LOST_records_fails(self, tmp_path):
        asyncio.run(seed(boot="bootA", wipe=True))
        cp = str(tmp_path / "cp.json")
        m = canary()
        run(m, checkpoint_write=cp)

        async def wipe_and_reseed():
            s = st.PgStore(dsn=PG_DSN, boot_id="bootB")
            await s.start()
            pool = await s._get_pool()
            async with pool.acquire() as con:
                await con.execute("DELETE FROM bettor_live_journal "
                                  " WHERE lane = $1", st.LANE)
            await s.close()

        asyncio.run(wipe_and_reseed())
        asyncio.run(seed(boot="bootB", n=10))
        m2 = canary()
        run(m2, checkpoint=cp)
        assert states(m2)[
            "a restart happened and records were retained"] == m2.FAIL


# ── 5. operational evidence comes from outside ───────────────────────

@needs_pg
class TestOperationalEvidence:

    def test_without_an_evidence_file_it_is_unknown(self):
        asyncio.run(seed(boot="bootA", wipe=True))
        m = canary()
        run(m)
        assert states(m)["existing workers unaffected"] == m.UNKNOWN

    def test_an_incomplete_evidence_file_is_unknown_not_pass(self):
        asyncio.run(seed(boot="bootA", wipe=True))
        m = canary()
        run(m, evidence={"rss_mb_p95": 1})
        assert states(m)["existing workers unaffected"] == m.UNKNOWN

    def test_an_oom_since_the_deploy_fails(self):
        asyncio.run(seed(boot="bootA", wipe=True))
        m = canary()
        run(m, evidence=dict(EVIDENCE, oom_since_deploy=1))
        assert states(m)["existing workers unaffected"] == m.FAIL

    def test_a_missing_heartbeat_fails(self):
        asyncio.run(seed(boot="bootA", wipe=True))
        m = canary()
        run(m, evidence=dict(EVIDENCE, loops_heartbeating=18))
        assert states(m)["existing workers unaffected"] == m.FAIL

    def test_memory_growth_past_the_band_fails(self):
        asyncio.run(seed(boot="bootA", wipe=True))
        m = canary()
        run(m, evidence=dict(EVIDENCE, rss_mb_p95=1400))
        assert states(m)["existing workers unaffected"] == m.FAIL


# ── 6. scope, stated in the script itself ────────────────────────────

def test_the_no_order_check_is_labelled_as_a_journal_read():
    src = open(_CANARY).read()
    assert "NOT A VENUE-ACCOUNT AUDIT" in src
    assert "no execution recorded in our own journal" in src
    assert "(STRUCTURAL)" in src


def test_the_script_refuses_to_be_read_as_a_profitability_result():
    src = open(_CANARY).read()
    assert src.count("profitability") >= 3
