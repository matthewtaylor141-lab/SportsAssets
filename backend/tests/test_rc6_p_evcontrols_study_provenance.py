"""CAPITAL-CRITICAL (EVIDENCE): THE STRATEGY-SELECTION STUDY SAYS WHICH OF
ITS OBSERVATIONS WERE MADE AFTER IT WAS REGISTERED (RC6.2 lane
p-evcontrols). A LABEL, NO RULE.

THE DEFECT. research_registry's PREREGISTER row is written when a release
carrying it first runs (RC6.1: 2026-10-09 or later), but every observation
the study measures in production was made before (2026-10-01..06, research-
sql 37943749921 §11), the 29 holdout events included -- and the strategy
lifecycle's quarantines already reacted to them. `measure` did not separate
the two, so a future PBO / DSR pass resting on past data would read as a
preregistered result. (No false pass today: PBO 1.0, DSR 0.062.)

THE FIX. `measure` reports observations, UTC days, events and holdout events
before and after the registration's instant, the prospective share and
whether the study is all retrospective; MULTIPLE_TESTING carries it. The
measurement itself is unchanged: every train + test observation stays in
(nothing leaves the population), PBO / DSR and their thresholds are as
frozen, and whether acceptance must rest on post-registration observations
is named as the owner's decision, not added.
"""
from __future__ import annotations

import random

from sportsassets.redteam import controls as C
from sportsassets.redteam import research_registry as RR

T0 = 1_790_000_000.0


def _rows(days: int, per_day: int = 3, seed: int = 5):
    rnd = random.Random(seed)
    rows, fx = [], {}
    i = 0
    for d in range(days):
        for s in ("DEREK_ENTRY_POLICY_V2", "PINNACLE_EXPLORATION_PAPER",
                  "PINNACLE_COMPLETED_GAME_PAPER"):
            for k in range(per_day):
                i += 1
                g = "g%d" % i
                fx[g] = "fx-%s-%d-%d" % (s, d, k)
                rows.append({"identity_claimed": True, "strategy": s,
                             "group_id": g, "decided_at": T0 + d * 86400,
                             "realized_pnl_usd": rnd.gauss(-20, 40)})
    return rows, fx


REG = {"registered_candidates": list(RR.PLAN["candidates"]),
       "study": RR.STUDY, "plan_sha": RR.PLAN_SHA, "preregistered": 5}


def test_observations_before_and_after_registration_are_counted_apart():
    rows, fx = _rows(days=10)
    reg_at = T0 + 6 * 86400 - 1.0              # days 0..5 before, 6..9 after
    m = RR.measure(dict(REG, registered_at=reg_at), rows, fx)
    p = m["provenance"]
    assert p["registered_at"] == reg_at
    used = [r for r in rows if RR.slice_of(fx[r["group_id"]]) != RR.HOLDOUT]
    before = [r for r in used if r["decided_at"] < reg_at]
    after = [r for r in used if r["decided_at"] >= reg_at]
    assert p["observations"][RR.BEFORE] == len(before) > 0
    assert p["observations"][RR.AFTER] == len(after) > 0
    assert p["observations"][RR.UNKNOWN] == 0
    assert p["days"] == {RR.BEFORE: 6, RR.AFTER: 4, RR.UNKNOWN: 0}
    held = [r for r in rows if RR.slice_of(fx[r["group_id"]]) == RR.HOLDOUT]
    assert sum(p["holdout_events"].values()) == len(
        {fx[r["group_id"]] for r in held})
    assert p["prospective_share"] == len(after) / len(used)
    assert p["all_retrospective"] is False
    assert "owner decision" in p["acceptance_rule"]


def test_the_label_changes_nothing_the_study_measures():
    rows, fx = _rows(days=10)
    a = RR.measure(dict(REG, registered_at=T0 + 100 * 86400), rows, fx)
    b = RR.measure(dict(REG, registered_at=T0 - 1.0), rows, fx)
    c = RR.measure(REG, rows, fx)                  # no instant on record
    for k in ("pbo", "dsr", "pbo_ok", "dsr_ok", "tested", "days", "events",
              "excluded", "candidates_tested"):
        assert a[k] == b[k] == c[k], k
    # all before registration: the study is all retrospective, and says so
    assert a["provenance"]["all_retrospective"] is True
    assert a["provenance"]["prospective_share"] == 0.0
    assert b["provenance"]["prospective_share"] == 1.0
    # no registration instant on record: neither before nor after, and the
    # share is unknown, never guessed
    n = a["provenance"]["observations"][RR.BEFORE]
    assert n > 0 and c["provenance"]["observations"] == {
        RR.BEFORE: 0, RR.AFTER: 0, RR.UNKNOWN: n}
    assert c["provenance"]["prospective_share"] is None
    assert c["provenance"]["all_retrospective"] is False


def test_multiple_testing_carries_the_provenance_and_adds_no_rule():
    rows, fx = _rows(days=10)
    m = RR.measure(dict(REG, registered_at=T0 + 100 * 86400), rows, fx)
    reg = dict(REG, measurement=m, pbo_ok=m["pbo_ok"], dsr_ok=m["dsr_ok"],
               candidates_tested=m["candidates_tested"])
    c = C.multiple_testing(reg)
    assert c["evidence"]["provenance"] == m["provenance"]
    # the blockers are the measured ones only; retrospective is a label
    assert not [b for b in c["blockers"] if "RETRO" in b or "PROSPECT" in b]
    green = C.multiple_testing(dict(reg, pbo_ok=True, dsr_ok=True,
                                    measurement=dict(m, unregistered_tested=[]
                                                     )))
    assert green["status"] == C.GREEN


def test_the_plan_and_its_sha_are_unchanged_by_the_label():
    """A changed plan would be a NEW registration row: the label lives in
    the measurement's output, never in PLAN."""
    assert "provenance" not in RR.PLAN
    assert RR.PLAN_SHA == __import__("hashlib").sha256(
        __import__("json").dumps(RR.PLAN, sort_keys=True).encode()
    ).hexdigest()
