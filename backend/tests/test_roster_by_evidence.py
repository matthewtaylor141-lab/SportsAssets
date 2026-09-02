"""The roster is moved by two numbers, and every move is written down.

Owner order 2026-09-01 (evening): whales enter, are promoted, and are
demoted by the data. The rules run on the edge gate's on-chain verdict
on HIS book (funded) and the proof cohort's interval on OUR settled
copies of him (realized). These tests pin what makes that safe:

  * a whale is promoted only by our realized lower bound above zero, on
    enough copies and enough independent games -- never by his edge
  * a whale is demoted only by our realized upper bound below zero on
    enough copies; demotion sticks
  * a promoted whale whose interval widens back over zero is HELD, not
    churned; a measuring whale whose gate flickers is held too
  * the measurement cap binds newcomers by his own lower bound
  * the job fails CLOSED on an unreadable input and writes nothing
  * an empty roster is never written; clips are zeroed instead
  * every decision row carries the numbers that made it
"""
import asyncio
import json

import pytest

from sportsassets.analytics import roster_rules as R
from sportsassets.workers import roster_auto as W


def _real(n=0, roi=None, lo=None, hi=None, clusters=None):
    d = {"n": n, "roi": roi, "clusters": clusters}
    if lo is not None and hi is not None:
        d["ci95"] = [lo, hi]
    return d


# ------------------------------------------------------------- promote

def test_promotion_needs_our_lower_bound_above_zero_not_his_edge():
    """rn1 is funded at [+4.4%, +6.9%] on his book and our copies of him
    read ~0%. His edge must never promote him."""
    d = R.decide("rn1", funded=True, realized=_real(800, -0.007, -0.10, 0.09, 700),
                 current_state="measuring")
    assert d.to_state == "measuring"
    d = R.decide("w", funded=True, realized=_real(80, 0.05, 0.01, 0.09, 60),
                 current_state="measuring")
    assert d.to_state == "promoted" and d.clip_usd == R.PROMOTED_CLIP_USD
    assert "lower bound" in d.reason


def test_a_lucky_low_variance_streak_on_few_copies_does_not_promote():
    d = R.decide("w", funded=True, realized=_real(5, 0.20, 0.05, 0.35, 5),
                 current_state="measuring")
    assert d.to_state == "measuring"


def test_promotion_needs_independent_games_not_just_copies():
    """Thirty legs of two matches are two results."""
    d = R.decide("w", funded=True, realized=_real(40, 0.05, 0.01, 0.09, clusters=2),
                 current_state="measuring")
    assert d.to_state == "measuring"


# -------------------------------------------------------------- demote

def test_losing_at_95_on_enough_copies_demotes_and_zeroes_the_clip():
    d = R.decide("w", funded=True, realized=_real(25, -0.30, -0.50, -0.05, 20),
                 current_state="promoted")
    assert d.to_state == "demoted" and d.clip_usd == 0.0
    assert "upper bound" in d.reason


def test_one_bad_afternoon_cannot_demote():
    d = R.decide("w", funded=True, realized=_real(8, -0.40, -0.70, -0.05, 8),
                 current_state="measuring")
    assert d.to_state == "measuring"


def test_demotion_sticks_whatever_the_numbers_say_later():
    d = R.decide("w", funded=True, realized=_real(200, 0.10, 0.05, 0.15, 150),
                 current_state="demoted")
    assert d.to_state == "demoted" and d.clip_usd == 0.0
    assert "owner decision" in d.reason


# ---------------------------------------------------------------- hold

def test_a_promoted_whale_whose_interval_widens_is_held_not_churned():
    d = R.decide("w", funded=True, realized=_real(300, 0.02, -0.01, 0.05, 250),
                 current_state="promoted")
    assert d.to_state == "promoted" and d.clip_usd == R.PROMOTED_CLIP_USD
    assert d.reason.startswith("holding")


def test_a_measuring_whale_whose_gate_flickers_is_held():
    d = R.decide("w", funded=False, realized=_real(10, 0.01, -0.2, 0.2, 9),
                 current_state="measuring")
    assert d.to_state == "measuring"


def test_an_unfunded_stranger_stays_absent():
    d = R.decide("x", funded=False, realized=None, current_state="absent")
    assert d.to_state == "absent" and d.clip_usd is None and not d.changed


def test_a_funded_newcomer_enters_measurement_at_the_small_clip():
    d = R.decide("new", funded=True, realized=None, current_state="absent")
    assert d.to_state == "measuring" and d.clip_usd == R.MEASURE_CLIP_USD
    assert "unmeasured" in d.reason and d.changed


# ----------------------------------------------------------------- cap

def test_the_measurement_cap_binds_newcomers_by_his_lower_bound():
    funded = {f"w{i}": {"funded": True, "ci95": [0.01 * i, 0.1]} for i in range(12)}
    current = {"w0": "measuring", "w1": "measuring"}
    out = R.plan(funded, {}, current,
                 {w: g["ci95"][0] for w, g in funded.items()})
    measuring = [d.whale for d in out if d.to_state == "measuring"]
    assert len(measuring) == R.MAX_MEASURING
    assert "w0" in measuring and "w1" in measuring          # seats kept
    waiting = [d for d in out if d.to_state == "absent" and "cap" in d.reason]
    assert waiting and all(d.whale in ("w2", "w3", "w4", "w5") for d in waiting)


def test_every_decision_carries_its_numbers():
    d = R.decide("w", funded=True, realized=_real(80, 0.05, 0.01, 0.09, 60),
                 current_state="measuring")
    row = d.as_row()
    assert row["n"] == 80 and row["roi"] == 0.05
    assert row["ci_lo"] == 0.01 and row["ci_hi"] == 0.09 and row["clusters"] == 60
    assert row["funded"] is True and row["changed"] is True


# ----------------------------------------------------------------- job

@pytest.fixture(autouse=True)
def _no_hardcoded_cuts(monkeypatch):
    """The pass seeds live_executor.COPY_CUT_WHALES as 'cut' (a real
    whale today); the fixtures below reason about their own names."""
    from sportsassets import live_executor as _le
    monkeypatch.setattr(_le, "COPY_CUT_WHALES", frozenset())


class _Pool:
    def __init__(self, state=None, decisions_raise=False, roster=None):
        self.kv = {W.K_STATE: state} if state is not None else {}
        if roster is not None:
            self.kv[W.K_ROSTER] = roster
        self.written: dict = {}
        self.rows: list[tuple] = []
        self.decisions_raise = decisions_raise

    async def fetchval(self, sql, *a):
        return json.dumps(self.kv.get(a[0])) if a[0] in self.kv else None

    async def execute(self, sql, *a):
        if "ingestion_state" in sql:
            self.written[a[0]] = json.loads(a[1])
            self.kv[a[0]] = json.loads(a[1])      # readable back, as a DB is
        elif "roster_decisions" in sql:
            if self.decisions_raise:
                raise RuntimeError("no table")
            self.rows.append(a)


def _roster_writes(pool):
    """The writes that move money: everything but the published status."""
    return {k: v for k, v in pool.written.items() if k != W.K_LAST}


def _snap(**whales):
    return {"whales": {w: {"funded": f, "ci95": [0.02, 0.06]} for w, f in whales.items()}}


def _run(pool, snap, realized):
    async def _r(_p):
        return {"by_whale": realized}
    return asyncio.run(W.run_once(pool, snapshot_fn=lambda: snap, realized_fn=_r))


def test_the_job_writes_roster_clips_state_and_an_audit_row_per_whale():
    pool = _Pool(state={"rn1": "measuring"})
    out = _run(pool, _snap(rn1=True, hrh=True),
               {"rn1": _real(80, 0.05, 0.01, 0.09, 60)})
    assert pool.written[W.K_ROSTER] == ["hrh", "rn1"]
    assert pool.written[W.K_CLIPS] == {"rn1": R.PROMOTED_CLIP_USD, "hrh": R.MEASURE_CLIP_USD}
    assert pool.written[W.K_STATE] == {"rn1": "promoted", "hrh": "measuring"}
    assert len(pool.rows) == 2
    assert out["changed"] and {c["whale"] for c in out["changed"]} == {"rn1", "hrh"}


def test_a_demoted_whale_leaves_the_roster_and_carries_an_explicit_zero_clip():
    """The 0 is what blocks his entries ahead of the hardcoded clip AND
    what keeps him exitable (live_executor.exitable_whales reads the
    key), so his open copies can still be sold after he has left."""
    pool = _Pool(state={"w": "promoted", "rn1": "measuring"})
    _run(pool, _snap(w=True, rn1=True), {"w": _real(25, -0.3, -0.5, -0.05, 20)})
    assert pool.written[W.K_ROSTER] == ["rn1"]
    assert pool.written[W.K_STATE]["w"] == "demoted"
    assert pool.written[W.K_CLIPS]["w"] == 0.0


def test_an_empty_roster_is_never_written_but_every_clip_is_zeroed():
    pool = _Pool(state={"w": "promoted"})
    _run(pool, _snap(w=True), {"w": _real(25, -0.3, -0.5, -0.05, 20)})
    assert W.K_ROSTER not in pool.written
    assert pool.written[W.K_CLIPS] == {"w": 0.0}


def test_an_unreadable_gate_writes_nothing_that_moves_money():
    pool = _Pool(state={"rn1": "measuring"})
    out = _run(pool, {"whales": {}, "err": "unreadable"}, {})
    assert out is None and _roster_writes(pool) == {} and pool.rows == []
    assert W.snapshot()["error"]
    assert pool.written[W.K_LAST]["error"]          # the failure is published


def test_the_first_pass_names_the_clip_cut_as_a_change():
    """ROUND FOUR: rn1 seeded as measuring and decided measuring read as
    'held' while his clip went from the hardcoded $250 to $50."""
    from sportsassets.live_executor import PER_FILL_BY_WHALE

    pool = _Pool(state={}, roster=["rn1"])
    out = _run(pool, _snap(rn1=True), {})
    ch = {c["whale"]: c for c in out["changed"]}
    assert "rn1" in ch and ch["rn1"]["changed"] is True
    assert f"clip ${PER_FILL_BY_WHALE['rn1']:.0f} -> ${R.MEASURE_CLIP_USD:.0f}" in ch["rn1"]["reason"]
    assert pool.rows and any(r[-1] is True for r in pool.rows)     # audit changed=true
    # the second pass, same numbers: nothing changed
    out2 = _run(pool, _snap(rn1=True), {})
    assert out2["changed"] == []


def test_a_whale_cut_in_code_is_never_re_admitted_by_a_funded_book(monkeypatch):
    from sportsassets import live_executor as _le

    w = "0xcut-by-owner"
    monkeypatch.setattr(_le, "COPY_CUT_WHALES", frozenset({w}))
    pool = _Pool(state={})
    _run(pool, _snap(**{w: True, "rn1": True}), {})
    assert w not in pool.written[W.K_ROSTER]
    assert pool.written[W.K_STATE][w] == "cut"
    assert pool.written[W.K_CLIPS][w] == 0.0


def test_the_current_roster_seeds_measurement_on_the_first_pass():
    """A whale the owner already rostered by hand is not a stranger:
    unfunded this hour, he is HELD at the measurement clip, not dropped."""
    pool = _Pool(state={}, roster=["HRH"])
    _run(pool, _snap(hrh=False, rn1=True), {})
    assert pool.written[W.K_ROSTER] == ["hrh", "rn1"]
    assert pool.written[W.K_STATE]["hrh"] == "measuring"
    assert pool.written[W.K_CLIPS]["hrh"] == R.MEASURE_CLIP_USD


def test_the_owners_set_roster_outranks_the_rules_in_both_directions():
    """The owner adds a whale the rules demoted and drops one they were
    measuring. The next pass must honour both, not rewrite them."""
    pool = _Pool(state={"w": "demoted", "rn1": "promoted", "x": "measuring"})
    out = asyncio.run(W.owner_set(pool, ["W", "rn1"]))
    assert out["state"] == {"w": "measuring", "rn1": "promoted", "x": "cut"}
    assert out["clips"] == {"w": R.MEASURE_CLIP_USD,
                            "rn1": R.PROMOTED_CLIP_USD, "x": 0.0}
    assert {(c["whale"], c["to"]) for c in out["changed"]} == {
        ("w", "measuring"), ("x", "cut")}
    assert len(pool.rows) == 2
    # and the rules keep the cut whatever his book says
    d = R.decide("x", funded=True, realized=_real(200, 0.1, 0.05, 0.15, 150),
                 current_state="cut")
    assert d.to_state == "cut" and d.clip_usd == 0.0 and "owner" in d.reason
    # the next pass, with the owner's state in memory
    _run(pool, _snap(w=True, rn1=True, x=True), {})
    assert pool.written[W.K_ROSTER] == ["rn1", "w"]
    assert pool.written[W.K_CLIPS]["x"] == 0.0


def test_the_admin_endpoint_tells_the_rules_what_the_owner_decided():
    import inspect

    from sportsassets.api import app as app_mod

    src = inspect.getsource(app_mod.api_set_verified_whales)
    assert "owner_set(pool, whales)" in src


def test_the_last_pass_is_published_and_status_reads_it_back():
    pool = _Pool(state={"rn1": "measuring"})
    _run(pool, _snap(rn1=True), {"rn1": _real(80, 0.05, 0.01, 0.09, 60)})
    last = pool.written[W.K_LAST]
    assert last["error"] is None and last["at"]
    assert [d["whale"] for d in last["decisions"]] == ["rn1"]
    st = asyncio.run(W.status(pool))
    assert st["state"] == {"rn1": "promoted"}
    assert st["clips"] == {"rn1": R.PROMOTED_CLIP_USD}
    assert st["last"]["decisions"][0]["to_state"] == "promoted"
    assert st["rules"]["promoted_clip_usd"] == R.PROMOTED_CLIP_USD


def test_a_failed_audit_row_does_not_undo_the_roster_write():
    pool = _Pool(state={}, decisions_raise=True)
    _run(pool, _snap(rn1=True), {})
    assert pool.written[W.K_ROSTER] == ["rn1"]


def test_the_snapshot_publishes_the_rules_it_runs_on():
    s = W.snapshot()
    assert s["rules"]["measure_clip_usd"] == R.MEASURE_CLIP_USD
    assert s["rules"]["min_n_promote"] == R.MIN_N_PROMOTE


def test_migration_043_ships_with_the_code():
    import pathlib
    root = pathlib.Path(W.__file__).resolve().parents[2]
    body = (root / "migrations" / "043_roster_decisions.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS roster_decisions" in body
